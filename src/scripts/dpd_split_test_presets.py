"""Split-test presets: one dial list WITH the market-research geography exclusions, one
WITHOUT (Basem, 2026-10-06: "a split test for calling one preset with exclusions and without
to see if we can improve our hit rate and be more efficient with calls").

    python src/scripts/dpd_split_test_presets.py                 # plan: sets + live counts, no write
    python src/scripts/dpd_split_test_presets.py --commit        # folder + 3 presets, read back + counted
    python src/scripts/dpd_split_test_presets.py --undo output/dpd_split_test/presets_created_<stamp>.json
    python src/scripts/dpd_split_test_presets.py --bar thin      # stricter bar (adds THIN markets)

What gets built (folder `15 SPLIT TEST - CALL`, same `<Label> - <stage>` naming as the 85):
  Split A - Ready to Call     byte-for-byte clone of the base lane (control, "without")
  Split B - Ready Mkt Excl    the clone + dead ZIPs excluded + dead neighbourhoods excluded ("with")
  Split C - Ready Dead Nbrs   the clone restricted to the dead neighbourhoods -- the records B
                              removes, as a list of their own. B and C are DISJOINT, so a dialer
                              working both gives a readable comparison; A alone against B would
                              dilute the difference across the ~90% of records they share.
The three read the same account-wide call counter as every other preset, so a dial from any of
them advances the record out of all three (no double-dial) -- see preset_spec.

Where the exclusions come from: `output/market_research/six_county_investor_exclusions_20260922.csv`
(the footprint re-score of the 2026-08-25 Market Finder pull + the DC SiftMap sweep). NOT the
per-county `_EXCLUDE_by_county.xlsx` strings: a Records preset holds ONE ZIP block across all
six counties, and a per-county table lists bordering ZIPs with zero data (22101 McLean reads 0
in Arlington's and Frederick's tables and 82 investor sales in Fairfax's), so the verdict must
be the MAX across county tables, which is what the 09-22 file carries. Bar `dead` (default) =
verdict DEAD (<= 1 investor transaction / 6 months, the repo's DEAD_MAX_INV_TRANS) or NOT A
MARKET (no sales, no value); bar `thin` adds THIN (2-4). DC has no neighbourhood data and no
dead ZIPs, so the exclusion is a no-op there by construction.

Store shape (read out of the app bundle `output/_archive/reisift_main.min.js`, 2026-10-06,
`getMustAndMustNotIds` + the must / must_not key registries), then PROVEN on the query endpoint
(a key that does not move the count is ignored: a bogus key returned the 18,447 baseline, these
two did not):
  * Property ZIP Code  -> `must.any_zip5`, a list of {uuid, title, isNegative} objects like
    `any_county` (the "modern" whole-object preset form); `isNegative: true` is the exclusion.
    The query side wants bare strings under `must_not.any_zip5` -- `to_query` does that split.
  * Property Neighborhood -> plain name strings, `must.any_neighborhood` to include,
    `must_not.any_neighborhood` to exclude (the bundle keys this block by title, not uuid).
    The match is CASE-INSENSITIVE on the endpoint (`Orchard at New Market` = `... At ...` = 26).
  Each block is single-instance and one direction at a time, like every other panel block.
  `preset_spec`'s "the panel has no neighbourhood block" (suppression part 4) was wrong and is
  corrected in the same change.

Every create is read back (stored `must` == sent `must`) AND counted through `to_query`, which
must equal the plan's count: a preset that stores but filters differently is the failure this
guards against. Backup under output/dpd_split_test/; `--undo` deletes what it created.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from dpd_lane_assignee_report import count, to_query  # noqa: E402
from obituary_lane_build import Api, folder_map, folder_presets, prefix_match  # noqa: E402

EXCLUSIONS_CSV = ROOT / "output" / "market_research" / "six_county_investor_exclusions_20260922.csv"
OUT_ROOT = ROOT / "output" / "dpd_split_test"

FOLDER = "15 SPLIT TEST - CALL"
BASE_FOLDER, BASE_LANE = "01 HOTTEST - CALL", "Hottest - 02 Ready to Call"
# Stored titles are truncated at 26 characters (see obituary_lane_build); keep every title under.
TITLES = {
    "A": "Split A - Ready to Call",
    "B": "Split B - Ready Mkt Excl",
    "C": "Split C - Ready Dead Nbrs",
}
BARS = {"dead": {"DEAD", "NOT A MARKET"}, "thin": {"DEAD", "NOT A MARKET", "THIN"}}
ZIP_KEY, NBR_KEY = "any_zip5", "any_neighborhood"


def say(msg: str) -> None:
    print(msg, flush=True)


def exclusion_sets(bar: str) -> tuple[list[str], list[str]]:
    """(dead ZIPs, dead neighbourhoods) at the chosen bar, from the 09-22 footprint file."""
    verdicts = BARS[bar]
    rows = list(csv.DictReader(open(EXCLUSIONS_CSV, encoding="utf-8")))
    zips = sorted({r["area"].strip() for r in rows if r["level"] == "ZIP" and r["verdict"] in verdicts})
    nbrs = sorted({r["area"].strip() for r in rows
                   if r["level"] == "Neighborhood" and r["verdict"] in verdicts})
    # A name that is dead in one county table and live in another would exclude the live one
    # too (the block is name-keyed across all six counties); the 09-22 file carries none, and
    # this is where that would show up if a re-score ever did.
    live_z = {r["area"].strip() for r in rows if r["level"] == "ZIP" and r["verdict"] not in verdicts}
    live_n = {r["area"].strip() for r in rows if r["level"] == "Neighborhood" and r["verdict"] not in verdicts}
    clash = sorted((set(zips) & live_z) | (set(nbrs) & live_n))
    if clash:
        raise SystemExit(f"{len(clash)} areas are both dead and live in the exclusion file: {clash[:10]}")
    if not zips or not nbrs:
        raise SystemExit("empty exclusion set -- refusing to build a no-op split test")
    return zips, nbrs


def zip_objects(zips: list[str], negative: bool) -> list[dict]:
    return [{"uuid": z, "title": z, "isNegative": negative} for z in zips]


def build_bodies(base_filters: dict, zips: list[str], nbrs: list[str]) -> dict[str, dict]:
    """Three filter blobs off the base lane's STORED filters. Nothing else in them changes."""
    a = copy.deepcopy(base_filters)
    b = copy.deepcopy(base_filters)
    c = copy.deepcopy(base_filters)
    for f in (a, b, c):
        must = f.setdefault("must", {})
        for k in (ZIP_KEY, NBR_KEY):
            if k in must or k in (must.get("must_not") or {}):
                raise SystemExit(f"base lane already carries a {k} block; not building over it")
    b["must"][ZIP_KEY] = zip_objects(zips, negative=True)
    b["must"].setdefault("must_not", {})[NBR_KEY] = list(nbrs)
    c["must"][NBR_KEY] = list(nbrs)
    return {"A": a, "B": b, "C": c}


