"""Fix the Incomplete records in the two Ready-to-Call lanes so the dialers can see them.

DataSift stamps every record `type = "clean" | "incomplete"`, and the Records UI's default
Clean tab HIDES the incomplete ones. Measured 2026-09-11: `FTM - 02 Ready to Call` held 197
incomplete of 1,306, `Hottest - 02 Ready to Call` 25 of 163 -- all with phones, 120 of them
deep prospected this month, and every FTM one assigned to a dialer who cannot see it.

What makes them incomplete (all 222 hydrated):
  A  entity owner -- `company` holds a trust/LLC title, `first_name` blank (A1: `last_name`
     blank too; A2: `last_name` is a 30-char truncated copy of the title)          185
  B  blank owner AND blank mailing (RoW probates whose PR sits only in the PR custom
     fields; Verified-Import foreclosures with no owner at all)                     15
  C  full person name yet flagged -- first name `Tracey M.` (initial + period),
     single-letter first, suffix in last (`Abell 4Th`), or a mailing block with null
     city/state/zip -- every one written by our own 09-01 pushes / Verified Imports   11
  D  `last_name` only, no company (mashed surnames like `Parker Dubois`)              5
  E  the PROPERTY address has no number/ZIP (`Lawson Rd`) -- no owner write fixes it   4
  F  owner named, mailing blank                                                        2

The fix is an owner-block PATCH; the job touches NOTHING else (no tags, lists, status,
assignee, phones, notes, REL/PR fields). Entities with no derivable person go to a worklist.

THE THREE WRITE TRAPS THIS SCRIPT IS BUILT AROUND
  1. `PATCH /api/internal/property/{uuid}/ {"owner": {...}}` renames the owner ENTITY in
     place (same owner uuid, phones survive) -- proven by addprop_owner_write.py 2026-09-07.
  2. A block WITHOUT the mailing address re-binds the record to a random existing entity
     ("Deborah Estep" on 7 properties, repair_pr_owners.py). Every write here carries the full
     block: first, last, company, address{street, city, state, postal_code}.
  3. A mailing string another owner entity already holds MERGES the two entities on PATCH
     (7901 Oakwood Rd absorbed into Arthur Drager, 2026-09-11). The account has duplicate
     property records at one street under different owners, and 161 of the 185 entities use
     the property address as mailing -- so the collision check runs before EVERY write, via
     the property `search` query (it matches mailing strings; the owner listing has no filter
     and a 10,000-offset ceiling, so a full index is not buildable). Read-back then requires
     the owner uuid, phone count and `skiptraced` unchanged; anything else is MERGE_SUSPECTED
     and the run stops.

Usage (FTM first, then hottest; one action per invocation):
    python src/scripts/lane_incomplete_cleanup.py --lane ftm --plan
    python src/scripts/lane_incomplete_cleanup.py --lane ftm --probe
    python src/scripts/lane_incomplete_cleanup.py --lane ftm --commit --group C
    python src/scripts/lane_incomplete_cleanup.py --lane ftm --commit --group A-diff
    python src/scripts/lane_incomplete_cleanup.py --lane ftm --commit --group A-same [--accept-one-way]
    python src/scripts/lane_incomplete_cleanup.py --lane ftm --commit --group B
    python src/scripts/lane_incomplete_cleanup.py --lane ftm --verify
    python src/scripts/lane_incomplete_cleanup.py --lane ftm --worklist
    python src/scripts/lane_incomplete_cleanup.py --lane ftm --undo <backup.json>
Every action after --plan reuses the newest run dir for the lane (or --run-dir).
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dpd_lane_assignee_report import count, list_uuids, resolve_lanes, to_query  # noqa: E402
from dpd_lane_rebalance import LiveApi as _LiveApi  # noqa: E402  (retrying mint)
from obituary_dp_batch import (  # noqa: E402
    ORDINALS, SUFFIXES, TRUST_WORDS, _gender_known, board_messages, custom_field_rows,
    norm_addr, parse_owner_blob, zip5,
)

LANES = {
    "ftm": ("05 FTM - CALL", "FTM - 02 Ready to Call"),
    "hottest": ("01 HOTTEST - CALL", "Hottest - 02 Ready to Call"),
}
# Incomplete count each lane held when this was designed (2026-09-11). Records leave a lane
# as dialers work it, so a small drift is normal; a large one means the picture changed and
# the plan should be re-read before anything is written.
EXPECT = {"ftm": 197, "hottest": 25}
DRIFT_TOLERANCE = 20

OUT_ROOT = ROOT / "output" / "lane_incomplete_cleanup"
NAME_MAP = ROOT / "output" / "dpd_assignee_name_map.json"

MAX_CONSECUTIVE_ERRORS = 8
CHECKPOINT_EVERY = 25
READBACK_WAITS = (0, 60, 180)          # seconds; `type` may be recomputed by an indexer

COMMIT_GROUPS = ("C", "F", "A-diff", "A-same", "B", "D")

NAME_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z'\-]+$")
INITIAL_FIRST_RE = re.compile(r"^(?P<first>[A-Za-z][A-Za-z'\-]+)\s+(?P<mi>[A-Za-z])\.?$")
STOPLIST = {"number", "estate", "family", "heritage", "living", "trust", "revocable", "the",
            "of", "and", "unknown", "available", "none", "null", "na"}
SUFFIX_IN_LAST_RE = re.compile(r"\b(\d+(st|nd|rd|th)|ii|iii|iv|jr|sr)\b\.?$", re.I)

# DP board note ("DEEP PROSPECTING ..." family), both the line-broken and the older
# "  ||  "-joined layout:  DECEDENT: Noel Salah - DOD 2026-08-20 (obituary)
DECEDENT_RE = re.compile(r"DECEDENT:\s*(?P<name>[^\n|]+?)\s*-\s*DOD\s*(?P<dod>[^(\n|]*)\((?P<src>[^)]*)\)")
VERDICT_RE = re.compile(r"VERDICT:\s*(?P<v>[^\n|]+)")
# PROBATE - Register of Wills note:  PR: Tracey Mulili - 205 Bel Aire Ln, Frederick, MD, 21702
PR_LINE_RE = re.compile(r"^PR:\s*(?P<name>[^-\n]+?)\s*-\s*(?P<street>[^,\n]+),\s*(?P<city>[^,\n]+),"
                        r"\s*(?P<state>[A-Z]{2}),?\s*(?P<zip>\d{5})", re.M)
# Register of Wills upload note (create_probates.py, 2026-09-28):
#   Personal Representative Paula A Boyer, 3916 Point Of Rocks Rd, Jefferson MD 21755-7500.
ROW_PR_RE = re.compile(r"Personal Representative\s+(?P<name>[^,\n]+?),\s*(?P<street>[^,\n]+),"
                       r"\s*(?P<city>[^,\n]+?)\s+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})")
# A truncated A2 last_name naming two co-trustees: "Lotfi Ali Tr & Lotfi Sedigheh".
RECORDER_PAIR_RE = re.compile(r"^\s*(?P<a>[A-Za-z'\-]+)\s+(?P<b>[A-Za-z'\-]+)\b[^&]*&\s*(?P<c>[A-Za-z'\-]+)")


def say(m: str) -> None:
    print(m, flush=True)


class Api(_LiveApi):
    def write(self, method: str, path: str, body, tries: int = 2):
        return self._call(path, method, body, None, tries)


# ───────────────────────── small helpers ─────────────────────────

def title(s: str) -> str:
    """Name casing that survives O'neil / Fitz-Patrick (addprop_owner_write.title)."""
    s = " ".join((s or "").split())
    return re.sub(r"(^|[\s\-'])([a-zA-Z])", lambda m: m.group(1) + m.group(2).upper(), s.lower())


