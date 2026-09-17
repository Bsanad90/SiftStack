"""Consolidate every deep-prospected OBITUARY record into one mailing CSV.

Each obituary record has a DECEASED owner, so the people who can actually sign are
relatives. This collects them into one tall file where each row is one mailpiece:

  * one HOUSEHOLD piece per record -- "The Shaws" at the PROPERTY address;
  * one SIGNER piece per ranked decision maker, at THAT PERSON'S OWN mailing address.

WHERE THE HEIR ADDRESS COMES FROM. The account holds heir names and phones but NO heir
mailing address -- it is never written to the CRM, the board note, `dial_sheet.csv`, the
`reload_*.csv` files, or `push_plan.json` (which carries only the OWNER's mailing block).
The only durable source is `ranked_records.json` in each DP run dir, whose `rels[]` already
carry `mailing_street/city/state/zip` straight from SmartSkip. So the heir side of this
file is built OFFLINE from the run dirs and the account side is pulled LIVE.

TWO TRAPS THIS FILE IS BUILT AROUND.

1. Run-dir NAMES are not cohort boundaries. The 619-record obituary batch (the 2026-09-01
   SmartSkip order) lives inside `output/dp_hottest_needs_skipped/`, which holds
   four unrelated orders. So "is this an obituary record" is decided by the LIVE ACCOUNT
   (Obituary list / Obituaries tags), never by which directory the heirs came from.

2. A filter key the API does not recognise is not an error -- it is silently ignored and
   the count comes back as the unfiltered account total, reading like a real answer. The
   unfiltered total is therefore taken FIRST and any cohort count equal to it aborts.

Nothing here writes to DataSift. Every call is a GET or a post_as_get read.

Usage:
    python src/scripts/obituary_mail_consolidate.py index          # offline, no API
    python src/scripts/obituary_mail_consolidate.py pull           # live, resumable
    python src/scripts/obituary_mail_consolidate.py build
    python src/scripts/obituary_mail_consolidate.py qa --sample 5
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from dpd.preset_spec import DEAD_STATUSES  # noqa: E402
from dpd_lane_assignee_report import PAGE, count  # noqa: E402
from dpd_lane_phone_score import Api, jload, jsave, say, tag_names  # noqa: E402
from obituary_dp_batch import ORDINALS, sift_key, zip5  # noqa: E402
from obituary_dp_input import ENTITY_WORDS, SUFFIXES  # noqa: E402
from rel_pr_name_repair import cf_values  # noqa: E402

OUT = ROOT / "output" / "obituary_mail_consolidate"
DP_INDEX = OUT / "dp_index.json"
COHORT = OUT / "cohort_thin.json"
RECORDS = OUT / "records.json"
MAILING_CSV = OUT / "obituary_mailing.csv"
EXCLUDED_CSV = OUT / "excluded.csv"
AUDIT = OUT / "audit.json"

OFFSET_CEILING = 10000

# Resolved live 2026-09-15. Titles are matched case-insensitively on "obitua" at pull time
# so a renamed or newly added tag is picked up; these are the fallbacks / the audit trail.
OBIT_LIST_TITLE = "Obituary"
OBIT_TAG_SUBSTR = "obitua"

# Property tag that means the house has sold out from under us. The sold sweep stamps it
# and the "Sold Property Cleanup" sequence maps it onto the `Already Sold` STATUS, but the
# tag lands first, so suppress on both.
RECENTLY_SOLD_TAG = "Recently Sold"

MAIL_COLS = [
    "mail_id", "addressee_type", "addressee",
    "mail_street", "mail_city", "mail_state", "mail_zip",
    "relationship", "dm_why",
    "decedent_name", "decedent_surname", "date_of_death",
    "property_street", "property_city", "property_state", "property_zip", "county",
    "owner_first", "owner_last", "pr_name",
    "status", "lists", "tags",
    "record_uuid", "sift_key", "dp_run_dir", "signer_mail_count", "flags",
]

EXCL_COLS = ["scope", "sift_key", "record_uuid", "property_street", "property_zip",
             "name", "relationship", "reason", "detail"]


# ------------------------------------------------------------------ small helpers

def _status_key(s: str) -> str:
    """Normalise a status for comparison.

    The account stores some statuses as display labels ("Already Sold", "No Answer") and
    others snake_cased ("not_interested", "under_contract", "dnc", "listed"). DEAD_STATUSES
    holds the display forms only, so a plain lowercase compare silently MISSES the
    snake_cased dead ones -- measured live in this very cohort: not_interested 31, listed 3,
    already sold 2, dead lead 1, dnc 1. Same normalisation as sold_backlog._status_key.
    """
    return re.sub(r"[\s_\-]+", " ", (s or "").strip().lower())


DEAD_KEYS = {_status_key(s) for s in DEAD_STATUSES}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def title_name(s: str) -> str:
    """SmartSkip SHOUTS names; a mail label must not. Keeps Mc/Mac/O' and hyphens."""
    out = []
    for word in re.split(r"(\s+)", (s or "").strip()):
        if not word.strip():
            out.append(word)
            continue
        parts = re.split(r"([-'])", word.lower())
        fixed = []
        for p in parts:
            if p in ("-", "'"):
                fixed.append(p)
            elif p.startswith("mc") and len(p) > 2:
                fixed.append("Mc" + p[2:].capitalize())
            elif p.startswith("mac") and len(p) > 4:
                fixed.append("Mac" + p[3:].capitalize())
            else:
                fixed.append(p.capitalize())
        out.append("".join(fixed))
    return "".join(out)


