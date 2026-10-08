"""RoW address review -- the steps AFTER the agents (Basem's PC).

Daily chain (automation/row_agent_review.ps1, Tue-Sat after the 9:15 address fill):
    1. row_address_review.py export           -> queue.csv (checked, still no address)
    2. claude --agent row-address-reviewer     -> 10 estates per researcher subagent
    3. row_address_review.py merge --commit    -> ledger (single writer)
    4. md_register_of_wills_pull --fill-addresses-only --commit -> SDAT grades agent streets
    5. THIS: finish --commit
         * manual list = estates the agents could not settle (NOT_FOUND, AMBIGUOUS, or an
           agent street SDAT graded LOW) -> row_manual_review_<day>.csv
         * Probate Extraction 2 / "Register of Wills": every NOT_FOUND / AMBIGUOUS estate that
           is NOT already on the tab is appended as a NEW row in the tab's own 32-column
           format with Property Address "Not Found (Claude)". Existing rows are never edited.
         * Drive "SiftStack Daily Pulls/<day>/": the agent review file + the manual list
         * popup to Basem with the counts
    6. Che's PC: `claude --agent row-manual-review` works the manual list, uploads his
       findings as a NEW file and renames OUR row_manual_review_<day>.csv to
       "... - REVIEWED.csv". That rename is the signal: drive.file can see a rename of a file
       this app created, so no wider Drive scope is needed.
    7. THIS: watch (every 30 min) -> popup to Basem "Che reviewed <day>".
Nothing here skip-traces, deep-prospects, uploads to DataSift or spends: those wait on Basem.

Sheet writes use the Google token of the sheet's OWNER (eng.basem.sanad.2012, the Divorce
Cases token.json, spreadsheets scope), read-only from disk, never saved back.

    python src/scripts/row_review_pipeline.py finish --day 2026-10-08 [--commit]
    python src/scripts/row_review_pipeline.py watch
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_pull_drive_sync as drive  # noqa: E402
import row_address_review as rar  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
REVIEW_ROOT = REPO / "output" / "row_review"
APPENDED_LOG = REVIEW_ROOT / "pe2_appended.json"  # estate #s WE added (keep Hisham's KPI honest)
NOTIFY_STATE = REVIEW_ROOT / "notified.json"
REMINDER_PS1 = REPO / "tools" / "manual" / "show_reminder.ps1"

PE2_ID = "1isF9fSS97XffuXZbaYkMklCVTkDczCCoKAA5qhUkiao"
PE2_TAB = "Register of Wills"
PE2_TOKEN = Path(os.environ.get(
    "PE2_SHEETS_TOKEN",
    str(REPO.parent / "Divorce Cases" / "Divorce Cases" / "token.json")))
NOT_FOUND = "Not Found (Claude)"
# The tab's 32 columns, in order (header row 1, read 2026-10-08). Never reorder.
PE2_COLUMNS = [
    "Estate #", "Deceased First Name", "Deceased Last Name", "Property Address", "Property City",
    "Property State", "Property Zipcode", "County", "Attorney", "Attorney Address",
    "PR First Name", "PR Last Name", "Owner Address (PR)", "Owner City", "Owner State",
    "Owner Zipcode", "Published on", "Uploaded", "Tag", "PR 2 First Name", "PR 2 Last Name",
    "PR 2 Address", "PR 2 city", "PR 2 State", "PR 2 Zipcode", "PR 3 First Name",
    "PR 3 Last Name", "PR 3 Address", "PR 3 city", "PR 3 State", "PR 3 Zipcode", "Last PR Check",
]
SUFFIXES = {"JR", "JR.", "SR", "SR.", "II", "III", "IV"}

MANUAL_COLUMNS = [
    "key", "estate_number", "county", "decedent", "date_of_death", "date_of_filing",
    "estate_type", "personal_reps", "attorney", "why_manual", "agent_status", "agent_candidate",
    "agent_reason", "agent_evidence", "sdat_owner", "prior_lookup_reason", "in_pe2",
    # Che fills these:
    "che_status", "street", "city", "zip", "notes",
]


def title(s: str) -> str:
    """'BOX 215 20 W. MAIN STREET' -> 'Box 215 20 W. Main Street' (the tab's casing)."""
    return " ".join(w if any(c.isdigit() for c in w) else w.capitalize() for w in (s or "").split())


def split_name(full: str) -> tuple[str, str]:
    """'TRAVIS STEVEN RICE' -> ('Travis Steven', 'Rice'); 'NELSON W RUPP JR' -> ('Nelson W', 'Rupp Jr')."""
    toks = (full or "").replace(",", " ").split()
    if not toks:
        return "", ""
    last = [toks.pop()]
    if last[0].upper() in SUFFIXES and toks:
        last.insert(0, toks.pop())
    return title(" ".join(toks)), title(" ".join(last))


def estate_cell(number: str):
    """Montgomery numbers are 'W128208'; AA / Frederick are numeric on the tab."""
    n = (number or "").strip()
    return int(n) if n.isdigit() else n.upper()


def pe2_row(key: str, rec: dict) -> list:
    county, _, number = key.partition("|")
    row = dict.fromkeys(PE2_COLUMNS, "")
    row["Estate #"] = estate_cell(number)
    row["Deceased First Name"] = title(rec.get("decedent_first_name", ""))
    row["Deceased Last Name"] = title(rec.get("decedent_last_name", ""))
    row["Property Address"] = NOT_FOUND
    row["County"] = county  # bare county name, as the tab's newest rows (2832+) carry it
    att = rec.get("attorney") or {}
    if isinstance(att, dict) and att.get("name"):
        row["Attorney"] = title(att["name"])
        tail = " ".join(x for x in [(att.get("state") or "").upper(), att.get("zip") or ""] if x)
        row["Attorney Address"] = ", ".join(x for x in [title(att.get("street", "")),
                                                        title(att.get("city", "")), tail] if x)
    prs = rec.get("personal_reps") or []
    slots = [("PR First Name", "PR Last Name", "Owner Address (PR)", "Owner City", "Owner State", "Owner Zipcode"),
             ("PR 2 First Name", "PR 2 Last Name", "PR 2 Address", "PR 2 city", "PR 2 State", "PR 2 Zipcode"),
             ("PR 3 First Name", "PR 3 Last Name", "PR 3 Address", "PR 3 city", "PR 3 State", "PR 3 Zipcode")]
    for pr, cols in zip(prs, slots):
        first, last = split_name(pr.get("name", ""))
        vals = [first, last, title(pr.get("street", "")), title(pr.get("city", "")),
                (pr.get("state") or "").upper(), (pr.get("zip") or "").split("-")[0]]
        row.update(dict(zip(cols, vals)))
    row["Published on"] = rec.get("date_of_filing") or rec.get("filing_date") or ""
    row["Tag"] = rec.get("estate_type", "")
    return [row[c] for c in PE2_COLUMNS]


# ---------------------------------------------------------------- sheet I/O
def sheets_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    info = json.loads(PE2_TOKEN.read_text(encoding="utf-8"))
    creds = Credentials.from_authorized_user_info(info)
    if not creds.valid:
        creds.refresh(Request())  # in memory only: the Divorce Cases project owns that file
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def pe2_existing(svc) -> tuple[set[str], int, list]:
    """Estate #s already on the tab, the last non-blank row number, and the header row."""
    res = svc.spreadsheets().values().batchGet(
        spreadsheetId=PE2_ID, ranges=[f"'{PE2_TAB}'!A1:AF1", f"'{PE2_TAB}'!A:A"],
        valueRenderOption="UNFORMATTED_VALUE").execute()["valueRanges"]
    header = (res[0].get("values") or [[]])[0]
    col_a = res[1].get("values") or []
    ids = {str(r[0]).strip().upper().removesuffix(".0") for r in col_a[1:] if r and str(r[0]).strip()}
    last = max((i for i, r in enumerate(col_a, 1) if r and str(r[0]).strip()), default=1)
    return ids, last, header


def append_pe2(keys: list[str], estates: dict, commit: bool) -> list[str]:
    svc = sheets_service()
    ids, last, header = pe2_existing(svc)
    if header != PE2_COLUMNS:
        # The tab's layout moved: refuse rather than write into the wrong columns.
        raise SystemExit(f"REFUSED: '{PE2_TAB}' header changed -- expected {len(PE2_COLUMNS)} known "
                         f"columns, got {header}. Update PE2_COLUMNS after checking the sheet.")
    new = [k for k in keys if k.partition("|")[2].strip().upper() not in ids]
    print(f"PE2 '{PE2_TAB}': {len(keys)} unsettled estate(s), {len(keys) - len(new)} already on the tab, "
          f"{len(new)} to append after row {last}")
    if not new:
        return []
    rows = [pe2_row(k, estates[k]) for k in new]
    for k, r in zip(new, rows):
        print(f"  + {k}: {r[1]} {r[2]} | {r[7]} | PR {r[10]} {r[11]} | filed {r[16]} | {r[18]}")
    if not commit:
        print("DRY RUN -- sheet unchanged.")
        return []
    rng = f"'{PE2_TAB}'!A{last + 1}:AF{last + len(rows)}"
    svc.spreadsheets().values().update(
        spreadsheetId=PE2_ID, range=rng, valueInputOption="USER_ENTERED",
        body={"values": rows}).execute()
    # Read back: every appended estate # must now be on the tab.
    ids_after, _, _ = pe2_existing(svc)
    missing = [k for k in new if k.partition("|")[2].strip().upper() not in ids_after]
    if missing:
        raise SystemExit(f"READ-BACK FAILED: {missing} not on the tab after the write ({rng})")
    log = json.loads(APPENDED_LOG.read_text(encoding="utf-8")) if APPENDED_LOG.exists() else {}
    for k in new:
        log[k] = {"date": date.today().isoformat(), "range": rng}
    APPENDED_LOG.parent.mkdir(parents=True, exist_ok=True)
    APPENDED_LOG.write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"appended {len(new)} row(s) at {rng} (read back OK)")
    return new


