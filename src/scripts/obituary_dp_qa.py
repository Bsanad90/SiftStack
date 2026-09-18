"""QA the obituary deep-prospecting writeback against the LIVE account.

Reads push_plan.json, pulls every record back over the internal API and checks what
actually landed. Read-only by default; `--repair` fixes the gaps it found.

    python -u src/scripts/obituary_dp_qa.py                 # audit, writes nothing
    python -u src/scripts/obituary_dp_qa.py --repair        # fix the gaps it found
    python -u src/scripts/obituary_dp_qa.py --limit 25 -v   # spot check

    # Records from SEVERAL run dirs at once (the 2026-09-01 probate-push collision):
    python -u src/scripts/obituary_dp_qa.py --keys-file output/probate_fields_push/repair_keys.json -v
    python -u src/scripts/obituary_dp_qa.py --keys-file ... --key "1392stonecreekrd|21403" \\
        --repair --clear-extra-rel --extra-source output/probate_fields_push/push_plan.json
    python -u src/scripts/obituary_dp_qa.py --undo output/probate_fields_push/qa_undo_<stamp>.jsonl

Checks per record:
  message   a PINNED message on the board carrying the DP note (NOT the `notes` field --
            add-notes writes a field nobody reads on the record page). Detected by note
            FAMILY (odb._note_family), so a PROBATE note is never mistaken for a DP one.
  phones    every planned number present on the owner
  tags      every planned phone carries its EXACT Rel{N}.{M} / Owner.{M} tag AND its Trestle tier
  rel       every planned REL{n} custom field holds the planned VALUE (phones compared on the
            last 10 digits: the account stores +1 E.164); REL slots beyond the plan that hold
            a value are reported ("beyond plan"); they are blanked only with --clear-extra-rel
            AND --extra-source <other push_plan.json>, and only where the live value is what
            that other plan wrote -- unprovenanced extras are never touched
  proptags  the property tags (Deep Prospected / Deceased Owner / ...)

Repair writes: post+pin the DP note (never deletes a message), re-upsert the planned phones,
PATCH wrong/empty REL fields (and blank extras on request), merge missing property tags.
Every repaired record is snapshotted first (qa_backup_<stamp>/<key>.json) and gets an undo
line (qa_undo_<stamp>.jsonl) that --undo replays.
"""
import argparse
import importlib.util
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

_spec = importlib.util.spec_from_file_location("odb", ROOT / "src" / "scripts" / "obituary_dp_batch.py")
odb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(odb)

# "Unknown" is deliberately never written as a tag (see cmd_build), so it must not be
# audited as a missing one -- that read as 2 defects that were correct behaviour.
TIERS = {"Dial First", "Dial Second", "Dial Third", "Dial Fourth", "Drop"}
REL_RE = re.compile(r"^REL\d+: (Full Name|Phone \d)$")
OK = (200, 201, 204)

digits = odb._d10   # ONE phone normaliser shared with the pusher's remove-phones guard


def _cf_equal(label: str, want, got) -> bool:
    """Planned vs live custom-field value. Phone fields read back as +1XXXXXXXXXX whatever
    was sent, so they compare on the last 10 digits; names compare case/space-insensitive."""
    if "Phone" in label:
        return digits(want) == digits(got)
    return " ".join(str(want or "").split()).upper() == " ".join(str(got or "").split()).upper()


def _cf_diff(plan_cf: dict, got_cf: dict) -> tuple[list[str], list[str], list[str]]:
    """(missing, wrong, extra): planned labels that are empty live, planned labels holding a
    different value, and live REL labels with a value that the plan does not mention."""
    missing = [k for k in plan_cf if not got_cf.get(k)]
    wrong = [k for k in plan_cf if got_cf.get(k) and not _cf_equal(k, plan_cf[k], got_cf[k])]
    extra = [k for k, v in got_cf.items() if REL_RE.match(k) and v and k not in plan_cf]
    return missing, wrong, extra


def _planned_tag(x: dict) -> str | None:
    return next((t for t in (x.get("tags") or []) if odb._OURS_RE.match(t)), None)


def _tagnames(tags) -> set[str]:
    return {t.get("name") if isinstance(t, dict) else str(t) for t in (tags or [])}


def _safe(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)


_board = odb.board_messages
_cf_rows = odb.custom_field_rows


