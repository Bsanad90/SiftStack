"""Re-qualify the records parked in the `Not Related to Property` status.

That status sits in `preset_spec.DEAD_STATUSES`, so every preset excludes these
records -- they are dead to all marketing. Basem's triage (2026-09-04):

  1. Entry-tag each record by SOURCE LIST: `Obituary`/`Inheritance` -> Priority 1,
     `Register of Wills` / probate lists -> FTM, neither -> Priority 2. A record
     matching both rules gets both tags (62 FTM+P1 records already exist).
  2. Sold check: `last_sold` within 365 days -> a REVIEW CSV first, never an
     automatic stamp (`last_sold` can equally mean the current owner just bought
     it). `stamp-sold` runs only on the reviewed CSV.
  3. Trestle-score every phone (never double-billing), tier-tag + tier-order.
  4. Move survivors (not sold + >= 1 callable phone) to the `No Answer` status
     so they re-enter the lane presets. Sold-pending-review, all-Drop and
     no-phone records keep the status.

     Why `No Answer` and not blank (Basem, 2026-09-04, after the live probe):
     `PATCH {"status": null}` 400s ("This field may not be null."), "" 400s
     ("is not a valid status choice"), and every fresh-lead status on this
     account (new_lead, lead, prospecting, No Contact New Lead) is DEACTIVATED
     (`is_active: false` on GET /api/internal/status/) -- a deactivated status
     is "not a valid status choice" even though records still hold it. The
     write vocabulary is exactly the is_active titles, verbatim casing.

Built on the proven `dpd_lane_phone_score.py` engine; this script's cohort is
the codebase's FIRST inclusive status query, so the account-total guard from
`dpd_lane_assignee_report` is kept verbatim (an unrecognised filter key is
silently ignored and the count comes back as the account total).

Facts inherited from the lane run (each verified live there):
  * Phone writes: full-list `POST /api/internal/owner/{uuid}/upsert-phones/`.
  * Property tags: read-modify-write `PATCH /api/internal/property/{uuid}/`
    with the FULL tag list. `tags_add` / `POST .../add-tags/` are banned.
  * Tag PATCH is merge-only: removal is `POST .../remove-tags/` (undo uses it).
  * Phone tags fail to land on ~3.5% of first upserts: qa --repair is not optional.
  * Status writes take an ACTIVE status title verbatim; null, "", display
    aliases and deactivated titles all 400 (probed live 2026-09-04).

One caveat stated up front: Trestle scores CONTACTABILITY, not identity. These
records went dead because the person reached was not related to the property --
a `Dial First` tag means the line is active, not that it belongs to the owner.

Usage (in order; each step gates the next):
    python src/scripts/not_related_requalify.py pull                # free, read-only
    python src/scripts/not_related_requalify.py score --pay         # Gate 1: spends money
    python src/scripts/not_related_requalify.py push --probe        # one record + read-back
    python src/scripts/not_related_requalify.py push --commit       # Gate 2: bulk write
    python src/scripts/not_related_requalify.py qa [--repair]
    python src/scripts/not_related_requalify.py push --undo <backup.json>
    # after Basem reviews sold_review.csv (delete rows to reject, or fill `skip`):
    python src/scripts/not_related_requalify.py stamp-sold --commit
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from dotenv import dotenv_values  # noqa: E402

from dpd_lane_assignee_report import count, list_uuids  # noqa: E402
from dpd_lane_phone_score import (  # noqa: E402
    CALLABLE_MAX_RANK,
    MAIL_ONLY,
    OWNER_PHONE_CAP,
    RANK,
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
from phone_validator import (  # noqa: E402
    COST_PER_PHONE,
    DEFAULT_TIERS,
    clean_phone,
    process_phones,
)

RUN = ROOT / "output" / "not_related_requalify"
RECORDS = RUN / "records.json"
PULL_PLAN = RUN / "pull_plan.json"
RUN_CACHE = RUN / "trestle_cache.json"
SCORE_ERRORS = RUN / "score_errors.json"
PUSH_PLAN = RUN / "push_plan.json"
PUSH_LOG = RUN / "push_log.json"
PROBE_OK = RUN / "probe_ok.json"
SOLD_REVIEW = RUN / "sold_review.csv"
SOLD_LOG = RUN / "sold_stamp_log.json"

# The exact stored casing -- statuses go into the query as bare strings and the
# account mixes display labels with snake_case, so the string must be verbatim.
STATUS = "Not Related to Property"

# Where survivors go (Basem's pick after blank proved impossible): the generic
# active-dialer status. Must stay an is_active title on GET /api/internal/status/.
REQUALIFY_STATUS = "No Answer"

# The lane run's paid results, reused for free (same run-cache entry shape).
LANE_CACHE = ROOT / "output" / "lane_phone_score" / "trestle_cache.json"

ENTRY_TAGS = {"Priority 1", "Priority 2", "FTM", "Tier 2"}
P1_LIST_MARKERS = ("obituary", "inheritance")
FTM_LIST_MARKERS = ("register of wills", "probate")

# = datasift_uploader.RECENTLY_SOLD_TAG; defined locally so an API-only script
# does not import the Playwright module for one constant.
RECENTLY_SOLD_TAG = "Recently Sold"

SOLD_WINDOW_DAYS = 365

# A phone whose CRM status already marks it bad is never paid for, never counts
# as callable, and ranks with Drop for ordering.
BAD_PHONE_MARKERS = ("wrong", "dead", "disconnect", "dnc", "do not call", "bad number")
BAD_STATUS_RANK = RANK["Drop"]


def bad_phone_status(s) -> bool:
    t = (str(s or "")).strip().lower().replace("_", " ")
    return any(m in t for m in BAD_PHONE_MARKERS)


def entry_tags_for(lists: list[str], existing_tags: list[str]) -> list[str]:
    """Basem's rule. Returns only the tags MISSING from the record."""
    ls = [(x or "").lower() for x in lists]
    derived = []
    if any(m in l for l in ls for m in P1_LIST_MARKERS):
        derived.append("Priority 1")
    if any(m in l for l in ls for m in FTM_LIST_MARKERS):
        derived.append("FTM")
    if not derived and not (set(existing_tags) & ENTRY_TAGS):
        derived.append("Priority 2")
    return [t for t in derived if t not in existing_tags]