def names(v) -> list[str]:
    return [x.get("name") if isinstance(x, dict) else str(x) for x in (v or []) if x]


def jdump(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def jload(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def name_of_map() -> dict[str, str]:
    if NAME_MAP.exists():
        return json.loads(NAME_MAP.read_text(encoding="utf-8")).get("name_of", {})
    return {}


def assignee_name(d: dict, name_of: dict) -> str:
    a = d.get("assigned_to")
    a = a.get("uuid") if isinstance(a, dict) else a
    return name_of.get(a, a) or ""


def owner_block(d: dict) -> dict:
    """The full owner block as the API wants it back."""
    o = d.get("owner") or {}
    a = o.get("address") or {}
    return {"first_name": o.get("first_name"), "last_name": o.get("last_name"),
            "company": o.get("company"),
            "address": {"street": a.get("street"), "city": a.get("city"),
                        "state": a.get("state"), "postal_code": a.get("postal_code")}}


def snapshot(d: dict) -> dict:
    o = d.get("owner") or {}
    return {"owner_uuid": o.get("uuid"), "block": owner_block(d), "type": d.get("type"),
            "phones": len(o.get("phones") or []), "skiptraced": o.get("skiptraced"),
            "deceased": o.get("deceased")}


def alpha_tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z]+", (s or "").lower()))


def clean_person_tokens(name: str) -> list[str]:
    """'Dolores V. Greene' -> ['Dolores', 'Greene']; drops initials, ordinals, suffixes."""
    out = []
    for t in re.split(r"[\s,]+", (name or "").strip()):
        t = t.strip(".")
        if len(t) < 2 or t.lower() in ORDINALS:
            continue
        out.append(t)
    return out


# ───────────────────────── run dir ─────────────────────────

def new_run_dir(lane: str) -> Path:
    p = OUT_ROOT / f"{lane}_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def latest_run_dir(lane: str, explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"run dir {p} does not exist")
        return p
    cands = sorted(OUT_ROOT.glob(f"{lane}_*")) if OUT_ROOT.exists() else []
    if not cands:
        raise SystemExit(f"no run dir for lane {lane!r}; run --plan first")
    return cands[-1]


# ───────────────────────── phase 1: cohort ─────────────────────────

def lane_query(api: Api, lane: str) -> tuple[dict, dict]:
    folder, preset = LANES[lane]
    info = resolve_lanes(api, [(folder, preset)])[0]
    return info, to_query(info["filters"])


def lane_counts(api: Api, q: dict) -> dict:
    baseline = count(api, {})
    qc = copy.deepcopy(q); qc["must"]["property_type"] = "clean"
    qi = copy.deepcopy(q); qi["must"]["property_type"] = "incomplete"
    out = {"account_total": baseline, "total": count(api, q),
           "clean": count(api, qc), "incomplete": count(api, qi)}
    # An unknown filter key is silently ignored and returns the account total (looks real).
    if out["total"] == baseline or out["incomplete"] == baseline or out["clean"] == baseline:
        raise RuntimeError(f"a lane count equals the account total {baseline}: filter ignored")
    if out["clean"] + out["incomplete"] != out["total"]:
        say(f"  note: clean {out['clean']} + incomplete {out['incomplete']} != total {out['total']} "
            "(index lag or a third type)")
    return out


def hydrate(api: Api, uuids: list[str], cache_path: Path) -> dict[str, dict]:
    """detail + custom fields + board messages per record; resumable by uuid."""
    cache = jload(cache_path, {})
    todo = [u for u in uuids if u not in cache]
    say(f"  hydrating {len(todo)} records ({len(cache)} cached)")
    t0 = time.time()
    for i, u in enumerate(todo, 1):
        st, d = api.get(f"/api/internal/property/{u}/")
        if st != 200:
            cache[u] = {"error": st}
            continue
        _rows, cf = custom_field_rows(api, u)
        msgs = [m.get("message") or "" for m in board_messages(api, u)]
        cache[u] = {"detail": d, "cf": {k: v for k, v in cf.items() if v not in (None, "", [])},
                    "messages": msgs}
        if i % CHECKPOINT_EVERY == 0 or i == len(todo):
            jdump(cache_path, cache)
            say(f"    {i}/{len(todo)}  {(time.time() - t0) / 60:.1f}m")
    return cache


# ───────────────────────── phase 2: classify + derive ─────────────────────────

def dp_decedent(messages: list[str]) -> dict | None:
    """The DP note's researched decedent, ONLY when the verdict is 'owner did die' on a real
    obituary. 'SiftMap obituary date, not the obituary' and 'unresolved' notes yield nothing."""
    for raw in messages:
        txt = (raw or "").replace("  ||  ", "\n")
        if not txt.lstrip().upper().startswith("DEEP PROSPECTING"):
            continue
        m, v = DECEDENT_RE.search(txt), VERDICT_RE.search(txt)
        if not (m and v):
            continue
        verdict = v.group("v").strip().lower()
        src = m.group("src").strip().lower()
        if not verdict.startswith("owner did die"):
            continue
        if "obituary" not in src or "not the obituary" in src or "siftmap" in src:
            continue
        toks = clean_person_tokens(m.group("name"))
        if len(toks) >= 2:
            return {"first": title(toks[0]), "last": title(toks[-1]), "raw": m.group("name").strip()}
    return None


def pr_line(messages: list[str]) -> dict | None:
    for raw in messages:
        txt = (raw or "").replace("  ||  ", "\n")
        if not txt.lstrip().upper().startswith("PROBATE"):
            continue
        m = PR_LINE_RE.search(txt)
        if m:
            toks = clean_person_tokens(m.group("name"))
            return {"first": title(toks[0]) if toks else "", "last": title(toks[-1]) if len(toks) > 1 else "",
                    "address": {"street": m.group("street").strip(), "city": m.group("city").strip(),
                                "state": m.group("state"), "postal_code": m.group("zip")}}
    return None


def row_pr_line(messages: list[str]) -> dict | None:
    """The PR + mailing from a Register of Wills upload note (the `Personal Representative
    <name>, <street>, <city> <ST> <zip>` sentence); falls back to the `PR:` line shape."""
    for raw in messages:
        m = ROW_PR_RE.search(raw or "")
        if m:
            toks = clean_person_tokens(m.group("name"))
            return {"first": title(toks[0]) if toks else "", "last": title(toks[-1]) if len(toks) > 1 else "",
                    "address": {"street": m.group("street").strip(), "city": m.group("city").strip(),
                                "state": m.group("state"), "postal_code": m.group("zip")}}
    return pr_line(messages)


def recorder_pair(last_name: str) -> tuple[str, str] | None:
    """'Lotfi Ali Tr & Lotfi Sedigheh' -> ('Ali', 'Lotfi'). Two co-trustees sharing their FIRST
    token fix that token as the SURNAME (recorder LAST FIRST order), which the title alone
    cannot. The 30-char truncation may cut the second surname ('... & Mitche'), so a prefix of
    at least 3 letters counts."""
    m = RECORDER_PAIR_RE.match(last_name or "")
    if not m:
        return None
    a, b, c = m.group("a"), m.group("b"), m.group("c")
    if len(c) >= 3 and a.lower().startswith(c.lower()) and b.lower() not in TRUST_WORDS \
            and b.lower() not in ORDINALS and len(b) > 1:
        return title(b), title(a)
    return None


def secondary_split(d: dict, surname: str) -> tuple[bool, list[str]]:
    """SiftMap imports write a recorder-style trustee ('RENZ MARISA S') as MANGLED
    secondary_owner rows: (None, 'Renz') then ('S', 'Marisa'). Returns (surname_seen, the
    given-name tokens that are not initials). Rows naming a real person with the surname as
    last_name ('Sook Han') are other family members and are skipped."""
    seen, given = False, []
    parts = {surname.lower()} | set(surname.lower().split("-"))   # 'Wavelet-Palmer' -> 'Palmer'
    for so in d.get("secondary_owners") or []:
        f, l = (so.get("first_name") or "").strip(), (so.get("last_name") or "").strip()
        if not f and not l:
            seen = True                    # an all-blank row: the surname row with nothing left
            continue
        if l and not f and l.lower() in parts:
            seen = True
            continue
        if f and l and l.lower() in parts:
            continue                       # a whole other person, not a split row
        for t in (f, l):
            if len(t) > 1 and t.lower() not in parts and NAME_TOKEN_RE.match(t) \
                    and t.lower() not in TRUST_WORDS:
                given.append(title(t))
    return seen, given


def rel_confirms(rec: dict, first: str, last: str) -> str:
    """The REL field that names exactly this person, if any ('REL1: LYNNE EDITH KARP')."""
    for lbl, val in (rec.get("cf") or {}).items():
        if lbl.startswith("REL") and lbl.endswith("Full Name") and isinstance(val, str):
            toks = [t.lower() for t in clean_person_tokens(val)]
            if toks and toks[0] == first.lower() and toks[-1] == last.lower():
                return f"{lbl.split(':')[0]}: {val}"
    return ""


def known_firsts(rec: dict) -> set[str]:
    """First tokens the record itself vouches for: REL fields, secondary owners, PR field."""
    out = set()
    for lbl, val in (rec.get("cf") or {}).items():
        if lbl.startswith("REL") and lbl.endswith("Full Name") and isinstance(val, str):
            toks = clean_person_tokens(val)
            if toks:
                out.add(toks[0].lower())
    for so in (rec["detail"].get("secondary_owners") or []):
        f = (so.get("first_name") or "").strip()
        if len(f) > 1:
            out.add(f.lower())
    pf = (rec.get("cf") or {}).get("PR First Name")
    if isinstance(pf, str):
        toks = clean_person_tokens(pf)
        if toks:
            out.add(toks[0].lower())
    return out


def rel_last_tokens(rec: dict) -> set[str]:
    out = set()
    for lbl, val in (rec.get("cf") or {}).items():
        if lbl.startswith("REL") and lbl.endswith("Full Name") and isinstance(val, str):
            toks = clean_person_tokens(val)
            if toks:
                out.add(toks[-1].lower())
    return out


def gate(first: str, last: str, rec: dict, extra_firsts: set[str] = frozenset()) -> str:
    """'' when the derived name passes every sanity check, else the reason it needs eyes."""
    d = rec["detail"]
    if not (first and last):
        return "name_incomplete"
    for t in (first, last):
        if not NAME_TOKEN_RE.match(t) or len(t) < 2:
            return f"token_shape:{t}"
        if t.lower() in STOPLIST:
            return f"stoplist:{t}"
    street_toks = alpha_tokens((d.get("address") or {}).get("street")) | \
        alpha_tokens(((d.get("owner") or {}).get("address") or {}).get("street"))
    for t in (first, last):
        if t.lower() in street_toks:
            return f"street_token:{t}"
    if not (_gender_known(first) or first.lower() in known_firsts(rec) or first.lower() in extra_firsts):
        return f"first_unrecognised:{first}"
    return ""


def order_ambiguous(company: str, first: str, last: str) -> bool:
    """A two-token title with no initial ('Lowry Tucker Living Trust', 'Lotfi Ali Trust') where
    the token we took as the SURNAME also reads as a given name: the parser had nothing but a
    style default to decide the order, so a human should."""
    low = " " + re.sub(r"[.,()/]", " ", (company or "").lower()) + " "
    if re.search(r"\s(?:&|and)\s", low):
        return False
    toks = low.split()
    if any(len(t) == 1 for t in toks):
        return False                       # an initial fixes the order
    human = [t for t in toks if t not in TRUST_WORDS and t not in ORDINALS
             and t not in SUFFIXES and not t.isdigit()]
    if len(human) != 2:
        return False
    words = set(toks)
    long_form = bool(words & {"trust", "living", "revocable", "irrevocable", "amended",
                              "restated", "residuary", "family", "declaration", "agreement"})
    ends_abbrev = bool(re.search(r"\b(tr|trs|ttee|ttees|trustee|trustees)\s*$", low.strip()))
    return long_form and not ends_abbrev   # recorder style (LAST FIRST Tr) is reliable


def normalise_first(first: str) -> tuple[str, str]:
    """('Tracey M.' -> ('Tracey M', 'Tracey')); anything else unchanged with no fallback."""
    m = INITIAL_FIRST_RE.match((first or "").strip())
    if m:
        return f"{title(m.group('first'))} {m.group('mi').upper()}", title(m.group("first"))
    return first, ""


def property_address_incomplete(d: dict) -> bool:
    a = d.get("address") or {}
    street = (a.get("street") or "").strip()
    return not street or not re.match(r"^\d", street) or not (a.get("postal_code") or "").strip()


def mailing_incomplete(block: dict) -> bool:
    a = block["address"]
    return not all((a.get(k) or "").strip() for k in ("street", "city", "state", "postal_code"))


def classify(rec: dict) -> str:
    d = rec["detail"]
    o = d.get("owner") or {}
    first, last, co = (o.get("first_name") or "").strip(), (o.get("last_name") or "").strip(), \
        (o.get("company") or "").strip()
    mail_street = ((o.get("address") or {}).get("street") or "").strip()
    if property_address_incomplete(d):
        return "E"
    if co and not first:
        return "A"
    if co and first and (not last or last.lower() in co.lower()):
        return "A"           # a person first name beside a truncated title
    if not first and not last:
        return "B"
    if not first and last:
        return "D"
    if not mail_street:
        return "F"
    return "C"


def derive(rec: dict, name_of: dict) -> dict:
    """One plan row: group, bucket (write|review|worklist), reason, the planned block."""
    d = rec["detail"]
    before = snapshot(d)
    o = d.get("owner") or {}
    addr = d.get("address") or {}
    group = classify(rec)
    row = {
        "uuid": d["uuid"], "street": addr.get("street"), "city": addr.get("city"),
        "zip": addr.get("postal_code"), "county": addr.get("county"),
        "assignee": assignee_name(d, name_of), "group": group, "subgroup": "", "bucket": "worklist",
        "reason": "", "source": "",
        "before_first": before["block"]["first_name"], "before_last": before["block"]["last_name"],
        "before_company": before["block"]["company"],
        "before_mailing": before["block"]["address"]["street"],
        "planned_first": "", "planned_last": "", "planned_first_fallback": "",
        "mailing_changed": False, "planned_block": None, "phones": before["phones"],
        "collision_owner_uuids": [], "hint": "",
    }
    block = copy.deepcopy(before["block"])
    prop_mail = {"street": addr.get("street"), "city": addr.get("city"),
                 "state": addr.get("state"), "postal_code": addr.get("postal_code")}

    def plan_write(first, last, source, bucket="write", reason=""):
        block["first_name"], block["last_name"] = first, last
        row.update(planned_first=first, planned_last=last, source=source, bucket=bucket,
                   reason=reason, planned_block=block)

    if group == "E":
        row["reason"] = "property_address_incomplete"
        return row

    if group == "A":
        co = block["company"] or ""
        row["subgroup"] = "A-same" if norm_addr(block["address"]["street"]) == norm_addr(addr.get("street")) \
            else "A-diff"
        if not block["last_name"]:
            row["subgroup"] += "/A1"
        else:
            row["subgroup"] += "/A2"
        if "&" in co or re.search(r"\band\b", co, re.I):
            row["subgroup"] += "/two"
        # Register of Wills probate: the PR is the person to name (Basem's rule for MD/DC/VA
        # probates; 4911 Ridge Crest Ct 2026-09-28), the trust title stays in `company`.
        cf = rec.get("cf") or {}
        pf, pl = cf.get("PR First Name"), cf.get("PR Last Name")
        if isinstance(pf, str) and isinstance(pl, str) and pf.strip() and pl.strip() \
                and any("register of wills" in (m or "").lower() for m in rec.get("messages") or []):
            ft, lt = clean_person_tokens(pf), clean_person_tokens(pl)
            if ft and lt:
                first, last = title(ft[0]), title(" ".join(lt))
                pr = row_pr_line(rec.get("messages") or [])
                if pr and pr["last"].lower() == last.lower() and pr["address"]["street"]:
                    block["address"] = pr["address"]
                    row["mailing_changed"] = True
                why = gate(first, last, rec)
                row["hint"] = "PR wins over the trust title (Register of Wills record)"
                plan_write(first, last, "pr_fields", "review" if why else "write", why)
                return row
        parsed = parse_owner_blob(co)
        title_toks = alpha_tokens(co)
        dp = dp_decedent(rec.get("messages") or [])
        pair = recorder_pair(block["last_name"] or "")
        first = last = source = ""
        if parsed["source"] == "entity_company" or not (parsed["first"] and parsed["last"]):
            # No person in the title ('Renz Family Trust'): SiftMap's mangled secondary-owner
            # rows may still carry the trustee's given name beside the title's surname.
            surname = title(parsed["last"] or "")
            seen, given = secondary_split(d, surname) if surname else (False, [])
            if surname and seen and given:
                first = given[0]
                why = gate(first, surname, rec, {g.lower() for g in given})
                conf = rel_confirms(rec, first, surname)
                row["hint"] = (f"secondary rows -> given {given!r} + surname {surname}"
                               + (f"; {conf} confirms" if conf else "")
                               + ("; ORDER UNSURE, two given tokens" if len(given) > 1 else ""))
                plan_write(first, surname, "secondary_split", "review",
                           why or ("secondary_split_order" if len(given) > 1 else "secondary_split_guess"))
                return row
            row["reason"] = "entity_no_person_name"
            row["hint"] = f"parsed={parsed['first']} {parsed['last']} ({parsed['confidence']})"
            return row
        if dp and dp["last"].lower() in title_toks and \
                (not parsed["first"] or dp["first"].lower() in title_toks):
            first, last, source = dp["first"], dp["last"], "dp_note"
        elif pair and pair[1].lower() in title_toks:
            first, last, source = pair[0], pair[1], "recorder_pair"
        else:
            first, last, source = title(parsed["first"]), title(parsed["last"]), "trust_title"
        why = gate(first, last, rec, {dp["first"].lower()} if dp else set())
        if not why and source == "trust_title" and order_ambiguous(co, first, last):
            # a last-name-only secondary row names the SURNAME ('Lowry Tucker Living Trust'
            # beside (None, 'Tucker')): that settles the order the title could not
            seen_l, _ = secondary_split(d, last)
            seen_f, _ = secondary_split(d, first)
            if seen_l and not seen_f:
                source = "trust_title+secondary"
            elif seen_f and not seen_l:
                first, last, source = last, first, "trust_title+secondary"
                why = gate(first, last, rec, {dp["first"].lower()} if dp else set())
            else:
                why = f"order_ambiguous:{first} {last}"
        if mailing_incomplete(block):
            plan_write(first, last, source, "worklist", "mailing_unparseable")
            return row
        plan_write(first, last, source, "review" if why else "write", why)
        return row

    if group == "B":
        cf = rec.get("cf") or {}
        pf, pl = cf.get("PR First Name"), cf.get("PR Last Name")
        if not (isinstance(pf, str) and isinstance(pl, str) and pf.strip() and pl.strip()):
            row["reason"] = "no_pr_fields"
            row["hint"] = (rec.get("messages") or [""])[0][:200].replace("\n", " | ")
            return row
        if any(("," in x) or ("pr." in x.lower()) for x in (pf, pl)):
            row["reason"] = "pr_field_malformed"
            row["hint"] = f"PR fields: {pf!r} / {pl!r}"
            return row
        ft, lt = clean_person_tokens(pf), clean_person_tokens(pl)
        if not (ft and lt):
            row["reason"] = "pr_field_malformed"
            return row
        first, last = title(ft[0]), title(" ".join(lt))
        if not block["address"]["street"]:
            pr = pr_line(rec.get("messages") or [])
            if pr and pr["last"].lower() == last.lower():
                block["address"] = pr["address"]
            else:
                block["address"] = dict(prop_mail)
            row["mailing_changed"] = True
        why = gate(first, last, rec)
        plan_write(first, last, "pr_fields", "review" if why else "write", why)
        return row

    if group == "D":
        so = (d.get("secondary_owners") or [{}])[0]
        sf, sl = (so.get("first_name") or "").strip(), (so.get("last_name") or "").strip()
        own_last = (block["last_name"] or "").strip()
        if sf and sl.lower() == own_last.lower() and _gender_known(sf) \
                and sf.lower() not in rel_last_tokens(rec) and NAME_TOKEN_RE.match(sf):
            plan_write(title(sf), title(own_last), "secondary_owner", "review", "secondary_owner_guess")
        else:
            row["reason"] = "needs_manual_name"
            row["hint"] = f"secondary={sf} {sl}; last={own_last}"
        return row

    if group == "F":
        f_first, f_fallback = normalise_first(block["first_name"])
        row["planned_first_fallback"] = f_fallback
        if f_fallback:
            row["subgroup"] = "F+C-initial"
        pr = pr_line(rec.get("messages") or [])
        if pr and pr["last"].lower() == (block["last_name"] or "").lower():
            block["address"] = pr["address"]
            row["mailing_changed"] = True
            plan_write(f_first, block["last_name"], "pr_line_mailing")
        else:
            block["address"] = dict(prop_mail)
            row["mailing_changed"] = True
            plan_write(f_first, block["last_name"], "property_as_mailing", "review",
                       "mailing_guessed_property")
        return row

    # group C -- named but flagged
    first, last = (block["first_name"] or "").strip(), (block["last_name"] or "").strip()
    m = INITIAL_FIRST_RE.match(first)
    if SUFFIX_IN_LAST_RE.search(last):
        row["reason"] = "needs_manual_name"
        row["hint"] = f"suffix in last_name {last!r}"
        row["subgroup"] = "C-suffix"
        return row
    if mailing_incomplete(block):
        pr = pr_line(rec.get("messages") or [])
        if pr and pr["last"].lower() == last.lower():
            block["address"] = pr["address"]
            row["mailing_changed"] = True
            row["subgroup"] = "C-mailing"
        else:
            row["reason"] = "mailing_unparseable"
            row["subgroup"] = "C-mailing"
            row["hint"] = f"mailing={block['address']}"
            return row
    if m:
        row["subgroup"] = (row["subgroup"] + "+" if row["subgroup"] else "") + "C-initial"
        row["planned_first_fallback"] = title(m.group("first"))
        plan_write(f"{title(m.group('first'))} {m.group('mi').upper()}", last, "drop_period")
        return row
    if len(first) < 2:
        row["reason"] = "needs_manual_name"
        row["subgroup"] = "C-initial-only"
        row["hint"] = f"first={first!r}"
        return row
    if row["subgroup"] == "C-mailing":
        plan_write(first, last, "pr_line_mailing")
        return row
    row["reason"] = "flag_cause_unknown"
    row["subgroup"] = "C-unknown"
    return row


PLAN_COLS = ["uuid", "street", "city", "zip", "county", "assignee", "group", "subgroup", "bucket",
             "reason", "source", "before_first", "before_last", "before_company", "before_mailing",
             "planned_first", "planned_first_fallback", "planned_last", "mailing_changed", "phones",
             "collision_owner_uuids", "hint"]


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r2 = dict(r)
            if isinstance(r2.get("collision_owner_uuids"), list):
                r2["collision_owner_uuids"] = ";".join(r2["collision_owner_uuids"])
            w.writerow(r2)


# ───────────────────────── collision check ─────────────────────────

def colliding_owners(api: Api, own_owner_uuid: str, street: str, postal: str) -> list[str]:
    """Owner uuids OTHER than ours whose mailing equals the target string, found through the
    property search (which matches mailing strings). Any hit means: do not write."""
    if not street:
        return []
    want, want_zip = norm_addr(street), zip5(postal or "")
    out, offset = set(), 0
    while True:
        st, body = api.post_as_get("/api/internal/property/",
                                   {"limit": 100, "offset": offset,
                                    "query": {"must": {"search": street}}})
        if st != 200:
            raise RuntimeError(f"collision search failed {st}: {str(body)[:200]}")
        rows = (body or {}).get("results") or []
        for r in rows:
            o = r.get("owner") or {}
            ma = o.get("address") if isinstance(o.get("address"), dict) else {}
            if not o.get("uuid") or o["uuid"] == own_owner_uuid:
                continue
            if norm_addr(ma.get("street")) != want:
                continue
            hz = zip5(ma.get("postal_code") or "")
            if want_zip and hz and hz != want_zip:
                continue
            out.add(o["uuid"])
        offset += 100
        if not rows or offset >= min((body or {}).get("count") or 0, 2000):
            break
    return sorted(out)


# ───────────────────────── actions ─────────────────────────

def cmd_plan(a) -> int:
    api = Api()
    lane = a.lane
    run = Path(a.run_dir) if a.run_dir else new_run_dir(lane)   # --run-dir reuses its cohort cache
    run.mkdir(parents=True, exist_ok=True)
    say(f"run dir: {run}")
    info, q = lane_query(api, lane)
    counts = lane_counts(api, q)
    say(f"{info['preset']}: total={counts['total']} clean={counts['clean']} "
        f"incomplete={counts['incomplete']} (account {counts['account_total']})")
    drift = abs(counts["incomplete"] - EXPECT[lane])
    if drift > DRIFT_TOLERANCE and not a.allow_drift:
        say(f"ABORT: incomplete count drifted {drift} from the pinned {EXPECT[lane]}; re-read the "
            "lane and pass --allow-drift if that is expected")
        return 3
    qi = copy.deepcopy(q); qi["must"]["property_type"] = "incomplete"
    uuids = list_uuids(api, qi, counts["incomplete"])
    say(f"  {len(uuids)} incomplete uuids paged")
    cohort = hydrate(api, uuids, run / "cohort.json")
    name_of = name_of_map()

    rows = []
    for u in uuids:
        rec = cohort.get(u) or {}
        if "detail" not in rec:
            rows.append({"uuid": u, "group": "?", "bucket": "worklist", "reason": f"hydrate_error_{rec.get('error')}"})
            continue
        rows.append(derive(rec, name_of))

    # collision check on every row that would be written
    say("  collision check on write/review rows ...")
    for r in rows:
        if r["bucket"] in ("write", "review") and r.get("planned_block"):
            own = ((cohort[r["uuid"]]["detail"].get("owner") or {}).get("uuid")) or ""
            tgt = r["planned_block"]["address"]
            hits = colliding_owners(api, own, tgt.get("street"), tgt.get("postal_code"))
            if hits:
                r["collision_owner_uuids"] = hits
                r["bucket"], r["reason"] = "worklist", "mailing_collision"

    jdump(run / "plan.json", {"lane": lane, "preset": info, "counts": counts, "rows": rows,
                              "built": datetime.now().isoformat()})
    write_csv(run / "plan.csv", rows, PLAN_COLS)
    tally = Counter((r["group"], r["bucket"]) for r in rows)
    say("\n  group x bucket:")
    for (g, b), n in sorted(tally.items()):
        say(f"    {g:3} {b:9} {n}")
    say("  buckets: " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(r['bucket'] for r in rows).items())))
    say("  worklist reasons: " + ", ".join(f"{k}={v}" for k, v in
                                          Counter(r["reason"] for r in rows if r["bucket"] == "worklist").most_common()))
    say("  review reasons: " + ", ".join(f"{k}={v}" for k, v in
                                        Counter(r["reason"].split(":")[0] for r in rows if r["bucket"] == "review").most_common()))
    say(f"\n  plan.csv -> {run / 'plan.csv'}   (nothing written to the account)")
    return 0


