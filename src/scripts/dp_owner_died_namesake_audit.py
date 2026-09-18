"""Audit every `owner did die` Deep Prospecting verdict for the NAMESAKE shape. READ-ONLY.

Why (2026-09-18): 8131 Turn Loop Rd carried a false "owner did die". Research matched Ronald F.
Zimmerman of Baltimore (68) to the living owner Ronald L. Zimmerman of Glen Burnie (SmartSkip
age 73) because (1) legacy.com stamps an obituary with the FUNERAL HOME's city, (2) `_llm_match`
accepted a 2,936-char legacy.com shell page after a 6,000-char full page had said no match, and
(3) nothing compared the middle initial or the age. This script re-reads every live DP run dir and
scores each owner-died finding on the signals that would have caught it, then writes a review
CSV. It never writes to the account, to a run dir, or to a ledger.

Tiers (each opt-in; a re-run is free because every tier caches under output/namesake_audit/):
  Tier 0  offline, always: the research trail (shell-override shape), the stored search-hit
          title/snippet (age, "of <City>"), SmartSkip age vs obituary birth year, CRM name vs
          decedent name (middle initial, suffix, given name), DOD vs SiftMap obituary date.
  Tier 1  --fetch-pages: re-fetch the obituary page for rows still lacking age or place. Plain
          requests only; --allow-firecrawl permits the billed fallback (legacy.com,
          dignitymemorial, echovita render client-side). --llm re-parses with _llm_match when
          the regexes find nothing (~$0.01/row).
  Tier 2  --owner-of-record: the tax roll. DC via ITSPE (free). MD via SDAT, one billed Firecrawl
          scrape per row, capped by --sdat-limit (default 0 = none). VA: no lookup exists.

Rules the scoring encodes:
  * Compare BIRTH years, not ages: SmartSkip's age is "now", the obituary's is at death.
    |gap| <= 2 ok, == 3 soft (neutral), 4..17 conflict (ancestry_enricher._years_ok tolerance),
    >= 18 a parent/child pair (GENERATION_GAP: the verdict may be right, the SmartSkip tree wrong).
  * A lone strong signal is REVIEW; LIKELY_FALSE needs a second independent negative signal.
  * A title city ("- Glen Burnie, MD - Legacy", "- Falls Church, VA - Dignity Memorial",
    "| Pikesville, MD - Tribute Archive") is the funeral home's. It is recorded (title_city) and
    NEVER scored. Residence comes only from body text: "of <City>, <State>", "resident of".
  * name_match_level() reports MEDIUM for a conflicting middle initial, never a conflict, so the
    audit computes its own initial_relation on top of it.
  * A trust on title is not "decedent not on title": oor_level_* is skipped on trust rows.

Usage:
  python -u src/scripts/dp_owner_died_namesake_audit.py                       # Tier 0, all dirs
  python -u src/scripts/dp_owner_died_namesake_audit.py --owner-of-record      # + free DC ITSPE
  python -u src/scripts/dp_owner_died_namesake_audit.py --run-dir <dir> --key "<street>|<zip>"
  python -u src/scripts/dp_owner_died_namesake_audit.py --fetch-pages --only LIKELY_FALSE,REVIEW
  python tools/manual/test_namesake_audit.py                                   # offline checks
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from obituary_dp_batch import (  # noqa: E402
    _matches_decedent, jdump, jload, name_tokens, names_match_loose, say, sift_key, write_csv,
)
from dp_ghost_name_resolver import suffix_of  # noqa: E402

OUT_DIR = ROOT / "output" / "namesake_audit"
VERDICT_DIED = "owner did die"
SHELL_CHARS = 3000          # a match:true page shorter than this, after a longer match:false one
AGE_OK, AGE_SOFT = 2, 3     # birth-year gap tolerances
DOD_GAP_DAYS = 30
MIN_PAGE_CHARS = 100

# LIKELY_FALSE = the owner is probably alive (a namesake, or the spouse/relative died and the
# verdict was mislabelled). The `shape` column says which. REVIEW = one real signal, or the
# generation-gap shape where the verdict may be right but the SmartSkip tree is not.
RECOMMENDATIONS = ["LIKELY_FALSE", "REVIEW", "NO_SIGNAL", "CLEAN"]
RANK = {r: i for i, r in enumerate(RECOMMENDATIONS)}
GENERATION_GAP = 18   # a birth-year gap this large is a parent/child pair, not a namesake stranger

STRONG = {"age_conflict": 3, "initial_conflict": 3, "suffix_conflict": 3, "given_name_differs": 3,
          "oor_initial_conflict": 3, "oor_level_low": 3}
MEDIUM = {"place_mismatch": 2, "shell_override": 2, "llm_match_false": 2, "dod_gap_30d": 2,
          "flag_name_differs": 2, "generation_gap": 2}
WEAK = {"dod_from_siftmap": 1, "flag_name_confidence_low": 1, "ss_first_differs": 1,
        "confidence_not_high": 1, "oor_surname_differs": 1}
# Markers: kept in `reasons` for triage (the tax-roll candidate list), worth 0 points. On 639 of
# 783 rows the decedent carries a middle initial the CRM cannot confirm; a point here turned
# every lone age gap into a top-bucket row on the first run.
MARKERS = {"decedent_initial_unverifiable": 0, "trust": 0}
POSITIVE = {"confirmed_rels": -3, "oor_high": -3, "initial_match": -2, "age_ok": -2,
            "place_match": -1, "deceased_flag": -1}
IDENTITY = {"confirmed_rels", "oor_high", "initial_match", "age_ok"}
POINTS = {**STRONG, **MEDIUM, **WEAK, **MARKERS, **POSITIVE}

# Title/rank/degree tokens that are not names ("Capt. Roberta J. Grace, USN (Ret.)").
HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "rev", "fr", "capt", "cpt", "col", "ltc", "gen", "brig", "lt",
              "sgt", "msgt", "maj", "cdr", "adm", "hon", "judge", "rabbi", "pastor", "deacon", "prof",
              "sister", "brother", "usn", "usa", "usaf", "usmc", "uscg", "ret", "retired", "md", "phd",
              "esq", "dds", "dvm", "rn", "jd", "cpa", "pe"}

# Local copy of md_land_records_lookup.SDAT_COUNTY_ID's KEYS so Tier 0 never imports that
# module (it pulls dotenv/requests and reads .env at call time).
SDAT_COUNTIES = {
    "Allegany", "Anne Arundel", "Baltimore City", "Baltimore County", "Calvert", "Caroline",
    "Carroll", "Cecil", "Charles", "Dorchester", "Frederick", "Garrett", "Harford", "Howard",
    "Kent", "Montgomery", "Prince George's", "Queen Anne's", "St. Mary's", "Somerset", "Talbot",
    "Washington", "Wicomico", "Worcester",
}

# Postal / spelling variants that name the same place. Small and documented on purpose.
CITY_ALIASES = {
    "washington dc": "washington", "dc": "washington", "washington d c": "washington",
    "district of columbia": "washington", "ft washington": "fort washington",
    "lutherville timonium": "lutherville", "timonium": "lutherville", "glenburnie": "glen burnie",
    "silver springs": "silver spring", "north bethesda": "bethesda", "n bethesda": "bethesda",
    "mt airy": "mount airy", "mt rainier": "mount rainier", "st leonard": "saint leonard",
    "n potomac": "north potomac", "la plata": "laplata", "temple hills": "temple hill",
    "chevy chase village": "chevy chase", "capitol heights": "capitol hts",
}
# Adjacent places where a resident plausibly describes themselves by either name. "near" is
# NEUTRAL (0 points). Baltimore is deliberately NOT a neighbour of Glen Burnie: that pair is
# the measured false positive.
NEIGHBORS = {
    "glen burnie": {"pasadena", "severn", "linthicum", "millersville", "severna park", "ferndale"},
    "silver spring": {"takoma park", "wheaton", "kensington", "chevy chase", "bethesda", "rockville", "burtonsville"},
    "rockville": {"potomac", "gaithersburg", "north potomac", "derwood", "bethesda", "silver spring"},
    "bethesda": {"chevy chase", "potomac", "rockville", "silver spring", "kensington"},
    "alexandria": {"arlington", "springfield", "annandale", "falls church", "mount vernon", "lorton"},
    "arlington": {"alexandria", "falls church", "mclean"},
    "fairfax": {"vienna", "annandale", "burke", "fairfax station", "chantilly", "centreville", "oakton"},
    "frederick": {"walkersville", "urbana", "ijamsville", "middletown", "new market", "mount airy", "adamstown"},
    "annapolis": {"arnold", "edgewater", "severna park", "crownsville", "davidsonville"},
    "germantown": {"clarksburg", "boyds", "gaithersburg"},
    "towson": {"lutherville", "parkville"},
}
for _a, _bs in list(NEIGHBORS.items()):
    for _b in _bs:
        NEIGHBORS.setdefault(_b, set()).add(_a)

STATE_RE = (r"(?:MD|Md\.?|Maryland|VA|Va\.?|Virginia|DC|D\.?C\.?|District\s+[Oo]f\s+Columbia|"
            r"Washington,?\s+D\.?C\.?|PA|Pennsylvania|DE|Delaware|WV|West\s+Virginia|NC|"
            r"North\s+Carolina|FL|Florida|TX|Texas|NJ|New\s+Jersey|NY|New\s+York|CA|California|"
            r"OH|Ohio|GA|Georgia|SC|South\s+Carolina|AZ|Arizona|CO|Colorado|TN|Tennessee|KS|Kansas)")
CITY_RE = r"([A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){0,2})"
_PLACE_PATS = [
    ("of_city_state", re.compile(r"\bof\s+" + CITY_RE + r",?\s+" + STATE_RE + r"\b")),
    # age-anchored: "Ronald F. Zimmerman, 68, of Baltimore, passed away" / "(71) of Herndon"
    ("age_of_city", re.compile(r"(?:\b\d{2,3},?|\(\d{2,3}\))\s+of\s+" + CITY_RE +
                               r"(?=[,.;]|\s+(?:passed|died|who|was|went|entered|departed|peacefully|on\b))")),
    ("resident_of", re.compile(r"\b(?:long-?time\s+|lifelong\s+)?resident\s+of\s+" + CITY_RE + r"\b")),
    ("home_in", re.compile(r"\b(?:his|her|their)\s+(?:home|residence)\s+in\s+" + CITY_RE + r",?\s+" + STATE_RE + r"\b")),
    ("formerly_of", re.compile(r"\bformerly\s+of\s+" + CITY_RE + r",?\s+" + STATE_RE + r"\b")),
]
_TITLE_CITY = re.compile(r"[-|]\s*" + CITY_RE + r",\s*" + STATE_RE + r"\b")
_AGE_PATS = [
    ("age_n", re.compile(r"\b(?:age|aged)\s+(\d{2,3})\b", re.I)),
    ("age_of", re.compile(r"\bat\s+the\s+age\s+of\s+(\d{2,3})\b", re.I)),
    ("comma_age", re.compile(r",\s*(\d{2,3}),\s+(?:of|formerly|a\b|late|beloved|passed|died|went)", re.I)),
    ("paren_age", re.compile(r"\((\d{2,3})\)\s+(?:of|formerly|passed|died)\b", re.I)),
    ("yrs", re.compile(r"\b(\d{2,3})\s*(?:yrs?\.?|years?\s+old)\b", re.I)),
]
_YEAR_RANGE = re.compile(r"\b((?:18|19|20)\d{2})\s*[-–—]\s*((?:19|20)\d{2})\b")
_DATE_RANGE = re.compile(
    r"\b[A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+((?:18|19|20)\d{2})\s*[-–—]\s*"
    r"[A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+((?:19|20)\d{2})\b")
_BORN = re.compile(r"\b[Bb]orn\b[^.\n]{0,60}?\b((?:18|19|20)\d{2})\b")

# ───────────────────────── discovery / loading ─────────────────────────


def run_name(run: Path) -> str:
    try:
        return str(run.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(run).replace("\\", "/")


def find_run_dirs() -> list[Path]:
    """Every live DP run dir with at least one owner-died finding. Backup snapshots excluded."""
    out = []
    for f in sorted(ROOT.glob("output/**/stage_c_research.json")):
        if any("backup" in p.lower() for p in f.relative_to(ROOT).parts):
            continue
        research = jload(f, {}) or {}
        if any(x.get("verdict") == VERDICT_DIED for x in research.get("findings", [])):
            out.append(f.parent)
    return out


def pushed_records(run: Path) -> dict[str, dict]:
    """key -> {uuid, street} for the LATEST successful push (dp_strip_order.pushed_records, copied
    so this script never imports WriteApi)."""
    out: dict[str, dict] = {}
    log = run / "push_log.jsonl"
    if not log.exists():
        return out
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("route") != "api" or not e.get("commit") or e.get("error") or not e.get("uuid"):
            continue
        out[e["key"]] = {"uuid": e["uuid"], "street": e.get("street") or ""}
    return out


def load_run(run: Path) -> dict:
    research = jload(run / "stage_c_research.json", {}) or {}
    return {
        "run": run, "name": run_name(run),
        "findings": [f for f in research.get("findings", []) if f.get("verdict") == VERDICT_DIED],
        "records": jload(run / "records.json", {}) or {},
        "ranked": jload(run / "ranked_records.json", {}) or {},
        "plan": ((jload(run / "push_plan.json", {}) or {}).get("records") or {}),
        "pushed": pushed_records(run),
    }


def owner_age(rk: dict) -> tuple[int | None, str]:
    """SmartSkip age of the person the verdict is about (ancestry_dp_pilot.load_cohort rule):
    the re-rooted decedent when the subject was a namesake child, else the subject."""
    subj = rk.get("subject") or {}
    dec = rk.get("decedent_from_smartskip") or {}
    age, src = subj.get("age"), "smartskip_subject"
    if rk.get("rerooted") and dec.get("age") not in (None, ""):
        age, src = dec.get("age"), "smartskip_decedent"
    try:
        age = int(str(age).strip()) if age not in (None, "") else None
    except (TypeError, ValueError):
        age = None
    return age, (src if age is not None else "")


def winning_trail(f: dict) -> tuple[dict | None, dict | None, list[dict], int]:
    """(search hit for obituary_url, the fetched page entry that matched, the fetched pages
    before it in the same trail, total pages the LLM saw). Tolerates a missing url/page."""
    url = f.get("obituary_url") or ""
    hit = page = None
    before: list[dict] = []
    n_pages = 0
    for s in f.get("searched") or []:
        tr = [e for e in (s.get("trail") or []) if isinstance(e, dict)]
        if not tr:
            continue
        hits = tr[0].get("hits") or []
        pages = [e for e in tr[1:] if e.get("url")]
        n_pages += sum(1 for p in pages if not p.get("skipped"))
        if not url:
            continue
        if hit is None:
            hit = next((h for h in hits if h.get("url") == url), None)
        if page is None:
            for i, p in enumerate(pages):
                if p.get("url") == url and p.get("match"):
                    page, before = p, pages[:i]
                    break
    return hit, page, before, n_pages


def shell_override(before: list[dict], page: dict | None) -> tuple[bool, int]:
    """The Zimmerman shape: a longer page said match:false, then a short page said match:true.
    Returns (flag, chars of the biggest earlier match:false page)."""
    big = [int(q.get("chars") or 0) for q in before
           if q.get("match") is False and not q.get("skipped") and int(q.get("chars") or 0) >= SHELL_CHARS]
    if not page or not big:
        return False, max(big) if big else 0
    return int(page.get("chars") or 0) < SHELL_CHARS, max(big)


# ───────────────────────── name primitives ─────────────────────────

def _clean_tokens(name: str) -> list[str]:
    """Name tokens with punctuation, honorifics, suffixes, quoted nicknames and parenthesised
    maiden names removed, case kept: 'Capt. Roberta J. Grace, USN (Ret.)' -> [Roberta, J, Grace]."""
    raw = re.sub(r"\([^)]*\)", " ", str(name or ""))          # (Stroup), (Ret.)
    raw = re.sub(r"[\"“”'‘’][^\"“”'‘’]*[\"“”'‘’]", " ", raw)  # "Moe"
    sfx = set(suffix_of(raw).split("/")) - {""}
    toks = [t.strip(".,()") for t in raw.replace(",", " ").split()]
    return [t for t in toks if t and re.search(r"[A-Za-z]", t) and t.lower() not in sfx and t.lower() not in HONORIFICS]


def clean_name(name: str) -> str:
    return " ".join(_clean_tokens(name))


def middle_initials(name: str) -> str:
    """Every middle token's initial: 'Ronald F. Zimmerman' -> 'F'; 'Evelyn Elizabeth Green White'
    -> 'EG' (a maiden name rides as a middle); 'Ronald Zimmerman' -> ''."""
    toks = _clean_tokens(name)
    if len(toks) < 3:
        return ""
    return "".join(t[0].upper() for t in toks[1:-1])


def middle_initial(name: str) -> str:
    """The first middle initial (see middle_initials)."""
    return middle_initials(name)[:1]


def owner_initial(rec: dict) -> tuple[str, str]:
    """Middle initial the CRM carries for the owner: 'Tracey M' -> ('M', 'owner_first'); else the
    SmartSkip first-name field ('LARRY DEAN' -> ('D', 'ss_first')); else ('', '')."""
    for field in ("owner_first", "ss_first"):
        toks = _clean_tokens(rec.get(field) or "")
        if len(toks) >= 2:
            return toks[1][0].upper(), field
    return "", ""


def initial_relation(decedent_initials: str, other: str) -> str:
    """`other` (one initial from the CRM or the tax roll) against ALL of the decedent's middle
    initials: 'EG' vs 'G' = match, 'EG' vs 'M' = conflict, either side blank = unknown."""
    if not decedent_initials or not other:
        return "unknown"
    return "match" if other[0].upper() in decedent_initials.upper() else "conflict"


def suffix_relation(decedent_name: str, *owner_names: str) -> str:
    """Sr against Jr is a conflict (a namesake child on the CRM, or the parent in the obituary)."""
    ds = suffix_of(decedent_name)
    for on in owner_names:
        os_ = suffix_of(on)
        if ds and os_:
            return "match" if ds == os_ else "conflict"
    return "unknown"


def given_name_relation(decedent_name: str, *owner_names: str) -> str:
    """'match' when any owner spelling names the decedent (nickname/prefix tolerant, or the
    middle-name-as-first case), 'differs' otherwise, 'unknown' when nothing to compare."""
    dec = clean_name(decedent_name)
    cands = [clean_name(n) for n in owner_names if n]
    cands = [n for n in cands if len(n.split()) >= 2]
    if not dec or len(dec.split()) < 2 or not cands:
        return "unknown"
    dt = dec.split()
    for n in cands:
        if names_match_loose(dec, n) or _matches_decedent(dec, n):
            return "match"
        # 'T. Joseph Touhey' vs 'T Touhey': an initial on either side, same surname.
        nt = n.split()
        if dt[-1].lower() == nt[-1].lower() and (len(dt[0]) == 1 or len(nt[0]) == 1) and dt[0][0].lower() == nt[0][0].lower():
            return "match"
    return "differs"


_ROLL_NOISE = {"jr", "sr", "ii", "iii", "iv", "tr", "trustee", "trustees", "etal", "et", "al", "life", "tenant"}


def roll_middle_initial(roll_owner: str, decedent_first: str) -> str | None:
    """Middle initial on a tax-roll owner string ('ZIMMERMAN RONALD L' / 'JOYNER, MICHAEL L'):
    the token after the decedent's first name. None when the first name is not on the roll."""
    toks = [t.strip(".,") for t in re.split(r"[\s,]+", (roll_owner or "").upper()) if t.strip(".,")]
    first = (decedent_first or "").upper().strip(".,")
    if not first or first not in toks:
        return None
    i = toks.index(first)
    if i + 1 < len(toks) and toks[i + 1].isalpha() and toks[i + 1].lower() not in _ROLL_NOISE:
        return toks[i + 1][0]
    return ""


