"""The 73 Records presets, as data.

One row per preset, in SEMANTIC blocks -- `call_attempts`, `has_numbers`, `tags_any` --
rather than in panel controls. The translation from these to the live filter blocks is a
separate layer (`dpd_filter_blocks.py` maps the vocabulary; the builder applies it), for
the same reason the SiftMap tier configs were built from a confirmed parameter list rather
than from guessed names: a preset built on the wrong control filters the wrong records and
still looks like a preset.

The cadence comes from the challenge hub's niche/bulk sequential guides, not from
`src/niche_sequential.py`. That module's 12 presets are the SUPERSEDED scheme: they gate on
hand-set tags (`sms_sent`, `called_day1`) in one folder, while this stack gates on two
shared counters across eight folders, which is what makes it self-advancing. The two
designs are incompatible; do not mix them.

THE TWO COUNTERS ARE THE WHOLE MECHANISM. `predictivecall_attempts` and
`directmail_attempts` are account-wide, so a dial or a send from ANY view advances the
record in every preset that reads them. That is why bulk and niche can run at once with no
double-dial, and why a bulk preset must use the counter field and never a tag standing in
for it.
"""
from __future__ import annotations

# ---------------------------------------------------------------- entry scopes
# Which records a folder is allowed to see, before any stage filter.
# Tier 2 subtracts both priority tags so a record is worked at its HIGHEST tier only.
# Bulk is the union of the three, with FTM deliberately excluded so the first-to-market
# niche keeps its own weekly blitz rhythm instead of being diluted into the volume lane.
ENTRY = {
    "HOTTEST": {"any": ["Priority 1"], "none": []},
    "STRONG": {"any": ["Priority 2"], "none": []},
    "FTM": {"any": ["FTM"], "none": []},
    "TIER2": {"any": ["Tier 2"], "none": ["Priority 1", "Priority 2"]},
    "BULK": {"any": ["Priority 1", "Priority 2", "Tier 2"], "none": ["FTM"]},
    "ALL": {"any": ["Priority 1", "Priority 2", "FTM", "Tier 2"], "none": []},
}

# ------------------------------------------------------- universal suppression
# Verified against the Phase 0 baseline: every status and list named here exists on this
# account. Nothing is invented.
#
# The six-part stack from the plan maps onto the Records panel as FIVE parts, and the
# missing one is worth stating rather than quietly dropping:
#   1  done/dead statuses      -> DEAD_STATUSES below
#   2  recently-sold TAG       -> COVERED WITHOUT A PRESET CHANGE, via status. There is
#                                 still no `Sold` tag among the property tags, and there
#                                 must not be: `Sold` and `Already Sold` are STATUSES and
#                                 a same-named tag would split suppression across two
#                                 vocabularies. Instead `manage_sold_properties` (now
#                                 pointed at all 14 jurisdictions) stamps
#                                 datasift_uploader.RECENTLY_SOLD_TAG and the
#                                 "Sold Property Cleanup" sequence maps that tag onto
#                                 SOLD_STATUS ("Already Sold"), which part 1 already
#                                 excludes. These presets therefore need no edit for
#                                 part 2; what they need is the sweep to keep running.
#                                 The per-month "Sold YYYY-MM" companion tag is an audit
#                                 trail of WHEN each record sold, not a filter input.
#   3  low / negative equity   -> SUPPRESS_LISTS below
#   4  dead neighbourhoods     -> NOT a Records filter at all. The panel has no
#                                 neighbourhood block; this is a PULL-side suppression and
#                                 it is already applied in the SiftMap tier configs.
#   5  not single family       -> STRUCTURE below. Blank is UNKNOWN and is surfaced, never
#                                 silently dropped: 2,892 records on this account carry a
#                                 permanently blank structure_type (condo/apartment units
#                                 with no APN, where the assessor tracks one parcel per
#                                 building rather than per unit).
#   6  Mail Only               -> CALL presets only. Records whose every phone scores
#                                 under 20 are not worth a dial but are still worth a
#                                 stamp.
DEAD_STATUSES = [
    "Dead Lead", "Not Interested", "DNC", "Opt-out", "Closed", "Sold", "Already Sold",
    "Under Contract", "Lost Deal", "Close Out", "Not Related to Property",
    "Auction Date Passed", "Listed", "Buyer", "Buyer Found", "Buyer Lost",
]
SUPPRESS_LISTS = ["Low Equity", "Negative Equity"]
SUPPRESS_TAGS_CALL = ["Mail Only"]
STRUCTURE = {"prefer": "Single Family", "blank": "surface, never drop"}

