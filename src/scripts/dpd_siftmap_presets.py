"""Save one county's doors-per-deal presets in SiftMap ("Save Filters"), and read them back.

    python src/scripts/dpd_siftmap_presets.py --fips 11001 --discover     # map the dialog, cancel
    python src/scripts/dpd_siftmap_presets.py --fips 11001 --smoke-test   # save ONE throwaway, reload it
    python src/scripts/dpd_siftmap_presets.py --fips 11001 --commit       # save every manifest preset
    python src/scripts/dpd_siftmap_presets.py --fips 11001 --verify       # enumerate + reload each
    python src/scripts/dpd_siftmap_presets.py --fips 11001 --discover-delete "ZZ ..."  # probe one row's icons, click nothing
    python src/scripts/dpd_siftmap_presets.py --fips 11001 --delete-name "ZZ ..."      # delete ONE saved filter (name must start "ZZ ")

Reads data/dpd_siftmap_manifest_<fips>.json (dpd_siftmap_manifest.py --measure).
Writes output/dpd_siftmap_presets_<fips>.json and screenshots under output/dpd_siftmap/.

What this does NOT do: click "Add Records to Account". No record is added to the account
by any mode here. A saved filter is a named, replayable query and nothing more.

Mechanics, carried over from the SiftMap work already done in this build:
  * The rail (Price | Beds/Baths | Property Types | AI Scores | Presets | More | Save
    Filters) runs across the TOP of the map (y < 200). Its controls need a REAL mouse
    click at the element centre; a JS .click() opens a stub.
  * The dialog is located by DOM DIFF: every leaf visible before the click is marked, and
    only elements that appear afterwards count. Geometry and "largest container"
    heuristics both grabbed the nav or the result cards on this page before.
  * A saved filter appears under the "Saved Filters" heading of the Presets popover,
    which is what dpd_doctor.capture_siftmap_presets already enumerates.
  * React inputs need the native value setter plus input/change events; a confirm button
    still disabled after typing means React never received the value, and the script
    refuses to click it rather than reporting success.
  * The load-bearing check is the RELOAD: a saved preset is clicked from the popover and
    the resulting URL's parameters and count are compared to the source. A preset that
    reloads county-wide unfiltered is worse than none.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from datasift_core import DATASIFT_SIFTMAP_URL, create_browser, get_credentials, login  # noqa: E402
from dpd_siftmap_discover import _clean_map, _result_count  # noqa: E402

SHOTS = ROOT / "output" / "dpd_siftmap"
RAIL_SAVE = "Save Filters"
RAIL_PRESETS = "Presets"
SAVED_HEADING = "Saved Filters"


# ── generic page helpers ──────────────────────────────────────────────


async def goto_map(page, url: str, wait_ms: int = 9000) -> int | None:
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(wait_ms)
    await _clean_map(page)
    return await _result_count(page)


async def _rail_box(page, label: str) -> dict | None:
    return await page.evaluate(
        """(label) => {
        const hits = [...document.querySelectorAll('*')].filter(el => {
            if ((el.innerText || '').trim() !== label) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.y < 200 && r.width < 260;
        });
        if (!hits.length) return null;
        // narrowest match = the control itself, not a wrapper
        hits.sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
        const r = hits[0].getBoundingClientRect();
        return {x: r.x, y: r.y, w: r.width, h: r.height};
    }""",
        label,
    )


async def click_rail(page, label: str) -> bool:
    """Real mouse click on a top-rail control.

    The DOM scan finds Save Filters reliably but missed Presets on one run while the
    exact-text locator found it (as dpd_doctor.capture_siftmap_presets always has), so the
    locator is the fallback -- and the click is still a mouse click at its centre, because
    a JS .click() on this rail opens a stub.
    """
    box = await _rail_box(page, label)
    if not box:
        loc = page.get_by_text(label, exact=True)
        if await loc.count() == 0:
            return False
        bb = await loc.first.bounding_box()
        if not bb or bb["y"] > 200:
            return False
        box = {"x": bb["x"], "y": bb["y"], "w": bb["width"], "h": bb["height"]}
    await page.mouse.click(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
    await page.wait_for_timeout(3000)
    return True


async def mark_all(page) -> int:
    return await page.evaluate(
        """() => {
        let n = 0;
        document.querySelectorAll('*').forEach(el => { el.setAttribute('data-dpd-seen', '1'); n++; });
        return n;
    }"""
    )


async def fresh_dump(page) -> dict:
    """Everything that appeared since mark_all(): leaf texts and interactive controls."""
    return await page.evaluate(
        """() => {
        const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
        const fresh = [...document.querySelectorAll('*')].filter(el => !el.hasAttribute('data-dpd-seen'));
        const leaves = fresh.filter(el => el.children.length === 0 && vis(el))
            .map(el => (el.textContent || '').trim()).filter(t => t && t.length < 120);
        const ctrls = fresh.filter(el => vis(el) &&
            ['INPUT', 'TEXTAREA', 'BUTTON', 'SELECT'].includes(el.tagName)).map(el => {
            const r = el.getBoundingClientRect();
            return {tag: el.tagName, type: el.getAttribute('type'), name: el.getAttribute('name'),
                    placeholder: el.getAttribute('placeholder'), text: (el.innerText || '').trim().slice(0, 60),
                    disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true' ||
                              getComputedStyle(el).pointerEvents === 'none',
                    x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)};
        });
        // clickable non-button leaves (styled components render buttons as divs)
        const clickish = fresh.filter(el => vis(el) && el.children.length === 0 &&
            /^(save|cancel|close|create|confirm|delete|apply|yes|no)/i.test((el.textContent || '').trim()))
            .map(el => { const r = el.getBoundingClientRect(); return {text: (el.textContent || '').trim(),
                x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; });
        let container = null;
        const inputs = fresh.filter(el => el.tagName === 'INPUT' && vis(el));
        if (inputs.length) {
            let c = inputs[0];
            for (let i = 0; i < 12 && c.parentElement; i++) {
                c = c.parentElement;
                const leafN = [...c.querySelectorAll('*')].filter(e => e.children.length === 0 && vis(e)).length;
                if (leafN >= 3 && c.getBoundingClientRect().width < window.innerWidth * 0.9) break;
            }
            container = c.outerHTML.slice(0, 6000);
        }
        return {leaves: [...new Set(leaves)], controls: ctrls, clickish, container_html: container};
    }"""
    )


async def set_react_value(page, sel_x: int, sel_y: int, value: str) -> bool:
    """Type into the input at a screen point through React's native setter."""
    return await page.evaluate(
        """([x, y, value]) => {
        const el = document.elementFromPoint(x, y);
        if (!el || !['INPUT', 'TEXTAREA'].includes(el.tagName)) return false;
        el.focus();
        const proto = el.tagName === 'INPUT' ? window.HTMLInputElement.prototype : window.HTMLTextAreaElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
        setter.call(el, value);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return el.value === value;
    }""",
        [sel_x, sel_y, value],
    )


