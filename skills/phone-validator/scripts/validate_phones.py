#!/usr/bin/env python3
"""
Phone Validator & Tagger
========================
Validates phone numbers via Trestle's phone_intel API, assigns tier-based
phone tags, and produces DataSift/REISift-ready CSVs for upload.

Two REISift/DataSift export layouts are auto-detected:
  - the flat "Phone Enrichment" export -- Phone 1 through Phone 30, each with
    associated Phone Type N, Phone Status N, Phone Tags N, Phone Is Connected N
    columns.
  - a per-contact "ready for dialing" export, e.g. "PR First Name"/"PR Last Name"
    + "PH: Phone1".."PH: Phone5", plus repeating "REL1: Full Name" + "REL1: Phone
    1".."REL1: Phone 3" contact blocks (REL2..REL5, ...). Every phone in every
    contact block gets validated -- none of these columns match a bare "Phone N"
    pattern, so a detector that only looked for that would silently skip every
    relative's number.

This script is CSV-only (stdlib + requests, no extra dependencies). If your
source is an .xlsx "ready for dialing" workbook, save it as CSV first.

Usage:
    # Step 1: Estimate cost (always do this first)
    python3 validate_phones.py --input phones.csv --estimate

    # Step 2: Run validation after user confirms
    python3 validate_phones.py --input phones.csv --output ./results/ --api-key YOUR_KEY
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' library required. Install with: pip install --break-system-packages requests")
    sys.exit(1)


# ─── Default Tier Configuration ──────────────────────────────────────────────

DEFAULT_TIERS = {
    "Dial First":  (81, 100),
    "Dial Second": (61, 80),
    "Dial Third":  (41, 60),
    "Dial Fourth": (21, 40),
    "Drop":        (0, 20),
}

# Cost per API call (Trestle phone_intel pricing)
COST_PER_PHONE = 0.015
LITIGATOR_ADDON_COST = 0.005  # +$0.005/phone when --add-litigator is passed

# Line types that are never personal numbers -- removed regardless of activity
# score. Deliberately does NOT include NonFixedVOIP or Landline -- 24% of
# numbers Sift labels "Landline" are actually FixedVOIP/NonFixedVOIP and
# textable, so those are still scored by activity like any other number.
SKIP_LINE_TYPES = {"tollfree", "premium", "voicemail"}

# Trestle API config
TRESTLE_ENDPOINT = "https://api.trestleiq.com/3.0/phone_intel"
MAX_RETRIES = 3
RETRY_BACKOFF = 1.5  # seconds, multiplied each retry


# ─── Phone Number Cleaning ───────────────────────────────────────────────────

def clean_phone(raw: str) -> str:
    """Strip a phone string down to digits, normalize to 10-digit US format."""
    if not raw:
        return ""
    digits = re.sub(r"[^\d]", "", str(raw).strip())
    # Handle 11-digit with leading 1
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    # Handle E.164 with +1
    if len(digits) > 10 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


# ─── Trestle API Caller ─────────────────────────────────────────────────────

def call_trestle(phone: str, api_key: str, add_litigator: bool = False) -> dict:
    """
    Call Trestle phone_intel API for a single phone number.
    Returns the parsed JSON response or an error dict.
    """
    params = {"phone": phone}
    if add_litigator:
        params["add_ons"] = "litigator_checks"

    headers = {
        "x-api-key": api_key,
        "Accept": "application/json",
    }

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                TRESTLE_ENDPOINT,
                params=params,
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                # Rate limited — wait and retry
                wait = RETRY_BACKOFF * (2 ** attempt)
                print(f"  Rate limited on {phone}, waiting {wait:.1f}s...")
                time.sleep(wait)
                continue
            elif resp.status_code == 403:
                return {"error": "Invalid API key", "phone_number": phone}
            else:
                return {
                    "error": f"HTTP {resp.status_code}",
                    "phone_number": phone,
                    "detail": resp.text[:200],
                }
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
                continue
            return {"error": "Timeout after retries", "phone_number": phone}
        except requests.exceptions.RequestException as e:
            return {"error": str(e), "phone_number": phone}

    return {"error": "Max retries exceeded", "phone_number": phone}


# ─── Tier Assignment ─────────────────────────────────────────────────────────

def assign_tier(score: int, tiers: dict) -> str:
    """Given an activity score, return the matching tier tag name."""
    if score is None:
        return "Unknown"
    for tag_name, (low, high) in tiers.items():
        if low <= score <= high:
            return tag_name
    return "Unknown"


def build_tag(tier: str) -> str:
    """Build the final phone tag string — always just the tier name."""
    return tier


# ─── Qualification Engine ────────────────────────────────────────────────────
#
# Applied to every Trestle response before assigning a tag: litigator override,
# then invalid, then non-personal line types, then activity-score tier.

def evaluate_phone(data: dict, tiers: dict, litigator_checked: bool = False) -> dict:
    """Qualify one Trestle phone_intel response.

    Returns {"tag": str, "code": str, "keep": bool}. "keep" is False for
    litigator-risk, invalid, and skip-line-type numbers, and for whichever tier
    is named "Drop" in `tiers` -- those are still tagged (never silently
    dropped), just excluded from the dial-tier upload CSV.
    """
    if litigator_checked:
        litigator_checks = (data.get("add_ons") or {}).get("litigator_checks") or {}
        if litigator_checks.get("phone.is_litigator_risk") is True:
            return {"tag": "Litigator Risk", "code": "LITIGATOR", "keep": False}

    if data.get("is_valid") is not True:
        return {"tag": "Invalid", "code": "INVALID", "keep": False}

    line_type = (data.get("line_type") or "").strip()
    if line_type.lower() in SKIP_LINE_TYPES:
        return {"tag": f"Skip - {line_type}", "code": "SKIP_LINE_TYPE", "keep": False}

    tier = assign_tier(data.get("activity_score"), tiers)
    is_drop = tier == "Drop"
    return {"tag": tier, "code": "LOW_ACTIVITY" if is_drop else "KEEP", "keep": not is_drop}


# ─── Export Format Detection ─────────────────────────────────────────────────
#
# Two real layouts are recognized:
#   Format A ("flat"): DataSift's wide "Phone Enrichment" export -- Phone 1..30,
#     optionally paired with existing Phone Tags 1..30 columns. One generic
#     contact per row.
#   Format B ("contact_blocks"): a per-contact "ready for dialing" export, e.g.
#     "PR First Name"/"PR Last Name" + "PH: Phone1".."PH: Phone5", plus
#     repeating "REL1: Full Name" + "REL1: Phone 1".."REL1: Phone 3" blocks
#     (REL2..REL5, ...). No pre-existing tag columns -- reinsertion creates one
#     per phone column.
#   Format C ("single"): one generic "Phone"/"Phone Number" column.
# If none of these match, raise loudly rather than silently processing zero
# phones -- a run that "succeeds" with no data is worse than one that fails.

_PHONE_N_RE = re.compile(r"^phone[\s_]?(\d+)$", re.IGNORECASE)
_METADATA_RE = re.compile(r"^phone\s*(type|status|tags?|is\s*connected)\s*\d*$", re.IGNORECASE)
_BLOCK_PHONE_RE = re.compile(r"^([A-Za-z0-9]+)\s*:\s*phone\s*(\d+)$", re.IGNORECASE)

_GENERIC_PHONE_NAMES = (
    "phone", "phone_number", "phone number", "phonenumber",
    "mobile", "cell", "landline", "home phone", "work phone",
    "contact phone", "primary phone",
)


@dataclass
class ContactBlock:
    """One named contact's phone slots within a row (e.g. the PR, or Relative 3)."""
    prefix: str
    phone_columns: list = field(default_factory=list)
    # phone_column -> existing tag column header, or None if one needs to be created
    tag_columns: dict = field(default_factory=dict)
    name_columns: list = field(default_factory=list)


