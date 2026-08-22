"""Full live pull of the account's property records via apiv2.reisift.io.

Three phases:
  1. Recursively split the account's `created` date range into buckets under
     the API's hard offset-pagination ceiling. Verified live: any query
     (filtered or not) 400s past offset 10000 with "Can't fetch more than
     10000 items!" -- a flat account-wide paginate dies at record 10,000 of
     26,645. `created` accepts a [start, end] range filter (verified live),
     so each bucket is queried for its own count and split again if still
     over SAFE_LIMIT, the same adaptive-band-splitting shape this codebase
     already uses in zillow_market_api.py for its 41-row-per-call cap.
  2. Page each bucket (well under the ceiling) to collect every uuid + a
     thin fallback row.
  3. Hydrate each uuid via the per-record detail endpoint (slow: one call
     per record at the API's real rate limit). This is the only endpoint
     that carries the fields the 4 Pillars engine scores on -- estimated
     value, equity %, year built, sqft, the Lists array, tax/lien/
     foreclosure/probate fields. The thin list endpoint strips all of that.

A small fraction of uuids 404 on detail even though they list fine (observed
live, not a bug in this script) -- each is counted and kept as a thin-only
fallback row rather than dropped, consistent with this codebase's "nothing
silently discarded" rule.

Checkpoints to the cache file every CHECKPOINT_EVERY records, so a killed or
interrupted run resumes instead of re-paying for records already hydrated.

Usage:
    python src/scripts/live_pull.py [--cache output/live_account_pull.json]
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import dotenv_values

BASE = "https://apiv2.reisift.io"
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
MIN_INTERVAL = 0.45
CHECKPOINT_EVERY = 250
RETRY_HINT = re.compile(r"available in (\d+)", re.I)
SAFE_LIMIT = 9000  # stay well clear of the API's hard 10,000 offset ceiling
EARLIEST_GUESS = datetime(2015, 1, 1, tzinfo=timezone.utc)


class LiveApi:
    def __init__(self):
        v = dotenv_values(str(ENV_PATH))
        self.email = v.get("DATASIFT_EMAIL")
        self.pw = v.get("DATASIFT_PASSWORD")
        if not self.email or not self.pw:
            raise RuntimeError("DATASIFT_EMAIL / DATASIFT_PASSWORD not set in .env")
        self.token = ""
        self.minted = 0.0
        self._last_call = 0.0
        self._mint()

    def _mint(self):
        body = json.dumps({"email": self.email, "password": self.pw}).encode()
        req = urllib.request.Request(BASE + "/api/token/", data=body, method="POST",
                                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as r:
            self.token = json.loads(r.read())["access"]
        self.minted = time.time()

    def _throttle(self):
        gap = time.monotonic() - self._last_call
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        self._last_call = time.monotonic()

    def get(self, path, tries=5):
        return self._call(path, "GET", None, None, tries)

    def post_as_get(self, path, body, tries=5):
        return self._call(path, "POST", body, "GET", tries)

    def _call(self, path, method, body, override, tries):
        if time.time() - self.minted > 1800:
            self._mint()
        for attempt in range(tries):
            self._throttle()
            headers = {"Authorization": "Bearer " + self.token}
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                headers["Content-Type"] = "application/json"
            if override:
                headers["x-http-method-override"] = override
            req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                    return 200, (json.loads(raw) if raw else {})
            except urllib.error.HTTPError as e:
                txt = e.read().decode("utf-8", "replace")
                if e.code == 401 and attempt == 0:
                    self._mint()
                    continue
                if e.code == 429:
                    m = RETRY_HINT.search(txt)
                    wait = min((int(m.group(1)) if m else 30) + 2, 300)
                    time.sleep(wait)
                    continue
                if e.code == 404:
                    return 404, txt
                if attempt == tries - 1:
                    return e.code, txt
                time.sleep(2 ** attempt)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == tries - 1:
                    return 0, "network error, retries exhausted"
                time.sleep(2 ** attempt)
        return 0, "retries exhausted"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _range_count(api: LiveApi, start: datetime, end: datetime) -> int:
    status, r = api.post_as_get(
        "/api/internal/property/",
        {"limit": 1, "offset": 0, "query": {"must": {"created": [_iso(start), _iso(end)]}}},
    )
    if status != 200:
        raise RuntimeError(f"range count failed [{start}, {end}]: {status} {str(r)[:200]}")
    return r.get("count") or 0


def split_into_buckets(api: LiveApi, start: datetime, end: datetime) -> list[tuple[datetime, datetime, int]]:
    """Recursively split [start, end] until every bucket is under SAFE_LIMIT."""
    count = _range_count(api, start, end)
    if count == 0:
        return []
    if count <= SAFE_LIMIT or (end - start) <= timedelta(seconds=1):
        return [(start, end, count)]
    mid = start + (end - start) / 2
    left = split_into_buckets(api, start, mid)
    right = split_into_buckets(api, mid, end)
    return left + right


def list_all(api: LiveApi) -> list[dict]:
    now = datetime.now(timezone.utc) + timedelta(days=1)
    print("  finding date buckets under the 10,000-item ceiling...", flush=True)
    buckets = split_into_buckets(api, EARLIEST_GUESS, now)
    total_expected = sum(c for _, _, c in buckets)
    print(f"  {len(buckets)} bucket(s), {total_expected} records expected", flush=True)

    out, seen = [], set()
    for bi, (start, end, bcount) in enumerate(buckets, 1):
        offset, limit = 0, 200
        got = 0
        while True:
            status, r = api.post_as_get(
                "/api/internal/property/",
                {"limit": limit, "offset": offset,
                 "query": {"must": {"created": [_iso(start), _iso(end)]}}},
            )
            if status != 200:
                raise RuntimeError(f"list phase failed bucket {bi} offset {offset}: {status} {str(r)[:200]}")
            rows = r.get("results") or []
            for row in rows:
                if row["uuid"] not in seen:
                    seen.add(row["uuid"])
                    out.append(row)
            got += len(rows)
            offset += limit
            if offset >= bcount or not rows:
                break
        print(f"  bucket {bi}/{len(buckets)} [{start.date()} .. {end.date()}]: "
              f"{got}/{bcount}  (total so far {len(out)})", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="output/live_account_pull.json")
    args = ap.parse_args()

    cache = Path(args.cache)
    cache.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    pulled_meta = {}
    if cache.exists():
        blob = json.loads(cache.read_text(encoding="utf-8"))
        pulled_meta = {"started": blob.get("started")}
        for rec in blob.get("records", []):
            existing[rec["uuid"]] = rec
        print(f"Resuming from checkpoint: {len(existing)} records already hydrated", flush=True)

    api = LiveApi()
    print("JWT minted OK", flush=True)

    print("Phase 1: paginating the full property list...", flush=True)
    thin_rows = list_all(api)
    print(f"  {len(thin_rows)} total records on the account\n", flush=True)

    print("Phase 2: hydrating detail per record...", flush=True)
    out = []
    n_404 = n_err = n_ok = n_skipped_cached = 0
    t0 = time.time()
    started = pulled_meta.get("started") or time.strftime("%Y-%m-%d %H:%M:%S")

    for i, thin in enumerate(thin_rows, 1):
        u = thin["uuid"]
        if u in existing:
            out.append(existing[u])
            n_skipped_cached += 1
        else:
            status, body = api.get(f"/api/internal/property/{u}/")
            if status == 200:
                out.append({"uuid": u, "thin": thin, "record": body, "error": None})
                n_ok += 1
            elif status == 404:
                out.append({"uuid": u, "thin": thin, "record": None, "error": "404 not found on detail"})
                n_404 += 1
            else:
                out.append({"uuid": u, "thin": thin, "record": None, "error": f"{status}: {str(body)[:150]}"})
                n_err += 1

        if i % CHECKPOINT_EVERY == 0 or i == len(thin_rows):
            cache.write_text(json.dumps(
                {"started": started, "checkpoint_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "total_records": len(thin_rows), "records": out}, default=str), encoding="utf-8")
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed else 0
            remaining = (len(thin_rows) - i) / rate if rate else 0
            print(f"  {i}/{len(thin_rows)}  ok={n_ok} 404={n_404} err={n_err} cached={n_skipped_cached}  "
                  f"elapsed={elapsed/60:.1f}m  ETA={remaining/60:.1f}m", flush=True)

    print(f"\nDone. ok={n_ok} 404={n_404} err={n_err} reused_from_cache={n_skipped_cached}")
    print(f"Wrote {cache}")


if __name__ == "__main__":
    main()
