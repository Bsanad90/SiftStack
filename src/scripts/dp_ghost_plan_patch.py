"""Carry the resolved ghost names into the DP run dirs' PLAN files, so a re-push cannot revert them.

Found live 2026-09-17: while dp_ghost_name_write.py was renaming `REL{n}: Full Name` slots on
the account, another session's `obituary_dp_batch.py push --force` re-sent dp_obit_aug27's
push_plan.json -- which still said UNKNOWN COLARICH -- and put the ghost name back on 6 records
within minutes. The account is downstream of the plan files; the names have to live there too.

For every ghost with a written name (the write logs, or the enrichment sheet's NEW PERSON /
NAMESAKE SON / renamed-duplicate rows) this rewrites, per run dir, with a timestamped backup:
  push_plan.json        records[key].custom_fields["REL{n}: Full Name"], phones[].rel_name,
                        and the ghost's name inside the note text (+ a NAME UPDATE footer)
  ranked_records.json   rels[i].name / .first for the ghost relative
Nothing on the account is touched. Idempotent: a plan already carrying the name is skipped.

    python src/scripts/dp_ghost_plan_patch.py            # dry run
    python src/scripts/dp_ghost_plan_patch.py --commit
    python src/scripts/dp_ghost_plan_patch.py --commit --skip-run output/dp_obit_aug27   # while a push runs there
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from dp_ghost_name_resolver import OUT, jload  # noqa: E402

ENRICH = OUT / "trestle_enrichment.csv"
RESULTS = OUT / "ghost_name_resolution.json"
EXCLUDE = {"Unknown Welzel"}
STAMP = datetime.now().strftime("%Y%m%dT%H%M%S")
TODAY = datetime.now().strftime("%Y-%m-%d")


def targets() -> dict[str, dict]:
    """key -> {ghost_name, new_name, run, verdict}. Names actually written win; otherwise the
    enrichment sheet's write-ready rows (NEW PERSON, NAMESAKE SON, = NAMED RELATIVE rename)."""
    res = {r["key"]: r for r in jload(RESULTS)["records"]}
    out: dict[str, dict] = {}
    for x in csv.DictReader(open(ENRICH, encoding="utf-8-sig")):
        if x["verdict"] in ("NEW PERSON", "NAMESAKE SON", "= NAMED RELATIVE") and x["proposed_full"] \
                and x["ghost_name"] not in EXCLUDE:
            out[x["key"]] = {"ghost_name": x["ghost_name"], "new_name": x["proposed_full"].upper(),
                             "run": res[x["key"]]["run"], "verdict": x["verdict"]}
    for f in sorted(OUT.glob("write_log_*.jsonl")):
        for line in open(f, encoding="utf-8"):
            e = json.loads(line)
            if e.get("commit") and e.get("ok") and e.get("new_name") and e["key"] in out:
                out[e["key"]]["new_name"] = e["new_name"]
    return out


def patch_run(run: Path, items: list[dict], commit: bool) -> dict:
    rep = {"run": str(run), "plan": 0, "ranked": 0, "skipped": [], "missing": []}
    pp_path, rr_path = run / "push_plan.json", run / "ranked_records.json"
    pp, rr = jload(pp_path), jload(rr_path)
    if not pp or not rr:
        rep["missing"].append("plan files")
        return rep
    changed_pp = changed_rr = False
    for t in items:
        key, ghost, new = t["key"], t["ghost_name"], t["new_name"]
        pat = re.compile(re.escape(ghost), re.I)
        rec = (pp.get("records") or {}).get(key)
        if rec:
            hit = False
            for lbl, val in list((rec.get("custom_fields") or {}).items()):
                if lbl.endswith(": Full Name") and str(val or "").strip().lower() == ghost.lower():
                    rec["custom_fields"][lbl] = new
                    hit = True
            for ph in rec.get("phones") or []:
                if str(ph.get("rel_name") or "").strip().lower() == ghost.lower():
                    ph["rel_name"] = new
                    hit = True
            note = rec.get("note") or ""
            if pat.search(note) and f"NAME UPDATE {TODAY}: '{ghost}'" not in note:
                rec["note"] = pat.sub(new, note) + f"\n\nNAME UPDATE {TODAY}: '{ghost}' -> {new} (Trestle reverse phone)"
                hit = True
            if hit:
                rep["plan"] += 1
                changed_pp = True
            else:
                rep["skipped"].append(f"{ghost}: plan already carries no '{ghost}'")
        else:
            rep["missing"].append(f"{ghost}: not in push_plan")
        rrec = rr.get(key)
        if rrec:
            for rel in rrec.get("rels") or rrec.get("relatives") or []:
                if str(rel.get("name") or "").strip().lower() == ghost.lower():
                    rel["name"] = new
                    rel["first"] = new.split()[0] if new.split() else rel.get("first")
                    rel["name_source"] = f"trestle_reverse_phone {TODAY}"
                    rep["ranked"] += 1
                    changed_rr = True
        else:
            rep["missing"].append(f"{ghost}: not in ranked_records")
    if commit:
        if changed_pp:
            shutil.copy2(pp_path, run / f"push_plan_preghostnames_{STAMP}.json")
            pp_path.write_text(json.dumps(pp, indent=1, ensure_ascii=False), encoding="utf-8")
        if changed_rr:
            shutil.copy2(rr_path, run / f"ranked_records_preghostnames_{STAMP}.json")
            rr_path.write_text(json.dumps(rr, indent=1, ensure_ascii=False), encoding="utf-8")
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--skip-run", action="append", default=[], help="run dir to leave alone (a push is using it)")
    a = ap.parse_args()
    tg = targets()
    by_run: dict[str, list[dict]] = {}
    for key, t in tg.items():
        by_run.setdefault(t["run"], []).append({**t, "key": key})
    total_p = total_r = 0
    for run, items in sorted(by_run.items()):
        if any(Path(run).resolve() == (ROOT / s).resolve() for s in a.skip_run):
            print(f"{run}: SKIPPED ({len(items)} ghosts) -- a push is using it")
            continue
        rep = patch_run(ROOT / run, items, a.commit)
        total_p += rep["plan"]
        total_r += rep["ranked"]
        print(f"{run}: plan records {rep['plan']}, ranked rels {rep['ranked']}"
              + (f"; skipped {rep['skipped']}" if rep["skipped"] else "")
              + (f"; MISSING {rep['missing']}" if rep["missing"] else ""))
    print(f"\n{'patched' if a.commit else 'would patch'} {total_p} plan records and {total_r} ranked relatives "
          f"across {len(by_run)} run dirs" + ("" if a.commit else " -- DRY RUN"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
