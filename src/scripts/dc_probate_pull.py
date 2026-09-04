"""DC probate pull -- DWLR weekly legal notices + DC Courts Tyler Odyssey portal.

Two sources, one ledger:

1. DWLR (Daily Washington Law Reporter) -- https://dwlr.com/online-legal-notices/
   One server-rendered page holding the TWO most recent weeks of full notice
   text (plain GET works; verified 2026-09-03). Probate Division notices carry
   case number ("2026 ADM 000823"), decedent + AKAs, DOD, WITH/WITHOUT a Will,
   PR name + mailing address, attorney, Date of First Publication. Revocable-
   trust creditor notices (settlor + trustee, NO case number) are captured as
   pre-probate rows. The decedent's PROPERTY address is never in the notice.

2. Tyler Odyssey portal -- https://portal-dc.tylertech.cloud/Portal/
   Smart Search walked FORWARD by case number from the checkpoint (seed:
   2026-ADM-000838, Basem's last known case) until --miss-tolerance
   consecutive numbers return nothing. Case detail should carry party
   addresses. Prior recon (output/dpd_dc_ftm_investigation.md par.6): case info
   is anonymous for a person, but headless loads hit an AWS WAF challenge
   ("amzn-captcha-verify-button"). Ladder: persisted storage_state (solve once
   with --headed) -> 2Captcha amazon_waf -> abort after 3 solves in one run.

Address discovery/verification: DC ITSPE (Integrated Tax System Public
Extract) via the FREE anonymous ArcGIS REST table -- layer 53 of
DCGIS_DATA/Property_and_Land_WebMercator. Verified live 2026-09-03 both ways:
PREMISEADD LIKE '3650 APPLETON%%' -> "JOHNSON, DAVID R"; OWNERNAME LIKE
'%%KEUNEN%%' -> clean empty. OWNERNAME format is "LAST, FIRST M". SALEDATE is
epoch milliseconds.

Output CSV uses md_register_of_wills_pull.CSV_COLUMNS (same review-workbook
contract as the MD RoW pull); ledger/checkpoint follow the same conventions.
REVIEW-ONLY: nothing here touches DataSift.

Usage:
    python src/scripts/dc_probate_pull.py --doctor
    python src/scripts/dc_probate_pull.py --source dwlr --out output/dc_portal_probe/dwlr_test.csv
    python src/scripts/dc_probate_pull.py --source portal --headed --start-case 2026-ADM-000839 --max-cases 2
    python src/scripts/dc_probate_pull.py --source portal --start-case 2026-ADM-000839 --max-cases 15 --no-addresses
    python src/scripts/dc_probate_pull.py --source both --commit

Traps confirmed so far (extend as recon lands):
  - DWLR dates are inconsistent: "July, 26, 2026" (comma after month),
    "MAY 18, 2026", "AUG. 18, 2026" -- parse via month-name map, never strptime.
  - DC quadrant tokens (NW/NE/SW/SE) arrive as their OWN comma-separated part
    in DWLR addresses ("1622-A BELMONT STREET, NW, WASHINGTON, DC 20009") --
    merge them into the street, do not mistake them for a city.
  - The DWLR page URL never changes; idempotency lives in ledger keys only.
    Rotation LAGS: on 2026-09-03 the page still showed the Aug 17 + Aug 24
    weeks, so the staleness warning threshold is 16 days, warn-only.
  - Trust notices have no case number: ledger key DC|TRUST-<name>-<pubdate>.
    If a later ADM case matches a TRUST key's decedent tokens, a warning is
    printed for the reviewer to merge by hand.
  - Portal checkpoint advances to (highest HIT + 1), never past a miss, so a
    transient portal failure cannot skip real cases.
  - NEVER run `--source dwlr --commit` once the portal has seeded the ledger:
    for a case both sources know, reconcile_ledger would overwrite the richer
    portal record (decedent residence, live status) with the DWLR-only view.
    The daily DWLR job is commit-less by design; the portal job commits only
    portal records; `--source both --commit` merges before reconciling and is
    safe.
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent))
from md_register_of_wills_pull import (  # noqa: E402
    CSV_COLUMNS,  # noqa: F401  (re-exported for tooling that introspects columns)
    _split_pr_name,
    load_json,
    reconcile_ledger,
    save_json,
    split_street_city_tokens,
)
from md_land_records_lookup import name_match_level  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
OUTPUT_DIR = ROOT / "output"

DWLR_URL = "https://dwlr.com/online-legal-notices/"
PORTAL_BASE = "https://portal-dc.tylertech.cloud/Portal"
SMART_SEARCH_URL = f"{PORTAL_BASE}/Home/Dashboard/29"
ITSPE_URL = ("https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/"
             "Property_and_Land_WebMercator/MapServer/53/query")

LEDGER_PATH = OUTPUT_DIR / "dc_probate_ledger.json"
LAST_RUN_PATH = OUTPUT_DIR / "dc_probate_last_run.json"
PORTAL_STATE_PATH = OUTPUT_DIR / "dc_portal_state.json"
PROBE_DIR = OUTPUT_DIR / "dc_portal_probe"

COUNTY = "District of Columbia"
SEED_START_CASE = "2026-ADM-000838"   # last case Basem pulled; the walk starts at +1
# The live challenge (captured 2026-09-03) is the AWS WAF interactive CAPTCHA in
# region us-gov-west-1: title "Human Verification", window.gokuProps, verify
# button #amzn-btn-verify-internal, scripts on *.awswaf.com. The earlier
# "amzn-captcha-verify-button" guess is NOT on this variant, so detection keys
# on several stable markers instead of one id.
WAF_MARKERS = ("gokuProps", "awswaf.com", "amzn-btn-verify-internal", "Human Verification")
WAF_COOKIE_DOMAIN = ".tylertech.cloud"   # from the page's awsWafCookieDomainList
MAX_WAF_SOLVES_PER_RUN = 30   # the session re-challenges mid-walk; each solve ~$0.003
                              # (30 covers a long backfill; a normal run uses 1-2)
DWLR_STALE_DAYS = 16                  # rotation lag observed live; warn, never fail
DWLR_MIN_BODY = 20_000                # the 2-week page is large; tiny body = block/redesign

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# ── shared parsing helpers ─────────────────────────────────────────────────

MONTHS = {
    "JAN": 1, "JANUARY": 1, "FEB": 2, "FEBRUARY": 2, "MAR": 3, "MARCH": 3,
    "APR": 4, "APRIL": 4, "MAY": 5, "JUN": 6, "JUNE": 6, "JUL": 7, "JULY": 7,
    "AUG": 8, "AUGUST": 8, "SEP": 9, "SEPT": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTOBER": 10, "NOV": 11, "NOVEMBER": 11, "DEC": 12, "DECEMBER": 12,
}

DATE_WORDS_RE = re.compile(r"([A-Za-z]+)\.?,?\s+(\d{1,2}),?\s+(\d{4})")


def parse_word_date(raw: str) -> str:
    """'MAY 18, 2026' / 'Aug. 18, 2026' / 'July, 26, 2026' -> 'MM/DD/YYYY'.
    DWLR mixes all three forms (the comma-after-month one is live on the
    Judith Johnson trust notice), so strptime is a trap -- map the month word.
    """
    m = DATE_WORDS_RE.search(raw or "")
    if not m:
        return ""
    month = MONTHS.get(m.group(1).upper().rstrip("."))
    if not month:
        return ""
    return f"{month:02d}/{int(m.group(2)):02d}/{m.group(3)}"


PUB_DATES_FIRST_RE = re.compile(r"([A-Za-z]+)\.?,?\s+(\d{1,2})(?:\s*,\s*\d{1,2})*\s*,\s*(\d{4})")


def parse_pub_dates_first(raw: str) -> str:
    """A Pub Dates line lists MULTIPLE days before one year ('Aug. 18, 25,
    2026'), which defeats the single-date regex -- take the FIRST day."""
    m = PUB_DATES_FIRST_RE.search(raw or "")
    if not m:
        return parse_word_date(raw)
    month = MONTHS.get(m.group(1).upper().rstrip("."))
    if not month:
        return ""
    return f"{month:02d}/{int(m.group(2)):02d}/{m.group(3)}"


def _date_key(mdy: str) -> tuple:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", mdy or "")
    return (m.group(3), m.group(1), m.group(2)) if m else ("", "", "")


DC_QUADRANTS = {"NW", "NE", "SW", "SE"}


def split_dc_address(blob: str) -> dict:
    """DWLR inline addresses are comma-delimited with the quadrant as its OWN
    part: '1622-A BELMONT STREET, NW, WASHINGTON, DC 20009' -> street
    '1622-A BELMONT STREET NW', city 'WASHINGTON'. Falls back to the MD
    suffix-split for comma-less blobs (which already handles a post-
    directional after the suffix)."""
    blob = " ".join((blob or "").split()).strip(" .")
    if not blob:
        return {"street": "", "city": "", "state": "", "zip": ""}
    m = re.search(r",?\s*([A-Za-z]{2})[.]?\s+(\d{5})(?:-\d{4})?\s*$", blob)
    state, zip5, body = ("", "", blob)
    if m:
        state, zip5 = m.group(1).upper(), m.group(2)
        body = blob[: m.start()].strip(" ,")
    parts = [p.strip() for p in body.split(",") if p.strip()]
    if len(parts) >= 2:
        def _quad(p: str) -> str | None:
            q = p.upper().replace(".", "")
            return q if q in DC_QUADRANTS else None

        city = parts[-1]
        street_parts = []
        for p in parts[:-1]:
            q = _quad(p)
            if q and street_parts:
                street_parts[-1] += f" {q}"
            else:
                street_parts.append(p)
        # A quadrant can also trail the CITY slot when the street is the only
        # earlier part ("123 MAIN ST, NW" with no city) -- treat that as street.
        q = _quad(city)
        if q:
            street_parts[-1] += f" {q}"
            city = ""
        return {"street": " ".join(street_parts), "city": city, "state": state, "zip": zip5}
    st_toks, city_toks = split_street_city_tokens(body.split())
    return {"street": " ".join(st_toks), "city": " ".join(city_toks), "state": state, "zip": zip5}


CASE_NO_RE = re.compile(r"\b(20\d{2})\s*[-–]?\s*ADM\s*[-–]?\s*(\d{1,6})\b", re.I)


def normalize_case_no(raw: str) -> str:
    m = CASE_NO_RE.search(raw or "")
    if not m:
        return ""
    return f"{m.group(1)}-ADM-{int(m.group(2)):06d}"


def case_parts(case_no: str) -> tuple[int, int]:
    m = re.match(r"(\d{4})-ADM-(\d{6})$", case_no)
    if not m:
        raise ValueError(f"bad case number: {case_no!r}")
    return int(m.group(1)), int(m.group(2))


def make_case_no(year: int, num: int) -> str:
    return f"{year}-ADM-{num:06d}"


def trust_key_slug(name: str, first_pub: str) -> str:
    slug = re.sub(r"[^A-Z0-9]+", "-", (name or "UNKNOWN").upper()).strip("-")
    return f"TRUST-{slug}-{(first_pub or 'NODATE').replace('/', '')}"


# ── DWLR ───────────────────────────────────────────────────────────────────

def fetch_dwlr() -> str:
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(DWLR_URL, headers={"User-Agent": UA}, timeout=60)
            resp.raise_for_status()
            if len(resp.text) < DWLR_MIN_BODY:
                raise RuntimeError(f"DWLR body only {len(resp.text)} bytes -- block or redesign, not an empty week")
            return resp.text
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"DWLR fetch failed after 3 attempts: {last_err}")


