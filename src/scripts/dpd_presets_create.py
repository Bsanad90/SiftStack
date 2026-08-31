"""Phase 5d: build the 73 Records presets into the 12 folders.

    python src/scripts/dpd_presets_create.py --smoke-test          # ONE throwaway preset
    python src/scripts/dpd_presets_create.py --read                # what exists today
    python src/scripts/dpd_presets_create.py --commit              # build all 73
    python src/scripts/dpd_presets_create.py --commit --only "01 HOTTEST - CALL"

The definitions come from `src/dpd/preset_spec.py` (73, asserted at import) and the
panel mapping from `src/dpd/block_map.py`. Neither is guessed: the 144-block vocabulary,
the Params & Others parameter list, the Include / Do not include toggle and the Save New
dialog were all read off the live panel first.

PRESET NAMES ARE UNIQUE ACROSS THE WHOLE ACCOUNT, not per folder. The dialog refuses a
duplicate with "This preset name is already in use.", so the folder-prefixed names in
`preset_spec` (`Hottest - 02 Ready to Call`, `Bulk - 02 Ready to Call`) are load-bearing:
a bare `02 Ready to Call` would collide across the eight CALL folders.

RESUMABLE AND ADDITIVE. Every folder is read before building, and a preset whose name is
already there is skipped, so a mid-run failure costs one preset rather than the run. It
creates presets and nothing else: no record is touched, no tag written, no existing preset
opened or overwritten. `Save` (which overwrites a loaded preset) is never clicked -- only
`Save New`.

A HALF-BUILT PRESET IS NEVER SAVED. Each block is verified to have landed before the next
is added, and if any block fails the preset is abandoned with its reason recorded. A saved
preset that quietly lost its suppression block looks exactly like a working one, and the
person who finds out is a caller dialling a Do Not Call number.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from datasift_core import (  # noqa: E402
    create_browser, dismiss_popups, get_credentials, login,
)
from dpd.block_map import (  # noqa: E402
    BLOCK, GAPS, MODE_EXCLUDE, PARAM_FOR, PARAM_SUPPRESSION, PLACEHOLDER,
    STATUS_NOT_SELECTABLE, TAG_BUDGET_TRIMS,
)
from dpd.preset_spec import COUNTY_SCOPE, COUNTY_SCOPE_MAIL, PRESETS  # noqa: E402

BASE = "https://app.reisift.io"
SEARCH = "#RecordsFilters__Filter_Blocks__Search"
OUT = ROOT / "output" / "dpd_presets_create.json"
OUT_WIDEN = ROOT / "output" / "dpd_presets_widen.json"

# ------------------------------------------------------------------ panel bits

_BAR = """(want) => {
    let best = null;
    for (const el of document.querySelectorAll('*')) {
        const t = (el.innerText || '').trim();
        if (t !== want) continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        if (!best || r.width < best.w) {
            best = {x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width,
                    op: getComputedStyle(el).opacity};
        }
    }
    return best;
}"""


async def bar_click(page, label: str) -> bool:
    """Click one of Load / Save / Save New / Clear.

    NOT a leaf-node scan. Each control wraps an <svg> beside its text, so a
    `children.length === 0` filter skips it -- which is exactly why five earlier passes
    concluded the action bar did not exist.
    """
    box = await page.evaluate(_BAR, label)
    if not box:
        return False
    await page.mouse.click(box["x"], box["y"])
    await page.wait_for_timeout(1600)
    return True


async def open_panel(page) -> bool:
    """Open the Records filter panel. Polls for the trigger (up to ~20s) and reloads
    the page once if it never renders; a single fixed wait failed one run outright."""
    sels = ("#Records__Filters_Trigger", '[id*="Filters_Trigger"]')

    async def _find():
        for sel in sels:
            loc = page.locator(sel)
            if await loc.count():
                return loc
        return None

    el = None
    for round_ in range(2):
        for _ in range(20):
            el = await _find()
            if el is not None:
                break
            await page.wait_for_timeout(1000)
        if el is not None:
            break
        if round_ == 0:
            print("      filter trigger not rendered after 20s; reloading /records once")
            await page.goto(f"{BASE}/records", wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            await dismiss_popups(page)
    if el is None:
        return False
    for _ in range(1):
        box = await el.first.bounding_box()
        if box:
            await page.mouse.click(box["x"] + box["width"] / 2,
                                   box["y"] + box["height"] / 2)
        else:
            await el.first.click(timeout=6000)
        await page.wait_for_timeout(2800)
        await dismiss_popups(page)
        return True
    return False


async def _set_input(page, sel: str, text: str) -> bool:
    return await page.evaluate(
        """([sel, txt]) => {
        const el = document.querySelector(sel);
        if (!el) return false;
        const set = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        el.focus(); set.call(el, txt);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    }""", [sel, text])


async def _click_leaf(page, label: str, *, min_y: int = 0) -> bool:
    """Click the narrowest leaf with this exact text THAT A CLICK CAN ACTUALLY REACH.

    The records grid keeps its bounding boxes UNDER the filter overlay, so a status
    picker value like "Auction Date Passed" matches both the open suggestion list and a
    record's status badge behind the panel -- and the badge is NARROWER, so the old
    narrowest-match rule clicked it, the overlay swallowed the click, no chip appeared,
    and the miss was reported as success. `document.elementFromPoint` tells the truth:
    only a candidate that is itself what the click would land on is accepted.
    """
    box = await page.evaluate(
        """([lbl, minY]) => {
        const cands = [];
        for (const el of document.querySelectorAll('div,span,li,button,[role="option"]')) {
            if (el.children.length !== 0) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0 || r.x < 300 || r.y < minY) continue;
            if ((el.innerText || '').trim() !== lbl) continue;
            cands.push({el, w: r.width});
        }
        cands.sort((a, b) => a.w - b.w);
        for (const c of cands) {
            let r = c.el.getBoundingClientRect();
            if (r.y < 0 || r.y + r.height > window.innerHeight) {
                c.el.scrollIntoView({block: 'center'});
                r = c.el.getBoundingClientRect();
            }
            const x = r.x + r.width / 2, y = r.y + r.height / 2;
            const at = document.elementFromPoint(x, y);
            if (at && (at === c.el || c.el.contains(at) || at.contains(c.el))) {
                return {x, y};
            }
        }
        return null;
    }""", [label, min_y])
    if not box:
        return False
    await page.mouse.click(box["x"], box["y"])
    await page.wait_for_timeout(1400)
    return True


def _present(name: str, seen: list[str]) -> bool:
    """Is `name` among `seen`, allowing for the list UI truncating what it displays?

    The preset list clips a name at roughly 25 characters ("ZZ SMOKE TEST 144921 - del"),
    so an equality test reports a preset that saved perfectly well as missing. Compare on
    the prefix the UI actually shows.
    """
    n = name.strip()
    for s0 in seen:
        t = (s0 or "").strip()
        if not t:
            continue
        if t == n or n.startswith(t) or t.startswith(n):
            return True
    return False


async def _click_any(page, label: str, *, min_y: int = 0) -> bool:
    """Click by innerText WITHOUT requiring a leaf node, scrolling it into view first.

    `Save Preset` and `Cancel` each wrap a check/cross icon beside their text, so the
    leaf-only clicker never matches them -- the same trap that hid the whole action bar.
    """
    box = await page.evaluate(
        """([lbl, minY]) => {
        let best = null;
        for (const el of document.querySelectorAll('*')) {
            if ((el.innerText || '').trim() !== lbl) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0 || r.y < minY) continue;
            if (!best || r.width < best.w) best = {el, w: r.width};
        }
        if (!best) return null;
        best.el.scrollIntoView({block: 'center'});
        const r = best.el.getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2};
    }""", [label, min_y])
    if not box:
        return False
    await page.mouse.click(box["x"], box["y"])
    await page.wait_for_timeout(1300)
    return True


async def block_count(page) -> int:
    """How many filter blocks are currently in the panel."""
    return await page.evaluate(
        """() => [...document.querySelectorAll('*')].filter(el => {
            if (el.children.length !== 0) return false;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.x < 300) return false;
            return (el.innerText || '').trim() === 'Remove';
        }).length;""")


async def add_block(page, label: str) -> bool:
    """Add one filter block and confirm the count actually went up."""
    before = await block_count(page)
    await page.evaluate(
        """(sel) => {const el = document.querySelector(sel);
                    if (el) el.scrollIntoView({block: 'center'});}""", SEARCH)
    await page.wait_for_timeout(400)
    box = await page.locator(SEARCH).first.bounding_box()
    if box:
        await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await page.wait_for_timeout(500)
    if not await _set_input(page, SEARCH, label):
        return False
    await page.wait_for_timeout(1300)
    if not await _click_leaf(page, label):
        return False
    await page.wait_for_timeout(1600)
    return await block_count(page) > before


async def _pick_in_select(page, sv_handle, wanted: str) -> bool:
    """Open the styled select owned by `sv_handle` and choose `wanted`, then verify.

    RESOLVED THROUGH THE ELEMENT, NOT BY COORDINATES. Two traps make a positional
    approach silently wrong here:

      * The options live in the DOM while the select is CLOSED, so clicking one without
        opening it returns truthy and changes nothing. Thirteen presets were saved with
        every suppression block still reading "Include" -- including a Property Status
        block that therefore matched ONLY dead leads -- and with every Params row still
        reading "Select".
      * EVERY Params row reports its option scroller as visible, all fourteen of them, so
        "the open list is the visible one" is false. Picking the scroller nearest the
        trigger lands one row off: setting `Numbers` actually set `Owner PO Box`.

    The scroller that belongs to this select is the one INSIDE its own SelectContainer,
    which is exact and cannot drift.
    """
    box = await sv_handle.bounding_box()
    if not box:
        return False
    await sv_handle.scroll_into_view_if_needed(timeout=5000)
    box = await sv_handle.bounding_box()
    if not box:
        return False
    await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    await page.wait_for_timeout(900)

    opt = await page.evaluate(
        """([sv, want]) => {
        let box = sv;
        for (let i = 0; i < 6 && box.parentElement; i++) {
            box = box.parentElement;
            if (/SelectContainer/.test((box.className || '').toString())) break;
        }
        const sc = box.querySelector('[class*="SelectOptionsContainerScroller"]');
        if (!sc) return {err: 'this select has no option scroller'};
        for (const el of sc.querySelectorAll('[class*="SelectOptionContainer"]')) {
            if ((el.innerText || '').trim() !== want) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }
        const offered = [...sc.querySelectorAll('[class*="SelectOptionContainer"]')]
            .map(e => (e.innerText || '').trim()).filter(Boolean);
        return {err: 'option not offered', offered: offered.slice(0, 8)};
    }""", [sv_handle, wanted])
    if not opt or opt.get("err"):
        return False
    await page.mouse.click(opt["x"], opt["y"])
    await page.wait_for_timeout(900)
    got = await page.evaluate("(sv) => (sv.innerText || '').trim()", sv_handle)
    return got == wanted


async def set_mode_exclude(page) -> bool:
    """Flip the NEWEST block's Include / Do not include select to exclude, and verify."""
    h = await page.evaluate_handle(
        """() => {
        let best = null;
        for (const el of document.querySelectorAll('[class*="SelectValue"]')) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0 || r.x < 900) continue;
            if ((el.innerText || '').trim() !== 'Include') continue;
            if (!best || r.y > best.y) best = {y: r.y, el};
        }
        return best ? best.el : null;
    }""")
    el = h.as_element()
    if not el:
        return False
    return await _pick_in_select(page, el, MODE_EXCLUDE)


