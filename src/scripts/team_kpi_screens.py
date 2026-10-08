"""team_kpi_screens.py - what callers' off-dialer time actually was, from Hubstaff screenshots.

For every Hubstaff 10-minute slot in which a caller was paid but not dialing, decide:
  work      the screen shows work tools (DataSift, SmrtPhone, Podio, the team WhatsApp, Hubstaff,
            the Dialer Day Card, Google Sheets of leads, MLS / property research)
  not_work  something else (video, social media, shopping, games, unrelated browsing)
  away      0-2% keyboard/mouse AND the screen did not change from the previous shot, or no shot
  unclear   anything the model could not decide; never counted against anyone

"away" is computed locally (pixel difference, free). work / not_work / unclear comes from one
Claude call per screenshot (Basem's go 2026-10-07: about $1-2 a week). Each label is cached
forever per screenshot id in output/team_kpi/cache/screens.json, so a slot is only paid for once.
Screenshots of flagged slots are kept under output/team_kpi/screens/<person>/<date>/ as the
evidence; they appear only in Basem's local reports, never on a shared link.

Read-only everywhere: Hubstaff API (activities + screenshots), the SmrtPhone rows already pulled.
"""
from __future__ import annotations

import base64
import collections as C
import datetime as D
import io
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "src")]
ET = ZoneInfo("America/New_York")
CACHE = ROOT / "output" / "team_kpi" / "cache" / "screens.json"
SHOTS = ROOT / "output" / "team_kpi" / "screens"
MODEL = "claude-opus-5-5"
GAP_MIN = 15
STILL_ACTIVITY = 2          # % keyboard/mouse at or below which a slot can be "away"
_CLIENT = None
REUSE_DIFF = 6.0            # screen this close to the last LABELED shot keeps that label (no new call)
STILL_DIFF = 2.0            # mean absolute pixel difference (0-255) below which the screen "did not change"

