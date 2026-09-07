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

# SiftMap sold property tagging (imports + tags sold properties, monthly)
# ALWAYS dry-run first: with no --counties this covers all 14 jurisdictions, and a
# live run IMPORTS every sold property in each county-month (~7.9K/month across 14).
python src/main.py manage-sold --dry-run                          # counts only, writes nothing
python src/main.py manage-sold --counties Montgomery --dry-run     # one county, prove it resolves
python src/main.py manage-sold                                    # live, last month, all 14
python src/main.py manage-sold --counties "Baltimore County,Fairfax" --min-sale-price 5000
python src/main.py manage-sold --sold-account-scope in            # tag held records only, import nothing
python src/main.py manage-sold --counties Knox,Blount             # the legacy TN footprint

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


---

## Where the details live

This file is instructions plus current state. The full build history — every hard-won API
contract, every trap, every measured number — moved to `docs/history/` on 2026-08-28. Nothing was
deleted; `docs/history/_CLAUDE-snapshot-2026-08-28.md` is the complete pre-split file.

**Read the relevant history file before touching one of these pipelines.** The traps recorded
there are the kind that fail silently.

| File | Covers |
|---|---|
| [docs/history/tn-ftm-pipeline.md](docs/history/tn-ftm-pipeline.md) | TN scheduled first-to-market pull (Fly `siftstack-ftm`), egress/Turnstile gating, backfill, Scrapfly backend, foreclosure master list, courthouse photo import, Dropbox watch, retired notice screenshots |
| [docs/history/mddc-va-md.md](docs/history/mddc-va-md.md) | MDDC Trustee's Sale, VA Public Notice (`publicnoticevirginia.com`), MD Probates + MD Legal Notices (Register of Wills), Land Records/SDAT lookup, the 2026-08-26/27 extraction audit, the external MD Cases task |
| [docs/history/doors-per-deal.md](docs/history/doors-per-deal.md) | The DPD account build end to end: SiftMap filter contract, tier configs, the 73 Records presets, the DC one-shot, the FTM coverage matrix, the market research tables, and the 2026-08-26 inverted-preset post-mortem |
| [docs/history/prospecting.md](docs/history/prospecting.md) | Obituary opportunity ranking, the obituary deep-prospecting batch + reload, Deep Prospecting v5 (SmartSkip) and the retired v4/Enformion path |
| [docs/history/deal-analysis.md](docs/history/deal-analysis.md) | Comp package engine, dispo stack, post-walkthrough package, locked master material list / SKU rehab engine, the lender package |
| [docs/history/soi-and-knox.md](docs/history/soi-and-knox.md) | Knox first-to-market pull, sphere-of-influence pipeline (Columbus OH), account-wide enrichment/scoring audit, phone validator multi-contact detection |
| [docs/history/sms-and-coaching.md](docs/history/sms-and-coaching.md) | Two-way SMS agent (Fly, smrtPhone transports, autonomy ladder), call coaching engine |

Also: `docs/api/` holds the per-vendor API notes, `docs/setup/` the onboarding guides,
`tools/manual/` the headed-browser and one-off dev harnesses.

## Active pipelines — current state

Each line is the state as of 2026-08-28; the reasoning is in the linked history file.

- **TN FTM scrape** — LIVE on Fly as `siftstack-ftm`, daily 06:30 America/New_York with
  `--commit`. Backfill complete (1,226 records, 12 months). The `county` stage still skips in the
  container. Zero notices is a FAILURE, not a quiet day. → [history](docs/history/tn-ftm-pipeline.md)
- **MDDC Trustee's Sale** — saved search 41 is GONE from the live site; default is now `["43"]`.
  Always run `--list-searches` first. `BATCH_TAG` now stamps `FTM` (changed 2026-08-28, not yet
  run; the next upload lands in the FTM lane; the browser pipeline imports the same constant).
  **`notice_id` question SETTLED 2026-09-03:** the Firecrawl-captured grid HTML carries NO id at
  all (the View buttons' `Details.aspx?SID&ID` onclick is attached by client-side JS that the
  Firecrawl capture misses — 0 of 94 rows had one), so id-first dedup never fires on a pull CSV
  and `mddc_fetch_full_text.py` (Scrapfly, fetch-by-id) has nothing to key on — and its asp=True
  mechanism does NOT clear the Details.aspx gate anyway (`--test-id` live: `gate_not_cleared`,
  Cloudflare Turnstile). The working path is `mddc_fetch_full_text_browser.py`: Playwright
  settles the grid (ids then present on every row, 94/94), navigates each Details.aspx directly
  (first nav per session bounces back to Search.aspx — retry), solves the Turnstile via 2Captcha
  (sitekey `0x4AAAAAADs-0gpXV1IADMoy`, same `btnViewNotice` shape as VA), and re-mines
  auction_date with the VA parser's deed-date guards — the MDDC snippet parser has none and
  emitted a 2024 "auction date" that was a deed recording date. Dropped rows (off-type/out-of-footprint, incl. the
  `order_nisi` reclassifications) now land in `<out>_dropped.csv`; review it, don't trust the
  count. `--hide-read-notices` defaults OFF as of 2026-08-31. → [history](docs/history/mddc-va-md.md)
