"""Map each `assigned_to` user uuid to a person's name. Read-only, one login.

No users/team endpoint exists on this account (every guessed route 404'd -- see
doors_per_deal_resolve_assignees.py), so the rendered UI is the only place a
name lives. This resolves the map in the SAFE direction: the API tells us a
record's `assigned_to` uuid, and the property page DISPLAYS that record's
assignee name. So we read uuid -> name.

It deliberately does NOT cycle a record's assignee to discover names. That
would be a WRITE against a live record, would need a second write to restore
the original, and this account has sequences that fire on record changes.

TWO THINGS THIS GOT WRONG FIRST, both worth keeping written down:

  1. `doors_per_deal_resolve_assignees.read_current_assignee` searches Records
     and clicks the owner name, which lands on the **Owner Details** page --
     and that page has no assignee control at all. It reported 5 of 7 records
     as "unassigned" when the API said all 7 were assigned. The fix is the
     direct route `/records/properties/{uuid}/details`, which needs no clicks.
  2. Matching against a hardcoded name roster turns an unrecognised person into
     an indistinguishable "unassigned". This reads the control's own text
     instead, so a user missing from the roster is REPORTED as a new name
     rather than silently lost.

The read itself: on the details page the assignee is the topmost text in the
column x 650..980 at y 95..145 (verified live: 'Mariam Mohamed' at y=113,
x=708, immediately right of the property street). The Select's OPTION rows
render in that same column just below (y=183+), so anything carrying a
`SelectOption` / `Placeholder` class is excluded -- otherwise the dropdown's
first option gets read as the assigned person.

Input: output/dpd_assignee_candidates.json -- {user_uuid: [property_uuid,
address, owner_name]}, each already re-verified live against the API so a
since-reassigned record cannot mislabel a person.

Usage:
    python src/scripts/dpd_assignee_name_map.py [--headed]
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

from dotenv import dotenv_values  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

from datasift_core import login, dismiss_popups  # noqa: E402
from doors_per_deal_resolve_assignees import KNOWN_USERS  # noqa: E402

CANDIDATES = ROOT / "output" / "dpd_assignee_candidates.json"
OUT = ROOT / "output" / "dpd_assignee_name_map.json"
DETAILS_URL = "https://app.reisift.io/records/properties/{uuid}/details"

READ_JS = """() => {
    const out = [];
    document.querySelectorAll('*').forEach(el => {
        if (el.children.length) return;
        const t = (el.textContent || '').trim();
        if (!t || t.length > 40) return;
        const cls = (el.className || '').toString();
        // The Select's own option rows sit in this column too -- reading one
        // of those would report a dropdown entry as the assigned person.
        if (/SelectOption|Placeholder/.test(cls)) return;
        const r = el.getBoundingClientRect();
        if (r.width === 0) return;
        if (r.x < 650 || r.x > 980 || r.y < 95 || r.y > 145) return;
        out.push({t, y: Math.round(r.y), x: Math.round(r.x)});
    });
    out.sort((a, b) => a.y - b.y);
    return out;
}"""

# Rendered in the same band but never a person.
NOT_A_NAME = {"no answer", "default", "cold lead", "warm lead", "hot lead",
              "not vacant mailing", "not vacant property", "collapse"}


async def run(headless: bool) -> int:
    cands = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    env = dotenv_values(str(ROOT / ".env"))
    name_of: dict[str, str] = {}
    problems: list[str] = []
    new_names: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await ctx.new_page()
        try:
            if not await login(page, env.get("DATASIFT_EMAIL"), env.get("DATASIFT_PASSWORD")):
                print("login failed")
                return 2
            for user_uuid, (prop_uuid, address, owner) in cands.items():
                print(f"\n{user_uuid[:8]}  via {address!r} ({owner})", flush=True)
                await page.goto(DETAILS_URL.format(uuid=prop_uuid),
                                wait_until="domcontentloaded")
                await page.wait_for_timeout(7000)
                await dismiss_popups(page)
                await page.wait_for_timeout(1200)

                nodes = await page.evaluate(READ_JS)
                cand = [n["t"] for n in nodes if n["t"].lower() not in NOT_A_NAME]
                if not cand:
                    await page.screenshot(path=f"dpd_namemap_fail_{user_uuid[:8]}.png")
                    problems.append(f"{user_uuid}: no assignee text found on "
                                    f"{page.url} (screenshot written)")
                    print("  FAILED: nothing readable in the assignee band", flush=True)
                    continue
                name = cand[0]
                print(f"  reads as: {name}", flush=True)
                name_of[user_uuid] = name
                if name not in KNOWN_USERS:
                    new_names.append(name)
        finally:
            await browser.close()

    # Two uuids resolving to one name means a candidate showed the wrong panel.
    seen = list(name_of.values())
    dupes = sorted({n for n in seen if seen.count(n) > 1})
    if dupes:
        problems.append(f"two uuids resolved to the same name {dupes} -- one "
                        f"candidate is probably showing the wrong record")
    if new_names:
        problems.append(f"name(s) not in doors_per_deal_resolve_assignees."
                        f"KNOWN_USERS: {sorted(set(new_names))} -- real people, "
                        f"roster is stale")

    OUT.write_text(json.dumps({"name_of": name_of, "problems": problems},
                              indent=2), encoding="utf-8")
    print("\n" + "=" * 62)
    for u, n in name_of.items():
        print(f"  {u}  {n}")
    print(f"\nmapped {len(name_of)}/{len(cands)}")
    for pr in problems:
        print("  PROBLEM:", pr)
    print(f"Wrote {OUT}")
    return 0 if len(name_of) == len(cands) and not dupes else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    return asyncio.run(run(headless=not ap.parse_args().headed))


if __name__ == "__main__":
    sys.exit(main())
