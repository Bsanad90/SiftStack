"""team_kpi.py - monthly team KPI + invoice review (hours vs dials vs leads).

The deterministic half of the `team-kpi-reviewer` agent. Three manual exports go in,
one KPI file comes out; the agents do the judging, this does the arithmetic.

  Hubstaff   Reports -> Time & activity, by member by day, CSV
             (time-activity-report_<from>_to_<to>.csv)           -> hours, idle, invoice
  SmrtPhone  Interaction Logs -> Export CSV (interaction-logs*.csv) -> dials, dispositions,
                                                                      active dial hours
  WhatsApp   Team chat -> Export chat (zip with chat.txt)          -> lead posts
             (the whatsapp agent turns messages.json into leads.json; this script never
             judges a lead)

Subcommands (all read-only, everything lands in output/team_kpi/<from>_<to>/):
  find       locate the three exports for the period and report coverage
  smrtphone  SmrtPhone API (src/smrtphone_segments.py), CSV fallback -> smrtphone.json
  hubstaff   Hubstaff API (src/hubstaff_client.py), CSV fallback -> hubstaff.json
  whatsapp   parse the chat export              -> messages.json (+ lead candidates)
  report     join everything (+ leads.json)     -> kpi.json + tables.md
  daily      one day from both APIs (no leads)  -> output/team_kpi/daily/<date>.txt (8 PM pop-up)
  weekly     7 days ending --date (or --from/--to) -> output/team_kpi/weekly/<from>_<to>.txt

Period: --month 2026-09, or --from 2026-09-01 --to 2026-09-30 (default: last full month).
People, rates and name mapping: team_kpi_config.json at the repo root (gitignored; the
committed template is team_kpi_config.example.json).

Definitions (fixed 2026-10-06 in the September review, keep them stable month to month):
  dial               an OUTBOUND row in the SmrtPhone export
  conversation       outbound status in HUMAN (a person picked up)
  right-party (RPC)  outbound status in RPC (reached the owner)
  voicemail rate     "No Answer, Left VM" / (Left VM + No VM)
  no disposition     blank or "No Disposition" / dials
  active dial hours  sum of gaps <= gap_minutes between consecutive dials, + the last call
  slack hours        per day max(paid x (1 - management_share) - active - daily_allowance, 0),
                     callers only; a data manager is never judged on dials
  management_share   per-person share of paid hours that is management (Bagoury 0.3): taken
                     off dial hours, slack and cost-per-contact, never off the invoice
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
import io
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "team_kpi_config.json"
OUT_BASE = ROOT / "output" / "team_kpi"
HOME = Path.home()
SEARCH_DIRS = [HOME / "Downloads", ROOT.parent, ROOT / "output" / "team_kpi" / "inbox"]

HUMAN = {"Hung up", "Not Interested", "Not Related to Property", "Correct Number", "Wrong Number",
         "DNC Correct", "DNC Wrong", "Already Sold", "DNC"}
RPC = {"Correct Number", "Not Interested", "DNC Correct", "Already Sold"}
VM_LEFT, VM_NONE = "No Answer, Left VM", "No Answer, No VM"
NO_DISPO = {"", "No Disposition"}


# ---------------------------------------------------------------- helpers
def die(msg: str, code: int = 2):
    print(json.dumps({"ok": False, "error": msg}, indent=1))
    sys.exit(code)


def load_config() -> dict:
    if not CONFIG.exists():
        die(f"{CONFIG.name} missing - copy team_kpi_config.example.json and fill in rates")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    idx = {"hubstaff": {}, "smrtphone": {}, "whatsapp": {}}
    for person, p in cfg["people"].items():
        for src in idx:
            for alias in p.get(src, []):
                idx[src][alias.strip().lower()] = person
    cfg["_idx"] = idx
    return cfg


def period(a) -> tuple[D.date, D.date]:
    if a.month:
        y, m = map(int, a.month.split("-"))
        f = D.date(y, m, 1)
        t = (D.date(y + (m == 12), m % 12 + 1, 1) - D.timedelta(days=1))
        return f, t
    if a.day_from and a.day_to:
        return D.date.fromisoformat(a.day_from), D.date.fromisoformat(a.day_to)
    first_this = D.date.today().replace(day=1)
    t = first_this - D.timedelta(days=1)
    return t.replace(day=1), t


def out_dir(f: D.date, t: D.date) -> Path:
    d = OUT_BASE / f"{f}_{t}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def hms(s: str) -> float:
    a = (s or "0:0:0").split(":")
    if len(a) != 3:
        return 0.0
    return int(a[0]) + int(a[1]) / 60 + int(a[2]) / 3600


def _secs(s: str) -> int:
    # the export prefixes numbers with ' (spreadsheet-safe) and writes a few negatives
    v = re.sub(r"[^\d-]", "", s or "")
    try:
        return max(int(v), 0)
    except ValueError:
        return 0


def digits(s: str) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else d


def emit(obj):
    print(json.dumps(obj, indent=1, default=str))


# ---------------------------------------------------------------- file discovery
def _csv_dates(path: Path, col: str) -> tuple[str, str, int]:
    with open(path, encoding="utf-8-sig") as fh:
        ds = [r.get(col, "") for r in csv.DictReader(fh)]
    ds = [d for d in ds if re.match(r"\d{4}-\d{2}-\d{2}$", d or "")]
    return (min(ds), max(ds), len(ds)) if ds else ("", "", 0)


def find_smrtphone(f: D.date, t: D.date, explicit: str | None = None) -> dict:
    cands = [Path(explicit)] if explicit else [p for d in SEARCH_DIRS if d.exists()
                                                for p in d.glob("interaction-logs*.csv")]
    best = None
    seen = []
    for p in sorted(cands, key=lambda p: p.stat().st_mtime, reverse=True):
        lo, hi, n = _csv_dates(p, "Date")
        ok = bool(lo) and lo <= str(f + D.timedelta(days=3)) and hi >= str(t - D.timedelta(days=3))
        seen.append({"file": str(p), "first": lo, "last": hi, "rows": n, "covers": ok})
        if ok and best is None:
            best = seen[-1]
    return {"chosen": best, "candidates": seen}


def find_hubstaff(f: D.date, t: D.date, explicit: str | None = None) -> dict:
    cands = [Path(explicit)] if explicit else [p for d in SEARCH_DIRS if d.exists()
                                                for p in d.glob("time-activity-report_*.csv")]
    best, seen = None, []
    for p in sorted(cands, key=lambda p: p.stat().st_mtime, reverse=True):
        m = re.search(r"(\d{4}-\d{2}-\d{2})_to_(\d{4}-\d{2}-\d{2})", p.name)
        lo, hi, n = _csv_dates(p, "Date")
        rng = (m.group(1), m.group(2)) if m else (lo, hi)
        ok = bool(rng[0]) and rng[0] <= str(f) and rng[1] >= str(t)
        seen.append({"file": str(p), "range": rng, "first_row": lo, "last_row": hi,
                     "rows": n, "covers": ok, "exact": rng == (str(f), str(t))})
    for s in sorted(seen, key=lambda s: (not s["exact"], not s["covers"])):
        if s["covers"]:
            best = s
            break
    return {"chosen": best, "candidates": seen}


def _chat_text(p: Path) -> str | None:
    try:
        with zipfile.ZipFile(p) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".txt")]
            pick = next((n for n in names if "chat" in n.lower()), names[0] if names else None)
            return z.read(pick).decode("utf-8", "replace") if pick else None
    except (zipfile.BadZipFile, OSError):
        return None


def find_whatsapp(f: D.date, t: D.date, explicit: str | None = None) -> dict:
    if explicit:
        cands = [Path(explicit)]
    else:
        cands = [p for d in SEARCH_DIRS if d.exists() for p in d.glob("*.zip")
                 if "whatsapp" in p.name.lower() or "dream team" in p.name.lower()
                 or "chat" in p.name.lower()]
    best, seen = None, []
    for p in sorted(cands, key=lambda p: p.stat().st_mtime, reverse=True):
        txt = _chat_text(p)
        if not txt:
            continue
        msgs = parse_chat(txt)
        lo = min((m["ts"][:10] for m in msgs), default="")
        hi = max((m["ts"][:10] for m in msgs), default="")
        exported = D.date.fromtimestamp(p.stat().st_mtime)
        in_period = sum(1 for m in msgs if str(f) <= m["ts"][:10] <= str(t))
        # exported after the period closed and holds messages inside it; a chat whose first
        # message is late in the period may be a trimmed export, so say so
        ok = in_period > 0 and exported >= t
        seen.append({"file": str(p), "first_msg": lo, "last_msg": hi, "messages": len(msgs),
                     "in_period": in_period, "file_date": str(exported), "covers": ok,
                     "warning": ("first message is after the period start - confirm the export is "
                                 "the full chat") if lo > str(f + D.timedelta(days=7)) else None})
        if ok and best is None:
            best = seen[-1]
    return {"chosen": best, "candidates": seen}


# ---------------------------------------------------------------- extractors
def smrtphone_api_rows(f: D.date, t: D.date, cfg) -> tuple[list[dict], dict]:
    """Every call segment in the period from the SmrtPhone v2 API, one walk per user."""
    sys.path.insert(0, str(ROOT / "src"))
    import smrtphone_segments as ss
    users = {int(k): v for k, v in (cfg.get("smrtphone_user_ids") or {}).items()}
    # a seat added since the config was written: the newest 2,000 segments would show it
    for p in range(1, 21):
        for x in ss._page({}, p):
            if x.get("user_id") and x["user_id"] not in users:
                users[x["user_id"]] = ss.user_name(x["user_id"])
    idx = cfg["_idx"]["smrtphone"]
    rows, per_user = [], {}
    for uid, name in sorted(users.items()):
        segs = [x for x in ss.segments_for_user(uid, f, t) if x.get("segment_type", "").endswith("_call")]
        per_user[name] = len(segs)
        for x in segs:
            rows.append({"date": x["_et"].strftime("%Y-%m-%d"), "time": x["_et"].strftime("%H:%M"),
                         "agent": name, "person": idx.get(name.strip().lower()),
                         "direction": (x.get("call_direction") or "").capitalize(),
                         "participant": x.get("contact_name") or "",
                         "participant_number": digits(x.get("phone_number")),
                         "agent_number": digits(x.get("smrtphone_number")),
                         "duration": max(int(x.get("duration") or 0), 0),
                         "outcome": x.get("call_status") or "", "status": x.get("disposition_code") or "",
                         "segment_id": x["id"], "contact_id": x.get("contact_id")})
    # Outbound calls SmrtPhone stored with NO user (user_id null) never show up in a per-user
    # walk: 2026-10-07, Bagoury's 4m20s Correct Number call that produced the Angela Doyle lead
    # (10-04) was one. Walk outbound segments newest first to the period start and credit each
    # unassigned one to the seat that owns its caller-ID number.
    from zoneinfo import ZoneInfo
    owner_of = {}
    for num, n in C.Counter((r["agent_number"], r["agent"]) for r in rows if r["direction"] == "Outbound").most_common():
        owner_of.setdefault(num[0], num[1])
    start = D.datetime.combine(f, D.time.min, ZoneInfo("America/New_York"))
    unassigned, reached = 0, False
    for p in range(1, ss.WINDOW // ss.PAGE + 1):
        items = ss._page({"segment_type": "outbound_call"}, p)
        if not items:
            reached = True
            break
        for x in items:
            at = D.datetime.fromisoformat(x["interaction_at"]).astimezone(ZoneInfo("America/New_York"))
            if at < start:
                reached = True
                break
            if x.get("user_id") or at.date() > t:
                continue
            name = owner_of.get(digits(x.get("smrtphone_number")))
            if not name:
                continue
            unassigned += 1
            rows.append({"date": at.strftime("%Y-%m-%d"), "time": at.strftime("%H:%M"), "agent": name,
                         "person": idx.get(name.strip().lower()), "direction": "Outbound",
                         "participant": x.get("contact_name") or "", "participant_number": digits(x.get("phone_number")),
                         "agent_number": digits(x.get("smrtphone_number")),
                         "duration": max(int(x.get("duration") or 0), 0), "outcome": x.get("call_status") or "",
                         "status": x.get("disposition_code") or "", "segment_id": x["id"],
                         "contact_id": x.get("contact_id"), "attributed_by": "caller_id"})
        if reached:
            break
    return rows, {"users": {str(k): v for k, v in users.items()}, "calls_per_user": per_user,
                  "unassigned_outbound_credited": unassigned, "unassigned_walk_complete": reached}


def cmd_smrtphone(a, cfg):
    f, t = period(a)
    if a.source in ("auto", "api") and not a.csv:
        try:
            rows, meta = smrtphone_api_rows(f, t, cfg)
            return _write_smrtphone(f, t, rows, "SmrtPhone API /api/v1/logs/segments", {"source": "api", **meta})
        except Exception as e:  # noqa: BLE001 - fall back to the CSV export, say why
            if a.source == "api":
                die(f"SmrtPhone API failed: {e}")
            api_err = str(e)[:300]
    else:
        api_err = None
    hit = find_smrtphone(f, t, a.csv)
    if not hit["chosen"]:
        emit({"ok": False, "status": "NEEDS_EXPORT", "period": [f, t], **hit})
        return 3
    path = Path(hit["chosen"]["file"])
    idx = cfg["_idx"]["smrtphone"]
    rows = []
    with open(path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if not (str(f) <= r["Date"] <= str(t)):
                continue
            person = idx.get(r["Agent"].strip().lower())
            rows.append({"date": r["Date"], "time": r["Time"], "agent": r["Agent"], "person": person,
                         "direction": r["Direction"], "participant": r["Participant"],
                         "participant_number": digits(r["Participant Number"]),
                         "agent_number": digits(r["Agent Number"]),
                         "duration": _secs(r["Duration (sec)"]),
                         "outcome": r["System Outcome"], "status": r["Status"]})
    return _write_smrtphone(f, t, rows, str(path), {"source": "csv", "coverage": hit["chosen"],
                                                     "api_error": api_err})


def _write_smrtphone(f, t, rows, label, extra):
    unmapped = C.Counter(r["agent"] for r in rows if r["agent"] and not r["person"])
    od = out_dir(f, t)
    summ = C.defaultdict(C.Counter)
    for r in rows:
        summ[r["person"] or f"(unmapped) {r['agent'] or 'no agent'}"][r["direction"]] += 1
    (od / "smrtphone.json").write_text(json.dumps(
        {"source": label, "period": [str(f), str(t)], "rows": rows}, indent=0), encoding="utf-8")
    emit({"ok": True, "status": "OK", "from": label, **extra,
          "rows_in_period": len(rows), "per_person": {k: dict(v) for k, v in summ.items()},
          "unmapped_agents": dict(unmapped), "out": str(od / "smrtphone.json")})
    return 0


def hubstaff_api_rows(f: D.date, t: D.date, cfg) -> tuple[list[dict], str]:
    """Daily activities from the Hubstaff API (read-only). Returns (rows, source label)."""
    sys.path.insert(0, str(ROOT / "src"))
    import hubstaff_client as hs
    org = cfg.get("hubstaff_org_id") or hs.get("/organizations")["organizations"][0]["id"]
    acts = hs.get_all(f"/organizations/{org}/activities/daily", "daily_activities",
                      {"date[start]": str(f), "date[stop]": str(t)})
    # a project manager's members list only returns themself; /users/{id} resolves the rest
    names = {uid: hs.get(f"/users/{uid}")["user"]["name"] for uid in {x["user_id"] for x in acts}}
    idx = cfg["_idx"]["hubstaff"]
    rows = []
    for x in acts:
        tracked = x.get("tracked") or 0
        rows.append({"date": x["date"], "member": names[x["user_id"]],
                     "person": idx.get(names[x["user_id"]].strip().lower()),
                     "project": x.get("project_id"), "hours": round(tracked / 3600, 4),
                     "idle_hours": round((x.get("idle") or 0) / 3600, 4),
                     "activity_pct": round(100 * (x.get("overall") or 0) / tracked) if tracked else None})
    return rows, f"Hubstaff API org {org}"


def cmd_hubstaff(a, cfg):
    f, t = period(a)
    if a.source in ("auto", "api") and not a.csv:
        try:
            rows, label = hubstaff_api_rows(f, t, cfg)
            return _write_hubstaff(f, t, rows, label, {"source": "api"})
        except Exception as e:  # noqa: BLE001 - fall back to the CSV, say why
            if a.source == "api":
                die(f"Hubstaff API failed: {e}")
            api_err = str(e)[:300]
    else:
        api_err = None
    hit = find_hubstaff(f, t, a.csv)
    if not hit["chosen"]:
        emit({"ok": False, "status": "NEEDS_EXPORT", "period": [f, t], **hit})
        return 3
    path = Path(hit["chosen"]["file"])
    idx = cfg["_idx"]["hubstaff"]
    rows = []
    with open(path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if not (str(f) <= r["Date"] <= str(t)):
                continue
            person = idx.get(r["Member"].strip().lower())
            act = r.get("Activity %", "-")
            rows.append({"date": r["Date"], "member": r["Member"], "person": person,
                         "project": r.get("Project", ""), "hours": round(hms(r["Total hours"]), 4),
                         "idle_hours": round(hms(r.get("Idle (hr)", "0:0:0")), 4),
                         "activity_pct": int(act.rstrip("%")) if act not in ("-", "") else None})
    return _write_hubstaff(f, t, rows, str(path), {"source": "csv", "coverage": hit["chosen"],
                                                    "api_error": api_err})


def _write_hubstaff(f, t, rows, label, extra):
    unmapped = C.Counter(r["member"] for r in rows if not r["person"])
    od = out_dir(f, t)
    per = C.defaultdict(lambda: {"days": 0, "hours": 0.0})
    for r in rows:
        k = r["person"] or f"(unmapped) {r['member']}"
        per[k]["days"] += 1
        per[k]["hours"] = round(per[k]["hours"] + r["hours"], 2)
    (od / "hubstaff.json").write_text(json.dumps(
        {"source": label, "period": [str(f), str(t)], "rows": rows}, indent=0), encoding="utf-8")
    emit({"ok": True, "status": "OK", "from": label, **extra,
          "rows_in_period": len(rows), "per_person": per, "unmapped_members": dict(unmapped),
          "out": str(od / "hubstaff.json")})
    return 0


CHAT_LINE = re.compile(r"^‎?\[(\d{1,2}/\d{1,2}/\d{2,4}), (\d{1,2}:\d{2}(?::\d{2})?\s?[AP]M)\] ([^:]+): (.*)$")
# Android export: "06/01/2026, 22:23 - Moe Galal: text", 24-hour, day order set by the phone's locale
CHAT_LINE_ANDROID = re.compile(r"^‎?(\d{1,2}/\d{1,2}/\d{2,4}), (\d{1,2}:\d{2}(?:\s?[AP]M)?) - ([^:]+): (.*)$")


def parse_chat(txt: str) -> list[dict]:
    lines = txt.splitlines()
    # day-first when any Android date's first field is past 12 (this group's export is dd/mm/yyyy)
    day_first = any(int(m.group(1).split("/")[0]) > 12
                    for m in map(CHAT_LINE_ANDROID.match, lines) if m)
    msgs = []
    for line in lines:
        m = CHAT_LINE.match(line)
        a = None if m else CHAT_LINE_ANDROID.match(line)
        if m or a:
            d, tm, who, body = (m or a).groups()
            yy = "%y" if len(d.split("/")[2]) == 2 else "%Y"
            fmt_d = f"%d/%m/{yy}" if a and day_first else f"%m/%d/{yy}"
            tm = tm.replace(chr(0x202f), " ")
            if "M" in tm:
                fmt_t = "%I:%M:%S %p" if tm.count(":") == 2 else "%I:%M %p"
            else:
                fmt_t = "%H:%M"
            ts = D.datetime.strptime(f"{d} {tm}", f"{fmt_d} {fmt_t}")
            msgs.append({"ts": ts.isoformat(), "sender": who.strip(), "text": body})
        elif msgs:
            msgs[-1]["text"] += "\n" + line
    return msgs


def cmd_whatsapp(a, cfg):
    f, t = period(a)
    hit = find_whatsapp(f, t, a.zip)
    if not hit["chosen"]:
        emit({"ok": False, "status": "NEEDS_EXPORT", "period": [f, t], **hit})
        return 3
    path = Path(hit["chosen"]["file"])
    idx = cfg["_idx"]["whatsapp"]
    msgs = [m for m in parse_chat(_chat_text(path)) if str(f) <= m["ts"][:10] <= str(t)]
    for i, m in enumerate(msgs):
        m["id"] = i
        m["person"] = idx.get(m["sender"].lower())
        txt = m["text"]
        m["phones"] = sorted({digits(x) for x in re.findall(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", txt)})
        m["lead_candidate"] = bool(re.search(r"(?im)^\s*(name|address|property address)\s*:", txt)) \
            and "<document omitted>" not in txt
    od = out_dir(f, t)
    (od / "messages.json").write_text(json.dumps(
        {"source": str(path), "period": [str(f), str(t)], "messages": msgs}, indent=1),
        encoding="utf-8")
    emit({"ok": True, "status": "OK", "source": str(path), "coverage": hit["chosen"],
          "messages_in_period": len(msgs),
          "lead_candidates": [{"id": m["id"], "ts": m["ts"], "sender": m["sender"], "person": m["person"],
                               "first_line": m["text"].splitlines()[0][:90]} for m in msgs if m["lead_candidate"]],
          "unmapped_senders": sorted({m["sender"] for m in msgs if not m["person"]}),
          "out": str(od / "messages.json")})
    return 0


def cmd_find(a, cfg):
    f, t = period(a)
    res = {"period": [str(f), str(t)],
           "smrtphone": find_smrtphone(f, t), "hubstaff": find_hubstaff(f, t),
           "whatsapp": find_whatsapp(f, t)}
    res["missing"] = [k for k in ("smrtphone", "hubstaff", "whatsapp") if not res[k]["chosen"]]
    emit(res)
    return 0 if not res["missing"] else 3


# ---------------------------------------------------------------- report
def cmd_report(a, cfg):
    f, t = period(a)
    od = out_dir(f, t)
    need = ["hubstaff.json", "smrtphone.json"]
    miss = [n for n in need if not (od / n).exists()]
    if miss:
        die(f"missing {miss} in {od} - run the extractors first")
    hub = json.loads((od / "hubstaff.json").read_text(encoding="utf-8"))["rows"]
    sp = json.loads((od / "smrtphone.json").read_text(encoding="utf-8"))["rows"]
    leads = json.loads((od / "leads.json").read_text(encoding="utf-8")) if (od / "leads.json").exists() else None
    gap_min = cfg.get("gap_minutes", 15)
    allow = cfg.get("daily_allowance_hours", 1.0)

    paid = C.defaultdict(lambda: C.defaultdict(float))
    idle, actw = C.defaultdict(float), C.defaultdict(float)
    for r in hub:
        if not r["person"]:
            continue
        paid[r["person"]][r["date"]] += r["hours"]
        idle[r["person"]] += r["idle_hours"]
        actw[r["person"]] += r["hours"] * (r["activity_pct"] or 0)
    calls = C.defaultdict(lambda: C.defaultdict(list))
    st = C.defaultdict(C.Counter)
    for r in sp:
        if not r["person"] or r["direction"] != "Outbound":
            continue
        tm = D.datetime.strptime(f"{r['date']} {r['time']}", "%Y-%m-%d %H:%M")
        calls[r["person"]][r["date"]].append((tm, r["duration"], r["status"]))
        st[r["person"]][r["status"]] += 1

    people, daily = {}, {}
    for person, p in cfg["people"].items():
        role, rate = p["role"], p["rate"]
        # share of paid time that is management, not dialing (Bagoury 0.3, Basem 2026-10-07):
        # it comes off the hours the dial metrics and slack are judged on, never off the invoice
        mshare = float(p.get("management_share") or 0)
        P, Cd = paid.get(person, {}), calls.get(person, {})
        hours = sum(P.values())
        dials = sum(len(v) for v in Cd.values())
        conv = sum(1 for v in Cd.values() for _, _, s in v if s in HUMAN)
        rpc = sum(1 for v in Cd.values() for _, _, s in v if s in RPC)
        long2 = sum(1 for v in Cd.values() for _, d, _ in v if d >= 120)
        talk = sum(d for v in Cd.values() for _, d, _ in v) / 3600
        active = slack = 0.0
        rows = []
        for day in sorted(set(P) | set(Cd)):
            v = sorted(Cd.get(day, []))
            act = 0.0
            if v:
                for k in range(1, len(v)):
                    g = (v[k][0] - v[k - 1][0]).total_seconds() / 60
                    if g <= gap_min:
                        act += g / 60
                act += v[-1][1] / 3600 + 1 / 60
            pd = P.get(day, 0.0)
            sl = max(pd * (1 - mshare) - act - allow, 0.0) if (pd and role != "data_manager") else 0.0
            active += act
            slack += sl
            rows.append({"date": day, "paid": round(pd, 2), "dials": len(v),
                         "first": v[0][0].strftime("%H:%M") if v else "",
                         "last": v[-1][0].strftime("%H:%M") if v else "",
                         "active": round(act, 2), "slack": round(sl, 2)})
        daily[person] = rows
        invoice = round(hours * rate, 2)
        dial_hours = hours * (1 - mshare)
        calling_cost = round(invoice * (1 - mshare), 2)
        vm_l, vm_n = st[person][VM_LEFT], st[person][VM_NONE]
        nod = sum(st[person][s] for s in NO_DISPO)
        people[person] = {
            "role": role, "rate": rate, "days": len(P), "hours": round(hours, 2), "invoice": invoice,
            "idle_hours": round(idle[person], 2),
            "activity_pct": round(actw[person] / hours, 1) if hours else None,
            "management_share": mshare, "management_hours": round(hours * mshare, 2),
            "dial_hours": round(dial_hours, 2), "calling_cost": calling_cost,
            "dials": dials, "dials_per_paid_hour": round(dials / dial_hours, 1) if dial_hours and dials else None,
            "active_dial_hours": round(active, 2),
            "dials_per_active_hour": round(dials / active, 1) if active else None,
            "slack_hours": round(slack, 1) if role != "data_manager" else None,
            "slack_usd": round(slack * rate) if role != "data_manager" else None,
            "talk_hours": round(talk, 2), "conversations": conv, "rpc": rpc, "calls_2min": long2,
            "correct": st[person]["Correct Number"],
            "vm_left_rate": round(vm_l / (vm_l + vm_n), 3) if (vm_l + vm_n) else None,
            "no_dispo_rate": round(nod / dials, 3) if dials else None,
            "paid_days_no_dials": sum(1 for d in P if not Cd.get(d)),
            "cost_per_conversation": round(calling_cost / conv, 2) if conv else None,
            "cost_per_rpc": round(calling_cost / rpc, 2) if rpc else None,
            "median_first_dial": _median_time([r["first"] for r in rows if r["first"]]),
            "statuses": dict(st[person].most_common()),
        }

    # leads: count + match each lead's numbers to SmrtPhone calls (in and out)
    lead_rows = []
    if leads is not None:
        by_num = C.defaultdict(list)
        for r in sp:
            if r["participant_number"]:
                by_num[r["participant_number"]].append(r)
        for L in leads:
            hits = [c for n in L.get("phones", []) for c in by_num.get(digits(n), [])]
            how = "phone" if hits else None
            if not hits:
                # the call often went to another household line (Blythe, Sept: the husband's)
                # - fall back to the owner's surname in the dialer contact name
                last = [w for w in re.findall(r"[A-Za-z]{4,}", L.get("owner_name") or "")
                        if w.lower() not in {"mrs", "estate", "owner", "deceased", "spoke"}]
                if last:
                    sn = last[-1].lower() if len(last) > 1 else last[0].lower()
                    hits = [c for c in sp if sn in (c["participant"] or "").lower()
                            and c["date"] <= (L.get("posted_at") or "9999")[:10]]
                    how = "surname" if hits else None
            hits.sort(key=lambda c: -c["duration"])
            top = hits[0] if hits else None
            lead_rows.append({**L, "matched_calls": len(hits), "matched_by": how,
                              "best_call": ({k: top[k] for k in ("date", "time", "agent", "direction",
                                                                 "duration", "status", "participant")}
                                            if top else None)})
            p = people.get(L.get("poster"))
            if p is not None:
                p["leads"] = p.get("leads", 0) + 1
                p["workable_leads"] = p.get("workable_leads", 0) + (1 if L.get("workable") else 0)
        for p in people.values():
            if p["role"] != "data_manager":
                p.setdefault("leads", 0)
                p.setdefault("workable_leads", 0)
                p["cost_per_lead"] = round(p["calling_cost"] / p["leads"], 2) if p["leads"] else None
                p["cost_per_workable_lead"] = (round(p["calling_cost"] / p["workable_leads"], 2)
                                               if p["workable_leads"] else None)

    groups = C.defaultdict(lambda: {"people": [], "hours": 0.0, "invoice": 0.0, "calling_cost": 0.0,
                                    "management_hours": 0.0, "conversations": 0,
                                    "rpc": 0, "dials": 0, "leads": 0, "workable_leads": 0})
    for name, p in people.items():
        g = groups["data_managers" if p["role"] == "data_manager" else "calling_team"]
        g["people"].append(name)
        for k in ("hours", "invoice", "calling_cost", "management_hours", "conversations", "rpc", "dials", "leads",
                  "workable_leads"):
            g[k] = round(g[k] + (p.get(k) or 0), 2)
    ct = groups["calling_team"]
    ct["cost_per_rpc"] = round(ct["calling_cost"] / ct["rpc"], 2) if ct["rpc"] else None
    ct["cost_per_conversation"] = round(ct["calling_cost"] / ct["conversations"], 2) if ct["conversations"] else None
    ct["cost_per_lead"] = round(ct["calling_cost"] / ct["leads"], 2) if ct["leads"] else None
    ct["cost_per_workable_lead"] = round(ct["calling_cost"] / ct["workable_leads"], 2) if ct["workable_leads"] else None

    res = {"period": [str(f), str(t)], "generated": D.datetime.now().isoformat(timespec="seconds"),
           "definitions": {"gap_minutes": gap_min, "daily_allowance_hours": allow,
                           "conversation": sorted(HUMAN), "rpc": sorted(RPC)},
           "total_invoice": round(sum(p["invoice"] for p in people.values()), 2),
           "total_hours": round(sum(p["hours"] for p in people.values()), 2),
           "groups": groups, "people": people, "leads": lead_rows if leads is not None else None,
           "daily": daily}
    (od / "kpi.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    (od / "tables.md").write_text(render(res), encoding="utf-8")
    emit({"ok": True, "out": [str(od / "kpi.json"), str(od / "tables.md")],
          "total_invoice": res["total_invoice"], "leads_loaded": leads is not None})
    return 0


def _median_time(ts: list[str]) -> str:
    if not ts:
        return ""
    s = sorted(ts)
    return s[len(s) // 2]


def _m(v, money=False, pct=False):
    if v is None:
        return "-"
    if pct:
        return f"{v * 100:.0f}%"
    return f"${v:,.2f}" if money else f"{v:,}" if isinstance(v, int) else f"{v:,.1f}"


def render(r: dict) -> str:
    P = r["people"]
    out = [f"# Team KPI {r['period'][0]} to {r['period'][1]}", "",
           f"Total invoice **${r['total_invoice']:,.2f}** on {r['total_hours']:,.1f} h.", "",
           "## Invoices", "", "| Person | Role | Rate | Days | Hours | Invoice | Idle h |",
           "|---|---|---|---|---|---|---|"]
    for n, p in P.items():
        out.append(f"| {n} | {p['role']} | ${p['rate']} | {p['days']} | {p['hours']:.2f} | "
                   f"{_m(p['invoice'], money=True)} | {p['idle_hours']:.2f} |")
    out += ["", "## Paid hours against dialing (callers)", "",
            "| Caller | Paid h | Active dial h | Slack h | Slack $ | Paid days, 0 dials | Median first dial |",
            "|---|---|---|---|---|---|---|"]
    for n, p in P.items():
        if p["role"] == "data_manager":
            continue
        out.append(f"| {n} | {p['hours']:.1f} | {p['active_dial_hours']:.1f} | {_m(p['slack_hours'])} | "
                   f"${p['slack_usd']} | {p['paid_days_no_dials']} | {p['median_first_dial']} |")
    out += ["", "## Dials (outbound)", "",
            "| Caller | Dials | /paid h | /active h | Conversations | RPC | Correct # | $/RPC | VM left | No dispo |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for n, p in P.items():
        if p["role"] == "data_manager":
            continue
        out.append(f"| {n} | {p['dials']:,} | {_m(p['dials_per_paid_hour'])} | {_m(p['dials_per_active_hour'])} | "
                   f"{p['conversations']} | {p['rpc']} | {p['correct']} | {_m(p['cost_per_rpc'], money=True)} | "
                   f"{_m(p['vm_left_rate'], pct=True)} | {_m(p['no_dispo_rate'], pct=True)} |")
    if r["leads"] is not None:
        out += ["", "## Leads (WhatsApp)", "",
                "| Date | Poster | Lead | Workable | Best matching call |", "|---|---|---|---|---|"]
        for L in r["leads"]:
            b = L["best_call"]
            call = (f"{b['date']} {b['agent']} {b['direction']} {b['duration']}s {b['status'] or '(no dispo)'}"
                    if b else "no call on these numbers")
            out.append(f"| {L.get('posted_at', '')[:10]} | {L.get('poster')} | {L.get('owner_name', '')}, "
                       f"{L.get('address', '')} | {'yes' if L.get('workable') else 'no'} | {call} |")
        out += ["", "| Caller | Invoice | Leads | Workable | $/lead | $/workable |", "|---|---|---|---|---|---|"]
        for n, p in P.items():
            if p["role"] == "data_manager":
                continue
            out.append(f"| {n} | {_m(p['invoice'], money=True)} | {p['leads']} | {p['workable_leads']} | "
                       f"{_m(p['cost_per_lead'], money=True)} | {_m(p['cost_per_workable_lead'], money=True)} |")
    g = r["groups"]
    out += ["", "## Split", "", "| Group | People | Hours | Invoice |", "|---|---|---|---|"]
    for k, v in g.items():
        out.append(f"| {k} | {', '.join(v['people'])} | {v['hours']:,.1f} | ${v['invoice']:,.2f} |")
    ct = g.get("calling_team", {})
    out += ["", f"Calling team: {_m(ct.get('cost_per_rpc'), money=True)} per right-party contact, "
                f"{_m(ct.get('cost_per_conversation'), money=True)} per conversation, "
                f"{_m(ct.get('cost_per_lead'), money=True)} per lead."]
    return "\n".join(out) + "\n"


FORCE_SCREENS = False      # set by --screens: run the screenshot check on a monthly report too
LEADS_STORE = OUT_BASE / "leads" / "leads.json"


def _period_leads(f: D.date, t: D.date, od: Path) -> None:
    """Copy the WhatsApp leads posted in [f, t] from the running store into the period folder
    (leads.json), so the report prices them. The store is filled from each chat export
    (team-kpi-whatsapp / the monthly review); an empty period writes an empty list."""
    store = json.loads(LEADS_STORE.read_text(encoding="utf-8")) if LEADS_STORE.exists() else []
    rows = [L for L in store if str(f) <= (L.get("posted_at") or "")[:10] <= str(t)]
    (od / "leads.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


CACHE = OUT_BASE / "cache" / "smrtphone"
WINDOW_DAYS = 28


def smrtphone_window(f: D.date, t: D.date, cfg, today: D.date) -> list[dict]:
    """SmrtPhone rows for [f, t] with a per-day cache: a finished day (before today) is
    fetched once and kept; only missing days (and today) are walked from the API."""
    CACHE.mkdir(parents=True, exist_ok=True)
    days = [f + D.timedelta(days=i) for i in range((t - f).days + 1)]
    missing = [d for d in days if d >= today or not (CACHE / f"{d}.json").exists()]
    fresh = {}
    if missing:
        rows, _ = smrtphone_api_rows(min(missing), t, cfg)
        for r in rows:
            fresh.setdefault(r["date"], []).append(r)
        for d in missing:
            if d < today:
                (CACHE / f"{d}.json").write_text(json.dumps(fresh.get(str(d), [])), encoding="utf-8")
    out = []
    for d in days:
        if str(d) in fresh or d in missing:
            out += fresh.get(str(d), [])
        else:
            out += json.loads((CACHE / f"{d}.json").read_text(encoding="utf-8"))
    return out


def _run_span(kind: str, f: D.date, t: D.date, cfg) -> int:
    """daily / weekly: both APIs -> kpi.json, a text summary and the HTML dashboard.
    Never raises: a source that fails is named in the report with its fix."""
    import contextlib
    from zoneinfo import ZoneInfo
    import team_kpi_dash as dash
    today = D.datetime.now(ZoneInfo("America/New_York")).date()
    ns = argparse.Namespace(month=None, day_from=str(f), day_to=str(t), csv=None, zip=None, source="api")
    errors, log = [], io.StringIO()
    od = out_dir(f, t)
    _period_leads(f, t, od)
    try:
        with contextlib.redirect_stdout(log):
            cmd_hubstaff(ns, cfg)
    except BaseException as e:  # noqa: BLE001 - SystemExit from die() included
        msg = log.getvalue().strip().splitlines()
        errors.append(f"Hubstaff failed: {(str(e) if not isinstance(e, SystemExit) else (msg[-2] if len(msg) > 1 else 'see log'))[:160]}"
                      " -> token dead? make a new one at developer.hubstaff.com, replace the .env line, delete hubstaff_state.json")
    wrows, prows = [], []
    try:
        wf = min(f, t - D.timedelta(days=WINDOW_DAYS - 1))
        wrows = smrtphone_window(wf, t, cfg, today)
        prows = [r for r in wrows if str(f) <= r["date"] <= str(t)]
        with contextlib.redirect_stdout(log):
            _write_smrtphone(f, t, prows, "SmrtPhone API (cached per day)", {"source": "api"})
    except Exception as e:  # noqa: BLE001
        errors.append(f"SmrtPhone failed: {str(e)[:160]} -> run: python skills/cold-call-coach/scripts/smrtphone_login.py")
    k = None
    if not errors:
        with contextlib.redirect_stdout(log):
            cmd_report(ns, cfg)
        k = json.loads((od / "kpi.json").read_text(encoding="utf-8"))
    if kind == "daily":
        text = render_daily(t, k, errors, cfg)
        title, sub, stem = f"Team KPI - {t:%A %b %d}", f"As of {D.datetime.now():%H:%M} ET", str(t)
    elif kind == "monthly":
        head = f"Team KPI month  {f:%B %Y}"
        text = "\n".join([head, ""] + errors) + "\n" if errors else render_weekly(head, k, prows, cfg)
        title, sub, stem = f"Team KPI - {f:%B %Y}", f"{f:%b %d} to {t:%b %d, %Y}", f"{f:%Y-%m}"
    else:
        head = f"Team KPI week  {f:%a %b %d} - {t:%a %b %d}"
        text = "\n".join([head, ""] + errors) + "\n" if errors else render_weekly(head, k, prows, cfg)
        title, sub, stem = f"Team KPI - week of {f:%b %d}", f"{f:%a %b %d} to {t:%a %b %d}", f"{f}_{t}"
    dmf = OUT_BASE / "data_manager" / f"{f}_{t}" / "kpi.json"
    dm = json.loads(dmf.read_text(encoding="utf-8")) if dmf.exists() else None
    lanes_html = ""
    if k:
        try:
            import team_kpi_lanes as tl
            callers = {n for n, p in k["people"].items() if p["role"] != "data_manager"}
            ls = tl.lane_stats(prows, callers, HUMAN, RPC)
            (od / "lanes.json").write_text(json.dumps(ls, indent=1), encoding="utf-8")
            lanes_html = dash.lanes_card(ls, cfg)
        except Exception as e:  # noqa: BLE001 - lanes are an extra; never block the report
            lanes_html = f'<div class="err"><strong>Lanes unavailable:</strong> {dash.esc(str(e)[:200])}</div>'
    prev = None
    if kind == "weekly" and k:
        prev = _prev_kpi(f - D.timedelta(days=7), t - D.timedelta(days=7), cfg, today, log)
    screens = None
    if k and (kind != "monthly" or FORCE_SCREENS):   # a month's first check costs ~$9: --screens on Basem's go
        # off-dialer time judged from the Hubstaff screenshots (labels cached per screenshot)
        try:
            import team_kpi_screens as tsc
            screens = tsc.off_dialer(f, t, prows, cfg)
            (od / "screens.json").write_text(json.dumps(screens, indent=1), encoding="utf-8")
        except Exception as e:  # noqa: BLE001 - an extra; never block the report
            lanes_html += f'<div class="err"><strong>Screenshot check unavailable:</strong> {dash.esc(str(e)[:200])}</div>'
    page = dash.render(kind, title, sub, k, prows, wrows, cfg, errors, HUMAN, RPC, dm, prev, lanes_html, screens)
    dd = OUT_BASE / kind
    dd.mkdir(parents=True, exist_ok=True)
    (dd / f"{stem}.txt").write_text(text, encoding="utf-8")
    (dd / f"{stem}.html").write_text(page, encoding="utf-8")
    (dd / f"{stem}.log").write_text(log.getvalue(), encoding="utf-8")
    build_dialer_pages(cfg)
    write_index()
    print(text)
    print(f"HTML: {dd / f'{stem}.html'}")
    return 0


def build_dialer_pages(cfg) -> list[Path]:
    """output/team_kpi/dialers/<name>.html for every caller, from the daily folders on disk."""
    import team_kpi_dash as dash
    days = C.defaultdict(list)
    for kf in OUT_BASE.glob("*_*/kpi.json"):
        a_, _, b_ = kf.parent.name.partition("_")
        if a_ != b_:
            continue
        k = json.loads(kf.read_text(encoding="utf-8"))
        scr = json.loads((kf.parent / "screens.json").read_text(encoding="utf-8")) if (kf.parent / "screens.json").exists() else {}
        ln = json.loads((kf.parent / "lanes.json").read_text(encoding="utf-8")) if (kf.parent / "lanes.json").exists() else {}
        d = D.date.fromisoformat(a_)
        for n, p in k["people"].items():
            if p["role"] == "data_manager":
                continue
            pc = (ln.get("per_caller") or {}).get(n, {})
            lanes = " / ".join(f"{pc[l]['records']}" for l in ("FTM", "Hottest", "Obituary") if l in pc) if pc else ""
            days[n].append({"date": a_, "dow": f"{d:%a}", "hours": p["hours"], "active_dial_hours": p["active_dial_hours"],
                            "slack_hours": p["slack_hours"], "dial_hours": p.get("dial_hours", p["hours"]),
                            "dials": p["dials"], "conversations": p["conversations"], "rpc": p["rpc"],
                            "leads": p.get("leads", 0), "lanes": lanes,
                            "first": next((r["first"] for r in k["daily"].get(n, []) if r["first"]), ""),
                            "screens": scr.get(n)})
    store = json.loads(LEADS_STORE.read_text(encoding="utf-8")) if LEADS_STORE.exists() else []
    out_dir_ = OUT_BASE / "dialers"
    out_dir_.mkdir(parents=True, exist_ok=True)
    written = []
    for n, p in cfg["people"].items():
        if p["role"] == "data_manager":
            continue
        page = dash.dialer_page(n, days.get(n, []), [L for L in store if L.get("poster") == n], cfg)
        f = out_dir_ / f"{dash.slug(n)}.html"
        f.write_text(page, encoding="utf-8")
        written.append(f)
    return written


def write_index() -> Path:
    """output/team_kpi/index.html: every daily and weekly dashboard, newest first."""
    import team_kpi_dash as dash

    def links(kind):
        files = sorted((OUT_BASE / kind).glob("*.html"), reverse=True)
        out = []
        for p in files:
            label = p.stem
            try:
                if kind == "daily":
                    label = f"{D.date.fromisoformat(p.stem):%a %b %d, %Y}"
                elif kind == "monthly":
                    label = f"{D.date.fromisoformat(p.stem + '-01'):%B %Y}"
                else:
                    a_, b_ = p.stem.split("_")
                    label = f"{D.date.fromisoformat(a_):%b %d} - {D.date.fromisoformat(b_):%b %d, %Y}"
            except ValueError:
                pass
            out.append(f'<li><a href="{kind}/{p.name}">{dash.esc(label)}</a></li>')
        return "".join(out) or "<li>none yet</li>"

    page = ("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Team KPI reports</title><style>{dash.CSS} ul{{margin:6px 0 0;padding-left:18px;columns:2}} li{{margin:4px 0}}"
            " a{color:var(--accent)}</style></head><body><main><h1>Team KPI reports</h1>"
            f"<p class=sub>Updated {D.datetime.now():%Y-%m-%d %H:%M}. Daily at 8 PM, weekly on Sunday.</p>"
            f'<section class="card"><h2>Monthly</h2><ul>{links("monthly")}</ul></section>'
            '<section class="card"><h2>Dialers</h2><ul>' + "".join(
                f'<li><a href="dialers/{dash.slug(n)}.html">{dash.esc(n)}</a></li>'
                for n, p in load_config()["people"].items() if p["role"] != "data_manager") + "</ul></section>"
            f'<div class="grid2"><section class="card"><h2>Weekly</h2><ul>{links("weekly")}</ul></section>'
            f'<section class="card"><h2>Daily</h2><ul>{links("daily")}</ul></section></div></main></body></html>')
    out = OUT_BASE / "index.html"
    out.write_text(page, encoding="utf-8")
    return out


def _prev_kpi(f: D.date, t: D.date, cfg, today: D.date, log) -> dict | None:
    """The comparison period's kpi.json (built quietly from the cache + Hubstaff if missing)."""
    import contextlib
    kf = out_dir(f, t) / "kpi.json"
    _period_leads(f, t, out_dir(f, t))
    if kf.exists():
        return json.loads(kf.read_text(encoding="utf-8"))
    ns = argparse.Namespace(month=None, day_from=str(f), day_to=str(t), csv=None, zip=None, source="api")
    try:
        with contextlib.redirect_stdout(log):
            cmd_hubstaff(ns, cfg)
            _write_smrtphone(f, t, smrtphone_window(f, t, cfg, today), "SmrtPhone API (cached per day)", {"source": "api"})
            cmd_report(ns, cfg)
        return json.loads(kf.read_text(encoding="utf-8"))
    except BaseException:  # noqa: BLE001 - a missing comparison never blocks the report
        return None


