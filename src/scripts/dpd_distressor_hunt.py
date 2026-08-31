"""Phase 2d: the LAST place the six missing doors-per-deal signals could hide.

Phase 2/2b mapped SiftMap's whole filter URL vocabulary and closed Out-of-State, but six
signals were still absent from every named preset, every URL probe, and the 236 labels the
More panel renders by default:

    Bad Credit, HOA Lien, Lis Pendens, Bankruptcy, Estate Sale, Probate  (+ Low Income)

Two of those decide real money. Bad Credit is Calvert's 218x and Prince William's 196.6x
Priority-1 signal; without it those counties ship at ~3x instead. So "not found" has to be
a MEASUREMENT, not an assumption.

The one place left is the PRO "Homeowner and Property Distressors" section, whose method
control offers `Stacked (contains two or more distressors)` and `Custom Combination`. The
default is Stacked, and a stacked view needs no distressor list, which is exactly why the
236-label dump shows none: the checkbox list is only RENDERED once Custom Combination is
chosen. Setting `distressors_method=custom` by URL was already tried and did nothing --
it returned the unfiltered 215,290 -- so this must be driven through the UI.

    python src/scripts/dpd_distressor_hunt.py --headed

Writes output/dpd_siftmap_distressor_list.json. Read-only: no records, tags or lists are
written to the account, and no filter is saved.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from datasift_core import DATASIFT_SIFTMAP_URL, create_browser, get_credentials, login  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dpd_siftmap_discover import _clean_map, _result_count, location_param  # noqa: E402

logger = logging.getLogger("dpd_distressor_hunt")
OUT_PATH = ROOT / "output" / "dpd_siftmap_distressor_list.json"

# The signals still unaccounted for after Phase 2b. Matched case-insensitively against
# whatever the Custom Combination list turns out to render.
WANTED = [
    "Bad Credit", "HOA Lien", "Lis Pendens", "Bankruptcy",
    "Estate Sale", "Probate", "Low Income", "Tired Landlord",
]

# Why each one matters, so a gap reads as a business consequence rather than a blank.
STAKES = {
    "Bad Credit": "Calvert 218x P1 and Prince William 196.6x P1 -- the two biggest lifts in the set",
    "HOA Lien": "Montgomery 53.6x P1, Anne Arundel 12x P1",
    "Lis Pendens": "the judicial-state foreclosure signal for all 7 MD jurisdictions",
    "Low Income": "Spotsylvania 5.2x P1",
}


async def _open_more_panel(page) -> bool:
    """Open the top rail's More panel with a real mouse click.

    A JS .click() on the element whose text is 'More' opens a 41-label stub; only a real
    mouse event at the rail coordinates opens the full panel. The rail runs across the TOP
    of the map at y~84 -- an x<=420 bound reads the app's left NAV instead, which is the
    mistake that filled dpd_siftmap_filters.json's `panel_labels` with 'SiftMap, Records,
    Tags, Sequences'.
    """
    box = await page.evaluate(
        """() => {
        const hit = [...document.querySelectorAll('*')].find(el => {
            if (el.children.length !== 0) return false;
            if ((el.textContent || '').trim() !== 'More') return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.y < 200;   // the TOP rail, not the left nav
        });
        if (!hit) return null;
        const r = hit.getBoundingClientRect();
        return {x: r.x, y: r.y, w: r.width, h: r.height};
    }"""
    )
    if not box:
        return False
    await page.mouse.click(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
    await page.wait_for_timeout(2500)
    return True


async def _scroll_panel(page) -> None:
    """Scroll the open panel to the bottom; its lower sections do not exist until then.

    The panel renders 41 labels unscrolled and 234+ scrolled, so this is load-bearing
    rather than cosmetic -- Owner Details and Financial Details are not in the DOM at all
    until the container is scrolled.
    """
    for _ in range(14):
        await page.evaluate(
            """() => {
            const cands = [...document.querySelectorAll('div')].filter(el => {
                const r = el.getBoundingClientRect();
                return r.height > 200 && r.y < 400 && el.scrollHeight > el.clientHeight + 40;
            });
            cands.sort((a, b) => b.scrollHeight - a.scrollHeight);
            if (cands[0]) cands[0].scrollTop = cands[0].scrollHeight;
        }"""
        )
        await page.wait_for_timeout(400)


async def _panel_labels(page) -> list[str]:
    return [r["text"] for r in await _labels_with_rects(page)]


async def _labels_with_rects(page) -> list[dict]:
    """Every visible leaf label WITH its position.

    Position is what separates a filter control from a result card's own distressor badge.
    The map's result list renders 'Distressors' and the individual distressor names for
    whichever property is on screen, so a plain text match against the page finds
    'Lis Pendens' whether or not SiftMap can FILTER on it -- the same trap as the VA pull's
    grid metadata, where a confidently-wrong county field read as authoritative.
    """
    return await page.evaluate(
        """() => {
        const out = [];
        document.querySelectorAll('*').forEach(el => {
            if (el.children.length > 0) return;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) return;
            const t = (el.textContent || '').trim();
            if (t && t.length > 1 && t.length < 60)
                out.push({text: t, x: Math.round(r.x), y: Math.round(r.y)});
        });
        const seen = new Set();
        return out.filter(o => !seen.has(o.text) && seen.add(o.text));
    }"""
    )


async def _named_controls(page) -> list[dict]:
    """Every native control in the DOM, by `name`. The name IS the filter's real identity.

    Each styled dropdown hides a native <select name="...">, so dumping these yields the
    whole vocabulary at once instead of guessing parameter names one at a time.
    """
    return await page.evaluate(
        """() => [...document.querySelectorAll('select[name],input[name]')].map(el => ({
            name: el.name, tag: el.tagName, type: el.type || null,
            value: el.value || null,
            options: el.tagName === 'SELECT' ? [...el.options].map(o => o.value) : null,
        }))"""
    )


async def _choose_custom_combination(page) -> str:
    """Select 'Custom Combination' inside the PRO distressors section.

    Tried two ways because the control's rendering is unknown: a native <select> whose
    options carry the word, then a real mouse click on the label. Whichever lands, the
    proof is the panel growing new labels, not the click reporting success.
    """
    picked = await page.evaluate(
        """() => {
        for (const sel of document.querySelectorAll('select')) {
            const opt = [...sel.options].find(o =>
                /custom/i.test(o.textContent || '') || /custom/i.test(o.value || ''));
            if (!opt) continue;
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLSelectElement.prototype, 'value').set;
            setter.call(sel, opt.value);
            sel.dispatchEvent(new Event('input', {bubbles: true}));
            sel.dispatchEvent(new Event('change', {bubbles: true}));
            return 'select:' + (sel.name || '?') + '=' + opt.value;
        }
        return null;
    }"""
    )
    if picked:
        await page.wait_for_timeout(2500)
        return picked

    box = await page.evaluate(
        """() => {
        const hit = [...document.querySelectorAll('*')].find(el =>
            el.children.length === 0 &&
            /^Custom Combination$/i.test((el.textContent || '').trim()));
        if (!hit) return null;
        hit.scrollIntoView({behavior: 'instant', block: 'center'});
        const r = hit.getBoundingClientRect();
        return {x: r.x, y: r.y, w: r.width, h: r.height};
    }"""
    )
    if box:
        await page.mouse.click(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
        await page.wait_for_timeout(2500)
        return "mouse-click on 'Custom Combination'"
    return ""


async def run(county: str, headless: bool, probe_county: str | None) -> dict:
    email, password = get_credentials()
    out = {
        "hunted_at": datetime.now().isoformat(timespec="seconds"),
        "county": county,
        "wanted": WANTED,
    }
    async with create_browser(headless=headless) as (_b, _c, page):
        if not await login(page, email, password) or "/login" in page.url:
            out["error"] = "login failed"
            return out

        await page.goto(f"{DATASIFT_SIFTMAP_URL}?location={quote(location_param(county))}",
                        wait_until="domcontentloaded")
        await page.wait_for_timeout(9000)
        await _clean_map(page)
        out["baseline_count"] = await _result_count(page)

        if not await _open_more_panel(page):
            out["error"] = "could not find 'More' on the top rail"
            return out
        await _scroll_panel(page)

        before_labels = await _panel_labels(page)
        before_names = [c["name"] for c in await _named_controls(page)]
        out["before"] = {"labels": len(before_labels), "controls": len(before_names)}

        out["selection"] = await _choose_custom_combination(page)
        if not out["selection"]:
            out["error"] = "'Custom Combination' control not found in the open panel"
            out["before_labels"] = before_labels
            return out

        await _scroll_panel(page)
        after_rects = await _labels_with_rects(page)
        after_labels = [r["text"] for r in after_rects]
        after_controls = await _named_controls(page)
        after_names = [c["name"] for c in after_controls]

        out["after"] = {"labels": len(after_labels), "controls": len(after_names)}
        out["after_labels"] = after_labels
        out["new_labels"] = [t for t in after_labels if t not in set(before_labels)]
        out["new_controls"] = [c for c in after_controls if c["name"] not in set(before_names)]
        out["url_after"] = page.url

        # The verdict. A bare text match is not enough: only a hit INSIDE the open filter
        # panel is a filter. A hit out in the results list is a property's own distressor
        # badge and proves nothing about what can be filtered on.
        out["panel_scope"] = await _panel_scope(page)
        out["panel_control"] = await _panel_control(page)
        out["found_detail"] = await _classify_hits(page, WANTED)
        out["found"] = {w: v["in_filter_panel"] for w, v in out["found_detail"].items()}

        # A label alone is not a usable filter. Phase 4 drives SiftMap by URL, so every
        # recovered signal needs its native control's `name` -- that name IS the parameter.
        out["located"] = {}
        for w, v in out["found_detail"].items():
            if v["in_filter_panel"]:
                out["located"][w] = await _locate_signal_control(page, w)

        # Probe the Foreclosure Filters parameter names on a JUDICIAL county, not on DC.
        # DC is non-judicial and carries almost no foreclosure data at all (its whole
        # pre-foreclosure preset is 709 of 215,290), so a working Lis Pendens filter there
        # could return a number small enough to be indistinguishable from a rejected value.
        # Baltimore County is judicial and is where LP is supposed to have volume.
        if probe_county:
            pbase = f"{DATASIFT_SIFTMAP_URL}?location={quote(location_param(probe_county))}"
            await page.goto(pbase, wait_until="domcontentloaded")
            await page.wait_for_timeout(9000)
            await _clean_map(page)
            pb = await _result_count(page)
            out["probe_county"] = probe_county
            out["probe_baseline"] = pb
            logger.info("Unfiltered %s: %s properties", probe_county, pb)
            if pb:
                out["foreclosure_params"] = await probe_foreclosure_params(page, probe_county, pb)
            else:
                out["foreclosure_params"] = {}
                out["probe_error"] = "no baseline count for the probe county; probes skipped"

        # A container that also holds the map attribution or a result card is the whole
        # page, and every "in_filter_panel" answer computed from it is worthless. Say so
        # rather than shipping a confident verdict off a container that was never the panel.
        leaked = [t for t, inside in (out["panel_control"] or {}).items() if inside]
        if leaked:
            out["error"] = ("the located container also holds page furniture "
                            f"({', '.join(leaked)}), so it is not the filter panel; "
                            "the in-panel verdict is void")
        if not out["panel_scope"]:
            # Refuse to report a verdict computed from a container that was never located.
            # A classifier that silently answers "no" for everything is the exact failure
            # this build keeps rediscovering: a run that succeeds while measuring nothing.
            out["error"] = ("could not locate the filter panel container; the in-panel "
                            "verdict below is unverified")
    return out


# Section headings that exist ONLY inside the filter panel. Used to find the panel's DOM
# container, which is what a hit is then tested against -- coordinates are useless here
# because the panel scrolls and off-screen labels carry huge y values.
PANEL_ANCHORS = ["Owner Details", "Data Details", "Lot Details", "Financial Details"]


async def _panel_scope(page) -> str | None:
    """Locate the filter panel's container and tag it, so containment can be tested."""
    return await page.evaluate(
        """(anchors) => {
        for (const name of anchors) {
            const a = [...document.querySelectorAll('*')].find(el =>
                el.children.length === 0 && (el.textContent || '').trim() === name);
            if (!a) continue;
            let el = a;
            for (let i = 0; i < 14 && el.parentElement; i++) {
                el = el.parentElement;
                // The panel holds several sections, so require more than one anchor in it.
                const inside = anchors.filter(n => [...el.querySelectorAll('*')].some(c =>
                    c.children.length === 0 && (c.textContent || '').trim() === n)).length;
                if (inside >= 2) { el.setAttribute('data-dpd-panel', '1'); return name; }
            }
        }
        return null;
    }""",
        PANEL_ANCHORS,
    )


