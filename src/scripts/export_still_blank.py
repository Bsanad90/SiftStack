"""Export the records still missing structure_type after the enrichment run,
so they can be checked/fixed in DataSift and re-enriched.

Usage:
    python src/scripts/export_still_blank.py [--cache output/live_account_pull.json] [--out output/still_incomplete.csv]
"""
import argparse
import csv
import json
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="output/live_account_pull.json")
    ap.add_argument("--out", default="output/still_incomplete.csv")
    args = ap.parse_args()

    blob = json.loads(Path(args.cache).read_text(encoding="utf-8"))
    rows = []
    for e in blob.get("records") or []:
        r = e.get("record")
        if not r:
            continue
        if (r.get("structure_type") or "").strip():
            continue
        addr = r.get("address") or {}
        owner = r.get("owner") or {}
        rows.append({
            "uuid": e["uuid"],
            "owner": f"{(owner.get('first_name') or '').strip()} {(owner.get('last_name') or '').strip()}".strip()
                     or (owner.get("company") or ""),
            "street": addr.get("street") or "",
            "city": addr.get("city") or "",
            "state": addr.get("state") or "",
            "zip": (addr.get("postal_code") or "")[:5],
            "county": addr.get("county") or "",
            "status": r.get("status") or "",
            "lists": ", ".join(r.get("lists") or []),
            "apn": r.get("apn") or "",
            "parcel_id": r.get("parcel_id") or "",
        })

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                            ["uuid", "owner", "street", "city", "state", "zip",
                             "county", "status", "lists", "apn", "parcel_id"])
        w.writeheader()
        w.writerows(rows)

    print(f"{len(rows)} still-incomplete records -> {args.out}")


if __name__ == "__main__":
    main()
