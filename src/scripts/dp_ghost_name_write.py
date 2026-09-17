"""Write the resolved names of the "Unknown <Surname>" DP relatives to the account (step 3).

Input is `output/unknown_ghosts/trestle_enrichment.csv` (Trestle verdict per ghost) joined to
`ghost_name_resolution.json` (run dir, live REL slot, ghost's phones). Two stages:

  names    verdict NEW PERSON / NAMESAKE SON: the slot `REL{n}: Full Name` that still reads
           `UNKNOWN <SURNAME>` gets the confirmed name, and the pinned dial sheet has the
           ghost's line renamed plus a one-line NAME UPDATE footer. Nothing else moves.
  merges   verdict = NAMED RELATIVE: the ghost IS a relative already in another slot
           (SmartSkip listed one person twice). The ghost's `Rel{A}.{m}` phones are retagged
           `Rel{B}.{k}` onto the named relative's slot B, its numbers fill B's free
           `REL{B}: Phone k` fields, slot A is blanked, and the dial sheet line is rewritten.
           Phone tags go through owner upsert-phones with the FULL list in live order (the
           dial order must not reshuffle), status/flags passed through unchanged.

Guards (every one was a live trap on an earlier pusher): only ACTIVE custom-field uuids; a
slot is written only while it STILL holds the ghost name; the board note is edited in place
(PATCH text, trusted on read-back only, re-pinned if the pin dropped) and never deleted;
every write is read back; a per-record backup (fields, message text, full phone list) goes to
the log so `--undo` can restore it.

    python src/scripts/dp_ghost_name_write.py names                     # dry run, all
    python src/scripts/dp_ghost_name_write.py names --probe "<key>" --commit
    python src/scripts/dp_ghost_name_write.py names --commit
    python src/scripts/dp_ghost_name_write.py merges --probe "<key>" --commit
    python src/scripts/dp_ghost_name_write.py qa                        # re-read every written record
    python src/scripts/dp_ghost_name_write.py undo --log <write_log.jsonl> [--commit]

Excluded by design: the 10 rows Trestle left unconfirmed, the Welzel ghost (Trestle says it is
the named in-law Cindy Welzel -- not a signer, step 5), and every ghost never probed.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from dpd_lane_phone_score import Api  # noqa: E402
from obituary_dp_batch import board_messages, names_match  # noqa: E402
from rel_pr_name_repair import _phone_payload, active_fields, clean_name, d10, live_state, parse_tag  # noqa: E402
from dp_ghost_name_resolver import OUT, jload, same_person  # noqa: E402

ENRICH = OUT / "trestle_enrichment.csv"
RESULTS = OUT / "ghost_name_resolution.json"
EXCLUDE_GHOSTS = {"Unknown Welzel"}          # Trestle: the named in-law Cindy Welzel, not a signer
REL_NAME_RE = re.compile(r"^REL(\d+): Full Name$")
REL_PHONE_RE = re.compile(r"^REL(\d+): Phone (\d+)$")
TODAY = datetime.now().strftime("%Y-%m-%d")


def say(m: str) -> None:
    print(m, flush=True)


def load_targets(stage: str) -> list[dict]:
    res = jload(RESULTS)
    by_key = {r["key"]: r for r in res["records"]}
    rows = list(csv.DictReader(open(ENRICH, encoding="utf-8-sig")))
    # dupnames: the "= NAMED RELATIVE" rows whose twin holds NO REL slot of their own on the live
    # record (7 of 9, measured 2026-09-17) -- the ghost slot is that person's only presence, so
    # the write is a rename, not a phone move; the note says who the twin is
    want = {"names": ("NEW PERSON", "NAMESAKE SON"), "merges": ("= NAMED RELATIVE",),
            "dupnames": ("= NAMED RELATIVE",)}[stage]
    out = []
    for x in rows:
        if x["verdict"] not in want or x["ghost_name"] in EXCLUDE_GHOSTS or not x["proposed_full"]:
            continue
        r = by_key[x["key"]]
        uuid = None
        for line in open(ROOT / r["run"] / "push_log.jsonl", encoding="utf-8"):
            e = json.loads(line)
            if e.get("key") == x["key"] and e.get("uuid"):
                uuid = e["uuid"]
        if not uuid:
            say(f"  !! no property uuid in the push log for {x['ghost_name']} ({x['key']}) -- skipped")
            continue
        out.append({**x, "uuid": uuid, "run": r["run"], "ghost_rel": r.get("ghost_rel") or {},
                    "named_relatives": r["named_relatives_detail"], "note_hint": x["note"]})
    return out


def find_ghost_slot(cf: dict, ghost_name: str, hint: str) -> str:
    """The REL slot number whose Full Name is still the ghost's ('UNKNOWN GLENN')."""
    if hint and clean_name(cf.get(f"{hint}: Full Name")).lower() == ghost_name.lower():
        return hint[3:]
    for lbl, val in cf.items():
        m = REL_NAME_RE.match(lbl)
        if m and clean_name(val).lower() == ghost_name.lower():
            return m.group(1)
    return ""


