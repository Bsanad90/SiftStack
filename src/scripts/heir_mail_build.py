"""Heir mailing list: deep-prospected OBITUARY + REGISTER OF WILLS records, filed since a
date, never mailed before (Basem, 2026-10-06).

Builds on obituary_mail_consolidate.py (the 2026-09-15/17 obituary mailing tool) and reuses
its pieces -- the household "The Smiths" piece, one piece per ranked signer at the signer's
own SmartSkip address, the previously-mailed screen (lists + CRM counter + Drive sent files)
and the one-letter-per-door merge -- but changes the COHORT and adds two gates:

  * cohort = Obituary list / obituary tags  UNION  the `Register of Wills Probates` list,
    intersected with the deep-prospected index (ranked_records.json across the DP run dirs,
    BACKUP copies excluded -- a pre-repair backup can out-score the repaired file);
  * FILED SINCE <date>: an obituary record by its `last_obituary_date` (research DOD as
    the fallback), a Register of Wills record by the filing date in our own pull ledgers
    (`output/md_row_ledger.json`, `output/dc_probate_ledger.json`, joined on the property
    address) with the pull-date tags (`Register of Wills M.D.YYYY`, `VER YYYYMMDD ROW`,
    `probate_MMDD`) as the fallback. The CRM holds NO filing date for these records
    (`Probate Open Date` is empty on every one read 2026-10-06);
  * CERTAIN HEIRS ONLY: the DP research verdict must be "owner did die" at high / court
    record confidence, the ranked signer basis must come from labelled family (never the
    "no labelled immediate family, best-scoring unlabelled" fallback), and at least one
    ranked signer must carry a mailing address.

Basem's three rules are the previously-mailed screen of the base tool, unchanged:
  1. never mailed before   -> `directmail_attempts` > 0 drops the property;
  2. not on Inheritance / Probate / Divorce -> list membership drops it, counter or not
     (`Register of Wills Probates` membership only WITH an attempt);
  3. not on Drive          -> any hit in the weekly SENT files (`*_Mailings.xlsx`) drops it.
A dropped property loses its household piece AND every heir piece.

Nothing here writes to DataSift.

Usage:
    python src/scripts/heir_mail_build.py index                 # offline
    python src/scripts/heir_mail_build.py pull [--threads 2]    # live read, resumable
    python src/scripts/heir_mail_build.py build --filed-since 2026-07-01
    python src/scripts/heir_mail_build.py qa --sample 8

Deliverables under output/heir_mail_<run>/:
    heir_mailing.csv          one row per mailpiece (household + each signer)
    heir_mailing_grouped.csv  one row per door (heirs sharing an address on one envelope)
    excluded.csv              every record / piece left out, with the reason
    audit.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import os
import sys
import threading
import time
from argparse import Namespace
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

import mailed_history  # noqa: E402
import obituary_mail_consolidate as omc  # noqa: E402
from dpd_lane_assignee_report import count  # noqa: E402
from dpd_lane_phone_score import Api, jload, jsave, say  # noqa: E402
from obituary_dp_batch import sift_key, zip5  # noqa: E402

RUN = os.environ.get("HEIR_MAIL_RUN", "20261006")   # a re-screen writes a new dir: HEIR_MAIL_RUN=YYYYMMDD
OUT = ROOT / "output" / f"heir_mail_{RUN}"
DP_INDEX = OUT / "dp_index.json"
COHORT = OUT / "cohort_thin.json"
RECORDS = OUT / "records.json"
RECORDS_IN_SCOPE = OUT / "records_in_scope.json"
PIECES_CSV = OUT / "mailing_pieces_base.csv"       # the base tool's file (fixed columns)
GROUPED_BASE_CSV = OUT / "mailing_grouped_base.csv"
FINAL_CSV = OUT / "heir_mailing.csv"
FINAL_GROUPED_CSV = OUT / "heir_mailing_grouped.csv"
EXCLUDED_CSV = OUT / "excluded.csv"
AUDIT = OUT / "audit.json"
PREFILTER_CSV = OUT / "excluded_prefilter.csv"

MD_LEDGER = ROOT / "output" / "md_row_ledger.json"
DC_LEDGER = ROOT / "output" / "dc_probate_ledger.json"

ROW_LIST_TITLE = "Register of Wills Probates"
OFFSET_CEILING = 10000

# Run-dir path parts that mark a COPY of a ranked file, not the live one. The base tool
# globs every ranked_records.json and keeps the copy with MORE signers, so a pre-repair
# backup (more signers because the false ones were not yet removed) would win.
BACKUP_PARTS = re.compile(r"backup|_pre_|correction", re.I)

# Register of Wills filing-date fallback: our own pull / verified-import tags.
TAG_ROW_DATE = re.compile(r"^Register of Wills (\d{1,2})\.(\d{1,2})\.(\d{4})$", re.I)
TAG_VER_ROW = re.compile(r"^VER (\d{4})(\d{2})(\d{2}) (ROW|DC)$", re.I)
TAG_PROBATE_MMDD = re.compile(r"^probate_(\d{2})(\d{2})$", re.I)
# month-only proxies (first of the month): the month-stamped verified import of the Register
# of Wills pull, and the month the clerk's Register of Wills list was bought (the IDI lists
# carry `Register of Wills` + `List Purchased MM/YYYY` and nothing finer)
TAG_VER_ROW_MONTH = re.compile(r"^VER (\d{4})(\d{2}) (ROW|DC)$", re.I)
TAG_LIST_PURCHASED = re.compile(r"^List Purchased (\d{2})/(\d{4})$", re.I)

CERTAIN_VERDICT = "owner did die"
CERTAIN_CONFIDENCE = {"high", "court record"}
UNCERTAIN_BASIS = "no labelled immediate family"

EXTRA_COLS = ["record_kind", "filed_date", "filed_date_source", "verdict", "confidence",
              "signer_basis", "phone_unscored", "footprint", "on_account", "source_list", "drive_source"]

# Deep-prospected batches whose records were NEVER pushed to the account (Basem's
# read-only rule for that batch, 2026-09-29): the Sept Drive monthly inheritance + probate
# lists and Sheet26 estates missing from the account, and the 0930 weekly probate rows.
# Basem 2026-10-06: "make sure they are included". They are synthesised from the run dir
# (records.json + dp_input_export.csv + push_plan.json) and go through the same gates; the
# account-side signals (lists, counter, status) do not exist for them, so the Drive sent
# files are the only previously-mailed check that can fire.
UNPUSHED_RUN_DIRS = ("output/dp_drive_sept_missing_20260929", "output/dp_drive_0930_new")
UNPUSHED_CHECK = OUT / "unpushed_account_check.json"   # key -> {uuids: [...]} (read-only probe)
LIST_KIND = {"inheritance": "inheritance", "probate": "probate",
             "register of wills probates": "row"}
WEEKLY_SRC = re.compile(r"Weekly probate (\d{2})(\d{2})", re.I)
SHEET26_PUB = re.compile(r"pub (\d{2})/(\d{2})/(\d{4})")
MONTHLY_PROXY = "2026-09-01"     # the batch read the Sept 2026 Drive folder's monthly lists

# the 6-county footprint, as the CRM spells the county on the property address (the
# Market Finder label is that spelling: "District of Columbia", "Anne Arundel", ...)
from dpd.jurisdictions import FOOTPRINT  # noqa: E402
FOOTPRINT_COUNTIES = {j.market_finder_label.lower() for j in FOOTPRINT}
MAIL_READY_CSV = OUT / "heir_mailing_mail_ready.csv"
MAIL_READY_COLS = ["piece_no", "decedent_name", "mail_to", "salutation", "addressee_type",
                   "relationship", "mail_street", "mail_city", "mail_state", "mail_zip", "phone_unscored",
                   "property_street", "property_city", "property_state", "property_zip", "county",
                   "footprint", "record_kind", "filed_date", "on_account", "source_list",
                   "flags", "record_uuid"]


# ------------------------------------------------------------------ helpers

def _iso(s: str) -> str:
    """'09/01/2026' / '2026-09-01' / '2026-09-01T..' -> '2026-09-01', else ''."""
    s = (s or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    return m.group(0) if m else ""


def _clean_name(name: str) -> str:
    """'Samuel Hastey,' -> 'Samuel Hastey' (a vendor row's trailing comma survived)."""
    return re.sub(r"[\s,.;]+$", "", (name or "").strip())


def _best_phone(rel: dict) -> str:
    ph = rel.get("phones") or []
    for p in ph:
        if isinstance(p, dict):
            n = p.get("number") or p.get("phone") or ""
        else:
            n = str(p)
        n = re.sub(r"\D", "", n)
        if len(n) == 11 and n.startswith("1"):
            n = n[1:]
        if len(n) == 10:
            return n
    return ""


SPOUSE_RELS = {"spouse", "wife", "husband", "widow", "widower"}
BUILD_INDEX = OUT / "dp_index_build.json"     # in-scope entries, decedent rows removed


def _name_key(name: str) -> tuple[str, str]:
    """(first, last) of a person string, lower-cased, punctuation stripped."""
    toks = [re.sub(r"[^a-z]", "", t.lower()) for t in _clean_name(name).split()]
    toks = [t for t in toks if t and t not in omc._NOT_SURNAME]
    return (toks[0], toks[-1]) if len(toks) >= 2 else ("", toks[0] if toks else "")


def decedent_keys(entry: dict, rec: dict) -> set:
    """The decedent's (first, last) keys: the CRM Deceased fields, the research finding,
    and -- on an obituary or local probate record, where the owner IS the decedent -- the
    owner name."""
    cf = rec.get("cf") or {}
    research = entry.get("research") or {}
    owner_is_decedent = ("obituary" in (rec.get("_kinds") or [])) or rec.get("_on_account") is False
    keys = set()
    for full in ((f"{cf.get('Deceased First Name') or ''} {cf.get('Deceased Last Name') or ''}"),
                 research.get("decedent_name") or "",
                 (f"{rec.get('owner_first') or ''} {rec.get('owner_last') or ''}" if owner_is_decedent else "")):
        k = _name_key(full)
        if k[0] and k[1]:
            keys.add(k)
    return keys


def decedent_rows_out(entry: dict, rec: dict) -> tuple[dict, list[str]]:
    """A copy of the DP entry with the DECEDENT removed from the signer list.

    SmartSkip returns the household as the subject's relatives, and on a surviving-spouse
    household the "Spouse" row IS the dead owner. The ranker scored that row as a signer
    ("surname matches the owner; shared the owner's address") and the base tool mailed
    it -- 17 envelopes addressed to the decedent on the 2026-10-06 build (verifier). A
    spouse-type signer whose first + last name equals the decedent's is struck here; a
    same-named CHILD stays (namesake children are real heirs) and is flagged downstream.
    """
    dec_names = decedent_keys(entry, rec)
    if not dec_names:
        return entry, []
    out = json.loads(json.dumps(entry))
    struck = []
    for r in (out.get("record") or {}).get("rels") or []:
        if not r.get("is_dm"):
            continue
        if (r.get("canon_rel") or "").lower() in SPOUSE_RELS and _name_key(r.get("name") or "") in dec_names:
            r["is_dm"] = False
            r["dm_why"] = "STRUCK: this is the decedent (spouse row = the dead owner); " + (r.get("dm_why") or "")
            struck.append(r.get("name") or "")
    return out, struck


def _point_base_tool_at(out: Path) -> None:
    """The base tool resolves its file paths from module globals at call time."""
    omc.OUT = out
    omc.DP_INDEX = BUILD_INDEX if BUILD_INDEX.exists() else DP_INDEX
    omc.COHORT = COHORT
    omc.RECORDS = RECORDS_IN_SCOPE
    omc.MAILING_CSV = PIECES_CSV
    omc.EXCLUDED_CSV = EXCLUDED_CSV
    omc.AUDIT = OUT / "audit_base.json"
    omc.MERGED_CSV = GROUPED_BASE_CSV
    omc.AUDIT_MERGED = OUT / "audit_grouped_base.json"


# ------------------------------------------------------------------ 1. offline index

def build_dp_index() -> dict:
    """Same shape as the base tool's index, but backup / pre-* / correction copies of a
    run dir never compete with the live file."""
    ranked: dict[str, dict] = {}
    contested: list[dict] = []
    skipped: list[str] = []
    files = []
    for f in sorted(ROOT.glob("output/**/ranked_records.json")):
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        if BACKUP_PARTS.search(rel):
            skipped.append(rel)
            continue
        files.append(f)
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            say(f"  ! unreadable, skipped: {f} ({e})")
            continue
        run = str(f.parent.relative_to(ROOT)).replace("\\", "/")
        for k, rec in data.items():
            cand = {"run_dir": run, "record": rec,
                    "score": omc._signer_score(rec), "mtime": omc._mtime(f)}
            prev = ranked.get(k)
            if prev is None:
                ranked[k] = cand
                continue
            keep, drop = ((cand, prev) if (cand["score"], cand["mtime"])
                          > (prev["score"], prev["mtime"]) else (prev, cand))
            contested.append({"key": k, "kept": keep["run_dir"], "kept_signers": keep["score"],
                              "dropped": drop["run_dir"], "dropped_signers": drop["score"]})
            ranked[k] = keep

    research: dict[str, dict] = {}
    for f in sorted(ROOT.glob("output/**/stage_c_research.json")):
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        if BACKUP_PARTS.search(rel):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for fin in data.get("findings") or []:
            k = fin.get("record")
            if not k:
                continue
            prev = research.get(k)
            # A CORRECTION wins outright (the namesake repair BLANKS decedent_name and
            # writes a newer researched_at -- code review 2026-10-06: a stale "owner did
            # die" in another run dir was beating the 3112 Embrey Ct correction); among
            # the rest the NEWEST finding wins, including an anchored re-run that rejected
            # a match and left the record unresolved -- that is the conservative answer.
            def _rank(f: dict) -> tuple:
                corrected = bool(f.get("namesake_correction") or f.get("ferry_point_correction")
                                 or f.get("notes_manual"))
                return (corrected, f.get("researched_at") or "")
            if prev is None or _rank(fin) > _rank(prev):
                research[k] = fin
    for k, v in ranked.items():
        v["research"] = research.get(k) or {}
    return {"built": datetime.now().isoformat(timespec="seconds"),
            "ranked_files": len(files), "backup_files_skipped": skipped,
            "keys": len(ranked), "contested": contested, "index": ranked}


def load_ledgers() -> dict:
    """{sift_key: {filed, source, dod, decedent, county}} from the MD RoW and DC ledgers."""
    out: dict[str, dict] = {}
    for path, label in ((MD_LEDGER, "md_row_ledger"), (DC_LEDGER, "dc_probate_ledger")):
        if not path.exists():
            say(f"  ! ledger missing: {path}")
            continue
        est = (jload(path, {}) or {}).get("estates") or {}
        n = 0
        for ek, e in est.items():
            st, zc = e.get("property_street") or "", e.get("property_zip") or ""
            if not (st and zc):
                continue
            filed = _iso(e.get("filing_date") or e.get("date_of_filing")
                         or e.get("date_opened") or e.get("legal_notice_published_on") or "")
            if not filed:
                continue
            k = sift_key(st, zc)
            prev = out.get(k)
            # two estates at one address: keep the LATEST filing (the one that put the
            # record on the list this summer)
            if prev is None or filed > prev["filed"]:
                out[k] = {"filed": filed, "source": f"{label}:{ek}",
                          "dod": _iso(e.get("date_of_death") or e.get("dod") or ""),
                          "decedent": e.get("decedent_name") or "",
                          "county": e.get("county") or ""}
                n += 1
        say(f"  {label}: {len(est)} estates, {n} with an address + filing date")
    return out


def cmd_index(a) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    idx = build_dp_index()
    idx["ledger"] = load_ledgers()
    jsave(DP_INDEX, idx)
    rec = idx["index"]
    sig = sum(omc._signer_score(v["record"]) for v in rec.values())
    nosig = sum(1 for v in rec.values() if omc._signer_score(v["record"]) == 0)
    say(f"DP index: {idx['keys']} records from {idx['ranked_files']} ranked_records.json files "
        f"({len(idx['backup_files_skipped'])} backup copies skipped)")
    say(f"  ranked signers with a mailing address: {sig}")
    say(f"  records with no mailable signer: {nosig}")
    say(f"  keys in more than one run dir: {len(idx['contested'])} (richer source kept)")
    say(f"  ledger addresses with a filing date: {len(idx['ledger'])}")
    say(f"  -> {DP_INDEX}")
    return 0


# ------------------------------------------------------------------ 2. live cohort

def cmd_pull(a) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    idx = jload(DP_INDEX, {})
    if not idx.get("index"):
        say("no DP index -- run `index` first")
        return 2
    dp_keys = set(idx["index"])

    api = Api()
    say("JWT minted OK")
    total = count(api, {})
    say(f"account total: {total}")
    src = omc.resolve_sources(api)
    st, r = api.get("/api/internal/list/?limit=999")
    if st != 200:
        raise RuntimeError(f"list fetch {st}")
    row_lists = [x for x in (r.get("results") or [])
                 if (x.get("title") or "").strip().lower() == ROW_LIST_TITLE.lower()]
    if not row_lists:
        raise RuntimeError(f"no `{ROW_LIST_TITLE}` list on the account")
    src["row_lists"] = [{"title": x.get("title"), "uuid": x["uuid"]} for x in row_lists]
    say(f"sources: obituary list {[x['title'] for x in src['lists']]}, "
        f"obituary tags {[x['title'] for x in src['tags']]}, "
        f"RoW list {[x['title'] for x in src['row_lists']]}")

    queries = []
    if src["lists"]:
        queries.append(("obituary", "Obituary list",
                        {"must": {"any_lists": [x["uuid"] for x in src["lists"]]}}))
    if src["tags"]:
        queries.append(("obituary", "obituary tags",
                        {"must": {"any_tags": [x["uuid"] for x in src["tags"]]}}))
    queries.append(("row", "Register of Wills list",
                    {"must": {"any_lists": [x["uuid"] for x in src["row_lists"]]}}))

    thin: dict[str, dict] = {}
    kinds: dict[str, set] = {}
    per_source = {}
    for kind, label, q in queries:
        c = count(api, q)
        if c == total:
            raise RuntimeError(f"{label} count == account total ({c}) -- the filter was "
                               "ignored, not matched. Refusing.")
        if c == 0:
            raise RuntimeError(f"{label} returned 0 records -- a failure, not a quiet day.")
        per_source[label] = c
        say(f"  {label}: {c}")
        for row in omc.page_thin(api, q, c, label):
            thin.setdefault(row["uuid"], row)
            kinds.setdefault(row["uuid"], set()).add(kind)
    say(f"cohort (obituary UNION RoW): {len(thin)} records")

    keyed, cohort = {}, {}
    for u, row in thin.items():
        ad = row.get("address") or {}
        k = sift_key(ad.get("street") or "", ad.get("zip5") or ad.get("postal_code") or "")
        keyed.setdefault(k, []).append(u)
        if k in dp_keys:
            cohort[u] = dict(row, _key=k, _kinds=sorted(kinds[u]))
    dp_outside = sorted(dp_keys - set(keyed))
    kc = Counter("+".join(v["_kinds"]) for v in cohort.values())
    say(f"cohort AND deep-prospected: {len(cohort)} records  {dict(kc)}")
    say(f"  DP records that are neither obituary nor RoW (left out): {len(dp_outside)}")
    jsave(COHORT, {"generated_at": datetime.now().isoformat(timespec="seconds"),
                   "account_total": total, "sources": src, "per_source": per_source,
                   "cohort_records": len(thin), "cohort": cohort,
                   "dp_keys_outside": dp_outside})

    records = {} if a.fresh else jload(RECORDS, {})
    todo = [u for u in cohort if u not in records or "_error" in records[u]]
    if a.limit:
        todo = todo[:a.limit]
    say(f"hydrating {len(todo)} ({len(records)} cached) with {a.threads} threads...")
    apis = [api] + [Api() for _ in range(a.threads - 1)]
    lock, t0, done = threading.Lock(), time.time(), 0

    def work(i_u):
        i, u = i_u
        return u, omc.hydrate_one(apis[i % len(apis)], u)

    with ThreadPoolExecutor(max_workers=a.threads) as ex:
        futs = [ex.submit(work, (i, u)) for i, u in enumerate(todo)]
        for f in as_completed(futs):
            u, rec = f.result()
            with lock:
                rec["_key"] = cohort[u]["_key"]
                rec["_kinds"] = cohort[u]["_kinds"]
                records[u] = rec
                done += 1
                if done % 100 == 0 or done == len(todo):
                    jsave(RECORDS, records)
                    rate = done / max(time.time() - t0, 1)
                    say(f"  {done}/{len(todo)}  {rate:.2f} rec/s  "
                        f"eta {((len(todo) - done) / max(rate, .01)) / 60:.0f} min")
    jsave(RECORDS, records)
    gone = sum(1 for r in records.values() if r.get("_gone"))
    err = sum(1 for r in records.values() if r.get("_error"))
    say(f"done: {len(records)} hydrated ({gone} gone, {err} errors)")
    return 0


# ------------------------------------------------------------------ 3. the two gates

def filed_date(rec: dict, entry: dict, ledger: dict) -> tuple[str, str]:
    """(iso date, source). Obituary: the obituary date, else the research DOD.
    Register of Wills: the ledger filing date, else a pull-date tag. A synthesised
    (unpushed) record carries its own hints in priority order and takes the first."""
    hints = rec.get("_date_hints") or []
    if hints:
        return tuple(hints[0])
    kinds = set(rec.get("_kinds") or [])
    cands: list[tuple[str, str]] = []
    research = entry.get("research") or {}
    if "obituary" in kinds:
        # The research DOD is the fact; SiftMap's `last_obituary_date` is the date the
        # drip FOUND an obituary and can sit years after the death (verifier 2026-10-06:
        # 23 records with a 2026-07..09 drip date over a 2010-2025 DOD, four exactly one
        # year off). So the DOD decides when the research has one; the drip date is the
        # fallback, never a competitor.
        d = _iso(research.get("dod") or "")
        if d and (research.get("verdict") or "") == CERTAIN_VERDICT:
            return d, f"research_dod:{research.get('dod_source') or ''}"
        d = _iso(rec.get("last_obituary_date") or "")
        if d:
            cands.append((d, "last_obituary_date"))
    if "row" in kinds:
        led = ledger.get(rec.get("_key") or "")
        if led:
            cands.append((led["filed"], led["source"]))
        for t in rec.get("tags") or []:
            m = TAG_ROW_DATE.match(t)
            if m:
                cands.append((f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}",
                              f"tag:{t}"))
                continue
            m = TAG_VER_ROW.match(t)
            if m:
                cands.append((f"{m.group(1)}-{m.group(2)}-{m.group(3)}", f"tag:{t}"))
                continue
            m = TAG_PROBATE_MMDD.match(t)
            if m:
                cands.append((f"2026-{m.group(1)}-{m.group(2)}", f"tag:{t}"))
                continue
            m = TAG_VER_ROW_MONTH.match(t)
            if m:
                cands.append((f"{m.group(1)}-{m.group(2)}-01", f"tag:{t} (month proxy)"))
                continue
            m = TAG_LIST_PURCHASED.match(t)
            if m:
                cands.append((f"{m.group(2)}-{m.group(1)}-01", f"tag:{t} (month proxy)"))
    if not cands:
        return "", ""
    # the LATEST signal is the one that put the record in front of us this summer -- but
    # an exact date always beats a month PROXY (code review 2026-10-06: 20 records with
    # every exact date before July sat in the window on a `List Purchased 07/2026` tag)
    exact = [c for c in cands if "(month proxy)" not in c[1]]
    pool = exact or cands
    pool.sort(reverse=True)
    return pool[0]