_STREET_FIX = {"Po": "PO", "Pobox": "PO Box", "Ne": "NE", "Nw": "NW", "Se": "SE",
                "Sw": "SW", "Us": "US", "Rr": "RR", "Hc": "HC"}


def title_street(s: str) -> str:
    """Title-case a SHOUTED SmartSkip street without mangling the parts USPS cares about:
    PO Box and the quadrant directionals must stay upper (`Po Box`, `Nw` read as typos on
    an envelope)."""
    return " ".join(_STREET_FIX.get(w, w) for w in title_street_words(s))


def title_street_words(s: str) -> list[str]:
    return title_name(s).split()


def household_name(surname: str) -> str:
    """"Shaw" -> "The Shaws"; "Jones" -> "The Joneses"; "Marsh" -> "The Marshes".

    English surname pluralisation only: a final y never becomes -ies (Kennedy -> Kennedys).
    The raw surname is also written to its own column so a bad plural can be corrected in
    the sheet without re-running anything.
    """
    s = title_name(surname).strip()
    if not s:
        return ""
    low = s.lower()
    if low.endswith(("s", "x", "z", "ch", "sh")):
        return f"The {s}es"
    return f"The {s}s"


def has_house_number(street: str) -> bool:
    """A street line with no house number cannot be delivered.

    The first token must carry a digit, or the line must be a PO Box / rural route. Note
    the digit need not lead: "N4806 County Road E" is a valid Wisconsin grid address, so
    a leading-digit test alone would wrongly reject real addresses.
    """
    st = (street or "").strip()
    if not st:
        return False
    if re.match(r"^(p\.?\s?o\.?\s?box|rr|hc)\b", st, re.I):
        return True
    return any(c.isdigit() for c in st.split()[0])


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


# ------------------------------------------------------------------ 1. offline DP index

def _signer_score(rec: dict) -> int:
    """How rich this run dir's copy of the record is: ranked signers WITH an address.

    Basem's standing rule is that the richer source wins on a record two batches both
    loaded -- not the newest. 11 keys live in more than one run dir.
    """
    return sum(1 for r in rec.get("rels") or []
               if r.get("is_dm") and r.get("mailing_street") and r.get("mailing_zip"))


def build_dp_index() -> dict:
    """{sift_key: {run_dir, record, research}} across every DP run dir under output/."""
    ranked: dict[str, dict] = {}
    contested: list[dict] = []
    files = sorted(ROOT.glob("output/**/ranked_records.json"))
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            say(f"  ! unreadable, skipped: {f} ({e})")
            continue
        run = str(f.parent.relative_to(ROOT)).replace("\\", "/")
        for k, rec in data.items():
            cand = {"run_dir": run, "record": rec,
                    "score": _signer_score(rec), "mtime": _mtime(f)}
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
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for fin in data.get("findings") or []:
            k = fin.get("record")
            if not k:
                continue
            # a finding that actually names the decedent beats an "unresolved" shell
            prev = research.get(k)
            if prev is None or (fin.get("decedent_name") and not prev.get("decedent_name")):
                research[k] = fin

    for k, v in ranked.items():
        v["research"] = research.get(k) or {}
    return {"built": datetime.now().isoformat(timespec="seconds"),
            "ranked_files": len(files), "keys": len(ranked),
            "contested": contested, "index": ranked}


