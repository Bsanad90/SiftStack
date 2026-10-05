"""court_parties step of the DP v5 re-run -- entirely on disk (2026-10-02).

Joins every record of a DP run dir to the DC probate portal party store (the
``output/daily_pulls/<date>/dc_probates_portal.json`` files) and writes
``<run-dir>/rerun_v5/court.json``: PR + heirs/legatees with addresses, DOD, filing
date, status, and ``will_on_file`` (any party with connection ``Legatee``).

No scraping, no 2Captcha, no DataSift, no spend. Reads records.json, never writes it
(the ``build`` reconciliation reads court.json beside it).

Join order, each match recorded in ``join_basis``:
  1. ``case_number``  -- anchors.case_number == portal case_no
  2. ``address``      -- house number + first street token + quadrant + zip5
  3. ``name_only``    -- decedent last name + first-name token, accepted only when the
                         portal property_zip equals the record zip or the portal row
                         has no property address at all. REVIEW these by hand.
MD / VA records get the PR from ``anchors`` only (no interested-persons list online).

    python src/scripts/dp_court_parties.py --run-dir output/dp_drive_0930_new
    python src/scripts/dp_court_parties.py --run-dir <dir> --keys-file keys.txt --summary-keys pilot.txt

Idempotent and deterministic: court.json carries no timestamps (those go to court.log).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
PORTAL_GLOB = str(ROOT / "output" / "daily_pulls" / "*" / "dc_probates_portal.json")

QUADRANTS = {"NW", "NE", "SE", "SW"}
# unit designators that start the part of a street we throw away
UNIT_RE = re.compile(
    r"\s*(?:,\s*)?(?:#|\bapt\b\.?|\bunit\b|\bste\b\.?|\bsuite\b|\bfl\b\.?|\bfloor\b|\brm\b\.?|\broom\b|\bbldg\b\.?)\s*.*$",
    re.I,
)
HOUSE_RE = re.compile(r"^\s*(\d+)(?:\s+1/2|-?[A-Za-z](?=\s))?\b")
SUFFIX_MAP = {
    "street": "st", "str": "st", "avenue": "ave", "av": "ave", "place": "pl", "road": "rd",
    "drive": "dr", "court": "ct", "terrace": "ter", "terr": "ter", "lane": "ln", "boulevard": "blvd",
    "circle": "cir", "parkway": "pkwy", "highway": "hwy", "square": "sq", "plaza": "plz",
    "crescent": "cres", "way": "way",
}
# city hints for pr_mailing parsing; extended from the records at run time
BASE_CITIES = {
    "washington", "silver spring", "takoma park", "chevy chase", "bethesda", "rockville",
    "gaithersburg", "germantown", "kensington", "wheaton", "potomac", "olney", "annapolis",
    "glen burnie", "severna park", "pasadena", "crofton", "odenton", "arnold", "frederick",
    "mount airy", "thurmont", "brunswick", "walkersville", "middletown", "new market", "urbana",
    "arlington", "fairfax", "alexandria", "falls church", "vienna", "mclean", "herndon", "reston",
    "springfield", "annandale", "burke", "centreville", "chantilly", "lorton", "oakton",
    "great falls", "randallstown", "copper center", "chadds ford", "huntington", "hyattsville",
    "college park", "upper marlboro", "bowie", "laurel", "capitol heights", "temple hills",
    "oxon hill", "fort washington", "clinton", "district heights", "suitland", "landover",
    "lanham", "greenbelt", "beltsville", "adelphi", "riverdale", "new carrollton", "baltimore",
    "columbia", "ellicott city", "owings mills", "towson", "catonsville", "waldorf", "la plata",
}


# ──────────────────────────────────────────────────────────────────────────────
# normalisation helpers
# ──────────────────────────────────────────────────────────────────────────────
def zip5(z: Any) -> str:
    m = re.match(r"\s*(\d{5})", str(z or ""))
    return m.group(1) if m else ""


def norm_token(tok: str) -> str:
    t = re.sub(r"[^A-Za-z0-9]", "", tok or "").lower()
    return SUFFIX_MAP.get(t, t)


def norm_addr(street: Any, zip_code: Any = "") -> Optional[Tuple[str, str, str, str]]:
    """``(house, first_street_token, quadrant, zip5)`` or None when no house number.

    Unit / apartment parts are stripped, suffixes abbreviated, dots and commas removed,
    the quadrant read from any ``NW / N.W. / ne`` token.  Street names that START with a
    quadrant-looking word (``N Street NW``) keep that word as the street token because
    the quadrant is taken from the LAST matching token only.
    """
    s = str(street or "").strip()
    if not s:
        return None
    s = UNIT_RE.sub("", s)
    s = s.replace(".", "").replace(",", " ")
    m = HOUSE_RE.match(s)
    if not m:
        return None
    house = m.group(1)
    rest = s[m.end():].split()
    rest = [t for t in rest if t and t.lower() != "1/2"]
    quad = ""
    for i in range(len(rest) - 1, 0, -1):  # never the first token: "N Street" is a street
        if rest[i].upper() in QUADRANTS:
            quad = rest[i].upper()
            del rest[i]
            break
    first = norm_token(rest[0]) if rest else ""
    return (house, first, quad, zip5(zip_code))


def addr_match(a: Optional[Tuple[str, str, str, str]], b: Optional[Tuple[str, str, str, str]]) -> bool:
    if not a or not b:
        return False
    if a[0] != b[0] or a[1] != b[1] or not a[1]:
        return False
    if not a[3] or not b[3] or a[3] != b[3]:
        return False
    if a[2] and b[2] and a[2] != b[2]:
        return False
    return True


def norm_name_token(s: Any) -> str:
    return re.sub(r"[^a-z]", "", str(s or "").lower())


def last_name_parts(last: Any) -> set:
    """``Chambers-Chandler`` -> {chamberschandler, chambers, chandler}."""
    raw = str(last or "").strip().lower()
    if not raw:
        return set()
    parts = {norm_name_token(raw)}
    for p in re.split(r"[-\s]+", raw):
        if len(p) >= 2:
            parts.add(norm_name_token(p))
    return {p for p in parts if p}


def split_portal_name(name: Any) -> Tuple[str, str]:
    """Portal names are ``Last, First M`` -> (last, first_token)."""
    n = str(name or "").strip()
    if "," in n:
        last, first = n.split(",", 1)
    else:
        toks = n.split()
        last, first = (toks[-1], " ".join(toks[:-1])) if toks else ("", "")
    ft = first.strip().split()
    return last.strip(), (ft[0] if ft else "")


def record_names(rec: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Every (last, first_token) pair a record offers for the decedent."""
    out: List[Tuple[str, str]] = []
    an = rec.get("anchors") or {}
    full = an.get("decedent_full") or ""
    toks = [t for t in re.sub(r"[.,]", " ", full).split() if t]
    if len(toks) >= 2:
        out.append((toks[-1], toks[0]))
    for lk, fk in (("decedent_last", "decedent_first"), ("owner_last", "owner_first")):
        last, first = rec.get(lk) or "", rec.get(fk) or ""
        ft = str(first).split()
        if last and ft:
            out.append((last, ft[0]))
    seen, uniq = set(), []
    for last, first in out:
        k = (norm_name_token(last), norm_name_token(first))
        if k not in seen and all(k):
            seen.add(k)
            uniq.append((last, first))
    return uniq


