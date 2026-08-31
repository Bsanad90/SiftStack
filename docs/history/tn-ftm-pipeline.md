# TN First-to-Market Pipeline

> Build history extracted from `CLAUDE.md` on 2026-08-28. Verbatim — the API
> contracts, traps and measured numbers here are the record. Live state and next
> steps live in `CLAUDE.md`.

---

## Notice Screenshots (proof-of-source)

Each scraped notice gets a full-page screenshot of its detail page on tnpublicnotice.com, captured the moment the reCAPTCHA is solved and the legal notice is visible (`notice_screenshot.py::capture_notice_screenshot`, called from `scraper.py` in the kept-notice branch). The image is the actual published notice, used to add legitimacy to outreach.

- **Scope:** foreclosures only by default (`config.NOTICE_SCREENSHOT_TYPES`, comma-separated env override). Toggle the whole feature with `CAPTURE_NOTICE_SCREENSHOTS` (default on). Capture is best-effort: a screenshot failure never drops the record. PNGs land in `output/notices/` (gitignored), named `notice_{ID}.png` by the numeric notice ID.
- **Carried on `NoticeData`:** `notice_screenshot_path` (local PNG, set at scrape) → `notice_screenshot_url` (hosted link, set at output time).
- **Hosting:** Apify run pushes each PNG to the key-value store and sets a shareable URL (mirrors the deep-prospecting PDF pattern). CLI run uploads to Google Drive when `GOOGLE_DRIVE_FOLDER_ID` + `GOOGLE_SERVICE_ACCOUNT_KEY` are set, else falls back to the local path. Helpers: `host_screenshots_via_drive()`, `set_local_screenshot_urls()`.
- **Delivery to DataSift:** the URL rides along as the `Notice Screenshot` custom field plus a "Notice Screenshot:" line in record Notes (`datasift_formatter`). DataSift's CSV upload cannot push an image into the REISift Gallery panel, so the link is the supported route.

## Scheduled First-to-Market Pull (build 1.0.42, 2026-08-14)

