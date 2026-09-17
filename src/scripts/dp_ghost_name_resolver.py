"""Propose a first name for every "Unknown <Surname>" DP relative -- READ-ONLY, no spend.

Background (2026-09-17, handoff memory project_unknown_first_name_ghosts; step 1 of 5): 130 relatives across the
DP run dirs are named `Unknown <Surname>`. Every one is a SmartSkip Subject row that came
back with a blank First Name and was kept as a Child signer by `_reroot_namesake`; 113 sit
live in `REL{n}: Full Name` fields with `Rel{n}.{m}` phones, so dialers ring numbers with no
name and the mailing prints "The <Surname> Family". Our own pipeline had the names and never
joined them. This script does the join, offline, and writes a review CSV for Basem.

Sources, in order of trust:
  1. Vendor skip-trace index (`output/rel_pr_name_repair/index.json`, built by
     rel_pr_name_repair.py from the Drive ReadyForDialing / SkipTraced files): the ghost's
     phones -> same-surname persons. A phone that NO named relative on the record shares
     ("unique") is strong evidence; a household line shared with a named relative is weak.
     Vendor spellings of one person (GARY TOMITA / GARY EUGENE TOMITA, GIUSEPPE BORIS
     COLARICH / BORIS E COLARICH) are merged before ranking.
  2. Obituary survivors (`stage_c_research.json` findings): same-surname survivors not
     already paired to a named relative; a son/daughter word makes a child candidate. A
     vendor name the obituary lists under "preceded in death" is dropped.
  3. SmartSkip Subject-row emails (`smartskip_vertical_*.csv`): handles such as
     EBONY.BROWN@ or CMSNOOTS@ are hints only, never accepted alone.

Confidence tiers (the CSV sorts in this order):
  HIGH        vendor on a unique phone, no competing vendor name on a unique phone, and no
              obituary child that disagrees
  MEDIUM      vendor on shared phones only, or vendor ambiguous, resolved by the obituary or
              an email hint agreeing; or a single obituary child corroborated by an email
  NAMESAKE    the vendor puts the ghost's unique phones on a person carrying the OWNER'S
              name (no Jr./Sr.) -- a namesake child, or the owner himself if the re-root
              misfired. The first name is right either way; the relationship needs a look.
  DUPLICATE   the vendor puts the ghost's unique phones on a relative ALREADY NAMED on the
              record -- SmartSkip listed one person twice. Proposed name = that relative;
              the fix is a merge, not a rename. Ages of both are in the note.
  CONFLICT    two sources name different people -> review, no proposal
  LOW         one weak source alone (vendor on a household line; a single obituary child)
  HINT        only an email handle -> no proposal, hints listed
  UNRESOLVED  nothing found

Nothing here writes to the account or calls a paid API. Trestle reverse-phone is NOT an
option for the residue (Basem, 2026-09-17: its licence does not allow commercial use); the
free follow-ups are the funeral-home page rescrape for obituaries that came back without a
survivors list, and hand review. What stays unnamed leaves the signer list (step 5). Outputs under output/unknown_ghosts/:
  ghost_name_resolution.csv   the review sheet (one row per ghost)
  ghost_name_resolution.json  full evidence per ghost, for the write step later
  vendor_hits_cache.json      the slice of the 256MB index this cohort needs (re-run fast)

    python src/scripts/dp_ghost_name_resolver.py                 # all 130
    python src/scripts/dp_ghost_name_resolver.py --key "27hobartct|21133" -v
    python src/scripts/dp_ghost_name_resolver.py --refresh-index  # re-slice index.json
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from obituary_dp_batch import name_tokens, names_match, names_match_loose, sift_key  # noqa: E402
from rel_pr_name_repair import clean_name, title_name  # noqa: E402

GHOSTS = ROOT / "output" / "obituary_mail_consolidate" / "unknown_first_name_ghosts.json"
INDEX = ROOT / "output" / "rel_pr_name_repair" / "index.json"
OUT = ROOT / "output" / "unknown_ghosts"
CACHE = OUT / "vendor_hits_cache.json"

UNKNOWN_FIRST = {"unknown", "unk", "", "n/a", "na", "none"}
CHILD_WORDS = {"son", "daughter", "child", "stepson", "stepdaughter"}
# survivor words that can never be the owner's child -- kept out of the candidate list
NON_PERSON_WORDS = {"special friend", "family friend", "former wife"}
CONF_ORDER = ["HIGH", "MEDIUM", "NAMESAKE", "DUPLICATE", "CONFLICT", "LOW", "HINT", "UNRESOLVED"]
OPEN_TIERS = {"CONFLICT", "HINT", "UNRESOLVED"}          # step-2 (Trestle) candidates
SUFFIX_TOKENS = {"jr", "ii", "iii", "iv", "2nd", "3rd", "4th"}
GENERIC_HANDLE_WORDS = {"the", "and", "family", "mail", "email", "info", "home", "mom", "dad",
                        "mr", "mrs", "ms", "me", "my", "love", "baby", "lady", "miss", "big",
                        "lil", "little", "mama", "papa", "gmail", "yahoo", "aol", "hotmail"}
DUP_AGE_GAP = 8        # vendor name = a named relative AND ages within this -> one person listed twice

HEADER = ["confidence", "proposed_first", "proposed_full", "ghost_name", "ghost_age", "key",
          "property", "run", "live_rel_slot", "record_group", "owner_name", "decedent_name",
          "verdict", "deciding_phones", "unique_phones_n", "shared_phones_n", "sources",
          "vendor_top", "vendor_alternates", "vendor_duplicates_of_named", "vendor_owner_name_hits",
          "vendor_other_surname_on_unique", "obituary_candidates", "obituary_predeceased_hits",
          "email_hints", "emails", "named_relatives", "note", "obituary_url"]


def say(msg: str) -> None:
    print(msg, flush=True)


def d10(n) -> str:
    d = re.sub(r"\D", "", str(n or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else ""


def surname_of(name: str) -> str:
    toks = name_tokens(name)
    return toks[-1] if toks else ""


def given_tokens(name: str) -> list[str]:
    """Given-name tokens (first + middles), initials dropped."""
    return [t for t in name_tokens(name)[:-1] if len(t) > 1]


def first_of(name: str) -> str:
    toks = given_tokens(name) or name_tokens(name)
    return toks[0] if toks else ""


def has_suffix(raw: str) -> bool:
    return bool({t.lower().strip(".,") for t in str(raw or "").split()} & SUFFIX_TOKENS)


def rel_phones(r: dict) -> set[str]:
    return {d10(p.get("number") if isinstance(p, dict) else p) for p in (r.get("phones") or [])} - {""}


def first_equiv(a: str, b: str) -> bool:
    """Two given names name the same person (nickname / prefix tolerant, via names_match_loose)."""
    if not a or not b:
        return False
    return names_match_loose(f"{a} zz", f"{b} zz")


ALL_SUFFIXES = SUFFIX_TOKENS | {"sr"}


def suffix_of(raw: str) -> str:
    toks = {t.lower().strip(".,") for t in str(raw or "").split()} & ALL_SUFFIXES
    return "/".join(sorted(toks))


def same_person(a: str, b: str) -> bool:
    """Same surname and either a loose first-name match, or one side's FIRST given name is
    among the other's given names: LON MICHAEL HEAD SR ~ MICHAEL HEAD, GIUSEPPE BORIS
    COLARICH ~ BORIS E COLARICH -- but not BENJAMIN LEE WELZEL ~ RAYMOND LEE WELZEL (a shared
    middle name is a family habit, not an identity), and never Sr against Jr."""
    if not a or not b or surname_of(a) != surname_of(b):
        return False
    sa, sb = suffix_of(a), suffix_of(b)
    if sa and sb and sa != sb:
        return False
    if names_match_loose(a, b):
        return True
    ga, gb = given_tokens(a), given_tokens(b)
    if not ga or not gb:
        return False
    return ga[0] in gb or gb[0] in ga


def age_note(label: str, age) -> str:
    return f"{label} {age}" if str(age or "").strip() else f"{label} ?"


def age_int(a) -> int | None:
    m = re.search(r"\d+", str(a or ""))
    return int(m.group()) if m else None


def ages_fit(a, b, gap: int = DUP_AGE_GAP) -> bool:
    """True when either age is unknown or they are within `gap` years."""
    x, y = age_int(a), age_int(b)
    return x is None or y is None or abs(x - y) <= gap


# ───────────────────────── run-dir loading ─────────────────────────

def jload(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


class RunDir:
    """Everything one DP run dir knows about its records, loaded once."""

    def __init__(self, run: Path):
        self.run = run
        self.ranked = jload(run / "ranked_records.json", {}) or {}
        self.records = jload(run / "records.json", {}) or {}
        sc = jload(run / "stage_c_research.json", {}) or {}
        self.findings = {f["record"]: f for f in sc.get("findings", []) if f.get("record")}
        pp = jload(run / "push_plan.json", {}) or {}
        self.plan = pp.get("records", {}) if isinstance(pp, dict) else {}
        self.subject_rows: dict[str, list[dict]] = defaultdict(list)
        for f in sorted(glob.glob(str(run / "smartskip_vertical_*.csv"))):
            with open(f, encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh):
                    if (row.get("Relationship") or "").strip().lower() != "subject":
                        continue
                    k = sift_key(row.get("Property Address") or "", row.get("Property Zip") or "")
                    self.subject_rows[k].append(row)

    def rels(self, key: str) -> list[dict]:
        rec = self.ranked.get(key) or {}
        return rec.get("rels") or rec.get("relatives") or []

    def ghost_rel(self, key: str, ghost: dict) -> dict | None:
        same = [r for r in self.rels(key) if (r.get("name") or "").strip().lower() == ghost["name"].lower()]
        if len(same) > 1:
            same = [r for r in same if str(r.get("age") or "") == str(ghost.get("age") or "")] or same
        return same[0] if same else None

    def named_rels(self, key: str, ghost_rel: dict | None) -> list[dict]:
        out = [r for r in self.rels(key) if r is not ghost_rel
               and first_of(r.get("name") or "") not in UNKNOWN_FIRST]
        dec = (self.ranked.get(key) or {}).get("decedent_from_smartskip")
        if dec and dec.get("name"):
            out.append({"name": dec["name"], "phones": dec.get("phones") or [], "age": dec.get("age"),
                        "canon_rel": "Decedent (SmartSkip)"})
        return out

    def live_slot(self, key: str, ghost_name: str) -> str:
        cf = ((self.plan.get(key) or {}).get("custom_fields") or {})
        for label, val in cf.items():
            if label.endswith(": Full Name") and str(val or "").strip().upper() == ghost_name.upper():
                return label.split(":")[0]
        return ""

    def subject_emails(self, key: str, ghost: dict) -> list[str]:
        rows = self.subject_rows.get(key) or []
        blank = [r for r in rows if not (r.get("First Name") or "").strip()]
        aged = [r for r in blank if (r.get("Age") or "").strip() == str(ghost.get("age") or "")]
        pick = aged or blank or rows
        emails: list[str] = []
        for r in pick:
            for i in range(1, 16):
                e = (r.get(f"Email {i}") or "").strip().lower()
                if e and e not in emails:
                    emails.append(e)
        return emails


# ───────────────────────── vendor index ─────────────────────────

def vendor_slice(phones: set[str], refresh: bool) -> dict[str, list[dict]]:
    """phone -> vendor persons, sliced out of the 256MB index once and cached."""
    if CACHE.exists() and not refresh:
        cached = jload(CACHE, {})
        if cached and phones <= set(cached.get("phones", [])):
            return cached["hits"]
    say(f"loading vendor index {INDEX} (slow, once) ...")
    idx = jload(INDEX)
    if not idx:
        sys.exit(f"vendor index missing: {INDEX} -- run rel_pr_name_repair.py index first")
    persons, by_phone = idx["persons"], idx["by_phone"]
    hits: dict[str, list[dict]] = {}
    for ph in sorted(phones):
        rows = []
        for pid in by_phone.get(ph, []):
            p = persons[pid]
            rows.append({"name": p.get("name") or "", "first": p.get("first") or "", "last": p.get("last") or "",
                         "role": p.get("role") or "", "anchor": p.get("anchor") or "",
                         "prop_key": p.get("prop_key") or "", "input_key": p.get("input_key") or "",
                         "file": p.get("file") or "", "phones": p.get("phones") or []})
        hits[ph] = rows
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"built": datetime.now().isoformat(timespec="seconds"),
                                 "index_built": idx.get("built"), "phones": sorted(phones), "hits": hits},
                                ensure_ascii=False), encoding="utf-8")
    say(f"vendor slice: {len(phones)} phones, {sum(len(v) for v in hits.values())} person rows -> {CACHE}")
    return hits


def _new_group(nm: str, **flags) -> dict:
    return {"name": title_name(nm), "first": title_name(first_of(nm)), "unique_phones": set(),
            "shared_phones": set(), "roles": set(), "files": set(), "anchored_here": False,
            "raw_names": {nm}, **flags}


def _merge_groups(groups: list[dict]) -> list[dict]:
    """Fold vendor spellings of one person together (same_person). Display name = the
    longest spelling; first name = the given token shared by most spellings."""
    merged: list[dict] = []
    for g in groups:
        for m in merged:
            if any(same_person(a, b) for a in g["raw_names"] for b in m["raw_names"]):
                for f in ("unique_phones", "shared_phones", "roles", "files", "raw_names"):
                    m[f] |= g[f]
                m["anchored_here"] = m["anchored_here"] or g["anchored_here"]
                for fl in ("namesake_jr", "namesake_suffixed"):
                    m[fl] = m.get(fl) or g.get(fl)
                m["namesake_of"] = m.get("namesake_of") or g.get("namesake_of")
                m["namesake_of_age"] = m.get("namesake_of_age") or g.get("namesake_of_age")
                break
        else:
            merged.append(g)
    for m in merged:
        longest = max(m["raw_names"], key=len)
        m["name"] = title_name(longest)
        tally = Counter(t for nm in m["raw_names"] for t in set(given_tokens(nm)))
        firsts = Counter(first_of(nm) for nm in m["raw_names"])
        best = max(tally, key=lambda t: (tally[t], firsts.get(t, 0), t == first_of(longest))) if tally else first_of(longest)
        m["first"] = title_name(best)
        if len(m["raw_names"]) > 1:
            m["name"] += " (a.k.a. " + ", ".join(title_name(n) for n in sorted(m["raw_names"]) if n != longest) + ")"
    return merged


def vendor_candidates(ghost: dict, key: str, unique: set[str], shared: set[str], owner: str,
                      named: list[dict], preceded: list[str], survivors: list[str],
                      hits: dict[str, list[dict]]) -> dict:
    """Group vendor rows on the ghost's phones by person.

    Returns {"cands": same-surname candidates ranked, "dups": vendor rows that ARE an
    already-named relative (with that relative's age), "owner_hits": rows carrying the
    owner's own name, "predeceased": rows the obituary lists as preceded in death,
    "other_surname": other-surname names on UNIQUE phones}.
    """
    surname = surname_of(ghost["name"])
    groups: list[dict] = []
    dups: dict[str, dict] = {}
    owner_hits = _new_group(owner or "owner") if owner else None
    owner_hits_seen = False
    pre: Counter = Counter()
    other_surname: Counter = Counter()
    for ph in sorted(unique | shared):
        for p in hits.get(ph, []):
            nm = clean_name(p["name"])
            if not nm or len(name_tokens(nm)) < 2:
                continue
            if not re.fullmatch(r"[A-Za-z][A-Za-z'.-]*", p.get("first") or first_of(nm) or "-"):
                continue
            if surname_of(nm) != surname:
                if ph in unique:
                    other_surname[title_name(nm)] += 1
                continue
            if p["role"] == "deceased":
                continue                      # a decedent row is never the living child
            # preceded-in-death: strict match, and never when the same spelling also fits a
            # SURVIVOR (Milton Craig Amoss is the son "Craig", not the late Milton J. Amoss)
            if any(names_match(nm, x) for x in preceded) and not any(same_person(nm, x) for x in survivors):
                pre[title_name(nm)] += 1
                continue
            bucket = "unique_phones" if ph in unique else "shared_phones"
            # the owner's own name (suffix-for-suffix) -> a namesake, or the owner -- its own tier
            if owner and names_match(nm, owner) and (not has_suffix(nm) or has_suffix(owner)):
                owner_hits[bucket].add(ph)
                owner_hits["roles"].add(p["role"])
                owner_hits["files"].add(Path(p["file"]).name)
                owner_hits["raw_names"].add(nm)
                owner_hits_seen = True
                continue
            # a relative already named on the record (any spelling) -> that relative, i.e. a
            # duplicate identity when the phone is the ghost's own (unique) and the ages fit.
            # On a SHARED household line the named relative is simply the line's other holder,
            # so the fold applies whatever the ages. Kept as a candidate instead when: the
            # vendor spelling carries a suffix the relative lacks (the Jr.); a UNIQUE phone
            # with a big age gap under the SAME first+last (an unsuffixed namesake -> own
            # tier); or the spelling is a DIFFERENT obituary survivor (Milton Craig Amoss is
            # the son Craig, not the named father Milton). A "Sr." is never the child.
            def _is_other_survivor(vn: str, rn: str) -> bool:
                return any(same_person(vn, sv) and not same_person(sv, rn) for sv in survivors)
            twin = next((n for n in named if same_person(nm, n.get("name") or "")
                         and not (has_suffix(nm) and not has_suffix(n.get("name") or ""))
                         and not _is_other_survivor(nm, n.get("name") or "")
                         and (ages_fit(ghost.get("age"), n.get("age")) or suffix_of(nm) == "sr"
                              or ph in shared)), None)
            if twin:
                d = dups.setdefault(twin["name"], {"named": twin["name"], "named_age": str(twin.get("age") or ""),
                                                  "canon_rel": twin.get("canon_rel") or "", "vendor_names": set(),
                                                  "unique_phones": set(), "shared_phones": set()})
                d["vendor_names"].add(title_name(nm))
                d[bucket].add(ph)
                continue
            g = next((g for g in groups if any(same_person(nm, x) for x in g["raw_names"])), None)
            if g is None:
                twin_name = next((n["name"] for n in named if names_match(nm, n.get("name") or "")), "")
                twin_age = next((n.get("age") for n in named if names_match(nm, n.get("name") or "")), None)
                g = _new_group(nm, namesake_jr=bool(owner and names_match(nm, owner)),
                               namesake_of=twin_name, namesake_of_age=str(twin_age or ""),
                               namesake_suffixed=bool(twin_name) and has_suffix(nm))
                groups.append(g)
            g[bucket].add(ph)
            g["roles"].add(p["role"])
            g["files"].add(Path(p["file"]).name)
            g["raw_names"].add(nm)
            if key in (p["prop_key"], p["input_key"]):
                g["anchored_here"] = True
    cands = _merge_groups(groups)
    for g in cands:
        g["score"] = (len(g["unique_phones"]), int(g["anchored_here"]), len(g["shared_phones"]), len(g["files"]))
        for f in ("unique_phones", "shared_phones", "roles", "files", "raw_names"):
            g[f] = sorted(g[f])
    cands.sort(key=lambda g: g["score"], reverse=True)
    for d in dups.values():
        for f in ("vendor_names", "unique_phones", "shared_phones"):
            d[f] = sorted(d[f])
    if owner_hits_seen:
        for f in ("unique_phones", "shared_phones", "roles", "files", "raw_names"):
            owner_hits[f] = sorted(owner_hits[f])
    return {"cands": cands, "dups": sorted(dups.values(), key=lambda d: -len(d["unique_phones"])),
            "owner_hits": owner_hits if owner_hits_seen else None,
            "predeceased": [f"{n} x{c}" for n, c in pre.most_common()],
            "other_surname": [f"{n} x{c}" for n, c in other_surname.most_common()]}


# ───────────────────────── obituary + emails ─────────────────────────

def preceded_names(finding: dict) -> list[str]:
    out = []
    for p in finding.get("preceded") or []:
        nm = clean_name(p.get("name") if isinstance(p, dict) else p)
        nm = re.sub(r"\([^)]*\)", " ", nm).strip()
        if nm:
            out.append(nm)
    return out


def obituary_candidates(finding: dict, ghost: dict, owner: str, named: list[dict]) -> list[dict]:
    surname = surname_of(ghost["name"])
    named_names = [n.get("name") or "" for n in named]
    out = []
    for sv in finding.get("survivors") or []:
        nm = clean_name(sv.get("name") if isinstance(sv, dict) else sv)
        word = ((sv.get("relationship") if isinstance(sv, dict) else "") or "").strip().lower()
        nm = re.sub(r"\([^)]*\)", " ", nm).strip()       # "John (Mary) Glenn" -> John Glenn
        if not nm or len(name_tokens(nm)) < 2 or word in NON_PERSON_WORDS:
            continue
        if surname_of(nm) != surname:
            continue
        if owner and names_match(nm, owner) and not has_suffix(nm):
            continue
        if any(names_match_loose(nm, n) for n in named_names):
            continue
        out.append({"name": title_name(nm), "first": title_name(first_of(nm)), "word": word,
                    "is_child": word in CHILD_WORDS})
    out.sort(key=lambda c: (not c["is_child"], c["name"]))
    return out


def email_hints(emails: list[str], surname: str, exclude_firsts: set[str]) -> list[dict]:
    """Given-name fragments in email handles. `initial` = one letter before the surname,
    `initials` = two (CMSNOOTS -> C.M.)."""
    hints: list[dict] = []
    seen = set()

    def add(frag: str, email: str, kind: str):
        frag = frag.lower()
        if not frag or frag in GENERIC_HANDLE_WORDS or frag in exclude_firsts or frag == surname:
            return
        if kind not in ("initial", "initials") and len(frag) < 3:
            return
        if (frag, kind) in seen:
            return
        seen.add((frag, kind))
        hints.append({"frag": frag, "kind": kind, "email": email})

    for e in emails:
        handle = e.split("@")[0]
        letters = re.sub(r"[^a-z]", "", handle)
        toks = [t for t in re.split(r"[^a-z]+", handle) if t]
        if surname and letters.endswith(surname) and len(letters) > len(surname):
            pre = letters[: -len(surname)]
            add(pre, e, {1: "initial", 2: "initials"}.get(len(pre), "before_surname"))
        if surname and letters.startswith(surname) and len(letters) > len(surname):
            add(letters[len(surname):], e, "after_surname")
        for t in toks:
            if t != surname and (not surname or surname not in t):
                add(t, e, "token")
    return hints


def hint_agrees(hints: list[dict], full_name: str) -> str:
    """The email that supports this candidate, or ''."""
    given = given_tokens(full_name)
    if not given:
        return ""
    f = given[0]
    for h in hints:
        frag = h["frag"]
        if h["kind"] == "initial":
            if frag == f[0]:
                return f"{h['email']} (initial)"
            continue
        if h["kind"] == "initials":
            if frag[0] == f[0] and (len(given) < 2 or frag[1] == given[1][0]):
                return f"{h['email']} (initials)"
            continue
        if frag == f or (len(frag) >= 3 and f.startswith(frag)) or (len(f) >= 3 and frag.startswith(f)) \
                or first_equiv(frag, f):
            return h["email"]
    return ""


# ───────────────────────── decision ─────────────────────────

def decide(v: dict, obit: list[dict], hints: list[dict], ghost_age: str, owner: str) -> dict:
    vend = v["cands"]
    strong = [c for c in vend if c["unique_phones"]]
    weak = [c for c in vend if not c["unique_phones"]]
    dups_strong = [d for d in v["dups"] if d["unique_phones"]]
    owner_strong = v["owner_hits"] if v["owner_hits"] and v["owner_hits"]["unique_phones"] else None
    obit_children = [o for o in obit if o["is_child"]]
    obit_pool = obit_children or obit
    note: list[str] = []

    def agrees_obit(full: str) -> dict | None:
        for o in obit_pool:
            if first_equiv(first_of(full), o["first"]) or same_person(full, o["name"]):
                return o
        return None

    def flag_notes(c: dict) -> None:
        if c.get("namesake_jr"):
            note.append("vendor name is the owner's namesake with a suffix (Jr./II)")
        if c.get("namesake_of"):
            if c.get("namesake_suffixed"):
                note.append(f"vendor name is a suffixed namesake of named relative {c['namesake_of']}")
            else:
                note.append(f"same first+last as named relative {c['namesake_of']} "
                            f"({age_note('age', c.get('namesake_of_age'))}) but ghost {age_note('age', ghost_age)} "
                            f"-- a namesake child of that relative, or the vendor conflated two people")

    def result(conf, cand_first, cand_full, sources, phones, cand: dict | None = None):
        if cand is not None:
            flag_notes(cand)
            if conf in ("HIGH", "MEDIUM", "LOW") and cand.get("namesake_of") and not cand.get("namesake_suffixed"):
                conf = "NAMESAKE"
        return {"confidence": conf, "first": cand_first, "full": cand_full, "sources": sources,
                "phones": sorted(phones), "note": "; ".join(note)}

    if dups_strong:
        note.append("vendor puts the ghost's unique phone(s) on already-named "
                    + ", ".join(f"{d['named']} ({d['canon_rel']}, {age_note('age', d['named_age'])}; vendor spells it "
                                f"{'/'.join(d['vendor_names'])})" for d in dups_strong)
                    + f"; ghost {age_note('age', ghost_age)}")
    if owner_strong:
        note.append(f"vendor puts the ghost's unique phone(s) on {'/'.join(owner_strong['raw_names'])} "
                    f"({'/'.join(owner_strong['roles'])}) -- the owner's own name"
                    + (" (suffix and all: this may be the owner himself, demoted by the re-root)" if has_suffix(owner)
                       else " with no suffix: a namesake child, or the owner himself if the re-root misfired")
                    + f"; ghost {age_note('age', ghost_age)}")

    if strong:
        top = strong[0]
        if len(strong) == 1:
            srcs = ["vendor:unique"]
            o = agrees_obit(top["name"])
            e = hint_agrees(hints, top["name"])
            if o:
                srcs.append(f"obituary:{o['word']}")
            if e:
                srcs.append("email")
            if obit_children and not o and len(obit_children) == 1:
                note.append(f"obituary's only child candidate is {obit_children[0]['name']}, vendor says {top['name']}")
                return result("CONFLICT", "", "", srcs + ["obituary:disagrees"], top["unique_phones"])
            if o and o["word"] and o["word"] not in CHILD_WORDS:
                note.append(f"obituary calls this person '{o['word']}', the record has Child")
            if len(vend) > 1:
                note.append("other same-surname names on household lines: " + ", ".join(c["name"] for c in vend[1:]))
            return result("HIGH", top["first"], top["name"], srcs, top["unique_phones"], top)
        picks = []
        for c in strong:
            o, e = agrees_obit(c["name"]), hint_agrees(hints, c["name"])
            if o or e:
                picks.append((c, o, e))
        if len(picks) == 1:
            c, o, e = picks[0]
            srcs = ["vendor:unique(ambiguous)"] + ([f"obituary:{o['word']}"] if o else []) + (["email"] if e else [])
            note.append("vendor ambiguous: " + ", ".join(f"{s['name']} on {len(s['unique_phones'])} unique" for s in strong))
            return result("MEDIUM", c["first"], c["name"], srcs, c["unique_phones"], c)
        note.append("vendor ambiguous, nothing breaks the tie: "
                    + ", ".join(f"{s['name']} on {','.join(s['unique_phones'])}" for s in strong))
        return result("CONFLICT", "", "", ["vendor:ambiguous"], {p for s in strong for p in s["unique_phones"]})

    if dups_strong and not owner_strong:
        d = dups_strong[0]
        if len(dups_strong) > 1:
            note.append("more than one named relative claims the unique phones -- review")
            return result("CONFLICT", "", "", ["vendor:duplicate(ambiguous)"], {p for x in dups_strong for p in x["unique_phones"]})
        return result("DUPLICATE", title_name(first_of(d["named"])), title_name(d["named"]), ["vendor:duplicate"], d["unique_phones"])
    if owner_strong and not dups_strong:
        return result("NAMESAKE", title_name(first_of(owner)), title_name(max(owner_strong["raw_names"], key=len)),
                      ["vendor:owner-name"], owner_strong["unique_phones"])
    if owner_strong and dups_strong:
        note.append("both the owner's name and a named relative claim the unique phones -- review")
        return result("CONFLICT", "", "", ["vendor:owner-name", "vendor:duplicate"],
                      set(owner_strong["unique_phones"]) | {p for x in dups_strong for p in x["unique_phones"]})

    if weak:
        picks = []
        for c in weak:
            o, e = agrees_obit(c["name"]), hint_agrees(hints, c["name"])
            if o or e:
                picks.append((c, o, e))
        if len(picks) == 1:
            c, o, e = picks[0]
            srcs = ["vendor:shared"] + ([f"obituary:{o['word']}"] if o else []) + (["email"] if e else [])
            note.append("vendor hit only on household lines shared with named relatives")
            return result("MEDIUM", c["first"], c["name"], srcs, c["shared_phones"], c)
        if len(weak) == 1 and not picks:
            if len(obit_children) == 1:
                note.append(f"vendor (household line) says {weak[0]['name']}, obituary's only child is {obit_children[0]['name']}")
                return result("CONFLICT", "", "", ["vendor:shared", "obituary:disagrees"], weak[0]["shared_phones"])
            note.append("single vendor name on household lines only; nothing corroborates")
            return result("LOW", weak[0]["first"], weak[0]["name"], ["vendor:shared"], weak[0]["shared_phones"], weak[0])
        note.append("several vendor names on household lines: " + ", ".join(c["name"] for c in weak))

    if len(obit_children) == 1:
        o = obit_children[0]
        e = hint_agrees(hints, o["name"])
        if e:
            return result("MEDIUM", o["first"], o["name"], [f"obituary:{o['word']}", "email"], [])
        note.append("single obituary child, no phone evidence")
        return result("LOW", o["first"], o["name"], [f"obituary:{o['word']}"], [])
    if len(obit_children) > 1:
        picks = [o for o in obit_children if hint_agrees(hints, o["name"])]
        if len(picks) == 1:
            note.append("obituary lists several children; the email handle picks one")
            return result("LOW", picks[0]["first"], picks[0]["name"], [f"obituary:{picks[0]['word']}", "email"], [])
        note.append("obituary lists several children, nothing picks one: " + ", ".join(o["name"] for o in obit_children))
    elif obit:
        note.append("obituary same-surname survivors are not children: "
                    + ", ".join(f"{o['name']} ({o['word']})" for o in obit))

    if hints:
        return result("HINT", "", "", ["email"], [])
    return result("UNRESOLVED", "", "", [], [])


# ───────────────────────── main ─────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ghosts", default=str(GHOSTS))
    ap.add_argument("--key", help="one record key, e.g. 27hobartct|21133")
    ap.add_argument("--refresh-index", action="store_true", help="re-slice index.json even if the cache covers it")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    ghosts = jload(Path(a.ghosts))
    if not ghosts:
        sys.exit(f"no ghost list at {a.ghosts}")
    if a.key:
        ghosts = [g for g in ghosts if g["key"] == a.key]
        if not ghosts:
            sys.exit(f"key {a.key} not in the ghost list")
    say(f"{len(ghosts)} ghosts across {len({g['run'] for g in ghosts})} run dirs")

    runs: dict[str, RunDir] = {}
    for g in ghosts:
        if g["run"] not in runs:
            runs[g["run"]] = RunDir(ROOT / g["run"])

    all_phones = {d10(p) for g in ghosts for p in g.get("phones") or []} - {""}
    hits = vendor_slice(all_phones, a.refresh_index)

    rows, detail = [], []
    for g in ghosts:
        rd, key = runs[g["run"]], g["key"]
        crm = rd.records.get(key) or {}
        owner = clean_name(f"{crm.get('ss_first') or crm.get('owner_first') or ''} "
                           f"{crm.get('ss_last') or crm.get('owner_last') or ''}")
        grel = rd.ghost_rel(key, g)
        named = rd.named_rels(key, grel)
        ghost_phones = {d10(p) for p in g.get("phones") or []} - {""}
        others = set().union(*(rel_phones(r) for r in named)) if named else set()
        unique, shared = ghost_phones - others, ghost_phones & others
        finding = rd.findings.get(key) or {}
        surname = surname_of(g["name"])
        exclude_firsts = ({first_of(owner)} | {first_of(n.get("name") or "") for n in named}) - {""}
        emails = rd.subject_emails(key, g)
        preceded = preceded_names(finding)
        survivors = [re.sub(r"\([^)]*\)", " ", clean_name(sv.get("name") if isinstance(sv, dict) else sv)).strip()
                     for sv in (finding.get("survivors") or [])]

        v = vendor_candidates(g, key, unique, shared, owner, named, preceded, [x for x in survivors if x], hits)
        obit = obituary_candidates(finding, g, owner, named)
        hints = email_hints(emails, surname, exclude_firsts)
        dec = decide(v, obit, hints, str(g.get("age") or ""), owner)

        note_parts = []
        if not ghost_phones:
            note_parts.append("ghost carries no phones")
        elif not unique:
            note_parts.append("household ghost: every phone is shared with a named relative")
        if dec["note"]:
            note_parts.append(dec["note"])
        if v["predeceased"]:
            note_parts.append("vendor names dropped as predeceased per obituary: " + ", ".join(v["predeceased"]))
        if not finding:
            note_parts.append("never researched")
        vend = v["cands"]
        top = vend[0] if vend else None
        oh = v["owner_hits"]

        row = {
            "confidence": dec["confidence"], "proposed_first": dec["first"], "proposed_full": dec["full"],
            "ghost_name": g["name"], "ghost_age": g.get("age") or "", "key": key,
            "property": g.get("mailing") or f"{crm.get('street', '')} {crm.get('city', '')} "
                                            f"{crm.get('state', '')} {crm.get('zip', '')}".strip(),
            "run": g["run"], "live_rel_slot": rd.live_slot(key, g["name"]),
            "record_group": (rd.plan.get(key) or {}).get("group") or "",
            "owner_name": owner, "decedent_name": finding.get("decedent_name") or "",
            "verdict": finding.get("verdict") or "", "deciding_phones": " ".join(dec["phones"]),
            "unique_phones_n": len(unique), "shared_phones_n": len(shared),
            "sources": " + ".join(dec["sources"]),
            "vendor_top": (f"{top['name']} [unique {len(top['unique_phones'])}, shared {len(top['shared_phones'])}, "
                           f"{'/'.join(top['roles'])}{', here' if top['anchored_here'] else ''}]") if top else "",
            "vendor_alternates": " | ".join(
                f"{c['name']} [u{len(c['unique_phones'])} s{len(c['shared_phones'])} {'/'.join(c['roles'])}]"
                for c in vend[1:]),
            "vendor_duplicates_of_named": " | ".join(
                f"{d['named']} ({d['canon_rel']}, {age_note('age', d['named_age'])}) as {'/'.join(d['vendor_names'])} "
                f"[u{len(d['unique_phones'])} s{len(d['shared_phones'])}]" for d in v["dups"]),
            "vendor_owner_name_hits": (f"{'/'.join(oh['raw_names'])} [u{len(oh['unique_phones'])} s{len(oh['shared_phones'])} "
                                       f"{'/'.join(oh['roles'])}]") if oh else "",
            "vendor_other_surname_on_unique": " | ".join(v["other_surname"]),
            "obituary_candidates": " | ".join(f"{o['name']} ({o['word']})" for o in obit),
            "obituary_predeceased_hits": " | ".join(v["predeceased"]),
            "email_hints": " | ".join(f"{h['frag']}<{h['kind']}>" for h in hints),
            "emails": " ".join(emails),
            "named_relatives": " | ".join(f"{n.get('name')} ({n.get('canon_rel')}, {age_note('age', n.get('age'))})" for n in named),
            "note": "; ".join(note_parts),
            "obituary_url": finding.get("obituary_url") or "",
        }
        rows.append(row)
        detail.append({**row, "ghost_phones": sorted(ghost_phones), "unique_phones": sorted(unique),
                       "shared_phones": sorted(shared),
                       "named_relatives_detail": [{"name": n.get("name"), "canon_rel": n.get("canon_rel"),
                                                   "age": n.get("age"), "phones": sorted(rel_phones(n))} for n in named],
                       "survivors_listed": bool(finding.get("survivors")),
                       "vendor": v, "obituary": obit, "preceded": preceded, "hints": hints, "ghost_rel": grel})
        if a.verbose:
            say(f"{row['confidence']:<10} {g['name']:<26} -> {dec['full'] or '-':<34} {row['sources']}  {row['note']}")

    rows.sort(key=lambda r: (CONF_ORDER.index(r["confidence"]), r["key"]))
    OUT.mkdir(parents=True, exist_ok=True)
    stem = "ghost_name_resolution" if not a.key else "ghost_" + re.sub(r"[^a-z0-9]", "_", a.key)
    csv_path, json_path = OUT / f"{stem}.csv", OUT / f"{stem}.json"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    json_path.write_text(json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"),
                                     "ghosts": len(rows), "ghost_list": str(a.ghosts), "records": detail},
                                    indent=1, ensure_ascii=False), encoding="utf-8")

    conf = Counter(r["confidence"] for r in rows)
    say("\nconfidence: " + ", ".join(f"{c} {conf.get(c, 0)}" for c in CONF_ORDER))
    src = Counter(s.split(":")[0] for r in rows for s in r["sources"].split(" + ") if s)
    say("source hits: " + ", ".join(f"{k} {v}" for k, v in src.most_common()))
    proposed = [r for r in rows if r["proposed_first"]]
    say(f"proposed a name: {len(proposed)} "
        f"({sum(1 for r in proposed if r['live_rel_slot'])} with a live REL slot in the push plan)")
    open_rows = [d for d in detail if d["confidence"] in OPEN_TIERS]
    shells = [d for d in open_rows if d["obituary_url"] and not (d.get("obituary") or d.get("obituary_candidates"))
              and not d.get("survivors_listed")]
    say(f"open (conflict/hint/unresolved): {len(open_rows)}; {sum(1 for d in open_rows if not d['obituary_url'])} with no "
        f"obituary found, {len(shells)} with an obituary page that lists no survivors (rescrape the funeral-home page, free)")
    say(f"household ghosts (no unique phone): {sum(1 for d in detail if d['ghost_phones'] and not d['unique_phones'])}; "
        f"no phones at all: {sum(1 for d in detail if not d['ghost_phones'])}")
    say(f"\nwrote {csv_path}\nwrote {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