# ───────────────────────── age / place primitives ─────────────────────────

def extract_age_facts(text: str) -> dict:
    """{age, birth_year, death_year, pattern} from obituary prose or a search snippet."""
    out = {"age": None, "birth_year": None, "death_year": None, "pattern": ""}
    t = text or ""
    m = _DATE_RANGE.search(t) or _YEAR_RANGE.search(t)
    if m:
        b, d = int(m.group(1)), int(m.group(2))
        if 1890 <= b <= 2027 and 1900 <= d <= 2027 and 15 <= d - b <= 115:
            out.update(birth_year=b, death_year=d, pattern="range")
    for label, pat in _AGE_PATS:
        m = pat.search(t)
        if m and 18 <= int(m.group(1)) <= 110:
            out["age"] = int(m.group(1))
            out["pattern"] = (out["pattern"] + "+" + label).strip("+")
            break
    if out["birth_year"] is None:
        m = _BORN.search(t)
        if m and 1890 <= int(m.group(1)) <= 2010:
            out["birth_year"] = int(m.group(1))
            out["pattern"] = (out["pattern"] + "+born").strip("+")
    return out


def extract_place(text: str) -> dict:
    """Residence stated in BODY text: 'of Herndon, Virginia', 'resident of McLean'. Returns
    {city, pattern}. Title cities are handled by title_city() and never scored."""
    for label, pat in _PLACE_PATS:
        m = pat.search(text or "")
        if m:
            city = m.group(1).strip(" .,")
            if city.lower() in ("the", "a", "his", "her") or len(city) < 3:
                continue
            return {"city": city, "pattern": label}
    return {"city": "", "pattern": ""}


