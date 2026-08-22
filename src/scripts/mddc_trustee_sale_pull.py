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
"""
from __future__ import annotations

import argparse
import csv
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"
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

# Saved Searches named "Trustee's Sale" on the account, verified live 2026-08-21
# (there happen to be two entries with the same label under different ids --
# both are pulled by default; trim with --saved-searches if one is stale).
DEFAULT_SAVED_SEARCHES = ["41", "43"]

# Matches both the two-line notice-caption form ("STREET\nCITY, ST ZIP") and
# the single-line form ("STREET, CITY, ST ZIP").
ADDRESS_RE = re.compile(
    r"([0-9][A-Za-z0-9 .,#\-']{4,60}?)\s*(?:\n|,)\s*"
    r"([A-Za-z .'-]{2,40}),\s*(MD|DC|DE|VA)\s*(\d{5})",
)

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


def parse_loan_principal(text: str):
    m = LOAN_PRINCIPAL_RE.search(text)
    return m.group(1) if m else ""


def parse_auction_date(text: str):
    m = AUCTION_DATE_RE.search(text)
    return m.group(1) if m else ""


def parse_county_mention(text: str):
    m = COUNTY_MENTION_RE.search(text)
    return m.group(1).strip() if m else ""


NOTICE_TYPE_RULES = [
    ("foreclosure", re.compile(r"trustee'?s?\s+sale|substitute trustee|deed of trust|power of sale", re.I)),
    ("tax_sale", re.compile(r"tax\s+sale|tax\s+lien\s+sale", re.I)),
    ("probate", re.compile(r"notice to creditors|letters testamentary|personal representative|estate of", re.I)),
]


def classify_notice_type(text: str) -> str:
    for label, pattern in NOTICE_TYPE_RULES:
        if pattern.search(text):
            return label
    return "other"


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
    if not matches:
        return None
    street, city, state, zip5 = matches[-1].groups()
    return {
        "street": " ".join(street.split()),
        "city": city.strip().title(),
        "state": state.upper(),
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


def build_actions(saved_search_value: str, counties: list, days: int, max_pages: int) -> list:
    county_js = "\n".join(
        f"""(function(){{
            var cb = document.querySelector('#ctl00_ContentPlaceHolder1_as1_lstCounty_{COUNTY_CHECKBOX_INDEX[c]}');
            if (cb) {{ cb.checked = true; cb.dispatchEvent(new Event('click',{{bubbles:true}})); cb.dispatchEvent(new Event('change',{{bubbles:true}})); }}
        }})();"""
        for c in counties
        if c in COUNTY_CHECKBOX_INDEX
    )

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
                var dr = document.querySelector('#ctl00_ContentPlaceHolder1_as1_rbLastNumDays');
                if (dr) {{ dr.checked = true; dr.dispatchEvent(new Event('change',{{bubbles:true}})); }}
                var nd = document.querySelector('#ctl00_ContentPlaceHolder1_as1_txtLastNumDays');
                if (nd) {{ nd.value = '{days}'; nd.dispatchEvent(new Event('input',{{bubbles:true}})); nd.dispatchEvent(new Event('change',{{bubbles:true}})); }}
            """,
        },
        {"type": "click", "selector": "#ctl00_ContentPlaceHolder1_as1_btnGo"},
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


def run_saved_search(creds: dict, saved_search_value: str, counties: list, days: int, max_pages: int) -> list:
    actions = build_actions(saved_search_value, counties, days, max_pages)
    # Substitute credentials into the write actions without ever logging them.
    for a in actions:
        if a.get("type") == "write" and a["text"] == "{{email}}":
            a["text"] = creds["email"]
        elif a.get("type") == "write" and a["text"] == "{{password}}":
            a["text"] = creds["password"]

    resp = requests.post(
        FIRECRAWL_URL,
        headers={"Authorization": f"Bearer {creds['firecrawl_key']}", "Content-Type": "application/json"},
        json={"url": LOGIN_URL, "actions": actions, "formats": ["html"]},
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    scrapes = (data.get("actions") or {}).get("scrapes") or []
    return [s.get("html", "") for s in scrapes if s.get("html")]


CURRENT_PAGE_RE = re.compile(r'lblCurrentPage"[^>]*>\s*(\d+)\s*<')


def detected_page_number(html: str):
    m = CURRENT_PAGE_RE.search(html)
    return int(m.group(1)) if m else None


def parse_grid_html(html: str, saved_search_value: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
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
        rows.append(
            {
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
    return rows


IN_FOOTPRINT_STATES = {"MD", "DC", "DE", "VA"}


def filter_footprint(rows: list) -> tuple:
    """Drop rows whose parsed address isn't MD/DC/DE/VA.

    Live 2026-08-21: checking only MD county boxes still returned a Prince
    William County, VA notice AND a genuine Orange County, VA trustee sale
    (the site's county filter doesn't hard-exclude every out-of-footprint
    result, and there is no VA checkbox to select in the first place -- see
    the note above DEFAULT_COUNTIES). VA is kept rather than dropped since
    that leakage is real content, not noise; this only drops a state truly
    outside the MD/DC/DE/VA area (e.g. a law firm HQ in another state with no
    in-area property mentioned at all). Rows with no parsed address are kept
    -- the notice snippet was truncated before any address appeared, not
    proof the property is out of scope.
    """
    kept, dropped = [], []
    for r in rows:
        if r["state"] and r["state"] not in IN_FOOTPRINT_STATES:
            dropped.append(r)
        else:
            kept.append(r)
    return kept, dropped


def dedupe(rows: list) -> list:
    seen = set()
    out = []
    for r in rows:
        key = (r["street"].lower(), r["city"].lower(), r["date_published"])
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
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--max-pages", type=int, default=10,
                     help="Pages per saved search (newest-first). The synthetic-click fix makes this "
                          "reliable now; each page costs one Firecrawl action round-trip, so a broad "
                          "search 60-80 pages deep still needs multiple runs or a higher value here.")
    ap.add_argument("--out", default="output/mddc_trustee_sale.csv")
    args = ap.parse_args()

    creds = load_credentials()
    counties = [c.strip() for c in args.counties.split(",") if c.strip()]
    unknown = [c for c in counties if c not in COUNTY_CHECKBOX_INDEX]
    if unknown:
        raise SystemExit(f"Unknown county name(s): {unknown}. Known: {sorted(COUNTY_CHECKBOX_INDEX)}")

    all_rows = []
    for sv in [s.strip() for s in args.saved_searches.split(",") if s.strip()]:
        print(f"Saved search {sv}: logging in and searching {len(counties)} counties, last {args.days} days...")
        htmls = run_saved_search(creds, sv, counties, args.days, args.max_pages)
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
        print(f"  dropped {len(out_of_footprint)} out-of-footprint (non MD/DC/DE) row(s), e.g. "
              f"{out_of_footprint[0]['city']}, {out_of_footprint[0]['state']}")
    rows = dedupe(in_footprint)
    print(f"{len(all_rows)} raw rows, {len(rows)} after footprint filter + de-dup")

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["publication", "date_published", "notice_type", "street", "city", "state", "zip",
                  "county", "loan_principal", "auction_date",
                  "saved_search", "notice_text_snippet", "source_url"]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
