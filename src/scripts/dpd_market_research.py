"""Phase 1 of the Doors-Per-Deal build: the Market Finder geography layer.

Two modes, deliberately separate so the slow browser work is never repeated
just to re-score:

    --extract   log in ONCE, walk all 14 jurisdictions, write one JSON per
                county to output/dpd_market_finder/. Resumable.
    --score     read those files and emit the three geography artifacts:
                data/dpd_zips.json                top 3-5 zips per county  (T2)
                data/dpd_neighborhoods.json       top 3-5 nbrs per county  (T1)
                data/dpd_dead_neighborhoods.json  suppression #4

Read-only against the account. Nothing here writes to DataSift.

Why this exists rather than a loop over extract_market_finder.py's CLI:
  * that CLI logs in per county (14 logins) and sys.exit(1)s on the first
    failure, so one bad county costs the other thirteen;
  * its county picker matches with :has-text(), which selects "Baltimore
    City" when asked for "Baltimore" and "Fairfax City" when asked for
    "Fairfax". Both are jurisdictions we deliberately exclude. Selection
    here is EXACT against the live option list, and reports the options it
    saw when no exact match exists.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datasift_core import create_browser, login, screenshot  # noqa: E402
from extract_market_finder import (  # noqa: E402
    NEIGHBORHOOD_COLUMNS,
    ZIP_COLUMNS,
    _dismiss_all_popups,
    _extract_all_table_rows,
    _navigate_to_market_finder,
    _row_to_dict,
    _switch_view,
)

logger = logging.getLogger("dpd_market_research")

RAW_DIR = Path("output/dpd_market_finder")
DATA_DIR = Path("data")

# The 14 target jurisdictions. `label` is matched EXACTLY (case-insensitive,
# trimmed) against the live Market Finder county dropdown.
JURISDICTIONS = [
    {"state": "Maryland", "label": "Baltimore", "name": "Baltimore County, MD", "fips": "24005"},
    {"state": "Maryland", "label": "Montgomery", "name": "Montgomery, MD", "fips": "24031"},
    {"state": "Maryland", "label": "Anne Arundel", "name": "Anne Arundel, MD", "fips": "24003"},
    {"state": "Maryland", "label": "Frederick", "name": "Frederick, MD", "fips": "24021"},
    {"state": "Maryland", "label": "Carroll", "name": "Carroll, MD", "fips": "24013"},
    {"state": "Maryland", "label": "Calvert", "name": "Calvert, MD", "fips": "24009"},
    {"state": "Maryland", "label": "Charles", "name": "Charles, MD", "fips": "24017"},
    {"state": "District of Columbia", "label": "District of Columbia",
     "name": "District of Columbia", "fips": "11001"},
    {"state": "Virginia", "label": "Fairfax", "name": "Fairfax, VA", "fips": "51059"},
    {"state": "Virginia", "label": "Prince William", "name": "Prince William, VA", "fips": "51153"},
    {"state": "Virginia", "label": "Arlington", "name": "Arlington, VA", "fips": "51013"},
    {"state": "Virginia", "label": "Stafford", "name": "Stafford, VA", "fips": "51179"},
    {"state": "Virginia", "label": "Spotsylvania", "name": "Spotsylvania, VA", "fips": "51177"},
    {"state": "Virginia", "label": "Fredericksburg City",
     "name": "Fredericksburg City, VA", "fips": "51630"},
]

# -- Scoring thresholds (the playbook's own rules) ---------------------
MAX_SUPPLY_MONTHS = 3.0     # "under 3 months of supply"
PRICE_BAND_LO_PCT = 20      # the 60% price range rule = the middle 60%
PRICE_BAND_HI_PCT = 80
COMPETITION_PCT = 90        # investor volume above this = heavy competition
TOP_N = 5                   # "top 3-5"
DEAD_MAX_INV_TRANS = 1      # dead neighbourhood = 1 investor deal or fewer


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text).strip("_")


# ======================================================================
# Extraction
# ======================================================================

async def _pick_exact(page, placeholder: str, wanted: str) -> tuple[bool, list[str]]:
    """Type into an InputMultiSearch and click the option matching EXACTLY.

    Returns (clicked, options_seen). A near-match is never accepted: on this
    site "Baltimore" and "Baltimore City" are different jurisdictions and
    :has-text() would happily take the wrong one.
    """
    box = page.locator('[placeholder="%s"]' % placeholder)
    if await box.count() == 0:
        box = page.locator('[placeholder*="%s" i]' % placeholder.split()[-1])
    if await box.count() == 0:
        return False, []

    inp = box.first
    if not await inp.is_enabled():
        return False, ["<input disabled>"]

    await inp.click(force=True)
    await page.wait_for_timeout(700)
    await inp.fill(wanted)
    await page.wait_for_timeout(1800)

    # The left rail (x < 200) is the app's main nav and matches [class*="Item"]
    # too; without the bound the "options" come back as SiftMap / Records / Tags.
    # Each real option renders as two lines, "Baltimore\nMD", so the match is
    # against the FIRST line only - and "Baltimore" vs "Baltimore City" are two
    # different jurisdictions sitting next to each other in that list.
    options = await page.evaluate(
        """() => [...document.querySelectorAll('[class*="Item"],[class*="Option"]')]
              .filter(e => {
                  const r = e.getBoundingClientRect();
                  return r.height > 0 && r.y > 100 && r.x > 200
                         && e.innerText.trim().length < 60;
              })
              .map(e => e.innerText.trim())
              .filter(Boolean)"""
    )
    seen, opts = set(), []
    for o in options:
        if o not in seen:
            seen.add(o)
            opts.append(o)

    target = wanted.strip().lower()
    if not any(o.split("\n")[0].strip().lower() == target for o in opts):
        return False, opts

    clicked = await page.evaluate(
        """(text) => {
            const els = [...document.querySelectorAll('[class*="Item"],[class*="Option"]')]
                .filter(e => {
                    const r = e.getBoundingClientRect();
                    return r.height > 0 && r.y > 100 && r.x > 200;
                });
            for (const e of els) {
                const first = e.innerText.trim().split('\\n')[0].trim().toLowerCase();
                if (first === text) { e.click(); return true; }
            }
            return false;
        }""",
        target,
    )
    return bool(clicked), opts


async def _grid_granularity(page, county_label: str) -> str:
    """Decide whether the grid drilled down or is still showing the rollup.

    A county-level rollup row reads "<County>, <ST>"; a real ZIP row reads
    "20817". Recording a rollup as data is how a one-row 'success' happens.
    """
    first = await page.evaluate(
        """() => {
            const r = document.querySelector('[class*="TableRow"]');
            if (!r) return null;
            const c = r.querySelector('[class*="TableCell"]');
            return c ? c.innerText.trim() : null;
        }"""
    )
    if not first:
        return "empty"
    if first.strip().lower().startswith(county_label.strip().lower() + ","):
        return "county_rollup"
    return "ok"


async def extract_one(page, juris: dict) -> dict:
    """Extract ZIP + neighbourhood tables for one jurisdiction."""
    state, label = juris["state"], juris["label"]
    out = {
        "jurisdiction": juris["name"],
        "state": state,
        "county_label": label,
        "fips": juris["fips"],
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "zip_data": [],
        "neighborhood_data": [],
        "granularity": "unknown",
        "status": "",
        "county_options_seen": [],
    }

    # A fresh page load resets the filter bar; selecting a second county on a
    # dirty bar would stack chips and silently pull two counties at once.
    await _navigate_to_market_finder(page)
    await _dismiss_all_popups(page)
    await page.wait_for_timeout(1500)

    ok, opts = await _pick_exact(page, "Select States", state)
    if not ok:
        out["status"] = "state '%s' not selectable; options seen: %s" % (state, opts[:15])
        return out
    await _dismiss_all_popups(page)
    await page.wait_for_timeout(2500)

    ok, opts = await _pick_exact(page, "Select Counties", label)
    out["county_options_seen"] = opts[:25]
    if not ok:
        out["status"] = "county '%s' has no EXACT option; options seen: %s" % (label, opts[:15])
        return out
    await _dismiss_all_popups(page)
    await page.wait_for_timeout(5000)

    gran = await _grid_granularity(page, label)
    out["granularity"] = gran
    if gran != "ok":
        out["status"] = (
            "grid shows %s after selecting the county - this jurisdiction has no "
            "sub-county breakdown in Market Finder" % gran
        )
        # Still capture the rollup row so the county-level metrics are not lost.
        rows = await _extract_all_table_rows(page)
        out["county_rollup"] = [_row_to_dict(r, ZIP_COLUMNS) for r in rows]
        return out

    await _switch_view(page, "ZIP Codes")
    await page.wait_for_timeout(2000)
    out["zip_data"] = [_row_to_dict(r, ZIP_COLUMNS)
                       for r in await _extract_all_table_rows(page)]

    if await _switch_view(page, "Neighborhoods"):
        await page.wait_for_timeout(2000)
        out["neighborhood_data"] = [_row_to_dict(r, NEIGHBORHOOD_COLUMNS)
                                    for r in await _extract_all_table_rows(page)]

    out["status"] = "ok"
    return out


async def run_extract(targets: list[dict], *, headless: bool, refresh: bool) -> list[dict]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    pending = []
    for j in targets:
        path = RAW_DIR / ("%s.json" % _slug(j["name"]))
        if path.exists() and not refresh:
            logger.info("SKIP %-28s already extracted (%s)", j["name"], path.name)
            results.append(json.loads(path.read_text(encoding="utf-8")))
        else:
            pending.append((j, path))

    if not pending:
        logger.info("Nothing to extract; all %d already on disk", len(targets))
        return results

    async with create_browser(headless=headless) as (_browser, _ctx, page):
        if not await login(page):
            logger.error("Login failed")
            sys.exit(2)
        logger.info("Logged in once for %d jurisdictions", len(pending))

        for juris, path in pending:
            logger.info("=== %s ===", juris["name"])
            try:
                res = await extract_one(page, juris)
            except Exception as exc:                       # one county must not
                logger.exception("extract failed for %s", juris["name"])
                try:
                    await screenshot(page, "dpd_mf_fail_%s" % _slug(juris["name"]))
                except Exception:
                    pass
                res = {                                     # cost the other 13
                    "jurisdiction": juris["name"], "state": juris["state"],
                    "county_label": juris["label"], "fips": juris["fips"],
                    "extracted_at": datetime.now().isoformat(timespec="seconds"),
                    "zip_data": [], "neighborhood_data": [],
                    "granularity": "error", "status": "exception: %s" % exc,
                }
            path.write_text(json.dumps(res, indent=2), encoding="utf-8")
            logger.info(
                "%-28s %-14s %3d zips %4d nbrs  %s",
                juris["name"], res["granularity"],
                len(res["zip_data"]), len(res["neighborhood_data"]), res["status"][:60],
            )
            results.append(res)

    return results


# ======================================================================
# Scoring
# ======================================================================

def _pct(values: list, p: int):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def _gate(row: dict, *, band_lo, band_hi, dom_baseline) -> dict:
    """Apply the playbook's four checks. Every one is reported, pass or fail."""
    hom = row.get("homes_on_market")
    sold = row.get("homes_sold_last_month")
    dom = row.get("median_days_on_market")
    mhv = row.get("median_home_value")
    msp = row.get("median_sale_price")

    supply = (hom / sold) if (hom is not None and sold) else None

    checks = {
        # under 3 months of supply
        "supply_under_3mo": bool(supply is not None and supply < MAX_SUPPLY_MONTHS),
        # not the darkest-zip trap: high volume with a slow market is a trap
        "dom_not_slow": bool(dom is not None and dom_baseline is not None
                             and dom <= dom_baseline),
        # median sale price below median home value = the opportunity signal
        "sale_below_value": bool(msp is not None and mhv is not None and msp <= mhv),
        # inside the middle 60% of county home values
        "in_price_band": bool(mhv is not None and band_lo is not None
                              and band_hi is not None and band_lo <= mhv <= band_hi),
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "supply_months": round(supply, 2) if supply is not None else None,
    }


