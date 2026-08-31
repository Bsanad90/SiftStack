"""Read-only probe of the Records filter panel: the preset and folder creation controls.

Phase 5 has to create 12 folders and 73 presets, and NO code in this repo creates either
today -- `datasift_uploader` only loads and overwrites an existing preset, and `Save New`
appears once in the whole repo as a comment. Driving a styled-component panel from guessed
selectors is how you get a run that reports 73 successes having clicked nothing, so the
panel is photographed and dumped first.

    python src/scripts/dpd_preset_probe.py --headless

Writes screenshots to output/dpd_shots/ and a control dump to output/dpd_preset_probe.json.
Touches nothing on the account.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from datasift_core import (  # noqa: E402
    create_browser, dismiss_popups, get_credentials, login,
)

BASE = "https://app.reisift.io"
SHOTS = ROOT / "output" / "dpd_shots"
OUT = ROOT / "output" / "dpd_preset_probe.json"


async def _dump(page, label: str) -> dict:
    """Every visible control right of the left nav (which ends at x=244)."""
    return await page.evaluate(
        """(label) => {
        const btns = [], ins = [];
        for (const b of document.querySelectorAll('button,a,[role="button"]')) {
            const r = b.getBoundingClientRect();
            if (r.width === 0 || r.height === 0 || r.x < 300) continue;
            const t = (b.innerText || b.getAttribute('aria-label') || '').trim();
            if (!t || t.length > 40) continue;
            btns.push({t, x: Math.round(r.x), y: Math.round(r.y),
                       d: !!b.disabled});
        }
        for (const i of document.querySelectorAll('input:not([type=hidden])')) {
            const r = i.getBoundingClientRect();
            if (r.width === 0 || r.x < 300) continue;
            ins.push({ph: i.placeholder || '', id: i.id || '', name: i.name || '',
                      x: Math.round(r.x), y: Math.round(r.y)});
        }
        return {label, buttons: btns, inputs: ins};
    }""", label)


async def main_async(headless: bool) -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    email, password = get_credentials()
    out: dict = {}
    async with create_browser(headless=headless) as (_b, _c, page):
        if not await login(page, email, password) or "/login" in page.url:
            print("LOGIN FAILED")
            return 2
        await page.goto(f"{BASE}/records", wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        await dismiss_popups(page)

        # Open the filter panel.
        opened = False
        for sel in ("#Records__Filters_Trigger", '[id*="Filters_Trigger"]'):
            el = page.locator(sel)
            if await el.count():
                try:
                    await el.first.click(timeout=6000)
                except Exception:  # noqa: BLE001
                    box = await el.first.bounding_box()
                    if box:
                        await page.mouse.click(box["x"] + box["width"] / 2,
                                               box["y"] + box["height"] / 2)
                opened = True
                break
        if not opened:
            print("could not find the filter trigger")
            return 3
        await page.wait_for_timeout(3000)
        await dismiss_popups(page)
        out["panel_open"] = await _dump(page, "panel_open")
        await page.screenshot(path=str(SHOTS / "panel_open.png"), full_page=True)

        # "Filter Presets" is a COLLAPSED section at the BOTTOM of the panel, below a
        # scroll boundary. Scroll the panel's own scrollable container, not the viewport.
        await page.evaluate(
            """() => {
            for (const el of document.querySelectorAll('div')) {
                const r = el.getBoundingClientRect();
                if (r.x < 300 || r.width < 250) continue;
                if (el.scrollHeight > el.clientHeight + 50) el.scrollTop = el.scrollHeight;
            }
        }""")
        await page.wait_for_timeout(1500)
        # Then click the section heading to expand it.
        expanded = await page.evaluate(
            """() => {
            for (const el of document.querySelectorAll('div,span,button,h1,h2,h3,h4')) {
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.x < 300) continue;
                const t = (el.innerText || '').trim();
                if (t === 'Filter Presets' || t === 'Filter presets') {
                    el.scrollIntoView({behavior: 'instant', block: 'center'});
                    return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                }
            }
            return null;
        }""")
        if expanded:
            await page.wait_for_timeout(800)
            box = await page.evaluate(
                """() => {
                for (const el of document.querySelectorAll('div,span,button,h1,h2,h3,h4')) {
                    const r = el.getBoundingClientRect();
                    if (r.width === 0 || r.x < 300) continue;
                    if ((el.innerText || '').trim().startsWith('Filter Preset')) {
                        return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                    }
                }
                return null;
            }""")
            if box:
                await page.mouse.click(box["x"], box["y"])
                await page.wait_for_timeout(2500)
        out["presets_expanded"] = await _dump(page, "presets_expanded")
        await page.screenshot(path=str(SHOTS / "panel_presets.png"), full_page=True)

        print("\n=== FILTER PANEL, presets section ===")
        for b in out["presets_expanded"]["buttons"]:
            print(f"  BTN {b['t']!r:34s} x={b['x']:>4} y={b['y']:>4}"
                  f"{'  DISABLED' if b['d'] else ''}")
        for i in out["presets_expanded"]["inputs"]:
            print(f"  IN  ph={i['ph']!r:30s} id={i['id']!r} x={i['x']} y={i['y']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote {OUT} and screenshots to {SHOTS}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    return asyncio.run(main_async(a.headless))


if __name__ == "__main__":
    raise SystemExit(main())
