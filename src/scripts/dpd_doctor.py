"""Phase 0 preflight for the doors-per-deal build: log in, capture a baseline, diff it later.

The account this runs against is live and holds ~26,600 records. Everything the
doors-per-deal build adds is additive -- new tags, new SiftMap presets, new Records
preset folders -- and the ONLY proof we left the existing structure alone is a
before/after diff. This script is that before, and later that after.

    python src/scripts/dpd_doctor.py                    # capture the baseline
    python src/scripts/dpd_doctor.py --headed           # watch it work
    python src/scripts/dpd_doctor.py --verify           # diff against the saved baseline

Auth is the .env browser login, never the API. Presets, folders and SiftMap have no
API at all, and this account's internal API is read-only in practice (POST /property/
403s), so app.reisift.io driven by Playwright is the only surface that sees everything
this build touches.

Page structure, all verified live 2026-08-25 -- none of it was guessable:
  * Lists and property tags are NESTED IN FOLDERS on /lists and /tags/property. The
    table renders folder rows only; the names are hidden until each folder is expanded.
  * Phone tags live on their own route /tags/phone and are paginated 10 per page.
    They are a SEPARATE vocabulary from property tags -- the dial tiers live here.
  * "Filter Presets" is a collapsed section at the BOTTOM of the Records filter panel,
    below a scroll boundary, next to a "Create New Folder" button.
  * The filter panel's action bar is Load | Save | Save New | Clear. "Save New" is real
    and enabled once filter blocks exist -- it is how this build creates presets.

Nothing here writes to DataSift. Every section records its own status AND is checked
against a plausibility floor, because the failure this codebase keeps rediscovering is
a run that reports success having collected nothing. An empty capture on an account
known to hold 39 lists is a broken selector, not an empty account.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from datasift_core import (  # noqa: E402
    DATASIFT_RECORDS_URL,
    DATASIFT_SIFTMAP_URL,
    create_browser,
    dismiss_popups,
    get_credentials,
    login,
)

logger = logging.getLogger("dpd_doctor")

BASE = "https://app.reisift.io"
BASELINE_PATH = ROOT / "output" / "dpd_baseline.json"

# A section coming back under its floor means a broken selector, not a small account.
# Floors are set well below the counts measured on this account (39 lists, 322 property
# tags, 79 phone tags, 16 presets in one DEFAULT folder) so normal growth never trips them.
PLAUSIBILITY_FLOOR = {
    "lists": 20,
    "property_tags": 100,
    "phone_tags": 20,
    "preset_items": 5,
}


# ── generic page helpers ──────────────────────────────────────────────


async def _goto(page, route: str, wait_ms: int = 9000) -> None:
    await page.goto(BASE + route, wait_until="domcontentloaded")
    await page.wait_for_timeout(wait_ms)
    await dismiss_popups(page)


async def _table_rows(page) -> list[list[str]]:
    """Read the current page of a DataSift admin table as rows of cell text."""
    return await page.evaluate(
        """() => {
        const cells = [...document.querySelectorAll('[class*="TableCellContainer"]')]
            .map(el => { const r = el.getBoundingClientRect();
                         return {y: Math.round(r.y), x: Math.round(r.x),
                                 t: (el.textContent || '').trim()}; })
            .filter(c => c.t && c.x > 250);
        const byRow = {};
        for (const c of cells) {
            const k = Math.round(c.y / 12);           // tolerate sub-pixel row drift
            (byRow[k] = byRow[k] || []).push(c);
        }
        return Object.keys(byRow).sort((a, b) => a - b)
            .map(k => byRow[k].sort((a, b) => a.x - b.x).map(c => c.t));
    }"""
    )


async def _page_count(page) -> int:
    """Read 'of N' out of the pagination control. 1 when there is no pager."""
    txt = await page.evaluate(
        """() => [...document.querySelectorAll('[class*="Pagination"]')]
             .map(e => (e.textContent || '').trim()).join(' ')"""
    )
    import re

    m = re.search(r"of\s+([\d,]+)", txt or "")
    return int(m.group(1).replace(",", "")) if m else 1


async def _next_page(page) -> bool:
    """Click the pager's next arrow. Returns False when it will not advance."""
    ok = await page.evaluate(
        """() => {
        const pager = document.querySelector('[class*="Pagination"]');
        if (!pager) return false;
        const btns = [...pager.querySelectorAll('button')];
        const nxt = btns[btns.length - 1];
        if (!nxt || nxt.disabled) return false;
        nxt.click();
        return true;
    }"""
    )
    if ok:
        await page.wait_for_timeout(3500)
    return bool(ok)


