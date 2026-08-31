"""Build DC's geography layer by measuring SiftMap directly.

DC is the one jurisdiction Market Finder gives no sub-county data for -- with both state
and county chips applied the grid returns a single rollup row, pagination reads "1-1 of 1",
and the ZIP input is disabled. So Phase 1 produced no ZIPs and no neighbourhoods for DC,
which left it with no T1 narrowing layer and a "T2" of 213,296 that is really a volume tier.

But Market Finder is only the RANKING source. SiftMap's own geography filter works fine on
DC -- zip_codes and neighborhoods take JSON arrays like anywhere else. So the layer can be
built by measuring instead of by reading: probe every candidate ZIP and neighbourhood, keep
the ones that actually hold property, and rank them by volume.

    python src/scripts/dpd_dc_geography.py

Writes data/dpd_dc_geography.json and folds the result into data/dpd_zips.json and
data/dpd_neighborhoods.json under "District of Columbia", so dpd_tier_configs.py picks it
up with no further change. Read-only against the account; nothing is written to DataSift.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from datasift_core import DATASIFT_SIFTMAP_URL, create_browser, get_credentials, login  # noqa: E402
from dpd_siftmap_discover import _clean_map, _result_count, location_param  # noqa: E402

NAME = "District of Columbia"
OUT = ROOT / "data" / "dpd_dc_geography.json"
ZIPS = ROOT / "data" / "dpd_zips.json"
NBRS = ROOT / "data" / "dpd_neighborhoods.json"

# DC's residential ZIPs. The 202xx range also holds federal and PO-box-only ZIPs which
# carry no housing stock; those simply measure ~0 and drop out, so the list is deliberately
# generous rather than pre-judged.
DC_ZIPS = ["20001", "20002", "20003", "20004", "20005", "20006", "20007", "20008",
           "20009", "20010", "20011", "20012", "20015", "20016", "20017", "20018",
           "20019", "20020", "20024", "20032", "20036", "20037"]

# DC neighbourhood names as SiftMap is likely to spell them. Anything it does not know
# measures 0 and is dropped, which is itself the test -- there is no neighbourhood list to
# read anywhere, so the only way to learn the vocabulary is to ask for names and see which
# ones come back with property.
DC_NBRS = [
    "Capitol Hill", "Columbia Heights", "Petworth", "Anacostia", "Congress Heights",
    "Brookland", "Deanwood", "Shaw", "Trinidad", "Fort Dupont", "Michigan Park",
    "Takoma", "Brightwood", "Hillcrest", "Woodridge", "Eckington", "Bloomingdale",
    "Kingman Park", "Lamond Riggs", "Marshall Heights", "Georgetown", "Dupont Circle",
    "Adams Morgan", "Mount Pleasant", "Logan Circle", "Navy Yard", "Bellevue",
    "Barry Farm", "Benning", "Riggs Park",
]

# A candidate holding fewer than this is noise, not a market: a wrong neighbourhood name
# or a federal ZIP with no housing.
MIN_COUNT = 50


async def _probe(page, field: str, value: str, base: str) -> int | None:
    url = f"{base}&{field}={quote(json.dumps([value]))}"
    for attempt in (1, 2):
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(8000 if attempt == 1 else 13000)
        await _clean_map(page)
        c = await _result_count(page)
        if c is not None:
            return c
    return None


async def run(headless: bool) -> dict:
    email, password = get_credentials()
    base = f"{DATASIFT_SIFTMAP_URL}?location={quote(location_param(NAME))}"
    out = {"measured_at": datetime.now().isoformat(timespec="seconds"),
           "jurisdiction": NAME, "min_count": MIN_COUNT}

    async with create_browser(headless=headless) as (_b, _c, page):
        if not await login(page, email, password):
            out["error"] = "login failed"
            return out
        await page.goto(base, wait_until="domcontentloaded")
        await page.wait_for_timeout(9000)
        await _clean_map(page)
        baseline = await _result_count(page)
        out["baseline"] = baseline
        print(f"DC unfiltered: {baseline}")

        for field, values, key in (("zip_codes", DC_ZIPS, "zips"),
                                   ("neighborhoods", DC_NBRS, "neighborhoods")):
            rows = []
            for v in values:
                c = await _probe(page, field, v, base)
                # A candidate returning the FULL baseline means the filter did not apply,
                # not that this one ZIP holds the entire city.
                if c is not None and baseline and c == baseline:
                    print(f"  {field:14s} {v:20s} {c}  IGNORED (returned the baseline)")
                    rows.append({"value": v, "count": c, "status": "ignored"})
                    continue
                status = "ok" if (c or 0) >= MIN_COUNT else "empty"
                rows.append({"value": v, "count": c, "status": status})
                print(f"  {field:14s} {v:20s} {c}  {status}")
            rows.sort(key=lambda r: -(r["count"] or 0))
            out[key] = rows
    return out


def fold_in(out: dict) -> None:
    """Write DC's measured geography into the Phase 1 artifacts the tier builder reads."""
    zips = [r["value"] for r in out.get("zips", []) if r["status"] == "ok"]
    nbrs = [r["value"] for r in out.get("neighborhoods", []) if r["status"] == "ok"]

    if zips:
        doc = json.loads(ZIPS.read_text(encoding="utf-8"))
        doc["counties"][NAME] = {
            "fips": "11001", "status": "measured_from_siftmap",
            "selected": zips[:5], "ranked": zips, "all": out.get("zips"),
            "note": "Market Finder publishes no sub-county data for DC, so these are "
                    "ranked by live SiftMap property count rather than by the four "
                    "market checks the other 13 counties use. Volume only -- no supply, "
                    "DOM or price-band screening was possible.",
        }
        ZIPS.write_text(json.dumps(doc, indent=1), encoding="utf-8")

    if nbrs:
        doc = json.loads(NBRS.read_text(encoding="utf-8"))
        doc["counties"][NAME] = {
            "fips": "11001", "status": "measured_from_siftmap",
            "selected": nbrs[:5], "ranked": nbrs, "all": out.get("neighborhoods"),
            "note": "ranked by live SiftMap property count, not by the market checks; "
                    "names were probed rather than read, because no DC neighbourhood "
                    "list is published anywhere in the app.",
        }
        NBRS.write_text(json.dumps(doc, indent=1), encoding="utf-8")


