"""Probe the DC Recorder of Deeds search surface (washington.dc.publicsearch.us).

Phase 0a of the HOA-lien harvest plan. Answers the questions left OPEN in
output/dpd_dc_ftm_investigation.md section 2:
  - does the UI expose document-type + recorded-date-range search?
  - what are the exact HOA/condo lien document-type names in its type list?
  - does the results index carry Square/Suffix/Lot (-> direct ITSPE SSL join)
    or party names only?
  - is there an export?
  - what JSON endpoint sits under the Kofile SPA (the docs/api/county-data.md
    "general method": watch the wire, not the widgets)?

Read-only against the portal: logs in with DC_ROD_EMAIL / DC_ROD_PASSWORD from
.env, opens the search UI, captures every /api/ request+response, dumps the
document-type vocabulary, and runs ONE small sample search for lien-ish types
over a short recorded-date window. Buys nothing ($4 images are never touched).

    python tools/manual/probe_dc_rod.py            # headless
    python tools/manual/probe_dc_rod.py --headed   # watch it

Evidence lands in output/dc_rod_probe_<ts>.json + output/dc_rod_probe_*.png.
Every stage is fault-tolerant: a failed stage records its error and the later
stages still run, so one bad selector never zeroes the evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "https://washington.dc.publicsearch.us"
TS = datetime.now().strftime("%Y%m%dT%H%M%S")
OUT_JSON = ROOT / "output" / f"dc_rod_probe_{TS}.json"
SHOT = ROOT / "output" / f"dc_rod_probe_{TS}"

# responses whose URL matches this are captured with their JSON body
API_RE = re.compile(r"/api/|/graphql", re.I)
MAX_BODY = 200_000  # chars of JSON body kept per response

LIEN_WORD_RE = re.compile(r"lien", re.I)


def load_creds() -> tuple[str, str]:
    load_dotenv(ROOT / ".env")
    email = os.getenv("DC_ROD_EMAIL", "").strip()
    password = os.getenv("DC_ROD_PASSWORD", "").strip()
    if not email or not password:
        raise SystemExit("DC_ROD_EMAIL / DC_ROD_PASSWORD missing from .env")
    return email, password


def say(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def dump_controls(page) -> dict:
    """Everything interactive on the page: inputs, selects, buttons, labels."""
    return page.evaluate(
        """() => {
        const vis = el => { const r = el.getBoundingClientRect();
                            return r.width > 0 && r.height > 0; };
        const inputs = [...document.querySelectorAll('input, textarea')].filter(vis).map(el => ({
            tag: el.tagName.toLowerCase(), type: el.type || null, id: el.id || null,
            name: el.name || null, placeholder: el.placeholder || null,
            aria: el.getAttribute('aria-label'), cls: (el.className || '').toString().slice(0, 120),
        }));
        const selects = [...document.querySelectorAll('select')].filter(vis).map(el => ({
            id: el.id || null, name: el.name || null,
            options: [...el.options].map(o => o.text).slice(0, 400),
        }));
        const buttons = [...document.querySelectorAll('button, a[role="button"], [type="submit"]')]
            .filter(vis).map(el => (el.innerText || '').trim()).filter(t => t && t.length < 60);
        const labels = [...document.querySelectorAll('label, legend, h1, h2, h3, h4, th')]
            .filter(vis).map(el => (el.innerText || '').trim()).filter(t => t && t.length < 80);
        return { inputs, selects, buttons: [...new Set(buttons)], labels: [...new Set(labels)] };
    }"""
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--days", type=int, default=30,
                    help="recorded-date window for the sample search (default 30)")
    a = ap.parse_args()

    email, password = load_creds()
    evidence: dict = {"at": datetime.now().isoformat(timespec="seconds"),
                      "base": BASE, "stages": {}, "api_log": []}

    def stage(name: str, fn) -> None:
        try:
            evidence["stages"][name] = {"ok": True, "result": fn()}
            say(f"stage {name}: ok")
        except Exception as e:  # noqa: BLE001 - probe must keep going
            evidence["stages"][name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            say(f"stage {name}: FAILED {type(e).__name__}: {e}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not a.headed)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950})
        page = ctx.new_page()

        def on_response(resp):
            try:
                rt = resp.request.resource_type
                # capture ALL xhr/fetch traffic (run 1 proved the backend does
                # not live under /api/), plus anything matching the old pattern
                if rt not in ("xhr", "fetch") and not API_RE.search(resp.url):
                    return
                post = (resp.request.post_data or "")[:5000] or None
                if post and ("password" in post or "/signin" in resp.url):
                    post = "<REDACTED credentials>"
                entry = {"url": resp.url, "status": resp.status,
                         "method": resp.request.method,
                         "resource_type": rt,
                         "post_data": post}
                ct = (resp.headers or {}).get("content-type", "")
                if "json" in ct and resp.status < 400:
                    try:
                        entry["body"] = resp.text()[:MAX_BODY]
                    except Exception:  # noqa: BLE001
                        entry["body"] = "<unreadable>"
                evidence["api_log"].append(entry)
            except Exception:  # noqa: BLE001 - never break the run from a listener
                pass

        page.on("response", on_response)

        # ---- stage 1: landing page --------------------------------------
        def s_landing():
            page.goto(BASE, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(4000)
            page.screenshot(path=f"{SHOT}_1_landing.png", full_page=True)
            return {"url": page.url, "title": page.title(), "controls": dump_controls(page)}

        stage("landing", s_landing)

        # ---- stage 2: login ---------------------------------------------
        def s_login():
            clicked = None
            for text in ("Log In", "Login", "Sign In", "Sign in", "LOG IN", "SIGN IN"):
                loc = page.locator(f"text={text}").first
                if loc.count() and loc.is_visible():
                    loc.click()
                    clicked = text
                    break
            if clicked is None:
                page.goto(f"{BASE}/login", wait_until="domcontentloaded", timeout=60_000)
                clicked = "/login direct nav"
            page.wait_for_timeout(2500)
            page.screenshot(path=f"{SHOT}_2_loginform.png", full_page=True)
            # fill by input type; Kofile's form is a plain email+password pair
            em = page.locator('input[type="email"], input[name*="mail" i], input[id*="mail" i]').first
            pwd = page.locator('input[type="password"]').first
            em.fill(email)
            pwd.fill(password)
            btn = page.locator('button[type="submit"], button:has-text("Log"), button:has-text("Sign")').first
            btn.click()
            page.wait_for_timeout(5000)
            page.screenshot(path=f"{SHOT}_3_after_login.png", full_page=True)
            body = page.inner_text("body")[:2000]
            logged_in = password not in body and not page.locator('input[type="password"]').first.is_visible() \
                if page.locator('input[type="password"]').count() else True
            return {"clicked": clicked, "url": page.url, "looks_logged_in": bool(logged_in),
                    "body_head": body[:600]}

        stage("login", s_login)

        # ---- stage 3: advanced search surface ---------------------------
        def s_advanced():
            for text in ("Advanced Search", "Advanced", "ADVANCED SEARCH"):
                loc = page.locator(f"text={text}").first
                if loc.count() and loc.is_visible():
                    loc.click()
                    page.wait_for_timeout(2500)
                    break
            page.screenshot(path=f"{SHOT}_4_advanced.png", full_page=True)
            return {"url": page.url, "controls": dump_controls(page)}

        stage("advanced_search_surface", s_advanced)

        # ---- stage 4: document-type vocabulary --------------------------
        def _doctype_options(seed: str) -> list[str]:
            """Type a seed into the #docTypes tokenized select, harvest the
            option list, clear the input."""
            inp = page.locator("#docTypes").first
            inp.click()
            inp.fill(seed)
            page.wait_for_timeout(1500)
            opts = page.evaluate(
                """() => [...document.querySelectorAll('[role="option"], li')]
                    .map(el => (el.innerText || '').trim())
                    .filter(t => t && t.length < 80).slice(0, 300)"""
            )
            inp.fill("")
            page.keyboard.press("Escape")
            return sorted({o for o in opts if o})

        def s_doctypes():
            page.goto(f"{BASE}/search/advanced", wait_until="domcontentloaded",
                      timeout=60_000)
            page.wait_for_timeout(2500)
            vocab = {}
            for seed in ("lien", "hoa", "association", "condo", "notice", "foreclos"):
                try:
                    vocab[seed] = _doctype_options(seed)
                except Exception as e:  # noqa: BLE001
                    vocab[seed] = [f"<error: {e}>"]
            page.screenshot(path=f"{SHOT}_5_doctypes.png", full_page=True)
            return vocab

        stage("doctype_vocabulary", s_doctypes)

        # ---- stage 5: real advanced search: CONDO LIEN, N days -----------
        def _add_doctype_token(name: str) -> bool:
            """Type into #docTypes and JS-click the dropdown row whose text is
            exactly the type name (the :text-is locator missed in run 2)."""
            inp = page.locator("#docTypes").first
            inp.click()
            inp.fill(name)
            page.wait_for_timeout(1500)
            clicked = page.evaluate(
                """(name) => {
                    const cands = [...document.querySelectorAll('[role="option"], li, div')]
                        .filter(el => (el.innerText || '').trim() === name
                                      && el.getBoundingClientRect().height > 0
                                      && el.getBoundingClientRect().height < 80);
                    if (!cands.length) return false;
                    cands[cands.length - 1].click();
                    return true;
                }""", name)
            page.wait_for_timeout(600)
            if not clicked:
                page.keyboard.press("Escape")
            return bool(clicked)

        def _read_results(tag: str) -> dict:
            page.wait_for_timeout(8000)
            page.screenshot(path=f"{SHOT}_{tag}.png", full_page=True)
            body = page.inner_text("body")
            rows = page.evaluate(
                """() => [...document.querySelectorAll(
                       'tr, [class*="result" i] [class*="row" i], [class*="card" i]')]
                    .map(el => (el.innerText || '').trim().replace(/\\n+/g, ' | '))
                    .filter(t => t && t.length > 30 && t.length < 600).slice(0, 12)"""
            )
            return {"url": page.url, "result_rows": rows,
                    "count_lines": [l.strip() for l in body.splitlines()
                                    if re.search(r"results?|matches", l, re.I)
                                    and len(l.strip()) < 120][:6],
                    "mentions_export": bool(re.search(r"export|download", body, re.I))}

        def s_sample_search():
            since_c = (datetime.now() - timedelta(days=a.days)).strftime("%Y%m%d")
            until_c = datetime.now().strftime("%Y%m%d")
            # attempt 1: URL-driven (run 1 showed /results is param-addressable)
            url = (f"{BASE}/results?department=RP&docTypes=CONDO%20LIEN"
                   f"&recordedDateRange={since_c}%2C{until_c}&searchType=advancedSearch")
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            direct = _read_results("6_url_results")
            out = {"direct_url": url, "direct": direct}
            if not direct["result_rows"]:
                # attempt 2: drive the UI
                page.goto(f"{BASE}/search/advanced", wait_until="domcontentloaded",
                          timeout=60_000)
                page.wait_for_timeout(2500)
                since = (datetime.now() - timedelta(days=a.days)).strftime("%m/%d/%Y")
                until = datetime.now().strftime("%m/%d/%Y")
                out["added_tokens"] = {t: _add_doctype_token(t) for t in (
                    "CONDO LIEN", "CONDO LIEN RELEASE", "LIEN", "LIEN RELEASE")}
                page.locator("#recordedDateRange-start").fill(since)
                page.locator("#recordedDateRange-end").fill(until)
                page.screenshot(path=f"{SHOT}_7_filled.png", full_page=True)
                page.locator('button:text-is("Search")').first.click()
                out["ui"] = _read_results("8_ui_results")
            return out

        stage("sample_search", s_sample_search)

        browser.close()

    OUT_JSON.write_text(json.dumps(evidence, indent=1), encoding="utf-8")
    say(f"wrote {OUT_JSON} ({len(evidence['api_log'])} api calls captured)")
    say(f"screenshots: {SHOT}_*.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
