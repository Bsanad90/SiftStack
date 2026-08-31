"""Find the URL parameters behind the rail's Property Types and AI Scores controls.

These two are the last unmapped filters, and both cost something real. Property Types is
the single-family BUY BOX, so without it every count in the tier configs includes condos
and multi-family and the true house count is unknown. AI Scores is the playbook's most
efficient single lever (90+ runs ~22.8 doors/deal against a ~218 baseline).

Neither appears in the More panel's 260 labels or its 36 named controls: they live only on
the top rail, and their popovers are styled components with no `name` and no native input.

Two things this script does that earlier attempts got wrong:

1. **Locating the popover by DOM DIFF, not by geometry.** A "largest floating container"
   heuristic and a body-child snapshot both grabbed the left nav and the results list
   instead. The map's result cards render "Single Family Residential" themselves, so any
   text search finds the phrase whether or not a filter exists. Here every pre-existing
   match is MARKED before the popover opens, so whatever appears afterwards is provably
   new -- the same containment discipline that caught the fake Lis Pendens hit.

2. **Probing the NAME, not the value.** A parameter the server does not recognise returns
   the unfiltered count no matter what value it carries, which is exactly what
   property_types=["SFR"], ["single_family"], ["Single Family Residential"] and
   ["SINGLE_FAMILY"] all did. So the value was never the blocker. A recognised name given
   a bad value returns 0 instead, and that difference is what identifies a hit.
   The strongest naming lead is DataSift's own record field for this: `structure_type`.

    python src/scripts/dpd_rail_controls.py

Writes output/dpd_rail_controls.json. Read-only against the account.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from datasift_core import DATASIFT_SIFTMAP_URL, create_browser, get_credentials, login  # noqa: E402
from dpd_siftmap_discover import _clean_map, _result_count, location_param  # noqa: E402

OUT = ROOT / "output" / "dpd_rail_controls.json"
COUNTY = "Baltimore"

# Text that would appear inside each popover, used to find it after it opens.
ANCHORS = {
    "Property Types": ["Single Family", "Condominium", "Duplex", "Townhouse",
                       "Multi Family", "Mobile", "Vacant Land"],
    "AI Scores": ["Investor", "Realtor", "Score", "Seller"],
}


async def _mark_existing(page, words: list[str]) -> int:
    """Tag every element already showing these words, so new ones can be told apart."""
    return await page.evaluate(
        """(words) => {
        let n = 0;
        document.querySelectorAll('*').forEach(el => {
            if (el.children.length > 0) return;
            const t = (el.textContent || '');
            if (words.some(w => t.includes(w))) { el.setAttribute('data-dpd-seen', '1'); n++; }
        });
        return n;
    }""",
        words,
    )


async def _open_rail(page, label: str) -> bool:
    box = await page.evaluate(
        """(label) => {
        const hit = [...document.querySelectorAll('*')].find(el => {
            if (el.children.length !== 0) return false;
            if ((el.textContent || '').trim() !== label) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.y < 200;   // the TOP rail
        });
        if (!hit) return null;
        const r = hit.getBoundingClientRect();
        return {x: r.x, y: r.y, w: r.width, h: r.height};
    }""",
        label,
    )
    if not box:
        return False
    # A real mouse event. A JS .click() on the rail opens only a stub, as the More button
    # already demonstrated (41 labels instead of 234).
    await page.mouse.click(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
    await page.wait_for_timeout(2500)
    return True


async def _capture_popover(page, words: list[str]) -> dict:
    """Return the container holding the newly-appeared matches, and its markup."""
    return await page.evaluate(
        """(words) => {
        const fresh = [...document.querySelectorAll('*')].filter(el =>
            el.children.length === 0 &&
            !el.hasAttribute('data-dpd-seen') &&
            words.some(w => (el.textContent || '').includes(w)));
        if (!fresh.length) return {found: false, fresh: 0};

        // Climb from the first new match until the container holds several of them --
        // that container is the popover rather than a single row.
        let el = fresh[0], best = null;
        for (let i = 0; i < 14 && el.parentElement; i++) {
            el = el.parentElement;
            const inside = fresh.filter(f => el.contains(f)).length;
            if (inside >= Math.min(3, fresh.length)) { best = el; break; }
        }
        if (!best) best = fresh[0].parentElement;
        return {
            found: true,
            fresh: fresh.length,
            labels: [...new Set(fresh.map(f => (f.textContent || '').trim()))],
            html: best.outerHTML.slice(0, 24000),
        };
    }""",
        words,
    )


async def discover(page, base: str) -> dict:
    found = {}
    for label, words in ANCHORS.items():
        await page.goto(base, wait_until="domcontentloaded")
        await page.wait_for_timeout(9000)
        await _clean_map(page)
        marked = await _mark_existing(page, words)
        opened = await _open_rail(page, label)
        cap = await _capture_popover(page, words) if opened else {"found": False}
        html = cap.get("html") or ""
        cap["values"] = sorted(set(re.findall(r'value="([^"]{1,60})"', html)))
        cap["names"] = sorted(set(re.findall(r'name="([^"]{1,60})"', html)))
        cap.pop("html", None)
        cap["pre_marked"] = marked
        cap["opened"] = opened
        found[label] = cap
        print(f"\n  {label}: opened={opened} new_labels={cap.get('fresh')}")
        for t in (cap.get("labels") or [])[:24]:
            print(f"    label  {t}")
        if cap["values"]:
            print(f"    values: {cap['values']}")
        if cap["names"]:
            print(f"    names : {cap['names']}")
    return found


def _candidates(values: list[str]) -> list[str]:
    """Candidate query fragments for the property-type filter.

    The value list comes from the popover when it renders, and falls back to the phrasings
    DataSift uses for this field elsewhere. Names are the real unknown, and `structure_type`
    leads because that is what the DataSift record field and CSV column are called.
    """
    vals = values or ["Single Family Residential", "Single Family Res.", "SFR"]
    names = ["structure_type", "structure_types", "extra_structure_type",
             "property_type", "property_types", "extra_property_type",
             "land_use", "extra_land_use", "propertyDetails.extra_structure_type"]
    out = []
    for n in names:
        for v in vals[:3]:
            out.append(f"{n}={quote(json.dumps([v]))}")
        out.append(f"{n}={quote(vals[0])}")           # bare value, to expose a 0
    return out


SCORE_CANDIDATES = [
    # A low threshold matters: at min=90 a working filter can legitimately return a small
    # number, which is hard to tell from a rejected value. At min=1 a working filter must
    # return a large one.
    "investor_score_min=1", "investor_score_min=50",
    "extra_investor_score_min=1", "ai_score_min=1",
    "scores_min=1", "investor_score=" + quote(json.dumps([50, 100])),
    "realtor_score_min=1", "realtor_score_min=50",
    "extra_realtor_score_min=1",
    "seller_score_min=1", "extra_ai_score_min=1",
]


async def probe(page, base: str, cands: list[str], baseline: int, label: str) -> dict:
    print(f"\n  Probing {label} ({len(cands)} candidates, baseline {baseline})")
    res = {}
    for cand in cands:
        try:
            await page.goto(f"{base}&{cand}", wait_until="domcontentloaded")
            await page.wait_for_timeout(7000)
            await _clean_map(page)
            c = await _result_count(page)
        except Exception as e:  # noqa: BLE001
            res[cand] = {"count": None, "verdict": f"failed: {e}"}
            continue
        if c is None:
            v = "no count"
        elif c == baseline:
            v = "IGNORED - the name is wrong; the value is irrelevant here"
        elif c == 0:
            v = "0 - the NAME is recognised, the value is not"
        else:
            v = "FILTERS"
        res[cand] = {"count": c, "verdict": v}
        flag = "  <<<" if v == "FILTERS" else ("  <-- name found" if c == 0 else "")
        print(f"    {cand[:66]:68s} {str(c):>9s}  {v.split(' -')[0]}{flag}")
    return res


async def run(headless: bool) -> dict:
    email, password = get_credentials()
    base = f"{DATASIFT_SIFTMAP_URL}?location={quote(location_param(COUNTY))}"
    out = {"at": datetime.now().isoformat(timespec="seconds"), "county": COUNTY}

    async with create_browser(headless=headless) as (_b, _c, page):
        if not await login(page, email, password):
            out["error"] = "login failed"
            return out

        await page.goto(base, wait_until="domcontentloaded")
        await page.wait_for_timeout(9000)
        await _clean_map(page)
        baseline = await _result_count(page)
        out["baseline"] = baseline
        print(f"  {COUNTY} unfiltered: {baseline}")
        if not baseline:
            out["error"] = "no baseline; cannot judge any probe"
            return out

        out["popovers"] = await discover(page, base)
        pt_values = (out["popovers"].get("Property Types") or {}).get("values") or []
        pt_labels = (out["popovers"].get("Property Types") or {}).get("labels") or []
        # Prefer real option values; fall back to the rendered labels.
        vals = pt_values or [t for t in pt_labels if "Single Family" in t]
        out["property_type_probe"] = await probe(page, base, _candidates(vals),
                                                 baseline, "Property Types")
        out["ai_score_probe"] = await probe(page, base, SCORE_CANDIDATES,
                                            baseline, "AI Scores")
    return out


def report(out: dict) -> int:
    print("\n=== RAIL CONTROL DISCOVERY ===")
    if out.get("error"):
        print("  ERROR:", out["error"])
        return 1
    for key, label in (("property_type_probe", "Property Types"),
                       ("ai_score_probe", "AI Scores")):
        res = out.get(key) or {}
        works = [c for c, e in res.items() if e["verdict"] == "FILTERS"]
        near = [c for c, e in res.items() if e["count"] == 0]
        print(f"\n  {label}:")
        if works:
            for c in works:
                print(f"    WORKS  {c}  -> {res[c]['count']}")
        elif near:
            print("    No candidate filtered, but these names ARE recognised (they return 0,")
            print("    which an unknown name never does) -- the value format is the last step:")
            for c in near:
                print(f"      {c}")
        else:
            print("    Nothing recognised. Every candidate returned the unfiltered count,")
            print("    so the parameter name is still unknown and the buy box cannot be")
            print("    applied in the pull URL.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Find the rail controls' URL parameters")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    out = asyncio.run(run(headless=not a.headed))
    rc = report(out)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote {OUT}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
