"""Offline guard for the v5-method reconciliation in obituary_dp_batch (2026-10-02).

Covers: _matches_decedent (the middle-name spouse fix), _natural_name, the court / title /
obituary / label basis, co-owner detection against the court list, the TITLE / DEED /
COURT PARTIES lines and the who-must-sign prefix. Synthetic data modelled on 1391
Pennsylvania Ave SE #433. No network, no files touched.

    python -X utf8 tools/manual/test_dp_title_court.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import obituary_dp_batch as b  # noqa: E402

FAILS = 0


def check(label: str, got, want) -> None:
    global FAILS
    ok = got == want
    FAILS += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n       got  {got!r}\n       want {want!r}"))


# ── _matches_decedent ──
check("dec: strict first+last", b._matches_decedent("David Beardsley Smith", "DAVID SMITH"), True)
check("dec: middle-name-as-first (Lynn Wayne Hill / WAYNE HILL)", b._matches_decedent("Lynn Wayne Hill", "WAYNE HILL"), True)
check("dec: living spouse carrying the husband's name as a middle name is NOT the decedent",
      b._matches_decedent("Arjan Aswani", "Kavita Arjan Aswani"), False)
check("dec: different surname", b._matches_decedent("Lynn Wayne Hill", "WAYNE HALL"), False)
check("dec: single token never matches", b._matches_decedent("Smith", "DAVID SMITH"), False)

# ── _natural_name ──
check("natural: Last, First M", b._natural_name("Berk, Ben A"), "Ben A Berk")
check("natural: upper", b._natural_name("SMITH,  HELEN H"), "HELEN H SMITH")
check("natural: already natural", b._natural_name("Jason Abraham Beardsley-Smith"), "Jason Abraham Beardsley-Smith")
check("natural: blank", b._natural_name(""), "")
check("natural: suffix segment in the middle", b._natural_name("Morgan, Jr, Donald G"), "Donald G Morgan Jr")
check("natural: suffix segment at the end", b._natural_name("Morgan, Donald G, Jr"), "Donald G Morgan Jr")

# ── fixtures (1391 Pennsylvania) ──
COURT = {"case_no": "2026-ADM-001053", "join_basis": "case_number", "filing_date": "09/25/2026", "status": "Open",
         "dod": "08/13/2026", "decedent_name": "Smith, David Beardsley", "will_on_file": True,
         "pr": [{"name": "Beardsley-Smith, Jason Abraham", "connection": "Applicant",
                 "street": "276 Berkeley Pl", "city": "Brooklyn", "state": "NY", "zip": "11217"}],
         "heirs": [{"name": "Devary, Sarah Smith", "connection": "Legatee", "street": "6310 E 214th St",
                    "city": "Belton", "state": "MO", "zip": "64012"},
                   {"name": "Smith, Laura Ann", "connection": "Legatee", "street": "1421 S 5th St E",
                    "city": "Louisburg", "state": "KS", "zip": "66053"}]}
TITLE = {"owners": ["SMITH, HELEN H", "SMITH, DAVID B"], "owner_mailing": "1391 PENNSYLVANIA AVE SE #433",
         "sale_date": "2019-08-06", "sale_price": 811000, "ssl": "1045-2155", "source": "itspe", "match": "address",
         "owner_of_record_is_decedent": True,
         "household_subject": {"age": 80, "n_lines": 2, "lines": [{"number": "2025340768", "type": "Mobile"}]}}
DEED = {"searches": [{"term": "SMITH DAVID", "types": ["grantor", "grantee"], "d0": "19900101", "d1": "20261002",
                      "count": 240, "capped": True}],
        "instruments": [{"recorded_date": "2019-10-23", "doc_type": "DEED", "doc_number": "2019111111",
                         "grantor": "SMITH HELEN H; SMITH DAVID B", "grantee": "NURNBERGER MARC",
                         "legal": "SQ 1038 LOT 68", "consideration": "", "flags": ["deed"]},
                        {"recorded_date": "2020-03-02", "doc_type": "RELEASE", "doc_number": "2020000001",
                         "grantor": "BANK", "grantee": "SMITH DAVID B", "legal": "SQ 1038 LOT 68", "flags": ["release"]}],
        "last_instrument": None, "vesting": "unread - image $4", "flags": ["common name - capped", "deed", "release", "mortgage"], "source": "dc_rod_index"}
DEED["last_instrument"] = DEED["instruments"][1]
REC = {"owner_first": "David", "owner_last": "Smith", "decedent_first": "David", "decedent_last": "Smith",
       "ss_first": "DAVID", "ss_last": "SMITH", "street": "1391 Pennsylvania Ave SE #433", "state": "DC",
       "pr": "Jason Abraham Beardsley-smith",
       "anchors": {"decedent_full": "David Beardsley Smith", "pr_full": "Jason Abraham Beardsley-smith"}}
F = {"decedent_name": "David Beardsley Smith", "dod": "2026-08-13", "verdict": "owner did die", "drop_names": []}
RELS = [{"n": 1, "name": "SARAH DEVARY", "first": "SARAH", "last": "DEVARY", "canon_rel": "Child", "is_dm": True, "phones": [{"number": "9132309865"}]},
        {"n": 2, "name": "RHEA R SMITH", "first": "RHEA", "last": "SMITH", "canon_rel": "Relative", "is_dm": True, "phones": []},
        {"n": 3, "name": "HELEN SMITH", "first": "HELEN", "last": "SMITH", "canon_rel": "Spouse", "is_dm": True, "phones": []},
        {"n": 4, "name": "JAMES MORGAN", "first": "JAMES", "last": "MORGAN", "canon_rel": "Relative", "is_dm": False, "phones": []}]
RK = {"rels": RELS, "subject": {"first": "", "last": "Smith", "age": 80, "phones": []}}

# ── court record ──
check("court: has case", b._v5_has_case(COURT), True)
check("court: no case", b._v5_has_case({"case_no": None, "join_basis": None, "note": "no case on file"}), False)
check("court: MD anchors-only PR is not a party list", b._v5_has_case({"case_no": "estate 12345", "join_basis": "anchors", "pr": [{"name": "Jane Doe"}]}), False)
md_parts = b._v5_note_parts({**REC, "state": "MD"}, RK, F, {"case_no": "estate 12345", "join_basis": "anchors", "pr": [{"name": "Jane Doe"}], "note": "MD/VA: PR from anchors only"}, {}, {}, {}, {}, None, "Jane Doe", False, False, {})
check("parts: MD anchors-only -> no CASE line, no court block, no prefix", (md_parts["case_line"], md_parts["court_lines"], md_parts["signer_prefix"]), ("", [], ""))
check("court: party names natural", b._v5_party_names(COURT)[:2], ["Jason Abraham Beardsley-Smith", "Sarah Smith Devary"])
check("court: Sarah is on the record (loose: SARAH DEVARY vs Sarah Smith Devary)", b._v5_on_court_record("SARAH DEVARY", COURT), True)
check("court: Rhea is on no record", b._v5_on_court_record("RHEA R SMITH", COURT), False)
check("court: nothing matches without a case", b._v5_on_court_record("SARAH DEVARY", {}), False)

# ── basis ──
check("basis: court beats everything", b._v5_rel_basis(RELS[0], COURT, TITLE, {"SARAH DEVARY"}, "David Beardsley Smith"), "court")
check("basis: title owner (Helen) -> title", b._v5_rel_basis(RELS[2], COURT, TITLE, set(), "David Beardsley Smith"), "title")
check("basis: a title owner who IS the decedent is not 'title'",
      b._v5_rel_basis({"name": "DAVID SMITH"}, {}, TITLE, set(), "David Beardsley Smith"), "label")
check("basis: obituary", b._v5_rel_basis(RELS[1], {}, {}, {"RHEA R SMITH"}, "David Beardsley Smith"), "obituary")
check("basis: label", b._v5_rel_basis(RELS[1], COURT, TITLE, set(), "David Beardsley Smith"), "label")

# ── co-owners ──
check("co-owner: Helen flagged, David (decedent) not", b._v5_co_owners(REC, F, TITLE, COURT), ["HELEN H SMITH"])
check("co-owner: a title owner who is the PR is not flagged",
      b._v5_co_owners(REC, F, {"owners": ["SMITH, DAVID B", "BEARDSLEY-SMITH, JASON A"]}, COURT), [])
check("co-owner: a court legatee on title is not flagged",
      b._v5_co_owners(REC, F, {"owners": ["SMITH, LAURA ANN"]}, COURT), [])
check("co-owner: no title = none", b._v5_co_owners(REC, F, {}, COURT), [])
COBB = {**REC, "owner_first": "Essie", "owner_last": "Cobb", "decedent_first": "Essie", "decedent_last": "Cobb",
        "ss_first": "ESSIE", "ss_last": "COBB", "anchors": None, "pr": ""}
check("co-owner: TRUSTEE suffix stripped, decedent as trustee is not a co-owner",
      b._v5_co_owners(COBB, {"decedent_name": "Essie Mae Cobb"},
                      {"owners": ["ESSIE M COBB TRUSTEE"], "trust_vesting": True, "decedent_on_title": ["ESSIE M COBB"]}, {}), [])
check("co-owner: dp_title's probable spelling match is not a co-owner",
      b._v5_co_owners({**REC, "owner_first": "Dwight", "owner_last": "Stancil", "anchors": None, "pr": ""}, {"decedent_name": "Dwight Stancil"},
                      {"owners": ["NASH, EVON", "STACIL, DWIGHT"], "decedent_probable": ["STACIL, DWIGHT"]}, {}), ["EVON NASH"])
check("strip trustee", b._v5_strip_trustee("NORMAN M LINSKY TR"), "NORMAN M LINSKY")

# ── lines ──
tl = b._v5_title_line(REC, TITLE, ["HELEN H SMITH"], True)
check("title line: owners + sale", tl.startswith("TITLE (DC tax roll, SSL 1045-2155): HELEN H SMITH + DAVID B SMITH - bought 2019-08-06 for $811,000"), True)
check("title line: co-owner clause", "HELEN H SMITH is NOT a party to the probate" in tl, True)
check("title line: mailing equal to the property is not repeated", "; mailing" in tl, False)
check("title line: no match DC", b._v5_title_line(REC, {"source": "none", "match": "none"}, []),
      "TITLE: no tax-roll match for this address (ITSPE) - VERIFY the owner of record by hand")
check("title line: MD not checked", b._v5_title_line({"state": "MD"}, {"source": "none", "match": "none"}, []),
      "TITLE: not checked (SDAT lookup needs a Firecrawl go)")
check("title line: owner is not the decedent",
      "DECEDENT NOT ON TITLE (last transfer 2019-08-06) - the owners above hold it" in b._v5_title_line(REC, {**TITLE, "owner_of_record_is_decedent": False, "owners": ["JONES, ANN"]}, ["ANN JONES"]), True)
check("title line: $0 transfer + trustee wording",
      "last transfer 2023-01-18 ($0 - family / trust deed); held AS TRUSTEE" in
      b._v5_title_line(REC, {"owners": ["ESSIE M COBB TRUSTEE"], "source": "itspe", "sale_date": "2023-01-18", "sale_price": 0.0, "trust_vesting": True}, []), True)
check("title line: co-owner with NO case uses the survivorship wording",
      "EVON NASH is also on title and not the decedent" in
      b._v5_title_line(REC, {"owners": ["NASH, EVON", "STACIL, DWIGHT"], "source": "itspe", "decedent_probable": ["STACIL, DWIGHT"]}, ["EVON NASH"], False), True)
check("title line: no sidecar = blank", b._v5_title_line(REC, {}, []), "")

dl = b._v5_deed_line(REC, DEED)
check("deed line: last CONVEYANCE, not the later release",
      dl, "DEED (Recorder of Deeds index): last conveyance 2019-10-23 DEED SMITH HELEN H; SMITH DAVID B -> NURNBERGER MARC (SQ 1038 LOT 68); "
          "later RELEASE 2020-03-02; 2 instruments since 1990; vesting unread - image $4")
check("deed: noise flags filtered", b._v5_deed_flags({"flags": ["deed", "release", "mortgage", "lien", "transfer on death deed", "owner is GRANTOR on last conveyance - may no longer hold title"]}),
      ["transfer on death deed", "owner is GRANTOR on last conveyance - may no longer hold title"])
TOD = {"instruments": [{"recorded_date": "2025-03-25", "doc_type": "TRANSFER ON DEATH DEED", "grantor": "FICKLIN SADIE", "grantee": "FICKLIN BRUCE JR",
                        "legal": "SQ 5389 LOT 0826", "is_conveyance": True, "on_parcel": True, "flags": ["transfer on death deed"]}],
       "flags": ["transfer on death deed", "owner is GRANTOR on last conveyance - may no longer hold title"]}
TOD["last_conveyance"] = TOD["instruments"][0]
tod = b._v5_note_parts({**REC, "anchors": None, "pr": "", "owner_first": "Sadie", "owner_last": "Ficklin"}, RK, {"decedent_name": "Sadie E Ficklin", "verdict": "owner did die"}, {"case_no": None, "join_basis": None},
                       {"owners": ["FICKLIN, SADIE E"], "source": "itspe", "owner_of_record_is_decedent": True}, TOD, {}, {}, None, "", False, False, {})
check("parts: TOD deed -> beneficiary signs", (tod["signer_prefix"].startswith("A TRANSFER ON DEATH deed is on record (FICKLIN SADIE -> FICKLIN BRUCE JR, 2025-03-25)"),
      "TOD deed to FICKLIN BRUCE JR - passes outside probate" in tod["flags"], "TOD deed not revoked; beneficiary FICKLIN BRUCE JR living" in tod["must_verify"]), (True, True, True))
FC = {"instruments": [{"recorded_date": "2018-08-14", "doc_type": "DEED", "grantor": "AIELLO", "grantee": "BEPPU JENNIFER", "is_conveyance": True, "on_parcel": True, "flags": ["deed"]},
                      {"recorded_date": "2026-05-26", "doc_type": "LIS PENDENS", "grantor": "NEWREZ", "grantee": "BEPPU JENNIFER", "is_conveyance": False, "on_parcel": True, "flags": ["foreclosure notice"]}],
      "flags": ["foreclosure notice"]}
FC["last_conveyance"] = FC["instruments"][0]
fcp = b._v5_note_parts({**REC, "anchors": None, "pr": "", "owner_first": "Jennifer", "owner_last": "Beppu"}, RK, {"decedent_name": "Jennifer Beppu", "verdict": "owner did die"}, {"case_no": None, "join_basis": None},
                       {"owners": ["BEPPU, JENNIFER"], "source": "itspe", "owner_of_record_is_decedent": True}, FC, {}, {}, None, "", False, False, {})
check("parts: 2026 lis pendens -> foreclosure flag", any(x.startswith("FORECLOSURE activity on the parcel - LIS PENDENS 2026-05-26") for x in fcp["flags"]), True)
check("parts: no TOD prefix on a plain deed", fcp["signer_prefix"], "")
check("deed: conveyance by doc type", b._v5_is_conveyance({"doc_type": "QUITCLAIM DEED", "flags": []}), True)
check("deed: deed of trust is not a conveyance", b._v5_is_conveyance({"doc_type": "TRUST", "flags": ["mortgage"]}), False)
check("deed: explicit is_conveyance wins", b._v5_is_conveyance({"doc_type": "TRUST", "is_conveyance": True}), True)
check("deed line: instruments but no conveyance",
      b._v5_deed_line(REC, {"instruments": [{"recorded_date": "2021-05-05", "doc_type": "RELEASE", "flags": ["release"]}], "last_instrument": {"recorded_date": "2021-05-05", "doc_type": "RELEASE", "flags": ["release"]}, "flags": ["release"]}),
      "DEED (Recorder of Deeds index): no conveyance on this parcel under the searched names since 1990; last instrument 2021-05-05 RELEASE; 1 instruments since 1990; vesting unread - image $4")
check("deed: module's own last_conveyance wins", b._v5_last_conveyance({"last_conveyance": {"doc_number": "X", "recorded_date": "2024-05-01"}, "instruments": [{"recorded_date": "2026-07-29", "doc_type": "DEED", "is_conveyance": True, "on_parcel": False}]})["doc_number"], "X")
check("deed: off-parcel conveyance never picked", b._v5_last_conveyance({"instruments": [{"recorded_date": "2026-07-29", "doc_type": "DEED", "is_conveyance": True, "on_parcel": False}, {"recorded_date": "2025-09-18", "doc_type": "DEED", "is_conveyance": True, "on_parcel": True}]})["recorded_date"], "2025-09-18")
check("deed: 'trust' (deed of trust paper) is noise", b._v5_deed_flags({"flags": ["trust", "transfer on death deed", "estate as party"]}), ["transfer on death deed", "estate as party"])
check("deed line: on-parcel count", "2 on this parcel of 5 under the names" in b._v5_deed_line(REC, {**DEED, "n_on_parcel": 2, "instruments": DEED["instruments"] * 2 + [DEED["instruments"][1]]}), True)
check("deed line: no hit", b._v5_deed_line(REC, {"searches": [{"term": "TENEYCK BRENDA", "capped": False}], "instruments": [], "flags": ["no index hit"]}),
      "DEED: no Recorder of Deeds index hit under TENEYCK BRENDA (1990-2026); vesting unread")
check("deed line: no sidecar = blank", b._v5_deed_line(REC, {}), "")

basis = {r["name"]: b._v5_rel_basis(r, COURT, TITLE, set(), "David Beardsley Smith") for r in RELS}
cl = b._v5_court_lines(COURT, RK, basis, {1: ["Dial First"]}, None, {})
check("court lines: header", cl[0], "COURT PARTIES (2026-ADM-001053, filed 09/25/2026, Open; a WILL is on file - heirs are Legatees)")
check("court lines: PR with address, no number", cl[1], "PR - Jason Abraham Beardsley-Smith (Applicant) - 276 Berkeley Pl, Brooklyn NY 11217 - no number on file")
check("court lines: legatee matched to REL1 with its tier", cl[2], "Sarah Smith Devary - Legatee - 6310 E 214th St, Belton MO 64012 - SmartSkip/IDI REL1 (Dial First)")
check("court lines: legatee not in SmartSkip", cl[3], "Laura Ann Smith - Legatee - 1421 S 5th St E, Louisburg KS 66053 - no number on file")
check("court lines: none without a case", b._v5_court_lines({}, RK, basis, {}, None, {}), [])
RK_ASIDE = {"rels": [RELS[1], RELS[3]], "subject": RK["subject"],
            "excluded_smartskip_namesake": {"rels": [RELS[0]], "why": "SmartSkip traced another David Smith"}}
cl2 = b._v5_court_lines(COURT, RK_ASIDE, {}, {}, None, {"9132309865": {"tier": "Dial First"}})
check("court lines: a legatee in the namesake set-aside is found there with her tier", cl2[2],
      "Sarah Smith Devary - Legatee - 6310 E 214th St, Belton MO 64012 - SmartSkip had them (set aside as a namesake's family; the court says otherwise); best number on file Dial First")
check("court overruled: names", b._v5_court_overruled(COURT, RK_ASIDE), ["SARAH DEVARY"])
check("court overruled: nothing without a case", b._v5_court_overruled({}, RK_ASIDE), [])
pa = b._v5_note_parts(REC, RK_ASIDE, F, COURT, TITLE, {}, {}, {}, None, REC["pr"], False, False, {})
check("parts: overruled set-aside flag", any(x.startswith("SmartSkip namesake set-aside OVERRULED") and "SARAH DEVARY" in x for x in pa["flags"]), True)

# ── note parts ──
parts = b._v5_note_parts(REC, RK, F, COURT, TITLE, DEED, basis, {1: ["Dial First"]}, None, REC["pr"], False, False, {})
check("parts: signer prefix names the PR and the co-owner question",
      parts["signer_prefix"],
      "PR Jason Abraham Beardsley-Smith signs for the estate (will on file - the heirs are legatees); "
      "co-owner HELEN H SMITH on title is NOT a party to the probate - living / deceased / divorced UNKNOWN - decides the deal")
check("parts: Rhea flagged on no record", any(x.startswith("On NO record") and "RHEA R SMITH" in x for x in parts["not_loaded"]), True)
check("parts: Sarah (court) and Helen (title) are not 'on no record'",
      any("SARAH DEVARY" in x or "HELEN SMITH" in x for x in parts["not_loaded"] if x.startswith("On NO record")), False)
check("parts: household subject line", any(x.startswith("Household line at the address") and "likely HELEN H SMITH" in x for x in parts["not_loaded"]), True)
check("parts: flags (deed noise filtered out)", parts["flags"], ["co-owner HELEN H SMITH on title, not in the probate", "signer(s) on no record"])
check("parts: must verify", parts["must_verify"],
      ["no transfer since the tax-roll read", "HELEN H SMITH living or deceased", "deed vesting (Recorder of Deeds image, $4)", "PR letters issued"])
check("parts: case line from the court join (printed only when anchors carry no case)", parts["case_line"],
      "CASE: 2026-ADM-001053 - DC Superior Court, Probate Division, filed 09/25/2026, Open; a WILL is on file (heirs are Legatees)")

nocase = b._v5_note_parts({**REC, "anchors": None, "pr": ""}, RK, {**F, "verdict": "unresolved"},
                          {"case_no": None, "join_basis": None, "note": "no case on file"}, {"source": "none", "match": "none"}, {}, {}, {}, None, "", False, False, {})
check("parts: no case -> CASE line says so", nocase["case_line"], "CASE: no case on file")
check("parts: no case -> no signer prefix", nocase["signer_prefix"], "")
check("parts: no case DC -> must verify names the portal", nocase["must_verify"][-1].startswith("DC probate portal by decedent name"), True)
off = b._v5_note_parts({**REC, "anchors": None, "pr": "", "owner_first": "Evelyn", "owner_last": "Bellamy"}, RK,
                       {"decedent_name": "Evelyn Bellamy", "verdict": "owner did die"}, {"case_no": None, "join_basis": None},
                       {"owners": ["BELLAMY, LEWIS", "TITUS, RICKEY"], "source": "itspe", "sale_date": "2026-09-15", "sale_price": 0.0,
                        "owner_of_record_is_decedent": False, "co_owners_not_in_probate": ["BELLAMY, LEWIS", "TITUS, RICKEY"],
                        "household_subject": {"age": 85, "lines": ["2023617955 (Mobile)"]}}, {}, {}, {}, None, "", False, False, {})
check("parts: decedent off title -> prefix + flag + must verify",
      (off["signer_prefix"].startswith("DECEDENT NOT ON TITLE - LEWIS BELLAMY and RICKEY TITUS hold the property since 2026-09-15"),
       off["flags"][0], off["must_verify"][0]),
      (True, "decedent NOT on title - transfer 2026-09-15", "who holds title now and under what deed (decedent is OFF title)"))
quiet = b._v5_note_parts({**REC, "anchors": None, "pr": ""}, RK, F, {"case_no": None, "join_basis": None},
                         {"owners": ["SMITH, DAVID B"], "source": "itspe", "owner_of_record_is_decedent": True,
                          "household_subject": {"age": 90, "lines": ["2025551212 (Mobile)"]}}, {}, {}, {}, None, "", False, False, {})
check("parts: sole owner, subject aged 90 -> no household line", quiet["not_loaded"], [])
young = b._v5_note_parts({**REC, "anchors": None, "pr": ""}, RK, F, {"case_no": None, "join_basis": None},
                         {"owners": ["SMITH, DAVID B"], "source": "itspe", "owner_of_record_is_decedent": True,
                          "household_subject": {"age": 37, "lines": ["2025551212 (Mobile)"]}}, {}, {}, {}, None, "", False, False, {})
check("parts: sole owner, subject aged 37 -> household line printed", any(x.startswith("Household line") for x in young["not_loaded"]), True)
tr = b._v5_note_parts(COBB, RK, {"decedent_name": "Essie Mae Cobb", "verdict": "owner did die"}, {"case_no": None, "join_basis": None},
                      {"owners": ["ESSIE M COBB TRUSTEE"], "source": "itspe", "trust_vesting": True, "decedent_on_title": ["ESSIE M COBB"],
                       "owner_of_record_is_decedent": True, "household_subject": {"age": 37, "lines": []}}, {}, {}, {}, None, "", False, False, {})
check("parts: trust vesting -> trustee prefix + flag", (tr["signer_prefix"], tr["flags"]),
      ("Title is held in TRUST - the successor trustee signs, not the heirs; trust instrument unread", ["title held in trust - successor trustee signs"]))

empty = b._v5_note_parts(REC, RK, F, {}, {}, {}, {}, {}, None, "", False, False, {})
check("parts: no sidecars -> everything blank (old behaviour)",
      (empty["title_line"], empty["deed_line"], empty["case_line"], empty["court_lines"], empty["signer_prefix"], empty["flags"], empty["not_loaded"]),
      ("", "", "", [], "", [], []))
check("parts: presumed-alive record gets nothing", b._v5_note_parts(REC, RK, F, COURT, TITLE, DEED, basis, {}, None, "", True, True, {})["title_line"], "")

# after-death instrument
late = {**DEED, "instruments": [{**DEED["instruments"][0], "recorded_date": "2026-09-15"}]}
late["last_instrument"] = late["instruments"][0]
p2 = b._v5_note_parts(REC, RK, F, COURT, TITLE, late, basis, {}, None, REC["pr"], False, False, {})
check("parts: conveyance after the DOD is flagged", any("AFTER the DOD" in x for x in p2["flags"]), True)
rel_late = {**DEED, "instruments": [{**DEED["instruments"][1], "recorded_date": "2026-09-15"}]}
rel_late["last_instrument"] = rel_late["instruments"][0]
p3 = b._v5_note_parts(REC, RK, F, COURT, TITLE, rel_late, basis, {}, None, REC["pr"], False, False, {})
check("parts: a RELEASE after the DOD is not a transfer flag", any("AFTER the DOD" in x for x in p3["flags"]), False)

print()
print("ALL PASS" if not FAILS else f"{FAILS} FAILED")
sys.exit(1 if FAILS else 0)
