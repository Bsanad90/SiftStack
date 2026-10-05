"""Repair DP records whose "owner did die" verdict the namesake audit rated LIKELY_FALSE.

The Zimmerman path (8131 Turn Loop Rd, 2026-09-18), scripted so it can run on a cohort:

  1. plan files FIRST (they are the source of truth; a `push --force` from any session
     re-sends them): back up the run dir's stage_c_research / ranked_records / push_plan /
     trestle_cache, then rewrite the finding's verdict to a presumed-alive / spouse-died one
     that carries the evidence, stash the original under `namesake_correction`, put the
     dead same-name relative on `drop_names`, drop the obituary-injected relatives (they are
     the NAMESAKE's family) and give a re-rooted record its owner's phones back;
  2. price the owner's (and any co-owner's) unscored numbers with Trestle;
  3. rebuild ONLY the repaired records through the real `cmd_build` (temp dir with the
     filtered inputs), splice them into push_plan.json, carry the cohort tags (FTM / Priority
     1) over, and edit the note so a dialer sees the CORRECTION first;
  4. push (`obituary_dp_batch.py --run-dir <dir> push --keys-file ... --force --commit`),
     which edits the DP board note in place and swaps the relatives' numbers for the owner's;
  5. `untag`: POST remove-tags `Deceased Owner` (the tag PATCH is merge-only), read back;
  6. QA with obituary_dp_qa.py --keys-file.

    python src/scripts/dp_namesake_repair.py plan                      # dry run, writes the review only
    python src/scripts/dp_namesake_repair.py apply --commit            # steps 1-3 (bills Trestle)
    python src/scripts/dp_namesake_repair.py untag --commit            # step 5
    python src/scripts/dp_namesake_repair.py undo --backup-stamp <stamp>

Selection: LIKELY_FALSE rows of the audit sheet whose plan_group is `deceased` (the 23 pushed
live as `Deceased Owner`), or `--keys`. Nothing here touches the account except `untag`.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]
from dpd.lane_tags import apply_lane_rule, split_lists  # noqa: E402

_spec = importlib.util.spec_from_file_location("odb", ROOT / "src" / "scripts" / "obituary_dp_batch.py")
odb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(odb)

AUDIT_DIR = ROOT / "output" / "namesake_audit"
DEFAULT_SHEET = AUDIT_DIR / "owner_died_namesake_audit_20260918_free.csv"
STAMP = datetime.now().strftime("%Y%m%dT%H%M%S")
TODAY = datetime.now().strftime("%Y-%m-%d")
PLAN_FILES = ("stage_c_research.json", "ranked_records.json", "push_plan.json", "trestle_cache.json")
# Tags cmd_build assigns from the verdict; anything else on the old plan record is a cohort
# tag (FTM, Priority 1) and is carried over.
BUILD_TAGS = {odb.TAG_DP, odb.TAG_DECEASED, odb.TAG_ALIVE, odb.TAG_UNVERIFIED, odb.TAG_PRESUMED,
              odb.TAG_TRUST, odb.TAG_SIGNERS_DARK, odb.TAG_REACH_ONLY, odb.TAG_NO_NUMBERS}
_MONTH_TAG_RE = re.compile(r"^(Deep Prospected|DP No Numbers) \d{2}/\d{4}$")

# Per-record facts the audit sheet cannot carry (hand review 2026-09-18 of the 23 findings).
#   shape        NAMESAKE (default) | RELATIVE (the decedent IS a same-name SmartSkip relative)
#                | SPOUSE (the decedent is the owner's spouse; owner alive)
#   drop         same-name relatives who ARE the decedent -> drop_names ("Predeceased, do not call")
#   co_owner     relatives on title with the owner (tax roll / trust title) -> loaded as REL slots
#   extra_load   relatives to load with a caption (possible owner of record)
#   restore_subject_phones  the re-root moved the owner's phones to decedent_from_smartskip
#   remove_rels  obituary-injected relatives to remove (the owner himself, listed as a survivor)
#   spouse_word  husband/wife for the SPOUSE verdict
#   owner_label  what to call the owner in the note when SmartSkip left the first name blank
OVERRIDES: dict[str, dict] = {
    "5908verandadr|22152": {"shape": "RELATIVE", "drop": ["CALVIN MORRISON"]},
    "10930ravenwooddr|20111": {"shape": "RELATIVE", "drop": ["HUBERT BENFIELD"]},
    "3330nleisureworldblvd5102|20906": {"shape": "RELATIVE", "drop": ["PHILIMON CAVASILIOS"]},
    "7911wintersetave|21208": {"shape": "RELATIVE", "drop": ["JULIAN GOLDBERG"]},
    "3112embreyct|21701": {
        "shape": "RELATIVE", "drop": ["JOHN STUSH"],
        "owner_label": "AUDREY RAYFIELD (surviving trustee of the Stush Rayfield Family Revocable Trust)",
        "verdict": ("owner presumed alive (the obituary is John Paul Stush, 87, DOD 2026-08-02 - the trust's "
                    "OTHER named settlor; it names AUDREY RAYFIELD as his survivor, and she is the SmartSkip "
                    "subject at this address, 76, and the trustee to call)")},
    "3641jeffersonpike|21755": {"shape": "SPOUSE", "spouse_word": "husband"},
    "7801heathertonln|20854": {"shape": "SPOUSE", "spouse_word": "wife", "remove_rels": ["David Pincus"]},
    "4126hayesstne|20019": {
        "co_owner": {"REGINALD FREEMAN": "Husband - CO-OWNER on the DC tax roll (FREEMAN, REGINALD L) "
                                         "and the CRM owner name; the SmartSkip household head here is "
                                         "Yolanda L Freeman, 60, also on the roll"}},
    "2715thstne|20002": {"co_owner": {"OSTELL JENNINGS": "Parent - CO-OWNER on the DC tax roll (JENNINGS, OSTELL)"}},
    "13704holtonpl|20151": {"co_owner": {"RADHA RAMASWAMY": "Co-trustee named in the title (Ramaswamy Radha Tr)"}},
    "675estnwapt620|20004": {
        "restore_subject_phones": True,
        "co_owner": {"MARY MCKAY": "Spouse - CO-OWNER on the DC tax roll (MCKAY, MARY)"},
        "extra_load": {"KENNETH K MCKAY": "Child, 33 - carries the tax-roll owner's exact name (MCKAY, KENNETH K); "
                                          "may be the owner of record, ask"}},
    "56648thplne|20019": {"restore_subject_phones": True},
}


def say(m: str) -> None:
    print(m, flush=True)


def _abs(run: str) -> Path:
    p = Path(run)
    return p if p.is_absolute() else ROOT / p


def load_rows(sheet: Path, keys: list[str] | None) -> list[dict]:
    rows = list(csv.DictReader(open(sheet, encoding="utf-8-sig")))
    if keys:
        want = set(keys)
        sel = [r for r in rows if r["key"] in want]
        missing = want - {r["key"] for r in sel}
        if missing:
            say(f"STOP: keys not in the audit sheet: {sorted(missing)}")
            sys.exit(2)
        return sel
    return [r for r in rows if r["recommendation"] == "LIKELY_FALSE" and r["plan_group"] == "deceased"]


# ───────────────────────── verdict text ─────────────────────────

def _dec_desc(r: dict, f: dict) -> str:
    dec = f.get("decedent_name") or r["decedent_name"]
    bits = [dec]
    if r.get("obit_age"):
        bits.append(f"{r['obit_age']} at death")
    elif r.get("obit_birth_year"):
        bits.append(f"born {r['obit_birth_year']}")
    if r.get("obit_city"):
        bits.append(f"of {r['obit_city']}")
    if f.get("dod"):
        bits.append(f"DOD {f['dod']}")
    return ", ".join(bits)


def _owner_desc(r: dict) -> str:
    out = []
    if r.get("owner_age"):
        out.append(f"the owner here is {r['owner_age']} per SmartSkip")
    if r.get("oor_owners"):
        out.append(f"tax roll owner of record: {r['oor_owners']}")
    return "; ".join(out)


def make_verdict(r: dict, f: dict, ov: dict) -> tuple[str, str]:
    """(verdict, shape). The verdict string is what cmd_score/cmd_build ROUTE on:
    'presumed alive' -> presumed_alive group / TAG_PRESUMED; 'spouse died' + 'owner alive'
    -> owner_alive group / TAG_ALIVE with the decedent-drop gap rule active."""
    shape = ov.get("shape", "NAMESAKE")
    if ov.get("verdict"):
        return ov["verdict"], shape
    dec = _dec_desc(r, f)
    own = _owner_desc(r)
    if shape == "SPOUSE":
        return (f"spouse died ({ov.get('spouse_word', 'spouse')} {dec}); owner alive - "
                f"{r['owner_first']} {r['owner_last']} is the person to call"
                + (f"; {own}" if own else "")), shape
    if shape == "RELATIVE":
        return (f"owner presumed alive (the obituary is a same-name RELATIVE, not the owner: {dec} - "
                f"SmartSkip lists that person among the owner's relatives"
                + (f"; {own}" if own else "") + ")"), shape
    return (f"owner presumed alive (the matched obituary is a NAMESAKE, not the owner: {dec}"
            + (f"; {own}" if own else "") + ")"), shape


# ───────────────────────── plan-file patching ─────────────────────────

def patch_finding(f: dict, verdict: str, shape: str, ov: dict, r: dict) -> dict:
    orig = {k: f.get(k) for k in ("verdict", "decedent_name", "dod", "dod_source", "obituary_url",
                                  "flags", "drop_names", "evidence", "researched_at")}
    f["namesake_correction"] = {"corrected_at": TODAY, "audit_score": r.get("score"),
                                "audit_reasons": r.get("reasons"), "shape": shape, "original": orig}
    f["verdict"] = verdict
    flags = [x for x in (f.get("flags") or []) if not x.startswith("dod_")]
    flags.append(f"namesake_obituary_corrected_{TODAY.replace('-', '')}")
    if shape == "SPOUSE":
        # A real decedent brief: the spouse did die. Keep name/DOD/URL; the note prints them.
        # The OWNER is a survivor in that obituary; cmd_build recomputes obit_only_names from
        # `survivors`, so take the owner out or the note lists them as "not in SmartSkip".
        owner = f"{r['owner_first']} {r['owner_last']}".strip()
        def _nm(x):
            return x if isinstance(x, str) else (x.get("name") or "")
        f["survivors"] = [x for x in (f.get("survivors") or []) if not odb.names_match(_nm(x), owner)]
        flags.append("owner_alive_spouse_deceased")
    else:
        # Not the owner, not the owner's family: the obituary fields would print as if they
        # were. Blank them (the originals sit in namesake_correction) and record the evidence.
        f["evidence"] = (f"{TODAY} correction: {verdict}. Prior match "
                         f"{orig['decedent_name']} ({orig['obituary_url']}) was a namesake.")
        f["decedent_name"] = ""
        f["dod"] = ""
        f["dod_source"] = ""
        f["obituary_url"] = ""
        f["survivors"] = []
        f["preceded"] = []
        f["confirmed_rels"] = []
        f["obit_only_names"] = []
        flags.append("owner_alive_by_namesake_evidence")
    drop = list(dict.fromkeys((f.get("drop_names") or []) + list(ov.get("drop") or [])))
    f["drop_names"] = drop
    f["flags"] = flags
    f["researched_at"] = datetime.now().isoformat(timespec="seconds")
    return f


def patch_ranked(rk: dict, shape: str, ov: dict, rec: dict) -> dict:
    note = {}
    if ov.get("restore_subject_phones"):
        src = rk.get("decedent_from_smartskip") or {}
        rk["subject"]["phones"] = list(src.get("phones") or [])
        note["subject_phones_restored_from_decedent_from_smartskip"] = [p["number"] for p in rk["subject"]["phones"]]
    removed = []
    owner = f"{rec.get('ss_first', '')} {rec.get('ss_last', '')}".strip()
    keep = []
    for rel in rk["rels"]:
        src = rel.get("source") or ""
        nm = rel.get("name") or ""
        if nm in (ov.get("remove_rels") or []):
            removed.append(nm)
            continue
        # Obituary-injected relatives are the DECEDENT's survivors. On a namesake record that
        # is a stranger's family; on a spouse record the owner himself appears as "husband".
        if src.startswith("obituary") and (shape != "SPOUSE" or odb.names_match(nm, owner)):
            removed.append(nm)
            continue
        keep.append(rel)
    if removed:
        rk["rels"] = keep
        note["removed_obituary_rels"] = removed
    if note:
        rk["namesake_repair"] = {"at": TODAY, **note}
    return rk


# ───────────────────────── Trestle ─────────────────────────

def numbers_to_score(rk: dict, ov: dict, cache: dict) -> list[str]:
    want = [p["number"] for p in rk["subject"]["phones"]]
    names = set(ov.get("co_owner") or {}) | set(ov.get("extra_load") or {})
    for rel in rk["rels"]:
        if rel.get("name") in names:
            want += [p["number"] for p in rel["phones"]]
    return sorted({n for n in want if n and (n not in cache or cache[n].get("error"))})


def score_numbers(cache: dict, numbers: list[str], commit: bool) -> int:
    if not numbers:
        return 0
    api_key = os.environ.get("TRESTLE_PAID_API_KEY") or os.environ.get("TRESTLE_API_KEY", "")
    if not api_key:
        say("STOP: no TRESTLE_PAID_API_KEY / TRESTLE_API_KEY")
        sys.exit(2)
    if not commit:
        say(f"  trestle: {len(numbers)} numbers would be scored (~${0.015 * len(numbers):.2f})")
        return 0
    import phone_validator as pv  # noqa: E402
    import obituary_dp_run as dpr  # noqa: E402
    for n in numbers:
        d = pv.call_trestle(n, api_key)
        if not isinstance(d, dict) or (d.get("error") and not d.get("phone_number")):
            cache[n] = {"error": str((d or {}).get("error") or d)[:120]}
        else:
            score = d.get("activity_score")
            cache[n] = {"activity_score": score, "tier": dpr.tier_for(score),
                        "line_type": d.get("line_type"), "is_valid": d.get("is_valid"),
                        "carrier": d.get("carrier") or "",
                        "litigator": ((d.get("add_ons") or {}).get("litigator_checks") or {}).get(
                            "phone.is_litigator_risk")}
        time.sleep(0.12)
    return len(numbers)


# ───────────────────────── build + splice ─────────────────────────

def build_records(run: Path, keys: list[str]) -> dict[str, dict]:
    """Run the real cmd_build on a temp dir holding only `keys`; return its plan records."""
    records = odb.jload(run / "records.json", {})
    ranked = odb.jload(run / "ranked_records.json", {})
    research = odb.jload(run / "stage_c_research.json", {}) or {}
    tmp = Path(tempfile.mkdtemp(prefix="dp_namesake_build_"))
    odb.jdump(tmp / "records.json", {k: records[k] for k in keys})
    odb.jdump(tmp / "ranked_records.json", {k: ranked[k] for k in keys})
    odb.jdump(tmp / "stage_c_research.json", {"generated_at": research.get("generated_at"),
                                              "findings": [f for f in research.get("findings", []) if f["record"] in keys]})
    shutil.copy(run / "trestle_cache.json", tmp / "trestle_cache.json")
    a = argparse.Namespace(run_dir=str(tmp), max_rel_field_phones=3, dial_cap=odb.DIAL_LIST_CAP,
                           max_rel_slots=odb.MAX_REL_SLOTS, include_phoneless=False, extra_tag=[])
    rc = odb.cmd_build(a)
    if rc:
        say(f"STOP: cmd_build returned {rc} in {tmp}")
        sys.exit(2)
    out = odb.jload(tmp / "push_plan.json", {}).get("records", {})
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def _kept(rel: dict, cache: dict, seen: set) -> list[tuple[dict, dict]]:
    kept = []
    for p in rel["phones"]:
        t = cache.get(p["number"], {})
        if odb._keep_number(t)[0] and p["number"] not in seen:
            seen.add(p["number"])
            kept.append((p, t))
    kept.sort(key=odb._tier_sort_key)
    return kept


def add_rel_slot(p: dict, rel: dict, caption: str, cache: dict) -> str | None:
    """Load one relative into the next free REL slot of plan record `p` (co-owner / possible
    owner of record). Returns the WHO TO CALL line, or None when the relative has no number."""
    seen = {ph["number"] for ph in p["phones"]}
    kept = _kept(rel, cache, seen)
    if not kept:
        # The owner rule on shared lines: a household number is claimed by the owner first,
        # so a co-owner on the same lines has nothing of their own to load. Still named.
        age = f", {rel['age']}" if rel.get("age") else ""
        return (f"REL - {rel['name']} - {caption}{age} - no separate number "
                f"(shares the owner's lines above)")
    used = {int(m.group(1)) for k in p["custom_fields"] for m in [odb._REL_NAME_RE.match(k)] if m}
    n = (max(used) + 1) if used else 1
    p["custom_fields"][f"REL{n}: Full Name"] = (rel["name"] or "").upper()
    for m, (ph, _t) in enumerate(kept[:3], 1):
        p["custom_fields"][f"REL{n}: Phone {m}"] = ph["number"]
    word = odb.REL_TAG_WORD.get(rel.get("canon_rel") or "")
    tiers = []
    for m, (ph, t) in enumerate(kept, 1):
        if len(p["phones"]) >= odb.DIAL_LIST_CAP:
            break
        tags = [f"Rel{n}.{m}"] + ([word] if word else []) + ([t["tier"]] if t.get("tier") and t["tier"] != "Unknown" else [])
        p["phones"].append({"number": ph["number"], "type": odb._api_phone_type(ph.get("type"), t.get("line_type")),
                            "tags": tags, "status": "UNKNOWN", "is_connected": True,
                            "rel_n": n, "rel_name": rel["name"], "tier": t.get("tier")})
        tiers.append(t.get("tier") or "?")
    return f"REL{n} - {rel['name']} - {caption} - {' / '.join(tiers)}"


def _strip_from_held_line(note: str, names: list[str]) -> str:
    """Remove loaded relatives from the 'family found but NOT loaded' line."""
    out = []
    for line in note.split("\n"):
        if line.startswith("Owner is alive and signs alone;"):
            head, _, tail = line.partition(": ")
            items = [x for x in tail.split("; ") if not any(x.upper().startswith(nm.upper()) for nm in names)]
            if not items:
                continue
            line = f"{head}: " + "; ".join(items)
        out.append(line)
    return "\n".join(out)


def finish_note(p: dict, rec: dict, r: dict, f: dict, rk: dict, shape: str, ov: dict, rel_lines: list[str]) -> str:
    lines = p["note"].split("\n")
    orig = (f.get("namesake_correction") or {}).get("original") or {}
    prior_dec = orig.get("decedent_name") or r["decedent_name"]
    prior_dod = orig.get("dod") or r.get("dod") or "?"
    prior_url = orig.get("obituary_url") or r.get("obituary_url") or ""
    if shape == "SPOUSE":
        corr = [f"CORRECTED {TODAY} - the earlier DP note on this record said the OWNER died. The obituary "
                f"is the owner's {ov.get('spouse_word', 'spouse')}; the owner {rec['owner_first']} "
                f"{rec['owner_last']} is ALIVE and is the person to call.",
                "Condolences for the spouse are appropriate; do not treat the owner as deceased."]
    else:
        corr = [f"CORRECTED {TODAY} - the earlier DP note on this record was WRONG: the obituary it cited "
                f"({prior_dec}, DOD {prior_dod}) is a DIFFERENT PERSON. Nobody on this record is confirmed dead.",
                "DO NOT open with condolences. Pitch as a living owner."]
    lines[1:1] = corr
    # SmartSkip left the first name blank on most of these: "OWNER:  Morrison" -> the CRM name.
    label = ov.get("owner_label") or f"{rec['owner_first']} {rec['owner_last']}".strip()
    for i, ln in enumerate(lines):
        if ln.startswith("OWNER: "):
            lines[i] = f"OWNER: {label} - presumed alive; the owner is the person to call"
            break
    # The builder's SIGNERS line is the intestacy basis "as if the owner had died"; on a living
    # owner it tells the dialer the opposite of the truth. Same for the probate MUST VERIFY.
    for i, ln in enumerate(lines):
        if ln.startswith("SIGNERS: "):
            lines[i] = ("SIGNERS: the surviving owner; if title was held jointly the late spouse's estate "
                        "may also have to sign - check the deed" if shape == "SPOUSE"
                        else "SIGNERS: the living owner(s) on title - nobody else can sell")
        if ln.startswith("MUST VERIFY: ") and shape == "SPOUSE":
            lines[i] = ("MUST VERIFY: the owner is alive and on title; deed vesting (joint tenancy passes "
                        "to the survivor, tenancy in common does not)")
    note = "\n".join(lines)
    if rel_lines:
        owner_line = next((ln for ln in lines if ln.startswith("OWNER (")), None)
        if owner_line:
            # co-owners sit UNDER the owner line, not above it
            note = note.replace(owner_line, owner_line + "\n" + "\n".join(rel_lines), 1)
        elif "\nWHO TO CALL\n" in note:
            note = note.replace("\nWHO TO CALL\n", "\nWHO TO CALL\n" + "\n".join(rel_lines) + "\n", 1)
        else:
            # no owner number landed: add the block before NOT LOADED / the tail
            anchor = "\n\nNOT LOADED\n" if "\n\nNOT LOADED\n" in note else "\n\n" + next(
                (ln for ln in lines if ln.startswith(("OBITUARY:", "TITLE HELD BY:", "FLAGS:", "MUST VERIFY:"))), "")
            note = note.replace(anchor, "\n\nWHO TO CALL\n" + "\n".join(rel_lines) + anchor, 1)
        note = _strip_from_held_line(note, [ln.split(" - ")[1] for ln in rel_lines])
    tail = []
    if r.get("oor_owners"):
        tail.append(f"TAX ROLL OWNER OF RECORD: {r['oor_owners']} ({r.get('oor_source') or 'tax roll'})")
    if (rk.get("subject") or {}).get("deceased_flag"):
        tail.append("SmartSkip's own deceased flag is SET on this subject. That flag is unreliable, but "
                    "confirm the owner is alive on first contact before pitching.")
    if shape != "SPOUSE" and prior_url:
        tail.append(f"PRIOR MATCH (namesake, NOT the owner): {prior_dec} - {prior_url}")
    if tail:
        note = note.replace("\nMUST VERIFY:", "\n" + "\n".join(tail) + "\nMUST VERIFY:", 1)
    if len(note) > odb.NOTE_MAX:
        # trim the NOT LOADED lines first, as _note does
        keep, cut = [], False
        for ln in note.split("\n"):
            if ln.startswith(("Owner is alive and signs alone", "Further relatives on file", "In-laws found")) and len(ln) > 160:
                ln = ln[:156].rstrip(" ;,") + " ..."
                cut = True
            keep.append(ln)
        note = "\n".join(keep)
        if len(note) > odb.NOTE_MAX:
            note = note[: odb.NOTE_MAX]
    return note


# ───────────────────────── commands ─────────────────────────

def cmd_plan_apply(a, commit: bool) -> int:
    rows = load_rows(Path(a.sheet), a.keys)
    say(f"{len(rows)} records selected ({'APPLY' if commit else 'PLAN, dry run'})")
    out_dir = AUDIT_DIR / f"repair_{STAMP}"
    out_dir.mkdir(parents=True, exist_ok=True)
    by_run: dict[str, list[dict]] = {}
    for r in rows:
        by_run.setdefault(r["run_dir"], []).append(r)

    review, qa_rows, cmds = [], [], []
    total_scored = 0
    for run_s, rrows in by_run.items():
        run = _abs(run_s)
        research_doc = odb.jload(run / "stage_c_research.json", {}) or {}
        findings = {f["record"]: f for f in research_doc.get("findings", [])}
        ranked = odb.jload(run / "ranked_records.json", {})
        plan_doc = odb.jload(run / "push_plan.json", {})
        plan = plan_doc.get("records", {})
        records = odb.jload(run / "records.json", {})
        cache = odb.jload(run / "trestle_cache.json", {})
        uuids = {}
        for line in (run / "push_log.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("uuid"):
                uuids[e["key"]] = e["uuid"]

        if commit:
            bdir = run / f"namesake_repair_backup_{STAMP}"
            bdir.mkdir(exist_ok=True)
            for fn in PLAN_FILES:
                shutil.copy2(run / fn, bdir / fn)
            say(f"\n== {run_s}: backup -> {bdir.name}")
        else:
            say(f"\n== {run_s}")

        keys = [r["key"] for r in rrows]
        shapes, ovs, old_tags = {}, {}, {}
        to_score: list[str] = []
        for r in rrows:
            k = r["key"]
            f, rk, rec = findings[k], ranked[k], records[k]
            ov = OVERRIDES.get(k, {})
            if f.get("verdict") != "owner did die":
                say(f"  SKIP {k}: finding verdict is already {f.get('verdict')!r}")
                keys.remove(k)
                continue
            verdict, shape = make_verdict(r, f, ov)
            shapes[k], ovs[k] = shape, ov
            old_tags[k] = list(plan.get(k, {}).get("tags") or [])
            patch_finding(f, verdict, shape, ov, r)
            patch_ranked(rk, shape, ov, rec)
            to_score += numbers_to_score(rk, ov, cache)
            say(f"  {k}: {shape} -> {verdict[:110]}...")
            if ov.get("drop"):
                say(f"      drop (dead namesake relative): {ov['drop']}")
            if (rk.get("namesake_repair") or {}).get("removed_obituary_rels"):
                say(f"      removed obituary-injected rels: {rk['namesake_repair']['removed_obituary_rels']}")
            if ov.get("restore_subject_phones"):
                say(f"      subject phones restored: {[p['number'] for p in rk['subject']['phones']]}")
        to_score = sorted(set(to_score))
        say(f"  trestle: {len(to_score)} unscored numbers on these records (~${0.015 * len(to_score):.2f})")
        if not keys:
            continue
        if commit:
            n = score_numbers(cache, to_score, commit=True)
            total_scored += n
            odb.jdump(run / "trestle_cache.json", cache)
            odb.jdump(run / "stage_c_research.json", research_doc)
            odb.jdump(run / "ranked_records.json", ranked)
            built = build_records(run, keys)
        else:
            # dry run: build from the patched in-memory state via a temp copy of the run dir inputs
            tmp_run = Path(tempfile.mkdtemp(prefix="dp_namesake_plan_"))
            odb.jdump(tmp_run / "records.json", records)
            odb.jdump(tmp_run / "ranked_records.json", ranked)
            odb.jdump(tmp_run / "stage_c_research.json", research_doc)
            odb.jdump(tmp_run / "trestle_cache.json", cache)
            (tmp_run / "push_log.jsonl").write_text("", encoding="utf-8")
            built = build_records(tmp_run, keys)
            shutil.rmtree(tmp_run, ignore_errors=True)

        for k in keys:
            p = built[k]
            r = next(x for x in rrows if x["key"] == k)
            rec, rk, f, ov, shape = records[k], ranked[k], findings[k], ovs[k], shapes[k]
            # cohort tags (FTM, Priority 1) ride along; month-stamped DP tags are rebuilt
            for t in old_tags[k]:
                if t not in BUILD_TAGS and not _MONTH_TAG_RE.match(t) and t not in p["tags"]:
                    p["tags"].append(t)
            # ...except that an obituary record never carries Priority 1 (Basem 2026-10-01):
            # the carried-over set goes through the same lane rule as a fresh build.
            p["tags"] = apply_lane_rule(split_lists(rec.get("lists")), p["tags"])
            rel_lines = []
            for nm, cap in {**(ov.get("co_owner") or {}), **(ov.get("extra_load") or {})}.items():
                rel = next((x for x in rk["rels"] if (x.get("name") or "").upper() == nm.upper()), None)
                if rel is None:
                    say(f"  ! {k}: relative {nm!r} not in ranked rels")
                    continue
                line = add_rel_slot(p, rel, cap, cache)
                if line:
                    rel_lines.append(line)
                else:
                    say(f"  ! {k}: relative {nm!r} has no callable number")
            if p["phones"] and odb.TAG_NO_NUMBERS in p["tags"]:
                p["tags"].remove(odb.TAG_NO_NUMBERS)
            p["note"] = finish_note(p, rec, r, f, rk, shape, ov, rel_lines)
            p["namesake_repair"] = {"at": TODAY, "shape": shape, "prior_group": "deceased",
                                    "prior_tags": old_tags[k]}
            plan[k] = p
            uuid = uuids.get(k, "")
            review.append({"run_dir": run_s, "key": k, "street": p["street"], "zip": p["zip"], "uuid": uuid,
                           "shape": shape, "group": p["group"], "tags": ", ".join(p["tags"]),
                           "phones": len(p["phones"]), "owner_phones": sum(1 for ph in p["phones"] if ph["rel_n"] == 0),
                           "rel_slots": sum(1 for c in p["custom_fields"] if c.endswith("Full Name")),
                           "verdict": p["verdict"], "note_chars": len(p["note"]), "note": p["note"]})
            qa_rows.append({"key": k, "dp_batch": run_s, "uuid": uuid})
            say(f"  built {k}: group={p['group']} phones={len(p['phones'])} "
                f"(owner {review[-1]['owner_phones']}) rel_slots={review[-1]['rel_slots']} tags={p['tags']} note={len(p['note'])}ch")
        if commit:
            plan_doc["records"] = plan
            plan_doc["namesake_repair"] = {"at": datetime.now().isoformat(timespec="seconds"), "keys": keys,
                                           "backup": f"namesake_repair_backup_{STAMP}"}
            odb.jdump(run / "push_plan.json", plan_doc)
            say(f"  push_plan.json updated ({len(keys)} records)")
        kf = out_dir / f"keys_{run.name}.json"
        json.dump(keys, open(kf, "w", encoding="utf-8"), indent=1)
        no_num = "--include-no-numbers " if any(not built[k]["phones"] for k in keys) else ""
        cmds.append(f'python -u src/scripts/obituary_dp_batch.py --run-dir "{run_s}" push --route api '
                    f'--keys-file "{kf.relative_to(ROOT)}" {no_num}--force --commit')

    odb.write_csv(out_dir / "repair_review.csv", review, list(review[0].keys()) if review else ["key"])
    json.dump(qa_rows, open(out_dir / "qa_keys.json", "w", encoding="utf-8"), indent=1)
    (out_dir / "push_commands.txt").write_text("\n".join(cmds) + "\n", encoding="utf-8")
    say(f"\nreview: {out_dir / 'repair_review.csv'}\nqa keys: {out_dir / 'qa_keys.json'}\n"
        f"push commands: {out_dir / 'push_commands.txt'}")
    if commit:
        say(f"Trestle numbers scored: {total_scored} (~${0.015 * total_scored:.2f})")
        say("NEXT: probe one record with `push --probe <key> --force --commit`, then the commands above, "
            "then `dp_namesake_repair.py untag --commit`, then obituary_dp_qa.py --keys-file qa_keys.json")
    return 0


def cmd_untag(a) -> int:
    rows = json.load(open(a.qa_keys, encoding="utf-8"))
    api = odb.WriteApi()
    log_path = Path(a.qa_keys).parent / f"untag_{STAMP}.jsonl"
    bad = 0
    with open(log_path, "a", encoding="utf-8") as lf:
        for it in rows:
            uuid = it.get("uuid")
            if not uuid:
                say(f"  {it['key']}: no uuid, skipped")
                bad += 1
                continue
            st, full = api.get(f"/api/internal/property/{uuid}/")
            if st != 200:
                say(f"  {it['key']}: fetch {st}")
                bad += 1
                continue
            tags = [t.get("name") if isinstance(t, dict) else str(t) for t in (full.get("tags") or [])]
            entry = {"key": it["key"], "uuid": uuid, "tags_before": tags, "at": datetime.now().isoformat(timespec="seconds")}
            new_ok = any(t in tags for t in (odb.TAG_PRESUMED, odb.TAG_ALIVE))
            if odb.TAG_DECEASED not in tags:
                entry["result"] = "already absent"
            elif not new_ok:
                entry["result"] = "REFUSED: corrected tag not on the record yet (push first)"
                bad += 1
            elif not a.commit:
                entry["result"] = "would remove"
            else:
                st2, resp = api.write("POST", f"/api/internal/property/{uuid}/remove-tags/", {"tags": [odb.TAG_DECEASED]})
                time.sleep(0.8)
                st3, after = api.get(f"/api/internal/property/{uuid}/")
                tags_after = [t.get("name") if isinstance(t, dict) else str(t) for t in (after.get("tags") or [])]
                entry.update({"remove_status": st2, "resp": str(resp)[:200], "tags_after": tags_after})
                entry["result"] = "removed" if odb.TAG_DECEASED not in tags_after else "STILL PRESENT"
                if entry["result"] != "removed":
                    bad += 1
            lf.write(json.dumps(entry, ensure_ascii=False) + "\n")
            say(f"  {it['key']}: {entry['result']}  tags={entry.get('tags_after', tags)}")
    say(f"log: {log_path}; problems: {bad}")
    return 1 if bad else 0


def cmd_undo(a) -> int:
    n = 0
    for run in sorted({_abs(r["run_dir"]) for r in load_rows(Path(a.sheet), a.keys)}):
        bdir = run / f"namesake_repair_backup_{a.backup_stamp}"
        if not bdir.exists():
            say(f"  {run}: no backup {bdir.name}")
            continue
        for fn in PLAN_FILES:
            if (bdir / fn).exists():
                shutil.copy2(bdir / fn, run / fn)
                n += 1
        say(f"  {run}: restored from {bdir.name}")
    say(f"{n} files restored (plan files only; the account is NOT touched by undo)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sheet", default=str(DEFAULT_SHEET))
    ap.add_argument("--keys", nargs="*", help="plan keys to repair (default: all LIKELY_FALSE deceased-group rows)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    s = sub.add_parser("apply")
    s.add_argument("--commit", action="store_true")
    s = sub.add_parser("untag")
    s.add_argument("--qa-keys", required=True, help="qa_keys.json written by apply")
    s.add_argument("--commit", action="store_true")
    s = sub.add_parser("undo")
    s.add_argument("--backup-stamp", required=True)
    a = ap.parse_args()
    if a.cmd == "plan":
        return cmd_plan_apply(a, commit=False)
    if a.cmd == "apply":
        if not a.commit:
            return cmd_plan_apply(a, commit=False)
        return cmd_plan_apply(a, commit=True)
    if a.cmd == "untag":
        return cmd_untag(a)
    if a.cmd == "undo":
        return cmd_undo(a)
    return 2


if __name__ == "__main__":
    sys.exit(main())