def load_plan(run: Path) -> dict:
    p = jload(run / "plan.json", None)
    if not p:
        raise SystemExit(f"{run} has no plan.json; run --plan")
    return p


def read_detail(api: Api, uuid: str) -> dict:
    st, d = api.get(f"/api/internal/property/{uuid}/")
    if st != 200:
        raise RuntimeError(f"detail GET {uuid} -> {st}")
    return d


def apply_row(api: Api, row: dict, state: dict, backup: dict, *, timed: bool,
              block_override: dict | None = None) -> dict:
    """The one write path: re-read, collision check, snapshot, PATCH, read back. Returns a log."""
    uuid = row["uuid"]
    log = {"uuid": uuid, "street": row["street"], "group": row["group"], "ok": False, "steps": []}
    d = read_detail(api, uuid)
    before = snapshot(d)
    if before["type"] == "clean":
        log.update(skipped="already_clean", ok=True)
        return log
    live_block = before["block"]
    if (live_block["first_name"], live_block["last_name"], live_block["company"]) != \
            (row["before_first"], row["before_last"], row["before_company"]):
        log["skipped"] = "owner_changed_since_plan"
        return log
    block = copy.deepcopy(block_override or row["planned_block"])
    hits = colliding_owners(api, before["owner_uuid"], block["address"].get("street"),
                            block["address"].get("postal_code"))
    if hits:
        log.update(skipped="mailing_collision", collisions=hits)
        return log
    if uuid not in backup:
        backup[uuid] = {"before": before, "planned": block, "at": datetime.now().isoformat()}
    st, resp = api.write("PATCH", f"/api/internal/property/{uuid}/", {"owner": block})
    log["steps"].append(("patch", st, str(resp)[:200] if st != 200 else ""))
    if st != 200:
        log["error"] = f"patch {st}: {str(resp)[:200]}"
        return log
    waits = READBACK_WAITS if timed else (0,)
    for i, w in enumerate(waits):
        if w:
            time.sleep(w - (waits[i - 1] if i else 0))
        after = snapshot(read_detail(api, uuid))
        log["after"] = after
        log["steps"].append(("readback", w, after["type"]))
        if after["owner_uuid"] != before["owner_uuid"] or after["phones"] < before["phones"] \
                or after["skiptraced"] != before["skiptraced"]:
            log["error"] = ("MERGE_SUSPECTED: owner uuid/phones/skiptraced changed "
                            f"{before['owner_uuid']}->{after['owner_uuid']} phones "
                            f"{before['phones']}->{after['phones']} skiptraced "
                            f"{before['skiptraced']}->{after['skiptraced']}")
            return log
        if after["phones"] > before["phones"]:
            # Same entity, MORE phones: an ORPHAN owner entity (no property of its own, so the
            # collision search cannot see it) carrying the same name + mailing was absorbed into
            # ours. Seen live on 9925 Old Frederick Rd 2026-09-11: the PR's own `Pr.1..4` numbers
            # joined the record. No record is re-pointed; logged, not fatal.
            new_nums = [(p.get("number"), names(p.get("tags")))
                        for p in ((read_detail(api, uuid).get("owner") or {}).get("phones") or [])]
            log["warning"] = f"ORPHAN_ABSORBED: phones {before['phones']}->{after['phones']}"
            log["phones_after"] = new_nums
        got = (after["block"]["first_name"] or "", after["block"]["last_name"] or "")
        want = (block["first_name"] or "", block["last_name"] or "")
        if tuple(x.strip().lower() for x in got) != tuple(x.strip().lower() for x in want):
            log["error"] = f"read-back name mismatch {got} != {want}"
            return log
        if after["type"] == "clean":
            log["ok"] = True
            return log
    log["error"] = f"type still {log['after']['type']} after {waits[-1]}s"
    return log


