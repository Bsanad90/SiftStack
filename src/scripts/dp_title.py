"""dp_title.py -- the `title` step of the DP v5 re-run: owner of record per key.

Reads a DP run dir (records.json + ranked_records.json), looks up the owner of
record for each key and writes ONE file, `<run-dir>/rerun_v5/title.json`,
keyed by record key. Nothing else in the run dir is touched.

Sources per state
  DC  ITSPE (= OTR, the DC tax roll) over ArcGIS -- FREE, read-only. Reuses
      `itspe_query` and the unit-aware address normalisation from
      `dc_probate_pull.py`. ITSPE IS OTR (trap): a MEDIUM / partial name match
      cannot be resolved by re-querying it.
  MD  SDAT Real Property via `md_land_records_lookup.lookup_sdat_by_address`.
      One Firecrawl scrape per record = SPEND. Only reachable behind
      `--allow-firecrawl`; without the flag MD keys are SKIPPED (not written),
      so a later run with the flag is not blocked by idempotency.
  VA  no fetcher in the stack: writes source none / match none.

Output shape per key
  owners[]                 names as stored on the roll (OWNERNAME, OWNNAME2)
  owner_mailing            ADDRESS1 + CITYSTZIP
  premise                  PREMISEADD as stored
  sale_date / sale_price   last sale (ISO date, number)
  ssl                      square-suffix-lot
  source                   itspe | sdat | none
  match                    address | none
  match_note               how the parcel was chosen / why it was not
  co_owners_not_in_probate owner names on title that match neither the decedent
                           (owner_first/last, decedent_first/last,
                           anchors.decedent_full) nor the PR (anchors.pr_full,
                           records pr). Conservative: when unsure the name goes
                           here and match_note says why. Court parties are NOT
                           joined here (another step builds them).
  household_subject        the SmartSkip subject row at the address when it has
                           NO first name (age + phone lines) -- a candidate
                           co-owner living at the property -- else null
  owner_of_record_is_decedent  true / false / null (no title)

Usage
  python src/scripts/dp_title.py --run-dir output/dp_drive_sept_missing_20260929 \
      --keys-file output/dp_drive_sept_missing_20260929/rerun_v5/groups.csv \
      --pilot --state DC [--probe <key>] [--force] [--allow-firecrawl]

Idempotent: title.json is reloaded and keys already done are skipped unless
--force. A small sleep between ITSPE calls (--sleep, default 1.0s).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dc_probate_pull import (  # noqa: E402
    _normalize_street_for_itspe,
    _sql_escape,
    _unit_of,
    itspe_query,
)

ROOT = Path(__file__).resolve().parents[2]

ENTITY_RE = re.compile(
    r"\b(INC|LLC|L\.?L\.?C|CORP|CORPORATION|CO|COMPANY|FOUNDATION|TRUST|"
    r"ASSOCIATION|ASSN|LP|LLP|PARTNERS|PARTNERSHIP|PROPERTIES|MANAGEMENT|HOUSING|DEVELOPMENT|"
    r"AUTHORITY|CHURCH|BANK|FUND|HOLDINGS|GROUP|ENTERPRISES|ESTATE|DISTRICT OF COLUMBIA|"
    r"UNITED STATES|GOVERNMENT|CONDOMINIUM|COOPERATIVE|COOP)\b", re.I)
# A person holding AS trustee ("ESSIE M COBB TRUSTEE", "SMITH JOHN TR") is a
# person on title under a trust vesting, not an entity: 1613 4th St NW
# (probe 2026-10-02) is the decedent herself, deed 2023-01-18 for $0.
TRUSTEE_RE = re.compile(r"\b(TRUSTEE|TRUSTEES|TR|TRS|CO-TRUSTEE|SUCCESSOR TRUSTEE)\b", re.I)
HONORIFIC_RE = re.compile(r"^(MR|MRS|MS|MISS|DR|REV|CAPT|COL|SGT|LT|HON|SR|JR|II|III|IV|ESQ)$", re.I)
SUFFIX_TOKENS = {"JR", "SR", "II", "III", "IV", "ESQ", "ET", "AL", "ETAL", "ETUX", "ETVIR"}


# ── name normalisation ──────────────────────────────────────────────────────

def _tokens(name: str) -> list[str]:
    """Upper-case alphabetic tokens; punctuation, honorifics and suffixes
    dropped; hyphens split ("WHITMON-CLARK" -> WHITMON, CLARK)."""
    s = re.sub(r"[^A-Za-z\s\-]", " ", name or "")
    out = []
    for t in re.split(r"[\s\-]+", s.upper()):
        t = t.strip()
        if not t or HONORIFIC_RE.match(t) or t in SUFFIX_TOKENS:
            continue
        out.append(t)
    return out


def split_title_owner(owner: str) -> list[str]:
    """One stored owner string -> the person names in it. 'SMITH HELEN H & DAVID B'
    yields ['SMITH HELEN H', 'SMITH DAVID B'] (a second name with a single
    token inherits the surname). Entity strings are returned whole."""
    owner = " ".join((owner or "").split())
    if not owner:
        return []
    if ENTITY_RE.search(owner):
        return [owner]
    owner = " ".join(TRUSTEE_RE.sub(" ", owner).split())  # person(s) holding as trustee
    parts = [p.strip(" ,") for p in re.split(r"\s*(?:&|\bAND\b)\s*", owner, flags=re.I) if p.strip(" ,")]
    if not parts:
        return [owner]
    surname = _tokens(parts[0])[0] if _tokens(parts[0]) else ""
    out = [parts[0]]
    for p in parts[1:]:
        toks = _tokens(p)
        if surname and len(toks) <= 2 and not any(len(t) > 1 and t == surname for t in toks):
            # 'DAVID B' / 'DAVID' -> same surname as the first name
            p = f"{surname} {p}"
        out.append(p)
    return out


def person_parts(name: str) -> tuple[list[str], str]:
    """A person's full name (any order, 'First Middle Last' or 'LAST FIRST M')
    -> (last-name tokens, first-name token). For a 'Last First' string the
    caller passes the pieces separately via person_match()."""
    toks = _tokens(name)
    if not toks:
        return [], ""
    return toks[-1:], toks[0]


def _lev1(a: str, b: str) -> bool:
    """True when a and b are within ONE edit (insert / delete / substitute)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    for i in range(len(b)):
        if a == b[:i] + b[i + 1:]:
            return True
    return False


