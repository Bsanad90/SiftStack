"""Turn one county's doors-per-deal rows into SiftMap preset definitions, size them, and
plan how the SAVED SiftMap filters should change to match the current data.

    python src/scripts/dpd_siftmap_manifest.py --fips 11001            # build
    python src/scripts/dpd_siftmap_manifest.py --fips 11001 --measure  # + live counts
    python src/scripts/dpd_siftmap_manifest.py --plan                  # check saved filters vs data

SOURCE OF TRUTH: the no-AI pull order (`rows_no_ai` in data/dpd_playbook_<fips>.json,
written by `dpd_playbook_extract.py --xlsx ... --fetch` from the Work Order export). This
account does not buy AI data (decision 2026-08-26), so the ladder with AI rungs removed
is the ranking; rung numbers and doors/deal are MARGINAL (see the extractor). By default
only Priority 1 rungs (plus the Tier 2 ZIP preset) become presets: every P2 SiftMap
filter was deleted on purpose (2026-09-07). `--priorities 1,2` widens it.

`--plan` (offline, writes nothing to the account) compares the complete saved-filter
inventory -- the read-only `dpd_siftmap_presets.py --audit-auto-add` output, which reads
the management page, NOT the ~100-row popover -- against the manifests built from current
data, and sorts every saved filter into keep / keep-old-label / stale / missing / hold /
out-of-scope / foreign. A saved P2 filter is stale (Basem 2026-09-24: "No need for P2
filters"). It writes a reviewed-allowlist file for the EXISTING gated delete
(`dpd_siftmap_presets.py --delete-from-file`) and a save list for `--commit --save-list`.
It never deletes filters with auto-add ON, the held Montgomery P1-04 pull, filters whose
auto-add state is unknown, or anything not named like a DPD preset: those are held back
for Basem. Counties default to the 6-county footprint.

The playbook's instruction is literal: "pull your Priority 1 rows ... save each one as its
own preset in SiftMap, and work them top to bottom." So this emits ONE preset per Priority
1 / Priority 2 row that SiftMap can actually express, in ladder order (priority, then
doors/deal ascending), plus a Tier 2 geography preset from the workbook's own rated ZIPs.
Rows SiftMap cannot filter (HOA Lien, Other Lien, Low Income, Bad Credit, the AI score
bands, Obituary) are kept in the manifest as stated gaps and routed to first-to-market.

Reads  data/dpd_playbook_<fips>.json   (dpd_playbook_extract.py)
Writes data/dpd_siftmap_manifest_<fips>.json; --plan writes output/dpd_siftmap_plan/<stamp>/

Every URL carries the single-family buy box. The saved-preset URL does NOT carry
`in_my_account_mode=not_in` -- that is a pull-time suppression, so `--measure` records
both the raw count and the not-in-account count.

`--measure` is read-only: it opens URLs and reads the "<N> Properties" figure. Nothing is
saved and no record is added. Three guards, each from an earlier failure in this build:
  * a count EQUAL to the unfiltered single-family baseline means a parameter was ignored,
    so that row is marked unbuildable rather than saved as a county-wide preset;
  * a count of 0 is recorded as "measured 0", never as success or as a broken filter (the
    Notice-of-Foreclosure rows legitimately measure 0-2 in DC);
  * a page that never renders its count is retried once and then recorded None.
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

from dpd.jurisdictions import BY_FIPS  # noqa: E402
from dpd_tier_configs import BUY_BOX, build_url, location_param  # noqa: E402

NOT_IN = "in_my_account_mode=not_in"
T2_STARS_MIN = 4       # the workbook's own rating; 4-5 star ZIPs make the Tier 2 layer
T2_MAX_ZIPS = 5        # the playbook's "top 3-5 zip codes"

# Atomic signal -> URL fragment(s). Every entry except the proxy was confirmed live in
# Phase 2/2b/2d (output/dpd_siftmap_filters.json): a parameter that the server ignores
# returns the unfiltered count, and these did not.
SIGNAL_PARAM = {
    "Absentee": ["preset_absentee_owners=true"],
    "Free & Clear": ["preset_free_clear=true"],
    "High Equity": ["preset_high_equity=true"],
    "Vacant": ["preset_vacant=true"],
    "Zombie": ["preset_zombie=true"],
    "Judgment Lien": ["preset_judgment=true"],
    "Pre-Probate": ["preset_deceased=true"],
    "Tax Delinquent": ["preset_tax_lien=true"],
    # Label "Absentee-Out-of-State" against field extra_absentee_in_state: the LABEL is
    # right. true excludes owner-occupiers, which only out-of-state can do (Phase 2b).
    "Out-of-State": ["extra_absentee_in_state=true"],
    "Senior": ["extra_owner_age_min=65"],
    "Notice of Foreclosure": ["extra_foreclosure_notice_type=NF"],
    "Notice of Default": ["extra_foreclosure_notice_type=ND"],
    "Lis Pendens": ["extra_foreclosure_notice_type=LP"],
}

# Signals SiftMap will not filter on at all (the More panel's 260 labels and 36 named
# controls were dumped; these are absent). AI bands are recognised parameters but every
# property on this account scores empty -- a billing decision, not a technical gap.
UNFILTERABLE = {
    "HOA Lien": "no SiftMap filter (badge only on property cards)",
    "Other Lien": "no SiftMap filter",
    "Low Income": "no SiftMap filter",
    "Bad Credit": "no SiftMap filter",
    "Bankruptcy": "no SiftMap filter",
    "Estate Sale": "no SiftMap filter",
    "Probate": "no SiftMap filter; first-to-market source",
    "Obituary": "not a SiftMap filter; the account's Obituary list is an FTM feed",
}
# AI rungs arrive as "AI score 95+", "AI score 70-89" ... (the band set differs per county),
# so they are matched by prefix rather than listed.
AI_REASON = "AI data not bought on this account (decision 2026-08-26)"


def _unfilterable_reason(signal: str) -> str:
    if signal.lower().startswith("ai score"):
        return AI_REASON
    return UNFILTERABLE.get(signal, "no SiftMap filter")

# Tired Landlord has no filter. The nearest control is the "Owners with multiple
# properties" checkbox (ownerDetails.extra_owners_with_multiple_properties). Its URL value
# form is unconfirmed, so --measure probes these in order and takes the first that filters.
PROXY_SIGNAL = "Tired Landlord"
PROXY_LABEL = "Tired Landlord (proxy)"   # reconstructed as absentee + owned 10+ yrs; see PROXY_CANDIDATES
# Measured 2026-08-27 on DC: extra_owners_with_multiple_properties=true returns 47,399 of
# 48,530 single-family parcels (98%) against a workbook Tired Landlord list of 2,046. It
# "filters" in the narrow sense (not the baseline) and restricts nothing, so a count alone
# is not enough: a proxy is accepted only when it lands within PROXY_BAND of the workbook
# list size for the standalone signal. Candidates are tried in order.
# Probed 2026-08-27 on DC against the workbook's 2,046:
#   preset_absentee_owners=true&extra_years_owned_min=10   2,050   <-- 0.2% off: this IS the definition
#   extra_owner_is_investor=true                           1,789
#   preset_absentee_owners=true&extra_owner_is_investor=true 918
#   extra_owners_with_multiple_properties=true            47,399   (98% of the county)
PROXY_CANDIDATES = ["preset_absentee_owners=true&extra_years_owned_min=10",
                    "extra_owner_is_investor=true",
                    "preset_absentee_owners=true&extra_owner_is_investor=true",
                    "extra_owners_with_multiple_properties=true"]
PROXY_BAND = (0.5, 2.0)   # count / workbook list size must fall inside this

DEFAULT_PRIORITIES = (1,)   # every P2 SiftMap filter was deleted on purpose (2026-09-07)
# The 6-county footprint (CLAUDE.md "Main counties", narrowed 2026-09-01). --plan touches
# only these unless --fips names others; the out-of-footprint filters were deleted on
# purpose 2026-09-07 and must not be rebuilt by default.
FOOTPRINT_FIPS = ("11001", "24031", "24003", "24021", "51059", "51013")
# Saved filters the planner must never propose deleting, whatever the data says.
HOLD_NAMES = {
    "Montgomery P1-04 Free & Clear + Out-of-State":
        "held for a separate go (~1,709 records, auto-add census 2026-09-08)",
}
# The management page glues a filter's description onto its name; every description
# this build writes starts with this prefix (dpd_siftmap_presets._describe).
DESC_PREFIX = "Doors-per-deal"
DEFAULT_INVENTORY = ROOT / "output" / "dpd_siftmap_presets_account_audit.json"


def bare_name(title: str) -> str:
    """A saved filter's NAME from a management-page title (name + description glued)."""
    t = (title or "").strip()
    i = t.find(DESC_PREFIX)
    return t[:i].strip() if i > 0 else t