def cmd_probe(a) -> int:
    run = latest_run_dir(a.lane, a.run_dir)
    plan = load_plan(run)
    rows = [r for r in plan["rows"] if r["bucket"] == "write" and r.get("planned_block")]
    api = Api()
    state = jload(run / "state.json", {"probe": {}, "committed": {}})
    backup = jload(run / "backup.json", {})

    def pick(pred, label):
        for r in rows:
            if pred(r) and r["uuid"] not in state["probe"]:
                return r
        say(f"  (no candidate for probe {label})")
        return None

    shapes = [
        ("A1-same", lambda r: r["group"] == "A" and r["subgroup"].startswith("A-same") and "/A1" in r["subgroup"] and "/two" not in r["subgroup"]),
        ("A2", lambda r: r["group"] == "A" and "/A2" in r["subgroup"]),
        ("A-two", lambda r: r["group"] == "A" and "/two" in r["subgroup"]),
        ("A-diff", lambda r: r["group"] == "A" and r["subgroup"].startswith("A-diff")),
        ("C-initial", lambda r: r["group"] == "C" and "C-initial" in r["subgroup"]),
        ("B", lambda r: r["group"] == "B"),
    ]
    for label, pred in shapes:
        r = pick(pred, label)
        if not r:
            continue
        say(f"\nPROBE {label}: {r['street']} | {r['before_first']} {r['before_last']} co={r['before_company']} "
            f"-> {r['planned_first']} {r['planned_last']}")
        log = apply_row(api, r, state, backup, timed=True)
        if label == "A1-same" and not log["ok"] and log.get("error", "").startswith("type still"):
            say("  type did not flip with company kept; retrying with company omitted")
            blk = copy.deepcopy(r["planned_block"]); blk.pop("company", None)
            log2 = apply_row(api, r, state, backup, timed=True, block_override=blk)
            log["company_omitted_retry"] = log2
            if log2["ok"]:
                state["company_variant"] = "omit"
                log = log2
        elif label == "A1-same" and log["ok"]:
            state["company_variant"] = "keep"
        if label == "C-initial" and not log["ok"] and log.get("error", "").startswith("type still"):
            say("  'Xxx M' did not flip; retrying with the initial dropped")
            blk = copy.deepcopy(r["planned_block"]); blk["first_name"] = r["planned_first_fallback"]
            log2 = apply_row(api, r, state, backup, timed=True, block_override=blk)
            log["initial_dropped_retry"] = log2
            if log2["ok"]:
                state["c_variant"] = "drop_initial"
                log = log2
        elif label == "C-initial" and log["ok"]:
            state["c_variant"] = "keep_initial"
        state["probe"][r["uuid"]] = {"shape": label, **log}
        jdump(run / "backup.json", backup)
        jdump(run / "state.json", state)
        say(f"  -> {'OK' if log['ok'] else 'FAIL'} {log.get('error') or log.get('skipped') or ''} "
            f"steps={log['steps']}")
        if log.get("error", "").startswith("MERGE_SUSPECTED"):
            say("STOP: merge suspected on the probe; undo with --undo " + str(run / "backup.json"))
            return 4
        if label == "A1-same" and log["ok"]:
            state["undo_variant"] = undo_rehearsal(api, r, backup[r["uuid"]]["before"]["block"], state)
            jdump(run / "state.json", state)
            # leave the probe record FIXED whichever way the rehearsal went
            relog = apply_row(api, r, state, backup, timed=True)
            say(f"  re-applied fix after rehearsal: {'OK' if relog['ok'] else relog.get('error') or relog.get('skipped')}")

    ok = sum(1 for p in state["probe"].values() if p.get("ok"))
    say(f"\nPROBE SUMMARY: {ok}/{len(state['probe'])} flipped to clean; company_variant="
        f"{state.get('company_variant')} c_variant={state.get('c_variant')} undo_variant={state.get('undo_variant')}")
    if state.get("undo_variant") is None:
        say("  group A renames are ONE-WAY on this API (no variant blanked the names); "
            "--commit --group A-* will require --accept-one-way")
    return 0 if ok == len(state["probe"]) and ok else 2


