"""Join the SiftMap auto-add census to the manifests: what would each saved filter add?

    python src/scripts/dpd_siftmap_add_exposure.py
    python src/scripts/dpd_siftmap_add_exposure.py --census output/dpd_siftmap_presets_account_audit.json

Reads the census written by `dpd_siftmap_presets.py --audit-auto-add` and the six
footprint manifests (`data/dpd_siftmap_manifest_<fips>.json`), and writes
output/dpd_siftmap_add_exposure_<ts>.md: per county, the pull ladder (P1 by rank ->
P2 -> T2) with `measured_not_in_account` (= what a manual "Add Records to Account"
pull would import), each filter's live auto-add state, and the recommended first
pulls (highest-lift distress rungs).

Read-only and offline: touches no browser and adds nothing to the account.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# footprint (2026-09-01 narrowing); ladder presentation order
FOOTPRINT = [("11001", "DC"), ("24031", "Montgomery"), ("51059", "Fairfax"),
             ("24021", "Frederick"), ("51013", "Arlington"), ("24003", "Anne Arundel")]


def load_manifest_rows() -> list[dict]:
    rows = []
    for fips, county in FOOTPRINT:
        mp = ROOT / "data" / f"dpd_siftmap_manifest_{fips}.json"
        man = json.loads(mp.read_text(encoding="utf-8"))
        measured_at = man.get("measured_at")
        for p in man.get("presets", []):
            rows.append({**p, "county": county, "fips": fips, "band": _band(p),
                         "measured_at": measured_at})
        if man.get("tier2"):
            rows.append({**man["tier2"], "county": county, "fips": fips, "band": 2,
                         "priority": "T2", "rank": 999, "measured_at": measured_at})
    return rows


def _band(p: dict) -> int:
    pr = str(p.get("priority") or "")
    if pr in ("1", "P1"):
        return 0
    if pr in ("2", "P2"):
        return 1
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--census", default=str(ROOT / "output" / "dpd_siftmap_presets_account_audit.json"))
    ap.add_argument("--fresh", help="dpd_siftmap_fresh_measure output JSON; overlays "
                                    "measured_count / measured_not_in_account with the "
                                    "fresh reads (rows it did not measure keep the old "
                                    "counts, labelled stale)")
    a = ap.parse_args()

    census = json.loads(Path(a.census).read_text(encoding="utf-8"))
    audited = census.get("results") or []
    manifest_rows = load_manifest_rows()

    fresh_at = None
    if a.fresh:
        fm = json.loads(Path(a.fresh).read_text(encoding="utf-8"))
        fresh_at = fm.get("at")
        by_name = {r["name"]: r for r in fm.get("results", [])}
        for m in manifest_rows:
            fr = by_name.get(m["name"])
            if fr and fr.get("status") in ("ok", "measured_zero"):
                m["measured_count"] = fr["fresh_count"]
                m["old_not_in"] = m.get("measured_not_in_account")
                m["measured_not_in_account"] = fr["fresh_not_in"]
                m["fresh"] = True

    # census titles concatenate name + description; match manifests by prefix
    def find_audit(name: str) -> dict | None:
        return next((r for r in audited if (r.get("title") or "").startswith(name)), None)

    live = []
    for m in manifest_rows:
        au = find_audit(m["name"])
        if au is None:
            continue  # deleted from the account (out-of-footprint / P2 purge)
        live.append({**m, "auto_add": au.get("auto_add"), "evidence": au.get("evidence")})

    matched_titles = {m["name"] for m in live}
    unmatched = [r for r in audited
                 if not any((r.get("title") or "").startswith(n) for n in matched_titles)]

    def aa(v) -> str:
        return {True: "**ON**", False: "off", None: "?"}[v]

    lines = [f"# SiftMap add exposure -- {datetime.now():%Y-%m-%d %H:%M}", ""]
    lines.append(f"Census: `{Path(a.census).name}` ({census.get('at')}); "
                 f"{len(audited)} filters audited of {census.get('total_reported')} reported.")
    if fresh_at:
        n_fresh = sum(1 for m in manifest_rows if m.get("fresh"))
        lines.append(f"Counts: FRESH measure {fresh_at} for {n_fresh} filters "
                     "(rows without a fresh read keep the 08-27/08-31 numbers and say so).")
    lines.append("`would add` = `measured_not_in_account`"
                 + ("" if fresh_at else " (DC measured 2026-08-27, others "
                    "2026-08-31 -- ~1 week stale; the account has gained ~2K records since)")
                 + ". "
                 "Naive sums overlap heavily (P1 combos are subsets of their parts; each T2 "
                 "overlaps everything in-county): pulled in ladder order with "
                 "`in_my_account_mode=not_in`, each rung excludes what earlier rungs added.")
    lines.append("")

    on_rows = [m for m in live if m["auto_add"] is True]
    on_unmatched = [r for r in unmatched if r.get("auto_add") is True]
    lines.append("## Auto-add enabled")
    lines.append("")
    if not on_rows and not on_unmatched:
        lines.append("No live filter has auto-add enabled per the census.")
    else:
        lines.append("These drip NEW matches into the account as they appear "
                     "(auto-add never backfills):")
        lines.append("")
        lines.append("| Filter | County | would add (manual pull) | new-match drip |")
        lines.append("|---|---|---|---|")
        for m in sorted(on_rows, key=lambda r: -(r.get("measured_not_in_account") or 0)):
            lines.append(f"| {m['name']} | {m['county']} | "
                         f"{m.get('measured_not_in_account'):,} | ongoing |")
        for r in on_unmatched:
            lc = r.get("live_count")
            lines.append(f"| {r.get('title')} | ? | {lc if lc is not None else '?'} | ongoing |")
    lines.append("")

    lines.append("## Recommended first pulls (highest lift, trivial volume)")
    lines.append("")
    lines.append("| # | Filter | Lift | would add |")
    lines.append("|---|---|---|---|")
    first = sorted([m for m in live if (m.get("lift") or 0) >= 20
                    and (m.get("measured_not_in_account") or 0) > 0],
                   key=lambda r: -(r.get("lift") or 0))
    for i, m in enumerate(first, 1):
        lines.append(f"| {i} | {m['name']} | {m['lift']}x | {m['measured_not_in_account']:,} |")
    lines.append("")

    lines.append("## The full pull ladder")
    lines.append("")
    for _fips, county in FOOTPRINT:
        cr = sorted([m for m in live if m["county"] == county],
                    key=lambda r: (r["band"], r.get("rank") if r.get("rank") is not None else 998))
        if not cr:
            continue
        tot = sum(m.get("measured_not_in_account") or 0 for m in cr)
        lines.append(f"### {county} -- {len(cr)} filters, naive would-add sum {tot:,}")
        lines.append("")
        lines.append("| Filter | Prio | Rank | Lift | dpd | would add | auto-add |")
        lines.append("|---|---|---|---|---|---|---|")
        for m in cr:
            ni = m.get("measured_not_in_account")
            shown = f"{ni:,}" if ni is not None else "?"
            if fresh_at and not m.get("fresh"):
                shown += " (stale)"
            elif m.get("fresh") and m.get("old_not_in") is not None and ni is not None:
                d = ni - m["old_not_in"]
                shown += f" ({'+' if d >= 0 else ''}{d})"
            lines.append(f"| {m['name']} | {m.get('priority')} | {m.get('rank')} | "
                         f"{m.get('lift')} | {m.get('dpd')} | {shown} | {aa(m['auto_add'])} |")
        lines.append("")

    if unmatched:
        lines.append("## Filters no manifest knows")
        lines.append("")
        for r in unmatched:
            lc = r.get("live_count")
            lines.append(f"- {r.get('title')} -- auto-add {aa(r.get('auto_add'))}, "
                         f"live count {lc if lc is not None else 'unknown'}"
                         + (f" ({r.get('live_count_error')})" if r.get("live_count_error") else ""))
        lines.append("")

    deleted = [m["name"] for m in manifest_rows if find_audit(m["name"]) is None]
    lines.append(f"_{len(deleted)} manifest presets are no longer live (deleted in the "
                 "2026-09-07 narrowing / P2 purge) and are excluded above._")
    lines.append("")

    out_path = ROOT / "output" / f"dpd_siftmap_add_exposure_{datetime.now():%Y%m%dT%H%M%S}.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"live filters matched to manifests: {len(live)}; unmatched in census: {len(unmatched)}; "
          f"auto-add ON: {len(on_rows) + len(on_unmatched)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
