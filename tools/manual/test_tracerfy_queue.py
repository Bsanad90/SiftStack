"""Offline guard for src/tracerfy_queue.py -- no network, no spend.

Replays the 2026-09-29/30 failure: while a job runs, GET /queue/{id} answers [] and then a
partial list, and /queues/ answers a rate-limit dict when called within 20 s. A poller must
return ONLY the finished list. Run after ANY change to tracerfy_queue or its callers.

    python tools/manual/test_tracerfy_queue.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import tracerfy_queue as tq  # noqa: E402

FAILS = 0


def check(label, cond):
    global FAILS
    print(("ok   " if cond else "FAIL ") + label)
    FAILS += 0 if cond else 1


class Resp:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def fake(listing_seq, queue_seq):
    """requests.get stand-in: each URL pops the next answer (the last one repeats)."""
    calls = {"listing": 0, "queue": 0}

    def get(url, headers=None, timeout=None):
        if url.startswith(tq.QUEUES_URL):
            i = min(calls["listing"], len(listing_seq) - 1)
            calls["listing"] += 1
            return Resp(listing_seq[i])
        i = min(calls["queue"], len(queue_seq) - 1)
        calls["queue"] += 1
        return Resp(queue_seq[i])
    return get, calls


def run(listing_seq, queue_seq, max_wait_s=300):
    get, calls = fake(listing_seq, queue_seq)
    tq.requests.get = get
    tq.time.sleep = lambda s: None
    return tq.wait_for_queue(7, "k", poll_s=25, max_wait_s=max_wait_s), calls


PENDING = [{"id": 7, "pending": True}]
DONE = [{"id": 7, "pending": False}]
RATE = {"error": "Rate limit exceeded. Retry in intervals of 20 seconds."}
FULL = [{"first_name": f"P{i}", "last_name": "X", "mobile_1": "7035551212"} for i in range(5)]

# 1. The bug: [] while pending must NOT be returned.
recs, calls = run([PENDING, PENDING, DONE], [FULL])
check("empty/pending list is not taken as the result", recs == FULL)
check("records fetched only after the listing said done", calls["queue"] == 1)

# 2. The queue route is never read while pending (flag first, records after), so a
#    partial list can never be taken for the whole result.
recs, calls = run([PENDING, PENDING, DONE], [FULL])
check("no queue fetch while pending", calls["queue"] == 1 and recs == FULL)

# 3. A rate-limited listing is 'unknown', not 'done'.
recs, calls = run([RATE, RATE, DONE], [FULL])
check("rate-limit answer keeps waiting", recs == FULL and calls["listing"] == 3)

# 4. Id missing from the listing (unknown) never finishes -> timeout returns None.
recs, _ = run([[{"id": 99, "pending": False}]], [FULL], max_wait_s=100)
check("unlisted id times out to None, not []", recs is None)

# 5. A job that finished with no hits returns [] (a real zero, distinguishable from None).
recs, _ = run([DONE], [[]])
check("finished-empty job returns []", recs == [])

# 6. Status-wrapper shapes.
recs, _ = run([DONE], [{"status": "completed", "records": FULL}])
check("status wrapper with records returned", recs == FULL)
try:
    run([DONE], [{"status": "failed"}])
    check("failed job raises", False)
except RuntimeError:
    check("failed job raises", True)

# 7. Rate limit on the queue route after 'done' keeps waiting rather than returning it.
recs, _ = run([DONE], [RATE, FULL])
check("rate-limited queue fetch retried", recs == FULL)

# 8. fetch_queue refuses while pending and returns records when done.
get, _ = fake([PENDING], [FULL]); tq.requests.get = get
check("fetch_queue None while pending", tq.fetch_queue(7, "k") is None)
get, _ = fake([DONE], [FULL]); tq.requests.get = get
check("fetch_queue returns finished records", tq.fetch_queue(7, "k") == FULL)

# 9. End to end through the DP batch gap-fill (the path that read 0/453).
import obituary_dp_run as dpr  # noqa: E402

get, calls = fake([PENDING, PENDING, DONE], [FULL])
tq.requests.get = get            # same `requests` module object gapfill imports
tq.requests.post = lambda *a, **k: Resp({"queue_id": 7})
dms = [{"first": f"P{i}", "last": "X", "mailing_street": "1 A St", "phones": []} for i in range(5)]
found, stats = dpr.tracerfy_gapfill(dms, "k", poll_s=25, max_polls=10)
check("gapfill fills 5/5 once the job finishes", stats.get("filled") == 5 and len(found) == 5)
check("gapfill stats carry the queue id", stats.get("queue_id") == 7)

print(f"\n{'ALL PASS' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
