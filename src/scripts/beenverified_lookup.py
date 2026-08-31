"""BeenVerified people-search runner - Source 3 of the deep-prospecting waterfall.

BeenVerified is a paid consumer subscription with no API and a Cloudflare front, so
this drives the user's OWN logged-in session with Playwright, the same pattern
src/mms_sender.py uses for smrtPhone: capture storage_state once in a headed
browser, then reuse it headlessly.

Deliberately NOT routed through Scrapfly. The deep-prospecting skill records that
hardened people-search aggregators (TruePeopleSearch, FastPeopleSearch) IP-ban
Scrapfly ASP with SHIELD_PROTECTION_FAILED. An authenticated paid session is a
different thing from an anonymous scrape and is the route that actually works.

    # 1. once (a real browser opens; log in by hand, then press Enter here)
    python src/scripts/beenverified_lookup.py login

    # 2. then, as often as needed
    python src/scripts/beenverified_lookup.py search --worklist wl.json --out web.json
    python src/scripts/beenverified_lookup.py search --name "John Sample" --state VA
    python src/scripts/beenverified_lookup.py search --address "123 Example Rd, Spotsylvania, VA 22551"

Output is written in the shape src/dispo_skiptrace.py --web already consumes:
    {label: {"phones": {"5405551234": {}}, "emails": [...], ...}}
so results merge into the existing pipeline and then Trestle-score through
src/phone_validator.py.

Pacing is deliberate and slow. This is a human-rate subscription, not a bulk API;
hammering it is how an account gets flagged.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright  # noqa: E402

import datasift_core as dc  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "beenverified_state.json"
BASE = "https://www.beenverified.com"

PHONE_RE = re.compile(r"\(?\b(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
# Support-line and marketing numbers that appear in the chrome of every page.
NOISE_PHONES = {"8888793735", "8446276383", "8005551212"}


def digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


async def _pace(lo: float = 2.5, hi: float = 5.5) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


# ── session capture ───────────────────────────────────────────────────

async def do_login(minutes: int = 10) -> int:
    """Open a real browser and POLL until the human has logged in.

    Deliberately not an input() prompt: this is normally launched from an agent
    harness whose stdin is the null device, where input() reads EOF instantly and
    the window would slam shut before anyone could type a password.
    """
    deadline = asyncio.get_event_loop().time() + minutes * 60
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(viewport=dc.DEFAULT_VIEWPORT,
                                            user_agent=dc.DEFAULT_USER_AGENT)
        page = await context.new_page()
        await page.goto(BASE + "/login/", wait_until="domcontentloaded")
        print("=" * 70, flush=True)
        print("A browser window is open. Log in to BeenVerified by hand.", flush=True)
        print("Solve any CAPTCHA or 2FA there - this script will not touch it.", flush=True)
        print(f"It polls for up to {minutes} min and saves itself once you are in.", flush=True)
        print("=" * 70, flush=True)

        saved = False
        seen_tabs = 1
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(3)
            # Poll EVERY tab in the context, not just the one we opened. Login
            # flows routinely hand off to a new tab (SSO, "sign in" popups), and
            # watching only the original page reports a timeout while the user is
            # in fact logged in one tab over.
            pages = [p for p in context.pages if not p.is_closed()]
            if not pages:
                print("every tab was closed before login completed; nothing saved", flush=True)
                return 1
            if len(pages) != seen_tabs:
                print(f"({len(pages)} tab(s) open)", flush=True)
                seen_tabs = len(pages)
            for p in pages:
                try:
                    url = p.url
                    if "/login" in url.lower() or "/signin" in url.lower():
                        continue
                    body = (await p.inner_text("body")).lower()
                except Exception:
                    continue
                # Require a positive logged-in marker. "the login form is gone" is
                # the inference that reports dead sessions as healthy.
                if any(k in body for k in ("sign out", "log out", "my account",
                                           "dashboard", "search history")):
                    await context.storage_state(path=str(STATE))
                    saved = True
                    print(f"detected a logged-in session at {url}", flush=True)
                    break
            if saved:
                break

        await browser.close()

    if not saved:
        print(f"TIMED OUT after {minutes} min without seeing a logged-in page. Nothing saved.", flush=True)
        return 1
    print(f"saved session -> {STATE}", flush=True)
    return 0


# ── extraction ────────────────────────────────────────────────────────

def _harvest(text: str) -> tuple[set[str], set[str]]:
    phones = {digits("".join(m)) for m in PHONE_RE.findall(text)}
    phones = {p for p in phones if len(p) == 10 and p not in NOISE_PHONES
              and not p.startswith(("800", "888", "877", "866", "855", "844", "833"))}
    emails = {e.lower() for e in EMAIL_RE.findall(text)
              if not e.lower().endswith(("beenverified.com", "example.com"))}
    return phones, emails


# A report renders phones as "(864) 555-0147 \n OtherPhone \n Aug 2015 - May 2021".
# The type and the date range are worth far more than the bare digits: a Mobile last
# seen this year beats a Work Phone that went quiet in 2017, and neither is visible
# in a naked digit scrape.
PHONE_BLOCK_RE = re.compile(
    r"\((\d{3})\)\s*(\d{3})-(\d{4})\s*\n+\s*([A-Za-z ]*Phone|Mobile|Landline)?\s*\n*\s*"
    r"((?:[A-Z][a-z]{2} \d{4}) - (?:[A-Z][a-z]{2} \d{4}))?", re.M)
RELATIVE_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+)+)\s*\n"
    r"Possible relative: (?P<rel>[^\n]+)\s*\n+"
    r"Age: (?P<age>\d+)\s*\n+"
    r"Lives in: (?P<loc>[^\n]+)", re.M)
SUBJECT_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+)+)\s*\n"
    r"Age: (?P<age>\d+), Born (?P<born>[A-Za-z]{3},? \d{4})\s*\n"
    r"Location: (?P<loc>[^\n]+)", re.M)


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z ]", "", (s or "").lower()).strip()


def _parse_report(body: str) -> dict:
    """Pull the structured blocks out of a rendered person report."""
    out = {"subject": {}, "phones": {}, "emails": [], "relatives": []}

    m = SUBJECT_RE.search(body)
    if m:
        out["subject"] = {"name": m.group("name"), "age": int(m.group("age")),
                          "born": m.group("born"), "location": m.group("loc").strip()}

    for a, b, c, ptype, seen in PHONE_BLOCK_RE.findall(body):
        d = a + b + c
        if len(d) != 10 or d in NOISE_PHONES or d.startswith(("800", "888", "877", "866", "855", "844", "833")):
            continue
        prev = out["phones"].get(d, {})
        # Keep the richest annotation if the same number appears twice.
        out["phones"][d] = {"type": (ptype or prev.get("type") or "").strip(),
                            "last_seen": (seen or prev.get("last_seen") or "").strip()}

    _, emails = _harvest(body)
    out["emails"] = sorted(emails)

    seen_rel = set()
    for m in RELATIVE_RE.finditer(body):
        key = _norm_name(m.group("name"))
        if key in seen_rel:
            continue
        seen_rel.add(key)
        out["relatives"].append({"name": m.group("name").strip(),
                                 "relationship": m.group("rel").strip(),
                                 "age": int(m.group("age")),
                                 "lives_in": m.group("loc").strip()})
    return out


async def _open_report(page, url: str) -> dict:
    """Open one report and wait for it to actually build."""
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    body = ""
    for _ in range(6):
        await asyncio.sleep(5)
        try:
            body = await page.inner_text("body")
        except Exception:
            continue
        if len(body) > 6000 and ("Possible Phone Numbers" in body or "Relatives" in body):
            break
    return _parse_report(body)


async def _search_one(page, label: str, query: str, kind: str, anchor: str) -> dict:
    """Search, pick the EXACT match, then open that report and harvest it."""
    if kind == "address":
        url = f"{BASE}/rf/search/address?address={query}"
    else:
        parts = query.split()
        first, last = parts[0], " ".join(parts[1:]) or ""
        url = f"{BASE}/rf/search/person?fname={first}&ln={last}"
        if anchor:
            url += f"&state={anchor}"
    url = url.replace(" ", "%20")

    out = {"query": query, "kind": kind, "anchor": anchor, "url": url, "subject": {},
           "phones": {}, "emails": [], "relatives": [], "candidates": [], "note": ""}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await _pace(6, 9)
        body = await page.inner_text("body")
    except Exception as e:
        out["note"] = f"search failed: {type(e).__name__}: {str(e)[:140]}"
        return out

    low = body.lower()
    if "sign in" in low and "dashboard" not in low and len(body) < 4000:
        out["note"] = "SESSION EXPIRED - re-run `login`"
        return out
    if len(body) < 800:
        out["note"] = f"results page suspiciously small ({len(body)} chars) - possible bot wall"
        return out

    m = re.search(r"(\d+) exact match(?:es)? and (\d+) other match", body)
    if m:
        out["note"] = f"{m.group(1)} exact, {m.group(2)} other"

    # Pair each report link with its own card text, so the person is chosen by NAME
    # rather than by page order. Taking the first link is how you end up harvesting
    # the spouse's report and reporting it as the target's.
    cards = await page.eval_on_selector_all(
        'a[href*="/rf/report/person"]',
        """els => els.map(e => {
             let n = e; for (let i = 0; i < 6 && n.parentElement; i++) n = n.parentElement;
             return {href: e.getAttribute('href'), text: (n.innerText || '').slice(0, 300)};
        })""")

    target = _norm_name(query)
    exact, loose = [], []
    for c in cards:
        first_line = (c["text"] or "").strip().split("\n")[0]
        nm = _norm_name(re.sub(r",.*$", "", first_line))
        entry = {"name": first_line.strip(), "href": c["href"]}
        if not nm:
            continue
        toks_t, toks_n = set(target.split()), set(nm.split())
        if toks_t and toks_t <= toks_n:
            exact.append(entry)
        elif toks_t and len(toks_t & toks_n) >= 2:
            loose.append(entry)
    out["candidates"] = [e["name"] for e in (exact + loose)[:8]]

    pick = (exact or loose)
    if not pick:
        out["note"] = (out["note"] + " | " if out["note"] else "") + \
            f"no card matched '{query}' by name; not opening a report on a guess"
        return out

    try:
        rep = await _open_report(page, pick[0]["href"])
    except Exception as e:
        out["note"] = f"report failed: {type(e).__name__}: {str(e)[:140]}"
        return out

    out.update({k: rep[k] for k in ("subject", "phones", "emails", "relatives")})
    # Check the GIVEN name, not "any shared token". On a family search the surname is
    # shared by everyone, so a token-overlap test silently accepts the spouse's report
    # as the target's - which is exactly what it did on the first live run.
    got_name = rep.get("subject", {}).get("name", "")
    got = _norm_name(got_name).split()
    want = target.split()
    if got and want and got[0] != want[0]:
        out["subject_mismatch"] = True
        out["note"] = (out["note"] + " | " if out["note"] else "") + \
            f"SUBJECT MISMATCH: searched '{query}', opened '{got_name}'. " \
            f"Phones below belong to {got_name}, NOT {query}."
    if not out["phones"]:
        out["note"] = (out["note"] + " | " if out["note"] else "") + "report carried no phones"
    return out


# ── run ───────────────────────────────────────────────────────────────

async def do_search(items: list[dict], out_path: Path) -> int:
    if not STATE.exists():
        print(f"FATAL: no BeenVerified session at {STATE}.")
        print("Run:  python src/scripts/beenverified_lookup.py login")
        return 2

    results: dict[str, dict] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(storage_state=str(STATE),
                                            viewport=dc.DEFAULT_VIEWPORT,
                                            user_agent=dc.DEFAULT_USER_AGENT)
        page = await context.new_page()
        for i, it in enumerate(items, 1):
            label = it["label"]
            print(f"[{i}/{len(items)}] {label} ...", flush=True)
            r = await _search_one(page, label, it["query"], it.get("kind", "person"),
                                  it.get("anchor", ""))
            results[label] = r
            print(f"      phones={len(r['phones'])} emails={len(r['emails'])}"
                  + (f"  NOTE: {r['note']}" if r["note"] else ""), flush=True)
            if r["note"].startswith("SESSION EXPIRED"):
                print("      stopping: the session is dead, nothing below would work either")
                break
            await _pace()
        # Re-save state so a refreshed cookie is not lost.
        try:
            await context.storage_state(path=str(STATE))
        except Exception:
            pass
        await browser.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=1), encoding="utf-8")
    found = sum(len(r["phones"]) for r in results.values())
    print(f"\nwrote {out_path}  ({len(results)} searches, {found} phone numbers)")
    if not found:
        print("ZERO phones across every search. That is a failure to investigate, not a result:")
        print("  - session may be expired          -> re-run `login`")
        print("  - reports may need a click to open -> inspect a page headed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["login", "search"])
    ap.add_argument("--worklist", help="JSON list of {label, query, kind, anchor}")
    ap.add_argument("--name", help="single person search, e.g. \"John Sample\"")
    ap.add_argument("--address", help="single reverse-address search")
    ap.add_argument("--state", default="", help="two-letter state anchor for --name")
    ap.add_argument("--out", default="output/deep_prospecting/beenverified_results.json")
    ap.add_argument("--minutes", type=int, default=10, help="login: how long to poll")
    a = ap.parse_args()

    if a.cmd == "login":
        return asyncio.run(do_login(a.minutes))

    items: list[dict] = []
    if a.worklist:
        items = json.loads(Path(a.worklist).read_text(encoding="utf-8"))
    elif a.name:
        items = [{"label": a.name, "query": a.name, "kind": "person", "anchor": a.state}]
    elif a.address:
        items = [{"label": a.address, "query": a.address, "kind": "address"}]
    else:
        ap.error("search needs --worklist, --name or --address")
    return asyncio.run(do_search(items, ROOT / a.out))


if __name__ == "__main__":
    sys.exit(main())
