"""Browser-automation REIsift pipeline for the MDDC trustee-sale batch.

The internal API path (datasift_api_upload.py) 403'd on POST /property/ for
this account (read access works, write does not -- a role/plan restriction,
not a code bug), so this drives the actual web UI via Playwright instead,
reusing datasift_uploader.py's proven wizard-automation functions.

Every action below is scoped by the "Claude first batch 8.22" TAG, never by
the "Foreclosure" LIST alone. That list already exists on this account with
real prior activity (not an empty template), so a list-only filter would
sweep in records that have nothing to do with this batch. datasift_uploader's
own skip_trace_records()/enrich_records() only filter by list -- this module
adds a tag-based variant instead of touching that file.

Usage:
    python src/scripts/mddc_browser_pipeline.py upload
    python src/scripts/mddc_browser_pipeline.py skiptrace
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import dotenv_values
from playwright.async_api import Page, async_playwright

from datasift_core import login  # noqa: E402
from datasift_uploader import (  # noqa: E402
    upload_csv,
    _navigate_to_records,
    _dismiss_popups,
    _screenshot,
)
# One source of truth for the batch tag: the upload stamps it, this pipeline filters on it.
# (They diverged 2026-08-28..31 -- upload moved to "FTM" while this file still said
# "Claude first batch 8.22", which would have made the skip-trace filter match nothing.)
from mddc_datasift_upload import BATCH_TAG  # noqa: E402

TRANSFORMED_CSV = ROOT / "output" / "mddc_datasift_upload_transformed.csv"
LIST_NAME = "Foreclosure"


async def _filter_by_tag(page: Page, tag_name: str) -> bool:
    """Filter records by TAG (not list). Mirrors datasift_uploader._filter_by_list
    exactly, but searches the filter-block picker for "Tags" instead of "Lists"
    -- this account's "Foreclosure" list already has real unrelated records in
    it, so tag is the only filter precise enough to select just this batch.
    """
    try:
        await _dismiss_popups(page)

        filter_link = page.locator('#Records__Filters_Trigger')
        if await filter_link.count() == 0:
            filter_link = page.locator('a:has-text("Filter Records")')
        if await filter_link.count() == 0:
            print("No Filter Records link found")
            return False
        await filter_link.first.click()
        await page.wait_for_timeout(2000)
        await _dismiss_popups(page)
        await _screenshot(page, "tagfilter_opened")

        filter_search = page.locator('#RecordsFilters__Filter_Blocks__Search')
        if await filter_search.count() == 0:
            filter_search = page.locator('input[placeholder*="filter block"]')
        if await filter_search.count() == 0:
            print("Filter block search input not found")
            return False
        await filter_search.first.click()
        await filter_search.first.fill("Tags")
        await page.wait_for_timeout(1500)

        all_tags = page.locator('text="All Tags (AND)"')
        if await all_tags.count() > 0:
            await all_tags.first.click()
        else:
            any_tags = page.locator('text="Any Tags (OR)"')
            if await any_tags.count() > 0:
                await any_tags.first.click()
        await page.wait_for_timeout(2000)
        await _screenshot(page, "tagfilter_block_added")

        tag_search = page.locator('input[placeholder*="Search for tags"], input[placeholder*="Search tags"]')
        if await tag_search.count() == 0:
            print("Tag search input not found")
            return False
        await tag_search.first.fill(tag_name)
        await page.wait_for_timeout(2000)
        await _screenshot(page, "tagfilter_searched")

        tag_option = page.locator(f'text="{tag_name}"')
        if await tag_option.count() == 0:
            print(f"No matching tag option found for '{tag_name}'")
            return False
        await tag_option.last.click()
        await page.wait_for_timeout(1000)
        await _screenshot(page, "tagfilter_selected")

        apply_btn = page.locator('text="Apply Filters"')
        if await apply_btn.count() > 0:
            await apply_btn.first.click()
            await page.wait_for_timeout(3000)
        else:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(2000)

        await _screenshot(page, "tagfilter_applied")
        return True
    except Exception as e:
        print(f"Filter by tag failed: {e}")
        await _screenshot(page, "tagfilter_failed")
        return False


async def do_upload(headless: bool) -> dict:
    env = dotenv_values(str(ROOT / ".env"))
    email, pw = env.get("DATASIFT_EMAIL"), env.get("DATASIFT_PASSWORD")
    if not email or not pw:
        return {"success": False, "message": "DATASIFT_EMAIL/DATASIFT_PASSWORD not set"}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        try:
            if not await login(page, email, pw):
                return {"success": False, "message": "login failed"}
            result = await upload_csv(
                page, TRANSFORMED_CSV,
                mode="add", list_name=LIST_NAME, existing_list=True,
                custom_tag=BATCH_TAG,
            )
            return result
        finally:
            await browser.close()


async def _select_all_matching(page: Page) -> dict:
    """Click the header checkbox's dropdown caret and choose "Select all (N)",
    not just the ~13 visible on the current page.

    REIsift's records grid paginates at ~13/page with no "select all across
    pages" behavior on a plain header-checkbox click (verified live
    2026-08-22: clicking the checkbox alone only ever selects the current
    page). The checkbox has a small dropdown caret immediately to its right
    that opens "Choose selection: Select visible (N) / Select all (N) /
    Select <custom number> Records" -- "Select all (N)" is the one that
    actually covers every page matching the current filter.
    """
    # The header checkbox+caret cluster is NOT a plain <input type="checkbox">
    # (verified live 2026-08-22: every real <input type="checkbox"> on this
    # page is a row checkbox, ~70px below the header row -- the header
    # control must be a custom-drawn component). The "Owner" column label
    # (an exact-text DIV, case is literally "Owner" despite rendering as
    # "OWNER" via CSS text-transform) is reliably findable instead; the
    # checkbox+caret sit a fixed offset to its left.
    header_pos = await page.evaluate("""() => {
        const allEls = document.querySelectorAll('*');
        for (const el of allEls) {
            if (el.textContent.trim().toUpperCase() === 'OWNER' && el.children.length === 0) {
                const rect = el.getBoundingClientRect();
                return {x: rect.left, y: rect.top + rect.height / 2};
            }
        }
        return null;
    }""")
    if not header_pos:
        await _screenshot(page, "select_all_matching_no_header")
        return {"success": False, "message": "could not locate the Owner column header"}

    # Caret sits ~17px left of the "Owner" label's left edge (verified live).
    await page.mouse.click(header_pos["x"] - 17, header_pos["y"])
    await page.wait_for_timeout(1500)
    await _screenshot(page, "select_dropdown_opened")

    select_all_opt = page.locator('text=/Select all \\(\\d+\\)/')
    if await select_all_opt.count() == 0:
        return {"success": False, "message": "'Select all (N)' option not found in dropdown"}
    label = (await select_all_opt.first.text_content()) or ""
    await select_all_opt.first.click()
    await page.wait_for_timeout(1500)
    await _screenshot(page, "select_all_matching_done")
    return {"success": True, "message": label.strip()}


async def do_skiptrace(headless: bool, confirm_send: bool,
                       expect_count: int | None = None) -> dict:
    env = dotenv_values(str(ROOT / ".env"))
    email, pw = env.get("DATASIFT_EMAIL"), env.get("DATASIFT_PASSWORD")
    if not email or not pw:
        return {"success": False, "message": "DATASIFT_EMAIL/DATASIFT_PASSWORD not set"}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await context.new_page()
        try:
            if not await login(page, email, pw):
                return {"success": False, "message": "login failed"}
            await _navigate_to_records(page)

            # "Clean" is the default tab and can hide records DataSift scores
            # as incomplete -- every record in this batch has no owner name,
            # so it's exactly the kind of record that tab could exclude.
            all_tab = page.locator('text="All"').first
            if await all_tab.count() > 0:
                await all_tab.click()
                await page.wait_for_timeout(2000)

            filtered = await _filter_by_tag(page, BATCH_TAG)
            if not filtered:
                return {"success": False, "message": "could not filter by tag -- ABORTING before select-all "
                                                        "to avoid selecting the whole account"}

            await _dismiss_popups(page)
            await page.wait_for_timeout(2500)
            await _dismiss_popups(page)
            sel = await _select_all_matching(page)
            if not sel.get("success"):
                return {"success": False, "message": f"select-all-matching failed: {sel.get('message')}"}
            print(f"Selection: {sel['message']}")
            # Scope gate: with --expect-count the selected-record count must match exactly
            # (was a hardcoded "132"/"133" substring check from the 2026-08-22 batch, which
            # aborted every later run and would also have passed "1132").
            if expect_count is not None:
                m = re.search(r"([\d,]+)", sel["message"] or "")
                got = int(m.group(1).replace(",", "")) if m else None
                if got != expect_count:
                    await _screenshot(page, "skiptrace_unexpected_count")
                    return {"success": False,
                            "message": f"Expected {expect_count} records selected, got "
                                       f"{sel['message']!r} -- stopping rather than risk "
                                       "sending the wrong scope to Skip Trace."}

            if not confirm_send:
                await _screenshot(page, "skiptrace_ready_to_send")
                return {"success": True, "message": f"Verified selection ({sel['message']}) -- "
                                                       "re-run with --confirm-send to actually trigger Skip Trace."}

            # ---- Send To -> Skip Trace -> agree -> confirm ----
            send_to_btn = page.locator('button:has-text("Send To"), button:has-text("Send to")')
            if await send_to_btn.count() == 0:
                send_to_btn = page.locator('text="Send To"')
            if await send_to_btn.count() == 0:
                await _screenshot(page, "skip_no_send_to")
                return {"success": False, "message": "Could not find 'Send To' button"}
            await send_to_btn.first.click()
            await page.wait_for_timeout(1500)

            skip_option = page.locator('text="Skip Trace"')
            if await skip_option.count() == 0:
                skip_option = page.locator('text="Skip trace"')
            if await skip_option.count() == 0:
                await _screenshot(page, "skip_no_option")
                return {"success": False, "message": "Could not find 'Skip Trace' option in Send To menu"}
            await skip_option.first.click()
            await page.wait_for_timeout(2000)
            await _screenshot(page, "skip_modal")

            agree_btn = page.locator('button:has-text("I Agree with the terms")')
            if await agree_btn.count() == 0:
                agree_btn = page.locator('button:has-text("I Agree")')
            if await agree_btn.count() == 0:
                await _screenshot(page, "skip_no_agree")
                return {"success": False, "message": "Could not find 'I Agree with the terms' button"}
            await agree_btn.first.click()
            await page.wait_for_timeout(2000)
            await _screenshot(page, "skip_review_step")

            for btn_text in ["Skip Trace", "Skip Trace Records", "Start Skip Trace", "Submit", "Confirm", "Process"]:
                skip_btn = page.locator(f'button:has-text("{btn_text}")')
                if await skip_btn.count() > 0:
                    await skip_btn.first.click()
                    await page.wait_for_timeout(3000)
                    break
            else:
                await _screenshot(page, "skip_no_button")
                return {"success": False, "message": "Could not find skip trace submit button"}

            await _screenshot(page, "skip_submitted")
            return {"success": True,
                    "message": f"Skip trace started for {sel['message']} -- track progress in "
                                "Activity -> Skip Trace tab"}
        finally:
            await browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["upload", "skiptrace"])
    ap.add_argument("--headed", action="store_true", help="Show the browser window")
    ap.add_argument("--confirm-send", action="store_true",
                     help="skiptrace only: actually click Send To -> Skip Trace. Without this, "
                          "stops after verifying the selection count as a safety checkpoint.")
    ap.add_argument("--expect-count", type=int, default=None,
                     help="skiptrace only: abort unless exactly this many records are selected "
                          "after the tag filter. Omit to skip the count gate.")
    args = ap.parse_args()

    os.chdir(Path(__file__).parent)  # keep datasift_*.png debug screenshots out of repo root

    if args.step == "upload":
        res = asyncio.run(do_upload(headless=not args.headed))
    else:
        res = asyncio.run(do_skiptrace(headless=not args.headed, confirm_send=args.confirm_send,
                                       expect_count=args.expect_count))
    print(res)
    return 0 if res.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
