"""Fold the widen search's chosen T1s back into the canonical tier config.

Phase 3 sized all 28 tier configs against live counts; six counties' T1 did not land in
the 200-500 band and `dpd_widen_search.py` found a shippable stack for each. Those six
answers lived only in `data/dpd_widen_search.json`, so `data/dpd_tier_configs.json` still
named the SUPERSEDED stack for them -- and Phase 4 reads the tier config. One artifact has
to be authoritative or the pull runs the wrong stack for six of fourteen counties.

    python src/scripts/dpd_fold_widen.py            # dry run, prints the diff
    python src/scripts/dpd_fold_widen.py --commit   # rewrite data/dpd_tier_configs.json

Additive and idempotent: the superseded stack is kept under T1.superseded, every measured
candidate the search rejected is kept under T1.high_lift_slices (that is the decision
material for whether a small high-conviction slice rides alongside the widened tier), and
re-running produces the same file.

DO NOT fold by re-running `dpd_tier_configs.py` -- its main() calls build() from scratch and
only writes measured counts when --measure is passed, so a plain run silently discards
every live count in the file. This script edits the artifact in place instead.

Read-only against the account: it touches no browser and measures nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

CONFIGS = ROOT / "data" / "dpd_tier_configs.json"
WIDEN = ROOT / "data" / "dpd_widen_search.json"

T1_LO, T1_HI = 200, 500


def _in_band(n) -> bool:
    return isinstance(n, int) and T1_LO <= n <= T1_HI


def cross_check(cfgs: dict, widen: dict) -> list[dict]:
    """Do the two artifacts describe the same measurements?

    Timestamps cannot answer this. The widen file's `merged_with` is the PREVIOUS widen
    run's stamp, not the tier build it measured against, and the tier config's
    `generated_at` is stamped at build time while the file is written after a --measure
    pass that runs for many minutes -- so the two never match even when both are correct.

    The honest check is arithmetic instead: the widen search re-measured each county's
    NAMED stack on its way down the lift ranking, so where the same stack appears in both
    files their stack-only counts must agree. A disagreement means one of the files was
    measured against a different account state or a different filter vocabulary.
    """
    out = []
    for name, w in widen["counties"].items():
        cfg = cfgs["counties"].get(name)
        if cfg is None:
            continue
        t1 = cfg["T1"]
        sv = t1["variants"].get("stack_only") or {}
        mine = sv.get("measured_count")
        # `stack_only` measures the stack that was named when it was MEASURED, which after
        # a fold is the superseded one, not T1.stack. Comparing it against the new name
        # would report every already-folded county as a mismatch on a re-run.
        stack = sv.get("measures_stack") or (t1.get("superseded") or {}).get("stack") \
            or t1["stack"]
        theirs = {t.get("signal"): t.get("stack_only_count") for t in w.get("tried", [])}
        if stack not in theirs or mine is None:
            out.append({"county": name, "status": "no_overlap", "stack": stack})
            continue
        got = theirs[stack]
        out.append({
            "county": name, "stack": stack, "config": mine, "widen": got,
            "status": "agree" if got == mine else "disagree",
        })
    return out


def fold(cfgs: dict, widen: dict, force: bool = False) -> list[dict]:
    """Return one row per county describing what the fold did. Mutates `cfgs`."""
    bad = [c for c in cross_check(cfgs, widen) if c["status"] == "disagree"]
    if bad and not force:
        lines = "\n".join(
            f"    {c['county']}: {c['stack']} reads {c['config']} in the tier config but "
            f"{c['widen']} in the widen search" for c in bad)
        raise SystemExit(
            "the two artifacts disagree on a stack they both measured, so they do not "
            f"describe the same account state:\n{lines}\n"
            "Re-measure before folding, or pass --force if you know why they differ."
        )

    rows = []
    for name, w in widen["counties"].items():
        cfg = cfgs["counties"].get(name)
        if cfg is None:
            rows.append({"county": name, "action": "skipped",
                         "why": "not in the tier config"})
            continue

        t1 = cfg["T1"]
        if w.get("status") != "found":
            rows.append({"county": name, "action": "skipped",
                         "why": f"widen status {w.get('status')!r}"})
            continue

        ch = w["choice"]
        count = ch.get("chosen_count")
        if not _in_band(count):
            # Never promote a stack the search itself did not land in band. Saying so beats
            # writing an out-of-band tier into the artifact Phase 4 will trust.
            rows.append({"county": name, "action": "refused", "signal": ch.get("signal"),
                         "count": count,
                         "why": f"chosen_count {count} is outside {T1_LO}-{T1_HI}"})
            continue

        # Keep the stack this replaces, with its own measured count, so the trade is
        # visible in the same file rather than only in the widen artifact.
        prev_chosen = t1.get("chosen")
        prev_count = (t1["variants"].get(prev_chosen) or {}).get("measured_count")
        superseded = {
            "stack": t1["stack"],
            "signals": t1.get("signals"),
            "lift": t1.get("lift"),
            "workbook_list_size": t1.get("workbook_list_size"),
            "measured_count": prev_count,
            "why_superseded": (t1.get("sizing") or {}).get("reason"),
        }
        # Idempotent: a re-run must not record the widened stack as its own predecessor.
        if t1.get("superseded") and t1.get("chosen") == "widened":
            superseded = t1["superseded"]

        variant = {
            "label": ("widened stack, top %d neighbourhoods" % ch["depth"]
                      if ch.get("depth") else "widened stack, county-wide"),
            "params": ch["params"],
            "url": ch["url"],
            "measured_count": count,
        }
        if ch.get("neighborhoods"):
            variant["neighborhoods"] = ch["neighborhoods"]
        if ch.get("depth") is not None:
            variant["depth"] = ch["depth"]

        # The pre-existing variants measure the SUPERSEDED stack, not the one T1 now names.
        # Stamp each with the stack it actually measures so nothing downstream reads a
        # stack_only count as belonging to the widened tier.
        for vn, vv in t1["variants"].items():
            if vn != "widened":
                vv.setdefault("measures_stack", superseded["stack"])

        t1["variants"]["widened"] = variant
        t1["chosen"] = "widened"
        t1["superseded"] = superseded
        t1["stack"] = ch["signal"]
        t1["signals"] = [s.strip() for s in ch["signal"].split("+")]
        t1["lift"] = ch.get("lift")
        t1["workbook_list_size"] = ch.get("workbook_list_size")
        t1["sizing"] = {
            "state": "in_band",
            "action": "none",
            "count": count,
            "depth": ch.get("depth"),
            "source": "dpd_widen_search",
            "reason": (
                f"the named stack ({superseded['stack']}) measured "
                f"{superseded['measured_count']}, outside the band; the widen search took "
                f"{ch['signal']} at {ch.get('lift')}x"
                + (f", cut to its top {ch['depth']} neighbourhoods" if ch.get("depth") else "")
                + f", measuring {count}"
            ),
        }
        if not ch.get("lift"):
            # Fredericksburg City's pick is not among the four stacks the workbook ranks
            # for it, so its lift is genuinely unknown rather than zero.
            t1["sizing"]["lift_caveat"] = (
                "this stack carries no workbook lift for this county -- it was taken from "
                "the buildable set, so its lift here is unevidenced"
            )

        # Every candidate the search measured and rejected, highest lift first. These are
        # the highest-conviction records in the county; whether they ride alongside the
        # widened tier as a small slice is a decision for the account owner, not a default.
        slices = []
        for t in w.get("tried", []):
            if t.get("signal") == ch.get("signal"):
                continue
            n = t.get("chosen_count", t.get("stack_only_count"))
            slices.append({
                "stack": t.get("signal"),
                "lift": t.get("lift"),
                "workbook_list_size": t.get("workbook_list_size"),
                "measured_count": n,
                "verdict": t.get("verdict"),
                "params": t.get("params"),
                "url": t.get("url"),
            })
        # Only the UNDER-band rejects are slices. A candidate rejected for being over band
        # is not a high-conviction cut, it is simply a broader stack that the chosen one
        # already beats, so carrying it here would misrepresent what is on offer.
        slices = [s for s in slices if isinstance(s["measured_count"], int)
                  and 0 < s["measured_count"] < T1_LO]
        # A slice that beats the chosen T1 on lift AND sits just under the floor is not
        # really a slice, it is a better tier the band rejected on a technicality. 182
        # records against a 200 floor is a rounding difference; 16.5x against 11.1x is not.
        near = int(T1_LO * 0.9)
        for s in slices:
            s["beats_chosen_lift"] = bool(
                s.get("lift") and ch.get("lift") and s["lift"] > ch["lift"])
            s["near_band"] = s["measured_count"] >= near
            if s["beats_chosen_lift"] and s["near_band"]:
                s["note"] = (
                    f"{s['measured_count']} records is {T1_LO - s['measured_count']} short "
                    f"of the {T1_LO} floor while carrying {s['lift']}x against the chosen "
                    f"tier's {ch['lift']}x -- worth considering as the T1 itself, since the "
                    "band is a cadence-capacity rule rather than a quality one"
                )
        slices.sort(key=lambda s: -(s.get("lift") or 0))
        if slices:
            t1["high_lift_slices"] = {
                "note": ("measured stacks the widen search rejected for landing UNDER the "
                         "band. They are not worthless -- they are the highest-conviction "
                         "records in the county. Running one as a small priority slice "
                         "alongside T1 is a decision for the account owner, not a default. "
                         "Counts are single-family and already exclude what the account "
                         "holds, so a single-digit count means the records genuinely are "
                         "not there, not that the filter is wrong."),
                "candidates": slices,
            }

        rows.append({
            "county": name, "action": "folded", "signal": ch["signal"],
            "lift": ch.get("lift"), "count": count, "depth": ch.get("depth"),
            "from_stack": superseded["stack"], "from_lift": superseded["lift"],
            "from_count": superseded["measured_count"],
            "slices": len(slices),
        })

    cfgs.setdefault("folds", [])
    cfgs["folds"] = [f for f in cfgs["folds"] if f.get("source") != "dpd_widen_search"]
    cfgs["folds"].append({
        "source": "dpd_widen_search",
        "searched_at": widen.get("searched_at"),
        "folded_at": datetime.now().isoformat(timespec="seconds"),
        "counties": [r["county"] for r in rows if r["action"] == "folded"],
    })
    return rows


def report(cfgs: dict, rows: list[dict], checks: list[dict]) -> int:
    print("\n=== FOLD WIDEN SEARCH INTO THE TIER CONFIG ===\n")

    print("  Cross-check (stacks both files measured, which must agree):")
    for c in checks:
        if c["status"] == "no_overlap":
            print(f"    {c['county']:26s} no overlap - the widen search never re-measured "
                  f"{c['stack']!r}")
        else:
            mark = "ok " if c["status"] == "agree" else "MISMATCH"
            print(f"    {c['county']:26s} {mark} {c['stack'][:34]:34s} "
                  f"config {c['config']:>6}  widen {c['widen']:>6}")
    print()

    folded = [r for r in rows if r["action"] == "folded"]
    for r in folded:
        d = f" (top {r['depth']} nbrs)" if r.get("depth") else ""
        print(f"  {r['county']:26s} {str(r['from_stack'])[:34]:34s} "
              f"{str(r['from_lift']):>5s}x {str(r['from_count']):>6s}"
              f"  ->  {str(r['signal'])[:34]:34s} {str(r['lift']):>5s}x "
              f"{r['count']:>5d}{d}")
    for r in rows:
        if r["action"] != "folded":
            print(f"  {r['county']:26s} {r['action'].upper()}: {r['why']}")

    print(f"\n  {len(folded)} of {len(rows)} widen answers folded in.")
    slices = sum(r.get("slices", 0) for r in folded)
    print(f"  {slices} rejected high-lift stacks preserved as T1.high_lift_slices.")

    # The whole point of the fold is that every county now names one shippable T1 in the
    # canonical artifact. Verify that against the file rather than assuming it.
    print("\n  Every jurisdiction's T1, as the tier config now states it:")
    bad = []
    for name, cfg in cfgs["counties"].items():
        t1 = cfg["T1"]
        ch = t1.get("chosen")
        n = (t1["variants"].get(ch) or {}).get("measured_count")
        flag = "" if _in_band(n) else "   <-- NOT IN BAND"
        if flag:
            bad.append(name)
        print(f"    {name:26s} {str(ch):10s} {str(t1['stack'])[:36]:36s} "
              f"{str(t1.get('lift')):>5s}x {str(n):>6s}{flag}")
    if bad:
        print(f"\n  {len(bad)} still outside the band: {', '.join(bad)}")
        return 1
    print(f"\n  All {len(cfgs['counties'])} T1s are in band ({T1_LO}-{T1_HI}).")

    # The open decision this fold sets up: these are the stacks each county gave away to
    # get a list big enough to run a cadence on. Print the sizes so the trade is decidable
    # rather than buried in the artifact.
    print("\n  High-lift slices given up to reach the band (the open decision):")
    any_slice = False
    for name, cfg in cfgs["counties"].items():
        hl = cfg["T1"].get("high_lift_slices")
        if not hl:
            continue
        any_slice = True
        chosen_lift = cfg["T1"].get("lift")
        print(f"    {name}  (T1 now {chosen_lift}x)")
        for s in hl["candidates"]:
            mark = "  <-- near band AND higher lift than the chosen T1" \
                if s.get("beats_chosen_lift") and s.get("near_band") else ""
            print(f"        {str(s['stack'])[:40]:40s} {str(s['lift']):>5s}x  "
                  f"{s['measured_count']:>5d} records{mark}")
    if not any_slice:
        print("    (none)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true",
                    help="rewrite data/dpd_tier_configs.json (default is a dry run)")
    ap.add_argument("--force", action="store_true",
                    help="fold even if the widen search measured a different tier build")
    a = ap.parse_args()

    for p in (CONFIGS, WIDEN):
        if not p.exists():
            raise SystemExit(f"missing {p}")

    cfgs = json.loads(CONFIGS.read_text(encoding="utf-8"))
    widen = json.loads(WIDEN.read_text(encoding="utf-8"))

    checks = cross_check(cfgs, widen)
    rows = fold(cfgs, widen, force=a.force)
    rc = report(cfgs, rows, checks)

    if a.commit:
        CONFIGS.write_text(json.dumps(cfgs, indent=1), encoding="utf-8")
        print(f"\nWrote {CONFIGS}")
    else:
        print("\nDry run -- nothing written. Re-run with --commit.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
