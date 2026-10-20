"""RoW address review -- the steps AFTER the agents (Basem's PC).

Daily chain (automation/row_agent_review.ps1, Tue-Sat after the 9:15 address fill):
    1. row_address_review.py export           -> queue.csv (checked, still no address)
    2. claude --agent row-address-reviewer     -> 10 estates per researcher subagent
    3. row_address_review.py merge --commit    -> ledger (single writer)
    4. md_register_of_wills_pull --fill-addresses-only --commit -> SDAT grades agent streets
    5. THIS: finish --commit
         * account check (read-only, row_account_check.py): an estate that is ALREADY on the
           DataSift account under the decedent's name, or at the agents' candidate street, is
           HELD BACK from Che's list and from the PE2 append (Basem 2026-10-20: never let him
           spend time on a record already on the account) -> row_manual_review_<day>_on_account.csv.
           A hit under a PR's name only is flagged in `on_account`, not removed. A failed check
           keeps every row and says so in the popup.
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
import row_account_check as rac  # noqa: E402
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
    "on_account",   # '' / 'PR only -- ...' / 'CHECK FAILED: ...' (a decedent hit never reaches the list)
    # Che fills these:
    "che_status", "street", "city", "zip", "notes",
]
CHE_COLUMNS = ["che_status", "street", "city", "zip", "notes"]
# the held-back list: the manual row without Che's columns + where the account already has it
HELD_COLUMNS = [c for c in MANUAL_COLUMNS if c not in CHE_COLUMNS] + [c for c in rac.RESULT_COLUMNS if c != "key"]


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
def manual_rows(day: str, estates: dict, sheet_ids: set[str], only: set[str] | None = None) -> list[dict]:
    out = []
    for key, rec in estates.items():
        rev = rec.get("agent_review") or {}
        if rev.get("date") != day or (only is not None and key not in only):
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
            "on_account": "",
            "che_status": "", "street": "", "city": "", "zip": "", "notes": "",
        })
    return out


def account_check(manual: list[dict], estates: dict) -> tuple[dict[str, dict], str]:
    """READ-ONLY DataSift lookups for every prospective manual row. Returns (key -> result, note);
    the note is non-empty only when the check could not run at all (then nothing is held back)."""
    try:
        from dpd_lane_phone_score import Api
        search = rac.make_search(Api())
        return rac.check_estates([(m["key"], estates[m["key"]]) for m in manual], search), ""
    except Exception as exc:  # noqa: BLE001 -- never let a dead API turn into "not on the account"
        return {}, f"ACCOUNT CHECK FAILED ({type(exc).__name__}: {str(exc)[:120]}) -- list sent UNFILTERED"


def apply_account_check(manual: list[dict], results: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Split the manual rows into (kept, held). ON_ACCOUNT rows are held back with the account
    columns attached; PR_ONLY and ERROR rows stay on the list with the `on_account` column set."""
    kept, held = [], []
    for m in manual:
        res = results.get(m["key"])
        v = res["verdict"] if res else rac.NO
        if v == rac.ON_ACCOUNT:
            row = {c: m.get(c, "") for c in HELD_COLUMNS}
            row.update({k: val for k, val in rac.flat(m["key"], res).items() if k != "key"})
            held.append(row)
            continue
        if v == rac.PR_ONLY:
            m["on_account"] = "PR only -- " + rac.summary_text(m["key"], res)
        elif v == rac.ERROR:
            m["on_account"] = "CHECK FAILED: " + res.get("note", "")
        kept.append(m)
    return kept, held


def popup(text: str) -> None:
    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    f = REVIEW_ROOT / f"popup_{datetime.now():%Y%m%d_%H%M%S}.txt"
    f.write_text(text, encoding="utf-8")
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                      "-File", str(REMINDER_PS1), "-ReminderFile", str(f)])


def upload(svc, state: dict, day: str, paths: list[Path]) -> str:
    created, updated, _ = drive.sync_day(svc, state, day, paths)
    return f"https://drive.google.com/drive/folders/{state['day_folders'][day]}"


