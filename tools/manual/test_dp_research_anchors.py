"""Offline guard for the anchored research path in obituary_dp_batch (2026-10-01).

Covers: _iso_date, _court_for, _anchor_queries, _url_outside_footprint, _anchor_facts and
_verify_anchors -- the code that turns the vendor file's DOD / case number / middle name /
PR into search anchors and REJECTS a judge's match that contradicts them. No network, no
LLM, no files touched.

    python -X utf8 tools/manual/test_dp_research_anchors.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import obituary_dp_batch as b  # noqa: E402

FAILS = 0


def check(label: str, got, want) -> None:
    global FAILS
    ok = got == want
    FAILS += 0 if ok else 1
    print(f"{'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f"\n       got  {got!r}\n       want {want!r}"))


# ── _iso_date ──
check("iso: vendor MM-DD-YYYY", b._iso_date("08-13-2026"), "2026-08-13")
check("iso: ledger M/D/YYYY", b._iso_date("7/5/2026"), "2026-07-05")
check("iso: already ISO", b._iso_date("2026-08-13"), "2026-08-13")
check("iso: blank", b._iso_date(""), "")
check("iso: garbage", b._iso_date("unknown"), "")

# ── _court_for ──
check("court DC", b._court_for("DC", "District of Columbia"), "DC Superior Court, Probate Division")
check("court MD", b._court_for("MD", "Montgomery"), "Montgomery Register of Wills")
check("court VA", b._court_for("VA", "Fairfax"), "Fairfax Circuit Court")

# ── _anchor_queries ──
q = b._anchor_queries("David Smith", "Washington", "DC", None)
check("queries: no anchors = the one base query", q, ['"David Smith" obituary Washington DC'])
q = b._anchor_queries("David Smith", "Washington", "DC",
                      {"decedent_full": "David Beardsley Smith", "dod": "2026-08-13"})
check("queries: middle name first, year on the base query", q,
      ['"David Beardsley Smith" obituary', '"David Smith" obituary Washington DC 2026'])
q = b._anchor_queries("Joan Leko", "Centreville", "VA", {"decedent_full": "Joan Leko"})
check("queries: two-token full name adds nothing", q, ['"Joan Leko" obituary Centreville Virginia'])
q = b._anchor_queries("Linda Levine", "Rockville", "MD", {"decedent_full": "Linda Zlotowitz Levine"})
check("queries: Sheet26 maiden name becomes the anchored query", q[0], '"Linda Zlotowitz Levine" obituary')

q = b._anchor_queries("William Drews", "Mount Airy", "MD", {"decedent_full": "William A Drews", "dod": "2023-07-29"})
check("queries: a one-letter middle initial is NOT searched", q, ['"William Drews" obituary Mount Airy Maryland 2023'])

# ── _rank_hits ──
hits = [
    {"url": "https://www.ancestry.com/search/categories/bmd_death?name=William_Drews", "title": "William Drews death records", "snippet": ""},
    {"url": "https://www.mercurynews.com/obituaries/donald-william-drews/", "title": "Donald William Drews Obituary", "snippet": "San Jose"},
    {"url": "https://www.dignitymemorial.com/obituaries/marriottsville-md/william-drews-11390434", "title": "William Drews Obituary 2023", "snippet": "Mount Airy, Maryland"},
]
ranked = b._rank_hits(hits, "William Drews", "Mount Airy", "MD", {"dod": "2023-07-29"})
check("rank: the Mount Airy 2023 obituary first, the ancestry listing last",
      [h["url"].split("/")[2] for h in ranked], ["www.dignitymemorial.com", "www.mercurynews.com", "www.ancestry.com"])
check("rank: stable with no anchors", [h["url"] for h in b._rank_hits(hits[:2], "Jane Doe", "", "MD", None)],
      [hits[1]["url"], hits[0]["url"]])

# ── _url_outside_footprint ──
check("url: dignitymemorial brooksville-fl is outside", b._url_outside_footprint(
    "https://www.dignitymemorial.com/obituaries/brooksville-fl/paulette-barazotto-12925416"), True)
check("url: dignitymemorial falls-church-va is inside", b._url_outside_footprint(
    "https://www.dignitymemorial.com/obituaries/falls-church-va/henriette-nguyen-10344323"), False)
check("url: washingtonpost legacy is inside", b._url_outside_footprint(
    "https://www.legacy.com/us/obituaries/washingtonpost/name/x-obituary?id=1"), False)
check("url: a plain funeral home is unknown = inside", b._url_outside_footprint(
    "https://www.collinsfuneralhome.com/obituaries/Janet-Boller-Heins?obId=27375184"), False)
check("url: echovita oakland ca", b._url_outside_footprint(
    "https://www.echovita.com/us/obituaries/ca/oakland/martha-perego-21671262"), False)  # state is a path segment, not a slug
check("url: blank", b._url_outside_footprint(""), False)

# ── _title_outside_footprint ──
check("title: Gaffney, SC is outside", b._title_outside_footprint("Kenneth Gordon Obituary (1948 - 2026) - Gaffney, SC - Gaffney Ledger"), True)
check("title: Washington, DC is inside", b._title_outside_footprint("Jody Tavss Obituary (2026) - Washington, DC - The Washington Post"), False)
check("title: no place", b._title_outside_footprint("David Russell Smith Obituary Aug 27, 2026"), False)
p = {"date_of_death": "2026-06-25", "anchor_hits": [], "location_match": True}
r = b._verify_anchors(p, {}, "https://www.legacy.com/us/obituaries/gaffneyledger/name/kenneth-gordon-obituary?id=1", False,
                      "Kenneth Gordon Obituary (1948 - 2026) - Gaffney, SC - Gaffney Ledger")
check("verify: the title's out-of-state place rejects with no anchor hit", r, "obituary published outside MD/DC/VA and no anchor confirmed")

# ── _anchor_facts ──
facts, strong = b._anchor_facts({"decedent_full": "David Beardsley Smith", "decedent_middle": "Beardsley",
                                 "dod": "2026-08-13", "pr_full": "Jason Abraham Beardsley-smith",
                                 "pr_mailing": "276 Berkeley Pl, Brooklyn, NY 11217"},
                                [{"name": "RHEA R SMITH", "first": "RHEA"},
                                 {"name": "Unknown Smith", "first": ""}])
check("facts: strong when DOD/middle/PR present", strong, True)
check("facts: lines", facts.split("\n"), [
    "- full_name: the filing names the decedent as David Beardsley Smith",
    "- middle_name: Beardsley",
    "- dod: date of death on the filing 2026-08-13",
    "- pr: personal representative Jason Abraham Beardsley-smith (276 Berkeley Pl, Brooklyn, NY 11217)",
    "- relative:Rhea R Smith: on the skip-trace file as a relative (label unverified)",
])
facts, strong = b._anchor_facts({}, [{"name": "JOY GORDON", "first": "JOY"}])
check("facts: relatives only is not strong", strong, False)
check("facts: none at all", b._anchor_facts(None, None), ("", False))

# ── _verify_anchors ──
an = {"dod": "2026-07-05", "decedent_middle": "C."}
p = {"date_of_death": "2022-08-24", "anchor_hits": [], "location_match": True}
r = b._verify_anchors(p, an, "https://www.sheehanfuneralhome.com/obituary/Manuel-Ramos", True)
check("verify: obituary 4 years before the filing DOD is rejected", r.startswith("obituary DOD 2022-08-24 vs filing DOD 2026-07-05"), True)

p = {"date_of_death": "2026-07-03", "anchor_hits": [], "location_match": False}
r = b._verify_anchors(p, an, "https://www.somefh.com/obituaries/manuel-ramos", True)
check("verify: DOD within tolerance counts as the anchor hit", (r, p["anchor_hits"]), ("", ["dod"]))

p = {"date_of_death": "", "anchor_hits": [], "location_match": False}
r = b._verify_anchors(p, an, "https://www.somefh.com/obituaries/manuel-ramos", True)
check("verify: strong anchors, nothing confirmed, no location = rejected",
      r, "no anchor confirmed (filing DOD / middle name / PR / relatives) and no location match")

p = {"date_of_death": "", "anchor_hits": [], "location_match": True}
r = b._verify_anchors(p, an, "https://www.somefh.com/obituaries/manuel-ramos", True)
check("verify: location match alone carries a no-date obituary", r, "")

p = {"date_of_death": "2026-06-25", "anchor_hits": [], "location_match": True}
r = b._verify_anchors(p, {}, "https://www.legacy.com/us/obituaries/gaffneyledger/name/kenneth-gordon-obituary?id=1", False)
check("verify: gaffneyledger is not an out-of-state slug (plain legacy path) -> relies on anchors", r, "")

p = {"date_of_death": "2026-06-12", "anchor_hits": [], "location_match": True}
r = b._verify_anchors(p, {}, "https://www.dignitymemorial.com/obituaries/brooksville-fl/paulette-barazotto-1", False)
check("verify: out-of-state slug with no anchor hit is rejected", r, "obituary published outside MD/DC/VA and no anchor confirmed")

p = {"date_of_death": "2026-06-12", "anchor_hits": ["pr"], "location_match": False}
r = b._verify_anchors(p, {"pr_full": "Susan Gillin"}, "https://www.dignitymemorial.com/obituaries/brooksville-fl/paulette-barazotto-1", True)
check("verify: out-of-state slug survives on a PR hit", (r, p["outside_footprint"]), ("", True))

p = {"date_of_death": "2026-06-12", "anchor_hits": None, "location_match": False}
r = b._verify_anchors(p, None, "", False)
check("verify: no anchors at all = the old behaviour (judge's word stands)", r, "")

print()
print("ALL PASS" if not FAILS else f"{FAILS} FAILED")
sys.exit(1 if FAILS else 0)
