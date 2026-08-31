"""Build the top-N Doors-Per-Deal opportunity pool for the MD/DC/VA REIsift account.

Reads the 3 "county-compare-*-doors-per-deal-community.xlsx" market-research
workbooks (Combined Ranking sheet, one row per county+signal combination ranked
by doors/deal efficiency) plus the local `output/live_account_pull.json` account
snapshot (26,643 hydrated records, including `lists` and `owner.phones`), and
greedily selects up to --target unique, phone-having, non-dead records ranked by
the workbooks' own Priority (ascending) then Lift (descending) -- DataSift's own
recommended read order for "which signal to pull first".

This only reads local files -- no live REIsift call, no cost, no write. It
produces the CSV that src/phone_validator.py --estimate consumes next.

Usage:
    python src/scripts/doors_per_deal_opportunities.py
    python src/scripts/doors_per_deal_opportunities.py --target 1250 \
        --cache output/live_account_pull.json --out output/doors_per_deal_opportunities_1250.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Callable

import openpyxl

ROOT = Path(__file__).resolve().parents[2]
GALAL_DEV = ROOT.parent  # "Galal Development" folder holding the research xlsx files (outside this repo)

WORKBOOKS = [
    GALAL_DEV / "county-compare-24005-24031-24003-24021-24013-24009-doors-per-deal-community.xlsx",
    GALAL_DEV / "county-compare-24017-11001-doors-per-deal-community.xlsx",
    GALAL_DEV / "county-compare-51153-51059-51013-51179-51177-51630-doors-per-deal-community.xlsx",
]

# Signal token -> real DataSift `lists` value(s) present on this account
# (verified live 2026-08-23 against output/live_account_pull.json's distinct
# `lists` vocabulary). A token is satisfied if the record carries ANY name in
# its tuple -- some signals are tracked under more than one list name here
# (e.g. two separate "Notice of Foreclosure" sources).
TOKEN_TO_LISTS: dict[str, tuple[str, ...]] = {
    "Absentee": ("Absentee Owners",),
    "Bad Credit": ("Low Credit Score",),
    "Bankruptcy": ("Bankruptcy",),
    "Estate Sale": ("Estate Sales",),
    "Free & Clear": ("Free & Clear",),
    "High Equity": ("High Equity",),
    "Lis Pendens": ("Lis Pendens", "Pre-Foreclosures - Lis Pendens"),
    "Low Income": ("Low Income",),
    "Notice of Default": ("Pre-Foreclosures - Notice of Default",),
    "Notice of Foreclosure": ("Foreclosure Notices", "Pre-Foreclosures - Notice of Foreclosure"),
    # Approximate: this account has no lien-type breakdown beyond one generic
    # "Liens" bucket, so "Other Lien" maps to it rather than being left unmatched.
    "Other Lien": ("Liens",),
    "Pre-Probate": ("Pre-Probate/Deceased",),
    "Probate": ("Probate",),
    "Senior": ("Senior Homeowners",),
    "Tax Delinquent": ("Tax Delinquent",),
    "Tired Landlord": ("Tired Landlord",),
    "Vacant": ("Vacant",),
    # Verified live: exactly 1 record in the account carries this list at all.
    "Judgment Lien": ("Judgments",),
}

# Tokens with NO corresponding list/tag anywhere in this account's live data
# (verified live 2026-08-23 against the full distinct `lists` counter over all
# 26,643 records) -- rows using these are reported as zero-supply rather than
# silently dropped or guessed at with a substitute.
ZERO_SUPPLY_TOKENS = {"HOA Lien", "Zombie"}


def _is_out_of_state(rec: dict) -> bool:
    owner = rec.get("owner") or {}
    owner_state = ((owner.get("address") or {}).get("state") or "").strip().upper()
    prop_state = ((rec.get("address") or {}).get("state") or "").strip().upper()
    return bool(owner_state) and bool(prop_state) and owner_state != prop_state


# Computed (not list-based) signals -- "Out-of-State" has no list tag in this
# account at all, so it's derived from owner mailing state vs property state.
COMPUTED_TOKENS: dict[str, Callable[[dict], bool]] = {"Out-of-State": _is_out_of_state}

DEAD_STATUSES = {
    "already sold", "sold", "closed", "under_contract", "listed",
    "dnc", "dead lead", "not_interested",
}

MAX_PHONE_COLUMNS = 30  # matches DataSift's own "Phone Enrichment" export convention

# The 14 jurisdictions this pull targets (raw County column strings, for the
# summary's coverage check) -- a pure priority/lift-ranked greedy walk can let
# a few high-volume/high-lift counties crowd out a thin one entirely, which is
# expected under this selection method but must be surfaced, not buried.
TARGET_COUNTIES_RAW = [
    "Baltimore, MD", "Montgomery, MD", "Anne Arundel, MD", "Frederick, MD",
    "Carroll, MD", "Calvert, MD", "Charles, MD", "District of Columbia, DC",
    "Prince William, VA", "Fairfax, VA", "Arlington, VA", "Stafford, VA",
    "Spotsylvania, VA", "Fredericksburg City, VA",
]


def normalize_county(raw: str) -> str:
    """"Baltimore, MD" -> "baltimore"; "District of Columbia, DC" -> "district of columbia"."""
    return raw.split(",")[0].strip().lower()


def load_rankings() -> list[dict]:
    rows = []
    for wb_path in WORKBOOKS:
        if not wb_path.exists():
            raise SystemExit(f"Research workbook not found: {wb_path}")
        wb = openpyxl.load_workbook(wb_path, data_only=True)
        ws = wb["Combined Ranking"]
        all_rows = list(ws.iter_rows(values_only=True))
        for r in all_rows[1:]:
            if r[0] is None or r[1] is None or r[2] is None:
                continue
            lift = r[6] if isinstance(r[6], (int, float)) else 0
            rows.append({
                "priority": r[0],
                "signal": r[1],
                "county": normalize_county(r[2]),
                "county_raw": r[2],
                "type": r[3],
                "evidence": r[4],
                "doors_per_deal": r[5],
                "lift": lift,
                "deals": r[7],
                "list_size": r[8],
                "typical_gross": r[9],
                "caveats": r[10],
                "workbook": wb_path.name,
            })
    return rows


def resolve_tokens(signal: str) -> tuple[list[str], list[str], bool]:
    """Return (required_list_tokens, computed_tokens, has_zero_supply_token)."""
    required_tokens: list[str] = []
    computed_tokens: list[str] = []
    zero_supply = False
    for tok in [t.strip() for t in signal.split(" + ")]:
        if tok in ZERO_SUPPLY_TOKENS:
            zero_supply = True
        elif tok in COMPUTED_TOKENS:
            computed_tokens.append(tok)
        elif tok in TOKEN_TO_LISTS:
            required_tokens.append(tok)
        else:
            # Unknown token never seen in the vocabulary sweep -- treat as
            # unmatched rather than guess at a substitute list.
            zero_supply = True
    return required_tokens, computed_tokens, zero_supply


def record_matches(rec: dict, required_tokens: list[str], computed_tokens: list[str]) -> bool:
    lists = set(rec.get("lists") or [])
    for tok in required_tokens:
        if not any(name in lists for name in TOKEN_TO_LISTS[tok]):
            return False
    for tok in computed_tokens:
        if not COMPUTED_TOKENS[tok](rec):
            return False
    return True


def is_dead(rec: dict) -> bool:
    status = (rec.get("status") or "").strip().lower()
    if status in DEAD_STATUSES:
        return True
    owner = rec.get("owner") or {}
    if owner.get("do_not_mail_ever") or owner.get("opt_out"):
        return True
    return False


def has_dialable_phone(rec: dict) -> bool:
    owner = rec.get("owner") or {}
    return bool(owner.get("phones"))


def build_county_index(cache_path: Path) -> tuple[dict, dict]:
    """Returns (records_by_uuid, uuids_by_county); dead/opted-out records are
    dropped here so nothing downstream has to re-check."""
    blob = json.loads(cache_path.read_text(encoding="utf-8"))
    records_by_uuid: dict[str, dict] = {}
    uuids_by_county: dict[str, list[str]] = defaultdict(list)
    for entry in blob.get("records") or []:
        rec = entry.get("record")
        if not rec:
            continue
        if is_dead(rec):
            continue
        uuid = rec.get("uuid") or entry.get("uuid")
        if not uuid:
            continue
        records_by_uuid[uuid] = rec
        county = ((rec.get("address") or {}).get("county") or "").strip().lower()
        uuids_by_county[county].append(uuid)
    return records_by_uuid, uuids_by_county


def select_opportunities(rankings: list[dict], records_by_uuid: dict, uuids_by_county: dict, target: int):
    rankings_sorted = sorted(rankings, key=lambda r: (r["priority"], -r["lift"]))

    selected: list[dict] = []
    selected_uuids: set[str] = set()
    zero_supply_rows: list[dict] = []
    contribution: dict[tuple, int] = defaultdict(int)
    skipped_phoneless = 0

    for row in rankings_sorted:
        if len(selected) >= target:
            break
        required_tokens, computed_tokens, zero_supply = resolve_tokens(row["signal"])
        if zero_supply:
            zero_supply_rows.append(row)
            continue

        candidate_uuids = uuids_by_county.get(row["county"], [])
        added_this_row = 0
        for uuid in candidate_uuids:
            if len(selected) >= target:
                break
            if uuid in selected_uuids:
                continue
            rec = records_by_uuid[uuid]
            if not record_matches(rec, required_tokens, computed_tokens):
                continue
            if not has_dialable_phone(rec):
                skipped_phoneless += 1
                continue
            selected_uuids.add(uuid)
            selected.append({"uuid": uuid, "row": row})
            added_this_row += 1

        if added_this_row:
            key = (row["workbook"], row["county_raw"], row["signal"], row["priority"], row["lift"])
            contribution[key] += added_this_row

    return selected, zero_supply_rows, contribution, skipped_phoneless


def write_output_csv(selected: list[dict], records_by_uuid: dict, out_path: Path) -> int:
    max_phones = 1
    for sel in selected:
        rec = records_by_uuid[sel["uuid"]]
        n = len(((rec.get("owner") or {}).get("phones")) or [])
        max_phones = max(max_phones, min(n, MAX_PHONE_COLUMNS))

    fieldnames = [
        "uuid", "matched_signal", "priority", "lift", "workbook",
        "county", "Property Street", "Property City", "Property State", "Property ZIP",
        "Owner First Name", "Owner Last Name", "Owner Company",
    ]
    for i in range(1, max_phones + 1):
        fieldnames += [f"Phone {i}", f"Phone Type {i}", f"Phone Status {i}", f"Phone Tags {i}"]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for sel in selected:
            rec = records_by_uuid[sel["uuid"]]
            row = sel["row"]
            addr = rec.get("address") or {}
            owner = rec.get("owner") or {}
            out_row = {
                "uuid": sel["uuid"],
                "matched_signal": row["signal"],
                "priority": row["priority"],
                "lift": row["lift"],
                "workbook": row["workbook"],
                "county": row["county_raw"],
                "Property Street": addr.get("street") or "",
                "Property City": addr.get("city") or "",
                "Property State": addr.get("state") or "",
                "Property ZIP": (addr.get("postal_code") or "")[:5],
                "Owner First Name": owner.get("first_name") or "",
                "Owner Last Name": owner.get("last_name") or "",
                "Owner Company": owner.get("company") or "",
            }
            phones = (owner.get("phones") or [])[:max_phones]
            for i, ph in enumerate(phones, start=1):
                out_row[f"Phone {i}"] = ph.get("number") or ""
                out_row[f"Phone Type {i}"] = ph.get("type") or ""
                out_row[f"Phone Status {i}"] = ph.get("status") or ""
                out_row[f"Phone Tags {i}"] = ", ".join(ph.get("tags") or [])
            writer.writerow(out_row)
    return max_phones


def write_summary(
    selected: list[dict],
    zero_supply_rows: list[dict],
    contribution: dict,
    skipped_phoneless: int,
    target: int,
    summary_path: Path,
) -> None:
    lines = []
    lines.append("Doors-Per-Deal Opportunity Selection Summary")
    lines.append("=" * 50)
    lines.append(f"Target: {target}")
    lines.append(f"Selected: {len(selected)}")
    if len(selected) < target:
        lines.append(f"SHORTFALL: {target - len(selected)} short of target -- ranked rows exhausted "
                      "(or all remaining rows are zero-supply). See zero-supply list below.")
    lines.append(f"Candidates skipped for having no phone on file: {skipped_phoneless}")
    lines.append("")

    county_counts: dict[str, int] = defaultdict(int)
    for sel in selected:
        county_counts[sel["row"]["county_raw"]] += 1
    missing_counties = [c for c in TARGET_COUNTIES_RAW if county_counts.get(c, 0) == 0]
    lines.append("County coverage of the 14 target jurisdictions:")
    lines.append("-" * 50)
    for c in TARGET_COUNTIES_RAW:
        lines.append(f"{county_counts.get(c, 0):5d}  {c}")
    if missing_counties:
        lines.append("")
        lines.append(f"ZERO representation ({len(missing_counties)} of 14) -- crowded out by higher-lift "
                      "counties/signals elsewhere under the greedy priority/lift walk (expected under this "
                      "selection method, not a bug -- flagged so it isn't silent):")
        for c in missing_counties:
            lines.append(f"  - {c}")
    lines.append("")

    lines.append("Contribution by (workbook, county, signal), most-productive first:")
    lines.append("-" * 50)
    for (workbook, county, signal, priority, lift), count in sorted(
        contribution.items(), key=lambda kv: -kv[1]
    ):
        lines.append(f"{count:5d}  P{priority}  lift {lift:>6.1f}  {county:28s}  {signal}  [{workbook}]")
    lines.append("")

    if zero_supply_rows:
        lines.append(f"Zero-supply rows (no matching list/tag exists in this account, {len(zero_supply_rows)} rows):")
        lines.append("-" * 50)
        seen = set()
        for row in zero_supply_rows:
            key = (row["county_raw"], row["signal"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  P{row['priority']}  {row['county_raw']:28s}  {row['signal']}  [{row['workbook']}]")
    else:
        lines.append("No zero-supply rows encountered.")

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1250)
    ap.add_argument("--cache", default=str(ROOT / "output" / "live_account_pull.json"))
    ap.add_argument("--out", default=str(ROOT / "output" / "doors_per_deal_opportunities_1250.csv"))
    ap.add_argument("--summary", default=str(ROOT / "output" / "doors_per_deal_opportunities_summary.txt"))
    args = ap.parse_args()

    print("Parsing 3 Doors-Per-Deal workbooks...")
    rankings = load_rankings()
    print(f"  {len(rankings)} county+signal rows across {len(WORKBOOKS)} workbooks")

    print(f"Loading account cache: {args.cache}")
    records_by_uuid, uuids_by_county = build_county_index(Path(args.cache))
    print(f"  {len(records_by_uuid)} live (non-dead) records across {len(uuids_by_county)} counties")

    print(f"Selecting up to {args.target} opportunities (greedy priority/lift walk)...")
    selected, zero_supply_rows, contribution, skipped_phoneless = select_opportunities(
        rankings, records_by_uuid, uuids_by_county, args.target
    )
    print(f"  selected {len(selected)} unique records "
          f"({skipped_phoneless} phoneless candidates skipped along the way)")

    max_phones = write_output_csv(selected, records_by_uuid, Path(args.out))
    write_summary(selected, zero_supply_rows, contribution, skipped_phoneless, args.target, Path(args.summary))

    print(f"Wrote {args.out} ({len(selected)} rows, up to {max_phones} phone slots)")
    print(f"Wrote {args.summary}")
    if len(selected) < args.target:
        print(f"WARNING: shortfall of {args.target - len(selected)} -- see summary for zero-supply rows")


if __name__ == "__main__":
    main()