def _name(abbr: str, row: dict) -> str:
    sig = row["signal"].replace(PROXY_SIGNAL, PROXY_LABEL)
    return f"{abbr} P{row['priority']}-{row['rank']:02d} {sig}"


def build(pb: dict, proxy_param: str | None = PROXY_CANDIDATES[0],
          priorities: tuple = DEFAULT_PRIORITIES) -> dict:
    # NOTE: the proxy param is a placeholder until --measure confirms or demotes it.
    j = BY_FIPS[pb["fips"]]
    loc = location_param(j.market_finder_label, j.state_abbr, j.fips)
    abbr = j.state_abbr if j.state_abbr == "DC" else j.name.split(",")[0]

    ladder = pb.get("rows_no_ai")
    if ladder is None:
        raise SystemExit(f"data/dpd_playbook_{pb['fips']}.json has no rows_no_ai (the no-AI "
                         "ladder). Re-run dpd_playbook_extract.py --xlsx <export> --fetch.")
    entries, gaps = [], []
    for row in ladder:
        if row["priority"] not in priorities:
            continue
        params, missing, proxy = [], [], False
        for s in row["signals"]:
            if s in SIGNAL_PARAM:
                params += SIGNAL_PARAM[s]
            elif s == PROXY_SIGNAL:
                proxy = True
                if proxy_param:
                    params.append(proxy_param)
                else:
                    missing.append(f"{s} (proxy unconfirmed)")
            else:
                missing.append(s)
        base = {
            "name": _name(abbr, row), "priority": row["priority"], "rank": row["rank"],
            "rung": row.get("rung"), "doors_added": row.get("doors_added"),
            "signal": row["signal"], "signals": row["signals"], "keys": row.get("keys"),
            "lift": row["lift"], "dpd": row["dpd"], "deals": row["deals"],
            "workbook_list_size": row["list_size"], "typical_gross": row["typical_gross"],
            "caveat": row.get("caveats"), "verify_reason": row.get("verify_reason"),
            "plan": row.get("plan"), "proxy": proxy,
        }
        if missing:
            base["buildable"] = False
            base["missing_signals"] = missing
            base["reason"] = "; ".join(_unfilterable_reason(m) for m in missing)
            gaps.append(base)
            continue
        base.update({
            "buildable": True, "params": params,
            "url": build_url(loc, params + BUY_BOX),
            "url_not_in": build_url(loc, params + BUY_BOX + [NOT_IN]),
        })
        entries.append(base)

    # Pull order: the ladder's own rung order (marginal dpd is not monotonic, so never
    # re-sort by it).
    entries.sort(key=lambda e: (e["priority"], e["rung"]))

    # Tier 2: the workbook's rated ZIPs, 4-5 stars, top 5 by deals.
    zips = [z for z in pb.get("top_zips") or [] if (z.get("stars") or 0) >= T2_STARS_MIN]
    zips.sort(key=lambda z: -(z.get("deals") or 0))
    zips = zips[:T2_MAX_ZIPS]
    t2 = None
    if zips:
        zl = [z["zip"] for z in zips]
        zp = [f"zip_codes={quote(json.dumps(zl))}"]
        t2 = {
            "name": f"{abbr} T2 Top ZIPs {' '.join(zl)}",
            "tier": "Tier 2", "zips": zips, "params": zp,
            "url": build_url(loc, zp + BUY_BOX),
            "url_not_in": build_url(loc, zp + BUY_BOX + [NOT_IN]),
            "note": "geography only, no distressor stack; the bulk + mail tier trades lift "
                    "for reach. ZIPs are the workbook's own 4-5 star ratings by deals.",
        }

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "fips": j.fips, "jurisdiction": j.name, "state_abbr": j.state_abbr,
        "source": pb.get("source_xlsx") or "playbook JSON shard",
        "location": json.loads(loc),
        "buy_box": BUY_BOX,
        "baseline_url": build_url(loc, list(BUY_BOX)),
        "proxy": {"signal": PROXY_SIGNAL, "label": PROXY_LABEL, "param": proxy_param,
                  "candidates": PROXY_CANDIDATES, "status": "unmeasured"},
        "presets": entries,
        "tier2": t2,
        "gaps": gaps,
        "ladder": "no-AI (rows_no_ai)",
        "ladder_measured": pb.get("ladder_measured"),
        "priorities": list(priorities),
        # the proxy yardstick is the STANDALONE Tired Landlord list, whatever its priority
        "tired_landlord_list_size": next((r.get("list_size") for r in ladder
                                          if r.get("signals") == [PROXY_SIGNAL]), None),
    }


