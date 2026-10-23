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
# Basem 2026-09-22, non-negotiable: anything that loads numbers stamps the trace date in the
# SAME operation. Stamped on EVERY record in the batch, including the ones SmartSkip returned
# nothing for: the trace was paid for either way, and an unstamped miss is what gets re-skipped
# next month.
# Basem 2026-09-30: one trace tag per SOURCE -- this batch's trace is SmartSkip, so `SS Traced
# MM/YYYY` (the wizard is `REISift Traced`, clerk IDI data `IDI Traced`). The older
# `Skip Traced MM/YYYY` tags on the account are history and are not created any more.
TAG_SKIPTRACED = f"SS Traced {MONTH_TAG}"
TAG_DECEASED = "Deceased Owner"          # exists on the account already
TAG_ALIVE = "Owner Alive - Spouse Deceased"
TAG_UNVERIFIED = "DP Death Unverified"
TAG_PRESUMED = "DP Owner Presumed Alive"
TAG_NO_NUMBERS = f"DP No Numbers {MONTH_TAG}"
TAG_TRUST = "DP Trust Trustee Inferred"
TAG_SIGNERS_DARK = "DP No Signer Numbers"   # signer set phoneless; blood tier loaded instead
TAG_REACH_ONLY = "DP Reach Path Only"       # owner alive with no number; family is the only route
# The CRM named a decedent who is NOT the owner -- the MD/DC/VA probate convention where the
# owner slot holds the PR. Deliberately NOT TAG_ALIVE: that one asserts a researched SPOUSE
# death, and here the decedent may be no relation by marriage at all (7854 Linden Leaf Rd:
# decedent Theodore Q Page, owner Robert Page his PR, no spouse involved).
TAG_OWNER_IS_PR = "DP Owner Is PR"

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
               "special", "heritage", "co", "successor", "sole", "joint",
               # 2026-09-23: "Joseph C Hottel Jr Discretionary Trust" parsed as "Joseph
               # Discretionary" -- a trust-type word not on this list reads as a surname.
               "discretionary", "charitable", "remainder", "testamentary", "grantor",
               "inter", "vivos", "unitrust", "annuity", "lead", "dynasty", "asset",
               "protection"}
# A title carrying one of these names a company, not a person: no human to trace.
CORP_WORDS = {"llc", "inc", "corp", "corporation", "properties", "holdings", "company",
              "partners", "lp", "llp", "ltd", "foundation", "church", "bank", "ministries",
              "association", "investments", "group", "enterprises", "realty", "homes"}
ORDINALS = {"2nd", "3rd", "4th", "ii", "iii", "iv", "jr", "sr", "esq", "md", "dds", "phd"}

SPOUSE_RELS = {"Husband", "Wife", "Spouse"}

# A phone tag this pipeline wrote. Nothing outside this pattern is ever removed.
_OURS_RE = re.compile(r"^(Rel\d+\.\d+|Owner\.\d+)$")
# The Personal Representative's numbers ride `Pr.{m}` (Basem, 2026-09-30: the PR goes in
# the PR fields, never a REL slot). That is the account's probate vocabulary, SHARED with
# the vendor RFD imports (`Pr.N` / `PR.N` on ~1,500 records), so the tag alone is not
# provenance: a Pr-tagged number comes off only with a prior plan proving we wrote it, and
# the tag-alone fallback (`prior=None`) never touches one.
_PR_TAG_RE = re.compile(r"^Pr\.\d+$")
# Every positional tag `build` can emit -- what the QA compares slot-exact.
_PLAN_TAG_RE = re.compile(r"^(Rel\d+\.\d+|Owner\.\d+|Pr\.\d+)$")
PR_FIELDS = ("PR First Name", "PR Last Name", "Personal Representative",
             "Personal Representative Phone")
PR_PHONE_FIELD = "Personal Representative Phone"
def _d10(n) -> str:
    """Last 10 digits -- the account stores some numbers as +1XXXXXXXXXX."""
    return "".join(c for c in str(n or "") if c.isdigit())[-10:]
_FAMILY_DATE_RE = re.compile(r"\s+\d{4}-\d{2}-\d{2}$")
def _note_family(note: str) -> str:
    """Header marker of a board note: its first line, cut at the first " (", with a trailing
    ISO date stripped. Two notes are the same FAMILY when this matches.

        "DEEP PROSPECTING 2026-09-01 (SmartSkip + obituary + Trestle)"     -> "DEEP PROSPECTING"
        "PROBATE - Register of Wills Anne Arundel County (loaded 2026-09-01)"
                                        -> "PROBATE - Register of Wills Anne Arundel County"
    """
    text = (note or "").strip()
    first = text.splitlines()[0] if text else ""
    # the pre-2026-09-01 note format joined every section with "  ||  " on one line
    first = first.split("  ||  ", 1)[0]
    head = first.split(" (", 1)[0]
    return _FAMILY_DATE_RE.sub("", head).strip()
def board_messages(api, uuid: str) -> list[dict]:
    """Every message on a record's board. `?limit=100`: without it the default page can hide
    an older same-family note, which then never gets replaced and stacks."""
    st, mb = api.get(f"/api/internal/property/{uuid}/message/?limit=100")
    if isinstance(mb, dict):
        return mb.get("results") or []
    return mb if isinstance(mb, list) else []


def custom_field_rows(api, uuid: str) -> tuple[list[dict], dict[str, object]]:
    """(rows, {label: value}) for a record's custom fields. The endpoint answers a bare list
    on this account; a paginated dict is handled too."""
    st, cf = api.get(f"/api/internal/property/{uuid}/custom-field/")
    rows = cf if isinstance(cf, list) else ((cf.get("results") or cf.get("data") or []) if isinstance(cf, dict) else [])
    return rows, {((r.get("custom_field") or {}).get("label") or ""): r.get("value") for r in rows}


def _stale_board_messages(msgs: list[dict], new_note: str, new_uuid: str) -> list[dict]:
    """Earlier board messages the new note REPLACES: same family, different uuid.

    2026-09-01 trap: this pusher was reused for a "PROBATE - Register of Wills" plan and the
    cleanup deleted every message starting "DEEP PROSPECTING" -- the obituary DP note on 21
    records, replaced by a SmartSkip-raw list with no obituary. A note may only ever displace
    its OWN family (equality, not prefix: "PROBATE - ... Baltimore County" is not "... Anne
    Arundel County"). An unrecognisable header (< 8 chars) deletes nothing.
    """
    fam = _note_family(new_note)
    if len(fam) < 8:
        return []
    return [m for m in msgs
            if m.get("uuid") and m.get("uuid") != new_uuid
            and _note_family(m.get("message") or "") == fam]


# Edit the record's existing same-family board note in place instead of post-new + delete-old
# (Basem, 2026-09-17: "edit the existing note"). Set False to force the old replace path.
NOTE_EDIT_IN_PLACE = True
_REL_PHONE_RE = re.compile(r"^REL(\d+): Phone (\d+)$")
_REL_NAME_RE = re.compile(r"^REL(\d+): Full Name$")


def _ghost_blanks(live_cf: dict, plan_cf: dict, removed_digits: set, fields: dict) -> list[str]:
    """Live REL custom fields a re-push must blank, decided by PROVENANCE only.

    A re-push only ever WROTE fields; when the new plan uses fewer slots than the old one a
    dropped relative survives under a slot the plan no longer names. Two rules, both anchored
    on numbers this push just removed from the owner under our own Rel{n}.{m} tag:

      1. `REL{n}: Phone k` not in the plan whose digits are a removed number -> blank
         (the 2026-09-11 probe: the dead husband's numbers stayed in REL1: Phone 2/3).
      2. `REL{n}: Full Name` not in the plan, when rule 1 blanked at least one phone of slot
         n AND every other phone field of that slot is empty or also blanked -> blank
         (2026-09-15 FTM strip: 200 name ghosts on 60 records survived rule 1 alone).

    A slot still holding a number we did NOT remove belongs to another pusher, so its name
    stays -- the 2026-09-10 rule: another plan's REL values are never touched. A label the
    account does not have (`fields`) is never written.

      3. `Personal Representative Phone` not in the plan whose digits are a removed number
         -> blank (the PR path, 2026-10-01). The PR NAME fields are never blanked: the
         probate pulls and the vendor imports write them too, and a name is never stale
         the way a dropped number is.
    """
    out: list[str] = []
    slots: set[str] = set()
    for lbl, val in live_cf.items():
        if (lbl == PR_PHONE_FIELD and val and lbl not in plan_cf
                and _d10(str(val)) in removed_digits and fields.get(lbl)):
            out.append(lbl)
            continue
        m = _REL_PHONE_RE.match(lbl)
        if (m and val and lbl not in plan_cf and _d10(str(val)) in removed_digits
                and fields.get(lbl)):
            out.append(lbl)
            slots.add(m.group(1))
    blanked = set(out)
    for n in sorted(slots, key=int):
        name_lbl = f"REL{n}: Full Name"
        if not live_cf.get(name_lbl) or name_lbl in plan_cf or not fields.get(name_lbl):
            continue
        leftovers = [l for l, v in live_cf.items()
                     if v and l not in blanked and l not in plan_cf
                     and _REL_PHONE_RE.match(l) and _REL_PHONE_RE.match(l).group(1) == n]
        if leftovers:
            continue
        out.append(name_lbl)
    return out


def _ordered_keys(pushable: list[str], wanted: list[str]) -> list[str]:
    """`wanted` in its own order, restricted to keys the push may touch. Order is the point:
    the never-dialed wave must land before the dialed one."""
    allowed = set(pushable)
    return [k for k in wanted if k in allowed]


def _stale_numbers(owner_phones: list, want: set, prior: "set | None") -> tuple[list, list, list]:
    """Numbers to take OFF the owner before the upsert: (stale, restore_payload, foreign_kept).

    A number comes off only when it (a) is absent from the new plan, (b) carries our own
    Rel{n}.{m}/Owner.{m} tag AND (c) -- when `prior` is given -- was written by OUR earlier
    plan for this record. (c) is the 2026-09-17 fix: other pushers (RFD imports, probate
    pushes) tag THEIR numbers Rel{n}.{m} too, so the tag alone is not provenance -- on
    2026-09-10 the tag-only guard removed 18 foreign numbers from 3005 S Leisure World #405
    (Basem's richer-source rule: append, never blank). Rel-tagged numbers skipped under (c)
    are returned as `foreign_kept` so the log shows them. `prior=None` = the old behaviour."""
    stale, restore, foreign = [], [], []
    for ph in owner_phones or []:
        num = ph.get("number") or ""
        tags = [t.get("name") if isinstance(t, dict) else str(t) for t in (ph.get("tags") or [])]
        d = _d10(num)
        # Pr.{m} is ours ONLY under a prior plan (see _PR_TAG_RE): the vendor imports use it too.
        ours = any(_OURS_RE.match(t) for t in tags) or (
            prior is not None and any(_PR_TAG_RE.match(t) for t in tags))
        if d in want or not ours:
            continue
        if prior is not None and d not in prior:
            foreign.append(num)
            continue
        stale.append(num)
        restore.append({"number": num, "type": ph.get("type") or "UNKNOWN", "tags": tags,
                        "status": ph.get("status") or "UNKNOWN",
                        "is_connected": bool(ph.get("isConnected", True)), "verified": False})
    return stale, restore, foreign


