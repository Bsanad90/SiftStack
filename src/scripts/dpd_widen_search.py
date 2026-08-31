"""Find a shippable T1 for the counties whose best stack does not land in the band.

Phase 3 sized all 28 tier configs against live counts and nine T1s landed in the 200-500
band. The other five did not, in two different ways:

  UNDER band  Baltimore County 107, DC 63, Fairfax 168  -- the stack is too precise
  TOO COARSE  Calvert, Fredericksburg City              -- one neighbourhood already
                                                           overshoots, so geography
                                                           cannot narrow it

Both are the same search: walk that county's buildable stacks from the highest lift down,
measure each, and take the first that lands in band -- narrowing with neighbourhoods when
the stack alone is over. The candidates cannot be chosen on paper because the workbook list
sizes they carry are 2-5x off the live counts (Prince William 427 -> 2,319).

    python src/scripts/dpd_widen_search.py                 # every county not in band
    python src/scripts/dpd_widen_search.py --county Fairfax

Writes data/dpd_widen_search.json. Read-only: counts are read off the map, nothing is
written to the account.
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
from dpd_tier_configs import (  # noqa: E402
    BUY_BOX, SUPPRESSION, T1_HI, T1_LO, _geo, _search_depth, _size_state, build,
    build_url,
)

CONFIGS = ROOT / "data" / "dpd_tier_configs.json"
BUILDABILITY = ROOT / "data" / "dpd_buildability.json"
OUT = ROOT / "data" / "dpd_widen_search.json"

# How far down the lift ranking to go before giving up. Past this the stacks are so broad
# that they stop being a hyper-targeted tier at all.
MAX_CANDIDATES = 20


def _all_candidates(name: str, cfg: dict) -> list[dict]:
    """Every buildable stack for this county, highest lift first.

    The tier config's own `widen_candidates` is truncated to six, which is not enough:
    Baltimore County has 25 buildable stacks and the first six were all under band, so the
    search gave up while nineteen were untried. The full list lives in the buildability
    artifact, so read it from there and fall back to the config only if it is missing.
    """
    if BUILDABILITY.exists():
        b = json.loads(BUILDABILITY.read_text(encoding="utf-8"))
        rows = (b["counties"].get(name) or {}).get("all_stacks") or []
        rows = [s for s in rows if s.get("buildable") and s.get("params")]
        if rows:
            seen, out = set(), []
            for s in sorted(rows, key=lambda s: -(s.get("lift") or 0)):
                key = tuple(sorted(s["params"]))
                if key in seen:
                    continue
                seen.add(key)
                out.append({"signal": s["signal"], "lift": s.get("lift") or 0.0,
                            "lift_known": s.get("lift") is not None,
                            "workbook_list_size": s.get("list_size"),
                            "params": s["params"]})
            return out
    return cfg.get("widen_candidates") or []


async def search_county(page, count, name: str, cfg: dict) -> dict:
    """Walk this county's stacks from highest lift down and take the first in-band one."""
    loc = cfg["T1"]["_loc"]
    ranked_nbrs = cfg["T1"].get("ranked_neighborhoods") or []
    cands = _all_candidates(name, cfg)[:MAX_CANDIDATES]
    tried: list[dict] = []

    for cand in cands:
        params = list(cand["params"])
        url = build_url(loc, params + BUY_BOX + SUPPRESSION)
        c = await count(url)
        rec = {"signal": cand["signal"], "lift": cand["lift"],
               "workbook_list_size": cand["workbook_list_size"],
               "stack_only_count": c}
        lift = f"{cand['lift']:>6}x" if cand.get("lift_known", True) else "     ?x"
        print(f"    {lift}  {cand['signal'][:44]:46s} {c}")

        if c is None:
            rec["verdict"] = "no count; skipped rather than assumed empty"
            tried.append(rec)
            continue

        state = _size_state(c)
        if state == "in_band":
            rec.update(verdict="in_band", chosen_count=c, depth=None,
                       url=url, params=params)
            tried.append(rec)
            return {"status": "found", "choice": rec, "tried": tried}

        if state == "under_band":
            # Candidates are ordered by lift, and lower lift generally means a broader
            # list, so a miss here is not the end of the walk.
            rec["verdict"] = f"under band ({c} < {T1_LO})"
            tried.append(rec)
            continue

        # Over band: try to narrow with the neighbourhood layer.
        if len(ranked_nbrs) > 1:
            print(f"           over band ({c}); searching neighbourhood depth")
            ds = await _search_depth(count, loc, params, ranked_nbrs,
                                     log=lambda s: print("     " + s.strip()))
            rec["depth_search"] = {k: v for k, v in ds.items() if k != "probes"}
            if ds.get("status") == "in_band":
                k = ds["depth"]
                narrowed = params + _geo("neighborhoods", ranked_nbrs[:k])
                rec.update(verdict="in_band_after_narrowing", chosen_count=ds["count"],
                           depth=k, neighborhoods=ranked_nbrs[:k], params=narrowed,
                           url=build_url(loc, narrowed + BUY_BOX + SUPPRESSION))
                tried.append(rec)
                return {"status": "found", "choice": rec, "tried": tried}
            rec["verdict"] = f"over band ({c}) and {ds.get('status')}"
        else:
            rec["verdict"] = f"over band ({c}) with no neighbourhood layer to narrow with"
        tried.append(rec)

    return {"status": "none_in_band", "tried": tried}