async def measure(m: dict) -> None:
    from datasift_core import create_browser, get_credentials, login
    from dpd_siftmap_discover import _clean_map, _result_count

    email, password = get_credentials()
    async with create_browser(headless=True) as (_b, _c, page):
        if not await login(page, email, password):
            m["measure_error"] = "login failed; nothing measured"
            return

        async def count(url: str):
            for attempt in (1, 2):
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(8000 if attempt == 1 else 14000)
                await _clean_map(page)
                c = await _result_count(page)
                if c is not None:
                    return c
            return None

        base = await count(m["baseline_url"])
        m["baseline_single_family"] = base
        print(f"  baseline (single-family, whole county): {base}")
        if base is None:
            m["measure_error"] = "no baseline count rendered; cannot judge any probe"
            return

        # Settle the proxy first: it decides whether six rows exist at all.
        loc = json.dumps(m["location"])
        chosen = None
        probes = []
        # The standalone Tired Landlord row's workbook size is the yardstick.
        wb_tl = m.get("tired_landlord_list_size") or next(
            (e.get("workbook_list_size") for e in m["presets"] + m["gaps"]
             if e.get("signals") == [PROXY_SIGNAL]), None)
        m["proxy"]["workbook_list_size"] = wb_tl
        for cand in PROXY_CANDIDATES:
            c = await count(build_url(loc, [cand] + BUY_BOX))
            if c is None:
                verdict = "no count"
            elif c == base:
                verdict = "IGNORED (baseline)"
            elif c == 0:
                verdict = "0 (name recognised, value not)"
            elif wb_tl and not (PROXY_BAND[0] <= c / wb_tl <= PROXY_BAND[1]):
                verdict = f"IMPLAUSIBLE ({c / wb_tl:.1f}x the workbook list of {int(wb_tl)})"
            else:
                verdict = "FILTERS"
            probes.append({"param": cand, "count": c, "verdict": verdict})
            print(f"  proxy probe {cand:58s} {str(c):>8s}  {verdict}")
            if verdict == "FILTERS":
                chosen = cand
                break
        m["proxy"]["probes"] = probes
        if chosen:
            m["proxy"]["param"] = chosen
            m["proxy"]["status"] = "confirmed"
            m["proxy"]["count"] = next(p["count"] for p in probes if p["param"] == chosen)
        else:
            m["proxy"]["param"] = None
            m["proxy"]["status"] = "unbuildable"
            # Demote every proxy row to a gap; the rows are rebuilt without the proxy.
            keep, demoted = [], []
            for e in m["presets"]:
                if e.get("proxy"):
                    e.update({"buildable": False, "missing_signals": [PROXY_SIGNAL],
                              "reason": "Tired Landlord proxy did not filter; see proxy.probes"})
                    for k in ("params", "url", "url_not_in"):
                        e.pop(k, None)
                    demoted.append(e)
                else:
                    keep.append(e)
            m["presets"], m["gaps"] = keep, m["gaps"] + demoted
        if chosen:
            # Rebuild proxy rows on the confirmed form (it may be several fragments).
            for e in m["presets"]:
                if e.get("proxy"):
                    params = []
                    for p in e["params"]:
                        params += chosen.split("&") if p in PROXY_CANDIDATES else [p]
                    # de-duplicate while keeping order (Absentee + proxy-with-absentee)
                    e["params"] = list(dict.fromkeys(params))
                    e["url"] = build_url(loc, e["params"] + BUY_BOX)
                    e["url_not_in"] = build_url(loc, e["params"] + BUY_BOX + [NOT_IN])

        for e in m["presets"] + ([m["tier2"]] if m.get("tier2") else []):
            c = await count(e["url"])
            n = await count(e["url_not_in"])
            e["measured_count"] = c
            e["measured_not_in_account"] = n
            if c is None:
                e["measure_status"] = "no_count"
            elif c == base:
                e["measure_status"] = "IGNORED_PARAM"
                e["buildable"] = False
                e["reason"] = ("returned the unfiltered single-family baseline; a parameter "
                               "was silently ignored -- do not save this preset")
            elif c == 0:
                e["measure_status"] = "measured_zero"
            else:
                e["measure_status"] = "ok"
            print(f"  {e['name'][:60]:60s} {str(c):>8s}  not-in-account {str(n):>8s}  "
                  f"{e['measure_status']}")

        bad = [e for e in m["presets"] if e.get("measure_status") == "IGNORED_PARAM"]
        if bad:
            m["gaps"] += bad
            m["presets"] = [e for e in m["presets"] if e.get("measure_status") != "IGNORED_PARAM"]
        m["measured_at"] = datetime.now().isoformat(timespec="seconds")


