"""Fix the two VA records whose fresh foreclosure notice landed while their
status still said `Auction Date Passed` (2026-09-07, plan step 3).

6501 Princeton Dr and 4163 Legato Rd were confirmed on the account 2026-09-06
with stale statuses from the Feb-2026 MDDC era. Today's upload carried their
fresh auction dates but the wizard cannot update FIELDS on existing records
(known limitation), so the account still shows the old foreclosure_date. This
writes, per record: `foreclosure_date` = the new auction date (from
output/upload_ready_va_20260907T084805.csv) and `status` = No Answer (an
is_active title, verbatim -- the vocabulary rule) so they re-enter the call
lanes. Prior values are backed up first; the write is read back.

Usage:
    python -u src/scripts/va_stale_status_fix.py            # dry run (reads only)
    python -u src/scripts/va_stale_status_fix.py --commit
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from obituary_dp_batch import WriteApi  # noqa: E402

FIXES = [
    {"uuid": "401d566b-e6e1-4101-a9b6-f42020ab194a", "street": "6501 Princeton Dr",
     "foreclosure_date": "2026-09-29"},
    {"uuid": "c8d83261-0747-4932-8f79-2ab5a748e76c", "street": "4163 Legato Rd",
     "foreclosure_date": "2026-09-30"},
]
NEW_STATUS = "No Answer"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    api = WriteApi()
    backup = []
    fails = 0
    for f in FIXES:
        st, before = api.get(f"/api/internal/property/{f['uuid']}/")
        if st != 200:
            print(f"READ FAIL {f['street']}: {st}")
            fails += 1
            continue
        print(f"{f['street']}: status={before.get('status')!r} "
              f"foreclosure_date={before.get('foreclosure_date')!r} "
              f"-> {NEW_STATUS!r} / {f['foreclosure_date']!r}")
        backup.append({"uuid": f["uuid"], "street": f["street"],
                       "status": before.get("status"),
                       "foreclosure_date": before.get("foreclosure_date")})
        if not a.commit:
            continue
        wst, _ = api.write("PATCH", f"/api/internal/property/{f['uuid']}/",
                           {"foreclosure_date": f["foreclosure_date"], "status": NEW_STATUS})
        st2, after = api.get(f"/api/internal/property/{f['uuid']}/")
        ok = (after.get("status") == NEW_STATUS
              and after.get("foreclosure_date") == f["foreclosure_date"])
        print(f"  PATCH {wst} | read-back: status={after.get('status')!r} "
              f"foreclosure_date={after.get('foreclosure_date')!r} "
              f"{'OK' if ok else 'MISMATCH'}")
        fails += (not ok)

    if a.commit and backup:
        p = ROOT / "output" / f"va_stale_status_backup_{datetime.now():%Y%m%dT%H%M%S}.json"
        p.write_text(json.dumps(backup, indent=1), encoding="utf-8")
        print("prior values ->", p)
    if not a.commit:
        print("DRY RUN: nothing written. Re-run with --commit.")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
