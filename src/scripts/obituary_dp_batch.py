"""Deep-prospect a DataSift "Needs Skipped" OBITUARY export and reload it.

Built 2026-08-28 for `Hottest - 00 Needs Skipped` on the moe@galaldev.com account:
641 deceased-owner records, zero phones. The chain is the deep-prospecting-v5
heir engine run as a BATCH instead of one hand-written research pack per house:

    prep      export CSV  -> SmartSkip input (persons + parsed trustees) + sidecars
    trace     SmartSkip bulk order (submit is free, --pay bills the card)
    rank      SmartSkip export -> relatives ranked as signers, REL slots, Tracerfy gap-fill
    research  obituary search + LLM: who actually died, DOD, spouse-obituary trap
    augment   obituary-named signers SmartSkip missed -> Tracerfy at the property address
    score     Trestle dial tiers on every number that will be uploaded
    build     reload CSVs, phone-tag CSV, review workbook, push plan
    schema    make sure REL{N}: Full Name custom fields exist for the deepest record
    push      write ONE record, read it back, then the pilot, then everything

Writeback convention (Basem, 2026-08-28): the decedent stays the record owner;
every relative's phones land on the record tagged Rel{N}.{M} plus a dial tier;
every relative's name lands in the `REL{N}: Full Name` custom field, with NO cap
on N (new REL fields are created by `schema`); `Deceased Owner` property tag;
signers, DOD, obituary URL and MUST-VERIFY flags in the note.

Run from the repo root with `python -u` (stdout is block-buffered when redirected).
Every stage is resumable and caches under output/dp_hottest_needs_skipped/.

    python -u src/scripts/obituary_dp_batch.py prep --export "C:/Users/pc/Downloads/Hottest - 00 Needs skipped.csv" --pilot 25
    python -u src/scripts/obituary_dp_batch.py trace --csv smartskip_input_pilot.csv          # free: shows the bill
    python -u src/scripts/obituary_dp_batch.py trace --csv smartskip_input_pilot.csv --pay    # bills the card
    python -u src/scripts/obituary_dp_batch.py rank --tracerfy
    python -u src/scripts/obituary_dp_batch.py research
    python -u src/scripts/obituary_dp_batch.py score
    python -u src/scripts/obituary_dp_batch.py build
    python -u src/scripts/obituary_dp_batch.py schema --commit
    python -u src/scripts/obituary_dp_batch.py push --route api --probe            # ONE record, read back
    python -u src/scripts/obituary_dp_batch.py push --route api --limit 25 --commit
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SKILL_SCRIPTS = ROOT / "skills" / "deep-prospecting-v5" / "scripts"
for p in (SRC, SRC / "scripts", SKILL_SCRIPTS):
    sys.path.insert(0, str(p))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

DEFAULT_RUN_DIR = ROOT / "output" / "dp_hottest_needs_skipped"
TODAY = date.today().isoformat()
MONTH_TAG = date.today().strftime("%m/%Y")

TAG_DP = f"Deep Prospected {MONTH_TAG}"
TAG_DECEASED = "Deceased Owner"          # exists on the account already
TAG_ALIVE = "Owner Alive - Spouse Deceased"
TAG_UNVERIFIED = "DP Death Unverified"
TAG_PRESUMED = "DP Owner Presumed Alive"
TAG_NO_NUMBERS = f"DP No Numbers {MONTH_TAG}"
TAG_TRUST = "DP Trust Trustee Inferred"

# The presumed-alive verdict (FTM cohorts, research --presume-alive-without-signal).
# It deliberately CONTAINS the substring "owner alive" nowhere: score/build test for it
# with "presumed alive" explicitly, so a grep for either phrase finds every gate.
VERDICT_PRESUMED = "owner presumed alive (no deceased signal)"

# What counts as a reason to run obituary research on a record. FTM lanes are mixed:
# probate-sourced records likely have a dead owner, foreclosure-sourced ones a living
# one -- research is only worth its Firecrawl/LLM spend where a signal exists.
DECEASED_SIGNAL_LISTS = ("probate", "obituary")
DECEASED_SIGNAL_TAGS = ("deceased owner", "owner deceased", "obituaries", "deceased")


def deceased_signal(lists: str, tags: str, obit_date: str, pr: str) -> str:
    """Why this record's owner might be dead, or '' if nothing suggests it."""
    ll = (lists or "").lower()
    for w in DECEASED_SIGNAL_LISTS:
        if w in ll:
            return f"list contains {w!r}"
    tl = (tags or "").lower()
    for w in DECEASED_SIGNAL_TAGS:
        if w in tl:
            return f"tag contains {w!r}"
    if (obit_date or "").strip():
        return "SiftMap obituary date set"
    if (pr or "").strip():
        return "personal representative on record"
    return ""
# The account's native IDI-import shape is REL1-5; Basem added two on 2026-08-28 (cap 7) and
# raised it to 15 on 2026-08-31 so that signers and blood relatives always land in a REL field
# rather than in the note. Measured on the first 77 records: 18 of 77 had blood relatives with
# phones that did not fit at 7, none at 15. `schema --commit` creates whatever is missing.
MAX_REL_SLOTS = 15

STATE_NAMES = {"MD": "Maryland", "VA": "Virginia", "DC": "Washington DC",
               "DE": "Delaware", "PA": "Pennsylvania", "WV": "West Virginia"}

# Words that make a Business Name an entity rather than a person. Whole-word test
# (a substring test flags "Ralph" because of "lp"), same list obituary_dp_input uses.
from obituary_dp_input import ENTITY_WORDS, SUFFIXES  # noqa: E402

TRUST_WORDS = {"trust", "tr", "trs", "ttee", "ttees", "trustee", "trustees", "living",
               "revocable", "revoc", "revo", "rev", "irrevocable", "irrev", "amended", "restated",
               "residuary", "family", "fam", "estate", "of", "the", "declaration", "dated",
               "agreement", "u/a", "ua", "dtd", "etal", "et", "al", "life", "marital", "survivor",
               "survivors", "credit", "shelter", "bypass", "qtip", "supplemental", "needs",
               "special", "heritage", "co", "successor", "sole", "joint"}
# A title carrying one of these names a company, not a person: no human to trace.
CORP_WORDS = {"llc", "inc", "corp", "corporation", "properties", "holdings", "company",
              "partners", "lp", "llp", "ltd", "foundation", "church", "bank", "ministries",
              "association", "investments", "group", "enterprises", "realty", "homes"}
ORDINALS = {"2nd", "3rd", "4th", "ii", "iii", "iv", "jr", "sr", "esq", "md", "dds", "phd"}

SPOUSE_RELS = {"Husband", "Wife", "Spouse"}

# A phone tag this pipeline wrote. Nothing outside this pattern is ever removed.
_OURS_RE = re.compile(r"^(Rel\d+\.\d+|Owner\.\d+)$")


def _d10(n) -> str:
    """Last 10 digits -- the account stores some numbers as +1XXXXXXXXXX."""
    return "".join(c for c in str(n or "") if c.isdigit())[-10:]


# ───────────────────────── small helpers ─────────────────────────