async def _dismiss_picker(page) -> None:
    """Close an open autocomplete list and clear its text.

    An open token list overlays the controls below it, so the NEXT click -- typically on
    "Add new filter block" -- lands on a list item instead and silently adds a second,
    unwanted value. That is where the stray `25.3 Upload` and `Building Permits 2025`
    chips came from: they are simply the second entry in each alphabetical list.
    """
    await page.evaluate(
        """() => {
        const set = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        for (const i of document.querySelectorAll('input')) {
            const ph = (i.placeholder || '').toLowerCase();
            if (!ph.startsWith('search for ') && !ph.startsWith('enter ')) continue;
            const r = i.getBoundingClientRect();
            if (r.width === 0 || r.x < 900) continue;
            set.call(i, '');
            i.dispatchEvent(new Event('input', {bubbles: true}));
            i.dispatchEvent(new Event('change', {bubbles: true}));
            i.blur();
        }
    }""")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(700)


async def block_chips(page) -> list[str]:
    """The values currently chosen in the NEWEST block."""
    return await page.evaluate(
        """() => {
        // Chips render as short pills carrying a remove control; read the lowest block's.
        let lo = 0;
        for (const el of document.querySelectorAll('*')) {
            if (el.children.length !== 0) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.x < 900) continue;
            if ((el.innerText || '').trim() === 'Remove' && r.y > lo) lo = r.y;
        }
        const out = [];
        for (const el of document.querySelectorAll('[class*="Chip"],[class*="Tag"],'
                                                 + '[class*="Badge"],[class*="Token"]')) {
            if (el.children.length > 2) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0 || r.x < 900 || r.y < lo) continue;
            const t = (el.innerText || '').trim();
            if (!t || t.length > 50 || out.includes(t)) continue;
            out.push(t);
        }
        return out;
    }""")


_PICK_INPUT_JS = """([ph, label, kind]) => {
    // Resolve the picker for block `label` the way audit_panel does: panel root = the
    // aside owning the block-search input; a block = a RecordsFiltersSection element;
    // its heading = the first ALL-CAPS leaf. `innerText.startsWith(label)` (the 2026-08-27
    // first version) was intermittent -- a wrapper section that happens to begin with the
    // same heading, or a section whose text begins with its mode select, matched or missed
    // depending on layout, and every miss fell through to "last placeholder input in the
    // document", which is exactly the rule that mis-filed the exclusion tags.
    const diag = {branch: null, heads: [], picked: null};
    const okInput = i => { const r = i.getBoundingClientRect();
        return r.width > 0 && r.x > 300
            && (i.placeholder || '').toLowerCase().startsWith(ph.toLowerCase()); };
    for (const el of document.querySelectorAll('input[data-dpd-pick]')) el.removeAttribute('data-dpd-pick');
    const search = document.querySelector('#RecordsFilters__Filter_Blocks__Search');
    let root = search;
    if (root) { while (root.parentElement) { root = root.parentElement;
        if ((root.innerText || '').includes('Filter Records')) break; } }
    let els = [];
    if (root) {
        const secs = [];
        for (const sec of root.querySelectorAll('[class*="RecordsFiltersSection-"]')) {
            let head = null;
            for (const el of sec.querySelectorAll('*')) {
                if (el.children.length !== 0) continue;
                const t = (el.innerText || '').trim();
                if (!t || t.length > 40 || t !== t.toUpperCase()) continue;
                head = t; break;
            }
            if (!head) continue;
            secs.push({sec, head});
            diag.heads.push(head);
        }
        // Innermost match: a wrapper whose first heading is ours also matches, so take
        // the candidate that contains no other candidate.
        const mine = secs.filter(x => x.head === label);
        const inner = mine.filter(x => !mine.some(y => y !== x && x.sec.contains(y.sec)));
        for (const x of inner) {
            els = [...x.sec.querySelectorAll('input')].filter(okInput);
            if (els.length) { diag.branch = 'section+placeholder'; break; }
            els = [...x.sec.querySelectorAll('input')].filter(i => {
                const r = i.getBoundingClientRect();
                return r.width > 0 && r.x > 300 && (i.type || 'text') === 'text'; });
            if (els.length) { diag.branch = 'section+any-text'; break; }
        }
    }
    if (!els.length) {
        // No block resolved by heading. For the two TAG blocks the document-wide rule
        // is the bug (it always lands on the OR block), so refuse unless only one tag
        // block exists; every other kind has a unique placeholder and the old rule is
        // the one 36 presets were built on.
        const tagHeads = diag.heads.filter(h => h === 'ANY TAGS (OR)' || h === 'ALL TAGS (AND)');
        if ((kind === 'tags_any' || kind === 'tags_all') && tagHeads.length > 1) {
            diag.branch = 'refused: two tag blocks, heading not resolved';
            return {ok: false, diag};
        }
        els = [...document.querySelectorAll('input')].filter(okInput);
        diag.branch = els.length ? 'document+placeholder' : 'none';
    }
    if (!els.length) return {ok: false, diag};
    const el = els[els.length - 1];
    el.setAttribute('data-dpd-pick', '1');
    diag.picked = el.placeholder || '';
    return {ok: true, diag};
}"""


async def set_tokens(page, kind: str, values: list[str]) -> list[str]:
    """Type each value into the right block's picker and click the exact match.

    Returns the values that did NOT land. The caller refuses to save on any miss, because
    a preset missing half its tags looks exactly like a working one.

    The picker is resolved ONCE per value by `_PICK_INPUT_JS` and marked with
    `data-dpd-pick`, so the typing step and the suggestion-click step cannot resolve two
    different inputs. On a miss the resolution diagnostics are printed, because the
    2026-08-27 rebuild abandoned two presets ("counties MISSED", "status_none MISSED")
    with nothing in the log to say which branch had picked what.
    """
    ph = PLACEHOLDER[kind]
    label = BLOCK.get(kind, "").upper()
    missed = []
    for v in values:
        landed = False
        for try_ in range(2):
            if try_:
                print(f"      set_tokens[{kind}] {v!r}: retrying once")
                await _dismiss_picker(page)
                await page.wait_for_timeout(800)
            if await _set_one_token(page, kind, ph, label, v):
                landed = True
                break
        if not landed:
            missed.append(v)
    await page.evaluate(
        """() => document.querySelectorAll('input[data-dpd-pick]')
                 .forEach(e => e.removeAttribute('data-dpd-pick'))""")
    return missed


async def _set_one_token(page, kind: str, ph: str, label: str, v: str) -> bool:
    """One attempt at one value; True if the suggestion was clicked."""
    if True:
        pick = None
        for attempt in range(2):
            pick = await page.evaluate(_PICK_INPUT_JS, [ph, label, kind])
            if pick and pick.get("ok"):
                break
            # The block may still be rendering right after add_block; one short retry.
            await page.wait_for_timeout(700)
        if not pick or not pick.get("ok"):
            print(f"      set_tokens[{kind}] {v!r}: no input resolved; {pick and pick.get('diag')}")
            return False
        typed = await page.evaluate(
            """(txt) => {
            const el = document.querySelector('input[data-dpd-pick]');
            if (!el) return false;
            const set = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            el.scrollIntoView({block: 'center'});
            el.focus(); set.call(el, txt);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""", v)
        if not typed:
            print(f"      set_tokens[{kind}] {v!r}: marked input vanished; {pick.get('diag')}")
            return False
        await page.wait_for_timeout(1100)
        # Click the suggestion INSIDE this input's own container. A document-wide
        # text match is wrong twice over: the grid behind the overlay keeps its boxes
        # (a status badge stole the "Auction Date Passed" click), and CLOSED suggestion
        # lists from earlier pickers stay in the DOM -- the same value can be a tag, a
        # status and a badge at once. Only the active picker's own list is the truth.
        pos = None
        for attempt in range(3):
            pos = await page.evaluate(
                """(txt) => {
                const el = document.querySelector('input[data-dpd-pick]');
                if (!el) return {err: 'marked input vanished'};
                let box = el;
                for (let i = 0; i < 6 && box.parentElement; i++) {
                    box = box.parentElement;
                    if (/InputContainer/.test((box.className || '').toString())) break;
                }
                const seen = [];
                for (const s of box.querySelectorAll('[class*="InputSuggestionContainer"]')) {
                    const t = (s.innerText || '').trim();
                    if (seen.length < 8) seen.push(t);
                    if (t !== txt) continue;
                    s.scrollIntoView({block: 'center'});
                    const r = s.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    return {x: r.x + r.width / 2, y: r.y + r.height / 2};
                }
                return {err: 'no matching suggestion', seen, value: el.value};
            }""", v)
            if pos and "x" in pos:
                break
            # Suggestions arrive over the network; give them one more beat.
            await page.wait_for_timeout(900)
        ok = False
        if pos and "x" in pos:
            await page.mouse.click(pos["x"], pos["y"])
            await page.wait_for_timeout(1400)
            ok = True
        else:
            print(f"      set_tokens[{kind}] {v!r}: {pos}; pick={pick.get('diag')}")
        # Always close the list, even on a miss -- an open list is what corrupts the
        # NEXT interaction, not this one.
        await _dismiss_picker(page)
        return ok


