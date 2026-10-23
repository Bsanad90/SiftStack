"""Probate PR / legatee mailing from our own pull ledgers (Basem, 2026-10-16).

Fills the Register of Wills + DC probate gap of the heir mailing list
(`heir_mail_build.py`), which only holds estates that went through a deep-prospecting
batch. The court already names who must sign: the MD ledger carries every personal
representative with an address, the DC ledger the PR / applicant and every heir /
legatee / beneficiary with an address (portal Parties). Mailing them needs no SmartSkip.

  cohort   offline: ledgers -> estates opened since --filed-since whose property the
           DECEDENT owns (MD `HIGH` = deed confirmed by SDAT; DC "Decedent OWNS residence"
           at HIGH, or the ITSPE owner-name match at HIGH). Everything weaker goes to
           review.csv with the reason, never to the mail file.
  account  live, READ-ONLY: each cohort property is searched on the account (`search` =
           exact stored street, so the unit-stripped street is tried too) and every match
           hydrated (status, lists, tags, directmail_attempts). Resumable.
  build    pieces: one per DOOR (everyone the court lists at one address shares an
           envelope, `joint_line`), plus the household "The Smiths" piece to the property
           unless a court party lives there. Drops a property that is in the heir list
           already, previously mailed (Probate / Inheritance / Divorce list, any attempt,
           any Drive SENT file -- the heir list's three rules) or suppressed (dead status,
           do-not-mail, Recently Sold).

Nothing is written to DataSift, the ledgers or Drive.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

import mailed_history  # noqa: E402
import obituary_mail_consolidate as omc  # noqa: E402
from dpd_lane_phone_score import Api, jload, jsave, say  # noqa: E402
from obituary_dp_batch import sift_key, zip5  # noqa: E402

RUN = os.environ.get("PR_MAIL_RUN", date.today().strftime("%Y%m%d"))
OUT = ROOT / "output" / f"probate_pr_mail_{RUN}"
MD_LEDGER = ROOT / "output" / "md_row_ledger.json"
DC_LEDGER = ROOT / "output" / "dc_probate_ledger.json"
COHORT = OUT / "cohort.json"
REVIEW_CSV = OUT / "review.csv"
ACCOUNT = OUT / "account.json"
MAIL_CSV = OUT / "pr_mailing_mail_ready.csv"
EXCLUDED_CSV = OUT / "excluded.csv"
AUDIT = OUT / "audit.json"

FOOTPRINT_MD = {"Montgomery", "Anne Arundel", "Frederick"}
# DC party roles that are a signer or the person the court put in charge
DC_ROLES = {"personal representative", "applicant", "special administrator", "heir",
            "legatee", "beneficiary", "petitioner"}
ENTITY_RE = re.compile(r"\b(bank|trust company|llc|inc|corp|n\.?a\.?|esq|attorney|law|"
                       r"office|foundation|church|university|hospital|society|fund)\b", re.I)
MAX_BENEFICIARIES = 5
SUFFIXES = {"avenue": "Ave", "street": "St", "road": "Rd", "drive": "Dr", "court": "Ct",
            "lane": "Ln", "place": "Pl", "terrace": "Ter", "boulevard": "Blvd", "circle": "Cir",
            "parkway": "Pkwy", "highway": "Hwy", "square": "Sq", "trail": "Trl", "north": "N",
            "south": "S", "east": "E", "west": "W", "northeast": "NE", "northwest": "NW",
            "southeast": "SE", "southwest": "SW"}
SUFFIX_RE = re.compile(r"\b(" + "|".join(SUFFIXES) + r")\b", re.I)
UNIT_TAIL = re.compile(r"\s+(?:#|apt\.?|unit|ste\.?|suite)\s*\S+$", re.I)


def _date(s) -> date | None:
    s = (s or "").strip()[:10]
    for f in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def flip(name: str) -> str:
    """DC portal 'Wilson, Stephen G' -> 'Stephen G Wilson'; MD names are already in order."""
    name = (name or "").strip()
    if "," in name:
        last, _, first = name.partition(",")
        name = f"{first.strip()} {last.strip()}"
    return omc.title_name(re.sub(r"\s+", " ", name))


def surname(rec: dict) -> str:
    """The base tool's cleaner: a last-name field of "SMITH III" must not give "The Iiis"."""
    last = omc.surname_of(rec.get("decedent_last_name") or "") \
        or omc.surname_of(flip(rec.get("decedent_name") or ""))
    return omc.title_name(last)


