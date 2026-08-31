"""Phone validation via Trestle's phone_intel API with DataSift phone tag output.

Validates phone numbers from a REISift/DataSift export, scores each number by activity
(0-100), assigns tier tags (Dial First through Drop), and produces:
  - a two-column CSV for DataSift's "Update Data -> Tag phones by phone number" upload
  - a reinserted copy of the source export with tags written back next to the phone
    they came from, ready to re-import via "Add Data -> Update existing records"

Two export layouts are auto-detected:
  - the flat DataSift "Phone Enrichment" export (Phone 1..Phone 30, optionally paired
    with existing Phone Tags 1..30 columns)
  - a per-contact "ready for dialing" export (e.g. "PR First Name"/"PR Last Name" +
    "PH: Phone1".."PH: Phone5", plus repeating "REL1: Full Name" + "REL1: Phone 1..3"
    contact blocks) -- this is the layout that was silently dropping every relative's
    phone before, since none of those headers matched the old bare "Phone N" regex.

General-purpose -- works on any DataSift/REISift export, not tied to the scraping pipeline.

Usage:
    # As a module (called from main.py phone-validate subcommand)
    from phone_validator import estimate_cost, run_phone_validation

    # Estimate only
    est = estimate_cost("Phone Enrichment.csv")

    # Full validation
    results = run_phone_validation("Phone Enrichment.csv", api_key, output_dir)
"""

import csv
import json
import logging
import os
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import openpyxl
import requests
from openpyxl import Workbook

import config

logger = logging.getLogger(__name__)

# ── Tier Configuration ────────────────────────────────────────────────────

DEFAULT_TIERS = {
    "Dial First":  (81, 100),
    "Dial Second": (61, 80),
    "Dial Third":  (41, 60),
    "Dial Fourth": (21, 40),
    "Drop":        (0, 20),
}

COST_PER_PHONE = 0.015  # Trestle phone_intel pricing

# Line types that are never personal numbers -- removed regardless of activity score.
# Deliberately does NOT include NonFixedVOIP or Landline: 24% of numbers Sift labels
# "Landline" are actually FixedVOIP/NonFixedVOIP and textable, so those are still
# scored by activity like any other number rather than auto-dropped.
SKIP_LINE_TYPES = {"tollfree", "premium", "voicemail"}

# ── Trestle API Config ────────────────────────────────────────────────────

TRESTLE_ENDPOINT = "https://api.trestleiq.com/3.0/phone_intel"
MAX_RETRIES = 3
RETRY_BACKOFF = 1.5  # seconds, multiplied each retry


# ── Phone Number Cleaning ─────────────────────────────────────────────────


def clean_phone(raw: str) -> str:
    """Strip a phone string down to digits, normalize to 10-digit US format."""
    if not raw:
        return ""
    digits = re.sub(r"[^\d]", "", str(raw).strip())
    # Handle 11-digit with leading 1 (country code)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    # Handle E.164 with +1
    if len(digits) > 10 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


# ── Trestle API Caller ────────────────────────────────────────────────────