async def set_min_max(page, lo, hi) -> bool:
    """Fill the newest Min/Max pair. `None` for hi means open-ended."""
    return await page.evaluate(
        """([lo, hi]) => {
        const set = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        const ins = [...document.querySelectorAll('input[type=number]')].filter(i => {
            const ph = (i.placeholder || '').toLowerCase();
            const r = i.getBoundingClientRect();
            return r.width > 0 && r.x > 300 && (ph === 'min' || ph === 'max');
        });
        if (ins.length < 2) return false;
        const mn = ins[ins.length - 2], mx = ins[ins.length - 1];
        const put = (el, v) => {
            el.focus(); set.call(el, v === null ? '' : String(v));
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        };
        put(mn, lo); put(mx, hi);
        return true;
    }""", [lo, hi])


async def set_param(page, name: str, value: str) -> bool:
    """Set one Params & Others parameter (e.g. Skiptraced -> No), and verify it stuck.

    SCOPED TO THE BLOCK, never by x-coordinate: the panel is an overlay over the records
    grid and the grid's cells keep their boxes underneath, so an x>1100 scan picked
    `24d ago` out of the table behind it.
    """
    h = await page.evaluate_handle(
        """(nm) => {
        let head = null;
        for (const el of document.querySelectorAll('*')) {
            if (el.children.length !== 0) continue;
            if ((el.innerText || '').trim() === 'PARAMS & OTHERS') { head = el; break; }
        }
        if (!head) return null;
        let box = head;
        for (let i = 0; i < 8 && box.parentElement; i++) {
            box = box.parentElement;
            if (box.getBoundingClientRect().height > 200) break;
        }
        let labEl = null;
        for (const el of box.querySelectorAll('*')) {
            if (el.children.length !== 0) continue;
            if ((el.innerText || '').trim() !== nm) continue;
            if (el.getBoundingClientRect().width === 0) continue;
            labEl = el;
            break;
        }
        if (!labEl) return null;
        labEl.scrollIntoView({block: 'center'});
        const lr = labEl.getBoundingClientRect();
        const midY = lr.y + lr.height / 2;
        let sel = null;
        for (const el of box.querySelectorAll('[class*="SelectValue"]')) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            if (Math.abs(r.y + r.height / 2 - midY) > 16) continue;
            sel = el;
        }
        return sel;
    }""", name)
    el = h.as_element()
    if not el:
        return False
    return await _pick_in_select(page, el, value)


def allocate_tags(include: list[str], include_2: list[str],
                  exclude: list[str]) -> tuple[list[tuple], list[str]]:
    """Fit the tag groups into the one OR block and one AND block that exist.

    Returns (plan, dropped). Each plan entry is (block_key, exclude?, values).

    THE EXCLUSION SEMANTICS ARE THE OPPOSITE OF THE LABELS. Measured live 2026-08-27 on the
    Clean tab (N = 2,363; A = "Absentee Owner", B = "Courthouse Data", |A or B| = 660):
        Any Tags (OR)  + Do not include [B]     -> removes 659   (single value: fine)
        Any Tags (OR)  + Do not include [A, B]  -> removes 0     (stored must_not.all_tags: "carrying BOTH")
        All Tags (AND) + Do not include [A, B]  -> removes 660   ("none of these" -- what we want)
        Any Tags (OR)  + Include        [A, B]  -> 660           (union, as labelled)
    So a multi-value EXCLUSION must be built on the "All Tags (AND)" block, and a
    multi-value INCLUSION on "Any Tags (OR)". A group of ONE value means the same thing in
    either block. Consequence for the budget: include-multi takes OR, exclude-multi takes
    AND, and both fit -- the only remaining trim is a preset with TWO include groups.

    The 2026-08-27 first build put Tier 2's three-tag exclusion on the OR block; those
    presets were deleted and rebuilt on this rule.
    """
    plan, dropped = [], []
    if include_2:
        # Two include groups spend both blocks; a single-value include_2 sits on AND.
        plan.append(("tags_any", False, include))
        plan.append(("tags_all", False, include_2))
        dropped = list(exclude)
        return plan, dropped
    if exclude and len(exclude) > 1:
        # Exclusion needs AND (see above); the include, any size, goes to OR.
        if include:
            plan.append(("tags_any", False, include))
        plan.append(("tags_all", True, exclude))
        return plan, dropped
    if len(include) > 1:
        plan.append(("tags_any", False, include))
        if exclude:
            plan.append(("tags_all", True, exclude))
        return plan, dropped
    # Single-value include and (at most) single-value exclude: either block is correct;
    # this is the shape the 30 Hottest / Strong / FTM presets were built and verified in.
    if include:
        plan.append(("tags_all", False, include))
    if exclude:
        plan.append(("tags_any", True, exclude))
    return plan, dropped


# ------------------------------------------------------------- read / verify

async def expand_presets_section(page) -> bool:
    await page.evaluate(
        """() => {
        for (const el of document.querySelectorAll('div')) {
            const r = el.getBoundingClientRect();
            if (r.x < 300 || r.width < 250) continue;
            if (el.scrollHeight > el.clientHeight + 50) el.scrollTop = el.scrollHeight;
        }
    }""")
    await page.wait_for_timeout(900)
    # Already expanded if a known folder is rendered.
    body = await page.evaluate(
        """() => {const b = document.querySelector('[class*="PresetsBelowBody"]');
                  return b ? (b.innerText || '') : '';}""")
    if "HOTTEST" in body:
        return True
    return await _click_leaf(page, "Filter Presets")


async def read_folder(page, folder: str) -> list[str] | None:
    """Preset names inside one folder, by expanding it and reading its own subtree."""
    res = await page.evaluate(
        """(name) => {
        const b = document.querySelector('[class*="PresetsBelowBody"]');
        if (!b) return {err: 'no body'};
        let lab = null;
        for (const el of b.querySelectorAll('*')) {
            if ((el.innerText || '').trim() !== name) continue;
            if (!lab || el.getBoundingClientRect().width
                        < lab.getBoundingClientRect().width) lab = el;
        }
        if (!lab) return {err: 'folder not found'};
        lab.scrollIntoView({block: 'center'});
        const r = lab.getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2};
    }""", folder)
    if res.get("err"):
        return None
    await page.mouse.click(res["x"], res["y"])
    await page.wait_for_timeout(1800)
    names = await page.evaluate(
        """(name) => {
        const b = document.querySelector('[class*="PresetsBelowBody"]');
        if (!b) return [];
        // Read POSITIONALLY, between this folder's row and the next folder's row.
        //
        // Climbing the DOM until a container "has preset children" does not work: an
        // EMPTY folder has none, so the climb runs all the way to the body and returns
        // the DEFAULT folder's 16 presets. Every one of the 12 new folders reported
        // "holds 16 presets" that way, which is the precise shape of a read that looks
        // like data and is not.
        const folders = [];
        for (const el of b.querySelectorAll('[class*="CollapsibleFolderTitle"],'
                                          + '[class*="SectionHeadingTitle"]')) {
            const r = el.getBoundingClientRect();
            const t = (el.innerText || '').trim();
            if (!t || r.height === 0) continue;
            folders.push({t, y: r.y});
        }
        folders.sort((a, c) => a.y - c.y);
        const me = folders.find(f => f.t === name);
        if (!me) return [];
        const next = folders.find(f => f.y > me.y + 4);
        const hi = next ? next.y : Infinity;

        const out = [];
        for (const el of b.querySelectorAll('[class*="CollapsibleFolderPreset"]')) {
            const r = el.getBoundingClientRect();
            if (r.height === 0 || r.y <= me.y + 4 || r.y >= hi) continue;
            const t = (el.innerText || '').trim();
            if (!t || t.length > 60 || out.includes(t)) continue;
            if (t === 'This folder is empty.') return [];
            out.push(t);
        }
        return out;
    }""", folder)
    # collapse again so the next folder starts from a known state
    await page.mouse.click(res["x"], res["y"])
    await page.wait_for_timeout(900)
    return names




# ------------------------------------------------------------ junk deletion

JUNK_RE = re.compile(r"^ZZ ")


