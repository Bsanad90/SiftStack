"""Phase 3d: take the dead neighbourhoods out of the T1 geography, and put the free
exclusion to work on the T1s that have no include list.

Two defects in `data/dpd_tier_configs.json`, both in the T1 geography layer, both found
by checking a claim in CLAUDE.md that turned out to be false ("no dead neighbourhood
appears in a T1 include list anyway, since both come from the same Phase 1 ranking").
It holds at the top 5. It does not hold at the depths the search actually reached --
58 neighbourhoods in Anne Arundel, 48 in Baltimore, 44 in Fairfax -- because that is
precisely the ranked tail where a neighbourhood with one investor deal or fewer sits.

    A. CONTAMINATED INCLUDE. The chosen T1 includes neighbourhoods Phase 1 already
       classified dead. Fix: drop dead from the ranked pool FIRST, then re-run the
       depth search over the clean pool.

    B. FREE EXCLUSION UNUSED. A geography field takes one direction at a time, so a T1
       that includes its top neighbourhoods cannot also exclude the dead ones. But a T1
       with NO include list leaves that field completely free, and four counties were
       carrying no exclusion at all. Fix: add it.

Both change the count, so both are re-measured live. Nothing is applied on an unmeasured
or implausible number.

    python src/scripts/dpd_geo_fix.py            # dry run: classify + plan, no browser
    python src/scripts/dpd_geo_fix.py --measure  # measure the fixes, still writes nothing
    python src/scripts/dpd_geo_fix.py --commit   # measure and write

Read-only against the ACCOUNT in every mode: it opens SiftMap URLs and reads counts. It
never saves a preset, never adds a record, never touches a tag.

Edits the artifact IN PLACE. Do not "fix" this by re-running dpd_tier_configs.py -- its
build() starts from scratch and would discard both the fold and every live count.
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

from dpd_tier_configs import (  # noqa: E402
    BUY_BOX, GEO_FIELDS, OUT, SUPPRESSION, T1_HI, T1_LO, _geo, _search_depth, build_url,
)

DEAD = ROOT / "data" / "dpd_dead_neighborhoods.json"


def _dead_for(dead: dict, name: str) -> list[str]:
    """Dead-neighbourhood rows are dicts ({neighborhood, inv_trans_6mo}), not strings."""
    return [d["neighborhood"] for d in (dead.get(name, {}).get("dead") or [])
            if isinstance(d, dict) and d.get("neighborhood")]


def _stack_params(params: list[str]) -> list[str]:
    """The distressor stack alone: strip any geography fragment.

    A folded `widened` variant carries its neighbourhoods inside `params` as an
    already-encoded fragment, so the base stack has to be recovered rather than read.
    """
    return [p for p in params
            if not any(p.startswith(f"{f}=") or p.startswith(f"{f}_mode=")
                       for f in GEO_FIELDS)]


def classify(tc: dict, dead: dict) -> list[dict]:
    """One verdict per county, from the artifact alone. No browser."""
    plans = []
    for name, cfg in tc["counties"].items():
        t1 = cfg["T1"]
        ch = t1.get("chosen")
        var = (t1["variants"].get(ch) or {}) if ch else {}
        dn = _dead_for(dead, name)
        inc = [str(n) for n in (var.get("neighborhoods") or [])]
        ranked = [str(n) for n in (t1.get("ranked_neighborhoods") or [])]
        overlap = [n for n in inc if n in set(dn)]

        p = {"name": name, "chosen": ch, "count": var.get("measured_count"),
             "include": len(inc), "dead": len(dn), "dead_in_include": overlap,
             "ranked": len(ranked)}

        if var.get("geo_fix"):
            p["kind"] = "already_fixed"
            p["reason"] = f"carries a geo_fix stamp ({var['geo_fix']})"
        elif not dn:
            p["kind"] = "no_dead_list"
            p["reason"] = ("Market Finder publishes no sub-county data for this "
                           "jurisdiction, so it has no dead list to apply")
        elif overlap:
            clean = [n for n in ranked if n not in set(dn)]
            p["kind"] = "contaminated_include"
            p["clean_pool"] = len(clean)
            p["reason"] = (f"{len(overlap)} of the {len(inc)} included neighbourhoods are "
                           f"dead; re-depth-search over the {len(clean)} clean ranked")
        elif inc:
            p["kind"] = "clean_include"
            p["reason"] = ("the include list is already clean, and the field is spent on "
                           "include so no exclusion is possible")
        else:
            p["kind"] = "free_exclusion"
            p["reason"] = (f"no include list, so the neighbourhoods field is free; "
                           f"{len(dn)} dead neighbourhoods can be excluded")
        plans.append(p)
    return plans


async def apply_fixes(tc: dict, dead: dict, plans: list[dict], log=print) -> dict:
    """Measure every proposed fix live and record what actually happened."""
    from datasift_core import create_browser, get_credentials, login
    from dpd_siftmap_discover import _clean_map, _result_count

    todo = [p for p in plans if p["kind"] in ("contaminated_include", "free_exclusion")]
    results = {"measured_at": datetime.now().isoformat(timespec="seconds"),
               "applied": [], "rejected": [], "failed": []}
    if not todo:
        return results

    email, password = get_credentials()
    async with create_browser(headless=True) as (_b, _c, page):
        if not await login(page, email, password):
            results["error"] = "login failed; nothing measured, nothing changed"
            return results

        async def count(url: str):
            # One retry on a missing count. A map that has not finished rendering
            # reports nothing, and nothing was being written down as a real measurement
            # -- DC came back unmeasured on one pass and 63 on the next from the
            # identical URL.
            for attempt in (1, 2):
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(8000 if attempt == 1 else 14000)
                await _clean_map(page)
                c = await _result_count(page)
                if c is not None:
                    return c
            return None

        for p in todo:
            name = p["name"]
            cfg = tc["counties"][name]
            t1 = cfg["T1"]
            ch = t1["chosen"]
            var = t1["variants"][ch]
            base = _stack_params(var["params"])
            dn = _dead_for(dead, name)
            before = var.get("measured_count")
            log(f"\n  {name}  ({p['kind']}, was {before})")

            if p["kind"] == "free_exclusion":
                frags = base + _geo("neighborhoods", dn, mode="exclude")
                url = build_url(t1["_loc"], frags + BUY_BOX + SUPPRESSION)
                after = await count(url)
                log(f"      exclude {len(dn):>3} dead -> {after}")
                if after is None:
                    results["failed"].append({**p, "why": "no count rendered twice"})
                    continue
                if before is not None and after > before:
                    # Excluding geography can only ever remove records. A higher number
                    # means the page did not apply the filter, not a finding.
                    results["failed"].append({
                        **p, "after": after,
                        "why": f"excluding {len(dn)} neighbourhoods returned {after}, "
                               f"ABOVE the unfiltered {before}; impossible, so the filter "
                               "did not apply"})
                    continue
                if after < T1_LO:
                    # The band is a cadence-capacity rule. Suppression is a quality
                    # nicety. Do not spend a shippable list on it -- say what it costs
                    # and name the lever instead.
                    results["rejected"].append({
                        **p, "after": after,
                        "why": f"the exclusion drops this T1 from {before} to {after}, "
                               f"under the {T1_LO} floor. The dead geography is holding "
                               f"{(before or 0) - after} of its records, so the honest fix "
                               "is a broader stack (dpd_widen_search), not a smaller list."})
                    continue
                var["_pre_geo_fix"] = {"params": list(var["params"]),
                                       "url": var["url"],
                                       "measured_count": before}
                var["params"] = frags
                var["url"] = url
                var["measured_count"] = after
                var["neighborhoods_excluded"] = dn
                var["label"] = f"{var['label']}, minus {len(dn)} dead neighbourhoods"
                var["geo_fix"] = "free_exclusion"
                t1["sizing"]["count"] = after
                t1["sizing"]["geo_fix"] = (
                    f"suppression #4 applied: {len(dn)} dead neighbourhoods excluded, "
                    f"{before} -> {after}")
                results["applied"].append({**p, "after": after})
                continue

            # contaminated_include: rebuild the pool, then re-search the depth.
            ranked = [str(n) for n in (t1.get("ranked_neighborhoods") or [])]
            clean = [n for n in ranked if n not in set(dn)]
            if len(clean) < 2:
                results["failed"].append({**p,
                                          "why": "clean ranked pool too small to search"})
                continue
            res = await _search_depth(count, t1["_loc"], base, clean,
                                      log=lambda m: log("    " + m.rstrip()))
            p["depth_search"] = res
            if res.get("status") != "in_band":
                results["rejected"].append({
                    **p, "after": res.get("count"), "status": res.get("status"),
                    "why": res.get("reason", f"the depth search over the clean pool ended "
                                             f"{res.get('status')}, so no clean cut lands "
                                             "in the band")})
                continue
            k, after = res["depth"], res["count"]
            frags = base + _geo("neighborhoods", clean[:k])
            var["_pre_geo_fix"] = {"params": list(var["params"]),
                                   "neighborhoods": list(var.get("neighborhoods") or []),
                                   "url": var["url"], "measured_count": before}
            var["params"] = frags
            var["neighborhoods"] = clean[:k]
            var["url"] = build_url(t1["_loc"], frags + BUY_BOX + SUPPRESSION)
            var["measured_count"] = after
            var["label"] = f"stack + top {k} neighbourhoods (dead-free, depth-searched)"
            var["geo_fix"] = "contaminated_include"
            t1["clean_ranked_neighborhoods"] = clean
            t1["depth_search_clean"] = res
            t1["sizing"]["count"] = after
            t1["sizing"]["depth"] = k
            t1["sizing"]["geo_fix"] = (
                f"{len(p['dead_in_include'])} dead neighbourhoods removed from the pool; "
                f"re-depth-searched over {len(clean)} clean to depth {k}, "
                f"{before} -> {after}")
            results["applied"].append({**p, "after": after, "depth": k})
    return results


def report(plans: list[dict], results: dict | None) -> int:
    print("\n=== T1 GEOGRAPHY FIX ===\n")
    kinds: dict[str, list[dict]] = {}
    for p in plans:
        kinds.setdefault(p["kind"], []).append(p)

    print(f"  {'Jurisdiction':26s} {'chosen':11s} {'count':>6s} {'inc':>4s} {'dead':>5s}"
          f"  verdict")
    print("  " + "-" * 104)
    for p in plans:
        print(f"  {p['name']:26s} {str(p['chosen']):11s} "
              f"{str(p['count']):>6s} {p['include']:>4d} {p['dead']:>5d}  {p['kind']}")

    print("\n  Plan:")
    for kind in ("contaminated_include", "free_exclusion", "clean_include",
                 "no_dead_list", "already_fixed"):
        rows = kinds.get(kind) or []
        if not rows:
            continue
        print(f"    {kind:22s} ({len(rows)}) "
              f"{', '.join(r['name'].split(',')[0] for r in rows)}")
        for r in rows:
            if kind in ("contaminated_include", "free_exclusion"):
                print(f"        {r['name']:24s} {r['reason']}")

    if results is None:
        print("\n  Nothing measured (dry run). Re-run with --measure or --commit.")
        return 0

    if results.get("error"):
        print(f"\n  MEASUREMENT FAILED: {results['error']}")
        return 1

    print(f"\n  Measured {results['measured_at']}")
    if results["applied"]:
        print(f"\n  APPLIED ({len(results['applied'])}):")
        for r in results["applied"]:
            d = f" at depth {r['depth']}" if r.get("depth") else ""
            print(f"    {r['name']:26s} {r['count']} -> {r['after']}{d}  ({r['kind']})")
    if results["rejected"]:
        print(f"\n  REJECTED, and this is a finding rather than a failure "
              f"({len(results['rejected'])}):")
        for r in results["rejected"]:
            print(f"    {r['name']:26s} {r['count']} -> {r.get('after')}")
            print(f"        {r['why']}")
    if results["failed"]:
        print(f"\n  COULD NOT MEASURE ({len(results['failed'])}) -- left unchanged:")
        for r in results["failed"]:
            print(f"    {r['name']:26s} {r['why']}")
    return 1 if results["failed"] else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Fix the T1 dead-neighbourhood geography")
    ap.add_argument("--measure", action="store_true",
                    help="measure the fixes, write nothing")
    ap.add_argument("--commit", action="store_true", help="measure the fixes AND write")
    ap.add_argument("--path", default=str(OUT))
    a = ap.parse_args()

    path = Path(a.path)
    tc = json.loads(path.read_text(encoding="utf-8"))
    dead = json.loads(DEAD.read_text(encoding="utf-8"))["counties"]
    plans = classify(tc, dead)

    results = None
    if a.measure or a.commit:
        results = asyncio.run(apply_fixes(tc, dead, plans))
    rc = report(plans, results)

    if a.commit and results and not results.get("error"):
        if results["applied"]:
            tc.setdefault("geo_fixes", []).append({
                "source": "dpd_geo_fix",
                "applied_at": results["measured_at"],
                "counties": [r["name"] for r in results["applied"]],
                "rejected": [{"county": r["name"], "why": r["why"]}
                             for r in results["rejected"]],
            })
            path.write_text(json.dumps(tc, indent=1), encoding="utf-8")
            print(f"\nWrote {path}  ({len(results['applied'])} T1 geographies fixed)")
        else:
            print("\nNothing applied; artifact left untouched.")
    elif results:
        print("\nDry run: measured only, artifact NOT written. Re-run with --commit.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
