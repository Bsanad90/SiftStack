"""Offline checks for src/scripts/skip_dp_census.py. No network, no account.

Run after ANY change to classify / is_entity / is_dp_tag / is_skip_tag / within_tolerance /
choose_probe_tags / cut_counts. Each check is a shape the 2026-09-25 plan review named as a
way the census could misclassify a record:

  1. Bucket order: the P / not T / D / not DP shape is SCORE, never DP_DECEASED_WITH_NUMBERS.
  2. Mail Only with tiered numbers is TIERED_UNCALLABLE, not OK.
  3. "Owner Alive - Spouse Deceased" / "DP Owner Presumed Alive" are DP tags, never deceased.
  4. Entity owner on both signals (company w/o first name; business word in the name fields),
     and never on "Ralph" (substring trap); the _ENTITY split applies to EVERY not-P bucket.
  5. Skip traced from a tag alone; DP from a local push log alone.
  6. Dead status via snake_case and via the six statuses the 10-name query cannot see.
  7. Set-size tolerance: 20 records or 0.5%, whichever is larger.
  8. OR-probe tag choice: two smallest non-zero tags under the offset ceiling.
  9. Every not-P combination lands in exactly one bucket; DONE_NO_NUMBERS wins over all.

  python tools/manual/test_skip_dp_census.py
"""
import itertools
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

import skip_dp_census as m  # noqa: E402

FAILS = 0


def check(label, cond):
    global FAILS
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        FAILS += 1


def fl(**kw):
    f = {"P": 0, "T": 0, "S": 0, "D": 0, "DP": 0, "MO": 0, "E": 0}
    f.update(kw)
    return f


# 1. precedence
check("P,notT,D,notDP -> SCORE (not DP_DECEASED_WITH_NUMBERS)", m.classify(fl(P=1, D=1)) == "SCORE")
check("P,T,D,notDP -> DP_DECEASED_WITH_NUMBERS", m.classify(fl(P=1, T=1, D=1)) == "DP_DECEASED_WITH_NUMBERS")
check("P,T,D,DP -> OK", m.classify(fl(P=1, T=1, D=1, DP=1)) == "OK")
check("P,notT,DP -> SCORE (DP done does not excuse scoring)", m.classify(fl(P=1, DP=1)) == "SCORE")

# 2. Mail Only
check("P,T,MO,notDP -> TIERED_UNCALLABLE", m.classify(fl(P=1, T=1, MO=1)) == "TIERED_UNCALLABLE")
check("P,T,MO,DP -> OK", m.classify(fl(P=1, T=1, MO=1, DP=1)) == "OK")
check("P,T,MO,D,notDP -> TIERED_UNCALLABLE wins over DP_DECEASED", m.classify(fl(P=1, T=1, MO=1, D=1)) == "TIERED_UNCALLABLE")

# 3. tag vocab
check("Owner Alive - Spouse Deceased is a DP tag", m.is_dp_tag("Owner Alive - Spouse Deceased"))
check("Owner Alive - Spouse Deceased is NOT a deceased tag", "Owner Alive - Spouse Deceased" not in m.DECEASED_TAGS)
check("DP Owner Presumed Alive is a DP tag", m.is_dp_tag("DP Owner Presumed Alive"))
check("Deep Prospected 09/2026 is a DP tag", m.is_dp_tag("Deep Prospected 09/2026"))
check("DP No Numbers 09/2026 is a DP tag", m.is_dp_tag("DP No Numbers 09/2026"))
check("Deceased Owner is NOT a DP tag (pre-existing account tag)", not m.is_dp_tag("Deceased Owner"))
check("Deceased Owner IS a deceased tag", "Deceased Owner" in m.DECEASED_TAGS)
check("Skip Traced 09/2026 is a skip tag", m.is_skip_tag("Skip Traced 09/2026"))
check("Skip Traced IDI 05/2026 is a skip tag", m.is_skip_tag("Skip Traced IDI 05/2026"))
check("REISift Skipped 09/26 is a skip tag", m.is_skip_tag("REISift Skipped 09/26"))
check("skip_traced_2026-09 is a skip tag", m.is_skip_tag("skip_traced_2026-09"))
check("'Needs Skipped' preset-ish tag is NOT a skip tag", not m.is_skip_tag("Needs Skipped"))

# 4. entity
check("company + no first name -> entity", m.is_entity("", "", "Jas Holdings Llc"))
check("company beside a person name -> not entity", not m.is_entity("Sophia", "Wang", "Wangs Family Trust"))
check("'Neil Sandberg Revocable Trust' in name fields -> entity", m.is_entity("Neil Sandberg Revocable", "Trust", ""))
check("Ralph is not an entity (lp substring)", not m.is_entity("Ralph", "Smith", ""))
for base, kw in (("SKIP_TRACE", {}), ("DP_NEVER_TRACED", {"D": 1}),
                 ("DP_TRACED_EMPTY", {"S": 1, "D": 1}), ("DP_TRACED_EMPTY_NO_SIGNAL", {"S": 1})):
    check(f"{base} + entity -> {base}_ENTITY", m.classify(fl(E=1, **kw)) == base + "_ENTITY")
