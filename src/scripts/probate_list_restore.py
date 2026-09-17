"""Restore the `Probate` list, which was emptied account-wide on 2026-09-16.

WHAT HAPPENED. While probing for a per-record list-removal route, a call to
`POST /api/internal/property/remove-lists/` with a body of
`{"props": ["<one uuid>"], "lists": ["Probate"]}` was fired. The endpoint IGNORED the `props`
key and applied to the WHOLE ACCOUNT, answering `{"count": 30766}` -- records modified, not a
preview. Live count of the `Probate` list afterwards: 0. No other list was touched
(`Register of Wills Probates` stayed at 7,799; Obituary / Owner Occupied / Free & Clear /
Senior Homeowners all intact), and of 49 records compared against a snapshot taken 100 minutes
earlier, 46 had lost EXACTLY `Probate` and nothing else.

THE LESSON, for whoever reads this next: an account-wide route is a WRITE, not a probe. The
per-record route `POST /api/internal/property/{uuid}/remove-lists/ {"lists": [...]}` works and
returns `{"removed_lists": [...]}` -- use it, and never fire a collection-level variant to see
what it does. Note also that OPTIONS returns 404 on these routes even where POST works, so a
404 from OPTIONS is NOT evidence that an endpoint is absent.

HOW THE RESTORE SET IS BUILT. Every local snapshot that recorded per-record `lists` is read,
and for each uuid the LATEST observation wins -- so a record deliberately taken off the list
after an older snapshot is NOT re-added (2 such records were excluded):

    2026-08-22  output/_archive/live_account_pull.json         26,643 records
    2026-09-01  output/live_account_pull_20260901.json         28,577 records
    2026-09-15  output/rel_pr_name_repair/records.json         21,322 records  <- best
    2026-09-15  output/obituary_mail_consolidate/records.json   1,399 records
    2026-09-16  output/dc_portal_account_overlap/idle_audit_*.csv   71 records

Coverage is 30,559 distinct uuids against ~30,766 in the account (99.3%). The ~200 never
observed anywhere cannot be recovered from local data; if any were on the list they stay off,
and that is stated rather than hidden.

THE WRITE. `PATCH /property/{uuid}/ {"lists": ["Probate"]}` is MERGE-ONLY -- proven live before
and after the incident: it ADDS the name and cannot remove the record's other lists. A record
that already has `Probate` is skipped, so the run is resumable and idempotent.

THROUGHPUT. `/api/internal` caps near 2-4 req/s AGGREGATE regardless of worker count, with
escalating Retry-After under sustained load (src/dispo_buyers.py:447, which has already hung
once on compounding 429 backoff). `live_pull.LiveApi` paces PER INSTANCE (MIN_INTERVAL 0.45 =
2.2 req/s each), so the usual per-thread-client pattern would offer ~9 req/s on 4 threads and
buy nothing but 429s. The pacer here is therefore process-global: worker count buys latency
hiding, never offered load. `--rate` refuses anything above 3.0 req/s.

THROTTLE VS FAILURE. The base client swallows an exhausted 429 into status 0, and the first
version of this script collapsed every non-200 read into `READBACK_FAILED` -- so a throttled
read-back was indistinguishable from a write that did not apply, on a record that actually
succeeded. Transient statuses (0 / 429 / 5xx) now retry at the RECORD level and, if still
unresolved, log as *_THROTTLED and are NOT banked: --resume re-checks them, and the PATCH being
idempotent means a re-check costs one ALREADY.

Modes: --plan (default) | --probe | --commit [--limit N] | --verify | --resume
"""
from __future__ import annotations
import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src" / "scripts"))
import config  # noqa: E402,F401
from dpd_lane_phone_score import Api as _Api  # noqa: E402

TS = datetime.now().strftime("%Y%m%dT%H%M%S")

# ---------------------------------------------------------------- paced client
MAX_RATE = 3.0                    # req/s aggregate; Basem's ceiling, not a suggestion
_PACE_LOCK = threading.Lock()
_PACE = {"last": 0.0, "interval": 1.0 / MAX_RATE, "calls": 0}