The TN Public Notice scrape now runs unattended in the cloud instead of on a workstation, and covers **probate as well as foreclosure** for Knox and Blount. Entry points: `src/ftm_runner.py` (one run) and `src/ftm_schedule.py` (the long-lived scheduler, the container's CMD). Full runbook: `deploy/FTM_RUNBOOK.md`.

```bash
python src/ftm_runner.py --doctor              # credentials, egress, state dir, searches
python src/main.py list-searches               # dump the LIVE saved-search dropdown labels
python src/ftm_runner.py --max-notices 2       # bounded dry run, writes nothing
python src/ftm_runner.py --commit              # the real thing
python src/ftm_schedule.py --next              # next 5 fire times, business-local
```

**THE SCRAPE IS GATED ON EGRESS, NOT ON CODE. This is the finding that reframes everything else.** tnpublicnotice.com decides per-IP whether it will serve notice detail pages at all. Verified live 2026-08-14 against the same logged-in account: from the office IP the page carries **no CAPTCHA whatsoever**, just "You are not permitted to view public notices from this computer at this time"; through an Apify datacenter proxy the same notice serves the normal Turnstile gate and the text; through Scrapfly residential it serves with no gate at all. A Fly machine is a datacenter IP by definition, so `proxy_resolver.py` is mandatory infrastructure, not an optimization. Resolution order: `SIFTSTACK_PROXY_URL` -> `APIFY_PROXY_GROUPS` + `APIFY_TOKEN` (the API token is NOT the proxy password; it is used to look the password up) -> direct. Apify's RESIDENTIAL group is **not on the current plan** (`availableCount: 0`); `BUYPROXIES94952` (27 US datacenter IPs) clears the block today. A run blocked this way exits **3**, distinct from a normal failure, because no retry fixes it. The CLI path previously had no proxy support at all while the Apify Actor did, which is exactly why the scrape worked in the cloud and died on a workstation.

**The gate is Cloudflare TURNSTILE, and the old solver never solved it.** `config.py` had recorded the 2026-07-13 migration but `captcha_solver.py` still called `solver.recaptcha()` and injected into `g-recaptcha-response`, a field the page no longer reads: every solve was billed and discarded. It now selects method and response field off `CAPTCHA_KIND`, reads the sitekey off the **live page** (a rotation logs `SITEKEY ROTATED` rather than silently killing the scrape), creates the `cf-turnstile-response` input when the headless widget never renders one, and runs the blocking 2Captcha call in a thread so the browser event loop keeps servicing the page. Verified live: gate cleared, notice text visible. **The gate is session-level, so one solve covers the rest of the run.** A blocked IP now raises `NoticeAccessBlocked` and aborts the whole run instead of grinding 50 results x 3 attempts against a wall.

**Zero notices is a FAILURE.** Success requires positively seeing the notice body; there is deliberately no "the challenge markup is gone, so we must have passed" inference, which is precisely the reasoning that reported 13 consecutive dead runs as successful over 19 days. `ftm_runner` reports a 0-notice run as EMPTY and exits non-zero.

**Probate (`Probate V2 Knox` / `Probate V2 Blount`, names verified live).** `main.py list-searches` dumps the real dropdown labels and flags configured-but-missing entries, because a mistyped saved search scrapes nothing and looks exactly like a quiet day. Three parsing bugs found by running real notices through the pipeline, all now regression-tested:
- **The PR was the court.** "Notice to Creditors" names the role in prose ("issued to the referenced Personal Representative by the Chancery Court") before naming the human under a standalone `PERSONAL REPRESENTATIVE(S)` heading, so a same-line pattern set `owner_name` to "By The Chancery Court". Patterns are now tried block-form first, a rejected candidate falls through instead of ending the search, and court/clerk prose is in `_INVALID_NAMES`.
- **The courthouse became the subject property.** A probate body's only street addresses belong to the court, the attorney, or the PR, so `_parse_address` now returns immediately for probate (it was uploading "400 W. Main Street", the Knox County courthouse). The real property is resolved downstream by `property_lookup` (Knox Tax API by decedent name -> executor family search -> people search), which works: a live run resolved 1234 Example Dr from decedent "Mary O. Sample".
- **The vacant-land filter deleted the entire type.** It judges by house number and probate has no address yet, so on a mixed run every probate record vanished while the foreclosures came through and the run looked healthy. `NO_ADDRESS_TYPES = {"probate", "divorce"}` is exempt per-record, so the filter keeps doing its real job on types that do carry an address.

**Two more fixes with reach beyond probate:** `max_notices` is now enforced **within** a results page (a cap of 1 still ground through all 50 results, paying a gate solve and screenshot for each, which matters because it is the cloud run's cost ceiling); and `_clean_and_split_name` no longer folds a spelled-out middle name into the surname ("Eric Lee Sharp" uploaded as last name "Lee Sharp", breaking record matching and skip trace), with a surname-particle list so "Van Buren" and "De La Cruz" stay whole.

**Deployment: LIVE on Fly as `siftstack-ftm` (deployed 2026-08-14).** A separate app from the SMS agent's `siftstack` because the shapes are opposite: the SMS agent is a web service that must never stop, this is a 10-40 minute batch job idle the rest of the day, and sharing a machine would put a long scrape in contention with webhook handling. Four deliberate choices: the **Playwright base image is pinned to the client version** (the site is ASP.NET postbacks behind a JS gate, so there is no HTTP-only path); a **volume at `/data`** holds `seen_ids.json`, `last_run.json`, `cookies.json` and `ftm_runs.jsonl`, because losing the seen-ID cache means re-scraping and re-paying for months of notices; a **scheduler process rather than `fly machine run --schedule`**, since Fly's schedules are coarse and pick their own minute while a first-to-market pull wants a specific business-local hour; and **`FTM_ARGS` ships without `--commit`** so the first scheduled run does everything except write. `deploy/sync_ftm_secrets.py` pushes credentials from `.env` in one staged call, masked by default.

**THE 407 THAT LOOKED LIKE BAD CREDENTIALS.** The first Fly deploy could not reach the site at all: 60s timeouts, then `407 Proxy Authentication Required` from Apify on a token and password that were byte-identical to the working local ones. The cause is that **Apify session ids accept only letters, digits, `_` and `.`** and `fly.ftm.toml` set `APIFY_PROXY_SESSION = 'tnpn-fly'`. Proven live from the machine on one credential set: `session-tnpn-fly` 407s while `tnpn_fly`, `tnpnfly`, `tnpn.fly` and `tnpn` all return 200. A hyphen in a session name is indistinguishable from a rejected password in the error, so `proxy_resolver._safe_session()` now rewrites illegal characters and logs the substitution. **The VM is `shared-cpu-2x` / 2GB, not 1x/1gb**: on one shared core Chromium took 60 seconds just to launch and the backfill would have run roughly twice as long as it needs to.

**Backfill: the 1000-row cap is why 12 months was never reachable.** The site truncates EVERY result set at 20 pages / 1000 rows, newest first, so a plain 12-month search silently loses its tail: Knox foreclosure and Knox probate both sat on that ceiling showing only their most recent weeks. The site itself retains 12 months and no more ("Notices for the past 12 months are available in the current search"), so 12 is both the target and the maximum. `--backfill-months N` re-submits each saved search once per calendar month over an explicit date range (`rbRange` + `txtDateFrom`/`txtDateTo`, set after selecting the saved search so its keywords and county checkboxes stay intact), and `--backfill-offset M` shifts that window so one 19-hour job becomes twelve resumable ones. Measured monthly volume: Knox probate ~150, Knox foreclosure ~100, Blount probate ~80, Blount foreclosure ~33, about **4,350 raw notices over 12 months**. Resuming is cheap because the seen-ID check now reads the notice id out of the RESULTS GRID and skips before opening the page (was ~5s per already-seen notice, now zero).

**Blount probate was returning nothing, for two stacked silent reasons.** `property_lookup._tpad_lookup` hit TPAD with bare `requests`: TPAD **403s anything without a browser User-Agent**, so every Blount lookup failed outright. Even fixed, the HTML page only ships an EMPTY table shell whose id is `searchResultsTable`, not the `resultsTable` the parser searched for; the rows come from `POST /TPAD/Search/GetSearchResults`, which returns clean JSON. Both failures were quiet (lookup returns [], address stays empty, validation later drops the record as "missing address"), so a Blount probate backfill would have produced ~950 records and zero usable ones. Also: **TPAD prints the house number LAST** ("LAKESHORE DR  5705"), which fails validation and Smarty unless normalized to "2345 Example Dr". A live test slice went from 0 usable to 3 of 5.

**Trustee sales leak into the probate saved search.** Its keyword is "probate", which also appears in foreclosure notices, so a successor-trustee sale surfaces in probate results and uploads to the Probate list with the trustee's law firm as the personal representative (seen live: a Marinosci Law Group notice produced "PR = From Felicia F. Coalson"). `foreclosure_filter.looks_like_trustee_sale()` drops those, and requires the ABSENCE of a genuine probate anchor (notice to creditors, letters testamentary, personal representative) so a real estate filing that merely mentions a trustee still passes.

**Probate notices never carry a phone number.** Measured on 10 Knox notices: 8 had no phone at all and the 2 that did carried the LAW FIRM's ("The Ebbert Law Firm ... Telephone (865) 234-2488", a successor trustee's office line). The PR is published with a mailing address only, so every probate phone must come from skip trace against the PR name and address; a number lifted from the notice body dials the estate's attorney.

**Notice screenshots retired 2026-08-14** (Ty: not used for anything from TN Public Notice any more). Capture, Drive/Dropbox/KVS hosting and the CSV re-write are removed from the live path and the tooling is in `archive/notice_screenshots/`. It was also the slowest step in a foreclosure notice, which matters directly on a multi-thousand-notice backfill. The `notice_screenshot_path` / `notice_screenshot_url` fields, the CSV column and the DataSift custom-field mapping are deliberately KEPT so historical records retain their URLs.

**BACKFILL COMPLETE 2026-08-16: 1,226 records, 936 distinct properties, all 12 months, 48 of 48 jobs.** Probate 786 / Foreclosure 476; Knox 910 / Blount 352. The daily schedule went live with `--commit` the same day (06:30 America/New_York).

**The constraint that dictated the whole backfill shape: the site blocks an egress IP by VOLUME.** One month running all four searches through a single sticky Apify session viewed about **204 notices** before that IP began refusing, and every later month then failed in ~60s against the same dead IP. Two mechanisms fix it, and both are load-bearing: `ftm_runner` rotates to a fresh Apify session id per PROCESS (counter on the volume), and the backfill runs **one process per (saved search x month)**, 48 jobs, keeping the worst case (Knox probate ~150/month) under the threshold. Even so ~7% of jobs hit a burned IP; a retry pass that re-runs any non-zero exit with a fresh IP converged both stragglers within three passes. Do not "fix" a run of exit-3s by retrying immediately in a tight loop; the pool is 27 addresses and they need to cool.

**seen_ids is persisted ONLY after a successful upload.** The scrape no longer writes it incrementally. Ordering is the whole point: the upload happens after the entire scrape, so persisting mid-scrape meant an aborted run left notices flagged as handled that were never sent anywhere. The first egress block did exactly that to **204 notices**, and a retry would have skipped every one of them permanently. `_revert_seen()` rolls back on failure, on `--no-upload`, and on any dry run.

**Verified in production, not just in tests:** across the 675 post-fix rows checked mid-run there were 0 junk owner names, 0 courthouse addresses, 0 trustee sales on the Probate list, and 0 duplicate rows within a job, while legitimate multi-token surnames survived intact (St. John, St. Leger, Van Zandt, Van Gentry, VAN SAMPLE). Repeats ACROSS months are expected and harmless: a foreclosure republished over a month boundary appears in both files and `POST /property/` upserts by address (confirmed by re-POSTing and getting the same uuid back).

**Still workstation-only:** the `county` stage (`knox_ftm_pull.py`) skips in the container with the reason stated in the run summary, because its buy-box enrichment needs the SiftMap client from the Deal Room `_api` checkout. Vendoring it the way `sms_agent/crm_standalone.py` vendored the reisift client is the next step to get liens, condemnations, trustee deeds and evictions on the same schedule.

**Also fixed here:** `python src/main.py <mode>` used to dispatch into the Apify Actor whenever `APIFY_TOKEN` was present in `.env` (it is, for `consolidate_foreclosures`), so every local CLI call died on "tn_username and tn_password are required". The Actor path now triggers only on `APIFY_IS_AT_HOME` (or an explicit `SIFTSTACK_FORCE_ACTOR=1`). `datasift_api_upload.env()` reads `os.environ` before falling back to a `.env` file, so the uploader works on a box that has no such file. State paths honor `SIFTSTACK_STATE_DIR` / `SIFTSTACK_OUTPUT_DIR` / `SIFTSTACK_LOG_DIR`. Saved-search selection and per-page postbacks wait on `domcontentloaded` rather than `networkidle`, which regularly never settles through a proxy and abandoned a working search after 30s.

## Scraping Backend: Scrapfly (build 1.0.31+)

The gated notice detail fetch (the "caps structure": residential proxy, anti-bot, reCAPTCHA, and the proof-of-source screenshot) can run through the **Scrapfly API** instead of the in-house Playwright + 2Captcha path. Selected by `SCRAPE_BACKEND` (defaults to `scrapfly` when `SCRAPFLY_KEY` is set, otherwise `playwright`).

- **`scrapfly_client.py`** provides `ScrapflyNoticeClient`. `login(session)` logs into Smart Search inside a Scrapfly session (forms-auth cookie + sticky residential IP), then `fetch_notice(id, session)` opens the detail page with `asp=True` + `render_js=True`, a JS scenario clicks "View Notice" (ASP solves the reCAPTCHA), and it returns rendered HTML + a full-page screenshot in one call. `fetch_notices(ids)` logs in once and yields a result per ID. Best-effort with retries; every call returns a `NoticeFetchResult`.
- **Scraper integration** (`scraper.py`): when `SCRAPE_BACKEND == "scrapfly"`, Playwright still drives login + saved-search navigation and supplies each notice ID, but the per-notice content + screenshot come from Scrapfly via `_scrapfly_notice()`. Any Scrapfly failure falls back to the 2Captcha path, so the swap is safe. Returned HTML is parsed by `notice_parser.parse_notice_html()` (shares field extraction with `parse_notice_page`).
- **Screenshots** come natively from Scrapfly (`screenshots={'notice': 'fullpage'}`), saved to `output/notices/` and hosted/linked exactly like the Playwright path.
- **Tooling:** `scrapfly_spike.py --id <id>` validates one notice (gate clears + screenshot) before relying on it. `backfill_screenshots.py [--csv ...]` logs in once and backfills screenshots for a master list (e.g. the output of `consolidate_foreclosures.py`), writing `notice_screenshot_path` / `notice_screenshot_url` back to the CSV.
- **Env:** `SCRAPFLY_KEY` (required), `SCRAPE_BACKEND`, `SCRAPFLY_COUNTRY` (default `us`), `SCRAPFLY_RENDER_WAIT_MS`, `SCRAPFLY_TIMEOUT_MS`, `SCRAPFLY_MAX_RETRIES`. Needs `scrapfly-sdk` (in requirements.txt).
- **Open validation:** whether Scrapfly's ASP clears this site's in-page reCAPTCHA "View Notice" gate is confirmed per-notice by the spike. A `gate_not_cleared` result means the JS scenario action schema or an explicit CAPTCHA step needs a tweak.
- **STATUS 2026-08-14: the route wired into `scraper.py` is the broken one.** `_scrapfly_notice()` calls `fetch_notice()` (a direct `Details.aspx?ID=` fetch), and the client's own `fetch_notice_via_search` docstring explains why that cannot work: every Scrapfly scrape gets a fresh ASP.NET cookieless session, so a detail fetch lands in a session that never ran a search and the server returns an unpopulated shell. Live result is `gate_not_cleared` on every notice, ~3 minutes each, before falling back to Playwright. `fetch_notice_via_search` (search + walk inside ONE call) **does** work: verified live returning real notice content with no gate at all from Scrapfly's residential IP. Until the saved-search equivalent of that in-session walk is built, **leave `SCRAPE_BACKEND=playwright`** (`.env` currently overrides it to `scrapfly`, which is what makes runs slow rather than wrong).

## Foreclosure Master List Consolidation (build 1.0.31+)

`consolidate_foreclosures.py` builds a master list of still-active foreclosures from the last N months of runs. It pulls each Apify run's `output.csv` from the run's key-value store (the default dataset is unused), merges local `output/` CSVs, dedupes by **property** (address + city, keeping the latest sale date so republished/postponed notices collapse to one), and removes any whose `auction_date` ("option date") has already passed. Needs `APIFY_TOKEN`. Output: `output/foreclosure_master_active_<date>.csv`.

```bash
python src/consolidate_foreclosures.py --months 3                  # Apify + local
python src/consolidate_foreclosures.py --months 3 --require-sale-date  # drop no-date junk
python src/consolidate_foreclosures.py --county Knox --no-apify     # local only, one county
```


## FTM Foreclosure: multi-pass skip-trace + screenshot-MMS (2026-06; orchestrated from `_api`)

The FTM foreclosure pipeline (consolidate -> single-family filter -> wizard upload -> phone scoring -> cadence) is orchestrated by `_api/ftm_pipeline.py`; these SiftStack scripts are its skip-trace + texting building blocks. Deep detail: the `_api` CLAUDE.md + the `reisift-tagging-and-phone-scoring` / `smrtphone-mms-screenshot-texting` memories.

- **`src/tracerfy_ftm.py`** — Tracerfy re-skip for FTM records (2nd phone source after the free DataSift enrichment). `--all` traces EVERY record (not just no-phone); `--finish` merges found phones into reisift via Add-Data upsert by ADDRESS into the existing "Foreclosure" list. ~$0.02/record.
- **`src/enformion_ftm.py`** — Enformion/Endato 3rd skip-trace pass. Reuses `enformion_heir.person_search` but for the LIVING OWNER (name + property-address anchor; name alone is HTTP-400'd) -> `enf_phones` -> populate `NoticeData.PHONE_FIELDS` -> same merge path. **`clean_owner_name(raw)`** cuts messy co-owner notice strings (AND/&/AKA/C-O markers, Jr/Sr/II-IV suffixes, middle initials, punctuation) to ONE clean (First,Last) so they don't 400. `--addr "<substr,...>"` re-runs specific records; `--finish` merges. **reisift MERGES phones, so Tracerfy + Enformion ACCUMULATE** — run sequentially, then re-score (`_api/score_ftm_phones.py --commit`) + re-tag (`src/run_phone_tag_upload.py --finish`). Live 2026-06-25: 109 -> 302 phones across 33 records, 32/33 with a Dial 1/2. CWD: run `run_phone_tag_upload.py` from the SiftStack root (relative `output/` path).
- **`src/mms_sender.py`** — GATED browser sender for the foreclosure screenshot-MMS (texts each homeowner the auction-notice Dropbox image + a personal message). Built + validated, **PAUSED pre-send (needs Ty's explicit GO).** Drives the **SmrtPhone web app** (SmrtPhone's API can't do MMS): a 2-step send — the TEXT via the new-message "Compose Message" modal, then the IMAGE via the conversation reply box, which lives in the **`main-iframe`** (`page.frame(name="main-iframe")` -> set the screenshot on its hidden `input[type=file]` -> click the send arrow by `bounding_box()` screen position). Reuses `datasift_core` Playwright primitives. Session captured to `smrtphone_state.json` by `_api/smrtphone_login.py`. Recipients/compose/schedule live in `_api` (`build_mms_recipients.py` pulls from the "FTM - 02 Ready to Call" preset). Full mechanism: the `smrtphone-mms-screenshot-texting` memory.


## Courthouse Photo Pipeline (build 1.0.28+)

Courthouse terminal photos → OCR → LLM parse → enrichment → DataSift. Runner takes phone photos at Knox/Blount county terminals, uploads to Dropbox organized as `{county}/{notice_type}/`, system auto-processes.

### Notice Types (7 total)
- `foreclosure`, `tax_sale`, `tax_delinquent`, `probate` — existing from web scraper
- `eviction` — plaintiff = landlord (target contact), defendant = tenant
- `code_violation` — owner of record, violation type, compliance deadline
- `divorce` — petitioner + respondent, property from schedule page

### Critical OCR Patterns (hard-won from live testing)

**Moire pattern from terminal screens is the #1 OCR killer.** Standard Tesseract preprocessing (adaptive threshold, CLAHE) produces garbage on courthouse terminal photos. The fix:
- **Bilateral filter** (`cv2.bilateralFilter(gray, 15, 75, 75)`) removes moire while preserving text edges
- **Otsu threshold** (`cv2.THRESH_BINARY + cv2.THRESH_OTSU`) after bilateral — auto-determines optimal binary threshold
- **PSM 4** (single column variable text) for terminal screens — NOT PSM 6 (single uniform block) which was the research recommendation but fails in practice
- **Do NOT use `fix_rotation()` (Tesseract OSD) on phone photos** — EXIF transpose handles rotation. OSD on raw phone images often fails and the 270° fallback rotates correct images sideways

### Probate Deep Prospecting (from courthouse terminals)

Courthouse probate records have decedent name + PR/executor name but NO property address. Multi-tier lookup fills the gap:

**Property Address Lookup** (Step 3c in enrichment pipeline):
1. **Tier 1: Knox Tax API name search** — search `/parcels/{decedent_name}`, score by token overlap (FIRST MIDDLE LAST → LAST FIRST MIDDLE), accept >= 0.4 match. Tries multiple name variations (with/without suffix, LAST FIRST format, first+last only).
2. **Tier 2: Executor family search** — search Knox Tax API by executor name, look for properties where decedent's last name appears in owner field (family property transferred to executor).
3. **Tier 3: People search** — search TruePeopleSearch/FastPeopleSearch for decedent's last known Knox County address.

**Probate Preset** (obituary enricher):
- Triggers when court record has PR name + decedent name (no address required) — prevents wrong obituary from overriding court-named executor
- Sets DM = the named PR/executor directly, skips obituary search entirely
- Then runs DM address lookup (Knox Tax API → People Search → Tracerfy)

**DOD Sanity Check** (obituary enricher):
- Rejects obituary matches where DOD is > 3 years before the notice **publication** date (`MAX_DOD_GAP_YEARS = 3`)
- Prevents matching a 2014 obituary to a 2025 court filing (wrong person with same name)
- Applied to both full-page and snippet matches
- Anchors on `date_published` (the legal publication date), falling back to `date_added` — NOT `date_added` alone, which is now the run date (see "Date Semantics" under Output)


### Dropbox Folder Structure
```
{DROPBOX_ROOT_FOLDER}/
├── Knox/
│   ├── eviction/
│   ├── code_violation/
│   ├── divorce/
│   ├── foreclosure/
│   ├── tax_sale/
│   └── probate/
└── Blount/
    └── (same subfolders)
```

### Environment Variables
- `DROPBOX_APP_KEY` — Dropbox OAuth2 app key
- `DROPBOX_APP_SECRET` — Dropbox OAuth2 app secret
- `DROPBOX_REFRESH_TOKEN` — Dropbox offline refresh token (auto-rotates access tokens)
- `DROPBOX_POLL_INTERVAL` — seconds between polls (default 900 = 15 min)
- `DROPBOX_ROOT_FOLDER` — root folder path in Dropbox (e.g., "TN Public Notice")

### Dependencies (added to requirements.txt)
- `opencv-python-headless>=4.13.0` — image preprocessing (headless = no GUI, saves 26MB in Docker)
- `numpy>=1.26.0` — required by OpenCV
- `dropbox>=12.0.2` — Dropbox SDK (minimum for post-Jan-2026 API compatibility)

