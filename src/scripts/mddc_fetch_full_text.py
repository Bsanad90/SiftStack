"""Fetch full notice text for MDDC Trustee's Sale notices via Scrapfly.

`mddc_trustee_sale_pull.py` only captures the search-results GRID SNIPPET for
each notice ("click 'view' to open the full text"), so `auction_date` and
`loan_principal` -- which both typically appear past that truncation point --
come back blank most of the time. This script fetches each notice's real
`Details.aspx?ID=<n>` page and re-parses those fields from the full text.

WHY ONE SCRAPFLY CALL PER NOTICE, NOT A BATCH: `Details.aspx` sits behind a
Cloudflare-style "Verify you are human" challenge that Firecrawl can't clear.
Scrapfly's `asp=True` mode is the fix, but `mddc_trustee_sale_pull.py`'s own
module docstring already documents a proven failure mode: a login call
followed by a SEPARATE `.scrape()` call to the notice -- even reusing the
same Scrapfly `session=` string -- lands unauthenticated and still shows the
challenge. Scrapfly's `session=` only gives a sticky proxy IP + cookie jar;
it does not replay this site's own server-side session state across a call
boundary. So login and the notice fetch have to happen inside ONE
`js_scenario` in ONE `.scrape()` call, per notice -- there's no way to log in
once and cheaply fetch many notices the way the TN pipeline's
`fetch_notice`/`fetch_notices` do for tnpublicnotice.com (verified NOT to
carry over for this site). Budget accordingly: ~640 notices means ~640
individual Scrapfly calls, not a handful of batched ones.

THIS EXACT MECHANISM IS UNVERIFIED LIVE FOR MDDC. The one-scenario pattern is
proven for tnpublicnotice.com's gate (`fetch_notice_via_search` in
`src/scrapfly_client.py`), which mirrors the login-then-navigate shape this
script uses, but MDDC's gate has not been tested with it yet. Always run
`--test-id` on one real notice first and read the printed result before
committing to a full batch.

Usage:
    # Spike: validate the mechanism on ONE notice before running the batch.
    python src/scripts/mddc_fetch_full_text.py --csv output/mddc_trustee_sale.csv --test-id 123456

    # Full run (resumable -- re-running skips notice_ids already checkpointed).
    python src/scripts/mddc_fetch_full_text.py --csv output/mddc_trustee_sale.csv \\
        --out output/mddc_trustee_sale_full.csv --workers 6
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import mddc_trustee_sale_pull as gridmod  # noqa: E402

BASE_URL = "https://mddcpublicnotices.com"
LOGIN_URL = f"{BASE_URL}/authenticate.aspx"

# Lowercased substring match against the fetched page. Not yet confirmed
# against a live MDDC gate page -- refine after the first --test-id run if
# these don't actually appear.
_GATE_MARKERS = (
    "verify you are human", "you must complete", "cf-challenge",
    "checking your browser", "attention required",
)
# Below this, treat the response as a gate page / empty shell rather than a
# genuine notice -- a real Details.aspx page has a full legal notice body.
_MIN_FULLTEXT_CHARS = 300


@dataclass
class FullTextResult:
    ok: bool = False
    text: str = ""
    error: str = ""


def load_credentials() -> dict:
    env = dotenv_values(str(ENV_PATH))
    email = env.get("MDDC_EMAIL")
    pw = env.get("MDDC_PASSWORD")
    scrapfly_key = env.get("SCRAPFLY_KEY")
    missing = [
        name for name, val in
        (("MDDC_EMAIL", email), ("MDDC_PASSWORD", pw), ("SCRAPFLY_KEY", scrapfly_key))
        if not val
    ]
    if missing:
        raise RuntimeError(f"Missing from .env: {', '.join(missing)}")
    return {"email": email, "password": pw, "scrapfly_key": scrapfly_key}


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


class MddcScrapflyClient:
    """One js_scenario per notice: login + navigate to Details.aspx, in ONE
    Scrapfly call, so no session state has to survive a call boundary.
    """

    def __init__(self, key: str, country: str = "us", proxy_pool: str = "public_residential_pool"):
        try:
            from scrapfly import ScrapflyClient
        except ImportError as exc:
            raise ImportError("scrapfly-sdk not installed. Run: pip install 'scrapfly-sdk'") from exc
        self._client = ScrapflyClient(key=key)
        self.country = country
        self.proxy_pool = proxy_pool

    def _fetch_once(self, notice_id: str, email: str, password: str, session: str) -> FullTextResult:
        from scrapfly import (
            ScrapeConfig,
            ScrapflyScrapeError,
            UpstreamHttpClientError,
            UpstreamHttpServerError,
        )

        scenario = [
            {"wait_for_selector": {
                "selector": "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_txtEmailAddress",
                "timeout": 15000}},
            {"fill": {"selector": "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_txtEmailAddress",
                      "value": email}},
            {"fill": {"selector": "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_txtPassword",
                      "value": password}},
            {"click": {"selector": "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_btnAuth"}},
            {"wait": 5000},
            {"execute": {"script": f"window.location.href = '{BASE_URL}/Details.aspx?ID={notice_id}';"}},
            {"wait": 5000},
        ]
        cfg = ScrapeConfig(
            url=LOGIN_URL,
            render_js=True,
            asp=True,
            country=self.country,
            session=session,
            proxy_pool=self.proxy_pool,
            rendering_wait=2000,
            raise_on_upstream_error=False,
            js_scenario=scenario,
        )
        try:
            resp = self._client.scrape(cfg)
        except ScrapflyScrapeError as exc:
            code = str(getattr(exc, "code", "") or exc).lower()
            if "quota_limit_reached" in code:
                return FullTextResult(ok=False, error="quota_exhausted")
            return FullTextResult(ok=False, error=f"{getattr(exc, 'code', 'ScrapflyScrapeError')}: {exc}")
        except (UpstreamHttpClientError, UpstreamHttpServerError) as exc:
            return FullTextResult(ok=False, error=f"upstream_http_error: {exc}")
        except Exception as exc:  # network, SDK, etc.
            return FullTextResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        try:
            content = resp.scrape_result.get("content", "") or ""
        except Exception:
            content = ""
        if not content:
            return FullTextResult(ok=False, error="empty_response")

        lowered = content.lower()
        if any(m in lowered for m in _GATE_MARKERS):
            return FullTextResult(ok=False, text=_extract_text(content), error="gate_not_cleared")

        text = _extract_text(content)
        if len(text) < _MIN_FULLTEXT_CHARS:
            return FullTextResult(ok=False, text=text, error="short_response")

        return FullTextResult(ok=True, text=text)

    def fetch(self, notice_id: str, email: str, password: str, session: str,
              max_attempts: int = 2) -> FullTextResult:
        result = FullTextResult(ok=False, error="not_attempted")
        for attempt in range(1, max_attempts + 1):
            result = self._fetch_once(notice_id, email, password, session)
            if result.ok or result.error == "quota_exhausted":
                break
            if attempt < max_attempts:
                time.sleep(2)
        return result


def load_checkpoint(path: Path) -> dict:
    results = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                results[rec["notice_id"]] = rec
    return results


def append_checkpoint(path: Path, rec: dict):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True,
                     help="Grid-pull CSV from mddc_trustee_sale_pull.py (needs a notice_id column).")
    ap.add_argument("--out", default="", help="Default: <csv>_full.csv")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--test-id", default="",
                     help="Spike mode: fetch just this one notice_id and print the result, no CSV write.")
    ap.add_argument("--limit", type=int, default=0,
                     help="Only attempt the first N rows with a notice_id (0 = all).")
    ap.add_argument("--checkpoint", default="", help="Default: <csv>_full_checkpoint.jsonl")
    ap.add_argument("--session-prefix", default="mddc-full")
    args = ap.parse_args()

    creds = load_credentials()
    client = MddcScrapflyClient(creds["scrapfly_key"])

    if args.test_id:
        print(f"Spike: fetching notice_id={args.test_id} ...")
        res = client.fetch(args.test_id, creds["email"], creds["password"],
                            session=f"{args.session_prefix}-spike")
        print(f"ok={res.ok} error={res.error!r} text_len={len(res.text)}")
        if res.text:
            print("---- first 1500 chars ----")
            print(res.text[:1500])
            print("---- end excerpt ----")
            auction = gridmod.parse_auction_date(res.text)
            loan = gridmod.parse_loan_principal(res.text)
            addr = gridmod.parse_address(res.text)
            print(f"parsed auction_date={auction!r} loan_principal={loan!r} address={addr!r}")
        return

    csv_path = Path(args.csv)
    out_path = Path(args.out) if args.out else csv_path.with_name(csv_path.stem + "_full.csv")
    checkpoint_path = (Path(args.checkpoint) if args.checkpoint
                        else csv_path.with_name(csv_path.stem + "_full_checkpoint.jsonl"))

    with csv_path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    targets = [r for r in rows if r.get("notice_id")]
    if args.limit:
        targets = targets[: args.limit]
    print(f"{len(rows)} total rows, {len(targets)} have a notice_id and will be attempted")

    checkpoint = load_checkpoint(checkpoint_path)
    pending = [r for r in targets if r["notice_id"] not in checkpoint]
    print(f"{len(checkpoint)} already checkpointed at {checkpoint_path}, {len(pending)} remaining")

    def work(row):
        nid = row["notice_id"]
        session = f"{args.session_prefix}-{nid}"
        res = client.fetch(nid, creds["email"], creds["password"], session=session)
        rec = {"notice_id": nid, "ok": res.ok, "error": res.error, "text": res.text}
        append_checkpoint(checkpoint_path, rec)
        return rec

    if pending:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(work, r): r["notice_id"] for r in pending}
            done_n = 0
            for fut in as_completed(futures):
                rec = fut.result()
                checkpoint[rec["notice_id"]] = rec
                done_n += 1
                status = "OK" if rec["ok"] else f"FAIL({rec['error']})"
                print(f"  [{done_n}/{len(pending)}] {rec['notice_id']}: {status}")

    n_full = n_auction = n_loan = 0
    for r in rows:
        nid = r.get("notice_id")
        rec = checkpoint.get(nid) if nid else None
        if rec and rec.get("ok") and rec.get("text"):
            text = rec["text"]
            r["full_text_fetched"] = "true"
            auction = gridmod.parse_auction_date(text)
            loan = gridmod.parse_loan_principal(text)
            addr = gridmod.parse_address(text) or {}
            if auction:
                r["auction_date"] = auction
                n_auction += 1
            if loan:
                r["loan_principal"] = loan
                n_loan += 1
            if addr.get("street"):
                r["street"] = addr["street"]
                r["city"] = addr["city"]
                r["state"] = addr["state"]
                r["zip"] = addr["zip"]
            n_full += 1
        else:
            r["full_text_fetched"] = "false"

    fieldnames = list(rows[0].keys()) if rows else []
    if "full_text_fetched" not in fieldnames:
        fieldnames.append("full_text_fetched")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    attempted = len(targets)
    print(f"Wrote {out_path}")
    if attempted:
        print(f"{n_full}/{attempted} notices got full text ({n_full / attempted * 100:.0f}%)")
        if n_full:
            print(f"  of those: {n_auction}/{n_full} had a parseable auction_date "
                  f"({n_auction / n_full * 100:.0f}%), {n_loan}/{n_full} had a parseable "
                  f"loan_principal ({n_loan / n_full * 100:.0f}%)")
    else:
        print("No rows had a notice_id -- nothing was attempted.")


if __name__ == "__main__":
    main()
