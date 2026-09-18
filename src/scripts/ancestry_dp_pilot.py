"""Ancestry pilot on the UNRESOLVED deep-prospecting cohort (READ-ONLY, no account writes).

Question the pilot answers (session 2026-09-18): when DP research finds no obituary for
the owner on title, does Ancestry (SSDI 1935-2014 + the obituary collection) settle
whether the owner died -- with a match basis stronger than county alone? That number
decides whether Ancestry earns a place in the MD/DC/VA pipeline.

Selection (deterministic, stated in the report):
  - verdict == "unresolved" in <run-dir>/stage_c_research.json
  - a real person owner (owner_first + owner_last, not a trust / business)
  - a SmartSkip subject age (the birth-year hint) in ranked_records.json
  - mailing state == property state (or blank), so "which state" is not a guess
  - stratified per state (DC / MD / VA), sorted by record key, first N each

Hints per record:
  expected_birth_year = this year - SmartSkip age      (tolerance +/-2 in the enricher)
  expected_death_year = year of the SiftMap obituary date (the DP finding's `dod`)

Passes per record (each a lookup_deceased call; the enricher counts page loads):
  A  birth + death hints  -> 1 load (SSDI skipped for a post-2014 death year)
  B  birth hint only      -> up to 2 loads (SSDI + obituary), only if A found nothing.
     The SiftMap obituary date can belong to a spouse or relative (that is often WHY the
     record is unresolved), so B tests "the owner died years ago and the web forgot".
  +1 once per run for the Newspapers paywall page before that tier disables itself.

Usage:
  python -u src/scripts/ancestry_dp_pilot.py --dry-run                 # selection only
  python -u src/scripts/ancestry_dp_pilot.py                           # 15 records, headed browser
  python -u src/scripts/ancestry_dp_pilot.py --n 15 --run-dir output/dp_obit_aug27 --no-fallback

Outputs: output/ancestry_pilot/pilot_<ts>.{json,csv,md} + pilot_<ts>.log
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

import ancestry_enricher as anc  # noqa: E402

log = logging.getLogger("ancestry_pilot")

FOOTPRINT_STATES = ("DC", "MD", "VA")
STRONG_BASIS = {"birth_year", "death_year", "middle_initial"}


def _year(s: str) -> int | None:
    s = (s or "").strip()
    return int(s[:4]) if len(s) >= 4 and s[:4].isdigit() else None


def load_cohort(run_dir: Path) -> list[dict]:
    recs = json.loads((run_dir / "records.json").read_text(encoding="utf-8"))
    ranked = json.loads((run_dir / "ranked_records.json").read_text(encoding="utf-8"))
    findings = json.loads((run_dir / "stage_c_research.json").read_text(encoding="utf-8"))["findings"]
    this_year = date.today().year
    rows = []
    for f in findings:
        if f.get("verdict") != "unresolved":
            continue
        key = f["record"]
        r = recs.get(key)
        rk = ranked.get(key) or {}
        if not r:
            continue
        subj = rk.get("subject") or {}
        age = subj.get("age")
        dec = rk.get("decedent_from_smartskip") or {}
        if rk.get("rerooted") and dec.get("age"):
            age = dec.get("age")          # the re-rooted decedent, not the namesake child
        try:
            age = int(age) if age not in (None, "") else None
        except (TypeError, ValueError):
            age = None
        rows.append({
            "key": key,
            "owner_first": (r.get("owner_first") or "").strip(),
            "owner_last": (r.get("owner_last") or "").strip(),
            "is_trust": bool(r.get("is_trust")) or bool(r.get("business_name")),
            "street": r.get("street", ""),
            "city": (r.get("city") or "").strip(),
            "state": (r.get("state") or "").strip().upper(),
            "zip": r.get("zip", ""),
            "mail_state": (r.get("mail_state") or "").strip().upper(),
            "lists": r.get("lists", ""),
            "obit_date": f.get("dod") or r.get("obit_date") or "",
            "dod_source": f.get("dod_source", ""),
            "age": age,
            "rerooted": bool(rk.get("rerooted")),
            "expected_birth_year": (this_year - age) if age else None,
            "expected_death_year": _year(f.get("dod") or ""),
            "evidence": f.get("evidence", ""),
        })
    return rows


def select(rows: list[dict], n: int) -> tuple[list[dict], dict]:
    reasons = Counter()
    eligible = []
    for r in rows:
        if r["state"] not in FOOTPRINT_STATES:
            reasons["out_of_footprint"] += 1
        elif r["is_trust"]:
            reasons["trust_or_business"] += 1
        elif not (r["owner_first"] and r["owner_last"]):
            reasons["no_owner_name"] += 1
        elif len(r["owner_first"]) < 3 or not r["owner_first"].isalpha():
            reasons["first_name_not_a_name"] += 1     # "Ex", initials, "J."
        elif not r["age"]:
            reasons["no_smartskip_age"] += 1
        elif r["mail_state"] and r["mail_state"] != r["state"]:
            reasons["mailing_state_differs"] += 1
        else:
            eligible.append(r)
    eligible.sort(key=lambda r: r["key"])
    per_state = {s: n // len(FOOTPRINT_STATES) for s in FOOTPRINT_STATES}
    for s in ("MD", "VA", "DC")[: n - sum(per_state.values())]:
        per_state[s] += 1
    picked, taken = [], Counter()
    for r in eligible:
        if taken[r["state"]] < per_state[r["state"]]:
            picked.append(r)
            taken[r["state"]] += 1
    # top up from any state if one state ran short
    if len(picked) < n:
        for r in eligible:
            if r not in picked:
                picked.append(r)
                if len(picked) >= n:
                    break
    audit = {
        "unresolved_total": len(rows),
        "excluded": dict(reasons),
        "eligible": len(eligible),
        "eligible_by_state": dict(Counter(r["state"] for r in eligible)),
        "picked": len(picked),
        "picked_by_state": dict(Counter(r["state"] for r in picked)),
        "target_per_state": per_state,
    }
    return picked, audit


def classify(result: dict | None) -> str:
    if not result or not result.get("confirmed_deceased"):
        return "no_match"
    basis = set(result.get("match_basis") or [])
    if basis & STRONG_BASIS:
        return "dated_death_strong_basis" if result.get("date_of_death") else "match_strong_basis_no_date"
    return "dated_death_weak_basis" if result.get("date_of_death") else "match_weak_basis_no_date"


async def run_live(picked: list[dict], fallback: bool, out_prefix: Path) -> list[dict]:
    loads_start = anc._get_page_loads_today()
    log.info("Page loads already counted today: %d / %d", loads_start, anc.DAILY_LIMIT)
    pw, context, page = await anc.launch_browser()
    if page is None:
        log.error("Ancestry login failed -- nothing looked up")
        return []
    results = []
    try:
        for i, r in enumerate(picked, 1):
            if anc.is_circuit_broken():
                log.error("Circuit breaker tripped -- stopping at record %d", i)
                break
            if not anc._can_load_page():
                log.error("Daily page-load cap reached -- stopping at record %d", i)
                break
            name = f"{r['owner_first']} {r['owner_last']}"
            row = dict(r)
            row.update({"pass_hit": "", "result": None, "loads_used": 0, "outcome": "no_match"})
            t0 = time.time()
            before = anc._get_page_loads_today()
            log.info("[%d/%d] %s | %s, %s | age %s -> birth ~%s | obit %s -> death %s",
                     i, len(picked), name, r["city"], r["state"], r["age"],
                     r["expected_birth_year"], r["obit_date"], r["expected_death_year"])
            res = await anc.lookup_deceased(page, name=name, city=r["city"], state=r["state"],
                                            expected_birth_year=r["expected_birth_year"],
                                            expected_death_year=r["expected_death_year"])
            if res:
                row["pass_hit"] = "A"
            elif fallback and not anc.is_circuit_broken() and anc._can_load_page():
                await anc._delay(2, 4)
                res = await anc.lookup_deceased(page, name=name, city=r["city"], state=r["state"],
                                                expected_birth_year=r["expected_birth_year"],
                                                expected_death_year=None)
                if res:
                    row["pass_hit"] = "B"
            row["result"] = res
            row["loads_used"] = anc._get_page_loads_today() - before
            row["seconds"] = round(time.time() - t0, 1)
            row["outcome"] = classify(res)
            if res:
                log.info("   -> %s: %s died %s (%s) basis=%s", row["pass_hit"], res.get("full_name"),
                         res.get("date_of_death"), res.get("source_type"), res.get("match_basis"))
            else:
                log.info("   -> no match (%d loads)", row["loads_used"])
            results.append(row)
            # checkpoint after every record so a crash loses nothing
            out_prefix.with_suffix(".partial.json").write_text(
                json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
            await anc._delay(3, 6)
    finally:
        await anc.close_browser(pw, context)
    log.info("Page loads: %d -> %d (+%d)", loads_start, anc._get_page_loads_today(),
             anc._get_page_loads_today() - loads_start)
    return results


def write_report(results: list[dict], audit: dict, picked: list[dict], out_prefix: Path,
                 loads_before: int, dry_run: bool) -> Path:
    summary = Counter(r["outcome"] for r in results)
    passes = Counter(r["pass_hit"] or "-" for r in results)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "selection_audit": audit,
        "page_loads_before": loads_before,
        "page_loads_after": anc._get_page_loads_today(),
        "daily_limit": anc.DAILY_LIMIT,
        "circuit_broken": anc.is_circuit_broken(),
        "outcomes": dict(summary),
        "pass_hits": dict(passes),
        "records": results if results else picked,
    }
    out_prefix.with_suffix(".json").write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                                               encoding="utf-8")
    cols = ["key", "owner_first", "owner_last", "street", "city", "state", "zip", "age",
            "expected_birth_year", "obit_date", "expected_death_year", "rerooted", "pass_hit",
            "outcome", "loads_used", "anc_full_name", "anc_date_of_death", "anc_source_type",
            "anc_match_basis", "anc_last_residence", "anc_source_url"]
    with out_prefix.with_suffix(".csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in (results or picked):
            res = r.get("result") or {}
            w.writerow({**r,
                        "anc_full_name": res.get("full_name", ""),
                        "anc_date_of_death": res.get("date_of_death", ""),
                        "anc_source_type": res.get("source_type", ""),
                        "anc_match_basis": "|".join(res.get("match_basis") or []),
                        "anc_last_residence": res.get("last_residence", ""),
                        "anc_source_url": res.get("source_url", "")})
    lines = [f"# Ancestry pilot on the unresolved DP cohort -- {payload['generated_at']}", ""]
    lines.append(f"Selection: {audit['unresolved_total']} unresolved -> {audit['eligible']} eligible "
                 f"({audit['eligible_by_state']}) -> {audit['picked']} picked ({audit['picked_by_state']}). "
                 f"Excluded: {audit['excluded']}")
    if results:
        lines.append(f"Page loads: {loads_before} -> {payload['page_loads_after']} of {anc.DAILY_LIMIT}; "
                     f"circuit breaker {'TRIPPED' if payload['circuit_broken'] else 'quiet'}.")
        lines.append(f"Outcomes: {dict(summary)}; pass hits: {dict(passes)}")
        lines += ["", "| # | owner | city, st | age/birth | obit/death hint | pass | outcome | Ancestry name | died | basis | loads |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for i, r in enumerate(results, 1):
            res = r.get("result") or {}
            lines.append(f"| {i} | {r['owner_first']} {r['owner_last']} | {r['city']}, {r['state']} | "
                         f"{r['age']}/{r['expected_birth_year']} | {r['obit_date']}/{r['expected_death_year']} | "
                         f"{r['pass_hit'] or '-'} | {r['outcome']} | {res.get('full_name', '')} | "
                         f"{res.get('date_of_death', '')} | {'|'.join(res.get('match_basis') or [])} | {r['loads_used']} |")
    else:
        lines += ["", "| # | owner | city, st | age/birth hint | obit/death hint | lists |", "|---|---|---|---|---|---|"]
        for i, r in enumerate(picked, 1):
            lines.append(f"| {i} | {r['owner_first']} {r['owner_last']} | {r['city']}, {r['state']} | "
                         f"{r['age']}/{r['expected_birth_year']} | {r['obit_date']}/{r['expected_death_year']} | {r['lists']} |")
    md = out_prefix.with_suffix(".md")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default="output/dp_obit_aug27")
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--out-dir", default="output/ancestry_pilot")
    ap.add_argument("--dry-run", action="store_true", help="select and report, open no browser")
    ap.add_argument("--no-fallback", action="store_true", help="pass A only (skip the birth-only pass)")
    a = ap.parse_args()

    run_dir = ROOT / a.run_dir if not Path(a.run_dir).is_absolute() else Path(a.run_dir)
    out_dir = ROOT / a.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_prefix = out_dir / f"pilot_{ts}"

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(out_prefix.with_suffix(".log"), encoding="utf-8")])
    logging.getLogger("ancestry_enricher").setLevel(logging.DEBUG)

    rows = load_cohort(run_dir)
    picked, audit = select(rows, a.n)
    log.info("Selection: %s", json.dumps(audit))
    for r in picked:
        log.info("  %s | %s %s | %s, %s | age %s | obit %s", r["key"], r["owner_first"], r["owner_last"],
                 r["city"], r["state"], r["age"], r["obit_date"])
    loads_before = anc._get_page_loads_today()
    if a.dry_run:
        md = write_report([], audit, picked, out_prefix, loads_before, dry_run=True)
        log.info("Dry run written: %s", md)
        return 0
    if not picked:
        log.error("Nothing eligible to look up")
        return 2
    results = asyncio.run(run_live(picked, fallback=not a.no_fallback, out_prefix=out_prefix))
    md = write_report(results, audit, picked, out_prefix, loads_before, dry_run=False)
    log.info("Report: %s", md)
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