# ---------------------------------------------------------------- manual list
def manual_rows(day: str, estates: dict, sheet_ids: set[str]) -> list[dict]:
    out = []
    for key, rec in estates.items():
        rev = rec.get("agent_review") or {}
        if rev.get("date") != day:
            continue
        status = rev.get("status", "")
        graded_low = (status == "FOUND" and rec.get("property_lookup_confidence") == "LOW")
        if status == "FOUND" and not graded_low:
            continue
        why = {"NOT_FOUND": "agents found no address", "AMBIGUOUS": "agents found several candidates"}.get(
            status, f"agent street graded LOW by SDAT: {rec.get('property_lookup_reason', '')}")
        county, _, number = key.partition("|")
        out.append({
            "key": key, "estate_number": number, "county": county,
            "decedent": rec.get("decedent_name", ""), "date_of_death": rec.get("date_of_death", ""),
            "date_of_filing": rec.get("date_of_filing", ""), "estate_type": rec.get("estate_type", ""),
            "personal_reps": rar._pr_text(rec.get("personal_reps")),
            "attorney": (rec.get("attorney") or {}).get("name", "") if isinstance(rec.get("attorney"), dict) else "",
            "why_manual": why, "agent_status": status,
            "agent_candidate": ", ".join(x for x in [rec.get("property_street", ""), rec.get("property_city", ""),
                                                     rec.get("property_zip", "")] if x) if status == "FOUND" else "",
            "agent_reason": rev.get("reason", ""), "agent_evidence": rev.get("evidence", ""),
            "sdat_owner": rec.get("property_sdat_owner", ""),
            "prior_lookup_reason": rec.get("property_lookup_reason", "") if status != "FOUND" else "",
            "in_pe2": "yes" if number.strip().upper() in sheet_ids else "added today",
            "che_status": "", "street": "", "city": "", "zip": "", "notes": "",
        })
    return out


