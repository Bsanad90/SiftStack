"""The Obituary lane (Basem, 2026-09-30): its own CALL + MAIL preset folders, and the
obituary records moved off `FTM` / `Priority 1` onto the `Obituaries` tag.

    python src/scripts/obituary_lane_build.py presets --plan
    python src/scripts/obituary_lane_build.py presets --commit          # folders + 12 presets, read back
    python src/scripts/obituary_lane_build.py presets --undo <backup>   # DELETE what that commit created

    python src/scripts/obituary_lane_build.py retag --plan              # -> output/obituary_lane_build/<stamp>/plan.csv
    python src/scripts/obituary_lane_build.py retag --commit --run-dir <dir> [--limit N] [--probe <uuid>]

Why a lane of its own: every Call / Mail folder keys on `FTM`, `Priority 1`, `Priority 2` or
`Tier 2` (preset_spec.ENTRY); with FTM and Priority 1 gone from obituary records, no dialer
could see them. The two new folders are byte-for-byte clones of `05 FTM - CALL` /
`06 FTM - MAIL` (same stages, counties, statuses, `Low Equity` + `Negative Equity`
exclusion, Mail Only / Entity Owner exclusion on CALL, the obituary window, one-per-owner)
with ONE change: the entry block is `must.any_tags = [Obituaries, Deceased Owner]` (OR)
instead of `must.all_tags = [FTM]`. Both tags exist on the account; nothing is created.

The retag, on every record on the `Obituary` LIST (5,675 on 2026-09-30):
  * add `Obituaries` where missing        -> PATCH /property/{uuid}/ {"tags": [...]} (merge-only)
  * remove `Priority 1`                   -> POST /property/{uuid}/remove-tags/
  * remove `FTM` UNLESS the record is also a probate case (Basem: "keep FTM on probates"):
    on the Probate / Register of Wills Probates / Pre-Probate/Deceased list, or carrying a
    probate-ish tag (probate / register of wills / clerk +), or holding a PR (built-in
    `personal_representative`, `probate_open_date`, or the `PR First|Last Name` fields).
Per-record routes only (the collection-level remove-tags route is ACCOUNT-WIDE, trap 1),
one record at a time, read back after every write, progress banked per record so
`--commit --run-dir <dir>` resumes.

Store facts this rests on (read live 2026-09-30): preset folder create is
`POST /api/internal/filter-preset-folder/ {"title","type":"properties","permissions":[]}`,
preset create `POST /api/internal/filter-preset/ {"title","folder","quick_filter","filters","type"}`
(the account-blueprint skill's proven path); titles are account-unique and stored TRUNCATED
(~24-26 chars), so matching is by prefix; the thin search row carries NO tags/lists, so
membership comes from server-side sets (count + page) and per-record detail reads.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dpd.preset_spec import ENTRY, PRESETS  # noqa: E402
from dpd_lane_assignee_report import count  # noqa: E402
from obituary_dp_batch import WriteApi  # noqa: E402

OUT_ROOT = ROOT / "output" / "obituary_lane_build"
PAGE = 200
SAFE_LIMIT = 9000

SRC_FOLDERS = {"13 OBITUARY - CALL": "05 FTM - CALL", "14 OBITUARY - MAIL": "06 FTM - MAIL"}
SRC_LABEL, NEW_LABEL = "FTM - ", "Obituary - "
ENTRY_TAGS = ENTRY["OBITUARY"]["any"]          # ["Obituaries", "Deceased Owner"]
OBIT_LIST = "Obituary"
OBIT_TAG = "Obituaries"
DROP_TAGS = ["FTM", "Priority 1"]
PROBATE_LISTS = ["Probate", "Register of Wills Probates", "Pre-Probate/Deceased"]
PROBATE_TAG_RE = re.compile(r"probate|register of wills|clerk \+", re.I)
PR_FIELDS = ("PR First Name", "PR Last Name", "Personal Representative")


class Api(WriteApi):
    """`dpd_lane_assignee_report.count` calls `post_as_get`; WriteApi names it `search`."""

    def post_as_get(self, path, body):
        return self.api.post_as_get(path, body)


def say(msg: str) -> None:
    print(msg, flush=True)


def jsave(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1), encoding="utf-8")


def jload(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def prefix_match(a: str, b: str) -> bool:
    a, b = a.strip(), (b or "").strip()
    return a == b or a.startswith(b) or b.startswith(a)


# ------------------------------------------------------------------ vocabulary
def listing(api: WriteApi, path: str) -> list[dict]:
    out, off = [], 0
    while True:
        st, r = api.get(f"{path}&offset={off}")
        if st != 200 or not isinstance(r, dict):
            raise RuntimeError(f"listing {path}: {st} {str(r)[:200]}")
        rows = r.get("results") or []
        out += rows
        off += len(rows)
        if not r.get("next") or not rows:
            break
    return out


def vocab(api: WriteApi) -> tuple[dict, dict]:
    tags = {t["title"].strip(): t["uuid"] for t in listing(api, "/api/internal/tag/?limit=999")}
    lists = {t["title"].strip(): t["uuid"] for t in listing(api, "/api/internal/list/?limit=999")}
    for t in ENTRY_TAGS + DROP_TAGS + [OBIT_TAG]:
        if t not in tags:
            raise RuntimeError(f"tag {t!r} does not exist on the account -- refusing (nothing is created)")
    for lst in [OBIT_LIST] + PROBATE_LISTS:
        if lst not in lists:
            raise RuntimeError(f"list {lst!r} does not exist on the account")
    return tags, lists


# ------------------------------------------------------------------ presets
def folder_map(api: WriteApi) -> dict[str, dict]:
    st, r = api.get("/api/internal/filter-preset-folder/?limit=999&type=properties")
    return {f["title"]: f for f in r.get("results") or []}


def folder_presets(api: WriteApi, folder_uuid: str) -> list[dict]:
    st, r = api.get(f"/api/internal/filter-preset-folder/{folder_uuid}/filter-preset/"
                    "?limit=999&ordering=title&type=properties")
    return r.get("results") or []


def build_preset_bodies(api: WriteApi, tags: dict) -> list[dict]:
    """One body per new preset, cloned from the FTM twin's STORED filters."""
    folders = folder_map(api)
    entry_uuids = [tags[t] for t in ENTRY_TAGS]
    ftm_uuid = tags["FTM"]
    bodies = []
    for new_folder, src_folder in SRC_FOLDERS.items():
        src = folders.get(src_folder)
        if not src:
            raise RuntimeError(f"source folder {src_folder!r} missing")
        src_rows = folder_presets(api, src["uuid"])
        for spec in [p for p in PRESETS if p["folder"] == new_folder]:
            stage = spec["name"][len(NEW_LABEL):]
            twin = next((r for r in src_rows if prefix_match(SRC_LABEL + stage, r["title"])), None)
            if twin is None:
                raise RuntimeError(f"no FTM twin for {spec['name']!r} in {src_folder}")
            st, d = api.get(f"/api/internal/filter-preset/{twin['uuid']}/")
            filters = copy.deepcopy(d["filters"])
            must = filters["must"]
            if must.get("all_tags") != [ftm_uuid] or must.get("any_tags"):
                raise RuntimeError(f"{twin['title']}: entry block is not all_tags=[FTM] "
                                   f"({must.get('all_tags')} / {must.get('any_tags')}); not cloning blind")
            must.pop("all_tags")
            must["any_tags"] = list(entry_uuids)
            bodies.append({"title": spec["name"], "folder_title": new_folder,
                           "quick_filter": bool(d.get("quick_filter")), "filters": filters,
                           "type": "properties", "_twin": twin["title"], "_twin_uuid": twin["uuid"]})
    return bodies


