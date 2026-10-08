"""DNC scrub over dial sheets: report every number's DNC state; READ-ONLY to DataSift.

    python src/scripts/dnc_scrub.py --input output/<run>/dial_sheet.csv
    python src/scripts/dnc_scrub.py --run-dir output/<run>            # every dial_sheet*.csv under it
    python src/scripts/dnc_scrub.py --run-dir output/<run> --harvest output/<run>/tracerfy_prs.json
    python src/scripts/dnc_scrub.py --input <csv> --spend [--limit 1] # PAID, on Basem's go only

Default mode is CACHE-ONLY and spends nothing. It counts each number as dnc / clean /
stale / unchecked (rules in dnc_client) and gives a cost estimate for the numbers that
still need the vendor. Free signals are recorded before the counting:
  - a DataSift phone status containing DNC (the dial sheet's "DataSift Status" column);
  - `--harvest <json>`: any phone object with `"dnc": true` anywhere in a vendor
    result file (Tracerfy returns `phones=[{number, type, dnc, ...}]`).
  - `--sc-blocked <csv>`: Smarter Contact's per-campaign blocked-numbers export. Only rows
    whose reason names DNC / opt-out / compliance / litigator count; Carrier Blocked and
    Undeliverable are deliverability, not consent, and are listed as skipped.
All of these only ever ADD a hit.

`--spend` sends the stale + unchecked numbers to the vendor (dnc_client.scrub_remote).
`--limit N` caps that to the first N (use `--limit 1` for the probe).

Writes `dnc_review.csv` beside the first input (or `--out`). One row per (sheet, row,
number). `dial_ok` is True only for a FRESH vendor-clean number.

Exit: 0 = every number is fresh-clean or a known hit; 2 = some are stale/unchecked;
1 = error (bad input, vendor failure).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

import dnc_client  # noqa: E402

PHONE_COLS = ("number", "Phone", "phone", "Phone Number", "phone_number", "cleaned")
KEY_COLS = ("key", "Record UUID", "record_uuid", "uuid", "Street")
NAME_COLS = ("name", "Contact", "contact", "owner_name")
TAG_COLS = ("tier", "Tag", "tag")
STATUS_COLS = ("DataSift Status", "datasift_status", "status")

REVIEW_FIELDS = ["source_file", "row", "key", "contact", "number", "dnc_status",
                 "dnc_sources", "federal", "state", "litigator", "wireless", "reason",
                 "vendor_checked_at", "datasift_status", "current_tag", "dial_ok"]


def say(msg: str) -> None:
    print(msg, flush=True)


def pick(row: dict, cols) -> str:
    for c in cols:
        if row.get(c) not in (None, ""):
            return str(row[c]).strip()
    return ""


def find_sheets(run_dir: Path) -> list[Path]:
    return sorted(p for p in run_dir.rglob("dial_sheet*.csv") if p.is_file())


def read_sheet(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if rows and not any(c in rows[0] for c in PHONE_COLS):
        raise ValueError(f"{path}: no phone column (looked for {', '.join(PHONE_COLS)})")
    return rows


def harvest(obj, out: list[str]) -> None:
    """Every phone dict with dnc true, anywhere in a JSON tree."""
    if isinstance(obj, dict):
        if obj.get("dnc") is True or str(obj.get("dnc")).lower() == "true":
            n = dnc_client.clean_number(obj.get("number") or obj.get("phone") or obj.get("phone_number"))
            if n:
                out.append(n)
        for v in obj.values():
            harvest(v, out)
    elif isinstance(obj, list):
        for v in obj:
            harvest(v, out)


SC_HIT_WORDS = ("dnc", "do not call", "opt", "compliance", "litigator", "stop")
SC_REASON_COLS = ("Reason", "reason", "Status", "status", "Suppression", "List", "Label", "Type")


def read_sc_blocked(path: Path) -> tuple[list[tuple[str, str]], Counter]:
    """(number, reason) hits from a Smarter Contact blocked export; plus skipped reasons.
    A row with no reason column counts as a hit only if the file names DNC itself."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return [], Counter()
    phone_cols = [c for c in rows[0] if "phone" in c.lower() or "number" in c.lower()]
    if not phone_cols:
        raise ValueError(f"{path}: no phone column in {list(rows[0])}")
    hits, skipped = [], Counter()
    file_says_dnc = any(w in path.name.lower() for w in ("dnc", "opt", "litigator"))
    for r in rows:
        n = dnc_client.clean_number(r.get(phone_cols[0]))
        if not n:
            continue
        reason = pick(r, SC_REASON_COLS)
        if (reason and any(w in reason.lower() for w in SC_HIT_WORDS)) or (not reason and file_says_dnc):
            hits.append((n, reason or path.name))
        else:
            skipped[reason or "(no reason column)"] += 1
    return hits, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", action="append", default=[], help="dial sheet CSV (repeatable)")
    ap.add_argument("--run-dir", help="scan for dial_sheet*.csv under this directory")
    ap.add_argument("--harvest", action="append", default=[], help="vendor JSON with per-phone dnc flags")
    ap.add_argument("--sc-blocked", action="append", default=[],
                    help="Smarter Contact blocked-numbers export CSV (repeatable)")
    ap.add_argument("--out", help="review CSV path (default: beside the first input)")
    ap.add_argument("--cache", help="override output/dnc_cache.json (tests)")
    ap.add_argument("--spend", action="store_true", help="PAID: send stale+unchecked numbers to the vendor")
    ap.add_argument("--limit", type=int, default=0, help="with --spend: at most N numbers")
    a = ap.parse_args(argv)
    load_dotenv(ROOT / ".env")

    sheets = [Path(p) for p in a.input]
    if a.run_dir:
        found = find_sheets(Path(a.run_dir))
        if not found:
            say(f"ERROR: no dial_sheet*.csv under {a.run_dir}")
            return 1
        sheets += found
    if not sheets:
        say("ERROR: give --input and/or --run-dir")
        return 1

    cache_path = Path(a.cache) if a.cache else None
    cache = dnc_client.load_cache(cache_path)

    rows = []
    for sheet in sheets:
        try:
            for i, r in enumerate(read_sheet(sheet), start=2):
                n = dnc_client.clean_number(pick(r, PHONE_COLS))
                if n:
                    rows.append((sheet, i, r, n))
        except (OSError, ValueError) as exc:
            say(f"ERROR: {exc}")
            return 1
    if not rows:
        say("ERROR: zero numbers in the input -- a failure, not a clean sheet")
        return 1

    # Free signals first: they only ever add a hit.
    added = Counter()
    for _sheet, _i, r, n in rows:
        st = pick(r, STATUS_COLS).upper()
        if "DNC" in st:
            dnc_client.record(n, "datasift", True, cache, reason=st)
            added["datasift"] += 1
    for hp in a.harvest:
        hits: list[str] = []
        try:
            harvest(json.loads(Path(hp).read_text(encoding="utf-8")), hits)
        except (OSError, ValueError) as exc:
            say(f"ERROR: harvest {hp}: {exc}")
            return 1
        for n in hits:
            dnc_client.record(n, "tracerfy", True, cache, reason=f"harvest {Path(hp).name}")
        added["tracerfy"] += len(hits)

    for sp in a.sc_blocked:
        try:
            hits, skipped = read_sc_blocked(Path(sp))
        except (OSError, ValueError) as exc:
            say(f"ERROR: sc-blocked {sp}: {exc}")
            return 1
        for n, reason in hits:
            dnc_client.record(n, "smartercontact", True, cache, reason=reason)
        added["smartercontact"] += len(hits)
        for reason, k in skipped.items():
            say(f"  sc-blocked skipped (not a DNC reason): {reason} x{k}")

    numbers = list(dict.fromkeys(n for *_x, n in rows))
    need = [n for n in numbers if dnc_client.status(n, cache) in ("stale", "unchecked")]
    price = dnc_client.price_per_number()
    est = f"${len(need) * price:,.2f}" if price is not None else "price unknown (DNC_PRICE_PER_NUMBER unset)"
    say(f"{len(rows)} rows, {len(numbers)} unique numbers from {len(sheets)} sheet(s)")
    say(f"free signals recorded: datasift {added['datasift']}, tracerfy {added['tracerfy']}, "
        f"smartercontact {added['smartercontact']}")
    say(f"need vendor check (stale + unchecked): {len(need)}  est. cost {est}")

    if a.spend and need:
        batch = need[: a.limit] if a.limit else need
        say(f"SPEND: sending {len(batch)} number(s) to the DNC vendor")
        try:
            got = dnc_client.scrub_remote(batch)
        except (dnc_client.DncVendorNotConfigured, dnc_client.DncVendorError) as exc:
            say(f"ERROR: {exc}")
            dnc_client.save_cache(cache, cache_path)  # keep the free signals
            return 1
        for n, e in got.items():
            dnc_client.record(n, dnc_client.VENDOR, bool(e.get("dnc")), cache,
                              **{k: e.get(k) for k in ("federal", "state", "litigator", "wireless", "reason")})
    dnc_client.save_cache(cache, cache_path)

    out = Path(a.out) if a.out else sheets[0].parent / "dnc_review.csv"
    tally = Counter()
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_FIELDS)
        w.writeheader()
        for sheet, i, r, n in rows:
            st = dnc_client.status(n, cache)
            v = (cache.get(n) or {}).get(dnc_client.VENDOR) or {}
            w.writerow({
                "source_file": str(sheet), "row": i, "key": pick(r, KEY_COLS),
                "contact": pick(r, NAME_COLS), "number": n, "dnc_status": st,
                "dnc_sources": "|".join(dnc_client.dnc_sources(n, cache)),
                "federal": v.get("federal", ""), "state": v.get("state", ""),
                "litigator": v.get("litigator", ""), "wireless": v.get("wireless", ""),
                "reason": v.get("reason", ""), "vendor_checked_at": v.get("checked_at", ""),
                "datasift_status": pick(r, STATUS_COLS), "current_tag": pick(r, TAG_COLS),
                "dial_ok": st == "clean",
            })
    for n in numbers:
        tally[dnc_client.status(n, cache)] += 1
    say(f"review: {out}")
    say(f"VERDICT: {len(numbers)} numbers -- dnc {tally['dnc']}, clean {tally['clean']}, "
        f"stale {tally['stale']}, unchecked {tally['unchecked']}")
    return 0 if tally["stale"] + tally["unchecked"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