async def _junk_rows(page, folder: str, names: list[str] | None = None) -> list[dict]:
    """Expand `folder` and return the visible preset rows whose text starts with 'ZZ '."""
    res = await page.evaluate(
        """(name) => {
        const b = document.querySelector('[class*="PresetsBelowBody"]');
        if (!b) return {err: 'no body'};
        // Same locate rule as read_folder: the NARROWEST element whose text (first line)
        // is the folder name. The CollapsibleFolderTitle element's innerText carries more
        // than the bare name, so an exact match on it silently found nothing.
        let lab = null;
        for (const el of b.querySelectorAll('*')) {
            const t = (el.innerText || '').trim().split('\\n')[0].trim();
            if (t !== name) continue;
            if (!lab || el.getBoundingClientRect().width
                        < lab.getBoundingClientRect().width) lab = el;
        }
        if (!lab) return {err: 'folder not found'};
        lab.scrollIntoView({block: 'center'});
        const r = lab.getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2};
    }""", folder)
    if res.get("err"):
        print(f"  _junk_rows: {res['err']} for {folder!r}")
        return []
    # "Already open" means THIS folder's target rows are visible -- not merely that some
    # preset rows are (DEFAULT is open on load, so some always are).
    already = await page.evaluate(
        """(names) => {
        const b = document.querySelector('[class*="PresetsBelowBody"]');
        const rows = [...b.querySelectorAll('[class*="CollapsibleFolderPresetTitle"]')];
        return rows.some(el => {
            const t = (el.innerText || '').trim();
            return /^ZZ /.test(t) || names.some(n => n === t || n.startsWith(t) || t.startsWith(n));
        });
    }""", names or [])
    if not already:
        await page.mouse.click(res["x"], res["y"])
        await page.wait_for_timeout(1800)
    rows_all = await page.evaluate(
        """() => [...document.querySelectorAll('[class*="PresetsBelowBody"] [class*="CollapsibleFolderPreset"]')]
                 .map(el => (el.innerText || '').trim().split('\\n')[0]).filter(t => t)""")
    print(f"  _junk_rows: {len(rows_all)} preset rows visible; "
          f"{sum(1 for t in rows_all if JUNK_RE.match(t))} start with 'ZZ '")
    return await page.evaluate(
        """(names) => {
        const b = document.querySelector('[class*="PresetsBelowBody"]');
        const out = [];
        for (const el of b.querySelectorAll('[class*="CollapsibleFolderPreset"]')) {
            const t = (el.innerText || '').trim().split('\\n')[0].trim();
            const listed = names.some(n => n === t || n.startsWith(t) || t.startsWith(n));
            if (!/^ZZ /.test(t) && !listed) continue;
            el.scrollIntoView({block: 'center'});
            const r = el.getBoundingClientRect();
            out.push({text: t, x: r.x, y: r.y, w: r.width, h: r.height});
        }
        return out;
    }""", names or [])


_ROW_CONTROLS_JS = """([x, y]) => {
    const el = document.elementFromPoint(x, y);
    let rowEl = el;
    for (let i = 0; i < 8 && rowEl && !/CollapsibleFolderPreset/.test(rowEl.className || ''); i++)
        rowEl = rowEl.parentElement;
    if (!rowEl) return null;
    const sel = 'svg, button, [role="button"], [class*="Icon"], [class*="icon"], '
              + '[class*="Delete"], [class*="Trash"], [class*="Remove"]';
    const ctrls = [...rowEl.querySelectorAll(sel)].map(c => {
        const r = c.getBoundingClientRect();
        return {tag: c.tagName, cls: (c.getAttribute('class') || '').slice(0, 90),
                title: c.getAttribute('title') || '', aria: c.getAttribute('aria-label') || '',
                x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height,
                visible: r.width > 0 && r.height > 0};
    });
    return {row_html: rowEl.outerHTML.slice(0, 4000), controls: ctrls};
}"""


async def discover_delete(page, folder: str) -> dict:
    """Hover a junk row and record what controls appear on it. Clicks nothing."""
    rows = await _junk_rows(page, folder)
    if not rows:
        return {"folder": folder, "junk_rows": 0}
    row = rows[0]
    cx, cy = row["x"] + row["w"] / 2, row["y"] + row["h"] / 2
    await page.mouse.move(cx, cy)
    await page.wait_for_timeout(1200)
    hovered = await page.evaluate(_ROW_CONTROLS_JS, [cx, cy])
    shot = ROOT / "output" / "dpd_preset_row_hover.png"
    await page.screenshot(path=str(shot))
    return {"folder": folder, "junk_rows": len(rows), "first": row["text"],
            "hovered": hovered, "screenshot": str(shot)}


_KEBAB_JS = """([x, y]) => {
    // From the hovered title, climb to the row container and find its kebab (options) icon.
    let el = document.elementFromPoint(x, y);
    while (el && !/CollapsibleFolderPresetContainer/.test(el.className || '')) el = el.parentElement;
    if (!el) return null;
    const k = el.querySelector('[class*="OptionsIcon"]');
    if (!k) return null;
    const rr = k.getBoundingClientRect();
    return {x: rr.x + rr.width / 2, y: rr.y + rr.height / 2,
            row: (el.innerText || '').trim().split(String.fromCharCode(10))[0]};
}"""

# The kebab menu's rows are icon+text (NOT leaves), so a leaf-only scan reports an empty
# menu -- the same trap that hid the Load | Save | Save New | Clear bar on 2026-08-26.
# Match on innerText across all elements and take the narrowest.
_MENU_ITEM_JS = """(label) => {
    const hits = [...document.querySelectorAll('*')].filter(el => {
        if ((el.innerText || '').trim() !== label) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.x > 900;
    });
    if (!hits.length) return null;
    hits.sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
    const r = hits[0].getBoundingClientRect();
    return {x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width};
}"""


_DELETE_DIALOG_JS = """() => {
    // The confirm dialog: 'Are you sure you wanna delete this preset? You are about to
    // delete "<name>" CONFIRM BY TYPING DELETE FOREVER  No, I don't want to delete it /
    // Yes, delete it'. Find it by its own sentence, never by page-wide text (the page-wide
    // leaf join swallows <style> and <script> bodies).
    const cands = [...document.querySelectorAll('div,section,form')].filter(el => {
        const t = el.innerText || '';
        return /about to delete/i.test(t) && /DELETE FOREVER/i.test(t) &&
               el.getBoundingClientRect().width > 250 && el.getBoundingClientRect().width < 900;
    });
    if (!cands.length) return null;
    cands.sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
    const dlg = cands[0];
    const m = (dlg.innerText || '').match(/about to delete\\s*"([^"]+)"/i);
    const inp = dlg.querySelector('input[type=text], input:not([type]), input[type=search]');
    const yes = [...dlg.querySelectorAll('*')].filter(el =>
        /^yes, delete it$/i.test((el.innerText || '').trim()) && el.getBoundingClientRect().width > 0)
        .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width)[0];
    const rr = inp ? inp.getBoundingClientRect() : null;
    const yr = yes ? yes.getBoundingClientRect() : null;
    return {name: m ? m[1] : null,
            input: rr ? {x: rr.x + Math.min(20, rr.width / 2), y: rr.y + rr.height / 2} : null,
            yes: yr ? {x: yr.x + yr.width / 2, y: yr.y + yr.height / 2,
                       disabled: !!(yes.closest('button') && yes.closest('button').disabled) ||
                                 getComputedStyle(yes).pointerEvents === 'none'} : null,
            text: (dlg.innerText || '').trim().slice(0, 300)};
}"""

_ROW_RECT_JS = """(text) => {
    // Fresh rect for THIS row's title, after scrolling it into view. Rects measured before
    // a later scrollIntoView pointed the kebab click at a neighbouring row (2026-08-27).
    const b = document.querySelector('[class*="PresetsBelowBody"]');
    for (const el of b.querySelectorAll('[class*="CollapsibleFolderPresetTitle"]')) {
        if ((el.innerText || '').trim() !== text) continue;
        el.scrollIntoView({block: 'center'});
        const r = el.getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2};
    }
    return null;
}"""


async def _type_into(page, pt: dict, value: str) -> bool:
    ok = await page.evaluate(
        """([x, y, value]) => {
        const el = document.elementFromPoint(x, y);
        if (!el || el.tagName !== 'INPUT') return false;
        el.focus();
        const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        set.call(el, value);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return el.value === value;
    }""", [pt["x"], pt["y"], value])
    if not ok:
        await page.mouse.click(pt["x"], pt["y"])
        await page.keyboard.type(value, delay=20)
    await page.wait_for_timeout(600)
    return True


async def delete_junk(page, folder: str, dry: bool, names: list[str] | None = None) -> dict:
    """Delete every 'ZZ ' preset in `folder`: kebab -> Delete preset -> type DELETE FOREVER
    -> Yes, delete it. Guards: the row text must match JUNK_RE; the confirm dialog quotes
    the preset it is about to delete and that quoted name must ALSO match JUNK_RE, or the
    dialog is dismissed with "No" and the run stops. `dry` stops once the menu item is found.
    """
    log: list[dict] = []

    def _allowed(text: str) -> bool:
        # Either junk by name, or an exact spec name Basem/this run explicitly listed. The
        # list UI truncates titles (~25 chars), so an allowlisted name may match by prefix.
        if JUNK_RE.match(text):
            return True
        return any(n == text or n.startswith(text) or text.startswith(n) for n in (names or []))

    for _ in range(12):
        rows = await _junk_rows(page, folder, names=names)
        if not rows:
            break
        row = rows[0]
        if not _allowed(row["text"]):
            log.append({"row": row["text"], "status": "refused: not junk / not allowlisted"})
            break
        pt = await page.evaluate(_ROW_RECT_JS, row["text"])
        if not pt:
            log.append({"row": row["text"], "status": "row vanished before hover"})
            break
        await page.mouse.move(pt["x"], pt["y"])
        await page.wait_for_timeout(600)
        kebab = await page.evaluate(_KEBAB_JS, [pt["x"], pt["y"]])
        if not kebab or (kebab.get("row") or "") != row["text"]:
            log.append({"row": row["text"], "status": f"kebab resolved on a different row: {kebab}"})
            break
        await page.mouse.click(kebab["x"], kebab["y"])
        await page.wait_for_timeout(1400)
        item = await page.evaluate(_MENU_ITEM_JS, "Delete preset")
        if not item:
            log.append({"row": row["text"], "status": "menu opened but no 'Delete preset' item"})
            await page.keyboard.press("Escape")
            break
        if dry:
            log.append({"row": row["text"], "status": "dry-run: 'Delete preset' located"})
            await page.keyboard.press("Escape")
            break
        await page.mouse.click(item["x"], item["y"])
        await page.wait_for_timeout(1600)
        dlg = await page.evaluate(_DELETE_DIALOG_JS)
        if not dlg:
            log.append({"row": row["text"], "status": "no type-to-confirm dialog appeared"})
            await page.keyboard.press("Escape")
            break
        if not dlg.get("name") or not _allowed(dlg["name"]):
            log.append({"row": row["text"], "status": f"dialog names a NON-junk preset {dlg.get('name')!r}; refused"})
            no = await page.evaluate(_MENU_ITEM_JS, "No, I don't want to delete it")
            if no:
                await page.mouse.click(no["x"], no["y"])
            else:
                await page.keyboard.press("Escape")
            break
        if not dlg.get("input") or not dlg.get("yes"):
            log.append({"row": row["text"], "status": "dialog lacks input or Yes button", "dialog": dlg})
            await page.keyboard.press("Escape")
            break
        await _type_into(page, dlg["input"], "DELETE FOREVER")
        dlg2 = await page.evaluate(_DELETE_DIALOG_JS)
        if not dlg2 or not dlg2.get("yes") or dlg2["yes"].get("disabled"):
            log.append({"row": row["text"], "status": "Yes button still disabled after typing; refusing", "dialog": dlg2})
            await page.keyboard.press("Escape")
            break
        await page.mouse.click(dlg2["yes"]["x"], dlg2["yes"]["y"])
        await page.wait_for_timeout(2500)
        gone = not any(r["text"] == row["text"] for r in await _junk_rows(page, folder))
        log.append({"row": row["text"], "deleted_name": dlg["name"],
                    "status": "deleted" if gone else "confirmed but row still present"})
        if not gone:
            break
    return {"folder": folder, "log": log}


