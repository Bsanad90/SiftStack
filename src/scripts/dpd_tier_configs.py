"""Phase 3: the T1/T2 tier definitions, as replayable SiftMap URLs sized against live counts.

Joins the confirmed filter vocabulary to the buildability analysis and the Phase 1
geography, and emits one T1 and one T2 config per jurisdiction:

    output/dpd_siftmap_filters.json   what SiftMap can actually filter on (Phase 2/2b/2d)
    data/dpd_buildability.json        every buildable stack per county, with list sizes
    data/dpd_zips.json                top ZIPs per county            (T2 geography)
    data/dpd_neighborhoods.json       top neighbourhoods per county  (T1 geography)
    data/dpd_dead_neighborhoods.json  suppression #4

    python src/scripts/dpd_tier_configs.py            # build data/dpd_tier_configs.json
    python src/scripts/dpd_tier_configs.py --measure  # + size every tier against live counts

**--measure is not optional in practice.** The workbook list sizes come from DataSift's
doors-per-deal tool and the counts come from the SiftMap filter that will actually run the
pull; they disagree badly in BOTH directions (Anne Arundel 552 -> 796 stack-alone,
Spotsylvania 1,751 -> 170, against Prince William 427 -> 2,319). Sizing a cadence off the
workbook figure puts several counties in the wrong tier entirely.

Read-only. Building touches no browser; --measure only READS counts and writes nothing to
the account.
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

from dpd.jurisdictions import JURISDICTIONS  # noqa: E402

SIFTMAP_URL = "https://app.reisift.io/siftmap"

BUILDABILITY = ROOT / "data" / "dpd_buildability.json"
ZIPS = ROOT / "data" / "dpd_zips.json"
NBRS = ROOT / "data" / "dpd_neighborhoods.json"
DEAD = ROOT / "data" / "dpd_dead_neighborhoods.json"
OUT = ROOT / "data" / "dpd_tier_configs.json"

# The playbook's own T1 sizing contract.
T1_LO, T1_HI = 200, 500
T2_ZIPS = 5
T1_NBRS = 5

# The geography layer's URL contract, confirmed live on Baltimore County (baseline
# 303,888) and verified by arithmetic rather than by a parameter merely returning a
# number: include and exclude sum EXACTLY to the baseline on all three fields tested.
#   zip_codes=["21228"]                            -> 17,062
#   zip_codes=["21228"]&zip_codes_mode=exclude     -> 286,826   (17,062 + 286,826 = baseline)
#   neighborhoods=["Arbutus"]                      ->  1,415
#   neighborhoods=["Arbutus"]&..._mode=exclude     -> 302,473   ( 1,415 + 302,473 = baseline)
#   cities=["Catonsville"]                         -> 16,577
# The value MUST be a JSON array. A bare `zip_codes=21228` returns 0, which is the
# signature of a recognised parameter handed an unparseable value -- not of an empty
# county, and not of an ignored parameter (those return the baseline instead).
GEO_FIELDS = ("zip_codes", "cities", "neighborhoods", "municipalities")

# Do not re-add what the account already holds. Also verified by arithmetic on Anne
# Arundel: not_in 222,393 + in 2,750 = 225,143, the exact unfiltered baseline.
SUPPRESSION = ["in_my_account_mode=not_in"]

# The single-family buy box. Property Types is ONE BOOLEAN PER TYPE -- type_single_family,
# type_condo, type_townhouse, type_multi_family, type_land, type_mobile_home,
# type_trailer_rv_parks, type_warehouse, type_multi_family_commercial -- which is why every
# `property_types=[...]` array guess was silently ignored: the name was wrong, so the value
# never mattered. Confirmed live and cross-checked against an independent source: on
# Baltimore County `type_single_family=true` returns 177,078, the exact SFR Supply figure
# the doors-per-deal workbook publishes for that county.
BUY_BOX = ["type_single_family=true"]


def _geo(field: str, values: list[str], mode: str = "include") -> list[str]:
    if field not in GEO_FIELDS:
        raise ValueError(f"unknown geography field {field!r}")
    if not values:
        return []
    frags = [f"{field}={quote(json.dumps(list(values)))}"]
    if mode == "exclude":
        frags.append(f"{field}_mode=exclude")
    return frags


def location_param(county: str, state: str, fips: str) -> str:
    return json.dumps({
        "searchType": "county",
        "title": f"{county} County, {state}",
        "county": county,
        "state": state,
        "counties": [{"fips": fips, "county_name": county}],
    })


def build_url(loc: str, frags: list[str]) -> str:
    q = "&".join(f for f in frags if f)
    return f"{SIFTMAP_URL}?location={quote(loc)}" + (f"&{q}" if q else "")


def _size_state(n: int) -> str:
    if n < T1_LO:
        return "under_band"
    return "in_band" if n <= T1_HI else "over_band"


def build() -> dict:
    for p in (BUILDABILITY, ZIPS, NBRS):
        if not p.exists():
            raise SystemExit(f"missing {p} - run the earlier phases first")

    b = json.loads(BUILDABILITY.read_text(encoding="utf-8"))
    zips = json.loads(ZIPS.read_text(encoding="utf-8"))["counties"]
    nbrs = json.loads(NBRS.read_text(encoding="utf-8"))["counties"]
    dead = (json.loads(DEAD.read_text(encoding="utf-8"))["counties"]
            if DEAD.exists() else {})

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "band": [T1_LO, T1_HI],
        "geography_contract": {
            "fields": list(GEO_FIELDS),
            "value_format": "JSON array, URL-encoded",
            "exclude": "<field>_mode=exclude (include is the default)",
            "verified": "include + exclude sum exactly to the county baseline on "
                        "zip_codes, cities and neighborhoods (Baltimore County, 303,888)",
        },
        "buy_box": {
            "params": BUY_BOX,
            "note": "Property Types is one boolean per type (type_single_family, "
                    "type_condo, type_townhouse, type_multi_family, type_land, "
                    "type_mobile_home, type_trailer_rv_parks, type_warehouse, "
                    "type_multi_family_commercial). Every count below is single-family "
                    "only. Verified: Baltimore County returns 177,078, the exact SFR "
                    "Supply figure in the doors-per-deal workbook.",
        },
        "unavailable_controls": {
            "AI Scores": "investor_off_market_score_min / investor_on_market_score_min / "
                         "realtor_score_min are all RECOGNISED (they return 0, which an "
                         "unknown name never does) but every property scores empty -- "
                         "min=0 returns 0. AI scores are a paid per-property add-on this "
                         "account has not bought, so the playbook's most efficient single "
                         "lever is unavailable here. Filterable, not available, exactly "
                         "like Lis Pendens.",
        },
        "counties": {},
    }

    for j in JURISDICTIONS:
        county = b["counties"].get(j.name)
        if not county:
            continue
        loc = location_param(j.market_finder_label, j.state_abbr, county["fips"])
        gz = zips.get(j.name, {})
        gn = nbrs.get(j.name, {})
        top_zips = [str(z) for z in (gz.get("selected") or [])][:T2_ZIPS]
        top_nbrs = [str(n) for n in (gn.get("selected") or [])][:T1_NBRS]
        ranked_nbrs = [str(n) for n in (gn.get("ranked") or [])]
        # dead_neighborhoods rows are dicts ({neighborhood, inv_trans_6mo}), not strings.
        dead_nbrs = [d["neighborhood"] for d in (dead.get(j.name, {}).get("dead") or [])
                     if isinstance(d, dict) and d.get("neighborhood")]

        stack = county.get("best_buildable_stack") or {}
        params = list(stack.get("params") or [])

        # T1 is offered as TWO variants and the choice is made from measured counts, not
        # from the workbook. Building only the narrowed one is what produced the earlier
        # nonsense of a 16-record Anne Arundel list being reported as a thin STACK, when in
        # fact the stack holds 796 and it was the neighbourhood cut that was too deep.
        variants = {
            "stack_only": {
                "label": "distressor stack, county-wide",
                "params": list(params),
                "url": build_url(loc, params + BUY_BOX + SUPPRESSION),
            }
        }
        if top_nbrs:
            narrowed = params + _geo("neighborhoods", top_nbrs)
            variants["narrowed"] = {
                "label": f"stack + top {len(top_nbrs)} neighbourhoods",
                "neighborhoods": top_nbrs,
                "params": narrowed,
                "url": build_url(loc, narrowed + BUY_BOX + SUPPRESSION),
            }

        # T2 = the ZIP layer, deliberately WITHOUT the distressor stack: it is the bulk and
        # mail tier, so it trades lift for reach.
        t2 = _geo("zip_codes", top_zips) if top_zips else []
        t2_geo = {"zip_codes": top_zips, "mode": "include"}
        # Suppression #4 rides on T2 only. The neighbourhoods field takes ONE direction at
        # a time, so a T1 that already INCLUDES its top neighbourhoods cannot also exclude
        # the dead ones -- and does not need to, since the top-5 list was selected by the
        # Phase 1 checks and no dead neighbourhood is in it.
        if dead_nbrs:
            t2 += _geo("neighborhoods", dead_nbrs, mode="exclude")
            t2_geo["neighborhoods_excluded"] = len(dead_nbrs)

        out["counties"][j.name] = {
            "fips": county["fips"],
            "T1": {
                "tier": "T1 Hyper-Targeted",
                "stack": stack.get("signal"),
                "signals": stack.get("signals"),
                "lift": stack.get("lift"),
                "workbook_list_size": stack.get("list_size"),
                "variants": variants,
                "ranked_neighborhoods": ranked_nbrs,
                "_loc": loc,
                "chosen": None,
                "sizing": {"state": "unmeasured",
                           "action": "run --measure; the workbook figure is not a "
                                     "reliable basis for this decision"},
            },
            "T2": {
                "tier": "T2 Focused",
                "note": "geography only; the bulk and mail tier trades lift for reach",
                "geography": t2_geo,
                "params": t2,
                "url": build_url(loc, t2 + BUY_BOX + SUPPRESSION),
            },
            "blocking_signals": county.get("blocking_signals"),
            "named_top_stack": (county.get("named_top_stack") or {}).get("signal"),
            "widen_candidates": [
                {"signal": s["signal"], "lift": s["lift"],
                 "workbook_list_size": s["list_size"], "params": s["params"]}
                for s in sorted((s for s in county.get("all_stacks", [])
                                 if s.get("buildable") and s.get("list_size")),
                                key=lambda s: -s.get("lift", 0))[:6]
            ],
        }
    return out


def decide(out: dict) -> None:
    """Pick each county's T1 variant from the measured counts and name the next action."""
    for cfg in out["counties"].values():
        t1 = cfg["T1"]
        v = t1["variants"]
        so = v.get("stack_only", {}).get("measured_count")
        nc = v.get("narrowed", {}).get("measured_count")

        if so is None:
            continue
        st_so = _size_state(so)

        if st_so == "in_band":
            t1["chosen"] = "stack_only"
            t1["sizing"] = {"state": "in_band", "action": "none", "count": so,
                            "reason": f"the stack alone measures {so}, inside the band; "
                                      "no geography needed"}
            continue

        if st_so == "under_band":
            best = (cfg.get("widen_candidates") or [None])[0]
            t1["chosen"] = "stack_only"
            t1["sizing"] = {
                "state": "under_band", "action": "widen", "count": so,
                "reason": f"the stack alone measures only {so}, under {T1_LO}; narrowing "
                          "would make it worse, so the fix is a broader stack",
                "widen_caveat": "widen_candidates are sized from the workbook and must "
                                "themselves be measured before use",
                "widen_first_choice": best,
            }
            continue

        # Over band. The neighbourhood cut is the lever, and it can overshoot.
        if nc is None:
            t1["chosen"] = "stack_only"
            t1["sizing"] = {
                "state": "over_band", "action": "narrow_unavailable", "count": so,
                "reason": f"the stack measures {so}, over {T1_HI}, but this county has no "
                          "neighbourhood layer to narrow with",
            }
            continue

        # A depth search, when it found a depth in band, beats both fixed variants.
        ds = t1.get("depth_search") or {}
        if ds.get("status") == "in_band":
            k, c = ds["depth"], ds["count"]
            t1["chosen"] = "tuned"
            t1["sizing"] = {
                "state": "in_band", "action": "none", "count": c, "depth": k,
                "reason": f"stack alone {so} is over band; the top-{k} neighbourhood cut "
                          f"(depth-searched over {len(t1.get('ranked_neighborhoods') or [])} "
                          f"ranked) measures {c}",
            }
            continue
        if ds.get("status") in ("no_count", "probe_failed"):
            # A search that could not measure has not found anything. Say that, rather than
            # letting a failed probe read as "geography cannot fill this band".
            t1["chosen"] = "narrowed" if nc is not None else "stack_only"
            t1["sizing"] = {
                "state": "measurement_failed", "action": "re_measure",
                "count": nc, "stack_only_count": so,
                "reason": "the depth search could not get a trustworthy count; re-run "
                          "--measure before drawing any conclusion about this county",
                "probes": ds.get("probes"),
            }
            continue
        if ds.get("status") in ("band_skipped", "unreachable_low", "unreachable_high"):
            t1["chosen"] = "narrowed" if nc is not None else "stack_only"
            t1["sizing"] = {
                "state": "no_depth_in_band", "action": "needs_another_lever",
                "count": nc, "stack_only_count": so,
                "depth_search_status": ds["status"],
                "reason": ds.get("reason", "the neighbourhood layer cannot reach the band"),
            }
            continue

        st_nc = _size_state(nc)
        if st_nc == "in_band":
            t1["chosen"] = "narrowed"
            t1["sizing"] = {"state": "in_band", "action": "none", "count": nc,
                            "reason": f"stack alone {so} is over band; the top-"
                                      f"{len(v['narrowed']['neighborhoods'])} neighbourhood "
                                      f"cut brings it to {nc}"}
        elif st_nc == "under_band":
            # Both variants are wrong in opposite directions. Say so and name the lever,
            # rather than picking one and calling it sized.
            t1["chosen"] = "narrowed"
            t1["sizing"] = {
                "state": "cut_overshoots", "action": "tune_neighbourhood_count",
                "count": nc, "stack_only_count": so,
                "reason": f"the stack alone measures {so} (over {T1_HI}) but the top-"
                          f"{len(v['narrowed']['neighborhoods'])} neighbourhood cut drops it "
                          f"to {nc} (under {T1_LO}). The band sits between the two, so the "
                          "neighbourhood list needs to be longer than 5. Phase 1 only "
                          "stores the top 5 -- re-run dpd_market_research.py --score with a "
                          "larger top_n to get the intermediate cut.",
            }
        else:
            t1["chosen"] = "narrowed"
            t1["sizing"] = {
                "state": "over_band", "action": "narrow_further", "count": nc,
                "reason": f"even narrowed to the top neighbourhoods this measures {nc}, "
                          f"still over {T1_HI}; add a distressor or cut the neighbourhood "
                          "list further",
            }