- **VA Public Notice** — works unauthenticated via the Popular Searches widget, **one county per
  Firecrawl call with adaptive date-window splitting** (multi-county selection cancels its own
  postbacks; Fairfax + 60 days exceeds Firecrawl's budget). Pagination was broken until 2026-08-28
  (wrong grid id) — every earlier run was page 1 only. `--max-pages` cannot exceed **8**
  (Firecrawl caps actions at 50 and total wait at 60s; a higher value 400s and the script
  misreads it as a budget problem). `--full-text` WORKS as of 2026-08-31 — `CAPTCHA_API_KEY` is
  real and 2Captcha solves fine; the earlier blocker was Scrapfly QUOTA EXHAUSTION, which was
  masked as `unknown_page_state` because Scrapfly reports quota errors in `scrape_result["error"]`
  while returning empty content. Now surfaced, and `scrapfly_preflight()` aborts before the loop
  so a dead quota no longer bills one 2Captcha solve per row. → [history](docs/history/mddc-va-md.md)
- **MD Probates + MD Legal Notices** — `--date` exact-date pulls, PRs split into columns, co-PR and
  foreign-PR notices parsed (the latter state the MD property), SDAT checked BY STREET ADDRESS with a
  full-name owner match (HIGH/MEDIUM/LOW; LOW = sold since the deed). **Checkpoint HEALTHY as of
  2026-09-06:** per-county (`county_cutoffs` schema, never-backwards, `--overlap-days 2`); the
  contaminated Calvert-only global checkpoint is archived at
  `output/_archive/md_row_*_LEGACY_calvert_only_20260906.json`. All 3 footprint counties backfilled
  30 days to 09/04/2026 (450 estates; addresses filled 449/450 — 177 found, 94 HIGH / 83 MEDIUM)
  and the daily runner's MD-RoW leg now runs checkpoint-driven `--commit` + `--dump-json` (commit =
  local checkpoint/ledger only; review-before-DataSift unchanged). **Backfill trap:** the Estate
  Search pagination call tops out around 12 result pages (Firecrawl's 60s total-wait cap — each page
  adds a 4.5s wait), so a wide `--since/--until` window must be split; `--max-pages` past 12 causes
  a Firecrawl 400, and the truncation warning's "raise --max-pages" advice cannot work past it.
  Land Records/SDAT lookups fail transiently in batches (Firecrawl 500s); failed records stay
  unchecked and any later fill/daily run resumes them. → [history](docs/history/mddc-va-md.md)
- **Doors-Per-Deal account build** — all 73 Records presets built and verified (0 defects), 12
  folders, 6 entry tags, 24 DC SiftMap presets saved. ZERO records pulled in, so every preset loads
  empty for DC until the pull runs on explicit go. → [history](docs/history/doors-per-deal.md)
