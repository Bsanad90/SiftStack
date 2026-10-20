"""Offline guard for row_account_check (no DataSift calls): surname terms, street variants,
the namesake guard, the PR-only verdict, the candidate-street match, and the error path.

    python tools/manual/test_row_account_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src" / "scripts"), str(ROOT / "src")]

import row_account_check as rac  # noqa: E402

FAILS = []


def check(name: str, got, want) -> None:
    if got != want:
        FAILS.append(f"{name}: got {got!r}, want {want!r}")


def row(uuid, street, zipc, first, last, state="MD", city="Rockville", status="No Answer", company=None):
    return {"uuid": uuid, "status": status,
            "owner": {"first_name": first, "last_name": last, "company": company},
            "address": {"street": street, "city": city, "zip5": zipc, "postal_code": zipc, "state": state}}


# ZIP -> county cache stub (the real one is data/zip_county_cache.json)
rac._zip_county = {"20850": "Montgomery", "21401": "Anne Arundel", "21701": "Frederick",
                   "22207": "Arlington", "20001": "District of Columbia"}

# --- helpers
check("surname", rac.surname("KEITH DOUGLAS STICKLEY SR"), "stickley")
check("surname one token", rac.surname("MADONNA"), "")
check("surname initial", rac.surname("YVONNE E ENNIS"), "ennis")
check("street terms", rac.street_terms("11412 Emerald Park Road, Apt 3", "Potomac"),
      ["11412 Emerald Park Road Apt 3", "11412 Emerald Park Road", "11412 Emerald Park Rd Apt 3",
       "11412 Emerald Park Rd"])
check("street terms city tail", rac.street_terms("9600 Old Spring Road Kensington", "Kensington"),
      ["9600 Old Spring Road", "9600 Old Spring Rd"])
check("unit", rac._unit("1155 23rd St Nw Apt Ph1L"), "ph1l")
check("county of zip", rac.county_of_zip("20850-1234"), "Montgomery")
check("county unknown", rac.county_of_zip("99999"), "")

# --- a fake account
ACCOUNT = {
    "umberger": [row("u1", "10802 Coppermine Rd", "21701", "James", "Umberger"),
                 row("u2", "8130 Gas House Pike", "21701", "Regina", "Umberger"),
                 row("u3", "1 Namesake Ln", "22207", "Regina", "Umberger", state="VA", city="Arlington"),
                 row("u4", "2 Other County Rd", "20850", "Regina", "Umberger")],
    "smith": [row("s1", "5 Elm St", "21401", "Sally", "Smith"),
              row("s9", "9 Pr Home Ct", "21401", "Kanika", "Smith")],
    "11412 emerald park rd": [row("e1", "11412 Emerald Park Rd", "20854", "Adesunkanmi", "Balogun")],
    "estate": [row("x1", "7 Trust Way", "21701", "", "", company="Estate Of Harold Finch")],
    "finch": [row("x1", "7 Trust Way", "21701", "", "", company="Estate Of Harold Finch")],
}
rac._zip_county["20854"] = "Montgomery"
CALLS = []


def fake_search(term):
    CALLS.append(term)
    return ACCOUNT.get(term.lower(), []), ""


def estate(**kw):
    base = {"county": "Frederick", "decedent_name": "REGINA M. UMBERGER", "personal_reps": [],
            "property_street": "", "agent_review": {"status": "NOT_FOUND"}}
    base.update(kw)
    return base


# 1. decedent on the account in her county: ON_ACCOUNT, namesakes in VA / Montgomery rejected
r = rac.check_estate(estate(), fake_search)
check("decedent verdict", r["verdict"], rac.ON_ACCOUNT)
check("decedent hits", [h["uuid"] for h in r["hits"]], ["u2"])
check("decedent how", r["how"], "decedent name")
check("summary", rac.summary_text("k", r), "decedent name: Regina Umberger at 8130 Gas House Pike, Rockville 21701 [No Answer]")

# 2. the PR's own record only: PR_ONLY (flag, never a skip); the decedent-as-PR is not re-searched
r = rac.check_estate(estate(county="Anne Arundel", decedent_name="KHAMANI DEJOHN HICKS",
                            personal_reps=[{"name": "KANIKA SMITH"}, {"name": "KHAMANI HICKS"}]), fake_search)
check("pr-only verdict", r["verdict"], rac.PR_ONLY)
check("pr-only hits", [h["uuid"] for h in r["hits"]], ["s9"])
check("pr-only how", r["how"], "PR name KANIKA SMITH")

# 3. nothing anywhere: NO
r = rac.check_estate(estate(decedent_name="NOBODY HERE"), fake_search)
check("no verdict", r["verdict"], rac.NO)

# 4. candidate street (agent FOUND, graded LOW) matches house + zip: ON_ACCOUNT by street
r = rac.check_estate(estate(county="Montgomery", decedent_name="AFUSAT A BALOGUN", property_street="11412 Emerald Park Road",
                            property_city="Potomac", property_zip="20854", property_lookup_confidence="LOW",
                            agent_review={"status": "FOUND"}), fake_search)
check("street verdict", r["verdict"], rac.ON_ACCOUNT)
check("street how", r["how"], "candidate street")
check("street hits", [h["uuid"] for h in r["hits"]], ["e1"])

# 4b. the same street is NOT searched when the agents did not find it (NOT_FOUND rows carry no street)
CALLS.clear()
rac.check_estate(estate(decedent_name="AFUSAT A BALOGUN", property_street="Not Found (Claude)",
                        agent_review={"status": "NOT_FOUND"}), fake_search)
check("not-found street skipped", [c for c in CALLS if "emerald" in c.lower()], [])

# 5. an estate held as a company owner ("Estate Of ...") still matches on the company string
r = rac.check_estate(estate(decedent_name="HAROLD J FINCH"), fake_search)
check("company verdict", r["verdict"], rac.ON_ACCOUNT)

# 6. a failed call is ERROR, never NO
def dead_search(term):
    raise RuntimeError("search HTTP 502 for 'x'")
r = rac.check_estate(estate(), dead_search)
check("error verdict", r["verdict"], rac.ERROR)
check("error note", r["note"].startswith("RuntimeError: search HTTP 502"), True)

# 7. ZIP unknown to the cache: Maryland is enough; another state is not
rac._zip_county["21999"] = ""
r = rac.check_estate(estate(decedent_name="SALLY SMITH", county="Frederick"), fake_search)
check("unknown zip county -> state only", r["verdict"], rac.NO)   # 21401 = Anne Arundel, estate Frederick
r = rac.check_estate(estate(decedent_name="SALLY SMITH", county="Anne Arundel"), fake_search)
check("same county", r["verdict"], rac.ON_ACCOUNT)

# 8. flat row for the CSV
f = rac.flat("Frederick|52945", rac.check_estate(estate(), fake_search))
check("flat keys", list(f), rac.RESULT_COLUMNS)
check("flat uuid", f["account_uuid"], "u2")

if FAILS:
    print("\n".join(FAILS))
    print(f"FAILED {len(FAILS)}")
    sys.exit(1)
print("row_account_check: 26 checks OK")