def _norm_note(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _place_note(api, uuid: str, note: str, log: dict) -> None:
    """Step 4 of a push: the note onto the record's message board, PINNED.

    add-notes writes the `notes` FIELD, which is not the board a caller reads (it answers 204,
    so the old 404/405-gated fallback never ran and 619 boards were empty). `pinned:true` on
    create is accepted and IGNORED; PATCHing pinned answers 200 and stores nothing; only
    POST .../message/{uuid}/pin/ (204) pins.

    2026-09-17 (Basem: "edit the existing note"): when the record already carries a note of
    the SAME FAMILY, PATCH its text in place -- same message uuid, same pin, same date -- so a
    1,000-record re-push neither floods the Activity Log (which echoes board posts) nor resets
    when the research was done. The PATCH is trusted only on READ-BACK (the route lies for
    `pinned`); if the text did not land, fall back to what every earlier push did: POST the
    new note, DELETE the same-family old ones (each logged in full), pin. A failed POST deletes
    nothing -- a failed re-push must never end with an empty board.
    """
    text = note[:4000]
    board = board_messages(api, uuid)
    same = _stale_board_messages(board, text, "")
    if same and NOTE_EDIT_IN_PLACE:
        target = next((m for m in same if m.get("pinned")), same[0])
        before = target.get("message") or ""
        st_e, resp_e = api.write("PATCH", f"/api/internal/property/{uuid}/message/{target['uuid']}/",
                                 {"message": text})
        after = board_messages(api, uuid)
        got = next((m for m in after if m.get("uuid") == target["uuid"]), None)
        stored = (got or {}).get("message") or ""
        if got and _norm_note(stored) == _norm_note(text):
            log["steps"]["message_edited"] = {"status": st_e, "uuid": target["uuid"],
                                              "was_pinned": bool(target.get("pinned")),
                                              "before": before[:4000]}
            log["steps"]["message"] = {"status": st_e, "uuid": target["uuid"], "edited": True}
            if got.get("pinned"):
                log["steps"]["pin"] = {"status": "kept"}
            else:
                st3, _ = api.write("POST", f"/api/internal/property/{uuid}/message/{target['uuid']}/pin/", {})
                log["steps"]["pin"] = {"status": st3}
            dupes = [m for m in same if m.get("uuid") != target["uuid"]]
            if dupes:
                replaced = []
                for old in dupes:
                    st_d, _ = api.write("DELETE", f"/api/internal/property/{uuid}/message/{old['uuid']}/", {})
                    replaced.append({"uuid": old["uuid"], "status": st_d, "deleted": st_d in (200, 202, 204),
                                     "pinned": bool(old.get("pinned")), "message": (old.get("message") or "")[:4000]})
                log["steps"]["message_replaced"] = replaced
            return
        log["steps"]["message_edit_failed"] = {"status": st_e, "uuid": target["uuid"],
                                               "resp": str(resp_e)[:200],
                                               "stored_unchanged": _norm_note(stored) == _norm_note(before),
                                               "before": before[:4000]}
        log.setdefault("warnings", []).append("message PATCH did not store the text; posted a new note instead")
    st2, resp2 = api.write("POST", f"/api/internal/property/{uuid}/message/", {"message": text})
    msg_uuid = (resp2 or {}).get("uuid") if isinstance(resp2, dict) else None
    log["steps"]["message"] = {"status": st2, "uuid": msg_uuid, "resp": str(resp2)[:120]}
    if not msg_uuid:
        return
    # Only now that the replacement exists: drop earlier notes of the SAME FAMILY (equality
    # of _note_family, never a prefix -- a probate plan pushed through this code once wiped
    # 21 obituary DP notes by matching "DEEP PROSPECTING"). Every deletion is logged in full.
    stale_msgs = _stale_board_messages(board_messages(api, uuid), text, msg_uuid)
    replaced = []
    for old_msg in stale_msgs:
        st_d, _ = api.write("DELETE", f"/api/internal/property/{uuid}/message/{old_msg['uuid']}/", {})
        replaced.append({"uuid": old_msg["uuid"], "status": st_d, "deleted": st_d in (200, 202, 204),
                         "pinned": bool(old_msg.get("pinned")), "message": (old_msg.get("message") or "")[:4000]})
    log["steps"]["message_replaced"] = replaced
    if any(not r["deleted"] for r in replaced):
        log.setdefault("warnings", []).append(
            f"{sum(not r['deleted'] for r in replaced)} stale same-family message(s) NOT deleted")
    st3, _ = api.write("POST", f"/api/internal/property/{uuid}/message/{msg_uuid}/pin/", {})
    log["steps"]["pin"] = {"status": st3}
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
def _names_match_by(a: str, b: str, first_eq) -> bool:
    """Shared body of the two matchers: last token exact, first token by `first_eq`,
    middle names / initials / suffixes ignored, single-token names never match."""
    ta, tb = name_tokens(a), name_tokens(b)
    ta = [t for t in ta if len(t) > 1] or ta
    tb = [t for t in tb if len(t) > 1] or tb
    if len(ta) < 2 or len(tb) < 2:
        return False
    return ta[-1] == tb[-1] and first_eq(ta[0], tb[0])
def names_match(a: str, b: str) -> bool:
    """First token + last token match, ignoring middle names, initials, suffixes.

    STRICT on purpose: pr_repair_write / pr_repair_copr_multi / dc_case_pr_lookup import this
    to decide PR writes, where a loose match means writing the wrong person; and the
    predeceased drop list uses it, because a false "predeceased" silences a living signer.
    The obituary cross-checks use names_match_loose below, through _cross_check's 1:1 rule.
    """
    return _names_match_by(a, b, lambda x, y: x == y)
# nickname -> formal spellings. Consulted in both directions. Obituaries use the family
# name (Judy, Sally, Bill); SmartSkip returns the legal one (JUDITH, SARAH, WILLIAM) -- the
# strict matcher called 58 loaded relatives "not in SmartSkip" over exactly this.
NICKNAMES: dict[str, set[str]] = {
    "judy": {"judith"}, "sally": {"sarah"},
    "bill": {"william"}, "billy": {"william"}, "will": {"william"},
    "bob": {"robert"}, "bobby": {"robert"}, "rob": {"robert"},
    "dick": {"richard"}, "rick": {"richard"},
    "jim": {"james"}, "mike": {"michael"},
    "chris": {"christopher", "christine", "christina", "christian"},
    "peggy": {"margaret"}, "maggie": {"margaret"}, "meg": {"margaret"}, "marge": {"margaret"},
    "kathy": {"katherine", "kathleen", "catherine"}, "kate": {"katherine", "kathleen", "catherine"}, "cathy": {"catherine", "katherine"},
    "liz": {"elizabeth"}, "beth": {"elizabeth", "bethany"}, "betty": {"elizabeth"}, "jack": {"john"},
    "tom": {"thomas"}, "tony": {"anthony"}, "ted": {"edward", "theodore"}, "ed": {"edward", "edwin", "edmund"},
    "dan": {"daniel"}, "danny": {"daniel"}, "dave": {"david"},
    "steve": {"steven", "stephen"}, "joe": {"joseph"},
    "larry": {"lawrence", "laurence"}, "jerry": {"gerald", "jerome", "gerard"},
    "ron": {"ronald"}, "don": {"donald"},
    "ken": {"kenneth"}, "pat": {"patricia", "patrick"}, "patty": {"patricia"}, "trish": {"patricia"},
    "sue": {"susan", "suzanne"},
    "chuck": {"charles"}, "charlie": {"charles"}, "hank": {"henry"}, "fred": {"frederick", "alfred"},
    "greg": {"gregory"}, "andy": {"andrew"}, "drew": {"andrew"}, "matt": {"matthew"},
    "nick": {"nicholas"}, "ben": {"benjamin"}, "sam": {"samuel", "samantha"}, "alex": {"alexander", "alexandra", "alexis"},
    "jen": {"jennifer"}, "jenny": {"jennifer"}, "becky": {"rebecca"},
    "debbie": {"deborah", "debra"}, "deb": {"deborah", "debra"},
    "cindy": {"cynthia"}, "vicki": {"victoria"}, "vicky": {"victoria"},
    "tim": {"timothy"}, "ray": {"raymond"}, "walt": {"walter"}, "al": {"albert", "alfred", "alan", "allen", "alvin"}, "herb": {"herbert"}, "frank": {"francis", "franklin"},
    "fran": {"frances", "francis"},
    "russ": {"russell"}, "randy": {"randall", "randolph"}, "nate": {"nathaniel", "nathan"}, "len": {"leonard"},
    "vince": {"vincent"}, "phil": {"philip", "phillip"}, "pete": {"peter"},
    "stu": {"stuart"}, "wes": {"wesley"}, "zach": {"zachary"}, "josh": {"joshua"}, "jon": {"jonathan"},
    "doug": {"douglas"}, "brad": {"bradley", "bradford"}, "bernie": {"bernard"}, "ernie": {"ernest"}, "jackie": {"jacqueline"}, "millie": {"mildred", "millicent"}, "pam": {"pamela"}, "rosie": {"rose", "rosemary"}, "steph": {"stephanie"},
    "val": {"valerie"},
}
# Keys that are common standalone given names in their own right (Lisa, Carol, Lynn, Jamie,
# Harry, ...) are NOT in the table: "preceded by daughter Lisa Jones" must never equate a
# living ELIZABETH JONES. The table is unioned with soi_owner_match.NICKNAMES so the two
# pipelines cannot disagree on a pair (stephen<->steven, kate<->kathryn, jeff<->jeffrey).
try:  # pragma: no cover - import only
    from soi_owner_match import NICKNAMES as _SOI_NICKNAMES  # noqa: E402
    for _k, _v in _SOI_NICKNAMES.items():
        NICKNAMES.setdefault(_k, set()).update(_v)
except Exception:  # noqa: BLE001
    pass
# The SOI table serves owner-name matching, where a false positive costs a lookup; here a
# false positive can silence a signer or stamp the wrong kinship, so standalone given names
# are removed again after the union whatever either table says.
_STANDALONE_GIVEN = {"lisa", "carol", "lynn", "jamie", "harry", "terry", "kim", "jan", "jo", "kit",
                     "jody", "leo", "bert", "marty", "les", "lou", "mo", "nan", "nell", "rae", "wendy",
                     "tina", "toni", "connie", "bonnie", "dolly", "sandy", "sherry", "shelly", "penny",
                     "mandy", "tammy", "gene", "art", "norm", "polly", "gerry", "rich", "hal"}
for _k in _STANDALONE_GIVEN:
    NICKNAMES.pop(_k, None)
def _first_equiv(a: str, b: str, prefix: bool = True) -> bool:
    """Two first-name tokens name the same person: equal, nickname either way, or (when
    `prefix`) one is a >=4-char prefix of the other (Chris/Christopher, Steph/Stephanie)."""
    if a == b:
        return True
    if b in NICKNAMES.get(a, ()) or a in NICKNAMES.get(b, ()):
        return True
    if prefix and min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return False
def names_match_loose(a: str, b: str, prefix: bool = True) -> bool:
    """names_match with nickname / prefix tolerance on the FIRST token only; last token exact.

    Used for the obituary <-> SmartSkip cross-checks (confirmed_rels, obit_only_names,
    augment dedupe). `prefix=False` for the predeceased drop list: a false "predeceased"
    silences a signer, so only the nickname table is trusted there (Rose vs Rosemary stays
    two people).
    """
    return _names_match_by(a, b, lambda x, y: _first_equiv(x, y, prefix))


NAMESAKE_GAP_YEARS = 18   # a same-name relative this much older/younger is a Jr./Sr., not the decedent


def _matches_decedent(decedent: str, name: str) -> bool:
    """Strict first+last match, OR last name equal and any given-name token shared.

    The loose arm exists for the middle-name-as-first case: obituary "Lynn Wayne Hill",
    SmartSkip "WAYNE HILL". It is deliberately narrower than names_match_loose (no
    nickname table, no prefix) because a hit here silences a relative; the age gap
    check in cmd_build is what protects a living namesake.
    """
    if names_match(decedent, name):
        return True
    dt, nt = name_tokens(decedent), name_tokens(name)
    if len(dt) < 2 or len(nt) < 2 or dt[-1] != nt[-1]:
        return False
    # The shared token must be the RELATIVE's first name. "Lynn Wayne Hill" vs "WAYNE
    # HILL" matches (Wayne is what he goes by); "Arjan Aswani" vs "Kavita Arjan Aswani"
    # does NOT -- Kavita is the living spouse carrying her husband's name as a middle
    # name, and the old symmetric check silenced her (2026-10-01 handoff).
    given_dec = {t for t in dt[:-1] if len(t) > 1}
    return len(nt[0]) > 1 and nt[0] in given_dec


def _cross_check(f: dict, rels: list[dict]) -> dict:
    """Obituary survivors <-> SmartSkip relatives, paired ONE-TO-ONE.

    A loose match is trusted only when it is unambiguous: the survivor loosely matches
    exactly one relative and that relative loosely matches exactly one survivor. Strict
    matches pair first. So "Paul Smith" + "Paula Smith" against a lone PAUL SMITH pairs
    Paul strictly and leaves Paula unmatched (she gets traced, not silenced), and
    "Chris Jones" (son) / "Christine Jones" (daughter) against CHRISTOPHER + CHRISTINE
    pair by strict-then-unique instead of stamping "son" on both.

    Returns {"confirmed": [rel names], "obit_only": [survivor names],
             "paired_survivors": [survivor names], "words": {rel name: relationship word}}.
    """
    survivors = []
    for sv in f.get("survivors") or []:
        nm = _survivor_name(sv)
        if nm:
            survivors.append((nm, ((sv.get("relationship") if isinstance(sv, dict) else "") or "").strip().lower()))
    rel_names = [r["name"] for r in rels if r.get("name")]
    paired: dict[str, tuple[str, str]] = {}   # rel name -> (survivor name, word)
    used_surv: set[str] = set()
    for matcher in (names_match, names_match_loose):
        cand_by_surv = {nm: [r for r in rel_names if r not in paired and matcher(r, nm)]
                        for nm, _w in survivors if nm not in used_surv}
        cand_by_rel: dict[str, list[str]] = {}
        for nm, cands in cand_by_surv.items():
            for r in cands:
                cand_by_rel.setdefault(r, []).append(nm)
        for nm, word in survivors:
            if nm in used_surv:
                continue
            cands = cand_by_surv.get(nm) or []
            if len(cands) == 1 and len(cand_by_rel.get(cands[0], [])) == 1:
                paired[cands[0]] = (nm, word)
                used_surv.add(nm)
    return {"confirmed": [r for r in rel_names if r in paired],
            "obit_only": [nm for nm, _w in survivors if nm not in used_surv],
            "paired_survivors": sorted(used_surv),
            "words": {r: w for r, (_nm, w) in paired.items() if w}}
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
        elif g0 and g1 and len(words) >= 3 and not _gender_known(words[-1]):
            # "Nathan Wayne Gross Trustee": two given names followed by a token that is
            # not one is FIRST MIDDLE LAST whatever the title's style says. The recorder
            # form of the same person, "Gross Nathan Wayne", fails g0 and takes the
            # branch above. Without this the trailing "Trustee" forced recorder order and
            # produced "Wayne Nathan" (2026-09-23).
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
    # The shared list lacks the singular "trustee" and the recorder abbreviations "trs" /
    # "ttees", so "Nathan Wayne Gross Trustee" and "Adams Belle H & Harold B Trs" were
    # classed as plain owner blobs and never earned TAG_TRUST (2026-09-23). Widened HERE,
    # not in obituary_dp_input, so the mail consolidator's entity test is unchanged.
    is_entity = bool(words & (ENTITY_WORDS | {"trs", "trustee", "ttees", "irrevocable"}))
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
            # The CRM's own answer to "who died", from the Deceased First/Last Name custom
            # fields the probate + Register of Wills pulls write. cmd_research used to ignore
            # this entirely and search the OWNER for an obituary, which is wrong whenever the
            # owner slot holds the PR -- the MD/DC/VA probate convention. 7854 Linden Leaf Rd
            # (2026-09-23): decedent Theodore Q Page, owner Robert Page his living PR; the
            # owner search re-rooted the whole family around a man who had not died.
            "decedent_first": (r.get("Deceased First Name") or "").strip(),
            "decedent_last": (r.get("Deceased Last Name") or "").strip(),
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
# ───────────────────────── anchors ─────────────────────────

VENDOR_ROOT = ROOT / "output" / "vendor_lists" / "files" / "Marketing Lists"
ROW_LEDGER = ROOT / "output" / "md_row_ledger.json"
SHEET26_DEFAULT = Path.home() / "Downloads" / "Probate Extraction 2 - Sheet26.csv"


def _iso_date(s: str) -> str:
    """'08-13-2026' / '8/13/2026' / '2026-08-13' -> '2026-08-13'; '' when unparseable."""
    s = (s or "").strip()
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    return m.group(0) if m else ""


def _court_for(state: str, county: str) -> str:
    if state == "DC":
        return "DC Superior Court, Probate Division"
    if state == "MD":
        return f"{county} Register of Wills" if county else "Register of Wills"
    if state == "VA":
        return f"{county} Circuit Court" if county else "Circuit Court"
    return "probate court"


def _join_name(*parts) -> str:
    return " ".join(p.strip() for p in parts if p and str(p).strip() and str(p).strip() != "None")


def cmd_anchors(a) -> int:
    """Attach the search anchors the vendor files ALREADY HOLD to every record.

    1391 Pennsylvania Ave SE #433 (Basem, 2026-10-01): the note said "DOD unknown (no
    source)" while the vendor's raw weekly probate CSV for that row carried the date of
    death (08-13-2026), the case number (2026-ADM-001053), the decedent's middle name
    (Beardsley), the PR's full name and address, and the attorney. The DP input dropped
    all of it, the research searched "David Smith" obituary Washington, judged three
    namesakes and gave up. Across the Sept batch 76% of the SIGNER lines rested on a
    SmartSkip label alone. This step writes rec["anchors"] from three sources, none of
    which costs anything:
      - the vendor raw probate CSVs (`*Raw*.csv` under --vendor-root): DOD, case number,
        middle name, PR full name + mailing, attorney;
      - the Register of Wills extraction sheet (--sheet26): estate number, full decedent
        name, PR name(s) + mailing, notice date;
      - the MD RoW ledger (--row-ledger), by county|estate number: DOD, decedent name as
        filed, PRs, attorney, filing date.
    `research` then searches with the anchors and the judge must confirm one; `build`
    prints them (CASE line, PR mailing, attorney) and uses the court DOD when no obituary
    gives one. Idempotent: re-running overwrites rec["anchors"].
    """
    import glob as _glob
    run = Path(a.run_dir)
    records = jload(run / "records.json", {})
    export = Path(a.export) if a.export else run / "dp_input_export.csv"
    drive_src: dict[str, str] = {}
    if export.exists():
        for r in csv.DictReader(open(export, newline="", encoding="utf-8-sig")):
            k = sift_key(r.get("Property address", ""), r.get("Property zip5") or r.get("Property zip", ""))
            drive_src[k] = (r.get("Drive source") or "").strip()

    # (a) vendor raw CSVs
    raw: dict[str, dict] = {}
    n_raw_files = 0
    for fn in sorted(_glob.glob(str(Path(a.vendor_root) / "**" / "*Raw*.csv"), recursive=True)):
        try:
            rows = list(csv.DictReader(open(fn, newline="", encoding="utf-8-sig", errors="replace")))
        except OSError:
            continue
        if not rows or "Date of Death" not in rows[0] or "Property Address" not in rows[0]:
            continue
        n_raw_files += 1
        for r in rows:
            k = sift_key(r.get("Property Address", ""), r.get("Property Zip", ""))
            if not k or k in raw:
                continue        # first file wins (oldest week); the data does not change
            pr_full = _join_name(r.get("PR First Name"), r.get("PR Middle Name"), r.get("PR Last Name"))
            att = _join_name(r.get("Attorney First Name"), r.get("Attorney Middle Name"), r.get("Attorney Last Name"))
            raw[k] = {
                "dod": _iso_date(r.get("Date of Death", "")),
                "dod_source": "vendor_probate_file" if _iso_date(r.get("Date of Death", "")) else "",
                "case_number": (r.get("Case Number") or "").strip(),
                "file_date": _iso_date(r.get("File Date", "")),
                "decedent_full": _join_name(r.get("First Name (Deceased)"), r.get("Middle Name"),
                                            r.get("Last Name"), r.get("Suffix")),
                "decedent_middle": (r.get("Middle Name") or "").strip(),
                "pr_full": pr_full,
                "pr_mailing": _join_name(r.get("PR Address"), (r.get("PR City") or "") + ",",
                                         r.get("PR State"), r.get("PR Zip")).replace(" ,", ","),
                "attorney": _join_name(att, r.get("Attorney Ph Number"), r.get("Attorney Email ID")),
                "source": f"vendor raw {Path(fn).name}",
            }

    # (b) Sheet26 (Register of Wills extraction sheet)
    sheet: dict[str, dict] = {}
    by_estate: dict[str, dict] = {}
    s26 = Path(a.sheet26) if a.sheet26 else SHEET26_DEFAULT
    if s26.exists():
        for r in csv.DictReader(open(s26, newline="", encoding="utf-8-sig", errors="replace")):
            k = sift_key((r.get("Property Address") or "").strip(), r.get("Property Zipcode", ""))
            est = (r.get("Estate #") or "").strip()
            prs = [_join_name(r.get("PR First Name"), r.get("PR Last Name"))]
            for i in (2, 3):
                nm = _join_name(r.get(f"PR {i} First Name"), r.get(f"PR {i} Last Name"))
                if nm:
                    prs.append(nm)
            d = {
                "estate_number": est,
                "county": (r.get("County") or "").replace(" County", "").strip(),
                "decedent_full": _join_name(r.get("Deceased First Name"), r.get("Deceased Last Name")),
                "pr_full": prs[0], "pr_others": prs[1:],
                "pr_mailing": _join_name(r.get("Owner Address (PR)"), (r.get("Owner City") or "") + ",",
                                         r.get("Owner State"), r.get("Owner Zipcode")).replace(" ,", ","),
                "attorney": (r.get("Attorney") or "").strip(),
                "notice_date": _iso_date(r.get("Published on", "")),
                "source": "Sheet26",
            }
            if k:
                sheet.setdefault(k, d)
            if est:
                by_estate.setdefault(est, d)

    # (c) the RoW ledger, by county|estate number
    ledger = (jload(Path(a.row_ledger), {}) or {}).get("estates", {}) if Path(a.row_ledger).exists() else {}
    ledger_by_est: dict[str, dict] = {}
    for ck, v in ledger.items():
        en = ck.split("|", 1)[1] if "|" in ck else ck
        ledger_by_est.setdefault(en, v)

    counts = Counter()
    for key, rec in records.items():
        an: dict = {}
        if key in raw:
            an.update(raw[key]); counts["vendor_raw"] += 1
        est = ""
        if key in sheet:
            d = sheet[key]
            for kk, vv in d.items():
                if vv and not an.get(kk):
                    an[kk] = vv
            est = d.get("estate_number") or ""
            counts["sheet26"] += 1
        if not est:
            m = re.search(r"Sheet26 estate (\S+)", drive_src.get(key, ""))
            if m:
                est = m.group(1)
                if est in by_estate:
                    for kk, vv in by_estate[est].items():
                        if vv and not an.get(kk):
                            an[kk] = vv
                    counts["sheet26_by_estate"] += 1
        if est:
            an["estate_number"] = est
            lv = ledger_by_est.get(est)
            if lv:
                counts["row_ledger"] += 1
                dod = _iso_date(lv.get("date_of_death", ""))
                if dod and not an.get("dod"):
                    an["dod"], an["dod_source"] = dod, "register_of_wills"
                if lv.get("decedent_name") and not an.get("decedent_as_filed"):
                    an["decedent_as_filed"] = lv["decedent_name"]
                if not an.get("file_date"):
                    an["file_date"] = _iso_date(lv.get("date_of_filing") or lv.get("filing_date") or "")
                prs = lv.get("personal_reps") or []
                if prs and not an.get("pr_full"):
                    an["pr_full"] = prs[0].get("name", "")
                    an["pr_mailing"] = _join_name(prs[0].get("street"), (prs[0].get("city") or "") + ",",
                                                  prs[0].get("state"), prs[0].get("zip")).replace(" ,", ",")
                if lv.get("attorney") and not an.get("attorney"):
                    an["attorney"] = (lv["attorney"] or {}).get("name", "")
                an["row_record_id"] = lv.get("record_id", "")
                an["source"] = (an.get("source", "") + " + RoW ledger").strip(" +")
        if an:
            an.setdefault("court", _court_for(rec.get("state", ""), rec.get("county", "")))
            # the middle name as a search anchor, from the fullest name on file
            full = an.get("decedent_full") or ""
            toks = [t for t in re.sub(r"[.,]", "", full).split() if t]
            if len(toks) >= 3 and not an.get("decedent_middle"):
                an["decedent_middle"] = " ".join(toks[1:-1])
            rec["anchors"] = an
            counts["anchored"] += 1
            if an.get("dod"):
                counts["with_dod"] += 1
        else:
            rec.pop("anchors", None)
    if getattr(a, "estate_search", False):
        counts["estate_search"] = _estate_search_fill(run, records, a.estate_limit)
    jdump(run / "records.json", records)
    say(f"anchors: {len(records)} records, raw files={n_raw_files}, {dict(counts)}")
    return 0
def _estate_search_fill(run: Path, records: dict, limit: int = 0) -> int:
    """Register of Wills Estate Search BY ESTATE NUMBER (#txtEstateNo + county) for every
    anchored record that has an estate number and no DOD yet. The results grid itself
    carries the date of death, the filing date and the name as filed, so it is ONE
    Firecrawl scrape per estate and no detail page. Cached in row_estate_lookup.json;
    the RoW ledger is never written (two writers of that file lose work, see CLAUDE.md).
    """
    import md_register_of_wills_pull as row  # noqa: E402
    cache_p = run / "row_estate_lookup.json"
    cache = jload(cache_p, {}) or {}
    todo = [(k, r) for k, r in records.items()
            if (r.get("anchors") or {}).get("estate_number") and not (r.get("anchors") or {}).get("dod")]
    if limit:
        todo = todo[:limit]
    say(f"estate search: {len(todo)} estates without a DOD ({sum(1 for k, _ in todo if k in cache)} cached)")
    filled = 0
    for i, (key, rec) in enumerate(todo, 1):
        an = rec["anchors"]
        est = an["estate_number"]
        county = an.get("county") or rec.get("county") or ""
        hit = cache.get(key)
        if hit is None:
            cid = row.COUNTY_ID.get(county)
            if not cid:
                say(f"  [{i}/{len(todo)}] {rec['street']}: county {county!r} not in COUNTY_ID, skipped")
                continue
            actions = [
                {"type": "wait", "milliseconds": 2000},
                {"type": "executeJavascript",
                 "script": row._set_value_js("#cboCountyId", cid) + row._set_value_js("#txtEstateNo", est)},
                {"type": "wait", "milliseconds": 1500},
                {"type": "click", "selector": "#cmdSearch"},
                {"type": "wait", "milliseconds": 5000},
                {"type": "scrape"},
            ]
            try:
                html = row.firecrawl_scrape_retry(row.ESTATE_SEARCH_URL, actions, timeout=90)[-1]
            except Exception as e:  # noqa: BLE001
                say(f"  [{i}/{len(todo)}] {rec['street']}: estate {est} fetch failed: {str(e)[:120]}")
                continue
            rows = [g for g in row.parse_estate_search_grid(html, county)
                    if g.get("estate_number", "").upper() == est.upper()]
            hit = rows[0] if rows else {}
            cache[key] = hit
            jdump(cache_p, cache)
        if not hit:
            say(f"  [{i}/{len(todo)}] {rec['street']}: estate {est} ({county}) -> no grid row")
            continue
        dod = _iso_date(hit.get("date_of_death", ""))
        if dod:
            an["dod"], an["dod_source"] = dod, "register_of_wills"
            filled += 1
        if hit.get("name_raw") and not an.get("decedent_as_filed"):
            first, last = row.split_last_first_name(hit["name_raw"])
            an["decedent_as_filed"] = f"{first} {last}".strip()
        if not an.get("file_date"):
            an["file_date"] = _iso_date(hit.get("filing_date", ""))
        an["row_record_id"] = hit.get("record_id", "")
        an["estate_status"] = hit.get("status", "")
        an["source"] = (an.get("source", "") + " + RoW estate search").strip(" +")
        say(f"  [{i}/{len(todo)}] {rec['street']}: estate {est} -> DOD {dod or '?'} {hit.get('name_raw', '')}")
    return filled
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
    # `--channel-cap` DEFAULTS TO 0 (Basem, 2026-09-15): load no in-law at all. An in-law
    # cannot sign, and a dialer working a CHANNEL entry as a decision maker burns the dial.
    # They are still NAMED in the note ("In-laws found, not loaded"), so the reach path is
    # documented without costing a REL slot, a dial-list number or a Trestle solve -- a
    # dropped in-law keeps `"phones": []`, and `score` prices only numbers on a KEPT
    # contact, so an in-law number is never priced. Measured on the 2026-09-15 FTM batch:
    # cap 2 cost 115 REL entries and 232 of 1,100 dial numbers (21%).
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
        blood_held = [{"name": x.get("name"), "canon_rel": x.get("canon_rel"),
                       "age": x.get("age"), "n_phones": len(x.get("phones") or [])}
                      for x in blood[a.max_rels:]]
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
        stats["non_signers_held"] += len(blood_held)
        stats["channels"] += len(channels)
        stats["channels_dropped"] += len(channels_dropped)
        stats["phones"] += n_ph
        ranked[key] = {
            "key": key, "input_name": rec.get("input_name"),
            "subject": {"first": rec.get("first"), "last": rec.get("last"), "age": rec.get("subject_age"),
                        "deceased_flag": rec.get("deceased"),
                        "phones": sorted(rec.get("subject_phones") or [], key=_phone_sort_key)},
            "signer_basis": rec.get("signer_basis"), "rels": rels,
            "channels_dropped": channels_dropped, "blood_held": blood_held,
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
def _anchor_queries(name: str, city: str, state: str, anchors: dict | None) -> list[str]:
    """The search strings, most specific first. With no anchors this is the one query the
    pipeline always ran: '"First Last" obituary City State'. With a middle name on the
    filing the full name is searched first ("David Beardsley Smith" finds one person;
    "David Smith" Washington found three namesakes, 2026-10-01). A DOD year narrows the
    base query."""
    sname = STATE_NAMES.get(state, state)
    an = anchors or {}
    out = []
    full = re.sub(r"\s+", " ", (an.get("decedent_full") or "").replace(".", "")).strip()
    toks = full.split()
    # A one-letter middle INITIAL adds nothing to a quoted query and made '"William A
    # Drews" obituary' return an ancestry listing and two strangers (5418 Ridge Rd,
    # 2026-10-01); only a real middle/maiden name ("Beardsley", "Zlotowitz") is searched.
    if len(toks) >= 3 and full.lower() != name.lower() and all(len(t) >= 2 for t in toks[1:-1]):
        out.append(f'"{full}" obituary')
    yr = (an.get("dod") or "")[:4]
    place = sname if (city or "").lower() in sname.lower() else f"{city} {sname}"   # "Washington Washington DC"
    out.append(f'"{name}" obituary {place}'.strip() + (f" {yr}" if yr else ""))
    return out
def _search_obit(name: str, city: str, state: str, anchors: dict | None = None) -> list[dict]:
    """Obituary web search: DDGS (free) with retries, Firecrawl search when DDGS is throttled.

    DDGS answered the first handful of pilot searches and then returned "No results found."
    for every query -- a rate limit that reads exactly like a person with no obituary. A
    search failure is therefore reported as {"error": ...} (never as an empty list) so the
    caller can refuse to cache the verdict.

    With anchors (cmd_anchors) up to two queries run and their hits are merged, the
    anchored query's hits first.
    """
    from obituary_enricher import _is_obituary_url  # noqa: E402
    merged: list[dict] = []
    seen: set[str] = set()
    last_err = None
    for query in _anchor_queries(name, city, state, anchors):
        results = _search_once(query)
        if results and results[0].get("error"):
            last_err = results[0]
            continue
        for r in results:
            url = r.get("href", "")
            title, body = (r.get("title") or ""), (r.get("body") or "")
            low = (title + " " + body).lower()
            if url in seen:
                continue
            if _is_obituary_url(url) or any(w in low for w in ("obituar", "passed away", "death notice", "funeral")):
                seen.add(url)
                merged.append({"url": url, "title": title, "snippet": body, "query": query})
    if not merged and last_err:
        return [last_err]
    return _rank_hits(merged, name, city, state, anchors)[:8]
def _rank_hits(hits: list[dict], name: str, city: str, state: str, anchors: dict | None) -> list[dict]:
    """Order the merged hits by how much of the OWNER they carry, so the page budget
    (max_fetch, 3) is spent on the likeliest obituary. The first pass judged pages in
    query order and the anchored query's junk exhausted the budget before the base
    query's correct hit (dignitymemorial marriottsville-md/william-drews, 5418 Ridge Rd).
    Score: first + last name in title/url, the property city or state, the filing DOD
    year; genealogy / search listings (ancestry, myheritage, findagrave search, legacy
    search) go last. Stable, so equal scores keep query order."""
    from obituary_enricher import _is_listing_url  # noqa: E402
    toks = [t for t in re.sub(r"[^a-z ]", "", (name or "").lower()).split() if len(t) > 1]
    first, last = (toks[0], toks[-1]) if len(toks) >= 2 else ("", toks[0] if toks else "")
    sname = STATE_NAMES.get(state, state).lower()
    yr = ((anchors or {}).get("dod") or "")[:4]
    def score(h):
        t = ((h.get("title") or "") + " " + (h.get("snippet") or "")).lower()
        u = (h.get("url") or "").lower()
        sc = 0
        if last and (last in t or last in u):
            sc += 2
        if first and (first in t or first in u):
            sc += 2
        if city and city.lower() in t:
            sc += 1
        elif sname and (sname in t or f"-{state.lower()}/" in u):
            sc += 1
        if yr and yr in t:
            sc += 1
        if _is_listing_url(u) or any(x in u for x in ("ancestry.com", "myheritage", "/search", "familysearch")):
            sc -= 10
        if _url_outside_footprint(u) or _title_outside_footprint(h.get("title") or ""):
            sc -= 3
        return -sc
    return sorted(hits, key=score)
def _search_once(query: str) -> list[dict]:
    global _ddgs_dead_until
    from ddgs import DDGS  # noqa: E402
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
    time.sleep(1.5)
    return results
_FOOTPRINT_SLUGS = ("-va/", "-md/", "-dc/", "washingtonpost", "baltimoresun", "fredericknewspost",
                    "capitalgazette", "washingtontimes")
_OUT_OF_STATE_RE = re.compile(
    r"-(al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|ma|mi|mn|ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|"
    r"oh|ok|or|pa|ri|sc|sd|tn|tx|ut|vt|wa|wv|wi|wy)/")
def _url_outside_footprint(url: str) -> bool:
    """dignitymemorial / echovita / legacy-newspaper slugs carry the place: 'brooksville-fl',
    'gaffneyledger'. An obituary published outside MD/DC/VA is a namesake until an anchor
    says otherwise (1403 Horizon Ct, Herndon matched a Gaffney SC obituary, 2026-10-01)."""
    u = (url or "").lower()
    if any(x in u for x in _FOOTPRINT_SLUGS):
        return False
    return bool(_OUT_OF_STATE_RE.search(u))
_TITLE_PLACE_RE = re.compile(r"-\s*([A-Za-z .']+),\s*([A-Z]{2})\s*-")
def _title_outside_footprint(title: str) -> bool:
    """legacy.com titles carry the place: 'Kenneth Gordon Obituary (1948 - 2026) - Gaffney,
    SC - Gaffney Ledger'. A newspaper slug says nothing (gaffneyledger is not '-sc/'), the
    title does. 1403 Horizon Ct, Herndon survived the URL rule on exactly this page."""
    m = _TITLE_PLACE_RE.search(title or "")
    return bool(m) and m.group(2) not in ("MD", "VA", "DC")
ANCHOR_PROMPT = """

KNOWN FACTS from the court filing / vendor record for this owner. Use them to confirm or \
reject the match; they are the only reliable way to tell a namesake apart:
{facts}

Add these keys to the JSON:
- "anchor_hits": array of the fact labels above that the obituary TEXT ITSELF confirms \
(for example ["dod", "middle_name", "pr", "relative:Rhea Smith"]). Confirm a date only \
when the obituary's date of death is within a few days of the filing's. Confirm "pr" or \
a "relative:" only when that person is named in the text. Empty array if none.
- "location_match": true only if the obituary places the person in or near {city}, {state}.

A short common name (two tokens) with NO anchor hit and location_match false is NOT a \
match, whatever the name similarity. Set "match" false in that case."""
def _anchor_facts(anchors: dict | None, rels: list[dict] | None) -> tuple[str, bool]:
    an = anchors or {}
    lines = []
    if an.get("decedent_full"):
        lines.append(f"- full_name: the filing names the decedent as {an['decedent_full']}")
    if an.get("decedent_middle"):
        lines.append(f"- middle_name: {an['decedent_middle']}")
    if an.get("dod"):
        lines.append(f"- dod: date of death on the filing {an['dod']}")
    if an.get("pr_full"):
        lines.append(f"- pr: personal representative {an['pr_full']}"
                     + (f" ({an['pr_mailing']})" if an.get("pr_mailing") else ""))
    for nm in (an.get("pr_others") or [])[:2]:
        lines.append(f"- pr: co-personal representative {nm}")
    names = []
    for r in rels or []:
        nm = (r.get("name") or "").strip()
        if nm and (r.get("first") or "").lower() not in ("", "unknown") and nm not in names:
            names.append(nm)
    for nm in names[:8]:
        lines.append(f"- relative:{nm.title()}: on the skip-trace file as a relative (label unverified)")
    return "\n".join(lines), bool(an.get("dod") or an.get("decedent_middle") or an.get("pr_full"))
ANCHOR_DOD_TOLERANCE_DAYS = 45
def _verify_anchors(parsed: dict, anchors: dict | None, url: str, strong: bool, title: str = "") -> str:
    """Anchor verification IN CODE, after the judge said match=true. Returns the rejection
    reason, or "" when the match stands (and then sets parsed["anchor_hits"] /
    parsed["outside_footprint"]).

    The judge's say-so is not enough: it answered "high" on a Gaffney SC obituary for a
    Herndon owner (1403 Horizon Ct) and on five obituaries dated 2019-2024 against 2026
    filings (7016 Channel Village Ct, 15105 McKnew Rd, 405 Pearl St, ...). Rules:
      - a filing DOD the obituary contradicts by more than ANCHOR_DOD_TOLERANCE_DAYS is a
        different person;
      - with a strong anchor on file (DOD, middle name or PR) the text must confirm at
        least one anchor or place the person in the footprint;
      - an obituary published outside MD/DC/VA needs an anchor hit.
    """
    an = anchors or {}
    hits = [str(h) for h in (parsed.get("anchor_hits") or []) if h]
    p_dod = (parsed.get("date_of_death") or "")[:10]
    if an.get("dod") and p_dod:
        try:
            gap = abs((date.fromisoformat(p_dod) - date.fromisoformat(an["dod"][:10])).days)
        except ValueError:
            gap = None
        if gap is not None and gap > ANCHOR_DOD_TOLERANCE_DAYS:
            return f"obituary DOD {p_dod} vs filing DOD {an['dod']} ({gap} days apart)"
        if gap is not None and "dod" not in hits:
            hits.append("dod")
    outside = _url_outside_footprint(url) or _title_outside_footprint(title)
    if strong and not hits and not parsed.get("location_match"):
        return "no anchor confirmed (filing DOD / middle name / PR / relatives) and no location match"
    if outside and not hits:
        return "obituary published outside MD/DC/VA and no anchor confirmed"
    parsed["anchor_hits"] = hits
    parsed["outside_footprint"] = outside
    return ""
def _llm_match(text: str, owner_name: str, city: str, address: str, state: str,
               anchors: dict | None = None, rels: list[dict] | None = None,
               url: str = "", title: str = "") -> dict | None:
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
    facts, strong = _anchor_facts(anchors, rels)
    if facts:
        prompt += ANCHOR_PROMPT.format(facts=facts, city=city or "the property city", state=sname)
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
    rejected = _verify_anchors(parsed, anchors, url, strong, title)
    if rejected:
        return {"rejected": rejected}
    deceased = parsed.get("full_name") or owner_name
    parsed["survivors"] = _validate_survivors_against_text(parsed.get("survivors", []), text, deceased)
    if parsed.get("preceded_in_death"):
        parsed["preceded_in_death"] = _validate_survivors_against_text(
            parsed.get("preceded_in_death", []), text, deceased)
    return parsed
def _try_person(name: str, rec: dict, max_fetch: int, anchors: dict | None = None,
                rels: list[dict] | None = None) -> tuple[dict | None, list]:
    """Search + fetch + LLM for one candidate decedent. Returns (parsed, trail).

    `anchors` (rec["anchors"], from cmd_anchors) drive the search strings and the judge's
    verification; `rels` are the SmartSkip relatives offered to the judge as names to
    confirm. Both None = the pre-2026-10-01 behaviour."""
    from obituary_enricher import _fetch_page_text  # noqa: E402
    hits = _search_obit(name, rec["city"], rec["state"], anchors)
    trail = [{"name": name, "hits": hits, "queries": _anchor_queries(name, rec["city"], rec["state"], anchors)}]
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
        parsed = _llm_match(text, name, rec["city"], rec["street"], rec["state"], anchors, rels, url,
                            h.get("title") or "")
        trail.append({"url": url, "chars": len(text or ""),
                      "match": bool(parsed and not parsed.get("error") and not parsed.get("rejected")),
                      "error": (parsed or {}).get("error"),
                      "rejected": (parsed or {}).get("rejected"),
                      "anchor_hits": (parsed or {}).get("anchor_hits")})
        if parsed and parsed.get("rejected"):
            parsed = None
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
    if getattr(a, "keys_file", None):
        kf = Path(a.keys_file)
        want = (json.loads(kf.read_text(encoding="utf-8")) if kf.suffix == ".json"
                else [ln.strip() for ln in kf.read_text(encoding="utf-8").splitlines() if ln.strip()])
        want = set(want)
        keys = [k for k in keys if k in want]
        say(f"research: --keys-file {kf.name}: {len(keys)} of {len(want)} keys found in this run")
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
            anchors = rec.get("anchors") or {}
            if anchors:
                f["anchors_used"] = {k: anchors[k] for k in ("dod", "case_number", "estate_number",
                                                             "decedent_full", "pr_full") if anchors.get(k)}
            # The CRM already knows who died on a probate/RoW record. When it names someone
            # who is NOT the owner, that is authoritative -- it came off the court filing --
            # and searching the owner for an obituary can only manufacture a false death:
            # any same-name hit becomes "owner did die" and rank re-roots the family around
            # a living person (7854 Linden Leaf Rd, 2026-09-23: three false signers, a real
            # title name dropped over the cap, a nameless ghost promoted to decision-maker).
            # So: research the NAMED DECEDENT for the heir graph, and never search the owner.
            crm_decedent = f"{rec.get('decedent_first') or ''} {rec.get('decedent_last') or ''}".strip()
            if crm_decedent and not names_match(crm_decedent, owner_name):
                f["decedent_from_crm"] = crm_decedent
                p0, trail0 = _try_person(crm_decedent, rec, a.max_fetch, anchors, rk["rels"])
                f["searched"].append({"who": "crm decedent", "name": crm_decedent, "trail": trail0})
                # "owner alive" is the substring cmd_build keys on to load the OWNER'S own
                # numbers -- correct here: a living PR is exactly who the dialer must reach.
                f["verdict"] = "named decedent died; owner alive (likely the PR)"
                f["decedent_name"] = (p0 or {}).get("full_name") or crm_decedent
                if p0:
                    f["dod"] = p0.get("date_of_death") or ""
                    f["dod_source"] = "obituary" if f["dod"] else ""
                    f["obituary_url"] = p0.get("obituary_url")
                    f["confidence"] = p0.get("confidence")
                    f["survivors"] = p0.get("survivors") or []
                    f["preceded"] = p0.get("preceded_in_death") or []
                    f["executor_named"] = p0.get("executor_named") or ""
                    f["evidence"] = (f"CRM names {crm_decedent} as the decedent, not the owner "
                                     f"{owner_name}; obituary confirms ({p0.get('obituary_url')})")
                else:
                    # No obituary is NOT a reason to doubt the CRM: plenty of estates never
                    # publish one (7854 Linden Leaf had none; the Register of Wills filing
                    # was the proof). The verdict stands on the court record, and the
                    # missing obituary is stated as a gap in the heir graph, not as doubt.
                    f["survivors"] = []
                    f["preceded"] = []
                    f["flags"] = ["no obituary found for the named decedent; "
                                  "survivors unknown -- heirs not corroborated"]
                    f["evidence"] = (f"CRM names {crm_decedent} as the decedent, not the owner "
                                     f"{owner_name}; no obituary found, verdict rests on the "
                                     f"CRM/court record")
                parsed = None          # skip the owner-obituary branch entirely
                if not f.get("dod") and anchors.get("dod"):
                    f["dod"], f["dod_source"] = anchors["dod"], anchors.get("dod_source") or "court_record"
            else:
                parsed, trail = _try_person(owner_name, rec, a.max_fetch, anchors, rk["rels"])
                f["searched"].append({"who": "owner", "trail": trail})
            if f.get("decedent_from_crm"):
                pass                   # verdict already settled above
            elif parsed:
                f["verdict"] = "owner did die"
                f["decedent_name"] = parsed.get("full_name")
                f["dod"] = parsed.get("date_of_death") or ""
                f["dod_source"] = "obituary" if f["dod"] else ""
                if not f["dod"] and anchors.get("dod"):
                    f["dod"], f["dod_source"] = anchors["dod"], anchors.get("dod_source") or "court_record"
                f["obituary_url"] = parsed.get("obituary_url")
                f["confidence"] = parsed.get("confidence")
                f["anchor_hits"] = parsed.get("anchor_hits") or []
                f["survivors"] = parsed.get("survivors") or []
                f["preceded"] = parsed.get("preceded_in_death") or []
                f["executor_named"] = parsed.get("executor_named") or ""
                f["evidence"] = (f"obituary names {parsed.get('full_name')} ({parsed.get('obituary_url')})"
                                 + (f"; anchors confirmed: {', '.join(f['anchor_hits'])}" if f["anchor_hits"] else ""))
                if parsed.get("outside_footprint"):
                    f.setdefault("extra_flags", []).append("obituary_outside_footprint")
            elif anchors.get("dod"):
                # The court record says the OWNER died (the probate row names them as the
                # decedent and carries a date of death). No obituary matched -- that is a
                # gap in the heir graph, not doubt about the death, and it must never fall
                # through to the spouse search (which would call the owner alive) or to
                # "unresolved" (which printed the intestacy template over SmartSkip labels).
                f["verdict"] = "owner did die"
                f["decedent_name"] = anchors.get("decedent_full") or owner_name
                f["dod"] = anchors["dod"]
                f["dod_source"] = anchors.get("dod_source") or "court_record"
                f["confidence"] = "court record"
                f["survivors"], f["preceded"] = [], []
                f["executor_named"] = ""
                f["evidence"] = (f"death on the court/vendor record (DOD {anchors['dod']}"
                                 + (f", case {anchors['case_number']}" if anchors.get("case_number") else "")
                                 + (f", estate {anchors['estate_number']}" if anchors.get("estate_number") else "")
                                 + "); no obituary matched - heirs NOT identified by research")
                f.setdefault("extra_flags", []).append("no obituary found; death from the court record only")
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
            cc = _cross_check(f, rk["rels"])
            f["confirmed_rels"] = cc["confirmed"]
            f["obit_only_names"] = cc["obit_only"]
            # dict.fromkeys: names_match is fuzzy and matched the same person repeatedly.
            # STRICT here: 867 of 871 cached `preceded` entries are bare strings with no
            # relationship word to corroborate, and a false "predeceased" drops a living
            # signer from the dial list ("preceded by Chris Swam" must not drop CHRISTINE).
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
            flags += f.pop("extra_flags", [])
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

    # A subset run (--key / --limit / --keys-file) must not shrink the file to the subset:
    # build reads stage_c_research.json for EVERY record. Keep the untouched findings.
    done = {f["record"] for f in findings}
    prior = (jload(run / "stage_c_research.json", {}) or {}).get("findings", [])
    kept = [f for f in prior if f.get("record") not in done and f.get("record") in ranked]
    if kept:
        say(f"stage_c_research.json: {len(findings)} researched now + {len(kept)} kept from the previous file")
    jdump(run / "stage_c_research.json", {"generated_at": datetime.now().isoformat(timespec="seconds"),
                                          "findings": findings + kept})
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

# Labelling ONLY (the REL line in the note). OBIT_REL_MAP above decides who gets traced and
# who is a signer and is deliberately untouched: a niece is a real relationship worth
# printing, not a $0.02 trace target or an intestacy heir.
OBIT_LABEL_MAP = {**OBIT_REL_MAP,
                  "stepson": "Stepson", "stepdaughter": "Stepdaughter",
                  "niece": "Niece", "nephew": "Nephew", "aunt": "Aunt", "uncle": "Uncle",
                  "cousin": "Cousin", "grandson": "Grandchild", "granddaughter": "Grandchild",
                  "grandchild": "Grandchild", "great-grandchild": "Great-grandchild",
                  "son-in-law": "In-Law", "daughter-in-law": "In-Law", "sister-in-law": "In-Law",
                  "brother-in-law": "In-Law", "mother-in-law": "In-Law", "father-in-law": "In-Law",
                  "niece-in-law": "In-Law", "nephew-in-law": "In-Law",
                  "companion": "Partner", "fiance": "Partner", "fiancee": "Partner",
                  "executor": "Executor", "personal representative": "Personal Representative"}
_GENERIC_RELS = {"Relative", "In-Law", "Unknown", "", None}
def _obituary_words(f: dict, rels: list[dict]) -> dict[str, str]:
    """SmartSkip relative name -> the obituary's relationship word, 1:1 (see _cross_check)."""
    return _cross_check(f, rels)["words"]
def _rel_label(rel: dict, obit_words: dict[str, str]) -> str:
    """The relationship printed on a REL line. SmartSkip's canon_rel, enriched with the
    obituary's word when the obituary names this person: a "Relative, 44" the obituary
    calls a niece prints "Niece (obituary), 44"."""
    canon = rel.get("canon_rel") or "Relative"
    word = obit_words.get(rel.get("name") or "")
    if not word:
        return canon
    lab = OBIT_LABEL_MAP.get(word) or word.title()
    if canon in _GENERIC_RELS or canon == lab:
        return f"{lab} (obituary)"
    return f"{canon}, {lab} (obituary)"
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
        # 1:1 pairing: "Judy McAuley" paired with JUDITH MCAULEY is not traced again, but
        # "Paula Smith" next to a lone PAUL SMITH (prefix collision) still is.
        already = {n.lower() for n in _cross_check(f, rk["rels"])["paired_survivors"]}
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
            if nm.lower() in already:
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
    if t.get("litigator") is None:
        return False, "litigator unchecked"
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
    records = jload(run / "records.json", {})
    numbers: list[str] = []
    n_pr_added = 0
    for key, rk in ranked.items():
        f = research.get(key, {})
        v = f.get("verdict") or ""
        # "presumed alive" is the FTM-cohort verdict (VERDICT_PRESUMED): the owner is the
        # decision maker there too, so their own numbers are priced like any signer's.
        owner_alive = "owner alive" in v or "presumed alive" in v
        # Price the SIGNERS' numbers only (Basem, 2026-09-16). Under the signers-only
        # build a non-signer's number can never reach the dial list, so pricing it buys
        # nothing -- and this was the largest line in the bill: Trestle $146.12 across
        # 9,741 numbers on the 619-record obituary cohort. On an owner-alive record no
        # relative is loaded at all, so none of them are priced either.
        #
        # THE ONE EXCEPTION, and it must stay: if the signer set came back with no phone
        # at all, signers-only would take the record dark -- an unscored number fails
        # _keep_number, so skipping the blood tier here would also strip it at build.
        # For that record only, fall back to the blood tier so the saving never costs a
        # callable lead. `build` applies the same rule and tags the record.
        sig = [r for r in rk["rels"] if r.get("is_dm")]
        if owner_alive:
            # Phoneless living owner: price the family, they are the only route to them.
            pool = [] if rk["subject"]["phones"] else [r for r in rk["rels"] if not r.get("is_channel")]
        elif any(r.get("phones") for r in sig):
            pool = sig
        else:
            pool = [r for r in rk["rels"] if not r.get("is_channel")]
        # The Personal Representative is loaded on EVERY path (PR fields + Pr.N numbers,
        # Basem 2026-09-30), so their numbers are priced whether or not the intestacy
        # rank made them a signer -- an unscored number fails _keep_number at build, and
        # a PR with no callable number is the one person the dialers most need.
        pr_rel = _pick_pr(rk["rels"], (records.get(key) or {}).get("pr") or "")
        if pr_rel is not None and pr_rel not in pool:
            pool = list(pool) + [pr_rel]
            n_pr_added += 1
        for rel in pool:
            for p in (rel["phones"][:cut] if cut else rel["phones"]):
                numbers.append(p["number"])
        if owner_alive:
            for p in (rk["subject"]["phones"][:cut] if cut else rk["subject"]["phones"]):
                numbers.append(p["number"])
    # A cache entry with litigator None was scored WITHOUT the add-on (before 2026-10-05):
    # it is UNCHECKED, not clean, and is re-scored. Litigator scrub is mandatory (Basem).
    uniq = sorted({n for n in numbers if n and (n not in cache or cache[n].get("error")
                                                or cache[n].get("litigator") is None)})
    if a.limit:
        uniq = uniq[: a.limit]
    say(f"trestle: {len(set(numbers))} unique numbers referenced, {len(uniq)} to score "
        f"(~${0.015 * len(uniq):.2f}), {len(cache)} cached"
        + (f"; {n_pr_added} non-signer PRs added to the priced pool" if n_pr_added else ""))
    if uniq:
        # phone_validator.call_trestle goes through `requests`. obituary_dp_run.trestle_score
        # uses urllib and Trestle's edge answers the default Python-urllib user agent with
        # 403 AUTHENTICATION_FAILED on a key that works -- 390 of 390 pilot calls "failed"
        # that way before this was traced to the transport, not the credential.
        import phone_validator as pv  # noqa: E402
        for i, n in enumerate(uniq, 1):
            d = pv.call_trestle(n, api_key)  # litigator add-on always on
            if not isinstance(d, dict) or d.get("error") and not d.get("phone_number"):
                cache[n] = {"error": str((d or {}).get("error") or d)[:120]}
            else:
                score = d.get("activity_score")
                lit = ((d.get("add_ons") or {}).get("litigator_checks") or {}).get("phone.is_litigator_risk")
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
                             "kinship_source": ("obituary" if rel["name"] in (f.get("confirmed_rels") or [])
                                                else "filing (PR)" if rel.get("pr_on_record")
                                                or rel.get("canon_rel") == "Personal Representative"
                                                else "smartskip label"),
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
    "obituary_outside_footprint": "obituary published OUTSIDE MD/DC/VA - namesake risk, verify",
    "two_spouses": "two or more relatives labelled Spouse - a re-root artefact, verify who the spouse is",
    "unknown_name_signer": "a signer with no first name on file is loaded - identify before calling",
    "label_only_signers": "signer relationships are SmartSkip labels, not confirmed by an obituary or court record",
}
# _push_one_api posts note[:4000] on the message route (the 2000 cap belonged to the dead
# add-notes route). 1391 Pennsylvania's 2,515-char note was stored and read back in full
# on 2026-10-01. Stay under 4000 with room for the "NOT LOADED" trimming below.
NOTE_MAX = 3900
# Measured live 2026-09-01 on 1815 Drew St: 39 phones sent, 200 + "added" for all of them,
# 30 stored. The limit is the account's, not ours.
OWNER_PHONE_CAP = 30
# Ty's hard cap on how many numbers one record's dial list may carry. DataSift's own
# ceiling is OWNER_PHONE_CAP; this is the QUALITY ceiling under it -- 30 numbers on a
# record is a wall, not a call list. Basem 2026-09-16.
DIAL_LIST_CAP = 20
# The relationship word that rides ON the phone tag (Ty's REL_TAG). Anything not named
# here stays unlabelled rather than being called "Relative": a wrong kinship word on a
# dial sheet is worse than none.
REL_TAG_WORD = {"Son": "Son", "Daughter": "Daughter", "Child": "Child",
                "Husband": "Husband", "Wife": "Wife", "Spouse": "Spouse",
                "Mother": "Mother", "Father": "Father", "Parent": "Parent",
                "Brother": "Brother", "Sister": "Sister", "Sibling": "Sibling"}

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
    "vendor_probate_file": "probate filing on the vendor file",
    "register_of_wills": "Register of Wills estate record",
    "court_record": "court record",
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
def _pick_pr(rels: list[dict], pr_name: str) -> dict | None:
    """The relative who IS the Personal Representative named on the filing, or None.

    Basem, 2026-09-30: the PR goes in the PR fields, never a REL slot, and when a relative
    is also the PR the PR role wins. Three ways a relative is the PR, tried in order, each
    accepted only when it singles out ONE person: an IDI merge already flagged them
    (`pr_on_record` / canon_rel "Personal Representative"), a strict first+last match, then
    the loose nickname match -- Bill Brent IS William Brent, Betsy Haines IS Elizabeth
    Haines (3 of the 97 Sept PRs found in SmartSkip). Two candidates = ambiguous = nobody,
    and `build` prints a flag so the caller sees both stayed REL.
    """
    if not (pr_name or "").strip():
        return None
    flagged = [r for r in rels if r.get("pr_on_record") or r.get("canon_rel") == "Personal Representative"]
    if flagged:
        return flagged[0] if len(flagged) == 1 else None
    for matcher in (names_match, names_match_loose):
        hits = [r for r in rels if matcher(r.get("name") or "", pr_name)]
        if hits:
            return hits[0] if len(hits) == 1 else None
    return None


def _split_pr_name(pr_name: str) -> tuple[str, str]:
    """'James K Heins' -> ('James K', 'Heins'); 'Anthony LaScola' -> ('Anthony', 'Lascola').

    Title case with periods stripped, the way the account stores PR First/Last (vendor
    imports + rel_pr_name_repair.title_name: 'Jennifer M' / 'Mckee'). A single-letter
    middle rides with the first name (that is where the live records carry it); a suffix
    stays with the surname. No period anywhere: a period in a name is what flips a
    DataSift owner block to Incomplete (CLAUDE.md, lane cleanup)."""
    toks = [t for t in (pr_name or "").replace(".", " ").split() if t]

    def tc(w: str) -> str:
        if w.lower() in ("ii", "iii", "iv"):
            return w.upper()
        part = "-".join(x[:1].upper() + x[1:].lower() for x in w.split("-"))
        part = re.sub(r"^(Mc)(\w)", lambda m: m.group(1) + m.group(2).upper(), part)
        return re.sub(r"^(O')(\w)", lambda m: m.group(1) + m.group(2).upper(), part)

    if not toks:
        return "", ""
    if len(toks) == 1:
        return tc(toks[0]), ""
    first = [toks[0]]
    rest = toks[1:]
    while len(rest) > 1 and len(rest[0]) == 1:
        first.append(rest.pop(0))
    return " ".join(tc(t) for t in first), " ".join(tc(t) for t in rest)


def _trace_month(run: Path) -> str:
    """MM/YYYY of the SmartSkip TRACE, read off the paid order's id.

    A bulkSkipId is a Mongo ObjectId: its first 8 hex chars are the creation time
    (6abd6a32... -> 2026-09-30, 6abc220d... -> 2026-09-29, 6a9735af... -> 2026-09-01, all
    three checked against the known order dates). The trace tag must say the month the
    trace HAPPENED, never the month of a rebuild (Basem, 2026-09-22): the Sept Drive batch
    was traced 09-29 and is rebuilt in October. Empty string when no order id is on file."""
    orders = jload(run / "smartskip_orders.json", []) or []
    paid = [o for o in orders if o.get("paid")] or orders
    for o in reversed(paid):
        oid = str(o.get("bulkSkipId") or "")
        if re.fullmatch(r"[0-9a-f]{24}", oid):
            return time.strftime("%m/%Y", time.gmtime(int(oid[:8], 16)))
    return ""

# ───────────────────────── v5 method: title / court parties / deed index ─────────────────────────
# Basem, 2026-10-01: a batch record must carry what the single-record v5 session carried
# on 1391 Pennsylvania #433 -- owner of record (tax roll), the deed party index, the court
# party list, every SmartSkip / IDI name reconciled against them, and a who-must-sign line
# that names the open question. The three sidecars are produced by dp_title.py,
# dp_court_parties.py and dc_rod_lookup.py (rerun_v5/{title,court,deed}.json) and merged
# into records.json[key]["title" | "court" | "deed"] by `v5merge`. Everything below only
# READS them; a record with no sidecar renders exactly as before.

_V5_SIGNER_LABEL = {"court": " - SIGNER (court)", "title": " - SIGNER (title)",
                    "obituary": " - SIGNER (obituary)", "label": " - SIGNER (label only)"}
_V5_KINSHIP = {"court": "court", "title": "title", "obituary": "obituary", "label": "smartskip label"}


def _natural_name(n: str) -> str:
    """'Berk, Ben A' / 'SMITH, HELEN H' -> 'Ben A Berk' / 'HELEN H SMITH'."""
    n = (n or "").strip()
    if "," in n:
        parts = [x.strip() for x in n.split(",") if x.strip()]
        # "Morgan, Jr, Donald G" / "Morgan, Donald G, Jr": the suffix segment goes last.
        suffix = [x for x in parts[1:] if x.lower().strip(".") in ORDINALS]
        rest = [x for x in parts[1:] if x not in suffix]
        n = " ".join(rest + [parts[0]] + suffix)
    return re.sub(r"\s+", " ", n).strip()


def _v5_party_names(court: dict) -> list[str]:
    out = []
    for p in (court or {}).get("pr") or []:
        out.append(_natural_name(p.get("name") if isinstance(p, dict) else str(p)))
    for h in (court or {}).get("heirs") or []:
        out.append(_natural_name(h.get("name") if isinstance(h, dict) else str(h)))
    return [x for x in out if x]


def _v5_has_case(court: dict) -> bool:
    # "anchors" (MD / VA: a PR name from the vendor file, no party list) is NOT a court
    # party list -- no COURT PARTIES block, no who-must-sign prefix from it.
    return bool((court or {}).get("case_no")) and (court or {}).get("join_basis") in ("case_number", "address", "name_only")


def _v5_on_court_record(name: str, court: dict) -> bool:
    if not name or not _v5_has_case(court):
        return False
    return any(names_match(name, c) or names_match_loose(name, c) for c in _v5_party_names(court))


_TRUSTEE_WORDS = {"TRUSTEE", "TRUSTEES", "TR", "TRS", "ETAL", "AL", "ET"}


def _v5_strip_trustee(o: str) -> str:
    """'ESSIE M COBB TRUSTEE' -> 'ESSIE M COBB'; 'NORMAN M LINSKY TR' -> 'NORMAN M LINSKY'."""
    toks = _natural_name(o).split()
    while toks and toks[-1].upper().strip(".,") in _TRUSTEE_WORDS:
        toks.pop()
    return " ".join(toks)


def _v5_title_owner_names(title: dict) -> list[str]:
    return [_v5_strip_trustee(o) for o in ((title or {}).get("owners") or []) if o and _v5_strip_trustee(o)]


def _v5_rel_basis(rel: dict, court: dict, title: dict, confirmed_set: set, decedent: str) -> str:
    """court > title > obituary > label. A title owner who matches the decedent is the
    decedent, not a 'title' basis for a relative."""
    nm = rel.get("name") or ""
    if _v5_on_court_record(nm, court):
        return "court"
    for o in _v5_title_owner_names(title):
        if (names_match(nm, o) or names_match_loose(nm, o)) and not (decedent and _matches_decedent(decedent, o)):
            return "title"
    if nm in confirmed_set:
        return "obituary"
    return "label"


def _v5_addr(p: dict) -> str:
    parts = [p.get("street") or "", " ".join(x for x in (p.get("city") or "", p.get("state") or "") if x),
             p.get("zip") or ""]
    s = ", ".join(x for x in parts[:2] if x)
    return (s + (" " + parts[2] if parts[2] else "")).strip() or "address not on the record"


def _v5_decedent_names(rec: dict, f: dict) -> list[str]:
    an = rec.get("anchors") or {}
    cands = [f.get("decedent_name") or "", an.get("decedent_full") or "",
             f"{rec.get('owner_first') or ''} {rec.get('owner_last') or ''}",
             f"{rec.get('decedent_first') or ''} {rec.get('decedent_last') or ''}",
             f"{rec.get('ss_first') or ''} {rec.get('ss_last') or ''}"]
    return [re.sub(r"\s+", " ", c).strip() for c in cands if c and c.strip()]


def _v5_co_owners(rec: dict, f: dict, title: dict, court: dict) -> list[str]:
    """Owners of record who are neither the decedent, nor the PR, nor a court party.
    1391 Pennsylvania: HELEN H SMITH on title, not in the probate -- she decides the deal."""
    if not (title or {}).get("owners"):
        return []
    an = rec.get("anchors") or {}
    known = _v5_decedent_names(rec, f) + _v5_party_names(court)
    for pr in (an.get("pr_full"), rec.get("pr")):
        if pr:
            known.append(pr)
    # dp_title's own classification (exact / probable spelling match) counts as known too.
    for k in (title.get("decedent_on_title") or []) + (title.get("decedent_probable") or []):
        known.append(_v5_strip_trustee(k))
    out = []
    for o in _v5_title_owner_names(title):
        if any(names_match(o, k) or names_match_loose(o, k) or _matches_decedent(k, o) for k in known):
            continue
        out.append(o)
    return out


def _v5_title_line(rec: dict, title: dict, co_owners: list[str], has_case: bool = False) -> str:
    st = (rec.get("state") or "").upper()
    if not title:
        return ""
    if title.get("source") == "itspe" and title.get("owners"):
        ssl = f", SSL {title['ssl']}" if title.get("ssl") else ""
        price = title.get("sale_price")
        try:
            price_txt = f" for ${int(float(price)):,}" if price not in (None, "", 0, "0") else ""
        except (TypeError, ValueError):
            price_txt = f" for {price}" if price else ""
        sale = (f"bought {title['sale_date']}{price_txt}" if title.get("sale_date") and price_txt
                else f"last transfer {title['sale_date']} ($0 - family / trust deed)" if title.get("sale_date")
                else "no sale date on the roll")
        owners_txt = " + ".join(_natural_name(o) for o in title.get("owners") or [])
        line = f"TITLE (DC tax roll{ssl}): {owners_txt} - {sale}"
        mail = (title.get("owner_mailing") or "").strip()
        if mail and norm_addr(mail)[:12] != norm_addr(rec.get("street") or "")[:12]:
            line += f"; mailing {mail}"
        if title.get("trust_vesting"):
            line += "; held AS TRUSTEE - the successor trustee signs, trust instrument unread"
        if title.get("decedent_probable") and not title.get("decedent_on_title"):
            line += f"; decedent matched by roll spelling ({', '.join(title['decedent_probable'])}) - confirm on the deed"
        m = re.search(r"holds \d+ parcels in the building: [^;]+", title.get("match_note") or "")
        if m:
            line += "; " + m.group(0)
        if title.get("owner_of_record_is_decedent") is False:
            line += (". DECEDENT NOT ON TITLE" + (f" (last transfer {title['sale_date']})" if title.get("sale_date") else "")
                     + " - the owners above hold it; the estate may own nothing here")
        elif co_owners:
            who = " and ".join(co_owners)
            if has_case:
                line += (f". {who} {'is' if len(co_owners) == 1 else 'are'} NOT a party to the "
                         "probate and not the decedent - living / deceased / divorced UNKNOWN")
            else:
                line += (f". {who} {'is' if len(co_owners) == 1 else 'are'} also on title and not "
                         "the decedent - living / deceased UNKNOWN; if living with survivorship, "
                         + ("they own" if len(co_owners) > 1 else "that person owns") + " the property outright")
        return line
    if title.get("source") == "sdat" and title.get("owners"):
        # MD: SDAT Real Property (dp_title.title_for_md, 2026-10-02). Account = district-account;
        # the roll's Transfer Information gives the last recorded transfer (date / price / type).
        acct = f", acct {title['ssl']}" if title.get("ssl") else ""
        price = title.get("sale_price")
        kind = (title.get("sale_type") or "").strip()
        if title.get("sale_date") and price not in (None, "", 0, "0"):
            try:
                sale = f"last transfer {title['sale_date']} for ${int(float(price)):,}"
            except (TypeError, ValueError):
                sale = f"last transfer {title['sale_date']} for {price}"
        elif title.get("sale_date"):
            sale = f"last transfer {title['sale_date']} ($0 - {kind.lower() if kind else 'family / trust deed'})"
            kind = ""
        else:
            sale = "no transfer date on the roll"
        if kind and "ARMS LENGTH" in kind.upper() and "NON" not in kind.upper():
            sale += ", arms-length sale"
        elif kind:
            sale += f", {kind.lower()}"
        owners_txt = " + ".join(_natural_name(o) for o in title.get("owners") or [])
        line = f"TITLE (MD SDAT roll{acct}): {owners_txt} - {sale}"
        mail = (title.get("owner_mailing") or "").strip()
        if mail and norm_addr(mail)[:12] != norm_addr(rec.get("street") or "")[:12]:
            line += f"; mailing {mail}"
        if title.get("principal_residence") == "NO":
            line += "; NOT the owner's principal residence per the roll"
        if title.get("trust_vesting"):
            line += "; held AS TRUSTEE - the successor trustee signs, trust instrument unread"
        if title.get("decedent_probable") and not title.get("decedent_on_title"):
            line += f"; decedent matched by roll spelling ({', '.join(title['decedent_probable'])}) - confirm on the deed"
        if title.get("owner_of_record_is_decedent") is False:
            line += (". DECEDENT NOT ON TITLE" + (f" (last transfer {title['sale_date']})" if title.get("sale_date") else "")
                     + " - the owners above hold it; the estate may own nothing here")
        elif co_owners:
            who = " and ".join(co_owners)
            if has_case:
                line += (f". {who} {'is' if len(co_owners) == 1 else 'are'} NOT a party to the "
                         "probate and not the decedent - living / deceased / divorced UNKNOWN")
            else:
                line += (f". {who} {'is' if len(co_owners) == 1 else 'are'} also on title and not "
                         "the decedent - living / deceased UNKNOWN; if living with survivorship, "
                         + ("they own" if len(co_owners) > 1 else "that person owns") + " the property outright")
        return line
    if title.get("source") == "itspe" or (st == "DC" and title.get("source") == "none"):
        return "TITLE: no tax-roll match for this address (ITSPE) - VERIFY the owner of record by hand"
    if st == "MD" and title.get("source") in ("sdat", "none") and title.get("match_note", "").startswith("SDAT"):
        return "TITLE: no SDAT parcel for this street address - VERIFY the owner of record by hand"
    if st == "MD" and title.get("match_note", "").startswith("lookup raised"):
        return "TITLE: SDAT lookup FAILED (scrape error) - VERIFY the owner of record by hand"
    if st == "MD":
        return "TITLE: not checked (SDAT lookup needs a Firecrawl go)"
    if st == "VA":
        return "TITLE: not checked (no VA fetcher)"
    return ""


# dc_rod_lookup flags every instrument by type. Routine financing (deed of trust, release,
# condo declaration, POA) tells a dialer nothing; these do.
# "trust" in the DC index is DEED OF TRUST paper (a mortgage), never a trust transfer (trap, 2026-10-02).
_DEED_NOISE_FLAGS = {"deed", "mortgage", "release", "condo / declaration", "power of attorney",
                     "common name - capped", "no index hit", "trust", "affidavit", "special warranty",
                     "correction", "lien"}
_CONVEYANCE_TYPES = re.compile(r"\b(deed|quit ?claim|transfer on death|distribution|executor|personal rep|pr deed)\b", re.I)


def _v5_is_conveyance(ins: dict) -> bool:
    if ins.get("is_conveyance") is not None:
        return bool(ins["is_conveyance"])
    fl = set(ins.get("flags") or [])
    if fl & {"deed", "transfer on death deed", "quitclaim", "pr deed", "executor's deed", "deed of distribution"}:
        return True
    dt = (ins.get("doc_type") or "").upper()
    return bool(_CONVEYANCE_TYPES.search(dt)) and dt not in ("TRUST", "DEED OF TRUST")


def _v5_last_conveyance(deed: dict) -> dict | None:
    """dc_rod_lookup's own ON-PARCEL pick when it made one; else the latest conveyance among
    the instruments that are on the parcel (an off-parcel namesake deed never counts:
    2311 15th St NW's 2026 Cox -> Ferguson deed is another square)."""
    if (deed or {}).get("last_conveyance"):
        return deed["last_conveyance"]
    conv = [i for i in (deed or {}).get("instruments") or []
            if _v5_is_conveyance(i) and i.get("on_parcel") is not False]
    conv.sort(key=lambda i: _iso_date(i.get("recorded_date") or "") or "")
    return conv[-1] if conv else None


def _v5_deed_flags(deed: dict) -> list[str]:
    return [x for x in dict.fromkeys((deed or {}).get("flags") or []) if x not in _DEED_NOISE_FLAGS]


def _v5_deed_line(rec: dict, deed: dict) -> str:
    st = (rec.get("state") or "").upper()
    if not deed:
        return ""
    md = deed.get("source") == "mdlandrec"
    index_name = "MDLANDREC index" if md else "Recorder of Deeds index"
    span = "" if md else " since 1990"
    inst = deed.get("instruments") or []
    if inst:
        conv = _v5_last_conveyance(deed)
        last = deed.get("last_instrument") or inst[0]
        fl = _v5_deed_flags(deed)
        if conv:
            legal = f" ({conv['legal']})" if conv.get("legal") else ""
            if md and not (conv.get("grantor") and conv.get("grantee")):
                party = (f"grantee {conv['grantee']}" if conv.get("grantee") else f"grantor {conv.get('grantor') or '?'}")
                head = (f"DEED ({index_name}): last conveyance {conv.get('recorded_date') or '?'} "
                        f"{conv.get('doc_type') or '?'}, {party} ({conv.get('doc_number') or '?'}){legal}")
            else:
                head = (f"DEED ({index_name}): last conveyance {conv.get('recorded_date') or '?'} "
                        f"{conv.get('doc_type') or '?'} {conv.get('grantor') or '?'} -> {conv.get('grantee') or '?'}{legal}")
            if last.get("doc_number") != conv.get("doc_number") and _iso_date(last.get("recorded_date") or "") > _iso_date(conv.get("recorded_date") or ""):
                head += f"; later {last.get('doc_type') or '?'} {last.get('recorded_date')}"
        else:
            head = (f"DEED ({index_name}): no conveyance on this parcel under the searched names{span}; "
                    f"last instrument {last.get('recorded_date') or '?'} {last.get('doc_type') or '?'}")
        n_on = deed.get("n_on_parcel")
        count = (f"{n_on} on this parcel of {len(inst)} under the names" if n_on is not None
                 else f"{len(inst)} instruments{span}" + (" (parcel unconfirmed - no SDAT reference)" if md else ""))
        return (head + f"; {count}" + (f"; {', '.join(fl)}" if fl else "")
                + f"; vesting {deed.get('vesting') or 'unread - image $4'}")
    if deed.get("searches"):
        terms = ", ".join(dict.fromkeys(s.get("term") or "" for s in deed["searches"] if s.get("term")))
        capped = any(s.get("capped") for s in deed["searches"])
        if any(s.get("error") for s in deed["searches"]) and not any(not s.get("error") for s in deed["searches"]):
            return f"DEED: {index_name} search FAILED (scrape / login error) - index unread"
        return (f"DEED: no {index_name} hit under {terms}" + ("" if md else " (1990-2026)")
                + ("; common name - search capped" if capped else "") + "; vesting unread")
    if st == "MD":
        return "DEED: not checked (MDLANDREC grantee search needs a go)"
    if st == "VA":
        return "DEED: not checked (no VA fetcher)"
    return ""


def _v5_set_aside_rels(rk: dict) -> list[dict]:
    """The SmartSkip family rank set aside as a namesake's (1391: SARAH DEVARY, who the court
    then named as a legatee). Their numbers are still on the record."""
    ex = (rk or {}).get("excluded_smartskip_namesake") or {}
    return list(ex.get("rels") or [])


def _v5_court_overruled(court: dict, rk: dict) -> list[str]:
    """Court parties who sit in the namesake set-aside: the set-aside was wrong."""
    out = []
    for nm in _v5_party_names(court) if _v5_has_case(court) else []:
        for r in _v5_set_aside_rels(rk):
            if names_match(r.get("name") or "", nm) or names_match_loose(r.get("name") or "", nm):
                out.append(r.get("name") or nm)
    return list(dict.fromkeys(out))


def _v5_court_lines(court: dict, rk: dict, basis_by_name: dict, loaded_by_n: dict,
                    pr_rel: dict | None, cache: dict) -> list[str]:
    if not _v5_has_case(court):
        return []
    rels = (rk or {}).get("rels") or []
    set_aside = _v5_set_aside_rels(rk)
    will = court.get("will_on_file")
    head = (f"COURT PARTIES ({court.get('case_no')}"
            + (f", filed {court['filing_date']}" if court.get("filing_date") else "")
            + (f", {court['status']}" if court.get("status") else "")
            + ("; a WILL is on file - heirs are Legatees" if will else
               "; heirs listed, no will indicated" if will is False else "") + ")")
    lines = [head]

    def _match(nm: str) -> str:
        for r in rels:
            if names_match(r.get("name") or "", nm) or names_match_loose(r.get("name") or "", nm):
                if pr_rel is not None and r is pr_rel:
                    return " - in the PR fields" + (" (" + " / ".join(loaded_by_n.get(-1, [])) + ")" if loaded_by_n.get(-1) else ", no callable number")
                tiers = loaded_by_n.get(r.get("n"))
                best = ""
                if not tiers:
                    tt = sorted((cache.get(p["number"], {}).get("tier") or "unscored" for p in r.get("phones") or []),
                                key=lambda t: TIER_RANK.get(t, 8))
                    best = f"; best number on file {tt[0]}" if tt else ""
                return (f" - SmartSkip/IDI REL{r.get('n')}" + (" (" + " / ".join(tiers) + ")" if tiers else f", not loaded{best}"))
        for r in set_aside:
            if names_match(r.get("name") or "", nm) or names_match_loose(r.get("name") or "", nm):
                tt = sorted((cache.get(p["number"], {}).get("tier") or "unscored" for p in r.get("phones") or []),
                            key=lambda t: TIER_RANK.get(t, 8))
                return (" - SmartSkip had them (set aside as a namesake's family; the court says otherwise)"
                        + (f"; best number on file {tt[0]}" if tt else "; no number"))
        return " - no number on file"

    for p in court.get("pr") or []:
        nm = _natural_name(p.get("name"))
        lines.append(f"PR - {nm} ({p.get('connection') or 'Applicant'}) - {_v5_addr(p)}{_match(nm)}")
    for h in court.get("heirs") or []:
        nm = _natural_name(h.get("name"))
        lines.append(f"{nm} - {h.get('connection') or 'Heir'} - {_v5_addr(h)}{_match(nm)}")
    return lines


def _v5_note_parts(rec: dict, rk: dict, f: dict, court: dict, title: dict, deed: dict,
                   basis_by_name: dict, loaded_by_n: dict, pr_rel: dict | None, pr_name: str,
                   owner_alive: bool, presumed: bool, cache: dict) -> dict:
    """Everything the note needs from the three sidecars, plus flags / NOT LOADED / MUST VERIFY."""
    out = {"title_line": "", "deed_line": "", "case_line": "", "court_lines": [],
           "signer_prefix": "", "flags": [], "not_loaded": [], "must_verify": []}
    if presumed:
        return out
    co = _v5_co_owners(rec, f, title, court)
    has_case = _v5_has_case(court)
    not_on_title = bool(title.get("owners")) and title.get("owner_of_record_is_decedent") is False
    trust = bool(title.get("trust_vesting"))
    out["title_line"] = _v5_title_line(rec, title, co, has_case)
    out["deed_line"] = _v5_deed_line(rec, deed)
    st_rec = (rec.get("state") or "").upper()
    ns = (court or {}).get("name_search") or {}
    if court and not court.get("case_no"):
        if ns.get("terms") and ns.get("hits"):
            out["case_line"] = (f"CASE: {ns['hits']} Register of Wills estate(s) under {' / '.join(ns['terms'])} "
                                f"in {rec.get('county') or 'the county'} - none matched this decedent "
                                f"({ns.get('basis') or 'review'}); REVIEW before calling it no case")
        elif ns.get("terms"):
            out["case_line"] = (f"CASE: no {rec.get('county') or ''} Register of Wills estate under "
                                f"{' / '.join(ns['terms'])} as of {ns.get('searched_at') or TODAY} - no probate opened "
                                "(trust / TOD / small estate / not yet filed): heirs or trustee route only")
        else:
            out["case_line"] = "CASE: no case on file"
    elif st_rec in ("MD", "VA") and court.get("case_no") and court.get("join_basis") == "name_search":
        # MD: an estate number found by decedent-NAME search (2026-10-02), no party list. An
        # anchors-based estate prints its CASE line from the anchors in _note, not here.
        out["case_line"] = (f"CASE: estate {court['case_no']} - {court.get('court') or (rec.get('county') or '') + ' Register of Wills'}"
                            + (f", filed {court['filing_date']}" if court.get("filing_date") else "")
                            + (f", {court['status']}" if court.get("status") else "")
                            + (f", DOD {court['dod']}" if court.get("dod") else "")
                            + (" (found by decedent-name search - PR not online; RoW notice names the PR)"
                               if court.get("join_basis") == "name_search" else ""))
        if court.get("join_basis") == "name_search":
            out["flags"].append("case found by decedent-NAME search on the Register of Wills - verify it is this decedent before relying on the DOD")
    elif has_case:
        # Printed by _note only when the vendor anchors carry no case of their own.
        out["case_line"] = (f"CASE: {court['case_no']} - DC Superior Court, Probate Division"
                            + (f", filed {court['filing_date']}" if court.get("filing_date") else "")
                            + (f", {court['status']}" if court.get("status") else "")
                            + ("; a WILL is on file (heirs are Legatees)" if court.get("will_on_file") else ""))
    if has_case and court.get("join_basis") == "name_only":
        out["flags"].append("court case matched on the decedent's NAME only - verify it is this property")
    out["court_lines"] = _v5_court_lines(court, rk, basis_by_name, loaded_by_n, pr_rel, cache)
    overruled = _v5_court_overruled(court, rk)
    if overruled:
        out["flags"].append("SmartSkip namesake set-aside OVERRULED by the court record ("
                            + "; ".join(overruled) + ") - re-rank to load their numbers")

    # who must sign
    if has_case and not owner_alive:
        prs = [_natural_name(p.get("name")) for p in court.get("pr") or []] or [x for x in ((rec.get("anchors") or {}).get("pr_full"), pr_name) if x][:1]
        will = court.get("will_on_file")
        out["signer_prefix"] = (f"PR {' / '.join(prs) if prs else '(not yet named)'} signs for the estate"
                                + (" (will on file - the heirs are legatees)" if will else
                                   " (no will indicated - intestate shares)" if will is False else ""))
    def _join(clause: str) -> str:
        return (out["signer_prefix"] + "; " + clause) if out["signer_prefix"] else clause[0].upper() + clause[1:]

    if not_on_title:
        out["signer_prefix"] = _join(
            f"DECEDENT NOT ON TITLE - {' and '.join(co) if co else 'the owners of record'} hold the property"
            + (f" since {title['sale_date']}" if title.get("sale_date") else "")
            + "; the estate may own nothing here - they sign, if anyone")
        out["flags"].append("decedent NOT on title" + (f" - transfer {title['sale_date']}" if title.get("sale_date") else ""))
    elif co:
        who = " and ".join(co)
        if has_case:
            out["signer_prefix"] = _join(f"co-owner {who} on title is NOT a party to the probate - living / deceased / "
                                         "divorced UNKNOWN - decides the deal")
        else:
            out["signer_prefix"] = _join(f"co-owner {who} on title (not the decedent) - living / deceased UNKNOWN; "
                                         "if living with survivorship they own outright and sign alone - decides the deal")
        for c in co:
            out["flags"].append(f"co-owner {c} on title" + (", not in the probate" if has_case else ""))
    if trust and not not_on_title:
        out["signer_prefix"] = _join("title is held in TRUST - the successor trustee signs, not the heirs; trust instrument unread")
        out["flags"].append("title held in trust - successor trustee signs")
    # A transfer-on-death deed recorded by the owner moves the house to its beneficiary at
    # death, outside probate (5 of the 53 DC pilot records, 2026-10-02). The beneficiary
    # signs, not the heirs -- unless a later revocation is on the index.
    conv0 = _v5_last_conveyance(deed) or {}
    dt0 = (conv0.get("doc_type") or "").upper()
    if "TRANSFER ON DEATH" in dt0 and "REVOC" not in dt0 and not not_on_title:
        out["signer_prefix"] = _join(
            f"a TRANSFER ON DEATH deed is on record ({conv0.get('grantor') or '?'} -> {conv0.get('grantee') or '?'}, "
            f"{conv0.get('recorded_date') or '?'}) - the property passes to the TOD beneficiary outside probate; "
            "the beneficiary signs, not the heirs")
        out["flags"].append(f"TOD deed to {conv0.get('grantee') or '?'} - passes outside probate")
    # Foreclosure paper on the parcel in the last two years: time-critical.
    fc = [i for i in (deed or {}).get("instruments") or []
          if i.get("on_parcel") is not False and _iso_date(i.get("recorded_date") or "")[:4] >= "2025"
          and re.search(r"LIS PENDENS|FORECLOS|SUBSTITUTE TRUSTEE|TRUSTEES DEED|NOTICE OF (DEFAULT|SALE)", (i.get("doc_type") or "").upper())]
    if fc:
        fc.sort(key=lambda i: _iso_date(i.get("recorded_date") or ""))
        out["flags"].append(f"FORECLOSURE activity on the parcel - {fc[-1].get('doc_type')} {fc[-1].get('recorded_date')} - time-critical")

    # deed flags: the meaningful ones only, and a conveyance recorded after the DOD.
    for fl in _v5_deed_flags(deed):
        out["flags"].append(f"deed: {fl}")
    conv = _v5_last_conveyance(deed) or {}
    dod = f.get("dod") or court.get("dod") or ""
    try:
        if conv.get("recorded_date") and dod and _iso_date(conv["recorded_date"]) > _iso_date(dod) > "":
            out["flags"].append(f"conveyance recorded {conv['recorded_date']} is AFTER the DOD - possible sale/transfer after death")
    except (TypeError, ValueError):
        pass

    # on no record
    if has_case:
        nobody = [r["name"] for r in rk.get("rels") or [] if r.get("is_dm") and basis_by_name.get(r["name"]) == "label"
                  and (r.get("name") or "").lower() not in {x.lower() for x in (f.get("drop_names") or [])}]
        if nobody:
            out["not_loaded"].append("On NO record (not court, not title, not obituary) - SmartSkip label only: "
                                     + "; ".join(dict.fromkeys(nobody)))
            out["flags"].append("signer(s) on no record")
    # The SmartSkip subject row carries no first name on EVERY record of this cohort, so
    # the line is printed only when it says something: a co-owner it probably is, or an
    # occupant a generation younger than the decedent with lines of their own.
    hs = (title or {}).get("household_subject")
    try:
        hs_age = int(hs.get("age")) if hs and hs.get("age") not in (None, "") else None
    except (TypeError, ValueError):
        hs_age = None
    if hs and hs.get("lines") and not hs.get("deceased_flag") and (co or (hs_age is not None and hs_age <= 65)):
        nums = []
        for ln in hs.get("lines") or []:
            num = ln.get("number") if isinstance(ln, dict) else str(ln)
            t = cache.get(re.sub(r"\D", "", num or ""), {}).get("tier")
            nums.append(f"{num}" + (f" ({t})" if t else ""))
        out["not_loaded"].append(f"Household line at the address (SmartSkip subject with no first name, age {hs.get('age') or '?'}"
                                 + (f", likely {co[0]}" if co else " - candidate co-owner / occupant") + "): "
                                 + (", ".join(nums) if nums else "no number") + " - not loaded")

    # must verify
    mv = []
    if not_on_title:
        mv.append("who holds title now and under what deed (decedent is OFF title)")
    elif title.get("owner_of_record_is_decedent") is True:
        mv.append("no transfer since the tax-roll read")
    elif title.get("owners"):
        mv.append("decedent on title (roll spelling) - read the deed")
    else:
        mv.append("decedent is the owner of record")
    if trust:
        mv.append("successor trustee named in the trust instrument")
    if "TRANSFER ON DEATH" in dt0 and "REVOC" not in dt0 and not not_on_title:
        mv.append(f"TOD deed not revoked; beneficiary {conv0.get('grantee') or '?'} living")
    for c in co:
        mv.append(f"{c} living or deceased")
    if deed and deed.get("instruments"):
        mv.append("deed vesting (MDLANDREC image, free, one scrape on a go)" if deed.get("source") == "mdlandrec"
                  else "deed vesting (Recorder of Deeds image, $4)")
    else:
        mv.append("deed vesting")
    if has_case:
        mv.append("PR letters issued")
    elif (rec.get("state") or "").upper() == "DC" and court:
        mv.append(f"DC probate portal by decedent name (no case on file as of {TODAY})")
    else:
        mv.append("PR appointment (Register of Wills / Circuit Court)")
    out["must_verify"] = mv
    return out


def _load_keys_file(path: str | None) -> set | None:
    if not path:
        return None
    p = Path(path)
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else data.keys())
    rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
    return {r["key"] for r in rows if r.get("key")}


