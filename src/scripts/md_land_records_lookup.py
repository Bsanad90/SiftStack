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

# Land Records party radio list (#body_rdlParty_N). _2 = Grantee is verified live
# (2026-09-04); _0 / _1 are the form's other two buttons (Both / Grantor) and are
# verified by md_deed_lookup's --probe, which reads the "Grantor:" / "Grantee:" prefix
# the results grid prints on every party cell.
LANDREC_PARTY_RADIO = {"both": "#body_rdlParty_0", "grantor": "#body_rdlParty_1", "grantee": "#body_rdlParty_2"}


def search_grantee(county: str, last_name: str, first_name: str = "",
                    match_mode: str = "Begins", middle_name: str = "", party: str = "grantee") -> list[dict]:
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
        {"type": "executeJavascript", "script": _check_radio_js(LANDREC_PARTY_RADIO[party])},  # Grantee by default
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


def deed_account_candidates(remarks: str, tax_id: str) -> list[tuple[str, str, str]]:
    """(district, subdivision, account) interpretations to try against the
    SDAT Account Identifier form, best-first. The full identifier decomposes
    county(2) + district(2) + subdivision(3) + account(8) (Basem 2026-09-08);
    deeds carry fragments of it in two places and several shapes:
      tax_id  '03-02366800'      -> ('03', '', '02366800')
      tax_id  '03-437-90017499'  -> ('03', '437', '90017499')
      remarks '... 2107481 8590' -> 8-digit blob, legacy 2+6 split
      remarks 13-digit blob      -> 2+3+8 split
    A wrong interpretation costs one SDAT call and fails the owner match, so
    the list is capped by the caller."""
    out: list[tuple[str, str, str]] = []

    def add(d: str, s: str, a: str):
        t = (d, s, a)
        if d and a and t not in out:
            out.append(t)

    tid = (tax_id or "").strip()
    if tid:
        parts = [p for p in re.split(r"[^\d]+", tid) if p]
        if len(parts) == 3:
            add(parts[0].zfill(2), parts[1], parts[2])
        elif len(parts) == 2:
            add(parts[0].zfill(2), "", parts[1])
        else:
            digits = "".join(parts)
            if len(digits) == 13:
                add(digits[:2], digits[2:5], digits[5:])
            elif len(digits) == 10:
                add(digits[:2], "", digits[2:])
    for blob in re.findall(r"\d{7,13}", remarks or ""):
        if len(blob) == 7:
            blob = "0" + blob
        if len(blob) == 13:
            add(blob[:2], blob[2:5], blob[5:])
        elif len(blob) == 10:
            add(blob[:2], "", blob[2:])
        elif len(blob) == 8:
            add(blob[:2], "", blob[2:])          # legacy 2+6 (proven on Calvert)
            lone = [t for t in re.findall(r"(?<!\d)(\d{1,2})(?!\d)", remarks or "")
                    if int(t) > 0]                # zeros are lot/block filler, not a district
            if lone:                              # a bare district token elsewhere
                add(lone[0].zfill(2), "", blob)   # 8 digits ARE the account (AA shape)
    return out


# Maryland open-data assessment roll (Socrata), one dataset per county.
# Owner names are withheld ('Hidden Property Owner Names') but legal
# description, lot, block and the premise address are all present and
# queryable for free -- which turns a Remarks cell like 'MANOR WOODS12  7'
# (subdivision + lot + block, the Montgomery convention) into the parcel's
# street address with NO Firecrawl spend. Proven live 2026-09-08: MANOR
# WOODS / lot 12 / block 7 -> exactly one parcel, 4708 MILTFRED TER.
OPENDATA_DATASET = {
    "Montgomery": "kb22-is2w",
    "Anne Arundel": "3w75-7rie",
    "Frederick": "gx8c-a963",
}
_OD_LEGAL = "legal_description_line_1_mdp_field_legal1_sdat_field_17"
_OD_LOT = "lot_mdp_field_lot_sdat_field_41"
_OD_BLOCK = "block_mdp_field_block_sdat_field_40"
_OD_ADDR = "mdp_street_address_mdp_field_address"
_OD_CITY = "mdp_street_address_city_mdp_field_city"
_OD_ZIP = "premise_address_zip_code_mdp_field_premzip_sdat_field_26"

REMARKS_LEGAL_RE = re.compile(
    r"^([A-Z][A-Z .'&-]{2,}?)\s*(\d{1,4}[A-Z]?)\s+(\d{1,4}[A-Z]?|[A-Z])\s*$")


