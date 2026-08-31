"""Render data/dpd_playbook_<fips>.json into a readable per-county markdown playbook.

    python src/scripts/dpd_playbook_render.py            # all 14
    python src/scripts/dpd_playbook_render.py --fips 24005

Read-only against the data. Writes output/dpd_playbooks/<fips>_<slug>.md plus an
INDEX.md. The JSON stays the machine-readable source of truth; this is the human
view of the same numbers, nothing recomputed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dpd.jurisdictions import BY_FIPS  # noqa: E402

OUT = ROOT / "output" / "dpd_playbooks"

# Which signals SiftMap can actually filter on, from the confirmed vocabulary in
# data/dpd_buildability.json plus the Tired Landlord proxy that the DC run pinned down
# (absentee + owned 10+ years, landing 0.2% off the workbook's standalone list).
# A high-lift row built on a signal that is NOT here cannot be pulled from SiftMap at
# all, so the lift is real but unreachable, and it routes to a first-to-market pull.
PROXY_SIGNAL = "Tired Landlord"


def buildable_signals() -> dict[str, str]:
    b = json.loads((ROOT / "data" / "dpd_buildability.json").read_text(encoding="utf-8"))
    m = dict(b["signal_to_param"])
    m[PROXY_SIGNAL] = "preset_absentee_owners=true&extra_years_owned_min=10 (proxy)"
    return m


# Signals that come from the Foreclosure Filters layer. The filter is confirmed working,
# but FILTERABLE IS NOT THE SAME AS AVAILABLE: in a county whose provider foreclosure
# verdict is low/unknown, the filter returns almost nothing (live: Lis Pendens 14 records
# in Baltimore County out of 303,888, Notice of Foreclosure 1). Flag, never absorb.
FORECLOSURE_SIGNALS = {"Notice of Default", "Notice of Foreclosure", "Lis Pendens"}
THIN_VERDICTS = {"low", "unknown", "unpriced", "no_provider"}

# The provider verdict does NOT predict the notice-type filter's yield: the preset and
# the notice-type filter are not equivalent instruments. Fairfax is rated `low` yet its
# ND filter returns 169; Baltimore County is also `low` and returns ~1. So a `low` county
# needs a live count, not an assumption. These are the counts actually measured live
# (recorded in CLAUDE.md Phase 2d); anything not here is genuinely unverified.
LIVE_NOTICE_COUNTS: dict[tuple[str, str], int] = {
    ("51059", "Notice of Default"): 169,
    ("24005", "Lis Pendens"): 14,
    ("24005", "Notice of Foreclosure"): 1,
}
LIVE_MIN = 50   # below this a row cannot carry a tier on its own


def row_modelled(r: dict) -> bool:
    """A row carrying a doors/deal but no deal count and no evidence letter is a MODEL
    ESTIMATE, not a measurement. The Obituary row is the only one in the whole set, and it
    prints dpd 34 in all 14 counties regardless of a baseline ranging 76.8 to 486.6 - its
    lift varies only because the baseline does. It ranks P1 everywhere on that basis, so
    it has to be labelled or it reads as the top measured row in several counties."""
    return r.get("dpd") is not None and r.get("deals") is None and not r.get("evidence")


def row_buildable(r: dict, ok: dict[str, str]) -> tuple[bool, list[str]]:
    """AI-score rows are excluded: the filter works but this account has not bought
    AI data, so every property scores empty (see Phase 2f)."""
    if r.get("type") == "AI score":
        return False, ["AI data not purchased"]
    missing = [s for s in (r.get("signals") or []) if s not in ok]
    return (not missing), missing


def foreclosure_verdict(d: dict) -> str | None:
    verd = ((d.get("siftmap_gaps") or {}).get("from_shard_ftm_verdicts")
            or (d.get("shard_extra") or {}).get("ftm_verdicts") or [])
    for v in verd:
        if v.get("k") == "foreclosure":
            return v.get("v")
    return None


def row_data_status(r: dict, fips: str, fc_verdict: str | None) -> tuple[str, str]:
    """('ok'|'proven'|'empty'|'verify', note) for a row that passes the filter test."""
    sigs = FORECLOSURE_SIGNALS & set(r.get("signals") or [])
    if not sigs:
        return "ok", ""
    live = [LIVE_NOTICE_COUNTS.get((fips, s)) for s in sorted(sigs)]
    if all(v is not None for v in live):
        n = min(live)
        if n >= LIVE_MIN:
            return "proven", f"measured live: {n} records"
        return "empty", f"measured live: {n} records"
    if fc_verdict in THIN_VERDICTS:
        return "verify", f"provider {fc_verdict or 'unknown'}, no live count yet"
    return "ok", ""


def money(v):
    return "-" if v is None else f"${v:,.0f}"


def num(v, dp=1):
    if v is None:
        return "-"
    if dp == 0:
        return f"{v:,.0f}"
    return f"{v:,.{dp}f}".rstrip("0").rstrip(".")


def pct(v):
    return "-" if v is None else f"{v * 100:.0f}%"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def render(fips: str, ok: dict[str, str]) -> Path:
    d = json.loads((ROOT / "data" / f"dpd_playbook_{fips}.json").read_text(encoding="utf-8"))
    j = BY_FIPS[fips]
    ov = d.get("overview") or {}
    ex = d.get("shard_extra") or {}
    L: list[str] = []

    L.append(f"# {j.name} - doors-per-deal playbook")
    L.append("")
    L.append(f"FIPS {fips} | {ov.get('Foreclosure regime', '-')} foreclosure regime | "
             f"window {ov.get('Data window', '-')}")
    L.append("")
    origin = "workbook + shard" if d.get("source_xlsx") else "playbook JSON shard"
    L.append(f"Source: `data/dpd_playbook_{fips}.json` ({origin}). "
             f"Live page: https://learn.datasift.ai/county-list-playbook#{fips}")
    L.append("")

    # ---- at a glance
    cov = ex.get("coverage") or {}
    L.append("## At a glance")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Investor purchases (6mo) | {num(ov.get('Investor purchases (6mo)'), 0)} |")
    L.append(f"| Baseline doors/deal, whole county | {num(ov.get('Baseline doors/deal (whole county)'))} |")
    L.append(f"| County SFR supply | {num(ov.get('County SFR supply'), 0)} |")
    L.append(f"| Typical built-in equity | {money(ov.get('Typical built-in equity (median gross)'))} |")
    if cov:
        L.append(f"| Deals reachable via P1 rows | {pct(cov.get('p1'))} |")
        L.append(f"| Deals reachable via P1 + P2 | {pct(cov.get('p1p2'))} |")
        L.append(f"| Deals reachable via all ranked rows | {pct(cov.get('all'))} |")
    if ex.get("obit_supply") is not None:
        L.append(f"| Obituary / deceased-owner supply | {num(ex.get('obit_supply'), 0)} |")
    L.append("")

    # ---- priority ranking
    rows = d.get("rows") or []
    fc = foreclosure_verdict(d)
    nulls = [r for r in rows if r.get("dpd") is None]
    L.append("## Priority ranking")
    L.append("")
    L.append("Doors/Deal is live list size divided by the deals that list produced in the window, "
             "so lower is better. Lift is baseline doors/deal divided by the row's. "
             "Evidence A is this county's own sold deals, B is a cross-market benchmark, "
             "C is a single-market anchor.")
    L.append("")
    L.append("**SiftMap** says whether the row can be pulled from SiftMap today. A `no` is not a "
             "bad row, it is an unreachable one: the lift is real but the signal is absent from "
             "SiftMap's filter surface, so the row routes to a first-to-market county pull instead. "
             "The two middle states are the trap. `VERIFY LIVE` means the filter works but this "
             f"county's provider foreclosure coverage is rated `{fc or 'unknown'}` and no live "
             "count has been taken, so the list size beside it is the workbook's, not SiftMap's. "
             "`filter only` means a live count WAS taken and came back near zero. The provider "
             "verdict alone does not settle it either way: Fairfax is rated low and its Notice of "
             "Default filter returns 169 records, Baltimore County is rated low and returns 1.")
    L.append("")
    tiers = ((1, "Priority 1 - lift 3x baseline or better, pull these first"),
             (2, "Priority 2 - lift 1.5x or better, strong second wave"),
             (3, "Priority 3 - coverage plays and caution rows"))
    for p, label in tiers:
        sel = [r for r in rows if r.get("priority") == p]
        L.append(f"### {label}")
        L.append("")
        if not sel:
            L.append("_No rows at this priority._")
            L.append("")
            continue
        L.append("| # | Signal | Type | Doors/Deal | Lift | Deals | List size | Typical gross "
                 "| Ev | Conf | SiftMap |")
        L.append("|---|---|---|---:|---:|---:|---:|---:|:-:|:-:|---|")
        for r in sel:
            sig = r.get("signal")
            if row_modelled(r):
                sig = f"{sig} (modelled)"
            can, miss = row_buildable(r, ok)
            if not can:
                mark = f"no - {', '.join(miss)}"
            else:
                st, note = row_data_status(r, fips, fc)
                mark = {"ok": "yes", "proven": f"yes ({note})",
                        "empty": f"filter only ({note})",
                        "verify": f"VERIFY LIVE ({note})"}[st]
            L.append(f"| {r.get('rank')} | {sig} | {r.get('type') or '-'} | "
                     f"{num(r.get('dpd'))} | {num(r.get('lift'))}x | {num(r.get('deals'), 0)} | "
                     f"{num(r.get('list_size'), 0)} | {money(r.get('typical_gross'))} | "
                     f"{r.get('evidence') or '-'} | {r.get('conf') or '-'} | {mark} |")
        L.append("")
        mod = [r for r in sel if row_modelled(r)]
        if mod:
            L.append("> Rows marked **(modelled)** carry a doors/deal the playbook estimated "
                     "rather than measured: no deal count, no evidence letter, and the same "
                     "figure in every county. The lift moves only because the baseline moves. "
                     "Take the list size as real and the ranking as a hypothesis to test.")
            L.append("")
        notes = [r for r in sel if r.get("caveats")]
        if notes:
            L.append("Caveats:")
            L.append("")
            for r in notes:
                L.append(f"- **{r['signal']}** - {r['caveats']}")
            L.append("")

    if nulls:
        names = ", ".join(r["signal"] for r in nulls)
        L.append(f"> **{len(nulls)} row(s) carry no measurement at all** - no doors/deal, no lift, "
                 f"no list size: {names}. The signal is named for this county but was never "
                 f"measured here. Do not build a tier on one of these without an independent count.")
        L.append("")

    # ---- capture ladder
    ladder = (ex.get("capture_ladder") or {}).get("variants") or {}
    lists = ladder.get("lists") or []
    if lists:
        L.append("## Capture ladder")
        L.append("")
        L.append("Work top to bottom. Each row's marginal numbers are what it adds ON TOP of "
                 "everything above it, so cumulative share is the fraction of the county's "
                 "investor deals reachable once you have pulled down to that row.")
        L.append("")
        L.append("| Signal | Plan | Pri | Marg. list | Marg. deals | Marg. D/D | Cum. list | Cum. share |")
        L.append("|---|:-:|:-:|---:|---:|---:|---:|---:|")
        for v in lists:
            flag = " (thin)" if v.get("thin") else (" (est.)" if v.get("est") else "")
            L.append(f"| {v.get('label')}{flag} | {v.get('plan') or '-'} | {v.get('priority') or '-'} | "
                     f"{num(v.get('marg_supply'), 0)} | {num(v.get('marg_deals'), 0)} | "
                     f"{num(v.get('marg_dpd'))} | {num(v.get('cum_supply'), 0)} | "
                     f"{pct(v.get('cum_share'))} |")
        L.append("")

    # ---- zips
    zd = {z["zip"]: z for z in (ex.get("zips_detail") or [])}
    zips = d.get("top_zips") or []
    if zips:
        L.append("## Rated ZIP codes")
        L.append("")
        L.append("Momentum above 1.0 means the back half of the window outpaced the front. "
                 "Med AVM and Med buy are the median value and the median investor purchase price.")
        L.append("")
        L.append("| ZIP | Stars | Deals | Doors/Deal | Typical gross | Momentum | Med AVM | Med buy | List size |")
        L.append("|---|:-:|---:|---:|---:|---:|---:|---:|---:|")
        for z in zips:
            e = zd.get(z["zip"]) or {}
            stars = "*" * (z.get("stars") or 0)
            L.append(f"| {z['zip']} | {stars} | {num(z.get('deals'), 0)} | "
                     f"{num(z.get('dpd'))} | {money(z.get('typical_gross'))} | "
                     f"{num(z.get('momentum'), 2)} | {money(e.get('med_avm'))} | "
                     f"{money(e.get('med_buy'))} | {num(e.get('list_size'), 0)} |")
        L.append("")

    # ---- price bands
    bands = d.get("price_bands") or []
    if bands:
        L.append("## Price bands")
        L.append("")
        L.append("| Band | Deals | List size | Doors/Deal |")
        L.append("|---|---:|---:|---:|")
        for b in bands:
            L.append(f"| {b.get('band')} | {num(b.get('deals'), 0)} | "
                     f"{num(b.get('list_size'), 0)} | {num(b.get('dpd'))} |")
        L.append("")

    # ---- siftmap coverage
    gaps = d.get("siftmap_gaps") or {}
    verd = gaps.get("from_shard_ftm_verdicts") or ex.get("ftm_verdicts")
    L.append("## SiftMap (provider) coverage")
    L.append("")
    if isinstance(verd, list) and verd:
        L.append("| Data type | Verdict | Records | List size | Deals | Recommended county source |")
        L.append("|---|:-:|---:|---:|---:|---|")
        for v in verd:
            L.append(f"| {v.get('k')} | {v.get('v')} | {num(v.get('rec'), 0)} | "
                     f"{num(v.get('ls'), 0)} | {num(v.get('deals'), 0)} | {v.get('src') or '-'} |")
        L.append("")
        L.append("`no_provider` and `low` mean the data cannot be bought here, so the county "
                 "office below is not a backup, it is the only way in.")
        L.append("")
    else:
        if gaps.get("low_or_zero"):
            L.append(f"- Low or zero provider data: {', '.join(gaps['low_or_zero'])}")
        if gaps.get("absent"):
            L.append(f"- Absent entirely: {', '.join(gaps['absent'])}")
        L.append("")

    # ---- FTM offices
    ftm = d.get("ftm") or []
    if ftm:
        L.append("## First to market - county offices")
        L.append("")
        for r in ftm:
            L.append(f"### {r.get('data_type')}")
            L.append("")
            L.append(f"{r.get('priority')} | access {r.get('access')} | "
                     f"updates {r.get('updates')} | {r.get('verified')}")
            L.append("")
            L.append(f"- **Office:** {r.get('office_official') or '-'}")
            L.append(f"- **Phone:** {r.get('phone') or '-'}")
            L.append(f"- **URL:** {r.get('source_url') or '-'}")
            L.append(f"- **Address:** {r.get('office_address') or '-'}")
            L.append(f"- **Records / FOIA:** {r.get('records_foia') or '-'}")
            if r.get("notes"):
                L.append(f"- **Notes:** {r['notes']}")
            L.append("")

    for nar in d.get("ftm_narrative") or []:
        L.append(f"> {nar}")
        L.append("")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{fips}_{slug(j.name)}.md"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Render per-county doors-per-deal playbooks")
    ap.add_argument("--fips", help="one county; default is all 14")
    a = ap.parse_args()
    todo = [a.fips] if a.fips else sorted(BY_FIPS)
    ok = buildable_signals()
    idx = ["# Doors-per-deal playbooks", "",
           "One file per jurisdiction, rendered from `data/dpd_playbook_<fips>.json` by "
           "`src/scripts/dpd_playbook_render.py`.", "",
           "**Best measured** is the highest-lift row in the county. **Best pullable** is the "
           "highest-lift row SiftMap can both filter AND actually return records for. Where they "
           "differ the gap is either a signal SiftMap has no filter for (HOA Lien, Other Lien, "
           "Bad Credit, Low Income, Bankruptcy, Estate Sale, Probate), an AI-score row (AI data "
           "not purchased on this account), or a foreclosure-notice row in a county whose provider "
           "coverage is rated low - filterable but empty. All three route to a first-to-market "
           "county pull instead.", "",
           "The **unproven** column counts rows that pass the filter test but whose data "
           "availability here is either unmeasured or measured near zero. Those are the rows most "
           "likely to be mistaken for a win, and each county file names them individually.", "",
           "| Jurisdiction | FIPS | Rows | P1 | Best measured | Best pullable | Top pullable row "
           "| Unproven |",
           "|---|---|---:|---:|---:|---:|---|---:|"]
    for f in todo:
        src = ROOT / "data" / f"dpd_playbook_{f}.json"
        if not src.exists():
            print(f"  SKIP {f}: no {src.name}")
            continue
        p = render(f, ok)
        d = json.loads(src.read_text(encoding="utf-8"))
        rows = d.get("rows") or []
        lifts = [r["lift"] for r in rows
                 if r.get("lift") is not None and not row_modelled(r)]
        best = max(lifts) if lifts else None
        fc = foreclosure_verdict(d)
        filt = [r for r in rows if r.get("lift") is not None
                and not row_modelled(r) and row_buildable(r, ok)[0]]
        st = {id(r): row_data_status(r, f, fc)[0] for r in filt}
        thin = [r for r in filt if st[id(r)] in ("verify", "empty")]
        cands = [r for r in filt if st[id(r)] in ("ok", "proven")]
        top = max(cands, key=lambda r: r["lift"]) if cands else None
        n1 = sum(1 for r in rows if r.get("priority") == 1)
        row_lbl = (f"[{top['signal']}]({p.name}) (list {num(top.get('list_size'), 0)})"
                   if top else "-")
        idx.append(f"| [{BY_FIPS[f].name}]({p.name}) | {f} | {len(rows)} | {n1} | "
                   f"{('%gx' % best) if best else 'none'} | "
                   f"{('%gx' % top['lift']) if top else 'none'} | {row_lbl} | {len(thin)} |")
        print(f"  wrote {p.relative_to(ROOT)}")
    if not a.fips:
        (OUT / "INDEX.md").write_text("\n".join(idx) + "\n", encoding="utf-8")
        print(f"  wrote {(OUT / 'INDEX.md').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