# ------------------------------------------------------------------ the stages
# Six stages per niche CALL folder. 00 and 01 are the data-repair queue: a record with no
# phone is not a call, it is a skip-trace job, and separating "never skipped" from "skipped
# and still empty" is what stops the second being re-skipped forever.
CALL_STAGES = [
    ("00 Needs Skipped", {"has_numbers": False, "skiptraced": False}),
    ("01 Skipped No Numbers", {"has_numbers": False, "skiptraced": True}),
    ("02 Ready to Call", {"has_numbers": True, "call_attempts": (0, 0)}),
    ("03 Call 2", {"has_numbers": True, "call_attempts": (1, 1)}),
    ("04 Call 3", {"has_numbers": True, "call_attempts": (2, 2)}),
    ("05 Call 4", {"has_numbers": True, "call_attempts": (3, 3)}),
]

# Bulk runs the same shape with an extended runway: the niche cadence stops at 3 attempts,
# bulk carries on to 6.
BULK_CALL_STAGES = CALL_STAGES + [
    ("06 Call 5", {"has_numbers": True, "call_attempts": (4, 4)}),
    ("07 Call 6", {"has_numbers": True, "call_attempts": (5, 5)}),
    ("08 Call 7", {"has_numbers": True, "call_attempts": (6, 6)}),
]

# The six-piece mail rotation. Piece 01 has NO call prerequisite -- mail runs in parallel
# with the phone, it does not wait behind it. Pieces cycle handwritten / postcard / check
# and restart at 04, so a household never receives the same piece twice in a row.
MAIL_PIECES = ["Handwritten Letter", "Family Postcard", "Soft-Offer Check",
               "Handwritten Letter", "Family Postcard", "Soft-Offer Check"]
MAIL_STAGES = []
for _i, _piece in enumerate(MAIL_PIECES, start=1):
    _b = {"mail_attempts": (_i - 1, _i - 1), "vacant_mailing": False}
    if _i > 1:
        # Do not re-mail inside a month. Without this the counter alone would let a
        # record take all six pieces in a week.
        _b["last_mailed_days_min"] = 30
    MAIL_STAGES.append((f"Mail {_i:02d} {_piece}", _b))

# --------------------------------------------------------- the special folders
# Deep prospecting is where a record goes when the ordinary cadence cannot reach it.
#
# `tags_any_2` is a SECOND include block, ANDed with the entry scope -- not a replacement
# for it. The distinction is load-bearing: "02 Obituary/Deceased" must mean "a tiered
# record that is also deceased", not "every deceased record in the account". Written as a
# plain `tags_any` it would have silently widened the folder to all 26,643 records,
# including everything the doors-per-deal build never pulled.
# 02 and 05 are expressed through Params & Others (`Deceased`, `Vacant Property`) rather
# than through tags. That is not a workaround, it is the better instrument: the panel
# allows ONE block of each type, so a tag-based version would have spent the only spare
# tag block on an OR-group of three near-synonyms (`Deceased Owner` / `Owner Deceased` /
# `Obituaries`) and still missed any record flagged deceased without one of those tags.
DEEP_STAGES = [
    ("01 No/Bad Phone", {"has_numbers": False, "skiptraced": True}),
    ("02 Obituary/Deceased", {"deceased": True}),
    ("03 Exhausted Call", {"has_numbers": True, "call_attempts": (4, None)}),
    ("04 Return Mail", {"tags_any_2": ["Return Mail"]}),
    ("05 Vacant", {"vacant_property": True}),
]

