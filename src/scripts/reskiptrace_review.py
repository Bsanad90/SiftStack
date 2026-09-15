"""Execute the 2026-09-15 review of the `Re-Skiptrace` status: note + tag + status
+ owner fixes + task closure per record, with probe / commit / qa / undo gates.

Why this exists (Basem, 2026-09-15: "the status seems to not have tags or notes"):
`reskiptrace_audit.py` proved that all 15 records in the status are already skip
traced, that 11 were parked by dialers since 09-08 -- 13 of 15 without a single
number dispositioned -- and that the only "reasons" on file are the titles of
Pal's tasks to Basem. `reskiptrace_fix.py` handles the SCORE/RETAG shape but
knows nothing about entity owners, undialed numbers or tasks, and the decisions
here are per-record human calls, not a rule. So the decisions are DATA in this
file (`DECISIONS`), reviewed once, and this script is only the executor:

  return_to_dialer  status -> `No Answer`, note says dial the undialed numbers first
  entity            Enformion BusinessV2 principal lookup; if ONE human principal is
                    found the owner block gets the name (company kept, the
                    lane_incomplete_cleanup surface: collision check + merge guard),
                    status -> `No Answer`; else note the candidates and stay parked
  trust_owner       trustee name from the title into the owner block (`Alan B` +
                    `Stull`, no period -- a period makes the record Incomplete),
                    status -> `No Answer`
  mail_track        DP already came back empty -> `Mail Only` tag, stays in the status
                    (same as the 09-07 residue: a mail-track record has no active
                    dialer status to go to), note explains
  residue           the 09-07 leftovers + the out-of-footprint blank-owner row: note
                    + review tag only, nothing else changes
  dp                deep prospected through obituary_dp_batch (run dir
                    output/dp_reskiptrace3); this script only posts the review note
                    and flips the status AFTER that push (`--phase post-dp`)

Every record gets ONE pinned `RE-SKIPTRACE REVIEW <date>` board note (same family
as reskiptrace_fix, so either tool replaces the other's, never anything else) and
the `Re-Skip Reviewed MM/YYYY` tag. Open re-skip tasks on a processed record are
completed with an outcome note (`PATCH /api/internal/task/{uuid}/`; `completed` is
an ISO timestamp, null reopens -- probed live before bulk use).

Usage (in order):
    python -u src/scripts/reskiptrace_review.py plan [--research]   # read-only (+Enformion)
    python -u src/scripts/reskiptrace_review.py push --probe <uuid>
    python -u src/scripts/reskiptrace_review.py push --commit [--phase main|post-dp] [--only u1,u2]
    python -u src/scripts/reskiptrace_review.py qa
    python -u src/scripts/reskiptrace_review.py undo <backup.json>
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from dpd_lane_phone_score import (  # noqa: E402
    MAIL_ONLY,
    jload,
    jsave,
    rank_of,
    say,
    score_tag_of,
    tag_names,
)
from lane_incomplete_cleanup import colliding_owners, owner_block, snapshot  # noqa: E402
from not_related_requalify import bad_phone_status  # noqa: E402
from obituary_dp_batch import _note_family, board_messages  # noqa: E402
from phone_validator import clean_phone  # noqa: E402
from reskiptrace_audit import (  # noqa: E402
    fetch_open_tasks,
    moved_into_status,
    record_log,
    status_key,
)
from reskiptrace_fix import (  # noqa: E402
    REQUALIFY_STATUS,
    REVIEW_FAMILY,
    REVIEW_TAG,
    STATUS,
    TODAY,
    Api,
    build_dial_sheet,
    pinned_note_of_family,
    post_pinned_note,
    rel_names_from_cf,
    who_owns_number,
)

# The first (2026-09-15 morning) version posted a process note under this family on one
# record (9832 Marcliff Ct). Basem: not a review note, a dial sheet. Our own family, so
# the push removes it when it posts the dial sheet.
LEGACY_FAMILY = "RE-SKIPTRACE REVIEW"

RUN = ROOT / "output" / "reskiptrace_review"
PLAN = RUN / "plan.json"
PUSH_LOG = RUN / "push_log.json"
PROBE_OK = RUN / "probe_ok.json"
PRINCIPALS = RUN / "principals.json"

TASK_RE = ("re-skip", "reskip", "re skip", "no name", "not related")

# ───────────────────────── the decisions (reviewed 2026-09-15) ─────────────────────────
# `why` is what the note says under WHY IT WAS HERE; `do` under DONE. Phones are
# never touched by this script.
DECISIONS: dict[str, dict] = {
    # ── UNDIALED: back to the dialer ──
    "ce46bb61-2b04-4601-a73d-aec17a54c576": {
        "street": "9832 Marcliff Ct", "action": "return_to_dialer",
        "why": "parked by Pal 2026-09-15 with a 'Reskip trace' task; owner Judith Perrault is "
               "skip traced and all 5 numbers (3 Dial First, 1 Dial Second, 1 Dial Fourth) are "
               "still UNKNOWN -- none was dialed",
        "do": "back to the dialer: dial the Dial First numbers and disposition each one before "
              "parking the record again"},
    "7b765758-7dfb-4843-bcc7-2d7fa1b30a0f": {
        "street": "616 John Marshall Dr Nw", "action": "return_to_dialer",
        "why": "parked by Pal 2026-09-14 ('only 1 name on the numbers'); Pamela Soncini is the "
               "titled owner and all 3 Dial Fourth numbers are still UNKNOWN -- none was dialed",
        "do": "back to the dialer: one name IS the owner, dial the 3 numbers; if all three come "
              "back wrong, mark them WRONG and it becomes a deep-prospect job"},
    "79db281d-d57f-469b-86ea-bc97bc5b022f": {
        "street": "901 1St St Nw", "action": "return_to_dialer",
        "why": "parked by Bagoury 2026-09-14 with nothing attached; 9 numbers on record: 2 WRONG, "
               "3 NO_ANSWER, and 4 Dial First still UNKNOWN (never dialed)",
        "do": "back to the dialer: 4 undialed Dial First numbers first",
        # probate_merge_20260831T175059: estate 2026-ADM-000832 (DC), decedent Jerome Harold
        # Parson III, PR = attorney Tamara McDowell Christian Esq, 1629 K St NW Suite 300;
        # Donna Wulkan "matched neither PR nor decedent" -- she is the county-listed owner.
        "status_line": "PROBATE (DC estate 2026-ADM-000832): the DECEDENT is Jerome Harold "
                       "Parson III. Donna Wulkan is the county-listed owner and is NOT the PR "
                       "and NOT the decedent -- relationship unknown; the numbers below are hers.",
        "extra": ["ASK FOR: Donna Wulkan -- confirm how she is connected to the Parson estate "
                  "(heir? co-owner? buyer?). The estate's PR is attorney Tamara McDowell "
                  "Christian Esq, 1629 K St NW Suite 300, Washington DC 20006 (office, no phone "
                  "on file)."]},
    "74254d26-c54c-42bd-9b19-034f1b8ff4d5": {
        "street": "11 Shaw St", "action": "return_to_dialer",
        "why": "parked by Pal 2026-09-11 with nothing attached; 6 numbers (4 Dial First), only "
               "one ever dialed (NO_ANSWER today by Bagoury), 5 still UNKNOWN",
        "do": "back to the dialer: 3 undialed Dial First numbers (Rel-tagged relatives of "
              "Julia Gay) first"},
    "faf1db72-0415-4369-9625-33dd46d1bdba": {
        "street": "5857 Glen Forest Dr", "action": "return_to_dialer",
        "in_status": False,
        "why": "Pal's 'Reskip trace' task 2026-09-14 (record never moved into the status); "
               "1 of 4 numbers marked WRONG, the other Dial First + 2 Dial Fourth still UNKNOWN",
        "do": "keep dialing: 3 undialed numbers on Maria Flores; re-skip would return the same set"},
    # ── ENTITY owners: research the principal ──
    "ecfeef09-adbd-43b0-bafc-f321c9acd8a0": {
        "street": "5217 Clay St Ne", "action": "entity", "entity": "Rainman Capital LLC",
        "city_state": "Washington, DC",
        "why": "parked by Pal 2026-09-14 ('Re-Skip'); owner is Rainman Capital LLC with no person "
               "name, 3 numbers (2 Dial First) all UNKNOWN -- the dialer had nobody to ask for",
        "do": "principal research (Enformion BusinessV2 / SOS) -- see PRINCIPALS below",
        "candidates": [
            "Thomas Kokolis -- filed motions for Rainman Capital, LLC in 5615 Addison Road LLC "
            "v. Rainman Capital, LLC (MD bankruptcy court, 2019); UNVERIFIED that it is the "
            "same DC entity",
            "the LLC's tax mailing address is 2126 Wyoming Ave NW, Washington DC 20008 (a "
            "Kalorama single-family home) -- the member most likely lives there",
        ]},
    "8b612f22-9342-4aea-9608-61c2123113f4": {
        "street": "824 Eastern Ave Ne", "action": "entity", "entity": "822 Eastern Avenue LLC",
        "city_state": "Washington, DC",
        "why": "parked by Pal 2026-09-14 ('No Name'); owner is 822 Eastern Avenue LLC with no "
               "person name, 2 Dial First numbers both UNKNOWN",
        "do": "principal research (Enformion BusinessV2 / SOS) -- see PRINCIPALS below",
        "candidates": [
            "the LLC's tax mailing address is 4905 Sheriff Rd NE, Washington DC 20019 = HERISE "
            "GROWTH CENTER INC (assisted living; NPI 1740171891), authorized official Andrane "
            "Gordon, Founder/President, 202-617-1006 / 227-263-4371 -- same address, so likely "
            "the LLC's principal; UNVERIFIED against the DC business registry",
        ]},
    "0d75877e-beaf-4bd6-8039-40801e368fb6": {
        "street": "5819 Ridings Manor Pl", "action": "trust_owner",
        "owner_first": "Alan B", "owner_last": "Stull",
        "why": "parked by Pal 2026-09-14 ('Reskip trace'); title reads 'Stull Alan B Tr' -- the "
               "trustee IS the person to call, the record just had no first/last name; 5 numbers "
               "(4 Dial First) all UNKNOWN",
        "do": "owner name set to Alan B Stull (trust title kept); back to the dialer"},
    # ── MAIL TRACK ──
    "98312d25-3381-4759-a2d7-893ce9ff4251": {
        "street": "14322 Bradshaw Dr", "action": "mail_track",
        "why": "parked by Mostafa 2026-09-14 after marking both numbers DEAD; deep prospecting "
               "already ran 2026-09-07 (owner Joan Guyther died 2026-06-29, no heir numbers "
               "found -> 'DP No Numbers')",
        "do": f"tagged {MAIL_ONLY}; stays here as the mail-track parking status; a re-skip of a "
              "deceased owner returns nothing new"},
    # ── RESIDUE (09-07 leftovers, on purpose) ──
    "55be7644-512b-4be8-8cc4-f3cbd30c6228": {
        "street": "7796 Ann Harbor Dr", "action": "residue",
        "why": "left here 2026-09-07 on purpose: blank owner, both numbers Drop-tier, tagged "
               f"{MAIL_ONLY} + Priority 2",
        "do": "nothing further; mail track"},
    "ba73c3b2-b4b2-43af-aa8f-21db7db9b314": {
        "street": "112 Apple Creek Rd", "action": "residue",
        "why": "left here 2026-09-07 on purpose: blank owner, the one number is Drop-tier, tagged "
               f"{MAIL_ONLY} + Priority 2",
        "do": "nothing further; mail track"},
    "c55354fb-9371-40d3-a99b-caa6442ed887": {
        "street": "1205 N Columbus St", "action": "residue",
        "why": "left here 2026-09-07 on purpose: both numbers Drop-tier, tagged "
               f"{MAIL_ONLY} + Priority 1",
        "do": "nothing further; mail track"},
    "e405aa44-caeb-490e-8805-9085e555acf1": {
        "street": "4845 Little Falls Rd # 76", "action": "residue",
        "why": "left here 2026-09-07: blank owner, Incomplete, one DEAD number, the house sold "
               "May 2025 BEFORE the 03/2026 divorce import -- malformed row",
        "do": "manual review or delete (Basem)"},
    "640d66e6-aea9-4486-bdf6-c14c177fa63d": {
        "street": "4431 Potts Ct # 595", "action": "residue",
        "why": "parked by Pal 2026-09-08; blank owner (Divorce list, Plaintiff.1 DEAD / "
               "Plaintiff.2 NO_ANSWER); Owings Mills = Baltimore County, OUTSIDE the 6-county "
               "footprint since 2026-09-01",
        "do": "out of footprint: no research spend; leave or delete (Basem)"},
    # ── DP (obituary_dp_batch, run dir output/dp_reskiptrace3) ──
    "c63c43e9-0c12-4699-9e4d-81aaee32f814": {
        # DP 2026-09-15 (output/dp_reskiptrace3): owner Darci Vanderhoff DIED 2026-01-23
        # (dignitymemorial obituary); SmartSkip returned no heir with a number. The 3 numbers
        # on record are the dead owner's -> mail track. The first post-dp pass flipped it to
        # No Answer on the strength of those numbers; `status_force` puts it back.
        "street": "8716 Plymouth Rd", "action": "mail_track", "phase": "post-dp",
        "status_force": STATUS,
        "status_line": "DECEASED -- Darci M. Vanderhoff, DOD 2026-01-23 (obituary, confirmed "
                       "2026-09-15). Title: Darci M Vanderhoff Revocable Living Trust.",
        "why": "parked by Mostafa 2026-09-11 with nothing attached; Obituary/Inheritance list, "
               "trust title, 2 Dial Fourth NO_ANSWER + 1 never-scored number -- all three are the "
               "DEAD owner's numbers. Deep prospecting 2026-09-15 found no heir with a phone",
        "do": "do NOT dial the owner's numbers (she died 2026-01-23); no heir number found -> "
              f"tagged {MAIL_ONLY}, mail to the trust; successor trustee research is the next step"},
    "a57804aa-1c27-45f2-abc5-d3496f05e83f": {
        "street": "6122 1St Pl Ne", "action": "dp",
        "why": "parked by Bagoury 2026-09-14 after marking the one mobile DEAD; the other number "
               "is Drop-tier -> zero callable numbers; Register of Wills record, owner Andrew "
               "Brian Shane is the PR",
        "do": "deep prospected (SmartSkip relatives + Trestle) -- see the DEEP PROSPECTING note"},
    "d3f1a25f-1086-42c3-9bfe-15ffaa0629be": {
        # Arrived mid-review 2026-09-15 18:11: Bagoury dialed the only number (DEAD) and
        # parked it. SiftMap obituary auto-add 09-03 (Robert Kern, 86, obituary 2026-08-05),
        # DataSift skip trace 09-06 returned that one landline. DP run output/dp_reskiptrace4.
        "street": "7639 Paradise Beach Rd", "action": "dp",
        "why": "parked by Bagoury 2026-09-15 after the only number (a landline) came back DEAD; "
               "Obituary list, obituary dated 2026-08-05, owner Robert Kern age 86, owner-occupied "
               "free & clear since 1988; DataSift skip trace 09-06 found only that one number",
        "do": "deep prospected 2026-09-15 (obituary research + SmartSkip relatives + Trestle) -- see "
              "the DEEP PROSPECTING note",
        # Register of Wills checked 2026-09-15 (Probate Extraction 2 sheet + our 08-21 pull):
        # estate 118927, OPEN, published 08/21/2026, co-PRs = two daughters. Obituary (Singleton
        # Funeral Home): wife June predeceased; children = Matija Hoppe, Robin Kronenwetter,
        # Robert Kern Jr. Six SmartSkip "children" were not children; Frank/Kevin Kern are nephews.
        "status_line": "DECEASED -- Robert Richard Kern Sr, DOD 2026-08-05 (obituary). Wife June "
                       "predeceased. Anne Arundel Register of Wills estate #118927 is OPEN "
                       "(published 08/21/2026).",
        "priority_names": ["Matija Hoppe", "Robin Kronenwetter", "Robert Kern Jr"],
        "next": "call the co-PRs first (Matija Hoppe, Robin Kronenwetter), then Robert Jr; the "
                "nephews and in-laws are only a way to reach those three. The 410-255-2578 landline "
                "is the house line Bagoury found DEAD today -- skip it.",
        "extra": ["WHO SIGNS: the two co-personal representatives, both daughters -- MATIJA DARLENE "
                  "HOPPE (136 Cottage Grove Dr, Pasadena) and ROBIN MARIE KRONENWETTER (7624 Paradise "
                  "Beach Rd, next door). Heirs: those two + son ROBERT KERN JR (58, lives at the "
                  "property). Everyone else on the list is a nephew, in-law or other relative -- "
                  "a way to reach the three, not a signer."]},
    "da23b3d5-4774-4be6-8ab4-6dec8b5354b2": {
        "street": "1401 Potomac Ave Se Apt 2", "action": "dp", "in_status": False,
        "why": "Pal's task 2026-09-14: both numbers answered 'not related to the property', "
               "marked WRONG (record never moved into the status)",
        "do": "deep prospected (SmartSkip relatives + Trestle) -- see the DEEP PROSPECTING note"},
}

FLIP_ACTIONS = {"return_to_dialer", "trust_owner", "dp"}     # entity flips only with a name


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def record_context(api: Api, uuid: str, d: dict, priority_names: list[str] | None = None) -> dict:
    """Everything the dial sheet needs beyond the detail body: custom fields (REL/PR
    names live there), the DP verdict from the board, phones in current order."""
    _st, cf_raw = api.get(f"/api/internal/property/{uuid}/custom-field/")
    rows = cf_raw if isinstance(cf_raw, list) else ((cf_raw.get("results") or []) if isinstance(cf_raw, dict) else [])
    cf = {((r.get("custom_field") or {}).get("label") or ""): (r.get("value") or "").strip()
          for r in rows if (r.get("value") or "").strip()}
    o = d.get("owner") or {}
    person = " ".join(x for x in [o.get("first_name"), o.get("last_name")] if x).strip()
    pr_name = " ".join(x for x in [cf.get("PR First Name"), cf.get("PR Last Name")] if x).strip() \
        or (d.get("personal_representative") or "")
    deceased = "DECEASED per the account's owner flag" if o.get("deceased") else ""
    for m in board_messages(api, uuid):
        txt = m.get("message") or ""
        if txt.startswith("DEEP PROSPECTING"):
            dec = re.search(r"DECEDENT:\s*(.+)", txt)
            ver = re.search(r"VERDICT:\s*(.+)", txt)
            if ver and "did die" in ver.group(1).lower() and dec:
                deceased = f"DECEASED -- {dec.group(1).strip()} (deep prospecting {str(m.get('created') or '')[:10]})"
            elif ver:
                deceased = f"{ver.group(1).strip()} (deep prospecting {str(m.get('created') or '')[:10]})"
            break
    if not deceased and d.get("last_obituary_date"):
        deceased = (f"unknown -- an obituary dated {d['last_obituary_date'][:10]} is attached "
                    "(may be a relative's); confirm on the call")
    lists = tag_names(d.get("lists"))
    if not deceased and any("probate" in (l or "").lower() or "register of wills" in (l or "").lower()
                            for l in lists):
        # A probate record means SOMEBODY died. On this account the owner field is the PR
        # when a person is PR; when the PR is an attorney/company, the owner named here is
        # usually the DECEDENT (the 2026-09-08 corporate-PR mechanic). Say so on the sheet.
        if pr_name and person and pr_name.lower() != person.lower():
            deceased = (f"PROBATE record -- the estate's personal representative is {pr_name}, "
                        f"so {person} is probably the DECEDENT; the numbers below were skip "
                        "traced on that name. Ask for the personal representative.")
        elif pr_name and person and pr_name.lower() == person.lower():
            deceased = "alive -- this person is the estate's personal representative (probate record)"
        else:
            deceased = "PROBATE record (an estate is open); confirm who died on the call"
    phones = []
    for p in (o.get("phones") or []):
        if isinstance(p, dict) and p.get("number"):
            tags = tag_names(p.get("tags"))
            phones.append({"cleaned": clean_phone(str(p["number"])), "number": p["number"],
                           "type": p.get("type"), "status": p.get("status"), "tags": tags,
                           "tag": score_tag_of(tags) or ""})
    # print best tier first, bad-status numbers last (the dialer's real order); a
    # decision may name the people to reach FIRST (signers / PRs) -- their numbers
    # lead regardless of tier, because a Dial Second PR beats a Dial First nephew.
    rel_names = rel_names_from_cf(cf)
    prio = [n.lower() for n in (priority_names or [])]
    def _prio(ph):
        who = who_owns_number(ph["tags"], person, rel_names, pr_name).lower()
        return 0 if any(n in who for n in prio) else 1
    phones.sort(key=lambda ph: (1 if bad_phone_status(ph["status"]) else 0, _prio(ph),
                                rank_of(ph["tag"] or "Unknown")))
    return {"cf": cf, "person": person, "company": o.get("company") or "", "pr_name": pr_name,
            "deceased": deceased, "phones": phones,
            "emails": [e for e in (o.get("emails") or []) if isinstance(e, str)]}


NEXT_STEP = {
    "return_to_dialer": "dial the numbers above best-first; mark each one WRONG / DEAD / NO ANSWER. "
                        "Only when every number is marked wrong does this become a deep-prospect "
                        f"job -- never a re-skip. Status set to {REQUALIFY_STATUS}.",
    "entity": "ask for the owner of the LLC by the candidate name below; if nobody claims it, mark "
              "the number WRONG. Basem confirms the principal on DC CorpOnline (boss.dc.gov).",
    "trust_owner": f"the trustee named above is the person to ask for. Status set to {REQUALIFY_STATUS}.",
    "mail_track": f"do NOT dial -- owner deceased and no heir numbers found; tagged {MAIL_ONLY}, mail only.",
    "residue": "nothing to dial; mail only.",
    "dp": "the DEEP PROSPECTING note on this record lists the heirs and whose number is whose; "
          f"dial those. Status set to {REQUALIFY_STATUS}.",
}


def build_note(dec: dict, ctx: dict, principals: list[dict] | None, owner_set: dict | None) -> str:
    extra = []
    if dec["action"] == "entity":
        if principals:
            extra.append("PRINCIPALS (SOS filings): " + "; ".join(
                f"{p['name']} {p.get('title') or ''}".strip() for p in principals))
        for c in dec.get("candidates") or []:
            extra.append("WHO TO ASK FOR: " + c)
    if dec["action"] == "trust_owner" and owner_set:
        extra.append(f"TRUSTEE / PERSON TO ASK FOR: {owner_set['first_name']} {owner_set['last_name']}")
    nxt = dec.get("next") or NEXT_STEP[dec["action"]]
    if dec["action"] == "residue" and dec["street"].startswith("4431 Potts"):
        nxt = "out of the 6-county footprint; nothing to dial."
    owner_name = ctx["person"]
    if owner_set:
        owner_name = f"{owner_set['first_name']} {owner_set['last_name']}"
    extra = list(dec.get("extra") or []) + extra
    return build_dial_sheet(
        owner_name=owner_name, company=ctx["company"],
        owner_deceased=dec.get("status_line") or ctx["deceased"],
        pr_name=ctx["pr_name"], cf=ctx["cf"], phones=ctx["phones"], emails=ctx["emails"],
        why_here=dec["why"] + ". The owner is ALREADY skip traced -- a re-skip returns the same numbers",
        next_step=nxt, extra=extra, source=dec["action"].replace("_", " "))


def research_entities(decisions: dict) -> dict:
    """Enformion BusinessV2 principal lookup for every `entity` decision (cached)."""
    import enformion_business as eb
    out = jload(PRINCIPALS, {})
    if not eb.is_configured():
        say("Enformion not configured (ENFORMION_AP_NAME/PASSWORD) -- skipping research")
        return out
    for u, dec in decisions.items():
        if dec["action"] != "entity" or u in out:
            continue
        say(f"  Enformion BusinessV2: {dec['entity']} ({dec['city_state']})")
        try:
            recs = eb.business_search(dec["entity"], dec["city_state"], results=5)
            offs = eb.extract_officers(recs, dec["entity"])
        except Exception as e:  # noqa: BLE001
            say(f"    ERROR {e}")
            continue
        if not recs:
            # business_search swallows HTTP errors and returns [] (2026-09-15: the
            # Enformion token was dead -> "Invalid Galaxy Token"). An empty answer
            # is NOT cached, so a retry with a working key actually retries.
            say("    no records (error or genuinely none) -- not cached")
            continue
        out[u] = {"entity": dec["entity"], "records": len(recs), "principals": offs,
                  "at": datetime.now().isoformat(timespec="seconds"),
                  "raw_names": [r.get("businessName") or r.get("name") for r in recs][:10]}
        say(f"    {len(recs)} filing record(s); {len(offs)} human principal(s): "
            + "; ".join(f"{o['name']} ({o.get('title') or '-'})" for o in offs))
        jsave(PRINCIPALS, out)
    return out


def _person_split(full: str) -> tuple[str, str]:
    toks = [t for t in full.replace(",", " ").split() if t]
    if len(toks) < 2:
        return "", ""
    # "LAST, FIRST M" (SOS style) when a comma led; else FIRST [M] LAST
    if "," in full:
        return " ".join(toks[1:]).title().replace(".", ""), toks[0].title()
    return " ".join(toks[:-1]).title().replace(".", ""), toks[-1].title()


def cmd_plan(a) -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    api = Api()
    say("JWT minted OK")
    principals = research_entities(DECISIONS) if a.research else jload(PRINCIPALS, {})
    open_tasks = fetch_open_tasks(api)

    plan, problems = {}, []
    for u, dec in DECISIONS.items():
        st, d = api.get(f"/api/internal/property/{u}/")
        if st != 200:
            problems.append(f"{dec['street']}: detail {st}")
            continue
        live_status = d.get("status")
        expect_in = dec.get("in_status", True)
        push_log = jload(PUSH_LOG, {})
        already_flipped = (status_key(live_status) == status_key(REQUALIFY_STATUS)
                           and u in push_log and not push_log[u].get("error"))
        if expect_in and status_key(live_status) != status_key(STATUS) and not already_flipped:
            problems.append(f"{dec['street']}: status is {live_status!r}, expected {STATUS!r}")
            continue
        if not expect_in and live_status not in (None, "") and not already_flipped:
            problems.append(f"{dec['street']}: status is {live_status!r}, expected blank")
            continue
        snap = snapshot(d)
        tags = tag_names(d.get("tags"))
        tasks = [t for t in open_tasks.get(u, [])
                 if any(k in t["title"].lower() for k in TASK_RE)]
        moved = moved_into_status(record_log(api, u), status_key(STATUS))

        entry = {"street": dec["street"], "action": dec["action"], "phase": "main",
                 "status_before": live_status, "tags_before": tags,
                 "owner_before": snap, "moved": moved,
                 "tasks": tasks, "tags_add": [], "status_to": None,
                 "owner_set": None, "principals": None}
        if dec["action"] == "dp" or dec.get("phase") == "post-dp":
            entry["phase"] = "post-dp"
        flip = dec["action"] in FLIP_ACTIONS
        if dec["action"] == "entity":
            pr = (principals.get(u) or {}).get("principals") or []
            entry["principals"] = pr
            humans = [p for p in pr if _person_split(p["name"])[0]]
            if len(humans) == 1:
                first, last = _person_split(humans[0]["name"])
                entry["owner_set"] = {"first_name": first, "last_name": last}
                flip = True
            elif humans:
                entry["note_extra"] = f"{len(humans)} principals -- owner name NOT set (ambiguous)"
        if dec["action"] == "trust_owner":
            entry["owner_set"] = {"first_name": dec["owner_first"], "last_name": dec["owner_last"]}
        if dec["action"] == "mail_track" and MAIL_ONLY not in tags:
            entry["tags_add"].append(MAIL_ONLY)
        if REVIEW_TAG not in tags:
            entry["tags_add"].append(REVIEW_TAG)
        if flip:
            entry["status_to"] = REQUALIFY_STATUS
        if dec.get("status_force") and status_key(live_status) != status_key(dec["status_force"]):
            entry["status_to"] = dec["status_force"]      # explicit put-back, verified on read-back
        ctx = record_context(api, u, d, dec.get("priority_names"))
        entry["context"] = {k: v for k, v in ctx.items() if k != "cf"}
        if dec["action"] == "dp":
            # Post-DP: a record that came back with no callable number is mail track,
            # not a dialer record (8716 Plymouth Rd: owner died, no heir numbers found).
            live = [p for p in ctx["phones"] if not bad_phone_status(p["status"])
                    and rank_of(p["tag"] or "Unknown") <= 4]
            if not live:
                entry["status_to"] = None
                if MAIL_ONLY not in tags and MAIL_ONLY not in entry["tags_add"]:
                    entry["tags_add"].append(MAIL_ONLY)
                dec = dict(dec, do=dec["do"] + f"; no callable heir number came back -> tagged {MAIL_ONLY}")
        entry["note"] = build_note(dec, ctx, entry["principals"], entry.get("owner_set"))
        if entry.get("owner_set"):
            blk = owner_block(d)
            blk.update(entry["owner_set"])
            entry["owner_block"] = blk
        plan[u] = entry

    jsave(PLAN, plan)
    say("")
    say(f"=== plan: {len(plan)} records ({len(problems)} problems) ===")
    for u, e in plan.items():
        say(f"  [{e['phase']:7}] {e['street']:28} {e['action']:17} "
            f"status {e['status_before']!r} -> {e['status_to'] or '(keep)':10} "
            f"+tags {e['tags_add']} owner_set={e.get('owner_set')} tasks={len(e['tasks'])}")
        if e.get("principals") is not None:
            say(f"           principals: {[p['name'] for p in e['principals']] or 'none'}")
    for p in problems:
        say(f"  PROBLEM {p}")
    say(f"\nplan: {PLAN}")
    return 0 if not problems else 1


# ───────────────────────── push ─────────────────────────

def set_owner(api: Api, uuid: str, block: dict, before: dict, log: dict) -> bool:
    """The lane_incomplete_cleanup surface: collision check, PATCH, merge guard, read back."""
    hits = colliding_owners(api, before["owner_uuid"], block["address"].get("street"),
                            block["address"].get("postal_code"))
    if hits:
        log["error"] = f"owner mailing collides with {hits}: not written"
        return False
    st, resp = api.write("PATCH", f"/api/internal/property/{uuid}/", {"owner": block})
    log["steps"]["owner"] = {"status": st, "resp": str(resp)[:150] if st != 200 else ""}
    if st != 200:
        log["error"] = f"owner PATCH {st}"
        return False
    time.sleep(1.0)
    st2, d2 = api.get(f"/api/internal/property/{uuid}/")
    after = snapshot(d2) if st2 == 200 else {}
    log["steps"]["owner"]["after"] = after
    if not after or after["owner_uuid"] != before["owner_uuid"] \
            or after["phones"] < before["phones"] or after["skiptraced"] != before["skiptraced"]:
        log["error"] = (f"MERGE_SUSPECTED owner {before['owner_uuid']}->{after.get('owner_uuid')} "
                        f"phones {before['phones']}->{after.get('phones')}")
        return False
    got = ((after["block"].get("first_name") or "").lower(), (after["block"].get("last_name") or "").lower())
    want = (block["first_name"].lower(), block["last_name"].lower())
    if got != want:
        log["error"] = f"owner read-back {got} != {want}"
        return False
    return True


def push_one(api: Api, uuid: str, e: dict, complete_tasks: bool) -> dict:
    log = {"uuid": uuid, "street": e["street"], "steps": {},
           "at": datetime.now().isoformat(timespec="seconds")}
    if e.get("owner_block"):
        if not set_owner(api, uuid, e["owner_block"], e["owner_before"], log):
            return log
    body: dict = {}
    st0, full = api.get(f"/api/internal/property/{uuid}/")
    current = tag_names((full or {}).get("tags")) if st0 == 200 else []
    add = [t for t in e["tags_add"] if t not in current]
    if add:
        body["tags"] = current + add
    if e.get("status_to"):
        body["status"] = e["status_to"]
    if body:
        st, resp = api.write("PATCH", f"/api/internal/property/{uuid}/", body)
        log["steps"]["prop"] = {"status": st, "added": add, "status_to": e.get("status_to"),
                                "resp": str(resp)[:150] if st != 200 else ""}
        if st not in (200, 201, 204):
            log["error"] = f"property PATCH {st}"
            return log
    note_log = post_pinned_note(api, uuid, e["note"])
    log["steps"]["note"] = note_log
    if note_log.get("error"):
        log["error"] = note_log["error"]
        return log
    # our own earlier process note (first version of this script), never anything else
    legacy = [m for m in board_messages(api, uuid)
              if m.get("uuid") and _note_family(m.get("message") or "") == LEGACY_FAMILY]
    if legacy:
        log["steps"]["legacy_removed"] = []
        for m in legacy:
            st_d, _ = api.write("DELETE", f"/api/internal/property/{uuid}/message/{m['uuid']}/", {})
            log["steps"]["legacy_removed"].append({"uuid": m["uuid"], "status": st_d,
                                                   "message": (m.get("message") or "")[:4000]})
    # An entity record whose principal is still unconfirmed keeps its task open: the
    # task is Basem's reminder to confirm the name on the DC registry.
    if complete_tasks and e.get("tasks") and not (e["action"] == "entity" and not e.get("owner_set")):
        outcome = (f"Reviewed {TODAY}: not a re-skip (owner already skip traced). "
                   f"{e['action'].replace('_', ' ')} -- see the pinned RE-SKIPTRACE REVIEW note.")
        done = []
        for t in e["tasks"]:
            # `completed` is READ-ONLY on the task PATCH (200, stores nothing -- probed
            # live 2026-09-15); only POST .../complete/ completes. outcome_notes IS
            # writable through PATCH and lands.
            st1, resp1 = api.write("PATCH", f"/api/internal/task/{t['uuid']}/",
                                   {"outcome_notes": outcome})
            st, resp = api.write("POST", f"/api/internal/task/{t['uuid']}/complete/", {})
            done.append({"uuid": t["uuid"], "title": t["title"], "notes_status": st1,
                         "status": st, "resp": str(resp)[:120] if st != 200 else ""})
        log["steps"]["tasks"] = done
        if any(x["status"] != 200 for x in done):
            log["error"] = "task complete POST failed"
    return log


def verify_one(api: Api, uuid: str, e: dict, complete_tasks: bool) -> list[str]:
    bad = []
    st, full = api.get(f"/api/internal/property/{uuid}/")
    if st != 200:
        return [f"detail read {st}"]
    tags = tag_names(full.get("tags"))
    for t in e["tags_add"]:
        if t not in tags:
            bad.append(f"tag {t!r} missing")
    live_status = full.get("status")
    if e.get("status_to"):
        if live_status != e["status_to"]:
            bad.append(f"status {live_status!r} != {e['status_to']!r}")
    elif status_key(live_status) != status_key(e["status_before"]):
        bad.append(f"status changed unexpectedly to {live_status!r}")
    if e.get("owner_block"):
        o = full.get("owner") or {}
        got = ((o.get("first_name") or "").lower(), (o.get("last_name") or "").lower())
        want = (e["owner_block"]["first_name"].lower(), e["owner_block"]["last_name"].lower())
        if got != want:
            bad.append(f"owner {got} != {want}")
        if full.get("type") != "clean":
            bad.append(f"record type still {full.get('type')!r}")
    m = pinned_note_of_family(api, uuid, REVIEW_FAMILY)
    if not m:
        bad.append(f"no pinned {REVIEW_FAMILY!r} note")
    elif not (m.get("message") or "").startswith(e["note"].splitlines()[0]):
        bad.append("pinned review note is not this run's")
    if complete_tasks and e.get("tasks"):
        for t in e["tasks"]:
            st, tk = api.get(f"/api/internal/task/{t['uuid']}/")
            if st != 200 or not tk.get("completed"):
                bad.append(f"task {t['title']!r} not completed")
    return bad


def cmd_push(a) -> int:
    plan = jload(PLAN, {})
    if not plan:
        say("run `plan` first")
        return 2
    targets = {u: e for u, e in plan.items() if e["phase"] == a.phase}
    if a.only:
        keep = set(a.only.split(","))
        targets = {u: e for u, e in targets.items() if u in keep}
    if a.probe:
        if a.probe not in targets:
            say(f"--probe {a.probe}: not in the {a.phase} plan")
            return 2
        targets = {a.probe: targets[a.probe]}
    if not targets:
        say("nothing to push")
        return 0
    if a.commit and not a.probe and not PROBE_OK.exists() and not a.force:
        say("Gate: no successful probe on record. Run `push --probe <uuid>` first.")
        return 2
    if not (a.probe or a.commit):
        say(f"dry run: {len(targets)} record(s) in phase {a.phase}, nothing sent")
        for u, e in targets.items():
            say(f"  {e['street']:28} {e['action']:17} -> {e['status_to'] or '(keep)'} "
                f"+{e['tags_add']} owner_set={e.get('owner_set')} tasks={len(e['tasks'])}")
        return 0

    api = Api()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = RUN / f"backup_{stamp}.json"
    push_log = jload(PUSH_LOG, {})
    backup = {}
    done = errors = 0
    for u, e in targets.items():
        if u in push_log and not push_log[u].get("error") and not a.probe and not a.force:
            continue
        # live pre-write snapshot: status, tags, owner block, task state
        st, d = api.get(f"/api/internal/property/{u}/")
        backup[u] = {"street": e["street"], "status": d.get("status"),
                     "tags": tag_names(d.get("tags")), "tags_add": e["tags_add"],
                     "owner_block": owner_block(d) if e.get("owner_block") else None,
                     "tasks": [t["uuid"] for t in e.get("tasks") or []] if a.complete_tasks else []}
        jsave(backup_path, backup)          # lands BEFORE the write, always
        log = push_one(api, u, e, a.complete_tasks)
        push_log[u] = log
        jsave(PUSH_LOG, push_log)
        if log.get("error"):
            errors += 1
            say(f"  ERROR {e['street']}: {log['error']}")
        else:
            done += 1
            say(f"  ok    {e['street']}: {e['action']} -> {e['status_to'] or '(keep)'}"
                f" note={log['steps'].get('note', {}).get('status')} "
                f"pin={log['steps'].get('note', {}).get('pin')}")

    if a.probe:
        u, e = next(iter(targets.items()))
        time.sleep(1.5)
        bad = verify_one(api, u, e, a.complete_tasks)
        say(f"\nPROBE {e['street']}: " + ("READ-BACK CLEAN" if not bad else f"FAILED {bad}"))
        if bad:
            return 3
        jsave(PROBE_OK, {"uuid": u, "at": datetime.now().isoformat(timespec="seconds")})
        return 0
    say(f"\npushed {done}, errors {errors}. backup: {backup_path.name}   next: qa")
    return 0 if not errors else 1


def cmd_qa(a) -> int:
    plan = jload(PLAN, {})
    push_log = jload(PUSH_LOG, {})
    pushed = {u: e for u, e in plan.items() if u in push_log and not push_log[u].get("error")}
    api = Api()
    fails = {}
    for u, e in pushed.items():
        bad = verify_one(api, u, e, bool(push_log[u]["steps"].get("tasks")))
        if bad:
            fails[u] = bad
            say(f"  FAIL {e['street']}: {bad}")
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    jsave(RUN / f"qa_{stamp}.json", fails)
    say(f"clean: {len(pushed) - len(fails)}/{len(pushed)}")
    return 0 if not fails else 1


def cmd_undo(a) -> int:
    from obituary_dp_batch import _note_family, board_messages
    backup = jload(Path(a.backup), None)
    if not backup:
        say("backup not found/empty")
        return 2
    api = Api()
    bad = 0
    for u, b in backup.items():
        body = {"status": b["status"]}
        st, _ = api.write("PATCH", f"/api/internal/property/{u}/", body)
        bad += st != 200
        added = [t for t in b["tags_add"] if t not in b["tags"]]
        if added:
            st, _ = api.write("POST", f"/api/internal/property/{u}/remove-tags/", {"tags": added})
            bad += st != 200
        if b.get("owner_block"):
            blk = copy.deepcopy(b["owner_block"])
            # `""` is rejected with 400; null restores a blank name (proven 2026-09-07)
            for k in ("first_name", "last_name"):
                blk[k] = blk[k] or None
            st, _ = api.write("PATCH", f"/api/internal/property/{u}/", {"owner": blk})
            bad += st != 200
        for m in board_messages(api, u):
            if _note_family(m.get("message") or "") == REVIEW_FAMILY and m.get("uuid"):
                st, _ = api.write("DELETE", f"/api/internal/property/{u}/message/{m['uuid']}/", {})
                bad += st not in (200, 202, 204)
        for t in b.get("tasks") or []:
            # POST .../uncomplete/ is the reverse of .../complete/ (OPTIONS 200, probed 2026-09-15)
            st, _ = api.write("POST", f"/api/internal/task/{t}/uncomplete/", {})
            bad += st != 200
            st, _ = api.write("PATCH", f"/api/internal/task/{t}/", {"outcome_notes": None})
            bad += st != 200
        say(f"  undone {b['street']}")
    say(f"undo done, {bad} failures")
    return 0 if not bad else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--research", action="store_true", help="run Enformion for entity owners")
    w = sub.add_parser("push")
    w.add_argument("--probe", default=None, metavar="UUID")
    w.add_argument("--commit", action="store_true")
    w.add_argument("--force", action="store_true")
    w.add_argument("--phase", choices=["main", "post-dp"], default="main")
    w.add_argument("--only", default="", help="comma-separated uuids")
    w.add_argument("--complete-tasks", action="store_true",
                   help="also complete the open re-skip tasks on each record")
    sub.add_parser("qa")
    un = sub.add_parser("undo")
    un.add_argument("backup")
    a = ap.parse_args()
    RUN.mkdir(parents=True, exist_ok=True)
    return {"plan": cmd_plan, "push": cmd_push, "qa": cmd_qa, "undo": cmd_undo}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
