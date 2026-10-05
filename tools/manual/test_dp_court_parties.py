"""Offline guard for dp_court_parties (2026-10-02).

Covers: norm_addr / addr_match (unit stripping, suffix abbreviation, quadrant with dots,
"1/2" and "-B" house numbers, "N Street NW"), name matching (Last, First M; hyphenated
surnames; anchors.decedent_full), parse_mailing, the portal-store dedupe rule, and the
three join bases (case_number > address > name_only with the zip gate) on synthetic rows.
No network, no files touched.

    python -X utf8 tools/manual/test_dp_court_parties.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import dp_court_parties as cp  # noqa: E402

FAILS = 0


def check(label: str, got, want) -> None:
    global FAILS
    ok = got == want
    FAILS += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n       got  {got!r}\n       want {want!r}"))


# ── norm_addr ──
check("addr: plain record form", cp.norm_addr("1613 4th St NW", "20001"), ("1613", "4th", "NW", "20001"))
check("addr: portal 'Street, NW' with comma + dots", cp.norm_addr("1603 35th Street, N.W.", "20007-1234"), ("1603", "35th", "NW", "20007"))
check("addr: unit after comma is dropped", cp.norm_addr("2301 N Street NW, Apt 209", "20037"), ("2301", "n", "NW", "20037"))
check("addr: '#316' unit dropped", cp.norm_addr("654 Girard Street NW, #316", "20001"), ("654", "girard", "NW", "20001"))
check("addr: '1/2' house keeps the base number", cp.norm_addr("4294 1/2 Southern Ave S.E.", "20019"), ("4294", "southern", "SE", "20019"))
check("addr: '5201-B' house letter dropped, unit after quadrant dropped",
      cp.norm_addr("5201-B Wisconsin Ave, NW #102", "20015"), ("5201", "wisconsin", "NW", "20015"))
check("addr: upper-case ordinal street", cp.norm_addr("321 10TH STREET SE", "20003"), ("321", "10th", "SE", "20003"))
check("addr: 'Pl' vs 'Place' normalise alike",
      cp.norm_addr("1223 44TH PL SE", "20020")[:3], cp.norm_addr("1223 44th Place SE", "20020")[:3])
check("addr: no quadrant (MD) leaves quad blank", cp.norm_addr("10106 Cedar Ln", "20895"), ("10106", "cedar", "", "20895"))
check("addr: no house number = None", cp.norm_addr("Not Found (Claude)", "20001"), None)
check("addr: blank = None", cp.norm_addr("", ""), None)

# ── addr_match ──
a = cp.norm_addr("1013 Bryant St NE", "20018")
check("match: same address, portal with comma", cp.addr_match(a, cp.norm_addr("1013 Bryant Street, NE", "20018")), True)
check("match: blank quadrant on one side still matches", cp.addr_match(a, cp.norm_addr("1013 Bryant St", "20018")), True)
check("match: other quadrant does not", cp.addr_match(a, cp.norm_addr("1013 Bryant St NW", "20018")), False)
check("match: other zip does not", cp.addr_match(a, cp.norm_addr("1013 Bryant St NE", "20017")), False)
check("match: other house does not", cp.addr_match(a, cp.norm_addr("1015 Bryant St NE", "20018")), False)
check("match: None side", cp.addr_match(a, None), False)

# ── names ──
check("name: split 'Last, First M'", cp.split_portal_name("Chambers-Chandler, Mary F"), ("Chambers-Chandler", "Mary"))
check("name: hyphen parts", cp.last_name_parts("Chambers-Chandler"), {"chamberschandler", "chambers", "chandler"})
row = {"decedent_name": "Young, Bettye M", "decedent_first_name": "Bettye M", "decedent_last_name": "Young"}
check("name: owner fields match", cp.name_match(row, {"owner_first": "Bettye", "owner_last": "Young"}), True)
check("name: first-name differs", cp.name_match(row, {"owner_first": "Betty", "owner_last": "Young"}), False)
check("name: via anchors.decedent_full", cp.name_match(row, {"anchors": {"decedent_full": "Bettye Maureen Young"}}), True)
check("name: hyphenated portal surname matches one part",
      cp.name_match({"decedent_name": "Chambers-Chandler, Mary F", "decedent_first_name": "Mary F", "decedent_last_name": "Chambers-Chandler"},
                    {"owner_first": "Mary", "owner_last": "Chandler"}), True)
check("name: no record names = no match", cp.name_match(row, {"owner_first": "", "owner_last": ""}), False)

# ── parse_mailing ──
m = cp.parse_mailing("4911 Brooks St Ne #207 Washington, DC 20019")
check("mailing: Washington DC", (m["street"], m["city"], m["state"], m["zip"]), ("4911 Brooks St Ne #207", "Washington", "DC", "20019"))
m = cp.parse_mailing("Po Box 202 Copper Center, AK 99573")
check("mailing: two-word known city", (m["street"], m["city"], m["state"], m["zip"]), ("Po Box 202", "Copper Center", "AK", "99573"))
m = cp.parse_mailing("8702 Greens Ln Randallstown, MD 21133")
check("mailing: one-word city", (m["street"], m["city"]), ("8702 Greens Ln", "Randallstown"))
m = cp.parse_mailing("12 Elm St Zyxville, MD 21133", cities=["Zyxville"])
check("mailing: caller-supplied city list", (m["street"], m["city"]), ("12 Elm St", "Zyxville"))
m = cp.parse_mailing("no state here")
check("mailing: unparseable keeps raw in street", (m["street"], m["zip"], m["mailing_raw"]), ("no state here", "", "no state here"))
check("mailing: blank", cp.parse_mailing("")["street"], "")

# ── portal store dedupe ──
import json, tempfile, os  # noqa: E402

tmp = tempfile.mkdtemp()
old = {"2026-ADM-000001": {"case_no": "2026-ADM-000001", "last_checked": "09/16/2026", "heirs": [{"name": "A"}], "personal_reps": []}}
new = {"2026-ADM-000001": {"case_no": "2026-ADM-000001", "last_checked": "10/01/2026", "heirs": [], "personal_reps": []}}
same_rich = {"2026-ADM-000001": {"case_no": "2026-ADM-000001", "last_checked": "10/01/2026", "heirs": [{"name": "A"}, {"name": "B"}], "personal_reps": []}}
p1, p2, p3 = (os.path.join(tmp, f"{n}.json") for n in ("a_old", "b_new", "c_rich"))
for p, d in ((p1, old), (p2, new), (p3, same_rich)):
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
st = cp.load_portal_store([p1, p2])
check("store: latest last_checked wins over richer older", len(st["2026-ADM-000001"]["heirs"]), 0)
st = cp.load_portal_store([p1, p2, p3])
check("store: same date -> richer wins", len(st["2026-ADM-000001"]["heirs"]), 2)
check("store: source file recorded", st["2026-ADM-000001"]["_source_file"].endswith("c_rich.json"), True)

# ── the three join bases ──
STORE = {
    "2026-ADM-000983": {
        "case_no": "2026-ADM-000983", "decedent_name": "Young, Bettye M", "decedent_first_name": "Bettye M",
        "decedent_last_name": "Young", "filing_date": "09/11/2026", "dod": "07/21/2026", "status": "Open",
        "property_street": "274 15th Street SE", "property_zip": "20003", "property_itspe_owner": "YOUNG, BETTYE M",
        "personal_reps": [{"name": "Hatcher, Tev Antonette", "connection": "Applicant", "street": "8702 Greens Ln", "city": "Randallstown", "state": "MD", "zip": "21133"}],
        "heirs": [{"name": "Young, Carl", "connection": "Legatee", "street": "", "city": "", "state": "", "zip": ""},
                  {"name": "Young, Dee", "connection": "Heir", "street": "", "city": "", "state": "", "zip": ""}],
        "_source_file": "output/daily_pulls/2026-09-16/dc_probates_portal.json",
    },
    "2026-ADM-000900": {
        "case_no": "2026-ADM-000900", "decedent_name": "Cobb, Essie", "decedent_first_name": "Essie",
        "decedent_last_name": "Cobb", "filing_date": "09/01/2026", "dod": "06/01/2026", "status": "Open",
        "property_street": "1613 4th Street, N.W., Apt 2", "property_zip": "20001", "property_itspe_owner": "COBB, ESSIE",
        "personal_reps": [{"name": "Cobb, Ray", "connection": "Applicant", "street": "", "city": "", "state": "", "zip": ""}],
        "heirs": [{"name": "Cobb, Sue", "connection": "Heir", "street": "", "city": "", "state": "", "zip": ""}],
        "_source_file": "output/daily_pulls/2026-09-11/dc_probates_portal.json",
    },
    "2026-ADM-000901": {
        "case_no": "2026-ADM-000901", "decedent_name": "Stancil, Dwight", "decedent_first_name": "Dwight",
        "decedent_last_name": "Stancil", "filing_date": "09/02/2026", "dod": "", "status": "Open",
        "property_street": None, "property_zip": None, "property_itspe_owner": None,
        "personal_reps": [], "heirs": [],
        "_source_file": "output/daily_pulls/2026-09-09/dc_probates_portal.json",
    },
    "2026-ADM-000902": {
        "case_no": "2026-ADM-000902", "decedent_name": "Stancil, Dwight", "decedent_first_name": "Dwight",
        "decedent_last_name": "Stancil", "filing_date": "09/03/2026", "dod": "", "status": "Open",
        "property_street": "9 Other St NE", "property_zip": "20019", "property_itspe_owner": None,
        "personal_reps": [], "heirs": [],
        "_source_file": "output/daily_pulls/2026-09-09/dc_probates_portal.json",
    },
}
BY_ADDR = cp.build_indexes(STORE)

rec = {"state": "DC", "owner_first": "Bettye", "owner_last": "Young", "street": "274 15th St SE", "zip": "20003",
       "anchors": {"case_number": "2026-ADM-000983", "decedent_full": "Bettye Maureen Young"}}
e, un = cp.court_entry_dc(rec, STORE, BY_ADDR)
check("join 1: case_number", (e["case_no"], e["join_basis"], un), ("2026-ADM-000983", "case_number", None))
check("join 1: PR + heirs + will_on_file (Legatee)", (len(e["pr"]), len(e["heirs"]), e["will_on_file"]), (1, 2, True))
check("join 1: connections tally", e["connections"], {"Applicant": 1, "Heir": 1, "Legatee": 1})
check("join 1: source file + dod carried", (e["source_file"], e["dod"]), (STORE["2026-ADM-000983"]["_source_file"], "07/21/2026"))

rec = {"state": "DC", "owner_first": "Essie", "owner_last": "Cobb", "street": "1613 4th St NW", "zip": "20001", "anchors": None}
e, un = cp.court_entry_dc(rec, STORE, BY_ADDR)
check("join 2: address (portal has dots + unit)", (e["case_no"], e["join_basis"]), ("2026-ADM-000900", "address"))
check("join 2: Heir-only = will_on_file False", e["will_on_file"], False)

rec = {"state": "DC", "owner_first": "Essie", "owner_last": "Cobb", "street": "1613 4th St NW", "zip": "20001",
       "anchors": {"case_number": "2026-ADM-999999"}}
e, un = cp.court_entry_dc(rec, STORE, BY_ADDR)
check("join 2 after a missing anchors case: address still resolves, case flagged",
      (e["join_basis"], un, e["anchors_case_not_in_portal"]), ("address", "2026-ADM-999999", "2026-ADM-999999"))

rec = {"state": "DC", "owner_first": "Dwight", "owner_last": "Stancil", "street": "225 P St NW", "zip": "20001", "anchors": None}
e, un = cp.court_entry_dc(rec, STORE, BY_ADDR)
check("join 3: name_only accepts the row with NO property address, not the other-zip row",
      (e["case_no"], e["join_basis"]), ("2026-ADM-000901", "name_only"))
check("join 3: review note", e["note"].startswith("REVIEW name_only"), True)
check("join 3: no parties -> will_on_file False, heirs empty", (e["will_on_file"], e["heirs"]), (False, []))

rec = {"state": "DC", "owner_first": "Dwight", "owner_last": "Stancil", "street": "9 Other St NE", "zip": "20019", "anchors": None}
e, un = cp.court_entry_dc(rec, STORE, BY_ADDR)
check("join 2 beats 3: same name, address row wins", (e["case_no"], e["join_basis"]), ("2026-ADM-000902", "address"))

rec = {"state": "DC", "owner_first": "Nobody", "owner_last": "Here", "street": "1 Nowhere Pl SE", "zip": "20020", "anchors": None}
e, un = cp.court_entry_dc(rec, STORE, BY_ADDR)
check("no case: note + nulls", (e["case_no"], e["join_basis"], e["will_on_file"], e["note"]), (None, None, None, "no case on file"))

rec = {"state": "DC", "owner_first": "Nobody", "owner_last": "Here", "street": "1 Nowhere Pl SE", "zip": "20020",
       "anchors": {"case_number": "2026-ADM-777777"}}
e, un = cp.court_entry_dc(rec, STORE, BY_ADDR)
check("no case but anchors case: reported as unmatched", (e["case_no"], e["join_basis"], un), ("2026-ADM-777777", None, "2026-ADM-777777"))

# ── MD / VA ──
rec = {"state": "MD", "anchors": {"case_number": "W128194", "dod": "2026-09-07", "file_date": "2026-09-22",
                                  "decedent_full": "Paul H Snyder", "pr_full": "Albert Snyder",
                                  "pr_mailing": "Po Box 202 Copper Center, AK 99573"}}
e = cp.court_entry_mdva(rec, [])
check("MD: anchors basis, PR parsed", (e["case_no"], e["join_basis"], e["pr"][0]["name"], e["pr"][0]["city"], e["will_on_file"]),
      ("W128194", "anchors", "Albert Snyder", "Copper Center", None))
check("MD: note", e["note"], "MD/VA: no interested-persons list online; PR from anchors only")
e = cp.court_entry_mdva({"state": "VA", "anchors": None}, [])
check("VA without anchors: nulls, empty pr", (e["case_no"], e["join_basis"], e["pr"]), (None, None, []))

# ── summarize ──
court = {"_meta": {}, "k1": {"state": "DC", "join_basis": "case_number", "case_no": "x", "will_on_file": True, "heirs": [1]},
         "k2": {"state": "DC", "join_basis": None, "case_no": None, "will_on_file": None, "heirs": []},
         "k3": {"state": "MD", "join_basis": "anchors", "case_no": "W1"}}
s = cp.summarize(court)
check("summary counts", (s["dc_case_number"], s["dc_no_case"], s["dc_will_on_file"], s["md_anchors"], s["records"]), (1, 1, 1, 1, 3))
check("summary subset", cp.summarize(court, ["k1"])["records"], 1)

print()
print("ALL PASS" if not FAILS else f"{FAILS} FAILED")
sys.exit(1 if FAILS else 0)
