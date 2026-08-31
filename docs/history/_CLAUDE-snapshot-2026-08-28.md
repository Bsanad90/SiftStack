# CLAUDE.md — SiftStack

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**SiftStack** — Full-stack real estate investing operations platform built around DataSift.ai CRM. Covers the entire REI business lifecycle:

1. **Data Acquisition:** Web scraping tnpublicnotice.com (foreclosures, tax sales, probates), scanned PDF import, courthouse terminal photo import (probate, eviction, code violations, divorce), Dropbox auto-polling
2. **Enrichment Pipeline:** 10+ steps — Smarty address standardization, Zillow property data, Knox County Tax API, obituary/heir research, Ancestry.com SSDI, Tracerfy skip trace, Trestle phone scoring, entity research
3. **Deal Analysis:** Comparable sales (Two-Bucket ARV), rehab estimation (4-tier room-by-room), deal analyzer (MAO/ROI/financing scenarios)
4. **Market Intelligence:** Zip code scoring, Market Finder reports, cash buyer list building, investor portfolio analysis
5. **CRM Automation:** DataSift upload, 26 TCA sequence templates, 12 niche sequential marketing presets, filter preset management, SiftMap sold property tagging
6. **Lead Management:** 4 Pillars of Motivation auto-qualification, STABM daily routine, pipeline reporting, deep prospecting (4-level framework)
7. **Operations:** Acquisition playbook generator (SOPs, scripts, checklists), Slack/Discord notifications, Google Drive upload, Apify Actor deployment

Currently focused on Knox and Blount counties, Tennessee. A realtor sphere-of-influence beta runs on the Columbus OH metro (the `soi_*` modules; see "Sphere of Influence Pipeline").