def report(m: dict) -> None:
    print(f"\n=== SIFTMAP PRESET MANIFEST  {m['jurisdiction']} ({m['fips']}) ===")
    print(f"source {m['source']}   presets {len(m['presets'])}   gaps {len(m['gaps'])}   "
          f"tier2 {'yes' if m.get('tier2') else 'no'}")
    print(f"proxy: {m['proxy']['status']} ({m['proxy'].get('param')})")
    print(f"\n  {'preset':62s} {'lift':>6s} {'wbook':>6s} {'count':>7s} {'not_in':>7s}  status")
    for e in m["presets"]:
        print(f"  {e['name'][:62]:62s} {str(e['lift']):>6s} "
              f"{str(int(e['workbook_list_size']) if e['workbook_list_size'] else '-'):>6s} "
              f"{str(e.get('measured_count', '-')):>7s} "
              f"{str(e.get('measured_not_in_account', '-')):>7s}  "
              f"{e.get('measure_status', 'unmeasured')}")
    if m.get("tier2"):
        t = m["tier2"]
        print(f"  {t['name'][:62]:62s} {'-':>6s} {'-':>6s} "
              f"{str(t.get('measured_count', '-')):>7s} "
              f"{str(t.get('measured_not_in_account', '-')):>7s}  "
              f"{t.get('measure_status', 'unmeasured')}")
    print("\n  Not buildable in SiftMap (routed to first-to-market):")
    for g in m["gaps"]:
        print(f"    P{g['priority']}-{g['rank']:02d} {g['signal'][:44]:44s} "
              f"{str(g['lift']):>6s}x  {g.get('reason')}")