def cmd_presets(a) -> int:
    api = Api()
    tags, _ = vocab(api)
    if a.undo:
        bk = jload(Path(a.undo), None)
        if not bk:
            raise SystemExit(f"no backup at {a.undo}")
        for p in bk.get("presets", []):
            st, r = api.write("DELETE", f"/api/internal/filter-preset/{p['uuid']}/", {})
            say(f"  DELETE preset {p['title']!r}: {st}")
        for f in bk.get("folders", []):
            st, r = api.write("DELETE", f"/api/internal/filter-preset-folder/{f['uuid']}/", {})
            say(f"  DELETE folder {f['title']!r}: {st}")
        return 0

    bodies = build_preset_bodies(api, tags)
    say(f"{len(bodies)} preset bodies built from their FTM twins; entry any_tags = {ENTRY_TAGS}")
    for b in bodies:
        m = b["filters"]["must"]
        say(f"  {b['title']:40} <- {b['_twin']:28} keys={sorted(k for k in m if k != 'any_county')}")
    if not a.commit:
        say("plan only (--commit to create)")
        return 0

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = OUT_ROOT / f"presets_created_{stamp}.json"
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    created = {"folders": [], "presets": []}
    folders = folder_map(api)
    for new_folder in SRC_FOLDERS:
        if new_folder in folders:
            say(f"folder {new_folder!r} exists ({folders[new_folder]['uuid']})")
            continue
        st, r = api.write("POST", "/api/internal/filter-preset-folder/",
                          {"title": new_folder, "type": "properties", "permissions": []})
        if st not in (200, 201):
            raise SystemExit(f"folder create {new_folder!r}: {st} {str(r)[:300]}")
        folders = folder_map(api)
        if new_folder not in folders:
            raise SystemExit(f"folder {new_folder!r} POST {st} but not in the read-back listing")
        created["folders"].append({"title": new_folder, "uuid": folders[new_folder]["uuid"]})
        jsave(backup, created)
        say(f"folder {new_folder!r} created {folders[new_folder]['uuid']}")

    failed = 0
    for b in bodies:
        fol = folders[b["folder_title"]]
        existing = [r for r in folder_presets(api, fol["uuid"]) if prefix_match(b["title"], r["title"])]
        if existing:
            say(f"  exists  {b['title']!r} ({existing[0]['uuid']})")
            continue
        body = {"title": b["title"], "folder": fol["uuid"], "quick_filter": b["quick_filter"],
                "filters": b["filters"], "type": "properties"}
        st, r = api.write("POST", "/api/internal/filter-preset/", body)
        if st not in (200, 201) or not isinstance(r, dict) or not r.get("uuid"):
            say(f"  FAILED  {b['title']!r}: {st} {str(r)[:300]}")
            failed += 1
            continue
        st2, got = api.get(f"/api/internal/filter-preset/{r['uuid']}/")
        ok = (got.get("filters") or {}).get("must") == body["filters"]["must"] \
            and got.get("folder") == fol["uuid"]
        created["presets"].append({"title": b["title"], "uuid": r["uuid"], "folder": fol["uuid"],
                                   "twin": b["_twin_uuid"], "readback_ok": ok})
        jsave(backup, created)
        say(f"  {'created' if ok else 'MISMATCH'} {b['title']!r} {r['uuid']}")
        if not ok:
            failed += 1
    say(f"backup {backup}")
    say(f"done: {len(created['presets'])} presets, {failed} failed")
    return 1 if failed else 0