- **FTM DP batch (lane `05 FTM - CALL / FTM - 01 Skipped No Numbers`)** — **COMPLETE
  2026-09-01. All 25 records pushed and QA-clean (25/25 first audit, no repair needed).**
  Ty's escalation applied: skip-traced + no numbers -> don't re-skip, keep mailing, deep
  prospect. Cohort pulled LIVE from the stored preset by `src/scripts/dpd_ftm_dp_candidates.py`
  (new: preset -> prep-ready CSV; generalize of `resolve_lanes` + `to_query` + the
  account-total guard; resumable detail cache). All 25 were Register of Wills probates, so
  the hybrid research gate researched everything. Total spend **~$11.15**: SmartSkip $3.75
  (order `6a9735af17ff2290a0b8044c`), Tracerfy $0.10, Trestle ~$7.28 (485 numbers — 191
  Dial First), research LLM/Firecrawl small. Verdicts: 1 owner-died (7103 Minna Rd, the
  probe), 3 owner-alive, 4 relative-died (two look like the probate decedents behind
  PR-owned records), 17 unresolved (owners are PRs, living). 4 records got zero numbers
  (`DP No Numbers` tagged, note posted). 7819 Chestnut Ave — the known duplicate-address
  record — resolved correctly on owner Gail Huber.
  **The batch now has an FTM mode** (`research --presume-alive-without-signal`): records with
  no deceased signal (Probate/Obituary list, deceased-ish tag, obituary date, PR) skip the
  research spend, get `VERDICT_PRESUMED` (score/build then load the OWNER'S own numbers),
  tag `DP Owner Presumed Alive` + group/reload `presumed_alive`, and a live-owner note.
  Unused on this all-probate cohort; built for foreclosure-heavy FTM cohorts.
  **The Anthropic dead-LLM trap replayed mid-research** (credit balance exhausted): the
  post-obituary-batch guard held — 6 records were NOT cached as false unresolved and
  resolved on re-run. Basem enabled auto-reload; the charge took ~10 min to settle before
  the API accepted calls again. Expect that lag.
- **Obituary DP batch** — **COMPLETE 2026-09-01. 618 of 619 records live on the account**,
  every one read back (phones N/N, REL N/N, tags, owner unchanged, note present). Total spend
  **$231.54**: SmartSkip $82.50 (550 entities, order `6a95dda1713541767bd1d0e8`), Tracerfy
  $2.92, Trestle $146.12 (9,741 numbers). Research: 299 owner-died, 215 unresolved, 83
  relative-died, **22 owner-ALIVE spouse-trap catches**. One record left unpushed:
  `8429 Farrell Dr 20815` — blank owner (trust title) against two duplicate account records,
  so the resolver refused to guess; needs a manual uuid.
  **REL slots went 7 → 15** (`MAX_REL_SLOTS`); `schema --commit` created REL8–15 (32 custom
  fields, group 14 "Custom Fields 1"). 221 of 619 records (36%) actually use a slot past REL7.
  **Four traps found live, all fixed in `obituary_dp_batch.py`:**
  1. **A dead LLM looked exactly like "no obituary found."** `llm_client._chat_anthropic`
     swallows every exception and returns `None` (llm_client.py:94), so `_llm_match`'s own
     `except` never fired and the don't-cache-an-error guard never saw an error. An exhausted
     Anthropic credit balance froze **52 records as false `unresolved`** before it was caught
     (re-run after top-up: the same records resolve to "owner did die" with obituaries).
     `_llm_match` now treats a `None` as an error (a real non-match returns `match: false`),
     and `--max-consecutive-errors` (default 8) aborts instead of grinding for hours.
  2. **DataSift silently caps an owner at 30 phones.** It answers the upsert `200` and echoes
     every number in `added`, then stores 30. 33 of 619 records exceeded it. `OWNER_PHONE_CAP`
     trims in REL order and the note names what missed the dial list; the numbers are NOT lost
     (they still land in the `REL{n}: Phone` fields).
  3. **Duplicate relatives ate REL slots.** SmartSkip returns some people on multiple rows;
     nothing deduped them, so **101 of 619 records had the same person in two REL fields**.
     `_dedupe_relatives` merges them (phones combined, signer identity preserved) — 335 merged.
     Side effect: Tracerfy gap-fill went from 4 signers filled to 35.
  4. **The DP note never reached the message board.** `add-notes` writes the `notes` FIELD,
     not the board a caller reads, and it answers 204 -- so the old code's `message/`
     fallback (gated on 404/405) never fired and **all 619 boards were empty**. Pinning is a
     TWO-step contract, and the two obvious ways both lie: `POST .../message/` with
     `{"pinned": true}` returns 201 and stores `pinned: false`, and
     `PATCH .../message/{uuid}/ {"pinned": true}` returns **200 while changing nothing**.
     Only `POST /api/internal/property/{uuid}/message/{message_uuid}/pin/` (204) pins.
     `add-notes` is no longer called at all; a re-push deletes any earlier DP board so a
     record never ends up with two; and `ok` in the read-back now REQUIRES a pinned message.
  5. **Duplicate PROPERTY records in the account.** 17 addresses resolve to two records under
     different owners (7819 Chestnut Ave is on file under both Gordon Thompson and Gail Huber).
     `_resolve_uuid` now disambiguates on owner first+last, and **refuses on a blank owner** —
     an empty name matches a null `last_name` and "resolves" to a stranger's record.
  **Phone allocation rebuilt 2026-09-01** after Basem caught that the wrong numbers were
  loading. Three defects, all measured, all fixed in `cmd_build`:
  1. **One cap did two jobs.** `--max-phones-per-rel` gated the REL custom fields AND the
     owner's phone list. The fields really are `Phone 1..3`; the phone list has no limit but
     the account's 30-per-owner ceiling. Split into `--max-rel-field-phones` (3) and an
     unbounded phone list -- **1,184 numbers now load as `Rel{N}.4`+** (the account already
     carried `Rel4.4`/`Rel4.5` from the IDI imports, so the shape was always there).
  2. **Phone choice was TIER-BLIND.** `_phone_sort_key` orders on line type and runs at RANK
     time, before Trestle scoring exists, so a Dial First landline lost to a Dial Fourth
     mobile on position alone. **538 Dial First numbers were parked; now 5** (all on records
     whose 30 slots are full of better-or-equal numbers). Sorting by tier must happen in
     `cmd_build`, never in `cmd_rank`.
  3. **The 30-cap trimmed by position, and a LIVING owner's numbers were appended last.**
     Two owner-alive records lost the owner's numbers entirely, including a Dial First
     (14029 Breeders Cup Dr) -- on a record where the owner is alive and is the person to
     call. Replaced with two-pass allocation (Basem's choice): pass 1 gives every contact its
     best number, owner first, so no signer is silenced; pass 2 fills the rest by tier alone.
     All 24 owner-alive records now carry the owner's number at position 0.
  **The hidden ceiling under all of it:** `cmd_score` priced only `max_phones_per_rel + 1`
  numbers per relative, so the 4th+ could never load however the build caps were set -- an
  unscored number fails `_keep_number`. It now prices every number on a kept contact
  (`--max-score-per-rel 0`); 559 more cost $8.38. Phones loaded 9,510 -> 10,360.
  **Two re-push traps found the same day:** the upsert SKIPPED numbers already on the owner,
  so a re-push could never CORRECT a tag (fatal once tier ordering reshuffles the slots) --
  it now sends the full list; and a record already holding 30 phones silently refuses every
  new one, so superseded numbers must come OFF first. Removal is
  `POST /api/internal/owner/{uuid}/remove-phones/ {"phones": [...]}` (200, returns `removed`),
  **guarded to numbers carrying our own `Rel{N}.{M}`/`Owner.{M}` tag and absent from the
  current plan** -- verified zero third-party numbers on these owners, which follows from
  both batches being selected on having no phones at all. `PATCH /owner/{uuid}/ {"phones":...}`
  replaces the list wholesale and is NOT used: it would delete numbers we did not write.
  **QA:** `src/scripts/obituary_dp_qa.py` audits every record against the LIVE account
  (pinned message, phones, `Rel{N}.{M}` tags, Trestle tier tags, REL fields, property tags);
  read-only by default, `--repair` re-posts only the gaps. Final state **616 -> 618 of 619
  clean**, the one exception being 8429 Farrell Dr. Two things it taught: phone TAGS fail to
  land on ~3.5% of records on the first upsert (a plain re-upsert fixes them, so the repair
  pass is not optional), and tier `Unknown` is deliberately never written as a tag -- auditing
  for it reports correct behaviour as a defect.
  Also: **SmartSkip dedupes its input by NAME, not property** — 9 of 628 rows returned nothing
  of their own because the same owner holds two properties. Their heir graphs are identical
  people, so the result can be fanned across same-owner records for free (not done).
  The note was rewritten from one `"  ||  "` paragraph to line-broken blocks; **DataSift
  preserves the newlines** (verified live, 37 of them). → [history](docs/history/prospecting.md)
- **SMS agent** — deployed on Fly, API transport verified. Autonomy ladder controlled by
  `SMS_AGENT_PHASE`. → [history](docs/history/sms-and-coaching.md)
- **manage-sold (SiftMap sold sweep)** — CODE DONE + SUPPRESSION WIRED 2026-08-31. **No sweep
  has run; no records imported.** Ported from Ty's Knox/Blount flow to all 14 jurisdictions:
  cadence, per-month `Sold YYYY-MM` tags, `--min-sale-price 1000` and the record-importing
  behaviour are his, unchanged. Three things are not: counties resolve through
  `dpd.jurisdictions` and **raise** instead of falling back to Knox's `47093` (the old
  `.get(county, "47093")` silently queried Knoxville for every MD/DC/VA county); the tag is
  `Recently Sold`, not `Sold`, which is a status here; and a per-(fips, month) checkpoint lets a
  14-county run resume. Offline verification passes
  (`python tools/manual/test_manage_sold_url.py`).
  **Done live:** the `Recently Sold` property tag exists (`dpd_tags_create.py --names`, now
  supports arbitrary names), and the **`Sold Property Cleanup` sequence is live and Active** in
  the Transactions folder — trigger *tag added* → condition `Recently Sold` → **Change Status to
  `Already Sold`** + Delete All Property Tasks + Clear Assignee. Because every preset excludes
  `Already Sold` via `DEAD_STATUSES`, suppression needs no preset edit. `Already Sold` IS
  selectable in the sequence action picker, so `SOLD_STATUS_FALLBACK` was not needed.
  **Two gaps, stated rather than hidden:** (1) `Remove Property Lists` never landed — the React
  DnD reported "Added action" twice while leaving an empty drop zone, so the sequence has 3 of 4
  actions. Status suppression is unaffected; removing lists is hygiene and can be added by hand
  via "Make Changes". (2) **`dpd_doctor` under-reports sequences** — it read `sequences
  unchanged (5)` after the sequence was demonstrably created, because it does not expand the
  folder chevrons on `/sequences`. Do not trust `--verify` for sequences; search `/sequences`
  by name instead. The earlier "5 sequences, all Basem's" baseline is suspect for the same
  reason.
  **Traps learned:** the sequence condition input is an autocomplete over EXISTING tags — typing
  a tag that does not exist leaves the field empty and the save is rejected, so the tag must be
  created first. And DataSift confirms a save with a MODAL while staying on
  `/sequences/new/actions`; the old code both claimed success unconditionally and tested the URL
  with `"/new" not in url or "/sequences/" in url`, which is always true there. Both fixed —
  the save now reads the confirmation modal and the validation banner, and reports which action
  cards actually landed.
  **Dry run PASSED live 2026-08-31** (`--counties Montgomery --months-back 1 --dry-run`):
  resolved to fips 24031, window 2026-07-01..07-31, **992 properties matched**, nothing
  imported or tagged, no checkpoint written, exit 0. The screenshot
  (`datasift_siftmap_filtered_24031_2026-07.png`) shows Potomac / Montgomery Village /
  Poolesville and rows in Cabin John MD 20818, Beallsville MD 20839 — Montgomery County, NOT
  Knoxville. 992 against the ~1,152/month Market Finder predicts is good corroboration. Rows
  include Condominium Unit and Townhouse, confirming there is no single-family filter: the
  sweep takes every property type. One defect found and fixed by this run — county success was
  keyed on `records > 0`, so every dry run reported "No counties processed successfully" and
  exited 1; it now keys on the month results, and the offline suite has a regression check.
  **Next:** dry-run all 14 headless, then a live sweep on the smallest county.
- **Sold backlog suppression (`src/scripts/sold_backlog.py`) — BUILT, AUDITED, PARKED
  2026-08-31 pending a write path.** The sweep above catches what sells from now on; this
  catches what already sold while we kept marketing it. Audit (read-only, from the 26,645-record
  hydration) selects **1,559 records** that sold on/after 2024-08-31 and are STILL in an active
  status: 1,123 already mailed, all of them on a list, 643 sitting in "No Answer" on the dialer.
  MD 941 / VA 488 / DC 127. Price bands $100k+ 905, blank-or-$0 635, $20k-100k 11, under-$20k 8.
  (Was 1,668 until a code review caught the status bug below.)
  Reviewable CSV at `output/sold_backlog_<ts>.csv`, sorted review-first (blank/$0 price and 2024
  sales lead, being the likeliest false positives — `last_sold` means the property changed hands,
  which can equally mean the CURRENT owner just bought it). Every row carries `prior_status`;
  every write run snapshots prior tags to a backup with `--undo` to restore.
  **Browser path VALIDATED except the tag click (2026-08-31).** `src/scripts/sold_backlog_upload.py`
  drives Upload File -> **Update Data -> "Tagging existing properties"**, which is the right
  surface: the wizard's own Data Requirements for that option are Property Street / City / State /
  ZIP Code and NOTHING else, so the file carries no owner column and cannot overwrite PR/DM
  mapping, and it updates existing records rather than creating any. Proven in a dry run on one
  row: the option sticks, the file uploads, **all four address columns auto-map with the real
  values**, and the Review step is reached. Nothing was ever submitted.
  **The one blocker is applying the tag**, and both routes are unproven:
  (a) the Add-tags step's input refuses automation — Playwright `.type()` leaves it empty and the
  dropdown unfiltered, and React's native-value-setter pattern sets the DOM value (readback
  confirms `Recently Sold`) but React clears it and never filters. (b) The CSV's Tags column
  **does not auto-map** at step 4 (the known Tags/Lists behaviour), so mapping it needs the React
  DnD.
  **Three false successes were caught by screenshots in this flow, all now fixed to fail loudly:**
  the tag helper reported success on an empty input; the chip check matched `Recently Sold` in the
  open SUGGESTION LIST rather than a committed chip (it is a real account tag now, so it appears
  as an option); and the script would have proceeded to Review and Finish having tagged nothing.
  It now refuses to continue unless the tag is verifiably committed.
  **RUNBOOK — one human pass, ~2 minutes, no code needed.** This is a one-off backlog clear, not a
  recurring job, so hand-running the wizard is cheaper than automating two hostile React controls:
  Records -> Upload File -> **Update Data** -> "Tagging existing properties" -> Next ->
  **Add tags: type `Recently Sold` and click Add** -> Next -> upload
  `output/sold_backlog_wizard_20260831T163705.csv` -> Next -> the four address columns auto-map; **drag the
  `Tags` column onto the `Tags` target** to also land each row's `Sold YYYY-MM` -> Next -> Finish.
  Then confirm on one record that the status flipped to `Already Sold` (the sequence does that) and
  that it left two presets.
  **Why the API route is parked, not disproven:** the one-record write probe was stopped by the local permission classifier, so
  **Code review 2026-08-31 caught a silent selection bug:** the account stores some statuses as
  display labels ("No Answer") and others snake_cased ("not_interested", "under_contract",
  "dnc"), while `preset_spec.DEAD_STATUSES` holds only the display forms. A plain lowercase
  compare therefore MISSED the snake_cased dead statuses, and 109 already-dead records --
  107 of them `not_interested` -- were selected as "still active" and would have been re-tagged
  and flipped. `sold_backlog._status_key()` now normalises underscores and hyphens before
  comparing. Anything else comparing a live status against `DEAD_STATUSES` has the same hazard.
  **Related finding:** a record read live carried the list `Probate`, which is NOT among the 27
  lists `dpd_doctor` captured — so its LIST capture under-reports as well as its sequence capture.
  `datasift_uploader.SOLD_REMOVE_LISTS` was built from that baseline and is therefore probably
  missing real lists. Not blocking (status does the suppression, lists are hygiene), but do not
  treat those 22 names as the full set. A live sweep **imports** ~7.9K records/month across the 14 (measured from
  `output/dpd_market_finder/*.json`), so it needs an explicit go. Separately, 1,689 records
  already in the account sold in the last 24 months and are still active (1,214 already mailed,
  651 on the dialer) — now that the tag and sequence exist, stamping `Recently Sold` on those
  by address suppresses them with no browser and no imports.