def name_match(portal_row: Dict[str, Any], rec: Dict[str, Any]) -> bool:
    p_last = portal_row.get("decedent_last_name") or ""
    p_first = (portal_row.get("decedent_first_name") or "").split()
    if not p_last or not p_first:
        p_last, pf = split_portal_name(portal_row.get("decedent_name"))
        p_first = [pf] if pf else []
    if not p_last or not p_first:
        return False
    p_parts = last_name_parts(p_last)
    p_ft = norm_name_token(p_first[0])
    for last, first in record_names(rec):
        if norm_name_token(first) == p_ft and (last_name_parts(last) & p_parts):
            return True
    return False


def parse_mailing(mailing: Any, cities: Iterable[str] = ()) -> Dict[str, str]:
    """``4911 Brooks St Ne #207 Washington, DC 20019`` -> street / city / state / zip.

    The vendor string has no street-city separator, so the city is the longest known city
    name ending the pre-comma part, else its last word.  ``mailing_raw`` keeps the string.
    """
    raw = str(mailing or "").strip()
    out = {"street": "", "city": "", "state": "", "zip": "", "mailing_raw": raw}
    if not raw:
        return out
    m = re.match(r"^(.*?)(?:,\s*|\s+)([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)\s*$", raw)
    if not m:
        out["street"] = raw
        return out
    pre, out["state"], out["zip"] = m.group(1).strip().rstrip(","), m.group(2).upper(), m.group(3)
    known = {c.lower() for c in cities} | BASE_CITIES
    pre_l = pre.lower()
    best = ""
    for c in known:
        if pre_l.endswith(" " + c) and len(c) > len(best):
            best = c
    if best:
        out["city"] = pre[len(pre) - len(best):]
        out["street"] = pre[: len(pre) - len(best)].strip().rstrip(",")
    else:
        toks = pre.split()
        out["city"] = toks[-1] if len(toks) > 1 else ""
        out["street"] = " ".join(toks[:-1]) if len(toks) > 1 else pre
    return out


