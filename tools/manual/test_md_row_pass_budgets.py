"""Offline check of the MD Register of Wills slow-pass budgets. No network.

Run after ANY change to run_reconciliation_pass / fill_addresses in
src/scripts/md_register_of_wills_pull.py, the way test_manage_sold_url.py
guards the sold sweep.

What it protects (all three failed live, 2026-09-11..09-15, and the leg wrote
nothing for five days while still spending Firecrawl calls):

  1. Both slow passes must honour a cap, so the CSV write after them is reached
     inside the runner's 75m per-leg kill.
  2. A capped pass must ROTATE -- least-recently-checked first -- or it re-checks
     the same head of the queue forever and the tail is never looked at.
  3. Both must bank progress through save_cb, or a kill discards the whole pass
     and the next day repeats it. fill_addresses in particular must flush every
     record: at minutes per decedent, a 10-record cadence never fires.

  python tools/manual/test_md_row_pass_budgets.py
"""
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

_lookups: list[str] = []
_fake = types.ModuleType("md_land_records_lookup")
_fake.find_property_for_decedent = lambda county, last, first, **kw: (
    _lookups.append(last), {"street": "", "confidence": "LOW", "reason": "test"})[1]
_fake.lookup_sdat_by_address = lambda *a, **k: None
_fake.name_match_level = lambda *a, **k: "LOW"
_fake.MAX_DEED_CANDIDATES = 5
sys.modules["md_land_records_lookup"] = _fake

import md_register_of_wills_pull as m  # noqa: E402

m.time.sleep = lambda s: None
_fetched: list[str] = []
m.fetch_estate_detail = lambda rid: (_fetched.append(rid), {"personal_reps": []})[1]

FAILURES = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILURES.append(label)


def recon_ledger():
    # last_checked deliberately NOT in insertion order, so a bare slice of dict
    # order picks a different set than least-recently-checked-first.
    rows = [("E1", "09/14/2026"), ("E2", "09/10/2026"), ("E3", None),
            ("E4", "09/12/2026"), ("E5", "09/11/2026")]
    return {"estates": {
        f"Test|{k}": {"record_id": k, "incomplete": True,
                      "incomplete_since": "09/08/2026", "first_seen": "09/08/2026",
                      **({"last_checked": lc} if lc else {})}
        for k, lc in rows}}


print("reconciliation pass")
led, saves = recon_ledger(), []
runs = []
for day in ("09/15/2026", "09/16/2026", "09/17/2026"):
    _fetched.clear()
    m.run_reconciliation_pass(led, ["Test"], run_date=day, limit=2,
                              save_cb=lambda: saves.append(1))
    runs.append(list(_fetched))

check("cap honoured (2 per run)", all(len(r) == 2 for r in runs))
check("never-checked first, then oldest", runs[0] == ["E3", "E2"])
check("capped pass rotates instead of repeating", set(runs[1]).isdisjoint(runs[0]))
check("whole queue covered within 3 capped runs",
      set(runs[0] + runs[1] + runs[2]) == {f"E{i}" for i in range(1, 6)})
check("save_cb fired every run", len(saves) >= 3)

_fetched.clear()
m.run_reconciliation_pass(recon_ledger(), ["Test"], run_date="09/15/2026")
check("uncapped still processes the whole queue", len(_fetched) == 5)

_fetched.clear()
m.run_reconciliation_pass(recon_ledger(), ["Other"], run_date="09/15/2026", limit=2)
check("county scoping still excludes other counties", _fetched == [])

# A legal-notice-only record has no detail page, so it never gets last_checked.
# Left in the queue it sorts first forever and eats a slot of every capped run.
jam = recon_ledger()
jam["estates"]["Test|NOID"] = {"incomplete": True, "first_seen": "09/08/2026",
                               "incomplete_since": "09/08/2026"}
_fetched.clear()
m.run_reconciliation_pass(jam, ["Test"], run_date="09/15/2026", limit=2)
check("record_id-less rows cannot jam the cap head", len(_fetched) == 2)

print("\naddress pass")


def addr_ledger():
    return {"estates": {f"Test|{i}": {
        "decedent_last_name": f"L{i}", "decedent_first_name": "Jane",
        "estate_type": "RE", "record_id": str(i)} for i in range(1, 8)}}


led, saves = addr_ledger(), []
_lookups.clear()
m.fill_addresses(led, ["Test"], limit=3, save_cb=lambda: saves.append(1))
check("cap honoured (3 lookups)", len(_lookups) == 3)
check("flushes every record, not every 10", len(saves) >= 3)

first = list(_lookups)
_lookups.clear()
m.fill_addresses(led, ["Test"], limit=3, save_cb=lambda: saves.append(1))
check("resumes on the unchecked tail rather than repeating",
      len(_lookups) == 3 and not set(_lookups) & set(first))

_lookups.clear()
m.fill_addresses(addr_ledger(), ["Test"], save_cb=None)
check("uncapped still resolves every target", len(_lookups) == 7)

print("\nRESULT:", "ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURE(S)")
sys.exit(1 if FAILURES else 0)
