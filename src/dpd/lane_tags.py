"""Which ENTRY tags a record may carry, from the lists it sits on (Basem, 2026-09-30 / 10-01).

The Records presets open on one entry tag per lane (`preset_spec.ENTRY`): `FTM` (first-to-
market: probate / foreclosure pulls), `Priority 1` / `Priority 2` (SiftMap rungs), `Tier 2`,
and since 2026-09-30 `Obituaries` (folders 13 / 14). Three pushers used to hand out these
tags on their own -- the DP batch's `--extra-tag`, the namesake repair's carry-over, and
`not_related_requalify.entry_tags_for` -- and all three could put `Priority 1` on an
obituary record, which Basem's rule forbids: **an obituary record carries `Obituaries` and
nothing else from this vocabulary, except `FTM` when it is ALSO a probate case.** The
2026-09-30 retag took `Priority 1` off 805 and `FTM` off 1,510 Obituary-list records for
exactly that reason; this module is what keeps them off.

    tags = apply_lane_rule(lists, tags)      # drop what the rule forbids, add what it requires

`lists` are the record's DataSift list titles (any case, already split); `tags` its property
tag titles. Nothing here touches the account: callers PATCH the result (merge-only) and
REMOVE what the rule dropped only through `/remove-tags/` on their own authority.
"""
from __future__ import annotations

ENTRY_TAGS = frozenset({"Priority 1", "Priority 2", "FTM", "Tier 2", "Obituaries"})
OBITUARY_TAG = "Obituaries"

# Substrings (lower-case) of list titles. Obituary wins; Inheritance alone is Priority 1
# (not_related_requalify, 2026-09-04); Register of Wills / probate lists are FTM feeds.
OBIT_LIST_MARKERS = ("obituary",)
P1_LIST_MARKERS = ("inheritance",)
PROBATE_MARKERS = ("register of wills", "probate")


def _has(items: list[str], markers: tuple[str, ...]) -> bool:
    low = [(x or "").lower() for x in items]
    return any(m in s for s in low for m in markers)


def is_obituary(lists: list[str], tags: list[str] = ()) -> bool:
    """On the Obituary list, or already carrying the `Obituaries` tag."""
    return _has(lists, OBIT_LIST_MARKERS) or OBITUARY_TAG in tags


def is_probate(lists: list[str], tags: list[str] = ()) -> bool:
    """A probate signal on the LISTS (Register of Wills Probates, Probate, Pre-Probate) or on
    the TAGS -- the vendor imports' own lower-case `probate` / `Register of Wills` markers.
    The 2026-09-30 retag kept FTM on 330 Obituary-list records on exactly these signals."""
    return _has(lists, PROBATE_MARKERS) or _has(tags, PROBATE_MARKERS)


def derive_entry_tags(lists: list[str], existing_tags: list[str]) -> list[str]:
    """The entry tags a record SHOULD carry given its lists, ignoring what it has.

    Obituary -> [Obituaries] (+ FTM when it is also a probate case; Inheritance adds
    nothing). Otherwise Inheritance -> Priority 1, probate lists -> FTM, and a record with
    no list signal and no entry tag at all -> Priority 2 (the bulk lane).
    """
    if is_obituary(lists, existing_tags):
        return [OBITUARY_TAG] + (["FTM"] if is_probate(lists, existing_tags) else [])
    derived: list[str] = []
    if _has(lists, P1_LIST_MARKERS):
        derived.append("Priority 1")
    if is_probate(lists):
        derived.append("FTM")
    if not derived and not (set(existing_tags) & ENTRY_TAGS):
        derived.append("Priority 2")
    return derived


def forbidden_entry_tags(lists: list[str], tags: list[str]) -> list[str]:
    """Entry tags in `tags` the rule does not allow on this record. Only the obituary case
    forbids anything: everything from the vocabulary except `Obituaries`, and `FTM` too
    unless the record is also a probate case."""
    if not is_obituary(lists, tags):
        return []
    allowed = {OBITUARY_TAG} | ({"FTM"} if is_probate(lists, tags) else set())
    return [t for t in tags if t in ENTRY_TAGS and t not in allowed]


def apply_lane_rule(lists: list[str], tags: list[str]) -> list[str]:
    """`tags` with the forbidden entry tags removed and the required ones present.
    Order of the surviving tags is kept; additions go at the end."""
    drop = set(forbidden_entry_tags(lists, tags))
    out = [t for t in tags if t not in drop]
    for t in derive_entry_tags(lists, out):
        if t not in out:
            out.append(t)
    return out


def split_lists(value) -> list[str]:
    """A DP record's `lists` is a comma-joined string from the prep CSV; a live detail read
    gives dicts with `title`. Accept both."""
    if not value:
        return []
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return [(x.get("title") if isinstance(x, dict) else str(x)) or "" for x in value]