def mail_street(street: str) -> str:
    """'4700 Connecticut Avenue, Unit 504, NW' -> no commas on the envelope line."""
    return omc.title_street(re.sub(r"\s*,\s*", " ", street or ""))


def party(name, street, city, state, zipc, role) -> dict | None:
    if not (name or "").strip() or not omc.has_house_number(street or ""):
        return None
    return {"name": flip(name), "role": role, "street": mail_street(street),
            "city": omc.title_name(city or ""), "state": (state or "").upper(),
            "zip": zip5(zipc or ""), "entity": bool(ENTITY_RE.search(name or ""))}


# ------------------------------------------------------------------ cohort

def md_estates(since: date) -> tuple[list[dict], list[dict]]:
    keep, review = [], []
    for e in json.loads(MD_LEDGER.read_text(encoding="utf-8"))["estates"].values():
        opened = _date(e.get("date_opened")) or _date(e.get("filing_date")) \
            or _date(e.get("published_on"))
        base = {"kind": "row_md", "id": f"MD {e.get('county')} {e.get('estate_number')}",
                "decedent": omc.title_name(e.get("decedent_name") or ""),
                "surname": surname(e), "dod": e.get("date_of_death") or "",
                "opened": opened.isoformat() if opened else "",
                "estate_status": e.get("status") or "", "county": e.get("county") or "",
                "street": e.get("property_street") or "", "city": e.get("property_city") or "",
                "state": e.get("property_state") or "MD", "zip": zip5(e.get("property_zip") or ""),
                "basis": f"{e.get('property_lookup_confidence')} / {e.get('property_lookup_source')}"}
        if not opened or opened < since:
            continue
        conf = e.get("property_lookup_confidence")
        street = base["street"]
        if conf != "HIGH" or not street or "not found" in street.lower():
            if conf in ("MEDIUM", "LOW") and street and "not found" not in street.lower():
                review.append(dict(base, reason=f"ownership {conf}: {e.get('property_lookup_reason') or ''}"[:300]))
            continue
        if base["county"] not in FOOTPRINT_MD:
            continue
        people, seen = [], set()
        prs = list(e.get("personal_reps") or [])
        pa = e.get("pr_address") or {}
        if e.get("pr_name") and pa.get("street"):
            prs.append({"name": e["pr_name"], **pa})
        prs += [p for p in (e.get("extra_prs") or []) if isinstance(p, dict)]
        for p in prs:
            pp = party(p.get("name"), p.get("street"), p.get("city"), p.get("state"),
                       p.get("zip"), "Personal Representative")
            if pp and (pp["name"].lower(), sift_key(pp["street"], pp["zip"])) not in seen:
                seen.add((pp["name"].lower(), sift_key(pp["street"], pp["zip"])))
                people.append(pp)
        keep.append(dict(base, people=people, foreign_pr=bool(e.get("foreign_pr"))))
    return keep, review