# ── plan: saved filters vs current data ───────────────────────────────

_ID_RE = re.compile(r"^(?P<abbr>.+?) (?:(?P<pid>P(?P<p>\d)-(?P<r>\d{2}))|(?P<t2>T2)) (?P<rest>.+)$")


def _abbr_for(fips: str) -> str:
    j = BY_FIPS[fips]
    return j.state_abbr if j.state_abbr == "DC" else j.name.split(",")[0]


def _key_of(kind: str, rest: str) -> tuple:
    """What a preset IS, independent of its rank label: the signal set, or the ZIP set."""
    if kind == "T2":
        return ("T2", frozenset(re.findall(r"\b\d{5}\b", rest)))
    sig = rest.replace(PROXY_LABEL, PROXY_SIGNAL)
    return ("SIG", frozenset(x.strip() for x in sig.split(" + ")))


def list_key(name: str) -> tuple | None:
    """A DPD preset name -> what list it is (county + signal set, or county + ZIP set),
    ignoring the rank label. None for anything not named like a DPD preset."""
    m = _ID_RE.match(name)
    if not m:
        return None
    kind = "T2" if m.group("t2") else "P"
    return (m.group("abbr"),) + _key_of(kind, m.group("rest"))


def parse_saved(name: str, abbr_to_fips: dict) -> dict | None:
    m = _ID_RE.match(name)
    if not m or m.group("abbr") not in abbr_to_fips:
        return None
    kind = "T2" if m.group("t2") else "P"
    return {"fips": abbr_to_fips[m.group("abbr")], "kind": kind,
            "priority": int(m.group("p")) if m.group("p") else None,
            "key": _key_of(kind, m.group("rest"))}


def load_inventory(path: Path) -> tuple[list[dict], dict]:
    """Saved filters as [{name, auto_add}] + meta, from --audit-auto-add (preferred: it
    carries auto-add) or --list-account output (auto-add unknown -> nothing deletable)."""
    d = json.loads(path.read_text(encoding="utf-8"))
    if "results" in d:
        items = [{"name": bare_name(r.get("title") or ""), "auto_add": r.get("auto_add")}
                 for r in d["results"] if r.get("title")]
    else:
        items = [{"name": bare_name(n), "auto_add": None} for n in d.get("names") or []]
    meta = {"path": str(path), "at": d.get("at"), "total_reported": d.get("total_reported"),
            "read": len(items), "has_auto_add": "results" in d}
    return items, meta


