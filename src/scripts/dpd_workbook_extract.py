"""Extract the doors-per-deal workbooks into machine-readable registries.

Source: the three `county-compare-*.xlsx` files in the parent Galal Development
folder (research artifacts, outside this repo).

Two outputs:
    data/dpd_signal_rankings.json   every ranked signal/stack per jurisdiction
    data/ftm_sources_mddcva.csv     the First to Market office rows (Phase 6)

Why extract every ranked row and not just the top one: the plan names a single
Priority-1 stack per county, but the workbooks list SEVERAL at the same lift.
DC has three at 76.8x (Absentee + Notice of Foreclosure, Absentee + HOA Lien,
Free & Clear + Notice of Foreclosure). That matters because 10 SiftMap signals
have no URL parameter - a county whose named stack is unbuildable may have an
equally strong alternate that is buildable, and you cannot see that from the
single row quoted in the plan.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dpd.jurisdictions import JURISDICTIONS, resolve  # noqa: E402

WORKBOOK_DIR = Path("..").resolve()
DATA_DIR = Path("data")

RANK_HEADER = ["Priority", "Signal", "County", "Type", "Evidence",
               "Doors/Deal", "Lift vs Baseline", "Deals (6mo)", "List Size"]
# All 12 columns of the sheet. The first cut listed only 9, so `Updates`
# (the publishing cadence), `Verified?` and `Notes` never reached the CSV --
# and the cadence then looked like something that needed fresh research when
# it had been sitting in the workbooks the whole time (found 2026-08-28).
FTM_HEADER = ["County", "Priority", "Data Type", "Office / Official", "Phone",
              "Source URL", "Office Address", "Records / FOIA", "Access",
              "Updates", "Verified?", "Notes"]


def _find_workbooks() -> list[Path]:
    books = sorted(WORKBOOK_DIR.glob("county-compare-*doors-per-deal-community.xlsx"))
    if not books:
        raise SystemExit(
            "No county-compare-*.xlsx found in %s - these are research artifacts "
            "that live outside the repo." % WORKBOOK_DIR
        )
    return books


def _rows_after_header(ws, header: list[str]) -> list[dict]:
    """Yield dict rows from the first row that matches `header`."""
    rows, cols = [], None
    for raw in ws.iter_rows(values_only=True):
        vals = ["" if v is None else str(v).strip() for v in raw]
        if cols is None:
            if vals[:len(header)] == header:
                cols = header
            continue
        if not any(vals):
            continue
        rows.append({c: (vals[i] if i < len(vals) else "") for i, c in enumerate(cols)})
    return rows


def _num(text: str):
    if text in ("", None):
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(text))
    if cleaned in ("", "-", "."):
        return None
    try:
        return float(cleaned) if "." in cleaned else int(cleaned)
    except ValueError:
        return None


def _split_signals(signal: str) -> list[str]:
    return [p.strip() for p in signal.split("+") if p.strip()]


def extract_rankings(books: list[Path]) -> dict:
    by_juris: dict[str, list] = {j.name: [] for j in JURISDICTIONS}
    unmatched: dict[str, int] = {}

    for book in books:
        wb = openpyxl.load_workbook(book, read_only=True, data_only=True)
        if "Combined Ranking" not in wb.sheetnames:
            continue
        for r in _rows_after_header(wb["Combined Ranking"], RANK_HEADER):
            juris = resolve(r["County"])
            if juris is None:
                unmatched[r["County"]] = unmatched.get(r["County"], 0) + 1
                continue
            by_juris[juris.name].append({
                "priority": _num(r["Priority"]),
                "signal": r["Signal"],
                "signals": _split_signals(r["Signal"]),
                "type": r["Type"],
                "evidence": r["Evidence"],
                "doors_per_deal": _num(r["Doors/Deal"]),
                "lift": _num(r["Lift vs Baseline"]),
                "deals_6mo": _num(r["Deals (6mo)"]),
                "list_size": _num(r["List Size"]),
                "source_workbook": book.name,
            })
        wb.close()

    for name, rows in by_juris.items():
        rows.sort(key=lambda x: (x["priority"] if x["priority"] is not None else 99,
                                 -(x["lift"] or 0)))

    # The distinct atomic signals the workbooks reference at all - this is the
    # vocabulary Phase 2's parameter discovery has to cover.
    vocab: dict[str, int] = {}
    for rows in by_juris.values():
        for row in rows:
            for s in row["signals"]:
                vocab[s] = vocab.get(s, 0) + 1

    return {"by_jurisdiction": by_juris,
            "signal_vocabulary": dict(sorted(vocab.items(), key=lambda kv: -kv[1])),
            "unmatched_county_strings": unmatched,
            "source_workbooks": [b.name for b in books]}


# The sheet interleaves narrative rows into the County column: a per-county
# "Data gaps to know about: ..." paragraph and one how-to-read preamble. They
# are not source rows, but the gap paragraphs are the workbook's own statement
# of what SiftMap cannot supply per county, so they are parsed, not discarded.
GAP_RE = re.compile(
    r"low or zero in (?P<county>.+?) for: (?P<missing>.+?)\.",
    re.IGNORECASE,
)
ALSO_RE = re.compile(r"Also not in SiftMap here: (?P<missing>.+?)\.", re.IGNORECASE)


def _parse_gap_note(text: str) -> dict | None:
    m = GAP_RE.search(text)
    if not m:
        return None
    raw_county = m.group("county").strip()
    # "Baltimore County, MD" -> "Baltimore, MD"; "District of Columbia County, DC"
    normalised = raw_county.replace(" County,", ",")
    juris = resolve(normalised) or resolve(raw_county)
    low_or_zero = [s.strip() for s in m.group("missing").split(",") if s.strip()]
    also = ALSO_RE.search(text)
    absent = ([s.strip() for s in also.group("missing").split(",") if s.strip()]
              if also else [])
    return {
        "jurisdiction_key": juris.key if juris else "",
        "jurisdiction": juris.name if juris else raw_county,
        "county_raw": raw_county,
        "siftmap_low_or_zero": low_or_zero,
        "siftmap_absent": absent,
    }


def extract_ftm(books: list[Path]) -> tuple[list[dict], list[dict]]:
    seen, out, gaps = set(), [], []
    for book in books:
        wb = openpyxl.load_workbook(book, read_only=True, data_only=True)
        if "First to Market" not in wb.sheetnames:
            continue
        for r in _rows_after_header(wb["First to Market"], FTM_HEADER):
            # Narrative rows carry a paragraph in the County cell and nothing
            # in Data Type; treating them as sources inflated the jurisdiction
            # count from 14 to 29.
            if len(r["County"]) > 60 or not r["Data Type"]:
                note = _parse_gap_note(r["County"])
                if note:
                    gaps.append(note)
                continue
            juris = resolve(r["County"]) or resolve(r["County"].replace(" County", ""))
            key = (r["County"], r["Data Type"], r["Office / Official"], r["Source URL"])
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "jurisdiction_key": juris.key if juris else "",
                "jurisdiction": juris.name if juris else r["County"],
                "county_raw": r["County"],
                "priority": r["Priority"],
                "data_type": r["Data Type"],
                "office": r["Office / Official"],
                "phone": r["Phone"],
                "source_url": r["Source URL"],
                "office_address": r["Office Address"],
                "records_foia": r["Records / FOIA"],
                "access": r["Access"],
                "updates": r["Updates"],
                "verified": r["Verified?"],
                "notes": r["Notes"],
                "source_workbook": book.name,
            })
        wb.close()

    # de-dupe gap notes by jurisdiction (a county appears in one workbook only)
    by_juris = {}
    for g in gaps:
        by_juris.setdefault(g["jurisdiction"], g)
    return out, sorted(by_juris.values(), key=lambda g: g["jurisdiction"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract the doors-per-deal workbooks")
    ap.add_argument("--rankings", action="store_true")
    ap.add_argument("--ftm", action="store_true")
    args = ap.parse_args()
    if not (args.rankings or args.ftm):
        args.rankings = args.ftm = True

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    books = _find_workbooks()
    print("Workbooks: %s" % ", ".join(b.name[:40] for b in books))

    if args.rankings:
        data = extract_rankings(books)
        path = DATA_DIR / "dpd_signal_rankings.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print("\n%-26s %5s %5s  top Priority-1 stacks (lift, list)" %
              ("Jurisdiction", "rows", "P1"))
        print("-" * 96)
        for name, rows in data["by_jurisdiction"].items():
            p1 = [r for r in rows if r["priority"] == 1]
            top = "; ".join("%s (%sx, %s)" % (r["signal"], r["lift"], r["list_size"])
                            for r in p1[:2]) or "(no Priority-1 row)"
            print("%-26s %5d %5d  %s" % (name, len(rows), len(p1), top[:60]))
        if data["unmatched_county_strings"]:
            print("\nUnmatched county strings:", data["unmatched_county_strings"])
        print("\nSignal vocabulary (%d distinct): %s" % (
            len(data["signal_vocabulary"]), ", ".join(data["signal_vocabulary"])))
        print("Wrote", path)

    if args.ftm:
        rows, gaps = extract_ftm(books)
        path = DATA_DIR / "ftm_sources_mddcva.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        types = {}
        for r in rows:
            types[r["data_type"]] = types.get(r["data_type"], 0) + 1
        unresolved = [r for r in rows if not r["jurisdiction_key"]]
        print("\nFTM source rows: %d across %d jurisdictions, %d data types" % (
            len(rows), len({r["jurisdiction"] for r in rows}), len(types)))
        if unresolved:
            print("  UNRESOLVED county strings: %d" % len(unresolved))
        print("Wrote", path)

        gpath = DATA_DIR / "dpd_siftmap_coverage_gaps.json"
        gpath.write_text(json.dumps(gaps, indent=2), encoding="utf-8")
        print("\nSiftMap coverage gaps stated by the workbooks (%d jurisdictions):"
              % len(gaps))
        for g in gaps:
            print("  %-26s low/zero: %-46s absent: %s" % (
                g["jurisdiction"], ", ".join(g["siftmap_low_or_zero"])[:46],
                ", ".join(g["siftmap_absent"])[:44]))
        print("Wrote", gpath)


if __name__ == "__main__":
    main()
