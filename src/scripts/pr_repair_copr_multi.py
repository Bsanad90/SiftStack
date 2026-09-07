"""Resolve + write the co_pr and multi_estate buckets of the all-cohort PR repair.

The clean/variant buckets were written 2026-09-04 (live-sampled SOUND). These two
buckets were held back because the sheet is UNRELIABLE here: live verification of
the DC-cohort co-PR picks showed the sheet's choice wrong 79% of the time. So the
name that gets written must come from the LIVE Register of Wills, not the sheet.

`resolve` (read-only, resumable):
  For each of the 164 review rows, parse the candidate (name, tab, estate)
  triples, look up every MD estate on the RoW Estate Search (shared cache with
  the 09-04 run: output/dp_1122branch/row_estate_lookup_cache.json), and pick
  the estate that CONNECTS to this record -- a live PR's mailing street equals
  the record street, or the decedent/PR name matches a sheet candidate. Verdicts:
    write_live        one connected estate with a live PR -> write that PR
    write_dc_recency  DC-only candidates (portal not scriptable): highest case
                      number wins, name is sheet-confidence -- flagged
    hold_*            ambiguous / no estate / no live PR / lookup error
  Output: output/pr_repair_allcohorts/copr_multi_plan.csv (review before write).

`write --commit`:
  Writes only write_live (+ write_dc_recency unless --skip-dc) rows through the
  same guard as the main driver: GET live values first, skip if already equal,
  HOLD as conflict if the record changed since the review snapshot, backup
  before PATCH, echo-verified, resumable via its own write log.

Usage:
    python -u src/scripts/pr_repair_copr_multi.py resolve
    python -u src/scripts/pr_repair_copr_multi.py write            # dry summary
    python -u src/scripts/pr_repair_copr_multi.py write --commit
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from md_register_of_wills_pull import (  # noqa: E402
    firecrawl_scrape_retry, fetch_estate_detail, parse_estate_search_grid,
    _set_value_js, COUNTY_ID, ESTATE_SEARCH_URL, _split_pr_name)
from obituary_dp_batch import WriteApi, names_match  # noqa: E402
from pr_repair_allcohorts_write import (  # noqa: E402
    read_live_pr, PR_FIRST_UUID, PR_LAST_UUID, REVIEW_CSV)

RUN = ROOT / "output" / "pr_repair_allcohorts"
CACHE_P = ROOT / "output" / "dp_1122branch" / "row_estate_lookup_cache.json"
PLAN_P = RUN / "copr_multi_plan.csv"

DC_ESTATE_RE = re.compile(r"^\d{4}-[A-Z]{2,5}-?\d*", re.I)
MD_ESTATE_RE = re.compile(r"^[Ww]?\d{4,7}$")
CAND_RE = re.compile(r"([^|\[\]]+?)\s*\[([^\[\]]*)\]")

PLAN_FIELDS = ["uuid", "street", "bucket", "verdict", "new_first", "new_last",
               "estate", "estate_status", "evidence", "candidates",
               "current_pr_first", "current_pr_last", "detail_prs"]


def norm_street(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(s or "").lower()).strip()


def street_matches(a: str, b: str) -> bool:
    na, nb = norm_street(a), norm_street(b)
    if not na or not nb:
        return False
    ta, tb = na.split(), nb.split()
    # house number + first street token must agree; suffix spelling may differ
    return len(ta) >= 2 and len(tb) >= 2 and ta[0] == tb[0] and ta[1] == tb[1]


def parse_candidates(row: dict) -> list[dict]:
    """-> [{name, tab, estate, kind}] from the alternatives column (+ the row's
    own estate/proposed columns when present)."""
    cands = []
    for name, bracket in CAND_RE.findall(row.get("alternatives") or ""):
        name = " ".join(name.split()).strip(" |")
        toks = bracket.split()
        est = ""
        if toks and (MD_ESTATE_RE.match(toks[-1]) or DC_ESTATE_RE.match(toks[-1])):
            est = toks[-1]
            toks = toks[:-1]
        if name:
            cands.append({"name": name, "tab": " ".join(toks), "estate": est})
    pf, pl = (row.get("proposed_first") or "").strip(), (row.get("proposed_last") or "").strip()
    if pf and pl:
        cands.append({"name": f"{pf} {pl}", "tab": row.get("source_tab") or "",
                      "estate": (row.get("estate") or "").strip()})
    # dedupe on (name, estate)
    seen, out = set(), []
    for c in cands:
        k = (c["name"].lower(), c["estate"])
        if k not in seen:
            seen.add(k)
            out.append(c)
    for c in out:
        c["kind"] = ("dc" if DC_ESTATE_RE.match(c["estate"]) else
                     "md" if MD_ESTATE_RE.match(c["estate"]) else "none")
    return out


def lookup_estate(cache: dict, est: str) -> dict:
    est = est.strip()
    if est in cache:
        return cache[est]
    script = _set_value_js("#txtEstateNo", est)
    actions = [
        {"type": "wait", "milliseconds": 2000},
        {"type": "executeJavascript", "script": script},
        {"type": "wait", "milliseconds": 1200},
        {"type": "click", "selector": "#cmdSearch"},
        {"type": "wait", "milliseconds": 4500},
        {"type": "scrape"},
    ]
    try:
        htmls = firecrawl_scrape_retry(ESTATE_SEARCH_URL, actions)
        rows = parse_estate_search_grid(htmls[-1], "")
        entry = {"estate": est, "county_searched": None, "grid_rows": rows,
                 "reasons": ["copr_multi_allcohorts"]}
        if len(rows) == 1 and rows[0].get("record_id"):
            entry["detail"] = fetch_estate_detail(rows[0]["record_id"])
        elif len(rows) > 1:
            for r in rows:
                if r.get("record_id"):
                    entry.setdefault("details", []).append(fetch_estate_detail(r["record_id"]))
    except Exception as e:  # noqa: BLE001
        entry = {"estate": est, "error": str(e)[:300]}
    cache[est] = entry
    json.dump(cache, open(CACHE_P, "w"), indent=1)
    return entry


def details_of(entry: dict) -> list[dict]:
    if entry.get("detail"):
        return [entry["detail"]]
    return entry.get("details") or []


def connects(detail: dict, row: dict, cands: list[dict]) -> str | None:
    """Why this estate belongs to this record, or None."""
    for pr in detail.get("personal_reps") or []:
        if street_matches(pr.get("street"), row["street"]):
            return f"PR street == record street ({pr.get('street')})"
    dec = detail.get("decedent_name") or ""
    for c in cands:
        if dec and names_match(c["name"], dec):
            return f"candidate matches decedent ({dec})"
        for pr in detail.get("personal_reps") or []:
            if names_match(c["name"], pr.get("name") or ""):
                return f"candidate matches live PR ({pr.get('name')})"
    return None


def resolve(rows: list[dict]) -> None:
    cache = json.load(open(CACHE_P, encoding="utf-8")) if CACHE_P.exists() else {}
    plan: list[dict] = []
    md_estates = sorted({c["estate"] for r in rows for c in parse_candidates(r)
                         if c["kind"] == "md"})
    todo = [e for e in md_estates if e not in cache]
    print(f"{len(rows)} rows; {len(md_estates)} unique MD estates ({len(todo)} to fetch, "
          f"{len(md_estates) - len(todo)} cached)")

    for i, r in enumerate(rows, 1):
        cands = parse_candidates(r)
        base = {"uuid": r["uuid"], "street": r["street"], "bucket": r["bucket"],
                "current_pr_first": r["current_pr_first"],
                "current_pr_last": r["current_pr_last"],
                "candidates": " | ".join(f"{c['name']} [{c['estate'] or '-'}]" for c in cands),
                "new_first": "", "new_last": "", "estate": "", "estate_status": "",
                "evidence": "", "detail_prs": ""}
        md = [c for c in cands if c["kind"] == "md"]
        dc = [c for c in cands if c["kind"] == "dc"]
        if not cands:
            plan.append({**base, "verdict": "hold_no_candidates"})
            continue
        if not md and not dc:
            plan.append({**base, "verdict": "hold_no_estate_numbers"})
            continue

        eligible = []  # (estate, detail, why)
        errors = 0
        for est in sorted({c["estate"] for c in md}):
            entry = lookup_estate(cache, est)
            if entry.get("error"):
                errors += 1
                continue
            for d in details_of(entry):
                why = connects(d, r, cands)
                if why:
                    eligible.append((est, d, why))
        if eligible:
            opens = [e for e in eligible if (e[1].get("status") or "").upper() == "OPEN"]
            pool = opens or eligible
            pool.sort(key=lambda e: e[1].get("date_of_filing") or "", reverse=True)
            if len({e[0] for e in pool}) > 1 and len(opens) != 1:
                plan.append({**base, "verdict": "hold_ambiguous",
                             "evidence": "; ".join(f"{e[0]}:{e[2]}" for e in eligible)[:300]})
                continue
            est, d, why = pool[0]
            prs = d.get("personal_reps") or []
            if not prs:
                plan.append({**base, "verdict": "hold_no_live_pr", "estate": est,
                             "estate_status": d.get("status") or "", "evidence": why})
                continue
            first, last = _split_pr_name(" ".join((prs[0].get("name") or "").split()).title())
            plan.append({**base, "verdict": "write_live", "new_first": first,
                         "new_last": last, "estate": est,
                         "estate_status": d.get("status") or "", "evidence": why,
                         "detail_prs": " | ".join(p.get("name") or "" for p in prs)})
            continue
        if md and errors:
            plan.append({**base, "verdict": "hold_lookup_error"})
            continue
        if dc:
            # DC portal is not scriptable headless; recency rule (case numbers are
            # sequential within a year) -- the name is sheet-confidence, flagged.
            dc.sort(key=lambda c: c["estate"], reverse=True)
            pick = dc[0]
            if sum(1 for c in dc if c["estate"] == pick["estate"]) > 1:
                plan.append({**base, "verdict": "hold_dc_ambiguous"})
                continue
            first, last = _split_pr_name(pick["name"])
            plan.append({**base, "verdict": "write_dc_recency", "new_first": first,
                         "new_last": last, "estate": pick["estate"],
                         "evidence": "DC recency pick; sheet-confidence only"})
            continue
        plan.append({**base, "verdict": "hold_no_connection"})
        if i % 20 == 0:
            print(f"  {i}/{len(rows)} resolved", flush=True)

    RUN.mkdir(parents=True, exist_ok=True)
    with open(PLAN_P, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PLAN_FIELDS)
        w.writeheader()
        w.writerows(plan)
    from collections import Counter
    print("verdicts:", dict(Counter(p["verdict"] for p in plan)))
    print("plan ->", PLAN_P)


def write(commit: bool, skip_dc: bool) -> int:
    plan = list(csv.DictReader(open(PLAN_P, encoding="utf-8-sig")))
    verdicts = {"write_live"} | (set() if skip_dc else {"write_dc_recency"})
    todo = [p for p in plan if p["verdict"] in verdicts and p["new_first"] and p["new_last"]]
    print(f"{len(todo)} of {len(plan)} plan rows are writable ({sorted(verdicts)})")
    if not commit:
        print("DRY RUN. Re-run with --commit.")
        return 0

    writelog_p = RUN / "copr_multi_writelog.jsonl"
    done = set()
    if writelog_p.exists():
        for line in open(writelog_p, encoding="utf-8"):
            try:
                e = json.loads(line)
                if e.get("verified"):
                    done.add(e["uuid"])
            except json.JSONDecodeError:
                pass
    api = WriteApi()
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = open(RUN / f"copr_multi_backup_{ts}.jsonl", "a", encoding="utf-8")
    log = open(writelog_p, "a", encoding="utf-8")
    counts = {"written": 0, "already_done": 0, "conflict": 0, "read_fail": 0,
              "write_fail": 0, "resumed": 0}
    holds = []
    try:
        for p in todo:
            if p["uuid"] in done:
                counts["resumed"] += 1
                continue
            st, live_f, live_l = read_live_pr(api, p["uuid"])
            if st != 200:
                counts["read_fail"] += 1
                holds.append({**p, "hold_reason": f"read_{st}"})
                continue
            if live_f == p["new_first"] and live_l == p["new_last"]:
                counts["already_done"] += 1
                log.write(json.dumps({"uuid": p["uuid"], "verified": True,
                                      "note": "already_done"}) + "\n")
                log.flush()
                continue
            snap = ((p["current_pr_first"] or "").strip(), (p["current_pr_last"] or "").strip())
            if (live_f, live_l) != snap and (live_f or live_l):
                counts["conflict"] += 1
                holds.append({**p, "hold_reason": "conflict",
                              "live_first": live_f, "live_last": live_l})
                continue
            backup.write(json.dumps({"uuid": p["uuid"], "street": p["street"],
                                     "prior_first": live_f, "prior_last": live_l,
                                     "new_first": p["new_first"], "new_last": p["new_last"],
                                     "verdict": p["verdict"]}) + "\n")
            backup.flush()
            body = [{"field_uuid": PR_FIRST_UUID, "value": p["new_first"]},
                    {"field_uuid": PR_LAST_UUID, "value": p["new_last"]}]
            wst, resp = api.write(
                "PATCH", f"/api/internal/property/{p['uuid']}/custom-field/update-values/", body)
            got = {}
            if wst == 200 and isinstance(resp, list):
                for row in resp:
                    cf = (row.get("custom_field") or {})
                    got[cf.get("uuid")] = row.get("value")
            good = (wst == 200 and got.get(PR_FIRST_UUID) == p["new_first"]
                    and got.get(PR_LAST_UUID) == p["new_last"])
            counts["written" if good else "write_fail"] += 1
            log.write(json.dumps({**{k: p[k] for k in ("uuid", "street", "verdict",
                                                       "new_first", "new_last", "estate")},
                                  "prior_first": live_f, "prior_last": live_l,
                                  "status": wst, "verified": good}) + "\n")
            log.flush()
            if not good:
                print(f"  FAIL {p['street']}: status {wst} echo {got}")
    finally:
        backup.close()
        log.close()
        if holds:
            hp = RUN / f"copr_multi_holds_{ts}.json"
            json.dump(holds, open(hp, "w", encoding="utf-8"), indent=1)
            print("holds ->", hp)
    print("SUMMARY:", counts)
    return 0 if counts["write_fail"] == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("resolve")
    w = sub.add_parser("write")
    w.add_argument("--commit", action="store_true")
    w.add_argument("--skip-dc", action="store_true")
    a = ap.parse_args()
    if a.cmd == "resolve":
        rows = [r for r in csv.DictReader(open(REVIEW_CSV, encoding="utf-8-sig"))
                if r["bucket"] in ("co_pr", "multi_estate")]
        resolve(rows)
        return 0
    return write(a.commit, a.skip_dc)


if __name__ == "__main__":
    sys.exit(main())
