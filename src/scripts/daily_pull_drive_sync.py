"""Mirror one day's pull folder to the team Google Drive (org Workspace account).

    output/daily_pulls/<date>/*.csv + summary.txt
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
row_manual_review_<day>.csv to the same day folder. Mohammad's findings come back as a
file HIS app created, which drive.file cannot read; the signal Basem's PC watches is
his rename of OUR manual list to "... - REVIEWED.csv" (a file this app created).

Auth: an OAuth user token for the org account, stored in drive_sync_state.json
(gitignored via *_state.json) with the folder ids. Scope is drive.file: the
script sees ONLY the files it created, never the rest of the Drive.

    # one time, opens a browser: sign in with the galaldev.com account
    python src/scripts/daily_pull_drive_sync.py --login
    # one time: share the root folder
    python src/scripts/daily_pull_drive_sync.py --share Mohammad@galaldev.com
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
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
ROOT_NAME = "SiftStack Daily Pulls"
INCLUDE = ("*.csv", "summary.txt")
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


def upload_ready(svc, state: dict, day: str) -> None:
    """READY.txt goes up AFTER every other file of the day, never before."""
    from googleapiclient.http import MediaIoBaseUpload

    folder = state["day_folders"][day]
    body = f"SiftStack daily pull {day} complete at {datetime.now():%Y-%m-%d %H:%M} ET.\n".encode()
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
    files = local_files(day_dir) if day_dir.is_dir() else []

    if args.dry_run:
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
            print(f"NOTHING TO SYNC: {day_dir} missing or has no CSV / summary.txt")
            return 1
        created, updated, skipped = sync_day(svc, state, args.date, files)
        if args.ready:
            upload_ready(svc, state, args.date)
    except HttpError as exc:
        print(f"DRIVE ERROR: {exc}")
        return 3

    folder = state["day_folders"][args.date]
    print(f"Drive sync {args.date}: {created} created, {updated} updated, {skipped} unchanged "
          f"-> https://drive.google.com/drive/folders/{folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
