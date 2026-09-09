"""DC HOA/condo lien pull -- the SiftMap-inexpressible Priority 1 rungs.

Harvests the DC Recorder of Deeds lien index (washington.dc.publicsearch.us,
Kofile "Official Records Search") for the doors-per-deal rungs SiftMap cannot
filter (manifest gaps, `data/dpd_siftmap_manifest_11001.json`):

    DC P1-02 Absentee + HOA Lien   76.8x lift
    DC P1-06 HOA Lien              21.3x lift

REVIEW-ONLY: writes CSVs to output/, never touches DataSift. Import happens
later, on an explicit go, through the upload wizard.

What it does:
 1. Logs in (DC_ROD_EMAIL / DC_ROD_PASSWORD in .env; free account) and sweeps
    the URL-addressable results grid for lien doc types over --months:
        /results?department=RP&docTypes=CNDL,L&recordedDateRange=...&searchType=advancedSearch
    Codes proven live 2026-09-08 (tools/manual/probe_dc_rod.py): CONDO LIEN =
    CNDL, LIEN = L, CONDO LIEN RELEASE = RELC, LIEN RELEASE = RELL. The grid
    carries DOCUMENT NUMBER / GRANTOR / GRANTEE / DOC TYPE / RECORDED DATE /
    SQUARE / LOT / RELATED INSTRUMENT NUMBER, 50 rows per page.
 2. Sweeps the release types over the same window through today and drops any
    lien whose instrument is referenced by a release (docs/api/county-data.md:
    release filtering at INSTRUMENT level is not optional; square+lot+grantor
    is the fallback key when a release carries no related instrument).
 3. Splits HOA liens (grantor is an association) from other liens. Generic
    LIEN rows with a non-association grantor go to a separate *_other_liens
    CSV -- raw material for the Other Lien P1 rungs (P1-07..15), unprocessed.
 4. Joins each surviving HOA lien to the ITSPE tax roll by SQUARE + LOT
    (free, anonymous ArcGIS layer 53 -- same source dc_probate_pull.py uses)
    for premise address, owner name + mailing address, HSTDCODE (homestead),
    and MORTGAGECO (mortgage of record -- a free-and-clear proxy signal).
 5. Flags Absentee = mailing address differs from the premise AND no
    homestead code. The review CSV sorts the P1-02 cohort (HOA + absentee)
    first.

    python src/scripts/dc_hoa_lien_pull.py                  # 12 months, CNDL+L
    python src/scripts/dc_hoa_lien_pull.py --months 3       # smaller probe
    python src/scripts/dc_hoa_lien_pull.py --types CNDL     # condo liens only
    python src/scripts/dc_hoa_lien_pull.py --headed         # watch the browser

Outputs (timestamped, in output/):
    dc_hoa_liens_<ts>.csv          the reviewable cohort
    dc_hoa_liens_<ts>_other_liens.csv  non-HOA lien rows (Other Lien raw feed)
    dc_hoa_liens_<ts>_dropped.csv  released / unjoinable rows, with reasons
    dc_hoa_liens_<ts>_raw.json     every grid row as scraped (free re-runs)

$4 document images are never purchased; everything comes from the free index
and the free tax roll.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

BASE = "https://washington.dc.publicsearch.us"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# proven live 2026-09-08 by tools/manual/probe_dc_rod.py (URL after UI token add)
DOC_TYPE_CODES = {
    "CONDO LIEN": "CNDL",
    "LIEN": "L",
    "CONDO LIEN RELEASE": "RELC",
    "LIEN RELEASE": "RELL",
}
DEFAULT_LIEN_TYPES = "CNDL,L"
RELEASE_TYPES = "RELC,RELL"

# grantor names that mark an HOA/condo lien (vs mechanics/medical/government)
ASSOCIATION_RE = re.compile(
    r"CONDOMINIUM|CONDO\b|COUNCIL OF (UNIT |CO-?)?OWNERS|HOMEOWNERS?|"
    r"\bHOA\b|COMMUNITY ASS|CIVIC ASS|\bASSN\b|ASSOCIATION", re.I)

ITSPE_URL = ("https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/"
             "Property_and_Land_WebMercator/MapServer/53/query")
ITSPE_FIELDS = ("OWNERNAME,OWNNAME2,PREMISEADD,SSL,SQUARE,SUFFIX,LOT,UNITNUMBER,"
                "ADDRESS1,ADDRESS2,CITYSTZIP,HSTDCODE,MORTGAGECO,PROPTYPE,"
                "SALEDATE,SALEPRICE,ASSESSMENT")

PAGE_WAIT_MS = 2500
MAX_PAGES_GUARD = 200  # 10,000 rows; a lien sweep should never get near this


def say(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_creds() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    email = os.getenv("DC_ROD_EMAIL", "").strip()
    password = os.getenv("DC_ROD_PASSWORD", "").strip()
    if not email or not password:
        raise SystemExit("DC_ROD_EMAIL / DC_ROD_PASSWORD missing from .env")
    return email, password


# ── portal: login + grid sweep ──────────────────────────────────────────────

def login(page, email: str, password: str) -> None:
    page.goto(BASE, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(3000)
    for text in ("Sign In", "Log In", "Login"):
        loc = page.locator(f"text={text}").first
        if loc.count() and loc.is_visible():
            loc.click()
            break
    page.wait_for_timeout(2000)
    page.locator('input[type="email"], input[name*="mail" i]').first.fill(email)
    page.locator('input[type="password"]').first.fill(password)
    page.locator('button[type="submit"], button:has-text("Sign"), '
                 'button:has-text("Log")').first.click()
    page.wait_for_timeout(4000)
    body = page.inner_text("body")
    if "Sign Out" not in body:
        raise RuntimeError("login failed: no 'Sign Out' after submit")
    say("logged in")


COUNT_RE = re.compile(r"(\d[\d,]*)\s*-\s*(\d[\d,]*)\s+of\s+(\d[\d,]*)\s+results", re.I)

# grid header observed live 2026-09-08; parse maps by header name so a column
# reshuffle fails loudly instead of silently mislabeling
EXPECTED_COLS = ["DOCUMENT NUMBER", "BOOK TYPE/ROLL/FRAME", "GRANTOR", "GRANTEE",
                 "DOC TYPE", "RECORDED DATE", "SQUARE", "LOT",
                 "RELATED INSTRUMENT NUMBER"]


def read_grid(page) -> tuple[list[dict], int, int]:
    """Parse the visible results grid. Returns (rows, shown_through, total)."""
    body = page.inner_text("body")
    m = COUNT_RE.search(body)
    if not m:
        if re.search(r"No Results Found", body, re.I):
            return [], 0, 0
        raise RuntimeError("results count line not found -- page shape changed?")
    shown_through = int(m.group(2).replace(",", ""))
    total = int(m.group(3).replace(",", ""))

    raw = page.evaluate(
        """() => {
            const tables = [...document.querySelectorAll('table')];
            for (const t of tables) {
                const head = [...t.querySelectorAll('th')].map(el => (el.innerText || '').trim());
                if (!head.some(h => /DOCUMENT NUMBER/i.test(h))) continue;
                const rows = [...t.querySelectorAll('tbody tr')].map(tr =>
                    [...tr.querySelectorAll('td')].map(td => (td.innerText || '').trim()));
                return { head, rows };
            }
            return null;
        }""")
    if not raw:
        raise RuntimeError("results table not found")
    head = [h.upper() for h in raw["head"]]
    idx = {}
    for want in EXPECTED_COLS:
        for i, h in enumerate(head):
            if want in h:
                idx[want] = i
                break
    missing = [c for c in ("DOCUMENT NUMBER", "GRANTOR", "GRANTEE", "DOC TYPE",
                           "RECORDED DATE", "SQUARE", "LOT") if c not in idx]
    if missing:
        raise RuntimeError(f"grid header changed, missing columns: {missing} in {head}")

    rows = []
    for cells in raw["rows"]:
        if len(cells) < len(head) - 1:
            continue

        def cell(name: str) -> str:
            i = idx.get(name)
            return cells[i] if i is not None and i < len(cells) else ""

        rows.append({
            "doc_number": cell("DOCUMENT NUMBER"),
            "grantor": cell("GRANTOR"),
            "grantee": cell("GRANTEE"),
            "doc_type": cell("DOC TYPE"),
            "recorded_date": cell("RECORDED DATE"),
            "square": cell("SQUARE"),
            "lot": cell("LOT"),
            "related_instrument": cell("RELATED INSTRUMENT NUMBER"),
        })
    return rows, shown_through, total


def sweep(page, codes: str, date_from: str, date_to: str, label: str,
          _retry: bool = True) -> list[dict]:
    """Sweep one doc-type set over one recorded-date window, all pages.

    Zero rows is treated as a failure to be retried once, then raised: the
    12-month run of 2026-09-08 read a transient 'No Results Found' flash on
    the release sweep (the real count was 1,120) and silently kept every
    released lien. Callers wanting a legitimately-empty sweep pass a window
    they know is empty and catch the error."""
    url = (f"{BASE}/results?department=RP&docTypes={codes.replace(',', '%2C')}"
           f"&recordedDateRange={date_from}%2C{date_to}&searchType=advancedSearch")
    say(f"sweep {label}: {codes} {date_from}..{date_to}")
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(PAGE_WAIT_MS + 3000)

    all_rows: list[dict] = []
    pages = 0
    while True:
        rows, shown_through, total = read_grid(page)
        all_rows.extend(rows)
        pages += 1
        say(f"  page {pages}: +{len(rows)} rows ({len(all_rows)} of {total})")
        if shown_through >= total or not rows:
            break
        if pages >= MAX_PAGES_GUARD:
            raise RuntimeError(f"pagination guard hit at {pages} pages -- "
                               f"total {total} looks wrong for a lien sweep")
        nxt = page.locator('button[aria-label*="next" i]:not([disabled]), '
                           'a[aria-label*="next" i]').first
        if not nxt.count():
            raise RuntimeError(
                f"no next-page control but only {len(all_rows)} of {total} read")
        nxt.click()
        page.wait_for_timeout(PAGE_WAIT_MS)

    if len(all_rows) != total:
        say(f"  WARNING: read {len(all_rows)} rows, count line said {total}")
    if not all_rows:
        if _retry:
            say(f"  zero rows for {label} -- retrying once (transient "
                f"'No Results Found' flash seen live 2026-09-08)")
            page.wait_for_timeout(5000)
            return sweep(page, codes, date_from, date_to, label, _retry=False)
        raise RuntimeError(f"sweep {label} returned zero rows twice -- "
                           "zero results is a failure, not a quiet day")
    return all_rows


# ── release filtering (instrument level) ────────────────────────────────────

def apply_releases(liens: list[dict], releases: list[dict]) -> tuple[list[dict], list[dict]]:
    by_instrument = {r["related_instrument"] for r in releases
                     if r.get("related_instrument") and r["related_instrument"] != "N/A"}
    # fallback key for releases that carry no related instrument
    loose = {(r["square"], r["lot"], r["grantor"].upper()) for r in releases
             if not r.get("related_instrument") or r["related_instrument"] == "N/A"}
    kept, dropped = [], []
    for l in liens:
        if l["doc_number"] in by_instrument:
            dropped.append({**l, "drop_reason": "released (instrument match)"})
        elif (l["square"], l["lot"], l["grantor"].upper()) in loose:
            dropped.append({**l, "drop_reason": "released (square+lot+grantor match)"})
        else:
            kept.append(l)
    return kept, dropped


# ── ITSPE join ──────────────────────────────────────────────────────────────

def _sql_escape(s: str) -> str:
    return (s or "").replace("'", "''")


def itspe_query(where: str, limit: int = 10) -> list[dict]:
    resp = requests.get(ITSPE_URL, params={
        "where": where, "outFields": ITSPE_FIELDS, "f": "json",
        "returnGeometry": "false", "resultRecordCount": limit,
    }, headers={"User-Agent": UA}, timeout=45)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"ITSPE error: {data['error']}")
    return [f["attributes"] for f in data.get("features", [])]


def itspe_by_square_lot(square: str, lot: str) -> list[dict]:
    sq, lo = _sql_escape(square.strip()), _sql_escape(lot.strip())
    if not sq or not lo:
        return []
    rows = itspe_query(f"SQUARE = '{sq}' AND LOT = '{lo}'")
    if not rows:  # tolerate zero-padding drift between the two systems
        rows = itspe_query(
            f"SQUARE = '{sq.lstrip('0') or '0'}' AND LOT = '{lo.lstrip('0') or '0'}'")
    if not rows:  # tolerate a square suffix the grid drops ('2027' vs '2027E')
        rows = itspe_query(f"SQUARE LIKE '{sq}%' AND LOT = '{lo}'")
    if not rows:  # last resort: SSL prefix (SSL concatenates square+suffix+lot)
        rows = itspe_query(f"SSL LIKE '{sq}%{lo}'")
    return rows


_STREET_NUM_RE = re.compile(r"^\s*(\d+)\s+(\S+)")


def _addr_key(addr: str) -> tuple[str, str] | None:
    m = _STREET_NUM_RE.match((addr or "").upper())
    return (m.group(1), m.group(2)) if m else None


def _name_tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^A-Z]+", (name or "").upper()) if len(t) > 1}


def join_itspe(lien: dict) -> dict:
    """Attach tax-roll facts to one lien row; never raises (records status)."""
    out = dict(lien)
    try:
        parcels = itspe_by_square_lot(lien["square"], lien["lot"])
    except Exception as e:  # noqa: BLE001 - transient roll errors stay visible
        out["join_status"] = f"itspe_error: {type(e).__name__}: {e}"
        return out
    if not parcels:
        out["join_status"] = "no_parcel"
        return out
    if len(parcels) > 1:
        # square+lot should be unique; prefer the one whose owner matches the
        # lien's grantee (the debtor)
        gt = _name_tokens(lien["grantee"])
        parcels.sort(key=lambda p: -len(gt & _name_tokens(p.get("OWNERNAME") or "")))
    p = parcels[0]
    premise = " ".join((p.get("PREMISEADD") or "").split())
    mailing1 = " ".join((p.get("ADDRESS1") or "").split())
    citystzip = " ".join((p.get("CITYSTZIP") or "").split())
    hstd = str(p.get("HSTDCODE") or "").strip()
    pk, mk = _addr_key(premise), _addr_key(mailing1)
    mailing_differs = bool(pk and mk and pk != mk) or bool(pk and not mk and mailing1)
    homestead = hstd not in ("", "0", "None")
    owner_match = len(_name_tokens(lien["grantee"]) &
                      _name_tokens(p.get("OWNERNAME") or "")) >= 1

    out.update({
        "join_status": "ok" if len(parcels) == 1 else f"ok_multi({len(parcels)})",
        "ssl": p.get("SSL") or "",
        "premise_address": premise,
        "unit": str(p.get("UNITNUMBER") or "").strip(),
        "owner_name": p.get("OWNERNAME") or "",
        "owner_name2": p.get("OWNNAME2") or "",
        "mailing_address": mailing1,
        "mailing_address2": p.get("ADDRESS2") or "",
        "mailing_citystzip": citystzip,
        "hstd_code": hstd,
        "homestead": homestead,
        "mailing_differs": mailing_differs,
        "absentee": mailing_differs and not homestead,
        "mortgage_co": p.get("MORTGAGECO") or "",
        "no_mortgage_of_record": not (p.get("MORTGAGECO") or "").strip(),
        "proptype": p.get("PROPTYPE") or "",
        "assessment": p.get("ASSESSMENT"),
        "sale_price": p.get("SALEPRICE"),
        "owner_matches_debtor": owner_match,
    })
    return out


# ── output ──────────────────────────────────────────────────────────────────

MAIN_COLS = ["absentee", "doc_type", "recorded_date", "association", "grantee",
             "premise_address", "unit", "owner_name", "owner_name2",
             "mailing_address", "mailing_address2", "mailing_citystzip",
             "homestead", "hstd_code", "mailing_differs", "owner_matches_debtor",
             "mortgage_co", "no_mortgage_of_record", "proptype", "assessment",
             "sale_price", "square", "lot", "ssl", "doc_number", "join_status"]


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    say(f"wrote {path} ({len(rows)} rows)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--months", type=int, default=12, help="lien filing window")
    ap.add_argument("--types", default=DEFAULT_LIEN_TYPES,
                    help=f"lien doc-type codes (default {DEFAULT_LIEN_TYPES}; "
                         f"known: {DOC_TYPE_CODES})")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--raw", help="reprocess a prior *_raw.json instead of scraping")
    a = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_base = ROOT / "output" / f"dc_hoa_liens_{ts}"
    date_to = datetime.now().strftime("%Y%m%d")
    date_from = (datetime.now() - timedelta(days=30 * a.months)).strftime("%Y%m%d")

    if a.raw:
        raw = json.loads(Path(a.raw).read_text(encoding="utf-8"))
        liens, releases = raw["liens"], raw["releases"]
        say(f"reprocessing {a.raw}: {len(liens)} liens, {len(releases)} releases")
    else:
        from playwright.sync_api import sync_playwright
        email, password = load_creds()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not a.headed)
            page = browser.new_context(viewport={"width": 1500, "height": 950},
                                       user_agent=UA).new_page()
            login(page, email, password)
            liens = sweep(page, a.types, date_from, date_to, "liens")
            # releases can postdate the window start by anything up to today
            releases = sweep(page, RELEASE_TYPES, date_from, date_to, "releases")
            browser.close()
        raw_path = Path(f"{out_base}_raw.json")
        raw_path.write_text(json.dumps(
            {"at": ts, "window": [date_from, date_to], "types": a.types,
             "liens": liens, "releases": releases}, indent=1), encoding="utf-8")
        say(f"wrote {raw_path}")

    kept, released = apply_releases(liens, releases)
    say(f"release filter: {len(liens)} liens -> {len(kept)} kept, "
        f"{len(released)} released ({len(released) * 100 // max(len(liens), 1)}%)")

    hoa = [l for l in kept if ASSOCIATION_RE.search(l["grantor"])]
    other = [l for l in kept if not ASSOCIATION_RE.search(l["grantor"])]
    say(f"grantor split: {len(hoa)} HOA/condo liens, {len(other)} other liens")

    joined, dropped = [], list(released)
    for i, l in enumerate(hoa, 1):
        r = join_itspe({**l, "association": l["grantor"]})
        if r.get("join_status", "").startswith("ok"):
            joined.append(r)
        else:
            dropped.append({**r, "drop_reason": r.get("join_status", "join failed")})
        if i % 25 == 0:
            say(f"  itspe join {i}/{len(hoa)}")
        time.sleep(0.4)

    joined.sort(key=lambda r: (not r.get("absentee"), r.get("recorded_date", "")))
    n_abs = sum(1 for r in joined if r.get("absentee"))
    write_csv(Path(f"{out_base}.csv"), joined, MAIN_COLS)
    write_csv(Path(f"{out_base}_other_liens.csv"), other,
              ["doc_type", "recorded_date", "grantor", "grantee", "square",
               "lot", "doc_number", "related_instrument"])
    write_csv(Path(f"{out_base}_dropped.csv"), dropped,
              ["drop_reason"] + [c for c in MAIN_COLS if c != "join_status"])

    say(f"DONE: {len(joined)} HOA-liened properties "
        f"({n_abs} absentee -- the P1-02 cohort), "
        f"{len(other)} other-lien rows parked, {len(dropped)} dropped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
