"""Offline guard for lane_incomplete_cleanup's name derivation helpers and the relative
obituary-window resolution in to_query. No account, no browser. Run after ANY change to
recorder_pair / secondary_split / row_pr_line / preset_spec.resolve_relative_date / to_query.

    python tools/manual/test_lane_cleanup_derive.py
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from dpd.preset_spec import OBIT_WINDOW_VALUE, is_relative_date, resolve_relative_date  # noqa: E402
from dpd_lane_assignee_report import to_query  # noqa: E402
from lane_incomplete_cleanup import recorder_pair, row_pr_line, secondary_split  # noqa: E402

n = 0


def check(cond, label):
    global n
    n += 1
    if not cond:
        raise SystemExit(f"FAIL {n}: {label}")


# recorder_pair: two co-trustees sharing the first token fix it as the surname
check(recorder_pair("Lotfi Ali Tr & Lotfi Sedigheh") == ("Ali", "Lotfi"), "lotfi")
check(recorder_pair("Mitchell Jimmy Ray Tr & Mitche") == ("Jimmy", "Mitchell"), "truncated second surname")
check(recorder_pair("Appelstein Mark J Tr & Appelst") == ("Mark", "Appelstein"), "appelstein")
check(recorder_pair("Smith John Tr & Jones Mary") is None, "different surnames")
check(recorder_pair("") is None, "empty")

# secondary_split: SiftMap's mangled secondary-owner rows
so = lambda *pairs: {"secondary_owners": [{"first_name": f, "last_name": l} for f, l in pairs]}  # noqa: E731
check(secondary_split(so((None, "Renz"), ("S", "Marisa")), "Renz") == (True, ["Marisa"]), "renz")
check(secondary_split(so((None, "Han"), ("Sook", "Han"), ("Lisa", "Han")), "Han") == (True, []), "real people skipped")
check(secondary_split(so(("Robert", "Wilson")), "Bourelly") == (False, ["Robert", "Wilson"]), "no surname row")
check(secondary_split(so((None, "Tucker"), ("Karen", "Lynnette")), "Tucker") == (True, ["Karen", "Lynnette"]), "tucker")
check(secondary_split(so((None, None), (None, "Lynne")), "Karp") == (True, ["Lynne"]), "blank surname row")
check(secondary_split(so((None, "Palmer"), (None, "Jacqueline")), "Wavelet-Palmer") == (True, ["Jacqueline"]), "hyphen part")

# row_pr_line: both note shapes
r = row_pr_line(["Register of Wills probate, estate #n/a, Frederick County MD. Decedent Yoshiko Enjoji Tucker. "
                 "Personal Representative Paula A Boyer, 3916 Point Of Rocks Rd, Jefferson MD 21755-7500. Uploaded 2026-09-28"])
check(r == {"first": "Paula", "last": "Boyer", "address": {"street": "3916 Point Of Rocks Rd", "city": "Jefferson",
                                                            "state": "MD", "postal_code": "21755"}}, "RoW upload note")
check(row_pr_line(["PROBATE - Register of Wills\nPR: Tracey Mulili - 205 Bel Aire Ln, Frederick, MD, 21702"])["address"]["city"]
      == "Frederick", "PR: line fallback")
check(row_pr_line(["nothing here"]) is None, "no PR")

# relative obituary window
check(is_relative_date(["360-months", "month"]) and is_relative_date(["24-months", "quarter"]), "relative shapes")
check(not is_relative_date(["2024-01-01", "2026-01-01"]) and not is_relative_date("x") and not is_relative_date([{"a": 1}]),
      "absolute / junk")
check(resolve_relative_date(["24-months", "quarter"], date(2026, 9, 25)) == ["2024-06-25", "2026-06-25"], "09-25 measured")
check(resolve_relative_date(["360-months", "month"], date(2026, 9, 28)) == ["1996-08-28", "2026-08-28"], "09-28 measured")
check(resolve_relative_date(["45-days", "today"], date(2026, 9, 28)) == ["2026-08-14", "2026-09-28"], "days")
check(resolve_relative_date(["2-weeks", "week"], date(2026, 9, 28)) == ["2026-09-07", "2026-09-21"], "weeks")
check(resolve_relative_date(["1-months", "month"], date(2026, 3, 31)) == ["2026-01-28", "2026-02-28"], "month-end clamp (start counts from the clamped end)")
q = to_query({"must": {"last_obituary_date": ["360-months", "month"], "account": "x",
                       "any_county": [{"uuid": "u", "title": "Fairfax", "isNegative": False},
                                      {"uuid": "v", "title": "Loudoun", "isNegative": True}]}})
check(q["must"]["last_obituary_date"] == resolve_relative_date(OBIT_WINDOW_VALUE), "to_query resolves")
check(q["must"]["any_county"] == ["Fairfax"] and q["must"]["must_not"]["any_county"] == ["Loudoun"], "counties untouched")
check(to_query({"must": {"last_obituary_date": ["2024-01-01", "2026-01-01"]}})["must"]["last_obituary_date"]
      == ["2024-01-01", "2026-01-01"], "absolute passes through")

print(f"OK: {n} checks")
