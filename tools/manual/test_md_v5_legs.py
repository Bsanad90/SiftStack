"""Offline guard for the Maryland v5 legs (2026-10-02): SDAT title parsing, the MDLANDREC deed
index entry, the Estate Search by-name chooser, the MD court entry and the MD note lines.

Covers: md_land_records_lookup.parse_sdat_transfers / deed_ref_key, md_deed_lookup
(term_from_title_owner, parse_party, on_parcel, finalize dedupe + flags), md_estate_name_search
(build_terms, row_matches, choose), dp_court_parties.court_entry_mdva with a name-search store,
and obituary_dp_batch's TITLE (SDAT) / DEED (MDLANDREC) / CASE lines. Synthetic data modelled on
302 Mary Lou Ave (Whitlock, Anne Arundel estate 119190). No network, no files touched.

    python -X utf8 tools/manual/test_md_v5_legs.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import md_land_records_lookup as L  # noqa: E402
import md_deed_lookup as D  # noqa: E402
import md_estate_name_search as E  # noqa: E402
import dp_court_parties as C  # noqa: E402
import obituary_dp_batch as b  # noqa: E402

FAILS = 0


def check(label: str, got, want) -> None:
    global FAILS
    ok = got == want
    FAILS += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n       got  {got!r}\n       want {want!r}"))


# ── SDAT parsing ──
SDAT_TEXT = ("Transfer Information\nSeller:\nDate:\n04/20/1972\nPrice:\n$0\nType:\nNON-ARMS LENGTH OTHER\nDeed1:\n"
             "/02483/ 00123\nDeed2:\nSeller:\nDOE JOHN\nDate:\n03/01/1965\nPrice:\n$18,500\nType:\nARMS LENGTH IMPROVED\n"
             "Deed1:\n/01800/ 00050\nDeed2:\nSeller:\nDate:\nPrice:\nType:\nDeed1:\nDeed2:\nExemption Information\n")
tr = L.parse_sdat_transfers(SDAT_TEXT)
check("sdat: two transfers kept, the blank third dropped", len(tr), 2)
check("sdat: first transfer", (tr[0]["date"], tr[0]["price"], tr[0]["type"], tr[0]["deed1"], tr[0]["seller"]),
      ("04/20/1972", "$0", "NON-ARMS LENGTH OTHER", "/02483/ 00123", ""))
check("sdat: second transfer has the seller", (tr[1]["seller"], tr[1]["price"]), ("DOE JOHN", "$18,500"))
check("ref: SDAT form", L.deed_ref_key("/02483/ 00123"), ("2483", "123"))
check("ref: Land Records pp.", L.deed_ref_key("Book 2483, pp. 123-124"), ("2483", "123"))
check("ref: Land Records single p.", L.deed_ref_key("Book 7696, p. 29"), ("7696", "29"))
check("ref: garbage", L.deed_ref_key("05"), None)

# ── deed index ──
check("deed: title owner term", D.term_from_title_owner("WHITLOCK ALMY S"), ("WHITLOCK", "ALMY"))
check("deed: trustee suffix dropped", D.term_from_title_owner("REAMY MICHAEL JOSEPH JR TRUSTEE ET AL"), ("REAMY", "MICHAEL"))
check("deed: party cell", D.parse_party("Grantee: WHITLOCK , PAUL R"), ("grantee", "WHITLOCK PAUL R"))
check("deed: grantor cell", D.parse_party("Grantor: WHITLOCK , PAUL R"), ("grantor", "WHITLOCK PAUL R"))
TITLE = {"owners": ["WHITLOCK PAUL R", "WHITLOCK ALMY S"], "deed_reference": "/02483/ 00123", "account": "17384690",
         "premise": "302 MARY LOU AVE GLEN BURNIE 21060-0000", "transfers": [{"deed1": "/02483/ 00123", "deed2": ""}],
         "source": "sdat", "ssl": "05-17384690", "sale_date": "1972-04-20", "sale_price": 0,
         "sale_type": "NON-ARMS LENGTH OTHER", "owner_mailing": "302 MARY LOU AVE GLEN BURNIE MD 21060-6930",
         "decedent_on_title": ["WHITLOCK PAUL R"], "co_owners_not_in_probate": ["WHITLOCK ALMY S"],
         "owner_of_record_is_decedent": True, "trust_vesting": False, "match": "address", "match_note": ""}
refs = D.parcel_refs(TITLE)
check("deed: refs", (refs["book_pages"], refs["account"], refs["street_number"]), ({("2483", "123")}, "17384690", "302"))
term_p = {"last": "WHITLOCK", "first": "PAUL", "label": "owner"}
term_a = {"last": "WHITLOCK", "first": "ALMY", "label": "title owner"}
rows = [
    {"date": "2020-11-23", "party": "Grantee: WHITLOCK II, PAUL E", "instrument_type": "DEED", "book_page": "Book 35656, pp. 90-95",
     "remarks": "512310670525 AD 202 MARIE AVE", "info_viewer_url": ""},
    {"date": "1998-08-28", "party": "Grantee: WHITLOCK , PAUL R", "instrument_type": "MODIFICATION DEED OF TRUST", "book_page": "Book 8655, pp. 714-718",
     "remarks": "", "info_viewer_url": ""},
    {"date": "1972-04-21", "party": "Grantee: WHITLOCK , PAUL R", "instrument_type": "DEED", "book_page": "Book 2483, pp. 123-124",
     "remarks": "05", "info_viewer_url": ""},
]
insts = [D.to_instrument(r, term_p) for r in rows] + [D.to_instrument({**rows[2], "party": "Grantee: WHITLOCK , ALMY S"}, term_a)]
check("deed: a deed of trust is not a conveyance", insts[1]["is_conveyance"], False)
check("deed: a DEED is", insts[2]["is_conveyance"], True)
entry = D.finalize("k", [{"term": "WHITLOCK PAUL", "count": 3}], insts, refs, "both")
check("deed: the 1972 deed merged across the two terms", entry["n_instruments"], 3)
merged = [i for i in entry["instruments"] if i["doc_number"] == "Book 2483, pp. 123-124"][0]
check("deed: merged grantee names", merged["grantee"], "WHITLOCK PAUL R + WHITLOCK ALMY S")
check("deed: on parcel by Book/page", merged["on_parcel"], True)
check("deed: namesake deed with another tax account is OFF parcel",
      [i for i in entry["instruments"] if i["recorded_date"] == "2020-11-23"][0]["on_parcel"], False)
check("deed: n_on_parcel", entry["n_on_parcel"], 1)
check("deed: last conveyance is the 1972 deed, not the 2020 namesake",
      entry["last_conveyance"]["recorded_date"], "1972-04-21")
check("deed: no 2026 flag", any("2026" in f for f in entry["flags"]), False)
g_only = D.finalize("k", [{"term": "X"}], [], refs, "grantee")
check("deed: no hit + grantee-only flags", g_only["flags"],
      ["grantee search only - a later sale BY the owner would not show", "no index hit"])
sold = D.finalize("k", [{"term": "X"}], [D.to_instrument(
    {"date": "2026-03-01", "party": "Grantor: WHITLOCK , PAUL R", "instrument_type": "DEED", "book_page": "Book 40000, p. 1",
     "remarks": "", "info_viewer_url": ""}, term_p)], {"book_pages": set(), "account": "", "street_number": "", "legal": ""}, "both")
check("deed: owner as GRANTOR in 2026 flags both",
      sorted(f for f in sold["flags"] if "GRANTOR" in f or "2026" in f),
      ["instrument recorded 2026 - possible post-death transfer", "owner is GRANTOR on last conveyance - may no longer hold title"])

# ── Estate Search by name ──
REC = {"owner_first": "Paul", "owner_last": "Whitlock", "decedent_first": "Paul", "decedent_last": "Whitlock",
       "county": "Anne Arundel", "state": "MD", "street": "302 Mary Lou Ave"}
check("estate: owner term only when decedent repeats it", E.build_terms(REC, None), [{"last": "WHITLOCK", "first": "PAUL", "label": "owner"}])
terms = E.build_terms({**REC, "owner_last": "Whitmon-Clark", "owner_first": "Mary L.", "decedent_last": "Whitmon-Clark", "decedent_first": "Mary"},
                      {"decedent_name_research": "Mary L. Whitmon-Clark"})
check("estate: hyphenated surname -> whole + both parts, research dup dropped",
      [(t["last"], t["first"]) for t in terms], [("WHITMON CLARK", "MARY"), ("WHITMON", "MARY"), ("CLARK", "MARY")])
grid = {"name_raw": "WHITLOCK , PAUL RONALD", "date_of_death": "08/09/2026", "estate_number": "119190"}
check("estate: row matches", E.row_matches(grid, {"last": "WHITLOCK", "first": "PAUL"}), True)
check("estate: first name mismatch", E.row_matches(grid, {"last": "WHITLOCK", "first": "PETER"}), False)
check("estate: surname prefix (WHITLOCKE) is not a match", E.row_matches({"name_raw": "WHITLOCKE , PAUL"}, {"last": "WHITLOCK", "first": "PAUL"}), False)
check("estate: Jr. suffix in the grid surname", E.row_matches({"name_raw": "HOFFMAN JR., GEORGE E."}, {"last": "HOFFMAN", "first": "GEORGE"}), True)
check("estate: one candidate, DOD within 45 days", E.choose([grid], "2026-08-09")[1], "name+dod")
check("estate: one candidate, DOD 2 years off -> conflict", E.choose([grid], "2024-08-09")[0], None)
check("estate: one candidate, no research DOD, recent death", E.choose([grid], "")[1], "name")
check("estate: one candidate, no research DOD, 1998 estate -> review, not joined",
      E.choose([{**grid, "date_of_death": "04/30/1998"}], "")[0], None)
two = [grid, {**grid, "estate_number": "100000", "date_of_death": "01/01/2015"}]
check("estate: two candidates, DOD picks one", E.choose(two, "2026-08-20")[0]["estate_number"], "119190")
check("estate: two candidates, no DOD -> not chosen", E.choose(two, "")[0], None)

# ── court entry (MD) ──
rec_md = {**REC, "anchors": {"estate_number": "119190", "pr_full": "Albert Whitlock", "pr_mailing": "516 Moonflower Ct Millersville, MD 21108",
                             "dod": "2026-08-09", "file_date": "2026-09-25", "decedent_full": "Paul Ronald Whitlock", "estate_status": "OPEN",
                             "court": "Anne Arundel Register of Wills"}}
ent = C.court_entry_mdva(rec_md, {"Millersville"})
check("court: anchors estate number becomes case_no", (ent["case_no"], ent["join_basis"], ent["status"]), ("119190", "anchors", "OPEN"))
check("court: anchors is NOT a party list", b._v5_has_case(ent), False)
extra_hit = {"terms": [{"last": "WHITLOCK", "first": "PAUL"}], "hits": [grid], "chosen": {**grid, "filing_date": "09/25/2026", "status": "OPEN",
             "matched_term": "WHITLOCK, PAUL (owner)"}, "basis": "name+dod", "county": "Anne Arundel", "searched_at": "2026-10-02T12:00:00"}
ent2 = C.court_entry_mdva(REC, set(), extra_hit)
check("court: name-search hit joins", (ent2["case_no"], ent2["join_basis"], ent2["dod"], ent2["filing_date"], ent2["decedent_name"]),
      ("119190", "name_search", "2026-08-09", "2026-09-25", "Paul Ronald Whitlock"))
check("court: name-search is not a party list either", b._v5_has_case(ent2), False)
ent3 = C.court_entry_mdva(REC, set(), {"terms": [{"last": "WHITLOCK", "first": "PAUL"}], "hits": [], "chosen": None,
                                        "basis": "no_candidate", "county": "Anne Arundel", "searched_at": "2026-10-02T12:00:00"})
check("court: a miss stays caseless and says so", (ent3["case_no"], ent3["note"]),
      (None, "MD: no Register of Wills estate under WHITLOCK, PAUL in Anne Arundel as of 2026-10-02"))
check("court: anchors win over a name-search hit", C.court_entry_mdva(rec_md, set(), extra_hit)["join_basis"], "anchors")

# ── note lines ──
F = {"verdict": "died", "dod": "2026-08-09", "decedent_name": "Paul Ronald Whitlock"}
tl = b._v5_title_line(rec_md, TITLE, ["WHITLOCK ALMY S"], False)
check("title line: SDAT head (roll order LAST FIRST kept - no comma to re-order on)",
      tl.startswith("TITLE (MD SDAT roll, acct 05-17384690): WHITLOCK PAUL R + WHITLOCK ALMY S - last transfer 1972-04-20 ($0 - non-arms length other)"), True)
check("title line: co-owner sentence", "WHITLOCK ALMY S is also on title and not the decedent" in tl, True)
check("title line: same mailing not repeated", "mailing" in tl, False)
check("title line: SDAT no parcel", b._v5_title_line(rec_md, {"source": "none", "match_note": "SDAT: no parcel for the street address", "owners": []}, [], False),
      "TITLE: no SDAT parcel for this street address - VERIFY the owner of record by hand")
dl = b._v5_deed_line(rec_md, entry)
check("deed line: MDLANDREC wording (matched party only - the index shows one side)",
      dl.startswith("DEED (MDLANDREC index): last conveyance 1972-04-21 DEED, grantee WHITLOCK PAUL R + WHITLOCK ALMY S (Book 2483, pp. 123-124) (05)"), True)
check("deed line: count on parcel", "1 on this parcel of 3 under the names" in dl, True)
check("deed line: MD vesting text", dl.endswith("vesting unread - MDLANDREC image (free, one scrape, on a go)"), True)
check("deed line: no hit", b._v5_deed_line(rec_md, g_only), "DEED: no MDLANDREC index hit under X; vesting unread")
parts = b._v5_note_parts(REC, {"rels": []}, F, ent2, {}, {}, {}, {}, None, "", False, False, {})
check("case line: name-search hit", parts["case_line"].startswith("CASE: estate 119190 - Anne Arundel Register of Wills, filed 2026-09-25, OPEN, DOD 2026-08-09 (found by decedent-name search"), True)
check("case line: name-search flag", any("decedent-NAME search" in f for f in parts["flags"]), True)
parts3 = b._v5_note_parts(REC, {"rels": []}, F, ent3, {}, {}, {}, {}, None, "", False, False, {})
check("case line: searched miss", parts3["case_line"].startswith("CASE: no Anne Arundel Register of Wills estate under WHITLOCK, PAUL as of 2026-10-02 - no probate opened"), True)
parts_a = b._v5_note_parts(rec_md, {"rels": []}, F, ent, {}, {}, {}, {}, None, "", False, False, {})
check("case line: anchors-based estate prints nothing here (the anchors CASE line does)", parts_a["case_line"], "")

print()
print("ALL PASS" if not FAILS else f"{FAILS} FAILED")
sys.exit(1 if FAILS else 0)
