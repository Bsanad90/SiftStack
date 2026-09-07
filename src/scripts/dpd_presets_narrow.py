"""Narrow every DPD Records preset's county scope to the 6-county footprint.

Basem's directive 2026-09-07: ALL presets carry only District Of Columbia,
Anne Arundel, Frederick, Montgomery, Fairfax, Arlington (he already hand-
narrowed 6 of the 73; this brings the other 67 in line — the 9-county call
scope and the 15-county mail scope both collapse to the 6).

Mechanism: the internal API. The old "presets have no write API" note predates
the proven PATCH surfaces; this script PROBES first — PATCH one preset's
`filters` with `any_county` swapped to the 6 county objects (copied VERBATIM
from a hand-narrowed preset, so uuid/title/isNegative shape is exactly what
the UI wrote), then reads the store back and requires (a) the county change
landed and (b) every OTHER filter key is byte-identical. The known trap here
is a 200 that changes nothing (the message-pin PATCH did exactly that), so
read-back comparison is the only accepted evidence.

Usage:
    python -X utf8 -u src/scripts/dpd_presets_narrow.py            # audit only
    python -X utf8 -u src/scripts/dpd_presets_narrow.py --probe    # one preset
    python -X utf8 -u src/scripts/dpd_presets_narrow.py --commit   # the rest
    python -X utf8 -u src/scripts/dpd_presets_narrow.py --undo <backup.json>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from obituary_dp_batch import WriteApi  # noqa: E402

RUN = ROOT / "output"
TARGET = ["Anne Arundel", "Arlington", "District Of Columbia",
          "Fairfax", "Frederick", "Montgomery"]
# The preset whose stored county objects are the template (hand-narrowed by
# Basem, verified live 2026-09-07 to hold exactly the 6).
TEMPLATE_PRESET = "Hottest - 02 Ready to Call"


def list_presets(api: WriteApi) -> list[dict]:
    st, folders = api.get("/api/internal/filter-preset-folder/?limit=999&type=properties")
    rows = folders if isinstance(folders, list) else (folders.get("results") or [])
    out = []
    for f in rows:
        st2, pr = api.get(f"/api/internal/filter-preset-folder/{f['uuid']}"
                          "/filter-preset/?offset=0&limit=999&ordering=title&type=properties")
        prs = pr if isinstance(pr, list) else (pr.get("results") or [])
        for p in prs:
            p["_folder"] = f.get("title")
            p["_folder_uuid"] = f["uuid"]
            out.append(p)
    return out


def county_titles(p: dict) -> list[str]:
    return sorted((c.get("title") or "")
                  for c in ((p.get("filters") or {}).get("must") or {}).get("any_county") or [])


def patch_counties(api: WriteApi, p: dict, county_objs: list[dict]) -> tuple[bool, str]:
    """PATCH the preset's filters with any_county swapped; verify by re-read."""
    new_filters = json.loads(json.dumps(p.get("filters") or {}))
    new_filters.setdefault("must", {})["any_county"] = county_objs
    paths = [f"/api/internal/filter-preset/{p['uuid']}/",
             f"/api/internal/filter-preset-folder/{p['_folder_uuid']}/filter-preset/{p['uuid']}/"]
    last = ""
    for path in paths:
        st, resp = api.write("PATCH", path, {"filters": new_filters})
        last = f"{path} -> {st}"
        if st == 200:
            break
    else:
        return False, f"no PATCH surface accepted it ({last})"
    # Read back THROUGH THE STORE (never trust the PATCH echo).
    st2, pr = api.get(f"/api/internal/filter-preset-folder/{p['_folder_uuid']}"
                      "/filter-preset/?offset=0&limit=999&ordering=title&type=properties")
    prs = pr if isinstance(pr, list) else (pr.get("results") or [])
    stored = next((x for x in prs if x["uuid"] == p["uuid"]), None)
    if stored is None:
        return False, "preset vanished from the folder listing on read-back"
    got = county_titles(stored)
    if got != TARGET:
        return False, f"read-back counties {got} != target (200-but-unchanged trap?)"
    # Everything else must be byte-identical.
    a = json.loads(json.dumps(stored.get("filters") or {}))
    b = json.loads(json.dumps(new_filters))
    a.get("must", {}).pop("any_county", None)
    b.get("must", {}).pop("any_county", None)
    if a != b:
        return False, "a non-county filter key changed on write — restore from backup"
    return True, last


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--probe", action="store_true", help="narrow ONE preset and stop")
    g.add_argument("--commit", action="store_true", help="narrow every remaining preset")
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

    presets = list_presets(api)
    dpd = [p for p in presets if p.get("_folder", "").split(" ", 1)[0].isdigit()]
    template = next((p for p in dpd if p.get("title") == TEMPLATE_PRESET), None)
    if template is None:
        print(f"template preset {TEMPLATE_PRESET!r} not found")
        return 1
    county_objs = ((template.get("filters") or {}).get("must") or {}).get("any_county") or []
    if sorted(c.get("title") for c in county_objs) != TARGET or \
            any(c.get("isNegative") for c in county_objs):
        print(f"template counties are not the 6: {county_titles(template)}")
        return 1

    # A preset with NO county filter (the "30 My Tasks" views) is not county-
    # scoped at all — writing counties into it would change its meaning, not
    # narrow it. Only presets that already filter on counties are in scope.
    scoped = [p for p in dpd if county_titles(p)]
    todo = [p for p in scoped if county_titles(p) != TARGET]
    print(f"{len(dpd)} presets in numbered folders; {len(dpd) - len(scoped)} carry no "
          f"county filter (skipped); {len(scoped) - len(todo)} already on the 6; "
          f"{len(todo)} to narrow")
    for p in todo:
        print(f"  {p['_folder']:24} {p['title']}: {len(county_titles(p))} counties")
    if not (a.probe or a.commit):
        print("\nAudit only. --probe narrows one, --commit the rest.")
        return 0

    targets = todo[:1] if a.probe else todo
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_p = RUN / f"dpd_presets_narrow_backup_{ts}.json"
    backup_p.write_text(json.dumps(
        [{"uuid": p["uuid"], "name": p["title"], "folder": p["_folder"],
          "filters": p.get("filters")} for p in targets], indent=1), encoding="utf-8")
    print(f"\nprior filters -> {backup_p}")

    ok = 0
    for i, p in enumerate(targets, 1):
        good, note = patch_counties(api, p, county_objs)
        ok += good
        print(f"  [{i}/{len(targets)}] {p['title']}: {'OK' if good else 'FAIL — ' + note}")
        if not good:
            print("stopping on first failure; restore with --undo if needed")
            return 1
    print(f"\nnarrowed {ok}/{len(targets)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