async def _leaf_box(page, text: str, fresh_only: bool = True, y_min: int = 0) -> dict | None:
    return await page.evaluate(
        """([text, freshOnly, yMin]) => {
        const hits = [...document.querySelectorAll('*')].filter(el => {
            if (freshOnly && el.hasAttribute('data-dpd-seen')) return false;
            if ((el.innerText || '').trim() !== text) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.y >= yMin;
        });
        if (!hits.length) return null;
        hits.sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
        const el = hits[0];
        const r = el.getBoundingClientRect();
        const dis = el.closest('button') ? (el.closest('button').disabled ||
                    getComputedStyle(el.closest('button')).pointerEvents === 'none') :
                    (getComputedStyle(el).pointerEvents === 'none');
        return {x: r.x, y: r.y, w: r.width, h: r.height, disabled: !!dis};
    }""",
        [text, fresh_only, y_min],
    )


async def saved_filter_names(page) -> list[str] | None:
    """Open the Presets popover and list the account's saved filters (None = heading absent)."""
    if not await click_rail(page, RAIL_PRESETS):
        return None
    # The popover renders progressively; poll for its heading rather than trusting one wait.
    for _ in range(5):
        seen = await page.evaluate(
            """(h) => [...document.querySelectorAll('*')].some(
                el => el.children.length === 0 && (el.textContent || '').trim() === h)""",
            SAVED_HEADING)
        if seen:
            break
        await page.wait_for_timeout(1500)
    # Exhaust "Show more" completely: this enumeration is the commit skip logic and the
    # verify presence check, so an under-read popover re-saves an existing name (hard stop)
    # or reports false absences. Stall detection guards a "Show more" that stops revealing.
    async def _leaf_total():
        return await page.evaluate(
            """() => [...document.querySelectorAll('*')].filter(
                el => el.children.length === 0 && (el.textContent || '').trim()).length""")
    prev_total = await _leaf_total()
    for _ in range(40):
        more = page.get_by_text("Show more", exact=False)
        if await more.count() == 0:
            break
        try:
            await more.first.click()
            await page.wait_for_timeout(1500)
        except Exception:  # noqa: BLE001
            break
        total = await _leaf_total()
        if total <= prev_total:
            break
        prev_total = total
    items = await page.evaluate(
        """(heading) => {
        const hits = [...document.querySelectorAll('*')].filter(
            el => el.children.length === 0 && (el.textContent || '').trim() === heading);
        if (!hits.length) return null;
        const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
        const countLeaves = n => [...n.querySelectorAll('*')].filter(el => el.children.length === 0 && vis(el)
            && (el.textContent || '').trim() && (el.textContent || '').trim() !== heading).length;
        let c = hits[hits.length - 1];
        for (let i = 0; i < 10 && c.parentElement; i++) { c = c.parentElement; if (countLeaves(c) >= 3) break; }
        const out = [];
        c.querySelectorAll('*').forEach(el => {
            if (el.children.length > 0 || !vis(el)) return;
            const t = (el.textContent || '').trim();
            if (t && t.length > 1 && t.length < 90 && t !== heading) out.push(t);
        });
        return [...new Set(out)];
    }""",
        SAVED_HEADING,
    )
    if items is None:
        return None
    junk = ("Configure", "Show more", "Show less", "Default Presets")
    return [t for t in items if t not in junk
            and not t.startswith(("Filters that you saved", "Pre-build filters"))]


