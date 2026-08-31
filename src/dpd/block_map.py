"""How each semantic block in `preset_spec` is expressed on the live Records panel.

Everything here was read off the panel on 2026-08-26, not guessed. The vocabulary dump is
`output/dpd_filter_blocks.json` (144 blocks) and the Params & Others parameter list is
`output/dpd_params_others.json`.

THE ACTION BAR IS REAL AND IT IS AT THE TOP OF THE PANEL: `Load | Save | Save New | Clear`,
directly under the "Filter Records" heading. Five separate DOM scans reported it missing,
every one of them because they filtered to LEAF elements (`children.length === 0`) and each
control wraps an `<svg>` icon next to its text node. Scan by `innerText` across all
elements and take the narrowest match. `Save New` is enabled as soon as one filter block
exists; `Save` stays greyed until a preset is loaded.

Save New opens an inline dialog with `input[name="new_preset_name"]`, a FOLDER dropdown
(defaults to `default`), two checkboxes (Add preset to quick filters, Export file on
schedule) and `Cancel` / `Save Preset`.

PARAMS & OTHERS IS THE BOOLEAN CONTAINER, and it is what the challenge guide's wording was
always referring to ("Numbers No, Skiptraced No", "Vacant Mailing No"). Without it, stages
00 and 01 would have been byte-identical presets, because nothing else on the panel can say
"has been skip traced".
"""
from __future__ import annotations

# Exact block labels from the live "Add new filter block" dropdown.
BLOCK = {
    "tags_any": "Any Tags (OR)",
    "tags_all": "All Tags (AND)",
    "lists_any": "Any Lists (OR)",
    "status": "Property Status",
    "call_attempts": "Call Attempts",
    "mail_attempts": "Direct Mail Attempts",
    "phone_count": "Phone Count",
    "params": "Params & Others",
    "structure": "Structure Types",
    "last_mailed": "Last Direct Mailed",
    "county": "Property County",
}

# Value-entry placeholders, per block.
PLACEHOLDER = {
    "tags_any": "Search for tags",
    "tags_all": "Search for tags",
    "lists_any": "Search for lists",
    "status": "Enter property status",
    "structure": "Enter structure types",
    "county": "Enter county name",
}

# Every value block carries this toggle. "Include" is the default.
MODE_INCLUDE = "Include"
MODE_EXCLUDE = "Do not include"

# The 13 parameters inside Params & Others, each a Yes/No style selector.
PARAMS = ["Absentee", "Vacant Mailing", "Vacant Property", "Property PO Box",
          "Owner PO Box", "Numbers", "Phone Type", "Owner Type", "DNC", "Opt-out",
          "Skiptraced", "Direct Mailed", "Deceased", "Record Type"]

# Semantic block -> the Params & Others parameter that expresses it.
PARAM_FOR = {
    "has_numbers": "Numbers",
    "skiptraced": "Skiptraced",
    "vacant_mailing": "Vacant Mailing",
    "deceased": "Deceased",
    "vacant_property": "Vacant Property",
}

# Suppression that rides in Params & Others rather than in Property Status. This is a
# genuine recovery, not a workaround: the Property Status picker does not offer `DNC` or
# `Opt-out` at all, so naming them there would have produced a suppression that silently
# did not apply.
PARAM_SUPPRESSION = {"DNC": "No", "Opt-out": "No"}

# The Property Status picker offers 19 of this account's 38 statuses. These six are in
# DEAD_STATUSES but cannot be selected, verified one at a time by typing each into the
# picker and reading back its options (10 of 16 matched, these 6 returned nothing at all).
# DNC and Opt-out are recovered above; the remaining four are a stated gap.
STATUS_NOT_SELECTABLE = ["Opt-out", "Lost Deal", "Close Out", "Buyer", "Buyer Found",
                         "Buyer Lost"]
STATUS_GAP_UNRECOVERED = ["Lost Deal", "Close Out", "Buyer", "Buyer Found", "Buyer Lost"]

# ---------------------------------------------------------------- stated gaps
# Each of these is omitted from the built presets ON PURPOSE, with the reason, rather than
# approximated by a control that does not mean the same thing.
GAPS = {
    "last_mailed_days_min": (
        "`Last Direct Mailed` offers Fixed / Since / Prior to Date against a CALENDAR -- "
        "there is no relative mode. A saved preset holding `prior to <today-30>` gets "
        "MORE restrictive as time passes: six months on it hides every record mailed "
        "since that fixed date, which is work silently disappearing from a queue. The "
        "mail-attempts counter already gates each stage, so the date block is omitted and "
        "the 30-day spacing is a cadence rule the operator holds, not a filter."),
    "status_age_days_min": (
        "No block filters on how long a record has been IN a status. `Last Updated Date` "
        "is the nearest and means any edit at all, so it would silently reactivate a "
        "record whose only change was a tag. The four Reactivation timers are built "
        "without their age gate and are flagged; they return every Not Interested record "
        "in scope until a status-age block exists."),
    "structure": (
        "Omitted deliberately. Filtering `Structure Types = Single Family` would DROP the "
        "2,892 records whose structure_type is permanently blank (condo/apartment units "
        "with no APN), and the rule is that blank is unknown and surfaced, never dropped. "
        "The buy box is applied at PULL time instead (`type_single_family=true` in the "
        "SiftMap tier configs), so every record these folders can see is already single "
        "family by construction."),
    "sold_tag": (
        "Suppression part 2 wanted a recently-sold TAG. No such tag exists among the 340 "
        "on this account; `Sold` and `Already Sold` are statuses and are excluded by part "
        "1. The tag is a Phase 4 item."),
}


# ------------------------------------------------- one block of each type, ever
# Verified live: adding a second block of the same type is refused (the dropdown stops
# offering it). The AND and OR variants ARE separate types and coexist, so each preset has
# a budget of exactly one OR-group and one AND-group for tags, and the same for lists.
#
#   Any Tags (OR) + All Tags (AND)     -> both add, count 2
#   Any Lists (OR) + All Lists (AND)   -> both add, count 4
#   Any Lists (OR) x2                  -> second refused
#   Property Status x2, Call Attempts x2, Params & Others x2 -> second refused
#
# Consequence: a preset needing an OR-include AND an OR-exclude cannot have both. A
# single-tag group is safe in either block, because AND and OR are the same for one value.
SINGLE_INSTANCE = True

# Where that budget forced a trim. Both are stated rather than silently applied.
TAG_BUDGET_TRIMS = {
    # CORRECTED 2026-08-27. The earlier entry for "09 BULK - CALL" assumed an AND block in
    # Do-not-include mode means "exclude records carrying ALL of them". Measured live it is
    # the OPPOSITE: "All Tags (AND)" + Do not include [A, B] removes every record carrying
    # A or B (660 of 2,363 on the Clean tab), while "Any Tags (OR)" + Do not include [A, B]
    # removes none (stored as must_not.all_tags = carrying both). So a multi-value EXCLUSION
    # belongs on the AND block, the include-OR / exclude-AND pair fits every preset, and
    # 09 BULK - CALL keeps BOTH `FTM` and `Mail Only` in its exclusion. See
    # dpd_presets_create.allocate_tags for the measurements.
    "11 DEEP PROSPECTING/04 Return Mail": (
        "Two INCLUDE groups (the four-tag tier scope on OR, `Return Mail` on AND) spend "
        "both tag blocks, so `Mail Only` is not excluded on this one preset. It costs "
        "nothing today, since `Mail Only` is a Phase 4 tag not stamped on any record yet."),
}