# Reactivation works the nos. The timer differs by how fast that no goes stale: a
# foreclosure headed to auction changes its mind in a fortnight, a probate takes six weeks,
# and everything else can wait a quarter.
REACTIVATION_STAGES = [
    ("01 Not Interested 15d - Auction", {"status_any": ["Not Interested"],
                                         "status_age_days_min": 15,
                                         "tags_any": ["Priority 1", "Priority 2", "FTM"],
                                         "scope_note": "foreclosure headed to auction"}),
    ("02 Not Interested 45d - Probate", {"status_any": ["Not Interested"],
                                         "status_age_days_min": 45,
                                         "tags_any": ["Priority 1", "Priority 2", "FTM"],
                                         "scope_note": "probate"}),
    ("03 Not Interested 90d - Other FTM", {"status_any": ["Not Interested"],
                                           "status_age_days_min": 90,
                                           "tags_any": ["FTM"]}),
    ("04 Not Interested 45d - Tier 2", {"status_any": ["Not Interested"],
                                        "status_age_days_min": 45,
                                        "tags_any": ["Tier 2"]}),
    ("05 Rehash Ready", {"tags_any_2": ["Rehash Ready"]}),
]

# ------------------------------------------------------------- the 73, as data
# Geography carried by EVERY preset (Basem, 2026-08-27): the 9 jurisdictions of the
# account's newest SiftMap saved filter, "Obituary 9 Counties" (read off its live URL's
# location parameter). Values are the EXACT strings the Property County picker offers --
# exactness matters because the picker also holds near-collisions this set deliberately
# excludes: Baltimore City, Fairfax City, Fredericksburg City.
COUNTY_SCOPE = [
    "District Of Columbia",  # DC 11001
    "Anne Arundel",          # MD 24003
    "Baltimore",             # MD 24005 (Baltimore County, NOT Baltimore City)
    "Frederick",             # MD 24021
    "Montgomery",            # MD 24031
    "Arlington",             # VA 51013
    "Fairfax",               # VA 51059 (county, NOT Fairfax City)
    "Alexandria City",       # VA 51510
    "Prince William",        # VA 51153
]

# MAIL presets cover all 14 DPD jurisdictions (Basem, 2026-08-31: pull everything,
# call only the core 9, mail everywhere). The six extra strings follow the picker's
# convention (bare county name; independent cities carry " City") and match the
# spelling on 4,300+ records already in the account. The saved filter named
# "Obituaries in 15 Counties" is NOT a source -- its location param holds only the
# same 9 as "Obituary 9 Counties" despite the name (read 2026-08-31).
COUNTY_SCOPE_MAIL = COUNTY_SCOPE + [
    "Calvert",               # MD 24009
    "Carroll",               # MD 24013
    "Charles",               # MD 24017 (NOT "Charles City", which is a VA county)
    "Stafford",              # VA 51179
    "Spotsylvania",          # VA 51177
    "Fredericksburg City",   # VA 51630 (independent city, like Alexandria City)
]

# (folder, label, entry scope, channel, stages)
_FOLDER_PLAN = [
    ("01 HOTTEST - CALL", "Hottest", "HOTTEST", "call", CALL_STAGES),
    ("02 HOTTEST - MAIL", "Hottest", "HOTTEST", "mail", MAIL_STAGES),
    ("03 STRONG - CALL", "Strong", "STRONG", "call", CALL_STAGES),
    ("04 STRONG - MAIL", "Strong", "STRONG", "mail", MAIL_STAGES),
    ("05 FTM - CALL", "FTM", "FTM", "call", CALL_STAGES),
    ("06 FTM - MAIL", "FTM", "FTM", "mail", MAIL_STAGES),
    ("07 TIER 2 - CALL", "Tier 2", "TIER2", "call", CALL_STAGES),
    ("08 TIER 2 - MAIL", "Tier 2", "TIER2", "mail", MAIL_STAGES),
    ("09 BULK - CALL", "Bulk", "BULK", "call", BULK_CALL_STAGES),
    ("10 BULK - MAIL", "Bulk", "BULK", "mail", MAIL_STAGES),
    ("11 DEEP PROSPECTING", "Deep", "ALL", "deep", DEEP_STAGES),
    ("12 REACTIVATION", "Rehash", "ALL", "reactivation", REACTIVATION_STAGES),
]


def _suppression(channel: str, stage: dict) -> dict:
    """The universal stack, minus whatever this stage deliberately overrides.

    Reactivation is the one lane that TARGETS a dead status, so it must not inherit the
    exclusion that would empty it. Saying that out loud beats a preset that silently
    returns nothing and reads as "no leads".
    """
    s = {"lists_none": list(SUPPRESS_LISTS), "structure": dict(STRUCTURE)}
    if stage.get("status_any"):
        s["status_none"] = [x for x in DEAD_STATUSES if x not in stage["status_any"]]
        s["status_override"] = ("this lane targets a dead status, so that status is "
                                "removed from the exclusion set")
    else:
        s["status_none"] = list(DEAD_STATUSES)
    if channel in ("call", "deep"):
        s["tags_none_extra"] = list(SUPPRESS_TAGS_CALL)
    return s