async def load_saved_filter(page, name: str) -> dict:
    """Click a saved filter in the open Presets popover; return the URL params and count."""
    # The popover's list SCROLLS. With 29 saved filters a row can sit at y = -51: it still
    # reports a bounding box, so a click at its centre lands on the map and the read-back
    # sees the unfiltered county (verified 2026-08-27: every DC preset "reloaded" to 48,530).
    # Scroll the row into view, re-measure, then require the URL to change.
    pt = await page.evaluate(
        """(name) => {
        const hits = [...document.querySelectorAll('*')]
            .filter(el => (el.innerText || '').trim() === name)
            .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
        if (!hits.length) return null;
        hits[0].scrollIntoView({block: 'center'});
        const r = hits[0].getBoundingClientRect();
        return {x: r.x + r.width / 2, y: r.y + r.height / 2};
    }""",
        name,
    )
    if not pt:
        return {"loaded": False, "reason": "name not found in popover"}
    before = page.url
    await page.mouse.click(pt["x"], pt["y"])
    for _ in range(15):
        await page.wait_for_timeout(1000)
        if page.url != before:
            break
    if page.url == before:
        return {"loaded": False, "reason": "click did not load the preset (URL unchanged)",
                "url_before": before}
    await page.wait_for_timeout(7000)
    await _clean_map(page)
    count = await _result_count(page)
    return {"loaded": True, "url_before": before, "url": page.url, "count": count}


# Only saved filters whose name marks them as throwaway may ever be deleted.
# Same gate as dpd_presets_create.JUNK_RE; never widen it to silence a refusal.
DELETE_OK_RE = re.compile(r"^ZZ ")

# The saved-filters MANAGEMENT page (found 2026-08-31, from Basem's screenshot): a real
# table -- FILTER NAME / CREATED / AUTO-ADD / star / edit / trash per row, paginated
# 10-per-page. The Presets popover's "Configure" lands on the sibling /presets/default
# tab, which is why 2026-08-27 recorded it as "just closes the popover".
PRESETS_ACCOUNT_URL = "https://app.reisift.io/siftmap/presets/account"

_ROW_PROBE_JS = """([name, hover]) => {
    const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const hits = [...document.querySelectorAll('*')]
        .filter(el => vis(el) && (el.innerText || '').trim() === name)
        .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
    if (!hits.length) return null;
    // The row container. closest('[class*="TopPresetListItem"]') matches the TITLE div
    // (TopPresetsListItemTitle) whose icons live in a SIBLING, so instead climb to the
    // nearest ancestor that holds the icon cluster (an svg) -- that is the actual row.
    let row = hits[0];
    for (let i = 0; i < 8 && row.parentElement; i++) {
        row = row.parentElement;
        if (row.querySelector('svg') || row.getBoundingClientRect().width > 400) break;
    }
    row.scrollIntoView({block: 'center'});
    const r = row.getBoundingClientRect();
    const cls = el => (el.className && el.className.baseVal !== undefined
                       ? el.className.baseVal : el.className || '').toString();
    const seen = new Set(); const controls = [];
    row.querySelectorAll('svg, button, [role="button"], [class*="Icon"], [class*="Delete"],'
        + ' [class*="Trash"], [class*="Remove"], [class*="Star"], [class*="Edit"]').forEach(el => {
        const b = el.getBoundingClientRect();
        if (b.width === 0 || b.height === 0) return;
        const key = `${Math.round(b.x)},${Math.round(b.y)},${Math.round(b.width)}`;
        if (seen.has(key)) return;
        seen.add(key);
        controls.push({tag: el.tagName, cls: cls(el).slice(0, 120),
                       aria: el.getAttribute('aria-label'), title: el.getAttribute('title'),
                       html: el.outerHTML.slice(0, 300),
                       x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2),
                       w: Math.round(b.width), h: Math.round(b.height)});
    });
    return {x: r.x, y: r.y, w: r.width, h: r.height,
            cx: Math.round(r.x + r.width / 2), cy: Math.round(r.y + r.height / 2),
            text: (row.innerText || '').trim().slice(0, 160), cls: cls(row).slice(0, 120),
            row_html: row.outerHTML.slice(0, 4000), controls};
}"""


async def probe_saved_filter_row(page, name: str) -> dict | None:
    """With the Presets popover open: scroll the named row into view, hover it, and dump
    every icon-ish control in it (coords + markup). Clicks nothing."""
    row = await page.evaluate(_ROW_PROBE_JS, [name, False])
    if not row:
        return None
    # Icons can be hover-only (the 2026-08-27 probe hovered nothing and saw only the star).
    await page.mouse.move(row["cx"], row["cy"])
    await page.wait_for_timeout(800)
    return await page.evaluate(_ROW_PROBE_JS, [name, True])


def _delete_candidates(controls: list[dict]) -> list[dict]:
    pat = re.compile(r"delete|trash|remove|bin\b", re.I)
    out = []
    for c in controls:
        hay = " ".join(str(c.get(k) or "") for k in ("cls", "aria", "title", "html"))
        if pat.search(hay):
            out.append(c)
    # Collapse nested matches (an svg inside a matching button) to distinct click points.
    distinct: list[dict] = []
    for c in out:
        if not any(abs(c["x"] - d["x"]) <= 6 and abs(c["y"] - d["y"]) <= 6 for d in distinct):
            distinct.append(c)
    return distinct