async def _search_depth(count_fn, loc: str, params: list[str], ranked: list[str],
                        seed: dict[int, int] | None = None, log=print) -> dict:
    """Find the neighbourhood depth whose count lands inside the T1 band.

    The count rises monotonically with the number of included neighbourhoods, so this is a
    binary search for the SMALLEST depth reaching T1_LO, then a check that it has not
    already overshot T1_HI. Fixing the depth at 5 was what left four counties measuring
    over band on the stack alone and under band on the cut, with the band in between.

    Returns the chosen depth, its count, and the probes taken, so the decision is auditable
    rather than a bare number.
    """
    # Seed with counts already measured (the fixed top-5 cut), so the very first probe of
    # the search is checked against a known-good anchor rather than against nothing.
    probes: list[dict] = [{"k": k, "count": c} for k, c in (seed or {}).items()
                          if c is not None]

    def _violates(k: int, c: int | None) -> str:
        """Monotonicity check: including more neighbourhoods can never return fewer.

        This is the guard that stops a failed probe being read as a finding. Charles
        measured 685 at depth 5 and then 0 at depth 29, and without this check that 0 was
        reported as 'geography cannot fill this band' -- a conclusion drawn from a page
        that simply had not rendered its count.
        """
        if c is None:
            return "no count rendered"
        for p in probes:
            if p["count"] is None:
                continue
            if p["k"] < k and c < p["count"]:
                return f"depth {k} returned {c}, below depth {p['k']}'s {p['count']}"
            if p["k"] > k and c > p["count"]:
                return f"depth {k} returned {c}, above depth {p['k']}'s {p['count']}"
        return ""

    async def at(k: int) -> int | None:
        for p in probes:
            if p["k"] == k:
                return p["count"]
        url = build_url(loc, params + _geo("neighborhoods", ranked[:k]) + BUY_BOX + SUPPRESSION)
        c = await count_fn(url)
        bad = _violates(k, c)
        if bad:
            # Retry once before believing an impossible number.
            log(f"      depth {k:>4} -> {c}  IMPLAUSIBLE ({bad}); retrying")
            c2 = await count_fn(url)
            bad2 = _violates(k, c2)
            if bad2:
                probes.append({"k": k, "count": None, "rejected": [c, c2],
                               "reason": bad2})
                log(f"      depth {k:>4} -> rejected twice ({bad2})")
                return None
            c = c2
        probes.append({"k": k, "count": c})
        log(f"      depth {k:>4} -> {c}")
        return c

    full = await at(len(ranked))
    if full is None:
        return {"status": "no_count", "probes": probes}
    if full < T1_LO:
        # Every neighbourhood in the ranked pool still is not enough.
        return {"status": "unreachable_high", "depth": len(ranked), "count": full,
                "probes": probes,
                "reason": f"including all {len(ranked)} ranked neighbourhoods still measures "
                          f"{full}, under {T1_LO}; geography cannot fill this band"}
    one = await at(1)
    if one is not None and one > T1_HI:
        return {"status": "unreachable_low", "depth": 1, "count": one, "probes": probes,
                "reason": f"even a single neighbourhood measures {one}, over {T1_HI}; the "
                          "geography layer is too coarse here and the stack needs another "
                          "distressor instead"}

    lo, hi = 1, len(ranked)          # count(lo) < T1_LO <= count(hi)
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        c = await at(mid)
        # A failed probe would break the invariant, so stop rather than guess.
        if c is None:
            return {"status": "probe_failed", "probes": probes}
        if c < T1_LO:
            lo = mid
        else:
            hi = mid
    chosen = await at(hi)
    if chosen is None:
        return {"status": "probe_failed", "probes": probes}
    if chosen > T1_HI:
        prev = await at(lo)
        return {"status": "band_skipped", "depth": hi, "count": chosen, "probes": probes,
                "reason": f"depth {lo} measures {prev} (under {T1_LO}) and depth {hi} "
                          f"measures {chosen} (over {T1_HI}); no depth lands in the band "
                          "because one neighbourhood is larger than the band is wide"}
    return {"status": "in_band", "depth": hi, "count": chosen, "probes": probes}


