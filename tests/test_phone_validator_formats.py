"""Phone validator export-format detection, qualification, and reinsertion.

Both real REISift export layouts were confirmed live before this was written:
  - the flat DataSift "Phone Enrichment" export (Phone 1..Phone 30 + paired
    Phone Tags 1..30), against All Records 8.21.2026.csv
  - the MDDC probate "ready for dialing" export (PR First/Last Name +
    PH: Phone1..5, plus REL1..REL5: Full Name + Phone 1..3), against
    Probate_20260812_ReadyForDialing.xlsx

The second layout is the one that was silently dropping every relative's and
PR's phone: none of its headers matched the old bare "Phone N" detector, so
zero of those numbers ever reached Trestle.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import pytest  # noqa: E402

from phone_validator import (  # noqa: E402
    detect_export_format,
    extract_phone_entries,
    evaluate_phone,
    write_reinserted_export,
)

FLAT_HEADERS = [
    "First Name", "Last Name", "Property address",
    "Phone 1", "Phone Type 1", "Phone Status 1", "Phone Tags 1", "Phone Is Connected 1",
    "Phone 2", "Phone Type 2", "Phone Status 2", "Phone Tags 2", "Phone Is Connected 2",
    "Phone 3", "Phone Type 3", "Phone Status 3", "Phone Tags 3", "Phone Is Connected 3",
]

BLOCK_HEADERS = [
    "Deceased First Name", "Deceased Last Name",
    "Property Address", "Property City", "Property State", "Property Zip",
    "PR First Name", "PR Last Name",
    "PH: Phone1", "PH: Phone2", "PH: Phone3", "PH: Phone4", "PH: Phone5",
    "REL1: Full Name", "REL1: Phone 1", "REL1: Phone 2", "REL1: Phone 3",
    "REL2: Full Name", "REL2: Phone 1", "REL2: Phone 2", "REL2: Phone 3",
]


class TestDetectFlatFormat:
    def test_recognizes_phone_n_columns(self):
        fmt = detect_export_format(FLAT_HEADERS)
        assert fmt.kind == "flat"
        assert len(fmt.contacts) == 1
        assert fmt.contacts[0].phone_columns == ["Phone 1", "Phone 2", "Phone 3"]

    def test_pairs_existing_phone_tags_columns(self):
        fmt = detect_export_format(FLAT_HEADERS)
        tag_cols = fmt.contacts[0].tag_columns
        assert tag_cols["Phone 1"] == "Phone Tags 1"
        assert tag_cols["Phone 3"] == "Phone Tags 3"

    def test_metadata_columns_are_not_treated_as_phones(self):
        fmt = detect_export_format(FLAT_HEADERS)
        phone_cols = fmt.contacts[0].phone_columns
        assert "Phone Tags 1" not in phone_cols
        assert "Phone Type 1" not in phone_cols
        assert "Phone Is Connected 1" not in phone_cols


class TestDetectContactBlockFormat:
    def test_finds_pr_and_relative_blocks(self):
        fmt = detect_export_format(BLOCK_HEADERS)
        assert fmt.kind == "contact_blocks"
        prefixes = [c.prefix for c in fmt.contacts]
        assert prefixes == ["PH", "REL1", "REL2"]

    def test_ph_block_gets_five_phone_slots_from_pr_name(self):
        fmt = detect_export_format(BLOCK_HEADERS)
        ph = next(c for c in fmt.contacts if c.prefix == "PH")
        assert ph.phone_columns == ["PH: Phone1", "PH: Phone2", "PH: Phone3", "PH: Phone4", "PH: Phone5"]
        assert ph.name_columns == ["PR First Name", "PR Last Name"]

    def test_relative_block_gets_its_own_name_and_three_phones(self):
        fmt = detect_export_format(BLOCK_HEADERS)
        rel1 = next(c for c in fmt.contacts if c.prefix == "REL1")
        assert rel1.phone_columns == ["REL1: Phone 1", "REL1: Phone 2", "REL1: Phone 3"]
        assert rel1.name_columns == ["REL1: Full Name"]

    def test_no_pre_existing_tag_columns(self):
        fmt = detect_export_format(BLOCK_HEADERS)
        for contact in fmt.contacts:
            assert contact.tag_columns == {}


class TestDetectFallbackAndErrors:
    def test_single_generic_phone_column(self):
        fmt = detect_export_format(["Name", "Phone", "City"])
        assert fmt.kind == "single"
        assert fmt.contacts[0].phone_columns == ["Phone"]

    def test_unrecognized_layout_raises_instead_of_silently_finding_nothing(self):
        with pytest.raises(ValueError):
            detect_export_format(["Name", "City", "State"])

    def test_explicit_phone_column_override_skips_detection(self):
        fmt = detect_export_format(BLOCK_HEADERS, phone_column="REL3: Phone 1")
        assert fmt.kind == "single"
        assert fmt.contacts[0].phone_columns == ["REL3: Phone 1"]


class TestExtractPhoneEntries:
    def test_flat_format_extracts_populated_phones_only(self):
        fmt = detect_export_format(FLAT_HEADERS)
        rows = [{
            "Phone 1": "8651234567", "Phone Tags 1": "Rel5.1",
            "Phone 2": "", "Phone Tags 2": "",
            "Phone 3": "not-a-phone", "Phone Tags 3": "",
        }]
        entries = extract_phone_entries(rows, fmt)
        assert [e.cleaned for e in entries] == ["8651234567"]
        assert entries[0].tag_column == "Phone Tags 1"

    def test_contact_block_extracts_every_relative_phone(self):
        # This is the exact failure the fix targets: 5 PR phones + 3+3 relative
        # phones across two relatives, all previously invisible to the old
        # bare "Phone N" detector.
        fmt = detect_export_format(BLOCK_HEADERS)
        rows = [{
            "PR First Name": "James", "PR Last Name": "Sampleton",
            "PH: Phone1": "3015550142", "PH: Phone2": "", "PH: Phone3": "",
            "PH: Phone4": "", "PH: Phone5": "",
            "REL1: Full Name": "Sandy Anne Doe",
            "REL1: Phone 1": "5405550161", "REL1: Phone 2": "5405550162", "REL1: Phone 3": "5405550163",
            "REL2: Full Name": "Patricia Shannon Doe",
            "REL2: Phone 1": "8185550164", "REL2: Phone 2": "", "REL2: Phone 3": "",
        }]
        entries = extract_phone_entries(rows, fmt)
        cleaned = sorted(e.cleaned for e in entries)
        assert cleaned == sorted([
            "3015550142", "5405550161", "5405550162", "5405550163", "8185550164",
        ])
        rel1_entries = [e for e in entries if e.contact_prefix == "REL1"]
        assert all(e.contact_name == "Sandy Anne Doe" for e in rel1_entries)
        assert all(e.tag_column is None for e in entries)  # synthesized at write time

    def test_decedent_name_columns_never_produce_phone_entries(self):
        fmt = detect_export_format(BLOCK_HEADERS)
        rows = [{"Deceased First Name": "Ruth", "Deceased Last Name": "Sampleton"}]
        entries = extract_phone_entries(rows, fmt)
        assert entries == []


class TestQualificationEngine:
    TIERS = {
        "Dial First": (81, 100), "Dial Second": (61, 80), "Dial Third": (41, 60),
        "Dial Fourth": (21, 40), "Drop": (0, 20),
    }

    def test_litigator_override_beats_a_high_activity_score(self):
        data = {"is_valid": True, "activity_score": 95, "line_type": "Mobile",
                 "add_ons": {"litigator_checks": {"phone.is_litigator_risk": True}}}
        result = evaluate_phone(data, self.TIERS, litigator_checked=True)
        assert result == {"tag": "Litigator Risk", "code": "LITIGATOR", "keep": False}

    def test_litigator_rule_inactive_when_check_not_requested(self):
        data = {"is_valid": True, "activity_score": 95, "line_type": "Mobile"}
        result = evaluate_phone(data, self.TIERS, litigator_checked=False)
        assert result["tag"] == "Dial First"
        assert result["keep"] is True

    def test_invalid_phone_is_excluded(self):
        data = {"is_valid": False, "activity_score": 90, "line_type": "Mobile"}
        result = evaluate_phone(data, self.TIERS)
        assert result == {"tag": "Invalid", "code": "INVALID", "keep": False}

    def test_tollfree_is_skipped_regardless_of_score(self):
        data = {"is_valid": True, "activity_score": 90, "line_type": "Tollfree"}
        result = evaluate_phone(data, self.TIERS)
        assert result["code"] == "SKIP_LINE_TYPE"
        assert result["tag"] == "Skip - Tollfree"
        assert result["keep"] is False

    def test_nonfixedvoip_is_scored_by_activity_not_skipped(self):
        data = {"is_valid": True, "activity_score": 55, "line_type": "NonFixedVOIP"}
        result = evaluate_phone(data, self.TIERS)
        assert result["tag"] == "Dial Third"
        assert result["keep"] is True

    def test_landline_is_scored_by_activity_not_skipped(self):
        data = {"is_valid": True, "activity_score": 72, "line_type": "Landline"}
        result = evaluate_phone(data, self.TIERS)
        assert result["tag"] == "Dial Second"
        assert result["keep"] is True

    def test_drop_tier_is_tagged_but_not_kept(self):
        data = {"is_valid": True, "activity_score": 8, "line_type": "Mobile"}
        result = evaluate_phone(data, self.TIERS)
        assert result["tag"] == "Drop"
        assert result["keep"] is False


class TestReinsertion:
    def test_flat_format_merges_into_existing_tag_cell(self):
        fmt = detect_export_format(FLAT_HEADERS)
        rows = [{"Phone 1": "8651234567", "Phone Tags 1": "Rel5.1", "Phone 2": "", "Phone 3": ""}]
        entries = extract_phone_entries(rows, fmt)
        results = {"8651234567": {"assigned_tag": "Dial First"}}

        out_path = write_reinserted_export(
            FLAT_HEADERS, rows, fmt, entries, results, "output/_test_reinsert_unit.csv",
        )
        import csv
        with open(out_path, encoding="utf-8") as f:
            out_rows = list(csv.DictReader(f))
        assert out_rows[0]["Phone Tags 1"] == "Rel5.1, Dial First"
        os.remove(out_path)

    def test_reinsertion_is_idempotent(self):
        fmt = detect_export_format(FLAT_HEADERS)
        rows = [{"Phone 1": "8651234567", "Phone Tags 1": "Rel5.1", "Phone 2": "", "Phone 3": ""}]
        entries = extract_phone_entries(rows, fmt)
        results = {"8651234567": {"assigned_tag": "Dial First"}}

        out_path = "output/_test_reinsert_idempotent.csv"
        write_reinserted_export(FLAT_HEADERS, rows, fmt, entries, results, out_path)
        write_reinserted_export(FLAT_HEADERS, rows, fmt, entries, results, out_path)

        import csv
        with open(out_path, encoding="utf-8") as f:
            out_rows = list(csv.DictReader(f))
        assert out_rows[0]["Phone Tags 1"] == "Rel5.1, Dial First"
        os.remove(out_path)

    def test_contact_block_format_inserts_new_tag_column_after_phone_column(self):
        fmt = detect_export_format(BLOCK_HEADERS)
        rows = [{
            "PR First Name": "James", "PR Last Name": "Sampleton",
            "PH: Phone1": "3015550142", "PH: Phone2": "", "PH: Phone3": "",
            "PH: Phone4": "", "PH: Phone5": "",
            "REL1: Full Name": "Sandy Anne Doe",
            "REL1: Phone 1": "5405550161", "REL1: Phone 2": "", "REL1: Phone 3": "",
            "REL2: Full Name": "", "REL2: Phone 1": "", "REL2: Phone 2": "", "REL2: Phone 3": "",
        }]
        entries = extract_phone_entries(rows, fmt)
        results = {"3015550142": {"assigned_tag": "Dial First"}, "5405550161": {"assigned_tag": "Litigator Risk"}}

        out_path = write_reinserted_export(
            BLOCK_HEADERS, rows, fmt, entries, results, "output/_test_reinsert_block.csv",
        )
        import csv
        with open(out_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            out_headers = reader.fieldnames
            out_rows = list(reader)

        assert "PH: Phone1 Tag" in out_headers
        assert out_headers.index("PH: Phone1 Tag") == out_headers.index("PH: Phone1") + 1
        assert out_rows[0]["PH: Phone1 Tag"] == "Dial First"
        assert out_rows[0]["REL1: Phone 1 Tag"] == "Litigator Risk"
        # Untouched phone slots never get a stray tag.
        assert out_rows[0]["REL1: Phone 2 Tag"] == ""
        os.remove(out_path)

    def test_rejected_numbers_are_tagged_not_deleted(self):
        fmt = detect_export_format(FLAT_HEADERS)
        rows = [{"Phone 1": "8651234567", "Phone Tags 1": "", "Phone 2": "", "Phone 3": ""}]
        entries = extract_phone_entries(rows, fmt)
        results = {"8651234567": {"assigned_tag": "Litigator Risk"}}

        out_path = write_reinserted_export(
            FLAT_HEADERS, rows, fmt, entries, results, "output/_test_reinsert_rejected.csv",
        )
        import csv
        with open(out_path, encoding="utf-8") as f:
            out_rows = list(csv.DictReader(f))
        assert out_rows[0]["Phone 1"] == "8651234567"  # never blanked
        assert out_rows[0]["Phone Tags 1"] == "Litigator Risk"
        os.remove(out_path)
