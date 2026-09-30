"""Recover a DP run's Tracerfy phones from queues that were paid for but read too early.

`obituary_dp_run.tracerfy_gapfill` used to take the queue route's first list as the
result, but that route answers [] (then a growing partial list) while the job is still
running. On 2026-09-29/30 that froze `rank --tracerfy` at 1/56 filled and `augment` at
0/453 while Tracerfy billed the hits. This fetches the FINISHED queues (no re-trace, no
spend) and fills phoneless relatives in ranked_records.json, matched on first+last name
AND house number (the gap-fill traced each person at a known address, so a same-name hit
at another house is not them).

    python src/scripts/dp_tracerfy_recover.py --run-dir <run> --queue 181928 --queue 182474          # dry run
    python src/scripts/dp_tracerfy_recover.py --run-dir <run> --queue 181928 --queue 182474 --commit

Local files only. Writes a backup of ranked_records.json before changing it; re-run
`score` afterwards to tier the new numbers.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import obituary_dp_run as dpr  # noqa: E402


def house_no(street: str) -> str:
    m = re.match(r"\s*(\d+)", street or "")
    return m.group(1) if m else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--queue", action="append", required=True, help="Tracerfy queue id (repeatable)")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    run = Path(a.run_dir)
    api_key = os.environ.get("TRACERFY_API_KEY", "")
    hits: dict[tuple[str, str], dict] = {}
    for i, q in enumerate(a.queue):
        if i:
            time.sleep(21)  # the /queues/ listing allows one call per 20 s
        recs = dpr.tracerfy_fetch_queue(q, api_key)
        if recs is None:
            print(f"queue {q}: still pending (or not listed / rate limited) -- retry in a minute")
            return 2
        parsed = 0
        for rec in recs:
            one = dpr.tracerfy_parse_records([rec])
            for name, h in one.items():
                hits[(name, house_no(h["address"]))] = h
                parsed += 1
        print(f"queue {q}: {len(recs)} rows, {parsed} with a phone or email")

    ranked_p = run / "ranked_records.json"
    ranked = json.loads(ranked_p.read_text(encoding="utf-8"))
    stats = Counter()
    for rk in ranked.values():
        for r in rk["rels"]:
            if r.get("phones"):
                continue
            stats["phoneless"] += 1
            name = f"{r.get('first', '')} {r.get('last', '')}".strip().lower()
            h = hits.get((name, house_no(r.get("mailing_street", ""))))
            if not h or not h["numbers"]:
                continue
            r["phones"] = [{"number": n, "type": h["types"].get(n, "UNKNOWN"), "source": "tracerfy"}
                           for n in h["numbers"]]
            r["tracerfy"] = True
            if h["emails"] and not r.get("emails"):
                r["emails"] = h["emails"]
            stats["filled"] += 1
            stats["numbers"] += len(h["numbers"])
            stats[f"filled_{r.get('source') or 'smartskip_signer'}"] += 1
        rk["n_phones"] = sum(len(r.get("phones") or []) for r in rk["rels"])
    print(dict(stats))
    if not a.commit:
        print("dry run -- nothing written (add --commit)")
        return 0
    bak = run / f"ranked_records_pre_tracerfy_recover_{datetime.now():%Y%m%dT%H%M%S}.json"
    shutil.copy2(ranked_p, bak)
    ranked_p.write_text(json.dumps(ranked, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"ranked_records.json updated (backup {bak.name}). Re-run `score`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