def parse_remarks_legal(remarks: str) -> tuple[str, str, str] | None:
    """'MANOR WOODS12  7' -> ('MANOR WOODS', '12', '7');
    'STRATHMORE BEL PRE12  5' -> ('STRATHMORE BEL PRE', '12', '5').
    A trailing truncated address fragment ('...LLE MD 20853') is stripped
    first. Returns None when the cell isn't the subdivision+lot+block shape
    (account blobs and book-page refs are handled by the other decoders)."""
    txt = (remarks or "").strip().upper()
    txt = re.sub(r"\s+[A-Z]*\s*MD\s+\d{5}(?:-\d{4})?$", "", txt).strip()
    m = REMARKS_LEGAL_RE.match(txt)
    if not m:
        return None
    name, lot, block = m.group(1).strip(), m.group(2), m.group(3)
    if len(name) < 3 or name in {"N/AN/A", "N/A"}:
        return None
    return name, lot, block


def lookup_opendata_by_legal(county: str, subdiv_name: str, lot: str, block: str) -> list[dict]:
    """Query the county's open-data assessment roll for parcels whose legal
    description carries `subdiv_name` and whose lot/block match exactly
    (values are space-padded in the dataset, so the compare trims). Returns
    [{street, city, zip}] -- usually exactly one row; the caller SDAT-checks
    the owner before believing it."""
    dataset = OPENDATA_DATASET.get(county)
    if not dataset:
        return []
    safe = subdiv_name.replace("'", "''")
    # NO upper() in the $where. Socrata sits behind Cloudflare, and a $where
    # containing "upper(" is served an interstitial challenge -- HTTP 403 with
    # a "Just a moment..." HTML body, which `raise_for_status` surfaces as a
    # bare 403 that reads like an auth/quota problem rather than a WAF block.
    # It cost 5 silent address-lookup fall-throughs on the 2026-09-10 run
    # before anyone noticed; the identical query without upper() returns 200.
    # Dropping it is behaviour-preserving: `like` is case-sensitive, and the
    # legal-description values are stored uppercase apart from a literal
    # "IMPS (Improvements)." prefix that is never part of a subdivision name
    # (measured over ~14.6K rows across all three county datasets: 0 lowercase
    # remainders in Montgomery and Anne Arundel, 2 non-name artifacts in
    # Frederick). `parse_remarks_legal` already uppercases what it passes in.
    resp = requests.get(
        f"https://opendata.maryland.gov/resource/{dataset}.json",
        params={"$where": f"{_OD_LEGAL} like '%{safe}%'", "$limit": 5000},
        timeout=120)
    resp.raise_for_status()
    out = []
    for row in resp.json():
        if str(row.get(_OD_LOT, "")).strip().lstrip("0") != lot.lstrip("0"):
            continue
        if str(row.get(_OD_BLOCK, "")).strip() != block:
            continue
        street = (row.get(_OD_ADDR) or "").strip()
        if street:
            out.append({"street": street, "city": (row.get(_OD_CITY) or "").strip(),
                        "zip": (row.get(_OD_ZIP) or "").strip()[:5]})
    return out


def remarks_street_number(remarks: str) -> str:
    """The TRAILING short number in a Remarks cell is the parcel's street
    number ('SAMPLE MANOR 0 0 2107481 8590' -> the parcel is 8590 Oak Hill
    Dr). Usable as a partial: SDAT street search by NUMBER with the row
    picked by the decedent's name (Basem 2026-09-08). Returns '' when the
    cell has no plausible trailing street number."""
    toks = (remarks or "").split()
    if len(toks) < 2:
        # A lone token ('03') is a district code, not a street number.
        return ""
    tail = toks[-1]
    if re.fullmatch(r"\d{1,6}", tail) and not re.fullmatch(r"0+", tail):
        return tail
    return ""


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


SUBDIV_SEL = DISTRICT_SEL.replace("txtDistrict", "txtSubDiv")


def lookup_sdat(county: str, district: str, account: str, subdivision: str = "") -> dict | None:
    """SDAT search type 02, PROPERTY ACCOUNT IDENTIFIER. The full identifier
    decomposes as county(2) + district(2) + subdivision(3) + account(8)
    (Basem 2026-09-08); counties whose form shows the Subdivision box
    (e.g. Anne Arundel) reject a submit without it -- pass `subdivision`
    there. The box is inside pnlSubDiv and simply absent on counties that
    don't use it, where filling it is a harmless no-op."""
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
        {"type": "executeJavascript", "script": (_set_text_js(SUBDIV_SEL, subdivision) if subdivision else "")
                                               + _set_text_js(ACCOUNT_SEL, account)},
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


