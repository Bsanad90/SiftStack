"""Guards added after the 2026-09-01 probate-push / obituary-DP collision.

Pure-function tests over obituary_dp_batch.py and obituary_dp_qa.py; both scripts are loaded
by path (they are not packages) and importing them needs no network.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


odb = _load("odb_guard_test", "src/scripts/obituary_dp_batch.py")
qa = _load("odq_guard_test", "src/scripts/obituary_dp_qa.py")

DP_NOTE = "DEEP PROSPECTING 2026-09-01 (SmartSkip + obituary + Trestle)\n\nDECEDENT: Nancy Libson"
PROBATE_NOTE = "PROBATE - Register of Wills Anne Arundel County (loaded 2026-09-01)\n\nDECEDENT (owner of record): Nancy Libson"
PROBATE_BALT = "PROBATE - Register of Wills Baltimore County (loaded 2026-09-01)\n\nx"


# ── B1: note families and the same-family deletion guard ──────────────────

def test_note_family_headers():
    assert odb._note_family(DP_NOTE) == "DEEP PROSPECTING"
    assert odb._note_family("DEEP PROSPECTING 2026-09-06 (SmartSkip + Trestle)") == "DEEP PROSPECTING"
    assert odb._note_family(PROBATE_NOTE) == "PROBATE - Register of Wills Anne Arundel County"
    assert odb._note_family("") == ""
    assert odb._note_family(None) == ""


def test_stale_messages_never_cross_families():
    board = [{"uuid": "dp-old", "message": DP_NOTE, "pinned": True},
             {"uuid": "pb-old", "message": PROBATE_NOTE, "pinned": True},
             {"uuid": "pb-new", "message": PROBATE_NOTE, "pinned": False}]
    # a PROBATE push replaces only the earlier PROBATE note of the same county
    stale = odb._stale_board_messages(board, PROBATE_NOTE, "pb-new")
    assert [m["uuid"] for m in stale] == ["pb-old"]
    # ... and a DP re-push replaces only the earlier DP note
    board2 = [{"uuid": "dp-old", "message": DP_NOTE}, {"uuid": "pb", "message": PROBATE_NOTE},
              {"uuid": "dp-new", "message": DP_NOTE}]
    assert [m["uuid"] for m in odb._stale_board_messages(board2, DP_NOTE, "dp-new")] == ["dp-old"]
    # different county = different family
    assert odb._stale_board_messages([{"uuid": "b", "message": PROBATE_BALT}], PROBATE_NOTE, "n") == []


def test_stale_messages_unrecognisable_header_deletes_nothing():
    board = [{"uuid": "x", "message": "hi"}, {"uuid": "y", "message": DP_NOTE}]
    assert odb._stale_board_messages(board, "hi there", "new") == []
    assert odb._stale_board_messages(board, "", "new") == []


# ── B2: loose name matching, strict matcher untouched ─────────────────────

@pytest.mark.parametrize("a,b", [
    ("Judy McAuley", "JUDITH MCAULEY"),
    ("Sally Libson", "SARAH LIBSON"),
    ("Bill Dauses", "WILLIAM DAUSES"),
    ("Steph Green", "STEPHANIE GREEN"),      # >=4-char prefix
    ("Christopher Swam", "CHRIS SWAM"),
    ("Karen A. Morrow", "KAREN MORROW"),      # middle initial ignored
])
def test_names_match_loose_accepts(a, b):
    assert odb.names_match_loose(a, b)
    assert odb.names_match_loose(b, a)


@pytest.mark.parametrize("a,b", [
    ("Ann Smith", "ANNA SMITH"),              # 3-char prefix is not enough
    ("Judy McAuley", "JUDITH MCALLISTER"),    # last token must be exact
    ("Karen Morrow", "MORROW KAREN"),
    ("Judy", "JUDITH MCAULEY"),               # single token never matches
])
def test_names_match_loose_rejects(a, b):
    assert not odb.names_match_loose(a, b)


def test_prefix_rule_can_be_disabled():
    assert odb.names_match_loose("Rose Hall", "ROSEMARY HALL")
    assert not odb.names_match_loose("Rose Hall", "ROSEMARY HALL", prefix=False)
    assert odb.names_match_loose("Judy Hall", "JUDITH HALL", prefix=False)   # nickname table still applies


def test_standalone_given_names_are_not_nicknames():
    # Lisa, Carol, Lynn, Jamie, Harry, Terry are people's actual names, not short forms
    for a, b in [("Lisa Jones", "ELIZABETH JONES"), ("Carol Lee", "CAROLYN LEE"),
                 ("Jamie Roe", "JAMES ROE"), ("Harry Doe", "HENRY DOE")]:
        assert not odb.names_match_loose(a, b, prefix=False), (a, b)
    # pairs the SOI table already knew are honoured here too
    assert odb.names_match_loose("Stephen Green", "STEVEN GREEN", prefix=False)


def test_legacy_paragraph_note_keeps_its_family():
    legacy = "DEEP PROSPECTING 2026-08-30  ||  DECEDENT: John Doe - DOD 2020-01-01 (obituary)  ||  VERDICT: x"
    assert odb._note_family(legacy) == "DEEP PROSPECTING"


def test_cross_check_pairs_one_to_one():
    f = {"survivors": [{"name": "Paul Smith", "relationship": "son"},
                       {"name": "Paula Smith", "relationship": "daughter"},
                       {"name": "Judy McAuley", "relationship": "niece"}]}
    rels = [{"name": "PAUL SMITH"}, {"name": "JUDITH MCAULEY"}]
    cc = odb._cross_check(f, rels)
    assert cc["words"] == {"PAUL SMITH": "son", "JUDITH MCAULEY": "niece"}
    assert cc["obit_only"] == ["Paula Smith"]          # a prefix collision never silences her
    assert cc["paired_survivors"] == ["Judy McAuley", "Paul Smith"]
    # ambiguous nickname with two candidates: strict pairs win, the rest stay unpaired
    f2 = {"survivors": [{"name": "Chris Jones", "relationship": "son"},
                        {"name": "Christine Jones", "relationship": "daughter"}]}
    cc2 = odb._cross_check(f2, [{"name": "CHRISTOPHER JONES"}, {"name": "CHRISTINE JONES"}])
    assert cc2["words"] == {"CHRISTINE JONES": "daughter", "CHRISTOPHER JONES": "son"}
    # one nickname, two same-surname candidates, nothing strict -> no pairing at all
    cc3 = odb._cross_check({"survivors": [{"name": "Chris Jones", "relationship": "son"}]},
                           [{"name": "CHRISTOPHER JONES"}, {"name": "CHRISTINE JONES"}])
    assert cc3["words"] == {} and cc3["obit_only"] == ["Chris Jones"]


def test_strict_matcher_is_unchanged():
    assert not odb.names_match("Judy McAuley", "JUDITH MCAULEY")
    assert odb.names_match("Karen A Morrow", "KAREN MORROW")
    assert not odb.names_match("Chris Swam", "CHRISTINE SWAM")   # what the predeceased drop relies on


def test_rel_label_uses_obituary_word():
    words = {"KAREN MORROW": "niece", "ANN DOE": "daughter"}
    assert odb._rel_label({"name": "KAREN MORROW", "canon_rel": "Relative"}, words) == "Niece (obituary)"
    assert odb._rel_label({"name": "ANN DOE", "canon_rel": "Daughter"}, words) == "Daughter (obituary)"
    assert odb._rel_label({"name": "ANN DOE", "canon_rel": "Sibling"}, words) == "Sibling, Daughter (obituary)"
    assert odb._rel_label({"name": "BOB ROE", "canon_rel": "Relative"}, words) == "Relative"
    assert odb._rel_label({"name": "X", "canon_rel": "In-Law"}, {"X": "sister-in-law"}) == "In-Law (obituary)"


def test_obituary_words_maps_smartskip_names_to_relationships():
    f = {"survivors": [{"name": "Judy McAuley", "relationship": "niece"},
                       {"name": "Karen Morrow", "relationship": "niece"},
                       {"name": "Robert Libson", "relationship": "brother"}]}
    rels = [{"name": "JUDITH MCAULEY"}, {"name": "KAREN MORROW"}, {"name": "SARAH LIBSON"}]
    assert odb._obituary_words(f, rels) == {"JUDITH MCAULEY": "niece", "KAREN MORROW": "niece"}


def test_signer_words_and_trace_map_untouched_by_label_map():
    # niece is a label, never a signer or a $0.02 trace target
    assert "niece" not in odb.OBIT_REL_MAP
    assert "Niece" not in odb.SIGNER_WORDS
    assert odb.OBIT_LABEL_MAP["niece"] == "Niece"


# ── B3: QA value compare and the beyond-plan diff ─────────────────────────

def test_cf_equal_normalises_phones_and_names():
    assert qa._cf_equal("REL1: Phone 1", "4123647272", "+14123647272")
    assert not qa._cf_equal("REL1: Phone 1", "4123647272", "+14123647273")
    assert qa._cf_equal("REL1: Full Name", "Sarah  Libson", "SARAH LIBSON")
    assert not qa._cf_equal("REL1: Full Name", "SARAH LIBSON", "JUDITH MCAULEY")


def test_cf_diff_on_stonecreek_shape():
    plan = {"REL1: Full Name": "SARAH LIBSON", "REL1: Phone 1": "4123647272",
            "REL1: Phone 2": "4123649234", "REL1: Phone 3": "4124968583",
            "REL2: Full Name": "JUDITH MCAULEY", "REL2: Phone 1": "4128187182",
            "REL2: Phone 2": "4124860219", "REL2: Phone 3": "4123010689",
            "REL3: Full Name": "KAREN MORROW", "REL3: Phone 1": "4129155015"}
    live = {"REL1: Full Name": "JUDITH MCAULEY", "REL1: Phone 1": "+14128187182",
            "REL1: Phone 2": "+14128187185", "REL1: Phone 3": "+14124860219",
            "REL2: Full Name": "KAREN MORROW", "REL2: Phone 1": "+14129155015",
            "REL2: Phone 2": "+14123649234", "REL2: Phone 3": "+14124860219",
            "REL3: Full Name": "FRANKLIN LIBSON", "REL3: Phone 1": "+14123647272",
            "REL3: Phone 2": "+17244540602", "REL3: Phone 3": "+14129155015",
            "REL4: Full Name": "SARAH LIBSON", "REL4: Phone 1": "+14124968583",
            "REL4: Phone 2": "+14123649234", "REL4: Phone 3": "+14123647272",
            "REL5: Full Name": "S LIBSON", "REL6: Full Name": "",
            "PR First Name": "Dori", "Personal Representative": "Dori Libson"}
    missing, wrong, extra = qa._cf_diff(plan, live)
    assert missing == []
    assert set(wrong) == set(plan)            # every planned label holds someone else's value
    assert set(extra) == {"REL3: Phone 2", "REL3: Phone 3", "REL4: Full Name", "REL4: Phone 1",
                          "REL4: Phone 2", "REL4: Phone 3", "REL5: Full Name"}
    # PR fields and empty slots are never "extra"; a clean record diffs to nothing
    clean = {k: ("+1" + v if "Phone" in k else v) for k, v in plan.items()}
    assert qa._cf_diff(plan, {**clean, "PR First Name": "Dori"}) == ([], [], [])


def test_planned_tag_is_the_exact_slot():
    assert qa._planned_tag({"tags": ["Rel2.1", "Dial First"]}) == "Rel2.1"
    assert qa._planned_tag({"tags": ["Owner.3", "Drop"]}) == "Owner.3"
    assert qa._planned_tag({"tags": ["Dial First"]}) is None
