"""dialer_report.py - one cumulative coaching record per dialer, plus a manager index.

Every graded call report (the per-call .md files export_excel.py already parses) is
folded into ONE running document per dialer, written in that dialer's name:

  output/call_coaching/dialers/<Dialer Name>.md      the record, sendable as-is
  output/call_coaching/dialers/README.md             manager index: every dialer,
                                                     calls, average, latest, trend,
                                                     unmapped caller aliases

The record is designed to be handed to the dialer at any point: progress table
across every coached call, category trend (first vs latest), the criteria that
keep scoring low (the retraining list), the current drill, and a newest-first
call log with each call's strengths and fixes. Internal operational notes in a
call report (anything after the SCORES JSON footer) are never copied in.

Roster (roster.json in the project folder, see roster.example.json):
  "manager":  the cold callers' manager, printed on every record
  "dialers":  the dialers who report to them (a record is written for each, even
              with zero calls, so the file exists before the first coaching)
  "aliases":  {"name as recorded on the call report": "Dialer Name"} - callers use
              aliases on the phone; an unmapped alias gets its own record under the
              alias and is listed in README.md for the manager to resolve

USAGE (run from the project folder, after grading):
  python skills/cold-call-coach/scripts/dialer_report.py
  python skills/cold-call-coach/scripts/dialer_report.py --dir output/call_coaching/reports/cold_call
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_excel import _is_report, parse_report  # noqa: E402

ROOT = Path.cwd()
DEFAULT_DIR = ROOT / "output" / "call_coaching" / "reports" / "cold_call"
OUT_DIR = ROOT / "output" / "call_coaching" / "dialers"

CATEGORIES = {
    "1": ("Opener and Decision-Maker Confirmation", 15),
    "2": ("Four Pillars Discovery", 30),
    "3": ("Objection Handling and List Sensitivity", 20),
    "4": ("Tonality and Call Control", 15),
    "5": ("Close and Next Step", 20),
}
CRITERIA = {
    "1.1": "Decision-maker confirmed first", "1.2": "Soft plans frame with specific property",
    "1.3": "Awkwardness named", "1.4": "Escalation discipline",
    "2.1": "Motivation asked soft, then mined", "2.2": "Timeline pinned",
    "2.3": "Condition earned and non-judgmental", "2.4": "Price last, ballpark, max two asks",
    "2.5": "Order kept, flow flexible",
    "3.1": "How did you get my info", "3.2": "List-type do-not-say discipline",
    "3.3": "Converting the no", "3.4": "Delay and busy handled small",
    "3.5": "Address earned, not demanded",
    "4.1": "Silence honored", "4.2": "Human, not script-reading",
    "4.3": "Mirroring and active listening", "4.4": "Calm and scarce, never desperate",
    "4.5": "Filler-word discipline",
    "5.1": "Time and Agenda set", "5.2": "Result and Permission given",
    "5.3": "Deal killers surfaced", "5.4": "Specific either-or booking with partner intro",
    "5.5": "Role discipline: no closing on call one",
}
BAND_ACTION = {
    "Elite": "training tape; run the next team roleplay",
    "Strong": "one targeted drill on the lowest category this week",
    "Developing": "rebuild the two weakest categories with scripted roleplay before the next session",
    "Needs Work": "back to the script daily: side-by-side review + morning roleplay until two calls score 60+",
    "Retrain": "off live dials: script recertification, shadowing, passed roleplay before returning",
}


def load_roster() -> dict:
    f = ROOT / "roster.json"
    r = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    r["aliases"] = {k.lower().strip(): v for k, v in (r.get("aliases") or {}).items()}
    return r


def call_date(rep: dict, path: Path) -> tuple[str, bool]:
    """ISO date of the call; falls back to the report file's date (flagged)."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", rep.get("datetime") or "")
    if m:
        return m.group(1), True
    return dt.date.fromtimestamp(path.stat().st_mtime).isoformat(), False


