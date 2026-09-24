"""Building-permit portal evaluation pull -- the three FREE footprint sources.

READ-ONLY and REVIEW-ONLY: public open-data endpoints in, CSVs + evaluation.md
out under output/permits/<date>/. Nothing touches DataSift, no vendor is
billed, no List is created. Built 2026-09-24 from a live portal survey; the
survey itself (every jurisdiction, every URL, what each carries) is written
into the evaluation.md this script produces.

Sources pulled (machine-readable, anonymous):
  dc          Open Data DC, ArcGIS FeatureServer FEEDS/DCRA: layer 18 =
              Building Permits 2026, 17 = 2025 (window rows + never-issued),
              13 = Basic Business License Points, category "Contractor and
              Construction Services" (the DC contractor roster). Layer 4
              (last 30 days) is a subset of 18 and is NOT pulled.
  montgomery  dataMontgomery Socrata: residential xfxj-qszi, commercial
              i26v-w6bd, demolition b6ht-fw3x (+ electrical/mechanical with
              --moco-trades). m88u-pqki duplicates residential and is skipped.
  fairfax     Fairfax GIS hosted layer Building_Records_PLUS/FeatureServer/0
              (nightly from PLUS).

What each source CAN and CANNOT say (measured 2026-09-24):
  * Only DC carries names: OWNER_NAME (good) and PERMIT_APPLICANT, which is
    the APPLICANT -- dominated by permit EXPEDITERS ("NA NA; BRYCE CARRINGTON"
    792 permits, "DFM PERMITS") and homeowners, not the licensed contractor.
    The applicant table therefore carries expediter_flag, self_owner_share and
    a join to the BBL roster; the vendor seed draws ONLY from the roster join.
  * Montgomery and Fairfax carry NO applicant, contractor or owner. "Multiple
    permits" there can only be counted by address / ZIP. The contractor name
    lives on Montgomery DPS eSearch and the Fairfax PLUS record page (phase 2,
    browser).
  * DC's open layer has no application date, so DC "stalled" rows come from
    status only (expired / withdrawn); Montgomery (addeddate) and Fairfax
    (SUBMITTED_DATE) support the applied-never-issued rule.

Traps encoded here:
  * Socrata + Cloudflare: ANY function call in $where (upper(, starts_with(,
    count( ...) returns a 403 "Just a moment" page (memory
    project_socrata_cloudflare_upper_403). socrata_where() forbids function
    syntax outright and http_get() names the trap when it sees the page.
  * ArcGIS caps a page at maxRecordCount (2000); pagination is
    resultOffset + orderByFields=<oid> ASC, stop on a short page, and the
    fetched total is compared with returnCountOnly (1% drift tolerance for
    the nightly refresh between doctor and pull).
  * ArcGIS dates are epoch MILLISECONDS; never strptime a raw value.
  * Layers 17 and 18 overlap on PERMIT_ID; the 18 row wins.
  * The layer-17 filter must keep ISSUE_DATE IS NULL or every never-issued
    2025 application vanishes.
  * Government / institution owners (DISTRICT OF COLUMBIA 344, DC HOUSING
    AUTHORITY 205, USA 109) are removed by WORD-BOUNDARY rules, never
    substrings (the WILLBANKS/BANK lesson in dispo_buyers.local_tier).
  * aca-prod.accela.com/FREDERICK is the Town of Frederick, COLORADO.

    python src/scripts/permit_portal_pull.py --doctor
    python src/scripts/permit_portal_pull.py --jurisdictions dc --page-limit 1 --out output/permits/smoke
    python src/scripts/permit_portal_pull.py                          # dc,montgomery,fairfax; 12 months
    python src/scripts/permit_portal_pull.py --resume output/permits/20260924
    python src/scripts/permit_portal_pull.py --analyze-only --out output/permits/20260924

Outputs (output/permits/<YYYYMMDD>/):
    raw/state.json, raw/doctor.json, raw/<source>.jsonl   the cache (resume file)
    permits_all.csv              every permit in the common schema
    dc_repeat_applicants.csv     applicants with >= --min-permits (DC only)
    repeat_owners.csv            owners with >= --min-permits construction permits (DC only)
    stalled_permits.csv          stalled / expired / withdrawn permits by address
    zip_heat.csv                 permits by jurisdiction + ZIP + bucket
    dc_contractor_licenses.csv   the BBL contractor roster joined to applicant counts
    vendor_directory_seed.csv    build_directory.py FIELDS order, roster entries with >= 2 permits
    evaluation.md                the answer to "what can the portals fill in"

Offline guard: python tools/manual/test_permit_portal_pull.py
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from enterprise_prospects import (  # noqa: E402
    ENTITY_RX, EXCLUDE_GROUPS, classify_one, norm_name,
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

DC_BASE = "https://maps2.dcgis.dc.gov/dcgis/rest/services/FEEDS/DCRA/FeatureServer"
DC_LAYERS = {"dc_permits_2026": 18, "dc_permits_2025": 17, "dc_bbl": 13}
BBL_CATEGORY = "Contractor and Construction Services"
FAIRFAX_URL = ("https://services1.arcgis.com/ioennV6PpG5Xodq0/arcgis/rest/services/"
               "Building_Records_PLUS/FeatureServer/0")
SOCRATA_BASE = "https://data.montgomerycountymd.gov/resource/{id}.json"
MOCO_DATASETS = {"moco_residential": "xfxj-qszi", "moco_commercial": "i26v-w6bd",
                 "moco_demolition": "b6ht-fw3x"}
MOCO_TRADES = {"moco_electrical": "qxie-8qnp", "moco_mechanical": "ih88-a6aa"}
MOCO_ALT_RESIDENTIAL = "m88u-pqki"      # duplicate view of xfxj-qszi; documented, not pulled

JURIS = {"dc": ("District of Columbia", "11001"),
         "montgomery": ("Montgomery", "24031"),
         "fairfax": ("Fairfax", "51059")}
SOURCE_JURIS = {"dc_permits_2026": "dc", "dc_permits_2025": "dc",
                "moco_residential": "montgomery", "moco_commercial": "montgomery",
                "moco_demolition": "montgomery", "moco_electrical": "montgomery",
                "moco_mechanical": "montgomery", "fairfax": "fairfax"}

REGISTRY_DEFAULT = ROOT / "output" / "dispo_buyers" / "registry.json"
ZIP_CACHE = ROOT / "data" / "zip_county_cache.json"
SOCRATA_PAGE = 5000
ARCGIS_PAGE_CAP = 2000

PERMIT_COLS = ["jurisdiction", "fips", "source", "permit_id", "permit_type",
               "permit_subtype", "bucket", "category", "status", "applied_date",
               "issued_date", "finaled_date", "address", "city", "zip", "county",
               "parcel_id", "description", "applicant_raw", "applicant_name",
               "applicant_org", "applicant_alt", "applicant_key",
               "applicant_is_entity", "owner_raw", "owner_name", "owner_key",
               "owner_is_entity", "owner_excluded", "valuation", "fees", "lat",
               "lon", "link_url", "last_modified"]

# The vendor-directory-builder column contract (skills/vendor-directory-builder/
# scripts/build_directory.py FIELDS). Copied so this script never imports openpyxl;
# the offline test asserts the two lists are identical.
VENDOR_FIELDS = ["trade", "company", "contact", "phone", "phone_status", "email",
                 "website", "service_area", "serves_geo", "rating", "license", "bbb",
                 "why", "source", "cautions", "confidence", "top_pick"]


class SocrataBlocked(RuntimeError):
    """Cloudflare WAF answered instead of Socrata -- a function call in $where."""


def say(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ── window + dates ──────────────────────────────────────────────────────────

def window(months: int, today: datetime | None = None) -> tuple[str, str]:
    today = today or datetime.now()
    start = today - timedelta(days=round(365 * months / 12))
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def epoch_ms_to_iso(v) -> str:
    if v in (None, ""):
        return ""
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    s = str(v).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    if re.match(r"^\d{11,14}$", s):
        return epoch_ms_to_iso(int(s))
    return ""


def soc_ts_to_iso(v) -> str:
    s = str(v or "").strip()
    return s[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else ""


def iso_to_dt(s: str) -> datetime | None:
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


# ── HTTP ────────────────────────────────────────────────────────────────────

CF_MARKERS = ("Just a moment", "cf-browser-verification", "cf-chl", "challenge-platform")


def http_get(session, url: str, params: dict, tries: int = 3, timeout: int = 90):
    delay = 5
    last = None
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, headers={"User-Agent": UA}, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            if attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        if r.status_code == 403 and any(m in r.text[:5000] for m in CF_MARKERS):
            raise SocrataBlocked(
                "Cloudflare WAF answered the Socrata call; a function in $where "
                "(upper(, starts_with(, count( ...) is the known trigger")
        if r.status_code >= 500 and attempt < tries - 1:
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} on {url}: {r.text[:300]}")
        return r
    raise RuntimeError(f"gave up on {url}: {last}")


def _arcgis_json(session, url: str, params: dict) -> dict:
    data = http_get(session, url, {**params, "f": "json"}).json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS error on {url}: {data['error']}")
    return data


def arcgis_meta(session, layer_url: str) -> dict:
    d = _arcgis_json(session, layer_url, {})
    adv = d.get("advancedQueryCapabilities") or {}
    return {"name": d.get("name"),
            "fields": [f["name"] for f in d.get("fields", [])],
            "maxRecordCount": int(d.get("maxRecordCount") or ARCGIS_PAGE_CAP),
            "supportsPagination": bool(adv.get("supportsPagination")),
            "supportsStatistics": bool(adv.get("supportsStatistics")),
            "oid": d.get("objectIdField") or "OBJECTID"}


def arcgis_count(session, layer_url: str, where: str) -> int:
    d = _arcgis_json(session, f"{layer_url}/query", {"where": where, "returnCountOnly": "true"})
    return int(d.get("count", 0))


def arcgis_groupby(session, layer_url: str, where: str, field: str, oid: str = "OBJECTID",
                   top: int = 60) -> list[dict]:
    d = _arcgis_json(session, f"{layer_url}/query", {
        "where": where, "groupByFieldsForStatistics": field,
        "outStatistics": json.dumps([{"statisticType": "count", "onStatisticField": oid,
                                      "outStatisticFieldName": "n"}]),
        "orderByFields": "n DESC", "resultRecordCount": top, "returnGeometry": "false"})
    out = []
    for f in d.get("features", []):
        attrs = f["attributes"]
        # ArcGIS Server (DC) echoes the stat under its own casing; hosted layers use "n".
        n = attrs.get("n")
        if n is None:
            n = next((v for k, v in attrs.items() if k.upper() != field.upper()), None)
        out.append({"value": attrs.get(field), "n": n})
    return out


def arcgis_page(session, layer_url: str, where: str, offset: int, n: int,
                oid: str = "OBJECTID") -> tuple[list[dict], bool]:
    d = _arcgis_json(session, f"{layer_url}/query", {
        "where": where, "outFields": "*", "returnGeometry": "false",
        "orderByFields": f"{oid} ASC", "resultOffset": offset, "resultRecordCount": n})
    return ([f["attributes"] for f in d.get("features", [])],
            bool(d.get("exceededTransferLimit")))


def socrata_where(start_iso: str, end_iso: str, added: str = "addeddate",
                  issued: str = "issueddate") -> str:
    a, b = f"{start_iso}T00:00:00", f"{end_iso}T23:59:59"
    w = f"({added} between '{a}' and '{b}') or ({issued} between '{a}' and '{b}')"
    assert not re.search(r"[A-Za-z_]\(", w), "SoQL function syntax is forbidden (Cloudflare 403)"
    return w


def socrata_page(session, dataset_id: str, where: str, offset: int, n: int) -> list[dict]:
    r = http_get(session, SOCRATA_BASE.format(id=dataset_id), {
        "$where": where, "$limit": n, "$offset": offset, "$order": "permitno"})
    rows = r.json()
    if not isinstance(rows, list):
        raise RuntimeError(f"Socrata {dataset_id}: unexpected body {str(rows)[:200]}")
    return rows


# ── checkpointed fetch ─────────────────────────────────────────────────────

def _append_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _save_state(out_dir: Path, state: dict) -> None:
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    (out_dir / "raw" / "state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")


def fetch_pages(source_key: str, out_dir: Path, state: dict, page_fn, page_size: int,
                page_limit: int = 0, pace: float = 0.5, count_claimed: int | None = None,
                where: str = "") -> int:
    """Generic resumable pager. `page_fn(offset, n) -> (rows, more_hint)`.

    Appends every page to raw/<source_key>.jsonl and rewrites state.json after
    each page, so a killed run resumes at next_offset. Stops on a short page
    (and on more_hint False when the page is full but the server says done).
    """
    st = state["sources"].setdefault(source_key, {
        "ok": False, "n": 0, "pages": 0, "next_offset": 0, "where": where,
        "count_claimed": count_claimed, "error": None})
    if st.get("ok"):
        say(f"{source_key}: already complete ({st['n']} rows), skipping")
        return st["n"]
    if count_claimed is not None:
        st["count_claimed"] = count_claimed
    st["where"] = where
    raw_path = out_dir / "raw" / f"{source_key}.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        if page_limit and st["pages"] >= page_limit:
            st["error"] = f"stopped at --page-limit {page_limit}"
            break
        if st["pages"]:
            time.sleep(pace)
        try:
            rows, more = page_fn(st["next_offset"], page_size)
        except Exception as e:  # noqa: BLE001 - checkpoint and report
            st["error"] = f"{type(e).__name__}: {e}"
            _save_state(out_dir, state)
            say(f"{source_key}: FAILED at offset {st['next_offset']}: {e}")
            return st["n"]
        _append_jsonl(raw_path, rows)
        st["n"] += len(rows)
        st["pages"] += 1
        st["next_offset"] += len(rows)
        _save_state(out_dir, state)
        say(f"{source_key}: page {st['pages']} +{len(rows)} (total {st['n']})")
        if len(rows) < page_size or (not more and len(rows) == 0):
            st["ok"] = True
            st["error"] = None
            break
    if st["ok"] and st.get("count_claimed"):
        claimed = st["count_claimed"]
        if abs(st["n"] - claimed) > max(5, 0.01 * claimed):
            st["ok"] = False
            st["error"] = f"fetched {st['n']} vs claimed {claimed}"
            say(f"{source_key}: COUNT DRIFT {st['error']}")
    _save_state(out_dir, state)
    return st["n"]


# ── where clauses + literal styles ─────────────────────────────────────────

def arcgis_date_where(field: str, start_iso: str, style: str) -> str:
    if style == "timestamp":
        return f"{field} >= TIMESTAMP '{start_iso} 00:00:00'"
    if style == "date":
        return f"{field} >= DATE '{start_iso}'"
    ms = int(datetime.strptime(start_iso, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    return f"{field} >= {ms}"


def dc_2025_where(start_iso: str, style: str) -> str:
    return f"({arcgis_date_where('ISSUE_DATE', start_iso, style)}) OR ISSUE_DATE IS NULL"


def fairfax_where(start_iso: str, style: str) -> str:
    return (f"({arcgis_date_where('SUBMITTED_DATE', start_iso, style)}) OR "
            f"({arcgis_date_where('ISSUED_DATE', start_iso, style)})")


def pick_literal_style(session, layer_url: str, where_fn, start_iso: str) -> tuple[str, int]:
    """Try TIMESTAMP, then DATE, then epoch-ms; the first count that succeeds wins."""
    last = None
    for style in ("timestamp", "date", "epoch"):
        try:
            return style, arcgis_count(session, layer_url, where_fn(start_iso, style))
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"no date literal style accepted by {layer_url}: {last}")


# ── name cleaning ──────────────────────────────────────────────────────────

JUNK_PARTS = {"", "NA", "NA NA", "NA NA NA", "NULL", "NULL NULL", "NULL NA", "NA NULL",
              "N/A", "NONE", "UNKNOWN", "SAME", "N A"}
JUNK_TOKENS = {"NA", "NULL", "N/A", "NONE"}
PAREN_RX = re.compile(r"\(([^)]*)\)\s*$")
EXPEDITER_RX = re.compile(r"\b(PERMIT|PERMITS|EXPEDIT\w*|CONSULT\w*|FILING|PROCESSING)\b")


def name_key(name: str) -> str:
    n = norm_name(name)
    if not n:
        return ""
    if ENTITY_RX.search(n):
        return n
    return " ".join(sorted(n.split()))


def clean_applicant(raw) -> dict:
    out = {"name": "", "org": "", "alt": "", "key": "", "is_entity": False}
    s = " ".join(str(raw or "").upper().split())
    s = re.sub(r"\bN\s*/\s*A\b", "N/A", s)      # "N / A" is the same junk as "N/A"
    if not s:
        return out
    parts, seen = [], set()
    for p in s.split(";"):
        p = p.strip(" ,")
        if p in JUNK_PARTS or (p and all(t in JUNK_TOKENS for t in p.split())):
            continue
        org = ""
        m = PAREN_RX.search(p)
        if m:
            org = m.group(1).strip()
            p = p[:m.start()].strip()
        if re.match(r"^[A-Z][A-Z' .-]+,\s*[A-Z][A-Z' .-]*$", p) and not ENTITY_RX.search(p):
            last, first = [x.strip() for x in p.split(",", 1)]
            p = f"{first} {last}"
        p = re.sub(r"[.,]", "", p)
        p = " ".join(p.split())
        k = p.lower()
        if not p or k in seen:
            continue
        seen.add(k)
        parts.append((p, org))
    if not parts:
        return out
    name, org = parts[0]
    out.update({"name": name, "org": org or next((o for _, o in parts[1:] if o), ""),
                "alt": " | ".join(p for p, _ in parts[1:]),
                "key": name_key(name), "is_entity": bool(ENTITY_RX.search(norm_name(name)))})
    return out


DC_INSTITUTION_TERMS = [
    "DISTRICT OF COLUMBIA", "DC HOUSING AUTHORITY", "HOUSING AUTHORITY", "USA",
    "UNITED STATES", "U S A", "US GOVERNMENT", "GENERAL SERVICES ADMIN", "GSA",
    "DEPARTMENT OF", "DEPT OF", "DC PUBLIC", "DCPS", "WMATA", "WASHINGTON METROPOLITAN",
    "SMITHSONIAN", "NATIONAL PARK", "EMBASSY", "HOWARD UNIVERSITY", "GEORGETOWN",
    "GEORGE WASHINGTON UNIV", "AMERICAN UNIVERSITY", "CATHOLIC UNIVERSITY", "GALLAUDET",
    "UNIVERSITY", "HOSPITAL", "MEDSTAR", "CHILDRENS NATIONAL", "CHURCH", "CATHEDRAL",
    "DIOCESE", "ARCHDIOCESE", "SYNAGOGUE", "MOSQUE", "TEMPLE", "SCHOOL", "ACADEMY",
    "PEPCO", "WASHINGTON GAS", "VERIZON", "CONDOMINIUM", "CONDO ASSOC", "ASSOCIATION",
    "COOPERATIVE", "CO OP", "HOUSING OPPORTUNITIES COMMISSION", "MONTGOMERY COUNTY",
    "FAIRFAX COUNTY", "COUNTY OF", "BOARD OF EDUCATION", "PUBLIC SCHOOLS",
    "REDEVELOPMENT AND HOUSING", "ARCHITECT OF THE CAPITOL",
]
_GOV_TERMS = sorted({norm_name(t) for t in (
    EXCLUDE_GROUPS["GOVERNMENT"]["high"] + EXCLUDE_GROUPS["ESTATE_NONPROFIT"]["high"]
    + DC_INSTITUTION_TERMS)} - {""}, key=len, reverse=True)
GOV_RX = re.compile(r"\b(" + "|".join(re.escape(t) for t in _GOV_TERMS) + r")\b")


def is_gov_or_institution(name: str) -> bool:
    return bool(GOV_RX.search(norm_name(name)))


# ── buckets + jurisdiction-specific normalisation ──────────────────────────

def bucket_subtype(juris: str, row: dict) -> str:
    sub = (row.get("permit_subtype") or "").upper()
    src = row.get("source") or ""
    if juris == "dc":
        if sub == "ALTERATION AND REPAIR":
            return "alteration"
        if sub == "ADDITION ALTERATION REPAIR":
            return "addition"
        if sub == "NEW BUILDING":
            return "new_building"
        if sub in ("DEMOLITION", "RAZE"):
            return "demolition"
        if sub.startswith("SOLAR"):
            return "solar"
        return "other"
    if juris == "montgomery":
        if src == "moco_demolition" or "DEMOL" in sub:
            return "demolition"
        if src in MOCO_TRADES:
            return "other"
        if sub.startswith("ALTER") or "RESTORE" in sub or "REPAIR" in sub:
            return "alteration"
        if sub.startswith("ADD"):
            return "addition"
        if sub.startswith("NEW") or sub.startswith("CONSTRUCT"):
            return "new_building"
        if "SOLAR" in sub:
            return "solar"
        return "other"
    if juris == "fairfax":
        if "DEMOL" in sub:
            return "demolition"
        if "SOLAR" in sub:
            return "solar"
        if "NEW" in sub:
            return "new_building"
        if "ADDITION" in sub:
            return "addition"
        if "ALTER" in sub:
            return "alteration"
        return "other"
    return "other"


def zip5(v) -> str:
    s = re.sub(r"\D", "", str(v or ""))
    return s[:5] if len(s) >= 5 else ""


def zip_county(z: str, cache: dict) -> str:
    if not z:
        return ""
    if z.startswith("200") or z[:3] in ("202", "203", "204", "205"):
        return "District of Columbia"
    return cache.get(z, "")


def _owner_fields(raw: str) -> dict:
    name = " ".join(str(raw or "").upper().replace(".", "").split())
    n = norm_name(name)
    return {"owner_raw": raw or "", "owner_name": name, "owner_key": n,
            "owner_is_entity": bool(ENTITY_RX.search(n)) if n else False,
            "owner_excluded": is_gov_or_institution(name) if name else False}


def _base(juris: str, source: str) -> dict:
    return {c: "" for c in PERMIT_COLS} | {
        "jurisdiction": JURIS[juris][0], "fips": JURIS[juris][1], "source": source}


def norm_dc(a: dict, source: str, cache: dict) -> dict:
    r = _base("dc", source)
    app = clean_applicant(a.get("PERMIT_APPLICANT"))
    z = zip5(a.get("ZIPCODE"))
    if not z:   # ZIPCODE is null on every row measured; the address string carries it
        m = re.search(r"\b(\d{5})(?:-\d{4})?\s*$", str(a.get("FULL_ADDRESS") or ""))
        z = m.group(1) if m else ""
    r.update({
        "permit_id": a.get("PERMIT_ID") or "", "permit_type": a.get("PERMIT_TYPE_NAME") or "",
        "permit_subtype": a.get("PERMIT_SUBTYPE_NAME") or "",
        "category": a.get("PERMIT_CATEGORY_NAME") or "",
        "status": a.get("APPLICATION_STATUS_NAME") or "",
        "issued_date": epoch_ms_to_iso(a.get("ISSUE_DATE")),
        "address": a.get("FULL_ADDRESS") or "", "city": a.get("CITY") or "WASHINGTON",
        "zip": z, "county": "District of Columbia", "parcel_id": a.get("SSL") or "",
        "description": " ".join(str(a.get("DESC_OF_WORK") or "").split()),
        "applicant_raw": a.get("PERMIT_APPLICANT") or "", "applicant_name": app["name"],
        "applicant_org": app["org"], "applicant_alt": app["alt"],
        "applicant_key": app["key"], "applicant_is_entity": app["is_entity"],
        "fees": a.get("FEES_PAID") if a.get("FEES_PAID") is not None else "",
        "lat": a.get("LATITUDE") or "", "lon": a.get("LONGITUDE") or "",
        "last_modified": epoch_ms_to_iso(a.get("LASTMODIFIEDDATE")),
    })
    r.update(_owner_fields(a.get("OWNER_NAME")))
    r["bucket"] = bucket_subtype("dc", r)
    return r


def norm_moco(row: dict, source: str, cache: dict) -> dict:
    r = _base("montgomery", source)
    street = " ".join(x for x in (row.get("stno"), row.get("predir"), row.get("stname"),
                                  row.get("suffix"), row.get("postdir")) if x)
    z = zip5(row.get("zip"))
    r.update({
        "permit_id": row.get("permitno") or "", "permit_type": row.get("applicationtype") or "",
        "permit_subtype": row.get("worktype") or "", "category": row.get("usecode") or "",
        "status": row.get("status") or "", "applied_date": soc_ts_to_iso(row.get("addeddate")),
        "issued_date": soc_ts_to_iso(row.get("issueddate")),
        "finaled_date": soc_ts_to_iso(row.get("finaleddate")),
        "address": street, "city": row.get("city") or "", "zip": z, "county": "Montgomery",
        "description": " ".join(str(row.get("description") or "").split()),
        "valuation": row.get("declaredvaluation") or "",
    })
    loc = row.get("location") or {}
    if isinstance(loc, dict):
        r["lat"], r["lon"] = loc.get("latitude") or "", loc.get("longitude") or ""
    r["bucket"] = bucket_subtype("montgomery", r)
    return r


def norm_fairfax(a: dict, source: str, cache: dict) -> dict:
    r = _base("fairfax", source)
    z = zip5(a.get("ZIP_CODE"))
    r.update({
        "permit_id": a.get("RECORDID") or "", "permit_type": a.get("APPTYPEALIAS") or "",
        "permit_subtype": a.get("APPTYPEALIAS") or "", "category": a.get("PROJECT_NAME") or "",
        "status": a.get("RECORD_STATUS") or "",
        "applied_date": epoch_ms_to_iso(a.get("SUBMITTED_DATE")),
        "issued_date": epoch_ms_to_iso(a.get("ISSUED_DATE")),
        "finaled_date": epoch_ms_to_iso(a.get("CLOSED_DATE")),
        "address": " ".join(x for x in (a.get("ADDRESS_1"), a.get("ADDRESS_2")) if x),
        "city": a.get("CITY") or "", "zip": z, "county": "Fairfax",
        "parcel_id": a.get("PARCEL_ID") or "", "link_url": a.get("LINK_URL") or "",
    })
    r["bucket"] = bucket_subtype("fairfax", r)
    return r


NORMALISERS = {"dc": norm_dc, "montgomery": norm_moco, "fairfax": norm_fairfax}


# ── analyses (pure functions) ──────────────────────────────────────────────

def dedupe_dc(rows: list[dict]) -> tuple[list[dict], int]:
    """One row per PERMIT_ID; the 2026 layer wins, then the later issue date."""
    rank = {"dc_permits_2026": 1, "dc_permits_2025": 0}
    best: dict[str, dict] = {}
    for r in rows:
        k = r.get("permit_id") or f"?{id(r)}"
        cur = best.get(k)
        if cur is None or (rank.get(r["source"], 0), r.get("issued_date", "")) > \
                (rank.get(cur["source"], 0), cur.get("issued_date", "")):
            best[k] = r
    return list(best.values()), len(rows) - len(best)


def _mix(counter: Counter, top: int = 6) -> str:
    return "|".join(f"{k}:{n}" for k, n in counter.most_common(top))


def _person_tokens(name: str) -> set:
    return {t for t in norm_name(name).split() if len(t) > 2}


def repeat_applicants(rows: list[dict], min_n: int = 2, bbl_index: dict | None = None) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("applicant_key"):
            groups[r["applicant_key"]].append(r)
    out = []
    for key, rs in groups.items():
        if len(rs) < min_n:
            continue
        names = Counter(r["applicant_name"] for r in rs)
        name = names.most_common(1)[0][0]
        owners = {r["owner_key"] for r in rs if r.get("owner_key")}
        subs = Counter(r["permit_subtype"] for r in rs if r.get("permit_subtype"))
        toks = _person_tokens(name)
        self_own = sum(1 for r in rs if toks and toks <= _person_tokens(r.get("owner_name", "")))
        n, n_own, n_sub = len(rs), len(owners), len(subs)
        # Filing for 10+ different owners is professional filing (expediter or a large
        # contractor); the data cannot split the two, so the roster join decides.
        if EXPEDITER_RX.search(name) or (n >= 15 and n_own >= 10) or \
                (n >= 15 and n_own >= 5 and n_sub >= 3):
            flag = "likely"
        elif n >= 8 and n_own >= 4:
            flag = "possible"
        else:
            flag = ""
        bbl = bbl_lookup(name, bbl_index) if bbl_index else None
        dates = sorted(d for d in (r.get("issued_date") for r in rs) if d)
        out.append({
            "applicant_key": key, "applicant_name": name,
            "applicant_org": next((r["applicant_org"] for r in rs if r.get("applicant_org")), ""),
            "is_entity": rs[0].get("applicant_is_entity", False), "n_permits": n,
            "n_distinct_addresses": len({r["address"] for r in rs if r.get("address")}),
            "n_distinct_owners": n_own, "subtype_mix": _mix(subs),
            "permit_type_mix": _mix(Counter(r["permit_type"] for r in rs)),
            "zips": _mix(Counter(r["zip"] for r in rs if r.get("zip")), 8),
            "first_date": dates[0] if dates else "", "last_date": dates[-1] if dates else "",
            "self_owner_share": round(self_own / n, 2), "expediter_flag": flag,
            "bbl_match": bbl["how"] if bbl else "",
            "bbl_entity": bbl["ENTITYNAME"] if bbl else "",
            "bbl_license_type": bbl["LICENSETYPE"] if bbl else "",
            "bbl_status": bbl["LICENSESTATUS"] if bbl else "",
            "sample_addresses": " || ".join(sorted({r["address"] for r in rs if r.get("address")})[:3]),
        })
    out.sort(key=lambda d: -d["n_permits"])
    return out


APPLICANT_COLS = ["applicant_key", "applicant_name", "applicant_org", "is_entity", "n_permits",
                  "n_distinct_addresses", "n_distinct_owners", "subtype_mix", "permit_type_mix",
                  "zips", "first_date", "last_date", "self_owner_share", "expediter_flag",
                  "bbl_match", "bbl_entity", "bbl_license_type", "bbl_status", "sample_addresses"]

INVESTOR_BUCKETS = {"alteration", "addition", "new_building", "demolition"}


def repeat_owners(rows: list[dict], min_n: int = 2, registry_index: dict | None = None) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if (r.get("permit_type") == "CONSTRUCTION" and r.get("bucket") in INVESTOR_BUCKETS
                and r.get("owner_key") and not r.get("owner_excluded")):
            groups[r["owner_key"]].append(r)
    out = []
    for key, rs in groups.items():
        if len(rs) < min_n:
            continue
        name = Counter(r["owner_name"] for r in rs).most_common(1)[0][0]
        # volume 10 = MIN_VOLUME: a generic keyword becomes REVIEW, not EXCLUDE (a local
        # "DOUGLAS DEVELOPMENT" is a buyer, not D R Horton -- dispo_buyers.local_tier lesson);
        # then the word-boundary re-check, and ordinary names carry no label at all.
        tier, reason, _ = classify_one(norm_name(name), 10)
        if tier == "EXCLUDE":
            kw = reason.split(":", 1)[-1].strip()
            if kw and not re.search(r"\b" + re.escape(norm_name(kw)) + r"\b", norm_name(name)):
                tier, reason = "REVIEW", f"substring-only match: {kw}"
        if tier in ("1", "below"):
            tier, reason = "", ""
        hit, rec = registry_match(key, name, registry_index) if registry_index else ("", None)
        dates = sorted(d for d in (r.get("issued_date") for r in rs) if d)
        fees = sum(float(r["fees"]) for r in rs if str(r.get("fees") or "").replace(".", "").isdigit())
        out.append({
            "owner_key": key, "owner_name": name, "is_entity": rs[0].get("owner_is_entity", False),
            "tier": tier, "tier_reason": reason, "n_permits": len(rs),
            "n_distinct_addresses": len({r["address"] for r in rs if r.get("address")}),
            "subtype_mix": _mix(Counter(r["permit_subtype"] for r in rs)),
            "zips": _mix(Counter(r["zip"] for r in rs if r.get("zip")), 8),
            "first_date": dates[0] if dates else "", "last_date": dates[-1] if dates else "",
            "fees_total": round(fees), "registry_hit": hit or "none",
            "registry_name": rec["name"] if rec else "",
            "registry_n_buys": rec.get("n_buys", "") if rec else "",
            "registry_tier": rec.get("tier", "") if rec else "",
            "registry_counties": "|".join(rec.get("counties") or []) if rec else "",
            "sample_addresses": " || ".join(sorted({r["address"] for r in rs if r.get("address")})[:3]),
        })
    out.sort(key=lambda d: -d["n_permits"])
    return out


OWNER_COLS = ["owner_key", "owner_name", "is_entity", "tier", "tier_reason", "n_permits",
              "n_distinct_addresses", "subtype_mix", "zips", "first_date", "last_date",
              "fees_total", "registry_hit", "registry_name", "registry_n_buys", "registry_tier",
              "registry_counties", "sample_addresses"]

EXPIRED_RX = re.compile(r"EXPIR|VOID|REVOK|CANCEL|LAPSE", re.I)
WITHDRAWN_RX = re.compile(r"WITHDRAW|DENIED|ABANDON", re.I)
DONE_RX = re.compile(r"ISSU|FINAL|CLOSED|COMPLET|APPROV", re.I)
# DC publishes no application date; these statuses mean "applied, not issued" and the
# layer's LASTMODIFIEDDATE is the only age signal (carried as last_modified).
PENDING_RX = re.compile(r"PENDING|IN PROCESS|INCOMPLETE|SCREENING|INFORMATION MI|STOP WORK", re.I)


def stall_class(row: dict, stale_months: int, today: datetime) -> str:
    st = row.get("status") or ""
    if EXPIRED_RX.search(st):
        return "expired"
    if WITHDRAWN_RX.search(st):
        return "withdrawn"
    if row.get("issued_date") or DONE_RX.search(st):
        return ""
    applied = iso_to_dt(row.get("applied_date") or "")
    if applied:
        if (today - applied).days >= round(30.4 * stale_months):
            return "stalled"
        return ""
    if PENDING_RX.search(st):
        ref = iso_to_dt(row.get("last_modified") or "")
        if ref is None or (today - ref).days >= round(30.4 * stale_months):
            return "pending"
    return ""


def stalled(rows: list[dict], stale_months: int = 6, today: datetime | None = None) -> list[dict]:
    today = today or datetime.now()
    out = []
    for r in rows:
        cls = stall_class(r, stale_months, today)
        if not cls:
            continue
        ref = (iso_to_dt(r.get("applied_date") or "") or iso_to_dt(r.get("issued_date") or "")
               or iso_to_dt(r.get("last_modified") or ""))
        out.append({**{k: r.get(k, "") for k in (
            "jurisdiction", "permit_id", "status", "applied_date", "issued_date", "last_modified",
            "address", "city", "zip", "owner_name", "applicant_name", "bucket", "description",
            "valuation", "link_url")},
            "stall_class": cls, "age_days": (today - ref).days if ref else ""})
    addr_n = Counter((r["jurisdiction"], r["address"]) for r in out if r.get("address"))
    for r in out:
        r["n_at_address"] = addr_n.get((r["jurisdiction"], r["address"]), 1)
    out.sort(key=lambda d: (d["jurisdiction"], -(d["age_days"] or 0)))
    return out


STALLED_COLS = ["jurisdiction", "stall_class", "permit_id", "status", "applied_date",
                "issued_date", "last_modified", "age_days", "address", "city", "zip", "owner_name",
                "applicant_name", "bucket", "description", "valuation", "link_url", "n_at_address"]


def zip_heat(rows: list[dict], stalled_rows: list[dict], owners: list[dict]) -> list[dict]:
    g: dict[tuple, dict] = {}
    repeat_keys = {o["owner_key"] for o in owners}
    stall_ct = Counter((r["jurisdiction"], r["zip"], r["stall_class"]) for r in stalled_rows)
    for r in rows:
        k = (r["jurisdiction"], r.get("zip") or "")
        d = g.setdefault(k, {"jurisdiction": k[0], "zip": k[1], "county": r.get("county", ""),
                             "n_total": 0, "n_construction": 0, "n_alteration": 0, "n_addition": 0,
                             "n_new_building": 0, "n_demolition": 0, "n_solar": 0,
                             "n_repeat_owner_permits": 0, "valuation_sum": 0.0,
                             "_owners": Counter()})
        d["n_total"] += 1
        if r.get("permit_type") == "CONSTRUCTION" or r["jurisdiction"] != "District of Columbia":
            if r.get("bucket") in INVESTOR_BUCKETS | {"solar"}:
                d["n_construction"] += 1
        b = r.get("bucket")
        if b in ("alteration", "addition", "new_building", "demolition", "solar"):
            d[f"n_{b}"] += 1
        if r.get("owner_key") in repeat_keys:
            d["n_repeat_owner_permits"] += 1
            if r.get("owner_is_entity"):
                d["_owners"][r["owner_name"]] += 1
        try:
            d["valuation_sum"] += float(r.get("valuation") or 0)
        except ValueError:
            pass
    tot = Counter(r["jurisdiction"] for r in rows)
    out = []
    for (j, z), d in g.items():
        d["n_stalled"] = stall_ct.get((j, z, "stalled"), 0)
        d["n_expired"] = stall_ct.get((j, z, "expired"), 0) + stall_ct.get((j, z, "withdrawn"), 0)
        d["developer_heat"] = d["n_new_building"] + d["n_demolition"] + d["n_addition"]
        d["top_owner_entities"] = _mix(d.pop("_owners"), 3)
        d["valuation_sum"] = round(d["valuation_sum"])
        d["share_of_jurisdiction"] = round(d["n_total"] / max(1, tot[j]), 4)
        out.append(d)
    out.sort(key=lambda d: (d["jurisdiction"], -d["developer_heat"], -d["n_total"]))
    return out


ZIP_COLS = ["jurisdiction", "zip", "county", "n_total", "n_construction", "n_alteration",
            "n_addition", "n_new_building", "n_demolition", "n_solar", "n_stalled", "n_expired",
            "developer_heat", "n_repeat_owner_permits", "top_owner_entities", "valuation_sum",
            "share_of_jurisdiction"]


# ── BBL roster + vendor seed ───────────────────────────────────────────────

def bbl_active(row: dict, today_iso: str) -> bool:
    st = str(row.get("LICENSESTATUS") or "").upper()
    end = epoch_ms_to_iso(row.get("LICENSEENDDATE"))
    return "ACTIVE" in st or (bool(end) and end >= today_iso)


def build_bbl_index(bbl_rows: list[dict]) -> dict:
    """exact: norm_name(entity | trade name | owner first+last) -> row;
    tokens: first significant token -> [rows] for the fuzzy pass."""
    exact: dict[str, dict] = {}
    tokens: dict[str, list[dict]] = defaultdict(list)
    for r in bbl_rows:
        names = [r.get("ENTITYNAME"), r.get("ENTITYTRADENAME"),
                 " ".join(x for x in (r.get("BUSINESSOWNERFIRSTNAME"),
                                      r.get("BUSINESSOWNERLASTNAME")) if x)]
        for nm in names:
            if " ".join(str(nm or "").upper().split()) in NAME_JUNK:
                continue          # an "N/A" trade name must never be a join key
            n = norm_name(nm or "")
            if n:
                exact.setdefault(n, r)
                exact.setdefault(name_key(n), r)
                toks = [t for t in n.split() if len(t) > 2]
                if toks:
                    tokens[toks[0]].append(r)
    return {"exact": exact, "tokens": tokens}


def _similar(a: str, b: str) -> bool:
    """Copy of dispo_buyers._similar (importing that module drags SiftMap in)."""
    stop = {"LLC", "LLP", "LP", "INC", "CORP", "THE", "COMPANY", "CO"}
    ta = {t for t in norm_name(a).split() if t not in stop and len(t) > 2}
    tb = {t for t in norm_name(b).split() if t not in stop and len(t) > 2}
    if not ta or not tb:
        return False
    if ta <= tb or tb <= ta:
        return True
    return len(ta & tb) / max(1, min(len(ta), len(tb))) >= 0.6


def bbl_lookup(name: str, index: dict) -> dict | None:
    n = norm_name(name)
    r = index["exact"].get(n) or index["exact"].get(name_key(n))
    if r:
        return {**r, "how": "exact"}
    toks = [t for t in n.split() if len(t) > 2]
    if not toks or ENTITY_RX.search(n) is None and len(toks) < 2:
        return None
    for cand in index["tokens"].get(toks[0], []):
        for nm in (cand.get("ENTITYNAME"), cand.get("ENTITYTRADENAME")):
            if nm and ENTITY_RX.search(n) and _similar(n, nm):
                return {**cand, "how": "fuzzy"}
    return None


NAME_JUNK = {"", "N/A", "NA", "NONE", "NULL", "UNKNOWN", "SAME"}


def company_name(r: dict) -> str:
    """Trade name, else entity name, else the licensee's own name; never 'N/A'."""
    for v in (r.get("ENTITYTRADENAME"), r.get("ENTITYNAME")):
        v = " ".join(str(v or "").split())
        if v.upper() not in NAME_JUNK:
            return v
    return " ".join(x for x in (r.get("owner_first") or r.get("BUSINESSOWNERFIRSTNAME"),
                                r.get("owner_last") or r.get("BUSINESSOWNERLASTNAME")) if x)