def title_name_probable(title_owner: str, last: str, first: str) -> tuple[bool, str]:
    """A roll SPELLING of the person, not an exact match: 'STACIL, DWIGHT' for
    Dwight Stancil, 'JODY MTAVSS' for Jody M Tavss, 'ROMERO, CECILLIA' for
    Cecilia Romero, or an initials-only owner 'LAWRENCE, M V' for Malendia V
    Lawrence (surname exact, first initial equal). Reported separately as
    `decedent_probable`, never as a confirmed owner."""
    t_toks = _tokens(title_owner)
    last_toks = _tokens(last)
    first_toks = _tokens(first)
    if not t_toks or not last_toks or not first_toks:
        return False, ""
    first_tok, last_tok = first_toks[0], last_toks[-1]
    surname_on = any(lt in t_toks for lt in last_toks)
    # initials-only owner: every non-surname token is a single letter
    rest = [t for t in t_toks if t not in last_toks]
    if surname_on and rest and all(len(t) == 1 for t in rest):
        if rest[0] == first_tok[0]:
            return True, f"initials only on roll ({' '.join(rest)} = {first} ?)"
        return False, ""
    # exact first name, surname within one edit (len >= 5) or glued with a middle initial
    if first_tok in t_toks:
        for t in t_toks:
            if t == first_tok:
                continue
            if len(last_tok) >= 5 and len(t) >= 5 and _lev1(t, last_tok):
                return True, f"roll spelling {t} ~ {last_tok}"
            if len(t) == len(last_tok) + 1 and (t[1:] == last_tok or t[:-1] == last_tok):
                return True, f"roll glued initial {t} = ? {last_tok}"
    # exact surname, first name within one edit (len >= 5)
    if surname_on:
        for t in rest:
            if len(first_tok) >= 5 and len(t) >= 5 and _lev1(t, first_tok):
                return True, f"roll spelling {t} ~ {first_tok}"
        # exact surname, the roll's first name is a nickname / formal form of ours
        variants = {v.upper() for v in _name_variants(first_tok)}
        for t in rest:
            if len(t) > 1 and t in variants:
                return True, f"roll first name {t} is a form of {first_tok}"
    return False, ""


def _name_variants(first_tok: str) -> list[str]:
    try:
        if str(ROOT / "src") not in sys.path:
            sys.path.insert(0, str(ROOT / "src"))
        from obituary_enricher import _get_name_variants  # noqa: WPS433
    except Exception:  # noqa: BLE001
        return []
    return _get_name_variants(first_tok)


def title_name_matches(title_owner: str, last: str, first: str) -> tuple[bool, str]:
    """Does a stored title name ('COBB ESSIE M', 'SMITH, HELEN H') name the
    person (last, first)? Conservative: the first-name token must appear as a
    WHOLE token (an initial on the title side is not enough) and every
    last-name token must appear; a hyphenated surname is accepted when at
    least one of its tokens is on title, with a note."""
    t_toks = _tokens(title_owner)
    last_toks = _tokens(last)
    first_toks = _tokens(first)
    if not t_toks or not last_toks or not first_toks:
        return False, "blank side"
    first_tok = first_toks[0]
    if first_tok not in t_toks:
        return False, "first name not on title"
    if all(lt in t_toks for lt in last_toks):
        return True, "exact"
    if len(last_toks) > 1 and any(lt in t_toks for lt in last_toks):
        return True, f"hyphenated surname partial ({'/'.join(last_toks)})"
    return False, "surname not on title"