def title_city(title: str) -> str:
    m = _TITLE_CITY.search(title or "")
    return m.group(1).strip() if m else ""


def norm_city(s: str) -> str:
    x = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    x = re.sub(r"\b(md|va|dc|maryland|virginia|county|city)\b", " ", x)   # "Frederick County" is Frederick
    x = " ".join(x.split())
    return CITY_ALIASES.get(x, x)


def place_relation(obit_city: str, prop_city: str, mail_city: str = "") -> str:
    o = norm_city(obit_city)
    if not o:
        return "unknown"
    props = {norm_city(prop_city), norm_city(mail_city)} - {""}
    if not props:
        return "unknown"
    if o in props:
        return "match"
    if any(o in NEIGHBORS.get(p, set()) for p in props):
        return "near"
    return "mismatch"


def birth_year_gap(owner_age_: int | None, ref_year: int, obit_birth: int | None,
                   obit_age: int | None, death_year: int | None) -> tuple[int | None, str]:
    """|owner birth year - obituary birth year|. Owner birth = ref_year - SmartSkip age; obituary
    birth = the explicit year, else death_year - age at death."""
    if owner_age_ is None:
        return None, ""
    if obit_birth is not None:
        return abs((ref_year - owner_age_) - obit_birth), "explicit_birth"
    if obit_age is not None and death_year is not None:
        return abs((ref_year - owner_age_) - (death_year - obit_age)), "dod_minus_age"
    return None, ""


