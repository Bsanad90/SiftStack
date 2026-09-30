"""Skip-trace / Trestle / deep-prospecting census of the whole DataSift account. READ-ONLY.

Basem's two questions (2026-09-25): how many records hold numbers that were never Trestle
scored (no Dial First..Fourth / Drop tier tag on the phone) or carry no phone tags at all, and
which records need deep prospecting versus a skip trace alone.

The rules are the ones `reskiptrace_audit.classify` and `obituary_dp_batch` already encode,
applied to every record instead of one status. Hydrating 30k records is ~5.5 h, so the census
is built from SERVER-SIDE COUNTS + PAGED UUID SETS + the THIN LIST ROW (~1,300 calls) and only
hydrates a random sample to price the Trestle run.

Buckets (first match wins), with P = has phones, T = at least one tier-tagged phone,
S = skip traced (owner flag OR a Skip Traced tag), D = deceased signal (deceased flag, a
probate/obituary list, an obituary date, or the Deceased Owner tag), DP = deep prospected
(a DP tag or a local push log), MO = Mail Only tag, E = entity owner:

  DONE_NO_NUMBERS            not P, DP           -> mail only, nothing left to trace
  SKIP_TRACE[_ENTITY]        not P, not S, not D -> DataSift skip trace ($0 marginal)
  DP_NEVER_TRACED[_ENTITY]   not P, not S, D     -> straight to DP (Basem 2026-09-23)
  DP_TRACED_EMPTY[_ENTITY]   not P, S, D         -> DP with research (Ty's rule)
  DP_TRACED_EMPTY_NO_SIGNAL[_ENTITY]  not P, S, not D -> DP, presume-alive mode
  SCORE                      P, not T            -> Trestle run (SCORE_NO_TAGS = no tags at all)
  TIERED_UNCALLABLE          P, T, MO, not DP    -> nothing dialable; DP if D else re-trace
  DP_DECEASED_WITH_NUMBERS   P, T, D, not DP     -> numbers are the decedent's; Basem's call
  OK                         everything else

Every count is guarded: an unknown query key is silently ignored and returns the ACCOUNT
TOTAL; a mistyped value returns 0. Complementary pairs must sum to the total, every paged set
must match its count (with a small tolerance for the index lag), and multi-uuid
`any_phone_tags` OR-semantics are proven on two small tags before being trusted.

Nothing here writes to the account. There is no --commit.

    python -u src/scripts/skip_dp_census.py                 # full census, 300-record sample
    python -u src/scripts/skip_dp_census.py --sample 0      # counts + buckets only
    python -u src/scripts/skip_dp_census.py --resume --run-dir output/skip_dp_census/<stamp>
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from dpd.preset_spec import DEAD_STATUSES  # noqa: E402
from dpd_lane_assignee_report import PAGE, count  # noqa: E402
from dpd_lane_phone_score import (  # noqa: E402
    MAIL_ONLY, SCORE_TAGS, SKIP_PREFIX, Api, jload, jsave, load_seeds, say, score_tag_of,
    tag_names,
)
from dp_strip_order import pushed_records  # noqa: E402
from live_pull import EARLIEST_GUESS, SAFE_LIMIT, _iso  # noqa: E402
from not_related_requalify import bad_phone_status  # noqa: E402
from obituary_dp_batch import TAG_ALIVE, TAG_DECEASED, TAG_PRESUMED, TAG_UNVERIFIED  # noqa: E402
from obituary_dp_input import ENTITY_WORDS  # noqa: E402
from phone_validator import COST_PER_PHONE, clean_phone  # noqa: E402

OUT_ROOT = ROOT / "output" / "skip_dp_census"

# The six in-scope jurisdictions, spelled the way the stored presets spell them.
FOOTPRINT = ["District Of Columbia", "Anne Arundel", "Frederick", "Montgomery",
             "Arlington", "Fairfax"]
# The dead-status vocabulary the stored presets use (10 names; the query cannot see the
# other six in preset_spec.DEAD_STATUSES -- those are handled locally on the thin status).
DEAD_QUERY_VOCAB = ["Dead Lead", "not_interested", "dnc", "closed", "sold", "Already Sold",
                    "under_contract", "Not Related to Property", "Auction Date Passed", "listed"]
DECEASED_LISTS = ["Probate", "Register of Wills Probates", "Obituary", "Pre-Probate/Deceased"]
# Present on some accounts, not this one (2026-09-25: 39 lists, none by this name); optional.
OPTIONAL_DECEASED_LISTS = ["Obituaries Siftmap PRO"]
INHERITANCE_LIST = "Inheritance"
# Property tags that are a deceased signal on their own (obituary_dp_batch.DECEASED_SIGNAL_TAGS
# minus the substring trap: "Owner Alive - Spouse Deceased" contains "deceased").
DECEASED_TAGS = [TAG_DECEASED, "Owner Deceased", "Obituaries", "Deceased"]
# DP verdict tags written only by the DP pusher.
DP_EXACT_TAGS = [TAG_ALIVE, TAG_UNVERIFIED, TAG_PRESUMED, "DP Trust Trustee Inferred",
                 "DP No Signer Numbers", "DP Reach Path Only", "DP Owner Is PR"]
DP_PREFIXES = ("deep prospected", "dp no numbers")
# Every trace-date spelling the account holds: the per-source tags (Basem, 2026-09-30:
# REISift / SS / IDI Traced MM/YYYY), DataSift's own `REISift Skipped`, and the legacy
# `Skip Traced` / `skip_traced_` series.
SKIP_TAG_RE = re.compile(
    r"^(reisift traced\b|ss traced\b|idi traced\b|reisift skipped\b|skip traced\b|skip_traced_)",
    re.I,
)
# Small phone-tag families used to prove multi-uuid OR semantics cheaply.
PROBE_TAG_CANDIDATES = ["Attorney.1", "Attorney.2", "Deceased.2", "Spouse.1", "Son", "Shady"]

DP_COST_LO, DP_COST_HI = 0.30, 0.55
WORD_RE = re.compile(r"[a-z0-9]+")

BUCKET_ORDER = ["DONE_NO_NUMBERS", "SKIP_TRACE", "SKIP_TRACE_ENTITY", "DP_NEVER_TRACED",
                "DP_NEVER_TRACED_ENTITY", "DP_TRACED_EMPTY", "DP_TRACED_EMPTY_ENTITY",
                "DP_TRACED_EMPTY_NO_SIGNAL", "DP_TRACED_EMPTY_NO_SIGNAL_ENTITY", "SCORE",
                "TIERED_UNCALLABLE", "DP_DECEASED_WITH_NUMBERS", "OK"]
NEXT_STEP = {
    "DONE_NO_NUMBERS": "nothing to trace; mail only",
    "SKIP_TRACE": "DataSift skip trace (Send To -> Skip Trace), $0 marginal",
    "SKIP_TRACE_ENTITY": "find the principal (deed / SOS) first, then skip trace the person",
    "DP_NEVER_TRACED": "deep prospect (SmartSkip + obituary research), skip trace not needed",
    "DP_NEVER_TRACED_ENTITY": "trustee / principal off the title, then deep prospect",
    "DP_TRACED_EMPTY": "deep prospect with research (Ty: traced + empty -> don't re-skip)",
    "DP_TRACED_EMPTY_ENTITY": "principal research (enformion_business / SOS), not the obituary pipeline",
    "DP_TRACED_EMPTY_NO_SIGNAL": "deep prospect in --presume-alive-without-signal mode",
    "DP_TRACED_EMPTY_NO_SIGNAL_ENTITY": "principal research (enformion_business / SOS)",
    "SCORE": "Trestle tier run (dpd_lane_phone_score engine, never-double-bill)",
    "TIERED_UNCALLABLE": "every number scored uncallable: DP if deceased signal, else re-trace",
    "DP_DECEASED_WITH_NUMBERS": "numbers belong to a dead owner: DP to find the heirs (Basem's call)",
    "OK": "tiered numbers on a living owner, or DP already done",
}


# ------------------------------------------------------------------ small helpers

def status_key(s) -> str:
    # = sold_backlog._status_key / reskiptrace_audit.status_key: the account stores some
    # statuses snake_cased ("not_interested") beside display labels ("No Answer").
    return (str(s or "")).strip().lower().replace("_", " ").replace("-", " ")


DEAD_KEYS = {status_key(s) for s in DEAD_STATUSES}


def is_entity(first: str, last: str, company: str) -> bool:
    """An LLC/trust owner: company set with no person name, OR a business word jammed
    into the name fields (wizard-era uploads). Whole words only -- 'Ralph' holds 'lp'."""
    if (company or "").strip() and not (first or "").strip():
        return True
    words = set(WORD_RE.findall(f"{first or ''} {last or ''}".lower()))
    return bool(words & ENTITY_WORDS)


def is_dp_tag(title: str) -> bool:
    t = (title or "").strip()
    return t in DP_EXACT_TAGS or t.lower().startswith(DP_PREFIXES)


def is_skip_tag(title: str) -> bool:
    return bool(SKIP_TAG_RE.match((title or "").strip()))


def safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def classify(f: dict) -> str:
    """One record's flags -> bucket. First match in the documented order.

    f keys: P (has phones), T (tiered phone), S (skip traced), D (deceased signal),
    DP (deep prospected), MO (Mail Only), E (entity owner).
    """
    P, T, S, D, DP, MO, E = (bool(f.get(k)) for k in ("P", "T", "S", "D", "DP", "MO", "E"))
    ent = "_ENTITY" if E else ""
    if not P:
        if DP:
            return "DONE_NO_NUMBERS"
        if not S and not D:
            return "SKIP_TRACE" + ent
        if not S and D:
            return "DP_NEVER_TRACED" + ent
        if S and D:
            return "DP_TRACED_EMPTY" + ent
        return "DP_TRACED_EMPTY_NO_SIGNAL" + ent
    if not T:
        return "SCORE"
    if MO and not DP:
        return "TIERED_UNCALLABLE"
    if D and not DP:
        return "DP_DECEASED_WITH_NUMBERS"
    return "OK"


def within_tolerance(got: int, expected: int) -> bool:
    """The count comes off an index that lags writes by a minute or two and dialers write all
    day; a small miss is a warning, not a failure."""
    return abs(got - expected) <= max(20, int(0.005 * expected))


def choose_probe_tags(counts: dict[str, int]) -> list[str]:
    """Two non-zero tags small enough to page in full, smallest first."""
    ok = sorted((c, t) for t, c in counts.items() if 0 < c <= SAFE_LIMIT)
    return [t for _, t in ok[:2]]


# ------------------------------------------------------------------ API readers

class Census:
    def __init__(self, api, run: Path, resume: bool):
        self.api = api
        self.run = run
        self.resume = resume
        self.total = 0
        self.counts: dict[str, dict] = {}
        self.warnings: list[str] = []
        self.calls = 0

    # -- counts --------------------------------------------------------------
    def q_count(self, name: str, must: dict, *, expect_nonzero: bool = False) -> int:
        self.calls += 1
        c = count(self.api, {"must": must})
        self.counts[name] = {"must": must, "count": c}
        if c == self.total:
            raise RuntimeError(f"count {name!r} == account total {self.total}: a query key was "
                               f"silently ignored ({json.dumps(must)[:200]})")
        if expect_nonzero and c == 0:
            raise RuntimeError(f"count {name!r} is 0 but this key has a known population "
                               f"({json.dumps(must)[:200]}): the VALUE is probably wrong")
        say(f"  count {name:42} {c:>7}")
        return c

    # -- listings ------------------------------------------------------------
    def listing(self, path: str) -> list[dict]:
        """A vocabulary endpoint with ?limit=999 (trap 5: the default page is 10 rows).
        The phone-tag endpoint caps at 200 per page and is paged on `next`."""
        out, off, r = [], 0, {}
        while True:
            self.calls += 1
            st, r = self.api.get(f"{path}&offset={off}")
            if st != 200 or not isinstance(r, dict):
                raise RuntimeError(f"listing {path} failed: {st} {str(r)[:200]}")
            rows = r.get("results") or r.get("data") or []
            out += rows
            off += len(rows)
            if not r.get("next") or not rows:
                break
        want = r.get("count")
        if isinstance(want, int) and want != len(out):
            raise RuntimeError(f"listing {path}: {len(out)} rows but count says {want}")
        return out

    # -- paged uuid sets -------------------------------------------------------
    def _range_count(self, must: dict, start: datetime, end: datetime) -> int:
        self.calls += 1
        return count(self.api, {"must": {**must, "created": [_iso(start), _iso(end)]}})

    def _buckets(self, must: dict, start: datetime, end: datetime) -> list[tuple]:
        c = self._range_count(must, start, end)
        if c == 0:
            return []
        if c <= SAFE_LIMIT or (end - start) <= timedelta(seconds=1):
            return [(start, end, c)]
        mid = start + (end - start) / 2
        return self._buckets(must, start, mid) + self._buckets(must, mid, end)

    def _page(self, must: dict, expected: int, rows: dict | None) -> set[str]:
        out: set[str] = set()
        offset = 0
        while offset < expected:
            self.calls += 1
            st, r = self.api.post_as_get("/api/internal/property/",
                                         {"limit": PAGE, "offset": offset, "ordering": "created",
                                          "query": {"must": must}})
            if st != 200:
                raise RuntimeError(f"page failed at offset {offset}: {st} {str(r)[:300]}")
            got = r.get("results") or []
            if not got:
                break
            for row in got:
                out.add(row["uuid"])
                if rows is not None:
                    rows.setdefault(row["uuid"], row)
            offset += PAGE
        return out

    def page_set(self, name: str, must: dict, expected: int, rows: dict | None = None) -> set[str]:
        """Every uuid matching `must`, under the 10,000-offset ceiling via `created` buckets.
        Checked against the UNBUCKETED count with a lag tolerance; re-paged once on a miss."""
        cache = self.run / f"set_{safe(name)}.json"
        if self.resume and cache.exists() and rows is None:
            got = set(jload(cache, []))
            say(f"  set   {name:42} {len(got):>7}  (resumed)")
            return got
        got: set[str] = set()
        for attempt in (1, 2):
            if expected <= SAFE_LIMIT:
                got = self._page(must, expected, rows)
            else:
                now = datetime.now(timezone.utc) + timedelta(days=1)
                got = set()
                for (s, e, c) in self._buckets(must, EARLIEST_GUESS, now):
                    got |= self._page({**must, "created": [_iso(s), _iso(e)]}, c, rows)
            if len(got) == expected:
                break
            if within_tolerance(len(got), expected):
                self.warnings.append(f"set {name}: {len(got)} paged vs count {expected} (index lag)")
                break
            if attempt == 2:
                raise RuntimeError(f"set {name}: paged {len(got)} vs count {expected} twice")
            say(f"  set   {name}: {len(got)} vs {expected}, re-paging once")
        say(f"  set   {name:42} {len(got):>7}")
        jsave(cache, sorted(got))
        return got


# ------------------------------------------------------------------ the census

def resolve_vocab(c: Census) -> dict:
    say("Phase 0: vocabularies")
    phone_tags = c.listing("/api/internal/phone/tag/?limit=200")
    prop_tags = c.listing("/api/internal/tag/?limit=999")
    lists = c.listing("/api/internal/list/?limit=999")
    statuses = c.listing("/api/internal/status/?limit=999")
    pt = {(t.get("title") or "").strip(): t["uuid"] for t in phone_tags}
    tg = {(t.get("title") or "").strip(): t["uuid"] for t in prop_tags}
    ls = {(t.get("title") or "").strip(): t["uuid"] for t in lists}
    say(f"  {len(pt)} phone tags, {len(tg)} property tags, {len(ls)} lists, {len(statuses)} statuses")

    tier = {t: pt[t] for t in sorted(SCORE_TAGS) if t in pt}
    tier.update({t: u for t, u in pt.items() if t.startswith(SKIP_PREFIX)})
    missing = [t for t in ("Dial First", "Dial Second", "Dial Third", "Dial Fourth", "Drop") if t not in pt]
    if missing:
        raise RuntimeError(f"tier phone tags missing on the account: {missing}")
    for fixed in (TAG_DECEASED, MAIL_ONLY):
        if fixed not in tg:
            raise RuntimeError(f"property tag {fixed!r} missing; have {sorted(tg)[:20]}...")
    for fixed in DECEASED_LISTS:
        if fixed not in ls:
            raise RuntimeError(f"list {fixed!r} missing; have {sorted(ls)}")

    v = {
        "phone_tags": pt, "tier": tier,
        "deceased_lists": {t: ls[t] for t in DECEASED_LISTS + OPTIONAL_DECEASED_LISTS if t in ls},
        "inheritance": ls.get(INHERITANCE_LIST),
        "deceased_tags": {t: tg[t] for t in DECEASED_TAGS if t in tg},
        "dp_tags": {t: u for t, u in tg.items() if is_dp_tag(t)},
        "skip_tags": {t: u for t, u in tg.items() if is_skip_tag(t)},
        "mail_only": tg[MAIL_ONLY],
        "statuses": [s.get("title") for s in statuses],
    }
    for k in ("tier", "dp_tags", "skip_tags", "deceased_tags"):
        say(f"  {k}: {sorted(v[k])}")
    if not v["skip_tags"]:
        say("  WARNING: no Skip Traced tags found -- S falls back to the owner flag only")
    return v


def phase1_counts(c: Census, v: dict) -> dict:
    say("Phase 1: headline counts (server-side, guarded)")
    n: dict = {}
    n["phones"] = c.q_count("phone=1", {"phone": 1}, expect_nonzero=True)
    n["no_phones"] = c.q_count("phone=0", {"phone": 0}, expect_nonzero=True)
    n["skiptraced"] = c.q_count("skiptraced=1", {"skiptraced": 1}, expect_nonzero=True)
    n["not_skiptraced"] = c.q_count("skiptraced=0", {"skiptraced": 0}, expect_nonzero=True)
    n["deceased_flag"] = c.q_count("deceased=1", {"deceased": 1}, expect_nonzero=True)
    n["not_deceased_flag"] = c.q_count("deceased=0", {"deceased": 0}, expect_nonzero=True)
    for a, b in (("phones", "no_phones"), ("skiptraced", "not_skiptraced")):
        if n[a] + n[b] != c.total:
            raise RuntimeError(f"{a} + {b} = {n[a] + n[b]} != total {c.total}: a key lied")
    # `deceased` is TRI-STATE on the owner (measured 2026-09-25: 3 true, 18,741 false, the
    # rest null), so its halves do not sum to the total; the null share is reported instead.
    n["deceased_null"] = c.total - n["deceased_flag"] - n["not_deceased_flag"]
    if n["deceased_null"] < 0:
        raise RuntimeError("deceased=1 + deceased=0 exceeds the total: a key lied")
    say(f"  deceased flag null on {n['deceased_null']} records")
    n["nophone_never"] = c.q_count("phone=0&skiptraced=0", {"phone": 0, "skiptraced": 0})
    n["nophone_traced"] = c.q_count("phone=0&skiptraced=1", {"phone": 0, "skiptraced": 1})

    # OR-semantics probe for a multi-uuid any_phone_tags, on two small tags.
    probe_counts = {t: c.q_count(f"phone_tag:{t}", {"any_phone_tags": [v["phone_tags"][t]]})
                    for t in PROBE_TAG_CANDIDATES if t in v["phone_tags"]}
    pair = choose_probe_tags(probe_counts)
    n["or_semantics"] = None
    if len(pair) == 2:
        a, b = pair
        sa = c.page_set(f"probe_{a}", {"any_phone_tags": [v["phone_tags"][a]]}, probe_counts[a])
        sb = c.page_set(f"probe_{b}", {"any_phone_tags": [v["phone_tags"][b]]}, probe_counts[b])
        both = c.q_count(f"phone_tag:{a}|{b}",
                         {"any_phone_tags": [v["phone_tags"][a], v["phone_tags"][b]]})
        n["or_semantics"] = (both == len(sa | sb))
        n["or_probe"] = {"tags": pair, "combined": both, "union": len(sa | sb)}
        say(f"  OR probe on {a!r}+{b!r}: combined {both} vs union {len(sa | sb)} -> "
            f"{'OR proven' if n['or_semantics'] else 'NOT OR -- per-uuid unions only'}")
    else:
        c.warnings.append("OR-semantics probe skipped: fewer than two small probe tags exist")

    n["tier_each"] = {t: c.q_count(f"phone_tag:{t}", {"any_phone_tags": [u]}) for t, u in v["tier"].items()}
    n["obit_dated"] = c.q_count("last_obituary_date",
                                {"last_obituary_date": ["2000-01-01", date.today().isoformat()]},
                                expect_nonzero=True)
    n["lists"] = {t: c.q_count(f"list:{t}", {"any_lists": [u]}, expect_nonzero=True)
                  for t, u in v["deceased_lists"].items()}
    if v["inheritance"]:
        n["lists"][INHERITANCE_LIST] = c.q_count(f"list:{INHERITANCE_LIST}", {"any_lists": [v["inheritance"]]})
    n["deceased_tags"] = {t: c.q_count(f"tag:{t}", {"any_tags": [u]}) for t, u in v["deceased_tags"].items()}
    n["dp_tags"] = {t: c.q_count(f"tag:{t}", {"any_tags": [u]}) for t, u in v["dp_tags"].items()}
    n["skip_tags"] = {t: c.q_count(f"tag:{t}", {"any_tags": [u]}) for t, u in v["skip_tags"].items()}
    n["mail_only"] = c.q_count("tag:Mail Only", {"any_tags": [v["mail_only"]]})
    n["dead_status_query"] = c.q_count("dead statuses (10-name query)",
                                       {"any_property_status": DEAD_QUERY_VOCAB})
    n["dnc"] = c.q_count("dnc=1", {"dnc": 1})
    n["opt_out"] = c.q_count("opt_out=1", {"opt_out": 1})
    n["footprint"] = c.q_count("footprint", {"any_county": FOOTPRINT}, expect_nonzero=True)
    return n


def phase2_thin(c: Census) -> dict[str, dict]:
    say("Phase 2: thin list of the whole account")
    cache = c.run / "thin.json"
    if c.resume and cache.exists():
        rows = jload(cache, {})
        say(f"  {len(rows)} thin rows (resumed)")
        return rows
    rows: dict[str, dict] = {}
    c.page_set("all", {}, c.total, rows)
    slim = {}
    for u, r in rows.items():
        o = r.get("owner") or {}
        a = r.get("address") or {}
        slim[u] = {
            "street": a.get("street") or "", "city": a.get("city") or "",
            "state": a.get("state") or "",
            "zip5": (a.get("zip5") or a.get("postal_code") or "")[:5],
            "first": o.get("first_name") or "", "last": o.get("last_name") or "",
            "company": o.get("company") or "", "owner_uuid": o.get("uuid") or "",
            "has_phones": bool(r.get("has_phones")), "skiptraced": bool(r.get("skiptraced")),
            "last_skip_traced": (r.get("last_skip_traced") or "")[:10],
            "status": r.get("status") or "",
            "directmail_attempts": r.get("directmail_attempts") or 0,
            "do_not_mail_ever": bool(r.get("do_not_mail_ever")),
        }
    jsave(cache, slim)
    say(f"  {len(slim)} thin rows kept")
    return slim


def phase3_sets(c: Census, v: dict, n: dict) -> dict[str, set]:
    say("Phase 3: uuid sets")
    S: dict[str, set] = {}
    tier_union: set = set()
    for t, u in v["tier"].items():
        if n["tier_each"][t]:
            tier_union |= c.page_set(f"tier_{t}", {"any_phone_tags": [u]}, n["tier_each"][t])
    S["tiered"] = tier_union
    all_uuids = list(v["phone_tags"].values())
    if n["or_semantics"]:
        # Exact re-check of the combined query against the per-tier union.
        want = c.q_count("any_tier (combined)", {"any_phone_tags": list(v["tier"].values())})
        if want != len(tier_union):
            c.warnings.append(f"combined tier count {want} != per-tier union {len(tier_union)}")
        k = c.q_count("any_phone_tag (combined)", {"any_phone_tags": all_uuids})
        S["any_phone_tag"] = c.page_set("any_phone_tag", {"any_phone_tags": all_uuids}, k)
    else:
        say("  building any_phone_tag as a per-uuid union (OR semantics not proven)")
        u_all: set = set(tier_union)
        for t, u in v["phone_tags"].items():
            if t in v["tier"]:
                continue
            k = c.q_count(f"phone_tag:{t}", {"any_phone_tags": [u]})
            if k:
                u_all |= c.page_set(f"ptag_{t}", {"any_phone_tags": [u]}, k)
        S["any_phone_tag"] = u_all
    S["deceased_flag"] = c.page_set("deceased_flag", {"deceased": 1}, n["deceased_flag"])
    S["obit_dated"] = c.page_set("obit_dated",
                                 {"last_obituary_date": ["2000-01-01", date.today().isoformat()]},
                                 n["obit_dated"])
    S["deceased_lists"] = set()
    for t, u in v["deceased_lists"].items():
        S["deceased_lists"] |= c.page_set(f"list_{t}", {"any_lists": [u]}, n["lists"][t])
    S["inheritance"] = (c.page_set("list_Inheritance", {"any_lists": [v["inheritance"]]},
                                   n["lists"][INHERITANCE_LIST])
                        if v["inheritance"] and n["lists"].get(INHERITANCE_LIST) else set())
    S["deceased_tags"] = set()
    for t, u in v["deceased_tags"].items():
        if n["deceased_tags"][t]:
            S["deceased_tags"] |= c.page_set(f"dtag_{t}", {"any_tags": [u]}, n["deceased_tags"][t])
    S["dp_tags"] = set()
    for t, u in v["dp_tags"].items():
        if n["dp_tags"][t]:
            S["dp_tags"] |= c.page_set(f"dptag_{t}", {"any_tags": [u]}, n["dp_tags"][t])
    S["skip_tags"] = set()
    for t, u in v["skip_tags"].items():
        if n["skip_tags"][t]:
            S["skip_tags"] |= c.page_set(f"stag_{t}", {"any_tags": [u]}, n["skip_tags"][t])
    S["mail_only"] = (c.page_set("mail_only", {"any_tags": [v["mail_only"]]}, n["mail_only"])
                      if n["mail_only"] else set())
    S["dnc"] = c.page_set("dnc", {"dnc": 1}, n["dnc"]) if n["dnc"] else set()
    S["opt_out"] = c.page_set("opt_out", {"opt_out": 1}, n["opt_out"]) if n["opt_out"] else set()
    S["footprint"] = c.page_set("footprint", {"any_county": FOOTPRINT}, n["footprint"])
    S["dead_query"] = (c.page_set("dead_query", {"any_property_status": DEAD_QUERY_VOCAB},
                                  n["dead_status_query"]) if n["dead_status_query"] else set())
    return S


def local_dp_inventory() -> dict[str, str]:
    """uuid -> run dir name, from every output/dp_*/push_log.jsonl (the Deep Prospected
    month tag only exists from 2026-09-01; earlier batches are only in these logs)."""
    out: dict[str, str] = {}
    for run in sorted((ROOT / "output").glob("dp_*")):
        if not run.is_dir():
            continue
        for rec in pushed_records(run).values():
            out.setdefault(rec["uuid"], run.name)
    return out


def phase4_classify(thin: dict, S: dict, dp_local: dict) -> list[dict]:
    say("Phase 4: classification")
    D_set = S["deceased_flag"] | S["obit_dated"] | S["deceased_lists"] | S["deceased_tags"]
    rows = []
    for u, t in thin.items():
        f = {
            "P": t["has_phones"], "T": u in S["tiered"],
            "S": t["skiptraced"] or u in S["skip_tags"],
            "D": u in D_set, "DP": u in S["dp_tags"] or u in dp_local,
            "MO": u in S["mail_only"], "E": is_entity(t["first"], t["last"], t["company"]),
        }
        dead = status_key(t["status"]) in DEAD_KEYS or u in S["dnc"] or u in S["opt_out"]
        rows.append({
            "uuid": u, "bucket": classify(f),
            "street": t["street"], "city": t["city"], "state": t["state"], "zip5": t["zip5"],
            "owner": (" ".join(x for x in (t["first"], t["last"]) if x).strip() or t["company"]),
            "entity": f["E"], "status": t["status"], "dead_status": dead,
            "in_footprint": u in S["footprint"],
            "has_phones": f["P"], "tiered": f["T"], "any_phone_tag": u in S["any_phone_tag"],
            "skiptraced_flag": t["skiptraced"], "skip_trace_tag": u in S["skip_tags"],
            "last_skip_traced": t["last_skip_traced"],
            "deceased_flag": u in S["deceased_flag"], "obit_dated": u in S["obit_dated"],
            "deceased_list": u in S["deceased_lists"], "deceased_tag": u in S["deceased_tags"],
            "inheritance_list": u in S["inheritance"],
            "dp_tag": u in S["dp_tags"], "dp_local": dp_local.get(u, ""),
            "mail_only": f["MO"], "directmail_attempts": t["directmail_attempts"],
        })
    return rows


def has_deceased_signal(r: dict) -> bool:
    return bool(r["deceased_flag"] or r["obit_dated"] or r["deceased_list"] or r["deceased_tag"])


def phase5_sample(c: Census, rows: list[dict], n_sample: int) -> dict:
    """Hydrate a random sample of ACTIVE SCORE records to price the Trestle run."""
    if n_sample <= 0:
        return {}
    say(f"Phase 5: sample hydration ({n_sample} active SCORE records)")
    pool = sorted(r["uuid"] for r in rows if r["bucket"] == "SCORE" and not r["dead_status"])
    if not pool:
        return {"n": 0, "pool": 0}
    rnd = random.Random(20260925)
    picks = rnd.sample(pool, min(n_sample, len(pool)))
    cache_p = c.run / "sample_detail.json"
    cache = jload(cache_p, {})
    for i, u in enumerate(picks, 1):
        if u in cache:
            continue
        c.calls += 1
        st, body = c.api.get(f"/api/internal/property/{u}/")
        cache[u] = body if st == 200 else {"_gone": True, "_status": st}
        if i % 25 == 0:
            jsave(cache_p, cache)
            say(f"    {i}/{len(picks)}")
    jsave(cache_p, cache)
    seeds = load_seeds()
    live_counts, seeded, partial, gone, bad_only = [], 0, 0, 0, 0
    for u in picks:
        d = cache.get(u) or {}
        if d.get("_gone"):
            gone += 1
            continue
        phones = [p for p in ((d.get("owner") or {}).get("phones") or []) if isinstance(p, dict)]
        alive = [p for p in phones if not bad_phone_status(p.get("status"))]
        scored = [p for p in alive if score_tag_of(tag_names(p.get("tags")))]
        unscored = [p for p in alive if not score_tag_of(tag_names(p.get("tags")))]
        if scored:
            partial += 1
        if phones and not alive:
            bad_only += 1
        live_counts.append(len(unscored))
        seeded += sum(1 for p in unscored if clean_phone(str(p.get("number") or "")) in seeds)
    n_ok = len(live_counts)
    mean = statistics.mean(live_counts) if live_counts else 0.0
    sd = statistics.pstdev(live_counts) if len(live_counts) > 1 else 0.0
    ci = 1.96 * sd / (n_ok ** 0.5) if n_ok else 0.0
    return {"n": n_ok, "gone": gone, "pool": len(pool), "mean_unscored_live": round(mean, 2),
            "ci95": round(ci, 2), "partial_scored": partial, "bad_status_only": bad_only,
            "seeded_free": seeded, "total_unscored_in_sample": sum(live_counts),
            "cost_per_phone": COST_PER_PHONE}


def snapshot_partial() -> dict:
    """Partial scoring as of the 2026-09-01 hydration (the only account-wide file with tags)."""
    p = ROOT / "output" / "live_account_pull_20260901.json"
    if not p.exists():
        return {}
    say("  reading the 2026-09-01 hydration for the partial-scoring figure...")
    d = json.loads(p.read_text(encoding="utf-8"))
    none = some = full = 0
    for r in d.get("records") or []:
        rec = r.get("record") or {}
        phones = [x for x in ((rec.get("owner") or {}).get("phones") or []) if isinstance(x, dict)]
        if not phones:
            continue
        sc = sum(1 for x in phones if score_tag_of(tag_names(x.get("tags"))))
        if sc == 0:
            none += 1
        elif sc == len(phones):
            full += 1
        else:
            some += 1
    return {"as_of": str(d.get("checkpoint_at") or "")[:10], "with_phones": none + some + full,
            "none_scored": none, "some_scored": some, "all_scored": full}


# ------------------------------------------------------------------ outputs

CUT_KEYS = ("all", "active", "active_footprint")
EXTRA_CUTS = ["SCORE_NO_TAGS", "SCORE_deceased_not_dp", "TIERED_UNCALLABLE_deceased"]


def cut_counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    out = {b: {k: 0 for k in CUT_KEYS} for b in BUCKET_ORDER + EXTRA_CUTS}
    for r in rows:
        keys = [r["bucket"]]
        if r["bucket"] == "SCORE":
            if not r["any_phone_tag"]:
                keys.append("SCORE_NO_TAGS")
            if has_deceased_signal(r) and not (r["dp_tag"] or r["dp_local"]):
                keys.append("SCORE_deceased_not_dp")
        if r["bucket"] == "TIERED_UNCALLABLE" and has_deceased_signal(r):
            keys.append("TIERED_UNCALLABLE_deceased")
        for k in keys:
            out[k]["all"] += 1
            if not r["dead_status"]:
                out[k]["active"] += 1
                if r["in_footprint"]:
                    out[k]["active_footprint"] += 1
    return out


def write_csvs(run: Path, rows: list[dict]) -> None:
    cols = list(rows[0].keys()) if rows else ["uuid"]
    with open(run / "records.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    for b in BUCKET_ORDER:
        if b == "OK":
            continue
        sub = sorted((r for r in rows if r["bucket"] == b),
                     key=lambda r: (r["dead_status"], not r["in_footprint"], r["state"],
                                    r["city"], r["street"]))
        if not sub:
            continue
        with open(run / f"bucket_{b}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(sub)


def write_report(run: Path, c: Census, n: dict, cuts: dict, sample: dict, snap: dict,
                 S: dict, extra: dict, dp_local: dict, started: str, elapsed_min: float) -> None:
    total = c.total
    phones = n["phones"]
    zero_tier = phones - len(S["tiered"])          # tiered is a subset of has-phones
    no_tags = phones - len(S["any_phone_tag"])
    L = [f"# Skip-trace / Trestle / DP census -- {started}\n",
         f"Account total {total:,} records. Read-only; {c.calls:,} API calls, {elapsed_min:.1f} min. "
         f"Counts come off a search index that lags writes by 1-2 minutes.\n",
         "## Headline\n",
         "| measure | records | share of records with phones |\n|---|---:|---:|",
         f"| records with phone numbers | {phones:,} | 100% |",
         f"| **no Trestle tier tag on any phone** (needs scoring) | **{zero_tier:,}** | {zero_tier / max(phones, 1):.1%} |",
         f"| no phone tags at all (nobody knows whose numbers they are) | {no_tags:,} | {no_tags / max(phones, 1):.1%} |",
         f"| at least one tiered phone | {len(S['tiered']):,} | {len(S['tiered']) / max(phones, 1):.1%} |"]
    if snap:
        L.append(f"| partially scored (some phones tiered, some not) -- as of the {snap['as_of']} hydration | "
                 f"{snap['some_scored']:,} of {snap['with_phones']:,} | "
                 f"{snap['some_scored'] / max(snap['with_phones'], 1):.1%} |")
    L += ["", "Records carrying at least one phone in each tier family:\n", "| tag | records |\n|---|---:|"]
    L += [f"| {t} | {k:,} |" for t, k in n["tier_each"].items()]
    L += ["", "## Buckets\n",
          "`all` = every record; `active` = not in a dead status, not DNC / opt-out; "
          "`active+footprint` = active AND in the 6 in-scope counties.\n",
          "| bucket | all | active | active+footprint | next step |\n|---|---:|---:|---:|---|"]
    for b in BUCKET_ORDER:
        k = cuts[b]
        L.append(f"| {b} | {k['all']:,} | {k['active']:,} | {k['active_footprint']:,} | {NEXT_STEP[b]} |")
        if b == "SCORE":
            for sub, label in (("SCORE_NO_TAGS", "of which: no phone tags at all"),
                               ("SCORE_deceased_not_dp", "of which: deceased signal and no DP yet (also heir-unknown)")):
                k2 = cuts[sub]
                L.append(f"| &nbsp;&nbsp;{label} | {k2['all']:,} | {k2['active']:,} | {k2['active_footprint']:,} | |")
        if b == "TIERED_UNCALLABLE":
            k2 = cuts["TIERED_UNCALLABLE_deceased"]
            L.append(f"| &nbsp;&nbsp;of which: deceased signal | {k2['all']:,} | {k2['active']:,} | {k2['active_footprint']:,} | DP |")
    L += ["", "## Spend estimates\n"]
    dp_b = ["DP_NEVER_TRACED", "DP_NEVER_TRACED_ENTITY", "DP_TRACED_EMPTY", "DP_TRACED_EMPTY_ENTITY",
            "DP_TRACED_EMPTY_NO_SIGNAL", "DP_TRACED_EMPTY_NO_SIGNAL_ENTITY"]
    dp_fp = sum(cuts[b]["active_footprint"] for b in dp_b)
    dp_all = sum(cuts[b]["active"] for b in dp_b)
    L.append(f"- Skip trace alone: {cuts['SKIP_TRACE']['active_footprint']:,} records in footprint "
             f"({cuts['SKIP_TRACE']['active']:,} active anywhere), $0 marginal on the unlimited plan; "
             f"plus {cuts['SKIP_TRACE_ENTITY']['active_footprint']:,} entity owners needing a principal first.")
    L.append(f"- Deep prospecting (no numbers): {dp_fp:,} records in footprint ({dp_all:,} active anywhere) "
             f"at ${DP_COST_LO:.2f}-{DP_COST_HI:.2f}/record = **${dp_fp * DP_COST_LO:,.0f}-{dp_fp * DP_COST_HI:,.0f}** "
             f"(footprint) / ${dp_all * DP_COST_LO:,.0f}-{dp_all * DP_COST_HI:,.0f} (all active).")
    L.append(f"- Deceased owners WITH tiered numbers, not DP'd: {cuts['DP_DECEASED_WITH_NUMBERS']['active_footprint']:,} "
             f"in footprint ({cuts['DP_DECEASED_WITH_NUMBERS']['active']:,} active) -- Basem's call whether these get DP.")
    sc_fp, sc_act = cuts["SCORE"]["active_footprint"], cuts["SCORE"]["active"]
    if sample and sample.get("n"):
        m, ci = sample["mean_unscored_live"], sample["ci95"]
        free = sample["seeded_free"] / max(sample["total_unscored_in_sample"], 1)
        per = COST_PER_PHONE * (1 - free)
        L.append(f"- Trestle scoring: a random sample of {sample['n']} active SCORE records (pool {sample['pool']:,}) "
                 f"averages {m} +/- {ci} unscored live numbers each; {sample['partial_scored']} of them already "
                 f"carry some tiered numbers, {sample['bad_status_only']} hold only bad-status numbers, "
                 f"{free:.0%} of the numbers are already in a paid Trestle cache (free). "
                 f"At ${COST_PER_PHONE}/number: footprint {sc_fp:,} records = **${sc_fp * m * per:,.0f}** "
                 f"(range ${sc_fp * max(m - ci, 0) * per:,.0f}-${sc_fp * (m + ci) * per:,.0f}); "
                 f"all active {sc_act:,} records = ${sc_act * m * per:,.0f}.")
    else:
        L.append(f"- Trestle scoring: {sc_fp:,} SCORE records in footprint ({sc_act:,} active); "
                 f"no sample hydrated, so no per-number price (run with --sample N).")
    or_txt = ("PROVEN (combined == union)" if n["or_semantics"] else
              "NOT proven; per-uuid unions used" if n["or_semantics"] is False else "skipped")
    L += ["", "## Cross-checks\n",
          f"- OR-semantics probe for multi-uuid `any_phone_tags`: {or_txt} {n.get('or_probe', '')}.",
          f"- Thin `has_phones` = {extra['thin_has_phones']:,} vs query phone=1 = {phones:,}; "
          f"thin `skiptraced` = {extra['thin_skiptraced']:,} vs query skiptraced=1 = {n['skiptraced']:,}.",
          f"- Dead statuses: 10-name query {n['dead_status_query']:,} vs local 16-name match "
          f"{extra['dead_local_n']:,} (the query cannot see Lost Deal / Close Out / Buyer* / Opt-out).",
          f"- DP inventory: {len(S['dp_tags']):,} records carry a DP tag; local push logs add "
          f"{sum(1 for u in dp_local if u not in S['dp_tags']):,} more ({len(dp_local):,} logged in total).",
          f"- Skip-traced: owner flag {n['skiptraced']:,}; Skip Traced tags {len(S['skip_tags']):,}; "
          f"tag-only (flag false) {extra['skip_tag_only_n']:,} -- the external-trace backfill gap.",
          "- 2026-09-15 REL/PR snapshot (Rel/Pr-tagged cohort only, 21,319 records with phones): 16,064 with "
          "no scored phone, 7,026 with an untagged phone -- same order of magnitude expected today."]
    L += [f"- WARNING: {w}" for w in c.warnings]
    L += ["", "## Where this census is blind\n",
          "- Partial scoring inside a record is not queryable; the figure above is from the 2026-09-01 hydration and the sample.",
          "- `Unknown`-tier phones are never tagged by design and read here as unscored.",
          "- `has_phones` counts bad-status numbers; TIERED_UNCALLABLE covers the scored half of that, SCORE may hold records whose only numbers are already marked wrong.",
          "- DP evidence that lives only on a board note or in REL fields (no tag, no local push log) is invisible; those records may be over-counted as DP candidates.",
          "- The Inheritance list is reported as a column, not a deceased signal (Basem's rule).",
          "- Records are classified on the OWNER's phones; secondary owners carry no phones on the API."]
    (run / "census.md").write_text("\n".join(L) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", type=int, default=300,
                    help="active SCORE records to hydrate for the Trestle price (0 = none)")
    ap.add_argument("--resume", action="store_true", help="reuse thin.json / set_*.json in --run-dir")
    ap.add_argument("--run-dir", default="", help="existing run dir to resume into")
    ap.add_argument("--no-snapshot", action="store_true", help="skip the 2026-09-01 hydration read")
    a = ap.parse_args()

    started = datetime.now()
    run = Path(a.run_dir) if a.run_dir else OUT_ROOT / started.strftime("%Y%m%dT%H%M%S")
    run.mkdir(parents=True, exist_ok=True)
    api = Api()
    say("JWT minted OK")
    c = Census(api, run, a.resume)
    c.calls += 1
    c.total = count(api, {})
    say(f"account total: {c.total}")

    v = resolve_vocab(c)
    n = phase1_counts(c, v)
    thin = phase2_thin(c)
    thin_p = {u for u, t in thin.items() if t["has_phones"]}
    thin_s = {u for u, t in thin.items() if t["skiptraced"]}
    for label, got, want in (("has_phones", len(thin_p), n["phones"]),
                             ("skiptraced", len(thin_s), n["skiptraced"])):
        if got != want:
            msg = f"thin {label} {got} vs query {want}"
            if within_tolerance(got, want):
                c.warnings.append(msg + " (index lag)")
            else:
                raise RuntimeError(msg + ": the thin flag and the query key disagree")
    S = phase3_sets(c, v, n)
    dp_local = local_dp_inventory()
    say(f"  local DP push logs: {len(dp_local)} uuids")
    rows = phase4_classify(thin, S, dp_local)
    extra = {
        "thin_has_phones": len(thin_p), "thin_skiptraced": len(thin_s),
        "skip_tag_only_n": len(S["skip_tags"] - thin_s),
        "dead_local_n": sum(1 for r in rows if status_key(r["status"]) in DEAD_KEYS),
    }
    cuts = cut_counts(rows)
    for b in BUCKET_ORDER:
        say(f"  {b:34} all {cuts[b]['all']:>6}  active {cuts[b]['active']:>6}  "
            f"footprint {cuts[b]['active_footprint']:>6}")
    sample = phase5_sample(c, rows, a.sample)
    snap = {} if a.no_snapshot else snapshot_partial()

    write_csvs(run, rows)
    elapsed = (datetime.now() - started).total_seconds() / 60
    write_report(run, c, n, cuts, sample, snap, S, extra, dp_local,
                 started.isoformat(timespec="minutes"), elapsed)
    jsave(run / "census.json", {
        "started": started.isoformat(timespec="seconds"), "elapsed_min": round(elapsed, 1),
        "calls": c.calls, "account_total": c.total, "counts": c.counts, "headline": n,
        "set_sizes": {k: len(x) for k, x in S.items()}, "extra": extra,
        "cuts": cuts, "sample": sample, "snapshot_partial": snap, "warnings": c.warnings,
        "vocab": {k: v[k] for k in ("tier", "deceased_lists", "deceased_tags", "dp_tags", "skip_tags")},
    })
    say(f"\nwrote {run / 'census.md'}  ({c.calls} calls, {elapsed:.1f} min, {len(c.warnings)} warnings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
