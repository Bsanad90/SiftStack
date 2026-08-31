"""Phase 2: learn SiftMap's filter URL vocabulary, read-only.

SiftMap's filter contract is a URL, not a UI dance -- datasift_uploader._siftmap_search_sold
proved that by navigating straight to

    /siftmap?location=<json>&extra_last_sale_date_min=...&extra_last_sale_price_min=...

and getting a server-side filtered result. But only those three parameter names were ever
known. The doors-per-deal tier presets need many more (absentee, free & clear, vacant,
senior, tax delinquent, equity, owner age, AI score...) and guessing a parameter name
produces an empty result that looks exactly like an empty county.

The shortcut this script exploits, found while capturing the Phase 0 baseline: SiftMap
ships **20 default presets** whose names map almost one-to-one onto the doors-per-deal
atomic signals -- Vacant, High Equity, Free & Clear, Absentee Owners, Tax Lien,
Pre Foreclosure, Foreclosure, Deceased, Low Equity, Owner Occupied, Cash Buyer and more.
Applying a preset writes its filters into the URL. So instead of reverse-engineering each
control, load each preset and read the URL back.

    python src/scripts/dpd_siftmap_discover.py --headed
    python src/scripts/dpd_siftmap_discover.py --county "District of Columbia" --state DC

Writes output/dpd_siftmap_filters.json: preset name -> the extra_* parameters it sets,
plus the live result count. Signals with no discoverable parameter are recorded with a
stated reason rather than omitted, because "this filter does not exist" is the finding
that decides whether a county can be pulled from SiftMap at all.

Nothing here writes to DataSift. No records are added.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from datasift_core import (  # noqa: E402
    DATASIFT_SIFTMAP_URL,
    create_browser,
    dismiss_popups,
    get_credentials,
    login,
)

logger = logging.getLogger("dpd_siftmap_discover")

OUT_PATH = ROOT / "output" / "dpd_siftmap_filters.json"

# The 14 target jurisdictions, FIPS from the doors-per-deal workbook filenames.
FIPS = {
    "Baltimore": ("24005", "MD"),
    "Montgomery": ("24031", "MD"),
    "Anne Arundel": ("24003", "MD"),
    "Frederick": ("24021", "MD"),
    "Carroll": ("24013", "MD"),
    "Calvert": ("24009", "MD"),
    "Charles": ("24017", "MD"),
    "District of Columbia": ("11001", "DC"),
    "Prince William": ("51153", "VA"),
    "Fairfax": ("51059", "VA"),
    "Arlington": ("51013", "VA"),
    "Stafford": ("51179", "VA"),
    "Spotsylvania": ("51177", "VA"),
    "Fredericksburg City": ("51630", "VA"),
}

# Which doors-per-deal atomic signal each SiftMap default preset stands in for. A signal
# absent from this map has no default preset and must be found on the filter panel.
PRESET_TO_SIGNAL = {
    "Absentee Owners": "Absentee",
    "Free & Clear": "Free & Clear",
    "High Equity": "High Equity",
    "Low Equity": None,            # suppression input, not a doors-per-deal signal
    "Negative Equity": None,       # suppression input
    "Vacant": "Vacant",
    "Tax Lien": "Tax Delinquent",
    "Pre Foreclosure": "Notice of Default",
    "Foreclosure": "Notice of Foreclosure",
    "Deceased": "Pre-Probate",
    "Judgment": "Judgment Lien",
    "Owner Occupied": None,        # the inverse of Absentee, kept for the suppression stack
    "Cash Buyer": None,            # dispo-side, not a seller signal
    "Bank Owned REO": None,
    "Private Lender": None,
    "Assumable Loan": None,
    "Adjustable Loans": None,
    "Quick Resale": None,
    "Auction": None,
    "Land": None,                  # excluded by the single-family base criteria
}

# Doors-per-deal signals with no default preset. These must come off the filter panel,
# and any that cannot be found is a real coverage gap -- HOA Lien especially, since it is
# Anne Arundel's only Priority-1 signal.
SIGNALS_WITHOUT_PRESET = [
    "Out-of-State", "Senior", "Tired Landlord", "Low Income", "Bad Credit",
    "HOA Lien", "Lis Pendens", "Other Lien", "Bankruptcy", "Estate Sale",
    "Zombie", "Probate",
]


def location_param(county: str) -> str:
    """Build SiftMap's `location` JSON for a county search."""
    fips, state = FIPS[county]
    return json.dumps({
        "searchType": "county",
        "title": f"{county} County, {state}",
        "county": county,
        "state": state,
        "counties": [{"fips": fips, "county_name": county}],
    })