def find_named_slot(cf: dict, name: str, skip_slot: str) -> str:
    for lbl, val in cf.items():
        m = REL_NAME_RE.match(lbl)
        if m and m.group(1) != skip_slot and val and (same_person(clean_name(val), name) or names_match(clean_name(val), name)):
            return m.group(1)
    return ""


def dial_sheet(api: Api, uuid: str, ghost_name: str) -> dict | None:
    """The pinned board note that names the ghost (any family), else any note that does."""
    msgs = board_messages(api, uuid)
    hits = [m for m in msgs if ghost_name.lower() in (m.get("message") or "").lower()]
    if not hits:
        return None
    return next((m for m in hits if m.get("pinned")), hits[0])


def edit_note(api: Api, uuid: str, msg: dict, new_text: str, log: dict, commit: bool) -> None:
    log["backup"]["message"] = {"uuid": msg["uuid"], "pinned": bool(msg.get("pinned")), "text": msg.get("message") or ""}
    if not commit:
        log["would_write"]["message"] = new_text[-400:]
        return
    st, resp = api.write("PATCH", f"/api/internal/property/{uuid}/message/{msg['uuid']}/", {"message": new_text[:4000]})
    after = next((m for m in board_messages(api, uuid) if m.get("uuid") == msg["uuid"]), None)
    stored = (after or {}).get("message") or ""
    ok = re.sub(r"\s+", " ", stored).strip() == re.sub(r"\s+", " ", new_text[:4000]).strip()
    log["steps"]["message"] = {"status": st, "edited": ok, "resp": "" if ok else str(resp)[:200]}
    if not ok:
        log["verify"].append("board note PATCH did not store the new text")
        return
    if msg.get("pinned") and not after.get("pinned"):
        st3, _ = api.write("POST", f"/api/internal/property/{uuid}/message/{msg['uuid']}/pin/", {})
        log["steps"]["repin"] = st3
        re_read = next((m for m in board_messages(api, uuid) if m.get("uuid") == msg["uuid"]), {})
        if not re_read.get("pinned"):
            log["verify"].append("board note lost its pin and re-pin did not stick")


def write_cf(api: Api, uuid: str, body: list[dict], log: dict) -> bool:
    st, resp = api.write("PATCH", f"/api/internal/property/{uuid}/custom-field/update-values/", body)
    log["steps"]["cf"] = {"status": st, "sent": len(body), "resp": "" if st in (200, 201, 204) else str(resp)[:200]}
    if st not in (200, 201, 204):
        log["error"] = f"custom-field update {st}"
        return False
    return True


# ───────────────────────── stage 1: names ─────────────────────────