def license_roster(bbl_rows: list[dict], applicant_rows: list[dict], today_iso: str) -> list[dict]:
    by_name: dict[str, dict] = {}
    for a in applicant_rows:
        if a.get("bbl_entity"):
            by_name.setdefault(norm_name(a["bbl_entity"]), a)
    out = []
    for r in bbl_rows:
        if not bbl_active(r, today_iso):
            continue
        ent = norm_name(r.get("ENTITYNAME") or "")
        a = by_name.get(ent)
        out.append({
            "license_key": r.get("CUSTOMERNUMBER") or "",
            "ENTITYNAME": r.get("ENTITYNAME") or "", "ENTITYTRADENAME": r.get("ENTITYTRADENAME") or "",
            "owner_first": r.get("BUSINESSOWNERFIRSTNAME") or "",
            "owner_last": r.get("BUSINESSOWNERLASTNAME") or "",
            "LICENSETYPE": r.get("LICENSETYPE") or "", "BUSINESSACTIVITY": r.get("BUSINESSACTIVITY") or "",
            "LICENSESTATUS": r.get("LICENSESTATUS") or "",
            "LICENSEENDDATE": epoch_ms_to_iso(r.get("LICENSEENDDATE")),
            "PREMISEADDRESS": r.get("PREMISEADDRESS") or "", "BILLINGADDRESS": r.get("BILLINGADDRESS") or "",
            "n_permits_as_applicant": a["n_permits"] if a else 0,
            "permit_zips": a["zips"] if a else "", "last_permit_date": a["last_date"] if a else "",
            "expediter_flag": a["expediter_flag"] if a else "",
        })
    out.sort(key=lambda d: (-d["n_permits_as_applicant"], d["ENTITYNAME"]))
    return out


