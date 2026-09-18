"""Offline checks for src/scripts/dp_owner_died_namesake_audit.py. No network, no run dir.

Run after ANY change to recommend / extract_age_facts / extract_place / place_relation /
birth_year_gap / shell_override, the way test_ghost_name_resolver.py guards the resolver.
Each fixture is a live shape:

  1. 8131 Turn Loop Rd (2026-09-18): legacy.com title city Glen Burnie (the funeral home), body
     "of Baltimore", 68 vs SmartSkip 73, a 6,000-char match:false page before a 2,936-char
     match:true one -> LIKELY_FALSE. With the SDAT roll ZIMMERMAN RONALD L the initial
     conflicts and name_match_level says MEDIUM (never a conflict on its own).
  2. 15 Disney Ave (Herbert M. Dolch Sr.): initial match, birth-year gap 1, city match,
     two SmartSkip relatives confirmed by the obituary -> CLEAN.
  3. Shell-override alone -> REVIEW, nothing else flagged.
  4. Empty snippet, no obituary_url -> NO_SIGNAL and no exception.
  5. Re-rooted record: the decedent's SmartSkip age (80) is compared, not the namesake child's (45).
  6. Sr on the obituary against Jr on SmartSkip -> suffix conflict -> at least REVIEW.
  7. The regex shapes seen in the stored snippets, sdat_county, age_relation, title_city.

  python tools/manual/test_namesake_audit.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

import dp_owner_died_namesake_audit as m  # noqa: E402

FAILS = 0


def check(label, cond):
    global FAILS
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        FAILS += 1


def finding(owner, decedent, url, title, snippet, pages, **kw):
    trail = [{"name": owner, "hits": [{"url": url, "title": title, "snippet": snippet}] if url else []}]
    trail += [{"url": p.get("url", f"https://x.example/{i}"), "chars": p["chars"], "match": p["match"], "error": None}
              for i, p in enumerate(pages)]
    f = {"record": kw.get("key", "1mainst|21061"), "owner_name": owner, "verdict": "owner did die",
         "decedent_name": decedent, "obituary_url": url, "dod": kw.get("dod", "2026-07-31"),
         "dod_source": kw.get("dod_source", "obituary"), "confidence": kw.get("confidence", "high"),
         "survivors": [], "preceded": [], "confirmed_rels": kw.get("confirmed_rels", []),
         "obit_only_names": [], "flags": kw.get("flags", []), "evidence": "",
         "researched_at": kw.get("researched_at", "2026-09-10T14:00:00"),
         "searched": [{"who": "owner", "trail": trail}]}
    return f


def record(first, last, city, ss_first=None, ss_last=None, **kw):
    return {"key": kw.get("key", "1mainst|21061"), "owner_first": first, "owner_last": last,
            "ss_first": first if ss_first is None else ss_first, "ss_last": last if ss_last is None else ss_last,
            "street": "1 Main St", "city": city, "state": kw.get("state", "MD"), "zip": "21061",
            "county": kw.get("county", "Anne Arundel"), "mail_city": kw.get("mail_city", ""),
            "obit_date": kw.get("obit_date", "2026-08-01"), "is_trust": kw.get("is_trust", False),
            "business_name": kw.get("business_name", "")}


def ranked(age, rerooted=False, dec_age=None, deceased=False):
    rk = {"subject": {"first": "", "last": "X", "age": age, "deceased_flag": deceased}, "rels": [], "rerooted": rerooted}
    if rerooted:
        rk["decedent_from_smartskip"] = {"age": dec_age}
    return rk


def audit(f, rec, rk, **kw):
    return m.audit_finding(f, rec, rk, {}, False, "test", **kw)


# --- 1. Zimmerman
Z_URL = "https://www.legacy.com/us/obituaries/thebaltimorebanner/name/ronald-zimmerman-obituary?id=1"
zf = finding("Ronald Zimmerman", "Ronald F. Zimmerman", Z_URL,
             "Ronald F. Zimmerman Obituary (1958 - 2026) - Glen Burnie, MD - Legacy",
             "Ronald F. Zimmerman, 68, of Baltimore, passed away peacefully on July 31, 2026 ...",
             [{"url": "https://www.thebaltimorebanner.com/obit/ronald-zimmerman", "chars": 6000, "match": False},
              {"url": Z_URL, "chars": 2936, "match": True}])
zr = record("Ronald", "Zimmerman", "Glen Burnie")
z = audit(zf, zr, ranked(73))
check("Zimmerman: LIKELY_FALSE", z["recommendation"] == "LIKELY_FALSE")
check("Zimmerman: birth-year gap 5 (1953 vs 1958)", z["birth_year_gap"] == 5 and z["age_rel"] == "conflict")
check("Zimmerman: body city Baltimore, title city Glen Burnie kept apart",
      z["obit_city"] == "Baltimore" and z["title_city"] == "Glen Burnie" and z["place_rel"] == "mismatch")
check("Zimmerman: shell override flagged with the 6000-char page recorded",
      z["shell_override"] is True and z["prior_big_false_chars"] == 6000 and z["winning_chars"] == 2936)
check("Zimmerman: decedent initial F, CRM none -> unverifiable",
      z["decedent_initial"] == "F" and z["owner_initial"] == "" and "decedent_initial_unverifiable" in z["reasons"])
check("Zimmerman: reasons carry age_conflict place_mismatch shell_override",
      all(r in z["reasons"].split() for r in ("age_conflict", "place_mismatch", "shell_override")))
check("Zimmerman: shape NAMESAKE", z["shape"] == "NAMESAKE")
# the REAL 8131 Turn Loop Rd snippet has no residence: age gap + shell override alone must still flag
zf_real = finding("Ronald Zimmerman", "Ronald F. Zimmerman", Z_URL,
                  "Ronald Zimmerman Obituary, Age 68 - July 31, 2026 - Glen Burnie, Maryland",
                  "Read the obituary of Ronald F. Zimmerman, age 68 (April 10, 1958 - July 31, 2026), with arrangements entrusted to Singleton Funeral",
                  [{"url": "https://www.legacy.com/us/obituaries/thebaltimorebanner/name/ronald-zimmerman-obituary?id=1", "chars": 6000, "match": False},
                   {"url": Z_URL, "chars": 2936, "match": True}])
zreal = audit(zf_real, zr, ranked(73))
check("Zimmerman (real snippet): LIKELY_FALSE on age gap + shell override, place unknown",
      zreal["recommendation"] == "LIKELY_FALSE" and zreal["place_rel"] == "unknown" and zreal["title_city"] == "Glen Burnie")
# a lone age gap of 4-5 is SmartSkip noise until a second signal joins it
lone = audit(finding("Ann Lee", "Ann Lee", "https://fh.example/ann", "Ann Lee Obituary",
                     "Ann Lee, 85, passed away on May 1, 2026.", [{"url": "https://fh.example/ann", "chars": 4000, "match": True}]),
             record("Ann", "Lee", "Rockville"), ranked(81))
check("Lone age gap 4: REVIEW, not LIKELY_FALSE", lone["age_rel"] == "conflict" and lone["recommendation"] == "REVIEW")
oor = m.oor_relations(["ZIMMERMAN RONALD L", "ZIMMERMAN CONSUELO"], "Ronald F. Zimmerman", zr)
check("Zimmerman + SDAT roll: initial L vs F = conflict", oor["oor_initial"] == "L" and oor["oor_initial_rel"] == "conflict")
check("Zimmerman + SDAT roll: name_match_level MEDIUM (documented: never a conflict)", oor["oor_level"] == "MEDIUM")
check("Zimmerman + SDAT roll: CRM surname on roll", oor["oor_surname_on_roll"] is True)
z2 = audit(zf, zr, ranked(73), oor={"oor_status": "sdat", "oor_source": "sdat", **oor})
check("Zimmerman + roll: still LIKELY_FALSE with two strong signals",
      z2["recommendation"] == "LIKELY_FALSE" and "oor_initial_conflict" in z2["reasons"])

# --- 2. Dolch (clean)
D_URL = "https://www.mccullyfuneral.com/obituaries/herbert-dolchsr"
df = finding("Herbert Dolch", "Herbert M. Dolch Sr.", D_URL,
             "Herbert Dolch, Sr. Obituary Feb 10, 2022 - McCully Polyniak & Collins",
             "Herbert Dolch, Sr. Aug 31, 1926 — Feb 10, 2022 Herbert M. Dolch Sr., 95 yrs., passed away on Thursday "
             "evening, February 10, 2022 at Sinai Hospital in Baltimore, Maryland. Herb Sr. was born in Baltimore.",
             [{"url": D_URL, "chars": 5200, "match": True}], dod="2022-02-10", confirmed_rels=["DAVID DOLCH", "EDWARD DOLCH"])
dr = record("Herbert M", "Dolch", "Baltimore", ss_first="HERBERT M", obit_date="2022-02-12")
d = audit(df, dr, ranked(99, deceased=True))   # born 1926/27: 2026 - 99 = 1927 vs 1926
check("Dolch: CLEAN", d["recommendation"] == "CLEAN")
check("Dolch: initial M matches", d["initial_rel"] == "match")
check("Dolch: explicit birth year 1926 from the date range, gap 1 -> ok",
      d["obit_birth_year"] == 1926 and d["birth_year_gap"] == 1 and d["age_rel"] == "ok")
check("Dolch: 95 yrs parsed", d["obit_age"] == 95)
check("Dolch: 'born in Baltimore' is not a residence; hospital city not taken either", d["obit_city"] == "")
check("Dolch: suffix Sr vs no CRM suffix -> unknown, never conflict", d["suffix_rel"] == "unknown")
check("Dolch: middle_initial M", m.middle_initial("Herbert M. Dolch Sr.") == "M")

# --- 3. Shell-only
sf = finding("Jane Roe", "Jane Roe", "https://www.legacy.com/us/obituaries/name/jane-roe-obituary?id=2",
             "Jane Roe Obituary (2026) - Legacy", "Jane Roe Obituary ... leave a kind word or memory",
             [{"url": "https://fh.example/jane", "chars": 4100, "match": False},
              {"url": "https://www.legacy.com/us/obituaries/name/jane-roe-obituary?id=2", "chars": 1900, "match": True}])
s = audit(sf, record("Jane", "Roe", "Rockville"), ranked(70))
check("Shell-only: REVIEW", s["recommendation"] == "REVIEW")
check("Shell-only: reasons == shell_override alone", s["reasons"].split() == ["shell_override"])
check("Shell-only: age and place unknown", s["age_rel"] == "unknown" and s["place_rel"] == "unknown")
check("Shell-only: '(2026)' alone is not a birth range", s["obit_birth_year"] is None)

# --- 4. Empty
ef = finding("John Doe", "John Doe", "", "", "", [])
e = audit(ef, record("John", "Doe", "Frederick"), ranked(None))
check("Empty: NO_SIGNAL, no exception", e["recommendation"] == "NO_SIGNAL")
hit, page, before, n = m.winning_trail({"searched": [{"who": "owner", "trail": []}]})
check("winning_trail tolerates an empty trail", hit is None and page is None and before == [] and n == 0)
check("shell_override false when the long page MATCHED",
      m.shell_override([{"chars": 6000, "match": True}], {"chars": 2000, "match": True})[0] is False)
check("shell_override false with no winning page", m.shell_override([{"chars": 6000, "match": False}], None)[0] is False)

# --- 5. Re-rooted
rf = finding("Mary Smith", "Mary Smith", "https://fh.example/mary-smith", "Mary Smith Obituary",
             "Mary Smith, age 79, of Frederick, Maryland passed away on March 3, 2025.",
             [{"url": "https://fh.example/mary-smith", "chars": 4000, "match": True}], dod="2025-03-03")
r = audit(rf, record("Mary", "Smith", "Frederick"), ranked(45, rerooted=True, dec_age=80))
check("Re-rooted: decedent age 80 used", r["owner_age"] == 80 and r["owner_age_source"] == "smartskip_decedent")
check("Re-rooted: 1946 vs 1946 -> ok", r["age_rel"] == "ok" and r["birth_year_gap"] == 0)
check("Re-rooted: place match Frederick", r["place_rel"] == "match" and r["obit_city"] == "Frederick")

# --- 6. Suffix
xf = finding("Larry Mcclaugherty", "Larry Dean Mcclaugherty Sr.", "https://fh.example/larry",
             "Larry Dean Mcclaugherty Sr. Obituary", "Larry passed away ...",
             [{"url": "https://fh.example/larry", "chars": 3500, "match": True}])
x = audit(xf, record("Larry", "Mcclaugherty", "Glen Burnie", ss_first="LARRY D JR"), ranked(50))
check("Suffix: Sr vs Jr = conflict", x["suffix_rel"] == "conflict")
check("Suffix: at least REVIEW", x["recommendation"] in ("REVIEW", "LIKELY_FALSE"))
check("Suffix: CRM initial D matches decedent Dean -> initial_match", x["initial_rel"] == "match")

# --- 6b. Generation gap: SmartSkip subject 44, decedent born 1938 -> REVIEW / GENERATION_GAP, never LIKELY
gf = finding("William Bethune", "William T. Bethune", "https://fh.example/wb", "William T. Bethune Obituary | 1938 - 2026 | Washington, DC",
             "William T. Bethune passed away", [{"url": "https://fh.example/wb", "chars": 4000, "match": True}])
g = audit(gf, record("William", "Bethune", "Washington", state="DC"), ranked(44))
check("Generation gap: age_rel generation, REVIEW, shape GENERATION_GAP",
      g["age_rel"] == "generation" and g["recommendation"] == "REVIEW" and g["shape"] == "GENERATION_GAP")
g2 = audit(gf, record("William", "Bethune", "Washington", state="DC"), ranked(44),
           oor={"oor_status": "itspe", "oor_source": "itspe", **m.oor_relations(["BETHUNE, WILLIAM J"], "William T. Bethune", record("William", "Bethune", "Washington"))})
check("Generation gap + roll initial J vs T: LIKELY_FALSE, shape NAMESAKE",
      g2["recommendation"] == "LIKELY_FALSE" and g2["shape"] == "NAMESAKE" and "oor_initial_conflict" in g2["reasons"])

# --- 6c. Spouse shape, honorifics, initials
pf = finding("David Pincus", "Ronnie Halpern Pincus", "https://fh.example/rp", "Ronnie Pincus Obituary",
             "Ronnie Halpern Pincus, 79, of Potomac, Maryland, passed away", [{"url": "https://fh.example/rp", "chars": 4000, "match": True}],
             flags=["decedent name differs from owner name - check"], confidence="medium")
pp = audit(pf, record("David", "Pincus", "Potomac"), ranked(80))
check("Spouse: given_name_differs, shape SPOUSE_OR_RELATIVE, at least REVIEW",
      pp["given_name_rel"] == "differs" and pp["shape"] == "SPOUSE_OR_RELATIVE" and pp["recommendation"] in ("REVIEW", "LIKELY_FALSE"))
check("Spouse: the pipeline's own flag is not double counted", "flag_name_differs" not in pp["reasons"].split())
check("given_name_relation: 'Capt. Roberta J. Grace, USN (Ret.)' ~ 'Roberta Grace'",
      m.given_name_relation("Capt. Roberta J. Grace, USN (Ret.)", "Roberta Grace") == "match")
check("given_name_relation: 'T. Joseph Touhey' ~ 'T Touhey'", m.given_name_relation("T. Joseph Touhey", "T Touhey") == "match")
check("given_name_relation: trust title is not compared (unknown via audit_finding)",
      audit(finding("Stush Rayfield", "John Paul Stush", "https://fh.example/js", "t", "s", [{"url": "https://fh.example/js", "chars": 4000, "match": True}]),
            record("", "Stush Rayfield Family Revocable Trust", "Frederick", ss_first="Stush", ss_last="Rayfield", is_trust=True), ranked(70))["given_name_rel"] == "unknown")
check("middle_initials: maiden name rides as a middle -> 'EG'", m.middle_initials("Evelyn Elizabeth Green White") == "EG")
check("initial_relation: 'EG' vs roll G = match, vs M = conflict",
      (m.initial_relation("EG", "G"), m.initial_relation("EG", "M")) == ("match", "conflict"))
check("clean_name strips quoted nickname and parenthesised maiden name",
      m.clean_name('Mary Elizabeth "Betty" Keeton (Stroup)') == "Mary Elizabeth Keeton")

# --- 7. Primitives
af = m.extract_age_facts
check("age: 'age 68 (April 10, 1958 - July 31, 2026)'",
      af("age 68 (April 10, 1958 - July 31, 2026)") == {"age": 68, "birth_year": 1958, "death_year": 2026, "pattern": "range+age_n"})
check("age: ', 79, of Herndon'", af("Lynne Bursenos, 79, of Herndon, Virginia, passed away")["age"] == 79)
check("age: '(71) of Herndon'", af("Mark William Ambrose (71) of Herndon, Virginia passed away")["age"] == 71)
check("age: 'at the age of 100'", af("passed away at the age of 100 in Woodbridge")["age"] == 100)
check("age: '| 1935 - 2026 |'", af("Bernice Offit Berger Obituary | 1935 - 2026 | Pikesville, MD")["birth_year"] == 1935)
check("age: 'Aug 31, 1926 — Feb 10, 2022'", af("Aug 31, 1926 — Feb 10, 2022")["birth_year"] == 1926)
check("age: phone-like '410-555-1234' ignored", af("call 410-555-1234")["birth_year"] is None)
check("age: 'age 5' out of range ignored", af("age 5 at the time")["age"] is None)
ep = m.extract_place
check("place: 'of Herndon, Virginia'", ep("Lynne Bursenos, 79, of Herndon, Virginia, passed")["city"] == "Herndon")
check("place: 'of Silver Spring, Maryland'", ep("On Sunday, July 19, 2026, of Silver Spring, Maryland.")["city"] == "Silver Spring")
check("place: 'of Washington, District Of Columbia'", ep("Joyce Thomas of Washington, District Of Columbia passed away")["city"] == "Washington")
check("place: 'resident of McLean, Virginia'", ep("Jen Wen Chen was a resident of McLean, Virginia.")["city"] == "McLean")
check("place: 'son of John and Bessie' not a city", ep("was the son of John and Bessie Dolch.")["city"] == "")
check("place: 'died of congestive heart failure' not a city", ep("passed away of congestive heart failure at Sinai")["city"] == "")
check("place: 'his home in Alexandria, VA'", ep("died at his home in Alexandria, VA, on April 26")["city"] == "Alexandria")
check("title_city: dignitymemorial", m.title_city("Jen Chen Obituary - Falls Church, VA - Dignity Memorial") == "Falls Church")
check("title_city: tributearchive", m.title_city("Bernice Offit Berger Obituary | 1935 - 2026 | Pikesville, MD - Tribute Archive") == "Pikesville")
pr = m.place_relation
check("place_relation: Baltimore vs Glen Burnie = mismatch (the trap)", pr("Baltimore", "Glen Burnie") == "mismatch")
check("place_relation: Pasadena vs Glen Burnie = near", pr("Pasadena", "Glen Burnie") == "near")
check("place_relation: Washington vs Washington DC alias", pr("Washington", "Washington DC") == "match")
check("place_relation: mail city counts", pr("Frederick", "Rockville", "Frederick") == "match")
check("place_relation: unknown when blank", pr("", "Rockville") == "unknown")
check("age_relation 2/3/4", (m.age_relation(2), m.age_relation(3), m.age_relation(4)) == ("ok", "soft", "conflict"))
sc = m.sdat_county
check("sdat_county: Baltimore + city Baltimore -> Baltimore City", sc({"county": "Baltimore", "city": "Baltimore"}) == "Baltimore City")
check("sdat_county: Baltimore + city Towson -> Baltimore County", sc({"county": "Baltimore", "city": "Towson"}) == "Baltimore County")
check("sdat_county: blank -> None", sc({"county": "", "city": "X"}) is None)
check("sdat_county: Anne Arundel passes through", sc({"county": "Anne Arundel"}) == "Anne Arundel")
check("roll_middle_initial: 'ZIMMERMAN RONALD L'", m.roll_middle_initial("ZIMMERMAN RONALD L", "Ronald") == "L")
check("roll_middle_initial: 'JOYNER, MICHAEL' -> '' (no middle)", m.roll_middle_initial("JOYNER, MICHAEL", "Michael") == "")
check("roll_middle_initial: first not on roll -> None", m.roll_middle_initial("SMITH JANE A", "Michael") is None)
check("roll_middle_initial: 'DOE JOHN JR' suffix is not a middle", m.roll_middle_initial("DOE JOHN JR", "John") == "")
check("middle_initial: nickname in quotes is not a middle", m.middle_initial('Morris "Moe" Rosen') == "")
check("middle_initial: 'Lynn Richard Ausherman' -> R", m.middle_initial("Lynn Richard Ausherman") == "R")
check("owner_initial: 'Tracey M' -> M from owner_first", m.owner_initial({"owner_first": "Tracey M", "ss_first": ""}) == ("M", "owner_first"))
check("given_name_relation: 'Lynn Wayne Hill' ~ 'WAYNE HILL' match", m.given_name_relation("Lynn Wayne Hill", "WAYNE HILL") == "match")
check("given_name_relation: 'Annie Nicole Stringer' vs 'William Stringer' differs",
      m.given_name_relation("Annie Nicole Stringer", "William Gerald Stringer") == "differs")

print(f"\n{FAILS} failure(s)" if FAILS else "\nall checks passed")
sys.exit(1 if FAILS else 0)