_PAGE_ROWS_JS = """(name) => {
    const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const cls = el => (el.className && el.className.baseVal !== undefined
                       ? el.className.baseVal : el.className || '').toString();
    const txt = document.body.innerText || '';
    const m = txt.match(/(\\d+)\\s*-\\s*(\\d+)\\s*of\\s*(\\d+)/);
    const pg = txt.match(/of\\s*(\\d+)\\s*$/m);
    const hits = [...document.querySelectorAll('*')]
        .filter(el => vis(el) && (el.innerText || '').trim() === name)
        .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width);
    let row = null;
    if (hits.length) {
        // rows are PresetRowstyles__PresetRow... containers with edit-icon/trash-icon svgs
        row = hits[0].closest('[class*="PresetRowstyles__PresetRow-"], [class*="PresetRow"]');
        if (row && !row.querySelector('svg')) row = null;
        if (!row) {
            row = hits[0];
            for (let i = 0; i < 10 && row.parentElement; i++) {
                row = row.parentElement;
                if (row.querySelectorAll('svg').length >= 2) break;
            }
        }
        row.scrollIntoView({block: 'center'});
    }
    let rowDump = null;
    if (row) {
        const r = row.getBoundingClientRect();
        const seen = new Set(); const controls = [];
        row.querySelectorAll('svg, button, [role="button"]').forEach(el => {
            const b = el.getBoundingClientRect();
            if (b.width === 0 || b.height === 0) return;
            const key = `${Math.round(b.x)},${Math.round(b.y)}`;
            if (seen.has(key)) return;
            seen.add(key);
            controls.push({tag: el.tagName, cls: cls(el).slice(0, 120),
                           aria: el.getAttribute('aria-label'), title: el.getAttribute('data-tip'),
                           html: el.outerHTML.slice(0, 300),
                           x: Math.round(b.x + b.width / 2), y: Math.round(b.y + b.height / 2)});
        });
        rowDump = {text: (row.innerText || '').trim().slice(0, 200), cls: cls(row).slice(0, 120),
                   row_html: row.outerHTML.slice(0, 4000), controls,
                   x: r.x, y: r.y, w: r.width, h: r.height};
    }
    // pagination: real <button class*="Paginationstyles"> elements (prev/next). They sit
    // BELOW the 900px viewport, so callers must use JS el.click(), never a mouse click.
    const pager = [...document.querySelectorAll('button[class*="Pagination"]')].filter(vis)
        .sort((a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x);
    const next = pager.length ? {count: pager.length, disabled: !!pager[pager.length - 1].disabled} : null;
    return {range: m ? m[0] : null, total: m ? Number(m[3]) : null, row: rowDump, next};
}"""

_CLICK_NEXT_JS = """() => {
    const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    const pager = [...document.querySelectorAll('button[class*="Pagination"]')].filter(vis)
        .sort((a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x);
    if (!pager.length) return false;
    const nxt = pager[pager.length - 1];
    if (nxt.disabled) return false;
    nxt.click();
    return true;
}"""


async def find_presets_row(page, name: str, out: dict) -> dict | None:
    """On PRESETS_ACCOUNT_URL, page through the table until the named row is visible.
    Returns the row dump (text, controls, geometry) or None."""
    await page.goto(PRESETS_ACCOUNT_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)
    await _clean_map(page)
    for hop in range(8):
        state = await page.evaluate(_PAGE_ROWS_JS, name)
        out["table_total"] = state.get("total")
        out["table_range"] = state.get("range")
        if state.get("row"):
            return state["row"]
        if not await page.evaluate(_CLICK_NEXT_JS):
            return None
        await page.wait_for_timeout(2500)
    return None