def cmd_index(a) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    idx = build_dp_index()
    jsave(DP_INDEX, idx)
    rec = idx["index"]
    sig = sum(_signer_score(v["record"]) for v in rec.values())
    nosig = sum(1 for v in rec.values() if _signer_score(v["record"]) == 0)
    say(f"DP index: {idx['keys']} records from {idx['ranked_files']} ranked_records.json files")
    say(f"  ranked signers with a mailing address: {sig}")
    say(f"  records with no mailable signer: {nosig}")
    say(f"  keys in more than one run dir: {len(idx['contested'])} (richer source kept)")
    say(f"  -> {DP_INDEX}")
    return 0


# ------------------------------------------------------------------ 2. live cohort

def resolve_sources(api: Api) -> dict:
    """The account's own definition of 'obituary': the Obituary LIST plus every tag whose
    title contains 'obitua'. Both exist live (list `Obituary`, tag `Obituaries`)."""
    st, r = api.get("/api/internal/list/?limit=999")
    if st != 200:
        raise RuntimeError(f"list fetch {st}: {str(r)[:200]}")
    lists = [x for x in (r.get("results") or [])
             if (x.get("title") or "").strip().lower() == OBIT_LIST_TITLE.lower()]
    st, r = api.get("/api/internal/tag/?limit=999")
    if st != 200:
        raise RuntimeError(f"tag fetch {st}: {str(r)[:200]}")
    tags = [x for x in (r.get("results") or [])
            if OBIT_TAG_SUBSTR in (x.get("title") or "").lower()]
    if not lists and not tags:
        raise RuntimeError("no Obituary list and no obituary tag on the account -- refusing "
                           "to build a cohort from nothing")
    return {"lists": [{"title": x.get("title"), "uuid": x["uuid"]} for x in lists],
            "tags": [{"title": x.get("title"), "uuid": x["uuid"]} for x in tags]}


def page_thin(api: Api, query: dict, expected: int, label: str) -> list[dict]:
    """Page a query keeping the THIN ROW, which already carries uuid, property address,
    owner mailing, status and both do-not-mail flags -- so the cohort can be intersected
    and suppressed without a detail read."""
    if expected > OFFSET_CEILING:
        raise RuntimeError(f"{label} holds {expected}, past the {OFFSET_CEILING} offset "
                           "ceiling -- needs created-range bucketing")
    out, seen, offset = [], set(), 0
    while offset < expected:
        st, r = api.post_as_get("/api/internal/property/",
                                {"limit": PAGE, "offset": offset, "query": query})
        if st != 200:
            raise RuntimeError(f"{label} page {offset}: {st} {str(r)[:200]}")
        rows = r.get("results") or []
        if not rows:
            break
        for row in rows:
            if row["uuid"] not in seen:
                seen.add(row["uuid"])
                out.append(row)
        offset += PAGE
    return out


