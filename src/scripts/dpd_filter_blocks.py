"""Phase 5b: map the Records filter-block vocabulary, so the 73 presets can be built
from controls that actually exist rather than from guesses.

`dpd_presets_build.py` created the 12 folders and stopped, because a preset is a saved
set of FILTER BLOCKS and none of them had been mapped. Its `FILTER_BLOCKS` list is the
checklist of what the 73 need; this pass finds each one on the live panel and dumps the
controls inside it.

    python src/scripts/dpd_filter_blocks.py              # vocabulary only (fast)
    python src/scripts/dpd_filter_blocks.py --probe      # + open each target block and
                                                         #   dump its inner controls
    python src/scripts/dpd_filter_blocks.py --probe --only "Tags,Lists"

READ-ONLY. It adds filter blocks to the panel to read them and clicks Clear afterwards.
It never clicks Save or Save New, so no preset is created and nothing on the account
changes. Filtering the records grid is a view, not a write.

Mechanics, inherited from the Phase 0/5a passes and not guessable:

  * `#RecordsFilters__Filter_Blocks__Search` is the "Add new filter block" control. It is
    an INPUT and its text is a PLACEHOLDER, so anchoring on textContent never matches it.
  * Every click goes through a real `page.mouse.click()` at the element centre. Playwright's
    own click gets intercepted by `RecordsFiltersstyles__RecordsFiltersSection` and a JS
    `.click()` silently no-ops on several of these controls.
  * React inputs need the native value setter plus input/change events; assigning `.value`
    leaves the component's state untouched.
  * The panel is a scrollable div, not the viewport.
  * Everything is bounded to x > 300 so the left nav cannot be scraped as filter options.
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
from dpd_presets_build import FILTER_BLOCKS  # noqa: E402

BASE = "https://app.reisift.io"
SEARCH = "#RecordsFilters__Filter_Blocks__Search"
OUT = ROOT / "output" / "dpd_filter_blocks.json"

# What each checklist entry is actually called on the panel is unknown, so each target
# carries SEARCH TERMS rather than one guessed label. A target that matches nothing is
# reported as unmatched -- never silently skipped, and never approximated to a
# near-miss, because a preset built on the wrong block filters the wrong records and
# still looks like a preset.
TARGETS = {
    # Exact labels, read off the live 144-block vocabulary on 2026-08-26. They were
    # search terms until that capture; a term like "tag" matched four blocks
    # (`All Tags (AND)`, `Any Tags (OR)`, `Phone Tags`, `Tag Count`) and taking the
    # first would have built the entry scope on AND when the scope is an OR of four
    # tags -- a preset that returns only records carrying all four at once, which is
    # none of them.
    "Tags any (OR)": ["Any Tags (OR)"],
    "Tags all (AND)": ["All Tags (AND)"],
    "Lists any (OR)": ["Any Lists (OR)"],
    "Property Status": ["Property Status"],
    "Call attempts": ["Call Attempts"],
    "Mail attempts": ["Direct Mail Attempts"],
    "Phone count": ["Phone Count"],
    "Last skip trace": ["Last Skip Trace Date"],
    "Last direct mailed": ["Last Direct Mailed"],
    "Structure types": ["Structure Types"],
    # Suppression and reactivation candidates whose fitness is not yet established.
    "Do not mail ever": ["Do Not Mail Ever (Property)"],
    "Last updated date": ["Last Updated Date"],
    "Last updated field": ["Last Updated Field"],
}

# The negative control. If the "options" we scrape contain these, we scraped the page
# rather than the dropdown, and the capture is void rather than thin.
PAGE_FURNITURE = {"siftmap", "siftline", "market finder", "records", "tags", "sequences",
                  "buy credits", "talk to us", "add new property"}


async def _open_panel(page) -> bool:
    for sel in ("#Records__Filters_Trigger", '[id*="Filters_Trigger"]'):
        el = page.locator(sel)
        if not await el.count():
            continue
        box = await el.first.bounding_box()
        if box:
            await page.mouse.click(box["x"] + box["width"] / 2,
                                   box["y"] + box["height"] / 2)
        else:
            await el.first.click(timeout=6000)
        await page.wait_for_timeout(3000)
        await dismiss_popups(page)
        return True
    return False


async def _type_search(page, text: str) -> bool:
    """Focus the block search and set its value through React's own setter."""
    el = page.locator(SEARCH)
    if not await el.count():
        return False
    box = await el.first.bounding_box()
    if box:
        await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await page.wait_for_timeout(600)
    ok = await page.evaluate(
        """([sel, txt]) => {
        const el = document.querySelector(sel);
        if (!el) return false;
        const set = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        el.focus(); set.call(el, txt);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    }""", [SEARCH, text])
    await page.wait_for_timeout(1500)
    return bool(ok)