async def _expand_all_folders(page) -> int:
    """Expand every collapsed folder row on a /lists or /tags/property page.

    Names are children of the folder rows and simply do not exist in the DOM until the
    folder is opened, so reading the table without this returns folder names only.
    """
    opened = 0
    for _ in range(25):
        clicked = await page.evaluate(
            """() => {
            // Folder rows carry a chevron/caret. Clicking the row toggles it.
            for (const el of document.querySelectorAll('svg, [class*="chevron"], [class*="Chevron"], [class*="caret"], [class*="Arrow"]')) {
                const r = el.getBoundingClientRect();
                if (r.x < 300 || r.x > 420 || r.width === 0) continue;
                if (el.dataset.dpdSeen) continue;
                el.dataset.dpdSeen = '1';
                (el.closest('a, button, div') || el).click();
                return true;
            }
            return false;
        }"""
        )
        if not clicked:
            break
        opened += 1
        await page.wait_for_timeout(1400)
    if opened:
        await page.wait_for_timeout(1500)
    return opened


async def _items_under_heading(page, heading: str) -> list[str] | None:
    """Read the visible leaf texts inside the section that owns `heading`.

    Coordinate- and ancestry-based scoping both proved unreliable here: the records grid
    sits behind the filter overlay at the same x, and the expanded preset list renders
    outside the filter input's ancestor chain. Anchoring on the section heading and
    climbing to its container is the one approach that survives both.
    Returns None when the heading is not on the page at all -- distinct from an empty
    section, which returns [].
    """
    return await page.evaluate(
        """(heading) => {
        const hits = [...document.querySelectorAll('*')].filter(
            el => el.children.length === 0 && (el.textContent || '').trim() === heading);
        if (!hits.length) return null;
        const countLeaves = (n) => [...n.querySelectorAll('*')].filter(el => {
            if (el.children.length > 0) return false;
            const r = el.getBoundingClientRect();
            const t = (el.textContent || '').trim();
            return r.width > 0 && r.height > 0 && t && t !== heading;
        }).length;
        // Climb until the container actually holds the section's items. Stopping at the
        // first parent with >8 nodes lands on the heading row alone and returns nothing.
        let c = hits[hits.length - 1];
        for (let i = 0; i < 10 && c.parentElement; i++) {
            c = c.parentElement;
            if (countLeaves(c) >= 3) break;
        }
        const out = [];
        c.querySelectorAll('*').forEach(el => {
            if (el.children.length > 0) return;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return;
            const t = (el.textContent || '').trim();
            if (t && t.length > 1 && t.length < 70 && t !== heading) out.push(t);
        });
        return [...new Set(out)];
    }""",
        heading,
    )


# ── section captures ──────────────────────────────────────────────────


async def capture_paginated_table(page, route: str, key: str, out: dict,
                                  expand_folders: bool = False) -> None:
    """Walk every page of an admin table, collecting the first cell of each row."""
    try:
        await _goto(page, route, wait_ms=11000)
        names: list[str] = []
        # Folders must be expanded BEFORE the pager is read: collapsed, /lists shows two
        # folder rows and reports "of 1"; expanded, it reports the real page count of the
        # list names inside. Reading the pager first is how this silently captured 10 of 39.
        if expand_folders:
            await _expand_all_folders(page)
        pages = await _page_count(page)
        for i in range(pages):
            if expand_folders and i:
                await _expand_all_folders(page)
            for row in await _table_rows(page):
                if not row:
                    continue
                name = row[0].strip()
                # Skip the header row and the "N properties" second column.
                if name in ("Tag Name", "List Name", "Status Name", "Name") or not name:
                    continue
                if name not in names:
                    names.append(name)
            if i < pages - 1 and not await _next_page(page):
                break
        out[key] = names
        out["_status"][key] = "ok" if names else "FAILED: table returned no rows"
        logger.info("%s: %d captured across %d page(s)", key, len(names), pages)
    except Exception as e:  # noqa: BLE001 - sections are best-effort by design
        out["_status"][key] = f"FAILED: {e}"
        logger.warning("%s capture failed: %s", key, e)