async def delete_saved_filter(page, base_url: str, name: str, out: dict) -> bool:
    """Delete ONE saved filter from the management table (PRESETS_ACCOUNT_URL). Refuses
    non-ZZ names, re-checks the row before clicking, and requires any confirm dialog to
    quote the name."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    if not DELETE_OK_RE.match(name):
        out["error"] = f"refused: {name!r} does not start with 'ZZ ' and is not deletable"
        return False
    # Popover census first: the load-bearing after-check compares against this.
    await goto_map(page, base_url)
    before = await saved_filter_names(page)
    out["names_before"] = before
    if before is None or name not in before:
        out["error"] = f"{name!r} not present in Saved Filters (nothing to delete)"
        return False

    row = await find_presets_row(page, name, out)
    out["row"] = {k: row.get(k) for k in ("text", "cls")} if row else None
    out["controls"] = (row or {}).get("controls")
    await page.screenshot(path=str(SHOTS / "delete_row_table.png"))
    if not row:
        out["error"] = "row not found in the management table"
        return False
    if name not in row["text"]:
        out["error"] = f"row identity check failed: row says {row['text']!r}"
        return False
    cands = _delete_candidates(row["controls"])
    out["delete_candidates"] = cands
    if len(cands) != 1:
        out["error"] = (f"{len(cands)} delete-looking controls in the row; refusing to guess "
                        f"(see delete_row_table.png and controls[])")
        return False

    # Find-row, verify-text and click-trash in ONE JS pass. The table re-renders with
    # different vertical offsets between reads (seen 2026-08-31: a stored coordinate
    # drifted ~50px and the mouse click landed in the gap between rows), so stored
    # coordinates must never be clicked.
    await mark_all(page)
    clicked = await page.evaluate(
        """(name) => {
        const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
        const hits = [...document.querySelectorAll('*')]
            .filter(el => vis(el) && (el.innerText || '').trim() === name);
        for (const h of hits) {
            let row = h;
            for (let i = 0; i < 10 && row.parentElement; i++) {
                row = row.parentElement;
                if (row.querySelectorAll('svg').length >= 2) break;
            }
            if (!(row.innerText || '').trim().startsWith(name)) continue;
            const trash = row.querySelector('.trash-icon');
            if (!trash) continue;
            const target = trash.closest('button, [role="button"]') || trash;
            target.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            return {ok: true, row_text: (row.innerText || '').trim().slice(0, 100)};
        }
        return {ok: false, hits: hits.length};
    }""", name)
    out["trash_click"] = clicked
    if not clicked.get("ok"):
        out["error"] = f"could not click the trash icon atomically: {clicked}"
        return False
    await page.wait_for_timeout(2000)
    dialog = await fresh_dump(page)
    await page.screenshot(path=str(SHOTS / "delete_after_click.png"))
    dialog_text = " ".join(dialog.get("leaves") or [])
    out["dialog_leaves"] = (dialog.get("leaves") or [])[:20]
    if dialog.get("controls") or dialog.get("clickish"):
        await page.screenshot(path=str(SHOTS / "delete_confirm_dialog.png"))
        # The dialog (verified 2026-08-31) is GENERIC: "DELETE FILTER / Do you want to
        # delete permanently? / This operation cannot be undone." -- it never quotes the
        # name. That is acceptable ONLY because the trash click verified the row's own
        # text in the same JS pass; any other dialog wording is still refused.
        generic_ok = re.search(r"delete permanently|DELETE FILTER", dialog_text, re.I)
        if name not in dialog_text and not generic_ok:
            out["error"] = ("a dialog appeared with unrecognized wording; dismissed "
                            "(see delete_confirm_dialog.png)")
            await page.keyboard.press("Escape")
            return False
        # The dialog arms its Yes button only after the challenge phrase is typed
        # ("CONFIRM BY TYPING DELETE FILTER", verified in DeletePresetModal HTML 2026-08-31).
        typed = next((c for c in dialog["controls"] if c["tag"] in ("INPUT", "TEXTAREA")), None)
        phrase = next((p for p in ("DELETE FILTER", "DELETE FOREVER") if p in dialog_text), None)
        if typed and phrase:
            ok = await set_react_value(page, typed["x"] + 10, typed["y"] + typed["h"] // 2,
                                       phrase)
            if not ok:
                await page.mouse.click(typed["x"] + 10, typed["y"] + typed["h"] // 2)
                await page.keyboard.type(phrase, delay=15)
            await page.wait_for_timeout(800)
            out["typed_phrase"] = phrase
        # Click the Yes BUTTON via JS once it is enabled. Refuse while disabled -- an armed
        # button proves React received the challenge phrase.
        confirmed = await page.evaluate(
            """() => {
            const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
            const btns = [...document.querySelectorAll('button')].filter(b => vis(b)
                && /^yes/i.test((b.innerText || '').trim()));
            if (!btns.length) return 'absent';
            if (btns.every(b => b.disabled)) return 'disabled';
            const b = btns.find(b => !b.disabled);
            b.click();
            return (b.innerText || '').trim();
        }""")
        out["confirmed_via"] = confirmed
        if confirmed in ("absent", "disabled"):
            out["error"] = f"Yes button {confirmed} after typing the challenge; dismissed"
            await page.screenshot(path=str(SHOTS / "delete_yes_stuck.png"))
            await page.keyboard.press("Escape")
            return False
        await page.wait_for_timeout(2500)
        await page.screenshot(path=str(SHOTS / "delete_after_yes.png"))

    # Load-bearing check: a fresh popover no longer lists the name, and lists everything else.
    await goto_map(page, base_url)
    after = await saved_filter_names(page) or []
    out["names_after"] = after
    await page.screenshot(path=str(SHOTS / "delete_after.png"))
    if name in after:
        out["error"] = "the filter is still listed after the delete"
        return False
    lost = [n for n in before if n != name and n not in after]
    if lost:
        out["error"] = f"OTHER filters vanished with it: {lost} -- investigate immediately"
        return False
    return True


def url_params(url: str) -> dict:
    qs = parse_qs(urlparse(url).query, keep_blank_values=True)
    flat = {k: (v[0] if len(v) == 1 else v) for k, v in qs.items()}
    loc = flat.pop("location", None)
    try:
        loc = json.loads(loc) if loc else None
    except json.JSONDecodeError:
        pass
    return {"location": loc, "params": flat}


def params_match(src_url: str, got_url: str) -> tuple[bool, list[str]]:
    a, b = url_params(src_url), url_params(got_url)
    problems = []
    if (a["location"] or {}).get("counties") != (b["location"] or {}).get("counties"):
        problems.append(f"location differs: {a['location']} vs {b['location']}")
    for k, v in a["params"].items():
        if b["params"].get(k) != v:
            problems.append(f"{k}: saved {a['params'][k]!r}, reloaded {b['params'].get(k)!r}")
    # A loaded saved filter carries its own `id=<n>`; that is the preset's identity, not a
    # filter, so it is not a mismatch (smoke test 2026-08-27: id=10189).
    extra = {k: v for k, v in b["params"].items() if k not in a["params"] and k != "id"}
    if extra:
        problems.append(f"reloaded URL carries extra params {extra}")
    return (not problems), problems


def _describe(e: dict) -> str:
    """Provenance for the dialog's description field: where this preset came from."""
    if e.get("tier") == "Tier 2":
        return ("Doors-per-deal Tier 2 geography: workbook 4-5 star ZIPs by deals; single-family. "
                "Bulk + mail tier.")
    bits = [f"Doors-per-deal P{e.get('priority')} rank {e.get('rank')}",
            f"lift {e.get('lift')}x", f"dpd {e.get('dpd')}",
            f"workbook list {int(e['workbook_list_size']) if e.get('workbook_list_size') else '-'}"]
    if e.get("proxy"):
        bits.append("Tired Landlord = absentee + owned 10+ yrs (measured 2,050 vs workbook 2,046)")
    bits.append("single-family")
    return "; ".join(bits)