def load_lane_cache() -> dict:
    cache = jload(LANE_CACHE, {})
    if cache:
        say(f"  lane run cache: {len(cache)} numbers reused free")
    return cache


def resolve_tag(n: str, plan_numbers: dict, run_cache: dict,
                lane_cache: dict, seeds: dict) -> str:
    e = plan_numbers.get(n) or {}
    if e.get("class") == "account_tag":
        return e["tag"]
    if n in run_cache:
        return run_cache[n].get("tag") or eval_tag(run_cache[n])
    if n in lane_cache:
        return lane_cache[n].get("tag") or eval_tag(lane_cache[n])
    if e.get("tag"):
        return e["tag"]
    if n in seeds:
        return seeds[n]["tag"]
    return "Unknown"


def price_band(p) -> str:  # = sold_backlog.price_band (kept local, same reason)
    try:
        v = float(p or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v <= 0:
        return "blank_or_zero"
    if v < 20000:
        return "under_20k"
    if v < 100000:
        return "20k_100k"
    return "100k_plus"


# ───────────────────────── pull ─────────────────────────

def cmd_pull(a) -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    api = Api()
    say("JWT minted OK")

    # THE GUARD: this is the codebase's first inclusive status query. An
    # unrecognised key is silently ignored and the count comes back as the
    # account total looking like a real answer -- so the total is taken first.
    query = {"must": {"any_property_status": [STATUS]}}
    total = count(api, {})
    n = count(api, query)
    say(f"account total (guard): {total}")
    say(f"status {STATUS!r}: {n}")
    if n == total:
        say("FAIL: cohort count equals the unfiltered account total -- the "
            "any_property_status key was ignored, not applied. Stopping.")
        return 3
    if n == 0:
        say("WARNING: zero records in the status. The 2026-09-01 hydration "
            "showed ~178 -- open the Records UI before trusting this.")
        return 2

    uuids = list_uuids(api, query, n)
    if len(uuids) != n:
        say(f"  NOTE: paged {len(uuids)} uuids but count said {n} "
            "(records moving under us mid-read)")

    records: dict = {} if a.fresh else jload(RECORDS, {})
    todo = [u for u in uuids if u not in records]
    say(f"hydrating {len(todo)} records ({len(records)} already cached)...")
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
            "state": addr.get("state"), "zip": addr.get("postal_code"),
            "county": addr.get("county"),
            "status": body.get("status"),
            "last_sold": body.get("last_sold"),
            "last_sale_price": body.get("last_sale_price"),
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
    # Keep only the current cohort: a stale checkpoint may hold records whose
    # status has since changed.
    records = {u: r for u, r in records.items() if u in set(uuids)}
    jsave(RECORDS, records)
    if gone:
        say(f"  {len(gone)} record(s) 404'd (deleted from the account): {gone[:5]}")

    # The filtered index lags writes: trust each record's own detail read, not
    # the search index that selected it.
    moved = {u for u, r in records.items() if (r.get("status") or "") != STATUS}
    if moved:
        say(f"  {len(moved)} record(s) no longer hold the status on their detail "
            f"read (index lag / moved) -- excluded: {sorted(moved)[:5]}")
        records = {u: r for u, r in records.items() if u not in moved}
        jsave(RECORDS, records)

    # ── sold split ──
    since = (date.today() - timedelta(days=SOLD_WINDOW_DAYS)).isoformat()
    sold_rows = []
    for u, r in sorted(records.items()):
        sold = r.get("last_sold") or ""
        if sold and sold >= since:
            sold_rows.append({
                "uuid": u, "street": r["street"] or "", "city": r["city"] or "",
                "state": r["state"] or "", "zip": r["zip"] or "",
                "owner": r["owner_name"], "prior_status": r["status"] or "",
                "last_sold": sold, "sale_year": sold[:4],
                "last_sale_price": r.get("last_sale_price") or "",
                "price_band": price_band(r.get("last_sale_price")),
                "planned_entry_tags": ", ".join(
                    entry_tags_for(r["lists"], r["prop_tags"])),
                "sold_month_tag": f"Sold {sold[:7]}",
                "skip": "",
            })
    # Review-first ordering, same as sold_backlog: likeliest false positives lead.
    band_rank = {"blank_or_zero": 0, "under_20k": 1, "20k_100k": 2, "100k_plus": 3}
    sold_rows.sort(key=lambda x: (band_rank.get(x["price_band"], 9), x["last_sold"]))
    if sold_rows:
        with open(SOLD_REVIEW, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(sold_rows[0].keys()))
            w.writeheader()
            w.writerows(sold_rows)
    sold_uuids = {r["uuid"] for r in sold_rows}

    # ── entry-tag split (informational; the push applies it) ──
    tag_split: dict[str, int] = {}
    for u, r in records.items():
        if u in sold_uuids:
            continue
        for t in entry_tags_for(r["lists"], r["prop_tags"]) or ["(already tagged)"]:
            tag_split[t] = tag_split.get(t, 0) + 1

    # ── phone classification over NON-SOLD records only ──
    say("classifying every number (the never-double-bill rule)...")
    seeds = load_seeds()
    lane_cache = load_lane_cache()
    run_cache = jload(RUN_CACHE, {})

    live = {u: r for u, r in records.items() if u not in sold_uuids}
    account_tag: dict[str, str] = {}
    entries = 0
    status_all_bad: dict[str, bool] = {}
    for r in live.values():
        for p in r["phones"]:
            n = p["cleaned"]
            if not n:
                continue
            entries += 1
            status_all_bad[n] = status_all_bad.get(n, True) and bad_phone_status(p["status"])
            t = score_tag_of(p["tags"])
            if t and (n not in account_tag or rank_of(t) < rank_of(account_tag[n])):
                account_tag[n] = t

    numbers: dict[str, dict] = {}
    for r in live.values():
        for p in r["phones"]:
            n = p["cleaned"]
            if not n or n in numbers:
                continue
            if n in account_tag:
                numbers[n] = {"class": "account_tag", "tag": account_tag[n]}
            elif n in run_cache:
                e = run_cache[n]
                numbers[n] = {"class": "run_cache", "tag": e.get("tag") or eval_tag(e)}
            elif n in lane_cache:
                e = lane_cache[n]
                numbers[n] = {"class": "lane_cache", "tag": e.get("tag") or eval_tag(e)}
            elif n in seeds:
                numbers[n] = {"class": "seed_cache", "tag": seeds[n]["tag"],
                              "source": seeds[n]["source"]}
            elif status_all_bad.get(n):
                numbers[n] = {"class": "bad_status"}   # never pay; ranks with Drop
            else:
                numbers[n] = {"class": "to_pay"}

    to_pay = sorted(n for n, e in numbers.items() if e["class"] == "to_pay")
    by_class: dict[str, int] = {}
    for e in numbers.values():
        by_class[e["class"]] = by_class.get(e["class"], 0) + 1
    no_phone = sum(1 for r in live.values() if not any(p["cleaned"] for p in r["phones"]))

    jsave(PULL_PLAN, {"ran_at": datetime.now().isoformat(timespec="seconds"),
                      "status": STATUS, "cohort": len(records),
                      "sold_pending_review": sorted(sold_uuids),
                      "sold_since": since,
                      "entry_tag_split": tag_split,
                      "phone_entries": entries, "unique_numbers": len(numbers),
                      "by_class": by_class, "numbers": numbers, "to_pay": to_pay,
                      "gone": gone, "moved": sorted(moved)})

    say("")
    say("=" * 64)
    say("GATE 1 REPORT -- nothing spent, nothing written")
    say(f"  cohort ({STATUS!r}): {len(records)} records")
    say(f"  sold in past {SOLD_WINDOW_DAYS}d (PENDING REVIEW, excluded below): "
        f"{len(sold_uuids)}  -> {SOLD_REVIEW.name}")
    say(f"  entry-tag plan on the {len(live)} live records:")
    for t, c in sorted(tag_split.items()):
        say(f"    {t:>18}: {c}")
    say(f"  records w/o phone (keep status, no Mail Only): {no_phone}")
    say(f"  phone entries: {entries}   unique numbers: {len(numbers)}")
    for cls in ("account_tag", "run_cache", "lane_cache", "seed_cache", "bad_status"):
        if by_class.get(cls):
            say(f"  {cls:>12}: {by_class[cls]}  ($0)")
    say(f"  TO PAY:       {len(to_pay)}")
    say(f"  cost at ${COST_PER_PHONE:.3f}/number: ${len(to_pay) * COST_PER_PHONE:.2f}"
        f"  (+ litigator add-on billed by Trestle on top)")
    say("  NOTE: Trestle scores contactability, not identity -- these records "
        "went dead because the person reached was not related.")
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
    lane_cache = load_lane_cache()
    cache = jload(RUN_CACHE, {})
    # Re-check every cache right here: a checkpoint restart must never re-bill.
    todo = [n for n in plan["to_pay"]
            if n not in cache and n not in seeds and n not in lane_cache]
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
            cache[n] = {"score": r.get("activity_score"), "line_type": r.get("line_type"),
                        "is_valid": r.get("is_valid"),
                        "litigator": r.get("is_litigator_risk"),
                        "carrier": r.get("carrier"), "is_prepaid": r.get("is_prepaid"),
                        "tag": r.get("assigned_tag"), "keep": r.get("keep"),
                        "scored_at": datetime.now().isoformat(timespec="seconds")}
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

def build_push_plan(records: dict, plan: dict, cache: dict,
                    lane_cache: dict, seeds: dict) -> dict:
    """Per non-sold record: tier-tagged tier-sorted phones, entry tags, Mail
    Only, and whether the status clears. Only records needing a write are kept."""
    sold = set(plan.get("sold_pending_review") or [])
    plan_numbers = plan["numbers"]
    out = {}
    for u, r in records.items():
        if u in sold:
            continue
        phones = [p for p in r["phones"] if p["cleaned"]]
        planned, ranks, seen = [], [], set()
        for idx, p in enumerate(phones):
            n = p["cleaned"]
            if n in seen:
                continue  # upsert is by number; a duplicate entry collapses anyway
            seen.add(n)
            tag = resolve_tag(n, plan_numbers, cache, lane_cache, seeds)
            # A phone whose CRM status marks it bad ranks with Drop no matter
            # what the tier says: it was dialed and disproven on THIS record.
            rk = BAD_STATUS_RANK if bad_phone_status(p["status"]) else rank_of(tag)
            keep_tags = [t for t in p["tags"]
                         if t not in SCORE_TAGS and not t.startswith(SKIP_PREFIX)]
            new_tags = keep_tags + ([tag] if tag != "Unknown" else [])
            planned.append({"idx": idx, "rank": rk, "tag": tag,
                            "number": p["number"], "cleaned": n,
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
        clear_status = callable_ and (r.get("status") or "") == STATUS

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
    live_tags = tag_names(full.get("tags"))
    for t in p.get("tags_add") or []:
        if t not in live_tags:
            bad.append(f"property tag {t!r} missing")
    live_status = full.get("status")
    if p.get("clear_status"):
        if live_status != REQUALIFY_STATUS:
            bad.append(f"status {live_status!r} != planned {REQUALIFY_STATUS!r}")
    elif (live_status or "") not in (STATUS, ""):
        # not planned to change; a different status means someone moved it
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
    lane_cache = load_lane_cache()
    cache = jload(RUN_CACHE, {})
    push_plan = build_push_plan(records, plan, cache, lane_cache, seeds)
    mail_only_n = sum(1 for p in push_plan.values() if p["mail_only"])
    clear_n = sum(1 for p in push_plan.values() if p["clear_status"])
    say(f"push plan: {len(push_plan)} records need writes "
        f"({clear_n} status clears, {mail_only_n} get {MAIL_ONLY!r}, "
        f"{len(plan.get('sold_pending_review') or [])} sold held for review)")
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
        if a.probe == "first":
            # Prefer probing a record that exercises the unproven status clear.
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
        # Tag PATCH is merge-only: restore the prior list, then strip what we
        # ADDED via remove-tags (the only real tag-removal surface).
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


# ───────────────────────── stamp-sold ─────────────────────────

def cmd_stamp_sold(a) -> int:
    """Stamp `Recently Sold` + `Sold YYYY-MM` on the REVIEWED rows only.

    Runs after Basem edits sold_review.csv: delete a row (or put anything in
    its `skip` column) to reject it. The live "Sold Property Cleanup" sequence
    then flips each stamped record to `Already Sold`, deletes its tasks and
    clears its assignee -- that is the point, and it is why review comes first.
    """
    path = Path(a.csv)
    if not path.exists():
        say(f"reviewed CSV not found: {path}")
        return 2
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh)]
    kept = [r for r in rows if not (r.get("skip") or "").strip()]
    say(f"{path.name}: {len(rows)} rows, {len(kept)} to stamp "
        f"({len(rows) - len(kept)} marked skip)")
    if not kept:
        return 0
    if not a.commit:
        say("dry run (no --commit): nothing sent. Rows that would be stamped:")
        for r in kept[:15]:
            say(f"  {r['uuid']}  {r['street']}  sold {r['last_sold']} "
                f"({r['price_band']})  -> Recently Sold + {r['sold_month_tag']}")
        return 0

    api = Api()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = RUN / f"sold_stamp_backup_{stamp}.json"
    log = jload(SOLD_LOG, {})
    backup = {}
    done = errors = 0
    for r in kept:
        u = r["uuid"]
        if u in log:
            continue  # resumable
        st0, full = api.get(f"/api/internal/property/{u}/")
        if st0 != 200:
            say(f"  {u}: detail read {st0}; skipped")
            errors += 1
            continue
        current = tag_names(full.get("tags"))
        want = [RECENTLY_SOLD_TAG, r["sold_month_tag"]]
        add = [t for t in want if t not in current]
        backup[u] = {"prop_tags": current, "status": full.get("status"),
                     "tags_add": add}
        jsave(backup_path, backup)
        if not add:
            log[u] = {"already": True}
            done += 1
            continue
        st, resp = api.write("PATCH", f"/api/internal/property/{u}/",
                             {"tags": current + add})
        stv, fullv = api.get(f"/api/internal/property/{u}/")
        live = tag_names((fullv or {}).get("tags")) if stv == 200 else []
        ok = st in (200, 201, 204) and all(t in live for t in want)
        log[u] = {"status": st, "added": add, "verified": ok}
        jsave(SOLD_LOG, log)
        if ok:
            done += 1
        else:
            errors += 1
            say(f"  ERROR {u} {r['street']}: PATCH {st}, live tags miss "
                f"{[t for t in want if t not in live]}")
    jsave(SOLD_LOG, log)
    say(f"\nstamped {done}, errors {errors}. backup: {backup_path.name}")
    say("the Sold Property Cleanup sequence now flips them to Already Sold; "
        "spot-check one in the UI (allow a minute of index lag)")
    return 0 if not errors else 1


# ───────────────────────── main ─────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pull", help="query the status, hydrate, classify (free)")
    p.add_argument("--fresh", action="store_true",
                   help="discard the hydration checkpoint and re-read every record")

    s = sub.add_parser("score", help="pay Trestle for the unscored numbers")
    s.add_argument("--pay", action="store_true", help="Gate 1: actually spend")
    s.add_argument("--no-litigator", action="store_true")
    s.add_argument("--limit", type=int, default=0, help="score at most N numbers")

    w = sub.add_parser("push", help="write entry tags + tier tags + status clears")
    w.add_argument("--probe", nargs="?", const="first", default=None,
                   metavar="UUID", help="write ONE record and read it back")
    w.add_argument("--commit", action="store_true", help="Gate 2: bulk write")
    w.add_argument("--force", action="store_true", help="commit without a probe on record")
    w.add_argument("--limit", type=int, default=0)
    w.add_argument("--undo", default=None, metavar="BACKUP_JSON",
                   help="restore phones + tags + status from a push backup")

    q = sub.add_parser("qa", help="audit pushed records against the live account")
    q.add_argument("--repair", action="store_true", help="re-push the failures")

    ss = sub.add_parser("stamp-sold",
                        help="stamp Recently Sold on the REVIEWED sold_review.csv")
    ss.add_argument("--csv", default=str(SOLD_REVIEW))
    ss.add_argument("--commit", action="store_true")

    a = ap.parse_args()
    return {"pull": cmd_pull, "score": cmd_score, "push": cmd_push,
            "qa": cmd_qa, "stamp-sold": cmd_stamp_sold}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
