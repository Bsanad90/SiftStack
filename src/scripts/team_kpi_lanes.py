"""team_kpi_lanes.py - which dialer lane each SmrtPhone call belongs to (FTM / Hottest / Obituary).

The Dialer Day Card's own EOD numbers never reach us (the WhatsApp paste transport was parked
2026-09-22), so lanes are rebuilt from the systems, read-only:

  1. call -> DataSift owner: the SmrtPhone segment detail carries contact.contactLink
     (app.reisift.io/records/owners/<owner uuid>/...). Cached forever per SmrtPhone contact id
     (and per segment id) in output/team_kpi/cache/contact_owner.json.
     Many segments come back with contact = null (2026-10-07: 290 of 594 outbound on 10-06,
     although has_crm is true); for those the DataSift property `search` on the dialed number
     finds the owner (it matches owner phones), accepted only when the owner's last name appears
     in the SmrtPhone contact name. Cached per number in the same file.
  2. owner -> lane: POST property/ with {"owners": [...], "all_tags": [<tag uuid>]} (proven
     2026-10-07: `owners` is a real query key; the thin row carries owner.uuid), batched 100
     owners per query, one query set per lane tag.

Lane precedence follows the entry-tag rules: FTM (a probate that is also an obituary keeps FTM)
> Obituaries > Priority 1/2 (= Hottest) > "Other" (in DataSift, no lane tag) > "Not in
DataSift" (the dialed contact has no REISift link). Lanes are the record's tags NOW, not at
dial time; a record that moved lanes this week is counted in its current lane.
"""
from __future__ import annotations

import collections as C
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE), str(ROOT / "src")]
CACHE = ROOT / "output" / "team_kpi" / "cache" / "contact_owner.json"
LANES = ["FTM", "Hottest", "Obituary", "Other", "Not in DataSift"]
LANE_TAGS = [("FTM", ["FTM"]), ("Obituary", ["Obituaries"]), ("Hottest", ["Priority 1", "Priority 2"])]
OWNER_RE = re.compile(r"/records/owners/([0-9a-f-]{36})")


def _load() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {"contact": {}, "segment": {}, "number": {}}


def _save(c: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c), encoding="utf-8")
    tmp.replace(CACHE)


def owners_for(rows: list[dict]) -> dict[str, str]:
    """segment id -> DataSift owner uuid ('' when the contact is not a REISift record)."""
    import smrtphone_segments as ss
    c = _load()
    need = []
    for r in rows:
        sid, cid = str(r.get("segment_id") or ""), str(r.get("contact_id") or "")
        if not sid:
            continue
        if cid and cid in c["contact"]:
            c["segment"][sid] = c["contact"][cid]
        elif sid not in c["segment"]:
            need.append(sid)

    def fetch(sid):
        try:
            d = ss._get(f"/api/v1/logs/segments/{sid}")["data"]
        except Exception:  # noqa: BLE001 - leave it unresolved, retry next run
            return sid, None, None
        ct = d.get("contact") or {}
        m = OWNER_RE.search(ct.get("contactLink") or "")
        return sid, str(ct.get("contactId") or d.get("contact_id") or ""), (m.group(1) if m else "")

    with ThreadPoolExecutor(4) as ex:
        for i, (sid, cid, owner) in enumerate(ex.map(fetch, need)):
            if owner is None:
                continue
            c["segment"][sid] = owner
            if cid and owner:
                c["contact"][cid] = owner
            if i % 200 == 199:
                _save(c)
    # fallback: no link on the segment -> find the owner by the dialed number
    c.setdefault("number", {})
    todo = {}
    for r in rows:
        sid = str(r.get("segment_id") or "")
        if sid and c["segment"].get(sid) == "" and r.get("participant_number"):
            todo.setdefault(r["participant_number"], r.get("participant") or "")
    todo = {n: nm for n, nm in todo.items() if n not in c["number"]}
    if todo:
        # 4 workers, each with its own client; saved every 200 so a stopped run keeps its work
        # (a month's first run is several thousand numbers)
        from dpd_lane_phone_score import Api
        apis = [Api() for _ in range(4)]

        def look(i_item):
            i, (num, name) = i_item
            try:
                st, res = apis[i % 4].post_as_get("/api/internal/property/",
                                                  {"limit": 10, "offset": 0, "query": {"must": {"search": num}}})
            except Exception:  # noqa: BLE001 - unresolved, retried next run
                return num, None
            words = {w.lower() for w in re.findall(r"[A-Za-z']{2,}", name)}
            hits = {(x.get("owner") or {}).get("uuid") for x in (res.get("results") or [] if st == 200 else [])
                    if ((x.get("owner") or {}).get("last_name") or "").lower() in words
                    or ((x.get("owner") or {}).get("company") or "").lower() in name.lower()}
            hits.discard(None)
            return num, (sorted(hits)[0] if len(hits) == 1 else "")

        with ThreadPoolExecutor(4) as ex:
            for i, (num, owner) in enumerate(ex.map(look, enumerate(todo.items()))):
                if owner is not None:
                    c["number"][num] = owner
                if i % 200 == 199:
                    _save(c)
    _save(c)
    out = {}
    for r in rows:
        sid = str(r.get("segment_id"))
        o = c["segment"].get(sid)
        if o == "":
            o = c["number"].get(r.get("participant_number") or "", "")
        out[sid] = o
    return out