def call_trestle(phone: str, api_key: str, add_litigator: bool = False) -> dict:
    """Call Trestle phone_intel API for a single phone number.

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
                wait = RETRY_BACKOFF * (2 ** attempt)
                logger.debug("Rate limited on %s, waiting %.1fs...", phone, wait)
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


# ── Tier Assignment ───────────────────────────────────────────────────────


def assign_tier(score: int | None, tiers: dict) -> str:
    """Given an activity score, return the matching tier tag name."""
    if score is None:
        return "Unknown"
    for tag_name, (low, high) in tiers.items():
        if low <= score <= high:
            return tag_name
    return "Unknown"


# ── Qualification Engine ──────────────────────────────────────────────────
#
# Mirrors the Trestle Ready workflow's evaluateTrestlePhone(): litigator override,
# then invalid, then non-personal line types, then activity-score tier. Tiering
# itself (assign_tier above) is unchanged -- this only decides whether a phone
# should ever reach a dial tier at all, and what to tag it when it shouldn't.


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


# ── Export Format Detection ──────────────────────────────────────────────
#
# Two real layouts are recognized:
#   Format A ("flat"): DataSift's wide "Phone Enrichment" export -- Phone 1..30,
#     optionally paired with existing Phone Tags 1..30 columns. One generic
#     contact per row.
#   Format B ("contact_blocks"): a per-contact "ready for dialing" export, e.g.
#     "PR First Name"/"PR Last Name" + "PH: Phone1".."PH: Phone5", plus repeating
#     "REL1: Full Name" + "REL1: Phone 1".."REL1: Phone 3" blocks (REL2..REL5, ...).
#     No pre-existing tag columns -- reinsertion creates one per phone column.
#   Format C ("single"): one generic "Phone"/"Phone Number" column, for simple
#     lead lists.
# If none of these match, raise loudly rather than silently processing zero
# phones -- a run that "succeeds" with no data is worse than one that fails.

_PHONE_N_RE = re.compile(r"^phone[\s_]?(\d+)$", re.IGNORECASE)
_METADATA_RE = re.compile(r"^phone\s*(type|status|tags?|is\s*connected)\s*\d*$", re.IGNORECASE)
_BLOCK_PHONE_RE = re.compile(r"^([A-Za-z0-9]+)\s*:\s*phone\s*(\d+)$", re.IGNORECASE)

_GENERIC_PHONE_NAMES = {
    "phone", "phone_number", "phone number", "phonenumber",
    "mobile", "cell", "landline", "home phone", "work phone",
    "contact phone", "primary phone",
}


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
    tag_column: str | None  # existing header (flat) -- None means "synthesize one"


def detect_phone_columns(headers: list[str]) -> list[str]:
    """Find bare numbered phone columns (Phone 1..Phone 30), excluding metadata
    columns like Phone Type N / Phone Status N / Phone Tags N / Phone Is Connected N.
    """
    found = []
    for header in headers:
        lower = header.strip().lower()
        if _METADATA_RE.match(lower):
            continue
        if _PHONE_N_RE.match(lower):
            found.append(header)
    return found


def detect_export_format(headers: list[str], phone_column: str | None = None) -> ExportFormat:
    """Auto-detect which of the real export layouts this header row is.

    Raises ValueError (naming the headers seen) if none match -- callers should
    treat that as fatal rather than proceeding with zero phones found.
    """
    header_by_lower = {h.strip().lower(): h for h in headers}

    if phone_column:
        # Explicit override (skill's --phone-column flag): treat as a single
        # generic column regardless of what else is in the file.
        return ExportFormat(
            kind="single",
            contacts=[ContactBlock(prefix="Property", phone_columns=[phone_column],
                                    tag_columns={phone_column: None})],
        )

    # ---- Format A: flat "Phone N" (+ optional "Phone Tags N") ----
    flat_cols = detect_phone_columns(headers)
    if flat_cols:
        def _slot(h: str) -> int:
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
    blocks_by_prefix: dict[str, list[tuple[int, str]]] = {}
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

        # Deterministic order: PR/PH block first, then REL1..RELn, then anything else.
        def _contact_sort_key(c: ContactBlock):
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


def _cell_to_str(val) -> str:
    """Normalize one openpyxl cell value to the string clean_phone()/csv expect.

    A phone number stored as a "General"-formatted Excel number comes back as a
    Python float (e.g. 4438754884.0) -- str()'ing that directly would leave a
    trailing ".0" that corrupts digit extraction downstream, so integer-valued
    floats are coerced to int first.
    """
    if val is None:
        return ""
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val).strip()


def load_export_rows(filepath: str | Path) -> tuple[list[str], list[dict]]:
    """Load a CSV or XLSX export into (headers, rows) -- rows are dicts keyed by
    the exact source header text, values normalized to strings.
    """
    filepath = Path(filepath)

    if filepath.suffix.lower() in (".xlsx", ".xlsm"):
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            return [], []
        headers = [_cell_to_str(c) for c in header_row]

        rows = []
        for raw_row in rows_iter:
            if raw_row is None or all(c is None for c in raw_row):
                continue
            row = {headers[i]: _cell_to_str(raw_row[i]) if i < len(raw_row) else ""
                   for i in range(len(headers))}
            rows.append(row)
        return headers, rows

    with open(filepath, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = [dict(r) for r in reader]
    return headers, rows


def extract_phone_entries(rows: list[dict], export_format: ExportFormat) -> list[PhoneEntry]:
    """Pull every phone out of every contact block of every row, tied back to
    exactly which row/column/contact it came from (for reinsertion later).
    """
    entries: list[PhoneEntry] = []
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


def read_phones_from_csv(
    filepath: str | Path, phone_column: str | None = None
) -> tuple[list[tuple[str, str]], int, int]:
    """Read phone numbers from a CSV or XLSX export (auto-detects layout).

    Returns:
        (phones_list, unique_count, total_entries)
        - phones_list: list of (raw, cleaned) phone tuples
        - unique_count: number of unique cleaned phone numbers
        - total_entries: total phone entries found (before dedup)
    """
    headers, rows = load_export_rows(filepath)
    export_format = detect_export_format(headers, phone_column=phone_column)
    entries = extract_phone_entries(rows, export_format)

    logger.info(
        "Detected export format '%s' (%d contact block(s)) -- %d phone entries across %d rows",
        export_format.kind, len(export_format.contacts), len(entries), len(rows),
    )

    phones = [(e.raw, e.cleaned) for e in entries]
    unique = {e.cleaned for e in entries}
    return phones, len(unique), len(entries)


# ── Cost Estimation ──────────────────────────────────────────────────────


def estimate_cost(filepath: str | Path) -> dict:
    """Parse CSV/XLSX to count unique phones and estimate Trestle API cost.

    Returns dict with stats for display or JSON output.
    """
    phones, unique_count, total_entries = read_phones_from_csv(filepath)

    cost = unique_count * COST_PER_PHONE

    return {
        "input_file": Path(filepath).name,
        "total_entries": total_entries,
        "unique_phones": unique_count,
        "duplicates_saved": total_entries - unique_count,
        "cost_per_phone": COST_PER_PHONE,
        "estimated_cost": round(cost, 2),
    }


def print_estimate(est: dict) -> None:
    """Print a formatted cost estimate to stdout."""
    print()
    print("=" * 50)
    print("  PHONE VALIDATION COST ESTIMATE")
    print("=" * 50)
    print(f"  Input file:          {est['input_file']}")
    print(f"  Total phone entries: {est['total_entries']:,}")
    print(f"  Unique phones:       {est['unique_phones']:,}")
    print(f"  Duplicates saved:    {est['duplicates_saved']:,}")
    print(f"  Cost per phone:      ${est['cost_per_phone']:.3f}")
    print(f"  -----------------------------------------")
    print(f"  ESTIMATED COST:      ${est['estimated_cost']:.2f}")
    print("=" * 50)
    print()


# ── Main Processing ──────────────────────────────────────────────────────


def process_phones(
    phones: list[tuple[str, str]],
    api_key: str,
    tiers: dict | None = None,
    add_litigator: bool = False,
    batch_size: int = 10,
    delay: float = 0.1,
) -> tuple[list[dict], list[dict]]:
    """Process all phone numbers through Trestle API.

    Args:
        phones: List of (raw, cleaned) phone tuples.
        api_key: Trestle API key.
        tiers: Tier definitions (default: DEFAULT_TIERS).
        add_litigator: Include litigator risk check (enables the litigator-override
            qualification rule; without it, that rule simply never fires).
        batch_size: Concurrent API requests per batch.
        delay: Seconds between batches.

    Returns:
        (results_list, errors_list). Each result carries "assigned_tag" (a dial
        tier, or "Litigator Risk" / "Invalid" / "Skip - <LineType>") and "keep"
        (False for anything that should never reach a dial-tier upload).
    """
    if tiers is None:
        tiers = DEFAULT_TIERS

    # Deduplicate
    unique_phones = list(dict.fromkeys(p[1] for p in phones))
    total = len(unique_phones)
    logger.info("Processing %d unique phone numbers...", total)

    results = []
    errors = []
    processed = 0

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
                    if data.get("error") == "Invalid API key":
                        logger.error("Invalid Trestle API key — aborting")
                        raise ValueError("Invalid Trestle API key")
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

                # Progress every 25 records
                if processed % 25 == 0 or processed == total:
                    pct = (processed / total) * 100
                    logger.info("  Progress: %d/%d (%.0f%%)", processed, total, pct)

        # Delay between batches
        if batch_start + batch_size < total and delay > 0:
            time.sleep(delay)

    return results, errors


# ── Per-Notice Phone Scoring (DM + heirs) ────────────────────────────────


DM_PHONE_FIELDS = [
    "primary_phone", "mobile_1", "mobile_2", "mobile_3", "mobile_4",
    "mobile_5", "landline_1", "landline_2", "landline_3",
]


def _collect_phones_from_notice(notice) -> list[str]:
    """Return all cleaned phones on a notice — DM #1 flat fields + heir_map_json."""
    out: list[str] = []
    for field_name in DM_PHONE_FIELDS:
        val = getattr(notice, field_name, "") or ""
        cleaned = clean_phone(val)
        if cleaned:
            out.append(cleaned)

    heir_json = getattr(notice, "heir_map_json", "") or ""
    if heir_json:
        try:
            heirs = json.loads(heir_json)
        except (ValueError, TypeError):
            heirs = []
        if isinstance(heirs, list):
            for h in heirs:
                if not isinstance(h, dict):
                    continue
                for ph in h.get("phones", []) or []:
                    cleaned = clean_phone(ph)
                    if cleaned:
                        out.append(cleaned)
    return out


