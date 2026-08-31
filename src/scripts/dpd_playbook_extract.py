"""Extract one jurisdiction's doors-per-deal playbook data into a machine-readable file.

    python src/scripts/dpd_playbook_extract.py --fips 11001 \
        --xlsx "<downloads>/11001-District-of-Columbia-DC-doors-per-deal.xlsx" --fetch

Two sources, same data, either or both:

  * The SINGLE-COUNTY workbook exported from learn.datasift.ai/county-list-playbook#<fips>
    (sheets Overview / Priority Ranking / AI Score / Top ZIPs / Price Bands / First to
    Market / Benchmarks / Read Me). This is NOT the county-compare format that
    dpd_workbook_extract.py reads -- different sheet names, different header, no County
    column -- so it gets its own parser rather than bending that one.
  * The playbook page's own JSON shards, free and unauthenticated, keyed by the 2-digit
    state FIPS: /county-data/<st>.json, /ftm-data/<st>.json, /capture-data/<st>.json.
    Each holds every county in that state under its 5-digit FIPS. `--fetch` downloads
    them; with a workbook present they are a CROSS-CHECK (baseline numbers and row
    count must agree) and the source of each row's stable signal `keys`
    (is_absentee_owners, is_notice_of_foreclosure ...). Without a workbook they are the
    source.

Writes data/dpd_playbook_<fips>.json. Read-only against everything.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dpd.jurisdictions import BY_FIPS  # noqa: E402

PLAYBOOK = "https://learn.datasift.ai"
SHARDS = ("county-data", "ftm-data", "capture-data")

RANK_HEADER = ["Priority", "Signal", "Type", "Plan", "Evidence", "Doors/Deal",
               "Lift vs Baseline", "Deals (6mo)", "Deal Share %", "Coverage",
               "List Size", "Typical Gross", "Caveats"]
ZIP_HEADER = ["ZIP", "Rating (1-5)", "Deals (6mo)", "Typical Gross", "Doors/Deal", "Momentum"]
FTM_HEADER = ["Priority", "Data Type", "Office / Official", "Phone", "Source URL",
              "Office Address", "Records / FOIA", "Access", "Updates", "Verified?", "Notes"]
FTM_KEYS = ["priority", "data_type", "office_official", "phone", "source_url",
            "office_address", "records_foia", "access", "updates", "verified", "notes"]


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, str) and v.strip().lower() in ("n/a", "-", ""):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _rows(ws) -> list[tuple]:
    return [tuple(r) for r in ws.iter_rows(values_only=True)]


def _c(r: tuple, i: int):
    """Cell i, or None past the end of the row.

    openpyxl read_only mode TRIMS trailing empty cells, so a row whose last columns are
    blank comes back short. Fredericksburg City's AI Score sheet holds ('80-90', 0, 0) --
    a zero-deal band with no Typical Gross -- and a bare r[3] raised IndexError on it. Any
    county with an empty band or a truncated row hits the same thing, so every fixed-index
    read of a data row goes through here.
    """
    return r[i] if i < len(r) else None


def _after_header(rows: list[tuple], header: list[str]) -> list[tuple]:
    for i, r in enumerate(rows):
        cells = [str(c).strip() if c is not None else "" for c in r[:len(header)]]
        if cells == header:
            return [x for x in rows[i + 1:] if any(c not in (None, "") for c in x)]
    raise SystemExit(f"header not found: {header[:3]}...")


def parse_xlsx(path: Path) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    need = {"Overview", "Priority Ranking", "Top ZIPs", "First to Market"}
    missing = need - set(wb.sheetnames)
    if missing:
        raise SystemExit(f"{path.name} lacks sheets {sorted(missing)}; is this the "
                         "single-county export? (county-compare files are not accepted)")

    out: dict = {"source_xlsx": path.name}

    # Overview: title row + 2-column facts.
    ov = _rows(wb["Overview"])
    out["title"] = str(ov[0][0]) if ov and ov[0] else None
    m = re.search(r"FIPS\s+(\d{5})", out["title"] or "")
    out["fips_in_title"] = m.group(1) if m else None
    out["overview"] = {str(_c(r, 0)).strip(): _c(r, 1) for r in ov
                       if len(r) >= 2 and _c(r, 0) not in (None, "") and _c(r, 1) not in (None, "")}

    # Priority Ranking.
    rows = []
    for r in _after_header(_rows(wb["Priority Ranking"]), RANK_HEADER):
        r = tuple(r) + (None,) * (len(RANK_HEADER) - len(r))
        rows.append({
            "priority": int(_c(r, 0)) if _num(_c(r, 0)) is not None else None,
            "signal": str(_c(r, 1)).strip(),
            "type": _c(r, 2), "plan": _c(r, 3), "evidence": _c(r, 4),
            "dpd": _num(_c(r, 5)), "lift": _num(_c(r, 6)), "deals": _num(_c(r, 7)),
            "deal_share_pct": _num(_c(r, 8)), "coverage": _c(r, 9),
            "list_size": _num(_c(r, 10)), "typical_gross": _num(_c(r, 11)),
            "caveats": (str(_c(r, 12)).strip() if _c(r, 12) else None),
        })
    # Rank within priority, in the workbook's own order (it is sorted by doors/deal).
    counters: dict = {}
    for row in rows:
        p = row["priority"]
        counters[p] = counters.get(p, 0) + 1
        row["rank"] = counters[p]
        row["signals"] = [s.strip() for s in row["signal"].split(" + ")]
    out["rows"] = rows

    # AI Score (optional sheet).
    if "AI Score" in wb.sheetnames:
        cut, bands, mode = [], [], None
        for r in _rows(wb["AI Score"]):
            if not r or _c(r, 0) in (None, ""):
                continue
            head = str(_c(r, 0)).strip()
            if head == "Cutoff":
                mode = "cut"
                continue
            if head == "Score Band":
                mode = "band"
                continue
            if mode == "cut" and head.startswith("AI"):
                cut.append({"cutoff": head, "deals": _num(_c(r, 1)), "coverage_pct": _num(_c(r, 2)),
                            "dpd": _num(_c(r, 3)), "typical_gross": _num(_c(r, 4))})
            elif mode == "band" and re.match(r"^\d+-\d+$", head):
                bands.append({"band": head, "deals": _num(_c(r, 1)), "coverage_pct": _num(_c(r, 2)),
                              "typical_gross": _num(_c(r, 3))})
        out["ai_score"] = {"cutoffs": cut, "bands": bands}

    # Top ZIPs.
    zips = []
    for r in _after_header(_rows(wb["Top ZIPs"]), ZIP_HEADER):
        zips.append({"zip": str(_c(r, 0)).strip(), "stars": int(_num(_c(r, 1)) or 0),
                     "deals": _num(_c(r, 2)), "typical_gross": _num(_c(r, 3)),
                     "dpd": _num(_c(r, 4)), "momentum": _num(_c(r, 5))})
    out["top_zips"] = zips

    if "Price Bands" in wb.sheetnames:
        pb = _rows(wb["Price Bands"])
        out["price_bands"] = [{"band": _c(r, 0), "deals": _num(_c(r, 1)),
                               "live_sfr_supply": _num(_c(r, 2)), "dpd": _num(_c(r, 3))}
                              for r in pb[1:]
                              if r and _c(r, 0) and len(r) >= 3 and _num(_c(r, 1)) is not None]

    # First to Market: 11 columns, then narrative rows (gap paragraph, how-to-read).
    ftm_rows, narrative = [], []
    for r in _after_header(_rows(wb["First to Market"]), FTM_HEADER):
        r = tuple(r) + (None,) * (len(FTM_HEADER) - len(r))
        if _c(r, 1) in (None, "") and _c(r, 0):
            narrative.append(str(_c(r, 0)).strip())
            continue
        ftm_rows.append({k: (str(v).strip() if v is not None else None)
                         for k, v in zip(FTM_KEYS, r)})
    out["ftm"] = ftm_rows
    out["ftm_narrative"] = narrative
    gap = next((n for n in narrative if n.startswith("Data gaps")), "")
    low = re.search(r"for:\s*(.+?)\.\s", gap + " ")
    absent = re.search(r"Also not in SiftMap here:\s*(.+?)\.?$", gap)
    out["siftmap_gaps"] = {
        "low_or_zero": [s.strip() for s in low.group(1).split(",")] if low else [],
        "absent": [s.strip() for s in absent.group(1).split(",")] if absent else [],
        "verbatim": gap or None,
    }

    if "Read Me" in wb.sheetnames:
        out["read_me"] = {str(_c(r, 0)).strip(): str(_c(r, 1)).strip()
                          for r in _rows(wb["Read Me"]) if len(r) >= 2 and _c(r, 0) and _c(r, 1)}
    return out


def fetch_shards(state_fips2: str, dest: Path) -> dict:
    got = {}
    for k in SHARDS:
        url = f"{PLAYBOOK}/{k}/{state_fips2}.json"
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
            got[k] = json.loads(resp.read().decode("utf-8"))
    dest.write_text(json.dumps(got, indent=1), encoding="utf-8")
    return got


def _dpd_key(x: dict):
    return (x.get("priority") or 9, x.get("dpd") if x.get("dpd") is not None else 1e9)


def rows_from_shard(c: dict) -> list[dict]:
    rows = []
    counters: dict = {}
    type_map = {"combo": "Stack", "list": "List", "ai": "AI score"}
    plan_map = {"all": "All plans", "pro": "Expert plan", "ai": "AI plan"}
    for r in sorted(c.get("rows") or [], key=_dpd_key):
        p = r.get("priority")
        counters[p] = counters.get(p, 0) + 1
        share = r.get("share")
        rows.append({
            "priority": p, "rank": counters[p], "signal": r.get("seg"),
            "signals": [s.strip() for s in (r.get("seg") or "").split(" + ")],
            "keys": r.get("keys"), "type": type_map.get(r.get("type"), r.get("type")),
            "plan": plan_map.get(r.get("plan"), r.get("plan")), "evidence": r.get("evidence"),
            "dpd": r.get("dpd"), "lift": r.get("lift"), "deals": r.get("deals"),
            "deal_share_pct": (round(share * 100, 1) if share is not None else None),
            "coverage": r.get("cov"), "list_size": r.get("list_size"),
            "typical_gross": r.get("median_gross"),
            "caveats": "; ".join(r.get("caveats") or []) or None,
            "verify": r.get("verify"), "verify_reason": r.get("verify_reason"),
            "conf": r.get("conf"),
        })
    return rows


def merge_shard(out: dict, shards: dict, fips: str) -> None:
    c = (shards.get("county-data") or {}).get(fips)
    if not c:
        out["shard_check"] = {"status": "county not in shard"}
        return
    check: dict = {"status": "ok", "mismatches": []}
    b = c.get("baseline") or {}
    ov = out.get("overview") or {}
    pairs = [("Investor purchases (6mo)", b.get("deals")),
             ("Baseline doors/deal (whole county)", b.get("dpd")),
             ("County SFR supply", b.get("list_size")),
             ("Typical built-in equity (median gross)", b.get("median_gross"))]
    for label, sv in pairs:
        wv = _num(ov.get(label))
        if wv is not None and sv is not None and abs(float(wv) - float(sv)) > 0.05:
            check["mismatches"].append(f"{label}: workbook {wv} vs shard {sv}")
    shard_rows = rows_from_shard(c)
    by_sig = {r["signal"]: r for r in shard_rows}
    wb_sigs = {x["signal"] for x in out.get("rows") or []}
    matched = 0
    for row in out.get("rows") or []:
        s = by_sig.get(row["signal"])
        if s:
            matched += 1
            row["keys"] = s.get("keys")
            row["verify"] = s.get("verify")
            row["verify_reason"] = s.get("verify_reason")
            row["conf"] = s.get("conf")
    check["workbook_rows"] = len(out.get("rows") or [])
    check["shard_rows"] = len(shard_rows)
    check["rows_matched_by_signal"] = matched
    check["workbook_only"] = [s for s in wb_sigs if s not in by_sig]
    check["shard_only"] = [r["signal"] for r in shard_rows if r["signal"] not in wb_sigs]
    if check["mismatches"]:
        check["status"] = "MISMATCH"
    out["shard_check"] = check
    ladder = ((shards.get("capture-data") or {}).get(fips) or {})
    out["shard_extra"] = {
        "coverage": c.get("coverage"), "obit_supply": c.get("obit_supply"),
        "ai_demand": c.get("ai_demand"), "ftm_verdicts": c.get("ftm"),
        "zips_detail": c.get("zips"), "trend": c.get("trend"),
        "capture_ladder": ladder,
    }


def from_shard_only(shards: dict, fips: str) -> dict:
    c = (shards.get("county-data") or {}).get(fips)
    if not c:
        raise SystemExit(f"FIPS {fips} not in the county-data shard")
    f = ((shards.get("ftm-data") or {}).get(fips) or {}).get("rows") or []
    band_map = {"A": "Pull first (A)", "B": "Pull next (B)", "C": "Pull last (C)"}
    b = c.get("baseline") or {}
    return {
        "source_xlsx": None,
        "title": f"{c.get('name')}, {c.get('state')} (FIPS {fips})",
        "overview": {"Investor purchases (6mo)": b.get("deals"),
                     "Baseline doors/deal (whole county)": b.get("dpd"),
                     "County SFR supply": b.get("list_size"),
                     "Typical built-in equity (median gross)": b.get("median_gross"),
                     "Foreclosure regime": "judicial" if c.get("judicial") else "non-judicial",
                     "Data window": c.get("window")},
        "rows": rows_from_shard(c),
        "top_zips": [{"zip": z["zip"], "stars": z.get("stars"), "deals": z.get("deals"),
                      "typical_gross": z.get("median_gross"), "dpd": z.get("dpd"),
                      "momentum": z.get("momentum")} for z in (c.get("zips") or [])],
        "price_bands": c.get("price_bands"),
        "ftm": [{"priority": band_map.get(r.get("p"), r.get("p")), "data_type": r.get("t"),
                 "office_official": r.get("o"), "phone": r.get("ph"), "source_url": r.get("u"),
                 "office_address": r.get("a"), "records_foia": r.get("foia"),
                 "access": r.get("d"), "updates": r.get("c"),
                 "verified": "Verified" if r.get("v") else "Verify locally", "notes": r.get("n")}
                for r in f],
        "ftm_narrative": [],
        "siftmap_gaps": {"from_shard_ftm_verdicts": c.get("ftm")},
        # Same block merge_shard() attaches on the workbook path: the shard carries more
        # than the workbook's sheets do (per-ZIP signal lift, the capture ladder), and
        # without this the shard-only counties come out thinner than the workbook ones.
        "shard_extra": {
            "coverage": c.get("coverage"), "obit_supply": c.get("obit_supply"),
            "ai_demand": c.get("ai_demand"), "ftm_verdicts": c.get("ftm"),
            "zips_detail": c.get("zips"), "trend": c.get("trend"),
            "capture_ladder": ((shards.get("capture-data") or {}).get(fips) or {}),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract a county's doors-per-deal playbook data")
    ap.add_argument("--fips", required=True, help="5-digit county FIPS, e.g. 11001")
    ap.add_argument("--xlsx", help="the single-county workbook exported from the playbook page")
    ap.add_argument("--fetch", action="store_true",
                    help="download the playbook's free JSON shards (cross-check, or the source)")
    ap.add_argument("--out")
    a = ap.parse_args()

    j = BY_FIPS.get(a.fips)
    if not j:
        raise SystemExit(f"FIPS {a.fips} is not one of the 14 target jurisdictions "
                         "(src/dpd/jurisdictions.py)")
    out_path = Path(a.out) if a.out else ROOT / "data" / f"dpd_playbook_{a.fips}.json"
    shard_path = ROOT / "data" / f"dpd_playbook_{a.fips}_shards.json"

    shards = None
    if a.fetch:
        shards = fetch_shards(a.fips[:2], shard_path)
        print(f"fetched {', '.join(SHARDS)} for state {a.fips[:2]} -> {shard_path.name}")

    if a.xlsx:
        out = parse_xlsx(Path(a.xlsx))
        if out.get("fips_in_title") and out["fips_in_title"] != a.fips:
            raise SystemExit(f"workbook is for FIPS {out['fips_in_title']}, not {a.fips}")
        if shards:
            merge_shard(out, shards, a.fips)
    elif shards:
        out = from_shard_only(shards, a.fips)
    else:
        raise SystemExit("give --xlsx and/or --fetch")

    out.update({"fips": a.fips, "jurisdiction": j.name, "state_abbr": j.state_abbr,
                "extracted_at": datetime.now().isoformat(timespec="seconds")})
    out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")

    rows = out["rows"]
    by_p: dict = {}
    for r in rows:
        by_p[r["priority"]] = by_p.get(r["priority"], 0) + 1
    print(f"{j.name} ({a.fips}): {len(rows)} ranked rows "
          f"{{P1 {by_p.get(1, 0)}, P2 {by_p.get(2, 0)}, P3 {by_p.get(3, 0)}}}, "
          f"{len(out.get('top_zips') or [])} rated ZIPs, {len(out.get('ftm') or [])} FTM rows")
    sc = out.get("shard_check")
    if sc:
        print(f"shard cross-check: {sc['status']}  rows matched "
              f"{sc.get('rows_matched_by_signal')}/{sc.get('workbook_rows')}  "
              f"workbook-only {sc.get('workbook_only')}  shard-only {sc.get('shard_only')}")
        for m in sc.get("mismatches") or []:
            print("  MISMATCH", m)
    print(f"wrote {out_path}")
    return 1 if (sc and sc["status"] != "ok") else 0


if __name__ == "__main__":
    raise SystemExit(main())
