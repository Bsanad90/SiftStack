"""Put the Last Obituary Date block (Last 24 Months, prior to Month) on every DPD preset.

2026-09-28 (Basem): the edge moved from Quarter to Month -- "no obituary less than 45 days
old"; Month is the nearest anchor the picker offers (see OBIT_WINDOW_VALUE in preset_spec).
A preset holding the OLD pair counts as "to patch": target_filters overwrites the key.

Basem, 2026-09-25: "this should be added to ALL presets", reaffirmed after the measured
impact was put in front of him (the block is an INCLUDE window: a record with no obituary
date does not load, so Hottest - 02 Ready to Call goes 52 -> 1 and FTM - 02 Ready to Call
303 -> 33; full table in output/obit_window_impact_20260925.json).

Stored shape: `must.last_obituary_date = ["24-months", "quarter"]`, copied byte-for-byte
from the block Basem's own hand-save landed on FTM - 02 Ready to Call, which the panel
renders as "Prior to Date / Last 24 Months / Prior to Quarter" (screenshot 2026-09-25,
resolving to Jun 25, 2024 - Jun 25, 2026). No new vocabulary is invented here: the key is
one the panel already shows, so the invisible-key trap of 2026-09-24 does not apply.

Mechanism: the proven filter-preset PATCH (dpd_presets_narrow.py, 2026-09-07), one
preset probed first, every write read back through the STORE and required to (a) hold
the block exactly and (b) leave every other key byte-identical. Backup of the prior
filters is written before the first PATCH; --undo restores it. Live counts cannot be
read over the API for a relative date (the endpoint wants YYYY-MM-DD), so the count
printed after each write is the block resolved the way the picker resolved it today.

Usage:
    python -X utf8 -u src/scripts/dpd_presets_obit_window.py            # audit only
    python -X utf8 -u src/scripts/dpd_presets_obit_window.py --probe    # one preset
    python -X utf8 -u src/scripts/dpd_presets_obit_window.py --commit   # the rest
    python -X utf8 -u src/scripts/dpd_presets_obit_window.py --undo <backup.json>
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dpd.preset_spec import PRESETS, OBIT_WINDOW_KEY, OBIT_WINDOW_VALUE, resolve_relative_date  # noqa: E402
from dpd_lane_assignee_report import LiveApi, count, to_query  # noqa: E402
from dpd_presets_narrow import list_presets  # noqa: E402
from dpd_presets_verify import prefix_match  # noqa: E402
from obituary_dp_batch import WriteApi  # noqa: E402

RUN = ROOT / "output"


def match_stored(spec: dict, stored: list[dict]) -> dict | None:
    hits = [s for s in stored if s.get("_folder") == spec["folder"]
            and prefix_match(spec["name"], s.get("title") or "")]
    return hits[0] if len(hits) == 1 else None


def _must(p: dict) -> dict:
    return (p.get("filters") or {}).get("must") or {}


def has_block(p: dict) -> bool:
    return _must(p).get(OBIT_WINDOW_KEY) == OBIT_WINDOW_VALUE


def resolved_window(today: date | None = None, value: list[str] | None = None) -> list[str]:
    """Kept for callers; the resolver itself lives in preset_spec.resolve_relative_date."""
    return resolve_relative_date(value, today)


def approx_count(api: LiveApi, filters: dict) -> str:
    """Count with the relative block replaced by today's resolution of it (to_query does it)."""
    try:
        return str(count(api, to_query(filters)))
    except Exception as e:
        return f"ERR {str(e)[:60]}"


def target_filters(p: dict) -> dict:
    new_filters = copy.deepcopy(p.get("filters") or {})
    new_filters.setdefault("must", {})[OBIT_WINDOW_KEY] = list(OBIT_WINDOW_VALUE)
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
        return False, (f"read-back {OBIT_WINDOW_KEY}={_must(stored).get(OBIT_WINDOW_KEY)!r} "
                       "(200-but-unchanged trap?)")
    a = copy.deepcopy(stored.get("filters") or {})
    b = copy.deepcopy(new_filters)
    for d in (a, b):
        d.get("must", {}).pop(OBIT_WINDOW_KEY, None)
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
    print(f"today's resolution of {OBIT_WINDOW_VALUE}: {resolved_window()}")
    stored = list_presets(api)
    todo, have, missing = [], [], []
    for s in PRESETS:
        m = match_stored(s, stored)
        if m is None:
            missing.append(s["name"])
        elif has_block(m):
            have.append(m)
        else:
            todo.append(m)
    print(f"{len(PRESETS)} presets in the spec; {len(have)} already carry "
          f"{OBIT_WINDOW_KEY}={OBIT_WINDOW_VALUE}; {len(todo)} to patch; "
          f"{len(missing)} not found in the store")
    for n in missing:
        print(f"  MISSING: {n}")
    for p in todo:
        other = _must(p).get(OBIT_WINDOW_KEY)
        print(f"  {p['_folder']:22} {p['title']:30} "
              f"{'holds ' + json.dumps(other) + '  ' if other is not None else ''}"
              f"live now: {approx_count(live, p['filters'])}")
    if missing:
        print("\nrefusing to write while a spec preset is unmatched")
        return 1
    if not (a.probe or a.commit):
        print("\nAudit only. --probe patches one, --commit the rest.")
        return 0

    picks = todo[:1] if a.probe else todo
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_p = RUN / f"dpd_presets_obit_window_backup_{ts}.json"
    backup_p.write_text(json.dumps(
        [{"uuid": p["uuid"], "name": p["title"], "folder": p["_folder"],
          "filters": p.get("filters")} for p in picks], indent=1), encoding="utf-8")
    print(f"\nprior filters -> {backup_p}")

    ok = 0
    for i, p in enumerate(picks, 1):
        before = approx_count(live, p["filters"])
        good, note = patch_one(api, p)
        ok += good
        after = approx_count(live, target_filters(p)) if good else "?"
        print(f"  [{i}/{len(picks)}] {p['_folder']:22} {p['title']:30} "
              f"{'OK' if good else 'FAIL -- ' + note}   count {before} -> {after}", flush=True)
        if not good:
            print("stopping on first failure; restore with --undo if needed")
            return 1
    print(f"\npatched {ok}/{len(picks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
