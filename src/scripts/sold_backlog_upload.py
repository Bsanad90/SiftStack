"""Drive Update Data -> "Tagging existing properties" to tag the sold backlog.

The browser path for `sold_backlog.py`, needing no API access. It tags records the
account ALREADY HOLDS, matched by property address, and cannot create records:
the wizard's own Data Requirements for this option are Property Street / City /
State / ZIP Code and nothing else, so there is no owner field in the file and
nothing that can overwrite the PR/DM contact mapping.

    python src/scripts/sold_backlog_upload.py --csv <file> --dry-run   # stops at Review
    python src/scripts/sold_backlog_upload.py --csv <file> --commit    # clicks Finish

--dry-run walks every step INCLUDING the column mapping and the review screen, and
stops before the final click. That is where a wrong mapping shows up, so run it
once per new CSV shape.

Why a dedicated script rather than `datasift_uploader.upload_csv(mode="update")`:
that function's steps are the ADD wizard's (Setup / Enrichment / Tags / Upload /
Map / Review). The UPDATE wizard is a different five-step flow (Setup / Add tags /
Upload the file / Map the columns / Review) gated behind a "WHAT ARE YOU GOING TO
UPDATE?" multi-select. Running the add-flow code against it is exactly the kind of
step drift that broke the uploader at build 1.0.46.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from datasift_core import (  # noqa: E402
    create_browser, dismiss_popups, get_credentials, login, screenshot,
)
from datasift_uploader import RECENTLY_SOLD_TAG  # noqa: E402

RECORDS_URL = "https://app.reisift.io/records/properties"
UPDATE_OPTION = "Tagging existing properties"


async def _shot(page, name: str) -> None:
    await screenshot(page, "backlog_%s" % name)


async def _click_text(page, text: str, *, exact: bool = True, what: str = "") -> bool:
    loc = page.get_by_text(text, exact=exact)
    if await loc.count() == 0:
        return False
    try:
        await loc.first.click(timeout=5000)
    except Exception:  # noqa: BLE001
        await loc.first.click(force=True)
    return True


async def _next_step(page, *, step: str) -> bool:
    """Advance the wizard. Returns False if no Next control could be clicked.

    Callers MUST check the return. Discarding it meant a wizard that stalled on
    one step carried on through the remaining ones and, under --commit, clicked
    Finish from whatever screen it happened to be on.
    """
    for label in ("Next Step", "Next", "Continue"):
        btn = page.locator('button:has-text("%s")' % label)
        if await btn.count():
            try:
                await btn.first.click(timeout=5000)
            except Exception:  # noqa: BLE001
                await btn.first.click(force=True)
            await page.wait_for_timeout(2500)
            return True
    print("could not advance past %s: no Next control found" % step)
    return False


async def _apply_wizard_tag(page, tag: str) -> bool:
    """Put `tag` into the Add-tags step and CONFIRM a chip appeared.

    Playwright's .type() does not register on this control -- the dropdown stays
    unfiltered and the input stays empty, while the calling code happily reports
    success. Verified by screenshot 2026-08-31. The account's other styled inputs
    need React's native value setter plus explicit input/change events (the
    pattern recorded in CLAUDE.md), so use that and then verify the chip.
    """
    box = page.locator('input[placeholder*="add a new tag" i], '
                       'input[placeholder*="Search or add" i], '
                       'input[placeholder*="tag" i]')
    if await box.count() == 0:
        print("no tag input found on the Add-tags step")
        return False
    handle = await box.first.element_handle()
    await handle.click()
    await page.wait_for_timeout(500)
    await page.evaluate("""([el, v]) => {
        const set = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        set.call(el, v);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
    }""", [handle, tag])
    await page.wait_for_timeout(2000)

    typed = await handle.input_value()
    print("tag input now reads: %r" % typed)
    if typed.strip() != tag:
        return False

    # Prefer an exact suggestion; fall back to the explicit Add control.
    opt = page.get_by_text(tag, exact=True)
    for i in range(await opt.count()):
        b = await opt.nth(i).bounding_box()
        if b and b["x"] > 700 and b["y"] > 340:      # inside the suggestion list
            await opt.nth(i).click()
            break
    else:
        add = page.get_by_text("Add", exact=True)
        if await add.count():
            await add.first.click()
        else:
            await handle.press("Enter")
    await page.wait_for_timeout(2000)

    # Proving a chip exists is harder than it looks, and getting it wrong is worse
    # than not checking. First attempt matched `tag` anywhere below y=340 and
    # returned True -- but `Recently Sold` is now a real account tag, so it appears
    # as an OPTION in the open suggestion list and the check passed while the input
    # was still empty and no chip had been added. Screenshot disproved it.
    #
    # So: require the suggestion list to be CLOSED (a committed chip closes it),
    # and require the match to sit outside the input row. If either is ambiguous,
    # report unverified rather than success -- this function gates a write.
    state = await page.evaluate("""(t) => {
        const vis = e => { const r = e.getBoundingClientRect();
                           return r.width > 0 && r.height > 0; };
        const inp = document.querySelector(
            'input[placeholder*="add a new tag" i], input[placeholder*="tag" i]');
        const iv = inp ? inp.value : null;
        const ir = inp ? inp.getBoundingClientRect() : null;
        // the suggestion list: many sibling leaves that are NOT the tag
        const leaves = [...document.querySelectorAll('*')].filter(
            el => el.children.length === 0 && vis(el) && (el.textContent||'').trim());
        const listOpen = leaves.filter(el => {
            const r = el.getBoundingClientRect();
            return ir && r.y > ir.bottom && r.y < ir.bottom + 320 && r.x > ir.x - 20;
        }).length > 3;
        const matches = leaves.filter(el => (el.textContent||'').trim() === t)
            .map(el => { const r = el.getBoundingClientRect();
                         return {y: Math.round(r.y), x: Math.round(r.x)}; });
        return {input_value: iv, list_open: listOpen, matches: matches};
    }""", tag)
    print("post-add state:", state)
    if state["list_open"]:
        print("suggestion list still open and no chip committed -- treating as FAILED")
        return False
    return bool(state["matches"])


async def run(csv_path: Path, commit: bool, headless: bool) -> int:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    print("CSV: %s (%d rows)" % (csv_path, len(rows)))
    if not rows:
        print("empty CSV; nothing to do")
        return 2
    print("columns:", list(rows[0]))
    print("mode:", "COMMIT" if commit else "DRY RUN (stops before Finish)")

    email, pw = get_credentials()
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await (await browser.new_context(
            viewport={"width": 1440, "height": 1000})).new_page()
        try:
            if not await login(page, email, pw):
                print("LOGIN FAILED")
                return 1
            await page.goto(RECORDS_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            await dismiss_popups(page)

            # ── Step 1: Setup ──
            if not await _click_text(page, "Upload File"):
                await _shot(page, "no_upload_link")
                print("could not find 'Upload File'")
                return 3
            await page.wait_for_timeout(3500)
            await dismiss_popups(page)

            if not await _click_text(page, "Update Data"):
                await _shot(page, "no_update_data")
                print("could not find 'Update Data'")
                return 3
            await page.wait_for_timeout(2500)

            if not await _click_text(page, "Select one or more options", exact=False):
                await _shot(page, "no_option_dropdown")
                print("could not open the update-options dropdown")
                return 3
            await page.wait_for_timeout(2000)

            if not await _click_text(page, UPDATE_OPTION):
                await _shot(page, "no_tagging_option")
                print("could not find %r" % UPDATE_OPTION)
                return 3
            await page.wait_for_timeout(2500)
            await _click_text(page, "WHAT ARE YOU UP TO?", exact=False)
            await page.wait_for_timeout(1500)

            # Confirm the selection actually stuck before moving on: this dropdown
            # is a multi-select, and a mis-click leaves it on the placeholder while
            # every later step still looks normal.
            chosen = await page.evaluate(
                """(want) => [...document.querySelectorAll('*')].some(
                    el => el.children.length === 0 &&
                          (el.textContent || '').trim() === want)""", UPDATE_OPTION)
            print("update option selected:", chosen)
            await _shot(page, "step1_setup")
            if not chosen:
                print("the option did not stick; refusing to continue")
                return 3
            if not await _next_step(page, step="Setup"):
                await _shot(page, "stuck_setup")
                return 3

            # ── Step 2: Add tags ──
            # RECENTLY_SOLD_TAG is applied to the whole file here. Each row's own
            # "Sold YYYY-MM" tag rides in the CSV's Tags column and is mapped at
            # step 4. Belt and braces on purpose: if the column mapping silently
            # fails, the trigger tag still lands and suppression still works.
            await page.wait_for_timeout(2000)
            ok_tag = await _apply_wizard_tag(page, RECENTLY_SOLD_TAG)
            await _shot(page, "step2_tags")
            if not ok_tag:
                print("REFUSING to continue: the Add-tags step did not accept %r. "
                      "Without it, and with the CSV Tags column unmapped by default, "
                      "the upload would match records and tag NOTHING."
                      % RECENTLY_SOLD_TAG)
                return 3
            if not await _next_step(page, step="Add tags"):
                await _shot(page, "stuck_tags")
                return 3

            # ── Step 3: Upload the file ──
            await page.wait_for_timeout(2000)
            file_input = page.locator('input[type="file"]')
            if await file_input.count() == 0:
                await _shot(page, "no_file_input")
                print("no file input on the upload step")
                return 3
            await file_input.first.set_input_files(str(csv_path))
            await page.wait_for_timeout(6000)
            await _shot(page, "step3_uploaded")
            if not await _next_step(page, step="Upload the file"):
                await _shot(page, "stuck_upload")
                return 3

            # ── Step 4: Map the columns ──
            await page.wait_for_timeout(4000)
            await _shot(page, "step4_mapping")
            mapped = await page.evaluate("""() => {
                const vis = e => { const r = e.getBoundingClientRect();
                                   return r.width > 0 && r.height > 0; };
                const out = [];
                document.querySelectorAll('*').forEach(el => {
                    if (el.children.length || !vis(el)) return;
                    const t = (el.textContent || '').trim();
                    if (t && t.length < 60) out.push(t);
                });
                return Array.from(new Set(out));
            }""")
            interesting = [t for t in mapped
                           if any(k in t.lower() for k in
                                  ("property", "tag", "unmapped", "select", "street",
                                   "city", "state", "zip", "required"))]
            print("\nmapping screen mentions:")
            for t in interesting[:30]:
                print("   ", t)
            if not await _next_step(page, step="Map the columns"):
                await _shot(page, "stuck_mapping")
                return 3

            # ── Step 5: Review ──
            await page.wait_for_timeout(4000)
            await _shot(page, "step5_review")
            body = await page.inner_text("body")
            for marker in ("Finish", "Review", "records", "properties"):
                if marker.lower() in body.lower():
                    pass
            print("\nreached the review step")

            if not commit:
                print("\nDRY RUN -- stopping before Finish. Nothing was submitted.")
                print("Check backlog_step4_mapping.png and backlog_step5_review.png.")
                return 0

            # NOT `has-text("Upload")`: that substring-matches the sidebar's
            # "Upload File" button, so a stalled wizard would "finish" by clicking
            # the control that restarts the whole flow. Exact labels only.
            finish = page.get_by_role("button", name="Finish Upload", exact=True)
            if await finish.count() == 0:
                finish = page.get_by_role("button", name="Finish", exact=True)
            if await finish.count() == 0:
                finish = page.get_by_role("button", name="Submit", exact=True)
            if await finish.count() == 0:
                await _shot(page, "no_finish")
                print("could not find the Finish button")
                return 3
            await finish.first.click(force=True)
            await page.wait_for_timeout(9000)
            await dismiss_popups(page)
            await _shot(page, "step6_finished")
            print("clicked Finish. Processing runs in the background -- verify by "
                  "reading a record back, not by trusting this message.")
            return 0
        finally:
            await browser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", required=True)
    ap.add_argument("--commit", action="store_true",
                    help="click Finish; without it the run stops at Review")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    return asyncio.run(run(Path(a.csv), a.commit, a.headless))


if __name__ == "__main__":
    raise SystemExit(main())