# ---------------------------------------------------------------- the builder

async def build_blocks(page, spec: dict) -> tuple[list[str], list[str]]:
    """Add every block this preset needs. Returns (applied, failed)."""
    applied, failed = [], []

    async def need(ok: bool, what: str):
        (applied if ok else failed).append(what)

    b = spec["blocks"]

    # 1-3. tags, fitted to the one OR block and one AND block that exist.
    plan, dropped = allocate_tags(spec["tags_any"], spec.get("tags_any_2") or [],
                                  spec["tags_none"])
    if dropped:
        applied.append(f"TRIMMED (block budget): exclusion {dropped} not applied")
    for kind, is_exclude, values in plan:
        if not await add_block(page, BLOCK[kind]):
            failed.append(f"{kind} block would not add")
            continue
        ok_mode = True
        if is_exclude:
            ok_mode = await set_mode_exclude(page)
        miss = await set_tokens(page, kind, values)
        await need(ok_mode and not miss,
                   f"{kind}{' EXCLUDE' if is_exclude else ''} {values}"
                   + ("" if ok_mode else " MODE-TOGGLE FAILED")
                   + (f" MISSED {miss}" if miss else ""))

    # 3b. geography: the 9-county scope (Basem 2026-08-27), mode stays Include.
    if spec.get("counties"):
        if await add_block(page, BLOCK["county"]):
            miss = await set_tokens(page, "county", spec["counties"])
            await need(not miss, f"counties ({len(spec['counties'])})"
                       + (f" MISSED {miss}" if miss else ""))
        else:
            failed.append("county block would not add")

    sup = spec["suppression"]

    # 4. list suppression: low and negative equity.
    if sup.get("lists_none"):
        if await add_block(page, BLOCK["lists_any"]):
            ok_mode = await set_mode_exclude(page)
            miss = await set_tokens(page, "lists_any", sup["lists_none"])
            await need(ok_mode and not miss,
                       f"lists_none {sup['lists_none']}"
                       + ("" if ok_mode else " MODE-TOGGLE FAILED")
                       + (f" MISSED {miss}" if miss else ""))
        else:
            failed.append("lists_none block would not add")

    # 5/6. ONE Property Status block exists, so a preset either excludes the dead
    # statuses or targets one. Reactivation targets `Not Interested`, and a record holds
    # exactly one status, so excluding the other dead ones would be redundant anyway --
    # adding both blocks was silently impossible, not merely wasteful.
    if b.get("status_any"):
        if await add_block(page, BLOCK["status"]):
            miss = await set_tokens(page, "status", b["status_any"])
            await need(not miss, f"status_any {b['status_any']}")
        else:
            failed.append("status_any block would not add")
    else:
        sn = [x for x in (sup.get("status_none") or []) if x not in STATUS_NOT_SELECTABLE]
        if sn:
            if await add_block(page, BLOCK["status"]):
                ok_mode = await set_mode_exclude(page)
                miss = await set_tokens(page, "status", sn)
                await need(ok_mode and not miss,
                           f"status_none ({len(sn)})"
                           + ("" if ok_mode else " MODE-TOGGLE FAILED")
                           + (f" MISSED {miss}" if miss else ""))
            else:
                failed.append("status_none block would not add")

    # 7. the two counters, and the phone gate.
    for key, blk in (("call_attempts", "call_attempts"), ("mail_attempts", "mail_attempts")):
        if key in b:
            lo, hi = b[key]
            if await add_block(page, BLOCK[blk]):
                await need(await set_min_max(page, lo, hi), f"{key} {lo}..{hi}")
            else:
                failed.append(f"{key} block would not add")

    # 8. Params & Others carries every boolean the panel has: Numbers, Skiptraced,
    #    Vacant Mailing, plus the DNC / Opt-out suppression the status picker cannot do.
    params: dict[str, str] = {}
    for key, pname in PARAM_FOR.items():
        if key in b:
            params[pname] = "Yes" if b[key] else "No"
    params.update(PARAM_SUPPRESSION)
    if params:
        if await add_block(page, BLOCK["params"]):
            bad = []
            for pname, val in params.items():
                if not await set_param(page, pname, val):
                    bad.append(f"{pname}={val}")
            await need(not bad, f"params {params}" + (f" FAILED {bad}" if bad else ""))
        else:
            failed.append("params block would not add")

    return applied, failed


async def _folder_value(page) -> str | None:
    """What the Save New dialog's FOLDER control currently reads."""
    return await page.evaluate(
        """() => {
        const inp = document.querySelector('input[name="new_preset_name"]');
        if (!inp) return null;
        const y = inp.getBoundingClientRect().y;
        for (const el of document.querySelectorAll('[class*="SelectValue"]')) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.y < y || r.y > y + 140) continue;
            return (el.innerText || '').trim();
        }
        return null;
    }""")


# ------------------------------------------------------- county widening (edit in place)
# Basem 2026-08-31: MAIL presets cover all 14 DPD jurisdictions, CALL stays on the core 9.
# ADD counties to the loaded preset and Save (overwrite) -- never delete-and-rebuild.
# The panel renders a loaded preset's exclusions as "Include" (render bug; the store is
# right), so the panel is only trusted for CHIP VALUES here; every save is verified
# against the stored definition over the internal API (reads are allowed on this account).

_widen_api = None


def _store_rows(folder: str) -> list[dict]:
    """All stored preset rows in one folder, via the internal API (read-only)."""
    global _widen_api
    from datasift_api_upload import Api
    if _widen_api is None:
        _widen_api = Api()
    folders = _widen_api.call("/api/internal/filter-preset-folder/"
                              "?offset=0&limit=999&ordering=title&type=properties")
    by_title = {r["title"]: r["uuid"] for r in folders.get("results") or []}
    u = by_title.get(folder)
    if not u:
        return []
    r = _widen_api.call(f"/api/internal/filter-preset-folder/{u}/filter-preset/"
                        "?offset=0&limit=999&ordering=title&type=properties")
    return r.get("results") or []


def _store_fetch(folder: str, name: str) -> dict | None:
    for s in _store_rows(folder):
        if _present(name, [s.get("title") or ""]):
            return s
    return None


def _store_counties(row: dict) -> tuple[list[str], bool]:
    f = (row.get("filters") or {}).get("must") or {}
    cs = f.get("any_county") or []
    return (sorted((c.get("title") or "") for c in cs),
            any(c.get("isNegative") for c in cs))


def _filters_minus_county(row: dict) -> dict:
    import copy
    f = copy.deepcopy(row.get("filters") or {})
    if isinstance(f.get("must"), dict):
        f["must"].pop("any_county", None)
    return f


async def _county_chips(page) -> list[str] | None:
    blocks = await page.evaluate(_PANEL_DUMP)
    if isinstance(blocks, dict):
        return None
    b = next((x for x in blocks if x["block"] == "PROPERTY COUNTY"), None)
    return None if b is None else list(b["chips"])


# Prefix-tolerant row finder: the list UI truncates titles and a row's innerText can
# carry extra lines, so exact equality misses real rows (and every miss toggled the
# folder shut again, 2026-08-31). Matches on first lines, prefix in either direction.
_FIND_ROW_JS = """(text) => {
    const b = document.querySelector('[class*="PresetsBelowBody"]');
    if (!b) return null;
    for (const el of b.querySelectorAll('[class*="CollapsibleFolderPresetTitle"]')) {
        const t = (el.innerText || '').trim().split('\\n')[0].trim();
        if (!t) continue;
        if (!(t === text || t.startsWith(text) || text.startsWith(t))) continue;
        el.scrollIntoView({block: 'center'});
        const r = el.getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2, t};
    }
    return null;
}"""