class Api(_Api):
    """dpd_lane_phone_score.Api (retrying mint + write verb) with a GLOBAL pacer.

    The base `_throttle` is per-instance, so N per-thread clients offer N x 2.2 req/s.
    This one serialises every call in the process behind one interval, so --threads
    changes only how much of the interval is spent waiting on latency instead of idle.
    """

    def _throttle(self):
        while True:
            with _PACE_LOCK:
                now = time.monotonic()
                gap = now - _PACE["last"]
                if gap >= _PACE["interval"]:
                    _PACE["last"] = now
                    _PACE["calls"] += 1
                    return
                wait = _PACE["interval"] - gap
            time.sleep(wait)          # never hold the lock while sleeping


_LOCAL = threading.local()


def thread_api():
    if getattr(_LOCAL, "api", None) is None:
        _LOCAL.api = Api()
    return _LOCAL.api


def transient(status) -> bool:
    """0 = retries exhausted / network, 429 = throttled, 5xx = server. None of these
    is evidence about whether the write applied."""
    return status == 0 or status == 429 or (isinstance(status, int) and status >= 500)


LIST_NAME = "Probate"
LIST_UUID = "67859e87-65bd-44b7-8574-f78e7fc24b83"
OUT = REPO / "output"
SET_FILE = OUT / "probate_list_restore_uuids.json"
DONE_FILE = OUT / "probate_list_restore_done.json"
LOG_FILE = OUT / ("probate_list_restore_log_" + TS + ".jsonl")


def load_set() -> list[str]:
    if not SET_FILE.exists():
        sys.exit("missing " + str(SET_FILE) + " -- build it first (see the module docstring)")
    return json.loads(SET_FILE.read_text(encoding="utf-8"))


def load_done() -> set[str]:
    if DONE_FILE.exists():
        return set(json.loads(DONE_FILE.read_text(encoding="utf-8")))
    return set()


def save_done(done: set[str]) -> None:
    DONE_FILE.write_text(json.dumps(sorted(done)), encoding="utf-8")


def list_count(api) -> int:
    st, b = api.post_as_get("/api/internal/property/",
                            {"limit": 1, "offset": 0,
                             "query": {"must": {"any_lists": [LIST_UUID]}}})
    return b.get("count") if st == 200 and isinstance(b, dict) else -1


def lists_of(api, uuid):
    """(status, lists). The status SURVIVES to the caller: collapsing it into None is
    what made a throttled read look like a write that did not apply."""
    st, b = api.get("/api/internal/property/" + uuid + "/")
    if st != 200:
        return st, None
    return 200, [l.get("title") if isinstance(l, dict) else l
                 for l in (b.get("lists") or [])]


def read_lists(api, uuid, tries=4):
    """Record-level retry on TRANSIENT status only, on top of the client's own.

    The client already burns its attempts inside one call; this outer layer waits
    longer (5/10/20s) for a throttle that outlives them, so the record ends up
    classified rather than guessed at."""
    st, ls = 0, None
    for attempt in range(tries):
        st, ls = lists_of(api, uuid)
        if st == 200 or not transient(st):
            return st, ls
        if attempt < tries - 1:
            time.sleep(5 * (2 ** attempt))
    return st, ls


EXPECTED_FILE = OUT / "probate_list_expected.json"
_EXPECTED = None


def expected_lists(uuid):
    """The record's lists as of the most recent local snapshot (09-15 for most).

    This stands in for the pre-read's before-state. It is an INDEPENDENT reference
    rather than a value read a half-second earlier, so checking `expected subset of
    after` still detects a write that dropped a list -- the one guarantee the pre-read
    was carrying. It can produce a false alarm if a list was added or removed on the
    account after the snapshot, so a flag here means LOOK, not necessarily damage.
    """
    global _EXPECTED
    if _EXPECTED is None:
        _EXPECTED = json.loads(EXPECTED_FILE.read_text(encoding="utf-8")) \
            if EXPECTED_FILE.exists() else {}
    return set(_EXPECTED.get(uuid) or [])