## Local working files

`output/` and `logs/` are gitignored scratch. Two notes after the 2026-08-28 cleanup:

- **`output/_archive/live_account_pull.json`** (133MB, the 26,645-record account hydration) moved
  out of `output/` to keep listings readable. Scripts taking `--cache` must now be given
  `--cache "output/_archive/live_account_pull.json"` explicitly. **`live_pull.py` especially** —
  without it, it will not find the file and starts a fresh multi-hour re-pull.
  `doors_per_deal_dialer_split.py` hardcodes the old path, so move the file back for that script.
- **`data/dpd_playbook_*_shards.json` were deleted** — write-only cross-check artifacts, and
  byte-identical per state (the fetcher pulls per state, writes per county). Re-derive with
  `python src/scripts/dpd_playbook_extract.py --fips <fips> --fetch`. The parsed
  `data/dpd_playbook_<fips>.json` files, which ARE read, are untouched.

---

## DataSift API upload contract (hard-won, 2026-08)

The contract for pushing records into DataSift entirely over the API (`src/datasift_api_upload.py`).
The pull side that feeds it is in [docs/history/soi-and-knox.md](docs/history/soi-and-knox.md).

**Auth: mint the JWT, never paste one.** `POST /api/token/` with `DATASIFT_EMAIL` / `DATASIFT_PASSWORD` from `.env` returns `{access, refresh}`. The uploader mints on start and re-mints every 30 minutes so long runs cannot die on expiry. **The Open API key cannot do this job** — custom fields do not exist anywhere in its 93-route surface and every write 401s. The minted user JWT reaches `/api/internal/` where they do.

