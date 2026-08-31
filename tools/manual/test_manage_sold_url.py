"""Offline check of the manage-sold county resolution and URL construction.

No browser, no network, no account. Drives the real `_siftmap_search_sold`
against a stub page and asserts what actually reaches SiftMap, because the bug
this guards against was silent: an unknown county resolved to Knox County TN and
the run reported success.

    python tools/manual/test_manage_sold_url.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import datasift_uploader as du  # noqa: E402
from dpd import siftmap as dpd_siftmap  # noqa: E402
from dpd.jurisdictions import JURISDICTIONS, county_label  # noqa: E402


class StubPage:
    """Records the URLs asked for and answers nothing else interesting."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    async def goto(self, url, **_kw):
        self.urls.append(url)

    async def wait_for_timeout(self, _ms):
        return None


def _neutralise(monkey_count: int | None):
    """Stub the browser-touching helpers; keep the logic under test intact."""
    async def _noop(*_a, **_kw):
        return None

    async def _count(_page):
        return monkey_count

    du._dismiss_popups = _noop
    du._screenshot = _noop
    dpd_siftmap.result_count = _count


async def sweep(county: str, count: int | None = 5):
    _neutralise(count)
    page = StubPage()
    res = await du._siftmap_search_sold(
        page,
        county=county,
        start_date="08/01/2026",
        end_date="08/31/2026",
        min_sale_price=1000,
        sold_tag_date="2026-08",
        dry_run=True,
    )
    return page, res


