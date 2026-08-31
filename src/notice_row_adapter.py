"""Bridge the MD/DC/VA pull scripts' flat CSV rows into NoticeData.

Why this exists
---------------
The Tennessee pipeline flows through NoticeData and therefore gets the whole
enrichment chain for free (Smarty standardization, geocode, RDI, vacancy...).
The MD/DC and VA pull scripts predate that and emit plain CSV row dicts, so
they got none of it -- which is how a courthouse clerk's address reached a
review workbook as a subject property.

This module is the adapter: rows in, NoticeData out, enrichment applied,
values written back onto the rows so each script's existing CSV writer keeps
working unchanged.

Scope, deliberately narrow
--------------------------
Only Smarty address standardization is wired here. The rest of the TN chain is
NOT safe to apply blindly to MD/DC/VA:

  * tax_enricher    -- Knox County assessor API, TN-only
  * property_enricher / obituary / skip trace -- cost money per record and
    should be opted into per run, not fired implicitly by a scrape

Adding those is a per-step decision, not something this adapter should assume.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from notice_parser import NoticeData

logger = logging.getLogger(__name__)

# Flat CSV column -> NoticeData field. Both the VA and MDDC scripts share this
# schema apart from a couple of provenance columns (popular_search /
# saved_search, full_text), which have no NoticeData equivalent and are left
# on the row.
ROW_TO_FIELD = {
    "street": "address",
    "city": "city",
    "state": "state",
    "zip": "zip",
    "county": "county",
    "notice_type": "notice_type",
    "date_published": "date_published",
    "auction_date": "auction_date",
    "source_url": "source_url",
}

# Columns the adapter ADDS to a row after standardization. Callers should
# append these to their DictWriter fieldnames.
ENRICHED_COLUMNS = [
    "std_street", "std_city", "std_state", "std_zip", "std_zip_plus4",
    "latitude", "longitude", "dpv_match_code", "rdi", "vacant",
]

# DC is its own USPS state code; everything else in the MDDC footprint is MD.
_DC_COUNTY_RE = re.compile(r"washington,?\s*d\.?\s*c\.?|district of columbia|^dc$", re.I)

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%A, %B %d, %Y",     # "Friday, August 28, 2026"  <- VA grid format
    "%B %d, %Y",
    "%m/%d/%Y",
    "%m/%d/%y",
)


def normalize_date(raw: str) -> str:
    """Best-effort YYYY-MM-DD. Returns "" rather than guessing."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    logger.debug("Unparseable date %r -- leaving blank", raw)
    return ""


def infer_state(row: dict, default_state: str = "") -> str:
    """Resolve a USPS state for a row.

    Order: the row's own state, then DC detected from the county, then the
    caller's default (which is knowledge, not a guess -- the pull script knows
    which state's counties it searched). Returns "" when genuinely unknown.
    """
    explicit = (row.get("state") or "").strip().upper()
    if explicit:
        return explicit
    county = (row.get("county") or "").strip()
    if county and _DC_COUNTY_RE.search(county):
        return "DC"
    return (default_state or "").strip().upper()


def row_to_notice(row: dict, default_state: str = "") -> NoticeData:
    """Convert one flat pull-script row into a NoticeData."""
    notice = NoticeData()
    for col, field in ROW_TO_FIELD.items():
        val = (row.get(col) or "").strip()
        if val:
            setattr(notice, field, val)

    notice.state = infer_state(row, default_state)
    notice.date_published = normalize_date(row.get("date_published", ""))
    notice.auction_date = normalize_date(row.get("auction_date", ""))

    # Prefer the opened notice body; fall back to the truncated grid snippet.
    notice.raw_text = (
        (row.get("full_text") or "").strip()
        or (row.get("notice_text_snippet") or "").strip()
    )
    return notice


def rows_to_notices(rows: list[dict], default_state: str = "") -> list[NoticeData]:
    return [row_to_notice(r, default_state) for r in rows]


def apply_notice_to_row(row: dict, notice: NoticeData) -> None:
    """Write standardized/enriched values back onto the row.

    The ORIGINAL scraped columns are left untouched so a bad standardization
    can never destroy the source data; results land in std_* plus the
    validation columns. Callers decide which to trust.

    std_* is written ONLY on a real USPS match (dpv_match_code present).
    Without that guard an unmatched row echoes its own input back into
    std_street, which reads to a reviewer as "USPS-verified" when nothing was
    verified at all -- the same class of false confidence as the courthouse
    address this adapter exists to catch.
    """
    # Validation columns are always written: blank here is meaningful (no match).
    row["dpv_match_code"] = notice.dpv_match_code or ""
    row["rdi"] = notice.rdi or ""
    row["vacant"] = notice.vacant or ""
    row["latitude"] = notice.latitude or ""
    row["longitude"] = notice.longitude or ""

    matched = bool(notice.dpv_match_code)
    row["std_street"] = (notice.address or "") if matched else ""
    row["std_city"] = (notice.city or "") if matched else ""
    row["std_state"] = (notice.state or "") if matched else ""
    row["std_zip"] = (notice.zip or "") if matched else ""
    row["std_zip_plus4"] = (getattr(notice, "zip_plus4", "") or "") if matched else ""


def standardize_rows(
    rows: list[dict],
    auth_id: str,
    auth_token: str,
    default_state: str = "",
    expected_states: set[str] | None = None,
) -> dict:
    """Run Smarty over pull-script rows, in place.

    Returns a stats dict. Never raises on a Smarty failure: on any error the
    rows are returned untouched, matching the TN pipeline's graceful
    degradation.
    """
    stats = {"rows": len(rows), "standardized": 0, "commercial": 0, "vacant": 0}
    if not rows:
        return stats
    if not (auth_id and auth_token):
        logger.info("Smarty credentials not configured -- skipping standardization")
        return stats

    notices = rows_to_notices(rows, default_state)

    try:
        from address_standardizer import standardize_addresses
    except ImportError:
        logger.warning("smartystreets-python-sdk not installed -- skipping standardization")
        return stats

    try:
        standardize_addresses(
            notices, auth_id, auth_token, expected_states=expected_states
        )
    except Exception as e:  # graceful degradation, as in enrichment_pipeline
        logger.warning("Smarty standardization failed: %s", e)
        return stats

    for row, notice in zip(rows, notices):
        apply_notice_to_row(row, notice)
        if notice.dpv_match_code:
            stats["standardized"] += 1
        if (notice.rdi or "").lower().startswith("commercial"):
            stats["commercial"] += 1
        if (notice.vacant or "").upper() == "Y":
            stats["vacant"] += 1

    return stats
