"""Resolve a decedent's property address via Maryland Land Records + SDAT.

This replaces the originally-planned Bright MLS Matrix property lookup.
Bright MLS was abandoned live 2026-08-25 after two real blockers: Matrix
requires SMS/email MFA on a fresh session (the user wasn't able to relay a
code at the time), and reusing the user's actual logged-in Chrome profile to
skip MFA hit a hard Chrome restriction -- "DevTools remote debugging
requires a non-default data directory" -- Chrome refuses CDP automation
against the literal default profile dir for security reasons, so a
copy-the-profile workaround would have been needed even to get that far.
Per the user: use Maryland's land records instead.

THE BRIDGE, verified live end-to-end 2026-08-25 (real records, not a guess):
  1. Maryland Land Records (landrec.msa.maryland.gov) lets you search the
     deed index by GRANTEE name, statewide, with real match-mode flexibility
     (Is/Begins/Ends/Contains/Fuzzy/Soundex) -- much better than a
     "starts with"-only search. The results grid gives Date, Grantor/
     Grantee, Instrument Type, Book/Page, and a free-text "Remarks" column
     -- but NO address directly. Older instruments (pre-2000s) have empty
     Remarks and are scanned raster images with zero machine-readable text.
  2. RECENT deeds (2020s, confirmed on a real 2024 Calvert County deed) are
     genuine text-layer PDFs, not scans -- Firecrawl's markdown extraction
     reads them directly, no OCR needed. And modern deeds' Remarks column
     encodes the SDAT tax account identifier: a real example's Remarks cell
     read "SAMPLE MANOR        0   0   2107481 8590" (subdivision, lot,
     block, then two numbers) and the number "2107481" decoded to SDAT
     District "02" + Account "107481" -- the leading zero of the
     concatenated district+account gets silently dropped when the site
     renders it as a plain integer, so a 7-digit Remarks number needs
     re-padding to 8 digits before splitting 2+6. Confirmed by opening the
     actual deed PDF: its own cover table explicitly listed "02-107481" for
     the exact parcel matching that Remarks value.
  3. Feeding that District+Account into SDAT's Real Property Data Search
     (sdat.dat.maryland.gov, search type "PROPERTY ACCOUNT IDENTIFIER")
     returned the EXACT match: Premises Address "8590 OAK HILL DR, OWINGS
     20736", Owner Name "SAMPLE JOHN A TRUSTEE / SAMPLE MARY JEAN
     TRUSTEE" -- confirmed correct against the deed's own text.

This means: Remarks-with-an-account-number is only reliably present on DEED
(and similar conveyance) instruments from roughly the 2000s onward, not on
RELEASE/MORTGAGE/SATISFACTION entries (confirmed empty in testing) and not
on pre-2000s scanned deeds (confirmed empty). A decedent whose only real
property was acquired decades ago and never re-deeded may not resolve this
way -- that's a genuine data-availability gap, not a bug, and such records
should fall through to the existing probate-property-finder skill's
assessor/deed/aggregator tiers rather than being marked "Not Found" from
this path alone.

UPGRADE, also verified live 2026-08-25: the Remarks column turned out to be
an unreliable middleman. A second real deed (a 2020 estate-distribution
deed, Jane L. Sample receiving 456 Example Court from the Estate of
Mary Louise Roe) had EMPTY Remarks despite being a perfectly normal,
fully text-readable modern DEED -- yet its own PDF text stated outright
"Said parcel has the address of: 456 Example Court, Owings, Maryland
20736-3159" AND "TAX ID NO: 03-000000" in the document header. So
`find_property_for_decedent` now reads the deed's own PDF text FIRST (via
`resolve_pdf_url` + `fetch_deed_text` + `parse_deed_text`) and prefers its
plain-English address statement when present (HIGH confidence, no
inference involved) -- falling back to the deed's stated Tax ID (cross-
checked against SDAT) or, only if the PDF has neither, the original
Remarks-decoding shortcut. See `find_property_for_decedent`'s own
docstring for the full priority order.

THREE DIFFERENT COUNTY CODE SCHEMES across the sites this pipeline touches
-- Estate Search/Legal Notice Search use one numeric map (see
md_register_of_wills_pull.COUNTY_ID), Land Records uses 2-letter codes, and
SDAT uses ITS OWN numeric map in a different order. Do not assume any two
of these line up; LANDREC_COUNTY_ID and SDAT_COUNTY_ID below are each
verified independently against their own live dropdown.

Login note: MD_LANDREC_EMAIL/MD_LANDREC_PASSWORD logged in cleanly with no
emailed access-code challenge in testing -- but the login page DOES have a
usercode step wired up (#body_tbUsercode), so a future run may hit it on a
new/flagged session. This module treats that as a hard failure (raises
rather than silently proceeding) since there's no way to relay an emailed
code without a human in the loop.

Usage:
    python src/scripts/md_land_records_lookup.py --doctor
    python src/scripts/md_land_records_lookup.py --county Calvert --last-name Sample --first-name Jane
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"

LANDREC_LOGIN_URL = "https://landrec.msa.maryland.gov/Pages/Login.aspx"
SDAT_URL = "https://sdat.dat.maryland.gov/RealProperty/Pages/default.aspx"

# Verified live 2026-08-25 against Land Records' #body_ddlbarcounties.
LANDREC_COUNTY_ID = {
    "Allegany": "AL", "Anne Arundel": "AA", "Baltimore City": "BC", "Baltimore County": "BA",
    "Calvert": "CV", "Caroline": "CA", "Carroll": "CR", "Cecil": "CE", "Charles": "CH",
    "Dorchester": "DO", "Frederick": "FR", "Garrett": "GA", "Harford": "HA", "Howard": "HO",
    "Kent": "KE", "Montgomery": "MO", "Prince George's": "PG", "Queen Anne's": "QA",
    "Somerset": "SO", "St. Mary's": "SM", "Talbot": "TA", "Washington": "WA",
    "Wicomico": "WI", "Worcester": "WO",
}

# Verified live 2026-08-25 against SDAT's ddlCounty -- a DIFFERENT numeric
# scheme from Estate Search's (e.g. here Calvert=05, there Calvert=4).
SDAT_COUNTY_ID = {
    "Allegany": "01", "Anne Arundel": "02", "Baltimore City": "03", "Baltimore County": "04",
    "Calvert": "05", "Caroline": "06", "Carroll": "07", "Cecil": "08", "Charles": "09",
    "Dorchester": "10", "Frederick": "11", "Garrett": "12", "Harford": "13", "Howard": "14",
    "Kent": "15", "Montgomery": "16", "Prince George's": "17", "Queen Anne's": "18",
    "St. Mary's": "19", "Somerset": "20", "Talbot": "21", "Washington": "22",
    "Wicomico": "23", "Worcester": "24",
}

# The 2026-09-01 footprint: only these 3 MD counties in scope by default
# (retired counties still resolve when named explicitly).
DEFAULT_COUNTIES = [
    "Montgomery", "Anne Arundel", "Frederick",
]

# Only these instrument types carry a useful Remarks/account number in
# practice (confirmed live: RELEASE/MORTGAGE/SATISFACTION entries had empty
# Remarks even on recent dates) -- conveyances are what we actually want
# anyway, since that's what tells us the person RECEIVED real property.
DEED_LIKE_INSTRUMENTS = {"DEED", "DEED OF TRUST", "DEED AND PARTIAL RELEASE"}
MAX_DEED_CANDIDATES = 5  # newest-first; see find_property_for_decedent


def load_credentials() -> dict:
    env = dotenv_values(str(ENV_PATH))
    firecrawl_key = env.get("FIRECRAWL_API_KEY")
    email = env.get("MD_LANDREC_EMAIL")
    password = env.get("MD_LANDREC_PASSWORD")
    missing = [n for n, v in (("FIRECRAWL_API_KEY", firecrawl_key),
                               ("MD_LANDREC_EMAIL", email),
                               ("MD_LANDREC_PASSWORD", password)) if not v]
    if missing:
        raise RuntimeError(f"Missing from .env: {', '.join(missing)}")
    return {"firecrawl_key": firecrawl_key, "email": email, "password": password}


def firecrawl_scrape(url: str, actions: list, timeout: int = 120) -> str:
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
    return scrapes[-1].get("html", "") if scrapes else data.get("html", "")


def firecrawl_scrape_retry(url: str, actions: list, timeout: int = 120,
                            min_len: int = 1000, attempts: int = 4) -> str:
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            html = firecrawl_scrape(url, actions, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * attempt)
            continue
        if len(html) >= min_len:
            return html
        last_err = RuntimeError(f"attempt {attempt}: page too small ({len(html)} chars)")
        time.sleep(2 * attempt)
    raise RuntimeError(f"firecrawl_scrape_retry exhausted {attempts} attempts against {url}: {last_err}")


def _set_select_js(selector: str, value: str) -> str:
    return f"""
        (function() {{
            var el = document.querySelector('{selector}');
            if (!el) return;
            var setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
            setter.call(el, '{value}');
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
        }})();
    """


def _set_text_js(selector: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"""
        (function() {{
            var el = document.querySelector('{selector}');
            if (!el) return;
            var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(el, '{escaped}');
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
        }})();
    """


def _check_radio_js(selector: str) -> str:
    return f"""
        (function() {{
            var el = document.querySelector('{selector}');
            if (el) {{ el.checked = true; el.dispatchEvent(new Event('click', {{bubbles:true}})); el.dispatchEvent(new Event('change', {{bubbles:true}})); }}
        }})();
    """


def _login_actions(creds: dict) -> list:
    return [
        {"type": "wait", "milliseconds": 2000},
        {"type": "click", "selector": "#body_tbUsername"},
        {"type": "write", "text": creds["email"]},
        {"type": "click", "selector": "#body_tbPassword"},
        {"type": "write", "text": creds["password"]},
        {"type": "click", "selector": "#body_btnSubmit"},
        {"type": "wait", "milliseconds": 4000},
    ]


def _assert_logged_in(html: str) -> None:
    """The site's own usercode (emailed access code) MFA step has a real
    live selector (#body_tbUsercode) even though it didn't trigger in
    testing. Fail loudly rather than silently proceeding on a half-completed
    login -- there is no way to relay an emailed code without a human."""
    if "tbUsercode" in html and "ddlbarcounties" not in html:
        raise RuntimeError(
            "Land Records login appears to be stuck on the emailed access-code (MFA) step -- "
            "a human needs to relay the code from the account's email before this can proceed."
        )


# ── Land Records grantee search ─────────────────────────────────────────

def search_grantee(county: str, last_name: str, first_name: str = "",
                    match_mode: str = "Begins", middle_name: str = "") -> list[dict]:
    """Search Land Records for `last_name` (+ optional first_name and
    middle_name) AS GRANTEE in `county`, sorted newest-first (Date
    Descending) so the most recent -- most likely current -- acquisition
    surfaces first.

    `middle_name` (Basem 2026-09-04: "use middle name in land records") --
    without it a common first+last returns every namesake's deeds and the
    MAX_DEED_CANDIDATES cap can eat the right person's conveyances. Pass the
    INITIAL, not the full middle: with "Begins" mode a query of "E" matches
    index values "E" and "EDWARD", while "EDWARD" misses an index that
    stored the bare initial. Field ids #body_tbMiddleName /
    #body_ddlMiddleName verified on the live form 2026-09-04. Callers
    should retry WITHOUT the middle on zero rows -- older instruments are
    sometimes indexed without one."""
    creds = load_credentials()
    county_id = LANDREC_COUNTY_ID[county]
    actions = _login_actions(creds) + [
        {"type": "executeJavascript", "script": _set_select_js("#body_ddlbarcounties", county_id)},
        {"type": "wait", "milliseconds": 4000},
        {"type": "executeJavascript", "script": _set_select_js("#body_ddlLastName", match_mode)},
        {"type": "click", "selector": "#body_tbLastName"},
        {"type": "write", "text": last_name},
    ]
    if first_name:
        actions += [
            # Match mode defaults to "Is" (exact) -- "Begins" here too so a
            # bare first name still matches even if the caller's name
            # splitting left a trailing middle initial off cleanly but
            # capitalization/whitespace differs from the DB value.
            {"type": "executeJavascript", "script": _set_select_js("#body_ddlFirstName", match_mode)},
            {"type": "click", "selector": "#body_tbFirstName"},
            {"type": "write", "text": first_name},
        ]
    if middle_name:
        actions += [
            {"type": "executeJavascript", "script": _set_select_js("#body_ddlMiddleName", match_mode)},
            {"type": "click", "selector": "#body_tbMiddleName"},
            {"type": "write", "text": middle_name},
        ]
    actions += [
        {"type": "executeJavascript", "script": _check_radio_js("#body_rdlParty_2")},  # Grantee
        {
            "type": "executeJavascript",
            "script": _set_select_js("#body_SortByID", "2"),  # Date Descending
        },
        {"type": "click", "selector": "#body_btnSubmit"},
        {"type": "wait", "milliseconds": 5000},
        {"type": "scrape"},
    ]
    html = firecrawl_scrape_retry(LANDREC_LOGIN_URL, actions, timeout=120)
    _assert_logged_in(html)
    return parse_grantee_results(html)


def parse_grantee_results(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for row in soup.select("tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
            continue
        date_cell = cells[1]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_cell.get_text(strip=True)):
            continue
        grant_text = cells[2].get_text(" ", strip=True)
        instrument = cells[3].get_text(strip=True)
        book_page = cells[4].get_text(" ", strip=True)
        remarks = cells[5].get_text(" ", strip=True) if len(cells) > 5 else ""
        # cells[3] carries the "view=I" (instrument info) link, cells[4] the
        # "view=B" (raw page image) link -- we want view=I, since that page
        # embeds the actual PDF's real URL (see resolve_pdf_url below).
        info_link = cells[3].find("a")
        results.append({
            "date": date_cell.get_text(strip=True),
            "party": grant_text,
            "instrument_type": instrument,
            "book_page": book_page,
            "remarks": remarks,
            "info_viewer_url": info_link.get("href", "") if info_link else "",
        })
    return results


# ── Remarks -> SDAT District/Account decoding ───────────────────────────

REMARKS_TRAILING_NUMS_RE = re.compile(r"(\d{6,8})\s+(\d{1,6})\s*$")


def decode_remarks_account(remarks: str) -> tuple[str, str] | None:
    """Decode a Remarks cell like 'SAMPLE MANOR        0   0   2107481 8590'
    into an SDAT (district, account) pair. Confirmed live: the site drops
    the leading zero when it renders the concatenated district+account as a
    plain integer, so a 7-digit number needs re-padding to 8 before the 2+6
    split. Returns None if the Remarks text doesn't match this shape at all
    (common on RELEASE/MORTGAGE-type instruments, which carry no account)."""
    m = REMARKS_TRAILING_NUMS_RE.search(remarks)
    if not m:
        return None
    acct_raw = m.group(1)
    if len(acct_raw) == 7:
        acct_raw = "0" + acct_raw
    if len(acct_raw) != 8:
        return None
    return acct_raw[:2], acct_raw[2:]


# ── Deed PDF text (primary address source) ──────────────────────────────

PDF_ORIGINAL_URL_RE = re.compile(r"original-url=[\"']([^\"']+)")
# The ". " terminator must NOT fire on a directional abbreviation inside the
# address ("614 W. Patrick Street" was truncated to "614 W" on a real
# Frederick deed, 2026-09-03) -- hence the lookbehind for a lone N/S/E/W.
DEED_ADDRESS_RE = re.compile(
    r"(?:has|having|bearing)\s+the\s+(?:street\s+|property\s+|mailing\s+)?address\s+of:?\s*(.+?)(?:(?<!\s[NSEW])\.\s|\n|$)", re.I)
# Second, LABELED form. MD deeds that don't use the "has the address of"
# sentence usually still state the property in one of these labeled ways:
#   "...the improvements thereon being known as No. 8590 Oak Hill Drive,
#    Owings, Maryland 20736"      /  "Property Address: 123 Main St, ..."
#   "premises known as 456 Example Court, Owings, MD 20736"
# The capture is anchored to START with a house number and END with a
# Maryland/MD + ZIP so "known as Lot 5, Block B" style legal descriptions
# can't match. Deliberately NOT a bare "anything that looks like an
# address anywhere in the text" regex -- deeds also print the grantor's and
# the preparer's mailing addresses, and an unlabeled match would pick those
# up as the property.
DEED_KNOWN_AS_RE = re.compile(
    r"(?:known\s+as|known\s+and\s+designated\s+as|property\s+address(?:\s+is)?|premises\s+address|"
    r"premises\s+known\s+as|located\s+at)\s*:?\s*(?:No\.?\s*)?"
    r"(\d{1,6}[A-Z]?\s+[A-Za-z0-9 .'#-]{3,60}?,\s*[A-Za-z .'-]{2,40}?,?\s*(?:Maryland|MD)\.?\s*\d{5}(?:-\d{4})?)",
    re.I)
DEED_TAX_ID_RE = re.compile(
    r"(?:PROPERTY\s+)?TAX\s+I\.?D\.?\s*(?:NO\.?|#)\s*:?\s*([\d-]{6,12})", re.I)
# Was `TAX\s+ID\s+NO\.?:?\s*(...)` -- confirmed live 2026-08-27 (Basem) this
# missed a real deed's own label, "Property Tax I.D.# 03-02366800" (a
# different header than the "TAX ID NO: 03-000000" the regex was written
# against). The narrower phrasing wasn't wrong, just not the only one MD
# deeds use; caught because JOHN T DOE's deed carried this tax id in
# plain text the whole time and find_property_for_decedent() still came
# back "found: False" -- a live 0-for-5 address-lookup run before this fix.


def resolve_pdf_url(info_viewer_url: str, creds: dict) -> str:
    """The Land Records 'view=I' page embeds the actual document PDF's real
    URL in a hidden <embed original-url="..."> attribute -- there is no
    predictable filename pattern to construct this directly (the numeric
    folder id in the path did not obviously vary with county/book/page in
    testing, but relying on that would be guessing rather than verifying)."""
    actions = _login_actions(creds) + [
        {"type": "executeJavascript", "script": f"window.location.href = '{info_viewer_url}';"},
        {"type": "wait", "milliseconds": 5000},
        {"type": "scrape"},
    ]
    html = firecrawl_scrape_retry(LANDREC_LOGIN_URL, actions, timeout=120)
    _assert_logged_in(html)
    m = PDF_ORIGINAL_URL_RE.search(html)
    return m.group(1) if m else ""


def fetch_deed_text(pdf_url: str) -> str:
    """Deed PDFs are reachable unauthenticated once you have the exact URL
    (confirmed live) and Firecrawl auto-extracts their text layer as
    markdown -- recent (2000s+) instruments are real text-layer PDFs, not
    scans, so no OCR step is needed here. Pre-2000s instruments ARE raster
    scans and this will come back with little/no usable text; callers
    should treat an empty/short result as "not machine-readable" rather
    than an error."""
    creds = load_credentials()
    resp = requests.post(
        FIRECRAWL_URL,
        headers={"Authorization": f"Bearer {creds['firecrawl_key']}", "Content-Type": "application/json"},
        json={"url": pdf_url, "formats": ["markdown"]},
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json().get("data", {}).get("markdown", "")


# Bare form, no city/state/zip -- the most common MD phrasing turned out to
# be "The improvements thereon being known as No. 123 Example Drive." (real
# 1983 Anne Arundel deed, 2026-08-28), which the labeled form above cannot
# match because nothing follows the street. The county is known and the
# SDAT street search fills city/zip, so number + street is enough. The
# street must END in a recognised suffix so "known as Lot 14, Block E" and
# "known as Parcel A" are not captured.
_SUFFIX_ALT = (r"Road|Rd|Drive|Dr|Street|St|Court|Ct|Lane|Ln|Avenue|Ave|Way|Circle|Cir|Place|Pl|"
               r"Boulevard|Blvd|Terrace|Ter|Highway|Hwy|Parkway|Pkwy|Trail|Trl|Loop|Run|Path|Square|Sq|"
               r"Point|Pt|Cove|Walk|Row|Pike|Alley|Aly|Plaza|Plz|Glen|Manor|Ridge|View|Crossing|Xing")
DEED_KNOWN_AS_BARE_RE = re.compile(
    r"(?:known\s+as|known\s+and\s+designated\s+as|located\s+at|premises\s+known\s+as)\s*:?\s*(?:No\.?\s*)?"
    r"(\d{1,6}[A-Z]?\s+(?:[A-Za-z][A-Za-z.'-]*\s+){0,4}(?:" + _SUFFIX_ALT + r")\.?)(?=[\s,.;)]|$)",
    re.I)


def _plausible_street(statement: str) -> bool:
    """A deed address statement's street part must start with a house NUMBER
    and have a real street NAME (>= 3 letters after the number). '614 W'
    (a truncated '614 W. Patrick Street') fails; '614 Elm' passes; 'Lot 6'
    (a legal description that slipped past the regexes -- shipped live
    2026-09-04 as 'Lot 6, Reich's Ford Road') fails."""
    street_part = statement.split(",")[0].strip()
    toks = street_part.split()
    if not toks or not toks[0][:1].isdigit():
        return False
    return sum(len(re.sub(r"[^A-Za-z]", "", t)) for t in toks[1:]) >= 3


def parse_deed_text(text: str) -> dict:
    """Pull the plain-English 'Said parcel has the address of: ...'
    statement and/or the 'TAX ID NO:' header directly out of the deed's own
    text -- confirmed live on a real 2020 estate-distribution deed
    ('Said parcel has the address of: 456 Example Court, Owings,
    Maryland 20736-3159.' + 'TAX ID NO: 03-000000' in the same document).
    This is MORE reliable than decoding the search grid's Remarks column,
    which is not always populated (confirmed: present on a multi-parcel
    deed, absent on this single-lot one) even though the underlying PDF had
    both pieces of information all along."""
    addr_m = DEED_ADDRESS_RE.search(text) or DEED_KNOWN_AS_RE.search(text) or DEED_KNOWN_AS_BARE_RE.search(text)
    tax_id_m = DEED_TAX_ID_RE.search(text)
    address_statement = " ".join(addr_m.group(1).split()).rstrip(".,") if addr_m else ""
    if address_statement and not _plausible_street(address_statement):
        # A truncated capture ("614 W" -- PDF line wrap or an abbreviation
        # ending the match early) is a PARSE FAILURE, not an address: better
        # to keep walking older deeds / report not-found than ship a street
        # SDAT can never confirm. Real Frederick estate 54826, 2026-09-03.
        address_statement = ""
    tax_id = tax_id_m.group(1) if tax_id_m else ""
    district, account = ("", "")
    if tax_id and "-" in tax_id:
        district, account = tax_id.split("-", 1)
    return {"address_statement": address_statement, "tax_id": tax_id,
            "district": district, "account": account}


def split_deed_address_statement(statement: str) -> dict:
    """'456 Example Court, Owings, Maryland 20736-3159' -> street/city/zip.
    Unlike SDAT's Premises Address, this form DOES have comma delimiters and
    a spelled-out state name, so a straightforward comma split works (no
    street-suffix heuristic needed here)."""
    statement = statement.strip().rstrip(".")
    parts = [p.strip() for p in statement.split(",")]
    zip_m = re.search(r"(\d{5})(?:-\d{4})?\s*$", statement)
    zip5 = zip_m.group(1) if zip_m else ""
    street = parts[0] if parts else ""
    city = parts[1] if len(parts) > 1 else ""
    # "Owings, Maryland 20736" -> the state+zip is its own comma part, not a
    # city; a bare "123 Example Drive" has neither and SDAT supplies both.
    if re.match(r"^(?:Maryland|MD)\b", city, re.I):
        city = ""
    return {"street": street, "city": city, "zip": zip5}


# ── SDAT lookup ──────────────────────────────────────────────────────────

COUNTY_SEL = "#cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucSearchType_ddlCounty"
TYPE_SEL = "#cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucSearchType_ddlSearchType"
CONTINUE_SEL = "#cphMainContentArea_ucSearchType_wzrdRealPropertySearch_StartNavigationTemplateContainerID_btnContinue"
DISTRICT_SEL = "#cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucEnterData_txtDistrict"
ACCOUNT_SEL = "#cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucEnterData_txtAccountIdentifier"
NEXT_SEL = "#cphMainContentArea_ucSearchType_wzrdRealPropertySearch_StepNavigationTemplateContainerID_btnStepNextButton"


def lookup_sdat(county: str, district: str, account: str) -> dict | None:
    county_id = SDAT_COUNTY_ID[county]
    actions = [
        {"type": "wait", "milliseconds": 2000},
        {"type": "executeJavascript", "script": _set_select_js(COUNTY_SEL, county_id)},
        {"type": "wait", "milliseconds": 500},
        {"type": "executeJavascript", "script": _set_select_js(TYPE_SEL, "02")},  # Property Account Identifier
        {"type": "wait", "milliseconds": 500},
        {"type": "click", "selector": CONTINUE_SEL},
        {"type": "wait", "milliseconds": 3000},
        # WAS click+write -- confirmed live 2026-08-27 (Basem) that pair
        # never actually populated these two ASP.NET text inputs: the next
        # page's own validation summary showed "District is required" /
        # "Account Identifier is required" even though both actions had
        # already run. Same native-setter + input/change pattern already
        # proven for every other text field in this codebase fixes it.
        {"type": "executeJavascript", "script": _set_text_js(DISTRICT_SEL, district)},
        {"type": "executeJavascript", "script": _set_text_js(ACCOUNT_SEL, account)},
        {"type": "wait", "milliseconds": 500},
        {"type": "click", "selector": NEXT_SEL},
        {"type": "wait", "milliseconds": 3000},
        {"type": "scrape"},
    ]
    html = firecrawl_scrape_retry(SDAT_URL, actions, timeout=90)
    return parse_sdat_result(html)


STREET_NUMBER_SEL = DISTRICT_SEL.replace("txtDistrict", "txtStreenNumber")  # sic -- SDAT's own id typo
STREET_NAME_SEL = DISTRICT_SEL.replace("txtDistrict", "txtStreetName")

# Directional / unit tokens SDAT's street-name box does not want.
_STREET_DIRECTIONALS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW", "NORTH", "SOUTH", "EAST", "WEST"}
_UNIT_TOKENS = {"APT", "UNIT", "STE", "SUITE", "#", "BLDG", "LOT"}


def split_street_for_sdat(street: str) -> tuple[str, str]:
    """'8590 Oak Hill Dr' -> ('8590', 'OAK HILL'). SDAT's STREET ADDRESS
    search wants the house number in one box and the street NAME WITHOUT
    its suffix in the other -- confirmed live 2026-08-28 on Calvert:
    '8590' + 'OAK HILL' returns the parcel, '8590' + 'OAK HILL DR' returns
    nothing at all (a blank result, not an error). Unit designators are
    dropped too; a leading directional is kept only when it's the whole
    name would otherwise be empty."""
    tokens = street.replace(",", " ").split()
    if not tokens:
        return "", ""
    number = ""
    m = re.match(r"^(\d+)[A-Za-z]?$", tokens[0])
    if m:
        number = m.group(1)
        tokens = tokens[1:]
    # cut at the first unit designator
    cut = len(tokens)
    for i, tok in enumerate(tokens):
        if tok.upper().rstrip(".") in _UNIT_TOKENS or tok.startswith("#"):
            cut = i
            break
    tokens = tokens[:cut]
    if tokens and tokens[-1].upper().rstrip(".") in STREET_SUFFIXES:
        tokens = tokens[:-1]
    if len(tokens) > 1 and tokens[0].upper().rstrip(".") in _STREET_DIRECTIONALS:
        tokens = tokens[1:]
    return number, " ".join(t.upper().rstrip(".") for t in tokens)


def _click_matching_result_row_js(street_number: str, street_name: str) -> str:
    """If the street search landed on a multi-match grid (several parcels
    on the same number/name, e.g. units), click the first row that carries
    both the number and the name. A no-op when the page is already a single
    parcel's detail (it has 'Owner Name' on it)."""
    num = street_number.replace("'", "")
    name = street_name.replace("'", "").upper()
    return f"""
        (function() {{
            if ((document.body.innerText || '').indexOf('Owner Name') >= 0) return;
            var rows = document.querySelectorAll('table tr');
            for (var i = 0; i < rows.length; i++) {{
                var t = (rows[i].innerText || '').toUpperCase();
                if (t.indexOf('{num}') >= 0 && t.indexOf('{name}') >= 0) {{
                    var a = rows[i].querySelector('a');
                    if (a) {{ a.click(); return; }}
                }}
            }}
        }})();
    """


def lookup_sdat_by_address(county: str, street: str) -> dict | None:
    """SDAT Real Property search, type 01 STREET ADDRESS (per Basem
    2026-08-27: 'use the property address and then street, don't use tax
    account ID'). Returns the parsed parcel (owner names, premises, mailing,
    legal description, district/account) or None when nothing matched.

    Verified live 2026-08-28 (Calvert): '456' + 'EXAMPLE' -> SAMPLE
    DEBORAH L, 456 EXAMPLE CT OWINGS 20736; '8590' + 'OAK HILL' -> the
    SAMPLE trustees' parcel. Both landed directly on the detail page; the
    multi-match grid click below is a guard for the unit/duplicate case and
    is a no-op on a direct hit."""
    county_id = SDAT_COUNTY_ID[county]
    number, name = split_street_for_sdat(street)
    if not (number and name):
        return None
    actions = [
        {"type": "wait", "milliseconds": 2000},
        {"type": "executeJavascript", "script": _set_select_js(COUNTY_SEL, county_id)},
        {"type": "wait", "milliseconds": 500},
        {"type": "executeJavascript", "script": _set_select_js(TYPE_SEL, "01")},  # STREET ADDRESS
        {"type": "wait", "milliseconds": 500},
        {"type": "click", "selector": CONTINUE_SEL},
        {"type": "wait", "milliseconds": 3000},
        {"type": "executeJavascript", "script": _set_text_js(STREET_NUMBER_SEL, number) + _set_text_js(STREET_NAME_SEL, name)},
        {"type": "wait", "milliseconds": 500},
        {"type": "click", "selector": NEXT_SEL},
        {"type": "wait", "milliseconds": 4000},
        {"type": "executeJavascript", "script": _click_matching_result_row_js(number, name)},
        {"type": "wait", "milliseconds": 3500},
        {"type": "scrape"},
    ]
    html = firecrawl_scrape_retry(SDAT_URL, actions, timeout=120)
    parsed = parse_sdat_result(html)
    if parsed:
        parsed["sdat_query"] = {"street_number": number, "street_name": name}
    return parsed


def _text_after_label(soup: BeautifulSoup, label: str) -> str:
    el = soup.find(string=re.compile(re.escape(label)))
    if not el:
        return ""
    nxt = el.parent.find_next(string=True)
    return nxt.strip() if nxt else ""


def parse_sdat_result(html: str) -> dict | None:
    if "is required" in html and "Owner Name" not in html:
        return None  # validation error -- district/account rejected
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    if "Owner Name" not in text:
        return None

    def grab(pattern: str) -> str:
        # DOTALL: the labeled values below span multiple lines in the
        # extracted text (e.g. two owner names on separate lines before
        # "Use:"), so '.' must match '\n' or the capture silently comes
        # back empty -- confirmed live, this was the actual bug the first
        # time this parser ran against a real result.
        m = re.search(pattern, text, re.DOTALL)
        return m.group(1).strip() if m else ""

    owner_block = grab(r"Owner Name:\s*\n(.+?)\nUse:")
    premises = grab(r"Premises Address:\s*\n(.+?)\n(?:Legal Description|Location)")
    mailing = grab(r"Mailing Address:\s*\n(.+?)\nDeed Reference")
    legal_desc = grab(r"Legal Description:\s*\n(.+?)\nMap:")
    district = grab(r"District -\s*\n(\d+)")
    account = grab(r"Account Identifier -\s*\n([\w-]+)")
    # "Use: | Principal Residence: | RESIDENTIAL | YES" -- the two values
    # follow the two labels, in that order.
    use_pr = grab(r"Principal Residence:\s*\n([A-Z ]+)\n(?:YES|NO)")
    principal_residence = grab(r"Principal Residence:\s*\n[A-Z ]+\n(YES|NO)")

    return {
        "owner_names": [n.strip() for n in owner_block.split("\n") if n.strip()],
        "premises_address": " ".join(premises.split()),
        "mailing_address": " ".join(mailing.split()),
        "legal_description": " ".join(legal_desc.split()),
        "district": district,
        "account": account,
        "use": use_pr.strip(),
        "principal_residence": principal_residence,
    }


# Reuse the street-suffix split from md_register_of_wills_pull rather than
# a plain greedy/non-greedy regex -- confirmed live to be ambiguous the same
# way there: '8590  OAK HILL DR OWINGS 20736-0000' needs "the last known
# street-suffix word" as the split point (street '8590 OAK HILL DR', city
# 'OWINGS'), not a naive minimal-street / maximal-city regex split, which
# put "OAK HILL DR OWINGS" entirely into city on the first pass here.
try:
    from md_register_of_wills_pull import STREET_SUFFIXES, split_street_city_tokens  # noqa: E402
except ImportError:  # running as a script with a different sys.path setup
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from md_register_of_wills_pull import STREET_SUFFIXES, split_street_city_tokens  # noqa: E402


def split_premises_address(premises: str) -> dict:
    """'8590  OAK HILL DR OWINGS 20736-0000' -> street/city/zip. No state
    token is present in this field (SDAT is MD-only by definition).
    Real premises strings can trail a descriptor after the zip ('... 21146-0000
    Waterfront', seen live 2026-09-04) or dangle the hyphen ('... 21703-') --
    both must still split, or the whole blob ships as the street."""
    m = re.match(r"^(.*?)\s+(\d{5})(?:-\d{0,4})?(?:\s+[A-Za-z][A-Za-z /-]*)?$", premises.strip())
    if not m:
        return {"street": premises.strip(), "city": "", "zip": ""}
    body, zip5 = m.groups()
    st_tokens, city_tokens = split_street_city_tokens(body.split())
    return {"street": " ".join(st_tokens).strip(), "city": " ".join(city_tokens).strip(), "zip": zip5}


# ── Orchestration ────────────────────────────────────────────────────────

_NAME_NOISE = {"&", "AND", "JR", "SR", "II", "III", "IV", "THE", "ESTATE", "OF", "TRUSTEE", "TR", "TRUST",
               "TRUSTEES", "ETAL", "ET", "AL", "LIFE", "TENANT", "MR", "MRS"}


def _name_tokens(name: str) -> set[str]:
    return {t.rstrip(".") for t in re.split(r"[\s,/]+", name.upper())
            if t and t.rstrip(".") not in _NAME_NOISE and len(t.rstrip(".")) > 1}


def _name_token_overlap(decedent: str, owner: str) -> float:
    """Fraction of the DECEDENT's name tokens (first, middle AND last) that
    appear in the SDAT owner string. Anchored on the decedent side so the
    middle initial SDAT adds is not evidence against, but the decedent's
    OWN middle name must be present for a full score: "JUAN CARLOS SAMPLE MORALES" against SDAT's "PEREZ MORALES JUAN DANILO" is 2 of 4, not a
    match -- a first+last-only comparison called that HIGH on 2026-08-28
    and handed the decedent a stranger's house."""
    ta, tb = _name_tokens(decedent), _name_tokens(owner)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta)


def _initial_tokens(name: str) -> set[str]:
    """Single-letter (initial) tokens of a name, noise words excluded --
    the tokens _name_tokens deliberately drops."""
    return {t.rstrip(".") for t in re.split(r"[\s,/]+", (name or "").upper())
            if len(t.rstrip(".")) == 1 and t.rstrip(".").isalpha()}


def name_match_level(decedent_full: str, last_name: str, first_name: str, owner: str) -> str:
    """HIGH: every token of the decedent's full name is on the SDAT owner.
    MEDIUM: surname and first name are, but a middle name is not (could be
    the decedent under a variant, could be a relative -- a human decides).
    LOW: surname only or nothing -- treated as NOT the decedent."""
    owner_t = _name_tokens(owner)
    full_t = _name_tokens(decedent_full or f"{first_name} {last_name}")
    # SDAT often carries a middle INITIAL where the estate carries the full
    # middle name ("SAMPLE WILLIAM H" vs "WILLIAM HENRY SAMPLE"): credit a
    # decedent token whose first letter is a bare initial on the owner side.
    owner_initials = _initial_tokens(owner)
    matched = {t for t in full_t if t in owner_t or t[0] in owner_initials}
    if full_t and matched == full_t:
        # A decedent middle INITIAL is dropped by _name_tokens (single
        # letter), so "ROBERT E KAISER" was matching ANY Robert Kaiser and
        # scoring HIGH -- live 2026-09-04, the owner was possibly the PR
        # Robert J Kaiser, a living relative. If the decedent carries a
        # middle initial, the owner side must carry something compatible
        # (the same initial, or a name starting with it) for HIGH; owners
        # with no middle at all can only reach MEDIUM -- a human decides.
        dec_initials = _initial_tokens(decedent_full)
        owner_word_initials = {t[0] for t in owner_t} | owner_initials
        if any(ini not in owner_word_initials for ini in dec_initials):
            return "MEDIUM"
        return "HIGH"
    last_t, first_t = _name_tokens(last_name), _name_tokens(first_name.split()[0] if first_name else "")
    if last_t and last_t <= owner_t and first_t and first_t <= owner_t:
        return "MEDIUM"
    return "LOW"


def _confidence_from_overlap(overlap: float) -> str:
    return "HIGH" if overlap >= 0.99 else "MEDIUM" if overlap >= 0.5 else "LOW"


def _sdat_confirm_by_address(county: str, street: str, last_name: str, first_name: str,
                             full_name: str = "") -> dict | None:
    """Cross-check a deed-stated street address against SDAT's STREET
    ADDRESS search (never the tax account id -- Basem 2026-08-27). Returns
    None when SDAT has no parcel at that address or the call failed; callers
    decide what an unconfirmed address is worth."""
    try:
        sdat = lookup_sdat_by_address(county, street)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    if not sdat or not sdat.get("premises_address"):
        return None
    levels = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    level = max((name_match_level(full_name, last_name, first_name, name) for name in sdat["owner_names"]),
                key=lambda lv: levels[lv], default="LOW")
    overlap = max((_name_token_overlap(full_name or f"{first_name} {last_name}", name)
                   for name in sdat["owner_names"]), default=0.0)
    addr = split_premises_address(sdat["premises_address"])
    return {"addr": addr, "owner_names": sdat["owner_names"], "mailing_address": sdat.get("mailing_address", ""),
            "legal_description": sdat["legal_description"], "overlap": overlap, "level": level,
            "district": sdat.get("district", ""), "account": sdat.get("account", ""),
            "use": sdat.get("use", ""), "principal_residence": sdat.get("principal_residence", "")}


def _pr_owner_match(prs: list[dict] | None, owner: str) -> str | None:
    """The name of the first PR whose first+last tokens all appear on the
    SDAT owner string, else None. Used two ways (Basem 2026-09-04, from the
    human review of the 43-estate run): an owner-name MISMATCH where the
    owner is actually the PR (surviving spouse Yiwen Huang on Weigang Zhu's
    title) is the estate's property, not a stranger's; and a HIGH decedent
    match where a PR shares the decedent's first+last (Robert J Kaiser vs
    decedent Robert E Kaiser) is ambiguous, not confirmed."""
    owner_t = _name_tokens(owner)
    for pr in prs or []:
        pr_name = (pr.get("name") or "").strip()
        pr_t = _name_tokens(pr_name)
        if len(pr_t) >= 2 and pr_t <= owner_t:
            return pr_name
    return None


def _probe_pr_addresses(county: str, prs: list[dict] | None, last_name: str,
                        first_name: str, full_name: str) -> dict | None:
    """The cheapest candidate the estate file already holds: the PR's own
    mailing address. In the 2026-09-04 human review 3 of 15 missed
    properties WERE the PR's address (the PR lives in / next to the
    decedent's house). SDAT-check each in-state PR street; accept only when
    SDAT's CURRENT OWNER matches the DECEDENT (an owner matching the PR
    would just be the PR's own home -- no evidence).

    The decedent match is scored PER OWNER COMPONENT, and a component that
    fully contains a PR's name is excluded from scoring -- otherwise a PR
    who shares the decedent's first+last (daughter Jacqueline HEWITT Stock
    for decedent Jacqueline HEWITT -- live false positive 2026-09-04) makes
    the PR's own house look like the decedent's."""
    seen: set[str] = set()
    for pr in prs or []:
        street = (pr.get("street") or "").strip()
        state = (pr.get("state") or "").strip().upper()
        if not street or (state and state != "MD"):
            continue
        key = street.upper()
        if key in seen:
            continue
        seen.add(key)
        confirmed = _sdat_confirm_by_address(county, street, last_name, first_name, full_name)
        if not confirmed or "error" in confirmed:
            continue
        levels = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        best = "LOW"
        for comp in confirmed["owner_names"]:
            if _pr_owner_match(prs, comp):
                continue  # this component IS a PR -- their name proves nothing here
            lvl = name_match_level(full_name, last_name, first_name, comp)
            if levels[lvl] > levels[best]:
                best = lvl
        confirmed = {**confirmed, "level": best}
        if confirmed["level"] in ("HIGH", "MEDIUM"):
            return {
                "found": True, "confidence": confirmed["level"],
                "street": confirmed["addr"]["street"] or street,
                "city": confirmed["addr"]["city"] or (pr.get("city") or ""),
                "state": "MD", "zip": confirmed["addr"]["zip"] or (pr.get("zip") or ""),
                "source_book_page": "", "source_date": "",
                "deed_tax_id": "", "deed_remarks_account": "",
                "sdat_premises": confirmed["addr"]["street"],
                "source": "pr_address_sdat_owner_match",
                "owner_names": confirmed["owner_names"], "mailing_address": confirmed["mailing_address"],
                "legal_description": confirmed["legal_description"],
                "sdat_district": confirmed["district"], "sdat_account": confirmed["account"],
                "sdat_use": confirmed["use"], "principal_residence": confirmed["principal_residence"],
                "name_overlap": round(confirmed["overlap"], 2), "name_match": confirmed["level"],
                "confidence_note": f"property found at the PR's own address ({street}); SDAT owner is the decedent",
            }
        time.sleep(1.0)
    return None


def find_property_for_decedent(county: str, last_name: str, first_name: str = "", full_name: str = "",
                                middle_name: str = "", prs: list[dict] | None = None) -> dict:
    """Full pipeline, most-reliable path first:

    1. Land Records grantee search, newest DEED-like instrument first.
    2. For each candidate, open its 'view=I' page to resolve the real PDF
       URL, fetch the PDF's text (public, no auth, and a genuine text layer
       on anything reasonably recent -- no OCR involved).
    3. Read the deed's OWN address statement ("has the address of: ...",
       "known as No. ...", "Property Address: ...").
    4. Cross-check that address against SDAT's STREET ADDRESS search --
       house number + street name, per Basem (2026-08-27): "use the
       property address and then street, don't use tax account ID". SDAT's
       current owner decides the verdict:
         - owner is the decedent          -> found, HIGH
         - surname only                   -> found, MEDIUM (spouse/estate)
         - somebody else entirely         -> the decedent no longer owns it
           (sold since the deed); keep looking at older deeds, and if nothing
           else confirms, report the mismatch rather than the address
         - SDAT unreachable / no parcel   -> found, MEDIUM, "sdat_unconfirmed"

    The deed's stated Tax ID and the Remarks-column account number are
    still parsed and RETURNED for reference, but are never used to query
    SDAT (the Account Identifier form needs a Subdivision field the
    automation never got to work, and Basem asked for the street path).
    A miss documents exactly what was tried so a human isn't left guessing."""
    middle_initial = (middle_name or "").strip()[:1]
    try:
        results = []
        if middle_initial:
            results = search_grantee(county, last_name, first_name, middle_name=middle_initial)
        if not results:
            # No middle given, the index stored no middle for this person, or
            # a transient empty response (seen live 2026-09-03: the same
            # decedent returned deeds in one run and "0 grantee records"
            # hours later) -- fall back to first+last, then one plain retry.
            results = search_grantee(county, last_name, first_name)
        if not results:
            time.sleep(3.0)
            results = search_grantee(county, last_name, first_name)
    except Exception as e:  # noqa: BLE001
        pr_hit = _probe_pr_addresses(county, prs, last_name, first_name, full_name)
        if pr_hit:
            return pr_hit
        # `error` marks this an INFRASTRUCTURE failure (dead Firecrawl quota,
        # login break, network), NOT a finding. Callers must not cache it as
        # "checked / not found" -- a 402 Payment Required froze 60 of 69
        # records as false NOT_FOUND on 2026-09-04, the same trap the
        # obituary DP batch hit with an exhausted LLM balance.
        return {"found": False, "error": True, "confidence": "LOW",
                "reason": f"Land Records search failed: {e}"}

    candidates = [r for r in results if r["instrument_type"].upper() in DEED_LIKE_INSTRUMENTS]
    if not candidates:
        pr_hit = _probe_pr_addresses(county, prs, last_name, first_name, full_name)
        if pr_hit:
            return pr_hit
        return {"found": False, "confidence": "LOW",
                "reason": f"{len(results)} grantee record(s) found, none were DEED-type instruments"}
    # Each candidate costs a Land Records login + PDF fetch (+ an SDAT call),
    # 30-60 s apiece; a common surname returns dozens of deed rows and one
    # decedent then eats 15+ minutes (seen live 2026-08-28 on Anne Arundel).
    # Newest-first is already the order, and the CURRENT home is almost
    # always within the newest few conveyances -- cap it and say so.
    skipped_candidates = max(0, len(candidates) - MAX_DEED_CANDIDATES)
    candidates = candidates[:MAX_DEED_CANDIDATES]

    creds = load_credentials()
    tried_pdf = 0
    addresses_seen = 0
    mismatches: list[dict] = []
    unconfirmed: dict | None = None
    for cand in candidates:
        deed_info = None
        if cand.get("info_viewer_url"):
            try:
                pdf_url = resolve_pdf_url(cand["info_viewer_url"], creds)
                if pdf_url:
                    text = fetch_deed_text(pdf_url)
                    if len(text) > 200:  # a real text layer, not a bare scan
                        tried_pdf += 1
                        deed_info = parse_deed_text(text)
            except Exception:  # noqa: BLE001
                deed_info = None

        if not (deed_info and deed_info["address_statement"]):
            continue
        addresses_seen += 1
        addr = split_deed_address_statement(deed_info["address_statement"])
        base = {
            "street": addr["street"], "city": addr["city"], "state": "MD", "zip": addr["zip"],
            "source_book_page": cand["book_page"], "source_date": cand["date"],
            "deed_tax_id": deed_info["tax_id"],
            "deed_remarks_account": "-".join(decode_remarks_account(cand["remarks"]) or ()) or "",
        }
        confirmed = _sdat_confirm_by_address(county, addr["street"], last_name, first_name, full_name)
        if confirmed and "error" in confirmed:
            if unconfirmed is None:
                unconfirmed = {**base, "sdat_error": confirmed["error"]}
            continue
        if not confirmed:
            if unconfirmed is None:
                unconfirmed = {**base, "sdat_error": "no parcel at that street address"}
            continue
        sdat_fields = {
            "owner_names": confirmed["owner_names"], "mailing_address": confirmed["mailing_address"],
            "legal_description": confirmed["legal_description"],
            "sdat_district": confirmed["district"], "sdat_account": confirmed["account"],
            "sdat_use": confirmed["use"], "principal_residence": confirmed["principal_residence"],
            "name_overlap": round(confirmed["overlap"], 2),
            "name_match": confirmed["level"],
        }
        if confirmed["level"] in ("HIGH", "MEDIUM"):
            # SDAT premises wins for city/zip formatting (deed text can carry
            # a ZIP+4 or a post-office city); the deed's street is kept when
            # SDAT abbreviates it, both are recorded.
            result = {
                "found": True, "confidence": confirmed["level"],
                **base,
                "city": confirmed["addr"]["city"] or addr["city"],
                "zip": confirmed["addr"]["zip"] or addr["zip"],
                "sdat_premises": confirmed["addr"]["street"],
                "source": "deed_text_confirmed_by_sdat_street",
                **sdat_fields,
            }
            # Kaiser case (2026-09-04): the owner matches the decedent, but a
            # PR shares the decedent's first+last -- the person on title may
            # be the living relative, not the decedent. Cap at MEDIUM and say
            # so; a human decides.
            ambiguous_pr = _pr_owner_match(prs, " / ".join(confirmed["owner_names"]))
            if ambiguous_pr and _name_tokens(ambiguous_pr) != _name_tokens(full_name):
                result["confidence"] = "MEDIUM"
                result["confidence_note"] = (f"SDAT owner also matches PR {ambiguous_pr} -- "
                                             "title may be the living relative's, verify")
            return result
        # Zhu case (2026-09-04): the owner isn't the decedent, but IS a PR
        # (surviving spouse / co-owner) -- the estate's property, not a
        # stranger's. Ship it MEDIUM instead of refusing.
        pr_owner = _pr_owner_match(prs, " / ".join(confirmed["owner_names"]))
        if pr_owner:
            return {
                "found": True, "confidence": "MEDIUM",
                **base,
                "city": confirmed["addr"]["city"] or addr["city"],
                "zip": confirmed["addr"]["zip"] or addr["zip"],
                "sdat_premises": confirmed["addr"]["street"],
                "source": "deed_text_owner_is_pr",
                **sdat_fields,
                "confidence_note": f"SDAT's current owner is the PR ({pr_owner}) -- estate-held, not sold",
            }
        mismatches.append({**base, **sdat_fields})

    # Nothing confirmed off the deeds -- the PR's own address is the last,
    # cheapest candidate (and an SDAT-owner-confirmed hit there outranks an
    # unconfirmed deed statement).
    pr_hit = _probe_pr_addresses(county, prs, last_name, first_name, full_name)
    if pr_hit:
        return pr_hit
    if unconfirmed:
        return {"found": True, "confidence": "MEDIUM", **unconfirmed,
                "source": "deed_text_statement_sdat_unconfirmed"}
    if mismatches:
        m = mismatches[0]
        return {
            "found": False, "confidence": "LOW",
            "reason": (f"deed says {m['street']}, {m['city']} but SDAT's current owner is "
                       f"{' / '.join(m['owner_names'])} (not the decedent) -- likely sold since the "
                       f"{m['source_date']} deed; {len(mismatches)} such address(es) checked"),
            "candidate_street": m["street"], "candidate_city": m["city"], "candidate_zip": m["zip"],
            "sdat_owner_names": m["owner_names"], "sdat_mailing": m.get("mailing_address", ""),
        }
    if middle_initial:
        # The middle-scoped search can EXCLUDE the address-bearing deed when
        # that instrument was indexed without a middle (live 2026-09-04:
        # McKetty's confirmed Parrs Ridge deed vanished under middle 'H' and
        # three other deeds took its slots). Zero-row fallback above cannot
        # catch a nonzero-but-wrong candidate set -- widen and re-walk.
        print(f"      middle-scoped deeds yielded no address -- widening to first+last only")
        return find_property_for_decedent(county, last_name, first_name, full_name=full_name,
                                           middle_name="", prs=prs)
    return {"found": False, "confidence": "LOW",
            "reason": f"{len(candidates)} DEED-type record(s) checked ({tried_pdf} had a readable PDF text "
                      f"layer, {addresses_seen} stated an address), none yielded a property address"
                      + (f"; {skipped_candidates} older deed(s) not opened (MAX_DEED_CANDIDATES)" if skipped_candidates else "")}


# ── Doctor / CLI ─────────────────────────────────────────────────────────

def run_doctor():
    creds = load_credentials()
    print("Logging into Land Records...")
    html = firecrawl_scrape_retry(LANDREC_LOGIN_URL, _login_actions(creds) + [{"type": "scrape"}], timeout=90)
    _assert_logged_in(html)
    soup = BeautifulSoup(html, "html.parser")
    sel = soup.select_one("#body_ddlbarcounties")
    live = {opt.get_text(strip=True): opt.get("value") for opt in sel.find_all("option") if opt.get("value")}
    print(f"Land Records login OK. {len(live)} counties on live dropdown.")

    print("Checking SDAT county/search-type dropdowns...")
    html2 = firecrawl_scrape_retry(SDAT_URL, [{"type": "wait", "milliseconds": 2000}, {"type": "scrape"}], timeout=60)
    soup2 = BeautifulSoup(html2, "html.parser")
    sdat_counties = {opt.get_text(strip=True): opt.get("value")
                     for opt in soup2.select_one(TYPE_SEL.replace("ddlSearchType", "ddlCounty")).find_all("option")
                     if opt.get("value") and opt.get("value") != "-1"}
    print(f"SDAT live county count: {len(sdat_counties)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--county", default="")
    parser.add_argument("--last-name", default="")
    parser.add_argument("--first-name", default="")
    args = parser.parse_args()

    if args.doctor:
        run_doctor()
        return

    if not (args.county and args.last_name):
        raise SystemExit("--county and --last-name are required (or use --doctor)")

    result = find_property_for_decedent(args.county, args.last_name, args.first_name)
    import json
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
