"""Offline checks for src/scripts/dp_ghost_name_resolver.py. No network, no index load.

Run after ANY change to same_person / vendor_candidates / decide / email_hints, the way
test_md_row_pass_budgets.py guards the RoW pull. Each check is a case that misfired on the
live 130-ghost cohort on 2026-09-17 before the rule existed:

  1. GARY TOMITA + GARY EUGENE TOMITA are one vendor person (merge, HIGH not CONFLICT).
  2. GIUSEPPE BORIS COLARICH ~ BORIS E COLARICH merge on the first given name of one side.
  3. BENJAMIN LEE WELZEL is NOT RAYMOND LEE WELZEL (shared middle name), and Sr is never Jr.
  4. A vendor name equal to a named relative with a fitting age -> DUPLICATE (Michael Head).
  5. ... but a 33-year-old is not a duplicate of the 66-year-old spouse who shares a given
     token (Cathleen Tram Anh Nguyen / Anh Thucthi Nguyen) -> HIGH on the child.
  6. A vendor "Sr." spelling of a named relative is that relative whatever the ages.
  7. A "preceded in death" name that also fits a survivor is kept (Milton Craig Amoss = the
     son Craig, not the late Milton J. Amoss).
  8. The owner's own name on the unique phones -> NAMESAKE, suffix-for-suffix (Larry D
     Mcclaugherty Jr owner vs LARRY DEAN MCCLAUGHERTY JR vendor).
  9. Email handles: CMSNOOTS -> initials C.M.; EBONY.BROWN -> ebony; a lone initial matches.
 10. An email hint alone never proposes a name (HINT tier).

  python tools/manual/test_ghost_name_resolver.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

import dp_ghost_name_resolver as m  # noqa: E402

FAILS = 0


def check(label, cond):
    global FAILS
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        FAILS += 1


def hit(name, role="rel", anchor="", prop_key="", file="x.xlsx"):
    toks = name.split()
    return {"name": name, "first": toks[0], "last": toks[-1], "role": role, "anchor": anchor,
            "prop_key": prop_key, "input_key": "", "file": file, "phones": []}


def run(ghost, unique, shared, owner, named, hits, preceded=(), survivors=(), obit=(), hints=()):
    v = m.vendor_candidates(ghost, "k|00000", set(unique), set(shared), owner, named, list(preceded), list(survivors), hits)
    d = m.decide(v, list(obit), list(hints), str(ghost.get("age") or ""), owner)
    return v, d


# --- 1 + 2: merging vendor spellings of one person
check("same_person: GARY TOMITA ~ GARY EUGENE TOMITA", m.same_person("GARY TOMITA", "GARY EUGENE TOMITA"))
check("same_person: GIUSEPPE BORIS COLARICH ~ BORIS E COLARICH", m.same_person("GIUSEPPE BORIS COLARICH", "BORIS E COLARICH"))
v, d = run({"name": "Unknown Tomita", "age": "60"}, ["7274492149", "7274592149"], [], "Hisao Tomita",
           [{"name": "JENNIE TOMITA", "age": "80", "canon_rel": "Spouse"}],
           {"7274492149": [hit("Gary Tomita", "pr"), hit("GARY EUGENE TOMITA")],
            "7274592149": [hit("Gary Tomita", "pr"), hit("GARY EUGENE TOMITA")]})
check("Tomita: one merged candidate", len(v["cands"]) == 1)
check("Tomita: HIGH Gary", d["confidence"] == "HIGH" and d["first"] == "Gary")

# --- 3: a shared middle name is not an identity; Sr is not Jr
check("same_person: BENJAMIN LEE WELZEL !~ RAYMOND LEE WELZEL", not m.same_person("BENJAMIN LEE WELZEL", "RAYMOND LEE WELZEL JR"))
check("same_person: RAYMOND LEE WELZEL SR !~ RAYMOND LEE WELZEL JR", not m.same_person("RAYMOND LEE WELZEL SR", "RAYMOND LEE WELZEL JR"))
check("same_person: LON MICHAEL HEAD SR ~ MICHAEL HEAD", m.same_person("LON MICHAEL HEAD SR", "MICHAEL HEAD"))

# --- 4: duplicate of a named relative, ages fit
v, d = run({"name": "Unknown Head", "age": "78"}, ["7032544091"], ["7033802619"], "Martha Head",
           [{"name": "MICHAEL HEAD", "age": "74", "canon_rel": "Relative"}],
           {"7032544091": [hit("LON MICHAEL HEAD SR")], "7033802619": [hit("LON MICHAEL HEAD SR")]})
check("Head: DUPLICATE of Michael Head", d["confidence"] == "DUPLICATE" and d["full"] == "Michael Head")

# --- 5: same given token but a generation apart -> a real candidate, not a duplicate
v, d = run({"name": "Unknown Nguyen", "age": "33"}, ["3015205139"], [], "Khanh Nguyen",
           [{"name": "ANH NGUYEN", "age": "66", "canon_rel": "Spouse"}],
           {"3015205139": [hit("CATHLEEN TRAM ANH NGUYEN")]})
check("Nguyen: HIGH Cathleen (age gap beats the shared token)", d["confidence"] == "HIGH" and d["first"] == "Cathleen")
check("Nguyen: not recorded as a duplicate", not v["dups"])

# --- 6: a Sr. spelling of a named relative is that relative whatever the ages
v, d = run({"name": "Unknown Welzel", "age": "52"}, [], ["4108201316"], "Jean Welzel",
           [{"name": "RAYMOND WELZEL", "age": "70", "canon_rel": "Spouse"}, {"name": "BENJAMIN WELZEL", "age": "37", "canon_rel": "Child"}],
           {"4108201316": [hit("RAYMOND LEE WELZEL SR"), hit("RAYMOND LEE WELZEL JR"), hit("BENJAMIN LEE WELZEL")]},
           hints=[{"frag": "raymondlee", "kind": "before_surname", "email": "raymondleewelzel@x.com"}])
check("Welzel: Sr folded into the named spouse", any(x["named"] == "RAYMOND WELZEL" for x in v["dups"]))
check("Welzel: Benjamin Lee folded into named Benjamin", any(x["named"] == "BENJAMIN WELZEL" for x in v["dups"]))
check("Welzel: Jr is the only candidate", [c["name"] for c in v["cands"]] == ["Raymond Lee Welzel Jr"])
check("Welzel: MEDIUM on the household line + email", d["confidence"] == "MEDIUM" and d["first"] == "Raymond")

# --- 7: preceded-in-death guard yields to a survivor match
v, d = run({"name": "Unknown Amoss", "age": "61"}, [], ["4105550001"], "Ruth Amoss",
           [{"name": "MILTON AMOSS", "age": "90", "canon_rel": "Spouse"}],
           {"4105550001": [hit("MILTON J AMOSS"), hit("MILTON CRAIG AMOSS")]},
           preceded=["Milton J. Amoss"], survivors=["Craig Amoss"],
           obit=[{"name": "Craig Amoss", "first": "Craig", "word": "son", "is_child": True}])
check("Amoss: Milton J dropped as predeceased", any(p.startswith("Milton J Amoss") for p in v["predeceased"]))
check("Amoss: Milton Craig kept", any(c["name"] == "Milton Craig Amoss" for c in v["cands"]))
check("Amoss: proposes Craig via the obituary son", d["first"] == "Craig" or d["full"] == "Milton Craig Amoss")

# --- 8: the owner's own name on the unique phones, suffix-for-suffix
v, d = run({"name": "Unknown Mcclaugherty Jr", "age": "58"}, ["4102556675"], [], "Larry D Mcclaugherty Jr",
           [{"name": "LARRY MCCLAUGHERTY", "age": "84", "canon_rel": "Spouse"}],
           {"4102556675": [hit("LARRY DEAN MCCLAUGHERTY JR")]})
check("McClaugherty: NAMESAKE tier", d["confidence"] == "NAMESAKE" and d["first"] == "Larry")
v, d = run({"name": "Unknown Destefano", "age": "57"}, ["4844440635"], [], "Anthony Destefano",
           [{"name": "ELAINE DESTEFANO", "age": "80", "canon_rel": "Spouse"}],
           {"4844440635": [hit("ANTHONY FRANCIS DESTEFANO"), hit("Anthony Destefano", "pr")]})
check("Destefano: NAMESAKE tier on the owner's unsuffixed name", d["confidence"] == "NAMESAKE" and d["first"] == "Anthony")

# --- 9: email handles
h = m.email_hints(["cmsnoots22@gmail.com", "ebony.brown@x.com", "dglenn@x.com"], "snoots", set())
check("initials: CMSNOOTS -> cm", any(x["frag"] == "cm" and x["kind"] == "initials" for x in h))
check("hint_agrees: C.M. fits Corey Michael Snoots", bool(m.hint_agrees(h, "Corey Michael Snoots")))
check("hint_agrees: C.M. does not fit Corey Allen Snoots", not m.hint_agrees(h, "Corey Allen Snoots"))
h2 = m.email_hints(["ebony.brown@x.com"], "brown", set())
check("token: EBONY.BROWN -> ebony", any(x["frag"] == "ebony" for x in h2))
check("hint_agrees: ebony fits Ebony Dominique Brown", bool(m.hint_agrees(h2, "Ebony Dominique Brown")))
h3 = m.email_hints(["dglenn@x.com"], "glenn", set())
check("initial: DGLENN -> d", any(x["frag"] == "d" and x["kind"] == "initial" for x in h3))
check("hint_agrees: initial d fits Damien Glenn", bool(m.hint_agrees(h3, "Damien Bernard Glenn")))

# --- 10: an email alone never proposes
v, d = run({"name": "Unknown Smith", "age": "40"}, ["3018318433"], [], "Terry Smith", [], {},
           hints=[{"frag": "jason", "kind": "before_surname", "email": "jasonsmith@x.com"}])
check("email-only -> HINT, no proposal", d["confidence"] == "HINT" and not d["first"])

print(f"\n{FAILS} failure(s)" if FAILS else "\nall checks passed")
sys.exit(1 if FAILS else 0)