def cmd_v5merge(a) -> int:
    """Merge rerun_v5/{title,court,deed}.json into records.json[key][...] with a backup."""
    run = Path(a.run_dir)
    side = Path(a.sidecar_dir) if a.sidecar_dir else run / "rerun_v5"
    records = jload(run / "records.json", {})
    if not records:
        say("ERROR: records.json missing or empty")
        return 2
    bak = run / f"records_pre_v5merge_{date.today().strftime('%Y%m%d')}.json"
    if not bak.exists():
        jdump(bak, records)
        say(f"backup -> {bak.name}")
    stats = Counter()
    for name in ("title", "court", "deed"):
        data = jload(side / f"{name}.json", None)
        if data is None:
            say(f"  {name}.json: absent, skipped")
            continue
        for key, val in data.items():
            if key.startswith("_") or key not in records:
                stats[f"{name}_unknown_key"] += 0 if key.startswith("_") else 1
                continue
            records[key][name] = val
            stats[name] += 1
    jdump(run / "records.json", records)
    say(f"merged: {dict(stats)}")
    return 0


_MONTH_TAG_RE = re.compile(r"^(0[1-9]|1[0-2])/20\d\d$")


def _note(rec: dict, rk: dict, f: dict, rel_lines: list[str], n_phones: int,
          not_loaded: list[str] | None = None, pr_status: str = "", v5: dict | None = None) -> str:
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
        src_prose = DOD_SRC_PROSE.get(src, src)
        if f.get("obituary_url") and src and src != "obituary":
            src_prose += "; the obituary carries no date"
        out.append(f"DECEDENT: {who} - DOD {f.get('dod') or 'unknown'} ({src_prose})")
        an = rec.get("anchors") or {}
        if an.get("case_number") or an.get("estate_number"):
            out.append(f"CASE: {an.get('case_number') or 'estate ' + an['estate_number']}"
                       f" - {an.get('court') or 'probate court'}"
                       + (f", filed {an['file_date']}" if an.get("file_date") else ""))
    # v5 method (2026-10-01): CASE from the court join when the anchors carry none, then
    # TITLE and DEED -- what the dialer must know before anyone's relationship matters.
    v5 = v5 or {}
    if not presumed:
        an_c = rec.get("anchors") or {}
        if v5.get("case_line") and not (an_c.get("case_number") or an_c.get("estate_number")):
            out.append(v5["case_line"])
        for ln in (v5.get("title_line"), v5.get("deed_line")):
            if ln:
                out.append(ln)
    # Cached pilot findings read "relative (relative) died" -- the label was interpolated
    # into a sentence that already carried it.
    verdict = (f.get("verdict") or "not researched").replace("relative (relative) died",
                                                             "a relative died")
    out.append(f"VERDICT: {verdict}")
    if rk.get("signer_basis"):
        out.append(f"SIGNERS: {rk['signer_basis']}")
    if v5.get("court_lines") and not presumed:
        out += [""] + list(v5["court_lines"])

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
    dec_dropped = list(dict.fromkeys(f.get("decedent_dropped") or []))
    if dec_dropped:
        nl.append("Matches the DECEDENT, not loaded (dead, or an unresolvable namesake): "
                  + "; ".join(dec_dropped))
    nl_start = len(out) + 2
    if nl:
        out += ["", "NOT LOADED"] + nl

    tail = []
    if f.get("executor_named"):
        tail.append(f"EXECUTOR NAMED IN OBITUARY: {f['executor_named']}")
    if rec.get("pr"):
        an = rec.get("anchors") or {}
        pr_txt = an.get("pr_full") if an.get("pr_full") and len(an["pr_full"]) > len(rec["pr"]) else rec["pr"]
        if an.get("pr_mailing"):
            pr_txt += f", {an['pr_mailing']}"
        tail.append(f"PR ON RECORD: {pr_txt}{pr_status}")
        if an.get("attorney"):
            tail.append(f"ATTORNEY ON FILING: {an['attorney']}")
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
    elif v5.get("must_verify"):
        tail.append("MUST VERIFY: " + "; ".join(dict.fromkeys(v5["must_verify"])))
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

    # Month tags. `Deep Prospected` carries the DP month (today unless --month-tag says
    # otherwise); `SS Traced` carries the month the TRACE happened, read off the SmartSkip
    # order id (see _trace_month) -- a rebuild in October of a batch traced in September
    # must not re-date the trace. --trace-month overrides the derived value.
    month = getattr(a, "month_tag", None) or MONTH_TAG
    derived = _trace_month(run)
    trace_month = getattr(a, "trace_month", None) or derived or month
    for lbl, val in (("--month-tag", month), ("--trace-month", trace_month)):
        if not _MONTH_TAG_RE.match(val):
            say(f"ERROR: {lbl} must be MM/YYYY, got {val!r}")
            return 2
    tag_dp, tag_traced = f"Deep Prospected {month}", f"SS Traced {trace_month}"
    tag_no_numbers = f"DP No Numbers {month}"
    say(f"tags: {tag_dp!r} / {tag_traced!r}"
        + (f" (trace month {derived} from the SmartSkip order id)" if derived else
           " (no SmartSkip order id on file; trace month = DP month)"))

    plan: dict[str, dict] = {}
    max_rel = 0
    max_phones = 0
    tag_rows = []
    no_numbers = []
    # --keys-file / --plan-out: a pilot rebuild of a subset that never touches the live
    # push_plan.json (the plan files are the source of truth; a stray rebuild reverted
    # live writes once). Every artifact goes beside the plan file.
    only = _load_keys_file(getattr(a, "keys_file", None))
    plan_path = Path(a.plan_out) if getattr(a, "plan_out", None) else run / "push_plan.json"
    out_dir = plan_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    if only is not None:
        say(f"subset: {len(only)} keys from --keys-file, plan -> {plan_path}")
    basis_by_key: dict[str, dict[str, str]] = {}
    for key, rk in ranked.items():
        if only is not None and key not in only:
            continue
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

        # The DECEDENT is never in drop_names -- that list is the obituary's predeceased --
        # yet SmartSkip returns the subject again under a second identity (type Unknown /
        # Other Relative, age a few years off), and on "spouse died" / "relative died"
        # verdicts the dead person legitimately sits among the relatives. Either way a dead
        # person was landing as a SIGNER with phones: on the 2026-09-11 Aug-27 batch the
        # probe record loaded the husband from the obituary as REL1 with 7 numbers, and 61
        # of 453 records carried a same-name relative within 4 years of the subject's age.
        # Drop any relative whose name matches the decedent unless the age gap to the
        # SmartSkip subject says it is a namesake child or parent (>= 18 years); everything
        # dropped here is printed in the note so the caller can see who was excluded.
        f = dict(f)
        f["decedent_dropped"] = []
        an_b = rec.get("anchors") or {}
        if not f.get("dod") and an_b.get("dod") and not owner_alive:
            f["dod"], f["dod_source"] = an_b["dod"], an_b.get("dod_source") or "court_record"
        # v5: the court party join. A case at this address (or by case number) whose
        # decedent IS the owner proves the death whatever the obituary search found --
        # 17 Sept "died" verdicts had no DOD, and the "death UNVERIFIED" records with a
        # case on file were unverified only because research never read the court.
        court_v5 = rec.get("court") or {}
        if _v5_has_case(court_v5) and not owner_alive:
            cdec = _natural_name(court_v5.get("decedent_name") or "")
            if cdec and any(names_match(cdec, nm) or names_match_loose(cdec, nm)
                            for nm in _v5_decedent_names(rec, f)):
                if not f.get("dod") and court_v5.get("dod"):
                    f["dod"], f["dod_source"] = _iso_date(court_v5["dod"]) or court_v5["dod"], "court_record"
                if not f.get("decedent_name"):
                    f["decedent_name"] = cdec
                if verdict != "owner did die":
                    verdict = "owner did die"
                    f["verdict"] = verdict
                    f["flags"] = list(f.get("flags") or []) + ["death_from_court_record"]
            elif cdec:
                f["flags"] = list(f.get("flags") or []) + [
                    f"court case {court_v5.get('case_no')} at this address names {cdec}, not the owner - verify"]
        dec = f.get("decedent_name") or ""
        if dec:
            subj_age = (rk.get("subject") or {}).get("age")
            # The age gap is measured against the SmartSkip SUBJECT (the owner). That only
            # says anything about a namesake when the decedent IS the owner, or is the
            # owner's spouse (a same-generation gap). On "parent died" / "a relative died"
            # the dead person is a different generation from the subject by definition, so
            # a same-name relative there is the decedent whatever the gap (Imogene Sparks,
            # 89, under a 32-year-old owner; Munzer Murshed, 54, under a 79-year-old).
            gap_is_meaningful = (verdict == "owner did die"
                                 or any(w in verdict for w in ("spouse died", "husband died", "wife died")))
            for rel in rk["rels"]:
                nm = rel.get("name") or ""
                if nm.lower() in drop or not _matches_decedent(dec, nm):
                    continue
                if _v5_on_court_record(nm, court_v5):
                    continue     # the court names this person as PR / heir: alive, never the decedent
                try:
                    gap = abs(int(subj_age) - int(rel.get("age")))
                except (TypeError, ValueError):
                    gap = None
                if gap_is_meaningful and gap is not None and gap >= NAMESAKE_GAP_YEARS:
                    continue     # a namesake child / parent, alive: keep
                drop.add(nm.lower())
                f["decedent_dropped"].append(
                    f"{nm}{', ' + str(rel['age']) if rel.get('age') else ''}")

        # Re-run the obituary <-> SmartSkip cross-check with the loose matcher, so a cached
        # finding stops listing "Judy McAuley" as not in SmartSkip while JUDITH MCAULEY sits
        # in REL2. No research re-run, no spend: the survivors are in the cache.
        cc = _cross_check(f, rk["rels"])
        if f.get("survivors"):
            f = dict(f)
            f["obit_only_names"] = cc["obit_only"]
            f["confirmed_rels"] = cc["confirmed"]
        # The relationship words describe kinship to the DECEDENT. Only when the decedent is
        # the owner do they describe the owner's relatives; on "parent died" / "a relative
        # died" / same-surname verdicts the words belong to someone else and are not printed.
        obit_words = cc["words"] if verdict == "owner did die" else {}
        basis = rk.get("signer_basis") or ""
        dms = [r for r in rk["rels"] if r.get("is_dm")]
        if basis.startswith("no labelled immediate family") and dms and all(r["name"] in obit_words for r in dms):
            rk = dict(rk)
            rk["signer_basis"] = ("no labelled immediate family; every signer below is named in the "
                                  "obituary (relationship shown), VERIFY before mailing")

        cfields, rel_lines, overflow, chan_over = {}, [], [], []
        nonsigners, alive_held, signers_dark_named = [], [], []
        slot = 0
        seen_numbers: set[str] = set()
        # SIGNERS ONLY (Basem, 2026-09-16). rank_record already picked the intestacy
        # signer set; before this the flag was read once, to reword a sentence, and every
        # blood relative was loaded anyway. The guard: if not one signer has a number that
        # survives _keep_number the record would go dark, so for that record the blood
        # tier is allowed back in and the record is tagged so it is visible, never silent.
        # `score` applies the identical rule, which is what makes the numbers exist here.
        # The drop set must be applied HERE too. It is not cosmetic: on 7 of the 453
        # Aug-27 records the only intestacy signer IS the decedent, whom the loop drops by
        # name a few lines down. Computing signers_dark over the undropped list made it
        # read False -- the decedent "had numbers" -- so the real family was then skipped
        # as non-signers and the record shipped with nothing. Caught by the build diff.
        # ── the Personal Representative (Basem, 2026-09-30: PR fields, never a REL slot;
        # a relative who is also the PR is loaded as the PR). Picked BEFORE the signer
        # maths so a reachable PR counts as a reachable signer, and before the drop set is
        # applied so a PR who shares the decedent's name (a Jr./Sr. pair) is never dropped
        # as dead: the filing names a living person.
        pr_name = (rec.get("pr") or "").strip()
        pr_rel = _pick_pr(rk["rels"], pr_name)
        pr_flags: list[str] = []
        if pr_rel is not None and (pr_rel.get("name") or "").lower() in drop:
            drop.discard((pr_rel.get("name") or "").lower())
            f["decedent_dropped"] = [x for x in f["decedent_dropped"]
                                     if not x.lower().startswith((pr_rel.get("name") or "").lower())]
            pr_flags.append("PR shares the decedent's name (Jr./Sr.?) - kept as PR, verify on the call")
        if pr_name and pr_rel is None and any(
                names_match(r.get("name") or "", pr_name) or names_match_loose(r.get("name") or "", pr_name)
                for r in rk["rels"]):
            pr_flags.append("two relatives match the PR's name - neither loaded as PR, both stay REL")
        if pr_flags:
            f["flags"] = list(f.get("flags") or []) + pr_flags
        _sig = [r for r in rk["rels"] if r.get("is_dm") and (r["name"] or "").lower() not in drop]
        if not _sig and not owner_alive:
            # The SIGNERS line is the intestacy TEMPLATE ("spouse and children (all children
            # must sign)"). With nobody identified as a child or spouse it reads as a finding
            # it is not -- 1391 Pennsylvania Ave SE #433 (2026-10-01, Basem): five vendor
            # relatives with no kinship, a namesake SmartSkip tree set aside, and the note
            # still announced "all children must sign" over a dial list holding the PR alone.
            # Say what is true instead: the heirs were NOT identified.
            n_named = sum(1 for r in rk["rels"]
                          if r is not pr_rel and (r["name"] or "").lower() not in drop and r.get("name"))
            rk = dict(rk)
            rk["signer_basis"] = ("heirs NOT identified - no kinship data on the relatives"
                                  + (f" ({n_named} named below, relationship unknown)" if n_named else "")
                                  + ("; the PR is the only confirmed contact" if pr_rel is not None
                                     else "; no PR on record"))
        # ── what the SIGNER lines actually rest on (Basem, 2026-10-01: "how much can the
        # dialer depend on the note?"). Measured on the Sept 646 before this: 1,138 of
        # 1,496 SIGNER lines were a SmartSkip relationship label alone, and 179 UNRESOLVED
        # records still printed "spouse and children (all children must sign)". The
        # template is the intestacy ORDER; it is not evidence that anyone below holds that
        # relationship. Say so, in the SIGNERS line and on each REL line.
        confirmed_set = set(cc["confirmed"]) if verdict == "owner did die" else set()
        label_only = [r for r in _sig if r is not pr_rel and r["name"] not in confirmed_set]
        death_unverified = not owner_alive and verdict != "owner did die"
        if _sig:
            rk = dict(rk)
            old_basis = rk.get("signer_basis") or ""
            if death_unverified:
                rk["signer_basis"] = ("death UNVERIFIED - no obituary or court record found; the relatives "
                                      "below are SmartSkip labels, not confirmed heirs"
                                      + (f" (intestacy order if the owner did die: {old_basis})" if old_basis else ""))
                f["flags"] = list(f.get("flags") or []) + ["label_only_signers"]
            elif label_only and not f.get("survivors"):
                rk["signer_basis"] = (old_basis + "; the obituary/court record names no survivors - the "
                                      "relationships below are SmartSkip labels, unverified")
                f["flags"] = list(f.get("flags") or []) + ["label_only_signers"]
            elif label_only:
                rk["signer_basis"] = (old_basis + f"; {len(label_only)} of {len(_sig)} signers below are "
                                      "label-only (not named in the obituary)")
        if not owner_alive and any((r.get("first") or "").lower() in ("", "unknown") for r in _sig):
            f["flags"] = list(f.get("flags") or []) + ["unknown_name_signer"]
        # v5: what each relative's relationship rests on -- court record, title, obituary,
        # or a SmartSkip label alone. Printed on the REL line and in dial_sheet.csv.
        title_v5, deed_v5 = rec.get("title") or {}, rec.get("deed") or {}
        basis_by_name = {r["name"]: _v5_rel_basis(r, court_v5, title_v5, confirmed_set, dec)
                         for r in rk["rels"] if r.get("name")}
        basis_by_key[key] = basis_by_name
        if _v5_has_case(court_v5) and _sig and all(basis_by_name.get(r["name"]) in ("court", "title", "obituary") for r in _sig):
            f["flags"] = [x for x in (f.get("flags") or []) if x != "label_only_signers"]
        pr_reachable = pr_rel is not None and any(_keep_number(cache.get(p["number"], {}))[0]
                                                  for p in pr_rel["phones"])
        signers_dark = not pr_reachable and not any(_keep_number(cache.get(p["number"], {}))[0]
                                                    for r in _sig for p in r["phones"])
        # The same guard on the owner-alive branch, and it is not hypothetical: on the
        # 453-record Aug-27 cohort 11 records are "spouse died; owner alive" where the
        # OWNER has no callable number of their own. Stripping relatives there leaves a
        # record with nobody to call at all -- one of them had 90 numbers on file. So when
        # the living owner is unreachable the family goes back on as a REACH PATH, tagged
        # and labelled as such in the note: a route to the owner, never a signer.
        owner_reachable = any(_keep_number(cache.get(p["number"], {}))[0]
                              for p in rk["subject"]["phones"])
        reach_only = owner_alive and not owner_reachable
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

        # PR fields on every record that names one -- the name alone when no number
        # exists, because the dialers read PR First/Last whether or not there is a
        # number. The PR's numbers are claimed before any relative's, so a line the PR
        # shares with a sibling is the PR's. One number goes in `Personal Representative
        # Phone`; every kept number rides the owner's list as Pr.1, Pr.2, ...
        pr_kept: list = []
        if pr_name:
            pf, pl = _split_pr_name(pr_name)
            cfields["PR First Name"], cfields["PR Last Name"] = pf, pl
            cfields["Personal Representative"] = f"{pf} {pl}".strip()
        if pr_rel is not None:
            for p in pr_rel["phones"]:
                t = cache.get(p["number"], {})
                if _keep_number(t)[0] and p["number"] not in seen_numbers:
                    seen_numbers.add(p["number"])
                    pr_kept.append((p, t))
            pr_kept.sort(key=_tier_sort_key)
            if pr_kept:
                cfields[PR_PHONE_FIELD] = pr_kept[0][0]["number"]
                contacts.append({"kind": "pr", "n": -1, "name": pr_rel["name"], "kept": pr_kept,
                                 "is_dm": True, "rel": pr_rel})

        # rk["rels"] already arrives signers -> blood -> in-law channels, so walking it in
        # order fills the REL fields signers-first.
        for rel in rk["rels"]:
            if rel is pr_rel:
                continue      # loaded as the PR above, never as a REL
            if (rel["name"] or "").lower() in drop:
                continue
            # The owner is ALIVE and signs alone. A relative on such a record holds no
            # authority to sell, and a dialer working one burns the dial. Named, not loaded.
            if owner_alive and not reach_only:
                alive_held.append(f"{rel['name']} ({_rel_label(rel, obit_words)})")
                continue
            # NEVER an in-law, on any path. The two fallbacks below reopen the blood
            # tier, and without this guard they reopened the CHANNEL tier with it --
            # on an Aug-27 reach-path record that put two In-Laws into REL6/REL7,
            # against the standing rule that an in-law is named in the note and
            # nowhere else. (Only bites on run dirs ranked before --channel-cap 0.)
            if rel.get("is_channel") and not rel.get("is_dm"):
                chan_over.append(f"{rel['name']} ({_rel_label(rel, obit_words)})")
                continue
            if not rel.get("is_dm") and not (signers_dark or reach_only):
                nonsigners.append(f"{rel['name']} ({_rel_label(rel, obit_words)})")
                continue
            if slot >= a.max_rel_slots:
                # Past the cap: named in the note, not loaded as a field or a phone.
                label = f"{rel['name']} ({_rel_label(rel, obit_words)}{', ' + str(rel['age']) if rel.get('age') else ''})"
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
                # A SIGNER with no callable number used to vanish from the note (2026-10-01:
                # 10106 Cedar Ln's son David Snyder, 2626 Gilbert Rd's sister Joan Maybury,
                # 5418 Ridge Rd's two children on Drop-tier lines). The caller must know a
                # signer exists even when there is nobody to dial yet.
                if rel.get("is_dm"):
                    signers_dark_named.append(
                        f"{rel['name']} ({_rel_label(rel, obit_words)}"
                        f"{', ' + str(rel['age']) if rel.get('age') else ''})")
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
            tag = (f"Owner.{m}" if c["kind"] == "owner" else
                   f"Pr.{m}" if c["kind"] == "pr" else f"Rel{c['n']}.{m}")
            # MERGE the relationship word onto the number (Ty's tag) beside our positional
            # one (Basem, 2026-09-16). The account has NO REL{n}: Relationship field, so a
            # dialer used to see "Rel3.2" and had to decode it against REL3 -- which is the
            # "relative fields missing" complaint exactly (~500 records carry Rel-tagged
            # phones over blank REL names). The word travels ON the number and cannot go
            # stale; Rel{n}.{m} stays the pointer into the custom fields.
            #
            # THE OWNER RULE holds for free: the owner's numbers are collected first and
            # seen_numbers stops a shared household line being claimed twice, so a number
            # the owner also holds is tagged Owner.{m} and never wears a kinship word.
            word = REL_TAG_WORD.get((c.get("rel") or {}).get("canon_rel") or "")                 if c["kind"] == "rel" else ""
            tags = [tag] + ([word] if word else [])                 + ([t["tier"]] if t.get("tier") and t["tier"] != "Unknown" else [])
            return {"number": p["number"], "type": _api_phone_type(p.get("type"), t.get("line_type")),
                    "tags": tags, "status": "UNKNOWN", "is_connected": True,
                    "rel_n": c["n"], "rel_name": c["name"], "tier": t.get("tier")}

        dial_cap = min(a.dial_cap or DIAL_LIST_CAP, OWNER_PHONE_CAP)
        rest = []
        for c in contacts:
            for m, (p, t) in enumerate(c["kept"], 1):
                (phones if m == 1 and len(phones) < dial_cap else rest).append(_mk(c, p, t, m))
        rest.sort(key=lambda ph: TIER_RANK.get(ph.get("tier"), 8))
        for ph in rest:
            (phones if len(phones) < dial_cap else phones_capped).append(ph)

        # The note lists each contact with the tiers that actually reached the dial list.
        loaded_by_n: dict[int, list[str]] = {}
        for ph in phones:
            loaded_by_n.setdefault(ph["rel_n"], []).append(ph.get("tier") or "?")
        for c in contacts:
            if c["kind"] != "rel":
                continue
            rel, n = c["rel"], c["n"]
            tiers = loaded_by_n.get(n, [])
            # On an owner-alive / presumed-alive record NOBODY on this list can sign --
            # the LIVING OWNER does. is_dm is the intestacy rank computed as if the owner
            # had died, so printing "SIGNER" next to it tells a dialer the exact opposite
            # of the truth, and on a reach-path record it contradicts the note's own
            # header two lines above. Caught on 1608 28th St SE, where six relatives of a
            # living owner were each labelled SIGNER.
            role = (" - REACH ONLY (cannot sign; owner is alive)" if owner_alive
                    else _V5_SIGNER_LABEL[basis_by_name.get(rel["name"], "label")] if rel.get("is_dm")
                    else " - CHANNEL" if rel.get("is_channel") else "")
            rel_lines.append(f"REL{n} - {rel['name']} - {_rel_label(rel, obit_words)}"
                             f"{', ' + str(rel['age']) if rel.get('age') else ''}"
                             f"{role}"
                             f"{' - ' + ' / '.join(tiers) if tiers else ' - no number'}")
        loaded_spouses = [c for c in contacts if c["kind"] == "rel"
                          and c["rel"].get("canon_rel") in SPOUSE_RELS]
        if len(loaded_spouses) >= 2 and not owner_alive:
            f["flags"] = list(f.get("flags") or []) + ["two_spouses"]
        if pr_rel is not None:
            tiers = loaded_by_n.get(-1, [])
            kin = pr_rel.get("canon_rel") or "Relative"
            kin_txt = f" ({_rel_label(pr_rel, obit_words)})" if kin not in ("Relative", "Personal Representative") else ""
            rel_lines.insert(0, f"PR - {pr_rel['name']} - Personal Representative{kin_txt}"
                             f"{', ' + str(pr_rel['age']) if pr_rel.get('age') else ''} - SIGNER - "
                             + (" / ".join(tiers) if tiers else "no number"))
        if owner_alive and loaded_by_n.get(0):
            rel_lines.insert(0, ("OWNER (presumed alive) - " if presumed else "OWNER (alive) - ")
                             + " / ".join(loaded_by_n[0]))
        pr_status = ("" if not pr_name else
                     f" - in the PR fields, {len(pr_kept)} numbers tagged Pr.N" if pr_kept else
                     " - in the PR fields; found in SmartSkip/IDI with no callable number" if pr_rel is not None else
                     " - name in the PR fields; no number found (not in SmartSkip/IDI)")

        tags = [tag_dp, tag_traced]
        if verdict == "owner did die":
            tags.append(TAG_DECEASED)
        elif f.get("decedent_from_crm"):
            # Someone else died and the owner is the representative. Must be tested BEFORE
            # owner_alive, which would otherwise claim a spouse death that never happened.
            tags.append(TAG_OWNER_IS_PR)
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
        if signers_dark and not owner_alive and any(r.get("phones") for r in rk["rels"]):
            tags.append(TAG_SIGNERS_DARK)
        if reach_only and phones:
            tags.append(TAG_REACH_ONLY)
        # Cohort-wide tags the caller asked for (e.g. `Priority 1`). Additive only --
        # the property PATCH merges, so an extra tag never displaces an existing one.
        for extra in getattr(a, "extra_tag", None) or []:
            if extra not in tags:
                tags.append(extra)
        # Basem 2026-10-01: an obituary record gets `Obituaries` and never `Priority 1`
        # (FTM only when it is also a probate case) -- whatever --extra-tag said. The rule
        # lives in dpd.lane_tags so every pusher refuses the same way.
        # The rule must see the tags the record ALREADY carries (prep keeps the CRM's own
        # Tags column): judged on the plan's additions alone, a `Priority 1` SiftMap record
        # reads as "no entry tag at all" and is handed `Priority 2` (smartskip_1005,
        # 2026-10-05: 9 of 11 P1 rung records). Existing tags are the CRM's, not ours to
        # re-send -- only what the rule adds or keeps of OUR additions goes in the plan.
        from dpd.lane_tags import apply_lane_rule, split_lists
        existing = split_lists(rec.get("tags"))
        ruled_all = apply_lane_rule(split_lists(rec.get("lists")), existing + tags)
        ruled = [t for t in ruled_all if t in tags or t not in existing]
        if ruled != tags:
            say(f"  {key}: lane rule {sorted(set(tags) - set(ruled))} -> {sorted(set(ruled) - set(tags))}")
        tags = ruled
        if not phones:
            tags.append(tag_no_numbers)
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
                f"dial list (the dial list is capped at {dial_cap}): "
                + ", ".join("PR" if x == -1 else "OWNER" if x == 0 else f"REL{x}" for x in rels_cut))
        if overflow:
            not_loaded.append("Over the REL cap: " + "; ".join(overflow))
        inlaws_out = list(dict.fromkeys(chan_over + [
            f"{c['name']} ({c.get('age') or '?'})" for c in (rk.get("channels_dropped") or [])]))
        if inlaws_out:
            not_loaded.append("In-laws found, not loaded: " + "; ".join(inlaws_out))
        if alive_held:
            not_loaded.append("Owner is alive and signs alone; family found but NOT loaded "
                              "(reach path only, they cannot sell): " + "; ".join(alive_held))
        if reach_only and phones:
            not_loaded.insert(0, "OWNER IS ALIVE and is the only person who can sell, but has "
                                 "no number on file. The family below is a REACH PATH to the "
                                 "owner -- ask for the owner by name. None of them can sign.")
        if signers_dark_named:
            not_loaded.insert(0, "SIGNERS with NO callable number (must still sign; find a number): "
                              + "; ".join(signers_dark_named))
        if nonsigners:
            not_loaded.append("Not signers under intestacy, named only: " + "; ".join(nonsigners))
        held = [f"{c['name']} ({c.get('canon_rel') or '?'})" for c in (rk.get("blood_held") or [])]
        if held:
            not_loaded.append("Further relatives on file, not loaded: " + "; ".join(held))
        # v5: TITLE / DEED / COURT PARTIES lines, the who-must-sign prefix, flags.
        v5 = _v5_note_parts(rec, rk, f, court_v5, title_v5, deed_v5, basis_by_name, loaded_by_n,
                            pr_rel, pr_name, owner_alive, presumed, cache)
        not_loaded += v5["not_loaded"]
        if v5["signer_prefix"] and not owner_alive:
            rk = dict(rk)
            old = rk.get("signer_basis") or ""
            # The court / title facts lead; the intestacy template (or "death UNVERIFIED",
            # now contradicted by a case on file) follows as the fallback order.
            if _v5_has_case(court_v5) and old.startswith("death UNVERIFIED"):
                old = old.split("(intestacy order if the owner did die: ", 1)[-1].rstrip(")") if "(intestacy order" in old else ""
            rk["signer_basis"] = v5["signer_prefix"] + (f"; then {old}" if old else "")
        if v5["flags"]:
            f["flags"] = list(f.get("flags") or []) + v5["flags"]
        plan[key] = {
            "key": key, "street": rec["street"], "city": rec["city"], "state": rec["state"], "zip": rec["zip"],
            "owner_first": rec["owner_first"], "owner_last": rec["owner_last"],
            "business_name": rec["business_name"],
            "mail_street": rec["mail_street"], "mail_city": rec["mail_city"],
            "mail_state": rec["mail_state"], "mail_zip": rec["mail_zip"],
            "verdict": verdict, "group": group, "tags": tags,
            "decedent_dropped": f.get("decedent_dropped") or [],
            # The PR as loaded: name on the filing, the SmartSkip/IDI contact it matched
            # (empty = name-only fields), and the numbers that ride Pr.1..N.
            "pr": {"name": pr_name, "contact": (pr_rel or {}).get("name") or "",
                   "phones": [p["number"] for p, _t in pr_kept]} if pr_name else None,
            "custom_fields": cfields, "phones": phones,
            "note": _note(rec, rk, f, rel_lines, len(phones), not_loaded, pr_status=pr_status, v5=v5),
        }
        max_rel = max(max_rel, slot)
        max_phones = max(max_phones, len(phones))
        for p in phones:
            for t in p["tags"]:
                tag_rows.append({"Phone Number": p["number"], "Phone Tag": t})

    jdump(plan_path, {"generated_at": datetime.now().isoformat(timespec="seconds"),
                      "max_rel_slots": max_rel, "max_phones": max_phones, "records": plan})

    # dial_sheet.csv: kinship_source now says court / title / obituary / smartskip label
    # (score wrote obituary / filing (PR) / smartskip label). Rows of other keys are kept.
    ds_in = run / "dial_sheet.csv"
    if ds_in.exists():
        dial_rows = list(csv.DictReader(open(ds_in, encoding="utf-8-sig")))
        changed = 0
        for r in dial_rows:
            b = basis_by_key.get(r.get("key") or "", {}).get(r.get("name") or "")
            if b and r.get("kinship_source") != "filing (PR)" and r.get("kinship_source") != _V5_KINSHIP[b]:
                r["kinship_source"] = _V5_KINSHIP[b]
                changed += 1
        if only is not None:
            dial_rows = [r for r in dial_rows if r.get("key") in only]
        if dial_rows:
            write_csv(out_dir / "dial_sheet.csv", dial_rows, list(dial_rows[0].keys()))
            say(f"dial_sheet.csv: {len(dial_rows)} rows, kinship_source changed on {changed}")

    # Reload CSVs, one per uniform tag group (the wizard applies one tag set per file).
    phone_cols = [f"Phone {i}" for i in range(1, max_phones + 1)]
    pr_cols = [c for c in PR_FIELDS if any(c in p["custom_fields"] for p in plan.values())]
    rel_cols = [f"REL{i}: Full Name" for i in range(1, max_rel + 1)]
    header = RELOAD_BASE + phone_cols + pr_cols + rel_cols + ["Notes"]
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
        write_csv(out_dir / f"reload_{group}.csv", rows, header)
        say(f"reload_{group}.csv: {len(rows)} rows")

    write_csv(out_dir / "phone_tags.csv", tag_rows, ["Phone Number", "Phone Tag"])
    write_csv(out_dir / "no_numbers.csv", no_numbers, list(no_numbers[0].keys()) if no_numbers else ["key"])

    # Review workbook.
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Records"
        ws.append(["Address", "City", "County", "Owner", "Business", "Verdict", "DOD", "DOD source",
                   "Decedent (obit)", "PR (filing)", "PR contact", "PR phones", "Relatives", "Phones",
                   "Rel slots", "Tags", "Obituary", "Flags", "Note"])
        for key, p in plan.items():
            f = research.get(key, {})
            pr = p.get("pr") or {}
            ws.append([p["street"], p["city"], records[key]["county"],
                       f"{p['owner_first']} {p['owner_last']}".strip(), p["business_name"], p["verdict"],
                       f.get("dod", ""), f.get("dod_source", ""), f.get("decedent_name", ""),
                       pr.get("name", ""), pr.get("contact", ""), len(pr.get("phones") or []),
                       len(ranked[key]["rels"]), len(p["phones"]),
                       sum(1 for k in p["custom_fields"] if _REL_NAME_RE.match(k)),
                       ", ".join(p["tags"]), f.get("obituary_url", ""), ", ".join(f.get("flags") or []),
                       p["note"]])
        ws2 = wb.create_sheet("Dial Sheet")
        dial = list(csv.DictReader(open(out_dir / "dial_sheet.csv", encoding="utf-8-sig"))) \
            if (out_dir / "dial_sheet.csv").exists() else []
        if dial:
            ws2.append(list(dial[0].keys()))
            for r in dial:
                ws2.append(list(r.values()))
        for title, path in (("No Numbers", out_dir / "no_numbers.csv"), ("Unusable", run / "unusable.csv")):
            w = wb.create_sheet(title)
            rows = list(csv.DictReader(open(path, encoding="utf-8-sig"))) if path.exists() else []
            if rows:
                w.append(list(rows[0].keys()))
                for r in rows:
                    w.append(list(r.values()))
        wb.save(out_dir / "review.xlsx")
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
    """(uuid, record, why) -- exact street+zip5 match among search hits, else ''.

    A plan record may carry an explicit `uuid` (set by hand for a record the address search
    cannot see: a blank street, or a trust title against two duplicate rows). It is fetched
    directly and still owner-checked, so a stale uuid cannot land on a stranger's record.
    Added 2026-09-23 for the street-less McLean 22102 record (owner Young Kim).
    """
    pinned = (p.get("uuid") or "").strip()
    if pinned:
        status, rec = api.get(f"/api/internal/property/{pinned}/")
        if status != 200:
            return "", {}, f"pinned uuid {pinned} -> {status}"
        o = rec.get("owner") or {}
        want = ((p.get("owner_first") or "").strip().lower(), (p.get("owner_last") or "").strip().lower())
        got = ((o.get("first_name") or "").strip().lower(), (o.get("last_name") or "").strip().lower())
        if want != got and any(want):
            return "", {}, f"pinned uuid {pinned} owner {got} != plan owner {want}"
        return pinned, rec, ""
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