HEADER_MARK = "Superior Court of the District of Columbia"
TRUST_MARK = "NOTICE TO CREDITORS OF A SETTLOR OF A REVOCABLE TRUST"

DECEASED_LINE_RE = re.compile(r"(?m)^\s*([A-Z][A-Za-z .,'’()/-]+?),?\s+Deceased\b")
AKA_SPLIT_RE = re.compile(r"\s+A/?K/?A\s+", re.I)
DOD_RE = re.compile(
    r"who died on\s+([A-Za-z]+\.?,?\s+\d{1,2},?\s+\d{4})\s*,?\s*(WITH(?:OUT)?)\s+a\s+Will", re.I)
DOD_BARE_RE = re.compile(r"died on\s+([A-Za-z]+\.?,?\s+\d{1,2},?\s+\d{4})", re.I)
PR_APPOINT_RE = re.compile(
    r"(.{3,200}?),?\s+whose\s+address(?:\(es\))?(?:es)?\s+(?:is/are|is|are)\s*:?\s+(.+?)\s*,?\s+"
    r"(?:was/were|was|were)\s+appointed", re.I | re.S)
FIRST_PUB_RE = re.compile(r"Date of first publication[:.]?\s+([A-Za-z]+\.?,?\s+\d{1,2},?\s+\d{4})", re.I)
ATTORNEY_LINE_RE = re.compile(r"(?m)^\s*(.+?),\s*(?:Esq\.?,?\s*)?(?:Petitioner/)?Attorney\s*$", re.I)
# Notice of Standard Probate is petition-stage: no PR exists yet, but the
# petitioner (the future PR / decision-maker) is named in the body.
PETITIONER_RE = re.compile(
    r"petition has been filed in this Court by\s+(.{3,120}?)\s*,?\s+for\s+(?:standard\s+)?probate", re.I | re.S)
NOTICE_HEADING_RE = re.compile(r"(?m)^\s*(Notice of [^\n]+)$", re.I)
PUB_DATES_RE = re.compile(r"Pub(?:\.|lication)? Dates?[.:]?\s*([^\n]+)", re.I)
TRUSTEE_RE = re.compile(
    r"the undersigned,?\s+(.{3,120}?),?\s+whose address(?:\(es\))?(?:es)? (?:is/are|is|are)\s+(.+?),?\s+"
    r"(?:is|are)\s+(?:a\s+|Co-)?Trustees?", re.I | re.S)
SETTLOR_RE = re.compile(r"notice that\s+(.{3,120}?)\s+died on", re.I)
TRUST_NAME_RE = re.compile(r"interested in\s+(?:the\s+)?(.{3,120}?)\s*:", re.I)


# PR_APPOINT_RE's name group must reach BACK from "whose address" -- but
# re.search picks the EARLIEST possible match start, so a lazy .{3,200}
# swallows the attorney block and headings above the PR sentence (measured
# live 2026-09-03: all 41 PR-bearing notices over-captured, and the AND in
# "And Notice to Unknown Heirs" then forged a phantom second PR). Trim the
# blob to whatever follows the LAST boundary phrase.
PR_NAME_BOUNDARY_RE = re.compile(
    r"(?:Unknown Heirs|Notice to Creditors|Notice of Appointment|Notice of Standard Probate|"
    r"PROBATE DIVISION|,\s*Deceased\b|,\s*Attorney\b|\d{5}(?:-\d{4})?)[.,:]?\s*", re.I)


def _trim_pr_names(blob: str) -> str:
    last_end = 0
    for m in PR_NAME_BOUNDARY_RE.finditer(blob):
        last_end = m.end()
    return blob[last_end:].strip(" ,.:;")


def dwlr_page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    return re.sub(r"\n{2,}", "\n", re.sub(r"[ \t]+", " ", text))


def segment_dwlr(text: str) -> tuple[list[str], list[str]]:
    """Return (probate_chunks, trust_chunks). Probate chunks run from one
    Superior Court header to the next; trust chunks run from the trust
    heading to the following 'Pub Dates' line (their attorney letterhead sits
    BEFORE the heading and is deliberately not captured in v1)."""
    starts = [m.start() for m in re.finditer(re.escape(HEADER_MARK), text, re.I)]
    probate_chunks = []
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        chunk = text[s:end]
        if re.search(r"PROBATE\s+DIVISION", chunk, re.I) and CASE_NO_RE.search(chunk):
            probate_chunks.append(chunk)
    trust_chunks = []
    for m in re.finditer(re.escape(TRUST_MARK), text, re.I):
        tail = text[m.start(): m.start() + 4000]
        pub = PUB_DATES_RE.search(tail)
        trust_chunks.append(tail[: pub.end()] if pub else tail)
    return probate_chunks, trust_chunks


def parse_probate_notice(chunk: str) -> dict | None:
    case_no = normalize_case_no(chunk)
    if not case_no:
        return None
    rec: dict = {"case_no": case_no, "notice_type": "probate", "sources": ["dwlr"],
                 "dwlr_raw": chunk.strip()[:2500]}

    dec_m = DECEASED_LINE_RE.search(chunk)
    if dec_m:
        names = [n.strip(" ,") for n in AKA_SPLIT_RE.split(dec_m.group(1)) if n.strip(" ,")]
        rec["decedent_name"] = names[0]
        rec["decedent_akas"] = names[1:]
        first, last = _split_pr_name(names[0].title())
        rec["decedent_first_name"], rec["decedent_last_name"] = first, last

    dod_m = DOD_RE.search(chunk)
    if dod_m:
        rec["dod"] = parse_word_date(dod_m.group(1))
        rec["has_will"] = dod_m.group(2).upper() == "WITH"
    else:
        bare = DOD_BARE_RE.search(chunk)
        if bare:
            rec["dod"] = parse_word_date(bare.group(1))

    pr_m = PR_APPOINT_RE.search(chunk)
    if pr_m:
        pr_names_blob = _trim_pr_names(" ".join(pr_m.group(1).split()))
        addr_blob = " ".join(pr_m.group(2).split())
        # Multi-PR: "A AND B, whose addresses are X and Y" -- when the name
        # side splits cleanly on AND but the address side does not, every PR
        # gets the whole blob rather than a wrong slice.
        names = [n.strip(" ,") for n in re.split(r"\s+(?:AND|&)\s+", pr_names_blob, flags=re.I) if n.strip(" ,")]
        addrs = [a.strip(" ,") for a in re.split(r"\s+(?:AND|&)\s+", addr_blob, flags=re.I) if a.strip(" ,")]
        if len(addrs) != len(names):
            addrs = [addr_blob] * len(names)
        prs = []
        for name, addr in zip(names, addrs):
            parsed = split_dc_address(addr)
            prs.append({"name": name.title(), **parsed})
        rec["personal_reps"] = prs

    if not rec.get("personal_reps"):
        pet_m = PETITIONER_RE.search(chunk)
        if pet_m:
            name = " ".join(pet_m.group(1).split())
            rec["petitioner"] = name
            rec["personal_reps"] = [{"name": name.title(), "street": "", "city": "", "state": "", "zip": ""}]

    att_m = ATTORNEY_LINE_RE.search(chunk)
    if att_m:
        att_start = att_m.end()
        heading = NOTICE_HEADING_RE.search(chunk, att_start)
        addr_blob = chunk[att_start: heading.start()] if heading else chunk[att_start: att_start + 200]
        parsed = split_dc_address(" ".join(addr_blob.split()))
        rec["attorney"] = {"name": " ".join(att_m.group(1).split()).title(), **parsed}

    heading_m = NOTICE_HEADING_RE.search(chunk)
    if heading_m:
        rec["status"] = " ".join(heading_m.group(1).split())

    pub_m = FIRST_PUB_RE.search(chunk)
    if pub_m:
        rec["legal_notice_published_on"] = parse_word_date(pub_m.group(1))
    pd_m = PUB_DATES_RE.search(chunk)
    if pd_m:
        rec["pub_dates_raw"] = pd_m.group(1).strip()
        if not rec.get("legal_notice_published_on"):
            rec["legal_notice_published_on"] = parse_pub_dates_first(pd_m.group(1))
    return rec