def _click_matching_result_row_js(street_number: str, street_name: str,
                                  owner_tokens: list[str] | None = None,
                                  require_owner: bool = False, unit: str = "") -> str:
    """If the street search landed on a multi-match grid (several parcels
    on the same number/name, e.g. condo units), click the best row. The
    grid shows each parcel's OWNER, so when `owner_tokens` (decedent name
    tokens) are given, a row carrying the street AND the owner's surname
    wins over the first street match -- on '300 KING FARM' the first row
    is an arbitrary unit and its owner is a stranger (Basem 2026-09-08:
    'putting partial name and find with decedent's name'). With
    `require_owner` (the partial-street retry) a street-only row is NOT
    clicked at all: a broad grid without an owner match must fail loudly
    rather than hand back a random neighbor. A no-op when the page is
    already a single parcel's detail (it has 'Owner Name' on it)."""
    num = street_number.replace("'", "")
    name = street_name.replace("'", "").upper()
    unit_tok = re.sub(r"[^A-Z0-9]", "", (unit or "").upper())
    # '231B' -> a JS regex that tolerates the grid's own separators ('23-1B', '23 1B') and is
    # anchored on a unit word, so the digits of an account number never satisfy it
    # the grid prints 'UNIT: 307' (colon, confirmed 2026-10-02 on 66 Franklin St: 79 rows)
    unit_re = ("(UNIT|APT|STE|NO|#)[^A-Z0-9]*" + "[^A-Z0-9]?".join(unit_tok) + "(?![A-Z0-9])") if unit_tok else ""
    toks = [re.sub(r"[^A-Z0-9]", "", t.upper()) for t in (owner_tokens or [])]
    toks = [t for t in toks if len(t) > 1]
    toks_js = "[" + ",".join(f"'{t}'" for t in toks) + "]"
    # The grid's row link is an ASP.NET LinkButton whose href runs
    # WebForm_DoPostBackWithOptions(..., performValidation=true, ...): a plain a.click()
    # went through the page validators and silently did nothing on every multi-row grid
    # (38 of 185 MD lookups "no parcel", 2026-10-02; the raw page is in
    # output/.../md_probe/sdat_grid_403vale_raw.html). Post back the row's own event
    # target directly through __doPostBack instead, and fall back to click().
    return f"""
        (function() {{
            if ((document.body.innerText || '').indexOf('Owner Name') >= 0) return;
            function go(a) {{
                var h = a.getAttribute('href') || '';
                // no regex with quotes or backslashes: the script travels through JSON and the
                // scraper's own string handling, and both mangled an escaped quote (2026-10-02).
                var m = null;
                var k = h.indexOf('PostBackOptions(');
                if (k >= 0) {{ m = h.slice(k + 16).split(',')[0].replace(/[^A-Za-z0-9_$]/g, ''); }}
                if (m && typeof __doPostBack === 'function') {{ __doPostBack(m, ''); return; }}
                a.click();
            }}
            var toks = {toks_js};
            var rows = document.querySelectorAll('table tr');
            var streetHit = null;
            for (var i = 0; i < rows.length; i++) {{
                var t = (rows[i].innerText || '').toUpperCase();
                if (t.indexOf('{num}') >= 0 && t.indexOf('{name}') >= 0
                        && ('{unit_tok}' === '' || new RegExp('{unit_re}').test(t))) {{
                    // a condo grid lists one row per UNIT: without the unit in the row text the
                    // first street match is a neighbour (66 Franklin St Unit 307 -> ADKINS, 2026-10-02)
                    // an OUTER layout row wraps the whole grid: its innerText matches everything and
                    // its first <a> is the header's SORT link, whose postback re-rendered the grid
                    // (every multi-row lookup failed this way until 2026-10-02). Leaf rows only,
                    // and the row's own details link.
                    if (rows[i].querySelector('table')) continue;
                    var a = rows[i].querySelector('a.lnkdetails') || rows[i].querySelector('a[href]');
                    if (!a) continue;
                    if (!streetHit) streetHit = a;
                    if (toks.length) {{
                        var hits = 0;
                        for (var j = 0; j < toks.length; j++) if (t.indexOf(toks[j]) >= 0) hits++;
                        if (hits >= Math.min(2, toks.length)) {{ go(a); return; }}
                    }}
                }}
            }}
            if (streetHit && !{str(bool(require_owner)).lower()}) go(streetHit);
        }})();
    """


def street_unit(street: str) -> str:
    """'66 Franklin St Unit 307' -> '307'; '4601 N Park Ave Apt 208H' -> '208H'; '8101 Connecticut
    Ave Unit S709' -> 'S709'; '14805 Pennfield Cir 312' -> '312' (a bare trailing token after the
    suffix); '10608D Bethel Rd' -> '' (the letter belongs to the house number)."""
    s_ = (street or "").strip()
    m = re.search(r"\b(?:UNIT|APT|APARTMENT|STE|SUITE|#)\s*#?\s*([A-Z0-9-]+)\s*$", s_, re.I)
    if m:
        return m.group(1).upper()
    toks = s_.split()
    if len(toks) >= 4 and re.fullmatch(r"[A-Z]?\d+[A-Z]?", toks[-1], re.I) and toks[-2].rstrip(".").upper() in STREET_SUFFIXES:
        return toks[-1].upper()
    return ""