def plan(fips_list: list[str], inventory: list[dict], priorities: tuple,
         renames: bool) -> dict:
    abbr_to_fips = {_abbr_for(f): f for f in BY_FIPS}
    current: dict = {}      # fips -> {key: entry}
    for f in fips_list:
        pb = json.loads((ROOT / "data" / f"dpd_playbook_{f}.json").read_text(encoding="utf-8"))
        m = build(pb, priorities=priorities)
        cur = {}
        for e in m["presets"]:
            cur[_key_of("P", _ID_RE.match(e["name"]).group("rest"))] = e
        if m.get("tier2"):
            cur[_key_of("T2", m["tier2"]["name"])] = m["tier2"]
        current[f] = cur

    rows, seen = [], {f: set() for f in fips_list}
    for it in inventory:
        name, auto = it["name"], it["auto_add"]
        ps = parse_saved(name, abbr_to_fips)
        row = {"name": name, "auto_add": auto}
        if ps is None:
            row.update(action="foreign", reason="not named like a DPD preset; never touched")
        elif ps["fips"] not in current:
            row.update(action="out_of_scope", fips=ps["fips"],
                       reason="county not in this plan (footprint default; pass --fips to include)")
        else:
            f, key = ps["fips"], ps["key"]
            row["fips"] = f
            cur = current[f].get(key)
            if cur is not None and key in seen[f]:
                if auto:
                    row.update(action="hold", current_name=cur["name"],
                               reason="duplicate of another saved filter, but auto-add is ON")
                else:
                    row.update(action="duplicate", current_name=cur["name"],
                               reason="a second saved filter for the same list")
            elif cur is not None:
                seen[f].add(key)
                row["current_name"] = cur["name"]
                if name == cur["name"]:
                    row.update(action="keep")
                elif name in HOLD_NAMES:
                    row.update(action="keep_old_label", reason=HOLD_NAMES[name])
                elif auto:
                    row.update(action="keep_old_label",
                               reason="auto-add is ON; renaming means delete + re-save and "
                                      "would stop the drip")
                elif renames:
                    row.update(action="rename",
                               reason="same list, new rank label: delete old, save new")
                else:
                    row.update(action="keep_old_label",
                               reason="same list, rank label out of date (--renames to fix)")
            elif name in HOLD_NAMES:
                row.update(action="hold", reason=HOLD_NAMES[name])
            elif auto:
                row.update(action="hold", reason="no longer ranked, but auto-add is ON: "
                                                 "deleting stops the drip -- decide")
            elif auto is None:
                row.update(action="hold", reason="auto-add state unknown: run "
                                                 "dpd_siftmap_presets.py --audit-auto-add")
            elif ps["kind"] == "P" and ps["priority"] not in priorities:
                # Basem, 2026-09-24: "No need for P2 filters." A saved filter whose priority
                # is outside the plan is stale, not a question.
                row.update(action="stale",
                           reason=f"P{ps['priority']} filter; only "
                                  f"{', '.join('P' + str(p) for p in priorities)} rungs are "
                                  "kept as SiftMap presets (Basem 2026-09-24)")
            else:
                row.update(action="stale", reason="list no longer in the current ranking")
        rows.append(row)

    # A current list is MISSING unless some saved filter already covers it (under any
    # label). A rename re-saves it under the new label, so it is missing again then.
    missing = []
    for f in fips_list:
        covered = {r["current_name"] for r in rows
                   if r.get("fips") == f and r.get("current_name")
                   and r["action"] in ("keep", "keep_old_label", "hold", "duplicate")}
        for e in current[f].values():
            if e["name"] in covered:
                continue
            missing.append({"fips": f, "name": e["name"], "action": "missing",
                            "priority": e.get("priority"), "rung": e.get("rung"),
                            "dpd": e.get("dpd"), "deals": e.get("deals"),
                            "list_size": e.get("workbook_list_size")})
    return {"rows": rows, "missing": missing}


ACTION_ORDER = ["stale", "rename", "duplicate", "missing", "keep_old_label", "keep", "hold",
                "decide", "out_of_scope", "foreign"]
ACTION_TEXT = {"stale": "DELETE (on the allowlist)", "rename": "DELETE old + SAVE new",
               "duplicate": "DELETE (duplicate)", "missing": "SAVE (on the save lists)",
               "keep_old_label": "keep, rank label out of date", "keep": "keep",
               "hold": "HOLD - needs Basem", "decide": "DECIDE - needs Basem (unused since 2026-09-24)",
               "out_of_scope": "untouched (county not in this plan)",
               "foreign": "untouched (not a DPD preset)"}


