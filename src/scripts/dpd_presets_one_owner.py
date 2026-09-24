"""Add "Show one property per owner" to every DPD dial-list preset.

Basem, 2026-09-24, after reading the DataSift course: the Ready-to-Call preset must
carry the Owner -- Properties Owned block so click-to-dial never reaches the same owner
several times a day through their other properties (the course's reason: that owner
reports the number as spam). He asked for it on the rest of the call presets too,
"if appropriate based on the modules". The course puts the block on Ready to Call
and on the obituary deep-prospecting preset; the Call 2..7 stages differ from Ready
to Call by the attempt counter only, so they inherit it. Skip-trace queues (00/01),
mail, the other deep-prospecting queues and reactivation are NOT dial lists and are
left alone. The target set is whatever `dpd.preset_spec` marks `one_owner`.

WHAT THE BLOCK MEANS, read out of the app bundle (main.min.js, 2026-09-24): the panel
block "Owner — Properties Owned" is a THREE-field object
    {properties_owned: [min, max], show_properties: 'one_per_owner' | 'all',
     highest_criteria: '' | 'ai_score' | 'list_count' | 'updated' | 'uploaded' | 'created'}
whose default is `show_properties: 'one_per_owner'` with an empty range. "One property
per owner" is therefore the COLLAPSE mode -- a multi-property owner shows as ONE row --
not a range of 1..1, which would EXCLUDE that owner from the lane altogether. Measured
on FTM - 02 Ready to Call: base 315, collapse 303 (51 multi-owner rows -> 39 owners),
range 1..1 264.

HOW IT IS STORED vs QUERIED (same bundle, `toPropertiesFilters`): a saved preset keeps
the object NESTED under `must.ownerPropertiesOwned`; the live query SPREADS its fields
to the top level of `must` (`show_properties`, `properties_owned`, `highest_criteria`)
after `omitEmpty` strips blanks. The panel renders the block only from the nested key.
And `must: {...passThrough, ...}` means every stored key the panel does not know is sent
to the query anyway -- so a top-level `properties_owned: [1, 1]` written into the store
(this script's first version, same day) was INVISIBLE in the panel yet silently applied
with the wrong meaning. `--commit` removes that stray key wherever it finds it.

The stored value written here is the minimal object `{"show_properties": "one_per_owner"}`:
the panel's destructuring defaults fill the rest, and the query never sees an empty
range (the endpoint 400s on `["", ""]` and `[null, null]`).

Mechanism: the proven filter-preset PATCH (`dpd_presets_narrow.py`, 2026-09-07). Read
back through the STORE after each write and require (a) the nested object present and
equal, (b) no stray top-level `properties_owned`, (c) every other key byte-identical.
Counts before/after use `dpd_lane_assignee_report.to_query`, which spreads the block
the way the app does; a preset holding a RELATIVE date (`["24-months", "quarter"]`)
cannot be counted over the API (it wants YYYY-MM-DD) and prints n/a.

Usage:
    python -X utf8 -u src/scripts/dpd_presets_one_owner.py            # audit only
    python -X utf8 -u src/scripts/dpd_presets_one_owner.py --probe    # one preset
    python -X utf8 -u src/scripts/dpd_presets_one_owner.py --commit   # the rest
    python -X utf8 -u src/scripts/dpd_presets_one_owner.py --undo <backup.json>
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

from dpd.preset_spec import (PRESETS, ONE_OWNER_STORE_KEY, ONE_OWNER_VALUE,  # noqa: E402
                             ONE_OWNER_STRAY_KEY)
from dpd_lane_assignee_report import LiveApi, count, to_query  # noqa: E402
from dpd_presets_narrow import list_presets  # noqa: E402
from dpd_presets_verify import prefix_match  # noqa: E402
from obituary_dp_batch import WriteApi  # noqa: E402

RUN = ROOT / "output"


def targets_from_spec() -> list[dict]:
    return [p for p in PRESETS if p["blocks"].get("one_owner")]


def match_stored(spec: dict, stored: list[dict]) -> dict | None:
    hits = [s for s in stored if s.get("_folder") == spec["folder"]
            and prefix_match(spec["name"], s.get("title") or "")]
    if len(hits) != 1:
        return None
    return hits[0]


def _must(p: dict) -> dict:
    return (p.get("filters") or {}).get("must") or {}


def has_block(p: dict) -> bool:
    m = _must(p)
    return m.get(ONE_OWNER_STORE_KEY) == ONE_OWNER_VALUE and ONE_OWNER_STRAY_KEY not in m


def has_stray(p: dict) -> bool:
    return ONE_OWNER_STRAY_KEY in _must(p)


def safe_count(api: LiveApi, filters: dict) -> str:
    try:
        return str(count(api, to_query(filters)))
    except Exception as e:  # relative-date presets 400 here; that is expected
        return "n/a" if "wrong format" in str(e) else f"ERR {str(e)[:60]}"


def target_filters(p: dict) -> dict:
    new_filters = copy.deepcopy(p.get("filters") or {})
    must = new_filters.setdefault("must", {})
    must[ONE_OWNER_STORE_KEY] = copy.deepcopy(ONE_OWNER_VALUE)
    must.pop(ONE_OWNER_STRAY_KEY, None)
    return new_filters


def patch_one(api: WriteApi, p: dict) -> tuple[bool, str]:
    new_filters = target_filters(p)
    paths = [f"/api/internal/filter-preset/{p['uuid']}/",
             f"/api/internal/filter-preset-folder/{p['_folder_uuid']}/filter-preset/{p['uuid']}/"]
    last = ""
    for path in paths:
        st, _ = api.write("PATCH", path, {"filters": new_filters})
        last = f"{path} -> {st}"
        if st == 200:
            break
    else:
        return False, f"no PATCH surface accepted it ({last})"
    st2, pr = api.get(f"/api/internal/filter-preset-folder/{p['_folder_uuid']}"
                      "/filter-preset/?offset=0&limit=999&ordering=title&type=properties")
    prs = pr if isinstance(pr, list) else (pr.get("results") or [])
    stored = next((x for x in prs if x["uuid"] == p["uuid"]), None)
    if stored is None:
        return False, "preset vanished from the folder listing on read-back"
    if not has_block(stored):
        m = _must(stored)
        return False, (f"read-back {ONE_OWNER_STORE_KEY}={m.get(ONE_OWNER_STORE_KEY)!r} "
                       f"stray={m.get(ONE_OWNER_STRAY_KEY)!r} (200-but-unchanged trap?)")
    a = copy.deepcopy(stored.get("filters") or {})
    b = copy.deepcopy(new_filters)
    for d in (a, b):
        d.get("must", {}).pop(ONE_OWNER_STORE_KEY, None)
        d.get("must", {}).pop(ONE_OWNER_STRAY_KEY, None)
    if a != b:
        return False, "a non-target filter key changed on write -- restore from backup"
    return True, last


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
            st, _ = api.write("PATCH", f"/api/internal/filter-preset/{r['uuid']}/",
                              {"filters": r["filters"]})
            done += (st == 200)
            print(f"  restore {r['name']!r}: {st}")
        print(f"restored {done} of {len(recs)}")
        return 0

    live = LiveApi()
    stored = list_presets(api)
    specs = targets_from_spec()
    todo, have, missing = [], [], []
    for s in specs:
        m = match_stored(s, stored)
        if m is None:
            missing.append(s["name"])
        elif has_block(m):
            have.append(m)
        else:
            todo.append(m)
    print(f"{len(specs)} presets marked one_owner in the spec; {len(have)} already carry "
          f"{ONE_OWNER_STORE_KEY}={ONE_OWNER_VALUE} with no stray key; {len(todo)} to patch; "
          f"{len(missing)} not found in the store")
    for n in missing:
        print(f"  MISSING: {n}")
    # Any DPD preset outside the target set that carries the block or the stray key is
    # reported, never touched.
    spec_names = {(s["folder"], s["name"]) for s in specs}
    for p in stored:
        if not (p.get("_folder") or "").split(" ", 1)[0].isdigit():
            continue
        if any(f == p["_folder"] and prefix_match(n, p["title"]) for f, n in spec_names):
            continue
        if ONE_OWNER_STORE_KEY in _must(p) or has_stray(p):
            print(f"  NOTE: {p['_folder']} | {p['title']} carries "
                  f"{ONE_OWNER_STORE_KEY}={_must(p).get(ONE_OWNER_STORE_KEY)!r} / "
                  f"{ONE_OWNER_STRAY_KEY}={_must(p).get(ONE_OWNER_STRAY_KEY)!r} but the spec "
                  "does not ask for it (left alone)")
    for p in todo:
        print(f"  {p['_folder']:22} {p['title']:30} "
              f"{'stray ' + ONE_OWNER_STRAY_KEY + '=' + json.dumps(_must(p)[ONE_OWNER_STRAY_KEY]) + '  ' if has_stray(p) else ''}"
              f"live now: {safe_count(live, p['filters'])}")
    if missing:
        print("\nrefusing to write while a spec preset is unmatched")
        return 1
    if not (a.probe or a.commit):
        print("\nAudit only. --probe patches one, --commit the rest.")
        return 0

    picks = todo[:1] if a.probe else todo
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_p = RUN / f"dpd_presets_one_owner_backup_{ts}.json"
    backup_p.write_text(json.dumps(
        [{"uuid": p["uuid"], "name": p["title"], "folder": p["_folder"],
          "filters": p.get("filters")} for p in picks], indent=1), encoding="utf-8")
    print(f"\nprior filters -> {backup_p}")

    ok = 0
    for i, p in enumerate(picks, 1):
        before = safe_count(live, p["filters"])
        good, note = patch_one(api, p)
        ok += good
        after = safe_count(live, target_filters(p)) if good else "?"
        print(f"  [{i}/{len(picks)}] {p['title']:30} {'OK' if good else 'FAIL -- ' + note}"
              f"   count {before} -> {after}")
        if not good:
            print("stopping on first failure; restore with --undo if needed")
            return 1
    print(f"\npatched {ok}/{len(picks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
