"""Batch-tag a Doors-Per-Deal opportunity CSV in REIsift.

This account's internal API 403s on writes (read-only), so this drives the
same proven Playwright wizard `mddc_browser_pipeline.py` uses. Generalized
from the original one-off (which uploaded the full 1250 as a NEW list+tag
both named "1250 D/D Opportunities") to also handle the per-dialer sub-batch
tags, which reuse that same existing list (--existing-list) so we don't spawn
5 more spurious lists, just distinct tags.

Usage:
    # Original full-cohort upload (creates the list)
    python src/scripts/doors_per_deal_batch_tag_upload.py \
        --csv output/doors_per_deal_batch_tag.csv --tag "1250 D/D Opportunities"

    # Per-dialer sub-batch (reuses the existing list, new tag only)
    python src/scripts/doors_per_deal_batch_tag_upload.py \
        --csv output/doors_per_deal_dialer_tag_Pal_John.csv \
        --tag "Dialer - Pal John - Doors Per Deal" \
        --list-name "1250 D/D Opportunities" --existing-list
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

from datasift_core import login  # noqa: E402
from datasift_uploader import upload_csv  # noqa: E402


async def do_upload(csv_path: Path, tag: str, list_name: str, existing_list: bool, headless: bool) -> dict:
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
                page, csv_path,
                mode="add", list_name=list_name, existing_list=existing_list,
                custom_tag=tag,
            )
            return result
        finally:
            await browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--list-name", default=None, help="Defaults to --tag if not set")
    ap.add_argument("--existing-list", action="store_true",
                     help="Target an existing list instead of creating a new one")
    ap.add_argument("--headed", action="store_true", help="Show the browser window")
    args = ap.parse_args()

    list_name = args.list_name or args.tag
    res = asyncio.run(do_upload(Path(args.csv), args.tag, list_name, args.existing_list, headless=not args.headed))
    print(res)
    return 0 if res.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
