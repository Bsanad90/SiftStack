"""Run DataSift's Enrich (optionally with Swap Owners) on an explicit uuid cohort over the API.

Contract captured 2026-09-23 by route-intercepting the SPA's own "Enrich N Records" click
(the request was aborted at the network layer, so nothing ran) -- the same technique that
found the SiftLine board API:

    POST /api/internal/property/enrich/
    {"query": {"must": {"any_tags": ["<tag uuid>"], "properties": ["<uuid>", ...]}},
     "enrich_property": true, "enrich_owner": true, "replace_owner": true}

It is a COLLECTION-level route: the scope lives INSIDE `query.must` (the trap recorded for
remove-tags / remove-lists -- a top-level `properties` is ignored and the call goes
account-wide; an EMPTY body answered with the count of every property in the account). So
this script never sends a body it has not first proven: the identical `query` is run against
the search endpoint and must return exactly `len(uuids)` before the enrich is posted.

The SPA also sends `"property_type": "clean"` because it sits on the Clean tab; it is omitted
here (= the "All" tab) so incomplete records in the cohort are not silently skipped.

`--swap-owners` sets enrich_owner + replace_owner: the record owner is REPLACED with the
tax-roll owner of record. That destroys a PR/DM owner mapping, so back the cohort up first
(owner block + custom fields) and never run it on probate records whose owner is the PR.

    python src/scripts/enrich_swap_owners_api.py --uuids-csv <csv with a uuid column> \
        [--tag "ZZ Swap Owners 20260923"] [--swap-owners] [--commit]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from live_pull import BASE, LiveApi  # noqa: E402


def tag_uuid(api: LiveApi, title: str) -> str:
    st, r = api.get("/api/internal/tag/?limit=999")
    if st != 200:
        raise RuntimeError(f"tag list {st}")
    rows = r if isinstance(r, list) else (r.get("results") or [])
    for t in rows:
        if (t.get("title") or "") == title:
            return t["uuid"]
    raise RuntimeError(f"tag {title!r} not on the account")


def post(api: LiveApi, path: str, body: dict) -> tuple[int, object]:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + api.token,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode(errors="replace")[:500]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uuids-csv", required=True, help="CSV carrying a `uuid` column")
    ap.add_argument("--tag", help="a tag every cohort record carries; added to the scope as a "
                                  "second fence (any_tags) beside the explicit uuids")
    ap.add_argument("--swap-owners", action="store_true",
                    help="enrich_owner + replace_owner (owner REPLACED by the tax-roll owner)")
    ap.add_argument("--commit", action="store_true", help="actually post the enrich")
    a = ap.parse_args()

    uuids = [r["uuid"] for r in csv.DictReader(open(a.uuids_csv, encoding="utf-8")) if r.get("uuid")]
    if not uuids:
        print("no uuids"); return 2
    api = LiveApi()
    must: dict = {"properties": uuids}
    if a.tag:
        must["any_tags"] = [tag_uuid(api, a.tag)]
    query = {"must": must}

    # GATE: the same scope against the search endpoint must select exactly the cohort.
    st, r = api.post_as_get("/api/internal/property/", {"limit": 1, "offset": 0, "query": query})
    count = r.get("count") if isinstance(r, dict) else None
    print(f"scope check: search count={count} for {len(uuids)} uuids (status {st})")
    if st != 200 or count != len(uuids):
        print("ABORT: the query does not select exactly the cohort; nothing posted")
        return 1

    body = {"query": query, "enrich_property": True,
            "enrich_owner": bool(a.swap_owners), "replace_owner": bool(a.swap_owners)}
    print("enrich body:", json.dumps({**body, "query": {"must": {**must, "properties": f"<{len(uuids)} uuids>"}}}))
    if not a.commit:
        print("dry run (no --commit): nothing posted"); return 0
    st, resp = post(api, "/api/internal/property/enrich/", body)
    print(f"enrich POST -> {st}: {str(resp)[:600]}")
    return 0 if st in (200, 201, 202) else 1


if __name__ == "__main__":
    sys.exit(main())