def hydrate_one(api: Api, u: str) -> dict:
    """Detail + custom fields. Custom fields are NOT on the detail endpoint.

    A failed custom-field read returns an error rather than an empty dict: "all fields
    empty" and "the read failed" must never look alike, or a record silently loses its
    decedent name and gets mailed under the owner's surname instead.
    """
    st, body = api.get(f"/api/internal/property/{u}/")
    if st == 404:
        return {"_gone": True}
    if st != 200:
        return {"_error": f"detail {st}"}
    stc, cf = api.get(f"/api/internal/property/{u}/custom-field/")
    if stc != 200:
        return {"_error": f"cf {stc}"}
    addr = body.get("address") or {}
    owner = body.get("owner") or {}
    oaddr = owner.get("address") or {}
    return {
        "street": addr.get("street"), "city": addr.get("city"), "state": addr.get("state"),
        "zip": addr.get("zip5") or (addr.get("postal_code") or "")[:5],
        "county": addr.get("county"),
        "status": body.get("status"), "type": body.get("type"),
        "owner_first": owner.get("first_name"), "owner_last": owner.get("last_name"),
        "owner_company": owner.get("company"), "owner_deceased": owner.get("deceased"),
        "mail_street": oaddr.get("street"), "mail_city": oaddr.get("city"),
        "mail_state": oaddr.get("state"),
        "mail_zip": oaddr.get("zip5") or (oaddr.get("postal_code") or "")[:5],
        "lists": tag_names(body.get("lists")), "tags": tag_names(body.get("tags")),
        "personal_representative": body.get("personal_representative"),
        "last_obituary_date": (body.get("last_obituary_date") or "")[:10],
        "do_not_mail_ever": body.get("do_not_mail_ever"),
        "owner_do_not_mail_ever": owner.get("do_not_mail_ever"),
        "cf": {k: v for k, v in cf_values(cf).items() if v},
        "pulled": datetime.now().isoformat(timespec="seconds"),
    }


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
    src = resolve_sources(api)
    say(f"sources: list {[x['title'] for x in src['lists']]}, "
        f"tags {[x['title'] for x in src['tags']]}")

    queries = []
    if src["lists"]:
        queries.append(("Obituary list",
                        {"must": {"any_lists": [x["uuid"] for x in src["lists"]]}}))
    if src["tags"]:
        queries.append(("obituary tags",
                        {"must": {"any_tags": [x["uuid"] for x in src["tags"]]}}))

    thin: dict[str, dict] = {}
    per_source = {}
    for label, q in queries:
        c = count(api, q)
        # THE guard: an unrecognised key is ignored and returns the account total.
        if c == total:
            raise RuntimeError(f"{label} count == account total ({c}) -- the filter was "
                               "ignored, not matched. Refusing to build a cohort from it.")
        if c == 0:
            raise RuntimeError(f"{label} returned 0 records -- that is a failure, not a "
                               "quiet day. Check the uuid.")
        per_source[label] = c
        say(f"  {label}: {c}")
        for row in page_thin(api, q, c, label):
            thin.setdefault(row["uuid"], row)
    say(f"obituary cohort (list UNION tags): {len(thin)} records")

    # Intersect on the DP join key. Every row here is obituary by construction (the query
    # filtered it), so membership needs no re-check.
    keyed, cohort = {}, {}
    for u, row in thin.items():
        ad = row.get("address") or {}
        k = sift_key(ad.get("street") or "", ad.get("zip5") or ad.get("postal_code") or "")
        keyed.setdefault(k, []).append(u)
        if k in dp_keys:
            cohort[u] = dict(row, _key=k)
    dp_outside = sorted(dp_keys - set(keyed))
    say(f"obituary AND deep-prospected: {len(cohort)} records")
    say(f"  DP records that are NOT obituary (left out on purpose): {len(dp_outside)}")
    jsave(COHORT, {"generated_at": datetime.now().isoformat(timespec="seconds"),
                   "account_total": total, "sources": src, "per_source": per_source,
                   "obituary_records": len(thin), "cohort": cohort,
                   "dp_keys_not_obituary": dp_outside})

    records = {} if a.fresh else jload(RECORDS, {})
    todo = [u for u in cohort if u not in records or "_error" in records[u]]
    if a.limit:
        todo = todo[:a.limit]
    say(f"hydrating {len(todo)} ({len(records)} cached) with {a.threads} threads...")
    apis = [api] + [Api() for _ in range(a.threads - 1)]
    lock, t0, done = threading.Lock(), time.time(), 0

    def work(i_u):
        i, u = i_u
        return u, hydrate_one(apis[i % len(apis)], u)

    with ThreadPoolExecutor(max_workers=a.threads) as ex:
        futs = [ex.submit(work, (i, u)) for i, u in enumerate(todo)]
        for f in as_completed(futs):
            u, rec = f.result()
            with lock:
                rec["_key"] = cohort[u]["_key"]
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


# ------------------------------------------------------------------ 3. build the CSV

# Everything that is NOT a surname but can end up last in a name string.
_NOT_SURNAME = {w.lower() for w in SUFFIXES} | {w.lower() for w in ORDINALS} | {
    "jr.", "sr.", "ii.", "iii.", "iv.", "the", "and", "etal", "et", "al", "trustee",
    "trustees", "deceased", "decd", "estate", "unknown", "none", "null",
    # title words that sit between the family name and the entity word, so that
    # "Smith Family Trust" resolves to Smith rather than to "Family"
    "family", "living", "irrevocable", "of", "heirs", "heir", "survivor", "survivors",
}