def age_relation(gap: int | None) -> str:
    """ok (<=2) / soft (3) / conflict (4..17) / generation (>=18: a parent-child pair -- the
    SmartSkip subject is the namesake CHILD at the address, or the re-root picked the wrong
    generation -- where the verdict may be right and the signer tree wrong)."""
    if gap is None:
        return "unknown"
    if gap <= AGE_OK:
        return "ok"
    if gap <= AGE_SOFT:
        return "soft"
    if gap >= GENERATION_GAP:
        return "generation"
    return "conflict"


def _year(s: str) -> int | None:
    s = (s or "").strip()
    return int(s[:4]) if len(s) >= 4 and s[:4].isdigit() else None


def _parse_date(s: str):
    s = (s or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):     # SiftMap obit_date arrives in both shapes
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def dod_gap_days(dod: str, obit_date: str) -> int | None:
    a, b = _parse_date(dod), _parse_date(obit_date)
    if not a or not b:
        return None
    return abs((a - b).days)


def sdat_county(rec: dict) -> str | None:
    """records.county -> an SDAT_COUNTY_ID key. records.json says plain 'Baltimore' for 133 MD
    rows; SDAT wants 'Baltimore City' or 'Baltimore County'. None = cannot resolve."""
    c = (rec.get("county") or "").strip()
    if c in SDAT_COUNTIES:
        return c
    cl = c.lower().replace("'", "").replace(".", "")
    if cl == "baltimore":
        return "Baltimore City" if norm_city(rec.get("city") or "") == "baltimore" else "Baltimore County"
    for k in SDAT_COUNTIES:
        if k.lower().replace("'", "").replace(".", "") == cl:
            return k
    return None


# ───────────────────────── row assembly + scoring ─────────────────────────

def recommend(row: dict) -> tuple[str, int, list[str], str]:
    """-> (recommendation, score, reasons, shape). Shapes: NAMESAKE (a same-name stranger),
    SPOUSE_OR_RELATIVE (same surname, different given name: the spouse/relative died, the CRM
    owner did not), GENERATION_GAP (parent/child pair, verdict may be right), or ''."""
    reasons: list[str] = []

    def add(code):
        if code not in reasons:
            reasons.append(code)

    if row["age_rel"] == "conflict":
        add("age_conflict")
    elif row["age_rel"] == "generation":
        add("generation_gap")
    elif row["age_rel"] == "ok":
        add("age_ok")
    if row["initial_rel"] == "conflict":
        add("initial_conflict")
    elif row["initial_rel"] == "match":
        add("initial_match")
    elif row["decedent_initial"] and not row["owner_initial"]:
        add("decedent_initial_unverifiable")
    if row["suffix_rel"] == "conflict":
        add("suffix_conflict")
    if row["given_name_rel"] == "differs":
        add("given_name_differs")
    if row["place_rel"] == "mismatch":
        add("place_mismatch")
    elif row["place_rel"] == "match":
        add("place_match")
    if row["shell_override"]:
        add("shell_override")
    if row["llm_match"] == "false":
        add("llm_match_false")
    if row["dod_gap_days"] is not None and row["dod_gap_days"] > DOD_GAP_DAYS:
        add("dod_gap_30d")
    fl = row["flags"] or ""
    if "decedent name differs" in fl and row["given_name_rel"] != "differs":
        add("flag_name_differs")           # the pipeline's own strict check; not counted twice
    if "name_confidence low" in fl:
        add("flag_name_confidence_low")
    if row["dod_source"] == "siftmap_last_obituary_date":
        add("dod_from_siftmap")
    if row["ss_first_differs"]:
        add("ss_first_differs")
    if row["confidence"] and row["confidence"] != "high":
        add("confidence_not_high")
    if row["confirmed_rels_n"] >= 1:
        add("confirmed_rels")
    if row["deceased_flag"]:
        add("deceased_flag")
    if row["oor_initial_rel"] == "conflict":
        add("oor_initial_conflict")
    if row["oor_level"] == "HIGH":
        add("oor_high")
    elif row["oor_level"] == "LOW" and not row["is_trust"] and row["oor_status"] in ("itspe", "sdat", "ledger"):
        add("oor_level_low" if row["oor_surname_on_roll"] else "oor_surname_differs")
    # MEDIUM on the roll is "middle initial not confirmable" -- neutral unless it CONFLICTS,
    # which oor_initial_conflict already carries.
    if row["is_trust"]:
        add("trust")

    score = sum(POINTS.get(r, 0) for r in reasons)
    strong = [r for r in reasons if r in STRONG]
    negatives = [r for r in reasons if r in STRONG or r in MEDIUM]
    pos = [r for r in reasons if r in POSITIVE]
    ident = [r for r in pos if r in IDENTITY]
    second = [r for r in negatives if r not in strong[:1]]      # anything beyond the first strong
    if len(strong) >= 2 or (strong and second and score >= 3) or \
            (("oor_initial_conflict" in reasons or "oor_level_low" in reasons) and not ident):
        rec = "LIKELY_FALSE"
    elif (strong and score > -2) or "generation_gap" in reasons or score >= 2 or (negatives and score >= 1) or \
            ("shell_override" in reasons and not pos):
        rec = "REVIEW"           # a lone strong signal outweighed by two identity positives falls through to CLEAN
    elif score <= 0 and ident:
        rec = "CLEAN"
    else:
        rec = "NO_SIGNAL"
    if rec == "CLEAN" or rec == "NO_SIGNAL":
        shape = ""
    elif "given_name_differs" in reasons:
        shape = "SPOUSE_OR_RELATIVE"
    elif "generation_gap" in reasons and not any(r in reasons for r in ("initial_conflict", "oor_initial_conflict", "place_mismatch", "shell_override", "suffix_conflict")):
        shape = "GENERATION_GAP"
    else:
        shape = "NAMESAKE"
    return rec, score, reasons, shape


