"""Upload output/mddc_trustee_sale.csv into REIsift via the internal API.

Reuses datasift_api_upload.py's upload_rows()/build_property() as-is (proven
against a shared REIsift account already) rather than duplicating the API
contract. This script's only job is the MDDC-specific column transform:

  street/city/state/zip          -> Property Street/City/State/ZIP Code
  (no owner name in this data)   -> Owner First/Last Name left BLANK, so
                                     build_property() omits person keys
                                     entirely rather than sending "" (the API
                                     rejects a blank first_name)
  county, loan_principal,
  auction_date, publication,
  notice_text_snippet, source_url -> folded into Notes (no MDDC custom
                                     fields exist on the account, and
                                     creating them needs datasift_schema_setup.py,
                                     which depends on an external checkout not
                                     present on this machine)
  Lists  = "Foreclosure" (shared with Ty's TN dataset on this account, by
            the user's explicit choice)
  Tags   = "Claude first batch 8.22" (REQUIRED -- this is the only thing that
            scopes every later action, e.g. skip trace, to just this batch;
            the shared "Foreclosure" list also holds Ty's TN records, so any
            action that selects by LIST ALONE would sweep those in too)

Usage:
    python src/scripts/mddc_datasift_upload.py --limit 1            # preview, no write
    python src/scripts/mddc_datasift_upload.py --limit 1 --commit   # one record, verify by readback
    python src/scripts/mddc_datasift_upload.py --commit             # all 139
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import datasift_api_upload as api_upload  # noqa: E402

SOURCE_CSV = ROOT / "output" / "mddc_trustee_sale.csv"
TRANSFORMED_CSV = ROOT / "output" / "mddc_datasift_upload_transformed.csv"

LIST_NAME = "Foreclosure"
BATCH_TAG = "FTM"  # entry tag for the 05 FTM - CALL / 06 FTM - MAIL preset lanes (was "Claude first batch 8.22")


def build_notes(row: dict) -> str:
    parts = []
    if row.get("notice_type"):
        parts.append(f"Notice Type: {row['notice_type']}")
    if row.get("county"):
        parts.append(f"County: {row['county']}")
    if row.get("loan_principal"):
        parts.append(f"Loan Principal: ${row['loan_principal']}")
    if row.get("auction_date"):
        parts.append(f"Auction Date: {row['auction_date']}")
    if row.get("publication"):
        parts.append(f"Publication: {row['publication']} ({row.get('date_published', '')})")
    if row.get("source_url"):
        parts.append(f"Source: {row['source_url']}")
    if row.get("notice_text_snippet"):
        parts.append("---")
        parts.append(row["notice_text_snippet"])
    return "\n".join(parts)


def transform(source_csv: Path, out_csv: Path) -> int:
    with source_csv.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    fieldnames = [
        "Property Street Address", "Property City", "Property State", "Property ZIP Code",
        "Owner First Name", "Owner Last Name",
        "Lists", "Tags", "Notes",
    ]
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            if not row.get("street"):
                continue  # nothing to upsert-by-address against
            writer.writerow({
                "Property Street Address": row["street"],
                "Property City": row.get("city", ""),
                "Property State": row.get("state", "") or "MD",
                "Property ZIP Code": row.get("zip", ""),
                "Owner First Name": "",
                "Owner Last Name": "",
                "Lists": LIST_NAME,
                "Tags": BATCH_TAG,
                "Notes": build_notes(row),
            })
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(SOURCE_CSV))
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.15)
    args = ap.parse_args()

    n = transform(Path(args.csv), TRANSFORMED_CSV)
    skipped = 0
    with Path(args.csv).open(encoding="utf-8") as f:
        total = sum(1 for _ in csv.DictReader(f))
    skipped = total - n
    print(f"Transformed {n}/{total} rows (skipped {skipped} with no street address) -> {TRANSFORMED_CSV}")

    res = api_upload.upload_csv(str(TRANSFORMED_CSV), commit=args.commit,
                                 limit=args.limit, sleep=args.sleep)

    if args.commit and args.limit == 1 and res.get("created"):
        print("\nReading back the one committed record to verify address/list/tag/notes...")
        verify_readback()
    return 0


def verify_readback():
    a = api_upload.Api()
    r = a.call(f"/api/internal/property/?limit=1&offset=0&ordering=-created")
    row = (r.get("results") or [None])[0]
    if not row:
        print("  Could not read back a record.")
        return
    addr = row.get("address") or {}
    print(f"  address: {addr.get('street')}, {addr.get('city')}, {addr.get('state')} {addr.get('postal_code')}")
    print(f"  lists: {row.get('lists')}")
    print(f"  tags: {row.get('tags')}")


if __name__ == "__main__":
    raise SystemExit(main())