def lookup_sdat_by_address(county: str, street: str, owner_name: str = "") -> dict | None:
    """SDAT Real Property search, type 01 STREET ADDRESS (per Basem
    2026-08-27: 'use the property address and then street, don't use tax
    account ID'). Returns the parsed parcel (owner names, premises, mailing,
    legal description, district/account) or None when nothing matched.

    Verified live 2026-08-28 (Calvert): '456' + 'EXAMPLE' -> SAMPLE
    DEBORAH L, 456 EXAMPLE CT OWINGS 20736; '8590' + 'OAK HILL' -> the
    SAMPLE trustees' parcel. Both landed directly on the detail page; the
    multi-match grid click below is a guard for the unit/duplicate case and
    is a no-op on a direct hit.

    `owner_name` (the decedent, when the caller knows who should be on
    title) steers the multi-match grid: a condo search returns one row per
    UNIT and the first street match is an arbitrary neighbor -- the row
    carrying the owner's name is the right parcel. When the full street
    name finds nothing, a PARTIAL street name (first word) is retried with
    the owner match REQUIRED (Basem 2026-09-08), so misspelled or
    reformatted street names still resolve without ever returning a
    stranger's parcel."""
    county_id = SDAT_COUNTY_ID[county]
    number, name = split_street_for_sdat(street)
    if not (number and name):
        return None
    owner_tokens = [t for t in re.split(r"[\s,/]+", (owner_name or "").upper())
                    if t and t.rstrip(".") not in _NAME_NOISE]
    unit = street_unit(street)

    def _run(street_name: str, require_owner: bool) -> dict | None:
        actions = [
            {"type": "wait", "milliseconds": 2000},
            {"type": "executeJavascript", "script": _set_select_js(COUNTY_SEL, county_id)},
            {"type": "wait", "milliseconds": 500},
            {"type": "executeJavascript", "script": _set_select_js(TYPE_SEL, "01")},  # STREET ADDRESS
            {"type": "wait", "milliseconds": 500},
            {"type": "click", "selector": CONTINUE_SEL},
            {"type": "wait", "milliseconds": 3000},
            {"type": "executeJavascript", "script": _set_text_js(STREET_NUMBER_SEL, number) + _set_text_js(STREET_NAME_SEL, street_name)},
            {"type": "wait", "milliseconds": 500},
            {"type": "click", "selector": NEXT_SEL},
            {"type": "wait", "milliseconds": 4000},
            {"type": "executeJavascript", "script": _click_matching_result_row_js(
                number, street_name, owner_tokens, require_owner=require_owner, unit=unit)},
            # the row postback + detail render takes longer than 3.5 s (measured 2026-10-02:
            # 3.5 s scraped the grid again, 7 s landed on the detail page)
            {"type": "wait", "milliseconds": 7000},
            {"type": "scrape"},
        ]
        html = firecrawl_scrape_retry(SDAT_URL, actions, timeout=120)
        parsed = parse_sdat_result(html)
        if parsed:
            parsed["sdat_query"] = {"street_number": number, "street_name": street_name}
        return parsed

    parsed = _run(name, require_owner=False)
    first_word = name.split()[0] if name.split() else ""
    if not parsed and owner_tokens and first_word and first_word != name:
        # Partial street name casts a wider grid; the decedent's name picks
        # the row, and WITHOUT an owner match no row is clicked at all.
        print(f"      SDAT: no parcel under {number} {name!r} -- retrying partial "
              f"street {first_word!r} matched on the owner's name")
        parsed = _run(first_word, require_owner=True)
    return parsed