def audit_finding(f: dict, rec: dict, rk: dict, plan_rec: dict, pushed: bool, run: str,
                  page_facts: dict | None = None, oor: dict | None = None) -> dict:
    """One CSV row from the finding + its record/ranked/plan context. Pure."""
    rec = rec or {}
    rk = rk or {}
    plan_rec = plan_rec or {}
    decedent = (f.get("decedent_name") or "").strip()
    url = f.get("obituary_url") or ""
    hit, page, before, n_pages = winning_trail(f)
    shell, big = shell_override(before, page)
    ref_year = _year(f.get("researched_at") or "") or date.today().year
    o_age, o_age_src = owner_age(rk)

    snippet_text = " ".join(((hit or {}).get("title") or "", (hit or {}).get("snippet") or "")).strip()
    facts = extract_age_facts(snippet_text)
    age_source = "snippet" if (facts["age"] is not None or facts["birth_year"] is not None) else "none"
    place = extract_place(snippet_text)
    place_source = "snippet_" + place["pattern"] if place["city"] else "none"
    if page_facts:
        if facts["age"] is None and facts["birth_year"] is None and \
                (page_facts.get("age") is not None or page_facts.get("birth_year") is not None):
            facts = {**facts, **{k: page_facts.get(k) for k in ("age", "birth_year", "death_year")}}
            age_source = page_facts.get("source", "page")
        if not place["city"] and page_facts.get("city"):
            place = {"city": page_facts["city"], "pattern": page_facts.get("place_pattern", "")}
            place_source = page_facts.get("source", "page")
    death_year = facts["death_year"] or _year(f.get("dod") or "")
    gap, gap_basis = birth_year_gap(o_age, ref_year, facts["birth_year"], facts["age"], death_year)

    owner_full_crm = f"{rec.get('owner_first', '')} {rec.get('owner_last', '')}".strip()
    owner_full_ss = f"{rec.get('ss_first', '')} {rec.get('ss_last', '')}".strip()
    owner_name = (f.get("owner_name") or "").strip()
    o_init, o_init_src = owner_initial(rec)
    d_init = middle_initials(decedent)
    # The initial compare is meaningful only when the two first names agree.
    first_agree = any(names_match_loose(decedent, n) for n in (owner_name, owner_full_ss, owner_full_crm) if n)
    init_rel = initial_relation(d_init, o_init) if (decedent and first_agree) else "unknown"
    ss_first_differs = False
    of_, sf_ = (rec.get("owner_first") or "").strip(), (rec.get("ss_first") or "").strip()
    if of_ and sf_ and not rec.get("is_trust") and not rec.get("business_name"):
        ss_first_differs = not names_match_loose(f"{of_} {rec.get('owner_last', '')}", f"{sf_} {rec.get('ss_last', '')}")

    row = {
        "recommendation": "", "score": 0, "reasons": "", "shape": "",
        "run_dir": run, "key": f.get("record") or rec.get("key", ""), "dup_key": 0,
        "street": rec.get("street", ""), "city": rec.get("city", ""), "state": (rec.get("state") or "").upper(),
        "zip": rec.get("zip", ""), "county": rec.get("county", ""),
        "owner_first": rec.get("owner_first", ""), "owner_last": rec.get("owner_last", ""),
        "ss_first": rec.get("ss_first", ""), "ss_last": rec.get("ss_last", ""),
        "is_trust": bool(rec.get("is_trust")) or bool(rec.get("business_name")),
        "business_name": rec.get("business_name", ""),
        "decedent_name": decedent, "obituary_url": url,
        "obit_host": urlparse(url).netloc.lower().replace("www.", "") if url else "",
        "dod": f.get("dod", ""), "dod_source": f.get("dod_source", ""), "obit_date": rec.get("obit_date", ""),
        "dod_gap_days": dod_gap_days(f.get("dod", ""), rec.get("obit_date", "")),
        "confidence": f.get("confidence", ""), "flags": "; ".join(f.get("flags") or []),
        "plan_group": plan_rec.get("group", ""), "plan_verdict": plan_rec.get("verdict", ""),
        "pushed": pushed,
        "deceased_flag": bool(((rk.get("subject") or {}).get("deceased_flag"))),
        "rerooted": bool(rk.get("rerooted")),
        "owner_age": o_age, "owner_age_source": o_age_src,
        "obit_age": facts["age"], "obit_birth_year": facts["birth_year"], "obit_death_year": death_year,
        "birth_year_gap": gap, "gap_basis": gap_basis, "age_source": age_source, "age_rel": age_relation(gap),
        "owner_initial": o_init, "owner_initial_source": o_init_src, "decedent_initial": d_init,
        "initial_rel": init_rel,
        "suffix_rel": suffix_relation(decedent, owner_name, owner_full_ss, owner_full_crm) if decedent else "unknown",
        # Trust rows: the CRM "owner" is a trust title and ss_* is a noisy parse of it -- not a
        # person to compare against.
        "given_name_rel": (given_name_relation(decedent, owner_name, owner_full_ss, owner_full_crm)
                           if decedent and not rec.get("is_trust") and not rec.get("business_name") else "unknown"),
        "ss_first_differs": ss_first_differs,
        "obit_city": place["city"], "place_source": place_source,
        "place_rel": place_relation(place["city"], rec.get("city", ""), rec.get("mail_city", "")),
        "title_city": title_city((hit or {}).get("title") or ""),
        "winning_chars": (page or {}).get("chars"), "winning_match": bool(page),
        "shell_override": shell, "pages_fetched": n_pages, "prior_big_false_chars": big,
        "confirmed_rels_n": len(f.get("confirmed_rels") or []),
        "obit_only_names_n": len(f.get("obit_only_names") or []),
        "page_fetched": (page_facts or {}).get("fetched", ""), "page_chars": (page_facts or {}).get("chars", ""),
        "llm_match": (page_facts or {}).get("llm_match", ""),
        "oor_status": "", "oor_source": "", "oor_owners": "", "oor_level": "", "oor_initial": "",
        "oor_initial_rel": "", "oor_decedent_on_title": "", "oor_surname_on_roll": "", "oor_reason": "",
        "researched_at": f.get("researched_at", ""),
    }
    if oor:
        row.update({k: v for k, v in oor.items() if k in row})
    row["recommendation"], row["score"], reasons, row["shape"] = recommend(row)
    row["reasons"] = " ".join(reasons)
    return row


def sort_key(row: dict) -> tuple:
    return (RANK.get(row["recommendation"], 9), -int(row["score"]), 0 if row["pushed"] else 1, row["run_dir"], row["key"])


# ───────────────────────── Tier 1: page re-fetch ─────────────────────────

def page_cache_path(out_dir: Path, key: str) -> Path:
    return out_dir / "page_cache" / (key.replace("|", "_") + ".txt")