def base_lane(api: Api) -> dict:
    folders = folder_map(api)
    if BASE_FOLDER not in folders:
        raise SystemExit(f"base folder {BASE_FOLDER!r} missing")
    rows = [r for r in folder_presets(api, folders[BASE_FOLDER]["uuid"])
            if prefix_match(BASE_LANE, r["title"])]
    if len(rows) != 1:
        raise SystemExit(f"{BASE_LANE!r}: {len(rows)} matches in {BASE_FOLDER!r}")
    st, d = api.get(f"/api/internal/filter-preset/{rows[0]['uuid']}/")
    if st != 200:
        raise SystemExit(f"base lane read {st}")
    return d


def cmd_undo(api: Api, path: str) -> int:
    bk = json.loads(Path(path).read_text(encoding="utf-8"))
    bad = 0
    for p in bk.get("presets", []):
        st, _ = api.write("DELETE", f"/api/internal/filter-preset/{p['uuid']}/", {})
        say(f"  DELETE preset {p['title']!r}: {st}")
        bad += st not in (200, 204)
    for f in bk.get("folders", []):
        st, _ = api.write("DELETE", f"/api/internal/filter-preset-folder/{f['uuid']}/", {})
        say(f"  DELETE folder {f['title']!r}: {st}")
        bad += st not in (200, 204)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--commit", action="store_true", help="create the folder and the 3 presets")
    g.add_argument("--undo", metavar="BACKUP_JSON", help="delete what a --commit created")
    ap.add_argument("--bar", choices=sorted(BARS), default="dead",
                    help="dead = DEAD + NOT A MARKET (default); thin adds THIN markets")
    a = ap.parse_args()
    api = Api()
    if a.undo:
        return cmd_undo(api, a.undo)

    zips, nbrs = exclusion_sets(a.bar)
    say(f"bar {a.bar}: {len(zips)} ZIPs, {len(nbrs)} neighbourhoods excluded "
        f"(source {EXCLUSIONS_CSV.name})")
    base = base_lane(api)
    bodies = build_bodies(base["filters"], zips, nbrs)
    plan_counts = {}
    for k in ("A", "B", "C"):
        plan_counts[k] = count(api, to_query(bodies[k]))
        say(f"  {TITLES[k]:26} {plan_counts[k]:5}")
    zip_only = plan_counts["A"] - plan_counts["B"] - plan_counts["C"]
    say(f"  A - B = {plan_counts['A'] - plan_counts['B']} removed by the exclusions; "
        f"C holds {plan_counts['C']} of them (dead neighbourhoods), {zip_only} sit in a dead ZIP "
        f"only and are in neither B nor C")
    if plan_counts["B"] >= plan_counts["A"]:
        raise SystemExit("the exclusions removed nothing -- not a split test; check the keys")
    if not a.commit:
        say("plan only (--commit to create)")
        return 0

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    backup = OUT_ROOT / f"presets_created_{datetime.now():%Y%m%dT%H%M%S}.json"
    created = {"bar": a.bar, "base": {"title": base.get("title"), "uuid": base.get("uuid")},
               "zips": zips, "nbrs": nbrs, "plan_counts": plan_counts,
               "folders": [], "presets": []}
    folders = folder_map(api)
    if FOLDER not in folders:
        st, r = api.write("POST", "/api/internal/filter-preset-folder/",
                          {"title": FOLDER, "type": "properties", "permissions": []})
        if st not in (200, 201):
            raise SystemExit(f"folder create: {st} {str(r)[:300]}")
        folders = folder_map(api)
        if FOLDER not in folders:
            raise SystemExit(f"folder POST {st} but absent from the read-back listing")
        created["folders"].append({"title": FOLDER, "uuid": folders[FOLDER]["uuid"]})
        backup.write_text(json.dumps(created, indent=1), encoding="utf-8")
        say(f"folder {FOLDER!r} created {folders[FOLDER]['uuid']}")
    else:
        say(f"folder {FOLDER!r} exists ({folders[FOLDER]['uuid']})")
    fol = folders[FOLDER]

    failed = 0
    for k in ("A", "B", "C"):
        title = TITLES[k]
        existing = [r for r in folder_presets(api, fol["uuid"]) if prefix_match(title, r["title"])]
        if existing:
            say(f"  exists   {title!r} ({existing[0]['uuid']}) -- not touched")
            continue
        body = {"title": title, "folder": fol["uuid"], "quick_filter": bool(base.get("quick_filter")),
                "filters": bodies[k], "type": "properties"}
        st, r = api.write("POST", "/api/internal/filter-preset/", body)
        if st not in (200, 201) or not isinstance(r, dict) or not r.get("uuid"):
            say(f"  FAILED   {title!r}: {st} {str(r)[:300]}")
            failed += 1
            continue
        st2, got = api.get(f"/api/internal/filter-preset/{r['uuid']}/")
        stored = (got.get("filters") or {}) if st2 == 200 else {}
        same = stored.get("must") == body["filters"]["must"] and got.get("folder") == fol["uuid"]
        n = count(api, to_query(stored)) if same else None
        ok = same and n == plan_counts[k]
        created["presets"].append({"key": k, "title": title, "uuid": r["uuid"], "folder": fol["uuid"],
                                   "readback_ok": same, "count": n, "plan_count": plan_counts[k]})
        backup.write_text(json.dumps(created, indent=1), encoding="utf-8")
        say(f"  {'created' if ok else 'MISMATCH'} {title!r} {r['uuid']} "
            f"store={'same' if same else 'DIFFERS'} count={n} plan={plan_counts[k]}")
        failed += not ok
    say(f"backup {backup}")
    say(f"done: {len(created['presets'])} presets, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
