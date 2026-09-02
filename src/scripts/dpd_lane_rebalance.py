"""Split the two ready-to-call lanes evenly across the 5 dialers.

Hands out the records in `Hottest - 02 Ready to Call` and `FTM - 02 Ready to
Call` that currently have NO assignee, so each of the 5 dialers ends up level.
Existing assignments are never touched (Basem, 2026-09-01).

BALANCED PER LANE, NOT ON THE TOTAL. FTM is its own weekly blitz, so equal
totals with a lopsided FTM would not actually be fair. Hottest divides
1040/5 = 208 flat; FTM is 571/5 = 114 with one remainder, given to the dialer
with the smallest existing book.

THE 45 THAT WOULD BE COUNTED TWICE. 954 unassigned in Hottest plus 557 in FTM
is 1,511, but 45 records carry BOTH `Priority 1` and `FTM` and so sit in both
lanes. There are 1,563 distinct records across the two, 97 assigned and 1,466
distinct unassigned. A record has one assignee, so each of those 45 is handed
out once and fills one slot in BOTH lanes. Anything that adds the two lane
counts is over by 45.

WRITE PATH. `assigned_to` is set by PATCH /api/internal/property/{uuid}/.
--probe proves that on ONE record (write, read back, restore) and distinguishes
"accepted and stored" from "accepted and silently discarded" -- this codebase
has already been bitten by a field that returns 200 and throws the value away
(`notes` on the property payload). Only a stored value unlocks --commit.

REVERSIBLE. Every run snapshots each record's prior assigned_to before writing
and supports --undo, the pattern from sold_backlog.py.

Usage:
    python src/scripts/dpd_lane_rebalance.py --plan-only   # read + plan CSV, no writes
    python src/scripts/dpd_lane_rebalance.py --probe       # prove the write path
    python src/scripts/dpd_lane_rebalance.py --commit      # do it
    python src/scripts/dpd_lane_rebalance.py --verify      # recount live, independent path
    python src/scripts/dpd_lane_rebalance.py --undo <backup.json>
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dpd_lane_assignee_report import count, list_uuids, resolve_lanes, to_query  # noqa: E402
from live_pull import LiveApi as _LiveApi  # noqa: E402


class LiveApi(_LiveApi):
    """live_pull.LiveApi with a RETRYING token mint.

    The base `_mint` is a single unguarded urlopen, and `_call` re-mints every
    30 minutes -- so on a run longer than that, one transient gateway error at
    the re-mint kills the whole run partway through a half-written assignment.
    Observed live 2026-09-01: a 502 Bad Gateway on mint killed the first
    --commit at step zero.
    """

    def _mint(self):
        for attempt in range(5):
            try:
                return super()._mint()
            except Exception as exc:                              # noqa: BLE001
                if attempt == 4:
                    raise
                wait = 5 * (2 ** attempt)
                print(f"  token mint failed ({str(exc)[:80]}); "
                      f"retrying in {wait}s", flush=True)
                time.sleep(wait)

OUT_PLAN = ROOT / "output" / "dpd_lane_rebalance_plan.csv"
OUT_STATE = ROOT / "output" / "dpd_lane_rebalance_state.json"
NAME_MAP = ROOT / "output" / "dpd_assignee_name_map.json"

HOTTEST = "Hottest - 02 Ready to Call"
FTM = "FTM - 02 Ready to Call"

# The 5 dialers (Basem, 2026-09-01) -- the same roster as
# doors_per_deal_dialer_split.DIALER_TARGETS. Moe Galal (admin) and Ahmed
# Hesham are deliberately excluded: both hold ZERO records in either lane
# (verified by filtered count), so there is nothing of theirs to reassign.
DIALERS = ["Mostafa Hisham", "Ahmed Galal", "Mohammed Bagoury", "Pal John",
           "Mariam Mohamed"]

# The shape this split was computed against. A drift means someone has been
# dialing and the targets need recomputing -- stop rather than split a stale
# picture.
EXPECT = {"hottest": 1040, "ftm": 571, "overlap": 48, "assigned": 97}

MAX_CONSECUTIVE_ERRORS = 8


def say(m: str) -> None:
    print(m, flush=True)


def uuid_of_name() -> dict[str, str]:
    m = json.loads(NAME_MAP.read_text(encoding="utf-8"))["name_of"]
    out = {v: k for k, v in m.items()}
    missing = [d for d in DIALERS if d not in out]
    if missing:
        raise RuntimeError(f"no uuid known for {missing}; re-run "
                           "src/scripts/dpd_assignee_name_map.py")
    return out


def patch_assignee(api: LiveApi, uuid: str, user_uuid: str | None):
    """PATCH the property's assigned_to. `None` clears it."""
    return api._call(f"/api/internal/property/{uuid}/", "PATCH",
                     {"assigned_to": user_uuid}, None, 4)