def score_record_phones(
    notices: list,
    api_key: str | None = None,
    tiers: dict | None = None,
    add_litigator: bool = False,
    batch_size: int = 10,
    delay: float = 0.1,
) -> dict[str, dict]:
    """Trestle-score every phone attached to these notices (DM #1 + all heirs).

    Closes the coverage gap where only DM #1 phones got scored via the CSV-export
    workflow. Writes a `phone_scores` dict onto each heir in heir_map_json so
    downstream consumers (PDF, DataSift export) can surface tier badges.

    Returns a flat `{cleaned_phone: {"score": int, "tier": str, "line_type": str}}`
    dict, directly usable as the `phone_tiers` parameter of
    `report_generator.generate_record_pdf`.
    """
    key = api_key or getattr(config, "TRESTLE_API_KEY", "")
    if not key:
        logger.info("Trestle API key not set — skipping per-record phone scoring")
        return {}
    if tiers is None:
        tiers = DEFAULT_TIERS

    # Collect unique cleaned phones across all notices
    unique: dict[str, None] = {}
    for n in notices:
        for p in _collect_phones_from_notice(n):
            unique.setdefault(p, None)
    phones = list(unique.keys())
    if not phones:
        return {}

    logger.info("Trestle scoring %d unique phones across %d records (~$%.2f)",
                len(phones), len(notices), len(phones) * COST_PER_PHONE)

    results: dict[str, dict] = {}
    for batch_start in range(0, len(phones), batch_size):
        batch = phones[batch_start : batch_start + batch_size]
        with ThreadPoolExecutor(max_workers=min(batch_size, len(batch))) as executor:
            futures = {
                executor.submit(call_trestle, ph, key, add_litigator): ph
                for ph in batch
            }
            for future in as_completed(futures):
                ph = futures[future]
                try:
                    data = future.result()
                except Exception as e:
                    logger.debug("Trestle exception on %s: %s", ph, e)
                    continue
                if "error" in data and not data.get("is_valid"):
                    if data.get("error") == "Invalid API key":
                        logger.error("Invalid Trestle API key — aborting heir scoring")
                        return results
                    continue
                score = data.get("activity_score")
                line_type = data.get("line_type")
                results[ph] = {
                    "score": score,
                    "tier": assign_tier(score, tiers),
                    "line_type": line_type,
                }
        if batch_start + batch_size < len(phones) and delay > 0:
            time.sleep(delay)

    # Persist per-heir scores back into heir_map_json so downstream consumers
    # don't need the global dict to surface tier info.
    for n in notices:
        heir_json = getattr(n, "heir_map_json", "") or ""
        if not heir_json:
            continue
        try:
            heirs = json.loads(heir_json)
        except (ValueError, TypeError):
            continue
        if not isinstance(heirs, list):
            continue
        mutated = False
        for h in heirs:
            if not isinstance(h, dict):
                continue
            scores: dict[str, dict] = {}
            for ph in h.get("phones", []) or []:
                cleaned = clean_phone(ph)
                if cleaned and cleaned in results:
                    scores[ph] = results[cleaned]
            if scores:
                h["phone_scores"] = scores
                mutated = True
        if mutated:
            n.heir_map_json = json.dumps(heirs, ensure_ascii=False)

    return results


