"""Mirror one day's pull folder to the team Google Drive (org Workspace account).

    output/daily_pulls/<date>/*.csv + "00 - READ ME FIRST.txt" (plain English, for Che)
        -> Drive "SiftStack Daily Pulls/<date>/"

Run at the end of automation/daily_sift_pulls.ps1 and dc_portal_pull.ps1, so the
folder fills as each runner finishes. Re-running is safe: a file whose bytes are
unchanged (md5) is skipped, a changed one is UPDATED in place (same Drive file id,
so a teammate's open link keeps working), a new one is created. Nothing is ever
deleted on Drive. Run logs and the big *_details.json sidecars stay local.

--ready (the 6 AM runner only): also writes row_needs_address.csv (the Register of
Wills estates still without an address, row_address_review.py export) and uploads
READY.txt LAST, so an agent polling the folder never reads a half-written day.

The RoW agent review (row_review_pipeline.py) adds row_address_review_<day>.csv and
row_manual_review_<day>.csv to the same day folder. Che's findings come back as a
file HIS app created, which drive.file cannot read; the signal Basem's PC watches is
his rename of OUR manual list to "... - REVIEWED.csv" (a file this app created).

Auth: an OAuth user token for the org account, stored in drive_sync_state.json
(gitignored via *_state.json) with the folder ids. Scope is drive.file: the
script sees ONLY the files it created, never the rest of the Drive.

    # one time, opens a browser: sign in with the galaldev.com account
    python src/scripts/daily_pull_drive_sync.py --login
    # one time: share the root folder
    python src/scripts/daily_pull_drive_sync.py --share mohammad@galaldev.com
    # daily (the runners do this)
    python src/scripts/daily_pull_drive_sync.py [--date 2026-10-08] [--ready] [--dry-run]

Exit codes: 0 synced, 1 no pull folder / nothing to upload, 2 auth needed or failed,
3 Drive error.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PULLS = REPO / "output" / "daily_pulls"
STATE_PATH = REPO / "drive_sync_state.json"
# The desktop OAuth client already on this PC (Divorce Cases project). Any
# "installed" client works; only the token below is account-specific.
DEFAULT_CLIENT = REPO.parent / "Divorce Cases" / "Divorce Cases" / "credentials.json"
# Full Drive scope since 2026-10-23 (Basem): drive.file saw only files this app created, so Che's
# hand-off workbooks (his app, his folder inside ours) read as "file not found". Re-minted with --login.
SCOPES = ["https://www.googleapis.com/auth/drive"]
ROOT_NAME = "SiftStack Daily Pulls"
# summary.txt (the runner's exit/rows log) stays local: it reads as noise to anyone but
# the morning Claude session. The team gets README_NAME instead (Basem, 2026-10-08).
INCLUDE = ("*.csv", "00 - READ ME FIRST.txt")
README_NAME = "00 - READ ME FIRST.txt"
READY_NAME = "READY.txt"
ROW_EXPORT_NAME = "row_needs_address.csv"
FOLDER_MIME = "application/vnd.google-apps.folder"
MIME = {".csv": "text/csv", ".txt": "text/plain"}


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_credentials(state: dict, client_path: Path, login: bool):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if login:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
        creds = flow.run_local_server(port=0, prompt="select_account")
        state["token"] = json.loads(creds.to_json())
        save_state(state)
        return creds

    if not state.get("token"):
        print("AUTH NEEDED: no Drive token yet. Run once: "
              "python src/scripts/daily_pull_drive_sync.py --login")
        sys.exit(2)
    creds = Credentials.from_authorized_user_info(state["token"], SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            # An OAuth consent screen in "Testing" mode expires refresh tokens
            # after 7 days; this is where that shows up.
            print(f"AUTH FAILED: Drive token refresh refused ({exc}). Re-run --login.")
            sys.exit(2)
        state["token"] = json.loads(creds.to_json())
        save_state(state)
    return creds


def find_or_create_folder(svc, name: str, parent: str | None, known_id: str | None) -> str:
    if known_id:
        try:
            meta = svc.files().get(fileId=known_id, fields="id, trashed").execute()
            if not meta.get("trashed"):
                return known_id
        except Exception:  # deleted or no longer visible -> recreate below
            pass
    q = f"name = '{name}' and mimeType = '{FOLDER_MIME}' and trashed = false"
    q += f" and '{parent}' in parents" if parent else ""
    hits = svc.files().list(q=q, fields="files(id)", pageSize=10).execute().get("files", [])
    if hits:
        return hits[0]["id"]
    body = {"name": name, "mimeType": FOLDER_MIME}
    if parent:
        body["parents"] = [parent]
    return svc.files().create(body=body, fields="id").execute()["id"]


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def local_files(day_dir: Path) -> list[Path]:
    return sorted(p for p in day_dir.iterdir()
                  if p.is_file() and any(fnmatch.fnmatch(p.name, pat) for pat in INCLUDE))


def sync_day(svc, state: dict, day: str, files: list[Path]) -> tuple[int, int, int]:
    from googleapiclient.http import MediaFileUpload

    root = find_or_create_folder(svc, ROOT_NAME, None, state.get("root_folder_id"))
    state["root_folder_id"] = root
    days = state.setdefault("day_folders", {})
    folder = find_or_create_folder(svc, day, root, days.get(day))
    days[day] = folder
    save_state(state)

    existing = {}
    page = None
    while True:
        resp = svc.files().list(
            q=f"'{folder}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, md5Checksum)",
            pageSize=200, pageToken=page).execute()
        for f in resp.get("files", []):
            existing[f["name"]] = f
        page = resp.get("nextPageToken")
        if not page:
            break

    created = updated = skipped = 0
    for path in files:
        remote = existing.get(path.name)
        if remote and remote.get("md5Checksum") == md5(path):
            skipped += 1
            continue
        mime = MIME.get(path.suffix.lower(), "application/octet-stream")
        media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
        if remote:
            svc.files().update(fileId=remote["id"], media_body=media).execute()
            updated += 1
            print(f"  updated  {path.name}")
        else:
            svc.files().create(body={"name": path.name, "parents": [folder]},
                               media_body=media, fields="id").execute()
            created += 1
            print(f"  created  {path.name}")
    return created, updated, skipped


# --- the plain-English readme for the team (Che) ------------------------------------
# Legs the runner reports in summary.txt, as a reader would name them. A leg missing
# from summary.txt simply did not run yet (the DC portal runner is separate).
LEG_NAMES = {
    "MDDC": "Maryland + DC foreclosures",
    "MDDC-fulltext": "Maryland + DC foreclosures (full notice text)",
    "VA": "Virginia foreclosures (raw)",
    "VA-filter": "Virginia foreclosures (our counties)",
    "VA-fulltext": "Virginia foreclosures (full notice text)",
    "VA-fulltext-fill": "Virginia foreclosures (text fill-in)",
    "MD-RoW": "Maryland Register of Wills",
    "DC-Probate": "DC probate",
}
NO_ADDRESS = {"", "NOT FOUND (CLAUDE)", "NOT FOUND"}


def _rows(path: Path) -> list[dict]:
    import csv
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _by(rows: list[dict], col: str) -> str:
    from collections import Counter
    c = Counter((r.get(col) or "?").strip() or "?" for r in rows)
    return ", ".join(f"{k} {v}" for k, v in c.most_common())


def leg_problems(day_dir: Path) -> list[str]:
    """Plain-English problems from the runner's summary.txt (exit != 0 or no rows)."""
    import re
    path = day_dir / "summary.txt"
    if not path.exists():
        return ["The pull's own status file is missing, so the 6 AM run may not have finished."]
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"\s*([\w-]+):\s*exit=(\S+)\s+rows=(\S+)", line)
        if not m:
            continue
        leg, code, rows = m.groups()
        name = LEG_NAMES.get(leg, leg)
        if code != "0":
            out.append(f"{name}: the pull failed ({code}).")
        elif not rows.isdigit() or rows == "0":
            out.append(f"{name}: came back with no rows ({rows}). Zero is a failure, not a quiet day.")
    return out


