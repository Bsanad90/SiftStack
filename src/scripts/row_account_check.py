"""row_account_check.py -- is a Register of Wills estate ALREADY on the DataSift account? READ-ONLY.

Che's manual list (row_review_pipeline.py finish) used to carry every estate the agents could not
settle, including estates already on the account under the decedent's name (Hisham's earlier
Register of Wills uploads, a SiftMap rung, an older pull). Basem, 2026-10-20: check the account
BEFORE the list goes to Che so he never spends time on a record that is already there.

Three lookups per estate, all `POST property/` with the GET override (the search query):
  1. decedent name -> `search` = the decedent's SURNAME. That is the only owner-name search
     DataSift answers (measured 2026-10-20: "Umberger" -> 2 hits, "Regina Umberger" -> 0,
     "UMBERGER REGINA" -> 0). Keep a row only when the owner's first+last matches the decedent
     (names_match_loose: nicknames, initials and suffixes tolerated, last token exact) AND the
     property sits in Maryland, in the estate's own county when data/zip_county_cache.json knows
     the ZIP. A namesake in DC / VA / another MD county is rejected.
  2. candidate street (rows the agents found but SDAT graded LOW) -> the street as given plus its
     abbreviated / unit-stripped forms (`search` is an EXACT match on the stored street string);
     keep the same house number + ZIP + compatible unit.
  3. each personal representative's name -> as 1. A hit here is only FLAGGED: the PR's own house
     is a lead, not the estate's property (row-manual-review rule).

Verdicts: ON_ACCOUNT (lookup 1 or 2 hit) / PR_ONLY (only lookup 3 hit) / NO / ERROR. A failed
call is ERROR, never NO ("an error is never 'not on the account'"); the caller decides what to
do with the row (finish keeps it on Che's list and says so).

  python src/scripts/row_account_check.py --day 2026-10-19          # that day's manual list
  python src/scripts/row_account_check.py --manual <csv> [--out <csv>]
Writes output/row_review/<day>/row_account_check_<day>.csv. Never writes to DataSift.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(REPO / "src")]

from obituary_dp_batch import name_tokens, names_match_loose, zip5  # noqa: E402

REVIEW_ROOT = REPO / "output" / "row_review"
ZIP_COUNTY_PATH = REPO / "data" / "zip_county_cache.json"
PAGE = 100
MAX_ROWS = 500   # a common surname (Smith) is paged to here, then reported as truncated
SUFFIXES = {"avenue": "Ave", "street": "St", "road": "Rd", "drive": "Dr", "court": "Ct",
            "lane": "Ln", "place": "Pl", "terrace": "Ter", "boulevard": "Blvd", "circle": "Cir",
            "parkway": "Pkwy", "highway": "Hwy", "square": "Sq", "trail": "Trl", "north": "N",
            "south": "S", "east": "E", "west": "W", "northeast": "NE", "northwest": "NW",
            "southeast": "SE", "southwest": "SW"}
SUFFIX_RE = re.compile(r"\b(" + "|".join(SUFFIXES) + r")\b", re.I)
UNIT_TAIL = re.compile(r"\s+(?:#|apt\.?|unit|ste\.?|suite)\s*\S+$", re.I)
UNIT_RE = re.compile(r"(?:#|\bapt\.?|\bunit|\bste\.?|\bsuite)\s*([a-z0-9-]+)", re.I)
ON_ACCOUNT, PR_ONLY, NO, ERROR = "ON_ACCOUNT", "PR_ONLY", "NO", "ERROR"
RESULT_COLUMNS = ["key", "verdict", "how", "account_street", "account_city", "account_zip",
                  "account_owner", "account_status", "account_uuid", "note"]

_zip_county: dict | None = None


def county_of_zip(z: str) -> str:
    """County name for a ZIP from the pulls' geocode cache, '' when unknown. Never geocodes."""
    global _zip_county
    if _zip_county is None:
        try:
            _zip_county = json.loads(ZIP_COUNTY_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _zip_county = {}
    return (_zip_county.get(zip5(z)) or "").strip()


def surname(name: str) -> str:
    """The search term for an owner-name lookup: the last real name token, '' for a one-token name."""
    toks = [t for t in name_tokens(name) if len(t) > 1] or name_tokens(name)
    return toks[-1] if len(toks) >= 2 else ""


def _house(street: str) -> str:
    m = re.match(r"\s*(\d+[a-z]?)\b", (street or "").lower())
    return m.group(1) if m else ""


def _unit(street: str) -> str:
    m = UNIT_RE.search(street or "")
    return m.group(1).lower() if m else ""


def street_terms(street: str, city: str = "") -> list[str]:
    """The stored-string variants to search for one street (search is an exact match)."""
    s = re.sub(r"\s+", " ", (street or "").replace(",", " ").replace(".", "")).strip()
    if city and s.lower().endswith(" " + city.lower()):
        s = s[: -len(city) - 1]
    short = SUFFIX_RE.sub(lambda m: SUFFIXES[m.group(1).lower()], s)
    return [t for t in dict.fromkeys([s, UNIT_TAIL.sub("", s), short, UNIT_TAIL.sub("", short)]) if t]


def owner_name(row: dict) -> str:
    o = row.get("owner") or {}
    return f"{o.get('first_name') or ''} {o.get('last_name') or ''}".strip()


ENTITY_NOISE = {"estate", "of", "the", "trust", "trustee", "trustees", "revocable", "living",
                "family", "irrevocable", "llc", "inc", "ttee", "etal", "et", "al"}


def company_matches(person: str, company: str) -> bool:
    """An entity owner such as 'Estate Of Harold Finch' / 'Finch Harold J Revocable Trust'
    names the person when the first AND last name tokens survive the entity words."""
    toks = [t for t in name_tokens(company) if t not in ENTITY_NOISE]
    if names_match_loose(person, " ".join(toks)):
        return True
    p = [t for t in name_tokens(person) if len(t) > 1] or name_tokens(person)
    return len(p) >= 2 and p[0] in toks and p[-1] in toks


def _hit(row: dict, how: str) -> dict:
    a = row.get("address") or {}
    o = row.get("owner") or {}
    return {"uuid": row.get("uuid", ""), "street": a.get("street") or "", "city": a.get("city") or "",
            "zip": zip5(a.get("zip5") or a.get("postal_code") or ""), "state": a.get("state") or "",
            "owner": owner_name(row) or (o.get("company") or ""), "status": row.get("status") or "",
            "how": how}


def _in_place(row: dict, county: str) -> bool:
    """Maryland, and the estate's county when the ZIP is known (namesake guard)."""
    a = row.get("address") or {}
    if (a.get("state") or "").upper() != "MD":
        return False
    c = county_of_zip(a.get("zip5") or a.get("postal_code") or "")
    return (not c) or (not county) or c.lower() == county.lower()


def name_hits(search, person: str, county: str, how: str) -> tuple[list[dict], str]:
    """Rows whose owner is `person` (first+last, loose) in the estate's county."""
    term = surname(person)
    if not term:
        return [], f"{how}: one-token name, not searched"
    rows, note = search(term)
    out = []
    for r in rows:
        o = r.get("owner") or {}
        named = names_match_loose(person, owner_name(r)) if owner_name(r) else False
        entity = company_matches(person, o["company"]) if o.get("company") else False
        if (named or entity) and _in_place(r, county):
            out.append(_hit(r, how))
    return out, note


def street_hits(search, street: str, city: str, zipc: str) -> tuple[list[dict], str]:
    zipc = zip5(zipc)
    out, notes = [], []
    for term in street_terms(street, city):
        rows, note = search(term)
        if note:
            notes.append(note)
        for r in rows:
            a = r.get("address") or {}
            rs = a.get("street") or ""
            if zip5(a.get("zip5") or a.get("postal_code") or "") == zipc and _house(rs) == _house(street) \
                    and _unit(rs) == _unit(street) and r.get("uuid") not in {h["uuid"] for h in out}:
                out.append(_hit(r, "candidate street"))
    return out, "; ".join(notes)


def has_address(rec: dict) -> bool:
    s = (rec.get("property_street") or "").strip()
    return bool(s) and "not found" not in s.lower()


def check_estate(rec: dict, search) -> dict:
    """One ledger estate -> {verdict, how, hits: [...], note}. `search(term) -> (rows, note)`."""
    county = rec.get("county") or ""
    decedent = rec.get("decedent_name") or ""
    hits, notes = [], []
    try:
        h, n = name_hits(search, decedent, county, "decedent name")
        hits += h
        if n:
            notes.append(n)
        if has_address(rec) and (rec.get("agent_review") or {}).get("status") == "FOUND":
            h, n = street_hits(search, rec["property_street"], rec.get("property_city") or "",
                               rec.get("property_zip") or "")
            hits += [x for x in h if x["uuid"] not in {y["uuid"] for y in hits}]
            if n:
                notes.append(n)
        pr_hits = []
        for pr in rec.get("personal_reps") or []:
            nm = (pr.get("name") or "").strip() if isinstance(pr, dict) else str(pr)
            if not nm or names_match_loose(nm, decedent):
                continue
            h, n = name_hits(search, nm, county, f"PR name {nm}")
            pr_hits += [x for x in h if x["uuid"] not in {y["uuid"] for y in hits + pr_hits}]
            if n:
                notes.append(n)
    except Exception as exc:  # noqa: BLE001 -- a failed call is never "not on the account"
        return {"verdict": ERROR, "how": "", "hits": [], "note": f"{type(exc).__name__}: {str(exc)[:160]}"}
    if hits:
        return {"verdict": ON_ACCOUNT, "how": " + ".join(dict.fromkeys(h["how"] for h in hits)),
                "hits": hits + pr_hits, "note": "; ".join(notes)}
    if pr_hits:
        return {"verdict": PR_ONLY, "how": " + ".join(dict.fromkeys(h["how"] for h in pr_hits)),
                "hits": pr_hits, "note": "; ".join(notes)}
    return {"verdict": NO, "how": "", "hits": [], "note": "; ".join(notes)}


def make_search(api):
    """Live `search(term)` over the property query, paged to MAX_ROWS. Raises on a non-200."""
    cache: dict[str, tuple[list[dict], str]] = {}

    def search(term: str) -> tuple[list[dict], str]:
        key = term.lower()
        if key in cache:
            return cache[key]
        rows, off, total = [], 0, None
        while True:
            st, body = api.post_as_get("/api/internal/property/",
                                       {"limit": PAGE, "offset": off, "query": {"must": {"search": term}}})
            if st != 200:
                raise RuntimeError(f"search HTTP {st} for {term!r}")
            page = body.get("results") or body.get("data") or []
            total = body.get("count") if total is None else total
            rows += page
            off += PAGE
            if len(page) < PAGE or off >= MAX_ROWS:
                break
        note = f"'{term}': {total} rows on the account, first {len(rows)} read" \
            if isinstance(total, int) and total > len(rows) else ""
        cache[key] = (rows, note)
        return cache[key]
    return search


def check_estates(estates: list[tuple[str, dict]], search) -> dict[str, dict]:
    return {key: check_estate(rec, search) for key, rec in estates}


def flat(key: str, res: dict) -> dict:
    hits = res.get("hits") or []
    j = lambda f: " ; ".join(str(h.get(f, "")) for h in hits)  # noqa: E731
    return {"key": key, "verdict": res["verdict"], "how": res.get("how", ""),
            "account_street": j("street"), "account_city": j("city"), "account_zip": j("zip"),
            "account_owner": j("owner"), "account_status": j("status"), "account_uuid": j("uuid"),
            "note": res.get("note", "")}


def summary_text(key: str, res: dict) -> str:
    """One short line for a list column / popup: where the account already has it."""
    hits = res.get("hits") or []
    if not hits:
        return ""
    h = hits[0]
    more = f" (+{len(hits) - 1} more)" if len(hits) > 1 else ""
    return f"{h['how']}: {h['owner']} at {h['street']}, {h['city']} {h['zip']} [{h['status'] or 'no status'}]{more}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--day", default=date.today().isoformat())
    ap.add_argument("--manual", help="a row_manual_review_<day>[_rN].csv (default: the day's)")
    ap.add_argument("--out", help="result CSV (default: output/row_review/<day>/row_account_check_<day>.csv)")
    a = ap.parse_args()
    import row_address_review as rar
    manual = Path(a.manual) if a.manual else REVIEW_ROOT / a.day / f"row_manual_review_{a.day}.csv"
    if not manual.exists():
        print(f"no manual list at {manual}")
        return 2
    with manual.open(newline="", encoding="utf-8-sig") as fh:
        keys = [r["key"] for r in csv.DictReader(fh)]
    estates = rar.load_ledger()["estates"]
    missing = [k for k in keys if k not in estates]
    if missing:
        print(f"{len(missing)} keys not in the ledger (skipped): {missing[:5]}")
    from dpd_lane_phone_score import Api
    search = make_search(Api())
    results = check_estates([(k, estates[k]) for k in keys if k in estates], search)
    out = Path(a.out) if a.out else manual.parent / f"row_account_check_{a.day}.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=RESULT_COLUMNS)
        w.writeheader()
        w.writerows(flat(k, r) for k, r in results.items())
    from collections import Counter
    c = Counter(r["verdict"] for r in results.values())
    print(f"{len(results)} estates: " + ", ".join(f"{v} {c.get(v, 0)}" for v in (ON_ACCOUNT, PR_ONLY, NO, ERROR)))
    for k, r in results.items():
        if r["verdict"] != NO:
            print(f"  {r['verdict']:10} {k:24} {estates[k].get('decedent_name', ''):28} {summary_text(k, r)}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
