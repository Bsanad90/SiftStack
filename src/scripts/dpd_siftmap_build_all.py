"""Drive the per-county SiftMap preset pipeline across the remaining DPD jurisdictions.

    python src/scripts/dpd_siftmap_build_all.py                 # the 12 post-gate counties
    python src/scripts/dpd_siftmap_build_all.py --fips 51059    # one county only
    python src/scripts/dpd_siftmap_build_all.py --dry-run       # show what would run/skip

Sequential only, one browser at a time (two fresh logins in the same second collide on
datasift_cookies.tmp -> .json, WinError 32). For each county:

    dpd_siftmap_manifest.py --fips F --measure     (skipped if manifest has measured_at)
    dpd_siftmap_presets.py  --fips F --commit      (skipped if commit artifact is clean)
    dpd_siftmap_presets.py  --fips F --verify      (skipped if verify artifact is clean)
    dpd_qa_report.py        --fips F               (always; seconds, no browser)

Halts the whole loop on the first defect: nonzero exit, a stage timeout, commit
`stopped_on`/`missing_after`, or any verify row with present/params_match/count_match
false. A re-run resumes at the failure point via the same artifact checks. Adds ZERO
records to the account — this only saves and reloads named filters.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "src" / "scripts"
LOGS = ROOT / "logs"

# Ascending saveable-preset count (gate county 51059 Fairfax excluded by default;
# the artifact skips make it harmless to add back via --fips).
DEFAULT_ORDER = [
    "51630",  # Fredericksburg City, VA   (1)
    "24009",  # Calvert, MD               (5)
    "24013",  # Carroll, MD               (8)
    "24017",  # Charles, MD               (8)
    "24021",  # Frederick, MD             (8)
    "51179",  # Stafford, VA              (9)
    "24003",  # Anne Arundel, MD          (10)
    "51013",  # Arlington, VA             (11)
    "51177",  # Spotsylvania, VA          (13)
    "51153",  # Prince William, VA        (15)
    "24031",  # Montgomery, MD            (19)
    "24005",  # Baltimore County, MD      (22)
]

STAGE_TIMEOUT_S = 45 * 60  # a hung Chromium shows as a dead pipe; don't wait forever


def _load(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _manifest_ok(fips: str) -> bool:
    man = _load(ROOT / "data" / f"dpd_siftmap_manifest_{fips}.json")
    return bool(man and man.get("measured_at"))


def commit_defects(out: dict | None) -> list[str]:
    if not out:
        return ["commit artifact missing or unreadable"]
    bad = []
    if out.get("error"):
        bad.append(f"top-level error: {out['error']}")
    if out.get("stopped_on"):
        bad.append(f"stopped_on: {out['stopped_on']}")
    if out.get("missing_after"):
        bad.append(f"missing_after: {out['missing_after']}")
    for r in out.get("results", []):
        if r.get("status") not in ("saved", "already_present"):
            bad.append(f"{r.get('name')}: status={r.get('status')} {r.get('error', '')}")
    if not out.get("results"):
        bad.append("no results recorded")
    return bad


def verify_defects(out: dict | None) -> list[str]:
    if not out:
        return ["verify artifact missing or unreadable"]
    if out.get("error"):
        return [f"top-level error: {out['error']}"]
    bad = []
    for r in out.get("results", []):
        if not r.get("present"):
            bad.append(f"{r.get('name')}: NOT PRESENT in the popover")
        elif r.get("params_match") is False:
            bad.append(f"{r.get('name')}: PARAM MISMATCH {r.get('problems')}")
        elif r.get("count_match") is False:
            bad.append(f"{r.get('name')}: count {r.get('count')} vs measured "
                       f"{r.get('measured_count')}")
    if not out.get("results"):
        bad.append("no results recorded")
    return bad


def run_stage(fips: str, stage: str, argv: list[str]) -> int:
    LOGS.mkdir(exist_ok=True)
    log = LOGS / f"dpd_siftmap_build_{fips}_{stage}.log"
    print(f"[{datetime.now():%H:%M:%S}] {fips} {stage}: running -> {log.name}", flush=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} {argv}\n")
        fh.flush()
        try:
            proc = subprocess.run([sys.executable, "-u", *argv], cwd=str(ROOT),
                                  stdout=fh, stderr=subprocess.STDOUT,
                                  timeout=STAGE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            fh.write(f"\n===== TIMEOUT after {STAGE_TIMEOUT_S}s\n")
            return -1
    return proc.returncode


def build_county(fips: str, dry: bool) -> list[str]:
    """Run the pipeline for one county; return defect strings (empty = clean)."""
    # 1. measure
    if _manifest_ok(fips):
        print(f"  = measure already done (manifest has measured_at)")
    elif dry:
        print(f"  > would run measure")
    else:
        rc = run_stage(fips, "measure",
                       [str(SCRIPTS / "dpd_siftmap_manifest.py"), "--fips", fips, "--measure"])
        if rc != 0 or not _manifest_ok(fips):
            return [f"measure failed (exit {rc}) or manifest lacks measured_at"]

    commit_path = ROOT / "output" / f"dpd_siftmap_presets_{fips}_commit.json"
    verify_path = ROOT / "output" / f"dpd_siftmap_presets_{fips}_verify.json"

    # 2. commit
    if not commit_defects(_load(commit_path)):
        print(f"  = commit already clean")
    elif dry:
        print(f"  > would run commit")
    else:
        rc = run_stage(fips, "commit",
                       [str(SCRIPTS / "dpd_siftmap_presets.py"), "--fips", fips, "--commit"])
        bad = commit_defects(_load(commit_path))
        if rc != 0 or bad:
            return [f"commit exit {rc}"] + bad

    # 3. verify
    if not verify_defects(_load(verify_path)):
        print(f"  = verify already clean")
    elif dry:
        print(f"  > would run verify")
    else:
        rc = run_stage(fips, "verify",
                       [str(SCRIPTS / "dpd_siftmap_presets.py"), "--fips", fips, "--verify"])
        bad = verify_defects(_load(verify_path))
        if rc != 0 or bad:
            return [f"verify exit {rc}"] + bad

    # 4. QA report (no browser, idempotent)
    if not dry:
        rc = run_stage(fips, "qa", [str(SCRIPTS / "dpd_qa_report.py"), "--fips", fips])
        if rc != 0:
            return [f"qa report exit {rc}"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fips", nargs="*", help="override the county list (default: the 12)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    order = a.fips or DEFAULT_ORDER

    for i, fips in enumerate(order, 1):
        print(f"\n[{datetime.now():%H:%M:%S}] == county {i}/{len(order)}: {fips} ==", flush=True)
        defects = build_county(fips, a.dry_run)
        if defects:
            print(f"\nHALT on {fips} — first defect stops the loop:")
            for d in defects:
                print(f"  ! {d}")
            print("Fix, then re-run; clean artifacts are skipped automatically.")
            return 1
    print(f"\n[{datetime.now():%H:%M:%S}] all {len(order)} counties clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
