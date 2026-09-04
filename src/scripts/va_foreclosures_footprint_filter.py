"""Assemble the VA foreclosure upload-candidate file for the narrowed footprint.

One-off for the 2026-09-01 review of the VA Popular Search 4 pulls. Merges the
per-county full-text-enriched runs, normalizes the county noise the notices
carry ("FAIRFAX", "Prin", "Spotslyvani"...), keeps only the footprint counties
(Fairfax + Arlington, per the 14->6 narrowing), drops the known commercial
notices, dedupes republished notices by (street, zip), and recovers the two
Arlington leads whose blank-street rows were hand-deleted from the deep file
(their addresses sit on line 2 of the notice text, a headline shape the parser
does not handle yet).

Writes:
  output/va_foreclosures_footprint.csv          -- upload candidates, for review
  output/va_foreclosures_footprint_dropped.csv  -- every removed row + drop_reason

Nothing here uploads anything.

Usage:
  python src/scripts/va_foreclosures_footprint_filter.py \
      --in output/va_fairfax_ft.csv --in output/va_arlington_ft.csv \
      --recover-from output/va_foreclosures_final.csv
"""

import argparse
import csv
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Sibling import (both live in src/scripts/): the auction-date parser and the
# page-chrome stripper stay single-sourced in the pull script.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from va_trustee_sale_pull import parse_auction_date, strip_detail_page_chrome  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]

FOOTPRINT = {"Fairfax", "Arlington"}

# Notices confirmed commercial on review 2026-09-01 (Basem's eyeball pass):
# a $65M office building and an office condominium unit. Matched on street
# so a re-run of the pulls cannot resurrect them.
COMMERCIAL_STREETS = {
    "13001 worldgate drive": "commercial ($65M office building)",
    "2501 north glebe road": "commercial (office condo unit 301)",
}

# The grid rows truncate county names and shout some of them. Prefix-map the
# observed variants (FAIRFAX, Prin, Spotsy, Spotslyvani, Spotsylvan, ALEXANDRIA,
# Alexandria city, Fredericksburg city...) onto canonical names.
_COUNTY_PREFIXES = [
    ("prin", "Prince William"),
    ("spots", "Spotsylvania"),
    ("alexandria", "Alexandria city"),
    ("fredericksburg", "Fredericksburg city"),
    ("manassas park", "Manassas Park city"),
]


def canon_county(raw: str) -> str:
    c = (raw or "").strip().casefold()
    if not c:
        return ""
    for prefix, name in _COUNTY_PREFIXES:
        if c.startswith(prefix):
            return name
    return c.title()


_PUB_FORMATS = ("%A, %B %d, %Y", "%B %d, %Y", "%m/%d/%Y")


def pub_date(row) -> datetime:
    raw = (row.get("date_published") or "").strip()
    for fmt in _PUB_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return datetime.min


def dedup_key(row):
    street = re.sub(r"\W+", " ", (row.get("street") or "").casefold()).strip()
    return (street, (row.get("zip") or "").strip())


# The two headline shapes the pull's parse_trustee_headline_address misses put
# the address on its own line under the title, e.g.
#   NOTICE OF SUBSTITUTE TRUSTEE SALE\n3407 South Stafford Street, Unit: B, Arlington, VA 22206
#   COMMISSIONER'S SALE - NOTICE\nOF DEFAULT AND FORECLOSURE SALE\n2028 S Kenmore Street, Arlington, Viginia 22204
# ("Viginia" is the notice's own typo.) Street = leading house number up to the
# city; an optional Unit segment stays with the street per the c5d4fd5 rule
# that dropping a unit number names a DIFFERENT property.
_SECOND_LINE_ADDR_RE = re.compile(
    r"^\s*(\d+[^,\n]+(?:,\s*Unit:?\s*[\w#-]+)?),\s*([A-Za-z .]+?),\s*"
    r"V(?:A|a|irginia|iginia)\.?,?\s+(\d{5})\s*$",
    re.M,
)

_CITY_COUNTY = {"arlington": "Arlington"}  # only recover what we can place


def repair_blank_street(row: dict, source: str) -> bool:
    """Re-parse a blank-street row's address off line 2 of its notice text.

    Returns True when the row was repaired in place (and stamps
    recovered_from). Prefers full_text when the row has it; falls back to the
    grid snippet, which carries the headline + address comfortably.
    """
    if (row.get("street") or "").strip():
        return False
    text = row.get("full_text") or ""
    if not text or text.startswith("[fetch failed"):
        text = row.get("notice_text_snippet") or ""
    m = _SECOND_LINE_ADDR_RE.search(text)
    if not m:
        return False
    street, city, zip5 = (g.strip() for g in m.groups())
    county = _CITY_COUNTY.get(city.casefold())
    if not county:
        return False
    row.update(street=street, city=city, state="VA", zip=zip5, county=county,
               recovered_from=source)
    return True


