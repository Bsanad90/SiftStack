"""Audit the records parked in the `Re-Skiptrace` property status. READ-ONLY.

Somebody parked these records expecting a re-trace, but Ty's escalation rule says
"skip-traced + no numbers -> don't re-skip, keep mailing, deep prospect" -- so the
status is a mixed bag. This script pulls the cohort live and sorts every record
into the action it actually needs:

  RE-SKIP        never skip traced and no numbers -> a skip trace IS the next step
  DEEP PROSPECT  skip traced and still empty (or every number is marked bad) -> a
                 re-skip returns the same nothing; research the heirs instead.
                 Sub-split by deceased_signal (research-worthy vs presumed-alive).
  SCORE          has live numbers that were never Trestle-tiered -> price them so
                 the dialer gets an order, no tracing needed
  RETAG          the work is already done (DP markers on the record, or every
                 number already tiered) -> the status is stale; just move it
  ENTITY         (2026-09-15) the owner is an LLC/trust with no person name and
                 the numbers are live -> the dialer has nobody to ask for;
                 research the principal (Enformion BusinessV2 / SOS), don't re-skip
  UNDIALED       (2026-09-15) callable, tiered numbers that were never
                 dispositioned (every one still UNKNOWN) -> parked prematurely;
                 send it back to the dialer, nothing to trace

2026-09-15 additions, after Basem found the status "has no tags or notes": every
row now carries WHO moved the record into the status and WHEN (the per-record
event log `GET /api/internal/property/{uuid}/logs/`, `property.status.updated`
with author email), the OPEN TASKS on the record (`GET /api/internal/task/` --
Pal's "Reskip trace" tasks are the only reasons anyone ever wrote down), the
board-note families present, and how many numbers were dispositioned since.

Nothing here writes to the account. The follow-up for each bucket runs only on an
explicit go.

Built on the proven pieces: cohort query + account-total guard from
`not_related_requalify` (an unrecognised filter key is silently ignored and the
count comes back as the account total), resumable detail cache from
`dpd_ftm_dp_candidates`, phone/tier reads from `dpd_lane_phone_score`, DP-done
markers from `obituary_dp_qa` (pinned DEEP PROSPECTING board message, REL custom
fields -- which live on their own endpoint, not the property detail).

Usage:
    python -u src/scripts/reskiptrace_audit.py                # full audit
    python -u src/scripts/reskiptrace_audit.py --count-only   # 2 calls, no hydration
    python -u src/scripts/reskiptrace_audit.py --fresh        # ignore the detail cache
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from dpd_lane_assignee_report import PAGE, count, list_uuids  # noqa: E402
from dpd_lane_phone_score import (  # noqa: E402
    CALLABLE_MAX_RANK,
    Api,
    jload,
    jsave,
    load_seeds,
    rank_of,
    say,
    score_tag_of,
    tag_names,
)
from not_related_requalify import bad_phone_status  # noqa: E402
from obituary_dp_batch import _note_family, deceased_signal  # noqa: E402
from phone_validator import COST_PER_PHONE, clean_phone  # noqa: E402

RUN = ROOT / "output" / "reskiptrace_audit"
DETAIL = RUN / "detail.json"

STATUS = "Re-Skiptrace"
# The account mixes display labels with snake_case ("No Answer" vs not_interested),
# so if the verbatim title counts zero these variants are probed before concluding
# the status is empty.
STATUS_VARIANTS = ["re_skiptrace", "Re Skiptrace", "re-skiptrace", "Re-skiptrace"]

# Paid Trestle results reused for free when estimating the SCORE bucket's cost
# (the never-double-bill rule from the lane run).
RUN_CACHES = [
    ROOT / "output" / "lane_phone_score" / "trestle_cache.json",
    ROOT / "output" / "not_related_requalify" / "trestle_cache.json",
]

# Loosened beyond obituary_dp_batch._OURS_RE on purpose: the account's phone-tag
# vocabulary holds both spellings ("Rel1.1" AND "Rel 1.1", "Owner.1" AND
# "Owner 1", "Pr.1"/"PR.1"/"Pr4" from the IDI era). The strict matcher would
# misread IDI-era records as never-tagged.
REL_TAG_RE = re.compile(r"^(rel ?\d+([. ]\d+)?|owner[. ]?\d+|pr\.? ?\d+([. ]\d+)?)$", re.I)

REL_CF_RE = re.compile(r"^REL\d+: Full Name$")

# Measured budgets (CLAUDE.md): the 619-record obituary batch ran $0.37/record
# all-in; the small lane batches $0.40-0.56. DataSift's own skip trace is the
# unlimited $97/mo plan, so a re-skip is $0 marginal.
DP_PER_RECORD_LO, DP_PER_RECORD_HI = 0.30, 0.55

BUCKETS = ["RE-SKIP", "DEEP PROSPECT", "ENTITY", "SCORE", "UNDIALED", "RETAG"]

SUGGESTED_NEXT = {
    "RE-SKIP": "run DataSift skip trace (unlimited plan, $0 marginal)",
    "DEEP PROSPECT": "obituary_dp_batch-style run (research heirs, keep mailing)",
    "ENTITY": "research the principal (enformion_business.find_principals / SOS), "
              "write the name into the owner block, then back to the dialer",
    "SCORE": "Trestle tier run (dpd_lane_phone_score engine, never-double-bill)",
    "UNDIALED": "parked with undialed callable numbers -> back to the dialer with a "
                "note; nothing to trace",
    "RETAG": "status flip off Re-Skiptrace (active-title vocabulary; e.g. "
             "'No Answer' for dialable records) -- explicit go required",
}

# Event-log shapes (per-record `logs/` endpoint, verified live 2026-09-15). The
# status payload is {"property": {"status": [old, new]}} with slug-cased values
# ("re-skiptrace"), the author is an email string or {"email": ...}.
EV_STATUS = "property.status.updated"
EV_PHONE_STATUS = "owner.phone.status.updated"
LOG_LIMIT = 50


def _author(ev: dict) -> str:
    a = ev.get("author")
    if isinstance(a, dict):
        return a.get("email") or a.get("first_name") or ""
    return str(a or "")


def record_log(api, uuid: str) -> list[dict]:
    """Latest LOG_LIMIT events on a record, newest first. [] on any failure."""
    st, r = api.get(f"/api/internal/property/{uuid}/logs/?limit={LOG_LIMIT}")
    if st != 200 or not isinstance(r, dict):
        return []
    return r.get("data") or []


def moved_into_status(events: list[dict], want_key: str) -> dict:
    """The most recent status change INTO the audited status: who, when, from what,
    plus how many phone dispositions happened after it (0 = parked untouched)."""
    out = {"moved_by": "", "moved_at": "", "moved_from": "", "dispositions_since": 0}
    for i, ev in enumerate(events):
        if ev.get("event_type") != EV_STATUS:
            continue
        old_new = ((ev.get("payload") or {}).get("property") or {}).get("status") or [None, None]
        if status_key(old_new[1]) == want_key:
            out.update(moved_by=_author(ev), moved_at=str(ev.get("timestamp") or ""),
                       moved_from=str(old_new[0] or ""))
            out["dispositions_since"] = sum(
                1 for e in events[:i] if e.get("event_type") == EV_PHONE_STATUS)
            break
    return out


def fetch_open_tasks(api) -> dict[str, list[dict]]:
    """property uuid -> open tasks (title, due, assignee, creator, task uuid).
    `GET /api/internal/task/` pages the whole account (242 tasks on 2026-09-15)."""
    out: dict[str, list[dict]] = {}
    seen: set[str] = set()
    offset = 0
    while True:
        # `ordering=created` pins the page order: without it the 2026-09-15 run
        # got one task twice and lost another across the page boundary.
        st, r = api.get(f"/api/internal/task/?limit=200&offset={offset}&ordering=created")
        if st != 200 or not isinstance(r, dict):
            break
        rows = r.get("results") or []
        for t in rows:
            if t.get("completed") or t.get("uuid") in seen:
                continue
            seen.add(t.get("uuid"))
            p = t.get("assigned_to_property")
            pid = p.get("uuid") if isinstance(p, dict) else p
            if not pid:
                continue
            asg = t.get("assigned_to_user") or {}
            cb = t.get("created_by") or {}
            out.setdefault(pid, []).append({
                "uuid": t.get("uuid"), "title": t.get("title") or "",
                "due": str(t.get("due_date") or "")[:10],
                "assigned": (asg.get("first_name") if isinstance(asg, dict) else asg) or "",
                "created_by": (cb.get("email") if isinstance(cb, dict) else cb) or "",
                "notes": t.get("notes") or "",
            })
        offset += 200
        if not rows or offset >= (r.get("count") or 0):
            break
    return out


def status_key(s) -> str:
    # = sold_backlog._status_key, kept local so this API-only script does not
    # import the sold-sweep module for a one-liner. Collapses _ and - because
    # the account stores some statuses snake_cased.
    return (str(s or "")).strip().lower().replace("_", " ").replace("-", " ")


def list_thin(api, query: dict, expected: int) -> dict[str, dict]:
    """Page the cohort keeping the thin rows -- `last_skip_traced` and the
    `skiptraced` flag exist there and corroborate the detail read."""
    out: dict[str, dict] = {}
    offset = 0
    while offset < expected:
        st, r = api.post_as_get("/api/internal/property/",
                                {"limit": PAGE, "offset": offset, "query": query})
        if st != 200:
            raise RuntimeError(f"thin list failed at offset {offset}: {st} {str(r)[:300]}")
        rows = r.get("results") or []
        if not rows:
            break
        for row in rows:
            out.setdefault(row["uuid"], row)
        offset += PAGE
    return out


def hydrate(api, uuids: list[str], fresh: bool) -> dict[str, dict]:
    """Detail + custom-field labels + board per record, resumable.

    Custom fields are NOT on the detail endpoint (dp_record_pull contract) and
    the DP note lives on the message board, not the notes field -- both need
    their own GET.
    """
    cache: dict = {} if fresh else jload(DETAIL, {})
    todo = [u for u in uuids if u not in cache]
    say(f"hydrating {len(todo)} records ({len(cache)} already cached)...")
    t0 = time.time()
    for i, u in enumerate(todo, 1):
        st, body = api.get(f"/api/internal/property/{u}/")
        if st == 404:
            cache[u] = {"_gone": True}   # real deletions happen mid-run (seen live)
            continue
        if st != 200:
            say(f"  detail {st} on {u}; skipping this pull")
            continue

        stc, cf = api.get(f"/api/internal/property/{u}/custom-field/")
        rows = cf if isinstance(cf, list) else ((cf.get("results") or [])
                                                if isinstance(cf, dict) else [])
        rel_cf = [((r.get("custom_field") or {}).get("label") or "")
                  for r in rows
                  if (r.get("value") or "").strip()
                  and REL_CF_RE.match((r.get("custom_field") or {}).get("label") or "")]

        stm, mb = api.get(f"/api/internal/property/{u}/message/?limit=100")
        msgs = (mb.get("results") or []) if isinstance(mb, dict) else []
        dp_msgs = [x for x in msgs if (x.get("message") or "").startswith("DEEP PROSPECTING")]

        events = record_log(api, u)

        cache[u] = {
            "detail": body,
            "rel_cf": rel_cf,
            "dp_board": bool(dp_msgs),
            "dp_pinned": any(x.get("pinned") for x in dp_msgs),
            # every board note's family (first line, cut at " (", date stripped) --
            # the reviewer's "is there ANY note here" answer, not just DP
            "board": [{"family": _note_family(x.get("message") or ""),
                       "pinned": bool(x.get("pinned")),
                       "author": _author(x), "created": str(x.get("created") or "")[:10]}
                      for x in msgs],
            "moved": moved_into_status(events, status_key(STATUS)),
            "log_events": len(events),
        }
        if i % 25 == 0 or i == len(todo):
            jsave(DETAIL, cache)
            say(f"    {i}/{len(todo)}  elapsed={(time.time() - t0) / 60:.1f}m")
    jsave(DETAIL, cache)
    return cache


def classify(u: str, entry: dict, thin: dict, tasks: list[dict] | None = None) -> dict:
    """One record -> bucket + everything a reviewer needs to check the call."""
    d = entry["detail"]
    addr = d.get("address") or {}
    owner = d.get("owner") or {}
    lists = tag_names(d.get("lists"))
    ptags = tag_names(d.get("tags"))
    tasks = tasks or []
    moved = entry.get("moved") or {}
    board = entry.get("board") or []
    # An entity owner with no person name: the dialer has nobody to ask for.
    entity_owner = bool((owner.get("company") or "").strip()) and \
        not (owner.get("first_name") or "").strip()

    phones = []
    for p in (owner.get("phones") or []):
        if not isinstance(p, dict):
            continue
        n = clean_phone(str(p.get("number") or ""))
        if not n:
            continue
        phones.append({"n": n, "tags": tag_names(p.get("tags")),
                       "status": p.get("status"), "type": p.get("type")})

    alive = [p for p in phones if not bad_phone_status(p["status"])]
    unscored = [p for p in alive if not score_tag_of(p["tags"])]
    scored = [p for p in alive if score_tag_of(p["tags"])]
    best_tier = min((score_tag_of(p["tags"]) for p in scored),
                    key=rank_of, default="")
    # Callable (rank <= CALLABLE_MAX_RANK) numbers whose CRM status is still the
    # never-dialed default: the record was parked without those being tried.
    callable_scored = [p for p in scored if rank_of(score_tag_of(p["tags"])) <= CALLABLE_MAX_RANK]
    undialed_callable = [p for p in callable_scored
                         if (p["status"] or "UNKNOWN").upper() == "UNKNOWN"]
    if callable_scored and len(undialed_callable) != len(callable_scored):
        undialed_callable = []   # at least one callable number WAS tried: not "undialed"

    skiptraced = bool(owner.get("skiptraced") or thin.get("skiptraced"))
    skip_tags = [t for t in ptags
                 if "skip" in t.lower() and ("trace" in t.lower() or "skipped" in t.lower())]

    # DP-done markers. Rel-tagged phones alone are only corroboration -- the IDI
    # imports also wrote Rel tags -- so they never flip dp_done by themselves.
    dp_tags = [t for t in ptags if t.lower().startswith(("deep prospect", "dp "))]
    rel_phone_tags = any(REL_TAG_RE.match(t) for p in phones for t in p["tags"])
    dp_markers = []
    if dp_tags:
        dp_markers.append("tags: " + ", ".join(dp_tags))
    if entry.get("dp_board"):
        dp_markers.append("DP board note" + ("" if entry.get("dp_pinned") else " (unpinned)"))
    if entry.get("rel_cf"):
        dp_markers.append(f"{len(entry['rel_cf'])} REL fields filled")
    dp_done = bool(dp_markers)
    if rel_phone_tags:
        dp_markers.append("Rel-tagged phones (corroboration only)")

    signal = deceased_signal(", ".join(lists), ", ".join(ptags),
                             (d.get("last_obituary_date") or "")[:10],
                             d.get("personal_representative") or "")

    if not phones:
        if dp_done:
            bucket, reason = "RETAG", "DP already done, still no numbers -> mail-only path"
        elif skiptraced:
            bucket, reason = "DEEP PROSPECT", "skip traced and came back empty (Ty's rule: don't re-skip)"
        else:
            bucket, reason = "RE-SKIP", "no numbers and never skip traced"
    elif not alive:
        if dp_done:
            bucket, reason = "RETAG", "DP done; every number on record is marked bad"
        elif skiptraced:
            bucket, reason = "DEEP PROSPECT", "skip traced; every number is marked wrong/dead/dnc"
        else:
            bucket, reason = "RE-SKIP", "only bad-status numbers and never skip traced"
    elif entity_owner and not dp_done:
        bucket = "ENTITY"
        reason = (f"owner is an entity ({owner.get('company')}) with no person name; "
                  f"{len(alive)} live numbers -> research the principal, don't re-skip")
    elif unscored:
        reason = f"{len(unscored)} of {len(alive)} live numbers never Trestle-scored"
        if dp_done:
            reason += " (DP already done)"
        bucket = "SCORE"
    elif undialed_callable:
        bucket = "UNDIALED"
        reason = (f"{len(undialed_callable)} callable number(s) (best {best_tier}) never "
                  f"dispositioned -- parked before being dialed")
    else:
        bucket = "RETAG"
        reason = (f"all {len(alive)} live numbers already tiered (best {best_tier})"
                  + ("; DP done" if dp_done else "") + " -> nothing left to trace")

    return {
        "uuid": u, "bucket": bucket, "reason": reason,
        "moved_by": moved.get("moved_by", ""), "moved_at": moved.get("moved_at", "")[:16],
        "moved_from": moved.get("moved_from", ""),
        "dispositions_since": moved.get("dispositions_since", 0),
        "open_tasks": " | ".join(f"{t['title']} (to {t['assigned']}, due {t['due']})"
                                 for t in tasks),
        "board_notes": " | ".join(f"{b['family']}{' [pinned]' if b['pinned'] else ''}"
                                  for b in board) or "(none)",
        "phones_undialed": len(undialed_callable),
        "street": addr.get("street") or "", "city": addr.get("city") or "",
        "state": addr.get("state") or "", "zip": (addr.get("postal_code") or "")[:5],
        "county": addr.get("county") or "",
        "owner": (" ".join(x for x in [owner.get("first_name"), owner.get("last_name")]
                           if x).strip() or (owner.get("company") or "")),
        "skiptraced": skiptraced,
        "last_skip_traced": (thin.get("last_skip_traced") or "")[:10],
        "skip_tags": ", ".join(skip_tags),
        "phones_total": len(phones), "phones_bad": len(phones) - len(alive),
        "phones_scored": len(scored), "phones_unscored": len(unscored),
        "best_tier": best_tier,
        "dp_markers": "; ".join(dp_markers),
        "deceased_signal": signal,
        "lists": ", ".join(lists), "tags": ", ".join(ptags),
        "assigned_to": d.get("assigned_to") or "",
        "estimate_value": d.get("estimate_value") or "",
        "last_sold": (d.get("last_sold") or "")[:10],
        "_unscored_numbers": [p["n"] for p in unscored],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", default=STATUS)
    ap.add_argument("--count-only", action="store_true")
    ap.add_argument("--fresh", action="store_true", help="ignore the detail cache")
    a = ap.parse_args()

    RUN.mkdir(parents=True, exist_ok=True)
    api = Api()
    say("JWT minted OK")

    # THE GUARD: an unrecognised filter key is silently ignored and the count
    # comes back as the account total looking like a real answer.
    total = count(api, {})
    say(f"account total (guard): {total}")
    status_title = a.status
    query = {"must": {"any_property_status": [status_title]}}
    n = count(api, query)
    say(f"status {status_title!r}: {n}")
    if n == total:
        say("FAIL: cohort count equals the unfiltered account total -- the "
            "any_property_status key was ignored, not applied. Stopping.")
        return 3
    if n == 0:
        say("zero on the verbatim title; probing casing/snake variants...")
        for v in STATUS_VARIANTS:
            q = {"must": {"any_property_status": [v]}}
            nv = count(api, q)
            say(f"  {v!r}: {nv}")
            if 0 < nv < total:
                status_title, query, n = v, q, nv
                break
        if n == 0:
            say("WARNING: every variant counted zero. The 2026-08-21 hydration "
                "held 111 -- open the Records UI before trusting this.")
            return 2
    if a.count_only:
        say("--count-only: stopping before hydration.")
        return 0

    thin_rows = list_thin(api, query, n)
    uuids = list(thin_rows)
    if len(uuids) != n:
        say(f"  NOTE: paged {len(uuids)} uuids but count said {n} "
            "(records moving under us mid-read)")

    cache = hydrate(api, uuids, a.fresh)
    gone = [u for u in uuids if (cache.get(u) or {}).get("_gone")]
    if gone:
        say(f"  {len(gone)} record(s) 404'd (deleted from the account): {gone[:5]}")

    open_tasks = fetch_open_tasks(api)
    say(f"open tasks account-wide: {sum(len(v) for v in open_tasks.values())} "
        f"on {len(open_tasks)} records")

    # The filtered index lags writes: trust each record's own detail read.
    want = status_key(status_title)
    rows, moved = [], []
    for u in uuids:
        entry = cache.get(u)
        if not entry or entry.get("_gone") or not entry.get("detail"):
            continue
        if status_key(entry["detail"].get("status")) != want:
            moved.append(u)
            continue
        rows.append(classify(u, entry, thin_rows.get(u) or {}, open_tasks.get(u)))

    # Records a dialer asked to re-skip via a TASK but never moved into the status
    # (Pal's convention, seen live 2026-09-15 on 2 records): list them so they are
    # not lost, without pretending they hold the status.
    tasked_elsewhere = {pid: ts for pid, ts in open_tasks.items()
                        if pid not in cache and any(
                            re.search(r"re.?skip|no name|not related", t["title"], re.I)
                            for t in ts)}
    if tasked_elsewhere:
        say(f"  {len(tasked_elsewhere)} record(s) carry a re-skip TASK but are NOT in "
            f"the status: {list(tasked_elsewhere)}")
    if moved:
        say(f"  {len(moved)} record(s) no longer hold the status on their detail "
            f"read (index lag / moved) -- excluded: {moved[:5]}")

    # ── SCORE bucket cost: dedupe unscored numbers against the paid caches ──
    seeds = load_seeds()
    for path in RUN_CACHES:
        c = jload(path, {})
        if c:
            say(f"  run cache {path.parent.name}: {len(c)} numbers reusable free")
        for num in c:
            nn = clean_phone(num)
            if nn:
                seeds.setdefault(nn, {"source": path.parent.name})
    to_pay, cached_free = set(), set()
    for r in rows:
        for nn in r["_unscored_numbers"]:
            (cached_free if nn in seeds else to_pay).add(nn)

    # ── outputs ──
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_csv = RUN / f"audit_{ts}.csv"
    order = {b: i for i, b in enumerate(BUCKETS)}
    rows.sort(key=lambda r: (order.get(r["bucket"], 9), r["street"]))
    fields = [k for k in rows[0].keys() if not k.startswith("_")] if rows else []
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    say("")
    say(f"=== Re-Skiptrace audit: {len(rows)} records "
        f"(cohort {n}, moved {len(moved)}, deleted {len(gone)}) ===")
    for b in BUCKETS:
        sub = [r for r in rows if r["bucket"] == b]
        if not sub:
            continue
        say(f"\n{b}: {len(sub)}  -> {SUGGESTED_NEXT[b]}")
        if b == "DEEP PROSPECT":
            with_sig = [r for r in sub if r["deceased_signal"]]
            say(f"    deceased signal (research-worthy): {len(with_sig)}; "
                f"presumed-alive path: {len(sub) - len(with_sig)}")
            say(f"    est. cost ${len(sub) * DP_PER_RECORD_LO:.2f}"
                f"-${len(sub) * DP_PER_RECORD_HI:.2f}")
        if b == "SCORE":
            say(f"    unscored numbers: {len(to_pay) + len(cached_free)} unique "
                f"({len(cached_free)} free from prior caches, {len(to_pay)} to pay "
                f"= ${len(to_pay) * COST_PER_PHONE:.2f})")
        for r in sub[:5]:
            say(f"    {r['street']}, {r['city']}  [{r['owner']}]  -- {r['reason']}")
            say(f"        parked by {r['moved_by'] or '?'} {r['moved_at'] or ''}; "
                f"tasks: {r['open_tasks'] or '-'}; notes: {r['board_notes']}")
        if len(sub) > 5:
            say(f"    ... and {len(sub) - 5} more (see CSV)")

    # Who parks records here, and how (the convention audit Basem asked for).
    by_mover: dict[str, dict] = {}
    for r in rows:
        m = by_mover.setdefault(r["moved_by"] or "(unknown)",
                                {"n": 0, "with_task": 0, "with_note": 0, "untouched": 0})
        m["n"] += 1
        m["with_task"] += bool(r["open_tasks"])
        m["with_note"] += r["board_notes"] != "(none)"
        m["untouched"] += (r["dispositions_since"] == 0)
    say("\nparked by (n / left a task / record has any note / no phone dispositioned):")
    for who, m in sorted(by_mover.items(), key=lambda kv: -kv[1]["n"]):
        say(f"    {who:36} {m['n']:3} / {m['with_task']:3} / {m['with_note']:3} / {m['untouched']:3}")

    jsave(RUN / f"summary_{ts}.json", {
        "status_title": status_title, "cohort": n, "audited": len(rows),
        "moved": moved, "deleted": gone,
        "tasked_not_in_status": tasked_elsewhere,
        "parked_by": by_mover,
        "buckets": {b: sum(1 for r in rows if r["bucket"] == b) for b in BUCKETS},
        "score_to_pay": sorted(to_pay), "score_cached_free": sorted(cached_free),
        "csv": str(out_csv),
    })
    say(f"\nCSV: {out_csv}")
    say("Read-only run complete. Nothing was written to the account.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