def parse_trust_notice(chunk: str) -> dict | None:
    rec: dict = {"notice_type": "trust", "sources": ["dwlr"], "dwlr_raw": chunk.strip()[:2500],
                 "status": "Revocable trust notice (pre-probate)"}
    settlor_m = SETTLOR_RE.search(chunk)
    if not settlor_m:
        return None
    rec["decedent_name"] = " ".join(settlor_m.group(1).split())
    first, last = _split_pr_name(rec["decedent_name"].title())
    rec["decedent_first_name"], rec["decedent_last_name"] = first, last
    trust_m = TRUST_NAME_RE.search(chunk)
    if trust_m:
        rec["trust_name"] = " ".join(trust_m.group(1).split())
    dod_m = DOD_BARE_RE.search(chunk)
    if dod_m:
        rec["dod"] = parse_word_date(dod_m.group(1))
    trustee_m = TRUSTEE_RE.search(chunk)
    if trustee_m:
        parsed = split_dc_address(" ".join(trustee_m.group(2).split()))
        rec["personal_reps"] = [{"name": " ".join(trustee_m.group(1).split()).title(), **parsed}]
    pd_m = PUB_DATES_RE.search(chunk)
    if pd_m:
        rec["pub_dates_raw"] = pd_m.group(1).strip()
        rec["legal_notice_published_on"] = parse_pub_dates_first(pd_m.group(1))
    return rec


def run_dwlr(since_mdy: str) -> dict[str, dict]:
    """Pull + parse the DWLR page. Returns {estate_number: record} where
    estate_number is the normalized case number or a TRUST- slug."""
    print("DWLR: fetching notices page...")
    html = fetch_dwlr()
    text = dwlr_page_text(html)
    probate_chunks, trust_chunks = segment_dwlr(text)
    print(f"DWLR: {len(probate_chunks)} probate-division notices, {len(trust_chunks)} trust notices on page.")
    records: dict[str, dict] = {}
    newest_pub = ""
    for chunk in probate_chunks:
        rec = parse_probate_notice(chunk)
        if not rec:
            continue
        pub = rec.get("legal_notice_published_on", "")
        newest_pub = max(newest_pub, pub, key=_date_key) if pub else newest_pub
        if since_mdy and pub and _date_key(pub) < _date_key(since_mdy):
            continue
        records[rec["case_no"]] = rec
    for chunk in trust_chunks:
        rec = parse_trust_notice(chunk)
        if not rec:
            continue
        pub = rec.get("legal_notice_published_on", "")
        newest_pub = max(newest_pub, pub, key=_date_key) if pub else newest_pub
        if since_mdy and pub and _date_key(pub) < _date_key(since_mdy):
            continue
        records[trust_key_slug(rec["decedent_name"], pub)] = rec
    if newest_pub:
        try:
            age = (datetime.now() - datetime.strptime(newest_pub, "%m/%d/%Y")).days
            if age > DWLR_STALE_DAYS:
                print(f"DWLR WARNING: newest first-publication date {newest_pub} is {age} days old "
                      f"-- page rotation missed, format drift, or the paper is behind.")
        except ValueError:
            pass
    return records


# ── Tyler portal ───────────────────────────────────────────────────────────

SEARCH_INPUT_SELECTORS = [
    "#caseCriteria_SearchCriteria",
    "input[name='caseCriteria.SearchCriteria']",
    "#SearchCriteriaContainer input[type='text']",
]
SEARCH_SUBMIT_SELECTORS = [
    "#btnSSSubmit",
    "input[type='submit']",
    "button:has-text('Submit')",
]
NO_RESULTS_RE = re.compile(r"no cases match|returned no results|no results found", re.I)


def _waf_present(page) -> bool:
    """The us-gov-west-1 AWS WAF CAPTCHA has no single stable id; key on the
    verify button, gokuProps, the awswaf.com challenge scripts, or the
    interstitial title (any one is proof)."""
    try:
        content = page.content()
    except Exception:  # noqa: BLE001
        return False
    return any(mark in content for mark in WAF_MARKERS)


def solve_aws_waf(page, context, captcha_key: str) -> bool:
    """2Captcha amazon_waf solve off the challenge page. Modern AWS WAF (this
    one is region us-gov-west-1) requires the challenge.js and captcha.js
    source URLs in addition to gokuProps key/iv/context -- omitting them was
    why the first attempt failed. Best-effort; --headed is the escape hatch."""
    try:
        props = page.evaluate("() => window.gokuProps || null")
    except Exception:  # noqa: BLE001
        props = None
    if not props or not props.get("key"):
        print("  WAF: window.gokuProps not found -- cannot auto-solve (use --headed once).")
        return False
    # The two awswaf.com script URLs the solver needs.
    try:
        srcs = page.eval_on_selector_all(
            "script[src*='awswaf.com']", "els => els.map(e => e.src)") or []
    except Exception:  # noqa: BLE001
        srcs = []
    challenge_script = next((s for s in srcs if "challenge.js" in s), None)
    captcha_script = next((s for s in srcs if "captcha.js" in s), None)
    try:
        from twocaptcha import TwoCaptcha
        solver = TwoCaptcha(captcha_key)
        print(f"  WAF: solving via 2Captcha (amazon_waf; challenge_script={'y' if challenge_script else 'n'}, "
              f"captcha_script={'y' if captcha_script else 'n'})...")
        kwargs = {"sitekey": props["key"], "iv": props["iv"],
                  "context": props["context"], "url": page.url}
        if challenge_script:
            kwargs["challenge_script"] = challenge_script
        if captcha_script:
            kwargs["captcha_script"] = captcha_script
        sol = solver.amazon_waf(**kwargs)
        code = sol.get("code") if isinstance(sol, dict) else str(sol)
        token = None
        if code:
            try:
                token = json.loads(code).get("existing_token") if code.strip().startswith("{") else code
            except (ValueError, AttributeError):
                token = code
        if not token:
            print("  WAF: 2Captcha returned no token.")
            return False
        # AWS WAF reads the token from the aws-waf-token cookie on the parent
        # domain declared in awsWafCookieDomainList (tylertech.cloud).
        context.add_cookies([{"name": "aws-waf-token", "value": token,
                              "domain": WAF_COOKIE_DOMAIN, "path": "/"}])
        page.reload(wait_until="domcontentloaded")
        # AWS WAF validates the token client-side AFTER load and only then
        # drops the interstitial -- an immediate check reads STILL PRESENT
        # even on a good token. Poll.
        for _ in range(16):  # up to ~8s
            if not _waf_present(page):
                print("  WAF: token applied, challenge CLEARED.")
                return True
            time.sleep(0.5)
        print("  WAF: token applied but challenge STILL PRESENT after wait.")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"  WAF: auto-solve failed: {type(e).__name__}: {e}")
        return False


def ensure_no_waf(page, context, headed: bool, captcha_key: str, solve_counter: dict) -> None:
    if not _waf_present(page):
        return
    # Try 2Captcha FIRST, in headed mode too -- a visible browser renders (so
    # we can capture case data) while 2Captcha clears the puzzle with no human.
    # The human is the fallback, not the default.
    if captcha_key:
        for _ in range(3):  # a couple of solve attempts before falling back
            if solve_counter["n"] >= MAX_WAF_SOLVES_PER_RUN:
                break
            solve_counter["n"] += 1  # counts REAL solve calls only ($ budget)
            if solve_aws_waf(page, context, captcha_key):
                return
    if headed:
        print("  WAF: 2Captcha did not clear it -- please solve the puzzle in the browser "
              "window and click Confirm (waiting up to 4 minutes)...")
        for _ in range(80):  # up to 4 minutes
            time.sleep(3)
            if not _waf_present(page):
                print("  WAF cleared by human.")
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:  # noqa: BLE001
                    pass
                return
        raise RuntimeError("WAF challenge not cleared within 4 minutes (headed).")
    if not captcha_key:
        raise RuntimeError("WAF challenge hit headless and CAPTCHA_API_KEY is not set -- "
                           "run once with --headed to seed the session state.")
    raise RuntimeError("WAF auto-solve failed after retries -- run once with --headed to seed the session.")


