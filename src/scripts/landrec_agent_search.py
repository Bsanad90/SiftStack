"""MD Land Records + SDAT for the row-address-researcher agents -- ONE session at a time.

Basem, 2026-10-08: the research agents may log in to Land Records. The trap they must not
re-open: MDLANDREC demands the emailed access code when several sessions log in at once (4
Firecrawl workers on 2026-10-02: 1 in 4 searches dead, then every login). Ten parallel agents
would do exactly that. So every call here:
  * waits for a machine-wide lock (output/row_review/landrec.lock), one Land Records / SDAT
    session at a time across all agents;
  * waits while the Register of Wills fill is running (its own Land Records session);
  * counts against a DAILY CAP (env ROW_AGENT_LANDREC_CAP, default 300 calls) and stops below a
    Firecrawl credit FLOOR (env ROW_AGENT_FIRECRAWL_FLOOR, default 20,000): each call is one or
    more Firecrawl scrapes, the same metered service the fill uses.
Prints ONE JSON object on stdout. Read-only: no ledger, no sheet, no DataSift.

    python src/scripts/landrec_agent_search.py lookup  --county Frederick --last AMOSS --first EARL [--middle R]
    python src/scripts/landrec_agent_search.py parties --county Frederick --last RICE --first TRAVIS [--party grantor|grantee|both]
    python src/scripts/landrec_agent_search.py sdat    --county Frederick --street "106 STONEGATE DR" [--owner "EARL AMOSS"]

  lookup  = the fill's own ladder (grantee deeds -> deed PDF text -> SDAT owner check) for a NAME
            the fill did not try: maiden name, nickname, full middle name from the obituary.
  parties = the raw instrument index for a name (deeds where an heir / spouse / PR is a party,
            or the decedent as GRANTOR = sold). Rows: date, party, instrument_type, book_page, remarks.
  sdat    = current owner of record at a street (the tax roll), to confirm a candidate.
"""

from __future__ import annotations

import argparse
import json
import msvcrt
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
REPO = Path(__file__).resolve().parents[2]
STATE_DIR = REPO / "output" / "row_review"
LOCK = STATE_DIR / "landrec.lock"
BUDGET = STATE_DIR / "landrec_budget.json"
# Basem 2026-10-08: "increase the cap, I have a lot of unused credits" (97,796 of 100,000 left,
# period renews 11-04). 300 calls x ~10 credits worst case = ~3k/day. The FLOOR keeps credits for
# the daily pulls + the fill: below it the agents stop using Firecrawl entirely.
CAP = int(os.environ.get("ROW_AGENT_LANDREC_CAP", "300"))
FLOOR = int(os.environ.get("ROW_AGENT_FIRECRAWL_FLOOR", "20000"))
LOCK_WAIT_S = 30 * 60
COUNTIES = {"Montgomery", "Anne Arundel", "Frederick"}


def out(obj: dict, code: int = 0) -> int:
    print(json.dumps(obj, default=str))
    return code


def fill_running() -> bool:
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*md_register_of_wills_pull*' } | Measure-Object | "
          "ForEach-Object { $_.Count }")
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    return res.stdout.strip() not in ("", "0")


def firecrawl_remaining() -> int | None:
    """Credits left on the Firecrawl plan, or None if the usage endpoint can't be read."""
    import urllib.request
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    key = os.environ.get("FIRECRAWL_API_KEY", "")
    try:
        req = urllib.request.Request("https://api.firecrawl.dev/v1/team/credit-usage",
                                     headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(json.loads(r.read())["data"]["remaining_credits"])
    except Exception:  # noqa: BLE001
        return None


def take_budget() -> tuple[int | None, str]:
    """(calls left after taking one, "") or (None, why). Caller holds the lock.
    The credit balance is read on the first call of the day and every 25th after."""
    today = date.today().isoformat()
    data = json.loads(BUDGET.read_text(encoding="utf-8")) if BUDGET.exists() else {}
    used = data.get(today, 0)
    if used >= CAP:
        return None, f"daily Land Records cap reached ({CAP} calls); use other sources"
    credits = data.get("credits") if data.get("credits_date") == today else None
    if credits is None or used % 25 == 0:
        credits = firecrawl_remaining()
    if credits is not None and credits < FLOOR:
        return None, (f"Firecrawl balance {credits:,} is under the {FLOOR:,} floor kept for the daily "
                      "pulls; use other sources")
    BUDGET.write_text(json.dumps({today: used + 1, "credits": credits, "credits_date": today}),
                      encoding="utf-8")
    return CAP - used - 1, ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("lookup", "parties"):
        p = sub.add_parser(name)
        p.add_argument("--county", required=True)
        p.add_argument("--last", required=True)
        p.add_argument("--first", default="")
        p.add_argument("--middle", default="")
        if name == "parties":
            p.add_argument("--party", default="both", choices=["grantee", "grantor", "both"])
    s = sub.add_parser("sdat")
    s.add_argument("--county", required=True)
    s.add_argument("--street", required=True)
    s.add_argument("--owner", default="")
    args = ap.parse_args()
    if args.county not in COUNTIES:
        return out({"error": f"county must be one of {sorted(COUNTIES)}"}, 2)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK, "a+")
    start = time.time()
    while True:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            break
        except OSError:
            if time.time() - start > LOCK_WAIT_S:
                return out({"error": "waited 30 min for the Land Records lock; try later"}, 3)
            time.sleep(5)
    try:
        while fill_running():
            if time.time() - start > LOCK_WAIT_S:
                return out({"error": "the Register of Wills fill is still running; try later"}, 3)
            time.sleep(30)
        left, why = take_budget()
        if left is None:
            return out({"error": why}, 4)

        import md_land_records_lookup as landrec
        try:
            if args.cmd == "lookup":
                name = f"{args.first} {args.middle} {args.last}".split()
                res = landrec.find_property_for_decedent(
                    args.county, args.last.upper(), args.first.upper(),
                    full_name=" ".join(name).upper(), middle_name=args.middle.upper())
            elif args.cmd == "parties":
                rows = landrec.search_grantee(args.county, args.last.upper(), args.first.upper(),
                                              middle_name=args.middle[:1].upper(), party=args.party)
                res = {"rows": [{k: r[k] for k in ("date", "party", "instrument_type", "book_page", "remarks")}
                                for r in rows[:40]], "total_rows": len(rows)}
            else:
                res = landrec.lookup_sdat_by_address(args.county, args.street.upper(), owner_name=args.owner.upper())
                res = {"found": bool(res), "parcel": res}
        except Exception as exc:  # noqa: BLE001 -- infrastructure, not a finding
            return out({"error": f"{type(exc).__name__}: {exc}", "calls_left_today": left}, 5)
        if isinstance(res, dict):
            res["calls_left_today"] = left
        return out(res)
    finally:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        fh.close()


if __name__ == "__main__":
    sys.exit(main())
