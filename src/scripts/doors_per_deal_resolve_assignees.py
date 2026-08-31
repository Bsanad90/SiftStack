"""One-off investigation: read a record's CURRENT assignee name in the live
REIsift UI, without clicking into the dropdown at all (pure read of the
rendered control's displayed text -- for an assigned record this shows the
person's name directly instead of the "Assign to user..." placeholder, so no
dropdown interaction is needed). No internal-API endpoint exposes users/team
members (all guessed endpoints 404'd), so this is the only way to map an
assigned_to uuid to a name.

Usage:
    python src/scripts/doors_per_deal_resolve_assignees.py \
        --uuid <property-uuid> --address "123 Main St" --owner-name "Jane Doe"
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import dotenv_values
from playwright.async_api import async_playwright

from datasift_core import login, dismiss_popups, DATASIFT_RECORDS_URL  # noqa: E402

# Full account user roster, read live from an unassigned record's dropdown
# (2026-08-24) -- no users/team API exists on this account.
KNOWN_USERS = [
    "Ahmed Galal", "Mariam Mohamed", "Moe Galal", "Mohammed Hisham",
    "Mohammed Bagoury", "Mostafa Hisham", "Pal John", "Shaddy El Ramly",
]


async def read_current_assignee(uuid: str, address: str, owner_name: str, headless: bool) -> dict:
    env = dotenv_values(str(ROOT / ".env"))
    email, pw = env.get("DATASIFT_EMAIL"), env.get("DATASIFT_PASSWORD")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()
        try:
            if not await login(page, email, pw):
                return {"success": False, "message": "login failed"}

            await page.goto(DATASIFT_RECORDS_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            await dismiss_popups(page)

            search = page.locator('input[placeholder*="Search for records"]')
            if await search.count() > 0:
                await search.first.click()
                await search.first.fill(address)
                await page.wait_for_timeout(1000)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(2500)
            await dismiss_popups(page)

            # Clicking the owner-name link lands on the Owner Details page
            # (verified live), not the property directly -- then click the
            # property row on that page to reach the actual property detail
            # panel where Assignee lives.
            if owner_name:
                link = page.get_by_text(owner_name, exact=True)
            else:
                link = page.get_by_text(address, exact=False)
            if await link.count() == 0:
                await page.screenshot(path=f"dpd_assignee_no_row_{uuid[:8]}.png")
                return {"success": False, "message": "record not found after search"}
            await link.first.click(force=True)
            await page.wait_for_timeout(3000)
            await dismiss_popups(page)

            if owner_name:
                prop_row = page.get_by_text(address, exact=False).first
                if await prop_row.count() > 0:
                    await prop_row.click(force=True)
                    await page.wait_for_timeout(2500)
                    await dismiss_popups(page)

            await page.screenshot(path=f"dpd_assignee_panel_{uuid[:8]}.png")

            # Pure read -- no click. The visible assignee select near the top
            # of the panel shows the assigned person's name directly when set
            # (verified live: "Mostafa Hisham" rendered right next to the
            # edit/mail/home icon buttons, around y=110-135, x=705-915 in a
            # 1440-wide viewport). BUG FOUND live: the logged-in user's own
            # name ("Moe Galal") also always appears in the top-right account
            # badge at y~32-48, x~1250-1400 -- a naive "topmost match wins"
            # search picks that header badge every time instead of the real
            # assignee control, since it's higher on the page than any
            # record-specific element. Excluding y<80 (below the header) and
            # x>1050 (left of the header badge) fixes it.
            current_text = None
            best_y = None
            for name in KNOWN_USERS:
                loc = page.get_by_text(name, exact=True)
                for i in range(await loc.count()):
                    box = await loc.nth(i).bounding_box()
                    if not box or box["y"] < 80 or box["x"] > 1050:
                        continue
                    if best_y is None or box["y"] < best_y:
                        best_y = box["y"]
                        current_text = name
            if current_text is None:
                current_text = "Assign to user... (unassigned)"

            return {
                "success": True,
                "uuid": uuid,
                "current_assignee_text": current_text,
                "url": page.url,
            }
        finally:
            await browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uuid", required=True)
    ap.add_argument("--address", required=True)
    ap.add_argument("--owner-name", default="")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    res = asyncio.run(read_current_assignee(args.uuid, args.address, args.owner_name, headless=not args.headed))
    print(res)


if __name__ == "__main__":
    main()
