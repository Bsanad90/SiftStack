"""Trestle-score the phones on the two ready-to-call lanes, tag + reorder them.

The lanes ("Hottest - 02 Ready to Call", "FTM - 02 Ready to Call") were fully
assigned across the 5 dialers on 2026-09-01, but every phone on them is
unscored: the preset has no tier condition and zero lane records carry a
`Dial *` tag. This script scores each number with TrestleIQ, writes the tier
tag onto the phone (preserving the import-written `Rel{N}.{M}` / `Pr.*` tags),
re-sends each owner's list tier-sorted so the best number dials first, and
stamps `Mail Only` on records whose every number is un-callable -- which the
CALL presets already exclude by uuid.

THE RULE THAT SHAPES EVERYTHING: never double-bill a number (Basem, 2026-09-01).
A number reaches Trestle only if it carries no score tag anywhere on the lane
AND misses every local cache (the obituary/skipped-batch caches plus the 5,502
probate run results) AND is unique in this run. `score` re-checks the run cache
before every chunk, so a checkpoint restart cannot re-pay either.

Facts this build relies on (each verified live before being relied on):
  * Phones live under `owner.phones[]` ONLY. Secondary owners in the detail
    read carry names but no uuid and no phones, so the owner is the single
    writable contact per record.
  * Phone writes: full-list `POST /api/internal/owner/{uuid}/upsert-phones/`
    (partial sends can never CORRECT a tag). `PATCH /owner/ {"phones":...}`
    replaces wholesale and is never used. `remove-phones` is not needed here:
    we retag/reorder the same list, so the 30-cap cannot be newly exceeded.
  * Property tags: read-modify-write `PATCH /api/internal/property/{uuid}/`
    with the FULL tag list. `tags_add` is silently ignored and
    `POST .../add-tags/` applies ACCOUNT-WIDE -- both are banned.
  * Phone tags fail to land on ~3.5% of first upserts (obituary batch,
    measured), so the qa --repair pass is not optional.
  * A filter key the query API does not recognise is silently ignored and the
    count comes back as the account total -- so the account total is taken
    first and any lane count equal to it aborts the run.

Usage (in order; each step gates the next):
    python src/scripts/dpd_lane_phone_score.py pull              # free, read-only
    python src/scripts/dpd_lane_phone_score.py score --pay       # Gate 1: spends money
    python src/scripts/dpd_lane_phone_score.py push --probe      # one record + read-back
    python src/scripts/dpd_lane_phone_score.py push --commit     # Gate 2: bulk write
    python src/scripts/dpd_lane_phone_score.py qa [--repair]
    python src/scripts/dpd_lane_phone_score.py push --undo <backup.json>
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dotenv import dotenv_values  # noqa: E402

from dpd_lane_assignee_report import count, list_uuids, resolve_lanes, to_query  # noqa: E402
from live_pull import LiveApi as _LiveApi  # noqa: E402
from phone_validator import (  # noqa: E402
    COST_PER_PHONE,
    DEFAULT_TIERS,
    SKIP_LINE_TYPES,
    assign_tier,
    clean_phone,
    process_phones,
)

RUN = ROOT / "output" / "lane_phone_score"
RECORDS = RUN / "records.json"
PULL_PLAN = RUN / "pull_plan.json"
RUN_CACHE = RUN / "trestle_cache.json"
SCORE_ERRORS = RUN / "score_errors.json"
PUSH_PLAN = RUN / "push_plan.json"
PUSH_LOG = RUN / "push_log.json"
PROBE_OK = RUN / "probe_ok.json"

# Already-paid results reused for free. The hottest-needs-skipped cache is the
# big one (11,649 numbers) and likely overlaps hard: needs-skipped records
# graduate into ready-to-call once the skip trace lands.
SEED_CACHES = [
    ROOT / "output" / "dp_hottest_needs_skipped" / "trestle_cache.json",
    ROOT / "output" / "dp_skipped_batch" / "trestle_cache.json",
]
SEED_CSVS = [
    ROOT / "output" / "probate_merge_20260901T085023" / "trestle" / "validation_results.csv",
]

DIAL_TIERS = ["Dial First", "Dial Second", "Dial Third", "Dial Fourth"]
# Any of these on a phone means it has already been scored: never pay again.
SCORE_TAGS = set(DIAL_TIERS) | {"Drop", "Litigator Risk", "Invalid"}
SKIP_PREFIX = "Skip - "
MAIL_ONLY = "Mail Only"

# Dial order. Unknown (scored-but-unscorable or score errored) sits above Drop:
# a known-bad number ranks below one we merely could not price.
RANK = {"Dial First": 0, "Dial Second": 1, "Dial Third": 2, "Dial Fourth": 3,
        "Unknown": 4, "Drop": 5, "Invalid": 7, "Litigator Risk": 8}
SKIP_RANK = 6
CALLABLE_MAX_RANK = 4   # rank <= this keeps the record OFF the Mail Only list
OWNER_PHONE_CAP = 30


def say(msg: str) -> None:
    print(msg, flush=True)


def jload(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def jsave(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


class Api(_LiveApi):
    """live_pull.LiveApi + retrying mint + a write verb.

    The base `_mint` is one unguarded urlopen and `_call` re-mints every 30
    minutes; a transient 502 at the re-mint killed a lane-rebalance commit at
    step zero (observed live 2026-09-01), hence the retry.
    """

    def _mint(self):
        for attempt in range(5):
            try:
                return super()._mint()
            except Exception as exc:  # noqa: BLE001
                if attempt == 4:
                    raise
                wait = 5 * (2 ** attempt)
                say(f"  token mint failed ({str(exc)[:80]}); retrying in {wait}s")
                time.sleep(wait)

    def write(self, method: str, path: str, body, tries: int = 4):
        return self._call(path, method, body, None, tries)


def tag_names(tags) -> list[str]:
    """Phone/property tags come back as strings here, but obituary_dp_qa saw
    dicts on other endpoints -- accept both."""
    out = []
    for t in tags or []:
        out.append(t.get("name") if isinstance(t, dict) else str(t))
    return [t for t in out if t]


def score_tag_of(tags: list[str]) -> str:
    """The scoring outcome already on this phone, or ''. Tier tags win over the
    disqualification tags so a phone carrying both reads as its tier."""
    for t in DIAL_TIERS + ["Drop"]:
        if t in tags:
            return t
    for t in tags:
        if t in ("Litigator Risk", "Invalid") or t.startswith(SKIP_PREFIX):
            return t
    return ""


def rank_of(tag: str) -> int:
    if tag.startswith(SKIP_PREFIX):
        return SKIP_RANK
    return RANK.get(tag, RANK["Unknown"])


def eval_tag(entry: dict) -> str:
    """Mirror phone_validator.evaluate_phone over a cached/normalised entry:
    litigator -> invalid -> skip line type -> activity-score tier."""
    if entry.get("litigator") is True:
        return "Litigator Risk"
    if entry.get("is_valid") is not True:
        return "Invalid"
    lt = (entry.get("line_type") or "").strip()
    if lt.lower() in SKIP_LINE_TYPES:
        return f"{SKIP_PREFIX}{lt}"
    return assign_tier(entry.get("score"), DEFAULT_TIERS)


def load_seeds() -> dict[str, dict]:
    """number -> normalised {score, line_type, is_valid, litigator, tag, source}.
    First source to name a number wins (the caches don't disagree on shape)."""
    seeds: dict[str, dict] = {}
    for path in SEED_CACHES:
        if not path.exists():
            continue
        raw = jload(path, {})
        added = 0
        for num, e in raw.items():
            n = clean_phone(num)
            if not n or n in seeds:
                continue
            entry = {"score": e.get("activity_score"), "line_type": e.get("line_type"),
                     "is_valid": e.get("is_valid"), "litigator": e.get("litigator"),
                     "source": path.parent.name}
            entry["tag"] = eval_tag(entry)
            seeds[n] = entry
            added += 1
        say(f"  seed cache {path.parent.name}: {added} numbers")
    for path in SEED_CSVS:
        if not path.exists():
            continue
        added = 0
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                n = clean_phone(row.get("phone_number") or "")
                if not n or n in seeds:
                    continue
                score = row.get("activity_score")
                lit = (row.get("is_litigator_risk") or "").strip().lower()
                entry = {"score": int(score) if score not in (None, "", "None") else None,
                         "line_type": row.get("line_type") or "",
                         "is_valid": (row.get("is_valid") or "").strip().lower() == "true",
                         "litigator": True if lit == "true" else (False if lit == "false" else None),
                         "source": path.parent.parent.name}
                entry["tag"] = eval_tag(entry)
                seeds[n] = entry
                added += 1
        say(f"  seed csv {path.parent.parent.name}: {added} numbers")
    return seeds


# ───────────────────────── pull ─────────────────────────

def cmd_pull(a) -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    api = Api()
    say("JWT minted OK")

    say("Phase 1: resolving the two lane presets in the server store...")
    lanes = resolve_lanes(api)

    # THE GUARD: an unrecognised filter key is silently ignored and the count
    # comes back as the account total, looking like a real answer.
    total = count(api, {})
    say(f"  account total (guard): {total}")
    lane_uuids: dict[str, set] = {}
    for ln in lanes:
        q = to_query(ln["filters"])
        c = count(api, q)
        say(f"  {ln['preset']}: {c}")
        if c == total:
            say("FAIL: lane count equals the unfiltered account total -- the "
                "filter was ignored, not applied. Stopping.")
            return 3
        lane_uuids[ln["preset"]] = set(list_uuids(api, q, c))

    hottest = lane_uuids[lanes[0]["preset"]]
    ftm = lane_uuids[lanes[1]["preset"]]
    ordered = list(dict.fromkeys(list(hottest) + [u for u in ftm if u not in hottest]))
    say(f"  union {len(ordered)}  (hottest {len(hottest)}, ftm {len(ftm)}, "
        f"both {len(hottest & ftm)})")

    # Hydrate -- one detail GET per record; phones live nowhere else.
    # Checkpointed so an interrupted pull resumes instead of re-reading.
    records: dict = {} if a.fresh else jload(RECORDS, {})
    todo = [u for u in ordered if u not in records]
    say(f"Phase 2: hydrating {len(todo)} records ({len(records)} already cached)...")
    t0 = time.time()
    gone = []
    for i, u in enumerate(todo, 1):
        status, body = api.get(f"/api/internal/property/{u}/")
        if status == 404:
            gone.append(u)          # real deletions happen mid-run (seen live)
            continue
        if status != 200:
            say(f"  detail {status} on {u}; skipping this pull")
            continue
        addr = body.get("address") or {}
        owner = body.get("owner") or {}
        records[u] = {
            "street": addr.get("street"), "city": addr.get("city"),
            "zip": addr.get("postal_code"), "county": addr.get("county"),
            "assigned_to": body.get("assigned_to"),
            "prop_tags": tag_names(body.get("tags")),
            "owner_uuid": owner.get("uuid"),
            "owner_name": " ".join(x for x in [owner.get("first_name"),
                                               owner.get("last_name")] if x).strip()
                          or (owner.get("company") or ""),
            "phones": [{"number": p.get("number"), "cleaned": clean_phone(str(p.get("number") or "")),
                        "type": p.get("type"), "status": p.get("status"),
                        "is_connected": p.get("is_connected"),
                        "tags": tag_names(p.get("tags"))}
                       for p in (owner.get("phones") or []) if isinstance(p, dict)],
            "in_hottest": u in hottest, "in_ftm": u in ftm,
        }
        if i % 100 == 0 or i == len(todo):
            jsave(RECORDS, records)
            el = time.time() - t0
            rate = i / el if el else 0
            eta = ((len(todo) - i) / rate) / 60 if rate else 0
            say(f"    {i}/{len(todo)}  elapsed={el/60:.1f}m  ETA={eta:.1f}m")
    jsave(RECORDS, records)
    # Drop stale checkpoint entries that are no longer in either lane.
    records = {u: r for u, r in records.items() if u in hottest or u in ftm}
    jsave(RECORDS, records)
    if gone:
        say(f"  {len(gone)} record(s) 404'd (deleted from the account): {gone[:5]}")

    say("Phase 3: classifying every number (the never-double-bill rule)...")
    seeds = load_seeds()
    run_cache = jload(RUN_CACHE, {})

    # A number already tier-tagged ANYWHERE on the lane is scored: reuse the
    # tag account-wide. Conflicting tags on the same number are reported and
    # the best (lowest-rank) wins.
    account_tag: dict[str, str] = {}
    conflicts = []
    entries = 0
    for r in records.values():
        for p in r["phones"]:
            n = p["cleaned"]
            if not n:
                continue
            entries += 1
            t = score_tag_of(p["tags"])
            if not t:
                continue
            prev = account_tag.get(n)
            if prev and prev != t:
                conflicts.append((n, prev, t))
                if rank_of(t) < rank_of(prev):
                    account_tag[n] = t
            elif not prev:
                account_tag[n] = t

    numbers: dict[str, dict] = {}
    for r in records.values():
        for p in r["phones"]:
            n = p["cleaned"]
            if not n or n in numbers:
                continue
            if n in account_tag:
                numbers[n] = {"class": "account_tag", "tag": account_tag[n]}
            elif n in run_cache:
                e = run_cache[n]
                numbers[n] = {"class": "run_cache", "tag": e.get("tag") or eval_tag(e)}
            elif n in seeds:
                numbers[n] = {"class": "seed_cache", "tag": seeds[n]["tag"],
                              "source": seeds[n]["source"]}
            else:
                numbers[n] = {"class": "to_pay"}

    to_pay = sorted(n for n, e in numbers.items() if e["class"] == "to_pay")
    by_class = {}
    for e in numbers.values():
        by_class[e["class"]] = by_class.get(e["class"], 0) + 1
    no_phone = sum(1 for r in records.values() if not any(p["cleaned"] for p in r["phones"]))

    jsave(PULL_PLAN, {"ran_at": datetime.now().isoformat(timespec="seconds"),
                      "lane_counts": {ln["preset"]: len(lane_uuids[ln["preset"]]) for ln in lanes},
                      "records": len(records), "phone_entries": entries,
                      "unique_numbers": len(numbers), "by_class": by_class,
                      "numbers": numbers, "to_pay": to_pay,
                      "tag_conflicts": conflicts[:50], "gone": gone})

    say("")
    say("=" * 64)
    say("GATE 1 REPORT -- nothing spent, nothing written")
    say(f"  records:            {len(records)}  "
        f"(hottest {len(hottest)}, ftm {len(ftm)}, both {len(hottest & ftm)})")
    say(f"  records w/o phone:  {no_phone}")
    say(f"  phone entries:      {entries}   unique numbers: {len(numbers)}")
    say(f"  already tagged:     {by_class.get('account_tag', 0)}  (account tier tags, $0)")
    say(f"  run-cache hits:     {by_class.get('run_cache', 0)}  ($0)")
    say(f"  seed-cache hits:    {by_class.get('seed_cache', 0)}  ($0)")
    say(f"  TO PAY:             {len(to_pay)}")
    say(f"  cost at ${COST_PER_PHONE:.3f}/number: ${len(to_pay) * COST_PER_PHONE:.2f}"
        f"  (+ litigator add-on billed by Trestle on top)")
    if conflicts:
        say(f"  NOTE: {len(conflicts)} numbers carry conflicting tier tags; best kept")
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
        say("Gate 1: refusing to spend without --pay. Re-read the pull report first.")
        return 2

    env = dotenv_values(str(ROOT / ".env"))
    key = env.get("TRESTLE_PAID_API_KEY") or env.get("TRESTLE_API_KEY") or ""
    if not key:
        say("TRESTLE_PAID_API_KEY / TRESTLE_API_KEY not set in .env")
        return 2

    seeds = load_seeds()
    cache = jload(RUN_CACHE, {})
    # Re-derive the paid set from the CLASSES, then re-check every cache right
    # here: a checkpoint restart, or a pull re-run since, must never re-bill.
    todo = [n for n in plan["to_pay"] if n not in cache and n not in seeds]
    if a.limit:
        todo = todo[: a.limit]
    say(f"to score: {len(todo)} of {len(plan['to_pay'])} planned "
        f"({len(plan['to_pay']) - len(todo)} already cached since the pull)")
    say(f"cost: ${len(todo) * COST_PER_PHONE:.2f} at ${COST_PER_PHONE:.3f}/number"
        f"{' + litigator add-on' if not a.no_litigator else ''}")
    if not todo:
        say("nothing to pay for -- all covered by tags/caches")
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
            n = clean_phone(r["phone_number"])
            entry = {"score": r.get("activity_score"), "line_type": r.get("line_type"),
                     "is_valid": r.get("is_valid"),
                     "litigator": r.get("is_litigator_risk"),
                     "carrier": r.get("carrier"), "is_prepaid": r.get("is_prepaid"),
                     "tag": r.get("assigned_tag"), "keep": r.get("keep"),
                     "scored_at": datetime.now().isoformat(timespec="seconds")}
            cache[n] = entry
            paid += 1
        errors_all.extend(errors)
        jsave(RUN_CACHE, cache)          # checkpoint: a kill here re-bills nothing
        jsave(SCORE_ERRORS, errors_all)
        say(f"  {min(start + CHUNK, len(todo))}/{len(todo)}  "
            f"(paid so far this run: {paid}, errors: {len(errors)})")

    dist: dict = {}
    for n in todo:
        t = (cache.get(n) or {}).get("tag") or "ERROR"
        dist[t] = dist.get(t, 0) + 1
    say("")
    say(f"scored {paid} numbers  (~${paid * COST_PER_PHONE:.2f} base)")
    for t, c in sorted(dist.items(), key=lambda kv: -kv[1]):
        say(f"  {t:>16}: {c}")
    if errors_all:
        say(f"errors so far: {len(errors_all)} (kept in {SCORE_ERRORS.name}; "
            "NOT cached, so a re-run retries them)")
    say("next: push --probe")
    return 0


# ───────────────────────── push ─────────────────────────

def resolve_number_tag(n: str, plan_numbers: dict, cache: dict, seeds: dict) -> str:
    e = plan_numbers.get(n) or {}
    if e.get("class") == "account_tag":
        return e["tag"]
    if n in cache:
        return (cache[n].get("tag")) or eval_tag(cache[n])
    if e.get("class") in ("run_cache", "seed_cache") and e.get("tag"):
        return e["tag"]
    if n in seeds:
        return seeds[n]["tag"]
    return "Unknown"


def build_push_plan(records: dict, plan_numbers: dict, cache: dict, seeds: dict) -> dict:
    """Per record: the full owner phone list, tier-tagged and tier-sorted, plus
    whether Mail Only is due. Only records whose stored state differs are kept."""
    out = {}
    for u, r in records.items():
        phones = [p for p in r["phones"] if p["cleaned"]]
        if not phones or not r.get("owner_uuid"):
            continue
        planned, ranks, seen = [], [], set()
        for idx, p in enumerate(phones):
            n = p["cleaned"]
            if n in seen:
                continue  # upsert is by number; a duplicate entry collapses anyway
            seen.add(n)
            tag = resolve_number_tag(n, plan_numbers, cache, seeds)
            keep_tags = [t for t in p["tags"]
                         if t not in SCORE_TAGS and not t.startswith(SKIP_PREFIX)]
            new_tags = keep_tags + ([tag] if tag != "Unknown" else [])
            planned.append({"idx": idx, "rank": rank_of(tag), "tag": tag,
                            "number": p["number"], "cleaned": n,
                            "type": p["type"], "status": p["status"],
                            "is_connected": p["is_connected"], "tags": new_tags,
                            "old_tags": p["tags"]})
            ranks.append(rank_of(tag))
        planned.sort(key=lambda x: (x["rank"], x["idx"]))
        if len(planned) > OWNER_PHONE_CAP:
            # Can't happen from a pure retag (the store already caps at 30),
            # but never send a list the account would silently truncate.
            planned = planned[:OWNER_PHONE_CAP]

        mail_only = bool(ranks) and min(ranks) > CALLABLE_MAX_RANK
        tags_after = list(r["prop_tags"])
        if mail_only and MAIL_ONLY not in tags_after:
            tags_after.append(MAIL_ONLY)

        unchanged_order = [p["cleaned"] for p in planned] == \
                          list(dict.fromkeys(p["cleaned"] for p in phones))
        unchanged_tags = all(sorted(p["tags"]) == sorted(p["old_tags"]) for p in planned)
        if unchanged_order and unchanged_tags and tags_after == r["prop_tags"]:
            continue
        out[u] = {"owner_uuid": r["owner_uuid"], "owner_name": r["owner_name"],
                  "street": r["street"], "zip": r["zip"],
                  "phones": [{k: p[k] for k in
                              ("number", "cleaned", "type", "status", "is_connected", "tags")}
                             for p in planned],
                  "mail_only": mail_only,
                  "tags_after": tags_after if tags_after != r["prop_tags"] else None,
                  "best_tag": min((p["tag"] for p in planned), key=rank_of) if planned else None}
    return out


def push_one(api: Api, uuid: str, p: dict) -> dict:
    log = {"uuid": uuid, "street": p["street"], "steps": {},
           "at": datetime.now().isoformat(timespec="seconds")}
    payload = [{"number": ph["number"], "type": ph["type"], "tags": ph["tags"],
                "status": ph["status"], "is_connected": ph["is_connected"],
                "verified": False} for ph in p["phones"]]
    st, resp = api.write("POST", f"/api/internal/owner/{p['owner_uuid']}/upsert-phones/",
                         {"phones": payload})
    log["steps"]["phones"] = {"status": st, "sent": len(payload), "resp": str(resp)[:150]}
    if st not in (200, 201, 204):
        log["error"] = f"upsert-phones {st}"
        return log
    if p.get("tags_after"):
        # Full-list PATCH built on a FRESH read: prop_tags in the plan can be
        # minutes old and a sequence may have added tags since.
        st0, full = api.get(f"/api/internal/property/{uuid}/")
        current = tag_names((full or {}).get("tags")) if st0 == 200 else []
        merged = current + [t for t in p["tags_after"] if t not in current]
        st, resp = api.write("PATCH", f"/api/internal/property/{uuid}/", {"tags": merged})
        log["steps"]["tags"] = {"status": st,
                                "added": [t for t in p["tags_after"] if t not in current],
                                "resp": str(resp)[:150]}
        if st not in (200, 201, 204):
            log["error"] = f"tags PATCH {st}"
    return log


def verify_one(api: Api, uuid: str, p: dict) -> list[str]:
    """Read the record back and list every deviation from the plan."""
    bad = []
    st, full = api.get(f"/api/internal/property/{uuid}/")
    if st != 200:
        return [f"detail read {st}"]
    live = [ph for ph in ((full.get("owner") or {}).get("phones") or []) if isinstance(ph, dict)]
    live_by_num = {}
    live_seq = []
    for ph in live:
        n = clean_phone(str(ph.get("number") or ""))
        if n and n not in live_by_num:
            live_by_num[n] = tag_names(ph.get("tags"))
            live_seq.append(n)
    want_seq = [ph["cleaned"] for ph in p["phones"]]
    for ph in p["phones"]:
        got = live_by_num.get(ph["cleaned"])
        if got is None:
            bad.append(f"phone {ph['cleaned']} missing")
        elif sorted(got) != sorted(ph["tags"]):
            bad.append(f"phone {ph['cleaned']} tags {got} != {ph['tags']}")
    if live_seq[: len(want_seq)] != want_seq:
        bad.append(f"order {live_seq[:5]}... != planned {want_seq[:5]}...")
    if p.get("mail_only") and MAIL_ONLY not in tag_names(full.get("tags")):
        bad.append("Mail Only tag missing")
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
    cache = jload(RUN_CACHE, {})
    push_plan = build_push_plan(records, plan["numbers"], cache, seeds)
    unknown = sum(1 for p in push_plan.values() for ph in p["phones"]
                  if not any(t in SCORE_TAGS or t.startswith(SKIP_PREFIX) for t in ph["tags"]))
    mail_only_n = sum(1 for p in push_plan.values() if p["mail_only"])
    say(f"push plan: {len(push_plan)} records need writes "
        f"({mail_only_n} get {MAIL_ONLY!r}; {unknown} phones stay untagged/Unknown)")
    jsave(PUSH_PLAN, push_plan)
    if not push_plan:
        return 0

    if a.commit and not a.probe and not PROBE_OK.exists() and not a.force:
        say("Gate 2: no successful probe on record (probe_ok.json missing). "
            "Run `push --probe` first, or --force to override.")
        return 2

    api = Api()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = RUN / f"push_backup_{stamp}.json"
    push_log = jload(PUSH_LOG, {})

    targets = list(push_plan.items())
    if a.probe:
        pick = a.probe if a.probe != "first" else targets[0][0]
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
                f"best={p['best_tag']} mail_only={p['mail_only']}")
        return 0

    backup = {}
    done = errors = 0
    t0 = time.time()
    for i, (u, p) in enumerate(targets, 1):
        if u in push_log and not a.probe:
            continue  # resumable: already pushed in a prior run
        backup[u] = {"owner_uuid": records[u]["owner_uuid"],
                     "phones": records[u]["phones"],
                     "prop_tags": records[u]["prop_tags"]}
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
            el = time.time() - t0
            say(f"  {i}/{len(targets)}  written={done} errors={errors} "
                f"elapsed={el/60:.1f}m")
    jsave(PUSH_LOG, push_log)

    if a.probe:
        u, p = targets[0]
        bad = verify_one(api, u, p)
        say("")
        say(f"PROBE {u}  {p['street']}  owner {p['owner_name']!r}")
        for ph in p["phones"]:
            say(f"    {ph['cleaned']}  tags={ph['tags']}")
        if bad:
            say(f"  READ-BACK FAILED: {bad}")
            return 3
        say(f"  read-back CLEAN: tags landed, order landed"
            + (f", {MAIL_ONLY!r} stamped" if p["mail_only"] else ""))
        jsave(PROBE_OK, {"uuid": u, "at": datetime.now().isoformat(timespec="seconds")})
        say("  Gate 2 open: `push --commit` will do the rest")
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
            st, _ = api.write("POST", f"/api/internal/owner/{b['owner_uuid']}/upsert-phones/",
                              {"phones": payload})
            if st not in (200, 201, 204):
                say(f"  {u}: phones restore {st}")
                bad += 1
        st, _ = api.write("PATCH", f"/api/internal/property/{u}/", {"tags": b["prop_tags"]})
        if st not in (200, 201, 204):
            say(f"  {u}: tags restore {st}")
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
        if i % 100 == 0 or i == len(pushed):
            say(f"  {i}/{len(pushed)}  defects so far: {len(failures)}")
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = RUN / f"qa_{'repair' if a.repair else 'audit'}_{stamp}.json"
    jsave(out, failures)
    say(f"clean: {len(pushed) - len(failures)}/{len(pushed)}   report: {out.name}")
    if not failures:
        return 0
    if not a.repair:
        say("re-run with --repair to re-send the failed records (the ~3.5% "
            "first-upsert tag drop is expected)")
        return 1
    say(f"repairing {len(failures)} records (plain re-push fixes the tag drop)...")
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

    p = sub.add_parser("pull", help="resolve lanes, hydrate, classify, price (free)")
    p.add_argument("--fresh", action="store_true",
                   help="discard the hydration checkpoint and re-read every record")

    s = sub.add_parser("score", help="pay Trestle for the unscored numbers")
    s.add_argument("--pay", action="store_true", help="Gate 1: actually spend")
    s.add_argument("--no-litigator", action="store_true")
    s.add_argument("--limit", type=int, default=0, help="score at most N numbers")

    w = sub.add_parser("push", help="write tier tags + order + Mail Only")
    w.add_argument("--probe", nargs="?", const="first", default=None,
                   metavar="UUID", help="write ONE record and read it back")
    w.add_argument("--commit", action="store_true", help="Gate 2: bulk write")
    w.add_argument("--force", action="store_true", help="commit without a probe on record")
    w.add_argument("--limit", type=int, default=0)
    w.add_argument("--undo", default=None, metavar="BACKUP_JSON",
                   help="restore phones + property tags from a push backup")

    q = sub.add_parser("qa", help="audit pushed records against the live account")
    q.add_argument("--repair", action="store_true", help="re-push the failures")

    a = ap.parse_args()
    return {"pull": cmd_pull, "score": cmd_score, "push": cmd_push, "qa": cmd_qa}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