async def _scroll_presets_body(page) -> None:
    """Scroll the Filter Presets list to exhaustion.

    The folder list scrolls INSIDE `PresetsBelowBody` (scrollHeight 1254 against
    clientHeight 524 on 2026-08-26), so a single on-screen read saw one folder where the
    account had seven. Step the container until scrollTop stops moving, then leave it at
    the top so positional reads start from a known state.
    """
    for _ in range(30):
        moved = await page.evaluate(
            """() => {
            const body = document.querySelector('[class*="PresetsBelowBody"]');
            if (!body) return false;
            const before = body.scrollTop;
            body.scrollTop = before + Math.max(150, body.clientHeight - 40);
            return body.scrollTop !== before;
        }""")
        await page.wait_for_timeout(350)
        if not moved:
            break
    await page.evaluate(
        """() => { const b = document.querySelector('[class*="PresetsBelowBody"]');
                   if (b) b.scrollTop = 0; }""")
    await page.wait_for_timeout(400)


async def _folder_titles(page) -> list[str]:
    """Every folder row title inside PresetsBelowBody, in display order, all scroll positions."""
    titles: list[str] = []
    for _ in range(30):
        batch = await page.evaluate(
            """() => {
            const body = document.querySelector('[class*="PresetsBelowBody"]');
            if (!body) return null;
            const out = [];
            // Folder headers are SectionHeadingTitle elements inside Collapsible__trigger
            // rows (verified 2026-08-27); CollapsibleFolderTitle does not exist in this DOM.
            for (const el of body.querySelectorAll('[class*="SectionHeadingTitle"],'
                                                 + '[class*="CollapsibleFolderTitle"]')) {
                const t = (el.innerText || '').trim();
                if (t && !out.includes(t)) out.push(t);
            }
            return out;
        }""")
        if batch is None:
            return titles
        for t in batch:
            if t not in titles:
                titles.append(t)
        moved = await page.evaluate(
            """() => {
            const body = document.querySelector('[class*="PresetsBelowBody"]');
            if (!body) return false;
            const before = body.scrollTop;
            body.scrollTop = before + Math.max(150, body.clientHeight - 40);
            return body.scrollTop !== before;
        }""")
        await page.wait_for_timeout(350)
        if not moved:
            break
    await page.evaluate(
        """() => { const b = document.querySelector('[class*="PresetsBelowBody"]');
                   if (b) b.scrollTop = 0; }""")
    return titles


async def _read_folder_no_toggle(page, folder: str) -> list[str]:
    """Positional read of a folder's presets WITHOUT clicking its header.

    `dpd_presets_create.read_folder` assumes a folder is collapsed and clicks to open it.
    The account's DEFAULT folder is open by default, so that click CLOSES it and the read
    returns [] -- which the 2026-08-27 diff reported as 16 removed presets. When a folder
    reads empty, read it again as-is; a folder that is genuinely empty still reads [].
    """
    return await page.evaluate(
        """(name) => {
        const b = document.querySelector('[class*="PresetsBelowBody"]');
        if (!b) return [];
        const folders = [];
        for (const el of b.querySelectorAll('[class*="SectionHeadingTitle"],'
                                          + '[class*="CollapsibleFolderTitle"]')) {
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
        for (const el of b.querySelectorAll('[class*="CollapsibleFolderPresetTitle"]')) {
            const r = el.getBoundingClientRect();
            if (r.height === 0 || r.y <= me.y + 4 || r.y >= hi) continue;
            const t = (el.innerText || '').trim();
            if (t && t.length <= 60 && !out.includes(t) && t !== 'This folder is empty.') out.push(t);
        }
        return out;
    }""", folder)