def _is_real_market(r: dict) -> bool:
    """A row we can actually judge.

    Roughly half of a county's ZIP rows sold nothing at all last month (PO-box
    and non-residential ZIPs): months-of-supply is undefined for them, so they
    fail every supply test for a reason that has nothing to do with being a bad
    market. They are dropped up front and counted, rather than left in the
    denominator to make the gates look brutal.
    """
    return bool(r.get("homes_sold_last_month")) and r.get("median_home_value") is not None


def _score_table(rows: list, key: str) -> dict:
    """Score, rank and cut a ZIP or neighbourhood table for one county.

    The playbook's four metrics all decide the ORDER, but they are not ANDed
    into a pass/fail gate. Each check independently removes 45-80% of a county
    (measured across the 6 MD counties), so requiring all four returns 0-4 rows
    from 80 and empties a small county entirely - Calvert scored zero. The
    thresholds are also Knoxville-calibrated: this metro's median months of
    supply is 3.15, so the guide's "under 3 months" rule sits at the median
    here rather than picking out a tight market.

    So: rank by how many checks a row passes, then by investor volume. The
    strict all-four count is still reported, as `passed_all_four`.
    """
    present = [r for r in rows if r.get(key)]
    usable = [r for r in present if _is_real_market(r)]
    dropped_no_market = len(present) - len(usable)
    if not usable:
        return {"eligible": [], "selected": [], "all": [], "band": None,
                "dom_baseline": None, "competition_cutoff": None,
                "counts": {"total": len(present), "real_markets": 0,
                           "dropped_no_market": dropped_no_market,
                           "eligible": 0, "set_aside_contested": 0}}

    values = [r.get("median_home_value") for r in usable]
    doms = [r.get("median_days_on_market") for r in usable]
    vols = [r.get("total_inv_trans_6mo") for r in usable]

    band_lo, band_hi = _pct(values, PRICE_BAND_LO_PCT), _pct(values, PRICE_BAND_HI_PCT)
    dom_baseline = _pct(doms, 50)
    comp_cutoff = _pct(vols, COMPETITION_PCT)

    scored = []
    for r in usable:
        g = _gate(r, band_lo=band_lo, band_hi=band_hi, dom_baseline=dom_baseline)
        vol = r.get("total_inv_trans_6mo") or 0
        over = comp_cutoff is not None and vol > comp_cutoff
        scored.append({
            key: r.get(key),
            "inv_trans_6mo": r.get("total_inv_trans_6mo"),
            "homes_on_market": r.get("homes_on_market"),
            "homes_sold_last_month": r.get("homes_sold_last_month"),
            "median_days_on_market": r.get("median_days_on_market"),
            "median_home_value": r.get("median_home_value"),
            "median_sale_price": r.get("median_sale_price"),
            "supply_months": g["supply_months"],
            "checks": g["checks"],
            "checks_passed": sum(1 for v in g["checks"].values() if v),
            "passed_all_four": g["passed"],
            "over_competition_cutoff": bool(over),
        })

    strict = [s for s in scored if s["passed_all_four"]]
    # "balanced investor volume, not maximum" - the busiest zips are the most
    # contested, so they are set aside rather than ranked first.
    contested = [s for s in scored if s["over_competition_cutoff"]]
    pool = [s for s in scored if not s["over_competition_cutoff"]] or scored
    pool = sorted(pool, key=lambda s: (s["checks_passed"], s["inv_trans_6mo"] or 0),
                  reverse=True)

    return {
        "band": [band_lo, band_hi],
        "dom_baseline": dom_baseline,
        "competition_cutoff": comp_cutoff,
        "counts": {"total": len(present), "real_markets": len(usable),
                   "dropped_no_market": dropped_no_market,
                   "eligible": len(strict), "set_aside_contested": len(contested)},
        "selected": pool[:TOP_N],
        # The full RANKED pool, not just the top 5. Phase 3 sizes a T1 by narrowing to
        # neighbourhoods until the count lands in the 200-500 band, and with only 5 stored
        # that search has one step: four counties measured over band on the stack alone and
        # under band on the top-5 cut, with the band sitting in the gap between them.
        "ranked": pool,
        "eligible": strict,
        "all": scored,
    }


