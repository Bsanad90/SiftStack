"""Move ready-to-call lane records from named dialers to one dialer.

Companion to dpd_lane_rebalance.py, which only hands out UNASSIGNED records.
This one MOVES existing assignments -- built 2026-09-10 on Basem's
instruction ("Mostafa and Galal have a lot of untouched Hottest records,
transfer some to Mariam"). "Untouched" is the lane definition itself: the
`* - 02 Ready to Call` presets filter on predictivecall_attempts == 0.

Targets: the source dialers and the receiver are leveled to the same count
(sum / n, remainders to the sources). Other dialers are not touched.

WRITE PATH is the proven one: PATCH /api/internal/property/{uuid}/
{"assigned_to": <user uuid>}. Each record is re-read immediately before the
write and skipped unless it STILL belongs to the source dialer; every write is
read back. The backup is the dpd_lane_rebalance format, so
`dpd_lane_rebalance.py --undo <backup>` restores it.

Usage:
    python src/scripts/dpd_lane_transfer.py --lane hottest --from "Mostafa Hisham" "Ahmed Galal" --to "Mariam Mohamed" --plan
    python src/scripts/dpd_lane_transfer.py --lane hottest --from "Mostafa Hisham" "Ahmed Galal" --to "Mariam Mohamed" --commit

    # fill mode: hand out the lane's UNASSIGNED records level across the 5
    # dialers (largest deficit first). Same job as dpd_lane_rebalance.py for
    # ONE lane, but from filtered listings (seconds) instead of hydrating every
    # record (10+ minutes), and without the pinned EXPECT shape guard.
    python src/scripts/dpd_lane_transfer.py --lane ftm --fill --plan
    python src/scripts/dpd_lane_transfer.py --lane ftm --fill --commit
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dpd_lane_assignee_report import count, list_uuids, resolve_lanes, to_query  # noqa: E402
from dpd_lane_rebalance import (FTM, HOTTEST, DIALERS, LiveApi, patch_assignee,  # noqa: E402
                                read_assignee)

NAME_MAP = ROOT / "output" / "dpd_assignee_name_map.json"
PLAN_PATH = ROOT / "output" / "dpd_lane_transfer_plan.json"
MAX_CONSECUTIVE_ERRORS = 8
LANES = {"hottest": HOTTEST, "ftm": FTM}


def say(m: str) -> None:
    print(m, flush=True)


def plan(api: LiveApi, lane_name: str, sources: list[str], receiver: str,
         uuid_of: dict[str, str]) -> dict:
    lane = next(l for l in resolve_lanes(api) if l["preset"] == lane_name)
    base = to_query(lane["filters"])

    def q_for(dialer: str) -> dict:
        q = json.loads(json.dumps(base))
        q["must"]["assigned_to"] = uuid_of[dialer]
        return q

    counts = {d: count(api, q_for(d)) for d in sources + [receiver]}
    total = sum(counts.values())
    n = len(sources) + 1
    target = total // n
    rem = total - target * n
    # receiver gets the floor; remainders stay with the sources (they were ahead)
    targets = {receiver: target}
    for i, d in enumerate(sources):
        targets[d] = target + (1 if i < rem else 0)
    moves: dict[str, int] = {d: max(0, counts[d] - targets[d]) for d in sources}
    say(f"lane {lane_name}: " + ", ".join(f"{d} {counts[d]}" for d in sources + [receiver]))
    say(f"  leveling {n} dialers over {total} records -> target {target} (+{rem} remainder)")
    for d in sources:
        say(f"  {d}: {counts[d]} -> {targets[d]}  (move {moves[d]})")
    say(f"  {receiver}: {counts[receiver]} -> {counts[receiver] + sum(moves.values())}")

    picks: list[dict] = []
    for d in sources:
        if moves[d] <= 0:
            continue
        uuids = list_uuids(api, q_for(d), counts[d])
        if len(uuids) < moves[d]:
            raise RuntimeError(f"{d}: listed {len(uuids)} < count {counts[d]}")
        # take from the END of the listing -- deterministic, and the head of
        # the list is what a dialer working top-down reaches first
        for u in uuids[-moves[d]:]:
            picks.append({"uuid": u, "from": d, "from_uuid": uuid_of[d]})
    out = {"lane": lane_name, "receiver": receiver, "receiver_uuid": uuid_of[receiver],
           "counts_before": counts, "targets": targets, "moves": picks,
           "planned_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    PLAN_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    say(f"  {len(picks)} moves planned -> {PLAN_PATH}")
    return out


def plan_fill(api: LiveApi, lane_name: str, uuid_of: dict[str, str]) -> dict:
    lane = next(l for l in resolve_lanes(api) if l["preset"] == lane_name)
    base = to_query(lane["filters"])
    total = count(api, base)
    all_uuids = list_uuids(api, base, total)
    counts, assigned = {}, set()
    for d in DIALERS:
        q = json.loads(json.dumps(base))
        q["must"]["assigned_to"] = uuid_of[d]
        n = count(api, q)
        counts[d] = n
        assigned |= set(list_uuids(api, q, n))
    blanks = [u for u in all_uuids if u not in assigned]
    say(f"lane {lane_name}: total {total}, assigned to the 5 dialers {len(assigned)}, "
        f"blank (or non-dialer) {len(blanks)}")
    say("  " + ", ".join(f"{d} {counts[d]}" for d in DIALERS))
    # largest deficit first: give each blank to the dialer with the smallest book
    running = dict(counts)
    moves = []
    for u in blanks:
        d = min(DIALERS, key=lambda x: (running[x], x))
        running[d] += 1
        moves.append({"uuid": u, "from": None, "from_uuid": None, "to": d,
                      "to_uuid": uuid_of[d]})
    say("  after fill: " + ", ".join(f"{d} {running[d]}" for d in DIALERS))
    out = {"lane": lane_name, "mode": "fill", "counts_before": counts,
           "counts_after": running, "moves": moves,
           "planned_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    PLAN_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    say(f"  {len(moves)} assignments planned -> {PLAN_PATH}")
    return out


def commit(api: LiveApi, p: dict) -> int:
    backup_path = ROOT / "output" / f"dpd_lane_transfer_backup_{time.strftime('%Y%m%dT%H%M%S')}.json"
    backup: list[dict] = []
    ok = skipped = failed = 0
    consecutive = 0
    for i, mv in enumerate(p["moves"], 1):
        u = mv["uuid"]
        to_uuid = mv.get("to_uuid") or p["receiver_uuid"]
        to_name = mv.get("to") or p["receiver"]
        try:
            current = read_assignee(api, u)
        except Exception as exc:                                  # noqa: BLE001
            failed += 1
            consecutive += 1
            say(f"  {u[:8]} read failed: {exc}")
            if consecutive >= MAX_CONSECUTIVE_ERRORS:
                say("ABORT: consecutive errors")
                break
            continue
        if current != mv["from_uuid"]:
            skipped += 1
            say(f"  {u[:8]} skipped: assignee moved since plan ({current})")
            continue
        backup.append({"uuid": u, "assigned_to": current, "set_to": to_uuid,
                       "dialer": to_name})
        st, body = patch_assignee(api, u, to_uuid)
        if st in (200, 202, 204):
            after = read_assignee(api, u)
            if after == to_uuid:
                ok += 1
                consecutive = 0
            else:
                failed += 1
                consecutive += 1
                say(f"  {u[:8]} PATCH {st} but read-back {after}")
        else:
            failed += 1
            consecutive += 1
            backup.pop()
            say(f"  {u[:8]} PATCH {st}: {str(body)[:160]}")
        if consecutive >= MAX_CONSECUTIVE_ERRORS:
            say("ABORT: consecutive errors")
            break
        if i % 10 == 0 or i == len(p["moves"]):
            backup_path.write_text(json.dumps(backup, indent=2), encoding="utf-8")
            say(f"  {i}/{len(p['moves'])} ok={ok} skipped={skipped} failed={failed}")
    backup_path.write_text(json.dumps(backup, indent=2), encoding="utf-8")
    say(f"\n  ok={ok} skipped={skipped} failed={failed}")
    say(f"  Backup -> {backup_path}")
    say(f"  Undo with: python src/scripts/dpd_lane_rebalance.py --undo {backup_path.name}")
    return 0 if failed == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", choices=list(LANES), required=True)
    ap.add_argument("--from", dest="sources", nargs="+")
    ap.add_argument("--to", dest="receiver")
    ap.add_argument("--fill", action="store_true",
                    help="hand out the lane's unassigned records level across the 5 dialers")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--commit", action="store_true", help="re-plans from a fresh read, then writes")
    a = ap.parse_args()

    names = json.loads(NAME_MAP.read_text(encoding="utf-8"))["name_of"]
    uuid_of = {v: k for k, v in names.items()}
    if a.fill:
        if a.sources or a.receiver:
            raise SystemExit("--fill takes no --from/--to")
        api = LiveApi()
        p = plan_fill(api, LANES[a.lane], uuid_of)
        if a.plan or not p["moves"]:
            return 0
        return commit(api, p)
    if not a.sources or not a.receiver:
        raise SystemExit("--from and --to are required unless --fill")
    for d in a.sources + [a.receiver]:
        if d not in uuid_of:
            raise SystemExit(f"unknown dialer {d!r}; known: {sorted(uuid_of)}")
        if d not in DIALERS:
            raise SystemExit(f"{d!r} is not on the 5-dialer roster")
    if a.receiver in a.sources:
        raise SystemExit("receiver cannot also be a source")

    api = LiveApi()
    p = plan(api, LANES[a.lane], a.sources, a.receiver, uuid_of)
    if a.plan:
        return 0
    if not p["moves"]:
        say("nothing to move")
        return 0
    return commit(api, p)


if __name__ == "__main__":
    raise SystemExit(main())
