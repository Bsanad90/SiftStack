"""Probe: does MD Land Records support a county-wide DOCUMENT-TYPE search?

Phase 0b of the HOA-lien harvest plan. The evidence conflicts: the DPD FTM
registry note says mdlandrec searches by "name, liber/folio, or tax account
number" only, while MSA web material suggests an instrument-type + date-range
mode exists. Nobody has probed the live form -- run_doctor() only dumps the
county dropdown. This settles it.

Two Firecrawl calls against landrec.msa.maryland.gov (login + Montgomery):
  1. the default search page -- dump every sidebar mode link, every select
     (with options) and every input on the page;
  2. the same after clicking the sidebar's "Instruments" link (if present) --
     the mode most likely to carry an instrument-type search.

Verdict + full control dump land in output/md_landrec_doctype_probe_<ts>.json.
Read-only against the site; costs 2 Firecrawl scrapes.

    python tools/manual/probe_md_landrec_doctype.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

from bs4 import BeautifulSoup  # noqa: E402

import md_land_records_lookup as landrec  # noqa: E402


def dump_controls(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a"):
        t = a.get_text(" ", strip=True)
        if t and len(t) < 50:
            links.append({"text": t, "href": (a.get("href") or "")[:120],
                          "id": a.get("id")})
    selects = []
    for s in soup.find_all("select"):
        selects.append({"id": s.get("id"), "name": s.get("name"),
                        "options": [o.get_text(strip=True) for o in s.find_all("option")][:200]})
    inputs = []
    for i in soup.find_all("input"):
        if i.get("type") in ("hidden",):
            continue
        inputs.append({"id": i.get("id"), "name": i.get("name"),
                       "type": i.get("type"), "value": (i.get("value") or "")[:60]})
    labels = [el.get_text(" ", strip=True) for el in soup.find_all(["label", "legend", "h1", "h2", "h3"])
              if el.get_text(strip=True) and len(el.get_text(strip=True)) < 80]
    return {"links": links, "selects": selects, "inputs": inputs, "labels": labels,
            "html_len": len(html)}


def main() -> int:
    creds = landrec.load_credentials()
    county_id = landrec.LANDREC_COUNTY_ID["Montgomery"]
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = {"at": datetime.now().isoformat(timespec="seconds"), "county": "Montgomery"}

    base_actions = landrec._login_actions(creds) + [
        {"type": "executeJavascript",
         "script": landrec._set_select_js("#body_ddlbarcounties", county_id)},
        {"type": "wait", "milliseconds": 4000},
    ]

    # call 1: the default search page after county selection
    html1 = landrec.firecrawl_scrape_retry(
        landrec.LANDREC_LOGIN_URL, base_actions + [{"type": "scrape"}], timeout=120)
    landrec._assert_logged_in(html1)
    out["default_page"] = dump_controls(html1)
    print(f"[1] default page: {len(html1)} chars, "
          f"{len(out['default_page']['selects'])} selects, "
          f"{len(out['default_page']['links'])} links", flush=True)

    # call 2: click the sidebar "Instruments" mode (text click via JS)
    click_instruments = """
        (function() {
            var links = Array.prototype.slice.call(document.querySelectorAll('a'));
            var el = links.find(function(a) {
                return a.textContent.trim().toLowerCase() === 'instruments'; });
            if (el) el.click();
        })();
    """
    html2 = landrec.firecrawl_scrape_retry(
        landrec.LANDREC_LOGIN_URL,
        base_actions + [
            {"type": "executeJavascript", "script": click_instruments},
            {"type": "wait", "milliseconds": 5000},
            {"type": "scrape"},
        ], timeout=120)
    out["instruments_page"] = dump_controls(html2)
    print(f"[2] instruments page: {len(html2)} chars, "
          f"{len(out['instruments_page']['selects'])} selects", flush=True)

    # verdict: any select whose options look like instrument types, or any
    # date-range control not tied to a name field
    def looks_like_doctype(sel: dict) -> bool:
        opts = " ".join(sel.get("options") or []).upper()
        return ("LIEN" in opts or "DEED OF TRUST" in opts) and len(sel.get("options") or []) > 5

    hits = []
    for pg in ("default_page", "instruments_page"):
        for sel in out[pg]["selects"]:
            if looks_like_doctype(sel):
                hits.append({"page": pg, "select": sel["id"] or sel["name"],
                             "n_options": len(sel["options"])})
    out["doctype_selects"] = hits
    out["verdict"] = ("DOC-TYPE SEARCH EXISTS" if hits else
                      "NO doc-type select found on either page")

    path = ROOT / "output" / f"md_landrec_doctype_probe_{ts}.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"VERDICT: {out['verdict']}")
    if hits:
        for h in hits:
            print(f"  {h}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