def cmd_daily(a, cfg):
    from zoneinfo import ZoneInfo
    day = D.date.fromisoformat(a.date) if a.date else D.datetime.now(ZoneInfo("America/New_York")).date()
    return _run_span("daily", day, day, cfg)


def render_daily(day: D.date, k: dict | None, errors: list[str], cfg: dict) -> str:
    head = f"Team KPI  {day:%a %b %d}  (as of {D.datetime.now():%H:%M})"
    if k is None:
        return "\n".join([head, ""] + errors) + "\n"
    P = k["people"]
    short = {n: p.get("short") or n.split()[0] for n, p in cfg["people"].items()}
    callers = {n: p for n, p in P.items() if p["role"] != "data_manager"}
    dials = sum(p["dials"] for p in callers.values())
    conv = sum(p["conversations"] for p in callers.values())
    rpc = sum(p["rpc"] for p in callers.values())
    out = [head,
           f"${k['total_invoice']:,.0f} on {k['total_hours']:.1f} h | {dials:,} dials, {conv} conv, {rpc} RPC", "",
           f"{'':9}{'Hrs':>5}{'Dials':>6}{'/h':>4}{'Conv':>5}{'RPC':>4}{'VM':>5}{'1st':>6}{'Slack':>6}"]
    flags = []
    for n, p in callers.items():
        if not p["hours"] and not p["dials"]:
            continue
        vm = f"{p['vm_left_rate'] * 100:.0f}%" if p["vm_left_rate"] is not None else "-"
        first = next((r["first"] for r in k["daily"][n] if r["first"]), "-")
        out.append(f"{short[n]:<9}{p['hours']:>5.1f}{p['dials']:>6}{(p['dials_per_paid_hour'] or 0):>4.0f}"
                   f"{p['conversations']:>5}{p['rpc']:>4}{vm:>5}{first:>6}{(p['slack_hours'] or 0):>6.1f}")
        if p["hours"] >= 1 and not p["dials"]:
            flags.append(f"{short[n]} {p['hours']:.1f} h paid, 0 dials")
        elif (p["slack_hours"] or 0) >= 2:
            flags.append(f"{short[n]} {p['slack_hours']:.1f} h off-dialer of {p['hours']:.1f} h worked")
        if p["dials"] and not p["hours"]:
            flags.append(f"{short[n]} dialing, no Hubstaff time")
        if p["vm_left_rate"] is not None and p["dials"] >= 50 and p["vm_left_rate"] < 0.25:
            flags.append(f"{short[n]} VM only {p['vm_left_rate'] * 100:.0f}%")
        if (p["no_dispo_rate"] or 0) > 0.08 and p["dials"] >= 50:
            flags.append(f"{short[n]} no-dispo {p['no_dispo_rate'] * 100:.0f}%")
    off = [short[n] for n, p in callers.items() if not p["hours"] and not p["dials"]]
    dm = [f"{short[n]} {p['hours']:.1f} h" for n, p in P.items() if p["role"] == "data_manager" and p["hours"]]
    if off:
        out.append(f"Off: {', '.join(off)}")
    if dm:
        out.append(f"Data mgrs: {', '.join(dm)}")
    out += ["", "Flags: " + ("; ".join(flags) if flags else "none")]
    return "\n".join(out) + "\n"