def fetch_page(url: str, key: str, out_dir: Path, allow_firecrawl: bool, refetch_empty: bool = False) -> tuple[str, str]:
    """(text, status) with status in cached|fetched|empty. Plain requests unless allow_firecrawl:
    obituary_enricher._fetch_page_text falls to the billed _fetch_firecrawl on 403/timeout and
    for KNOWN_403_DOMAINS, so the fallback is neutralised at module level for a free run."""
    p = page_cache_path(out_dir, key)
    if p.exists() and (p.stat().st_size > 0 or not refetch_empty):
        return p.read_text(encoding="utf-8"), "cached"
    import obituary_enricher as oe  # noqa: E402  (network-capable; Tier 1 only)
    saved = oe._fetch_firecrawl
    if not allow_firecrawl:
        oe._fetch_firecrawl = lambda *a, **k: ""
    try:
        text = oe._fetch_page_text(url) or ""
    except Exception as e:  # noqa: BLE001
        text = ""
        say(f"  fetch error {url}: {e}")
    finally:
        oe._fetch_firecrawl = saved
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return text, ("fetched" if len(text) >= MIN_PAGE_CHARS else "empty")


def page_facts_for(text: str) -> dict:
    facts = extract_age_facts(text[:6000])
    place = extract_place(text[:6000])
    return {"age": facts["age"], "birth_year": facts["birth_year"], "death_year": facts["death_year"],
            "age_pattern": facts["pattern"], "city": place["city"], "place_pattern": place["pattern"],
            "source": "page", "chars": len(text)}


def llm_facts(text: str, f: dict, rec: dict, key: str, cache: dict) -> dict:
    """_llm_match on the cached page: age_at_death + city. None from it means match:false (a
    REVIEW signal in itself); {"error"} means the LLM failed and nothing is concluded."""
    if key in cache:
        return cache[key]
    from obituary_dp_batch import _llm_match  # noqa: E402
    parsed = _llm_match(text[:6000], f.get("owner_name") or "", rec.get("city", ""), rec.get("street", ""), rec.get("state", ""))
    if parsed is None:
        out = {"llm_match": "false"}
    elif parsed.get("error"):
        out = {"llm_match": "error", "error": str(parsed.get("error"))[:200]}
    else:
        out = {"llm_match": "true", "age": parsed.get("age_at_death") or None, "city": parsed.get("city") or "",
               "full_name": parsed.get("full_name") or "", "dod": parsed.get("date_of_death") or ""}
    cache[key] = out
    return out


# ───────────────────────── Tier 2: owner of record ─────────────────────────

def load_ledger_owners() -> dict[str, dict]:
    """sift_key -> {owners: [...], source} from the MD RoW and DC probate ledgers."""
    out: dict[str, dict] = {}
    for path, field, src in ((ROOT / "output" / "md_row_ledger.json", "property_sdat_owner", "md_row_ledger"),
                             (ROOT / "output" / "dc_probate_ledger.json", "property_itspe_owner", "dc_probate_ledger")):
        est = (jload(path, {}) or {}).get("estates") or {}
        for e in est.values():
            if not e.get(field) or not e.get("property_street"):
                continue
            k = sift_key(e.get("property_street", ""), e.get("property_zip", ""))
            owners = [o.strip() for o in re.split(r"\s*/\s*", str(e[field])) if o.strip()]
            if e.get("property_itspe_co_owner"):
                owners.append(str(e["property_itspe_co_owner"]).strip())
            out.setdefault(k, {"owners": owners, "source": src})
    return out


def _decedent_first_last(decedent: str, rec: dict) -> tuple[str, str, str]:
    toks = _clean_tokens(decedent)
    if len(toks) >= 2:
        return toks[0], toks[-1], decedent
    first = (rec.get("ss_first") or rec.get("owner_first") or "").split()
    last = (rec.get("ss_last") or rec.get("owner_last") or "")
    return (first[0] if first else ""), last, f"{first[0] if first else ''} {last}".strip()


def oor_relations(owners: list[str], decedent: str, rec: dict) -> dict:
    """Compare the tax-roll owner strings with the obituary decedent: name_match_level for
    HIGH/MEDIUM/LOW (decedent on title?), plus the middle initial the roll carries."""
    from md_land_records_lookup import name_match_level  # noqa: E402  (pure function; module import only)
    first, last, full = _decedent_first_last(decedent, rec)
    levels = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    best, best_owner = "LOW", ""
    for o in owners:
        if not o:
            continue
        lv = name_match_level(full, last, first, o)
        if levels[lv] > levels[best] or not best_owner:
            best, best_owner = lv, o
    roll_init = roll_middle_initial(best_owner, first) if best_owner else None
    d_init = middle_initials(decedent)
    crm_last = (rec.get("owner_last") or rec.get("ss_last") or "").upper().strip()
    surname_on_roll = bool(crm_last) and any(
        crm_last in {t.strip(".,") for t in re.split(r"[\s,]+", (o or "").upper())} for o in owners)
    return {
        "oor_owners": " / ".join(o for o in owners if o), "oor_level": best,
        "oor_initial": roll_init if roll_init is not None else "",
        "oor_initial_rel": initial_relation(d_init, roll_init) if roll_init is not None else "unknown",
        "oor_decedent_on_title": best == "HIGH", "oor_surname_on_roll": surname_on_roll,
    }


def lookup_owner_of_record(row: dict, rec: dict, cache: dict, ledger: dict, sdat_budget: list[int],
                           sdat_ok: bool) -> dict:
    key = row["key"]
    if key in cache:
        return cache[key]
    state = row["state"]
    decedent = row["decedent_name"]
    res: dict
    if key in ledger:
        res = {"oor_status": "ledger", "oor_source": ledger[key]["source"], "oor_reason": "",
               **oor_relations(ledger[key]["owners"], decedent, rec)}
    elif state == "DC":
        from dc_probate_pull import itspe_verify_address  # noqa: E402  (free ArcGIS; Tier 2 only)
        first, last, full = _decedent_first_last(decedent, rec)
        r = itspe_verify_address(rec.get("street", ""), last, first, full)
        lvl = r.get("level") or ""
        if not lvl or r.get("error"):
            res = {"oor_status": "error", "oor_source": "itspe", "oor_reason": str(r.get("error") or "no level")[:160]}
        elif lvl in ("NO_PARCEL", "MULTI_UNIT"):
            res = {"oor_status": "skipped", "oor_source": "itspe", "oor_reason": lvl,
                   "oor_owners": " / ".join(o for o in r.get("owners") or [] if o)}
        else:
            owners = [o for o in (r.get("owner"), r.get("co_owner")) if o] or list(r.get("owners") or [])
            res = {"oor_status": "itspe", "oor_source": "itspe", "oor_reason": f"level {lvl}; premise {r.get('premise', '')}",
                   **oor_relations(owners, decedent, rec)}
            res["oor_level"] = lvl if lvl in ("HIGH", "MEDIUM", "LOW") else res["oor_level"]
    elif state == "MD":
        county = sdat_county(rec)
        if sdat_budget[0] <= 0:
            res = {"oor_status": "skipped", "oor_source": "sdat", "oor_reason": "sdat budget 0"}
        elif not sdat_ok:
            res = {"oor_status": "skipped", "oor_source": "sdat", "oor_reason": "no SDAT credentials in .env"}
        elif not county:
            res = {"oor_status": "skipped", "oor_source": "sdat", "oor_reason": f"county unresolved: {rec.get('county', '')!r}"}
        else:
            from md_land_records_lookup import lookup_sdat_by_address  # noqa: E402  (billed; Tier 2 only)
            sdat_budget[0] -= 1
            err = ""
            try:
                r = lookup_sdat_by_address(county, rec.get("street", ""), f"{rec.get('owner_first', '')} {rec.get('owner_last', '')}".strip())
            except Exception as e:  # noqa: BLE001
                r = None
                err = str(e)[:160]
            if not r:
                res = {"oor_status": "error" if err else "skipped", "oor_source": "sdat",
                       "oor_reason": err or f"no SDAT parcel ({county})"}
            else:
                res = {"oor_status": "sdat", "oor_source": "sdat", "oor_reason": f"{county}; principal_residence {r.get('principal_residence', '')}",
                       **oor_relations(list(r.get("owner_names") or []), decedent, rec)}
    else:
        res = {"oor_status": "skipped", "oor_source": "", "oor_reason": f"no {state or 'unknown-state'} owner-of-record lookup implemented"}
    cache[key] = res
    return res


