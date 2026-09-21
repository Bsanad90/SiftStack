"""Offline check of the cross-day full-text reuse (`--prior`). No network.

Run after ANY change to src/scripts/fulltext_prior.py or to the resolve loop
in mddc_fetch_full_text_browser.py / va_fetch_full_text_browser.py, the way
test_md_row_pass_budgets.py guards the MD RoW passes.

What it protects (the behaviours the 2026-09-21 build rests on):

  1. A prior text is keyed on notice_id and loaded from BOTH a `_full.csv`
     and a `_details.json` sidecar; rows without an id or with short text are
     ignored; a missing or corrupt prior is skipped, never fatal.
  2. `--prior-auto` walks the sibling YYYY-MM-DD dirs newest first, within the
     lookback, sidecar before CSV, and ignores non-day dirs.
  3. The sidecar MERGES on write (the VA fill pass must not shrink it).
  4. The abort gate counts FETCHES: five failures with zero successful fetches
     abort even when reused rows already fill `results`; one successful fetch
     disarms it.
  5. Republication detection fires only on an ADDRESS key already held under
     a different id -- never on a snippet key, never on the same id.
  6. The street guard judges reused text exactly like fetched text.
  7. End to end on the real MDDC loop with a fake browser/fetcher: an id in
     the prior is NOT fetched; a new id IS; a prior text failing the guard is
     re-fetched; the republication is skipped before any fetch; auction_date
     is re-parsed from the text, not copied from the prior.

  python tools/manual/test_fulltext_prior.py
"""
import csv
import json
import pathlib
import sys
import tempfile
import types
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import fulltext_prior as fp  # noqa: E402

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


TEXT_A = ("TRUSTEE'S SALE 123 Main Street, Rockville, MD 20850. " * 8
          + "will sell at public auction on October 15, 2026 at 10:00 AM ...")
TEXT_B = ("SUBSTITUTE TRUSTEES' SALE 456 Oak Avenue, Frederick, MD 21701. " * 8
          + "sale on November 2, 2026 at 11:00 AM ...")
TEXT_C = "IN THE CIRCUIT COURT FOR MONTGOMERY COUNTY order nisi ratification " * 6
assert len(TEXT_A) >= fp.MIN_TEXT_CHARS and len(TEXT_C) >= fp.MIN_TEXT_CHARS