def recover_blank_street_rows(path: Path):
    """Yield repaired rows from an older pull whose street failed to parse."""
    if not path.exists():
        print(f"  recover-from file not found, skipping: {path}")
        return
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            row = dict(row)
            if repair_blank_street(row, path.name):
                yield row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inputs", action="append", required=True,
                    help="Enriched pull CSV; repeatable.")
    ap.add_argument("--recover-from", default="",
                    help="Older pull CSV whose blank-street rows should be re-parsed "
                         "from their snippets and appended (Arlington only).")
    ap.add_argument("--out", default="output/va_foreclosures_footprint.csv")
    ap.add_argument("--full-text-cap", type=int, default=1200,
                    help="Truncate full_text in the KEPT file so it stays reviewable "
                         "(the auction date is already extracted). 0 = keep whole.")
    args = ap.parse_args()

    rows = []
    for src in args.inputs:
        path = ROOT / src
        with path.open(encoding="utf-8", newline="") as fh:
            batch = list(csv.DictReader(fh))
        print(f"  read {len(batch):3d} rows from {src}")
        rows.extend(batch)

    total_in = len(rows)
    kept, dropped = [], []

    # Canonicalize county in place so downstream grouping stops fragmenting.
    # Blank-street rows (the two multi-line headline shapes the pull's parser
    # misses) are repaired from their own text first, so a fresh pull's copy
    # of those notices survives the footprint gate under its real county.
    n_inline_repairs = n_reparsed = 0
    for row in rows:
        row.setdefault("recovered_from", "")
        if repair_blank_street(row, "own text"):
            n_inline_repairs += 1
        row["county"] = canon_county(row.get("county"))
        # ALWAYS re-derive the auction date from a real full text: earlier
        # enriched pulls stored dates poisoned by the detail page's own
        # "Notice Publish Date" header (every row's "auction" was its publish
        # date, 2026-09-01), so a stored value cannot be trusted over a fresh
        # parse of the same text.
        ft = row.get("full_text") or ""
        if ft and not ft.startswith("[fetch failed"):
            new = parse_auction_date(strip_detail_page_chrome(ft),
                                     row.get("date_published") or "")
            if new != (row.get("auction_date") or ""):
                n_reparsed += 1
            row["auction_date"] = new
    if n_inline_repairs:
        print(f"  repaired {n_inline_repairs} blank-street row(s) from their own text")
    if n_reparsed:
        print(f"  re-parsed auction_date from full text on {n_reparsed} row(s)")

    # Footprint + commercial gates.
    for row in rows:
        street_key = re.sub(r"\s+", " ", (row.get("street") or "").casefold()).strip()
        if row["county"] not in FOOTPRINT:
            row["drop_reason"] = f"out_of_footprint ({row['county'] or 'no county'})"
            dropped.append(row)
        elif street_key in COMMERCIAL_STREETS:
            row["drop_reason"] = COMMERCIAL_STREETS[street_key]
            dropped.append(row)
        else:
            kept.append(row)

    # Recovered leads join before dedup so a fresh pull's own copy wins.
    n_appended = 0
    if args.recover_from:
        recovered = list(recover_blank_street_rows(ROOT / args.recover_from))
        print(f"  recovered {len(recovered)} blank-street row(s) from {args.recover_from}")
        kept.extend(recovered)
        n_appended = len(recovered)

    # Foreclosure notices republish weekly by law; keep the freshest per
    # address. On a publication-date tie (the same notice in two merged pull
    # files), the enriched copy wins: a parsed auction_date beats none, then
    # a real full_text beats blank -- otherwise merging the un-enriched deep
    # file would silently discard the paid enrichment.
    def _rank(row):
        full = row.get("full_text") or ""
        return (pub_date(row),
                bool((row.get("auction_date") or "").strip()),
                bool(full) and not full.startswith("[fetch failed"))

    by_key = {}
    for row in kept:
        key = dedup_key(row)
        prior = by_key.get(key)
        if prior is None:
            by_key[key] = row
        else:
            newer, older = (row, prior) if _rank(row) > _rank(prior) else (prior, row)
            older["drop_reason"] = "duplicate (republished notice)"
            dropped.append(older)
            by_key[key] = newer
    kept = sorted(by_key.values(), key=lambda r: (r["county"], pub_date(r)), reverse=False)

    fieldnames = ["notice_id", "publication", "date_published", "notice_type", "street",
                  "city", "state", "zip", "county", "loan_principal", "auction_date",
                  "popular_search", "notice_text_snippet", "full_text", "recovered_from"]

    out_path = ROOT / args.out
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in kept:
            row = dict(row)
            if args.full_text_cap:
                row["full_text"] = (row.get("full_text") or "")[: args.full_text_cap]
            w.writerow(row)

    dropped_path = out_path.with_name(out_path.stem + "_dropped.csv")
    with dropped_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames + ["drop_reason"], extrasaction="ignore")
        w.writeheader()
        w.writerows(dropped)

    # Reconciliation + review summary. Only APPENDED recover-from rows enter
    # after total_in; inline blank-street repairs were already counted there.
    if len(kept) + len(dropped) != total_in + n_appended:
        print(f"  RECONCILIATION FAILED: kept {len(kept)} + dropped {len(dropped)} "
              f"!= input {total_in} + appended {n_appended}")
        return 1

    dated = [r for r in kept if (r.get("auction_date") or "").strip()]
    failed_fetch = [r for r in kept if (r.get("full_text") or "").startswith("[fetch failed")]
    print(f"\n  kept {len(kept)} -> {args.out}")
    for county, n in sorted(Counter(r["county"] for r in kept).items()):
        print(f"    {n:3d}  {county}")
    print(f"  with auction_date: {len(dated)} / {len(kept)}"
          f"  (fetch failures: {len(failed_fetch)})")
    print(f"  dropped {len(dropped)} -> {dropped_path.name}")
    for reason, n in Counter(r["drop_reason"] for r in dropped).most_common():
        print(f"    {n:3d}  {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
