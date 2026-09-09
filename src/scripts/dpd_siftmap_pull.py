"""Pull SiftMap rungs into the account, in ladder order, with the spec tags.

    python src/scripts/dpd_siftmap_pull.py                # DRY RUN: counts only, imports nothing
    python src/scripts/dpd_siftmap_pull.py --commit       # import the approved distress rungs
    python src/scripts/dpd_siftmap_pull.py --commit --rungs "Fairfax P1-01 Notice of Default"

THE DEFAULT RUNG SET IS THE ONE BASEM APPROVED 2026-09-08 ("distress only first"):
the four DC distress rungs + Fairfax P1-01 Notice of Default (~111 records fresh-measured
2026-09-08). Montgomery P1-04 F&C+OOS (~1,709) was explicitly HELD for a separate go --
add it only with --rungs and a fresh instruction.

Per rung, in order:
  1. open the manifest's `url_not_in` (in_my_account_mode=not_in -- only records NOT in
     the account) and read the count;
  2. GUARD: the count must be within max(5, 15%) of the 2026-09-08 fresh measure, or the
     rung is refused (a wild count means the wrong filter loaded or a param was ignored);
  3. a count of 0 is recorded as skipped, not an error (DC P1-01 legitimately measures 0);
  4. --commit only: Select Max -> Add Records to Account -> tag with
     `Priority 1` + the rung's signal stack + `DPD pulled <YYYY-MM>` -> confirm.
     Tags are applied through datasift_uploader._siftmap_add_page_to_account with
     require_tags=True: the confirm click is REFUSED unless every tag is a committed chip.
  5. verify: wait for the search index (it lags writes by a minute or two), re-open
     url_not_in and require the residual count to have collapsed. Halt the ladder on any
     verification failure.

Ladder order matters: each rung's not_in query excludes what earlier rungs imported, so
records land on their best rung. Owner protection: "Do not replace owners" is left AS-IS
(replace_owners=False), same reasoning as the sold sweep.

Writes output/dpd_siftmap_pull_<ts>.json progressively (one flush per rung).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from datasift_core import create_browser, get_credentials, login  # noqa: E402
from datasift_uploader import _siftmap_add_page_to_account  # noqa: E402
from dpd_siftmap_presets import goto_map  # noqa: E402

# Approved 2026-09-08, in ladder order. DC first (its rungs overlap each other), then VA.
APPROVED_RUNGS = [
    "DC P1-01 Absentee + Notice of Foreclosure",
    "DC P1-03 Free & Clear + Notice of Foreclosure",
    "DC P1-04 Notice of Foreclosure",
    "DC P1-05 Notice of Default",
    "Fairfax P1-01 Notice of Default",
]
FOOTPRINT = {"11001": "DC", "24031": "Montgomery", "51059": "Fairfax",
             "24021": "Frederick", "51013": "Arlington", "24003": "Anne Arundel"}
PRIORITY_TAGS = {0: "Priority 1", 1: "Priority 2", 2: "Tier 2"}


def _find_rung(name: str) -> dict:
    for fips, county in FOOTPRINT.items():
        man = json.loads((ROOT / "data" / f"dpd_siftmap_manifest_{fips}.json")
                         .read_text(encoding="utf-8"))
        cand = list(man.get("presets", []))
        if man.get("tier2"):
            cand.append({**man["tier2"], "priority": "T2"})
        for p in cand:
            if p["name"] == name:
                pr = str(p.get("priority"))
                band = 0 if pr in ("1", "P1") else 1 if pr in ("2", "P2") else 2
                return {"name": name, "county": county, "fips": fips, "band": band,
                        "signals": p.get("signals") or [], "url_not_in": p["url_not_in"]}
    raise SystemExit(f"rung not found in any footprint manifest: {name!r}")


def _fresh_expectations() -> dict[str, int]:
    """name -> fresh not-in count, from the newest fresh-measure artifact."""
    cands = sorted((ROOT / "output").glob("dpd_siftmap_fresh_measure_*.json"))
    if not cands:
        return {}
    fm = json.loads(cands[-1].read_text(encoding="utf-8"))
    return {r["name"]: r["fresh_not_in"] for r in fm.get("results", [])
            if r.get("fresh_not_in") is not None}


async def _count(page, url: str):
    return await goto_map(page, url)


async def run(rung_names: list[str], commit: bool, month: str) -> dict:
    rungs = [_find_rung(n) for n in rung_names]
    expect = _fresh_expectations()
    out = {"mode": "commit" if commit else "dry_run", "month": month,
           "at": datetime.now().isoformat(timespec="seconds"), "results": []}
    out_path = ROOT / "output" / f"dpd_siftmap_pull_{datetime.now():%Y%m%dT%H%M%S}.json"

    def flush():
        out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")

    email, password = get_credentials()
    async with create_browser(headless=True) as (_b, _c, page):
        if not await login(page, email, password) or "/login" in page.url:
            out["error"] = "login failed"
            flush()
            return out
        for r in rungs:
            row = dict(r)
            out["results"].append(row)
            exp = expect.get(r["name"])
            row["expected_not_in"] = exp
            c = await _count(page, r["url_not_in"])
            row["count_before"] = c
            if c is None:
                row["status"] = "FAILED: no count rendered"
                out["error"] = row["status"]
                flush()
                break
            tol = max(5, int((exp or 0) * 0.15))
            if exp is not None and abs(c - exp) > tol:
                row["status"] = (f"FAILED: count {c} is outside {exp}±{tol} from the fresh "
                                 "measure -- wrong filter or ignored param; refusing")
                out["error"] = row["status"]
                flush()
                break
            if c == 0:
                row["status"] = "skipped_zero"
                print(f"  = {r['name']}: 0 not-in-account, nothing to pull", flush=True)
                flush()
                continue
            if not commit:
                row["status"] = "dry_run"
                print(f"  ~ {r['name']}: would import {c} (dry run)", flush=True)
                flush()
                continue

            tags = [PRIORITY_TAGS[r["band"]], " + ".join(r["signals"]) or r["name"],
                    f"DPD pulled {month}"]
            row["tags"] = tags
            print(f"  > {r['name']}: importing {c} with tags {tags}", flush=True)
            got = await _siftmap_add_page_to_account(
                page, county=r["name"].replace(" ", "_")[:40], sold_tag_date=month,
                page_num=1, replace_owners=False, tags=tags, require_tags=True)
            row["add_result"] = got
            if not got.get("success"):
                row["status"] = f"FAILED at Add Records: {got.get('error') or got}"
                out["error"] = row["status"]
                flush()
                break
            # The modal's own count is the truth of what was imported.
            row["records_added"] = got.get("records_added")

            # Verify: the search index lags writes by a minute or two, so wait, then
            # require the not-in count to collapse. One long retry before failing.
            residual = None
            for wait_s in (90, 150):
                await page.wait_for_timeout(wait_s * 1000)
                residual = await _count(page, r["url_not_in"])
                if residual is not None and residual <= max(2, int(c * 0.2)):
                    break
            row["count_after"] = residual
            if residual is None or residual > max(2, int(c * 0.2)):
                row["status"] = (f"FAILED verification: not-in count still {residual} of {c} "
                                 "after import + index wait -- investigate before continuing")
                out["error"] = row["status"]
                flush()
                break
            row["status"] = "imported"
            print(f"  + {r['name']}: imported {row['records_added']}, "
                  f"not-in residual {residual}", flush=True)
            flush()
    flush()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Pull SiftMap rungs into the account (ladder order)")
    ap.add_argument("--commit", action="store_true",
                    help="actually import; default is a dry run that reads counts only")
    ap.add_argument("--rungs", help="semicolon-separated rung names (default: the approved "
                                    "distress set)")
    ap.add_argument("--month", default=f"{datetime.now():%Y-%m}",
                    help="month stamp for the 'DPD pulled' tag (default: current month)")
    a = ap.parse_args()
    names = ([s.strip() for s in a.rungs.split(";") if s.strip()] if a.rungs
             else list(APPROVED_RUNGS))
    out = asyncio.run(run(names, a.commit, a.month))
    done = [r for r in out["results"] if r.get("status") in ("imported", "skipped_zero", "dry_run")]
    print(f"\n{out['mode']}: {len(done)} of {len(out['results'])} rungs clean; "
          f"imported total {sum(r.get('records_added') or 0 for r in out['results'])}")
    if out.get("error"):
        print("ERROR:", out["error"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