def report(out: dict) -> int:
    print(f"\n=== DC GEOGRAPHY, MEASURED FROM SIFTMAP ===")
    if out.get("error"):
        print("  ERROR:", out["error"])
        return 1
    print(f"  baseline {out.get('baseline')}\n")
    for key, label in (("zips", "ZIP codes"), ("neighborhoods", "Neighborhoods")):
        rows = out.get(key) or []
        ok = [r for r in rows if r["status"] == "ok"]
        print(f"  {label}: {len(ok)} of {len(rows)} hold property (>= {out['min_count']})")
        for r in ok[:12]:
            print(f"    {r['value']:22s} {r['count']}")
        dead = [r["value"] for r in rows if r["status"] == "empty"]
        if dead:
            print(f"    not recognised / no stock: {', '.join(dead[:14])}"
                  + (" ..." if len(dead) > 14 else ""))
        print()
    if not [r for r in (out.get("zips") or []) if r["status"] == "ok"]:
        print("  No ZIP filtered. DC genuinely has no geography layer and its T2 stays a"
              "\n  volume tier -- but that is now a measured statement, not an assumption.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure DC's ZIP and neighbourhood layer")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--no-fold", action="store_true",
                    help="measure only; do not write into the Phase 1 geography artifacts")
    a = ap.parse_args()

    out = asyncio.run(run(headless=not a.headed))
    rc = report(out)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    if not a.no_fold and not out.get("error"):
        fold_in(out)
        print(f"Folded DC into {ZIPS.name} and {NBRS.name}")
    print(f"Wrote {OUT}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
