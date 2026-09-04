"""Put the Seller Leads xlsx properties onto the Lead Management SiftLine board.

For each property in "Seller Leads - Last view used.xlsx":
  1. resolve the DataSift record by exact street+zip5 match (owner tie-break on dupes),
  2. add a card in the "New Lead (Unqualified)" column of the Lead Management board
     (or MOVE the existing card there if the property is already on the board),
  3. tag "Re-qualify" (read-modify-write of the full tag set -- tags_add is ignored),
  4. assign to Mohammed Bagoury.

SiftLine API (discovered live 2026-09-03 by capturing the SPA's own calls):
  GET  /api/internal/siftline/board/                          boards
  GET  /api/internal/siftline/board/{board}/column/           columns (phases)
  GET  /api/internal/siftline/board/column/{col}/card/        cards in a column
  POST /api/internal/siftline/board/column/{col}/card/        create; body {"prop": uuid}
                                                              (OPTIONS says prop_uuid works;
                                                              live it 400s without "prop")
  PATCH  .../column/{col}/card/{card}/ {"column": new_col}    move a card between phases
  DELETE .../column/{col}/card/{card}/                        remove a card (204)

Tag semantics (proven live on the undo): PATCH /property/{uuid}/ {"tags": [...]} is
MERGE-ONLY -- it adds names missing from the record but never removes any, so a
read-modify-write with a name dropped is a silent no-op. Removal is its own endpoint:
POST /api/internal/property/{uuid}/remove-tags/ {"tags": [...]} (200, echoes removed_tags).

Usage:
    python src/scripts/seller_requalify.py plan             # read-only: resolve + board state
    python src/scripts/seller_requalify.py probe            # first resolvable record, full write + read-back
    python src/scripts/seller_requalify.py commit           # all records (resumable)
    python src/scripts/seller_requalify.py verify           # read-only re-audit of every pushed record
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_pull import LiveApi, BASE  # noqa: E402
import urllib.request  # noqa: E402
import urllib.error  # noqa: E402

XLSX = Path(r"C:\Users\pc\Desktop\Galal Development\Seller Leads - Last view used.xlsx")
STATE = Path(__file__).resolve().parents[2] / "output" / "seller_requalify_state.json"

BOARD = "8bbee183-27b4-4734-870f-c9eebd50c9d0"           # Lead Management
TARGET_COL = "ed3dbc3c-5375-4e45-ba83-348677250ef5"      # New Lead (Unqualified)
BAGOURY = "1a79ed6e-d8df-42af-9657-c4f756c5bcca"         # Mohammed Bagoury
TAG = "Re-qualify"


def say(msg: str) -> None:
    print(msg, flush=True)


def norm_addr(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def zip5(s: str) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[:5]


# ───────────────────────── api ─────────────────────────

class Api:
    def __init__(self):
        self.api = LiveApi()

    def get(self, path):
        return self.api.get(path)

    def search(self, body):
        return self.api.post_as_get("/api/internal/property/", body)

    def write(self, method: str, path: str, body, tries: int = 4):
        api = self.api
        for attempt in range(tries):
            if time.time() - api.minted > 1800:
                api._mint()
            api._throttle()
            data = json.dumps(body).encode()
            req = urllib.request.Request(
                BASE + path, data=data, method=method,
                headers={"Authorization": "Bearer " + api.token,
                         "Content-Type": "application/json", "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                    return r.status, (json.loads(raw) if raw else {})
            except urllib.error.HTTPError as e:
                txt = e.read().decode("utf-8", "replace")
                if e.code == 401 and attempt == 0:
                    api._mint()
                    continue
                if e.code == 429:
                    m = re.search(r"available in (\d+)", txt, re.I)
                    time.sleep(min((int(m.group(1)) if m else 30) + 2, 300))
                    continue
                return e.code, txt
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == tries - 1:
                    return 0, str(e)
                time.sleep(2 ** attempt)
        return 0, "retries exhausted"


# ───────────────────────── xlsx ─────────────────────────

def load_rows() -> list[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(XLSX, read_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    cols = {h: i for i, h in enumerate(rows[0]) if h}
    out, seen = [], {}
    for r in rows[1:]:
        raw = (r[cols["Property Address Map"]] or "").strip()
        seller = (r[cols["Seller Name"]] or "").strip()
        if not raw:
            continue
        addr = parse_addr(raw)
        if not addr:
            say(f"UNPARSEABLE address, skipped: {raw!r}")
            continue
        key = (norm_addr(addr["street"]), addr["zip"])
        if key in seen:
            seen[key]["sellers"].append(seller)
            continue
        rec = {"seller": seller, "sellers": [seller], "raw": raw, **addr}
        seen[key] = rec
        out.append(rec)
    return out


def parse_addr(raw: str) -> dict | None:
    s = re.sub(r",\s*USA\s*$", "", raw.strip(), flags=re.I)
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) < 3:
        return None
    m = re.match(r"^([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", parts[-1])
    if not m:
        return None
    return {"street": ", ".join(parts[:-2]), "city": parts[-2],
            "state": m.group(1), "zip": zip5(m.group(2))}


# ───────────────────────── resolve ─────────────────────────

def resolve(api: Api, rec: dict) -> tuple[str, dict, str]:
    """(uuid, hit, why_failed) -- exact street+zip5 among search hits."""
    st, body = api.search({"limit": 25, "offset": 0,
                           "query": {"must": {"search": rec["street"]}}})
    if st != 200:
        return "", {}, f"search failed {st}: {str(body)[:150]}"
    hits = body.get("results") or body.get("data") or []
    want = (norm_addr(rec["street"]), rec["zip"])
    exact = [h for h in hits if (norm_addr((h.get("address") or {}).get("street")),
                                 zip5((h.get("address") or {}).get("postal_code"))) == want]
    if len(exact) == 1:
        return exact[0]["uuid"], exact[0], ""
    if len(exact) > 1:
        # duplicate property records exist in this account -- tie-break on the seller's
        # last name against the record owner; never on a blank name.
        last = (rec["seller"].split()[-1] if rec["seller"] else "").strip().lower()
        if last:
            named = [h for h in exact
                     if ((h.get("owner") or {}).get("last_name") or "").strip().lower() == last]
            if named:
                return named[0]["uuid"], named[0], ""
        return "", {}, f"{len(exact)} duplicate records, seller '{rec['seller']}' broke no tie"
    return "", {}, f"0 exact of {len(hits)} hits"


# ───────────────────────── board ─────────────────────────

def board_columns(api: Api) -> list[dict]:
    st, body = api.get(f"/api/internal/siftline/board/{BOARD}/column/?offset=0&limit=999")
    if st != 200:
        raise RuntimeError(f"column list failed {st}: {str(body)[:200]}")
    return body.get("results") or []


def board_cards(api: Api) -> dict[str, dict]:
    """prop_uuid -> {card, column, column_title} across every column of the board."""
    out = {}
    for col in board_columns(api):
        offset = 0
        while True:
            st, body = api.get(f"/api/internal/siftline/board/column/{col['uuid']}/card/"
                               f"?ordering=order&offset={offset}&limit=200")
            if st != 200:
                raise RuntimeError(f"card list failed {st} for {col['title']}: {str(body)[:200]}")
            results = body.get("results") or []
            for c in results:
                pu = ((c.get("prop") or {}).get("uuid"))
                if pu:
                    out[pu] = {"card": c["uuid"], "column": col["uuid"],
                               "column_title": col["title"]}
            offset += len(results)
            if not body.get("next") or not results:
                break
    return out


def put_on_board(api: Api, uuid: str, existing: dict | None) -> tuple[bool, str]:
    if existing and existing["column"] == TARGET_COL:
        return True, "already in target column"
    if existing:
        # move the existing card rather than duplicating it
        st, resp = api.write("PATCH",
                             f"/api/internal/siftline/board/column/{existing['column']}/card/{existing['card']}/",
                             {"column": TARGET_COL})
        if st in (200, 202):
            return True, f"moved from '{existing['column_title']}'"
        return False, f"move failed {st}: {str(resp)[:200]}"
    # OPTIONS lists prop as required ("field") and prop_uuid as optional; live behaviour:
    # omitting prop 400s, so send the uuid under both keys.
    st, resp = api.write("POST",
                         f"/api/internal/siftline/board/column/{TARGET_COL}/card/",
                         {"prop": uuid, "prop_uuid": uuid})
    if st in (200, 201):
        return True, "card created"
    return False, f"create failed {st}: {str(resp)[:200]}"


# ───────────────────────── record writes ─────────────────────────

def tag_and_assign(api: Api, uuid: str) -> tuple[bool, str]:
    st, full = api.get(f"/api/internal/property/{uuid}/")
    if st != 200:
        return False, f"detail read failed {st}"
    current = [t.get("name") if isinstance(t, dict) else str(t) for t in (full.get("tags") or [])]
    body = {"assigned_to": BAGOURY}
    if TAG not in current:
        body["tags"] = current + [TAG]
    st, resp = api.write("PATCH", f"/api/internal/property/{uuid}/", body)
    if st != 200:
        return False, f"PATCH failed {st}: {str(resp)[:200]}"
    return True, "tagged+assigned"


def check_record(api: Api, uuid: str) -> list[str]:
    problems = []
    st, full = api.get(f"/api/internal/property/{uuid}/")
    if st != 200:
        return [f"detail read failed {st}"]
    tags = [t.get("name") if isinstance(t, dict) else str(t) for t in (full.get("tags") or [])]
    if TAG not in tags:
        problems.append("tag missing")
    a = full.get("assigned_to")
    au = a.get("uuid") if isinstance(a, dict) else a
    if au != BAGOURY:
        problems.append(f"assigned_to is {au}")
    return problems


# ───────────────────────── state ─────────────────────────

def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"records": {}}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")


# ───────────────────────── commands ─────────────────────────

def cmd_plan(api: Api) -> int:
    rows = load_rows()
    say(f"{len(rows)} unique properties in the xlsx")
    cards = board_cards(api)
    say(f"board currently holds {len(cards)} cards")
    state = load_state()
    ok = unresolved = 0
    for rec in rows:
        key = norm_addr(rec["street"]) + "|" + rec["zip"]
        uuid, hit, why = resolve(api, rec)
        entry = state["records"].setdefault(key, {})
        entry.update({"street": rec["street"], "city": rec["city"], "zip": rec["zip"],
                      "seller": rec["seller"], "uuid": uuid, "resolve_error": why})
        if uuid:
            ok += 1
            on = cards.get(uuid)
            entry["board_before"] = on["column_title"] if on else None
            owner = hit.get("owner") or {}
            say(f"  OK   {rec['street']}, {rec['zip']}  -> {uuid[:8]}  "
                f"owner={owner.get('first_name','')} {owner.get('last_name','')}  "
                f"status={hit.get('status')}  board={on['column_title'] if on else '-'}")
        else:
            unresolved += 1
            say(f"  MISS {rec['street']}, {rec['zip']}  ({rec['seller']}): {why}")
    save_state(state)
    say(f"\nresolved {ok}, unresolved {unresolved}. State -> {STATE}")
    return 0


def _push(api: Api, state: dict, keys: list[str]) -> int:
    cards = board_cards(api)
    failures = 0
    for key in keys:
        entry = state["records"][key]
        uuid = entry.get("uuid")
        if not uuid:
            continue
        okb, msgb = put_on_board(api, uuid, cards.get(uuid))
        okt, msgt = tag_and_assign(api, uuid)
        problems = check_record(api, uuid)
        entry["pushed"] = okb and okt and not problems
        entry["board_result"] = msgb
        entry["patch_result"] = msgt
        entry["problems"] = problems
        status = "OK " if entry["pushed"] else "FAIL"
        say(f"  {status} {entry['street']}: board[{msgb}] patch[{msgt}]"
            + (f" problems={problems}" if problems else ""))
        if not entry["pushed"]:
            failures += 1
        save_state(state)
    return failures


def cmd_probe(api: Api) -> int:
    state = load_state()
    keys = [k for k, e in state["records"].items() if e.get("uuid") and not e.get("pushed")]
    if not keys:
        say("nothing to probe -- run plan first")
        return 1
    say(f"probing 1 of {len(keys)} pending records")
    failures = _push(api, state, keys[:1])
    return 1 if failures else 0


def cmd_commit(api: Api) -> int:
    state = load_state()
    keys = [k for k, e in state["records"].items() if e.get("uuid") and not e.get("pushed")]
    say(f"{len(keys)} records to push")
    failures = _push(api, state, keys)
    say(f"\ndone, {failures} failures")
    return 1 if failures else 0


def cmd_verify(api: Api) -> int:
    state = load_state()
    cards = board_cards(api)
    bad = n = 0
    for key, entry in state["records"].items():
        uuid = entry.get("uuid")
        if not uuid:
            continue
        n += 1
        problems = check_record(api, uuid)
        on = cards.get(uuid)
        if not on:
            problems.append("no card on board")
        elif on["column"] != TARGET_COL:
            problems.append(f"card in '{on['column_title']}'")
        if problems:
            bad += 1
            say(f"  BAD {entry['street']}: {problems}")
    say(f"verify: {n - bad}/{n} clean")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "probe", "commit", "verify"])
    args = ap.parse_args()
    api = Api()
    return {"plan": cmd_plan, "probe": cmd_probe,
            "commit": cmd_commit, "verify": cmd_verify}[args.cmd](api)


if __name__ == "__main__":
    sys.exit(main())