def norm_addr(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def zip5(s: str) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[:5]


def sift_key(street: str, zipcode: str) -> str:
    return f"{norm_addr(street)}|{zip5(zipcode)}"


def name_tokens(name: str) -> list[str]:
    toks = [t for t in re.split(r"[^a-z]+", (name or "").lower()) if t]
    return [t for t in toks if t not in SUFFIXES and t not in ORDINALS]


def names_match(a: str, b: str) -> bool:
    """First token + last token match, ignoring middle names, initials, suffixes."""
    ta, tb = name_tokens(a), name_tokens(b)
    ta = [t for t in ta if len(t) > 1] or ta
    tb = [t for t in tb if len(t) > 1] or tb
    if len(ta) < 2 or len(tb) < 2:
        return False
    return ta[0] == tb[0] and ta[-1] == tb[-1]


def jdump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def jload(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict], header: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def say(msg: str) -> None:
    print(msg, flush=True)


# ───────────────────────── name parsing ─────────────────────────

def _gender_known(first: str) -> bool:
    try:
        import gender_guesser.detector as gg
        g = gg.Detector(case_sensitive=False).get_gender(first)
        return g in ("male", "female", "mostly_male", "mostly_female")
    except Exception:
        return False


def _order_tokens(toks: list[str], style_default: bool | None) -> tuple[str, str]:
    """toks are the human tokens of ONE person (initials kept). Return (first, last).

    Order is decided by evidence in this order, because titles mix conventions freely:
      1. a lone initial: "Anderson David F" is LAST FIRST M, "Kevin J Skiffington" is
         FIRST M LAST, and "Sebastian Michael J Rev Trust" is recorder order even though
         it says Trust;
      2. which token reads as a given name ("Ford Ronald" -> Ronald Ford, "Jane Takeuchi
         Udelson" -> Jane Udelson);
      3. the title's style: recorder abbreviations (Tr / Trs / Trustee) run LAST FIRST,
         long-form titles (Revocable Living Trust) run FIRST LAST.
    """
    if not toks:
        return "", ""
    words = [t for t in toks if len(t) > 1]
    if len(words) == 1:
        return "", words[0].title()   # surname only
    recorder_order = None
    if len(toks) >= 3 and len(toks[2]) == 1 and len(toks[1]) > 1:
        recorder_order = True
    elif len(toks) >= 3 and len(toks[1]) == 1 and len(toks[0]) > 1:
        recorder_order = False
    else:
        g0, g1 = _gender_known(words[0]), _gender_known(words[1])
        if g1 and not g0:
            recorder_order = True
        elif g0 and not g1:
            recorder_order = False
    if recorder_order is None:
        recorder_order = bool(style_default)
    if recorder_order:
        return words[1].title(), words[0].title()
    return words[0].title(), words[-1].title()


def parse_owner_blob(biz: str) -> dict:
    """A Business Name string -> {first, last, source, confidence, is_entity}.

    Recorder-style trust titles ("Condon Mark Stephen Tr", "Adams Belle H & Harold B Trs")
    run LAST FIRST M; long-form titles ("Norma Veltri Trust", "Larry And Joanne Stevens
    Living Trust") run FIRST M LAST. The first human named is the trustee we trace; the
    result is LOW confidence by definition, because a trust title names who set the trust
    up, not necessarily who holds title today.
    """
    raw = (biz or "").strip()
    low = " " + re.sub(r"[.,()/]", " ", raw.lower()) + " "
    words = set(re.findall(r"[a-z]+", low))
    is_entity = bool(words & ENTITY_WORDS)
    if not raw:
        return {"first": "", "last": "", "source": "none", "confidence": "none", "is_entity": False}

    if words & CORP_WORDS:
        return {"first": "", "last": "", "source": "entity_company", "confidence": "none",
                "is_entity": True}
    long_form = bool(words & {"trust", "living", "revocable", "irrevocable", "amended",
                              "restated", "residuary", "family", "declaration", "agreement"})
    ends_abbrev = bool(re.search(r"\b(tr|trs|ttee|ttees|trustee|trustees)\s*$", low.strip()))
    # style default, used only when neither an initial nor a recognisable given name decides
    # A long-form title wins over a trailing "Tr": "Christine Hopkins Family Revocable Tr"
    # is FIRST LAST even though it ends in the recorder abbreviation.
    recorder_order = False if long_form else (True if ends_abbrev else None)

    segments = re.split(r"\s+(?:&|and)\s+", low.strip())
    people: list[list[str]] = []
    for seg in segments:
        toks = [t for t in seg.split() if t not in TRUST_WORDS and t not in ORDINALS
                and t not in SUFFIXES and not t.isdigit()]
        if toks:
            people.append(toks)
    if not people:
        return {"first": "", "last": "", "source": "trust_title" if is_entity else "blob",
                "confidence": "none", "is_entity": is_entity}

    # "Larry And Joanne Stevens": a single-token segment borrows the surname of the
    # first multi-token segment.
    surname_pool = next((p for p in people if len([t for t in p if len(t) > 1]) >= 2), None)
    first_person = people[0]
    if len([t for t in first_person if len(t) > 1]) == 1 and surname_pool is not None \
            and surname_pool is not first_person:
        f, l = _order_tokens(surname_pool, recorder_order)
        first, last = first_person[0].title(), l
    else:
        first, last = _order_tokens(first_person, recorder_order)

    if is_entity:
        conf = "surname_only" if not first else "low"
        return {"first": first, "last": last, "source": "trust_title", "confidence": conf,
                "is_entity": True}
    return {"first": first, "last": last, "source": "blob",
            "confidence": "low" if first else "none", "is_entity": False}


# ───────────────────────── prep ─────────────────────────

SS_HEADER = ["First Name", "Last Name", "Mailing Address", "Mailing City", "Mailing State",
             "Mailing Zip", "Property Address", "Property City", "Property State", "Property Zip",
             "Sift Key", "Name Source", "Name Confidence"]


def cmd_prep(a) -> int:
    run = Path(a.run_dir)
    run.mkdir(parents=True, exist_ok=True)
    src = Path(a.export)
    rows = list(csv.DictReader(open(src, newline="", encoding="utf-8-sig")))
    say(f"export: {src.name}  rows={len(rows)}")

    records: dict[str, dict] = {}
    ss_rows, unusable = [], []
    counts = Counter()
    for r in rows:
        key = sift_key(r.get("Property address", ""), r.get("Property zip5") or r.get("Property zip", ""))
        if key in records:
            counts["dup_address"] += 1
            continue
        first, last = (r.get("First Name") or "").strip(), (r.get("Last Name") or "").strip()
        biz = (r.get("Business Name") or "").strip()
        source, conf, is_trust = "owner_fields", "high", False
        if not (first and last):
            parsed = parse_owner_blob(biz)
            first, last = parsed["first"], parsed["last"]
            source, conf, is_trust = parsed["source"], parsed["confidence"], parsed["is_entity"]
        elif biz and parse_owner_blob(biz)["is_entity"]:
            is_trust = True   # person parsed by reisift, trust also on title: trace the person

        rec = {
            "key": key,
            "owner_first": (r.get("First Name") or "").strip(),
            "owner_last": (r.get("Last Name") or "").strip(),
            "business_name": biz,
            "ss_first": first, "ss_last": last,
            "name_source": source, "name_confidence": conf, "is_trust": is_trust,
            "street": (r.get("Property address") or "").strip(),
            "city": (r.get("Property city") or "").strip(),
            "state": (r.get("Property state") or "").strip().upper(),
            "zip": zip5(r.get("Property zip5") or r.get("Property zip", "")),
            "county": (r.get("Property county") or "").strip(),
            "mail_street": (r.get("Mailing address") or "").strip(),
            "mail_city": (r.get("Mailing city") or "").strip(),
            "mail_state": (r.get("Mailing state") or "").strip().upper(),
            "mail_zip": zip5(r.get("Mailing zip5") or r.get("Mailing zip", "")),
            "lists": (r.get("Lists") or "").strip(),
            "tags": (r.get("Tags") or "").strip(),
            "obit_date": (r.get("Last obituary date") or "").strip(),
            "pr": (r.get("Personal representative") or "").strip(),
            "est_value": (r.get("Estimated value") or "").strip(),
            "equity_pct": (r.get("Equity percent") or "").strip(),
            "owned_since": (r.get("Owned since") or "").strip(),
            "year_built": (r.get("Year") or "").strip(),
            "apn": (r.get("Apn") or "").strip(),
        }
        records[key] = rec
        if not (first and last):
            reason = ("company on title, no human to trace" if source == "entity_company" else
                      "trust title names no first name (deed lookup needed)" if conf == "surname_only" else
                      "no parseable first and last name")
            unusable.append({**rec, "reason": reason})
            counts["unusable"] += 1
            continue
        counts["trust_parsed" if source == "trust_title" else
               "blob_parsed" if source == "blob" else "person"] += 1
        ss_rows.append({
            "First Name": first, "Last Name": last,
            "Mailing Address": rec["mail_street"] or rec["street"],
            "Mailing City": rec["mail_city"] or rec["city"],
            "Mailing State": rec["mail_state"] or rec["state"],
            "Mailing Zip": rec["mail_zip"] or rec["zip"],
            "Property Address": rec["street"], "Property City": rec["city"],
            "Property State": rec["state"], "Property Zip": rec["zip"],
            "Sift Key": key, "Name Source": source, "Name Confidence": conf,
        })

    jdump(run / "records.json", records)
    write_csv(run / "smartskip_input.csv", ss_rows, SS_HEADER)
    write_csv(run / "unusable.csv", unusable, list(unusable[0].keys()) if unusable else ["key", "reason"])

    pilot_rows = []
    if a.pilot:
        # Stratify across counties, and make sure at least one trust row rides along so
        # the trustee path is exercised before the full order.
        by_county: dict[str, list] = {}
        for s in ss_rows:
            by_county.setdefault(records[s["Sift Key"]]["county"], []).append(s)
        trust = next((s for s in ss_rows if s["Name Source"] == "trust_title"
                      and s["Name Confidence"] == "low"), None)
        if trust:
            pilot_rows.append(trust)
        i = 0
        while len(pilot_rows) < a.pilot:
            progressed = False
            for cty in sorted(by_county):
                lst = by_county[cty]
                if i < len(lst) and len(pilot_rows) < a.pilot:
                    s = lst[i]
                    if s not in pilot_rows and s["Name Source"] == "owner_fields":
                        pilot_rows.append(s)
                    progressed = True
            i += 1
            if not progressed:
                break
        write_csv(run / "smartskip_input_pilot.csv", pilot_rows, SS_HEADER)

    audit = {
        "export": str(src), "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows_in": len(rows), "records": len(records), "counts": dict(counts),
        "smartskip_rows": len(ss_rows), "pilot_rows": len(pilot_rows),
        "estimated_cost_full": round(0.15 * len(ss_rows), 2),
        "estimated_cost_pilot": round(0.15 * len(pilot_rows), 2),
        "trust_parses": [{"business_name": r["business_name"], "first": r["ss_first"],
                          "last": r["ss_last"], "confidence": r["name_confidence"]}
                         for r in records.values() if r["name_source"] == "trust_title"],
        "blob_parses": [{"business_name": r["business_name"], "first": r["ss_first"],
                         "last": r["ss_last"]} for r in records.values() if r["name_source"] == "blob"],
    }
    jdump(run / "prep_audit.json", audit)
    say(f"records={len(records)}  {dict(counts)}")
    say(f"smartskip_input.csv: {len(ss_rows)} rows (~${audit['estimated_cost_full']})"
        + (f"   pilot: {len(pilot_rows)} rows (~${audit['estimated_cost_pilot']})" if pilot_rows else ""))
    say("trust parses (first 12):")
    for t in audit["trust_parses"][:12]:
        say(f"   {t['business_name']!r:55} -> {t['first']} {t['last']}  [{t['confidence']}]")
    assert len(records) + counts["dup_address"] == len(rows)
    return 0


# ───────────────────────── trace ─────────────────────────

def _smartskip(run: Path):
    os.environ["SKIPTRACE_RUN_DIR"] = str(run)
    import importlib
    if "smartskip_trace" in sys.modules:
        return importlib.reload(sys.modules["smartskip_trace"])
    return importlib.import_module("smartskip_trace")


def cmd_trace(a) -> int:
    run = Path(a.run_dir)
    ss = _smartskip(run)
    sess = ss.Session()

    if a.download_id:
        out = run / f"smartskip_vertical_{a.download_id}.csv"
        ss.download(sess, a.download_id, str(out), "vertical")
        return 0

    if a.status:
        for i in ss.fetch_status(sess, a.bid):
            say(json.dumps(i, indent=1))
        return 0

    if a.pay_id:
        bid = a.pay_id
        orders = {o["bulkSkipId"]: o for o in ss.load_orders()}
        ent = (orders.get(bid) or {}).get("entities")
        say(f"paying existing order {bid} (entities={ent}, ~${0.15 * (ent or 0):.2f})")
        if a.max_entities and ent and ent > a.max_entities:
            say(f"REFUSING: entities {ent} > --max-entities {a.max_entities}")
            return 2
        if not (orders.get(bid) or {}).get("paid"):
            if not ss.pay(sess, bid, None):
                say("payment did not complete; see message above")
                return 3
        else:
            say("already marked paid; polling")
        t0 = time.time()
        while time.time() - t0 < a.wait:
            items = ss.fetch_status(sess, bid)
            st = ((items[0].get("status") if items else "?") or "?")
            say(f"  status={st}  ({int(time.time() - t0)}s)")
            if st.lower() == "completed":
                out = run / f"smartskip_vertical_{bid}.csv"
                ss.download(sess, bid, str(out), "vertical")
                return 0
            if st.lower() in ("error", "failed"):
                say(f"ERROR: bulk skip ended in status {st}")
                return 4
            time.sleep(15)
        say(f"TIMEOUT: re-run  trace --download-id {bid}")
        return 5

    csv_path = Path(a.csv)
    if not csv_path.is_absolute():
        csv_path = run / csv_path
    if not csv_path.exists():
        say(f"ERROR: {csv_path} not found")
        return 1
    n_rows = sum(1 for _ in csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    say(f"submitting {csv_path.name} ({n_rows} rows) -- upload + map + calculate are FREE")
    entry = ss.submit(sess, str(csv_path), {})
    bid, entities = entry["bulkSkipId"], entry.get("entities") or 0
    say(f"bulkSkipId={bid} entities={entities} duplicates={entry.get('duplicates')}  "
        f"bill if paid: ~${0.15 * entities:.2f}")
    if not a.pay:
        say("Not paid (no --pay). Re-run with --pay to bill the card.")
        return 0
    if a.max_entities and entities > a.max_entities:
        say(f"REFUSING to pay: entities {entities} > --max-entities {a.max_entities}")
        return 2
    if not ss.pay(sess, bid, None):
        say("payment did not complete (3DS or card issue); see message above")
        return 3
    t0 = time.time()
    while time.time() - t0 < a.wait:
        items = ss.fetch_status(sess, bid)
        st = ((items[0].get("status") if items else "?") or "?")
        say(f"  status={st}  ({int(time.time() - t0)}s)")
        if st.lower() == "completed":
            out = run / f"smartskip_vertical_{bid}.csv"
            ss.download(sess, bid, str(out), "vertical")
            return 0
        if st.lower() in ("error", "failed"):
            say(f"ERROR: bulk skip ended in status {st}")
            return 4
        time.sleep(15)
    say(f"TIMEOUT: re-run  trace --download-id {bid}")
    return 5


# ───────────────────────── rank ─────────────────────────

def _phone_sort_key(p: dict) -> tuple:
    t = (p.get("type") or "").lower()   # SmartSkip: Mobile / Residential / OtherPhone
    return (0 if "mobile" in t or "cell" in t or "wireless" in t else
            1 if "land" in t or "residential" in t else 2, p.get("number", ""))


PARENT_RELS = {"Mother", "Father", "Parent"}
CHILD_RELS = {"Son", "Daughter", "Child"}
SIB_RELS = {"Brother", "Sister", "Sibling"}


def _reroot_namesake(rec: dict, crm: dict) -> None:
    """SmartSkip resolved a NAMESAKE CHILD at the address instead of the owner.

    Seen on 5 of the first 25 pilot records: the input owner "Edith Sample" (owned since
    1965) came back as a 61-year-old subject whose "Mother" is EDITH SAMPLE, 96. The
    Possible Type labels are all relative to the wrong person, so "parents take" would
    hand the estate to the decedent herself. When a Parent carries the OWNER's name, the
    graph is re-rooted on that parent: the subject becomes a Child (a signer), the other
    Parent becomes the Spouse, the subject's siblings become Children, the subject's
    spouse becomes an In-Law and the subject's children become generic relatives. The
    same-name parent is removed from the relatives (they are the decedent) and recorded
    under rec["decedent_from_smartskip"] so the note can say so.
    """
    owner = f"{crm.get('ss_first', '')} {crm.get('ss_last', '')}".strip()
    rels = rec.get("relatives") or []
    same = [r for r in rels if r.get("canon_rel") in PARENT_RELS and names_match(r.get("name", ""), owner)]
    if not same:
        # Second signal: the subject cannot have bought the house before turning 18.
        try:
            bought = int((crm.get("owned_since") or "")[:4])
            sa = int(rec.get("subject_age") or 0)
            if sa and bought and (date.today().year - sa) + 18 > bought:
                rec["subject_age_inconsistent"] = f"subject age {sa} vs owned since {bought}"
        except ValueError:
            pass
        return
    decedent = same[0]
    sfirst = (rec.get("first") or "").strip() or "Unknown"   # SmartSkip left it blank; do not guess
    subj = {"first": sfirst, "last": rec.get("last"),
            "name": f"{sfirst} {rec.get('last', '')}".strip(),
            "type_raw": "Subject (namesake child)", "canon_rel": "Child",
            "gender": "?", "age": str(rec.get("subject_age") or ""),
            "mailing_street": rec.get("mailing_address"), "mailing_city": rec.get("mailing_city"),
            "mailing_state": rec.get("mailing_state"), "mailing_zip": rec.get("mailing_zip"),
            "phones": rec.get("subject_phones") or [], "deceased": False, "rerooted": "subject -> Child"}
    out = [subj]
    for r in rels:
        if r is decedent:
            continue
        c = r.get("canon_rel")
        new = c
        if c in PARENT_RELS:
            new = "Spouse"
        elif c in SIB_RELS:
            new = "Child"
        elif c in ("Husband", "Wife", "Spouse"):
            new = "In-Law"
        elif c in CHILD_RELS:
            new = "Relative"          # grandchildren of the decedent
        if new != c:
            r["rerooted"] = f"{c} -> {new}"
        r["canon_rel"] = new
        out.append(r)
    rec["relatives"] = out
    rec["subject_phones"] = []          # the subject is now REL, not the owner line
    # The age-sanity check that follows must measure generations from the DECEDENT now.
    rec["namesake_subject_age"] = rec.get("subject_age")
    try:
        rec["subject_age"] = int(str(decedent.get("age") or "").strip()) or rec.get("subject_age")
    except ValueError:
        pass
    rec["decedent_from_smartskip"] = {"name": decedent.get("name"), "age": decedent.get("age"),
                                      "phones": decedent.get("phones") or [],
                                      "deceased_flag": decedent.get("deceased")}
    rec["rerooted"] = True


def _dedupe_relatives(rec: dict) -> int:
    """Collapse repeated people in the relatives graph, merging their phones.

    SmartSkip returns some relatives on more than one row (the same person at two
    addresses). Nothing downstream deduped them, so one person could occupy two REL
    slots -- 101 of 619 records on this batch -- wasting the scarcest field on the
    record and listing the same human twice on the dial sheet.

    The survivor is the entry rank_record already chose as a signer, else the
    higher-scoring one, so `dms` object identity (which the caller keys on with id())
    is never broken.
    """
    ranked = rec.get("ranked") or []
    dm_ids = {id(x) for x in (rec.get("dms") or [])}
    keep: dict[str, dict] = {}
    order: list[dict] = []
    dropped = 0
    for x in ranked:
        k = " ".join(name_tokens(x.get("name") or "")).lower()
        if not k:
            order.append(x)
            continue
        prev = keep.get(k)
        if prev is None:
            keep[k] = x
            order.append(x)
            continue
        # Decide which object survives: a signer always wins, then the better score.
        if id(x) in dm_ids and id(prev) not in dm_ids:
            keep[k] = x
            order[order.index(prev)] = x
            winner, loser = x, prev
        else:
            winner, loser = prev, x
        have = {p.get("number") for p in (winner.get("phones") or [])}
        for ph in loser.get("phones") or []:
            if ph.get("number") not in have:
                winner.setdefault("phones", []).append(ph)
                have.add(ph.get("number"))
        if not winner.get("age") and loser.get("age"):
            winner["age"] = loser["age"]
        dropped += 1
    if dropped:
        rec["ranked"] = order
        rec["dms"] = [x for x in order if id(x) in dm_ids]
        rec["duplicate_relatives_merged"] = dropped
    return dropped


def cmd_rank(a) -> int:
    run = Path(a.run_dir)
    from parse_smartskip import parse  # noqa: E402
    import obituary_dp_run as dpr  # noqa: E402

    records = jload(run / "records.json", {})
    exports = sorted(run.glob("smartskip_vertical*.csv"))
    if not exports:
        say("no smartskip_vertical*.csv in the run dir; run `trace` first")
        return 1

    by_key: dict[str, dict] = {}
    by_name: dict[str, str] = {}
    for k, r in records.items():
        by_name.setdefault(f"{r['ss_first']} {r['ss_last']}".lower(), k)

    ranked: dict[str, dict] = jload(run / "ranked_records.json", {}) if a.keep else {}
    unmatched = []
    seen_rows = 0
    relabels = Counter()
    for ex in exports:
        # parse() drops the SUBJECT's age; read it back off the raw rows so the relationship
        # labels can be sanity-checked against it.
        subj_age = {}
        for row in csv.DictReader(open(ex, newline="", encoding="utf-8-sig")):
            if row.get("Relationship") == "Subject":
                try:
                    subj_age[row["Input Name"]] = int(str(row.get("Age") or "").strip())
                except ValueError:
                    pass
        for rec in parse(str(ex)):
            seen_rows += 1
            key = sift_key(rec.get("property_address", ""), rec.get("property_zip", ""))
            if key not in records:
                key = by_name.get((rec.get("input_name") or "").lower(), "")
            if key not in records:
                unmatched.append(rec.get("input_name"))
                continue
            rec["subject_age"] = subj_age.get(rec.get("input_name"))
            _reroot_namesake(rec, records[key])
            # SmartSkip's Possible Type is coarse and sometimes inverted: a "Father" who is
            # younger than an 85-year-old decedent is not his father. A generation is at
            # least ~12 years; anything closer is demoted to a generic Relative so the
            # intestacy ranking does not hand the estate to the wrong person.
            sa = rec["subject_age"]
            if sa:
                for rel in rec.get("relatives") or []:
                    try:
                        ra = int(str(rel.get("age") or "").strip())
                    except ValueError:
                        continue
                    c = rel.get("canon_rel")
                    if c in ("Mother", "Father", "Parent") and ra < sa + 12:
                        rel["canon_rel"], rel["relabel"] = "Relative", f"{c} but only {ra} vs subject {sa}"
                        relabels["parent->relative"] += 1
                    elif c in ("Son", "Daughter", "Child") and ra > sa - 12:
                        rel["canon_rel"], rel["relabel"] = "Relative", f"{c} but {ra} vs subject {sa}"
                        relabels["child->relative"] += 1
            dpr.rank_record(rec, max_generic=a.max_generic, min_score=a.min_score)
            for rel in rec.get("relatives") or []:
                rel["phones"] = sorted(rel.get("phones") or [], key=_phone_sort_key)
            by_key[key] = rec

    # Tracerfy gap-fill: only the SIGNERS SmartSkip left phoneless.
    gap_stats = {}
    if a.tracerfy:
        api_key = os.environ.get("TRACERFY_API_KEY", "")
        targets = []
        for key, rec in by_key.items():
            for d in rec.get("dms") or []:
                if not d.get("phones"):
                    d.setdefault("mailing_state", records[key]["state"])
                    targets.append(d)
        say(f"tracerfy gap-fill: {len(targets)} signers without a phone")
        if targets:
            found, gap_stats = dpr.tracerfy_gapfill(targets, api_key)
            for d in targets:
                hit = found.get(f"{d.get('first', '')} {d.get('last', '')}".strip().lower())
                if hit:
                    d["phones"] = [{"number": n, "type": hit["types"].get(n, "UNKNOWN"),
                                    "source": "tracerfy"} for n in hit["numbers"]]
                    d["tracerfy"] = True
            say(f"  {gap_stats}")

    # REL slots, in the order obituary_dp_run.rank_record intends: signers (by intestacy),
    # then the rest of the blood/unknown relatives, then in-laws LAST as dial channels.
    #
    # rank_record keeps in-laws deliberately -- "the way you reach a daughter who does not
    # answer is often her husband" -- but they are never signers, so they must not displace
    # blood kin out of the REL fields. `--max-rels` bounds the blood tier; `--channel-cap`
    # bounds the in-law tail, which is appended after it.
    #
    # Only In-Law is capped, never the generic "Relative" bucket: 63% of SmartSkip labels come
    # back generic and that is where mislabeled blood kin land (it called a husband of 62 years
    # a plain "Relative"), so capping generics would drop real signers.
    max_n = 0
    stats = Counter()
    for key, rec in by_key.items():
        _dedupe_relatives(rec)
        dms = rec.get("dms") or []
        dm_ids = {id(x) for x in dms}
        others = [x for x in (rec.get("ranked") or []) if id(x) not in dm_ids and not x.get("deceased")]
        # An in-law that rank_record put in dms stays a signer; the cap never touches it.
        blood = [x for x in others if x.get("canon_rel") != "In-Law"]
        inlaws = [x for x in others if x.get("canon_rel") == "In-Law"]
        blood.sort(key=lambda x: (0 if x.get("phones") else 1, -(x.get("dm_score") or 0)))
        inlaws.sort(key=lambda x: (0 if x.get("phones") else 1, -(x.get("dm_score") or 0)))
        blood = blood[: a.max_rels]
        channels = inlaws[: a.channel_cap]
        chan_ids = {id(x) for x in channels}
        # Keep enough of a capped in-law to stay a candidate for the spouse-obituary trap
        # scan in `research`, which matches on surname + mailing street.
        channels_dropped = [{"name": x.get("name"), "canon_rel": x.get("canon_rel"),
                             "age": x.get("age"), "n_phones": len(x.get("phones") or []),
                             "first": x.get("first"), "last": x.get("last"),
                             "mailing_street": x.get("mailing_street"), "phones": []}
                            for x in inlaws[a.channel_cap:]]
        ordered = dms + blood + channels
        rels = []
        for n, x in enumerate(ordered, 1):
            rels.append({
                "is_channel": id(x) in chan_ids,
                "n": n, "name": x.get("name"), "first": x.get("first"), "last": x.get("last"),
                "canon_rel": x.get("canon_rel"), "type_raw": x.get("type_raw"), "age": x.get("age"),
                "relabel": x.get("relabel"), "rerooted": x.get("rerooted"),
                "is_dm": id(x) in dm_ids, "dm_score": x.get("dm_score"), "dm_why": x.get("dm_why"),
                "mailing": " ".join(s for s in [x.get("mailing_street"), x.get("mailing_city"),
                                                x.get("mailing_state"), x.get("mailing_zip")] if s),
                "mailing_street": x.get("mailing_street"), "mailing_city": x.get("mailing_city"),
                "mailing_state": x.get("mailing_state"), "mailing_zip": x.get("mailing_zip"),
                "phones": x.get("phones") or [], "tracerfy": bool(x.get("tracerfy")),
            })
        dropped = max(0, len(others) - len(blood) - len(channels) - len(channels_dropped))
        n_ph = sum(len(r["phones"]) for r in rels)
        max_n = max(max_n, len(rels))
        stats["records"] += 1
        stats["rerooted_namesake"] += 1 if rec.get("rerooted") else 0
        stats["subject_age_inconsistent"] += 1 if rec.get("subject_age_inconsistent") else 0
        stats["with_relatives"] += 1 if rels else 0
        stats["with_phones"] += 1 if n_ph else 0
        stats["relatives"] += len(rels)
        stats["duplicate_relatives_merged"] += rec.get("duplicate_relatives_merged") or 0
        stats["channels"] += len(channels)
        stats["channels_dropped"] += len(channels_dropped)
        stats["phones"] += n_ph
        ranked[key] = {
            "key": key, "input_name": rec.get("input_name"),
            "subject": {"first": rec.get("first"), "last": rec.get("last"), "age": rec.get("subject_age"),
                        "deceased_flag": rec.get("deceased"),
                        "phones": sorted(rec.get("subject_phones") or [], key=_phone_sort_key)},
            "signer_basis": rec.get("signer_basis"), "rels": rels,
            "channels_dropped": channels_dropped,
            "rerooted": bool(rec.get("rerooted")),
            "decedent_from_smartskip": rec.get("decedent_from_smartskip"),
            "subject_age_inconsistent": rec.get("subject_age_inconsistent"),
            "rels_dropped_over_cap": dropped, "has_results": rec.get("has_results"),
            "n_phones": n_ph,
        }

    jdump(run / "ranked_records.json", ranked)
    jdump(run / "rank_audit.json", {"generated_at": datetime.now().isoformat(timespec="seconds"),
                                    "exports": [e.name for e in exports], "smartskip_rows": seen_rows,
                                    "unmatched_input_names": unmatched, "stats": dict(stats),
                                    "max_rel_slots": max_n, "tracerfy": gap_stats})
    say(f"age-sanity relabels: {dict(relabels)}")
    say(f"ranked {stats['records']} records: {stats['with_relatives']} with relatives, "
        f"{stats['with_phones']} with at least one phone, {stats['relatives']} relatives, "
        f"{stats['phones']} phones.  MAX REL SLOTS NEEDED = {max_n}")
    if unmatched:
        say(f"WARNING {len(unmatched)} SmartSkip rows did not match a record: {unmatched[:5]}")
    say("Signer share split differs by state (MD/DC/VA intestacy), ranking order does not.")
    return 0


# ───────────────────────── research ─────────────────────────

def _firecrawl_search(query: str, limit: int = 8) -> list[dict]:
    """Firecrawl /v1/search as the fallback when DDGS is rate-limited."""
    import config as cfg  # noqa: E402
    if not cfg.FIRECRAWL_API_KEY:
        return [{"error": "no FIRECRAWL_API_KEY", "query": query}]
    body = json.dumps({"query": query, "limit": limit}).encode()
    req = urllib.request.Request("https://api.firecrawl.dev/v1/search", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {cfg.FIRECRAWL_API_KEY}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return [{"error": f"firecrawl {e.code}: {e.read()[:120]!r}", "query": query}]
    except Exception as e:  # noqa: BLE001
        return [{"error": f"firecrawl {type(e).__name__}: {str(e)[:120]}", "query": query}]
    items = data.get("data") or []
    return [{"href": it.get("url", ""), "title": it.get("title", ""), "body": it.get("description", "")}
            for it in items if isinstance(it, dict)]


_ddgs_dead_until = 0.0


def _search_obit(name: str, city: str, state: str) -> list[dict]:
    """Obituary web search: DDGS (free) with retries, Firecrawl search when DDGS is throttled.

    DDGS answered the first handful of pilot searches and then returned "No results found."
    for every query -- a rate limit that reads exactly like a person with no obituary. A
    search failure is therefore reported as {"error": ...} (never as an empty list) so the
    caller can refuse to cache the verdict.
    """
    global _ddgs_dead_until
    from ddgs import DDGS  # noqa: E402
    from obituary_enricher import _is_obituary_url  # noqa: E402
    sname = STATE_NAMES.get(state, state)
    query = f'"{name}" obituary {city} {sname}'.strip()
    results, err = None, ""
    if time.time() >= _ddgs_dead_until:
        for attempt, backend in enumerate(("google,duckduckgo,brave", "duckduckgo,bing", "brave,google")):
            try:
                results = DDGS().text(query, max_results=8, backend=backend) or []
                if results:
                    break
                err = "DDGS empty"
            except Exception as e:  # noqa: BLE001
                err = str(e)[:160]
            time.sleep(3 + 3 * attempt)
        if not results:
            _ddgs_dead_until = time.time() + 600      # rest DDGS for 10 minutes, use Firecrawl
    if not results:
        fc = _firecrawl_search(query)
        if fc and fc[0].get("error"):
            return [{"error": f"ddgs: {err} | {fc[0]['error']}", "query": query}]
        results = fc
        if not results:
            return [{"error": f"ddgs: {err} | firecrawl: 0 results", "query": query}]
    out = []
    for r in results:
        url = r.get("href", "")
        title, body = (r.get("title") or ""), (r.get("body") or "")
        low = (title + " " + body).lower()
        if _is_obituary_url(url) or any(w in low for w in ("obituar", "passed away", "death notice", "funeral")):
            out.append({"url": url, "title": title, "snippet": body})
    time.sleep(1.5)
    return out[:6]


def _llm_match(text: str, owner_name: str, city: str, address: str, state: str) -> dict | None:
    import config as cfg  # noqa: E402
    import llm_client  # noqa: E402
    from obituary_enricher import (OBITUARY_PROMPT, SYSTEM_PROMPT, MAX_OBITUARY_TEXT,  # noqa: E402
                                   MAX_TOKENS, _obituary_model, _validate_survivors_against_text)
    if not text or len(text.strip()) < 100:
        return None
    sname = STATE_NAMES.get(state, state)
    prompt = OBITUARY_PROMPT.replace("Tennessee", sname).format(
        owner_name=owner_name, city=city or "unknown", address=address or "unknown",
        obituary_text=text[:MAX_OBITUARY_TEXT])
    try:
        parsed = llm_client.chat_json(prompt, system=SYSTEM_PROMPT, max_tokens=MAX_TOKENS,
                                      api_key=cfg.ANTHROPIC_API_KEY, model=_obituary_model())
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}
    # llm_client._chat_anthropic swallows EVERY exception and returns None (llm_client.py:94),
    # so the except above never fires -- an exhausted API credit balance arrived here as a
    # plain None and was indistinguishable from "no obituary matched". On 2026-08-31 that
    # froze 52 records as false "unresolved" before it was caught. A None is always a
    # failure (a genuine non-match returns a dict with match=false), so report it as one
    # and let the caller's guard refuse to cache the verdict.
    if parsed is None:
        return {"error": "LLM returned nothing (API failure, no key, or unparseable JSON) "
                         "- see the llm_client log line above"}
    if not parsed.get("match") or not parsed.get("full_name"):
        return None
    deceased = parsed.get("full_name") or owner_name
    parsed["survivors"] = _validate_survivors_against_text(parsed.get("survivors", []), text, deceased)
    if parsed.get("preceded_in_death"):
        parsed["preceded_in_death"] = _validate_survivors_against_text(
            parsed.get("preceded_in_death", []), text, deceased)
    return parsed


def _try_person(name: str, rec: dict, max_fetch: int) -> tuple[dict | None, list]:
    """Search + fetch + LLM for one candidate decedent. Returns (parsed, trail)."""
    from obituary_enricher import _fetch_page_text  # noqa: E402
    hits = _search_obit(name, rec["city"], rec["state"])
    trail = [{"name": name, "hits": hits}]
    if hits and hits[0].get("error"):
        return None, trail
    tried = 0
    for h in hits:
        if tried >= max_fetch:
            break
        url = h.get("url", "")
        if not url or url.lower().endswith(".pdf") or "/search?" in url:
            continue
        text = _fetch_page_text(url)
        if len(text or "") < 100:
            # legacy.com and friends render client-side: plain fetch returns nothing, and a
            # nothing must not spend one of the three page slots.
            from obituary_enricher import _fetch_firecrawl  # noqa: E402
            text = _fetch_firecrawl(url) or ""
        if len(text or "") < 100:
            trail.append({"url": url, "chars": len(text or ""), "match": False, "error": None, "skipped": "empty"})
            continue
        tried += 1
        parsed = _llm_match(text, name, rec["city"], rec["street"], rec["state"])
        trail.append({"url": url, "chars": len(text or ""), "match": bool(parsed and not parsed.get("error")),
                      "error": (parsed or {}).get("error")})
        if parsed and not parsed.get("error"):
            parsed["obituary_url"] = url
            return parsed, trail
        time.sleep(0.5)
    return None, trail


def _name_from_hit(hit: dict, surname: str) -> str:
    """'https://.../obituaries/jane-ann-sample-21' or 'Jane Ann Sample Obituary' -> 'Jane Sample'."""
    sur = (surname or "").lower()
    if not sur:
        return ""
    title = (hit.get("title") or "")
    m = re.search(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]*\.?)*\s+" + re.escape(surname.title()) + r")\b", title)
    if m:
        toks = m.group(1).split()
        return f"{toks[0]} {toks[-1]}"
    # Walk the path from the end: the name slug may sit before a numeric id segment
    # ("/memorials/jane-sample/5728088/") or carry one ("mary-jones-obituary?id=618").
    path = (hit.get("url") or "").lower().split("?")[0].rstrip("/")
    for seg in reversed(path.split("/")):
        seg = re.sub(r"[-_]?\d+.*$", "", seg)
        parts = [t for t in re.split(r"[-_]", seg) if t]
        if sur not in parts or len(parts) < 2:
            continue
        i = parts.index(sur)
        if i == 0:
            continue
        first = parts[0] if parts[0] not in ("obituary", "obituaries", "name", "memorial") else ""
        if first and len(first) > 1:
            return f"{first.title()} {surname.title()}"
    return ""