def lookup_sdat_by_street_number(county: str, number: str, owner_name: str) -> dict | None:
    """SDAT street search by house NUMBER alone -- the street-name box left
    empty -- with the result row REQUIRED to match the owner's name. Used
    when a deed's Remarks cell yields only a street number (its trailing
    token) and the decedent's name must pick the parcel out of every
    same-numbered street in the county (Basem 2026-09-08). Never clicks a
    row without an owner match, so it cannot return a stranger's parcel."""
    county_id = SDAT_COUNTY_ID[county]
    number = (number or "").strip()
    owner_tokens = [t for t in re.split(r"[\s,/]+", (owner_name or "").upper())
                    if t and t.rstrip(".") not in _NAME_NOISE]
    if not (number and owner_tokens):
        return None
    actions = [
        {"type": "wait", "milliseconds": 2000},
        {"type": "executeJavascript", "script": _set_select_js(COUNTY_SEL, county_id)},
        {"type": "wait", "milliseconds": 500},
        {"type": "executeJavascript", "script": _set_select_js(TYPE_SEL, "01")},  # STREET ADDRESS
        {"type": "wait", "milliseconds": 500},
        {"type": "click", "selector": CONTINUE_SEL},
        {"type": "wait", "milliseconds": 3000},
        {"type": "executeJavascript", "script": _set_text_js(STREET_NUMBER_SEL, number)},
        {"type": "wait", "milliseconds": 500},
        {"type": "click", "selector": NEXT_SEL},
        {"type": "wait", "milliseconds": 4000},
        {"type": "executeJavascript", "script": _click_matching_result_row_js(
            number, "", owner_tokens, require_owner=True)},
        {"type": "wait", "milliseconds": 3500},
        {"type": "scrape"},
    ]
    html = firecrawl_scrape_retry(SDAT_URL, actions, timeout=120)
    parsed = parse_sdat_result(html)
    if parsed:
        parsed["sdat_query"] = {"street_number": number, "street_name": "(number-only)"}
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
    subdivision = grab(r"Subdivision -\s*\n(\d+)")
    account = grab(r"Account (?:Identifier|Number) -\s*\n([\w-]+)")
    # "Use: | Principal Residence: | RESIDENTIAL | YES" -- the two values
    # follow the two labels, in that order.
    use_pr = grab(r"Principal Residence:\s*\n([A-Z ]+)\n(?:YES|NO)")
    principal_residence = grab(r"Principal Residence:\s*\n[A-Z ]+\n(YES|NO)")

    deed_ref = grab(r"Deed Reference:\s*\n(/?\s*\d+\s*/\s*\d+)")
    return {
        "owner_names": [n.strip() for n in owner_block.split("\n") if n.strip()],
        "premises_address": " ".join(premises.split()),
        "mailing_address": " ".join(mailing.split()),
        "legal_description": " ".join(legal_desc.split()),
        "district": district,
        "subdivision": subdivision,
        "account": account,
        "use": use_pr.strip(),
        "principal_residence": principal_residence,
        "deed_reference": " ".join(deed_ref.split()),
        "transfers": parse_sdat_transfers(text),
    }


_SDAT_TRANSFER_LABELS = ("Seller:", "Date:", "Price:", "Type:", "Deed1:", "Deed2:")


def parse_sdat_transfers(text: str) -> list[dict]:
    """The detail page's 'Transfer Information' block: up to three transfers, each a
    Seller / Date / Price / Type / Deed1 / Deed2 label run. A blank value is a label
    followed directly by the next label (confirmed live 2026-10-02: 'Seller: | Date: |
    04/20/1972 | Price: | $0 | Type: | NON-ARMS LENGTH OTHER | Deed1: | /02483/ 00123').
    Returns only transfers that carry a date or a deed reference."""
    i = text.find("Transfer Information")
    if i < 0:
        return []
    j = text.find("Exemption Information", i)
    block = text[i + len("Transfer Information"):j if j > 0 else None]
    lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
    out: list[dict] = []
    cur: dict | None = None
    label = None
    for ln in lines:
        if ln in _SDAT_TRANSFER_LABELS:
            if ln == "Seller:":
                cur = {"seller": "", "date": "", "price": "", "type": "", "deed1": "", "deed2": ""}
                out.append(cur)
            label = ln.rstrip(":").lower()
            continue
        if cur is not None and label:
            cur[label] = (cur[label] + " " + ln).strip() if cur[label] else ln
    return [t for t in out if t["date"] or t["deed1"]]


def deed_ref_key(ref: str) -> tuple[str, str] | None:
    """'/02483/ 00123' (SDAT), 'Book 2483, pp. 123-124' or 'Book 7696, p. 29' (Land Records)
    -> ('2483', '123'): book and FIRST page, digits only, no zero padding."""
    s = (ref or "").upper()
    m = re.search(r"BOOK\s*(\d+)\s*,?\s*PP?\.?\s*(\d+)", s) or re.search(r"/?\s*(\d+)\s*/\s*(\d+)", s)
    if not m:
        return None
    book, page = m.group(1).lstrip("0"), m.group(2).lstrip("0")
    return (book or "0", page or "0")


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
    # The initial credit is for MIDDLE names only. The decedent's first and
    # last name must be on the owner as whole tokens: with the credit applied
    # to every token, "Mychael Torres" scored HIGH against "TORRES, MARIA M"
    # (M = Mychael) and the pull adopted a stranger's house as owned
    # elsewhere (2026-ADM-001011, measured 2026-09-18).
    anchor = _name_tokens(last_name) | _name_tokens(first_name.split()[0] if first_name else "")
    matched = {t for t in full_t if t in owner_t or (t not in anchor and t[0] in owner_initials)}
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
        sdat = lookup_sdat_by_address(county, street,
                                      owner_name=full_name or f"{first_name} {last_name}")
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


def _confirm_from_parcel(sdat: dict, last_name: str, first_name: str,
                         full_name: str = "") -> dict | None:
    """Same output shape as `_sdat_confirm_by_address`, but for a parcel we
    already hold (from an account-identifier or street-number lookup) --
    no second SDAT query needed to score the owner match."""
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