def extras_from_url(url: str) -> dict:
    """Pull the extra_* filter parameters out of a SiftMap URL."""
    qs = parse_qs(urlparse(url).query)
    return {k: (v[0] if len(v) == 1 else v) for k, v in qs.items() if k != "location"}


async def _clean_map(page) -> None:
    """Remove the overlays that swallow pointer events on SiftMap."""
    await dismiss_popups(page)
    await page.evaluate(
        """() => {
        document.querySelectorAll('[class*="PropertyDetails"]').forEach(p => p.remove());
        const nps = document.getElementById('npsIframeContainer');
        if (nps) nps.remove();
    }"""
    )
    await page.wait_for_timeout(500)


async def _result_count(page) -> int | None:
    """Read the '<N> Properties' figure off the map."""
    txt = await page.evaluate(
        """() => {
        const hit = [...document.querySelectorAll('*')].find(
            el => el.children.length === 0 && /[\\d,]+\\s*Propert/i.test(el.textContent || ''));
        return hit ? hit.textContent.trim() : '';
    }"""
    )
    m = re.search(r"([\d,]+)\s*Propert", txt or "", re.I)
    return int(m.group(1).replace(",", "")) if m else None


async def _open_presets(page) -> bool:
    for label in ("Presets", "Saved Filters", "Default Presets"):
        el = page.get_by_text(label, exact=True)
        if await el.count() > 0:
            try:
                await el.first.click()
                await page.wait_for_timeout(2200)
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


# Candidate URL parameter names per preset. The form was proven live on DC:
# preset_negative_equity=true, preset_judgment=true, preset_adjustable_loans=true,
# preset_quick_resale=true, preset_auction=true. Ambiguous snake-casing (Free & Clear,
# Bank Owned REO) gets several candidates and the probe settles it.
PRESET_PARAM_CANDIDATES = {
    "Absentee Owners": ["preset_absentee_owners", "preset_absentee"],
    "Free & Clear": ["preset_free_clear", "preset_free_and_clear", "preset_freeclear"],
    "High Equity": ["preset_high_equity"],
    "Low Equity": ["preset_low_equity"],
    "Negative Equity": ["preset_negative_equity"],
    "Vacant": ["preset_vacant"],
    "Tax Lien": ["preset_tax_lien"],
    "Pre Foreclosure": ["preset_pre_foreclosure", "preset_preforeclosure"],
    "Foreclosure": ["preset_foreclosure"],
    "Deceased": ["preset_deceased"],
    "Judgment": ["preset_judgment"],
    "Owner Occupied": ["preset_owner_occupied"],
    "Cash Buyer": ["preset_cash_buyer"],
    "Bank Owned REO": ["preset_bank_owned_reo", "preset_bank_owned", "preset_reo"],
    "Private Lender": ["preset_private_lender"],
    "Assumable Loan": ["preset_assumable_loan", "preset_assumable_loans"],
    "Adjustable Loans": ["preset_adjustable_loans"],
    "Quick Resale": ["preset_quick_resale"],
    "Auction": ["preset_auction"],
    "Land": ["preset_land"],
}