async def _ensure_folder_open(page, folder: str, display: str) -> bool:
    """Make `display`'s row visible: if it isn't, click the folder title once."""
    for attempt in range(2):
        pt = await page.evaluate(_FIND_ROW_JS, display)
        if pt:
            return True
        res = await page.evaluate(
            """(name) => {
            const b = document.querySelector('[class*="PresetsBelowBody"]');
            if (!b) return null;
            let lab = null;
            for (const el of b.querySelectorAll('*')) {
                const t = (el.innerText || '').trim().split('\\n')[0].trim();
                if (t !== name) continue;
                if (!lab || el.getBoundingClientRect().width
                            < lab.getBoundingClientRect().width) lab = el;
            }
            if (!lab) return null;
            lab.scrollIntoView({block: 'center'});
            const r = lab.getBoundingClientRect();
            return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }""", folder)
        if not res:
            return False
        await page.mouse.click(res["x"], res["y"])
        await page.wait_for_timeout(1800)
    return bool(await page.evaluate(_FIND_ROW_JS, display))


async def save_overwrite(page) -> tuple[bool, str]:
    """Click Save on the action bar (overwrites the LOADED preset), confirm any dialog.
    The caller MUST verify the store afterward -- the panel's render is not trusted."""
    if not await bar_click(page, "Save"):
        return False, "Save not found on the action bar"
    await page.wait_for_timeout(1500)
    for label in ("Overwrite", "Save Preset", "Confirm", "Yes"):
        pt = await page.evaluate(_MENU_ITEM_JS, label)
        if pt:
            await page.mouse.click(pt["x"], pt["y"])
            await page.wait_for_timeout(1200)
            return True, f"Save + confirmed via {label!r}"
    return True, "Save clicked; no confirm dialog appeared"


async def widen_one(page, spec: dict, display: str) -> dict:
    """Load `display`, add the missing counties from spec['counties'], Save, verify store."""
    row = {"folder": spec["folder"], "name": spec["name"], "display": display}
    want = sorted(spec["counties"])

    snap = _store_fetch(spec["folder"], spec["name"])
    if snap is None:
        row["status"] = "store row not found before edit"
        return row
    snap_counties, _neg = _store_counties(snap)
    if snap_counties == want:
        row["status"] = "already_widened"
        return row
    snap_rest = _filters_minus_county(snap)

    # Start from a FRESH page every time: after Clear (or a previous Save) the panel sits
    # in its empty state with the Filter Presets section collapsed and the folder list not
    # rendered at all (screenshot 2026-08-31), so nothing below can find a row. The smoke
    # gate passed on exactly this fresh-navigation state chain.
    await page.goto(f"{BASE}/records", wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)
    await dismiss_popups(page)
    if not await open_panel(page):
        row["status"] = "filter panel would not open"
        return row
    row["presets_section"] = await expand_presets_section(page)
    if not await _ensure_folder_open(page, spec["folder"], display):
        row["status"] = "row not visible and folder would not expand"
        return row

    # Loading = a JS click on the row's TITLE element (the uploader's proven pattern;
    # a coordinate mouse click at the title's centre did not load, 2026-08-31).
    _LOAD_ROW_JS = """(text) => {
        const b = document.querySelector('[class*="PresetsBelowBody"]');
        if (!b) return false;
        for (const el of b.querySelectorAll('[class*="CollapsibleFolderPresetTitle"]')) {
            const t = (el.innerText || '').trim().split('\\n')[0].trim();
            if (!t) continue;
            if (!(t === text || t.startsWith(text) || text.startsWith(t))) continue;
            el.scrollIntoView({block: 'center'});
            el.click();
            return true;
        }
        return false;
    }"""
    chips = None
    for attempt in range(2):
        if not await page.evaluate(_LOAD_ROW_JS, display):
            row["status"] = "row title element not found for the load click"
            return row
        await page.wait_for_timeout(4000)
        chips = await _county_chips(page)
        if chips:
            break
    if not chips:
        row["status"] = "load failed: no PROPERTY COUNTY block after the row click"
        return row
    row["chips_before"] = chips
    missing = [c for c in spec["counties"] if c not in chips]
    if missing:
        missed = await set_tokens(page, "county", missing)
        if missed:
            row["status"] = f"picker missed {missed}"
            return row
    chips2 = await _county_chips(page) or []
    row["chips_after"] = chips2
    if sorted(chips2) != want:
        row["status"] = f"chips after add are {sorted(chips2)}, wanted {want}"
        return row

    ok, why = await save_overwrite(page)
    row["save"] = why
    if not ok:
        row["status"] = "save failed"
        return row

    # The load-bearing check: the STORE, not the panel.
    after = _store_fetch(spec["folder"], spec["name"])
    if after is None:
        row["status"] = "store row VANISHED after save"
        return row
    got, neg = _store_counties(after)
    if got != want:
        row["status"] = f"store counties {got} != wanted {want}"
        return row
    if neg:
        row["status"] = "a stored county is NEGATIVE"
        return row
    if _filters_minus_county(after) != snap_rest:
        row["status"] = "NON-COUNTY FILTERS CHANGED on save (render-bug serialization?)"
        row["store_before"] = snap_rest
        row["store_after"] = _filters_minus_county(after)
        return row
    row["status"] = "widened"
    return row


_PANEL_DUMP = """() => {
    // SCOPED TO THE PANEL AND ITS BLOCK CONTAINERS, never by x-coordinate. The first
    // version windowed the page by y between ALL-CAPS leaves at x>=950 -- and the grid
    // behind the overlay keeps its boxes (column headers, "DNC" status chips, cell
    // values like "1076"), so junk headings fragmented every window and the audit read
    // a correct panel as empty. The panel root is the aside that owns the block-search
    // input; each filter block is its own RecordsFiltersSection element.
    const search = document.querySelector('#RecordsFilters__Filter_Blocks__Search');
    if (!search) return {err: 'block-search input not found; is the panel open?'};
    let root = search;
    while (root.parentElement) {
        root = root.parentElement;
        if ((root.innerText || '').includes('Filter Records')) break;
    }
    const out = [];
    for (const sec of root.querySelectorAll('[class*="RecordsFiltersSection-"]')) {
        // The heading is the first ALL-CAPS leaf in the section (DOM order puts it
        // before any chips, so an all-caps chip like "DNC" cannot shadow it).
        let head = null;
        for (const el of sec.querySelectorAll('*')) {
            if (el.children.length !== 0) continue;
            const t = (el.innerText || '').trim();
            if (!t || t.length > 40 || t !== t.toUpperCase()) continue;
            head = t;
            break;
        }
        if (!head) continue;
        let mode = null;
        const sels = [];
        for (const el of sec.querySelectorAll('[class*="SelectValue"]')) {
            // A closed select keeps its option list in the DOM; only the display value
            // outside SelectOption*/InputSuggestion* containers is the truth.
            if (el.closest('[class*="SelectOption"],[class*="InputSuggestion"]')) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0) continue;
            const t = (el.innerText || '').trim();
            if (t === 'Include' || t === 'Do not include') { mode = t; continue; }
            sels.push({mid: r.y + r.height / 2, value: t});
        }
        // Chips: tags render as SelectedTag, statuses as SelectedEntry, lists as
        // SelectedList (title in SelectedListTitle). Read off the live DOM 2026-08-27.
        const chips = [];
        for (const el of sec.querySelectorAll('[class*="SelectedTag-"],'
                + '[class*="SelectedEntry-"],[class*="SelectedListTitle-"]')) {
            const t = (el.innerText || '').trim();
            if (t && t.length < 60 && !chips.includes(t)) chips.push(t);
        }
        const nums = [];
        for (const el of sec.querySelectorAll('input[type=number]')) {
            nums.push({ph: (el.placeholder || ''), v: el.value});
        }
        // Params rows: leaf grid-item labels paired with the select on the same row.
        const rows = [];
        for (const el of sec.querySelectorAll('[class*="FiltersGridItem"]')) {
            if (el.children.length !== 0) continue;
            const t = (el.innerText || '').trim();
            if (!t || t.length > 30) continue;
            const r = el.getBoundingClientRect();
            const mid = r.y + r.height / 2;
            const hit = sels.find(sv => Math.abs(sv.mid - mid) < 20);
            if (hit) rows.push({name: t, value: hit.value});
        }
        out.push({block: head, mode, chips, nums, rows});
    }
    return out;
}"""