def _survivor_name(s) -> str:
    if isinstance(s, dict):
        return (s.get("name") or "").strip()
    return str(s or "").strip()


def cmd_research(a) -> int:
    run = Path(a.run_dir)
    records = jload(run / "records.json", {})
    ranked = jload(run / "ranked_records.json", {})
    cache_dir = run / "research_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    keys = [k for k in ranked if k in records]
    if a.key:
        keys = [k for k in keys if k == a.key]
    if a.limit:
        keys = keys[: a.limit]
    say(f"research: {len(keys)} records (cache: {sum(1 for k in keys if (cache_dir / (k.replace('|', '_') + '.json')).exists())} cached)")

    findings = []
    verdicts = Counter()
    # "A run that succeeds with zero data found is worse than one that fails loudly."
    # A dead search or LLM backend produces a long run of uncacheable unresolveds; stop
    # rather than spend hours and Firecrawl credits writing nothing.
    consecutive_errors = 0
    for i, key in enumerate(keys, 1):
        rec, rk = records[key], ranked[key]
        cpath = cache_dir / (key.replace("|", "_") + ".json")
        if cpath.exists() and not a.force:
            f = jload(cpath)
        elif a.presume_alive_without_signal and not deceased_signal(
                rec.get("lists", ""), rec.get("tags", ""),
                rec.get("obit_date", ""), rec.get("pr", "")):
            # FTM-style cohort: nothing on the record suggests a death, so the owner is
            # presumed alive and the Firecrawl/LLM spend is skipped. The verdict makes
            # score/build collect the OWNER'S own numbers (the person to call), which the
            # obituary verdicts only do after research proves the owner alive.
            flags = ["presumed alive, not researched"]
            if rec.get("is_trust"):
                flags.append("trust - trustee inferred")
            if rec.get("name_confidence") in ("low", "surname_only", "check"):
                flags.append(f"name_confidence {rec['name_confidence']}")
            f = {"record": key,
                 "owner_name": f"{rec['ss_first']} {rec['ss_last']}".strip(),
                 "searched": [], "verdict": VERDICT_PRESUMED,
                 "evidence": "no deceased signal on the record (lists, tags, obituary "
                             "date and PR all quiet); obituary research skipped",
                 "confirmed_rels": [], "obit_only_names": [], "drop_names": [],
                 "flags": flags,
                 "researched_at": datetime.now().isoformat(timespec="seconds")}
            consecutive_errors = 0
            jdump(cpath, f)
        else:
            owner_name = f"{rec['ss_first']} {rec['ss_last']}".strip()
            f = {"record": key, "owner_name": owner_name, "searched": []}
            parsed, trail = _try_person(owner_name, rec, a.max_fetch)
            f["searched"].append({"who": "owner", "trail": trail})
            if parsed:
                f["verdict"] = "owner did die"
                f["decedent_name"] = parsed.get("full_name")
                f["dod"] = parsed.get("date_of_death") or ""
                f["dod_source"] = "obituary" if f["dod"] else ""
                f["obituary_url"] = parsed.get("obituary_url")
                f["confidence"] = parsed.get("confidence")
                f["survivors"] = parsed.get("survivors") or []
                f["preceded"] = parsed.get("preceded_in_death") or []
                f["executor_named"] = parsed.get("executor_named") or ""
                f["evidence"] = f"obituary names {parsed.get('full_name')} ({parsed.get('obituary_url')})"
            else:
                # Spouse-obituary trap: the obituary SiftMap saw may be the spouse's.
                # In-laws capped out of the REL slots are still candidates here: the trap
                # is about who the obituary is FOR, not about who gets a phone field.
                pool = list(rk["rels"]) + list(rk.get("channels_dropped") or [])
                cands = [r for r in pool if r.get("canon_rel") in SPOUSE_RELS]
                cands += [r for r in pool if r not in cands and (r.get("last") or "").lower()
                          == rec["ss_last"].lower() and r.get("mailing_street")
                          and norm_addr(r["mailing_street"]) == norm_addr(rec["street"])]
                cands = [r for r in cands if (r.get("first") or "").lower() not in ("", "unknown")]
                verdict = "unresolved"
                for cand in cands[: a.max_spouse]:
                    p2, trail2 = _try_person(cand["name"], rec, a.max_fetch)
                    f["searched"].append({"who": f"relative:{cand['canon_rel']}", "name": cand["name"], "trail": trail2})
                    if p2:
                        rel = (cand.get("canon_rel") or "relative").lower()
                        if cand.get("canon_rel") in SPOUSE_RELS:
                            verdict = f"{rel} died; owner alive"
                        elif cand.get("canon_rel") in ("Mother", "Father", "Parent"):
                            verdict = "parent died; owner alive"
                        else:
                            # canon_rel is often literally "Relative", which read as
                            # "relative (relative) died" on the record.
                            verdict = ("a relative died; owner status unknown" if rel == "relative"
                                       else f"relative ({rel}) died; owner status unknown")
                        f["decedent_name"] = p2.get("full_name")
                        f["dod"] = p2.get("date_of_death") or ""
                        f["dod_source"] = "obituary" if f["dod"] else ""
                        f["obituary_url"] = p2.get("obituary_url")
                        f["confidence"] = p2.get("confidence")
                        f["survivors"] = p2.get("survivors") or []
                        f["preceded"] = p2.get("preceded_in_death") or []
                        f["evidence"] = (f"obituary is for {p2.get('full_name')} ({cand['canon_rel']} of the owner), "
                                         f"{p2.get('obituary_url')}")
                        break
                if verdict == "unresolved":
                    # The owner's own search often surfaces a SAME-SURNAME obituary in the
                    # same town (Sample -> "Jane Ann Sample, Middletown"). That is the
                    # spouse-obituary trap in the wild: fetch it and name who actually died.
                    owner_hits = f["searched"][0]["trail"][0].get("hits") or []
                    for h in owner_hits[:6]:
                        cand = _name_from_hit(h, rec["ss_last"])
                        if not cand or names_match(cand, owner_name):
                            continue
                        p3, trail3 = _try_person(cand, rec, 1)
                        f["searched"].append({"who": "same-surname hit", "name": cand, "trail": trail3})
                        if p3 and names_match(p3.get("full_name") or "", cand):
                            verdict = f"relative ({cand}) died; owner status unknown"
                            f["decedent_name"] = p3.get("full_name")
                            f["dod"] = p3.get("date_of_death") or ""
                            f["dod_source"] = "obituary" if f["dod"] else ""
                            f["obituary_url"] = p3.get("obituary_url")
                            f["confidence"] = p3.get("confidence")
                            f["survivors"] = p3.get("survivors") or []
                            f["preceded"] = p3.get("preceded_in_death") or []
                            f["evidence"] = (f"same-surname obituary in {rec['city']}: {p3.get('full_name')} "
                                             f"({p3.get('obituary_url')}); the OWNER may be a survivor")
                            break
                f["verdict"] = verdict
                if verdict == "unresolved":
                    f["evidence"] = "no obituary matched the owner or a spouse candidate"
            if not f.get("dod") and rec.get("obit_date"):
                f["dod"] = rec["obit_date"]
                f["dod_source"] = "siftmap_last_obituary_date"
            # Cross-check the obituary's people against SmartSkip's relatives.
            rel_names = [r["name"] for r in rk["rels"]]
            surv = [_survivor_name(s) for s in f.get("survivors") or []]
            f["confirmed_rels"] = [n for n in rel_names if any(names_match(n, s) for s in surv)]
            f["obit_only_names"] = [s for s in surv if s and not any(names_match(s, n) for n in rel_names)]
            # dict.fromkeys: names_match is fuzzy and matched the same person repeatedly.
            f["drop_names"] = list(dict.fromkeys(
                n for n in rel_names
                if any(names_match(n, _survivor_name(p)) for p in f.get("preceded") or [])))
            flags = []
            try:
                if f.get("dod") and (date.today() - date.fromisoformat(f["dod"][:10])).days < 90:
                    flags.append("dod_under_90d")
            except ValueError:
                pass
            if f.get("dod_source", "").startswith("siftmap"):
                flags.append("dod_from_siftmap_not_obituary")
            if rec.get("is_trust"):
                flags.append("trust - trustee inferred")
            if rec.get("name_confidence") in ("low", "surname_only", "check"):
                flags.append(f"name_confidence {rec['name_confidence']}")
            if f["verdict"] == "unresolved":
                flags.append("unresolved")
            if f.get("decedent_name") and f["verdict"] == "owner did die" \
                    and not names_match(f["decedent_name"], f["owner_name"]):
                flags.append("decedent name differs from owner name - check")
            f["flags"] = flags
            f["researched_at"] = datetime.now().isoformat(timespec="seconds")
            # A verdict that rests on an LLM/fetch ERROR is not a finding: do not cache it,
            # so the next run retries instead of freezing "unresolved" into the file.
            llm_errors = [t.get("error") for sch in f["searched"] for t in sch["trail"] if t.get("error")]
            llm_errors += [h["error"] for sch in f["searched"] for t in sch["trail"]
                           for h in (t.get("hits") or []) if h.get("error")]
            if llm_errors and f["verdict"] == "unresolved":
                f["transient_errors"] = llm_errors[:3]
                consecutive_errors += 1
                say(f"    not cached: {llm_errors[0][:100]}")
                if consecutive_errors >= a.max_consecutive_errors:
                    say(f"STOPPING: {consecutive_errors} records in a row failed on a backend "
                        f"error, not on the data. Last error: {llm_errors[0][:200]}")
                    say("Nothing from this streak was cached. Fix the backend and re-run "
                        "`research` -- it resumes from the cache.")
                    jdump(run / "stage_c_research.json",
                          {"generated_at": datetime.now().isoformat(timespec="seconds"),
                           "aborted_on_backend_error": llm_errors[0][:300],
                           "findings": findings})
                    return 4
            else:
                consecutive_errors = 0
                jdump(cpath, f)
        findings.append(f)
        verdicts[f["verdict"]] += 1
        say(f"  [{i}/{len(keys)}] {rec['street']}, {rec['city']}: {f['verdict']}"
            f"{'  DOD ' + f['dod'] if f.get('dod') else ''}"
            f"{'  ' + (f.get('obituary_url') or '') if f.get('obituary_url') else ''}")

    jdump(run / "stage_c_research.json", {"generated_at": datetime.now().isoformat(timespec="seconds"),
                                          "findings": findings})
    say(f"verdicts: {dict(verdicts)}")
    return 0