async def measure(out: dict) -> None:
    from datasift_core import create_browser, get_credentials, login
    from dpd_siftmap_discover import _clean_map, _result_count

    email, password = get_credentials()
    async with create_browser(headless=True) as (_b, _c, page):
        if not await login(page, email, password):
            out["measure_error"] = "login failed; nothing measured"
            return

        async def count(url: str):
            # One retry on a missing count. A map that has not finished rendering reports
            # nothing, and nothing was being written down as a real measurement -- DC came
            # back unmeasured on one pass and 63 on another from the identical URL.
            for attempt in (1, 2):
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(8000 if attempt == 1 else 14000)
                await _clean_map(page)
                c = await _result_count(page)
                if c is not None:
                    return c
            return None

        for name, cfg in out["counties"].items():
            for key, var in cfg["T1"]["variants"].items():
                try:
                    var["measured_count"] = await count(var["url"])
                except Exception as e:  # noqa: BLE001
                    var["measured_count"], var["measure_error"] = None, str(e)
                print(f"  {name:26s} T1/{key:11s} {var.get('measured_count')}")
            try:
                cfg["T2"]["measured_count"] = await count(cfg["T2"]["url"])
            except Exception as e:  # noqa: BLE001
                cfg["T2"]["measured_count"], cfg["T2"]["measure_error"] = None, str(e)
            print(f"  {name:26s} T2             {cfg['T2'].get('measured_count')}")

            # Only counties whose stack alone is over band can be fixed by geography.
            so = cfg["T1"]["variants"].get("stack_only", {}).get("measured_count")
            ranked = cfg["T1"].get("ranked_neighborhoods") or []
            if so is not None and so > T1_HI and len(ranked) > 1:
                print(f"  {name:26s} searching neighbourhood depth (stack alone {so})")
                nar = cfg["T1"]["variants"].get("narrowed") or {}
                seed = ({len(nar["neighborhoods"]): nar["measured_count"]}
                        if nar.get("measured_count") is not None else None)
                res = await _search_depth(count, cfg["T1"]["_loc"],
                                          cfg["T1"]["variants"]["stack_only"]["params"],
                                          ranked, seed=seed)
                cfg["T1"]["depth_search"] = res
                if res.get("status") == "in_band":
                    k = res["depth"]
                    tuned = (cfg["T1"]["variants"]["stack_only"]["params"]
                             + _geo("neighborhoods", ranked[:k]))
                    cfg["T1"]["variants"]["tuned"] = {
                        "label": f"stack + top {k} neighbourhoods (depth-searched)",
                        "neighborhoods": ranked[:k],
                        "params": tuned,
                        "url": build_url(cfg["T1"]["_loc"], tuned + BUY_BOX + SUPPRESSION),
                        "measured_count": res["count"],
                    }