@dataclass
class ExportFormat:
    kind: str  # "flat" | "contact_blocks" | "single"
    contacts: list = field(default_factory=list)  # list[ContactBlock]


@dataclass
class PhoneEntry:
    """One phone number found in one contact block of one row."""
    row_index: int
    raw: str
    cleaned: str
    phone_column: str
    contact_prefix: str
    contact_name: str
    tag_column: object  # existing header (flat) -- None means "synthesize one"


def detect_phone_columns(headers: list) -> list:
    """
    Find all columns that contain phone numbers.

    Handles the DataSift wide export format (Phone 1 through Phone 30) as well
    as simpler formats with a single Phone or Phone Number column.

    Excludes metadata columns like 'Phone Type N', 'Phone Status N',
    'Phone Tags N', and 'Phone Is Connected N'.
    """
    found = []
    for header in headers:
        lower = header.strip().lower()
        if _METADATA_RE.match(lower):
            continue
        if _PHONE_N_RE.match(lower):
            found.append(header)
    return found


def detect_export_format(headers: list, phone_column: str = None) -> ExportFormat:
    """Auto-detect which of the real export layouts this header row is.

    Raises ValueError (naming the headers seen) if none match -- callers should
    treat that as fatal rather than proceeding with zero phones found.
    """
    header_by_lower = {h.strip().lower(): h for h in headers}

    if phone_column:
        return ExportFormat(
            kind="single",
            contacts=[ContactBlock(prefix="Property", phone_columns=[phone_column],
                                    tag_columns={phone_column: None})],
        )

    # ---- Format A: flat "Phone N" (+ optional "Phone Tags N") ----
    flat_cols = detect_phone_columns(headers)
    if flat_cols:
        def _slot(h):
            m = _PHONE_N_RE.match(h.strip().lower())
            return int(m.group(1)) if m else 0

        flat_cols = sorted(flat_cols, key=_slot)
        tag_cols = {}
        for h in flat_cols:
            slot = _slot(h)
            tag_cols[h] = (
                header_by_lower.get(f"phone tags {slot}")
                or header_by_lower.get(f"phone tag {slot}")
            )
        block = ContactBlock(prefix="Property", phone_columns=flat_cols, tag_columns=tag_cols)
        return ExportFormat(kind="flat", contacts=[block])

    # ---- Format B: "<Prefix>: Phone N" contact blocks ----
    blocks_by_prefix = {}
    for h in headers:
        m = _BLOCK_PHONE_RE.match(h.strip())
        if m:
            prefix, slot = m.group(1).upper(), int(m.group(2))
            blocks_by_prefix.setdefault(prefix, []).append((slot, h))

    if blocks_by_prefix:
        contacts = []
        for prefix, slots in blocks_by_prefix.items():
            slots.sort(key=lambda t: t[0])
            phone_cols = [h for _, h in slots]

            name_cols = []
            full_name_header = header_by_lower.get(f"{prefix.lower()}: full name")
            if full_name_header:
                name_cols.append(full_name_header)
            elif prefix == "PH":
                # PR's own phone block -- name lives in separate PR First/Last
                # Name columns, not a "PH: Full Name" column.
                for candidate in ("pr first name", "pr last name"):
                    if candidate in header_by_lower:
                        name_cols.append(header_by_lower[candidate])

            contacts.append(ContactBlock(
                prefix=prefix, phone_columns=phone_cols, tag_columns={}, name_columns=name_cols,
            ))

        def _contact_sort_key(c):
            if c.prefix == "PH":
                return (0, 0)
            m = re.match(r"^REL(\d+)$", c.prefix)
            if m:
                return (1, int(m.group(1)))
            return (2, c.prefix)

        contacts.sort(key=_contact_sort_key)
        return ExportFormat(kind="contact_blocks", contacts=contacts)

    # ---- Format C: single generic phone column ----
    for h in headers:
        if h.strip().lower() in _GENERIC_PHONE_NAMES:
            block = ContactBlock(prefix="Property", phone_columns=[h], tag_columns={h: None})
            return ExportFormat(kind="single", contacts=[block])

    preview = headers[:25]
    raise ValueError(
        "Could not identify a phone column structure in this file. Expected one of: "
        "'Phone 1'..'Phone 30' (DataSift export), '<Label>: Phone N' contact blocks "
        "(e.g. 'PH: Phone1', 'REL1: Phone 1'), or a single 'Phone'/'Phone Number' column. "
        f"Headers found: {preview}{' ...' if len(headers) > len(preview) else ''}"
    )