CREATE_PATH = "/api/internal/property/"
LIST_ROW = "Register of Wills Probates"


def _create_body(p: dict) -> dict | None:
    """The create body for a plan record: ADDRESS + OWNER only, nothing else.

    Mirrors probe_property_create.owner_for_create / datasift_api_upload.build_property
    (proven live 2026-09-23): a person carries first_name (+ last_name when present), an
    entity carries `company` with the person keys OMITTED -- the API rejects a blank
    first_name and omitting a key is not sending "". Periods are stripped from the person
    keys (`Tracey M.` flips the record to Incomplete; `Tracey M` is clean, measured). No
    mailing on file (the DP input traces the decedent at the property) -> the property
    address is the mailing, as the probe did. Tags / lists / fields / note are attached
    afterwards by the per-record routes: the create body's `lists` shape is disputed
    upstream (an array "returns 201 and changes nothing"). None = no usable owner.
    """
    addr = {"street": (p.get("street") or "").strip(), "city": (p.get("city") or "").strip(),
            "state": (p.get("state") or "").strip(), "postal_code": (p.get("zip") or "").strip()}
    if not addr["street"] or not addr["postal_code"]:
        return None
    mail = {"street": (p.get("mail_street") or "").strip(), "city": (p.get("mail_city") or "").strip(),
            "state": (p.get("mail_state") or "").strip(), "postal_code": (p.get("mail_zip") or "").strip()}
    if not mail["street"]:
        mail = dict(addr)
    owner: dict = {"address": mail}
    first = " ".join((p.get("owner_first") or "").replace(".", " ").split())
    last = " ".join((p.get("owner_last") or "").replace(".", " ").split())
    company = (p.get("business_name") or "").strip()
    if first:
        owner["first_name"] = first
        if last:
            owner["last_name"] = last
    elif company:
        owner["company"] = company
    else:
        return None
    return {"address": addr, "owner": owner}


