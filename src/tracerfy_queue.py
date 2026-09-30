"""Wait for a Tracerfy batch queue to FINISH, then return its records.

`GET /v1/api/queue/{id}` answers a LIST while the job is still running: [] first, then a
growing partial list. Every poller in this repo used to take the first list as the result,
so a batch read as "0 matched" (or a fraction) while Tracerfy billed every hit -- measured
2026-09-29/30: rank gap-fill kept 1 of 29 billed hits, augment 0 of 111. The only
completion signal is the `pending` flag on the `/v1/api/queues/` listing, and that listing
allows ONE call per 20 seconds (a faster call answers {"error": "Rate limit exceeded..."}).
"""
from __future__ import annotations

import time

import requests

QUEUE_URL = "https://tracerfy.com/v1/api/queue/"
QUEUES_URL = "https://tracerfy.com/v1/api/queues/"


def queue_pending(qid, api_key):
    """True/False from the /queues/ listing's `pending` flag; None when unknown
    (not listed, rate limited, or a transport error)."""
    try:
        r = requests.get(QUEUES_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
        r.raise_for_status()
        d = r.json()
    except (requests.RequestException, ValueError):
        return None
    items = d if isinstance(d, list) else d.get("results", [])
    for q in items:
        if isinstance(q, dict) and str(q.get("id")) == str(qid):
            return bool(q.get("pending"))
    return None


def fetch_queue(qid, api_key):
    """Records of a FINISHED queue (no re-trace, no spend); None while it is not known done."""
    if queue_pending(qid, api_key) is not False:
        return None
    r = requests.get(f"{QUEUE_URL}{qid}", headers={"Authorization": f"Bearer {api_key}"},
                     timeout=30)
    r.raise_for_status()
    d = r.json()
    return d if isinstance(d, list) else d.get("records", [])


def wait_for_queue(qid, api_key, poll_s=25, max_wait_s=900, on_poll=None):
    """Poll until the queue is finished, then return its records (possibly []).

    Order matters: the `pending` flag is read FIRST and the records fetched AFTER, so a
    list fetched moments before completion can never be taken for the whole result.
    Raises RuntimeError("failed") for a failed job. Returns None on timeout -- the job is
    still paid for, so callers should log `qid`; `fetch_queue` recovers it later.
    """
    waited = 0
    while waited < max_wait_s:
        time.sleep(poll_s)
        waited += poll_s
        if on_poll:
            on_poll(waited)
        if queue_pending(qid, api_key) is not False:
            continue
        try:
            r = requests.get(f"{QUEUE_URL}{qid}", headers={"Authorization": f"Bearer {api_key}"},
                             timeout=30)
            r.raise_for_status()
            d = r.json()
        except (requests.RequestException, ValueError):
            continue
        if isinstance(d, list):
            return d
        if isinstance(d, dict):
            if d.get("status") == "failed":
                raise RuntimeError("failed")
            if "records" in d:
                return d.get("records") or []
    return None