def restore_one_fast(api, uuid):
    """Two calls: PATCH, then read back. No pre-read.

    Safe to fire blind because PATCH {"lists": [...]} is merge-only and idempotent --
    re-adding a name the record already has is a no-op, so the skip the pre-read bought
    was an efficiency, not a safety device. The read-back is KEPT: this API has form for
    answering 200 while storing nothing, so a status code is not proof.
    """
    st, _ = api.write("PATCH", "/api/internal/property/" + uuid + "/",
                      {"lists": [LIST_NAME]}, tries=5)
    if st == 404:
        return "GONE", None, None
    if st != 200:
        # A throttled/failed PATCH says nothing about the record; a real 4xx does.
        return ("PATCH_THROTTLED" if transient(st) else "PATCH_" + str(st)), None, None
    rst, after = read_lists(api, uuid)
    if after is None:
        if rst == 404:
            return "GONE", None, None
        return ("READBACK_THROTTLED" if transient(rst)
                else "READBACK_" + str(rst)), None, None
    if LIST_NAME not in after:
        return "NOT_APPLIED", None, after
    exp = expected_lists(uuid)
    if exp and not exp.issubset(set(after)):
        return "MISSING_EXPECTED", sorted(exp), after
    return "OK", None, after


def restore_one(api, uuid):
    """Returns (outcome, before, after). Never removes: PATCH lists is merge-only."""
    bst, before = read_lists(api, uuid)
    if before is None:
        if bst == 404:
            return "GONE", None, None
        return ("GET_THROTTLED" if transient(bst) else "GET_" + str(bst)), None, None
    if LIST_NAME in before:
        return "ALREADY", before, before
    st, _ = api.write("PATCH", "/api/internal/property/" + uuid + "/",
                      {"lists": [LIST_NAME]}, tries=5)
    if st == 404:
        return "GONE", before, None
    if st != 200:
        return ("PATCH_THROTTLED" if transient(st) else "PATCH_" + str(st)), before, None
    rst, after = read_lists(api, uuid)
    if after is None:
        return ("READBACK_THROTTLED" if transient(rst)
                else "READBACK_" + str(rst)), before, None
    if LIST_NAME not in after:
        return "NOT_APPLIED", before, after
    if not set(before).issubset(set(after)):
        return "LOST_A_LIST", before, after
    return "OK", before, after


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-pre-read", action="store_true",
                    help="2 calls/record instead of 3: PATCH then read back, no before-GET. "
                         "Loss is still detected, against the snapshot's expected lists.")
    ap.add_argument("--threads", type=int, default=4,
                    help="workers (default 4). The pacer is global, so this hides latency "
                         "rather than raising offered load.")
    ap.add_argument("--rate", type=float, default=MAX_RATE,
                    help="req/s AGGREGATE (default %.1f). Values above %.1f are refused."
                         % (MAX_RATE, MAX_RATE))
    ap.add_argument("--max-consecutive-failures", type=int, default=8)
    a = ap.parse_args()

    if a.rate > MAX_RATE:
        sys.exit("--rate %.2f exceeds the %.1f req/s ceiling: /api/internal caps near 2-4 req/s "
                 "aggregate and answers sustained load with escalating Retry-After."
                 % (a.rate, MAX_RATE))
    if a.rate <= 0:
        sys.exit("--rate must be positive")
    _PACE["interval"] = 1.0 / a.rate

    uuids = load_set()
    done = load_done() if (a.resume or a.commit) else set()
    todo = [u for u in uuids if u not in done]

    if not (a.probe or a.commit or a.verify):
        print("PLAN -- writes nothing.")
        print("  restore set : " + str(len(uuids)) + " records")
        print("  already done: " + str(len(done)))
        print("  to write    : " + str(len(todo)))
        print("  write       : PATCH {\"lists\": [\"" + LIST_NAME + "\"]} (merge-only, additive)")
        print("  pacing      : %.2f req/s aggregate, %d workers, %d calls/record -> ~%d rec/min"
              % (a.rate, a.threads, 2 if a.no_pre_read else 3,
                 a.rate * 60 / (2 if a.no_pre_read else 3)))
        api = Api()
        print("  live count of the " + LIST_NAME + " list right now: " + str(list_count(api)))
        return

    api = Api()

    if a.verify:
        n = list_count(api)
        print("live count of the " + LIST_NAME + " list: " + str(n))
        print("restore set size: " + str(len(uuids)) + "  | recorded done: " + str(len(done)))
        miss = []
        import random
        sample = random.sample(uuids, min(40, len(uuids)))
        for u in sample:
            _st, ls = read_lists(api, u)
            if ls is None or LIST_NAME not in ls:
                miss.append(u)
        print("spot-check " + str(len(sample)) + " records: "
              + str(len(sample) - len(miss)) + " carry the list, " + str(len(miss)) + " do not")
        for u in miss[:10]:
            print("   MISSING " + u)
        return

    batch = todo[:1] if a.probe else (todo[:a.limit] if a.limit else todo)
    workers = 1 if a.probe else max(1, a.threads)
    print(("PROBE: 1 record" if a.probe
           else "COMMIT: %d records, %d workers, %.2f req/s aggregate"
                % (len(batch), workers, a.rate)), flush=True)

    expected_lists(batch[0] if batch else "")   # preload before the pool: no lazy-load race
    counts = {}
    banked = ("OK", "ALREADY", "GONE")   # GONE can never succeed; retrying it forever is waste
    abort = threading.Event()
    state = {"consecutive": 0, "i": 0}
    lock = threading.Lock()
    t0 = time.time()

    with open(LOG_FILE, "a", encoding="utf-8") as log:

        def work(u):
            if abort.is_set():
                return "SKIPPED", None, None
            try:
                api_t = thread_api()
                outcome, before, after = (restore_one_fast(api_t, u) if a.no_pre_read
                                          else restore_one(api_t, u))
            except Exception as exc:                                  # noqa: BLE001
                # One stray exception must not kill a 90-minute run; 8 in a row still
                # trips the abort below.
                outcome, before, after = "EXC_" + str(exc)[:60], None, None
            with lock:
                counts[outcome] = counts.get(outcome, 0) + 1
                log.write(json.dumps({"uuid": u, "outcome": outcome,
                                      "before": before, "after": after}) + "\n")
                # Flush EVERY record. A 60s trial wrote ~32 records and logged NONE of
                # them -- the buffer died with the process. On a run this long the log is
                # the only evidence of what was touched, so it must never sit in a buffer.
                log.flush()
                if outcome in banked:
                    done.add(u)
                    state["consecutive"] = 0
                else:
                    print("   " + outcome + " " + u + " before=" + str(before), flush=True)
                    state["consecutive"] += 1
                    if state["consecutive"] >= a.max_consecutive_failures:
                        # Something systemic (dead token, dead endpoint, hard throttle).
                        # Grinding through thousands of them helps nobody.
                        abort.set()
                        print("ABORT: %d consecutive non-success outcomes"
                              % state["consecutive"], flush=True)
                state["i"] += 1
                i = state["i"]
                if i % 20 == 0:
                    save_done(done)
                    el = max(time.time() - t0, 1)
                    rpm = i / el * 60
                    print("  %d/%d  %s  %.2f req/s  %.0f rec/min  ~%dm left"
                          % (i, len(batch), counts, _PACE["calls"] / el, rpm,
                             int((len(batch) - i) / max(rpm, 0.01))), flush=True)
            return outcome, before, after

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, batch))

    save_done(done)
    el = max(time.time() - t0, 1)
    print("")
    print("outcomes: " + str(counts))
    print("measured: %.2f req/s, %.0f rec/min over %.1f min"
          % (_PACE["calls"] / el, state["i"] / el * 60, el / 60))
    print("log: " + str(LOG_FILE))
    print("live count of the " + LIST_NAME + " list now: " + str(list_count(api)))
    if abort.is_set():
        sys.exit("aborted on sustained failures -- nothing further was attempted; "
                 "re-run with --resume once the cause is understood")


if __name__ == "__main__":
    main()