def lanes_for(owner_uuids: set[str]) -> dict[str, str]:
    """owner uuid -> lane (current tags)."""
    from dpd_lane_phone_score import Api
    api = Api()
    st, t = api.get("/api/internal/tag/?limit=999")
    tags = t.get("results", t) if isinstance(t, dict) else t
    tid = {x["title"]: x["uuid"] for x in tags}
    owners = sorted(o for o in owner_uuids if o)
    lane = {}
    for name, titles in LANE_TAGS:
        for title in titles:
            if title not in tid:
                continue
            for i in range(0, len(owners), 100):
                chunk = owners[i:i + 100]
                off = 0
                while True:
                    st, r = api.post_as_get("/api/internal/property/", {
                        "limit": 100, "offset": off,
                        "query": {"must": {"owners": chunk, "all_tags": [tid[title]]}}})
                    if st != 200:
                        raise RuntimeError(f"lane query {title}: HTTP {st}")
                    res = r.get("results") or []
                    for x in res:
                        ou = (x.get("owner") or {}).get("uuid")
                        if ou and ou not in lane:      # precedence = LANE_TAGS order
                            lane[ou] = name
                    off += 100
                    if len(res) < 100:
                        break
    return {o: lane.get(o, "Other") for o in owners}


def lane_stats(rows: list[dict], callers: set, human: set, rpc: set) -> dict:
    """Outbound calls by lane and by caller x lane: records (owners) worked, dials, pick-ups, owners reached."""
    out = [r for r in rows if r.get("direction") == "Outbound" and r.get("person") in callers]
    seg_owner = owners_for(out)
    lane_of = lanes_for({o for o in seg_owner.values() if o})
    tot = {l: {"records": set(), "dials": 0, "pickups": 0, "owners": 0, "correct": 0} for l in LANES}
    per = C.defaultdict(lambda: {l: {"records": set(), "dials": 0, "pickups": 0, "owners": 0} for l in LANES})
    unresolved = 0
    for r in out:
        o = seg_owner.get(str(r.get("segment_id")))
        if o is None:
            unresolved += 1
        ln = "Not in DataSift" if not o else lane_of.get(o, "Other")
        key = o or f"num:{r.get('participant_number')}"
        for acc in (tot[ln], per[r["person"]][ln]):
            acc["records"].add(key)
            acc["dials"] += 1
            acc["pickups"] += r["status"] in human
            acc["owners"] += r["status"] in rpc
        tot[ln]["correct"] += r["status"] == "Correct Number"

    def fin(d):
        return {**d, "records": len(d["records"])}
    return {"lanes": {l: fin(v) for l, v in tot.items()},
            "per_caller": {p: {l: fin(v) for l, v in ls.items()} for p, ls in per.items()},
            "unresolved_calls": unresolved}