def _first_visible(page, selectors: list[str]):
    for sel in selectors:
        loc = page.locator(sel)
        try:
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:  # noqa: BLE001
            continue
    return None


STYLE_PREFIX_RE = re.compile(r"^\s*In\s+Re:?\s*|^\s*Estate of:?\s*|^\s*In (?:the )?Matter of:?\s*", re.I)


def parse_results_row(html: str, case_no: str) -> dict | None:
    """Read the Smart Search RESULTS grid row for this case. Confirmed live
    2026-09-03: the grid is a <table> of <tr>, each row
    [<a class=caseLink>CASE#</a>, Style/Defendant, File Date, Status,
    Location]. The Details (Register of Actions) page is a JS route that the
    anonymous flow does not reliably reach under automation AND -- per DC
    probate privacy -- would not carry party home addresses anyway, so the
    grid row is the portal's contribution: existence, decedent name, file
    date, status, case type. The property address comes from ITSPE off the
    decedent name (same as the DWLR half)."""
    soup = BeautifulSoup(html, "html.parser")
    # One matching pass: find the grid ROW carrying the case number (whether
    # the number renders as the caseLink's text or as plain cell text).
    row = None
    for tr in soup.find_all("tr"):
        if case_no in " ".join(tr.get_text().split()):
            row = tr
            break
    if row is None:
        return None
    cells = [" ".join(td.get_text().split()) for td in row.find_all(["td", "th"])]
    cells = [c for c in cells if c]  # drop the leading empty expander cell
    rec: dict = {"case_no": case_no, "notice_type": "probate", "sources": ["portal"],
                 "portal_raw": " | ".join(cells)[:1000]}
    # The Register of Actions (case detail: parties, addresses, docket incl.
    # the estate petition) is reached via the caseLink's data-url; the id is
    # session-bound so it must be navigated INSIDE the live browser context.
    link = row.select_one("a.caseLink")
    if link and link.get("data-url"):
        rec["detail_url"] = link["data-url"]
    # cells: [CASE#, STYLE, FILE DATE, STATUS, LOCATION]
    if len(cells) >= 2:
        name = STYLE_PREFIX_RE.sub("", cells[1]).strip(" :,")
        name = re.sub(r"\s*\((?:Deceased|Decedent)\)\s*", "", name, flags=re.I).strip()
        # Odyssey styles a generational suffix with a comma ("Darrell, JR");
        # drop it so _split_pr_name keeps the suffix on the surname cleanly.
        name = re.sub(r",\s*(JR|SR|II|III|IV|V)\b", r" \1", name, flags=re.I)
        # Guard against a misaligned grid row leaking a bare date/number into
        # the style cell (seen live on a brand-new filing with no style yet).
        if name and not re.match(r"^[\d/.\-\s]+$", name):
            rec["decedent_name"] = name
            first, last = _split_pr_name(name.title())
            rec["decedent_first_name"], rec["decedent_last_name"] = first, last
    for c in cells[2:]:
        m = re.match(r"^(\d{1,2}/\d{1,2}/\d{4})$", c)
        if m and not rec.get("filing_date"):
            rec["filing_date"] = m.group(1)
        elif c.lower() in ("open", "closed", "reopened", "pending", "disposed", "active", "inactive"):
            rec["status"] = c
        elif "probate" in c.lower() or "estate" in c.lower():
            rec["case_type"] = c
    return rec


ROA_SERVICE = f"{PORTAL_BASE}/app/RegisterOfActionsService/"
CASE_ID_HASH_RE = re.compile(r"/#/([0-9A-F]{32,})/", re.I)
# Party ConnectionType -> our role. The Decedent's own address is the
# residence/property lead; the Applicant/Petitioner/PR is who to contact.
PR_CONNECTIONS = {"personal representative", "applicant", "petitioner", "co-personal representative",
                  "administrator", "executor", "special administrator"}


def _party_address(party: dict) -> dict:
    """First usable address on a party -> our street/city/state/zip dict."""
    for addr in party.get("Addresses") or []:
        line1 = " ".join((addr.get("AddressLine1") or "").split())
        line2 = " ".join((addr.get("AddressLine2") or "").split())
        street = ", ".join(x for x in (line1, line2) if x)
        if street or addr.get("City"):
            return {"street": street, "city": addr.get("City", ""),
                    "state": addr.get("State", ""), "zip": addr.get("PostalCode", "")}
    return {"street": "", "city": "", "state": "", "zip": ""}


def parse_parties_json(data: dict, rec: dict) -> None:
    """Parse the RegisterOfActionsService Parties('<caseId>') JSON onto rec:
    the Decedent's residence becomes the property address (a real case-file
    address, confidence HIGH), plus DOD, the PR/petitioner(s), attorney and
    heirs. Verified live 2026-09-03 on 2026-ADM-000839 (Rachelle M Williams,
    DOD 02/25/2019, 2203 U Place SE)."""
    prs, heirs, attorneys = [], [], []
    for party in data.get("Parties") or []:
        conn = (party.get("ConnectionType") or "").strip()
        conn_l = conn.lower()
        # Skip dismissed/removed connections (e.g. 000840's Applicant Xenia
        # Elvis was dismissed, then re-appears as the active PR).
        conns = party.get("CasePartyConnections") or []
        if conns and all(c.get("Inactive") or c.get("Removed") for c in conns):
            continue
        name = party.get("FormattedName") or " ".join(
            filter(None, [party.get("NameFirst"), party.get("NameMid"), party.get("NameLast")]))
        addr = _party_address(party)
        is_atty = any(c.get("IsAttorney") for c in party.get("CasePartyConnections") or [])
        if conn_l in ("decedent", "deceased", "estate"):
            rec["decedent_name"] = name
            if party.get("NameFirst") or party.get("NameLast"):
                rec["decedent_first_name"] = " ".join(filter(None, [party.get("NameFirst"), party.get("NameMid")]))
                rec["decedent_last_name"] = party.get("NameLast", "")
            if party.get("DateOfDeath"):
                rec["dod"] = party["DateOfDeath"]
            akas = [a.get("FormattedName") for a in party.get("AdditionalNames") or []
                    if a.get("FormattedName")]
            if akas:
                rec["decedent_akas"] = sorted(set(akas))
            if addr["street"] and not rec.get("property_street"):
                rec["property_street"] = addr["street"]
                rec["property_city"] = addr["city"] or "Washington"
                rec["property_state"] = addr["state"] or "DC"
                rec["property_zip"] = addr["zip"]
                rec["property_lookup_source"] = "DC portal case record (decedent residence)"
                rec["property_lookup_confidence"] = "HIGH"
        elif is_atty or conn_l == "attorney":
            attorneys.append({"name": name.title(), **addr})
        elif conn_l in PR_CONNECTIONS:
            prs.append({"name": name.title(), "connection": conn, **addr})
        elif conn_l in ("heir", "interested party", "legatee", "beneficiary"):
            heirs.append({"name": name.title(), "connection": conn, **addr})
    def _dedupe(items):
        seen, out = set(), []
        for it in items:
            k = it["name"].lower()
            if k not in seen:
                seen.add(k)
                out.append(it)
        return out
    if prs:
        rec["personal_reps"] = _dedupe(prs)
    if attorneys and not rec.get("attorney"):
        rec["attorney"] = attorneys[0]
    if heirs:
        rec["heirs"] = _dedupe(heirs)