def undo_rehearsal(api: Api, row: dict, before_block: dict, state: dict) -> str | None:
    """Can a renamed entity be blanked again? Try '', None, omitted -- report what STORES."""
    uuid = row["uuid"]
    for variant in ("empty", "null", "omit"):
        blk = copy.deepcopy(before_block)
        if variant == "empty":
            blk["first_name"], blk["last_name"] = blk["first_name"] or "", blk["last_name"] or ""
        elif variant == "null":
            blk["first_name"], blk["last_name"] = blk["first_name"] or None, blk["last_name"] or None
        else:
            for k in ("first_name", "last_name"):
                if not blk.get(k):
                    blk.pop(k, None)
        st, resp = api.write("PATCH", f"/api/internal/property/{uuid}/", {"owner": blk})
        after = snapshot(read_detail(api, uuid))
        got = (after["block"]["first_name"] or "", after["block"]["last_name"] or "")
        want = (before_block["first_name"] or "", before_block["last_name"] or "")
        say(f"  undo rehearsal variant={variant}: PATCH {st}; names now {got} (want {want}); "
            f"owner uuid same={after['owner_uuid'] == state['probe'][uuid]['after']['owner_uuid']}")
        if st == 200 and got == want:
            return variant
    return None


def cmd_commit(a) -> int:
    run = latest_run_dir(a.lane, a.run_dir)
    plan = load_plan(run)
    state = jload(run / "state.json", {"probe": {}, "committed": {}})
    backup = jload(run / "backup.json", {})
    if not state.get("probe") or not any(p.get("ok") for p in state["probe"].values()):
        say("refusing: no successful --probe recorded for this run")
        return 2
    g = a.group
    if g.startswith("A") and state.get("undo_variant") is None and not a.accept_one_way:
        say("refusing: the probe showed group A renames cannot be undone to blank names; "
            "pass --accept-one-way to proceed")
        return 2
    if g == "C" and not state.get("c_variant"):
        say("refusing: the probe did not settle the group C first-name variant")
        return 2

    def in_group(r):
        if g in ("A-same", "A-diff"):
            return r["group"] == "A" and r["subgroup"].startswith(g)
        return r["group"] == g

    rows = [r for r in plan["rows"] if in_group(r) and r.get("planned_block")
            and (r["bucket"] == "write" or (a.include_review and r["bucket"] == "review"))
            and not (state["committed"].get(r["uuid"]) or {}).get("ok")
            and r["uuid"] not in state["probe"]]
    say(f"commit group {g}: {len(rows)} rows ({'incl. review' if a.include_review else 'write only'})")
    api = Api()
    consec, done, t0 = 0, 0, time.time()
    for i, r in enumerate(rows, 1):
        override = None
        if g == "C" and state.get("c_variant") == "drop_initial" and r.get("planned_first_fallback"):
            override = copy.deepcopy(r["planned_block"]); override["first_name"] = r["planned_first_fallback"]
        if r["group"] == "A" and state.get("company_variant") == "omit":
            override = copy.deepcopy(override or r["planned_block"]); override.pop("company", None)
        try:
            log = apply_row(api, r, state, backup, timed=False, block_override=override)
        except Exception as ex:  # noqa: BLE001
            log = {"uuid": r["uuid"], "street": r["street"], "ok": False, "error": f"{type(ex).__name__}: {ex}"}
        state["committed"][r["uuid"]] = log
        if log.get("ok"):
            consec, done = 0, done + 1
            say(f"  [{i}/{len(rows)}] ok {r['street']}: {r['planned_first']} {r['planned_last']}"
                + (" (already clean)" if log.get("skipped") else "")
                + (f"  WARNING {log['warning']}" if log.get("warning") else ""))
        else:
            consec += 1
            say(f"  [{i}/{len(rows)}] {log.get('skipped') or 'FAIL'} {r['street']}: {log.get('error') or ''}")
        if i % CHECKPOINT_EVERY == 0 or i == len(rows):
            jdump(run / "backup.json", backup); jdump(run / "state.json", state)
        if str(log.get("error", "")).startswith("MERGE_SUSPECTED"):
            jdump(run / "backup.json", backup); jdump(run / "state.json", state)
            say(f"STOP: merge suspected. Undo: --lane {a.lane} --undo {run / 'backup.json'}")
            return 4
        if consec >= MAX_CONSECUTIVE_ERRORS:
            jdump(run / "backup.json", backup); jdump(run / "state.json", state)
            say(f"ABORT: {consec} consecutive failures")
            return 3
    # type-lag sweep: rows that were written but read back still incomplete at +0s
    lag = [u for u, l in state["committed"].items() if not l.get("ok") and str(l.get("error", "")).startswith("type still")]
    if lag:
        say(f"  {len(lag)} rows read back incomplete immediately; re-checking after 120s")
        time.sleep(120)
        for u in lag:
            after = snapshot(read_detail(api, u))
            if after["type"] == "clean":
                state["committed"][u]["ok"] = True
                state["committed"][u]["error"] = ""
                state["committed"][u]["after"] = after
        jdump(run / "state.json", state)
    okc = sum(1 for l in state["committed"].values() if l.get("ok"))
    say(f"DONE group {g}: ok={okc} of {len(state['committed'])} committed so far; "
        f"{(time.time() - t0) / 60:.1f}m; backup={run / 'backup.json'}")
    return 0


