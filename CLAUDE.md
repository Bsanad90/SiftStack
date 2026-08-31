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
  On the next real pull: **eyeball `notice_id` uniqueness before trusting the id-first dedup** —
  the `BARE_ID_RE` fallback is unconfirmed against live HTML, and a shared per-page `ID=` param
  would collapse every row on a page to one. Dropped rows (off-type/out-of-footprint, incl. the
  `order_nisi` reclassifications) now land in `<out>_dropped.csv`; review it, don't trust the
  count. `--hide-read-notices` defaults OFF as of 2026-08-31. → [history](docs/history/mddc-va-md.md)
- **VA Public Notice** — works unauthenticated via the Popular Searches widget, **one county per
  Firecrawl call with adaptive date-window splitting** (multi-county selection cancels its own
  postbacks; Fairfax + 60 days exceeds Firecrawl's budget). Pagination was broken until 2026-08-28
  (wrong grid id) — every earlier run was page 1 only. `--full-text` is built but blocked:
  `CAPTCHA_API_KEY` in `.env` is still the literal placeholder. → [history](docs/history/mddc-va-md.md)
- **MD Probates + MD Legal Notices** — `--date` exact-date pulls, PRs split into columns, co-PR and
  foreign-PR notices parsed (the latter state the MD property), SDAT checked BY STREET ADDRESS with a
  full-name owner match (HIGH/MEDIUM/LOW; LOW = sold since the deed). Review output for Anne Arundel
  08/26/2026 is in `output/data_extraction_test.xlsx` — NOT uploaded, per Basem. The checkpoint is
  still contaminated (`output/md_row_last_run.json` holds a Calvert-only cutoff of 08/24/2026 while
  6 of 7 counties were never backfilled): do NOT run plain `--commit` until it is reset or made
  per-county. → [history](docs/history/mddc-va-md.md)
- **Doors-Per-Deal account build** — all 73 Records presets built and verified (0 defects), 12
  folders, 6 entry tags, 24 DC SiftMap presets saved. ZERO records pulled in, so every preset loads
  empty for DC until the pull runs on explicit go. → [history](docs/history/doors-per-deal.md)
- **Obituary DP batch** — pilot done and verified (25 records, 257 phones). The 603-record
  SmartSkip order is submitted but the card declined; re-attempt with
  `trace --pay-id <pay-id: in Claude memory, smartskip trap note>`. → [history](docs/history/prospecting.md)
- **SMS agent** — deployed on Fly, API transport verified. Autonomy ladder controlled by
  `SMS_AGENT_PHASE`. → [history](docs/history/sms-and-coaching.md)

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
- `tools/manual/test_manage_sold.py` — Headed browser test (SiftMap sold property tagging)

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

- **Main counties:** Washington DC; Montgomery, Anne Arundel, Frederick, Carroll, Calvert, Charles, and Baltimore County (not Baltimore City) in Maryland; and Fairfax, Prince William, Arlington, Stafford, and Spotsylvania Counties plus the independent city of Fredericksburg in Virginia. Coverage: the 7 MD counties + DC are already the `DEFAULT_COUNTIES` in the MDDC Trustee's Sale pipeline (`src/scripts/mddc_trustee_sale_pull.py`). The 5 VA counties are covered by `src/scripts/va_trustee_sale_pull.py` against `publicnoticevirginia.com` (`mddcpublicnotices.com` has no Virginia checkbox at all). **Fredericksburg City is not on that site either** and remains uncovered — unresolved whether its notices fold into Spotsylvania's.
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
3. **Run the two free VA pulls — CODE FIXED 2026-08-28, FINAL RUN NOT COMPLETED (session paused mid-run on Basem's instruction).** Three real fixes landed: multi-county postback cancellation (one county per Firecrawl call), Firecrawl budget on heavy windows (adaptive date-window split), and a wrong pager id that had limited every earlier run to page 1. **Next session, run:** `python -u src/scripts/va_trustee_sale_pull.py --popular-search 6 --days 60 --max-pages 3 --out output/va_estate_claims.csv` then the same with `--popular-search 8 --out output/va_tax_deeds.csv` (detached, ~15–25 min each; exit 3 = Firecrawl gave up on a 7-day window, re-run), then `python src/scripts/data_extraction_test_build.py --md-json output/md_row_review_08-26-2026.json --va-estate-claims output/va_estate_claims.csv --va-tax-deeds output/va_tax_deeds.csv` to add the VA tabs to the review workbook. The CSVs currently on disk are from the BROKEN pre-fix run (statewide, page 1 only) — do not review them. Nothing goes to the account until Basem has checked the workbook.
4. **The DC pull (Phase 4) — ONLY on Basem's explicit go.** Described under "What the next (pull) run would do" in [docs/history/doors-per-deal.md](docs/history/doors-per-deal.md). Until it runs, every one of the 73 presets loads empty for DC.
5. ~~The other 13 jurisdictions~~ **DONE 2026-08-31:** all 13 built and verified via `src/scripts/dpd_siftmap_build_all.py` (sequential per-county driver, halts on first defect, resume-aware) — 152 presets saved, every one reloading to its exact measured count, 72 inexpressible rows routed to FTM, 8 measured-zero rows kept. Fixes that made it survivable: `saved_filter_names` now exhausts "Show more" (was capped at 4 clicks — fatal at 200+ rows), `reload_check` retries an empty popover once (transient mid-session failure seen on Anne Arundel). QA reports in `output/dpd_qa_report_<fips>.md`. Still open from the old item: the six counties outside the nine-county scope (Calvert, Carroll, Charles, Stafford, Spotsylvania, Fredericksburg City) need `COUNTY_SCOPE` widened — a delete-and-rebuild of all 73 Records presets (~2.7 h) — before *pulled* records from them show in the Records presets; defer until a pull for those counties is actually approved.
6. Sign-ups when Basem is ready (DC Recorder of Deeds free registration; a real `CAPTCHA_API_KEY`; DOB eRecords) — the ordered list is in `output/dpd_dc_ftm_investigation.md`.

**Standing constraints:** create no Lists (reuse what exists; a SiftMap pull applies *tags* in the Add-Records modal, not lists); `.env` browser auth for every WRITE (presets/folders/SiftMap have no write API, and this account's internal API 403s on writes) — internal-API READS are fine and are how `dpd_presets_verify.py` checks the store; Priority 1/2 are stamped at pull time so they arrive with the property; FTM is stamped by the daily Register of Wills / MDDC / VA pulls; never click `Add Records to Account` and never tick the Save Filters PRO auto-add checkbox without an explicit go.

**Decisions taken 2026-08-26 (Basem):** the high-lift slices ride ALONGSIDE the widened T1s (Baltimore `Senior + Vacant` 182/16.5x, Fairfax `Notice of Default` 91/37.3x, Arlington `Free & Clear + Out-of-State + Senior` 123/16.5x as their own smaller higher-priority lists; DC's are skipped as its 76.8x measures 1 record). **AI data is NOT being bought**, so build on the distressor and geography layers only. **Superseded for DC on 2026-08-27** by the per-row design in [docs/history/doors-per-deal.md](docs/history/doors-per-deal.md); still the plan of record for the 13 counties whose per-row manifests have not been built.

