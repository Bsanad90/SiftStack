"""Pull public notices from publicnoticevirginia.com to a CSV -- no login needed.

publicnoticevirginia.com fills the Virginia gap CLAUDE.md already flagged:
mddcpublicnotices.com has no Virginia county checkbox at all, so deliberate
VA coverage needed a separate public-notice source. This is that source.

REVISION 2026-08-24: the original build of this script assumed the same
login-gated Smart Search flow as tnpublicnotice.com / mddcpublicnotices.com
(authenticate.aspx -> Search.aspx -> ddlSavedSearches). Live testing found
that account has NO subscription on this site ("Subscription or free trial
expired" was the only dropdown option), and per the user, there will never
be one -- use the PUBLIC "Popular Searches" Quick Search widget instead.
That widget lives right on the homepage (default.aspx), fully unauthenticated,
confirmed live via a plain unauthenticated GET: same
`ctl00_ContentPlaceHolder1_as1_...` control naming as the gated Search.aspx
page (same vendor template as TN/MDDC), including the IDENTICAL county
CheckBoxList (`lstCounty_{N}`, verified same indices as the earlier
authenticated --list-counties run: Arlington 6, Fairfax 29, Prince William
75, Spotsylvania 89, Stafford 90) plus a `ddlPopularSearches` dropdown of
pre-built category searches -- and NO login step in the flow at all.

This is a strictly better source than a paid Smart Search saved-search would
have been: Popular Searches isn't limited to foreclosures. Verified live
values (see POPULAR_SEARCHES below): Foreclosures (4) is the direct
equivalent of the MDDC Trustee's Sale pull, but Estate Claims (6) is a
probate-adjacent category and Tax Deeds (8) is a tax-sale-adjacent one --
neither exists anywhere else in the MD/DC/VA pipeline today (CLAUDE.md's
doors-per-deal research found zero SiftMap coverage for those list types
across all 14 target jurisdictions). --popular-search selects which one to
pull; run it once per category you want.

CONFIRMED against a live results page 2026-08-25 (a real --max-pages 1
Foreclosures pull returned genuine Arlington and Prince William trustee-sale
notices, correctly parsed). Three things needed fixing versus the
MDDC-derived first cut, all confirmed against real row HTML:
  - Each county checkbox fires its OWN onclick -> __doPostBack (unlike a
    plain form field), so checking N boxes without waiting for them to
    round-trip races Go's postback and silently drops the county filter --
    fixed with an explicit wait scaled to county count (see build_actions).
  - This site's "View" button is a plain postback submit with no
    Details.aspx?ID= anywhere in the row -- there is no stable per-notice id
    to extract here, so notice_id is always blank (not a parsing miss).
  - The row DOES carry a hidden, structured `City:`/`County:` field
    (div.right) that MDDC's equivalent div does not -- used as the
    authoritative county source in parse_grid_meta, ahead of the free-text
    COUNTY_MENTION_RE fallback.
Also confirmed live: Firecrawl's own render of this page is flaky --
roughly half of otherwise-identical attempts return an empty `<body></body>`
shell instead of the real ~150K+ char page. run_popular_search retries on
that (and on a bare Firecrawl 500, also seen live) rather than trusting a
too-small page as a true zero-results run.

Usage:
    # confirm the Popular Searches categories haven't been renumbered
    python src/scripts/va_trustee_sale_pull.py --list-searches
    # confirm county checkbox indices haven't been renumbered
    python src/scripts/va_trustee_sale_pull.py --list-counties

    # pull Foreclosures (the MDDC Trustee's Sale equivalent) for the target counties
    python src/scripts/va_trustee_sale_pull.py --popular-search 4 --days 60 \\
        --counties "Fairfax,Prince William,Arlington,Stafford,Spotsylvania" \\
        --max-pages 10 --out output/va_foreclosures.csv

    # Estate Claims (probate-adjacent) and Tax Deeds (tax-sale-adjacent)
    python src/scripts/va_trustee_sale_pull.py --popular-search 6 --out output/va_estate_claims.csv
    python src/scripts/va_trustee_sale_pull.py --popular-search 8 --out output/va_tax_deeds.csv
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
ZIP_COUNTY_CACHE_PATH = ROOT / "data" / "zip_county_cache.json"  # shared with mddc_trustee_sale_pull.py
FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"
BASE_URL = "https://www.publicnoticevirginia.com"
# Public, unauthenticated entry point -- the Quick Search widget (Popular
# Searches + county filter + date range + Go) is embedded directly on this
# page. No authenticate.aspx step, no MDDC_EMAIL/MDDC_PASSWORD needed.
SEARCH_URL = f"{BASE_URL}/default.aspx"

# ddlPopularSearches option values, verified live 2026-08-24 from the public
# homepage (unauthenticated GET). Re-verify with --list-searches if a pull
# using one of these comes back empty -- the site can renumber this list.
POPULAR_SEARCHES = {
    "3": "Assessments",
    "4": "Foreclosures",
    "6": "Estate Claims",
    "7": "Truth in Taxation",
    "8": "Tax Deeds",
    "9": "Schedule of Meetings",
    "10": "Annual Treasurer's Report",
    "14": "Prevailing Wage Notices",
    "15": "Water",
    "16": "Virginia Marine Resources Commission",
}
DEFAULT_POPULAR_SEARCH = "4"  # Foreclosures -- the MDDC Trustee's Sale equivalent

# Which classify_notice_type() label each Popular Search is expected to yield.
# filter_notice_type() keys its keep-set off THIS, not off a hardcoded
# {"foreclosure"}: the 2026-08-27 filter shipped with that default for every
# search, so --popular-search 6 (Estate Claims -> "probate") and 8 (Tax Deeds
# -> "tax_sale") returned ZERO rows while reporting success. A search with no
# entry here is passed through unfiltered rather than emptied.
# For 6 and 8 "other" is KEPT on purpose: the grid snippet is often clipped to
# a caption ("ORDER OF PUBLICATION / Case No. ... / click 'view'"), so a real
# Estate Claims or Tax Deeds notice can classify as "other". The site's own
# Popular Search is the primary classification there; this filter only drops a
# row that is POSITIVELY a different type (a trustee's sale leaking into Estate
# Claims). Foreclosures keeps the strict set because that is where the
# off-topic noise (bank mergers, tender offers) was actually observed.
SEARCH_NOTICE_TYPE = {
    "4": frozenset({"foreclosure"}),
    "6": frozenset({"probate", "other"}),
    "8": frozenset({"tax_sale", "other"}),
}

# County checkbox index on the Quick Search widget's county CheckBoxList,
# verified live 2026-08-24 -- confirmed IDENTICAL on both the public
# default.aspx page and the login-gated Search.aspx page (104 checkboxes on
# each: every VA county + the state's independent cities, plus
# "Washington D.C." at idx=98). Re-verify with --list-counties if the site
# renumbers the list.
#
# NOTE: "Fredericksburg City" is NOT on this list at all -- confirmed absent
# from all 104 live checkboxes (independent cities that ARE present include
# Chesapeake, Colonial Heights, Falls Church, Hampton, Newport News City,
# Norfolk City, Portsmouth, Richmond City, Roanoke City, Suffolk City,
# Virginia Beach, Williamsburg City -- Fredericksburg is conspicuously
# missing from that set). Left out of COUNTY_CHECKBOX_INDEX rather than
# guessed a workaround; ask before assuming one (e.g. whether it's folded
# into Spotsylvania's notices, or genuinely uncovered by this site).
COUNTY_CHECKBOX_INDEX: dict[str, int] = {
    "Arlington": 6,
    "Fairfax": 29,
    "Prince William": 75,
    "Spotsylvania": 89,
    "Stafford": 90,
}

DEFAULT_COUNTIES = [
    "Fairfax",
    "Prince William",
    "Arlington",
    "Stafford",
    "Spotsylvania",
]

# Matches both the two-line notice-caption form ("STREET\nCITY, ST ZIP") and
# the single-line form ("STREET, CITY, ST ZIP"). State group widened past the
# MDDC script's (MD|DC|DE|VA) to VA's actual land-neighbors, since the MDDC
# site already showed cross-state bleed in the other direction (a VA notice
# surfaced under an MD-only county filter) -- the same bleed is plausible here.
STATE_NAMES = {"VIRGINIA": "VA", "MARYLAND": "MD", "WEST VIRGINIA": "WV",
               "NORTH CAROLINA": "NC", "DISTRICT OF COLUMBIA": "DC", "WASHINGTON DC": "DC"}
STATE_ALT = r"VA|MD|DC|WV|NC|Virginia|Maryland|West Virginia|North Carolina|District of Columbia"

# Same two live gaps found and fixed in mddc_trustee_sale_pull.py's identical
# regex 2026-08-27 (Basem: "follow the exact same rules as MDDC"), confirmed
# on real VA notices: a spelled-out state name ("...CULPEPER, VIRGINIA
# 22701...") and no delimiter at all between street and city ("1234 SAMPLE
# MEADOWS DRIVE MANASSAS, VA 20109", "1234 EXAMPLE LN  MIDLOTHIAN, VA
# 23112"). STATE_ALT covers the first; ADDRESS_FALLBACK_RE below covers the
# second.
ADDRESS_RE = re.compile(
    r"([0-9][A-Za-z0-9 .,#\-']{4,60}?)\s*(?:\n|,)\s*"
    rf"([A-Za-z .'-]{{2,40}}),\s*({STATE_ALT})\s*(\d{{5}})",
    re.I,  # a spelled-out state name shows up ALL CAPS as often as title case
)

# Fallback for "NUMBER ... CITY, STATE ZIP" with no street/city delimiter --
# splits on the LAST recognized street-suffix word, same technique as
# md_register_of_wills_pull._split_address_blob / mddc's identical fallback.
ADDRESS_FALLBACK_RE = re.compile(
    rf"([0-9][A-Za-z0-9 .,#\-']{{4,60}})\s*,?\s*({STATE_ALT})\s*(\d{{5}})",
    re.I,
)

# A trustee's sale notice states the SUBJECT PROPERTY in its headline, on the
# first line, immediately after "TRUSTEE'S SALE" -- verified live 2026-08-31
# against a real Fairfax notice ("TRUSTEE'S SALE 2098 GOLF COURSE DRIVE
# RESTON, VA 20191"). This is the ONLY authoritative property address in the
# notice, and it must be preferred over every other address in the body:
#
#   * the courthouse appears later as the AUCTION VENUE ("...at the front of
#     the Fairfax County Circuit Court (Fairfax County Judicial Center, 4110
#     Chain Bridge Road)")
#   * the trustee's law firm appears near the end as its own letterhead
#     ("101 North Lynnhaven Road, Suite 104, Virginia Beach, Virginia 23452")
#
# Both are real, deliverable, USPS-confirmable addresses -- so no amount of
# address VALIDATION catches them. Only anchoring on the headline does.
#
# Note the headline has NO comma between street and city ("DRIVE RESTON"),
# which is exactly why ADDRESS_RE misses it; the street/city split is done
# on the last street-suffix token, same technique as ADDRESS_FALLBACK_RE.
# The title varies more than "TRUSTEE'S SALE". Measured against 139 real MDDC
# notices 2026-08-31: singular possessive is only 25/139, while the dominant
# form is the PLURAL "SUBSTITUTE TRUSTEES' SALE" (51 ASCII apostrophe + 18
# curly U+2019 = 69), and the broad alternation below reaches 114/139 (82%).
# A pattern requiring \s+ straight after the S silently misses every plural,
# because the apostrophe sits between the S and the space. The curly variant
# has bitten this codebase before (a SiftMap delete-dialog matcher, same day),
# so both apostrophes are always spelled out.
TRUSTEE_HEADLINE_RE = re.compile(
    "TRUSTEES?['’]?S?['’]?" + r"\s+SALE\s+(?:OF\s+)?"
    r"([0-9][A-Za-z0-9 .,#\-']{4,70}?)\s*,?\s*"
    rf"({STATE_ALT})\s*(\d{{5}})",
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
# fetch is wired up, same caveat as the MDDC script.
LOAN_PRINCIPAL_RE = re.compile(
    r"(?:original\s+)?principal\s+(?:amount|balance)\s+of\s+\$([\d,]+(?:\.\d{2})?)", re.I)

# Structurally near-always blank for this script, not just "sometimes" --
# the auction date is typically stated near the END of a trustee's sale
# notice ("...will offer for sale... on <date>"), well past where this
# site's own grid snippet truncates ("click 'view' to open the full text").
# Unlike MDDC (which at least has an unwired Scrapfly full-text plan), this
# public VA view has NO notice id or URL to fetch a full detail page from at
# all (see the comment above COUNTY_CHECKBOX_ID_RE) -- there is currently no
# path to the real auction date for this data source, not a regex to tune.
AUCTION_DATE_RE = re.compile(
    r"(?:on|dated for)\s+"
    r"((?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+\d{4})",
)

# Only a LITERAL county/city mention in the notice text counts -- never
# inferred from city, matching this codebase's established rule (see the
# "courthouse became the subject property" probate lesson in CLAUDE.md): a
# guess that's wrong is worse than a blank field.
#
# Virginia notices commonly use "the County of <Name>" / "Circuit Court of
# the County of <Name>" rather than "<Name> County" -- verified live
# 2026-08-25 (a real Prince William notice read "...Circuit Court of the
# County of Prince William, Virginia"). The old MDDC-derived pattern's
# capture group allowed spaces, so it swallowed everything back to the
# nearest capitalized word ("Office of the Circuit Court of the") instead
# of the actual county name. Each alternative below caps the capture at 1-3
# TITLE-CASE words only (lowercase glue words like "of"/"the" can't extend
# the match), tried in this order: "County of <Name>", "City of <Name>",
# "<Name> County" as a fallback for the rarer reversed phrasing.
#
# SECOND bug, also caught live 2026-08-25: allowing \s+ (which matches
# newlines) between the 1-3 repeated words let the match bridge ACROSS
# separate lines to an unrelated capitalized word. A "Board of Equalization"
# meeting notice, laid out as "...Board of Equalization\nOrange County,
# Virginia\n...", captured "Equalization\nOrange" instead of just "Orange" --
# the engine's leftmost-match search started at "Equalization" (itself
# title-case) and greedily bridged the newline to "Orange" right before
# "County". Restricted to [ \t]+ (same-line whitespace only) so a line break
# breaks the chain; legitimate same-line multi-word names ("Prince William")
# are unaffected.
#
# THIRD bug, caught on the very next live pull: VA notice CAPTIONS are
# routinely ALL CAPS ("IN THE CIRCUIT COURT OF THE COUNTY OF ALBEMARLE"), so
# the literal "County"/"of"/"City" keywords need case-insensitive matching --
# but doing that with a blanket re.IGNORECASE also loosens the [A-Z] class
# that keeps glue words out of the capture, reintroducing the original bug
# for ALL-CAPS text ("OF THE COUNTY OF ALBEMARLE" then matched "THE COUNTY"
# via the reversed alternative, and even "HE" once the true start got
# rejected -- the class needs an explicit word boundary too, or a rejected
# start just retries mid-word). The fix layers three things: (1) scoped
# `(?i:...)` on the keyword literals only, not a blanket flag, so the class
# stays case-SENSITIVE; (2) a `\b(?!(?i:GLUE)\b)` guard before the captured
# name's FIRST word as well as each repeated word (a guard on the repeats
# alone still let "THE" through as the first/only word); (3) COUNTY and CITY
# themselves added to the glue list, since a doubled caption
# ("COUNTY OF ALBEMARLE  COUNTY OF ALBEMARLE, VIRGINIA", a real duplicate
# from a live notice) otherwise bled the first match into the second
# occurrence's own "COUNTY" token.
_GLUE = (r"OF|THE|AND|CIRCUIT|COURT|STATE|COMMONWEALTH|VIRGINIA|CLERK|OFFICE|"
         r"SUBDIVISION|POLITICAL|CASE|NO|COUNTY|CITY|COMPLAINANT|RESPONDENT|IN")
# "IN" added 2026-08-27 (Basem): "...22701 COUNTY OF CULPEPER   In execution
# of a certain deed of trust..." bled the next sentence's leading "In" into
# the county capture as a second word ("CULPEPER In"), since "In" starts
# with a capital letter and wasn't on the glue list -- confirmed live.
_CO_NAME = (
    rf"\b(?!(?i:{_GLUE})\b)[A-Z][A-Za-z.'\-]+"
    rf"(?:[ \t]+(?!(?i:{_GLUE})\b)[A-Z][A-Za-z.'\-]+){{0,2}}"
)
COUNTY_MENTION_RE = re.compile(
    rf"(?i:county)\s+(?i:of)\s+({_CO_NAME})"
    rf"|(?i:city)\s+(?i:of)\s+({_CO_NAME})"
    rf"|({_CO_NAME})\s+(?i:county)\b"
)

# NO stable notice id is extractable from this public grid -- checked live
# 2026-08-25 against a real row's raw HTML. Unlike the MDDC/TN authenticated
# grid (a real <a>/<input> carrying `Details.aspx?ID=<n>`), this public
# view's "View" button is a plain ASP.NET postback submit
# (`ctl00$ContentPlaceHolder1$WSExtendedGridNP1$GridView1$ctl03$btnView2`) --
# `ctl03` is just this row's position in the CURRENT page's grid, not a
# stable global id, and there is no URL/query-string id anywhere in the row
# markup. notice_id is therefore always blank for this script on purpose,
# not a parsing miss; dedup relies on (street, city, date_published) instead
# (see dedupe() below), same as it would for any row with a missing id.

# County checkbox id pattern -- same naming convention as the MDDC site's
# CheckBoxList (ctl00_ContentPlaceHolder1_as1_lstCounty_{N}), confirmed
# present verbatim on this site's public homepage.
COUNTY_CHECKBOX_ID_RE = re.compile(r"ctl00_ContentPlaceHolder1_as1_lstCounty_(\d+)$")

NOTICE_TYPE_RULES = [
    ("foreclosure", re.compile(r"trustee'?s?\s+sale|substitute trustee|deed of trust|power of sale", re.I)),
    # VA judicial tax sales are a suit by the locality under Va. Code 58.1-3965
    # ("COUNTY OF X ... Complainant, v. OWNER"), so the statute and the
    # "delinquent real estate taxes" phrasing are the reliable tells.
    ("tax_sale", re.compile(r"tax\s+sale|tax\s+lien\s+sale|tax\s+deed|58\.1-39\d\d|"
                            r"delinquent\s+(?:real\s+estate\s+)?taxes", re.I)),
    # VA Estate Claims notices run under Va. Code 64.2-550 ("show cause against
    # the payment and delivery of the estate"); the TN/MD creditor-notice
    # phrasing rarely appears.
    ("probate", re.compile(r"notice to creditors|letters testamentary|personal representative|estate of|"
                           r"estate claim|64\.2-5\d\d|show\s+cause\s+against|administrat(?:or|rix)\s+of|"
                           r"execut(?:or|rix)\s+of|deceased", re.I)),
]


def classify_notice_type(text: str) -> str:
    for label, pattern in NOTICE_TYPE_RULES:
        if pattern.search(text):
            return label
    return "other"


def parse_grid_meta(info_cell) -> dict:
    """Structured City:/County: fields from the row's hidden `div.right`.

    Verified live 2026-08-25: unlike the MDDC site (whose equivalent div is
    documented empty), this site actually populates it, e.g.
    `<div class="right" style="display:none">City: Charlottesville<br>
    County: Albemarle</div>`.

    NOT authoritative for county, despite looking like it should be -- caught
    live on a single-county (Arlington-only) test pull where every returned
    notice was a genuine Arlington property (confirmed by street address and
    the notice body's own "Circuit Court for Arlington County" text), yet
    this field read "Fairfax" or "Washington D.C." for several of them. It
    looks like it reflects the PUBLICATION's own registered coverage area
    rather than the actual subject property's county -- accurate for a small
    hyper-local paper (Daily Progress / Albemarle), wrong for a DC-metro
    paper (Washington Post/Times) covering multiple jurisdictions.
    parse_grid_html therefore NEVER uses out["county"] -- county there comes
    from COUNTY_MENTION_RE against the notice body only. out["city"] is
    still used as a fallback (no comparable counter-example found for city),
    with the same caveat.

    A second bug lived here too, also caught live 2026-08-25: on a row where
    a field is genuinely EMPTY (e.g. `City: <br>County: Orange`, no city
    value at all), a bare whitespace-star right after the label greedily ate the newline
    separating lines and the following `.+` then captured the NEXT field's
    label+value as if it were this field's content (city came back
    "County: Orange" instead of blank). Fixed with [ \t]* (same-line
    whitespace only, matching the _CO_NAME fix above) and `.*` instead of
    `.+` so a genuinely empty field yields an empty capture instead of
    bleeding into whatever line follows.
    """
    right = info_cell.select_one("div.right") if info_cell else None
    if not right:
        return {}
    text = right.get_text("\n", strip=True)
    out = {}
    m = re.search(r"City:[ \t]*(.*)", text)
    if m and m.group(1).strip():
        out["city"] = m.group(1).strip()
    m = re.search(r"County:[ \t]*(.*)", text)
    if m and m.group(1).strip():
        out["county"] = m.group(1).strip()
    return out


def parse_loan_principal(text: str):
    m = LOAN_PRINCIPAL_RE.search(text)
    return m.group(1) if m else ""


_MONTHS = ("January|February|March|April|May|June|July|August|September|"
           "October|November|December")
_ANY_DATE_RE = re.compile(rf"({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})", re.I)
# Words that mark a date as the DEED's date, never the sale's.
_DEED_DATE_CONTEXT_RE = re.compile(
    r"(recorded|dated|deed\s+of\s+trust|instrument|book|modified|assigned)"
    r"[^.]{0,40}$", re.I)
# Sale language that legitimately precedes the auction date.
_SALE_CONTEXT_RE = re.compile(
    r"(offer\s+for\s+sale|will\s+sell|public\s+auction|auction\s+sale|"
    r"date\s+of\s+sale|sale\s+date|sell\s+at\s+public)", re.I)
_AUCTION_PUB_FORMATS = ("%A, %B %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%Y-%m-%d")


def parse_auction_date(text: str, published: str = ""):
    """The SALE date, or "" -- never the Deed of Trust's recording date.

    AUCTION_DATE_RE alone matched the FIRST "on <date>" in the notice, which is
    almost always the deed's recording date: "In execution of the Deed of Trust
    dated August 18, 2021 and recorded on September 2, 2021". Measured on a
    live 5-county pull 2026-08-31: 17 of 18 extracted auction dates were deed
    dates, several years in the past. A stale auction date is worse than a
    blank one -- it misprices the marketing window and reads as a sale that
    already happened.

    Two guards:
      1. a date whose immediately preceding words are deed language
         (recorded/dated/instrument/book) is rejected outright;
      2. the sale of a property cannot predate the notice advertising it, so
         any candidate earlier than `published` is rejected. Same spirit as
         obituary_enricher's MAX_DOD_GAP_YEARS sanity check.

    Prefers a candidate that follows sale language ("will offer for sale ...
    on September 28, 2026"); falls back to the earliest surviving future date.
    """
    from datetime import datetime as _dt

    def _parse_pub(raw):
        raw = (raw or "").strip()
        for fmt in _AUCTION_PUB_FORMATS:
            try:
                return _dt.strptime(raw, fmt)
            except ValueError:
                continue
        return None

    pub = _parse_pub(published)
    preferred, fallback = [], []
    for m in _ANY_DATE_RE.finditer(text):
        raw = f"{m.group(1)} {m.group(2)}, {m.group(3)}"
        try:
            when = _dt.strptime(raw, "%B %d, %Y")
        except ValueError:
            continue
        before = text[max(0, m.start() - 60):m.start()]
        if _DEED_DATE_CONTEXT_RE.search(before):
            continue
        if pub and when.date() < pub.date():
            continue
        (preferred if _SALE_CONTEXT_RE.search(before) else fallback).append((when, raw))

    for bucket in (preferred, fallback):
        if bucket:
            return min(bucket)[1]
    return ""


def parse_county_mention(text: str):
    m = COUNTY_MENTION_RE.search(text)
    if not m:
        return ""
    return (m.group(1) or m.group(2) or m.group(3) or "").strip()


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
    """Same fix as MDDC's resolve_county_by_zip, 2026-08-27 (Basem): the
    COUNTY_MENTION_RE free-text parse is often blank because a plain
    Foreclosure notice frequently never says the word 'County' at all, and
    parse_grid_meta's own County: field is documented above as NOT
    authoritative. This is a real geocode (zippopotam.us for lat/lon, then
    the FCC's Census Area API for the county that point falls in), not a
    name-based guess -- cached in the file shared with mddc_trustee_sale_pull.py
    since VA/DC/MD ZIPs can repeat across both scripts."""
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
                    county = name[:-len(" County")] if name.endswith(" County") else name
    except requests.exceptions.RequestException:
        county = ""
    cache[zip5] = county
    return county


def _normalize_state(raw: str) -> str:
    return STATE_NAMES.get(raw.strip().upper(), raw.strip().upper())


def _split_street_city(blob: str):
    """Split a "street + city" blob into (street, city).

    A COMMA wins when present: "1476 Vineyard Ct Unit# 105XA, Crofton" must
    split at the comma, giving street="1476 Vineyard Ct Unit# 105XA". The
    suffix-token method below would instead cut at "Ct" and produce
    street="1476 Vineyard Ct", city="Unit# 105XA Crofton" -- dropping the unit
    number, which on a condo means the WRONG PROPERTY, and corrupting the
    city. Found 2026-08-31 against real MDDC data.

    Only when there is no comma (VA's headline form, "2098 GOLF COURSE DRIVE
    RESTON") does it fall back to splitting on the last street-suffix token.
    Returns None if neither method yields both halves.
    """
    blob = " ".join(blob.split())
    if "," in blob:
        street, _, city = blob.rpartition(",")
        street, city = street.strip(), city.strip()
        if street and city:
            return street, city
        return None

    tokens = [t.rstrip(",") for t in blob.split()]
    split_idx = None
    for i, tok in enumerate(tokens):
        if tok.upper().rstrip(".") in STREET_SUFFIXES:
            split_idx = i
    if split_idx is None:
        return None
    street = " ".join(tokens[: split_idx + 1]).strip()
    city = " ".join(tokens[split_idx + 1 :]).strip()
    if not street or not city:
        return None
    return street, city


def parse_trustee_headline_address(text: str):
    """The property address from the TRUSTEE'S SALE headline, or None.

    Preferred over parse_address() for trustee's sale notices -- see the
    TRUSTEE_HEADLINE_RE comment for why every other address in the body is
    the wrong building.
    """
    m = TRUSTEE_HEADLINE_RE.search(text)
    if not m:
        return None
    blob, state, zip5 = m.groups()
    split = _split_street_city(" ".join(blob.split()))
    if not split:
        return None
    street, city = split
    return {
        "street": street,
        "city": city.title(),
        "state": _normalize_state(state),
        "zip": zip5,
    }


def parse_address(text: str):
    """Return the SUBJECT PROPERTY address, not the law firm's letterhead.

    The TRUSTEE'S SALE headline is tried FIRST: on a trustee's sale notice it
    is the only authoritative property address, and both the "last match"
    heuristic below and plain address validation pick the wrong building.

    Same fix as the MDDC script: these notices open with the filing firm's
    own office address (which frequently also matches the address pattern)
    and name the actual property later. Taking the LAST regex match returns
    the property; taking the first returns the firm's office.
    """
    headline = parse_trustee_headline_address(text)
    if headline:
        return headline

    matches = list(ADDRESS_RE.finditer(text))
    if matches:
        street, city, state, zip5 = matches[-1].groups()
        return {
            "street": " ".join(street.split()),
            "city": city.strip().title(),
            "state": _normalize_state(state),
            "zip": zip5,
        }
    # Fallback for no delimiter between street and city -- see
    # ADDRESS_FALLBACK_RE above.
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
        return None
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
    """FIRECRAWL_API_KEY is the only hard requirement -- this site's Quick
    Search widget is fully public, no login. CAPTCHA_API_KEY and
    SCRAPFLY_KEY are optional here and only enforced by main() when
    --full-text is actually requested (see fetch_full_text)."""
    env = dotenv_values(str(ENV_PATH))
    firecrawl_key = env.get("FIRECRAWL_API_KEY")
    if not firecrawl_key:
        raise RuntimeError("Missing from .env: FIRECRAWL_API_KEY")
    return {
        "firecrawl_key": firecrawl_key,
        "captcha_api_key": env.get("CAPTCHA_API_KEY", ""),
        "scrapfly_key": env.get("SCRAPFLY_KEY", ""),
    }


def synth_click_js(selector: str) -> str:
    """JS for a full synthetic mouse-event click. On the MDDC site (same
    vendor platform) a plain Firecrawl "click" action silently no-ops against
    the ASP.NET UpdatePanel's postback interception, but a full synthetic
    MouseEvent sequence works every time -- carried over here unverified.
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


def _firecrawl_run(creds: dict, actions: list, timeout: int) -> list[str]:
    resp = requests.post(
        FIRECRAWL_URL,
        headers={"Authorization": f"Bearer {creds['firecrawl_key']}", "Content-Type": "application/json"},
        json={"url": SEARCH_URL, "actions": actions, "formats": ["html"]},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    scrapes = (data.get("actions") or {}).get("scrapes") or []
    return [s.get("html", "") for s in scrapes if s.get("html")]


def list_saved_searches(creds: dict) -> list[tuple[str, str]]:
    """Re-read the live Popular Searches dropdown on the public homepage.
    No login needed. Cross-check against POPULAR_SEARCHES if a pull using
    one of those hardcoded values comes back empty.
    """
    htmls = _firecrawl_run(creds, [{"type": "scrape"}], timeout=60)
    if not htmls:
        return []
    soup = BeautifulSoup(htmls[-1], "html.parser")
    select = soup.select_one("#ctl00_ContentPlaceHolder1_as1_ddlPopularSearches")
    if not select:
        return []
    pairs = []
    for opt in select.find_all("option"):
        value = (opt.get("value") or "").strip()
        label = opt.get_text(strip=True)
        if value and label and value != "0":
            pairs.append((value, label))
    return pairs


def list_counties(creds: dict) -> list[tuple[int, str]]:
    """Re-read the live county CheckBoxList on the public homepage. No login
    needed."""
    htmls = _firecrawl_run(creds, [{"type": "scrape"}], timeout=60)
    if not htmls:
        return []
    soup = BeautifulSoup(htmls[-1], "html.parser")
    out = []
    for cb in soup.find_all("input", id=COUNTY_CHECKBOX_ID_RE):
        m = COUNTY_CHECKBOX_ID_RE.search(cb["id"])
        idx = int(m.group(1))
        label_el = soup.find("label", attrs={"for": cb["id"]})
        if label_el:
            label = label_el.get_text(strip=True)
        else:
            nxt = cb.next_sibling
            label = ""
            while nxt is not None and not label:
                label = nxt.strip() if isinstance(nxt, str) else nxt.get_text(strip=True)
                nxt = nxt.next_sibling
        out.append((idx, label))
    out.sort(key=lambda t: t[0])
    return out


def build_actions(popular_search_value: str, counties: list, days: int, max_pages: int,
                   date_from: str = "", date_to: str = "") -> list:
    # ONE COUNTY CLICK PER ACTION, WITH A SETTLE WAIT BETWEEN. The county
    # CheckBoxList is an AutoPostBack control inside the as1 UpdatePanel
    # (it is listed in the page's PageRequestManager._initialize call), so
    # every click is its own async postback and ASP.NET ABORTS the in-flight
    # one when the next starts. Five clicks in one JS burst therefore
    # cancel each other and the results page comes back with NO county
    # checked -- confirmed live 2026-08-28: Foreclosures + Arlington alone
    # returned 10/10 Arlington rows with box 6 checked, the same code with
    # all five counties returned a statewide grid with none checked. Setting
    # `checked` WITHOUT the click event does nothing either (also confirmed:
    # the server only learns a county from its postback). So: click, wait
    # for the panel to settle, click the next.
    county_clicks = [
        f"""(function(){{
            var cb = document.querySelector('#ctl00_ContentPlaceHolder1_as1_lstCounty_{COUNTY_CHECKBOX_INDEX[c]}');
            if (cb && !cb.checked) {{ cb.checked = true; cb.dispatchEvent(new Event('click',{{bubbles:true}})); cb.dispatchEvent(new Event('change',{{bubbles:true}})); }}
        }})();"""
        for c in counties
        if c in COUNTY_CHECKBOX_INDEX
    ]

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

    # NAVIGATION MARKERS. Changing the Popular Search dropdown and clicking
    # Go are both FULL page navigations to Search.aspx (confirmed live
    # 2026-08-28 by scraping after each step: the page is a ~2.7K empty
    # shell mid-navigation, then a fresh Search.aspx with the dropdown reset
    # to "0"). A fixed wait is a bet on Firecrawl's speed that day: on
    # 2026-08-28 the page was still blank 5 s after the change, so the county
    # clicks ran against an empty document, Go ran a statewide all-type
    # search, and the run reported 30 clean-looking rows. So: stamp the
    # CURRENT body with a marker before each navigation and wait for a body
    # WITHOUT it (i.e. the next document) that also carries the quick-search
    # form. The category itself survives server-side in the (S(...)) session
    # URL -- the dropdown re-renders as "0" on Search.aspx either way.
    mark = "document.body.setAttribute('data-dpd-pre','1');"
    loaded = "body:not([data-dpd-pre]) #ctl00_ContentPlaceHolder1_as1_ddlPopularSearches"

    actions = [
        {"type": "wait", "milliseconds": 1500},
        {
            "type": "executeJavascript",
            "script": f"""
                {mark}
                var sel = document.querySelector('#ctl00_ContentPlaceHolder1_as1_ddlPopularSearches');
                if (sel) {{ sel.value = '{popular_search_value}'; sel.dispatchEvent(new Event('change', {{bubbles: true}})); }}
            """,
        },
        {"type": "wait", "selector": loaded},
        {"type": "wait", "milliseconds": 1500},
    ]
    for js in county_clicks:
        actions.append({"type": "executeJavascript", "script": js})
        actions.append({"type": "wait", "milliseconds": COUNTY_POSTBACK_SETTLE_MS})
    actions += [
        {"type": "executeJavascript", "script": date_js},
        {"type": "wait", "milliseconds": 1500},
        # The results page is then CHECKED for every requested box still
        # being ticked -- see run_popular_search / counties_not_applied.
        {
            "type": "executeJavascript",
            "script": mark + synth_click_js("#ctl00_ContentPlaceHolder1_as1_btnGo"),
        },
        {"type": "wait", "selector": loaded},
        # the results grid is the heaviest part of the page to render
        {"type": "wait", "milliseconds": 4000},
        {"type": "scrape"},
    ]
    for _ in range(max(0, max_pages - 1)):
        actions.append({
            "type": "executeJavascript",
            "script": mark + synth_click_js("#ctl00_ContentPlaceHolder1_WSExtendedGridNP1_GridView1_ctl01_btnNext"),
        })
        # On the last page there is no next-page navigation, so the marker
        # never clears; a plain timed wait keeps that case from hanging the
        # whole action sequence (the caller already detects a re-captured
        # page via detected_page_number).
        actions.append({"type": "wait", "milliseconds": 6000})
        actions.append({"type": "scrape"})
    return actions


# A genuinely rendered result page runs well over 100K chars (the quick-search
# form alone is ~50K of markup, plus a full results grid). Confirmed live
# 2026-08-25 across repeated identical runs: Firecrawl's "scrape" action
# sometimes fires before this page's JS-driven UpdatePanel postback finishes
# and captures an empty `<body></body>` shell (~2.7K chars) instead --
# happened on MORE THAN HALF of attempts across a full session of live
# testing (one run failed 3 straight tries), both before and after the
# county-checkbox postback-race fix above, so it's a render-timing issue
# independent of that fix and worse than a coin flip in practice. A
# too-small page silently looks like "zero results" rather than a failure,
# which is exactly the kind of silent zero this codebase's own FTM runbook
# keeps warning about -- so retry rather than trust it. max_retries defaults
# higher than a typical transient-failure budget for exactly this reason;
# a caller running this unattended (a future cron/schedule) should not
# lower it without also tightening MIN_VALID_PAGE_LEN or fixing the root
# cause (a longer post-Go wait, or detecting the UpdatePanel's own
# "loading-indicator" overlay clearing instead of a fixed timeout).
MIN_VALID_PAGE_LEN = 20000
# Settle time after each county checkbox's own async postback (see the
# county_clicks comment in build_actions). 2.5 s was enough live; too short
# and the next click aborts this one and the county silently drops out.
COUNTY_POSTBACK_SETTLE_MS = 2500


MIN_SPLIT_DAYS = 7


def collect_pages(creds: dict, popular_search_value: str, counties: list, days: int, max_pages: int,
                  date_from: str = "", date_to: str = "") -> list[str]:
    """ONE COUNTY PER FIRECRAWL CALL, with the date window split in half
    whenever a call fails. Two live findings (2026-08-28) force this shape:

      * Multi-county selection is unreliable on this widget even with the
        per-click settle wait (each county checkbox is its own async
        postback and they cancel each other), while a single county is
        confirmed correct for every category tested.
      * A heavy single query (Estate Claims + Fairfax + 60 days) exceeds
        Firecrawl's per-request budget and comes back as
        SCRAPE_ALL_ENGINES_FAILED after ~130 s, while the same query over
        7 days succeeds in 23 s. The failure is about result-set size, not
        code, so the remedy is a smaller window, retried.

    Every (county, window) call verifies the county box is still checked on
    the results page (run_popular_search). Windows below MIN_SPLIT_DAYS that
    still fail raise UntrustworthyResult, so a partial pull is never written
    as if it were complete."""
    from datetime import datetime, timedelta

    if date_from and date_to:
        d_from = datetime.strptime(date_from, "%m/%d/%Y")
        d_to = datetime.strptime(date_to, "%m/%d/%Y")
    else:
        d_to = datetime.now()
        d_from = d_to - timedelta(days=days)

    def fmt(d):
        return f"{d.month}/{d.day}/{d.year}"

    def pull_window(county: str, a, b, depth: int = 0) -> list[str]:
        span = (b - a).days + 1
        label = f"{county} {fmt(a)}..{fmt(b)}"
        try:
            htmls = run_popular_search(creds, popular_search_value, [county], days, max_pages,
                                       fmt(a), fmt(b), max_retries=1 if span > MIN_SPLIT_DAYS else 3)
        except UntrustworthyResult as e:
            if span <= MIN_SPLIT_DAYS:
                raise UntrustworthyResult(f"{label}: {e}") from e
            mid = a + timedelta(days=span // 2)
            print(f"  {label}: failed ({str(e)[:80]}...) -- splitting the window")
            return pull_window(county, a, mid - timedelta(days=1), depth + 1) + pull_window(county, mid, b, depth + 1)
        nums = [detected_page_number(h) for h in htmls]
        rows = sum(len(parse_grid_html(h, popular_search_value)) for h in htmls)
        print(f"  {label}: {len(htmls)} page(s) {nums}, {rows} row(s)")
        if len(htmls) > 1 and len(set(p for p in nums if p is not None)) < len(htmls) - 1:
            print(f"    NOTE: pagination re-captured a page on {label} (fewer pages than --max-pages, or a "
                  f"missed click) -- rows are de-duplicated downstream")
        return htmls

    out: list[str] = []
    for county in counties:
        out.extend(pull_window(county, d_from, d_to))
    return out


def checked_counties(html: str) -> list[int]:
    """County checkbox indexes that the RESULTS page renders as checked.
    ASP.NET re-renders the quick-search form with the posted state, so this
    is the server's own statement of which counties it filtered on."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for cb in soup.select("input[type=checkbox]"):
        m = COUNTY_CHECKBOX_ID_RE.search(cb.get("id", ""))
        if m and cb.has_attr("checked"):
            out.append(int(m.group(1)))
    return sorted(out)


def counties_not_applied(html: str, counties: list) -> list:
    """Requested counties whose checkbox the results page does NOT show as
    checked. Non-empty means the rows on that page are NOT scoped to the
    requested counties and must not be trusted (caught live 2026-08-28:
    a 5-county Estate Claims pull returned Richmond/Albemarle/Botetourt rows
    with every county box unchecked on the results page)."""
    got = set(checked_counties(html))
    return [c for c in counties if c in COUNTY_CHECKBOX_INDEX and COUNTY_CHECKBOX_INDEX[c] not in got]


class UntrustworthyResult(RuntimeError):
    """Raised when every attempt failed (Firecrawl error) or came back
    suspiciously small -- the caller must NOT treat this as "zero results"
    (see the module-level MIN_VALID_PAGE_LEN comment and main()'s handling
    of it below). Same discipline as the FTM runbook's "zero notices is a
    FAILURE" rule elsewhere in this codebase: a run that silently writes an
    empty-looking CSV on a service outage is worse than one that refuses to.
    """


def run_popular_search(creds: dict, popular_search_value: str, counties: list, days: int, max_pages: int,
                        date_from: str = "", date_to: str = "", max_retries: int = 4) -> list:
    actions = build_actions(popular_search_value, counties, days, max_pages, date_from, date_to)
    timeout = max(300, 90 + max_pages * 10)
    htmls: list[str] = []
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 2):
        try:
            htmls = _firecrawl_run(creds, actions, timeout)
            last_error = None
        except requests.exceptions.RequestException as e:
            # Also transient (a live 500 from Firecrawl's own API was hit
            # during testing 2026-08-25) -- retry the same as a too-small page
            # rather than letting one flaky call kill the whole run.
            last_error = e
            htmls = []
        else:
            if htmls and all(len(h) >= MIN_VALID_PAGE_LEN for h in htmls):
                missing = counties_not_applied(htmls[0], counties)
                if not missing:
                    return htmls
                last_error = RuntimeError(
                    f"county filter NOT applied -- results page shows {missing} unchecked "
                    f"(the county clicks ran before Search.aspx had loaded); rows would be statewide")
        lens = [len(h) for h in htmls]
        reason = f"error: {last_error}" if last_error else f"too-small page (lens={lens})"
        if attempt <= max_retries:
            print(f"  WARNING: attempt {attempt} failed ({reason}) -- retrying...")
            time.sleep(2 * attempt)
        else:
            raise UntrustworthyResult(
                f"gave up after {attempt} attempts ({reason}). This run's row count "
                f"cannot be trusted as a true zero -- see UntrustworthyResult."
            )
    return htmls


# ── Full-text fetch (Scrapfly + 2Captcha) ──────────────────────────────
#
# The grid's snippet truncates ("click 'view' to open the full text") and
# the "View" button is a plain postback with no Details.aspx?ID= to fetch
# directly -- confirmed live 2026-08-25 (see the module docstring's AUCTION
# DATE note). Clicking it lands on a page gated by a Cloudflare Turnstile
# challenge, id="ctl00_ContentPlaceHolder1_PublicNoticeDetailsBody1_..." --
# the SAME control-name prefix tnpublicnotice.com uses, confirming this is
# the identical vendor page template, gate and all. src/scrapfly_client.py
# already solves this exact gate for TN: pre-solve the Turnstile via
# 2Captcha against the site's sitekey BEFORE the scenario runs (a token is
# valid for the domain, not tied to one specific page render), then inject
# it into the hidden `cf-turnstile-response` field once the widget appears
# and click through. This is that same technique, ported to VA's public
# no-login search (redo the Popular-Search + county + date setup, then
# click the Nth `.viewButton` by POSITION -- there's no id to target it by).
#
# Verified live 2026-08-25: the sitekey, the "I Agree, View Notice" button,
# and its exact id all confirmed against a real captured detail page.
# _NOTICE_MARKERS is inherited from TN's own scrapfly_client.py unverified
# for VA specifically -- fetch_full_text() falls back to raw gate/error
# detection if neither marker appears, rather than silently calling a
# non-match "success".
DETAIL_TURNSTILE_SITEKEY = "0x4AAAAAADs-42ukJJ2lrjsf"
SEL_VIEW_NOTICE_BUTTON = "#ctl00_ContentPlaceHolder1_PublicNoticeDetailsBody1_btnViewNotice"
_NOTICE_MARKERS = ("Notice Content", "Notice Publish Date")
_GATE_MARKERS = ("cf-turnstile", "I Agree, View Notice")

_INJECT_TURNSTILE = (
    'var t="__TOKEN__";var out={set:0};'
    'document.querySelectorAll(\'[name="cf-turnstile-response"],'
    '[id^="cf-chl-widget"][id$="_response"]\').forEach(function(el){el.value=t;out.set++;});'
    'if(!out.set){var form=document.querySelector("form");'
    'var el=document.createElement("input");el.type="hidden";'
    'el.name="cf-turnstile-response";el.id="cf-turnstile-response";el.value=t;'
    '(form||document.body).appendChild(el);out.created=true;out.set=1;}'
    'return JSON.stringify(out);'
)


def _synth_click_expr(target_js_expr: str) -> str:
    """Same synthetic-MouseEvent-sequence technique as synth_click_js, but
    targeting an arbitrary JS expression (e.g. an indexed querySelectorAll
    result) instead of a CSS selector -- needed to click the Nth View
    button by position, since it has no stable id/selector of its own.
    """
    return f"""
        var el = {target_js_expr};
        if (el) {{
            var rect = el.getBoundingClientRect();
            var x = rect.left + rect.width/2, y = rect.top + rect.height/2;
            ['mouseover','mousedown','mouseup','click'].forEach(function(type) {{
                el.dispatchEvent(new MouseEvent(type, {{bubbles:true, cancelable:true, view:window, clientX:x, clientY:y}}));
            }});
        }}
    """


def _solve_turnstile(url: str, sitekey: str, captcha_api_key: str) -> str | None:
    from twocaptcha import TwoCaptcha
    solver = TwoCaptcha(captcha_api_key)
    try:
        sol = solver.turnstile(sitekey=sitekey, url=url)
        return sol.get("code") if isinstance(sol, dict) else str(sol)
    except Exception as exc:
        print(f"    2Captcha solve error: {exc}")
        return None


def scrapfly_preflight(scrapfly_key: str) -> tuple[bool, str]:
    """Cheap health check BEFORE any 2Captcha spend.

    fetch_full_text() pays for a Turnstile solve first and only then calls
    Scrapfly, so a quota-exhausted or invalid Scrapfly key burns one captcha
    solve per row for nothing (observed 2026-08-31: an entire run billed
    solves against ERR::SCRAPE::QUOTA_LIMIT_REACHED). One trivial scrape up
    front turns that into a single clear abort.
    """
    from scrapfly import ScrapeConfig, ScrapflyClient

    try:
        client = ScrapflyClient(key=scrapfly_key)
        resp = client.scrape(ScrapeConfig(
            url="https://httpbin.org/html", render_js=False,
            raise_on_upstream_error=False,
        ))
        sr = resp.scrape_result or {}
        err = sr.get("error")
        if err:
            code = err.get("code") if isinstance(err, dict) else str(err)
            return False, str(code)
        if not (sr.get("content") or ""):
            return False, "empty response from Scrapfly on control scrape"
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def fetch_full_text(popular_search_value: str, counties: list, days: int, view_index: int, *,
                     captcha_api_key: str, scrapfly_key: str, session: str = "va_fulltext",
                     date_from: str = "", date_to: str = "") -> dict:
    """Fetch ONE notice's full text: redo the search, click the view_index'th
    (0-based, in on-page order) result's View button, clear the Turnstile
    gate, and return the resulting page.

    Returns {"ok": True, "html": ...} or {"ok": False, "error": ..., "html": ...}.
    Each call redoes the ENTIRE search from scratch and pays for one 2Captcha
    solve -- there is no cheaper way to reach a specific row's detail page in
    this public flow (no stable id/URL exists to jump to directly).
    """
    from scrapfly import ScrapeConfig, ScrapflyClient, ScrapflyScrapeError

    token = _solve_turnstile(SEARCH_URL, DETAIL_TURNSTILE_SITEKEY, captcha_api_key)
    if not token:
        return {"ok": False, "error": "captcha_solve_failed", "html": ""}

    county_js = "\n".join(
        f"""(function(){{
            var cb = document.querySelector('#ctl00_ContentPlaceHolder1_as1_lstCounty_{COUNTY_CHECKBOX_INDEX[c]}');
            if (cb) {{ cb.checked = true; cb.dispatchEvent(new Event('click',{{bubbles:true}})); cb.dispatchEvent(new Event('change',{{bubbles:true}})); }}
        }})();"""
        for c in counties if c in COUNTY_CHECKBOX_INDEX
    )
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

    scen = [
        {"wait_for_selector": {"selector": "#ctl00_ContentPlaceHolder1_as1_ddlPopularSearches", "timeout": 15000}},
        {"execute": {"script": f"""
            var sel = document.querySelector('#ctl00_ContentPlaceHolder1_as1_ddlPopularSearches');
            if (sel) {{ sel.value = '{popular_search_value}'; sel.dispatchEvent(new Event('change', {{bubbles: true}})); }}
        """}},
        {"wait": 3000},
        {"execute": {"script": county_js + date_js}},
        {"wait": max(3000, 700 * len(counties))},
        {"execute": {"script": _synth_click_expr("document.querySelector('#ctl00_ContentPlaceHolder1_as1_btnGo')")}},
        {"wait": 8000},
        {"execute": {"script": _synth_click_expr(f"document.querySelectorAll('.viewButton')[{int(view_index)}]")}},
        {"wait": 5000},
        {"execute": {"script": _INJECT_TURNSTILE.replace("__TOKEN__", token)}},
        {"execute": {"script": _synth_click_expr(f"document.querySelector('{SEL_VIEW_NOTICE_BUTTON}')")}},
        {"wait": 4000},
    ]

    client = ScrapflyClient(key=scrapfly_key)
    cfg = ScrapeConfig(
        url=SEARCH_URL, render_js=True, asp=True, country="us",
        session=session, proxy_pool="public_residential_pool",
        rendering_wait=1500, js_scenario=scen, raise_on_upstream_error=False,
    )
    try:
        resp = client.scrape(cfg)
    except ScrapflyScrapeError as exc:
        return {"ok": False, "error": f"ScrapflyScrapeError: {exc}", "html": ""}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "html": ""}

    content = ""
    upstream = None
    try:
        content = resp.scrape_result.get("content", "") or ""
        upstream = resp.scrape_result.get("error")
    except Exception:
        pass

    if any(m in content for m in _NOTICE_MARKERS):
        return {"ok": True, "html": content}
    if any(m in content for m in _GATE_MARKERS):
        return {"ok": False, "error": "gate_not_cleared", "html": content}

    # Scrapfly reports quota/billing/proxy failures in scrape_result["error"]
    # while returning EMPTY content. Those used to fall through to
    # "unknown_page_state", which reads like a parsing problem and hid a
    # plain "out of quota" behind 26 identical mystery failures.
    if upstream:
        code = ""
        if isinstance(upstream, dict):
            code = upstream.get("code") or upstream.get("message") or ""
        return {"ok": False, "error": f"scrapfly: {code or upstream}", "html": content}
    if not content:
        return {"ok": False, "error": "empty_response_no_upstream_error", "html": ""}
    return {"ok": False, "error": "unknown_page_state", "html": content}


def extract_full_notice_text(html: str) -> str:
    """Pull the readable notice body out of a cleared detail page.

    Structure UNVERIFIED until a real gate-clear succeeds -- falls back to
    the largest block of visible text in the page body if none of the
    known-from-TN container selectors match, so a template difference shows
    up as "got something, maybe wrong" rather than an empty string that
    looks identical to total failure.
    """
    soup = BeautifulSoup(html, "html.parser")
    for sel in ("#ctl00_ContentPlaceHolder1_PublicNoticeDetailsBody1_lblNoticeText",
                ".noticeText", ".notice-text", "#noticeContent"):
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text("\n", strip=True)
    # Fallback: the body's largest text block, skipping nav/script/style noise.
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    body = soup.body or soup
    return body.get_text("\n", strip=True)


CURRENT_PAGE_RE = re.compile(r'lblCurrentPage"[^>]*>\s*(\d+)\s*<')


def detected_page_number(html: str):
    m = CURRENT_PAGE_RE.search(html)
    return int(m.group(1)) if m else None


def parse_grid_html(html: str, popular_search_value: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for view_index, nested in enumerate(soup.select("table.nested")):
        info = nested.select_one("td.info")
        text_cell = nested.select_one("td[colspan='3']")
        if not info or not text_cell:
            continue
        # div.left holds "<publication name><br>Monday, August 24, 2026" as
        # two lines -- verified live 2026-08-25 there is no "Published:"
        # label anywhere in this markup (a plain weekday-format date string
        # follows the publication name directly), unlike what the
        # MDDC-derived regex assumed.
        left = info.select_one("div.left")
        left_lines = [l for l in (left.get_text("\n", strip=True) if left else "").split("\n") if l]
        publication = left_lines[0] if left_lines else ""
        published = left_lines[1] if len(left_lines) > 1 else ""
        meta = parse_grid_meta(info)
        notice_text = text_cell.get_text("\n", strip=True)
        if not notice_text:
            continue
        addr = parse_address(notice_text) or {}
        rows.append(
            {
                "notice_id": "",  # not extractable from this public view -- see the comment above COUNTY_CHECKBOX_ID_RE
                "publication": publication,
                "date_published": published,
                "notice_type": classify_notice_type(notice_text),
                "street": addr.get("street", ""),
                "city": addr.get("city") or meta.get("city", ""),
                "state": addr.get("state", ""),
                "zip": addr.get("zip", ""),
                # meta.get("county") is deliberately NOT used as a fallback here
                # (see parse_grid_meta's docstring) -- on the same Arlington-only
                # test, 4 of 10 notices had no county phrase within the 2000-char
                # snippet, and falling back to this field would have relabeled
                # them "Fairfax"/"Washington D.C." instead of leaving them blank.
                # Blank and honest beats confident and wrong, same rule this
                # codebase applies everywhere else (see the probate
                # "courthouse became the subject property" lesson in CLAUDE.md).
                "county": parse_county_mention(notice_text),
                "loan_principal": parse_loan_principal(notice_text),
                "auction_date": parse_auction_date(notice_text, published),
                "popular_search": POPULAR_SEARCHES.get(popular_search_value, popular_search_value),
                "notice_text_snippet": notice_text[:2000],
                # Populated by main() via fetch_full_text() when --full-text is
                # passed, replacing source_url -- every row shares the same
                # SEARCH_URL, which carries no information (Basem, 2026-08-25:
                # "extract the full text ... rather than source URL - cause
                # its a general URL"). "_view_index" is this row's position
                # among .viewButton elements on THIS page (0-based) -- the only
                # way to target its own View button, since there's no id/URL
                # to jump to directly. Leading underscore = internal, not a
                # CSV column; popped off before writing (see main()).
                "full_text": "",
                "_view_index": view_index,
            }
        )
    return rows


IN_FOOTPRINT_STATES = {"VA", "DC", "MD"}


def filter_footprint(rows: list) -> tuple:
    """Drop rows whose parsed address isn't VA/DC/MD.

    The MDDC site already showed bleed in the reverse direction (a VA notice
    surfaced under an MD-only filter), so the same is plausible here in the
    other direction -- kept rather than dropped, same reasoning as the MDDC
    script. Rows with no parsed address are kept -- the snippet was
    truncated before any address appeared, not proof the property is out of
    scope.
    """
    kept, dropped = [], []
    for r in rows:
        if r["state"] and r["state"] not in IN_FOOTPRINT_STATES:
            dropped.append(r)
        else:
            kept.append(r)
    return kept, dropped


def filter_notice_type(rows: list, keep=frozenset({"foreclosure"})) -> tuple:
    """Drop rows whose classified notice_type isn't the one this Popular
    Search is actually pulling. Same fix as MDDC's filter_notice_type,
    2026-08-27 (Basem): 'Foreclosures' (popular_search=4) can still surface
    an off-topic notice that classify_notice_type correctly labels 'other'
    (or a stray tax_sale/probate hit), and nothing dropped those before they
    reached the CSV."""
    if keep is None:
        # A Popular Search we have no notice-type mapping for: filtering would
        # drop everything, so pass rows through untouched.
        return list(rows), []
    kept, dropped = [], []
    for r in rows:
        if r["notice_type"] in keep:
            kept.append(r)
        else:
            dropped.append(r)
    return kept, dropped


def dedupe(rows: list) -> list:
    """Collapse repeats of the same notice.

    THE ADDRESS IS NOT ALWAYS AN IDENTITY. Keying on (street, city, date) alone is right
    for Foreclosures, where every notice names a property, and catastrophic for the other
    Popular Searches: an Estate Claims notice is an ORDER OF PUBLICATION naming a decedent
    and a case number with no property address at all, so every row published on the same
    day collapses to the single key ("", "", date). A live 5-county Estate Claims pull went
    10 raw rows -> 1, silently, and reported success. The same trap catches any Foreclosure
    row whose address failed to parse.

    So an addressless row falls back to the notice text itself, which is what actually
    distinguishes one estate from another.

    An ADDRESSED row's key deliberately EXCLUDES date_published, and the most
    recent publication wins. Foreclosure notices republish weekly by law (a
    real notice read "Run: August 31, September 7, 2026"), so keeping the date
    in the key let one property survive once per publication date: measured
    2026-08-31 on a live 5-county pull, 34 of 137 output rows -- 25% -- were
    republications of a property already in the set (1421 PRINCE STREET
    appeared 3x on three consecutive days). This matches the project-wide rule
    in CLAUDE.md: same property across multiple notices collapses, keeping the
    most recent.

    The ADDRESSLESS path keeps date_published exactly as before -- that is the
    10-rows-to-1 trap in the paragraph above and must not be touched.

    Caveat: a property re-noticed with a NEW auction date also collapses to
    the most recent row. That is the intended reading (the latest notice is
    the authoritative one), but it means auction_date must be taken from the
    surviving row, not aggregated across the collapsed ones.
    """
    from datetime import datetime as _dt

    _PUB_FORMATS = ("%A, %B %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%Y-%m-%d")

    def _pub_key(raw: str):
        raw = (raw or "").strip()
        for fmt in _PUB_FORMATS:
            try:
                return _dt.strptime(raw, fmt)
            except ValueError:
                continue
        return _dt.min

    seen = set()
    out = []
    at_index = {}
    for r in rows:
        if r.get("notice_id"):
            key = ("id", r["notice_id"])
        elif r["street"].strip() and r.get("notice_type") == "foreclosure":
            # Dateless collapse ONLY for foreclosures, exactly as this
            # docstring's own rule says: address-keying "is right for
            # Foreclosures, and catastrophic for the other Popular Searches."
            # On a foreclosure a street IS the identity, so weekly
            # republications of the same property collapse to the newest.
            # Two measurements taken while writing this, both on live data:
            #   * city-only rows keyed as ("addr","","fairfax") collapsed
            #     Estate Claims 26 -> 7
            #   * even street-bearing Estate Claims rows collapsed 26 -> 24,
            #     because every one of them has the COURTHOUSE parsed into
            #     street, so two unrelated estates share an address
            # Restricting to foreclosure removes both failure modes; the other
            # searches keep their original date-inclusive behaviour untouched.
            key = ("addr", r["street"].strip().lower(), r["city"].strip().lower())
        elif r["street"] or r["city"]:
            key = ("addr", r["street"].lower(), r["city"].lower(), r["date_published"])
        else:
            body = (r.get("notice_text_snippet") or "").strip().lower()
            key = ("text", r["date_published"], body[:400])
        if key in seen:
            # Republication of a property we already kept: keep the newer one.
            if key[0] == "addr":
                i = at_index[key]
                if _pub_key(r["date_published"]) > _pub_key(out[i]["date_published"]):
                    out[i] = r
            continue
        seen.add(key)
        at_index[key] = len(out)
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--popular-search", default=DEFAULT_POPULAR_SEARCH,
                     help=f"ddlPopularSearches option value. Known values: "
                          f"{', '.join(f'{v}={l}' for v, l in POPULAR_SEARCHES.items())}.")
    ap.add_argument("--counties", default=",".join(DEFAULT_COUNTIES))
    ap.add_argument("--days", type=int, default=60,
                     help="Lookback window; ignored when --date-from/--date-to are both given. "
                          "The widget's own default is 60.")
    ap.add_argument("--date-from", default="", help="M/D/YYYY. Requires --date-to.")
    ap.add_argument("--date-to", default="", help="M/D/YYYY. Requires --date-from.")
    ap.add_argument("--max-pages", type=int, default=10,
                     help="Pages per search (newest-first). Each page costs one Firecrawl "
                          "action round-trip.")
    ap.add_argument("--out", default="output/va_trustee_sale.csv")
    ap.add_argument("--standardize", action="store_true",
                    help="Run Smarty USPS standardization over the scraped rows "
                         "(adds std_* / rdi / vacant / lat-lon columns). Needs "
                         "SMARTY_AUTH_ID and SMARTY_AUTH_TOKEN in .env. rdi="
                         "Commercial is the courthouse/office tell. Original "
                         "scraped columns are never overwritten.")
    ap.add_argument("--list-searches", action="store_true",
                     help="Print the live Popular Searches categories and exit. No login needed.")
    ap.add_argument("--list-counties", action="store_true",
                     help="Print the live county checkbox index + label and exit. No login needed.")
    ap.add_argument("--full-text", action="store_true",
                     help="Fetch each row's full notice text (not just the truncated grid snippet) "
                          "via Scrapfly + 2Captcha, clearing the Cloudflare Turnstile gate on the "
                          "detail page. Needs CAPTCHA_API_KEY and SCRAPFLY_KEY in .env. Each row "
                          "redoes the ENTIRE search + pays for one 2Captcha solve, so this is slow "
                          "and metered -- capped by --full-text-limit. Only reaches rows from page 1 "
                          "of the results (view_index is per-page; multi-page --max-pages isn't "
                          "supported yet for this flag).")
    ap.add_argument("--full-text-limit", type=int, default=10,
                     help="Max rows to fetch full text for for when --full-text is set (default 10, "
                          "matching the initial test batch).")
    args = ap.parse_args()

    creds = load_credentials()
    if args.full_text and not (creds.get("captcha_api_key") and creds.get("scrapfly_key")):
        raise SystemExit("--full-text needs CAPTCHA_API_KEY and SCRAPFLY_KEY in .env (both used "
                          "elsewhere in this repo for the same Turnstile-gate pattern on TN).")

    if args.list_searches:
        pairs = list_saved_searches(creds)
        if not pairs:
            print("No Popular Searches found (page structure changed, or the selector needs updating).")
            raise SystemExit(1)
        print(f"\n{len(pairs)} Popular Search categor(ies) on {BASE_URL.replace('https://www.', '')}:\n")
        for value, label in pairs:
            expected = POPULAR_SEARCHES.get(value)
            drift = f"   !! expected {expected!r}" if expected and expected != label else ""
            hardcoded = "  (hardcoded above)" if value in POPULAR_SEARCHES else "  (NEW, not in POPULAR_SEARCHES)"
            print(f"  value={value:>3}  {label}{hardcoded}{drift}")
        print()
        return

    if args.list_counties:
        pairs = list_counties(creds)
        if not pairs:
            print("No county checkboxes found (page structure changed, or the selector needs updating).")
            raise SystemExit(1)
        print(f"\n{len(pairs)} county checkbox(es) on {BASE_URL.replace('https://www.', '')}:\n")
        for idx, label in pairs:
            expected = {v: k for k, v in COUNTY_CHECKBOX_INDEX.items()}.get(idx)
            drift = f"   !! expected label {expected!r} at this index" if expected and expected != label else ""
            print(f"  idx={idx:>3}  {label}{drift}")
        print()
        return

    if bool(args.date_from) != bool(args.date_to):
        raise SystemExit("--date-from and --date-to must be given together")

    counties = [c.strip() for c in args.counties.split(",") if c.strip()]
    unknown = [c for c in counties if c not in COUNTY_CHECKBOX_INDEX]
    if unknown:
        raise SystemExit(f"Unknown county name(s): {unknown}. Known: {sorted(COUNTY_CHECKBOX_INDEX)}")

    ps = args.popular_search.strip()
    if ps not in POPULAR_SEARCHES:
        print(f"  NOTE: popular-search value {ps!r} isn't in the verified POPULAR_SEARCHES map "
              f"-- proceeding anyway, but run --list-searches if this comes back empty.")

    if args.date_from and args.date_to:
        print(f"Popular search {ps} ({POPULAR_SEARCHES.get(ps, '?')}): searching {len(counties)} counties, "
              f"{args.date_from} to {args.date_to}...")
    else:
        print(f"Popular search {ps} ({POPULAR_SEARCHES.get(ps, '?')}): searching {len(counties)} counties, "
              f"last {args.days} days...")
    try:
        htmls = collect_pages(creds, ps, counties, args.days, args.max_pages, args.date_from, args.date_to)
    except UntrustworthyResult as e:
        # Do NOT write --out here: an empty/near-empty CSV from a failed run
        # is indistinguishable from a genuine zero-results day, and a
        # scheduled re-run of this script would silently overwrite a GOOD
        # prior file with a misleadingly empty one. Fail loudly instead --
        # same "zero notices is a FAILURE" discipline as the TN FTM runbook.
        print(f"FAILED: {e}")
        print(f"  {args.out} was NOT written/overwritten -- Firecrawl itself looked down "
              f"during this run, not a code problem. Try again once it recovers.")
        raise SystemExit(3)
    print(f"  got {len(htmls)} page(s) of HTML across all county/window calls")

    all_rows = []
    for html in htmls:
        all_rows.extend(parse_grid_html(html, ps))

    in_footprint, out_of_footprint = filter_footprint(all_rows)
    if out_of_footprint:
        print(f"  dropped {len(out_of_footprint)} out-of-footprint (non VA/DC/MD) row(s), e.g. "
              f"{out_of_footprint[0]['city']}, {out_of_footprint[0]['state']}")
    on_type, off_type = filter_notice_type(in_footprint, keep=SEARCH_NOTICE_TYPE.get(ps))
    if off_type:
        from collections import Counter
        counts = Counter(r["notice_type"] for r in off_type)
        print(f"  dropped {len(off_type)} off-type row(s): {dict(counts)}")
    rows = dedupe(on_type)
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

    if args.full_text:
        # Verify Scrapfly is healthy BEFORE the loop: each iteration pays for a
        # 2Captcha solve before it ever reaches Scrapfly, so a dead key or an
        # exhausted quota would otherwise bill one solve per row for nothing.
        ok, why = scrapfly_preflight(creds["scrapfly_key"])
        if not ok:
            print(f"  --full-text ABORTED before any 2Captcha spend: Scrapfly unusable ({why})")
            print("     Fix the Scrapfly account/quota and re-run. The CSV below is "
                  "snippet-only and its addresses are NOT subject properties.")
            args.full_text = False

    if args.full_text:
        batch = rows[: args.full_text_limit]
        print(f"  --full-text: fetching full notice text for {len(batch)} of {len(rows)} row(s) "
              f"(each redoes the full search + one 2Captcha solve, expect ~30-90s/row)...")
        for i, row in enumerate(batch, 1):
            print(f"    [{i}/{len(batch)}] view_index={row['_view_index']} "
                  f"({row['street'] or row['city'] or row['publication']})...")
            result = fetch_full_text(
                ps, counties, args.days, row["_view_index"],
                captcha_api_key=creds["captcha_api_key"], scrapfly_key=creds["scrapfly_key"],
                date_from=args.date_from, date_to=args.date_to,
            )
            if result["ok"]:
                row["full_text"] = extract_full_notice_text(result["html"])
                print(f"      got {len(row['full_text'])} chars")
            else:
                # Visible in the CSV itself rather than silently blank -- a
                # blank full_text is otherwise indistinguishable from "not
                # attempted" (rows past --full-text-limit).
                row["full_text"] = f"[fetch failed: {result['error']}]"
                print(f"      FAILED: {result['error']}")

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["notice_id", "publication", "date_published", "notice_type", "street", "city", "state", "zip",
                  "county", "loan_principal", "auction_date",
                  "popular_search", "notice_text_snippet", "full_text"]

    if args.standardize:
        # src/ is not on sys.path when these scripts run standalone (only
        # src/scripts/ is, via the STREET_SUFFIXES import block above).
        import sys as _sys
        _src_dir = str(ROOT / "src")
        if _src_dir not in _sys.path:
            _sys.path.insert(0, _src_dir)
        from notice_row_adapter import ENRICHED_COLUMNS, standardize_rows

        env = dotenv_values(str(ENV_PATH))
        stats = standardize_rows(
            rows,
            env.get("SMARTY_AUTH_ID", ""),
            env.get("SMARTY_AUTH_TOKEN", ""),
            default_state="VA",
            expected_states={"VA"},
        )
        print(f"  Smarty: {stats['standardized']}/{stats['rows']} USPS-confirmed, "
              f"{stats['commercial']} commercial (likely courthouse/office, not a "
              f"subject property), {stats['vacant']} flagged vacant")
        fieldnames = fieldnames + ENRICHED_COLUMNS
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