async def run(only: list[str] | None) -> dict:
    cfgs = json.loads(CONFIGS.read_text(encoding="utf-8")) if CONFIGS.exists() else build()
    targets = {
        n: c for n, c in cfgs["counties"].items()
        if c["T1"]["sizing"]["state"] != "in_band"
        and (not only or any(o.lower() in n.lower() for o in only))
    }
    out = {"searched_at": datetime.now().isoformat(timespec="seconds"),
           "band": [T1_LO, T1_HI], "counties": {}}
    if not targets:
        out["note"] = "every T1 is already in band; nothing to widen"
        return out

    email, password = get_credentials()
    async with create_browser(headless=True) as (_b, _c, page):
        if not await login(page, email, password):
            out["error"] = "login failed"
            return out

        async def count(url: str):
            for attempt in (1, 2):
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(8000 if attempt == 1 else 13000)
                await _clean_map(page)
                c = await _result_count(page)
                if c is not None:
                    return c
            return None

        for name, cfg in targets.items():
            cur = cfg["T1"]
            print(f"\n  {name}  (current: {cur['stack']} {cur['lift']}x -> "
                  f"{cur['sizing'].get('count')}, {cur['sizing']['state']})")
            res = await search_county(page, count, name, cfg)
            res["previous"] = {"stack": cur["stack"], "lift": cur["lift"],
                               "count": cur["sizing"].get("count"),
                               "state": cur["sizing"]["state"]}
            out["counties"][name] = res
    return out


def report(out: dict) -> int:
    print("\n=== WIDEN SEARCH ===")
    if out.get("error"):
        print("  ERROR:", out["error"])
        return 1
    if out.get("note"):
        print("  " + out["note"])
        return 0

    found, missed = [], []
    for name, res in out["counties"].items():
        (found if res["status"] == "found" else missed).append((name, res))

    if found:
        print(f"\n  Now shippable ({len(found)}):")
        for name, res in found:
            ch, prev = res["choice"], res["previous"]
            cut = f"top {ch['depth']} nbrs" if ch.get("depth") else "stack only"
            print(f"    {name:26s} {ch['signal'][:40]:42s} "
                  f"{str(ch.get('lift', '?')):>6}x  {ch['chosen_count']:>5}  ({cut})")
            print(f"      was: {prev['stack']} {prev['lift']}x -> {prev['count']} "
                  f"({prev['state']})")

    if missed:
        print(f"\n  Still nothing in band ({len(missed)}):")
        for name, res in missed:
            print(f"    {name}: {len(res['tried'])} stacks measured, none landed")
            for t in res["tried"][:6]:
                print(f"      {str(t.get('lift','?')):>6}x {t['signal'][:40]:42s} "
                      f"{str(t.get('stack_only_count')):>6}  {t.get('verdict','')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Widen the T1 stacks that miss the band")
    ap.add_argument("--county", help="comma-separated substrings to limit the search")
    a = ap.parse_args()
    only = [s.strip() for s in a.county.split(",")] if a.county else None

    out = asyncio.run(run(only))
    rc = report(out)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # MERGE, never replace. A --county run covers a subset, and writing the whole file
    # from it would silently discard every county the earlier full pass resolved.
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            merged = dict(prev.get("counties") or {})
            merged.update(out.get("counties") or {})
            out["counties"] = merged
            out["merged_with"] = prev.get("searched_at")
        except Exception as e:  # noqa: BLE001
            out["merge_error"] = str(e)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote {OUT}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