def surname_of(name: str) -> str:
    """The family surname inside a free-text name string.

    Three real shapes measured on this cohort, each of which broke a naive "last token":
      * a SUFFIX last          -- "William Henry Maddox, Jr."  -> Maddox, not "Jr."
      * a MAIDEN NAME in parens -- "Mary Keeton (Strouss)"     -> Keeton, not "(Strouss)"
      * a VESTING BLOB first    -- "Kupiec Thomas Andrew & Evelyn" -> Kupiec, not "Evelyn"
    A blob is recognised by "&"/"and" or by being long enough to be "Last First Middle".
    """
    raw = (name or "").strip()
    if not raw:
        return ""
    # drop parenthesised maiden names and any trailing comma-suffix
    raw = re.sub(r"\([^)]*\)", " ", raw)
    toks = [t.strip(".,") for t in re.split(r"[\s,]+", raw) if t.strip(".,")]
    if not toks:
        return ""
    # a vesting blob reads surname-first
    if any(t.lower() in ("&", "and") or t == "&" for t in toks) and len(toks) > 2:
        head = toks[0]
        core = re.sub(r"[^a-z]", "", head.lower())
        return head if (core and core not in _NOT_SURNAME
                        and core not in ENTITY_WORDS) else ""
    def _ok(t: str) -> bool:
        low = t.lower()
        # an entity word can arrive welded to digits or a hyphen ("Trust-2010" is a real
        # owner_last on this cohort); compare on the alphabetic core, not the raw token
        core = re.sub(r"[^a-z]", "", low)
        return (low not in _NOT_SURNAME and core not in _NOT_SURNAME
                and low not in ENTITY_WORDS and core not in ENTITY_WORDS
                and any(c.isalpha() for c in t)
                and not re.fullmatch(r"[A-Za-z]", t))     # a bare middle initial

    good = [t for t in toks if _ok(t)]
    return good[-1] if good else ""


def decedent_of(rec: dict, research: dict) -> tuple[str, str]:
    """(full name, surname). The decedent is the person the household is named for.

    Priority: the account's own Deceased fields, then the obituary research finding, then
    the owner name -- on an obituary record the owner IS the decedent.
    """
    cf = rec.get("cf") or {}
    first = (cf.get("Deceased First Name") or "").strip()
    last = (cf.get("Deceased Last Name") or "").strip()
    if last:
        # the CRM field is not automatically a person's name -- a trust record can carry
        # "Trust-2010" in it, and "The Trust-2010s" must never reach an envelope
        sur = surname_of(last)
        if sur:
            return (f"{first} {last}".strip(), sur)
    rname = (research.get("decedent_name") or "").strip()
    olast = (rec.get("owner_last") or "").strip()
    if rname:
        sur = surname_of(rname)
        if sur:
            return (rname, sur)
    if olast:
        # owner_last is usually the bare surname, but some records carry the whole
        # vesting blob ("Kupiec Thomas Andrew & Evelyn") in the field. Never fall back to
        # the RAW value when surname_of rejects it -- that is what put "The Trust-2010s"
        # on an envelope. An empty surname routes the record to excluded.csv instead.
        full = rname or f"{(rec.get('owner_first') or '').strip()} {olast}".strip()
        return (full, surname_of(olast))
    return (rname, "")


def suppression_reason(rec: dict) -> str | None:
    """Why this record must not be mailed. Returns None when it is mailable."""
    if rec.get("_gone"):
        return "record deleted from the account"
    if rec.get("_error"):
        return f"read failed: {rec['_error']}"
    sk = _status_key(rec.get("status") or "")
    if sk in DEAD_KEYS:
        return f"dead status: {rec.get('status')}"
    if rec.get("do_not_mail_ever") or rec.get("owner_do_not_mail_ever"):
        return "do_not_mail_ever"
    if RECENTLY_SOLD_TAG in (rec.get("tags") or []):
        return f"tagged {RECENTLY_SOLD_TAG}"
    return None


