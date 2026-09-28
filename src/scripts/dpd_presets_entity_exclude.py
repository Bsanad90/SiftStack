"""Exclude `Entity Owner` records from every CALL preset (Basem 2026-09-28: "tag them out of
the call lanes").

The spec now carries `SUPPRESS_TAGS_CALL_ONLY = ["Entity Owner"]` (preset_spec), so each of the
33 CALL presets' `tags_none` is at least ["Mail Only", "Entity Owner"] -- a MULTI-value
exclusion, which the store must hold under `must_not.any_tags` whatever block built it
(measured 2026-08-27: must_not.all_tags means "carrying ALL of them" and removes nothing).
This script:
  1. resolves the tag uuids from GET /api/internal/tag/ (the tag must already exist -- it is
     created by the first record PATCH that names it, output/dpd_pull_20260928/tag_entities.py),
  2. for every CALL preset: takes the stored must_not tag uuids (any_tags + all_tags), adds the
     wanted set, writes them ALL under must_not.any_tags and drops must_not.all_tags, leaving
     every other key untouched,
  3. PATCHes and reads the store back; `--probe` does one, `--commit` the rest, `--undo`
     restores from the backup written before the first write.
Run `python src/scripts/dpd_presets_verify.py` afterwards (it derives the wanted groups from
the spec).
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dpd.preset_spec import PRESETS  # noqa: E402
from dpd_presets_narrow import list_presets  # noqa: E402
from dpd_presets_verify import prefix_match  # noqa: E402
from obituary_dp_batch import WriteApi  # noqa: E402


def tag_uuids(api: WriteApi) -> dict[str, str]:
    st, r = api.get("/api/internal/tag/?limit=999")
    rows = r if isinstance(r, list) else (r.get("results") or [])
    return {t["title"]: t["uuid"] for t in rows}


def target_filters(p: dict, want_uuids: list[str]) -> dict | None:
    """New filters dict, or None when the store already holds the wanted set."""
    f = copy.deepcopy(p.get("filters") or {})
    must = f.setdefault("must", {})
    mn = must.setdefault("must_not", {})
    have = list(mn.get("any_tags") or []) + list(mn.get("all_tags") or [])
    merged = list(dict.fromkeys(have + want_uuids))
    if mn.get("any_tags") == merged and not mn.get("all_tags"):
        return None
    mn["any_tags"] = merged
    mn.pop("all_tags", None)
    return f


def patch_one(api: WriteApi, p: dict, new_filters: dict) -> tuple[bool, str]:
    paths = [f"/api/internal/filter-preset/{p['uuid']}/",
             f"/api/internal/filter-preset-folder/{p['_folder_uuid']}/filter-preset/{p['uuid']}/"]
    last = ""
    for path in paths:
        st, _ = api.write("PATCH", path, {"filters": new_filters})
        last = f"{path} -> {st}"
        if st == 200:
            break
    else:
        return False, last
    st, back = api.get(f"/api/internal/filter-preset/{p['uuid']}/")
    got = (back.get("filters") or {}) if st == 200 else {}
    if got != new_filters:
        return False, f"read-back differs: {json.dumps(got.get('must', {}).get('must_not'))[:200]}"
    return True, "OK"


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--probe", action="store_true", help="patch ONE preset and stop")
    g.add_argument("--commit", action="store_true", help="patch every remaining preset")
    g.add_argument("--undo", metavar="BACKUP_JSON", help="restore filters from a backup")
    a = ap.parse_args()
    api = WriteApi()
    if a.undo:
        recs = json.loads(Path(a.undo).read_text(encoding="utf-8"))
        done = 0
        for r in recs:
            st, _ = api.write("PATCH", f"/api/internal/filter-preset/{r['uuid']}/", {"filters": r["filters"]})
            done += (st == 200)
            print(f"  restore {r['name']!r}: {st}")
        print(f"restored {done} of {len(recs)}")
        return 0

    uid = tag_uuids(api)
    stored = list_presets(api)
    todo, have, missing = [], [], []
    for s in PRESETS:
        if s["channel"] != "call":
            continue
        want_names = [t for t in s["tags_none"]]
        want_uuids = []
        for n in want_names:
            if n not in uid:
                raise SystemExit(f"tag {n!r} does not exist on the account -- create it (tag a record) first")
            want_uuids.append(uid[n])
        hits = [p for p in stored if p.get("_folder") == s["folder"] and prefix_match(p.get("title") or "", s["name"])]
        if len(hits) != 1:
            missing.append(f"{s['folder']} / {s['name']} ({len(hits)} matches)")
            continue
        p = hits[0]
        nf = target_filters(p, want_uuids)
        (have if nf is None else todo).append((s, p, nf))
    print(f"CALL presets: {len(have)} already hold the exclusion, {len(todo)} to patch, {len(missing)} unmatched")
    for m in missing:
        print("  MISSING", m)
    if missing:
        return 2
    if not (a.probe or a.commit):
        for s, p, nf in todo:
            print(f"  would patch {s['folder']:22} {s['name']:30} must_not.any_tags -> {len(nf['must']['must_not']['any_tags'])} uuids")
        return 0
    if todo:
        bk = ROOT / "output" / f"dpd_presets_entity_exclude_backup_{datetime.now():%Y%m%dT%H%M%S}.json"
        bk.write_text(json.dumps([{"uuid": p["uuid"], "name": p.get("title"), "filters": p.get("filters")}
                                  for _, p, _ in todo], indent=1), encoding="utf-8")
        print(f"prior filters -> {bk}")
    n_ok = 0
    for i, (s, p, nf) in enumerate(todo, 1):
        ok, msg = patch_one(api, p, nf)
        n_ok += ok
        print(f"  [{i}/{len(todo)}] {s['folder']:22} {s['name']:30} {'OK' if ok else 'FAILED ' + msg}")
        if not ok or a.probe:
            break
    print(f"patched {n_ok}/{1 if a.probe else len(todo)}")
    return 0 if n_ok == (1 if a.probe else len(todo)) else 1


if __name__ == "__main__":
    sys.exit(main())