ROSTER_COLS = ["license_key", "ENTITYNAME", "ENTITYTRADENAME", "owner_first", "owner_last",
               "LICENSETYPE", "BUSINESSACTIVITY", "LICENSESTATUS", "LICENSEENDDATE",
               "PREMISEADDRESS", "BILLINGADDRESS", "n_permits_as_applicant", "permit_zips",
               "last_permit_date", "expediter_flag"]


def vendor_seed(roster_rows: list[dict], min_permits: int = 2) -> list[dict]:
    out = []
    seen: set[str] = set()
    for r in roster_rows:
        if r["n_permits_as_applicant"] < min_permits or r.get("expediter_flag") == "likely":
            continue
        company = company_name(r)
        if not company or norm_name(company) in seen:   # one licensee, several licenses
            continue
        seen.add(norm_name(company))
        zips = ", ".join(p.split(":")[0] for p in r["permit_zips"].split("|") if p)
        out.append({f: "" for f in VENDOR_FIELDS} | {
            "trade": r["BUSINESSACTIVITY"] or r["LICENSETYPE"],
            "company": company,
            "contact": " ".join(x for x in (r["owner_first"], r["owner_last"]) if x),
            "service_area": zips, "serves_geo": "Y",
            "license": f"DC BBL {r['LICENSETYPE']}, exp {r['LICENSEENDDATE']}".strip(", "),
            "source": (f"{r['n_permits_as_applicant']} DC permits as applicant, last "
                       f"{r['last_permit_date']} (Open Data DC FEEDS/DCRA L17+L18)"),
            "cautions": "phone / email / rating / BBB not in any portal; verify BBL status live",
            "confidence": "Medium",
        })
    return out