# ── Output Writers ────────────────────────────────────────────────────────


def write_datasift_tags_csv(results: list[dict], output_dir: str | Path) -> Path:
    """Write the DataSift-ready phone tags CSV (Phone Number + Phone Tag).

    This is the file uploaded to DataSift via "Update Data → Tag phones by phone number".
    Only phones the qualification engine marked "keep" are included -- litigator-risk,
    invalid, skip-line-type, and Drop-tier numbers are excluded (they're still visible,
    tagged, in validation_results.csv and in the reinserted export).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / "phone_tags_for_datasift.csv"

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Phone Number", "Phone Tag"])
        for r in results:
            if r.get("keep"):
                writer.writerow([r["phone_number"], r["assigned_tag"]])

    logger.info("DataSift phone tags CSV: %s (%d phones)", filepath, len(results))
    return filepath


def write_detailed_csv(results: list[dict], output_dir: str | Path) -> Path:
    """Write detailed validation results CSV with all API data."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / "validation_results.csv"

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

    logger.info("Detailed results CSV: %s", filepath)
    return filepath


def write_errors_csv(errors: list[dict], output_dir: str | Path) -> Path | None:
    """Write errors to CSV for review. Returns None if no errors."""
    if not errors:
        return None
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / "errors.csv"

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["phone_number", "error", "detail"])
        for e in errors:
            writer.writerow([
                e.get("phone_number", ""),
                e.get("error", ""),
                e.get("detail", ""),
            ])

    logger.warning("Errors CSV: %s (%d failed)", filepath, len(errors))
    return filepath


