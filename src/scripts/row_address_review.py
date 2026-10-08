"""MD Register of Wills -- hand the no-address estates to the agent re-review, and
merge its findings back into the ledger.

The Land Records lookup (md_row_address_fill.ps1) checks every estate once. When it
finds nothing the estate is marked checked and never looked at again: 394 of 922 on
2026-10-08. This is the second pass. It does NOT touch Land Records: that site locks
out parallel logins (emailed access code), so the re-review is a fan-out of
`row-address-researcher` subagents over the free sources, coordinated by
`row-address-reviewer`, on Basem's PC (automation/row_agent_review.ps1).

    export  -> output/row_review/<day>/queue.csv (also row_needs_address.csv on Drive, --ready)
    agents  -> output/row_review/<day>/row_address_review_<day>.csv
    merge   -> this script; then row_review_pipeline.py finish (sheet, Drive, Mohammad)

A merged FOUND row is NOT trusted as-is: it is written with
`property_needs_sdat_owner = True`, so the next fill pass runs the same SDAT
current-owner check the notice-stated addresses get and grades it HIGH / MEDIUM /
LOW from the owner of record. The agent's own confidence is kept in `agent_review`.

Single writer: the ledger is read and written WHOLESALE, so merge refuses to run
while any md_register_of_wills_pull process is alive (the same rule as the fill job).
An address already on the ledger is never overwritten.

    python src/scripts/row_address_review.py export --out <csv>
    python src/scripts/row_address_review.py merge output/row_address_reviews/*.csv [--commit]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO / "output" / "md_row_ledger.json"
ARCHIVE = REPO / "output" / "_archive"
EXCLUDED_ESTATE_TYPES = {"SE", "SJ", "MV"}  # = md_register_of_wills_pull.EXCLUDED_ESTATE_TYPES
REREVIEW_DAYS = 30  # an agent NOT_FOUND / AMBIGUOUS comes back to the queue after this

EXPORT_COLUMNS = [
    "key", "county", "estate_number", "record_id", "decedent_name", "first_middle", "last_name",
    "date_of_death", "date_of_filing", "estate_type", "personal_reps", "attorney",
    "prior_lookup_reason", "prior_sdat_owner", "prior_agent_review",
]
REVIEW_COLUMNS = [
    "key", "status", "street", "city", "state", "zip", "confidence", "source",
    "evidence", "reason", "reviewed_on",
]
STATUSES = {"FOUND", "NOT_FOUND", "AMBIGUOUS"}
CONFIDENCES = {"HIGH", "MEDIUM", "LOW"}
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")


def load_ledger() -> dict:
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))


def has_address(rec: dict) -> bool:
    street = (rec.get("property_street") or "").strip()
    return bool(street) and "not found" not in street.lower()


def needs_review(rec: dict, today: date) -> bool:
    if rec.get("estate_type") in EXCLUDED_ESTATE_TYPES:
        return False
    if not rec.get("property_lookup_checked") or has_address(rec):
        return False  # unchecked = the fill job's queue; addressed = done
    prior = rec.get("agent_review") or {}
    if prior.get("date"):
        try:
            last = date.fromisoformat(prior["date"])
        except ValueError:
            return True
        return (today - last).days >= REREVIEW_DAYS
    return True


def _pr_text(prs) -> str:
    out = []
    for pr in prs or []:
        addr = ", ".join(x for x in [pr.get("street"), pr.get("city"),
                                     f"{pr.get('state', '')} {pr.get('zip', '')}".strip()] if x)
        out.append(f"{pr.get('name', '')} | {addr}" if addr else pr.get("name", ""))
    return " ; ".join(out)


def export(out: Path, today: date) -> int:
    estates = load_ledger().get("estates", {})
    rows = []
    for key, rec in estates.items():
        if not needs_review(rec, today):
            continue
        county, _, number = key.partition("|")
        prior = rec.get("agent_review") or {}
        rows.append({
            "key": key, "county": county, "estate_number": number,
            "record_id": rec.get("record_id", ""),
            "decedent_name": rec.get("decedent_name", ""),
            "first_middle": rec.get("decedent_first_name", ""),
            "last_name": rec.get("decedent_last_name", ""),
            "date_of_death": rec.get("date_of_death", ""),
            "date_of_filing": rec.get("date_of_filing") or rec.get("filing_date", ""),
            "estate_type": rec.get("estate_type", ""),
            "personal_reps": _pr_text(rec.get("personal_reps")),
            "attorney": rec.get("attorney") or "",
            "prior_lookup_reason": rec.get("property_lookup_reason", ""),
            "prior_sdat_owner": rec.get("property_sdat_owner", ""),
            "prior_agent_review": f"{prior.get('date', '')} {prior.get('status', '')}".strip(),
            "_first_seen": rec.get("first_seen", ""),
        })
    rows.sort(key=lambda r: r.pop("_first_seen") or "")  # oldest first: the backlog drains in order
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=EXPORT_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"row_needs_address: {len(rows)} estate(s) -> {out}")
    return len(rows)


def row_process_alive() -> list[str]:
    """PIDs of any live md_register_of_wills_pull process (Windows CIM; no psutil here)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*md_register_of_wills_pull*' } | "
          "ForEach-Object { $_.ProcessId }")
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"REFUSED: cannot check for a running Register of Wills process ({exc})")
    return [p for p in res.stdout.split() if p.strip()]


