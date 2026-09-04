"""Fetch full VA notice text: browser grid-harvest + Scrapfly detail fetch.

Root cause (verified 2026-09-04 on the daily run): the Firecrawl pull's grid
HTML carries NO notice id -- the View buttons' `Details.aspx?SID=..&ID=<n>`
onclick is attached by client-side JS the Firecrawl capture misses (0 of 38
rows had one), so va_trustee_sale_pull's inline --full-text falls back to its
replay-the-search-and-click-the-Nth-button path, which drifts after ~5 rows:
only 5 of 38 got auction dates, the other 33 correctly hit the wrong-notice
guard (no bad data, but no enrichment either).

Cure, in two proven halves:
  1. A settled real Chromium exposes the onclick on EVERY grid row (10/10 per
     page, verified), giving a real `Details.aspx?SID=&ID=` per notice. VA is
     PUBLIC (no login) -- this drives the Popular Searches widget; county
     checkboxes are AutoPostBack so they're clicked one at a time with a
     settle wait (the trap in va_trustee_sale_pull.build_actions).
  2. Each harvested detail URL is fetched with the ALREADY-PROVEN
     va_trustee_sale_pull.fetch_full_text_direct (Scrapfly asp + the correct
     Turnstile injection that sets both the name and widget-id response
     fields + a synthetic-MouseEvent View-Notice click). An in-browser
     solve+click was tried first and rejected: it cleared the gate MESSAGE
     but never populated `lblContentText` (the notice body renders via an
     ASP.NET postback that a plain Playwright click does not complete the
     same way the synthetic sequence does). fetch_full_text_direct handles
     that, so we reuse it rather than re-derive it.

VA's SID is cookieless (a URL query param, not an `(S(..))` path), so a fresh
Scrapfly call to the harvested URL resumes that server-side session -- the
same mechanism the direct path already relied on.

Auction date is re-mined with parse_auction_date (deed-recording-date
rejection + cannot-predate-publication guards).

Usage:
    python src/scripts/va_fetch_full_text_browser.py \
        --csv output/daily_pulls/2026-09-04/va_foreclosures_footprint.csv --limit 3
    python src/scripts/va_fetch_full_text_browser.py \
        --csv output/daily_pulls/2026-09-04/va_foreclosures_footprint.csv \
        --counties Fairfax,Arlington --days 7
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import va_trustee_sale_pull as va  # noqa: E402
from va_trustee_sale_pull import (  # noqa: E402
    extract_full_notice_text,
    fetch_full_text,
    parse_auction_date,
    scrapfly_preflight,
)

DDL_POPULAR = "#ctl00_ContentPlaceHolder1_as1_ddlPopularSearches"
GRID_ROW = "table.nested"
NEXT_BTN = "#ctl00_ContentPlaceHolder1_WSExtendedGridNP1_GridView1_ctl01_btnNext"
GO_BTN = "#ctl00_ContentPlaceHolder1_as1_btnGo"


def _row_key(street: str, zip5: str, snippet: str) -> tuple:
    street = " ".join((street or "").lower().split())
    if street:
        return ("addr", street, (zip5 or "").strip()[:5])
    return ("snip", " ".join((snippet or "").lower().split())[:600], "")


def _eval_retry(page, js: str, arg=None, attempts: int = 4):
    """evaluate that survives 'Execution context was destroyed' from the
    AutoPostBack controls (dropdown + county checkboxes)."""
    last = None
    for _ in range(attempts):
        try:
            return page.evaluate(js, arg)
        except Exception as exc:
            last = exc
            if "Execution context was destroyed" not in str(exc):
                raise
            page.wait_for_load_state("domcontentloaded")
            time.sleep(2)
    raise last


def open_search(page, popular_search: str, counties: list, days: int) -> None:
    """Public popular-search navigation to the results grid at page 1."""
    page.goto(va.SEARCH_URL, wait_until="domcontentloaded")
    page.wait_for_selector(DDL_POPULAR, timeout=30_000)

    # Category change is a full navigation back to Search.aspx.
    _eval_retry(
        page,
        f"""() => {{
            const sel = document.querySelector('{DDL_POPULAR}');
            if (sel) {{ sel.value = '{popular_search}'; sel.dispatchEvent(new Event('change', {{bubbles: true}})); }}
        }}""",
    )
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector(DDL_POPULAR, timeout=30_000)
    time.sleep(2)

    idx = [va.COUNTY_CHECKBOX_INDEX[c] for c in counties if c in va.COUNTY_CHECKBOX_INDEX]

    def _check_one(i: int) -> None:
        try:
            page.evaluate(
                """(i) => {
                    const cb = document.querySelector('#ctl00_ContentPlaceHolder1_as1_lstCounty_' + i);
                    if (cb && !cb.checked) {
                        cb.checked = true;
                        cb.dispatchEvent(new Event('click', {bubbles: true}));
                        cb.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                }""",
                i,
            )
        except Exception:
            pass  # postback destroyed the context; the click still posted
        page.wait_for_load_state("domcontentloaded")
        time.sleep(va.COUNTY_POSTBACK_SETTLE_MS / 1000)

    for _round in range(3):
        states = _eval_retry(
            page,
            """(idx) => idx.map(i => {
                const cb = document.querySelector('#ctl00_ContentPlaceHolder1_as1_lstCounty_' + i);
                return cb ? cb.checked : null;
            })""",
            idx,
        )
        missing = [i for i, on in zip(idx, states) if not on]
        if not missing:
            break
        for i in missing:
            _check_one(i)
    else:
        raise RuntimeError(f"county checkboxes never settled checked: {counties}")

    _eval_retry(
        page,
        """(days) => {
            const dr = document.querySelector('#ctl00_ContentPlaceHolder1_as1_rbLastNumDays');
            if (dr) { dr.checked = true; dr.dispatchEvent(new Event('change', {bubbles: true})); }
            const nd = document.querySelector('#ctl00_ContentPlaceHolder1_as1_txtLastNumDays');
            if (nd) {
                nd.value = String(days);
                nd.dispatchEvent(new Event('input', {bubbles: true}));
                nd.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }""",
        days,
    )
    time.sleep(1.5)
    page.click(GO_BTN)
    page.wait_for_selector(GRID_ROW, timeout=30_000)
    time.sleep(3)  # let the onclick-attaching JS run so Details URLs exist


def _content_settled(page) -> str:
    """page.content() that rides out a late async postback still repainting
    the grid ('page is navigating and changing the content')."""
    last = None
    for _ in range(6):
        try:
            return page.content()
        except Exception as exc:
            last = exc
            if "navigating" not in str(exc) and "changing the content" not in str(exc):
                raise
            page.wait_for_load_state("domcontentloaded")
            time.sleep(2)
    raise last


def collect_grid_rows(page, popular_search: str, max_pages: int) -> list[dict]:
    rows: list[dict] = []
    page_no = 1
    while page_no <= max_pages:
        html = _content_settled(page)
        page_rows = va.parse_grid_html(html, popular_search, page_no=page_no)
        with_id = sum(1 for r in page_rows if r.get("notice_id"))
        print(f"  grid page {page_no}: {len(page_rows)} row(s), {with_id} with an id")
        rows.extend(page_rows)
        nxt = page.locator(NEXT_BTN)
        if nxt.count() == 0 or nxt.first.get_attribute("disabled") is not None:
            break
        page_no += 1
        if page_no > max_pages:
            break
        page.click(NEXT_BTN)
        page.wait_for_load_state("domcontentloaded")
        time.sleep(3)
        page.wait_for_selector(GRID_ROW, timeout=30_000)
        time.sleep(2)
    return rows


def harvest_grid(popular_search: str, counties: list, days: int, max_pages: int,
                 headed: bool) -> list[dict]:
    """Browser-only: return the grid rows (WITH real Details.aspx URLs)."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        page = browser.new_page()
        open_search(page, popular_search, counties, days)
        rows = collect_grid_rows(page, popular_search, max_pages)
        browser.close()
    return rows


def fetch_all(popular_search: str, counties: list, days: int, max_pages: int,
              limit: int, captcha_api_key: str, scrapfly_key: str, headed: bool,
              need_keys: set | None = None) -> dict:
    results: dict = {}
    grid_rows = harvest_grid(popular_search, counties, days, max_pages, headed)

    seen: set = set()
    targets = []
    for r in grid_rows:
        nid = r.get("notice_id")
        durl = r.get("_details_url")
        if nid and durl and nid not in seen:
            seen.add(nid)
            targets.append(r)
    no_id = len(grid_rows) - sum(1 for r in grid_rows if r.get("notice_id"))
    if no_id:
        print(f"  NOTE: {no_id} grid row(s) still had no id after settle -- no full text for them")
    # --fill-missing: only spend a solve on grid rows whose CSV row still
    # lacks an auction date (the harvest above is free; the detail fetch is not).
    if need_keys is not None:
        before = len(targets)
        targets = [r for r in targets
                   if _row_key(r.get("street", ""), r.get("zip", ""),
                               r.get("notice_text_snippet", "")) in need_keys]
        print(f"  --fill-missing: {len(targets)} of {before} grid rows map to a CSV row "
              f"still missing an auction date")
    if limit:
        targets = targets[:limit]

    # Preflight Scrapfly BEFORE any 2Captcha spend (each detail pays a solve
    # first, so a dead quota would bill one per row for nothing).
    ok, why = scrapfly_preflight(scrapfly_key)
    if not ok:
        raise SystemExit(f"Scrapfly unusable ({why}) -- aborting before any 2Captcha spend; "
                         "fix the Scrapfly account/quota and re-run")
    print(f"fetching {len(targets)} detail page(s) via Scrapfly (click-by-id)...")

    # Errors that are transient Scrapfly render/session hiccups, worth one
    # fresh-session retry (each fetch_full_text call mints a new session).
    TRANSIENT = ("still_on_search_page", "SCENARIO_TIMEOUT", "ScrapflyScrapeError",
                 "captcha_solve_failed", "gate_not_cleared",
                 # Scrapfly infra blips, not logic failures (both seen live).
                 "ApiHttpServerError", "503", "No backend available")

    failures = 0
    for i, row in enumerate(targets, 1):
        label = row.get("street") or row.get("notice_text_snippet", "")[:40]
        # Replay the search in Scrapfly and click THIS row's View button by
        # notice_id on its own results page -- exact, drift-proof. The direct
        # Details.aspx fetch was tried first and returned gate_not_cleared:
        # the browser-harvested SID is a browser-session token Scrapfly's
        # separate session/IP cannot resume.
        res, full = {}, ""
        for attempt in range(2):
            res = fetch_full_text(
                popular_search, counties, days, int(row.get("_view_index") or 0),
                captcha_api_key=captcha_api_key, scrapfly_key=scrapfly_key,
                page_no=int(row.get("_page_no") or 1), notice_id=row["notice_id"],
            )
            full = extract_full_notice_text(res["html"]) if res.get("ok") else ""
            if res.get("ok") and len(full) >= 200:
                break
            err = res.get("error") or f"short_text ({len(full)} chars)"
            if attempt == 0 and any(t in err for t in TRANSIENT):
                print(f"  [{i}/{len(targets)}] id={row['notice_id']} {err} -- retrying once ({label})")
                continue
            break
        if not res.get("ok") or len(full) < 200:
            failures += 1
            err = res.get("error") or f"short_text ({len(full)} chars)"
            print(f"  [{i}/{len(targets)}] id={row['notice_id']} FAILED: {err} ({label})")
            if failures >= 5 and not results:
                raise SystemExit("first 5 detail fetches all failed -- aborting before "
                                 "burning more 2Captcha solves; investigate the gate")
            continue
        parts = (row.get("street") or "").split()
        if parts and not re.search(
                re.escape(parts[0]) + (r"\s+" + re.escape(parts[1]) if len(parts) > 1 else ""),
                full, re.I):
            failures += 1
            print(f"  [{i}/{len(targets)}] id={row['notice_id']} WRONG NOTICE returned ({label})")
            continue
        key = _row_key(row.get("street", ""), row.get("zip", ""),
                       row.get("notice_text_snippet", ""))
        if key in results and results[key]["notice_id"] != row["notice_id"]:
            if key[0] == "addr":
                # Weekly republication of the same foreclosure notice;
                # the grid is newest-first so the stored one is current.
                print(f"  [{i}/{len(targets)}] id={row['notice_id']} republication of "
                      f"id={results[key]['notice_id']} ({label}) -- keeping the newer")
                continue
            print(f"  [{i}/{len(targets)}] id={row['notice_id']} KEY COLLISION with "
                  f"id={results[key]['notice_id']} -- dropping both")
            del results[key]
            continue
        results[key] = {
            "notice_id": row["notice_id"],
            "full_text": full,
            "auction_date": parse_auction_date(full, row.get("date_published", "")),
            "loan_principal": va.parse_loan_principal(full),
        }
        print(f"  [{i}/{len(targets)}] id={row['notice_id']} ok "
              f"auction={results[key]['auction_date'] or '-'} ({label})")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="the day's VA foreclosure CSV to enrich")
    ap.add_argument("--out", default="", help="default: <csv basename>_full.csv")
    ap.add_argument("--popular-search", default="4")
    ap.add_argument("--counties", default="Fairfax,Arlington")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max-pages", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="spike: stop after N fetched details")
    ap.add_argument("--fill-missing", action="store_true",
                    help="only fetch rows whose auction_date is still blank in --csv "
                         "(the grid harvest is free; this avoids re-spending a solve on rows "
                         "already enriched). Writes back in place unless --out is given.")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    creds = va.load_credentials()
    captcha_key = creds.get("captcha_api_key", "")
    scrapfly_key = creds.get("scrapfly_key", "")
    if not captcha_key or not scrapfly_key:
        raise SystemExit("--full-text needs CAPTCHA_API_KEY and SCRAPFLY_KEY in .env (2Captcha "
                         "solves the Turnstile, Scrapfly asp clears the detail gate)")

    counties = [c.strip() for c in args.counties.split(",") if c.strip()]
    csv_path = Path(args.csv)
    if args.out:
        out_path = Path(args.out)
    elif args.fill_missing:
        out_path = csv_path  # in place
    else:
        out_path = csv_path.with_name(csv_path.stem + "_full.csv")

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if "full_text" not in fieldnames:
        fieldnames.append("full_text")

    need_keys = None
    if args.fill_missing:
        need_keys = {
            _row_key(r.get("street", ""), r.get("zip", ""), r.get("notice_text_snippet", ""))
            for r in rows if not (r.get("auction_date") or "").strip()
        }
        if not need_keys:
            print("--fill-missing: every row already has an auction date; nothing to do.")
            return
        print(f"--fill-missing: {len(need_keys)} row(s) still missing an auction date")

    results = fetch_all(args.popular_search, counties, args.days, args.max_pages,
                        args.limit, captcha_key, scrapfly_key, args.headed, need_keys=need_keys)
    print(f"fetched {len(results)} detail page(s)")

    matched = 0
    for r in rows:
        key = _row_key(r.get("street", ""), r.get("zip", ""), r.get("notice_text_snippet", ""))
        d = results.get(key)
        if not d:
            continue
        matched += 1
        r["full_text"] = d["full_text"]
        if d["notice_id"] and "notice_id" in fieldnames:
            r["notice_id"] = d["notice_id"]
        # The full-text re-mine WINS: the snippet-era value has no deed-date
        # guard, so it can be a years-stale recording date.
        if d["auction_date"]:
            r["auction_date"] = d["auction_date"]
        if d["loan_principal"] and not r.get("loan_principal"):
            r["loan_principal"] = d["loan_principal"]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"matched {matched} of {len(rows)} CSV row(s); wrote {out_path}")
    if len(results) and matched < len(results):
        print(f"NOTE: {len(results) - matched} fetched detail(s) matched no CSV row")


if __name__ == "__main__":
    main()
