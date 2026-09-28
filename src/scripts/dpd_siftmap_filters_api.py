"""Read and PATCH the account's SiftMap saved filters over the map API (no browser).

`GET https://map.reisift.io/filters/?scope=account&page_size=100` (Ty's account-blueprint
`export.py`) returns every saved filter with `auto_add_enabled`, `tags`, `lists`,
`filter_data` and `updated`. PATCH `/filters/{id}/` with a partial body was probed here
2026-09-28 (Basem's go: tags on DC P1-04). Every write is followed by a GET read-back and
the whole inventory is backed up to output/ before the first write; `--undo` restores.

  python src/scripts/dpd_siftmap_filters_api.py --list
  python src/scripts/dpd_siftmap_filters_api.py --id 10192 --tags "Priority 1,Notice of Foreclosure" --commit
  python src/scripts/dpd_siftmap_filters_api.py --id 11496 --obit-window 1996-08-28 2026-08-28 --commit
  python src/scripts/dpd_siftmap_filters_api.py --id 12061 --auto-add on --commit
  python src/scripts/dpd_siftmap_filters_api.py --undo output/dpd_siftmap_filters_backup_<stamp>.json --commit
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = "https://apiv2.reisift.io"
MAP = "https://map.reisift.io"
OBIT_MIN = "extra_last_obituary_date_min"
OBIT_MAX = "extra_last_obituary_date_max"


def _env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class MapApi:
    def __init__(self):
        env = _env()
        body = json.dumps({"email": env["DATASIFT_EMAIL"], "password": env["DATASIFT_PASSWORD"]}).encode()
        req = urllib.request.Request(API + "/api/token/", data=body, method="POST",
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            self.tok = json.load(r)["access"]

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(MAP + path, data=data, method=method, headers={
            "authorization": "Bearer " + self.tok, "accept": "application/json",
            "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:500]

    def all_filters(self) -> list[dict]:
        out, url = [], "/filters/?scope=account&page_size=100&page=1"
        while url:
            st, d = self.call("GET", url)
            if st != 200:
                raise SystemExit(f"GET {url} -> {st} {d}")
            out.extend(d.get("results", d) if isinstance(d, dict) else d)
            nxt = d.get("next") if isinstance(d, dict) else None
            url = nxt.replace(MAP, "") if nxt else None
        return out

    def one(self, fid: int) -> dict:
        st, d = self.call("GET", f"/filters/{fid}/")
        if st != 200:
            raise SystemExit(f"GET /filters/{fid}/ -> {st} {d}")
        return d


def backup(api: MapApi) -> Path:
    p = ROOT / "output" / f"dpd_siftmap_filters_backup_{datetime.now():%Y%m%dT%H%M%S}.json"
    p.write_text(json.dumps(api.all_filters(), indent=1), encoding="utf-8")
    return p


def _dig(d, dotted: str):
    cur = d
    for part in dotted.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


def patch_and_verify(api: MapApi, fid: int, body: dict, expect: dict, before: dict | None = None) -> bool:
    """PATCH then GET. `expect` maps a dotted key to the value it must hold afterwards; every
    other top-level field must read back unchanged from `before` (a partial PATCH must not
    blank what it did not name)."""
    before = before or api.one(fid)
    st, resp = api.call("PATCH", f"/filters/{fid}/", body)
    print(f"  PATCH /filters/{fid}/ {json.dumps(body)[:160]} -> {st}")
    if st not in (200, 202):
        print(f"  refused: {resp}")
        return False
    got = api.one(fid)
    ok = True
    for k, v in expect.items():
        cur = _dig(got, k)
        if cur != v:
            print(f"  READ-BACK MISMATCH {k}: stored {cur!r}, wanted {v!r}")
            ok = False
    for k in ("name", "description", "auto_add_enabled", "replace_owners_enabled", "email_enabled",
              "lists", "tags", "filter_data", "limit", "limit_type"):
        if k in body or any(e == k or e.startswith(k + ".") for e in expect):
            continue
        if got.get(k) != before.get(k):
            print(f"  SIDE EFFECT on {k}: {json.dumps(before.get(k))[:120]} -> {json.dumps(got.get(k))[:120]}")
            ok = False
    flt = (got.get("filter_data") or {}).get("filters")
    print(f"  read-back {'OK' if ok else 'FAILED'}: auto_add={got.get('auto_add_enabled')} tags={got.get('tags')} "
          f"lists={got.get('lists')} filters={json.dumps(flt)[:220]}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--id", type=int)
    ap.add_argument("--tags", help="comma-separated FULL tag list to store on the filter")
    ap.add_argument("--obit-window", nargs=2, metavar=("MIN", "MAX"))
    ap.add_argument("--auto-add", choices=["on", "off"])
    ap.add_argument("--undo", metavar="BACKUP_JSON")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    api = MapApi()
    if a.list:
        for f in api.all_filters():
            flt = (f.get("filter_data") or {}).get("filters", {})
            print(f"{'ON ' if f['auto_add_enabled'] else 'off'} {f['id']:>6} tags={f['tags']} lists={f['lists']} "
                  f"mode={flt.get('in_my_account_mode')} upd={f['updated'][:16]} | {f['name']}")
        return 0
    if a.undo:
        rows = json.loads(Path(a.undo).read_text(encoding="utf-8"))
        if not a.commit:
            print(f"would restore {len(rows)} filters from {a.undo} (add --commit)")
            return 0
        bad = 0
        for f in rows:
            body = {k: f[k] for k in ("name", "description", "auto_add_enabled", "replace_owners_enabled",
                                      "email_enabled", "lists", "tags", "filter_data")}
            if not patch_and_verify(api, f["id"], body, {"tags": f["tags"], "auto_add_enabled": f["auto_add_enabled"]}):
                bad += 1
        return 1 if bad else 0
    if not a.id:
        ap.error("--id required")
    cur = api.one(a.id)
    flt0 = (cur.get("filter_data") or {}).get("filters", {})
    print(f"target {a.id} {cur['name']!r}: auto_add={cur['auto_add_enabled']} tags={cur['tags']} "
          f"obit={flt0.get(OBIT_MIN)}..{flt0.get(OBIT_MAX)}")
    body, expect = {}, {}
    if a.tags is not None:
        tags = [t.strip() for t in a.tags.split(",") if t.strip()]
        body["tags"] = tags
        expect["tags"] = tags
    if a.obit_window:
        fd = json.loads(json.dumps(cur.get("filter_data") or {}))
        flt = fd.setdefault("filters", {})
        if OBIT_MIN not in flt or OBIT_MAX not in flt:
            raise SystemExit(f"filter {a.id} carries no obituary date range -- refusing to add one")
        flt[OBIT_MIN], flt[OBIT_MAX] = a.obit_window
        body["filter_data"] = fd
        expect[f"filter_data.filters.{OBIT_MIN}"] = a.obit_window[0]
        expect[f"filter_data.filters.{OBIT_MAX}"] = a.obit_window[1]
    if a.auto_add:
        body["auto_add_enabled"] = a.auto_add == "on"
        expect["auto_add_enabled"] = a.auto_add == "on"
    if not body:
        ap.error("nothing to change")
    if not a.commit:
        print(f"DRY RUN: would PATCH {json.dumps(body)[:300]}")
        return 0
    print(f"backup -> {backup(api)}")
    return 0 if patch_and_verify(api, a.id, body, expect, before=cur) else 1


if __name__ == "__main__":
    sys.exit(main())