async def discover_presets(page, county: str, out: dict) -> None:
    """Probe each preset parameter by URL and confirm it actually filters.

    Clicking presets in the UI proved unreliable -- TopPresetListItem intercepts pointer
    events and half the list hides behind "Show more". Probing the URL directly is both
    deterministic and a truer test, because the URL is exactly how the Phase 4 pull will
    drive SiftMap.

    A parameter that the server silently IGNORES returns the unfiltered county count.
    That is the trap this codebase already hit once on the Zillow API, where price_min was
    accepted and discarded, so every probe is compared against the baseline rather than
    trusted for returning a number.
    """
    base = f"{DATASIFT_SIFTMAP_URL}?location={quote(location_param(county))}"

    await page.goto(base, wait_until="domcontentloaded")
    await page.wait_for_timeout(9000)
    await _clean_map(page)
    baseline = await _result_count(page)
    out["baseline_count"] = baseline
    logger.info("Unfiltered %s: %s properties", county, baseline)
    if baseline is None:
        out["error"] = "could not read an unfiltered result count; cannot verify any filter"
        return

    for preset, candidates in PRESET_PARAM_CANDIDATES.items():
        entry = {"signal": PRESET_TO_SIGNAL.get(preset), "param": None,
                 "count": None, "note": ""}
        for cand in candidates:
            try:
                await page.goto(f"{base}&{cand}=true", wait_until="domcontentloaded")
                await page.wait_for_timeout(7000)
                await _clean_map(page)
                count = await _result_count(page)
                if count is None:
                    entry["note"] = "no result count rendered"
                    continue
                if count == baseline:
                    # Same as unfiltered: the server accepted and ignored it.
                    entry["note"] = (f"{cand} returned the unfiltered count ({count}) "
                                     "-- silently ignored, not a real filter")
                    continue
                entry["param"], entry["count"], entry["note"] = cand, count, ""
                break
            except Exception as e:  # noqa: BLE001
                entry["note"] = f"failed: {e}"
        if entry["param"]:
            logger.info("%-18s %-24s %6s properties", preset, entry["param"], entry["count"])
        else:
            logger.info("%-18s GAP: %s", preset, entry["note"][:70])
        out["presets"][preset] = entry


# The 12 doors-per-deal signals with no default preset. The playbook calls these the
# "SiftMap Pro Distressors" layer, and the account already carries a saved filter named
# "Stacked Distressors", so they should exist as parameters even without a named preset.
# Several are load-bearing: Bad Credit is Prince William's 196x signal, and HOA Lien is
# Anne Arundel's ONLY Priority-1 signal.
# Candidates are full query FRAGMENTS, because the two parameter families take different
# value types. Both are confirmed live on DC (unfiltered baseline 215,290):
#   preset_<name>=true          boolean distressor presets, 21 confirmed
#   extra_<field>_min=<number>  numeric ranges -- extra_owner_age_min=65 -> 36,507
# Passing a boolean to a numeric param is the trap: extra_owner_age_min=true returns 0,
# which looks like a working-but-empty filter rather than the type error it is.
SIGNAL_PARAM_CANDIDATES = {
    "Out-of-State": ["preset_out_of_state=true", "preset_out_of_state_owner=true",
                     "preset_absentee_out_of_state=true", "extra_out_of_state=true"],
    "Senior": ["extra_owner_age_min=65", "preset_senior=true", "preset_senior_owner=true"],
    "Tired Landlord": ["preset_tired_landlord=true", "preset_tired_landlords=true"],
    "Low Income": ["preset_low_income=true"],
    "Bad Credit": ["preset_bad_credit=true", "preset_low_credit=true",
                   "preset_low_credit_score=true"],
    "HOA Lien": ["preset_hoa_lien=true", "preset_hoa=true", "preset_hoa_liens=true"],
    "Lis Pendens": ["preset_lis_pendens=true"],
    "Other Lien": ["preset_other_lien=true", "preset_lien=true", "preset_liens=true"],
    "Bankruptcy": ["preset_bankruptcy=true"],
    "Estate Sale": ["preset_estate_sale=true", "preset_estate_sales=true"],
    "Zombie": ["preset_zombie=true", "preset_zombie_property=true"],
    "Probate": ["preset_probate=true"],
}


async def discover_signal_params(page, county: str, out: dict) -> None:
    """Probe parameter names for the signals that have no named default preset."""
    base = f"{DATASIFT_SIFTMAP_URL}?location={quote(location_param(county))}"
    baseline = out.get("baseline_count")
    if baseline is None:
        return
    out["signal_params"] = {}
    for signal, candidates in SIGNAL_PARAM_CANDIDATES.items():
        entry = {"param": None, "count": None, "note": "no candidate filtered"}
        for cand in candidates:
            try:
                await page.goto(f"{base}&{cand}", wait_until="domcontentloaded")
                await page.wait_for_timeout(7000)
                await _clean_map(page)
                count = await _result_count(page)
                if count is None or count == baseline:
                    continue
                if count == 0:
                    # Zero is not proof of a working filter. A numeric parameter handed a
                    # boolean returns 0, which reads as "filter works, county is empty".
                    entry["note"] = (f"{cand} returned 0 -- treat as unconfirmed; a range "
                                     "parameter given a non-numeric value does exactly this")
                    continue
                entry = {"param": cand, "count": count, "note": ""}
                break
            except Exception as e:  # noqa: BLE001
                entry["note"] = f"failed: {e}"
        if entry["param"]:
            logger.info("%-16s %-28s %6s properties", signal, entry["param"], entry["count"])
        else:
            logger.info("%-16s GAP -- no working parameter found", signal)
        out["signal_params"][signal] = entry