# ──────────────────────────────────────────────────────────────────────────────
# portal store
# ──────────────────────────────────────────────────────────────────────────────
def _mdy_key(s: Any) -> Tuple[int, int, int]:
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", str(s or ""))
    if m:
        return (int(m.group(3)), int(m.group(1)), int(m.group(2)))
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", str(s or ""))
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (0, 0, 0)


def _richness(row: Dict[str, Any]) -> int:
    return len(row.get("heirs") or []) + len(row.get("personal_reps") or [])


def load_portal_store(paths: Optional[Iterable[str]] = None, ledger: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """case_no -> portal row (+ ``_source_file``); the latest ``last_checked`` wins, then the
    richer party list, then the later file path (deterministic).

    ``ledger`` (opt-in) adds ``output/dc_probate_ledger.json`` -- ``{"estates": {case_no: row}}``,
    273 cases / 269 with parties on 2026-10-02 -- under the same dedupe rule.  It is OFF by
    default because the v5 handoff names the daily files as the party store.
    """
    files = sorted(paths) if paths is not None else sorted(glob.glob(PORTAL_GLOB))
    if ledger:
        files = files + [ledger]
    store: Dict[str, Dict[str, Any]] = {}
    for f in files:
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("estates"), dict) and "case_no" not in data:
            data = data["estates"]
        rows = data.values() if isinstance(data, dict) else data
        for row in rows:
            case_no = (row.get("case_no") or "").strip()
            if not case_no:
                continue
            cand = dict(row)
            cand["_source_file"] = _rel(f)
            cur = store.get(case_no)
            if cur is None or (_mdy_key(cand.get("last_checked")), _richness(cand)) >= (
                _mdy_key(cur.get("last_checked")), _richness(cur)
            ):
                store[case_no] = cand
    return store