def synthesize_unpushed(dp: dict, ledger: dict, present_keys: set) -> tuple[dict, list[dict]]:
    """Records for the DP keys of UNPUSHED_RUN_DIRS that are not on the account cohort,
    built from the run dir's own files. Returns ({uuid: rec}, skipped rows)."""
    check = jload(UNPUSHED_CHECK, {}) if UNPUSHED_CHECK.exists() else {}
    out: dict[str, dict] = {}
    skipped: list[dict] = []
    for run in UNPUSHED_RUN_DIRS:
        rdir = ROOT / run
        recs = jload(rdir / "records.json", {})
        recs = recs if isinstance(recs, dict) else {r["key"]: r for r in recs}
        plan = jload(rdir / "push_plan.json", [])
        plan = plan if isinstance(plan, list) else list(plan.values())
        plan_by = {p.get("key"): p for p in plan if isinstance(p, dict)}
        inputs: dict[str, dict] = {}
        exp = rdir / "dp_input_export.csv"
        if exp.exists():
            for row in csv.DictReader(exp.open(encoding="utf-8-sig")):
                inputs[sift_key(row.get("Property address") or "", row.get("Property zip") or "")] = row
        for k, src in recs.items():
            entry = dp.get(k) or {}
            if k in present_keys:
                continue
            if not entry or entry.get("run_dir") != run:
                # a prep row the batch never ranked (no SmartSkip result / unusable
                # input) -- or ranked in another run dir, where it is handled there
                if not entry:
                    skipped.append({"gate": "certainty", "reason": "not deep prospected",
                                    "detail": f"no ranked_records entry in {run}",
                                    "record_kind": "local", "record_uuid": "local:" + k, "sift_key": k,
                                    "property_street": src.get("street") or "",
                                    "property_zip": src.get("zip") or "",
                                    "owner": f"{src.get('owner_first') or ''} {src.get('owner_last') or ''}".strip()})
                continue
            hit = (check.get(k) or {}).get("uuids") or []
            if hit:
                skipped.append({"gate": "account", "reason": "now on the account but on neither list",
                                "detail": "uuid " + ", ".join(hit) + " -- re-pull to screen it",
                                "record_kind": "local", "record_uuid": "local:" + k, "sift_key": k,
                                "property_street": src.get("street") or "",
                                "property_zip": src.get("zip") or "",
                                "owner": f"{src.get('owner_first') or ''} {src.get('owner_last') or ''}".strip()})
                continue
            inp = inputs.get(k) or {}
            lists_txt = inp.get("Lists") or src.get("lists") or ""
            kinds = sorted({LIST_KIND.get(x.strip().lower(), x.strip().lower())
                            for x in lists_txt.split(",") if x.strip()})
            drive_src = inp.get("Drive source") or ""
            anchors = src.get("anchors") or {}
            court = src.get("court") or {}
            led = ledger.get(k) or {}
            hints: list[tuple[str, str]] = []
            for lab, val in (("anchors.file_date", anchors.get("file_date")),
                             ("court.filing_date", court.get("filing_date")),
                             ("ledger", led.get("filed"))):
                d = _iso(val or "")
                if d:
                    hints.append((d, lab))
            m = SHEET26_PUB.search(drive_src)
            if m:
                hints.append((f"{m.group(3)}-{m.group(1)}-{m.group(2)}", "sheet26 publish date"))
            # a WEEKLY probate file is dated by the week it was pulled, which is the
            # filing week in practice -- so it ranks above a DOD (a September filing on
            # a May death is still a September filing); the monthly list has no such date
            m = WEEKLY_SRC.search(drive_src)
            if m:
                hints.append((f"2026-{m.group(1)}-{m.group(2)}", f"weekly vendor file {drive_src} (proxy)"))
            for lab, val in (("anchors.dod", anchors.get("dod")), ("court.dod", court.get("dod")),
                             ("obit_date", src.get("obit_date"))):
                d = _iso(val or "")
                if d:
                    hints.append((d, lab))
            if not m and drive_src.lower().startswith("monthly"):
                hints.append((MONTHLY_PROXY, f"Sept 2026 monthly vendor list: {drive_src} (proxy)"))
            pl = plan_by.get(k) or {}
            cf = dict(pl.get("custom_fields") or {})
            cf.setdefault("Deceased First Name", src.get("decedent_first") or "")
            cf.setdefault("Deceased Last Name", src.get("decedent_last") or "")
            pr = (pl.get("pr") or {}).get("name") if isinstance(pl.get("pr"), dict) else pl.get("pr")
            u = "local:" + k
            out[u] = {
                "street": src.get("street"), "city": src.get("city"), "state": src.get("state"),
                "zip": zip5(src.get("zip") or ""), "county": src.get("county"),
                "status": "", "type": "",
                "owner_first": src.get("owner_first"), "owner_last": src.get("owner_last"),
                "owner_company": src.get("business_name"), "owner_deceased": None,
                "mail_street": src.get("mail_street"), "mail_city": src.get("mail_city"),
                "mail_state": src.get("mail_state"), "mail_zip": src.get("mail_zip"),
                # the record's ORIGIN list stands in for its account list: a row off the
                # Drive Inheritance / Probate (clerk) lists was mailed by that campaign
                # (Basem 2026-10-06: "its all clerk data - do not include, those were
                # mailed before"), so the base previously-mailed rule drops it the same
                # way it drops an account record on those lists; a Sheet26 Register of
                # Wills row counts as mailed only with an attempt, and has none
                "lists": [x.strip() for x in lists_txt.split(",") if x.strip()], "tags": [],
                "personal_representative": pr or src.get("pr") or "",
                "last_obituary_date": _iso(src.get("obit_date") or ""),
                "do_not_mail_ever": False, "owner_do_not_mail_ever": False,
                "directmail_attempts": 0, "cf": {k2: v for k2, v in cf.items() if v},
                "_key": k, "_kinds": kinds or ["probate"], "_on_account": False,
                "_source_list": lists_txt, "_drive_source": drive_src,
                "_date_hints": hints, "_run_dir": run,
            }
    return out, skipped


