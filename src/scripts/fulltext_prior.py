"""Cross-day full-text reuse for the MDDC and VA fulltext legs (`--prior`).

Why (measured 2026-09-21): both sites publish only ~3-4 trustee notices a day,
yet every 06:00 run re-fetched the WHOLE 7-day window -- 87 MDDC detail pages
(Playwright + one 2Captcha Turnstile solve each) and ~53 VA detail pages
(Scrapfly search replay + solve, ~65 s each, the leg ran ~60 min). Notice ids
are stable across days (77 of 77 MDDC ids on 09-21 were already in 09-20's
`_full.csv`; a REPUBLICATION carries a NEW id, so keying on the id re-fetches
exactly what changed). So: a notice whose text we already hold is copied, not
fetched.

Two sources of prior text, both keyed on `notice_id`:
  * a previous day's `<stem>_full.csv` -- only the rows that matched the pull
    CSV (in-footprint), which is what existed before this module;
  * the `<stem>_details.json` sidecar this module writes beside every
    `_full.csv` -- EVERY resolved detail, including grid rows that matched no
    CSV row (off-footprint / time-share / key-mismatch rows: 13 of 53 VA
    fetches on 09-21 were such rows and were re-fetched daily). Each day's
    sidecar is the union of what it fetched and what it reused, so the chain
    never thins.

`--prior-auto` finds both in the sibling `output/daily_pulls/YYYY-MM-DD/` dirs
without the runner having to know which day the previous run was (the
schedule is Tue-Sat since 2026-09-21, so "yesterday" is wrong on Tuesdays).

Rules the callers keep:
  * Reused text goes through the SAME street guard as fetched text; a prior
    text that fails it is re-fetched, never trusted.
  * `auction_date` / `loan_principal` are RE-PARSED from the text every run
    (parser fixes propagate; the prior file's values are not copied).
  * The "first 5 fetches all failed -> abort" gate must count FETCHES, not
    resolved rows: with reuse, `results` fills up without the gate ever being
    exercised, so a dead gate would grind through every new id. `FetchBudget`
    keeps the two counts apart.
"""
from __future__ import annotations

import csv
import glob
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

MIN_TEXT_CHARS = 200          # same threshold both fetchers use for "real" text
DEFAULT_LOOKBACK_DAYS = 14    # covers the 7-day window + a weekend + a missed run
DAY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SIDECAR_SUFFIX = "_details.json"


# ----------------------------------------------------------------- guard ---

def street_in_text(street: str, text: str) -> bool:
    """The wrong-notice guard both fetchers apply: the row's own street (first
    two tokens) must appear in the text. Streetless rows pass (nothing to
    check against). Shared so reused text is judged exactly like fetched."""
    parts = (street or "").split()
    if not parts:
        return True
    pat = re.escape(parts[0])
    if len(parts) > 1:
        pat += r"\s+" + re.escape(parts[1])
    return re.search(pat, text or "", re.I) is not None


def is_republication(key: tuple, notice_id: str, results: dict) -> bool:
    """True when an ADDRESS key already resolved to a DIFFERENT id: the grid is
    newest-first, so the stored entry is the current publication and this
    older id is the weekly republication -- skip it BEFORE spending a fetch
    (the fetchers used to fetch it and then discard it). Streetless (snippet)
    keys are NOT republications; those collide and the caller drops both."""
    if key[0] != "addr":
        return False
    prev = results.get(key)
    return bool(prev) and prev.get("notice_id") != notice_id


# ---------------------------------------------------------------- budget ---

class FetchBudget:
    """Counts real fetch attempts so the abort gate ignores reused rows."""

    def __init__(self, abort_after: int = 5):
        self.abort_after = abort_after
        self.fetched_ok = 0
        self.failed = 0
        self.reused = 0
        self.skipped_republication = 0
        self.refetched_after_guard = 0

    def note_success(self) -> None:
        self.fetched_ok += 1

    def note_failure(self) -> None:
        self.failed += 1

    def should_abort(self) -> bool:
        return self.failed >= self.abort_after and self.fetched_ok == 0

    def summary(self, resolved: int) -> str:
        return (f"resolved {resolved} detail page(s): {self.fetched_ok} fetched, "
                f"{self.reused} reused from --prior, {self.failed} failed, "
                f"{self.skipped_republication} republication(s) skipped before fetching"
                + (f", {self.refetched_after_guard} prior text(s) failed the street guard "
                   f"and were re-fetched" if self.refetched_after_guard else ""))


# ----------------------------------------------------------------- prior ---

def _valid(entry: dict) -> bool:
    return bool((entry.get("notice_id") or "").strip()) and \
        len(entry.get("full_text") or "") >= MIN_TEXT_CHARS


def _load_csv(path: Path) -> dict:
    out: dict = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if _valid(r):
                nid = r["notice_id"].strip()
                out.setdefault(nid, {
                    "notice_id": nid,
                    "full_text": r["full_text"],
                    "street": r.get("street", ""),
                    "zip": r.get("zip", ""),
                    "source": str(path),
                })
    return out


