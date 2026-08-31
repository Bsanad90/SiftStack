"""Turn one county's doors-per-deal rows into SiftMap preset definitions, and size them.

    python src/scripts/dpd_siftmap_manifest.py --fips 11001            # build
    python src/scripts/dpd_siftmap_manifest.py --fips 11001 --measure  # + live counts

The playbook's instruction is literal: "pull your Priority 1 rows ... save each one as its
own preset in SiftMap, and work them top to bottom." So this emits ONE preset per Priority
1 / Priority 2 row that SiftMap can actually express, in ladder order (priority, then
doors/deal ascending), plus a Tier 2 geography preset from the workbook's own rated ZIPs.
Rows SiftMap cannot filter (HOA Lien, Other Lien, Low Income, Bad Credit, the AI score
bands, Obituary) are kept in the manifest as stated gaps and routed to first-to-market.

Reads  data/dpd_playbook_<fips>.json   (dpd_playbook_extract.py)
Writes data/dpd_siftmap_manifest_<fips>.json

Every URL carries the single-family buy box. The saved-preset URL does NOT carry
`in_my_account_mode=not_in` -- that is a pull-time suppression, so `--measure` records
both the raw count and the not-in-account count.

`--measure` is read-only: it opens URLs and reads the "<N> Properties" figure. Nothing is
saved and no record is added. Three guards, each from an earlier failure in this build:
  * a count EQUAL to the unfiltered single-family baseline means a parameter was ignored,
    so that row is marked unbuildable rather than saved as a county-wide preset;
  * a count of 0 is recorded as "measured 0", never as success or as a broken filter (the
    Notice-of-Foreclosure rows legitimately measure 0-2 in DC);
  * a page that never renders its count is retried once and then recorded None.
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

from dpd.jurisdictions import BY_FIPS  # noqa: E402
from dpd_tier_configs import BUY_BOX, build_url, location_param  # noqa: E402

NOT_IN = "in_my_account_mode=not_in"
T2_STARS_MIN = 4       # the workbook's own rating; 4-5 star ZIPs make the Tier 2 layer
T2_MAX_ZIPS = 5        # the playbook's "top 3-5 zip codes"

# Atomic signal -> URL fragment(s). Every entry except the proxy was confirmed live in
# Phase 2/2b/2d (output/dpd_siftmap_filters.json): a parameter that the server ignores
# returns the unfiltered count, and these did not.
SIGNAL_PARAM = {
    "Absentee": ["preset_absentee_owners=true"],
    "Free & Clear": ["preset_free_clear=true"],
    "High Equity": ["preset_high_equity=true"],
    "Vacant": ["preset_vacant=true"],
    "Zombie": ["preset_zombie=true"],
    "Judgment Lien": ["preset_judgment=true"],
    "Pre-Probate": ["preset_deceased=true"],
    "Tax Delinquent": ["preset_tax_lien=true"],
    # Label "Absentee-Out-of-State" against field extra_absentee_in_state: the LABEL is
    # right. true excludes owner-occupiers, which only out-of-state can do (Phase 2b).
    "Out-of-State": ["extra_absentee_in_state=true"],
    "Senior": ["extra_owner_age_min=65"],
    "Notice of Foreclosure": ["extra_foreclosure_notice_type=NF"],
    "Notice of Default": ["extra_foreclosure_notice_type=ND"],
    "Lis Pendens": ["extra_foreclosure_notice_type=LP"],
}

# Signals SiftMap will not filter on at all (the More panel's 260 labels and 36 named
# controls were dumped; these are absent). AI bands are recognised parameters but every
# property on this account scores empty -- a billing decision, not a technical gap.
UNFILTERABLE = {
    "HOA Lien": "no SiftMap filter (badge only on property cards)",
    "Other Lien": "no SiftMap filter",
    "Low Income": "no SiftMap filter",
    "Bad Credit": "no SiftMap filter",
    "Bankruptcy": "no SiftMap filter",
    "Estate Sale": "no SiftMap filter",
    "Probate": "no SiftMap filter; first-to-market source",
    "Obituary": "not a SiftMap filter; the account's Obituary list is an FTM feed",
    "AI Score 90-100": "AI data not bought on this account (decision 2026-08-26)",
    "AI Score 80-90": "AI data not bought on this account (decision 2026-08-26)",
    "AI Score 50+ (coverage layer)": "AI data not bought on this account (decision 2026-08-26)",
}

# Tired Landlord has no filter. The nearest control is the "Owners with multiple
# properties" checkbox (ownerDetails.extra_owners_with_multiple_properties). Its URL value
# form is unconfirmed, so --measure probes these in order and takes the first that filters.
PROXY_SIGNAL = "Tired Landlord"
PROXY_LABEL = "Tired Landlord (proxy)"   # reconstructed as absentee + owned 10+ yrs; see PROXY_CANDIDATES
# Measured 2026-08-27 on DC: extra_owners_with_multiple_properties=true returns 47,399 of
# 48,530 single-family parcels (98%) against a workbook Tired Landlord list of 2,046. It
# "filters" in the narrow sense (not the baseline) and restricts nothing, so a count alone
# is not enough: a proxy is accepted only when it lands within PROXY_BAND of the workbook
# list size for the standalone signal. Candidates are tried in order.
# Probed 2026-08-27 on DC against the workbook's 2,046:
#   preset_absentee_owners=true&extra_years_owned_min=10   2,050   <-- 0.2% off: this IS the definition
#   extra_owner_is_investor=true                           1,789
#   preset_absentee_owners=true&extra_owner_is_investor=true 918
#   extra_owners_with_multiple_properties=true            47,399   (98% of the county)
PROXY_CANDIDATES = ["preset_absentee_owners=true&extra_years_owned_min=10",
                    "extra_owner_is_investor=true",
                    "preset_absentee_owners=true&extra_owner_is_investor=true",
                    "extra_owners_with_multiple_properties=true"]
PROXY_BAND = (0.5, 2.0)   # count / workbook list size must fall inside this


def _name(abbr: str, row: dict) -> str:
    sig = row["signal"].replace(PROXY_SIGNAL, PROXY_LABEL)
    return f"{abbr} P{row['priority']}-{row['rank']:02d} {sig}"


def build(pb: dict, proxy_param: str | None = PROXY_CANDIDATES[0]) -> dict:
    # NOTE: the proxy param is a placeholder until --measure confirms or demotes it.
    j = BY_FIPS[pb["fips"]]
    loc = location_param(j.market_finder_label, j.state_abbr, j.fips)
    abbr = j.state_abbr if j.state_abbr == "DC" else j.name.split(",")[0]

    entries, gaps = [], []
    for row in pb["rows"]:
        if row["priority"] not in (1, 2):
            continue
        params, missing, proxy = [], [], False
        for s in row["signals"]:
            if s in SIGNAL_PARAM:
                params += SIGNAL_PARAM[s]
            elif s == PROXY_SIGNAL:
                proxy = True
                if proxy_param:
                    params.append(proxy_param)
                else:
                    missing.append(f"{s} (proxy unconfirmed)")
            else:
                missing.append(s)
        base = {
            "name": _name(abbr, row), "priority": row["priority"], "rank": row["rank"],
            "signal": row["signal"], "signals": row["signals"], "keys": row.get("keys"),
            "lift": row["lift"], "dpd": row["dpd"], "deals": row["deals"],
            "workbook_list_size": row["list_size"], "typical_gross": row["typical_gross"],
            "caveat": row.get("caveats"), "verify_reason": row.get("verify_reason"),
            "plan": row.get("plan"), "proxy": proxy,
        }
        if missing:
            base["buildable"] = False
            base["missing_signals"] = missing
            base["reason"] = "; ".join(UNFILTERABLE.get(m, "no SiftMap filter") for m in missing)
            gaps.append(base)
            continue
        base.update({
            "buildable": True, "params": params,
            "url": build_url(loc, params + BUY_BOX),
            "url_not_in": build_url(loc, params + BUY_BOX + [NOT_IN]),
        })
        entries.append(base)

    # Ladder order: priority, then doors/deal ascending (the workbook's own order).
    entries.sort(key=lambda e: (e["priority"], e["dpd"] if e["dpd"] is not None else 1e9))

    # Tier 2: the workbook's rated ZIPs, 4-5 stars, top 5 by deals.
    zips = [z for z in pb.get("top_zips") or [] if (z.get("stars") or 0) >= T2_STARS_MIN]
    zips.sort(key=lambda z: -(z.get("deals") or 0))
    zips = zips[:T2_MAX_ZIPS]
    t2 = None
    if zips:
        zl = [z["zip"] for z in zips]
        zp = [f"zip_codes={quote(json.dumps(zl))}"]
        t2 = {
            "name": f"{abbr} T2 Top ZIPs {' '.join(zl)}",
            "tier": "Tier 2", "zips": zips, "params": zp,
            "url": build_url(loc, zp + BUY_BOX),
            "url_not_in": build_url(loc, zp + BUY_BOX + [NOT_IN]),
            "note": "geography only, no distressor stack; the bulk + mail tier trades lift "
                    "for reach. ZIPs are the workbook's own 4-5 star ratings by deals.",
        }

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "fips": j.fips, "jurisdiction": j.name, "state_abbr": j.state_abbr,
        "source": pb.get("source_xlsx") or "playbook JSON shard",
        "location": json.loads(loc),
        "buy_box": BUY_BOX,
        "baseline_url": build_url(loc, list(BUY_BOX)),
        "proxy": {"signal": PROXY_SIGNAL, "label": PROXY_LABEL, "param": proxy_param,
                  "candidates": PROXY_CANDIDATES, "status": "unmeasured"},
        "presets": entries,
        "tier2": t2,
        "gaps": gaps,
    }


async def measure(m: dict) -> None:
    from datasift_core import create_browser, get_credentials, login
    from dpd_siftmap_discover import _clean_map, _result_count

    email, password = get_credentials()
    async with create_browser(headless=True) as (_b, _c, page):
        if not await login(page, email, password):
            m["measure_error"] = "login failed; nothing measured"
            return

        async def count(url: str):
            for attempt in (1, 2):
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(8000 if attempt == 1 else 14000)
                await _clean_map(page)
                c = await _result_count(page)
                if c is not None:
                    return c
            return None

        base = await count(m["baseline_url"])
        m["baseline_single_family"] = base
        print(f"  baseline (single-family, whole county): {base}")
        if base is None:
            m["measure_error"] = "no baseline count rendered; cannot judge any probe"
            return

        # Settle the proxy first: it decides whether six rows exist at all.
        loc = json.dumps(m["location"])
        chosen = None
        probes = []
        # The standalone Tired Landlord row's workbook size is the yardstick.
        wb_tl = next((e.get("workbook_list_size") for e in m["presets"] + m["gaps"]
                      if e.get("signals") == [PROXY_SIGNAL]), None)
        m["proxy"]["workbook_list_size"] = wb_tl
        for cand in PROXY_CANDIDATES:
            c = await count(build_url(loc, [cand] + BUY_BOX))
            if c is None:
                verdict = "no count"
            elif c == base:
                verdict = "IGNORED (baseline)"
            elif c == 0:
                verdict = "0 (name recognised, value not)"
            elif wb_tl and not (PROXY_BAND[0] <= c / wb_tl <= PROXY_BAND[1]):
                verdict = f"IMPLAUSIBLE ({c / wb_tl:.1f}x the workbook list of {int(wb_tl)})"
            else:
                verdict = "FILTERS"
            probes.append({"param": cand, "count": c, "verdict": verdict})
            print(f"  proxy probe {cand:58s} {str(c):>8s}  {verdict}")
            if verdict == "FILTERS":
                chosen = cand
                break
        m["proxy"]["probes"] = probes
        if chosen:
            m["proxy"]["param"] = chosen
            m["proxy"]["status"] = "confirmed"
            m["proxy"]["count"] = next(p["count"] for p in probes if p["param"] == chosen)
        else:
            m["proxy"]["param"] = None
            m["proxy"]["status"] = "unbuildable"
            # Demote every proxy row to a gap; the rows are rebuilt without the proxy.
            keep, demoted = [], []
            for e in m["presets"]:
                if e.get("proxy"):
                    e.update({"buildable": False, "missing_signals": [PROXY_SIGNAL],
                              "reason": "Tired Landlord proxy did not filter; see proxy.probes"})
                    for k in ("params", "url", "url_not_in"):
                        e.pop(k, None)
                    demoted.append(e)
                else:
                    keep.append(e)
            m["presets"], m["gaps"] = keep, m["gaps"] + demoted
        if chosen:
            # Rebuild proxy rows on the confirmed form (it may be several fragments).
            for e in m["presets"]:
                if e.get("proxy"):
                    params = []
                    for p in e["params"]:
                        params += chosen.split("&") if p in PROXY_CANDIDATES else [p]
                    # de-duplicate while keeping order (Absentee + proxy-with-absentee)
                    e["params"] = list(dict.fromkeys(params))
                    e["url"] = build_url(loc, e["params"] + BUY_BOX)
                    e["url_not_in"] = build_url(loc, e["params"] + BUY_BOX + [NOT_IN])

        for e in m["presets"] + ([m["tier2"]] if m.get("tier2") else []):
            c = await count(e["url"])
            n = await count(e["url_not_in"])
            e["measured_count"] = c
            e["measured_not_in_account"] = n
            if c is None:
                e["measure_status"] = "no_count"
            elif c == base:
                e["measure_status"] = "IGNORED_PARAM"
                e["buildable"] = False
                e["reason"] = ("returned the unfiltered single-family baseline; a parameter "
                               "was silently ignored -- do not save this preset")
            elif c == 0:
                e["measure_status"] = "measured_zero"
            else:
                e["measure_status"] = "ok"
            print(f"  {e['name'][:60]:60s} {str(c):>8s}  not-in-account {str(n):>8s}  "
                  f"{e['measure_status']}")

        bad = [e for e in m["presets"] if e.get("measure_status") == "IGNORED_PARAM"]
        if bad:
            m["gaps"] += bad
            m["presets"] = [e for e in m["presets"] if e.get("measure_status") != "IGNORED_PARAM"]
        m["measured_at"] = datetime.now().isoformat(timespec="seconds")


def report(m: dict) -> None:
    print(f"\n=== SIFTMAP PRESET MANIFEST  {m['jurisdiction']} ({m['fips']}) ===")
    print(f"source {m['source']}   presets {len(m['presets'])}   gaps {len(m['gaps'])}   "
          f"tier2 {'yes' if m.get('tier2') else 'no'}")
    print(f"proxy: {m['proxy']['status']} ({m['proxy'].get('param')})")
    print(f"\n  {'preset':62s} {'lift':>6s} {'wbook':>6s} {'count':>7s} {'not_in':>7s}  status")
    for e in m["presets"]:
        print(f"  {e['name'][:62]:62s} {str(e['lift']):>6s} "
              f"{str(int(e['workbook_list_size']) if e['workbook_list_size'] else '-'):>6s} "
              f"{str(e.get('measured_count', '-')):>7s} "
              f"{str(e.get('measured_not_in_account', '-')):>7s}  "
              f"{e.get('measure_status', 'unmeasured')}")
    if m.get("tier2"):
        t = m["tier2"]
        print(f"  {t['name'][:62]:62s} {'-':>6s} {'-':>6s} "
              f"{str(t.get('measured_count', '-')):>7s} "
              f"{str(t.get('measured_not_in_account', '-')):>7s}  "
              f"{t.get('measure_status', 'unmeasured')}")
    print("\n  Not buildable in SiftMap (routed to first-to-market):")
    for g in m["gaps"]:
        print(f"    P{g['priority']}-{g['rank']:02d} {g['signal'][:44]:44s} "
              f"{str(g['lift']):>6s}x  {g.get('reason')}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build (and size) a county's SiftMap presets")
    ap.add_argument("--fips", required=True)
    ap.add_argument("--measure", action="store_true", help="open every URL and record counts")
    ap.add_argument("--out")
    a = ap.parse_args()

    if a.fips not in BY_FIPS:
        raise SystemExit(f"FIPS {a.fips} is not a target jurisdiction")
    src = ROOT / "data" / f"dpd_playbook_{a.fips}.json"
    if not src.exists():
        raise SystemExit(f"missing {src}; run dpd_playbook_extract.py --fips {a.fips} first")
    out_path = Path(a.out) if a.out else ROOT / "data" / f"dpd_siftmap_manifest_{a.fips}.json"

    pb = json.loads(src.read_text(encoding="utf-8"))
    m = build(pb)
    if a.measure:
        asyncio.run(measure(m))
    elif out_path.exists():
        # Preserve earlier measurements on a rebuild without --measure.
        old = json.loads(out_path.read_text(encoding="utf-8"))
        prev = {e["name"]: e for e in old.get("presets", []) + old.get("gaps", [])}
        if old.get("tier2"):
            prev[old["tier2"]["name"]] = old["tier2"]
        for e in m["presets"] + ([m["tier2"]] if m.get("tier2") else []):
            p = prev.get(e["name"]) or {}
            for k in ("measured_count", "measured_not_in_account", "measure_status"):
                if k in p:
                    e[k] = p[k]
        for k in ("baseline_single_family", "measured_at"):
            if k in old:
                m[k] = old[k]
        if old.get("proxy", {}).get("status") != "unmeasured":
            m["proxy"] = old["proxy"]
    report(m)
    out_path.write_text(json.dumps(m, indent=1), encoding="utf-8")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
