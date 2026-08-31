"""Pull Maryland probate filings from the Register of Wills system into a CSV.

Two sources, both on registers.maryland.gov, both confirmed live 2026-08-25 --
this is its own pipeline, NOT the MDDC/VA trustee-sale pipeline
(mddc_trustee_sale_pull.py / va_trustee_sale_pull.py), even though all three
ultimately produce Maryland leads:

  1. Estate Search (frmEstateSearch2.aspx) -- the structured case database.
     Blank-name + county + Filing Date range enumerates every estate in a
     window. The results grid (#dgSearchResults) gives only County, Estate
     Number (linked to a RecordId), Filing Date, Date of Death, Type, Status,
     Name -- it does NOT carry Personal Rep or Attorney. Those live on the
     per-estate detail page (frmDocketImages.aspx?src=row&RecordId=<id>),
     which has clean labeled spans (#lblPersonalReps, #lblAttorney) in the
     format "NAME [<small>ADDRESS, CITY, STATE ZIP</small>] <br/>" repeated
     per person -- verified live against several real records, including a
     blank-PR case (estate 111602, type SJ) which is exactly the kind of
     record the reconciliation pass below exists to re-check later.

  2. Legal Notice Search (NoticeSearch.aspx) -- publishes the actual notice
     TEXT (like tnpublicnotice.com / mddcpublicnotices.com), which turns out
     to already embed decedent name, PR name + mailing address, date of
     death, and estate number in one free-text block per row -- confirmed
     live. This makes it a CHEAPER source than Estate Search's two-step
     search-then-detail-page flow for the fields that matter, so it is
     pulled and merged in, not just cross-checked. It also literally says
     "SMALL ESTATE ..." in the notice body for small estates, which doubles
     as a second signal for the SE exclusion below. It carries no visible
     "Latest data as of" marker of its own -- its date range is deliberately
     pinned to the SAME cutoff read off Estate Search (see cli below), not
     its own rolling default-30-days window.

CONFIRMED LIVE 2026-08-25, both load-bearing:
  - Direct `requests`/WebFetch to registers.maryland.gov times out entirely
    from this environment (not just a 403 on specific marketing pages) --
    Firecrawl is required for every fetch in this module, the same
    conclusion already reached for the MDDC/VA sibling sites.
  - Estate Search's #cboCountyId is a plain single <select>, no checkbox-list
    postback race like MDDC's county checkboxes. Legal Notice Search's own
    #cboCountyId DOES have a postback race though: setting it and clicking
    Search immediately submits before the county's own change-triggered
    postback round-trips, silently returning the UNFILTERED statewide result
    set (confirmed live: two back-to-back identical-length blank-state
    fetches). A 2000ms wait between setting the county and clicking Search
    fixes it -- the same class of bug already documented for MDDC's county
    checkboxes, just on a single dropdown instead of N checkboxes.
  - Estate Search's results-grid pager has no visible href/onclick in the
    fetched HTML (unlike a classic ASP.NET GridView's inline
    __doPostBack(...) attribute) -- clicking the page-number <a> by matching
    its exact text content in a live browser context (Firecrawl's
    executeJavascript) works regardless of how the click is actually wired,
    confirmed live paging from "Page 1 of 11" to "Page 2 of 11".

OPERATIONAL CADENCE (per the user, confirmed against a real Monday run
2026-08-25): courts don't file on weekends, so a county can show zero or
near-zero Filing Date activity for Sat/Sun -- that's expected, not a filter
bug (verified live: Calvert had exactly one record dated the "Latest data
as of" Sunday, and it was Estate Type SE, correctly excluded). The intended
schedule is WEEKLY, run on Mondays, as a check on the PREVIOUS week --
catching any estate whose docket entry lagged (a PR added a day or two
late, an entry backfilled after the weekend) rather than a strict daily
poll. The checkpoint design below already handles this correctly by
construction: each run picks up from wherever the last run's cutoff left
off, so a Monday run naturally covers the full prior week regardless of
how many days elapsed, and `run_reconciliation_pass`'s incomplete-queue
re-check is exactly the mechanism that catches those late-arriving PRs on
the following Monday.

Usage:
    # confirm dropdown option values + the "Latest data as of" marker
    # haven't drifted, before trusting a real pull
    python src/scripts/md_register_of_wills_pull.py --doctor

    # weekly incremental pull, intended to run Mondays (uses
    # output/md_row_last_run.json to pick up where the last successful run
    # left off; first run needs --since)
    python src/scripts/md_register_of_wills_pull.py --commit
    python src/scripts/md_register_of_wills_pull.py --since 07/26/2026 --commit

    # one county, dry run (nothing written to the ledger/checkpoint)
    python src/scripts/md_register_of_wills_pull.py --counties "Anne Arundel" --since 08/01/2026
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
OUTPUT_DIR = ROOT / "output"
FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"

ESTATE_SEARCH_URL = "https://registers.maryland.gov/RowNetWeb/Estates/frmEstateSearch2.aspx"
NOTICE_SEARCH_URL = "https://registers.maryland.gov/LegalNotice/Notices/NoticeSearch.aspx"
DETAIL_URL_TMPL = "https://registers.maryland.gov/RowNetWeb/Estates/frmDocketImages.aspx?src=row&RecordId={record_id}"

LEDGER_PATH = OUTPUT_DIR / "md_row_ledger.json"
LAST_RUN_PATH = OUTPUT_DIR / "md_row_last_run.json"

# Confirmed live 2026-08-25 against both Estate Search and Legal Notice
# Search's #cboCountyId <select> -- identical option list/values on both
# pages. Re-verify with --doctor if a county below stops matching (the site
# can renumber this list).
COUNTY_ID = {
    "Allegany": "1",
    "Anne Arundel": "2",
    "Baltimore County": "3",
    "Baltimore City": "24",
    "Calvert": "4",
    "Caroline": "5",
    "Carroll": "6",
    "Cecil": "7",
    "Charles": "8",
    "Dorchester": "9",
    "Frederick": "10",
    "Garrett": "11",
    "Harford": "12",
    "Howard": "13",
    "Kent": "14",
    "Montgomery": "15",
    "Prince George's": "16",
    "Queen Anne's": "17",
    "St. Mary's": "18",
    "Somerset": "19",
    "Talbot": "20",
    "Washington": "21",
    "Wicomico": "22",
    "Worcester": "23",
}

DEFAULT_COUNTIES = [
    "Montgomery",
    "Anne Arundel",
    "Frederick",
    "Carroll",
    "Calvert",
    "Charles",
    "Baltimore County",
]

# Estate Type codes confirmed live on Estate Search's #cboType dropdown:
# FP, LO, MA, MV, NP, RE, RJ, SE, SJ, UN. Per the user: exclude SE (Small
# Estate, under $50,000 -- too small to be a workable lead), SJ, and MV.
# Applied as a post-fetch filter (not a search-form filter) since the form's
# Estate Type field is single-select, not a multi-exclude control.
EXCLUDED_ESTATE_TYPES = {"SE", "SJ", "MV"}

# Legal Notice Search bodies literally say "SMALL ESTATE ..." for small
# estates -- a second, independent signal for the same SE exclusion on
# records that only came through this source.
SMALL_ESTATE_TEXT_RE = re.compile(r"\bSMALL ESTATE\b", re.I)


def load_credentials() -> dict:
    env = dotenv_values(str(ENV_PATH))
    key = env.get("FIRECRAWL_API_KEY")
    if not key:
        raise RuntimeError("Missing FIRECRAWL_API_KEY in .env")
    return {"firecrawl_key": key}


def firecrawl_scrape(url: str, actions: list, timeout: int = 120) -> list[str]:
    """POST to Firecrawl's /v2/scrape with an actions sequence, return the
    HTML of every 'scrape' action in order."""
    creds = load_credentials()
    resp = requests.post(
        FIRECRAWL_URL,
        headers={"Authorization": f"Bearer {creds['firecrawl_key']}", "Content-Type": "application/json"},
        json={"url": url, "actions": actions, "formats": ["html"]},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    scrapes = (data.get("actions") or {}).get("scrapes") or []
    return [s.get("html", "") for s in scrapes if s.get("html")]


def firecrawl_scrape_retry(url: str, actions: list, timeout: int = 120,
                            min_len: int = 500, attempts: int = 5) -> list[str]:
    """Same as firecrawl_scrape, but retries when the page comes back as a
    near-empty <body></body> shell -- confirmed live to happen intermittently
    on this vendor stack (same flakiness already documented for the VA
    public-notice site), rather than trusting a too-small page as a genuine
    empty result."""
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            htmls = firecrawl_scrape(url, actions, timeout=timeout)
        except Exception as e:  # noqa: BLE001 - genuinely want to retry on any transient error
            last_err = e
            time.sleep(2 * attempt)
            continue
        if htmls and max(len(h) for h in htmls) >= min_len:
            return htmls
        last_err = RuntimeError(f"attempt {attempt}: page(s) too small ({[len(h) for h in htmls]})")
        time.sleep(2 * attempt)
    raise RuntimeError(f"firecrawl_scrape_retry exhausted {attempts} attempts against {url}: {last_err}")


def _set_value_js(selector: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"""
        (function() {{
            var el = document.querySelector('{selector}');
            if (!el) return;
            var proto = el.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
            var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
            setter.call(el, '{escaped}');
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
        }})();
    """


def _click_pager_page_js(page_number: int) -> str:
    return f"""
        (function() {{
            var links = document.querySelectorAll('.grid-pager a');
            for (var i = 0; i < links.length; i++) {{
                if (links[i].textContent.trim() === '{page_number}') {{ links[i].click(); return; }}
            }}
        }})();
    """


# ── "Latest data as of" freshness marker ───────────────────────────────

LATEST_DATA_RE = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})\s+\d{1,2}:\d{2}:\d{2}\s*[AP]M")


def parse_latest_data_date(html: str) -> str | None:
    """Extract the MM/DD/YYYY date out of Estate Search's
    '#lblLatestDataDateTime' footer (e.g. "8/24/2026 4:00:00 PM
    (rownetwebalt)"). Returns None if the marker isn't present -- Legal
    Notice Search doesn't carry this marker at all (confirmed live), so
    callers must source the cutoff from Estate Search and pass it in rather
    than expecting to find it here too."""
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one("#lblLatestDataDateTime")
    if not el:
        return None
    m = LATEST_DATA_RE.search(el.get_text(" ", strip=True))
    if not m:
        return None
    m2, d2, y2 = m.group(1).split("/")
    return f"{int(m2):02d}/{int(d2):02d}/{y2}"


# ── Estate Search ───────────────────────────────────────────────────────

STATUS_RE = re.compile(r"Viewing Page (\d+) of (\d+) \((\d+) RECORDS TOTAL\)")


def build_estate_search_actions(county_id: str, date_from: str, date_to: str, max_pages: int) -> list:
    actions = [
        {"type": "wait", "milliseconds": 2000},
        {
            "type": "executeJavascript",
            "script": (
                _set_value_js("#cboCountyId", county_id)
                + _set_value_js("#DateOfFilingFrom", date_from)
                + _set_value_js("#DateOfFilingTo", date_to)
            ),
        },
        {"type": "wait", "milliseconds": 1500},
        {"type": "click", "selector": "#cmdSearch"},
        {"type": "wait", "milliseconds": 5000},
        {"type": "scrape"},
    ]
    for page in range(2, max_pages + 1):
        actions.append({"type": "executeJavascript", "script": _click_pager_page_js(page)})
        actions.append({"type": "wait", "milliseconds": 4500})
        actions.append({"type": "scrape"})
    return actions


def parse_estate_search_status(html: str) -> tuple[int, int, int] | None:
    m = STATUS_RE.search(html)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _mdy(d: str) -> str:
    """'6/19/2026' -> '06/19/2026' so both sources print dates the same way."""
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", d or "")
    return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}" if m else (d or "")


def parse_estate_search_grid(html: str, county: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#dgSearchResults")
    if not table:
        return []
    rows = []
    for tr in table.find_all("tr"):
        if "grid-header" in (tr.get("class") or []) or "grid-pager" in (tr.get("class") or []):
            continue
        cells = tr.find_all("td")
        if len(cells) != 7:
            continue
        link = cells[1].find("a")
        record_id_m = re.search(r"RecordId=(\d+)", link.get("href", "")) if link else None
        name_raw = cells[6].get_text(" ", strip=True)
        rows.append({
            "county": cells[0].get_text(strip=True) or county,
            "estate_number": cells[1].get_text(strip=True),
            "record_id": record_id_m.group(1) if record_id_m else "",
            "filing_date": _mdy(cells[2].get_text(strip=True)),
            "date_of_death": _mdy(cells[3].get_text(strip=True)),
            "estate_type": cells[4].get_text(strip=True),
            "status": cells[5].get_text(strip=True),
            "name_raw": name_raw,
        })
    return rows


def split_last_first_name(name_raw: str) -> tuple[str, str]:
    """Estate Search grid names are 'LAST , FIRST MIDDLE' (confirmed live,
    e.g. 'DOE , JOHN T'). The detail page's #lblName is the reverse,
    'FIRST MIDDLE LAST' with no comma -- prefer the detail page's name when
    available and only fall back to splitting the grid's comma form."""
    if "," in name_raw:
        last, _, first_mid = name_raw.partition(",")
        return first_mid.strip(), last.strip()
    # No comma = the detail page's "FIRST MIDDLE LAST [SUFFIX]" form. The
    # suffix belongs to the surname ("RONNIE JAMES SAMPLE SR" -> last
    # "SAMPLE SR", not "SR" -- a plain last-token split searched Land
    # Records for surname "SR" and found nothing, 2026-08-28).
    return _split_pr_name(name_raw.strip())


PERSON_BLOCK_RE = re.compile(r"([^\[\]]+?)\s*\[\s*<small>(.*?)</small>\s*\]", re.I)


def _parse_person_blocks(raw_html: str) -> list[dict]:
    """Parse the '#lblPersonalReps' / '#lblAttorney' detail-page format:
    'NAME [<small>ADDRESS, CITY, STATE ZIP</small>] <br/>' repeated per
    person -- confirmed live across several real records. The <br/> before
    each entry (not just after) must be stripped first, or it leaks into the
    NEXT person's captured name on any record with 2+ people."""
    raw_html = re.sub(r"<br\s*/?>", " ", raw_html, flags=re.I)
    people = []
    for name, addr in PERSON_BLOCK_RE.findall(raw_html):
        name = " ".join(name.split())
        addr_parts = [p.strip() for p in addr.split(",")]
        street = addr_parts[0] if addr_parts else ""
        city = addr_parts[1].strip() if len(addr_parts) > 1 else ""
        state_zip = addr_parts[2].strip() if len(addr_parts) > 2 else ""
        st_m = re.match(r"([A-Z]{2})\s+([\d-]+)", state_zip)
        state, zip5 = (st_m.group(1), st_m.group(2)) if st_m else ("", "")
        people.append({"name": name, "street": street, "city": city, "state": state, "zip": zip5})
    return people


def fetch_estate_detail(record_id: str) -> dict:
    url = DETAIL_URL_TMPL.format(record_id=record_id)
    htmls = firecrawl_scrape_retry(url, [{"type": "wait", "milliseconds": 2500}, {"type": "scrape"}])
    html = htmls[-1]
    soup = BeautifulSoup(html, "html.parser")

    def txt(sel):
        el = soup.select_one(sel)
        return el.get_text(" ", strip=True) if el else ""

    pr_el = soup.select_one("#lblPersonalReps")
    att_el = soup.select_one("#lblAttorney")
    personal_reps = _parse_person_blocks(pr_el.decode_contents()) if pr_el else []
    attorneys = _parse_person_blocks(att_el.decode_contents()) if att_el else []

    return {
        "estate_number": txt("#lblEstateNumber"),
        "estate_type": txt("#lblType"),
        "status": txt("#lblStatus"),
        "date_opened": txt("#lblDateOpened"),
        "date_closed": txt("#lblDateClosed"),
        "decedent_name": txt("#lblName"),
        "date_of_death": _mdy(txt("#lblDateOfDeath")),
        "date_of_filing": _mdy(txt("#lblDateOfFiling")),
        "personal_reps": personal_reps,
        "attorney": attorneys[0] if attorneys else None,
        "latest_data_as_of": parse_latest_data_date(html),
    }


def pull_estate_search(county: str, date_from: str, date_to: str, max_pages: int = 30,
                        fetch_details: bool = True) -> tuple[list[dict], str | None]:
    """Run Estate Search for one county's Filing Date range, paginate through
    every page, then fetch the detail page for every non-excluded estate.
    Returns (records, latest_data_as_of_date)."""
    county_id = COUNTY_ID[county]
    actions = build_estate_search_actions(county_id, date_from, date_to, max_pages=1)
    htmls = firecrawl_scrape_retry(ESTATE_SEARCH_URL, actions, timeout=90)
    first_html = htmls[-1]
    status = parse_estate_search_status(first_html)
    latest_data = parse_latest_data_date(first_html)
    if not status:
        # No results banner at all -- likely a genuine zero-record window.
        return [], latest_data
    _, total_pages, total_records = status
    print(f"  {county}: {total_records} records across {total_pages} page(s)")

    all_rows = parse_estate_search_grid(first_html, county)
    if total_pages > 1:
        actions = build_estate_search_actions(county_id, date_from, date_to, max_pages=min(total_pages, max_pages))
        htmls = firecrawl_scrape_retry(ESTATE_SEARCH_URL, actions, timeout=90 + 10 * total_pages)
        all_rows = []
        for html in htmls:
            all_rows.extend(parse_estate_search_grid(html, county))

    # De-dupe by estate_number (paging can theoretically overlap on a race).
    seen = set()
    deduped = []
    for row in all_rows:
        key = (row["county"], row["estate_number"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    kept = [r for r in deduped if r["estate_type"] not in EXCLUDED_ESTATE_TYPES]
    excluded_count = len(deduped) - len(kept)
    if excluded_count:
        print(f"  {county}: excluded {excluded_count} record(s) of type {EXCLUDED_ESTATE_TYPES}")

    records = []
    for row in kept:
        record = dict(row)
        if fetch_details and row["record_id"]:
            try:
                detail = fetch_estate_detail(row["record_id"])
                record.update(detail)
            except Exception as e:  # noqa: BLE001
                print(f"    WARNING: detail fetch failed for estate {row['estate_number']} ({row['record_id']}): {e}")
            time.sleep(1.5)  # be polite between per-record detail fetches
        first_name, last_name = split_last_first_name(record.get("decedent_name") or row["name_raw"])
        record["decedent_first_name"] = first_name
        record["decedent_last_name"] = last_name
        record["source"] = "estate_search"
        records.append(record)
    return records, latest_data


# ── Legal Notice Search ─────────────────────────────────────────────────

NOTICE_ESTATE_NO_RE = re.compile(r"ESTATE NO\.?\s*([A-Z0-9-]+)", re.I)
# ":" allowed so "ESTATE OF JOHN Q SAMPLE AKA: JOHN QUINCY SAMPLE WHO DIED"
# matches; the AKA tail is stripped afterwards (kept in `decedent_aka`).
NOTICE_ESTATE_OF_RE = re.compile(r"ESTATE OF\s*([A-Z][A-Z .,':-]*?)\s*(?:ESTATE NO|WHO DIED|WITH A WILL|WITHOUT A WILL)", re.I)
# The appointment sentence, both shapes seen live on Anne Arundel 08/26/2026:
#   "NOTICE IS GIVEN THAT: NAME WHOSE ADDRESS IS ADDR WAS ON <date> APPOINTED"
#   "NOTICE IS GIVEN THAT: NAME WHOSE ADDRESS IS ADDR, NAME2 WHOSE ADDRESS IS
#    ADDR2 WERE ON <date> APPOINTED"                      (co-personal reps)
# The old regex demanded a single NAME/ADDR pair followed by "WAS ON", so
# every co-PR notice fell into unparsed_other and the estate was silently
# dropped from this source (caught live: estate 100001, two PRs).
NOTICE_APPOINTED_RE = re.compile(
    r"NOTICE IS GIVEN THAT:?\s*(.+?)\s+(?:WAS|WERE) ON\s+[A-Z]+ \d{1,2},?\s*\d{4}\s+APPOINTED", re.I)
NOTICE_PR_PAIR_RE = re.compile(r"(.+?)\s+WHOSE ADDRESS IS\s+(.+?)(?=,\s*[A-Z][A-Z .'-]+\s+WHOSE ADDRESS IS|$)", re.I | re.S)
# Foreign personal representative notice (decedent domiciled out of state,
# ancillary MD estate). Different sentence, and it carries something the
# domestic notice never does -- the Maryland real property itself:
#   "...APPOINTED NAME WHOSE ADDRESS IS ADDR AS THE PERSONAL REPRESENTATIVE
#    OF THE ESTATE OF DECEDENT ... WHO DIED ON <date> DOMICILED IN FLORIDA
#    ... THE DECEDENT OWNED REAL OR LEASEHOLD PROPERTY IN THE FOLLOWING
#    MARYLAND COUNTIES: ANNE ARUNDEL 123 EXAMPLE DRIVE ODENTON MD 21113 ."
NOTICE_FOREIGN_PR_RE = re.compile(
    r"APPOINTED\s+(.+?)\s+WHOSE ADDRESS IS\s+(.+?)\s+AS THE (?:FOREIGN )?PERSONAL REPRESENTATIVE", re.I)
NOTICE_FOREIGN_PROPERTY_RE = re.compile(
    r"PROPERTY IN THE FOLLOWING MARYLAND COUNT(?:Y|IES):\s*(.+?)\s*\.\s*ALL PERSONS", re.I | re.S)
NOTICE_FOREIGN_RE = re.compile(r"FOREIGN PERSONAL REPRESENTATIVE", re.I)
NOTICE_DOD_RE = re.compile(r"WHO DIED ON\s*([A-Z]+ \d{1,2},?\s*\d{4})", re.I)
NOTICE_PUBLISHED_RE = re.compile(r"Published on\s*(\d{2}/\d{2}/\d{4})", re.I)
NOTICE_COUNTY_RE = re.compile(r"^(.*?County|Baltimore City)Published on", re.I)
# "NOTICE OF JUDICIAL PROBATE" is a court-hearing notice on a petition, not a
# PR appointment -- it never carries the "WHOSE ADDRESS IS ... WAS ON"
# appointment sentence parse_notice_row needs, so it always fails to parse.
# Confirmed live 2026-08-27 (Basem): these silently returned None alongside
# genuinely broken rows, with no way to tell the two apart from the outside.
JUDICIAL_PROBATE_RE = re.compile(r"NOTICE OF JUDICIAL PROBATE", re.I)


STREET_SUFFIXES = {
    "ST", "STREET", "AVE", "AVENUE", "RD", "ROAD", "DR", "DRIVE", "LN", "LANE",
    "CT", "COURT", "BLVD", "BOULEVARD", "WAY", "PL", "PLACE", "TER", "TERRACE",
    "CIR", "CIRCLE", "PKWY", "PARKWAY", "HWY", "HIGHWAY", "TRL", "TRAIL",
    "LOOP", "RUN", "PATH", "XING", "CROSSING", "SQ", "SQUARE", "PT", "POINT",
    "CV", "COVE", "WALK", "ROW", "PIKE", "ALY", "ALLEY", "PLZ", "PLAZA",
    "GLN", "GLEN", "MNR", "MANOR", "RDG", "RIDGE", "VW", "VIEW",
}


# Suffix words that are ALSO common as the first word of a Maryland city
# ("GLEN Burnie", "Sparrows POINT", "Perry HALL"...). When one of these is
# the LAST suffix in a run but a strong suffix (RD/DR/ST/...) appears
# earlier, the strong one is the street's end -- caught live 2026-08-28:
# SDAT premises "345 EXAMPLE RD GLEN BURNIE" split as street "345 EXAMPLE
# RD GLEN" + city "BURNIE".
WEAK_SUFFIXES = {"GLN", "GLEN", "PT", "POINT", "MNR", "MANOR", "RDG", "RIDGE", "VW", "VIEW",
                 "RUN", "PATH", "LOOP", "ROW", "WALK", "CV", "COVE", "SQ", "SQUARE", "PL", "PLACE"}


def split_street_city_tokens(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Split a run of tokens with no delimiter between street and city at
    the LAST recognised street-suffix word -- preferring the last STRONG
    suffix when the last suffix overall is a weak one that can open a city
    name. Returns (street_tokens, city_tokens)."""
    last_any = last_strong = None
    for i, tok in enumerate(tokens):
        t = tok.upper().rstrip(".")
        if t in STREET_SUFFIXES:
            last_any = i
            if t not in WEAK_SUFFIXES:
                last_strong = i
    idx = last_any
    if last_any is not None and tokens[last_any].upper().rstrip(".") in WEAK_SUFFIXES and last_strong is not None:
        idx = last_strong
    if idx is not None and idx == len(tokens) - 1:
        # The only/last suffix IS the final token ("5 SAMPLE RUN SPARROWS
        # POINT"): it must be the city's last word, so back off to the
        # previous suffix if there is one.
        earlier = [i for i, t in enumerate(tokens[:-1]) if t.upper().rstrip(".") in STREET_SUFFIXES]
        idx = earlier[-1] if earlier else None
    if idx is None:
        if len(tokens) > 1:
            return tokens[:-1], tokens[-1:]
        return tokens, []
    # A post-directional right after the suffix is still the street
    # ("123 MAPLE ST SE GLEN BURNIE" -> street "123 MAPLE ST SE").
    if idx + 2 < len(tokens) and tokens[idx + 1].upper().rstrip(".") in {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}:
        idx += 1
    return tokens[: idx + 1], tokens[idx + 1 :]


def _split_address_blob(addr: str) -> dict:
    """Legal Notice's inline address has no delimiter between street and
    city beyond a trailing 'CITY, ST ZIP' -- e.g.
    '100 EXAMPLE ST OCEAN VIEW, DE 19970-4441'. A plain regex split is
    ambiguous (both street and city can be multi-word: 'EXAMPLE COURT
    OWINGS' is street '456 EXAMPLE COURT' + city 'OWINGS', confirmed live
    on estate 10001 in Calvert). Find the LAST word before the comma that is
    a known street suffix and split right after it -- everything up to and
    including that word is the street, everything after is the city."""
    pre_comma_m = re.match(r"^(.*?),\s*([A-Z]{2})\s+([\d-]+)$", addr.strip())
    if not pre_comma_m:
        return {"street": addr.strip(), "city": "", "state": "", "zip": ""}
    body, state, zip5 = pre_comma_m.groups()
    st_tokens, city_tokens = split_street_city_tokens(body.split())
    return {"street": " ".join(st_tokens).strip(), "city": " ".join(city_tokens).strip(), "state": state, "zip": zip5}


def _split_pr_pairs(block: str) -> list[tuple[str, str]]:
    """'A WHOSE ADDRESS IS X, B WHOSE ADDRESS IS Y' -> [(A, X), (B, Y)].
    Split on the WHOSE ADDRESS IS anchors; the address runs up to the comma
    that precedes the next name (an address itself has exactly one comma,
    the one before 'ST ZIP', so the split point is the comma FOLLOWED by a
    name and another anchor)."""
    parts = re.split(r"\s+WHOSE ADDRESS IS\s+", block, flags=re.I)
    if len(parts) < 2:
        return []
    pairs = []
    names = [parts[0]]
    for chunk in parts[1:-1]:
        # chunk = "<address>, <next name>" -- the next name is whatever
        # follows the LAST comma that is followed by non-address text.
        m = re.match(r"^(.+?,\s*[A-Z]{2}\s+[\d-]+)\s*,\s*(.+)$", chunk.strip(), re.I | re.S)
        if m:
            addr, nxt = m.group(1), m.group(2)
        else:
            addr, _, nxt = chunk.rpartition(",")
        pairs.append((names[-1], addr.strip()))
        names.append(nxt.strip())
    pairs.append((names[-1], parts[-1].strip()))
    return [(" ".join(n.split()), a) for n, a in pairs if n.strip()]


def _parse_foreign_property(blob: str) -> dict | None:
    """'ANNE ARUNDEL\\n123 EXAMPLE DRIVE\\nODENTON MD 21113' -> address dict.
    The county name comes first, then the street line, then 'CITY ST ZIP'
    (no comma in this variant)."""
    lines = [l.strip() for l in re.split(r"[\n\r]+", blob) if l.strip()]
    if not lines:
        return None
    tail_re = re.compile(r"^(.*?)\s*,?\s*(MD|MARYLAND)\s+(\d{5})(?:-\d{4})?\s*$", re.I)
    # Preferred: the grid keeps the notice's own line breaks --
    # [county, street, "CITY MD ZIP"] -- so no splitting heuristic is needed.
    if len(lines) >= 3 and re.match(r"^\d{1,6}[A-Z]?\s", lines[-2]) and tail_re.match(lines[-1]):
        m = tail_re.match(lines[-1])
        return {"street": lines[-2], "city": m.group(1).strip(), "state": "MD", "zip": m.group(3)}
    flat = " ".join(lines)
    m = re.search(r"(\d{1,6}[A-Z]?\s.*?)\s*,?\s*(MD|MARYLAND)\s+(\d{5})(?:-\d{4})?\s*$", flat, re.I)
    if not m:
        return None
    st_tokens, city_tokens = split_street_city_tokens(m.group(1).split())
    return {"street": " ".join(st_tokens), "city": " ".join(city_tokens), "state": "MD", "zip": m.group(3)}


def parse_notice_row(text: str) -> dict | None:
    county_m = NOTICE_COUNTY_RE.search(text)
    published_m = NOTICE_PUBLISHED_RE.search(text)
    estate_no_m = NOTICE_ESTATE_NO_RE.search(text)
    estate_of_m = NOTICE_ESTATE_OF_RE.search(text)
    dod_m = NOTICE_DOD_RE.search(text)
    if not estate_no_m:
        return None

    foreign = bool(NOTICE_FOREIGN_RE.search(text))
    property_addr = None
    if foreign:
        appointed_m = NOTICE_FOREIGN_PR_RE.search(text)
        if not appointed_m:
            return None
        pr_pairs = [(" ".join(appointed_m.group(1).split()), appointed_m.group(2))]
        prop_m = NOTICE_FOREIGN_PROPERTY_RE.search(text)
        if prop_m:
            property_addr = _parse_foreign_property(prop_m.group(1))
    else:
        appointed_m = NOTICE_APPOINTED_RE.search(text)
        if not appointed_m:
            return None
        pr_pairs = _split_pr_pairs(appointed_m.group(1))
        if not pr_pairs:
            return None

    pr_name, pr_addr_raw = pr_pairs[0]
    pr_addr = _split_address_blob(pr_addr_raw)
    extra_prs = [{"name": n, **_split_address_blob(a)} for n, a in pr_pairs[1:]]
    # "WHO DIED ON JANUARY 15, 2021" -> "01/15/2021", the same MM/DD/YYYY
    # form Estate Search's grid uses, so the two sources agree in the CSV.
    dod = dod_m.group(1) if dod_m else ""
    if dod:
        for fmt in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"):
            try:
                dod = datetime.strptime(" ".join(dod.split()).title(), fmt).strftime("%m/%d/%Y")
                break
            except ValueError:
                continue
    decedent_name = " ".join(estate_of_m.group(1).split()).rstrip(",") if estate_of_m else ""
    decedent_aka = ""
    aka_m = re.match(r"^(.*?)\s+(?:AKA|A/K/A|ALSO KNOWN AS)\s*:?\s*(.+)$", decedent_name, re.I)
    if aka_m:
        decedent_name, decedent_aka = aka_m.group(1).strip(), aka_m.group(2).strip()
    # A trailing generational suffix is not the surname ("ROBERT RICHARD DOE
    # SR" -> last DOE SR, not last "SR").
    if "," in decedent_name:
        first_name, last_name = split_last_first_name(decedent_name)
    elif decedent_name:
        first_name, last_name = _split_pr_name(decedent_name)
    else:
        first_name, last_name = "", ""

    return {
        "county": county_m.group(1).strip() if county_m else "",
        "published_on": published_m.group(1) if published_m else "",
        "estate_number": estate_no_m.group(1).strip(),
        "decedent_name": decedent_name,
        "decedent_aka": decedent_aka,
        "decedent_first_name": first_name,
        "decedent_last_name": last_name,
        "date_of_death": dod,
        "pr_name": pr_name,
        "pr_address": pr_addr,
        "extra_prs": extra_prs,
        "foreign_pr": foreign,
        "notice_property": property_addr,  # only the foreign-PR notice states it
        "is_small_estate": bool(SMALL_ESTATE_TEXT_RE.search(text)),
        "source": "legal_notice",
    }


def build_notice_search_actions(county_id: str, date_from: str, date_to: str, max_pages: int,
                                 select_wait_ms: int = 2000) -> list:
    actions = [
        {"type": "wait", "milliseconds": 3000},
        {
            "type": "executeJavascript",
            "script": (
                _set_value_js("#cboCountyId", county_id)
                + _set_value_js("#txtDoPFrom", date_from)
                + _set_value_js("#txtDoPTo", date_to)
            ),
        },
        # CONFIRMED LIVE 2026-08-25, RECONFIRMED 2026-08-27: without a long
        # enough wait here, clicking Search races the county select's own
        # change-triggered postback and silently returns the unfiltered
        # statewide result set instead of erroring -- same class of bug as
        # MDDC's county-checkbox race. 2000ms wasn't always enough (caught
        # live 2026-08-27 on Anne Arundel returning 1774 statewide records);
        # callers that see a suspiciously large total should retry with a
        # longer select_wait_ms rather than trusting the count.
        {"type": "wait", "milliseconds": select_wait_ms},
        {"type": "click", "selector": "#cmdSearch"},
        {"type": "wait", "milliseconds": 5000},
        {"type": "scrape"},
    ]
    for page in range(2, max_pages + 1):
        actions.append({"type": "executeJavascript", "script": _click_pager_page_js(page)})
        actions.append({"type": "wait", "milliseconds": 4000})
        actions.append({"type": "scrape"})
    return actions


NOTICE_STATUS_RE = re.compile(r"Viewing Page (\d+) of (\d+) \((\d+) RECORDS TOTAL\)")


def parse_notice_search_status(html: str) -> tuple[int, int, int] | None:
    m = NOTICE_STATUS_RE.search(html)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def parse_notice_grid(html: str) -> list[dict]:
    """Returns (records, skip_counts). skip_counts breaks out WHY a row
    didn't become a record -- judicial-probate hearing notices (expected,
    not a bug) vs. a genuine parse failure (worth investigating) -- so a
    page that's mostly hearing notices doesn't read as a silent zero the
    way it did before 2026-08-27."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#dgSearchResults")
    if not table:
        return [], {}
    records = []
    skip_counts: dict[str, int] = {}
    for tr in table.find_all("tr"):
        text = tr.get_text(" ", strip=True)
        if not text or "Published on" not in text:
            continue
        rec = parse_notice_row(text)
        if rec:
            records.append(rec)
        elif JUDICIAL_PROBATE_RE.search(text):
            skip_counts["judicial_probate_hearing"] = skip_counts.get("judicial_probate_hearing", 0) + 1
        else:
            skip_counts["unparsed_other"] = skip_counts.get("unparsed_other", 0) + 1
    return records, skip_counts


# A real single-county window has never come close to this live -- Anne
# Arundel over 30 days ran in the tens/low hundreds. A total this large is
# the signature of the documented county-select race (the click on Search
# firing before the county change's own postback resolved, so the query
# ran statewide instead of scoped) rather than a real count.
SUSPECT_STATEWIDE_LEAK_THRESHOLD = 500


def pull_legal_notice_search(county: str, date_from: str, date_to: str, max_pages: int = 30) -> list[dict]:
    county_id = COUNTY_ID[county]

    def _fetch(select_wait_ms: int):
        actions = build_notice_search_actions(county_id, date_from, date_to, max_pages=1,
                                               select_wait_ms=select_wait_ms)
        htmls = firecrawl_scrape_retry(NOTICE_SEARCH_URL, actions, timeout=90)
        return htmls[-1]

    first_html = _fetch(select_wait_ms=2000)
    status = parse_notice_search_status(first_html)
    if not status:
        return []
    _, total_pages, total_records = status
    if total_records > SUSPECT_STATEWIDE_LEAK_THRESHOLD:
        print(f"  {county} (Legal Notice): WARNING {total_records} records looks like the statewide-leak race "
              f"(county select postback not settled before Search) -- retrying with a longer wait.")
        first_html = _fetch(select_wait_ms=4000)
        status = parse_notice_search_status(first_html)
        if not status:
            return []
        _, total_pages, total_records = status
        if total_records > SUSPECT_STATEWIDE_LEAK_THRESHOLD:
            print(f"  {county} (Legal Notice): still {total_records} records after retry -- likely a genuine "
                  f"statewide result, not scoped to {county}. Records below are UNTRUSTED for county attribution.")
    print(f"  {county} (Legal Notice): {total_records} records across {total_pages} page(s)")

    all_records, skip_counts = parse_notice_grid(first_html)
    if total_pages > 1:
        actions = build_notice_search_actions(county_id, date_from, date_to, max_pages=min(total_pages, max_pages))
        htmls = firecrawl_scrape_retry(NOTICE_SEARCH_URL, actions, timeout=90 + 10 * total_pages)
        all_records = []
        skip_counts = {}
        for html in htmls:
            recs, skips = parse_notice_grid(html)
            all_records.extend(recs)
            for k, v in skips.items():
                skip_counts[k] = skip_counts.get(k, 0) + v
    if skip_counts:
        print(f"  {county} (Legal Notice): skipped {skip_counts} (judicial-probate hearing notices are expected "
              f"and not a bug; unparsed_other is worth checking)")

    kept = [r for r in all_records if not r["is_small_estate"]]
    excluded_count = len(all_records) - len(kept)
    if excluded_count:
        print(f"  {county} (Legal Notice): excluded {excluded_count} SMALL ESTATE record(s)")
    for r in kept:
        r["county"] = county  # the search was scoped to this county; trust it over the parsed text
    return kept


# ── Merge Estate Search + Legal Notice Search by estate number ─────────

def _notice_prs(rec: dict) -> list[dict]:
    """Legal-notice PRs as the same list-of-dicts shape Estate Search's
    detail page yields, co-PRs included."""
    prs = []
    if rec.get("pr_name"):
        prs.append({"name": rec["pr_name"], **{k: rec["pr_address"].get(k, "") for k in ("street", "city", "state", "zip")}})
    prs.extend(rec.get("extra_prs") or [])
    return prs


def _apply_notice_property(target: dict, rec: dict) -> None:
    """A foreign-PR notice states the Maryland property outright. Take it as
    the property address (source legal_notice_text) unless a deed/SDAT
    lookup already resolved one; fill_addresses() still runs the SDAT
    owner check on it, so `property_sdat_owner` is filled the same way."""
    prop = rec.get("notice_property")
    if not prop or target.get("property_street"):
        return
    target.update({
        "property_street": prop["street"], "property_city": prop["city"],
        "property_state": prop["state"], "property_zip": prop["zip"],
        "property_lookup_confidence": "HIGH", "property_lookup_source": "legal_notice_text",
        "property_lookup_checked": True, "property_needs_sdat_owner": True,
    })


def merge_sources(estate_records: list[dict], notice_records: list[dict]) -> dict[str, dict]:
    """Key by (county, estate_number). Legal Notice supplies PR name/address
    when Estate Search's detail page had none yet (the common case for a
    freshly-filed estate); Estate Search's Status/Type/dates win when both
    are present, since it's the more authoritative structured source."""
    merged: dict[str, dict] = {}
    for rec in estate_records:
        key = (rec["county"], rec["estate_number"])
        merged[key] = dict(rec)

    for rec in notice_records:
        key = (rec["county"], rec["estate_number"])
        prs = _notice_prs(rec)
        if key not in merged:
            # Notice-only record (no matching Estate Search row this run) --
            # normalize to the same shape Estate Search records use
            # (personal_reps list, legal_notice_published_on) so the CSV
            # writer and the ledger's is_incomplete() check work uniformly
            # regardless of which source a record came from.
            normalized = dict(rec)
            normalized["personal_reps"] = prs
            normalized["legal_notice_published_on"] = rec.get("published_on", "")
            normalized["status"] = normalized.get("status", "")
            _apply_notice_property(normalized, rec)
            merged[key] = normalized
            continue
        existing = merged[key]
        if not existing.get("personal_reps") and prs:
            existing["personal_reps"] = prs
        if not existing.get("date_of_death") and rec.get("date_of_death"):
            existing["date_of_death"] = rec["date_of_death"]
        for k in ("decedent_aka", "foreign_pr"):
            if rec.get(k) and not existing.get(k):
                existing[k] = rec[k]
        _apply_notice_property(existing, rec)
        existing["source"] = "both" if existing.get("source") != rec.get("source") else existing.get("source")
        existing.setdefault("legal_notice_published_on", rec.get("published_on"))
    return merged


# ── Ledger / reconciliation ──────────────────────────────────────────────

def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def is_incomplete(record: dict) -> bool:
    return not record.get("personal_reps")


def reconcile_ledger(ledger: dict, merged: dict[(str, str), dict], run_date: str) -> dict:
    """Diff freshly-pulled records against the ledger. Three outcomes,
    logged distinctly per the plan -- never a silent overwrite:
      - added: an estate key not previously in the ledger
      - changed: a field (most importantly personal_reps) differs from what
        was stored
      - unchanged: identical, just refresh last_checked
    Estates already in the ledger but MISSING from this pull are left alone
    here -- they're only re-verified when they come up in the reconciliation
    lookback pass (see run_reconciliation_pass), not on every forward pull.
    """
    changes = {"added": [], "changed": [], "unchanged": []}
    estates = ledger.setdefault("estates", {})
    for (county, estate_number), record in merged.items():
        key = f"{county}|{estate_number}"
        if key not in estates:
            estates[key] = {
                **record,
                "first_seen": run_date,
                "last_checked": run_date,
                "incomplete": is_incomplete(record),
                "incomplete_since": run_date if is_incomplete(record) else None,
            }
            changes["added"].append(key)
            continue

        existing = estates[key]
        prev_prs = existing.get("personal_reps") or []
        new_prs = record.get("personal_reps") or []
        prev_status = existing.get("status")
        new_status = record.get("status")
        if prev_prs != new_prs or (new_status and prev_status != new_status):
            print(f"  RECONCILE changed: {key} -- PRs {prev_prs!r} -> {new_prs!r}, "
                  f"status {prev_status!r} -> {new_status!r}")
            existing.update(record)
            existing["last_checked"] = run_date
            was_incomplete = existing.get("incomplete")
            existing["incomplete"] = is_incomplete(record)
            if was_incomplete and not existing["incomplete"]:
                existing["incomplete_since"] = None
            changes["changed"].append(key)
        else:
            existing["last_checked"] = run_date
            changes["unchanged"].append(key)
    return changes


def run_reconciliation_pass(ledger: dict, counties: list[str], run_date: str,
                              lookback_days: int = 45, retry_ceiling_days: int = 90) -> None:
    """Re-check estates already on the ledger's 'incomplete' queue (missing
    PR, most commonly) -- separate from, and cheaper than, re-diffing the
    entire rolling lookback window on every run. Drops an estate from active
    re-checking after `retry_ceiling_days` on the queue, logging it as
    given up rather than retrying forever."""
    estates = ledger.get("estates", {})
    run_dt = datetime.strptime(run_date, "%m/%d/%Y")
    to_check = []
    for key, record in estates.items():
        if not record.get("incomplete"):
            continue
        since = record.get("incomplete_since")
        if since:
            age_days = (run_dt - datetime.strptime(since, "%m/%d/%Y")).days
            if age_days > retry_ceiling_days:
                if not record.get("gave_up"):
                    print(f"  RECONCILE gave up (still incomplete after {age_days}d): {key}")
                    record["gave_up"] = True
                continue
        to_check.append((key, record))

    if not to_check:
        print("  Reconciliation: nothing on the incomplete queue to re-check.")
        return
    print(f"  Reconciliation: re-checking {len(to_check)} incomplete estate(s).")
    for key, record in to_check:
        record_id = record.get("record_id")
        if not record_id:
            continue  # a legal-notice-only record has no detail page to re-check
        try:
            detail = fetch_estate_detail(record_id)
        except Exception as e:  # noqa: BLE001
            print(f"    WARNING: reconciliation re-fetch failed for {key}: {e}")
            continue
        prev_prs = record.get("personal_reps") or []
        new_prs = detail.get("personal_reps") or []
        if new_prs and new_prs != prev_prs:
            print(f"  RECONCILE changed (incomplete queue): {key} -- PR appeared: {new_prs}")
            record.update(detail)
            record["incomplete"] = is_incomplete(detail)
            if not record["incomplete"]:
                record["incomplete_since"] = None
        record["last_checked"] = run_date
        time.sleep(1.5)


# ── Property address lookup (Maryland Land Records + SDAT) ─────────────
#
# Bright MLS Matrix was the originally planned source here; abandoned live
# 2026-08-25 after an unresolvable MFA + Chrome-profile-automation blocker
# (full story in md_land_records_lookup.py's module docstring). Per the
# user: use Maryland's land records instead. That module implements the
# actual grantee-name-search -> deed-PDF-text -> SDAT bridge; this just
# wires it into the ledger so a lookup is never repeated once resolved (or
# once given up on).

def fill_addresses(ledger: dict, counties: list[str]) -> None:
    import md_land_records_lookup as landrec

    estates = ledger.get("estates", {})
    targets = [
        (key, rec) for key, rec in estates.items()
        if key.split("|", 1)[0] in counties
        and rec.get("estate_type") not in EXCLUDED_ESTATE_TYPES
        and not rec.get("property_lookup_checked")
    ]
    # Addresses the notice itself stated (foreign-PR notices) skip Land
    # Records but still get the SDAT current-owner check by street.
    sdat_only = [
        (key, rec) for key, rec in estates.items()
        if key.split("|", 1)[0] in counties and rec.get("property_needs_sdat_owner")
    ]
    for key, rec in sdat_only:
        county = key.split("|", 1)[0]
        try:
            sdat = landrec.lookup_sdat_by_address(county, rec["property_street"])
        except Exception as e:  # noqa: BLE001
            print(f"    WARNING: SDAT owner check failed for {key}: {e}")
            continue
        rec["property_needs_sdat_owner"] = False
        if sdat:
            first_tok = (rec.get("decedent_first_name", "") or "").split()[:1]
            last = rec.get("decedent_last_name", "")
            full_name = f"{rec.get('decedent_first_name', '')} {last}".strip()
            rec["property_sdat_owner"] = " / ".join(sdat.get("owner_names") or [])
            rec["property_principal_residence"] = sdat.get("principal_residence", "")
            rec["property_sdat_use"] = sdat.get("use", "")
            levels = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
            level = max((landrec.name_match_level(full_name, last, first_tok[0] if first_tok else "", n)
                         for n in sdat.get("owner_names") or []), key=lambda lv: levels[lv], default="LOW")
            # The notice itself is the source here, so a LOW owner match is
            # a flag (sold since? heir already on title?), not a rejection.
            rec["property_lookup_confidence"] = level
            rec["property_lookup_reason"] = "" if level != "LOW" else "SDAT owner does not match the decedent"
            print(f"    {key}: notice-stated {rec['property_street']} -> SDAT owner {rec['property_sdat_owner']} "
                  f"({rec['property_lookup_confidence']})")
        else:
            rec["property_lookup_confidence"] = "MEDIUM"
            rec["property_lookup_reason"] = "SDAT found no parcel at the notice-stated street (typo in the notice?)"
            print(f"    {key}: notice-stated {rec['property_street']} -- SDAT found no parcel")
        time.sleep(1.0)

    if not targets:
        print("  Address lookup: nothing new to resolve.")
        return
    print(f"  Address lookup: resolving {len(targets)} decedent(s) via Land Records + SDAT.")
    for key, rec in targets:
        county = key.split("|", 1)[0]
        last = rec.get("decedent_last_name", "")
        # decedent_first_name here is actually "First [Middle]" (see
        # split_last_first_name) -- Land Records stores first/middle in
        # SEPARATE fields with an exact-match default, so passing the whole
        # "JANE L." string as First Name silently matched nothing against
        # a DB value of plain "DEBORAH" (confirmed live). Use only the first
        # token.
        first = (rec.get("decedent_first_name", "") or "").split()[:1]
        first = first[0] if first else ""
        if not last:
            continue
        try:
            full_name = f"{rec.get('decedent_first_name', '')} {last}".strip()
            result = landrec.find_property_for_decedent(county, last, first, full_name=full_name)
        except Exception as e:  # noqa: BLE001
            print(f"    WARNING: address lookup failed for {key}: {e}")
            continue
        rec["property_lookup_checked"] = True
        if result.get("found"):
            rec["property_street"] = result["street"]
            rec["property_city"] = result["city"]
            rec["property_state"] = result["state"]
            rec["property_zip"] = result["zip"]
            rec["property_lookup_confidence"] = result["confidence"]
            rec["property_lookup_source"] = result.get("source", "")
            # SDAT's current owner of record is the review column that says
            # whether the decedent still owned it (Basem: SDAT check by
            # property address + street). Kept alongside, never merged into
            # the address itself.
            rec["property_sdat_owner"] = " / ".join(result.get("owner_names") or [])
            rec["property_principal_residence"] = result.get("principal_residence", "")
            rec["property_sdat_use"] = result.get("sdat_use", "")
            rec["property_deed_date"] = result.get("source_date", "")
            rec["property_deed_book_page"] = result.get("source_book_page", "")
            rec["property_lookup_reason"] = result.get("sdat_error", "")
            print(f"    {key}: {result['street']}, {result['city']} MD {result['zip']} "
                  f"({result['confidence']}, via {result.get('source')}; SDAT owner {rec['property_sdat_owner'] or '?'})")
        else:
            rec["property_lookup_confidence"] = "NOT_FOUND"
            rec["property_lookup_reason"] = result.get("reason", "")
            rec["property_sdat_owner"] = " / ".join(result.get("sdat_owner_names") or [])
            print(f"    {key}: not found -- {result.get('reason')}")
        time.sleep(1.5)


# ── CSV output ───────────────────────────────────────────────────────────

CSV_COLUMNS = [
    "Estate #", "Deceased First Name", "Deceased Last Name",
    "Property Address", "Property City", "Property State", "Property Zipcode",
    "County", "Attorney", "Attorney Address",
    "PR First Name", "PR Last Name", "Owner Address (PR)", "Owner City", "Owner State", "Owner Zipcode",
    "Published on", "Uploaded", "Tag",
    "PR 2 First Name", "PR 2 Last Name", "PR 2 Address", "PR 2 city", "PR 2 State", "PR 2 Zipcode",
    "PR 3 First Name", "PR 3 Last Name", "PR 3 Address", "PR 3 city", "PR 3 State", "PR 3 Zipcode",
    "Not Found", "Status",
]


NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}


def _split_pr_name(full_name: str) -> tuple[str, str]:
    """Split 'FIRST [MIDDLE] LAST [SUFFIX]' into (first+middle, last),
    keeping a trailing generational suffix (Jr./Sr./II/III/IV) attached to
    the last name rather than mistaken for it -- confirmed live on estate
    10001 ('RUSSELL C. DOE JR.'), the same class of name-suffix bug already
    documented elsewhere in this codebase (probate-property-finder's
    suffix-stripping tiers)."""
    parts = full_name.split()
    if len(parts) < 2:
        return full_name, ""
    suffix = ""
    if parts[-1].upper().rstrip(".") in NAME_SUFFIXES:
        suffix = parts[-1]
        parts = parts[:-1]
    if len(parts) < 2:
        return "", (parts[0] + (" " + suffix if suffix else "")) if parts else suffix
    last = parts[-1] + (" " + suffix if suffix else "")
    return " ".join(parts[:-1]), last


def write_csv(ledger: dict, counties: list[str], out_path: Path,
              only_keys: set[str] | None = None) -> int:
    """`only_keys`, when given, restricts output to those specific
    "county|estate_number" ledger keys -- normally the ones touched by THIS
    run. Without it, every estate the ledger has ever accumulated for these
    counties gets dumped every time, which turns each week's CSV into a
    growing superset of all history rather than that week's batch -- this
    was a real gap, not just a nicety for one-off requests, so callers
    should pass this in the normal case."""
    import csv as csv_module

    rows = []
    for key, record in ledger.get("estates", {}).items():
        county, estate_number = key.split("|", 1)
        if county not in counties:
            continue
        if only_keys is not None and key not in only_keys:
            continue
        if record.get("estate_type") in EXCLUDED_ESTATE_TYPES:
            continue
        prs = record.get("personal_reps") or []
        pr1 = prs[0] if len(prs) > 0 else {}
        pr2 = prs[1] if len(prs) > 1 else {}
        pr3 = prs[2] if len(prs) > 2 else {}
        pr1_first, pr1_last = _split_pr_name(pr1.get("name", "")) if pr1 else ("", "")
        pr2_first, pr2_last = _split_pr_name(pr2.get("name", "")) if pr2 else ("", "")
        pr3_first, pr3_last = _split_pr_name(pr3.get("name", "")) if pr3 else ("", "")
        attorney = record.get("attorney") or {}
        # "Not Found (Claude)" matches the convention already established in
        # the user's example CSV (row 4, Mary Katherine Prater) for exactly
        # this failure case -- a lookup was attempted and came back empty,
        # as opposed to blank meaning "never checked yet".
        if record.get("property_lookup_confidence") == "NOT_FOUND":
            property_address = "Not Found (Claude)"
        else:
            property_address = record.get("property_street", "")

        rows.append({
            "Estate #": estate_number,
            "Deceased First Name": record.get("decedent_first_name", ""),
            "Deceased Last Name": record.get("decedent_last_name", ""),
            "Property Address": property_address,
            "Property City": record.get("property_city", ""),
            "Property State": record.get("property_state", ""),
            "Property Zipcode": record.get("property_zip", ""),
            "County": county,
            "Attorney": attorney.get("name", ""),
            "Attorney Address": ", ".join(filter(None, [attorney.get("street"), attorney.get("city"),
                                                          attorney.get("state"), attorney.get("zip")])),
            "PR First Name": pr1_first,
            "PR Last Name": pr1_last,
            "Owner Address (PR)": pr1.get("street", ""),
            "Owner City": pr1.get("city", ""),
            "Owner State": pr1.get("state", ""),
            "Owner Zipcode": pr1.get("zip", ""),
            "Published on": record.get("legal_notice_published_on") or record.get("filing_date", ""),
            "Uploaded": "",
            "Tag": "",
            "PR 2 First Name": pr2_first,
            "PR 2 Last Name": pr2_last,
            "PR 2 Address": pr2.get("street", ""),
            "PR 2 city": pr2.get("city", ""),
            "PR 2 State": pr2.get("state", ""),
            "PR 2 Zipcode": pr2.get("zip", ""),
            "PR 3 First Name": pr3_first,
            "PR 3 Last Name": pr3_last,
            "PR 3 Address": pr3.get("street", ""),
            "PR 3 city": pr3.get("city", ""),
            "PR 3 State": pr3.get("state", ""),
            "PR 3 Zipcode": pr3.get("zip", ""),
            "Not Found": "",
            "Status": record.get("status", ""),
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Excel holds an exclusive lock on an open CSV -- write to a pending
    # name and swap rather than fail the whole run because someone has
    # yesterday's file open for review.
    pending_path = out_path.with_name(f"_PENDING_{out_path.name}")
    with open(pending_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv_module.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    try:
        pending_path.replace(out_path)
    except PermissionError:
        print(f"  WARNING: {out_path} is locked (likely open in Excel) -- "
              f"wrote to {pending_path} instead; close the file and re-run to finalize.")
        return len(rows)
    return len(rows)


# ── Doctor / discovery ───────────────────────────────────────────────────

def run_doctor():
    print("Fetching Estate Search form...")
    htmls = firecrawl_scrape_retry(ESTATE_SEARCH_URL, [{"type": "wait", "milliseconds": 2000}, {"type": "scrape"}])
    html = htmls[-1]
    soup = BeautifulSoup(html, "html.parser")
    county_sel = soup.select_one("#cboCountyId")
    live_counties = {opt.get_text(strip=True): opt.get("value") for opt in county_sel.find_all("option") if opt.get("value")}
    type_sel = soup.select_one("#cboType")
    live_types = [opt.get("value") for opt in type_sel.find_all("option") if opt.get("value")]
    latest = parse_latest_data_date(html)

    print(f"Latest data as of: {latest}")
    print(f"Estate Type options: {live_types}")
    # The site labels Baltimore COUNTY plain "Baltimore" (disambiguated from
    # "Baltimore City"); we key our map "Baltimore County" for CSV clarity,
    # so that one label mismatch is expected, not drift -- compare by value.
    live_by_value = {val: name for name, val in live_counties.items()}
    drift = {name: (cid, live_by_value.get(cid)) for name, cid in COUNTY_ID.items()
             if live_by_value.get(cid) not in (name, name.replace(" County", ""))}
    if drift:
        print(f"WARNING: county id drift detected (ours -> live label mismatch): {drift}")
    else:
        print("County id map matches live dropdown for all target counties.")
    missing_excl = EXCLUDED_ESTATE_TYPES - set(live_types)
    if missing_excl:
        print(f"WARNING: expected-exclude type(s) no longer on the live dropdown: {missing_excl}")
    else:
        print(f"Confirmed exclusion codes still present live: {sorted(EXCLUDED_ESTATE_TYPES)}")


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--doctor", action="store_true", help="verify live form fields/dropdowns, no pull")
    parser.add_argument("--counties", default=",".join(DEFAULT_COUNTIES),
                         help="comma-separated county names (must match COUNTY_ID keys)")
    parser.add_argument("--since", default="", help="MM/DD/YYYY lower bound; overrides the saved checkpoint")
    parser.add_argument("--until", default="", help="MM/DD/YYYY upper bound; defaults to the site's own "
                                                      "'Latest data as of' date. Use for a specific historical "
                                                      "window (e.g. --since 08/21/2026 --until 08/21/2026) -- "
                                                      "does NOT advance the saved checkpoint past this date even "
                                                      "with --commit, so a narrower one-off query can't create a "
                                                      "gap for the next normal forward run.")
    parser.add_argument("--date", default="", help="MM/DD/YYYY: pull exactly this ONE filing/publication date "
                                                     "(shorthand for --since D --until D). Per Basem 2026-08-27: "
                                                     "search an exact date, not a rolling window.")
    parser.add_argument("--dump-json", default="", help="also write this run's records (only the estates touched by "
                                                          "this run, with PRs, sources and property lookups) as JSON -- "
                                                          "the input for the review workbook builder")
    parser.add_argument("--seed-days", type=int, default=30, help="first-run lookback window if no checkpoint exists")
    parser.add_argument("--reconcile-lookback-days", type=int, default=45)
    parser.add_argument("--retry-ceiling-days", type=int, default=90)
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--no-details", action="store_true", help="skip per-estate detail-page fetch (Estate Search only)")
    parser.add_argument("--out", default="", help="output CSV path (default: output/Register of Wills <date>.csv)")
    parser.add_argument("--no-addresses", action="store_true",
                         help="skip the Land Records + SDAT property address lookup step")
    parser.add_argument("--commit", action="store_true", help="persist the ledger/checkpoint; without this, dry-run only")
    args = parser.parse_args()

    if args.doctor:
        run_doctor()
        return

    if args.date:
        if (args.since and args.since != args.date) or (args.until and args.until != args.date):
            raise SystemExit("--date cannot be combined with a different --since/--until")
        try:
            datetime.strptime(args.date, "%m/%d/%Y")
        except ValueError:
            raise SystemExit(f"--date must be MM/DD/YYYY, got {args.date!r}")
        args.since = args.until = args.date

    counties = [c.strip() for c in args.counties.split(",") if c.strip()]
    for c in counties:
        if c not in COUNTY_ID:
            raise SystemExit(f"Unknown county {c!r}; known: {sorted(COUNTY_ID)}")

    ledger = load_json(LEDGER_PATH, {"estates": {}})
    last_run = load_json(LAST_RUN_PATH, {})

    # Discover the site's own "Latest data as of" cutoff off Estate Search
    # first (a cheap, single fetch), then use that SAME date as the upper
    # bound for both sources -- per the user, never the run's own wall-clock
    # date, so a run kicked off before the site's daily update still asks
    # for exactly what the site says it has.
    probe_html = firecrawl_scrape_retry(
        ESTATE_SEARCH_URL, [{"type": "wait", "milliseconds": 2000}, {"type": "scrape"}])[-1]
    site_cutoff = parse_latest_data_date(probe_html)
    if not site_cutoff:
        raise SystemExit("Could not read 'Latest data as of' marker off Estate Search -- aborting rather than guessing a date.")
    print(f"Site's 'Latest data as of' date: {site_cutoff}")

    date_from = args.since or last_run.get("last_cutoff")
    if not date_from:
        seed_dt = datetime.strptime(site_cutoff, "%m/%d/%Y") - timedelta(days=args.seed_days)
        date_from = seed_dt.strftime("%m/%d/%Y")
        print(f"No checkpoint found -- seeding with --seed-days={args.seed_days} back to {date_from}")
    date_to = args.until or site_cutoff
    if args.until:
        print(f"Explicit --until {args.until} -- querying {date_from} to {date_to} "
              f"(checkpoint will NOT advance past this date).")

    all_estate_records = []
    all_notice_records = []
    for county in counties:
        print(f"\n=== {county} ===")
        estate_records, county_latest = pull_estate_search(
            county, date_from, date_to, max_pages=args.max_pages, fetch_details=not args.no_details)
        all_estate_records.extend(estate_records)
        notice_records = pull_legal_notice_search(county, date_from, date_to, max_pages=args.max_pages)
        all_notice_records.extend(notice_records)

    merged = merge_sources(all_estate_records, all_notice_records)
    print(f"\nMerged {len(merged)} unique estate(s) across {len(counties)} counties (post SE/SJ/MV exclusion).")
    only_keys = {f"{c}|{n}" for (c, n) in merged.keys()}

    changes = reconcile_ledger(ledger, merged, run_date=site_cutoff)
    print(f"Ledger: {len(changes['added'])} added, {len(changes['changed'])} changed, "
          f"{len(changes['unchanged'])} unchanged.")

    run_reconciliation_pass(ledger, counties, run_date=site_cutoff,
                             lookback_days=args.reconcile_lookback_days,
                             retry_ceiling_days=args.retry_ceiling_days)

    if not args.no_addresses:
        fill_addresses(ledger, counties)

    out_path = Path(args.out) if args.out else OUTPUT_DIR / f"Register of Wills {date_to.replace('/', '-')}.csv"
    # Scope the CSV to just what THIS run touched, not the whole
    # accumulated ledger -- otherwise every weekly deliverable would grow
    # into a superset of all history rather than that period's batch.
    row_count = write_csv(ledger, counties, out_path, only_keys=only_keys)
    print(f"\nWrote {row_count} row(s) to {out_path}")

    if args.dump_json:
        dump = {
            "run": {"counties": counties, "date_from": date_from, "date_to": date_to,
                    "site_cutoff": site_cutoff, "run_at": datetime.now().isoformat(),
                    "committed": bool(args.commit)},
            "records": [dict(ledger["estates"][k], ledger_key=k)
                        for k in sorted(only_keys) if k in ledger["estates"]],
        }
        save_json(Path(args.dump_json), dump)
        print(f"Dumped {len(dump['records'])} record(s) to {args.dump_json}")

    if args.commit:
        save_json(LEDGER_PATH, ledger)
        # Advance the checkpoint only to the date actually queried (date_to),
        # never blindly to site_cutoff -- an explicit --until narrower than
        # the site's latest data would otherwise let the checkpoint jump
        # PAST dates that were never actually pulled, silently skipping them
        # on the next normal forward run. And never move it BACKWARDS either:
        # a one-off --date on an older day must not make the next forward run
        # re-pull weeks it already covered.
        prev = last_run.get("last_cutoff")
        if prev and datetime.strptime(prev, "%m/%d/%Y") > datetime.strptime(date_to, "%m/%d/%Y"):
            print(f"Committed ledger; checkpoint left at {prev} (this run's {date_to} is older).")
        else:
            save_json(LAST_RUN_PATH, {"last_cutoff": date_to, "run_at": datetime.now().isoformat()})
            print(f"Committed ledger + checkpoint (next run will pull forward from {date_to}).")
    else:
        print("Dry run (--commit not passed) -- ledger/checkpoint NOT saved.")


if __name__ == "__main__":
    main()