def _load_sidecar(path: Path) -> dict:
    out: dict = {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data.get("details", data) if isinstance(data, dict) else {}
    for nid, e in entries.items():
        if not isinstance(e, dict):
            continue
        e = dict(e, notice_id=str(nid))
        if _valid(e):
            e.setdefault("source", str(path))
            out[str(nid)] = e
    return out


def load_prior(paths) -> dict:
    """{notice_id: {notice_id, full_text, street, zip, source}} from any mix
    of `_full.csv` files and `_details.json` sidecars. Globs allowed. Earlier
    paths win on a duplicate id (pass newest first). Missing files are
    skipped with a note, never fatal: a prior is an optimisation."""
    merged: dict = {}
    for spec in paths or []:
        expanded = sorted(glob.glob(str(spec))) or [str(spec)]
        for p in expanded:
            path = Path(p)
            if not path.is_file():
                print(f"  --prior: {path} not found -- skipped")
                continue
            try:
                got = _load_sidecar(path) if path.suffix.lower() == ".json" else _load_csv(path)
            except Exception as exc:  # a corrupt prior must not kill the leg
                print(f"  --prior: {path} unreadable ({exc}) -- skipped")
                continue
            new = 0
            for nid, e in got.items():
                if nid not in merged:
                    merged[nid] = e
                    new += 1
            print(f"  --prior: {path.name}: {len(got)} text(s), {new} new")
    return merged


def sidecar_path(out_path: Path) -> Path:
    """`<dir>/<stem>_details.json` for a `<dir>/<stem>.csv` output.
    A `_full` stem keeps it: mddc_trustee_sale_full.csv ->
    mddc_trustee_sale_full_details.json."""
    return out_path.with_name(out_path.stem + SIDECAR_SUFFIX)


def auto_prior_paths(out_path: Path, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                     today: date | None = None) -> list[Path]:
    """Sibling day dirs (`.../YYYY-MM-DD/`) of `out_path`, newest first, back
    to `lookback_days` before the run's own day dir, each contributing its
    sidecar and then its `_full.csv` of the SAME name. The run's own day dir
    is included (a re-run the same day may reuse its earlier output); the
    file about to be written is not excluded because reading our own previous
    version and then overwriting it with a superset is harmless."""
    out_path = Path(out_path)
    day_dir = out_path.parent
    if not DAY_DIR_RE.match(day_dir.name):
        print(f"  --prior-auto: {day_dir} is not a YYYY-MM-DD day dir -- no prior found")
        return []
    try:
        anchor = datetime.strptime(day_dir.name, "%Y-%m-%d").date()
    except ValueError:
        return []
    if today is not None and today > anchor:
        anchor = today
    floor = anchor - timedelta(days=lookback_days)
    found: list[Path] = []
    for d in sorted(day_dir.parent.iterdir(), reverse=True):
        if not d.is_dir() or not DAY_DIR_RE.match(d.name):
            continue
        try:
            when = datetime.strptime(d.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if when > anchor or when < floor:
            continue
        side = d / sidecar_path(out_path).name
        full = d / out_path.name
        if side.is_file():
            found.append(side)
        if full.is_file():
            found.append(full)
    return found


def write_sidecar(path: Path, details: dict, merge: bool = True) -> int:
    """Persist EVERY resolved detail (fetched or reused) keyed by notice_id.
    Written once at the end -- the leg is minutes long and a partial sidecar
    would only ever be a subset of the `_full.csv`'s own prior value.

    MERGES into an existing sidecar at `path` by default: the VA leg runs
    twice on the same output (`--fill-missing` re-resolves only the rows still
    missing an auction date), and a plain overwrite would shrink the first
    pass's complete file to that subset. New entries win on a duplicate id.
    Returns the number of entries written."""
    path = Path(path)
    merged: dict = {}
    if merge and path.is_file():
        try:
            merged = _load_sidecar(path)
        except Exception as exc:
            print(f"  sidecar {path.name} unreadable ({exc}) -- rewriting from this run only")
            merged = {}
    merged.update(details)
    details = merged
    payload = {
        "written_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(details),
        "details": {
            nid: {
                "full_text": e.get("full_text", ""),
                "street": e.get("street", ""),
                "zip": e.get("zip", ""),
                "date_published": e.get("date_published", ""),
                "auction_date": e.get("auction_date", ""),
                "loan_principal": e.get("loan_principal", ""),
                "source": e.get("source", ""),
                "fetched_at": e.get("fetched_at", ""),
            }
            for nid, e in sorted(details.items())
        },
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    tmp.replace(path)
    return len(details)


def add_prior_args(ap) -> None:
    """The three flags, identical on both fetchers."""
    ap.add_argument("--prior", action="append", default=[], metavar="PATH",
                    help="a previous run's <stem>_full.csv or <stem>_details.json (repeatable, "
                         "globs ok): a notice_id already there is copied, not fetched")
    ap.add_argument("--prior-auto", action="store_true",
                    help="find priors in the sibling output/daily_pulls/YYYY-MM-DD/ dirs "
                         "(sidecar + _full.csv of the same name), newest first")
    ap.add_argument("--prior-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                    help=f"--prior-auto lookback in days (default {DEFAULT_LOOKBACK_DAYS})")


def resolve_prior(args, out_path: Path) -> dict:
    """Explicit --prior paths first (they win on duplicates), then --prior-auto."""
    paths = list(args.prior or [])
    if getattr(args, "prior_auto", False):
        auto = auto_prior_paths(out_path, getattr(args, "prior_days", DEFAULT_LOOKBACK_DAYS))
        print(f"  --prior-auto: {len(auto)} candidate file(s) within {args.prior_days} day(s)")
        paths.extend(str(p) for p in auto)
    if not paths:
        return {}
    prior = load_prior(paths)
    print(f"prior texts available: {len(prior)}")
    return prior
