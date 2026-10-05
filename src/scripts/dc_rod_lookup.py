"""DC Recorder of Deeds (washington.dc.publicsearch.us) -- FREE party-index lookup.

Lifted from the working pattern in output/dp_1391_pennsylvania_20261001/rod_url.py.

What this does
  * signs in with DC_ROD_EMAIL / DC_ROD_PASSWORD (from .env, never printed)
  * drives the ADVANCED SEARCH FORM (/search/advanced: party name, recorded-date window, Search)
    -- TRAP (found 2026-10-02): navigating straight to a /results?parties=...&recordedDateRange=...
    URL answers "No results found" for EVERY term, even the exact URL the form lands on; the four
    rod_url.py searches on 1391 Pennsylvania were false negatives.  Only the form returns rows.
  * reads the result table (`table tbody tr`), 50 rows per page, and follows the `next page`
    button once (100 rows max) -- the INDEX, which is free.  TRAP: the rows are NOT date-ordered
    (the "newest to oldest" text is a sort-menu option, not the applied order), so a truncated
    read loses ARBITRARY rows; a term that overflows must be narrowed (30-year cap, then the
    middle initial from the tax roll: --mi-pass), never trusted as "the latest 100".
  * saves every result page's HTML for audit
  * flags instrument types per skills/deep-prospecting-v5/references/deed-analysis.md

What this NEVER does
  * open a document / image, add to cart, purchase -- a document image is $4 (+$1.50/txn)
    and only happens on Basem's explicit go.  On a result page the module clicks ONLY the
    `button[aria-label="next page"]` pager; never a row, a document, an image or the cart.
  * call DataSift, Firecrawl, or anything metered.

Result columns (verified on the saved 1391 Pennsylvania HTML, 2026-10-01):
  3 control cells (checkbox / image / cart) then
  Document Number | Book Type/Roll/Frame | Grantor | Grantee | Doc Type | Recorded Date |
  Square | Lot | Related Instrument Number
There is NO consideration column in the index -> `consideration` is always None here.
DC index Doc Type vocabulary seen so far: DEED, DEED RELEASE, TRUST (= DEED OF TRUST, a MORTGAGE),
TRUST ASSIGNMENT, CERTIFICATE OF SATISFACTION, RELEASE, APPOINTMENT SUBSTITUTE TRUSTEE,
TRUSTEES DEED (foreclosure), FORECLOSURE NOTICE, TRANSFER ON DEATH DEED.

CLI
  python src/scripts/dc_rod_lookup.py --run-dir output/<run> --keys-file keys.txt [--force] [--probe]
  Reads <run>/records.json (dict keyed by key), writes ONLY
  <run>/rerun_v5/deed.json, <run>/rerun_v5/deed.log, <run>/rerun_v5/rod_html/*.html.
  Idempotent: keys already in deed.json are skipped unless --force.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

BASE = "https://washington.dc.publicsearch.us"
SIGNIN_URL = BASE + "/signin"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "Chrome/128 Safari/537.36")
SETTLE_MS = 9000          # same settle rod_url.py / rod_party.py used after Search
FORM_SETTLE_MS = 3000
BETWEEN_SEARCHES_S = 1.5
DEFAULT_D0, DEFAULT_D1 = "19900101", "20261002"
CAP_YEARS = 30
PAGE_SIZE = 50            # the site's fixed page size ("Results Per Page:: 50")
MAX_PAGES = 2             # read at most 100 rows per search
PAGE_LIMIT = PAGE_SIZE * MAX_PAGES

# Surnames common enough that a 1990-2026 party search returns hundreds of rows.
COMMON_SURNAMES = {
    # US top-100 surnames: a 1990-2026 party search on these overflows the 100-row page even
    # with a first name, so the window is capped to the last 30 years up front.  Any other
    # name that overflows is re-run on the capped window dynamically (one extra search).
    "SMITH", "JOHNSON", "WILLIAMS", "BROWN", "JONES", "GARCIA", "MILLER", "DAVIS",
    "RODRIGUEZ", "MARTINEZ", "HERNANDEZ", "LOPEZ", "GONZALEZ", "WILSON", "ANDERSON",
    "THOMAS", "TAYLOR", "MOORE", "JACKSON", "MARTIN", "LEE", "PEREZ", "THOMPSON", "WHITE",
    "HARRIS", "SANCHEZ", "CLARK", "RAMIREZ", "LEWIS", "ROBINSON", "WALKER", "YOUNG",
    "ALLEN", "KING", "WRIGHT", "SCOTT", "TORRES", "NGUYEN", "HILL", "FLORES", "GREEN",
    "ADAMS", "NELSON", "BAKER", "HALL", "RIVERA", "CAMPBELL", "MITCHELL", "CARTER",
    "ROBERTS", "GOMEZ", "PHILLIPS", "EVANS", "TURNER", "DIAZ", "PARKER", "CRUZ", "EDWARDS",
    "COLLINS", "REYES", "STEWART", "MORRIS", "MORALES", "MURPHY", "COOK", "ROGERS",
    "GUTIERREZ", "ORTIZ", "MORGAN", "COOPER", "PETERSON", "BAILEY", "REED", "KELLY",
    "HOWARD", "RAMOS", "KIM", "COX", "WARD", "RICHARDSON", "WATSON", "BROOKS", "CHAVEZ",
    "WOOD", "JAMES", "BENNETT", "GRAY", "MENDOZA", "RUIZ", "HUGHES", "PRICE", "ALVAREZ",
    "CASTILLO", "SANDERS", "PATEL", "MYERS", "LONG", "ROSS", "FOSTER", "JIMENEZ", "POWELL",
    "WASHINGTON", "JENKINS", "PERRY", "RUSSELL", "SULLIVAN", "BELL", "COLEMAN", "BUTLER",
}

# ----------------------------------------------------------------------------- instrument flags
# Patterns on the index's Doc Type string -> flag label.  Per deed-analysis.md plus the DC-specific
# instruments the party index exposes (TRANSFER ON DEATH, TRUSTEES DEED = foreclosure under a
# deed of trust).  A "DEED OF TRUST" in DC is a MORTGAGE, not a trust transfer, so it is labelled
# `mortgage`, never `trust`.
_FLAG_RULES = [
    (r"QUIT\s*CLAIM|QUITCLAIM", "quitclaim"),
    (r"EXECUT|ADMINISTRAT|PERSONAL\s+REP|\bPR\s+DEED|ESTATE\s+DEED|FIDUCIARY", "PR deed"),
    (r"DISTRIBUTION", "deed of distribution"),
    (r"LIFE\s+ESTATE|REMAINDER", "life estate"),
    (r"TRANSFER\s+ON\s+DEATH|\bTOD\b|REVOCATION\s+OF\s+TRANSFER", "transfer on death deed"),
    # DC index: "TRUST" / "TRUST ASSIGNMENT" / "TRUST MODIFICATION" are DEED OF TRUST paper (a MORTGAGE)
    (r"DEED\s+OF\s+TRUST|^DOT\b|^TRUST$|^TRUST\s+(ASSIGNMENT|MODIFICATION|SUBORDINATION|AMENDMENT)|MODIFICATION|SUBORDINATION|ASSIGNMENT|REFINANC", "mortgage"),
    (r"TRUSTEE'?S?\s+DEED|TRUSTEES?\s+DEED", "trustee's deed (foreclosure / trust)"),
    (r"TO\s+TRUST|FROM\s+TRUST|TRUST\s+DEED|CERTIFICATE\s+OF\s+TRUST|TRUST\s+AGREEMENT|REVOCABLE|LIVING\s+TRUST", "trust"),
    (r"IN\s+LIEU", "deed in lieu"),
    (r"SHERIFF|TAX\s+DEED|TAX\s+SALE|MARSHAL", "sheriff's / tax deed"),
    (r"SPECIAL\s+WARRANTY", "special warranty"),
    (r"CORRECT|CONFIRMATORY|RE-?RECORD", "correction deed"),
    (r"CONTRACT|INSTALLMENT|LAND\s+CONTRACT", "contract for deed"),
    (r"NOTICE\s+OF\s+FORECLOSURE|FORECLOSURE", "foreclosure notice"),
    (r"\bLIEN\b|WATER|SEWER|JUDGMENT|LIS\s+PENDENS", "lien"),
    (r"GIFT", "gift deed"),
    (r"APPOINTMENT.*TRUSTEE|SUBSTITUT.*TRUSTEE", "substitute trustee"),
    (r"POWER\s+OF\s+ATTORNEY|\bPOA\b", "power of attorney"),
    (r"AFFIDAVIT", "affidavit"),
    (r"RELEASE|SATISFACTION|RECONVEY", "release"),
    (r"CONDO|BYLAWS|DECLARATION|PLAT", "condo / declaration"),
]
_FLAG_RE = [(re.compile(p, re.I), f) for p, f in _FLAG_RULES]


CONVEYANCE_FLAGS = {"deed", "quitclaim", "PR deed", "trustee's deed (foreclosure / trust)",
                    "deed of distribution", "gift deed", "transfer on death deed", "deed in lieu",
                    "sheriff's / tax deed", "special warranty", "life estate", "trust"}


def flag_instrument(doc_type: str) -> set[str]:
    """Flags for one index Doc Type.  Plain 'DEED' -> {'deed'}; unknown -> set()."""
    dt = (doc_type or "").strip().upper()
    if not dt:
        return set()
    if "REVOCATION" in dt:
        return {"transfer on death deed REVOCATION"} if "DEATH" in dt else {"revocation"}
    flags = {f for rx, f in _FLAG_RE if rx.search(dt)}
    if not flags and re.fullmatch(r"(COOPERATIVE\s+|CORRECTIVE\s+|CONFIRMATORY\s+)?DEED(\s+\w+)?", dt) and "RELEASE" not in dt:
        flags.add("deed")
    return flags


def flag_parties(grantor: str, grantee: str, doc_type: str = "DEED") -> set[str]:
    """Name-based flags the Doc Type does not carry (a trust or estate as a party).
    Applied to CONVEYANCES only -- on a DC "TRUST" (mortgage) the grantee is the lender's
    trustee and means nothing for vesting."""
    out = set()
    dt = (doc_type or "").upper()
    if not re.search(r"DEED", dt) or re.search(r"RELEASE|TRUST", dt):
        return out
    both = f"{grantor} || {grantee}".upper()
    both_wo = re.sub(r"TRUST\s+(COMPANY|CO\b|BANK)", "", both)
    if re.search(r"\bTRUST\b|\bTRUSTEES?\b|\bTR\b|\bTTEE", both_wo):
        out.add("trust")
    if re.search(r"\bESTATE\s+OF\b|\bEST\s+OF\b|\bPERSONAL\s+REP|\bEXECUT|\bADMINISTRAT", both):
        out.add("estate as party")
    if re.search(r"\bLLC\b|\bINC\b|\bCORP|\bL\.?P\.?\b|\bCOMPANY\b|\bBANK\b|\bMORTGAGE\b|\bASSOCIATES\b|\bINVESTMENT", grantee.upper()):
        out.add("entity grantee")
    return out


# ----------------------------------------------------------------------------- name helpers
def rod_name(last: str, first: str = "") -> str:
    """'Whitmon-Clark', 'Mary' -> 'WHITMON CLARK MARY' (surname first, upper, no punctuation)."""
    s = f"{last} {first}".upper()
    s = re.sub(r"[.,'’\"]", "", s)
    s = re.sub(r"[-/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def rod_name_variants(last: str, first: str = "") -> list[str]:
    """The de-punctuated form first; for hyphenated / spaced surnames also the joined form."""
    main = rod_name(last, first)
    out = [main]
    if re.search(r"[-\s]", (last or "").strip()):
        joined = re.sub(r"[^A-Z]", "", (last or "").upper())
        alt = (joined + " " + re.sub(r"[^A-Z ]", "", (first or "").upper())).strip()
        if alt and alt != main:
            out.append(alt)
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper())).strip()


def party_matches(term: str, party: str) -> bool:
    """Does an index party string belong to the searched name?  Prefix match on the
    normalised strings ('COBB ESSIE' matches 'COBB ESSIE M', not 'COBB ESSIEX')."""
    t, p = _norm(term), _norm(party)
    if not t or not p:
        return False
    return p == t or p.startswith(t + " ")


def is_common(term: str) -> bool:
    return (_norm(term).split() or [""])[0] in COMMON_SURNAMES


def capped_window(d1: str = DEFAULT_D1) -> tuple[str, str]:
    y = int(d1[:4]) - CAP_YEARS
    return f"{y}{d1[4:]}", d1


# ----------------------------------------------------------------------------- URL + browser
def party_url(term: str, types, d0: str, d1: str) -> str:
    """The /results URL the advanced form lands on for this search (kept for the audit record).
    NOTE: navigating to it directly returns "No results found" -- use party_search()."""
    parties = json.dumps({"parties": [{"term": term, "types": list(types)}]})
    return (f"{BASE}/results?department=RP&parties=" + urllib.parse.quote(parties)
            + f"&recordedDateRange={d0}%2C{d1}&searchType=advancedSearch")


def _creds() -> tuple[str, str]:
    try:
        from dotenv import load_dotenv
        load_dotenv(".env")
    except Exception:
        pass
    email, pw = os.environ.get("DC_ROD_EMAIL"), os.environ.get("DC_ROD_PASSWORD")
    if not email or not pw:
        raise SystemExit("DC_ROD_EMAIL / DC_ROD_PASSWORD missing from .env")
    return email, pw


def login(page) -> bool:
    """Sign in.  Returns True when the page left /signin.  Never logs the credentials."""
    email, pw = _creds()
    page.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    page.fill("#email", email)
    page.fill("#password", pw)
    page.click("button[type='submit']")
    page.wait_for_timeout(5000)
    ok = "/signin" not in page.url
    if not ok:
        body = page.inner_text("body")[:300].replace("\n", " ")
        print(f"[login] still on /signin: {body}")
    return ok


def _needs_login(page, body_text: str) -> bool:
    if "/signin" in page.url:
        return True
    low = body_text[:600].lower()
    return ("forbidden" in low and "sign in" in low) or "perhaps you need to sign in" in low


_HEAD_RE = re.compile(r"(\d[\d,]*-\d[\d,]* of [\d,]+ results[^\n]*|No results found[^\n]*|Sorry about this![^\n]*)")
_COUNT_RE = re.compile(r"of ([\d,]+) results")

_ROWS_JS = ("Array.from(document.querySelectorAll('table tbody tr'))"
            ".map(tr=>Array.from(tr.querySelectorAll('td')).map(td=>td.innerText.trim()))")


def _mdy(yyyymmdd: str) -> str:
    return f"{yyyymmdd[4:6]}/{yyyymmdd[6:8]}/{yyyymmdd[:4]}"


def _read_rows(page) -> list[list[str]]:
    rows = []
    for cells in page.evaluate(_ROWS_JS):
        if len(cells) >= 9:
            rows.append(cells[-9:])          # drop the leading control cells
        elif cells:
            rows.append(cells)
    return rows


def _submit_form(page, term: str, types, d0: str, d1: str) -> None:
    """Fill /search/advanced and press Search.  types: both -> Party Names box;
    only grantor / only grantee -> the matching box."""
    page.goto(BASE + "/search/advanced", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(FORM_SETTLE_MS)
    tset = set(types)
    box = "#partyNames-input" if tset >= {"grantor", "grantee"} else ("#grantor-input" if "grantor" in tset else "#grantee-input")
    page.click(box)
    page.keyboard.type(term)
    page.keyboard.press("Enter")
    page.wait_for_timeout(600)
    for sel, val in (("#recordedDateRange-start", _mdy(d0)), ("#recordedDateRange-end", _mdy(d1))):
        page.click(sel)
        page.keyboard.press("Control+A")
        page.keyboard.type(val)
        page.keyboard.press("Tab")
        page.wait_for_timeout(300)
    page.locator("button[type='submit']:has-text('Search')").first.click()
    page.wait_for_timeout(SETTLE_MS)


def party_search(page, term: str, types, d0: str, d1: str, html_path: str | None = None,
                 relogin: bool = True, max_pages: int = MAX_PAGES) -> dict:
    """One party search through the advanced form.  Returns
    {"head", "rows", "count", "url", "html_file", "html_files", "pages_read", "error"}.
    rows = raw cell lists (control cells stripped), site order (NOT by date), up to max_pages * 50.
    The only click on a result page is the `next page` pager button."""
    _submit_form(page, term, types, d0, d1)
    txt = page.inner_text("body")
    if relogin and _needs_login(page, txt):
        print("[session] bounced to sign-in; logging in again")
        if not login(page):
            raise RuntimeError("re-login failed")
        _submit_form(page, term, types, d0, d1)
        txt = page.inner_text("body")
    m = _HEAD_RE.search(txt)
    head = m.group(0) if m else txt[:150].replace("\n", " ")
    rows = _read_rows(page)
    count = None
    cm = _COUNT_RE.search(head)
    if cm:
        count = int(cm.group(1).replace(",", ""))
    elif head.startswith("No results"):
        count = 0
    html_files = []
    if html_path:
        Path(html_path).parent.mkdir(parents=True, exist_ok=True)
        Path(html_path).write_text(page.content(), encoding="utf-8")
        html_files.append(html_path)
    pages_read = 1
    while count and len(rows) < count and pages_read < max_pages:
        nxt = page.locator("button[aria-label='next page']:not([disabled])")
        if nxt.count() == 0:
            break
        nxt.first.click()
        page.wait_for_timeout(SETTLE_MS)
        more = _read_rows(page)
        if not more:
            break
        rows += more
        pages_read += 1
        if html_path:
            p2 = re.sub(r"\.html$", f"__p{pages_read}.html", html_path)
            Path(p2).write_text(page.content(), encoding="utf-8")
            html_files.append(p2)
    return {"head": head, "rows": rows, "count": count, "url": page.url, "html_file": html_path,
            "html_files": html_files, "pages_read": pages_read,
            "error": None if (count is not None) else head}


def _iso(mdy: str) -> str | None:
    try:
        return datetime.strptime(mdy.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except Exception:
        return None


def parse_rows(rows) -> list[dict]:
    """Index row cells -> instrument dicts.  Column order per module docstring."""
    out = []
    for c in rows:
        if len(c) < 9:
            c = list(c) + [""] * (9 - len(c))
        doc_no, book, grantor, grantee, doc_type, rec, square, lot, related = c[:9]
        legal = None
        if square or lot:
            legal = f"SQ {square} LOT {lot}".strip()
        out.append({
            "recorded_date": _iso(rec) or rec,
            "doc_type": doc_type,
            "doc_number": doc_no,
            "grantor": grantor,
            "grantee": grantee,
            "legal": legal,
            "book": book,
            "related_instrument": None if related in ("", "N/A") else related,
            "consideration": None,          # not in the index; only on the $4 image
        })
    return out


# ----------------------------------------------------------------------------- per-record driver
def search_terms(rec: dict) -> list[str]:
    terms: list[str] = []
    biz = (rec.get("business_name") or "").strip()
    if biz:
        terms.append(rod_name(biz))
    of, ol = rec.get("owner_first") or "", rec.get("owner_last") or ""
    if ol:
        terms += rod_name_variants(ol, of)
    df, dl = rec.get("decedent_first") or "", rec.get("decedent_last") or ""
    if dl and (df.strip().lower(), dl.strip().lower()) != (of.strip().lower(), ol.strip().lower()):
        terms += rod_name_variants(dl, df)
    anc = rec.get("anchors") or {}
    full = re.sub(r"\b(JR|SR|II|III|IV)\b\.?", " ", (anc.get("decedent_full") or "").upper())
    full = re.sub(r"\s+", " ", full).strip()
    if full and len(full.split()) >= 2:
        parts = full.split()
        terms += rod_name_variants(parts[-1], parts[0])
    seen, uniq = set(), []
    for t in terms:
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


SURNAME_PARTICLES = {"VAN", "VON", "DE", "DEL", "DELA", "DI", "DA", "LA", "LE", "MC", "MAC", "ST", "O", "EL", "AL", "BIN"}


def title_terms(owner: str) -> list[str]:
    """Tax-roll owner string -> ROD search terms.
    'CLARK, MARY L' -> ['CLARK MARY'];  'ESSIE M COBB TRUSTEE' -> ['COBB ESSIE'];
    'LAWRENCE, M V' (initials only) -> ['LAWRENCE M V'];
    'POLYXENI LIEDERMAN MOLOKOTOS TRUSTEE' -> ['MOLOKOTOS POLYXENI', 'LIEDERMAN POLYXENI'].
    Middle initials are dropped; TRUSTEE / TR / TRS / TTEE / ET AL are stripped."""
    o = (owner or "").upper()
    o = re.sub(r"\b(TRUSTEES?|TRS?|TTEE|ET\s*AL|ETAL|ET\s*UX|ET\s*VIR|JR|SR|II|III)\b\.?", " ", o)
    o = re.sub(r"[.'’\"]", "", o)
    o = re.sub(r"\s+", " ", o).strip(" ,")
    if not o:
        return []
    out: list[str] = []
    if "," in o:
        last, _, rest = o.partition(",")
        firsts = rest.split()
        if not firsts:
            return rod_name_variants(last.strip())
        if len(firsts[0]) == 1:                      # initials only: keep them all
            first = " ".join(t for t in firsts if len(t) == 1)
        else:
            first = firsts[0]
        out += rod_name_variants(last.strip(), first)
    else:
        toks = o.split()
        if len(toks) == 1:
            return rod_name_variants(toks[0])
        first = toks[0] if len(toks[0]) > 1 else " ".join(t for t in toks[:-1] if len(t) == 1)
        if len(toks) >= 3 and toks[-2] in SURNAME_PARTICLES:   # VAN FLEET, DE LA CRUZ, MC ...
            out += rod_name_variants(toks[-2] + " " + toks[-1], first)
        else:
            out += rod_name_variants(toks[-1], first)
            if len(toks) >= 3 and len(toks[-2]) > 1:  # possible double surname
                out += rod_name_variants(toks[-2], first)
    seen, uniq = set(), []
    for t in out:
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def title_terms_mi(owner: str) -> list[str]:
    """Like title_terms but KEEPS the middle initial(s): 'SMITH, HELEN H' -> ['SMITH HELEN H'];
    'ESSIE M COBB TRUSTEE' -> ['COBB ESSIE M'].  Only owners that carry one yield a term."""
    o = (owner or "").upper()
    o = re.sub(r"\b(TRUSTEES?|TRS?|TTEE|ET\s*AL|ETAL|ET\s*UX|ET\s*VIR|JR|SR|II|III)\b\.?", " ", o)
    o = re.sub(r"[.'’\"]", "", o)
    o = re.sub(r"\s+", " ", o).strip(" ,")
    if not o:
        return []
    if "," in o:
        last, _, rest = o.partition(",")
        firsts = rest.split()
    else:
        toks = o.split()
        if len(toks) < 3:
            return []
        if toks[-2] in SURNAME_PARTICLES:
            last, firsts = toks[-2] + " " + toks[-1], toks[:-2]
        else:
            last, firsts = toks[-1], toks[:-1]
    if len(firsts) < 2 or len(firsts[0]) == 1:
        return []
    return rod_name_variants(last.strip(), " ".join(firsts[:2]))


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:60]


def search_term(page, key: str, term: str, html_dir: Path, d0=DEFAULT_D0, d1=DEFAULT_D1,
                source_term: str = "record", verbose=False, log=print) -> tuple[list, list, set]:
    """Run ONE term (grantor + grantee), with the common-name / overflow cap.
    Returns (searches, instruments, flags) for that term."""
    types = ["grantor", "grantee"]
    searches, instruments, flags = [], [], set()
    capped = is_common(term)
    s0, s1 = capped_window(d1) if capped else (d0, d1)
    res = None
    for attempt in range(2):
        html = html_dir / f"{_safe(key)}__{_safe(term)}__{s0}-{s1}.html"
        res = party_search(page, term, types, s0, s1, str(html))
        log(f"  [{key}] '{term}' {s0}-{s1}{' CAPPED' if capped else ''} ({source_term}): {res['head']}  rows={len(res['rows'])}")
        searches.append({"term": term, "types": types, "d0": s0, "d1": s1, "count": res["count"],
                         "capped": capped, "rows_read": len(res["rows"]), "pages_read": res.get("pages_read"),
                         "html_file": str(html).replace("\\", "/"), "head": res["head"],
                         "url": res["url"], "source_term": source_term})
        if res["error"]:
            flags.add("search error")
        if (not capped and res["count"] is not None and res["count"] > PAGE_LIMIT and attempt == 0):
            log(f"  [{key}] {res['count']} rows > {PAGE_LIMIT}: re-running on the last {CAP_YEARS} years")
            capped = True
            s0, s1 = capped_window(d1)
            time.sleep(BETWEEN_SEARCHES_S)
            continue
        break
    if capped:
        flags.add("common name - capped")
    if res["count"] and res["count"] > len(res["rows"]):
        flags.add(f"index truncated - read {len(res['rows'])} of {res['count']} (unordered)")
    for inst in parse_rows(res["rows"]):
        hit_as = [side for side in ("grantor", "grantee") if party_matches(term, inst[side])]
        if not hit_as:
            continue
        inst["matched_term"] = term
        inst["matched_as"] = hit_as
        inst["source_term"] = source_term
        f = flag_instrument(inst["doc_type"]) | flag_parties(inst["grantor"], inst["grantee"], inst["doc_type"])
        inst["flags"] = sorted(f)
        inst["is_conveyance"] = bool(f & CONVEYANCE_FLAGS)
        instruments.append(inst)
        if verbose:
            print("    ", inst)
    time.sleep(BETWEEN_SEARCHES_S)
    return searches, instruments, flags


def ssl_to_legal(ssl: str) -> str | None:
    """Tax-roll SSL '0520    0008' -> 'SQ 0520 LOT 0008' (the index's Square / Lot form)."""
    if not ssl:
        return None
    parts = str(ssl).split()
    if len(parts) < 2:
        return None
    return f"SQ {parts[0].zfill(4)} LOT {parts[1].zfill(4)}"


def parcel_key(legal: str | None) -> tuple[str, str] | None:
    """Comparable (square, lot).  The tax roll writes the square suffix AFTER the number
    ('2318W', '4304E'), the index BEFORE it ('W2318') or not at all ('2318') -- so the square
    is compared on its DIGITS only.  'N/A' -> None."""
    if not legal:
        return None
    m = re.match(r"SQ\s+(\S+)\s+LOT\s+(\S+)", legal)
    if not m:
        return None
    sq, lot = m.group(1), m.group(2)
    if sq.upper() == "N/A" or lot.upper() == "N/A":
        return None
    sqd = re.sub(r"\D", "", sq).lstrip("0") or "0"
    lotd = re.sub(r"\D", "", lot).lstrip("0") or "0"
    return (sqd, lotd)


def finalize(key: str, searches: list, instruments: list, extra_flags: set, ssl: str | None = None) -> dict:
    """Dedupe by doc number, order by date, derive last_instrument / last_conveyance / flags."""
    seen, uniq = set(), []
    for inst in instruments:
        k = inst["doc_number"] or (inst["recorded_date"], inst["doc_type"], inst["grantor"], inst["grantee"])
        if k in seen:
            continue
        seen.add(k)
        if "is_conveyance" not in inst:
            inst["is_conveyance"] = bool(set(inst.get("flags", [])) & CONVEYANCE_FLAGS)
        uniq.append(inst)
    uniq.sort(key=lambda i: i["recorded_date"] or "")
    legal = ssl_to_legal(ssl)
    pk = parcel_key(legal)
    parcel_match_mode = None
    for inst in uniq:
        ik = parcel_key(inst.get("legal"))
        inst["on_parcel"] = (ik == pk) if (pk and ik) else None
    # when the tax roll gives the parcel, judge on the parcel's own paper (namesakes drop out)
    if pk:
        scope = [i for i in uniq if i["on_parcel"]]
        if scope:
            parcel_match_mode = "square+lot"
        else:
            # condo / re-subdivided lots: the index often carries the old record lot -> same SQUARE
            scope = [i for i in uniq if parcel_key(i.get("legal")) and parcel_key(i["legal"])[0] == pk[0]]
            if scope:
                parcel_match_mode = "square only"
                for i in scope:
                    i["on_parcel"] = "square"
    else:
        scope = uniq
    flags = set(extra_flags)
    for inst in scope:
        flags |= set(inst["flags"])
    last = scope[-1] if scope else None
    if not uniq:
        flags.add("no index hit")
    elif pk and not scope:
        flags.add("no index hit ON PARCEL (hits are other parcels / namesakes)")
    elif parcel_match_mode == "square only":
        flags.add("parcel matched on SQUARE only (lot differs - condo / re-subdivision?)")
    if last and (last["recorded_date"] or "") >= "2026-01-01":
        flags.add("instrument recorded 2026 - possible post-death transfer")
    conveyances = [i for i in scope if i["is_conveyance"]]
    lastc = conveyances[-1] if conveyances else None
    if lastc and "grantor" in lastc["matched_as"] and "grantee" not in lastc["matched_as"]:
        flags.add("owner is GRANTOR on last conveyance - may no longer hold title")
    return {
        "key": key,
        "searches": searches,
        "instruments": uniq,
        "parcel_legal": legal,
        "parcel_match_mode": parcel_match_mode,
        "n_instruments": len(uniq),
        "n_on_parcel": len(scope) if pk else None,
        "last_instrument": last,
        "last_conveyance": lastc,
        "vesting": "unread - image $4",
        "flags": sorted(flags),
        "source": "dc_rod_index",
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def lookup_record(page, key: str, rec: dict, html_dir: Path, d0=DEFAULT_D0, d1=DEFAULT_D1,
                  verbose=False, log=print) -> dict:
    searches, instruments, flags = [], [], set()
    for term in search_terms(rec):
        s_, i_, f_ = search_term(page, key, term, html_dir, d0, d1, "record", verbose, log)
        searches += s_
        instruments += i_
        flags |= f_
    return finalize(key, searches, instruments, flags)


def extend_with_title(page, key: str, entry: dict, owners: list, html_dir: Path, d0=DEFAULT_D0,
                      d1=DEFAULT_D1, verbose=False, log=print, mode: str = "title") -> tuple[dict, list]:
    """Title pass: search every tax-roll owner term not already searched for this key and
    merge into the existing deed.json entry.  Returns (new_entry, new_terms)."""
    done = {_norm(s["term"]) for s in entry.get("searches", [])}
    new_terms = []
    deriver = title_terms_mi if mode == "title+mi" else title_terms
    for o in owners or []:
        for t in deriver(o):
            if _norm(t) not in done:
                done.add(_norm(t))
                new_terms.append(t)
    if not new_terms:
        return entry, []
    searches = list(entry.get("searches", []))
    for s_ in searches:
        s_.setdefault("source_term", "record")
    instruments = list(entry.get("instruments", []))
    for i_ in instruments:
        i_.setdefault("source_term", "record")
    flags = {f for f in entry.get("flags", []) if f in ("common name - capped", "search error")
             or f.startswith("index truncated")}
    for term in new_terms:
        s2, i2, f2 = search_term(page, key, term, html_dir, d0, d1, mode, verbose, log)
        searches += s2
        instruments += i2
        flags |= f2
    out = finalize(key, searches, instruments, flags)
    out["title_terms_searched"] = sorted({s["term"] for s in searches if str(s.get("source_term", "")).startswith("title")})
    return out, new_terms


def refinalize_entry(entry: dict, ssl: str | None) -> dict:
    """Recompute flags / is_conveyance / on_parcel / last_* from the STORED instruments (no network)."""
    keep = {f.replace("(newest first)", "(unordered)") for f in entry.get("flags", [])
            if f in ("common name - capped", "search error") or f.startswith("index truncated")}
    insts = []
    for i in entry.get("instruments", []):
        i = dict(i)
        i.setdefault("source_term", "record")
        f = flag_instrument(i["doc_type"]) | flag_parties(i["grantor"], i["grantee"], i["doc_type"])
        i["flags"] = sorted(f)
        i["is_conveyance"] = bool(f & CONVEYANCE_FLAGS)
        insts.append(i)
    out = finalize(entry["key"], entry.get("searches", []), insts, keep, ssl)
    for k in ("title_terms_searched", "error"):
        if k in entry:
            out[k] = entry[k]
    out["checked_at"] = entry.get("checked_at", out["checked_at"])
    return out


# ----------------------------------------------------------------------------- self-check
def _selfcheck_1391() -> None:
    """Parser check against the saved 1391 Pennsylvania UI results (no network)."""
    p = Path("output/dp_1391_pennsylvania_20261001/rod_results.json")
    if not p.exists():
        print("selfcheck: no saved 1391 results; skipped")
        return
    d = json.load(open(p, encoding="utf-8"))
    rows = d["helen"]["rows"]
    inst = parse_rows(rows)
    hit = [i for i in inst if i["recorded_date"] == "2019-10-23" and i["doc_type"] == "DEED"
           and i["grantor"] == "SMITH HELEN H" and i["grantee"] == "NURNBERGER MARC"
           and i["doc_number"] == "2019114520" and i["legal"] == "SQ 1038 LOT 0068"]
    assert hit, "parser did not reproduce the 2019-10-23 Smith -> Nurnberger deed"
    assert party_matches("SMITH HELEN", hit[0]["grantor"])
    assert "deed" in flag_instrument(hit[0]["doc_type"])
    assert flag_instrument("TRUSTEES DEED") == {"trustee's deed (foreclosure / trust)"}
    assert flag_instrument("COOPERATIVE DEED") == {"deed"}
    assert flag_instrument("TRANSFER ON DEATH DEED REVOCATION") == {"transfer on death deed REVOCATION"}
    assert ssl_to_legal("0520    0008") == "SQ 0520 LOT 0008" and ssl_to_legal("1038 68") == "SQ 1038 LOT 0068"
    assert parcel_key(ssl_to_legal("2318W   0803")) == parcel_key("SQ W2318 LOT 0803") == parcel_key("SQ 2318 LOT 0803")
    assert parcel_key("SQ N/A LOT N/A") is None and parcel_key("SQ 5649N LOT 0005") == ("5649", "5")
    fin = finalize("t", [], [dict(h, flags=["deed"], is_conveyance=True, matched_as=["grantor"])
                            for h in [hit[0]]], set(), "1038 68")
    assert fin["n_on_parcel"] == 1 and fin["last_conveyance"]["doc_number"] == "2019114520"
    assert "owner is GRANTOR on last conveyance - may no longer hold title" in fin["flags"]
    assert "mortgage" in flag_instrument("DEED OF TRUST")
    assert flag_instrument("TRUST") == {"mortgage"} and flag_instrument("TRUST ASSIGNMENT") == {"mortgage"}
    assert flag_instrument("CERTIFICATE OF SATISFACTION") == {"release"}
    assert flag_parties("TENEYCK BRENDA A", "WALTON JOAN", "TRUST") == set()
    assert flag_parties("DOE JOHN", "DOE FAMILY TRUST", "DEED") == {"trust"}
    assert flag_parties("DOE JOHN", "BANKERS TRUST COMPANY", "DEED") == {"entity grantee"}
    assert "estate as party" in flag_parties("ESTATE OF FERDINAND T SCHNEIDER", "SMITH DAVID B JR", "DEED")
    assert "quitclaim" in flag_instrument("QUIT CLAIM DEED")
    assert "PR deed" in flag_instrument("PERSONAL REPRESENTATIVE DEED")
    assert "transfer on death deed" in flag_instrument("TRANSFER ON DEATH DEED")
    assert title_terms("CLARK, MARY L") == ["CLARK MARY"]
    assert title_terms("ESSIE M COBB TRUSTEE") == ["COBB ESSIE"]
    assert title_terms("LAWRENCE, M V") == ["LAWRENCE M V"]
    assert title_terms("SUSAN B VENER LINSKY TRUSTEE") == ["LINSKY SUSAN", "VENER SUSAN"]
    assert title_terms("MURRAY-SHORT, ANNIE R") == ["MURRAY SHORT ANNIE", "MURRAYSHORT ANNIE"]
    assert title_terms("QUEEN, MARIA-LANA C") == ["QUEEN MARIA LANA"]
    assert title_terms("HOLLY H VAN FLEET TRUSTEE") == ["VAN FLEET HOLLY", "VANFLEET HOLLY"]
    assert title_terms_mi("SMITH, HELEN H") == ["SMITH HELEN H"] and title_terms_mi("ESSIE M COBB TRUSTEE") == ["COBB ESSIE M"]
    assert title_terms_mi("LEWIS, LESLIE") == [] and title_terms_mi("LAWRENCE, M V") == []
    assert search_terms({"owner_first": "Donald", "owner_last": "Morgan", "decedent_first": "Donald",
                         "decedent_last": "Morgan", "anchors": {"decedent_full": "Donald G Morgan Sr"}}) == ["MORGAN DONALD"]
    assert rod_name("Whitmon-Clark", "Mary") == "WHITMON CLARK MARY"
    assert rod_name_variants("Whitmon-Clark", "Mary") == ["WHITMON CLARK MARY", "WHITMONCLARK MARY"]
    print(f"selfcheck OK: {len(inst)} rows parsed from the saved 1391 results; 2019-10-23 Smith sale reproduced")


# ----------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=False, help="run dir holding records.json; outputs go to <run-dir>/rerun_v5/")
    ap.add_argument("--keys-file", help="one record key per line")
    ap.add_argument("--key", action="append", default=[], help="single key (repeatable)")
    ap.add_argument("--out-dir", help="override output dir (default <run-dir>/rerun_v5)")
    ap.add_argument("--force", action="store_true", help="redo keys already in deed.json")
    ap.add_argument("--probe", action="store_true", help="print head + parsed rows for every search")
    ap.add_argument("--selfcheck", action="store_true", help="offline parser check against the saved 1391 results")
    ap.add_argument("--refinalize", action="store_true",
                    help="offline: recompute flags / is_conveyance / on_parcel (SSL from --title-json) for every "
                         "key already in deed.json; no browser")
    ap.add_argument("--mi-pass", action="store_true",
                    help="with --title-json: only keys that have a TRUNCATED search; search the tax-roll owner "
                         "WITH the middle initial (narrower term, no overflow)")
    ap.add_argument("--title-json", help="title pass: search each tax-roll owner in this file (key -> {owners: [...]}) "
                                         "not already searched, merge into the existing deed.json entries")
    ap.add_argument("--d0", default=DEFAULT_D0)
    ap.add_argument("--d1", default=DEFAULT_D1)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args(argv)

    if a.selfcheck:
        _selfcheck_1391()
        return 0
    if not a.run_dir:
        ap.error("--run-dir is required")

    run_dir = Path(a.run_dir)
    out_dir = Path(a.out_dir) if a.out_dir else run_dir / "rerun_v5"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_dir = out_dir / "rod_html"
    deed_path = out_dir / "deed.json"
    log_path = out_dir / "deed.log"

    records = json.load(open(run_dir / "records.json", encoding="utf-8"))
    keys = list(a.key)
    if a.keys_file:
        keys += [ln.strip() for ln in open(a.keys_file, encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    if not keys:
        ap.error("no keys (--keys-file or --key)")
    missing = [k for k in keys if k not in records]
    if missing:
        print(f"WARNING: {len(missing)} keys not in records.json: {missing[:5]}")
        keys = [k for k in keys if k in records]

    deed = json.load(open(deed_path, encoding="utf-8")) if deed_path.exists() else {}
    if a.refinalize:
        title = json.load(open(a.title_json, encoding="utf-8")) if a.title_json else {}
        n = 0
        for k in keys:
            if k in deed:
                deed[k] = refinalize_entry(deed[k], (title.get(k) or {}).get("ssl"))
                n += 1
        json.dump(deed, open(deed_path, "w", encoding="utf-8"), indent=1)
        print(f"refinalized {n} keys -> {deed_path}")
        return 0
    title = None
    if a.title_json:
        title = json.load(open(a.title_json, encoding="utf-8"))
        todo = []
        for k in keys:
            if k not in deed:
                print(f"  {k}: not in deed.json yet -- run the record pass first; skipped")
                continue
            if a.mi_pass and not any(f.startswith("index truncated") for f in deed[k].get("flags", [])):
                continue
            done = {_norm(s_["term"]) for s_ in deed[k].get("searches", [])}
            deriver = title_terms_mi if a.mi_pass else title_terms
            want = [t for o in (title.get(k) or {}).get("owners", []) for t in deriver(o) if _norm(t) not in done]
            if want or a.force:
                todo.append(k)
        print(f"{'middle-initial' if a.mi_pass else 'title'} pass: {len(keys)} keys, {len(todo)} with unsearched terms")
    else:
        todo = [k for k in keys if a.force or k not in deed]
        print(f"{len(keys)} keys, {len(todo)} to do ({len(keys) - len(todo)} already in {deed_path})")
    if not todo:
        return 0

    logf = open(log_path, "a", encoding="utf-8")

    def log(msg: str):
        print(msg)
        logf.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
        logf.flush()

    from playwright.sync_api import sync_playwright
    t0 = time.time()
    done = 0
    with sync_playwright() as p:
        b = p.chromium.launch(headless=not a.headed)
        ctx = b.new_context(user_agent=USER_AGENT, viewport={"width": 1500, "height": 1100})
        page = ctx.new_page()
        if not login(page):
            log("[login] first attempt failed; retrying once")
            time.sleep(5)
            if not login(page):
                log("[login] FAILED twice -- stopping")
                b.close()
                return 2
        log(f"[login] OK ({page.url})")
        try:
            for i, key in enumerate(todo, 1):
                rec = records[key]
                log(f"[{i}/{len(todo)}] {key}  {rec.get('street')} {rec.get('zip')}  terms={search_terms(rec)}")
                try:
                    if title is not None:
                        owners = (title.get(key) or {}).get("owners", [])
                        res, new_terms = extend_with_title(page, key, deed[key], owners, html_dir, a.d0, a.d1,
                                                           verbose=a.probe, log=log,
                                                           mode="title+mi" if a.mi_pass else "title")
                        log(f"  [{key}] title owners={owners} new terms={new_terms}")
                    else:
                        res = lookup_record(page, key, rec, html_dir, a.d0, a.d1, verbose=a.probe, log=log)
                except Exception as e:  # keep going; record the failure
                    log(f"  [{key}] ERROR {type(e).__name__}: {str(e)[:200]}")
                    if title is not None:
                        res = dict(deed[key])
                        res["flags"] = sorted(set(res.get("flags", [])) | {"search error"})
                        res["error"] = f"title pass {type(e).__name__}: {str(e)[:200]}"
                    else:
                        res = {"key": key, "searches": [], "instruments": [], "last_instrument": None,
                               "last_conveyance": None, "vesting": "unread - image $4", "flags": ["search error"],
                               "source": "dc_rod_index", "error": f"{type(e).__name__}: {str(e)[:200]}",
                               "checked_at": datetime.now().isoformat(timespec="seconds")}
                    if "/signin" in page.url or "closed" in str(e).lower():
                        if not login(page):
                            log("[login] re-login failed after error -- stopping")
                            deed[key] = res
                            break
                deed[key] = res
                done += 1
                li = res["last_instrument"]
                log(f"  -> {len(res['instruments'])} instruments; last={li['recorded_date'] + ' ' + li['doc_type'] if li else None}; flags={res['flags']}")
                json.dump(deed, open(deed_path, "w", encoding="utf-8"), indent=1)
        finally:
            b.close()
    json.dump(deed, open(deed_path, "w", encoding="utf-8"), indent=1)
    log(f"done {done}/{len(todo)} keys in {time.time() - t0:.0f}s -> {deed_path}")
    logf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