def _addr_is(full: dict, p: dict) -> bool:
    a = full.get("address") or {}
    return (odb.norm_addr(a.get("street")) == odb.norm_addr(p["street"])
            and odb.zip5(a.get("postal_code") or a.get("zip5") or "") == odb.zip5(p["zip"]))


def audit_one(api, key, p, repair=False, known_uuid=None, fields=None, opts=None,
              backup_dir: Path | None = None, undo_fh=None):
    """Return a dict of what is wrong with this record on the live account."""
    opts = opts or {}
    out = {"key": key, "street": p["street"], "uuid": None, "problems": [], "repaired": []}
    uuid, _rec, why = odb._resolve_uuid(api, p)
    if not uuid and known_uuid:
        # The resolver refuses blank owners and undisambiguated duplicates; the keys file is
        # the one place a manual uuid can be supplied. Trust it only if the address matches.
        st, full = api.get(f"/api/internal/property/{known_uuid}/")
        if st == 200 and _addr_is(full, p):
            uuid = known_uuid
        else:
            out["problems"].append(f"unresolved: {why}; supplied uuid {'404' if st != 200 else 'is another address'}")
            return out
    if not uuid:
        out["problems"].append(f"unresolved: {why}")
        return out
    # Duplicate-address records exist in the account: when the keys file says which uuid
    # this plan belongs to, a different resolution is a stop, not a write target.
    if known_uuid and uuid != known_uuid:
        out["problems"].append(f"uuid mismatch (resolved {uuid}, expected {known_uuid})")
        return out
    out["uuid"] = uuid

    st, full = api.get(f"/api/internal/property/{uuid}/")
    if st != 200:
        out["problems"].append(f"GET property {st}")
        return out
    msgs = _board(api, uuid)
    cf_rows, got_cf = _cf_rows(api, uuid)

    undo = {"key": key, "uuid": uuid, "cf_restore": [], "messages_posted": [], "tags_added": []}
    out["notes"] = []
    snapshot = {"key": key, "uuid": uuid, "taken_at": datetime.now().isoformat(timespec="seconds"),
                "property": full, "custom_fields": cf_rows, "messages": msgs}

    # ── the message board ──────────────────────────────────────────────
    fam = odb._note_family(p["note"])
    dp = [x for x in msgs if odb._note_family(x.get("message") or "") == fam]
    pinned = [x for x in dp if x.get("pinned")]
    other_pinned = {x.get("uuid") for x in msgs if x.get("pinned") and x not in dp}
    if not dp:
        out["problems"].append("no DP message on the board")
    elif not pinned:
        out["problems"].append("DP message present but NOT pinned")
    if repair and (not dp or not pinned):
        if not dp:
            sc, r = api.write("POST", f"/api/internal/property/{uuid}/message/",
                              {"message": p["note"][:4000]})
            mu = (r or {}).get("uuid") if isinstance(r, dict) else None
            out["repaired"].append(f"posted message ({sc})")
            if mu:
                undo["messages_posted"].append(mu)
        else:
            newest = sorted(dp, key=lambda x: str(x.get("created") or x.get("created_at") or ""))[-1]
            mu = newest.get("uuid")
            if len(dp) > 1:
                out["notes"].append(f"{len(dp)} same-family DP messages on the board; pinned the newest")
        if mu:
            sc2, _ = api.write("POST", f"/api/internal/property/{uuid}/message/{mu}/pin/", {})
            out["repaired"].append(f"pinned ({sc2})")
        after = _board(api, uuid)
        dp_after = [x for x in after if odb._note_family(x.get("message") or "") == fam and x.get("pinned")]
        still = {x.get("uuid") for x in after if x.get("pinned")}
        out["message_verified"] = bool(dp_after)
        out["other_pins_kept"] = other_pinned <= still
        if not out["message_verified"]:
            out["problems"].append("REPAIR FAILED: DP message not pinned after repair")
        if not out["other_pins_kept"]:
            out["problems"].append("REPAIR SIDE-EFFECT: a previously pinned message lost its pin")

    # ── phones + their tags ────────────────────────────────────────────
    have = {digits(x.get("number")): x for x in ((full.get("owner") or {}).get("phones") or [])}
    want = p["phones"]
    missing = [x["number"] for x in want if digits(x["number"]) not in have]
    if missing:
        out["problems"].append(f"{len(missing)} of {len(want)} phones missing")
    wrong_rel_tag, no_tier = [], []
    for x in want:
        got = have.get(digits(x["number"]))
        if not got:
            continue
        tags = _tagnames(got.get("tags"))
        # The EXACT planned slot tag, not any "Rel*": a phone carrying Rel1.1 when the plan
        # says Rel2.1 names the wrong relative on the dial list.
        pt = _planned_tag(x)
        if pt and pt not in tags:
            wrong_rel_tag.append(x["number"])
        if x.get("tier") and x["tier"] in TIERS and x["tier"] not in tags:
            no_tier.append(x["number"])
    if wrong_rel_tag:
        out["problems"].append(f"{len(wrong_rel_tag)} phones with wrong Rel tag")
    if no_tier:
        out["problems"].append(f"{len(no_tier)} phones with no Trestle tier tag")
    out["phone_repair"] = (missing, wrong_rel_tag, no_tier)
    if repair and (missing or wrong_rel_tag or no_tier):
        body = [{"number": x["number"], "type": x.get("type") or "UNKNOWN",
                 "tags": x.get("tags") or [], "status": "UNKNOWN", "is_connected": True}
                for x in want]
        owner_uuid = (full.get("owner") or {}).get("uuid")
        if owner_uuid:
            sc, resp = api.write("POST", f"/api/internal/owner/{owner_uuid}/upsert-phones/",
                                 {"phones": body})
            out["repaired"].append(f"re-upserted {len(body)} phones ({sc})")
            if sc not in OK:
                out["problems"].append(f"REPAIR FAILED: upsert-phones {sc} {str(resp)[:120]}")
            else:
                time.sleep(1.0)
                st_a, after_p = api.get(f"/api/internal/property/{uuid}/")
                have2 = {digits(x.get("number")): x for x in ((after_p.get("owner") or {}).get("phones") or [])}
                still_missing = [x["number"] for x in want if digits(x["number"]) not in have2]
                still_wrong = [x["number"] for x in want if digits(x["number"]) in have2
                               and _planned_tag(x) and _planned_tag(x) not in _tagnames(have2[digits(x["number"])].get("tags"))]
                out["phones_verified"] = not (still_missing or still_wrong)
                if still_missing:
                    # The QA never removes numbers; a full owner (30) refuses new ones. That is
                    # the pusher's job (`push --probe <key> --force --commit` re-pushes with the
                    # remove-phones guard), not something to fake here with a 200.
                    out["problems"].append(f"REPAIR FAILED: {len(still_missing)} phones still missing"
                                           + (" (owner at 30-phone cap: use the pusher re-push)" if len(have2) >= 30 else ""))
                if still_wrong:
                    out["problems"].append(f"REPAIR FAILED: {len(still_wrong)} phones still carry the wrong Rel tag")

    # ── REL custom fields: VALUES, not just presence ────────────────────
    cf_missing, cf_wrong, cf_extra = _cf_diff(p["custom_fields"], got_cf)
    if cf_missing:
        out["problems"].append(f"{len(cf_missing)} of {len(p['custom_fields'])} REL fields empty")
    if cf_wrong:
        out["problems"].append(f"{len(cf_wrong)} REL fields wrong value: "
                               + ", ".join(f"{k}={got_cf[k]!r} want {p['custom_fields'][k]!r}" for k in cf_wrong[:3]))
    # Which extras may be blanked: ONLY those whose live value is what --extra-source (the
    # other pusher's plan) wrote. Without provenance nothing beyond the plan is blankable:
    # a REL field from the IDI import / Verified Import / RFD is reported and KEPT.
    src_cf = ((opts.get("extra_source") or {}).get(key) or {}).get("custom_fields") or {}
    cf_extra_ours = [k for k in cf_extra if src_cf.get(k) and _cf_equal(k, src_cf[k], got_cf[k])]
    cf_extra_other = [k for k in cf_extra if k not in cf_extra_ours]
    if cf_extra_ours:
        out["problems"].append(f"{len(cf_extra_ours)} REL fields beyond plan hold values: "
                               + ", ".join(f"{k}={got_cf[k]!r}" for k in cf_extra_ours[:4]))
    if cf_extra_other:
        # informational: correct-and-untouchable must not fail the record
        out["notes"].append(f"{len(cf_extra_other)} REL fields beyond plan from another source (kept): "
                            + ", ".join(f"{k}={got_cf[k]!r}" for k in cf_extra_other[:4]))
    out["cf_missing"], out["cf_wrong"], out["cf_extra"] = cf_missing, cf_wrong, cf_extra
    out["cf_extra_ours"], out["cf_extra_other"] = cf_extra_ours, cf_extra_other
    # "Is this a REL plan at all?" -- the guard that keeps a non-DP plan from blanking DP
    # slots. It must also accept a plan that DROPPED every relative (owner-alive re-push,
    # custom_fields == {}) when the extra-source plan for the same key wrote REL fields:
    # that is the exact case the blank exists for. 2026-09-18 namesake repair: 7 of 9
    # provenanced ghosts were skipped because the corrected plan had no REL slot left.
    plan_has_rel = any(REL_RE.match(k) for k in p["custom_fields"]) or any(REL_RE.match(k) for k in src_cf)
    clear_extra = bool(opts.get("clear_extra_rel")) and plan_has_rel and bool(cf_extra_ours)
    if repair and (cf_missing or cf_wrong or clear_extra):
        if fields is None:
            fields = odb._field_index(api)
        blank = ""   # proven live 2026-09-10: "" via update-values empties the field
        body, missing_defs = [], []
        for lbl in cf_missing + cf_wrong:
            f = fields.get(lbl)
            if not f:
                missing_defs.append(lbl)
                continue
            body.append({"field_uuid": f["uuid"], "value": p["custom_fields"][lbl]})
            undo["cf_restore"].append({"field_uuid": f["uuid"], "label": lbl, "value": got_cf.get(lbl) or ""})
        blanked = []
        if clear_extra:
            for lbl in cf_extra_ours:
                f = fields.get(lbl)
                if not f:
                    missing_defs.append(lbl)
                    continue
                body.append({"field_uuid": f["uuid"], "value": blank})
                blanked.append(lbl)
                undo["cf_restore"].append({"field_uuid": f["uuid"], "label": lbl, "value": got_cf.get(lbl)})
        if missing_defs:
            out["problems"].append(f"no active field definition for {missing_defs}")
        if body:
            sc, resp = api.write("PATCH", f"/api/internal/property/{uuid}/custom-field/update-values/", body)
            out["repaired"].append(f"PATCHed {len(body)} custom fields ({sc}; {len(blanked)} blanked)")
            time.sleep(1.0)
            _rows2, got2 = _cf_rows(api, uuid)
            m2, w2, e2 = _cf_diff(p["custom_fields"], got2)
            out["cf_verified"] = not (m2 or w2) and not (clear_extra and any(l in e2 for l in blanked))
            out["blank_worked"] = all(not got2.get(lbl) for lbl in blanked) if blanked else None
            if m2 or w2:
                out["problems"].append(f"REPAIR FAILED: {len(m2)} REL fields still empty, {len(w2)} still wrong")
            if blanked and out["blank_worked"] is False:
                out["problems"].append("STOP: blanking did not take (" + ", ".join(
                    f"{lbl}={got2.get(lbl)!r}" for lbl in blanked if got2.get(lbl)) + ")")
                out["stop"] = True

    # ── property tags ──────────────────────────────────────────────────
    ptags = _tagnames(full.get("tags"))
    tag_missing = [t for t in p["tags"] if t not in ptags]
    if tag_missing:
        out["problems"].append(f"property tags missing: {tag_missing}")
    if repair and tag_missing:
        # Merge-only PATCH (read-modify-write of the full set), same as the pusher: the
        # probate/FTM/list tags already on the record are kept.
        current = [t.get("name") if isinstance(t, dict) else str(t) for t in (full.get("tags") or [])]
        sc, _ = api.write("PATCH", f"/api/internal/property/{uuid}/", {"tags": current + tag_missing})
        out["repaired"].append(f"tags merged {tag_missing} ({sc})")
        if sc in OK:
            undo["tags_added"] = tag_missing
        st_t, after_t = api.get(f"/api/internal/property/{uuid}/")
        still = [t for t in tag_missing if t not in _tagnames(after_t.get("tags"))]
        out["tags_verified"] = not still
        if still:
            out["problems"].append(f"REPAIR FAILED: property tags still missing: {still}")

    if repair and out["repaired"]:
        if backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            (backup_dir / f"{_safe(key)}.json").write_text(
                json.dumps(snapshot, indent=1, ensure_ascii=False), encoding="utf-8")
        if undo_fh is not None and (undo["cf_restore"] or undo["messages_posted"] or undo["tags_added"]):
            undo_fh.write(json.dumps(undo, ensure_ascii=False) + "\n")
            undo_fh.flush()
    return out