def write_name(api: Api, t: dict, fields: dict[str, str], commit: bool) -> dict:
    new_name = clean_name(t["proposed_full"]).upper()
    log = {"stage": "names", "key": t["key"], "verdict": t["verdict"], "uuid": t["uuid"], "ghost": t["ghost_name"], "new_name": new_name,
           "at": datetime.now().isoformat(timespec="seconds"), "commit": commit, "steps": {}, "backup": {},
           "would_write": {}, "verify": []}
    live = live_state(api, t["uuid"])
    if live.get("_error"):
        log["error"] = f"detail {live['_error']}"
        return log
    slot = find_ghost_slot(live["cf"], t["ghost_name"], t["live_rel_slot"])
    if not slot:
        log["error"] = f"no REL slot still holds {t['ghost_name']!r} (live Full Names: " \
                       f"{[v for l, v in live['cf'].items() if REL_NAME_RE.match(l) and v]})"
        return log
    label = f"REL{slot}: Full Name"
    if label not in fields:
        log["error"] = f"{label} is not an active custom field"
        return log
    log["slot"] = slot
    log["backup"]["cf"] = {label: live["cf"].get(label, "")}
    msg = dial_sheet(api, t["uuid"], t["ghost_name"])
    new_text = None
    if msg:
        text = msg.get("message") or ""
        new_text = re.sub(re.escape(t["ghost_name"]), new_name, text, flags=re.I)
        why = t["note_hint"].split(";")[0].strip()
        new_text += (f"\n\nNAME UPDATE {TODAY}: REL{slot} was '{t['ghost_name']}' -> {new_name} "
                     f"(Trestle reverse phone on {t['holder_phones'].split()[0] if t['holder_phones'] else 'its number'}, "
                     f"age {t['holder_age_range'] or '?'}{', at the property' if t['holder_at_property'] else ''}"
                     f"{'; ' + why if why and t['verdict'] == 'NAMESAKE SON' else ''})")
    else:
        log["verify"].append("no board note names the ghost -- field written, sheet unchanged")
    if not commit:
        log["would_write"]["cf"] = {label: new_name}
        if msg:
            edit_note(api, t["uuid"], msg, new_text, log, commit=False)
        return log
    if not write_cf(api, t["uuid"], [{"field_uuid": fields[label], "value": new_name}], log):
        return log
    if msg:
        edit_note(api, t["uuid"], msg, new_text, log, commit=True)
    after = live_state(api, t["uuid"])
    got = clean_name(after.get("cf", {}).get(label))
    if got.lower() != new_name.lower():
        log["verify"].append(f"{label}: want {new_name!r} got {got!r}")
    log["ok"] = not log["verify"]
    return log


# ───────────────────────── stage 2: merges ─────────────────────────

