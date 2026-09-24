"""Offline checks for src/scripts/permit_portal_pull.py -- no network, no account.

Guards the parts that fail silently: applicant-name cleaning (the DC feed's
"NA NA; X" / "NULL NULL; X" junk), the government exclusion (word boundaries,
not substrings), date parsing (epoch ms), the 2025/2026 layer dedupe, the
bucket maps, the stalled rule, the expediter flag, the registry join, the
no-function Socrata $where, the resumable pager, and the vendor-seed column
contract against build_directory.py.

    python tools/manual/test_permit_portal_pull.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import permit_portal_pull as pp  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {label}: {'ok' if ok else 'FAIL'}{(' -- ' + detail) if detail and not ok else ''}")
    if not ok:
        failures.append(label)


def main() -> int:
    print("1-6. clean_applicant")
    c = pp.clean_applicant("NA NA; BRYCE CARRINGTON")
    check("NA NA prefix stripped", c["name"] == "BRYCE CARRINGTON" and c["alt"] == "" and c["org"] == "", str(c))
    c = pp.clean_applicant("NULL NULL; KARLEN MURRAY")
    check("NULL NULL prefix stripped", c["name"] == "KARLEN MURRAY", str(c))
    c = pp.clean_applicant("MARGO RICKS; MARGO RICKS")
    check("duplicate parts collapse", c["name"] == "MARGO RICKS" and c["alt"] == "", str(c))
    c = pp.clean_applicant("JOSEPH G. JARVIS (RAMCO)")
    check("parenthetical org split", c["name"] == "JOSEPH G JARVIS" and c["org"] == "RAMCO", str(c))
    blanks = [pp.clean_applicant(x) for x in ("NA NA", "", None, "NULL; NA")]
    check("junk-only inputs blank", all(b["name"] == "" and b["key"] == "" for b in blanks), str(blanks))
    c = pp.clean_applicant("DFM PERMITS")
    ent = pp.clean_applicant("ABC HOMES LLC")
    check("entity key keeps order, expediter regex hits",
          bool(pp.EXPEDITER_RX.search(c["name"])) and ent["is_entity"] and ent["key"] == "ABC HOMES LLC",
          f"{c} {ent}")
    c = pp.clean_applicant("; JOHN D LEDERER; JOHN D LEDERER")
    check("leading empty part + dup", c["name"] == "JOHN D LEDERER" and c["alt"] == "", str(c))

    print("7. name_key symmetry")
    check("LAST, FIRST == FIRST LAST", pp.name_key("RICKS, MARGO") == pp.name_key("MARGO RICKS")
          and pp.clean_applicant("RICKS, MARGO")["name"] == "MARGO RICKS")

    print("8. is_gov_or_institution")
    for nm in ("DISTRICT OF COLUMBIA", "DC HOUSING AUTHORITY", "USA", "HOWARD UNIVERSITY",
               "UNITED STATES OF AMERICA", "ST JOHNS CHURCH"):
        check(f"gov true: {nm}", pp.is_gov_or_institution(nm))
    for nm in ("MCMILLAN ASSOCIATES LLC", "SKYLAND PHASE C-2, LLC", "JOHN SMITH",
               "WILLBANKS PROPERTIES LLC", "USADA HOMES LLC", "TEMPLETON CAPITAL LLC"):
        check(f"gov false: {nm}", not pp.is_gov_or_institution(nm))

    print("9-10. dates")
    check("epoch ms -> iso", pp.epoch_ms_to_iso(1758672000000) == "2025-09-24", pp.epoch_ms_to_iso(1758672000000))
    check("None -> ''", pp.epoch_ms_to_iso(None) == "")
    check("iso passthrough", pp.epoch_ms_to_iso("2026-01-05T00:00:00") == "2026-01-05")
    check("socrata ts", pp.soc_ts_to_iso("2026-03-01T00:00:00.000") == "2026-03-01")
    s, e = pp.window(12, datetime(2026, 9, 24))
    check("12-month window", s == "2025-09-24" and e == "2026-09-24", f"{s}..{e}")

    print("11. dedupe_dc")
    rows = [{"source": "dc_permits_2025", "permit_id": "B1", "issued_date": "2025-10-01"},
            {"source": "dc_permits_2026", "permit_id": "B1", "issued_date": "2025-10-01"},
            {"source": "dc_permits_2026", "permit_id": "B2", "issued_date": "2026-01-01"},
            {"source": "dc_permits_2026", "permit_id": "B2", "issued_date": "2026-02-01"}]
    out, overlap = pp.dedupe_dc(rows)
    by = {r["permit_id"]: r for r in out}
    check("2026 layer wins, later date wins", len(out) == 2 and overlap == 2
          and by["B1"]["source"] == "dc_permits_2026" and by["B2"]["issued_date"] == "2026-02-01")

    print("12. bucket_subtype")
    cases = [("dc", {"permit_subtype": "RAZE", "source": "dc_permits_2026"}, "demolition"),
             ("dc", {"permit_subtype": "ALTERATION AND REPAIR", "source": "x"}, "alteration"),
             ("dc", {"permit_subtype": "SOLAR SYSTEM", "source": "x"}, "solar"),
             ("dc", {"permit_subtype": "ELECTRICAL", "source": "x"}, "other"),
             ("montgomery", {"permit_subtype": "ALTER", "source": "moco_demolition"}, "demolition"),
             ("montgomery", {"permit_subtype": "RESTORE AND / OR REPAIR", "source": "moco_commercial"}, "alteration"),
             ("montgomery", {"permit_subtype": "NEW CONSTRUCTION", "source": "moco_residential"}, "new_building"),
             ("fairfax", {"permit_subtype": "Residential New", "source": "fairfax"}, "new_building"),
             ("fairfax", {"permit_subtype": "Commercial Addition/Alteration", "source": "fairfax"}, "addition"),
             ("fairfax", {"permit_subtype": "Something Else", "source": "fairfax"}, "other")]
    for j, row, want in cases:
        got = pp.bucket_subtype(j, row)
        check(f"bucket {j} {row['permit_subtype']} -> {want}", got == want, got)

    print("13. stalled")
    today = datetime(2026, 9, 24)
    base = {"jurisdiction": "Montgomery", "address": "1 MAIN ST", "city": "", "zip": "20902",
            "owner_name": "", "applicant_name": "", "bucket": "alteration", "description": "",
            "valuation": "", "link_url": "", "issued_date": ""}
    rows = [{**base, "permit_id": "A", "status": "Open", "applied_date": "2026-01-10"},
            {**base, "permit_id": "B", "status": "EXPIRED", "applied_date": "2025-11-01", "issued_date": "2025-12-01"},
            {**base, "permit_id": "C", "status": "Open", "applied_date": "2026-08-01"},
            {**base, "permit_id": "D", "status": "Issued", "applied_date": "2026-01-01", "issued_date": "2026-02-01"},
            {**base, "permit_id": "E", "status": "Open", "applied_date": "2026-01-10", "address": "2 OAK ST"}]
    st = pp.stalled(rows, 6, today)
    cls = {r["permit_id"]: r["stall_class"] for r in st}
    check("stalled / expired / fresh / issued", cls == {"A": "stalled", "B": "expired", "E": "stalled"}, str(cls))
    n_at = {r["permit_id"]: r["n_at_address"] for r in st}
    check("n_at_address counts duplicates (A shares 1 MAIN ST with B, E alone)",
          n_at["A"] == 2 and n_at["E"] == 1 and all(r["age_days"] for r in st), str(n_at))
    rows2 = [{**base, "permit_id": "A", "status": "Open", "applied_date": "2026-01-10"},
             {**base, "permit_id": "F", "status": "Open", "applied_date": "2026-02-10"}]
    check("n_at_address = 2 for a shared address",
          all(r["n_at_address"] == 2 for r in pp.stalled(rows2, 6, today)))

    print("14. repeat_applicants")
    def dc(pid, applicant, owner, sub, zipc="20001", addr=None):
        return {"permit_id": pid, "applicant_raw": applicant, "applicant_key": pp.clean_applicant(applicant)["key"],
                "applicant_name": pp.clean_applicant(applicant)["name"], "applicant_org": "",
                "applicant_is_entity": False, "owner_name": owner, "owner_key": pp.norm_name(owner),
                "permit_subtype": sub, "permit_type": "CONSTRUCTION", "zip": zipc,
                "address": addr or f"{pid} X ST", "issued_date": "2026-03-01", "source": "dc_permits_2026",
                "bucket": pp.bucket_subtype("dc", {"permit_subtype": sub, "source": "dc_permits_2026"}),
                "owner_is_entity": bool(pp.ENTITY_RX.search(pp.norm_name(owner))),
                "owner_excluded": pp.is_gov_or_institution(owner), "fees": 10}
    rows = []
    subs = ["ALTERATION AND REPAIR", "NEW BUILDING", "DEMOLITION"]
    for i in range(16):
        rows.append(dc(f"E{i}", "NA NA; BIG EXPEDITER", f"OWNER {i % 6} LLC", subs[i % 3]))
    for i in range(3):
        rows.append(dc(f"S{i}", "JOHN SMITH", "SMITH, JOHN", "ALTERATION AND REPAIR"))
    ra = pp.repeat_applicants(rows, 2)
    byk = {r["applicant_name"]: r for r in ra}
    e, s = byk.get("BIG EXPEDITER"), byk.get("JOHN SMITH")
    check("expediter counted + flagged likely", e and e["n_permits"] == 16 and e["n_distinct_owners"] == 6
          and e["expediter_flag"] == "likely" and "ALTERATION AND REPAIR:6" in e["subtype_mix"], str(e))
    check("self-owner applicant not flagged", s and s["expediter_flag"] == "" and s["self_owner_share"] == 1.0
          and s["first_date"] == "2026-03-01", str(s))

    print("15. repeat_owners + registry join")
    rows = [dc("G1", "", "DISTRICT OF COLUMBIA", "ALTERATION AND REPAIR"),
            dc("G2", "", "DISTRICT OF COLUMBIA", "ALTERATION AND REPAIR"),
            dc("L1", "", "MCMILLAN ASSOCIATES LLC", "NEW BUILDING"),
            dc("L2", "", "MCMILLAN ASSOCIATES LLC", "DEMOLITION"),
            dc("A1", "", "SKYLAND PHASE C-2, LLC", "ALTERATION AND REPAIR"),
            dc("A2", "", "SKYLAND PHASE C-2, LLC", "ALTERATION AND REPAIR"),
            dc("F1", "", "CAPITOL CROSSING II LLC", "ALTERATION AND REPAIR"),
            dc("F2", "", "CAPITOL CROSSING II LLC", "ALTERATION AND REPAIR"),
            dc("N1", "", "NOBODY KNOWN LLC", "ALTERATION AND REPAIR"),
            dc("N2", "", "NOBODY KNOWN LLC", "SOLAR SYSTEM"),
            dc("N3", "", "NOBODY KNOWN LLC", "ALTERATION AND REPAIR"),
            dc("W1", "", "WILLBANKS PROPERTIES LLC", "ALTERATION AND REPAIR"),
            dc("W2", "", "WILLBANKS PROPERTIES LLC", "ALTERATION AND REPAIR")]
    reg = [{"buyer_key": "MCMILLAN ASSOCIATES LLC", "name": "McMillan Associates LLC", "aliases": [],
            "n_buys": 3, "tier": "1", "counties": ["District of Columbia"]},
           {"buyer_key": "SKYLAND DEV LLC", "name": "Skyland Dev LLC", "aliases": ["SKYLAND PHASE C 2 LLC"],
            "n_buys": 1, "tier": "1", "counties": []},
           {"buyer_key": "CAPITOL CROSSING LLC", "name": "Capitol Crossing LLC", "aliases": [],
            "n_buys": 2, "tier": "1", "counties": []}]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "registry.json"
        p.write_text(json.dumps(reg), encoding="utf-8")
        idx = pp.load_registry(p)
    ro = pp.repeat_owners(rows, 2, idx)
    byo = {r["owner_name"]: r for r in ro}
    check("government owner removed", "DISTRICT OF COLUMBIA" not in byo, str(list(byo)))
    check("LLC kept with entity flag", byo["MCMILLAN ASSOCIATES LLC"]["is_entity"] is True)
    check("registry exact hit", byo["MCMILLAN ASSOCIATES LLC"]["registry_hit"] == "exact"
          and byo["MCMILLAN ASSOCIATES LLC"]["registry_n_buys"] == 3)
    check("registry alias hit", byo["SKYLAND PHASE C-2, LLC"]["registry_hit"] == "exact", str(byo.get("SKYLAND PHASE C-2, LLC")))
    check("registry fuzzy hit", byo["CAPITOL CROSSING II LLC"]["registry_hit"] == "fuzzy")
    check("registry none", byo["NOBODY KNOWN LLC"]["registry_hit"] == "none"
          and byo["NOBODY KNOWN LLC"]["n_permits"] == 2, str(byo["NOBODY KNOWN LLC"]))
    check("WILLBANKS not excluded as a bank", byo["WILLBANKS PROPERTIES LLC"]["tier"] != "EXCLUDE",
          str(byo["WILLBANKS PROPERTIES LLC"]["tier_reason"]))

    print("16. where clauses")
    w = pp.socrata_where("2025-09-24", "2026-09-24")
    check("socrata between, no functions", "between" in w and "upper(" not in w and "(" not in w.replace("(addeddate", "").replace("(issueddate", ""))
    try:
        pp.socrata_where("2025-09-24", "2026-09-24", added="upper(addeddate)")
        check("function syntax refused", False)
    except AssertionError:
        check("function syntax refused", True)
    check("layer-17 keeps never-issued", "OR ISSUE_DATE IS NULL" in pp.dc_2025_where("2025-09-24", "date")
          and "DATE '2025-09-24'" in pp.dc_2025_where("2025-09-24", "date"))
    check("epoch style", pp.arcgis_date_where("ISSUE_DATE", "2025-09-24", "epoch") == "ISSUE_DATE >= 1758672000000",
          pp.arcgis_date_where("ISSUE_DATE", "2025-09-24", "epoch"))

    print("17. fetch_pages resume / short page / drift")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        asked = []
        def page_fn(off, n):
            asked.append(off)
            rows = [{"OBJECTID": off + i} for i in range(n if off < 6000 else 500)]
            return rows, off < 6000
        state = {"window": ["2025-09-24", "2026-09-24"],
                 "sources": {"x": {"ok": False, "n": 4000, "pages": 2, "next_offset": 4000,
                                   "where": "", "count_claimed": 6500, "error": None}}}
        n = pp.fetch_pages("x", out, state, page_fn, 2000, 0, 0.0, 6500, "1=1")
        st = state["sources"]["x"]
        check("resumed at 4000, stopped on short page", asked == [4000, 6000] and n == 6500 and st["ok"],
              f"{asked} {n} {st}")
        check("jsonl appended, state saved", len(pp._read_jsonl(out / "raw" / "x.jsonl")) == 2500
              and (out / "raw" / "state.json").exists())
        state2 = {"window": [], "sources": {}}
        pp.fetch_pages("y", out, state2, lambda off, n: ([{"a": 1}] * 10, False), 2000, 0, 0.0, 5000, "")
        check("count drift flips ok", state2["sources"]["y"]["ok"] is False
              and "drift" in (state2["sources"]["y"]["error"] or "") or "vs claimed" in (state2["sources"]["y"]["error"] or ""),
              str(state2["sources"]["y"]))
        state3 = {"window": [], "sources": {}}
        pp.fetch_pages("z", out, state3, lambda off, n: ([{"a": 1}] * 3, False), 2000, 0, 0.0, 3, "")
        check("exact count ok", state3["sources"]["z"]["ok"] is True)
        state4 = {"window": [], "sources": {}}
        pp.fetch_pages("w", out, state4, lambda off, n: ([{"a": 1}] * n, True), 5, 2, 0.0, None, "")
        check("page-limit stops and reports", state4["sources"]["w"]["pages"] == 2
              and state4["sources"]["w"]["ok"] is False and "page-limit" in state4["sources"]["w"]["error"])

    print("18. write_csv + vendor seed contract")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.csv"
        pp.write_csv(p, [{"b": 2, "a": 1, "c": 3}], ["a", "b"])
        raw = p.read_bytes()
        check("utf-8-sig BOM + header order", raw.startswith(b"\xef\xbb\xbf") and raw[3:].startswith(b"a,b"))
    try:
        sys.path.insert(0, str(ROOT / "skills" / "vendor-directory-builder" / "scripts"))
        import build_directory  # noqa: E402
        fields = list(build_directory.FIELDS)
    except Exception as e:  # noqa: BLE001 - openpyxl may be absent
        print(f"  (build_directory import failed: {e}; comparing against the hard-coded copy)")
        fields = ["trade", "company", "contact", "phone", "phone_status", "email", "website",
                  "service_area", "serves_geo", "rating", "license", "bbb", "why", "source",
                  "cautions", "confidence", "top_pick"]
    check("VENDOR_FIELDS == build_directory.FIELDS", pp.VENDOR_FIELDS == fields, str(pp.VENDOR_FIELDS))
    roster = [{"license_key": "1", "ENTITYNAME": "ACME BUILDERS LLC", "ENTITYTRADENAME": "",
               "owner_first": "A", "owner_last": "B", "LICENSETYPE": "General Contractor",
               "BUSINESSACTIVITY": "GC", "LICENSESTATUS": "ACTIVE", "LICENSEENDDATE": "2027-01-01",
               "PREMISEADDRESS": "", "BILLINGADDRESS": "", "n_permits_as_applicant": 3,
               "permit_zips": "20001:2|20002:1", "last_permit_date": "2026-05-01", "expediter_flag": ""},
              {"license_key": "2", "ENTITYNAME": "ONE OFF LLC", "ENTITYTRADENAME": "", "owner_first": "",
               "owner_last": "", "LICENSETYPE": "", "BUSINESSACTIVITY": "", "LICENSESTATUS": "ACTIVE",
               "LICENSEENDDATE": "", "PREMISEADDRESS": "", "BILLINGADDRESS": "",
               "n_permits_as_applicant": 1, "permit_zips": "", "last_permit_date": "", "expediter_flag": ""}]
    seed = pp.vendor_seed(roster)
    check("seed keeps >=2 permits only, fills company/contact/license/service_area",
          len(seed) == 1 and seed[0]["company"] == "ACME BUILDERS LLC" and seed[0]["contact"] == "A B"
          and seed[0]["service_area"] == "20001, 20002" and seed[0]["phone"] == ""
          and list(seed[0].keys()) == pp.VENDOR_FIELDS, str(seed))

    print("19. BBL index lookup")
    bbl = [{"ENTITYNAME": "RAMCO CONSTRUCTION LLC", "ENTITYTRADENAME": "RAMCO", "BUSINESSOWNERFIRSTNAME": "JOSEPH",
            "BUSINESSOWNERLASTNAME": "JARVIS", "LICENSETYPE": "GC", "LICENSESTATUS": "ACTIVE"},
           {"ENTITYNAME": "CAPITOL IMPROVEMENTS INC", "ENTITYTRADENAME": "", "BUSINESSOWNERFIRSTNAME": "",
            "BUSINESSOWNERLASTNAME": "", "LICENSETYPE": "HIC", "LICENSESTATUS": "ACTIVE"}]
    idx = pp.build_bbl_index(bbl)
    check("owner-name exact hit", (pp.bbl_lookup("JARVIS, JOSEPH", idx) or {}).get("how") == "exact")
    check("entity fuzzy hit", (pp.bbl_lookup("CAPITOL IMPROVEMENTS INC OF DC", idx) or {}).get("how") == "fuzzy")
    check("person no hit", pp.bbl_lookup("MARGO RICKS", idx) is None)

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILURES:"))
    for f in failures:
        print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