# The Land Records grantee index stores the BARE surname -- an estate name
# like "MULLINIX JR, EARL WILLIAM" puts the suffix in decedent_last_name, and
# a "Begins" search for "MULLINIX JR" matches nothing (28 of the ledger's 273
# address misses carried a suffix, measured 2026-09-08).
NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}

# Cross-initial nicknames only: a first-INITIAL prefix search already covers
# same-letter shortenings (JIM under J finds JAMES), so this maps only pairs
# whose first letters differ. Both directions are generated at build time.
_NICKNAME_GROUPS = [
    {"WILLIAM", "BILL", "BILLY"}, {"ROBERT", "BOB", "BOBBY"}, {"JOHN", "JACK"},
    {"MARGARET", "PEGGY", "PEG"}, {"ELIZABETH", "BETTY", "BETSY", "LIZ"},
    {"RICHARD", "DICK"}, {"CHARLES", "CHUCK"}, {"EDWARD", "TED", "NED"},
    {"HENRY", "HANK", "HARRY"}, {"HELEN", "NELL"}, {"SARAH", "SALLY"},
    {"MARY", "POLLY", "MOLLY"}, {"ANNE", "NANCY", "NAN"}, {"THEODORE", "TED"},
    {"FRANCES", "FANNY"}, {"VIRGINIA", "GINNY"}, {"DOROTHY", "DOT"},
    {"KATHERINE", "CATHERINE", "KAY", "KITTY"}, {"ELEANOR", "NORA"},
]
NICKNAMES: dict[str, set[str]] = {}
for _grp in _NICKNAME_GROUPS:
    for _n in _grp:
        NICKNAMES.setdefault(_n, set()).update(_grp - {_n})

# Each variant costs a full Land Records login + search (~30s of Firecrawl);
# cap the ladder so one impossible decedent cannot eat minutes of quota.
MAX_VARIANT_SEARCHES = 5


def split_name_suffix(last_name: str) -> tuple[str, str]:
    """'MULLINIX JR' -> ('MULLINIX', 'JR'); 'TOMS SR.' -> ('TOMS', 'SR');
    a bare surname passes through unchanged."""
    toks = (last_name or "").strip().split()
    if len(toks) >= 2 and toks[-1].upper().rstrip(".") in NAME_SUFFIXES:
        return " ".join(toks[:-1]), toks[-1].upper().rstrip(".")
    return (last_name or "").strip(), ""


def name_search_variants(last_name: str, first_name: str = "",
                         middle_name: str = "") -> list[tuple[str, str, str]]:
    """Ordered (last, first, middle) tuples for the grantee search ladder.
    The deed index often disagrees with the estate record on name FORM --
    suffix glued to the surname, nickname vs formal first name, a bare
    initial, someone who goes by their middle name, a hyphenated surname
    indexed under one half. Deduped, most-precise first, capped by the
    caller."""
    bare_last, _suffix = split_name_suffix(last_name)
    first = (first_name or "").strip()
    middle = (middle_name or "").strip()
    mid_initial = middle[:1]

    variants: list[tuple[str, str, str]] = []

    def add(last: str, fst: str, mid: str):
        v = (last, fst, mid)
        if last and v not in variants:
            variants.append(v)

    if mid_initial:
        add(bare_last, first, mid_initial)
    add(bare_last, first, "")
    if len(first) > 1:
        add(bare_last, first[0], "")            # prefix search: E matches EARL
    for nick in sorted(NICKNAMES.get(first.upper(), ())):
        if nick[0] != first[:1].upper():        # cross-initial only
            add(bare_last, nick, "")
    if len(middle) > 1:
        add(bare_last, middle, "")              # goes-by-middle
    if "-" in bare_last:                        # hyphenated: each half
        for part in bare_last.split("-"):
            if part.strip():
                add(part.strip(), first, "")
    return variants


