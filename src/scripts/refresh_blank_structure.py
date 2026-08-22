"""Targeted refresh of just the records that had a blank structure_type in
the completed live pull (output/live_account_pull.json), after enrichment
was run on the account in DataSift.

Re-fetching all 26,645 records again would take ~6 hours like the original
pull. This re-hydrates only the ~8,282 uuids that were blank, which should
take roughly 8282 * 0.45s ~= 60 minutes, and merges the refreshed detail
back into the same cache file in place so score_live_pull.py and
score_live_pull_townhouse_condo.py can be rerun against current data without
any changes to those scripts.

Usage:
    python src/scripts/refresh_blank_structure.py [--cache output/live_account_pull.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_pull import LiveApi  # noqa: E402

CHECKPOINT_EVERY = 250


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="output/live_account_pull.json")
    ap.add_argument("--limit", type=int, default=0, help="test on only the first N targets")
    args = ap.parse_args()

    cache = Path(args.cache)
    blob = json.loads(cache.read_text(encoding="utf-8"))
    records = blob["records"]

    targets = [i for i, e in enumerate(records)
               if e.get("record") and not (e["record"].get("structure_type") or "").strip()]
    if args.limit:
        targets = targets[:args.limit]
    print(f"{len(targets)} records to refresh (blank structure_type)", flush=True)

    api = LiveApi()
    print("JWT minted OK", flush=True)

    t0 = time.time()
    changed = still_blank = errors = 0
    for n, idx in enumerate(targets, 1):
        u = records[idx]["uuid"]
        status, body = api.get(f"/api/internal/property/{u}/")
        if status == 200:
            new_structure = (body.get("structure_type") or "").strip()
            if new_structure:
                changed += 1
            else:
                still_blank += 1
            records[idx]["record"] = body
            records[idx]["error"] = None
        else:
            errors += 1
            records[idx]["error"] = f"refresh failed: {status}: {str(body)[:150]}"

        if n % CHECKPOINT_EVERY == 0 or n == len(targets):
            blob["records"] = records
            blob["refreshed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            blob["refresh_note"] = "blank-structure_type records re-hydrated after account enrichment"
            cache.write_text(json.dumps(blob, default=str), encoding="utf-8")
            elapsed = time.time() - t0
            rate = n / elapsed if elapsed else 0
            remaining = (len(targets) - n) / rate if rate else 0
            print(f"  {n}/{len(targets)}  now-populated={changed} still-blank={still_blank} "
                  f"errors={errors}  elapsed={elapsed/60:.1f}m  ETA={remaining/60:.1f}m", flush=True)

    print(f"\nDone. now-populated={changed} still-blank={still_blank} errors={errors}")
    print(f"Updated {cache} in place")


if __name__ == "__main__":
    main()
