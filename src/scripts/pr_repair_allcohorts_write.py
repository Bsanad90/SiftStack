"""Write the all-cohort PR First/Last repairs from the reviewed plan.

Input is `output/dp_1122branch/pr_repair_review_all_cohorts.csv` (built and
frozen 2026-09-04): 4,843 records whose PR fields hold junk (match-notes, row
numbers, dates, "FALSE") or are empty, each with a bucket and a proposed name
from the Probate Extraction workbook. This driver writes ONLY the buckets it
is told to (default `clean,variant` -- the ones the live sample proved SOUND);
`co_pr` and `multi_estate` require live Register of Wills verification first
and are refused here.

Safety, in the order it runs per record:
  1. GET the record's live PR fields. The review snapshot is from 09-04, so:
       live == proposed             -> skip (already repaired)
       live != snapshot AND != ""   -> HOLD as conflict (the record changed
                                       since the review; never stomp a newer
                                       correction)
       otherwise                    -> write
  2. The live prior values are appended to the backup JSONL BEFORE the PATCH.
  3. The PATCH echo must return both new values or the row is a FAIL.
Resume: uuids already in the write log are skipped, so a killed run re-runs
clean. Nothing in this file touches co_pr/multi_estate/no_match rows.

Usage:
    python -u src/scripts/pr_repair_allcohorts_write.py            # dry run
    python -u src/scripts/pr_repair_allcohorts_write.py --probe 10 --commit
    python -u src/scripts/pr_repair_allcohorts_write.py --commit
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from obituary_dp_batch import WriteApi  # noqa: E402

OUT = ROOT / "output" / "dp_1122branch"
REVIEW_CSV = OUT / "pr_repair_review_all_cohorts.csv"
RUN = ROOT / "output" / "pr_repair_allcohorts"

PR_FIRST_UUID = "1dd4aaf9-7304-4a25-b684-54a342aedc04"
PR_LAST_UUID = "eeac8728-6773-4f62-9b03-ef968da073a0"
WRITABLE_BUCKETS = {"clean", "variant"}


def read_live_pr(api: WriteApi, uuid: str) -> tuple[int, str, str]:
    st, cf = api.get(f"/api/internal/property/{uuid}/custom-field/")
    rows = cf if isinstance(cf, list) else ((cf.get("results") or []) if isinstance(cf, dict) else [])
    vals = {((r.get("custom_field") or {}).get("uuid") or ""): (r.get("value") or "") for r in rows}
    return st, str(vals.get(PR_FIRST_UUID) or "").strip(), str(vals.get(PR_LAST_UUID) or "").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--buckets", default="clean,variant")
    ap.add_argument("--probe", type=int, default=0, help="stop after N written records")
    ap.add_argument("--limit", type=int, default=0, help="stop after N rows considered")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    buckets = {b.strip() for b in a.buckets.split(",") if b.strip()}
    refused = buckets - WRITABLE_BUCKETS
    if refused:
        print(f"REFUSED buckets {sorted(refused)}: co_pr/multi_estate need live RoW "
              "verification first; no_match has nothing to write.")
        return 2

    rows = [r for r in csv.DictReader(open(REVIEW_CSV, encoding="utf-8-sig"))
            if r["bucket"] in buckets]
    print(f"{len(rows)} rows in buckets {sorted(buckets)}")

    RUN.mkdir(parents=True, exist_ok=True)
    writelog_p = RUN / "writelog.jsonl"
    done: set[str] = set()
    if writelog_p.exists():
        for line in open(writelog_p, encoding="utf-8"):
            try:
                e = json.loads(line)
                if e.get("verified"):
                    done.add(e["uuid"])
            except json.JSONDecodeError:
                pass
    if done:
        print(f"resume: {len(done)} already verified in the write log")

    if not a.commit:
        no_proposed = sum(1 for r in rows if not (r["proposed_first"].strip() and r["proposed_last"].strip()))
        print(f"DRY RUN: would consider {len(rows) - len(done)} records "
              f"({no_proposed} lack a proposed name and would be skipped). "
              "Re-run with --commit.")
        return 0

    api = WriteApi()
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = open(RUN / f"backup_{ts}.jsonl", "a", encoding="utf-8")
    log = open(writelog_p, "a", encoding="utf-8")
    holds: list[dict] = []
    counts = {"written": 0, "already_done": 0, "conflict": 0, "no_proposed": 0,
              "read_fail": 0, "write_fail": 0, "resumed": 0}

    considered = 0
    try:
        for r in rows:
            if a.limit and considered >= a.limit:
                break
            considered += 1
            uuid = r["uuid"]
            if uuid in done:
                counts["resumed"] += 1
                continue
            new_f, new_l = r["proposed_first"].strip(), r["proposed_last"].strip()
            if not (new_f and new_l):
                counts["no_proposed"] += 1
                holds.append({**r, "hold_reason": "no_proposed"})
                continue

            st, live_f, live_l = read_live_pr(api, uuid)
            if st != 200:
                counts["read_fail"] += 1
                holds.append({**r, "hold_reason": f"read_{st}"})
                continue
            if live_f == new_f and live_l == new_l:
                counts["already_done"] += 1
                log.write(json.dumps({"uuid": uuid, "street": r["street"],
                                      "verified": True, "note": "already_done"}) + "\n")
                continue
            snap_f = (r["current_pr_first"] or "").strip()
            snap_l = (r["current_pr_last"] or "").strip()
            if (live_f, live_l) != (snap_f, snap_l) and (live_f or live_l):
                counts["conflict"] += 1
                holds.append({**r, "hold_reason": "conflict",
                              "live_first": live_f, "live_last": live_l})
                continue

            backup.write(json.dumps({"uuid": uuid, "street": r["street"],
                                     "prior_first": live_f, "prior_last": live_l,
                                     "new_first": new_f, "new_last": new_l,
                                     "bucket": r["bucket"]}) + "\n")
            backup.flush()
            body = [{"field_uuid": PR_FIRST_UUID, "value": new_f},
                    {"field_uuid": PR_LAST_UUID, "value": new_l}]
            wst, resp = api.write(
                "PATCH", f"/api/internal/property/{uuid}/custom-field/update-values/", body)
            got = {}
            if wst == 200 and isinstance(resp, list):
                for row in resp:
                    cf = (row.get("custom_field") or {})
                    got[cf.get("uuid")] = row.get("value")
            good = wst == 200 and got.get(PR_FIRST_UUID) == new_f and got.get(PR_LAST_UUID) == new_l
            counts["written" if good else "write_fail"] += 1
            log.write(json.dumps({"uuid": uuid, "street": r["street"], "bucket": r["bucket"],
                                  "prior_first": live_f, "prior_last": live_l,
                                  "new_first": new_f, "new_last": new_l,
                                  "status": wst, "verified": good}) + "\n")
            log.flush()
            if not good:
                print(f"  FAIL {r['street']}: status {wst} echo {got}")
            n = counts["written"]
            if a.probe and n >= a.probe:
                print(f"probe cap reached: {n} written")
                break
            if n and n % 100 == 0:
                print(f"{n} written / {considered} considered "
                      f"(conflicts {counts['conflict']}, already done {counts['already_done']})",
                      flush=True)
    finally:
        backup.close()
        log.close()
        if holds:
            hp = RUN / f"holds_{ts}.json"
            json.dump(holds, open(hp, "w", encoding="utf-8"), indent=1)
            print("holds written:", hp)

    print("SUMMARY:", counts)
    return 0 if counts["write_fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
