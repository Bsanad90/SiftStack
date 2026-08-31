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

BY_KEY = {j.key: j for j in JURISDICTIONS}
BY_FIPS = {j.fips: j for j in JURISDICTIONS}
BY_NAME = {j.name.lower(): j for j in JURISDICTIONS}


def resolve(text: str) -> Jurisdiction | None:
    """Resolve a jurisdiction from any of the names the pipelines use.

    Accepts the canonical key, the display name, the FIPS code, the Market
    Finder label, or the raw CRM county string. Deliberately exact per field
    rather than fuzzy: "Baltimore" and "Baltimore City" are different places,
    and a substring match is how the wrong county gets pulled.
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
    for j in JURISDICTIONS:
        if j.opportunities_raw and j.opportunities_raw.lower() == low:
            return j
    # Market Finder labels are ambiguous across states ("Frederick" exists in
    # both MD and VA nationally), so they only resolve within our 14.
    hits = [j for j in JURISDICTIONS
            if j.market_finder_label and j.market_finder_label.lower() == low]
    return hits[0] if len(hits) == 1 else None


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