PROMPT = """This is a screenshot from a remote employee's work computer, taken by time-tracking software.
The employee is a cold caller for a real-estate investing company. Their work tools are:
DataSift / REISift (app.reisift.io, property records), smrtPhone (phone.smrt.studio, the dialer and
call logs), Podio lead forms, WhatsApp (team chat), Hubstaff, a "Dialer Day Card" checklist page,
Google Sheets or Docs about leads or properties, Zillow / Redfin / county property or MLS research,
Gmail for work, and Claude or ChatGPT used for work.

Classify what the screen shows:
- "work": the visible, focused content is one of those work tools or clearly work-related.
- "not_work": the focused content is unrelated to the job (videos, social media feeds, shopping,
  games, news, personal browsing, entertainment).
- "unclear": a lock screen, a blank desktop, an unreadable screen, or you cannot tell.

Judge the focused window, not background tabs. Name the app or site you see in "app"
and give the reason in under 15 words in "why"."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["label", "app", "why"],
          "properties": {"label": {"type": "string", "enum": ["work", "not_work", "unclear"]},
                         "app": {"type": "string"}, "why": {"type": "string"}}}


def _load() -> dict:
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def _save(c: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c), encoding="utf-8")
    tmp.replace(CACHE)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0"}), timeout=60) as r:
        return r.read()


def _small(jpg: bytes, width: int = 800) -> tuple[bytes, "object"]:
    from PIL import Image
    im = Image.open(io.BytesIO(jpg)).convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue(), im


def _diff(a, b) -> float:
    from PIL import ImageChops, ImageStat
    if a is None or b is None or a.size != b.size:
        return 999.0
    return sum(ImageStat.Stat(ImageChops.difference(a.convert("L"), b.convert("L"))).mean)


def classify(jpg_small: bytes) -> dict:
    """One Claude call. Raises on API errors (a failed call is never a label)."""
    import anthropic
    global _CLIENT
    if _CLIENT is None:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")          # ANTHROPIC_API_KEY lives in the repo .env
        _CLIENT = anthropic.Anthropic()
    client = _CLIENT
    r = client.beta.messages.create(
        model=MODEL, max_tokens=2000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.standard_b64encode(jpg_small).decode()}},
            {"type": "text", "text": PROMPT}]}])
    if r.stop_reason == "refusal":
        return {"label": "unclear", "app": "", "why": "model declined"}
    txt = next((b.text for b in r.content if b.type == "text"), "")
    out = json.loads(txt)
    out["tokens_in"], out["tokens_out"] = r.usage.input_tokens, r.usage.output_tokens
    return out


def hubstaff_people(f: D.date, t: D.date, cfg: dict) -> dict[int, str]:
    """Hubstaff user id -> person (config name), for everyone with time in the period."""
    import hubstaff_client as hs
    org = cfg.get("hubstaff_org_id") or hs.get("/organizations")["organizations"][0]["id"]
    acts = hs.get_all(f"/organizations/{org}/activities/daily", "daily_activities",
                      {"date[start]": str(f), "date[stop]": str(t)})
    idx = cfg["_idx"]["hubstaff"]
    out = {}
    for uid in {a["user_id"] for a in acts}:
        name = hs.get(f"/users/{uid}")["user"]["name"]
        if idx.get(name.strip().lower()):
            out[uid] = idx[name.strip().lower()]
    return out


def dial_intervals(rows: list[dict], person: str) -> list[tuple[D.datetime, D.datetime]]:
    """Dialing stretches (ET) for one person: consecutive outbound calls <= GAP_MIN apart."""
    times = sorted(D.datetime.strptime(f"{r['date']} {r['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
                   + D.timedelta(seconds=0) for r in rows
                   if r.get("person") == person and r.get("direction") == "Outbound")
    durs = {}
    for r in rows:
        if r.get("person") == person and r.get("direction") == "Outbound":
            k = D.datetime.strptime(f"{r['date']} {r['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
            durs[k] = max(durs.get(k, 0), r["duration"])
    out = []
    for tm in times:
        end = tm + D.timedelta(minutes=1, seconds=durs.get(tm, 0))
        if out and (tm - out[-1][1]).total_seconds() <= GAP_MIN * 60:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((tm, end))
    return out


def off_dialer(f: D.date, t: D.date, sp_rows: list[dict], cfg: dict, max_calls: int = 2500) -> dict:
    """Per caller: off-dialer minutes by bucket + per-day split + evidence for not_work / away."""
    import hubstaff_client as hs
    org = cfg.get("hubstaff_org_id")
    callers = {n for n, p in cfg["people"].items() if p["role"] != "data_manager"}
    people = {u: p for u, p in hubstaff_people(f, t, cfg).items() if p in callers}
    if not people:
        return {}
    win = {"time_slot[start]": f"{f}T04:00:00Z", "time_slot[stop]": f"{t + D.timedelta(days=1)}T04:00:00Z"}
    cache = _load()
    result, calls = {}, 0
    todo = []          # (key, shot, person, slot info) needing a Claude label
    plan = []          # every off-dialer slot with what decides it
    for uid, person in people.items():
        # Hubstaff rejects activity / screenshot ranges over 7 days (HTTP 400, 2026-10-07): ask week by week
        acts, shots = [], []
        c0 = f
        while c0 <= t:
            c1 = min(t, c0 + D.timedelta(days=6))
            w = {"time_slot[start]": f"{c0}T04:00:00Z", "time_slot[stop]": f"{c1 + D.timedelta(days=1)}T04:00:00Z",
                 "user_ids[]": uid}
            acts += hs.get_all(f"/organizations/{org}/activities", "activities", w)
            shots += hs.get_all(f"/organizations/{org}/screenshots", "screenshots", w)
            c0 = c1 + D.timedelta(days=1)
        by_slot = C.defaultdict(list)
        for s in shots:
            by_slot[s["time_slot"]].append(s)
        ivs = dial_intervals(sp_rows, person)
        prev_img = None
        for a in sorted(acts, key=lambda a: a["time_slot"]):
            st = D.datetime.fromisoformat(a["time_slot"].replace("Z", "+00:00")).astimezone(ET)
            if not (f <= st.date() <= t) or not a.get("tracked"):
                continue
            en = st + D.timedelta(minutes=10)
            on = sum(max(0, (min(en, b) - max(st, s_)).total_seconds()) for s_, b in ivs)
            minutes = a["tracked"] / 60
            off_min = max(0.0, minutes - on / 60)
            slot_shots = sorted(by_slot.get(a["time_slot"], []), key=lambda s: s.get("recorded_at") or "")
            if off_min < 2:          # mostly dialing: not an off-dialer slot
                prev_img = None
                continue
            act = round(100 * (a.get("overall") or 0) / a["tracked"])
            plan.append({"person": person, "uid": uid, "start": st, "off_min": off_min, "activity": act,
                         "shots": slot_shots})
    # decide each slot: cache -> still check (needs images) -> Claude
    for slot in plan:
        slot["labels"] = []
    need_img = [(slot, s) for slot in plan for s in slot["shots"]]

    def get_img(pair):
        slot, s = pair
        key = str(s["id"])
        try:
            small, im = _small(_fetch(s["full_url"]))
            return pair, small, im, None
        except Exception as e:  # noqa: BLE001
            return pair, None, None, str(e)[:80]

    imgs = {}
    with ThreadPoolExecutor(6) as ex:
        for (slot, s), small, im, err in ex.map(get_img, need_img):
            imgs[str(s["id"])] = (small, im)
    # per person, in time order. A slot's capture can be SEVERAL monitors taken at the same instant
    # (Mostafa: a 2560 main screen + a 1080 portrait screen, 2026-10-07); every monitor is judged.
    # Still = low activity AND every monitor unchanged. Otherwise each monitor is labelled; within one
    # monitor, near-identical consecutive shots share the label of the first ("leader") of the stretch.
    followers = []       # (key, leader_key)

    def capture(slot):
        """The slot's latest capture: {screen index: shot}."""
        if not slot["shots"]:
            return {}
        last = max(s_.get("recorded_at") or "" for s_ in slot["shots"])
        return {s_.get("screen", 0): s_ for s_ in slot["shots"] if (s_.get("recorded_at") or "") == last}

    for person in {s_["person"] for s_ in plan}:
        prev, leader = {}, {}      # per monitor: last image / (image, key) of the stretch leader
        for slot in sorted((s_ for s_ in plan if s_["person"] == person), key=lambda s_: s_["start"]):
            cap = capture(slot)
            if not cap:
                slot["bucket"], slot["why"], slot["shot"] = ("away" if slot["activity"] <= STILL_ACTIVITY else "unclear",
                                                             "no screenshot in this slot", None)
                prev = {}
                continue
            ims = {sc_: imgs.get(str(sh["id"]), (None, None)) for sc_, sh in cap.items()}
            still = slot["activity"] <= STILL_ACTIVITY and all(
                _diff(prev.get(sc_), im) <= STILL_DIFF for sc_, (_, im) in ims.items())
            prev = {sc_: im for sc_, (_, im) in ims.items()}
            slot["cap"] = cap
            slot["shot"] = cap[min(cap)]
            if still:
                slot["bucket"], slot["why"] = "away", f"{slot['activity']}% activity, screen unchanged"
                continue
            for sc_, sh in cap.items():
                small, im = ims[sc_]
                key = str(sh["id"])
                if key in cache:
                    leader[sc_] = (im, key)
                    continue
                if sc_ in leader and _diff(leader[sc_][0], im) <= REUSE_DIFF:
                    followers.append((key, leader[sc_][1]))
                    continue
                leader[sc_] = (im, key)
                if small is not None:
                    todo.append((slot, key, small))

    def run(item):
        slot, key, small = item
        try:
            return item, classify(small), None
        except Exception as e:  # noqa: BLE001 - surfaced, never a silent label
            return item, None, f"{type(e).__name__}: {str(e)[:120]}"

    errors = []
    skipped = todo[max_calls:]
    with ThreadPoolExecutor(4) as ex:
        for i, ((slot, key, small), lab, err) in enumerate(ex.map(run, todo[:max_calls])):
            if lab is None:
                errors.append(err)
                continue
            cache[key] = {k: lab[k] for k in ("label", "app", "why", "tokens_in", "tokens_out") if k in lab}
            calls += 1
            if i % 50 == 49:
                _save(cache)
    for key, leader_key in followers:
        if leader_key in cache and key not in cache:
            cache[key] = {k: v for k, v in cache[leader_key].items() if k not in ("tokens_in", "tokens_out")}
            cache[key]["reused"] = True
    _save(cache)
    # a slot is work if ANY monitor shows work; not work if one shows something unrelated and none
    # shows work; otherwise unclear
    for slot in plan:
        if "bucket" in slot:
            continue
        labs = [(sh, cache.get(str(sh["id"]))) for sh in slot.get("cap", {}).values()]
        labs = [(sh, l) for sh, l in labs if l]
        pick = (next(((sh, l) for sh, l in labs if l["label"] == "work"), None)
                or next(((sh, l) for sh, l in labs if l["label"] == "not_work"), None)
                or (labs[0] if labs else None))
        if pick:
            sh, lab = pick
            slot["shot"] = sh
            more = f" ({len(slot['cap'])} screens)" if len(slot.get("cap", {})) > 1 else ""
            slot["bucket"], slot["why"] = lab["label"], (f"{lab.get('app', '')}: {lab.get('why', '')}".strip(": ") + more)
        else:
            slot["bucket"], slot["why"] = "unclear", ("check failed" if errors else "not checked (run cap)")
    if len(errors) > 10 and len(errors) == len(todo):
        raise RuntimeError(f"every screenshot check failed: {errors[0]}")

    for slot in plan:
        p = result.setdefault(slot["person"], {"work": 0.0, "not_work": 0.0, "away": 0.0, "unclear": 0.0,
                                               "by_day": {}, "evidence": []})
        b = slot.get("bucket", "unclear")
        p[b] += slot["off_min"]
        day = p["by_day"].setdefault(str(slot["start"].date()), {"work": 0.0, "not_work": 0.0, "away": 0.0, "unclear": 0.0})
        day[b] += slot["off_min"]
        if b == "unclear":
            uw = p.setdefault("unclear_why", {})
            uw[slot.get("why", "")] = round(uw.get(slot.get("why", ""), 0) + slot["off_min"], 1)
        if b in ("not_work", "away", "unclear") and slot.get("shot"):
            s = slot["shot"]
            dest = SHOTS / slug(slot["person"]) / str(slot["start"].date()) / f"{slot['start']:%H%M}_{s['id']}.jpg"
            if not dest.exists():
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(_fetch(s["full_url"]))
                except Exception:  # noqa: BLE001 - evidence image is a convenience
                    pass
            p["evidence"].append({"date": str(slot["start"].date()), "time": f"{slot['start']:%H:%M}",
                                  "bucket": b, "minutes": round(slot["off_min"], 1), "activity": slot["activity"],
                                  "why": slot["why"], "img": str(dest) if dest.exists() else ""})
    for p in result.values():
        for k in ("work", "not_work", "away", "unclear"):
            p[k] = round(p[k] / 60, 2)
        for d in p["by_day"].values():
            for k in d:
                d[k] = round(d[k] / 60, 2)
    usage = [cache[str(i)] for i in cache]
    result["_meta"] = {"claude_calls_this_run": calls, "check_errors": errors[:5], "not_checked_cap": len(skipped),
                       "shared_label": len(followers),
                       "labels_cached": len(cache)}
    return result
