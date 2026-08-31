"""Phase 4a: create the four entry tags the Records presets filter on.

Roughly 60 of the 73 presets gate on `Priority 1`, `Priority 2`, `FTM` or `Tier 2`, and the
Phase 0 baseline proves NONE of them exist on this account (334 property tags; the only
near-matches are `SiftMap` and `Siftmap Preforeclosure`). DataSift's tag filter is a styled
dropdown populated from tags that already exist, so building the presets first would save
them with an empty tag block -- structurally present, silently matching the wrong records.
The tags therefore have to exist before the presets, and before the pull that stamps them.

    python src/scripts/dpd_tags_create.py --discover   # read-only: dump the page controls
    python src/scripts/dpd_tags_create.py              # dry run, says what it would create
    python src/scripts/dpd_tags_create.py --commit     # create the missing tags

THE ONLY WRITE THIS SCRIPT MAKES IS CREATING A TAG THAT DOES NOT ALREADY EXIST. It never
renames, never deletes, never touches a record, and never touches an existing tag. A tag
that is already present is reported as present and skipped, so re-running is safe.

--discover exists because the standalone tag-creation control on /tags/property has never
been driven by this codebase. The only tag-creating code today is inside the upload wizard
(datasift_uploader.py:329), which creates a tag as a side effect of an upload -- not usable
here. Guessing a selector against a styled-component admin page is how you get a run that
reports success having clicked nothing, so the control is read off the live page first.
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
TAGS_URL = f"{BASE}/tags/property"
BASELINE = ROOT / "output" / "dpd_baseline.json"
OUT = ROOT / "output" / "dpd_tags_created.json"

# The tags the 73 presets filter on that this account does not already have. Priority 1/2
# and Tier 2 are stamped at pull time in the SiftMap Add-Records modal and FTM by the daily
# county-direct pulls -- but a preset cannot SELECT a tag that does not yet exist, so they
# have to be created before Phase 5 rather than left to appear with the records.
#
# Deliberately short. Everything else the suppression stack needs is already on the account
# in some form and must NOT be duplicated: `Return Mail`, `Vacant`, `Deceased Owner` and
# `Obituaries` are live property tags; `Low Equity` / `Negative Equity` / `High Equity` are
# LISTS; `Sold` and `Already Sold` are STATUSES. Creating a second `Sold` as a tag would
# split the suppression across two vocabularies.
ENTRY_TAGS = [
    ("Priority 1", "entry tag - folders 01 HOTTEST - CALL / 02 HOTTEST - MAIL"),
    ("Priority 2", "entry tag - folders 03 STRONG - CALL / 04 STRONG - MAIL"),
    ("FTM", "entry tag - folders 05 FTM - CALL / 06 FTM - MAIL"),
    ("Tier 2", "entry tag - folders 07 TIER 2 - CALL / 08 TIER 2 - MAIL"),
    ("Mail Only", "suppression #6 - excluded from every CALL preset (all phones score <20)"),
    ("Rehash Ready", "the 5th preset in folder 12 REACTIVATION"),
]

# Tags live inside a folder. This account has exactly one, `default`, holding 34 pages.
FOLDER = "default"


def _baseline_tags() -> set[str]:
    """Tag names from the Phase 0 capture, if it is on disk."""
    if not BASELINE.exists():
        return set()
    b = json.loads(BASELINE.read_text(encoding="utf-8"))
    out = set()
    for t in b.get("property_tags") or []:
        name = t if isinstance(t, str) else (t.get("name") or "")
        if name:
            out.add(name.strip())
    return out


async def _enter_folder(page, folder: str = FOLDER) -> bool:
    """Click INTO a tag folder. The create control only exists in this state.

    At the top level /tags/property offers only "Create Folder" and a search box; open a
    folder and an "Add New Tag" button appears in the same header slot. Two mechanics, both
    learned the hard way:

      * A JS .click() does nothing on this page -- an early probe produced three
        byte-identical screenshots. It needs a real mouse click at the element's centre.
      * Take the NARROWEST element whose text is the folder name. The row's outer div also
        reads "default" and its centre lands 500px right of the link, on empty space.
    """
    box = await page.evaluate(
        """(name) => {
        let best = null;
        for (const el of document.querySelectorAll('a,span,div,button,[role="button"]')) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.x < 300 || r.y < 140) continue;
            if ((el.innerText || '').trim() !== name) continue;
            if (!best || r.width < best.w) {
                best = {x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width};
            }
        }
        return best;
    }""", folder)
    if not box:
        return False
    await page.mouse.click(box["x"], box["y"])
    await page.wait_for_timeout(3000)
    await dismiss_popups(page)
    # Prove we are inside: the create control is the thing that only exists here.
    return await page.evaluate(
        """() => [...document.querySelectorAll('button')]
            .some(b => (b.innerText || '').trim().toLowerCase() === 'add new tag')""")


async def _tag_exists(page, name: str) -> bool:
    """Search for one tag by name rather than paginating 34 pages of the folder."""
    ok = await page.evaluate(
        """(nm) => {
        const el = document.querySelector('input[placeholder*="Search for tags" i]');
        if (!el) return false;
        const set = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        el.focus(); set.call(el, nm);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    }""", name)
    if not ok:
        return False
    await page.wait_for_timeout(2500)
    # Exact match on the row's first line. A substring test would report "FTM" present
    # because some unrelated tag contains it.
    return await page.evaluate(
        """(nm) => {
        for (const tr of document.querySelectorAll('tr,[class*="TableRow"]')) {
            const r = tr.getBoundingClientRect();
            if (r.width === 0) continue;
            const t = (tr.innerText || '').split('\\n')[0].trim();
            if (t === nm) return true;
        }
        return false;
    }""", name)


async def _clear_search(page) -> None:
    await page.evaluate(
        """() => {
        const el = document.querySelector('input[placeholder*="Search for tags" i]');
        if (!el) return;
        const set = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        el.focus(); set.call(el, '');
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
    }""")
    await page.wait_for_timeout(1500)


async def discover(page) -> dict:
    """Read the tag-creation control off the live page. Writes nothing."""
    await page.goto(TAGS_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await dismiss_popups(page)

    # Top-level chrome first, then again with a folder open, because the create control
    # only appears in the second state.
    before = await page.evaluate(
        """() => [...document.querySelectorAll('button,a,[role="button"]')]
            .map(b => (b.innerText || b.getAttribute('aria-label') || '').trim())
            .filter(t => t && t.length < 40)""")
    folder = await _open_first_folder(page)
    await page.wait_for_timeout(2500)

    found = await page.evaluate(
        """() => {
        const out = {buttons: [], inputs: []};
        for (const b of document.querySelectorAll('button,a,[role="button"]')) {
            const r = b.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            const t = (b.innerText || b.getAttribute('aria-label') || '').trim();
            if (!t || t.length > 40) continue;
            out.buttons.push({text: t, x: Math.round(r.x), y: Math.round(r.y),
                              w: Math.round(r.width)});
        }
        for (const i of document.querySelectorAll('input')) {
            const r = i.getBoundingClientRect();
            if (r.width === 0) continue;
            out.inputs.push({placeholder: i.placeholder || '', name: i.name || '',
                             x: Math.round(r.x), y: Math.round(r.y)});
        }
        return out;
    }""")
    # Anything that plausibly opens a create flow, so the report names real candidates
    # rather than asserting one.
    kw = ("add", "create", "new", "+")
    # x > 300 drops the left nav, whose "Dashboard New" / "Events New" items match the
    # keywords and are not controls on this page at all.
    found["create_candidates"] = [
        b for b in found["buttons"]
        if b["x"] > 300 and (any(k in b["text"].lower() for k in kw) or b["text"] == "+")
    ]
    # What appeared only after opening a folder is, by construction, the folder's own UI.
    found["opened_folder"] = folder
    found["new_after_open"] = [b for b in found["buttons"]
                               if b["text"] not in before and b["x"] > 300]
    found["url"] = page.url
    return found


async def create_tag(page, name: str) -> dict:
    """Create one property tag from inside an open folder.

    The flow, read off the live page rather than guessed: "Add New Tag" (header, only
    present inside a folder) opens an inline row with `input[name="title"]` placeholder
    "New tag name" and a **Create Tag button that starts DISABLED**. That disabled state is
    the useful part -- it is React telling us whether it actually received the value, so a
    button still disabled after typing means the injection failed and nothing should be
    clicked. Assigning `.value` alone leaves the component's state empty; it needs the
    native setter plus input and change events.
    """
    opened = await page.evaluate(
        """() => {
        for (const b of document.querySelectorAll('button,a,[role="button"]')) {
            const r = b.getBoundingClientRect();
            if (r.width === 0) continue;
            if ((b.innerText || '').trim().toLowerCase() === 'add new tag') {
                return {x: r.x + r.width / 2, y: r.y + r.height / 2};
            }
        }
        return null;
    }""")
    if not opened:
        return {"tag": name, "status": "no_create_control",
                "why": "no 'Add New Tag' button -- not inside a folder"}
    await page.mouse.click(opened["x"], opened["y"])
    await page.wait_for_timeout(1800)

    typed = await page.evaluate(
        """(nm) => {
        const el = document.querySelector('input[name="title"]')
               || [...document.querySelectorAll('input')]
                    .find(i => /new tag name/i.test(i.placeholder || ''));
        if (!el) return false;
        const set = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        el.focus(); set.call(el, nm);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    }""", name)
    if not typed:
        return {"tag": name, "status": "no_input",
                "why": "clicked Add New Tag but no 'New tag name' input appeared"}
    await page.wait_for_timeout(1200)

    btn = await page.evaluate(
        """() => {
        for (const b of document.querySelectorAll('button')) {
            const r = b.getBoundingClientRect();
            if (r.width === 0) continue;
            if ((b.innerText || '').trim().toLowerCase() === 'create tag') {
                return {x: r.x + r.width / 2, y: r.y + r.height / 2, disabled: b.disabled};
            }
        }
        return null;
    }""")
    if not btn:
        return {"tag": name, "status": "no_create_button",
                "why": "typed the name but found no Create Tag button"}
    if btn["disabled"]:
        # Do not click a disabled button and call it done. This is the exact shape of the
        # silent-success failure this codebase keeps rediscovering.
        return {"tag": name, "status": "button_still_disabled",
                "why": "Create Tag stayed disabled after typing, so React never received "
                       "the value; the tag was NOT created"}

    await page.mouse.click(btn["x"], btn["y"])
    await page.wait_for_timeout(2500)
    return {"tag": name, "status": "clicked_create"}


async def run(commit: bool, headless: bool, do_discover: bool) -> int:
    email, password = get_credentials()
    out = {"ran_at": datetime.now().isoformat(timespec="seconds"), "commit": commit}

    async with create_browser(headless=headless) as (_b, _c, page):
        if not await login(page, email, password) or "/login" in page.url:
            print("LOGIN FAILED")
            return 2

        if do_discover:
            d = await discover(page)
            out["discover"] = d
            print(f"\n=== /tags/property CONTROLS (read-only) ===\n  url {d['url']}")
            print(f"\n  {len(d['buttons'])} visible buttons; create candidates:")
            for b in d["create_candidates"]:
                print(f"    {b['text']!r:28s} at x={b['x']:>4} y={b['y']:>4} w={b['w']}")
            if not d["create_candidates"]:
                print("    NONE -- this page may have no standalone create control, in "
                      "which case\n    the tags must be created by the pull's Add-Records "
                      "modal instead.")
            print(f"\n  opened folder: {d.get('opened_folder')!r}")
            print("  controls that appeared only after opening it:")
            for b in d.get("new_after_open", [])[:15]:
                print(f"    {b['text']!r:28s} at x={b['x']:>4} y={b['y']:>4}")
            if not d.get("new_after_open"):
                print("    (none)")
            print(f"\n  inputs: {[i['placeholder'] for i in d['inputs']][:8]}")
            OUT.parent.mkdir(parents=True, exist_ok=True)
            OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
            print(f"\nWrote {OUT}")
            return 0

        await page.goto(TAGS_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3500)
        await dismiss_popups(page)

        if not await _enter_folder(page):
            print(f"could not open the {FOLDER!r} tag folder. The create control only "
                  "exists inside a folder -- run --discover.")
            return 3

        base = _baseline_tags()
        out["baseline_tag_count"] = len(base)

        rows = []
        for name, gates in ENTRY_TAGS:
            exists = await _tag_exists(page, name) or (name in base)
            rows.append({"tag": name, "gates": gates,
                         "status": "already_exists" if exists else "missing"})
            await _clear_search(page)
        out["results"] = rows

        print("\n=== ENTRY TAGS ===")
        print(f"  folder {FOLDER!r} open; Phase 0 baseline holds {len(base)} tags\n")
        for r in rows:
            print(f"  {r['tag']:14s} {r['status']:16s} {r['gates']}")

        todo = [r for r in rows if r["status"] == "missing"]
        if not todo:
            print("\n  All present. Nothing to do.")
        elif not commit:
            print(f"\n  Would create {len(todo)}: "
                  f"{', '.join(r['tag'] for r in todo)}")
            print("  Dry run -- nothing written. Re-run with --commit.")
        else:
            print(f"\n  Creating {len(todo)}...")
            for r in todo:
                res = await create_tag(page, r["tag"])
                r.update(res)
                extra = ("  " + res["why"]) if res.get("why") else ""
                print(f"    {r['tag']:14s} {res['status']}{extra}")
                await _clear_search(page)

            # Read back from a FRESH page load. A styled admin page will happily accept a
            # click that saved nothing, so the click's own result is not the evidence.
            await page.goto(TAGS_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)
            await dismiss_popups(page)
            if not await _enter_folder(page):
                print("  could not re-open the folder to verify")
                return 1
            print("\n  Read-back:")
            ok = 0
            for r in rows:
                present = await _tag_exists(page, r["tag"])
                r["verified"] = present
                if present:
                    ok += 1
                print(f"    {r['tag']:14s} {'PRESENT' if present else 'MISSING'}")
                await _clear_search(page)
            out["verified_count"] = ok
            print(f"\n  {ok} of {len(rows)} verified present.")
            if ok < len(rows):
                OUT.parent.mkdir(parents=True, exist_ok=True)
                OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
                print(f"\nWrote {OUT}")
                return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote {OUT}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true", help="actually create missing tags")
    ap.add_argument("--discover", action="store_true",
                    help="read-only: dump the page's controls and exit")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    return asyncio.run(run(a.commit, a.headless, a.discover))


if __name__ == "__main__":
    raise SystemExit(main())