def cmd_verify(a) -> int:
    run = latest_run_dir(a.lane, a.run_dir)
    plan = load_plan(run)
    state = jload(run / "state.json", {"probe": {}, "committed": {}})
    api = Api()
    info, q = lane_query(api, a.lane)
    counts = lane_counts(api, q)
    written = {**{u: p for u, p in state["probe"].items() if p.get("ok")},
               **{u: c for u, c in state["committed"].items() if c.get("ok")}}
    worklist_n = sum(1 for r in plan["rows"] if r["bucket"] == "worklist")
    review_left = sum(1 for r in plan["rows"] if r["bucket"] == "review" and r["uuid"] not in written)
    say(f"{info['preset']}: total={counts['total']} clean={counts['clean']} incomplete={counts['incomplete']}")
    say(f"  planned: written {len(written)}, worklist {worklist_n}, review not written {review_left}; "
        f"expected incomplete ~{worklist_n + review_left} (index lags 1-2 min)")
    defects, collapsed, warnings = [], [], []
    backup_all = jload(run / "backup.json", {})
    for u, w in written.items():
        if w.get("warning"):
            warnings.append((u, w["warning"]))
        st, d = api.get(f"/api/internal/property/{u}/")
        if st == 404:
            # DataSift collapses duplicate property records (same address, same owner) when the
            # owner is renamed -- seen live on 4803 S Polling House Rd 2026-09-11. The twin
            # survives; report it, do not count it as a defect.
            collapsed.append(u)
            continue
        if st != 200:
            defects.append((u, f"detail {st}"))
            continue
        after = snapshot(d)
        b = (backup_all.get(u) or {}).get("before") or {}
        if after["type"] != "clean":
            defects.append((u, f"type {after['type']}"))
        if b and after["owner_uuid"] != b.get("owner_uuid"):
            defects.append((u, "owner uuid changed"))
        if b and after["phones"] != b.get("phones"):
            defects.append((u, f"phones {b.get('phones')}->{after['phones']}"))
        if b and after["skiptraced"] != b.get("skiptraced"):
            defects.append((u, f"skiptraced {b.get('skiptraced')}->{after['skiptraced']}"))
    jdump(run / "verify.json", {"counts": counts, "written": len(written), "worklist": worklist_n,
                                "review_left": review_left, "defects": defects,
                                "collapsed_duplicates": collapsed, "warnings": warnings,
                                "at": datetime.now().isoformat()})
    say(f"  per-record: {len(written) - len({u for u, _ in defects}) - len(collapsed)}/{len(written)} "
        f"clean and intact; defects={len(defects)}; collapsed duplicates={len(collapsed)}; "
        f"orphan absorptions={len(warnings)}")
    for u, why in defects[:20]:
        say(f"    DEFECT {u} {why}")
    for u in collapsed:
        say(f"    COLLAPSED {u} (duplicate record removed by DataSift; twin survives)")
    for u, w in warnings:
        say(f"    WARNING {u} {w}")
    return 1 if defects else 0