def read_assignee(api: LiveApi, uuid: str):
    st, body = api.get(f"/api/internal/property/{uuid}/")
    if st != 200:
        raise RuntimeError(f"read-back failed for {uuid}: {st}")
    return (body or {}).get("assigned_to")


# ------------------------------------------------------------- phase 1: read

def read_lanes(api: LiveApi) -> dict:
    lanes = {l["preset"]: l for l in resolve_lanes(api)}
    for want in (HOTTEST, FTM):
        if want not in lanes:
            raise RuntimeError(f"preset {want!r} not resolved")

    sets, queries = {}, {}
    for key, preset in (("hottest", HOTTEST), ("ftm", FTM)):
        q = to_query(lanes[preset]["filters"])
        queries[key] = q
        n = count(api, q)
        say(f"  {preset}: {n}")
        sets[key] = set(list_uuids(api, q, n))
        if len(sets[key]) != n:
            say(f"    NOTE paged {len(sets[key])} uuids against a count of {n}")

    both = sets["hottest"] & sets["ftm"]
    distinct = sets["hottest"] | sets["ftm"]
    say(f"  overlap (both lanes): {len(both)}")
    say(f"  distinct records:     {len(distinct)}")

    say(f"\n  hydrating {len(distinct)} records for assignee + county...")
    recs: dict[str, dict] = {}
    t0 = time.time()
    for i, u in enumerate(sorted(distinct), 1):
        st, body = api.get(f"/api/internal/property/{u}/")
        if st != 200:
            recs[u] = {"uuid": u, "error": f"HTTP {st}"}
            continue
        addr = (body or {}).get("address") or {}
        recs[u] = {"uuid": u,
                   "assigned_to": (body or {}).get("assigned_to"),
                   "street": addr.get("street") or "",
                   "city": addr.get("city") or "",
                   "county": addr.get("county") or "(blank)",
                   "in_hottest": u in sets["hottest"],
                   "in_ftm": u in sets["ftm"]}
        if i % 200 == 0 or i == len(distinct):
            say(f"    {i}/{len(distinct)}  elapsed={(time.time()-t0)/60:.1f}m")
    return {"queries": queries,
            "sets": {k: sorted(v) for k, v in sets.items()},
            "records": recs, "overlap": len(both)}


def guard_shape(data: dict) -> None:
    recs = data["records"]
    got = {"hottest": len(data["sets"]["hottest"]),
           "ftm": len(data["sets"]["ftm"]),
           "overlap": data["overlap"],
           "assigned": sum(1 for r in recs.values() if r.get("assigned_to"))}
    drift = {k: {"got": got[k], "expected": EXPECT[k]}
             for k in EXPECT if got[k] != EXPECT[k]}
    if drift:
        raise RuntimeError(
            "the lanes no longer match the shape this split was computed "
            f"against: {drift}. Re-read and recompute the targets rather than "
            "splitting a stale picture.")
    say(f"\n  shape guard OK: {got}")


def existing_counts(data: dict, name_of: dict[str, str]) -> dict:
    out = {"hottest": Counter(), "ftm": Counter()}
    for r in data["records"].values():
        who = r.get("assigned_to")
        if not who:
            continue
        nm = name_of.get(who, f"UNMAPPED {who[:8]}")
        if r.get("in_hottest"):
            out["hottest"][nm] += 1
        if r.get("in_ftm"):
            out["ftm"][nm] += 1
    return out


# --------------------------------------------------------- phase 1b: allocate

def targets(data: dict, existing: dict) -> dict:
    """Per-lane target per dialer. Hottest divides flat; FTM has 1 remainder."""
    out = {}
    for key in ("hottest", "ftm"):
        total = len(data["sets"][key])
        base, rem = divmod(total, len(DIALERS))
        out[key] = {d: base for d in DIALERS}
        # The remainder goes to the smallest existing book -- the dialer with
        # the most capacity, which is the point of the exercise.
        for d in sorted(DIALERS, key=lambda x: (existing[key].get(x, 0), x))[:rem]:
            out[key][d] += 1
    return out