def _rel(p: str) -> str:
    try:
        return str(Path(p).resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


def build_indexes(store: Dict[str, Dict[str, Any]]):
    by_addr: Dict[Tuple[str, str, str, str], List[str]] = {}
    for case_no, row in store.items():
        key = norm_addr(row.get("property_street"), row.get("property_zip"))
        if key and key[3]:
            by_addr.setdefault(key, []).append(case_no)
    return by_addr


# ──────────────────────────────────────────────────────────────────────────────
# the join
# ──────────────────────────────────────────────────────────────────────────────
def _party(p: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (p.get(k) or "") for k in ("name", "connection", "street", "city", "state", "zip")}


def _pick(cands: List[str], store: Dict[str, Dict[str, Any]], rec: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Deterministic choice among several matching cases: a decedent-name match first,
    then the latest filing date, then case_no."""
    uniq = sorted(set(cands))
    if len(uniq) == 1:
        return uniq[0], []
    ranked = sorted(
        uniq,
        key=lambda c: (0 if name_match(store[c], rec) else 1, tuple(-x for x in _mdy_key(store[c].get("filing_date"))), c),
    )
    return ranked[0], ranked[1:]


def court_entry_dc(rec: Dict[str, Any], store: Dict[str, Dict[str, Any]], by_addr) -> Tuple[Dict[str, Any], Optional[str]]:
    """Returns (entry, unmatched_case_no_or_None)."""
    an = rec.get("anchors") or {}
    anchor_case = (an.get("case_number") or "").strip()
    unmatched = None
    case_no, basis, alts = None, None, []

    if anchor_case:
        if anchor_case in store:
            case_no, basis = anchor_case, "case_number"
        else:
            unmatched = anchor_case

    if case_no is None:
        rkey = norm_addr(rec.get("street"), rec.get("zip"))
        cands = [c for c in by_addr.get(rkey, [])] if rkey else []
        # a portal row whose quadrant is blank still matches on house + token + zip
        if rkey and not cands:
            cands = [c for k, cs in by_addr.items() if addr_match(k, rkey) for c in cs]
        if cands:
            case_no, alts = _pick(cands, store, rec)
            basis = "address"

    if case_no is None:
        rz = zip5(rec.get("zip"))
        cands = []
        for c, row in store.items():
            if not name_match(row, rec):
                continue
            has_addr = bool((row.get("property_street") or "").strip())
            if (has_addr and zip5(row.get("property_zip")) == rz) or not has_addr:
                cands.append(c)
        if cands:
            case_no, alts = _pick(cands, store, rec)
            basis = "name_only"

    if case_no is None:
        entry = _empty_entry(rec)
        entry["note"] = "no case on file"
        if unmatched:
            entry["case_no"] = unmatched
            entry["note"] = "anchors case not in any portal file (needs the portal Parties service on a go)"
        return entry, unmatched

    row = store[case_no]
    prs = [_party(p) for p in (row.get("personal_reps") or [])]
    heirs = [_party(p) for p in (row.get("heirs") or [])]
    conns = Counter(p["connection"] for p in prs + heirs)
    entry = {
        "state": rec.get("state") or "DC",
        "case_no": case_no,
        "join_basis": basis,
        "source_file": row.get("_source_file"),
        "dod": row.get("dod") or "",
        "filing_date": row.get("filing_date") or "",
        "status": row.get("status") or "",
        "pr": prs,
        "heirs": heirs,
        "will_on_file": any(p["connection"] == "Legatee" for p in prs + heirs),
        "connections": dict(sorted(conns.items())),
        "decedent_name": row.get("decedent_name") or "",
        "property_itspe_owner": row.get("property_itspe_owner") or "",
        "portal_property": " ".join(
            str(row.get(k) or "") for k in ("property_street", "property_city", "property_state", "property_zip")
        ).strip(),
        "note": "",
    }
    if row.get("property_itspe_co_owner"):
        entry["property_itspe_co_owner"] = row["property_itspe_co_owner"]
    if alts:
        entry["alt_case_nos"] = alts
        entry["note"] = f"{len(alts)} other case(s) also matched: {', '.join(alts)}"
    if basis == "name_only":
        entry["note"] = ("REVIEW name_only join" + (f"; {entry['note']}" if entry["note"] else ""))
    if unmatched:
        entry["anchors_case_not_in_portal"] = unmatched
        entry["note"] = (entry["note"] + "; " if entry["note"] else "") + f"anchors case {unmatched} is not in any portal file"
    return entry, unmatched


def _empty_entry(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "state": rec.get("state") or "",
        "case_no": None,
        "join_basis": None,
        "source_file": None,
        "dod": None,
        "filing_date": None,
        "status": None,
        "pr": [],
        "heirs": [],
        "will_on_file": None,
        "decedent_name": None,
        "property_itspe_owner": None,
        "note": "",
    }


def _mdy_to_iso(s: Any) -> Optional[str]:
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", str(s or ""))
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return str(s) if s else None


def court_entry_mdva(rec: Dict[str, Any], cities: Iterable[str],
                     extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """MD / VA: the vendor anchors (RoW notice: estate number, PR + mailing, DOD) are the
    court record we have; MD publishes no interested-persons list. ``extra`` is this
    record's entry from ``md_estate_name_search.py`` (Estate Search by decedent NAME) for
    the records the anchors never reached: a CHOSEN row supplies the estate number, DOD,
    filing date, status and the name as filed (join_basis 'name_search'); a searched miss
    leaves case_no None and says so in the note, so the DP note can print
    "no Register of Wills estate under <terms> as of <date>" instead of "no case on file".
    Neither basis is a party list: _v5_has_case stays False, no COURT PARTIES block."""
    an = rec.get("anchors") or {}
    entry = _empty_entry(rec)
    case = an.get("case_number") or an.get("estate_number") or None
    entry["case_no"] = case
    entry["join_basis"] = "anchors" if case else None
    entry["dod"] = an.get("dod") or None
    entry["filing_date"] = an.get("file_date") or None
    entry["status"] = an.get("estate_status") or None
    entry["decedent_name"] = an.get("decedent_full") or an.get("decedent_as_filed") or None
    entry["court"] = an.get("court") or None
    if an.get("pr_full"):
        pr = {"name": an["pr_full"], "connection": "PR (anchors)"}
        pr.update(parse_mailing(an.get("pr_mailing"), cities))
        entry["pr"] = [pr]
    entry["note"] = "MD/VA: no interested-persons list online; PR from anchors only"
    if extra and not case:
        terms = [f"{t.get('last', '')}, {t.get('first', '')}" for t in extra.get("terms") or [] if t.get("last")]
        county = extra.get("county") or rec.get("county") or ""
        when = (extra.get("searched_at") or "")[:10]
        chosen = extra.get("chosen")
        entry["name_search"] = {"terms": terms, "hits": len(extra.get("hits") or []),
                                "basis": extra.get("basis") or "", "searched_at": when}
        if chosen:
            entry["case_no"] = chosen.get("estate_number") or None
            entry["join_basis"] = "name_search"
            entry["dod"] = _mdy_to_iso(chosen.get("date_of_death"))
            entry["filing_date"] = _mdy_to_iso(chosen.get("filing_date"))
            entry["status"] = chosen.get("status") or None
            raw = chosen.get("name_raw") or ""
            if "," in raw:
                last, _, first_mid = raw.partition(",")
                entry["decedent_name"] = f"{first_mid.strip()} {last.strip()}".strip().title()
            entry["court"] = f"{county} Register of Wills"
            entry["source_file"] = "rerun_v5/md_name_search.json"
            entry["note"] = (f"MD: estate found by decedent-name search ({chosen.get('matched_term') or terms[0]}; "
                             f"basis {extra.get('basis')}); PR not online for MD - from the RoW notice only")
        elif extra.get("hits"):
            entry["note"] = (f"MD: {len(extra['hits'])} Register of Wills estate(s) under {' / '.join(terms)} in "
                             f"{county} but none chosen ({extra.get('basis')}) - REVIEW, not joined")
        elif terms:
            entry["note"] = f"MD: no Register of Wills estate under {' / '.join(terms)} in {county} as of {when}"
    return entry


def run(run_dir: Path, keys: Optional[Iterable[str]] = None, portal_paths: Optional[Iterable[str]] = None,
        ledger: Optional[str] = None, extra_store: Optional[str] = None) -> Dict[str, Any]:
    with open(run_dir / "records.json", encoding="utf-8") as fh:
        records = json.load(fh)
    store = load_portal_store(portal_paths, ledger)
    extra: Dict[str, Any] = {}
    if extra_store:
        with open(extra_store, encoding="utf-8") as fh:
            extra = {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    by_addr = build_indexes(store)
    cities = {r.get("city") or "" for r in records.values()} | {
        (r.get("property_city") or "") for r in store.values()
    }
    cities.discard("")
    out: Dict[str, Any] = {}
    unmatched: List[Dict[str, Any]] = []
    want = set(keys) if keys is not None else None
    for key in sorted(records):
        if want is not None and key not in want:
            continue
        rec = records[key]
        if (rec.get("state") or "").upper() == "DC":
            entry, un = court_entry_dc(rec, store, by_addr)
            if un:
                unmatched.append({
                    "key": key, "state": "DC", "case_number": un, "street": rec.get("street"),
                    "decedent": (rec.get("anchors") or {}).get("decedent_full") or f"{rec.get('decedent_first','')} {rec.get('decedent_last','')}".strip(),
                    "resolved_by": entry.get("join_basis"),
                })
        else:
            entry = court_entry_mdva(rec, cities, extra.get(key))
        out[key] = entry
    result = {
        "_meta": {
            "run_dir": _rel(str(run_dir)),
            "portal_files": sorted({r["_source_file"] for r in store.values()}),
            "portal_cases": len(store),
            "records": len(out),
            "summary": summarize(out),
        },
        "_unmatched_cases": unmatched,
    }
    result.update(out)
    return result


def summarize(court: Dict[str, Any], keys: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    ks = [k for k in (keys if keys is not None else court) if not k.startswith("_") and k in court]
    c: Counter = Counter()
    for k in ks:
        e = court[k]
        st = (e.get("state") or "").upper()
        if st == "DC":
            c[f"dc_{e.get('join_basis') or 'no_case'}"] += 1
            if e.get("join_basis") in ("case_number", "address", "name_only"):
                c["dc_matched"] += 1
                c["dc_will_on_file" if e.get("will_on_file") else "dc_heir_only"] += 1
                if not e.get("heirs"):
                    c["dc_matched_no_heirs_listed"] += 1
            if e.get("anchors_case_not_in_portal") or (e.get("join_basis") is None and e.get("case_no")):
                c["dc_anchor_case_missing_from_portal"] += 1
        else:
            c[f"{st.lower() or 'xx'}_{e.get('join_basis') or 'no_anchor'}"] += 1
    c["records"] = len(ks)
    return dict(sorted(c.items()))


def table(court: Dict[str, Any], keys: Optional[Iterable[str]] = None) -> List[str]:
    ks = [k for k in (keys if keys is not None else court) if not k.startswith("_") and k in court]
    lines = []
    for k in sorted(ks):
        e = court[k]
        if e.get("join_basis") not in ("case_number", "address", "name_only"):
            continue
        pr = "; ".join(p["name"] for p in e.get("pr") or []) or "-"
        lines.append(f"{k} | {e['case_no']} | {e['join_basis']} | PR {pr} | heirs {len(e.get('heirs') or [])} | will {e.get('will_on_file')}")
    return lines


def _read_keys(path: str) -> List[str]:
    txt = Path(path).read_text(encoding="utf-8").strip()
    if txt.startswith("["):
        return list(json.loads(txt))
    return [ln.strip() for ln in txt.splitlines() if ln.strip() and not ln.startswith("#")]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--keys-file", help="only these keys (one per line or a JSON list)")
    ap.add_argument("--summary-keys", help="print a second summary + table for these keys (does not restrict output)")
    ap.add_argument("--out", help="output path (default <run-dir>/rerun_v5/court.json)")
    ap.add_argument("--ledger", nargs="?", const=str(ROOT / "output" / "dc_probate_ledger.json"),
                    help="ALSO read output/dc_probate_ledger.json (273 cases) into the party store; off by default")
    ap.add_argument("--extra-store", help="rerun_v5/md_name_search.json (MD Estate Search by decedent name); "
                                          "a chosen row supplies the MD case, a miss is written into the note")
    args = ap.parse_args(argv)

    run_dir = Path(args.run_dir)
    keys = _read_keys(args.keys_file) if args.keys_file else None
    result = run(run_dir, keys, ledger=args.ledger, extra_store=args.extra_store)
    out_path = Path(args.out) if args.out else run_dir / "rerun_v5" / "court.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    lines = [f"court_parties {datetime.now().isoformat(timespec='seconds')} run_dir={run_dir} out={out_path}",
             f"portal files: {len(result['_meta']['portal_files'])}, cases: {result['_meta']['portal_cases']}"
             + (f" (ledger {args.ledger} included)" if args.ledger else ""),
             "summary (all records): " + json.dumps(result["_meta"]["summary"]),
             f"anchors cases missing from the portal files: {len(result['_unmatched_cases'])}"]
    for u in result["_unmatched_cases"]:
        lines.append(f"  MISSING {u['case_number']} {u['key']} {u['decedent']} resolved_by={u['resolved_by']}")
    if args.summary_keys:
        sk = _read_keys(args.summary_keys)
        lines.append(f"summary ({len(sk)} summary keys): " + json.dumps(summarize(result, sk)))
        lines.extend("  " + t for t in table(result, sk))
    lines.append("matched records (all):")
    lines.extend("  " + t for t in table(result))
    text = "\n".join(lines)
    print(text)
    with open(out_path.with_name("court.log"), "a", encoding="utf-8") as fh:
        fh.write(text + "\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