# ───────────────────────── augment ─────────────────────────

OBIT_REL_MAP = {"wife": "Wife", "husband": "Husband", "spouse": "Spouse", "partner": "Spouse",
                "son": "Son", "daughter": "Daughter", "child": "Child", "stepson": "Relative",
                "stepdaughter": "Relative", "sister": "Sister", "brother": "Brother",
                "sibling": "Sibling", "mother": "Mother", "father": "Father",
                "executor": "Relative", "personal representative": "Relative"}
SIGNER_WORDS = {"Wife", "Husband", "Spouse", "Son", "Daughter", "Child", "Sister", "Brother",
                "Sibling", "Mother", "Father"}


def cmd_augment(a) -> int:
    """Obituary-named signers SmartSkip never returned -> Tracerfy at the property address.

    Roeworth (pilot): SmartSkip returned zero relatives; the obituary names a wife and a
    sister. Those are the signers, and at $0.02 a trace they are the cheapest phones in
    the whole run. Adds them to ranked_records.json as relatives with is_dm=True and
    source "obituary+tracerfy"; signers are moved ahead of generic relatives so they take
    the low REL slots.
    """
    run = Path(a.run_dir)
    import obituary_dp_run as dpr  # noqa: E402
    records = jload(run / "records.json", {})
    ranked = jload(run / "ranked_records.json", {})
    research = {f["record"]: f for f in (jload(run / "stage_c_research.json", {}) or {}).get("findings", [])}
    api_key = os.environ.get("TRACERFY_API_KEY", "")

    targets, owners = [], []
    for key, rk in ranked.items():
        f = research.get(key) or {}
        if f.get("verdict") != "owner did die":
            continue
        rec = records[key]
        existing = [r["name"] for r in rk["rels"]]
        drop = {n.lower() for n in f.get("drop_names") or []}
        names = list(f.get("survivors") or [])
        if f.get("executor_named"):
            names.append({"name": f["executor_named"], "relationship": "executor"})
        for sv in names:
            nm = _survivor_name(sv)
            rel_word = (sv.get("relationship") if isinstance(sv, dict) else "") or ""
            canon = OBIT_REL_MAP.get(rel_word.strip().lower())
            if not nm or not canon or nm.lower() in drop:
                continue
            if any(names_match(nm, e) for e in existing):
                continue
            toks = [t for t in re.split(r"\s+", nm.strip()) if t and t.strip(".") .lower() not in SUFFIXES]
            if len(toks) < 2:
                continue
            first, last = toks[0], toks[-1]
            if len(first) < 2 or len(last) < 2:
                continue
            if any(t["first"].lower() == first.lower() and t["last"].lower() == last.lower()
                   and t["_key"] == key for t in targets):
                continue
            targets.append({"_key": key, "first": first.title(), "last": last.title(), "name": f"{first} {last}".title(),
                            "canon_rel": canon, "type_raw": f"obituary: {rel_word}",
                            "mailing_street": rec["street"], "mailing_city": rec["city"],
                            "mailing_state": rec["state"], "mailing_zip": rec["zip"],
                            "phones": [], "age": "", "is_dm": canon in SIGNER_WORDS,
                            "source": "obituary+tracerfy"})
    say(f"augment: {len(targets)} obituary-named relatives to trace (~${0.02 * len(targets):.2f})")
    if not targets:
        return 0
    if a.dry_run:
        for t in targets:
            say(f"   {ranked[t['_key']]['input_name']:<14} {t['name']:<28} {t['canon_rel']}")
        return 0
    found, stats = dpr.tracerfy_gapfill(targets, api_key)
    say(f"   tracerfy: {stats}")
    added = Counter()
    for t in targets:
        hit = found.get(f"{t['first']} {t['last']}".lower())
        if hit:
            t["phones"] = [{"number": n, "type": hit["types"].get(n, "UNKNOWN"), "source": "tracerfy"}
                           for n in hit["numbers"]]
            t["tracerfy"] = True
        rk = ranked[t["_key"]]
        rk["rels"].append({k: v for k, v in t.items() if k != "_key"})
        added["with_phone" if t["phones"] else "no_phone"] += 1
    # signers first, then everything else in its existing order; renumber slots
    for rk in ranked.values():
        rels = rk["rels"]
        rels.sort(key=lambda r: 0 if r.get("is_dm") else 1)
        for i, r in enumerate(rels, 1):
            r["n"] = i
        rk["n_phones"] = sum(len(r["phones"]) for r in rels)
    jdump(run / "ranked_records.json", ranked)
    say(f"   added {dict(added)}; ranked_records.json updated. Re-run `score` to tier the new numbers.")
    return 0


