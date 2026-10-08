"""Bring every Records preset's TAG EXCLUSIONS up to the spec (Basem 2026-10-06: "make sure
`Recently Sold` is excluded from all calling lanes, and mailing lanes").

Generalises dpd_presets_entity_exclude.py (CALL presets only) to ALL 85 presets and to
whatever `preset_spec` says each one must exclude: `SUPPRESS_TAGS_ALL` (`Recently Sold`,
every channel) + `SUPPRESS_TAGS_CALL` (`Mail Only`, call + deep) + `SUPPRESS_TAGS_CALL_ONLY`
(`Entity Owner`, call) + the entry scope's own `none` list (Tier 2 and Bulk).

Per preset:
  1. the wanted names come from `dpd_presets_create.allocate_tags`, so the tag-block budget
     is honoured: a preset with TWO include groups (Deep 04 Return Mail, Rehash 05 Rehash
     Ready) has no block left for an exclusion and is reported as a budget skip, never
     patched -- those two stay on status suppression alone (block_map.TAG_BUDGET_TRIMS);
  2. the tag uuids are resolved from GET /api/internal/tag/ (a missing tag aborts: it is
     created by the first record PATCH that names it, never here);
  3. the stored must_not tag uuids (any_tags + all_tags) are merged with the wanted set and
     written ALL under `must_not.any_tags`, `must_not.all_tags` dropped -- a multi-value
     exclusion must live under any_tags (measured 2026-08-27: must_not.all_tags means
     "carrying ALL of them" and removes nothing); a single value means the same under either
     key. Every other key of the stored filters is left untouched;
  4. PATCH + store read-back; `--probe` does one preset, `--commit` the rest, `--undo
     <backup>` restores the filters written to the backup before the first write.

Run `python src/scripts/dpd_presets_verify.py` afterwards; it derives the wanted set from the
same spec.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dpd.preset_spec import PRESETS  # noqa: E402
from dpd_presets_create import allocate_tags  # noqa: E402
from dpd_presets_entity_exclude import patch_one, tag_uuids, target_filters  # noqa: E402
from dpd_presets_narrow import list_presets  # noqa: E402
from dpd_presets_verify import prefix_match  # noqa: E402
from obituary_dp_batch import WriteApi  # noqa: E402


def wanted_exclusions(spec: dict) -> tuple[list[str], list[str]]:
    """(names the store must exclude, names the tag-block budget drops) for one preset."""
    plan, dropped = allocate_tags(spec["tags_any"], spec.get("tags_any_2") or [],
                                  spec["tags_none"])
    want: list[str] = []
    for _block, is_exclude, values in plan:
        if is_exclude:
            want += [v for v in values if v not in want]
    return want, list(dropped)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--probe", action="store_true", help="patch ONE preset and stop")
    g.add_argument("--commit", action="store_true", help="patch every remaining preset")
    g.add_argument("--undo", metavar="BACKUP_JSON", help="restore filters from a backup")
    ap.add_argument("--only-tag", default=None,
                    help="report / patch only presets whose wanted set names this tag "
                         "(the full wanted set is still written)")
    a = ap.parse_args()
    api = WriteApi()
    if a.undo:
        recs = json.loads(Path(a.undo).read_text(encoding="utf-8"))
        done = 0
        for r in recs:
            st, _ = api.write("PATCH", f"/api/internal/filter-preset/{r['uuid']}/",
                              {"filters": r["filters"]})
            done += (st == 200)
            print(f"  restore {r['name']!r}: {st}")
        print(f"restored {done} of {len(recs)}")
        return 0 if done == len(recs) else 1

    uid = tag_uuids(api)
    stored = list_presets(api)
    todo, have, missing, budget = [], [], [], []
    for s in PRESETS:
        want_names, dropped = wanted_exclusions(s)
        if dropped:
            budget.append(f"{s['folder']} / {s['name']}: budget drops {dropped}")
        if a.only_tag and a.only_tag not in want_names:
            continue
        if not want_names:
            continue
        want_uuids = []
        for n in want_names:
            if n not in uid:
                raise SystemExit(f"tag {n!r} does not exist on the account -- create it "
                                 "(tag a record) first")
            want_uuids.append(uid[n])
        hits = [p for p in stored
                if p.get("_folder") == s["folder"] and prefix_match(p.get("title") or "", s["name"])]
        if len(hits) != 1:
            missing.append(f"{s['folder']} / {s['name']} ({len(hits)} matches)")
            continue
        p = hits[0]
        nf = target_filters(p, want_uuids)
        (have if nf is None else todo).append((s, p, nf, want_names))
    print(f"presets: {len(have)} already hold their exclusion set, {len(todo)} to patch, "
          f"{len(missing)} unmatched, {len(budget)} budget skips")
    for b in budget:
        print("  BUDGET", b)
    for m in missing:
        print("  MISSING", m)
    if missing:
        return 2
    if not (a.probe or a.commit):
        for s, p, nf, names in todo:
            print(f"  would patch {s['folder']:22} {s['name']:40} must_not.any_tags -> "
                  f"{len(nf['must']['must_not']['any_tags'])} uuids {names}")
        return 0
    if todo:
        bk = ROOT / "output" / f"dpd_presets_tag_exclude_backup_{datetime.now():%Y%m%dT%H%M%S}.json"
        bk.write_text(json.dumps([{"uuid": p["uuid"], "name": p.get("title"), "filters": p.get("filters")}
                                  for _, p, _, _ in todo], indent=1), encoding="utf-8")
        print(f"prior filters -> {bk}")
    n_ok = 0
    for i, (s, p, nf, names) in enumerate(todo, 1):
        ok, msg = patch_one(api, p, nf)
        n_ok += ok
        print(f"  [{i}/{len(todo)}] {s['folder']:22} {s['name']:40} "
              f"{'OK' if ok else 'FAILED ' + msg}")
        if not ok or a.probe:
            break
    n_want = 1 if a.probe else len(todo)
    print(f"patched {n_ok}/{n_want}")
    return 0 if n_ok == n_want else 1


if __name__ == "__main__":
    sys.exit(main())
