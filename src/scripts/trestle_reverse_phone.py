"""Trestle Reverse Phone (3.2) on the unique numbers of chosen "Unknown <Surname>" ghosts.

    GET https://api.trestleiq.com/3.2/phone?phone=<10 digits>      header x-api-key
    ~$0.07 per query (Basem, 2026-09-17). PAID: every call is cached under
    output/unknown_ghosts/trestle_reverse/<phone>.json and never repeated.

Probe authorised by Basem 2026-09-17 on FIVE properties "that are worth it and would give a
person to call". Selection lives in the caller (--keys); the script itself refuses to run
without --commit and stops per ghost as soon as one query returns a holder with the ghost's
surname (mobiles first), so a 6-number ghost usually costs one or two queries.

    python src/scripts/trestle_reverse_phone.py --keys "2148glencourseln|20191" ... --commit
    python src/scripts/trestle_reverse_phone.py --keys ... --max-per-ghost 3 --commit

Writes output/unknown_ghosts/trestle_reverse_probe.csv (one row per query) and prints, per
ghost, the holder Trestle names and whether surname + age fit the ghost.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]
load_dotenv(ROOT / ".env")

from obituary_dp_batch import names_match  # noqa: E402
from dp_ghost_name_resolver import OUT, age_int, first_of, jload, same_person, surname_of  # noqa: E402

ENDPOINT = "https://api.trestleiq.com/3.2/phone"
COST = 0.07
CACHE_DIR = OUT / "trestle_reverse"
RESULTS = OUT / "ghost_name_resolution.json"
PROBE_CSV = OUT / "trestle_reverse_probe.csv"
HEADER = ["ghost_name", "ghost_age", "key", "property", "phone", "phone_type_smartskip", "cached",
          "is_valid", "line_type", "carrier", "holders", "surname_match", "age_fit", "age_range",
          "current_address", "error"]


def say(m: str) -> None:
    print(m, flush=True)


def lookup(phone: str, api_key: str, commit: bool) -> tuple[dict, bool]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp = CACHE_DIR / f"{phone}.json"
    if cp.exists():
        return json.loads(cp.read_text(encoding="utf-8")), True
    if not commit:
        return {"dry_run": True}, False
    for attempt in range(3):
        try:
            r = requests.get(ENDPOINT, params={"phone": phone}, headers={"x-api-key": api_key}, timeout=30)
        except requests.RequestException as e:      # noqa: PERF203
            if attempt == 2:
                return {"error": f"request failed: {e}"}, False
            time.sleep(2)
            continue
        if r.status_code == 429 and attempt < 2:
            time.sleep(3)
            continue
        try:
            body = r.json()
        except ValueError:
            body = {"error": f"non-JSON {r.status_code}: {r.text[:200]}"}
        body["_http_status"] = r.status_code
        body["_queried_at"] = datetime.now().isoformat(timespec="seconds")
        if r.status_code == 200:
            cp.write_text(json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8")
        return body, False
    return {"error": "gave up"}, False


def age_fits(age_range: str, ghost_age) -> str:
    """Trestle gives '60-64' style ranges; the SmartSkip age is a point estimate."""
    g = age_int(ghost_age)
    ar = str(age_range or "")
    m = re.match(r"(\d+)\s*-\s*(\d+)", ar)
    if g and not m and re.match(r"(\d+)\+", ar):          # "65+"
        return "yes" if g >= int(ar.split("+")[0]) - 3 else "no"
    if not g or not m:
        return ""
    lo, hi = int(m.group(1)), int(m.group(2))
    return "yes" if lo - 3 <= g <= hi + 3 else "no"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keys", nargs="+", required=True, help="record keys from ghost_name_resolution.json")
    ap.add_argument("--max-per-ghost", type=int, default=6)
    ap.add_argument("--all-numbers", action="store_true", help="do not stop at the first surname hit")
    ap.add_argument("--shared", action="store_true",
                    help="for ghosts with NO unique phone, query the household lines they share with named relatives")
    ap.add_argument("--commit", action="store_true", help="actually call Trestle (bills ~$0.07/query)")
    a = ap.parse_args()

    api_key = os.getenv("TRESTLE_API_KEY")
    if not api_key:
        sys.exit("TRESTLE_API_KEY missing from .env")
    res = jload(RESULTS)
    if not res:
        sys.exit(f"run dp_ghost_name_resolver.py first ({RESULTS} missing)")
    by_key = {r["key"]: r for r in res["records"]}
    missing = [k for k in a.keys if k not in by_key]
    if missing:
        sys.exit(f"keys not in the ghost list: {missing}")

    rows, spent, queries = [], 0.0, 0
    for k in a.keys:
        r = by_key[k]
        surname = surname_of(r["ghost_name"])
        grel = r.get("ghost_rel") or {}
        typed = {re.sub(r"\D", "", str(p.get("number") or ""))[-10:]: (p.get("type") or "") for p in grel.get("phones") or []}
        pool = r["unique_phones"] or (r["shared_phones"] if a.shared else [])
        order = sorted(pool, key=lambda ph: (0 if "mobile" in typed.get(ph, "").lower() else 1, ph))
        if not r["unique_phones"] and a.shared:
            say(f"  (no unique phone -- querying the {len(pool)} shared household lines)")
        say(f"\n{r['ghost_name']} ({r['ghost_age']}) -- {r['property']} -- owner {r['owner_name']} -- {r['verdict']}")
        say(f"  unique numbers, in query order: {', '.join(f'{ph} [{typed.get(ph) or '?'}]' for ph in order)}")
        hit = False
        for ph in order[: a.max_per_ghost]:
            body, cached = lookup(ph, api_key, a.commit)
            if body.get("dry_run"):
                say(f"  {ph}: DRY RUN (would query, $0.07)")
                continue
            if not cached:
                queries += 1
                spent += COST
            holders = body.get("owners") or body.get("belongs_to") or []   # 3.2 says "owners" (measured live)
            names = [h.get("name") or f"{h.get('firstname', '')} {h.get('lastname', '')}".strip() for h in holders]
            sm = [n for n in names if surname_of(n) == surname]
            if not r["unique_phones"]:
                # on a shared line the named relative is EXPECTED; only an unnamed same-surname
                # holder counts as a hit
                known = [n["name"] for n in r["named_relatives_detail"]] + [r["owner_name"]]
                sm = [n for n in sm if not any(same_person(n, k2) or names_match(n, k2) for k2 in known)]
            ages = [h.get("age_range") or "" for h in holders]
            fit = [age_fits(h.get("age_range"), r["ghost_age"]) for h in holders if surname_of(h.get("name") or "") == surname]
            addrs = []
            for h in holders:
                for c0 in (h.get("current_addresses") or [])[:1]:
                    addrs.append(" ".join(str(c0.get(x) or "") for x in ("street_line_1", "city", "state_code", "postal_code")).strip())
            addr = " | ".join(addrs)
            err = body.get("error")
            if isinstance(err, dict):
                err = err.get("message") or json.dumps(err)
            say(f"  {ph} [{typed.get(ph) or '?'}]{' (cached)' if cached else ''}: valid={body.get('is_valid')} "
                f"{body.get('line_type') or ''} {body.get('carrier') or ''} -> "
                f"{'; '.join(f'{n} ({ag})' for n, ag in zip(names, ages)) or 'no holder returned'}"
                f"{'  @ ' + addr if addr else ''}"
                f"{'  <-- SURNAME MATCH, age fit ' + '/'.join(fit) if sm else ''}{'  ERROR ' + str(err) if err else ''}")
            rows.append({"ghost_name": r["ghost_name"], "ghost_age": r["ghost_age"], "key": k, "property": r["property"],
                         "phone": ph, "phone_type_smartskip": typed.get(ph) or "", "cached": cached,
                         "is_valid": body.get("is_valid"), "line_type": body.get("line_type") or "",
                         "carrier": body.get("carrier") or "", "holders": "; ".join(names),
                         "surname_match": "; ".join(sm), "age_fit": "/".join(fit), "age_range": "; ".join(ages),
                         "current_address": addr, "error": err or ""})
            if sm:
                hit = True
                if not a.all_numbers:
                    break
        if not hit and a.commit:
            say("  no same-surname holder on the queried numbers")

    if rows:
        exists = PROBE_CSV.exists()
        with open(PROBE_CSV, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
            if not exists:
                w.writeheader()
            w.writerows(rows)
    say(f"\n{queries} billed queries this run, ~${spent:.2f}; {sum(1 for x in rows if x['cached'])} served from cache"
        + (f"; appended to {PROBE_CSV}" if rows else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