# ───────────────────────── score ─────────────────────────

SKIP_LINE_TYPES = {"tollfree", "premium", "voicemail"}


def _keep_number(t: dict) -> tuple[bool, str]:
    if not t or t.get("error"):
        return False, "unscored"
    if t.get("is_valid") is False:
        return False, "invalid"
    if (t.get("line_type") or "").lower() in SKIP_LINE_TYPES:
        return False, f"skip line type {t.get('line_type')}"
    if t.get("litigator"):
        return False, "litigator risk"
    if t.get("tier") == "Drop":
        return False, "drop tier"
    return True, ""


def cmd_score(a) -> int:
    run = Path(a.run_dir)
    import obituary_dp_run as dpr  # noqa: E402
    ranked = jload(run / "ranked_records.json", {})
    research = {f["record"]: f for f in (jload(run / "stage_c_research.json", {}) or {}).get("findings", [])}
    cache = jload(run / "trestle_cache.json", {})
    api_key = os.environ.get("TRESTLE_PAID_API_KEY") or os.environ.get("TRESTLE_API_KEY", "")
    if not api_key:
        say("ERROR: no TRESTLE_PAID_API_KEY / TRESTLE_API_KEY")
        return 1

    # Price EVERY number on a kept contact, not the first few. The old slice took
    # `max_phones_per_rel + 1` -- three plus "one spare so a dropped number can be replaced" --
    # which quietly made scoring the real ceiling on how many numbers could ever load: an
    # unscored number fails _keep_number and is dropped, so the 4th+ could never reach a record
    # however the build caps were set. `--max-score-per-rel` bounds it only if a future batch
    # comes back with 20 numbers a head.
    cut = a.max_score_per_rel or None
    numbers: list[str] = []
    for key, rk in ranked.items():
        f = research.get(key, {})
        v = f.get("verdict") or ""
        # "presumed alive" is the FTM-cohort verdict (VERDICT_PRESUMED): the owner is the
        # decision maker there too, so their own numbers are priced like any signer's.
        owner_alive = "owner alive" in v or "presumed alive" in v
        for rel in rk["rels"]:
            for p in (rel["phones"][:cut] if cut else rel["phones"]):
                numbers.append(p["number"])
        if owner_alive:
            for p in (rk["subject"]["phones"][:cut] if cut else rk["subject"]["phones"]):
                numbers.append(p["number"])
    uniq = sorted({n for n in numbers if n and n not in cache or (n in cache and cache[n].get("error"))})
    if a.limit:
        uniq = uniq[: a.limit]
    say(f"trestle: {len(set(numbers))} unique numbers referenced, {len(uniq)} to score "
        f"(~${0.015 * len(uniq):.2f}), {len(cache)} cached")
    if uniq:
        # phone_validator.call_trestle goes through `requests`. obituary_dp_run.trestle_score
        # uses urllib and Trestle's edge answers the default Python-urllib user agent with
        # 403 AUTHENTICATION_FAILED on a key that works -- 390 of 390 pilot calls "failed"
        # that way before this was traced to the transport, not the credential.
        import phone_validator as pv  # noqa: E402
        for i, n in enumerate(uniq, 1):
            d = pv.call_trestle(n, api_key, add_litigator=a.litigator)
            if not isinstance(d, dict) or d.get("error") and not d.get("phone_number"):
                cache[n] = {"error": str((d or {}).get("error") or d)[:120]}
            else:
                score = d.get("activity_score")
                lit = ((d.get("add_ons") or {}).get("litigator_checks") or {}).get("phone.is_litigator_risk")                     if a.litigator else None
                cache[n] = {"activity_score": score, "tier": dpr.tier_for(score),
                            "line_type": d.get("line_type"), "is_valid": d.get("is_valid"),
                            "carrier": d.get("carrier") or "", "litigator": lit}
            if i % 50 == 0 or i == len(uniq):
                jdump(run / "trestle_cache.json", cache)
                say(f"  scored {i}/{len(uniq)}")
            time.sleep(0.12)

    tiers = Counter(v.get("tier", "error") for v in cache.values())
    say(f"tiers: {dict(tiers)}")

    rows = []
    for key, rk in ranked.items():
        for rel in rk["rels"]:
            for p in rel["phones"]:
                t = cache.get(p["number"], {})
                keep, why = _keep_number(t)
                rows.append({"key": key, "input_name": rk["input_name"], "rel_n": rel["n"],
                             "name": rel["name"], "relationship": rel["canon_rel"], "age": rel["age"],
                             "is_signer": rel["is_dm"], "number": p["number"],
                             "ss_type": p.get("type"), "line_type": t.get("line_type"),
                             "activity_score": t.get("activity_score"), "tier": t.get("tier"),
                             "keep": keep, "why_not": why, "source": p.get("source", "smartskip")})
    write_csv(run / "dial_sheet.csv", rows, list(rows[0].keys()) if rows else ["key"])
    say(f"dial_sheet.csv: {len(rows)} numbers, {sum(1 for r in rows if r['keep'])} kept")
    return 0


# ───────────────────────── build ─────────────────────────

RELOAD_BASE = ["Property Street Address", "Property City", "Property State", "Property ZIP Code",
               "Owner First Name", "Owner Last Name",
               "Mailing Street Address", "Mailing City", "Mailing State", "Mailing ZIP Code"]


def _api_phone_type(ss_type: str, trestle_line: str) -> str:
    t = (trestle_line or ss_type or "").lower()
    if "mobile" in t or "cell" in t or "wireless" in t:
        return "MOBILE"
    if "land" in t or "residential" in t:
        return "LANDLINE"
    return "UNKNOWN"


# Slug -> prose. The flags are written for the code; the note is read by a caller.
FLAG_PROSE = {
    "dod_under_90d": "DOD under 90 days",
    "dod_from_siftmap_not_obituary": "DOD from SiftMap, not the obituary",
    "unresolved": "no obituary matched the owner or a spouse candidate",
    "not researched": "not researched",
    "presumed alive, not researched": "owner presumed alive - no deceased signal, "
                                      "obituary research skipped",
    "trust - trustee inferred": "title in a trust, trustee inferred from the title",
    "decedent name differs from owner name - check": "decedent name differs from the owner name, check",
}
# _push_one_api truncates the note at 2000 (the add-notes cap); stay just under it.
NOTE_MAX = 1990
# Measured live 2026-09-01 on 1815 Drew St: 39 phones sent, 200 + "added" for all of them,
# 30 stored. The limit is the account's, not ours.
OWNER_PHONE_CAP = 30

# Dial priority, best first. `_phone_sort_key` orders on LINE TYPE and runs at rank time,
# before Trestle scoring exists -- so on its own it parked 538 Dial First numbers behind
# Dial Fourth mobiles. Tier has to lead, with line type kept as the tie-break.
TIER_RANK = {"Dial First": 0, "Dial Second": 1, "Dial Third": 2, "Dial Fourth": 3}


def _tier_sort_key(pt: tuple) -> tuple:
    p, t = pt
    return (TIER_RANK.get(t.get("tier"), 8), _phone_sort_key(p))
DOD_SRC_PROSE = {
    "obituary": "obituary",
    "siftmap_last_obituary_date": "SiftMap obituary date, not the obituary",
    "": "no source",
}


def _flag_prose(fl: str) -> str:
    if fl in FLAG_PROSE:
        return FLAG_PROSE[fl]
    if fl.startswith("name_confidence "):
        return f"owner name parsed with {fl.split(' ', 1)[1]} confidence"
    if fl.startswith("dod_conflict:"):
        return "DOD conflict - " + fl.split(":", 1)[1].strip()
    return fl.replace("_", " ")


