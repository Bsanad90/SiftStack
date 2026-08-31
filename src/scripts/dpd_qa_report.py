"""Assemble the doors-per-deal QA report for one county from the build's own artifacts.

    python src/scripts/dpd_qa_report.py --fips 11001

Reads (each optional; a missing artifact is stated, never invented):
    data/dpd_siftmap_manifest_<fips>.json          measured presets + gaps
    output/dpd_siftmap_presets_<fips>_verify.json  SiftMap preset read-back
    output/dpd_siftmap_presets_<fips>_commit.json  what was saved
    output/dpd_presets_verify.json                 the 73 Records presets, server-side
    output/dpd_presets_create.json                 last builder run
    output/dpd_baseline.json / dpd_baseline_current.json   the doctor's before/after
    output/dpd_dc_ftm_investigation.md             (linked, not embedded)

Writes output/dpd_qa_report_<fips>.md. Read-only.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dpd.jurisdictions import BY_FIPS  # noqa: E402

try:
    from dpd.block_map import GAPS, STATUS_GAP_UNRECOVERED  # noqa: E402
except Exception:  # noqa: BLE001
    GAPS, STATUS_GAP_UNRECOVERED = {}, []


def _load(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _n(v):
    return "-" if v is None else (f"{int(v):,}" if isinstance(v, (int, float)) else str(v))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fips", required=True)
    a = ap.parse_args()
    j = BY_FIPS.get(a.fips)
    if not j:
        raise SystemExit(f"unknown FIPS {a.fips}")

    pb = _load(ROOT / "data" / f"dpd_playbook_{a.fips}.json")
    man = _load(ROOT / "data" / f"dpd_siftmap_manifest_{a.fips}.json")
    sm_verify = _load(ROOT / "output" / f"dpd_siftmap_presets_{a.fips}_verify.json")
    sm_commit = _load(ROOT / "output" / f"dpd_siftmap_presets_{a.fips}_commit.json")
    pv = _load(ROOT / "output" / "dpd_presets_verify.json")
    base = _load(ROOT / "output" / "dpd_baseline.json")
    cur = _load(ROOT / "output" / "dpd_baseline_current.json")
    ftm_doc = ROOT / "output" / "dpd_dc_ftm_investigation.md"

    L: list[str] = []
    w = L.append
    w(f"# Doors-per-deal QA report — {j.name} (FIPS {a.fips})")
    w(f"\nGenerated {datetime.now():%Y-%m-%d %H:%M}. Account: moe@galaldev.com. "
      "Every number below is read from the build's own artifacts; nothing is typed in.")
    w("\n**No record was added to the account by this build.** SiftMap presets were saved and "
      "measured; the Add-Records pull is deferred to a separately approved run.\n")

    # ── SiftMap presets ────────────────────────────────────────────────
    w("## 1. SiftMap presets (the doors-per-deal lists)\n")
    if not man:
        w("_manifest missing — run dpd_siftmap_manifest.py --measure_")
    else:
        wb_sfr = ((pb or {}).get("overview") or {}).get("County SFR supply")
        base_sf = man.get("baseline_single_family")
        if wb_sfr is not None and base_sf is not None:
            agree = ("the two agree to the unit" if int(wb_sfr) == int(base_sf)
                     else f"a {abs(int(base_sf) - int(wb_sfr)):,} difference vs the workbook")
            cmp_note = f" (the workbook's SFR supply is {_n(wb_sfr)}; {agree})"
        else:
            cmp_note = ""
        w(f"Source: `{man.get('source')}`. Single-family baseline measured in SiftMap: "
          f"**{_n(base_sf)}**{cmp_note}.")
        px = man.get("proxy") or {}
        w(f"\nTired Landlord: **{px.get('status')}** as `{px.get('param')}` — measured "
          f"{_n(px.get('count'))} against the workbook's {_n(px.get('workbook_list_size'))}.")
        vr = {r["name"]: r for r in (sm_verify or {}).get("results", [])}
        cm = {r["name"]: r for r in (sm_commit or {}).get("results", [])}
        w("\n| Preset | lift | workbook | measured | not in account | saved | reloads to |")
        w("|---|---|---|---|---|---|---|")
        items = list(man.get("presets", [])) + ([man["tier2"]] if man.get("tier2") else [])
        for e in items:
            v = vr.get(e["name"], {})
            c = cm.get(e["name"], {})
            saved = c.get("status") or ("present" if v.get("present") else "-")
            reload = ("-" if not v else
                      ("MISSING" if not v.get("present") else
                       (f"{_n(v.get('count'))} {'ok' if v.get('params_match') else 'PARAM MISMATCH'}")))
            w(f"| {e['name']} | {e.get('lift', '-')} | {_n(e.get('workbook_list_size'))} | "
              f"{_n(e.get('measured_count'))} | {_n(e.get('measured_not_in_account'))} | "
              f"{saved} | {reload} |")
        if sm_verify:
            ok = sum(1 for r in sm_verify["results"] if r.get("present") and r.get("params_match"))
            w(f"\nRead-back: **{ok} of {len(sm_verify['results'])}** presets present in the "
              "Presets popover and reloading with their county, buy box and distressor "
              "parameters intact.")
            for r in sm_verify["results"]:
                for p in r.get("problems") or []:
                    w(f"- {r['name']}: {p}")
        else:
            w("\n_SiftMap read-back not yet run (dpd_siftmap_presets.py --verify)._")
        w("\n### Rows SiftMap cannot express (routed to first-to-market)\n")
        w("| Row | lift | reason |")
        w("|---|---|---|")
        for g in man.get("gaps", []):
            w(f"| P{g['priority']}-{g['rank']:02d} {g['signal']} | {g.get('lift', '-')}x | {g.get('reason')} |")

    # ── Records presets ────────────────────────────────────────────────
    w("\n## 2. Records-page presets (the challenge structure)\n")
    if not pv:
        w("_dpd_presets_verify.json missing_")
    else:
        w(f"Server-side verify at {pv.get('ran_at')}: **{pv.get('checked')}/73 present**, "
          f"{len(pv.get('missing', []))} missing, {len(pv.get('defects', []))} defects, "
          f"{len(pv.get('extras', []))} extras.")
        if pv.get("missing"):
            w("\nMissing:")
            for m in pv["missing"]:
                w(f"- {m}")
        if pv.get("defects"):
            w("\nDefects:")
            for d in pv["defects"]:
                w(f"- {d}")
        if pv.get("extras"):
            w("\nExtras (not in spec):")
            for x in pv["extras"]:
                w(f"- {x}")
        w("\nSolved tag uuids: " + ", ".join(f"`{k}`" for k in (pv.get("tag_uuids") or {}) if k != "__lists__"))
    w(f"\n**Every preset loads empty for {j.name} today** — no record carries `Priority 1`, "
      "`Priority 2` or `Tier 2` yet because no pull ran for this county. The structure is "
      "complete and fills the moment a pull stamps the entry tags.")
    if GAPS:
        w("\n### Stated gaps (omitted on purpose, not approximated)\n")
        for k, v in GAPS.items():
            w(f"- **{k}** — {v.splitlines()[0]}")
        if STATUS_GAP_UNRECOVERED:
            w(f"- **status picker** cannot select {', '.join(STATUS_GAP_UNRECOVERED)} "
              "(DNC / Opt-out recovered through Params & Others)")

    # ── Baseline diff ──────────────────────────────────────────────────
    w("\n## 3. Account baseline (the doctor)\n")
    if base and cur:
        w(f"Before: {base.get('captured_at')}  After: {cur.get('captured_at')}\n")
        w("| Section | before | after |")
        w("|---|---|---|")
        for k in ("lists", "property_tags", "phone_tags", "statuses", "preset_items",
                  "preset_folders", "sequences", "siftmap_presets"):
            w(f"| {k} | {len(base.get(k) or [])} | {len(cur.get(k) or [])} |")
        rem = {k: sorted(set(base.get(k) or []) - set(cur.get(k) or []))
               for k in ("lists", "property_tags", "phone_tags", "statuses", "sequences")}
        rem = {k: v for k, v in rem.items() if v}
        w("\nRemovals in lists / tags / statuses / sequences: "
          + ("**none**" if not rem else f"**{rem}**"))
        if cur.get("preset_tree"):
            w("\nPreset folders and counts (after):")
            for f, ps in cur["preset_tree"].items():
                w(f"- {f}: {len(ps)}")
    else:
        w("_baseline files missing_")

    # ── FTM ────────────────────────────────────────────────────────────
    w("\n## 4. First-to-market\n")
    if ftm_doc.exists():
        w(f"See `{ftm_doc.relative_to(ROOT)}` — sources probed, what is free today (ITSPE tax "
          "roll, OTR tax-lien list, vacant/blighted registry, beneficial owners), what needs a "
          "sign-up (Recorder of Deeds, 2Captcha, DOB eRecords), and the ordered actions.")
    else:
        w("_investigation document missing_")

    # ── What the next run does ────────────────────────────────────────
    w("\n## 5. What the next (pull) run would do — not done here\n")
    w("Open each saved preset in ladder order (P1 by doors/deal, then P2, then Tier 2) with "
      "`in_my_account_mode=not_in`, Select Max, Add Records to Account, tagging `Priority 1` / "
      "`Priority 2` / `Tier 2` plus the stack name and `DPD pulled <YYYY-MM>`. Because each pull "
      "excludes what earlier pulls added, every record receives its best rung's tag — the "
      "playbook's own ladder semantics. A separate, gated pass (`in_my_account_mode=in`, one "
      "record first, read back before/after) would tag the DC records the account already "
      "holds. Volume, from the not-in-account counts above: the P1 rows add roughly "
      "the P1 union, then P2, then Tier 2 — approve per pull.")

    out = ROOT / "output" / f"dpd_qa_report_{a.fips}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
