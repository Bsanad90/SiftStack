"""Maryland Land Records (MDLANDREC) deed INDEX per DP record -> rerun_v5/deed.json (MD entries).

The MD counterpart of ``dc_rod_lookup.py``: for every record the owner (and every other
owner SDAT shows on title, from ``rerun_v5/title.json``) is searched as a PARTY in the
county's land-records index through ``md_land_records_lookup.search_grantee`` -- one
Firecrawl scrape per term (login + search in one action chain, 1 credit, ~30 s). The index
row gives recorded date, instrument type, the matched party, Book/page and Remarks (the
clerk's parcel note: subdivision + lot/block, a tax account blob, or a street).

"On this parcel" is decided against SDAT (title.json): an instrument whose Book/page equals
the roll's Deed Reference or a Transfer Information deed is ON the parcel; a conveyance
whose Remarks carry a different tax account or a different street number is OFF it (a
namesake); anything else is unknown (None) and still counts for the dialer's view.

Instruments are written in dc_rod_lookup's shape so ``obituary_dp_batch`` renders them with
the same DEED line: recorded_date, doc_type, doc_number (Book/page), grantor / grantee (the
index shows only the MATCHED party; the other side is blank), legal (Remarks), flags,
is_conveyance, on_parcel, matched_term, matched_as.  Images are free on MDLANDREC but are
one more scrape each and are NOT read here (vesting stays 'unread').

--party both|grantor|grantee picks the form's radio.  'both' is the useful one (an owner who
appears as GRANTOR on the last conveyance may no longer hold title); the probe prints the
'Grantor:' / 'Grantee:' prefixes the grid returns so the radio ids can be confirmed live.

    python src/scripts/md_deed_lookup.py --run-dir output/dp_drive_sept_missing_20260929 \
        --keys-file output/dp_drive_sept_missing_20260929/rerun_v5/md_keys.json --probe "302marylouave|21060"
    python src/scripts/md_deed_lookup.py --run-dir ... --keys-file ... --workers 3
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import md_land_records_lookup as L  # noqa: E402
from dc_rod_lookup import CONVEYANCE_FLAGS, flag_instrument, flag_parties  # noqa: E402

MAX_TERMS = 3
_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}
_NOISE = {"TRUSTEE", "TRUSTEES", "TR", "TRS", "ETAL", "ET", "AL", "&", "AND", "THE", "ESTATE", "OF"}


# ── names ───────────────────────────────────────────────────────────────────

def norm(s: str) -> str:
    s = re.sub(r"[.,'’\"]", "", (s or "").upper())
    s = re.sub(r"[-/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def term_from_owner(last: str, first: str) -> tuple[str, str]:
    ln = norm(last)
    fn = norm(first).split()
    return ln, (fn[0] if fn else "")


def term_from_title_owner(owner: str) -> tuple[str, str]:
    """SDAT prints 'WHITLOCK PAUL R' / 'SMITH JOHN A JR' / 'COBB ESSIE M TRUSTEE' -> ('WHITLOCK', 'PAUL')."""
    toks = [t for t in norm(owner).split() if t not in _NOISE and t not in _SUFFIXES]
    if len(toks) < 2:
        return ("", "")
    return toks[0], toks[1]


def build_terms(rec: dict, title: dict | None) -> list[dict]:
    cands = [(*term_from_owner(rec.get("owner_last") or "", rec.get("owner_first") or ""), "owner")]
    for o in (title or {}).get("owners") or []:
        cands.append((*term_from_title_owner(o), "title owner"))
    out, seen = [], set()
    for ln, fn, label in cands:
        if not ln or not fn or (ln, fn) in seen:
            continue
        seen.add((ln, fn))
        out.append({"last": ln, "first": fn, "label": label})
    return out[:MAX_TERMS]


# ── parcel refs from SDAT ───────────────────────────────────────────────────

def parcel_refs(title: dict | None) -> dict:
    """Book/page keys and the tax account the roll gives for the record's parcel."""
    t = title or {}
    refs = set()
    for r in [t.get("deed_reference")] + [x.get(k) for x in (t.get("transfers") or []) for k in ("deed1", "deed2")]:
        k = L.deed_ref_key(r or "")
        if k:
            refs.add(k)
    prem = t.get("premise") or ""
    m = re.match(r"\s*(\d+)", prem)
    return {"book_pages": refs, "account": (t.get("account") or "").lstrip("0"),
            "street_number": m.group(1) if m else "", "legal": t.get("deed_reference") or ""}