def _person_keys(rec: dict) -> dict[str, list[tuple[str, str]]]:
    """(last, first) pairs for the decedent and for the PR, from the record."""
    dec: list[tuple[str, str]] = []
    pr: list[tuple[str, str]] = []

    def add(bucket, last, first):
        last, first = (last or "").strip(), (first or "").strip()
        if last and first and (last, first) not in bucket:
            bucket.append((last, first))

    def add_full(bucket, full):
        toks = _tokens(full)
        if len(toks) >= 2:
            add(bucket, toks[-1], toks[0])

    add(dec, rec.get("owner_last"), rec.get("owner_first"))
    add(dec, rec.get("decedent_last"), rec.get("decedent_first"))
    anchors = rec.get("anchors") or {}
    add_full(dec, anchors.get("decedent_full", ""))
    add_full(pr, anchors.get("pr_full", ""))
    add_full(pr, rec.get("pr", ""))
    return {"decedent": dec, "pr": pr}


def classify_owners(owner_strings: list[str], rec: dict) -> dict:
    """Split every stored owner into persons and sort them: decedent / pr /
    co_owners_not_in_probate (+ a reason per name)."""
    keys = _person_keys(rec)
    decedent_hits, pr_hits, probable, others, notes = [], [], [], [], []
    trust_vesting = False
    for stored in owner_strings:
        if TRUSTEE_RE.search(stored or "") or re.search(r"\bTRUST\b", stored or "", re.I):
            trust_vesting = True
        for name in split_title_owner(stored):
            if ENTITY_RE.search(name):
                others.append(name)
                # a trust NAMED for the decedent ("ESSIE COBB REVOCABLE TRUST") still points at her
                named_for = [f"{f} {l}" for l, f in keys["decedent"]
                             if title_name_matches(ENTITY_RE.sub(" ", name), l, f)[0]]
                notes.append(f"{name}: entity on title" + (f" (named for the decedent {named_for[0]}; "
                             "successor trustee signs, not the estate)" if named_for else ""))
                continue
            hit = None
            for last, first in keys["decedent"]:
                ok, why = title_name_matches(name, last, first)
                if ok:
                    hit = ("decedent", why)
                    break
            if not hit:
                for last, first in keys["pr"]:
                    ok, why = title_name_matches(name, last, first)
                    if ok:
                        hit = ("pr", why)
                        break
            if not hit:
                for last, first in keys["decedent"]:
                    ok, why = title_name_probable(name, last, first)
                    if ok:
                        hit = ("probable", why)
                        break
            if hit and hit[0] == "decedent":
                decedent_hits.append(name)
                if hit[1] != "exact":
                    notes.append(f"{name}: decedent match on {hit[1]}")
                if TRUSTEE_RE.search(stored or ""):
                    notes.append(f"{name}: holds AS TRUSTEE -- trust vesting, the successor trustee signs; "
                                 "vesting unread")
            elif hit and hit[0] == "pr":
                pr_hits.append(name)
                notes.append(f"{name}: matches the PR on record")
            elif hit and hit[0] == "probable":
                probable.append(name)
                notes.append(f"{name}: PROBABLY the decedent -- {hit[1]} -- confirm on the deed")
            else:
                others.append(name)
                # say why: shares the surname? then likely family, still not in the probate
                dec_last = {lt for last, _ in keys["decedent"] for lt in _tokens(last)}
                toks = _tokens(name)
                shares = bool(dec_last & set(toks))
                initials = shares and all(len(t) == 1 for t in toks if t not in dec_last) and len(toks) > 1
                why = " (shares surname, initials only -- not the decedent's initial)" if initials else \
                      (" (shares surname)" if shares else "")
                notes.append(f"{name}: not the decedent{why}, not the PR")
    return {"decedent": decedent_hits, "pr": pr_hits, "probable": probable,
            "co_owners_not_in_probate": others, "trust_vesting": trust_vesting, "notes": notes}


# ── ITSPE (DC) ──────────────────────────────────────────────────────────────

def _mdy_to_iso(s: str) -> str:
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", s or "")
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else (s or "")