def run_score(files: list) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zips_out, nbrs_out, dead_out, review = {}, {}, {}, []

    for path in files:
        d = json.loads(path.read_text(encoding="utf-8"))
        name = d["jurisdiction"]

        if d.get("granularity") != "ok":
            reason = d.get("status") or ("granularity=%s" % d.get("granularity"))
            zips_out[name] = {"fips": d.get("fips"), "status": "no_data",
                              "reason": reason, "selected": []}
            nbrs_out[name] = {"fips": d.get("fips"), "status": "no_data",
                              "reason": reason, "selected": []}
            dead_out[name] = {"fips": d.get("fips"), "status": "no_data",
                              "reason": reason, "dead": []}
            review.append({"jurisdiction": name, "status": "no_data", "reason": reason})
            continue

        z = _score_table(d["zip_data"], "zip_code")
        n = _score_table(d["neighborhood_data"], "neighborhood")

        dead = [
            {"neighborhood": r.get("neighborhood"),
             "inv_trans_6mo": r.get("total_inv_trans_6mo")}
            for r in d["neighborhood_data"]
            if r.get("neighborhood")
            and (r.get("total_inv_trans_6mo") or 0) <= DEAD_MAX_INV_TRANS
        ]

        zips_out[name] = {"fips": d["fips"], "status": "ok",
                          "price_band_60pct": z["band"], "dom_baseline": z["dom_baseline"],
                          "competition_cutoff": z["competition_cutoff"],
                          "counts": z["counts"],
                          "selected": [s["zip_code"] for s in z["selected"]],
                          "selected_detail": z["selected"], "all": z["all"]}
        nbrs_out[name] = {"fips": d["fips"], "status": "ok",
                          "price_band_60pct": n["band"], "dom_baseline": n["dom_baseline"],
                          "competition_cutoff": n["competition_cutoff"],
                          "counts": n["counts"],
                          "selected": [s["neighborhood"] for s in n["selected"]],
                          "selected_detail": n["selected"],
                          "ranked": [s["neighborhood"] for s in n["ranked"]]}
        dead_out[name] = {"fips": d["fips"], "status": "ok",
                          "count": len(dead), "dead": dead}

        review.append({
            "jurisdiction": name, "status": "ok",
            "zips_total": z["counts"]["total"],
            "zips_real": z["counts"]["real_markets"],
            "zips_strict": z["counts"]["eligible"],
            "zips_selected": [s["zip_code"] for s in z["selected"]],
            "zips_selected_checks": [s["checks_passed"] for s in z["selected"]],
            "nbrs_total": n["counts"]["total"],
            "nbrs_real": n["counts"]["real_markets"],
            "nbrs_strict": n["counts"]["eligible"],
            "nbrs_selected": [s["neighborhood"] for s in n["selected"]],
            "dead_nbrs": len(dead),
            "price_band": z["band"],
        })

    stamp = {"generated_at": datetime.now().isoformat(timespec="seconds"),
             "rules": {"max_supply_months": MAX_SUPPLY_MONTHS,
                       "price_band_pct": [PRICE_BAND_LO_PCT, PRICE_BAND_HI_PCT],
                       "competition_pct": COMPETITION_PCT, "top_n": TOP_N,
                       "dead_max_inv_trans": DEAD_MAX_INV_TRANS}}

    (DATA_DIR / "dpd_zips.json").write_text(
        json.dumps({**stamp, "counties": zips_out}, indent=2), encoding="utf-8")
    (DATA_DIR / "dpd_neighborhoods.json").write_text(
        json.dumps({**stamp, "counties": nbrs_out}, indent=2), encoding="utf-8")
    (DATA_DIR / "dpd_dead_neighborhoods.json").write_text(
        json.dumps({**stamp, "counties": dead_out}, indent=2), encoding="utf-8")

    return {"review": review}