FOOTPRINT = {"DISTRICT OF COLUMBIA", "WASHINGTON DC", "MONTGOMERY", "ANNE ARUNDEL", "FREDERICK",
             "FAIRFAX", "ARLINGTON"}


def _split(rows: list[dict], col: str) -> tuple[list[dict], int, int]:
    """(rows in our 6 counties, rows outside them, rows with no county)."""
    ours = [r for r in rows if (r.get(col) or "").strip().upper() in FOOTPRINT]
    blank = sum(1 for r in rows if not (r.get(col) or "").strip())
    return ours, len(rows) - len(ours) - blank, blank


def build_readme(day_dir: Path, day: str) -> str:
    d = date.fromisoformat(day)
    row = _rows(day_dir / "register_of_wills.csv")
    row_addr = [r for r in row if (r.get("Property Address") or "").strip().upper() not in NO_ADDRESS]
    dc = _rows(day_dir / "dc_probates.csv")
    portal = _rows(day_dir / "dc_probates_portal.csv")
    mddc, mddc_out, mddc_blank = _split(_rows(day_dir / "mddc_trustee_sale.csv"), "county")
    va = _rows(day_dir / "va_foreclosures_footprint.csv")
    queue = _rows(day_dir / ROW_EXPORT_NAME)
    problems = leg_problems(day_dir)

    L = ["=" * 64,
         f"  SIFTSTACK DAILY PULL  -  {d:%A, %B} {d.day}, {d.year}",
         "=" * 64,
         "",
         "For: Che",
         f"Updated: {datetime.now():%Y-%m-%d %H:%M} Eastern",
         ""]
    if problems:
        L += ["STATUS:  PROBLEM  -  tell Basem before you use today's files.", ""]
        L += [f"  - {p}" for p in problems]
    else:
        L += ["STATUS:  OK  -  every pull ran and returned records."]

    L += ["", "", "1. PROBATE  (the owner died; we talk to the PR)", "-" * 64, "",
          f"  * Maryland Register of Wills: {len(row)} estates",
          f"      - File: register_of_wills.csv",
          f"      - By county: {_by(row, 'County') or '-'}",
          f"      - {len(row_addr)} have a property address",
          f"      - {len(row) - len(row_addr)} do not yet (Property Address says \"Not Found (Claude)\")",
          "",
          f"  * DC probate, court notices: {len(dc)} estates",
          f"      - File: dc_probates.csv"]
    if portal:
        L += ["", f"  * DC probate, court portal: {len(portal)} cases",
              f"      - File: dc_probates_portal.csv"]

    L += ["", "", "2. FORECLOSURES  (trustee's sale notices)", "-" * 64, "",
          f"  * Maryland + DC: {len(mddc)} notices in our counties",
          f"      - File: mddc_trustee_sale.csv",
          f"      - By county: {_by(mddc, 'county') or '-'}"]
    extra = []
    if mddc_out:
        extra.append(f"{mddc_out} rows outside our counties")
    if mddc_blank:
        extra.append(f"{mddc_blank} with no county")
    if extra:
        L += [f"      - The file also has {' and '.join(extra)} - ignore those"]
    L += ["",
          f"  * Virginia: {len(va)} notices",
          f"      - File: va_foreclosures_footprint.csv",
          f"      - By county: {_by(va, 'county') or '-'}"]

    L += ["", "", "3. YOUR PART", "-" * 64, "",
          "  1) Look over today's files above. Nothing here has been uploaded",
          "     to DataSift, skip-traced or paid for. Basem decides that.",
          "",
          f"  2) Later today, row_manual_review_{day}.csv appears in this folder.",
          "     It lists the Maryland estates Claude's agents could not find",
          "     a property address for. That list is your job.",
          "",
          "  3) To work it, open Claude Code and type:",
          "",
          "         claude --agent row-manual-review",
          "",
          "     It walks you through each estate and uploads your findings.",
          "     Basem gets a popup when you finish.",
          "",
          "", "4. FILES YOU CAN SKIP", "-" * 64, "",
          f"  * {ROW_EXPORT_NAME}",
          f"      - {len(queue)} Maryland estates (all days so far) with no address.",
          "      - Claude's agents work this one, not you.",
          "  * *_full.csv  -  the same notices with the full legal text",
          "  * *_raw.csv  -  raw material behind the files above",
          "  * *_dropped.csv  -  notices thrown out (outside our counties, or not a lead)",
          "  * READY.txt  -  only means the upload finished",
          "",
          "", "Our 6 counties: Washington DC  |  Montgomery, Anne Arundel, Frederick (MD)",
          "                |  Fairfax, Arlington (VA)",
          "Questions: ask Basem."]
    return "\n".join(L) + "\n"


