"""Read the cached Trestle Reverse Phone responses back against each ghost -> enrichment sheet.

No network, no spend: joins output/unknown_ghosts/trestle_reverse/<phone>.json to the ghost
evidence in ghost_name_resolution.json and decides, per ghost, who the number holder is:

  NEW PERSON        a same-surname holder whose age range fits the ghost and who is not the
                    owner, the decedent or a relative already named -> a name to write
  NAMESAKE SON      the holder carries the owner's name WITH a suffix (Jr./II/III) or a
                    different middle name, age fits the ghost, and the ghost is a generation
                    below the decedent -> the son SmartSkip could not name; a name to write
  = NAMED RELATIVE  the holder is a relative already on the record and the ages fit ->
                    SmartSkip listed one person twice; merge, do not rename
  OWNER'S OWN LINE  the holder is the owner/decedent (or spouse) and the ghost's age matches
                    theirs -> the ghost is the owner returned again; drop from signers
  AGE MISMATCH      same surname but no holder within the ghost's age band -> review
  NO SURNAME HIT    Trestle names other people on the number -> the number is not the ghost's

    python src/scripts/trestle_reverse_enrich.py            # every ghost with a cached query
Writes output/unknown_ghosts/trestle_enrichment.csv.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from obituary_dp_batch import names_match  # noqa: E402
from dp_ghost_name_resolver import OUT, age_int, has_suffix, jload, same_person, surname_of, suffix_of  # noqa: E402

CACHE_DIR = OUT / "trestle_reverse"
RESULTS = OUT / "ghost_name_resolution.json"
ENRICH = OUT / "trestle_enrichment.csv"
HEADER = ["verdict", "proposed_full", "resolver_tier", "resolver_proposed", "agrees_with_resolver", "line_kind",
          "ghost_name", "ghost_age", "key", "property", "live_rel_slot", "owner_name",
          "decedent_age", "holder_age_range", "holder_at_property", "holder_phones", "other_holders",
          "queried_numbers", "note"]
ORDER = ["NEW PERSON", "NAMESAKE SON", "= NAMED RELATIVE", "OWNER'S OWN LINE", "HOUSEHOLD ONLY", "AGE MISMATCH", "NO SURNAME HIT"]


def range_fit(age_range: str, age) -> str:
    g = age_int(age)
    ar = str(age_range or "")
    if not g or not ar or ar == "None":
        return ""
    if "+" in ar:
        return "yes" if g >= int(ar.split("+")[0]) - 3 else "no"
    m = re.match(r"(\d+)\s*-\s*(\d+)", ar)
    if not m:
        return ""
    return "yes" if int(m.group(1)) - 3 <= g <= int(m.group(2)) + 3 else "no"


def main() -> int:
    res = jload(RESULTS)
    if not res:
        sys.exit(f"{RESULTS} missing -- run dp_ghost_name_resolver.py")
    rows = []
    for r in res["records"]:
        line_kind = "unique"
        cached = [(ph, CACHE_DIR / f"{ph}.json") for ph in r.get("unique_phones") or []]
        cached = [(ph, p) for ph, p in cached if p.exists()]
        if not cached:
            cached = [(ph, CACHE_DIR / f"{ph}.json") for ph in r.get("shared_phones") or []]
            cached = [(ph, p) for ph, p in cached if p.exists()]
            line_kind = "shared"
        if not cached:
            continue
        surname = surname_of(r["ghost_name"])
        owner = r["owner_name"]
        named = r["named_relatives_detail"]
        dec = next((n for n in named if n["canon_rel"].startswith("Decedent")), None)
        dec_age = age_int(dec["age"]) if dec else None
        ghost_age = age_int(r["ghost_age"])
        holders: dict[str, dict] = {}
        others: set[str] = set()
        for ph, p in cached:
            body = json.loads(p.read_text(encoding="utf-8"))
            for h in body.get("owners") or []:
                nm = h.get("name") or ""
                if surname_of(nm) != surname:
                    if h.get("type") == "Person":
                        others.add(nm)
                    continue
                if line_kind == "shared" and (names_match(nm, owner) or (dec and names_match(nm, dec["name"]))
                                              or any(same_person(nm, n["name"]) for n in named)):
                    others.add(nm + " [named on record]")
                    continue          # the named relative legitimately holds a shared line
                addr = (h.get("current_addresses") or [{}])[0]
                at_prop = (addr.get("street_line_1") or "").lower()[:10] == r["property"].lower()[:10]
                e = holders.setdefault(nm, {"age": h.get("age_range") or "", "phones": set(), "at_prop": at_prop})
                e["phones"].add(ph)
                e["at_prop"] = e["at_prop"] or at_prop

        note = []
        if not holders:
            verdict, best, e = ("HOUSEHOLD ONLY" if line_kind == "shared" else "NO SURNAME HIT"), "", {"age": "", "phones": set(), "at_prop": False}
            note.append("number(s) belong to: " + ", ".join(sorted(others)) if others else "no person returned")
        else:
            # prefer a holder whose age range fits the ghost; then the one on most numbers
            # (when the owner's name appears both plain and as Jr./III at the same age, the
            #  suffixed spelling is the son -- prefer it)
            ranked = sorted(holders.items(), key=lambda kv: (range_fit(kv[1]["age"], ghost_age) != "yes",
                                                             not (names_match(kv[0], owner) and has_suffix(kv[0])),
                                                             -len(kv[1]["phones"]), kv[0]))
            best, e = ranked[0]
            fit = range_fit(e["age"], ghost_age)
            is_owner_name = names_match(best, owner) or (dec and names_match(best, dec["name"]))
            # a named relative is the SAME person only when the names match first+last, or
            # loosely AND the holder's age fits that relative (Cathleen Tram Anh Nguyen 31-35 is
            # not the spouse ANH NGUYEN, 66, however many tokens they share)
            twin = next((n for n in named if not n["canon_rel"].startswith("Decedent")
                         and (names_match(best, n["name"])
                              or (same_person(best, n["name"]) and range_fit(e["age"], n.get("age")) != "no"))), None)
            owner_generation = dec_age is not None and ghost_age is not None and abs(dec_age - ghost_age) <= 8
            if fit == "no":
                verdict = "AGE MISMATCH"
                note.append(f"best same-surname holder {best} is {e['age']}, ghost {ghost_age}")
            elif is_owner_name and (has_suffix(best) or suffix_of(best)) and not owner_generation:
                verdict = "NAMESAKE SON"
                note.append(f"carries the owner's name with suffix '{suffix_of(best)}'; decedent {dec_age}, ghost {ghost_age}")
            elif is_owner_name and not owner_generation and fit == "yes" and dec_age and ghost_age and dec_age - ghost_age >= 18:
                verdict = "NAMESAKE SON"
                note.append(f"owner's name, no suffix, but a generation younger than the decedent ({dec_age} vs {ghost_age})")
            elif is_owner_name:
                verdict = "OWNER'S OWN LINE"
                note.append(f"holder is the owner/decedent themself (decedent {dec_age}, ghost {ghost_age})")
            elif twin:
                if range_fit(e["age"], twin.get("age")) == "no" and fit == "yes":
                    verdict = "= NAMED RELATIVE"
                    note.append(f"same person as named {twin['name']} ({twin['canon_rel']}); SmartSkip's age {twin.get('age')} "
                                f"was wrong, Trestle says {e['age']} which fits the ghost -> merge")
                else:
                    verdict = "= NAMED RELATIVE"
                    note.append(f"same person as named {twin['name']} ({twin['canon_rel']}, {twin.get('age')}) -> merge")
            else:
                verdict = "NEW PERSON"
            if len(ranked) > 1:
                note.append("other same-surname holders: " + ", ".join(f"{n} ({x['age']})" for n, x in ranked[1:]))
        proposed = best if verdict in ("NEW PERSON", "NAMESAKE SON", "= NAMED RELATIVE") else ""
        rp = r.get("proposed_full") or ""
        agrees = ""
        if rp and proposed:
            agrees = "yes" if same_person(proposed, rp.split(" (a.k.a.")[0]) else "NO"
        elif rp and verdict in ("AGE MISMATCH", "NO SURNAME HIT", "OWNER'S OWN LINE", "HOUSEHOLD ONLY"):
            agrees = "unconfirmed"
        rows.append({"verdict": verdict, "proposed_full": proposed, "resolver_tier": r["confidence"],
                     "resolver_proposed": rp, "agrees_with_resolver": agrees, "line_kind": line_kind,
                     "ghost_name": r["ghost_name"], "ghost_age": r["ghost_age"], "key": r["key"], "property": r["property"],
                     "live_rel_slot": r["live_rel_slot"], "owner_name": owner, "decedent_age": dec_age or "",
                     "holder_age_range": e["age"], "holder_at_property": "yes" if e["at_prop"] else "",
                     "holder_phones": " ".join(sorted(e["phones"])), "other_holders": ", ".join(sorted(others)),
                     "queried_numbers": len(cached), "note": "; ".join(note)})
    rows.sort(key=lambda x: (ORDER.index(x["verdict"]), x["ghost_name"]))
    with open(ENRICH, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)
    from collections import Counter
    c = Counter(x["verdict"] for x in rows)
    print(f"{len(rows)} probed ghosts: " + ", ".join(f"{k} {c[k]}" for k in ORDER if c[k]))
    ag = Counter(x["agrees_with_resolver"] for x in rows if x["resolver_proposed"])
    print(f"against the resolver's {sum(ag.values())} proposals: " + ", ".join(f"{k or 'n/a'} {v}" for k, v in ag.items()))
    for x in rows:
        print(f"{x['verdict']:<17} {x['ghost_name']:<24}{x['ghost_age']:>3} -> {x['proposed_full'] or '-':<30} "
              f"{x['holder_age_range']:<6} {'@prop' if x['holder_at_property'] else '     '} "
              f"{('resolver ' + x['resolver_tier'] + ' ' + x['resolver_proposed'] + ' => ' + x['agrees_with_resolver']) if x['resolver_proposed'] else ''} | {x['note']}")
    print(f"\nwrote {ENRICH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
