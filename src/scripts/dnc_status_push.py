"""Write DNC phone statuses onto DataSift from a dnc_review.csv. WRITES -- on Basem's go only.

    python src/scripts/dnc_status_push.py --review <dir>/dnc_review.csv             # dry run (default)
    python src/scripts/dnc_status_push.py --review <dir>/dnc_review.csv --probe     # 1 record, read back
    python src/scripts/dnc_status_push.py --review <dir>/dnc_review.csv --commit    # the rest

For each record with a `dnc` row: read the record, rebuild the owner's FULL phone list
(type, tags, is_connected kept as stored; only the DNC numbers change status:
CORRECT -> CORRECT_DNC, WRONG -> WRONG_DNC, else DNC, as sms_agent.crm.DNC_FOR), upsert
it in ONE call per owner, then read back: every DNC number must carry its new status,
and every phone's tags must be unchanged.

Rows need a record UUID in `key` (dpd_phone_validate dial sheets carry one). DP dial
sheets key on `street|zip`; those rows are listed as "no uuid" and skipped.

Per-record route only (owner/{uuid}/upsert-phones/). Never a collection route.
Log: <review dir>/dnc_status_push_<ts>.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import dnc_client  # noqa: E402

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Kept in step with sms_agent.crm.DNC_FOR (not imported: that module pulls in the
# SMS agent's CRM client config).
DNC_FOR = {"CORRECT": "CORRECT_DNC", "WRONG": "WRONG_DNC",
           "WRONG_DNC": "WRONG_DNC", "CORRECT_DNC": "CORRECT_DNC"}


def say(msg: str) -> None:
    print(msg, flush=True)


def dnc_status_for(current: str | None) -> str:
    return DNC_FOR.get((current or "UNKNOWN").upper(), "DNC")


def tag_names(tags) -> list[str]:
    return [t for t in ((x.get("name") or x.get("title")) if isinstance(x, dict) else str(x)
                        for x in (tags or [])) if t]


def load_targets(review: Path) -> tuple[dict[str, set[str]], list[dict]]:
    """uuid -> DNC numbers; plus the dnc rows that carry no uuid."""
    targets: dict[str, set[str]] = defaultdict(set)
    no_uuid = []
    with open(review, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("dnc_status") != "dnc":
                continue
            if UUID_RE.match(r.get("key") or ""):
                targets[r["key"].lower()].add(r["number"])
            else:
                no_uuid.append(r)
    return targets, no_uuid


def plan_phones(live: list[dict], dnc_numbers: set[str]) -> tuple[list[dict], list[str]]:
    """The full upsert list and the numbers whose status changes."""
    payload, changed = [], []
    for ph in live:
        n = dnc_client.clean_number(ph.get("number"))
        if not n:
            continue
        status = (ph.get("status") or "UNKNOWN").upper()
        if n in dnc_numbers and "DNC" not in status:
            status = dnc_status_for(status)
            changed.append(n)
        payload.append({"number": ph.get("number"), "type": ph.get("type") or "UNKNOWN",
                        "tags": tag_names(ph.get("tags")), "status": status,
                        "is_connected": ph.get("is_connected", True),
                        "verified": ph.get("verified", False)})
    return payload, changed


def owner_phones(full: dict) -> list[dict]:
    return [p for p in ((full.get("owner") or {}).get("phones") or []) if isinstance(p, dict)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--review", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--probe", action="store_true", help="write ONE record and read it back")
    g.add_argument("--commit", action="store_true", help="write every record")
    a = ap.parse_args(argv)

    review = Path(a.review)
    targets, no_uuid = load_targets(review)
    say(f"{len(targets)} record(s) with DNC numbers; {len(no_uuid)} dnc row(s) without a record uuid")
    for r in no_uuid[:20]:
        say(f"  no uuid: {r.get('key')}  {r.get('number')}")
    if not targets:
        return 0

    from dpd_lane_phone_score import Api  # noqa: E402  (live client; only when needed)
    api = Api()
    write = a.probe or a.commit
    log, bad = [], 0
    for i, (uuid, nums) in enumerate(sorted(targets.items())):
        if a.probe and i >= 1:
            break
        st, full = api.get(f"/api/internal/property/{uuid}/")
        if st != 200:
            log.append({"uuid": uuid, "error": f"detail read {st} (404 can be a MERGE: re-resolve by address)"})
            bad += 1
            continue
        owner_uuid = (full.get("owner") or {}).get("uuid")
        live = owner_phones(full)
        payload, changed = plan_phones(live, nums)
        entry = {"uuid": uuid, "owner": owner_uuid, "change": changed,
                 "not_on_record": sorted(nums - {dnc_client.clean_number(p.get("number")) for p in live})}
        if not changed:
            entry["skipped"] = "already DNC or not on the record"
        elif not write:
            entry["dry_run"] = True
        else:
            before_tags = {dnc_client.clean_number(p.get("number")): sorted(tag_names(p.get("tags"))) for p in live}
            st, resp = api.write("POST", f"/api/internal/owner/{owner_uuid}/upsert-phones/", {"phones": payload})
            entry["upsert"] = st
            if st not in (200, 201, 204):
                entry["error"] = f"upsert-phones {st}: {str(resp)[:150]}"
            else:
                st2, after = api.get(f"/api/internal/property/{uuid}/")
                got = {dnc_client.clean_number(p.get("number")): p for p in owner_phones(after or {})}
                fails = [n for n in changed if "DNC" not in (got.get(n, {}).get("status") or "").upper()]
                fails += [f"tags changed on {n}" for n, t in before_tags.items()
                          if n in got and sorted(tag_names(got[n].get("tags"))) != t]
                entry["verified"] = not fails
                if fails:
                    entry["error"] = "read-back: " + "; ".join(fails)
        bad += bool(entry.get("error"))
        log.append(entry)
        say(f"  {uuid}  change {len(changed)}  {entry.get('error') or entry.get('skipped') or ('dry run' if not write else 'verified')}")

    out = review.parent / f"dnc_status_push_{datetime.now():%Y%m%dT%H%M%S}.json"
    out.write_text(json.dumps({"mode": "commit" if a.commit else "probe" if a.probe else "dry_run",
                               "records": log, "no_uuid": no_uuid}, indent=1), encoding="utf-8")
    say(f"log: {out}")
    say(f"VERDICT: {len(log)} record(s), {bad} error(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
