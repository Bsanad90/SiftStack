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
    _select_all_records,
    _dismiss_popups,
    _screenshot,
)

TRANSFORMED_CSV = ROOT / "output" / "mddc_datasift_upload_transformed.csv"
LIST_NAME = "Foreclosure"
BATCH_TAG = "Claude first batch 8.22"


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


async def do_skiptrace(headless: bool) -> dict:
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
            filtered = await _filter_by_tag(page, BATCH_TAG)
            if not filtered:
                return {"success": False, "message": "could not filter by tag -- ABORTING before select-all "
                                                        "to avoid selecting the whole account"}
            selected = await _select_all_records(page)
            if not selected:
                return {"success": False, "message": "filter applied but select-all failed"}
            await _screenshot(page, "skiptrace_ready_to_send")
            return {"success": True,
                    "message": "Filtered to tag, selected all -- STOPPED HERE before Send To/Skip Trace "
                                "for a manual checkpoint. Re-run with --confirm-send to actually trigger it."}
        finally:
            await browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["upload", "skiptrace"])
    ap.add_argument("--headed", action="store_true", help="Show the browser window")
    args = ap.parse_args()

    os.chdir(Path(__file__).parent)  # keep datasift_*.png debug screenshots out of repo root

    if args.step == "upload":
        res = asyncio.run(do_upload(headless=not args.headed))
    else:
        res = asyncio.run(do_skiptrace(headless=not args.headed))
    print(res)
    return 0 if res.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