def find_property_for_decedent(county: str, last_name: str, first_name: str = "", full_name: str = "",
                                middle_name: str = "", prs: list[dict] | None = None) -> dict:
    """Variant-ladder wrapper around `_find_property_once`: tries the name
    forms from `name_search_variants` in order (suffix stripped first --
    the single biggest source of false 'no grantee records'), returns the
    first confirmed hit, and only then falls back to the PR-address probe.
    An infrastructure error aborts the ladder immediately so a dead
    Firecrawl quota is not billed once per variant."""
    variants = name_search_variants(last_name, first_name, middle_name)[:MAX_VARIANT_SEARCHES]
    if not variants:
        return {"found": False, "confidence": "LOW", "reason": "no usable decedent name to search"}

    misses: list[dict] = []
    all_zero = True
    for i, (v_last, v_first, v_mid) in enumerate(variants):
        if i:
            print(f"      name variant {i + 1}/{len(variants)}: last={v_last!r} first={v_first!r} mid={v_mid!r}")
        res = _find_property_once(county, v_last, v_first, full_name=full_name,
                                  middle_name=v_mid, prs=prs, probe_pr=False)
        if res.get("error"):
            return res
        if res.get("found"):
            res["searched_as"] = f"{v_last}, {v_first} {v_mid}".strip()
            return res
        if not res.get("zero_rows"):
            all_zero = False
        misses.append(res)

    if all_zero:
        # A transient empty response is a known live failure mode (2026-09-03:
        # the same decedent returned deeds in one run and zero rows hours
        # later) -- one plain retry of the primary variant before concluding.
        time.sleep(3.0)
        v_last, v_first, v_mid = variants[0]
        res = _find_property_once(county, v_last, v_first, full_name=full_name,
                                  middle_name=v_mid, prs=prs, probe_pr=False)
        if res.get("found") and not res.get("error"):
            res["searched_as"] = f"{v_last}, {v_first} {v_mid}".strip()
            return res

    pr_hit = _probe_pr_addresses(county, prs, last_name, first_name, full_name)
    if pr_hit:
        return pr_hit

    # Most informative miss wins: an SDAT owner mismatch names a concrete
    # address; deeds-without-addresses beats bare zero-row noise.
    def _rank(m: dict) -> int:
        r = m.get("reason", "")
        if "SDAT's current owner" in r:
            return 0
        if "DEED-type record(s) checked" in r:
            return 1
        if not m.get("zero_rows"):
            return 2
        return 3
    best = sorted(misses, key=_rank)[0]
    tried = "; ".join(f"{l}/{f}{('/' + m) if m else ''}" for l, f, m in variants)
    best["reason"] = f"{best.get('reason', '')} [searched {len(variants)} name variant(s): {tried}]"
    best.pop("zero_rows", None)
    return best


def _try_deed_identifiers(county: str, cand: dict, tax_id: str,
                          last_name: str, first_name: str, full_name: str,
                          prs: list[dict] | None = None) -> dict | None:
    """Resolve a deed that states NO address through what it does carry:
    the SDAT account (Remarks / Tax ID, via the Account Identifier form)
    or, failing that, the Remarks' trailing STREET NUMBER as an
    owner-matched partial search (both Basem 2026-09-08). Returns a full
    found-result dict, {'mismatch': {...}} when a parcel resolved but its
    owner is a stranger, or None when the deed carries nothing usable."""
    remarks = cand.get("remarks", "")

    # Branch 0 (FREE, no Firecrawl): Montgomery-style remarks carry
    # subdivision name + lot + block -- the open-data assessment roll turns
    # that into the premise address; SDAT then adjudicates the owner.
    legal = parse_remarks_legal(remarks)
    if legal:
        try:
            parcels = lookup_opendata_by_legal(county, *legal)
        except Exception as e:  # noqa: BLE001
            print(f"      open-data legal lookup failed ({e}) -- falling through")
            parcels = []
        for parcel in parcels[:2]:
            confirmed = _sdat_confirm_by_address(county, parcel["street"],
                                                 last_name, first_name, full_name)
            if not confirmed or "error" in confirmed:
                continue
            if confirmed["level"] in ("HIGH", "MEDIUM") or _pr_owner_match(prs, " / ".join(confirmed["owner_names"])):
                low_parcel = confirmed
                print(f"      remarks legal {legal} -> open-data -> {parcel['street']} "
                      f"({confirmed['level']})")
                break
        else:
            low_parcel = None
        if low_parcel:
            confidence = low_parcel["level"]
            note = ""
            if confidence == "LOW":
                # Accepted via the PR-owner match (Zhu case) -- estate-held,
                # not a stranger's; same MEDIUM-with-note the main branch uses.
                confidence = "MEDIUM"
                note = (f"SDAT's current owner is the PR "
                        f"({_pr_owner_match(prs, ' / '.join(low_parcel['owner_names']))}) -- estate-held, not sold")
            base = {
                "street": low_parcel["addr"]["street"] or parcel["street"],
                "city": low_parcel["addr"]["city"] or parcel["city"],
                "state": "MD", "zip": low_parcel["addr"]["zip"] or parcel["zip"],
                "source_book_page": cand.get("book_page", ""), "source_date": cand.get("date", ""),
                "deed_tax_id": tax_id or "", "deed_remarks_account": "",
            }
            if note:
                base["confidence_note"] = note
            return {"found": True, "confidence": confidence, **base,
                    "sdat_premises": low_parcel["addr"]["street"],
                    "source": "deed_remarks_legal_opendata_sdat_confirmed",
                    "owner_names": low_parcel["owner_names"], "mailing_address": low_parcel["mailing_address"],
                    "legal_description": low_parcel["legal_description"],
                    "sdat_district": low_parcel["district"], "sdat_account": low_parcel["account"],
                    "sdat_use": low_parcel["use"], "principal_residence": low_parcel["principal_residence"],
                    "name_overlap": round(low_parcel["overlap"], 2), "name_match": low_parcel["level"]}

    attempts = deed_account_candidates(remarks, tax_id)[:2]
    low_parcel = None
    for d, s, a in attempts:
        try:
            sdat = lookup_sdat(county, d, a, subdivision=s)
        except Exception:  # noqa: BLE001
            continue
        confirmed = _confirm_from_parcel(sdat, last_name, first_name, full_name)
        if confirmed:
            low_parcel = low_parcel or confirmed
            if confirmed["level"] in ("HIGH", "MEDIUM"):
                low_parcel = confirmed
                break
    else:
        streetnum = remarks_street_number(remarks)
        if streetnum and not low_parcel:
            try:
                sdat = lookup_sdat_by_street_number(
                    county, streetnum, full_name or f"{first_name} {last_name}")
            except Exception:  # noqa: BLE001
                sdat = None
            low_parcel = _confirm_from_parcel(sdat, last_name, first_name, full_name) if sdat else None

    if not low_parcel:
        return None
    confirmed = low_parcel
    base = {
        "street": confirmed["addr"]["street"], "city": confirmed["addr"]["city"],
        "state": "MD", "zip": confirmed["addr"]["zip"],
        "source_book_page": cand.get("book_page", ""), "source_date": cand.get("date", ""),
        "deed_tax_id": tax_id or "",
        "deed_remarks_account": "-".join(decode_remarks_account(remarks) or ()) or "",
    }
    sdat_fields = {
        "owner_names": confirmed["owner_names"], "mailing_address": confirmed["mailing_address"],
        "legal_description": confirmed["legal_description"],
        "sdat_district": confirmed["district"], "sdat_account": confirmed["account"],
        "sdat_use": confirmed["use"], "principal_residence": confirmed["principal_residence"],
        "name_overlap": round(confirmed["overlap"], 2),
        "name_match": confirmed["level"],
    }
    if confirmed["level"] in ("HIGH", "MEDIUM"):
        print(f"      deed stated no address -- resolved via its own identifiers "
              f"({base['deed_tax_id'] or base['deed_remarks_account'] or 'street number'}) "
              f"-> {base['street']} ({confirmed['level']})")
        result = {"found": True, "confidence": confirmed["level"], **base,
                  "sdat_premises": confirmed["addr"]["street"],
                  "source": "deed_identifier_sdat_confirmed", **sdat_fields}
        # Kaiser guard (parity with the street-confirmed branch): a PR who
        # shares the decedent's first+last may be the person on title.
        ambiguous_pr = _pr_owner_match(prs, " / ".join(confirmed["owner_names"]))
        if ambiguous_pr and _name_tokens(ambiguous_pr) != _name_tokens(full_name):
            result["confidence"] = "MEDIUM"
            result["confidence_note"] = (f"SDAT owner also matches PR {ambiguous_pr} -- "
                                         "title may be the living relative's, verify")
        return result
    return {"mismatch": {**base, **sdat_fields}}