def cmd_weekly(a, cfg):
    """A date range (default: the 7 days ending today, ET)."""
    from zoneinfo import ZoneInfo
    if a.day_from and a.day_to:
        f, t = D.date.fromisoformat(a.day_from), D.date.fromisoformat(a.day_to)
    else:
        t = D.date.fromisoformat(a.date) if a.date else D.datetime.now(ZoneInfo("America/New_York")).date()
        f = t - D.timedelta(days=6)
    return _run_span("weekly", f, t, cfg)


def cmd_monthly(a, cfg):
    """A calendar month (--month YYYY-MM; default last month): the weekly page over the month."""
    f, t = period(a)
    return _run_span("monthly", f, t, cfg)


def render_weekly(head: str, k: dict, sp: list[dict], cfg: dict) -> str:
    P = k["people"]
    short = {n: p.get("short") or n.split()[0] for n, p in cfg["people"].items()}
    callers = {n: p for n, p in P.items() if p["role"] != "data_manager"}
    ct = k["groups"].get("calling_team", {})
    dials = sum(p["dials"] for p in callers.values())
    out = [head,
           f"${k['total_invoice']:,.0f} on {k['total_hours']:.1f} h (callers ${ct.get('invoice', 0):,.0f}) | "
           f"{dials:,} dials, {ct.get('conversations', 0)} conv, {ct.get('rpc', 0)} RPC, "
           f"{_m(ct.get('cost_per_rpc'), money=True)}/RPC", "",
           f"{'':9}{'Days':>5}{'Hrs':>6}{'Dials':>6}{'/h':>4}{'Conv':>5}{'RPC':>4}{'$/RPC':>7}{'VM':>5}{'NoD':>5}{'1st':>6}{'Slack':>6}"]
    flags = []
    for n, p in callers.items():
        if not p["hours"] and not p["dials"]:
            out.append(f"{short[n]:<9}  off all week")
            continue
        vm = f"{p['vm_left_rate'] * 100:.0f}%" if p["vm_left_rate"] is not None else "-"
        nd = f"{p['no_dispo_rate'] * 100:.0f}%" if p["no_dispo_rate"] is not None else "-"
        cpr = f"${p['cost_per_rpc']:.0f}" if p["cost_per_rpc"] else "-"
        out.append(f"{short[n]:<9}{p['days']:>5}{p['hours']:>6.1f}{p['dials']:>6}{(p['dials_per_paid_hour'] or 0):>4.0f}"
                   f"{p['conversations']:>5}{p['rpc']:>4}{cpr:>7}{vm:>5}{nd:>5}{p['median_first_dial'] or '-':>6}"
                   f"{(p['slack_hours'] or 0):>6.1f}")
        if p["paid_days_no_dials"]:
            flags.append(f"{short[n]} {p['paid_days_no_dials']} paid day(s) with 0 dials")
        if (p["slack_hours"] or 0) >= 8:
            flags.append(f"{short[n]} {p['slack_hours']:.0f} h off-dialer of {p['hours']:.0f} h worked")
        if p["vm_left_rate"] is not None and p["dials"] >= 100 and p["vm_left_rate"] < 0.25:
            flags.append(f"{short[n]} VM {p['vm_left_rate'] * 100:.0f}%")
        if (p["no_dispo_rate"] or 0) > 0.08 and p["dials"] >= 100:
            flags.append(f"{short[n]} no-dispo {p['no_dispo_rate'] * 100:.0f}%")
    dm = [f"{short[n]} {p['hours']:.1f} h" for n, p in P.items() if p["role"] == "data_manager" and p["hours"]]
    if dm:
        out.append(f"Data mgrs: {', '.join(dm)}")
    # team by day: paid hours, dials, conversations, RPC
    by = C.defaultdict(lambda: [0.0, 0, 0, 0])
    for n in callers:
        for r in k["daily"].get(n, []):
            by[r["date"]][0] += r["paid"]
    for r in sp:
        if r["person"] in callers and r["direction"] == "Outbound":
            b = by[r["date"]]
            b[1] += 1
            b[2] += r["status"] in HUMAN
            b[3] += r["status"] in RPC
    out += ["", f"{'By day':<11}{'Hrs':>6}{'Dials':>6}{'Conv':>5}{'RPC':>4}"]
    for d in sorted(by):
        h, dl, cv, rp = by[d]
        out.append(f"{D.date.fromisoformat(d):%a %m-%d}  {h:>6.1f}{dl:>6}{cv:>5}{rp:>4}")
    out += ["", "Flags: " + ("; ".join(flags) if flags else "none")]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["find", "smrtphone", "hubstaff", "whatsapp", "report", "daily", "weekly", "monthly"])
    ap.add_argument("--date", help="daily / weekly: YYYY-MM-DD (default today, America/New_York)")
    ap.add_argument("--month", help="YYYY-MM")
    ap.add_argument("--from", dest="day_from")
    ap.add_argument("--to", dest="day_to")
    ap.add_argument("--csv", help="explicit export path (smrtphone / hubstaff)")
    ap.add_argument("--zip", help="explicit WhatsApp export zip")
    ap.add_argument("--source", choices=["auto", "api", "csv"], default="auto",
                    help="hubstaff / smrtphone: API first, CSV fallback (auto); or force one")
    ap.add_argument("--screens", action="store_true",
                    help="monthly: also run the Hubstaff screenshot check (paid, ~$9 for a month's first run)")
    a = ap.parse_args()
    global FORCE_SCREENS
    FORCE_SCREENS = a.screens
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config()
    return {"find": cmd_find, "smrtphone": cmd_smrtphone, "hubstaff": cmd_hubstaff,
            "whatsapp": cmd_whatsapp, "report": cmd_report, "daily": cmd_daily,
            "weekly": cmd_weekly, "monthly": cmd_monthly}[a.cmd](a, cfg)


if __name__ == "__main__":
    sys.exit(main())
