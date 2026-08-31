"""Pull Trustee's Sale (foreclosure) notices from mddcpublicnotices.com to a CSV.

mddcpublicnotices.com is the MD/DC/DE public-notice platform -- verified live
2026-08-21 to be the SAME ASP.NET WebForms vendor software as tnpublicnotice.com
(identical session-in-URL pattern `/(S({guid}))/`, identical "Notices for the
past 12 months" retention copy, identical rbRange/txtDateFrom/txtDateTo field
names, even a leftover "Public Notice Arkansas" logo alt-text from the shared
template). Unlike tnpublicnotice.com there is no CAPTCHA on the search or
notice pages, so this runs on Firecrawl (executeJavascript + click actions
inside one browser session) instead of Playwright + 2Captcha.

Flow, all inside ONE Firecrawl /v2/scrape actions sequence per saved search
(a fresh login per saved search -- simpler and more robust than depending on
undocumented cross-call session persistence):
  1. Log into Smart Search (authenticate.aspx) with MDDC_EMAIL / MDDC_PASSWORD.
  2. Select the given Saved Search (loads its keyword criteria, e.g. "Trustee's
     Sale") from the ddlSavedSearches dropdown on Search.aspx.
  3. Check the target county checkboxes (OR'd together by the site) and set
     the "in the last N days" date window.
  4. Click Search, then click "Next page" up to --max-pages times, scraping
     the grid after each page.

There is NO per-row county field in the grid (verified live: the row's
`County:` div is empty), so county is inferred from city/state/zip parsed out
of the free-text notice body -- the same "no structured fields, everything is
in the notice text" situation documented for tnpublicnotice.com. Each row's
notice text is the GRID SNIPPET, not the full notice (the site truncates with
"click 'view' to open the full text", which is a separate per-row postback
not fetched here) -- good enough for address/notice-type extraction, not a
verbatim copy of the published notice.

PAGINATION FIX, verified live 2026-08-21: Firecrawl's own "click" action on
the pager's "Next" button silently did nothing (HTTP 200, page number never
advanced) no matter how long the wait or whether the button was scrolled
into view first. The grid lives in an ASP.NET UpdatePanel (async partial
postback via PageRequestManager), and the root cause turned out to be the
click itself: Firecrawl's click action does not reliably register with this
framework's client-side postback interception. Dispatching a full synthetic
mouse sequence instead (mouseover/mousedown/mouseup/click, each a real
MouseEvent with bubbles/cancelable/view/clientX/clientY set, via
executeJavascript) DOES advance the page every time -- confirmed via the
lblCurrentPage counter. The same synthetic-click fix is required for the
per-notice "View" button below; a selector-based Firecrawl click on that
button also does nothing.

Each notice's grid row shows only a TRUNCATED snippet ("click 'view' to open
the full text"); the real address, loan principal amount, and auction date
frequently fall past that truncation point. Clicking "View" navigates to a
real `Details.aspx?ID=<n>` page -- but that page is behind a Cloudflare-style
"Verify you are human" challenge gate (verified live: the search/grid pages
have NO such gate, only the per-notice detail page does), the same class of
protection captcha_solver.py already handles for tnpublicnotice.com.
Firecrawl has no built-in CAPTCHA/Turnstile solving (confirmed against its
own docs), so full-text fetch is planned to route through Scrapfly's
`asp=True` (already proven against this exact gate style for TN) instead --
NOT YET WIRED UP. `SCRAPFLY_KEY` in `.env` is a real key (verified live
2026-08-21: a Scrapfly login to mddcpublicnotices.com succeeds), but a
SEPARATE Scrapfly `.scrape()` call to `Details.aspx?ID=<n>` -- even reusing
the same `session=` string -- lands unauthenticated and still shows "You must
complete the challenge". This is the exact lesson already documented for the
TN Scrapfly integration in `scrapfly_client.py`: "every Scrapfly scrape gets
a fresh ASP.NET cookieless session... one browser context is one session, so
the whole walk has to be one call." The fix is almost certainly the same one
used there (`fetch_notice_via_search`): do login AND the Details.aspx fetch
inside ONE Scrapfly call via a `js_scenario` (fill login form, submit, then
navigate/click through to the notice), not two separate `.scrape()` calls.
Not yet built. Until it is, this script reports address/loan/auction/county
on a best-effort basis from the grid snippet alone.

This is a first cut: scrape to CSV only, no enrichment or DataSift upload.

Usage:
    python src/scripts/mddc_trustee_sale_pull.py
    python src/scripts/mddc_trustee_sale_pull.py --saved-searches 41,43 --days 30 \
        --counties "Anne Arundel,Baltimore County,Calvert,Carroll,Charles,Frederick,Montgomery" \
        --max-pages 15 --out output/mddc_trustee_sale.csv

    # Verify the configured saved-search ids still point at "Trustee's Sale" on
    # the live site (and see what OTHER document types the account has saved),
    # the same discipline as `python src/main.py list-searches` for TN:
    python src/scripts/mddc_trustee_sale_pull.py --list-searches
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"
ZIP_COUNTY_CACHE_PATH = ROOT / "data" / "zip_county_cache.json"  # shared with va_trustee_sale_pull.py
LOGIN_URL = "https://mddcpublicnotices.com/authenticate.aspx"

# County checkbox index on Search.aspx's #ctl00_ContentPlaceHolder1_as1_lstCounty
# list (verified live 2026-08-21; the site can renumber this list if it adds/
# removes a jurisdiction, so re-verify against a fresh Search.aspx pull if a
# county below stops matching).
COUNTY_CHECKBOX_INDEX = {
    "Allegany": 0,
    "Anne Arundel": 1,
    "Baltimore City": 2,
    "Baltimore County": 3,
    "Calvert": 4,
    "Caroline": 5,
    "Carroll": 6,
    "Cecil": 7,
    "Charles": 8,
    "Dorchester": 9,
    "Frederick": 10,
    "Garrett": 11,
    "Harford": 12,
    "Howard": 13,
    "Kent (DE)": 14,
    "Kent": 15,
    "Montgomery": 16,
    "New Castle": 17,
    "Prince George's": 18,
    "Queen Anne's": 19,
    "Somerset": 20,
    "St. Marys": 21,
    "Sussex": 22,
    "Talbot": 23,
    "Washington": 24,
    "Washington DC": 25,
    "Wicomico": 26,
    "Worcester": 27,
}

DEFAULT_COUNTIES = [
    "Anne Arundel",
    "Baltimore County",
    "Calvert",
    "Carroll",
    "Charles",
    "Frederick",
    "Montgomery",
    "Washington DC",
]

# NOTE on Virginia: mddcpublicnotices.com has NO Virginia county checkbox at
# all (verified live 2026-08-21 -- all 28 checkboxes on Search.aspx are MD/
# DE/DC only). "Add VA to the counties" isn't selectable on this platform.
# Real VA content DOES leak into results anyway (e.g. a genuine Orange County,
# VA trustee sale surfaced under a MD-only county filter), so VA is kept
# in-footprint below rather than dropped -- this is incidental coverage, not
# a deliberate VA search. Deliberate VA coverage would need Virginia's own
# public-notice site (same vendor family is plausible given the shared
# template, but no URL has been confirmed -- ask before guessing one).

# Saved Searches named "Trustee's Sale" on the account. 2026-08-21 there were
# two under different ids (41 and 43); `--list-searches` on 2026-08-28 found
# 41 GONE from the live dropdown (only "-1 Saved Searches" and 43 remain), so a
# run still configured for 41 sets a value the <select> does not have and
# scrapes whatever the unselected form returns. Only 43 is configured now.
DEFAULT_SAVED_SEARCHES = ["43"]

# Expected LIVE label for each configured saved-search id, from the same
# 2026-08-21 verification. `--list-searches` re-reads the actual dropdown and
# cross-checks it against this -- the same discipline as the TN workflow's
# `python src/main.py list-searches`: a saved search that gets renamed or
# renumbered on the site scrapes zero notices and looks exactly like a quiet
# day, so verify against the live site rather than trusting a hardcoded id.
SAVED_SEARCH_LABELS = {
    "43": "Trustee's Sale",
}

# Matches both the two-line notice-caption form ("STREET\nCITY, ST ZIP") and
# the single-line form ("STREET, CITY, ST ZIP").
STATE_NAMES = {"MARYLAND": "MD", "VIRGINIA": "VA", "DELAWARE": "DE",
               "DISTRICT OF COLUMBIA": "DC", "WASHINGTON DC": "DC"}
STATE_ALT = r"MD|DC|DE|VA|Maryland|Virginia|Delaware|District of Columbia"

# Requires a comma OR newline between street and city -- confirmed live
# 2026-08-27 (Basem) this misses real addresses on two real formats:
# "123 Example Road, \nMidland, Virginia 22728" (spelled-out state name,
# not the MD/DC/DE/VA abbreviation this regex originally required) and
# "1234 SAMPLE MEADOWS DRIVE MANASSAS, VA 20109" (no delimiter at all
# between street and city -- both run together on one line). The state-name
# gap is fixed inline (STATE_ALT); the no-delimiter gap needs a different
# strategy entirely -- see ADDRESS_FALLBACK_RE below.
ADDRESS_RE = re.compile(
    r"([0-9][A-Za-z0-9 .,#\-']{4,60}?)\s*(?:\n|,)\s*"
    rf"([A-Za-z .'-]{{2,40}}),\s*({STATE_ALT})\s*(\d{{5}})",
    re.I,  # a spelled-out state name shows up ALL CAPS as often as title case -- confirmed live on VA's twin script
)

# Fallback for "NUMBER ... CITY, STATE ZIP" with no delimiter between street
# and city (e.g. "1234 SAMPLE MEADOWS DRIVE MANASSAS, VA 20109"). Captures
# everything from the leading house number up to the state/zip as one blob,
# then splits on the LAST recognized street-suffix word -- the exact
# technique already proven for the identical ambiguity in
# md_register_of_wills_pull.STREET_SUFFIXES / _split_address_blob (e.g.
# "8590 OAK HILL DR OWINGS" -> street "8590 OAK HILL DR", city "OWINGS").
ADDRESS_FALLBACK_RE = re.compile(
    rf"([0-9][A-Za-z0-9 .,#\-']{{4,60}})\s*,?\s*({STATE_ALT})\s*(\d{{5}})",
    re.I,
)

try:
    from md_register_of_wills_pull import STREET_SUFFIXES  # noqa: E402
except ImportError:  # running as a script with a different sys.path setup
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from md_register_of_wills_pull import STREET_SUFFIXES  # noqa: E402

# These often appear before the grid snippet's truncation point (loan
# principal especially -- it's usually named in the SAME sentence as the
# Deed of Trust date, near the top of the notice); auction date usually
# appears much later in the notice body and is frequently past the cutoff,
# so it comes back blank more often than the other fields until full-text
# fetch (Scrapfly) is wired up -- see module docstring.
LOAN_PRINCIPAL_RE = re.compile(
    r"(?:original\s+)?principal\s+(?:amount|balance)\s+of\s+\$([\d,]+(?:\.\d{2})?)", re.I)

AUCTION_DATE_RE = re.compile(
    r"(?:on|dated for)\s+"
    r"((?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+\d{4})",
)

# Only a LITERAL "<Name> County" mention in the notice text counts -- never
# inferred from city, matching this codebase's established rule (see the
# "courthouse became the subject property" probate lesson in CLAUDE.md):
# a guess that's wrong is worse than a blank field.
COUNTY_MENTION_RE = re.compile(
    r"([A-Z][A-Za-z.' ]+?)\s+County(?:,\s*(?:Maryland|MD|Virginia|VA|Delaware|DE))?\b")

# Notice ID extraction, carried over from the two proven TN patterns for this
# same ASP.NET WebForms vendor platform (not yet independently confirmed
# against live MDDC HTML -- parse_grid_html logs a miss count so a bad
# selector shows up as a visible warning rather than a silent zero).
VIEW_BUTTON_ID_RE = re.compile(r"Details\.aspx\?SID=[a-z0-9]+&(?:amp;)?ID=(\d+)", re.I)
BARE_ID_RE = re.compile(r"[?&]ID=(\d+)")


def parse_notice_id(row_html: str):
    m = VIEW_BUTTON_ID_RE.search(row_html)
    if m:
        return m.group(1)
    m = BARE_ID_RE.search(row_html)
    return m.group(1) if m else None


def parse_loan_principal(text: str):
    m = LOAN_PRINCIPAL_RE.search(text)
    return m.group(1) if m else ""


def parse_auction_date(text: str):
    m = AUCTION_DATE_RE.search(text)
    return m.group(1) if m else ""


# Words that prove a COUNTY_MENTION_RE hit is glue text, not a county name --
# confirmed live 2026-08-27, "recorded in the Clerk's Office of the Circuit
# Court of the County of Fauquier" style VA phrasing ("County of X" rather
# than "X County") made the regex match "the" as the county. Rejecting these
# rather than teaching the regex every VA phrasing variant means a bad match
# falls through to blank, which the ZIP-geocode fallback in main() then
# fills in correctly -- consistent with this file's own rule that a wrong
# guess is worse than a blank field.
COUNTY_MENTION_JUNK = {"the", "of", "clerk's", "clerk", "circuit", "court", "office"}


def parse_county_mention(text: str):
    m = COUNTY_MENTION_RE.search(text)
    if not m:
        return ""
    candidate = m.group(1).strip()
    # Token-level check, not whole-string equality: "Clerk's Office of the
    # Circuit Court of the" is the full (wrong) candidate the regex actually
    # produces on VA "County of X" phrasing (see COUNTY_MENTION_JUNK above),
    # not a bare "the" -- a whole-string check against the junk set missed
    # this live, since the match is never just one word.
    words = {w.lower().rstrip(".,'") for w in candidate.split()}
    if words & COUNTY_MENTION_JUNK:
        return ""
    return candidate


def _load_zip_county_cache() -> dict:
    if ZIP_COUNTY_CACHE_PATH.exists():
        try:
            return json.loads(ZIP_COUNTY_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_zip_county_cache(cache: dict) -> None:
    ZIP_COUNTY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ZIP_COUNTY_CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def resolve_county_by_zip(zip5: str, cache: dict) -> str:
    """County fallback for when the notice body never names one (the common
    case: most Trustee's Sale notices just give a street/city/zip, with no
    'County' word anywhere -- confirmed live 2026-08-27, county was blank on
    10 of 11 clean rows in a real run). This is a GEOCODE, not a guess:
    zippopotam.us resolves the ZIP to a lat/lon, then the FCC's own Census
    Area API resolves that point to its real county -- both are authoritative
    government/postal data, the same evidentiary bar this codebase applies
    everywhere else ('a guess that's wrong is worse than a blank field' does
    not forbid a real geocode, only a name-based inference). Cached to
    data/zip_county_cache.json since the same handful of ZIPs repeat
    across notices and re-hitting two APIs per row is wasteful."""
    if not zip5:
        return ""
    zip5 = zip5[:5]
    if zip5 in cache:
        return cache[zip5]
    county = ""
    try:
        r = requests.get(f"https://api.zippopotam.us/us/{zip5}", timeout=10)
        if r.status_code == 200:
            place = r.json()["places"][0]
            lat, lon = place["latitude"], place["longitude"]
            r2 = requests.get("https://geo.fcc.gov/api/census/area",
                               params={"lat": lat, "lon": lon, "format": "json"}, timeout=10)
            if r2.status_code == 200:
                results = r2.json().get("results") or []
                if results:
                    name = results[0]["county_name"]
                    # FCC returns "District of Columbia" as its own county_name
                    # for DC -- strip the trailing " County" MD/VA carry so the
                    # column matches the free-text-parsed style elsewhere.
                    county = name[:-len(" County")] if name.endswith(" County") else name
    except requests.exceptions.RequestException:
        county = ""
    cache[zip5] = county
    return county


# Checked FIRST, ahead of the "foreclosure" rule below: an ORDER NISI /
# ratification notice is a court filing CONFIRMING a sale that already
# happened, not a listing describing a property for sale -- it still
# contains "trustee's sale" / "substitute trustee" language (the auction it's
# ratifying), which is exactly why it was misclassified as a real foreclosure
# lead before this rule existed. Confirmed live 2026-08-27 (Basem): these
# notices never carry a property address at all (a Circuit Court ORDER NISI
# for Charles County ran "...ORDERED...that the sale o[f]..." with nothing
# more specific), matching Basem's own read that anything filed "in the
# circuit court of X county" that will be "ratified and confirmed" should be
# dropped, not treated as a lead.
ORDER_NISI_RE = re.compile(
    r"order\s+nisi|ratified\s+and\s+confirmed|will\s+be\s+ratified"
    # Broader structural fingerprint, needed because the grid snippet
    # truncates BEFORE the word "ratified" on some of these -- confirmed
    # live 2026-08-27, a Charles County notice cut off at "...ORDERED this
    # 13th day of August, 2026 by the Circuit Court for Charles County,
    # Maryland, that the sale o..." with no "ratified"/"nisi" text visible
    # yet. "ORDERED this <date> by the Circuit Court for <county>...that the
    # sale" is this notice type's own boilerplate opening, distinct from a
    # real Trustee's Sale listing (which describes the property/deed of
    # trust, not a court order about a sale that already happened).
    r"|ORDERED\s+this\s+.{0,60}?\s+by\s+the\s+Circuit\s+Court\s+for\s+.{0,40}?County.{0,20}?that\s+the\s+sale",
    re.I | re.S,
)

NOTICE_TYPE_RULES = [
    ("order_nisi", ORDER_NISI_RE),
    ("foreclosure", re.compile(r"trustee'?s?\s+sale|substitute trustee|deed of trust|power of sale", re.I)),
    ("tax_sale", re.compile(r"tax\s+sale|tax\s+lien\s+sale", re.I)),
    ("probate", re.compile(r"notice to creditors|letters testamentary|personal representative|estate of", re.I)),
]


def classify_notice_type(text: str) -> str:
    for label, pattern in NOTICE_TYPE_RULES:
        if pattern.search(text):
            return label
    return "other"


def _normalize_state(raw: str) -> str:
    return STATE_NAMES.get(raw.strip().upper(), raw.strip().upper())


def parse_address(text: str):
    """Return the SUBJECT PROPERTY address, not the law firm's letterhead.

    These notices open with the filing firm's own office address (which
    frequently also matches the address pattern) and name the actual property
    later, right before "Under a power of sale..." / "Default having been
    made...". Taking the FIRST regex match returns the firm's office almost
    every time (verified live 2026-08-21: 4 of 5 sampled notices). Taking the
    LAST match returns the property in every sampled case instead.
    """
    matches = list(ADDRESS_RE.finditer(text))
    if matches:
        street, city, state, zip5 = matches[-1].groups()
        return {
            "street": " ".join(street.split()),
            "city": city.strip().title(),
            "state": _normalize_state(state),
            "zip": zip5,
        }
    # Fallback: no comma/newline between street and city at all (confirmed
    # live 2026-08-27, "1234 SAMPLE MEADOWS DRIVE MANASSAS, VA 20109" has
    # none). Split the captured blob on the LAST recognized street-suffix
    # word instead of requiring a delimiter -- same technique already proven
    # for this exact ambiguity in md_register_of_wills_pull._split_address_blob.
    fallback_matches = list(ADDRESS_FALLBACK_RE.finditer(text))
    if not fallback_matches:
        return None
    blob, state, zip5 = fallback_matches[-1].groups()
    tokens = [t.rstrip(",") for t in blob.split()]
    split_idx = None
    for i, tok in enumerate(tokens):
        if tok.upper().rstrip(".") in STREET_SUFFIXES:
            split_idx = i
    if split_idx is None:
        return None  # no recognizable suffix -- don't guess where street ends
    street = " ".join(tokens[: split_idx + 1])
    city = " ".join(tokens[split_idx + 1 :])
    if not city:
        return None
    return {
        "street": street.strip(),
        "city": city.strip().title(),
        "state": _normalize_state(state),
        "zip": zip5,
    }


def load_credentials() -> dict:
    env = dotenv_values(str(ENV_PATH))
    email = env.get("MDDC_EMAIL")
    pw = env.get("MDDC_PASSWORD")
    firecrawl_key = env.get("FIRECRAWL_API_KEY")
    missing = [
        name
        for name, val in (("MDDC_EMAIL", email), ("MDDC_PASSWORD", pw), ("FIRECRAWL_API_KEY", firecrawl_key))
        if not val
    ]
    if missing:
        raise RuntimeError(f"Missing from .env: {', '.join(missing)}")
    return {"email": email, "password": pw, "firecrawl_key": firecrawl_key}


def synth_click_js(selector: str) -> str:
    """JS for a full synthetic mouse-event click, which this site's ASP.NET
    UpdatePanel postback interception actually honors (a plain Firecrawl
    "click" action does not -- see module docstring, verified live 2026-08-21).
    """
    return f"""
        var el = document.querySelector('{selector}');
        if (el) {{
            var rect = el.getBoundingClientRect();
            var x = rect.left + rect.width/2, y = rect.top + rect.height/2;
            ['mouseover','mousedown','mouseup','click'].forEach(function(type) {{
                el.dispatchEvent(new MouseEvent(type, {{bubbles:true, cancelable:true, view:window, clientX:x, clientY:y}}));
            }});
        }}
    """


def build_actions(saved_search_value: str, counties: list, days: int, max_pages: int,
                   date_from: str = "", date_to: str = "", hide_read_notices: bool = False) -> list:
    county_js = "\n".join(
        f"""(function(){{
            var cb = document.querySelector('#ctl00_ContentPlaceHolder1_as1_lstCounty_{COUNTY_CHECKBOX_INDEX[c]}');
            if (cb) {{ cb.checked = true; cb.dispatchEvent(new Event('click',{{bubbles:true}})); cb.dispatchEvent(new Event('change',{{bubbles:true}})); }}
        }})();"""
        for c in counties
        if c in COUNTY_CHECKBOX_INDEX
    )

    # Explicit range (rbRange/txtDateFrom/txtDateTo) when both dates are given,
    # otherwise fall back to the "last N days" control -- same field names as
    # the identical TN vendor platform (src/scraper.py:270-293), native setter
    # + input/change so the page's own JS notices the value.
    if date_from and date_to:
        date_js = f"""
            var setVal = function(el, v) {{
                var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                setter.call(el, v);
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
            }};
            var rr = document.querySelector('#ctl00_ContentPlaceHolder1_as1_rbRange');
            if (rr) {{ rr.checked = true; rr.dispatchEvent(new Event('click',{{bubbles:true}})); rr.dispatchEvent(new Event('change',{{bubbles:true}})); }}
            var df = document.querySelector('#ctl00_ContentPlaceHolder1_as1_txtDateFrom');
            if (df) {{ setVal(df, '{date_from}'); }}
            var dt = document.querySelector('#ctl00_ContentPlaceHolder1_as1_txtDateTo');
            if (dt) {{ setVal(dt, '{date_to}'); }}
        """
    else:
        date_js = f"""
            var dr = document.querySelector('#ctl00_ContentPlaceHolder1_as1_rbLastNumDays');
            if (dr) {{ dr.checked = true; dr.dispatchEvent(new Event('change',{{bubbles:true}})); }}
            var nd = document.querySelector('#ctl00_ContentPlaceHolder1_as1_txtLastNumDays');
            if (nd) {{ nd.value = '{days}'; nd.dispatchEvent(new Event('input',{{bubbles:true}})); nd.dispatchEvent(new Event('change',{{bubbles:true}})); }}
        """

    hide_read_js = ""
    if hide_read_notices:
        hide_read_js = """
            var hr = document.querySelector('#ctl00_ContentPlaceHolder1_cbHideReadNotices');
            if (hr) { hr.checked = true; hr.dispatchEvent(new Event('click',{bubbles:true})); hr.dispatchEvent(new Event('change',{bubbles:true})); }
        """

    actions = [
        {"type": "click", "selector": "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_txtEmailAddress"},
        {"type": "write", "text": "{{email}}"},
        {"type": "click", "selector": "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_txtPassword"},
        {"type": "write", "text": "{{password}}"},
        {"type": "click", "selector": "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_btnAuth"},
        {"type": "wait", "milliseconds": 4000},
        {
            "type": "executeJavascript",
            "script": f"""
                var sel = document.querySelector('#ctl00_ContentPlaceHolder1_as1_ddlSavedSearches');
                sel.value = '{saved_search_value}';
                sel.dispatchEvent(new Event('change', {{bubbles: true}}));
            """,
        },
        {"type": "wait", "milliseconds": 5000},
        {
            "type": "executeJavascript",
            "script": f"""
                {county_js}
                {date_js}
                {hide_read_js}
            """,
        },
        {
            "type": "executeJavascript",
            "script": synth_click_js("#ctl00_ContentPlaceHolder1_as1_btnGo"),
        },
        {"type": "wait", "milliseconds": 5000},
        {"type": "scrape"},
    ]
    for _ in range(max(0, max_pages - 1)):
        actions.append({
            "type": "executeJavascript",
            "script": synth_click_js("#ctl00_ContentPlaceHolder1_WSExtendedGrid1_GridView1_ctl01_btnNext"),
        })
        actions.append({"type": "wait", "milliseconds": 4500})
        actions.append({"type": "scrape"})
    return actions


def run_saved_search(creds: dict, saved_search_value: str, counties: list, days: int, max_pages: int,
                      date_from: str = "", date_to: str = "", hide_read_notices: bool = False) -> list:
    actions = build_actions(saved_search_value, counties, days, max_pages, date_from, date_to, hide_read_notices)
    # Substitute credentials into the write actions without ever logging them.
    for a in actions:
        if a.get("type") == "write" and a["text"] == "{{email}}":
            a["text"] = creds["email"]
        elif a.get("type") == "write" and a["text"] == "{{password}}":
            a["text"] = creds["password"]

    # Scaled with max_pages: ~4.5s of scripted wait per page alone means the
    # old flat 300s ceiling would cut off a 60+ page run before it finished.
    timeout = max(300, 90 + max_pages * 10)
    resp = requests.post(
        FIRECRAWL_URL,
        headers={"Authorization": f"Bearer {creds['firecrawl_key']}", "Content-Type": "application/json"},
        json={"url": LOGIN_URL, "actions": actions, "formats": ["html"]},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    scrapes = (data.get("actions") or {}).get("scrapes") or []
    return [s.get("html", "") for s in scrapes if s.get("html")]


def list_saved_searches(creds: dict) -> list[tuple[str, str]]:
    """Log in and return every (value, label) pair in the Saved Searches
    dropdown on Search.aspx -- the MDDC equivalent of the TN workflow's
    `python src/main.py list-searches`.

    This is the check the site itself won't give you for free: the pull
    above trusts DEFAULT_SAVED_SEARCHES / SAVED_SEARCH_LABELS blindly, and a
    saved search that gets renamed, renumbered, or deleted on the account
    scrapes zero rows while looking exactly like a quiet day -- the same
    "mistyped saved search" failure mode documented for tnpublicnotice.com.
    Run this whenever a saved search is added/edited on the account, or
    whenever a pull's row count looks suspiciously low.

    Also surfaces any OTHER document types already saved on the account
    (Tax Sale, Probate, Foreclosure, etc.) that aren't pulled yet -- the
    live dropdown is the only authoritative list of what's actually there.
    """
    actions = [
        {"type": "click", "selector": "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_txtEmailAddress"},
        {"type": "write", "text": creds["email"]},
        {"type": "click", "selector": "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_txtPassword"},
        {"type": "write", "text": creds["password"]},
        {"type": "click", "selector": "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_btnAuth"},
        {"type": "wait", "milliseconds": 4000},
        {"type": "scrape"},
    ]
    resp = requests.post(
        FIRECRAWL_URL,
        headers={"Authorization": f"Bearer {creds['firecrawl_key']}", "Content-Type": "application/json"},
        json={"url": LOGIN_URL, "actions": actions, "formats": ["html"]},
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    scrapes = (data.get("actions") or {}).get("scrapes") or []
    if not scrapes:
        return []
    soup = BeautifulSoup(scrapes[-1].get("html", ""), "html.parser")
    select = soup.select_one("#ctl00_ContentPlaceHolder1_as1_ddlSavedSearches")
    if not select:
        return []
    pairs = []
    for opt in select.find_all("option"):
        value = (opt.get("value") or "").strip()
        label = opt.get_text(strip=True)
        if value and label:
            pairs.append((value, label))
    return pairs


CURRENT_PAGE_RE = re.compile(r'lblCurrentPage"[^>]*>\s*(\d+)\s*<')


def detected_page_number(html: str):
    m = CURRENT_PAGE_RE.search(html)
    return int(m.group(1)) if m else None


def parse_grid_html(html: str, saved_search_value: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    missing_id = 0
    for nested in soup.select("table.nested"):
        info = nested.select_one("td.info")
        text_cell = nested.select_one("td[colspan='3']")
        if not info or not text_cell:
            continue
        publication_el = info.select_one("strong")
        publication = publication_el.get_text(strip=True) if publication_el else ""
        info_text = info.get_text(" ", strip=True)
        pub_date_m = re.search(r"Published:\s*([\d/]+)", info_text)
        published = pub_date_m.group(1) if pub_date_m else ""
        notice_text = text_cell.get_text("\n", strip=True)
        if not notice_text:
            continue
        addr = parse_address(notice_text) or {}
        notice_id = parse_notice_id(str(nested))
        if not notice_id:
            missing_id += 1
        rows.append(
            {
                "notice_id": notice_id or "",
                "publication": publication,
                "date_published": published,
                "notice_type": classify_notice_type(notice_text),
                "street": addr.get("street", ""),
                "city": addr.get("city", ""),
                "state": addr.get("state", ""),
                "zip": addr.get("zip", ""),
                "county": parse_county_mention(notice_text),
                "loan_principal": parse_loan_principal(notice_text),
                "auction_date": parse_auction_date(notice_text),
                "saved_search": saved_search_value,
                "notice_text_snippet": notice_text[:2000],
                "source_url": "https://mddcpublicnotices.com/Search.aspx",
            }
        )
    if missing_id:
        print(f"  WARNING: {missing_id} of {len(rows)} row(s) on this page had no extractable notice_id "
              f"(neither the View-button pattern nor the bare ID= pattern matched)")
    return rows


IN_FOOTPRINT_STATES = {"MD", "DC", "DE", "VA"}


def filter_footprint(rows: list) -> tuple:
    """Drop rows whose parsed address isn't MD/DC/DE/VA.

    Live 2026-08-21: checking only MD county boxes still returned a Prince
    William County, VA notice AND a genuine Orange County, VA trustee sale
    (the site's county filter doesn't hard-exclude every out-of-footprint
    result, and there is no VA checkbox to select in the first place -- see
    the note above DEFAULT_COUNTIES). VA is real content, not noise, and
    2026-08-27 (Basem) confirmed it should be KEPT, not dropped -- a
    same-day attempt to exclude it (on the theory that va_trustee_sale_pull.py
    already covers VA) was reversed the same day. This only drops a state
    truly outside the MD/DC/DE/VA area (e.g. a law firm HQ in another state
    with no in-area property mentioned at all). Rows with no parsed address
    are kept -- the notice snippet was truncated before any address
    appeared, not proof the property is out of scope.
    """
    kept, dropped = [], []
    for r in rows:
        if r["state"] and r["state"] not in IN_FOOTPRINT_STATES:
            dropped.append(r)
        else:
            kept.append(r)
    return kept, dropped


def filter_notice_type(rows: list, keep: set = frozenset({"foreclosure"})) -> tuple:
    """Drop rows whose classified notice_type isn't one we're pulling.

    Added 2026-08-27 (Basem): a live test run of saved searches 41/43
    ("Trustee's Sale") returned an OCC bank-merger notice, a tender-offer
    notice, and a healthcare Certificate-of-Need filing alongside real
    foreclosure notices -- classify_notice_type correctly labeled them
    "other" (none of them match the foreclosure/tax_sale/probate regexes),
    but nothing dropped them before they landed in the output. These
    saved searches are scoped to Trustee's Sale, so anything that isn't a
    "foreclosure" notice is off-topic noise for this pull, not a different
    kind of lead to keep -- tax_sale/probate saved searches are their own
    future pull, not something to accept incidentally here.
    """
    kept, dropped = [], []
    for r in rows:
        if r["notice_type"] in keep:
            kept.append(r)
        else:
            dropped.append(r)
    return kept, dropped


def dedupe(rows: list) -> list:
    """Collapse repeats of the same notice.

    THE ADDRESS IS NOT ALWAYS AN IDENTITY. Keying on (street, city, date) alone works for
    Trustee's Sale, where every notice names a property, but it silently merges every
    ADDRESSLESS row published on the same day into one -- they all share the key
    ("", "", date). That costs rows here whenever an address fails to parse, and it is
    catastrophic on any other notice type: the sibling VA script lost 9 of 10 Estate Claims
    rows to exactly this, and reported success. `--list-searches` exists to find those
    other saved searches (Tax Sale, Probate, Foreclosure), so the trap is one command away.

    This site DOES publish a stable per-notice id, so prefer it; fall back to the address,
    then to the notice text, which is what actually distinguishes one addressless notice
    from another.
    """
    seen = set()
    out = []
    for r in rows:
        if r.get("notice_id"):
            key = ("id", r["notice_id"])
        elif r["street"] or r["city"]:
            key = ("addr", r["street"].lower(), r["city"].lower(), r["date_published"])
        else:
            body = (r.get("notice_text_snippet") or "").strip().lower()
            key = ("text", r["date_published"], body[:400])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--saved-searches", default=",".join(DEFAULT_SAVED_SEARCHES),
                     help="Comma-separated ddlSavedSearches option values.")
    ap.add_argument("--counties", default=",".join(DEFAULT_COUNTIES))
    ap.add_argument("--days", type=int, default=30,
                     help="Lookback window; ignored when --date-from/--date-to are both given.")
    ap.add_argument("--date-from", default="", help="M/D/YYYY. Requires --date-to.")
    ap.add_argument("--date-to", default="", help="M/D/YYYY. Requires --date-from.")
    # Default OFF (flipped 2026-08-31): hiding notices the account has already viewed on the
    # site makes a re-run look like a quiet day instead of a coverage gap -- the same silent
    # failure mode the zero-notices-is-a-FAILURE rule exists for. Opt in explicitly.
    ap.add_argument("--hide-read-notices", dest="hide_read_notices", action="store_true", default=False)
    ap.add_argument("--no-hide-read-notices", dest="hide_read_notices", action="store_false")
    ap.add_argument("--max-pages", type=int, default=10,
                     help="Pages per saved search (newest-first). The synthetic-click fix makes this "
                          "reliable now; each page costs one Firecrawl action round-trip, so a broad "
                          "search 60-80 pages deep still needs multiple runs or a higher value here.")
    ap.add_argument("--out", default="output/mddc_trustee_sale.csv")
    ap.add_argument("--standardize", action="store_true",
                    help="Run Smarty USPS standardization over the scraped rows "
                         "(adds std_* / rdi / vacant / lat-lon columns). Needs "
                         "SMARTY_AUTH_ID and SMARTY_AUTH_TOKEN in .env. Original "
                         "scraped columns are never overwritten.")
    ap.add_argument("--list-searches", action="store_true",
                     help="Log in, print every Saved Search on the account (value + label), "
                          "flag any --saved-searches id that no longer matches the live "
                          "dropdown, and exit. Mirrors `python src/main.py list-searches` on "
                          "the TN site -- verify a saved search before trusting it, since a "
                          "renamed/deleted one scrapes zero rows and looks like a quiet day.")
    args = ap.parse_args()

    if bool(args.date_from) != bool(args.date_to):
        raise SystemExit("--date-from and --date-to must be given together")

    creds = load_credentials()

    if args.list_searches:
        configured = [s.strip() for s in args.saved_searches.split(",") if s.strip()]
        pairs = list_saved_searches(creds)
        if not pairs:
            print("No saved searches found (login failed or dropdown missing).")
            raise SystemExit(1)
        live_by_value = dict(pairs)
        print(f"\n{len(pairs)} saved search(es) on mddcpublicnotices.com:\n")
        for value, label in pairs:
            mark = "[configured]" if value in configured else "[ not used  ]"
            expected = SAVED_SEARCH_LABELS.get(value)
            drift = f"   !! expected {expected!r}" if expected and expected != label else ""
            print(f"  {mark}  id={value:>4}  {label}{drift}")
        missing = [v for v in configured if v not in live_by_value]
        if missing:
            print("\nCONFIGURED BUT NOT ON THE SITE (these scrape nothing):")
            for value in missing:
                print(f"  !!  id={value}  (expected {SAVED_SEARCH_LABELS.get(value, '?')!r})")
            raise SystemExit(1)
        print()
        return

    counties = [c.strip() for c in args.counties.split(",") if c.strip()]
    unknown = [c for c in counties if c not in COUNTY_CHECKBOX_INDEX]
    if unknown:
        raise SystemExit(f"Unknown county name(s): {unknown}. Known: {sorted(COUNTY_CHECKBOX_INDEX)}")

    all_rows = []
    for sv in [s.strip() for s in args.saved_searches.split(",") if s.strip()]:
        if args.date_from and args.date_to:
            print(f"Saved search {sv}: logging in and searching {len(counties)} counties, "
                  f"{args.date_from} to {args.date_to}...")
        else:
            print(f"Saved search {sv}: logging in and searching {len(counties)} counties, last {args.days} days...")
        htmls = run_saved_search(creds, sv, counties, args.days, args.max_pages,
                                  args.date_from, args.date_to, args.hide_read_notices)
        page_numbers = [detected_page_number(h) for h in htmls]
        print(f"  got {len(htmls)} page(s) of HTML, detected page numbers: {page_numbers}")
        if len(set(p for p in page_numbers if p is not None)) < len(htmls) - 1:
            print("  WARNING: pagination did not advance for every click -- some pages were re-captured. "
                  "Rows are still de-duped, but coverage of this saved search is incomplete.")
        for html in htmls:
            all_rows.extend(parse_grid_html(html, sv))
        time.sleep(1)

    in_footprint, out_of_footprint = filter_footprint(all_rows)
    if out_of_footprint:
        print(f"  dropped {len(out_of_footprint)} out-of-footprint (non MD/DC/DE/VA) row(s), e.g. "
              f"{out_of_footprint[0]['city']}, {out_of_footprint[0]['state']}")
    on_type, off_type = filter_notice_type(in_footprint)
    if off_type:
        from collections import Counter
        counts = Counter(r["notice_type"] for r in off_type)
        print(f"  dropped {len(off_type)} off-type row(s): {dict(counts)}")
    rows = dedupe(on_type)
    # Dropped rows go to a sidecar CSV, not the void: the order_nisi rule matches
    # "will be ratified" boilerplate that also appears in genuine trustee-sale ads, so a
    # misclassified lead must stay reviewable instead of vanishing into a count.
    dropped_rows = out_of_footprint + off_type
    if dropped_rows:
        dropped_path = (ROOT / args.out).with_name(Path(args.out).stem + "_dropped.csv")
        dropped_path.parent.mkdir(parents=True, exist_ok=True)
        drop_fields = ["notice_id", "publication", "date_published", "notice_type", "street",
                       "city", "state", "zip", "county", "loan_principal", "auction_date",
                       "saved_search", "notice_text_snippet", "source_url"]
        with dropped_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=drop_fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(dropped_rows)
        print(f"  wrote {len(dropped_rows)} dropped row(s) to {dropped_path} for review")
    print(f"{len(all_rows)} raw rows, {len(rows)} after footprint filter + notice-type filter + de-dup")

    zip_cache = _load_zip_county_cache()
    geocoded = 0
    for r in rows:
        if not r["county"] and r["zip"]:
            resolved = resolve_county_by_zip(r["zip"], zip_cache)
            if resolved:
                r["county"] = resolved
                geocoded += 1
    _save_zip_county_cache(zip_cache)
    if geocoded:
        print(f"  resolved county by ZIP geocode for {geocoded} row(s) that had no free-text county mention")

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["notice_id", "publication", "date_published", "notice_type", "street", "city", "state", "zip",
                  "county", "loan_principal", "auction_date",
                  "saved_search", "notice_text_snippet", "source_url"]

    if args.standardize:
        # src/ is not on sys.path when these scripts run standalone (only
        # src/scripts/ is, via the STREET_SUFFIXES import block above).
        import sys as _sys
        _src_dir = str(ROOT / "src")
        if _src_dir not in _sys.path:
            _sys.path.insert(0, _src_dir)
        from notice_row_adapter import ENRICHED_COLUMNS, standardize_rows

        env = dotenv_values(str(ENV_PATH))
        # This footprint is MD counties plus DC; the adapter resolves DC per row
        # from the county, so no single default_state is passed.
        stats = standardize_rows(
            rows,
            env.get("SMARTY_AUTH_ID", ""),
            env.get("SMARTY_AUTH_TOKEN", ""),
            default_state="MD",
            expected_states={"MD", "DC"},
        )
        print(f"  Smarty: {stats['standardized']}/{stats['rows']} USPS-confirmed, "
              f"{stats['commercial']} commercial (likely courthouse/office, not a "
              f"subject property), {stats['vacant']} flagged vacant")
        fieldnames = fieldnames + ENRICHED_COLUMNS
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