async def discover_panel_filters(page, county: str, out: dict) -> None:
    """Inventory the filter controls SiftMap actually offers, for the signals with no preset."""
    try:
        await page.goto(f"{DATASIFT_SIFTMAP_URL}?location={quote(location_param(county))}",
                        wait_until="domcontentloaded")
        await page.wait_for_timeout(9000)
        await _clean_map(page)

        labels = await page.evaluate(
            """() => {
            const out = [];
            document.querySelectorAll('*').forEach(el => {
                if (el.children.length > 0) return;
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0 || r.x > 420) return;   // left filter rail
                const t = (el.textContent || '').trim();
                if (t && t.length > 1 && t.length < 45) out.push(t);
            });
            return [...new Set(out)];
        }"""
        )
        out["panel_labels"] = labels

        # Report which of the preset-less signals have anything resembling a control.
        found = {}
        for signal in SIGNALS_WITHOUT_PRESET:
            needle = signal.lower().replace("-", " ")
            match = [t for t in labels if needle in t.lower().replace("-", " ")]
            found[signal] = match or None
        out["signals_without_preset"] = found

        missing = [s for s, m in found.items() if not m]
        logger.info("Panel labels: %d. Signals with no visible control: %s",
                    len(labels), ", ".join(missing) or "none")
    except Exception as e:  # noqa: BLE001
        out["panel_labels_error"] = str(e)


async def run(county: str, headless: bool) -> dict:
    email, password = get_credentials()
    out = {
        "discovered_at": datetime.now().isoformat(timespec="seconds"),
        "county": county,
        "fips": FIPS[county][0],
        "presets": {},
    }
    async with create_browser(headless=headless) as (_b, _c, page):
        if not await login(page, email, password) or "/login" in page.url:
            out["error"] = "login failed"
            return out
        await discover_panel_filters(page, county, out)
        await discover_presets(page, county, out)
        await discover_signal_params(page, county, out)
    return out


def report(out: dict) -> int:
    print("\n=== SIFTMAP FILTER DISCOVERY ===")
    print(f"county : {out.get('county')} ({out.get('fips')})")
    print(f"at     : {out.get('discovered_at')}\n")

    usable = 0
    print("  Default presets -> URL parameters")
    for preset, e in out.get("presets", {}).items():
        sig = e.get("signal") or "-"
        param = e.get("param")
        if param:
            usable += 1
            print(f"    [ok  ] {preset:18s} {sig:18s} {param}  ({e.get('count')})")
        else:
            print(f"    [gap ] {preset:18s} {sig:18s} {e.get('note')}")

    sp = out.get("signal_params") or {}
    if sp:
        print("\n  Signals with no named preset -> probed parameters")
        for signal, e in sp.items():
            if e.get("param"):
                usable += 1
                print(f"    [ok  ] {signal:16s} {e['param']:28s} ({e['count']})")
            else:
                flag = "  <-- Anne Arundel's ONLY Priority-1 signal" if signal == "HOA Lien" else ""
                print(f"    [GAP ] {signal:16s} no working parameter found{flag}")

    gaps = [s for s, m in (out.get("signals_without_preset") or {}).items() if not m]
    if gaps:
        print("\n  Doors-per-deal signals with no default preset and no obvious control:")
        for s in gaps:
            flag = "  <-- Anne Arundel's ONLY Priority-1 signal" if s == "HOA Lien" else ""
            print(f"    {s}{flag}")

    print(f"\n  {usable} of {len(out.get('presets', {}))} presets wrote replayable URL parameters.")
    if usable == 0:
        print("\n  No preset produced URL parameters. The pull cannot be driven by URL as"
              "\n  planned -- stop and reconsider before building Phase 4.")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover SiftMap's filter URL vocabulary")
    ap.add_argument("--county", default="District of Columbia", choices=sorted(FIPS))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--out", default=str(OUT_PATH))
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    out = asyncio.run(run(a.county, headless=not a.headed))
    rc = report(out)
    path = Path(a.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nWrote {path}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