async def audit_panel(page, spec: dict) -> list[str]:
    """Read the whole panel back and compare it to the spec. Returns mismatches.

    THIS IS THE GUARD THAT WAS MISSING, and its absence is why thirteen presets were
    saved inverted. The builder verified that each block was ADDED, and the read-back
    verified that the preset NAME existed; neither ever looked at what the blocks
    actually contained. Everything passed while every suppression block read "Include"
    -- a Property Status preset that matched ONLY dead leads, list blocks that included
    Low Equity instead of excluding it, and stray chips picked up from an autocomplete
    list that had been left open.

    Verifying that a control was touched is not the same as verifying what it says.
    """
    blocks = await page.evaluate(_PANEL_DUMP)
    if isinstance(blocks, dict):
        return [f"panel dump failed: {blocks.get('err')}"]
    by = {b["block"]: b for b in blocks}
    bad = []

    plan, _dropped = allocate_tags(spec["tags_any"], spec.get("tags_any_2") or [],
                                  spec["tags_none"])
    want_label = {"tags_any": "ANY TAGS (OR)", "tags_all": "ALL TAGS (AND)"}
    for kind, is_exclude, values in plan:
        lbl = want_label[kind]
        b = by.get(lbl)
        if not b:
            bad.append(f"{lbl}: block missing")
            continue
        want_mode = MODE_EXCLUDE if is_exclude else "Include"
        if b["mode"] != want_mode:
            bad.append(f"{lbl}: mode is {b['mode']!r}, wanted {want_mode!r}")
        if sorted(b["chips"]) != sorted(values):
            bad.append(f"{lbl}: values {b['chips']}, wanted {values}")

    if spec.get("counties"):
        b = by.get("PROPERTY COUNTY")
        if not b:
            bad.append("PROPERTY COUNTY: block missing")
        else:
            if b["mode"] not in (None, "Include"):
                bad.append(f"PROPERTY COUNTY: mode is {b['mode']!r}, wanted Include")
            if sorted(b["chips"]) != sorted(spec["counties"]):
                bad.append(f"PROPERTY COUNTY: values {b['chips']}, "
                           f"wanted {spec['counties']}")

    sup = spec["suppression"]
    if sup.get("lists_none"):
        b = by.get("ANY LISTS (OR)")
        if not b:
            bad.append("ANY LISTS (OR): block missing")
        else:
            if b["mode"] != MODE_EXCLUDE:
                bad.append(f"ANY LISTS (OR): mode is {b['mode']!r}, wanted exclude")
            if sorted(b["chips"]) != sorted(sup["lists_none"]):
                bad.append(f"ANY LISTS (OR): values {b['chips']}, "
                           f"wanted {sup['lists_none']}")

    blk = spec["blocks"]
    if blk.get("status_any"):
        want, want_mode = list(blk["status_any"]), "Include"
    else:
        want = [x for x in (sup.get("status_none") or [])
                if x not in STATUS_NOT_SELECTABLE]
        want_mode = MODE_EXCLUDE
    if want:
        b = by.get("PROPERTY STATUS")
        if not b:
            bad.append("PROPERTY STATUS: block missing")
        else:
            if b["mode"] != want_mode:
                bad.append(f"PROPERTY STATUS: mode is {b['mode']!r}, wanted {want_mode!r}")
            extra = [c for c in b["chips"] if c not in want]
            miss = [c for c in want if c not in b["chips"]]
            if extra or miss:
                bad.append(f"PROPERTY STATUS: extra={extra} missing={miss}")

    for key, lbl in (("call_attempts", "CALL ATTEMPTS"),
                     ("mail_attempts", "DIRECT MAIL ATTEMPTS")):
        if key not in blk:
            continue
        lo, hi = blk[key]
        b = by.get(lbl)
        if not b:
            bad.append(f"{lbl}: block missing")
            continue
        got = {n["ph"].lower(): n["v"] for n in b["nums"]}
        if got.get("min") != str(lo):
            bad.append(f"{lbl}: min is {got.get('min')!r}, wanted {lo}")
        want_hi = "" if hi is None else str(hi)
        if got.get("max") != want_hi:
            bad.append(f"{lbl}: max is {got.get('max')!r}, wanted {want_hi!r}")

    want_params = {}
    for k, pname in PARAM_FOR.items():
        if k in blk:
            want_params[pname] = "Yes" if blk[k] else "No"
    want_params.update(PARAM_SUPPRESSION)
    if want_params:
        b = by.get("PARAMS & OTHERS")
        if not b:
            bad.append("PARAMS & OTHERS: block missing")
        else:
            got = {r["name"]: r["value"] for r in b["rows"]}
            for pname, val in want_params.items():
                if got.get(pname) != val:
                    bad.append(f"PARAMS {pname}: is {got.get(pname)!r}, wanted {val!r}")
    return bad


async def save_new(page, name: str, folder: str) -> tuple[bool, str]:
    """Save the current filter blocks as a NEW preset in `folder`.

    Every step is verified, because each one has a silent-failure mode:
      * `Save Preset` stays DISABLED until React receives the name, and clicking a
        disabled control is a no-op that looks exactly like a successful save.
      * The FOLDER control defaults to `default` and must be OPENED before an option can
        be picked. Clicking the folder's name without opening it matches the folder row
        in the presets list further down the panel instead, which changes nothing -- and
        the preset then lands in `default` while the run reports success.
      * The dialog closing is the only real confirmation the save was accepted.
    """
    if not await bar_click(page, "Save New"):
        return False, "Save New not clickable (is a filter block present?)"
    await page.wait_for_timeout(1200)
    if not await _set_input(page, 'input[name="new_preset_name"]', name):
        return False, "no new_preset_name input; the Save New dialog did not open"
    await page.wait_for_timeout(1000)

    # Open the FOLDER control, then pick the option inside it.
    opened = await page.evaluate(
        """() => {
        const inp = document.querySelector('input[name="new_preset_name"]');
        if (!inp) return null;
        const y = inp.getBoundingClientRect().y;
        for (const el of document.querySelectorAll('[class*="SelectValue"],'
                                                 + '[class*="SelectContainer"]')) {
            const r = el.getBoundingClientRect();
            if (r.width < 100 || r.y < y || r.y > y + 140) continue;
            return {x: r.x + r.width / 2, y: r.y + r.height / 2};
        }
        return null;
    }""")
    if not opened:
        await _click_any(page, "Cancel")
        return False, "folder control not found in the Save New dialog"
    await page.mouse.click(opened["x"], opened["y"])
    await page.wait_for_timeout(1300)

    picked = await page.evaluate(
        """([fld, y]) => {
        // The option must live INSIDE the folder select's own option list, never be a
        // bare text match: the presets list further down the panel carries the same
        // folder names. And the list SCROLLS -- with 19 folders only the first few are
        // in view, so folders 04 and below had a rect but sat under the list's edge;
        // the click landed on nothing and the control stayed on `default` (caught by
        // the read-back below, at the cost of a full build per preset). Scroll the
        // option into view, re-measure, and hit-test before returning a target.
        const cands = [];
        for (const el of document.querySelectorAll(
                '[class*="SelectOptionContainer"],[class*="InputSuggestionContainer"]')) {
            if ((el.innerText || '').trim() !== fld) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            if (r.y < y - 10 || r.y > y + 900) continue;
            cands.push(el);
        }
        for (const el of cands) {
            el.scrollIntoView({block: 'nearest'});
            const r = el.getBoundingClientRect();
            const x = r.x + r.width / 2, cy = r.y + r.height / 2;
            const at = document.elementFromPoint(x, cy);
            if (at && (at === el || el.contains(at) || at.contains(el))) {
                return {x, y: cy};
            }
        }
        return null;
    }""", [folder, opened["y"]])
    if not picked:
        await _click_any(page, "Cancel")
        return False, f"folder {folder!r} not offered in the dropdown"
    await page.mouse.click(picked["x"], picked["y"])
    await page.wait_for_timeout(1100)

    got = await _folder_value(page)
    if (got or "").strip().upper() != folder.strip().upper():
        await _click_any(page, "Cancel")
        return False, f"folder did not change: control still reads {got!r}, wanted {folder!r}"

    state = await page.evaluate(
        """() => {
        for (const el of document.querySelectorAll('*')) {
            if ((el.innerText || '').trim() !== 'Save Preset') continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0) continue;
            const host = el.closest('button');
            return {disabled: host ? !!host.disabled : null,
                    pointer: getComputedStyle(el).pointerEvents};
        }
        return null;
    }""")
    if not state:
        return False, "Save Preset control not present"
    if state.get("disabled") or state.get("pointer") == "none":
        await _click_any(page, "Cancel")
        return False, "Save Preset stayed disabled; React never took the preset name"

    if not await _click_any(page, "Save Preset"):
        return False, "Save Preset button not clickable"
    await page.wait_for_timeout(2600)
    if await page.evaluate(
            """() => !!document.querySelector('input[name="new_preset_name"]')"""):
        # The dialog reports its own refusal in a banner above the name field. Reading it
        # is the difference between "the save was not accepted" and knowing WHY -- the
        # first run of this builder hit "This preset name is already in use." and spent
        # several passes being diagnosed as a dead click.
        err = await page.evaluate(
            """() => {
            for (const el of document.querySelectorAll('*')) {
                if (el.children.length !== 0) continue;
                const t = (el.innerText || '').trim();
                if (!t || t.length > 140) continue;
                if (/already in use|required|invalid|cannot|error/i.test(t)) return t;
            }
            return null;
        }""")
        await _click_any(page, "Cancel")
        return False, err or "the dialog is still open, so the save was not accepted"
    return True, f"saved to {got}"


a_delete_names = ""