def write_plan(res: dict, inv_meta: dict, fips_list: list[str], priorities: tuple,
               renames: bool) -> Path:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    d = ROOT / "output" / "dpd_siftmap_plan" / stamp
    d.mkdir(parents=True, exist_ok=True)
    rel = d.relative_to(ROOT).as_posix()
    rows, missing = res["rows"], res["missing"]
    delete = [r["name"] for r in rows if r["action"] in ("stale", "rename", "duplicate")]
    (d / "delete_allowlist.txt").write_text(
        "# Reviewed allowlist for dpd_siftmap_presets.py --delete-from-file.\n"
        f"# Generated by dpd_siftmap_manifest.py --plan at {stamp}.\n"
        "# Delete a line to keep that filter. Only names listed here can be deleted.\n"
        + "".join(n + "\n" for n in delete), encoding="utf-8")
    for f in fips_list:
        names = [x["name"] for x in missing if x["fips"] == f]
        (d / f"save_list_{f}.txt").write_text("".join(n + "\n" for n in names), encoding="utf-8")
    (d / "plan.json").write_text(json.dumps(
        {"generated_at": stamp, "inventory": inv_meta, "counties": fips_list,
         "priorities": list(priorities), "renames": renames, "rows": rows, "missing": missing},
        indent=1), encoding="utf-8")

    allrows = rows + missing
    L = [f"# SiftMap preset plan {stamp}", "",
         f"Counties: {', '.join(_abbr_for(f) for f in fips_list)}. Presets from: "
         f"{', '.join('P' + str(p) for p in priorities)} rungs of the no-AI ladder + Tier 2 ZIPs.",
         f"Inventory: `{inv_meta['path']}`, read {inv_meta['at']} "
         f"({inv_meta['read']} of {inv_meta['total_reported']} saved filters"
         f"{'' if inv_meta['has_auto_add'] else '; NO auto-add data, so nothing is deletable'}).",
         "", "| Action | Count |", "|---|---:|"]
    for a in ACTION_ORDER:
        n = sum(1 for r in allrows if r["action"] == a)
        if n:
            L.append(f"| {ACTION_TEXT[a]} | {n} |")
    for a in ACTION_ORDER:
        sel = [r for r in allrows if r["action"] == a]
        if not sel:
            continue
        L += ["", f"## {ACTION_TEXT[a]} ({len(sel)})", ""]
        for r in sel:
            extra = []
            if r.get("current_name") and r["current_name"] != r["name"]:
                extra.append(f"now `{r['current_name']}`")
            if a == "missing":
                extra.append(f"rung {r.get('rung')}, {r.get('dpd')} doors/deal, "
                             f"{r.get('deals')} new deals, list {r.get('list_size')}")
            if r.get("reason"):
                extra.append(r["reason"])
            if r.get("auto_add"):
                extra.append("auto-add ON")
            L.append(f"- `{r['name']}`" + (f" -- {'; '.join(extra)}" if extra else ""))
    L += ["", "## To apply (every write step needs Basem's go)", "",
          "1. If the inventory is not from today, refresh it (read-only): "
          "`python src/scripts/dpd_siftmap_presets.py --audit-auto-add`, then re-plan.",
          f"2. Review `{rel}/delete_allowlist.txt` (delete lines to keep filters), then "
          f"`python src/scripts/dpd_siftmap_presets.py --fips <any> --delete-from-file "
          f"{rel}/delete_allowlist.txt`.",
          "3. For each county with a non-empty save list: "
          "`python src/scripts/dpd_siftmap_manifest.py --fips <fips> --measure` (rebuilds the "
          "manifest from current data; read-only counts), then "
          f"`python src/scripts/dpd_siftmap_presets.py --fips <fips> --commit --save-list "
          f"{rel}/save_list_<fips>.txt`, then `--verify --save-list` the same file.",
          "4. Re-run the audit and this plan. A clean plan shows only keep, hold, decide and "
          "untouched rows."]
    (d / "plan.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description="Build, size and plan a county's SiftMap presets")
    ap.add_argument("--fips", help="one county (build/measure), or a comma list for --plan")
    ap.add_argument("--measure", action="store_true", help="open every URL and record counts")
    ap.add_argument("--plan", action="store_true",
                    help="offline: compare the saved SiftMap filters with current data")
    ap.add_argument("--inventory", default=str(DEFAULT_INVENTORY),
                    help="--audit-auto-add (or --list-account) output to plan against")
    ap.add_argument("--priorities", default=",".join(map(str, DEFAULT_PRIORITIES)),
                    help="which ladder priorities become presets (default 1)")
    ap.add_argument("--renames", action="store_true",
                    help="plan: re-save same-list filters under their current rank label "
                         "(delete + save; never for auto-add or held filters)")
    ap.add_argument("--out")
    a = ap.parse_args()
    priorities = tuple(int(x) for x in a.priorities.split(",") if x.strip())

    if a.plan:
        fips_list = [f.strip() for f in a.fips.split(",")] if a.fips else list(FOOTPRINT_FIPS)
        for f in fips_list:
            if f not in BY_FIPS:
                raise SystemExit(f"FIPS {f} is not a target jurisdiction")
        inv_path = Path(a.inventory)
        if not inv_path.exists():
            raise SystemExit(f"missing {inv_path}; run dpd_siftmap_presets.py --audit-auto-add")
        inventory, meta = load_inventory(inv_path)
        if meta["total_reported"] and meta["read"] < meta["total_reported"]:
            raise SystemExit(f"inventory is incomplete ({meta['read']} of "
                             f"{meta['total_reported']}); a partial list would call present "
                             "filters missing. Re-run --audit-auto-add.")
        res = plan(fips_list, inventory, priorities, a.renames)
        d = write_plan(res, meta, fips_list, priorities, a.renames)
        counts: dict = {}
        for r in res["rows"] + res["missing"]:
            counts[r["action"]] = counts.get(r["action"], 0) + 1
        age = ""
        try:
            days = (datetime.now() - datetime.fromisoformat(meta["at"])).days
            if days:
                age = f"\n  inventory is {days} day(s) old -- refresh it before applying anything"
        except (TypeError, ValueError):
            pass
        print("plan: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + age)
        print(f"wrote {d.relative_to(ROOT).as_posix()}/  (plan.md, delete_allowlist.txt, "
              "save_list_<fips>.txt, plan.json)")
        return 0

    if not a.fips or a.fips not in BY_FIPS:
        raise SystemExit("--fips must name one target jurisdiction for build/measure")
    src = ROOT / "data" / f"dpd_playbook_{a.fips}.json"
    if not src.exists():
        raise SystemExit(f"missing {src}; run dpd_playbook_extract.py --fips {a.fips} first")
    out_path = Path(a.out) if a.out else ROOT / "data" / f"dpd_siftmap_manifest_{a.fips}.json"

    pb = json.loads(src.read_text(encoding="utf-8"))
    m = build(pb, priorities=priorities)
    if a.measure:
        asyncio.run(measure(m))
    elif out_path.exists():
        # Preserve earlier measurements on a rebuild without --measure.
        old = json.loads(out_path.read_text(encoding="utf-8"))
        prev = {e["name"]: e for e in old.get("presets", []) + old.get("gaps", [])}
        if old.get("tier2"):
            prev[old["tier2"]["name"]] = old["tier2"]
        # Match earlier measurements by LIST (list_key), not by name: a rank label moves
        # whenever the ladder is re-measured and the count belongs to the list.
        prev_by_key = {list_key(n): e for n, e in prev.items() if list_key(n)}
        live = m["presets"] + ([m["tier2"]] if m.get("tier2") else [])
        carried = 0
        for e in live:
            p = prev.get(e["name"]) or prev_by_key.get(list_key(e["name"])) or {}
            for k in ("measured_count", "measured_not_in_account", "measure_status"):
                if k in p:
                    e[k] = p[k]
            carried += "measured_count" in p
        if "baseline_single_family" in old:
            m["baseline_single_family"] = old["baseline_single_family"]
        # measured_at is what dpd_siftmap_build_all.py reads to SKIP the measure stage, so
        # it may only carry over when every live preset actually carries a count.
        # Otherwise a rebuild after new data would look fully measured and never be sized.
        if "measured_at" in old and carried == len(live):
            m["measured_at"] = old["measured_at"]
        elif "measured_at" in old:
            print(f"  measured_at dropped: {len(live) - carried} of {len(live)} presets are "
                  "unmeasured after the rebuild -- run --measure")
        if old.get("proxy", {}).get("status") != "unmeasured":
            m["proxy"] = old["proxy"]
    report(m)
    out_path.write_text(json.dumps(m, indent=1), encoding="utf-8")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