def popup(text: str) -> None:
    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    f = REVIEW_ROOT / f"popup_{datetime.now():%Y%m%d_%H%M%S}.txt"
    f.write_text(text, encoding="utf-8")
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                      "-File", str(REMINDER_PS1), "-ReminderFile", str(f)])


def upload(svc, state: dict, day: str, paths: list[Path]) -> str:
    created, updated, _ = drive.sync_day(svc, state, day, paths)
    return f"https://drive.google.com/drive/folders/{state['day_folders'][day]}"


def finish(day: str, commit: bool) -> int:
    day_dir = REVIEW_ROOT / day
    review = day_dir / f"row_address_review_{day}.csv"
    estates = rar.load_ledger().get("estates", {})
    reviewed = {k for k, r in estates.items() if (r.get("agent_review") or {}).get("date") == day}
    if not reviewed:
        print(f"NOTHING REVIEWED on {day}: the agent step wrote no merged results -- a failure, not a quiet day.")
        return 1

    unsettled = [k for k in reviewed
                 if (estates[k].get("agent_review") or {}).get("status") in ("NOT_FOUND", "AMBIGUOUS")]
    appended = append_pe2(unsettled, estates, commit)
    ids, _, _ = pe2_existing(sheets_service())
    manual = manual_rows(day, estates, ids)
    for m in manual:
        if m["key"] in appended:
            m["in_pe2"] = "added today"
    manual_path = day_dir / f"row_manual_review_{day}.csv"
    day_dir.mkdir(parents=True, exist_ok=True)
    with manual_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANUAL_COLUMNS)
        w.writeheader()
        w.writerows(manual)

    found = sum(1 for k in reviewed if estates[k]["agent_review"]["status"] == "FOUND")
    graded = {lv: sum(1 for k in reviewed if estates[k]["agent_review"]["status"] == "FOUND"
                      and estates[k].get("property_lookup_confidence") == lv) for lv in ("HIGH", "MEDIUM", "LOW")}
    summary = (f"RoW agent review {day}: {len(reviewed)} estates reviewed\n"
               f"  agents found {found} (SDAT: {graded['HIGH']} HIGH, {graded['MEDIUM']} MEDIUM, {graded['LOW']} LOW)\n"
               f"  {len(unsettled)} not found / ambiguous; {len(appended)} added to Probate Extraction 2\n"
               f"  {len(manual)} sent to Che for manual review")
    print(summary)
    if not commit:
        print("DRY RUN -- nothing uploaded, no popup.")
        return 0

    state = drive.load_state()
    creds = drive.get_credentials(state, drive.DEFAULT_CLIENT, False)
    from googleapiclient.discovery import build
    dsvc = build("drive", "v3", credentials=creds, cache_discovery=False)
    files = [p for p in (review, manual_path) if p.exists()]
    link = upload(dsvc, state, day, files)
    popup(summary + f"\n\nDrive: {link}\n\nNothing was skip-traced, uploaded or spent. "
          "Che's review comes next; you get another popup when he finishes.")
    return 0


