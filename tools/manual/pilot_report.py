"""Before/after report for a v5-method pilot rebuild: live push_plan.json vs rerun_v5/<pilot>/push_plan.json.

Reads the merged sidecars off records.json (title / court / deed) and the two plans, writes
<pilot>/before_after.csv and <pilot>/PILOT_<NAME>_REPORT.md. Nothing pushed, nothing spent.

    python -X utf8 tools/manual/pilot_report.py --run-dir output/dp_drive_sept_missing_20260929 \
        --pilot pilot_dc --keys output/dp_drive_sept_missing_20260929/rerun_v5/pilot_dc_keys.json \
        --title "DC pilot - v5 method on the 53 non-grounded DC records (Sept 646)"
    python -X utf8 tools/manual/pilot_report.py --run-dir ... --pilot pilot_md --keys .../rerun_v5/md_keys.json \
        --title "MD pass - v5 method on the 334 MD records (Sept 646)"
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def line(note: str, prefix: str) -> str:
    for ln in note.splitlines():
        if ln.startswith(prefix):
            return ln
    return ""


def signer_bases(note: str) -> list[str]:
    return re.findall(r"SIGNER \((court|title|obituary|label only)\)", note)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--pilot", required=True, help="sub-folder of rerun_v5 holding the pilot push_plan.json")
    ap.add_argument("--keys", required=True, help="json list of keys (or dict) the pilot rebuilt")
    ap.add_argument("--title", required=True)
    a = ap.parse_args(argv)

    run = Path(a.run_dir)
    if not run.is_absolute():
        run = ROOT / run
    v5 = run / "rerun_v5"
    out = v5 / a.pilot
    live = json.load(open(run / "push_plan.json", encoding="utf-8"))["records"]
    pilot = json.load(open(out / "push_plan.json", encoding="utf-8"))["records"]
    kp = Path(a.keys)
    if not kp.is_absolute():
        kp = ROOT / kp
    kd = json.load(open(kp, encoding="utf-8"))
    keys = [k for k in (kd.keys() if isinstance(kd, dict) else kd) if k in live and k in pilot]
    groups = {r["key"]: r for r in csv.DictReader(open(v5 / "groups.csv", encoding="utf-8"))} if (v5 / "groups.csv").exists() else {}
    records = json.load(open(run / "records.json", encoding="utf-8"))

    rows = []
    c = dict(title=0, title_decedent=0, title_not_decedent=0, title_nomatch=0, coowner=0, household=0, trust=0,
             deed_hit=0, deed_on_parcel=0, deed_flag=0, deed_tod=0, deed_grantor_out=0, deed_2026=0, deed_after_dod=0,
             court=0, court_anchor=0, court_name_search=0, court_name_review=0, court_searched_miss=0, court_will=0,
             label_to_court=0, unverified_before=0, unverified_after=0, verdict_changed=0, still_label_only=0,
             note_len_over=0, tags_changed=0)
    for k in keys:
        a_, b_ = live[k], pilot[k]
        rec = records[k]
        t, co, de = rec.get("title") or {}, rec.get("court") or {}, rec.get("deed") or {}
        g = groups.get(k, {})
        tl, dl = line(b_["note"], "TITLE"), line(b_["note"], "DEED")
        has_title = bool(t.get("owners"))
        c["title"] += has_title
        c["title_decedent"] += t.get("owner_of_record_is_decedent") is True
        c["title_not_decedent"] += has_title and t.get("owner_of_record_is_decedent") is False
        c["title_nomatch"] += bool(t) and not has_title
        c["trust"] += bool(t.get("trust_vesting"))
        coown = ("NOT a party to the probate" in tl) or ("also on title and not the decedent" in tl)
        c["coowner"] += coown
        c["household"] += "Household line at the address" in b_["note"]
        c["deed_hit"] += bool(de.get("instruments"))
        c["deed_on_parcel"] += bool(de.get("n_on_parcel"))
        dfl = de.get("flags") or []
        c["deed_flag"] += any(x in ("transfer on death deed", "estate as party", "foreclosure notice", "substitute trustee", "lis pendens")
                              or x.startswith("owner is GRANTOR") or x.startswith("instrument recorded 2026") for x in dfl)
        c["deed_tod"] += "transfer on death deed" in dfl
        c["deed_grantor_out"] += any(x.startswith("owner is GRANTOR") for x in dfl)
        c["deed_2026"] += any(x.startswith("instrument recorded 2026") for x in dfl)
        c["deed_after_dod"] += "AFTER the DOD" in b_["note"]
        jb = co.get("join_basis") or ""
        has_case = bool(co.get("case_no")) and jb in ("case_number", "address", "name_only")
        c["court"] += has_case
        c["court_anchor"] += bool(co.get("case_no")) and jb == "anchors"
        c["court_name_search"] += jb == "name_search"
        ns = co.get("name_search") or {}
        c["court_name_review"] += (not co.get("case_no")) and bool(ns.get("hits"))
        c["court_searched_miss"] += (not co.get("case_no")) and bool(ns.get("terms")) and not ns.get("hits")
        c["court_will"] += bool(co.get("will_on_file"))
        before_b, after_b = signer_bases(a_["note"]), signer_bases(b_["note"])
        conv = sum(1 for x in after_b if x in ("court", "title")) if has_case or coown else 0
        c["label_to_court"] += conv > 0
        ub, ua = "death UNVERIFIED" in a_["note"], "death UNVERIFIED" in b_["note"]
        c["unverified_before"] += ub
        c["unverified_after"] += ua
        c["verdict_changed"] += a_["verdict"] != b_["verdict"]
        c["still_label_only"] += bool(after_b) and all(x == "label only" for x in after_b) and not has_case and not has_title
        c["note_len_over"] += len(b_["note"]) > 2000
        c["tags_changed"] += sorted(x for x in a_["tags"] if not x.startswith("Deep Prospected")) != sorted(x for x in b_["tags"] if not x.startswith("Deep Prospected"))
        lc = de.get("last_conveyance") or {}
        rows.append({
            "key": k, "street": rec["street"], "county": rec.get("county", ""), "group": g.get("group", ""),
            "unverified_before": ub, "unverified_after": ua,
            "verdict_before": a_["verdict"], "verdict_after": b_["verdict"],
            "title_owners": " + ".join(t.get("owners") or []) if has_title else ("no match" if t else "-"),
            "owner_is_decedent": t.get("owner_of_record_is_decedent"), "sale_date": t.get("sale_date") or "",
            "trust": bool(t.get("trust_vesting")),
            "co_owner_outside_probate": coown, "household_subject_age": (t.get("household_subject") or {}).get("age") or "",
            "deed_instruments": len(de.get("instruments") or []), "deed_on_parcel": de.get("n_on_parcel"),
            "deed_last": f"{lc.get('recorded_date', '')} {lc.get('doc_type', '')} {(lc.get('grantor') or '')[:18]} -> {(lc.get('grantee') or '')[:18]}".strip() if lc else "",
            "deed_flags": "; ".join(x for x in dfl if x not in ("no index hit", "deed", "trust", "release", "mortgage")),
            "case_no": co.get("case_no") or "", "join": jb, "case_dod": co.get("dod") or "",
            "name_search": ns.get("basis", ""), "pr": "; ".join(p.get("name", "") for p in co.get("pr") or []),
            "heirs": len(co.get("heirs") or []), "will": co.get("will_on_file"),
            "signer_bases_before": ",".join(before_b), "signer_bases_after": ",".join(after_b),
            "phones": len(b_["phones"]), "note_len_before": len(a_["note"]), "note_len_after": len(b_["note"]),
            "tags_after": ", ".join(b_["tags"]),
        })

    with open(out / "before_after.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    name = a.pilot.replace("pilot_", "").upper()
    md = [f"# {a.title}", "",
          f"Live plan `push_plan.json` vs `rerun_v5/{a.pilot}/push_plan.json`, {date.today().isoformat()}. Nothing pushed.", "",
          "| Measure | Count |", "|---|---|",
          f"| Records | {len(keys)} |",
          f"| TITLE line with owner(s) of record (tax roll) | {c['title']} |",
          f"| ... owner of record IS the decedent | {c['title_decedent']} |",
          f"| ... owner of record is NOT the decedent (transfer / wrong record) | {c['title_not_decedent']} |",
          f"| ... no tax-roll match for the address | {c['title_nomatch']} |",
          f"| ... title held in trust (trustee signs) | {c['trust']} |",
          f"| Co-owner on title who is not the decedent (decides the deal) | {c['coowner']} |",
          f"| Household subject at the address (no first name) listed | {c['household']} |",
          f"| DEED: at least one deed-index instrument | {c['deed_hit']} |",
          f"| ... with an instrument confirmed ON this parcel | {c['deed_on_parcel']} |",
          f"| ... a red-flag instrument (TOD deed, estate as party, foreclosure, owner is grantor, 2026 paper) | {c['deed_flag']} |",
          f"| ... transfer-on-death deed recorded by the owner | {c['deed_tod']} |",
          f"| ... owner is GRANTOR on the last conveyance (may no longer hold title) | {c['deed_grantor_out']} |",
          f"| ... instrument recorded in 2026 | {c['deed_2026']} |",
          f"| ... conveyance recorded AFTER the DOD | {c['deed_after_dod']} |",
          f"| COURT PARTIES (DC case joined, party list) | {c['court']} |",
          f"| ... will on file (Legatees) | {c['court_will']} |",
          f"| CASE from the vendor anchors (estate number, PR from the notice) | {c['court_anchor']} |",
          f"| CASE found by decedent-NAME search (Register of Wills) | {c['court_name_search']} |",
          f"| ... name-search hits left for REVIEW (not joined) | {c['court_name_review']} |",
          f"| ... name-searched, no estate on file | {c['court_searched_miss']} |",
          f"| Records where a signer's basis moved from label to court/title | {c['label_to_court']} |",
          f"| 'death UNVERIFIED' before -> after | {c['unverified_before']} -> {c['unverified_after']} |",
          f"| Verdict changed | {c['verdict_changed']} |",
          f"| Still label-only with no title, no case | {c['still_label_only']} |",
          f"| Notes over 2,000 chars (pusher cap is 4,000) | {c['note_len_over']} |",
          f"| Tag set changed (ignoring the DP month) | {c['tags_changed']} |",
          "", "## Per record", "",
          "| Street | County | Group | Title owners | Owner = decedent | Co-owner? | Deed last | Case | Signer bases before -> after | Unverified before -> after | Phones |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        case = r["case_no"] + (f" ({r['join']})" if r["join"] else "") if r["case_no"] else (f"REVIEW: {r['name_search']}" if r["name_search"] and "no_candidate" not in r["name_search"] else ("searched, none" if r["name_search"] else ""))
        md.append(f"| {r['street']} | {r['county']} | {r['group']} | {r['title_owners']} | {r['owner_is_decedent']} | {'YES' if r['co_owner_outside_probate'] else ''} | "
                  f"{r['deed_last']} | {case} | {r['signer_bases_before'] or '-'} -> {r['signer_bases_after'] or '-'} | "
                  f"{r['unverified_before']} -> {r['unverified_after']} | {r['phones']} |")
    md += ["", "## Example notes (before / after)", ""]
    show = [k for k in keys if (records[k].get("court") or {}).get("join_basis") == "name_search"][:2] + \
           [k for k in keys if (records[k].get("court") or {}).get("case_no") and (records[k].get("court") or {}).get("join_basis") != "name_search"][:1] + \
           [k for k in keys if "also on title and not the decedent" in pilot[k]["note"] or "NOT a party" in pilot[k]["note"]][:2] + \
           [k for k in keys if ((records[k].get("title") or {}).get("owner_of_record_is_decedent") is False)][:1]
    for k in dict.fromkeys(show):
        md += [f"### {records[k]['street']} ({k})", "", "**Before**", "```", live[k]["note"], "```", "", "**After**", "```", pilot[k]["note"], "```", ""]
    report = out / f"PILOT_{name}_REPORT.md"
    report.write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(c, indent=1))
    print("wrote", report, "and before_after.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