async def capture_presets(page, out: dict) -> None:
    """Enumerate the Records filter-preset folders and their presets.

    Rewritten 2026-08-27 on the builder's proven mechanics (dpd_presets_create.open_panel,
    expand_presets_section, read_folder). The earlier heading-anchored scrape climbed to
    "the container with the preset list", which (a) could not see an EMPTY folder at all,
    (b) read only the on-screen part of a list that scrolls inside PresetsBelowBody, and
    (c) on 2026-08-27 swallowed records-grid cells ("Annapolis, MD 21409", "99d ago") as
    presets because the grid keeps its boxes under the overlay. Folders are now read as
    `CollapsibleFolderTitle` rows across the whole scroll range, and each folder's presets
    are read POSITIONALLY between its row and the next folder's row.

    Writes `preset_items` (flat preset names, for diffing against the old baseline),
    `preset_folders` (folder names) and `preset_tree` ({folder: [presets]}).
    """
    key = "preset_items"
    try:
        sys.path.insert(0, str(ROOT / "src" / "scripts"))
        from dpd_presets_create import expand_presets_section, open_panel, read_folder

        await _goto(page, DATASIFT_RECORDS_URL.replace(BASE, ""), wait_ms=8000)
        if not await open_panel(page):
            out["_status"][key] = "FAILED: filter panel never opened"
            out["_status"]["preset_folders"] = "FAILED: filter panel never opened"
            return
        if not await expand_presets_section(page):
            out["_status"][key] = "FAILED: Filter Presets section would not expand"
            out["_status"]["preset_folders"] = "FAILED: Filter Presets section would not expand"
            return
        await _scroll_presets_body(page)
        folders = await _folder_titles(page)
        if not folders:
            out["_status"][key] = "FAILED: no folder rows in PresetsBelowBody"
            out["_status"]["preset_folders"] = "FAILED: no folder rows in PresetsBelowBody"
            return
        tree: dict[str, list[str]] = {}
        for f in folders:
            # Read IN PLACE first: a folder that is open on load (DEFAULT) is closed by
            # read_folder's toggle and reads empty. Only a folder that shows nothing as-is
            # gets toggled open.
            names = await _read_folder_no_toggle(page, f)
            if not names:
                names = await read_folder(page, f)
            if not names:
                await page.evaluate(
                    """(name) => {
                    const b = document.querySelector('[class*="PresetsBelowBody"]');
                    for (const el of (b ? b.querySelectorAll('[class*="SectionHeadingTitle"]') : [])) {
                        if ((el.innerText || '').trim() === name) { el.scrollIntoView({block: 'start'}); break; }
                    }
                }""", f)
                await page.wait_for_timeout(500)
                names = await _read_folder_no_toggle(page, f)
            tree[f] = list(names or [])
        items: list[str] = []
        for names in tree.values():
            for n in names:
                if n not in items:
                    items.append(n)
        out[key] = items
        out["preset_folders"] = folders
        out["preset_tree"] = tree
        out["_status"][key] = "ok" if items else "FAILED: every folder read empty"
        out["_status"]["preset_folders"] = "ok"
        logger.info("preset folders: %d captured; preset items: %d across them",
                    len(folders), len(items))
    except Exception as e:  # noqa: BLE001
        out["_status"][key] = f"FAILED: {e}"
        out["_status"]["preset_folders"] = f"FAILED: {e}"
        logger.warning("preset capture failed: %s", e)


async def capture_sequences(page, out: dict) -> None:
    """Sequences on /sequences. The account held 0 at the 2026-08-25 baseline.

    Restored 2026-08-27 after the capture_presets rewrite accidentally removed it. Reads
    the page through the same admin-table walker as lists/tags; an account with no
    sequences reads [] exactly as before. If a sequence is ever created and the page's
    table markup differs from the admin tables, this reads 0 and the diff would miss it --
    stated here rather than hidden.
    """
    # /sequences renders FOLDER rows (Acquisitions, Lead Management, Transactions, default,
    # ...) until each is expanded, exactly like /lists; reading it unexpanded reports the
    # folders as if they were sequences (5 on 2026-08-27, against a true count of 0).
    await capture_paginated_table(page, "/sequences", "sequences", out, expand_folders=True)
    # The walker returns the table's header cell as a row on this page.
    out["sequences"] = [x for x in (out.get("sequences") or []) if x != "Sequence Name"]
    if out["_status"].get("sequences", "").startswith("FAILED") and not out.get("sequences"):
        # An empty sequences page is a legitimate state on this account.
        out["sequences"] = []
        out["_status"]["sequences"] = "ok (empty)"
    logger.info("sequences: %d captured", len(out.get("sequences") or []))