# ── registry join ──────────────────────────────────────────────────────────

def load_registry(path: Path) -> dict | None:
    if not path.exists():
        return None
    recs = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(recs, dict):
        recs = list(recs.values())
    exact: dict[str, dict] = {}
    tokens: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        for nm in [r.get("buyer_key"), r.get("name")] + list(r.get("aliases") or []):
            n = norm_name(nm or "")
            if n:
                exact.setdefault(n, r)
        n = norm_name(r.get("name") or "")
        toks = [t for t in n.split() if len(t) > 2]
        if toks:
            tokens[toks[0]].append(r)
    return {"exact": exact, "tokens": tokens, "n": len(recs)}


def registry_match(key: str, name: str, index: dict) -> tuple[str, dict | None]:
    n = norm_name(name)
    r = index["exact"].get(n) or index["exact"].get(key)
    if r:
        return "exact", r
    toks = [t for t in n.split() if len(t) > 2]
    if toks and ENTITY_RX.search(n):
        for cand in index["tokens"].get(toks[0], []):
            if _similar(n, cand.get("name") or ""):
                return "fuzzy", cand
    return "none", None


# ── output ─────────────────────────────────────────────────────────────────

def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    say(f"wrote {path.name} ({len(rows)} rows)")


def _md_table(rows: list[dict], cols: list[str], top: int = 25) -> str:
    if not rows:
        return "_none_\n"
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows[:top]:
        lines.append("| " + " | ".join(str(r.get(c, "")).replace("|", "/")[:60] for c in cols) + " |")
    return "\n".join(lines) + "\n"