UNCERTAIN_TAGS = {"dp death unverified", "dp owner presumed alive", "owner alive"}


def certainty_reason(entry: dict, rec: dict | None = None) -> str | None:
    """Why the heirs on this record are NOT certain. None = certain."""
    if not entry:
        return "not deep prospected"
    # the account's own verdict tags, written by an earlier DP push, outrank a run-dir
    # finding that says otherwise (code review 2026-10-06: 20 in-scope records carried
    # `DP Death Unverified` under a "high" finding)
    hit = sorted(t for t in (rec or {}).get("tags") or [] if t.strip().lower() in UNCERTAIN_TAGS)
    if hit:
        return "account tag says not verified: " + ", ".join(hit)
    rec = entry.get("record") or {}
    research = entry.get("research") or {}
    dms = [r for r in rec.get("rels") or []
           if r.get("is_dm") and r.get("mailing_street") and r.get("mailing_zip")]
    if not dms:
        return "no ranked signer with a mailing address"
    verdict = (research.get("verdict") or "").strip()
    conf = (research.get("confidence") or "").strip().lower()
    if verdict != CERTAIN_VERDICT:
        return f"death not verified: {verdict or 'no research finding'}"
    if conf not in CERTAIN_CONFIDENCE:
        return f"research confidence {conf or 'none'}"
    basis = (rec.get("signer_basis") or "").strip()
    if not basis:
        return "no signer basis recorded"
    if basis.lower().startswith(UNCERTAIN_BASIS):
        return "signers are unlabelled relatives (no labelled immediate family)"
    return None