def portal_walk(start_case: str, max_cases: int, miss_tolerance: int,
                headed: bool, captcha_key: str, no_case_detail: bool = False
                ) -> tuple[dict[str, dict], dict]:
    """Walk Smart Search forward from start_case. Returns
    ({estate_number: record}, walk_info) where walk_info carries the highest
    hit, the gap log, and counts for the checkpoint math."""
    from playwright.sync_api import sync_playwright

    year, num = case_parts(start_case)
    records: dict[str, dict] = {}
    walk = {"hits": 0, "misses": 0, "gap_log": [], "highest_hit": None,
            "stopped_reason": "", "seconds_per_case": []}
    solve_counter = {"n": 0}
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    snapshots_existing = len(list(PROBE_DIR.glob("case_*.html")))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        ctx_kwargs = {"user_agent": UA, "viewport": {"width": 1400, "height": 900}}
        if PORTAL_STATE_PATH.exists():
            ctx_kwargs["storage_state"] = str(PORTAL_STATE_PATH)
        context = browser.new_context(**ctx_kwargs)
        # The RegisterOfActionsService refuses our own out-of-band GET (500),
        # but the app's OWN XHR succeeds -- so capture the browser's Parties
        # responses off the wire instead of re-requesting them.
        parties_by_case: dict[str, dict] = {}

        def _on_response(resp):
            try:
                url = resp.url
                if "RegisterOfActionsService/Parties(" not in url or resp.status != 200:
                    return
                m = re.search(r"Parties\('([0-9A-Fa-f]+)'\)", url)
                if m:
                    parties_by_case[m.group(1)] = resp.json()
            except Exception:  # noqa: BLE001
                pass

        context.on("response", _on_response)
        context._parties_by_case = parties_by_case  # reachable from _fetch_case_detail
        page = context.new_page()
        page.goto(SMART_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
        ensure_no_waf(page, context, headed, captcha_key, solve_counter)

        consecutive_misses = 0
        fetched = 0
        while fetched < max_cases:
            case_no = make_case_no(year, num)
            t0 = time.time()
            try:
                hit = _search_one_case(page, context, case_no, headed, captcha_key,
                                       solve_counter, snapshots_existing + walk["hits"],
                                       no_case_detail)
            except RuntimeError:
                context.storage_state(path=str(PORTAL_STATE_PATH))
                browser.close()
                raise
            walk["seconds_per_case"].append(round(time.time() - t0, 1))
            fetched += 1
            if hit:
                records[case_no] = hit
                walk["hits"] += 1
                walk["highest_hit"] = case_no
                consecutive_misses = 0
                print(f"  {case_no}: HIT -- {hit.get('decedent_name', '?')} "
                      f"(filed {hit.get('filing_date', '?')}, {hit.get('status', '?')})")
            else:
                walk["misses"] += 1
                walk["gap_log"].append(case_no)
                consecutive_misses += 1
                print(f"  {case_no}: no result ({consecutive_misses}/{miss_tolerance} consecutive)")
                if consecutive_misses >= miss_tolerance:
                    walk["stopped_reason"] = f"{miss_tolerance} consecutive misses (sequence head)"
                    break
            num += 1
            time.sleep(random.uniform(4, 8))
        if not walk["stopped_reason"]:
            walk["stopped_reason"] = f"--max-cases {max_cases} reached"

        context.storage_state(path=str(PORTAL_STATE_PATH))
        browser.close()
    return records, walk


def _search_one_case(page, context, case_no: str, headed: bool, captcha_key: str,
                     solve_counter: dict, snapshot_count: int,
                     _no_case_detail: bool = False) -> dict | None:
    page.goto(SMART_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
    ensure_no_waf(page, context, headed, captcha_key, solve_counter)

    box = _first_visible(page, SEARCH_INPUT_SELECTORS)
    if box is None:
        (PROBE_DIR / "search_page_unrecognized.html").write_text(page.content(), encoding="utf-8")
        raise RuntimeError("Smart Search input not found -- selectors need recon "
                           "(snapshot: dc_portal_probe/search_page_unrecognized.html)")
    box.fill(case_no)
    btn = _first_visible(page, SEARCH_SUBMIT_SELECTORS)
    if btn is None:
        raise RuntimeError("Smart Search submit button not found -- selectors need recon")
    btn.click()
    page.wait_for_load_state("domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:  # noqa: BLE001
        pass
    ensure_no_waf(page, context, headed, captcha_key, solve_counter)

    # Give the async results grid a beat to render into the DOM.
    try:
        page.wait_for_selector("a.caseLink, table tr", timeout=8_000)
    except Exception:  # noqa: BLE001
        pass
    body = page.content()
    if snapshot_count < 15:
        snap = PROBE_DIR / f"case_{case_no}.html"
        snap.write_text(body, encoding="utf-8")
    rec = parse_results_row(body, case_no)
    if rec is None:
        # A miss WITH the site's own "no cases match" text is a genuinely
        # unfilled number. A miss WITHOUT it means the grid markup drifted and
        # the parser is blind -- if that were silent, every case would "miss"
        # and the walk would end on a false sequence-head verdict, silently
        # skipping real cases. Make drift loud and keep the evidence.
        if not NO_RESULTS_RE.search(body):
            drift = PROBE_DIR / f"unparsed_{case_no}.html"
            drift.write_text(body, encoding="utf-8")
            print(f"    WARNING {case_no}: results page had neither a parsable grid row nor a "
                  f"no-results message -- possible grid drift (snapshot: {drift.name})")
        return None
    if snapshot_count < 15:
        rec["portal_snapshot"] = f"case_{case_no}.html"

    # Case file (decedent residence, PR, heirs, DOD) via the Parties JSON
    # service. Clicking the caseLink resolves the internal caseId into the
    # RegisterOfActions URL hash; we then call Parties('<caseId>') directly in
    # the same authenticated context -- no fragile Angular rendering, no
    # direct-GET 500 (the ?id= endpoint is JS-only).
    if not _no_case_detail:
        try:
            _fetch_case_detail(page, context, case_no, rec, headed, captcha_key, solve_counter)
        except Exception as e:  # noqa: BLE001
            print(f"    {case_no}: case-detail failed ({e}); results-row fields kept.")
    return rec


def _fetch_case_detail(page, context, case_no, rec, headed, captcha_key, solve_counter) -> None:
    link = page.locator(f"a.caseLink[title='{case_no}']")
    if not link.count():
        link = page.locator(f"a.caseLink:has-text('{case_no}')")
    if not link.count():
        print(f"    {case_no}: caseLink not found for detail; results-row fields kept.")
        return
    detail = page
    try:
        with context.expect_page(timeout=3_000) as popup_info:
            link.first.click()
        detail = popup_info.value  # opened in a new tab
    except Exception:  # noqa: BLE001 -- no popup; same-page navigation
        detail = page
    detail.wait_for_load_state("domcontentloaded")
    ensure_no_waf(detail, context, headed, captcha_key, solve_counter)
    # Wait for the Angular route to resolve the internal caseId into the hash.
    case_id = None
    for _ in range(30):  # up to ~15s
        m = CASE_ID_HASH_RE.search(detail.url)
        if m:
            case_id = m.group(1)
            break
        time.sleep(0.5)
    if not case_id:
        print(f"    {case_no}: caseId did not resolve in URL; results-row fields kept.")
        if detail is not page:
            detail.close()
        return
    # The RoA Angular app must actually RENDER for the browser to fetch the
    # case data -- proven 2026-09-03 that headless renders nothing (DOM empty)
    # and our own service GET 500s, while a real (headed) browser renders and
    # its OWN Parties XHR succeeds. Wait for the browser to fetch Parties for
    # this caseId (captured off the wire by the context response listener).
    parties_map = getattr(context, "_parties_by_case", {})
    data = None
    for _ in range(40):  # up to ~20s
        if case_id in parties_map:
            data = parties_map[case_id]
            break
        # wait_for_timeout, NOT time.sleep: the context's response listener is
        # only dispatched while Playwright's event loop is pumped, and a plain
        # sleep pumps nothing -- the captured Parties body would sit unseen
        # until the loop timed out.
        detail.wait_for_timeout(500)
    if data is None:  # last resort: our own GET (works only if session already primed)
        data = _get_parties(context, case_id)
    if data is not None:
        (PROBE_DIR / f"parties_{case_no}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        rec["detail_source"] = "portal Parties service"
        parse_parties_json(data, rec)
    else:
        print(f"    {case_no}: Parties fetch failed; results-row fields kept.")
    if detail is not page:
        detail.close()


def _get_parties(context, case_id: str):
    """Fetch Parties('<caseId>') via requests using the live browser context's
    cookies. The OData service authorises per SESSION-that-has-viewed-the-case,
    so prime the session with the same init calls the Angular app makes
    (Config, then CaseSummariesSlim) before requesting Parties."""
    jar = requests.cookies.RequestsCookieJar()
    for c in context.cookies():
        jar.set(c["name"], c["value"], domain=c.get("domain", "").lstrip(".") or None,
                path=c.get("path", "/"))
    hdr = {"User-Agent": UA, "Accept": "application/json"}

    def _get(url):
        try:
            return requests.get(url, headers=hdr, cookies=jar, timeout=45)
        except Exception as e:  # noqa: BLE001
            print(f"      GET error {url[-40:]}: {e}")
            return None

    _get(f"{ROA_SERVICE}api/Config?mode=portalembed")
    slim = _get(f"{ROA_SERVICE}CaseSummariesSlim?key={case_id}&mode=portalembed")
    if slim is not None and slim.status_code != 200:
        print(f"      CaseSummariesSlim HTTP {slim.status_code} (caseId={case_id[:12]}...)")
    url = f"{ROA_SERVICE}Parties('{case_id}')?mode=portalembed"
    for attempt in range(3):
        r = _get(url)
        if r is None:
            time.sleep(2.0)
            continue
        if r.status_code == 200 and r.text.strip():
            return r.json()
        if r.status_code == 500 and attempt < 2:
            time.sleep(2.5)
            continue
        print(f"      Parties HTTP {r.status_code} (caseId={case_id[:12]}...)")
        return None
    return None


# ── ITSPE (DC property records) ────────────────────────────────────────────

ITSPE_FIELDS = "OWNERNAME,PREMISEADD,SSL,ADDRESS1,CITYSTZIP,SALEDATE,SALEPRICE"


def itspe_query(where: str, limit: int = 50) -> list[dict]:
    resp = requests.get(ITSPE_URL, params={
        "where": where, "outFields": ITSPE_FIELDS, "f": "json",
        "returnGeometry": "false", "resultRecordCount": limit,
    }, headers={"User-Agent": UA}, timeout=45)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"ITSPE error: {data['error']}")
    return [f["attributes"] for f in data.get("features", [])]


def _sql_escape(s: str) -> str:
    return (s or "").replace("'", "''")


def _premise_split(premise: str) -> dict:
    premise = " ".join((premise or "").split())
    m = re.match(r"^(.*?)\s+WASHINGTON\s+DC\s+(\d{5})?", premise, re.I)
    if m:
        return {"street": m.group(1), "city": "Washington", "state": "DC", "zip": m.group(2) or ""}
    return {"street": premise, "city": "Washington", "state": "DC", "zip": ""}


def _sale_after_dod(attrs: dict, dod_mdy: str) -> bool:
    if not dod_mdy:
        return False
    raw = attrs.get("SALEDATE")
    if not raw:
        return False
    try:
        sale = datetime.fromtimestamp(int(raw) / 1000) if isinstance(raw, (int, float)) \
            else datetime.strptime(str(raw)[:10], "%Y-%m-%d")
        return sale > datetime.strptime(dod_mdy, "%m/%d/%Y")
    except (ValueError, OSError, OverflowError):
        return False


def itspe_find_by_owner(last: str, first: str, full_name: str, dod_mdy: str = "") -> dict:
    """Discover a DC parcel by decedent name. HIGH-only auto-accept with the
    Johnson guard: >10 candidates, or MEDIUM-best across >=2 distinct owner
    spellings, refuses rather than guesses."""
    last_u, first_u = _sql_escape(last.upper()), _sql_escape(first.upper())
    if not last_u:
        return {"found": False, "reason": "no decedent last name"}
    tiers = [
        f"UPPER(OWNERNAME) LIKE '{last_u} {first_u}%' OR UPPER(OWNERNAME) LIKE '{last_u}, {first_u}%'",
    ]
    if first_u:
        tiers.append(f"UPPER(OWNERNAME) LIKE '{last_u} {first_u[0]}%' OR UPPER(OWNERNAME) LIKE '{last_u}, {first_u[0]}%'")
    candidates: list[dict] = []
    for where in tiers:
        try:
            candidates = itspe_query(where)
        except Exception as e:  # noqa: BLE001
            return {"found": False, "reason": f"ITSPE query failed: {e}"}
        if candidates:
            break
        time.sleep(1.0)
    if not candidates:
        return {"found": False, "reason": "no ITSPE owner-name match"}
    if len(candidates) > 10:
        return {"found": False, "reason": f"ambiguous: {len(candidates)} owner candidates",
                "sdat_owner_names": sorted({c.get('OWNERNAME', '') for c in candidates})[:5]}

    scored = []
    for c in candidates:
        level = name_match_level(full_name, last, first, c.get("OWNERNAME", ""))
        if level == "LOW":
            continue
        if _sale_after_dod(c, dod_mdy):
            continue  # sold after death -- current owner is someone else
        scored.append((level, c))
    highs = [c for lv, c in scored if lv == "HIGH"]
    meds = [c for lv, c in scored if lv == "MEDIUM"]
    if not highs and not meds:
        return {"found": False, "reason": "ITSPE candidates all LOW or sold post-DOD",
                "sdat_owner_names": sorted({c.get('OWNERNAME', '') for c in candidates})[:5]}
    if not highs:
        distinct = {c.get("OWNERNAME", "") for c in meds}
        if len(distinct) >= 2:
            return {"found": False, "reason": f"ambiguous: MEDIUM-only across {len(distinct)} owner spellings",
                    "sdat_owner_names": sorted(distinct)[:5]}
    pool = highs or meds
    confidence = "HIGH" if highs else "MEDIUM"

    def is_primary(c: dict) -> bool:
        mail = " ".join(f"{c.get('ADDRESS1', '')} {c.get('CITYSTZIP', '')}".upper().split())
        prem = " ".join((c.get("PREMISEADD", "") or "").upper().split())
        street = _premise_split(prem)["street"]
        return bool(street) and street in mail

    primary = next((c for c in pool if is_primary(c)), pool[0])
    addr = _premise_split(primary.get("PREMISEADD", ""))
    extra = [c.get("PREMISEADD", "") for c in pool if c is not primary]
    return {"found": True, **addr, "confidence": confidence, "source": "ITSPE owner-name",
            "owner_names": [primary.get("OWNERNAME", "")], "ssl": primary.get("SSL", ""),
            "additional_parcels": extra}


# The portal spells street suffixes in full ("145 Elmira STREET SW"); ITSPE
# abbreviates them ("145 ELMIRA ST SW"). Without this an exact-address owner
# check misses and the record looks like a rental. Map full -> ITSPE form.
_SUFFIX_ABBR = {
    "STREET": "ST", "AVENUE": "AVE", "PLACE": "PL", "ROAD": "RD", "DRIVE": "DR",
    "COURT": "CT", "LANE": "LN", "TERRACE": "TER", "BOULEVARD": "BLVD",
    "PARKWAY": "PKWY", "CIRCLE": "CIR", "HIGHWAY": "HWY", "SQUARE": "SQ",
    "CRESCENT": "CRES", "ALLEY": "ALY", "PROMENADE": "PROM",
}
_UNIT_RE = re.compile(r"\s*(?:#|UNIT|APT|APARTMENT|STE|SUITE)\b.*$", re.I)


def _normalize_street_for_itspe(street: str, strip_unit: bool = True) -> str:
    s = " ".join((street or "").upper().split())
    if strip_unit:
        s = _UNIT_RE.sub("", s).strip()
    s = s.replace(",", " ")  # "2555 PEN AVE, #1019" -> drop the comma too
    return " ".join(_SUFFIX_ABBR.get(tok, tok) for tok in s.split() if tok).strip()


def itspe_verify_address(street: str, last: str, first: str, full_name: str) -> dict:
    norm = _normalize_street_for_itspe(street)
    st = _sql_escape(norm)
    if not st:
        return {"level": "", "owners": []}
    rows = []
    try:
        rows = itspe_query(f"UPPER(PREMISEADD) LIKE '{st}%'", limit=6)
        if not rows and norm != _normalize_street_for_itspe(street, strip_unit=False):
            # A condo may carry its unit in PREMISEADD -- retry with the unit.
            st2 = _sql_escape(_normalize_street_for_itspe(street, strip_unit=False))
            rows = itspe_query(f"UPPER(PREMISEADD) LIKE '{st2}%'", limit=6)
    except Exception as e:  # noqa: BLE001
        return {"level": "", "owners": [], "error": str(e)}
    if not rows:
        return {"level": "NO_PARCEL", "owners": []}
    levels = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    scored = [(name_match_level(full_name, last, first, r.get("OWNERNAME", "")), r.get("OWNERNAME", ""))
              for r in rows]
    best_level, best_owner = max(scored, key=lambda t: levels[t[0]])
    return {"level": best_level, "owner": best_owner,
            "owners": [r.get("OWNERNAME", "") for r in rows]}


_COMPANY_RE = re.compile(r"\b(INC|LLC|L\.?L\.?C|CORP|CO|COMPANY|FOUNDATION|TRUST|ASSOCIATION|"
                         r"LP|LLP|PARTNERS|PROPERTIES|MANAGEMENT|HOUSING|DEVELOPMENT|AUTHORITY|"
                         r"CHURCH|BANK|FUND|HOLDINGS|GROUP|ENTERPRISES)\b", re.I)


def _owner_surname_matches(owner: str, last: str) -> bool:
    if not owner or not last:
        return False
    return last.upper() in {t.strip(".,") for t in re.split(r"[\s,]+", owner.upper())}


def fill_addresses_itspe(ledger: dict, keys: set[str], save_cb=None) -> None:
    """Portal-supplied address -> verify owner via ITSPE; no address ->
    discover via owner-name query. Mirrors the RoW fill_addresses contract
    (property_lookup_checked guard, NOT_FOUND convention, incremental saves)."""
    estates = ledger.get("estates", {})
    todo = [(k, estates[k]) for k in sorted(keys)
            if k in estates and not estates[k].get("property_lookup_checked")]
    if not todo:
        print("  ITSPE: nothing new to resolve.")
        return
    print(f"  ITSPE: resolving/verifying {len(todo)} record(s).")
    for n, (key, rec) in enumerate(todo, 1):
        last = rec.get("decedent_last_name", "")
        first = (rec.get("decedent_first_name", "") or "").split()
        first = first[0] if first else ""
        full_name = f"{rec.get('decedent_first_name', '')} {last}".strip()
        rec["property_lookup_checked"] = True
        if rec.get("property_street") and rec.get("property_lookup_source", "").startswith("DC portal"):
            # The case file gives the decedent's RESIDENCE. Whether it is an
            # ESTATE ASSET depends on whether the decedent OWNED it -- test by
            # address (sidesteps common-name ambiguity). Keep the residence
            # shown either way (it is the PR/contact lead); state the verdict.
            residence = rec["property_street"]
            rec["property_residence"] = residence
            check = itspe_verify_address(residence, last, first, full_name)
            owner = check.get("owner") or (check.get("owners") or [""])[0]
            rec["property_itspe_owner"] = owner
            level = check.get("level")
            if level in ("HIGH", "MEDIUM"):
                rec["property_lookup_confidence"] = level
                rec["property_ownership"] = f"Decedent OWNS residence ({level} name match: {owner})"
                print(f"    {key}: residence OWNED by decedent -> {residence} ({level})")
            elif level == "LOW" and _owner_surname_matches(owner, last):
                # Same surname as the decedent (often the surviving spouse /
                # heir / PR) -- family-owned, a real lead, NOT a rental.
                rec["property_lookup_confidence"] = "FAMILY"
                verdict = f"Residence owned by a RELATIVE ({owner}) -- likely joint/heir property"
                rec["property_ownership"] = rec["property_lookup_reason"] = verdict
                print(f"    {key}: {verdict}")
            else:
                # Not the decedent and not a namesake relative -> look for
                # property the decedent owns ELSEWHERE, then classify.
                elsewhere = itspe_find_by_owner(last, first, full_name, rec.get("dod", "")) if last else {}
                if elsewhere.get("found"):
                    rec["property_owned_elsewhere"] = elsewhere["street"]
                    rec["property_owned_elsewhere_zip"] = elsewhere.get("zip", "")
                    rec["property_lookup_confidence"] = elsewhere["confidence"]
                    verdict = (f"Residence not owned by decedent (owner {owner or '?'}); decedent OWNS "
                               f"{elsewhere['street']} ({elsewhere['confidence']})")
                elif level == "NO_PARCEL":
                    rec["property_lookup_confidence"] = "REVIEW"
                    verdict = "Residence not found in ITSPE (condo/unit format?) -- verify ownership by hand"
                elif _COMPANY_RE.search(owner or ""):
                    rec["property_lookup_confidence"] = "RENTAL"
                    verdict = f"Residence is a RENTAL (owned by {owner}); no owned DC property found"
                else:
                    rec["property_lookup_confidence"] = "REVIEW"
                    verdict = f"Residence owned by {owner or '?'} (not the decedent) -- verify by hand"
                rec["property_ownership"] = rec["property_lookup_reason"] = verdict
                print(f"    {key}: {verdict}")
        elif last:
            result = itspe_find_by_owner(last, first, full_name, rec.get("dod", ""))
            if result.get("found"):
                rec["property_street"] = result["street"]
                rec["property_city"] = result["city"]
                rec["property_state"] = result["state"]
                rec["property_zip"] = result["zip"]
                rec["property_lookup_confidence"] = result["confidence"]
                rec["property_lookup_source"] = result["source"]
                rec["property_itspe_owner"] = " / ".join(result.get("owner_names", []))
                rec["property_ssl"] = result.get("ssl", "")
                if result.get("additional_parcels"):
                    rec["additional_parcels"] = result["additional_parcels"]
                    rec["property_lookup_reason"] = f"{len(result['additional_parcels'])} additional parcel(s) in ledger"
                print(f"    {key}: {result['street']} ({result['confidence']}, owner {rec['property_itspe_owner']})")
            else:
                rec["property_lookup_confidence"] = "NOT_FOUND"
                rec["property_lookup_reason"] = result.get("reason", "")
                rec["property_itspe_owner"] = " / ".join(result.get("sdat_owner_names", []))
                print(f"    {key}: not found -- {result.get('reason')}")
        if save_cb and n % 10 == 0:
            save_cb()
        time.sleep(1.0)
    if save_cb:
        save_cb()


# ── merge / checkpoint ─────────────────────────────────────────────────────

# When a case appears in BOTH sources, these DWLR fields beat the portal's --
# the published notice states the CURRENT PR/attorney mailing addresses and the
# publication dates. Everything else (filing date, live case status, case type,
# decedent residence from the party record, portal raw/snapshot) stays portal.
DWLR_WINS_FIELDS = (
    "personal_reps", "attorney", "dod", "has_will", "decedent_akas",
    "legal_notice_published_on", "pub_dates_raw", "petitioner", "dwlr_raw",
)


def merge_records(dwlr_recs: dict[str, dict], portal_recs: dict[str, dict]) -> dict:
    """Join on case number: portal is the case-number spine, DWLR wins on
    PR/attorney/DOD/first-pub (the notice states the CURRENT mailing address),
    the portal wins on filing date/status/decedent address. Returns the
    {(county, estate_number): record} shape reconcile_ledger expects."""
    merged: dict = {}
    for est_no in sorted(set(dwlr_recs) | set(portal_recs)):
        d, p = dwlr_recs.get(est_no), portal_recs.get(est_no)
        if d and p:
            # Portal is the base; ONLY these DWLR fields override it (the
            # precedence contract stated once -- a blanket overlay would let
            # any future DWLR-parser field silently beat the portal's).
            rec = dict(p)
            for k in DWLR_WINS_FIELDS:
                if d.get(k) not in (None, "", [], {}):
                    rec[k] = d[k]
            rec["sources"] = sorted(set(d.get("sources", [])) | set(p.get("sources", [])))
        else:
            rec = dict(d or p)
        merged[(COUNTY, est_no)] = rec
    return merged


def warn_trust_matches(ledger: dict, new_case_keys: list[str]) -> None:
    estates = ledger.get("estates", {})
    trust_names = {k: set(re.findall(r"[A-Z]+", (v.get("decedent_name") or "").upper()))
                   for k, v in estates.items() if "|TRUST-" in k}
    if not trust_names:
        return
    for key in new_case_keys:
        if "|TRUST-" in key:
            continue  # a trust key trivially token-matches itself
        rec = estates.get(key, {})
        toks = set(re.findall(r"[A-Z]+", (rec.get("decedent_name") or "").upper()))
        if not toks:
            continue
        for tkey, ttoks in trust_names.items():
            if ttoks and len(toks & ttoks) >= 2:
                print(f"  NOTE: new case {key} token-matches trust notice {tkey} -- review for a manual merge.")


def advance_checkpoint(last_run: dict, walk: dict, start_case: str) -> None:
    if walk.get("highest_hit"):
        year, num = case_parts(walk["highest_hit"])
        candidate = make_case_no(year, num + 1)
    else:
        candidate = start_case  # no hits: do not advance past unproven numbers
    prev = last_run.get("portal_next_case", "")
    if prev and case_parts(prev) > case_parts(candidate):
        print(f"  Checkpoint held at {prev} (refusing to move backwards to {candidate}).")
        return
    if candidate != prev:
        print(f"  Checkpoint: portal_next_case {prev or '(none)'} -> {candidate}")
    last_run["portal_next_case"] = candidate


# ── doctor / main ──────────────────────────────────────────────────────────

DC_CSV_COLUMNS = [
    "Estate #", "Deceased First Name", "Deceased Last Name", "Date of Death",
    "Property Address", "Property City", "Property State", "Property Zipcode",
    "Ownership Verdict", "ITSPE Owner", "Owned Elsewhere",
    "County", "Case Type", "Filed On", "Case Status",
    "PR First Name", "PR Last Name", "PR Address", "PR City", "PR State", "PR Zip",
    "Attorney", "Attorney Address", "Heirs", "Source", "Notice Published On",
]


def write_dc_csv(ledger: dict, out_path: Path, only_keys: set[str]) -> int:
    """DC-specific review CSV: the core lead fields PLUS the ownership verdict,
    the ITSPE owner, and any property the decedent owns elsewhere -- so the
    review sheet shows owns/family/rental/review at a glance."""
    import csv as csv_module
    rows = []
    for key in sorted(only_keys):
        rec = ledger.get("estates", {}).get(key)
        if not rec:
            continue
        prs = rec.get("personal_reps") or []
        pr1 = prs[0] if prs else {}
        pr_first, pr_last = _split_pr_name(pr1.get("name", "")) if pr1.get("name") else ("", "")
        att = rec.get("attorney") or {}
        heirs = "; ".join(f"{h['name']} ({h.get('street', '')})".strip()
                          for h in rec.get("heirs") or [])
        owned_elsewhere = rec.get("property_owned_elsewhere", "")
        if owned_elsewhere and rec.get("property_owned_elsewhere_zip"):
            owned_elsewhere += f" {rec['property_owned_elsewhere_zip']}"
        rows.append({
            "Estate #": key.split("|", 1)[1],
            "Deceased First Name": rec.get("decedent_first_name", ""),
            "Deceased Last Name": rec.get("decedent_last_name", ""),
            "Date of Death": rec.get("dod", ""),
            "Property Address": rec.get("property_residence") or rec.get("property_street", ""),
            "Property City": rec.get("property_city", ""),
            "Property State": rec.get("property_state", ""),
            "Property Zipcode": rec.get("property_zip", ""),
            "Ownership Verdict": rec.get("property_ownership") or rec.get("property_lookup_reason", ""),
            "ITSPE Owner": rec.get("property_itspe_owner", ""),
            "Owned Elsewhere": owned_elsewhere,
            "County": COUNTY,
            "Case Type": rec.get("case_type", ""),
            "Filed On": rec.get("filing_date", ""),
            "Case Status": rec.get("status", ""),
            "PR First Name": pr_first,
            "PR Last Name": pr_last,
            "PR Address": pr1.get("street", ""),
            "PR City": pr1.get("city", ""),
            "PR State": pr1.get("state", ""),
            "PR Zip": pr1.get("zip", ""),
            "Attorney": att.get("name", ""),
            "Attorney Address": ", ".join(filter(None, [att.get("street"), att.get("city"),
                                                         att.get("state"), att.get("zip")])),
            "Heirs": heirs,
            "Source": "+".join(rec.get("sources", [])),
            "Notice Published On": rec.get("legal_notice_published_on", ""),
        })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pending = out_path.with_name(f"_PENDING_{out_path.name}")
    with open(pending, "w", newline="", encoding="utf-8-sig") as f:
        w = csv_module.DictWriter(f, fieldnames=DC_CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    try:
        pending.replace(out_path)
    except PermissionError:
        print(f"  WARNING: {out_path} is locked (open in Excel?) -- wrote {pending} instead.")
    return len(rows)


def run_doctor() -> int:
    ok = True
    print("== DWLR ==")
    try:
        text = dwlr_page_text(fetch_dwlr())
        probate_chunks, trust_chunks = segment_dwlr(text)
        print(f"  page fetched, {len(probate_chunks)} probate + {len(trust_chunks)} trust notices segmented.")
        if not probate_chunks:
            print("  FAIL: zero probate notices -- format drift or block.")
            ok = False
        else:
            rec = parse_probate_notice(probate_chunks[0]) or {}
            print(f"  first probate parse: case={rec.get('case_no')} decedent={rec.get('decedent_name')} "
                  f"dod={rec.get('dod')} PRs={len(rec.get('personal_reps') or [])} "
                  f"first_pub={rec.get('legal_notice_published_on')}")
            if not rec.get("case_no"):
                ok = False
        if trust_chunks:
            trec = parse_trust_notice(trust_chunks[0]) or {}
            print(f"  first trust parse: settlor={trec.get('decedent_name')} dod={trec.get('dod')} "
                  f"trustee={((trec.get('personal_reps') or [{}])[0]).get('name')}")
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL: {e}")
        ok = False

    print("== ITSPE ==")
    try:
        rows = itspe_query("UPPER(PREMISEADD) LIKE '3650 APPLETON ST NW%'", limit=2)
        if rows:
            print(f"  probe OK: {rows[0].get('OWNERNAME')} @ {rows[0].get('PREMISEADD')} (SSL {rows[0].get('SSL')})")
        else:
            print("  FAIL: known-good probe address returned nothing.")
            ok = False
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL: {e}")
        ok = False

    print("== Portal ==")
    try:
        resp = requests.get(PORTAL_BASE + "/", headers={"User-Agent": UA}, timeout=30)
        waf = any(mark in resp.text for mark in WAF_MARKERS)
        print(f"  GET /Portal/ -> HTTP {resp.status_code}, WAF marker {'PRESENT' if waf else 'absent'} "
              "(a browser session may still be challenged).")
    except Exception as e:  # noqa: BLE001
        print(f"  WARN: portal probe failed: {e}")
    env = dotenv_values(str(ENV_PATH))
    key = env.get("CAPTCHA_API_KEY", "")
    print(f"  CAPTCHA_API_KEY: {'set' if key and 'your' not in key.lower() else 'MISSING/placeholder'}")
    print(f"  storage_state: {'present' if PORTAL_STATE_PATH.exists() else 'absent (first portal run needs --headed)'}")

    print("== Checkpoint ==")
    last_run = load_json(LAST_RUN_PATH, {})
    ledger = load_json(LEDGER_PATH, {"estates": {}})
    print(f"  portal_next_case: {last_run.get('portal_next_case', f'(none; seed {SEED_START_CASE} + 1)')}")
    print(f"  dwlr_last_first_pub: {last_run.get('dwlr_last_first_pub', '(none)')}")
    print(f"  ledger records: {len(ledger.get('estates', {}))}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["dwlr", "portal", "both"], default="both")
    ap.add_argument("--start-case", default="", help="override portal walk start (e.g. 2026-ADM-000839)")
    ap.add_argument("--max-cases", type=int, default=25, help="cap on portal case fetches this run")
    ap.add_argument("--miss-tolerance", type=int, default=5, help="consecutive misses that end the walk")
    ap.add_argument("--since", default="", help="MM/DD/YYYY -- skip DWLR notices first-published earlier")
    ap.add_argument("--out", default="", help="CSV path (default: output/DC Probates <date>.csv)")
    ap.add_argument("--commit", action="store_true", help="persist ledger + checkpoint (local only, never DataSift)")
    ap.add_argument("--doctor", action="store_true", help="live checks, no pull")
    ap.add_argument("--no-addresses", action="store_true", help="skip the ITSPE step")
    ap.add_argument("--no-case-detail", action="store_true",
                    help="portal: skip the per-case Register of Actions fetch (results-row fields only)")
    ap.add_argument("--headed", action="store_true", help="portal: run headed (human can clear the WAF once)")
    ap.add_argument("--dump-json", default="", help="write this run's touched records to a JSON file")
    args = ap.parse_args()

    if args.doctor:
        return run_doctor()

    run_date = datetime.now().strftime("%m/%d/%Y")
    ledger = load_json(LEDGER_PATH, {"estates": {}})
    last_run = load_json(LAST_RUN_PATH, {})
    env = dotenv_values(str(ENV_PATH))
    captcha_key = env.get("CAPTCHA_API_KEY", "")
    if captcha_key and "your" in captcha_key.lower():
        captcha_key = ""

    dwlr_recs: dict[str, dict] = {}
    portal_recs: dict[str, dict] = {}
    walk: dict = {}
    portal_start = ""

    if args.source in ("dwlr", "both"):
        dwlr_recs = run_dwlr(args.since)
        print(f"DWLR: {len(dwlr_recs)} record(s) after --since filter.")

    if args.source in ("portal", "both"):
        if args.start_case:
            start = normalize_case_no(args.start_case)
            if not start:
                print(f"ERROR: --start-case {args.start_case!r} is not a recognizable ADM case number.")
                return 2
        elif last_run.get("portal_next_case"):
            start = last_run["portal_next_case"]
        else:
            year, num = case_parts(SEED_START_CASE)
            start = make_case_no(year, num + 1)
        portal_start = start
        print(f"Portal: walking from {start} (max {args.max_cases}, miss tolerance {args.miss_tolerance}, "
              f"{'headed' if args.headed else 'headless'})...")
        try:
            portal_recs, walk = portal_walk(start, args.max_cases, args.miss_tolerance,
                                            args.headed, captcha_key, args.no_case_detail)
            secs = walk.get("seconds_per_case") or [0]
            print(f"Portal: {walk['hits']} hit(s), {walk['misses']} miss(es), "
                  f"~{sum(secs) / len(secs):.0f}s/case; stopped: {walk['stopped_reason']}")
        except Exception as e:  # noqa: BLE001
            # In `both` mode the DWLR half is already gathered and must still
            # be written; a portal WAF/selector failure is non-fatal there and
            # the checkpoint simply does not advance. In `portal`-only mode the
            # failure IS the result -- re-raise so the run reports non-zero.
            print(f"Portal: FAILED -- {e}")
            if args.source == "portal":
                raise
            walk = {}

    if not dwlr_recs and not portal_recs:
        print("Nothing pulled from either source.")
        return 0 if args.source == "portal" and walk else 1

    merged = merge_records(dwlr_recs, portal_recs)
    changes = reconcile_ledger(ledger, merged, run_date)
    touched = set(changes["added"]) | set(changes["changed"]) | set(changes["unchanged"])
    print(f"Ledger: {len(changes['added'])} added, {len(changes['changed'])} changed, "
          f"{len(changes['unchanged'])} unchanged.")
    warn_trust_matches(ledger, changes["added"])

    if not args.no_addresses:
        save_cb = (lambda: save_json(LEDGER_PATH, ledger)) if args.commit else None
        fill_addresses_itspe(ledger, touched, save_cb=save_cb)

    out_path = Path(args.out) if args.out else OUTPUT_DIR / f"DC Probates {datetime.now():%m.%d.%Y}.csv"
    n = write_dc_csv(ledger, out_path, touched)
    print(f"CSV: {n} row(s) -> {out_path}")

    if args.dump_json:
        dump = {k: ledger["estates"][k] for k in sorted(touched) if k in ledger.get("estates", {})}
        save_json(Path(args.dump_json), dump)
        print(f"JSON dump: {len(dump)} record(s) -> {args.dump_json}")

    if args.commit:
        save_json(LEDGER_PATH, ledger)
        if walk:
            advance_checkpoint(last_run, walk, portal_start)
            last_run["portal_gap_log"] = walk.get("gap_log", [])
        newest = max((r.get("legal_notice_published_on", "") for r in dwlr_recs.values()),
                     key=_date_key, default="")
        if newest and _date_key(newest) > _date_key(last_run.get("dwlr_last_first_pub", "")):
            last_run["dwlr_last_first_pub"] = newest
        last_run["run_at"] = datetime.now().isoformat(timespec="seconds")
        save_json(LAST_RUN_PATH, last_run)
        print("Committed ledger + checkpoint.")
    else:
        print("Dry run: ledger and checkpoint NOT persisted (--commit to persist).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