def cmd_build(a) -> int:
    idx = jload(DP_INDEX, {})
    records = jload(RECORDS, {})
    if not idx.get("index") or not records:
        say("missing dp_index.json or records.json -- run `index` then `pull`")
        return 2
    dp = idx["index"]

    rows: list[dict] = []
    excluded: list[dict] = []
    tally = Counter()
    # how many pieces each distinct person receives, so repeat recipients are visible
    person_count: Counter = Counter()

    prepared = []
    # The account carries duplicate PROPERTY records (same address, two records) -- known
    # live on this cohort. Two records at one address must not produce two identical
    # envelopes, so the household piece is deduped on the property address itself.
    seen_household: set[tuple] = set()
    seen_signer: set[tuple] = set()
    for u, rec in sorted(records.items(), key=lambda kv: kv[1].get("_key") or ""):
        key = rec.get("_key") or ""
        why = suppression_reason(rec)
        if why:
            tally["record_suppressed"] += 1
            excluded.append({"scope": "record", "sift_key": key, "record_uuid": u,
                             "property_street": rec.get("street") or "",
                             "property_zip": rec.get("zip") or "", "name": "",
                             "relationship": "", "reason": why.split(":")[0],
                             "detail": why})
            continue
        entry = dp.get(key) or {}
        prepared.append((u, rec, entry))

    for u, rec, entry in prepared:
        key = rec.get("_key") or ""
        ranked = entry.get("record") or {}
        research = entry.get("research") or {}
        dec_name, dec_last = decedent_of(rec, research)
        cf = rec.get("cf") or {}
        pr_name = (rec.get("personal_representative")
                   or " ".join(x for x in [cf.get("PR First Name"), cf.get("PR Last Name")] if x)
                   or "")
        dod = (research.get("dod") or cf.get("Date of Death")
               or rec.get("last_obituary_date") or "")
        base = {
            "decedent_name": dec_name, "decedent_surname": dec_last, "date_of_death": dod,
            "property_street": rec.get("street") or "", "property_city": rec.get("city") or "",
            "property_state": rec.get("state") or "", "property_zip": rec.get("zip") or "",
            "county": rec.get("county") or "",
            "owner_first": rec.get("owner_first") or "", "owner_last": rec.get("owner_last") or "",
            "pr_name": pr_name, "status": rec.get("status") or "",
            "lists": ", ".join(rec.get("lists") or []), "tags": ", ".join(rec.get("tags") or []),
            "record_uuid": u, "sift_key": key,
            "dp_run_dir": entry.get("run_dir") or "",
        }

        # -- the household piece, at the PROPERTY address
        flags = []
        if not dec_last:
            flags.append("no_surname")
        if not (rec.get("street") and rec.get("zip")):
            flags.append("incomplete_property_address")
        elif not has_house_number(rec.get("street")):
            flags.append("property_street_has_no_house_number")
        hh = household_name(dec_last)
        hh_key = (norm(rec.get("street")), zip5(rec.get("zip") or ""))
        if hh and not flags and hh_key in seen_household:
            tally["household_duplicate_property"] += 1
            excluded.append({"scope": "household", "sift_key": key, "record_uuid": u,
                             "property_street": rec.get("street") or "",
                             "property_zip": rec.get("zip") or "", "name": hh,
                             "relationship": "", "reason": "duplicate_property",
                             "detail": "another account record shares this property "
                                       "address; one household piece already queued"})
        elif hh and not flags:
            seen_household.add(hh_key)
            rows.append(dict(base, mail_id=f"{u}|household", addressee_type="household",
                             addressee=hh, mail_street=rec.get("street") or "",
                             mail_city=rec.get("city") or "", mail_state=rec.get("state") or "",
                             mail_zip=rec.get("zip") or "", relationship="", dm_why="",
                             signer_mail_count=1, flags=""))
            tally["household_rows"] += 1
        else:
            tally["household_skipped"] += 1
            excluded.append({"scope": "household", "sift_key": key, "record_uuid": u,
                             "property_street": rec.get("street") or "",
                             "property_zip": rec.get("zip") or "", "name": hh,
                             "relationship": "", "reason": "unmailable_household",
                             "detail": ", ".join(flags) or "no decedent surname"})

        # -- one piece per ranked signer, at THEIR OWN address
        rels = ranked.get("rels") or []
        if not entry:
            tally["no_dp_entry"] += 1
        n_signers = 0
        for r in rels:
            if not r.get("is_dm"):
                continue
            n_signers += 1
            name = title_name(r.get("name") or "")
            street, zc = r.get("mailing_street") or "", zip5(r.get("mailing_zip") or "")
            if not (street and zc):
                tally["signer_no_address"] += 1
                excluded.append({"scope": "signer", "sift_key": key, "record_uuid": u,
                                 "property_street": rec.get("street") or "",
                                 "property_zip": rec.get("zip") or "", "name": name,
                                 "relationship": r.get("canon_rel") or "",
                                 "reason": "signer_no_address",
                                 "detail": "no SmartSkip mailing address for this heir"})
                continue
            if not has_house_number(street):
                tally["signer_undeliverable"] += 1
                excluded.append({"scope": "signer", "sift_key": key, "record_uuid": u,
                                 "property_street": rec.get("street") or "",
                                 "property_zip": rec.get("zip") or "", "name": name,
                                 "relationship": r.get("canon_rel") or "",
                                 "reason": "signer_undeliverable",
                                 "detail": f"street has no house number: {street!r}"})
                continue
            # Dedupe GLOBALLY on (person, property). Two account records can share one
            # property address and therefore one DP entry, which would otherwise mail the
            # same heir twice about the same house. An heir to a DIFFERENT property is a
            # different offer and still gets their own piece.
            dedupe = (norm(name), norm(street), zc,
                      norm(rec.get("street")), zip5(rec.get("zip") or ""))
            if dedupe in seen_signer:
                tally["signer_duplicate"] += 1
                continue
            seen_signer.add(dedupe)
            rows.append(dict(base,
                             mail_id=f"{u}|rel{r.get('n')}",
                             addressee_type="signer", addressee=name,
                             mail_street=title_street(street),
                             mail_city=title_name(r.get("mailing_city") or ""),
                             mail_state=(r.get("mailing_state") or "").upper(),
                             mail_zip=zc,
                             relationship=r.get("canon_rel") or "",
                             dm_why=r.get("dm_why") or "",
                             signer_mail_count=1, flags=""))
            tally["signer_rows"] += 1
        if n_signers == 0:
            tally["records_without_signer"] += 1

    # Flag the two overlaps that cost postage without losing a recipient, so they can be
    # filtered in the sheet rather than decided here:
    #   * a signer who lives AT the decedent's property already gets the household piece
    #     through the same door;
    #   * several signers can share one address (siblings in one house).
    at_addr: Counter = Counter()
    for r in rows:
        if r["addressee_type"] == "signer":
            at_addr[(r["sift_key"], norm(r["mail_street"]), r["mail_zip"])] += 1
    for r in rows:
        if r["addressee_type"] != "signer":
            continue
        f = []
        if (norm(r["mail_street"]) == norm(r["property_street"])
                and r["mail_zip"] == r["property_zip"]):
            f.append("same_door_as_household")
        if at_addr[(r["sift_key"], norm(r["mail_street"]), r["mail_zip"])] > 1:
            f.append("shares_address_with_other_signer")
        r["flags"] = "; ".join(f)

    # A person who is heir to several properties gets a piece for each, but the count is
    # stamped on every one of their rows so repeat recipients are visible before mailing.
    for r in rows:
        if r["addressee_type"] == "signer":
            person_count[(norm(r["addressee"]), norm(r["mail_street"]), r["mail_zip"])] += 1
    for r in rows:
        if r["addressee_type"] == "signer":
            r["signer_mail_count"] = person_count[
                (norm(r["addressee"]), norm(r["mail_street"]), r["mail_zip"])]

    OUT.mkdir(parents=True, exist_ok=True)
    with MAILING_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=MAIL_COLS)
        w.writeheader()
        w.writerows(rows)
    with EXCLUDED_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=EXCL_COLS)
        w.writeheader()
        w.writerows(excluded)

    mailable = len(prepared)
    audit = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "records_hydrated": len(records),
        "records_suppressed": tally["record_suppressed"],
        "records_mailable": mailable,
        "household_rows": tally["household_rows"],
        "signer_rows": tally["signer_rows"],
        "total_mailpieces": len(rows),
        "excluded_rows": len(excluded),
        "tally": dict(tally),
        "contested_keys": idx.get("contested") or [],
        "repeat_recipients": sum(1 for v in person_count.values() if v > 1),
    }
    jsave(AUDIT, audit)

    say(f"records hydrated      {len(records)}")
    say(f"  suppressed          {tally['record_suppressed']}")
    say(f"  mailable            {mailable}")
    say(f"household pieces      {tally['household_rows']}")
    say(f"signer pieces         {tally['signer_rows']}")
    say(f"TOTAL MAILPIECES      {len(rows)}")
    say(f"excluded rows         {len(excluded)}")
    say(f"  -> {MAILING_CSV}")
    say(f"  -> {EXCLUDED_CSV}")

    # reconciliation: household rows must equal mailable records minus unmailable households
    expect_hh = (mailable - tally["household_skipped"]
                 - tally["household_duplicate_property"])
    if tally["household_rows"] != expect_hh:
        say(f"! RECONCILE FAILED: household rows {tally['household_rows']} != {expect_hh}")
        return 1
    return 0