def run_undo(api, path: Path) -> int:
    """Replay an undo file: restore field values, delete the messages the repair posted,
    remove the tags it added. Read back each record afterwards."""
    bad = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        u = json.loads(line)
        uuid = u["uuid"]
        notes = []
        if u.get("cf_restore"):
            body = [{"field_uuid": r["field_uuid"], "value": r["value"] or ""} for r in u["cf_restore"]]
            sc, _ = api.write("PATCH", f"/api/internal/property/{uuid}/custom-field/update-values/", body)
            notes.append(f"fields restored ({sc}, {len(body)})")
        for mu in u.get("messages_posted") or []:
            sc, _ = api.write("DELETE", f"/api/internal/property/{uuid}/message/{mu}/", {})
            notes.append(f"message {mu[:8]} deleted ({sc})")
        if u.get("tags_added"):
            sc, _ = api.write("POST", f"/api/internal/property/{uuid}/remove-tags/", {"tags": u["tags_added"]})
            notes.append(f"tags removed ({sc})")
        time.sleep(0.5)
        _rows, got = _cf_rows(api, uuid)
        ok = all(_cf_equal(r["label"], r["value"] or "", got.get(r["label"]) or "") for r in u.get("cf_restore") or [])
        board = {m.get("uuid") for m in _board(api, uuid)}
        ok = ok and not (set(u.get("messages_posted") or []) & board)
        bad += 0 if ok else 1
        print(f"  {u['key']}: {'; '.join(notes)} -> {'ok' if ok else 'CHECK'}")
    return 0 if not bad else 1


