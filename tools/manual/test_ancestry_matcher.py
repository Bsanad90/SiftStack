"""Offline regression checks for ancestry_enricher's matching layer (no browser, no account).

Run after ANY change to `_location_matches`, `STATE_NAMES`, `CITY_COUNTY`, `_years_ok`
or `_match_basis`:

    python tools/manual/test_ancestry_matcher.py

Traps these encode (all measured live 2026-09-18):
  - the matcher knew six states and no DMV ones, so every MD/DC result was "wrong state";
  - "md"/"dc" as free substrings match inside words -- the code must be a delimited token;
  - Knoxville MD (Frederick) and Knoxville TN (Knox) must never collide;
  - a county match alone is not an identity: Ronald B. Zimmerman 1933-2020 Glen Burnie is a
    different man from the owner who died 2026-07-31, and only a year hint tells them apart;
  - a candidate with NO year on the disputed axis is unconfirmed, never rejected.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from ancestry_enricher import (  # noqa: E402
    CITY_COUNTY, _location_matches, _match_basis, _year_of, _years_ok, state_full_name,
)

FAILS = 0


def check(label, got, expected):
    global FAILS
    ok = got == expected
    FAILS += not ok
    print(("ok  " if ok else "BAD ") + label, "->", got, "" if ok else f"(expected {expected})")


LOCATION_CASES = [
    # (location string as Ancestry prints it, state, city, expected (state_ok, score))
    ("Glen Burnie, Anne Arundel, Maryland, USA", "MD", "Glen Burnie", (True, 2)),
    ("Glen Burnie, Anne Arundel County, Maryland, United States of America", "MD", "Glen Burnie", (True, 2)),
    ("Annapolis, Anne Arundel, Maryland", "MD", "Glen Burnie", (True, 2)),   # same county, other city
    ("Aberdeen, MD.", "MD", "Glen Burnie", (True, 1)),                       # right state, other county
    ("Aberdeen, Harford, Maryland, USA", "MD", "Glen Burnie", (True, 1)),
    ("Oroville, Butte, California, USA", "MD", "Glen Burnie", (False, 0)),
    ("Washington, District of Columbia, USA", "DC", "Washington", (True, 2)),
    ("Washington, DC", "DC", "Washington", (True, 2)),
    ("Washington, King, Washington, USA", "DC", "Washington", (False, 0)),   # the other Washington
    ("Bethesda, Montgomery, Maryland, USA", "MD", "Silver Spring", (True, 2)),
    ("McLean, Fairfax, Virginia, USA", "VA", "Vienna", (True, 2)),
    ("Richmond, Virginia, USA", "VA", "Vienna", (True, 1)),
    ("Arlington, Arlington, Virginia, USA", "VA", "Arlington", (True, 2)),
    ("Knoxville, Frederick, Maryland, USA", "MD", "Frederick", (True, 2)),
    ("Knoxville, Knox, Tennessee, USA", "TN", "Knoxville", (True, 2)),
    ("Knoxville, Knox, Tennessee, USA", "MD", "Frederick", (False, 0)),
    ("", "MD", "Glen Burnie", (True, 0)),                                     # no location = unknown
    ("xxxxxxxx", "MD", "Glen Burnie", (True, 0)),                             # masked location
    ("Amdahl, Some, Nebraska", "MD", "Glen Burnie", (False, 0)),              # 'md' inside a word
    ("Glen Burnie, Maryland", "MD", "", (True, 1)),                           # no target city
]

for loc, st, city, exp in LOCATION_CASES:
    check(f"loc {loc!r} {st} {city!r}", _location_matches(loc, st, city), exp)

check("state_full_name MD", state_full_name("MD"), "Maryland")
check("state_full_name DC", state_full_name("DC"), "District Of Columbia")
check("state_full_name ''", state_full_name(""), "")

check("footprint cities present",
      all(c in CITY_COUNTY["MD"] for c in ("glen burnie", "bethesda", "frederick"))
      and "washington" in CITY_COUNTY["DC"]
      and all(c in CITY_COUNTY["VA"] for c in ("arlington", "fairfax", "mclean")), True)

check("year_of full date", _year_of("22 Dec 1998"), 1998)
check("year_of year only", _year_of("2020"), 2020)
check("year_of masked", _year_of("xx xxx xxxx"), 0)

check("years_ok: 2020 death vs 2026 hint", _years_ok("17 Nov 1933", "4 Apr 2020", None, 2026),
      (False, "death 2020 vs expected 2026"))
check("years_ok: death within tol", _years_ok("17 Nov 1933", "4 Apr 2020", None, 2021), (True, ""))
check("years_ok: birth within tol", _years_ok("17 Nov 1933", "4 Apr 2020", 1934, None), (True, ""))
check("years_ok: birth contradiction", _years_ok("17 Nov 1933", "4 Apr 2020", 1950, None),
      (False, "birth 1933 vs expected 1950"))
check("years_ok: no year = unconfirmed, not rejected", _years_ok("", "", 1950, 2026), (True, ""))
check("years_ok: masked SSDI birth", _years_ok("x xxx 1940", "xx xxx 1996", 1940, None), (True, ""))

check("basis: county only", _match_basis("Ronald B. Zimmerman", "1933", "2020", 2, "", None, None), ["county"])
check("basis: everything", _match_basis("Ronald B. Zimmerman", "1933", "2020", 2, "B", 1933, 2020),
      ["county", "middle_initial", "birth_year", "death_year"])
check("basis: state + death", _match_basis("Ronald Zimmerman", "", "2020", 1, "B", None, 2020),
      ["state", "death_year"])
check("basis: nothing", _match_basis("Ronald Zimmerman", "", "", 0, "", None, None), [])

print(f"\n{'ALL PASSED' if not FAILS else str(FAILS) + ' FAILURES'}")
sys.exit(1 if FAILS else 0)