def write_merge(api: Api, t: dict, fields: dict[str, str], commit: bool) -> dict:
    target_name = clean_name(t["proposed_full"])
    log = {"stage": "merges", "key": t["key"], "uuid": t["uuid"], "ghost": t["ghost_name"], "into": target_name,
           "at": datetime.now().isoformat(timespec="seconds"), "commit": commit, "steps": {}, "backup": {},
           "would_write": {}, "verify": []}
    live = live_state(api, t["uuid"])
    if live.get("_error"):
        log["error"] = f"detail {live['_error']}"
        return log
    cf = live["cf"]
    a = find_ghost_slot(cf, t["ghost_name"], t["live_rel_slot"])
    if not a:
        log["error"] = f"no REL slot still holds {t['ghost_name']!r}"
        return log
    # the named relative's slot: match the Trestle holder OR the record's own spelling
    rec_named = next((n["name"] for n in t["named_relatives"] if same_person(target_name, n["name"])), target_name)
    b = find_named_slot(cf, target_name, a) or find_named_slot(cf, rec_named, a)
    if not b:
        log["error"] = f"{target_name!r} ({rec_named!r}) holds no REL slot on the live record -- cannot merge"
        return log
    log["slot_from"], log["slot_into"] = a, b
    tag_a, tag_b = f"Rel{a}.", f"Rel{b}."
    # phones the ghost slot owns = the numbers carrying Rel{a}.{m}
    ghost_phones = [ph for ph in live["phones"] if any(x.startswith(tag_a) for x in ph["tags"])]
    used_k = {int(x[len(tag_b):]) for ph in live["phones"] for x in ph["tags"]
              if x.startswith(tag_b) and x[len(tag_b):].isdigit()}
    next_k = (max(used_k) + 1) if used_k else 1
    retag: dict[str, list[str]] = {}
    for ph in ghost_phones:
        new_tags = [x for x in ph["tags"] if not x.startswith(tag_a)]
        if any(x.startswith(tag_b) for x in ph["tags"]):
            retag[d10(ph["number"])] = new_tags            # B already owns this line: just drop the A tag
            continue
        while next_k in used_k:
            next_k += 1
        used_k.add(next_k)
        retag[d10(ph["number"])] = new_tags + [f"{tag_b}{next_k}"]
    # fields: blank slot A, fill B's free Phone k with A's numbers not already listed under B
    body, backup = [], {}
    b_numbers = {d10(cf.get(f"REL{b}: Phone {k}")) for k in (1, 2, 3)} - {""}
    free_k = [k for k in (1, 2, 3) if f"REL{b}: Phone {k}" in fields and not d10(cf.get(f"REL{b}: Phone {k}"))]
    for ph in ghost_phones:
        n = d10(ph["number"])
        if n in b_numbers or not free_k:
            continue
        k = free_k.pop(0)
        lbl = f"REL{b}: Phone {k}"
        backup[lbl] = cf.get(lbl, "")
        body.append({"field_uuid": fields[lbl], "value": ph["number"]})
        b_numbers.add(n)
    for lbl in [f"REL{a}: Full Name"] + [f"REL{a}: Phone {k}" for k in (1, 2, 3)]:
        if lbl in fields and cf.get(lbl):
            backup[lbl] = cf.get(lbl, "")
            body.append({"field_uuid": fields[lbl], "value": ""})
    log["backup"]["cf"] = backup
    log["backup"]["owner_uuid"] = live["owner_uuid"]
    log["backup"]["phones"] = list(live["phones"])
    msg = dial_sheet(api, t["uuid"], t["ghost_name"])
    new_text = None
    if msg:
        text = msg.get("message") or ""
        new_text = re.sub(re.escape(t["ghost_name"]), f"(merged into REL{b} {rec_named.upper()})", text, flags=re.I)
        new_text += (f"\n\nMERGE {TODAY}: REL{a} '{t['ghost_name']}' was the same person as REL{b} {rec_named.upper()} "
                     f"(Trestle reverse phone: {target_name}, age {t['holder_age_range'] or '?'}); "
                     f"{len(retag)} number(s) moved to Rel{b}.x, slot REL{a} cleared")
    if not commit:
        log["would_write"] = {"cf": [(x["field_uuid"], x["value"]) for x in body], "cf_labels": list(backup),
                              "retag": retag, "message": (new_text or "")[-300:]}
        return log
    if body and not write_cf(api, t["uuid"], body, log):
        return log
    if retag and live["owner_uuid"]:
        payload = [_phone_payload(ph, retag.get(d10(ph["number"]), ph["tags"])) for ph in live["phones"]]
        st, resp = api.write("POST", f"/api/internal/owner/{live['owner_uuid']}/upsert-phones/", {"phones": payload})
        log["steps"]["phones"] = {"status": st, "sent": len(payload), "retagged": len(retag),
                                  "resp": "" if st in (200, 201, 204) else str(resp)[:200]}
        if st not in (200, 201, 204):
            log["error"] = f"upsert-phones {st}"
            return log
    if msg:
        edit_note(api, t["uuid"], msg, new_text, log, commit=True)
    after = live_state(api, t["uuid"])
    for x in body:
        lbl = next(l for l, u in fields.items() if u == x["field_uuid"])
        got = after["cf"].get(lbl, "") or ""
        if (d10(got) if "Phone" in lbl else clean_name(got).lower()) != (d10(x["value"]) if "Phone" in lbl else clean_name(x["value"]).lower()):
            log["verify"].append(f"{lbl}: want {x['value']!r} got {got!r}")
    after_by = {d10(ph["number"]): set(ph["tags"]) for ph in after["phones"]}
    for ph in live["phones"]:
        want = set(retag.get(d10(ph["number"]), ph["tags"]))
        got = after_by.get(d10(ph["number"]))
        if got is None:
            log["verify"].append(f"phone {ph['number']} vanished")
        elif got != want:
            log["verify"].append(f"phone {ph['number']} tags {sorted(got)} != {sorted(want)}")
    if [d10(p["number"]) for p in live["phones"]] != [d10(p["number"]) for p in after["phones"]]:
        log["verify"].append("phone order changed")
    log["ok"] = not log["verify"]
    return log


# ───────────────────────── qa / undo ─────────────────────────

def cmd_undo(api: Api, log_path: Path, fields: dict[str, str], commit: bool) -> int:
    n = 0
    for line in open(log_path, encoding="utf-8"):
        e = json.loads(line)
        if not e.get("commit") or e.get("error") and not e.get("steps"):
            continue
        bk = e.get("backup") or {}
        say(f"{e['ghost']} -> restore {list((bk.get('cf') or {}).keys())}"
            f"{', phones' if bk.get('phones') else ''}{', note' if bk.get('message') else ''}")
        if not commit:
            continue
        if bk.get("cf"):
            body = [{"field_uuid": fields[l], "value": v} for l, v in bk["cf"].items() if l in fields]
            st, _ = api.write("PATCH", f"/api/internal/property/{e['uuid']}/custom-field/update-values/", body)
            say(f"   cf {st}")
        if bk.get("phones") and bk.get("owner_uuid"):
            payload = [_phone_payload(ph, ph["tags"]) for ph in bk["phones"]]
            st, _ = api.write("POST", f"/api/internal/owner/{bk['owner_uuid']}/upsert-phones/", {"phones": payload})
            say(f"   phones {st}")
        if bk.get("message"):
            m = bk["message"]
            st, _ = api.write("PATCH", f"/api/internal/property/{e['uuid']}/message/{m['uuid']}/", {"message": m["text"]})
            say(f"   note {st}")
        n += 1
    say(f"{n} record(s) {'restored' if commit else 'would be restored'}")
    return 0