async def _options(page) -> list[str]:
    """Leaf labels currently offered by the open block dropdown."""
    return await page.evaluate(
        """() => {
        const out = [];
        for (const el of document.querySelectorAll('div,span,li,button,[role="option"]')) {
            if (el.children.length !== 0) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0 || r.x < 300) continue;
            const t = (el.innerText || '').trim();
            if (!t || t.length > 60 || t.includes('\\n')) continue;
            if (!out.includes(t)) out.push(t);
        }
        return out;
    }""")


async def _click_option(page, label: str) -> bool:
    box = await page.evaluate(
        """(lbl) => {
        let best = null;
        for (const el of document.querySelectorAll('div,span,li,button,[role="option"]')) {
            if (el.children.length !== 0) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0 || r.x < 300) continue;
            if ((el.innerText || '').trim() !== lbl) continue;
            if (!best || r.width < best.w) {
                best = {x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width};
            }
        }
        return best;
    }""", label)
    if not box:
        return False
    await page.mouse.click(box["x"], box["y"])
    await page.wait_for_timeout(2200)
    return True


async def _controls(page) -> dict:
    """Every named control, select and input currently in the panel.

    Reading the native `<select name>` is what mapped the SiftMap vocabulary in one shot
    instead of guessing parameter names one at a time; the same trick is tried here first.
    """
    return await page.evaluate(
        """() => {
        const vis = el => { const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0 && r.x > 300; };
        const selects = [...document.querySelectorAll('select')].filter(vis).map(s => ({
            name: s.name || null, id: s.id || null,
            options: [...s.options].map(o => ({value: o.value, label: o.text.trim()}))
        }));
        const inputs = [...document.querySelectorAll('input:not([type=hidden])')]
            .filter(vis).map(i => ({
                name: i.name || null, id: i.id || null, type: i.type,
                placeholder: i.placeholder || null
            }));
        return {selects, inputs};
    }""")


async def _clear(page) -> None:
    box = await page.evaluate(
        """() => {
        for (const b of document.querySelectorAll('button,div,span')) {
            const r = b.getBoundingClientRect();
            if (r.width === 0 || r.x < 300 || r.width > 200) continue;
            if ((b.innerText || '').trim() === 'Clear') {
                return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            }
        }
        return null;
    }""")
    if box:
        await page.mouse.click(box["x"], box["y"])
        await page.wait_for_timeout(1800)


async def _save_state(page) -> dict:
    """Are Load / Save / Save New / Clear present, and do they read as enabled?

    THEY ARE NOT `<button>` ELEMENTS. A first pass looked only at `button` and came back
    empty on every one of the 13 blocks, which reads exactly like "no action bar" when in
    fact the bar was there the whole time. So this scans every element, takes the
    narrowest match per label, and -- since a non-button has no `.disabled` -- infers the
    state from aria-disabled, the class name and the computed opacity/pointer-events that
    styled components use to grey a control out.
    """
    return await page.evaluate(
        """() => {
        const want = ['Load', 'Save', 'Save New', 'Clear'];
        const out = {};
        for (const el of document.querySelectorAll('*')) {
            if (el.children.length !== 0) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0 || r.x < 300) continue;
            const t = (el.innerText || '').trim();
            if (!want.includes(t)) continue;
            if (out[t] && out[t].w <= r.width) continue;
            const cs = getComputedStyle(el);
            const host = el.closest('button,[role="button"],div');
            out[t] = {
                w: r.width, x: r.x + r.width / 2, y: r.y + r.height / 2,
                tag: el.tagName, cls: (el.className || '').toString().slice(0, 80),
                aria: el.getAttribute('aria-disabled'),
                opacity: cs.opacity, pointer: cs.pointerEvents,
                hostDisabled: host && 'disabled' in host ? !!host.disabled : null,
            };
        }
        return out;
    }""")


async def _block_dom(page, before: list[str]) -> dict:
    """What the block ADDED to the panel: its labels and its markup.

    None of these blocks uses a native `<select>`, so the include/exclude toggle and any
    date-range chooser are styled components carrying no name, no id and no option value
    -- invisible to a select/input dump. The same problem was solved on SiftMap's
    Foreclosure Filters section by reading the section's own outerHTML, and the same
    marker-diff that stopped the rail probes scraping the nav applies here: only labels
    that appeared AFTER the block was added belong to the block.
    """
    after = await _options(page)
    new = [o for o in after if o not in before]
    html = await page.evaluate(
        """() => {
        // The narrowest container that holds the block's own value control.
        const anchors = [...document.querySelectorAll('input')].filter(i => {
            const ph = (i.placeholder || '').toLowerCase();
            return ph.startsWith('search for ') || ph.startsWith('enter ')
                   || ph === 'min' || ph === 'max';
        });
        if (!anchors.length) {
            // No text control at all -- a pure dropdown block. Fall back to the panel.
            const p = document.querySelector('[class*="RecordsFilters"]');
            return p ? p.outerHTML.slice(0, 20000) : '';
        }
        let el = anchors[anchors.length - 1];
        for (let i = 0; i < 6 && el.parentElement; i++) el = el.parentElement;
        return el.outerHTML.slice(0, 20000);
    }""")
    return {"new_labels": new, "html": html}