def params(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


async def main() -> int:
    failures: list[str] = []

    # 1 ── every jurisdiction reaches SiftMap with ITS OWN fips
    print("-- per-jurisdiction URL --")
    for j in JURISDICTIONS:
        page, res = await sweep(county_label(j))
        q = params(page.urls[0])
        fips_ok = f'"fips": "{j.fips}"' in q["location"]
        dates_ok = (q.get("extra_last_sale_date_min") == "2026-08-01"
                    and q.get("extra_last_sale_date_max") == "2026-08-31")
        price_ok = q.get("extra_last_sale_price_min") == "1000"
        # Ty's behaviour: no account filter unless asked for
        scope_ok = "in_my_account_mode" not in q
        ok = fips_ok and dates_ok and price_ok and scope_ok and res["success"]
        if not ok:
            failures.append(f"{j.name}: fips={fips_ok} dates={dates_ok} "
                            f"price={price_ok} scope={scope_ok}")
        print(f"  {j.name:26s} fips {j.fips}  {'ok' if ok else 'FAIL'}")

    # 2 ── an unknown county must RAISE, never default to 47093
    print("\n-- unknown county must raise --")
    for bad in ["Nowhere", "Baltimore City", "Fairfax City", "47093x"]:
        try:
            page, _ = await sweep(bad)
        except ValueError as e:
            # The message echoes the rejected input, so it may legitimately
            # contain "47093" (e.g. "47093x"). What matters is that it stopped
            # rather than substituting a county.
            assert "cannot resolve" in str(e), str(e)
            print(f"  {bad!r:20s} raised ValueError  ok")
        else:
            failures.append(f"{bad!r} did NOT raise; URL={page.urls}")
            print(f"  {bad!r:20s} *** DID NOT RAISE ***")

    # 3 ── the old fallback is gone: no county may produce Knox unless it IS Knox
    print("\n-- no silent Knox fallback --")
    page, _ = await sweep("Montgomery")
    leaked = "47093" in params(page.urls[0])["location"]
    print(f"  Montgomery URL contains 47093: {leaked}  "
          f"{'FAIL' if leaked else 'ok'}")
    if leaked:
        failures.append("Montgomery leaked Knox fips 47093")
    page, _ = await sweep("Knox")
    knox_ok = '"fips": "47093"' in params(page.urls[0])["location"]
    print(f"  Knox still resolves to 47093: {knox_ok}  {'ok' if knox_ok else 'FAIL'}")
    if not knox_ok:
        failures.append("Knox no longer resolves")

    # 4 ── account scope is opt-in
    print("\n-- account scope opt-in --")
    _neutralise(5)
    page = StubPage()
    await du._siftmap_search_sold(
        page, county="Montgomery", start_date="08/01/2026", end_date="08/31/2026",
        min_sale_price=1000, sold_tag_date="2026-08", dry_run=True,
        account_scope="in",
    )
    q = params(page.urls[0])
    scoped = q.get("in_my_account_mode") == "in"
    print(f"  in_my_account_mode=in present: {scoped}  {'ok' if scoped else 'FAIL'}")
    if not scoped:
        failures.append("account_scope=in did not reach the URL")

    # 5 ── an unrendered count is not zero
    print("\n-- unrendered count is not zero --")
    _, res = await sweep("Montgomery", count=None)
    not_zero = res.get("matched") is None and res["success"]
    print(f"  matched=None preserved: {not_zero}  {'ok' if not_zero else 'FAIL'}")
    if not not_zero:
        failures.append("None count was collapsed to zero")
    _, res0 = await sweep("Montgomery", count=0)
    zero_ok = res0["success"] and res0["records_added"] == 0
    print(f"  count=0 short-circuits cleanly: {zero_ok}  {'ok' if zero_ok else 'FAIL'}")

    # 6 -- checkpoint: resume skips done work, and a dry run never marks done
    print("\n-- checkpoint --")
    import json
    import tempfile
    from datetime import datetime as _dt

    from dpd.jurisdictions import resolve as _r

    async def run_months(ckpt: Path, dry: bool):
        _neutralise(7)
        page = StubPage()
        return await du.manage_sold_properties(
            page,
            counties=["Montgomery", "Carroll"],
            months_back=1,
            sold_tag_date=None,
            dry_run=dry,
            checkpoint_path=ckpt,
        )

    with tempfile.TemporaryDirectory() as td:
        ckpt = Path(td) / "state.json"

        # A dry run must leave the checkpoint empty. Otherwise the live run that
        # follows skips every county the dry run "completed".
        dres = await run_months(ckpt, dry=True)

        # A dry run must also REPORT SUCCESS. This regressed once: the county
        # accounting keyed on records>0, and a dry run adds no records, so a
        # perfectly good run exited 1 with "No counties processed successfully".
        dry_ok = dres.get("success") is True
        print("  dry run reports success: %s  %s"
              % (dry_ok, "ok" if dry_ok else "FAIL"))
        if not dry_ok:
            failures.append("dry run reported failure: %s" % dres.get("message"))
        matched_ok = dres.get("total_matched") == 14  # 7 per county x 2 counties
        print("  dry run totals matched (%s): %s  %s"
              % (dres.get("total_matched"), matched_ok, "ok" if matched_ok else "FAIL"))
        if not matched_ok:
            failures.append("total_matched wrong: %s" % dres.get("total_matched"))
        after_dry = json.loads(ckpt.read_text()) if ckpt.exists() else {}
        dry_clean = not after_dry.get("done")
        print("  dry run wrote no checkpoint entries: %s  %s"
              % (dry_clean, "ok" if dry_clean else "FAIL"))
        if not dry_clean:
            failures.append("dry run polluted the checkpoint: %s" % after_dry)

        # Seed a completed entry and confirm only that county-month is skipped.
        now = _dt.now()
        m = now.month - 1 or 12
        y = now.year if now.month > 1 else now.year - 1
        key = "%s:%d-%02d" % (_r("Montgomery").fips, y, m)
        ckpt.write_text(json.dumps({"done": {key: {"records": 42, "at": "seeded"}}}))

        res = await run_months(ckpt, dry=False)
        details = {d["county"]: d for d in res["month_details"]}
        skipped = details.get("Montgomery", {}).get("skipped") is True
        ran = details.get("Carroll", {}).get("skipped") is not True
        print("  seeded county skipped: %s  %s" % (skipped, "ok" if skipped else "FAIL"))
        print("  unseeded county still ran: %s  %s" % (ran, "ok" if ran else "FAIL"))
        if not skipped:
            failures.append("checkpoint did not skip a completed county-month")
        if not ran:
            failures.append("checkpoint skipped a county it should have run")

        carried = res["total_records"] >= 42
        print("  skipped count carried into total: %s  %s"
              % (carried, "ok" if carried else "FAIL"))
        if not carried:
            failures.append("skipped records not counted in the total")

    print("\n" + ("ALL CHECKS PASSED" if not failures else "FAILURES:"))
    for f in failures:
        print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
