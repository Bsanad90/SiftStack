"""Put every property of one owner on ONE dialer, across the Hottest and FTM call lanes.

Why (Basem, 2026-09-24): the DPD call presets now collapse a multi-property owner to one
row ("Show one property per owner"), but the collapse works INSIDE one preset only, and
the 2026-09-01 lane split leveled records by COUNT, not by owner. Measured that day on
Ready to Call .. Call 4: Hottest 5 multi-property owners, 3 split across dialers; FTM 37,
29 split, 24 rows unassigned. One FTM owner with seven properties was held by three
dialers at once -- exactly what the course's rule exists to prevent.

Rule applied, per owner (pooled across BOTH lanes, since the owner is one person):
  target = the dialer already holding the MOST of that owner's rows;
           tie -> the dialer holding the earliest stage (Ready to Call first);
           no dialer holds any -> the dialer with the lightest live load on the two
           Ready-to-Call lanes.
  Only rows not already on the target move. Non-dialer holders (admins) never win.
  Owners already on one dialer are left alone.
This MOVES records between dialers, which the standing rule ("level-up only") forbids
without Basem's explicit go -- given 2026-09-24 ("Go").

Mechanism: `PATCH /api/internal/property/{uuid}/ {"assigned_to": <user-uuid>}` (proven,
`dpd_lane_rebalance.py`), read back per record, per-run backup of prior assignees with
`--undo`. Rows are found by querying each lane preset with `show_properties: all` and
`properties_owned: [2, 999]` (owners with 2+ properties on the ACCOUNT), then keeping the
owners with 2+ rows inside the pooled lanes. `assigned_to` lives only on the detail read.

Usage:
    python -X utf8 -u src/scripts/dpd_owner_group_assign.py             # plan only
    python -X utf8 -u src/scripts/dpd_owner_group_assign.py --commit    # write + read back
    python -X utf8 -u src/scripts/dpd_owner_group_assign.py --verify    # re-measure splits
    python -X utf8 -u src/scripts/dpd_owner_group_assign.py --undo <backup.json>
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dpd_lane_assignee_report import to_query  # noqa: E402
from dpd_lane_rebalance import (DIALERS, NAME_MAP, LiveApi, patch_assignee,  # noqa: E402
                                read_assignee, uuid_of_name)
from dpd_presets_narrow import list_presets  # noqa: E402
from obituary_dp_batch import WriteApi  # noqa: E402

LANES = ["Hottest", "FTM"]
STAGES = ["02 Ready to Call", "03 Call 2", "04 Call 3", "05 Call 4"]
MAX_CONSECUTIVE_ERRORS = 8
OUT = ROOT / "output"


def say(m: str) -> None:
    print(m, flush=True)


def name_of() -> dict[str, str]:
    return json.loads(NAME_MAP.read_text(encoding="utf-8"))["name_of"]


def lane_rows(api: LiveApi, filters: dict, **extra) -> list[dict]:
    q = to_query(filters)
    for k, v in extra.items():
        q["must"][k] = v
    out, off = [], 0
    while True:
        st, r = api.post_as_get("/api/internal/property/", {"limit": 200, "offset": off, "query": q})
        if st != 200:
            raise RuntimeError(f"lane query failed: {st} {str(r)[:200]}")
        res = r.get("results") or []
        out += res
        if not res or len(out) >= (r.get("count") or 0):
            return out
        off += 200


def live_load(api: LiveApi, presets: dict, names: dict[str, str]) -> Counter:
    """Records per dialer on the two Ready-to-Call lanes (the tie-break for ownerless owners)."""
    load: Counter = Counter()
    for lane in LANES:
        f = copy.deepcopy(presets[f"{lane} - 02 Ready to Call"]["filters"])
        f["must"].pop("last_obituary_date", None)
        for d in DIALERS:
            q = to_query(f)
            q["must"]["assigned_to"] = names[d]
            st, r = api.post_as_get("/api/internal/property/", {"limit": 1, "offset": 0, "query": q})
            load[d] += (r.get("count") or 0) if st == 200 else 0
    return load


def build_plan(api: LiveApi) -> dict:
    presets = {p["title"]: p for p in list_presets(WriteApi())}
    nm = name_of()
    uuid_by_name = uuid_of_name()
    stage_of: dict[str, tuple[str, str, dict]] = {}
    for lane in LANES:
        for st in STAGES:
            f = copy.deepcopy(presets[f"{lane} - {st}"]["filters"])
            f["must"].pop("last_obituary_date", None)   # relative dates 400 on the query endpoint
            for r in lane_rows(api, f, show_properties="all", properties_owned=[2, 999]):
                stage_of.setdefault(r["uuid"], (lane, st, r))
    owners: dict[str, list[str]] = defaultdict(list)
    for u, (_lane, _st, r) in stage_of.items():
        o = r.get("owner") or {}
        owners[o.get("uuid") if isinstance(o, dict) else str(o)].append(u)
    multi = {o: us for o, us in owners.items() if len(us) >= 2 and o}
    say(f"  {len(stage_of)} multi-property rows across {len(LANES) * len(STAGES)} presets; "
        f"{len(multi)} owners hold 2+ of them inside the pooled lanes")

    detail: dict[str, dict] = {}
    todo = [u for us in multi.values() for u in us]
    for i, u in enumerate(todo, 1):
        st, body = api.get(f"/api/internal/property/{u}/")
        detail[u] = body if st == 200 else {"_error": st}
        if i % 50 == 0 or i == len(todo):
            say(f"    hydrated {i}/{len(todo)}")

    load = live_load(api, presets, uuid_by_name)
    stage_rank = {s: i for i, s in enumerate(STAGES)}
    dialer_uuids = {uuid_by_name[d] for d in DIALERS}
    plan_owners, moves = [], []
    already_one = 0
    for o, us in multi.items():
        rows = []
        for u in us:
            d = detail[u]
            a = d.get("address") or {}
            rows.append({"uuid": u, "lane": stage_of[u][0], "stage": stage_of[u][1],
                         "assigned_to": d.get("assigned_to"),
                         "street": a.get("street") if isinstance(a, dict) else str(a),
                         "city": a.get("city") if isinstance(a, dict) else ""})
        holders = Counter(r["assigned_to"] for r in rows if r["assigned_to"] in dialer_uuids)
        if holders:
            best = max(holders.values())
            cands = [h for h, n in holders.items() if n == best]
            if len(cands) > 1:
                cands.sort(key=lambda h: min(stage_rank[r["stage"]] for r in rows if r["assigned_to"] == h))
            target = cands[0]
            how = f"holds {best} of {len(rows)}"
        else:
            target = uuid_by_name[min(DIALERS, key=lambda d: (load[d], d))]
            how = "no dialer held any; lightest Ready-to-Call load"
            load[nm[target]] += len(rows)
        mv = [r for r in rows if r["assigned_to"] != target]
        if not mv:
            already_one += 1
            continue
        plan_owners.append({"owner": o, "target": target, "target_name": nm.get(target, target),
                            "how": how, "rows": rows})
        for r in mv:
            moves.append({"uuid": r["uuid"], "owner": o, "lane": r["lane"], "stage": r["stage"],
                          "street": r["street"], "city": r["city"],
                          "from": r["assigned_to"], "from_name": nm.get(r["assigned_to"], "UNASSIGNED") if r["assigned_to"] else "UNASSIGNED",
                          "to": target, "to_name": nm.get(target, target)})
    return {"owners": plan_owners, "moves": moves, "already_one_dialer": already_one,
            "multi_owner_count": len(multi), "load_before": dict(load)}


def write_plan(plan: dict, ts: str) -> Path:
    d = OUT / f"owner_group_assign_{ts}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    lines = [f"# Owner-grouped assignment plan {ts}", "",
             f"Owners with 2+ properties in the pooled Hottest+FTM call lanes: {plan['multi_owner_count']}; "
             f"already on one dialer: {plan['already_one_dialer']}; to consolidate: {len(plan['owners'])}; "
             f"record moves: {len(plan['moves'])}", ""]
    net: Counter = Counter()
    for m in plan["moves"]:
        net[m["to_name"]] += 1
        net[m["from_name"]] -= 1
    lines.append("Net change per dialer (moves in minus moves out): " +
                 ", ".join(f"{k} {v:+d}" for k, v in sorted(net.items())))
    lines.append("")
    for o in plan["owners"]:
        lines.append(f"## owner {o['owner'][:8]} -> {o['target_name']}  ({o['how']})")
        for r in o["rows"]:
            who = plan and next((m["from_name"] for m in plan["moves"] if m["uuid"] == r["uuid"]), None)
            mark = f"MOVE from {who}" if who else "stays"
            lines.append(f"  - {r['lane']:8} {r['stage']:17} {r['street']}, {r['city']}  [{mark}]")
        lines.append("")
    (d / "plan.md").write_text("\n".join(lines), encoding="utf-8")
    return d


def commit(api: LiveApi, plan: dict, run_dir: Path) -> int:
    backup_p = run_dir / "backup.json"
    backup: list[dict] = []
    errors = 0
    for i, m in enumerate(plan["moves"], 1):
        st, _ = patch_assignee(api, m["uuid"], m["to"])
        got = read_assignee(api, m["uuid"]) if st == 200 else None
        ok = st == 200 and got == m["to"]
        if ok:
            backup.append({"uuid": m["uuid"], "assigned_to": m["from"], "set_to": m["to"]})
            errors = 0
        else:
            errors += 1
        say(f"  [{i}/{len(plan['moves'])}] {m['street']}, {m['city']}: {m['from_name']} -> {m['to_name']} "
            f"{'OK' if ok else 'FAIL ' + str(st) + ' readback=' + str(got)}")
        backup_p.write_text(json.dumps(backup, indent=1), encoding="utf-8")
        if errors >= MAX_CONSECUTIVE_ERRORS:
            say("too many consecutive failures; stopping (backup holds what landed)")
            return 1
    say(f"\n  {len(backup)}/{len(plan['moves'])} moves landed and read back. Backup -> {backup_p}")
    return 0 if len(backup) == len(plan["moves"]) else 1


def undo(api: LiveApi, path: Path) -> int:
    recs = json.loads(path.read_text(encoding="utf-8"))
    ok = 0
    for r in recs:
        st, _ = patch_assignee(api, r["uuid"], r.get("assigned_to"))
        ok += (st == 200 and read_assignee(api, r["uuid"]) == r.get("assigned_to"))
    say(f"restored {ok}/{len(recs)}")
    return 0 if ok == len(recs) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--commit", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--undo", metavar="BACKUP_JSON")
    a = ap.parse_args()
    api = LiveApi()
    if a.undo:
        return undo(api, Path(a.undo))
    ts = time.strftime("%Y%m%dT%H%M%S")
    say("Reading the lanes...")
    plan = build_plan(api)
    if a.verify:
        say(f"\nVERIFY: {len(plan['owners'])} owners still split ({len(plan['moves'])} rows off their dialer); "
            f"{plan['already_one_dialer']} owners on one dialer")
        for m in plan["moves"]:
            say(f"  {m['lane']:8} {m['stage']:17} {m['street']}, {m['city']}: {m['from_name']} (target {m['to_name']})")
        return 0 if not plan["moves"] else 1
    run_dir = write_plan(plan, ts)
    say(f"\nPlan: {plan['multi_owner_count']} multi-property owners, {plan['already_one_dialer']} already on one "
        f"dialer, {len(plan['owners'])} to consolidate, {len(plan['moves'])} record moves -> {run_dir / 'plan.md'}")
    net: Counter = Counter()
    for m in plan["moves"]:
        net[m["to_name"]] += 1
        net[m["from_name"]] -= 1
    say("  net per dialer: " + ", ".join(f"{k} {v:+d}" for k, v in sorted(net.items())))
    if not a.commit:
        say("\nPlan only. --commit writes.")
        return 0
    return commit(api, plan, run_dir)


if __name__ == "__main__":
    sys.exit(main())