async def run(probe: bool, only: list[str], headless: bool) -> int:
    email, password = get_credentials()
    out = {"ran_at": datetime.now().isoformat(timespec="seconds"),
           "checklist": FILTER_BLOCKS, "vocabulary": [], "targets": {}}

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

        before = set(await _options(page))
        if not await _type_search(page, ""):
            print(f"no {SEARCH} on the page; the panel is not in the expected state")
            return 3
        after = await _options(page)
        # Only labels that APPEARED are dropdown options. Marking what was already on
        # screen is the trick that stopped the SiftMap rail probes scraping the nav.
        vocab = [o for o in after if o not in before]
        out["vocabulary"] = vocab
        out["pre_existing_labels"] = sorted(before)

        junk = [v for v in vocab if v.strip().lower() in PAGE_FURNITURE]
        if junk:
            out["status"] = "VOID"
            out["why"] = (f"the scraped options include page furniture ({junk}); this is "
                          "the page, not the dropdown, so the capture is void")
            print(f"\nCAPTURE VOID: {out['why']}")
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
            return 1
        if len(vocab) < 5:
            out["status"] = "THIN"
            out["why"] = (f"only {len(vocab)} options appeared; a filter panel offering "
                          "fewer than 5 blocks is a broken selector, not a small account")
            print(f"\nCAPTURE THIN: {out['why']}")
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
            return 1
        out["status"] = "ok"

        print(f"\n=== FILTER BLOCK VOCABULARY ({len(vocab)}) ===\n")
        for v in vocab:
            print(f"  {v}")

        # Match each checklist target against the live vocabulary.
        low = {v.lower(): v for v in vocab}
        for target, terms in TARGETS.items():
            hits = []
            for term in terms:
                t = term.lower()
                # Exact first, so `Call Attempts` cannot land on `Call Attempts (Owner)`.
                for lv, v in low.items():
                    if lv == t and v not in hits:
                        hits.append(v)
                for lv, v in low.items():
                    if t in lv and v not in hits:
                        hits.append(v)
            out["targets"][target] = {"search_terms": terms, "matches": hits}

        print("\n=== CHECKLIST -> LIVE BLOCKS ===\n")
        for target, info in out["targets"].items():
            m = info["matches"]
            print(f"  {target:22s} {('UNMATCHED' if not m else ', '.join(m[:4]))}")

        if probe:
            todo = [t for t in out["targets"]
                    if (not only or t in only) and out["targets"][t]["matches"]]
            print(f"\n=== PROBING {len(todo)} BLOCKS ===")
            for target in todo:
                label = out["targets"][target]["matches"][0]
                print(f"\n  {target}  ->  {label}")
                await _clear(page)
                await page.wait_for_timeout(800)
                # Marker: everything on screen BEFORE the block is added.
                pre = await _options(page)
                await _type_search(page, label)
                if not await _click_option(page, label):
                    out["targets"][target]["probe"] = {"status": "not_clickable"}
                    print("      could not click the option")
                    continue
                ctl = await _controls(page)
                sav = await _save_state(page)
                dom = await _block_dom(page, pre)
                out["targets"][target]["probe"] = {
                    "status": "ok", "label": label, "controls": ctl,
                    "action_bar": sav, "new_labels": dom["new_labels"],
                    "html": dom["html"]}
                print(f"      new labels: {dom['new_labels']}")
                bar = {k: (v.get('opacity'), v.get('pointer'), v.get('aria'))
                       for k, v in sav.items()}
                print(f"      action bar: {bar}")
                named = [s["name"] for s in ctl["selects"] if s.get("name")]
                print(f"      selects={len(ctl['selects'])} inputs={len(ctl['inputs'])} "
                      f"named={named}  save_enabled={sav}")
                for s in ctl["selects"]:
                    if s.get("name"):
                        opts = ", ".join(o["value"] for o in s["options"][:8])
                        print(f"        select {s['name']}: [{opts}]")
                for i in ctl["inputs"]:
                    if i.get("name") or i.get("placeholder"):
                        print(f"        input  {i.get('name') or ''} "
                              f"({i.get('type')}) ph={i.get('placeholder')!r}")
            await _clear(page)

    unmatched = [t for t, i in out["targets"].items() if not i["matches"]]
    if unmatched:
        print(f"\n  UNMATCHED targets ({len(unmatched)}): {', '.join(unmatched)}")
        print("  These need their real label found before any preset using them is built.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote {OUT}")
    return 1 if unmatched else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", action="store_true",
                    help="open each matched block and dump its inner controls")
    ap.add_argument("--only", default="", help="comma-separated target names to probe")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    only = [s.strip() for s in a.only.split(",") if s.strip()]
    return asyncio.run(run(a.probe, only, a.headless))


if __name__ == "__main__":
    raise SystemExit(main())