def _load_keys(a) -> tuple[list[tuple[str, dict, str | None, Path]], list[Path]]:
    """-> [(key, plan_record, known_uuid, run_dir)], run dirs of the SELECTED items (the
    first hosts the outputs), so a one-record probe writes next to its own plan."""
    plans_cache: dict[Path, dict] = {}

    def plan_for(run: Path) -> dict:
        run = run if run.is_absolute() else (ROOT / run)
        if run not in plans_cache:
            with open(run / "push_plan.json", encoding="utf-8") as fh:
                plans_cache[run] = json.load(fh)["records"]
        return plans_cache[run]

    def abs_run(run: Path) -> Path:
        return run if run.is_absolute() else (ROOT / run)

    items: list[tuple[str, dict, str | None, Path]] = []
    if a.keys_file:
        with open(a.keys_file, encoding="utf-8") as fh:
            raw = json.load(fh)
        for it in raw:
            if isinstance(it, str):
                key, run, known = it, Path(a.run_dir), None
            else:
                key, run, known = it["key"], Path(it.get("dp_batch") or a.run_dir), it.get("uuid")
            plan = plan_for(run)
            if key not in plan:
                print(f"  ! {key} not in {run}/push_plan.json -- skipped")
                continue
            items.append((key, plan[key], known, abs_run(run)))
    else:
        run = Path(a.run_dir)
        plan = plan_for(run)
        items = [(k, plan[k], None, abs_run(run)) for k in plan]
    if a.key:
        items = [it for it in items if it[0] == a.key]
    if a.limit:
        items = items[: a.limit]
    runs = list(dict.fromkeys(it[3] for it in items))
    return items, runs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=str(odb.DEFAULT_RUN_DIR))
    ap.add_argument("--keys-file", help="JSON list of keys or of {key, dp_batch, uuid} rows (several run dirs)")
    ap.add_argument("--repair", action="store_true", help="fix what the audit found")
    ap.add_argument("--clear-extra-rel", action="store_true",
                    help="with --repair: blank live REL fields the plan does not mention")
    ap.add_argument("--extra-source", help="push_plan.json of the OTHER pusher: REQUIRED with "
                    "--clear-extra-rel; only REL fields whose live value is what that plan wrote are blanked")
    ap.add_argument("--out-dir", help="where reports/backups/undo land (default: first run dir)")
    ap.add_argument("--undo", help="replay a qa_undo_<stamp>.jsonl and exit")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--key")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    if a.clear_extra_rel and not a.extra_source:
        ap.error("--clear-extra-rel needs --extra-source: nothing beyond the plan is blanked without provenance")
    api = odb.WriteApi()
    if a.undo:
        return run_undo(api, Path(a.undo))

    items, runs = _load_keys(a)
    if not items:
        print("nothing to audit: no keys selected")
        return 1
    out_dir = Path(a.out_dir) if a.out_dir else runs[0]
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_dir = out_dir / f"qa_backup_{stamp}" if a.repair else None
    undo_fh = open(out_dir / f"qa_undo_{stamp}.jsonl", "a", encoding="utf-8") if a.repair else None
    fields = odb._field_index(api) if a.repair else None
    extra_source = None
    if a.extra_source:
        with open(a.extra_source, encoding="utf-8") as fh:
            extra_source = json.load(fh)["records"]
    opts = {"clear_extra_rel": a.clear_extra_rel, "extra_source": extra_source}

    results, tally = [], Counter()
    stop = False
    t0 = time.time()
    for i, (k, p, known, _run) in enumerate(items, 1):
        try:
            r = audit_one(api, k, p, repair=a.repair, known_uuid=known, fields=fields, opts=opts,
                          backup_dir=backup_dir, undo_fh=undo_fh)
        except Exception as e:  # noqa: BLE001
            r = {"key": k, "street": p["street"], "problems": [f"EXCEPTION {type(e).__name__}: {e}"]}
        results.append(r)
        for pr in r["problems"]:
            tally[_bucket(pr)] += 1
        if not r["problems"]:
            tally["clean"] += 1
        if a.verbose or r["problems"]:
            print(f"  [{i}/{len(items)}] {r['street']}: "
                  + ("; ".join(r["problems"]) if r["problems"] else "clean")
                  + (f"  -> repaired: {', '.join(r['repaired'])}" if r.get("repaired") else "")
                  + (f"  [note: {'; '.join(r['notes'])}]" if r.get("notes") else ""), flush=True)
        if r.get("stop"):
            stop = True
            print("  STOP: blanking did not take on the record above; not continuing.", flush=True)
            break
        if i % 25 == 0:
            el = time.time() - t0
            print(f"  ... {i}/{len(items)}  ({el:.0f}s, {el / i:.1f}s/rec, "
                  f"clean {tally['clean']})", flush=True)
    if undo_fh:
        undo_fh.close()
        undo_path = out_dir / f"qa_undo_{stamp}.jsonl"
        if undo_path.stat().st_size == 0:
            undo_path.unlink()

    out = out_dir / f"qa_{'repair' if a.repair else 'audit'}_{stamp}.json"
    json.dump({"generated_at": datetime.now().isoformat(timespec="seconds"),
               "repair": a.repair, "records": len(items), "keys_file": a.keys_file,
               "tally": dict(tally), "results": results},
              open(out, "w", encoding="utf-8"), indent=1)
    print(f"\n{len(results)} of {len(items)} records audited{' + repaired' if a.repair else ''}")
    for kk, vv in tally.most_common():
        print(f"  {kk:44} {vv}")
    print(f"report: {out}")
    if a.repair:
        print(f"backup: {backup_dir} (written only for repaired records)\n"
              f"undo:   {out_dir / f'qa_undo_{stamp}.jsonl'} (absent if nothing was written)")
    if stop:
        return 2
    return 0 if tally["clean"] == len(items) else 1


def _bucket(pr: str) -> str:
    for frag in ("no DP message on the board", "NOT pinned", "phones missing",
                 "wrong Rel tag", "no Trestle tier tag", "REL fields empty",
                 "REL fields wrong value", "beyond plan", "property tags missing",
                 "uuid mismatch", "unresolved", "STOP", "REPAIR FAILED", "REPAIR SIDE-EFFECT",
                 "no active field definition", "EXCEPTION"):
        if frag in pr:
            return frag
    return pr[:40]


if __name__ == "__main__":
    sys.exit(main())