8. **REI Skill Library:** 22 Claude Co-Work skill files (`.skill`/`.plugin` ZIPs) for distribution to DataSift community via [learn.datasift.ai/claude-skills-rei](https://learn.datasift.ai/claude-skills-rei). Skills teach Claude specific REI workflows when uploaded to Co-Work sessions or Projects.

## Commands

```bash
# Setup
pip install -r requirements.txt
playwright install chromium
cp .env.example .env  # then fill in credentials

# Run
python src/main.py daily                          # new notices since last run
python src/main.py historical                     # last 12 months of data
python src/main.py daily --split                  # separate CSV per county+type
python src/main.py daily --counties Knox          # only Knox county
python src/main.py daily --types foreclosure,probate  # only specific types
python src/main.py daily -v                       # verbose/debug logging

# Comp package (boundary-filtered comps + dual-track ARV + rehab + buyers -> Excel)
python src/comp_package.py --address "158 Old State Rd" --zip 37914 \
    --beds 2 --baths 1 --sqft 1946 --year-built 1938 \
    --bbox "35.996,36.016,-83.895,-83.840" --streets "old state|nash rd|seahorn"

# Post-walkthrough package (comps + rehab matrix + walk findings + exits + dispo, Sift-linked)
python src/post_walkthrough.py --walkthrough-template     # writes walkthrough_template.json
python src/post_walkthrough.py --address "158 Old State Rd" --city Knoxville --zip 37914 \
    --bbox "35.996,36.016,-83.895,-83.840" --streets "old state|nash rd|seahorn" \
    --walkthrough walk_158.json --buyers output/buyer_sweep_37914_20260723.json \
    --outreach output/dispo_skiptrace_158.json
python src/post_walkthrough.py --address "..." --sold-json output/zillow_37914_sold.json  # free re-run

# DataSift preset/sequence management
python src/main.py manage-presets --discover                      # list all presets and sequences
python src/main.py manage-presets --add-sold-exclusion            # add Sold exclusion to all presets
python src/main.py manage-presets --create-sold-sequence          # create Sold cleanup sequence
python src/main.py manage-presets --all                           # discovery + update + sequence

# SiftMap sold property tagging
python src/main.py manage-sold --months-back 12                   # tag sold properties (last 12 months)
python src/main.py manage-sold --counties Knox --min-sale-price 5000

# Courthouse photo import (build 1.0.28+)
python src/main.py photo-import --folder ./photos --photo-county Knox --photo-type probate
python src/main.py photo-import --folder ./photos --photo-county Knox --photo-type eviction --skip-obituary
python src/main.py dropbox-watch                                  # auto-poll Dropbox for new photos
python src/main.py dropbox-watch --poll-interval 300 --max-polls 5  # 5-min interval, 5 cycles
python src/main.py dropbox-watch --no-delete                      # keep photos in Dropbox after processing
```

All source files are in `src/` and imports assume `src/` is the working directory. Run from project root with `python src/main.py` or set `PYTHONPATH=src`.

## Architecture

**Data flows:**
- **Web scrape:** `main.py` → `scraper.py` → `captcha_solver.py` → `notice_parser.py` + `foreclosure_filter.py` → enrichment → CSV
- **PDF import:** `main.py` → `pdf_importer.py` (pypdfium2 → `image_utils.py` OCR) → enrichment → CSV
- **Photo import:** `main.py` → `photo_importer.py` (OpenCV → `image_utils.py` OCR → `llm_parser.py`) → enrichment → CSV
- **Dropbox watch:** `dropbox_watcher.py` → `photo_importer.py` → enrichment → CSV (auto-polling loop)
- **Market Finder:** `extract_market_finder.py` → DataSift Market Finder (Playwright) → paginate all ZIP + neighborhood data → JSON → `generate_knox_report.py` → 7-sheet Excel

- **main.py** — CLI entry point. Parses args (`daily`/`historical`, `--split`, `--counties`, `--types`, `-v`). Filters saved searches by county/type, orchestrates scrape → dedup → export, logs run summary stats.
- **scraper.py** — Playwright browser automation. Reuses saved session cookies when possible, falls back to fresh login. Selects each saved search from the Smart Search dropdown (triggers ASP.NET postback), paginates results (50/page max), clicks each View button to open notice detail pages. Uses `last_run.json` for daily mode state, `cookies.json` for session persistence.
- **captcha_solver.py** — Solves reCAPTCHA v2 via **2Captcha API** on every notice detail page. Sends websiteURL + sitekey, gets back a `g-recaptcha-response` token, injects it, clicks "View Notice". Retries up to 3 times. This is the primary bottleneck (~10-30s per notice).
- **notice_parser.py** — Extracts structured fields from raw notice text using regex. There are NO structured HTML fields on the site — address, owner, dates are all embedded in free-text notice bodies. Defines the `NoticeData` dataclass used throughout.
- **foreclosure_filter.py** — Filters foreclosure search results to only keep real first-to-market trustee sales. Matches against observed title variations (substitute/successor trustee sales). Non-foreclosure notice types pass through unfiltered.
- **data_formatter.py** — Deduplicates by address (keeps most recent), then converts `NoticeData` list to Sift upload CSV. Split mode produces `{county}_{type}_{timestamp}.csv` files.
- **config.py** — Credentials (from `.env`), ASP.NET element selectors, saved search definitions, rate limiting constants, paths, image processing thresholds.
- **image_utils.py** — Shared OCR utilities used by both `pdf_importer.py` and `photo_importer.py`. Exports `fix_rotation()` (Tesseract OSD) and `ocr_page(image, psm)` with configurable page segmentation mode. Handles Tesseract binary detection.
- **photo_importer.py** — Courthouse phone photo import. OpenCV preprocessing chain (EXIF transpose → blur check → bilateral filter → perspective correction → Otsu threshold) → Tesseract OCR (PSM 4) → LLM parsing → NoticeData. Supports all 7 notice types.
- **dropbox_watcher.py** — Cursor-based Dropbox folder polling. Downloads new photos, resolves county + notice_type from folder path (`/Knox/eviction/photo.jpg`), processes through photo_importer, deletes from Dropbox after success. State persisted to `dropbox_state.json` + `photo_state.json`.
- **report_generator.py** — Generates per-record PDF deep prospecting reports using reportlab. Includes property summary, signing chain with phone tiers, valuation, deceased owner detection. Output to `output/reports/`.
- **extract_market_finder.py** — Playwright automation to extract ALL ZIP code + neighborhood data from DataSift Market Finder. Handles styled-component dropdowns, pagination (20 rows/page), Beamer popup dismissal. Outputs JSON. See "Market Finder Extraction Patterns" below.
- **market_analyzer.py** — ZIP code scoring engine. 6-factor weighted composite (Distress 30%, Value 20%, Equity 15%, Tax Delinquency 15%, Competition 10%, DOM 10%). Grades A/B/C/D, budget allocation across top ZIPs. Reads from scraped notice CSVs in `output/`.
- **drive_uploader.py** — Google Drive upload via service account. `upload_file()` (generic, returns webViewLink) and `upload_csv()` (CSV-specific, returns file ID).

## Site-Specific Details

The site is **ASP.NET WebForms** — all navigation uses `__doPostBack()` with ViewState. Session IDs are embedded in URL paths (`/(S({guid}))/`). Playwright is required because direct HTTP requests would need to manage ViewState/EventValidation manually.

**reCAPTCHA v2 is required on every single notice detail page**, even when logged in. There is no CAPTCHA on login, search, or results pages. The sitekey is hardcoded in `config.py`.

## Saved Searches

8 searches defined in `config.py` as `SAVED_SEARCHES`. Each maps to an exact dropdown option name on the Smart Search dashboard:
- Knox & Blount × (Foreclosure V2, Tax Sale V2, Tax Delinquent V2, Probate V2)

Filterable via `--counties` and `--types` CLI args (comma-separated, or omit for all).

## Key Domain Rules

- **Foreclosure filtering is critical.** Not all notices from "Foreclosure" saved searches are actual foreclosures. The scraper parses each notice's full text and only includes ones with trustee sale language. See `INCLUDE_PHRASES` / `EXCLUDE_PHRASES` in `foreclosure_filter.py`.
- **Probate owner_name** should be the Personal Representative/Executor/Administrator — not the deceased.
- **Owner names** in foreclosure notices typically appear after "executed by" in the deed of trust language.
- **Rate limiting:** 2-3 second random delays between requests, 3 retries per page.
- **Address dedup:** Same property can appear in multiple notices; `data_formatter.deduplicate()` keeps the most recent.

## Output

CSV files land in `output/` (gitignored). Logs go to `logs/` with timestamped filenames. Sift columns: `date_added, address, city, state, zip, owner_name, notice_type, county, source_url`.

**Date Semantics (build 1.0.30+):** `date_added` = the date WE added the record (the pipeline run date, stamped in `run_enrichment_pipeline`), so a daily run shows today. The legal notice's publication date lives in its own field/column, `date_published` / "Notice Publish Date" (parsed by `notice_parser` / the scraper results grid). PDF/photo imports set `date_added` explicitly (preserved, not re-stamped); CSV re-import preserves both columns. Downstream that needs the filing date (DOD sanity check, DataSift Probate Open Date, the month tag, dedup tie-break) uses `date_published` (fallback `date_added`).

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

## Comp Package Engine (build 1.0.33, 2026-07)

One-command, boundary-filtered comp package for a subject property (the "158 Old State Rd" deliverable, generalized). Pipeline: subject facts -> API sold/active pull -> boundary clip -> condition bucketing -> dual-track ARV -> rehab scenarios -> MAO math -> buyer matching -> branded Excel workbook.

- **`src/zillow_market_api.py`** — reusable OpenWeb Ninja `/search` client. THE API CONTRACT MOVED: `similar-sale-homes` (and every other comps-style endpoint) is retired and 404s; `/search` is the workhorse. Hard-won contract (verified 2026-07-21): `home_status` must be exactly `RECENTLY_SOLD`/`FOR_SALE` (else 400); every search caps at 41 rows with `totalPages=1` (~5 weeks of sales in an active zip), so `pull_sold()` partitions by `min_price`/`max_price` bands and recursively splits saturated bands (recovers 2-3 years per zip, ~50-80 calls); `price_min`/`price_max` are SILENTLY ignored — always check the echoed `parameters` object to confirm a filter applied; `dateSold` is epoch ms; `soldPrice` is a display string (use `unformattedPrice`); `homeType: LOT` can be a house sold at land value or a new build with missing sqft (verify against the county card). MLS-only: auction/wholesale/off-market transfers never appear — county records are truth for those.
- **`src/comp_package.py`** — CLI orchestrator (see Commands). Boundary = bbox AND street-regex (apply both: bbox catches street misses, streets catch bleed across I-40/highway edges). Condition bucketing by sold-price/Zestimate ratio (>=0.90 renovated/retail, <=0.70 distressed). Buyer sheet auto-matches the latest `output/buyers_datasift_*.csv` by zip. County card overrides (`--beds/--baths/--sqft/--year-built`) beat Zillow — aggregators get bedroom counts wrong.
- **`comp_analyzer.py`** `fetch_comparable_sales()` now routes through `zillow_market_api` (old endpoint dead); the ARV/adjustment/report engine on top is unchanged.

**Rollout (2026-07-21):** the API pull is the CORE comp-acquisition path across the deal-analysis stack. `deal_analyzer.py` and `main.py comps` already route through the fixed `fetch_comparable_sales`; `real-estate-comping.skill` and `deal-analyzer.plugin` now teach the API-first path (comp-package contract) with manual Zillow/Redfin browsing preserved as the no-key fallback for community users who skip the API. `property_enricher.py` is unaffected (uses the still-live `property-details-address`). Deep-prospecting v4 has no comp surface (heir resolution only). No SiftStack module calls apiv2.reisift.io directly (all CRM writes are Playwright browser automation or Deal Room `_api` scripts, which carry their own Api-Key auth per Ty's directive).

**Dual-track ARV (bedroom-band rule, Ty 2026-07-21):** a subject whose bed count is below the comp set lives in a LOWER value band than per-bedroom adjustments imply (37914 proof: renovated 2-beds capped $215-280K while same-size 3/2s ran $285-385K; a NEW 688sf 3/2 beat the whole 2-bed band at $265K). Base ARV = same-bed renovated comps only, clamped to that band's MEDIAN price (extra sqft cannot escape the band); reconfig-to-more-beds is a labeled UPSIDE track (capped at band p75) credited only after a walkthrough verifies the layout converts. Underwriting (MAO, contract targets) always uses the base track; future-value projections ride the same-bed curve. For stalled/partial renovations, underwrite full gut until walked.

## Dispo Stack (build 1.0.34, 2026-07)

Reusable buyer-finding + dispo-outreach pipeline, generalized from the 158 Old State Rd deal so ANY future property starts with deed-verified buyers and 3-source contact data instead of backfilling. Chain: `buyer_sweep` (who buys here) -> `dispo_skiptrace` (how to reach them) -> `deal_package` (one clean workbook). Runs against the shared Deal Room `_api` SiftMap client + reisift Open API key.

- **`src/buyer_sweep.py`** — SiftMap deed-level buyer sweep for a zip. Pulls the sold universe (Zillow `/search` band pull or a saved `--sold-json`), filters to the investor band (`--min-price/--max-price`, default $25K-$170K, `--months` default 18), then per sale runs SiftMap `autocomplete -> get_detail` for the DEED `sale_history` (buyer_name, is_cash_sale) + `owner_info` (portfolio size/value/equity, mailing). Aggregates + ranks buyers by purchase count, band-fit, portfolio. **Unmasks hidden principals:** when an LLC's mailing address is a residence, it reverse-lookups that address through SiftMap `owner_info` and takes the human owner as the principal (the "Harper move"), falling back to Enformion BusinessV2 officers. Live 2026-07: resolved 175/193 37914 sales -> ranked buyer list; found TN Super Props -> Jonathan Harper, Braden Family -> Joshua Braden by reverse-address. Output `output/buyer_sweep_<zip>_<date>.json|.csv`.
- **`src/dispo_skiptrace.py`** — three-source skip-trace waterfall with a built-in AUDIT MATRIX. Per contact: Source 1 Enformion Person Search (address-anchored via `_best_person` to beat common-name collisions), Source 2 Tracerfy batch ($0.02/rec), Source 3 web people-search cross-check (MANUAL: aggregators bot-block, so it merges a `--web` JSON dropped in by an agent/browser). Dedupes the union, Trestle-scores every unique number, and emits per-number `sources` + `confirm_count` (x2/x3 = cross-confirmed) plus a per-contact audit showing which source MISSED (answers "did we skip-trace this landline at both Tracerfy AND Enformion?"). Dial tiers = phone_validator standard (81-100 first, 61-80 second, 41-60 third, <=40 drop). Input = contacts JSON; output `.json|.csv` with `single_source_flag` + source-gap list.
- **`src/enformion_business.py`** — Enformion **BusinessV2** client (`galaxy-search-type: BusinessV2` on `devapi.enformion.com/BusinessV2Search`, verified live). The v1 `BusinessSearch` type is access-denied and `AddressSearch` is unlicensed on this account. `find_principals(entity, city_state)` returns human officers from `usCorpFilings`/`newBusinessFilings`, filtering out entity self-refs and commercial registered-agent fronts (Northwest Registered Agent, US Corp Agents, etc.).
- **`src/deal_package.py`** — spec-driven 6-sheet workbook generator (the consolidated 158 deliverable, generalized): 1 Deal Summary (numbers to use, value anchors, done-work story, contract gates), 2 Dial Sheet (ranked buyers with PER-BUYER open/target prices), 3 Deal Math (buyer-side + your-side, rehab detail, dual-track ARV), 4 Comps (each with its ROLE in the pitch), 5 Pitch + Sequence (30-sec script, objection answers, day-by-day plan), 6 Sources + Audit. Every section optional on its spec key. DataSift brand styling, zero em/en dashes. `--template` writes `deal_spec_template.json`; `--spec x.json --out "Addr_Deal_Package.xlsx"` renders.

**Feasibility framing (Ty, 158 run):** contract price at/above the as-is band converts a discount-wholesale into a dispo-EXECUTION play: the fee is won on the buyer side, not the buy. GC-model flippers drop out once rehab is heavy (their MAO collapses); the buyer pool becomes SELF-PERFORMERS and landlords, whose MAO/1%-rule math tolerates a higher price. Always verify the seller's real payoff at the Register of Deeds before trusting a stated "what he owes" number, and hold a novation/MLS listing as the backstop (an MLS shell sale is the true market ceiling). Per-buyer ask prices are tuned to each buyer's model (self-performer > landlord > out-of-state), not one blast number.

## Post-Walkthrough Package (build 1.0.35, 2026-07)

`src/post_walkthrough.py` is the JOIN POINT of the deal-analysis stack: the one workbook you build the hour after walking a house. It spins the comp engine, the rehab engine, the walkthrough findings, the exit engine, and the dispo stack into the exact 8 sheets of `Post Walkthrough Template.xlsx` (Overview | Exit Strats | Comps | Active-Pending | Repair Logic | Repair Numbers | Buyer Targets | Outreach Sheet), contextualized by the LIVE Sift lead. Where `comp_package.py` answers "what is it worth", this answers "we walked it, now what do we do with it and who do we call".

- **Sift lead is the anchor, not the address.** `load_lead()` runs the Deal Room `dossier.build_dossier(flow="A")` (CRM record + custom fields + message board + SIFTline cards + activity summary + SiftMap detail). Auth defaults to the **no-expiry Api-Key account** (`DEFAULT_SIFT_ACCOUNT = "datasift-apikey"`) by setting `REISIFT_ACCOUNT`, which `reisift_auth._resolve_account_name` honors ahead of `active_account`; `--sift-account` switches to a JWT account when the lead lives elsewhere. Live 2026-07-23 on 158 Old State: owner J. Doe, status Warm Lead, SIFTline Acquisitions/Offer Accepted, 11 board messages (surfaced the real blocker: title not cleared).
- **Record field names (verified live, they are NOT the CSV upload names):** `estimate_value`, `equity_percent`, `last_sold`, `last_sale_price`, `rental_value`, `sqft`, `bedrooms`, `bathrooms`, `year`, `lot_size`, `parcel_id`/`apn`, `investor_score`, `structure_type`, `assigned_to` (bare uuid, not a dict), `address{street,city,state,postal_code,county,latitude,longitude,vacant}`, `owner{first_name,last_name,company,address{...}}` (mailing lives here). County and lat/lon come off `address`, so the comp Dist column works with **no Zillow call**; `rental_value` auto-feeds the BRRRR line.
- **Subject-fact precedence:** explicit CLI (county card) > Sift record > Zillow. Aggregators get bed counts wrong, so the human override always wins.
- **Repair Numbers = the rehab engine expanded to a 4-scenario matrix** (Cosmetic at existing config / Mid Reno / Full Gut T2 / Full Gut T3 at the reconfig target), left block category x scenario, right block itemized line items per category. Line-item labels must NOT carry tier-dependent unit rates or the same line splits into one row per tier. Walkthrough **credits** (work the seller already paid for) and **team-walk flags** are their own rows, never smeared into categories, so every dollar traces back to Repair Logic. Bottom block: materials, labor, subtotal, soft costs, GC grand total, and a **self-perform estimate** (`SELF_PERFORM_LABOR_FACTOR = 0.55`, the lane that actually buys heavy-rehab shells).
- **Exit engine scores up to six exits off the CONSERVATIVE ARV track** and prints why each is recommended AND why not: wholesale assignment, wholetail, flip same-config T2, flip reconfig T3 (gated on `reconfig_verified`, else labeled upside only), BRRRR, novation/listing. **Only the exits that clear their gate get a suggestion block** (Rami, 158 review: the template's six slots were placeholders, not a quota); everything ruled out is named in the headline and explained in the logic block. If nothing clears, the two closest misses render under an explicit "nothing cleared its gate" banner. Each exit carries `kind` (assign/resale/hold): the Outreach sheet names the **buyer's** exit (best viable `resale`), never our hold. BRRRR profit is cash out at refi, a different unit from sale profit, and the logic block says so out loud.
- **EXACT numbers, not ranges (Ty, 112 Milligan review).** `EXACT_NUMBERS = True` makes every cell print one figure; the lo/hi still drives the math and surfaces as a single "If it moves" sensitivity line under each block. A wide band is not an answer you can take to a seller or a buyer. The range machinery below still computes the downside, it just does not render in the cells.
- **Lanes we actually run: wholesale, wholetail, fix and flip, rental (dispo angle). Novation is NOT modelled** and was removed from the exit engine, not just hidden.
- **`tight_arv()` is the underwriting ARV.** The dual track gives the wide market picture; underwriting uses the tight set and overwrites `arv["base"]` with it. Three hard rules: RECENT (prefer 12 months, widen to `--months` only if that leaves under 3 comps, and say so), SIZE (`ARV_SIZE_LO/HI` 0.70-1.35x subject sqft, because $/sf does not carry across a 2x size gap: a 2,392 sqft sale cannot price a 1,400 sqft house), SAME BED (clamped to the same-bed median sale so extra sqft cannot escape the bedroom band; a +/-1 bed widen costs an 8% discount). Emits a `basis` string (comp count, bed, sqft window, date span, median $/sf) that renders on Exit Strats. **Pass a `--months` POOL wider than the preference** (24 works) or the upstream recency cut starves the widener: 0.5mi + `--months 12` left only 2 comps.
- **`--months` is enforced on the cached-pull path too.** A saved `--sold-json` spans years; without the filter, stale sales quietly set the ARV.
- **Prices ship as RANGES unless we are confident** (Marwan, 158 review: "anytime it gives us a price, can it give us a range if it's not confident"). `rng()/fmt_rng()` carry lo/hi/point/confident; a confident figure writes a numeric cell, an unconfident one writes `"$X - $Y"` (plain hyphen). Rules: rehab is always a range (-10%/+15%, overruns skew high) until a signed bid lands in `walk["bids"]`; ARV is the comp band itself and tightens to one number only at n>=5 with band width <=30%; a signed `contract_price`/`assignment_price` is a fact, a derived MAO is a range; profit pairs the low sale with the high rehab.
- **Comps sheet restructured.** The template's second date column ("Date") had drifted from "Sold" in the sample data, so both ambiguous columns are replaced by the two facts that get argued about in a dispo call: **vs Zest** (sold over Zestimate, the signal the Bucket is derived from, so the call is auditable) and **Buyer (deed)** (who actually bought it, `CASH:` prefixed, joined from the buyer sweep's `records` block via `_norm_addr`). That wires the comp table into the dispo list: the buyer of the distressed comp two streets over is the person to call.
- **Bucket refinement (`refine_bucket`), a real accuracy fix the deed join exposed.** Zillow re-anchors the Zestimate to a recent sale, so an investor buy shows a ~1.00 ratio and `comp_package.classify` reads it RENOVATED, dragging the same-bed retail median down and understating ARV. When the ratio sits in the absorbed band (0.97-1.03) OR the deed shows an entity/cash buyer, AND the $/sf is well under the retail median, the comp is rebucketed. **Size guard:** $/sf falls as houses get bigger, so a sale priced at or above the retail band's lower quartile is never demoted (this is what keeps a large renovated comp from being thrown out). Refinement runs BEFORE the ARV: demoted comps are withheld from the list passed to `dual_track_arv` (which calls `classify` internally) but still render on the Comps sheet, correctly labeled, with a footnote counting the corrections. Live 158: 4 investor buys ($105K, $105K, $132K, $163K) pulled out of the 2-bed retail set, base ARV $265K -> $280K and tight enough to publish as a single number.
- **Walkthrough JSON is the human layer** (`--walkthrough-template`). Anything filled in OVERRIDES the live record, so fields stay empty unless the walk proved the record wrong. `work_done[].credit`, `flags[].cost`, and per-item `scenarios` flow straight into the matrix; `gates` render as pre-contract verify items.
- **`single_scenario` (walk key, 2026-08-10):** once the menu phase is over (contract signed, comps dictate the finish), the walk JSON collapses the 4-column matrix to ONE plan: `{"key","label","tier","scope","gut","beds","baths","drop"}`. Exits then price every lane off that one work number (`work_rng`/self-perform/pitch all fall back when the scenario list has a single entry). Comp-driven finish upgrades ride as named `flags` rows (auditable deltas), with the evidence recorded in a `comp_finish_basis` walk field. Built for the 3014 Sanland comp-match consolidation.
- **Placeholders, never blanks (Ty, 2026-08-10):** a pending sub quote never renders as $0 or an empty cell. Walk flag `"placeholder": true` gives the line a realistic assumed cost painted RED (C00000) with a legend row, so the grand total is always a true number; replace with the signed bid and re-render. Pair with `"drop"` on the scenario when the placeholder REPLACES an engine category (Sanland: engine Roof line dropped, the red $8,500 roofer line IS the roof budget). Labor model: `"self_perform_factor"` + `"labor_model_label"` walk keys override the 0.55 own-crew factor (Sanland runs 0.75 "PM + subs (owner-managed)"), renaming the second budget line and the flip lane to the model the operator actually runs.
- **Financing in the profit + Lender Analysis sheet (2026-08-11):** a walk `"financing"` block (`kind/rate/points/term_months/ltc/draws/lender/assumed`) bakes private money into every resale lane: profit goes NET OF DEBT (points + interest on the full balance over the lane's hold, conservative vs a draw schedule) plus buy-side closing (`BUY_CLOSE_FLAT` $900 + `BUY_TITLE_PCT` 0.77% title). ROI reads as cash-on-cash when financed, and a financed flip must ALSO clear the $10K wholesale floor to stay suggested. A 9th sheet, Lender Analysis, renders sources and uses, the draw schedule, loan-to-ARV, equity cushion, day-one as-is coverage, a band-floor stress case, the payoff waterfall at the POINT ARV over the FULL term (the conservative case; Exit Strats mids the band on a faster hold, the truth lives between), and the lender's annualized yield. `"assumed": true` paints the red placeholder-terms banner. No financing block = the old cash-basis math, byte-identical.
- **Free re-runs:** `--sold-json` reuses a saved band pull (`output/zillow_37914_sold.json`) instead of paying for the 50-80 call partition again. `--save-pack` writes the assembled pack; `--spec` re-renders it. Buyers come from `buyer_sweep`'s `ranked` list (`buyer`, `n_buys`, `cash_n`, `avg_price`, `portfolio_n`, `principal`, `buys[[addr,date,price]]`), ranked by fit against THIS deal's band and capped at `--max-buyers` (default 25) with the drop count stated on the sheet.
- **As-is band is the number that decides the deal, and it is the easiest one to corrupt.** Three guards, learned on the 112 Milligan run: (1) `NON_ARMS_LENGTH_RATIO = 0.30` drops family deeds/quitclaims (a $12,000 sale on a $247,700 house); they still render on Comps with a NOT ARM'S LENGTH role note. (2) Size band 0.65-1.40x subject sqft, because $/sf does not transfer across a 2x size gap. (3) Priced BELOW the retail band floor, because "sold under Zestimate" catches ordinary $240K-$300K trades that are not as-is investor buys. Best source when available is deed-verified cash/entity purchases from the buyer sweep (needs 3+ in the size band); the distressed bucket is the fallback. When the pocket is too thin for any of it, set `as_is_value` in the walkthrough JSON with a written basis: that override exists for exactly this.
- **Boundary discipline is not optional on the ARV.** 112 Milligan at 1.0mi bled into the Chaucer/Milton/Bobwhite subdivisions and pushed base ARV to $325K; held to 0.75mi the pocket reads $300K-$395K around the next-door twin at $305K. Always test 2-3 radii and look at WHICH streets enter before accepting an ARV.
- **Degrades, never fails:** no CRM auth, no API key, no buyer sweep, no skip trace each render a stated reason in place of the section and the workbook still builds.

**`buyer_sweep.py` auth (fixed 2026-07-23):** the sweep took `reisift_auth`'s `active_account`, normally the ~48h admin JWT. With that token expired every `get_detail` threw, the per-property `except` counted it a miss, and the run exited 0 with "resolved 0/133 sales" as if the market were empty. It now pins `--account` (default `datasift-apikey`, no expiry) into `REISIFT_ACCOUNT` and logs an explicit AUTH-or-COVERAGE error when it resolves zero of a non-empty target list. Same class of failure as any other silent-degradation path: a run that "succeeds" with no data is worse than one that fails.

## Knox First-to-Market Pull + DataSift API Upload (build 1.0.36, 2026-08)

`src/knox_ftm_pull.py` collects every Knox FTM source that carries a property address, enriches against SiftMap, applies the buy box, and writes an upload CSV. `src/datasift_api_upload.py` pushes it into DataSift entirely over the API. `src/knox_lien_resolve.py` turns lien debtors into parcels. `src/datasift_schema_setup.py` creates the custom fields, select options and lists (idempotent, dry-run by default).

```bash
python src/knox_lien_resolve.py --all --workers 6         # debtors -> parcels
python src/knox_ftm_pull.py --out output/knox_ftm_pull.csv
python src/datasift_schema_setup.py --commit              # schema, safe to re-run
python src/datasift_api_upload.py --limit 1 --commit      # ALWAYS verify one first
python src/datasift_api_upload.py --commit
```

**Buy box (Ty):** single family only, AVM **$1 to $700,000**. The $1 floor is deliberate and wider than the $100K floor in `_api/build-ty2-priority-siftmap.py`, because condemned and tax-distressed stock routinely falls under $100K.

**Sources and their real depth.** Liens/state tax/federal tax liens and trustee deeds come from the Register of Deeds (12 months). Notices come from tnpublicnotice (12 months). **Condemnations are one cycle only** and **evictions are one week only**: the city overwrites its agenda PDFs and the court keeps only the current week on the server (~86 back-dated URLs all 404). Both accumulate forward or not at all.

**Liens carry no parcel id** (0% of rows) because they are indexed against the person. The join is debtor name -> the open county tax API. See [[reference_knox_lien_join]] for the guards; full-run hit rate is **40%** (a 500-name sample read 64% only because it was sorted highest-lien-count first).

**Release filtering is not optional.** 27,493 release documents exist against 12 months of liens. **8% of lead debtors had EVERY lien already satisfied** and were dead leads. `load_liens` computes active = recorded minus released, drops fully-cleared debtors, and states `3 of 8 still active` in Notes. Instrument-level matching is the trustworthy signal; a name match only means that person had *something* released.

**Numbers that decide a deal, and where they come from:**
- Lien amounts live in the recorder's **Consideration** column (11,511 of 12,867 general liens, 373 of 377 federal; state tax liens carry none). Only ACTIVE liens are summed.
- **Condemnation dollar figures are PROSE in the agenda**, not API data (`"1 bill $254.00, county tax $271.36 (2025)"`). `_condemnation_money()` parses them; without it those records upload completely blank (caught on 1234 Example Ave).
- Tax delinquency is per-parcel and **only ~12% of parcels owe anything** — a sparse column is correct, not a fill failure. The delinquent YEAR is gated on a positive per-parcel amount, because the county API returns bills per OWNER and a multi-parcel owner would otherwise stamp one property's debt onto another.
- **No mortgage of record = free and clear = 100% equity** (Ty). Leaving equity blank made those records unjudgeable for the upside-down test.
- Upside-down records are written to `_upside_down.csv` and EXCLUDED from the upload: debt swallowing the equity is not workable.

**Date semantics here differ from the scraper.** `Date Added` holds the **county filing date** (recording date / hearing date / publication date / docket date), not the pull date, per Ty. Provenance survives as a `pulled_<date>` tag alongside `filed_<YYYY-Qn>`.

### DataSift API upload contract (hard-won, 2026-08)

**Auth: mint the JWT, never paste one.** `POST /api/token/` with `DATASIFT_EMAIL` / `DATASIFT_PASSWORD` from `.env` returns `{access, refresh}`. The uploader mints on start and re-mints every 30 minutes so long runs cannot die on expiry. **The Open API key cannot do this job** — custom fields do not exist anywhere in its 93-route surface and every write 401s. The minted user JWT reaches `/api/internal/` where they do.

Four traps, each of which fails silently or cryptically:
1. **Tags must be an ARRAY.** A comma string creates one tag literally named `"Courthouse Data, code_violation, Knox"`.
2. **A select field's value must be the OPTION'S UUID, not its label.** `"LEN"` returns `{"non_field_errors": ["'LEN' is not a valid UUID."]}`. Resolve via `custom-fields/` `options[]`.
3. **Entity owners cannot have a blank `first_name`** (the API rejects it). Send the business as `company` and OMIT the person keys; omitting a key is not the same as sending `""`.
4. **`notes` on the property payload returns 200 and is discarded.** Post it separately.

`POST /property/` is **upsert by address**, so re-runs never duplicate, and **lists accumulate** rather than overwrite (verified: a record came back with both new lists plus four it already had). Custom fields go to `PATCH /api/internal/property/{uuid}/custom-field/update-values/` with `[{"field_uuid": ..., "value": ...}]`. Creating a `select` custom field REQUIRES its options in the same POST.

**Always upload one record and read it back before releasing the file.** That single habit caught the tag format, the entity-owner rejection, the option-UUID requirement and a list-name mismatch that would have silently attached nothing for 2,512 of 2,573 records.

## Obituary Opportunity Ranking (build 1.0.37, 2026-08)

`src/obituary_opportunity.py` turns a reisift account's **Obituary list** into a lean-budget call order. The premise: a notice-of-default owner is on every wholesaler's mail drop because the filing is public and machine readable, but a decedent home is only reachable after somebody researches who died, who inherited and who signs. That research is the moat. Chain: pull (detail + custom fields) -> gate -> six weighted components -> branded 6-sheet Excel. Read-only, runs on the no-expiry Api-Key account (`datasift-apikey` = ty+2).

```bash
python src/obituary_opportunity.py --pull                          # refresh output/obituary_raw.json
python src/obituary_opportunity.py --out output/Obituary_Opportunities.xlsx --top 60
python src/obituary_opportunity.py --min-months 6 --mail-cost 0.75 --touches 6
```

**What the ty+2 obituary universe actually is (measured live, 740 records, 424 qualified):** NOT a distressed-debt list. 63% of qualified records are free and clear, **99% carry no auction-track flag at all**, and only ~3% carry any tax delinquency, lien, vacancy or code action. It is paid-off senior homes whose owner died. The motivation is the estate itself, so the pitch is speed, certainty and as-is, not rescue.

**Weights are set from that measured distribution, not intuition** (`W_DISTRESS 28, W_FIT 22, W_EQUITY 20, W_TIMING 12, W_SATURATION 10, W_CONTACT 8`). A first pass at equity 30 / saturation 20 produced only **11 distinct scores across the top 40** because on this list equity and quietness are near-constants. **Saturation is a LIST-level advantage, not a within-list ranking variable**: it is the reason to work obituary over foreclosure, and it is already banked the moment you pick the list. It is weighted 10 and the finding is stated on the Overview sheet rather than buried in a weight. The variables with real spread are dataflik `investor_score` (p10 18 to p100 100), `realtor_score` (inverted: a high one means an agent wins it, not you) and `year` built (older stock means rehab, which means retail hesitates).

**Gates (each counted on sheet 6, nothing silently dropped):** no obituary/death date; **under 3 months since death** (Ty's rule, give probate time to open, drops 237 of 740); already sold or an MLS sale after the death; `DEAD_STATUSES`; upside down; do-not-mail; no value; over the $700K buy box (drops 57); not single family.

**Two traps this build exists to avoid, both caught live:**
- **`Total Delinquency` is liens PLUS taxes.** 1234 Example Rdg reads 13,766.72 = 12,908.72 lien + 858.00 tax. Reading it as the tax figure double counts the lien and inflates exactly the records the model is built to surface (it put a lien-only record at rank 1). Tax amount comes from `Tax delinquency amount` or native `tax_delinquent_value`, never from Total Delinquency. Unpaid tax YEARS are often only in the `notes` prose, so `flatten()` parses "Unpaid county tax years: 2025" as a fallback.
- **Gate on lead status, not just on sold.** 123 Sample Dr topped the ranking on perfect fundamentals (vacant, absentee, free and clear, investor score 91) while sitting at `not_interested`. `DEAD_STATUSES` drops those 22 records; `IN_PROGRESS_STATUSES` flags rather than drops the ones already being worked.

**Every row ships a "Must verify" note**, because whether the person who died is the owner of record is NOT verifiable from CRM data. Zero ty+2 obituary records carry a probate open date, decedent name or resolved heir, so the whole research layer is still ahead and the spouse-obituary trap is live on every single row. Sheet 3 isolates the ~12 records that actually carry hard distress, since that is where the lien and tax-delinquency numbers exist at all.

**Rate limit:** `/api/internal/` throttles hard. Six threads at ~7 req/s 429'd 529 of 740; single-threaded at ~2 req/s with backoff on the server's "available in N seconds" hint completed cleanly. `pull()` is resumable and checkpoints every 25 records.

## Sphere of Influence Pipeline (Columbus OH beta, build 1.0.43, 2026-08-14)

Reverse-searches a realtor's exported Facebook/LinkedIn contacts (name + email ONLY, no addresses) into a Realtor-AI-scored priority list. Built for a Columbus OH realtor partner; the architecture is metro-agnostic. Chain: `soi_intake` (normalize/dedupe) -> `soi_county_pull` + `soi_owner_db` (free county owner rolls -> SQLite) -> `soi_owner_match` (name join) -> `soi_enformion` (paid resolve for misses) -> `soi_enrich` (SiftMap detail + `realtor_score`). First live run: 848 raw rows -> 728 unique people -> 222 confirmed metro homeowners -> 191 scored.

```bash
python src/soi_intake.py                                   # exports -> output/soi_contacts_normalized.csv/.json
python src/soi_county_pull.py                              # 4 ArcGIS counties -> output/soi/raw/*.jsonl
python src/soi_owner_db.py                                 # all 6 counties -> output/soi/owners.db (811K rows)
python src/soi_owner_match.py                              # name join -> output/soi_owner_matches.csv/.json
python src/soi_enformion.py                                # PAID (~$0.10/match) resolve of status=none
python src/soi_enrich.py                                   # SiftMap realtor_score on unique matches
python src/soi_enrich.py --matches output/soi_recovered_matches.json --out output/soi_enriched_recovered
```

**The whole Columbus metro is FREE data: 811,146 owner rows across six counties, $0.** Franklin (484K) from the open file server `apps.franklincountyauditor.com` - use `/Parcel_CSV/{yyyy}/{mm}/Parcel.csv` which carries NAME1/2/3 + MAILAD1-4 + values + TRANDT/PRICE; **the newer-looking `Outside_User_Files` Tab-Delimited appraisal extract has NO owner fields at all** (its Parcel.txt is values/situs only), and **the Parcel_CSV folder path is stale on purpose** (latest folder said 2025/07 but the file's Last-Modified was 3 days old - check the header, not the path). Fairfield (76K) from the nightly full CAMA dump `share.pivotpoint.us/oh/fairfield/cama/fairfieldaa407.zip` (iasWorld OWNDAT/PARDAT/APRVAL/DWELL, join on PARID, filter DEACTIVAT). Delaware/Licking/Pickaway/Union (250K) from open ArcGIS layers (endpoints + field maps in `soi_county_pull.py`; Licking serves 100K rows per call and inlines the last 3 transfers). The vendor SEARCH UIs (Schneider Beacon, DEVNET Pivot) are bot-walled and never needed. Ohio's statewide OGRIP parcel layer strips owner fields from the public view - counts and geometry only.

**Name-join mechanics that decide the hit rate** (`soi_owner_match.py`): deeds store "LAST FIRST M" with co-owners as "... & FIRST [LAST]"; LinkedIn last names carry credentials ("Weatherford, CRS"); Facebook's middle token is usually a MAIDEN name and is searched as an alternate surname; the nickname map is multi-target (Nikki -> Nicole/Nichole, Kathy -> Katherine/Kathleen); and a **household-pair boost** rescues spouses - if two roster contacts hit the same deed, both are lifted (Charlie Wlodyka scored 2.0 alone, confirmed by Jamie Wlodyka on the same Dublin parcel). Same-name collisions group by DISTINCT owner-name string: one person on 5 parcels is a portfolio signal, five different "JOHN SMITH" strings is ambiguity. `owner_occupied` = mailing addr-key == situs addr-key; **do not fall back to Franklin's OWNER_ADD1, it sometimes echoes the situs** and false-flagged a Texas absentee as owner-occupied.

**Enformion closes the gap, and EMAIL is the verifier.** Person Search accepts name + "Columbus, OH" city/state anchor (the name-alone 400 does not apply once a metro anchor is attached). The response's `emailAddresses` (a list of DICTS, `.emailAddress` inside) is matched against the contact's exported email - an exact hit grounds identity with no address needed; name-only OH matches are kept but flagged `name_metro`. Current address = `addresses[]` with `addressOrder == 1`, read `fullAddress` + `county`. Each resolved address is cross-checked back against owners.db: surname on the deed = `owns_here` (the roll join missed a name variation or trust - 37 of 349 on the live run), someone else = renter/other-titled (74), county outside the six = `out_of_metro` (106, cleans the sphere honestly). ~$24 total, misses free.

**`realtor_score` off SiftMap `get_detail` IS the Realtor AI score. Ty's rule: 95+ is a priority call.** Enrichment runs autocomplete -> get_detail per matched address and REQUIRES a token overlap between the county deed owner and SiftMap's `owner_info` before trusting the row (8 mismatches flagged, not trusted). The live distribution is steep - 191 scored: one 95+ (97), six 80s, seventeen 60-79 - so the 95+ bar isolates a real call list rather than a third of the sphere. Rows also carry equity, mortgage, portfolio count and both investor scores for an investor-referral cut. Renters are kept on their own track (future first-time buyers), RE-industry contacts (kw.com/mortgage/title domains, 27 flagged at intake) are referral partners, not homeowner sphere.

**Outputs:** `soi_contacts_normalized` -> `soi_owner_matches` -> `soi_enformion_resolved` -> `soi_enriched` / `soi_enriched_recovered` -> **`soi_priority_list.csv`** (merged, ranked by realtor_score). Enformion/SiftMap stages checkpoint to `soi_enrich_state.json` / `soi_enformion_state.json` and are resumable.

## Call Coaching Engine (2026-07)

Pulls real call recordings from the SmrtPhone web session, transcribes them with tonality notes, and routes them to three grading skills (`~/.claude/skills/`): **cold-call-coach**, **lead-manager-coach**, **closer-coach**. Each skill grades transcripts against a rubric built from the DataSift Call Playbook KB.

- **`src/call_coaching/pull_calls.py`** - SmrtPhone call log via `POST /logs/calls/filtered` (DataTables form, cookie session from `smrtphone_state.json`). Returns duration, disposition, caller, reisift record link, and a DIRECT recording URL on `rec.smrtphone.io` (public once known, no auth). Filters >= `--min-seconds` (default 60) + has recording; downloads MP3s to `output/call_coaching/recordings/`. Session expired -> exit 2; re-run `_api/smrtphone_login.py` (Deal Room Coaching Call project).
- **`src/call_coaching/transcribe.py`** - two passes per call via OpenRouter Gemini 2.5 Flash (~$0.002/audio-min): (1) audio -> diarized transcript with bracketed delivery notes + DELIVERY SUMMARY (pace/tone/talk balance; the model hears the audio), (2) text -> strict-JSON triage (call_type, pipeline cold_call|lead_management|closing, worth_grading). AGENT/SELLER labels are decided by content with the caller name as anchor (callbacks otherwise swap the labels). Outputs `transcripts/{id}.md|.json` + `review_queue.json` grouped by pipeline.
- **Grading:** Claude (in-session or via Workflow fan-out) scores each `worth_grading` transcript against the skill's `references/rubric.md`, writes per-call reports + per-caller scorecards to `output/call_coaching/reports/{pipeline}/`. Voicemails and wrong numbers are never scored.
- **Rubric sources:** DataSift Call Playbook (Cold Caller / Lead Manager / Closer scripts + trainings), LEAD-M_1.MD, playbook research corpus + elite-call transcripts.

## Two-Way SMS Agent (build 1.0.38, 2026-08)

`src/sms_agent/` sends outreach, reads replies in real time, classifies them, writes the result back to DataSift (phone status, opt-outs, lead status), and hands positive responses to a prospector in Slack. Full runbook: `src/sms_agent/README.md`.

**The constraint that shapes the whole build: DataSift webhooks CANNOT see an inbound text.** DataSift released webhooks as a **sequence ACTION**, so the trigger surface is exactly the ten sequence triggers, all of them CRM state changes (`Property Status Change`, `Property Assignee Change`, `Property Tags Added/Removed`, `Property Lists Added/Removed`, `Task Created/Completed`, `SiftLine Card Created/Moved`). There is no SMS-received trigger, no conversation event, and DataSift does not send SMS itself (it hands off to smrtPhone/Twilio/Plivo; drip campaigns have no documented reply-exit either). **smrtPhone's webhooks are the inbound leg**: `smsIncoming` (`smsId, from, to, message, date, callerIdName, userName, contactName, source`), `smsOutgoing`, `smsDeliveryCallback` (`status`, `failure_reason`), `addNumberToDNT`, `addNumberToDNC`. Both vendors post to the same receiver. Conversation replies go out over the smrtPhone API (`POST phone.smrt.studio/sms/send`, header `X-Auth-smrtPhone`), which is TEXT-ONLY, so only the original auction-screenshot MMS still needs the browser path in `mms_sender.py`.

**Voice comes from the `text-touch-builder` skill's message recipe**, not from defaults: warm, positive, properly capitalized, one easy question per message, under 160 chars, street line ONLY (never the full address with zip), first-real-name-token hygiene (initials-only / companies / trusts get owner-of-the-address wording), and the rule the whole program rests on, **never name the list** (foreclosure, auction, probate, inherited, tax, lien, code violation, eviction, divorce, bankruptcy, "behind on") because **the seller should feel found, not targeted**. Soft no vs hard no is noted on every NOT_INTERESTED since soft nos become follow-ups. STOP or hostility gets NO reply at all, not even an apology. `knowledge/playbook.md` is the editable system prompt.

**Identity is anchored to the ASSIGNEE, and we never say a company name** (Ty: a named company is litigation bait). The record's `assigned_to` uuid resolves through `config/sms_senders.json` to a first name, so an Adriana-assigned record signs as Adriana and Adriana is who calls; an unmapped uuid means the thread goes out UNSIGNED, never a guessed name. The agent describes itself by locality built from the record's own county ("a local buyer here in Blount County"). `cli.py senders [--record <uuid>]` shows what resolves. **The responder is given almost nothing** on purpose: owner first name, street line, city, county. Valuation, equity, distress flags, vacancy, beds/baths/sqft and every list tag are withheld from the prompt entirely, so there is nothing to leak. The validator hard-blocks any draft naming a dollar amount, naming the list, carrying a link or a zip code, over 320 chars, asking two questions, or self-identifying as automated.

**Two send transports (`SMS_AGENT_TRANSPORT`), because Ty believed smrtPhone had no API.** It does, for TEXT: `POST phone.smrt.studio/sms/send` with header `X-Auth-smrtPhone` (key from Admin > API Tokens). What has no API is **MMS**, which is what forced the browser route on the original auction-screenshot send; replies carry no image, so the API is the right transport here. `session_sender.py` is the fallback, driving the web app's Compose Message modal via `smrtphone_state.json` (reusing the mms_sender mechanics: context-level microphone permission or the dialer's Allow-microphone modal covers the compose UI and silently times out every send; the compose button is icon-only and targeted by POSITION at ~[80,63]). `auto` prefers API and falls back only on a transport failure, never a 4xx. **The session path sends from the account default caller ID only**, so sticky senders and per-number caps do not apply there and `doctor` says so.

**smrtPhone API key is VERIFIED LIVE (2026-08-10).** Auth probe: `POST /sms/send` with NO params returns 400 "Missing required parameter(s): from, to, message" on a valid key and 403 on a bogus one, so that is the clean credential check and it cannot send anything. Do NOT probe `GET /dialerConfigs`: it 405s on GET and serves the web app HTML on POST regardless of key, proving nothing. Prod transport is `api`; the browser session stays a local-only fallback.

**Cloud deployment (Fly.io, `fly.toml` + `deploy/Dockerfile`).** Four deliberate choices: ONE machine with the worker as a thread inside the receiver (`SMS_AGENT_INLINE_WORKER=1`), because SQLite is single-writer and two machines would fight over one volume; `auto_stop_machines = false`, because a stopped machine drops a webhook and there is no replay; a persistent volume at `/data` holding the event log, `sms_numbers.json` and `sms_senders.json` (editable without a redeploy via `fly ssh console`); and NO Playwright in the image since the API transport works. **`src/sms_agent/crm_standalone.py` is what makes this possible**: a self-contained reisift client (`authorization: Api-Key <key>`, `REISIFT_API_KEY`, base `apiv2.reisift.io`, 429 backoff that parses the server's "available in N seconds" hint) so the cloud box needs no Deal Room checkout on disk. `crm.py` prefers the shared CRMClient and falls back to it.

**Number pool is OWNER-BOUND, 18 numbers at 25/day = 450/day.** Pulled 2026-08-11 from smrtPhone Admin > Phone Numbers, which has NO public API: `/phoneNumbers` and `/callerIds` return the SPA shell to an API key. The route is the web session plus the FOSJsRouting trick (`GET /js/routing?callback=fos.Router.setData` dumps ~1,187 routes) which finds `POST /phoneNumbers/filtered`, a DataTables endpoint like `/logs/calls/filtered`; fields come back as HTML fragments and need parsing. 21 numbers total, 3 excluded (Website 865-324-1736 on the Inbound Calls flow, Ty - Dispo 865-338-9203 on the Ty Test flow, and Adriana Test Flow 865-273-0739), leaving Adriana 9 and Tinaa 9. **`config/sms_numbers.json` is keyed by the caller who owns each number** and `sender_pool.assign(phone, owner)` prefers that caller's numbers, because the thread is signed by the assigned person and a homeowner who calls the number back must reach the same person the text claimed to be from. A sticky number wins over owner preference: changing numbers mid-thread is the worse problem.

**Autonomy ladder (`SMS_AGENT_PHASE`), because the phone number is the asset:** 1 classify + write phone status/opt-outs, 2 + escalate and flip CRM status, 3 + draft replies held in Slack for approval, 4 + auto-send a narrow gated intent set. **Phase 2 already delivers the prospector handoff with zero AI-authored text sent.** `SMS_AGENT_DRY_RUN=1` independently blocks every CRM write and every send.

**Guardrails, each from a specific failure mode:** human takeover wins instantly (an `smsOutgoing` we did not author means a person typed it -> pause the thread, cancel every queued AND held message); opt-outs are decided by regex and never by a model, and cover natural language ("stop texting me", "take me off your list") not just the STOP keyword; 6-turn cap; recipient-local 8am-9pm quiet hours from the area code, with up to 30 min of wake jitter so a night's backlog is not one 08:00:00 burst; sticky sender number per conversation (switching mid-thread reads as a spam farm); per-number daily cap + pacing; a hard output validator that blocks any draft naming a dollar amount, carrying a link, over 320 chars, asking two questions, or self-identifying as automated; a 0.80 confidence floor; and `sys_`-prefixed system tags so our own writes never re-trigger the sequences that called us.

**The loop is complete both directions.** `seed.py` renders outreach touches from `knowledge/touches.py` (the text-touch-builder pools, kept in sync with the skill) and queues them through **the same outbox as every AI reply**, so outreach gets no private send path and inherits suppression, quiet hours, per-number caps, pacing and the sticky sender. Seeding also registers `phone_map`, which is how a reply later finds its record. Staged as HELD; `release --touch N` is the deliberate go/no-go. `digest.py` is the daily readout: funnel on top, work queue underneath (drafts awaiting approval, threads a human took, soft nos old enough to rework, send failures, and a warning when webhook events sit unprocessed for a day, meaning the worker is down). Soft nos close separately from hard nos because the playbook works them again later.

**Backfill proved the classifier on REAL replies before anything was wired.** smrtPhone already syncs inbound SMS into the CRM as `owner.sms.received` activity events, so `backfill.py` replays them through the live classifier read-only. First run (24 records from the June MMS send, 9 real replies): classifier correct on all 9 (5 on rules, 4 on the model). **The finding that changed the code: 5 of the 9 replies came from a DIFFERENT number than the one we texted.** People answer from whichever line is in their hand, so mapping only the target number leaves most replies unroutable. `crm.map_all_phones()` now maps every phone on a record, called from `seed.queue` and `map --all-phones` (219 extra numbers across those 24 records). Also caught: *"I'd like it get the house tho in auction if it's cheap enough"* is a BUYER, not a seller; the model read it OTHER at 0.55, under the floor, so it drafts for a human instead of paging a prospector.

**`selftest.py` is the test harness: 69 assertions, zero network, throwaway DB, every outbound edge stubbed. Covers the engine AND the FastAPI surface (wrong secret, empty secret, IP allowlist, retry dedupe, malformed body, non-object payload, health).** Safe to run any time with production credentials loaded. It ASSERTS rather than prints, because the failure mode this codebase keeps rediscovering is a run that reports success while doing nothing. It has already caught two real bugs (both below).

**Two traps caught during the build, both silent:**
- **`numbers.py` shadowed the stdlib `numbers` module** when the CLI ran as a script (its own directory lands on `sys.path` first). That broke pydantic inside the Anthropic SDK, the exception was swallowed, and EVERY classification silently degraded to the weak keyword fallback while still returning a plausible answer. Renamed to `sender_pool.py`.
- **The model invents an identity.** With no name configured it introduced itself as "Alex". Unresolved identity now means the agent is explicitly told it has NO name and NO company name, rather than being left to fill the gap.
- **Name hygiene greeted people by their surname.** `clean_first("E A Henry")` took "the first token of length 2 or more", which walks past the initials and lands on the SURNAME, so an initials-only owner got "Hi Henry!". The fix is positional: on a multi-token name only the tokens BEFORE the surname can supply a first name, and if they are all initials there is none. **This bug was shipped in the text-touch-builder skill too** and is fixed in both.

**Knowledge base = `src/sms_agent/knowledge/playbook.md`** (the system prompt): DataSift Call Playbook, 4 Pillars of Motivation, handoff triggers, hard rules, adapted to SMS. Edit the file, not the code. The flywheel worth building next is pointing the three coach skills' grading engine at the agent's own threads, so the texter is graded by the same rubric as the humans.

**Open items:** the DataSift webhook payload shape is unverified (`handle_datasift` logs and resolves defensively, writes nothing); smrtPhone's DNT *write* route is undocumented (only the webhook is), so `add_to_dnt` tries plausible paths, always suppresses locally, and Slack-alerts on failure; smrtPhone webhooks are unsigned and its logs purge after 30 days, hence the secret URL path, optional IP allowlist, and the local SQLite event log; Slack is post-only until a real Slack app replaces the incoming webhook.

```bash
python src/sms_agent/cli.py selftest                  # 69 assertions, zero network, safe any time
python src/sms_agent/cli.py backfill --queue output/mms_send_queue.csv   # classify REAL past replies, read-only
python src/sms_agent/cli.py doctor                    # wiring check, live transports, the webhook URLs to paste
python src/sms_agent/cli.py seed --csv export.csv --touch 1   # outreach preview (--queue stages, release sends)
python src/sms_agent/cli.py digest                    # daily funnel + work queue
python src/sms_agent/cli.py senders --record <uuid>   # which caller name a record signs as
python src/sms_agent/cli.py map --csv output/mms_send_queue.csv   # phone -> record backfill
python src/sms_agent/cli.py simulate 8652548712 "how much are you offering"
python src/sms_agent/cli.py serve                     # receiver
python src/sms_agent/cli.py work --loop               # worker (separate process)
```

## Locked Master Material List + SKU-Grounded Rehab Engine (build 1.0.39, 2026-08)

The team committed to the Master Material List as THE material source. Knox pricing is pulled fresh and FROZEN: `python src/material_list.py --master --zip 37914 --cached --lock` writes the git-tracked lock artifacts `data/master_materials_locked_37914.json` (engine-canonical) + `.csv` (skill/human twin). **Only `--lock` writes `data/`**: an ordinary re-pull refreshes `output/` cache + xlsx but can never drift prices into estimates. Current lock: 94/94 search keys priced, 88 SKU rows + 12 allowances, pulled 2026-08-10.

- **`src/sku_pricing.py`** loads the lock and prices per-category material BASKETS (quantity drivers from the MASTER catalog / `build_lines`). Grade map: tier 1/2/3 -> Budget/Standard/Upgrade; tier 4 (Premium/Custom) is off-list by definition. `estimate_rehab` (knoxville/blount, tier <= 3) takes SKU materials + engine labor per category; any missing SKU drops the WHOLE category back to the legacy table with a loud log (the "outstanding random issue" clause), and a missing/invalid lock file means full engine fallback, so nothing hard-fails.
- **THE DOUBLE-DISCOUNT TRAP: locked prices are already Knox-local. The 0.88/0.86 regional multiplier applies to LABOR ONLY in SKU mode.** Multiplying locked materials by 0.88 under-prices ~12%; `tests/test_sku_pricing.py` asserts the exact basket math to catch a leak.
- Demo reclassifies to the labor side in SKU mode (it is a service); exterior siding + driveway and Foundation/Structural stay on engine lines (no HD-SKU basket). `line_items` key contract is preserved so `post_walkthrough._line_rows` renders unchanged; `RoomEstimate`/`RehabEstimate` gained a trailing `materials_source` field and estimates stamp `locked_sku 37914 pulled <date>`.
- **Consumers needed zero changes** (post_walkthrough Repair Numbers, comp_package scenarios, deal_analyzer, main.py rehab). Knox totals SHIFTED on purpose: real SKUs raise the too-cheap tier 1 (~+17% grand) and trim the padded tier 3 (~-18%); non-Knox and tier-4 outputs are regression-tested byte-identical. `use_locked_materials=False` opts out.
- **rehab-estimator.skill + deal-analyzer.plugin** now ship `data/master_material_list_37914.csv` with the doctrine: locked list is the material source for the vast majority of items on Knox deals (off-list only for an outstanding random issue, flagged), cheat sheet keeps labor + non-Knox markets, never multiply locked material prices. The skill's `material_specs` JSON contract is now wired (the Material Specs sheet renderer always existed but was never fed). deal-analyzer's bundled `skills/rehab-estimator/` was EMPTY despite instructing Claude to read 5 files from it; it now carries the full 8-file skill. Both zips rebuilt with forward-slash entry names (Compress-Archive backslash paths are non-portable).
- Re-lock cadence: re-pull before each project cycle if desired, but re-lock (an explicit, dated, git-diffable act) only when the PM re-approves the list.

## The Lender Package (build 1.0.44, 2026-08)

An 8-piece set handed to a private money lender to fund ONE named property. The team hand-edited every template on 2026-08-16 and those edits are the spec; the originals live in `Lender Docs Templates-*.zip`.

```
1. Cover Letter                     lender_docs.py
2. The Private Lender Package.xlsx  lender_package.py
3. Promissory Note                  lender_docs.py
4. Personal Guarantee               lender_docs.py
5. Closing Instructions Letter      lender_docs.py
6. Insurance Request Letter         lender_docs.py
7. Investor Information Sheet       lender_docs.py
8. Satisfaction and Release Request lender_docs.py
```

```bash
python src/lender_package.py --spec deals/3014_sanland_lender.json
python src/lender_docs.py    --spec deals/3014_sanland_lender.json
```
Both write to `output/lender/<Deal_Name>/`, one folder per deal, numbered 1 through 8.

**THE FRONT END IS A BLOCK-FOR-BLOCK MIRROR OF THE REPAIR ESTIMATOR** (`Copy of The Repair Estimator`, Ty's Drive). Not "inspired by", mirrored: Property header, then `Property Values & Pricing | Holding Costs (Monthly)` with Annually and Monthly columns, then `Financing Costs | Buying Transaction Costs` and `Selling Transaction Costs` with Perc. Of Purch and Perc. Of ARV columns, then the `Estimated Net Profit and ROI Snapshot` band, then `Purchase and Deal Analysis | Lender Coverage and Return`. Six columns: label, percent, dollars on each side. Bold `Total X:` rows close every block. The single adaptation is the last right-hand block, which is the lender's coverage instead of our cash on cash, because this is their document. **Inputs live inline on that page**, never on a separate tab, for the same reason the Estimator does it: a blue cell next to the answer gets changed, a blue cell on another tab does not.

**Repair Costs is the Estimator's detail grid on its own tab** (Category / Include Y-N / Repair Type / Qty / Unit / Unit Cost / Total / Notes, banded EXTERIOR / INTERIOR / MECHANICALS / OTHER, 65 lines) and it rolls up into Estimated Repair Costs on the front page. **On a straight relist every line is switched to N and the total is zero, which is the answer rather than a missing tab.** Switch a line to Y and the budget, the loan, the LTV and every coverage ratio move with it.

**Selling costs are itemized, not a flat percentage.** Escrow, recording, realtor %, transfer %, warranty, staging, marketing, misc, exactly like the Estimator. That matters beyond cosmetics: `SellFixed` and `SellVarPct` are separate names so the band-floor case reprices commission against the LOWER sale price instead of carrying the ask's dollar figure down with it.

**THE COVER LETTER IS THE SPEC FOR THE WORKBOOK.** It tells the lender the package contains an overview of the deal, an overview of their contribution, a term sheet, the numbers on repairs and re-sell value, the comps, backups and risk, and next steps to fill out. So the tabs ARE that list, in that order, and nothing else: **Deal Overview, Your Investment, Term Sheet, Repair Costs, Resale Value, Comps, Backups and Risk, Next Steps** (repairs and resale being the two halves of one bullet). Do not add a tab without adding it to the cover letter first.

**Structure is lifted from The Repair Estimator** (`Copy of The Repair Estimator`, Ty's Drive), because that is the sheet the team actually trusts:
- **Inputs live INLINE on the summary page, not on their own tab.** The Estimator puts its blue cells right next to the results, which is why people actually change them. Build 1.0.41 had a separate Inputs tab and it was the thing Ty disliked.
- Banded full-width section headers, paired left and right blocks, dense rows.
- **A percent column beside every dollar column.** "$16,272" means nothing until it reads as 7% of ARV.
- Bold `TOTAL X` rows closing each block, and the detail page rolls UP into the summary.
- **The repair grid carries a Y/N per line.** Switch a category to N and `RehabTotal`, the loan, and every coverage ratio drop with it.

**Everything is a live Excel formula** off workbook defined names, including the sentences (`_say()` + `_t()` build `="..."&TEXT(Loan,"$#,##0")&"..."`, and the LTV paragraph is a live `IF(LTV>0.75,...)`). 240 formulas across the two live deals, verified by actually recalculating with the `formulas` pip package.

**Derived, never typed:** `DayOne = Loan - RehabTotal`. Typing the closing advance let financed closing costs land in the draw tranche, so the holdback disagreed with the repair budget ($88,800 against an $87,192 scope). Anything definitionally equal to other cells is a formula. The one deliberate exception is **`Loan`, which is a single blue input**: it is a negotiated number, not a derived one, and making it the only lever that sets the deal is what keeps the front page simple. `Borrower Cash` then falls out as `Purchase + Repairs + BuyCosts + HoldTotal - Loan`, deliberately excluding interest because that is paid from sale proceeds rather than at closing.

**Read the workbook back before regenerating over it.** Ty reviews in Excel and edits input cells directly. On the 158 review he made three changes and only mentioned one: realtor fees 6% to 5%, as-is raised to match the ask, and he deleted a comp. Diff the blue cells against the spec before overwriting, and rewrite any prose that cites a number or a comp he moved.

**Contract changes from the team's edits, all of which move numbers:**
- **The LOAN covers closing costs, document prep, recording and the lender's title policy**, repaid with interest and backed by the guarantee. It used to be borrower cash.
- **Every member of the company personally guarantees the note**, so `borrower.members` is a list and the guarantee plus closing letter render one signature block each.
- **Default is not a penalty rate and not a foreclosure lecture.** On default we liquidate immediately and the guarantee covers any shortfall including interest still owed. That framing replaced the old 15% default-rate language everywhere.
- **Minimum interest is quoted as a percent as well as months** ("3% guaranteed" at 12% over 3 months).
- `deal_type` picks the wholetail or flip branch in the cover letter; a non-zero repair budget picks builder's risk over vacant dwelling on the insurance letter. **Exactly one side of every OR gets written.**

**The templates carry a NOTES FOR CLAUDE block that must never reach a lender.** `build_all()` re-reads each rendered document and raises if the string survives, rather than trusting the code path. Same reflex as the partial-set guard: `main()` exits 1 if fewer than 7 documents write, and stale files from a previous numbering are deleted so a folder cannot grow a second copy of everything.

**No deed of trust template on purpose:** in TN the closing attorney draws it on their own form for the Register of Deeds and the title underwriter, so a downloaded form is a recording problem rather than a shortcut. Document 5 tells them exactly what to prepare instead.

**Voice** comes from `CMO Stack/context/voice-guide.md`. The note and the guarantee stay in formal legal register; the letters are in Ty's voice. Audit scans rendered formula output as well as static cells for em/en dashes, ~30 AI tell words, leaked notes and unresolved `XXXXXX` placeholders. Current state on both deals: zero.

**FORMATTING IS PART OF THE DELIVERABLE.** Nobody should drag a column or a row to read this workbook. `_polish()` runs over every sheet after the content is written: column widths come from the longest thing actually in each column, then wrapped rows get a height computed from the width they ended up with, plus landscape fit-to-width print setup. The trick that makes it work is **`_rendered_len()`, which measures what a cell will SHOW rather than what it holds.** A formula cell stores `="..."&TEXT(Loan,"$#,##0")&"..."` but displays a sentence, so sizing off the raw formula blows every column out; the function sums the quoted literals, adds 12 per `TEXT()`, and takes 62% when there is an `IF()` because only one branch ever renders. **Verify formatting against DISPLAYED text, not raw values:** a coverage cell holds 1.1176756139 and shows "1.12x", so a naive width check reports false overflows. Apply the number format first. Both live deals currently pass at zero fit problems.

**Gotcha:** Excel holds an exclusive lock, so a workbook open on the desktop makes `wb.save()` raise `PermissionError` and `formulas` cannot even read it. Write to a `_PENDING_` name and swap.

## MDDC Trustee's Sale Pipeline (build ~1.0.46, 2026-08-22)

A standalone scraping + upload pipeline for Trustee's Sale (foreclosure) public notices on `mddcpublicnotices.com`, covering Maryland, DC, and Delaware jurisdictions. `src/scripts/mddc_trustee_sale_pull.py` (pull), `mddc_browser_pipeline.py` (upload + skip-trace automation), `mddc_datasift_upload.py`, plus `live_pull.py`, `refresh_blank_structure.py`, `score_all_records.py`, `score_live_pull.py`, `score_live_pull_townhouse_condo.py`.

**Fetch mechanism:** the Smart Search results grid has no CAPTCHA, so it's pulled via **Firecrawl** rather than Playwright. The `Details.aspx` full-notice detail page carries its own separate bot-gate; clearing it reliably needs a single continuous Scrapfly call rather than the current two-call approach (login, then a separate fetch), which loses ASP.NET session state between the two calls — this is why `county`, `loan_principal`, and `auction_date` are currently best-effort/blank on a chunk of rows. That's a data-availability gap pending the single-call fix, not a bug.

**County coverage — verified straight from the script's own `COUNTY_CHECKBOX_INDEX`/`DEFAULT_COUNTIES` (28 checkboxes on the site, MD/DE/DC only, re-verify against a fresh `Search.aspx` pull if the site renumbers the list):** `DEFAULT_COUNTIES` = Anne Arundel, Baltimore County, Calvert, Carroll, Charles, Frederick, Montgomery, Washington DC. **There is no Virginia checkbox on this site at all** — the script's own comment states this plainly. Real VA content still surfaces incidentally (e.g. a genuine Orange County, VA trustee sale showed up under a MD-only county filter), so VA rows are kept in the output as incidental leakage rather than dropped, but this is not deliberate VA coverage. Deliberate VA coverage needs a separate public-notice source, not yet identified — the code's own instruction is to ask before guessing one.

**Two gotchas fixed live:**
- The address-extraction regex was grabbing the filing law firm's letterhead address (the first address-shaped match in the notice text) instead of the actual property address — fixed by taking the **last** match instead of the first.
- The site's pagination runs through an ASP.NET UpdatePanel that silently no-ops on a plain click — advancing a page needs a full synthetic `MouseEvent` sequence dispatched manually, not a simple click call.

**Uncommitted refinements in `mddc_browser_pipeline.py`:** a `_select_all_matching()` header-caret-dropdown workaround, because the plain header checkbox only selects the rows visible on the current page (~13), not the full result set; and a `--confirm-send` gate with a hardcoded expected-count guard before Skip Trace fires, so a short/incomplete selection can't silently get skip-traced.

**Saved-search verification, ported from the TN workflow (2026-08-24):** `mddc_trustee_sale_pull.py` used to trust `DEFAULT_SAVED_SEARCHES = ["41", "43"]` as "Trustee's Sale" purely off one 2026-08-21 manual check — the same blind-trust failure mode the TN site's `list-searches` command exists to catch (a renamed or renumbered saved search scrapes zero rows and looks exactly like a quiet day). `python src/scripts/mddc_trustee_sale_pull.py --list-searches` now logs in, reads every `<option>` on the live `ddlSavedSearches` dropdown, flags any configured id whose live label has drifted from `SAVED_SEARCH_LABELS`, and exits 1 if a configured id is missing entirely — mirroring `python src/main.py list-searches` for TN. It also surfaces every OTHER saved search already on the account (Tax Sale, Probate, Foreclosure, etc.), which is the only way to know what document types are actually available to pull beyond Trustee's Sale, since MD/DC/VA has no `knox_ftm_pull.py`-style county-direct coverage for those yet.

**Output:** `output/mddc_trustee_sale.csv`. A recent run: 200 raw rows -> 139 unique (124 MD, 8 VA leakage, 1 DC, 6 blank-state).

The skill wrapper (`.claude/skills/mddc-trustee-sale-pull/SKILL.md`) is gitignored and machine-local — it is not part of the distributable `skills/manifest.json` library; it's an internal-only tool.

## Virginia Public Notice Pull (build ~1.0.47, 2026-08-25) — fills the VA gap, no login needed

`src/scripts/va_trustee_sale_pull.py` targets `publicnoticevirginia.com`, the source that fills the exact hole the MDDC section above documents: `mddcpublicnotices.com` has no Virginia county at all. Same ASP.NET WebForms vendor template as TN/MDDC (confirmed live), same Firecrawl-actions approach, same synthetic-click fix for postback buttons. Structured as a separate standalone script (not a shared import) matching this codebase's existing per-site pattern.

**The account has no subscription on this site and per the user never will — the pull runs entirely through the PUBLIC "Popular Searches" Quick Search widget instead, confirmed live 2026-08-25 to need no login at all.** The original build assumed the same login-gated Smart Search flow as MDDC (`authenticate.aspx` -> `ddlSavedSearches`); live testing found that dropdown returns exactly one option, `"Subscription or free trial expired"`, and per the user there's no path to a paid subscription for this site. The fix wasn't a workaround — it's a different, better mechanism that was sitting on the homepage the whole time: `default.aspx` embeds the identical Quick Search form (same `ctl00_ContentPlaceHolder1_as1_...` control naming, same `lstCounty_{N}` county CheckBoxList, same `WSExtendedGrid` results grid) fully unauthenticated, with a `ddlPopularSearches` dropdown of pre-built category searches. This is strictly better than a paid saved-search would have been: **Foreclosures (value 4)** is the direct MDDC Trustee's Sale equivalent, but **Estate Claims (6)** is probate-adjacent and **Tax Deeds (8)** is tax-sale-adjacent — neither exists anywhere else in the MD/DC/VA pipeline, and CLAUDE.md's own doors-per-deal research found zero SiftMap coverage for those list types across all 14 target jurisdictions. `--popular-search` selects which category to pull.

**Two bootstrap discovery flags** (`--list-searches` for the Popular Searches categories, `--list-counties` for the county checkbox index + label) exist because guessing a checkbox index or category value silently produces wrong or empty results rather than an error — the same "ask before guessing" discipline as the rest of this pipeline. Both now hit the public page, no credentials beyond `FIRECRAWL_API_KEY` required.

**Verified live 2026-08-24/25:**
- **County list confirmed identical on both the public and login-gated pages:** 104 checkboxes — every VA county plus the state's independent cities, plus "Washington D.C." at idx=98. `COUNTY_CHECKBOX_INDEX` filled in for the target counties: Arlington 6, Fairfax 29, Prince William 75, Spotsylvania 89, Stafford 90.
- **Fredericksburg City is NOT on this site at all.** Confirmed absent from all 104 live checkboxes — not a parsing miss (the other independent cities parsed fine). Left out of `COUNTY_CHECKBOX_INDEX` rather than guessed a workaround; still an open question whether Fredericksburg notices are folded into Spotsylvania's or genuinely uncovered here.
- **Popular Searches categories confirmed live, all 10 match the hardcoded `POPULAR_SEARCHES` map exactly:** Assessments(3), Foreclosures(4), Estate Claims(6), Truth in Taxation(7), Tax Deeds(8), Schedule of Meetings(9), Annual Treasurer's Report(10), Prevailing Wage Notices(14), Water(15), Virginia Marine Resources Commission(16).
- **County filtering is now DEFINITIVELY confirmed correct, via a decisive single-county test.** Two full-5-county runs looked suspicious — every returned row landed in a DIFFERENT county each time (Albemarle, Orange, Shenandoah, Henrico, Hanover, Frederick, Powhatan — never once Fairfax/Prince William/Arlington/Stafford/Spotsylvania), which briefly looked like the county checkboxes weren't restricting results server-side at all. A `--counties "Arlington"` run resolved it cleanly: all 10 returned rows were genuine Arlington properties (confirmed by street address AND by "Circuit Court for Arlington County" in the notice body itself) — the two 5-county runs were just unlucky given how few Foreclosures post per day across 5 counties. Filtering works; the earlier concern was a sampling artifact, not a bug.
- **That same test caught a real bug: the "authoritative" grid metadata (`div.right`'s City:/County: field, from the very first live row inspected) is NOT actually authoritative for county.** On the Arlington-only pull, several genuinely-Arlington notices had their `County:` field read "Fairfax" or "Washington D.C." instead — it looks like this field reflects the PUBLICATION's own registered coverage area (accurate for a hyper-local paper like Daily Progress/Albemarle, wrong for a DC-metro paper like the Washington Post/Times covering several jurisdictions at once). `parse_grid_html`'s `county` field is now ALWAYS the free-text `COUNTY_MENTION_RE` parse of the notice body itself, with NO fallback to `meta.get("county")` — a blank county (when the notice text is truncated before naming one) beats a confidently wrong one, the same rule this codebase applies everywhere else (see the probate "courthouse became the subject property" lesson above). `parse_grid_meta()`'s `city` field is still used as a fallback (no comparable counter-example found for city), but carries the same caveat.
- **County checkbox filtering has its own postback race, fixed.** Each county checkbox carries its OWN `onclick -> __doPostBack` (unlike a plain form field that only submits on the final Go click), so checking N boxes queues N separate async partial postbacks. Clicking Go before they've all round-tripped raced Go's own postback against them and silently dropped the county filter — confirmed live (an unfiltered-looking statewide result, including counties never checked, came back before this fix). Fixed with an explicit wait (~700ms/checkbox) between setting the checkboxes and clicking Go.
- **The results page itself renders inconsistently under Firecrawl — MORE THAN HALF of attempts across a full session of live testing came back an empty `<body></body>` shell (~2.7K chars) instead of the real ~150K+ char page** (one run failed 3 straight tries), independent of the postback-race fix (seen both before and after). `run_popular_search` retries up to 4x with backoff whenever a returned page is suspiciously small or the Firecrawl call itself errors (a live 500 was hit during testing) — silently trusting a too-small page as "zero results" is exactly the class of failure the FTM runbook already warns about elsewhere in this file. The post-Go wait was also bumped 5000ms -> 8000ms as a root-cause mitigation (the results grid is the heaviest part of the page to render), not just papered over with more retries.
- **County-name extraction needed a VA-specific fix.** Virginia notices commonly phrase it "the Circuit Court of the County of Prince William" rather than "Prince William County" — the MDDC-derived regex's permissive capture (spaces allowed) grabbed "Office of the Circuit Court of the" instead of the actual name. `COUNTY_MENTION_RE` now tries "County of `<Name>`" and "City of `<Name>`" first, and caps every capture at 1-3 title-case words so lowercase glue words can't extend the match.
- **Two more fixes found by pulling one real row's raw HTML (2026-08-25), both in `parse_grid_html`:** (1) this public view's "View" button is a plain ASP.NET postback submit with no `Details.aspx?ID=` anywhere in the row — there is no stable per-notice id to extract here at all, so `notice_id` is intentionally always blank (dedup already runs on street/city/date instead), not a parsing miss to chase. (2) `date_published` was silently always blank: the MDDC-derived regex looked for a `"Published:"` label that doesn't exist in this markup — the real date is a plain second line under the publication name (e.g. `"Monday, August 24, 2026"`), now read directly.

**Full notice text (`--full-text`, 2026-08-25) — built, blocked on a real 2Captcha key.** Basem asked for the FULL notice text (not the 2000-char grid snippet, which truncates well before the auction date in most notices) in place of `source_url` (every row shared the same static search URL, carrying no information). Clicking a row's "View" button does NOT reveal the text inline — it navigates to a detail page gated by a **Cloudflare Turnstile challenge**, confirmed live: same `id="ctl00_ContentPlaceHolder1_PublicNoticeDetailsBody1_btnViewNotice"` / "I Agree, View Notice" button as tnpublicnotice.com, i.e. genuinely the same page template, gate and all. Firecrawl alone cannot clear this (no built-in CAPTCHA solving), so `fetch_full_text()` ports the exact technique `src/scrapfly_client.py` already uses for TN: pre-solve the Turnstile via 2Captcha against the site's sitekey (`0x4AAAAAADs-42ukJJ2lrjsf`, verified live) BEFORE the browser scenario runs (a token is valid for the domain, not one specific page render), then inject it into the hidden `cf-turnstile-response` field via Scrapfly `asp=True` once the widget renders and click through. Because this public view has no stable notice id/URL, each fetch must redo the ENTIRE Popular-Search + county + date setup from scratch and click the target row's View button by ON-PAGE POSITION (`_view_index`, an internal field on each parsed row, popped before the CSV writes) — there is no cheaper way to reach one specific row's detail page.

**Blocked on credentials, not code:** a live single-row test failed with 2Captcha's own `ERROR_WRONG_USER_KEY` — `.env`'s `CAPTCHA_API_KEY` is literally the placeholder string `"your_2captcha_api_key_here"`, never replaced with a real key on this machine (TN's own live-verified 2Captcha runs presumably happened in a different environment, e.g. the Apify cloud deploy, with its own secrets). `SCRAPFLY_KEY` IS real and confirmed working (already used for MDDC). `fetch_full_text()` fails fast on a bad/missing token BEFORE spending a Scrapfly call, so the failed test cost nothing beyond one wasted (rejected) 2Captcha attempt. **Next step: set a real `CAPTCHA_API_KEY` in `.env`, then run `python src/scripts/va_trustee_sale_pull.py --popular-search 4 --full-text --full-text-limit 10`** — everything else (search replication, position-based click, token injection, the "I Agree" submit) is built and ready to validate. `extract_full_notice_text()`'s selectors are UNVERIFIED until a real gate-clear succeeds (falls back to the page body's largest visible text block rather than returning silently empty if the known-from-TN selectors don't match VA's detail-page structure).

### Current state as of session end (2026-08-25)

- **Working output right now:** `output/va_foreclosures.csv` (Foreclosures, 5 target counties, county filter DEFINITIVELY confirmed correct, all extraction bugs above fixed and verified against real data). Regenerate any time: `python src/scripts/va_trustee_sale_pull.py --popular-search 4 --days 60 --max-pages 1 --out output/va_foreclosures.csv`.
- **`--full-text` / `--full-text-limit`** (default 10, matching the requested test batch) are built and unit-tested but never validated against a real gate-clear — blocked purely on the placeholder `CAPTCHA_API_KEY` above. Once a real key is in `.env`, run the command in the paragraph above.
- **Not yet supported:** `--full-text` only reaches page-1 rows (`_view_index` is per-page, not global) — a multi-page (`--max-pages` > 1) full-text fetch isn't built.
- **Still open:** Fredericksburg City has no county checkbox on this site at all — unresolved whether its notices fold into Spotsylvania's or need a separate source entirely. The 7 MD counties (judicial-foreclosure regime, per the doors-per-deal research below) would benefit from a Lis Pendens/Final Judgment pull, which doesn't exist anywhere in this pipeline yet — MDDC and this VA pull both key on Trustee's Sale/Foreclosures, the non-judicial-state signal.

## MD Probates + MD Legal Notices (build ~1.0.48, 2026-08-25, IN PROGRESS)

Formerly documented as one "MD Register of Wills Probate Pipeline" — renamed 2026-08-26 to name its two sources separately, since they are two separate sources feeding one script: **MD Probates** = the Estate Search side (`registers.maryland.gov` government registry) and **MD Legal Notices** = the Legal Notice Search side (published notices on the same site). Both are **explicitly separate from the MDDC/VA trustee-sale pulls below** even though all three produce Maryland leads — this pair targets `registers.maryland.gov` (the actual government Register of Wills / Estate Search system), not a legal-notice-publisher site. Two scripts: `src/scripts/md_register_of_wills_pull.py` (the pull, both sources) and `src/scripts/md_land_records_lookup.py` (the property-address lookup that replaced the originally-planned Bright MLS integration). **Session ended mid-task with the user's explicit stop instruction** — this section is the handoff for whoever picks it up next.

### The two sources — MD Probates and MD Legal Notices — both on registers.maryland.gov, both confirmed live

1. **Estate Search** (`RowNetWeb/Estates/frmEstateSearch2.aspx`) — plain HTML form (`txtEstateNo`, `cboCountyId`, `cboStatus`, `cboType`, `txtLN`/`txtFN`/`txtMN`, `DateOfFilingFrom`/`DateOfFilingTo` or `txtDOF` exact date, `cmdSearch` — a real `<input type="submit">`, no ASP.NET postback trickery needed for the search itself). Blank-name + county + Filing Date range enumerates every estate in a window. The results grid (`#dgSearchResults`) gives County/Estate Number (linked to a `RecordId`)/Filing Date/Date of Death/Type/Status/Name only — **no PR or Attorney**, those live on the per-estate detail page (`frmDocketImages.aspx?src=row&RecordId=<id>`), which has clean labeled spans: `#lblPersonalReps` / `#lblAttorney` in the format `NAME [<small>ADDRESS, CITY, STATE ZIP</small>] <br/>` repeated per person. **Pagination has no visible href/onclick on the pager `<a>` tags** (unlike a classic ASP.NET GridView) — clicking by exact text-match in a live browser context (Firecrawl `executeJavascript`) works regardless of the actual wiring; confirmed live paging "Page 1 of 11" → "Page 2 of 11".
2. **Legal Notice Search** (`LegalNotice/Notices/NoticeSearch.aspx`, "Search Public Notices") — publishes free-text notices that turn out to already embed decedent name, PR name + mailing address, date of death, and estate number in one block per row (cheaper than Estate Search's two-step flow for those fields, so it's *merged in*, not just cross-checked). Also literally says "SMALL ESTATE ..." in the body for small estates — a second signal for the SE exclusion. **Real bug found and fixed:** its `#cboCountyId` has its own onchange-triggered postback; setting the value and clicking Search immediately (no wait) races that postback and silently returns the **unfiltered statewide result set** instead of erroring — confirmed live via two identical-length responses regardless of county. Fixed with a 2000ms wait between setting the county and clicking Search — the same class of bug as MDDC's county-checkbox race, just on a single dropdown.

Both pages (and the estate detail page) show **`#lblLatestDataDateTime`: "Latest data as of: 8/24/2026 4:00:00 PM (rownetwebalt)"** — the pull reads this off Estate Search first and uses that date, never the run's own wall-clock date, as the upper bound for both sources. Confirmed by the user this reflects a real courthouse constraint, not a bug: **courts don't file on weekends**, so a Sat/Sun can show zero or near-zero activity for a smaller county — verified live, Calvert had exactly one record dated the cutoff Sunday and it was Estate Type SE (correctly excluded). **Intended operational cadence is WEEKLY, run Mondays, as a check on the PREVIOUS week** (catching docket entries that lagged — a PR added a day or two late, a weekend backfill) — the checkpoint design already handles this correctly by construction since it always picks up from wherever the last run left off, regardless of elapsed days.

### County code schemes — THREE DIFFERENT ones across the sites this pipeline touches, verified independently

Estate Search / Legal Notice Search share one numeric map (`COUNTY_ID` in `md_register_of_wills_pull.py`: Anne Arundel=2, Baltimore County=3 [site labels it plain "Baltimore"], Baltimore City=24, Calvert=4, Carroll=6, Charles=8, Frederick=10, Montgomery=15, etc.). Land Records uses 2-letter codes (`LANDREC_COUNTY_ID`: AA, BA, CV, CR, CH, FR, MO). SDAT uses its own DIFFERENT numeric scheme (`SDAT_COUNTY_ID`: Calvert=05 there vs. 4 on Estate Search). **Do not assume any two of these line up** — each is verified against its own live dropdown independently.

### Estate Type exclusion (per the user)

Exclude Estate Type codes **SE** (Small Estate, under $50,000), **SJ**, and **MV** — confirmed live these three codes still exist on the `#cboType` dropdown alongside FP/LO/MA/NP/RE/RJ/UN. Applied as a post-fetch filter on BOTH sources (Legal Notice has no Type field of its own, so it's detected there via the literal "SMALL ESTATE" phrase in the notice text). Verified this materially cuts volume: SE/UN types dominate a normal day's raw count.

### Reconciliation ledger — the pure date-cutoff pull isn't sufficient alone

Per the user's own design (relayed mid-session as "the side question"): a forward-moving "pull everything newer than the last cutoff" checkpoint assumes records are append-only and correctly dated at insert time. They aren't — a PR can get appointed weeks after the original filing date, and a pure date-range pull would never re-visit that estate once the window moves past it. Fix, built and verified live: `output/md_row_ledger.json` persists every Estate Number seen (per county) with its last-known field values; `reconcile_ledger()` diffs freshly-pulled records against it and logs three distinct outcomes (added / changed / unchanged) rather than silently overwriting; `run_reconciliation_pass()` separately re-checks only the "incomplete" queue (any estate with no PR yet) on a lookback window (default 45 days), dropping an estate from active re-checking after a retry ceiling (default 90 days) with a logged "gave up" rather than retrying forever. **Verified live end-to-end**: seeded the ledger, manually cleared a real PR to simulate a late appointment, re-ran, and confirmed the diff caught it and logged `RECONCILE changed` correctly.

### Property address lookup — Bright MLS was abandoned, Land Records + SDAT replaced it

**Bright MLS Matrix (`matrix.brightmls.com/Matrix/Tax/PublicRecords/PublicRecordsSearch`) was the original plan** (confirmed live: Owner Name section with `Last/Corporate Name`+`First Name`, "starts with" match semantics, Results grid with Address/City/Postal Code/Owner Name columns) but was **abandoned live 2026-08-25 after two real, unresolvable blockers**: (1) a fresh login hits SMS/email MFA with no way to relay a code without a human in the loop at that moment; (2) reusing the user's actual logged-in Chrome profile to skip MFA (`launch_persistent_context` pointed at `C:\Users\pc\AppData\Local\Google\Chrome\User Data` with `--profile-directory=Profile 1` for the "Basem" profile, found via parsing `Local State`'s `profile.info_cache`) hit a hard Chrome security restriction: **"DevTools remote debugging requires a non-default data directory"** — Chrome refuses CDP automation against the literal default profile dir outright, so even a copy-the-profile workaround would be needed. Per the user: use Maryland's land records instead. `BRIGHT_MLS_USERNAME`/`BRIGHT_MLS_PASSWORD` are still in `.env` but unused by any code now.

**The replacement, `md_land_records_lookup.py`, verified live end-to-end on real decedents and is actually a stronger source than Bright MLS would have been:**
- **Maryland Land Records** (`landrec.msa.maryland.gov`) needs a free registered account (`MD_LANDREC_EMAIL`/`MD_LANDREC_PASSWORD` in `.env`) — logged in cleanly with no emailed access-code challenge in testing, though the login page does have a live `#body_tbUsercode` MFA selector wired up, so `_assert_logged_in()` raises loudly rather than silently proceeding if a future session ever hits it (no way to relay an emailed code without a human).
- Searches the deed index by **GRANTEE name**, with real match-mode flexibility (Is/Begins/Ends/Contains/Fuzzy/Soundex — confirmed via `#body_ddlLastName`/`#body_ddlFirstName`), far better than Bright MLS's "starts with"-only. Results grid (`#dgSearchResults`) gives Date/Grantor-Grantee/Instrument Type/Book-Page/Remarks — **no address directly**.
- **Recent deeds (2020s+) are genuine text-layer PDFs**, not scans — confirmed live on real Calvert County deeds. The PDF's real URL is embedded in the "view=I" instrument-info page's `<embed original-url="...">` attribute (`resolve_pdf_url`), and — critically — **that PDF is fetchable publicly, no login needed**, and Firecrawl auto-extracts clean markdown text from it (no OCR). Pre-2000s instruments ARE raster scans with zero usable text — confirmed on real 1978/1987 deeds, a genuine data-availability gap, not a bug.
- **Priority order for extracting the actual address, in `find_property_for_decedent()`, most-reliable first:** (1) the deed's own plain-English **"Said parcel has the address of: ..."** statement when present — ground truth, HIGH confidence, no inference (confirmed live on a real 2020 estate-distribution deed: "456 Example Court, Owings, Maryland 20736-3159"); (2) the deed's own **"TAX ID NO: DD-NNNNNN"** header cross-checked against SDAT for the address + owner-name overlap score; (3) only if the PDF has neither, decode the search grid's **Remarks column** (e.g. `"SAMPLE MANOR        0   0   2107481 8590"` → SDAT District `02` + Account `107481`, confirmed by decoding the site's own dropped-leading-zero concatenation and verifying against the deed's own stated Tax ID) and cross-check that against SDAT too. **The Remarks-decode path turned out unreliable as a sole strategy** — a second real deed had empty Remarks despite being perfectly normal and fully text-readable, yet its own PDF stated both the address AND the tax ID plainly, which is why the deed-text-first priority exists.
- **SDAT** (`sdat.dat.maryland.gov/RealProperty`) does NOT allow owner-name search at all (privacy restriction, confirmed live) — only Street Address / Property Account Identifier (District + Account, two separate text fields) / Map-Parcel / Property Sales. Used here only as the District+Account cross-check path, never as a name-search entry point.
- **Two real bugs found and fixed during validation, both worth knowing about if this code gets copied elsewhere:** (1) `parse_sdat_result`'s label-value regex captures were missing `re.DOTALL`, so any multi-line value (e.g. two owner names on separate lines before "Use:") silently came back empty — this fully masked a working SDAT response as "not found" until caught. (2) Splitting a run-together address string like `"8590  OAK HILL DR OWINGS 20736-0000"` into street/city with a naive non-greedy regex put the entire "OAK HILL DR OWINGS" into city — fixed by reusing the same street-suffix-word heuristic (`STREET_SUFFIXES`, imported from `md_register_of_wills_pull`) already used to fix an identical ambiguity in the Legal Notice address parser (`_split_address_blob`) — this exact class of bug hit twice in one session, worth remembering as a pattern.
- **Name-splitting gotcha:** the ledger's `decedent_first_name` field is actually "First [Middle]" (e.g. "JANE L."), but Land Records stores First/Middle in separate DB fields with an exact-match default — passing the whole string as First Name silently matched nothing. `fill_addresses()` now passes only the first token.

### Performance reality check — this is genuinely slow at multi-county scale, not stuck

**Verified via live process monitoring** (low CPU%, but ticking upward over time = genuine I/O-wait progress, not a hang) that a full 7-county pull with address resolution took well over 75 minutes and was still running when stopped — this is NOT the same class of problem as the Bright MLS Chrome hang (which used 0% CPU and produced zero forward progress at all). Root cause, isolated via a single-county unbuffered foreground run (`python -u ...`): **each decedent needing address resolution costs roughly 20-40+ seconds** (Land Records login + county-select wait + search wait, plus possibly a PDF fetch, plus possibly an SDAT round-trip, each step with deliberate multi-second waits baked in for reliability against this vendor stack's postback timing). A single day for Anne Arundel alone surfaced 14 raw estates (8 kept); scaled to 7 counties for even one day, 50+ decedents needing resolution is plausible, i.e. 30-60+ minutes just for addresses. **`print()` output is fully block-buffered when redirected to a background task's output file** — nothing appears until a buffer's worth accumulates or the process exits, which made a genuinely-progressing-but-slow run initially indistinguishable from a hang; `python -u` (unbuffered) is the fix for diagnosing this live next time, and `Get-Process python | Select CPU` (rising CPU over repeated checks = real progress vs. flat CPU = actually stuck) is the general diagnostic.

**Practical implication for whoever runs this next:** don't kick off a bare multi-county `--commit` run and expect fast turnaround. Either run counties individually/in parallel, or use `--no-addresses` for a fast probate-data-only pass and run address resolution as a deliberately separate, slower follow-up step.

### `--until` + `only_keys` scoping — added mid-session, both real fixes not just conveniences

- **`--until MM/DD/YYYY`** lets a run target an explicit historical window (e.g. `--since 08/21/2026 --until 08/21/2026` for one specific day) without disturbing the forward-moving checkpoint — **critically, `--commit` advances `last_cutoff` to the actual `date_to` queried, never blindly to the site's current "Latest data as of" date.** The original code always advanced to `site_cutoff` regardless of what window was actually pulled, which would have silently created a gap (dates between the narrow `--until` and the real site cutoff would look "already covered" to a future default forward run and never get pulled).
- **`write_csv()` and the ledger now scope each CSV to `only_keys` (the estate keys touched by THIS run)**, not the whole accumulated ledger. Before this fix, every weekly CSV would have grown into a superset of all history ever pulled for those counties, rather than representing that period's batch — a real design gap caught while fulfilling a one-off request, not something invented for that request alone.

### Known open item — checkpoint contamination from this session's testing, MUST be addressed before relying on plain `--commit`

`output/md_row_last_run.json` currently holds `last_cutoff: 08/24/2026` from an early **Calvert-only** test `--commit`, but Estate Search/Legal Notice were never actually pulled for the other 6 target counties (Montgomery, Anne Arundel, Frederick, Carroll, Charles, Baltimore County) before that date. **Running the script with no `--since`/`--until` (the normal weekly mode) right now would start ALL 7 counties from 08/24/2026 forward and silently skip everything before that for the 6 counties never backfilled.** Fix needed before real production use: either reset/delete the checkpoint and re-seed with an appropriate `--seed-days` across all 7 counties in one real commit, or move to a per-county checkpoint instead of one global value (the more correct long-term fix, not yet built).

### Current state as of session end (2026-08-25, stopped on explicit user instruction)

- `output/md_row_ledger.json`: 5 estates, Calvert only (10002, 10003, 10004, 10005, 10001), fully enriched (PRs + one resolved address: Jane L. Sample → 456 Example Court, Owings MD 20736, HIGH confidence via deed text).
- `output/Register of Wills 08-24-2026.csv`: the committed Calvert batch.
- `output/Register of Wills 08-21-2026.csv`: Anne Arundel ONLY for 08/21/2026 (14 estates, address lookups completed) — this single-county run finished; a subsequent all-7-county attempt for the same 08/21 date was started, ran 75+ minutes, and was deliberately stopped (not committed, not written) so it could be re-run more deliberately (see Performance section above) — **the other 6 counties' 08/21 data has not been pulled yet.**
- A stray `output/_PENDING_Register of Wills 08-24-2026.csv` exists from an earlier Excel-file-lock test of the pending-swap fallback (`write_csv`'s `_PENDING_` write-then-replace pattern, added this session to handle Excel's exclusive lock) — safe to delete, harmless leftover.
- **Not started at all**: the zip-level market-grade column (originally planned step 5) — a new MD-specific formula built from `sift-market-research`'s raw Market Finder data, per the user's explicit override of an earlier plan to reuse `market_analyzer.py`'s TN weights directly.

## Data Extraction Test — MDDC/VA/MD Probates/MD Legal Notices live audit + fixes (2026-08-26 to 2026-08-27, IN PROGRESS, PAUSED on explicit user instruction)

Basem asked for a real 5-record live pull from each of the four MD/DC/VA sources, one tab per source, as a sanity check before trusting any of them — his own words after seeing the first pull: **"I am sad to say that NONE of those are working properly."** That test surfaced real, fixable bugs in three of the four; the fourth (MD Probates) surfaced a real bug plus a genuinely unfinished dependency (SDAT). The working file is **`output/data_extraction_test.xlsx`** (a plain local file — Google Drive's XLSX→Sheets conversion failed repeatedly and inexplicably on this workbook, so it's kept as a local Excel file, not a Drive link). It gets rebuilt and re-saved after every fix; `_PENDING_data_extraction_test.xlsx` in the same folder means the real file was open in Excel when a rebuild ran and needs a manual swap once closed.

**Working agreement for this whole effort, set by Basem: fix one source at a time, keep the rest queued in memory, confirm each fix against a real live pull before moving on.** The order actually run was MDDC first (closest to "it just needs polish"), then VA (ported the same fixes), with MD Probates and MD Legal Notices still queued and NOT started as of this pause.

### MDDC (`src/scripts/mddc_trustee_sale_pull.py`) — DONE, 10/10 clean rows verified live

Three rounds of real, live-verified fixes, each confirmed by re-running the pull and reading the output:

1. **`filter_notice_type()`** (new) drops any row whose `classify_notice_type()` isn't `"foreclosure"` — a live run under saved searches 41/43 ("Trustee's Sale") returned an OCC bank-merger notice, a tender-offer notice, and a healthcare Certificate-of-Need filing, none of them foreclosures. Confirmed live: 6 "other" + 3 "probate" dropped in one run.
2. **VA rows are KEPT, not dropped.** `IN_FOOTPRINT_STATES` was briefly narrowed to MD/DC only (on the theory that `va_trustee_sale_pull.py` already covers VA), then explicitly reversed same-day per Basem: "if you find VA do not skip it" — VA leakage through the MD-only county checkboxes is real content, not noise, same reasoning the original code comment already had. Back to `{"MD", "DC", "DE", "VA"}`.
3. **County is now populated on almost every row**, via two layers: the existing free-text `"X County"` mention when the notice states it, and — new — a **ZIP-code geocode fallback** (`resolve_county_by_zip`) for the common case where a plain Trustee's Sale notice never says the word "County" at all (confirmed live: county was blank on 10 of 11 clean rows before this). The geocode is two real API calls, not a guess: `zippopotam.us` resolves ZIP → lat/lon, then the **FCC's own Census Area API** (`geo.fcc.gov/api/census/area`) resolves that point to its actual county — both are authoritative government/postal data, consistent with this codebase's "a guess that's wrong is worse than a blank field" rule (a geocode isn't the kind of guess that rule forbids). Cached to **`data/zip_county_cache.json`** (shared with `va_trustee_sale_pull.py` — DC/MD/VA ZIPs can repeat across both sites' pulls, so one cache avoids double-billing the same lookup).
4. **Address regex fixed for two real formats it was missing entirely** (both confirmed live, both real notices): a spelled-out state name in **either case** ("Virginia" or "VIRGINIA", not just the `MD|DC|DE|VA` abbreviation — the case-sensitivity half of this was the actual live bug, not just the missing word forms), and **no delimiter at all between street and city** ("1234 SAMPLE MEADOWS DRIVE MANASSAS, VA 20109" — no comma, no newline). The no-delimiter case is handled by a new `ADDRESS_FALLBACK_RE` that captures the whole blob between the house number and the state/zip, then splits it on the **last recognized street-suffix word** — the exact technique already proven for the identical ambiguity in `md_register_of_wills_pull.STREET_SUFFIXES` / `_split_address_blob`, imported and reused here rather than re-invented.
5. **New `"order_nisi"` notice-type rule**, checked BEFORE the general foreclosure rule. A Circuit Court **ORDER NISI / ratification-and-confirmation notice** ("ORDERED this 13th day of August, 2026 by the Circuit Court for Charles County, Maryland, that the sale o[f]...") still matches the foreclosure regex (it names a trustee's sale) but is a court filing CONFIRMING a sale that already happened, not a property listing — per Basem: "Anything that is in the circuit court of x county — would have the sale will be ratified and confirmed, ignore those." These notices also never carry a property address, which is why some rows were showing up blank. The regex covers both the literal phrase ("order nisi", "ratified and confirmed") AND the structural boilerplate opening ("ORDERED this `<date>` by the Circuit Court for `<county>`...that the sale"), because the grid snippet frequently truncates BEFORE the word "ratified" appears.
6. **County-mention "junk" filter.** VA-style `"...recorded in the Clerk's Office of the Circuit Court of the County of Fauquier"` phrasing (county named AFTER "County of", not before "County") tricked `COUNTY_MENTION_RE` into capturing the glue phrase `"Clerk's Office of the Circuit Court of the"` as if it were the county name. Rather than trying to teach the regex every phrasing variant, a token-level check (`COUNTY_MENTION_JUNK`) rejects any candidate containing words like "the/of/clerk/circuit/court/office" and falls through to blank — which the ZIP-geocode fallback then fills in correctly. (First attempt at this fix checked the WHOLE candidate string against the junk set instead of individual tokens and missed the bug entirely — the candidate is never a single word, it's the whole multi-word glue phrase.)

**`notice_id` is still blank on every row** — the extraction regex hasn't been revisited against the site's current markup. Not yet fixed, not blocking anything else.

### VA (`src/scripts/va_trustee_sale_pull.py`) — same fixes ported, mostly confirmed live, one unconfirmed due to a Firecrawl outage

Per Basem: **"follow the exact same rules as MDDC except for the smart search — since there is no account for saved searches"** (VA has no login-gated Smart Search/saved-search account at all, only the public Popular Search widget — that piece of MDDC's design doesn't apply here and wasn't ported). Everything else was:

- `filter_notice_type()` — ported verbatim, confirmed live (3 "other" + 1 "probate" dropped in one run).
- ZIP-county geocode fallback — ported verbatim, **sharing the same `data/zip_county_cache.json`** as MDDC. Confirmed live.
- Address regex fixes (spelled-out state, no-delimiter fallback) — ported, confirmed live on `"1234 EXAMPLE LN  MIDLOTHIAN, VA 23112"` (no delimiter) and unit-tested (not yet re-confirmed in a full live run, see below) on `"...CULPEPER, VIRGINIA 22701..."` (ALL-CAPS spelled-out state).
- **VA-specific fix**: `COUNTY_MENTION_RE`'s `_GLUE` word-exclusion list was missing `"IN"` — `"...22701 COUNTY OF CULPEPER   In execution of a certain deed of trust..."` let the next sentence's leading "In" bleed into the county capture as a second word (`"CULPEPER In"`), since "In" starts with a capital letter and wasn't excluded. Added `IN` to `_GLUE`. Confirmed live: clean `"Culpeper"` now.
- **The `order_nisi` rule was NOT ported.** VA is a non-judicial-foreclosure state (per the Doors-Per-Deal research elsewhere in this file), so the Circuit-Court-ratification notice pattern that MD's judicial counties produce doesn't have an obvious VA equivalent, and no VA sample surfaced one. Left out on purpose, not an oversight — revisit if a VA order-nisi-shaped notice ever actually shows up.

**One fix is real but not yet re-confirmed against a full live VA run**, purely because Firecrawl itself started throwing five straight `500 Internal Server Error` responses on the actual multi-action VA pull request while otherwise being reachable (confirmed: a trivial one-off Firecrawl call to a different URL succeeded seconds later, so this is Firecrawl having a bad day on this specific heavier payload, not a code problem). That fix: making the state-name match **case-insensitive** (`re.I` added to both `ADDRESS_RE` and `ADDRESS_FALLBACK_RE`, in both scripts) — the bug was that `"...CULPEPER, VIRGINIA 22701..."` is ALL CAPS in the actual notice text, while the state-name literal added for MDDC's fix was only ever tested against title-case `"Virginia"`. Unit-tested directly against the real failing string and passed (`parse_address()` now correctly returns `street="5678 SAMPLE RUN LANE", city="Culpeper", state="VA", zip="22701"`), and MDDC's own live re-run confirmed adding `re.I` didn't regress anything there (still 10/10 clean). Basem said to stop retrying rather than keep burning calls against a flaky service — pick this back up with one more live VA run before calling VA fully closed.

### MD Probates — QUEUED, not started this pass. Requirements captured for next time:

- **Search an exact date, not a range** — the pull currently defaults to a rolling day-window; Basem wants a specific date. Either scope per-county or pull everything and filter down to the target estates afterward.
- **Personal Representative data must be split into separate columns**, not flattened into one string. The test sheet currently renders `personal_reps` as a single semicolon-joined `"NAME (street, city, state zip); NAME2 (...)"` cell — needs `pr1_name/pr1_street/pr1_city/pr1_state/pr1_zip`, `pr2_...` etc. as real columns instead.
- **SDAT cross-check: use the property address/street, not the tax account ID.** Basem's explicit correction: *"regarding SDAT check - use the property address and then street dont use tax axxount ID."* The current `_sdat_confirm()` / `lookup_sdat()` path searches SDAT by District+Account (Property Account Identifier), which is also the path with the still-open bug below (a required "Subdivision" field the automation never accounted for). Switching to SDAT's **Street Address** search type instead sidesteps that bug entirely — worth trying before sinking more time into the Account-Identifier form's Subdivision field.
- **Still-open bug from the prior session, relevant to whichever SDAT path is used**: `DEED_TAX_ID_RE` was fixed (it only matched the label `"TAX ID NO:"`, but a real deed said `"Property Tax I.D.# 03-02366800"` — confirmed by pulling the raw deed PDF text, now fixed and unit-verified: `parse_deed_text()` correctly extracts `tax_id="03-02366800"` from that label now). But the SDAT District+Account search itself was ALSO found broken separately: the live form actually has a third required field, **"Subdivision"** (`#..._ucEnterData_txtSubDiv`), that the automation never filled in at all — even after adding it and switching District/Account to the same reliable native-setter JS pattern used everywhere else in this codebase, the submitted values still weren't reaching the server on the last test. If the Street-Address-based approach above works, this whole Account-Identifier-search bug may become moot rather than needing its own fix.

### MD Legal Notices — QUEUED, not started this pass. Requirements captured for next time:

- **Add the notice's publication date to the sheet.** The pull already parses `published_on` (`NOTICE_PUBLISHED_RE`) but the test-sheet tab never included that column.
- **Address search is the same flow as MD Probates** — wire `find_property_for_decedent()` (land-records-first, SDAT-cross-check-fallback) into the Legal Notice Search path the same way, once that engine is actually working end-to-end from the Probates fix above.
- Carried over from the prior session, already fixed and verified live: the county-checkbox postback race (a `SUSPECT_STATEWIDE_LEAK_THRESHOLD` guard retries with a longer wait when the reported total looks like an unfiltered statewide leak — caught live, 1774 statewide → 153 correctly Anne-Arundel-scoped) and explicit classification of "NOTICE OF JUDICIAL PROBATE" hearing notices (a different notice subtype from a PR-appointment notice, now counted and skipped rather than silently producing a zero-record page).

### Session paused here on explicit instruction ("update claude.md with progress and hold it there")

Next session picks up at **MD Probates**, in the order above (exact-date search → PR column split → SDAT-by-street-address). `output/data_extraction_test.xlsx` currently reflects the finished MDDC + VA work; its MD Probates and MD Legal Notices tabs still show the PRIOR session's data (0/5 addresses resolved, no publication-date column) and need a fresh pull once that work resumes.

## Obituary Deep-Prospecting Batch + Reload (build ~1.0.50, 2026-08-28, PILOT DONE, FULL RUN BLOCKED ON CARD)

`src/scripts/obituary_dp_batch.py` runs the deep-prospecting-v5 heir engine as a BATCH over a DataSift export and writes the heirs back onto the same records. Built for `Hottest - 00 Needs Skipped` on the moe@galaldev.com account: 641 Obituary-list records (deceased owners, zero phones, all Priority 1, 9 counties). One orchestrator, eight resumable stages, every artifact under `output/dp_hottest_needs_skipped/`:

```bash
python -u src/scripts/obituary_dp_batch.py prep --export "<DataSift export>.csv" --pilot 25
python -u src/scripts/obituary_dp_batch.py trace --csv smartskip_input_pilot.csv            # FREE: shows the exact bill
python -u src/scripts/obituary_dp_batch.py trace --pay-id <bulkSkipId> --max-entities 30    # bills the card, polls, downloads
python -u src/scripts/obituary_dp_batch.py rank --tracerfy      # signers, REL slots, Tracerfy gap-fill for phoneless signers
python -u src/scripts/obituary_dp_batch.py research             # obituary search + Sonnet; who died, DOD, spouse trap
python -u src/scripts/obituary_dp_batch.py augment              # obituary-named signers SmartSkip missed -> Tracerfy
python -u src/scripts/obituary_dp_batch.py score                # Trestle tiers on every number that will be uploaded
python -u src/scripts/obituary_dp_batch.py build                # reload CSVs, phone_tags.csv, review.xlsx, push_plan.json
python -u src/scripts/obituary_dp_batch.py schema --commit      # REL{N}: Full Name / Phone 1-3 fields, capped at REL7
python -u src/scripts/obituary_dp_batch.py push --route api --probe --commit   # ONE record, read back
python -u src/scripts/obituary_dp_batch.py push --route api --limit 30 --include-no-numbers --commit
```

**Writeback convention (Basem):** the decedent stays the record owner; every relative's phones go on the owner tagged `Rel{N}.{M}` plus the Trestle tier; the relative's name lands in `REL{N}: Full Name` and their numbers in `REL{N}: Phone 1..3` (the account's existing IDI-import shape); `Deceased Owner` / `DP Death Unverified` / `Owner Alive - Spouse Deceased` plus `Deep Prospected MM/YYYY` as property tags; a note carrying decedent, DOD + source, verdict, ranked signers with tiers, obituary URL, flags and a MUST VERIFY line. **REL slots are capped at 7** (the existing five plus two; Basem 2026-08-28: "just 2 is enough"). REL8-13 were created and deleted the same day (`DELETE /api/internal/custom-fields/{numeric id}/` -> 204; by uuid it 404s). Relatives past the cap are named in the note only.

**THE INTERNAL API ACCEPTS WRITES ON THIS ACCOUNT.** The "403 on writes" recorded for the MDDC upload was specific to `POST /property/` (the Open-API route). With the same minted JWT, `POST /api/internal/custom-fields/` (201), `POST /api/internal/owner/{owner_uuid}/upsert-phones/` (200, returns `added: [...]`), `PATCH /api/internal/property/{uuid}/custom-field/update-values/` (200), `PATCH /api/internal/property/{uuid}/` with the FULL merged tag list (200) and `POST /api/internal/property/{uuid}/add-notes/` (204) all landed and read back. So the Playwright wizard was never needed; `push --route wizard` exists as the fallback and `sift_upload_wizard.run_upload` gained a `has_phones` kwarg for it. Phone-type custom fields accept bare 10-digit strings and read back as `+1...`.

**Pilot result (25 records, verified by read-back on all 25 and by `dp_record_pull.py` on 4):** 257 phones landed, 22 records got numbers (3 got none and carry `DP No Numbers 08/2026`), REL fields 100% on every record, owner name unchanged on every record. Research: 13 owners confirmed dead with obituary + DOD, 2 spouse/relative obituaries (owner status unknown), 10 unresolved. Cost: SmartSkip $3.75, Trestle ~$6, Tracerfy ~$0.70.

**FULL RUN BLOCKED 2026-08-28: the 603-record SmartSkip order (`<pay-id: in Claude memory, smartskip trap note>`, $90.45) was DECLINED by the only card on file (Amex ...5009).** The order is submitted and calculated; `trace --pay-id <pay-id: in Claude memory, smartskip trap note>` re-attempts payment once the card is sorted or a second card is added at app.smartskip.io. The wallet ($49.70) cannot fund bulk skip. `smartskip_input_full.csv` (603 rows = 628 traceable minus the 25 pilot) is the input.

### What the pilot taught, in the order it bit

- **SmartSkip resolves a NAMESAKE CHILD instead of the owner on senior-owner lists.** 7 of 25: "Edith Sample, owned since 1965" came back as a 61-year-old subject whose "Mother" is EDITH SAMPLE, 96. Every Possible Type is then relative to the wrong person and the intestacy ranker reads "parents take", handing the estate to the decedent. `_reroot_namesake()` re-roots the graph on the same-name Parent (subject -> Child, other parent -> Spouse, siblings -> Children, subject's spouse -> In-Law) and measures the age-sanity check from the decedent's age. Second signal: a subject who would have been under 18 at `Owned since` did not buy the house.
- **The obituary layer changes signer sets, it does not decorate them.** Doeja's SmartSkip "spouse" Roeja had predeceased him (dropped via `drop_names`); Sample's obituary hit was CAROL Sample of the same town (the spouse trap in the wild, now caught by `_name_from_hit` on same-surname hits); Roeworth had zero SmartSkip relatives and an obituary naming a wife and sister (`augment` traces those at the property address, $0.02 each). Doeworth's obituary DOD was 2018 against a 2026 SiftMap obituary date: the 3-year rule downgrades it to unresolved.
- **A verdict that rests on an error must not be cached.** DDGS answered the first handful of searches and then returned "No results found." for everything, which reads exactly like a person with no obituary; 22 records were frozen as unresolved before the search layer got retries, a Firecrawl `/v1/search` fallback and a rule that search/LLM errors block caching. The `anthropic` SDK, `ddgs` and `gender_guesser` were all missing on this machine; each failure was silent (the LLM path returned None, `canon_rel` degraded to neutral labels).
- **Trestle 403 AUTHENTICATION_FAILED was the transport, not the key.** `urllib` with the default `Python-urllib` user agent is refused; `requests` with the same key returns 200. `obituary_dp_run.trestle_score` uses urllib and "failed" 390 of 390 pilot numbers; scoring now goes through `phone_validator.call_trestle`. Zero-char fetches (legacy.com renders client-side) must not spend a page slot, or the right echovita page never gets opened.
- **Two REL vocabularies exist on the account.** `REL1: Full Name` appears twice: active in group 14 "Custom Fields 1" and INACTIVE in group 22 "Probate without PR". A label-keyed index picks the dead one and every value lands on a field nobody sees; `_field_index` keeps active fields only and prefers group 14.
- **Trust titles parse two ways.** Recorder abbreviations run LAST FIRST M ("Condon Mark Stephen Tr"), long-form titles run FIRST M LAST ("Norma Veltri Trust"); a lone initial's position decides first, then which token is a known given name, then the title style. 55 trustee names parsed, 13 rows (family trusts naming no human, one LLC) go to `unusable.csv` for a deed lookup. Enformion BusinessV2 is not used: family trusts are not registered with a Secretary of State.
- **The reload does not flip DataSift's `skiptraced` flag**, so records that got no numbers stay in `00 Needs Skipped` (tagged `DP No Numbers`) rather than moving to `01 Skipped No Numbers`; records with numbers leave `00` on `has_numbers` alone.

Memory: `project_smartskip_funded_namesake_trap` (the "SmartSkip unfunded" note was stale; the card WAS charged for the pilot, then declined on the $90 order).

## Account-Wide Enrichment + Scoring Audit (2026-08)

A one-off but committed audit of the live DataSift account, built alongside the MDDC pipeline (`src/scripts/live_pull.py`, `refresh_blank_structure.py`, `score_all_records.py`, `score_live_pull.py`, `score_live_pull_townhouse_condo.py`, `export_still_blank.py`).

**DataSift's `/api/internal/` list endpoint hard-caps offset pagination at 10,000 items** ("Can't fetch more than 10000 items!"). Worked around by recursively splitting the account's `created` date range into buckets, each queried under a `SAFE_LIMIT = 9000` ceiling and split further if still saturated. Full account hydration went through the per-record detail endpoint instead of the list endpoint (the list endpoint lacks estimated value, equity, year built, and Lists), 26,645 records total, checkpointed every 250 records to `output/live_account_pull.json`.

**Structure-type coverage:** ~8,282 of 26,645 records (31%) had a blank `structure_type` after the initial pull. After running DataSift's "Enrich Property Information" and re-hydrating just those blanks with `refresh_blank_structure.py`, 2,892 records remain permanently blank, of which 2,884 (99.7%) have no APN/parcel_id at all — almost entirely apartment/condo-unit addresses, where the county assessor tracks one parcel per building rather than per unit. `export_still_blank.py` exports this residual list to `output/still_incomplete.csv` for manual review/re-enrichment.

**Scoring-engine gotcha, fixed at the adapter level rather than in the engine:** `src/lead_manager.py`'s `_score_timeline()` has no floor on `days_until`, so a `tax_auction_date` far in the past scores as "hot: auction in -N days" (a 709-day-old filing scored this way in testing). `score_all_records.py`/`score_live_pull.py` withhold any date past a staleness floor from the scoring engine and surface it as a separate "Auction Note" column instead — `lead_manager.py` itself was not changed.

## Phone Validator: Multi-Contact Export Detection + Reinsertion (build 1.0.45, 2026-08-23)

`skills/phone-validator/scripts/validate_phones.py` and its internal twin `src/phone_validator.py` only recognized bare `Phone 1`..`Phone 30` columns, so on any export that names phones by contact instead of by flat slot, every one of those phones was silently never sent to Trestle -- no error, no warning, just zero coverage. Confirmed live: an MDDC probate "ready for dialing" export (`PR First Name`/`PR Last Name` + `PH: Phone1`..`PH: Phone5`, plus `REL1: Full Name` + `REL1: Phone 1`..`REL1: Phone 3` blocks through `REL5`) went from 0 phones extracted to 768 across 136 rows once the detector could see that layout. This is the same class of bug the FTM runbook keeps rediscovering elsewhere in this codebase: **a run that succeeds with zero data found is worse than one that fails loudly.**

**Two real export layouts are now auto-detected**, and a third that doesn't match either raises an error naming the headers seen rather than proceeding with zero phones:
- **Flat** -- DataSift's wide "Phone Enrichment" export (`Phone 1`..`Phone 30`, optionally paired with existing `Phone Tags 1`..`30`). One generic contact per row. Verified live against a real 26,643-row account export (`All Records 8.21.2026.csv`): 172,618 phone entries, 151,227 unique.
- **Contact blocks** -- the PR/relative "ready for dialing" layout above. Each `<Prefix>: Phone N` column group becomes its own contact (`PH`, `REL1`..`REL5`), matched to its name via `PR First Name`/`PR Last Name` for `PH` or `RELn: Full Name` for each relative.

**Qualification engine added** (previously the script only had score-based tiering, nothing else): litigator-risk override (`phone_is_litigator_risk == true` -> always `"Litigator Risk"`, never re-scored, never a fallback) -> invalid (`is_valid != true` -> `"Invalid"`) -> skip line type (Tollfree/Premium/Voicemail -> `"Skip - <LineType>"`; NonFixedVOIP and Landline are deliberately NOT auto-skipped, same 24%-miscategorization reasoning as before) -> the existing 5-tier activity score. `phone_tags_for_datasift.csv` now actually excludes Litigator/Invalid/Skip/Drop numbers -- the skill's own docs already claimed this but the code never enforced it until now.

**Reinsertion output** (`reisift_reimport_with_phone_tags.csv`/`.xlsx`, new): every phone's tag gets written back next to the exact phone/contact it came from, never deleting or blanking anything. Flat exports merge into the existing `Phone Tags N` cell (`"Rel5.1"` -> `"Rel5.1, Dial First"`, idempotent on re-run); contact-block exports get a new `<phone column> Tag` column inserted immediately after each phone column. This exists alongside the original global `phone_tags_for_datasift.csv` upload path (tags by phone number account-wide), not instead of it -- reinsertion is for when the tag needs to stay tied to a specific contact/slot rather than blast every record that happens to share that number.

`src/phone_validator.py` also reads `.xlsx` directly now (`openpyxl`, already a hard project dependency) -- the standalone community skill stays CSV-only by design (stdlib + `requests` only), so an `.xlsx` "ready for dialing" source needs Save As CSV first for that path. Caught in the process: `write_summary()` in both files was opening its output file without `encoding="utf-8"`, which crashes on Windows's default cp1252 console the moment the tier-breakdown box-drawing characters get written -- every other writer in the file already specified the encoding, this one didn't.

## FTM Foreclosure: multi-pass skip-trace + screenshot-MMS (2026-06; orchestrated from `_api`)

The FTM foreclosure pipeline (consolidate -> single-family filter -> wizard upload -> phone scoring -> cadence) is orchestrated by `_api/ftm_pipeline.py`; these SiftStack scripts are its skip-trace + texting building blocks. Deep detail: the `_api` CLAUDE.md + the `reisift-tagging-and-phone-scoring` / `smrtphone-mms-screenshot-texting` memories.

- **`src/tracerfy_ftm.py`** — Tracerfy re-skip for FTM records (2nd phone source after the free DataSift enrichment). `--all` traces EVERY record (not just no-phone); `--finish` merges found phones into reisift via Add-Data upsert by ADDRESS into the existing "Foreclosure" list. ~$0.02/record.
- **`src/enformion_ftm.py`** — Enformion/Endato 3rd skip-trace pass. Reuses `enformion_heir.person_search` but for the LIVING OWNER (name + property-address anchor; name alone is HTTP-400'd) -> `enf_phones` -> populate `NoticeData.PHONE_FIELDS` -> same merge path. **`clean_owner_name(raw)`** cuts messy co-owner notice strings (AND/&/AKA/C-O markers, Jr/Sr/II-IV suffixes, middle initials, punctuation) to ONE clean (First,Last) so they don't 400. `--addr "<substr,...>"` re-runs specific records; `--finish` merges. **reisift MERGES phones, so Tracerfy + Enformion ACCUMULATE** — run sequentially, then re-score (`_api/score_ftm_phones.py --commit`) + re-tag (`src/run_phone_tag_upload.py --finish`). Live 2026-06-25: 109 -> 302 phones across 33 records, 32/33 with a Dial 1/2. CWD: run `run_phone_tag_upload.py` from the SiftStack root (relative `output/` path).
- **`src/mms_sender.py`** — GATED browser sender for the foreclosure screenshot-MMS (texts each homeowner the auction-notice Dropbox image + a personal message). Built + validated, **PAUSED pre-send (needs Ty's explicit GO).** Drives the **SmrtPhone web app** (SmrtPhone's API can't do MMS): a 2-step send — the TEXT via the new-message "Compose Message" modal, then the IMAGE via the conversation reply box, which lives in the **`main-iframe`** (`page.frame(name="main-iframe")` -> set the screenshot on its hidden `input[type=file]` -> click the send arrow by `bounding_box()` screen position). Reuses `datasift_core` Playwright primitives. Session captured to `smrtphone_state.json` by `_api/smrtphone_login.py`. Recipients/compose/schedule live in `_api` (`build_mms_recipients.py` pulls from the "FTM - 02 Ready to Call" preset). Full mechanism: the `smrtphone-mms-screenshot-texting` memory.

## Apify Deployment

The project runs as an **Apify Actor** in the cloud. When `APIFY_IS_AT_HOME` or `APIFY_TOKEN` is set, `main.py` uses the Actor SDK instead of CLI args.

```bash
# Install Apify CLI
npm install -g apify-cli

# Local test (reads input.json, simulates Actor environment)
apify run --purge

# Deploy to Apify platform
apify login
apify push

# On Apify Console: set up daily schedule and configure secrets in Actor input
```

### Actor Input (configured in Apify Console or `input.json`)
- `mode`: "daily" or "historical"
- `counties` / `types`: arrays to filter saved searches (empty = all)
- `tn_username`, `tn_password`, `captcha_api_key`: secrets (required)
- `google_drive_folder_id`, `google_service_account_key`: optional Google Drive upload

### Actor Output
- **Dataset**: structured records pushed via `Actor.push_data()`
- **Key-value store**: `output.csv` backup
- **Google Drive** (optional): CSV + summary text file uploaded via service account

### Key Files
- `.actor/actor.json` — Actor manifest (name, version, Dockerfile path)
- `.actor/input_schema.json` — Input fields + validation for Apify Console UI
- `Dockerfile` — Based on `apify/actor-python-playwright:3.12`
- `src/drive_uploader.py` — Google Drive upload via base64-encoded service account key
- `input.json` — Local test input (gitignored, contains credentials)

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

### Deep Prospecting v5 — SmartSkip heir engine (build 1.0.36, 2026-07-29)

**The heir engine is now SmartSkip, not Enformion.** v4 resolved relatives through the Enformion/Endato Person Search; v5 retires it after a live head-to-head on Knox/Blount records. Enformion **BusinessV2 is retained for entity owners only** (see `src/enformion_business.py`) because nothing else can resolve an LLC/trust.

**The measured case for the swap (12 owners, same records, both sources):**
- **Coverage:** Enformion returned ZERO relatives on **6 of 12** owners; SmartSkip returned relatives on 12/12.
- **Phones:** Enformion's `relativesSummary` carries names but **no phone numbers** — every relative you want to call is another $0.10 search. SmartSkip returns relatives AND their phones in one batch row.
- **Cost:** 100 owners / 682 relatives = **$15.90** (SmartSkip $15.00 + Tracerfy $0.90) vs **$78.20** the Enformion way. **4.9x.**
- **Precision:** on the validation record SmartSkip returned exactly 3 relatives and **all 3 appeared in the published obituary**; Enformion returned a capped 50-name blob plus out-of-state numbers that looked like wrong-person bleed.

**The v5 stack:** SmartSkip ($0.15/hit, relatives + phones) -> Tracerfy ($0.02, gap-fill only for relatives SmartSkip named but left phoneless, ~7%) -> **obituary/web research (mandatory, free)** for date of death + true relationships -> TrestleIQ ($0.015/number) for dial tiers. One record end to end is **~$0.24**. Skill: `Skills for REI/improved/deep-prospecting-v5.skill`; runner `scripts/smartskip_trace.py`; API contract `references/smartskip-api.md` + the `reference_smartskip_api` memory.

**v5 gotchas (all verified live, they are why the research layer stayed):**
- **SmartSkip is WRONG about death.** It returned `Deceased=false` for a man who died 12/06/2025 with a published funeral-home obituary, and it has **no DOD column at all**. Death data comes from the obituary/web pass, always.
- **THE SPOUSE-OBITUARY TRAP (highest-value check in the skill).** An obituary on the record does NOT mean the OWNER died. Live case (2026-07-29, details in the private `project_smartskip_spouse_obituary_trap` memory): a Blount County record sat on the Obituary list in Deep Prospecting status. The obituary was the **owner's husband's**, not hers; the owner was alive and owned the property. It was never an heir case, it was a living senior widow to call gently. An un-researched caller would have asked a recent widow for her dead husband. **Always match the decedent name against the owner of record before treating a record as an heir case.**
- **Relationship labels are coarse.** The column is literally "Possible Type"; **63% came back generic** ("Relative"/"In-Law") on a 100-record batch, and it labeled a 62-year husband a plain "Relative." The obituary overwrites it.
- **The wallet does NOT pay for bulk skip** — it bills the saved Stripe card via `payment-intent`. $25 sat untouched in the wallet while a batch charged the card.
- **Unpaid orders are invisible** in `GET /bulk-skip`; persist the `bulkSkipId` before paying.
- **Entities can't be name-traced** (SmartSkip needs First+Last, Tracerfy is consumer-only). **35 of 321** vacant owners were LLCs/trusts -> route to BusinessV2, filter them out of the batch up front.
- **The owner rule wins on a shared line:** a household number the owner also holds carries source + tier only, never a relationship tag, or the dial sheet labels the owner's own landline "Husband."
- **The 3-year DOD sanity check still anchors on `date_published`.** A stale 2004 index date surfaced during validation.

**Retired (kept only as a v4 reference):** `src/enformion_heir.py` / `scripts/enformion_person_search.py`. Failure modes for the record: zero relatives half the time, no phones on the graph, ~50-relative cap that silently truncates, a surname gate that drops married-out daughters, `isDeceased` flags that lag reality, and wrong-person matches when anchored on city/ZIP instead of the full street line.

---

### Legacy: Deceased-Owner Heir Resolution — Enformion (v4, superseded by v5 above)

The default obituary path extracts survivors/heirs from obituary text with an LLM, which can hallucinate an entire heir map (see `project_obituary_heir_hallucination` memory). The **v4 Primary Path** of the `deep-prospecting` skill replaced this with the Enformion/Endato relatives graph — grounded, nothing inferred. **v5 supersedes this**: SmartSkip now supplies the grounded relative list, so the LLM never invents an heir set, and the obituary layer only confirms relationships and supplies the DOD.

- **Module:** `src/enformion_heir.py` — reusable client: `person_search()`, `relatives_to_survivors()`, `required_signers()` (cost gate: living closest-kin `relativeLevel == "ab"` + decedent surname + DOB), `dedupe_phones()`, and `resolve_heirs_enformion(notice, parsed)` which returns `(ranked_dms, error_info)` shaped exactly like `build_heir_map()` so the rest of the pipeline is unchanged. Heir signing authority reuses `obituary_enricher.rank_decision_makers` (TN intestacy).
- **Pipeline (Step A only, 1 call/record):** `python src/main.py daily --deep-heirs`. In `obituary_enricher` Phase B, a new **Path E** runs Enformion FIRST for confirmed-deceased owners that no cheaper high-confidence path resolved (surviving co-owner on title, court-named executor). Falls through to the obituary-survivor waterfall on a miss or when creds are absent. Default (no flag, and the Apify daily Actor) keeps the old behavior — Enformion is never auto-billed.
- **Full waterfall (one record):** `python src/run_deep_prospect.py --first X --last Y --street "..." --city Knoxville --state TN --zip 37917` runs Steps A-E (decedent → required signers → per-signer search → phone dedupe → Trestle scoring) and prints a master dial sheet. Consolidates the one-off `run_brice_*` scripts.
- **Creds:** `ENFORMION_AP_NAME` / `ENFORMION_AP_PASSWORD` in `.env` + `config.py`. Billed per match ($0.10/search on the DataSift/affiliate rate the community gets; ~$0.35 public rack); misses are free. Detect API failure by HTTP status, NOT the always-present `error` object.
- **DOD conflict:** Enformion's death-index DOD can disagree with the obituary DOD (often a second household death). Surfaced via a `dod_conflict` flag in `missing_data_flags`; never silently resolved.
- **Live-run gotchas (build 1.0.32, from the 7619 Example Oaks / John Q. Sample run):**
  - **Anchor with the full street line on common names.** A name + city/ZIP search returned the WRONG person as `persons[0]` (an Alabama "John B Sample"); only `Addresses:[{"AddressLine1":"1234 Example Oaks Ln","AddressLine2":"Knoxville, TN 37918"}]` pinned the exact record. `enformion_heir.person_search()` currently sends only `AddressLine2` (city/ST/ZIP), so on a common name pass the street line and confirm the match via address history + a cross-referenced relative before trusting `first_match`.
  - **`relativesSummary[].isDeceased` lags and is unreliable** — it showed the decedent, his late wife, and both long-deceased sons as "living." Trust the obituary + the person-level `dod` (the person index had a son's 2014 DOD even though the relatives-summary flag said living).
  - **The relatives graph is capped (~50) and misses married-out daughters** (different surname). Worse, `enformion_heir.required_signers()` gates on a surname match, so it DROPS married-out daughters who are required signers; the skill's shipped `scripts/enformion_person_search.py` correctly gates on `relativeType` (Son/Daughter/Child) and catches them. Always reconcile the signer set against the published obituary's survivor list, not the graph alone.
- **L3 fallback fetcher (Scrapfly ASP, build 1.0.32+):** `src/scrapfly_browser.py` (`ScrapflyBrowserClient.fetch(url)`, plus a `python src/scrapfly_browser.py <url>` CLI) clears Cloudflare/JS walls on county-record + genealogy pages (assessor & deed datalets, FindAGrave, Legacy, court info pages) that plain fetches and sandboxed agent WebFetch fail on. Reuses the `asp=True, render_js=True` core of `scrapfly_client.py` but is URL-generic. `run_deep_prospect.py --fallback-urls "<deed>,<obit>,<docket>"` pulls them inline in the same heir waterfall. **Sweet spot = county/records/genealogy portals** (e.g. recovered deed instrument + joint-owner names when the assessor datalet was blocking plain fetch). **Limits:** hardened people-search aggregators (TruePeopleSearch/FastPeopleSearch) frequently IP-ban ASP (`SHIELD_PROTECTION_FAILED`), and records a county doesn't publish online (Knox TN estate/probate cases, ROD deed images behind a paid subscription) can't be fetched at all (phone/in-person). Residential proxy via `SCRAPFLY_PROXY_POOL` (default `public_residential_pool`). The distributed skill ships a self-contained `scripts/scrapfly_fetch.py` (requests-only, no repo/SDK) for community users.
- **Deliverable = PDF (build 1.0.32+):** deep-prospecting research packs render to a branded PDF via `python src/deep_prospect_pdf.py <pack>.md` (reportlab; no new deps) so they upload cleanly into DataSift/Sift as a record attachment. The renderer keeps the heir map + master dial sheet monospaced and strips em/en dashes + non-WinAnsi glyphs to ASCII.

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

## DataSift.ai (REISift) Integration

DataSift.ai (formerly REISift) is the CRM where scraped records land for niche sequential marketing campaigns. There is **no REST API** — upload is via Playwright browser automation of the web UI.

**Domain:** `app.reisift.io` (NOT `app.datasift.ai`). API at `apiv2.reisift.io`.

**apiv2 JWT (shared with the Deal Room project):** any script hitting `apiv2.reisift.io` reads the shared auth store at `Deal Room Coaching Call/_api/clients/config/reisift_auth.json` (`datasift-admin` = staff ty+1, ~48h access token; NEVER hardcode a Bearer in SiftStack). Refresh: app.reisift.io DevTools -> Copy as cURL -> `python _api/clients/reisift_auth.py add datasift-admin <jwt>` (run with `PYTHONIOENCODING=utf-8`; the checkmark-glyph crash after "saved account" is cosmetic, the save succeeded). Then re-impersonate before client-account calls. Last refresh 2026-07-21, exp 2026-07-23 19:41 UTC.

### Key Files
- `src/datasift_formatter.py` — Transforms `NoticeData` → DataSift CSV (42 columns)
- `src/datasift_uploader.py` — Playwright login + upload wizard + enrich + skip trace + preset management + sequence builder + SiftMap sold workflow
- `test_datasift_upload.py` — Headed browser test (upload + enrich + skip trace)
- `test_manage_presets.py` — Headed browser test (preset discovery + sold exclusion + sequence creation)
- `test_manage_sold.py` — Headed browser test (SiftMap sold property tagging)

### CSV Column Structure (42 columns)
- **Core auto-mapped (11):** Property Street/City/State/ZIP, Owner First/Last Name, Mailing Street/City/State/ZIP, Tags
- **Lists + Notes (2):** Lists (for niche sequential), Notes (contextual per notice type)
- **Built-in fields (13):** Estimated Value, MSL Status, Last Sale Date/Price, Equity Percentage, Tax Deliquent Value, Tax Delinquent Year, Tax Auction Date, Foreclosure Date, Probate Open Date, Personal Representative, Parcel ID, Structure Type, Year Built, Living SqFt, Bedrooms, Bathrooms, Lot (Acres)
- **Custom fields (16):** Notice Type, County, Date Added, Owner Deceased, Date of Death, Decedent Name, Decision Maker, DM Relationship, DM Confidence, DM 2/3 Name/Relationship, Obituary URL, Source URL, Notice Screenshot

### Niche Sequential Marketing
DataSift's niche sequential system uses filter presets to guide records through SMS → Call → Mail → Deep Prospecting phases. Two preset folders: "00 Niche Sequential Marketing" (12 presets, courthouse data) and "01. Bulk Sequential Marketing" (9 presets, bulk data). All 21 presets exclude Sold status (build 1.0.23). A "Sold Property Cleanup" sequence in the Transactions folder auto-fires on "Sold" tag to change status, remove from lists, clear tasks, and clear assignee.

- **"Courthouse Data" tag:** Every record gets this tag — signals first-to-market county data (prioritized over bulk data in filter presets)
- **Lists column:** Maps `notice_type` → DataSift list name (`foreclosure` → "Foreclosure", `probate` → "Probate", `tax_sale` → "Tax Sale", `tax_delinquent` → "Tax Delinquent", `eviction` → "Eviction", `code_violation` → "Code Violation", `divorce` → "Divorce"). DataSift auto-creates lists from CSV.
- **Tags:** Courthouse Data, notice_type, county, YYYY-MM date, deceased/living, DM confidence level, has_auction, tax_delinquent, photo_import (for photo-sourced records)

### Upload Wizard (6 Steps, build 1.0.46 2026-08-22)
DataSift added an **Enrichment** step between Setup and Add Tags; the wizard code didn't know about it and every subsequent step/locator silently ran one step off. Fixed in `datasift_uploader.py`.
1. **Setup:** Click "Upload File" sidebar → "Add Data" → dropdown "Uploading a new list not in DataSift yet" → enter list name → organization questions
2. **Enrichment:** click through (new step, no fields to fill here yet)
3. **Tags:** Skip through — or fill from a `custom_tag` parameter (see below)
4. **Upload File:** Set file on `input[type="file"]`
5. **Map Columns:** Core address fields auto-map; Tags, Lists, and enrichment columns may need manual mapping
6. **Review + Finish Upload:** Click "Finish Upload" — processing happens in background

**`custom_tag` parameterization:** the Add Tags step used to type a hardcoded tag, silently overriding whatever the CSV's own Tags column already said. It's now a `custom_tag` parameter sourced from the CSV, so any upload — not just one specific pipeline's default tag — can carry its own tag through the wizard.

### Column Mapping Notes
- Only core address fields (Property Street, City, State, ZIP) reliably auto-map
- Tags, Lists, Estimated Value, and enrichment columns often stay unmapped in step 4
- Notes and MSL Status sometimes auto-map
- Custom fields (TN Public Notice group) require drag-and-drop mapping

### Contact Logic
- **Deceased owners:** Contact = decision maker (first/last name + mailing address from DM)
- **Living owners:** Contact = property owner (owner mailing address, falls back to property address)

### Post-Upload: Enrich + Skip Trace

After CSV upload, the pipeline automatically runs two DataSift actions via Playwright:

1. **Enrich Property Information** (Manage → Enrich Data): Adds SiftMap property data (beds, baths, Zestimate, sqft, sale history) to uploaded records. "Enrich Owners" and "Swap Owners" are OFF — protects our PR/DM contact mapping.
2. **Skip Trace** (Send To → Skip Trace): Pulls phone numbers (up to 5 per owner) + emails via unlimited plan ($97/mo). Adds auto-tag `skip_traced_YYYY-MM`.

Both run in background — tracked in Activity tab. Both are ON by default when `--upload-datasift` is set.

### CLI Flags
```bash
python src/main.py daily --upload-datasift        # upload + enrich + skip trace
python src/main.py daily --upload-datasift --no-enrich       # upload only, skip enrichment
python src/main.py daily --upload-datasift --no-skip-trace   # upload + enrich, skip skip trace
python src/main.py daily --notify-slack            # send run summary to Slack/Discord
python src/main.py daily --deep-heirs               # resolve deceased-owner heirs via Enformion ($0.10/match DataSift rate, ~$0.35 rack)
```

### Environment Variables
- `DATASIFT_EMAIL` — DataSift login email
- `DATASIFT_PASSWORD` — DataSift login password
- `SLACK_WEBHOOK_URL` — Slack/Discord webhook for run summaries

### Login Selectors (SPA quirks)
- Hidden checkboxes (Remember me, Terms) — click `<label>` elements, not `<input>`
- Use `wait_until="domcontentloaded"` (not `networkidle` — SPA keeps WebSocket connections open)
- Cookie validation: check for `/dashboard` or `/records` in URL (5s wait for SPA redirect)

### DataSift UI Automation Patterns

Hard-won patterns from build 1.0.22-1.0.23 (SiftMap, preset management, sequence builder). Follow these to avoid repeating past mistakes.

**Styled-Components (no native HTML controls)**
- No native `<select>` elements — all dropdowns are `[class*="Selectstyles__Select"]` containers
- `[class*="SelectValue"]` = current value display; `[class*="SelectOptionContainer"]` = dropdown options
- Multiple Select dropdowns exist per panel (Lists, Tags, Property Status) — always target the **LAST visible one**
- Use `x > 450` bounds check in all JS queries to avoid matching sidebar elements (sidebar is 0-400px)
- React state updates require native setter + event dispatch, not just `.value = ...`:
  ```js
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, 'new value');
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
  ```

**Panel Scrolling (Playwright scroll fails)**
- Filter panel is a scrollable `<div>`, NOT the viewport — `scroll_into_view_if_needed()` does nothing
- Use JS: `el.scrollIntoView({behavior: 'instant', block: 'center'})` instead
- Filter Presets section is at the BOTTOM of the filter panel — must scroll container down to reveal
- After scrollIntoView, element y-positions may be negative — don't filter by `y > 0` for the target element

**React DnD (Sequence Builder)**
- Cards have `draggable="false"` — Playwright's native drag won't work
- Must use slow mouse drag: `mouse.move()` → `mouse.down()` → 20 incremental steps (50ms each) → `mouse.up()`
- Add 500ms pauses between down/move/up phases
- "Add new Action +" button required for 2nd+ actions; first action uses initial drop zone
- Sidebar cards can scroll out of view when main area scrolls — scroll BOTH source and target into view before drag

**Pointer Interception (common blockers)**
- Beamer NPS survey iframe (`#npsIframeContainer`) blocks ALL pointer events globally — remove from DOM via `_dismiss_popups()`
- `RecordsFiltersstyles__RecordsFiltersSection` elements intercept clicks — use `page.evaluate()` JS click or `force=True`
- When Playwright click fails with "outside of viewport" or "intercept": switch to `page.evaluate(el => el.click())`
- SiftMap PropertyDetails panel blocks sidebar checkboxes — remove from DOM before interactions

**Preset Management Workflow**
- Flow: open filter panel → scroll to bottom → expand "Filter Presets" → expand folder → click preset → modify → Save (not Save New) → confirm overwrite
- Folder names have case variations ("00 Niche" vs "00 NICHE") — use `.toUpperCase()` comparison
- Preset names follow pattern `^\d{2}\.` (e.g., "00. Needs Skipped")
- 2 folders: "00 Niche Sequential Marketing" (12 presets), "01. Bulk Sequential Marketing" (9 presets)
- All 21 presets have Property Status "Do not include" → "Sold" (build 1.0.23)

**Sequence Builder Workflow**
- Flow: `/sequences` → Create → title + folder → drag trigger → condition → actions tab → drag actions → configure → save
- Duplicate name handling: detect error toast "different sequence title", retry with " V2" suffix
- Actions tab: navigate via "Set the Following Actions" button or URL (`/sequences/new/actions`)
- Autocomplete inputs: after each selection, `fill("")` + Escape to dismiss dropdown before next entry
- "Sold Property Cleanup" sequence exists in Transactions folder (build 1.0.23): Trigger (Property Tags Added) → Condition (Sold) → Actions (Status→Sold, Remove Lists, Clear Tasks, Clear Assignee)

**SiftMap Automation**
- Search by city (NOT county): Knox → "Knoxville, TN", Blount → "Maryville, TN"
- PropertyDetails panel auto-opens on search — remove from DOM before other interactions
- "Add Records to Account" modal: toggle OFF "Do not replace owners", add tags, dismiss dropdown by clicking heading (NOT Escape — clears tags)
- Known limitation: SiftMap filters (price, date) set values visually but don't trigger React re-query. Only sidebar-visible properties (~3-5) get added per run

**Market Finder Extraction Patterns (build 1.0.29+)**

Hard-won patterns from building `extract_market_finder.py`. The Market Finder UI differs significantly from the rest of DataSift.

- **NO HTML `<table>` element** — data table is entirely div-based: `Tablestyles__TableContainer` → `TableRow` → `TableCell` (styled-components). Searching for `<table>` or `<tr>/<td>` finds nothing.
- **PAGINATION, not infinite scroll** — table shows 20 rows per page with "1-20 of N" text and `PaginationInnerContainer` with prev/next `<button>` elements. Must click through ALL pages to get complete data. Knox County has 48 ZIPs (3 pages) and 120+ neighborhoods (7 pages).
- **State/County selection uses `InputMultiSearch`** — NOT styled-component Select dropdowns. Inputs have placeholders: `"Select States"`, `"Select Counties"`, `"Select ZIP Codes"`. Click input → type name → click dropdown result item (`[class*="Item"]:has-text("...")`).
- **ZIP/Neighborhood toggle is a styled Select dropdown** — at the top bar with `Selectstyles__SelectValue` showing current view. Check the displayed text BEFORE clicking — if already on the correct view, clicking toggles AWAY from it. Only click to switch if the displayed text doesn't match the desired view.
- **Beamer push modal (`#beamerPushModal`)** — appears on fresh login, blocks ALL pointer events. Different from the NPS survey (`#npsIframeContainer`). Both must be removed from DOM before any click interactions. Always call dismiss with `force=True` as fallback.
- **Page body scrolling required** — pagination controls are at `y=1867`, below the viewport (`clientH=824`). Must scroll `AdminPage__AdminPageBody` container down before pagination buttons are accessible.
- **Summary panel on right side** — shows county-level aggregates: Median Home Value, Homes on Market, Mo. Investor Transactions, Homes Sold Last Month, Market Rent, Gross Rental Yield, Homeownership Rate. Extract via regex on page text.

```bash
# Extract all Market Finder data for a county
python src/extract_market_finder.py --state "Tennessee" --county "Knox" -v
python src/extract_market_finder.py --state "Tennessee" --county "Knox,Blount" --headless

# Output: JSON file in output/market_finder_{state}_{county}_{timestamp}.json
```

## REI Skill Library (22 Skills)

Distribution-ready Claude Co-Work skill files at top-level `skills/` and `plugins/` (source of truth: `skills/manifest.json` — 22 current entries + 2 superseded, `source_dir` per entry). Each `.skill` is a ZIP containing `SKILL.md` + `references/` folder. Plugins (`.plugin`) also include `commands/` and `.claude-plugin/plugin.json`.

### Skill Inventory

| # | File | Division | Score | What It Does |
|---|------|----------|-------|-------------|
| 1 | `sift-market-research.skill` | Market Intel | 9.6 | Market Finder reports, zip code scoring (6 weights verified against `market_analyzer.py`), 7-sheet Excel output |
| 2 | `first-market-county-data.skill` | Market Intel | 9.7 | County clerk data extraction for all 7 notice types, FOIA templates, marketing windows |
| 3 | `buyer-prospector.skill` | Market Intel | 9.6 | Cash buyer list from 84K+ records, LLC/trust/corp research, 50-state SOS URLs |
| 4 | `real-estate-comping.skill` | Deal Analysis | 9.7 | Two-Bucket ARV, disclosure/non-disclosure routing (12 states), adjustments verified against `comp_analyzer.py`. API-first comp acquisition (Zillow /search per comp-package) with manual browsing fallback + bedroom-band dual-track rule (2026-07) |
| 5 | `rehab-estimator.skill` | Deal Analysis | 9.8 | 912-line skill, complete Repair Cheat Sheet verified against real contractor SOW, 4-tier system |
| 6 | `deal-analyzer.plugin` | Deal Analysis | 9.6 | Combined comp+rehab pipeline, MAO (75%/70% rules), multi-loan financing, exit strategy comparison. Phase 3 now routes comp acquisition API-first (comp-package contract) with the bedroom-band rule (2026-07) |
| 7 | `deep-prospecting-v5.skill` | Deal Analysis | v5 | **SmartSkip heir engine** (relatives + phones in one batch call) + mandatory obituary/web research for DOD and true relationships + Tracerfy gap-fill + Trestle tiers. ~$0.24/record, 4.9x cheaper than the retired Enformion person path. Ships the spouse-obituary trap, the unreliable-deceased-flag gotcha, and the owner-rule-on-shared-lines rule. Enformion BusinessV2 kept for entity owners only |
| 8 | `probate-property-finder.skill` | Deal Analysis | 9.7 | Property lookup for probate decedents, 3-tier search (Tax API→Executor→People search), confidence scoring |
| 9 | `phone-validator.skill` | Operations | 9.8 | Trestle API scoring, 5-tier dial priority, 3 tier strategies, litigator/invalid/skip-line-type qualification, 4.75x connect rate. Auto-detects both the flat DataSift export and per-contact PR/relative "ready for dialing" layouts (2026-08); writes tags back into a reinsertable export, not just the global tag-by-phone-number CSV |
| 10 | `sequential-presets.skill` | Operations | 9.5 | 12 niche + 9 bulk filter presets, Pendulum Theory (SMS→Call→Mail→DP), DataSift UI implementation steps |
| 11 | `sift-sequences.skill` | CRM | 9.5 | 26 TCA sequence templates (verified against `sequence_templates.py`), UI walkthrough, HOT A01-A16 chains |
| 12 | `sift-operations.plugin` | CRM | 9.3 | CRM operations encyclopedia, STABM routine, lead pipeline (9 statuses), task presets, team roles |
| 13 | `playbook-creator.skill` | Operations | 9.5 | Playbook/SOP generator from transcripts, 7-node chart limit, 5th grade reading level, Word doc output |
| 14 | `text-touch-builder.skill` | Operations | 2026-08 | Four-text-touch pre-call SMS sequence per ready-to-call record (identity check, drip, soft ask, breakup) with cold-email style copy rotation; CSV export -> stdlib script -> Add-Data re-import into Text Touch 1-4 custom fields. **Human-voice gate added 2026-08:** `AI_TELLS` refuses (not warns) any message or pool variant containing an em/en dash, a semicolon, a link, emoji, ALL CAPS, stacked exclamations, form-letter openers, or AI vocabulary; `--check-pools` audits the variants and runs on every invocation. Same list mirrored in `src/sms_agent/respond.py` so outbound touches and inbound replies sound like one person. Community-safe (no internal API) |
| 15 | `cold-call-coach.skill` | Operations | new | Pull SmrtPhone cold-call recordings, audio-model transcription with real tonality notes, grade vs the cold-calling rubric (measured reliability +/-3 pts, calibration examples, short calls on their own scale, JSON score footers), Excel workbook export. Self-contained scripts, config-driven roster |
| 16 | `lead-manager-coach.skill` | Operations | new | Same engine, lead-management rubric: 4 pillars qualification, roadblocks, no-ladder, next-action discipline. Call quality only (no CRM hygiene scoring) |
| 17 | `closer-coach.skill` | Operations | new | Same engine, closer rubric: money conversation, three-option offer stack, objection frameworks, commitment locking, negotiation timeline reports |
| 18 | `kpi-engine.skill` | Operations | new | Universal DataSift KPI reporting from the user's own account: activity-log pull (self-contained stdlib script, own JWT, no internal API), three distinct rates, lead counting incl new_lead statuses, funnel pacing (dials->correct->leads->appts->contracts), record-level detail mode, md/CSV/Excel/Slack outputs. Benchmarks shipped as tune-per-operation baselines; internal production version lives in Deal Room `_api/kpi-engine/` |
| 19 | `comp-package.skill` | Deal Analysis | new | Boundary-filtered comp package: /search API pull with 41-row-cap band partitioning, condition bucketing by price/Zestimate ratio, dual-track ARV (same-bed base + labeled reconfig upside), 3-scenario rehab, MAO math, buyer targeting, Excel deliverable spec. Community-safe (own OPENWEBNINJA_API_KEY, requests-only script) |
| 20 | `caller-reputation-monitor.skill` | Operations | new | Keeps outbound cold-calling numbers out of carrier "Spam Likely" labels: daily SmrtPhone-caller-ID health monitoring off your own call outcomes, warm-up/active/watch/rest/retire lifecycle with dial caps, HTML health dashboard, recommended dial pool, carrier registration + flag remediation walkthrough |
| 21 | `candidate-intake.skill` | Operations | new | Aggregates job applicants from Indeed, Gmail, Facebook group posts/Messenger, or pasted text into one running scored master Google Sheet; reviews the ranked list and sends screening outreach to the best candidates. Runs through the Claude in Chrome extension, no API keys |
| 22 | `team-hiring.skill` | Operations | new | Plans, posts for, interviews, and onboards a remote REI team (data manager, prospector, lead manager, acquisitions manager, dispo): role KPIs, hiring-geography cost arbitrage, pay bands and commission, job descriptions, KPI-anchored interview guide, first-week onboarding. Pairs with candidate-intake |

### Cross-Skill Verified Consistency

These values are identical across all skills that reference them:
- **Phone tiers:** 81-100 (Dial First), 61-80 (Dial Second), 41-60 (Dial Third), 21-40 (Dial Fourth), 0-20 (Drop)
- **Preset folders:** "00 Niche Sequential Marketing" (12 presets), "01. Bulk Sequential Marketing" (9 presets)
- **Sequence count:** 26 TCA templates across 5 folders (Lead Management 6, Acquisitions 6, Transactions 6, Deep Prospecting 4, Default 4)
- **Comp adjustments:** Bedroom $5,000, Bathroom $7,500, $/sqft $85, Age $500/yr (from `comp_analyzer.py`)
- **Financing defaults:** HML 12%, conventional 7%, 2 points, 2.5% closing (from `deal_analyzer.py`)
- **DOD sanity:** MAX_DOD_GAP_YEARS = 3 (from `obituary_enricher.py`)
- **Notice types:** 7 total (foreclosure, tax_sale, tax_delinquent, probate, eviction, code_violation, divorce)

### Key Corrections Made During Optimization (April 2026)
- **Hardcoded credentials removed** from sift-market-research (had email/password in SKILL.md)
- **Bedroom adjustment corrected** from $10K to $5K in real-estate-comping (matched to `comp_analyzer.py`)
- **HML points corrected** from 0% to 2% in deal-analyzer (matched to `deal_analyzer.py DEFAULT_HARD_MONEY_POINTS`)
- **Linux paths fixed** in sequential-presets (was `/home/ubuntu/skills/...`, now relative)
- **Preset names aligned** across 3 skills to match `niche_sequential.py` source code
- **Transfer tax labeled** as Tennessee-specific in deal-analyzer with state reference table for top 10 states
- **"Substantial renovation" defined** in real-estate-comping: kitchen + 1 bath minimum (~$15K spend)

### Skill File Structure
```
skill-name.skill (ZIP containing):
├── SKILL.md              # Main skill instructions
├── references/            # Domain knowledge files
│   ├── *.md              # Reference documents
│   └── *.pdf             # SOPs, guides
└── scripts/              # Optional automation scripts
    └── *.py / *.js

plugin-name.plugin (ZIP containing):
├── .claude-plugin/
│   └── plugin.json       # Plugin manifest
├── commands/             # Slash commands
│   └── *.md
├── skills/
│   └── skill-name/
│       ├── SKILL.md
│       └── references/
└── README.md
```

## My Defaults

- **Main counties:** Washington DC; Montgomery, Anne Arundel, Frederick, Carroll, Calvert, Charles, and Baltimore County (not Baltimore City) in Maryland; and Fairfax, Prince William, Arlington, Stafford, and Spotsylvania Counties plus the independent city of Fredericksburg in Virginia. Coverage: the 7 MD counties + DC are already the `DEFAULT_COUNTIES` in the MDDC Trustee's Sale pipeline (`src/scripts/mddc_trustee_sale_pull.py`). The 5 VA counties + Fredericksburg City have no scraper wired up yet — `mddcpublicnotices.com` has no Virginia checkbox at all, so deliberate VA coverage needs a separate public-notice source, not yet identified.
- **Daily summary channel:** WhatsApp. Note: not currently a wired notification transport — only `SLACK_WEBHOOK_URL` (Slack or Discord-compatible webhook) exists in `notify_slack` today.
- **Preferred run time:** 06:30 America/New_York.
- **Dispositions:** type-based DataSift lists (the auto-created per-notice-type lists — Foreclosure, Probate, Tax Sale, etc. — rather than one consolidated dispo list).

## External Scheduled Task: MD Cases (outside this repo, 2026-08-24)

Not part of SiftStack's own automation (no cron/scheduler in this repo runs it) — flagged here only so a session working in this directory knows it exists and what its failure looks like. Referred to here as **"MD Cases"** (the live Windows Task Scheduler job is still literally named "MD Courts Daily PDF Pull" — that name predates this doc's rename and hasn't been touched on the machine). It runs `Galal Development\automation\daily_mdcourts_pull.ps1` daily at **9:00 AM America/New_York** (machine-local). It downloads that day's `fileYYYY-MM-DD.pdf` from `https://www.mdcourts.gov/data/case/`, retrying hourly up to 9 attempts (9am-5pm) if the file isn't published yet, into `Galal Development\Divorce Cases\`, then launches `master_watch.bat` (which starts `watch_all_case_pipelines.py`, the Shared Case Watcher) unless it's already running. The PDF carries divorce, comptroller, and residential foreclosure filings in one daily feed; this feeds the divorce-case pipeline, a sibling to (not part of) the MDDC trustee-sale/foreclosure pull.

**Failed 2026-08-24 09:00 with a DNS resolution error** (`The remote name could not be resolved: 'www.mdcourts.gov'`), most likely the machine's network wasn't fully up yet right at the trigger. Two things made this harder to catch than it should have been, both visible in `automation\daily_pull_log.txt`:
- The task's own exit code was **0 (success)** even though no PDF was ever saved for the day — `LastTaskResult` in Task Scheduler is not a reliable success signal here. **Ground truth is whether `Divorce Cases\file<today>.pdf` exists**, not the task result or the log's tone.
- The script's own retry-loop and per-attempt log lines (`"Saved ..."`, `"Attempt N/9: not available yet..."`) did not reliably appear in the log even when the download itself worked (confirmed on the manual re-run same day: the file downloaded correctly but neither the "Saved" line nor any retry line was written) — treat `daily_pull_log.txt` as a partial trace, not a complete one, when diagnosing a miss.

**Recovery is just re-running the script**: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "Galal Development\automation\daily_mdcourts_pull.ps1"` — safe to run any time, it no-ops (`"Already have..."`) if today's file is already saved, and skips relaunching the watcher if it's already running.

## Target Market Research: Doors-Per-Deal (MD/DC/VA, 2026-08-22)

Three comparison workbooks pulled from DataSift Community Edition's "Doors Per Deal" tool (`learn.datasift.ai/doors-per-deal-distressors`), covering exactly the 14 jurisdictions above. Basis: single-family, off-market, sold-to-investor deals, 2026-01 to 2026-06 (6 complete months). Source files: `county-compare-*.xlsx` in the parent `Galal Development` folder (outside this repo — reference research, not code). Doors/Deal = live list size / deals that list produced in-window (lower is better); Lift = baseline doors/deal / signal's doors/deal.

**The finding that matters most for this codebase:** every one of the 14 jurisdictions has low-or-zero SiftMap (the paid data provider) coverage for at least Tax sale list; **8 of 14** also lack Foreclosure notices (**corrected 2026-08-26** — this said 12 of 14, and counting the workbooks' own gap paragraphs gives 8: Anne Arundel, Baltimore, Calvert, Charles, Fairfax, Montgomery, Prince William, Spotsylvania name it; Arlington, Carroll, DC, Frederick, Fredericksburg City and Stafford do not. **Four** counties list Tax sale as their only gap, not two: Carroll, DC, Frederick, Fredericksburg City); several also lack Tax delinquent roll (Montgomery MD, Calvert MD, DC, Fairfax VA, Arlington VA, Stafford VA, Spotsylvania VA); Fairfax VA also lacks Probate filings. **None of the 14 have Eviction filings, Code violations, or Divorce filings in SiftMap at all.** These are proven deal-makers nationally and nobody can buy this data for these markets, so a county-direct pull (the MDDC pipeline above, plus recorder/tax/probate office pulls per the `first-market-county-data` skill's method) is not a supplement here — it is the only way in.

**Headline comparison (all 14, from each workbook's Comparison sheet):**

| County | FIPS | Deals(6mo) | Baseline Doors/Deal | SFR Supply | Typical Gross | Margin% | Instit.% | Regime |
|---|---|---|---|---|---|---|---|---|
| Baltimore County, MD | 24005 | 1,065 | 166.3 | 177,078 | $51,000 | 19.8 | 24.9 | judicial |
| Montgomery, MD | 24031 | 556 | 332.2 | 184,700 | $52,000 | 8.9 | 8.5 | judicial |
| Anne Arundel, MD | 24003 | 552 | 268.1 | 147,986 | $62,000 | 14.6 | 21.9 | judicial |
| Frederick, MD | 24021 | 249 | 274.1 | 68,254 | $64,000 | 13.3 | 32.5 | judicial |
| Carroll, MD | 24013 | 107 | 486.6 | 52,065 | $82,350 | 25.8 | 14.0 | judicial |
| Calvert, MD | 24009 | 93 | 348.8 | 32,438 | $80,850 | 24.5 | 39.8 | judicial |
| Charles, MD | 24017 | 206 | 243.0 | 50,056 | $102,000 | 31.4 | 46.6 | judicial |
| District of Columbia | 11001 | 632 | 76.8 | 48,530 | $111,250 | 18.7 | 11.6 | non-judicial |
| Prince William, VA | 51153 | 396 | 216.3 | 85,672 | $71,000 | 14.4 | 39.4 | non-judicial |
| Fairfax, VA | 51059 | 666 | 290.7 | 193,586 | $36,000 | 5.1 | 7.8 | non-judicial |
| Arlington, VA | 51013 | 120 | 250.7 | 30,088 | $41,000 | 4.5 | 5.0 | non-judicial |
| Stafford, VA | 51179 | 176 | 246.2 | 43,325 | $71,000 | 17.8 | 37.5 | non-judicial |
| Spotsylvania, VA | 51177 | 152 | 326.5 | 49,628 | $92,500 | 23.1 | 14.5 | non-judicial |
| Fredericksburg City, VA | 51630 | 25 | 283.5 | 7,087 | $62,000 | 12.7 | 0.0 | non-judicial |

**Best-performing signals per region (from each workbook's Signal Consistency sheet — held Priority 1/2 across most/all counties in that region's own comparison set):**
- **MD 6-county set (Baltimore/Montgomery/Anne Arundel/Frederick/Carroll/Calvert):** "Absentee + Free & Clear" (Priority 1/2 in all 6, lift 2.5-4.6x, sharpest in Frederick), "Absentee" alone (all 6, lift 2-3.8x, sharpest Carroll), "Out-of-State" alone (all 6, lift 2.2-8.3x, sharpest Montgomery).
- **Charles MD + DC:** "Absentee + Free & Clear + Out-of-State" and "Absentee + Free & Clear" both Priority 1 in both (lift up to 8.2x in Charles).
- **VA 6-jurisdiction set (Prince William/Fairfax/Arlington/Stafford/Spotsylvania/Fredericksburg City):** "Out-of-State" alone Priority 1 in 4/6 (lift up to 21x in Prince William), "Absentee + Free & Clear" Priority 1 in 4/6 (lift up to 8.8x, sharpest Prince William).
- **Regime note carried in every workbook:** the 7 MD jurisdictions above are judicial-foreclosure states — Notice-of-Foreclosure-based signals there should defer to Lis Pendens / Final Judgment as the trustworthy court signal instead. DC and all 6 VA jurisdictions are non-judicial.
- **Caveat worth keeping over the raw lift numbers:** a thin-sample/high-churn stack (backed by only 25-30 deals) reads optimistic on doors/deal, since the live list is a snapshot compared against a 6-month deal window — the source sheets flag these "verify locally" rather than trusting the number outright.

**National benchmark context** (identical reference table embedded in all 3 workbooks — 13 metro studies, 3,144 counties): the strongest nationally-consistent signal is "Absentee + Notice of Foreclosure" (median 6.4 doors/deal, 22.7x lift) and plain "Notice of Foreclosure" (6.8 doors/deal, 20.9x lift, held up in 28/28 counties analyzed) — both explicitly non-judicial-state signals; judicial states use Lis Pendens / Final Judgment instead.

The full per-county Combined Ranking and First to Market office-contact detail (400+ rows per workbook) stays in the source xlsx files — not reproduced here.

## Doors-Per-Deal Account Build (build ~1.0.49, 2026-08-25 to 2026-08-26, IN PROGRESS)

Turning the doors-per-deal research into live structure inside the **moe@galaldev.com** account (26,643 records), built to the 5-Day Deal Flow Challenge playbook rather than to a scheme invented here. Plan file: `~/.claude/plans/i-want-you-to-moonlit-salamander.md`. Note the phases below are documented in the order they were RUN, not in numeric order — Phase 2 went first because it could have invalidated the whole approach (if SiftMap filters were not URL-replayable, Phase 4 would be impossible and Phase 1's work wasted).

**STATE AS OF 2026-08-26 — writes have begun.** Phases 0, 1, 2 (all sub-phases), 3 and 6 are complete and were read-only. Two write phases have landed and are verified: **the six entry tags** and **the twelve preset folders**. Still outstanding: the T1 geography fix, the 73 presets, the tagged SiftMap pull (Phase 4) and QA (Phase 7). See "Next session picks up here" at the end of this section.

| Artifact | What it holds |
|---|---|
| `data/dpd_tier_configs.json` | **the authoritative one** — 14 T1 + 14 T2 configs as replayable SiftMap URLs, sized in band against live single-family counts |
| `data/dpd_ftm_registry.json` + `dpd_ftm_coverage.csv` | the 126-cell first-to-market coverage matrix and build plan |
| `data/dpd_zips.json` / `dpd_neighborhoods.json` / `dpd_dead_neighborhoods.json` | Phase 1 geography: T2 ZIPs, T1 neighbourhoods (full ranked pools), 381 dead neighbourhoods |
| `data/dpd_buildability.json` / `dpd_signal_rankings.json` | every ranked stack per county and whether SiftMap can build it |
| `output/dpd_siftmap_filters.json` / `dpd_siftmap_field_names.json` | the confirmed filter vocabulary |
| `output/dpd_baseline.json` | the rollback reference — **guard it**, see the Phase 5 section |

`data/dpd_widen_search.json` is now **superseded**: its answers were folded into the tier configs and it is kept only as the search's working record.

```bash
# Read-only. Safe any time.
python src/scripts/dpd_doctor.py                       # capture the baseline (see the WRITE GUARD below)
python src/scripts/dpd_doctor.py --verify              # re-capture and diff; asserts nothing was removed
python src/dpd/jurisdictions.py                        # the 5-scheme county-id coverage matrix
python src/scripts/dpd_workbook_extract.py             # workbooks -> rankings, FTM sources, coverage gaps
python src/scripts/dpd_market_research.py --extract    # Market Finder, all 14 (resumable)
python src/scripts/dpd_market_research.py --score      # -> dpd_zips / dpd_neighborhoods / dead
python src/scripts/dpd_siftmap_discover.py             # probe filter params against a county baseline
python src/scripts/dpd_rail_controls.py                # the buy box and the AI-score controls
python src/scripts/dpd_distressor_hunt.py --merge      # foreclosure notice-type params -> filters json
python src/scripts/dpd_buildability.py                 # which ranked stacks SiftMap can actually build
python src/scripts/dpd_tier_configs.py --measure       # build AND size the 28 configs (see the trap below)
python src/scripts/dpd_widen_search.py                 # find a shippable T1 for counties out of band
python src/scripts/dpd_fold_widen.py --commit          # fold those into the authoritative tier config
python src/scripts/dpd_ftm_registry.py --csv           # the FTM coverage matrix + build plan + creds

# WRITES. Each is dry-run by default, --commit is explicit, each verifies by read-back.
python src/scripts/dpd_tags_create.py --discover       # read-only: dump the /tags/property controls
python src/scripts/dpd_tags_create.py --commit         # the six entry tags   [DONE 2026-08-26]
python src/scripts/dpd_presets_build.py --folders --commit   # the 12 folders [DONE 2026-08-26]
```

**TRAP: never run `dpd_tier_configs.py` without `--measure`.** Its `main()` calls `build()` from scratch and only writes measured counts when `--measure` is passed, so a plain run silently discards every live count in the artifact — and the fold on top of it. Re-fold with `dpd_fold_widen.py --commit` after any re-measure.

**WRITE GUARD: `dpd_doctor.py` with no `--verify` OVERWRITES `output/dpd_baseline.json`.** That file is the rollback reference for the whole build. It now refuses to overwrite on an incomplete capture and keeps a dated copy of the previous one, but the safe habit is `--verify` unless you deliberately intend a new baseline.

**The doctrine comes from three challenge-hub guides, and it is what decides the preset counts** (fetched via Firecrawl because the pages are JS-rendered and WebFetch returns only fragments):
- [siftmap-mastery](https://learn.datasift.ai/siftmap-mastery) — the **4-layer filter funnel** (base characteristics -> geography -> distressors -> AI score, ~48,000 to ~270) and **three preset tiers**: T1 Hyper-Targeted 200-500 (top 3-5 neighborhoods + 2-3 stacked distressors + AI score, niche sequential), T2 Focused 20-25K (top 3-5 zips, bulk + mail), T3 Volume 48K+ (county-wide, cold call only). Also the 17 proven configurations (9 singles, 2 age filters, 6 power stacks) and the rule "two filters is good, three is gold". **The AI/Investor Score is the single most efficient lever: 90+ runs ~22.8 doors/deal, 80 ~30.6, against ~218 for a broad single-family baseline** — but see Phase 2f: the filter works and every property on THIS account scores empty, because AI data is a paid per-property add-on that has not been bought. Basem decided 2026-08-26 not to buy it for now, so this layer is absent from every tier config.
- [market-finder-workflow](https://learn.datasift.ai/market-finder-workflow) — the **4-phase drill-down** (state, county, zip, neighborhood) on 4 metrics (median sales price, DOM, median home value, investor transaction volume), the **60% price range rule**, **under 3 months of supply**, and the trap that the darkest heat-map zip can still be slow (Knox 37902 at 112 DOM).
- [niche-sequential-marketing](https://learn.datasift.ai/niche-sequential-marketing) + [bulk-sequential-marketing](https://learn.datasift.ai/bulk-sequential-marketing) — the Records-page stack: **12 folders / 73 presets**, four niche CALL/MAIL pairs plus bulk, Deep Prospecting and Reactivation. Everything is driven by two counters (`predictivecall_attempts`, `directmail_attempts`) so a dial or send from any view advances the record everywhere; **that shared counter is why bulk and niche can run at once**, and why bulk presets must use counter fields rather than a tag substitute. Entry tags are **Priority 1 / Priority 2 / FTM / Tier 2**, and a universal six-part suppression stack sits inside every preset.

**`src/niche_sequential.py`'s 12 presets are the SUPERSEDED scheme and must not be built.** They gate on hand-set tags (`sms_sent`, `called_day1`) in one folder; the challenge stack gates on the two counters across eight folders, which is what makes it self-advancing. The two designs are incompatible.

### Phase 0 complete — `src/scripts/dpd_doctor.py`

Logs in from `.env` and captures a UI baseline to `output/dpd_baseline.json` (27 active lists, 334 property tags, 79 phone tags, 38 statuses, 16 Records presets, 22 SiftMap presets, 0 sequences). `--verify` re-captures and diffs; any *removal* fails the run, because this build is additive only.

**Three account facts that contradict the rest of this file, which documents Ty's account, not this one:**
- **The `00 Niche Sequential Marketing` (12) and `01. Bulk Sequential Marketing` (9) folders DO NOT EXIST here.** This account has a single `DEFAULT` folder with 16 per-dialer presets (A. Galal, A. Hesham, Bagoury, Mariam, Mostafa, Pal — each with a "Dials" twin — plus Deep Prospecting Zips, Not Interested, VIP Referrals - Bagoury, Workspace - Deep). Folders 01-12 therefore collide with nothing.
- **Zero sequences exist.**
- **`Save New` and `Create New Folder` are real, visible controls** in the filter panel action bar (`Load | Save | Save New | Clear`). Creating presets and folders is supported UI, not a gap.

**Page mechanics, all verified live and none of them guessable:**
- **Lists and property tags are nested in FOLDERS** on `/lists` and `/tags/property` (`default`, `2026`). The table renders folder rows only; names do not exist in the DOM until each folder is expanded. **Expand before reading the pager** — collapsed, `/lists` reports "of 1"; expanded it reports the real page count. Reading the pager first silently captured 10 of 39.
- **Phone tags are a separate vocabulary on `/tags/phone`**, paginated 10/page over 8 pages. The dial tiers live here, NOT in property tags: `Dial First` and `Dial Third` are phone tags among 79 (`Pr.1`, `Rel3.2`, `Deceased.1`, `Plaintiff.2`…). Tabs are real routes (`/tags/property`, `/tags/phone`, `/tags/contact`); navigate, do not click the tab (the parent `AdminTabsList` intercepts pointer events).
- **"Filter Presets" is a COLLAPSED section at the bottom of the Records filter panel**, below a scroll boundary. It must be clicked open or the panel yields only its own furniture, which a noise filter then strips to nothing.
- **Scope panel scrapes by ELEMENT, never by x-coordinate.** The records grid runs to x=1352 and sits *behind* the overlay, so a coordinate filter scrapes property rows instead of presets. Anchoring on a section heading and climbing to its container is the only approach that survived; climb until the container holds >=3 visible leaves, not until it has >8 nodes (that lands on the heading row alone).
- **"Add new filter block" is an INPUT PLACEHOLDER, not element text** — anchoring on `textContent` never matches. Use `#RecordsFilters__Filter_Blocks__Search`.
- **Do not blanket-click chevrons to expand folders inside the panel.** An earlier version walked every svg/Chevron in the overlay and re-collapsed the section it had just opened, after which the capture read empty.

**The guard that matters:** every section is checked against a `PLAUSIBILITY_FLOOR`, not just for non-emptiness. A capture of 10 lists on an account known to hold 39 is a broken selector, not a small account — and it passed a naive non-empty check twice before the floor caught it. Related: never let a "thin capture" message overwrite the underlying status string; doing so hid a real "section stayed empty" error behind a generic warning.

### Phase 2 substantially complete — `src/scripts/dpd_siftmap_discover.py`

**THE SIFTMAP FILTER CONTRACT IS A URL, AND IT IS NOW MAPPED.** Two parameter families, both confirmed live against DC (FIPS 11001, unfiltered baseline **215,290**), written to `output/dpd_siftmap_filters.json`:

```
preset_<snake_name>=true          boolean distressor presets
extra_<field>_min|_max=<number>   numeric ranges
```

**22 parameters confirmed**, including all 20 default presets: `preset_absentee_owners` (85,998), `preset_free_clear` (53,802), `preset_high_equity` (73,826), `preset_low_equity` (25,319), `preset_negative_equity` (5,087), `preset_vacant` (3,025), `preset_pre_foreclosure` (709), `preset_zombie` (294), `preset_bank_owned_reo` (494), `preset_private_lender` (513), `preset_adjustable_loans` (2,492), `preset_quick_resale` (737), `preset_auction` (13), `preset_judgment` (7), `preset_land` (11,143), `preset_owner_occupied` (129,292), `preset_cash_buyer` (99,770). Senior is **`extra_owner_age_min=65`** (36,507).

**Discovery is done by URL probing, not by clicking.** UI clicks proved unreliable — `TopPresetListItem` intercepts pointer events and half the default list hides behind "Show more" — while the URL is also exactly how the pull will drive SiftMap, so probing it is the truer test.

**Every probe is compared against the unfiltered county count.** A parameter the server accepts and silently ignores returns the baseline, which otherwise reads as a working filter (the same trap as the Zillow API's silently-discarded `price_min`). **And a zero count never confirms a parameter**: `extra_owner_age_min=true` returns 0, which looks like a working filter over an empty county but is really a range parameter handed a boolean.

**Live confirmation of the workbooks' coverage gaps**, from DC: `preset_tax_lien` = **0**, `preset_foreclosure` = **5**, `preset_deceased` = **9**. Tax sale and foreclosure genuinely are not buyable in these markets, exactly as the doors-per-deal research said.

**10 signals had no parameter after the URL-probe pass** — the playbook's "SiftMap Pro Distressors" layer: Out-of-State, Tired Landlord, Low Income, Bad Credit, HOA Lien, Lis Pendens, Other Lien, Bankruptcy, Estate Sale, Probate. **Six of those were closed by the UI pass below**; the rest are genuinely absent from the filter surface.

### Phase 2b — the More panel is the whole filter vocabulary (2026-08-25)

The URL probe could only confirm parameters it guessed the NAME of. The panel that defines them is **"More" on the top rail** (`Price | Beds/Baths | Property Types | AI Scores | Presets | More | Save Filters`, all at y≈84). Three mechanics, none guessable, all verified live:

- **The rail runs across the TOP of the map.** `discover_panel_filters`'s `r.x <= 420` bound reads the app's left NAV instead, which is why `output/dpd_siftmap_filters.json`'s `panel_labels` are "SiftMap, Records, Tags, Sequences…". That field is junk; ignore it.
- **The More panel needs a real MOUSE click at the rail coordinates.** A JS `.click()` on the element whose `innerText === 'More'` opens a **41-label stub**; `page.mouse.click(x+w/2, y+h/2)` opens the real thing. Then the panel must be **scrolled** — its lower sections (Owner Details, Financial Details) do not exist in the DOM until you scroll its container, and scrolling takes it from 41 to **234 labels**.
- **Every styled dropdown hides a native `<select name="...">`**, and that name is the filter's real identity. Dumping `select[name],input[name]` inside the open panel yields the entire vocabulary in one shot — **36 named controls** — instead of guessing parameter names one at a time. URL form drops the dotted prefix: `ownerDetails.extra_owner_age_min` is `extra_owner_age_min=65` in the URL, which is why the earlier guess happened to work.

**Newly available parameters** (`output/dpd_siftmap_field_names.json`):

| Signal / use | Control |
|---|---|
| **Out-of-State** | `extra_absentee_in_state` [true,false] — the gap that blocked 5 counties |
| Tired Landlord (proxy) | `extra_owners_with_multiple_properties`, `extra_years_owned_min/max` |
| Investor-owned | `extra_owner_is_investor` |
| Base characteristics | `extra_building_sqft_min/max`, `extra_year_built_min/max`, `extra_units_min/max`, `extra_vacant`, `extra_flood_zone`, `extra_sqft_min/max`, `extra_acres_value_min/max` |
| Owner | `extra_owner_absentee`, `extra_cash_buyer`, `extra_multiple_owners`, `extra_is_po_box`, `extra_owner_age_min/max` |
| Financial | `extra_private_lender`, `extra_equity_percent_unknown`, `extra_last_sale_price_unknown`, `extra_last_sale_date_unknown`, `extra_is_last_sale_interfamily` |
| MLS | `mls.extra_days_on_market` [1d,7d,14d,30d,90d,180d,365d,730d,1095d] |
| Account overlap | `in_my_account_mode` [in,not_in] — suppression, and how a pull avoids re-adding what we already own |

**The More panel also holds the geography layer**: `ZIP codes`, `Cities`, `Neighborhoods`, `Municipalities`, each with **Include / Exclude**. That is both the T1/T2 geography filter and the mechanism for suppression #4 (exclude dead neighborhoods) — Phase 1's outputs feed straight into it.

**THE OUT-OF-STATE FLAG'S DIRECTION IS RESOLVED: `extra_absentee_in_state=true` MEANS OUT-OF-STATE. The label is right and the field NAME is the misnomer.** The two disagreed — label "Absentee-Out-of-State" against field `extra_absentee_in_state` — and getting it backwards pulls exactly the wrong list for five counties, so it was settled by arithmetic rather than by reading either one. Reading owner mailing addresses off the result cards had already failed (the cards render only the property address, and the `[class*="PropertyDetails"]` panel came back empty on click), so the test used a population whose mailing state is known by definition: **an owner-OCCUPIED owner lives at the property, so their mailing state IS the property state.** Live DC: `owner_occupied AND flag=true` is **1,586** of 129,292 (≈0), while `owner_occupied AND flag=false` is **120,092** (≈all). `true` therefore excludes owner-occupiers, which only out-of-state can do. Supporting counts: flag `=true` 24,626 / `=false` 148,397; with absentee, 23,040 / 28,305. Evidence in `output/dpd_siftmap_direction.json`.

**Still absent from SiftMap's entire filter surface (7): Bad Credit, HOA Lien, Low Income, Other Lien, Bankruptcy, Estate Sale, Probate.** Lis Pendens came off that list — see Phase 2d. The `distressors_method` control [stacked, custom] under the PRO "Homeowner and Property Distressors" section is a dead end: setting it by URL returns the unfiltered count, and selecting Custom Combination through the UI adds **zero** new labels and zero new controls, so the custom list is not a hidden vocabulary.

### Phase 2d — the Foreclosure Filters section, found by scrolling further (`src/scripts/dpd_distressor_hunt.py`)

The Custom Combination hunt came back negative as expected, but scrolling the More panel harder than Phase 2b did (14 passes instead of a single scroll, 236 labels -> **260**) surfaced a whole section the earlier dump never reached: **Foreclosure Filters**, holding Auction Date, Notice Date, a Foreclosure Status control [`pre_foreclosure`, `auction`, `foreclosure`] and a **Notice Type** control whose seven codes are read straight out of its markup — `CO` Court Order, `FJ` Final Judgement, **`LP` Lis Pendens**, `ND` Notice of Default, `NF` Notice of Foreclosure, `NS` Notice of Sale, `NT` Notice of Trustee Sale. This is the layer the doors-per-deal workbooks said the 7 MD judicial jurisdictions need, since they must key on Lis Pendens / Final Judgment rather than Notice of Foreclosure.

**Three routes to the parameter name failed before the fourth worked, and each failure is worth keeping.** These controls are styled `SelectMulti` components with **no native `<select>` and no `name`**, so they never appear in the 36-control dump that mapped the rest of the vocabulary. Clicking one fires **no search request at all** — not by JS `.click()` and not by a real mouse click — and the four-control Phase 2b toggle test had already shown `url_changed: false`, so neither the URL nor the network traffic reveals anything. What worked was dumping the section's `outerHTML` and reading the option `value` attributes directly, then probing candidate parameter names by URL against a county baseline the way every other parameter in this build was confirmed. Two land: **`extra_foreclosure_notice_type=<code>`** and **`extra_foreclosure_status=<code>`**; `extra_notice_type`, `extra_notice_types`, `extra_foreclosure_type`, `notice_type`, `extra_foreclosure_statuses` and `foreclosure_status` all return the unfiltered count.

**A text match on the page is not a filter, and the guard that proves it is worth copying.** A first pass reported Lis Pendens "recovered" off a plain page-text search — but the map's result cards render each property's OWN distressor badges, so `Lis Pendens` appears whether or not it can be filtered on. Position was no help either: the panel is a wide overlay (the Foreclosure section sits at x=650..1254) and its off-screen labels carry y values in the thousands, so a coordinate test classified everything as outside. The verdict now uses **DOM containment against the panel container**, and the container itself is checked with a **negative control** — if it also holds `Mapbox`, `Search while moving`, `EST. VALUE` or the `Properties` count then it is the whole page and the verdict is declared void rather than reported. A second guard rejects **prose**: `Probate` matched only inside a deed-type tooltip ("...in probate-related transfers"), so a hit longer than 40 characters is help text, not a control label. Both guards fire on this account today.

**FILTERABLE IS NOT THE SAME AS AVAILABLE, and here the distinction is the whole story.** Lis Pendens filters correctly and returns **14 records in Baltimore County out of 303,888**; Final Judgement returns **0**; Notice of Foreclosure returns **1**. The filter exists, the data does not — which is precisely what the workbooks said when they recorded that 12 of the 14 jurisdictions have no Foreclosure-notice coverage. So this find does **not** rescue the MD judicial stacks, and saying it did would be the optimistic reading this build keeps guarding against.

**Where it does pay is Fairfax, and it corrects a 10x understatement.** On the same county: `preset_pre_foreclosure=true` returns **17**, `extra_foreclosure_status=pre_foreclosure` returns **245**, and `extra_foreclosure_notice_type=ND` returns **169**. The workbook's own Fairfax Notice-of-Default list size is **93**, which the notice-type figure is consistent with once the buy box is applied and the preset is not. **The preset and the notice-type filter are not equivalent instruments**, so Notice of Default now maps to `extra_foreclosure_notice_type=ND` and the preset is no longer trusted for it.

`--merge` folds the confirmed parameters into `output/dpd_siftmap_filters.json` (the file `dpd_buildability.py` reads as the single record of what SiftMap can filter). The merge is additive and refuses to overwrite an existing working parameter, so re-running cannot quietly discard an earlier measurement.

**Closing Out-of-State plus the notice-type layer moves nine of the fourteen counties**, and buildable signals go from 11 to 13 of 21. Baltimore, Charles and Arlington get their NAMED top stack back; the counties whose named stack is still unbuildable drop from nine to **five**:

| Jurisdiction | best buildable, before | best buildable, now |
|---|---|---|
| Prince William, VA | Absentee + Free & Clear 8.8x | **Free & Clear + Out-of-State 49.2x** (list 427) |
| Fairfax, VA | Notice of Default 37.3x via preset | Notice of Default 37.3x via `ND` (list 93) |
| Stafford, VA | Absentee + Free & Clear 6.2x | **Free & Clear + Out-of-State 29.3x** (list 245) |
| Baltimore County, MD | High Equity + Vacant 16.6x | **Out-of-State + Vacant 24.5x** — the named P1 (list 54) |
| Arlington, VA | Absentee + Free & Clear 2.1x | **Free & Clear + Out-of-State + Senior 16.5x** — the named P1 (list 122) |
| Montgomery, MD | Absentee + Free & Clear 3.8x | **Free & Clear + Out-of-State 13.3x** (list 1,696) |
| Charles, MD | Absentee + Free & Clear 3.2x | **Absentee + Free & Clear + Out-of-State 8.2x** — the named P1 (list 1,920) |
| District of Columbia | Absentee + Free & Clear | **Absentee + Notice of Foreclosure 76.8x** (list 23) |

**Five counties still lose their named stack, and the two biggest numbers in the whole set are among them.** Calvert's 218x and Prince William's 196.6x both need **Bad Credit**; Montgomery's 53.6x and Anne Arundel's 12x need **HOA Lien**; Spotsylvania's 5.2x needs **Low Income**. Calvert therefore ships at 3.2x and Anne Arundel at 5.8x, and that cost is stated rather than absorbed.

**T1 sizing shifted with the stacks and the neighbourhood layer is now load-bearing for eight counties, not eleven**: over band (needs neighbourhoods) Montgomery 1,696, Anne Arundel 552, Frederick 7,853, Carroll 3,166, Calvert 5,941, Charles 1,920, Spotsylvania 1,751, Fredericksburg City 1,928; **in band** Prince William 427 and Stafford 245; **under band** Baltimore 54, DC 23, Fairfax 93, Arlington 122. The four under-band counties are the sharper problem — a 23-record T1 cannot carry a full niche cadence, so those either widen to their next buildable stack or run T2 only.

### Phase 2c — buildability: which stacks SiftMap can actually build (`src/scripts/dpd_buildability.py`)

Joins `data/dpd_signal_rankings.json` × `output/dpd_siftmap_filters.json` × `data/dpd_zips.json` and writes `data/dpd_buildability.json`. It exists because the plan quotes ONE Priority-1 stack per county while **the workbooks rank many** — DC alone has three at 76.8x. The right question is not "is the named stack buildable" but "what is the best BUILDABLE stack".

**This overturns the plan's headline blocker. Anne Arundel is NOT "fully blocked":** it has 31 ranked rows, **14 of them buildable**, best being P2 Vacant (5.8x, list 552). Every one of the 14 jurisdictions has a buildable stack. But the cost of the missing parameters is far worse than the single-row table suggested — 9 of 14 counties lose their named stack:

| Jurisdiction | Named top stack | Best BUILDABLE | Cost |
|---|---|---|---|
| Calvert, MD | Absentee + Bad Credit + Free & Clear 218x | Absentee 3.2x | **68x** |
| Prince William, VA | Bad Credit + Out-of-State 196.6x | Absentee + Free & Clear 8.8x | **22x** |
| Montgomery, MD | Free & Clear + HOA Lien 53.6x | Absentee + Free & Clear 3.8x | **14x** |
| Stafford, VA | Free & Clear + Out-of-State 29.3x | Absentee + Free & Clear 6.2x | 4.7x |
| Arlington, VA | Free & Clear + Out-of-State + Senior 16.5x | Absentee + Free & Clear (P2) 2.1x | 7.9x |
| Charles, MD | Absentee + Free & Clear + Out-of-State 8.2x | Absentee + Free & Clear 3.2x | 2.6x |
| Baltimore County, MD | Out-of-State + Vacant 24.5x | High Equity + Vacant 16.6x | 1.5x |
| Anne Arundel, MD | HOA Lien 12x | Vacant (P2) 5.8x | 2.1x |
| Spotsylvania, VA | Low Income 5.2x | Absentee + Free & Clear + Senior 3.7x | 1.4x |

**Fairfax is an upgrade on the plan, not a loss.** The plan named Free & Clear + Tax Delinquent + Senior (10.3x, 340); the workbook's actual top P1 is **Notice of Default (37.3x, list 93)**, and `preset_pre_foreclosure` is confirmed working. Closing Out-of-State alone recovers Baltimore, Stafford, Arlington and Charles; Bad Credit recovers the two biggest numbers in the whole set (Calvert 218x, Prince William 196.6x).

**T1 sizing against the playbook's 200-500 band, using the best BUILDABLE stack** — **eleven** counties come back OVER band and need the neighbourhood layer (Montgomery 10,613, Charles 9,544, Frederick 7,853, Calvert 5,941, Prince William 4,397, Stafford 3,582, Arlington 3,386, Carroll 3,166, Fredericksburg City 1,928, Spotsylvania 1,751, Anne Arundel 552), and three come back UNDER it (DC 23, Fairfax 93, Baltimore 160). **Not one lands in band on the distressor layer alone** — far from the plan's expectation that eight would, because the fallback stacks are broader than the named ones. The neighbourhood layer is now load-bearing for eleven counties rather than four.

### Phase 1 complete — Market Finder across all 14 (`src/scripts/dpd_market_research.py`)

`--extract` logs in ONCE and walks all 14 (resumable, one JSON per county in `output/dpd_market_finder/`); `--score` turns those into the three geography artifacts. It is a new driver rather than a loop over `extract_market_finder.py`'s CLI because that CLI logs in per county and `sys.exit(1)`s on the first failure, so one bad county costs the other thirteen.

**THE COUNTY PICKER HAD TO BE REWRITTEN OR IT WOULD HAVE PULLED THE WRONG JURISDICTION.** `extract_market_finder._select_county` matches with `:has-text()` and takes the first hit, and the live dropdown holds **both `Baltimore` and `Baltimore City`**, and **both `Fairfax` and `Fairfax City`** — jurisdictions our defaults deliberately exclude. Two more mechanics fall out of that:

- **Options render as two lines, `"Baltimore\nMD"`**, so an exact match must compare the FIRST LINE only.
- **The option scrape needs an `x > 200` bound** or it returns the left nav ("SiftLine, SiftMap, Market Finder, Records, Tags…") as the county list — the same nav-vs-rail confusion as the SiftMap panel above.
- Verified correct: Baltimore County returned 21222/21234/21208 (Dundalk, Parkville, Pikesville), Fairfax returned 97 ZIPs including McLean/Vienna/Reston.

**DC HAS NO SUB-COUNTY DATA IN MARKET FINDER.** Verified three ways: with both state and county chips applied the grid returns only the rollup row `District of Columbia, DC`, pagination reads `1-1 of 1`, and the **ZIP input is `disabled`** for DC while enabled for Montgomery. So DC contributes no T1 neighbourhood layer and no dead-neighbourhood suppression. Its T1 already sizes at 23 without geography, so this is tolerable — but a naive run records that rollup row as one ZIP and calls it success, which is why `_grid_granularity()` refuses any first cell reading `"<County>,"` and records `granularity: county_rollup` instead.

**Fredericksburg City IS covered by Market Finder** (ZIP 22401, 7 neighbourhoods, 26 investor transactions) even though `publicnoticevirginia.com` has no checkbox for it at all. Only its FTM notice feed is missing, not its geography.

Live results, 13 of 14 with sub-county data — `zips` and `nbrs` read *returned : real markets : passed all four checks*:

| Jurisdiction | zips | nbrs | dead nbrs | selected ZIPs |
|---|---|---|---|---|
| Baltimore County, MD | 80:43:2 | 219:206:18 | 44 | 21228, 21093, 21152, 21286, 21236 |
| Montgomery, MD | 89:41:2 | 232:214:20 | 93 | 20902, 20853, 20904, 20895, 20851 |
| Anne Arundel, MD | 59:35:4 | 129:119:9 | 26 | 21113, 21012, 21114, 21061, 21146 |
| Frederick, MD | 43:26:3 | 65:63:8 | 24 | 21769, 21793, 21710, 21774, 21788 |
| Carroll, MD | 16:15:2 | 37:37:3 | 11 | 21155, 21158, 21074, 21771, 21102 |
| Calvert, MD | 15:11:0 | 18:18:0 | 3 | 20678, 20685, 20657, 20732, 20714 |
| Charles, MD | 28:21:1 | 34:32:1 | 5 | 20601, 20662, 20664, 20677, 20675 |
| District of Columbia | — | — | — | no sub-county data |
| Fairfax, VA | 97:44:7 | 274:260:35 | 96 | 22042, 22015, 20120, 22031, 22030 |
| Prince William, VA | 25:15:1 | 93:88:2 | 26 | 20136, 22192, 20112, 20111, 22025 |
| Arlington, VA | 45:9:1 | 71:59:7 | 40 | 22203, 22201, 22206, 22204, 22202 |
| Stafford, VA | 16:4:1 | 32:31:4 | 4 | 22556, 22405, 22406 |
| Spotsylvania, VA | 17:10:1 | 34:33:2 | 7 | 22553, 22960, 22407, 22551, 23117 |
| Fredericksburg City, VA | 1:1:0 | 7:6:0 | 2 | 22401 |

**THE PLAYBOOK'S FOUR METRICS CANNOT BE ANDed INTO A GATE — that returns nothing.** The first scoring pass required all four (supply under 3 months, DOM at or below the county median, sale price at or below home value, home value inside the middle-60% band) and produced 2-4 eligible ZIPs from 80, with **Calvert at zero**. Two measured causes:

- **Roughly half of every county's ZIP rows are not markets at all.** Only 43 of Baltimore's 80 sold anything last month, so months-of-supply is undefined and they fail the supply test for a reason unrelated to market quality. They are now dropped up front by `_is_real_market()` and counted separately rather than left in the denominator.
- **"Under 3 months of supply" is Knoxville-calibrated.** This metro's median across the 6 MD counties is **3.15 months** (p25 2.40, p75 4.34), so the guide's rule sits at the median here instead of isolating a tight market. ANDed with three more ~50% filters, empty is arithmetic, not a finding. Same lesson already recorded for the guide's "15 dead neighborhoods" being Knox+Blount's number.

So the four metrics now decide the **ORDER**: rank by how many checks a row passes, then by investor volume, with the top-decile-volume rows set aside as contested ("balanced investor volume, not maximum"). Every county yields a ranked 5 and the strict all-four count is still reported — Calvert's honest answer is 0 strict, best 3 of 4.

Outputs: `data/dpd_zips.json` (T2 geography, plus the 60% price band and every row's per-check detail), `data/dpd_neighborhoods.json` (T1 geography), `data/dpd_dead_neighborhoods.json` (suppression #4; **381 dead neighbourhoods across the 13**, nothing like the guide's 15).

### Phase 2e — the geography layer's URL contract, and the two controls that have none

The More panel's `ZIP codes | Cities | Neighborhoods | Municipalities` block is both the T1/T2 geography filter and the mechanism for suppression #4, and none of it was mapped. Unlike the Foreclosure Filters section, its markup gives nothing away: these are free-text token inputs with **no `name`, no `id` and no option values**, only placeholders ("Enter ZIP codes"). So the names had to be probed, and the probe is what found them:

```
zip_codes | cities | neighborhoods | municipalities  = <JSON array, URL-encoded>
<field>_mode = include (default) | exclude
```

**The value MUST be a JSON array, and the wrong format is what makes this hard to find.** `zip_codes=21228` returns **0** while `zip_codes=["21228"]` returns **17,062**. Every `extra_`-prefixed guess (`extra_zip_codes`, `extra_zips`, `extra_zip`, `extra_postal_code`, `include_zip_codes`, `extra_neighborhoods`…) returns the unfiltered count instead. So the whole geography layer sat behind two simultaneous unknowns, and the 0 from the bare value is the tell: **an ignored parameter returns the baseline, a recognised one handed an unparseable value returns 0.** Only `zip_codes` and `neighborhoods` returned 0 in the first sweep, which is precisely why they were the two worth pursuing.

**Exclude is a separate `_mode` parameter, not a differently-named field.** `exclude_neighborhoods`, `neighborhoods_exclude` and `excluded_neighborhoods` are all ignored; `neighborhoods=["Arbutus"]&neighborhoods_mode=exclude` works. **Verified by arithmetic on Baltimore County (baseline 303,888) rather than by the parameter merely returning a number** — include and exclude sum to the baseline EXACTLY on every field tested: ZIP 17,062 + 286,826; two neighbourhoods 2,765 + 301,123; cities exclude 287,311 against an include of 16,577. Multi-value works (`zip_codes=["21228","21093"]` -> 32,699). The same check clears the account-overlap suppression: `in_my_account_mode` not_in 222,393 + in 2,750 = 225,143, Anne Arundel's exact baseline.

**One structural consequence worth knowing before building presets: a geography field takes ONE direction at a time.** A T1 that INCLUDES its top neighbourhoods therefore cannot also EXCLUDE the dead ones on the same field. That is fine in practice — the top-N list is drawn from the Phase 1 ranking and no dead neighbourhood is in it — so dead-neighbourhood suppression rides on the ZIP-based T2 instead, where the neighbourhoods field is free.

### Phase 2f — the two rail controls, and why guessing their VALUES could never have worked

`Property Types` (the single-family buy box) and `AI Scores` live only on the top rail, are absent from the More panel's 260 labels and 36 named controls, and survived every earlier probe. Both are now resolved by `src/scripts/dpd_rail_controls.py`.

**THE BUY BOX IS `type_single_family=true`. Property Types is ONE BOOLEAN PER TYPE, not a list.** The full set, read out of the popover markup: `type_single_family`, `type_condo`, `type_townhouse`, `type_multi_family`, `type_multi_family_commercial`, `type_land`, `type_mobile_home`, `type_trailer_rv_parks`, `type_warehouse`. **Verified against an independent source**: on Baltimore County `type_single_family=true` returns **177,078**, which is exactly the SFR Supply figure the doors-per-deal workbook publishes for that county. `type_condo=true` returns 21,998 and adding townhouses gives 246,747.

**Every `property_types=[...]` guess failed on the NAME, so no amount of tuning the value would have helped** — `["SFR"]`, `["single_family"]`, `["Single Family Residential"]`, `["SINGLE_FAMILY"]` and the exact rendered label `["Single Family Res."]` all returned the unfiltered count, because **an unrecognised parameter is ignored regardless of its value**. That is the diagnostic that matters here and it is worth stating as a rule: **baseline means the name is wrong; 0 means the name is right and the value is wrong.** Only `extra_land_use` ever returned 0, and it was a red herring.

**The technique that finally worked was a DOM diff with a marker, not geometry.** Two earlier attempts at the popover grabbed the left nav and the results list — a "largest floating container" heuristic and a body-child snapshot both missed, and the map's own result cards render "Single Family Residential" so any text search matches whether or not a filter exists. The fix is to **mark every pre-existing match before opening the popover**, then keep only elements that appear afterwards; those are provably new. Same containment discipline that caught the fake Lis Pendens hit.

**AI SCORES ARE FILTERABLE BUT EMPTY ON THIS ACCOUNT — the lever does not exist here.** The names are `investor_off_market_score_min` / `_max`, `investor_on_market_score_min` / `_max` and `realtor_score_min` / `_max` (the popover renders them dotted, `investor_off_market_score.min`, but the URL takes the underscored form — the dotted form is ignored). All three are **recognised**: they return 0 rather than the baseline. But `investor_off_market_score_min=0` also returns **0**, which it could not if any property carried a score. AI scores are a paid per-property add-on this account has not bought — the property cards say "Get AI Data" and the balance reads $0.00. So the playbook's most efficient single lever (90+ runs ~22.8 doors/deal against a ~218 baseline) is unavailable, and that is a **billing** decision rather than a technical gap. Same shape as Lis Pendens: filterable, not available.

**DC's geography layer exists in SiftMap even though Market Finder will not publish it** (`src/scripts/dpd_dc_geography.py`). Market Finder returns a single rollup row for DC with the ZIP input disabled, which left Phase 1 with no DC ZIPs and no DC neighbourhoods and a "T2" of 213,296 that was really a volume tier. But Market Finder is only the RANKING source: SiftMap's own `zip_codes` and `neighborhoods` filters work on DC like anywhere else, so the layer was built by MEASURING instead of by reading. **All 22 residential DC ZIPs filter** — 20002 (23,698), 20011 (19,871), 20009 (17,873), 20001 (16,462), 20019 (16,158) lead — and **14 of 30 probed neighbourhood names are recognised** (Takoma 1,523, Petworth 1,510, Lamond Riggs 1,398, Kingman Park 1,298, Dupont Circle 1,290…). Sixteen are not, including Capitol Hill, Columbia Heights, Georgetown and Shaw, so **SiftMap's DC neighbourhood vocabulary is not the one locals use** and names have to be probed rather than assumed. The caveat that rides with this: DC's ZIPs are ranked by **volume alone**, since none of the four market checks (supply, DOM, price band, competition) have data for it — unlike the other 13 counties.

**SiftMap HAS the data for signals it will not let you filter on.** The property cards carry distressor badges reading `Tired Landlord`, `HOA Liens`, `Bankruptcy Properties` and `Senior Homeowners` — exactly the signals recorded above as absent from the filter surface. So Calvert's Bad Credit and Anne Arundel's HOA Lien are a *filtering* gap rather than a *data* gap, which leaves post-pull filtering on the badge as a possible route. That route is unbuilt and unproven; it is recorded here as an option, not a plan.

### Phase 3a — the registries

- **`src/dpd/jurisdictions.py`** — one canonical record per jurisdiction unifying the **five incompatible county-id schemes** and the four different NAMES the pipelines use for the same place (`Baltimore` / `Baltimore County` / `Baltimore, MD` / `Washington DC`). Calvert is the sharpest reason not to assume they align: **4 on Register of Wills but 05 on SDAT**. `resolve()` is exact per field and returns **`None` for "Baltimore City"** rather than silently folding it into Baltimore County. Every absence is annotated as a finding (DC absent from the MD-only sources, Fredericksburg City absent from the VA notices site). `python src/dpd/jurisdictions.py` prints the coverage matrix.
- **`src/scripts/dpd_workbook_extract.py`** — reads the three `county-compare-*.xlsx` into `data/dpd_signal_rankings.json` (every ranked stack per county, **21 distinct atomic signals**), `data/ftm_sources_mddcva.csv` (**exactly 139 source rows across 14 jurisdictions**, matching the plan's count) and `data/dpd_siftmap_coverage_gaps.json`. **The First to Market sheet interleaves narrative rows into the County column** — a per-county "Data gaps to know about…" paragraph plus a how-to-read preamble — and treating those as source rows inflated the jurisdiction count from 14 to 29. They are now parsed instead of dropped, which turns the workbooks' own coverage statement into machine-readable form. Reading the extracted artifact back against the workbooks **corrected the headline above**: all 14 lack Tax sale list and none of the 14 have Eviction, Code violation or Divorce, but Foreclosure notices is named by **8 of 14, not 12**, and **four** counties list Tax sale as their only gap rather than two. The gap paragraphs are the ground truth and they were never wrong; the prose summary drifted from them.

### Phase 3b — the 28 tier configs, sized against LIVE counts (`src/scripts/dpd_tier_configs.py`)

`data/dpd_tier_configs.json` holds one T1 and one T2 per jurisdiction as a replayable SiftMap URL. `--measure` is not a refinement, it is the point: it opens every URL, records the count, and re-decides the tier from that number.

**THE WORKBOOK LIST SIZES ARE NOT THE COUNTS THE PULL RETURNS, and sizing a cadence off them puts counties in the wrong tier.** The workbook figures come from DataSift's doors-per-deal tool, the counts come from the SiftMap filter that actually runs the pull, and they diverge by 2x or worse in BOTH directions on six of fourteen: Prince William 427 -> **2,319** (5.4x), Stafford 245 -> 997, Montgomery 1,696 -> 6,551, Arlington 122 -> 461, DC 23 -> 63, Calvert 5,941 -> 14,697. Phase 2c's whole "eleven counties over band, three under" table was built on the workbook numbers and does not survive measurement.

**T1 is the highest-lift buildable stack, not whichever stack happens to land in the band.** The 200-500 band is a cadence-capacity rule, not a quality rule: a 107-record list at 24.5x is better economics per door than a 476-record list at 8.4x, so trading the lift away to fill the band optimises the wrong number. Sizing is handled as a stated ACTION on the side instead.

**The neighbourhood cut is depth-searched, because a fixed top-5 was wrong in both directions.** The count rises monotonically with the number of included neighbourhoods, so the builder binary-searches the ranked pool for the depth that lands in band. At a fixed 5 the results were nonsense: Anne Arundel read 796 on the stack and **16** on the cut, Montgomery 6,551 and 142, with the band sitting in the gap — while Frederick, Carroll and Charles needed FEWER than five. Measured depths now run from **2** (Frederick, 495) to **43** (Anne Arundel, 225). This needed Phase 1 to persist the full ranked pool, not just its top 5: `_score_table` now returns `ranked` and `data/dpd_neighborhoods.json` carries it (16 to 237 rows per county). Re-scoring is offline and free, since the Market Finder pulls are already cached.

**A failed probe was being reported as a finding, and monotonicity is what caught it.** Charles measured 685 at depth 5 and then **0** at depth 29, which the search wrote up as "geography cannot fill this band" — a conclusion drawn from a page that had not rendered its count. Including more neighbourhoods can never return fewer, so every probe is now checked against that invariant, retried once when it violates it, and recorded as `None` with the rejected values rather than believed. DC came back unmeasured on one pass and 63 on the next from an identical URL, so the base measurement retries too. With the guard in place Charles reads 1,668 at depth 29 and lands **in band at depth 3 (363)**.

**THE BUY BOX CHANGES EVERY NUMBER, so all counts below are single-family only** (`type_single_family=true`, Phase 2f). It cuts roughly 40% — Baltimore County 303,888 to 177,078 — and it moves tiers: Calvert's stack fell 14,697 to 5,893, Charles 2,368 to 1,897, and **DC's top stack fell to 0**, because Absentee + Notice of Foreclosure on single-family houses does not exist in a city of rowhouses and condos. Any figure recorded before the buy box was applied is obsolete.

**Eight of fourteen T1s landed in band directly**, and `dpd_widen_search.py` then found a shippable stack for **all six** that did not. Every T1 is now sized in band:

| Jurisdiction | T1 stack | lift | cut | count |
|---|---|---|---|---|
| Frederick, MD | Absentee + Free & Clear | 4.6x | top 2 nbrs | 472 |
| Prince William, VA | Free & Clear + Out-of-State | 49.2x | stack only | 426 |
| Carroll, MD | Absentee + Free & Clear | 4.6x | top 4 nbrs | 391 |
| District of Columbia | High Equity + Lis Pendens | 7.9x | stack only | 370 |
| Charles, MD | Absentee + Free & Clear + Out-of-State | 8.2x | top 3 nbrs | 332 |
| Fredericksburg City, VA | Out-of-State | *unranked* | stack only | 274 |
| Stafford, VA | Free & Clear + Out-of-State | 29.3x | stack only | 240 |
| Arlington, VA | Out-of-State + Senior | 9.8x | stack only | 232 |
| Baltimore County, MD | Vacant | 11.1x | top 48 nbrs | 206 |
| Spotsylvania, VA | Absentee + Free & Clear + Senior | 3.7x | top 7 nbrs | 205 |
| Anne Arundel, MD | Vacant | 5.8x | top 58 nbrs | 203 |
| Calvert, MD | Out-of-State | 3.5x | top 3 nbrs | 202 |
| Fairfax, VA | Free & Clear + Out-of-State | 8.6x | top 44 nbrs | 202 |
| Montgomery, MD | Free & Clear + Out-of-State | 13.3x | top 22 nbrs | 200 |

**Six counties trade lift for a workable list, and the trade should be seen rather than buried.** Fairfax drops 37.3x -> 8.6x, Arlington 16.5x -> 9.8x, Baltimore 24.5x -> 11.1x, DC 76.8x -> 7.9x. In each case the high-lift stack survives in `data/dpd_widen_search.json` under `tried`, with its measured count, so it can be run as a small high-priority slice alongside the wider T1 rather than being thrown away. Baltimore's own numbers make the case: its 47.5x and 38.7x stacks measure **1 record each**.

**Fredericksburg City's pick carries no lift at all**, and that is a real caveat rather than a rounding artifact. Out-of-State is not among the four stacks the workbook ranks for Fredericksburg, so it was taken from the buildable set with an unknown lift. It is a defensible choice — Out-of-State is Priority 1 in four of the six VA jurisdictions and worth up to 21x in Prince William — but it is unevidenced *for this county*.

**DC's rescue came from Lis Pendens, the signal Phase 2d found and then discounted.** LP holds 14 records in Baltimore County, so it looked worthless; in DC, High Equity + Lis Pendens measures **370**. A signal being empty in one county says nothing about another, and treating the Baltimore reading as a verdict on the signal would have cost DC its only shippable T1.

**T2 is geography only** — top 5 ZIPs, no distressor stack, because the bulk and mail tier trades lift for reach. With the buy box applied it now sits far closer to the playbook's 20-25K target: in range on Montgomery 23,530, Stafford 25,452, Prince William 26,422; high on Anne Arundel 29,628, Spotsylvania 29,414, Baltimore 28,814; low on Fredericksburg 6,924, Arlington 7,453, Charles 9,919. **DC's T2 is now real** at 15,195, where before the ZIP layer and buy box it was 213,296 — a volume tier wearing a T2 label.

**Structural constraint that shapes the presets: a geography field takes ONE direction at a time.** A T1 that INCLUDES its top neighbourhoods cannot also EXCLUDE the dead ones on the same field, so dead-neighbourhood suppression (#4) rides on the ZIP-based T2 where the field is free. **Verified in the T2 URLs: every T2 but DC's carries `neighborhoods=[dead...]&neighborhoods_mode=exclude`, and its measured count was taken with the exclusion applied.** DC is the correct exception — Market Finder publishes no sub-county data for it, so it has no dead list.

**CORRECTED 2026-08-26 — this section previously claimed "no dead neighbourhood appears in a T1 include list anyway, since both come from the same Phase 1 ranking." That is FALSE, and checking it found a live defect in the T1 geography.** It holds at the top 5, but the depth search runs far deeper — 58 neighbourhoods in Anne Arundel, 48 in Baltimore, 44 in Fairfax, 22 in Montgomery — and that is precisely the ranked tail where a neighbourhood with one investor deal or fewer sits. **Five counties included dead neighbourhoods in their T1** (Fairfax 9, Anne Arundel 6, Baltimore 3, Montgomery 3, Carroll 2). Separately, **the four stack-only T1s leave a free exclusion unused**: with no include list the neighbourhoods field is available, and Prince William (26 dead), Arlington (40), Stafford (4) and Fredericksburg City (2) carry no exclusion at all. RESOLVED 2026-08-26 by Phase 3d below (`dpd_geo_fix.py`): dead dropped from the ranked pool before the depth search, the exclusion added to the stack-only four, everything re-measured live. Arlington was the one rejection. Nothing here is outstanding.

**`dpd_widen_search.py` MERGES its output rather than replacing it.** A `--county` run covers a subset, and writing the whole file from one would silently discard every county the earlier full pass had resolved — which is exactly what the first two-county re-run was about to do to four completed answers.

### Phase 3c — folded into one authoritative artifact (`src/scripts/dpd_fold_widen.py`, 2026-08-26)

The six widened T1s lived only in `data/dpd_widen_search.json` while `data/dpd_tier_configs.json` — the file Phase 4 reads — still named the superseded stack. `dpd_fold_widen.py --commit` folds them in: `T1.chosen` becomes `widened`, the replaced stack is kept under `T1.superseded` with its own measured count, and every under-band candidate the search rejected is kept under `T1.high_lift_slices`. **All 14 T1s now read in band from the one file.**

**Do not fold by re-running `dpd_tier_configs.py`.** Its `main()` calls `build()` from scratch and only writes measured counts when `--measure` is passed, so a plain run silently discards every live count in the file. The fold edits the artifact in place instead.

**Timestamps cannot tell you whether two artifacts describe the same measurements, and the first version of this guard was wrong because it assumed they could.** The widen file's `merged_with` is the PREVIOUS widen run's stamp, not the tier build it measured against, and the tier config's `generated_at` is stamped at build time while the file is written after a `--measure` pass that runs for many minutes — so the two never match even when both are correct. The check is arithmetic instead: the widen search re-measured each county's named stack on its way down the lift ranking, so where a stack appears in both files their stack-only counts must agree. Five of six agree exactly (Calvert has no overlap and is reported as such rather than assumed).

**An idempotence test caught a real ambiguity, not just a re-run glitch.** After a fold, `variants.stack_only` still holds the SUPERSEDED stack's measurement while `T1.stack` names the new one — so anything reading those two together mis-attributes the count. Every pre-existing variant is now stamped with `measures_stack`, and the cross-check reads that rather than `T1.stack`. Re-running now produces a byte-identical file apart from the fold timestamp.

**Only UNDER-band rejects are kept as slices.** A candidate rejected for being over band is not a high-conviction cut, it is a broader stack the chosen one already beats. Baltimore's `Senior + Vacant` is flagged specially: **182 records at 16.5x against a chosen T1 of 206 at 11.1x** — 18 short of the floor for 50% more lift, and the band is a cadence-capacity rule rather than a quality one.


### Phase 3d — the dead neighbourhoods are out of the T1 geography (`src/scripts/dpd_geo_fix.py`, 2026-08-26)

The claim in the Phase 3b notes that "no dead neighbourhood appears in a T1 include list anyway, since both come from the same Phase 1 ranking" was FALSE, and checking it is what found the defect. It holds at the top 5. It does not hold at the depths the search actually reached, because that is precisely the ranked tail where a neighbourhood with one investor deal or fewer sits. **Five counties, not six** (the earlier prose said six and then listed five): Fairfax 9, Anne Arundel 6, Baltimore 3, Montgomery 3, Carroll 2.

`dpd_geo_fix.py` edits `data/dpd_tier_configs.json` IN PLACE, preserving the fold and every live count -- rebuilding with `dpd_tier_configs.py` would have discarded both. It classifies each county from the artifact alone (dry run, no browser), then re-measures only what changes. **8 of 9 applied, and every one of the 14 T1s is still in band:**

| County | before -> after | fix |
|---|---|---|
| Baltimore County | 206 -> 200 (depth 45) | dead dropped from the pool, re-depth-searched over 151 clean |
| Montgomery | 200 -> 203 (depth 21) | over 122 clean |
| Anne Arundel | 203 -> 208 (depth 58) | over 88 clean |
| Carroll | 391 -> 356 (depth 2) | over 22 clean |
| Fairfax | 202 -> 208 (depth 39) | over 154 clean |
| Prince William | 426 -> 358 | free exclusion, 26 dead |
| Stafford | 240 -> 233 | free exclusion, 4 dead |
| Fredericksburg City | 274 -> 274 | free exclusion, 2 dead (they hold no single-family records) |

**Arlington is REJECTED, and that is a finding rather than a failure.** Excluding its 40 dead neighbourhoods drops the T1 from 232 to 149, under the 200 floor. The dead geography is holding 83 of its records, so the honest fix is a broader stack, not a smaller list. The band is a cadence-capacity rule and suppression #4 is a quality nicety; spending a shippable list on it would be the wrong trade. Left unchanged, recorded under `geo_fixes.rejected` with both counts.

Two guards carried over and both earned their place: the depth search's monotonicity check (including more neighbourhoods can never return fewer), and a plausibility check on the exclusion (excluding geography can only remove records, so a count ABOVE the unfiltered one means the filter did not apply, not that the county grew). Each applied variant keeps its pre-fix state under `_pre_geo_fix`, so the change is reversible.

### Phase 6 complete — the FTM coverage matrix (`src/scripts/dpd_ftm_registry.py`, 2026-08-26)

Joins the 139 researched county offices, the workbooks' own SiftMap gap paragraphs, and **the scrapers' own live module constants** into one verdict per (jurisdiction x data type) cell. Writes `data/dpd_ftm_registry.json` and, with `--csv`, `data/dpd_ftm_coverage.csv`. Read-only: no browser, no account, no network. Coverage is read from `DEFAULT_COUNTIES` / `COUNTY_CHECKBOX_INDEX` / `COUNTY_ID` rather than from prose, because a county list in a docstring drifts from the dict the code iterates and a scraper pointed at the wrong county scrapes nothing while looking like a quiet day.

**126 cells: 20 covered by a scraper we run, 11 by the provider, 10 reachable with existing code never pointed here, 7 fed externally, 71 researched but needing a new scraper, 7 unsourced.**

**Silence is not coverage — the first run invented provider coverage in 28 cells.** The gap paragraphs only ever name seven list types, so condemnation and the recorder lien/deed index are never judged either way; defaulting an unjudged type to "covered" marked condemnation as provider-covered in all 14 jurisdictions. Unjudged now reads `not_assessed` and routes to its researched county office. The assessed set is derived from the gap paragraphs themselves, so a workbook that starts judging a new type is picked up rather than treated as unjudged forever.

**The cheapest coverage in the whole matrix is already built and has never been run.** `va_trustee_sale_pull.py --popular-search 6` (Estate Claims) and `--popular-search 8` (Tax Deeds) cover probate and tax sale across all five VA counties — existing code, new runs only, and neither type exists in SiftMap for these markets. After that, ordered by the workbooks' own access rating: recorder lien/deed index (14 jurisdictions, 5 Easy), tax sale (9, 6 Easy), tax delinquency roll (6, 4 Easy), then code violations, condemnations and evictions — each researched in all 14 with **zero** rated Easy anywhere.

**Divorce is fed from outside this repo for MD only.** The MD Cases scheduled task covers all 7 MD counties statewide; DC and the 6 VA jurisdictions are genuinely unsourced. Reporting those 7 MD cells as "unsourced" would have been wrong, so external feeds are a verdict of their own — verify it is alive rather than building anything.

**The workbooks use two access vocabularies** (MD says Easy/Medium/Hard, VA also uses Moderate/High). Left as-is they sort as five levels and any ordering by difficulty is silently wrong; they are folded onto three.

**Credentials are checked for presence, never read.** One blocker: `CAPTCHA_API_KEY` is still the literal placeholder, which is the worst of the three failure states — it is present, so a naive `if os.getenv(...)` passes, and it fails only at the point of use. It blocks `va_trustee_sale_pull --full-text`, which is where the auction date and loan principal live (the grid snippet truncates before them). `FIRECRAWL_API_KEY`, `SCRAPFLY_KEY`, `MD_LANDREC_*` and the DataSift login are all set.

### Phase 6b — how each notice type is actually PUBLISHED, 14 x 6, live-probed (2026-08-28)

Basem asked for the publication channel, URL, scrapability and cadence of foreclosure / probate / tax sale / tax delinquency / eviction / code violation notices in all 14 jurisdictions, one research agent per type in parallel. **~85% of it was already in the repo and the missing column had been dropped by our own extractor:** `dpd_workbook_extract.py`'s `FTM_HEADER` listed 9 of the First to Market sheet's 12 columns, so `Updates` (cadence), `Verified?` (81 Verified / 58 "Verify locally") and `Notes` never reached `data/ftm_sources_mddcva.csv`. Fixed; the CSV is 15 columns, `dpd_ftm_registry.py` carries `cadence` / `verified` into every matrix cell (111 of 126) and `dpd_ftm_coverage.csv`, and the credential check now covers `MDDC_EMAIL` / `MDDC_PASSWORD`. **Cadence lives in the workbook; scrapability is the live probe** — two different questions, and only the second needed fresh work.

Artifacts: `data/dpd_notice_publication_<type>.json` (six, one per agent, fixed enum schema), merged by `src/scripts/dpd_notice_publication_report.py` into `data/dpd_notice_publication.json` (merge-not-replace, so re-running one type never wipes five) and `output/dpd_notice_publication.md` (the 14 x 6 summary table + per-type detail, "what changed vs the workbook", the unreachable list). 84 cells: 74 live-probed, 10 unreachable (kept at workbook value, marked with a double dagger), 0 schema problems. Rules the agents ran under: read-only, no accounts, no forms, no CAPTCHA, no Firecrawl; WebFetch first, `python src/scrapfly_browser.py` on a blocked page; a blank beats a confidently wrong value.

**What the probes changed:**
- **Eviction: Maryland has a bulk daily open feed the workbooks said does not exist, and this machine already downloads it.** `mdcourts.gov/data/case/fileYYYY-MM-DD.pdf` (the MD Cases task's file) lists every District Court landlord-tenant filing statewide — 2,037 Failure-to-Pay-Rent entries in one day, landlord printed first — so a multiple-eviction-landlord list is a GROUP BY over PDFs already in `Divorce Cases/`. No property address in it (per-case Case Search lookup). MD Case Search itself relaunched 2026-03-14 with CAPTCHA + sign-in and shields non-removal FTPR cases after 60 days (SB 19), so capture at filing. VA unlawful detainers: the per-court GDC portal is reCAPTCHA-gated, single-court, single-hearing-date; the statewide OCIS is criminal/traffic only for GDC; `virginiacourtdata.org` strips names.
- **Foreclosure: the MD Circuit Court docket channel is CAPTCHA-gated and unreachable from every host**, so the mddcpublicnotices ad feed we already scrape is the practical MD first signal; Anne Arundel's next-day civil docket PDF is the one open MD court target. **Every VA recorder channel is paywalled** (Fairfax CPAN $150/user/quarter, PWC LRMS $240/yr, Stafford/Spotsylvania subscription); Arlington and DC give a free index after free registration (Kofile), so publicnoticevirginia.com stays the only free VA signal. **Fredericksburg City's ads run on the Free Lance-Star's Column.us**, not publicnoticevirginia; fredericksburg.com redirects bots to a Tollbit token gateway.
- **Probate: `registers.maryland.gov` refuses non-residential fetchers (ECONNREFUSED to WebFetch and curl) but renders through a residential proxy** — keep the RoW pull on office/residential egress. Legal Notice Search defaults to the last 30 days statewide with "Published on MM/DD/YYYY" per row. VA has no free online probate index in 4 of 6 (Fairfax = paid CPAN, Stafford = paid SRA $300/6mo, Spotsylvania and Fredericksburg in person); the two open ones the workbook missed are **Arlington's STARR index** (guest search) and **Prince William's Eagle Recorder fiduciary index** (free registration).
- **Tax sale: one `taxva.com` (TACS) scraper covers Fairfax, Prince William, Arlington and Fredericksburg City** — plain HTML parcel lists, no login; Stafford and Spotsylvania go through Sands Anderson on `forsaleatauction.biz`. The county pages are pointers, not sources (PWC says it keeps no list). MD lists are PDFs of uneven use: Anne Arundel's has no address column (ACCOUNT# to SDAT join), Carroll's is a scanned image (OCR), Frederick's "2026 Property Listing" DocumentCenter id was reused for the excess-proceeds file, Montgomery has no list on its site at all. DC's 2026 sale began Aug 19, not July.
- **Tax delinquency: only DC (ITSPE daily open data) and Stafford (weekly list under Va. Code 58.1-3924) publish a real pre-sale roll.** Everything else is the annual advertisement alive for a few weeks, a per-parcel lookup, or FOIA; Spotsylvania's "Easy" rating does not hold (publishes nothing).
- **Code violation: the workbook undersold it.** Baltimore County, Anne Arundel, Fairfax and Arlington have anonymous, date-range-capable Accela enforcement searches; Montgomery has two Socrata mirrors (DHCA housing violations `k9nj-z35d` weekly, OCA citations `qdey-wt67` monthly). 10 of 14 workbook URLs were dead or WAF-blocked; `frederickmd.gov` is now a Bitly short domain (real site `cityoffrederickmd.gov`); Carroll's only public database is BZA variances, not enforcement.

**Fetcher hostility is the limiting factor, not the data.** `baltimorecountymd.gov`, `charlescountymd.gov`, `arlingtonva.us`, `staffordcountyva.gov`, `pwcva.gov`, `fairfaxva.gov`, `manassasva.gov` and the mdcourts hosts 403 plain fetches; Scrapfly cleared most on first try, then **throttled the account (429, 25 or more failed statuses per minute)** midway through every agent, which is what produced the 10 unreachable cells. Two tooling notes fell out: `src/scrapfly_browser.py` now reconfigures stdout to UTF-8 (it was crashing on cp1252 AFTER the paid render, losing the page), and WAF'd hosts should be re-probed one at a time with a real browser rather than in bulk.

**Same-day pipeline check** (`output/mddc_va_md_pipeline_check_2026-08-28.md`), findings worst first: MD RoW checkpoint contamination confirmed (global `last_cutoff 08/24/2026` from a Calvert-only run, ledger = 5 Calvert estates; recovery command and the per-county-checkpoint fix are in the report, NOT run); **VA regression fixed** — `filter_notice_type()` ran with the foreclosure-only default for every Popular Search, so `--popular-search 6` / `8` output zero rows; now `SEARCH_NOTICE_TYPE` keys the keep-set off the search, keeps `other` for 6/8 (clipped captions classify as `other`), and the classifier learned VA's `58.1-3965` / `64.2-550` phrasing; **MDDC saved search 41 is gone from the live site** (`--list-searches` caught it, exit 1) — default is now `["43"]`; `CAPTCHA_API_KEY` still the placeholder; SDAT Street-Address switch still unbuilt; MDDC's 08/27 code has never produced an output and lacks VA's exit-3 guard. Live: VA categories/counties unchanged, RoW data current to 08/27, Land Records + SDAT logins OK.

### Phase 4a DONE — the six entry tags exist (`src/scripts/dpd_tags_create.py`, 2026-08-26). FIRST WRITE TO THE ACCOUNT.

`Priority 1`, `Priority 2`, `FTM`, `Tier 2`, `Mail Only`, `Rehash Ready` — created and **verified present by read-back from a fresh page load**, 6 of 6. Nothing else was touched: no record, no existing tag, no list, no status.

**Why they had to come first.** Roughly 60 of the 73 presets filter on the four entry tags, and a DataSift filter can only select a tag that already exists. Building the presets first would have saved them with an empty tag block — structurally present, silently matching the wrong records.

**The list is deliberately six, not twenty.** Everything else the suppression stack needs is already on the account in another vocabulary and must NOT be duplicated: `Return Mail`, `Vacant`, `Deceased Owner`, `Obituaries` are live property tags; `Low Equity` / `Negative Equity` / `High Equity` are **lists**; `Sold` and `Already Sold` are **statuses**. A second `Sold` as a tag would split suppression across two vocabularies. Everything the pull stamps (`DPD T1`, the stack name, county, `DPD pulled <YYYY-MM>`) is created by the Add-Records modal and needs no hand step.

**THE CREATE CONTROL IS INSIDE A FOLDER, and this cost several probes.** At the top level `/tags/property` offers only `Create Folder` and a search box — there is no way to make a tag there. Click **into** a folder (this account has one, `default`, 34 pages) and an **`Add New Tag`** button appears in the same header slot, opening an inline `input[name="title"]` placeholder "New tag name" plus a **`Create Tag` button that starts DISABLED**.

Three mechanics, each of which produced a silent no-op first:
- **A JS `.click()` does nothing on this page.** The first probe produced three byte-identical screenshots. It needs a real `page.mouse.click()` at the element's centre.
- **Target the NARROWEST element whose text is the folder name.** The row's outer div also reads `default`, and its centre lands ~500px right of the link on empty space — which is what the first mouse click hit.
- **The disabled `Create Tag` button is free verification.** It enables only once React receives the value, so a button still disabled after typing proves the native-setter injection failed. The script refuses to click a disabled button rather than reporting success — exactly the silent-success shape this codebase keeps rediscovering.

Existence is checked with the folder's **search box, matched exactly on the row's first line**, not by paginating 34 pages: a substring test would report `FTM` present because some unrelated tag contains it. `--discover` dumps the live controls, and the whole script is a no-op for any tag that already exists, so re-running is safe.

### Phase 5a — the 12 preset folders exist (`src/scripts/dpd_presets_build.py --folders --commit`, 2026-08-26)

All 12 created and **verified present by read-back from a fresh page load**, 12 of 12: `01 HOTTEST - CALL`, `02 HOTTEST - MAIL`, `03 STRONG - CALL`, `04 STRONG - MAIL`, `05 FTM - CALL`, `06 FTM - MAIL`, `07 TIER 2 - CALL`, `08 TIER 2 - MAIL`, `09 BULK - CALL`, `10 BULK - MAIL`, `11 DEEP PROSPECTING`, `12 REACTIVATION`. They collide with nothing — this account's only pre-existing folder is `DEFAULT` with its 16 per-dialer presets, untouched.

The folder table carries each folder's preset count and `assert sum(...) == 73` runs at import, because a miscount here is a missing stage in somebody's call cadence.

**Panel mechanics, read off the live page** (`output/dpd_preset_probe.json`, `dpd_preset_probe.py`):
- The action bar is `Load | Save | Save New | Clear`, and **`Save` and `Save New` are DISABLED until at least one filter block exists**. That is the structural reason a preset cannot be created from an empty panel, and why the 73 need their blocks built before they can be saved at all.
- `Filter Presets` is a collapsed section at the BOTTOM of the panel, below a scroll boundary, and `Create New Folder` lives in its header row. The panel is a scrollable div rather than the viewport, so `scroll_into_view_if_needed()` does nothing — scroll the container.
- Every click goes through a real `page.mouse.click()` at the element centre. Playwright's own click reports "outside of viewport" or gets intercepted by `RecordsFiltersstyles__RecordsFiltersSection`, and a JS `.click()` silently no-ops on several of these controls.
- Same disabled-button discipline as the tag creator: a save button still disabled after typing means React never received the name, so the script reports it and refuses to click rather than claiming success. It also **stops on the first structural failure** instead of repeating it 11 times against a panel that is clearly not in the expected state.

**The 73 presets are NOT built.** They need the filter-block vocabulary mapped first — ten distinct block types, listed in `FILTER_BLOCKS` so the next pass knows exactly what to discover: Tags (include and exclude), Lists (exclude Low/Negative Equity), Property Status (exclude done/dead), `predictivecall_attempts` and `directmail_attempts` as numeric ranges, has-numbers, skiptraced, Vacant Mailing, last-direct-mailed age, and structure type. Nothing is built on a guess.

**Baseline diff after both write phases: `Nothing was removed. All changes are additions.`** Lists, phone tags, statuses, sequences and SiftMap presets all unchanged; property tags 334 -> 340, exactly the six created.

**But that diff proved less than it looked, and closing the gap exposed a worse bug in the doctor itself.** The diff reported `preset_items unchanged (16)` and named no folders, because `capture_presets` climbs to the container holding the preset LIST — so **an empty folder is invisible to it**, and Phase 7's own acceptance criterion ("the diff is clean apart from the new tags, 28 SiftMap presets and 12 new folders") could never actually have been checked. `capture_preset_folders()` now reads the folder rows directly and `preset_folders` is a diffed section.

**THE DOCTOR REFUSED A BAD CAPTURE IN WORDS AND WROTE IT ANYWAY, DESTROYING THE BASELINE.** `_report()` printed "Refusing to treat this as a usable baseline" and returned 1; `main()` ignored `rc` and called `baseline_path.write_text()` unconditionally. A thin capture (phone_tags **20 of 79**, statuses **0**, preset_items FAILED) therefore overwrote the known-good `output/dpd_baseline.json` — the rollback reference the whole build rests on. **Refusing in the report and writing in main is not refusing.** Two fixes: a non-zero report now writes to `dpd_baseline_rejected.json` and leaves the baseline untouched, and a good capture keeps a dated copy of the previous baseline before replacing it, so a reference is always recoverable. The baseline was restored from the clean 13:02 `_current` capture (27 lists, 340 property tags, 79 phone tags, 38 statuses, 16 preset items, zero failures); the pre-write 2026-08-25 original is gone, `output/` being gitignored, but its diff was already recorded above.

**A third, smaller instance of the same disease in the new code:** that broken run reported `preset_folders: 4` on a page where the panel had never opened, scraping whatever chrome happened to be narrow enough. It now requires the panel to be open and reports FAILED otherwise — junk that looks like data is worse than a stated failure.


### Phase 5b/5c — the Records filter panel, mapped (`dpd_filter_blocks.py`, `dpd_preset_save_probe.py`, 2026-08-26)

A preset is a saved set of FILTER BLOCKS, and none of them had been mapped. `output/dpd_filter_blocks.json` now holds the whole vocabulary: **144 blocks** in eleven sections (GENERAL, AI FILTERS, TASK, APPOINTMENT, PROPERTY, OWNER, OFFER, MARKETING, ADDITIONAL FIELDS, SIFTLINE, CUSTOM FIELDS, MISC). All ten checklist items have real controls, and the mapping lives in `src/dpd/block_map.py`.

**THE ACTION BAR IS REAL AND IT IS AT THE TOP OF THE PANEL** -- `Load | Save | Save New | Clear`, directly under the "Filter Records" heading, exactly as the Phase 0 notes said. Five separate DOM scans concluded it did not exist anywhere in the document, at any visibility, at viewports up to 2560x1440, with and without a filter block present. **Every one of those scans filtered to LEAF nodes (`children.length === 0`), and each control wraps an `<svg>` icon beside its text node.** Scan by `innerText` across all elements and take the narrowest match. The same trap hides `Save Preset` and `Cancel` in the save dialog. The lesson generalises past this panel: a leaf-only scan silently cannot see any icon+text control, and it fails by reporting absence, which reads exactly like a real finding.

**`Save New` is enabled the moment one filter block exists; `Save` stays greyed until a preset is LOADED** (it overwrites, and is never clicked by this build). Save New opens an inline dialog: `input[name="new_preset_name"]`, a FOLDER dropdown defaulting to `default`, two checkboxes (Add preset to quick filters, Export file on schedule), and `Cancel` / `Save Preset`.

**PARAMS & OTHERS IS THE BOOLEAN CONTAINER, and it is what the challenge guide's wording always meant.** Its 14 parameters -- Absentee, Vacant Mailing, Vacant Property, Property PO Box, Owner PO Box, **Numbers**, Phone Type, Owner Type, **DNC**, **Opt-out**, **Skiptraced**, Direct Mailed, **Deceased**, Record Type -- are each a Yes/No selector (Absentee also offers In state / Out of state). Without it, stages `00 Needs Skipped` and `01 Skipped No Numbers` would have been byte-identical presets, because nothing else on the panel can say "has been skip traced".

**ONE BLOCK OF EACH TYPE, EVER.** Verified live: a second block of the same type is refused (the dropdown stops offering it). The AND and OR variants ARE separate types and coexist, so each preset has a budget of exactly one OR-group and one AND-group for tags, and the same for lists. A single-value group is safe in either block, because AND and OR mean the same thing for one value. Consequence: a preset needing an OR-include AND an OR-exclude cannot have both, and `allocate_tags()` resolves it by giving OR to whichever group is multi-value and trimming the exclusion when both are. Two documented trims: **09 BULK - CALL** keeps `FTM` in its exclusion (keeping the first-to-market blitz out of the volume lane is the point of the folder) and drops `Mail Only`; **Deep - 04 Return Mail** drops it for the same reason. Both cost nothing today, because `Mail Only` is a Phase 4 tag not yet stamped on any record.

**PRESET NAMES ARE UNIQUE ACROSS THE WHOLE ACCOUNT, not per folder.** The dialog refuses a duplicate with "This preset name is already in use.", rendered in a banner above the name field -- which is why the folder-prefixed names (`Hottest - 02 Ready to Call`, `Bulk - 02 Ready to Call`) are load-bearing rather than cosmetic. **The preset LIST truncates a displayed name at about 25 characters** (`ZZ SMOKE TEST 144921 - del`), so verification must compare on a prefix; an equality test reports a preset that saved perfectly as missing.

**Three answers that shape what the 73 can and cannot express:**
- **The Property Status picker offers 19 of this account's 38 statuses.** Checked one at a time by typing each into the picker: 10 of 16 `DEAD_STATUSES` matched, and **Opt-out, Lost Deal, Close Out, Buyer, Buyer Found and Buyer Lost returned nothing at all.** `DNC` and `Opt-out` are recovered through Params & Others; the remaining four are a stated gap. A suppression naming a status the picker cannot select is a suppression that silently does not apply.
- **Date blocks are absolute only** -- `Fixed | Since | Prior to Date` against a calendar, no relative mode. A saved preset holding "prior to today-30" gets MORE restrictive as time passes, hiding work rather than showing too much, so the mail cadence's 30-day gate is omitted and stated instead of approximated.
- **Nothing filters on how long a record has been IN a status,** so the four Reactivation timers are built without their age gate and flagged.

**PHASE 0'S BASELINE IS WRONG ABOUT PRESET FOLDERS.** There are **seven** pre-existing folders -- `1. LEAD MANAGEMENT` (5), `2. ACQUISITIONS` (2), `3. TRANSACTIONS` (2), `30 MY TASKS` (3), `50. NICHE SEQUENTIAL MARKETING` (12), `REISIFT BASE PRESETS` (4), `DEFAULT` (16) -- not the single `DEFAULT` recorded earlier. `dpd_doctor.capture_preset_folders` missed them because the folder list scrolls inside its own container (`PresetsBelowBody`, scrollHeight 1254 against clientHeight 524) and it read only what was on screen. **Phase 7's baseline diff is wrong as written until that capture is fixed** (FIXED 2026-08-28: `capture_presets` reads all 19 folders positionally with a full scroll; the final diff in "State as of 2026-08-28" comes from the fixed capture). Note that `50. NICHE SEQUENTIAL MARKETING` already holds 12 presets, which is the superseded `src/niche_sequential.py` scheme -- leave it alone; the two designs are incompatible.

**Reading a folder's presets must be POSITIONAL**, between its row and the next folder's row. Climbing the DOM until a container "has preset children" does not work: an EMPTY folder has none, so the climb runs to the body and returns the DEFAULT folder's 16. Every one of the 12 new folders reported "holds 16 presets" that way -- the exact shape of a read that looks like data and is not.

**Manual `page.mouse.click` does not auto-scroll the way Playwright's own click does.** Blocks stack down the panel, so by the time Params & Others is added its rows sit below the viewport and a click at their coordinates lands on whatever is on screen there instead. Scroll the target into view and RE-MEASURE before every manual click. Related, and already recorded for SiftMap: the panel is an overlay drawn over the records grid, and the grid's cells keep their bounding boxes underneath it, so an x-coordinate scan picked `24d ago` out of the table behind the panel. Scope by container element, never by x.

### State as of 2026-08-28 — ALL 73 RECORDS PRESETS BUILT AND VERIFIED; 24 DC SiftMap presets saved; ZERO records added

**`dpd_presets_verify.py`: checked 73/73 against the server store, missing 0, defects 0, extras 0.** Folders 01–12 hold 6/6/6/6/6/6/6/6/9/6/5/5. Every throwaway (`ZZ …`) preset is gone — deletion is automated now (see below), not a hand step. No record, tag, list, status, sequence or pre-existing preset was altered; the final `dpd_doctor --verify` diff is additions only. **The one hand step left in the account is in SiftMap, not Records: delete the saved filter `ZZ DPD SMOKE 153311` from Presets → Saved Filters** (the Save Filters UI exposes no delete control the automation could drive).

**How the last 36 were built (2026-08-28, 01:46–03:05, unattended):** `python -u src/scripts/dpd_presets_create.py --commit --headless` detached via `Start-Process`, ~2.2 min per preset, **zero retries, zero abandonments, zero picker diagnostics**. The two 2026-08-27 resume attempts had not gone that way: run a abandoned `Tier 2 - 00` (county block missed `District Of Columbia`) and `Tier 2 - 03` (status block missed all ten tokens) and lost `Tier 2 - 02` to "the dialog is still open", then died silently; run b died on a single 5-second wait for the filter-panel trigger. Three fixes, all in `dpd_presets_create.py`: (1) `_PICK_INPUT_JS` resolves the picker as `audit_panel` does — panel root = the aside owning `#RecordsFilters__Filter_Blocks__Search`, block = the INNERMOST `RecordsFiltersSection-` whose first ALL-CAPS leaf equals the block label, input marked `data-dpd-pick` so typing and suggestion-click cannot resolve two different inputs — replacing an `innerText.startsWith(label)` match that was intermittent (a wrapper section beginning with the same heading matched or missed by layout, and every miss fell through to "last placeholder input in the document", the rule that mis-filed the exclusion tags in the first place); it prints its resolution branch on any miss. (2) `set_tokens` retries a missed value once from scratch (dismiss, re-resolve, retype). (3) `open_panel` polls for the trigger up to 20 s and reloads `/records` once before giving up. With (1) in place, (2) and (3) never fired.

**Storage keys settled by the last two folders:** `Deceased = Yes` stores as **`deceased: 1`**; `Vacant Property = Yes` matched a `PARAM_KEY` candidate (the verifier fails loudly if none does, so 0 defects is a real pass). **Verifier bug fixed 2026-08-28:** for a single-value tag group it read `any_tags or all_tags`, so on the two presets with TWO include groups (`Deep - 04 Return Mail`, `Rehash - 05 Rehash Ready`: the four-tag tier scope on OR plus one tag on AND) it grabbed the four-uuid OR group and reported the AND block "stored as 4 uuids". The store was right; the verifier now reads the key its block writes to (include: OR→`any_tags`, AND→`all_tags`; exclude side flipped). Solved uuids now include `Return Mail` and `Rehash Ready`.

**Every preset carries a `Property County` INCLUDE block with nine jurisdictions (Basem, 2026-08-27: "check the newest siftmap preset on the account and make sure to add all of those counties in", then "use obituaries in 9 counties").** The source is the account's SiftMap saved filter live-named **`Obituary 9 Counties`** (not "Obituaries in 9 Counties"), read off its URL `location` parameter: District Of Columbia (11001), Anne Arundel (24003), Baltimore County (24005), Frederick (24021), Montgomery (24031), Arlington (51013), Fairfax (51059), Alexandria City (51510), Prince William (51153). `preset_spec.COUNTY_SCOPE` holds the EXACT picker strings — the picker also offers the near-collisions this set excludes (`Baltimore City`, `Fairfax City`, `Fredericksburg City`), and `Alexandria` is stored as `Alexandria City`. This is narrower than the 14-jurisdiction default list (no Calvert, Carroll, Charles, Stafford, Spotsylvania, Fredericksburg City): records pulled from those six will not appear in any of these presets.

**Running the builder again (any county's future presets):** always `-u` and detached — stdout is block-buffered when redirected, and the Bash/PowerShell tools cap a foreground command at 10 minutes. A hung run (Chromium died, Python waiting on the dead pipe) shows flat CPU on `Get-Process python` across 20 s and zero `Get-Process chrome | ? Path -like '*ms-playwright*'`; kill and re-run, the skip logic loses at most the in-progress preset. Two processes doing a FRESH login in the same second collide on `datasift_cookies.tmp → .json` (`WinError 32`); start the second only after the first has saved cookies. Read-only browser runs (`dpd_doctor`) alongside a live build are safe — a fresh doctor login at 01:48 did not disturb the builder's session.

### The 2026-08-27 findings — what the smoke test and the load-back check caught

**The audit guard fired on the very first smoke test and was itself wrong.** It read every block as empty while the panel was correct. `_PANEL_DUMP` windowed the page by y between ALL-CAPS leaves at x>=950, and the records grid behind the overlay keeps its boxes there — column headers (`STATUS`, `LISTS`), cell values (`1076`), even the all-caps status chip `DNC` became fake block headings and fragmented every window. It also looked for chips by a `Chip|Badge|Token` class regex that matches nothing here. Rewritten to the codebase's own rule (scope by container, never by x): the panel root is the `Asidestyles__AsideContainer` that owns `#RecordsFilters__Filter_Blocks__Search`, each block is a `RecordsFiltersstyles__RecordsFiltersSection-` element, chips are `TagInputstyles__SelectedTag` (tags) / `EntryInputstyles__SelectedEntry` (statuses) / `ListInputstyles__SelectedListTitle` (lists), option lists are `Inputstyles__InputSuggestion*` and are excluded. **An audit that has never been seen passing is unproven in both directions.**

**A real build bug the fixed audit then exposed: the status chip `Auction Date Passed` was landing 9 times out of 10.** `_click_leaf` took the NARROWEST exact-text match, and a record's status badge in the grid behind the overlay is narrower than the picker's suggestion row; the overlay swallowed the click and `set_tokens` reported success. Two fixes: `_click_leaf` now hit-tests with `document.elementFromPoint` and only accepts a candidate the click will actually reach; `set_tokens` resolves the suggestion INSIDE the active input's own `InputContainer` (closed suggestion lists from earlier pickers stay in the DOM with real boxes, and the same value can be a tag, a status and a badge at once).

**The folder dropdown in the Save New dialog SCROLLS, and folders 04 and below were outside it.** With 19 folders the first three are in view; a lower folder's option had a rect but sat under the list's edge, the click landed on nothing, the control stayed on `default`, and the read-back guard cancelled the save — correctly, but only after a full ~4-minute build per preset. Six presets failed that way before the live output showed it. The pick now searches `SelectOptionContainer` elements, scrolls the option into view, re-measures and hit-tests.

**LOADING A PRESET RENDERS EVERY EXCLUSION AS "Include". THE STORE IS RIGHT; THE PANEL IS WRONG.** The load-back check (load `Hottest - 02 Ready to Call`, audit the restored panel) reported all three exclusion modes as `Include`, and an A/B (Save New with and without Apply Filters first) plus a load→flip→Save overwrite all read back the same. It looked exactly like the 2026-08-26 inversion. The truth came from the app's own network traffic: `GET /api/internal/filter-preset-folder/{uuid}/filter-preset/` returns the stored definition, and it is correct — `must.all_tags [Priority 1]`, `must_not.all_tags [Mail Only]`, `must_not.all_lists [2 uuids]`, `must_not.any_property_status [all 10]`, `any_county [9, isNegative:false]`, `dnc 0, opt_out 0, phone, skiptraced, predictivecall_attempts [lo,hi]`. Behavioral proof: loading the exclude-Low-Equity test preset and applying it DROPS Maria Carcamo (a Low Equity member) and keeps Michele Ingulia and Brenda Stein. So the mode dropdown simply draws its default on load. **Consequences: (1) do not "fix" a loaded preset's mode by hand and do not re-save from a loaded panel — that may serialize the displayed Include; (2) Phase 7 QA reads the store, not the panel: `dpd_presets_verify.py`.** This also means the 2026-08-26 "14 inverted presets" diagnosis may have been partly this rendering quirk, though the closed-select click bug it found was real.

**Storage facts read off the internal API:** titles are stored TRUNCATED at roughly 24–26 characters (width-dependent, not a fixed count: `Hottest - 02 Ready to Ca`, `Hottest - 01 Skipped No Nu`), so `_present()`'s prefix match is load-bearing and `preset_spec` names are verified collision-free through length 22. Tag and list values are bare uuids with no names; the verifier solves names by algebra from single-value groups (Priority 1 = `b34b1ec1-…`, Mail Only = `72c1956d-…`). `Vacant Mailing = No` stores as **`owner_vacant: 0`**, not `vacant_mailing`; `Numbers` is `phone`; the Deceased / Vacant Property keys are unobserved until folder 11 exists (`PARAM_KEY` carries candidates). A multi-value OR group MUST be stored under an `any_` key — `must_not.all_tags [P1, P2]` would mean "exclude records carrying BOTH", a much weaker test — and the verifier fails on that.

**The internal API is READABLE on this account** (the account-wide audit already used it), so "never the API" in the standing constraints means never for WRITES. Read surface used today: `filter-preset-folder/?limit=999&type=properties` and `filter-preset-folder/{uuid}/filter-preset/?limit=999&type=properties`, minted with the same `POST /api/token/` JWT as `datasift_api_upload.Api`.

---

## READ THIS BEFORE BUILDING ANY PRESET — the 2026-08-26 failure, in full

**14 presets were saved with their suppression INVERTED, and every check reported success.** A `Property Status` block meant to EXCLUDE ten dead statuses was saved as INCLUDE, so loading that preset returned only dead leads. The list block INCLUDED `Low Equity` / `Negative Equity` instead of excluding them. `Mail Only` was included rather than kept out. Basem caught it by looking at the panel; the automation never did. All 14 have since been deleted.

### The four mechanical bugs

1. **A leaf-node DOM scan cannot see any icon+text control.** Five separate passes concluded "there is no `Load | Save | Save New | Clear` anywhere in the document, at any visibility, at viewports up to 2560x1440". The bar was there the whole time, at the TOP of the panel under "Filter Records". Every scan filtered `el.children.length === 0`, and each control wraps an `<svg>` beside its text node. **The earlier CLAUDE.md note about the action bar was RIGHT; the scans were wrong.** Scan by `innerText` across all elements and take the narrowest match. Same trap hides `Save Preset` and `Cancel` in the save dialog.

2. **A styled select's options are in the DOM while it is CLOSED.** Clicking an option without first opening the select finds an element, clicks it, returns truthy, and changes nothing. This is what set every suppression block to Include and left every Params row on `Select`. The value must be clicked through the `SelectValue`, and then **read back**.

3. **"The open list is the visible one" is FALSE here.** Every one of the 14 Params rows reports its `SelectOptionsContainerScroller` as visible. Choosing the scroller nearest the trigger lands one row off — setting `Numbers` actually set `Owner PO Box`. The only reliable route is through the element itself: resolve the row's `SelectValue` as a Playwright handle, climb to its own `SelectContainer`, and query the scroller INSIDE it.

4. **An autocomplete list left open corrupts the NEXT click.** After choosing a tag the list stays open and overlays the controls below, so the following click (usually "Add new filter block") lands on a list item and silently adds a second value. That is the entire origin of the stray `25.3 Upload`, `Building Permits 2025` and `Hot Lead` chips — each is simply the second entry in its alphabetical list. Clear the input and dismiss the list after every selection. (CLAUDE.md's own DataSift UI Automation Patterns section already said this: "after each selection, `fill("")` + Escape to dismiss dropdown before next entry". It was not followed.)

### The verification gap that let all of it ship — the real lesson

The builder verified that each filter block was **added** (block count went up) and the read-back verified that the preset **name** existed in the folder. Neither ever looked at what the blocks **contained**. So the run printed `OK` for every preset and `6 present, 0 missing` on read-back while saving fourteen inverted presets.

**Verifying that a control was touched is not the same as verifying what it says.** `audit_panel()` in `dpd_presets_create.py` now reads the whole panel back — every block's mode, chips, min/max and Params rows — and compares it to the spec, refusing to save on any mismatch. **Do not remove that guard, and do not trust a preset run that has not passed it.** Before trusting a first build, screenshot the panel and look at it.

### Three more traps found the same day

- **`read_folder` returned 16 for every empty folder.** It climbed the DOM until a container "has preset children"; an empty folder has none, so the climb ran to the body and returned the DEFAULT folder's presets. Read folder contents **positionally**, between this folder's row and the next folder's row.
- **Phase 0's baseline is wrong about folders.** There are **7** pre-existing preset folders — `1. LEAD MANAGEMENT` (5), `2. ACQUISITIONS` (2), `3. TRANSACTIONS` (2), `30 MY TASKS` (3), `50. NICHE SEQUENTIAL MARKETING` (12), `REISIFT BASE PRESETS` (4), `DEFAULT` (16) — not the single `DEFAULT` recorded earlier. `dpd_doctor` missed six of them because the folder list scrolls inside `PresetsBelowBody` (scrollHeight 1254 against clientHeight 524) and it read only what was on screen. **Phase 7's baseline diff will not work until that capture is fixed** (FIXED 2026-08-28, see "State as of 2026-08-28"). `50. NICHE SEQUENTIAL MARKETING` already holds the superseded `src/niche_sequential.py` scheme — leave it alone.
- **`page.mouse.click` does not auto-scroll** the way Playwright's own click does. Blocks stack, so by the time Params is added its rows are below the viewport and a click at their coordinates hits whatever is on screen there. Scroll into view and RE-MEASURE before every manual click.

### Contract details that are load-bearing

- **Preset names are unique ACROSS THE ACCOUNT, not per folder** ("This preset name is already in use.", shown in a banner above the name field). The folder-prefixed names in `preset_spec.py` are therefore required, not cosmetic.
- **The preset list truncates a displayed name at about 25 characters**, so any presence check must compare on a prefix. An equality test reports a preset that saved perfectly as missing.
- **One block of each type, ever.** A second block of the same type is refused. The AND and OR variants are separate types and coexist, so each preset gets one OR-group and one AND-group for tags, and the same for lists. `allocate_tags()` fits the groups and records the two forced trims (`09 BULK - CALL` and `Deep - 04 Return Mail` each lose the `Mail Only` exclusion; costs nothing today because `Mail Only` is not stamped on any record yet).
- **The Save New dialog:** `input[name="new_preset_name"]`, a FOLDER dropdown defaulting to `default`, two checkboxes, then `Cancel` / `Save Preset`. **The folder dropdown must be OPENED and picked, then read back** — clicking the folder's name without opening it matches the folder row in the presets list below, changes nothing, and the preset lands in `default` while the run reports success. That happened once.
- **`Save New` is enabled as soon as one block exists; `Save` overwrites a LOADED preset and is never clicked by this build.**

---

## Where the code stands

- `src/dpd/preset_spec.py` — the 73 presets as data, asserted at import (6/6/6/6/6/6/6/6/9/6/5/5).
- `src/dpd/block_map.py` — the panel mapping, the stated gaps, the block budget and its one remaining trim (`Deep - 04 Return Mail`).
- `src/scripts/dpd_filter_blocks.py` — the 144-block vocabulary dump (`output/dpd_filter_blocks.json`).
- `src/scripts/dpd_presets_create.py` — the builder, **PROVEN on all 73**: container-scoped `audit_panel`, hit-tested `_click_leaf`, section-scoped `_PICK_INPUT_JS` / `set_tokens` with one retry, the scrolled folder pick, polling `open_panel`, a pre-save panel screenshot per folder (`output/dpd_panel_*.png`), and preset deletion (`--discover-delete | --delete-junk-dry | --delete-junk [--delete-names ...]`, refuses anything not `^ZZ ` or explicitly allowlisted). Resumable; skips by name prefix.
- `src/scripts/dpd_presets_verify.py` — **Phase 7 QA for presets.** Reads every stored definition over the internal API and checks counties, suppression, statuses, counters, params and tag-uuid algebra against `preset_spec`; lists extras. Exit 0 only when all 73 are present and defect-free. **Currently exits 0.** Run it after any preset change.
- `src/scripts/dpd_doctor.py` — `capture_presets` rewritten onto the builder's panel primitives (positional per-folder read, full scroll of `PresetsBelowBody`, no-toggle re-read for a folder that is open by default); writes `preset_items`, `preset_folders`, `preset_tree`. `--verify` diff is trustworthy again.
- `src/scripts/dpd_playbook_extract.py`, `dpd_siftmap_manifest.py`, `dpd_siftmap_presets.py`, `dpd_qa_report.py` — the county-agnostic DC one-shot pipeline (next section).

**Stated gaps, omitted on purpose rather than approximated** (all in `block_map.GAPS`): the mail cadence's 30-day spacing (date blocks are absolute-only, and a fixed date silently hides work as time passes); the four Reactivation status-age timers (nothing filters on time-in-status); the single-family filter (would drop the 2,892 blank-`structure_type` records, and the buy box is applied at pull time instead); the recently-sold tag (does not exist on this account). Also: the Property Status picker offers 19 of 38 statuses — `DNC` and `Opt-out` are recovered through Params & Others, but `Lost Deal`, `Close Out`, `Buyer`, `Buyer Found` and `Buyer Lost` cannot be selected at all.

## Order of work (from 2026-08-28)

1. **Hand step:** delete the SiftMap saved filter `ZZ DPD SMOKE 153311`.
2. **Make the daily DC foreclosure feed reach the FTM lane:** `mddc_datasift_upload.BATCH_TAG` stamps `Claude first batch 8.22`, not `FTM`, so nothing lands in `05 FTM - CALL` / `06 FTM - MAIL`. One-line change, left for an explicit decision because it changes what every future MDDC upload is tagged.
3. **Run the two free VA pulls** — `va_trustee_sale_pull.py --popular-search 6` (Estate Claims) and `8` (Tax Deeds) across the five VA counties. Existing code, no new credential, fills 10 cells of the FTM matrix. Firecrawl returns an empty `<body>` shell on more than half of attempts; the script retries 5x and refuses to write a false zero, so an exit 3 means "try again", not "no results".
4. **The DC pull (Phase 4) — ONLY on Basem's explicit go.** Described under "What the next (pull) run would do" below. Until it runs, every one of the 73 presets loads empty for DC.
5. **The other 13 jurisdictions:** `dpd_playbook_extract.py --fips <fips> --fetch` (or with that county's downloaded workbook) → `dpd_siftmap_manifest.py --measure` → `dpd_siftmap_presets.py --commit --verify`. The Records presets already cover the nine-county scope; the six counties outside it need `COUNTY_SCOPE` widened first.
6. Sign-ups when Basem is ready (DC Recorder of Deeds free registration; a real `CAPTCHA_API_KEY`; DOB eRecords) — the ordered list is in `output/dpd_dc_ftm_investigation.md`.

**Standing constraints:** create no Lists (reuse what exists; a SiftMap pull applies *tags* in the Add-Records modal, not lists); `.env` browser auth for every WRITE (presets/folders/SiftMap have no write API, and this account's internal API 403s on writes) — internal-API READS are fine and are how `dpd_presets_verify.py` checks the store; Priority 1/2 are stamped at pull time so they arrive with the property; FTM is stamped by the daily Register of Wills / MDDC / VA pulls; never click `Add Records to Account` and never tick the Save Filters PRO auto-add checkbox without an explicit go.

**Decisions taken 2026-08-26 (Basem):** the high-lift slices ride ALONGSIDE the widened T1s (Baltimore `Senior + Vacant` 182/16.5x, Fairfax `Notice of Default` 91/37.3x, Arlington `Free & Clear + Out-of-State + Senior` 123/16.5x as their own smaller higher-priority lists; DC's are skipped as its 76.8x measures 1 record). **AI data is NOT being bought**, so build on the distressor and geography layers only. **Superseded for DC on 2026-08-27** by the per-row design below; still the plan of record for the 13 counties whose per-row manifests have not been built.

## Doors-Per-Deal DC one-shot (2026-08-27) — per-row SiftMap presets, the 73 Records presets, the DC FTM map

Basem's instruction, verbatim in spirit: build the DC lists in SiftMap from the doors-per-deal framework, label them by priority, mirror the challenge's account structure, start the FTM investigation — **and do not pull any records in.** Sources of truth for this run were exactly two: the single-county workbook he downloaded from the playbook page (`data/11001-District-of-Columbia-DC-doors-per-deal.xlsx`) and the page itself (`learn.datasift.ai/county-list-playbook#11001`). **The `county-compare-*.xlsx` files and the artifacts derived from them (`dpd_signal_rankings.json`, the single-T1 design in `dpd_tier_configs.json`) are NOT inputs for this and should not be used for the other 13 either** — they are a thinner Community Edition export of the same data (no Top ZIPs / Price Bands / AI sheets, no Plan / Deal Share / Coverage columns).

**THE PLAYBOOK PRESCRIBES ONE SIFTMAP PRESET PER PRIORITY ROW, not one T1 stack.** Verbatim: *"pull your Priority 1 rows … save each one as its own preset in SiftMap, and work them top to bottom"*, and *"never AND-stack a distress list on top of the score."* That supersedes the single T1 / T2 design of Phase 3 for DC. Decisions Basem took 2026-08-27: P1 **and** P2 rows; the Tier 2 ZIP preset too; Tired Landlord via a labeled proxy; **no Add Records** this run.

**The playbook's data is free JSON, per state.** `learn.datasift.ai/county-data/<st>.json`, `ftm-data/<st>.json`, `capture-data/<st>.json` (`11` DC, `24` MD, `51` VA), every county under its 5-digit FIPS, no auth, no Firecrawl. The `#11001` anchor is a client-side selector, not a page. The xlsx and the shard agree to the unit (58 vs 57 rows — the Obituary model-estimate row is workbook-only); the shard adds each row's stable `keys` (`is_absentee_owners`, `is_notice_of_foreclosure`…), the AI demand curve, rated ZIPs with per-ZIP signal lift, and the capture ladder.

### The pipeline (county-agnostic, `--fips`; run for 11001)

```bash
python src/scripts/dpd_playbook_extract.py --fips 11001 --xlsx "<workbook>" --fetch   # -> data/dpd_playbook_11001.json (+ _shards.json cross-check)
python src/scripts/dpd_siftmap_manifest.py --fips 11001 --measure                    # -> data/dpd_siftmap_manifest_11001.json, live counts
python src/scripts/dpd_siftmap_presets.py --fips 11001 --discover|--smoke-test|--commit|--verify
python src/scripts/dpd_qa_report.py --fips 11001                                     # -> output/dpd_qa_report_11001.md
```
Another county is a re-run with its own downloaded workbook (or `--fetch` alone), not a rebuild. Market Finder is NOT a prerequisite: the workbook's rated ZIPs are the Tier 2 geography.

### What is in the account now (SiftMap side) — 24 saved filters, verified by reload, ZERO records added

**23 priority presets + 1 Tier 2**, named `DC P1-05 Notice of Default`, `DC P2-09 Absentee`, `DC T2 Top ZIPs 20019 20020 20011 20002 20032` — the number is the row's rank within its priority in the workbook, so the Presets popover sorts in ladder order. Every URL carries `type_single_family=true`; none carries `in_my_account_mode=not_in` (that is a pull-time suppression, measured separately). `--verify`: **24 ok, 0 not ok** — each reloads from the popover with county, buy box and distressor params intact at its measured count.

**THE WORKBOOK AND SIFTMAP AGREE TO THE UNIT.** DC single-family baseline in SiftMap = **48,530 = the workbook's SFR supply**. Absentee 7,165 vs 7,206; Free & Clear 11,433 vs 11,350; Out-of-State 3,416 vs 3,434; Absentee + Free & Clear 2,921 vs 2,920; Lis Pendens 906 vs 351 (the one that diverges — the LP filter is wider than the workbook's list). Tier 2 (five 4-5★ ZIPs) = **21,519**, inside the playbook's 20-25K band. The high-lift foreclosure rows are real but tiny: Notice of Default 19, Notice of Foreclosure 5, F&C + NF 1, Absentee + NF **0**. They are saved anyway (a preset with 0 records today is still the right filter).

**TIRED LANDLORD IS NOT A PROXY ANY MORE — IT IS THE DEFINITION.** `extra_owners_with_multiple_properties=true` (the obvious control) returns **47,399 of 48,530 (98%)** against a workbook list of 2,046 — it "filters" but restricts nothing. `preset_absentee_owners=true&extra_years_owned_min=10` returns **2,050**, and its stacks land too: Out-of-State + TL **945 vs 949**, High Equity + Out-of-State + TL **566 vs 572**, Senior + TL 483 vs 482, F&C + TL 561 vs 550. So DataSift's Tired Landlord is absentee + owned 10+ years. `dpd_siftmap_manifest.PROXY_BAND` (0.5x–2x of the workbook's standalone list) is the gate that rejected the 98% candidate; **a count that is merely "not the baseline" is not a filter.** The presets keep "(proxy)" in their names because that is the label Basem approved; the description field records the definition.

**16 P1/P2 rows SiftMap cannot express** — in the manifest as `gaps`, routed to FTM: the HOA Lien pair (P1-02 76.8x, P1-06 21.3x), the nine Other Lien rows (5.1x–15.1x), Low Income, Bad Credit, the two AI-score bands (AI data not bought), Obituary (an FTM list). SiftMap shows HOA Lien / Bankruptcy badges on cards but will not filter on them.

**THE SAVE FILTERS DIALOG, mapped (`dpd_siftmap_presets.py --discover`):** rail button at ~(1324, 84), real mouse click. Title "Save Filter"; required input placeholder `Enter new search name`; optional `Enter description`; a **PRO checkbox "Automatically add new properties that start to match this filter after it has been saved."** — that is an automatic PULL and the script asserts it is unchecked and never ticks it; `Cancel`; `Confirm`, disabled until React receives the name. A saved filter appears under the Presets popover's **"Saved Filters"** heading (`dpd_doctor.capture_siftmap_presets` reads exactly that), each row with an export icon and a favorite star and **no delete control**; "Configure" just closes the popover. Reloading a saved filter appends `id=<n>` to the URL — the comparison ignores that key. **The popover LIST SCROLLS**: with 29 saved filters a row can sit at y = −51 and still report a bounding box, so a click at its centre lands on the map and the read-back sees the unfiltered county (every DC preset "reloaded" to 48,530 on the first verify). `load_saved_filter` now scrolls the row into view and requires the URL to change. Saving a filter does NOT create a List (lists 27 before and after). **One throwaway `ZZ DPD SMOKE 153311` remains in Saved Filters** — nothing in the UI deletes a saved filter; leave it or remove it by hand if a control exists somewhere we did not find.

**The 4 hand-made saved filters (`Obituary 9 Counties`, `Obituaries in 15 Counties`, `Stacked Distressors`, `District Of Columbia, DC`) are Basem's, untouched.** `siftmap_presets` 22 → 49 = 20 defaults + 24 ours + the smoke + those 4 (two of his were already in the 2026-08-26 baseline, `Obituary 9 Counties` and `District Of Columbia, DC` were made after it, so the diff lists only those two).

### Records presets (the challenge structure)

**All 73 built and verified — see "State as of 2026-08-28" above for the counts, the storage keys and the builder fixes.** Built in four passes: 23 on 2026-08-27 morning, 13 more that afternoon (`04 STRONG - MAIL` finished, `05`/`06 FTM` complete), `Tier 2 - 00/01` deleted and rebuilt after the exclusion-semantics finding below, and the final 36 unattended on 2026-08-28.

**THE EXCLUSION SEMANTICS ARE THE OPPOSITE OF THE BLOCK LABELS, and the first multi-value lane shipped weak.** Caught by an early `dpd_presets_verify.py` run at 15/50: `Tier 2 - 00/01` stored their three-tag exclusion under `must_not.all_tags`. Measured live on the Clean tab (N = 2,363; A = `Absentee Owner`, B = `Courthouse Data`, |A∪B| = 660): **"Any Tags (OR)" + Do not include [A,B] removes 0 records** (stored `must_not.all_tags` = carrying BOTH); **"All Tags (AND)" + Do not include [A,B] removes 660** (stored `must_not.any_tags` = carrying ANY — what a suppression means); a single-value exclusion removes 659 either way; OR-include [A,B] = 660 as labelled. So on the `must_not` side the storage key is swapped relative to the label, and **the key decides the behavior**. `allocate_tags` now puts every multi-value EXCLUSION on the AND block and the include on OR, which also dissolves the old `09 BULK - CALL` trim (both `FTM` and `Mail Only` are excluded now; the only remaining trim is `Deep - 04 Return Mail`, two include groups). `dpd_presets_verify.py` requires a multi-value exclusion under `must_not.any_tags` regardless of block. The two defective presets were deleted and rebuilt; the 30 single-value presets were unaffected (one value means the same under either key). **Second bug the fix exposed:** `set_tokens` typed into "the last `Search for tags` input" — the panel renders tag blocks in a fixed type order (AND above OR), so that was always the OR block; with the AND block added second every exclusion tag landed in the include block and only `audit_panel` noticed. It now scopes the input to the `RecordsFiltersSection` whose heading matches the block. **An audit that compares what a control SAYS, not whether it was touched, is what caught both.**

**Deleting a preset IS automated now** (`dpd_presets_create.py --discover-delete | --delete-junk-dry | --delete-junk --only "<folder>"`): each row has an export icon, a favorite star and a **kebab** (`PresetsBelowCollapsibleFolderOptionsIcon`); the kebab menu is `Move to Folder / Rename preset / Delete preset` (icon+text rows — a leaf-only scan reports the menu empty, the same trap as the action bar); "Delete preset" opens *"Are you sure you wanna delete this preset? You are about to delete "<name>" — CONFIRM BY TYPING DELETE FOREVER — No, I don't want to delete it / Yes, delete it"*. The handler types `DELETE FOREVER`, refuses unless the quoted name matches `^ZZ `, and re-measures the row's rect right before hovering (a rect measured before a later `scrollIntoView` pointed the kebab click at the neighbouring row once). The four junk presets are gone; `01 HOTTEST - CALL` reads back at exactly 6.

The `vacant_mailing` "defect" was never a defect: every Mail preset stores `owner_vacant: 0`, which `PARAM_KEY` already accepts; the 13:26 verify simply ran before the `02 HOTTEST - MAIL` run finished.

**Folder headers in the panel are `SectionHeadingstyles__SectionHeadingTitle` inside `Collapsible__trigger` rows; `CollapsibleFolderTitle` does not exist in this DOM.** `dpd_doctor.capture_presets` was rewritten onto the builder's `open_panel` / `expand_presets_section` / `read_folder` (positional, per folder, full scroll of `PresetsBelowBody`) and now writes `preset_folders` + `preset_tree`; the old heading-climb scrape was capturing records-grid cells (`Annapolis, MD 21409`, `99d ago`) as presets. **The rewrite accidentally deleted `capture_sequences` and `capture_siftmap_presets` (a replace between two anchors that had other functions between them) — both restored; `capture_sequences` must expand folders (`/sequences` renders folder rows — Acquisitions, Lead Management, Transactions, default, Basem Test — until expanded, and returns the `Sequence Name` header as a row, now filtered). Read properly the account holds **5 real sequences, all Basem's** (`Placeholder`, `[LOOP] Offer Follow-Up`, `[MOVE] Send back to LM`, `[MOVE] Under Contract`, `[NEW] Offer Follow-Up`); the 2026-08-26 baseline's "0 sequences" and "1 preset folder" were capture failures, not account facts. A second trap in the rewrite: `read_folder` toggles a folder open, so the DEFAULT folder — open by default — was toggled CLOSED and read as 0, which the diff reported as 16 removed presets; `_read_folder_no_toggle` re-reads any folder that comes back empty.** **Final `dpd_doctor --verify` (2026-08-28 03:12) against the 2026-08-26 13:02 baseline: "Nothing was removed. All changes are additions."** lists 27, property tags 340, statuses 38 unchanged; preset_items 16 → **118** (= 45 pre-existing across the 7 old folders, DEFAULT 17 including Basem's new `Preset Template`, + our 73); preset_folders 0 → 19 (capture fix; 7 old + our 12); sequences 0 → 5 (capture fix, Basem's); siftmap_presets 22 → 49 (our 24 + the smoke + Basem's two newer saved filters). **One addition is NOT ours and is flagged, not absorbed: phone tags 79 → 89** (`Rel4.4`, `Rel4.5`, `Rel6.1-3`, `Rel7.1-3`, `Rel8.1`, `Rel9.1`), appearing between the 01:51 mid-build capture (79) and 03:16. Those are DataSift's auto-created relative-phone tags from a skip trace or phone-tag upload that ran on the account in that window; the builder never leaves the Records filter panel and has no path to `/tags/phone`. `output/dpd_baseline_current.json` is the state of record; `output/dpd_baseline.json` is still the 08-26 reference (the doctor keeps a dated copy before any replacement).

### DC first-to-market — `output/dpd_dc_ftm_investigation.md`

Read-only probes of every source; no scraper, no sign-up. The finding that changes the picture: **DC's Integrated Tax System Public Extract is a free, daily, 221,466-parcel tax roll** (ArcGIS REST + CSV, `ITSPE_08172026/FeatureServer/0`) with `OWNERNAME`, owner mailing `ADDRESS1/2` + `CITYSTZIP`, `HSTDCODE` (homestead — null is an absentee proxy), `PROPTYPE`, sale price/date, and per-parcel balances by half-year and prior year with `PY*TXSALE` tax-sale flags. Single-family 94,534; single-family with an unpaid first-half 2026 bill (`CY1BAL > 0`, due 03-31) **3,619**. `TOTBALAMT > 0` (180K) is NOT delinquency — it includes the second-half bill not yet due. `DELCODE` (Y/N, 5,547) is not in the published data dictionary and its Y rows sampled with zero balances: confirm before building on it. Also free and daily: **Vacant Property** (9,242) and **Vacant and Blighted** (2,396) ITSPE extracts, **Vacant and Blighted Building Addresses** (2,470), **Beneficial Owners** (490,017 — LLC unmasking), Certificate of Occupancy (81,475 with owner + mailing). The **OTR 2026 tax lien sale list is a public PDF** (1,279 parcels, updated 8/18, parses with `pdfminer.six`).

**Sign-ups, in the order that pays:** (1) **DC Recorder of Deeds** `washington.dc.publicsearch.us/register` — free self-service, search free, images $4/doc; the lien & deed index is the only route to HOA Lien / Other Lien / NOD / lis pendens, i.e. the signals behind 9 of DC's blocked P1 rows. (2) **A real 2Captcha key** — the DC Superior Court **Tyler Portal** (`portal-dc.tylertech.cloud`, Civil incl. Landlord & Tenant, Tax, **Probate**, anonymous for a person) throws an AWS WAF CAPTCHA at automation, and eAccess (Criminal/DV only) has its own; `CAPTCHA_API_KEY` is still the literal placeholder. (3) DOB **eRecords** is a sign-in wall; Open Data has no code-violation dataset (permits and CofO only); the vacant/blighted registry is the free stand-in. **Divorce is unsourced** (no DC office row; MD Cases covers MD only).

**THE MDDC PIPELINE DOES NOT STAMP `FTM`.** `mddc_datasift_upload.BATCH_TAG` is `Claude first batch 8.22`, so the daily DC foreclosure-notice feed does not reach `05 FTM - CALL` / `06 FTM - MAIL`. One-line fix, deliberately not made silently — first item in the investigation's action list.

### What the next (pull) run would do — not done here, needs Basem's go

Open each saved preset in ladder order (P1 by doors/deal, then P2, then Tier 2) with `in_my_account_mode=not_in`, Select Max, Add Records to Account, tagging `Priority 1` / `Priority 2` / `Tier 2` + the stack name + `DPD pulled <YYYY-MM>`. Each pull excludes what earlier pulls added, so every record receives its best rung's tag — the playbook's own ladder semantics. A separate gated pass (`in_my_account_mode=in`, one record first, read back before/after) would tag the DC records already in the account. Volume from the not-in-account counts: P1 rows ≈ 3.4K distinct (Out-of-State dominates), P2 adds ≈ 11K (Free & Clear), Tier 2 ≈ 21K. **Until a pull runs, every one of the 73 Records presets loads empty for DC** — the structure is complete and fills the moment the entry tags land.

**Operational trap, again:** the Bash/PowerShell tools cap a command at 10 minutes. The preset build is 2–4 min per preset; run it detached (`Start-Process python … -RedirectStandardOutput`) and watch the log. And two processes doing a FRESH login in the same second collide on `datasift_cookies.tmp → .json` (`WinError 32`) — start the second only after the first has saved cookies.