Four traps, each of which fails silently or cryptically:
1. **Tags must be an ARRAY.** A comma string creates one tag literally named `"Courthouse Data, code_violation, Knox"`.
2. **A select field's value must be the OPTION'S UUID, not its label.** `"LEN"` returns `{"non_field_errors": ["'LEN' is not a valid UUID."]}`. Resolve via `custom-fields/` `options[]`.
3. **Entity owners cannot have a blank `first_name`** (the API rejects it). Send the business as `company` and OMIT the person keys; omitting a key is not the same as sending `""`.
4. **`notes` on the property payload returns 200 and is discarded.** Post it separately.

`POST /property/` is **upsert by address**, so re-runs never duplicate, and **lists accumulate** rather than overwrite (verified: a record came back with both new lists plus four it already had). Custom fields go to `PATCH /api/internal/property/{uuid}/custom-field/update-values/` with `[{"field_uuid": ..., "value": ...}]`. Creating a `select` custom field REQUIRES its options in the same POST.

**Always upload one record and read it back before releasing the file.** That single habit caught the tag format, the entity-owner rejection, the option-UUID requirement and a list-name mismatch that would have silently attached nothing for 2,512 of 2,573 records.

### SiftLine boards over the API (learned 2026-09-03, seller re-qualify batch)

- **The board API is fully readable AND writable** with the minted user JWT
  (discovered by capturing the SPA's own calls; `src/scripts/seller_requalify.py` is the
  working reference): `GET /api/internal/siftline/board/` (boards),
  `GET .../board/{board}/column/` (phases), `GET .../board/column/{col}/card/` (cards,
  paginated). Create a card with `POST .../column/{col}/card/ {"prop": "<prop-uuid>"}` —
  OPTIONS advertises `prop_uuid` but live the request 400s without `prop`. Move a card
  with `PATCH .../column/{col}/card/{card}/ {"column": "<new-col-uuid>"}`; delete with
  `DELETE` on the same route (204). The Lead Management board is
  `8bbee183-27b4-4734-870f-c9eebd50c9d0`, 12 columns.
- **`PATCH /property/{uuid}/ {"tags": [...]}` is MERGE-ONLY.** It adds missing names
  (auto-creating unknown tags) but NEVER removes — a read-modify-write with a name
  dropped returns 200 and changes nothing. Removal is
  `POST /api/internal/property/{uuid}/remove-tags/ {"tags": [...]}` (200, echoes
  `removed_tags`). The obituary batch's "read-modify-write of the FULL set" comment is
  therefore only half true: correct for adds, wrong for removes.
- **A users endpoint DOES exist:** `GET /api/internal/account/user/?limit=999` (the
  board UI calls it) — supersedes "no users/team endpoint exists" below for lookups.
- `GET /api/internal/activity/` ignores a `?prop=` filter and returns account-wide
  bulk activity — useless for per-record history (e.g. recovering a prior assignee).

### Assignees over the API (learned 2026-09-01, lane rebalance)

- **`assigned_to` is readable, filterable and WRITABLE over the internal API.** The
  "internal API 403s on writes" note in `doors_per_deal_batch_tag_upload.py` is outdated —
  `PATCH /api/internal/property/{uuid}/ {"assigned_to": "<user-uuid>"}` returns 200 and
  STORES (proven by probe + read-back + restore, then 1,465 live writes). `null` clears it.
  The browser bulk-assign path (`doors_per_deal_bulk_assign.py`) is no longer the only way.
- **Filter/count by assignee:** the property query accepts `assigned_to` as a bare uuid
  STRING (`{"must": {"assigned_to": "<uuid>"}}`); a list 400s with "Must be a valid UUID."
  Unknown keys 400 ("Filter can't be empty."), they are not silently ignored.
- **No users/team endpoint exists.** uuid→name comes from the rendered UI only:
  `src/scripts/dpd_assignee_name_map.py` (direct route `/records/properties/{uuid}/details`;
  the assignee is the topmost text in the x 650–980 / y 95–145 band, excluding
  `SelectOption|Placeholder` classes). The 7-user map lives in auto-memory and in
  `output/dpd_assignee_name_map.json`. `doors_per_deal_resolve_assignees.KNOWN_USERS` is
  STALE (missing Ahmed Hesham) and its Records-search navigation lands on Owner Details,
  which has no assignee control — prefer the name-map script.
- **A stored preset's `filters` is NOT a valid query payload.** The store keeps counties as
  `{uuid,title,isNegative}` objects; the query endpoint wants bare strings and 400s
  otherwise. `dpd_lane_assignee_report.to_query()` translates (negative counties move to
  `must_not`).
- **Filtered counts come off a search index that lags writes by a minute or two.** A verify
  run straight after a bulk write reports phantom "unassigned" records; per-record
  read-back is the truth, or wait and recount. The Records UI shows NO record total — the
  paginator's "of N" is PAGES at 10 rows each, and the default "Clean" tab hides
  Incomplete records (compare against the "All" tab only).