WORKLIST_COLS = ["uuid", "street", "city", "zip", "county", "assignee", "group", "subgroup", "bucket",
                 "reason", "before_first", "before_last", "before_company", "before_mailing", "phones",
                 "planned_first", "planned_last", "collision_owner_uuids", "hint"]


def cmd_worklist(a) -> int:
    run = latest_run_dir(a.lane, a.run_dir)
    plan = load_plan(run)
    state = jload(run / "state.json", {"probe": {}, "committed": {}})
    written = {u for u, p in {**state["probe"], **state["committed"]}.items() if p.get("ok")}
    rows = [r for r in plan["rows"] if r["bucket"] == "worklist"
            or (r["bucket"] == "review" and r["uuid"] not in written)]
    cohort = jload(run / "cohort.json", {})
    for r in rows:
        cf = (cohort.get(r["uuid"]) or {}).get("cf") or {}
        pr = " / ".join(str(cf.get(k) or "") for k in ("PR First Name", "PR Last Name") if cf.get(k))
        so = ", ".join(f"{x.get('first_name')} {x.get('last_name')}" for x in
                       ((cohort.get(r["uuid"]) or {}).get("detail") or {}).get("secondary_owners") or [])
        r["hint"] = " | ".join(x for x in [f"PR={pr}" if pr else "", f"secondary={so}" if so else "", r.get("hint") or ""] if x)
    write_csv(run / "worklist.csv", rows, WORKLIST_COLS)
    say(f"worklist: {len(rows)} rows -> {run / 'worklist.csv'}")
    for k, v in Counter(r["reason"].split(":")[0] for r in rows).most_common():
        say(f"    {k}: {v}")
    return 0


