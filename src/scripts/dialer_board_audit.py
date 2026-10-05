"""Audit one dialer's assigned book for pinned board notes. READ-ONLY.

Bagoury (2026-10-04): "a lot of my properties have no note pinned on the message board --
I don't know if I'm calling the owner, the PR or who." The confusion is structural: on this
account the `owner` block means a different person depending on how the record arrived
(Register of Wills uploads put the PR there, Inheritance / Obituary records the decedent,
SiftMap Priority records a living owner) and only a pinned board note says which.

For every record assigned to the dialer this script reads detail + custom fields + the
message board and sorts it on two axes:

  board        NONE      no message on the board at all
               UNPINNED  messages exist, none pinned (the dialer sees nothing at the top)
               PINNED    at least one pinned message (families reported)

  who_signal   what the record ITSELF can tell a dialer about the owner block, note or not:
               OWNER-ALIVE     DP note says presumed alive, or a SiftMap/Hottest record with
                               no deceased signal
               OWNER=PR        the PR custom fields name the same person as the owner block
               OWNER=DECEDENT  PR fields name a DIFFERENT person, or owner.deceased /
                               Deceased Owner / Obituaries / Inheritance-only / DP DECEDENT
               UNCLEAR         a probate-list record with no PR fields and no deceased
                               marker -- nobody wrote down who the owner block is. This is
                               the bucket behind Bagoury's question.

Lane = which ready-to-call preset the record sits in for this dialer (Hottest / FTM /
Obituary), else "other" (assigned, but not in a dial lane right now).

Nothing here writes. The only POST is the `x-http-method-override: GET` count/list.

Usage:
    python -u src/scripts/dialer_board_audit.py --count-only           # 5 calls, no hydration
    python -u src/scripts/dialer_board_audit.py                        # Mohammed Bagoury
    python -u src/scripts/dialer_board_audit.py --dialer "Pal John"
    python -u src/scripts/dialer_board_audit.py --all-dialers
    python -u src/scripts/dialer_board_audit.py --lanes-only           # the 02-05 dial presets only
    python -u src/scripts/dialer_board_audit.py --rest-light           # whole book, parked = board only
    python -u src/scripts/dialer_board_audit.py --fresh                # ignore the detail cache
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from dpd_lane_assignee_report import (  # noqa: E402
    OFFSET_CEILING,
    count,
    list_uuids,
    resolve_lanes,
    to_query,
)
from dpd_lane_phone_score import Api, jload, jsave, say, tag_names  # noqa: E402
from dpd_lane_rebalance import DIALERS, uuid_of_name  # noqa: E402
from not_related_requalify import bad_phone_status  # noqa: E402
from obituary_dp_batch import (  # noqa: E402
    _note_family,
    board_messages,
    custom_field_rows,
    deceased_signal,
)

RUN = ROOT / "output" / "dialer_board_audit"
DEFAULT_DIALER = "Mohammed Bagoury"

# Every preset a dialer works from: the four dial stages of each CALL folder. 02 is the
# fresh lane; 03-05 hold the No Answer call-backs (3,381 of Bagoury's 5,851 on 2026-10-05),
# which is where "a lot of properties" live. The 00/01 (needs skipped / no numbers) stages
# and the MAIL folders are not dialed.
CALL_FOLDERS = [("01 HOTTEST - CALL", "Hottest"), ("03 STRONG - CALL", "Strong"),
                ("05 FTM - CALL", "FTM"), ("07 TIER 2 - CALL", "Tier 2"),
                ("09 BULK - CALL", "Bulk"), ("13 OBITUARY - CALL", "Obituary")]
DIAL_STAGES = ["02 Ready to Call", "03 Call 2", "04 Call 3", "05 Call 4"]
DIAL_LANES = [(folder, f"{short} - {stage}") for folder, short in CALL_FOLDERS
              for stage in DIAL_STAGES]
LANE_SHORT = {f"{short} - {stage}": f"{short}/{stage[:2]}" for _f, short in CALL_FOLDERS
              for stage in DIAL_STAGES}

PR_FIRST, PR_LAST, PR_FULL = "PR First Name", "PR Last Name", "Personal Representative"
REL_CF_RE = re.compile(r"^REL\d+: Full Name$")
# Both spellings live on the account (reskiptrace_audit.REL_TAG_RE): "Rel1.1" / "Rel 1.1",
# "Owner.1" / "Owner 1", "Pr.1" / "PR.1" / "Pr4".
WHO_TAG_RE = re.compile(r"^(rel ?\d+([. ]\d+)?|owner[. ]?\d+|pr\.? ?\d+([. ]\d+)?)$", re.I)
PROBATE_LIST_WORDS = ("probate", "register of wills")
DECEASED_TAG_WORDS = ("deceased owner", "obituaries")

BOARD_BUCKETS = ["NONE", "UNPINNED", "PINNED"]
WHO_BUCKETS = ["OWNER-ALIVE", "OWNER=PR", "OWNER=DECEDENT", "UNCLEAR", "(not read)"]


# ----------------------------------------------------------------------------- helpers
def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z ]", "", (s or "").lower()).strip()


def same_person(a: str, b: str) -> bool:
    """Last token equal and first token equal (middle names / initials ignored)."""
    ta, tb = _norm_name(a).split(), _norm_name(b).split()
    if not ta or not tb:
        return False
    return ta[-1] == tb[-1] and ta[0] == tb[0]


def _author(m: dict) -> str:
    a = m.get("author")
    if isinstance(a, dict):
        return a.get("email") or a.get("first_name") or ""
    return str(a or "")


def lane_members(api, who: str) -> dict[str, str]:
    """uuid -> lane short name for the records this dialer holds in each CALL lane."""
    out: dict[str, str] = {}
    for ln in resolve_lanes(api, DIAL_LANES):
        q = to_query(ln["filters"])
        q["must"]["assigned_to"] = who
        n = count(api, q)
        short = LANE_SHORT.get(ln["preset"], ln["preset"])
        if n:
            say(f"  lane {short}: {n} assigned to this dialer")
        for u in list_uuids(api, q, n):
            out.setdefault(u, short)   # first (freshest) stage wins on overlap
    say(f"  dial set: {len(out)} distinct records")
    return out


def list_thin(api, query: dict, expected: int) -> dict[str, dict]:
    """Page the cohort keeping the THIN rows (address, owner, status, has_phones)."""
    out: dict[str, dict] = {}
    offset = 0
    while offset < expected:
        st, r = api.post_as_get("/api/internal/property/",
                                {"limit": 200, "offset": offset, "query": query})
        if st != 200:
            raise RuntimeError(f"thin list failed at offset {offset}: {st} {str(r)[:300]}")
        rows = r.get("results") or []
        if not rows:
            break
        for row in rows:
            out.setdefault(row["uuid"], row)
        offset += 200
    return out


def _progress(i: int, n: int, t0: float) -> None:
    el = time.time() - t0
    rate = i / el if el else 0
    eta = ((n - i) / rate) / 60 if rate else 0
    say(f"    {i}/{n}  elapsed={el / 60:.1f}m  ETA={eta:.1f}m")


def hydrate(api, uuids: list[str], cache_path: Path, fresh: bool,
            light: dict[str, dict] | None = None) -> dict[str, dict]:
    """detail + custom-field values + board per record, resumable (saved every 25).

    `light` = {uuid: thin row} for records that only need the BOARD read (the parked
    remainder of a book): the thin row stands in for the detail, custom fields are
    skipped, and the entry is marked `light` so the who-axis is reported as unread.
    """
    light = light or {}
    cache: dict = {} if fresh else jload(cache_path, {})
    # a light entry is upgraded when the same uuid later needs the full read
    todo = [u for u in uuids if u not in cache or (cache[u].get("light") and u not in light)]
    say(f"hydrating {len(todo)} records ({len(cache)} already cached; "
        f"{sum(1 for u in todo if u in light)} board-only)...")
    t0 = time.time()
    for i, u in enumerate(todo, 1):
        if u in light:
            cache[u] = {"detail": light[u], "cf": {}, "light": True,
                        "board": _board_rows(board_messages(api, u))}
            if i % 25 == 0 or i == len(todo):
                jsave(cache_path, cache)
                _progress(i, len(todo), t0)
            continue
        st, body = api.get(f"/api/internal/property/{u}/")
        if st == 404:
            cache[u] = {"_gone": True}
            continue
        if st != 200:
            say(f"  detail {st} on {u}; skipping this pull")
            continue
        _rows, values = custom_field_rows(api, u)
        values = {k: v for k, v in values.items() if str(v or "").strip()}
        cache[u] = {"detail": body, "cf": values, "board": _board_rows(board_messages(api, u))}
        if i % 25 == 0 or i == len(todo):
            jsave(cache_path, cache)
            _progress(i, len(todo), t0)
    jsave(cache_path, cache)
    return cache


def _board_rows(msgs: list[dict]) -> list[dict]:
    return [{"family": _note_family(m.get("message") or ""),
             "pinned": bool(m.get("pinned")),
             "author": _author(m),
             "created": str(m.get("created") or "")[:10],
             # enough of the text to read a DP verdict line, no more
             "head": "\n".join((m.get("message") or "").splitlines()[:6])[:600]}
            for m in msgs]


# ----------------------------------------------------------------------------- classify
def classify(u: str, entry: dict, lane: str) -> dict:
    d = entry["detail"]
    addr = d.get("address") or {}
    owner = d.get("owner") or {}
    cf = entry.get("cf") or {}
    board = entry.get("board") or []
    lists = tag_names(d.get("lists"))
    ptags = tag_names(d.get("tags"))

    owner_name = " ".join(x for x in [owner.get("first_name"), owner.get("last_name")] if x).strip()
    company = (owner.get("company") or "").strip()
    pr_name = " ".join(x for x in [cf.get(PR_FIRST), cf.get(PR_LAST)] if x).strip() \
        or str(cf.get(PR_FULL) or "").strip()
    rel_names = [str(v) for k, v in cf.items() if REL_CF_RE.match(k)]

    # --- board axis
    pinned = [b for b in board if b["pinned"]]
    if not board:
        board_bucket = "NONE"
    elif not pinned:
        board_bucket = "UNPINNED"
    else:
        board_bucket = "PINNED"
    pinned_fams = sorted({b["family"] for b in pinned})
    all_fams = sorted({b["family"] for b in board})

    # --- what the notes (pinned or not) already say about the owner block
    heads = "\n".join(b["head"] for b in board)
    dp_alive = bool(re.search(r"^OWNER:.*presumed alive", heads, re.M | re.I))
    dp_decedent = bool(re.search(r"^DECEDENT:", heads, re.M))

    # --- deceased markers on the record itself
    lists_l = ", ".join(lists).lower()
    tags_l = ", ".join(ptags).lower()
    probate_list = any(w in lists_l for w in PROBATE_LIST_WORDS)
    inherit_only = "inheritance" in lists_l and not probate_list
    deceased_tag = any(w in tags_l for w in DECEASED_TAG_WORDS)
    signal = deceased_signal(", ".join(lists), ", ".join(ptags),
                             (d.get("last_obituary_date") or "")[:10],
                             d.get("personal_representative") or "")
    dead_marker = bool(owner.get("deceased")) or deceased_tag or inherit_only or dp_decedent

    # --- who axis
    if entry.get("light"):
        who, why = "(not read)", "board-only read; detail and custom fields skipped"
    elif dp_alive:
        who, why = "OWNER-ALIVE", "DP note: owner presumed alive"
    elif dp_decedent:
        who, why = "OWNER=DECEDENT", "DP note: DECEDENT line"
    elif pr_name and owner_name and same_person(pr_name, owner_name):
        who, why = "OWNER=PR", f"PR fields = owner ({pr_name})"
    elif pr_name and owner_name:
        who, why = "OWNER=DECEDENT", f"PR fields name {pr_name}, owner block is {owner_name}"
    elif dead_marker:
        who = "OWNER=DECEDENT"
        why = ("owner.deceased" if owner.get("deceased") else
               "Deceased Owner / Obituaries tag" if deceased_tag else
               "Inheritance-only list" if inherit_only else "decedent marker")
    elif probate_list:
        who, why = "UNCLEAR", "probate list, no PR fields, no deceased marker"
    elif lane.startswith("Hottest") or not signal:
        who, why = "OWNER-ALIVE", ("SiftMap lane" if lane.startswith("Hottest") else "no deceased signal")
    else:
        who, why = "UNCLEAR", f"signal only: {signal}"

    # --- phones and their who-tags
    phones = [p for p in (owner.get("phones") or []) if isinstance(p, dict)]
    live = [p for p in phones if not bad_phone_status(p.get("status"))]
    labelled = [p for p in live if any(WHO_TAG_RE.match(t) for t in tag_names(p.get("tags")))]

    # the dialer's practical question: is there a NAME on the record to ask for?
    has_contact_name = bool(pr_name or rel_names or (owner_name and who != "OWNER=DECEDENT"))

    return {
        "uuid": u, "lane": lane,
        "board": board_bucket,
        "pinned_families": " | ".join(pinned_fams),
        "all_families": " | ".join(all_fams),
        "messages": len(board), "pinned_count": len(pinned),
        "who_signal": who, "who_why": why,
        "has_contact_name": has_contact_name,
        "status": d.get("status") or "",
        "street": addr.get("street") or "", "city": addr.get("city") or "",
        "state": addr.get("state") or "", "zip": (addr.get("postal_code") or "")[:5],
        "county": addr.get("county") or "",
        "owner": owner_name or company, "entity": bool(company and not owner_name),
        "owner_deceased": bool(owner.get("deceased")),
        "pr_name": pr_name, "rel_names": len(rel_names),
        "phones_live": len(live), "phones_who_tagged": len(labelled),
        "lists": ", ".join(lists), "tags": ", ".join(ptags),
        "deceased_signal": signal,
    }


# ----------------------------------------------------------------------------- report
def _table(title: str, rows: dict[str, Counter], cols: list[str]) -> list[str]:
    out = [f"### {title}", "", "| | " + " | ".join(cols) + " | total |",
           "|---|" + "---|" * (len(cols) + 1)]
    tot = Counter()
    for k in sorted(rows):
        c = rows[k]
        out.append(f"| {k} | " + " | ".join(str(c.get(x, 0)) for x in cols)
                   + f" | {sum(c.values())} |")
        tot.update(c)
    out.append("| **total** | " + " | ".join(f"**{tot.get(x, 0)}**" for x in cols)
               + f" | **{sum(tot.values())}** |")
    return out + [""]


def summarise(name: str, rows: list[dict], gone: int, run: Path) -> str:
    by_lane: dict[str, Counter] = defaultdict(Counter)
    by_who: dict[str, Counter] = defaultdict(Counter)
    by_status: dict[str, Counter] = defaultdict(Counter)
    fam_pinned: Counter = Counter()
    fam_unpinned: Counter = Counter()
    dial = [r for r in rows if r["lane"] != "other"]
    for r in rows:
        by_lane[r["lane"]][r["board"]] += 1
        by_who[r["who_signal"]][r["board"]] += 1
        by_status[r["status"] or "(blank)"][r["board"]] += 1
        for f in filter(None, r["pinned_families"].split(" | ")):
            fam_pinned[f] += 1
        if r["board"] == "UNPINNED":
            for f in filter(None, r["all_families"].split(" | ")):
                fam_unpinned[f] += 1

    no_pin = [r for r in rows if r["board"] != "PINNED"]
    no_pin_dial = [r for r in dial if r["board"] != "PINNED"]
    unclear = [r for r in rows if r["who_signal"] == "UNCLEAR"]
    no_name = [r for r in dial if not r["has_contact_name"]]
    live_phones = sum(r["phones_live"] for r in rows)
    tagged = sum(r["phones_who_tagged"] for r in rows)

    scope = ("dial presets only" if all(r["lane"] != "other" for r in rows) else
             "whole book; parked records board-only"
             if any(r["who_signal"] == "(not read)" for r in rows) else "whole book")
    L = [f"# Dialer board audit: {name} ({scope})", "",
         f"Run {time.strftime('%Y-%m-%d %H:%M')}. Read-only. Records assigned: {len(rows)}"
         + (f" (+{gone} gone / 404)" if gone else "") + f"; in a ready-to-call lane: {len(dial)}.",
         "",
         "## Headline", "",
         f"- No pinned note: **{len(no_pin)} of {len(rows)}** assigned records "
         f"({sum(1 for r in no_pin if r['board'] == 'NONE')} with no message at all, "
         f"{sum(1 for r in no_pin if r['board'] == 'UNPINNED')} with messages but none pinned).",
         f"- In the dial presets (02-05 of the CALL folders): **{len(no_pin_dial)} of {len(dial)}** "
         "have no pinned note.",
         f"- Owner block UNCLEAR (probate record, nobody wrote who the owner is): **{len(unclear)}**.",
         f"- Dial-lane records with NO contact name anywhere (no owner name, PR or REL): **{len(no_name)}**.",
         f"- Live phones carrying a who-tag (Owner.N / Pr.N / RelN.M): **{tagged} of {live_phones}**.",
         ""]
    L += _table("Board state by lane", by_lane, BOARD_BUCKETS)
    L += _table("Board state by what the record says about the owner block", by_who, BOARD_BUCKETS)
    L += _table("Board state by property status", by_status, BOARD_BUCKETS)
    L += ["### Pinned note families", ""] + \
         [f"- {f}: {n}" for f, n in fam_pinned.most_common()] + [""]
    L += ["### Families present but NOT pinned (on UNPINNED records)", ""] + \
         [f"- {f}: {n}" for f, n in fam_unpinned.most_common()] + [""]
    if unclear:
        L += ["### UNCLEAR records (worklist)", "",
              "| lane | status | address | owner block | lists | board |", "|---|---|---|---|---|---|"]
        for r in sorted(unclear, key=lambda r: (r["lane"], r["street"])):
            L.append(f"| {r['lane']} | {r['status']} | {r['street']}, {r['city']} {r['zip']} | "
                     f"{r['owner']} | {r['lists']} | {r['board']} |")
        L.append("")
    L += [f"Rows: `{run / 'records.csv'}`; cache: `{run / 'detail.json'}`."]
    return "\n".join(L)


# ----------------------------------------------------------------------------- main
def audit_one(api, name: str, who: str, fresh: bool, count_only: bool,
              lanes_only: bool = False, rest_light: bool = False) -> int:
    run = RUN / name.replace(" ", "_")
    run.mkdir(parents=True, exist_ok=True)
    say(f"\n=== {name}  ({who}) ===")

    # THE GUARD: an unrecognised query key is silently ignored and the count comes
    # back as the account total looking like a real answer.
    total = count(api, {})
    q = {"must": {"assigned_to": who}}
    n = count(api, q)
    say(f"  account total {total}; assigned to {name}: {n}")
    if n == total:
        say("FAIL: assignee count equals the account total -- filter ignored; stopping.")
        return 3
    if n > OFFSET_CEILING:
        say(f"FAIL: {n} records is past the {OFFSET_CEILING} offset ceiling; needs bucketing.")
        return 3
    lanes = lane_members(api, who)
    if count_only:
        say("--count-only: stopping before hydration.")
        return 0

    # --lanes-only: the dial lanes answer the dialing question in minutes; the whole
    # book (5,851 for Bagoury on 2026-10-05) is a two-hour read -- run it separately.
    light: dict[str, dict] = {}
    if lanes_only:
        uuids = list(lanes)
    else:
        thin = list_thin(api, q, n)
        uuids = list(thin)
        if rest_light:
            # --rest-light: the parked remainder gets the board read only (1 GET, not 3)
            light = {u: row for u, row in thin.items() if u not in lanes}
    # a dial-lane record whose detail says this dialer belongs in the book even if the
    # thin index lagged the assignee query (index lag, seen on every fill)
    for u in lanes:
        if u not in uuids:
            uuids.append(u)
    cache = hydrate(api, uuids, run / "detail.json", fresh, light=light)

    rows, gone = [], 0
    for u in uuids:
        e = cache.get(u)
        if not e or e.get("_gone"):
            gone += 1
            continue
        if not e.get("light") and (e["detail"].get("assigned_to") or "") != who:
            continue   # moved since the index read; not this dialer's any more
        rows.append(classify(u, e, lanes.get(u, "other")))

    with (run / "records.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["uuid"])
        w.writeheader()
        w.writerows(rows)
    md = summarise(name, rows, gone, run)
    (run / "summary.md").write_text(md, encoding="utf-8")
    say("\n" + md)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dialer", default=DEFAULT_DIALER)
    ap.add_argument("--all-dialers", action="store_true", help=f"audit {DIALERS}")
    ap.add_argument("--count-only", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="ignore the detail cache")
    ap.add_argument("--lanes-only", action="store_true",
                    help="hydrate only the records in the CALL dial presets")
    ap.add_argument("--rest-light", action="store_true",
                    help="whole book, but records outside the dial presets get the board "
                         "read only (1 GET each instead of 3)")
    args = ap.parse_args()

    names = DIALERS if args.all_dialers else [args.dialer]
    uuid_of = uuid_of_name()
    for nm in names:
        if nm not in uuid_of:
            say(f"no uuid known for {nm!r}; have {sorted(uuid_of)}")
            return 2

    # the summary echoes owner names; a cp1252 console dies on the first accented one
    # (seen 2026-10-05 AFTER the files were written -- exit 1 with a complete run dir)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    api = Api()
    say("JWT minted OK")
    rc = 0
    for nm in names:
        rc = max(rc, audit_one(api, nm, uuid_of[nm], args.fresh, args.count_only,
                               lanes_only=args.lanes_only, rest_light=args.rest_light))
    return rc


if __name__ == "__main__":
    sys.exit(main())