def norm_street(s: str) -> str:
    """'302  MARY LOU AVE' / '302 Mary Lou Ave' -> '302 MARY LOU AVE'; unit tokens dropped."""
    s = re.sub(r"[.,#]", " ", (s or "").upper())
    s = re.sub(r"\b(APT|UNIT|STE|SUITE)\b.*$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _premise_street(premise: str) -> str:
    p = " ".join((premise or "").upper().split())
    return re.sub(r"\s+WASHINGTON\s+DC\b.*$", "", p).strip()


def _iso_date(raw) -> str:
    if raw in (None, "", 0):
        return ""
    try:
        if isinstance(raw, (int, float)):
            # ArcGIS dates are epoch ms at UTC midnight; local conversion would
            # roll the day back in America/New_York.
            return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        return str(raw)[:10]
    except (ValueError, OSError, OverflowError):
        return str(raw)


def itspe_rows_for_address(street: str, sleep: float = 1.0) -> tuple[list[dict], str]:
    """Unit-first lookup mirroring dc_probate_pull.itspe_verify_address but
    returning the rows. Returns (rows, how)."""
    norm = _normalize_street_for_itspe(street)
    if not norm:
        return [], "blank street"
    unit = _unit_of(street)
    rows: list[dict] = []
    how = ""
    if unit:
        rows = itspe_query(f"UPPER(PREMISEADD) LIKE '{_sql_escape(norm + ' ' + unit)} %'", limit=6)
        how = f"unit query '{norm} {unit}'"
        if not rows:
            time.sleep(sleep)
            rows = itspe_query(f"UPPER(PREMISEADD) LIKE '{_sql_escape(norm + ' #' + unit[2:])} %'", limit=6)
            how = f"unit query '{norm} #{unit[2:]}'"
    if not rows:
        if unit:
            time.sleep(sleep)
        # 400 rows: a condo building has more than 50 units and the decedent's
        # unit can sit past the cap (2710 Macomb St NW, 1001 L St NW, 2026-10-02)
        rows = itspe_query(f"UPPER(PREMISEADD) LIKE '{_sql_escape(norm)}%'", limit=400)
        how = f"street query '{norm}%'" + (" (unit not found)" if unit else "")
    if not rows:
        # the roll does not abbreviate a street NAME ('3909 IVY TERRACE CT NW') and
        # spells a directional out ('3419 SOUTH DAKOTA AVE NE'): fall back to
        # house number + the longest name word
        m = re.match(r"^(\d+[A-Z]?)\s+(.*)$", norm)
        if m:
            num, rest = m.groups()
            words = [w for w in rest.split() if w not in _DIRECTIONALS and w not in _SUFFIXES and len(w) >= 3]
            if words:
                core = max(words, key=len)
                time.sleep(sleep)
                rows = itspe_query(f"UPPER(PREMISEADD) LIKE '{_sql_escape(num)} %{_sql_escape(core)}%'", limit=400)
                how = f"fallback query '{num} %{core}%' after '{norm}%' found nothing"
                quad = next((w for w in rest.split() if w in ("NW", "NE", "SW", "SE")), "")
                if quad:
                    rows = [r for r in rows if f" {quad} " in f" {_premise_street(r.get('PREMISEADD'))} "]
    return rows, how


_DIRECTIONALS = {"N", "S", "E", "W", "NW", "NE", "SW", "SE", "NORTH", "SOUTH", "EAST", "WEST"}
_SUFFIXES = {"ST", "AVE", "PL", "RD", "DR", "CT", "LN", "TER", "BLVD", "PKWY", "CIR", "HWY", "SQ", "CRES",
             "ALY", "PROM", "WAY", "STREET", "AVENUE", "PLACE", "ROAD", "DRIVE", "COURT", "LANE", "TERRACE"}


def itspe_building_by_surname(street: str, rec: dict, sleep: float = 1.0) -> tuple[list[dict], str]:
    """A stated address with no unit in a multi-unit building: ask the roll for
    parcels IN the building whose owner carries the decedent's surname. Free
    and exact (the building is fixed), so not the OTR MEDIUM-name trap."""
    norm = _normalize_street_for_itspe(street)
    surnames = sorted({lt for last, _ in _person_keys(rec)["decedent"] for lt in _tokens(last) if len(lt) >= 3})
    if not norm or not surnames:
        return [], "no surname to search the building with"
    clauses = " OR ".join(f"UPPER(OWNERNAME) LIKE '%{_sql_escape(s)}%' OR UPPER(OWNNAME2) LIKE '%{_sql_escape(s)}%'"
                          for s in surnames)
    time.sleep(sleep)
    rows = itspe_query(f"UPPER(PREMISEADD) LIKE '{_sql_escape(norm)}%' AND ({clauses})", limit=50)
    return rows, f"building '{norm}' x surname {'/'.join(surnames)}"


def pick_parcel(rows: list[dict], street: str, rec: dict) -> tuple[dict | None, str, list[dict]]:
    """Choose the parcel for the stated address. Exact premise street first;
    among several unit parcels the one in the decedent's name; otherwise
    MULTI_UNIT (no guess). Returns (row, note, candidates)."""
    if not rows:
        return None, "NO_PARCEL", []
    norm = _normalize_street_for_itspe(street, strip_unit=False)
    norm = re.sub(r"\s*#\s*", " # ", norm)
    norm = re.sub(r"\b(?:UNIT|APT|APARTMENT|STE|SUITE)\s+", "# ", norm)
    norm = " ".join(norm.split())
    by_premise: dict[str, dict] = {}
    for r in rows:
        by_premise.setdefault(_premise_street(r.get("PREMISEADD")), r)
    cands = [{"premise": p, "owners": [r.get("OWNERNAME") or "", r.get("OWNNAME2") or ""]}
             for p, r in by_premise.items()]
    if len(by_premise) == 1:
        p, r = next(iter(by_premise.items()))
        return r, f"single parcel '{p}'", cands
    if norm in by_premise:
        return by_premise[norm], f"exact premise '{norm}' among {len(by_premise)} parcels", cands
    # several parcels: take the one in the decedent's name, if exactly one
    hits = []
    for p, r in by_premise.items():
        names = [r.get("OWNERNAME") or "", r.get("OWNNAME2") or ""]
        c = classify_owners(names, rec)
        if c["decedent"] or c["probable"]:
            hits.append((p, r))
    if len(hits) == 1:
        p, r = hits[0]
        return r, f"decedent's name on parcel '{p}' among {len(by_premise)} parcels (stated address had no unit)", cands
    if len(hits) > 1:
        owners = {(r.get("OWNERNAME") or "", r.get("OWNNAME2") or "") for _, r in hits}
        if len(owners) == 1:
            # one owner, several units (a unit plus parking / storage): report the
            # first, list the rest
            p, r = hits[0]
            return r, (f"decedent holds {len(hits)} parcels in the building: "
                       f"{', '.join(pp for pp, _ in hits)} (stated address had no unit)"), cands
        return None, f"MULTI_UNIT: decedent named on {len(hits)} of {len(by_premise)} parcels", cands
    return None, f"MULTI_UNIT: {len(by_premise)} parcels, none in the decedent's name", cands


def title_for_dc(rec: dict, sleep: float = 1.0) -> dict:
    street = rec.get("street") or ""
    out = _empty("itspe")
    try:
        rows, how = itspe_rows_for_address(street, sleep=sleep)
    except Exception as e:  # noqa: BLE001
        out.update(source="none", match="none", match_note=f"ITSPE query failed: {e}")
        return out
    row, note, cands = pick_parcel(rows, street, rec)
    out["match_note"] = f"{how}; {note}"
    if row is None and note.startswith("MULTI_UNIT") and "none in the decedent" in note:
        # condo building, no unit stated: look for the surname inside the building
        try:
            brows, bhow = itspe_building_by_surname(street, rec, sleep=sleep)
        except Exception as e:  # noqa: BLE001
            brows, bhow = [], f"building surname query failed: {e}"
        out["match_note"] += f"; {bhow} -> {len(brows)} parcel(s)"
        if brows:
            row, note2, cands = pick_parcel(brows, street, rec)
            out["match_note"] += f"; {note2}"
            if row is None and len(cands) > 1:
                # several parcels, several surname holders: a unit is not chosen,
                # but the surname holders ARE reported
                out["match_note"] += "; surname holders in the building: " + "; ".join(
                    f"{c['premise']} = {' / '.join(o for o in c['owners'] if o)}" for c in cands[:6])
            elif row is not None:
                out["match_note"] += " -- unit resolved by the decedent's surname inside the building; " \
                                     "confirm the stated address is this unit"
        else:
            out["match_note"] += "; no owner of the decedent's surname in the building (sold, or held under another name)"
    if len(cands) > 1:
        out["candidates"] = cands[:10]
    if row is None:
        out.update(source="none", match="none")
        return out
    owners = [o for o in [(row.get("OWNERNAME") or "").strip(), (row.get("OWNNAME2") or "").strip()] if o]
    cls = classify_owners(owners, rec)
    mailing = " ".join(f"{row.get('ADDRESS1') or ''} {row.get('CITYSTZIP') or ''}".split())
    premise = " ".join((row.get("PREMISEADD") or "").split())
    sale_price = row.get("SALEPRICE")
    out.update(
        owners=owners,
        owner_mailing=mailing,
        premise=premise,
        # OTR leaves ADDRESS1/CITYSTZIP null when the owner's mailing is the premises
        mailing_is_premise=(_same_street(mailing, premise) if mailing else None),
        trust_vesting=cls["trust_vesting"],
        sale_date=_iso_date(row.get("SALEDATE")),
        sale_price=sale_price,
        ssl=(row.get("SSL") or "").strip(),
        match="address",
        decedent_on_title=cls["decedent"],
        decedent_probable=cls["probable"],
        pr_on_title=cls["pr"],
        co_owners_not_in_probate=cls["co_owners_not_in_probate"],
        # true = named on title; null = only a probable roll spelling; false = not on title
        owner_of_record_is_decedent=(True if cls["decedent"] else (None if cls["probable"] else False)),
    )
    if cls["notes"]:
        out["match_note"] += "; " + "; ".join(cls["notes"])
    if out["owner_of_record_is_decedent"] is False:
        dod = (rec.get("anchors") or {}).get("dod") or ""
        when = f" (last transfer {out['sale_date']}" + (f", AFTER the filing DOD {dod}" if dod and out["sale_date"] > dod else "") + ")" \
            if out["sale_date"] else ""
        out["match_note"] += f"; DECEDENT NOT ON TITLE{when} -- the owners above hold it; the estate may own nothing here"
    if out["sale_date"] and (sale_price in (None, 0, 0.0)):
        out["match_note"] += f"; last transfer {out['sale_date']} for $0 = non-arm's-length (trust / family deed)"
    if not mailing:
        out["match_note"] += "; no mailing on roll (OTR mails to the premises)"
    elif out["mailing_is_premise"] is False:
        out["match_note"] += f"; owner MAILS ELSEWHERE: {mailing}"
    return out


def _same_street(mailing: str, premise: str) -> bool:
    """Same house number (digits only: '5201B' = '5201') and same first two
    street words."""
    a = re.sub(r"\s+WASHINGTON\s+DC.*$", "", " ".join((mailing or "").upper().split()))
    b = _premise_street(premise)
    a = re.sub(r"\s*#\s*", " # ", a)
    at, bt = a.split(), b.split()
    if len(at) < 2 or len(bt) < 2:
        return False
    return re.sub(r"\D", "", at[0]) == re.sub(r"\D", "", bt[0]) and at[1:3] == bt[1:3]


# ── MD (SDAT, Firecrawl -- paid) and VA ─────────────────────────────────────

def title_for_md(county: str, street: str, owner_name: str, rec: dict | None = None) -> dict:
    """SDAT Real Property by street address. ONE FIRECRAWL SCRAPE PER CALL --
    the CLI only reaches this behind --allow-firecrawl."""
    from md_land_records_lookup import lookup_sdat_by_address, lookup_sdat_by_street_number  # noqa: WPS433
    out = _empty("sdat")
    parcel = lookup_sdat_by_address(county, street, owner_name=owner_name or "")
    match_kind, match_text = "address", "SDAT street-address search"
    if not parcel:
        # Fallback (Basem 2026-10-02, "spend if it fills missing data"): the house NUMBER alone,
        # street box empty, result row REQUIRED to carry the owner's name -- so a renamed or
        # re-abbreviated street still finds the parcel and a stranger's never comes back.
        m_num = re.match(r"\s*(\d+[A-Za-z]?)", street or "")
        if m_num and (owner_name or "").strip():
            parcel = lookup_sdat_by_street_number(county, m_num.group(1), owner_name)
            match_kind, match_text = "street_number", "SDAT street-NUMBER search, row matched on the owner's name"
    if not parcel:
        out.update(source="none", match="none",
                   match_note="SDAT: no parcel for the street address (number-only search with owner match also empty)")
        return out
    owners = [o for o in (parcel.get("owner_names") or []) if o]
    transfers = parcel.get("transfers") or []
    last_tr = transfers[0] if transfers else {}   # SDAT lists the most recent transfer first
    sale_date = _mdy_to_iso(last_tr.get("date") or "")
    price_raw = (last_tr.get("price") or "").replace("$", "").replace(",", "").strip()
    try:
        sale_price = int(float(price_raw)) if price_raw else None
    except ValueError:
        sale_price = None
    acct = parcel.get("account") or ""
    out.update(owners=owners, owner_mailing=parcel.get("mailing_address") or "",
               premise=parcel.get("premises_address") or "", sale_date=sale_date, sale_price=sale_price,
               sale_type=last_tr.get("type") or "", sale_seller=last_tr.get("seller") or "",
               ssl=(f"{parcel.get('district')}-{acct}" if parcel.get("district") and acct else acct),
               account=acct, district=parcel.get("district") or "", deed_reference=parcel.get("deed_reference") or "",
               transfers=transfers, legal_description=parcel.get("legal_description") or "",
               principal_residence=parcel.get("principal_residence") or "",
               match=match_kind, match_note=match_text, sdat_raw=parcel)
    prem = norm_street(parcel.get("premises_address") or "")
    if prem and not prem.startswith(norm_street(street)):
        out["match_note"] += f"; SDAT premises reads {parcel['premises_address']}"
    if rec is not None:
        cls = classify_owners(owners, rec)
        out.update(decedent_on_title=cls["decedent"], decedent_probable=cls["probable"], pr_on_title=cls["pr"],
                   co_owners_not_in_probate=cls["co_owners_not_in_probate"], trust_vesting=cls["trust_vesting"],
                   owner_of_record_is_decedent=(None if not owners else
                                                (True if cls["decedent"] else (None if cls["probable"] else False))))
        if cls["notes"]:
            out["match_note"] += "; " + "; ".join(cls["notes"])
    return out


def title_for_va(rec: dict) -> dict:
    out = _empty("none")
    out.update(match="none", match_note="no VA fetcher")
    return out


def _empty(source: str) -> dict:
    return {"owners": [], "owner_mailing": "", "premise": "", "sale_date": "", "sale_price": None,
            "ssl": "", "source": source, "match": "none", "match_note": "",
            "decedent_on_title": [], "decedent_probable": [], "pr_on_title": [], "co_owners_not_in_probate": [],
            "trust_vesting": False,
            "household_subject": None, "owner_of_record_is_decedent": None}


# ── SmartSkip household subject ─────────────────────────────────────────────

def household_subject(ranked: dict | None) -> dict | None:
    """The SmartSkip subject row when it carries NO first name: someone of the
    owner's surname at the address, age + lines -- a candidate co-owner living
    at the property. None when the subject is a named person or absent."""
    if not ranked:
        return None
    subj = ranked.get("subject") or {}
    if not subj or (subj.get("first") or "").strip():
        return None
    phones = subj.get("phones") or []
    lines = []
    for p in phones:
        n = (p.get("number") or "").strip() if isinstance(p, dict) else str(p)
        if n:
            lines.append(f"{n} ({p.get('type')})" if isinstance(p, dict) and p.get("type") else n)
    return {"last": subj.get("last") or "", "age": subj.get("age"), "n_lines": len(lines), "lines": lines,
            "deceased_flag": subj.get("deceased_flag")}


# ── driver ──────────────────────────────────────────────────────────────────

def load_keys(path: Path, pilot: bool, state: str | None) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        keys = list(data.keys()) if isinstance(data, dict) else [str(k) for k in data]
        return keys
    rows = list(csv.DictReader(text.splitlines()))
    if not rows:
        return []
    if "key" not in rows[0]:
        return [list(r.values())[0] for r in rows]
    out = []
    for r in rows:
        if state and (r.get("state") or "").upper() != state.upper():
            continue
        if pilot and not (r.get("group") != "grounded" or r.get("death_unverified") == "True"):
            continue
        out.append(r["key"])
    return out


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def run_one(key: str, rec: dict, ranked: dict | None, allow_firecrawl: bool, sleep: float) -> dict | None:
    st = (rec.get("state") or "").upper()
    if st == "DC":
        out = title_for_dc(rec, sleep=sleep)
    elif st == "MD":
        if not allow_firecrawl:
            return None  # skipped, not written
        out = title_for_md(rec.get("county") or "", rec.get("street") or "",
                           f"{rec.get('owner_first', '')} {rec.get('owner_last', '')}".strip(), rec)
    elif st == "VA":
        out = title_for_va(rec)
    else:
        out = _empty("none")
        out.update(match="none", match_note=f"unknown state '{st}'")
    out["household_subject"] = household_subject(ranked)
    out["street"] = rec.get("street") or ""
    out["checked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--keys-file", required=True, help="csv (column 'key') or json (list / dict) of keys")
    ap.add_argument("--pilot", action="store_true",
                    help="with groups.csv: keep rows where group != grounded or death_unverified == True")
    ap.add_argument("--state", default=None, help="only keys whose record state is this (DC / MD / VA)")
    ap.add_argument("--probe", nargs="?", const="__first__", default=None,
                    help="look up ONE key (default: the first), print the raw rows and the result, write it, stop")
    ap.add_argument("--force", action="store_true", help="redo keys already in title.json")
    ap.add_argument("--allow-firecrawl", action="store_true", help="MD: allow the paid SDAT scrape")
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel lookups for MD (Firecrawl) keys; DC keys always run one at a time")
    ap.add_argument("--reclassify", action="store_true",
                    help="no lookup: re-run classify_owners on every stored entry with owners (after a rule change)")
    ap.add_argument("--out", default=None, help="override output path (default <run-dir>/rerun_v5/title.json)")
    a = ap.parse_args(argv)

    run_dir = Path(a.run_dir)
    if not run_dir.is_absolute():
        run_dir = ROOT / run_dir
    out_path = Path(a.out) if a.out else run_dir / "rerun_v5" / "title.json"
    log_path = out_path.with_suffix(".log")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    records = json.loads((run_dir / "records.json").read_text(encoding="utf-8"))
    ranked_path = run_dir / "ranked_records.json"
    ranked = json.loads(ranked_path.read_text(encoding="utf-8")) if ranked_path.exists() else {}
    keys_path = Path(a.keys_file)
    if not keys_path.is_absolute():
        keys_path = ROOT / keys_path
    keys = load_keys(keys_path, a.pilot, None)
    keys = [k for k in keys if k in records]
    if a.state:
        keys = [k for k in keys if (records[k].get("state") or "").upper() == a.state.upper()]

    titles: dict = {}
    if out_path.exists():
        titles = json.loads(out_path.read_text(encoding="utf-8"))
    todo = [k for k in keys if a.force or k not in titles]
    print(f"keys: {len(keys)} selected, {len(titles)} already in {out_path.name}, {len(todo)} to do")

    if a.reclassify:
        changed = 0
        for k in keys:
            res = titles.get(k)
            if not res or not res.get("owners") or k not in records:
                continue
            cls = classify_owners(res["owners"], records[k])
            before = (res.get("decedent_on_title"), res.get("decedent_probable"), res.get("pr_on_title"),
                      res.get("co_owners_not_in_probate"), res.get("trust_vesting"), res.get("owner_of_record_is_decedent"))
            base = (res.get("match_note") or "").split(";")[0]
            res.update(decedent_on_title=cls["decedent"], decedent_probable=cls["probable"], pr_on_title=cls["pr"],
                       co_owners_not_in_probate=cls["co_owners_not_in_probate"], trust_vesting=cls["trust_vesting"],
                       owner_of_record_is_decedent=(True if cls["decedent"] else (None if cls["probable"] else False)),
                       match_note=base + ("; " + "; ".join(cls["notes"]) if cls["notes"] else ""))
            after = (res["decedent_on_title"], res["decedent_probable"], res["pr_on_title"],
                     res["co_owners_not_in_probate"], res["trust_vesting"], res["owner_of_record_is_decedent"])
            if before != after:
                changed += 1
                print(f"  reclassified {k:40s} {res['owners']} -> decedent={after[0]} probable={after[1]} co={after[3]}")
        _atomic_write(out_path, titles)
        with log_path.open("a", encoding="utf-8") as lf:
            lf.write(f"{datetime.now():%Y-%m-%d %H:%M} RECLASSIFY {changed} of {len(keys)} entries changed\n")
        print(f"reclassify: {changed} changed -> {out_path}")
        return 0

    if a.probe:
        k = todo[0] if a.probe == "__first__" else a.probe
        if k not in records:
            print(f"probe key not in records.json: {k}")
            return 2
        rec = records[k]
        print(f"PROBE {k}: {rec.get('street')} {rec.get('city')} {rec.get('state')} | owner "
              f"{rec.get('owner_first')} {rec.get('owner_last')} | pr {rec.get('pr') or '-'} | anchors "
              f"{(rec.get('anchors') or {}).get('pr_full') or '-'}")
        if (rec.get("state") or "").upper() == "DC":
            rows, how = itspe_rows_for_address(rec.get("street") or "", sleep=a.sleep)
            print(f"  {how} -> {len(rows)} row(s)")
            for r in rows[:10]:
                print("   ", json.dumps(r, ensure_ascii=False))
        res = run_one(k, rec, ranked.get(k), a.allow_firecrawl, a.sleep)
        print(json.dumps(res, indent=1, ensure_ascii=False))
        if res is not None:
            titles[k] = res
            _atomic_write(out_path, titles)
            with log_path.open("a", encoding="utf-8") as lf:
                lf.write(f"{datetime.now():%Y-%m-%d %H:%M} PROBE {k} {res['source']}/{res['match']} "
                         f"owners={res['owners']} note={res['match_note']}\n")
        return 0

    n_done = n_skip = n_fail = 0
    lock = threading.Lock()

    def _lookup(k: str):
        rec = records[k]
        try:
            return run_one(k, rec, ranked.get(k), a.allow_firecrawl, a.sleep)
        except Exception as e:  # noqa: BLE001
            res = _empty("none")
            res.update(match="none", match_note=f"lookup raised: {e}", street=rec.get("street") or "",
                       household_subject=household_subject(ranked.get(k)),
                       checked_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
            return res

    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"{datetime.now():%Y-%m-%d %H:%M} RUN {len(todo)} keys state={a.state} pilot={a.pilot} "
                 f"force={a.force} firecrawl={a.allow_firecrawl} workers={a.workers}\n")

        def _record(i: int, k: str, res) -> None:
            nonlocal n_done, n_skip, n_fail
            rec = records[k]
            with lock:
                if res is None:
                    n_skip += 1
                    lf.write(f"  SKIP {k} ({rec.get('state')}: needs --allow-firecrawl)\n")
                    return
                titles[k] = res
                _atomic_write(out_path, titles)
                ok = res["match"] == "address"
                n_done += ok
                n_fail += (not ok)
                flag = ""
                if res.get("co_owners_not_in_probate"):
                    flag += " CO-OWNER"
                if res.get("owner_of_record_is_decedent") is False:
                    flag += " NOT-DECEDENT"
                if res.get("decedent_probable"):
                    flag += " PROBABLE-SPELLING"
                if res.get("trust_vesting"):
                    flag += " TRUST"
                line = (f"[{i}/{len(todo)}] {k:40s} {res['source']}/{res['match']:7s} "
                        f"owners={res['owners']} sale={res['sale_date']}{flag}")
                print(line, flush=True)
                lf.write(f"  {line}\n    {res['match_note']}\n")
                lf.flush()

        serial = [k for k in todo if (records[k].get("state") or "").upper() != "MD" or a.workers <= 1]
        pooled = [k for k in todo if k not in set(serial)]
        for i, k in enumerate(serial, 1):
            _record(i, k, _lookup(k))
            if i < len(serial) and (records[k].get("state") or "").upper() == "DC":
                time.sleep(a.sleep)
        if pooled:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=a.workers) as ex:
                futs = {ex.submit(_lookup, k): k for k in pooled}
                for i, fut in enumerate(as_completed(futs), len(serial) + 1):
                    _record(i, futs[fut], fut.result())
        lf.write(f"{datetime.now():%Y-%m-%d %H:%M} END matched={n_done} no_match={n_fail} skipped={n_skip}\n")
    print(f"done: matched={n_done} no_match={n_fail} skipped(MD w/o --allow-firecrawl)={n_skip} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
