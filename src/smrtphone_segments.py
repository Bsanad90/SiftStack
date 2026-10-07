"""smrtphone_segments.py - read-only pull of the SmrtPhone v2 interaction log (segments).

This is the data behind Logs -> Interaction logs and its CSV export, reached with the saved
browser session (smrtphone_state.json, captured by skills/cold-call-coach/scripts/
smrtphone_login.py). Proven 2026-10-07:

  GET /api/v1/logs/segments?user_id=<id>&pageSize=100&page=<n>     newest first
      - pageSize caps at 100, and the window stops at 10,000 rows (page 100) for ANY query:
        the account holds ~191k segments, so an unfiltered walk only reaches ~3 weeks back.
      - user_id, segment_type, call_direction, disposition_code and order=asc are honoured;
        every date parameter tried was silently ignored. So: one walk per caller, newest first,
        stop at the period start. A caller makes ~4k dials a month, well inside 10k.
  GET /api/v1/logs/segments/<id>                                    one segment, with user_name

`interaction_at` is UTC; the CSV export prints America/New_York. Dispositions come back as the
same labels the export uses ("No Answer, Left VM", "Correct Number", ...).
The legacy /logs/calls/filtered log purges after 30 days; this one held 2025-10-30 onward.
"""
from __future__ import annotations

import datetime as D
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "smrtphone_state.json"
BASE = "https://phone.smrt.studio"
ET = ZoneInfo("America/New_York")
WINDOW = 10_000
PAGE = 100


class SmrtphoneError(RuntimeError):
    pass


def _headers() -> dict:
    if not STATE.exists():
        raise SmrtphoneError("no smrtphone_state.json - run skills/cold-call-coach/scripts/smrtphone_login.py")
    st = json.loads(STATE.read_text(encoding="utf-8"))
    ck = "; ".join(f"{c['name']}={c['value']}" for c in st.get("cookies", []) if "smrt" in c.get("domain", ""))
    return {"cookie": ck, "x-requested-with": "XMLHttpRequest", "accept": "application/json",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126 Safari/537.36"}


def _get(path: str, params: dict | None = None) -> dict:
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=_headers()), timeout=90) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503) and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise SmrtphoneError(f"GET {path}: HTTP {e.code}") from None
        if body.lstrip().startswith("<"):
            raise SmrtphoneError("SmrtPhone session expired - re-run "
                                 "skills/cold-call-coach/scripts/smrtphone_login.py (headed login)")
        return json.loads(body)
    raise SmrtphoneError(f"GET {path}: retries exhausted")


def _page(params: dict, page: int) -> list[dict]:
    d = _get("/api/v1/logs/segments", {**params, "pageSize": PAGE, "page": page})["data"]
    return d.get("items") or []


def user_ids_recent() -> set[int]:
    """Every user_id in the newest 10k segments (about three weeks of the account)."""
    ids = set()
    for p in range(1, WINDOW // PAGE + 1):
        items = _page({}, p)
        if not items:
            break
        ids |= {x["user_id"] for x in items if x.get("user_id")}
    return ids


def user_name(uid: int) -> str:
    items = _page({"user_id": uid}, 1)
    if not items:
        return f"user {uid}"
    return _get(f"/api/v1/logs/segments/{items[0]['id']}")["data"].get("user_name") or f"user {uid}"


def segments_for_user(uid: int, f: D.date, t: D.date) -> list[dict]:
    """All of one user's segments with an ET date in [f, t]; raises if the 10k window ends first."""
    start = D.datetime.combine(f, D.time.min, ET)
    out, reached = [], False
    for p in range(1, WINDOW // PAGE + 1):
        items = _page({"user_id": uid}, p)
        if not items:
            reached = True
            break
        for x in items:
            at = D.datetime.fromisoformat(x["interaction_at"]).astimezone(ET)
            if at < start:
                reached = True
                break
            if at.date() <= t:
                out.append({**x, "_et": at})
        if reached:
            break
        time.sleep(0.2)
    if not reached:
        raise SmrtphoneError(f"user {uid}: 10,000-row window ended before {f}; the period is too far "
                             "back for the API - use the Interaction Logs CSV export")
    return out


if __name__ == "__main__":
    ids = sorted(user_ids_recent())
    print(json.dumps({u: user_name(u) for u in ids}, indent=1))
