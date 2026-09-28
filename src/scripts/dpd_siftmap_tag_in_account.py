"""Tag the records ALREADY IN THE ACCOUNT that match one SiftMap rung (in_my_account_mode=in).

Basem 2026-09-28 on `DC P1-04 Notice of Foreclosure`: "needs to be searched alone with
records in account and tag appropriately". The rung's auto-add had been ON with an empty
tag list, so anything it dripped, and anything imported by another path that later picked
up the signal, holds no `Priority 1` / stack tag and no Records preset can see it.

Mechanics are the DPD pull's (`dpd_siftmap_pull.py`) with the manifest URL switched to
`in_my_account_mode=in`: open, read the count, "Select Max" -> "Add Records to Account"
with the tags committed as chips (`require_tags=True`), "Do not replace owners" left ON
(`replace_owners=False`) so PR/DM mapping on the held records is untouched. Adding a record
that already exists merges the tags onto it -- the same mechanism the `Recently Sold`
in-mode sweep and Ty's manage-sold flow rely on. Default is a dry run (count only).

  python src/scripts/dpd_siftmap_tag_in_account.py --rung "DC P1-04 Notice of Foreclosure"
  python src/scripts/dpd_siftmap_tag_in_account.py --rung "DC P1-04 Notice of Foreclosure" --commit
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
from dpd_siftmap_pull import FOOTPRINT, PRIORITY_TAGS  # noqa: E402


def find_rung(name: str) -> dict:
    for fips, county in FOOTPRINT.items():
        man = json.loads((ROOT / "data" / f"dpd_siftmap_manifest_{fips}.json").read_text(encoding="utf-8"))
        for p in man.get("presets", []):
            if p["name"] == name:
                pr = str(p.get("priority"))
                band = 0 if pr in ("1", "P1") else 1 if pr in ("2", "P2") else 2
                url = p["url"]
                if "in_my_account_mode" in url:
                    raise SystemExit(f"manifest url for {name!r} already carries a mode: {url}")
                return {"name": name, "county": county, "fips": fips, "band": band,
                        "signals": p.get("signals") or [], "url_in": url + "&in_my_account_mode=in"}
    raise SystemExit(f"rung not found in any footprint manifest: {name!r}")


async def run(name: str, commit: bool, month: str, url: str | None = None,
              tags: list[str] | None = None) -> dict:
    """`url` = a SiftMap filter URL WITHOUT a mode param (or with not_in, which is swapped)
    for a saved filter that is no longer a manifest rung -- DC P1-04 NOF after the 09-24
    re-rank; `tags` then names the full tag list to commit."""
    if url:
        url = url.replace("&in_my_account_mode=not_in", "").replace("&in_my_account_mode=in", "")
        r = {"name": name, "url_in": url + "&in_my_account_mode=in", "signals": [], "band": 0}
        if not tags:
            raise SystemExit("--url needs --tags")
    else:
        r = find_rung(name)
    tags = tags or [PRIORITY_TAGS[r["band"]], " + ".join(r["signals"]) or r["name"]]
    out = {"mode": "commit" if commit else "dry_run", "rung": r, "tags": tags,
           "at": datetime.now().isoformat(timespec="seconds")}
    out_path = ROOT / "output" / f"dpd_siftmap_tag_in_account_{datetime.now():%Y%m%dT%H%M%S}.json"
    email, password = get_credentials()
    async with create_browser(headless=True) as (_b, _c, page):
        if not await login(page, email, password) or "/login" in page.url:
            out["error"] = "login failed"
        else:
            # in-mode pages with a handful of results rendered "2 Properties" only after ~15 s
            # on 2026-09-28; the pull's 9 s default read None.
            c = await goto_map(page, r["url_in"], wait_ms=16000)
            out["count_in_account"] = c
            print(f"  {name}: {c} records already in the account match (in mode)", flush=True)
            if c is None:
                out["error"] = "no count rendered"
            elif c == 0:
                out["status"] = "nothing_to_tag"
            elif not commit:
                out["status"] = "dry_run"
            else:
                print(f"  > tagging {c} with {tags}", flush=True)
                got = await _siftmap_add_page_to_account(
                    page, county=name.replace(" ", "_")[:40], sold_tag_date=month,
                    page_num=1, replace_owners=False, tags=tags, require_tags=True)
                out["add_result"] = got
                out["status"] = "tagged" if got.get("success") else f"FAILED: {got.get('error') or got}"
    out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"  -> {out_path}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rung", required=True)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--month", default=datetime.now().strftime("%Y-%m"))
    ap.add_argument("--url", help="SiftMap filter URL for a saved filter that is not a manifest rung")
    ap.add_argument("--tags", help="comma-separated full tag list (required with --url)")
    a = ap.parse_args()
    tags = [x.strip() for x in a.tags.split(",")] if a.tags else None
    out = asyncio.run(run(a.rung, a.commit, a.month, url=a.url, tags=tags))
    print(json.dumps({k: v for k, v in out.items() if k != "rung"}, indent=1)[:1500])
    return 0 if not out.get("error") and not str(out.get("status", "")).startswith("FAILED") else 1


if __name__ == "__main__":
    sys.exit(main())