def cmd_build(a) -> int:
    idx = jload(DP_INDEX, {})
    records = jload(RECORDS, {})
    if not idx.get("index") or not records:
        say("missing dp_index.json or records.json -- run `index` then `pull`")
        return 2
    dp, ledger = idx["index"], idx.get("ledger") or {}
    since = _iso(a.filed_since)
    until = _iso(a.filed_until) if a.filed_until else date.today().isoformat()
    if not since:
        say("--filed-since must be an ISO date")
        return 2
    say(f"window: filed {since} .. {until}")

    in_scope: dict[str, dict] = {}
    build_entries: dict[str, dict] = {}
    pre: list[dict] = []
    tally: Counter = Counter()
    if a.include_unpushed:
        present = {r.get("_key") for r in records.values()}
        local, skipped = synthesize_unpushed(dp, ledger, present)
        pre.extend(skipped)
        tally["unpushed_now_on_account"] = sum(1 for x in skipped if x["gate"] == "account")
        tally["unpushed_not_ranked"] = sum(1 for x in skipped if x["gate"] != "account")
        say(f"unpushed batch records synthesised: {len(local)} "
            f"({tally['unpushed_now_on_account']} now on the account, skipped; "
            f"{tally['unpushed_not_ranked']} prep rows never ranked, logged; "
            f"{'probe read' if UNPUSHED_CHECK.exists() else 'NO account probe file'})")
        records = dict(records, **local)
    for u, rec in sorted(records.items(), key=lambda kv: kv[1].get("_key") or ""):
        key = rec.get("_key") or ""
        entry = dp.get(key) or {}
        base = {"record_uuid": u, "sift_key": key, "record_kind": "+".join(rec.get("_kinds") or []),
                "property_street": rec.get("street") or "", "property_zip": rec.get("zip") or "",
                "owner": f"{rec.get('owner_first') or ''} {rec.get('owner_last') or ''}".strip()}
        if rec.get("_gone") or rec.get("_error"):
            tally["unreadable"] += 1
            pre.append(dict(base, gate="read", reason="record gone or read failed",
                            detail=str(rec.get("_error") or "gone")))
            continue
        fd, fsrc = filed_date(rec, entry, ledger)
        if not fd:
            tally["no_filed_date"] += 1
            pre.append(dict(base, gate="date", reason="no filing / obituary date found",
                            detail="no last_obituary_date, research DOD, ledger row or pull-date tag"))
            continue
        if not (since <= fd <= until):
            tally["outside_window"] += 1
            pre.append(dict(base, gate="date", reason="filed outside the window",
                            detail=f"{fd} ({fsrc})"))
            continue
        # the decedent is never a signer: strike the spouse row that IS the dead owner
        # BEFORE the certainty test, so a record whose only "signer" was the decedent
        # fails "no ranked signer with a mailing address" instead of mailing them
        entry, struck = decedent_rows_out(entry, rec)
        for name in struck:
            tally["decedent_struck_from_signers"] += 1
            pre.append(dict(base, gate="signer", reason="signer is the decedent",
                            detail=f"{name} (spouse row = the dead owner), struck"))
        if struck:
            build_entries[key] = entry
        why = certainty_reason(entry, rec)
        if why:
            tally["heirs_not_certain"] += 1
            pre.append(dict(base, gate="certainty", reason=why,
                            detail=f"run_dir={entry.get('run_dir') or ''} filed={fd}"))
            continue
        rec = dict(rec, _filed_date=fd, _filed_source=fsrc)
        in_scope[u] = rec
        tally["in_scope"] += 1
    jsave(RECORDS_IN_SCOPE, in_scope)
    # the index the base tool builds from: in-scope keys only, decedent rows struck
    jsave(BUILD_INDEX, {"built": datetime.now().isoformat(timespec="seconds"),
                        "source": str(DP_INDEX), "contested": idx.get("contested") or [],
                        "index": {r["_key"]: build_entries.get(r["_key"]) or dp[r["_key"]]
                                  for r in in_scope.values() if r.get("_key") in dp}})
    dp = dict(dp, **build_entries)
    say(f"  decedent struck     {tally['decedent_struck_from_signers']} spouse rows that were the dead owner")
    with PREFILTER_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["gate", "reason", "detail", "record_kind", "record_uuid",
                                           "sift_key", "property_street", "property_zip", "owner"])
        w.writeheader()
        w.writerows(pre)
    say(f"records hydrated      {len(records)}")
    say(f"  unreadable          {tally['unreadable']}")
    say(f"  no filed date       {tally['no_filed_date']}")
    say(f"  outside window      {tally['outside_window']}")
    say(f"  heirs not certain   {tally['heirs_not_certain']}")
    say(f"  IN SCOPE            {tally['in_scope']}  "
        f"{dict(Counter(r['record_kind'] for r in [dict(record_kind='+'.join(x.get('_kinds') or [])) for x in in_scope.values()]))}")
    say(f"  -> {PREFILTER_CSV}")
    if not in_scope:
        say("nothing in scope -- no CSV written")
        return 1

    # the base tool: suppression, the previously-mailed screen, household + signer pieces
    _point_base_tool_at(OUT)
    rc = omc.cmd_build(Namespace(keep_mailed=False, no_drive=a.no_drive,
                                 mailed_index=a.mailed_index))
    if rc != 0:
        say(f"! base build returned {rc}")
        return rc
    rc = omc.cmd_merge(Namespace())
    if rc != 0:
        say(f"! base merge returned {rc}")
        return rc

    # enrich both files with the gate facts and the heir's best phone
    def extras_for(row: dict) -> dict:
        rec = in_scope.get(row["record_uuid"]) or {}
        entry = dp.get(row["sift_key"]) or {}
        research = entry.get("research") or {}
        phones = []
        if row["addressee_type"] == "signer":
            rels = {str(r.get("n")): r for r in (entry.get("record") or {}).get("rels") or []}
            for mid in (row.get("merged_mail_ids") or row["mail_id"]).split("; "):
                m = re.search(r"\|rel(\d+)$", mid)
                r = rels.get(m.group(1)) if m else None
                p = _best_phone(r) if r else ""
                if p and p not in phones:
                    phones.append(p)
        return {"record_kind": "+".join(rec.get("_kinds") or []),
                "filed_date": rec.get("_filed_date") or "",
                "filed_date_source": rec.get("_filed_source") or "",
                "verdict": research.get("verdict") or "",
                "confidence": research.get("confidence") or "",
                "signer_basis": (entry.get("record") or {}).get("signer_basis") or "",
                # raw SmartSkip numbers, NOT Trestle / litigator scored: reference only, never a dial sheet
                "phone_unscored": "; ".join(phones),
                "footprint": ("yes" if (row.get("county") or "").strip().lower()
                              in FOOTPRINT_COUNTIES else "no"),
                "on_account": "no" if rec.get("_on_account") is False else "yes",
                "source_list": rec.get("_source_list") or "",
                "drive_source": rec.get("_drive_source") or ""}

    # one person at TWO addresses for the same property (SmartSkip lists a person on several
    # rows, sometimes a namesake parent/child): both pieces stay, both are flagged
    doors: dict[tuple, set] = {}
    for r in csv.DictReader(PIECES_CSV.open(encoding="utf-8-sig")):
        if r["addressee_type"] == "signer":
            doors.setdefault((r["record_uuid"], omc.norm(_clean_name(r["addressee"]))), set()).add(
                (omc.norm(r["mail_street"]), r["mail_zip"]))
    multi = {k for k, v in doors.items() if len(v) > 1}

    def names_in(r: dict) -> list[str]:
        co = r.get("co_addressees") or ""
        if co:
            return [re.sub(r"\s*\([^)]*\)\s*$", "", x).strip() for x in co.split("; ")]
        return [r["addressee"]]

    for src_csv, dst_csv in ((PIECES_CSV, FINAL_CSV), (GROUPED_BASE_CSV, FINAL_GROUPED_CSV)):
        rows = list(csv.DictReader(src_csv.open(encoding="utf-8-sig")))
        cols = list(rows[0].keys()) if rows else omc.MAIL_COLS
        n_flag = 0
        with dst_csv.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=cols + EXTRA_COLS)
            w.writeheader()
            for r in rows:
                r["addressee"] = _clean_name(r["addressee"])
                if r.get("co_addressees"):
                    r["co_addressees"] = re.sub(r"[,.;]+\s*\(", " (", r["co_addressees"])
                if r["addressee_type"] == "signer" and any(
                        (r["record_uuid"], omc.norm(_clean_name(n))) in multi for n in names_in(r)):
                    r["flags"] = "; ".join(f for f in [r.get("flags") or "", "same_person_other_address"] if f)
                    n_flag += 1
                # a non-spouse signer carrying the decedent's own name (namesake child):
                # a real heir, kept, but worth a look before the envelope is printed
                if r["addressee_type"] == "signer":
                    dk = decedent_keys(dp.get(r["sift_key"]) or {}, in_scope.get(r["record_uuid"]) or {})
                    if dk and any(_name_key(n) in dk for n in names_in(r)):
                        r["flags"] = "; ".join(f for f in [r.get("flags") or "", "namesake_of_decedent"] if f)
                w.writerow(dict(r, **extras_for(r)))
        say(f"  -> {dst_csv}  ({len(rows)} rows, {n_flag} flagged same_person_other_address)")

    # the mail-ready view: one row per envelope, the columns a mail house needs, in order
    grouped = list(csv.DictReader(FINAL_GROUPED_CSV.open(encoding="utf-8-sig")))
    grouped.sort(key=lambda r: (r["footprint"] != "yes", r["county"], r["property_street"],
                                r["addressee_type"] != "household", r["addressee"]))
    with MAIL_READY_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=MAIL_READY_COLS)
        w.writeheader()
        for i, r in enumerate(grouped, 1):
            w.writerow({"piece_no": i, "mail_to": r["addressee"],
                        **{c: r.get(c, "") for c in MAIL_READY_COLS if c not in ("piece_no", "mail_to")}})
    say(f"  -> {MAIL_READY_CSV}  ({len(grouped)} rows)")

    base_audit = jload(OUT / "audit_base.json", {})
    grouped_audit = jload(OUT / "audit_grouped_base.json", {})
    final_rows = list(csv.DictReader(FINAL_GROUPED_CSV.open(encoding="utf-8-sig")))
    jsave(AUDIT, {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": {"filed_since": since, "filed_until": until},
        "certainty_rule": {"verdict": CERTAIN_VERDICT, "confidence": sorted(CERTAIN_CONFIDENCE),
                           "basis_excluded_prefix": UNCERTAIN_BASIS,
                           "needs_ranked_signer_with_address": True},
        "prefilter": dict(tally),
        "base_build": base_audit, "grouped": grouped_audit,
        "final": {"pieces": base_audit.get("total_mailpieces"),
                  "grouped_rows": len(final_rows),
                  "properties": len({r["record_uuid"] for r in final_rows}),
                  "by_kind": dict(Counter(r["record_kind"] for r in final_rows)),
                  "by_addressee_type": dict(Counter(r["addressee_type"] for r in final_rows))},
    })
    say(f"  -> {AUDIT}")
    return 0


