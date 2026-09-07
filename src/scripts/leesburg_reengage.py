"""Re-engage 6141 Leesburg Pike Apt 209 (Basem's go, 2026-09-07).

Kenneth Tetteh's record sits in `not_interested`, but a fresh trustee-sale
notice published 09-07 (rejected from upload only for a missing auction date)
is a re-engagement signal: status -> `No Answer` so the record re-enters the
call lanes. The record is resolved by exact street + owner last name because
the account ALSO holds a typo duplicate of the same unit ("6141 Leesburg Oke",
owner "Kennedy Tetteh") that must NOT be touched. Backup + read-back as usual.

Usage: python -X utf8 -u src/scripts/leesburg_reengage.py [--commit]
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

WANT_STREET = "6141 Leesburg Pike Apt 209"
WANT_OWNER_FIRST = "Kenneth"
NEW_STATUS = "No Answer"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    api = WriteApi()
    st, body = api.search({"limit": 10, "offset": 0,
                           "query": {"must": {"search": WANT_STREET}}})
    hits = [h for h in (body.get("results") or [])
            if (h.get("address") or {}).get("street") == WANT_STREET
            and (h.get("owner") or {}).get("first_name") == WANT_OWNER_FIRST]
    if len(hits) != 1:
        print(f"REFUSED: expected exactly one {WANT_STREET!r}/{WANT_OWNER_FIRST} match, "
              f"got {len(hits)}")
        return 1
    uuid = hits[0]["uuid"]
    st0, before = api.get(f"/api/internal/property/{uuid}/")
    print(f"{WANT_STREET} ({uuid[:8]}): status {before.get('status')!r} -> {NEW_STATUS!r}")
    if not a.commit:
        print("DRY RUN. Re-run with --commit.")
        return 0
    wst, _ = api.write("PATCH", f"/api/internal/property/{uuid}/", {"status": NEW_STATUS})
    st2, after = api.get(f"/api/internal/property/{uuid}/")
    ok = after.get("status") == NEW_STATUS
    print(f"  PATCH {wst} | read-back status {after.get('status')!r} {'OK' if ok else 'MISMATCH'}")
    p = ROOT / "output" / f"leesburg_reengage_backup_{datetime.now():%Y%m%dT%H%M%S}.json"
    p.write_text(json.dumps({"uuid": uuid, "status": before.get("status")}, indent=1),
                 encoding="utf-8")
    print("prior value ->", p)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