def on_parcel(inst: dict, refs: dict) -> bool | None:
    k = L.deed_ref_key(inst.get("book") or "")
    if k and k in refs["book_pages"]:
        return True
    remarks = inst.get("legal") or ""
    if inst.get("is_conveyance"):
        if refs["account"]:
            for blob in re.findall(r"\d{7,13}", remarks):
                digits = blob.lstrip("0")
                if digits and refs["account"] in (digits, digits[-8:].lstrip("0"), digits[2:].lstrip("0")):
                    return True
            if re.search(r"\d{7,13}", remarks):
                return False     # a different tax account on a conveyance = another parcel
        num = L.remarks_street_number(remarks) if hasattr(L, "remarks_street_number") else ""
        if num and refs["street_number"] and num != refs["street_number"]:
            return False
        if num and refs["street_number"] and num == refs["street_number"]:
            return True
    return None


# ── one index row -> instrument ─────────────────────────────────────────────

def parse_party(cell: str) -> tuple[str, str]:
    """'Grantee: WHITLOCK , PAUL R' -> ('grantee', 'WHITLOCK PAUL R')."""
    m = re.match(r"\s*(Grantor|Grantee)\s*:\s*(.*)$", cell or "", re.I)
    role = m.group(1).lower() if m else ""
    name = (m.group(2) if m else cell or "")
    # the grid appends 'Capacity: TR' / 'Capacity: ETC' to a party name -- not part of the name
    name = re.split(r"\s*Capacity\s*:", name, maxsplit=1)[0]
    return role, re.sub(r"\s+", " ", name.replace(" ,", ",").replace(",", " ")).strip()


def to_instrument(r: dict, term: dict) -> dict:
    role, name = parse_party(r.get("party") or "")
    doc_type = (r.get("instrument_type") or "").strip()
    grantor = name if role == "grantor" else ""
    grantee = name if role == "grantee" else ""
    flags = flag_instrument(doc_type) | flag_parties(grantor, grantee, doc_type)
    inst = {
        "recorded_date": r.get("date") or "",
        "doc_type": doc_type,
        "doc_number": r.get("book_page") or "",
        "grantor": grantor,
        "grantee": grantee,
        "legal": r.get("remarks") or "",
        "book": r.get("book_page") or "",
        "related_instrument": None,
        "consideration": None,
        "matched_term": f"{term['last']} {term['first']}",
        "matched_as": [role] if role else [],
        "flags": sorted(flags),
        "info_viewer_url": r.get("info_viewer_url") or "",
        "source_term": term["label"],
    }
    inst["is_conveyance"] = bool(flags & CONVEYANCE_FLAGS) and "DEED OF TRUST" not in doc_type.upper()
    return inst