# ------------------------------------------------------------------ 4. qa

def cmd_qa(a) -> int:
    import random
    idx, records = jload(DP_INDEX, {}), jload(RECORDS, {})
    if not MAILING_CSV.exists():
        say("no mailing CSV -- run `build` first")
        return 2
    rows = list(csv.DictReader(MAILING_CSV.open(encoding="utf-8-sig")))
    by_uuid: dict[str, list[dict]] = {}
    for r in rows:
        by_uuid.setdefault(r["record_uuid"], []).append(r)

    # a suppressed status must not survive anywhere in the deliverable
    bad = [r for r in rows if _status_key(r["status"]) in DEAD_KEYS]
    say(f"rows carrying a dead status: {len(bad)} (expected 0)")
    sold = [r for r in rows if RECENTLY_SOLD_TAG in (r["tags"] or "")]
    say(f"rows tagged {RECENTLY_SOLD_TAG}: {len(sold)} (expected 0)")

    api = Api()
    say("JWT minted OK")
    sample = random.sample(sorted(by_uuid), min(a.sample, len(by_uuid)))
    problems = []
    for u in sample:
        live = hydrate_one(api, u)
        if live.get("_error") or live.get("_gone"):
            problems.append(f"{u}: {live}")
            continue
        r = by_uuid[u][0]
        if norm(live.get("street")) != norm(r["property_street"]):
            problems.append(f"{u}: street {live.get('street')!r} != CSV {r['property_street']!r}")
        if norm(live.get("owner_last")) != norm(r["owner_last"]):
            problems.append(f"{u}: owner {live.get('owner_last')!r} != CSV {r['owner_last']!r}")
        if _status_key(live.get("status") or "") in DEAD_KEYS:
            problems.append(f"{u}: status went dead since the pull ({live.get('status')})")
        # every signer row must match its ranked_records source exactly
        entry = (idx.get("index") or {}).get(r["sift_key"]) or {}
        src = {norm(x.get("name")): x for x in (entry.get("record") or {}).get("rels") or []}
        for row in by_uuid[u]:
            if row["addressee_type"] != "signer":
                continue
            m = src.get(norm(row["addressee"]))
            if not m:
                problems.append(f"{u}: signer {row['addressee']} not in ranked_records")
            elif norm(m.get("mailing_street")) != norm(row["mail_street"]):
                problems.append(f"{u}: {row['addressee']} address drift "
                                f"{m.get('mailing_street')!r} vs {row['mail_street']!r}")
        say(f"  checked {u} ({len(by_uuid[u])} pieces)")

    say(f"\nsampled {len(sample)} records, {len(problems)} problems")
    for p in problems:
        say(f"  ! {p}")
    return 0 if not problems and not bad and not sold else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index", help="offline: index every DP run dir's heirs")
    p = sub.add_parser("pull", help="live read: obituary cohort + hydrate the intersection")
    p.add_argument("--threads", type=int, default=2,
                   help="keep low; other live jobs share the account's rate limit")
    p.add_argument("--limit", type=int)
    p.add_argument("--fresh", action="store_true")
    sub.add_parser("build", help="emit the mailing CSV")
    q = sub.add_parser("qa", help="spot-check the CSV against the live account")
    q.add_argument("--sample", type=int, default=5)
    a = ap.parse_args()
    return {"index": cmd_index, "pull": cmd_pull,
            "build": cmd_build, "qa": cmd_qa}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