def _write_full_csv(path, rows):
    fields = ["notice_id", "street", "zip", "full_text"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------- 1. load ---
print("1. load_prior from CSV + sidecar")
with tempfile.TemporaryDirectory() as td:
    td = pathlib.Path(td)
    csv_p = td / "x_full.csv"
    _write_full_csv(csv_p, [
        {"notice_id": "100", "street": "123 Main Street", "zip": "20850", "full_text": TEXT_A},
        {"notice_id": "", "street": "9 Nowhere", "zip": "", "full_text": TEXT_A},      # no id
        {"notice_id": "101", "street": "456 Oak Avenue", "zip": "21701", "full_text": "short"},
    ])
    side_p = td / "x_full_details.json"
    fp.write_sidecar(side_p, {
        "200": {"full_text": TEXT_B, "street": "456 Oak Avenue", "zip": "21701"},
        "100": {"full_text": "DIFFERENT " * 30, "street": "123 Main Street"},   # dup id, loses
    }, merge=False)
    prior = fp.load_prior([str(csv_p), str(side_p), str(td / "missing.csv")])
    check("csv row with id + text loaded", "100" in prior)
    check("csv row without id ignored", all(e["notice_id"] for e in prior.values()))
    check("csv row with short text ignored", "101" not in prior)
    check("sidecar entry loaded", "200" in prior and prior["200"]["full_text"] == TEXT_B)
    check("first path wins on a duplicate id", prior["100"]["full_text"] == TEXT_A)
    check("missing prior skipped, not fatal", len(prior) == 2)
    (td / "bad.json").write_text("{not json", encoding="utf-8")
    check("corrupt prior skipped, not fatal", fp.load_prior([str(td / "bad.json")]) == {})
    check("glob accepted", "200" in fp.load_prior([str(td / "*_details.json")]))

# ---------------------------------------------------------- 2. auto scan ---
print("2. auto_prior_paths")
with tempfile.TemporaryDirectory() as td:
    td = pathlib.Path(td)
    for d in ["2026-09-05", "2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21",
              "2026-09-22", "_archive"]:
        (td / d).mkdir()
    _write_full_csv(td / "2026-09-19" / "m_full.csv", [])
    _write_full_csv(td / "2026-09-20" / "m_full.csv", [])
    fp.write_sidecar(td / "2026-09-20" / "m_full_details.json", {}, merge=False)
    _write_full_csv(td / "2026-09-05" / "m_full.csv", [])          # outside 14 days
    _write_full_csv(td / "2026-09-22" / "m_full.csv", [])          # future dir
    _write_full_csv(td / "_archive" / "m_full.csv", [])            # not a day dir
    _write_full_csv(td / "2026-09-18" / "other_full.csv", [])      # different leg
    got = fp.auto_prior_paths(td / "2026-09-21" / "m_full.csv", lookback_days=14)
    names = [f"{p.parent.name}/{p.name}" for p in got]
    check("newest first, sidecar before csv",
          names[:3] == ["2026-09-20/m_full_details.json", "2026-09-20/m_full.csv",
                        "2026-09-19/m_full.csv"], str(names))
    check("outside lookback excluded", not any("2026-09-05" in n for n in names), str(names))
    check("future day dir excluded", not any("2026-09-22" in n for n in names), str(names))
    check("non-day dir excluded", not any("_archive" in n for n in names), str(names))
    check("other leg's file excluded", not any("other_full" in n for n in names), str(names))
    check("non-day out dir -> empty", fp.auto_prior_paths(td / "_archive" / "m_full.csv") == [])
    check("Tuesday reaches back past a skipped weekend",
          any("2026-09-19" in n for n in fp.auto_prior_paths(
              td / "2026-09-22" / "m_full.csv", lookback_days=14, today=date(2026, 9, 22))
              and [f"{p.parent.name}" for p in fp.auto_prior_paths(
                  td / "2026-09-22" / "m_full.csv", lookback_days=14)]))

# ---------------------------------------------------------- 3. merge ---
print("3. write_sidecar merges")
with tempfile.TemporaryDirectory() as td:
    side = pathlib.Path(td) / "v_full_details.json"
    fp.write_sidecar(side, {"1": {"full_text": TEXT_A}, "2": {"full_text": TEXT_B}}, merge=False)
    n = fp.write_sidecar(side, {"2": {"full_text": TEXT_C}, "3": {"full_text": TEXT_A}})
    data = json.loads(side.read_text(encoding="utf-8"))["details"]
    check("fill pass adds without shrinking", set(data) == {"1", "2", "3"} and n == 3, str(set(data)))
    check("new entry wins on a duplicate id", data["2"]["full_text"] == TEXT_C)
    check("sidecar path derived from the csv name",
          fp.sidecar_path(pathlib.Path("a/b/mddc_trustee_sale_full.csv")).name
          == "mddc_trustee_sale_full_details.json")

# ---------------------------------------------------------- 4. budget ---
print("4. FetchBudget abort gate")
b = fp.FetchBudget()
b.reused = 40
for _ in range(5):
    b.note_failure()
check("5 failures + 0 fetched ok aborts even with reused rows", b.should_abort())
b2 = fp.FetchBudget()
b2.note_success()
for _ in range(20):
    b2.note_failure()
check("one successful fetch disarms the gate", not b2.should_abort())
b3 = fp.FetchBudget()
for _ in range(4):
    b3.note_failure()
check("4 failures do not abort", not b3.should_abort())
check("summary names the counts", "reused from --prior" in b.summary(40) and "5 failed" in b.summary(40))

# ---------------------------------------------------- 5. republication ---
print("5. is_republication")
results = {("addr", "123 main street", "20850"): {"notice_id": "100"},
           ("snip", "in the circuit court", ""): {"notice_id": "300"}}
check("address key held under another id -> republication",
      fp.is_republication(("addr", "123 main street", "20850"), "99", results))
check("same id is not a republication",
      not fp.is_republication(("addr", "123 main street", "20850"), "100", results))
check("unknown key is not a republication",
      not fp.is_republication(("addr", "1 elsewhere", "00000"), "5", results))
check("snippet key never a republication (collision path instead)",
      not fp.is_republication(("snip", "in the circuit court", ""), "301", results))

# ---------------------------------------------------------- 6. guard ---
print("6. street_in_text")
check("first two tokens present -> pass", fp.street_in_text("123 Main Street", TEXT_A))
check("case-insensitive", fp.street_in_text("123 MAIN st", TEXT_A))
check("other street -> fail", not fp.street_in_text("456 Oak Avenue", TEXT_A))
check("streetless row passes", fp.street_in_text("", TEXT_C))
check("empty text fails a street", not fp.street_in_text("123 Main", ""))

# ------------------------------------------------- 7. real MDDC loop ---
print("7. mddc_fetch_full_text_browser.fetch_all with a fake browser")
_pw = types.ModuleType("playwright.sync_api")


class _FakePage:
    url = "https://www.mddcpublicnotices.com/(S(abc123))/Search.aspx"


class _FakeBrowser:
    def new_page(self):
        return _FakePage()

    def close(self):
        pass


class _FakePW:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    class chromium:  # noqa: N801
        @staticmethod
        def launch(headless=True):
            return _FakeBrowser()


_pw.sync_playwright = lambda: _FakePW()
_pw.TimeoutError = TimeoutError
sys.modules["playwright.sync_api"] = _pw
sys.modules.setdefault("playwright", types.ModuleType("playwright"))

import mddc_fetch_full_text_browser as mb  # noqa: E402

GRID = [
    # newest first, as the site orders it
    {"notice_id": "500", "street": "123 Main Street", "zip": "20850",
     "date_published": "09/21/2026", "notice_text_snippet": "x"},        # in prior
    {"notice_id": "501", "street": "456 Oak Avenue", "zip": "21701",
     "date_published": "09/21/2026", "notice_text_snippet": "y"},        # new -> fetch
    {"notice_id": "502", "street": "789 Elm Road", "zip": "20852",
     "date_published": "09/21/2026", "notice_text_snippet": "z"},        # prior fails guard
    {"notice_id": "490", "street": "123 Main Street", "zip": "20850",
     "date_published": "09/14/2026", "notice_text_snippet": "x"},        # republication of 500
    {"notice_id": "503", "street": "", "zip": "",
     "date_published": "09/21/2026", "notice_text_snippet": TEXT_C},    # streetless, new
]
PRIOR = {
    "500": {"full_text": TEXT_A, "source": "prev/_full.csv", "fetched_at": "2026-09-20T06:03:00"},
    "502": {"full_text": TEXT_B, "source": "prev/_full.csv"},   # wrong text for 789 Elm
}
FETCHED_TEXT = {
    "501": TEXT_B,
    "502": "TRUSTEE'S SALE 789 Elm Road, Rockville MD 20852 " * 8 + "sale on December 1, 2026.",
    "503": TEXT_C,
    "490": TEXT_A,
}
fetched_ids: list[str] = []

mb.login_and_search = lambda *a, **k: None
mb.collect_grid_rows = lambda page, ss, mp_: list(GRID)


def _fake_fetch(page, nid, key):
    fetched_ids.append(nid)
    return {"ok": True, "error": "", "full_text": FETCHED_TEXT[nid]}


mb.fetch_detail = _fake_fetch
mb.parse_auction_date = lambda text, pub: "PARSED:" + text[:12]
mb.mp.parse_loan_principal = lambda text: ""

results, details, budget = mb.fetch_all(
    {"captcha_api_key": "k"}, "43", ["Montgomery"], 7, 12, 0, False, prior=PRIOR)

check("prior id NOT fetched", "500" not in fetched_ids, str(fetched_ids))
check("new ids fetched", {"501", "503"} <= set(fetched_ids), str(fetched_ids))
check("prior text failing the guard is re-fetched", "502" in fetched_ids, str(fetched_ids))
check("republication skipped before any fetch", "490" not in fetched_ids, str(fetched_ids))
check("counters: 3 fetched / 1 reused / 1 republication / 1 guard refetch",
      (budget.fetched_ok, budget.reused, budget.skipped_republication,
       budget.refetched_after_guard) == (3, 1, 1, 1),
      str((budget.fetched_ok, budget.reused, budget.skipped_republication,
           budget.refetched_after_guard)))
k500 = ("addr", "123 main street", "20850")
check("reused row keeps the NEWER id and its prior text",
      results[k500]["notice_id"] == "500" and results[k500]["full_text"] == TEXT_A)
check("auction_date re-parsed from text, not copied",
      results[k500]["auction_date"].startswith("PARSED:"))
check("sidecar holds every resolved id incl. the reused one",
      set(details) == {"500", "501", "502", "503"}, str(set(details)))
check("reused entry keeps its provenance",
      details["500"]["source"] == "prev/_full.csv"
      and details["500"]["fetched_at"] == "2026-09-20T06:03:00")
check("fetched entry stamped fetched", details["501"]["source"] == "fetched"
      and details["501"]["fetched_at"])
check("summary line", "3 fetched, 1 reused from --prior" in budget.summary(len(results)))

# abort gate through the real loop: every fetch fails, prior fills results
fetched_ids.clear()
mb.fetch_detail = lambda page, nid, key: {"ok": False, "error": "gate", "full_text": ""}
GRID_FAIL = [{"notice_id": str(600 + i), "street": f"{i} Fail St", "zip": "20850",
              "date_published": "", "notice_text_snippet": ""} for i in range(6)]
mb.collect_grid_rows = lambda page, ss, mp_: [GRID[0]] + GRID_FAIL
try:
    mb.fetch_all({"captcha_api_key": "k"}, "43", ["Montgomery"], 7, 12, 0, False, prior=PRIOR)
    check("dead gate aborts although a reused row filled results", False, "no SystemExit")
except SystemExit as exc:
    check("dead gate aborts although a reused row filled results",
          "first 5 detail fetches all failed" in str(exc))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("all checks passed")