def validate(row: dict) -> str | None:
    """Reason the row is unusable, or None."""
    status = (row.get("status") or "").strip().upper()
    if status not in STATUSES:
        return f"status {row.get('status')!r} not one of {sorted(STATUSES)}"
    if status != "FOUND":
        return None
    if not (row.get("street") or "").strip() or not re.match(r"^\d", row["street"].strip()):
        return "FOUND without a numbered street"
    if (row.get("state") or "").strip().upper() != "MD":
        return f"state {row.get('state')!r} is not MD (Register of Wills = MD property)"
    if not ZIP_RE.match((row.get("zip") or "").strip()):
        return f"zip {row.get('zip')!r} is not a ZIP"
    if (row.get("confidence") or "").strip().upper() not in CONFIDENCES:
        return f"confidence {row.get('confidence')!r} not one of {sorted(CONFIDENCES)}"
    if not (row.get("evidence") or "").strip():
        return "FOUND with no evidence URL / source note"
    return None


def merge(files: list[Path], commit: bool) -> int:
    if commit:
        alive = row_process_alive()
        if alive:
            print(f"SKIP: a Register of Wills process is running (PID {','.join(alive)}) -- "
                  "not touching the ledger; the next pass merges.")
            return 4  # non-zero: the runner must keep the files for the next pass
    ledger = load_ledger()
    estates = ledger.get("estates", {})
    counts = {"found": 0, "not_found": 0, "skipped": 0, "invalid": 0}
    for path in files:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        print(f"{path.name}: {len(rows)} row(s)")
        for row in rows:
            key = (row.get("key") or "").strip()
            rec = estates.get(key)
            if rec is None:
                print(f"  INVALID {key or '<no key>'}: not in the ledger")
                counts["invalid"] += 1
                continue
            problem = validate(row)
            if problem:
                print(f"  INVALID {key}: {problem}")
                counts["invalid"] += 1
                continue
            status = row["status"].strip().upper()
            reviewed = (row.get("reviewed_on") or "").strip() or date.today().isoformat()
            review = {"date": reviewed, "status": status,
                      "confidence": (row.get("confidence") or "").strip().upper(),
                      "source": (row.get("source") or "").strip(),
                      "evidence": (row.get("evidence") or "").strip(),
                      "reason": (row.get("reason") or "").strip(),
                      "file": path.name}
            if has_address(rec):
                # The fill job or an earlier merge got there first: never overwrite.
                print(f"  skip    {key}: already has {rec.get('property_street')}")
                counts["skipped"] += 1
                continue
            rec["agent_review"] = review
            if status == "FOUND":
                rec["property_street"] = row["street"].strip().upper()
                rec["property_city"] = (row.get("city") or "").strip().upper()
                rec["property_state"] = "MD"
                rec["property_zip"] = row["zip"].strip()
                rec["property_lookup_source"] = f"agent_review:{review['source'] or 'unspecified'}"
                rec["property_lookup_confidence"] = "MEDIUM"
                rec["property_lookup_reason"] = (f"agent review {reviewed}: {review['reason']} "
                                                 "-- SDAT owner check pending")
                rec["property_needs_sdat_owner"] = True  # the next fill pass grades it
                counts["found"] += 1
                print(f"  FOUND   {key}: {rec['property_street']}, {rec['property_city']} "
                      f"{rec['property_zip']} ({review['confidence']}, {review['source']})")
            else:
                counts["not_found"] += 1
                print(f"  {status:<9} {key}: {review['reason'][:100]}")
    print(f"merge: {counts['found']} address(es), {counts['not_found']} not found / ambiguous, "
          f"{counts['skipped']} already addressed, {counts['invalid']} invalid row(s)")
    if not commit:
        print("DRY RUN -- ledger unchanged. Re-run with --commit to write.")
        return 0
    if counts["found"] or counts["not_found"]:
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        backup = ARCHIVE / f"md_row_ledger_pre_review_{datetime.now():%Y%m%d_%H%M%S}.json"
        shutil.copy2(LEDGER_PATH, backup)
        LEDGER_PATH.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        print(f"ledger written (backup {backup.name})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="write the no-address estates for the agent re-review")
    ex.add_argument("--out", type=Path, required=True)
    mg = sub.add_parser("merge", help="apply row_address_review_*.csv files to the ledger")
    mg.add_argument("files", nargs="*", type=Path)
    mg.add_argument("--commit", action="store_true", help="write the ledger (default: dry run)")
    args = ap.parse_args()

    if args.cmd == "export":
        export(args.out, date.today())
        return 0
    files = [p for p in args.files if p.is_file()]
    if not files:
        print("merge: no review files to apply.")
        return 0
    return merge(files, args.commit)


if __name__ == "__main__":
    sys.exit(main())