def cmd_undo(a) -> int:
    run = latest_run_dir(a.lane, a.run_dir)
    backup = jload(Path(a.undo), None)
    if not backup:
        raise SystemExit(f"no backup at {a.undo}")
    state = jload(run / "state.json", {})
    variant = state.get("undo_variant") or "omit"
    api = Api()
    restored, not_rev = 0, []
    for uuid, ent in backup.items():
        before = ent["before"]["block"]
        blk = copy.deepcopy(before)
        if variant == "empty":
            blk["first_name"], blk["last_name"] = blk["first_name"] or "", blk["last_name"] or ""
        elif variant == "omit":
            for k in ("first_name", "last_name"):
                if not blk.get(k):
                    blk.pop(k, None)
        st, resp = api.write("PATCH", f"/api/internal/property/{uuid}/", {"owner": blk})
        after = snapshot(read_detail(api, uuid))
        got = (after["block"]["first_name"] or "", after["block"]["last_name"] or "")
        want = (before["first_name"] or "", before["last_name"] or "")
        if st == 200 and got == want and after["owner_uuid"] == ent["before"]["owner_uuid"]:
            restored += 1
        else:
            not_rev.append((uuid, st, got))
            say(f"  not_reversible {uuid}: PATCH {st}, names now {got}, wanted {want}")
    say(f"undo: restored {restored}/{len(backup)}; not reversible {len(not_rev)}")
    jdump(run / f"undo_result_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json",
          {"variant": variant, "restored": restored, "not_reversible": not_rev})
    return 0 if not not_rev else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lane", required=True, choices=sorted(LANES))
    ap.add_argument("--run-dir", help="explicit run dir (default: newest for the lane)")
    act = ap.add_mutually_exclusive_group(required=True)
    act.add_argument("--plan", action="store_true")
    act.add_argument("--probe", action="store_true")
    act.add_argument("--commit", action="store_true")
    act.add_argument("--verify", action="store_true")
    act.add_argument("--worklist", action="store_true")
    act.add_argument("--undo", metavar="BACKUP_JSON")
    ap.add_argument("--group", choices=COMMIT_GROUPS, help="required with --commit")
    ap.add_argument("--include-review", action="store_true", help="commit review-bucket rows too")
    ap.add_argument("--accept-one-way", action="store_true",
                    help="commit group A even though the probe showed the rename cannot be blanked again")
    ap.add_argument("--allow-drift", action="store_true", help="plan even if the incomplete count drifted")
    a = ap.parse_args()
    if a.plan:
        return cmd_plan(a)
    if a.probe:
        return cmd_probe(a)
    if a.commit:
        if not a.group:
            ap.error("--commit needs --group")
        return cmd_commit(a)
    if a.verify:
        return cmd_verify(a)
    if a.worklist:
        return cmd_worklist(a)
    if a.undo:
        return cmd_undo(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