def sdat_priority(rows: list[dict]) -> list[dict]:
    """Billed-lookup order: LIKELY_NAMESAKE (pushed first) -> REVIEW with age/place evidence ->
    decedent-has-an-initial rows by gap -> other REVIEW -> NO_SIGNAL. CLEAN never bills."""
    def bucket(r):
        rs = r["reasons"].split()
        if r["recommendation"] == "LIKELY_FALSE":
            return 0
        if r["recommendation"] == "REVIEW" and ("age_conflict" in rs or "place_mismatch" in rs):
            return 1
        if "decedent_initial_unverifiable" in rs:
            return 2
        if r["recommendation"] == "REVIEW":
            return 3
        if r["recommendation"] == "NO_SIGNAL":
            return 4
        return 9
    return sorted([r for r in rows if bucket(r) < 9 and not r["is_trust"]],
                  key=lambda r: (bucket(r), 0 if r["pushed"] else 1, -(r["birth_year_gap"] or 0),
                                 0 if r["plan_group"] == "deceased" else 1, r["key"]))


# ───────────────────────── outputs ─────────────────────────

CSV_COLS = [
    "recommendation", "shape", "score", "reasons", "run_dir", "key", "dup_key", "street", "city", "state", "zip", "county",
    "owner_first", "owner_last", "ss_first", "ss_last", "is_trust", "business_name", "decedent_name", "obituary_url",
    "obit_host", "dod", "dod_source", "obit_date", "dod_gap_days", "confidence", "flags", "plan_group", "plan_verdict",
    "pushed", "deceased_flag", "rerooted", "owner_age", "owner_age_source", "obit_age", "obit_birth_year",
    "obit_death_year", "birth_year_gap", "gap_basis", "age_source", "age_rel", "owner_initial", "owner_initial_source",
    "decedent_initial", "initial_rel", "suffix_rel", "given_name_rel", "ss_first_differs", "obit_city", "place_source",
    "place_rel", "title_city", "winning_chars", "winning_match", "shell_override", "pages_fetched",
    "prior_big_false_chars", "confirmed_rels_n", "obit_only_names_n", "page_fetched", "page_chars", "llm_match",
    "oor_status", "oor_source", "oor_owners", "oor_level", "oor_initial", "oor_initial_rel", "oor_decedent_on_title",
    "oor_surname_on_roll", "oor_reason", "researched_at",
]