def _lists_for_create(rec: dict | None) -> list[str]:
    """The lists a CREATED record gets: the source row's own lists, plus the standing rule
    (Basem 2026-09-07) that `Probate` always pairs with `Register of Wills Probates` --
    except Virginia (2026-09-10: VA probate runs through the Circuit Court, no RoW).
    Existing list names only; this pipeline creates no lists."""
    lists = [x.strip() for x in ((rec or {}).get("lists") or "").split(",") if x.strip()]
    # `probate_only` (Basem 2026-10-14, Martin's weekly list): a row NOT on the Probate
    # Extraction 2 Register of Wills tab stays on Probate alone.
    if (rec or {}).get("probate_only"):
        return lists
    if "Probate" in lists and LIST_ROW not in lists and ((rec or {}).get("state") or "").upper() != "VA":
        lists.append(LIST_ROW)
    return lists


def _created_uuid(status, body) -> tuple[str, str]:
    """(uuid, how) from a create response. 201 -> the new uuid, 'created'. 400 'already
    exists' -> the EXISTING uuid DataSift names, 'exists': the route normalises the address
    (`Drive` == `Dr`, ZIP+4 == ZIP5) where `search` matches the stored string exactly, so
    this 400 is the only reliable existence test and it writes nothing. Anything else ->
    ('', reason)."""
    # WriteApi.write hands a non-2xx body back as a STRING (measured on the live 400,
    # 2026-10-01: '{"non_field_errors": [...], "property": ["74742f8e-..."]}' arrived as
    # str and the uuid went unread). Parse it before looking for the uuid.
    obj = body if isinstance(body, dict) else {}
    if isinstance(body, str) and body.strip().startswith("{"):
        try:
            obj = json.loads(body)
        except json.JSONDecodeError:
            obj = {}
    if status in (200, 201) and (obj.get("uuid") or "").strip():
        return str(obj["uuid"]).strip(), "created"
    txt = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
    if status == 400 and "already exist" in txt.lower():
        prop = obj.get("property")
        got = (prop[0] if isinstance(prop, list) and prop else (prop or "")) or ""
        if got:
            return str(got), "exists"
        return "", "duplicate 400 without a uuid"
    return "", f"create {status}: {txt[:200]}"


