"""Fresh re-measure of the LIVE SiftMap saved filters (counts + not-in-account).

    python src/scripts/dpd_siftmap_fresh_measure.py
    python src/scripts/dpd_siftmap_fresh_measure.py --top5-only   # just the top-5 P1 rungs per county

Reads the live-filter census (`output/dpd_siftmap_presets_account_audit.json`) and the six
footprint manifests, then re-opens each live filter's stored `url` and `url_not_in` and
reads the "<N> Properties" figure -- the same read `dpd_siftmap_manifest.py --measure`
does, minus the proxy probes and the rows whose filters were deleted on 2026-09-07.

Ordering: the top-5 P1 rungs per county run FIRST (they are the pull candidates), then the
remaining P1, P2, and T2 rows, so a mid-run failure still leaves the decision numbers.

Guards carried over from --measure: a count equal to the county's fresh single-family
baseline means a parameter was silently ignored (flagged, not trusted); 0 is recorded as
measured_zero, never as an error; a non-rendering count is retried once then None.

Read-only: opens URLs and reads counts. Nothing is saved, added, or toggled.
Writes output/dpd_siftmap_fresh_measure_<ts>.json (old vs new counts, deltas).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from datasift_core import create_browser, get_credentials, login  # noqa: E402
from dpd_siftmap_discover import _clean_map, _result_count  # noqa: E402

FOOTPRINT = [("11001", "DC"), ("24031", "Montgomery"), ("51059", "Fairfax"),
             ("24021", "Frederick"), ("51013", "Arlington"), ("24003", "Anne Arundel")]
CENSUS = ROOT / "output" / "dpd_siftmap_presets_account_audit.json"
TOP_N = 5


def _band(p: dict) -> int:
    pr = str(p.get("priority") or "")
    return 0 if pr in ("1", "P1") else 1 if pr in ("2", "P2") else 2


def build_worklist(top5_only: bool) -> tuple[list[dict], dict[str, str]]:
    """Live manifest rows, top-5 P1 per county first. Returns (rows, baseline_urls)."""
    live_titles = [r.get("title") or "" for r in
                   json.loads(CENSUS.read_text(encoding="utf-8"))["results"]]
    rows: list[dict] = []
    baselines: dict[str, str] = {}
    for fips, county in FOOTPRINT:
        man = json.loads((ROOT / "data" / f"dpd_siftmap_manifest_{fips}.json")
                         .read_text(encoding="utf-8"))
        baselines[county] = man["baseline_url"]
        cand = list(man.get("presets", []))
        if man.get("tier2"):
            cand.append({**man["tier2"], "priority": "T2", "rank": 999})
        for p in cand:
            # census titles are the row's first line = the bare filter name
            if not any(t == p["name"] or t.startswith(p["name"]) for t in live_titles):
                continue
            rows.append({"county": county, "fips": fips, "name": p["name"],
                         "priority": str(p.get("priority")), "rank": p.get("rank"),
                         "lift": p.get("lift"), "dpd": p.get("dpd"),
                         "url": p["url"], "url_not_in": p["url_not_in"],
                         "old_count": p.get("measured_count"),
                         "old_not_in": p.get("measured_not_in_account"),
                         "band": _band(p)})
    # top-5 P1 per county, by rank
    top5: set[tuple[str, str]] = set()
    for _fips, county in FOOTPRINT:
        p1 = sorted([r for r in rows if r["county"] == county and r["band"] == 0],
                    key=lambda r: r["rank"] if r["rank"] is not None else 998)
        top5.update((county, r["name"]) for r in p1[:TOP_N])
    for r in rows:
        r["top5_p1"] = (r["county"], r["name"]) in top5
    if top5_only:
        rows = [r for r in rows if r["top5_p1"]]
    rows.sort(key=lambda r: (not r["top5_p1"], r["band"],
                             r["rank"] if r["rank"] is not None else 998))
    return rows, baselines


async def run(top5_only: bool) -> dict:
    rows, baselines = build_worklist(top5_only)
    out = {"at": datetime.now().isoformat(timespec="seconds"), "top5_only": top5_only,
           "baselines": {}, "results": rows}
    email, password = get_credentials()
    async with create_browser(headless=True) as (_b, _c, page):
        if not await login(page, email, password) or "/login" in page.url:
            out["error"] = "login failed; nothing measured"
            return out

        async def count(url: str):
            for attempt in (1, 2):
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(8000 if attempt == 1 else 14000)
                await _clean_map(page)
                c = await _result_count(page)
                if c is not None:
                    return c
            return None

        needed = {r["county"] for r in rows}
        for county, base_url in baselines.items():
            if county not in needed:
                continue
            b = await count(base_url)
            out["baselines"][county] = b
            print(f"  baseline {county}: {b}", flush=True)

        for i, r in enumerate(rows, 1):
            base = out["baselines"].get(r["county"])
            c = await count(r["url"])
            n = await count(r["url_not_in"])
            r["fresh_count"] = c
            r["fresh_not_in"] = n
            if c is None:
                r["status"] = "no_count"
            elif base is not None and c == base:
                r["status"] = "IGNORED_PARAM"  # unfiltered baseline -- do not trust
            elif c == 0:
                r["status"] = "measured_zero"
            else:
                r["status"] = "ok"
            dn = (n - r["old_not_in"]) if (n is not None and r["old_not_in"] is not None) else None
            tag = "T5P1" if r["top5_p1"] else "    "
            print(f"  [{i:2d}/{len(rows)}] {tag} {r['name'][:58]:58s} "
                  f"count {str(c):>7s}  not-in {str(n):>7s}"
                  f"  (was {str(r['old_not_in']):>7s}, d {dn if dn is not None else '?'})"
                  f"  {r['status']}", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--top5-only", action="store_true",
                    help="measure only the top-5 P1 rungs per county")
    a = ap.parse_args()
    out = asyncio.run(run(a.top5_only))
    path = ROOT / "output" / f"dpd_siftmap_fresh_measure_{datetime.now():%Y%m%dT%H%M%S}.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nwrote {path}")
    if out.get("error"):
        print("ERROR:", out["error"])
        return 1
    rows = out["results"]
    bad = [r for r in rows if r.get("status") in ("no_count", "IGNORED_PARAM")]
    print(f"fresh-measure: {len(rows)} filters, {len(bad)} not trustworthy "
          f"({[r['name'] for r in bad]})" if bad else
          f"fresh-measure: {len(rows)} filters, all readable")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