def finish(day: str, commit: bool, round_no: int | None = None, skip_account_check: bool = False) -> int:
    # round_no: backlog rounds (Basem 2026-10-14: send Che one round at a time, not the whole
    # backlog). The round covers only the keys in row_address_review_<day>_pass<N>.csv and gets
    # its own list, row_manual_review_<day>_r<N>.csv.
    day_dir = REVIEW_ROOT / day
    tag = f"_r{round_no}" if round_no else ""
    review = day_dir / (f"row_address_review_{day}_pass{round_no}.csv" if round_no
                        else f"row_address_review_{day}.csv")
    estates = rar.load_ledger().get("estates", {})
    reviewed = {k for k, r in estates.items() if (r.get("agent_review") or {}).get("date") == day}
    only = None
    if round_no:
        with review.open(newline="", encoding="utf-8-sig") as fh:
            only = {row["key"] for row in csv.DictReader(fh)}
        reviewed &= only
    if not reviewed:
        print(f"NOTHING REVIEWED on {day}: the agent step wrote no merged results -- a failure, not a quiet day.")
        return 1

    ids, _, _ = pe2_existing(sheets_service())
    manual = manual_rows(day, estates, ids, only)

    # Account check BEFORE anything leaves this PC (Basem 2026-10-20): an estate the account
    # already holds under the decedent's name, or at the agents' candidate street, is held back
    # from Che's list AND from the PE2 "Not Found" append. Read-only; an error holds nothing back.
    held, acct_note, results = [], "", {}
    if skip_account_check:
        acct_note = "account check SKIPPED (--skip-account-check)"
    else:
        results, acct_note = account_check(manual, estates)
        manual, held = apply_account_check(manual, results)
    held_keys = {h["key"] for h in held}
    unchecked = sum(1 for r in results.values() if r["verdict"] == rac.ERROR)
    pr_only = sum(1 for r in results.values() if r["verdict"] == rac.PR_ONLY)

    unsettled = [k for k in reviewed if k not in held_keys
                 and (estates[k].get("agent_review") or {}).get("status") in ("NOT_FOUND", "AMBIGUOUS")]
    appended = append_pe2(unsettled, estates, commit)
    for m in manual:
        if m["key"] in appended:
            m["in_pe2"] = "added today"
    manual_path = day_dir / f"row_manual_review_{day}{tag}.csv"
    held_path = day_dir / f"row_manual_review_{day}{tag}_on_account.csv"
    day_dir.mkdir(parents=True, exist_ok=True)
    with manual_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANUAL_COLUMNS)
        w.writeheader()
        w.writerows(manual)
    if held:
        with held_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=HELD_COLUMNS)
            w.writeheader()
            w.writerows(held)
    elif held_path.exists():
        held_path.unlink()

    found = sum(1 for k in reviewed if estates[k]["agent_review"]["status"] == "FOUND")
    graded = {lv: sum(1 for k in reviewed if estates[k]["agent_review"]["status"] == "FOUND"
                      and estates[k].get("property_lookup_confidence") == lv) for lv in ("HIGH", "MEDIUM", "LOW")}
    acct_line = (f"  {len(held)} already on the account -> held back from Che ({held_path.name})"
                 + (f"; {pr_only} flagged PR-name-only" if pr_only else "")
                 + (f"; {unchecked} could NOT be checked (on the list, see on_account)" if unchecked else ""))
    if acct_note:
        acct_line = f"  ** {acct_note} **"
    summary = (f"RoW agent review {day}{tag}: {len(reviewed)} estates reviewed\n"
               f"  agents found {found} (SDAT: {graded['HIGH']} HIGH, {graded['MEDIUM']} MEDIUM, {graded['LOW']} LOW)\n"
               f"  {len(unsettled)} not found / ambiguous; {len(appended)} added to Probate Extraction 2\n"
               f"{acct_line}\n"
               f"  {len(manual)} sent to Che for manual review")
    print(summary)
    if not commit:
        print("DRY RUN -- nothing uploaded, no popup.")
        return 0

    state = drive.load_state()
    creds = drive.get_credentials(state, drive.DEFAULT_CLIENT, False)
    from googleapiclient.discovery import build
    dsvc = build("drive", "v3", credentials=creds, cache_discovery=False)
    files = [p for p in (review, manual_path, held_path) if p.exists()]
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
        if date.fromisoformat(day) < cutoff:
            continue
        files = svc.files().list(
            q=f"'{folder}' in parents and trashed = false and name contains 'row_manual_review_'",
            fields="files(id, name, modifiedTime, lastModifyingUser(emailAddress))").execute().get("files", [])
        # one popup per REVIEWED list (a day can hold several backlog rounds), keyed by file id
        done = [f for f in files if " - REVIEWED" in f["name"] and f["id"] not in seen]
        if not done:
            continue
        who = (done[0].get("lastModifyingUser") or {}).get("emailAddress", "Che")
        link = f"https://drive.google.com/drive/folders/{folder}"
        popup(f"Che finished the RoW manual review for {day} ({who}): {', '.join(f['name'] for f in done)}.\n\n"
              f"His findings are in the Drive folder:\n{link}\n\n"
              "Next is yours: skip trace / deep prospect / dial-ready and the DataSift upload all wait "
              "on your go. Ask Claude: \"take Che's RoW review for " + day + "\".")
        for f in done:
            seen[f["id"]] = {"day": day, "notified": datetime.now().isoformat(timespec="seconds"),
                             "file": f["name"]}
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
    fi.add_argument("--round", type=int, dest="round_no", help="backlog round N: only that pass's estates")
    fi.add_argument("--commit", action="store_true", help="write the sheet, upload, popup (default: dry run)")
    fi.add_argument("--skip-account-check", action="store_true",
                    help="do not look the estates up on DataSift (emergency only: every row goes to Che)")
    sub.add_parser("watch", help="popup when Che's review lands")
    pe = sub.add_parser("pe2-preview", help="print the PE2 rows for some ledger keys (no write)")
    pe.add_argument("keys", nargs="+")
    args = ap.parse_args()
    if args.cmd == "finish":
        return finish(args.day, args.commit, args.round_no, args.skip_account_check)
    if args.cmd == "watch":
        return watch()
    estates = rar.load_ledger()["estates"]
    for k in args.keys:
        print(k, pe2_row(k, estates[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
