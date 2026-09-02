"""Cross-check the API lane counts against the Records panel's own count.

`dpd_lane_assignee_report.py` counts a lane by translating the STORED preset
filter into a query payload (`to_query`). That translation is the one place the
report could be confidently wrong: the store and the query speak different
shapes (the store keeps a county as an object, the query wants a string), so a
mistranslation would return a plausible number rather than an error.

This is the independent witness -- load the same preset in the UI, apply it,
and read what the app itself displays. Agreement means the translation is
faithful. Disagreement is reported, not reconciled.

TWO THINGS THE UI DOES NOT SAY OUT LOUD, both of which produced a false
MISMATCH first time round:

  * There is NO record total on the page. The only number is the paginator's
    "of N", and N is a count of PAGES at 10 rows each -- so a lane of 1040
    records renders as "of 104". Reading it as a record count under-reports by
    10x and looks like a catastrophic disagreement.
  * The default "Clean" tab silently hides "Incomplete" records (already known
    here -- it cost 1 of 10 records in doors_per_deal_bulk_assign). Clean
    showed "of 90" against the API's 1040; the "All" tab showed "of 104".
    The comparison is only valid on All.

So the check is: click All, read the page count P, and require the API count to
fall in [10*(P-1)+1, 10*P] -- the only record range that renders as P pages.

Read-only: loads a preset and applies a filter. No Save, no Save New, no
Add Records, no Save-Filters checkbox.

Usage:
    python src/scripts/dpd_lane_panel_crosscheck.py [--headed]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dotenv import dotenv_values  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

from datasift_core import login  # noqa: E402
from dpd_presets_create import (BASE, dismiss_popups, expand_presets_section,  # noqa: E402
                                _ensure_folder_open, open_panel)

REPORT = ROOT / "output" / "dpd_lane_assignee_report.json"
OUT = ROOT / "output" / "dpd_lane_panel_crosscheck.json"

LANES = [("01 HOTTEST - CALL", "Hottest - 02 Ready to Call"),
         ("05 FTM - CALL", "FTM - 02 Ready to Call")]

# Loading a preset = a JS click on the row's TITLE element (a coordinate mouse
# click at the title centre does not load -- dpd_presets_create, 2026-08-31).
_LOAD_ROW_JS = r"""(text) => {
    const b = document.querySelector('[class*="PresetsBelowBody"]');
    if (!b) return false;
    for (const el of b.querySelectorAll('[class*="CollapsibleFolderPresetTitle"]')) {
        const t = (el.innerText || '').trim().split('\n')[0].trim();
        if (!t) continue;
        if (!(t === text || t.startsWith(text) || text.startsWith(t))) continue;
        el.scrollIntoView({block: 'center'});
        el.click();
        return true;
    }
    return false;
}"""

# The default "Clean" tab hides Incomplete records; switching to "All" is a
# pure view toggle (no write) and is the only tab the API count can be
# compared against.
_ALL_TAB_JS = r"""() => {
    for (const el of document.querySelectorAll('*')) {
        if (el.children.length) continue;
        if ((el.textContent || '').trim() === 'All') { el.click(); return true; }
    }
    return false;
}"""

_APPLY_JS = """() => {
    for (const b of document.querySelectorAll('button, a, div')) {
        const t = (b.innerText || '').trim();
        if (/^apply filters?$/i.test(t)) { b.click(); return true; }
    }
    return false;
}"""

# The paginator's "of N" -- N is PAGES, not records (10 rows per page).
_COUNT_JS = r"""() => {
    const hits = [];
    document.querySelectorAll('*').forEach(el => {
        if (el.children.length) return;
        const t = (el.textContent || '').trim();
        if (!t || t.length > 60) return;
        if (!/[0-9]/.test(t)) return;
        if (!/record|result|of\s|properties/i.test(t)) return;
        const r = el.getBoundingClientRect();
        if (r.width === 0) return;
        hits.push({t, y: Math.round(r.y), x: Math.round(r.x)});
    });
    return hits;
}"""


ROWS_PER_PAGE = 10


def parse_pages(hits: list[dict]) -> tuple[int | None, list[str]]:
    """Read the paginator's page count. Returns PAGES, not records."""
    texts = [h["t"] for h in hits]
    for t in texts:
        m = re.search(r"of\s+([\d,]+)", t, re.I)
        if m:
            return int(m.group(1).replace(",", "")), texts
    return None, texts


def page_range(pages: int) -> tuple[int, int]:
    """The only record range that renders as `pages` pages."""
    return ROWS_PER_PAGE * (pages - 1) + 1, ROWS_PER_PAGE * pages


async def run(headless: bool) -> int:
    api_counts = {l["preset"]: l["count"]
                  for l in json.loads(REPORT.read_text(encoding="utf-8"))["lanes"]}
    env = dotenv_values(str(ROOT / ".env"))
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await ctx.new_page()
        try:
            if not await login(page, env.get("DATASIFT_EMAIL"), env.get("DATASIFT_PASSWORD")):
                print("login failed")
                return 2
            for folder, preset in LANES:
                row = {"folder": folder, "preset": preset,
                       "api_count": api_counts.get(preset)}
                # Fresh navigation each time: after a load the panel sits in a
                # state where the folder list is not rendered at all.
                await page.goto(f"{BASE}/records", wait_until="domcontentloaded")
                await page.wait_for_timeout(6000)
                await dismiss_popups(page)
                if not await open_panel(page):
                    row["status"] = "filter panel would not open"
                    results.append(row); continue
                await expand_presets_section(page)
                if not await _ensure_folder_open(page, folder, preset):
                    row["status"] = "row not visible and folder would not expand"
                    results.append(row); continue
                if not await page.evaluate(_LOAD_ROW_JS, preset):
                    row["status"] = "row title element not found"
                    results.append(row); continue
                await page.wait_for_timeout(4500)
                row["applied"] = await page.evaluate(_APPLY_JS)
                await page.wait_for_timeout(9000)
                await dismiss_popups(page)
                # "Clean" is the default tab and hides Incomplete records, so
                # the comparison is only valid on "All". Pure view toggle.
                row["all_tab"] = await page.evaluate(_ALL_TAB_JS)
                await page.wait_for_timeout(8000)
                pages, texts = parse_pages(await page.evaluate(_COUNT_JS))
                row["panel_pages"] = pages
                row["panel_texts"] = texts[:12]
                await page.screenshot(path=f"dpd_crosscheck_{folder[:2]}.png")
                if pages is None:
                    row["status"] = "no paginator text found (screenshot written)"
                else:
                    lo, hi = page_range(pages)
                    row["implied_records"] = [lo, hi]
                    if lo <= (row["api_count"] or -1) <= hi:
                        row["status"] = "MATCH"
                    else:
                        row["status"] = (f"MISMATCH api={row['api_count']} outside "
                                         f"{lo}..{hi} implied by {pages} pages")
                print(f"{preset}: api={row['api_count']}  panel={pages} pages "
                      f"-> {row['status']}", flush=True)
                results.append(row)
        finally:
            await browser.close()

    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT}")
    return 0 if all(r.get("status") == "MATCH" for r in results) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    return asyncio.run(run(headless=not ap.parse_args().headed))


if __name__ == "__main__":
    sys.exit(main())
