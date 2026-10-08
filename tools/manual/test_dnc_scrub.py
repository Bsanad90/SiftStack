"""Offline checks for the DNC scrub: dnc_client rules, the scorer override, dnc_scrub.py
and dnc_status_push's phone plan. No network, no account, no spend.

    python tools/manual/test_dnc_scrub.py
"""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import dnc_client as dc  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {name}")


def ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="dnc_test_"))
    cache_path = tmp / "dnc_cache.json"
    dc.reset_cache_for_tests({}, cache_path)
    c = dc.load_cache(cache_path)

    # clean_number
    check("clean 11-digit", dc.clean_number("+1 (301) 555-1234") == "3015551234")
    check("clean short -> ''", dc.clean_number("555-1234") == "")

    # status rules
    check("unknown number unchecked", dc.status("3015550000", c) == "unchecked")
    dc.record("3015550001", dc.VENDOR, False, c, checked_at=ago(5))
    check("fresh vendor clean", dc.status("3015550001", c) == "clean")
    dc.record("3015550002", dc.VENDOR, False, c, checked_at=ago(32))
    check("31-day rule: 32 days old is stale", dc.status("3015550002", c) == "stale")
    dc.record("3015550003", dc.VENDOR, True, c, checked_at=ago(400), federal=True)
    check("a hit never expires", dc.status("3015550003", c) == "dnc")
    dc.record("3015550003", dc.VENDOR, False, c)
    check("a later clean does not clear a hit", dc.status("3015550003", c) == "dnc")

    # secondary sources only add
    dc.record("3015550004", "tracerfy", False, c)
    check("tracerfy clean is not stored", "3015550004" not in c)
    check("tracerfy clean does not count as clean", dc.status("3015550004", c) == "unchecked")
    dc.record("3015550005", dc.VENDOR, False, c, checked_at=ago(1))
    dc.record("3015550005", "tracerfy", True, c)
    check("tracerfy hit beats a fresh vendor clean", dc.status("3015550005", c) == "dnc")
    check("dnc_sources names tracerfy", dc.dnc_sources("3015550005", c) == ["tracerfy"])
    dc.record("3015550006", "datasift", True, c, reason="CORRECT_DNC")
    check("datasift DNC status is a hit", dc.is_dnc("3015550006", c))
    try:
        dc.record("3015550007", "bogus", True, c)
        check("unknown source raises", False)
    except ValueError:
        check("unknown source raises", True)

    # override_tag
    check("override: hit -> DNC", dc.override_tag("3015550006", "Dial First", c) == "DNC")
    check("override: litigator keeps its tag", dc.override_tag("3015550006", "Litigator Risk", c) == "Litigator Risk")
    check("override: clean keeps tier", dc.override_tag("3015550001", "Dial First", c) == "Dial First")

    # vendor not configured / partial answer
    saved_adapter, saved_key = dc._ADAPTER, os.environ.pop("DNC_API_KEY", None)
    try:
        dc.scrub_remote(["3015550001"])
        check("unconfigured vendor raises", False)
    except dc.DncVendorNotConfigured:
        check("unconfigured vendor raises", True)
    os.environ["DNC_API_KEY"] = "test"
    dc._ADAPTER = lambda nums, key: {nums[0]: {"dnc": False}}
    try:
        dc.scrub_remote(["3015550001", "3015550002"])
        check("partial vendor answer raises (not clean)", False)
    except dc.DncVendorError:
        check("partial vendor answer raises (not clean)", True)
    dc._ADAPTER = saved_adapter
    os.environ.pop("DNC_API_KEY", None)
    if saved_key is not None:
        os.environ["DNC_API_KEY"] = saved_key

    # evaluate_phone override (uses the module cache)
    import phone_validator as pv
    data = {"is_valid": True, "line_type": "Mobile", "activity_score": 95}
    tiers = pv.DEFAULT_TIERS
    check("evaluate_phone: no number -> tier", pv.evaluate_phone(data, tiers)["keep"] is True)
    d = pv.evaluate_phone(data, tiers, number="3015550006")
    check("evaluate_phone: DNC drops", d == {"tag": "DNC", "code": "DNC", "keep": False})
    lit = {**data, "add_ons": {"litigator_checks": {"phone.is_litigator_risk": True}}}
    check("evaluate_phone: litigator before DNC",
          pv.evaluate_phone(lit, tiers, litigator_checked=True, number="3015550006")["tag"] == "Litigator Risk")

    # lane scorer wiring
    import dpd_lane_phone_score as ls
    check("DNC is a score tag", "DNC" in ls.SCORE_TAGS)
    check("DNC ranks below callable", ls.rank_of("DNC") > ls.CALLABLE_MAX_RANK)
    check("score_tag_of reads DNC", ls.score_tag_of(["Rel1.1", "DNC"]) == "DNC")
    plan = {"3015550006": {"class": "run_cache", "tag": "Dial First"}}
    check("resolve_number_tag applies DNC", ls.resolve_number_tag("3015550006", plan, {}, {}) == "DNC")
    plan = {"3015550001": {"class": "run_cache", "tag": "Dial First"}}
    check("resolve_number_tag leaves clean", ls.resolve_number_tag("3015550001", plan, {}, {}) == "Dial First")
    dc.save_cache(c, cache_path)

    # dnc_scrub.py end to end, cache-only
    import dnc_scrub
    sheet = tmp / "dial_sheet.csv"
    with open(sheet, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Street", "Contact", "Phone", "Tag", "DataSift Status", "Record UUID"])
        w.writerow(["1 A St", "Ann", "3015550001", "Dial First", "UNKNOWN", "u1"])
        w.writerow(["1 A St", "Ann", "3015550099", "Dial Second", "CORRECT_DNC", "u1"])
        w.writerow(["2 B St", "Bob", "3015550098", "Dial First", "UNKNOWN", "u2"])
    harvest = tmp / "tracerfy.json"
    harvest.write_text(json.dumps({"results": [{"phones": [{"number": "301-555-0098", "dnc": True}]}]}))
    dc.reset_cache_for_tests(None, None)
    rc = dnc_scrub.main(["--input", str(sheet), "--harvest", str(harvest), "--cache", str(cache_path)])
    rows = list(csv.DictReader(open(tmp / "dnc_review.csv", encoding="utf-8")))
    by = {r["number"]: r for r in rows}
    check("scrub exit 0 when all known", rc == 0)
    check("review has a row per sheet row", len(rows) == 3)
    check("review columns", list(rows[0].keys()) == dnc_scrub.REVIEW_FIELDS)
    check("datasift status harvested", by["3015550099"]["dnc_status"] == "dnc"
          and by["3015550099"]["dnc_sources"] == "datasift")
    check("tracerfy json harvested", by["3015550098"]["dnc_sources"] == "tracerfy")
    check("clean row dial_ok", by["3015550001"]["dial_ok"] == "True")
    check("dnc row not dial_ok", by["3015550099"]["dial_ok"] == "False")

    with open(sheet, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["3 C St", "Cy", "3015550097", "Dial First", "UNKNOWN", "u3"])
    dc.reset_cache_for_tests(None, None)
    check("scrub exit 2 with unchecked", dnc_scrub.main(["--input", str(sheet), "--cache", str(cache_path)]) == 2)
    dc.reset_cache_for_tests(None, None)
    check("spend without vendor fails, exit 1",
          dnc_scrub.main(["--input", str(sheet), "--cache", str(cache_path), "--spend"]) == 1)
    empty = tmp / "empty.csv"
    empty.write_text("Phone\n", encoding="utf-8")
    check("zero numbers is a failure", dnc_scrub.main(["--input", str(empty), "--cache", str(cache_path)]) == 1)

    # Smarter Contact blocked export
    sc = tmp / "campaign_blocked.csv"
    with open(sc, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Phone Number", "Reason"])
        w.writerow(["(301) 555-0097", "Do Not Call"])
        w.writerow(["3015550096", "Carrier Blocked"])
        w.writerow(["3015550095", "Opted Out"])
        w.writerow(["3015550094", "Undeliverable"])
    hits, skipped = dnc_scrub.read_sc_blocked(sc)
    check("sc: DNC + opt-out are hits", sorted(n for n, _ in hits) == ["3015550095", "3015550097"])
    check("sc: carrier/undeliverable skipped", skipped == {"Carrier Blocked": 1, "Undeliverable": 1})
    dc.reset_cache_for_tests(None, None)
    dnc_scrub.main(["--input", str(sheet), "--cache", str(cache_path), "--sc-blocked", str(sc)])
    rows = {r["number"]: r for r in csv.DictReader(open(tmp / "dnc_review.csv", encoding="utf-8"))}
    check("sc hit lands in review", rows["3015550097"]["dnc_sources"] == "smartercontact")
    dc.reset_cache_for_tests(None, None)
    check("sc source cannot add clean", (dc.record("3015550093", "smartercontact", False, dc.load_cache(cache_path)),
                                         dc.status("3015550093"))[1] == "unchecked")

    # dnc_status_push phone plan
    import dnc_status_push as dp
    check("status map CORRECT", dp.dnc_status_for("CORRECT") == "CORRECT_DNC")
    check("status map WRONG", dp.dnc_status_for("wrong") == "WRONG_DNC")
    check("status map other", dp.dnc_status_for("NO_ANSWER") == "DNC")
    live = [{"number": "(301) 555-0001", "type": "MOBILE", "tags": [{"name": "Dial First"}],
             "status": "CORRECT", "is_connected": True},
            {"number": "3015550002", "type": "LANDLINE", "tags": ["Rel1.1"], "status": "DEAD"},
            {"number": "3015550003", "type": "MOBILE", "tags": [], "status": "CORRECT_DNC"}]
    payload, changed = dp.plan_phones(live, {"3015550001", "3015550003"})
    check("plan sends the FULL list", len(payload) == 3)
    check("plan changes only new DNC", changed == ["3015550001"])
    check("plan keeps tags", payload[0]["tags"] == ["Dial First"] and payload[1]["tags"] == ["Rel1.1"])
    check("plan keeps other status", payload[1]["status"] == "DEAD")
    check("plan sets CORRECT_DNC", payload[0]["status"] == "CORRECT_DNC")

    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