def dc_estates(since: date) -> tuple[list[dict], list[dict]]:
    keep, review = [], []
    for e in json.loads(DC_LEDGER.read_text(encoding="utf-8"))["estates"].values():
        filed = _date(e.get("filing_date"))
        if not filed or filed < since:
            continue
        own = e.get("property_ownership") or ""
        conf = e.get("property_lookup_confidence")
        src = e.get("property_lookup_source") or ""
        base = {"kind": "dc_probate", "id": e.get("case_no") or "",
                "decedent": flip(e.get("decedent_name") or ""), "surname": surname(e),
                "dod": e.get("dod") or "", "opened": filed.isoformat(),
                "estate_status": e.get("status") or "", "county": "District Of Columbia",
                "street": e.get("property_street") or "", "city": e.get("property_city") or "Washington",
                "state": "DC", "zip": zip5(e.get("property_zip") or ""),
                "basis": f"{conf} / {src} / {own[:60]}"}
        owns = conf == "HIGH" and (own.startswith("Decedent OWNS residence") or src == "ITSPE owner-name")
        if not owns:
            if base["street"] and conf in ("HIGH", "MEDIUM", "FAMILY", "REVIEW", "HAND"):
                review.append(dict(base, reason=f"{conf}: {own or e.get('property_lookup_reason') or ''}"[:300]))
            continue
        people, seen = [], set()
        for p in (e.get("personal_reps") or []) + (e.get("heirs") or []):
            role = (p.get("connection") or "").strip()
            if role and role.lower() not in DC_ROLES:
                continue
            pp = party(p.get("name"), p.get("street"), p.get("city"), p.get("state"),
                       p.get("zip"), role or "Party")
            if pp and (pp["name"].lower(), sift_key(pp["street"], pp["zip"])) not in seen:
                seen.add((pp["name"].lower(), sift_key(pp["street"], pp["zip"])))
                people.append(pp)
        # a will's long bequest list (2026-ADM-001109: 41 beneficiaries) does not sign a
        # sale -- the PR does; keep PR / heirs / legatees and say so
        many = sum(p["role"].lower() == "beneficiary" for p in people) > MAX_BENEFICIARIES
        if many:
            people = [p for p in people if p["role"].lower() != "beneficiary"]
        keep.append(dict(base, people=people, beneficiaries_dropped=many))
    return keep, review