PHASE2 = [
    ("Montgomery MD contractor names", "DPS eSearch (ASP.NET, anonymous; search by contractor "
     "name / license, or by application date; results show the associated contractor + license)",
     "https://permittingservices.montgomerycountymd.gov/DPS/online/eSearch.aspx"),
    ("Fairfax VA contractor names", "PLUS Accela general search (anonymous: License Type, State "
     "License Number, Business Name, date range) or the per-record LINK_URL page",
     "https://plus.fairfaxcounty.gov/CitizenAccess/Cap/CapHome.aspx?module=Building&TabName=Building"),
    ("Anne Arundel MD (no open dataset)", "Accela Citizen Access, anonymous: date range (the "
     "START DATE must be changed to search >30 days), address, Licensed Professional, Business "
     "Name. gis.aacounty.org InspectionsPermits is token-gated; opendata.aacounty.org has no permits.",
     "https://aca-prod.accela.com/AACO/Cap/CapHome.aspx?module=Permits&TabName=Permits"),
    ("Frederick MD (no open dataset)", "CIVICS lookup (JS app) + Monthly Applications Report "
     "PDFs (application date, type, address, applicant/contractor) via pypdfium2 text "
     "extraction. TRAP: aca-prod.accela.com/FREDERICK is the Town of Frederick, COLORADO.",
     "https://planningandpermitting.frederickcountymd.gov/lookup-record ; "
     "https://frederickcountymd.gov/DocumentCenter/Index/287"),
    ("Arlington VA", "'Permit Application History' on the county open-data directory (JS "
     "portal; CSV/API link not yet captured) + Permit Arlington Accela (anonymous view)",
     "https://data.arlingtonva.us/dataset/329 ; "
     "https://aca-prod.accela.com/ARLINGTONCO/Cap/CapHome.aspx?module=Building&TabName=Home"),
    ("VA contractor license roster", "DPOR publishes bulk tab-delimited regulant list files "
     "(refreshed ~5 business days); capture the file URL from the Contractors board page",
     "https://www.dpor.virginia.gov/LicenseLookup"),
    ("MD contractor license roster", "MHIC public query, per-name only (no bulk file)",
     "https://www.dllr.state.md.us/cgi-bin/ElectronicLicensing/OP_Search/OP_search.cgi"),
    ("Owner names for Montgomery / Fairfax permits", "address-match join to the SDAT roll "
     "(Socrata kb22-is2w, pattern in md_hoa_lien_pull.py) / Fairfax assessor (no lookup in repo)",
     "https://opendata.maryland.gov/resource/kb22-is2w.json"),
]