check("DONE_NO_NUMBERS never gets an entity suffix", m.classify(fl(DP=1, E=1)) == "DONE_NO_NUMBERS")
check("SCORE never gets an entity suffix", m.classify(fl(P=1, E=1)) == "SCORE")

# 5. S from tag alone / DP from local log alone are flag-level -- classify only sees the OR
check("notP,S(tag only),notD -> DP_TRACED_EMPTY_NO_SIGNAL", m.classify(fl(S=1)) == "DP_TRACED_EMPTY_NO_SIGNAL")
check("notP,DP(local only) -> DONE_NO_NUMBERS", m.classify(fl(DP=1)) == "DONE_NO_NUMBERS")

# 6. dead statuses
check("not_interested -> dead", m.status_key("not_interested") in m.DEAD_KEYS)
check("Not Interested -> dead", m.status_key("Not Interested") in m.DEAD_KEYS)
check("Already Sold -> dead", m.status_key("Already Sold") in m.DEAD_KEYS)
for s in ("Lost Deal", "Close Out", "Buyer", "Buyer Found", "Buyer Lost", "Opt-out"):
    check(f"{s} -> dead (invisible to the 10-name query)", m.status_key(s) in m.DEAD_KEYS)
check("No Answer -> active", m.status_key("No Answer") not in m.DEAD_KEYS)
check("Re-Skiptrace -> active", m.status_key("Re-Skiptrace") not in m.DEAD_KEYS)
check("blank status -> active", m.status_key(None) not in m.DEAD_KEYS)

# 7. tolerance
check("100 vs 100 ok", m.within_tolerance(100, 100))
check("80 vs 100 within 20", m.within_tolerance(80, 100))
check("79 vs 100 out", not m.within_tolerance(79, 100))
check("9950 vs 10000 within 0.5%", m.within_tolerance(9950, 10000))
check("9940 vs 10000 out", not m.within_tolerance(9940, 10000))

# 8. probe choice
check("two smallest non-zero under ceiling",
      m.choose_probe_tags({"A": 0, "B": 5, "C": 3, "D": 20000, "E": 9}) == ["C", "B"])
check("one candidate -> one tag", m.choose_probe_tags({"A": 0, "B": 5}) == ["B"])

# 9. exhaustive not-P partition
seen = {}
for S_, D_, DP_ in itertools.product((0, 1), repeat=3):
    b = m.classify(fl(S=S_, D=D_, DP=DP_))
    seen.setdefault(b, []).append((S_, D_, DP_))
check("not-P space covers 5 buckets", set(seen) == {"DONE_NO_NUMBERS", "SKIP_TRACE", "DP_NEVER_TRACED",
                                                    "DP_TRACED_EMPTY", "DP_TRACED_EMPTY_NO_SIGNAL"})
check("DP wins every not-P combination", all(c[2] == 1 for c in seen["DONE_NO_NUMBERS"]) and len(seen["DONE_NO_NUMBERS"]) == 4)

# cut_counts
rows = [
    {"bucket": "SCORE", "any_phone_tag": False, "deceased_flag": True, "obit_dated": False,
     "deceased_list": False, "deceased_tag": False, "dp_tag": False, "dp_local": "",
     "dead_status": False, "in_footprint": True},
    {"bucket": "SCORE", "any_phone_tag": True, "deceased_flag": False, "obit_dated": False,
     "deceased_list": False, "deceased_tag": False, "dp_tag": False, "dp_local": "",
     "dead_status": True, "in_footprint": True},
    {"bucket": "TIERED_UNCALLABLE", "any_phone_tag": True, "deceased_flag": False, "obit_dated": True,
     "deceased_list": False, "deceased_tag": False, "dp_tag": False, "dp_local": "",
     "dead_status": False, "in_footprint": False},
]
cuts = m.cut_counts(rows)
check("SCORE all=2 active=1 footprint=1", (cuts["SCORE"]["all"], cuts["SCORE"]["active"], cuts["SCORE"]["active_footprint"]) == (2, 1, 1))
check("SCORE_NO_TAGS counts the untagged one", cuts["SCORE_NO_TAGS"]["all"] == 1)
check("SCORE_deceased_not_dp counts the deceased one", cuts["SCORE_deceased_not_dp"]["all"] == 1)
check("TIERED_UNCALLABLE_deceased active=1 footprint=0",
      (cuts["TIERED_UNCALLABLE_deceased"]["active"], cuts["TIERED_UNCALLABLE_deceased"]["active_footprint"]) == (1, 0))
check("every bucket in BUCKET_ORDER has a next step", all(b in m.NEXT_STEP for b in m.BUCKET_ORDER))

print(f"\n{'ALL OK' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