def print_review(review: list) -> None:
    print()
    print("zips/nbrs = rows returned : real markets (sold >0 last month) : passed all 4")
    header = "%-26s %-14s %-13s %5s  %s" % (
        "Jurisdiction", "zips", "nbrs", "dead", "selected zips (checks passed 0-4)")
    print(header)
    print("-" * len(header))
    for r in review:
        if r["status"] != "ok":
            print("%-26s %-14s %s" % (r["jurisdiction"], "NO DATA", r["reason"][:60]))
            continue
        zc = "%d:%d:%d" % (r["zips_total"], r["zips_real"], r["zips_strict"])
        nc = "%d:%d:%d" % (r["nbrs_total"], r["nbrs_real"], r["nbrs_strict"])
        sel = ", ".join("%s(%d)" % (z, c) for z, c
                        in zip(r["zips_selected"], r["zips_selected_checks"]))
        print("%-26s %-14s %-13s %5d  %s" % (
            r["jurisdiction"], zc, nc, r["dead_nbrs"], sel or "(no real markets)"))
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 1 - Market Finder geography layer")
    ap.add_argument("--extract", action="store_true", help="pull Market Finder for the targets")
    ap.add_argument("--score", action="store_true", help="score cached pulls into data/dpd_*.json")
    ap.add_argument("--county", help="comma-separated subset of jurisdiction labels")
    ap.add_argument("--refresh", action="store_true", help="re-extract counties already on disk")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("datasift_core").setLevel(logging.WARNING)
    logging.getLogger("extract_market_finder").setLevel(logging.WARNING)

    if not (args.extract or args.score):
        ap.error("pass --extract, --score, or both")

    targets = JURISDICTIONS
    if args.county:
        wanted = {c.strip().lower() for c in args.county.split(",")}
        targets = [j for j in JURISDICTIONS
                   if j["label"].lower() in wanted or j["name"].lower() in wanted]
        if not targets:
            ap.error("no jurisdiction matches %s" % sorted(wanted))

    if args.extract:
        asyncio.run(run_extract(targets, headless=not args.headed, refresh=args.refresh))

    if args.score:
        files = [RAW_DIR / ("%s.json" % _slug(j["name"])) for j in targets]
        files = [f for f in files if f.exists()]
        if not files:
            print("No extractions on disk. Run with --extract first.")
            sys.exit(1)
        out = run_score(files)
        print_review(out["review"])
        print("Wrote %s, %s, %s" % (DATA_DIR / "dpd_zips.json",
                                    DATA_DIR / "dpd_neighborhoods.json",
                                    DATA_DIR / "dpd_dead_neighborhoods.json"))


if __name__ == "__main__":
    main()
