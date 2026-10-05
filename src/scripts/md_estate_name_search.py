"""Maryland Register of Wills Estate Search BY DECEDENT NAME for DP records with no case.

The v5 court join (``dp_court_parties.py``) only knows an MD case when the vendor anchors
carry an estate number. 167 of the 334 MD Sept records have none: the owner died (obituary)
but no Register of Wills notice reached our files, or the estate opened before our store
starts. The same Estate Search form the RoW pull drives (``frmEstateSearch2.aspx``) takes a
decedent name; one Firecrawl scrape per term per record (1 credit each, measured 2026-10-02).

Form fields (verified on a probe 2026-10-02): ``#cboCountyId`` (county id, same list as the
RoW pull), ``#txtLN`` / ``#txtFN`` / ``#txtMN``, ``#chkExactMatchLastName`` (left OFF: a
hyphenated or re-spelled surname still comes back and we filter the grid ourselves),
``#cboPartyType`` = Decedent, submit ``#cmdSearch``, grid ``#dgSearchResults`` (County,
Estate No, Filing Date, Date of Death, Type, Status, 'LAST , FIRST MIDDLE').

Terms per record, in order, stop at the first term with a candidate:
  1. the owner (last, first token of first)       2. records.json decedent_first/last if different
  3. the research decedent name (``inheritance_no_case.csv`` ``decedent_name_research``)
  4. a hyphenated surname split both ways
A candidate is a grid row whose surname equals the term's and whose first name starts with
the term's. The record's case is CHOSEN only when:
  - exactly one candidate and the research DOD (if any) is within 45 days of the grid DOD, or
  - several candidates and exactly one is within 45 days of the research DOD.
Everything else stays in ``hits`` for review (``basis`` says why) and is never auto-joined
-- a surname-only hit on Smith / Brown / Johnson is somebody else's estate until proven.

Writes ``<run>/rerun_v5/md_name_search.json`` ({key: {...}}) and ``md_name_search.log``.
Never touches ``output/md_row_ledger.json`` (the RoW pull owns it; two writers lose work).
Idempotent: keys already in the store are skipped unless --force.

    python src/scripts/md_estate_name_search.py --run-dir output/dp_drive_sept_missing_20260929 \
        --keys-file output/dp_drive_sept_missing_20260929/rerun_v5/md_no_case_keys.json --probe 302marylouave|21060
    python src/scripts/md_estate_name_search.py --run-dir ... --keys-file ... --workers 6
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
from datetime import datetime, date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import md_register_of_wills_pull as row  # noqa: E402

DOD_WINDOW_DAYS = 45          # the _verify_anchors rule: a DOD more than 45 days off is a contradiction
MAX_DOD_GAP_YEARS = 3         # the skill-library constant: a name-only estate older than this is not auto-joined
MAX_PAGES = 3                 # a common surname pages; past 3 pages the name is too common to resolve
_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "ESQ", "MD", "PHD"}


# ── names ───────────────────────────────────────────────────────────────────

def norm(s: str) -> str:
    s = re.sub(r"[.,'’\"]", "", (s or "").upper())
    s = re.sub(r"[-/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_full_name(full: str) -> tuple[str, str]:
    """'Mary L. Whitmon-Clark' -> ('Whitmon-Clark', 'Mary'); 'John A Smith Jr' -> ('Smith', 'John')."""
    toks = [t for t in re.split(r"\s+", (full or "").strip()) if t]
    while toks and norm(toks[-1]) in _SUFFIXES:
        toks.pop()
    if len(toks) < 2:
        return ("", "")
    return (toks[-1], toks[0])


def first_token(first: str) -> str:
    toks = norm(first).split()
    return toks[0] if toks else ""


def build_terms(rec: dict, cohort: dict | None) -> list[dict]:
    """Ordered, deduped (last, first) search terms for one record."""
    cands: list[tuple[str, str, str]] = []
    cands.append((rec.get("owner_last") or "", rec.get("owner_first") or "", "owner"))
    if rec.get("decedent_last") or rec.get("decedent_first"):
        cands.append((rec.get("decedent_last") or "", rec.get("decedent_first") or "", "decedent"))
    if cohort and cohort.get("decedent_name_research"):
        last, first = split_full_name(cohort["decedent_name_research"])
        cands.append((last, first, "research"))
    # hyphenated surnames both ways
    for last, first, label in list(cands):
        parts = [p for p in re.split(r"[-/\s]+", last) if p]
        if len(parts) > 1:
            for p in parts:
                cands.append((p, first, f"{label}:surname-part"))
    out, seen = [], set()
    for last, first, label in cands:
        ln, fn = norm(last), first_token(first)
        if not ln or not fn or (ln, fn) in seen:
            continue
        seen.add((ln, fn))
        out.append({"last": ln, "first": fn, "label": label})
    return out


def row_matches(grid_row: dict, term: dict) -> bool:
    raw = grid_row.get("name_raw") or ""
    if "," in raw:
        last, _, first_mid = raw.partition(",")
    else:
        first_mid, last = row.split_last_first_name(raw)
    ln = norm(last)
    first_tok = first_token(first_mid)
    term_last_parts = set(term["last"].split())
    last_ok = ln == term["last"] or (bool(term_last_parts) and term_last_parts <= set(ln.split()))
    first_ok = bool(first_tok) and (first_tok.startswith(term["first"]) or term["first"].startswith(first_tok))
    return last_ok and first_ok


def _iso(mdy: str) -> str:
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", mdy or "")
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else ""


def _days_between(a: str, b: str) -> int | None:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except (TypeError, ValueError):
        return None


def choose(cands: list[dict], research_dod: str) -> tuple[dict | None, str]:
    """(chosen row or None, basis)."""
    if not cands:
        return None, "no_candidate"
    dod = (research_dod or "")[:10]
    if dod:
        close = [c for c in cands if _days_between(_iso(c.get("date_of_death", "")), dod) is not None
                 and _days_between(_iso(c.get("date_of_death", "")), dod) <= DOD_WINDOW_DAYS]
        if len(close) == 1:
            return close[0], "name+dod"
        if len(close) > 1:
            return None, "ambiguous: several candidates within the DOD window"
        if len(cands) == 1:
            return None, f"dod_conflict: grid DOD {cands[0].get('date_of_death') or '?'} vs research {dod}"
        return None, "name_only_multiple: no candidate within the DOD window"
    if len(cands) == 1:
        # No research DOD to confirm it. An estate opened within the last MAX_DOD_GAP_YEARS can be
        # this owner's; an older one (1998, 2004 ...) on a record that is on the inheritance list
        # in 2026 is a namesake or the prior generation until a human says otherwise.
        grid_dod = _iso(cands[0].get("date_of_death", ""))
        floor = date.today().replace(year=date.today().year - MAX_DOD_GAP_YEARS).isoformat()
        if grid_dod and grid_dod >= floor:
            return cands[0], "name"
        return None, f"name_old_estate: grid DOD {cands[0].get('date_of_death') or '?'} is older than {MAX_DOD_GAP_YEARS} years - namesake / prior generation? review"
    return None, "name_only_multiple: no research DOD to pick by"


# ── the scrape ──────────────────────────────────────────────────────────────

def _search_actions(county_id: str, term: dict, pages: int = 1) -> list:
    actions = [
        {"type": "wait", "milliseconds": 2000},
        {"type": "executeJavascript",
         "script": (row._set_value_js("#cboCountyId", county_id) + row._set_value_js("#txtLN", term["last"])
                    + row._set_value_js("#txtFN", term["first"]) + row._set_value_js("#cboPartyType", "Decedent"))},
        {"type": "wait", "milliseconds": 1500},
        {"type": "click", "selector": "#cmdSearch"},
        {"type": "wait", "milliseconds": 5000},
        {"type": "scrape"},
    ]
    for page in range(2, pages + 1):
        actions.append({"type": "executeJavascript", "script": row._click_pager_page_js(page)})
        actions.append({"type": "wait", "milliseconds": 4500})
        actions.append({"type": "scrape"})
    return actions


def search_term(county: str, term: dict, html_dir: Path | None, key: str, max_pages: int = MAX_PAGES) -> dict:
    """One term, one or two Firecrawl calls (the second only when the grid pages)."""
    cid = row.COUNTY_ID[county]
    htmls = row.firecrawl_scrape_retry(row.ESTATE_SEARCH_URL, _search_actions(cid, term), timeout=90)
    html = htmls[-1]
    status = row.parse_estate_search_status(html)
    rows = row.parse_estate_search_grid(html, county)
    pages_read = 1
    if status and status[1] > 1 and max_pages > 1:
        want = min(status[1], max_pages)
        htmls = row.firecrawl_scrape_retry(row.ESTATE_SEARCH_URL, _search_actions(cid, term, want),
                                           timeout=90 + 10 * want)
        rows = []
        for h in htmls:
            rows.extend(row.parse_estate_search_grid(h, county))
        pages_read = len(htmls)
    if html_dir is not None:
        html_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{key}__{term['last']}_{term['first']}")
        (html_dir / f"{safe}.html").write_text(html, encoding="utf-8")
    seen, uniq = set(), []
    for r in rows:
        if r.get("estate_number") in seen:
            continue
        seen.add(r.get("estate_number"))
        uniq.append(r)
    return {"last": term["last"], "first": term["first"], "label": term["label"],
            "rows_total": status[2] if status else len(uniq), "pages_total": status[1] if status else 1,
            "pages_read": pages_read, "rows_read": len(uniq), "rows": uniq}


def search_record(key: str, rec: dict, cohort: dict | None, html_dir: Path | None) -> dict:
    county = rec.get("county") or ""
    out = {"key": key, "street": rec.get("street") or "", "county": county, "terms": [], "hits": [],
           "chosen": None, "basis": "", "research_dod": (cohort or {}).get("dod") or "",
           "searched_at": datetime.now().isoformat(timespec="seconds")}
    if county not in row.COUNTY_ID:
        out["basis"] = f"county {county!r} not in COUNTY_ID"
        return out
    terms = build_terms(rec, cohort)
    if not terms:
        out["basis"] = "no usable name on the record"
        return out
    for term in terms:
        try:
            res = search_term(county, term, html_dir, key)
        except Exception as e:  # noqa: BLE001
            out["terms"].append({**term, "error": str(e)[:200]})
            continue
        cands = [r for r in res["rows"] if row_matches(r, term)]
        out["terms"].append({k: v for k, v in res.items() if k != "rows"} | {"n_candidates": len(cands)})
        if cands:
            for c in cands:
                c["matched_term"] = f"{term['last']}, {term['first']} ({term['label']})"
            out["hits"] = cands
            chosen, basis = choose(cands, out["research_dod"])
            out["chosen"], out["basis"] = chosen, basis
            break
    if not out["hits"]:
        errs = [t for t in out["terms"] if t.get("error")]
        out["basis"] = "search error on every term" if errs and len(errs) == len(out["terms"]) else "no_candidate"
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
    ap.add_argument("--keys-file", required=True, help="json list / dict or csv with a 'key' column")
    ap.add_argument("--cohort", default=None, help="inheritance_no_case.csv (research name + DOD); "
                                                   "default <run-dir>/rerun_v5/inheritance_no_case.csv")
    ap.add_argument("--state", default="MD")
    ap.add_argument("--probe", nargs="?", const="__first__", default=None, help="ONE key, print, write, stop")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--save-html", action="store_true", help="keep every results page under rerun_v5/estate_html/")
    ap.add_argument("--out", default=None)
    ap.add_argument("--rechoose", action="store_true",
                    help="recompute chosen / basis from the stored hits with the current rules; no scrape")
    a = ap.parse_args(argv)

    run_dir = Path(a.run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    out_path = Path(a.out) if a.out else run_dir / "rerun_v5" / "md_name_search.json"
    log_path = out_path.with_suffix(".log")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_dir = (out_path.parent / "estate_html") if a.save_html else None

    records = json.loads((run_dir / "records.json").read_text(encoding="utf-8"))
    cohort_path = Path(a.cohort) if a.cohort else run_dir / "rerun_v5" / "inheritance_no_case.csv"
    cohort: dict[str, dict] = {}
    if cohort_path.exists():
        cohort = {r["key"]: r for r in csv.DictReader(cohort_path.open(encoding="utf-8"))}
    keys_path = Path(a.keys_file)
    if not keys_path.is_absolute():
        keys_path = ROOT / keys_path
    keys = [k for k in load_keys(keys_path) if k in records
            and (records[k].get("state") or "").upper() == a.state.upper()]
    store: dict = {}
    if out_path.exists():
        store = json.loads(out_path.read_text(encoding="utf-8"))
    todo = [k for k in keys if a.force or k not in store]
    if a.limit:
        todo = todo[:a.limit]
    print(f"keys: {len(keys)} selected, {len([k for k in store if not k.startswith('_')])} already in "
          f"{out_path.name}, {len(todo)} to do")

    if a.rechoose:
        changed = 0
        for k, v in store.items():
            if k.startswith("_") or not v.get("hits"):
                continue
            chosen, basis = choose(v["hits"], v.get("research_dod") or "")
            if (chosen or {}).get("estate_number") != (v.get("chosen") or {}).get("estate_number") or basis != v.get("basis"):
                changed += 1
                print(f"  rechoose {k:40s} {v.get('basis', '')[:40]:40s} -> {basis[:60]}")
            v["chosen"], v["basis"] = chosen, basis
        _atomic_write(out_path, store)
        with log_path.open("a", encoding="utf-8") as lf:
            lf.write(f"{datetime.now():%Y-%m-%d %H:%M} RECHOOSE {changed} entries changed\n")
        print(f"rechoose: {changed} changed -> {out_path}")
        return 0

    if a.probe:
        k = todo[0] if a.probe == "__first__" else a.probe
        if k not in records:
            print(f"probe key not in records.json: {k}")
            return 2
        res = search_record(k, records[k], cohort.get(k), html_dir or out_path.parent / "estate_html")
        print(json.dumps(res, indent=1, ensure_ascii=False))
        store[k] = res
        _atomic_write(out_path, store)
        with log_path.open("a", encoding="utf-8") as lf:
            lf.write(f"{datetime.now():%Y-%m-%d %H:%M} PROBE {k} basis={res['basis']} hits={len(res['hits'])}\n")
        return 0

    lock = threading.Lock()
    counts = {"chosen": 0, "hits_unchosen": 0, "none": 0, "error": 0}
    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"{datetime.now():%Y-%m-%d %H:%M} RUN {len(todo)} keys workers={a.workers} force={a.force}\n")

        def _record(i: int, k: str, res: dict) -> None:
            with lock:
                store[k] = res
                _atomic_write(out_path, store)
                if res.get("chosen"):
                    counts["chosen"] += 1
                    tag = "CHOSEN"
                elif res.get("hits"):
                    counts["hits_unchosen"] += 1
                    tag = "REVIEW"
                elif res.get("basis", "").startswith("search error"):
                    counts["error"] += 1
                    tag = "ERROR"
                else:
                    counts["none"] += 1
                    tag = "none"
                ch = res.get("chosen") or {}
                line = (f"[{i}/{len(todo)}] {k:40s} {tag:6s} terms={len(res['terms'])} hits={len(res['hits'])} "
                        f"{ch.get('estate_number', '')} {ch.get('name_raw', '')} DOD {ch.get('date_of_death', '')} "
                        f"| {res['basis']}")
                print(line, flush=True)
                lf.write(line + "\n")
                lf.flush()

        if a.workers <= 1:
            for i, k in enumerate(todo, 1):
                _record(i, k, search_record(k, records[k], cohort.get(k), html_dir))
        else:
            with ThreadPoolExecutor(max_workers=a.workers) as ex:
                futs = {ex.submit(search_record, k, records[k], cohort.get(k), html_dir): k for k in todo}
                for i, fut in enumerate(as_completed(futs), 1):
                    _record(i, futs[fut], fut.result())
        lf.write(f"{datetime.now():%Y-%m-%d %H:%M} END {json.dumps(counts)}\n")
    print(f"done: {counts} -> {out_path}")
    if todo and counts["chosen"] == 0 and counts["hits_unchosen"] == 0:
        print("ZERO hits across the run is a FAILURE (selector / captcha / county id), not a quiet cohort")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