def _note(rec: dict, rk: dict, f: dict, rel_lines: list[str], n_phones: int,
          not_loaded: list[str] | None = None) -> str:
    """The note a caller actually reads: one section per line, one relative per line.

    The previous version joined every section with "  ||  " and every relative with " | ",
    two near-identical nested separators, so the whole thing arrived as one paragraph.
    """
    # A presumed-alive record (VERDICT_PRESUMED, FTM cohorts) is not a decedent brief:
    # the owner is the person to call, and no obituary step ever ran.
    presumed = "presumed alive" in (f.get("verdict") or "")
    if presumed:
        out = [f"DEEP PROSPECTING {TODAY} (SmartSkip + Trestle)", ""]
        # Do not claim "no numbers" here: the CRM skip trace found none (that is how
        # the record entered the cohort), but SmartSkip may have -- and build loads
        # the owner's own numbers FIRST, so the dial list can start with the owner.
        out.append(f"OWNER: {rec['ss_first']} {rec['ss_last']} - presumed alive; "
                   "the owner is the person to call (relatives are reach channels)")
    else:
        out = [f"DEEP PROSPECTING {TODAY} (SmartSkip + obituary + Trestle)", ""]
        who = f.get("decedent_name") or f"{rec['ss_first']} {rec['ss_last']}"
        src = f.get("dod_source") or ""
        out.append(f"DECEDENT: {who} - DOD {f.get('dod') or 'unknown'} "
                   f"({DOD_SRC_PROSE.get(src, src)})")
    # Cached pilot findings read "relative (relative) died" -- the label was interpolated
    # into a sentence that already carried it.
    verdict = (f.get("verdict") or "not researched").replace("relative (relative) died",
                                                             "a relative died")
    out.append(f"VERDICT: {verdict}")
    if rk.get("signer_basis"):
        out.append(f"SIGNERS: {rk['signer_basis']}")

    if rel_lines:
        out += ["", "WHO TO CALL"] + list(rel_lines)

    nl = list(not_loaded or [])
    if f.get("obit_only_names"):
        nl.append("Named in the obituary, not in SmartSkip: "
                  + "; ".join(dict.fromkeys(f["obit_only_names"][:8])))
    # dict.fromkeys, not set(): names_match is fuzzy and repeatedly matched the same person,
    # which put "DONALD DAVIS" in the note four times.
    dropped = list(dict.fromkeys(f.get("drop_names") or []))
    if dropped:
        nl.append("Predeceased, do not call: " + "; ".join(dropped))
    nl_start = len(out) + 2
    if nl:
        out += ["", "NOT LOADED"] + nl

    tail = []
    if f.get("executor_named"):
        tail.append(f"EXECUTOR NAMED IN OBITUARY: {f['executor_named']}")
    if rec.get("pr"):
        tail.append(f"PR ON RECORD: {rec['pr']}")
    if f.get("obituary_url"):
        tail.append(f"OBITUARY: {f['obituary_url']}")
    if rec.get("is_trust"):
        tail.append(f"TITLE HELD BY: {rec['business_name']} (trustee inferred from the title)")
    flags = [_flag_prose(x) for x in (f.get("flags") or [])]
    if n_phones == 0:
        flags.append("no numbers found")
    if flags:
        tail.append("FLAGS: " + "; ".join(dict.fromkeys(flags)))
    if presumed:
        tail.append("MUST VERIFY: owner is alive and still the owner of record; "
                    "deed vesting")
    else:
        tail.append("MUST VERIFY: decedent is the owner of record; deed vesting; "
                    "PR appointment (Register of Wills / Circuit Court)")
    out += [""] + tail

    note = "\n".join(out)
    if len(note) > NOTE_MAX and nl:
        # _push_one_api posts note[:2000]. MUST VERIFY is the LAST line, so a blind cut
        # removes exactly the line that matters most. Spend the overflow on the NOT LOADED
        # lists instead -- they are the long ones (a record with 20 capped in-laws) and the
        # least load-bearing, since nothing in them is being dialled anyway.
        fixed = len(note) - sum(len(x) + 1 for x in nl)
        per = max(60, (NOTE_MAX - fixed) // len(nl))
        for j, line in enumerate(nl):
            if len(line) > per:
                out[nl_start + j] = line[: per - 4].rstrip(" ;,") + " ..."
        note = "\n".join(out)
        if len(note) > NOTE_MAX:
            # Still over: drop the block entirely rather than shave the tail. One record
            # lost the closing ")" of MUST VERIFY to the per-line floor before this.
            del out[nl_start - 2: nl_start + len(nl)]
            out.insert(nl_start - 2, f"NOT LOADED: {len(nl)} lines omitted, note too long")
            note = "\n".join(out)
    return note[:NOTE_MAX]


def cmd_build(a) -> int:
    run = Path(a.run_dir)
    records = jload(run / "records.json", {})
    ranked = jload(run / "ranked_records.json", {})
    research = {f["record"]: f for f in (jload(run / "stage_c_research.json", {}) or {}).get("findings", [])}
    cache = jload(run / "trestle_cache.json", {})

    plan: dict[str, dict] = {}
    max_rel = 0
    max_phones = 0
    tag_rows = []
    no_numbers = []
    for key, rk in ranked.items():
        rec = records[key]
        f = research.get(key, {"verdict": "not researched", "flags": ["not researched"]})
        verdict = f.get("verdict") or "not researched"
        # The 3-year DOD sanity rule (obituary_enricher.MAX_DOD_GAP_YEARS): an obituary dated
        # years before SiftMap's own obituary date is a namesake, not the owner. Doeworth
        # (pilot): obituary DOD 2018-07-11 against a 2026 obituary date. Keep the record,
        # downgrade the verdict, and say why.
        try:
            if f.get("dod") and f.get("dod_source") == "obituary" and rec.get("obit_date"):
                gap = abs((date.fromisoformat(f["dod"][:10]) - date.fromisoformat(rec["obit_date"][:10])).days)
                if gap > 3 * 365:
                    f = dict(f)
                    f["flags"] = list(f.get("flags") or []) + [
                        f"dod_conflict: obituary DOD {f['dod']} vs SiftMap obituary date {rec['obit_date']} - wrong person?"]
                    if verdict == "owner did die":
                        verdict = f"unresolved (obituary DOD {f['dod']} conflicts with SiftMap {rec['obit_date']})"
                        f["verdict"] = verdict
        except ValueError:
            pass
        presumed = "presumed alive" in verdict     # VERDICT_PRESUMED, the FTM cohort
        owner_alive = "owner alive" in verdict or presumed
        drop = {n.lower() for n in f.get("drop_names") or []}

        cfields, rel_lines, overflow, chan_over = {}, [], [], []
        slot = 0
        seen_numbers: set[str] = set()
        # contacts[] is the candidate pool: every kept number for every contact, each already
        # sorted best-tier-first. Nothing is truncated here -- the 30-cap is applied afterwards
        # by priority, not by position, so a Dial First can never be parked behind a Dial
        # Fourth that merely sat in an earlier REL slot.
        contacts: list[dict] = []

        # A LIVING owner is the decision maker, so their own numbers are collected FIRST.
        # They used to be appended after all 15 relatives and were then cut by the 30-cap:
        # 14029 Breeders Cup Dr lost its owner's Dial First entirely that way.
        if owner_alive:
            kept = []
            for p in rk["subject"]["phones"]:
                t = cache.get(p["number"], {})
                if _keep_number(t)[0] and p["number"] not in seen_numbers:
                    seen_numbers.add(p["number"])
                    kept.append((p, t))
            kept.sort(key=_tier_sort_key)
            if kept:
                contacts.append({"kind": "owner", "n": 0, "name": "owner", "kept": kept})

        # rk["rels"] already arrives signers -> blood -> in-law channels, so walking it in
        # order fills the REL fields blood-first and channels take only what is left over.
        for rel in rk["rels"]:
            if (rel["name"] or "").lower() in drop:
                continue
            if slot >= a.max_rel_slots:
                # Past the cap: named in the note, not loaded as a field or a phone.
                label = f"{rel['name']} ({rel['canon_rel']}{', ' + str(rel['age']) if rel.get('age') else ''})"
                (chan_over if rel.get("is_channel") else overflow).append(label)
                continue
            kept = []
            for p in rel["phones"]:
                t = cache.get(p["number"], {})
                if _keep_number(t)[0] and p["number"] not in seen_numbers:
                    seen_numbers.add(p["number"])
                    kept.append((p, t))
            # Best tier first, so REL{n}: Phone 1..3 get this contact's THREE BEST numbers
            # rather than whichever three SmartSkip happened to list first.
            kept.sort(key=_tier_sort_key)
            if not kept and not a.include_phoneless:
                continue
            slot += 1
            n = slot
            cfields[f"REL{n}: Full Name"] = (rel["name"] or "").upper()
            # The REL custom fields only exist as Phone 1..3 -- that cap is real and stays.
            # The owner's PHONE LIST has no such limit, so it takes every number, tagged
            # Rel{n}.4, Rel{n}.5 and on (the account already carries Rel4.4 / Rel4.5 from the
            # IDI imports). Two different limits; they used to be one.
            for m, (p, _t) in enumerate(kept[: a.max_rel_field_phones], 1):
                cfields[f"REL{n}: Phone {m}"] = p["number"]
            contacts.append({"kind": "rel", "n": n, "name": rel["name"], "kept": kept,
                             "is_dm": bool(rel.get("is_dm")), "rel": rel})

        # ── allocation against the 30-phone owner cap ──────────────────
        # Pass 1 (coverage): one number for every contact, in REL order, owner first. No
        # signer can be left unreachable by a relative that happens to hold four good numbers.
        # Pass 2 (quality): everything else by TIER alone, whoever it belongs to.
        phones, phones_capped = [], []

        def _mk(c, p, t, m):
            tag = f"Owner.{m}" if c["kind"] == "owner" else f"Rel{c['n']}.{m}"
            tags = [tag] + ([t["tier"]] if t.get("tier") and t["tier"] != "Unknown" else [])
            return {"number": p["number"], "type": _api_phone_type(p.get("type"), t.get("line_type")),
                    "tags": tags, "status": "UNKNOWN", "is_connected": True,
                    "rel_n": c["n"], "rel_name": c["name"], "tier": t.get("tier")}

        rest = []
        for c in contacts:
            for m, (p, t) in enumerate(c["kept"], 1):
                (phones if m == 1 and len(phones) < OWNER_PHONE_CAP else rest).append(_mk(c, p, t, m))
        rest.sort(key=lambda ph: TIER_RANK.get(ph.get("tier"), 8))
        for ph in rest:
            (phones if len(phones) < OWNER_PHONE_CAP else phones_capped).append(ph)

        # The note lists each contact with the tiers that actually reached the dial list.
        loaded_by_n: dict[int, list[str]] = {}
        for ph in phones:
            loaded_by_n.setdefault(ph["rel_n"], []).append(ph.get("tier") or "?")
        for c in contacts:
            if c["kind"] != "rel":
                continue
            rel, n = c["rel"], c["n"]
            tiers = loaded_by_n.get(n, [])
            role = (" - SIGNER" if rel.get("is_dm")
                    else " - CHANNEL" if rel.get("is_channel") else "")
            rel_lines.append(f"REL{n} - {rel['name']} - {rel['canon_rel']}"
                             f"{', ' + str(rel['age']) if rel.get('age') else ''}"
                             f"{role}"
                             f"{' - ' + ' / '.join(tiers) if tiers else ' - no number'}")
        if owner_alive and loaded_by_n.get(0):
            rel_lines.insert(0, ("OWNER (presumed alive) - " if presumed else "OWNER (alive) - ")
                             + " / ".join(loaded_by_n[0]))

        tags = [TAG_DP]
        if verdict == "owner did die":
            tags.append(TAG_DECEASED)
        elif presumed:
            # Not TAG_ALIVE: that tag asserts a researched spouse death. Nothing here was
            # researched -- the record simply carried no deceased signal.
            tags.append(TAG_PRESUMED)
        elif owner_alive:
            tags.append(TAG_ALIVE)
        else:
            tags.append(TAG_UNVERIFIED)
        if rec.get("is_trust"):
            tags.append(TAG_TRUST)
        if not phones:
            tags.append(TAG_NO_NUMBERS)
            no_numbers.append({**rec, "verdict": verdict, "relatives": len(rk["rels"])})
        group = ("deceased" if verdict == "owner did die" else
                 "presumed_alive" if presumed else
                 "owner_alive" if owner_alive else "unverified")

        # DataSift keeps at most 30 phones on an owner: it answers the upsert with 200 and
        # echoes every number back in `added`, then silently stores the first 30. The cap is
        # applied above, by priority rather than by position.
        not_loaded = []
        if phones_capped:
            rels_cut = sorted({p["rel_n"] for p in phones_capped})
            not_loaded.append(
                f"{len(phones_capped)} more numbers are in the REL fields but not on the "
                f"dial list (DataSift caps an owner at {OWNER_PHONE_CAP}): "
                f"REL{', REL'.join(str(x) for x in rels_cut)}")
        if overflow:
            not_loaded.append("Over the REL cap: " + "; ".join(overflow))
        inlaws_out = list(dict.fromkeys(chan_over + [
            f"{c['name']} ({c.get('age') or '?'})" for c in (rk.get("channels_dropped") or [])]))
        if inlaws_out:
            not_loaded.append("In-laws found, not loaded: " + "; ".join(inlaws_out))
        plan[key] = {
            "key": key, "street": rec["street"], "city": rec["city"], "state": rec["state"], "zip": rec["zip"],
            "owner_first": rec["owner_first"], "owner_last": rec["owner_last"],
            "business_name": rec["business_name"],
            "mail_street": rec["mail_street"], "mail_city": rec["mail_city"],
            "mail_state": rec["mail_state"], "mail_zip": rec["mail_zip"],
            "verdict": verdict, "group": group, "tags": tags,
            "custom_fields": cfields, "phones": phones,
            "note": _note(rec, rk, f, rel_lines, len(phones), not_loaded),
        }
        max_rel = max(max_rel, slot)
        max_phones = max(max_phones, len(phones))
        for p in phones:
            for t in p["tags"]:
                tag_rows.append({"Phone Number": p["number"], "Phone Tag": t})

    jdump(run / "push_plan.json", {"generated_at": datetime.now().isoformat(timespec="seconds"),
                                   "max_rel_slots": max_rel, "max_phones": max_phones, "records": plan})

    # Reload CSVs, one per uniform tag group (the wizard applies one tag set per file).
    phone_cols = [f"Phone {i}" for i in range(1, max_phones + 1)]
    rel_cols = [f"REL{i}: Full Name" for i in range(1, max_rel + 1)]
    header = RELOAD_BASE + phone_cols + rel_cols + ["Notes"]
    for group in ("deceased", "owner_alive", "presumed_alive", "unverified"):
        rows = []
        for p in plan.values():
            if p["group"] != group or not p["phones"]:
                continue
            row = {"Property Street Address": p["street"], "Property City": p["city"],
                   "Property State": p["state"], "Property ZIP Code": p["zip"],
                   "Owner First Name": p["owner_first"], "Owner Last Name": p["owner_last"],
                   "Mailing Street Address": p["mail_street"], "Mailing City": p["mail_city"],
                   "Mailing State": p["mail_state"], "Mailing ZIP Code": p["mail_zip"],
                   "Notes": p["note"]}
            for i, ph in enumerate(p["phones"], 1):
                row[f"Phone {i}"] = ph["number"]
            row.update(p["custom_fields"])
            rows.append(row)
        write_csv(run / f"reload_{group}.csv", rows, header)
        say(f"reload_{group}.csv: {len(rows)} rows")

    write_csv(run / "phone_tags.csv", tag_rows, ["Phone Number", "Phone Tag"])
    write_csv(run / "no_numbers.csv", no_numbers, list(no_numbers[0].keys()) if no_numbers else ["key"])

    # Review workbook.
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Records"
        ws.append(["Address", "City", "County", "Owner", "Business", "Verdict", "DOD", "DOD source",
                   "Decedent (obit)", "Relatives", "Phones", "Rel slots", "Tags", "Obituary", "Flags", "Note"])
        for key, p in plan.items():
            f = research.get(key, {})
            ws.append([p["street"], p["city"], records[key]["county"],
                       f"{p['owner_first']} {p['owner_last']}".strip(), p["business_name"], p["verdict"],
                       f.get("dod", ""), f.get("dod_source", ""), f.get("decedent_name", ""),
                       len(ranked[key]["rels"]), len(p["phones"]), len(p["custom_fields"]),
                       ", ".join(p["tags"]), f.get("obituary_url", ""), ", ".join(f.get("flags") or []),
                       p["note"]])
        ws2 = wb.create_sheet("Dial Sheet")
        dial = list(csv.DictReader(open(run / "dial_sheet.csv", encoding="utf-8-sig"))) \
            if (run / "dial_sheet.csv").exists() else []
        if dial:
            ws2.append(list(dial[0].keys()))
            for r in dial:
                ws2.append(list(r.values()))
        for title, path in (("No Numbers", run / "no_numbers.csv"), ("Unusable", run / "unusable.csv")):
            w = wb.create_sheet(title)
            rows = list(csv.DictReader(open(path, encoding="utf-8-sig"))) if path.exists() else []
            if rows:
                w.append(list(rows[0].keys()))
                for r in rows:
                    w.append(list(r.values()))
        wb.save(run / "review.xlsx")
    except PermissionError:
        say("review.xlsx is open in Excel; skipped the workbook (CSV artifacts are written)")

    groups = Counter(p["group"] for p in plan.values())
    say(f"push_plan: {len(plan)} records, groups={dict(groups)}, "
        f"MAX REL SLOTS={max_rel}, max phones/record={max_phones}, "
        f"phone tags={len(tag_rows)}, no numbers={len(no_numbers)}")
    return 0


# ───────────────────────── API (read + write) ─────────────────────────

class WriteApi:
    """live_pull.LiveApi plus a `write` verb. Same minted JWT, same throttle."""

    def __init__(self):
        from live_pull import LiveApi, BASE  # noqa: E402
        self.api = LiveApi()
        self.base = BASE

    def get(self, path):
        return self.api.get(path)

    def search(self, body):
        return self.api.post_as_get("/api/internal/property/", body)

    def write(self, method: str, path: str, body, tries: int = 4):
        api = self.api
        for attempt in range(tries):
            if time.time() - api.minted > 1800:
                api._mint()
            api._throttle()
            data = json.dumps(body).encode()
            req = urllib.request.Request(
                self.base + path, data=data, method=method,
                headers={"Authorization": "Bearer " + api.token, "Content-Type": "application/json",
                         "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                    return r.status, (json.loads(raw) if raw else {})
            except urllib.error.HTTPError as e:
                txt = e.read().decode("utf-8", "replace")
                if e.code == 401 and attempt == 0:
                    api._mint()
                    continue
                if e.code == 429:
                    m = re.search(r"available in (\d+)", txt, re.I)
                    time.sleep(min((int(m.group(1)) if m else 30) + 2, 300))
                    continue
                return e.code, txt
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == tries - 1:
                    return 0, str(e)
                time.sleep(2 ** attempt)
        return 0, "retries exhausted"


def _resolve_uuid(api: WriteApi, p: dict) -> tuple[str, dict, str]:
    """(uuid, record, why) -- exact street+zip5 match among search hits, else ''."""
    status, body = api.search({"limit": 25, "offset": 0, "query": {"must": {"search": p["street"]}}})
    if status != 200:
        return "", {}, f"search failed {status}: {str(body)[:200]}"
    hits = body.get("results") or body.get("data") or []
    want = (norm_addr(p["street"]), p["zip"])
    exact = [h for h in hits if (norm_addr((h.get("address") or {}).get("street")),
                                 zip5((h.get("address") or {}).get("postal_code"))) == want]
    if len(exact) > 1:
        # This account holds duplicate property records: the same street+zip under two
        # different owner rows (7819 Chestnut Ave is on file under both Gordon Thompson and
        # Gail R. Huber). Disambiguate on the owner we actually traced. NEVER on a blank
        # name -- an empty owner_last matches a record whose last_name is null, which
        # "resolves" to an arbitrary stranger's record.
        first = (p.get("owner_first") or "").strip().lower()
        last = (p.get("owner_last") or "").strip().lower()
        if first and last:
            named = [h for h in exact
                     if ((h.get("owner") or {}).get("first_name") or "").strip().lower() == first
                     and ((h.get("owner") or {}).get("last_name") or "").strip().lower() == last]
            if len(named) == 1:
                return named[0]["uuid"], named[0], ""
            if len(named) > 1:
                # Same address AND same owner on both rows: a real duplicate record, not an
                # ambiguity. Either is correct; take the first and say so.
                return named[0]["uuid"], named[0], ""
        return "", {}, (f"{len(exact)} exact hits of {len(hits)} for {p['street']} {p['zip']}"
                        f" - owner {first or '?'} {last or '?'} did not single one out")
    if len(exact) != 1:
        return "", {}, f"{len(exact)} exact hits of {len(hits)} for {p['street']} {p['zip']}"
    return exact[0]["uuid"], exact[0], ""


REL_GROUP_LABEL = "Custom Fields 1"   # the live REL1..5 group (id 14); group 22 holds INACTIVE duplicates


def _group_id(f: dict):
    g = f.get("group")
    if isinstance(g, dict):
        return g.get("id")
    return f.get("group_id")


def _field_index(api: WriteApi) -> dict[str, dict]:
    """label -> field, ACTIVE fields only. The account carries an inactive duplicate
    'REL1: Full Name' in the 'Probate without PR' group; keyed naively by label it wins
    and every value lands on a dead field."""
    status, body = api.get("/api/internal/custom-fields/?limit=999")
    if status != 200:
        raise RuntimeError(f"custom-fields fetch failed {status}: {str(body)[:200]}")
    items = body if isinstance(body, list) else (body.get("results") or body.get("data") or [])
    out = {}
    for f in items:
        if f.get("is_active") is False:
            continue
        lbl = (f.get("label") or "").strip()
        if not lbl:
            continue
        prev = out.get(lbl)
        glabel = (f.get("group") or {}).get("label") if isinstance(f.get("group"), dict) else ""
        if prev is None or glabel == REL_GROUP_LABEL:
            out[lbl] = f
    return out


def cmd_schema(a) -> int:
    run = Path(a.run_dir)
    plan = jload(run / "push_plan.json", {})
    need = min(a.max_rel or plan.get("max_rel_slots") or 0, MAX_REL_SLOTS)
    api = WriteApi()
    fields = _field_index(api)
    have = sorted(int(m.group(1)) for lbl in fields for m in [re.match(r"REL(\d+): Full Name$", lbl)] if m)
    say(f"active REL Full Name fields on the account: {have}   needed up to REL{need}")
    wanted = []
    for n in range(1, need + 1):
        wanted.append((f"REL{n}: Full Name", "text"))
        for m in range(1, 4):
            wanted.append((f"REL{n}: Phone {m}", "phone"))
    missing = [(lbl, ft) for lbl, ft in wanted if lbl not in fields]
    if not missing:
        say("schema OK, nothing to create")
        return 0
    rel1 = fields.get("REL1: Full Name") or {}
    group_id = _group_id(rel1)
    glabel = (rel1.get("group") or {}).get("label") if isinstance(rel1.get("group"), dict) else ""
    say(f"group for new REL fields: id={group_id} label={glabel!r} (from the live REL1: Full Name)")
    say(f"to create ({len(missing)}): {[l for l, _ in missing]}")
    if not a.commit:
        say("dry run (no --commit)")
        return 0
    if not group_id or glabel != REL_GROUP_LABEL:
        say(f"ERROR: expected the live REL group {REL_GROUP_LABEL!r}; refusing to create fields elsewhere")
        return 2
    for lbl, ft in missing:
        body = {"label": lbl, "field_type": ft, "entity_type": "property",
                "group_id": group_id, "required": False}
        status, resp = api.write("POST", "/api/internal/custom-fields/", body)
        say(f"  create {lbl} ({ft}) -> {status} {str(resp)[:160]}")
        if status not in (200, 201):
            say("STOP: custom-field create refused. Add the remaining fields by hand in Settings > Custom "
                f"Fields (group {REL_GROUP_LABEL!r}), then re-run schema.")
            return 3
    fields = _field_index(api)
    still = [lbl for lbl, _ in missing if lbl not in fields]
    say("read-back: " + ("all present" if not still else f"STILL MISSING {still}"))
    return 0 if not still else 4


# ───────────────────────── push ─────────────────────────

def _push_one_api(api: WriteApi, p: dict, fields: dict, commit: bool) -> dict:
    log = {"key": p["key"], "street": p["street"], "route": "api", "at": datetime.now().isoformat(timespec="seconds"),
           "commit": commit, "steps": {}}
    uuid, rec, why = _resolve_uuid(api, p)
    if not uuid:
        log["error"] = why
        return log
    log["uuid"] = uuid
    status, full = api.get(f"/api/internal/property/{uuid}/")
    if status != 200:
        log["error"] = f"record fetch {status}"
        log["http_status"] = status
        return log
    owner = full.get("owner") or {}
    owner_uuid = owner.get("uuid")
    log["owner_before"] = f"{owner.get('first_name')} {owner.get('last_name')}".strip()
    log["phones_before"] = len(owner.get("phones") or [])
    if not commit:
        log["would_write"] = {"phones": len(p["phones"]), "custom_fields": p["custom_fields"],
                              "tags": p["tags"], "note_chars": len(p["note"])}
        return log

    # 1. phones (upsert by number on the OWNER)
    if p["phones"] and owner_uuid:
        want = {_d10(ph["number"]) for ph in p["phones"]}
        # Superseded numbers have to come OFF first. DataSift caps an owner at 30, so on a
        # re-push a record already holding 30 silently refuses every better number the new
        # tier ordering picked (14029 Breeders Cup Dr: 23 of 30 landed, 7 blocked by stale
        # ones). GUARD: only ever remove a number carrying OUR Rel{N}.{M} / Owner.{M} tag and
        # absent from the current plan -- a number this pipeline did not write is never
        # touched, however stale it looks.
        stale = []
        stale_restore = []
        for ph in owner.get("phones") or []:
            num = ph.get("number") or ""
            tags = [t.get("name") if isinstance(t, dict) else str(t) for t in (ph.get("tags") or [])]
            if _d10(num) not in want and any(_OURS_RE.match(t) for t in tags):
                stale.append(num)
                # Enough to put the number back if the upsert below then fails.
                stale_restore.append({"number": num, "type": ph.get("type") or "UNKNOWN",
                                      "tags": tags, "status": ph.get("status") or "UNKNOWN",
                                      "is_connected": bool(ph.get("isConnected", True)),
                                      "verified": False})
        if stale:
            st, resp = api.write("POST", f"/api/internal/owner/{owner_uuid}/remove-phones/",
                                 {"phones": stale})
            log["steps"]["phones_removed"] = {"status": st, "sent": len(stale),
                                              "removed": len((resp or {}).get("removed") or [])
                                              if isinstance(resp, dict) else None}
        # Send the FULL planned list, not just the numbers that are new. The old code skipped
        # anything already on the owner, so a re-push could never CORRECT a tag -- and the
        # tier ordering reshuffles almost every Rel{N}.{M} assignment.
        payload = [{"number": ph["number"], "type": ph["type"], "tags": ph["tags"], "status": ph["status"],
                    "is_connected": ph["is_connected"], "verified": False}
                   for ph in p["phones"]]
        if payload:
            st, resp = api.write("POST", f"/api/internal/owner/{owner_uuid}/upsert-phones/", {"phones": payload})
            log["steps"]["phones"] = {"status": st, "sent": len(payload), "resp": str(resp)[:200]}
            if st not in (200, 201, 204):
                log["http_status"] = st
                # The removal above has already landed. Without this restore, a failed
                # re-push leaves the owner holding FEWER dialable numbers than before
                # the push -- the one outcome strictly worse than doing nothing.
                if (log["steps"].get("phones_removed") or {}).get("removed"):
                    st_r, _ = api.write("POST", f"/api/internal/owner/{owner_uuid}/upsert-phones/",
                                        {"phones": stale_restore})
                    log["steps"]["phones_restored"] = {"status": st_r, "sent": len(stale_restore)}
                log["error"] = ("403 on upsert-phones" if st == 403
                                else f"upsert-phones failed (status {st})")
                return log
    # 2. custom fields
    if p["custom_fields"]:
        body = []
        for lbl, val in p["custom_fields"].items():
            f = fields.get(lbl)
            if not f:
                log.setdefault("missing_fields", []).append(lbl)
                continue
            body.append({"field_uuid": f["uuid"], "value": val})
        if body:
            st, resp = api.write("PATCH", f"/api/internal/property/{uuid}/custom-field/update-values/", body)
            log["steps"]["custom_fields"] = {"status": st, "sent": len(body), "resp": str(resp)[:200]}
            if st == 403:
                log["error"] = "403 on custom-field update"
                log["http_status"] = 403
                return log
            if st not in (200, 201, 204):
                # phone-type fields may want a format we did not guess: land the names alone
                uuid_to_label = {f["uuid"]: l for l, f in fields.items()}
                names_only = [b for b in body if "Full Name" in uuid_to_label.get(b["field_uuid"], "")]
                st2, resp2 = api.write("PATCH", f"/api/internal/property/{uuid}/custom-field/update-values/",
                                       names_only)
                log["steps"]["custom_fields_names_only"] = {"status": st2, "sent": len(names_only),
                                                            "resp": str(resp2)[:200]}
    # 3. tags: read-modify-write of the FULL set (tags_add is silently ignored)
    current = [t.get("name") if isinstance(t, dict) else str(t) for t in (full.get("tags") or [])]
    merged = current + [t for t in p["tags"] if t not in current]
    st, resp = api.write("PATCH", f"/api/internal/property/{uuid}/", {"tags": merged})
    log["steps"]["tags"] = {"status": st, "added": [t for t in p["tags"] if t not in current], "resp": str(resp)[:200]}
    if st == 403:
        log["error"] = "403 on tags PATCH"
        log["http_status"] = 403
        return log
    # 4. note -> the record's message board, PINNED.
    #
    # add-notes writes the `notes` FIELD, which is not the message board a caller reads. It
    # answers 204, so the old code's message/ fallback (gated on 404/405) never ran and 619
    # records ended up with an empty board. Post the message first, then pin it: `pinned:true`
    # on create is accepted and IGNORED (201, pinned=False), and PATCHing pinned returns 200
    # while changing nothing. Only POST .../message/{uuid}/pin/ (204) actually pins.
    # add-notes is deliberately NOT called: it appends to the `notes` field, which is not
    # what the record page shows, and a re-push would stack a second copy there.
    st2, resp2 = api.write("POST", f"/api/internal/property/{uuid}/message/",
                           {"message": p["note"][:4000]})
    msg_uuid = (resp2 or {}).get("uuid") if isinstance(resp2, dict) else None
    log["steps"]["message"] = {"status": st2, "uuid": msg_uuid, "resp": str(resp2)[:120]}
    if msg_uuid:
        # A re-push must not leave two DP boards on the record: drop any earlier one --
        # but only now that the replacement exists. This cleanup used to run even when
        # the POST above failed, deleting the record's ONLY pinned note and leaving the
        # board empty: a failed re-push must never end strictly worse than no push.
        stx, mbx = api.get(f"/api/internal/property/{uuid}/message/")
        for old_msg in ((mbx.get("results") or []) if isinstance(mbx, dict) else []):
            if (old_msg.get("message") or "").startswith("DEEP PROSPECTING")                     and old_msg.get("uuid") and old_msg.get("uuid") != msg_uuid:
                api.write("DELETE", f"/api/internal/property/{uuid}/message/{old_msg['uuid']}/", {})
        st3, resp3 = api.write("POST", f"/api/internal/property/{uuid}/message/{msg_uuid}/pin/", {})
        log["steps"]["pin"] = {"status": st3}

    # read back
    time.sleep(1.0)
    st, after = api.get(f"/api/internal/property/{uuid}/")
    st2, cf = api.get(f"/api/internal/property/{uuid}/custom-field/")
    cf_rows = cf if isinstance(cf, list) else (cf.get("results") or cf.get("data") or []) if isinstance(cf, dict) else []
    got_cf = {((r.get("custom_field") or {}).get("label") or ""): r.get("value") for r in cf_rows}
    got_phones = {(ph.get("number") or ""): ph for ph in ((after.get("owner") or {}).get("phones") or [])}
    log["readback"] = {
        "owner_after": f"{(after.get('owner') or {}).get('first_name')} {(after.get('owner') or {}).get('last_name')}".strip(),
        "phones_after": len(got_phones),
        "phones_landed": [n for n in (ph["number"] for ph in p["phones"]) if n in got_phones],
        "phones_missing": [n for n in (ph["number"] for ph in p["phones"]) if n not in got_phones],
        "phone_tags_sample": {n: got_phones[n].get("tags") for n in list(got_phones)[:3]},
        "custom_fields_landed": {k: v for k, v in got_cf.items() if k in p["custom_fields"] and v},
        "custom_fields_missing": [k for k in p["custom_fields"] if not got_cf.get(k)],
        "tags_missing": [t for t in p["tags"] if t not in
                         [x.get("name") if isinstance(x, dict) else str(x) for x in (after.get("tags") or [])]],
        "notes_present": bool(after.get("notes")),
        "message_pinned": None,   # filled below from the message board
        # Whether the field preserves newlines is the thing the probe exists to answer,
        # so keep the stored text verbatim rather than a boolean.
        # The `notes` FIELD is deliberately no longer written -- reading it here reported
        # "no newline survived" on a record whose pinned board message was perfect.
        # note_stored is filled from the message board below.
        "note_stored": "",
    }
    stm, mb = api.get(f"/api/internal/property/{uuid}/message/")
    msgs = (mb.get("results") or []) if isinstance(mb, dict) else []
    log["readback"]["message_pinned"] = any(x.get("pinned") for x in msgs)
    log["readback"]["messages_on_board"] = len(msgs)
    log["readback"]["note_stored"] = next(
        (x.get("message") or "" for x in msgs if x.get("pinned")), "")[:2500]
    rb = log["readback"]
    log["ok"] = (not rb["phones_missing"] and not rb["custom_fields_missing"] and not rb["tags_missing"]
                 and rb["owner_after"] == log["owner_before"] and rb["message_pinned"])
    return log


def _wizard_push(run: Path, group: str, tags: list[str], commit: bool, headed: bool, one_row_key: str | None) -> dict:
    from sift_upload_wizard import run_upload  # noqa: E402
    src = run / f"reload_{group}.csv"
    if not src.exists():
        return {"success": False, "message": f"{src.name} missing"}
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))
    if not rows:
        return {"success": True, "message": f"{src.name} empty, nothing to upload", "skipped": True}
    path = src
    if one_row_key:
        plan = jload(run / "push_plan.json", {})["records"]
        street = plan[one_row_key]["street"]
        rows = [r for r in rows if r["Property Street Address"] == street]
        if not rows:
            return {"success": False, "message": f"{one_row_key} not in {src.name}"}
        path = run / f"_probe_{group}.csv"
        write_csv(path, rows, list(rows[0].keys()))
    shot = str(run / f"wizard_{group}")
    return asyncio.run(run_upload(str(path), "Obituary", tags, existing_list=True, has_phones=True,
                                  do_finish=commit, headless=not headed, shot_base=shot))


def cmd_push(a) -> int:
    run = Path(a.run_dir)
    plan_doc = jload(run / "push_plan.json", {})
    plan: dict[str, dict] = plan_doc.get("records", {})
    log_path = run / "push_log.jsonl"
    done = set()
    if log_path.exists() and not a.force:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                if e.get("ok") and e.get("commit"):
                    done.add(e["key"])
            except json.JSONDecodeError:
                pass

    keys = [k for k, p in plan.items() if p["phones"] or a.include_no_numbers]
    if a.probe:
        if a.probe in plan:
            keys = [a.probe]
        else:
            # the record with the most REL slots exercises the REL6+ path
            keys = sorted(keys, key=lambda k: -len(plan[k]["custom_fields"]))[:1]
        say(f"PROBE record: {plan[keys[0]]['street']} ({len(plan[keys[0]]['custom_fields'])} REL slots, "
            f"{len(plan[keys[0]]['phones'])} phones)")
    keys = [k for k in keys if k not in done]
    if a.limit:
        keys = keys[: a.limit]
    say(f"push route={a.route} commit={a.commit} records={len(keys)} (already done: {len(done)})")

    if a.route == "api":
        api = WriteApi()
        fields = _field_index(api)
        need = {lbl for k in keys for lbl in plan[k]["custom_fields"]}
        missing = sorted(l for l in need if l not in fields)
        if missing:
            say(f"STOP: custom fields missing on the account: {missing}. Run `schema --commit` first.")
            return 2
        ok = fail = 0
        with open(log_path, "a", encoding="utf-8") as lf:
            for i, k in enumerate(keys, 1):
                entry = _push_one_api(api, plan[k], fields, a.commit)
                lf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                lf.flush()
                if entry.get("error"):
                    fail += 1
                    say(f"  [{i}/{len(keys)}] {plan[k]['street']}: ERROR {entry['error']}")
                    # match a real HTTP status, not any "403" in the text: the resolve
                    # failure message embeds the property ZIP, and MD zip 21403 aborted
                    # a 826-record run at record 179 (2026-09-01).
                    # The write-403 error strings start with "403" (no prefix word),
                    # so prose-matching missed them -- test the STRUCTURED status.
                    # (The old pattern also carried a literal 0x08 byte where a regex \b was
                    # meant, so it matched nothing, not even "record fetch 403".)
                    if entry.get("http_status") == 403:
                        say("  The internal API refuses writes on this account. Switch to --route wizard.")
                        return 3
                else:
                    ok += 1 if entry.get("ok") or not a.commit else 0
                    rb = entry.get("readback") or {}
                    say(f"  [{i}/{len(keys)}] {plan[k]['street']}: "
                        + (f"phones {len(rb.get('phones_landed', []))}/{len(plan[k]['phones'])}, "
                           f"REL {len(rb.get('custom_fields_landed', {}))}/{len(plan[k]['custom_fields'])}, "
                           f"tags missing {rb.get('tags_missing')}, owner {rb.get('owner_after')}"
                           + (" OK" if entry.get("ok") else " CHECK") if a.commit
                           else f"dry run: would write {entry.get('would_write')}"))
        say(f"done: {ok} ok, {fail} failed. Log: {log_path}")
        if a.probe and a.commit and keys:
            stored = (entry.get("readback") or {}).get("note_stored") or ""
            say("--- note as DataSift stored it "
                f"({stored.count(chr(10))} newlines, {len(stored)} chars) ---")
            for line in stored.splitlines() or [stored]:
                say("  | " + line)
            say("--- end note ---")
            if "\n" not in stored and stored:
                say("WARNING: no newline survived. The field collapsed the layout -- do NOT "
                    "bulk push; switch _note to a single-line separator first.")
            say(f"read back in full:  python src/scripts/dp_record_pull.py --uuid {entry.get('uuid')}")
        return 0 if not fail else 1

    # wizard route
    group_tags = {"deceased": [TAG_DP, TAG_DECEASED], "owner_alive": [TAG_DP, TAG_ALIVE],
                  "presumed_alive": [TAG_DP, TAG_PRESUMED],
                  "unverified": [TAG_DP, TAG_UNVERIFIED]}
    probe_key = keys[0] if a.probe else None
    groups = [plan[probe_key]["group"]] if probe_key else list(group_tags)
    results = {}
    for g in groups:
        say(f"wizard upload: reload_{g}.csv tags={group_tags[g]} finish={a.commit}")
        results[g] = _wizard_push(run, g, group_tags[g], a.commit, a.headed, probe_key)
        say(f"  -> {results[g]}")
        if not results[g].get("success"):
            say("  stopping: wizard did not reach a clean state")
            break
    if a.commit and all(r.get("success") for r in results.values()) and not probe_key:
        from sift_upload_wizard import run_phone_tag_upload  # noqa: E402
        say("phone-tag upload (Rel{N}.{M} + dial tiers)")
        r = asyncio.run(run_phone_tag_upload(str(run / "phone_tags.csv"), do_finish=True,
                                             headless=not a.headed, shot_base=str(run / "wizard_phone_tags")))
        say(f"  -> {r}")
        results["phone_tags"] = r
    with open(log_path, "a", encoding="utf-8") as lf:
        lf.write(json.dumps({"route": "wizard", "at": datetime.now().isoformat(timespec="seconds"),
                             "commit": a.commit, "probe": probe_key, "results": results}, ensure_ascii=False) + "\n")
    say("Wizard route note: REL custom fields only land if Map Columns maps them; run "
        "`push --route api --limit N` afterwards if the read-back shows them missing (API custom-field "
        "PATCH may work even where the wizard was needed for phones).")
    return 0


# ───────────────────────── main ─────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR))
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("prep")
    s.add_argument("--export", required=True)
    s.add_argument("--pilot", type=int, default=0)
    s.set_defaults(fn=cmd_prep)

    s = sub.add_parser("trace")
    s.add_argument("--csv", default="smartskip_input.csv")
    s.add_argument("--pay", action="store_true", help="bill the saved card after calculate")
    s.add_argument("--max-entities", type=int, default=0, help="refuse to pay above this many rows")
    s.add_argument("--wait", type=int, default=1800)
    s.add_argument("--download-id", help="download a finished order by bulkSkipId")
    s.add_argument("--pay-id", help="pay an order already submitted (calculated) by bulkSkipId, then poll + download")
    s.add_argument("--status", action="store_true")
    s.add_argument("--id", dest="bid")
    s.set_defaults(fn=cmd_trace)

    s = sub.add_parser("rank")
    s.add_argument("--tracerfy", action="store_true", help="Tracerfy gap-fill for phoneless signers ($0.02)")
    s.add_argument("--max-rels", type=int, default=15,
                   help="cap on the BLOOD/unknown relative tier (in-law channels are extra)")
    s.add_argument("--channel-cap", type=int, default=2,
                   help="in-laws kept as dial channels per record, after the blood tier")
    s.add_argument("--max-generic", type=int, default=50)
    s.add_argument("--min-score", type=float, default=30.0)
    s.add_argument("--keep", action="store_true", help="merge into an existing ranked_records.json")
    s.set_defaults(fn=cmd_rank)

    s = sub.add_parser("research")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--key")
    s.add_argument("--force", action="store_true")
    s.add_argument("--max-fetch", type=int, default=3)
    s.add_argument("--max-spouse", type=int, default=2)
    s.add_argument("--max-consecutive-errors", type=int, default=8,
                   help="abort if this many records in a row fail on a backend error")
    s.add_argument("--presume-alive-without-signal", action="store_true",
                   help="FTM-style cohorts: only research records carrying a deceased "
                        "signal (Probate/Obituary list, deceased-ish tag, obituary date, "
                        "PR); everything else gets the presumed-alive verdict for free")
    s.set_defaults(fn=cmd_research)

    s = sub.add_parser("augment")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_augment)

    s = sub.add_parser("score")
    s.add_argument("--litigator", action="store_true")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--max-score-per-rel", type=int, default=0,
                   help="0 = price every number on a kept contact (the default)")
    s.set_defaults(fn=cmd_score)

    s = sub.add_parser("build")
    s.add_argument("--max-rel-field-phones", type=int, default=3,
                   help="REL{n}: Phone 1..N custom fields only; the account has 3")
    s.add_argument("--max-rel-slots", type=int, default=MAX_REL_SLOTS,
                   help="REL slots per record (Basem: the existing 5 plus at most 2 more)")
    s.add_argument("--include-phoneless", action="store_true",
                   help="give relatives with no kept phone a REL slot anyway (name only)")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("schema")
    s.add_argument("--commit", action="store_true")
    s.add_argument("--max-rel", type=int, default=0)
    s.set_defaults(fn=cmd_schema)

    s = sub.add_parser("push")
    s.add_argument("--route", choices=["api", "wizard"], default="api")
    s.add_argument("--probe", nargs="?", const="__auto__", help="ONE record (optionally a key)")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--commit", action="store_true")
    s.add_argument("--force", action="store_true", help="re-push records already logged ok")
    s.add_argument("--headed", action="store_true")
    s.add_argument("--include-no-numbers", action="store_true",
                   help="also tag records that got no phones (DP No Numbers)")
    s.set_defaults(fn=cmd_push)

    a = ap.parse_args()
    if getattr(a, "probe", None) == "__auto__":
        a.probe = "__auto__"
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
