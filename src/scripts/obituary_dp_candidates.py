"""Find deceased-owner records the `00 Needs Skipped` preset structurally cannot see.

That preset requires NOT skip-traced, so a record whose skip trace already ran and came
back with nothing is excluded -- which is backwards: a failed skip trace is the trigger
for heir research, not a reason to skip it. 3610 Eitemiller Rd (Dale Booth, Obituary list,
Priority 1, zero phones, `Skip Traced IDI 05/2026`) is the case that surfaced it.

Phase 1 of live_pull's listing is cheap and its thin rows already carry `has_phones` and
`skiptraced`, so the expensive detail call is only made for rows that pass that gate.

    python -u src/scripts/obituary_dp_candidates.py --out output/dp_skipped_nonumbers.csv
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

_spec = importlib.util.spec_from_file_location("lp", ROOT / "src" / "scripts" / "live_pull.py")
lp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp)

# The columns obituary_dp_batch.cmd_prep reads.
EXPORT_COLS = ["Property address", "Property city", "Property state", "Property zip5",
               "Property county", "First Name", "Last Name", "Business Name",
               "Mailing address", "Mailing city", "Mailing state", "Mailing zip5",
               "Lists", "Tags", "Last obituary date", "Personal representative",
               "Estimated value", "Equity percent", "Owned since", "Year", "Apn"]


def names(v):
    return [x.get("name") if isinstance(x, dict) else str(x) for x in (v or [])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="output/dp_skipped_nonumbers.csv")
    ap.add_argument("--detail-cache", default="output/dp_skipped_nonumbers_detail.json")
    ap.add_argument("--require-obituary", action="store_true", default=True)
    a = ap.parse_args()

    api = lp.LiveApi()
    print("JWT minted OK", flush=True)
    print("Phase 1: listing every record (thin)...", flush=True)
    thin = lp.list_all(api)
    print(f"  {len(thin)} records on the account", flush=True)

    # The gate the preset gets wrong: no phones, but already flagged skip-traced.
    gate = [t for t in thin if not t.get("has_phones") and t.get("skiptraced")]
    print(f"  {len(gate)} have ZERO phones and are already flagged skiptraced "
          f"(invisible to `00 Needs Skipped`)", flush=True)

    cache_path = Path(a.detail_cache)
    detail = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    print(f"Phase 2: hydrating {len(gate)} of them ({len(detail)} cached)...", flush=True)
    for i, t in enumerate(gate, 1):
        u = t["uuid"]
        if u in detail:
            continue
        st, body = api.get(f"/api/internal/property/{u}/")
        detail[u] = body if st == 200 else {"_error": f"{st}"}
        if i % 100 == 0:
            cache_path.write_text(json.dumps(detail), encoding="utf-8")
            print(f"  {i}/{len(gate)}", flush=True)
    cache_path.write_text(json.dumps(detail), encoding="utf-8")

    rows, skipped = [], 0
    for u, r in detail.items():
        if not isinstance(r, dict) or r.get("_error"):
            continue
        lists, tags = names(r.get("lists")), names(r.get("tags"))
        is_obit = "Obituary" in lists or r.get("last_obituary_date")
        if a.require_obituary and not is_obit:
            skipped += 1
            continue
        # A record that has since gained numbers is no longer a candidate.
        o = r.get("owner") or {}
        if (o.get("phones") or []):
            skipped += 1
            continue
        ad, ma = r.get("address") or {}, (o.get("address") or {})
        rows.append({
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
        })

    out = Path(a.out)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=EXPORT_COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"\n{out}: {len(rows)} candidates "
          f"({skipped} of the gated set dropped: not an obituary record, or already has numbers)")
    print(f"generated {datetime.now().isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
