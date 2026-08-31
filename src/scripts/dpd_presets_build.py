"""Phase 5: the 12 Records preset folders, and (next) the 73 presets inside them.

    python src/scripts/dpd_presets_build.py --folders            # dry run
    python src/scripts/dpd_presets_build.py --folders --commit   # create the 12 folders

Folders only, for now. The 73 presets need the filter-block vocabulary mapped first (see
FILTER_BLOCKS below) and that is a separate discovery pass; creating the folders is
self-contained, verifiable on its own, and is what the records land into.

THE ONLY WRITE IS CREATING A FOLDER THAT DOES NOT ALREADY EXIST. Existing folders --
notably this account's single `DEFAULT` folder holding its 16 per-dialer presets -- are
never opened, renamed or touched. Re-running is a no-op.

Panel mechanics, read off the live page (output/dpd_preset_probe.json):

  * The action bar is `Load | Save | Save New | Clear` at the top of the panel. **Save and
    Save New are DISABLED until at least one filter block exists**, which is why a preset
    cannot be created from an empty panel and why the 73 need their blocks built first.
  * `Filter Presets` is a COLLAPSED section at the BOTTOM of the panel, below a scroll
    boundary, and `Create New Folder` lives in its header row. The panel is a scrollable
    div, not the viewport, so `scroll_into_view_if_needed()` does nothing here -- scroll
    the container itself.
  * Every click in this panel goes through a real mouse click at the element's centre.
    Playwright's own click reports "outside of viewport" or gets intercepted by
    `RecordsFiltersstyles__RecordsFiltersSection`, and a JS .click() silently no-ops on
    several of these controls.
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

BASE = "https://app.reisift.io"
BASELINE = ROOT / "output" / "dpd_baseline.json"
OUT = ROOT / "output" / "dpd_presets_build.json"

# The 12 folders, with the preset count each will hold. Numbered so they sort in the order
# the cadence is worked. They collide with nothing: this account has a single `DEFAULT`
# folder (the `00 Niche Sequential Marketing` / `01. Bulk Sequential Marketing` folders
# described elsewhere in CLAUDE.md are Ty's account, not this one).
FOLDERS = [
    ("01 HOTTEST - CALL", 6, "Priority 1"),
    ("02 HOTTEST - MAIL", 6, "Priority 1"),
    ("03 STRONG - CALL", 6, "Priority 2"),
    ("04 STRONG - MAIL", 6, "Priority 2"),
    ("05 FTM - CALL", 6, "FTM"),
    ("06 FTM - MAIL", 6, "FTM"),
    ("07 TIER 2 - CALL", 6, "Tier 2 minus both priority tags"),
    ("08 TIER 2 - MAIL", 6, "Tier 2 minus both priority tags"),
    ("09 BULK - CALL", 9, "Priority 1 or Priority 2 or Tier 2"),
    ("10 BULK - MAIL", 6, "Priority 1 or Priority 2 or Tier 2"),
    ("11 DEEP PROSPECTING", 5, "all tiers"),
    ("12 REACTIVATION", 5, "all tiers"),
]

# 48 + 9 + 6 + 5 + 5 = 73. Asserted rather than trusted, because a miscount here is a
# missing stage in somebody's call cadence.
assert sum(n for _, n, _ in FOLDERS) == 73, "the folder table must total 73 presets"

# The filter blocks the 73 presets need. NOT YET MAPPED to panel controls -- listed here so
# the next pass knows exactly what to discover, and so nothing gets built on a guess.
FILTER_BLOCKS = [
    "Tags (include and exclude)",
    "Lists (exclude: Low Equity, Negative Equity)",
    "Property Status (exclude the done/dead statuses)",
    "predictivecall_attempts (numeric range, n to n)",
    "directmail_attempts (numeric range, n to n)",
    "Has phone numbers (yes/no)",
    "Skiptraced (yes/no)",
    "Vacant Mailing (no)",
    "Last direct mailed (a month or more ago)",
    "Structure type (single family; blank is unknown, surfaced not dropped)",
]


def _existing_folders() -> set[str]:
    """Preset folder names from the Phase 0 capture."""
    if not BASELINE.exists():
        return set()
    b = json.loads(BASELINE.read_text(encoding="utf-8"))
    out = set()
    for it in b.get("preset_items") or []:
        name = it if isinstance(it, str) else (it.get("folder") or it.get("name") or "")
        if name:
            out.add(name.strip())
    return out


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


async def _scroll_panel_bottom(page) -> None:
    """The panel is a scrollable div. Scroll IT, not the viewport."""
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
    """Real mouse click on the narrowest visible control matching `text`."""
    box = await page.evaluate(
        """([txt, exact]) => {
        let best = null;
        for (const el of document.querySelectorAll('button,a,div,span,[role="button"]')) {
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


async def _read_folders(page) -> list[str]:
    """Folder names in the Filter Presets section, read from the section's own body.

    The previous version scraped any short label with x>300 and width<400, which is a
    heuristic, not a read: it cannot distinguish a folder row from page furniture, and it
    only sees what is on screen. The folder list SCROLLS inside `PresetsBelowBody`
    (scrollHeight 1254 against clientHeight 524), which is how the Phase 0 capture missed
    six pre-existing folders and reported this account as having one.

    The body's own innerText is the whole list, scrolled or not.
    """
    txt = await page.evaluate(
        """() => {
        const b = document.querySelector('[class*="PresetsBelowBody"]');
        return b ? (b.innerText || '') : '';
    }""")
    return [ln.strip() for ln in txt.splitlines() if ln.strip()]


async def create_folder(page, name: str) -> dict:
    """Create one preset folder from the expanded Filter Presets section."""
    await _scroll_panel_bottom(page)
    if not await _click_text(page, "Create New Folder"):
        return {"folder": name, "status": "no_create_control",
                "why": "no 'Create New Folder' control in the panel; is Filter Presets "
                       "expanded?"}

    typed = await page.evaluate(
        """(nm) => {
        const ins = [...document.querySelectorAll('input:not([type=hidden])')]
            .filter(i => { const r = i.getBoundingClientRect();
                           return r.width > 60 && r.x > 300; });
        if (!ins.length) return false;
        const el = ins[ins.length - 1];
        const set = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        el.focus(); set.call(el, nm);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    }""", name)
    if not typed:
        return {"folder": name, "status": "no_input",
                "why": "clicked Create New Folder but no text input appeared"}
    await page.wait_for_timeout(1000)

    btn = await page.evaluate(
        """() => {
        for (const b of document.querySelectorAll('button')) {
            const r = b.getBoundingClientRect();
            if (r.width === 0 || r.x < 300) continue;
            const t = (b.innerText || '').trim().toLowerCase();
            if (['create', 'save', 'add', 'confirm', 'create folder'].includes(t)) {
                return {x: r.x + r.width / 2, y: r.y + r.height / 2, disabled: b.disabled};
            }
        }
        return null;
    }""")
    if not btn:
        return {"folder": name, "status": "no_save_button",
                "why": "typed the name but found no Create/Save button"}
    if btn["disabled"]:
        # Never click a disabled control and call it done.
        return {"folder": name, "status": "button_still_disabled",
                "why": "the save button stayed disabled, so React never received the "
                       "name; the folder was NOT created"}
    await page.mouse.click(btn["x"], btn["y"])
    await page.wait_for_timeout(2500)
    return {"folder": name, "status": "clicked_create"}


async def run(commit: bool, headless: bool) -> int:
    email, password = get_credentials()
    out = {"ran_at": datetime.now().isoformat(timespec="seconds"), "commit": commit,
           "folders_planned": [{"name": n, "presets": c, "entry": e}
                               for n, c, e in FOLDERS],
           "filter_blocks_still_to_map": FILTER_BLOCKS}

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
        await _scroll_panel_bottom(page)
        await _click_text(page, "Filter Preset", exact=False)
        await page.wait_for_timeout(1500)

        live = await _read_folders(page)
        base = _existing_folders()
        known = {s.strip().upper() for s in list(live) + list(base)}
        out["existing_folders_seen"] = live

        rows = []
        for name, count, entry in FOLDERS:
            exists = name.upper() in known
            rows.append({"folder": name, "presets": count, "entry": entry,
                         "status": "already_exists" if exists else "missing"})
        out["results"] = rows

        print("\n=== 12 PRESET FOLDERS ===")
        print(f"  {len(live)} labels read in the panel; Phase 0 saw {len(base)}\n")
        for r in rows:
            print(f"  {r['folder']:24s} {r['presets']:>2d} presets  {r['status']:16s} "
                  f"entry: {r['entry']}")
        print(f"\n  {sum(r['presets'] for r in rows)} presets total across 12 folders")

        todo = [r for r in rows if r["status"] == "missing"]
        if not todo:
            print("\n  All 12 already exist. Nothing to do.")
        elif not commit:
            print(f"\n  Would create {len(todo)} folders.")
            print("  Dry run -- nothing written. Re-run with --commit.")
        else:
            print(f"\n  Creating {len(todo)}...")
            for r in todo:
                res = await create_folder(page, r["folder"])
                r.update(res)
                extra = ("  " + res["why"]) if res.get("why") else ""
                print(f"    {r['folder']:24s} {res['status']}{extra}")
                if res["status"] != "clicked_create":
                    # Stop on the first structural failure rather than repeating it 11
                    # times against a panel that clearly is not in the expected state.
                    print("    stopping: the panel is not in the state this expects")
                    break

            # Read back from a fresh load.
            await page.goto(f"{BASE}/records", wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            await dismiss_popups(page)
            await _open_panel(page)
            await _scroll_panel_bottom(page)
            await _click_text(page, "Filter Preset", exact=False)
            await page.wait_for_timeout(1500)
            after = {s.strip().upper() for s in await _read_folders(page)}
            print("\n  Read-back:")
            ok = 0
            for r in rows:
                present = r["folder"].upper() in after
                r["verified"] = present
                ok += bool(present)
                print(f"    {r['folder']:24s} {'PRESENT' if present else 'MISSING'}")
            out["verified_count"] = ok
            print(f"\n  {ok} of {len(rows)} folders verified present.")
            if ok < len(rows):
                OUT.parent.mkdir(parents=True, exist_ok=True)
                OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
                print(f"\nWrote {OUT}")
                return 1

    print("\n  Still to map before the 73 presets can be built:")
    for f in FILTER_BLOCKS:
        print(f"    - {f}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote {OUT}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--folders", action="store_true",
                    help="operate on the 12 preset folders")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    if not a.folders:
        print("nothing to do: pass --folders (the 73 presets are not built yet)")
        return 0
    return asyncio.run(run(a.commit, a.headless))


if __name__ == "__main__":
    raise SystemExit(main())