def write_readme(day_dir: Path, day: str) -> Path:
    path = day_dir / README_NAME
    path.write_text(build_readme(day_dir, day), encoding="utf-8")
    return path


def upload_ready(svc, state: dict, day: str, day_dir: Path) -> None:
    """READY.txt goes up AFTER every other file of the day, never before."""
    from googleapiclient.http import MediaIoBaseUpload

    folder = state["day_folders"][day]
    problems = leg_problems(day_dir)
    status = ("Some pulls had problems - see the top of the readme." if problems
              else "Every pull ran and returned records.")
    body = (f"All files for {day} are uploaded ({datetime.now():%Y-%m-%d %H:%M} Eastern).\n"
            f"{status}\nStart with: {README_NAME}\n").encode()
    media = MediaIoBaseUpload(io.BytesIO(body), mimetype="text/plain", resumable=False)
    hits = svc.files().list(q=f"name = '{READY_NAME}' and '{folder}' in parents and trashed = false",
                            fields="files(id)").execute().get("files", [])
    if hits:
        svc.files().update(fileId=hits[0]["id"], media_body=media).execute()
    else:
        svc.files().create(body={"name": READY_NAME, "parents": [folder]},
                           media_body=media, fields="id").execute()
    print(f"  ready    {READY_NAME}")