def _push_one_api(api: WriteApi, p: dict, fields: dict, commit: bool, prior: "set | None" = None,
                  create: bool = False, lists: "list[str] | None" = None, trace_stamp: bool = True) -> dict:
    log = {"key": p["key"], "street": p["street"], "route": "api", "at": datetime.now().isoformat(timespec="seconds"),
           "commit": commit, "steps": {}}
    uuid, rec, why = _resolve_uuid(api, p)
    if not uuid and create and why.startswith("0 exact hits"):
        # Not on the account by exact-street search. With --create the record is made
        # (address + owner only, per-record routes for the rest). A 400 "already exists"
        # means the search missed a spelling variant: attach to the uuid it names instead.
        body = _create_body(p)
        if not body:
            log["error"] = "create: no usable owner/address in the plan"
            return log
        log["steps"]["create"] = {"body": body, "lists": list(lists or [])}
        if not commit:
            log["would_create"] = True
            log["would_write"] = {"phones": len(p["phones"]), "custom_fields": p["custom_fields"],
                                  "tags": p["tags"], "note_chars": len(p["note"]), "lists": list(lists or [])}
            return log
        st_c, resp_c = api.write("POST", CREATE_PATH, body)
        uuid, how = _created_uuid(st_c, resp_c)
        log["steps"]["create"].update({"status": st_c, "how": how, "uuid": uuid})
        if not uuid:
            log["error"] = how
            log["http_status"] = st_c
            return log
        log["created"] = how == "created"
        if lists:
            # Merge-only PATCH, proven live on the 8,070-record Probate restore (2026-09-16).
            st_l, resp_l = api.write("PATCH", f"/api/internal/property/{uuid}/", {"lists": list(lists)})
            log["steps"]["lists"] = {"status": st_l, "sent": list(lists), "resp": str(resp_l)[:160]}
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
                              "tags": p["tags"], "note_chars": len(p["note"]),
                              # so a dry run shows BOTH halves of the skip-trace rule, not
                              # just the date tag riding along in `tags`
                              "skiptraced_flag": (not owner.get("skiptraced")) and bool(owner_uuid)}
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
        # `prior` (numbers OUR earlier plan wrote for this record) is the provenance that the
        # tag alone cannot give -- see _stale_numbers. stale_restore puts the numbers back if
        # the upsert below then fails.
        stale, stale_restore, foreign_kept = _stale_numbers(owner.get("phones") or [], want, prior)
        if foreign_kept:
            log["steps"]["phones_foreign_kept"] = list(foreign_kept)
        if stale:
            st, resp = api.write("POST", f"/api/internal/owner/{owner_uuid}/remove-phones/",
                                 {"phones": stale})
            log["steps"]["phones_removed"] = {"status": st, "sent": len(stale),
                                              "removed": len((resp or {}).get("removed") or [])
                                              if isinstance(resp, dict) else None}
            log["steps"]["phones_removed_numbers"] = list(stale)
        # Send the FULL planned list, not just the numbers that are new. The old code skipped
        # anything already on the owner, so a re-push could never CORRECT a tag -- and the
        # tier ordering reshuffles almost every Rel{N}.{M} assignment.
        # A number already on the owner keeps the DIALER'S disposition. The plan carries
        # "UNKNOWN" for every number it did not itself observe, and the upsert overwrites
        # status -- 2026-09-15, 7639 Paradise Beach Rd: the house line Bagoury had just
        # marked DEAD came back as "not dialed yet" and led the dial sheet. A plan status
        # only wins when it is a real observation (anything other than UNKNOWN/blank).
        live_status = {_d10(ph.get("number") or ""): (ph.get("status") or "UNKNOWN")
                       for ph in (owner.get("phones") or []) if ph.get("number")}
        payload = [{"number": ph["number"], "type": ph["type"], "tags": ph["tags"],
                    "status": (ph["status"] if (ph.get("status") or "UNKNOWN") != "UNKNOWN"
                               else live_status.get(_d10(ph["number"]), "UNKNOWN")),
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
    # 1b. the OTHER half of the skip-trace rule: the CRM's own structured flag.
    # The tag records WHEN; `owner.skiptraced` is what the CRM and the Needs-Skipped presets
    # actually read, and upsert-phones does NOT set it -- 7854 Linden Leaf Rd sat at
    # skiptraced:false / attempts:0 while holding a loaded Dial First number (2026-09-23).
    # A record that reads "never been skipped" with numbers on it is the precise incoherence
    # the rule exists to prevent, so this runs whether or not the trace returned phones.
    # attempts is max(existing, 1), never existing+1: a re-push must not inflate the count.
    # `trace_stamp=False` (--no-trace-stamp, 2026-10-22): a create-only upload where NO trace ran
    # (Che hand-off step 3) must not mark 400 never-traced owners as skipped.
    if owner_uuid and trace_stamp and not owner.get("skiptraced"):
        st_sk, _ = api.write("PATCH", f"/api/internal/owner/{owner_uuid}/",
                             {"skiptraced": True,
                              "skiptrace_attempts": max(int(owner.get("skiptrace_attempts") or 0), 1)})
        log["steps"]["skiptraced_flag"] = {"status": st_sk}
        if st_sk not in (200, 201, 204):
            # Not fatal -- the numbers and the date tag still land -- but it must be visible
            # in the log rather than leaving a silently half-stamped record.
            log.setdefault("warnings", []).append(f"skiptraced flag PATCH returned {st_sk}")

    # 2. custom fields
    # Run this step whenever there is something to WRITE or something to BLANK. Gated on
    # the plan's own fields alone, a plan with NO REL fields (an owner-alive re-push that
    # dropped every relative) skipped the ghost check entirely and left the dropped
    # relative's name + numbers in REL1 -- 5908 Veranda Dr, 2026-09-18 namesake repair.
    if p["custom_fields"] or prior or log["steps"].get("phones_removed_numbers"):
        body = []
        for lbl, val in p["custom_fields"].items():
            f = fields.get(lbl)
            if not f:
                log.setdefault("missing_fields", []).append(lbl)
                continue
            body.append({"field_uuid": f["uuid"], "value": val})
        # A re-push only ever WROTE fields; it never blanked one. When the new plan has a
        # relative in fewer slots than the old one (2026-09-11 probe: the dead husband
        # dropped from REL1, every slot shifted up, and REL1: Phone 2/3 kept HIS numbers), a
        # ghost value survives under a renamed slot. Blank a live REL field that is not in
        # the plan ONLY when its digits are a number this push just removed from the owner
        # under our own Rel tag -- that is the provenance rule from the 2026-09-10 repair:
        # another plan's REL values are never touched.
        # 2026-09-17: the slot's Full Name goes too once every phone field of the slot was
        # ours (see _ghost_blanks) -- 200 name ghosts survived the phone-only rule on 09-15.
        # Provenance = numbers this push removed from the owner PLUS numbers our prior plan
        # wrote that the new plan dropped: a REL field can hold a number that was never on
        # the owner (over the 30-cap, or already gone), which `phones_removed_numbers` never
        # names -- the 2026-09-17 QA backstop blanked 40+ such ghosts the push had left.
        removed_digits = {_d10(n) for n in (log["steps"].get("phones_removed_numbers") or [])}
        if prior:
            removed_digits |= prior - {_d10(ph["number"]) for ph in p["phones"]}
        if removed_digits:
            _rows, live_cf = custom_field_rows(api, uuid)
            for lbl in _ghost_blanks(live_cf, p["custom_fields"], removed_digits, fields):
                body.append({"field_uuid": fields[lbl]["uuid"], "value": ""})
                log["steps"].setdefault("custom_fields_blanked", []).append(lbl)
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
    # 4. note -> the record's message board, PINNED: edit the existing same-family note in
    #    place, else post + replace (see _place_note for the traps behind each route).
    _place_note(api, uuid, p["note"], log)

    # read back
    time.sleep(1.0)
    st, after = api.get(f"/api/internal/property/{uuid}/")
    _cf_rows, got_cf = custom_field_rows(api, uuid)
    got_phones = {(ph.get("number") or ""): ph for ph in ((after.get("owner") or {}).get("phones") or [])}
    log["readback"] = {
        "owner_after": f"{(after.get('owner') or {}).get('first_name')} {(after.get('owner') or {}).get('last_name')}".strip(),
        "phones_after": len(got_phones),
        "phones_landed": [n for n in (ph["number"] for ph in p["phones"]) if n in got_phones],
        "phones_missing": [n for n in (ph["number"] for ph in p["phones"]) if n not in got_phones],
        "phone_tags_sample": {n: got_phones[n].get("tags") for n in list(got_phones)[:3]},
        "custom_fields_landed": {k: v for k, v in got_cf.items() if k in p["custom_fields"] and v},
        # A field the plan sends as "" (a blanked REL phone slot) reads back blank by design;
        # only a NON-blank plan value that did not land is missing (2026-10-12: the tier-filtered
        # Hottest plan logged every blanked slot as missing, ok=False, and the done set lost it).
        "custom_fields_missing": [k for k, v in p["custom_fields"].items() if v and not got_cf.get(k)],
        "tags_missing": [t for t in p["tags"] if t not in
                         [x.get("name") if isinstance(x, dict) else str(x) for x in (after.get("tags") or [])]],
        # Only populated on a --create push: the lists attached to a record this push made.
        "lists_missing": ([l for l in (lists or []) if l not in
                           [x.get("title") or x.get("name") if isinstance(x, dict) else str(x)
                            for x in (after.get("lists") or [])]]
                          if log["steps"].get("create") else []),
        "notes_present": bool(after.get("notes")),
        "message_pinned": None,   # filled below from the message board
        # Whether the field preserves newlines is the thing the probe exists to answer,
        # so keep the stored text verbatim rather than a boolean.
        # The `notes` FIELD is deliberately no longer written -- reading it here reported
        # "no newline survived" on a record whose pinned board message was perfect.
        # note_stored is filled from the message board below.
        "note_stored": "",
    }
    msgs = board_messages(api, uuid)
    log["readback"]["message_pinned"] = any(x.get("pinned") for x in msgs)
    log["readback"]["messages_on_board"] = len(msgs)
    log["readback"]["note_stored"] = next(
        (x.get("message") or "" for x in msgs if x.get("pinned")), "")[:2500]
    rb = log["readback"]
    log["ok"] = (not rb["phones_missing"] and not rb["custom_fields_missing"] and not rb["tags_missing"]
                 and not rb["lists_missing"]
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
    if getattr(a, "keys_file", None):
        # An ORDERED subset (2026-09-17, Basem: records nobody has dialed yet go first).
        # The file is a JSON list of plan keys; the push walks it in that order and
        # touches nothing else. Keys the plan does not know are reported, not guessed.
        wanted = jload(Path(a.keys_file), [])
        if not isinstance(wanted, list):
            say(f"STOP: {a.keys_file} is not a JSON list of keys")
            return 2
        unknown = [k for k in wanted if k not in plan]
        keys, keys_set = _ordered_keys(keys, wanted), set(keys)
        phoneless = [k for k in wanted if k in plan and k not in keys_set]
        say(f"keys-file {a.keys_file}: {len(wanted)} keys -> {len(keys)} pushable"
            + (f", {len(unknown)} not in plan (skipped: {unknown[:3]}...)" if unknown else "")
            + (f", {len(phoneless)} without phones (skipped; --include-no-numbers to tag them)"
               if phoneless else ""))
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
        # Provenance for phone removal: the plan this run dir pushed BEFORE (backed up as
        # push_plan_prestrip_*.json by the 2026-09-17 strip). Without it the removal guard
        # falls back to the tag alone, which other pushers share -- so say so loudly.
        prior_path = a.prior_plan
        if not prior_path:
            cands = sorted(run.glob("push_plan_prestrip_*.json"))
            prior_path = str(cands[-1]) if cands else None
        prior_plan = None
        if prior_path:
            prior_plan = (jload(Path(prior_path), {}) or {}).get("records") or {}
            say(f"prior plan {prior_path}: phone removal restricted to the {len(prior_plan)} records' own numbers")
        else:
            say("NO prior plan found: phone removal keyed on our Rel/Owner tags alone (other "
                "pushers share that vocabulary -- fine for a FIRST push only)")
        need = {lbl for k in keys for lbl in plan[k]["custom_fields"]}
        missing = sorted(l for l in need if l not in fields)
        if missing:
            say(f"STOP: custom fields missing on the account: {missing}. Run `schema --commit` first.")
            return 2
        create = bool(getattr(a, "create", False))
        records_src = jload(run / "records.json", {}) if create else {}
        created_path = run / "created_records.jsonl"
        if create:
            say("--create: records the exact-street search cannot find will be CREATED (address + owner, "
                f"then lists/tags/fields/note); every created uuid is appended to {created_path.name}. "
                "A created record cannot be deleted over this API, only in the UI.")
        ok = fail = 0
        with open(log_path, "a", encoding="utf-8") as lf:
            for i, k in enumerate(keys, 1):
                prior = (None if prior_plan is None else
                         {_d10(ph["number"]) for ph in ((prior_plan.get(k) or {}).get("phones") or [])})
                entry = _push_one_api(api, plan[k], fields, a.commit, prior=prior, create=create,
                                      lists=_lists_for_create(records_src.get(k)) if create else None,
                                      trace_stamp=not getattr(a, "no_trace_stamp", False))
                lf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                lf.flush()
                if entry.get("created"):
                    with open(created_path, "a", encoding="utf-8") as cf:
                        cf.write(json.dumps({"key": k, "uuid": entry.get("uuid"), "street": plan[k]["street"],
                                             "zip": plan[k]["zip"], "at": entry["at"]}) + "\n")
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
                           else ("dry run: would CREATE " + json.dumps((entry['steps'].get('create') or {}).get('body'))
                                 + f" then write {entry.get('would_write')}" if entry.get("would_create")
                                 else f"dry run: would write {entry.get('would_write')}")))
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

    s = sub.add_parser("anchors", help="attach vendor-file / RoW anchors (DOD, case, middle name, PR) to records.json")
    s.add_argument("--export", help="dp input export (default <run>/dp_input_export.csv) for the Drive source")
    s.add_argument("--vendor-root", default=str(VENDOR_ROOT))
    s.add_argument("--sheet26", help=f"Register of Wills extraction CSV (default {SHEET26_DEFAULT})")
    s.add_argument("--row-ledger", default=str(ROW_LEDGER))
    s.add_argument("--estate-search", action="store_true",
                   help="Register of Wills Estate Search by estate number for anchored records with no DOD (1 Firecrawl scrape each)")
    s.add_argument("--estate-limit", type=int, default=0)
    s.set_defaults(fn=cmd_anchors)

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
    s.add_argument("--max-rels", type=int, default=0,
                   help="cap on the NON-SIGNER blood/unknown relative tier, after the "
                        "intestacy signer set. DEFAULT 0 (Basem, 2026-09-16): load the "
                        "signers and nobody else. A non-signer cannot convey the property, "
                        "so they take no REL slot, no dial number and no Trestle solve -- "
                        "they are still NAMED in the note. SIGNERS are never capped by "
                        "this: every child must sign, so six children take six slots.")
    s.add_argument("--channel-cap", type=int, default=0,
                   help="in-laws kept as dial channels per record, after the blood tier. "
                        "DEFAULT 0 (Basem, 2026-09-15): an in-law can never sign, so they "
                        "take no REL slot and no dial number and their phones are never "
                        "Trestle-scored -- a dropped in-law is still NAMED in the note's "
                        "'In-laws found, not loaded' line, which is the whole point of "
                        "keeping them. Raise it only on an explicit ask.")
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
    s.add_argument("--keys-file", metavar="FILE",
                   help="only these keys (JSON list or one key per line); with --force re-researches them")
    s.add_argument("--presume-alive-without-signal", action="store_true",
                   help="FTM-style cohorts: only research records carrying a deceased "
                        "signal (Probate/Obituary list, deceased-ish tag, obituary date, "
                        "PR); everything else gets the presumed-alive verdict for free")
    s.set_defaults(fn=cmd_research)

    s = sub.add_parser("augment")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_augment)

    s = sub.add_parser("score")
    s.add_argument("--litigator", action="store_true",
                   help="no-op: the litigator add-on ALWAYS rides (Basem, 2026-10-05)")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--max-score-per-rel", type=int, default=0,
                   help="0 = price every number on a kept contact (the default)")
    s.set_defaults(fn=cmd_score)

    s = sub.add_parser("build")
    s.add_argument("--max-rel-field-phones", type=int, default=3,
                   help="REL{n}: Phone 1..N custom fields only; the account has 3")
    s.add_argument("--dial-cap", type=int, default=DIAL_LIST_CAP,
                   help=f"numbers on one record's dial list (default {DIAL_LIST_CAP}). "
                        f"DataSift's own ceiling is {OWNER_PHONE_CAP}; this is the quality "
                        "ceiling under it. Numbers over the cap stay in the REL fields and "
                        "the note says which contacts they belong to.")
    s.add_argument("--max-rel-slots", type=int, default=MAX_REL_SLOTS,
                   help="REL slots per record (Basem: the existing 5 plus at most 2 more)")
    s.add_argument("--include-phoneless", action="store_true",
                   help="give relatives with no kept phone a REL slot anyway (name only)")
    s.add_argument("--extra-tag", action="append", default=[], metavar="TAG",
                   help="property tag applied to EVERY record in the batch, repeatable "
                        "(e.g. --extra-tag 'Priority 1')")
    s.add_argument("--month-tag", metavar="MM/YYYY",
                   help="month on the `Deep Prospected` / `DP No Numbers` tags (default: this month)")
    s.add_argument("--trace-month", metavar="MM/YYYY",
                   help="month on the `SS Traced` tag (default: read off the paid SmartSkip order id, "
                        "else --month-tag)")
    s.add_argument("--keys-file", help="CSV (key column) or JSON list: rebuild ONLY these keys")
    s.add_argument("--plan-out", help="write the plan (and every artifact) here instead of push_plan.json; "
                                      "use with --keys-file for a pilot that leaves the live plan alone")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("v5merge", help="merge rerun_v5/{title,court,deed}.json into records.json (backup first)")
    s.add_argument("--sidecar-dir", help="default <run-dir>/rerun_v5")
    s.set_defaults(fn=cmd_v5merge)

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
    s.add_argument("--include-no-numbers", action=argparse.BooleanOptionalAction, default=True,
                   help="push records that got no phones too. DEFAULT ON since 2026-09-23 "
                        "(Basem: 'including the no numbers allows for deep prospecting') -- "
                        "a record with no numbers is the DP candidate, not the exception, "
                        "and it is the one that most needs the pinned note naming who to "
                        "find. It also lands the `SS Traced MM/YYYY` date and the "
                        "owner.skiptraced flag, without which a record the batch paid to "
                        "trace that came back empty gets re-skipped next month. "
                        "--no-include-no-numbers restores the old phones-only behaviour")
    s.add_argument("--prior-plan", metavar="JSON",
                   help="push_plan.json this run dir pushed BEFORE; only numbers it wrote may be "
                        "removed (default: newest push_plan_prestrip_*.json in the run dir)")
    s.add_argument("--keys-file", metavar="JSON",
                   help="ORDERED JSON list of plan keys: push exactly these, in this order "
                        "(e.g. keys_never_dialed.json from dp_strip_order.py)")
    s.add_argument("--no-trace-stamp", action="store_true",
                   help="do NOT set owner.skiptraced / skiptrace_attempts (an upload where no trace ran)")
    s.add_argument("--create", action="store_true",
                   help="CREATE records the exact-street search cannot find (POST /api/internal/property/, "
                        "address + owner, then lists/tags/fields/note by the per-record routes). Without "
                        "--commit it only shows the create body. Created uuids -> created_records.jsonl")
    s.set_defaults(fn=cmd_push)

    a = ap.parse_args()
    if getattr(a, "probe", None) == "__auto__":
        a.probe = "__auto__"
    return a.fn(a)
if __name__ == "__main__":
    sys.exit(main())