def write_summary(path: Path, rows: list[dict], tier_notes: list[str]) -> None:
    by_rec = Counter(r["recommendation"] for r in rows)
    by_run: dict[str, Counter] = {}
    for r in rows:
        by_run.setdefault(r["run_dir"], Counter())[r["recommendation"]] += 1
    reason_counts = Counter(x for r in rows for x in r["reasons"].split())
    lines = [f"# Owner-died namesake audit -- {datetime.now():%Y-%m-%d %H:%M}", "",
             f"Rows: {len(rows)} owner-died findings across {len(by_run)} run dirs. READ-ONLY; nothing written to the account.", ""]
    lines += ["## Recommendation counts", "", "| recommendation | rows | pushed |", "|---|---|---|"]
    for rec in RECOMMENDATIONS:
        lines.append(f"| {rec} | {by_rec.get(rec, 0)} | {sum(1 for r in rows if r['recommendation'] == rec and r['pushed'])} |")
    by_shape = Counter((r["recommendation"], r["shape"]) for r in rows if r["shape"])
    lines += ["", "| recommendation | shape | rows |", "|---|---|---|"]
    for (rec, sh), n in sorted(by_shape.items(), key=lambda kv: (RANK.get(kv[0][0], 9), kv[0][1])):
        lines.append(f"| {rec} | {sh} | {n} |")
    lines += ["", "## Per run dir", "", "| run dir | " + " | ".join(RECOMMENDATIONS) + " | total |", "|---|" + "---|" * (len(RECOMMENDATIONS) + 1)]
    for run, c in sorted(by_run.items(), key=lambda kv: -sum(kv[1].values())):
        lines.append(f"| {run} | " + " | ".join(str(c.get(x, 0)) for x in RECOMMENDATIONS) + f" | {sum(c.values())} |")
    lines += ["", "## Signal counts", "", "| reason | rows |", "|---|---|"]
    for k, v in reason_counts.most_common():
        lines.append(f"| {k} | {v} |")
    cov = {
        "age known": sum(1 for r in rows if r["age_rel"] != "unknown"),
        "place known": sum(1 for r in rows if r["place_rel"] != "unknown"),
        "no age AND no place (Tier 1 candidates)": sum(1 for r in rows if r["age_rel"] == "unknown" and r["place_rel"] == "unknown"),
        "decedent has a middle initial, CRM has none (tax-roll candidates)": sum(1 for r in rows if "decedent_initial_unverifiable" in r["reasons"].split()),
        "owner-of-record resolved": sum(1 for r in rows if r["oor_status"] in ("itspe", "sdat", "ledger")),
    }
    lines += ["", "## Coverage", ""] + [f"- {k}: {v}" for k, v in cov.items()]
    by_state = Counter((r["state"], r["oor_status"] or "not run") for r in rows)
    lines += ["", "| state | owner-of-record status | rows |", "|---|---|---|"]
    for (st, s), n in sorted(by_state.items()):
        lines.append(f"| {st} | {s} | {n} |")
    if tier_notes:
        lines += ["", "## Tier notes", ""] + [f"- {n}" for n in tier_notes]
    top = [r for r in rows if r["recommendation"] == "LIKELY_FALSE"][:40]
    lines += ["", "## LIKELY_FALSE (top 40)", ""]
    for r in top:
        lines.append(f"- [{r['shape']}] **{r['street']}, {r['city']} {r['state']} {r['zip']}** ({r['run_dir']}; pushed={r['pushed']}) "
                     f"owner {r['owner_first']} {r['owner_last']} | decedent {r['decedent_name']} | dod {r['dod']} | "
                     f"SmartSkip age {r['owner_age']} vs obit birth {r['obit_birth_year'] or '?'} / age {r['obit_age'] or '?'} (gap {r['birth_year_gap']}) | "
                     f"obit city {r['obit_city'] or '?'} | roll {r['oor_owners'] or '-'} | score {r['score']}: {r['reasons']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ───────────────────────── main ─────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", action="append", default=[], help="repeatable; default = every live DP run dir")
    ap.add_argument("--key", default="", help="audit one record key (street|zip)")
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--stamp", default=datetime.now().strftime("%Y%m%dT%H%M%S"))
    ap.add_argument("--fetch-pages", action="store_true", help="Tier 1: re-fetch obituary pages lacking age/place")
    ap.add_argument("--allow-firecrawl", action="store_true", help="Tier 1: permit the BILLED Firecrawl fallback")
    ap.add_argument("--refetch-empty", action="store_true")
    ap.add_argument("--llm", action="store_true", help="Tier 1: _llm_match on cached text when regex finds nothing")
    ap.add_argument("--owner-of-record", action="store_true", help="Tier 2: ledgers + DC ITSPE (free) + MD SDAT (billed, capped)")
    ap.add_argument("--sdat-limit", type=int, default=0, help="max BILLED SDAT scrapes this run (default 0)")
    ap.add_argument("--only", default="", help="restrict Tier 1/2 to these Tier-0 recommendations, comma-separated")
    a = ap.parse_args(argv)

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    runs = [Path(p) if Path(p).is_absolute() else ROOT / p for p in a.run_dir] or find_run_dirs()
    say(f"run dirs: {len(runs)}")

    rows: list[dict] = []
    ctx: dict[tuple[str, str], tuple[dict, dict, dict, dict, bool]] = {}
    for run in runs:
        d = load_run(run)
        for f in d["findings"]:
            key = f.get("record") or ""
            if a.key and key != a.key:
                continue
            rec = d["records"].get(key) or {}
            rk = d["ranked"].get(key) or {}
            plan_rec = d["plan"].get(key) or {}
            pushed = key in d["pushed"]
            ctx[(d["name"], key)] = (f, rec, rk, plan_rec, pushed)
            rows.append(audit_finding(f, rec, rk, plan_rec, pushed, d["name"]))
        say(f"  {d['name']}: {len(d['findings'])} owner-died")
    dup = Counter(r["key"] for r in rows)
    for r in rows:
        r["dup_key"] = dup[r["key"]]
    if a.limit:
        rows.sort(key=sort_key)
        rows = rows[:a.limit]
    say(f"Tier 0: {len(rows)} rows; {dict(Counter(r['recommendation'] for r in rows))}")

    only = {x.strip().upper() for x in a.only.split(",") if x.strip()}
    notes: list[str] = []

    def rebuild(r: dict, **kw) -> dict:
        f, rec, rk, plan_rec, pushed = ctx[(r["run_dir"], r["key"])]
        new = audit_finding(f, rec, rk, plan_rec, pushed, r["run_dir"], **kw)
        new["dup_key"] = r["dup_key"]
        return new

    page_facts_cache: dict = jload(out_dir / "page_facts_cache.json", {}) or {}
    llm_cache: dict = jload(out_dir / "llm_cache.json", {}) or {}
    if a.fetch_pages:
        todo_ids = {id(r) for r in rows if r["obituary_url"] and (r["age_rel"] == "unknown" or r["place_rel"] == "unknown")
                    and (not only or r["recommendation"] in only)}
        say(f"Tier 1: {len(todo_ids)} rows to fetch (firecrawl {'ALLOWED' if a.allow_firecrawl else 'off'})")
        fetched = Counter()
        for i, r in enumerate(rows):
            if id(r) not in todo_ids:
                continue
            pf = page_facts_cache.get(r["key"])
            if not pf or (a.refetch_empty and pf.get("fetched") == "empty"):
                text, status = fetch_page(r["obituary_url"], r["key"], out_dir, a.allow_firecrawl, a.refetch_empty)
                pf = page_facts_for(text) if len(text) >= MIN_PAGE_CHARS else {"chars": len(text), "source": "page"}
                pf["fetched"] = status
                page_facts_cache[r["key"]] = pf
                jdump(out_dir / "page_facts_cache.json", page_facts_cache)
            fetched[pf.get("fetched", "")] += 1
            if a.llm and pf.get("fetched") in ("fetched", "cached") and pf.get("age") is None and not pf.get("city"):
                pcp = page_cache_path(out_dir, r["key"])
                text = pcp.read_text(encoding="utf-8") if pcp.exists() else ""
                if len(text) >= MIN_PAGE_CHARS:
                    f, rec, *_ = ctx[(r["run_dir"], r["key"])]
                    lf = llm_facts(text, f, rec, r["key"], llm_cache)
                    jdump(out_dir / "llm_cache.json", llm_cache)
                    pf = {**pf, "llm_match": lf.get("llm_match", "")}
                    if lf.get("llm_match") == "true":
                        pf.update({k: lf.get(k) for k in ("age", "city") if lf.get(k)}, source="llm")
            rows[i] = rebuild(r, page_facts=pf)
        notes.append(f"Tier 1 pages: {dict(fetched)}; firecrawl {'allowed' if a.allow_firecrawl else 'disabled'}; llm {'on' if a.llm else 'off'}")

    if a.owner_of_record:
        cache: dict = jload(out_dir / "owner_of_record_cache.json", {}) or {}
        ledger = load_ledger_owners()
        sdat_ok = False
        if a.sdat_limit > 0:
            try:
                from md_land_records_lookup import load_credentials  # noqa: E402
                load_credentials()
                sdat_ok = True
            except Exception as e:  # noqa: BLE001
                say(f"SDAT disabled: {e}")
        budget = [a.sdat_limit]
        order = sdat_priority(rows)
        seen = {id(r) for r in order}
        ordered = order + [r for r in rows if id(r) not in seen]
        n_calls = Counter()
        index = {id(r): i for i, r in enumerate(rows)}
        for r in ordered:
            if only and r["recommendation"] not in only:
                continue
            f, rec, *_ = ctx[(r["run_dir"], r["key"])]
            had = r["key"] in cache
            res = lookup_owner_of_record(r, rec, cache, ledger, budget, sdat_ok)
            if not had:
                n_calls[res["oor_status"] if res["oor_status"] != "skipped" else f"skipped:{res.get('oor_reason', '')[:24]}"] += 1
                jdump(out_dir / "owner_of_record_cache.json", cache)
            rows[index[id(r)]] = rebuild(r, oor=res, page_facts=page_facts_cache.get(r["key"]))
        notes.append(f"Tier 2 lookups this run: {dict(n_calls)}; SDAT budget left {budget[0]} of {a.sdat_limit}; ledger hits available {len(ledger)}")
        say(f"Tier 2: {dict(n_calls)}")

    rows.sort(key=sort_key)
    stem = f"owner_died_namesake_audit_{a.stamp}" if not a.key else f"owner_died_namesake_audit_{a.key.replace('|', '_')}_{a.stamp}"
    write_csv(out_dir / f"{stem}.csv", rows, CSV_COLS)
    jdump(out_dir / f"{stem}.json", {"generated_at": datetime.now().isoformat(timespec="seconds"), "rows": rows})
    write_summary(out_dir / f"summary_{a.stamp}.md", rows, notes)
    say(f"wrote {out_dir / (stem + '.csv')}")
    say(f"wrote {out_dir / ('summary_' + a.stamp + '.md')}")
    c = Counter(r["recommendation"] for r in rows)
    say(" | ".join(f"{k} {c.get(k, 0)}" for k in RECOMMENDATIONS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
