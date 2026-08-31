"""One canonical record per target jurisdiction.

Five incompatible county-id schemes are currently scattered across the
pipeline scripts, and none of them agree on the county's NAME either:

    Market Finder            "Baltimore"          (state on a second line)
    MDDC public notices      "Baltimore County"   checkbox index 3
    MD Register of Wills     "Baltimore County"   COUNTY_ID "3"
    MD Land Records          "Baltimore County"   "BA"
    MD SDAT                  "Baltimore County"   "04"   (NOT the same number)
    doors_per_deal_*         "Baltimore, MD"

Calvert is the sharpest example of why these cannot be assumed to line up:
it is 4 on Register of Wills and 05 on SDAT.

This module is ADDITIVE. Existing scripts keep their own dicts; nothing here
changes their behaviour. It exists so new code (the DPD tier configs, the FTM
registry, the QA report) has one place to resolve a jurisdiction, and so a
name used in one system can be translated into another without guessing.

`None` means "this jurisdiction is genuinely absent from that system", and
each absence is annotated - an absence is a research finding, not a TODO:
  * Fredericksburg City has no checkbox on publicnoticevirginia.com at all
    (verified against all 104 live checkboxes).
  * Virginia has no checkbox on mddcpublicnotices.com at all.
  * DC is not in the Maryland-only Register of Wills / Land Records / SDAT.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Jurisdiction:
    key: str                      # canonical slug used by DPD code
    name: str                     # display name, e.g. "Baltimore County, MD"
    state: str                    # full state name
    state_abbr: str
    fips: str
    foreclosure_regime: str       # "judicial" | "non-judicial"

    # Per-source identifiers. None = absent from that source (see `notes`).
    market_finder_label: str | None = None   # exact first line of the MF option
    mddc_checkbox: int | None = None         # mddcpublicnotices.com
    va_checkbox: int | None = None           # publicnoticevirginia.com
    row_county_id: str | None = None         # registers.maryland.gov Estate Search
    landrec_id: str | None = None            # landrec.msa.maryland.gov
    sdat_id: str | None = None               # sdat.dat.maryland.gov
    opportunities_raw: str | None = None     # the raw "County" string in CRM exports

    notes: dict = field(default_factory=dict)


JURISDICTIONS: list[Jurisdiction] = [
    # ---- Maryland (all judicial-foreclosure) ------------------------------
    Jurisdiction(
        key="baltimore_county_md", name="Baltimore County, MD",
        state="Maryland", state_abbr="MD", fips="24005",
        foreclosure_regime="judicial",
        market_finder_label="Baltimore", mddc_checkbox=3,
        row_county_id="3", landrec_id="BA", sdat_id="04",
        opportunities_raw="Baltimore, MD",
        notes={"market_finder": "the dropdown holds BOTH 'Baltimore' and "
                                "'Baltimore City'; match the first line exactly"},
    ),
    Jurisdiction(
        key="montgomery_md", name="Montgomery, MD",
        state="Maryland", state_abbr="MD", fips="24031",
        foreclosure_regime="judicial",
        market_finder_label="Montgomery", mddc_checkbox=16,
        row_county_id="15", landrec_id="MO", sdat_id="16",
        opportunities_raw="Montgomery, MD",
    ),
    Jurisdiction(
        key="anne_arundel_md", name="Anne Arundel, MD",
        state="Maryland", state_abbr="MD", fips="24003",
        foreclosure_regime="judicial",
        market_finder_label="Anne Arundel", mddc_checkbox=1,
        row_county_id="2", landrec_id="AA", sdat_id="02",
        opportunities_raw="Anne Arundel, MD",
        notes={"dpd": "HOA Lien is its ONLY Priority-1 signal, and HOA Lien has "
                      "no known SiftMap parameter - see output/dpd_siftmap_filters.json"},
    ),
    Jurisdiction(
        key="frederick_md", name="Frederick, MD",
        state="Maryland", state_abbr="MD", fips="24021",
        foreclosure_regime="judicial",
        market_finder_label="Frederick", mddc_checkbox=10,
        row_county_id="10", landrec_id="FR", sdat_id="11",
        opportunities_raw="Frederick, MD",
    ),
    Jurisdiction(
        key="carroll_md", name="Carroll, MD",
        state="Maryland", state_abbr="MD", fips="24013",
        foreclosure_regime="judicial",
        market_finder_label="Carroll", mddc_checkbox=6,
        row_county_id="6", landrec_id="CR", sdat_id="07",
        opportunities_raw="Carroll, MD",
    ),
    Jurisdiction(
        key="calvert_md", name="Calvert, MD",
        state="Maryland", state_abbr="MD", fips="24009",
        foreclosure_regime="judicial",
        market_finder_label="Calvert", mddc_checkbox=4,
        row_county_id="4", landrec_id="CV", sdat_id="05",
        opportunities_raw="Calvert, MD",
        notes={"ids": "4 on Register of Wills but 05 on SDAT - the schemes differ"},
    ),
    Jurisdiction(
        key="charles_md", name="Charles, MD",
        state="Maryland", state_abbr="MD", fips="24017",
        foreclosure_regime="judicial",
        market_finder_label="Charles", mddc_checkbox=8,
        row_county_id="8", landrec_id="CH", sdat_id="09",
        opportunities_raw="Charles, MD",
    ),

    # ---- District of Columbia --------------------------------------------
    Jurisdiction(
        key="district_of_columbia", name="District of Columbia",
        state="District of Columbia", state_abbr="DC", fips="11001",
        foreclosure_regime="non-judicial",
        market_finder_label="District of Columbia", mddc_checkbox=25,
        row_county_id=None, landrec_id=None, sdat_id=None,
        opportunities_raw="District of Columbia, DC",
        notes={"market_finder": "NO sub-county breakdown - the grid returns only the "
                                "county rollup row and the ZIP input is disabled. "
                                "Verified 2026-08-25. No T1 neighbourhood layer, no "
                                "dead-neighbourhood suppression for DC.",
               "md_sources": "not a Maryland county; Register of Wills, Land Records "
                             "and SDAT do not cover it"},
    ),

    # ---- Virginia (all non-judicial) --------------------------------------
    Jurisdiction(
        key="fairfax_va", name="Fairfax, VA",
        state="Virginia", state_abbr="VA", fips="51059",
        foreclosure_regime="non-judicial",
        market_finder_label="Fairfax", va_checkbox=29,
        opportunities_raw="Fairfax, VA",
        notes={"market_finder": "the dropdown may also hold 'Fairfax City'; match the "
                                "first line exactly"},
    ),
    Jurisdiction(
        key="prince_william_va", name="Prince William, VA",
        state="Virginia", state_abbr="VA", fips="51153",
        foreclosure_regime="non-judicial",
        market_finder_label="Prince William", va_checkbox=75,
        opportunities_raw="Prince William, VA",
        notes={"dpd": "top P1 stack is Bad Credit + Out-of-State (196.6x); NEITHER "
                      "has a known SiftMap parameter"},
    ),
    Jurisdiction(
        key="arlington_va", name="Arlington, VA",
        state="Virginia", state_abbr="VA", fips="51013",
        foreclosure_regime="non-judicial",
        market_finder_label="Arlington", va_checkbox=6,
        opportunities_raw="Arlington, VA",
    ),
    Jurisdiction(
        key="stafford_va", name="Stafford, VA",
        state="Virginia", state_abbr="VA", fips="51179",
        foreclosure_regime="non-judicial",
        market_finder_label="Stafford", va_checkbox=90,
        opportunities_raw="Stafford, VA",
    ),
    Jurisdiction(
        key="spotsylvania_va", name="Spotsylvania, VA",
        state="Virginia", state_abbr="VA", fips="51177",
        foreclosure_regime="non-judicial",
        market_finder_label="Spotsylvania", va_checkbox=89,
        opportunities_raw="Spotsylvania, VA",
    ),
    Jurisdiction(
        key="fredericksburg_city_va", name="Fredericksburg City, VA",
        state="Virginia", state_abbr="VA", fips="51630",
        foreclosure_regime="non-judicial",
        market_finder_label="Fredericksburg City", va_checkbox=None,
        opportunities_raw="Fredericksburg City, VA",
        notes={"va_notices": "NO checkbox on publicnoticevirginia.com - verified absent "
                             "from all 104 live checkboxes, not a parsing miss. Open "
                             "question whether its notices fold into Spotsylvania's.",
               "market_finder": "IS covered (verified 2026-08-25): one ZIP 22401 and 7 "
                                "neighbourhoods. Only the FTM notice feed is missing "
                                "here, not the geography layer.",
               "dpd": "no Priority-1 row exists in the doors-per-deal workbook"},
    ),
]

# ---------------------------------------------------------------- Tennessee
# NOT part of the doors-per-deal footprint, and deliberately NOT in JURISDICTIONS:
# six scripts iterate that list as "the 14 target jurisdictions" and would report
# these two as coverage gaps (dpd_notice_publication_report line 92 especially).
# They live here so `resolve()` and the BY_* maps can find them, which is what the
# Knox/Blount legacy pipelines (manage-sold, the TN FTM pull) actually need.
TN_JURISDICTIONS: list[Jurisdiction] = [
    Jurisdiction(
        key="knox_tn", name="Knox, TN",
        state="Tennessee", state_abbr="TN", fips="47093",
        foreclosure_regime="non-judicial",
        market_finder_label="Knox",
        notes={"scope": "legacy TN footprint, not a doors-per-deal target",
               "mddc": "Maryland/DC site has no Tennessee at all",
               "va_notices": "publicnoticevirginia.com is Virginia-only",
               "md_sources": "Register of Wills / Land Records / SDAT are Maryland-only",
               "tn_notices": "covered by tnpublicnotice.com via the TN FTM pipeline, "
                             "which uses its own saved-search names rather than an id"},
    ),
    Jurisdiction(
        key="blount_tn", name="Blount, TN",
        state="Tennessee", state_abbr="TN", fips="47009",
        foreclosure_regime="non-judicial",
        market_finder_label="Blount",
        notes={"scope": "legacy TN footprint, not a doors-per-deal target",
               "mddc": "Maryland/DC site has no Tennessee at all",
               "va_notices": "publicnoticevirginia.com is Virginia-only",
               "md_sources": "Register of Wills / Land Records / SDAT are Maryland-only",
               "tn_notices": "covered by tnpublicnotice.com via the TN FTM pipeline"},
    ),
]

# Lookups span every jurisdiction any pipeline in this repo can be pointed at, so a
# name resolves once and the same FIPS reaches SiftMap regardless of which pipeline
# asked. JURISDICTIONS itself stays the 14 doors-per-deal targets.
ALL_JURISDICTIONS: list[Jurisdiction] = JURISDICTIONS + TN_JURISDICTIONS

BY_KEY = {j.key: j for j in ALL_JURISDICTIONS}
BY_FIPS = {j.fips: j for j in ALL_JURISDICTIONS}
BY_NAME = {j.name.lower(): j for j in ALL_JURISDICTIONS}


def _bare_name_aliases() -> dict[str, Jurisdiction]:
    """Map the names a human or a CLI actually types onto jurisdictions.

    Two forms beyond the canonical display name, because `main.py` passes
    `--counties` through `.title()` and nobody types the ", MD" suffix:

        "Baltimore County"  -> Baltimore County, MD   (name minus state suffix)
        "Fairfax County"    -> Fairfax, VA            (name plus "County")

    Every generated key is asserted unique below rather than assumed. A
    collision here would be a wrong-county bug, so it fails at import.
    """
    out: dict[str, Jurisdiction] = {}
    for j in ALL_JURISDICTIONS:
        bare = j.name.rsplit(",", 1)[0].strip()
        forms = [bare]
        # "Baltimore County" already says County; "Fredericksburg City" is a real
        # distinct place and must never grow a "County" form.
        if not bare.endswith(("County", "City")):
            forms.append(f"{bare} County")
        for form in forms:
            key = form.lower()
            if key in BY_NAME:
                continue  # the canonical name wins
            assert key not in out, f"ambiguous jurisdiction alias {form!r}"
            out[key] = j
    return out


BY_BARE_NAME = _bare_name_aliases()


def resolve(text: str) -> Jurisdiction | None:
    """Resolve a jurisdiction from any of the names the pipelines use.

    Accepts the canonical key, the display name, the display name without its
    state suffix ("Baltimore County"), that name plus an explicit "County"
    ("Fairfax County"), the FIPS code, the Market Finder label, or the raw CRM
    county string. Deliberately exact per field rather than fuzzy: "Baltimore"
    and "Baltimore City" are different places, "Fairfax" and "Fairfax City" are
    different places, and a substring match is how the wrong county gets pulled.

    Returns None on a miss. Callers must treat that as an error and stop -- a
    caller that substitutes a default FIPS silently queries the wrong county.
    """
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()
    if low in BY_KEY:
        return BY_KEY[low]
    if t in BY_FIPS:
        return BY_FIPS[t]
    if low in BY_NAME:
        return BY_NAME[low]
    if low in BY_BARE_NAME:
        return BY_BARE_NAME[low]
    for j in ALL_JURISDICTIONS:
        if j.opportunities_raw and j.opportunities_raw.lower() == low:
            return j
    # Market Finder labels are ambiguous across states ("Frederick" exists in
    # both MD and VA nationally), so they only resolve within our own set.
    hits = [j for j in ALL_JURISDICTIONS
            if j.market_finder_label and j.market_finder_label.lower() == low]
    return hits[0] if len(hits) == 1 else None


def county_label(j: Jurisdiction) -> str:
    """The short county name the DataSift UI uses: no state suffix, no "County".

    "Baltimore County, MD" -> "Baltimore";  "Montgomery, MD" -> "Montgomery";
    "Fredericksburg City, VA" -> "Fredericksburg City" (an independent city, so
    "City" is part of the name and is kept).
    """
    bare = j.name.rsplit(",", 1)[0].strip()
    if bare.endswith(" County"):
        bare = bare[: -len(" County")].strip()
    return bare


def location_param(j: Jurisdiction) -> str:
    """Build SiftMap's `location` JSON for a whole-county search.

    The shape is copied verbatim from `dpd_siftmap_discover.location_param`,
    which ran live across all 14 jurisdictions while 152 SiftMap presets were
    built and verified. Two things about it that look wrong and are not:

      * `title` is cosmetic. It is what the search box displays, and DC renders
        as the nonsense "District of Columbia County, DC". `counties[].fips` is
        what actually drives the query, so leave the title alone.
      * `county` carries the SHORT county name, not the display name, matching
        what the UI puts there itself. "Baltimore County, MD" must send
        "Baltimore" -- sending "Baltimore County" yields the doubled title
        "Baltimore County County, MD" and a county_name the proven payload never
        used. A trailing "City" is NOT stripped: "Fredericksburg City" is the
        real name of a distinct independent city.
    """
    short = county_label(j)
    return json.dumps({
        "searchType": "county",
        "title": f"{short} County, {j.state_abbr}",
        "county": short,
        "state": j.state_abbr,
        "counties": [{"fips": j.fips, "county_name": short}],
    })


def with_source(attr: str) -> list[Jurisdiction]:
    """Jurisdictions that a given source actually covers."""
    return [j for j in JURISDICTIONS if getattr(j, attr) is not None]


def coverage_table() -> str:
    """Human-readable matrix of which source reaches which jurisdiction."""
    cols = [("MktFinder", "market_finder_label"), ("MDDC", "mddc_checkbox"),
            ("VA", "va_checkbox"), ("RoW", "row_county_id"),
            ("LandRec", "landrec_id"), ("SDAT", "sdat_id")]
    head = "%-26s %-6s %-5s " % ("Jurisdiction", "FIPS", "Reg.")
    head += " ".join("%-9s" % c[0] for c in cols)
    lines = [head, "-" * len(head)]
    for j in JURISDICTIONS:
        row = "%-26s %-6s %-5s " % (j.name, j.fips, j.foreclosure_regime[:5])
        row += " ".join("%-9s" % ("yes" if getattr(j, a) is not None else "-")
                        for _, a in cols)
        lines.append(row)
    return "\n".join(lines)


if __name__ == "__main__":
    print(coverage_table())
    print()
    for j in JURISDICTIONS:
        for k, v in j.notes.items():
            print("%-26s [%s] %s" % (j.name, k, v))