- **Detail 404s can be real deletions:** 4 of 1,466 lane records 404'd mid-run and left the
  lane listing entirely — they were deleted from the account, not a transient flake. And
  `LiveApi._mint` has no retry (a gateway 502 at token mint killed a run at step zero);
  `dpd_lane_rebalance.LiveApi` subclasses it with backoff.
- **The lane split itself:** `src/scripts/dpd_lane_rebalance.py` (plan/probe/commit/verify/
  undo; backups in `output/dpd_lane_rebalance_backup_*.json`, leveling moves in
  `output/dpd_lane_rebalance_moves_*.json`). Final state 2026-09-01: both ready-to-call
  lanes fully assigned across the 5 dialers, per-lane spread ≤1 (Hottest 207×4 + 208,
  FTM 114×4 + 115).


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


## DataSift.ai (REISift) Integration

DataSift.ai (formerly REISift) is the CRM where scraped records land for niche sequential marketing campaigns. There is **no REST API** — upload is via Playwright browser automation of the web UI.

**Domain:** `app.reisift.io` (NOT `app.datasift.ai`). API at `apiv2.reisift.io`.

**apiv2 JWT (shared with the Deal Room project):** any script hitting `apiv2.reisift.io` reads the shared auth store at `Deal Room Coaching Call/_api/clients/config/reisift_auth.json` (`datasift-admin` = staff ty+1, ~48h access token; NEVER hardcode a Bearer in SiftStack). Refresh: app.reisift.io DevTools -> Copy as cURL -> `python _api/clients/reisift_auth.py add datasift-admin <jwt>` (run with `PYTHONIOENCODING=utf-8`; the checkmark-glyph crash after "saved account" is cosmetic, the save succeeded). Then re-impersonate before client-account calls. Last refresh 2026-07-21, exp 2026-07-23 19:41 UTC.

### Key Files
- `src/datasift_formatter.py` — Transforms `NoticeData` → DataSift CSV (42 columns)
- `src/datasift_uploader.py` — Playwright login + upload wizard + enrich + skip trace + preset management + sequence builder + SiftMap sold workflow
- `tools/manual/test_datasift_upload.py` — Headed browser test (upload + enrich + skip trace)
- `tools/manual/test_manage_presets.py` — Headed browser test (preset discovery + sold exclusion + sequence creation)
- `tools/manual/test_manage_sold.py` — Headed browser test (SiftMap sold property tagging; `--dry-run` to count without writing)
- `tools/manual/test_manage_sold_url.py` — Offline check of county resolution + URL construction (no browser, no account). Run after any change to the sold sweep.

### CSV Column Structure (42 columns)
- **Core auto-mapped (11):** Property Street/City/State/ZIP, Owner First/Last Name, Mailing Street/City/State/ZIP, Tags
- **Lists + Notes (2):** Lists (for niche sequential), Notes (contextual per notice type)
- **Built-in fields (13):** Estimated Value, MSL Status, Last Sale Date/Price, Equity Percentage, Tax Deliquent Value, Tax Delinquent Year, Tax Auction Date, Foreclosure Date, Probate Open Date, Personal Representative, Parcel ID, Structure Type, Year Built, Living SqFt, Bedrooms, Bathrooms, Lot (Acres)
- **Custom fields (16):** Notice Type, County, Date Added, Owner Deceased, Date of Death, Decedent Name, Decision Maker, DM Relationship, DM Confidence, DM 2/3 Name/Relationship, Obituary URL, Source URL, Notice Screenshot

### Niche Sequential Marketing
DataSift's niche sequential system uses filter presets to guide records through SMS → Call → Mail → Deep Prospecting phases. Two preset folders: "00 Niche Sequential Marketing" (12 presets, courthouse data) and "01. Bulk Sequential Marketing" (9 presets, bulk data). All 21 presets exclude Sold status (build 1.0.23). A "Sold Property Cleanup" sequence in the Transactions folder auto-fires on the recently-sold tag to change status, remove from lists, clear tasks, and clear assignee.

> **This paragraph describes Ty's TN setup, not necessarily this account.** The `dpd_doctor` baseline reads no `Sold` among this account's 340 property tags and no "Sold Property Cleanup" among its 5 sequences — both artifacts the code would have created — so treat the 21 presets and the sequence as unverified here until `dpd_doctor.py` says otherwise. This account's own suppression runs through the 73 DPD presets, which exclude the `Sold` / `Already Sold` **statuses** via `preset_spec.DEAD_STATUSES`. That is why the sold sweep stamps `Recently Sold` (a tag) and the sequence maps it onto `Already Sold` (a status): a same-named tag would split suppression across two vocabularies.

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
- "Sold Property Cleanup" sequence (build 1.0.23): Trigger (Property Tags Added) → Condition (`Recently Sold`, from `datasift_uploader.RECENTLY_SOLD_TAG`) → Actions (Status→`Already Sold` per `SOLD_STATUS`, Remove Lists, Clear Tasks, Clear Assignee). **Not present on this account as of the last `dpd_doctor` capture** — build it before running the sold sweep, or the tag lands with nothing listening. Confirm `Already Sold` is offered by the sequence *action* status picker: the Records *filter* picker exposes only 19 of 38 statuses, and `SOLD_STATUS_FALLBACK` exists for that case.

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

- **Main counties (NARROWED 2026-09-01, from 14 to 6 — these are the ONLY jurisdictions in scope):** Washington DC; Montgomery, Anne Arundel, and Frederick in Maryland; Fairfax and Arlington in Virginia. Dropped: Carroll, Calvert, Charles, Baltimore County (MD); Prince William, Stafford, Spotsylvania, Fredericksburg city (VA). Coverage: DC + the 3 MD counties come through the MDDC Trustee's Sale pipeline (`src/scripts/mddc_trustee_sale_pull.py`) — its `DEFAULT_COUNTIES` still holds the old 7-MD+DC set and should be narrowed before the next pull. The 2 VA counties come through `src/scripts/va_trustee_sale_pull.py` against `publicnoticevirginia.com` — likewise still defaulting to the old 5-county set. **Other artifacts still wired to the old footprint** (do not treat their defaults as the footprint): `manage-sold` with no `--counties` sweeps all 14 — pass the 6 explicitly; the 30 DPD MAIL presets carry 15 counties and CALL/Deep/Reactivation carry the core 9 — re-narrowing them is a live-account preset edit that needs an explicit go.
- **Daily summary channel:** WhatsApp. Note: not currently a wired notification transport — only `SLACK_WEBHOOK_URL` (Slack or Discord-compatible webhook) exists in `notify_slack` today.
- **Preferred run time:** 06:30 America/New_York.
- **Dispositions:** type-based DataSift lists (the auto-created per-notice-type lists — Foreclosure, Probate, Tax Sale, etc. — rather than one consolidated dispo list).


