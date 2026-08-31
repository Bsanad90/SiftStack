"""Phase 6: the first-to-market coverage matrix -- what we can feed, and what we cannot.

The FTM niche is only as good as its feed, and the doors-per-deal workbooks are explicit
that the paid provider cannot supply it here: all 14 jurisdictions lack tax-sale coverage,
8 of 14 lack foreclosure notices, and NONE of the 14 carry eviction, code violation or
divorce in SiftMap at all. So county-direct sourcing is not a supplement in these markets,
it is the only way in.

(8, not the 12 stated elsewhere in this project's notes. Counted from the workbooks' own
gap paragraphs: Anne Arundel, Baltimore, Calvert, Charles, Fairfax, Montgomery, Prince
William, Spotsylvania name Foreclosure notices; Arlington, Carroll, DC, Frederick,
Fredericksburg City and Stafford do not. Four counties -- Carroll, DC, Frederick and
Fredericksburg City -- list Tax sale as their only gap, not two.)

This joins three things that already exist and have never been put side by side:

    data/ftm_sources_mddcva.csv          139 researched county offices (Phase 3 extract)
    data/dpd_siftmap_coverage_gaps.json  what the provider does NOT cover, per county
    the scrapers' own live constants     what we ACTUALLY reach today

and answers one question per (jurisdiction x data type) cell: is this covered by the
provider, covered by a scraper we already run, reachable with an existing scraper we have
simply never pointed at it, or genuinely unsourced.

    python src/scripts/dpd_ftm_registry.py            # the matrix and the build plan
    python src/scripts/dpd_ftm_registry.py --csv      # + data/dpd_ftm_coverage.csv

Writes data/dpd_ftm_registry.json. Read-only: no browser, no account, no network.

**Coverage is read from the scrapers' own module constants, never from prose.** A county
list in a docstring drifts from the dict the code actually iterates, and a scraper pointed
at the wrong county scrapes nothing while looking exactly like a quiet day -- which is the
failure this codebase keeps rediscovering.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dpd.jurisdictions import JURISDICTIONS, resolve  # noqa: E402

SOURCES = ROOT / "data" / "ftm_sources_mddcva.csv"
GAPS = ROOT / "data" / "dpd_siftmap_coverage_gaps.json"
OUT = ROOT / "data" / "dpd_ftm_registry.json"
OUT_CSV = ROOT / "data" / "dpd_ftm_coverage.csv"
ENV = ROOT / ".env"

# What each credential unlocks in THIS matrix. Presence is checked, never the value, and
# the value is never printed.
CREDENTIALS = {
    "CAPTCHA_API_KEY": "va_trustee_sale_pull --full-text (the Turnstile gate on VA detail "
                       "pages). Everything else in the VA pull works without it.",
    "FIRECRAWL_API_KEY": "the MDDC and VA grid pulls, and the MD Register of Wills pull.",
    "MDDC_EMAIL": "mddc_trustee_sale_pull login (the pull hard-fails without it).",
    "MDDC_PASSWORD": "mddc_trustee_sale_pull login (the pull hard-fails without it).",
    "SCRAPFLY_KEY": "gated detail pages: MDDC Details.aspx, VA --full-text.",
    "MD_LANDREC_EMAIL": "md_land_records_lookup deed search (free account).",
    "MD_LANDREC_PASSWORD": "md_land_records_lookup deed search (free account).",
    "DATASIFT_EMAIL": "every browser pass against the account.",
    "DATASIFT_PASSWORD": "every browser pass against the account.",
}

# Signing up for these is what unblocks the cells the matrix cannot fill today.
SIGNUPS = [
    ("2Captcha", "CAPTCHA_API_KEY", "unblocks VA --full-text, which is where the auction "
     "date and loan principal live; the grid snippet truncates before them"),
    ("Maryland Judiciary Case Search", None, "the MD judicial-foreclosure signal (lis "
     "pendens / final judgment) that the 7 MD counties need and no pull reaches"),
    ("DC washington.dc.publicsearch.us", None, "DC recorder lien and deed index"),
    ("per-county Accela / SeeClickFix portals", None, "code violations and condemnations, "
     "the two types with a researched office in all 14 and no Easy rating anywhere"),
]


def _credential_status() -> list[dict]:
    """Presence only. Never reads a value into the output."""
    import re as _re
    placeholder = _re.compile(r"your_|_here|xxx+|changeme|placeholder", _re.I)
    vals = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip("\"").strip("'")
    out = []
    for k, unlocks in CREDENTIALS.items():
        v = vals.get(k)
        if v is None:
            st = "absent"
        elif not v:
            st = "empty"
        elif placeholder.search(v):
            # The exact state CAPTCHA_API_KEY has been in the whole time: present, so a
            # naive `if os.getenv(...)` passes, and rejected the moment it is used.
            st = "placeholder"
        else:
            st = "set"
        out.append({"key": k, "status": st, "unlocks": unlocks})
    return out

# The data types the playbook actually markets on, in the order it works them. The
# workbooks name them 19 different ways (per-city variants, two spellings of the
# foreclosure row), so every raw label is folded onto one of these.
CANON = [
    "probate",
    "foreclosure",
    "tax_sale",
    "tax_delinquent",
    "code_violation",
    "condemnation",
    "eviction",
    "lien_deed_index",
    "divorce",
]

CANON_LABEL = {
    "probate": "Probate / estates / unknown heirs",
    "foreclosure": "Foreclosure (first public signal)",
    "tax_sale": "Tax sale / delinquent tax auction",
    "tax_delinquent": "Tax delinquency roll (pre-sale)",
    "code_violation": "Code violations",
    "condemnation": "Condemned / unsafe / demolition",
    "eviction": "Evictions (multiple-eviction landlords)",
    "lien_deed_index": "Recorder lien & deed index (incl. lis pendens)",
    "divorce": "Divorce filings",
}


def canon_type(raw: str) -> str | None:
    """Fold a workbook data_type label onto a canonical type.

    Deliberately ordered: 'City Tax Delinquency' and 'County Tax Delinquency (pre-sale
    roll)' are both tax_delinquent, but 'Tax Sale / Delinquent Tax Auction' is tax_sale
    even though it also contains the word 'Delinquent' -- so the sale test runs first.
    """
    t = (raw or "").lower()
    if "tax sale" in t or "tax auction" in t:
        return "tax_sale"
    if "tax delinquen" in t:
        return "tax_delinquent"
    if "probate" in t or "estate" in t or "unknown heirs" in t:
        return "probate"
    if "foreclosure" in t:
        return "foreclosure"
    if "condemn" in t or "unsafe" in t or "demolition" in t:
        return "condemnation"
    if "code violation" in t:
        return "code_violation"
    if "eviction" in t:
        return "eviction"
    if "recorder" in t or "register of deeds" in t or "lien" in t:
        return "lien_deed_index"
    if "divorce" in t:
        return "divorce"
    return None


# The workbooks use two different difficulty vocabularies -- the MD books say
# Easy/Medium/Hard while the VA book also uses Moderate/High. Left as-is they sort as five
# distinct levels and an ordering by difficulty is silently wrong.
ACCESS_CANON = {"easy": "Easy", "medium": "Medium", "moderate": "Medium",
                "hard": "Hard", "high": "Hard", "": "Unrated"}
ACCESS_RANK = {"Easy": 0, "Medium": 1, "Hard": 2, "Unrated": 3}


# Derived from the gap paragraphs themselves rather than hardcoded, so a workbook that
# starts assessing a new list type is picked up instead of being silently treated as
# unjudged forever.
def _assessed_types(gaps: dict) -> set[str]:
    seen = set()
    for g in gaps.values():
        for label in (g.get("siftmap_low_or_zero") or []) + (g.get("siftmap_absent") or []):
            ct = canon_type(label)
            if ct:
                seen.add(ct)
    return seen


ASSESSED_TYPES: set[str] = set()


# Sources that feed a type from outside this repo. Recording them beats reporting a type as
# unsourced when a pipeline for it already runs on this machine.
EXTERNAL_FEEDS = {
    "divorce": {
        "name": "MD Courts daily case PDF pull",
        "where": "Windows Task Scheduler job 'MD Courts Daily PDF Pull' (outside this repo)",
        "script": "Galal Development/automation/daily_mdcourts_pull.ps1",
        "covers_states": ["MD"],
        "note": ("downloads mdcourts.gov/data/case/fileYYYY-MM-DD.pdf daily at 09:00 ET and "
                 "launches the shared case watcher. Statewide MD, so it reaches all 7 MD "
                 "counties; it does NOT cover DC or VA. Its task exit code is 0 even when "
                 "nothing downloaded -- ground truth is whether the day's PDF exists."),
    },
}


def _live_coverage() -> dict:
    """What the scrapers reach TODAY, read off their own module constants."""
    cov: dict = {"scrapers": {}, "errors": []}

    def grab(mod: str, names: list[str]) -> dict:
        try:
            m = importlib.import_module(mod)
        except Exception as e:  # noqa: BLE001
            cov["errors"].append(f"{mod}: {e}")
            return {}
        return {n: getattr(m, n, None) for n in names}

    mddc = grab("mddc_trustee_sale_pull", ["DEFAULT_COUNTIES", "COUNTY_CHECKBOX_INDEX",
                                           "SAVED_SEARCH_LABELS"])
    va = grab("va_trustee_sale_pull", ["COUNTY_CHECKBOX_INDEX", "POPULAR_SEARCHES"])
    row = grab("md_register_of_wills_pull", ["DEFAULT_COUNTIES", "COUNTY_ID"])

    cov["scrapers"]["mddc_trustee_sale_pull"] = {
        "site": "mddcpublicnotices.com",
        "types": ["foreclosure"],
        "runs_today": sorted(mddc.get("DEFAULT_COUNTIES") or []),
        "reachable": sorted((mddc.get("COUNTY_CHECKBOX_INDEX") or {}).keys()),
        "note": "no Virginia checkbox exists on this site at all",
    }
    # Foreclosures is the trustee-sale equivalent; Estate Claims and Tax Deeds are built,
    # free, and have never been run -- that is the cheapest coverage in this whole matrix.
    pop = va.get("POPULAR_SEARCHES") or {}
    cov["scrapers"]["va_trustee_sale_pull"] = {
        "site": "publicnoticevirginia.com (public Quick Search, no login)",
        "types": ["foreclosure", "probate", "tax_sale"],
        "type_map": {"foreclosure": "--popular-search 4 (Foreclosures)",
                     "probate": "--popular-search 6 (Estate Claims)",
                     "tax_sale": "--popular-search 8 (Tax Deeds)"},
        "runs_today": sorted((va.get("COUNTY_CHECKBOX_INDEX") or {}).keys()),
        "reachable": sorted((va.get("COUNTY_CHECKBOX_INDEX") or {}).keys()),
        "categories_available": pop,
        "note": ("only Foreclosures has actually been run; Estate Claims and Tax Deeds are "
                 "existing code needing new runs, not new scrapers"),
    }
    cov["scrapers"]["md_register_of_wills_pull"] = {
        "site": "registers.maryland.gov",
        "types": ["probate"],
        "runs_today": sorted(row.get("DEFAULT_COUNTIES") or []),
        "reachable": sorted((row.get("COUNTY_ID") or {}).keys()),
        "note": "Maryland only; DC probate is a separate court system with no scraper",
    }
    return cov


# Which scraper, if any, reaches a given (jurisdiction, canonical type). Keyed on the
# jurisdiction registry rather than on name strings, because the four pipelines spell the
# same place four different ways.
def _scraper_for(j, ctype: str, cov: dict) -> dict | None:
    mddc = cov["scrapers"]["mddc_trustee_sale_pull"]
    va = cov["scrapers"]["va_trustee_sale_pull"]
    row = cov["scrapers"]["md_register_of_wills_pull"]

    if ctype == "foreclosure":
        if j.mddc_checkbox is not None:
            nm = next((n for n, i in _mddc_index().items() if i == j.mddc_checkbox), None)
            ran = nm in mddc["runs_today"]
            return {"script": "mddc_trustee_sale_pull.py", "status":
                    "running" if ran else "reachable_not_running", "county_arg": nm}
        if j.va_checkbox is not None:
            return {"script": "va_trustee_sale_pull.py --popular-search 4",
                    "status": "running", "county_arg": _va_name(j)}
        return None

    if ctype == "probate":
        if j.row_county_id is not None:
            nm = _row_name(j)
            ran = nm in row["runs_today"]
            return {"script": "md_register_of_wills_pull.py", "status":
                    "running" if ran else "reachable_not_running", "county_arg": nm}
        if j.va_checkbox is not None:
            return {"script": "va_trustee_sale_pull.py --popular-search 6",
                    "status": "built_never_run", "county_arg": _va_name(j)}
        return None

    if ctype == "tax_sale" and j.va_checkbox is not None:
        return {"script": "va_trustee_sale_pull.py --popular-search 8",
                "status": "built_never_run", "county_arg": _va_name(j)}
    return None


def _mddc_index() -> dict:
    try:
        m = importlib.import_module("mddc_trustee_sale_pull")
        return dict(getattr(m, "COUNTY_CHECKBOX_INDEX", {}) or {})
    except Exception:  # noqa: BLE001
        return {}


def _va_name(j) -> str | None:
    try:
        m = importlib.import_module("va_trustee_sale_pull")
        idx = dict(getattr(m, "COUNTY_CHECKBOX_INDEX", {}) or {})
    except Exception:  # noqa: BLE001
        return None
    return next((n for n, i in idx.items() if i == j.va_checkbox), None)


def _row_name(j) -> str | None:
    try:
        m = importlib.import_module("md_register_of_wills_pull")
        idx = dict(getattr(m, "COUNTY_ID", {}) or {})
    except Exception:  # noqa: BLE001
        return None
    return next((n for n, i in idx.items() if i == j.row_county_id), None)


def _lead_field(offices: list[dict], field: str) -> str | None:
    """The `field` value from the lead office row for a cell: Priority A before B
    before C, then Easy before Hard. None when no row carries the field."""
    ranked = sorted(offices, key=lambda o: ((o.get("priority") or "zz")[:12],
                                            ACCESS_RANK.get(o.get("access"), 9)))
    for o in ranked:
        v = (o.get(field) or "").strip()
        if v:
            return v
    return None


def build() -> dict:
    if not SOURCES.exists():
        raise SystemExit(f"missing {SOURCES} - run dpd_workbook_extract.py first")

    rows = list(csv.DictReader(SOURCES.open(encoding="utf-8")))
    gaps = {g["jurisdiction_key"]: g for g in json.loads(GAPS.read_text(encoding="utf-8"))} \
        if GAPS.exists() else {}
    cov = _live_coverage()

    global ASSESSED_TYPES
    ASSESSED_TYPES = _assessed_types(gaps)

    # --- the registry: 139 offices, keyed and canonicalised -------------------------
    registry: dict[str, list[dict]] = {}
    unmapped: list[dict] = []
    for r in rows:
        j = resolve(r.get("jurisdiction") or "") or resolve(r.get("county_raw") or "")
        ct = canon_type(r.get("data_type", ""))
        if j is None or ct is None:
            unmapped.append({"jurisdiction": r.get("jurisdiction"),
                             "data_type": r.get("data_type"),
                             "why": "no jurisdiction match" if j is None
                                    else "no canonical data type"})
            continue
        registry.setdefault(j.key, []).append({
            "data_type": ct,
            "raw_data_type": r.get("data_type"),
            "priority": r.get("priority"),
            "office": r.get("office"),
            "phone": r.get("phone"),
            "url": r.get("source_url"),
            "address": r.get("office_address"),
            "foia": r.get("records_foia"),
            "access": ACCESS_CANON.get((r.get("access") or "").strip().lower(), "Unrated"),
            "access_raw": r.get("access"),
            # Recovered from the workbooks 2026-08-28 (the extractor had dropped them).
            "updates": (r.get("updates") or "").strip(),
            "verified": (r.get("verified") or "").strip(),
            "notes": (r.get("notes") or "").strip(),
        })

    # --- the matrix: one verdict per (jurisdiction x type) ---------------------------
    matrix: dict[str, dict] = {}
    for j in JURISDICTIONS:
        g = gaps.get(j.key, {})
        low = set(g.get("siftmap_low_or_zero") or [])
        absent = set(g.get("siftmap_absent") or [])
        offices = registry.get(j.key, [])
        cells = {}
        for ct in CANON:
            # What the provider gives us. The workbooks phrase these as list names, so map
            # them onto the canonical types rather than string-matching per cell.
            #
            # ASSESSED_TYPES matters more than it looks. The gap paragraphs only ever name
            # seven list types, so condemnation and the recorder lien/deed index are never
            # judged either way. Defaulting an unjudged type to "covered" invents provider
            # coverage out of the workbook's silence -- and it did, marking condemnation as
            # provider-covered in all 14 jurisdictions on the first run. Silence is
            # "not_assessed", which routes the cell to its researched county office instead.
            prov = "covered" if ct in ASSESSED_TYPES else "not_assessed"
            for label in low:
                if canon_type(label) == ct:
                    prov = "low_or_zero"
            for label in absent:
                if canon_type(label) == ct:
                    prov = "absent"

            sc = _scraper_for(j, ct, cov)
            county_offices = [o for o in offices if o["data_type"] == ct]
            ext = EXTERNAL_FEEDS.get(ct)
            if ext and j.state_abbr not in ext["covers_states"]:
                ext = None

            if sc and sc["status"] == "running":
                verdict = "covered_by_scraper"
            elif sc and sc["status"] in ("built_never_run", "reachable_not_running"):
                verdict = "existing_scraper_never_run"
            elif prov == "covered":
                verdict = "covered_by_provider"
            elif ext:
                verdict = "external_feed"
            elif county_offices:
                verdict = "researched_needs_new_scraper"
            else:
                verdict = "unsourced"

            cells[ct] = {
                "provider": prov,
                "scraper": sc,
                "external_feed": ext,
                "county_offices": len(county_offices),
                "easiest_access": min((o["access"] for o in county_offices),
                                      key=lambda a: ACCESS_RANK[a], default=None),
                # Publishing cadence + the workbook's own confidence flag, taken from
                # the highest-priority office row for this type (the county-level
                # row sorts ahead of the per-city rows).
                "cadence": _lead_field(county_offices, "updates"),
                "verified": _lead_field(county_offices, "verified"),
                "verdict": verdict,
            }
        matrix[j.key] = {"jurisdiction": j.name, "regime": j.foreclosure_regime,
                         "cells": cells}

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_rows": len(rows),
        "canonical_types": {k: CANON_LABEL[k] for k in CANON},
        "live_coverage": cov,
        "registry": registry,
        "matrix": matrix,
        "unmapped": unmapped,
    }


VERDICT_MARK = {
    "covered_by_scraper": "RUN",
    "existing_scraper_never_run": "FREE",
    "covered_by_provider": "prov",
    "external_feed": "ext",
    "researched_needs_new_scraper": "bld",
    "unsourced": "--",
}


def report(out: dict) -> int:
    print("\n=== FIRST-TO-MARKET COVERAGE MATRIX ===")
    print(f"generated {out['generated_at']}   {out['source_rows']} researched source rows\n")

    if out["live_coverage"]["errors"]:
        print("  COULD NOT READ A SCRAPER'S CONSTANTS (its coverage is understated below):")
        for e in out["live_coverage"]["errors"]:
            print(f"    {e}")
        print()

    print("  Legend: RUN = a scraper we run today   FREE = existing code, never pointed here")
    print("          prov = the provider covers it  bld = researched, needs a new scraper")
    print("          ext  = fed from outside this repo   --  = no source at all\n")
    unassessed = [k for k in CANON if k not in ASSESSED_TYPES]
    if unassessed:
        print("  The workbooks never judge these types either way, so their cells route to "
              "a county\n  office rather than being credited to the provider: "
              + ", ".join(CANON_LABEL[k] for k in unassessed) + "\n")

    hdr = "  " + f"{'Jurisdiction':24s}" + "".join(f"{k[:6]:>7s}" for k in CANON)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for key, m in out["matrix"].items():
        line = f"  {m['jurisdiction']:24s}"
        for ct in CANON:
            line += f"{VERDICT_MARK[m['cells'][ct]['verdict']]:>7s}"
        print(line)

    # Counts per verdict -- this is the honest headline about how much is actually fed.
    tally: dict[str, int] = {}
    for m in out["matrix"].values():
        for c in m["cells"].values():
            tally[c["verdict"]] = tally.get(c["verdict"], 0) + 1
    total = sum(tally.values())
    print(f"\n  {total} cells ({len(out['matrix'])} jurisdictions x {len(CANON)} types):")
    for v, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"    {n:3d}  {v}")

    # The build plan, cheapest first. "Cheapest" is not a judgement call here: an existing
    # script needing only a new run beats anything that needs code.
    print("\n  BUILD PLAN, cheapest first\n")
    free: dict[str, list[str]] = {}
    for key, m in out["matrix"].items():
        for ct, c in m["cells"].items():
            if c["verdict"] == "existing_scraper_never_run":
                free.setdefault(c["scraper"]["script"], []).append(m["jurisdiction"])
    print("  1. FREE - existing code, new runs only, no new scraper:")
    if free:
        for script, js in sorted(free.items()):
            print(f"       {script}")
            print(f"         {len(js)}: {', '.join(sorted(j.split(',')[0] for j in js))}")
    else:
        print("       (none)")

    need: dict[str, list[tuple[str, str]]] = {}
    for key, m in out["matrix"].items():
        for ct, c in m["cells"].items():
            if c["verdict"] == "researched_needs_new_scraper":
                need.setdefault(ct, []).append((m["jurisdiction"], c["easiest_access"]))
    print("\n  2. RESEARCHED - offices known, scraper needed (by type, easiest first):")
    order = sorted(need.items(),
                   key=lambda kv: (min(ACCESS_RANK.get(a or "Unrated", 3) for _, a in kv[1]),
                                   -len(kv[1])))
    for ct, js in order:
        easy = sum(1 for _, a in js if a == "Easy")
        print(f"       {CANON_LABEL[ct]:46s} {len(js):>2d} jurisdictions"
              f"  ({easy} rated Easy)")

    uns: dict[str, list[str]] = {}
    for key, m in out["matrix"].items():
        for ct, c in m["cells"].items():
            if c["verdict"] == "unsourced":
                uns.setdefault(ct, []).append(m["jurisdiction"])
    ext_cells: dict[str, list[str]] = {}
    for key, m in out["matrix"].items():
        for ct, c in m["cells"].items():
            if c["verdict"] == "external_feed":
                ext_cells.setdefault(c["external_feed"]["name"], []).append(m["jurisdiction"])
    if ext_cells:
        print("\n  2b. EXTERNAL - already fed from outside this repo, so verify it is alive "
              "rather than\n      building anything:")
        for nm, js in ext_cells.items():
            print(f"       {nm}")
            print(f"         {len(js)}: {', '.join(sorted(j.split(',')[0] for j in js))}")

    if uns:
        print("\n  3. UNSOURCED - no provider coverage and no researched office:")
        for ct, js in sorted(uns.items(), key=lambda kv: -len(kv[1])):
            print(f"       {CANON_LABEL[ct]:46s} {len(js):>2d}: "
                  f"{', '.join(sorted(j.split(',')[0] for j in js))}")

    print("\n  CREDENTIALS (presence only, values never read):")
    for c in out.get("credentials", []):
        mark = "ok " if c["status"] == "set" else c["status"].upper()
        print(f"    {c['key']:22s} {mark:12s} {c['unlocks'][:70]}")
    blocked = [c for c in out.get("credentials", []) if c["status"] != "set"]
    if blocked:
        print(f"\n    {len(blocked)} not usable. A placeholder is the worst of the three: "
              "it is present, so a naive\n    presence check passes, and it fails only at "
              "the point of use.")

    print("\n  SIGN-UPS that unblock cells this matrix cannot fill:")
    for s in out.get("signups", []):
        print(f"    {s['service']:42s} {s['unlocks'][:60]}")

    if out["unmapped"]:
        print(f"\n  {len(out['unmapped'])} source rows did not map to a jurisdiction x type:")
        for u in out["unmapped"][:10]:
            print(f"    {u['jurisdiction']} / {u['data_type']}  ({u['why']})")
    return 0


def write_csv(out: dict) -> None:
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["jurisdiction", "regime", "data_type", "provider", "verdict",
                    "scraper", "scraper_status", "county_offices", "easiest_access",
                    "cadence", "verified"])
        for key, m in out["matrix"].items():
            for ct in CANON:
                c = m["cells"][ct]
                sc = c["scraper"] or {}
                w.writerow([m["jurisdiction"], m["regime"], CANON_LABEL[ct], c["provider"],
                            c["verdict"], sc.get("script", ""), sc.get("status", ""),
                            c["county_offices"], c["easiest_access"] or "",
                            c.get("cadence") or "", c.get("verified") or ""])
    print(f"Wrote {OUT_CSV}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", action="store_true", help="also write the flat coverage CSV")
    a = ap.parse_args()

    out = build()
    out["credentials"] = _credential_status()
    out["signups"] = [{"service": s, "env_key": k, "unlocks": u} for s, k, u in SIGNUPS]
    rc = report(out)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote {OUT}")
    if a.csv:
        write_csv(out)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
