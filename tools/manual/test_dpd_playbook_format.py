"""Offline guard for the Work Order playbook format and the SiftMap preset planner.

No browser, no network, no account. Run after ANY change to dpd_playbook_extract.py
(parse_ladder / merge_shard / rows_from_capture) or to dpd_siftmap_manifest.py
(build / list_key / plan). The failures this guards against were silent:

  * a rung's priority comes from the SECTION row above it, not a column -- a parser
    that misses the section rows files every rung under None;
  * the no-AI ladder (`rows_no_ai`) is the ranking on this account; a build() that
    falls back to the exported (with-AI) ladder ranks lists AI would have claimed;
  * a rank label moves whenever the ladder is re-measured, so any presence check that
    compares labels re-saves an existing list as a duplicate (list_key must not);
  * the planner must never put an auto-add filter, a held filter, a P2 filter or a
    foreign filter on the delete allowlist.

    python tools/manual/test_dpd_playbook_format.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import dpd_playbook_extract as X  # noqa: E402
import dpd_siftmap_manifest as M  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {label}: {'ok' if ok else 'FAIL'}{('  ' + detail) if detail and not ok else ''}")
    if not ok:
        failures.append(label)


# ── 1. parse_ladder on a synthetic Work Order sheet ──────────────────
print("\n[1] parse_ladder")
H = tuple(X.LADDER_HEADER)
sheet = [
    H,
    (0, "County baseline: every single-family home", None, None, 62.9, 1, 771, 117500, 100, 48530, None),
    ("Priority 1", "the cheapest deals in the county, work these first", None, None, None, None, None, None, None, None, None),
    (1, "Absentee + Notice of Foreclosure", "Stack", "Starter", 1, 62.9, 24, 183500, 3.1, 24, None),
    (2, "AI score 95+", "AI score", "AI", 10.4, 6, 76, 241821, 13, 1614),          # short row: no caveat cell
    ("Priority 2", "keeps the volume coming at a fair price", None, None, None, None, None, None, None, None, None),
    (3, "Free & Clear", "List", "Starter", 89.7, 0.7, 78, 125000, 75.1, 15658, "large list: this rung is priced from a same-day sample"),
    ("3 rungs earned from 771 investor deals against 48,530 single-family homes, measured 2026-08-13. Window: 2026-01 .. 2026-06 (6 complete months).", None, None, None, None, None, None, None, None, None, None),
]
base, rows, notes = X.parse_ladder(sheet)
check("baseline row parsed", base == {"dpd": 62.9, "deals": 771, "typical_gross": 117500, "list_size": 48530}, str(base))
check("3 rungs, none None-priority", [r["priority"] for r in rows] == [1, 1, 2], str([r["priority"] for r in rows]))
check("AI rung flagged ai=True, short row survives", rows[1]["ai"] is True and rows[1]["caveats"] is None)
check("doors_added is the running difference", [r["doors_added"] for r in rows] == [24, 1590, 14044])
check("rank restarts per priority", [r["rank"] for r in rows] == [1, 2, 1])
check("signals split on ' + '", rows[0]["signals"] == ["Absentee", "Notice of Foreclosure"])
check("footnote captured", any(n.startswith("3 rungs earned") for n in notes))

# ── 2. rows_from_capture (the no-AI ladder source) ───────────────────
print("\n[2] rows_from_capture")
variant = [
    {"label": "Absentee + Notice of Foreclosure", "plan": "Starter", "keys": ["is_absentee_owners", "is_notice_of_foreclosure"],
     "ai": None, "priority": 1, "thin": False, "est": False, "list_size": 24, "marg_deals": 24, "marg_supply": 24,
     "marg_dpd": 1.0, "cum_deals": 24, "cum_share": 3.1, "cum_supply": 24, "cum_dpd": 1.0, "median_gross": 183500},
    {"label": "Free & Clear", "plan": "Starter", "keys": ["is_free_clear"], "ai": None, "priority": 2, "thin": False,
     "est": True, "list_size": 11350, "marg_deals": 78, "marg_supply": 14044, "marg_dpd": 89.7, "cum_deals": 102,
     "cum_share": 75.1, "cum_supply": 14068, "cum_dpd": 137.9, "median_gross": 125000},
]
nr = X.rows_from_capture(variant, 62.9)
check("two rungs, keys and list_size carried", nr[0]["keys"] == ["is_absentee_owners", "is_notice_of_foreclosure"] and nr[1]["list_size"] == 11350)
check("lift derived from base / marginal dpd", nr[0]["lift"] == 62.9 and nr[1]["lift"] == 0.7, str([r["lift"] for r in nr]))
check("est flag carried", nr[1]["est"] is True)

# ── 3. merge_shard: variant detection + list_size fill ───────────────
print("\n[3] merge_shard")
out = {"overview": {"Investor purchases (6mo)": 771, "Baseline doors/deal": 62.9, "SFR supply": 48530},
       "rows": [dict(r) for r in rows]}
shards = {"county-data": {"11001": {"baseline": {"list_size": 48530}}},
          "capture-data": {"11001": {"deals": 771, "base_supply": 48530, "baseline_gross": 117500,
                                     "variants": {"ai": [{"label": r["signal"], "list_size": 100 + i, "keys": ["k"],
                                                          "marg_deals": r["deals"]} for i, r in enumerate(rows)],
                                                  "lists": variant}}}}
X.merge_shard(out, shards, "11001")
check("exported ladder recognised as 'ai'", out["shard_check"]["exported_variant"] == "ai", str(out["shard_check"]))
check("cross-check ok", out["shard_check"]["status"] == "ok", str(out["shard_check"].get("mismatches")))
check("list_size filled from the shard", [r["list_size"] for r in out["rows"]] == [100, 101, 102])
check("rows_no_ai attached from the lists variant", len(out["rows_no_ai"]) == 2 and out["rows_no_ai"][0]["lift"] == 62.9)
out2 = {"overview": {"SFR supply": 40000}, "rows": [dict(r) for r in rows]}
X.merge_shard(out2, shards, "11001")
check("SFR supply mismatch is reported", out2["shard_check"]["status"] == "MISMATCH")

# ── 4. parse_xlsx refuses the retired layout ─────────────────────────
print("\n[4] parse_xlsx layout gate")
try:
    import openpyxl
    tmp = ROOT / "output" / "_test_old_layout.xlsx"
    tmp.parent.mkdir(exist_ok=True)
    wb = openpyxl.Workbook()
    wb.active.title = "Overview"
    for n in ("Priority Ranking", "Top ZIPs", "First to Market"):
        wb.create_sheet(n)
    wb.save(tmp)
    refused = False
    try:
        X.parse_xlsx(tmp)
    except SystemExit as e:
        refused = "Work Order" in str(e)
    check("old Priority Ranking export is refused, naming Work Order", refused)
    try:
        tmp.unlink()
        check("parser released the file handle", True)
    except PermissionError:
        check("parser released the file handle", False, "workbook left open (read_only needs close())")
except ImportError:
    print("  (openpyxl missing; skipped)")

# ── 5. build() uses rows_no_ai and the default priorities ───────────
print("\n[5] build")
pb = {"fips": "11001", "source_xlsx": "x.xlsx", "ladder_measured": "2026-08-13",
      "rows": [{"rung": 1, "priority": 1, "signal": "AI score 95+", "signals": ["AI score 95+"], "ai": True,
                "lift": 6, "dpd": 10.4, "deals": 76, "list_size": 1614, "typical_gross": 1, "rank": 1}],
      "rows_no_ai": [
          {"rung": 1, "priority": 1, "signal": "Absentee + Notice of Foreclosure", "signals": ["Absentee", "Notice of Foreclosure"],
           "ai": False, "lift": 62.9, "dpd": 1.0, "deals": 24, "list_size": 24, "typical_gross": 183500, "rank": 1, "doors_added": 24},
          {"rung": 2, "priority": 1, "signal": "Absentee + HOA Lien", "signals": ["Absentee", "HOA Lien"],
           "ai": False, "lift": 41.9, "dpd": 1.5, "deals": 11, "list_size": 21, "typical_gross": 190000, "rank": 2, "doors_added": 17},
          {"rung": 3, "priority": 1, "signal": "Tired Landlord", "signals": ["Tired Landlord"],
           "ai": False, "lift": 1.3, "dpd": 48.1, "deals": 14, "list_size": 2047, "typical_gross": 21945, "rank": 3, "doors_added": 900},
          {"rung": 4, "priority": 2, "signal": "Free & Clear", "signals": ["Free & Clear"],
           "ai": False, "lift": 0.7, "dpd": 89.7, "deals": 78, "list_size": 11350, "typical_gross": 125000, "rank": 1, "doors_added": 14044}],
      "top_zips": [{"zip": "20019", "stars": 5, "deals": 92}, {"zip": "20007", "stars": 3, "deals": 48}]}
m = M.build(pb)
names = [e["name"] for e in m["presets"]]
check("P1 only by default", names == ["DC P1-01 Absentee + Notice of Foreclosure", "DC P1-03 Tired Landlord (proxy)"], str(names))
check("HOA Lien routed to gaps, AI row never seen", [g["signal"] for g in m["gaps"]] == ["Absentee + HOA Lien"] and not any("AI" in n for n in names))
check("tier2 from 4-5 star ZIPs only", m["tier2"]["name"] == "DC T2 Top ZIPs 20019")
check("standalone Tired Landlord yardstick recorded", m["tired_landlord_list_size"] == 2047)
m2 = M.build(pb, priorities=(1, 2))
check("--priorities 1,2 admits the P2 rung", "DC P2-01 Free & Clear" in [e["name"] for e in m2["presets"]])
try:
    M.build({"fips": "11001", "rows": pb["rows"]})
    check("build refuses a playbook without rows_no_ai", False)
except SystemExit as e:
    check("build refuses a playbook without rows_no_ai", "rows_no_ai" in str(e))

# ── 6. bare_name / list_key ──────────────────────────────────────────
print("\n[6] names")
check("bare_name strips the glued description",
      M.bare_name("DC P1-18 Out-of-StateDoors-per-deal P1 rank 18; lift 3.7x; dpd 20.8") == "DC P1-18 Out-of-State")
check("bare_name leaves a foreign name alone", M.bare_name("6 Counties Obituary 31.8") == "6 Counties Obituary 31.8")
check("list_key ignores the rank label",
      M.list_key("DC P1-12 Free & Clear + Out-of-State") == M.list_key("DC P1-16 Free & Clear + Out-of-State"))
check("list_key ignores signal order and the proxy label",
      M.list_key("DC P1-19 Out-of-State + Tired Landlord (proxy)") == M.list_key("DC P2-03 Tired Landlord + Out-of-State"))
check("list_key separates counties",
      M.list_key("DC P1-01 Out-of-State") != M.list_key("Fairfax P1-01 Out-of-State"))
check("list_key of a T2 preset is its ZIP set",
      M.list_key("Fairfax T2 Top ZIPs 22101 22180") == M.list_key("Fairfax T2 Top ZIPs 22180 22101"))
check("list_key is None for a foreign name", M.list_key("6 Counties Obituary 31.8") is None)

# ── 7. plan() buckets ────────────────────────────────────────────────
print("\n[7] plan")
inventory = [
    {"name": "DC P1-01 Absentee + Notice of Foreclosure", "auto_add": True},     # keep (same label)
    {"name": "DC P1-07 Tired Landlord (proxy)", "auto_add": False},              # same list, new rank -> keep_old_label
    {"name": "DC P1-18 Out-of-State", "auto_add": False},                        # stale
    {"name": "DC P1-04 Notice of Foreclosure", "auto_add": True},                # stale but auto-add -> hold
    {"name": "DC P2-09 Absentee", "auto_add": False},                            # P2 -> stale
    {"name": "DC P1-30 Free & Clear + Out-of-State", "auto_add": None},          # stale, unknown auto-add -> hold
    {"name": "DC T2 Top ZIPs 20019", "auto_add": False},                         # keep
    {"name": "Fairfax P1-01 Notice of Default", "auto_add": False},              # county not in plan -> out_of_scope
    {"name": "6 Counties Obituary 31.8", "auto_add": True},                      # foreign
]
_real_read = M.build
def _fake_read(path, *a, **k):  # noqa: E306
    import json
    return json.dumps(pb)
_orig = Path.read_text
Path.read_text = lambda self, *a, **k: _fake_read(self) if "dpd_playbook_11001" in str(self) else _orig(self, *a, **k)  # type: ignore[assignment]
try:
    res = M.plan(["11001"], inventory, (1,), renames=False)
finally:
    Path.read_text = _orig  # type: ignore[assignment]
by = {r["name"]: r["action"] for r in res["rows"]}
check("same label -> keep", by["DC P1-01 Absentee + Notice of Foreclosure"] == "keep")
check("same list, new rank -> keep_old_label", by["DC P1-07 Tired Landlord (proxy)"] == "keep_old_label", by["DC P1-07 Tired Landlord (proxy)"])
check("unranked, auto-add off -> stale", by["DC P1-18 Out-of-State"] == "stale")
check("unranked, auto-add ON -> hold", by["DC P1-04 Notice of Foreclosure"] == "hold")
check("unranked, auto-add unknown -> hold", by["DC P1-30 Free & Clear + Out-of-State"] == "hold")
check("P2 outside the plan -> stale (Basem 2026-09-24: no P2 filters)", by["DC P2-09 Absentee"] == "stale")
check("other county -> out_of_scope", by["Fairfax P1-01 Notice of Default"] == "out_of_scope")
check("foreign name -> foreign", by["6 Counties Obituary 31.8"] == "foreign")
check("T2 with same ZIPs -> keep", by["DC T2 Top ZIPs 20019"] == "keep")
check("nothing is missing (every current list is covered under some label)", res["missing"] == [], str(res["missing"]))
deletable = {r["name"] for r in res["rows"] if r["action"] in ("stale", "rename", "duplicate")}
check("only the stale P1 and the P2 filter are deletable",
      deletable == {"DC P1-18 Out-of-State", "DC P2-09 Absentee"}, str(deletable))
Path.read_text = lambda self, *a, **k: _fake_read(self) if "dpd_playbook_11001" in str(self) else _orig(self, *a, **k)  # type: ignore[assignment]
try:
    res2 = M.plan(["11001"], inventory, (1,), renames=True)
finally:
    Path.read_text = _orig  # type: ignore[assignment]
by2 = {r["name"]: r["action"] for r in res2["rows"]}
check("--renames turns keep_old_label into rename", by2["DC P1-07 Tired Landlord (proxy)"] == "rename")
check("--renames re-lists the new label as missing", [x["name"] for x in res2["missing"]] == ["DC P1-03 Tired Landlord (proxy)"], str(res2["missing"]))
check("--renames never renames an auto-add filter", by2["DC P1-01 Absentee + Notice of Foreclosure"] == "keep")

print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILURES:"))
for f in failures:
    print("  -", f)
raise SystemExit(1 if failures else 0)
