"""Which paid Tracerfy hits never reached a DP run? (READ-ONLY, no spend)

Until ad65905 every Tracerfy poller took the queue route's FIRST list as the result, but
that route answers [] / a partial list while the job is still running, so billed hits
could be dropped silently. This downloads every finished queue on the account (free:
re-reading a queue is not a trace) and matches each hit -- first+last name AND house
number, the address the person was traced at -- against every DP run's
ranked_records.json:

    RECORDED   the relative holds at least one of the hit's numbers
    OTHER      the relative has phones, none of them this hit's (another source won)
    MISSED     the relative is still phoneless -> the hit was lost
    NOT_DP     no DP relative matches (enricher / dispo / skip-tracer / ad-hoc jobs)

MISSED rows also say whether the record was pushed (push_log.jsonl), i.e. whether the
account is missing the numbers too.

    python src/scripts/tracerfy_queue_audit.py                 # fetch (cached) + report
    python src/scripts/tracerfy_queue_audit.py --no-fetch      # report from the cache only

Writes output/tracerfy_queue_audit/{queues/<id>.json, hits.csv, summary.md}.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import requests  # noqa: E402

import obituary_dp_run as dpr  # noqa: E402
import tracerfy_queue as tq  # noqa: E402

OUT = ROOT / "output" / "tracerfy_queue_audit"
SKIP_DIR_WORDS = ("backup", "correction", "prestrip", "_fix_")


def house_no(street: str) -> str:
    m = re.match(r"\s*(\d+)", street or "")
    return m.group(1) if m else ""


def fetch_all(api_key: str) -> list[dict]:
    qdir = OUT / "queues"
    qdir.mkdir(parents=True, exist_ok=True)
    r = requests.get(tq.QUEUES_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
    r.raise_for_status()
    listing = r.json()
    if not isinstance(listing, list):
        raise SystemExit(f"queue listing not a list: {str(listing)[:200]}")
    (OUT / "listing.json").write_text(json.dumps(listing, indent=1), encoding="utf-8")
    for q in listing:
        p = qdir / f"{q['id']}.json"
        if q.get("pending") or p.exists():
            continue
        for _try in range(5):
            d = requests.get(f"{tq.QUEUE_URL}{q['id']}",
                             headers={"Authorization": f"Bearer {api_key}"}, timeout=30).json()
            if isinstance(d, dict) and "error" in d:
                time.sleep(21)
                continue
            break
        recs = d if isinstance(d, list) else d.get("records", [])
        p.write_text(json.dumps(recs, indent=1), encoding="utf-8")
        print(f"  queue {q['id']}: {len(recs)} rows")
        time.sleep(2)
    return listing


def run_dirs() -> list[Path]:
    out = []
    for p in (ROOT / "output").rglob("ranked_records.json"):
        rel = str(p.parent.relative_to(ROOT / "output")).lower()
        if any(w in rel for w in SKIP_DIR_WORDS):
            continue
        out.append(p.parent)
    return sorted(out)


def pushed_keys(run: Path) -> set[str]:
    keys = set()
    log = run / "push_log.jsonl"
    if not log.exists():
        return keys
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("key") and row.get("commit"):
            keys.add(row["key"])
    return keys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()

    listing = (json.loads((OUT / "listing.json").read_text(encoding="utf-8")) if a.no_fetch
               else fetch_all(os.environ.get("TRACERFY_API_KEY", "")))
    created = {str(q["id"]): q.get("created_at", "")[:16] for q in listing}

    # index every DP relative (and subject) by (first last, house number)
    index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    pushed = {}
    for run in run_dirs():
        name = str(run.relative_to(ROOT / "output"))
        pushed[name] = pushed_keys(run)
        ranked = json.loads((run / "ranked_records.json").read_text(encoding="utf-8"))
        for key, rk in ranked.items():
            people = list(rk.get("rels") or [])
            if rk.get("subject"):
                people.append({**rk["subject"], "n": 0})
            for r in people:
                nm = f"{r.get('first', '')} {r.get('last', '')}".strip().lower()
                if not nm:
                    continue
                index[(nm, house_no(r.get("mailing_street", "")))].append({
                    "run": name, "key": key, "n": r.get("n"), "is_dm": bool(r.get("is_dm")),
                    "phones": {re.sub(r"\D", "", p.get("number", "") if isinstance(p, dict) else str(p))[-10:]
                               for p in r.get("phones") or []} - {""}})

    rows, per_queue = [], defaultdict(Counter)
    for p in sorted((OUT / "queues").glob("*.json")):
        qid = p.stem
        for rec in json.loads(p.read_text(encoding="utf-8")):
            for nm, h in dpr.tracerfy_parse_records([rec]).items():
                if not h["numbers"]:
                    continue
                matches = index.get((nm, house_no(h["address"]))) or []
                if not matches:
                    per_queue[qid]["NOT_DP"] += 1
                    rows.append({"queue": qid, "created": created.get(qid, ""), "name": nm,
                                 "address": h["address"], "numbers": " ".join(h["numbers"]),
                                 "status": "NOT_DP"})
                    continue
                for m in matches:
                    hit = set(h["numbers"])
                    st = ("RECORDED" if m["phones"] & hit else "OTHER" if m["phones"] else "MISSED")
                    per_queue[qid][st] += 1
                    rows.append({"queue": qid, "created": created.get(qid, ""), "name": nm,
                                 "address": h["address"], "numbers": " ".join(h["numbers"]),
                                 "status": st, "run": m["run"], "key": m["key"], "rel_n": m["n"],
                                 "is_dm": m["is_dm"],
                                 "pushed": m["key"] in pushed.get(m["run"], set())})

    OUT.mkdir(parents=True, exist_ok=True)
    cols = ["queue", "created", "status", "run", "key", "rel_n", "is_dm", "pushed", "name",
            "address", "numbers"]
    with open(OUT / "hits.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["queue"], r["status"])))

    tot = Counter(r["status"] for r in rows)
    missed = [r for r in rows if r["status"] == "MISSED"]
    by_run = Counter((r["run"], r["pushed"]) for r in missed)
    md = ["# Tracerfy queue audit", "",
          f"{len(listing)} queues, {sum(q.get('credits_deducted') or 0 for q in listing)} billed hits.",
          "", "| status | hits |", "|---|---|"]
    md += [f"| {k} | {v} |" for k, v in tot.most_common()]
    md += ["", "## Per queue", "", "| queue | created | uploaded | billed | RECORDED | OTHER | MISSED | NOT_DP |",
           "|---|---|---|---|---|---|---|---|"]
    for q in listing:
        c = per_queue.get(str(q["id"]), Counter())
        md.append(f"| {q['id']} | {q.get('created_at', '')[:16]} | {q.get('rows_uploaded')} | "
                  f"{q.get('credits_deducted')} | {c['RECORDED']} | {c['OTHER']} | {c['MISSED']} | {c['NOT_DP']} |")
    md += ["", "## MISSED by run", "", "| run | pushed to account | hits | signers |", "|---|---|---|---|"]
    for (run, was_pushed), n in sorted(by_run.items()):
        dm = sum(1 for r in missed if r["run"] == run and r["pushed"] == was_pushed and r["is_dm"])
        md.append(f"| {run} | {'yes' if was_pushed else 'no'} | {n} | {dm} |")
    (OUT / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