def extract_phone_entries(rows: list, export_format: ExportFormat) -> list:
    """Pull every phone out of every contact block of every row, tied back to
    exactly which row/column/contact it came from (for reinsertion later).
    """
    entries = []
    for row_index, row in enumerate(rows):
        for contact in export_format.contacts:
            name_parts = [v for col in contact.name_columns if (v := (row.get(col) or "").strip())]
            contact_name = " ".join(name_parts).strip() or contact.prefix

            for phone_col in contact.phone_columns:
                raw = (row.get(phone_col) or "").strip()
                if not raw:
                    continue
                cleaned = clean_phone(raw)
                if not cleaned:
                    continue
                entries.append(PhoneEntry(
                    row_index=row_index,
                    raw=raw,
                    cleaned=cleaned,
                    phone_column=phone_col,
                    contact_prefix=contact.prefix,
                    contact_name=contact_name,
                    tag_column=(contact.tag_columns or {}).get(phone_col),
                ))
    return entries


# ─── CSV Detection & Reading ────────────────────────────────────────────────

def load_csv_rows(filepath: str) -> tuple:
    """Load a CSV export into (headers, rows) -- rows are dicts keyed by the
    exact source header text.
    """
    with open(filepath, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = [dict(r) for r in reader]
    return headers, rows


def read_phones_from_csv(filepath: str, phone_column: str = None) -> tuple:
    """
    Read phone numbers from a CSV file.

    Returns:
        tuple: (phones_list, unique_count, total_entries)
            - phones_list: list of (raw_phone, cleaned_phone) tuples
            - unique_count: number of unique cleaned phone numbers
            - total_entries: total phone entries found (before dedup)
    """
    headers, rows = load_csv_rows(filepath)
    try:
        export_format = detect_export_format(headers, phone_column=phone_column)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    entries = extract_phone_entries(rows, export_format)

    if not os.environ.get("PHONE_VALIDATOR_QUIET"):
        print(f"Detected export format '{export_format.kind}' "
              f"({len(export_format.contacts)} contact block(s): "
              f"{', '.join(c.prefix for c in export_format.contacts)})")

    phones = [(e.raw, e.cleaned) for e in entries]
    unique = {e.cleaned for e in entries}
    return phones, len(unique), len(entries)


# ─── Cost Estimation ────────────────────────────────────────────────────────

def estimate_cost(filepath: str, phone_column: str = None, add_litigator: bool = False) -> dict:
    """
    Parse the CSV to count unique phones and estimate Trestle API cost.

    Returns a dict with stats that can be printed or returned as JSON.
    """
    phones, unique_count, total_entries = read_phones_from_csv(filepath, phone_column)

    cost_per_phone = COST_PER_PHONE + (LITIGATOR_ADDON_COST if add_litigator else 0)
    cost = unique_count * cost_per_phone

    result = {
        "input_file": os.path.basename(filepath),
        "total_entries": total_entries,
        "unique_phones": unique_count,
        "duplicates_saved": total_entries - unique_count,
        "cost_per_phone": cost_per_phone,
        "add_litigator": add_litigator,
        "estimated_cost": round(cost, 2),
    }

    return result


def print_estimate(est: dict):
    """Print a formatted cost estimate."""
    print()
    print("=" * 50)
    print("  PHONE VALIDATION COST ESTIMATE")
    print("=" * 50)
    print(f"  Input file:          {est['input_file']}")
    print(f"  Total phone entries: {est['total_entries']:,}")
    print(f"  Unique phones:       {est['unique_phones']:,}")
    print(f"  Duplicates saved:    {est['duplicates_saved']:,}")
    if est.get("add_litigator"):
        print(f"  Cost per phone:      ${est['cost_per_phone']:.3f} (base $0.015 + litigator add-on $0.005)")
    else:
        print(f"  Cost per phone:      ${est['cost_per_phone']:.3f}")
    print("  -----------------------------------------")
    print(f"  ESTIMATED COST:      ${est['estimated_cost']:.2f}")
    print("=" * 50)
    print()


# --- Main Processing --------------------------------------------------------

def process_phones(
    phones: list,
    api_key: str,
    tiers: dict,
    add_litigator: bool = False,
    batch_size: int = 10,
    delay: float = 0.1,
    dry_run: bool = False,
) -> tuple:
    """
    Process all phone numbers through Trestle API.
    Returns (results_list, errors_list).
    """
    # Deduplicate
    unique_phones = list(dict.fromkeys(p[1] for p in phones))
    total = len(unique_phones)
    print(f"\nProcessing {total} unique phone numbers...")

    if dry_run:
        print("DRY RUN - generating template without API calls")
        results = []
        for phone in unique_phones:
            results.append({
                "phone_number": phone,
                "activity_score": None,
                "line_type": None,
                "carrier": None,
                "is_valid": None,
                "is_prepaid": None,
                "assigned_tag": "Unscored",
                "is_litigator_risk": None,
                "keep": True,
                "qualification_code": "DRY_RUN",
            })
        return results, []

    results = []
    errors = []
    processed = 0

    # Process in batches
    for batch_start in range(0, total, batch_size):
        batch = unique_phones[batch_start : batch_start + batch_size]

        with ThreadPoolExecutor(max_workers=min(batch_size, len(batch))) as executor:
            future_to_phone = {
                executor.submit(call_trestle, phone, api_key, add_litigator): phone
                for phone in batch
            }

            for future in as_completed(future_to_phone):
                phone = future_to_phone[future]
                processed += 1

                try:
                    data = future.result()
                except Exception as e:
                    errors.append({"phone_number": phone, "error": str(e)})
                    continue

                if "error" in data and not data.get("is_valid"):
                    # Check if this is a real error vs just a warning
                    if data.get("error") == "Invalid API key":
                        print(f"\nERROR: Invalid Trestle API key. Please check your key.")
                        sys.exit(1)
                    errors.append(data)
                    continue

                score = data.get("activity_score")
                line_type = data.get("line_type")
                decision = evaluate_phone(data, tiers, litigator_checked=add_litigator)

                litigator_risk = None
                if add_litigator and data.get("add_ons", {}).get("litigator_checks"):
                    litigator_risk = data["add_ons"]["litigator_checks"].get(
                        "phone.is_litigator_risk", None
                    )

                results.append({
                    "phone_number": phone,
                    "activity_score": score,
                    "line_type": line_type,
                    "carrier": data.get("carrier"),
                    "is_valid": data.get("is_valid"),
                    "is_prepaid": data.get("is_prepaid"),
                    "assigned_tag": decision["tag"],
                    "is_litigator_risk": litigator_risk,
                    "keep": decision["keep"],
                    "qualification_code": decision["code"],
                })

                # Progress indicator
                if processed % 25 == 0 or processed == total:
                    pct = (processed / total) * 100
                    print(f"  Progress: {processed}/{total} ({pct:.0f}%)")

        # Delay between batches
        if batch_start + batch_size < total and delay > 0:
            time.sleep(delay)

    return results, errors


# ─── Output Writers ──────────────────────────────────────────────────────────

def write_datasift_csv(results: list, output_dir: str) -> str:
    """Write the DataSift-ready phone tags CSV (Phone Number + Phone Tag).

    Only phones the qualification engine marked "keep" are included --
    litigator-risk, invalid, skip-line-type, and Drop-tier numbers are
    excluded (they're still visible, tagged, in validation_results.csv and in
    the reinserted export).
    """
    filepath = os.path.join(output_dir, "phone_tags_for_datasift.csv")
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Phone Number", "Phone Tag"])
        for r in results:
            if r.get("keep"):
                writer.writerow([r["phone_number"], r["assigned_tag"]])
    return filepath


def write_detailed_csv(results: list, output_dir: str) -> str:
    """Write the detailed validation results CSV."""
    filepath = os.path.join(output_dir, "validation_results.csv")
    fieldnames = [
        "phone_number", "activity_score", "line_type", "carrier",
        "is_valid", "is_prepaid", "assigned_tag", "is_litigator_risk",
        "keep", "qualification_code",
    ]
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in fieldnames})
    return filepath


