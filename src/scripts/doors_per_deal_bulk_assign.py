"""Bulk-assign a dialer's tagged sub-batch to them in REIsift.

Flow (all new, verified live 2026-08-24 -- not documented anywhere before
this): filter records by the dialer-specific tag -> switch to the "All" tab
(the default "Clean" tab silently hides "Incomplete" records, which cost 1 of
Mostafa Hisham's 10 in testing) -> select all matching -> Manage -> "Assign to
user" -> open the "Update assignee" modal's dropdown -> pick the exact name ->
"Save and assign".

A hardcoded expected-count guard (matching mddc_browser_pipeline.py's
--confirm-send pattern) refuses to proceed if the modal's own "ASSIGN N
PROPERTIES TO SOMEONE" count doesn't match --expect-count, so a stale filter
or a partial tag upload can't silently assign the wrong scope.

Usage:
    python src/scripts/doors_per_deal_bulk_assign.py \
        --tag "Dialer - Mostafa Hisham - Doors Per Deal" \
        --assignee "Mostafa Hisham" --expect-count 10 --confirm
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dotenv import dotenv_values
from playwright.async_api import async_playwright

from datasift_core import login  # noqa: E402
from datasift_uploader import _navigate_to_records, _dismiss_popups, _screenshot  # noqa: E402
from mddc_browser_pipeline import _filter_by_tag, _select_all_matching  # noqa: E402


async def do_bulk_assign(tag: str, assignee: str, expect_count: int, confirm: bool, headless: bool) -> dict:
    env = dotenv_values(str(ROOT / ".env"))
    email, pw = env.get("DATASIFT_EMAIL"), env.get("DATASIFT_PASSWORD")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        try:
            if not await login(page, email, pw):
                return {"success": False, "message": "login failed"}

            await _navigate_to_records(page)
            await _dismiss_popups(page)

            if not await _filter_by_tag(page, tag):
                return {"success": False, "message": f"could not filter by tag {tag!r}"}
            await page.wait_for_timeout(2000)
            await _dismiss_popups(page)

            # "Clean" tab (default) silently hides "Incomplete" records --
            # verified live: cost 1 of 10 on Mostafa Hisham's batch. "All"
            # catches everything the tag actually landed on.
            all_tab = page.locator('text="All"').first
            if await all_tab.count() > 0:
                await all_tab.click(force=True)
                await page.wait_for_timeout(2000)
                await _dismiss_popups(page)

            sel = await _select_all_matching(page)
            if not sel.get("success"):
                return {"success": False, "message": f"select-all-matching failed: {sel.get('message')}"}

            if str(expect_count) not in sel["message"]:
                await _screenshot(page, f"bulk_assign_unexpected_count_{assignee.replace(' ', '_')}")
                return {"success": False,
                        "message": f"expected {expect_count}, got {sel['message']!r} -- stopping rather than "
                                    "risk assigning the wrong scope."}

            manage_btn = page.locator('button:has-text("Manage")')
            if await manage_btn.count() == 0:
                return {"success": False, "message": "Manage button not found"}
            await manage_btn.first.click(force=True)
            await page.wait_for_timeout(1000)

            assign_opt = page.locator('text="Assign to user"')
            if await assign_opt.count() == 0:
                await _screenshot(page, "bulk_assign_no_menu_option")
                return {"success": False, "message": "'Assign to user' option not found in Manage menu"}
            await assign_opt.first.click(force=True)
            await page.wait_for_timeout(1500)
            await _screenshot(page, f"bulk_assign_modal_{assignee.replace(' ', '_')}")

            # Verify the modal's own count line before touching anything. The
            # "ASSIGN N PROPERTIES..." text renders uppercase via CSS but is
            # lowercase in the DOM (same pattern as the "Owner" column header
            # elsewhere in this account) -- match case-insensitively.
            count_line = page.locator('text=/assign \\d+ propert/i')
            if await count_line.count() == 0:
                await _screenshot(page, f"bulk_assign_no_count_line_{assignee.replace(' ', '_')}")
                return {"success": False, "message": "could not find the modal's own count line -- aborting "
                                                        "rather than proceed unverified"}
            modal_text = await count_line.first.text_content()
            if str(expect_count) not in (modal_text or ""):
                return {"success": False,
                        "message": f"modal says {modal_text!r}, expected {expect_count} -- aborting"}

            dropdown = page.locator('text="Clear assignee"')
            if await dropdown.count() == 0:
                return {"success": False, "message": "assignee dropdown not found in modal"}
            await dropdown.first.click(force=True)
            await page.wait_for_timeout(1000)
            await _screenshot(page, f"bulk_assign_dropdown_open_{assignee.replace(' ', '_')}")

            option = page.get_by_text(assignee, exact=True)
            if await option.count() == 0:
                return {"success": False, "message": f"assignee {assignee!r} not found in dropdown options"}
            await option.first.click(force=True)
            await page.wait_for_timeout(1000)
            await _screenshot(page, f"bulk_assign_selected_{assignee.replace(' ', '_')}")

            if not confirm:
                return {"success": True,
                        "message": f"DRY RUN: would assign {expect_count} properties to {assignee} -- "
                                    "re-run with --confirm to actually save"}

            save_btn = page.locator('button:has-text("Save and assign")')
            if await save_btn.count() == 0:
                return {"success": False, "message": "'Save and assign' button not found"}
            await save_btn.first.click(force=True)
            await page.wait_for_timeout(3000)
            await _screenshot(page, f"bulk_assign_done_{assignee.replace(' ', '_')}")

            return {"success": True, "message": f"Assigned {expect_count} properties to {assignee}"}
        finally:
            await browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--assignee", required=True)
    ap.add_argument("--expect-count", type=int, required=True)
    ap.add_argument("--confirm", action="store_true", help="Actually click Save and assign (default: dry run)")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    res = asyncio.run(do_bulk_assign(
        args.tag, args.assignee, args.expect_count, args.confirm, headless=not args.headed
    ))
    print(res)
    return 0 if res.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
