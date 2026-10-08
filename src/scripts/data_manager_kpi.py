"""data_manager_kpi.py - Mohamed Hisham's output and KPIs from the Probate Extraction 2 sheet.

READ-ONLY on everything: downloads the sheet's xlsx export (anyone-with-link viewer, never
written to), reads Hubstaff hours over its API, and optionally reads the DataSift
`Register of Wills Probates` list. Basem, 2026-10-07: judge him on the Register of Wills,
Deep Dive and Legal Notices tabs only (NOT Sheet26, which was a one-off batch).

What the sheet can and cannot tell:
  - "Published on" is the court's filing date, the only per-row date on intake. An estate
    whose filing date falls in the period is counted as that period's intake.
  - "Last PR Check" is HIS activity stamp: the day he last looked the PR up.
  - The deep-dive and address pipeline (Calc_Data "Priority Tier", Deep Dive "Address Status")
    has no dates at all. So every run saves a snapshot, and progress = the difference from
    the previous snapshot (first run: backlog only, no progress line).

  python src/scripts/data_manager_kpi.py --month 2026-09            # a month
  python src/scripts/data_manager_kpi.py --from 2026-10-01 --to 2026-10-07 --account
Output: output/team_kpi/data_manager/<from>_<to>/{kpi.json,report.md,estates.csv};
snapshots in output/team_kpi/data_manager/snapshots/<date>.json.
"""
from __future__ import annotations

import os
# numpy (pulled in by openpyxl and the shared DataSift helper) must not reserve one OpenBLAS
# buffer per core: on this PC the Windows commit limit runs near full (2026-10-07 20:00 failure)
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse
import collections as C
import csv
import datetime as D
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "src")]

SHEET = "https://docs.google.com/spreadsheets/d/1isF9fSS97XffuXZbaYkMklCVTkDczCCoKAA5qhUkiao/export?format=xlsx"
BASE = ROOT / "output" / "team_kpi" / "data_manager"
SNAP = BASE / "snapshots"
PERSON = "Mohamed Hisham"
ROW_LIST = "b8815666-9e6e-4d41-8a7c-2416e3d65ac5"   # Register of Wills Probates
TABS = {"Register of Wills": "RoW", "Legal Notices (Archived)": "LN"}
FOOTPRINT = {"Montgomery", "Anne Arundel", "Frederick"}
PLACEHOLDER = re.compile(r"^\s*$|not found|no pr|ignored|deep dive|sold$|transferred$|#n/a", re.I)
TIERS = ["Done", "👀 PR Watch", "🤖 Claude MLS Dive", "🔍 Manual MLS Dive", "🏛️ Land Records Dive",
         "🛑 Dive Failed", "Ignored"]


def pdate(v) -> D.date | None:
    if isinstance(v, D.datetime):
        return v.date()
    if isinstance(v, D.date):
        return v
    if isinstance(v, (int, float)) and 40000 < v < 60000:
        return D.date(1899, 12, 30) + D.timedelta(days=int(v))
    if isinstance(v, str):
        for f in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%Y-%m-%d %H:%M:%S"):
            try:
                return D.datetime.strptime(v.strip(), f).date()
            except ValueError:
                pass
    return None


def estate_id(v) -> str:
    s = str(v or "").strip()
    return s[:-2] if s.endswith(".0") else s


def county(v) -> str:
    return re.sub(r"\s+County$", "", str(v or "").strip())


