"""Targeted DC probate case lookups on the Tyler portal (read-only).

Answers "who did the court actually appoint as PR" for SPECIFIC case numbers —
the thing the extraction sheet cannot be trusted for. Built entirely on
`dc_probate_pull`'s proven machinery (`_search_one_case`, WAF solve, parties
sniffing); unlike the sequential ADM walk this searches an explicit case list,
so SEB/NRT case types work too. Headed browser required (the portal
render-blocks headless); 2Captcha auto-solves the WAF, same as the
"SiftStack DC Portal Pull" task.

Default case list: the 10 write_dc_recency rows of
output/pr_repair_allcohorts/copr_multi_plan.csv (2026-09-07). Results land in
output/pr_repair_allcohorts/dc_case_pr_lookup.json (resumable — cases already
in the file are skipped) and a comparison against the plan's sheet names is
printed.

Usage:
    python -X utf8 -u src/scripts/dc_case_pr_lookup.py             # the plan's 10
    python -X utf8 -u src/scripts/dc_case_pr_lookup.py --cases 2026-ADM-000200,...
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dotenv import dotenv_values  # noqa: E402

import dc_probate_pull as dc  # noqa: E402

RUN = ROOT / "output" / "pr_repair_allcohorts"
PLAN_P = RUN / "copr_multi_plan.csv"
OUT_P = RUN / "dc_case_pr_lookup.json"


def plan_dc_rows() -> list[dict]:
    return [r for r in csv.DictReader(open(PLAN_P, encoding="utf-8-sig"))
            if r["verdict"] in ("write_dc_recency",)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", help="comma-separated case numbers (default: the plan's DC rows)")
    ap.add_argument("--headless", action="store_true",
                    help="try headless (known render-blocked; headed is the working mode)")
    a = ap.parse_args()

    plan = plan_dc_rows()
    cases = ([c.strip() for c in a.cases.split(",") if c.strip()] if a.cases
             else [r["estate"] for r in plan])
    results: dict[str, dict] = {}
    if OUT_P.exists():
        results = json.loads(OUT_P.read_text(encoding="utf-8"))
    todo = [c for c in cases if c not in results]
    print(f"{len(cases)} cases, {len(todo)} to fetch ({len(cases) - len(todo)} cached)")

    if todo:
        env = dotenv_values(str(dc.ENV_PATH))
        captcha_key = env.get("CAPTCHA_API_KEY", "") or ""
        if "your" in captcha_key.lower():
            captcha_key = ""
        headed = not a.headless
        solve_counter = {"n": 0}
        from playwright.sync_api import sync_playwright
        dc.PROBE_DIR.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headed)
            ctx_kwargs = {"user_agent": dc.UA, "viewport": {"width": 1400, "height": 900}}
            if dc.PORTAL_STATE_PATH.exists():
                ctx_kwargs["storage_state"] = str(dc.PORTAL_STATE_PATH)
            context = browser.new_context(**ctx_kwargs)
            parties_by_case: dict[str, dict] = {}

            def _on_response(resp):
                try:
                    url = resp.url
                    if "RegisterOfActionsService/Parties(" not in url or resp.status != 200:
                        return
                    import re
                    m = re.search(r"Parties\('([0-9A-Fa-f]+)'\)", url)
                    if m:
                        parties_by_case[m.group(1)] = resp.json()
                except Exception:  # noqa: BLE001
                    pass

            context.on("response", _on_response)
            context._parties_by_case = parties_by_case
            page = context.new_page()
            page.goto(dc.SMART_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
            dc.ensure_no_waf(page, context, headed, captcha_key, solve_counter)

            for i, case_no in enumerate(todo, 1):
                try:
                    hit = dc._search_one_case(page, context, case_no, headed, captcha_key,
                                              solve_counter, 0)
                except RuntimeError as e:
                    print(f"  [{i}/{len(todo)}] {case_no}: RUNTIME {e}")
                    break
                if hit:
                    results[case_no] = {k: hit.get(k) for k in
                                        ("case_no", "decedent_name", "filing_date", "status",
                                         "case_type", "personal_reps", "heirs")}
                    prs = hit.get("personal_reps") or []
                    print(f"  [{i}/{len(todo)}] {case_no}: {hit.get('decedent_name', '?')} "
                          f"({hit.get('status', '?')}) PRs: "
                          f"{' | '.join(p.get('name', '') for p in prs) or 'NONE PARSED'}")
                else:
                    results[case_no] = {"case_no": case_no, "not_found": True}
                    print(f"  [{i}/{len(todo)}] {case_no}: no result")
                OUT_P.write_text(json.dumps(results, indent=1), encoding="utf-8")
                time.sleep(random.uniform(4, 8))
            context.storage_state(path=str(dc.PORTAL_STATE_PATH))
            browser.close()

    # Compare against the plan's sheet-confidence names.
    if plan:
        print("\nplan vs portal:")
        from obituary_dp_batch import names_match
        for r in plan:
            got = results.get(r["estate"]) or {}
            prs = got.get("personal_reps") or []
            sheet = f"{r['new_first']} {r['new_last']}".strip()
            if got.get("not_found"):
                print(f"  {r['street'][:28]:28} {r['estate']}: NOT FOUND on portal")
            elif not prs:
                print(f"  {r['street'][:28]:28} {r['estate']}: no PRs parsed "
                      f"(status {got.get('status')})")
            else:
                match = any(names_match(sheet, p.get("name") or "") for p in prs)
                print(f"  {r['street'][:28]:28} {r['estate']}: sheet {sheet!r} vs live "
                      f"{' | '.join(p.get('name', '') for p in prs)!r} -> "
                      f"{'MATCH' if match else 'DIFFERS'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