MATRIX_ROWS = [
    ("company / contractor", "applicant only (expediter-heavy)", "N (eSearch)", "N (PLUS page)",
     "N (Accela)", "N (PDF)", "?"),
    ("contact person", "applicant name", "N", "N", "N (Accela)", "N (PDF)", "?"),
    ("license", "BBL roster join", "N (eSearch)", "N (PLUS)", "N (Accela)", "N", "?"),
    ("service area (permit ZIPs)", "Y", "Y (by address only)", "Y (by address only)", "N", "N", "?"),
    ("phone / email / rating / BBB", "N", "N", "N", "N", "N", "N"),
    ("owner name", "Y", "N", "N", "N", "N", "?"),
    ("valuation", "fees only", "Y (declared)", "N", "N", "N", "?"),
    ("applied date", "N", "Y", "Y", "N", "Y (PDF)", "?"),
    ("issued / finaled date", "Y / N", "Y / Y", "Y / closed", "N", "N", "?"),
    ("status", "Y", "Y", "Y", "N", "N", "?"),
    ("parcel id", "SSL", "N", "Y", "N", "N", "?"),
    ("lat / lon", "Y", "Y", "N (parcel geometry)", "N", "N", "?"),
    ("description of work", "Y (partial)", "Y", "project name only", "N", "N", "?"),
]