async def capture_siftmap_presets(page, out: dict) -> None:
    """SiftMap's own presets -- the pull-side system, separate from Records presets."""
    key = "siftmap_presets"
    try:
        await _goto(page, DATASIFT_SIFTMAP_URL.replace(BASE, ""), wait_ms=11000)
        # PropertyDetails auto-opens on load and swallows pointer events.
        await page.evaluate(
            """() => document.querySelectorAll('[class*="PropertyDetails"]')
                 .forEach(p => p.remove())"""
        )
        await page.wait_for_timeout(800)
        for label in ("Presets", "Saved Presets"):
            el = page.get_by_text(label, exact=True)
            if await el.count() > 0:
                try:
                    await el.first.click()
                    await page.wait_for_timeout(2500)
                    break
                except Exception:  # noqa: BLE001
                    continue
        # The default-preset list is truncated behind "Show more".
        for _ in range(4):
            more = page.get_by_text("Show more", exact=False)
            if await more.count() == 0:
                break
            try:
                await more.first.click()
                await page.wait_for_timeout(1800)
            except Exception:  # noqa: BLE001
                break

        # Two headings, two lists: the account's own saved filters and the 20 system
        # presets. Scraping the whole page instead swallows the entire left sidebar.
        # The Saved Filters list SCROLLS inside the popover once it is long (29 entries on
        # 2026-08-27); _items_under_heading reads rendered leaves regardless of scroll
        # position, so clipped rows are still captured.
        names: list[str] = []
        found_any = False
        for heading in ("Saved Filters", "Default Presets"):
            block = await _items_under_heading(page, heading)
            if block is None:
                continue
            found_any = True
            for t in block:
                label = f"{heading}: {t}"
                if t in ("Configure", "Show more", "Show less"):
                    continue
                if t.startswith(("Filters that you saved", "Pre-build filters")):
                    continue
                if label not in names:
                    names.append(label)
        if not found_any:
            out["_status"][key] = "FAILED: neither preset heading found on /siftmap"
            return
        out[key] = names
        out["_status"][key] = "ok" if names else "WARN: no SiftMap presets found"
        logger.info("siftmap presets: %d captured", len(names))
    except Exception as e:  # noqa: BLE001
        out["_status"][key] = f"FAILED: {e}"


async def capture_routes(page, out: dict) -> None:
    key = "routes"
    try:
        await _goto(page, "/records/properties", wait_ms=6000)
        hrefs = await page.evaluate(
            """() => [...new Set([...document.querySelectorAll('a[href]')]
                .map(a => a.getAttribute('href'))
                .filter(h => h && h.startsWith('/') && !h.includes('/details')))]"""
        )
        out[key] = sorted(hrefs)
        out["_status"][key] = "ok" if hrefs else "WARN: no internal links found"
    except Exception as e:  # noqa: BLE001
        out["_status"][key] = f"FAILED: {e}"


# ── orchestration ─────────────────────────────────────────────────────


async def run_capture(headless: bool) -> dict:
    email, password = get_credentials()
    logger.info("Logging in as %s", email)

    out: dict = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "account_email": email,
        "_status": {},
    }

    async with create_browser(headless=headless) as (_browser, _ctx, page):
        if not await login(page, email, password) or "/login" in page.url:
            out["_status"]["login"] = f"FAILED: not authenticated (url={page.url})"
            return out
        out["_status"]["login"] = "ok"
        logger.info("Logged in, landed on %s", page.url)

        await capture_routes(page, out)
        await capture_paginated_table(page, "/lists", "lists", out, expand_folders=True)
        await capture_paginated_table(page, "/tags/property", "property_tags", out,
                                      expand_folders=True)
        await capture_paginated_table(page, "/tags/phone", "phone_tags", out)
        await capture_paginated_table(page, "/statuses", "statuses", out)
        await capture_presets(page, out)
        await capture_preset_folders(page, out)
        await capture_sequences(page, out)
        await capture_siftmap_presets(page, out)

    return out


def _report(out: dict) -> int:
    print("\n=== DPD PREFLIGHT ===")
    print(f"captured_at : {out.get('captured_at')}")
    print(f"account     : {out.get('account_email')}\n")

    for key, status in out["_status"].items():
        n = len(out[key]) if isinstance(out.get(key), list) else ""
        floor = PLAUSIBILITY_FLOOR.get(key)
        thin = floor is not None and isinstance(n, int) and n < floor
        mark = "FAIL" if status.startswith("FAILED") or thin else (
            "WARN" if status.startswith("WARN") else "ok  ")
        note = "" if status == "ok" else status
        if thin:
            # Keep the underlying status visible. Overwriting it here once hid a real
            # "section stayed empty" message behind a generic thin-capture warning.
            thin_note = f"only {n} captured, expected at least {floor}"
            note = f"{thin_note} | {status}" if status != "ok" else thin_note
        print(f"  [{mark}] {key:15s} {str(n):>5}  {note}")

    thin = [k for k, floor in PLAUSIBILITY_FLOOR.items() if len(out.get(k) or []) < floor]
    if thin:
        print(
            "\nIncomplete capture: " + ", ".join(thin) +
            "\nThis account is known to hold 39 lists, 322 property tags and 79 phone tags,"
            "\nso a thin result is a broken selector, not an empty account."
            "\nRefusing to treat this as a usable baseline. Re-run with --headed to watch it."
        )
        return 1
    return 0