def report(out: dict) -> int:
    print("\n=== DOORS-PER-DEAL TIER CONFIGS ===")
    print(f"generated {out['generated_at']}   band {T1_LO}-{T1_HI}\n")

    g = out["geography_contract"]
    print("  Geography contract (confirmed live):")
    print(f"    fields  : {', '.join(g['fields'])}")
    print(f"    value   : {g['value_format']}")
    print(f"    exclude : {g['exclude']}")
    print(f"    verified: {g['verified']}\n")
    print(f"  Buy box: {', '.join(out['buy_box']['params'])}")
    print(f"    {out['buy_box']['note']}\n")
    print("  Controls that are recognised but carry no data on this account:")
    for k, v in out["unavailable_controls"].items():
        print(f"    {k}: {v}")

    print(f"\n  {'Jurisdiction':26s} {'T1 stack':38s} {'lift':>5s} {'wbook':>6s} "
          f"{'stack':>6s} {'narrow':>6s} {'T2':>7s}  state")
    print("  " + "-" * 122)
    for name, cfg in out["counties"].items():
        t1, v = cfg["T1"], cfg["T1"]["variants"]
        so = v.get("stack_only", {}).get("measured_count")
        nc = v.get("narrowed", {}).get("measured_count")
        print(f"  {name:26s} {str(t1['stack'])[:38]:38s} {str(t1['lift']):>5s} "
              f"{str(t1['workbook_list_size']):>6s} {str(so if so is not None else '-'):>6s} "
              f"{str(nc if nc is not None else '-'):>6s} "
              f"{str(cfg['T2'].get('measured_count', '-')):>7s}  {t1['sizing']['state']}")

    print("\n  Chosen T1 variant per county:")
    for name, cfg in out["counties"].items():
        t1 = cfg["T1"]
        ch = t1.get("chosen")
        var = (t1["variants"].get(ch) or {}) if ch else {}
        print(f"    {name:26s} {str(ch):11s} {str(var.get('measured_count','-')):>6s}  "
              f"{var.get('label','(unmeasured)')}")

    acts: dict[str, list[str]] = {}
    for name, cfg in out["counties"].items():
        acts.setdefault(cfg["T1"]["sizing"]["action"], []).append(name.split(",")[0])
    print("\n  T1 actions:")
    for act, names in sorted(acts.items()):
        print(f"    {act:32s} ({len(names)}) {', '.join(names)}")

    print("\n  Where the tier is not yet shippable, and the lever:")
    for name, cfg in out["counties"].items():
        s = cfg["T1"]["sizing"]
        if s["state"] not in ("in_band",):
            print(f"    {name}: {s.get('reason') or s.get('action') or s['state']}")

    wb_wrong = []
    for name, cfg in out["counties"].items():
        wb = cfg["T1"]["workbook_list_size"]
        so = cfg["T1"]["variants"].get("stack_only", {}).get("measured_count")
        if wb and so:
            ratio = so / wb
            if ratio >= 2 or ratio <= 0.5:
                wb_wrong.append((name, wb, so, ratio))
    if wb_wrong:
        print("\n  Workbook list size vs the live SiftMap count (2x or worse, both ways):")
        for name, wb, so, r in sorted(wb_wrong, key=lambda t: -abs(t[3])):
            print(f"    {name:26s} workbook {wb:>6}  live {so:>6}  ({r:.2f}x)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate and size the T1/T2 tier configs")
    ap.add_argument("--measure", action="store_true",
                    help="open each URL and size every tier from the live count")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()

    out = build()
    if a.measure:
        asyncio.run(measure(out))
        decide(out)
    rc = report(out)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1), encoding="utf-8")
    n = len(out["counties"])
    print(f"\nWrote {p}  ({n} jurisdictions, {n * 2} tier configs)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