def write_evaluation(out_dir: Path, s: dict) -> Path:
    L = []
    L.append("# Building-permit portal evaluation -- footprint, last %s months\n" % s["months"])
    L.append(f"Run {s['at']} | window {s['window'][0]} .. {s['window'][1]} | read-only, no spend, "
             "nothing written to DataSift.\n")
    L.append("## 1. Run facts\n")
    L.append(_md_table(s["source_rows"], ["source", "ok", "rows", "pages", "count_claimed", "error"], 20))
    L.append(f"\nDC 2025/2026 overlap removed: {s['dc_overlap']} permits. Unmapped ZIP->county: "
             f"{s['zip_unknown']} rows. Applicant cleaning: {s['clean_stats']}.\n")
    L.append("## 2. Per-jurisdiction coverage\n")
    L.append(_md_table(s["juris_rows"], ["jurisdiction", "permits", "with_applicant", "with_owner",
                                         "construction", "stalled", "expired_withdrawn",
                                         "repeat_applicants", "repeat_owners"], 10))
    L.append("\n## 3. Field availability by jurisdiction (Y / N / where it lives instead)\n")
    L.append(_md_table([dict(zip(["field", "DC", "Montgomery", "Fairfax", "Anne Arundel",
                                  "Frederick", "Arlington"], r)) for r in MATRIX_ROWS],
                       ["field", "DC", "Montgomery", "Fairfax", "Anne Arundel", "Frederick", "Arlington"], 20))
    L.append("\nAnne Arundel, Frederick and Arlington have no machine-readable feed today; every "
             "cell there is a phase-2 browser job (section 8).\n")
    L.append("## 4. What each Sift skill can take from this\n")
    L.append(f"- **vendor-directory-builder**: `vendor_directory_seed.csv` holds {s['n_seed']} DC "
             f"licensed contractors that pulled >= 2 permits in the window (company, contact, "
             f"license, service-area ZIPs, source). Phone, email, rating and BBB are NOT in any "
             f"portal and stay blank. {s['n_roster']} active DC contractor licenses in "
             f"`dc_contractor_licenses.csv`. Trap: the raw applicant field is expediters and "
             f"homeowners -- {s['n_expediters']} of the {s['n_applicants']} repeat applicants are "
             f"flagged `likely` expediters; only the roster join feeds the seed.\n")
    L.append(f"- **buyer registry / buyer-prospector**: `repeat_owners.csv` lists {s['n_owners']} "
             f"non-government owners with >= {s['min_permits']} DC construction permits "
             f"(alteration / addition / new building / demolition); {s['registry_hits']} already "
             f"sit in the dispo buyer registry ({s['registry_n']} buyers), {s['registry_new']} are "
             f"new names. Montgomery and Fairfax carry no owner -- that needs the SDAT / assessor "
             f"join (phase 2).\n")
    L.append(f"- **first-market-county-data / distress**: `stalled_permits.csv` -- "
             f"{s['n_stalled']} stalled (applied >= {s['stale_months']} months ago, never issued), "
             f"{s['n_expired']} expired / withdrawn. The DC open layer publishes ISSUED permits "
             f"only ({s['dc_no_issue']} rows without an issue date) and no application date, so DC "
             f"contributes expired / cancelled / revoked permits, never a stalled application; "
             f"unissued DC applications live only in the DOB citizen portal (phase 2). Fairfax "
             f"publishes accepted records only (statuses Issued / Closed), so it has none either.\n")
    L.append("- **comp-package / rehab-estimator**: a permit at a comp address in the last 12 "
             "months is renovated-comp evidence (`permits_all.csv`, join on address+zip); the "
             "24-month version needs DC layer 16 (2024) as well. Montgomery `valuation` is the "
             "applicant's declared cost.\n")
    L.append("- **sift-market-research / market_analyzer**: `zip_heat.csv` is a ready ZIP-level "
             "investor-activity factor (developer_heat = new building + demolition + addition).\n")
    L.append("\n## 5. Top repeat applicants (DC, cleaned; read expediter_flag first)\n")
    L.append(_md_table(s["applicants"], ["applicant_name", "n_permits", "n_distinct_owners",
                                         "expediter_flag", "self_owner_share", "bbl_match",
                                         "subtype_mix", "zips"]))
    L.append("\n## 6. Top repeat owners on DC construction permits (government removed)\n")
    L.append(_md_table(s["owners"], ["owner_name", "n_permits", "n_distinct_addresses", "tier",
                                     "registry_hit", "subtype_mix", "zips"]))
    L.append("\n## 7. ZIP heat (top by developer_heat)\n")
    L.append(_md_table(s["zips"], ["jurisdiction", "zip", "n_total", "n_alteration", "n_addition",
                                   "n_new_building", "n_demolition", "n_stalled", "developer_heat",
                                   "top_owner_entities"], 30))
    L.append("\nUnmapped subtype values (bucket = other), top 20:\n")
    L.append(_md_table(s["unmapped"], ["jurisdiction", "permit_subtype", "n"], 20))
    L.append("\nDoctor distributions (status / type vocabularies as the portals report them):\n")
    for k, v in (s.get("doctor") or {}).items():
        if isinstance(v, dict) and v.get("groupby"):
            for field, vals in v["groupby"].items():
                L.append(f"- {k} {field}: " + ", ".join(f"{x['value']}={x['n']}" for x in vals[:12]))
    L.append("\n## 8. Phase 2 -- browser / PDF sources (on go, not built)\n")
    L.append(_md_table([{"what": a, "how": b, "url": c} for a, b, c in PHASE2], ["what", "how", "url"], 20))
    L.append("\n## 9. Draft rows for data/ftm_sources_mddcva.csv\n")
    L.append("`data_type=building_permit` (no such CANON type in dpd_ftm_registry.py yet):\n")
    for j, off, url, acc in [
        ("District of Columbia", "DOB / Open Data DC", DC_BASE, "free ArcGIS JSON, layers 18/17/13"),
        ("Montgomery", "DPS / dataMontgomery", SOCRATA_BASE.format(id="xfxj-qszi"), "free Socrata JSON"),
        ("Fairfax", "LDS / Fairfax GIS", FAIRFAX_URL, "free ArcGIS JSON, nightly"),
        ("Anne Arundel", "Inspections & Permits", PHASE2[2][2], "anonymous browser search"),
        ("Frederick", "Permits & Inspections", PHASE2[3][2], "browser + monthly PDF"),
        ("Arlington", "CPHD / Permit Arlington", PHASE2[4][2], "browser; CSV link to capture"),
    ]:
        L.append(f"- {j} | building_permit | {off} | {url} | {acc} | verified 2026-09-24")
    p = out_dir / "evaluation.md"
    p.write_text("\n".join(L), encoding="utf-8")
    say(f"wrote {p.name}")
    return p


# ── doctor ─────────────────────────────────────────────────────────────────

def doctor(session, out_dir: Path, juris: list[str], start_iso: str) -> dict:
    d: dict = {"at": datetime.now().isoformat(timespec="seconds"), "window_start": start_iso}
    if "dc" in juris:
        for key, layer in DC_LAYERS.items():
            url = f"{DC_BASE}/{layer}"
            m = arcgis_meta(session, url)
            if key == "dc_permits_2026":
                where, style = "1=1", "n/a"
            elif key == "dc_permits_2025":
                style, _ = pick_literal_style(session, url, dc_2025_where, start_iso)
                where = dc_2025_where(start_iso, style)
            else:
                where, style = f"CATEGORYSERVICETYPE = '{BBL_CATEGORY}'", "n/a"
            cnt = arcgis_count(session, url, where)
            gb = {}
            for field in {"dc_permits_2026": ["APPLICATION_STATUS_NAME", "PERMIT_TYPE_NAME",
                                              "PERMIT_SUBTYPE_NAME"],
                          "dc_permits_2025": ["APPLICATION_STATUS_NAME"],
                          "dc_bbl": ["LICENSESTATUS", "LICENSETYPE"]}[key]:
                gb[field] = arcgis_groupby(session, url, where, field, m["oid"])
            d[key] = {**m, "where": where, "literal_style": style, "count": cnt, "groupby": gb}
            say(f"doctor {key}: {cnt} rows, page {m['maxRecordCount']}, style {style}")
    if "fairfax" in juris:
        m = arcgis_meta(session, FAIRFAX_URL)
        style, cnt = pick_literal_style(session, FAIRFAX_URL, fairfax_where, start_iso)
        where = fairfax_where(start_iso, style)
        gb = {f: arcgis_groupby(session, FAIRFAX_URL, where, f, m["oid"])
              for f in ("RECORD_STATUS", "APPTYPEALIAS")}
        d["fairfax"] = {**m, "where": where, "literal_style": style, "count": cnt, "groupby": gb}
        say(f"doctor fairfax: {cnt} rows in window, page {m['maxRecordCount']}, style {style}")
    if "montgomery" in juris:
        where = socrata_where(start_iso, window(0)[1])
        for key, ds in {**MOCO_DATASETS, **MOCO_TRADES}.items():
            try:
                rows = socrata_page(session, ds, where, 0, 1)
                d[key] = {"dataset": ds, "where": where, "ok": True,
                          "fields": sorted(rows[0].keys()) if rows else []}
                say(f"doctor {key}: ok ({len(rows)} probe row)")
            except Exception as e:  # noqa: BLE001
                d[key] = {"dataset": ds, "where": where, "ok": False, "error": f"{type(e).__name__}: {e}"}
                say(f"doctor {key}: FAILED {e}")
            time.sleep(1.0)
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    (out_dir / "raw" / "doctor.json").write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
    say(f"wrote {out_dir / 'raw' / 'doctor.json'}")
    return d


# ── pull ───────────────────────────────────────────────────────────────────

def pull(session, out_dir: Path, state: dict, juris: list[str], a) -> None:
    start_iso, end_iso = state["window"]
    pace = a.pace
    if "dc" in juris:
        for key, layer in DC_LAYERS.items():
            url = f"{DC_BASE}/{layer}"
            m = arcgis_meta(session, url)
            if key == "dc_permits_2026":
                where = "1=1"
            elif key == "dc_permits_2025":
                style = state.get("literal_style", {}).get(key) or \
                    pick_literal_style(session, url, dc_2025_where, start_iso)[0]
                state.setdefault("literal_style", {})[key] = style
                where = dc_2025_where(start_iso, style)
            else:
                where = f"CATEGORYSERVICETYPE = '{BBL_CATEGORY}'"
            claimed = arcgis_count(session, url, where)
            fetch_pages(key, out_dir, state,
                        lambda off, n, u=url, w=where, o=m["oid"]: arcgis_page(session, u, w, off, n, o),
                        min(m["maxRecordCount"], ARCGIS_PAGE_CAP), a.page_limit, pace, claimed, where)
    if "fairfax" in juris:
        m = arcgis_meta(session, FAIRFAX_URL)
        style = state.get("literal_style", {}).get("fairfax") or \
            pick_literal_style(session, FAIRFAX_URL, fairfax_where, start_iso)[0]
        state.setdefault("literal_style", {})["fairfax"] = style
        where = fairfax_where(start_iso, style)
        claimed = arcgis_count(session, FAIRFAX_URL, where)
        fetch_pages("fairfax", out_dir, state,
                    lambda off, n: arcgis_page(session, FAIRFAX_URL, where, off, n, m["oid"]),
                    min(m["maxRecordCount"], ARCGIS_PAGE_CAP), a.page_limit, pace, claimed, where)
    if "montgomery" in juris:
        where = socrata_where(start_iso, end_iso)
        datasets = {**MOCO_DATASETS, **(MOCO_TRADES if a.moco_trades else {})}
        for key, ds in datasets.items():
            fetch_pages(key, out_dir, state,
                        lambda off, n, d=ds: (socrata_page(session, d, where, off, n), True),
                        SOCRATA_PAGE, a.page_limit, max(pace, 1.0), None, where)