def write_errors_csv(errors: list, output_dir: str) -> str:
    """Write errors to CSV for review."""
    if not errors:
        return ""
    filepath = os.path.join(output_dir, "errors.csv")
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["phone_number", "error", "detail"])
        for e in errors:
            writer.writerow([
                e.get("phone_number", ""),
                e.get("error", ""),
                e.get("detail", ""),
            ])
    return filepath


def write_summary(results: list, errors: list, tiers: dict, output_dir: str) -> str:
    """Write a human-readable summary of the validation run."""
    filepath = os.path.join(output_dir, "summary.txt")

    # Compute stats
    total = len(results)
    scores = [r["activity_score"] for r in results if r["activity_score"] is not None]
    tag_counts = Counter(r["assigned_tag"] for r in results)
    line_type_counts = Counter(r["line_type"] for r in results if r["line_type"])
    avg_score = sum(scores) / len(scores) if scores else 0

    # Score distribution buckets
    buckets = defaultdict(int)
    for s in scores:
        bucket = (s // 10) * 10
        buckets[bucket] += 1

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write("PHONE VALIDATION SUMMARY\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 60 + "\n\n")

        f.write(f"Total phones processed: {total}\n")
        f.write(f"Errors/failures:        {len(errors)}\n")
        f.write(f"Average activity score: {avg_score:.1f}\n\n")

        f.write("─── TIER BREAKDOWN ───\n\n")
        for tag_name in tiers.keys():
            count = tag_counts.get(tag_name, 0)
            pct = (count / total * 100) if total else 0
            f.write(f"  {tag_name:20s}  {count:5d}  ({pct:5.1f}%)\n")
        # Include Unknown/Invalid/Unscored if present
        for tag_name in sorted(tag_counts.keys()):
            if tag_name not in tiers:
                count = tag_counts[tag_name]
                pct = (count / total * 100) if total else 0
                f.write(f"  {tag_name:20s}  {count:5d}  ({pct:5.1f}%)\n")

        f.write(f"\n─── LINE TYPE BREAKDOWN ───\n\n")
        for lt, count in line_type_counts.most_common():
            pct = (count / total * 100) if total else 0
            f.write(f"  {lt:20s}  {count:5d}  ({pct:5.1f}%)\n")

        f.write(f"\n─── SCORE DISTRIBUTION ───\n\n")
        for bucket in sorted(buckets.keys()):
            count = buckets[bucket]
            bar = "█" * (count // max(1, total // 40))
            f.write(f"  {bucket:3d}-{min(bucket+9, 100):3d}  {count:5d}  {bar}\n")

        f.write(f"\n─── DATASIFT UPLOAD INSTRUCTIONS ───\n\n")
        f.write("1. Open your DataSift/REISift account\n")
        f.write("2. Go to Upload → Update Data\n")
        f.write("3. Select 'Tag phones by phone number'\n")
        f.write("4. Upload phone_tags_for_datasift.csv\n")
        f.write("5. Map 'Phone Number' → Phone Number\n")
        f.write("6. Map 'Phone Tag' → Phone Tag\n")
        f.write("7. Complete the upload\n\n")
        f.write("Tags will apply to ALL records sharing each phone number.\n")
        f.write("When sending to a dialer, send ONE tier at a time.\n\n")
        f.write("For per-slot tags instead (tag stays tied to the exact phone/contact\n")
        f.write("it came from, e.g. a specific relative), re-import\n")
        f.write("reisift_reimport_with_phone_tags.csv via Add Data → Update existing\n")
        f.write("records instead.\n")

    return filepath


def write_reinserted_csv(
    headers: list,
    rows: list,
    export_format: ExportFormat,
    phone_entries: list,
    results_by_phone: dict,
    output_path: str,
) -> str:
    """Write a copy of the source CSV with each phone's qualification tag
    written back next to the phone it came from. Never deletes or blanks a
    phone cell -- rejected numbers (Invalid / Litigator Risk / Skip - X /
    Drop) are tagged in place too, same as kept ones.

    Flat exports (Format A) merge into the existing "Phone Tags N" cell,
    comma-appended to whatever's already there. Contact-block exports
    (Format B) get a new "<phone column> Tag" column inserted right after
    each phone column that doesn't already have one.
    """
    out_headers = list(headers)
    tag_col_for_phone_col = {}

    for contact in export_format.contacts:
        for phone_col in contact.phone_columns:
            existing = (contact.tag_columns or {}).get(phone_col)
            if existing:
                tag_col_for_phone_col[phone_col] = existing
                continue
            new_col = f"{phone_col} Tag"
            tag_col_for_phone_col[phone_col] = new_col
            if new_col not in out_headers:
                insert_at = out_headers.index(phone_col) + 1
                out_headers.insert(insert_at, new_col)

    out_rows = [dict(r) for r in rows]

    for entry in phone_entries:
        result = results_by_phone.get(entry.cleaned)
        if not result:
            continue
        tag_col = tag_col_for_phone_col.get(entry.phone_column)
        if not tag_col:
            continue

        row = out_rows[entry.row_index]
        new_tag = result["assigned_tag"]
        existing_value = (row.get(tag_col) or "").strip()

        if existing_value:
            existing_tags = [t.strip() for t in existing_value.split(",") if t.strip()]
            if new_tag not in existing_tags:
                existing_tags.append(new_tag)
            row[tag_col] = ", ".join(existing_tags)
        else:
            row[tag_col] = new_tag

    for row in out_rows:
        for h in out_headers:
            row.setdefault(h, "")

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_headers)
        writer.writeheader()
        for row in out_rows:
            writer.writerow({h: row.get(h, "") for h in out_headers})

    return output_path


# ─── CLI ─────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate phones via Trestle API and generate DataSift phone tags"
    )
    parser.add_argument("--input", "-i", required=True, help="Input CSV file path")
    parser.add_argument("--output", "-o", default=None,
                        help="Output directory path (required unless --estimate)")
    parser.add_argument("--api-key", "-k", default=os.environ.get("TRESTLE_API_KEY", ""),
                        help="Trestle API key (or set TRESTLE_API_KEY env var)")
    parser.add_argument("--estimate", action="store_true",
                        help="Estimate cost only — parse CSV, count unique phones, "
                             "print cost at $0.015/phone, then exit. No API calls.")
    parser.add_argument("--estimate-json", action="store_true",
                        help="Like --estimate but output as JSON (for programmatic use)")
    parser.add_argument("--tiers", choices=["default", "custom"], default="default",
                        help="Tier strategy: default (5 tiers) or custom")
    parser.add_argument("--custom-tiers", type=str, default=None,
                        help='JSON string for custom tiers, e.g. \'{"Hot": [80,100], "Cold": [0,79]}\'')
    parser.add_argument("--batch-size", type=int, default=10,
                        help="Concurrent API requests per batch")
    parser.add_argument("--delay", type=float, default=0.1,
                        help="Seconds to wait between batches")
    parser.add_argument("--phone-column", type=str, default=None,
                        help="Override phone column name (skips export-format auto-detection)")
    parser.add_argument("--add-litigator", action="store_true",
                        help="Include litigator risk check")
    parser.add_argument("--full-report", action="store_true",
                        help="Generate XLSX report (requires openpyxl)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate CSV template without API calls")
    return parser.parse_args()


def main():
    args = parse_args()

    # Validate input file exists
    if not os.path.isfile(args.input):
        print(f"ERROR: Input file not found: {args.input}")
        sys.exit(1)

    # --- Estimate mode -------------------------------------------------
    if args.estimate or args.estimate_json:
        if args.estimate_json:
            os.environ["PHONE_VALIDATOR_QUIET"] = "1"
        est = estimate_cost(args.input, args.phone_column, args.add_litigator)
        if args.estimate_json:
            print(json.dumps(est, indent=2))
        else:
            print_estimate(est)
        sys.exit(0)

    # ─── Full validation mode ────────────────────────────────────────

    # Validate required args for full run
    if not args.output:
        print("ERROR: --output is required for validation (or use --estimate for cost only)")
        sys.exit(1)

    if not args.api_key and not args.dry_run:
        print("ERROR: No Trestle API key provided.")
        print("Either pass --api-key YOUR_KEY or set TRESTLE_API_KEY env var.")
        print("Sign up at https://trestleiq.com for 25 free trial queries.")
        sys.exit(1)

    # Select tier strategy
    if args.tiers == "custom":
        if not args.custom_tiers:
            print("ERROR: --custom-tiers required when using --tiers custom")
            sys.exit(1)
        try:
            raw = json.loads(args.custom_tiers)
            tiers = {k: tuple(v) for k, v in raw.items()}
        except (json.JSONDecodeError, ValueError) as e:
            print(f"ERROR: Invalid --custom-tiers JSON: {e}")
            sys.exit(1)
    else:
        tiers = DEFAULT_TIERS

    # Create output directory
    os.makedirs(args.output, exist_ok=True)

    # Read phones
    print(f"Reading phones from: {args.input}")
    headers, rows = load_csv_rows(args.input)
    try:
        export_format = detect_export_format(headers, args.phone_column)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    phone_entries = extract_phone_entries(rows, export_format)
    print(f"Detected export format '{export_format.kind}' "
          f"({len(export_format.contacts)} contact block(s): "
          f"{', '.join(c.prefix for c in export_format.contacts)})")

    phones = [(e.raw, e.cleaned) for e in phone_entries]
    if not phones:
        print("ERROR: No valid phone numbers found in the input file.")
        sys.exit(1)
    unique_count = len({e.cleaned for e in phone_entries})
    total_entries = len(phone_entries)
    print(f"Found {total_entries} phone entries ({unique_count} unique)")
    _cost_per_phone = COST_PER_PHONE + (LITIGATOR_ADDON_COST if args.add_litigator else 0)
    print(f"Estimated cost: ${unique_count * _cost_per_phone:.2f} ({unique_count} x ${_cost_per_phone:.3f})")

    # Process
    results, errors = process_phones(
        phones=phones,
        api_key=args.api_key,
        tiers=tiers,
        add_litigator=args.add_litigator,
        batch_size=args.batch_size,
        delay=args.delay,
        dry_run=args.dry_run,
    )
    results_by_phone = {r["phone_number"]: r for r in results}

    # Write outputs
    print(f"\nWriting outputs to: {args.output}")
    tag_file = write_datasift_csv(results, args.output)
    print(f"  [OK] DataSift phone tags: {tag_file}")

    detail_file = write_detailed_csv(results, args.output)
    print(f"  [OK] Detailed results:    {detail_file}")

    if errors:
        err_file = write_errors_csv(errors, args.output)
        print(f"  [OK] Errors log:          {err_file}")

    summary_file = write_summary(results, errors, tiers, args.output)
    print(f"  [OK] Summary:             {summary_file}")

    reinserted_file = write_reinserted_csv(
        headers, rows, export_format, phone_entries, results_by_phone,
        os.path.join(args.output, "reisift_reimport_with_phone_tags.csv"),
    )
    print(f"  [OK] Reinserted export:   {reinserted_file}")

    # Optional XLSX report
    if args.full_report:
        try:
            from generate_report import create_xlsx_report
            report_file = create_xlsx_report(results, errors, tiers, args.output)
            print(f"  [OK] XLSX report:         {report_file}")
        except ImportError:
            print("  [WARN] XLSX report skipped (openpyxl not installed)")

    # Print quick summary
    tag_counts = Counter(r["assigned_tag"] for r in results)
    print(f"\n{'-' * 40}")
    print(f"RESULTS: {len(results)} scored, {len(errors)} errors")
    print(f"{'-' * 40}")
    for tag_name in tiers.keys():
        count = tag_counts.get(tag_name, 0)
        print(f"  {tag_name:20s}  {count:5d}")
    for tag_name in sorted(tag_counts.keys()):
        if tag_name not in tiers:
            print(f"  {tag_name:20s}  {tag_counts[tag_name]:5d}")
    print(f"{'-' * 40}")
    print(f"\nUpload '{os.path.basename(tag_file)}' to DataSift -> Update Data -> Tag phones by phone number")
    print(f"...or re-import '{os.path.basename(reinserted_file)}' via Add Data -> Update existing records")
    print("Done!")


if __name__ == "__main__":
    main()