def watch() -> int:
    """Popup once per day folder when Che's agent renames our manual list to '... - REVIEWED'."""
    state = drive.load_state()
    creds = drive.get_credentials(state, drive.DEFAULT_CLIENT, False)
    from googleapiclient.discovery import build
    svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    seen = json.loads(NOTIFY_STATE.read_text(encoding="utf-8")) if NOTIFY_STATE.exists() else {}
    cutoff = date.today() - timedelta(days=14)
    hits = 0
    for day, folder in sorted(state.get("day_folders", {}).items()):
        if date.fromisoformat(day) < cutoff or day in seen:
            continue
        files = svc.files().list(
            q=f"'{folder}' in parents and trashed = false and name contains 'row_manual_review_'",
            fields="files(id, name, modifiedTime, lastModifyingUser(emailAddress))").execute().get("files", [])
        done = [f for f in files if "REVIEWED" in f["name"].upper()]
        if not done:
            continue
        who = (done[0].get("lastModifyingUser") or {}).get("emailAddress", "Che")
        link = f"https://drive.google.com/drive/folders/{folder}"
        popup(f"Che finished the RoW manual review for {day} ({who}).\n\n"
              f"His findings are in the Drive folder:\n{link}\n\n"
              "Next is yours: skip trace / deep prospect / dial-ready and the DataSift upload all wait "
              "on your go. Ask Claude: \"take Che's RoW review for " + day + "\".")
        seen[day] = {"notified": datetime.now().isoformat(timespec="seconds"), "file": done[0]["name"]}
        hits += 1
    NOTIFY_STATE.parent.mkdir(parents=True, exist_ok=True)
    NOTIFY_STATE.write_text(json.dumps(seen, indent=2), encoding="utf-8")
    print(f"watch: {hits} new review(s) notified")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    fi = sub.add_parser("finish", help="manual list + PE2 rows + Drive upload + popup")
    fi.add_argument("--day", default=date.today().isoformat())
    fi.add_argument("--commit", action="store_true", help="write the sheet, upload, popup (default: dry run)")
    sub.add_parser("watch", help="popup when Che's review lands")
    pe = sub.add_parser("pe2-preview", help="print the PE2 rows for some ledger keys (no write)")
    pe.add_argument("keys", nargs="+")
    args = ap.parse_args()
    if args.cmd == "finish":
        return finish(args.day, args.commit)
    if args.cmd == "watch":
        return watch()
    estates = rar.load_ledger()["estates"]
    for k in args.keys:
        print(k, pe2_row(k, estates[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
