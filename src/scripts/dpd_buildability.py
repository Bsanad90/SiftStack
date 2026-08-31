"""Which doors-per-deal stacks can SiftMap actually build today?

Joins three read-only artifacts:
    data/dpd_signal_rankings.json    every ranked stack per county (workbooks)
    output/dpd_siftmap_filters.json  the confirmed URL parameters (Phase 2)
    data/dpd_zips.json               the geography layer (Phase 1)

The plan quotes ONE Priority-1 stack per county, and for several counties that
stack needs a signal SiftMap cannot filter (Out-of-State, Bad Credit, HOA
Lien). Read that way, five counties look blocked. But the workbooks rank many
stacks per county, so the real question is not "is the named stack buildable"
but "what is the best BUILDABLE stack" - which is what this answers.

Writes data/dpd_buildability.json and prints the decision table.
Read-only; touches no browser and no account.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dpd.jurisdictions import JURISDICTIONS, resolve  # noqa: E402

DATA_DIR = Path("data")
RANKINGS = DATA_DIR / "dpd_signal_rankings.json"
FILTERS = Path("output/dpd_siftmap_filters.json")
ZIPS = DATA_DIR / "dpd_zips.json"
NBRS = DATA_DIR / "dpd_neighborhoods.json"
OUT = DATA_DIR / "dpd_buildability.json"

# Signals the workbooks use that are base criteria or suppression inputs rather
# than distressors to stack. Kept explicit so nothing is silently ignored.
NOT_A_DISTRESSOR: dict[str, str] = {}

# T1's own sizing contract, from the playbook.
T1_LO, T1_HI = 200, 500


def _signal_to_param(filters: dict) -> tuple[dict, dict]:
    """Build signal -> parameter from the Phase 2 discovery output.

    Two sources inside that file: the named default presets (which carry their
    own signal mapping) and the probed signal parameters.
    """
    mapping, notes = {}, {}
    for preset_name, rec in (filters.get("presets") or {}).items():
        signal = rec.get("signal")
        if not signal or not rec.get("param"):
            continue
        mapping[signal] = "%s=true" % rec["param"]
        notes[signal] = {
            "source": "default preset '%s'" % preset_name,
            "probe_count": rec.get("count"),
        }
    for signal, rec in (filters.get("signal_params") or {}).items():
        if rec.get("param"):
            mapping[signal] = rec["param"]
            notes[signal] = {"source": "probed", "probe_count": rec.get("count")}
    return mapping, notes


def analyse() -> dict:
    for path in (RANKINGS, FILTERS, ZIPS):
        if not path.exists():
            raise SystemExit("missing %s - run the earlier phases first" % path)

    rankings = json.loads(RANKINGS.read_text(encoding="utf-8"))
    filters = json.loads(FILTERS.read_text(encoding="utf-8"))
    zips = json.loads(ZIPS.read_text(encoding="utf-8"))["counties"]
    nbrs = json.loads(NBRS.read_text(encoding="utf-8"))["counties"] if NBRS.exists() else {}

    sig2param, param_notes = _signal_to_param(filters)
    probe_county = filters.get("county")

    out = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "parameter_probe_county": probe_county,
        "signal_to_param": sig2param,
        "param_notes": param_notes,
        "counties": {},
    }

    for j in JURISDICTIONS:
        rows = rankings["by_jurisdiction"].get(j.name, [])
        geo_z = zips.get(j.name, {})
        geo_n = nbrs.get(j.name, {})

        evaluated = []
        for r in rows:
            missing = [s for s in r["signals"] if s not in sig2param]
            evaluated.append({
                **{k: r[k] for k in ("priority", "signal", "signals", "type",
                                     "lift", "list_size", "doors_per_deal")},
                "buildable": not missing,
                "missing_signals": missing,
                "params": [sig2param[s] for s in r["signals"] if s in sig2param],
            })

        buildable = [e for e in evaluated if e["buildable"]]
        # Best = lowest priority number, then highest lift.
        buildable.sort(key=lambda e: (e["priority"] if e["priority"] is not None else 99,
                                      -(e["lift"] or 0)))
        named = evaluated[0] if evaluated else None
        best = buildable[0] if buildable else None

        blockers = sorted({s for e in evaluated if not e["buildable"]
                           for s in e["missing_signals"]})

        # Does the best buildable stack land in the playbook's T1 band already,
        # or does it need the neighbourhood layer to narrow it?
        sizing = None
        if best and best["list_size"] is not None:
            n = best["list_size"]
            if n < T1_LO:
                sizing = "under band (%d < %d) - too thin for a full T1 cadence" % (n, T1_LO)
            elif n > T1_HI:
                sizing = ("over band (%d > %d) - needs the neighbourhood layer to narrow"
                          % (n, T1_HI))
            else:
                sizing = "in band (%d)" % n

        out["counties"][j.name] = {
            "key": j.key,
            "fips": j.fips,
            "ranked_rows": len(evaluated),
            "buildable_rows": len(buildable),
            "named_top_stack": named,
            "best_buildable_stack": best,
            "blocking_signals": blockers,
            "t1_sizing": sizing,
            "geography": {
                "zips": geo_z.get("selected", []),
                "zips_status": geo_z.get("status", "missing"),
                "neighborhoods": geo_n.get("selected", []),
                "price_band_60pct": geo_z.get("price_band_60pct"),
            },
            "all_stacks": evaluated,
        }

    return out


def report(out: dict) -> None:
    sig2param = out["signal_to_param"]
    print("\nSignals WITH a SiftMap parameter (%d):" % len(sig2param))
    for s, p in sorted(sig2param.items()):
        print("   %-22s %s" % (s, p))

    every = set()
    for c in out["counties"].values():
        for e in c["all_stacks"]:
            every.update(e["signals"])
    gaps = sorted(every - set(sig2param))
    print("\nSignals the workbooks use with NO parameter (%d): %s" % (len(gaps), ", ".join(gaps)))

    print("\n%-26s %-4s %-5s  %s" % ("Jurisdiction", "rank", "build", "best BUILDABLE stack"))
    print("-" * 104)
    for name, c in out["counties"].items():
        best = c["best_buildable_stack"]
        if best:
            desc = "P%s %s (%sx, list %s)" % (best["priority"], best["signal"],
                                              best["lift"], best["list_size"])
        else:
            desc = "NONE - blocked by: %s" % ", ".join(c["blocking_signals"])
        print("%-26s %-4d %-5d  %s" % (name, c["ranked_rows"], c["buildable_rows"], desc[:62]))

    print("\nWhere the NAMED top stack differs from the best BUILDABLE one:")
    any_diff = False
    for name, c in out["counties"].items():
        named, best = c["named_top_stack"], c["best_buildable_stack"]
        if not named:
            print("  %-26s no ranked rows at all" % name)
            any_diff = True
            continue
        if named["buildable"]:
            continue
        any_diff = True
        alt = ("%s (%sx, list %s)" % (best["signal"], best["lift"], best["list_size"])
               if best else "NOTHING buildable")
        print("  %-26s named %s (%sx) needs %s -> use %s" % (
            name, named["signal"][:34], named["lift"],
            "+".join(named["missing_signals"]), alt))
    if not any_diff:
        print("  (none - every county's named stack is buildable)")

    print("\nT1 sizing against the playbook's %d-%d band:" % (T1_LO, T1_HI))
    for name, c in out["counties"].items():
        if c["t1_sizing"]:
            print("  %-26s %s" % (name, c["t1_sizing"]))
        elif c["best_buildable_stack"] is None:
            print("  %-26s no buildable stack to size" % name)


def main() -> None:
    ap = argparse.ArgumentParser(description="Which DPD stacks can SiftMap build today?")
    ap.add_argument("--json", action="store_true", help="print the raw JSON too")
    args = ap.parse_args()

    out = analyse()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    report(out)
    if args.json:
        print(json.dumps(out, indent=2)[:4000])
    print("\nWrote %s" % OUT)


if __name__ == "__main__":
    main()