# Labels that live OUTSIDE the filter panel with certainty -- map furniture and the result
# list. If the located container holds any of them it is not the panel.
OUTSIDE_CONTROLS = ["Mapbox", "Search while moving", "EST. VALUE", "Properties"]


async def _panel_control(page) -> dict | None:
    """Negative control: does the located container leak page furniture?"""
    return await page.evaluate(
        """(outside) => {
        const panel = document.querySelector('[data-dpd-panel]');
        if (!panel) return null;
        const out = {};
        for (const t of outside) {
            out[t] = [...panel.querySelectorAll('*')].some(el =>
                el.children.length === 0 &&
                (el.textContent || '').trim().includes(t));
        }
        return out;
    }""",
        OUTSIDE_CONTROLS,
    )


async def _locate_signal_control(page, label: str) -> dict:
    """Find the native control that belongs to a recovered panel label.

    Climbs from the label to the nearest ancestor holding a named <select>/<input>, because
    the styled dropdown the user sees is a wrapper -- the native control underneath carries
    the `name`, and that name is the URL parameter Phase 4 will set.
    """
    return await page.evaluate(
        """(label) => {
        const el = [...document.querySelectorAll('*')].find(e =>
            e.children.length === 0 &&
            (e.textContent || '').trim().toLowerCase() === label.toLowerCase());
        if (!el) return {found: false};
        let node = el, controls = [];
        for (let i = 0; i < 8 && node.parentElement; i++) {
            node = node.parentElement;
            controls = [...node.querySelectorAll('select[name],input[name]')].map(c => ({
                name: c.name, tag: c.tagName, type: c.type || null,
                options: c.tagName === 'SELECT' ? [...c.options].map(o => o.value) : null,
            }));
            if (controls.length) break;
        }
        // The section heading gives the parameter its dotted prefix in the DOM.
        let section = null, s = el;
        for (let i = 0; i < 10 && s.parentElement; i++) {
            s = s.parentElement;
            const head = [...s.querySelectorAll('*')].find(h =>
                h.children.length === 0 && /Details$/.test((h.textContent || '').trim()));
            if (head) { section = head.textContent.trim(); break; }
        }
        return {found: true, section, controls,
                siblings: [...(el.parentElement ? el.parentElement.children : [])]
                    .map(c => (c.textContent || '').trim().slice(0, 40)).slice(0, 8)};
    }""",
        label,
    )


