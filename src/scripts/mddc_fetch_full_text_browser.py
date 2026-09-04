"""Fetch full MDDC notice text: Playwright session + 2Captcha Turnstile solve.

Why this exists (all verified live 2026-09-03):
  * The Firecrawl pull's grid HTML carries NO notice id -- the View buttons'
    `onclick="location.href='Details.aspx?SID=..&ID=<n>'"` is attached by
    client-side JS that Firecrawl's capture misses, so `notice_id` is blank
    on every pulled row and `mddc_fetch_full_text.py` (which fetches by id)
    has nothing to key on. In a real Chromium the onclick IS present once
    the grid settles.
  * `Details.aspx` is gated: "You must complete the challenge in order to
    continue" + a Cloudflare Turnstile widget (sitekey below) + the same
    `btnViewNotice` postback as the VA site. Scrapfly `asp=True` alone does
    NOT clear it (`mddc_fetch_full_text.py --test-id` returns
    gate_not_cleared), and the challenge never auto-solves in headless
    Chromium (waited 30s). A 2Captcha Turnstile solve + token inject + View
    Notice click is the working recipe -- same as va_trustee_sale_pull's
    fetch_full_text_direct, but run inside our own logged-in browser session
    so no Scrapfly is needed at all.
  * The first navigation to Details.aspx in a session BOUNCES back to
    Search.aspx; a retry lands. Handled with a bounce-retry loop.
  * The TN pipeline's identical vendor gate is session-level (one solve
    covers the run), so this solves lazily: only when a challenge actually
    renders.

Auction date / loan principal are re-mined from the full text with
va_trustee_sale_pull.parse_auction_date -- it carries the deed-recording-date
rejection and cannot-predate-publication guards that
mddc_trustee_sale_pull.parse_auction_date lacks (the snippet-era MDDC parse
emitted a 2024 "auction date" that was a deed date, seen live 2026-09-03).

Rows are matched back to the input CSV by (street, zip), falling back to the
first 60 chars of the notice snippet; the fetched notice_id is written back
into the CSV so downstream id-dedup finally has something to key on.

Usage:
    # Spike on a few rows first; --headed to watch it.
    python src/scripts/mddc_fetch_full_text_browser.py \
        --csv output/daily_pulls/2026-09-03/mddc_trustee_sale.csv --limit 3

    # Full run (writes <csv>_full.csv next to the input).
    python src/scripts/mddc_fetch_full_text_browser.py \
        --csv output/daily_pulls/2026-09-03/mddc_trustee_sale.csv \
        --counties "Washington DC,Montgomery,Anne Arundel,Frederick" --days 7
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mddc_trustee_sale_pull as mp  # noqa: E402
from va_trustee_sale_pull import (  # noqa: E402
    _solve_turnstile,
    extract_full_notice_text,
    parse_auction_date,
)

SEARCH_READY = "#ctl00_ContentPlaceHolder1_as1_ddlSavedSearches"
GRID_ROW = "table.nested"
NEXT_BTN = "#ctl00_ContentPlaceHolder1_WSExtendedGrid1_GridView1_ctl01_btnNext"
VIEW_NOTICE_BTN = "#ctl00_ContentPlaceHolder1_PublicNoticeDetailsBody1_btnViewNotice"
TURNSTILE_INPUT = "input[name='cf-turnstile-response']"
# Read off the live challenge page 2026-09-03. Differs from VA's.
MDDC_TURNSTILE_SITEKEY = "0x4AAAAAADs-0gpXV1IADMoy"
DETAILS_URL_RE = re.compile(r"Details\.aspx", re.I)
CHALLENGE_MARK_RE = re.compile(
    r"complete the challenge in order to continue|cf-turnstile|challenge-platform", re.I)
SID_RE = re.compile(r"\(S\(([a-z0-9]+)\)\)", re.I)


def _eval_retry(page, js: str, arg=None, attempts: int = 4):
    """page.evaluate that survives 'Execution context was destroyed': the
    saved-search select and every county checkbox fire their own async
    postbacks, any of which can reload the page mid-evaluate."""
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


def _row_key(street: str, zip5: str, snippet: str) -> tuple:
    street = " ".join((street or "").lower().split())
    if street:
        return ("addr", street, (zip5 or "").strip()[:5])
    # Streetless rows (order nisi / ratification notices) all OPEN with the
    # same court boilerplate -- a 60-char window collided two different
    # notices on the 2026-09-03 spike, silently swapping their full text.
    # 600 normalized chars reaches the case-specific body.
    return ("snip", " ".join((snippet or "").lower().split())[:600], "")


def login_and_search(page, creds: dict, saved_search: str, counties: list,
                     days: int, page_no: int = 1) -> None:
    """Land on the results grid at `page_no`. Raises on a missing grid."""
    page.goto(mp.LOGIN_URL, wait_until="domcontentloaded")
    if page.locator("#ctl00_ContentPlaceHolder1_AuthenticateIPA1_txtEmailAddress").count():
        page.fill("#ctl00_ContentPlaceHolder1_AuthenticateIPA1_txtEmailAddress", creds["email"])
        page.fill("#ctl00_ContentPlaceHolder1_AuthenticateIPA1_txtPassword", creds["password"])
        page.click("#ctl00_ContentPlaceHolder1_AuthenticateIPA1_btnAuth")
    page.wait_for_selector(SEARCH_READY, timeout=30_000)

    # Saved-search select fires its own postback on change.
    page.select_option(SEARCH_READY, saved_search)
    page.wait_for_load_state("domcontentloaded")
    time.sleep(4)

    county_idx = [mp.COUNTY_CHECKBOX_INDEX[c] for c in counties if c in mp.COUNTY_CHECKBOX_INDEX]
    # Each county checkbox fires its OWN async postback that reloads the page
    # and destroys the JS context (same trap the VA site documents for
    # multi-county selection) -- so: one checkbox per evaluate, settle, then
    # verify the whole set and retry whatever a competing postback undid.
    def _check_one(idx: int) -> None:
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
                idx,
            )
        except Exception:
            pass  # context destroyed by the postback -- the click still posted
        page.wait_for_load_state("domcontentloaded")
        time.sleep(2.5)

    for _round in range(3):
        states = _eval_retry(
            page,
            """(idx) => idx.map(i => {
                const cb = document.querySelector('#ctl00_ContentPlaceHolder1_as1_lstCounty_' + i);
                return cb ? cb.checked : null;
            })""",
            county_idx,
        )
        missing = [i for i, on in zip(county_idx, states) if not on]
        if not missing:
            break
        for idx in missing:
            _check_one(idx)
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
    page.wait_for_load_state("domcontentloaded")
    time.sleep(1.5)
    page.click("#ctl00_ContentPlaceHolder1_as1_btnGo")
    page.wait_for_selector(GRID_ROW, timeout=30_000)
    time.sleep(3)  # let the onclick-attaching JS run so the Details URLs exist
    for _ in range(page_no - 1):
        page.click(NEXT_BTN)
        page.wait_for_load_state("domcontentloaded")
        time.sleep(3)
        page.wait_for_selector(GRID_ROW, timeout=30_000)


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


def collect_grid_rows(page, saved_search: str, max_pages: int) -> list[dict]:
    """Parsed rows (WITH notice_id, from the settled grid) across all pages."""
    rows: list[dict] = []
    page_no = 1
    while page_no <= max_pages:
        html = _content_settled(page)
        page_rows = mp.parse_grid_html(html, saved_search)
        with_id = sum(1 for r in page_rows if r.get("notice_id"))
        print(f"  grid page {page_no}: {len(page_rows)} row(s), {with_id} with an id")
        rows.extend(page_rows)
        nxt = page.locator(NEXT_BTN)
        # On the last page the button is still rendered, just disabled --
        # clicking it times out, it does not no-op.
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


def fetch_detail(page, notice_id: str, captcha_api_key: str) -> dict:
    """Navigate to one Details.aspx and return {'ok', 'error', 'full_text'}."""
    landed = False
    for _ in range(3):
        sid_m = SID_RE.search(page.url)
        if not sid_m:
            return {"ok": False, "error": "no_session_in_url", "full_text": ""}
        page.evaluate(
            f"() => {{ location.href = 'Details.aspx?SID={sid_m.group(1)}&ID={notice_id}'; }}")
        try:
            # First nav in a session reliably bounces back to Search.aspx.
            page.wait_for_url(DETAILS_URL_RE, timeout=15_000)
            landed = True
            break
        except PWTimeout:
            continue
    if not landed:
        return {"ok": False, "error": "never_landed_on_details", "full_text": ""}
    time.sleep(2)
    html = page.content()

    if CHALLENGE_MARK_RE.search(html):
        token = _solve_turnstile(page.url, MDDC_TURNSTILE_SITEKEY, captcha_api_key)
        if not token:
            return {"ok": False, "error": "captcha_solve_failed", "full_text": ""}
        page.evaluate(
            """(tok) => {
                let inp = document.querySelector("input[name='cf-turnstile-response']");
                if (!inp) {
                    inp = document.createElement('input');
                    inp.type = 'hidden';
                    inp.name = 'cf-turnstile-response';
                    document.forms[0].appendChild(inp);
                }
                inp.value = tok;
            }""",
            token,
        )
        try:
            page.click(VIEW_NOTICE_BTN, timeout=10_000)
            page.wait_for_load_state("domcontentloaded")
        except PWTimeout:
            return {"ok": False, "error": "view_notice_click_failed", "full_text": ""}
        time.sleep(2)
        html = page.content()
        if CHALLENGE_MARK_RE.search(html) and "complete the challenge" in html.lower():
            return {"ok": False, "error": "gate_not_cleared_after_solve", "full_text": ""}

    full = extract_full_notice_text(html)
    if len(full) < 200:
        return {"ok": False, "error": f"suspiciously_short_text ({len(full)} chars)", "full_text": full}
    return {"ok": True, "error": "", "full_text": full}


def fetch_all(creds: dict, saved_search: str, counties: list, days: int,
              max_pages: int, limit: int, headed: bool) -> dict:
    """{row_key: {notice_id, full_text, auction_date, loan_principal}}."""
    results: dict = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        page = browser.new_page()
        login_and_search(page, creds, saved_search, counties, days)
        grid_rows = collect_grid_rows(page, saved_search, max_pages)

        seen: set = set()
        targets = []
        for r in grid_rows:
            nid = r.get("notice_id")
            if nid and nid not in seen:
                seen.add(nid)
                targets.append(r)
        no_id = len(grid_rows) - sum(1 for r in grid_rows if r.get("notice_id"))
        if no_id:
            print(f"  NOTE: {no_id} grid row(s) still had no id after settle -- they get no full text")
        if limit:
            targets = targets[:limit]
        print(f"fetching {len(targets)} detail page(s)...")

        failures = 0
        for i, row in enumerate(targets, 1):
            nid = row["notice_id"]
            res = fetch_detail(page, nid, creds.get("captcha_api_key", ""))
            label = row.get("street") or row.get("notice_text_snippet", "")[:40]
            if not res["ok"]:
                failures += 1
                print(f"  [{i}/{len(targets)}] id={nid} FAILED: {res['error']} ({label})")
                if failures >= 5 and not results:
                    raise SystemExit("first 5 detail fetches all failed -- aborting before "
                                     "burning more 2Captcha solves; investigate the gate")
                continue
            full = res["full_text"]
            # Wrong-notice guard (VA lesson 2026-09-01): the row's own street
            # must appear in what came back, or this is some other notice.
            parts = (row.get("street") or "").split()
            if parts and not re.search(
                    re.escape(parts[0]) + (r"\s+" + re.escape(parts[1]) if len(parts) > 1 else ""),
                    full, re.I):
                failures += 1
                print(f"  [{i}/{len(targets)}] id={nid} WRONG NOTICE returned ({label})")
                continue
            key = _row_key(row.get("street", ""), row.get("zip", ""),
                           row.get("notice_text_snippet", ""))
            if key in results and results[key]["notice_id"] != nid:
                if key[0] == "addr":
                    # Same street+zip under a different id = the weekly
                    # REPUBLICATION of the same foreclosure notice (they
                    # republish by law). The grid is newest-first, so the
                    # entry already stored is the current one -- keep it.
                    print(f"  [{i}/{len(targets)}] id={nid} republication of "
                          f"id={results[key]['notice_id']} ({label}) -- keeping the newer")
                    continue
                # Streetless boilerplate collision: two genuinely different
                # notices we cannot tell apart -- drop both rather than
                # attaching the wrong text to a record.
                print(f"  [{i}/{len(targets)}] id={nid} KEY COLLISION with "
                      f"id={results[key]['notice_id']} -- dropping both rather than "
                      f"attaching the wrong notice")
                del results[key]
                continue
            results[key] = {
                "notice_id": nid,
                "full_text": full,
                "auction_date": parse_auction_date(full, row.get("date_published", "")),
                "loan_principal": mp.parse_loan_principal(full),
            }
            print(f"  [{i}/{len(targets)}] id={nid} ok auction={results[key]['auction_date'] or '-'} "
                  f"({label})")
        browser.close()
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="the day's mddc_trustee_sale.csv to enrich")
    ap.add_argument("--out", default="", help="default: <csv basename>_full.csv")
    ap.add_argument("--saved-search", default="43")
    ap.add_argument("--counties", default="Washington DC,Montgomery,Anne Arundel,Frederick")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max-pages", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0, help="spike: stop after N fetched details")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    creds = mp.load_credentials()
    if not creds.get("captcha_api_key"):
        from dotenv import dotenv_values
        env_file = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
        creds["captcha_api_key"] = env_file.get("CAPTCHA_API_KEY", "")
    if not creds.get("captcha_api_key"):
        raise SystemExit("CAPTCHA_API_KEY missing from .env -- the detail gate needs a "
                         "2Captcha Turnstile solve")

    counties = [c.strip() for c in args.counties.split(",") if c.strip()]
    csv_path = Path(args.csv)
    out_path = Path(args.out) if args.out else csv_path.with_name(csv_path.stem + "_full.csv")

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if "full_text" not in fieldnames:
        fieldnames.append("full_text")

    results = fetch_all(creds, args.saved_search, counties, args.days,
                        args.max_pages, args.limit, args.headed)
    print(f"fetched {len(results)} detail page(s)")

    matched = 0
    for r in rows:
        key = _row_key(r.get("street", ""), r.get("zip", ""), r.get("notice_text_snippet", ""))
        d = results.get(key)
        if not d:
            continue
        matched += 1
        r["full_text"] = d["full_text"]
        if d["notice_id"]:
            r["notice_id"] = d["notice_id"]
        # The full-text re-mine WINS over the snippet-era value: the snippet
        # parse has no deed-date guard, so its value can be years stale.
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
        print(f"NOTE: {len(results) - matched} fetched detail(s) matched no CSV row "
              f"(off-footprint/off-type rows the pull dropped, or a key mismatch)")


if __name__ == "__main__":
    main()