def share_root(svc, state: dict, email: str, role: str) -> None:
    root = find_or_create_folder(svc, ROOT_NAME, None, state.get("root_folder_id"))
    state["root_folder_id"] = root
    svc.permissions().create(
        fileId=root, sendNotificationEmail=True,
        body={"type": "user", "role": role, "emailAddress": email}).execute()
    shared = state.setdefault("shared_with", {})
    shared[email] = role
    save_state(state)
    print(f"Shared '{ROOT_NAME}' with {email} as {role}.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", default=date.today().isoformat(), help="pull day, YYYY-MM-DD (default today)")
    ap.add_argument("--login", action="store_true", help="one-time browser sign-in with the org account")
    ap.add_argument("--share", metavar="EMAIL", help="share the root folder with this address, then exit")
    ap.add_argument("--role", default="reader", choices=["reader", "commenter", "writer"],
                    help="permission for --share (default reader = view only)")
    ap.add_argument("--client", type=Path, default=DEFAULT_CLIENT, help="OAuth desktop client JSON")
    ap.add_argument("--dry-run", action="store_true", help="list the files that would sync; no Drive calls")
    ap.add_argument("--ready", action="store_true",
                    help="6 AM runner: export row_needs_address.csv, then upload READY.txt last")
    args = ap.parse_args()

    day_dir = PULLS / args.date
    if args.ready and day_dir.is_dir() and not args.dry_run:
        import row_address_review
        row_address_review.export(day_dir / ROW_EXPORT_NAME, date.today())
    if day_dir.is_dir() and any(day_dir.glob("*.csv")):
        write_readme(day_dir, args.date)
    files = local_files(day_dir) if day_dir.is_dir() else []

    if args.dry_run:
        if (day_dir / README_NAME).exists():
            print((day_dir / README_NAME).read_text(encoding="utf-8"))
        print(f"{day_dir}: {len(files)} file(s) would sync to '{ROOT_NAME}/{args.date}/'")
        for p in files:
            print(f"  {p.name}  ({p.stat().st_size:,} bytes)")
        return 0 if files else 1

    state = load_state()
    creds = get_credentials(state, args.client, args.login)

    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    try:
        if args.share:
            share_root(svc, state, args.share, args.role)
            return 0
        if args.login:
            root = find_or_create_folder(svc, ROOT_NAME, None, state.get("root_folder_id"))
            state["root_folder_id"] = root
            save_state(state)
            print(f"Signed in. Root folder: https://drive.google.com/drive/folders/{root}")
            if not files:
                return 0
        if not files:
            # The runner calls this after every leg; an empty day means the pulls
            # themselves failed, which the pull summary already reports.
            print(f"NOTHING TO SYNC: {day_dir} missing or has no CSV")
            return 1
        created, updated, skipped = sync_day(svc, state, args.date, files)
        if args.ready:
            upload_ready(svc, state, args.date, day_dir)
    except HttpError as exc:
        print(f"DRIVE ERROR: {exc}")
        return 3

    folder = state["day_folders"][args.date]
    print(f"Drive sync {args.date}: {created} created, {updated} updated, {skipped} unchanged "
          f"-> https://drive.google.com/drive/folders/{folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