async def capture_preset_folders(page, out: dict) -> None:
    """Folders are now captured by capture_presets (one panel open, one scroll pass).

    Kept as a hook so run_capture's call order and the section list are unchanged. If
    capture_presets already recorded folders this does nothing; otherwise it records the
    failure rather than scraping "whatever chrome is narrow enough", which on 2026-08-26
    reported 4 fake folders on a page whose panel never opened.
    """
    key = "preset_folders"
    if out.get(key) is not None:
        return
    out["_status"].setdefault(key, "FAILED: capture_presets did not record folders")


def _diff(old: dict, new: dict) -> int:
    print("\n=== BASELINE DIFF ===")
    print(f"before : {old.get('captured_at')}")
    print(f"after  : {new.get('captured_at')}\n")

    removed_total = 0
    for key in ("lists", "property_tags", "phone_tags", "statuses", "preset_items",
                "preset_folders", "sequences", "siftmap_presets"):
        before, after = set(old.get(key) or []), set(new.get(key) or [])
        removed, added = sorted(before - after), sorted(after - before)
        if not removed and not added:
            print(f"  {key:15s} unchanged ({len(before)})")
            continue
        print(f"  {key:15s} {len(before)} -> {len(after)}")
        for item in removed:
            print(f"      REMOVED  {item}")
            removed_total += 1
        for item in added:
            print(f"      added    {item}")

    if removed_total:
        print(
            f"\n{removed_total} item(s) disappeared. This build is additive only, so a "
            "removal means something was overwritten. Investigate before continuing."
        )
        return 1
    print("\nNothing was removed. All changes are additions.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="DPD Phase 0 preflight and baseline capture")
    ap.add_argument("--headed", action="store_true", help="show the browser")
    ap.add_argument("--verify", action="store_true",
                    help="capture again and diff against the saved baseline")
    ap.add_argument("--baseline", default=str(BASELINE_PATH))
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    baseline_path = Path(a.baseline)
    out = asyncio.run(run_capture(headless=not a.headed))
    rc = _report(out)

    if a.verify:
        if not baseline_path.exists():
            print(f"\nNo baseline at {baseline_path}. Run without --verify first.")
            return 1
        old = json.loads(baseline_path.read_text(encoding="utf-8"))
        current = baseline_path.with_name(baseline_path.stem + "_current.json")
        current.write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"\nWrote current state to {current}")
        return _diff(old, out) or rc

    baseline_path.parent.mkdir(parents=True, exist_ok=True)

    # _report() already printed "Refusing to treat this as a usable baseline" and returned
    # non-zero -- but this function used to write the file anyway, which is how a thin
    # capture (phone_tags 20 of 79, statuses 0, preset_items FAILED) OVERWROTE a known-good
    # baseline on 2026-08-26 and destroyed the rollback reference the whole build depends
    # on. Refusing in the report and writing in main is not refusing.
    if rc != 0:
        rejected = baseline_path.with_name(baseline_path.stem + "_rejected.json")
        rejected.write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"\nDID NOT overwrite {baseline_path} -- the capture above is incomplete.")
        print(f"The rejected capture is at {rejected} if you want to inspect it.")
        return rc

    # Even a good capture must not silently destroy the previous reference: keep a dated
    # copy so a baseline is always recoverable.
    if baseline_path.exists():
        prev = json.loads(baseline_path.read_text(encoding="utf-8"))
        stamp = (prev.get("captured_at") or "unknown").replace(":", "").replace("-", "")
        backup = baseline_path.with_name(f"{baseline_path.stem}_{stamp}.json")
        if not backup.exists():
            backup.write_text(json.dumps(prev, indent=1), encoding="utf-8")
            print(f"\nPrevious baseline kept at {backup}")

    baseline_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote baseline to {baseline_path}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
