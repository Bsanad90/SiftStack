"""Pull the LIVE members of a stored Records preset into a prep-ready CSV.

Default lane: `05 FTM - CALL` / `FTM - 01 Skipped No Numbers` -- the FTM records whose
skip trace already ran and came back with nothing. Per the deep-prospecting escalation,
those are research jobs, not re-skip jobs, and this CSV is the input `obituary_dp_batch.py
prep --export` consumes (same columns as obituary_dp_candidates.EXPORT_COLS).

Why not reuse `obituary_dp_candidates.py`: it pages the WHOLE account and filters
client-side with no FTM tag, no county scope and no suppression, and its
`--require-obituary` is hardwired True. This script instead reads the preset's own stored
filter from the server and queries with it, so the cohort is exactly what the lane loads:
FTM tag, the 9 core counties, zero phones, skiptraced, dead statuses / Low + Negative
Equity / Mail Only all excluded.

Read-only against the account. Detail hydration is resumable via --detail-cache.

    python -u src/scripts/dpd_ftm_dp_candidates.py                 # count + CSV
    python -u src/scripts/dpd_ftm_dp_candidates.py --count-only    # 3 calls, no hydration
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from dpd_lane_assignee_report import count, list_uuids, resolve_lanes, to_query  # noqa: E402
from dpd_lane_rebalance import LiveApi  # noqa: E402  (retrying token mint)
from obituary_dp_batch import deceased_signal  # noqa: E402  (same gate research uses)
from obituary_dp_candidates import EXPORT_COLS, names  # noqa: E402

DEFAULT_FOLDER = "05 FTM - CALL"
DEFAULT_PRESET = "FTM - 01 Skipped No Numbers"

# Measured on the 619-record obituary batch (CLAUDE.md): the per-record budget shape.
SMARTSKIP_PER_ENTITY = 0.15
TRESTLE_PER_NUMBER = 0.015
NUMBERS_PER_RECORD = 15.7      # observed mean, 9,741 numbers / 619 records
RESEARCH_PER_RECORD = 0.20     # Firecrawl + LLM, mostly spent reaching "unresolved"


def build_row(r: dict) -> dict:
    """One detail record -> one EXPORT_COLS row (same shape obituary_dp_candidates writes)."""
    o = r.get("owner") or {}
    ad, ma = r.get("address") or {}, (o.get("address") or {})
    lists, tags = names(r.get("lists")), names(r.get("tags"))
    return {
        "Property address": ad.get("street") or "", "Property city": ad.get("city") or "",
        "Property state": ad.get("state") or "",
        "Property zip5": (ad.get("postal_code") or "")[:5],
        "Property county": ad.get("county") or "",
        "First Name": o.get("first_name") or "", "Last Name": o.get("last_name") or "",
        "Business Name": o.get("company") or "",
        "Mailing address": ma.get("street") or "", "Mailing city": ma.get("city") or "",
        "Mailing state": ma.get("state") or "",
        "Mailing zip5": (ma.get("postal_code") or "")[:5],
        "Lists": ", ".join(lists), "Tags": ", ".join(tags),
        "Last obituary date": (r.get("last_obituary_date") or "")[:10],
        "Personal representative": r.get("personal_representative") or "",
        "Estimated value": r.get("estimate_value") or "",
        "Equity percent": r.get("equity_percent") or "",
        "Owned since": (r.get("owned_since") or "")[:10],
        "Year": r.get("year") or "", "Apn": r.get("parcel_id") or r.get("apn") or "",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", default=DEFAULT_FOLDER)
    ap.add_argument("--preset", default=DEFAULT_PRESET)
    ap.add_argument("--out", default="output/dp_ftm_skipped_nonumbers.csv")
    ap.add_argument("--detail-cache", default="output/dp_ftm_skipped_detail.json")
    ap.add_argument("--count-only", action="store_true",
                    help="resolve + count only (3 API calls); no hydration, no CSV")
    ap.add_argument("--with-phones", action="store_true",
                    help="the lane SELECTS records with numbers (e.g. Deep - 03 Exhausted "
                         "Call): keep phone-bearing records instead of dropping them as "
                         "index lag")
    a = ap.parse_args()

    api = LiveApi()
    print("JWT minted OK", flush=True)

    print(f"Phase 1: resolving {a.preset!r} in {a.folder!r} on the server store...", flush=True)
    lane = resolve_lanes(api, [(a.folder, a.preset)])[0]
    print(f"  stored title {lane['stored_title']!r}")
    print(f"  filters: {json.dumps(lane['filters'])[:800]}", flush=True)

    # THE GUARD (from dpd_lane_assignee_report): an unrecognised filter key is silently
    # ignored and the count comes back as the account total looking like a real answer.
    total = count(api, {})
    query = to_query(lane["filters"])
    n = count(api, query)
    print(f"  account total {total}; lane count {n}", flush=True)
    if n == total:
        print("FAIL: lane count equals the unfiltered account total -- the filter was "
              "ignored rather than applied. Reporting nothing.")
        return 3
    if n == 0:
        print("WARNING: the lane counted ZERO records. That is surprising for this preset "
              "-- open it in the Records UI before trusting this. Nothing written.")
        return 2
    if a.count_only:
        print("--count-only: stopping before hydration.")
        return 0

    print(f"Phase 2: paging {n} uuids...", flush=True)
    uuids = list_uuids(api, query, n)
    if len(uuids) != n:
        print(f"  NOTE: paged {len(uuids)} uuids but count said {n} "
              "(records moving under us mid-read)")

    cache_path = Path(a.detail_cache)
    detail = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    todo = [u for u in uuids if u not in detail]
    print(f"Phase 3: hydrating {len(uuids)} records ({len(uuids) - len(todo)} cached)...",
          flush=True)
    for i, u in enumerate(todo, 1):
        st, body = api.get(f"/api/internal/property/{u}/")
        detail[u] = body if st == 200 else {"_error": f"{st}"}
        if i % 100 == 0 or i == len(todo):
            cache_path.write_text(json.dumps(detail), encoding="utf-8")
            print(f"  {i}/{len(todo)}", flush=True)
    if todo:
        cache_path.write_text(json.dumps(detail), encoding="utf-8")

    # Iterate the LANE's uuids, never the cache: the cache may hold members of an
    # earlier, different run of this script.
    rows, errors, gained_numbers, no_ftm_tag = [], 0, 0, 0
    counties: Counter = Counter()
    lists_tally: Counter = Counter()
    signal: Counter = Counter()
    for u in uuids:
        r = detail.get(u)
        if not isinstance(r, dict) or r.get("_error"):
            errors += 1
            continue
        # The filtered search index lags writes: a record that gained numbers since the
        # count is no longer a candidate, and the detail read is the truth. Only for
        # no-number lanes -- a lane that selects ON having numbers keeps them.
        if not a.with_phones and ((r.get("owner") or {}).get("phones") or []):
            gained_numbers += 1
            continue
        row = build_row(r)
        if "FTM" not in [t.strip() for t in row["Tags"].split(",")]:
            no_ftm_tag += 1
        counties[row["Property county"] or "?"] += 1
        for x in row["Lists"].split(","):
            if x.strip():
                lists_tally[x.strip()] += 1
        sig = deceased_signal(row["Lists"], row["Tags"],
                              row["Last obituary date"], row["Personal representative"])
        signal[sig or "no deceased signal (presumed alive)"] += 1
        rows.append(row)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    import csv
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=EXPORT_COLS)
        w.writeheader()
        w.writerows(rows)

    print(f"\n{out}: {len(rows)} candidates "
          f"({gained_numbers} dropped: gained numbers since the count; {errors} read errors)")
    if no_ftm_tag:
        print(f"  NOTE: {no_ftm_tag} rows do not show the FTM tag in their detail read -- "
              "index lag or a moved record; eyeball a few before trusting the cohort.")
    print("\nby county:")
    for c, k in counties.most_common():
        print(f"  {k:>5}  {c}")
    print("\ntop lists:")
    for c, k in lists_tally.most_common(10):
        print(f"  {k:>5}  {c}")
    print("\ndeceased-signal split (drives hybrid research):")
    research_n = 0
    for c, k in signal.most_common():
        print(f"  {k:>5}  {c}")
        if not c.startswith("no deceased signal"):
            research_n += k
    nn = len(rows)
    print(f"\nbudget estimate for {nn} records (obituary-batch rates):")
    print(f"  SmartSkip  <= ${nn * SMARTSKIP_PER_ENTITY:,.2f}  ({nn} x ${SMARTSKIP_PER_ENTITY}"
          ", less whatever prep marks unusable)")
    print(f"  Trestle     ~ ${nn * NUMBERS_PER_RECORD * TRESTLE_PER_NUMBER:,.2f}  "
          f"({NUMBERS_PER_RECORD} numbers/record x ${TRESTLE_PER_NUMBER})")
    print(f"  research    ~ ${research_n * RESEARCH_PER_RECORD:,.2f}  "
          f"({research_n} records carry a deceased signal; the rest are presumed alive free)")
    print(f"  Tracerfy    ~ $0.02 per phoneless signer (small)")
    print(f"\ngenerated {datetime.now().isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