def _find_property_once(county: str, last_name: str, first_name: str = "", full_name: str = "",
                        middle_name: str = "", prs: list[dict] | None = None,
                        probe_pr: bool = True) -> dict:
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
        # One search per call -- the VARIANT LADDER in the public wrapper
        # owns all fallbacks (no-middle, initials, nicknames, retry), so a
        # zero here must return honestly rather than re-search.
        results = search_grantee(county, last_name, first_name, middle_name=middle_initial)
    except Exception as e:  # noqa: BLE001
        pr_hit = _probe_pr_addresses(county, prs, last_name, first_name, full_name) if probe_pr else None
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
        pr_hit = _probe_pr_addresses(county, prs, last_name, first_name, full_name) if probe_pr else None
        if pr_hit:
            return pr_hit
        return {"found": False, "confidence": "LOW", "zero_rows": not results,
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
            # The deed's PDF states no address (or has no text layer), but
            # the instrument can still IDENTIFY the parcel: the Remarks cell
            # or the deed's own Tax ID carries the SDAT account (queried via
            # the Account Identifier form -- the Subdivision box works as of
            # 2026-09-08), and a remark's trailing token is the parcel's
            # STREET NUMBER, usable as an owner-matched partial search.
            acct_fallback = _try_deed_identifiers(
                county, cand, (deed_info or {}).get("tax_id", ""),
                last_name, first_name, full_name, prs=prs)
            if acct_fallback is not None:
                if acct_fallback.get("found"):
                    return acct_fallback
                mismatches.append(acct_fallback["mismatch"])
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
    pr_hit = _probe_pr_addresses(county, prs, last_name, first_name, full_name) if probe_pr else None
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
    # NOTE: the old middle-scoped "widen and re-walk" recursion (McKetty,
    # 2026-09-04) lives in the wrapper now -- its variant ladder always tries
    # first+last WITHOUT the middle right after the middle-scoped search.
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