def write_summary(
    results: list[dict],
    errors: list[dict],
    tiers: dict,
    output_dir: str | Path,
) -> Path:
    """Write a human-readable summary of the validation run."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / "summary.txt"

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

        f.write("--- TIER BREAKDOWN ---\n\n")
        for tag_name in tiers.keys():
            count = tag_counts.get(tag_name, 0)
            pct = (count / total * 100) if total else 0
            f.write(f"  {tag_name:20s}  {count:5d}  ({pct:5.1f}%)\n")
        for tag_name in sorted(tag_counts.keys()):
            if tag_name not in tiers:
                count = tag_counts[tag_name]
                pct = (count / total * 100) if total else 0
                f.write(f"  {tag_name:20s}  {count:5d}  ({pct:5.1f}%)\n")

        f.write(f"\n--- LINE TYPE BREAKDOWN ---\n\n")
        for lt, count in line_type_counts.most_common():
            pct = (count / total * 100) if total else 0
            f.write(f"  {lt:20s}  {count:5d}  ({pct:5.1f}%)\n")

        f.write(f"\n--- SCORE DISTRIBUTION ---\n\n")
        for bucket in sorted(buckets.keys()):
            count = buckets[bucket]
            bar = "#" * max(1, count // max(1, total // 40))
            f.write(f"  {bucket:3d}-{min(bucket+9, 100):3d}  {count:5d}  {bar}\n")

        f.write(f"\n--- DATASIFT UPLOAD INSTRUCTIONS ---\n\n")
        f.write("1. Open your DataSift/REISift account\n")
        f.write("2. Go to Upload -> Update Data\n")
        f.write("3. Select 'Tag phones by phone number'\n")
        f.write("4. Upload phone_tags_for_datasift.csv\n")
        f.write("5. Map 'Phone Number' -> Phone Number\n")
        f.write("6. Map 'Phone Tag' -> Phone Tag\n")
        f.write("7. Complete the upload\n\n")
        f.write("Tags will apply to ALL records sharing each phone number.\n")
        f.write("When sending to a dialer, send ONE tier at a time.\n\n")
        f.write("For per-slot tags instead (tag stays tied to the exact phone/contact\n")
        f.write("it came from, e.g. a specific relative), re-import\n")
        f.write("reisift_reimport_with_phone_tags.csv/.xlsx via Add Data -> Update\n")
        f.write("existing records instead.\n")

    logger.info("Summary: %s", filepath)
    return filepath


def write_reinserted_export(
    headers: list[str],
    rows: list[dict],
    export_format: ExportFormat,
    phone_entries: list[PhoneEntry],
    results_by_phone: dict[str, dict],
    output_path: str | Path,
) -> Path:
    """Write a copy of the source export with each phone's qualification tag
    written back next to the phone it came from. Never deletes or blanks a
    phone cell -- rejected numbers (Invalid / Litigator Risk / Skip - X / Drop)
    are tagged in place too, same as kept ones.

    Flat exports (Format A) merge into the existing "Phone Tags N" cell,
    comma-appended to whatever's already there. Contact-block exports
    (Format B) get a new "<phone column> Tag" column inserted right after each
    phone column that doesn't already have one.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    out_headers = list(headers)
    tag_col_for_phone_col: dict[str, str] = {}

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

    if output_path.suffix.lower() in (".xlsx", ".xlsm"):
        wb = Workbook()
        ws = wb.active
        ws.append(out_headers)
        for row in out_rows:
            ws.append([row.get(h, "") for h in out_headers])
        wb.save(output_path)
    else:
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=out_headers)
            writer.writeheader()
            for row in out_rows:
                writer.writerow({h: row.get(h, "") for h in out_headers})

    logger.info("Reinserted export: %s (%d rows)", output_path, len(out_rows))
    return output_path