def fnum(v, nd=1) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "-"


def avg(vals) -> float | None:
    vals = [float(v) for v in vals if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def arrow(first, last) -> str:
    if first is None or last is None:
        return ""
    d = last - first
    if abs(d) < 0.05:
        return "="
    return f"{'+' if d > 0 else ''}{d:.1f}"


def collect(rep_dir: Path, roster: dict) -> tuple[dict[str, list[dict]], set[str]]:
    by_dialer: dict[str, list[dict]] = defaultdict(list)
    unmapped: set[str] = set()
    for f in sorted(rep_dir.glob("*.md")):
        if not _is_report(f):
            continue
        rep = parse_report(f)
        alias = (rep.get("caller") or "unknown").strip()
        name = roster["aliases"].get(alias.lower())
        if not name:
            name = alias.title()
            unmapped.add(alias)
        rep["dialer"], rep["alias"] = name, alias
        rep["date"], rep["date_known"] = call_date(rep, f)
        rep["path"] = f
        by_dialer[name].append(rep)
    for name in roster.get("dialers") or []:
        by_dialer.setdefault(name, [])
    for reps in by_dialer.values():
        reps.sort(key=lambda r: (r["date"], str(r["path"])))
    return by_dialer, unmapped


def render_dialer(name: str, reps: list[dict], roster: dict, unmapped: bool) -> str:
    today = dt.date.today().isoformat()
    manager = roster.get("manager") or "-"
    full = [r for r in reps if r["call_type"] == "full"]
    short = [r for r in reps if r["call_type"] == "short"]
    L: list[str] = []
    L.append(f"# Cold Call Coaching Record - {name}")
    L.append("")
    L.append(f"Manager: {manager}    Updated: {today}    Calls coached: {len(reps)} "
             f"({len(full)} full, {len(short)} short)")
    if unmapped:
        L.append("")
        L.append(f"> This record is filed under the caller alias \"{name}\" as heard on the call. "
                 f"Map it to a dialer in roster.json (\"aliases\") to merge it into their record.")
    L.append("")
    L.append("Full calls are graded /100 against the DataSift cold-calling rubric (opener 15, four "
             "pillars 30, objections 20, tonality 15, close 20). Short calls (a decline inside ~30s) "
             "are on their own scale and never mixed in. Totals within 5 points are equivalent.")
    L.append("")

    if not reps:
        L.append("No coached calls on file yet.")
        return "\n".join(L) + "\n"

    # --- headline
    if full:
        latest = full[-1]
        first = full[0]
        L.append("## Where things stand")
        L.append("")
        L.append(f"- Latest full call: **{fnum(latest['total'])} / 100 - {latest['band']}** "
                 f"({latest['date']}, outcome: {latest['outcome']})")
        if len(full) > 1:
            tot = avg([float(r["total"]) for r in full if r["total"] not in ("", None)])
            L.append(f"- Average over {len(full)} full calls: **{fnum(tot)}**; "
                     f"first -> latest: {fnum(first['total'])} -> {fnum(latest['total'])} "
                     f"({arrow(float(first['total']), float(latest['total']))})")
        act = BAND_ACTION.get(latest["band"])
        if act:
            L.append(f"- Band prescription: {act}.")
        fails = [r for r in reps if r["autofail"].upper().startswith("FAIL")]
        if fails:
            L.append(f"- **Auto-fails on file: {len(fails)}** - manager review required "
                     f"({', '.join(r['call_id'] for r in fails)}).")
        L.append("")

    # --- progress table
    if full:
        L.append("## Progress - full calls")
        L.append("")
        L.append("| # | Date | Call | Length | Outcome | Auto-fail | Total | Band |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(full, 1):
            d = r["date"] + ("" if r["date_known"] else " (filed)")
            L.append(f"| {i} | {d} | {r['call_id']} | {r['length'] or '-'} | {r['outcome'] or '-'} | "
                     f"{r['autofail'] or '-'} | {fnum(r['total'])} | {r['band'] or '-'} |")
        L.append("")
    if short:
        L.append("## Progress - short calls (own scale)")
        L.append("")
        L.append("| # | Date | Call | Length | Outcome | Opener /5 | Conversion /5 | Attempt made |")
        L.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(short, 1):
            att = {True: "yes", False: "no"}.get(r["attempted"], "-")
            L.append(f"| {i} | {r['date']} | {r['call_id']} | {r['length'] or '-'} | {r['outcome'] or '-'} | "
                     f"{fnum(r['opener'], 2)} | {fnum(r['conversion'], 2)} | {att} |")
        L.append("")

    # --- category trend
    if full:
        L.append("## Category trend (0-5)")
        L.append("")
        L.append("| Category | Weight | First | Latest | Change | Average |")
        L.append("|---|---|---|---|---|---|")
        for k, (cname, w) in CATEGORIES.items():
            vals = [r["cats_js"].get(k) for r in full if isinstance(r["cats_js"].get(k), (int, float))]
            f0 = full[0]["cats_js"].get(k)
            f1 = full[-1]["cats_js"].get(k)
            L.append(f"| {k}. {cname} | {w}% | {fnum(f0, 2)} | {fnum(f1, 2)} | "
                     f"{arrow(f0 if isinstance(f0, (int, float)) else None, f1 if isinstance(f1, (int, float)) else None)} | "
                     f"{fnum(avg(vals), 2)} |")
        L.append("")

    # --- recurring weaknesses / strengths across calls
    crit_avg: dict[str, tuple[float, int, int]] = {}
    for k in CRITERIA:
        vals = []
        for r in full:
            v = r["crits"].get(k)
            if isinstance(v, (int, float)):
                vals.append(float(v))
            elif isinstance(v, str) and re.fullmatch(r"[0-5](\.\d+)?", v):
                vals.append(float(v))
        if vals:
            crit_avg[k] = (sum(vals) / len(vals), len(vals), sum(1 for v in vals if v <= 1))
    if crit_avg:
        weak = sorted(crit_avg.items(), key=lambda kv: (kv[1][0], -kv[1][2]))[:6]
        strong = sorted(crit_avg.items(), key=lambda kv: -kv[1][0])[:3]
        L.append("## Retraining list - criteria that keep scoring low")
        L.append("")
        L.append("Averaged across every scored full call; \"at 0-1\" counts the calls where the "
                 "criterion scored 1 or less. Fix from the top down.")
        L.append("")
        L.append("| Criterion | Average /5 | Scored on | At 0-1 |")
        L.append("|---|---|---|---|")
        for k, (a, n, low) in weak:
            L.append(f"| {k} {CRITERIA[k]} | {a:.1f} | {n} | {low} |")
        L.append("")
        L.append("Strongest habits: " + "; ".join(f"{k} {CRITERIA[k]} ({a:.1f})" for k, (a, _, _) in strong))
        L.append("")

    # --- current focus
    latest_any = reps[-1]
    L.append("## Current focus")
    L.append("")
    if latest_any["call_type"] == "full" and latest_any["improvements"]:
        L.append(f"From the latest call ({latest_any['date']}):")
        L.append("")
        for it in latest_any["improvements"]:
            L.append(f"- {it}")
        L.append("")
    elif latest_any["fix"]:
        L.append(f"- {latest_any['fix']}")
        L.append("")
    if latest_any["drill"]:
        L.append(f"**Drill this week:** {latest_any['drill']}")
        L.append("")

    # --- call log, newest first
    L.append("## Call log (newest first)")
    L.append("")
    for r in reversed(reps):
        try:
            rel = Path(os.path.relpath(r["path"], OUT_DIR)).as_posix()
        except ValueError:  # different drive
            rel = Path(r["path"]).as_posix()
        if r["call_type"] == "full":
            head = f"{fnum(r['total'])} / 100 - {r['band']}"
        else:
            head = f"short call - opener {fnum(r['opener'], 2)}, conversion {fnum(r['conversion'], 2)}"
        L.append(f"### {r['date']} - call {r['call_id']} - {head}")
        L.append("")
        L.append(f"Length {r['length'] or '-'} | Outcome: {r['outcome'] or '-'} | Auto-fail: {r['autofail'] or '-'}"
                 + (f" | Alias on call: {r['alias']}" if r["alias"].lower() != name.lower() else "")
                 + f" | [full report]({rel})")
        L.append("")
        if r["pillars"]:
            L.append(f"Pillars: {r['pillars']}")
            L.append("")
        if r["happened"]:
            L.append(f"What happened: {r['happened']}")
            L.append("")
        if r["strengths"]:
            L.append("Did well:")
            for s in r["strengths"]:
                L.append(f"- {s}")
            L.append("")
        if r["improvements"]:
            L.append("Fix:")
            for s in r["improvements"]:
                L.append(f"- {s}")
            L.append("")
        elif r["fix"]:
            L.append(f"Fix: {r['fix']}")
            L.append("")
        if r["drill"]:
            L.append(f"Drill: {r['drill']}")
            L.append("")
    return "\n".join(L) + "\n"


def render_index(by_dialer: dict[str, list[dict]], roster: dict, unmapped: set[str]) -> str:
    today = dt.date.today().isoformat()
    L = [f"# Cold Call Coaching - team index", "",
         f"Manager: {roster.get('manager') or '-'}    Updated: {today}", "",
         "One cumulative record per dialer in this folder; each is sendable as-is.", "",
         "| Dialer | Calls | Full | Avg /100 | Latest /100 | Latest band | First -> latest | Record |",
         "|---|---|---|---|---|---|---|---|"]
    order = list(roster.get("dialers") or []) + sorted(n for n in by_dialer if n not in (roster.get("dialers") or []))
    for name in order:
        reps = by_dialer.get(name, [])
        full = [r for r in reps if r["call_type"] == "full" and r["total"] not in ("", None)]
        a = avg([float(r["total"]) for r in full])
        latest = full[-1] if full else None
        trend = arrow(float(full[0]["total"]), float(full[-1]["total"])) if len(full) > 1 else ""
        L.append(f"| {name} | {len(reps)} | {len(full)} | {fnum(a)} | "
                 f"{fnum(latest['total']) if latest else '-'} | {latest['band'] if latest else '-'} | "
                 f"{trend} | [{name}.md]({name}.md) |")
    L.append("")
    if unmapped:
        L.append("## Unmapped caller aliases")
        L.append("")
        L.append("These names were heard on calls but are not in roster.json \"aliases\", so each has "
                 "its own record above. Add `\"alias\": \"Dialer Name\"` to fold them in.")
        L.append("")
        for a in sorted(unmapped):
            L.append(f"- {a}")
        L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Per-dialer cumulative coaching records")
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help="graded report directory")
    ap.add_argument("--out", default=str(OUT_DIR), help="output folder for the records")
    args = ap.parse_args()

    roster = load_roster()
    by_dialer, unmapped = collect(Path(args.dir), roster)
    if not by_dialer:
        print(f"No graded reports found in {args.dir}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, reps in by_dialer.items():
        alias_only = name.lower() in {u.lower() for u in unmapped}
        (out / f"{name}.md").write_text(render_dialer(name, reps, roster, alias_only), encoding="utf-8")
        print(f"  {name:<22} {len(reps)} call(s) -> {out / (name + '.md')}")
    (out / "README.md").write_text(render_index(by_dialer, roster, unmapped), encoding="utf-8")
    if unmapped:
        print(f"Unmapped aliases (add to roster.json): {', '.join(sorted(unmapped))}")
    print(f"-> {out / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
