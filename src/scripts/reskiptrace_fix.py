"""Fix the workable records parked in the `Re-Skiptrace` status.

The audit (`reskiptrace_audit.py`, 2026-09-07) proved every one of the 111
records was ALREADY skip traced, so none of them needs what the status asks
for. This script executes the fix for the two big buckets:

  SCORE (104)  live numbers never Trestle-tiered -> score them (never double
               billing), tier-tag + tier-order the phones, entry-tag by source
               list (Basem's standing rule from the not_related batch), and
               move callable records to `No Answer` so they re-enter the lanes.
  RETAG (3)    already tiered -> same push path fixes dial order + status.

  DEEP PROSPECT (4) is NOT handled here: records whose every number is marked
  wrong/dead are research jobs (Ty's rule) and go through obituary_dp_batch.
  This script excludes any record with zero live (non-bad-status) numbers.

This is the proven `not_related_requalify.py` engine re-aimed at a different
status cohort -- same gates, same write surfaces, same traps honoured:
  * account-total guard on the status query (unrecognised key = silent no-op)
  * index lag: each record's own detail read is the truth, not the search index
  * phone writes are full-list `POST /owner/{uuid}/upsert-phones/`
  * property tag PATCH is merge-only; undo removal is `POST .../remove-tags/`
  * status writes take an ACTIVE title verbatim (`No Answer`)
  * phone tags fail to land on ~3.5% of first upserts: qa --repair is mandatory

Usage (in order; each step gates the next):
    python -u src/scripts/reskiptrace_fix.py pull                # free, read-only
    python -u src/scripts/reskiptrace_fix.py score --pay         # Gate 1: spends money
    python -u src/scripts/reskiptrace_fix.py push --probe        # one record + read-back
    python -u src/scripts/reskiptrace_fix.py push --commit       # Gate 2: bulk write
    python -u src/scripts/reskiptrace_fix.py qa [--repair]
    python -u src/scripts/reskiptrace_fix.py push --undo <backup.json>
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from dotenv import dotenv_values  # noqa: E402

from dpd_lane_assignee_report import count, list_uuids  # noqa: E402
from dpd_lane_phone_score import (  # noqa: E402
    CALLABLE_MAX_RANK,
    MAIL_ONLY,
    OWNER_PHONE_CAP,
    SCORE_TAGS,
    SKIP_PREFIX,
    Api,
    eval_tag,
    jload,
    jsave,
    load_seeds,
    rank_of,
    say,
    score_tag_of,
    tag_names,
)
from not_related_requalify import (  # noqa: E402
    BAD_STATUS_RANK,
    bad_phone_status,
    entry_tags_for,
)
from phone_validator import (  # noqa: E402
    COST_PER_PHONE,
    DEFAULT_TIERS,
    clean_phone,
    process_phones,
)
from reskiptrace_audit import status_key  # noqa: E402

RUN = ROOT / "output" / "reskiptrace_fix"
RECORDS = RUN / "records.json"
PULL_PLAN = RUN / "pull_plan.json"
RUN_CACHE = RUN / "trestle_cache.json"
SCORE_ERRORS = RUN / "score_errors.json"
PUSH_PLAN = RUN / "push_plan.json"
PUSH_LOG = RUN / "push_log.json"
PROBE_OK = RUN / "probe_ok.json"

STATUS = "Re-Skiptrace"
# Same target as the not_related batch (Basem's pick): the generic active-dialer
# status, an is_active title verbatim, so callable records re-enter the lanes.
REQUALIFY_STATUS = "No Answer"

# Prior paid Trestle results, reused for free.
FREE_CACHES = [
    ROOT / "output" / "lane_phone_score" / "trestle_cache.json",
    ROOT / "output" / "not_related_requalify" / "trestle_cache.json",
]


def load_free_caches() -> dict:
    merged: dict = {}
    for path in FREE_CACHES:
        c = jload(path, {})
        n = 0
        for num, e in c.items():
            nn = clean_phone(num)
            if nn and nn not in merged:
                merged[nn] = e
                n += 1
        if n:
            say(f"  free cache {path.parent.name}: {n} numbers")
    return merged


def resolve_tag(n: str, plan_numbers: dict, run_cache: dict,
                free: dict, seeds: dict) -> str:
    e = plan_numbers.get(n) or {}
    if e.get("class") == "account_tag":
        return e["tag"]
    if n in run_cache:
        return run_cache[n].get("tag") or eval_tag(run_cache[n])
    if n in free:
        return free[n].get("tag") or eval_tag(free[n])
    if e.get("tag"):
        return e["tag"]
    if n in seeds:
        return seeds[n]["tag"]
    return "Unknown"


# ───────────────────────── pull ─────────────────────────

def cmd_pull(a) -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    api = Api()
    say("JWT minted OK")

    # THE GUARD: an unrecognised filter key is silently ignored and the count
    # comes back as the account total looking like a real answer.
    query = {"must": {"any_property_status": [STATUS]}}
    total = count(api, {})
    n = count(api, query)
    say(f"account total (guard): {total}")
    say(f"status {STATUS!r}: {n}")
    if n == total:
        say("FAIL: cohort count equals the unfiltered account total. Stopping.")
        return 3
    if n == 0:
        say("WARNING: zero records in the status. The 2026-09-07 audit read 111 "
            "-- open the Records UI before trusting this.")
        return 2

    uuids = list_uuids(api, query, n)
    if len(uuids) != n:
        say(f"  NOTE: paged {len(uuids)} uuids but count said {n}")

    records: dict = {} if a.fresh else jload(RECORDS, {})
    todo = [u for u in uuids if u not in records]
    say(f"hydrating {len(todo)} records ({len(records)} already cached)...")
    t0 = time.time()
    gone = []
    for i, u in enumerate(todo, 1):
        st, body = api.get(f"/api/internal/property/{u}/")
        if st == 404:
            gone.append(u)
            continue
        if st != 200:
            say(f"  detail {st} on {u}; skipping this pull")
            continue
        addr = body.get("address") or {}
        owner = body.get("owner") or {}
        records[u] = {
            "street": addr.get("street"), "city": addr.get("city"),
            "state": addr.get("state"), "zip": addr.get("postal_code"),
            "county": addr.get("county"),
            "status": body.get("status"),
            "lists": tag_names(body.get("lists")),
            "prop_tags": tag_names(body.get("tags")),
            "owner_uuid": owner.get("uuid"),
            "owner_name": " ".join(x for x in [owner.get("first_name"),
                                               owner.get("last_name")] if x).strip()
                          or (owner.get("company") or ""),
            "phones": [{"number": p.get("number"),
                        "cleaned": clean_phone(str(p.get("number") or "")),
                        "type": p.get("type"), "status": p.get("status"),
                        "is_connected": p.get("is_connected"),
                        "tags": tag_names(p.get("tags"))}
                       for p in (owner.get("phones") or []) if isinstance(p, dict)],
        }
        if i % 50 == 0 or i == len(todo):
            jsave(RECORDS, records)
            say(f"    {i}/{len(todo)}  elapsed={(time.time()-t0)/60:.1f}m")
    records = {u: r for u, r in records.items() if u in set(uuids)}
    jsave(RECORDS, records)
    if gone:
        say(f"  {len(gone)} record(s) 404'd (deleted): {gone[:5]}")

    # Index lag: trust each record's own detail read.
    moved = {u for u, r in records.items()
             if status_key(r.get("status")) != status_key(STATUS)}
    if moved:
        say(f"  {len(moved)} record(s) no longer hold the status -- excluded")
        records = {u: r for u, r in records.items() if u not in moved}
        jsave(RECORDS, records)

    # DP exclusion: a record with zero LIVE numbers (none, or every one carries
    # a bad CRM status) is a research job, not a scoring job -- it goes through
    # obituary_dp_batch, not this push.
    dp_out = {}
    for u, r in records.items():
        live_nums = [p for p in r["phones"]
                     if p["cleaned"] and not bad_phone_status(p["status"])]
        if not live_nums:
            dp_out[u] = f"{r['street']}, {r['city']} [{r['owner_name']}]"
    workable = {u: r for u, r in records.items() if u not in dp_out}
    say(f"  DP-excluded (no live numbers, research jobs): {len(dp_out)}")
    for u, s in dp_out.items():
        say(f"    {s}")

    # ── entry-tag split (informational; the push applies it) ──
    tag_split: dict[str, int] = {}
    for r in workable.values():
        for t in entry_tags_for(r["lists"], r["prop_tags"]) or ["(already tagged)"]:
            tag_split[t] = tag_split.get(t, 0) + 1

    # ── phone classification (the never-double-bill rule) ──
    say("classifying every number...")
    seeds = load_seeds()
    free = load_free_caches()
    run_cache = jload(RUN_CACHE, {})

    account_tag: dict[str, str] = {}
    entries = 0
    status_all_bad: dict[str, bool] = {}
    for r in workable.values():
        for p in r["phones"]:
            nn = p["cleaned"]
            if not nn:
                continue
            entries += 1
            status_all_bad[nn] = status_all_bad.get(nn, True) and bad_phone_status(p["status"])
            t = score_tag_of(p["tags"])
            if t and (nn not in account_tag or rank_of(t) < rank_of(account_tag[nn])):
                account_tag[nn] = t

    numbers: dict[str, dict] = {}
    for r in workable.values():
        for p in r["phones"]:
            nn = p["cleaned"]
            if not nn or nn in numbers:
                continue
            if nn in account_tag:
                numbers[nn] = {"class": "account_tag", "tag": account_tag[nn]}
            elif nn in run_cache:
                e = run_cache[nn]
                numbers[nn] = {"class": "run_cache", "tag": e.get("tag") or eval_tag(e)}
            elif nn in free:
                e = free[nn]
                numbers[nn] = {"class": "free_cache", "tag": e.get("tag") or eval_tag(e)}
            elif nn in seeds:
                numbers[nn] = {"class": "seed_cache", "tag": seeds[nn]["tag"],
                               "source": seeds[nn]["source"]}
            elif status_all_bad.get(nn):
                numbers[nn] = {"class": "bad_status"}   # never pay; ranks with Drop
            else:
                numbers[nn] = {"class": "to_pay"}

    to_pay = sorted(nn for nn, e in numbers.items() if e["class"] == "to_pay")
    by_class: dict[str, int] = {}
    for e in numbers.values():
        by_class[e["class"]] = by_class.get(e["class"], 0) + 1

    jsave(PULL_PLAN, {"ran_at": datetime.now().isoformat(timespec="seconds"),
                      "status": STATUS, "cohort": len(records),
                      "dp_excluded": dp_out,
                      "entry_tag_split": tag_split,
                      "phone_entries": entries, "unique_numbers": len(numbers),
                      "by_class": by_class, "numbers": numbers, "to_pay": to_pay,
                      "gone": gone, "moved": sorted(moved)})

    say("")
    say("=" * 64)
    say("GATE 1 REPORT -- nothing spent, nothing written")
    say(f"  cohort ({STATUS!r}): {len(records)} records; workable: {len(workable)}")
    say(f"  entry-tag plan: {tag_split}")
    say(f"  phone entries: {entries}   unique numbers: {len(numbers)}")
    for cls in ("account_tag", "run_cache", "free_cache", "seed_cache", "bad_status"):
        if by_class.get(cls):
            say(f"  {cls:>12}: {by_class[cls]}  ($0)")
    say(f"  TO PAY:       {len(to_pay)}  = ${len(to_pay) * COST_PER_PHONE:.2f}"
        f" (+ litigator add-on)")
    say(f"  next: score --pay   (writes {RUN_CACHE.name})")
    say("=" * 64)
    return 0


# ───────────────────────── score ─────────────────────────

def cmd_score(a) -> int:
    plan = jload(PULL_PLAN, None)
    if not plan:
        say("no pull_plan.json -- run `pull` first")
        return 2
    if not a.pay:
        say("Gate 1: refusing to spend without --pay.")
        return 2

    env = dotenv_values(str(ROOT / ".env"))
    key = env.get("TRESTLE_PAID_API_KEY") or env.get("TRESTLE_API_KEY") or ""
    if not key:
        say("TRESTLE_PAID_API_KEY / TRESTLE_API_KEY not set in .env")
        return 2

    seeds = load_seeds()
    free = load_free_caches()
    cache = jload(RUN_CACHE, {})
    # Re-check every cache right here: a checkpoint restart must never re-bill.
    todo = [n for n in plan["to_pay"]
            if n not in cache and n not in seeds and n not in free]
    if a.limit:
        todo = todo[: a.limit]
    say(f"to score: {len(todo)} of {len(plan['to_pay'])} planned")
    say(f"cost: ${len(todo) * COST_PER_PHONE:.2f} at ${COST_PER_PHONE:.3f}/number")
    if not todo:
        say("nothing to pay for")
        return 0

    errors_all = jload(SCORE_ERRORS, [])
    paid = 0
    CHUNK = 200
    for start in range(0, len(todo), CHUNK):
        chunk = [n for n in todo[start:start + CHUNK] if n not in cache]
        if not chunk:
            continue
        results, errors = process_phones([(n, n) for n in chunk], key,
                                         tiers=DEFAULT_TIERS,
                                         add_litigator=not a.no_litigator)
        for r in results:
            nn = clean_phone(r["phone_number"])
            cache[nn] = {"score": r.get("activity_score"), "line_type": r.get("line_type"),
                         "is_valid": r.get("is_valid"),
                         "litigator": r.get("is_litigator_risk"),
                         "carrier": r.get("carrier"), "is_prepaid": r.get("is_prepaid"),
                         "tag": r.get("assigned_tag"), "keep": r.get("keep"),
                         "scored_at": datetime.now().isoformat(timespec="seconds")}
            paid += 1
        errors_all.extend(errors)
        jsave(RUN_CACHE, cache)          # checkpoint: a kill here re-bills nothing
        jsave(SCORE_ERRORS, errors_all)
        say(f"  {min(start + CHUNK, len(todo))}/{len(todo)}  (paid: {paid}, "
            f"errors: {len(errors)})")

    dist: dict = {}
    for nn in todo:
        t = (cache.get(nn) or {}).get("tag") or "ERROR"
        dist[t] = dist.get(t, 0) + 1
    say(f"\nscored {paid} numbers  (~${paid * COST_PER_PHONE:.2f} base)")
    for t, c in sorted(dist.items(), key=lambda kv: -kv[1]):
        say(f"  {t:>16}: {c}")
    if errors_all:
        say(f"errors: {len(errors_all)} (NOT cached; a re-run retries them)")
    say("next: push --probe")
    return 0


# ───────────────────────── push ─────────────────────────

def build_push_plan(records: dict, plan: dict, cache: dict,
                    free: dict, seeds: dict) -> dict:
    """Per workable record: tier-tagged tier-sorted phones, entry tags, Mail
    Only, and whether the status clears. Only records needing a write kept."""
    dp_excluded = set(plan.get("dp_excluded") or {})
    plan_numbers = plan["numbers"]
    out = {}
    for u, r in records.items():
        if u in dp_excluded:
            continue
        phones = [p for p in r["phones"] if p["cleaned"]]
        planned, ranks, seen = [], [], set()
        for idx, p in enumerate(phones):
            nn = p["cleaned"]
            if nn in seen:
                continue
            seen.add(nn)
            tag = resolve_tag(nn, plan_numbers, cache, free, seeds)
            # A phone whose CRM status marks it bad ranks with Drop no matter
            # what the tier says: it was dialed and disproven on THIS record.
            rk = BAD_STATUS_RANK if bad_phone_status(p["status"]) else rank_of(tag)
            keep_tags = [t for t in p["tags"]
                         if t not in SCORE_TAGS and not t.startswith(SKIP_PREFIX)]
            new_tags = keep_tags + ([tag] if tag != "Unknown" else [])
            planned.append({"idx": idx, "rank": rk, "tag": tag,
                            "number": p["number"], "cleaned": nn,
                            "type": p["type"], "status": p["status"],
                            "is_connected": p["is_connected"], "tags": new_tags,
                            "old_tags": p["tags"]})
            ranks.append(rk)
        planned.sort(key=lambda x: (x["rank"], x["idx"]))
        if len(planned) > OWNER_PHONE_CAP:
            planned = planned[:OWNER_PHONE_CAP]

        callable_ = bool(ranks) and min(ranks) <= CALLABLE_MAX_RANK
        mail_only = bool(ranks) and not callable_
        entry_add = entry_tags_for(r["lists"], r["prop_tags"])
        tags_after = list(r["prop_tags"]) + entry_add
        if mail_only and MAIL_ONLY not in tags_after:
            tags_after.append(MAIL_ONLY)
        clear_status = callable_ and status_key(r.get("status")) == status_key(STATUS)

        phones_changed = planned and (
            [p["cleaned"] for p in planned]
            != list(dict.fromkeys(p["cleaned"] for p in phones))
            or any(sorted(p["tags"]) != sorted(p["old_tags"]) for p in planned))
        if not phones_changed and tags_after == r["prop_tags"] and not clear_status:
            continue
        out[u] = {"owner_uuid": r["owner_uuid"], "owner_name": r["owner_name"],
                  "street": r["street"], "zip": r["zip"],
                  "phones": [{k: p[k] for k in
                              ("number", "cleaned", "type", "status",
                               "is_connected", "tags")}
                             for p in planned] if phones_changed else [],
                  "mail_only": mail_only,
                  "clear_status": clear_status,
                  "tags_add": [t for t in tags_after if t not in r["prop_tags"]],
                  "best_tag": min((p["tag"] for p in planned), key=rank_of)
                              if planned else None}
    return out


def push_one(api: Api, uuid: str, p: dict) -> dict:
    log = {"uuid": uuid, "street": p["street"], "steps": {},
           "at": datetime.now().isoformat(timespec="seconds")}
    if p["phones"] and p.get("owner_uuid"):
        payload = [{"number": ph["number"], "type": ph["type"], "tags": ph["tags"],
                    "status": ph["status"], "is_connected": ph["is_connected"],
                    "verified": False} for ph in p["phones"]]
        st, resp = api.write("POST",
                             f"/api/internal/owner/{p['owner_uuid']}/upsert-phones/",
                             {"phones": payload})
        log["steps"]["phones"] = {"status": st, "sent": len(payload),
                                  "resp": str(resp)[:150]}
        if st not in (200, 201, 204):
            log["error"] = f"upsert-phones {st}"
            return log
    if p.get("tags_add") or p.get("clear_status"):
        # Full-list PATCH built on a FRESH read: the plan can be minutes old
        # and a sequence may have added tags since.
        st0, full = api.get(f"/api/internal/property/{uuid}/")
        current = tag_names((full or {}).get("tags")) if st0 == 200 else []
        merged = current + [t for t in (p.get("tags_add") or []) if t not in current]
        body: dict = {"tags": merged}
        if p.get("clear_status"):
            body["status"] = REQUALIFY_STATUS
        st, resp = api.write("PATCH", f"/api/internal/property/{uuid}/", body)
        log["steps"]["prop"] = {"status": st,
                                "added": [t for t in (p.get("tags_add") or [])
                                          if t not in current],
                                "cleared_status": p.get("clear_status", False),
                                "resp": str(resp)[:150]}
        if st not in (200, 201, 204):
            log["error"] = f"property PATCH {st}"
    return log


def verify_one(api: Api, uuid: str, p: dict) -> list[str]:
    """Read the record back and list every deviation from the plan."""
    bad = []
    st, full = api.get(f"/api/internal/property/{uuid}/")
    if st != 200:
        return [f"detail read {st}"]
    if p["phones"]:
        live = [ph for ph in ((full.get("owner") or {}).get("phones") or [])
                if isinstance(ph, dict)]
        live_by_num, live_seq = {}, []
        for ph in live:
            nn = clean_phone(str(ph.get("number") or ""))
            if nn and nn not in live_by_num:
                live_by_num[nn] = tag_names(ph.get("tags"))
                live_seq.append(nn)
        want_seq = [ph["cleaned"] for ph in p["phones"]]
        for ph in p["phones"]:
            got = live_by_num.get(ph["cleaned"])
            if got is None:
                bad.append(f"phone {ph['cleaned']} missing")
            elif sorted(got) != sorted(ph["tags"]):
                bad.append(f"phone {ph['cleaned']} tags {got} != {ph['tags']}")
        if live_seq[: len(want_seq)] != want_seq:
            bad.append(f"order {live_seq[:5]}... != planned {want_seq[:5]}...")
    live_tags = tag_names(full.get("tags"))
    for t in p.get("tags_add") or []:
        if t not in live_tags:
            bad.append(f"property tag {t!r} missing")
    live_status = full.get("status")
    if p.get("clear_status"):
        if live_status != REQUALIFY_STATUS:
            bad.append(f"status {live_status!r} != planned {REQUALIFY_STATUS!r}")
    elif status_key(live_status) not in (status_key(STATUS), ""):
        bad.append(f"status unexpectedly {live_status!r}")
    return bad


def cmd_push(a) -> int:
    if a.undo:
        return do_undo(a.undo)
    records = jload(RECORDS, {})
    plan = jload(PULL_PLAN, None)
    if not records or not plan:
        say("run `pull` first")
        return 2
    seeds = load_seeds()
    free = load_free_caches()
    cache = jload(RUN_CACHE, {})
    push_plan = build_push_plan(records, plan, cache, free, seeds)
    mail_only_n = sum(1 for p in push_plan.values() if p["mail_only"])
    clear_n = sum(1 for p in push_plan.values() if p["clear_status"])
    say(f"push plan: {len(push_plan)} records need writes "
        f"({clear_n} status clears, {mail_only_n} get {MAIL_ONLY!r}, "
        f"{len(plan.get('dp_excluded') or {})} DP-excluded)")
    jsave(PUSH_PLAN, push_plan)
    if not push_plan:
        return 0

    if a.commit and not a.probe and not PROBE_OK.exists() and not a.force:
        say("Gate 2: no successful probe on record. Run `push --probe` first.")
        return 2

    api = Api()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = RUN / f"push_backup_{stamp}.json"
    push_log = jload(PUSH_LOG, {})

    targets = list(push_plan.items())
    if a.probe:
        if a.probe == "first":
            pick = next((u for u, p in targets if p["clear_status"]), targets[0][0])
        else:
            pick = a.probe
        if pick not in push_plan:
            say(f"--probe {pick}: not in the push plan")
            return 2
        targets = [(pick, push_plan[pick])]
    elif a.limit:
        targets = targets[: a.limit]

    if not (a.probe or a.commit):
        say("dry run (no --probe / --commit): plan written, nothing sent")
        for u, p in targets[:10]:
            say(f"  {u}  {p['street']}  phones={len(p['phones'])} "
                f"best={p['best_tag']} +tags={p['tags_add']} "
                f"clear_status={p['clear_status']} mail_only={p['mail_only']}")
        return 0

    backup = {}
    done = errors = 0
    t0 = time.time()
    for i, (u, p) in enumerate(targets, 1):
        if u in push_log and not push_log[u].get("error") and not a.probe:
            continue  # resumable: already pushed CLEAN in a prior run
        backup[u] = {"owner_uuid": records[u]["owner_uuid"],
                     "phones": records[u]["phones"],
                     "prop_tags": records[u]["prop_tags"],
                     "status": records[u]["status"],
                     "tags_add": p.get("tags_add") or []}
        jsave(backup_path, backup)      # backup lands BEFORE the write, always
        log = push_one(api, u, p)
        push_log[u] = log
        if log.get("error"):
            errors += 1
            say(f"  ERROR {u} {p['street']}: {log['error']}")
        else:
            done += 1
        if i % 50 == 0 or i == len(targets):
            jsave(PUSH_LOG, push_log)
            say(f"  {i}/{len(targets)}  written={done} errors={errors} "
                f"elapsed={(time.time()-t0)/60:.1f}m")
    jsave(PUSH_LOG, push_log)

    if a.probe:
        u, p = targets[0]
        bad = verify_one(api, u, p)
        say("")
        say(f"PROBE {u}  {p['street']}  owner {p['owner_name']!r}")
        for ph in p["phones"]:
            say(f"    {ph['cleaned']}  tags={ph['tags']}")
        say(f"    +tags={p['tags_add']}  set_status={p['clear_status']} "
            f"(-> {REQUALIFY_STATUS!r})  mail_only={p['mail_only']}")
        if bad:
            say(f"  READ-BACK FAILED: {bad}")
            return 3
        jsave(PROBE_OK, {"uuid": u,
                         "at": datetime.now().isoformat(timespec="seconds")})
        say("  read-back CLEAN. Gate 2 open: `push --commit` will do the rest")
        return 0

    say(f"\npushed {done}, errors {errors}. backup: {backup_path.name}")
    say("next: qa   (then qa --repair for whatever the first upsert dropped)")
    return 0 if not errors else 1


def do_undo(backup_file: str) -> int:
    backup = jload(Path(backup_file), None)
    if not backup:
        say(f"backup not found/empty: {backup_file}")
        return 2
    api = Api()
    say(f"undoing {len(backup)} records from {backup_file}")
    bad = 0
    for u, b in backup.items():
        payload = [{"number": p["number"], "type": p["type"], "tags": p["tags"],
                    "status": p["status"], "is_connected": p["is_connected"],
                    "verified": False} for p in b["phones"] if p.get("number")]
        if payload:
            st, _ = api.write("POST",
                              f"/api/internal/owner/{b['owner_uuid']}/upsert-phones/",
                              {"phones": payload})
            if st not in (200, 201, 204):
                say(f"  {u}: phones restore {st}")
                bad += 1
        st, _ = api.write("PATCH", f"/api/internal/property/{u}/",
                          {"tags": b["prop_tags"], "status": b["status"]})
        if st not in (200, 201, 204):
            say(f"  {u}: tags/status restore {st}")
            bad += 1
        added = [t for t in (b.get("tags_add") or []) if t not in b["prop_tags"]]
        if added:
            st, _ = api.write("POST", f"/api/internal/property/{u}/remove-tags/",
                              {"tags": added})
            if st not in (200, 201, 204):
                say(f"  {u}: remove-tags {st}")
                bad += 1
    say(f"undo done, {bad} failures")
    return 0 if not bad else 1


# ───────────────────────── qa ─────────────────────────

def cmd_qa(a) -> int:
    push_plan = jload(PUSH_PLAN, {})
    push_log = jload(PUSH_LOG, {})
    if not push_plan:
        say("no push_plan.json -- nothing to audit")
        return 2
    pushed = {u: p for u, p in push_plan.items() if u in push_log}
    say(f"auditing {len(pushed)} pushed records against the LIVE account...")
    api = Api()
    failures = {}
    for i, (u, p) in enumerate(pushed.items(), 1):
        bad = verify_one(api, u, p)
        if bad:
            failures[u] = bad
        if i % 50 == 0 or i == len(pushed):
            say(f"  {i}/{len(pushed)}  defects so far: {len(failures)}")
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = RUN / f"qa_{'repair' if a.repair else 'audit'}_{stamp}.json"
    jsave(out, failures)
    say(f"clean: {len(pushed) - len(failures)}/{len(pushed)}   report: {out.name}")
    if not failures:
        return 0
    if not a.repair:
        say("re-run with --repair (the ~3.5% first-upsert tag drop is expected)")
        return 1
    say(f"repairing {len(failures)} records...")
    still = {}
    for u in failures:
        push_one(api, u, push_plan[u])
        bad = verify_one(api, u, push_plan[u])
        if bad:
            still[u] = bad
            say(f"  STILL BAD {u}: {bad[:2]}")
    say(f"after repair: {len(failures) - len(still)} fixed, {len(still)} still bad")
    return 0 if not still else 1


# ───────────────────────── main ─────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pull", help="query the status, hydrate, classify (free)")
    p.add_argument("--fresh", action="store_true")

    s = sub.add_parser("score", help="pay Trestle for the unscored numbers")
    s.add_argument("--pay", action="store_true", help="Gate 1: actually spend")
    s.add_argument("--no-litigator", action="store_true")
    s.add_argument("--limit", type=int, default=0)

    w = sub.add_parser("push", help="write tier tags + entry tags + status clears")
    w.add_argument("--probe", nargs="?", const="first", default=None, metavar="UUID")
    w.add_argument("--commit", action="store_true", help="Gate 2: bulk write")
    w.add_argument("--force", action="store_true")
    w.add_argument("--limit", type=int, default=0)
    w.add_argument("--undo", default=None, metavar="BACKUP_JSON")

    q = sub.add_parser("qa", help="read every pushed record back")
    q.add_argument("--repair", action="store_true")

    a = ap.parse_args()
    RUN.mkdir(parents=True, exist_ok=True)
    return {"pull": cmd_pull, "score": cmd_score,
            "push": cmd_push, "qa": cmd_qa}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