# ── High-Level Entry Points ──────────────────────────────────────────────


def run_phone_validation(
    csv_path: str | Path,
    api_key: str | None = None,
    output_dir: str | Path | None = None,
    tiers: dict | None = None,
    add_litigator: bool = False,
    batch_size: int = 10,
) -> dict:
    """Run full phone validation pipeline on a CSV or XLSX export.

    Args:
        csv_path: Path to a REISift/DataSift export (CSV or XLSX, either export
            layout described at the top of this module).
        api_key: Trestle API key (defaults to config.TRESTLE_API_KEY).
        output_dir: Output directory (defaults to output/phone_validation/).
        tiers: Custom tier definitions (defaults to DEFAULT_TIERS).
        add_litigator: Include litigator risk check (also enables the
            litigator-override qualification rule).
        batch_size: Concurrent API requests per batch.

    Returns:
        Dict with keys: success, results_count, errors_count, tag_csv_path,
        detail_csv_path, summary_path, reinserted_export_path, tier_counts.
    """
    if api_key is None:
        api_key = config.TRESTLE_API_KEY
    if not api_key:
        logger.error("No Trestle API key provided. Set TRESTLE_API_KEY in .env or pass --api-key.")
        return {"success": False, "message": "No Trestle API key"}

    if output_dir is None:
        output_dir = config.OUTPUT_DIR / "phone_validation"
    output_dir = Path(output_dir)

    if tiers is None:
        tiers = DEFAULT_TIERS

    csv_path = Path(csv_path)
    if not csv_path.exists():
        logger.error("Input file not found: %s", csv_path)
        return {"success": False, "message": f"File not found: {csv_path}"}

    # Load + detect layout + extract every phone with its row/column/contact context
    headers, rows = load_export_rows(csv_path)
    try:
        export_format = detect_export_format(headers)
    except ValueError as e:
        logger.error(str(e))
        return {"success": False, "message": str(e)}

    phone_entries = extract_phone_entries(rows, export_format)
    logger.info(
        "Detected export format '%s' (%d contact block(s): %s)",
        export_format.kind, len(export_format.contacts),
        ", ".join(c.prefix for c in export_format.contacts),
    )
    if not phone_entries:
        logger.error("No valid phone numbers found in %s", csv_path)
        return {"success": False, "message": "No valid phone numbers found"}

    phones = [(e.raw, e.cleaned) for e in phone_entries]
    unique_count = len({e.cleaned for e in phone_entries})
    total_entries = len(phone_entries)

    logger.info("Found %d phone entries (%d unique) — estimated cost: $%.2f",
                total_entries, unique_count, unique_count * COST_PER_PHONE)

    # Process through Trestle API
    results, errors = process_phones(
        phones=phones,
        api_key=api_key,
        tiers=tiers,
        add_litigator=add_litigator,
        batch_size=batch_size,
    )
    results_by_phone = {r["phone_number"]: r for r in results}

    # Write outputs
    tag_csv = write_datasift_tags_csv(results, output_dir)
    detail_csv = write_detailed_csv(results, output_dir)
    write_errors_csv(errors, output_dir)
    summary = write_summary(results, errors, tiers, output_dir)

    reinserted_path = output_dir / f"reisift_reimport_with_phone_tags{csv_path.suffix or '.csv'}"
    write_reinserted_export(
        headers, rows, export_format, phone_entries, results_by_phone, reinserted_path,
    )

    # Tier breakdown for logging
    tag_counts = Counter(r["assigned_tag"] for r in results)
    for tag_name in tiers.keys():
        count = tag_counts.get(tag_name, 0)
        logger.info("  %s: %d", tag_name, count)

    logger.info("Phone validation complete: %d scored, %d errors", len(results), len(errors))

    return {
        "success": True,
        "results_count": len(results),
        "errors_count": len(errors),
        "tag_csv_path": tag_csv,
        "detail_csv_path": detail_csv,
        "summary_path": summary,
        "reinserted_export_path": reinserted_path,
        "tier_counts": dict(tag_counts),
    }