# ------------------------------------------------------------------ retag
def page_set(api: WriteApi, must: dict, expected: int) -> set[str]:
    if expected > SAFE_LIMIT:
        raise RuntimeError(f"set of {expected} exceeds the offset ceiling; bucket it first")
    out: set[str] = set()
    offset = 0
    while offset < expected:
        st, r = api.search({"limit": PAGE, "offset": offset, "ordering": "created", "query": {"must": must}})
        if st != 200:
            raise RuntimeError(f"page failed at offset {offset}: {st} {str(r)[:300]}")
        got = r.get("results") or []
        if not got:
            break
        out |= {row["uuid"] for row in got}
        offset += PAGE
    if abs(len(out) - expected) > max(3, expected // 200):
        raise RuntimeError(f"paged {len(out)} vs count {expected}")
    return out


def named_set(api: WriteApi, name: str, must: dict) -> set[str]:
    n = count(api, {"must": must})
    s = page_set(api, must, n) if n else set()
    say(f"  set {name:44} {len(s):>6}")
    return s


def custom_fields(api: WriteApi, uuid: str) -> dict[str, str]:
    st, raw = api.get(f"/api/internal/property/{uuid}/custom-field/")
    rows = raw if isinstance(raw, list) else ((raw.get("results") or []) if isinstance(raw, dict) else [])
    return {((row.get("custom_field") or {}).get("label") or ""): (row.get("value") or "").strip()
            for row in rows if (row.get("value") or "").strip()}


def probate_evidence(api: WriteApi, uuid: str) -> str:
    """The reasons to KEEP FTM that only a detail read can see."""
    st, d = api.get(f"/api/internal/property/{uuid}/")
    if st != 200:
        return f"detail {st}"
    for k in ("personal_representative", "probate_open_date"):
        if d.get(k):
            return f"{k}={d[k]}"
    hits = [t for t in (d.get("tags") or []) if PROBATE_TAG_RE.search(t)]
    if hits:
        return "tag " + hits[0]
    hits = [x for x in (d.get("lists") or []) if x in PROBATE_LISTS]
    if hits:
        return "list " + hits[0]
    cf = custom_fields(api, uuid)
    pr = [f for f in PR_FIELDS if cf.get(f)]
    if pr:
        return f"field {pr[0]}={cf[pr[0]]}"
    return ""


def cmd_retag_plan(a) -> int:
    api = Api()
    tags, lists = vocab(api)
    run = OUT_ROOT / datetime.now().strftime("%Y%m%dT%H%M%S")
    run.mkdir(parents=True, exist_ok=True)
    O = lists[OBIT_LIST]
    say("server-side sets")
    obit = named_set(api, "on Obituary list", {"any_lists": [O]})
    has_tag = named_set(api, "  + Obituaries tag", {"any_lists": [O], "all_tags": [tags[OBIT_TAG]]})
    ftm = named_set(api, "  + FTM", {"any_lists": [O], "all_tags": [tags["FTM"]]})
    p1 = named_set(api, "  + Priority 1", {"any_lists": [O], "all_tags": [tags["Priority 1"]]})
    prob_lists: set[str] = set()
    for lst in PROBATE_LISTS:
        prob_lists |= named_set(api, f"  + list {lst}", {"all_lists": [O, lists[lst]]})
    prob_tag_uuids = [u for t, u in tags.items() if PROBATE_TAG_RE.search(t)]
    say(f"  {len(prob_tag_uuids)} probate-ish tags on the account")
    prob_tags = named_set(api, "  + a probate-ish tag", {"any_lists": [O], "any_tags": prob_tag_uuids})

    need_detail = sorted((obit & ftm) - prob_lists - prob_tags)
    say(f"detail reads for PR evidence on {len(need_detail)} FTM records with no list/tag evidence")
    cache_p = run / "pr_evidence.json"
    evidence = jload(cache_p, {})
    for i, u in enumerate(need_detail, 1):
        if u in evidence:
            continue
        evidence[u] = probate_evidence(api, u)
        if i % 25 == 0:
            jsave(cache_p, evidence)
            say(f"  {i}/{len(need_detail)} kept so far: {sum(1 for v in evidence.values() if v)}")
    jsave(cache_p, evidence)

    rows = []
    for u in sorted(obit):
        keep = ""
        if u in prob_lists:
            keep = "probate list"
        elif u in prob_tags:
            keep = "probate tag"
        elif evidence.get(u):
            keep = evidence[u]
        rows.append({"uuid": u,
                     "add_obituaries": int(u not in has_tag),
                     "remove_priority_1": int(u in p1),
                     "remove_ftm": int(u in ftm and not keep),
                     "ftm_kept": keep if u in ftm else ""})
    with (run / "plan.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    jsave(run / "plan.json", rows)
    s = {k: sum(r[k] for r in rows) for k in ("add_obituaries", "remove_priority_1", "remove_ftm")}
    s["ftm_kept"] = sum(1 for r in rows if r["ftm_kept"])
    s["records_touched"] = sum(1 for r in rows if r["add_obituaries"] or r["remove_priority_1"] or r["remove_ftm"])
    s["records_on_list"] = len(rows)
    jsave(run / "summary.json", s)
    say(json.dumps(s, indent=1))
    say(f"plan written to {run}")
    return 0


def cmd_retag_commit(a) -> int:
    run = Path(a.run_dir)
    rows = jload(run / "plan.json", None)
    if not rows:
        raise SystemExit("--run-dir must hold plan.json from `retag --plan`")
    api = Api()
    vocab(api)
    log_p = run / "commit_log.jsonl"
    done = set()
    if log_p.exists():
        for line in log_p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("ok"):
                    done.add(rec["uuid"])
    todo = [r for r in rows if (r["add_obituaries"] or r["remove_priority_1"] or r["remove_ftm"])
            and r["uuid"] not in done]
    if a.probe:
        todo = [r for r in todo if r["uuid"] == a.probe]
    if a.limit:
        todo = todo[:a.limit]
    say(f"{len(todo)} records to write ({len(done)} already done)")
    fails = 0
    with log_p.open("a", encoding="utf-8") as lg:
        for i, r in enumerate(todo, 1):
            u = r["uuid"]
            rec = {"uuid": u, "steps": {}, "ok": False}
            if r["add_obituaries"]:
                st, resp = api.write("PATCH", f"/api/internal/property/{u}/", {"tags": [OBIT_TAG]})
                rec["steps"]["add"] = st
            drop = [t for t, f in (("FTM", "remove_ftm"), ("Priority 1", "remove_priority_1")) if r[f]]
            if drop:
                st, resp = api.write("POST", f"/api/internal/property/{u}/remove-tags/", {"tags": drop})
                rec["steps"]["remove"] = st
                rec["steps"]["removed_tags"] = (resp.get("removed_tags") if isinstance(resp, dict) else None)
            st, d = api.get(f"/api/internal/property/{u}/")
            live = set(d.get("tags") or []) if st == 200 else None
            if live is None:
                rec["error"] = f"read-back {st}"
            else:
                want_in = {OBIT_TAG} if r["add_obituaries"] else set()
                rec["ok"] = want_in <= live and not (set(drop) & live)
                if not rec["ok"]:
                    rec["error"] = f"live tags {sorted(t for t in live if t in {OBIT_TAG, *drop})}"
                rec["ftm_still"] = "FTM" in live
            lg.write(json.dumps(rec) + "\n")
            lg.flush()
            if not rec["ok"]:
                fails += 1
                say(f"  FAIL {u} {rec.get('error')} {rec['steps']}")
            if i % 50 == 0 or i == len(todo):
                say(f"  {i}/{len(todo)} written, {fails} failed")
    say(f"done: {len(todo) - fails} ok, {fails} failed; log {log_p}")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("presets")
    p.add_argument("--plan", action="store_true")
    p.add_argument("--commit", action="store_true")
    p.add_argument("--undo", metavar="BACKUP_JSON")
    r = sub.add_parser("retag")
    r.add_argument("--plan", action="store_true")
    r.add_argument("--commit", action="store_true")
    r.add_argument("--run-dir")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--probe", help="one uuid from the plan")
    a = ap.parse_args()
    if a.cmd == "presets":
        return cmd_presets(a)
    if a.commit:
        return cmd_retag_commit(a)
    return cmd_retag_plan(a)


if __name__ == "__main__":
    raise SystemExit(main())