def finalize(key: str, searches: list, instruments: list, refs: dict, party: str) -> dict:
    # One instrument reaches us once per matched party (the 1972 Whitlock deed came back under
    # PAUL and again under ALMY): merge on Book/page + type, joining the names and roles.
    by_key: dict = {}
    for inst in instruments:
        k = (inst["doc_number"], inst["doc_type"])
        if k in by_key:
            prev = by_key[k]
            for side in ("grantor", "grantee"):
                if inst[side] and inst[side] not in (prev[side] or ""):
                    prev[side] = f"{prev[side]} + {inst[side]}" if prev[side] else inst[side]
            prev["matched_as"] = sorted(set(prev["matched_as"]) | set(inst["matched_as"]))
            if inst["matched_term"] not in prev["matched_term"]:
                prev["matched_term"] += f" / {inst['matched_term']}"
            prev["flags"] = sorted(set(prev["flags"]) | set(inst["flags"]))
            continue
        by_key[k] = inst
    uniq = list(by_key.values())
    for inst in uniq:
        inst["on_parcel"] = on_parcel(inst, refs)
    uniq.sort(key=lambda i: i["recorded_date"] or "")
    have_refs = bool(refs["book_pages"] or refs["account"])
    on = [i for i in uniq if i["on_parcel"] is True]
    scope = on if on else [i for i in uniq if i["on_parcel"] is not False]
    mode = "deed reference" if on else ("unknown parcel" if scope else None)
    flags: set[str] = set()
    for inst in scope:
        flags |= set(inst["flags"])
    last = scope[-1] if scope else None
    if not uniq:
        flags.add("no index hit")
    elif have_refs and not scope:
        flags.add("no index hit ON PARCEL (hits are other parcels / namesakes)")
    elif not on:
        flags.add("parcel NOT confirmed (no Book/page or account match against SDAT)")
    if last and (last["recorded_date"] or "") >= "2026-01-01":
        flags.add("instrument recorded 2026 - possible post-death transfer")
    conveyances = [i for i in scope if i["is_conveyance"]]
    lastc = conveyances[-1] if conveyances else None
    if lastc and lastc["matched_as"] == ["grantor"] and not lastc.get("grantee"):
        flags.add("owner is GRANTOR on last conveyance - may no longer hold title")
    if party == "grantee":
        flags.add("grantee search only - a later sale BY the owner would not show")
    return {
        "key": key,
        "searches": searches,
        "instruments": uniq,
        "parcel_legal": refs["legal"] or None,
        "parcel_match_mode": mode,
        "n_instruments": len(uniq),
        "n_on_parcel": len(on) if have_refs else None,
        "last_instrument": last,
        "last_conveyance": lastc,
        "vesting": "unread - MDLANDREC image (free, one scrape, on a go)",
        "flags": sorted(flags),
        "source": "mdlandrec",
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def lookup_record(key: str, rec: dict, title: dict | None, party: str) -> dict:
    county = rec.get("county") or ""
    refs = parcel_refs(title)
    terms = build_terms(rec, title)
    searches, instruments = [], []
    if county not in L.LANDREC_COUNTY_ID:
        return finalize(key, [{"error": f"county {county!r} not in LANDREC_COUNTY_ID"}], [], refs, party)
    for term in terms:
        try:
            rows = L.search_grantee(county, term["last"], term["first"], party=party)
            err = None
        except Exception as e:  # noqa: BLE001
            rows, err = [], str(e)[:200]
        searches.append({"term": f"{term['last']} {term['first']}", "label": term["label"], "party": party,
                         "count": len(rows), "error": err})
        instruments.extend(to_instrument(r, term) for r in rows)
    out = finalize(key, searches, instruments, refs, party)
    if searches and all(s.get("error") for s in searches):
        out["flags"] = sorted(set(out["flags"]) | {"search error"})
    return out


# ── driver ──────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_keys(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        return list(data.keys()) if isinstance(data, dict) else [str(k) for k in data]
    rows = list(csv.DictReader(text.splitlines()))
    return [r["key"] if "key" in r else list(r.values())[0] for r in rows]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--keys-file", required=True)
    ap.add_argument("--state", default="MD")
    ap.add_argument("--party", default="both", choices=["both", "grantor", "grantee"])
    ap.add_argument("--probe", nargs="?", const="__first__", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--title", default=None, help="title.json (default <run-dir>/rerun_v5/title.json)")
    ap.add_argument("--out", default=None, help="deed.json (default <run-dir>/rerun_v5/deed.json; DC entries kept)")
    a = ap.parse_args(argv)

    run_dir = Path(a.run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    out_path = Path(a.out) if a.out else run_dir / "rerun_v5" / "deed.json"
    log_path = out_path.with_suffix(".log")
    title_path = Path(a.title) if a.title else run_dir / "rerun_v5" / "title.json"
    records = json.loads((run_dir / "records.json").read_text(encoding="utf-8"))
    titles = json.loads(title_path.read_text(encoding="utf-8")) if title_path.exists() else {}
    keys_path = Path(a.keys_file)
    if not keys_path.is_absolute():
        keys_path = ROOT / keys_path
    keys = [k for k in load_keys(keys_path) if k in records
            and (records[k].get("state") or "").upper() == a.state.upper()]
    store: dict = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    todo = [k for k in keys if a.force or k not in store]
    if a.limit:
        todo = todo[:a.limit]
    no_title = sum(1 for k in todo if k not in titles)
    print(f"keys: {len(keys)} selected, {len(todo)} to do, {no_title} of them without a title.json entry "
          f"(owner term only, parcel unconfirmed); party={a.party}")

    if a.probe:
        k = todo[0] if a.probe == "__first__" else a.probe
        if k not in records:
            print(f"probe key not in records.json: {k}")
            return 2
        res = lookup_record(k, records[k], titles.get(k), a.party)
        print(json.dumps(res, indent=1, ensure_ascii=False))
        roles = sorted({r for i in res["instruments"] for r in i["matched_as"]})
        print(f"PROBE roles seen in the grid with --party {a.party}: {roles or 'NONE (radio id wrong?)'}")
        store[k] = res
        _atomic_write(out_path, store)
        with log_path.open("a", encoding="utf-8") as lf:
            lf.write(f"{datetime.now():%Y-%m-%d %H:%M} PROBE {k} party={a.party} n={res['n_instruments']} "
                     f"roles={roles} flags={res['flags']}\n")
        return 0

    lock = threading.Lock()
    counts = {"hit": 0, "on_parcel": 0, "none": 0, "error": 0}
    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"{datetime.now():%Y-%m-%d %H:%M} RUN MD {len(todo)} keys party={a.party} workers={a.workers}\n")

        def _record(i: int, k: str, res: dict) -> None:
            with lock:
                store[k] = res
                _atomic_write(out_path, store)
                if "search error" in res["flags"]:
                    counts["error"] += 1
                elif res["n_instruments"]:
                    counts["hit"] += 1
                    counts["on_parcel"] += bool(res.get("n_on_parcel"))
                else:
                    counts["none"] += 1
                lc = res.get("last_conveyance") or {}
                line = (f"[{i}/{len(todo)}] {k:40s} n={res['n_instruments']:3d} on_parcel={res.get('n_on_parcel')} "
                        f"last_conv={lc.get('recorded_date', '')} {lc.get('doc_type', '')} "
                        f"{(lc.get('grantor') or lc.get('grantee') or '')[:24]} | "
                        + "; ".join(f for f in res["flags"] if f not in ("deed", "trust", "release", "mortgage")))
                print(line, flush=True)
                lf.write(line + "\n")
                lf.flush()

        if a.workers <= 1:
            for i, k in enumerate(todo, 1):
                _record(i, k, lookup_record(k, records[k], titles.get(k), a.party))
        else:
            with ThreadPoolExecutor(max_workers=a.workers) as ex:
                futs = {ex.submit(lookup_record, k, records[k], titles.get(k), a.party): k for k in todo}
                for i, fut in enumerate(as_completed(futs), 1):
                    _record(i, futs[fut], fut.result())
        lf.write(f"{datetime.now():%Y-%m-%d %H:%M} END {json.dumps(counts)}\n")
    print(f"done: {counts} -> {out_path}")
    if todo and counts["hit"] == 0:
        print("ZERO index hits across the run is a FAILURE (login / radio / selector), not a quiet cohort")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