def cmd_cohort(a) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    since = _date(a.filed_since)
    md, md_r = md_estates(since)
    dc, dc_r = dc_estates(since)
    jsave(COHORT, {"filed_since": since.isoformat(), "built": datetime.now().isoformat(timespec="seconds"),
                   "estates": md + dc})
    with REVIEW_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        cols = ["kind", "id", "decedent", "dod", "opened", "street", "city", "state", "zip", "basis", "reason"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(md_r + dc_r)
    say(f"cohort: MD {len(md)} (+{len(md_r)} review), DC {len(dc)} (+{len(dc_r)} review); "
        f"estates with no court party address: {sum(not e['people'] for e in md + dc)}")
    say(f"-> {COHORT}\n-> {REVIEW_CSV}")
    return 0


# ------------------------------------------------------------------ account (read-only)

def _house(street: str) -> str:
    m = re.match(r"\s*(\d+[a-z]?)\b", (street or "").lower())
    return m.group(1) if m else ""


def find_on_account(api: Api, street: str, zipc: str, city: str = "") -> list[str]:
    """uuids of account records at this property. `search` is an exact match on the
    stored street string, so try as given and unit-stripped; accept a hit only on the
    same house number + ZIP and a compatible unit."""
    out = []
    # DC "530 Oklahoma Avenue, NE", "661 Emerson St. NE": the stored form has neither
    street = re.sub(r"\s+", " ", street.replace(",", " ").replace(".", "")).strip()
    if city and street.lower().endswith(" " + city.lower()):   # "9600 Old Spring Road Kensington"
        street = street[: -len(city) - 1]
    short = SUFFIX_RE.sub(lambda m: SUFFIXES[m.group(1).lower()], street)   # stored as "Ave"
    terms = [street, UNIT_TAIL.sub("", street), short, UNIT_TAIL.sub("", short)]
    for term in dict.fromkeys(terms):
        st, body = api.post_as_get("/api/internal/property/",
                                   {"limit": 25, "offset": 0, "query": {"must": {"search": term}}})
        if st != 200:
            raise RuntimeError(f"search {st} for {term!r}")
        for r in body.get("results") or body.get("data") or []:
            ad = r.get("address") or {}
            rs = ad.get("street") or r.get("street") or ""
            rz = zip5(ad.get("zip5") or ad.get("postal_code") or r.get("zip") or "")
            if rz == zipc and _house(rs) == _house(street) \
                    and mailed_history.units_compatible(street, rs) and r.get("uuid") not in out:
                out.append(r["uuid"])
    return out


def cmd_account(a) -> int:
    co = jload(COHORT, {})
    if not co:
        say("no cohort -- run `cohort` first")
        return 2
    acct = jload(ACCOUNT, {}) if not a.fresh else {}
    api = Api()
    todo = [e for e in co["estates"] if sift_key(e["street"], e["zip"]) not in acct
            or (a.retry_misses and not acct[sift_key(e["street"], e["zip"])]["uuids"])]
    say(f"{len(todo)} properties to look up ({len(acct)} cached)")
    errors = 0
    for i, e in enumerate(todo, 1):
        k = sift_key(e["street"], e["zip"])
        try:
            uuids = find_on_account(api, e["street"], e["zip"], e.get("city") or "")
            acct[k] = {"uuids": uuids, "records": {u: omc.hydrate_one(api, u) for u in uuids}}
            errors = 0
        except Exception as exc:  # noqa: BLE001 -- an error is never "not on the account"
            errors += 1
            say(f"  {e['street']}: {str(exc)[:120]}")
            if errors >= 5:
                say("5 consecutive errors -- stopping (resumable)")
                jsave(ACCOUNT, acct)
                return 3
        if i % 25 == 0:
            jsave(ACCOUNT, acct)
            say(f"  {i}/{len(todo)}")
    jsave(ACCOUNT, acct)
    on = sum(bool(v["uuids"]) for v in acct.values())
    say(f"done: {len(acct)} looked up, {on} on the account -> {ACCOUNT}")
    return 0


# ------------------------------------------------------------------ build

def heir_list_keys(paths: list[str]) -> set:
    keys = set()
    for p in paths:
        for r in csv.DictReader(open(p, encoding="utf-8-sig")):
            keys.add(sift_key(r.get("property_street") or "", r.get("property_zip") or ""))
    return keys


def cmd_build(a) -> int:
    co = jload(COHORT, {})
    acct = jload(ACCOUNT, None)
    if not co or acct is None:
        say("run `cohort` and `account` first")
        return 2
    index = mailed_history.load_index()
    heir = heir_list_keys(a.heir_list or [])
    rows, excluded, tally = [], [], Counter()
    # two ledger entries can name one property (a re-filed estate, an MD + legal-notice
    # pair): one property = one set of envelopes, court parties unioned
    by_prop: dict[str, dict] = {}
    for e in co["estates"]:
        k = sift_key(e["street"], e["zip"])
        if k not in by_prop:
            by_prop[k] = dict(e, people=list(e["people"]))
            continue
        have = {(p["name"].lower(), sift_key(p["street"], p["zip"])) for p in by_prop[k]["people"]}
        by_prop[k]["people"] += [p for p in e["people"]
                                 if (p["name"].lower(), sift_key(p["street"], p["zip"])) not in have]
        by_prop[k]["id"] += f"; {e['id']}"
    for k, e in by_prop.items():
        why = None
        if k not in acct:
            why = "not looked up on the account (run `account`)"
        elif k in heir:
            why = "already in the heir mailing list"
        recs = (acct.get(k) or {}).get("records") or {}
        if not why:
            for u, rec in recs.items():
                why = omc.suppression_reason(rec)
                if why:
                    break
                reason, _ = omc.previously_mailed(rec, omc.mail_attempts(rec, None), [])
                if reason:
                    why = reason
                    break
        if not why:
            hits = mailed_history.lookup(index, e["street"], e["zip"])
            if hits:
                why = "previously mailed: drive=" + mailed_history.describe(hits)
        if why:
            tally[why.split(":")[0]] += 1
            excluded.append({"kind": e["kind"], "id": e["id"], "decedent": e["decedent"],
                             "street": e["street"], "zip": e["zip"], "reason": why})
            continue
        tally[f"kept {e['kind']}"] += 1
        common = {"decedent_name": e["decedent"], "property_street": mail_street(e["street"]),
                  "property_city": omc.title_name(e["city"]), "property_state": e["state"],
                  "property_zip": e["zip"], "county": e["county"], "record_kind": e["kind"],
                  "estate_id": e["id"], "filed_date": e["opened"], "dod": e["dod"],
                  "estate_status": e["estate_status"], "ownership_basis": e["basis"],
                  "on_account": "yes" if recs else "no", "record_uuid": ";".join(recs)}
        doors: dict[str, list[dict]] = {}
        for p in e["people"]:
            doors.setdefault(sift_key(p["street"], p["zip"]), []).append(p)
        resident = k in doors
        for dk, ps in doors.items():
            line, sal = omc.joint_line([p["name"] for p in ps])
            flags = []
            if dk == k:
                flags.append("resides_at_property")
            if any(p["entity"] for p in ps):
                flags.append("entity_or_attorney")
            if e.get("foreign_pr"):
                flags.append("foreign_pr")
            if e.get("beneficiaries_dropped"):
                flags.append("beneficiaries_not_mailed")
            if e["estate_status"].upper() in ("CLOSED", "ARCHIVED"):
                flags.append("estate_closed")
            rows.append({**common, "mail_to": line, "salutation": sal,
                         "addressee_type": "court_party",
                         "relationship": "; ".join(f"{p['name']} ({p['role']})" for p in ps),
                         "mail_street": ps[0]["street"], "mail_city": ps[0]["city"],
                         "mail_state": ps[0]["state"], "mail_zip": ps[0]["zip"],
                         "flags": "; ".join(flags)})
        if not resident and e["surname"]:
            hh = omc.household_name(e["surname"])
            rows.append({**common, "mail_to": hh, "salutation": f"Dear {e['surname']} Family",
                         "addressee_type": "household", "relationship": "",
                         "mail_street": common["property_street"], "mail_city": common["property_city"],
                         "mail_state": e["state"], "mail_zip": e["zip"],
                         "flags": "estate_closed" if e["estate_status"].upper() in ("CLOSED", "ARCHIVED") else ""})
    cols = ["piece_no", "decedent_name", "mail_to", "salutation", "addressee_type", "relationship",
            "mail_street", "mail_city", "mail_state", "mail_zip", "property_street", "property_city",
            "property_state", "property_zip", "county", "record_kind", "estate_id", "filed_date", "dod",
            "estate_status", "ownership_basis", "on_account", "flags", "record_uuid"]
    for i, r in enumerate(rows, 1):
        r["piece_no"] = i
    with MAIL_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    with EXCLUDED_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["kind", "id", "decedent", "street", "zip", "reason"])
        w.writeheader()
        w.writerows(excluded)
    props = {(r["record_kind"], r["property_street"], r["property_zip"]) for r in rows}
    audit = {"built": datetime.now().isoformat(timespec="seconds"), "filed_since": co["filed_since"],
             "estates": len(co["estates"]), "tally": dict(tally), "pieces": len(rows),
             "properties": len(props),
             "pieces_by_kind": dict(Counter(r["record_kind"] for r in rows)),
             "pieces_by_type": dict(Counter(r["addressee_type"] for r in rows)),
             "heir_lists": a.heir_list or [],
             "drive_index": {"files": index.get("files"), "range": index.get("date_range")}}
    jsave(AUDIT, audit)
    say(json.dumps(audit, indent=1))
    say(f"-> {MAIL_CSV}\n-> {EXCLUDED_CSV}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cohort")
    c.add_argument("--filed-since", default="2026-07-01")
    c.set_defaults(fn=cmd_cohort)
    c = sub.add_parser("account")
    c.add_argument("--fresh", action="store_true")
    c.add_argument("--retry-misses", action="store_true", help="look up again every property that matched nothing")
    c.set_defaults(fn=cmd_account)
    c = sub.add_parser("build")
    c.add_argument("--heir-list", action="append", help="heir mail_ready CSV(s); their properties are skipped")
    c.set_defaults(fn=cmd_build)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