# ── analyze ────────────────────────────────────────────────────────────────

def analyze(out_dir: Path, state: dict, a, doctor_info: dict | None) -> dict:
    cache = json.loads(ZIP_CACHE.read_text(encoding="utf-8")) if ZIP_CACHE.exists() else {}
    today = datetime.now()
    rows: list[dict] = []
    dc_rows: list[dict] = []
    bbl_rows = _read_jsonl(out_dir / "raw" / "dc_bbl.jsonl")
    for key, juris in SOURCE_JURIS.items():
        raw = _read_jsonl(out_dir / "raw" / f"{key}.jsonl")
        if not raw:
            continue
        fn = NORMALISERS[juris]
        normed = [fn(r, key, cache) for r in raw]
        (dc_rows if juris == "dc" else rows).extend(normed)
        say(f"{key}: {len(raw)} raw rows normalised")
    dc_rows, overlap = dedupe_dc(dc_rows)
    rows = dc_rows + rows
    zip_unknown = 0
    for r in rows:
        if not r["county"]:
            r["county"] = zip_county(r["zip"], cache)
            zip_unknown += not r["county"]
    clean = Counter()
    for r in dc_rows:
        raw = r["applicant_raw"]
        if raw:
            clean["with_applicant"] += 1
            if re.search(r"\b(NA NA|NULL NULL)\b", raw.upper()):
                clean["junk_prefix"] += 1
            if "(" in raw:
                clean["parenthetical"] += 1
            if r["applicant_alt"]:
                clean["multi_part"] += 1
            if not r["applicant_key"]:
                clean["all_junk"] += 1

    bbl_index = build_bbl_index(bbl_rows) if bbl_rows else None
    registry = load_registry(Path(a.registry)) if a.registry else None
    applicants = repeat_applicants(dc_rows, a.min_permits, bbl_index)
    owners = repeat_owners(dc_rows, a.min_permits, registry)
    st_rows = stalled(rows, a.stale_months, today)
    zips = zip_heat(rows, st_rows, owners)
    roster = license_roster(bbl_rows, applicants, today.strftime("%Y-%m-%d"))
    seed = vendor_seed(roster)

    write_csv(out_dir / "permits_all.csv", rows, PERMIT_COLS)
    write_csv(out_dir / "dc_repeat_applicants.csv", applicants, APPLICANT_COLS)
    write_csv(out_dir / "repeat_owners.csv", owners, OWNER_COLS)
    write_csv(out_dir / "stalled_permits.csv", st_rows, STALLED_COLS)
    write_csv(out_dir / "zip_heat.csv", zips, ZIP_COLS)
    write_csv(out_dir / "dc_contractor_licenses.csv", roster, ROSTER_COLS)
    write_csv(out_dir / "vendor_directory_seed.csv", seed, VENDOR_FIELDS)

    juris_rows = []
    for j in ("District of Columbia", "Montgomery", "Fairfax"):
        rs = [r for r in rows if r["jurisdiction"] == j]
        if not rs:
            continue
        juris_rows.append({
            "jurisdiction": j, "permits": len(rs),
            "with_applicant": sum(1 for r in rs if r["applicant_key"]),
            "with_owner": sum(1 for r in rs if r["owner_key"]),
            "construction": sum(1 for r in rs if r["bucket"] in INVESTOR_BUCKETS),
            "stalled": sum(1 for r in st_rows if r["jurisdiction"] == j and r["stall_class"] == "stalled"),
            "expired_withdrawn": sum(1 for r in st_rows if r["jurisdiction"] == j and r["stall_class"] != "stalled"),
            "repeat_applicants": len(applicants) if j == "District of Columbia" else "n/a (no names)",
            "repeat_owners": len(owners) if j == "District of Columbia" else "n/a (no names)",
        })
    unmapped = Counter((r["jurisdiction"], r["permit_subtype"]) for r in rows
                       if r["bucket"] == "other" and r["permit_subtype"])
    summary = {
        "at": today.strftime("%Y-%m-%d %H:%M"), "months": a.months, "window": state["window"],
        "min_permits": a.min_permits, "stale_months": a.stale_months,
        "source_rows": [{"source": k, "ok": v.get("ok"), "rows": v.get("n"), "pages": v.get("pages"),
                         "count_claimed": v.get("count_claimed") if v.get("count_claimed") is not None else "",
                         "error": v.get("error") or ""}
                        for k, v in state["sources"].items()],
        "dc_overlap": overlap, "zip_unknown": zip_unknown, "clean_stats": dict(clean),
        "dc_no_issue": sum(1 for r in dc_rows if not r["issued_date"]),
        "juris_rows": juris_rows, "n_seed": len(seed), "n_roster": len(roster),
        "n_applicants": len(applicants),
        "n_expediters": sum(1 for x in applicants if x["expediter_flag"] == "likely"),
        "n_owners": len(owners), "registry_n": registry["n"] if registry else 0,
        "registry_hits": sum(1 for o in owners if o["registry_hit"] != "none"),
        "registry_new": sum(1 for o in owners if o["registry_hit"] == "none"),
        "n_stalled": sum(1 for r in st_rows if r["stall_class"] == "stalled"),
        "n_expired": sum(1 for r in st_rows if r["stall_class"] != "stalled"),
        "applicants": applicants, "owners": owners, "zips": zips,
        "unmapped": [{"jurisdiction": j, "permit_subtype": s, "n": n}
                     for (j, s), n in unmapped.most_common(20)],
        "doctor": doctor_info,
    }
    write_evaluation(out_dir, summary)
    return summary


# ── main ───────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--jurisdictions", default="dc,montgomery,fairfax")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--out", help="output dir (default output/permits/<YYYYMMDD>)")
    ap.add_argument("--resume", help="existing output dir: continue unfinished sources")
    ap.add_argument("--analyze-only", action="store_true", help="no HTTP; rebuild CSVs from raw/")
    ap.add_argument("--doctor", action="store_true", help="counts, fields, groupBys; no row pages")
    ap.add_argument("--page-limit", type=int, default=0, help="pages per source (0 = all)")
    ap.add_argument("--stale-months", type=int, default=6)
    ap.add_argument("--min-permits", type=int, default=2)
    ap.add_argument("--pace", type=float, default=0.5, help="seconds between pages")
    ap.add_argument("--registry", default=str(REGISTRY_DEFAULT),
                    help="dispo_buyers registry.json for the owner join ('' to skip)")
    ap.add_argument("--moco-trades", action="store_true",
                    help="also pull Montgomery electrical + mechanical permits")
    a = ap.parse_args(argv)

    juris = [j.strip().lower() for j in a.jurisdictions.split(",") if j.strip()]
    bad = [j for j in juris if j not in JURIS]
    if bad:
        ap.error(f"unknown jurisdiction(s) {bad}; choose from {list(JURIS)}")

    out_dir = Path(a.resume or a.out or (ROOT / "output" / "permits" / datetime.now().strftime("%Y%m%d")))
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "raw" / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        say(f"resuming {out_dir} (window {state['window'][0]}..{state['window'][1]})")
    else:
        state = {"at": datetime.now().isoformat(timespec="seconds"), "months": a.months,
                 "window": list(window(a.months)), "sources": {}}
    doctor_path = out_dir / "raw" / "doctor.json"
    doctor_info = json.loads(doctor_path.read_text(encoding="utf-8")) if doctor_path.exists() else None

    if not a.analyze_only:
        session = requests.Session()
        if a.doctor:
            doctor(session, out_dir, juris, state["window"][0])
            return 0
        _save_state(out_dir, state)
        pull(session, out_dir, state, juris, a)

    summary = analyze(out_dir, state, a, doctor_info)
    failed = [k for k, v in state["sources"].items() if not v.get("ok")]
    say(f"DONE: {sum(len(v) for v in (summary['juris_rows'],))} jurisdictions, "
        f"{summary['n_applicants']} repeat applicants, {summary['n_owners']} repeat owners, "
        f"{summary['n_stalled']} stalled, {summary['n_seed']} vendor seeds; "
        f"{len(failed)} source(s) incomplete {failed if failed else ''}")
    return 3 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