async def capture_param(page, label: str) -> dict:
    """Click a panel filter and read the field name off the search request it fires.

    Neither of the cheaper routes works for the Foreclosure Filters section. Its items
    carry no native `name` (the 36-control dump does not include them), and clicking a
    filter does NOT rewrite the URL -- the Phase 2b toggle test already showed four
    controls clicking cleanly with `url_changed: false`. What does change is the search
    request SiftMap posts, and its payload carries the server-side field name, which is
    the same name the URL accepts. So the request is the ground truth here.
    """
    seen: list[dict] = []

    def on_request(req):
        # Every request, both verbs. SiftMap's search may be a GET with the filter in the
        # query string, so a POST-only listener sees nothing and reports a false gap.
        try:
            seen.append({"method": req.method, "url": req.url,
                         "body": (req.post_data or "")[:4000]})
        except Exception:  # noqa: BLE001
            pass

    page.on("request", on_request)
    # Scroll the row into view, then click it with a REAL mouse event. A JS .click() here
    # does nothing at all -- no search request fires and the count does not move -- the
    # same behaviour the More button showed, where a JS click opened only a 41-label stub.
    box = await page.evaluate(
        """(label) => {
        const el = [...document.querySelectorAll('*')].find(e =>
            e.children.length === 0 &&
            (e.textContent || '').trim().toLowerCase() === label.toLowerCase());
        if (!el) return null;
        el.scrollIntoView({behavior: 'instant', block: 'center'});
        const r = (el.closest('label') || el).getBoundingClientRect();
        return {x: r.x, y: r.y, w: r.width, h: r.height};
    }""",
        label,
    )
    clicked = bool(box)
    if box:
        await page.mouse.click(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
    await page.wait_for_timeout(7000)
    page.remove_listener("request", on_request)
    if not clicked:
        return {"clicked": False}

    needle = label.lower().replace(" ", "")
    def _rel(s: str) -> bool:
        flat = s.lower().replace(" ", "").replace("_", "")
        return needle in flat or "notice" in flat or "foreclosure" in flat or "lispenden" in flat

    hits = [s for s in seen if _rel(s["url"]) or _rel(s["body"])]
    return {"clicked": True, "requests": len(seen), "matching": hits[:4],
            "all_urls": [s["url"].split("?")[0] for s in seen][:20],
            "count_after": await _result_count(page)}


# The Foreclosure Filters section, read straight out of its markup. It is a styled
# SelectMulti with no native <select> and no `name`, so the option VALUES are known exactly
# but the parameter name has to be probed the same way the rest of the vocabulary was.
#
# This section is the find that matters most in Phase 2d. The doors-per-deal workbooks say
# the 7 MD jurisdictions are judicial-foreclosure and must use Lis Pendens / Final Judgment
# rather than Notice of Foreclosure -- and both are here, as `LP` and `FJ`.
NOTICE_TYPE_CODES = {
    "CO": "Court Order", "FJ": "Final Judgement", "LP": "Lis Pendens",
    "ND": "Notice of Default", "NF": "Notice of Foreclosure",
    "NS": "Notice of Sale", "NT": "Notice of Trustee Sale",
}
FORECLOSURE_STATUS_CODES = ["pre_foreclosure", "auction", "foreclosure"]

FORECLOSURE_PARAM_CANDIDATES = [
    "extra_notice_type=LP",
    "extra_notice_types=LP",
    "extra_foreclosure_notice_type=LP",
    "extra_foreclosure_type=LP",
    "notice_type=LP",
    "extra_foreclosure_status=pre_foreclosure",
    "extra_foreclosure_statuses=pre_foreclosure",
    "foreclosure_status=pre_foreclosure",
]


async def probe_foreclosure_params(page, county: str, baseline: int) -> dict:
    """Probe the Foreclosure Filters parameter names by URL, against the county baseline.

    Same rule as every other probe in this build: a parameter the server accepts and
    silently ignores returns the unfiltered count, and a bare 0 proves nothing either.
    """
    base = f"{DATASIFT_SIFTMAP_URL}?location={quote(location_param(county))}"
    results = {}
    for cand in FORECLOSURE_PARAM_CANDIDATES:
        try:
            await page.goto(f"{base}&{cand}", wait_until="domcontentloaded")
            await page.wait_for_timeout(7000)
            await _clean_map(page)
            count = await _result_count(page)
            if count is None:
                verdict = "no count rendered"
            elif count == baseline:
                verdict = "ignored (returned the unfiltered count)"
            elif count == 0:
                verdict = "0 -- unconfirmed, a rejected value looks exactly like this"
            else:
                verdict = "FILTERS"
            results[cand] = {"count": count, "verdict": verdict}
            logger.info("%-46s %-9s %s", cand, count, verdict)
        except Exception as e:  # noqa: BLE001
            results[cand] = {"count": None, "verdict": f"failed: {e}"}
    return results


async def _classify_hits(page, wanted: list[str]) -> dict:
    """For each wanted signal, is it named inside the filter panel or only on a result card?"""
    return await page.evaluate(
        """(wanted) => {
        const panel = document.querySelector('[data-dpd-panel]');
        const out = {};
        for (const w of wanted) {
            const hits = [...document.querySelectorAll('*')].filter(el =>
                el.children.length === 0 &&
                (el.textContent || '').toLowerCase().includes(w.toLowerCase()));
            // A control's label is short. A sentence that merely contains the word is help
            // text -- 'probate-related transfers' inside a deed-type tooltip is not a
            // Probate filter, and counting it as one invents coverage that is not there.
            const labelish = hits.filter(h => (h.textContent || '').trim().length <= 40);
            out[w] = {
                text_hit: hits.length > 0,
                in_filter_panel: !!panel && labelish.some(h => panel.contains(h)),
                prose_only: hits.length > 0 && labelish.length === 0,
                where: hits.slice(0, 4).map(h => (h.textContent || '').trim().slice(0, 90)),
            };
        }
        return out;
    }""",
        wanted,
    )


def report(out: dict) -> int:
    print("\n=== PRO DISTRESSORS: CUSTOM COMBINATION HUNT ===")
    print(f"county    : {out.get('county')}  (baseline {out.get('baseline_count')})")
    print(f"selection : {out.get('selection') or '-'}")
    if out.get("error"):
        print(f"\n  ERROR: {out['error']}")
        return 1

    b, a = out.get("before", {}), out.get("after", {})
    print(f"scope     : panel container found via {out.get('panel_scope') or 'NOTHING'}")
    print(f"panel     : {b.get('labels')} labels / {b.get('controls')} controls"
          f"  ->  {a.get('labels')} / {a.get('controls')}")

    new_l, new_c = out.get("new_labels") or [], out.get("new_controls") or []
    print(f"\n  {len(new_l)} new labels, {len(new_c)} new named controls after the switch")
    for t in new_l[:60]:
        print(f"    label  {t}")
    for c in new_c[:60]:
        print(f"    ctrl   {c['name']:44s} {c.get('options') or c.get('type') or ''}")

    fc = out.get("foreclosure_params") or {}
    if fc:
        print(f"\n  Foreclosure Filters parameter probe on {out.get('probe_county')}"
              f" (baseline {out.get('probe_baseline')})")
        for cand, e in fc.items():
            mark = "ok  " if e["verdict"] == "FILTERS" else "----"
            print(f"    [{mark}] {cand:46s} {str(e['count']):>9s}  {e['verdict']}")
        working = [c for c, e in fc.items() if e["verdict"] == "FILTERS"]
        if working:
            print("\n    Notice Type codes available on this control:")
            for code, name in NOTICE_TYPE_CODES.items():
                print(f"      {code:3s} {name}")
        else:
            print("\n    No candidate filtered. The section exists in the UI but its URL"
                  "\n    parameter is still unknown, so Phase 4 cannot drive it yet.")

    found = out.get("found") or {}
    hits = [w for w, v in found.items() if v]
    gaps = [w for w, v in found.items() if not v]

    detail = out.get("found_detail") or {}
    decoys = [w for w, v in detail.items() if v.get("text_hit") and not v.get("in_filter_panel")]
    if decoys:
        print(f"\n  Text-matched on the page but NOT in the filter panel (result-card"
              f" distressor badges, not filters): {', '.join(decoys)}")

    print("\n  Signals recovered:", ", ".join(hits) or "NONE")
    if gaps:
        print("  Still absent from SiftMap's entire filter surface:")
        for g in gaps:
            print(f"    {g}{('  <-- ' + STAKES[g]) if g in STAKES else ''}")
    if not hits:
        print("\n  Custom Combination reveals no new distressor vocabulary. These six do not"
              "\n  exist in SiftMap for this account, so the counties that depend on them"
              "\n  ship on their best BUILDABLE stack and the lift cost must be stated.")
    return 0


FILTERS_PATH = ROOT / "output" / "dpd_siftmap_filters.json"

# What Phase 2b/2d actually confirmed, with the evidence that settles each one. These are
# merged into the Phase 2 filter vocabulary because dpd_buildability.py reads that file as
# the single record of "what SiftMap can filter on".
CONFIRMED: dict[str, dict] = {
    "Out-of-State": {
        "param": "extra_absentee_in_state=true",
        "count": 24626,
        "probe_county": "District of Columbia",
        "note": "Direction proven by arithmetic, not by the label: owner-occupied owners "
                "live at the property, so their mailing state IS the property state. "
                "owner_occupied AND flag=true is 1,586 of 129,292 (~0), while "
                "owner_occupied AND flag=false is 120,092 (~all). So true = OUT-OF-STATE "
                "and the field NAME 'absentee_in_state' is the misnomer, not the label.",
    },
    "Lis Pendens": {
        "param": "extra_foreclosure_notice_type=LP",
        "count": 14,
        "probe_county": "Baltimore",
        "note": "Found in the More panel's Foreclosure Filters section, which the earlier "
                "236-label dump had not scrolled far enough to reach. FILTERABLE but the "
                "DATA IS NOT THERE: 14 records in a 303,888-property county. That matches "
                "the workbooks' own finding that 12 of 14 jurisdictions have no "
                "foreclosure coverage, so this does not rescue the MD judicial stacks.",
    },
    "Notice of Default": {
        "param": "extra_foreclosure_notice_type=ND",
        "count": 169,
        "probe_county": "Fairfax",
        "note": "Replaces preset_pre_foreclosure for this signal. The preset is far "
                "narrower than the notice-type filter on the same county (Fairfax: preset "
                "17, status 245, notice type ND 169), and 169 is the figure consistent "
                "with the workbook's own Fairfax Notice-of-Default list size of 93 once "
                "the buy box is applied. The preset would have understated it 10x.",
    },
    "Notice of Foreclosure": {
        "param": "extra_foreclosure_notice_type=NF",
        "count": 1,
        "probe_county": "Baltimore",
        "note": "Filterable, but 1 record in Baltimore County. Non-judicial signal in a "
                "judicial state, exactly as the workbooks predicted.",
    },
}


def merge_into_filters(path: Path = FILTERS_PATH) -> int:
    """Fold the confirmed Phase 2b/2d parameters into the Phase 2 filter vocabulary.

    Additive only: an entry that already carries a working `param` is left alone and
    reported, so re-running cannot quietly overwrite an earlier measurement.
    """
    if not path.exists():
        print(f"  {path} not found -- run dpd_siftmap_discover.py first")
        return 1
    doc = json.loads(path.read_text(encoding="utf-8"))
    sp = doc.setdefault("signal_params", {})
    changed = []
    for signal, rec in CONFIRMED.items():
        existing = sp.get(signal) or {}
        if existing.get("param") and existing["param"] != rec["param"]:
            print(f"  [keep] {signal}: already {existing['param']}, not replacing "
                  f"with {rec['param']}")
            continue
        if existing.get("param") == rec["param"]:
            continue
        sp[signal] = {**rec, "source": "phase2d"}
        changed.append(signal)
    doc["phase2d_merged_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"  merged {len(changed)} signal(s): {', '.join(changed) or 'none (already current)'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Hunt the PRO Custom Combination distressor list")
    ap.add_argument("--county", default="District of Columbia",
                    help="county for the panel hunt; DC has the known 215,290 baseline")
    ap.add_argument("--probe-county", default="Baltimore",
                    help="judicial county to probe the Foreclosure Filters params against "
                         "(empty string to skip)")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--merge", action="store_true",
                    help="fold the confirmed parameters into output/dpd_siftmap_filters.json "
                         "(no browser); this is what dpd_buildability.py reads")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if a.merge:
        print("\n=== MERGE CONFIRMED PARAMETERS INTO THE FILTER VOCABULARY ===")
        return merge_into_filters()
    out = asyncio.run(run(a.county, headless=not a.headed,
                      probe_county=a.probe_county or None))
    rc = report(out)
    p = Path(a.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote {p}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