## Where the DPD code stands

Full build narrative, including every panel trap and the inverted-preset post-mortem: [docs/history/doors-per-deal.md](docs/history/doors-per-deal.md).

- `src/dpd/preset_spec.py` — the 73 presets as data, asserted at import (6/6/6/6/6/6/6/6/9/6/5/5).
- `src/dpd/block_map.py` — the panel mapping, the stated gaps, the block budget and its one remaining trim (`Deep - 04 Return Mail`).
- `src/scripts/dpd_filter_blocks.py` — the 144-block vocabulary dump (`output/dpd_filter_blocks.json`).
- `src/scripts/dpd_presets_create.py` — the builder, **PROVEN on all 73**: container-scoped `audit_panel`, hit-tested `_click_leaf`, section-scoped `_PICK_INPUT_JS` / `set_tokens` with one retry, the scrolled folder pick, polling `open_panel`, a pre-save panel screenshot per folder (`output/dpd_panel_*.png`), and preset deletion (`--discover-delete | --delete-junk-dry | --delete-junk [--delete-names ...]`, refuses anything not `^ZZ ` or explicitly allowlisted). Resumable; skips by name prefix.
- `src/scripts/dpd_presets_verify.py` — **Phase 7 QA for presets.** Reads every stored definition over the internal API and checks counties, suppression, statuses, counters, params and tag-uuid algebra against `preset_spec`; lists extras. Exit 0 only when all 73 are present and defect-free. **Currently exits 0.** Run it after any preset change.
- `src/scripts/dpd_doctor.py` — `capture_presets` rewritten onto the builder's panel primitives (positional per-folder read, full scroll of `PresetsBelowBody`, no-toggle re-read for a folder that is open by default); writes `preset_items`, `preset_folders`, `preset_tree`. `--verify` diff is trustworthy again.
- `src/scripts/dpd_playbook_extract.py`, `dpd_siftmap_manifest.py`, `dpd_siftmap_presets.py`, `dpd_qa_report.py` — the county-agnostic DC one-shot pipeline (see [docs/history/doors-per-deal.md](docs/history/doors-per-deal.md)).

**Stated gaps, omitted on purpose rather than approximated** (all in `block_map.GAPS`): the mail cadence's 30-day spacing (date blocks are absolute-only, and a fixed date silently hides work as time passes); the four Reactivation status-age timers (nothing filters on time-in-status); the single-family filter (would drop the 2,892 blank-`structure_type` records, and the buy box is applied at pull time instead); the recently-sold tag (does not exist on this account). Also: the Property Status picker offers 19 of 38 statuses — `DNC` and `Opt-out` are recovered through Params & Others, but `Lost Deal`, `Close Out`, `Buyer`, `Buyer Found` and `Buyer Lost` cannot be selected at all.

## Order of work (from 2026-08-28)

1. ~~Delete the SiftMap saved filter `ZZ DPD SMOKE 153311`~~ **DONE 2026-08-31, and no longer a hand
   step:** saved filters ARE deletable — the management table at `/siftmap/presets/account` (paginated,
   trash icon per row, type `DELETE FILTER` to arm the confirm) — via
   `dpd_siftmap_presets.py --delete-name` (refuses any name not starting `ZZ `). 28 saved filters remain,
   all verified present after the delete.
2. ~~Make the daily DC foreclosure feed reach the FTM lane~~ **DONE 2026-08-28 (code only):** `mddc_datasift_upload.BATCH_TAG = "FTM"`. The next MDDC upload — on Basem's go — lands in `05 FTM - CALL` / `06 FTM - MAIL`.
3. **VA pulls — FORECLOSURE LANE WORKING 2026-08-31; the other two searches parsed the wrong
   thing by design.** The old text here assumed searches 6 and 8 carry a property address. They
   do not, and no regex tuning would have found one.

   **Foreclosures (`--popular-search 4`) is the lane that works.** 103 leads in
   `output/va_foreclosures_final.csv`: 99 with a street, 0 duplicates, 0 wrong-building
   addresses. Free path, Firecrawl only. Kept for review; NOT uploaded (Basem, 2026-08-31).

   **THE DURABLE LESSON — address validation cannot catch this.** A trustee's sale notice holds
   THREE real, deliverable, USPS-confirmable addresses: the subject property (in the
   `TRUSTEE'S SALE` headline), the courthouse (mid-notice, the auction VENUE), and the trustee's
   law firm (near the end, letterhead). Smarty confirms all three, so `--standardize` cannot tell
   them apart. `parse_address` was returning the LAW FIRM. Only anchoring on the notice's own
   semantics works: `TRUSTEE_HEADLINE_RE` / `parse_trustee_headline_address()`, tried first.

   Five defects fixed in `va_trustee_sale_pull.py` (commit c5d4fd5), each measured on live data:
   - **wrong building** — law-firm address; headline anchor fixes it.
   - **plural titles missed** — `TRUSTEE'?S\s+SALE` cannot match `SUBSTITUTE TRUSTEES' SALE`
     (the apostrophe sits between the S and the space). Plural is DOMINANT: 69 of 139 real MDDC
     notices, 51 ASCII + 18 curly U+2019. Always spell both apostrophes out.
   - **unit numbers dropped** — `1476 Vineyard Ct Unit# 105XA` became `1476 Vineyard Ct`. On a
     condo that is a DIFFERENT property. A comma now wins over suffix-splitting.
   - **25% duplicate output** — foreclosure notices republish weekly by law, so dedup collapses
     foreclosures dateless on (street, city). 137 -> 103. Scoped to `notice_type ==
     "foreclosure"` ONLY: keying city-only rows collapsed Estate Claims 26 -> 7, street-bearing
     ones 26 -> 24 (all carry the courthouse in `street`). **`notice_id` update 2026-09-04:**
     the View button's onclick DOES carry `Details.aspx?SID&ID` (a real per-notice id), but a
     Firecrawl capture only sometimes includes it (JS-attached; 31/32 one run, 2/32 another) —
     and each republication carries a NEW id, so `dedupe()` deliberately checks the foreclosure
     address collapse BEFORE the id key; id-first keying would reinstate the 25% duplicates.
   - **auction dates 94% wrong** — matched `recorded on <date>`, the Deed of Trust RECORDING
     date; 17 of 18 were years in the past. Guarded by deed-context rejection plus "cannot
     predate publication".

   **`--max-pages` CEILING IS 8. Measured 2026-08-31 against live Firecrawl, two hard limits:**
   `Number of actions cannot exceed 50` (hit at 16 pages / 57 actions) and `Total wait time
   (waitFor + wait actions) cannot exceed 60 seconds` (hit at 10 pages / 39 actions — this one
   binds first). 8 pages = 33 actions = HTTP 200. Anything higher returns a 400 that the script
   MISDIAGNOSES: it responds by splitting the date window, which cannot reduce an action count,
   so it splits until it gives up with exit 3 and writes nothing. To go deeper than 8 pages of
   results, narrow `--days` and merge runs; do not raise `--max-pages`.

   **Cost economics (measured):** the property address resolves from ~120 chars and the loan
   principal from ~340 — both INSIDE the free 340-char grid snippet. Only the auction date needs
   ~700 chars, i.e. the paid full-text fetch. `--full-text` is an auction-date ENRICHMENT step,
   never a prerequisite.

   **Estate Claims (`--popular-search 6`) — needs a property-DISCOVERY step, not an address
   regex.** These are Notices to Creditors; their legal purpose is telling creditors where to
   file, so they never state the decedent's real estate. Verified live: the only three addresses
   are the courthouse (labelled `CIRCUIT COURT CLERK'S MAILING ADDRESS` in the text), the PR's
   home, and the attorney's firm. What they DO carry is valuable: decedent name, date of death,
   court file number, and PR name + address + phone (sample: DOD 05/20/2026, FI-2026-0001248, PR
   out-of-state in WA with a direct phone). Retarget extraction to those, then find the property
   via **land records, deeds and SDAT** (Basem, 2026-08-31) — the same lookup path the MD lane
   already uses — or the `probate-property-finder` skill.

   **Tax Deeds (`--popular-search 8`) — deprioritised.** All 26 rows classify as `other` and
   there is no `tax_deed` rule in `NOTICE_TYPE_RULES`. Basem has never seen a real one come
   through (2026-08-31), so do not invest here until one does.

   **Still open:** county values carry case and truncation noise (`FAIRFAX`/`Fairfax`, plus
   `Prin`, `Spotsylvan`, `Spotslyvani`) and a few out-of-footprint rows (`Loudoun`, `Manassas
   Park city`). Harmless for dedup (which keys on street) but it fragments per-county grouping.

