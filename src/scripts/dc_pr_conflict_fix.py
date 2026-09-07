"""Resolve the 6 DC PR-field conflicts of 2026-09-07 with court-verified names.

The guarded writer held these because the live values changed after the review
snapshot — the change was our own 09-04 sheet-recency pass. Each name below was
verified against the Tyler portal the same day (`dc_case_pr_lookup.py`, results
in output/pr_repair_allcohorts/dc_case_pr_lookup.json):

  1202 Geranium St Nw   Christopher McSweeney Sr   (adds court suffix)
  1313 New York Ave Nw  Jordan La'Shell Thomas     (court capitalization)
  1413 Duncan St Ne     Daniel A Hall              (adds court middle initial)
  2425 25Th St Se       Shantelle A Smith          (2025-ADM-001308 is the OPEN
                        estate — decedent Yvonne Elaine Williams; the SEB case
                        at the same address is a DIFFERENT, closed decedent.
                        The 09-04 pick was right; court adds the initial.)
  3700 N Capitol St Nw  Steven Christopher Denslow (REAL correction — live held
                        'Graner Ghevarghese', the wrong person entirely;
                        2026-ADM-000268 decedent is Bruce Edward Denslow)

760 19Th St Ne is NOT here: live 'Lloyd D Rucker' already beats the portal's
typo'd role string — keep live.

Backs up prior values, writes via the echo-verified PATCH, reads back.
Usage: python -X utf8 -u src/scripts/dc_pr_conflict_fix.py [--commit]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from obituary_dp_batch import WriteApi  # noqa: E402
from pr_repair_allcohorts_write import read_live_pr, PR_FIRST_UUID, PR_LAST_UUID  # noqa: E402

FIXES = [
    ("17388b71-ec13-4756-b4aa-d09c80c08ac7", "1202 Geranium St Nw", "Christopher", "McSweeney Sr"),
    ("92c3244a-ddf1-40d5-bae4-f12a07e404d4", "1313 New York Ave Nw", "Jordan La'Shell", "Thomas"),
    ("cb429095-eb87-47be-9aee-6db888535966", "1413 Duncan St Ne", "Daniel A", "Hall"),
    ("0127465f-7a99-4933-bc1b-2f6835b0c9c1", "2425 25Th St Se", "Shantelle A", "Smith"),
    ("18113195-b15f-4ac3-ba25-d9eac828eafc", "3700 N Capitol St Nw", "Steven Christopher", "Denslow"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    api = WriteApi()
    backup, fails = [], 0
    for uuid, street, nf, nl in FIXES:
        st, lf, ll = read_live_pr(api, uuid)
        print(f"{street}: live '{lf} {ll}' -> '{nf} {nl}'")
        if st != 200:
            fails += 1
            continue
        if (lf, ll) == (nf, nl):
            print("  already done")
            continue
        backup.append({"uuid": uuid, "street": street, "prior_first": lf, "prior_last": ll})
        if not a.commit:
            continue
        body = [{"field_uuid": PR_FIRST_UUID, "value": nf},
                {"field_uuid": PR_LAST_UUID, "value": nl}]
        wst, _ = api.write("PATCH", f"/api/internal/property/{uuid}/custom-field/update-values/", body)
        st2, f2, l2 = read_live_pr(api, uuid)
        ok = (f2, l2) == (nf, nl)
        print(f"  PATCH {wst} | read-back '{f2} {l2}' {'OK' if ok else 'MISMATCH'}")
        fails += (not ok)
    if a.commit and backup:
        p = ROOT / "output" / "pr_repair_allcohorts" / \
            f"dc_conflict_backup_{datetime.now():%Y%m%dT%H%M%S}.json"
        p.write_text(json.dumps(backup, indent=1), encoding="utf-8")
        print("prior values ->", p)
    if not a.commit:
        print("DRY RUN. Re-run with --commit.")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