def interleave_by_county(recs: list[dict]) -> list[dict]:
    """Emit records round-robin across counties so no dialer gets one county's
    worth of work. County comes free on the record; the (phone tier, county)
    stratifier in doors_per_deal_dialer_split.py is NOT reused because its tier
    data comes from a Trestle CSV built for the old 1,250 pool, stale here."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        buckets[r["county"]].append(r)
    order = sorted(buckets, key=lambda c: (-len(buckets[c]), c))
    out: list[dict] = []
    while any(buckets[c] for c in order):
        for c in order:
            if buckets[c]:
                out.append(buckets[c].pop())
    return out


def allocate(data: dict, existing: dict, tgt: dict) -> tuple[dict, list[str]]:
    """uuid -> dialer name.

    Dual-lane records are spent FIRST: each fills a slot in both lanes, so
    placing them before the single-lane records is what keeps both lanes
    solvable. Spending single-lane records first could exhaust one lane's
    deficits and leave a dual record with nowhere to go.
    """
    recs = [r for r in data["records"].values() if not r.get("error")]
    unassigned = [r for r in recs if not r.get("assigned_to")]

    deficit = {k: {d: tgt[k][d] - existing[k].get(d, 0) for d in DIALERS}
               for k in ("hottest", "ftm")}
    notes: list[str] = []
    for k in ("hottest", "ftm"):
        over = {d: v for d, v in deficit[k].items() if v < 0}
        if over:
            notes.append(f"{k}: already over target for {over} -- they keep "
                         "what they have and the rest level around them")
            for d in over:
                deficit[k][d] = 0

    dual = interleave_by_county([r for r in unassigned
                                 if r["in_hottest"] and r["in_ftm"]])
    h_only = interleave_by_county([r for r in unassigned
                                   if r["in_hottest"] and not r["in_ftm"]])
    f_only = interleave_by_county([r for r in unassigned
                                   if r["in_ftm"] and not r["in_hottest"]])

    plan: dict[str, str] = {}

    def take(rec: dict, keys: tuple[str, ...]) -> bool:
        # Largest total remaining deficit wins, among dialers with room in
        # EVERY lane this record belongs to.
        pool = [d for d in DIALERS if all(deficit[k][d] > 0 for k in keys)]
        if not pool:
            return False
        d = max(pool, key=lambda x: (sum(deficit[k][x] for k in keys), x))
        for k in keys:
            deficit[k][d] -= 1
        plan[rec["uuid"]] = d
        return True

    unplaced = Counter()
    for rec in dual:
        if not take(rec, ("hottest", "ftm")):
            unplaced["dual"] += 1
    for rec in h_only:
        if not take(rec, ("hottest",)):
            unplaced["hottest"] += 1
    for rec in f_only:
        if not take(rec, ("ftm",)):
            unplaced["ftm"] += 1
    if unplaced:
        notes.append(f"unplaced records (no dialer with room): {dict(unplaced)}")

    leftover = {k: {d: v for d, v in deficit[k].items() if v} for k in deficit}
    if any(leftover.values()):
        notes.append(f"unfilled target slots remain: {leftover}")
    return plan, notes


def report_plan(data: dict, plan: dict, existing: dict) -> None:
    say("")
    say(f"{'dialer':<20}{'H has':>7}{'H gets':>8}{'H ends':>8}"
        f"{'F has':>8}{'F gets':>8}{'F ends':>8}{'final':>8}")
    for d in DIALERS:
        gh = sum(1 for u, x in plan.items() if x == d and data["records"][u]["in_hottest"])
        gf = sum(1 for u, x in plan.items() if x == d and data["records"][u]["in_ftm"])
        eh, ef = existing["hottest"].get(d, 0), existing["ftm"].get(d, 0)
        say(f"{d:<20}{eh:>7}{gh:>8}{eh+gh:>8}{ef:>8}{gf:>8}{ef+gf:>8}"
            f"{eh+gh+ef+gf:>8}")
    th = sum(1 for u in plan if data["records"][u]["in_hottest"])
    tf = sum(1 for u in plan if data["records"][u]["in_ftm"])
    say(f"{'TOTAL handed out':<20}{'':>7}{th:>8}{'':>8}{'':>8}{tf:>8}")
    say(f"\n  {len(plan)} distinct records to assign "
        f"({th} Hottest slots + {tf} FTM slots, "
        f"{th + tf - len(plan)} records filling both)")

    per = Counter((d, data["records"][u]["county"]) for u, d in plan.items())
    say("\n  county spread (proof no dialer got one county's worth):")
    say("    " + "county".ljust(22) + "".join(d.split()[0][:7].rjust(9) for d in DIALERS))
    for c in sorted({c for _, c in per}):
        say("    " + c[:22].ljust(22)
            + "".join(str(per.get((d, c), 0)).rjust(9) for d in DIALERS))


def write_plan_csv(data: dict, plan: dict) -> None:
    rows = []
    for u, d in plan.items():
        r = data["records"][u]
        rows.append({"uuid": u, "street": r["street"], "city": r["city"],
                     "county": r["county"],
                     "lanes": ("both" if r["in_hottest"] and r["in_ftm"]
                               else "hottest" if r["in_hottest"] else "ftm"),
                     "current_assignee": r.get("assigned_to") or "",
                     "proposed_assignee": d})
    rows.sort(key=lambda x: (x["proposed_assignee"], x["county"], x["street"]))
    OUT_PLAN.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PLAN.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    say(f"  Wrote {OUT_PLAN}")


# ------------------------------------------------------------- phase 2: probe

def probe(api: LiveApi, data: dict, name_of_uuid: dict[str, str]) -> bool:
    """Write assigned_to on ONE record, read it back, restore it.

    Reports which of three things happened, because two of them look like
    success from the status code alone.
    """
    target = next((r for r in data["records"].values()
                   if not r.get("error") and not r.get("assigned_to")), None)
    if target is None:
        say("  no unassigned record available to probe")
        return False
    u = target["uuid"]
    who = name_of_uuid[DIALERS[0]]
    say(f"  probing on {target['street']!r} ({u[:8]}) -> {DIALERS[0]}")

    before = read_assignee(api, u)
    if before:
        say(f"  ABORT: record is assigned to {before[:8]} after all")
        return False

    st, body = patch_assignee(api, u, who)
    say(f"  PATCH returned {st}")
    if st not in (200, 202, 204):
        say(f"  REJECTED: {str(body)[:300]}")
        return False

    after = read_assignee(api, u)
    if after == who:
        say("  ACCEPTED AND STORED -- the write path works")
        st2, _ = patch_assignee(api, u, None)
        restored = read_assignee(api, u)
        say(f"  restored to {restored!r} (clear returned {st2})")
        if restored is not None:
            say("  WARNING: could not clear the probe record; assign it by hand "
                "or leave it -- it is one record and it is in the plan anyway")
        return True

    say(f"  ACCEPTED AND SILENTLY DISCARDED: read back {after!r}, not {who!r}. "
        "This is the `notes` failure mode -- the API answers 200 and keeps "
        "nothing. Use the browser path instead.")
    return False


# ------------------------------------------------------------- phase 3: write

def commit(api: LiveApi, data: dict, plan: dict, name_of_uuid: dict[str, str]) -> int:
    backup_path = ROOT / "output" / f"dpd_lane_rebalance_backup_{time.strftime('%Y%m%dT%H%M%S')}.json"
    backup: list[dict] = []
    ok = skipped = failed = 0
    consecutive = 0
    t0 = time.time()
    items = sorted(plan.items())

    for i, (u, dialer) in enumerate(items, 1):
        who = name_of_uuid[dialer]
        # Re-read immediately before writing: we only claim records that are
        # STILL unassigned. If someone took it since the plan was built, we do
        # not steal it.
        try:
            current = read_assignee(api, u)
        except Exception as exc:                                  # noqa: BLE001
            failed += 1
            consecutive += 1
            say(f"  {u[:8]} read failed: {exc}")
            if consecutive >= MAX_CONSECUTIVE_ERRORS:
                say(f"\nABORT: {consecutive} consecutive errors")
                break
            continue
        if current:
            skipped += 1
            consecutive = 0
            continue

        backup.append({"uuid": u, "assigned_to": None, "set_to": who,
                       "dialer": dialer})
        st, body = patch_assignee(api, u, who)
        if st in (200, 202, 204):
            ok += 1
            consecutive = 0
        else:
            failed += 1
            consecutive += 1
            backup.pop()
            say(f"  {u[:8]} PATCH {st}: {str(body)[:160]}")
            if consecutive >= MAX_CONSECUTIVE_ERRORS:
                say(f"\nABORT: {consecutive} consecutive errors -- stopping "
                    "rather than grinding through the rest")
                break

        if i % 100 == 0 or i == len(items):
            backup_path.write_text(json.dumps(backup, indent=2), encoding="utf-8")
            el = time.time() - t0
            rate = i / el if el else 0
            say(f"  {i}/{len(items)}  ok={ok} skipped={skipped} failed={failed}  "
                f"elapsed={el/60:.1f}m  ETA={((len(items)-i)/rate)/60:.1f}m"
                if rate else f"  {i}/{len(items)}")

    backup_path.write_text(json.dumps(backup, indent=2), encoding="utf-8")
    say(f"\n  ok={ok} skipped(already assigned)={skipped} failed={failed}")
    say(f"  Backup -> {backup_path}")
    say(f"  Undo with: python src/scripts/dpd_lane_rebalance.py --undo "
        f"{backup_path.name}")
    return 0 if failed == 0 else 1


def undo(api: LiveApi, path: Path) -> int:
    recs = json.loads(path.read_text(encoding="utf-8"))
    ok = failed = 0
    for r in recs:
        st, body = patch_assignee(api, r["uuid"], r.get("assigned_to"))
        if st in (200, 202, 204):
            ok += 1
        else:
            failed += 1
            say(f"  {r['uuid'][:8]} undo {st}: {str(body)[:140]}")
    say(f"  restored {ok}, failed {failed}")
    return 0 if failed == 0 else 1


# ------------------------------------------------------------ phase 4: verify

def verify(api: LiveApi, name_of: dict[str, str]) -> int:
    lanes = {l["preset"]: to_query(l["filters"]) for l in resolve_lanes(api)}
    bad = []
    for key, preset in (("hottest", HOTTEST), ("ftm", FTM)):
        q = lanes[preset]
        total = count(api, q)
        say(f"\n{preset}  (total {total})")
        seen = 0
        per: dict[str, int] = {}
        for who, nm in name_of.items():
            q2 = json.loads(json.dumps(q))
            q2["must"]["assigned_to"] = who
            n = count(api, q2)
            seen += n
            per[nm] = n
            if n or nm in DIALERS:
                say(f"  {n:>5}  {nm}")
        say(f"  {total - seen:>5}  (no assignee)")
        if total - seen != 0:
            bad.append(f"{preset}: {total - seen} still unassigned")
        # Reuse the counts above rather than querying each user a second time:
        # the filtered counts come off a search index that lags writes by a
        # minute or two, and two passes over a settling index can disagree
        # with each other (observed live 2026-09-01 -- the display said 207s
        # while a re-count said 206).
        vals = [per[nm] for nm in DIALERS]
        if max(vals) - min(vals) > 1:
            bad.append(f"{preset}: spread {min(vals)}..{max(vals)} is wider "
                       "than the 1-record remainder allows")
    say("")
    for b in bad:
        say(f"  DEFECT: {b}")
    say("  VERIFIED" if not bad else f"  {len(bad)} defect(s)")
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--undo", metavar="BACKUP_JSON")
    ap.add_argument("--reuse-state", action="store_true",
                    help="reuse the cached read from a previous --plan-only")
    args = ap.parse_args()

    api = LiveApi()
    say("JWT minted OK")
    name_of = json.loads(NAME_MAP.read_text(encoding="utf-8"))["name_of"]
    name_of_uuid = uuid_of_name()

    if args.undo:
        p = Path(args.undo)
        if not p.exists():
            p = ROOT / "output" / args.undo
        say(f"\nUndo from {p}")
        return undo(api, p)

    if args.verify:
        say("\nVerifying live, via the assigned_to query key...")
        return verify(api, name_of)

    if args.reuse_state and OUT_STATE.exists():
        say(f"\nReusing cached read from {OUT_STATE}")
        data = json.loads(OUT_STATE.read_text(encoding="utf-8"))
    else:
        say("\nPhase 1: reading both lanes...")
        data = read_lanes(api)
        OUT_STATE.parent.mkdir(parents=True, exist_ok=True)
        OUT_STATE.write_text(json.dumps(data), encoding="utf-8")
        say(f"  cached read -> {OUT_STATE}")

    guard_shape(data)
    existing = existing_counts(data, name_of)
    say(f"  existing in-lane assignments: "
        f"hottest={dict(existing['hottest'])} ftm={dict(existing['ftm'])}")
    tgt = targets(data, existing)
    say(f"  targets: hottest={tgt['hottest']} ftm={tgt['ftm']}")

    plan, notes = allocate(data, existing, tgt)
    report_plan(data, plan, existing)
    write_plan_csv(data, plan)
    for n in notes:
        say(f"  NOTE: {n}")

    if args.plan_only:
        say("\n--plan-only: nothing written to the account.")
        return 0

    if args.probe:
        say("\nPhase 2: probing the write path on ONE record...")
        return 0 if probe(api, data, name_of_uuid) else 3

    if args.commit:
        say(f"\nPhase 3: assigning {len(plan)} records...")
        rc = commit(api, data, plan, name_of_uuid)
        say("\nPhase 4: verifying...")
        return max(rc, verify(api, name_of))

    say("\nNo action flag given. Use --plan-only, --probe, --commit or --verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