def cmd_qa(api: Api) -> int:
    bad = 0
    for path in sorted(OUT.glob("write_log_*.jsonl")):
        for line in open(path, encoding="utf-8"):
            e = json.loads(line)
            if not e.get("commit") or e.get("error"):
                continue
            live = live_state(api, e["uuid"])
            if e["stage"] == "names":
                got = clean_name(live["cf"].get(f"REL{e['slot']}: Full Name"))
                ok = got.lower() == e["new_name"].lower()
            else:
                ok = not clean_name(live["cf"].get(f"REL{e['slot_from']}: Full Name")) and \
                     not any(x.startswith(f"Rel{e['slot_from']}.") for ph in live["phones"] for x in ph["tags"])
            msgs = board_messages(api, e["uuid"])
            pinned_ok = any(m.get("pinned") and (e.get("new_name") or "MERGE").lower() in (m.get("message") or "").lower() for m in msgs)
            say(f"{'ok  ' if ok and pinned_ok else 'FAIL'} {e['ghost']:<26} {e['stage']:<6} field={'ok' if ok else 'BAD'} sheet={'pinned+named' if pinned_ok else 'MISSING'}")
            bad += not (ok and pinned_ok)
    say(f"\n{bad} failing")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["names", "dupnames", "merges", "qa", "undo"])
    ap.add_argument("--probe", help="one record key")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--log", help="undo: the write_log_*.jsonl to restore from")
    a = ap.parse_args()
    api = Api()
    fields = active_fields(api)
    if a.stage == "qa":
        return cmd_qa(api)
    if a.stage == "undo":
        return cmd_undo(api, Path(a.log), fields, a.commit)
    targets = load_targets(a.stage)
    if a.stage == "dupnames":
        for t in targets:
            t["verdict"] = "NAMESAKE SON"     # so write_name prints the twin note as the footer reason
    if a.probe:
        targets = [t for t in targets if t["key"] == a.probe]
        if not targets:
            sys.exit(f"{a.probe} is not a {a.stage} target")
    say(f"{a.stage}: {len(targets)} record(s){' -- DRY RUN' if not a.commit else ''}")
    log_path = OUT / f"write_log_{a.stage}_{datetime.now():%Y%m%dT%H%M%S}.jsonl"
    ok = err = 0
    with open(log_path, "a", encoding="utf-8") as lf:
        for t in targets:
            log = (write_merge if a.stage == "merges" else write_name)(api, t, fields, a.commit)
            lf.write(json.dumps(log, ensure_ascii=False) + "\n")
            if log.get("error"):
                err += 1
                say(f"  ERR  {t['ghost_name']:<26} {log['error']}")
            elif not a.commit:
                ww = log["would_write"]
                say(f"  plan {t['ghost_name']:<26} -> {t['proposed_full']!r:<34} slot {log.get('slot') or log.get('slot_from', '?')}"
                    f"{' -> REL' + log['slot_into'] if log.get('slot_into') else ''}"
                    f"{'  note: yes' if ww.get('message') else '  note: NONE'}"
                    f"{'  retag ' + str(len(ww.get('retag', {}))) if a.stage == 'merges' else ''}"
                    f"{'  ' + '; '.join(log['verify']) if log['verify'] else ''}")
            else:
                ok += bool(log.get("ok"))
                say(f"  {'ok  ' if log.get('ok') else 'WARN'} {t['ghost_name']:<26} -> {log.get('new_name') or log.get('into')}"
                    f"  cf {log['steps'].get('cf', {}).get('status')} note {log['steps'].get('message', {}).get('status', '-')}"
                    f"{'  ' + '; '.join(log['verify']) if log['verify'] else ''}")
    say(f"\n{'written ok ' + str(ok) if a.commit else 'planned ' + str(len(targets) - err)}, errors {err}; log {log_path}")
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
