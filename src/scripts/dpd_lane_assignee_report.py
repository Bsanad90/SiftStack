"""Who is sitting on the two ready-to-call lanes, and who owns those records.

Answers two questions about the LIVE account, read-only:

  1. How many records currently load into `Hottest - 02 Ready to Call`
     (folder `01 HOTTEST - CALL`) and `FTM - 02 Ready to Call` (folder
     `05 FTM - CALL`).
  2. Inside those two lanes, the per-user assigned count and how many carry
     no assignee at all.

Nothing on this account answered that before. `dpd_presets_verify.py` proves a
preset is STORED correctly; this proves what LOADS into it.

Three facts shape the implementation, each verified before it was relied on:

  * A lane count is ONE call: `POST /api/internal/property/` with
    `x-http-method-override: GET` returns `count`. The stored filter is NOT
    accepted verbatim, though -- the store keeps a county as a rich object and
    the query endpoint 400s on it ("Not a valid string."), so `to_query()`
    translates the two shapes. See its docstring.
  * `assigned_to` is a bare user uuid that exists ONLY on the per-record detail
    endpoint. The thin list row does not carry it (checked field by field), so
    the per-user split costs one detail GET per lane record. There is no
    shortcut and pretending otherwise would mean guessing.
  * Preset titles are stored TRUNCATED (~24-26 chars), so they are matched by
    prefix via `dpd_presets_verify.prefix_match`, not by equality.

THE GUARD THAT MATTERS: a filter key the API does not recognise is not an
error, it is silently ignored -- the count comes back as the unfiltered account
total and reads like a real answer. So the unfiltered total is taken FIRST and
any lane count equal to it is treated as a failure, not a result.

Usage:
    python src/scripts/dpd_lane_assignee_report.py --presets-only   # cheap, ~4 calls
    python src/scripts/dpd_lane_assignee_report.py --counts-only    # + lane counts
    python src/scripts/dpd_lane_assignee_report.py                  # + per-user split
    python src/scripts/dpd_lane_assignee_report.py --max-hydrate 500
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dpd_presets_verify import prefix_match  # noqa: E402
from live_pull import LiveApi  # noqa: E402

OUT = ROOT / "output" / "dpd_lane_assignee_report.json"

# (folder title, preset name) -- the two lanes asked for. Preset names come from
# dpd.preset_spec's own construction: f"{label} - {stage_name}".
LANES = [
    ("01 HOTTEST - CALL", "Hottest - 02 Ready to Call"),
    ("05 FTM - CALL", "FTM - 02 Ready to Call"),
]

PAGE = 200
# The API 400s past offset 10000 ("Can't fetch more than 10000 items!") -- see
# live_pull.py. A lane over this needs created-range bucketing, so refuse rather
# than silently truncate.
OFFSET_CEILING = 10000


def resolve_lanes(api: LiveApi, lanes: list[tuple[str, str]] | None = None) -> list[dict]:
    """Find the presets in the server store and return their stored filters.

    `lanes` is (folder title, preset name) pairs; defaults to this script's own
    LANES so existing callers (dpd_lane_rebalance) are unaffected.
    """
    status, folders = api.get("/api/internal/filter-preset-folder/"
                              "?offset=0&limit=999&ordering=title&type=properties")
    if status != 200:
        raise RuntimeError(f"folder list failed: {status} {str(folders)[:200]}")
    by_title = {r["title"]: r["uuid"] for r in folders.get("results") or []}

    out = []
    for folder, preset_name in (lanes if lanes is not None else LANES):
        if folder not in by_title:
            raise RuntimeError(f"folder {folder!r} not on the account; "
                               f"have {sorted(by_title)}")
        status, r = api.get(f"/api/internal/filter-preset-folder/{by_title[folder]}"
                            "/filter-preset/?offset=0&limit=999&ordering=title"
                            "&type=properties")
        if status != 200:
            raise RuntimeError(f"preset list failed for {folder}: {status} {str(r)[:200]}")
        rows = r.get("results") or []
        row = next((s for s in rows if prefix_match(preset_name, s.get("title") or "")), None)
        if row is None:
            raise RuntimeError(f"{preset_name!r} not found in {folder!r}; "
                               f"folder holds {[s.get('title') for s in rows]}")
        out.append({
            "folder": folder,
            "preset": preset_name,
            "stored_title": row.get("title"),
            "preset_uuid": row.get("uuid"),
            "filters": row.get("filters") or {},
        })
    return out


def to_query(filters: dict) -> dict:
    """Translate a STORED preset filter into a QUERY payload.

    The two shapes are not the same and the difference is not cosmetic. The
    store keeps a county as a rich object -- {uuid, title, isNegative} -- while
    the property endpoint wants a bare string and 400s on the object with
    "Not a valid string." (observed live, which is how this was found). An
    `isNegative: true` county must also move from the include list into
    `must_not`, or an exclusion would be silently applied as an inclusion.

    `account` is dropped: the JWT already scopes the account, and it is part of
    the stored blob rather than a filter.
    """
    src = (filters or {}).get("must") or {}
    must: dict = {}
    must_not: dict = dict(src.get("must_not") or {})

    for key, val in src.items():
        if key in ("must_not", "account"):
            continue
        if isinstance(val, list) and val and all(isinstance(x, dict) for x in val):
            pos = [x.get("title") or x.get("uuid") for x in val if not x.get("isNegative")]
            neg = [x.get("title") or x.get("uuid") for x in val if x.get("isNegative")]
            if pos:
                must[key] = pos
            if neg:
                must_not[key] = neg + list(must_not.get(key) or [])
        else:
            must[key] = val

    out: dict = {"must": must}
    if must_not:
        out["must"]["must_not"] = must_not
    return out


def count(api: LiveApi, query: dict) -> int:
    status, r = api.post_as_get("/api/internal/property/",
                                {"limit": 1, "offset": 0, "query": query})
    if status != 200:
        raise RuntimeError(f"count failed: {status} {str(r)[:300]}")
    return r.get("count") or 0


def list_uuids(api: LiveApi, query: dict, expected: int) -> list[str]:
    """Page the lane's uuids. Refuses to page past the API's offset ceiling."""
    if expected > OFFSET_CEILING:
        raise RuntimeError(f"lane holds {expected} records, past the {OFFSET_CEILING} "
                           "offset ceiling -- needs created-range bucketing "
                           "(live_pull.split_into_buckets)")
    out, seen, offset = [], set(), 0
    while offset < expected:
        status, r = api.post_as_get("/api/internal/property/",
                                    {"limit": PAGE, "offset": offset, "query": query})
        if status != 200:
            raise RuntimeError(f"list failed at offset {offset}: {status} {str(r)[:300]}")
        rows = r.get("results") or []
        if not rows:
            break
        for row in rows:
            if row["uuid"] not in seen:
                seen.add(row["uuid"])
                out.append(row["uuid"])
        offset += PAGE
    return out


def hydrate_assignees(api: LiveApi, uuids: list[str], label: str) -> dict:
    """One detail GET per record -- assigned_to lives nowhere else."""
    tally: Counter = Counter()
    blank = 0
    errors: list[str] = []
    t0 = time.time()
    for i, u in enumerate(uuids, 1):
        status, body = api.get(f"/api/internal/property/{u}/")
        if status != 200:
            errors.append(f"{u}: {status}")
            continue
        who = (body or {}).get("assigned_to")
        if who:
            tally[who] += 1
        else:
            blank += 1
        if i % 100 == 0 or i == len(uuids):
            el = time.time() - t0
            rate = i / el if el else 0
            print(f"    {label}: {i}/{len(uuids)}  elapsed={el/60:.1f}m  "
                  f"ETA={((len(uuids)-i)/rate)/60:.1f}m" if rate else
                  f"    {label}: {i}/{len(uuids)}", flush=True)
    return {"by_uuid": dict(tally), "blank": blank, "errors": errors}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--presets-only", action="store_true",
                    help="resolve both presets and print their stored filters; no counting")
    ap.add_argument("--counts-only", action="store_true",
                    help="lane totals only; skip the per-record assignee hydration")
    ap.add_argument("--max-hydrate", type=int, default=0,
                    help="refuse to hydrate a lane larger than this (0 = no limit)")
    args = ap.parse_args()

    api = LiveApi()
    print("JWT minted OK\n", flush=True)

    print("Phase 1: resolving the two presets in the server store...", flush=True)
    lanes = resolve_lanes(api)
    for ln in lanes:
        print(f"  {ln['folder']}  ->  stored title {ln['stored_title']!r}")
        print(f"    filters: {json.dumps(ln['filters'])[:1500]}")
    if args.presets_only:
        print("\n--presets-only: stopping before any counting call.")
        return 0

    # THE GUARD: an unrecognised filter key is ignored, not rejected, and the
    # count comes back as the account total looking like a real answer.
    print("\nPhase 2: baseline (unfiltered) account count...", flush=True)
    total = count(api, {})
    print(f"  account total: {total}", flush=True)

    for ln in lanes:
        ln["query"] = to_query(ln["filters"])
        ln["count"] = count(api, ln["query"])
        print(f"  {ln['preset']}: {ln['count']}", flush=True)
        if ln["count"] == total:
            print(f"\nFAIL: {ln['preset']} counted {ln['count']}, identical to the "
                  "unfiltered account total. The filter was ignored rather than "
                  "applied; reporting nothing.")
            return 3

    result = {"ran_at": time.strftime("%Y-%m-%d %H:%M:%S"), "account_total": total,
              "lanes": lanes}

    if not args.counts_only:
        print("\nPhase 3: reading assigned_to per record (detail endpoint)...", flush=True)
        for ln in lanes:
            if args.max_hydrate and ln["count"] > args.max_hydrate:
                ln["assignees"] = {"skipped": f"{ln['count']} > --max-hydrate "
                                              f"{args.max_hydrate}"}
                print(f"  {ln['preset']}: SKIPPED ({ln['count']} records)", flush=True)
                continue
            uuids = list_uuids(api, ln["query"], ln["count"])
            if len(uuids) != ln["count"]:
                print(f"  NOTE {ln['preset']}: paged {len(uuids)} uuids but count "
                      f"said {ln['count']} (records moving under us mid-read)")
            ln["listed"] = len(uuids)
            ln["uuids"] = uuids
            ln["assignees"] = hydrate_assignees(api, uuids, ln["preset"])

        # The lanes are NOT disjoint by construction: a record carrying both
        # Priority 1 and FTM loads into both, and would be double-counted by
        # anyone adding the two totals.
        sets = [set(ln.get("uuids") or []) for ln in lanes]
        if all(sets):
            result["lane_overlap"] = len(sets[0] & sets[1])

    for ln in lanes:
        ln.pop("uuids", None)  # implementation detail of the overlap check
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    print("\n" + "=" * 68)
    for ln in lanes:
        print(f"\n{ln['folder']}  /  {ln['preset']}")
        print(f"  records in lane: {ln['count']}")
        a = ln.get("assignees") or {}
        if "skipped" in a:
            print(f"  assignee split: skipped ({a['skipped']})")
            continue
        if not a:
            continue
        for who, n in sorted(a["by_uuid"].items(), key=lambda kv: -kv[1]):
            print(f"    {n:>6}  {who}")
        print(f"    {a['blank']:>6}  (no assignee)")
        summed = sum(a["by_uuid"].values()) + a["blank"]
        flag = "OK" if summed == ln.get("listed") else f"MISMATCH vs listed {ln.get('listed')}"
        print(f"    ----- {summed} accounted for  [{flag}]")
        if a["errors"]:
            print(f"    {len(a['errors'])} record(s) failed to read: {a['errors'][:5]}")
    if "lane_overlap" in result:
        print(f"\nrecords in BOTH lanes (carry Priority 1 and FTM): "
              f"{result['lane_overlap']}")
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