# ── modes ─────────────────────────────────────────────────────────────


async def discover(page, url: str, out: dict) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    count = await goto_map(page, url)
    out["discover"] = {"url": url, "count": count}
    rail = {}
    for label in ("Price", "Beds/Baths", "Property Types", "AI Scores", RAIL_PRESETS, "More", RAIL_SAVE):
        rail[label] = await _rail_box(page, label)
    out["discover"]["rail"] = rail
    print("  rail:", {k: (None if v is None else (round(v['x']), round(v['y']))) for k, v in rail.items()})

    await mark_all(page)
    opened = await click_rail(page, RAIL_SAVE)
    dump = await fresh_dump(page) if opened else {}
    await page.screenshot(path=str(SHOTS / "discover_save_filters.png"))
    out["discover"]["save_filters"] = {"opened": opened, **dump}
    print(f"  Save Filters opened={opened}; fresh leaves={len(dump.get('leaves', []))} "
          f"controls={len(dump.get('controls', []))}")
    for c in dump.get("controls", []):
        print("    ctrl", c)
    for c in dump.get("clickish", []):
        print("    click", c)
    for t in dump.get("leaves", [])[:30]:
        print("    leaf", t)

    # Close it without saving: Cancel if offered, else Escape.
    closed_by = None
    for txt in ("Cancel", "Close", "cancel"):
        b = await _leaf_box(page, txt)
        if b:
            await page.mouse.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
            closed_by = txt
            break
    if not closed_by:
        await page.keyboard.press("Escape")
        closed_by = "Escape"
    await page.wait_for_timeout(1200)
    still = await fresh_dump(page)
    out["discover"]["save_filters"]["closed_by"] = closed_by
    out["discover"]["save_filters"]["still_open_controls"] = len(still.get("controls", []))

    # The Presets popover: where a saved filter shows up, and whether it can be deleted.
    await goto_map(page, url)
    names = await saved_filter_names(page)
    out["discover"]["saved_filters_now"] = names
    print(f"  Saved Filters now: {names}")
    html = await page.evaluate(
        """(heading) => {
        const hits = [...document.querySelectorAll('*')].filter(
            el => el.children.length === 0 && (el.textContent || '').trim() === heading);
        if (!hits.length) return null;
        let c = hits[hits.length - 1];
        for (let i = 0; i < 6 && c.parentElement; i++) c = c.parentElement;
        return c.outerHTML.slice(0, 8000);
    }""",
        SAVED_HEADING,
    )
    out["discover"]["saved_filters_html"] = html
    out["discover"]["saved_filters_has_svg"] = bool(html and "<svg" in html)
    await page.screenshot(path=str(SHOTS / "discover_presets_popover.png"))