def cmd_qa(a) -> int:
    """The base QA over the ACCOUNT rows only: a synthesised (local:) record has no uuid
    to read back, so those rows are held out of the sample file; the local rows are
    checked offline instead (every signer must match its ranked_records source)."""
    _point_base_tool_at(OUT)
    rows = list(csv.DictReader(PIECES_CSV.open(encoding="utf-8-sig")))
    acct = [r for r in rows if not r["record_uuid"].startswith("local:")]
    local = [r for r in rows if r["record_uuid"].startswith("local:")]
    tmp = OUT / "_qa_account_rows.csv"
    with tmp.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=omc.MAIL_COLS)
        w.writeheader()
        w.writerows(acct)
    omc.MAILING_CSV = tmp
    rc = omc.cmd_qa(a)
    omc.MAILING_CSV = PIECES_CSV
    idx = jload(DP_INDEX, {}).get("index") or {}
    mi = mailed_history.load_index(mailed_history.INDEX) if mailed_history.INDEX.exists() else None
    problems = []
    for r in local:
        if r["addressee_type"] != "signer":
            continue
        # one person can sit in several REL slots at different addresses (SmartSkip lists
        # the same person on several rows), so match on name AND street, not name alone
        src = {(omc.norm(_clean_name(x.get("name") or "")), omc.norm(x.get("mailing_street") or ""))
               for x in (idx.get(r["sift_key"]) or {}).get("record", {}).get("rels") or []}
        if (omc.norm(r["addressee"]), omc.norm(r["mail_street"])) not in src:
            problems.append(f"{r['record_uuid']}: {r['addressee']} not in / drifted from ranked_records")
    drive = [r for r in local if mi and mailed_history.lookup(mi, r["property_street"], r["property_zip"])]
    say(f"local rows {len(local)}: {len(problems)} signer mismatches, {len(drive)} Drive hits (expected 0)")
    for p in problems[:10]:
        say(f"  ! {p}")
    return rc if rc else (0 if not problems and not drive else 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="offline: DP run dirs (no backups) + the RoW / DC ledgers")
    p = sub.add_parser("pull", help="live read: obituary + RoW cohort, hydrate the DP intersection")
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--limit", type=int)
    p.add_argument("--fresh", action="store_true")
    b = sub.add_parser("build", help="gates + the base build + merge -> heir_mailing*.csv")
    b.add_argument("--filed-since", default="2026-07-01")
    b.add_argument("--filed-until", default="")
    b.add_argument("--mailed-index")
    b.add_argument("--no-drive", action="store_true")
    b.add_argument("--include-unpushed", action="store_true",
                   help="also the deep-prospected batches never pushed to the account "
                        f"({', '.join(UNPUSHED_RUN_DIRS)}), synthesised from their run dirs")
    q = sub.add_parser("qa")
    q.add_argument("--sample", type=int, default=8)
    a = ap.parse_args()
    return {"index": cmd_index, "pull": cmd_pull, "build": cmd_build, "qa": cmd_qa}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