4. **The DC pull (Phase 4) — ONLY on Basem's explicit go.** Described under "What the next (pull) run would do" in [docs/history/doors-per-deal.md](docs/history/doors-per-deal.md). Until it runs, every one of the 73 presets loads empty for DC.
5. ~~The other 13 jurisdictions~~ **DONE 2026-08-31:** all 13 built and verified via `src/scripts/dpd_siftmap_build_all.py` (sequential per-county driver, halts on first defect, resume-aware) — 152 presets saved, every one reloading to its exact measured count, 72 inexpressible rows routed to FTM, 8 measured-zero rows kept. Fixes that made it survivable: `saved_filter_names` now exhausts "Show more" (was capped at 4 clicks — fatal at 200+ rows), `reload_check` retries an empty popover once (transient mid-session failure seen on Anne Arundel). **Correction 2026-08-31: exhausting "Show more" is not the same as seeing every filter.** `saved_filter_names` reads the SiftMap **Presets popover**, and that popover tops out around 100 rows; the account actually holds **180 saved filters across 18 pages** (measured on the management page). The complete list is under **Configure → `/siftmap/presets/account`** (10 per page, already known to the code as `PRESETS_ACCOUNT_URL`, which is what `--delete-name` drives). Consequence: any presence/skip check built on `saved_filter_names` is working from a partial list — and `dpd_siftmap_presets.py --commit` uses it exactly that way, so it can re-save a name that already exists, which its own comment calls a hard stop. Read the management page, not the popover, when the question is "does this filter exist". QA reports in `output/dpd_qa_report_<fips>.md`. **County split DONE 2026-08-31 (Basem's design: pull all 14, call the core 9, mail everywhere):** the 30 MAIL presets now carry all 15 counties (`COUNTY_SCOPE_MAIL`), added IN PLACE via `dpd_presets_create.py --widen-counties` — load row (JS click on the title, from a FRESH /records navigation per preset), `set_tokens` the 6 new counties, Save (overwrite), then verify the STORE (counties 9→15, every other stored field byte-equal; the panel's exclusion render lies on load but Save does not serialize the lie — proven by the `--widen-smoke` gate). CALL / Deep Prospecting / Reactivation stay at 9. `dpd_presets_verify.py` exits 0: 73/73, 0 defects.
6. Sign-ups when Basem is ready (DC Recorder of Deeds free registration; DOB eRecords — `CAPTCHA_API_KEY` is now real and working, struck 2026-08-31) — the ordered list is in `output/dpd_dc_ftm_investigation.md`.

**Standing constraints:** create no Lists (reuse what exists; a SiftMap pull applies *tags* in the Add-Records modal, not lists); `.env` browser auth for every WRITE (presets/folders/SiftMap have no write API, and this account's internal API 403s on writes) — internal-API READS are fine and are how `dpd_presets_verify.py` checks the store; Priority 1/2 are stamped at pull time so they arrive with the property; FTM is stamped by the daily Register of Wills / MDDC / VA pulls; never click `Add Records to Account` and never tick the Save Filters PRO auto-add checkbox without an explicit go.

**Decisions taken 2026-08-26 (Basem):** the high-lift slices ride ALONGSIDE the widened T1s (Baltimore `Senior + Vacant` 182/16.5x, Fairfax `Notice of Default` 91/37.3x, Arlington `Free & Clear + Out-of-State + Senior` 123/16.5x as their own smaller higher-priority lists; DC's are skipped as its 76.8x measures 1 record). **AI data is NOT being bought**, so build on the distressor and geography layers only. **Superseded for DC on 2026-08-27** by the per-row design in [docs/history/doors-per-deal.md](docs/history/doors-per-deal.md); still the plan of record for the 13 counties whose per-row manifests have not been built.

