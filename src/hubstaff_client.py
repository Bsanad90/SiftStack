"""hubstaff_client.py - read-only Hubstaff API v2 client (personal access token).

A Hubstaff personal access token is a REFRESH token, and it ROTATES: every exchange at
account.hubstaff.com returns a new refresh token and the old one stops working. So the
newest refresh token (plus the current access token and its expiry) lives in
hubstaff_state.json at the repo root (gitignored by *_state.json), written BEFORE anything
else is done with the response. HUBSTAFF_REFRESH_TOKEN in .env is only the seed for the
very first exchange. If the state file is lost after a rotation, the .env token is dead:
create a new token at developer.hubstaff.com and replace the .env line.

Scope used: hubstaff:read. Nothing here writes to Hubstaff.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "hubstaff_state.json"
TOKEN_URL = "https://account.hubstaff.com/access_tokens"
API = "https://api.hubstaff.com/v2"
# Cloudflare in front of Hubstaff 403s (error 1010) the default Python-urllib signature
UA = "SiftStack-team-kpi/1.0 (+python urllib)"


class HubstaffError(RuntimeError):
    pass


def _env_token() -> str | None:
    v = os.environ.get("HUBSTAFF_REFRESH_TOKEN")
    if v:
        return v.strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("HUBSTAFF_REFRESH_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def _load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def _save_state(st: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def access_token() -> str:
    st = _load_state()
    if st.get("access_token") and st.get("expires_at", 0) > time.time() + 120:
        return st["access_token"]
    refresh = st.get("refresh_token") or _env_token()
    if not refresh:
        raise HubstaffError("no Hubstaff token: add HUBSTAFF_REFRESH_TOKEN to .env")
    data = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh}).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise HubstaffError(f"token exchange failed: HTTP {e.code} {e.read().decode()[:300]}"
                            " - the token may be expired or already rotated; create a new one at "
                            "developer.hubstaff.com and replace HUBSTAFF_REFRESH_TOKEN in .env "
                            "(and delete hubstaff_state.json)") from None
    # save the rotated refresh token FIRST: the old one is already dead
    st.update({"refresh_token": body.get("refresh_token", refresh),
               "access_token": body["access_token"],
               "expires_at": time.time() + int(body.get("expires_in", 3600)),
               "rotated_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    _save_state(st)
    return st["access_token"]


def get(path: str, params: dict | None = None) -> dict:
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token()}",
                                                   "Accept": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(int(e.headers.get("Retry-After", 10)))
                continue
            raise HubstaffError(f"GET {path}: HTTP {e.code} {e.read().decode()[:300]}") from None
    raise HubstaffError(f"GET {path}: rate-limited")


def get_all(path: str, key: str, params: dict | None = None) -> list[dict]:
    """Follow Hubstaff's page_start_id pagination."""
    out, p = [], dict(params or {})
    while True:
        body = get(path, p)
        out.extend(body.get(key, []))
        nxt = (body.get("pagination") or {}).get("next_page_start_id")
        if not nxt:
            return out
        p["page_start_id"] = nxt


if __name__ == "__main__":
    orgs = get("/organizations").get("organizations", [])
    print(json.dumps([{"id": o["id"], "name": o.get("name"), "status": o.get("status")} for o in orgs], indent=1))
