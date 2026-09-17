"""Order the DP cleanup re-push: records nobody has dialed yet go first. READ-ONLY.

Basem, 2026-09-17: "Prioritize records that were not yet called or dialed." Every DP batch
pushed 09-01..09-11 loaded in-laws and non-signers (old --max-rels 15 / --channel-cap 2);
the re-push with today's signers-only build strips them. A record a dialer has not touched
yet gets the clean dial sheet BEFORE anyone burns a dial on an in-law, so those go first.

For every record a run dir's push_log.jsonl logged as pushed (commit, no error) this reads
the live record once and classifies it with the Re-Skiptrace audit's UNDIALED test
(reskiptrace_audit.py): never_dialed = property AND owner `predictivecall_attempts` both 0
AND every owner phone `status` still UNKNOWN/blank. Anything else is `dialed`.

Writes per run dir:
  strip_order_<date>.csv     one row per record: tier, attempts, phones, dispositioned, ...
  keys_never_dialed.json     ORDERED plan keys (fewest phones first: cleanest sheets first)
  keys_dialed.json           ORDERED plan keys (fewest dispositioned numbers first)
  strip_order_detail.json    resumable detail cache (uuid -> thin record)

Then:  obituary_dp_batch.py --run-dir <run> push --route api --force --commit \
           --keys-file <run>/keys_never_dialed.json        (wave 1, every run dir)
       ... and keys_dialed.json for wave 2.

Usage:
    python -u src/scripts/dp_strip_order.py --run-dir output/dp_obit_aug27
    python -u src/scripts/dp_strip_order.py --all            # the known batch run dirs
    python -u src/scripts/dp_strip_order.py --all --fresh    # ignore the detail cache
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from obituary_dp_batch import WriteApi, jload, say  # noqa: E402
from obituary_dp_batch import jdump as jsave  # noqa: E402

# The batch run dirs pushed with the old rules (plan file, 2026-09-17). Ad-hoc single-record
# dirs (dp_marion_rd, dp_111laurel, ...) are deliberately not here.
DEFAULT_RUNS = [
    "dp_hottest_needs_skipped", "dp_obit_aug27", "dp_hottest_needs_batch2", "dp_deep03_batch",
    "dp_skipped_batch", "dp_obit_new_20260908", "dp_verified_import", "dp_ftm_batch",
    "dp_ftm_needs_skipped_20260915", "dp_deep01_batch", "dp_deep05_batch",
    "dp_hottest_skipped_batch", "dp_reskiptrace", "dp_reskiptrace2", "dp_reskiptrace3",
    "dp_reskiptrace4",
]

CSV_COLS = ["tier", "key", "street", "uuid", "call_attempts", "phones", "dispositioned",
            "status", "assigned_to", "skiptraced", "note"]


def pushed_records(run: Path) -> dict[str, dict]:
    """key -> {uuid, street} for the LATEST successful push of each key."""
    out: dict[str, dict] = {}
    log = run / "push_log.jsonl"
    if not log.exists():
        return out
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("route") != "api" or not e.get("commit") or e.get("error") or not e.get("uuid"):
            continue
        out[e["key"]] = {"uuid": e["uuid"], "street": e.get("street") or ""}
    return out


def _label(v) -> str:
    if isinstance(v, dict):
        return str(v.get("title") or v.get("name") or v.get("email") or v.get("uuid") or "")
    return "" if v is None else str(v)


def classify(rec: dict) -> dict:
    owner = rec.get("owner") or {}
    phones = [p for p in (owner.get("phones") or []) if isinstance(p, dict)]
    disp = [p for p in phones if (p.get("status") or "UNKNOWN").upper() not in ("UNKNOWN", "")]
    attempts = int(rec.get("predictivecall_attempts") or 0) + int(owner.get("predictivecall_attempts") or 0)
    never = attempts == 0 and not disp
    return {"tier": "never_dialed" if never else "dialed", "call_attempts": attempts,
            "phones": len(phones), "dispositioned": len(disp), "status": _label(rec.get("status")),
            "assigned_to": _label(rec.get("assigned_to")), "skiptraced": bool(owner.get("skiptraced"))}


def thin(rec: dict) -> dict:
    """Only what classify() reads, so the cache stays small and re-usable."""
    owner = rec.get("owner") or {}
    return {"predictivecall_attempts": rec.get("predictivecall_attempts"),
            "status": rec.get("status"), "assigned_to": rec.get("assigned_to"),
            "owner": {"predictivecall_attempts": owner.get("predictivecall_attempts"),
                      "skiptraced": owner.get("skiptraced"),
                      "phones": [{"number": p.get("number"), "status": p.get("status")}
                                 for p in (owner.get("phones") or []) if isinstance(p, dict)]}}


def order_run(api: WriteApi, run: Path, fresh: bool, stamp: str) -> dict:
    recs = pushed_records(run)
    if not recs:
        say(f"{run.name}: no successful API pushes in push_log.jsonl, skipped")
        return {}
    cache_path = run / "strip_order_detail.json"
    cache = {} if fresh else jload(cache_path, {})
    rows = []
    gone = 0
    t0 = time.time()
    for i, (key, meta) in enumerate(recs.items(), 1):
        u = meta["uuid"]
        entry = cache.get(u)
        if entry is None:
            st, body = api.get(f"/api/internal/property/{u}/")
            if st == 404:
                entry = {"gone": True}
            elif st != 200 or not isinstance(body, dict):
                say(f"  {key}: GET {st}, retry later")
                continue
            else:
                entry = thin(body)
            cache[u] = entry
            if i % 25 == 0:
                jsave(cache_path, cache)
                say(f"  {run.name} {i}/{len(recs)}  {i / max(time.time() - t0, 1):.1f} rec/s")
        if entry.get("gone"):
            gone += 1
            rows.append({"tier": "gone", "key": key, "street": meta["street"], "uuid": u,
                         "note": "404: deleted from the account"})
            continue
        c = classify(entry)
        rows.append({**c, "key": key, "street": meta["street"], "uuid": u, "note": ""})
    jsave(cache_path, cache)

    never = sorted([r for r in rows if r["tier"] == "never_dialed"], key=lambda r: (r["phones"], r["key"]))
    dialed = sorted([r for r in rows if r["tier"] == "dialed"],
                    key=lambda r: (r["dispositioned"], r["call_attempts"], r["key"]))
    ordered = never + dialed + [r for r in rows if r["tier"] == "gone"]
    with open(run / f"strip_order_{stamp}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for r in ordered:
            w.writerow({c: r.get(c, "") for c in CSV_COLS})
    jsave(run / "keys_never_dialed.json", [r["key"] for r in never])
    jsave(run / "keys_dialed.json", [r["key"] for r in dialed])
    summary = {"run": run.name, "pushed": len(recs), "never_dialed": len(never), "dialed": len(dialed),
               "gone": gone, "unread": len(recs) - len(rows)}
    say(f"{run.name}: {len(recs)} pushed -> never_dialed {len(never)}, dialed {len(dialed)}, "
        f"gone {gone}, unread {summary['unread']}")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", action="append", default=[], help="repeatable")
    ap.add_argument("--all", action="store_true", help=f"the {len(DEFAULT_RUNS)} known batch run dirs")
    ap.add_argument("--fresh", action="store_true", help="ignore strip_order_detail.json")
    a = ap.parse_args()
    runs = [Path(r) for r in a.run_dir]
    if a.all:
        runs += [ROOT / "output" / r for r in DEFAULT_RUNS]
    if not runs:
        ap.error("--run-dir or --all")
    stamp = date.today().strftime("%Y%m%d")
    api = WriteApi()
    totals = {"pushed": 0, "never_dialed": 0, "dialed": 0, "gone": 0, "unread": 0}
    for run in runs:
        if not run.exists():
            say(f"{run}: missing, skipped")
            continue
        s = order_run(api, run, a.fresh, stamp)
        for k in totals:
            totals[k] += s.get(k, 0)
    say(f"TOTAL: {totals}")
    return 0 if not totals["unread"] else 1


if __name__ == "__main__":
    sys.exit(main())