def build() -> list[dict]:
    """The 73 preset definitions, fully expanded."""
    out = []
    for folder, label, entry, channel, stages in _FOLDER_PLAN:
        scope = ENTRY[entry]
        for stage_name, blocks in stages:
            sup = _suppression(channel, blocks)
            tags_none = list(scope["none"]) + sup.pop("tags_none_extra", [])
            # A stage may narrow the entry scope (reactivation does).
            tags_any = list(blocks.get("tags_any") or scope["any"])
            # A second include block, ANDed with the first. Never a replacement.
            tags_any_2 = list(blocks.get("tags_any_2") or [])
            out.append({
                "folder": folder,
                "name": f"{label} - {stage_name}",
                "entry": entry,
                "channel": channel,
                "blocks": {k: v for k, v in blocks.items()
                           if k not in ("tags_any", "tags_any_2", "scope_note")},
                "tags_any": tags_any,
                "tags_any_2": tags_any_2,
                "tags_none": tags_none,
                "counties": list(COUNTY_SCOPE_MAIL if channel == "mail" else COUNTY_SCOPE),
                "suppression": sup,
                "note": blocks.get("scope_note"),
            })
    return out


PRESETS = build()

# A miscount here is a missing stage in somebody's call cadence, so it is asserted at
# import rather than trusted.
assert len(PRESETS) == 73, f"expected 73 presets, built {len(PRESETS)}"
_BY_FOLDER: dict[str, int] = {}
for _p in PRESETS:
    _BY_FOLDER[_p["folder"]] = _BY_FOLDER.get(_p["folder"], 0) + 1
assert list(_BY_FOLDER.values()) == [6, 6, 6, 6, 6, 6, 6, 6, 9, 6, 5, 5], _BY_FOLDER
assert len({(p["folder"], p["name"]) for p in PRESETS}) == 73, "duplicate preset name"


def main() -> int:
    print(f"=== {len(PRESETS)} RECORDS PRESETS ===\n")
    cur = None
    for p in PRESETS:
        if p["folder"] != cur:
            cur = p["folder"]
            print(f"\n  {cur}  ({_BY_FOLDER[cur]})")
            print(f"    entry: any={p['tags_any']} none={p['tags_none']}")
        bits = []
        b = p["blocks"]
        if "call_attempts" in b:
            lo, hi = b["call_attempts"]
            bits.append(f"calls {lo} to {'+' if hi is None else hi}")
        if "mail_attempts" in b:
            lo, hi = b["mail_attempts"]
            bits.append(f"mail {lo} to {hi}")
        for k in ("has_numbers", "skiptraced", "vacant_mailing"):
            if k in b:
                bits.append(f"{k}={'Yes' if b[k] else 'No'}")
        if "last_mailed_days_min" in b:
            bits.append(f"last mailed >={b['last_mailed_days_min']}d")
        if "status_any" in b:
            bits.append(f"status={b['status_any']}")
        if "status_age_days_min" in b:
            bits.append(f"in status >={b['status_age_days_min']}d")
        if p["tags_any_2"]:
            bits.append("AND tag any " + "/".join(p["tags_any_2"]))
        print(f"      {p['name']:38s} {', '.join(bits)}")
    print(f"\n  Suppression on every preset: {len(DEAD_STATUSES)} dead statuses, "
          f"lists {SUPPRESS_LISTS}, structure {STRUCTURE['prefer']} "
          f"(blank {STRUCTURE['blank']})")
    print(f"  CALL and DEEP presets additionally exclude {SUPPRESS_TAGS_CALL}")
    print("\n  OK: suppression part 2 (recently sold) is covered through STATUS, "
          "not a preset edit: manage-sold stamps the `Recently Sold` tag and the "
          "cleanup sequence sets status `Already Sold`, which part 1 excludes.")
    print("  N/A: suppression part 4 (dead neighbourhoods) is not a Records filter. "
          "It is applied at pull time in the SiftMap tier configs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
