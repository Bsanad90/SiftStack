"""Download the vendor skip-trace files from the Google Drive "Marketing Lists" folder.

Run with an interpreter that has `google-api-python-client` and `google-auth-oauthlib`
installed (this repo's requirements do not include them), with two variables in `.env`
or the environment:

    MARKETING_LISTS_TOKEN=<path to a Google OAuth token.json with Drive access>
    MARKETING_LISTS_FOLDER_ID=<Drive folder id of the "Marketing Lists" root>

    python src/scripts/vendor_lists_fetch.py --families dialing,skiptraced

READ ONLY against Drive -- this script never creates, edits or deletes anything there.

Why: the account carries ~18.6K records whose phones are tagged Rel{n}.{m} / Pr.{n} but
whose REL / PR name fields are empty (dialers, 2026-09-15). The names live in these
files beside the phones: `*_ReadyForDialing.xlsx` (Deceased / PR / REL names + phones,
what was uploaded) and `*_SkipTraced.xlsx` (vendor output, `INPUT:*` + `PH:*` / `REL{n}:*`).
`rel_pr_name_repair.py index` joins them by property address + phone digits.

Files land under output/vendor_lists/files/<Drive path>/<name>; a manifest at
output/vendor_lists/files_manifest.json records id, Drive path, modified time and the
local path. Re-runs skip files already present with the same modifiedTime.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output" / "vendor_lists"
FILES = OUT / "files"
MANIFEST = OUT / "files_manifest.json"
TREE = OUT / "marketing_lists_tree.json"


def _env(name: str, what: str) -> str:
    """A required setting from the environment (or .env), never from source."""
    v = os.environ.get(name, "").strip()
    if not v:
        # load .env once, lazily, so `--help` works without it
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith(f"{name}="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not v:
        sys.exit(f"set {name} to {what} (in .env or the environment)")
    return v


# The code below is read-only, but the token was granted these scopes; narrowing them here
# would break the refresh of an existing token.
SCOPES = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]

FAMILIES = {
    # what was uploaded to DataSift: Deceased/PR/REL names + PH: Phone1..5 per person
    "dialing": re.compile(r"Ready ?For ?Dialing", re.I),
    # vendor output: INPUT:* + PH:*/REL{n}:* columns
    "skiptraced": re.compile(r"Skip ?Traced", re.I),
    # the vendor INPUT files (no phones) -- only useful for the decedent/PR names
    "2bskip": re.compile(r"2 ?B ?Skip", re.I),
}
GSHEET = "application/vnd.google-apps.spreadsheet"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def say(msg: str) -> None:
    print(msg, flush=True)


def creds() -> Credentials:
    token = Path(_env("MARKETING_LISTS_TOKEN", "the path of a Google OAuth token.json"))
    c = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not c.valid:
        c.refresh(Request())
    return c


def walk(svc, folder_id: str, path: str, out: dict, depth: int = 0) -> None:
    """Depth-first listing of the folder tree into {path: [items]}."""
    items, token = [], None
    while True:
        r = svc.files().list(q=f"'{folder_id}' in parents and trashed = false",
                             fields="nextPageToken,files(id,name,mimeType,modifiedTime,size)",
                             pageSize=1000, pageToken=token).execute()
        items += r.get("files") or []
        token = r.get("nextPageToken")
        if not token:
            break
    out[path] = items
    for it in items:
        if it["mimeType"] == "application/vnd.google-apps.folder":
            walk(svc, it["id"], f"{path}/{it['name']}", out, depth + 1)


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def download(svc, it: dict, dest: Path, tries: int = 4) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(tries):
        try:
            if it["mimeType"] == GSHEET:
                req = svc.files().export_media(fileId=it["id"], mimeType=XLSX)
            else:
                req = svc.files().get_media(fileId=it["id"])
            buf = io.BytesIO()
            dl = MediaIoBaseDownload(buf, req)
            done = False
            while not done:
                _, done = dl.next_chunk()
            dest.write_bytes(buf.getvalue())
            return
        except HttpError as exc:
            if attempt == tries - 1:
                raise
            wait = 3 * (2 ** attempt)
            say(f"    {exc.resp.status} on {it['name']}; retry in {wait}s")
            time.sleep(wait)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--families", default="dialing,skiptraced",
                    help="comma list of " + ",".join(FAMILIES))
    ap.add_argument("--relist", action="store_true",
                    help="re-walk the Drive tree instead of reusing the snapshot")
    ap.add_argument("--limit", type=int, default=0, help="stop after N downloads (smoke)")
    ap.add_argument("--years", default="", help="comma list of top-level year folders to include")
    a = ap.parse_args()

    fams = [FAMILIES[f.strip()] for f in a.families.split(",") if f.strip()]
    folder_id = _env("MARKETING_LISTS_FOLDER_ID", "the Drive folder id of the Marketing Lists root")
    svc = build("drive", "v3", credentials=creds())
    OUT.mkdir(parents=True, exist_ok=True)

    if a.relist or not TREE.exists():
        say("walking the Drive tree...")
        tree: dict = {}
        walk(svc, folder_id, "Marketing Lists", tree)
        TREE.write_text(json.dumps(tree, indent=1), encoding="utf-8")
    else:
        tree = json.loads(TREE.read_text(encoding="utf-8"))
        # the snapshot abbreviates mime types; normalise back to what the API wants
        for items in tree.values():
            for it in items:
                m = it.get("mime") or it.get("mimeType") or ""
                if m == "spreadsheet":
                    it["mimeType"] = GSHEET
                elif m == "sheet":
                    it["mimeType"] = XLSX
                elif m == "folder":
                    it["mimeType"] = "application/vnd.google-apps.folder"
                else:
                    it.setdefault("mimeType", m)
                it.setdefault("modifiedTime", it.get("mod", ""))

    years = {y.strip() for y in a.years.split(",") if y.strip()}
    manifest: dict = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    todo = []
    for path, items in tree.items():
        parts = path.split("/")
        if years and (len(parts) < 2 or parts[1] not in years):
            continue
        for it in items:
            if it["mimeType"] == "application/vnd.google-apps.folder":
                continue
            if not any(f.search(it["name"]) for f in fams):
                continue
            if it["mimeType"] not in (GSHEET, XLSX) and not it["name"].lower().endswith((".xlsx", ".csv", ".xlsm")):
                continue
            prev = manifest.get(it["id"])
            if prev and prev.get("modifiedTime") == it.get("modifiedTime") and Path(prev["local"]).exists():
                continue
            todo.append((path, it))
    say(f"{len(todo)} files to download ({len(manifest)} already in the manifest)")

    # Two Drive files can share a folder AND a name (a Google Sheet beside its xlsx twin,
    # or a re-upload). First run overwrote 160 of them; a claimed path now gets the Drive
    # id appended for every id but the one that already holds it.
    claimed: dict[str, str] = {v["local"]: k for k, v in manifest.items() if not v.get("error")}
    n_ok = n_err = 0
    for i, (path, it) in enumerate(todo, 1):
        name = safe_name(it["name"])
        if it["mimeType"] == GSHEET and not name.lower().endswith(".xlsx"):
            name += ".xlsx"
        dest = FILES / Path(*[safe_name(p) for p in path.split("/")]) / name
        if claimed.get(str(dest), it["id"]) != it["id"]:
            dest = dest.with_name(f"{dest.stem}__{it['id'][:6]}{dest.suffix}")
        claimed[str(dest)] = it["id"]
        try:
            download(svc, it, dest)
            manifest[it["id"]] = {"name": it["name"], "drive_path": path, "mimeType": it["mimeType"],
                                  "modifiedTime": it.get("modifiedTime", ""), "local": str(dest)}
            n_ok += 1
        except Exception as exc:  # noqa: BLE001
            manifest[it["id"]] = {"name": it["name"], "drive_path": path, "mimeType": it["mimeType"],
                                  "modifiedTime": "", "local": str(dest), "error": str(exc)[:200]}
            n_err += 1
            say(f"  FAILED {path}/{it['name']}: {str(exc)[:120]}")
        if i % 20 == 0 or i == len(todo):
            MANIFEST.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
            say(f"  {i}/{len(todo)}  ok={n_ok} err={n_err}")
        if a.limit and n_ok >= a.limit:
            break
    MANIFEST.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    say(f"done: ok={n_ok} err={n_err} manifest={len(manifest)}")
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