async def save_one(page, url: str, name: str, out_row: dict, description: str = "") -> bool:
    """Navigate to `url`, open Save Filters, name it, confirm. True only if the dialog closed.

    The dialog, as mapped by --discover on 2026-08-27: title "Save Filter"; a required text
    input placeholder "Enter new search name"; an optional "Enter description" input; a PRO
    checkbox "Automatically add new properties that start to match this filter after it has
    been saved." which this script NEVER ticks (that is an automatic pull into the account,
    and this build adds no records); `Cancel`; and a `Confirm` button that starts DISABLED
    and enables only once React has received the name.
    """
    count = await goto_map(page, url)
    out_row["count_at_save"] = count
    await mark_all(page)
    if not await click_rail(page, RAIL_SAVE):
        out_row["error"] = "Save Filters not found on the rail"
        return False
    dump = await fresh_dump(page)
    texts = [c for c in dump["controls"] if c["tag"] in ("INPUT", "TEXTAREA")
             and (c["type"] or "text") in ("text", "search")]
    name_in = next((c for c in texts if re.search(r"name", c.get("placeholder") or "", re.I)), None)
    desc_in = next((c for c in texts if re.search(r"desc", c.get("placeholder") or "", re.I)), None)
    if not name_in:
        out_row["error"] = (f"no 'name' text input in the dialog (saw "
                            f"{[c.get('placeholder') for c in texts]})")
        out_row["dialog"] = dump
        await page.keyboard.press("Escape")
        return False

    # The auto-add checkbox must be OFF. Read it; never click it.
    auto_add = await page.evaluate(
        """() => [...document.querySelectorAll('input[type=checkbox]:not([data-dpd-seen])')]
                 .map(c => c.checked)""")
    out_row["auto_add_checkbox_states"] = auto_add
    if any(auto_add):
        out_row["error"] = "the PRO auto-add checkbox is ON by default; refusing to save"
        await page.keyboard.press("Escape")
        return False

    async def fill(ctrl, value):
        x, y = ctrl["x"] + min(20, ctrl["w"] // 2), ctrl["y"] + ctrl["h"] // 2
        ok = await set_react_value(page, x, y, value)
        if not ok:
            await page.mouse.click(x, y)
            await page.keyboard.type(value, delay=12)
        await page.wait_for_timeout(500)

    await fill(name_in, name)
    if desc_in and description:
        await fill(desc_in, description[:250])

    box = await _leaf_box(page, "Confirm")
    if not box:
        box = next((await _leaf_box(page, t) for t in ("Save", "Save Filter")
                    if await _leaf_box(page, t)), None)
    if not box:
        out_row["error"] = "no Confirm control in the dialog"
        out_row["dialog"] = dump
        await page.keyboard.press("Escape")
        return False
    if box["disabled"]:
        out_row["error"] = ("Confirm is still DISABLED after typing -- React did not receive "
                            "the name; refusing to click")
        await page.screenshot(path=str(SHOTS / "confirm_disabled.png"))
        await page.keyboard.press("Escape")
        return False
    await page.mouse.click(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
    await page.wait_for_timeout(3000)
    after = await fresh_dump(page)
    still_open = any(c["tag"] in ("INPUT", "TEXTAREA") and c.get("placeholder")
                     and "search name" in c["placeholder"].lower() for c in after["controls"])
    if still_open:
        out_row["error"] = "dialog still open after Confirm"
        out_row["dialog_after"] = after
        errs = [t for t in after.get("leaves", []) if re.search(r"already|exist|error|invalid|required", t, re.I)]
        if errs:
            out_row["error"] += f": {errs[:3]}"
        await page.keyboard.press("Escape")
        return False
    out_row["saved_via"] = "Confirm"
    return True


async def reload_check(page, base_url: str, name: str, src_url: str, expect_count) -> dict:
    await goto_map(page, base_url)
    names = await saved_filter_names(page) or []
    row = {"present": name in names}
    if not row["present"]:
        row["prefix_present"] = any(n and (name.startswith(n) or n.startswith(name)) for n in names)
        row["names_seen"] = names
        return row
    got = await load_saved_filter(page, name)
    row.update(got)
    if got.get("loaded"):
        ok, problems = params_match(src_url, got["url"])
        row["params_match"] = ok
        row["problems"] = problems
        row["expected_count"] = expect_count
        if expect_count is not None and got.get("count") is not None:
            tol = max(5, int(expect_count * 0.05))
            row["count_match"] = abs(got["count"] - expect_count) <= tol
    return row


async def run(mode: str, fips: str, headless: bool, target: str | None = None) -> dict:
    man_path = ROOT / "data" / f"dpd_siftmap_manifest_{fips}.json"
    if not man_path.exists():
        raise SystemExit(f"missing {man_path}")
    man = json.loads(man_path.read_text(encoding="utf-8"))
    items = list(man["presets"]) + ([man["tier2"]] if man.get("tier2") else [])
    out = {"mode": mode, "fips": fips, "at": datetime.now().isoformat(timespec="seconds"),
           "results": []}
    base_url = man["baseline_url"]
    # Smallest live count first, so a mechanical fault surfaces cheaply; unmeasured last.
    def _k(e):
        c = e.get("measured_count")
        return (c is None, c if c is not None else 0)
    items.sort(key=_k)
    smallest_nonzero = next((e for e in items if (e.get("measured_count") or 0) > 0), items[0])

    email, password = get_credentials()
    async with create_browser(headless=headless) as (_b, _c, page):
        if not await login(page, email, password) or "/login" in page.url:
            out["error"] = "login failed"
            return out

        if mode == "discover":
            await discover(page, smallest_nonzero["url"], out)
            return out

        if mode == "discover_delete":
            SHOTS.mkdir(parents=True, exist_ok=True)
            row = await find_presets_row(page, target, out)
            await page.screenshot(path=str(SHOTS / "discover_delete_row.png"))
            out["row"] = row
            out["delete_candidates"] = _delete_candidates(row["controls"]) if row else None
            return out

        if mode == "delete":
            row = {"name": target}
            row["deleted"] = await delete_saved_filter(page, base_url, target, row)
            out["results"].append(row)
            return out

        if mode == "smoke":
            name = f"ZZ DPD SMOKE {datetime.now():%H%M%S}"
            row = {"name": name, "source": smallest_nonzero["name"], "url": smallest_nonzero["url"]}
            row["saved"] = await save_one(page, smallest_nonzero["url"], name, row,
                                          description="throwaway smoke test; delete")
            SHOTS.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(SHOTS / "smoke_after_save.png"))
            if row["saved"]:
                row["readback"] = await reload_check(page, base_url, name, smallest_nonzero["url"],
                                                     smallest_nonzero.get("measured_count"))
                await page.screenshot(path=str(SHOTS / "smoke_after_reload.png"))
            out["results"].append(row)
            return out

        if mode == "commit":
            await goto_map(page, base_url)
            existing = await saved_filter_names(page) or []
            out["existing_before"] = existing
            for e in items:
                name = e["name"]
                row = {"name": name, "url": e["url"], "measured_count": e.get("measured_count")}
                if name in existing or any(n and name.startswith(n) and len(n) >= 20 for n in existing):
                    row["status"] = "already_present"
                    out["results"].append(row)
                    print(f"  = {name}  (already present)")
                    continue
                ok = await save_one(page, e["url"], name, row, description=_describe(e))
                row["status"] = "saved" if ok else "FAILED"
                out["results"].append(row)
                print(f"  {'+' if ok else 'X'} {name}  {row.get('error', '')}")
                if not ok:
                    out["stopped_on"] = name
                    SHOTS.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(SHOTS / "commit_failure.png"))
                    break
            # Read-back from a fresh load.
            await goto_map(page, base_url)
            after = await saved_filter_names(page) or []
            out["existing_after"] = after
            missing = [e["name"] for e in items if e["name"] not in after
                       and not any(n and e["name"].startswith(n) and len(n) >= 20 for n in after)]
            out["missing_after"] = missing
            return out

        if mode == "verify":
            for e in items:
                row = {"name": e["name"], "measured_count": e.get("measured_count")}
                row.update(await reload_check(page, base_url, e["name"], e["url"],
                                              e.get("measured_count")))
                out["results"].append(row)
                flag = ("ok" if row.get("present") and row.get("params_match")
                        else "MISSING" if not row.get("present") else "PARAM MISMATCH")
                print(f"  {flag:14s} {e['name'][:60]:60s} count {row.get('count')} "
                      f"(measured {e.get('measured_count')})")
            return out
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Save and verify SiftMap presets for one county")
    ap.add_argument("--fips", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--discover", action="store_true")
    g.add_argument("--smoke-test", action="store_true")
    g.add_argument("--commit", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--discover-delete", metavar="NAME",
                   help="probe one saved filter's row icons; clicks nothing")
    g.add_argument("--delete-name", metavar="NAME",
                   help="delete ONE saved filter; the name must start with 'ZZ '")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    mode = ("discover" if a.discover else "smoke" if a.smoke_test else
            "commit" if a.commit else "verify" if a.verify else
            "discover_delete" if a.discover_delete else "delete")
    target = a.discover_delete or a.delete_name
    out = asyncio.run(run(mode, a.fips, headless=not a.headed, target=target))
    path = ROOT / "output" / f"dpd_siftmap_presets_{a.fips}_{mode}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nwrote {path}")
    if out.get("error"):
        print("ERROR:", out["error"])
        return 1
    if mode == "smoke":
        r = out["results"][0]
        rb = r.get("readback") or {}
        ok = r.get("saved") and rb.get("present") and rb.get("params_match")
        print(f"smoke: saved={r.get('saved')} present={rb.get('present')} "
              f"params_match={rb.get('params_match')} count={rb.get('count')} "
              f"expected={rb.get('expected_count')}")
        for p in rb.get("problems") or []:
            print("   ", p)
        if r.get("error"):
            print("   error:", r["error"])
        return 0 if ok else 1
    if mode == "commit":
        bad = [r for r in out["results"] if r.get("status") == "FAILED"]
        print(f"commit: {sum(r.get('status') == 'saved' for r in out['results'])} saved, "
              f"{sum(r.get('status') == 'already_present' for r in out['results'])} already present, "
              f"{len(bad)} failed, missing after read-back: {out.get('missing_after')}")
        return 1 if (bad or out.get("missing_after")) else 0
    if mode == "verify":
        bad = [r for r in out["results"] if not (r.get("present") and r.get("params_match"))]
        print(f"verify: {len(out['results']) - len(bad)} ok, {len(bad)} not ok")
        return 1 if bad else 0
    if mode == "discover_delete":
        cands = out.get("delete_candidates") or []
        print(f"discover-delete: row found={bool(out.get('row'))}, "
              f"{len((out.get('row') or {}).get('controls') or [])} controls, "
              f"{len(cands)} delete candidate(s)")
        for c in cands:
            print(f"   candidate at ({c['x']},{c['y']}): cls={c['cls']!r} aria={c['aria']!r}")
        return 0 if len(cands) == 1 else 1
    if mode == "delete":
        r = out["results"][0]
        print(f"delete: {r['name']!r} deleted={r.get('deleted')}"
              + (f"  error: {r['error']}" if r.get("error") else ""))
        if r.get("names_after") is not None:
            print(f"   remaining saved filters: {len(r['names_after'])}")
        return 0 if r.get("deleted") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
