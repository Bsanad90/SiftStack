"""Phase 5c: the three things still unknown before the 73 presets can be built.

`dpd_filter_blocks.py` mapped the 144-block vocabulary and found that every value block
carries an `Include` / `Do not include` toggle. Three questions it could not answer, each
of which would silently produce a wrong preset if guessed:

  Q1  WHERE IS THE SAVE BAR? Scanning for `Save` / `Save New` as buttons returned nothing
      on all 13 probed blocks, and scanning every leaf element returned nothing either.
      The likely answer is that the preset action bar lives inside the COLLAPSED
      `Filter Presets` section at the bottom of the panel, not next to the blocks -- so
      it has to be expanded first. Reporting "no save bar" without expanding it would be
      the same mistake as reading `/lists` before expanding its folders.

  Q2  IS EVERY DEAD STATUS ACTUALLY SELECTABLE? The Property Status picker rendered 19 of
      this account's 38 statuses. Seven of the sixteen in `DEAD_STATUSES` were not among
      them (Opt-out, Lost Deal, Close Out, Buyer, Buyer Found, Buyer Lost, Auction Date
      Passed). That is either a scroll window or a genuinely shorter list, and the
      difference matters: a suppression naming a status the picker cannot select is a
      suppression that silently does not apply.

  Q3  DOES THE DATE BLOCK HAVE A RELATIVE MODE? `Last Direct Mailed` offered only
      "Pick a date". The mail cadence needs "a month or more ago". An absolute date in a
      SAVED preset goes stale the day after it is saved, so if there is no relative mode
      that is a standing limitation the operator has to know about, not a detail.

    python src/scripts/dpd_preset_save_probe.py

READ-ONLY. Adds filter blocks and expands sections to read them; never clicks Save or
Save New. Writes only `output/dpd_preset_save_probe.json`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from datasift_core import (  # noqa: E402
    create_browser, dismiss_popups, get_credentials, login,
)
from dpd.preset_spec import DEAD_STATUSES  # noqa: E402
from dpd_filter_blocks import (  # noqa: E402
    _click_option, _open_panel, _options, _type_search,
)

BASE = "https://app.reisift.io"
OUT = ROOT / "output" / "dpd_preset_save_probe.json"


async def _scroll_panel_bottom(page) -> None:
    await page.evaluate(
        """() => {
        for (const el of document.querySelectorAll('div')) {
            const r = el.getBoundingClientRect();
            if (r.x < 300 || r.width < 250) continue;
            if (el.scrollHeight > el.clientHeight + 50) el.scrollTop = el.scrollHeight;
        }
    }""")
    await page.wait_for_timeout(1200)


async def _click_text(page, text: str, *, exact: bool = True) -> bool:
    box = await page.evaluate(
        """([txt, exact]) => {
        let best = null;
        for (const el of document.querySelectorAll('*')) {
            if (el.children.length !== 0) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0 || r.x < 300) continue;
            const t = (el.innerText || '').trim();
            const hit = exact ? t === txt : t.startsWith(txt);
            if (!hit) continue;
            if (!best || r.width < best.w) {
                best = {x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width};
            }
        }
        return best;
    }""", [text, exact])
    if not box:
        return False
    await page.mouse.click(box["x"], box["y"])
    await page.wait_for_timeout(1800)
    return True


async def _action_bar(page) -> dict:
    """Every element reading Load / Save / Save New / Clear, with its enabled signals."""
    return await page.evaluate(
        """() => {
        const want = ['Load', 'Save', 'Save New', 'Clear'];
        const out = {};
        for (const el of document.querySelectorAll('*')) {
            if (el.children.length !== 0) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            const t = (el.innerText || '').trim();
            if (!want.includes(t)) continue;
            const cs = getComputedStyle(el);
            const host = el.closest('button,[role="button"]');
            const rec = {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width),
                         tag: el.tagName,
                         cls: (el.className || '').toString().slice(0, 90),
                         opacity: cs.opacity, pointer: cs.pointerEvents,
                         cursor: cs.cursor, color: cs.color,
                         hostTag: host ? host.tagName : null,
                         hostDisabled: host ? !!host.disabled : null};
            (out[t] = out[t] || []).push(rec);
        }
        return out;
    }""")


async def _fill_min_max(page, lo: int, hi: int) -> bool:
    return await page.evaluate(
        """([lo, hi]) => {
        const set = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        const ins = [...document.querySelectorAll('input[type=number]')].filter(i => {
            const ph = (i.placeholder || '').toLowerCase();
            return ph === 'min' || ph === 'max';
        });
        if (ins.length < 2) return false;
        for (const [el, v] of [[ins[0], lo], [ins[1], hi]]) {
            el.focus(); set.call(el, String(v));
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        }
        return true;
    }""", [lo, hi])


async def _type_into(page, placeholder_prefix: str, text: str) -> bool:
    return await page.evaluate(
        """([ph, txt]) => {
        const set = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        const el = [...document.querySelectorAll('input')].find(i => {
            const r = i.getBoundingClientRect();
            return r.width > 0 && r.x > 300
                && (i.placeholder || '').toLowerCase().startsWith(ph.toLowerCase());
        });
        if (!el) return false;
        el.focus(); set.call(el, txt);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    }""", [placeholder_prefix, text])


async def run(headless: bool) -> int:
    email, password = get_credentials()
    out = {"ran_at": datetime.now().isoformat(timespec="seconds")}

    async with create_browser(headless=headless) as (_b, _c, page):
        if not await login(page, email, password) or "/login" in page.url:
            print("LOGIN FAILED")
            return 2
        await page.goto(f"{BASE}/records", wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        await dismiss_popups(page)
        if not await _open_panel(page):
            print("could not open the Records filter panel")
            return 3

        # ---------------------------------------------------------------- Q1
        print("\n=== Q1  WHERE IS THE SAVE BAR ===")
        out["q1"] = {}
        out["q1"]["bar_empty_panel"] = await _action_bar(page)
        print(f"  empty panel, section collapsed : {sorted(out['q1']['bar_empty_panel'])}")

        await _scroll_panel_bottom(page)
        await _click_text(page, "Filter Preset", exact=False)
        await page.wait_for_timeout(1500)
        out["q1"]["bar_presets_expanded"] = await _action_bar(page)
        print(f"  empty panel, section expanded  : "
              f"{sorted(out['q1']['bar_presets_expanded'])}")

        # Now add a real block so Save/Save New have something to save.
        await _type_search(page, "Call Attempts")
        await _click_option(page, "Call Attempts")
        filled = await _fill_min_max(page, 0, 0)
        await page.wait_for_timeout(1500)
        await _scroll_panel_bottom(page)
        bar = await _action_bar(page)
        out["q1"]["bar_with_block"] = bar
        out["q1"]["min_max_filled"] = filled
        print(f"  one block present (min/max set={filled}): {sorted(bar)}")
        for label, recs in bar.items():
            for r in recs:
                print(f"    {label:9s} {r['tag']:6s} x={r['x']:>5} y={r['y']:>5} "
                      f"opacity={r['opacity']} pointer={r['pointer']} "
                      f"cursor={r['cursor']} cls={r['cls'][:48]}")
        if not bar:
            out["q1"]["verdict"] = ("NOT FOUND anywhere, with the section collapsed OR "
                                    "expanded, with and without a filter block present")
            print(f"  VERDICT: {out['q1']['verdict']}")
        else:
            out["q1"]["verdict"] = f"found: {sorted(bar)}"

        # ---------------------------------------------------------------- Q2
        print("\n=== Q2  IS EVERY DEAD STATUS SELECTABLE ===")
        await _click_text(page, "Clear")
        await page.wait_for_timeout(1000)
        await _type_search(page, "Property Status")
        await _click_option(page, "Property Status")
        await page.wait_for_timeout(1500)
        sel, missing = [], []
        for st in DEAD_STATUSES:
            if not await _type_into(page, "Enter property status", st):
                missing.append({"status": st, "why": "no status input on the page"})
                continue
            await page.wait_for_timeout(900)
            opts = await _options(page)
            if st in opts:
                sel.append(st)
            else:
                near = [o for o in opts if st.lower()[:6] in o.lower()]
                missing.append({"status": st, "nearest": near[:3]})
            print(f"  {st:26s} {'OK' if st in opts else 'NOT OFFERED'}")
        out["q2"] = {"selectable": sel, "not_offered": missing,
                     "checked": len(DEAD_STATUSES)}

        # ---------------------------------------------------------------- Q3
        print("\n=== Q3  DOES THE DATE BLOCK HAVE A RELATIVE MODE ===")
        await _click_text(page, "Clear")
        await page.wait_for_timeout(1000)
        pre = await _options(page)
        await _type_search(page, "Last Direct Mailed")
        await _click_option(page, "Last Direct Mailed")
        await page.wait_for_timeout(1500)
        mid = [o for o in await _options(page) if o not in pre]
        await _click_text(page, "Pick a date")
        await page.wait_for_timeout(2000)
        after = [o for o in await _options(page) if o not in pre and o not in mid]
        out["q3"] = {"block_labels": mid, "after_clicking_pick_a_date": after}
        print(f"  block labels          : {mid}")
        print(f"  after 'Pick a date'   : {after[:40]}")
        rel = [o for o in after
               if any(w in o.lower() for w in
                      ("ago", "month", "week", "day", "more than", "before", "after",
                       "between", "last", "older"))]
        out["q3"]["relative_candidates"] = rel
        out["q3"]["verdict"] = ("relative mode present" if rel else
                                "ABSOLUTE DATES ONLY -- a saved preset's date goes stale")
        print(f"  relative candidates   : {rel}")
        print(f"  VERDICT: {out['q3']['verdict']}")

        await _click_text(page, "Clear")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote {OUT}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    return asyncio.run(run(a.headless))


if __name__ == "__main__":
    raise SystemExit(main())
