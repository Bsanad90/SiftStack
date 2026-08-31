"""Server-side verification of the 73 Records presets against `dpd.preset_spec`.

    python src/scripts/dpd_presets_verify.py

Reads what the SERVER stored -- not what the panel displayed -- via the internal
filter-preset endpoints the app itself uses (sniffed live 2026-08-27):

    GET /api/internal/filter-preset-folder/?limit=999&type=properties
    GET /api/internal/filter-preset-folder/{uuid}/filter-preset/?limit=999&type=properties

This is the ground truth the panel cannot give: the panel RENDERS a loaded exclusion
as "Include" (verified live -- the stored filter is `must_not` and applying it excludes
correctly, the mode dropdown just draws the default). So Phase 7 QA reads the store.

Two storage facts that shape the checks:
  * Titles are stored TRUNCATED (~24-26 chars, width-dependent), so matching is by
    prefix; `dpd.preset_spec` names are collision-free through length 22.
  * Tag/list values are stored as bare uuids with no names. Names are solved by
    ALGEBRA rather than another endpoint: every single-value group whose spec name is
    known pins its uuid (Hottest include = {Priority 1} pins Priority 1's uuid), and
    multi-value groups are then checked against the solved mapping. An inconsistency
    is a hard failure.
  * A multi-value group built from an OR block MUST be stored under an `any_` key:
    `must_not.all_tags [P1, P2]` means "exclude records carrying BOTH", which is a
    different and much weaker suppression than "either".

Read-only: mints the same JWT as datasift_api_upload and only ever GETs.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from datasift_api_upload import Api  # noqa: E402
from dpd.block_map import STATUS_NOT_SELECTABLE  # noqa: E402
from dpd.preset_spec import PRESETS  # noqa: E402
from dpd_presets_create import allocate_tags  # noqa: E402  (src/scripts on caller path)

OUT = ROOT / "output" / "dpd_presets_verify.json"

FOLDERS = ["01 HOTTEST - CALL", "02 HOTTEST - MAIL", "03 STRONG - CALL",
           "04 STRONG - MAIL", "05 FTM - CALL", "06 FTM - MAIL", "07 TIER 2 - CALL",
           "08 TIER 2 - MAIL", "09 BULK - CALL", "10 BULK - MAIL",
           "11 DEEP PROSPECTING", "12 REACTIVATION"]

# Spec boolean -> candidate storage keys, most-likely first. "Vacant Mailing" stores
# as `owner_vacant` (observed live); the panel label and the storage key do not match,
# so each spec param carries the candidates until every one is observed.
PARAM_KEY = {"has_numbers": ["phone"], "skiptraced": ["skiptraced"],
             "vacant_mailing": ["owner_vacant", "vacant_mailing"],
             "deceased": ["deceased", "owner_deceased", "is_deceased"],
             "vacant_property": ["vacant", "property_vacant", "vacant_property"]}
COUNTER_KEY = {"call_attempts": "predictivecall_attempts",
               "mail_attempts": "directmail_attempts"}


def norm_status(s: str) -> str:
    return s.strip().lower().replace("_", " ")


def prefix_match(spec_name: str, stored: str) -> bool:
    a, b = spec_name.strip(), (stored or "").strip()
    return a == b or a.startswith(b) or b.startswith(a)


def tag_groups(spec: dict) -> list[tuple[str, bool, list[str]]]:
    plan, _dropped = allocate_tags(spec["tags_any"], spec.get("tags_any_2") or [],
                                   spec["tags_none"])
    return plan


def verify_preset(spec: dict, stored: dict, uuid_of: dict, bad: list[str]) -> None:
    name = spec["name"]
    f = (stored.get("filters") or {}).get("must") or {}
    must_not = f.get("must_not") or {}

    def fail(msg: str) -> None:
        bad.append(f"{name}: {msg}")

    # Geography: the 9-county scope, all positive.
    counties = f.get("any_county") or []
    got = sorted((c.get("title") or "") for c in counties)
    if got != sorted(spec["counties"]):
        fail(f"counties {got} != {sorted(spec['counties'])}")
    if any(c.get("isNegative") for c in counties):
        fail("a county is stored NEGATIVE")

    # Universal suppression riders.
    if f.get("dnc") != 0:
        fail(f"dnc is {f.get('dnc')!r}, wanted 0")
    if f.get("opt_out") != 0:
        fail(f"opt_out is {f.get('opt_out')!r}, wanted 0")

    # Lists: exactly the two equity exclusions, uuid-consistent across the account.
    lists_not = sorted(must_not.get("all_lists") or must_not.get("any_lists") or [])
    want_lists = spec["suppression"].get("lists_none") or []
    if len(lists_not) != len(want_lists):
        fail(f"{len(lists_not)} excluded lists stored, wanted {len(want_lists)}")
    else:
        prev = uuid_of.get("__lists__")
        if prev is None:
            uuid_of["__lists__"] = lists_not
        elif prev != lists_not:
            fail(f"excluded-list uuids drifted: {lists_not} vs {prev}")

    # Statuses.
    blk = spec["blocks"]
    if blk.get("status_any"):
        want = sorted(norm_status(s) for s in blk["status_any"])
        got_any = sorted(norm_status(s) for s in (f.get("any_property_status") or []))
        if got_any != want:
            fail(f"status include {got_any} != {want}")
    else:
        want = sorted(norm_status(s)
                      for s in (spec["suppression"].get("status_none") or [])
                      if s not in STATUS_NOT_SELECTABLE)
        got_not = sorted(norm_status(s)
                         for s in (must_not.get("any_property_status") or []))
        if got_not != want:
            fail(f"status exclude {got_not} != {want}")

    # Counters.
    for skey, dkey in COUNTER_KEY.items():
        if skey not in blk:
            continue
        lo, hi = blk[skey]
        got_c = f.get(dkey)
        if not isinstance(got_c, list) or not got_c or got_c[0] != lo:
            fail(f"{dkey} is {got_c!r}, wanted min {lo}")
        elif hi is not None and (len(got_c) < 2 or got_c[1] != hi):
            fail(f"{dkey} is {got_c!r}, wanted max {hi}")

    # Params booleans the spec sets. A param is satisfied by ANY of its candidate
    # storage keys holding the wanted value; none holding it is a defect.
    for skey, dkeys in PARAM_KEY.items():
        if skey not in blk:
            continue
        want_v = 1 if blk[skey] else 0
        if not any(f.get(d) == want_v for d in dkeys):
            got_v = {d: f.get(d) for d in dkeys if d in f}
            fail(f"{skey} ({'/'.join(dkeys)}) is {got_v or None!r}, wanted {want_v}")

    # Tags: solve uuids from single-value groups, verify sets, and require the
    # any_/all_ key semantics for multi-value groups.
    for kind, is_exclude, values in tag_groups(spec):
        side = must_not if is_exclude else f
        any_u = list(side.get("any_tags") or [])
        all_u = list(side.get("all_tags") or [])
        if len(values) > 1:
            # The storage key decides the semantics, and on the must_not side it is the
            # OPPOSITE of the block label (measured 2026-08-27): "All Tags (AND)" + Do not
            # include is stored as must_not.any_tags and removes every record carrying ANY
            # of the tags (660 of 2,363 on the Clean tab); "Any Tags (OR)" + Do not include
            # is stored as must_not.all_tags and removes only records carrying ALL of them
            # (0 of 2,363). So a multi-value EXCLUSION must be under any_tags whatever block
            # built it; a multi-value INCLUSION follows its block (OR -> any_tags).
            want_any = is_exclude or kind == "tags_any"
            stored_u = any_u if want_any else all_u
            wrong_u = all_u if want_any else any_u
            if len(stored_u) != len(values):
                where = "must_not" if is_exclude else "must"
                fail(f"{where}.{'any' if want_any else 'all'}_tags holds "
                     f"{len(stored_u)} uuids, wanted {len(values)} "
                     f"(other key holds {len(wrong_u)}) -- OR/AND semantics at risk")
                continue
        else:
            # A single value means the same thing under either key, so accept whichever
            # is populated -- but when BOTH are (a preset with two include groups, e.g.
            # Deep - 04 Return Mail: the tier scope on OR plus `Return Mail` on AND),
            # read the key this block writes to. `any_u or all_u` grabbed the four-uuid
            # OR group and reported the AND block "stored as 4 uuids" on 2026-08-28.
            # Include side: OR -> any_tags, AND -> all_tags. Exclude side is flipped
            # (Do-not-include on an AND block stores must_not.any_tags, measured).
            block_any = (kind == "tags_any") != bool(is_exclude)
            preferred = any_u if block_any else all_u
            other = all_u if block_any else any_u
            stored_u = preferred if preferred else other
            if len(stored_u) != 1:
                fail(f"{'exclude' if is_exclude else 'include'} tag group "
                     f"{values} stored as {len(stored_u)} uuids")
                continue
        if len(values) == 1:
            nm = values[0]
            prev = uuid_of.get(nm)
            if prev is None:
                uuid_of[nm] = stored_u[0]
            elif prev != stored_u[0]:
                fail(f"tag {nm!r} maps to two uuids: {stored_u[0]} vs {prev}")
        else:
            known = {uuid_of.get(v) for v in values}
            if None not in known and set(stored_u) != known:
                fail(f"tag group {values}: stored uuids do not match the "
                     f"solved mapping")


def main() -> int:
    api = Api()
    folders = api.call("/api/internal/filter-preset-folder/"
                       "?offset=0&limit=999&ordering=title&type=properties")
    by_title = {r["title"]: r["uuid"] for r in folders.get("results") or []}
    missing_folders = [x for x in FOLDERS if x not in by_title]
    if missing_folders:
        print("MISSING FOLDERS:", missing_folders)
        return 2

    stored: dict[str, list[dict]] = {}
    for fname in FOLDERS:
        r = api.call(f"/api/internal/filter-preset-folder/{by_title[fname]}"
                     "/filter-preset/?offset=0&limit=999&ordering=title"
                     "&type=properties")
        stored[fname] = r.get("results") or []

    bad: list[str] = []
    uuid_of: dict = {}
    extras: list[str] = []
    missing: list[str] = []
    checked = 0
    for spec in PRESETS:
        row = next((s for s in stored[spec["folder"]]
                    if prefix_match(spec["name"], s.get("title") or "")), None)
        if row is None:
            missing.append(f"{spec['folder']}: {spec['name']}")
            continue
        checked += 1
        verify_preset(spec, row, uuid_of, bad)
    for fname in FOLDERS:
        for s in stored[fname]:
            t = s.get("title") or ""
            if not any(prefix_match(p["name"], t) for p in PRESETS
                       if p["folder"] == fname):
                extras.append(f"{fname}: {t}")

    print(f"checked {checked}/73 against the server store")
    print(f"missing: {len(missing)}")
    for m in missing:
        print("  ", m)
    print(f"defects: {len(bad)}")
    for b in bad[:40]:
        print("  ", b)
    print(f"extras (not in spec, e.g. ZZ test junk): {len(extras)}")
    for e in extras:
        print("  ", e)
    print("solved tag uuids:", {k: v for k, v in uuid_of.items() if k != '__lists__'})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"ran_at": datetime.now().isoformat(timespec="seconds"),
         "checked": checked, "missing": missing, "defects": bad,
         "extras": extras, "tag_uuids": uuid_of}, indent=1), encoding="utf-8")
    print(f"wrote {OUT}")
    return 1 if (missing or bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