def download(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(SHEET, headers={"user-agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        if r.status != 200:
            raise RuntimeError(f"sheet export HTTP {r.status} - sharing may have changed; ask Basem")
        dest.write_bytes(r.read())
    return dest


def read_sheet(path: Path) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    def table(name):
        rows = [r for r in wb[name].iter_rows(values_only=True) if r and any(v not in (None, "") for v in r)]
        # Calc_Data's estate column has a blank header: name blanks by position
        H = [(str(h).strip() if h is not None else "") or f"_col{i}" for i, h in enumerate(rows[0])]
        return [{h: (r[i] if i < len(r) else None) for i, h in enumerate(H)} for r in rows[1:]]

    estates = []
    for tab, src in TABS.items():
        for r in table(tab):
            eid = estate_id(r.get("Estate #"))
            if not eid or eid == "#N/A":
                continue
            addr = re.sub(r"\s+", " ", str(r.get("Property Address") or "")).strip()
            estates.append({
                "tab": src, "estate": eid, "county": county(r.get("County")),
                "decedent": f"{r.get('Deceased First Name') or ''} {r.get('Deceased Last Name') or ''}".strip(),
                "address": addr, "zip": str(r.get("Property Zipcode") or "").replace(".0", "")[:5],
                "has_address": bool(addr) and not PLACEHOLDER.search(addr),
                "placeholder": (addr or "(blank)") if (not addr or PLACEHOLDER.search(addr)) else None,
                "pr": f"{r.get('PR First Name') or ''} {r.get('PR Last Name') or ''}".strip(),
                "published": pdate(r.get("Published on")),
                "pr_check": pdate(r.get("Last PR Check")),
            })
    calc = {}
    for r in table("Calc_Data"):
        eid = estate_id(r.get("_col0"))
        if eid and eid != "#N/A":
            calc[eid] = {"tier": str(r.get("Priority Tier") or ""), "source": str(r.get("Data Source") or ""),
                         "address": str(r.get("Fixed Property Address") or ""),
                         "pr_check": pdate(r.get("Last PR Check"))}
    dive = {}
    for r in table("Deep Dive"):
        eid = estate_id(r.get("Estate #"))
        if eid and eid != "#N/A" and eid != "No records found":
            dive[eid] = {"county": county(r.get("County")), "status": str(r.get("Address Status") or "")}
    return {"estates": estates, "calc": calc, "dive": dive}


def hubstaff_hours(f: D.date, t: D.date) -> tuple[float | None, float | None, str | None]:
    try:
        import team_kpi as tk
        cfg = tk.load_config()
        rows, _ = tk.hubstaff_api_rows(f, t, cfg)
        h = sum(r["hours"] for r in rows if r["person"] == PERSON)
        return round(h, 2), cfg["people"][PERSON]["rate"], None
    except Exception as e:  # noqa: BLE001
        return None, None, f"Hubstaff unavailable: {str(e)[:150]}"


def account_row_list() -> set[str]:
    """sift_key of every record on the Register of Wills Probates list (read-only query)."""
    from dpd_lane_phone_score import Api
    from obituary_dp_batch import sift_key
    api, keys, off = Api(), set(), 0
    while True:
        st, r = api.post_as_get("/api/internal/property/",
                                {"limit": 100, "offset": off, "query": {"must": {"any_lists": [ROW_LIST]}}})
        if st != 200:
            raise RuntimeError(f"DataSift query HTTP {st}")
        rows = r.get("results") or []
        for x in rows:
            a = x.get("address") or {}
            keys.add(sift_key(a.get("street") or "", a.get("zip5") or ""))
        off += 100
        if len(rows) < 100:
            return keys


def snapshot_diff(today: dict, prev: dict | None) -> dict | None:
    if not prev:
        return None
    pc, tc = prev["calc"], today["calc"]
    moved = C.Counter()
    newly_done = 0
    for e, v in tc.items():
        before = pc.get(e, {}).get("tier")
        if before is not None and before != v["tier"]:
            moved[(before, v["tier"])] += 1
            newly_done += v["tier"] == "Done"
    pd_, td = prev["dive"], today["dive"]
    dive_resolved = sum(1 for e, v in pd_.items()
                        if v["status"] in ("Not Found", "Not Found (Claude)", "")
                        and (td.get(e) is None or td[e]["status"] not in ("Not Found", "Not Found (Claude)", "")))
    return {"since": prev["taken"], "newly_done": newly_done, "dive_resolved": dive_resolved,
            "new_estates": len(set(tc) - set(pc)),
            "moves": [{"from": a, "to": b, "n": n} for (a, b), n in moved.most_common(12)]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--month")
    ap.add_argument("--from", dest="f")
    ap.add_argument("--to", dest="t")
    ap.add_argument("--xlsx", help="use this export instead of downloading")
    ap.add_argument("--account", action="store_true", help="also check the DataSift RoW list (read-only)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    today = D.date.today()
    if a.month:
        y, m = map(int, a.month.split("-"))
        f = D.date(y, m, 1)
        t = D.date(y + (m == 12), m % 12 + 1, 1) - D.timedelta(days=1)
    elif a.f and a.t:
        f, t = D.date.fromisoformat(a.f), D.date.fromisoformat(a.t)
    else:
        t, f = today, today - D.timedelta(days=6)
    od = BASE / f"{f}_{t}"
    od.mkdir(parents=True, exist_ok=True)
    xlsx = Path(a.xlsx) if a.xlsx else download(BASE / "pe2_latest.xlsx")
    s = read_sheet(xlsx)
    E = [e for e in s["estates"] if e["county"] in FOOTPRINT]

    # intake: estates whose filing date is in the period (first row per estate)
    seen, intake = set(), []
    for e in sorted(E, key=lambda e: (e["published"] or D.date.max, e["tab"])):
        if e["estate"] in seen:
            continue
        seen.add(e["estate"])
        if e["published"] and f <= e["published"] <= t:
            intake.append(e)
    pr_checks = [e for e in E if e["pr_check"] and f <= e["pr_check"] <= t]
    pr_check_days = C.Counter(str(e["pr_check"]) for e in pr_checks)
    addressed = [e for e in intake if e["has_address"]]
    tiers = C.Counter(v["tier"] for v in s["calc"].values())
    stale_watch = sum(1 for v in s["calc"].values() if v["tier"] == "👀 PR Watch"
                      and (not v["pr_check"] or (today - v["pr_check"]).days > 14))
    dive_status = C.Counter("Not Found" if v["status"].startswith("Not Found") else
                            (v["status"] if v["status"] in ("Sold", "Transferred", "") else "address found")
                            for v in s["dive"].values())

    # snapshot for progress tracking
    SNAP.mkdir(parents=True, exist_ok=True)
    snap = {"taken": today.isoformat(), "calc": {e: {"tier": v["tier"]} for e, v in s["calc"].items()},
            "dive": s["dive"]}
    prevs = sorted(p for p in SNAP.glob("*.json") if p.stem < today.isoformat())
    prev = json.loads(prevs[-1].read_text(encoding="utf-8")) if prevs else None
    (SNAP / f"{today}.json").write_text(json.dumps(snap), encoding="utf-8")
    progress = snapshot_diff(snap, prev)

    hours, rate, herr = hubstaff_hours(f, t)
    on_list = None
    if a.account:
        from obituary_dp_batch import sift_key
        keys = account_row_list()
        for e in addressed:
            e["on_row_list"] = sift_key(e["address"], e["zip"]) in keys
        on_list = sum(1 for e in addressed if e["on_row_list"])

    cost = round(hours * rate, 2) if hours is not None else None
    k = {
        "person": PERSON, "period": [str(f), str(t)], "sheet": str(xlsx), "tabs": list(TABS),
        "hours": hours, "invoice": cost, "hubstaff_error": herr,
        "intake_estates": len(intake),
        "intake_by_county": dict(C.Counter(e["county"] for e in intake)),
        "intake_by_tab": dict(C.Counter(e["tab"] for e in intake)),
        "intake_with_address": len(addressed),
        "intake_with_pr": sum(1 for e in intake if e["pr"]),
        "intake_placeholders": dict(C.Counter(e["placeholder"] for e in intake if e["placeholder"]).most_common(6)),
        "pr_checks": len(pr_checks), "pr_check_days": len(pr_check_days),
        "estates_per_hour": round(len(intake) / hours, 2) if hours else None,
        "addresses_per_hour": round(len(addressed) / hours, 2) if hours else None,
        "pr_checks_per_hour": round(len(pr_checks) / hours, 2) if hours else None,
        "cost_per_address": round(cost / len(addressed), 2) if cost and addressed else None,
        "pipeline": {t_: tiers.get(t_, 0) for t_ in TIERS},
        "pr_watch_overdue_14d": stale_watch,
        "deep_dive_tab": dict(dive_status),
        "progress_since_last_snapshot": progress,
        "addressed_on_row_list": on_list,
        "by_day": {str(d): {"intake": sum(1 for e in intake if e["published"] == d),
                            "pr_checks": pr_check_days.get(str(d), 0)}
                   for d in (f + D.timedelta(days=i) for i in range((t - f).days + 1))},
    }
    (od / "kpi.json").write_text(json.dumps(k, indent=1, default=str), encoding="utf-8")
    with open(od / "estates.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["tab", "estate", "county", "published", "decedent", "address", "zip", "pr",
                    "has_address", "placeholder", "pr_check", "on_row_list"])
        for e in intake:
            w.writerow([e["tab"], e["estate"], e["county"], e["published"], e["decedent"], e["address"], e["zip"],
                        e["pr"], e["has_address"], e["placeholder"] or "", e["pr_check"] or "", e.get("on_row_list", "")])
    print(json.dumps({k_: v for k_, v in k.items() if k_ != "by_day"}, indent=1, default=str, ensure_ascii=False))
    print(f"OUT: {od}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
