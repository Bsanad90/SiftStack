"""Synthesise the six per-type notice-publication research artifacts into one table.

    python src/scripts/dpd_notice_publication_report.py

Reads (each optional; a missing artifact is STATED in the report, never invented):
    data/dpd_notice_publication_<type>.json   one per notice type, written by the
                                              research agents (2026-08-28)
    data/dpd_ftm_registry.json                the build verdict per cell (which
                                              scraper, if any, covers it today)
    data/dpd_siftmap_coverage_gaps.json       the provider gap per jurisdiction

Writes:
    data/dpd_notice_publication.json          merged; merge-not-replace, so a re-run
                                              of one type never wipes the other five
    output/dpd_notice_publication.md          the summary table + per-type detail

Read-only against every input. Exit 0 unless NO type artifact exists at all.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dpd.jurisdictions import JURISDICTIONS, BY_KEY  # noqa: E402

DATA, OUT = ROOT / "data", ROOT / "output"
MERGED = DATA / "dpd_notice_publication.json"
REPORT = OUT / "dpd_notice_publication.md"

# The user's six, in the order asked, using dpd_ftm_registry.CANON strings.
TYPES = [
    ("foreclosure", "Foreclosure"),
    ("probate", "Probate"),
    ("tax_sale", "Tax sale"),
    ("tax_delinquent", "Tax delinquency"),
    ("eviction", "Eviction"),
    ("code_violation", "Code violation"),
]

ENUMS = {
    "primary_source_type": {"portal", "newspaper", "newspaper_aggregator", "court_portal", "county_pdf",
                            "open_data", "courthouse_only", "foia_only"},
    "url_status": {"ok", "redirect", "404", "blocked", "not_checked"},
    "scrapable": {"yes", "yes_with_credential", "partial", "no"},
    "access_mechanics": {"open_html", "json_or_arcgis_api", "pdf_download", "login_free", "login_paid",
                         "captcha_or_waf", "foia_only", "in_person"},
    "bulk_export": {"yes", "no", "unknown"},
    "date_range_query": {"yes", "no", "unknown"},
    "terms_restrict_automation": {"yes", "no", "unknown"},
    "cadence_source": {"workbook", "site", "inferred"},
    "verified": {"live_probe", "workbook_only", "unreachable"},
}

CHANNEL_SHORT = {
    "portal": "portal", "newspaper": "newspaper", "newspaper_aggregator": "notice aggregator",
    "court_portal": "court portal", "county_pdf": "county PDF", "open_data": "open data",
    "courthouse_only": "courthouse only", "foia_only": "FOIA only",
}
SCRAPABLE_SHORT = {"yes": "YES", "yes_with_credential": "yes w/ login", "partial": "partial", "no": "NO"}
MECH_SHORT = {
    "open_html": "open HTML", "json_or_arcgis_api": "JSON/ArcGIS API", "pdf_download": "PDF",
    "login_free": "free login", "login_paid": "paid login", "captcha_or_waf": "CAPTCHA/WAF",
    "foia_only": "FOIA", "in_person": "in person",
}
VERDICT_SHORT = {
    "covered_by_scraper": "scraper runs", "existing_scraper_never_run": "scraper built, never run",
    "covered_by_provider": "SiftMap covers", "external_feed": "external feed",
    "researched_needs_new_scraper": "needs scraper", "unsourced": "unsourced",
}


def _load(p: Path):
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  !! {p.name}: invalid JSON ({e})")
        return None


def _validate(t: str, art: dict) -> list[str]:
    """Schema problems, as human sentences. Never raises."""
    probs = []
    js = art.get("jurisdictions") or {}
    missing = [j.key for j in JURISDICTIONS if j.key not in js]
    extra = [k for k in js if k not in BY_KEY]
    if missing:
        probs.append(f"{t}: {len(missing)} jurisdiction(s) missing: {', '.join(missing)}")
    if extra:
        probs.append(f"{t}: unknown jurisdiction key(s) ignored: {', '.join(extra)}")
    for k, rec in js.items():
        for field, allowed in ENUMS.items():
            v = rec.get(field)
            if v not in allowed:
                probs.append(f"{t}/{k}: {field}={v!r} not in {sorted(allowed)}")
    return probs


def _short(s: str | None, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _cell(rec: dict | None) -> str:
    if rec is None:
        return "(not researched)"
    ch = CHANNEL_SHORT.get(rec.get("primary_source_type"), rec.get("primary_source_type") or "?")
    sc = SCRAPABLE_SHORT.get(rec.get("scrapable"), rec.get("scrapable") or "?")
    cad = _short(rec.get("cadence") or "?", 22)
    flag = "" if rec.get("verified") == "live_probe" else (" ‡" if rec.get("verified") == "unreachable" else " †")
    return f"{ch} / {sc} / {cad}{flag}"


def main() -> int:
    arts: dict[str, dict] = {}
    missing_types: list[str] = []
    problems: list[str] = []
    for t, _ in TYPES:
        a = _load(DATA / f"dpd_notice_publication_{t}.json")
        if a is None:
            missing_types.append(t)
            continue
        arts[t] = a
        problems += _validate(t, a)
    if not arts:
        print("No data/dpd_notice_publication_<type>.json found. Nothing to synthesise.")
        return 1

    registry = _load(DATA / "dpd_ftm_registry.json")
    matrix = (registry or {}).get("matrix") or {}
    gaps = {g["jurisdiction_key"]: g for g in (_load(DATA / "dpd_siftmap_coverage_gaps.json") or [])}

    # ---- merged artifact (merge-not-replace) -------------------------------------
    prev = _load(MERGED) or {}
    merged_types = dict(prev.get("types") or {})
    for t, a in arts.items():
        merged_types[t] = {
            "source_generated_at": a.get("generated_at"),
            "method": a.get("method"),
            "jurisdictions": a.get("jurisdictions") or {},
        }
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "merged_with": prev.get("generated_at"),
        "types_present_this_run": sorted(arts),
        "types_missing_this_run": missing_types,
        "schema_problems": problems,
        "types": merged_types,
    }
    MERGED.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

    # ---- report --------------------------------------------------------------------
    L: list[str] = []
    w = L.append
    w("# How distress notices are published: 14 MD/DC/VA jurisdictions x 6 notice types")
    w("")
    w(f"_Generated {out['generated_at']} by `src/scripts/dpd_notice_publication_report.py`._")
    w("")
    w("Each cell reads **channel / scrapable / cadence**. `†` = the workbook value, not re-probed live; "
      "`‡` = the source could not be reached, workbook value kept. No account was created and no form "
      "submitted anywhere in this research. Sources: the doors-per-deal workbooks' First to Market sheets "
      "(office, URL, cadence, `Verified?`), `output/dpd_dc_ftm_investigation.md` (DC, 2026-08-27), and the "
      "six agents' live probes on 2026-08-28.")
    w("")
    if missing_types:
        w(f"**Not researched in this run (artifact missing): {', '.join(missing_types)}.** Those columns read "
          "`(not researched)` rather than being filled from the workbook.")
        w("")
    if problems:
        w(f"**{len(problems)} schema problem(s)** in the agents' artifacts (listed at the end); the affected "
          "cells still render with whatever value was written.")
        w("")

    # counts
    ver = Counter()
    scr = Counter()
    for t, a in arts.items():
        for k, rec in (a.get("jurisdictions") or {}).items():
            if k in BY_KEY:
                ver[rec.get("verified")] += 1
                scr[rec.get("scrapable")] += 1
    total = sum(ver.values())
    w(f"**Evidence quality:** {total} cells; live-probed {ver['live_probe']}, workbook-only "
      f"{ver['workbook_only']}, unreachable {ver['unreachable']}. **Scrapable:** yes {scr['yes']}, "
      f"with a credential {scr['yes_with_credential']}, partial {scr['partial']}, no {scr['no']}.")
    w("")

    # ---- summary table
    w("## Summary table")
    w("")
    w("| Jurisdiction | " + " | ".join(lbl for _, lbl in TYPES) + " |")
    w("|---|" + "---|" * len(TYPES))
    for j in JURISDICTIONS:
        cells = []
        for t, _ in TYPES:
            rec = (arts.get(t) or {}).get("jurisdictions", {}).get(j.key) if t in arts else None
            cells.append(_cell(rec))
        w(f"| **{j.name}** ({j.foreclosure_regime}) | " + " | ".join(cells) + " |")
    w("")

    # ---- per type detail
    for t, lbl in TYPES:
        w(f"## {lbl} (`{t}`)")
        w("")
        a = arts.get(t)
        if a is None:
            w(f"_No artifact: `data/dpd_notice_publication_{t}.json` was not found. Nothing is filled in "
              "from the workbook here, so that the table never presents an un-probed row as research._")
            w("")
            continue
        if a.get("method"):
            w(f"_Method: {a['method']}_")
            w("")
        w("| Jurisdiction | Primary source | URL | Scrapable | Bulk / date-range | Cadence | Verified | "
          "Existing coverage | Changed vs workbook | Notes |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        for j in JURISDICTIONS:
            rec = (a.get("jurisdictions") or {}).get(j.key)
            cell = (matrix.get(j.key) or {}).get("cells", {}).get(t) or {}
            verdict = VERDICT_SHORT.get(cell.get("verdict"), cell.get("verdict") or "-")
            sc_name = (cell.get("scraper") or {}).get("script") if isinstance(cell.get("scraper"), dict) else None
            cov = f"{verdict}" + (f" (`{sc_name}`)" if sc_name else "")
            if rec is None:
                w(f"| {j.name} | (missing from artifact) | | | | | | {cov} | | |")
                continue
            src = f"{CHANNEL_SHORT.get(rec.get('primary_source_type'), rec.get('primary_source_type'))}: " \
                  f"{rec.get('publisher') or ''}"
            url = rec.get("url") or ""
            url_md = f"[link]({url}) `{rec.get('url_status')}`" if url else f"`{rec.get('url_status')}`"
            scrap = f"{SCRAPABLE_SHORT.get(rec.get('scrapable'), rec.get('scrapable'))} " \
                    f"({MECH_SHORT.get(rec.get('access_mechanics'), rec.get('access_mechanics'))})"
            if rec.get("terms_restrict_automation") == "yes":
                scrap += " - ToS restricts automation"
            bulk = f"bulk {rec.get('bulk_export')}, date-range {rec.get('date_range_query')}"
            cad = f"{rec.get('cadence') or ''} _({rec.get('cadence_source')})_"
            sec = rec.get("secondary_sources") or []
            notes = (rec.get("notes") or "").replace("|", "/")
            if sec:
                notes += " Also: " + "; ".join(
                    f"{s.get('publisher') or s.get('type')} {('<' + s['url'] + '>') if s.get('url') else ''}"
                    for s in sec[:3])
            w(f"| {j.name} | {src} | {url_md} | {scrap} | {bulk} | {cad} | {rec.get('verified')} | {cov} | "
              f"{(rec.get('changed_vs_workbook') or '').replace('|', '/')} | {notes} |")
        w("")

    # ---- what changed vs the workbook
    w("## What the live probes changed vs the workbook")
    w("")
    changed = [(t, k, rec) for t, a in arts.items() for k, rec in (a.get("jurisdictions") or {}).items()
               if k in BY_KEY and (rec.get("changed_vs_workbook") or "").strip()]
    if changed:
        for t, k, rec in sorted(changed):
            w(f"- **{BY_KEY[k].name} / {t}**: {rec['changed_vs_workbook']}")
    else:
        w("- nothing recorded as changed")
    w("")
    w("## Unreachable during this research (workbook value kept)")
    w("")
    unr = [(t, k, rec) for t, a in arts.items() for k, rec in (a.get("jurisdictions") or {}).items()
           if k in BY_KEY and rec.get("verified") == "unreachable"]
    if unr:
        for t, k, rec in sorted(unr):
            w(f"- **{BY_KEY[k].name} / {t}**: {rec.get('url')} ({rec.get('url_status')}) - {rec.get('notes') or ''}")
    else:
        w("- none")
    w("")

    # ---- provider gap, for context
    if gaps:
        w("## SiftMap provider gaps (from the workbooks, for context)")
        w("")
        for j in JURISDICTIONS:
            g = gaps.get(j.key) or {}
            w(f"- **{j.name}**: low/zero: {', '.join(g.get('siftmap_low_or_zero') or []) or '-'}; "
              f"absent: {', '.join(g.get('siftmap_absent') or []) or '-'}")
        w("")

    if problems:
        w("## Schema problems in the agents' artifacts")
        w("")
        for p in problems:
            w(f"- {p}")
        w("")

    OUT.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L), encoding="utf-8")
    print(f"types present: {sorted(arts)}  missing: {missing_types}")
    print(f"cells {total}: live_probe {ver['live_probe']}, workbook_only {ver['workbook_only']}, "
          f"unreachable {ver['unreachable']}; schema problems {len(problems)}")
    print(f"wrote {MERGED.relative_to(ROOT)} and {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
