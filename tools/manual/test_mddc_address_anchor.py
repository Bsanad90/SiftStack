"""Offline checks for the MDDC anchored address parse + notice-type rule (2026-09-30).

Run after ANY change to mddc_trustee_sale_pull.parse_anchored_address /
parse_address / NOTICE_TYPE_RULES:

    python tools/manual/test_mddc_address_anchor.py

Every case is a real notice shape from the 2026-09 daily pulls.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "scripts"))
import mddc_trustee_sale_pull as mp  # noqa: E402

FAILS = 0


def check(label, got, want):
    global FAILS
    ok = got == want
    FAILS += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n      got  {got!r}\n      want {want!r}"))


def addr(text):
    a = mp.parse_address(text)
    return (a["street"], a["city"], a["state"], a["zip"]) if a else None


# --- law firm letterhead first, property in the plural headline, units after a comma
check("law firm vs plural headline + units",
      addr("McMichael Taylor Gray LLC 6 N. East Street, Suite 202 Frederick, MD 21701 404-474-7149 "
           "SUBSTITUTE TRUSTEES' SALE COMMERCIAL MEDICAL/OFFICE CONDOMINIUM UNITS "
           "5 HILLCREST DRIVE, UNITS 100 & 101 FREDERICK, MARYLAND 21703 Under and by virtue"),
      ("5 HILLCREST DRIVE, UNITS 100 & 101", "Frederick", "MD", "21703"))

# --- descriptive "3-STORY" word before the house number, DC quadrant with no comma
check("3-STORY is not a house number; quadrant stays on street",
      addr("SUBSTITUTE TRUSTEE'S SALE 3-STORY BRICK APARTMENT CONVERSION BUILDING "
           "421 T STREET N.W. WASHINGTON, DC 20001 By virtue of"),
      ("421 T STREET N.W.", "Washington", "DC", "20001"))

# --- condo lien sale: "KNOWN AS:" with a unit after the comma
check("known as + #A unit",
      addr("HARVEY WEST AUCTIONEERS, INC. 5335 WISCONSIN AVENUE, N.W., SUITE 440 WASHINGTON, DC 20015 "
           "VALUABLE CONDOMINIUM UNIT KNOWN AS: 3287 SUTTON PLACE NW, #A, WASHINGTON DC 20016 Pursuant to"),
      ("3287 SUTTON PLACE NW, #A", "Washington", "DC", "20016"))

# --- HUD notice: blank on the old parse, "Commonly known as:" names it
check("commonly known as",
      addr("NOTICE OF DEFAULT AND FORECLOSURE SALE ... See attached Exhibit A Commonly known as: "
           "1419 Shady Rest Road, Shady Side, MD 20764 Tax ID: 07-000-04768400"),
      ("1419 Shady Rest Road", "Shady Side", "MD", "20764"))

# --- a line break is a delimiter: GLEN is a street suffix, GLEN BURNIE is the city
check("newline keeps Glen Burnie whole",
      addr("SUBSTITUTE TRUSTEES' SALE OF IMPROVED REAL PROPERTY 107 HUNTINGTON COURT\nGLEN BURNIE, MD 21061 By"),
      ("107 HUNTINGTON COURT", "Glen Burnie", "MD", "21061"))

# --- unit token after the suffix with no comma
check("unit after suffix, no comma",
      addr("TRUSTEE'S SALE OF 7505 DEMOCRACY BLVD #A-417 BETHESDA, MD 20817 Under a power"),
      ("7505 DEMOCRACY BLVD #A-417", "Bethesda", "MD", "20817"))

# --- snippet truncated before the property: the office is the ONLY candidate,
#     so the last-match fallback still returns it (fulltext stage corrects it)
check("office-only snippet still parses (fulltext corrects later)",
      addr("Law Firm LLC\n1099 Winterson Road, Suite 301\nLinthicum Heights, MD 21090\nNOTICE OF"),
      ("1099 Winterson Road, Suite 301", "Linthicum Heights", "MD", "21090"))

# --- office + property both matched by the last-match rule: property wins
check("office skipped when a property candidate exists",
      addr("Firm\n1602 VILLAGE MARKET BLVD. SE, SUITE 310\nLeesburg, VA 20175\n"
           "IMPROVED REAL PROPERTY\n4215 Drake Ct.\nWaldorf, MD 20603\nUnder a power"),
      ("4215 Drake Ct.", "Waldorf", "MD", "20603"))

# --- plain DC caption with no headline stays on the last-match path
check("DC caption, no headline",
      addr("4616 HAYES STREET NORTH EAST, WASHINGTON DC 20019\n \nIn execution of the Superior Court"),
      ("4616 HAYES STREET NORTH EAST", "Washington", "DC", "20019"))

# --- line breaks become commas: the anchor must still match across them
check("known as across a line break",
      addr("VALUABLE CONDOMINIUM UNIT KNOWN AS:\n3287 SUTTON PLACE NW, #A, WASHINGTON DC  20016\n\nPursuant to"),
      ("3287 SUTTON PLACE NW, #A", "Washington", "DC", "20016"))
check("headline across a line break",
      addr("SUBSTITUTE TRUSTEES' SALE\nCOMMERCIAL MEDICAL/OFFICE CONDOMINIUM UNITS\n"
           "5 HILLCREST DRIVE, UNITS 100 & 101\nFREDERICK, MARYLAND 21703\nUnder"),
      ("5 HILLCREST DRIVE, UNITS 100 & 101", "Frederick", "MD", "21703"))
check("line-wrapped street suffix rejoins the street",
      addr("TRUSTEE'S SALE OF 317 WOODSTREAM\nBOULEVARD, STAFFORD, VA 22556 In execution"),
      ("317 WOODSTREAM BOULEVARD", "Stafford", "VA", "22556"))
check("two-property headline takes the first",
      addr("TRUSTEE'S SALE OF 1621 CROMWELL RD, CATLETT, VA 20119 AND 2033 COTTONTAIL LN, CATLETT, VA 20119."),
      ("1621 CROMWELL RD", "Catlett", "VA", "20119"))

# --- notice-type rule
check("DC Decree of Sale is foreclosure",
      mp.classify_notice_type("In execution of the Superior Court for the District of Columbias Decree of "
                              "Sale in Case: 2023-CAB-003618 the undersigned Trustee(s) will offer for sale"),
      "foreclosure")
check("Fairfax special commissioner's judicial sale is foreclosure",
      mp.classify_notice_type("NOTICE OF SPECIAL \nCOMMISSIONER'S JUDICIAL SALE OF\n6935 Compton Lane"),
      "foreclosure")
check("ratification still order_nisi",
      mp.classify_notice_type("ORDERED this 8th day of September, 2026 by the Circuit Court for Montgomery "
                              "County, Maryland, that the sale of the property ... be ratified and confirmed"),
      "order_nisi")

print(f"\n{'ALL PASS' if not FAILS else f'{FAILS} FAILED'}")
sys.exit(1 if FAILS else 0)
