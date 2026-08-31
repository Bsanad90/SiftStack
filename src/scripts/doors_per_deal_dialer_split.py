"""Stratified 5-way dialer split for the doors-per-deal opportunity pool.

Only the currently-UNASSIGNED subset of the 1250 gets split (per Ty^H^H
Basem's 2026-08-24 call: 1019 of 1250 already have a live assignee, some with
active Hot/Warm/follow_up status, and those are left untouched). Within the
unassigned subset, records are grouped by (phone tier, county) and handed out
round-robin across the 5 dialers so each dialer's slice carries the same mix
of tiers and counties rather than a blind sequential chunk.

Usage:
    python src/scripts/doors_per_deal_dialer_split.py
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

OPPORTUNITIES_CSV = ROOT / "output" / "doors_per_deal_opportunities_1250.csv"
LIVE_CACHE = ROOT / "output" / "live_account_pull.json"
VALIDATION_RESULTS = ROOT / "output" / "doors_per_deal_phone_validation" / "validation_results.csv"
OUT_CSV = ROOT / "output" / "doors_per_deal_dialer_split.csv"

TIER_RANK = {"Dial First": 0, "Dial Second": 1, "Dial Third": 2, "Dial Fourth": 3}

# Fixed target counts (2026-08-24, explicit user call after seeing the existing
# assignment skew -- Bagoury already holds 447 of the 1019 pre-existing
# assignments, so gets none of this new pool; Pal/Mariam get the bulk since
# they're the most underweighted, Mostafa gets a small top-up, Ahmed gets none
# this round). Must sum to the dialable unassigned count (230).
DIALER_TARGETS = {
    "Pal John": 110,
    "Mariam Mohamed": 110,
    "Mostafa Hisham": 10,
    "Ahmed Galal": 0,
    "Mohammed Bagoury": 0,
}


def load_csv_rows() -> dict:
    rows = list(csv.DictReader(OPPORTUNITIES_CSV.open(encoding="utf-8-sig")))
    return {r["uuid"]: r for r in rows}


def load_unassigned_uuids(by_uuid_csv: dict) -> set:
    blob = json.loads(LIVE_CACHE.read_text(encoding="utf-8"))
    unassigned = set()
    for entry in blob.get("records") or []:
        rec = entry.get("record")
        if not rec:
            continue
        uuid = rec.get("uuid")
        if uuid in by_uuid_csv and not rec.get("assigned_to"):
            unassigned.add(uuid)
    return unassigned


def load_phone_tiers() -> dict:
    tiers = {}
    with VALIDATION_RESULTS.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            tiers[r["phone_number"]] = r["assigned_tag"]
    return tiers


def best_tier(row: dict, phone_tiers: dict) -> str | None:
    best = None
    for col, val in row.items():
        if not col.startswith("Phone ") or not col.replace("Phone ", "").isdigit():
            continue
        num = (val or "").strip()
        tag = phone_tiers.get(num)
        if tag in TIER_RANK and (best is None or TIER_RANK[tag] < TIER_RANK[best]):
            best = tag
    return best


def stratified_split(uuids: list, by_uuid_csv: dict, phone_tiers: dict, targets: dict) -> dict:
    """Group by (tier, county); within that ordering, hand records out using a
    deficit-weighted round robin so each dialer's share tracks its fixed
    target count throughout the walk (not just in the final tally) -- e.g.
    Pal/Mariam (110 each) get picked far more often than Mostafa (10), but
    interleaved rather than front- or back-loaded, so each dialer still gets
    a representative mix of tiers/counties instead of one lump.
    """
    groups: dict = defaultdict(list)
    dropped_no_phone = []
    for u in uuids:
        row = by_uuid_csv[u]
        tier = best_tier(row, phone_tiers)
        if tier is None:
            dropped_no_phone.append(u)
            continue
        groups[(tier, row["county"])].append(u)

    active_dialers = [d for d, t in targets.items() if t > 0]
    assignment: dict = {}
    dialer_counts = defaultdict(int)

    for (tier, county) in sorted(groups.keys(), key=lambda k: (TIER_RANK[k[0]], k[1])):
        for u in groups[(tier, county)]:
            remaining = [d for d in active_dialers if dialer_counts[d] < targets[d]]
            if not remaining:
                break  # all targets met; anything left over stays unassigned this round
            dialer = min(remaining, key=lambda d: (dialer_counts[d] / targets[d], d))
            assignment[u] = dialer
            dialer_counts[dialer] += 1

    return assignment, dialer_counts, dropped_no_phone


def main() -> None:
    by_uuid_csv = load_csv_rows()
    unassigned = load_unassigned_uuids(by_uuid_csv)
    phone_tiers = load_phone_tiers()

    assignment, dialer_counts, dropped = stratified_split(
        sorted(unassigned), by_uuid_csv, phone_tiers, DIALER_TARGETS
    )

    print(f"Unassigned pool: {len(unassigned)}")
    print(f"Dropped (no dialable phone): {len(dropped)}")
    print(f"Split total: {len(assignment)}")
    target_sum = sum(DIALER_TARGETS.values())
    if len(assignment) != target_sum:
        print(f"WARNING: assigned {len(assignment)} but targets sum to {target_sum} "
              "-- dialable pool size may have shifted since targets were set")
    print()
    for d, target in DIALER_TARGETS.items():
        actual = dialer_counts.get(d, 0)
        flag = "" if actual == target else f"  <-- target was {target}"
        print(f"  {actual:4d}  {d}{flag}")

    fieldnames = ["uuid", "dialer", "county", "Property Street", "Property City",
                  "Property State", "Property ZIP", "matched_signal", "best_tier"]
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for u, dialer in assignment.items():
            row = by_uuid_csv[u]
            w.writerow({
                "uuid": u,
                "dialer": dialer,
                "county": row["county"],
                "Property Street": row["Property Street"],
                "Property City": row["Property City"],
                "Property State": row["Property State"],
                "Property ZIP": row["Property ZIP"],
                "matched_signal": row["matched_signal"],
                "best_tier": best_tier(row, phone_tiers),
            })
    print(f"\nWrote {OUT_CSV}")


if __name__ == "__main__":
    main()