async def run(mode: str, only: list[str], headless: bool, limit: int) -> int:
    email, password = get_credentials()
    out = {"ran_at": datetime.now().isoformat(timespec="seconds"), "mode": mode,
           "gaps": GAPS, "results": []}
    targets = [p for p in PRESETS if not only or p["folder"] in only]
    if mode == "widen":
        # Widening is MAIL-only; filter BEFORE the limit so --limit 1 hits a mail preset.
        targets = [p for p in targets if p["channel"] == "mail"]
    if limit:
        targets = targets[:limit]

    async with create_browser(headless=headless) as (_b, _c, page):
        if not await login(page, email, password) or "/login" in page.url:
            print("LOGIN FAILED")
            return 2
        await page.goto(f"{BASE}/records", wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        await dismiss_popups(page)
        if not await open_panel(page):
            print("could not open the Records filter panel")
            return 3

        if not await expand_presets_section(page):
            print("could not expand the Filter Presets section")
            return 3

        folders = sorted({p["folder"] for p in targets})
        existing: dict[str, list[str]] = {}
        for f in folders:
            existing[f] = await read_folder(page, f) or []
            print(f"  {f:24s} holds {len(existing[f])} presets")
        out["existing"] = existing

        if mode in ("discover_delete", "delete_junk_dry", "delete_junk"):
            folder = only[0] if only else "01 HOTTEST - CALL"
            if mode == "discover_delete":
                out["discover_delete"] = await discover_delete(page, folder)
                dd = out["discover_delete"]
                print(json.dumps({k: v for k, v in dd.items() if k != "hovered"}, indent=1))
                h = dd.get("hovered") or {}
                for c in h.get("controls", []):
                    print("   ctrl", c)
                print("   row html:", (h.get("row_html") or "")[:1500])
            else:
                names = [n.strip() for n in (a_delete_names or "").split(",") if n.strip()]
                out["delete_junk"] = await delete_junk(page, folder, dry=(mode == "delete_junk_dry"),
                                                       names=names)
                for row in out["delete_junk"]["log"]:
                    print("  ", row)
                await page.goto(f"{BASE}/records", wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)
                await dismiss_popups(page)
                await open_panel(page)
                await expand_presets_section(page)
                after = await read_folder(page, folder) or []
                out["after"] = {folder: after}
                junk_left = [n for n in after if JUNK_RE.match(n)
                             or any(x == n or x.startswith(n) or n.startswith(x) for x in names)]
                print(f"  read-back: {folder} holds {len(after)} presets, junk left: {junk_left}")
                out["junk_left"] = junk_left
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
            return 1 if out.get("junk_left") else 0

        if mode == "read":
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
            print(f"\nWrote {OUT}")
            return 0

        if mode == "widen_smoke":
            # Phase B0 gate: prove on a throwaway that Save-overwrite adds counties
            # WITHOUT serializing the panel's lying "Include" render over the stored
            # exclusions. Create a ZZ miniature of a real MAIL preset with the 9-county
            # scope, snapshot the store, widen it to 15, diff the store.
            stamp = datetime.now().strftime("%H%M%S")
            base = next(p for p in PRESETS if p["channel"] == "mail")
            name = f"ZZ WIDEN SMOKE {stamp}"
            spec9 = {**base, "name": name, "counties": list(COUNTY_SCOPE)}
            print(f"\nWIDEN SMOKE: creating {name!r} in {spec9['folder']} (9 counties)")
            await bar_click(page, "Clear")
            await page.wait_for_timeout(1000)
            applied, failed_blocks = await build_blocks(page, spec9)
            if failed_blocks:
                print(f"  creation failed: {failed_blocks}")
                return 4
            mism = await audit_panel(page, spec9)
            if mism:
                print(f"  creation audit failed: {mism[:4]}")
                return 4
            ok, why = await save_new(page, name, spec9["folder"])
            print(f"  save_new: {why}")
            if not ok:
                return 4
            await page.wait_for_timeout(2000)
            spec15 = {**spec9, "counties": list(COUNTY_SCOPE_MAIL)}
            # Read back from a FRESH load, never from the page that just wrote it --
            # the presets list does not re-render the new row in place (same rule as
            # the commit mode's read-back).
            await page.goto(f"{BASE}/records", wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            await dismiss_popups(page)
            await open_panel(page)
            await expand_presets_section(page)
            rows_zz = await _junk_rows(page, spec9["folder"])
            display = next((r["text"] for r in rows_zz if _present(name, [r["text"]])), None)
            if not display:
                print(f"  ZZ row not found after save; rows seen: {[r['text'] for r in rows_zz]}")
                return 4
            row = await widen_one(page, spec15, display)
            out["results"].append(row)
            print(f"  widen: {row['status']}  ({row.get('save', '')})")
            gate_ok = row["status"] == "widened"
            # Clean up the ZZ preset regardless of the verdict.
            dj = await delete_junk(page, spec9["folder"], dry=False)
            print(f"  cleanup: {dj['log']}")
            out["gate"] = "PASS" if gate_ok else "FAIL"
            OUT_WIDEN.parent.mkdir(parents=True, exist_ok=True)
            OUT_WIDEN.write_text(json.dumps(out, indent=1), encoding="utf-8")
            print(f"\nWIDEN SMOKE GATE: {out['gate']}")
            return 0 if gate_ok else 5

        if mode == "widen":
            # targets is already MAIL-only (filtered before the limit above).
            assert all(p["channel"] == "mail" for p in targets)
            print(f"\n{len(targets)} MAIL presets to widen")
            done = 0
            for i, spec in enumerate(targets, 1):
                display = next((n.split("\n")[0].strip()
                                for n in existing.get(spec["folder"], [])
                                if _present(spec["name"], [n.split("\n")[0].strip()])), None)
                if not display:
                    print(f"  [{i:>2}] {spec['name'][:44]:46s} NOT PRESENT in folder read")
                    out["stopped_on"] = spec["name"]
                    break
                row = await widen_one(page, spec, display)
                out["results"].append(row)
                print(f"  [{i:>2}/{len(targets)}] {spec['name'][:44]:46s} {row['status']}")
                if row["status"] not in ("widened", "already_widened"):
                    out["stopped_on"] = spec["name"]
                    try:
                        await page.screenshot(path=str(ROOT / "output" / "dpd_widen_failure.png"))
                    except Exception:  # noqa: BLE001
                        pass
                    break
                done += 1
            out["widened"] = done
            OUT_WIDEN.parent.mkdir(parents=True, exist_ok=True)
            OUT_WIDEN.write_text(json.dumps(out, indent=1), encoding="utf-8")
            print(f"\n  widened/confirmed {done} of {len(targets)}; wrote {OUT_WIDEN}")
            return 1 if out.get("stopped_on") else 0

        if mode == "smoke":
            stamp = datetime.now().strftime("%H%M%S")
            targets = [{**targets[0], "name": f"ZZ SMOKE TEST {stamp} - delete me"}]
            print(f"\nSMOKE TEST: one throwaway preset in {targets[0]['folder']}")

        todo = [p for p in targets
                if not _present(p["name"], existing.get(p["folder"], []))]
        print(f"\n{len(todo)} to build ({len(targets) - len(todo)} already present)")

        built = fail = 0
        shot_folders: set[str] = set()
        for i, spec in enumerate(todo, 1):
            await bar_click(page, "Clear")
            await page.wait_for_timeout(1000)
            applied, failed = await build_blocks(page, spec)
            row = {"folder": spec["folder"], "name": spec["name"],
                   "applied": applied, "failed": failed}
            if failed:
                # Never save a preset missing a block. A preset that quietly lost its
                # suppression looks exactly like a working one.
                row["status"] = "abandoned"
                out["results"].append(row)
                fail += 1
                print(f"  [{i:>2}/{len(todo)}] {spec['name'][:44]:46s} ABANDONED {failed}")
                continue
            mism = await audit_panel(page, spec)
            if mism:
                row["status"] = "abandoned"
                row["mismatches"] = mism
                out["results"].append(row)
                fail += 1
                print(f"  [{i:>2}/{len(todo)}] {spec['name'][:44]:46s} AUDIT FAILED")
                for m in mism[:6]:
                    print(f"        {m}")
                continue
            # Panel screenshot for human inspection: every smoke build, and the
            # first preset of each folder on a commit run. Best-effort only.
            if mode == "smoke" or spec["folder"] not in shot_folders:
                shot_folders.add(spec["folder"])
                safe = "".join(c if c.isalnum() else "_"
                               for c in f"{spec['folder']}_{spec['name']}")
                try:
                    shot = ROOT / "output" / f"dpd_panel_{safe}.png"
                    await page.screenshot(path=str(shot))
                    row["screenshot"] = str(shot)
                except Exception as exc:  # a failed screenshot must not abandon a preset
                    row["screenshot_error"] = str(exc)
            ok, why = await save_new(page, spec["name"], spec["folder"])
            row["status"] = "saved" if ok else "save_failed"
            row["why"] = why
            out["results"].append(row)
            built += ok
            fail += (not ok)
            print(f"  [{i:>2}/{len(todo)}] {spec['name'][:44]:46s} "
                  f"{'OK' if ok else 'FAILED: ' + why}")

        print(f"\n  built {built}, failed {fail}")

        # Read back from a fresh load, never from the page that just wrote them.
        await page.goto(f"{BASE}/records", wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        await dismiss_popups(page)
        await open_panel(page)
        await expand_presets_section(page)
        after: dict[str, list[str]] = {}
        for f in folders:
            after[f] = await read_folder(page, f) or []
        out["after"] = after
        want = {}
        for p in targets:
            want.setdefault(p["folder"], []).append(p["name"])
        print("\n  Read-back:")
        missing_total = 0
        for f in folders:
            have = list(after.get(f) or [])
            miss = [n for n in want.get(f, []) if not _present(n, list(have))]
            missing_total += len(miss)
            print(f"    {f:24s} {len(have):>2} present, {len(miss)} missing")
            for m in miss[:6]:
                print(f"        missing: {m}")
        out["missing_total"] = missing_total

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote {OUT}")
    print("\n  Stated gaps (omitted on purpose, not forgotten):")
    for k, v in GAPS.items():
        print(f"    {k}: {v.splitlines()[0]}")
    return 1 if out.get("missing_total") else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--read", action="store_true", help="report what exists, build nothing")
    ap.add_argument("--discover-delete", action="store_true",
                    help="hover a ZZ junk preset row and dump its controls; deletes nothing")
    ap.add_argument("--delete-junk-dry", action="store_true",
                    help="find the delete control on each ZZ row; stop before clicking")
    ap.add_argument("--delete-junk", action="store_true",
                    help="delete every preset whose name starts with 'ZZ ' in the --only folder")
    ap.add_argument("--delete-names", default="",
                    help="comma-separated EXACT preset names to delete with --delete-junk "
                         "(in addition to the ZZ rule); each is confirmed against the dialog")
    ap.add_argument("--smoke-test", action="store_true",
                    help="build ONE throwaway preset and read it back")
    ap.add_argument("--widen-smoke", action="store_true",
                    help="Phase B0 gate: create a ZZ throwaway, widen its counties in "
                         "place, verify the store, delete it")
    ap.add_argument("--widen-counties", action="store_true",
                    help="ADD the extra MAIL counties to the existing MAIL presets in "
                         "place (Save overwrite); verifies each save against the store")
    ap.add_argument("--commit", action="store_true", help="build the presets")
    ap.add_argument("--only", default="", help="comma-separated folder names")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    global a_delete_names
    a_delete_names = a.delete_names
    mode = ("discover_delete" if a.discover_delete else
            "delete_junk_dry" if a.delete_junk_dry else
            "delete_junk" if a.delete_junk else
            "widen_smoke" if a.widen_smoke else
            "widen" if a.widen_counties else
            "smoke" if a.smoke_test else "commit" if a.commit else "read")
    only = [s.strip() for s in a.only.split(",") if s.strip()]
    return asyncio.run(run(mode, only, a.headless, a.limit))


if __name__ == "__main__":
    raise SystemExit(main())
