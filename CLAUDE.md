# CLAUDE.md — SiftStack

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Personal per-user instructions (greeting, style) go in `CLAUDE.local.md` (gitignored), not here.

## Project Overview

**SiftStack** — real estate investing operations platform built around the DataSift.ai CRM
(`app.reisift.io`, API `apiv2.reisift.io`), for ONE account: Basem's. It covers data acquisition
(first-to-market public-notice pulls, SiftMap pulls), enrichment (Smarty, Zillow, tax rolls, SDAT,
DC OTR), skip trace (SmartSkip, Tracerfy, Trestle scoring), deep prospecting (heirs and signers),
deal analysis (comps, rehab, MAO), market intelligence, CRM automation (presets, sequences, lanes,
sold suppression), and the 25-skill REI Skill Library distributed to the DataSift community.

**Footprint — the ONLY jurisdictions in scope (6, narrowed 2026-09-01 from 14):** Washington DC;
Montgomery, Anne Arundel and Frederick (MD); Fairfax and Arlington (VA). Everything here is built
for these six. Code inherited from Ty's upstream (tnpublicnotice.com scraper, `SAVED_SEARCHES`,
Knox Tax API, the Apify actor, the Fly deployments, the Columbus OH `soi_*` beta) is NOT this
account's data source. **NEVER RUN TN (Basem, 2026-10-08):** no `src/main.py daily` / `weekly`,
no tnpublicnotice.com scrape, no Knox/Blount pull, no Knox Tax API call, no scheduled task for any
of it. Read a TN example as the pattern only and apply it to the footprint.

**Who is who:** Basem is the user and owns this fork (`basem/work`, origin Bsanad90). Ty is the
upstream author, a different person, with his own account and Fly deployments. Nothing on Fly is
ours. This account's pulls run locally from `automation/*.ps1` on Basem's PC, Tue–Sat.

## Commands

```bash
# Setup
pip install -r requirements.txt && playwright install chromium
cp .env.example .env   # then fill in credentials

# Footprint daily pulls (automation/*.ps1 runs them Tue-Sat; review before any DataSift push)
python src/scripts/mddc_trustee_sale_pull.py --list-searches      # always first; default search ["43"]
python src/scripts/mddc_trustee_sale_pull.py --days 30            # DC + Montgomery/Anne Arundel/Frederick
python src/scripts/va_trustee_sale_pull.py --popular-search 4     # Fairfax + Arlington (--max-pages <= 8)
python src/scripts/md_register_of_wills_pull.py --commit --dump-json   # checkpoint-driven MD probates
python src/scripts/dc_probate_pull.py                             # DC probate
# full-text legs pass --prior-auto (cross-day reuse); the runner already does

# Team Drive (admin@galaldev.com, "SiftStack Daily Pulls/<date>/"): the 6 AM runner ends with --ready
# (CSVs + summary + row_needs_address.csv, READY.txt LAST); token in drive_sync_state.json
python src/scripts/daily_pull_drive_sync.py --date 2026-10-08 [--ready] [--dry-run]
# RoW agent re-review ON THIS PC: the 6 AM runner's last step fires task "SiftStack RoW After
# Pull" (row_after_pull.ps1 = md_row_address_fill.ps1 THEN row_agent_review.ps1): export ->
# `claude -p --agent row-address-reviewer` (row-address-researcher x 10 estates, parallel,
# dontAsk; Land Records/SDAT ONLY via landrec_agent_search.py = one login at a time + daily cap
# ROW_AGENT_LANDREC_CAP, default 300; stops under 20k Firecrawl credits) -> merge --commit (single writer, never overwrites an address) -> SDAT grade
# -> finish: still-not-found estates APPENDED to Probate Extraction 2 "Register of Wills" (32-col
# format, "Not Found (Claude)", existing rows never edited, read back; log output/row_review/
# pe2_appended.json) + row_manual_review_<day>.csv to Drive + popup. Mohammad works it with
# `claude --agent row-manual-review` (docs/setup/row-address-review.md); his REVIEWED rename ->
# `watch` popup. Skip trace / DP / upload / spend after that = Basem's go only.
python src/scripts/row_address_review.py merge <review.csv> [--commit]
python src/scripts/row_review_pipeline.py finish --day 2026-10-08 [--commit]   # dry run by default
python src/scripts/row_review_pipeline.py watch

# Preset / lane QA (read-only)
python src/scripts/dpd_presets_verify.py          # 85 presets, 14 folders; exit 0 = clean
python src/scripts/dpd_lane_assignee_report.py    # lane counts per dialer
python src/scripts/obituary_dp_qa.py --run-dir <dir>   # DP batch audit; --repair re-posts gaps
python src/scripts/dnc_scrub.py --run-dir <dir>        # DNC check, cache-only; --spend = paid, on go

# SiftMap sold sweep -- THE path for sold suppression. ALWAYS dry-run first. No --counties = the 6.
# Without --sold-account-scope in, a live run IMPORTS every sold property in each county-month.
python src/main.py manage-sold --sold-account-scope in --dry-run
python src/main.py manage-sold --sold-account-scope in                 # monthly, tag held records
python src/main.py manage-sold --sold-account-scope in --months-back 24   # backlog catch-up

# Offline guards -- run the matching one after ANY change to the code it names
python tools/manual/test_fulltext_prior.py        # resolve loop / --prior reuse (45 checks)
python tools/manual/test_md_row_pass_budgets.py   # MD RoW reconciliation + address passes (12)
python tools/manual/test_dpd_playbook_format.py   # parse_ladder / merge_shard / build / plan (42)
python tools/manual/test_skip_dp_census.py        # classify / is_entity / tag matchers (57)
python tools/manual/test_dp_push_ghosts.py        # _stale_numbers / _ghost_blanks / _place_note
python tools/manual/test_ghost_name_resolver.py   # same_person / vendor_candidates / decide
python tools/manual/test_namesake_audit.py        # recommend + age/place regexes
python tools/manual/test_property_create_probe.py # classify / owner_for_create (30)
python tools/manual/test_permit_portal_pull.py    # permit feed parsers (50+)
python tools/manual/test_manage_sold_url.py       # county -> SiftMap URL (no browser)
python tools/manual/test_dnc_scrub.py            # DNC cache rules / scorer override / scrub + push plan + SC import (48)

# Monthly team KPI + invoices (agent team-kpi-reviewer). Hubstaff = API (token ROTATES; live one in
# hubstaff_state.json, never delete it); SmrtPhone = v2 API over smrtphone_state.json; only the
# WhatsApp chat zip is a MANUAL export.
# Rates/names: team_kpi_config.json (gitignored)
python src/scripts/team_kpi.py find --month 2026-09     # which exports cover the month
python src/scripts/team_kpi.py report --month 2026-09   # after the 3 extractors -> output/team_kpi/
python src/scripts/team_kpi.py daily                    # today -> HTML dashboard; task "SiftStack Team KPI Daily" 20:00
python src/scripts/team_kpi.py weekly                   # last 7 days; task "SiftStack Team KPI Weekly" Sun 20:05
python src/scripts/team_kpi.py monthly --month 2026-09 [--screens]   # a month; --screens = paid screenshot check (~$8 a month's first run)
python src/scripts/data_manager_kpi.py --month 2026-09 --account   # Mohamed Hisham, Probate Extraction 2 (never Sheet26)
# all reports: output/team_kpi/index.html (desktop shortcut "Team KPI Reports"); every run also writes
# output/team_kpi/team/ (no pay, no data-manager hours, no private sections) = the shared link
# https://claude.ai/artifact/EgLz6qg82sLHaHamrKdj8M (org-shared; republish that folder, never the top one)

# Upstream TN scraper (Knox/Blount, `src/main.py daily`) -- NEVER RUN on this account (see Footprint)
```

All source is in `src/`; imports assume `src/` is the working directory. Run from the project
root with `python src/main.py` or set `PYTHONPATH=src`. Output CSVs land in `output/`
(gitignored), logs in `logs/`; daily pulls in `output/daily_pulls/<YYYY-MM-DD>/`.

## Architecture in one screen

- **Pull scripts** (`src/scripts/*_pull.py`) — Firecrawl / Scrapfly / Playwright fetch, regex +
  LLM parse, footprint filter, CSV + `_dropped.csv` + `_details.json` sidecar. Full text for
  foreclosures via `*_fetch_full_text_browser.py` (2Captcha solves the Turnstile).
- **Enrichment** — `smarty`, `zillow`, `sdat`/`itspe` tax rolls, `obituary_enricher`,
  `llm_client` (every exception swallowed, returns `None` — see Traps).
- **DP batches** — `src/scripts/obituary_dp_batch.py` (`research → rank → score → build → push`,
  run dirs under `output/`); SmartSkip + Tracerfy + Trestle; QA `obituary_dp_qa.py`.
- **DataSift** — `src/datasift_api_upload.py` + per-job scripts over `/api/internal/`;
  `src/datasift_uploader.py` (Playwright) for the wizard, enrich, skip trace, sequences, SiftMap.
- **DPD lanes** — `src/dpd/preset_spec.py` (85 presets as data), `block_map.py`,
  `jurisdictions.py` (FIPS, `FOOTPRINT`); scripts `dpd_presets_*.py`, `dpd_siftmap_*.py`,
  `dpd_lane_*.py`, `obituary_lane_build.py`.
- **Upstream TN scraper** — `main.py → scraper.py → captcha_solver.py → notice_parser.py →
  foreclosure_filter.py → data_formatter.py`; PDF / photo / Dropbox importers; Market Finder
  extraction; `report_generator.py`; `drive_uploader.py`. Detail in
  [tn-ftm-pipeline.md](docs/history/tn-ftm-pipeline.md).

**Date semantics (build 1.0.30+):** `date_added` = the date WE added the record (the run date);
the notice's publication date is `date_published` / "Notice Publish Date". Anything that needs
the filing date (DOD sanity, Probate Open Date, month tag, dedup tie-break) uses `date_published`.

**Zero notices is a FAILURE, not a quiet day.** Every pull leg, every day.

## Where the details live

This file is instructions, current state and the traps index. The build record — every API
contract, every measured number, every incident — is in `docs/history/`. Nothing was deleted:
`_CLAUDE-snapshot-2026-08-28.md` is the full pre-first-split file, `_CLAUDE-snapshot-2026-09-30.md`
the pre-fix file, `_CLAUDE-snapshot-2026-10-01.md` the pre-second-split file.

**Read the relevant history file before touching one of these pipelines.**

| File | Covers |
|---|---|
| [tn-ftm-pipeline.md](docs/history/tn-ftm-pipeline.md) | Ty's TN scheduled pull, the upstream scraper architecture, courthouse photo import, Dropbox watch, Apify actor |
| [mddc-va-md.md](docs/history/mddc-va-md.md) | MDDC Trustee's Sale, VA Public Notice, cross-day full-text reuse, MD Register of Wills + Legal Notices, DC probate, Land Records/SDAT, the extraction audit |
| [doors-per-deal.md](docs/history/doors-per-deal.md) | The DPD account build: SiftMap filter contract, the 85 Records presets, Work Order format + preset planner, one-owner / obit-window / entity-exclude preset passes, SiftMap filters API, "Where the DPD code stands", Local working files, inverted-preset post-mortem |
| [prospecting.md](docs/history/prospecting.md) | Obituary ranking, FTM + Obituary DP batches (every trap), DP v5, ghost resolver, owner-died namesake audit + repair |
| [datasift-api.md](docs/history/datasift-api.md) | The whole API contract: upload, endpoint traps from upstream, record creation, census, incomplete records + owner PATCH, event log + tasks, DP strip re-push, SiftLine boards, assignees, the full My Defaults text |
| [datasift-ui.md](docs/history/datasift-ui.md) | Wizard steps, CSV schema, enrich + skip trace, styled-components / DnD / popup patterns, Market Finder extraction |
| [deal-analysis.md](docs/history/deal-analysis.md) | Comp package, dispo stack, post-walkthrough, rehab engine, lender package |
| [sold-suppression.md](docs/history/sold-suppression.md) | SiftMap sold sweep port, Sold Property Cleanup sequence, the 2026-09-07 backlog |
| [sms-and-coaching.md](docs/history/sms-and-coaching.md) | SMS agent (Ty's deployment), call coaching engine |
| [soi-and-knox.md](docs/history/soi-and-knox.md) | Knox FTM pull, sphere-of-influence beta, account-wide enrichment audit |
| [mailing.md](docs/history/mailing.md) | Mailed-history index, the obituary mailing consolidation |
| [permits.md](docs/history/permits.md) | Building-permit portal survey + pull (DC / Montgomery / Fairfax) |
| [skill-library.md](docs/history/skill-library.md) | The 25-skill inventory, cross-skill constants, contractor research workflow |
| [docs/datasift-course/README.md](docs/datasift-course/README.md) | DataSift's own REI Mastery course condensed, and where this account differs |

Also `docs/api/` (per-vendor API notes), `docs/setup/` (onboarding), `tools/manual/` (headed
harnesses + offline guards), the auto-memory index (`MEMORY.md`) for open work and incidents.

## Active pipelines — current state (2026-10-01)

One line of state each; the reasoning and every number is in the linked file.

- **TN FTM scrape / SMS agent / Apify** — Ty's, not ours. No Fly account here; `fly.toml` is
  inherited. Zero `sys_` tags on this account proves the SMS agent never touched it.
- **MDDC Trustee's Sale** — default search `["43"]` (41 is gone; run `--list-searches` first).
  County checkboxes are inert; footprint = (state, county) clip after the ZIP geocode; use `--days 30`. Full text via the
  browser script (Turnstile + 2Captcha); `--prior-auto` reuses yesterday's text by notice id.
  `BATCH_TAG` stamps `FTM`. → [mddc-va-md](docs/history/mddc-va-md.md)
- **VA Public Notice** — one county per Firecrawl call, `--max-pages <= 8`, `--full-text` works
  (Scrapfly + 2Captcha; preflight aborts on a dead quota). → [mddc-va-md](docs/history/mddc-va-md.md)
- **MD Register of Wills + DC probate** — per-county checkpoints, healthy since 2026-09-06; the
  daily leg writes CSV + JSON BEFORE the address pass (fixed 09-15 after 5 dead days); real address
  throughput comes from `automation/md_row_address_fill.ps1`. DC ownership via ITSPE (= OTR).
  → [mddc-va-md](docs/history/mddc-va-md.md)
- **DPD account** — 85 Records presets in 14 folders, verifier 85/85: 73 DPD + the **Obituary lane
  (13 CALL / 14 MAIL, added 2026-09-30)** cloned from FTM with `any_tags = [Obituaries, Deceased
  Owner]`. Entry tags `FTM` / `Priority 1/2` / `Obituaries`. Obituary window
  `["360-months", "month"]` on every preset EXCEPT the Hottest + FTM CALL lanes; `Entity Owner`
  excluded from all CALL presets; **`Recently Sold` excluded on 83 of 85 presets since 2026-10-06**
  (every CALL, MAIL, DEEP and REACTIVATION lane; the two two-include-group presets have no tag
  block left); one-property-per-owner on the 24 dial lists. SiftMap saved
  filters follow the 09-24 Work Order plan (P1 rungs, 6 counties; 7 rungs pulled 09-28, 1,311
  records); **auto-add ON for all 30 current P1 rungs since 2026-10-05** (T2 ZIP, P2, stale OFF);
  drip 11496 tags `Obituaries` only. **Split test (2026-10-06): folder `15 SPLIT TEST - CALL`,
  3 presets off Hottest - 02 Ready to Call** — A control (763), B with the market-research
  dead ZIPs (191) + dead neighbourhoods (285) excluded (690), C the dead-neighbourhood records
  alone (70); outside the 85-preset spec and the verifier; `dpd_split_test_presets.py`
  (`--undo`, `--bar thin` for the stricter 570). Open: 10 old-label renames,
  Obituary lane assignment across the 5 dialers. → [doors-per-deal](docs/history/doors-per-deal.md)
- **DP batches** — FTM (25) and Obituary (618/619) batches live and QA-clean; signers-only
  re-push of 1,391 records done 09-17; 23 namesake false-positives repaired 09-18; 82 ghost REL
  names written 09-17. Open: 5 `DP Death Unverified`, REVIEW 141, 8429 Farrell Dr (blank owner).
  → [prospecting](docs/history/prospecting.md)
- **Sold suppression** — `manage-sold --sold-account-scope in` on the 6 counties is THE path; the
  live `Sold Property Cleanup` sequence turns `Recently Sold` into status `Already Sold`, which
  every preset excludes, and since 2026-10-06 the `Recently Sold` TAG itself is excluded on 83 of
  85 presets (belt and braces: the sequence is unproven on SiftMap filter 11798's auto-add drip).
  No live sweep has run yet (Montgomery dry run passed 08-31, 992 matched).
  → [sold-suppression](docs/history/sold-suppression.md)
- **Lane hygiene** — Incomplete-record cleanup (FTM 8 approved rows wait on Basem's
  `--commit --group A-same --include-review`), Re-Skiptrace dial sheets, owner-grouped
  assignment, mailed-history screening: all built, state in the memory index.

Local working files: `output/_archive/live_account_pull.json` (133 MB account hydration) must be
passed explicitly as `--cache`; without it `live_pull.py` starts a multi-hour re-pull. The
parsed `data/dpd_playbook_<fips>.json` are the Work Order rebuilds of 09-24; August versions
are archived under `output/_archive/`.

## Traps

One line per trap: a thing that answers success while doing something else, or fails silently.
Each ends with the history file that holds the full story. **A new trap gets its line here on the
day it is found; its story goes to the history file.**

### DataSift API — writes
- **Tags must be an ARRAY.** A comma string creates one tag literally named `"A, B, C"`. → datasift-api
- **A select custom field takes the OPTION UUID, not the label** (`"LEN"` → "not a valid UUID"). → datasift-api
- **Entity owners cannot have a blank `first_name`.** Send `company` and OMIT the person keys; `""` ≠ omitted. → datasift-api
- **`notes` on the property payload returns 200 and is discarded.** Post a board message + pin instead. → datasift-api
- **`PATCH /property/{uuid}/ {"tags": [...]}` is MERGE-ONLY.** A dropped name returns 200 and stays; removal is `POST .../remove-tags/`. Same for `lists`. → datasift-api
- **Pinning lies twice.** `POST message/ {"pinned": true}` stores false; `PATCH message/{id}/ {"pinned": true}` returns 200 and changes nothing; only `POST .../message/{id}/pin/` (204) pins. Verify by read-back. → prospecting
- **`PATCH message/{id}/ {"message": text}` DOES store** (proven on 1,391 records), so a re-push edits its own note in place. Verify by read-back, never by status. → datasift-api
- **`add-notes` writes the `notes` FIELD, not the board a caller reads,** and answers 204, so a 404-gated fallback never fires. 619 boards were empty. → prospecting
- **Collection-level `remove-*` / `add-*` routes apply ACCOUNT-WIDE** whatever the body scopes (`remove-lists/` with one uuid emptied the `Probate` list, 30,766 records, 2026-09-16). Per-record routes only; a collection route is a WRITE, never a probe. `OPTIONS` 404 ≠ route absent. → datasift-api
- **`POST /property/skip-trace/` traces the WHOLE ACCOUNT** (one uuid → 53,322 records, metered plans bill all of them). Read `activity/skiptrace/stats/` and the plan first; here it is the wizard. → datasift-api
- **The `lists` body shape is disputed** (string vs array; the wrong one returns 201 and changes nothing). One list per call as a STRING, read back. → datasift-api
- **The Open API `POST /property/` is dead (403 since 2026-09-01).** Create via `POST /api/internal/property/` (201, does NOT upsert; duplicate → 400 with the existing uuid; owner validated BEFORE the duplicate check). Create with address + owner only, attach the rest per record. → datasift-api
- **The duplicate 400 is the ONLY reliable existence test** — it normalises (`Drive`=`Dr`, `21704`=`21704-7091`) and writes nothing. `search` is an EXACT match on the stored street string. DataSift rewrites the address and auto-attaches its own lists on create; assert on the stored form. → datasift-api
- **DataSift silently caps an owner at 30 phones** (200, echoes every number in `added`, stores 30), and a full owner silently refuses every new one — superseded numbers must come OFF first via `POST owner/{uuid}/remove-phones/`. `PATCH owner/ {"phones": ...}` replaces WHOLESALE: never. → prospecting
- **The phone upsert SKIPPED numbers already on the owner,** so a re-push could never correct a tag; send the full list. → prospecting
- **The upsert RESET a dialer's disposition:** a plan phone stamped `status: UNKNOWN` overwrote a live DEAD mark. Keep the live status whenever the plan's is UNKNOWN/blank. → prospecting
- **The DataSift wizard skip trace is ADDRESS-anchored and returns the OCCUPANT when the owner's mailing is the situs but he lives elsewhere** (2221 Ridge Pl SE, 2026-10-06: SiftMap set mailing = situs, the trace loaded a 34-year-old tenant's 2 phones + 3 emails onto owner Marvin Turner, age 34 stamped on the owner block, phone_intel tiered them Dial First, the dialer reached the tenant). The wizard shows no matched name; `phone_intel` scores activity, never identity. Trestle Real Contact (`GET /2.0/real_contact`, $0.03 + litigator) returns `phone.name_match` / `address.name_match` per name: both numbers matched Barksdale, neither matched Turner. An owner age that contradicts the Senior list is the tell. → prospecting
- **Phone tags fail to land on ~3.5% of first upserts;** a plain re-upsert fixes them, so the QA repair pass is not optional. Tier `Unknown` is never written as a tag — auditing for it reports correct behaviour as a defect. → prospecting
- **An API phone upsert does NOT set `owner.skiptraced` / `skiptrace_attempts`,** and no skip-trace date field exists anywhere. Stamp the boolean AND the trace-date tag in the same operation (see My Defaults). → datasift-api
- **A `Rel{n}.{m}` phone tag is NOT provenance** — RFD imports and probate pushes use it too; the tag-only guard stripped 18 foreign numbers. Remove only numbers OUR prior plan wrote (`push --prior-plan`). → datasift-api
- **The owner-block PATCH needs the FULL block including the mailing;** a name-only block re-binds to a stranger's entity. Name + mailing matching an ORPHAN entity absorbs its phones (same uuid, count UP); renaming the owner of a DUPLICATE property record collapses the duplicate (the twin 404s). Undo with `null` names, not `""`. → datasift-api
- **DataSift has NO merge action; its dedupe fires on an ADDRESS PATCH and the record that RECEIVES the patch SURVIVES** (Schenkel 2026-10-02, twice: PATCH `{"address": {...}}` to the twin's exact street → the twin 404s within seconds). Lists and tags carry over; board notes, custom fields, status, assignee and estimate_value do NOT; phones pool only when the owner block is also patched to the same name + mailing (orphan-entity absorption). So: back up all copies, fold everything onto ONE, patch the OTHERS' addresses one at a time, then re-push the note/fields onto whichever uuid remains (`push --force` resolves by address). → prospecting
- **A 404 on a known uuid can be a MERGE** (`property.merged` in the survivor's `/logs/`) or a real deletion. Re-resolve by address before calling it either. → prospecting
- **`task.completed` is READ-ONLY on PATCH** (200, stores nothing); complete via `POST task/{uuid}/complete/`. The task list overlaps pages without `ordering=`. → datasift-api
- **A board note may only displace its OWN family** (`_note_family`: first line cut at " (", date stripped, equality). A non-DP plan pushed through the DP pusher deleted 21 DP boards. REL fields another plan wrote are not yours to blank unless their VALUE proves it. → prospecting
- **Duplicate PROPERTY records exist** (17 addresses under two owners). Resolve a uuid on owner first+last and REFUSE a blank owner — an empty name matches a null `last_name`. → prospecting
- **Which source wins when two pushers load one record:** the richer set (more contacts with callable numbers, better tiers, verification). Append the other's unique relatives into free REL slots; never blank. Not enforced in code — compare by hand before `--force`. → datasift-api

### DataSift API — reads and queries
- **`GET property/?lists=<uuid>` is silently IGNORED** and returns the whole account. Counts and membership come only from `POST property/` with `x-http-method-override: GET` and `{"query": {"must": {...}}}`. → datasift-api
- **A query string on the `POST property/` override URL (`?limit=1`) makes DataSift IGNORE the whole body** and return the full account count (32,948); paging goes in the BODY (`limit` / `offset`). An upload activity's records = `must.uploads: [<activity uuid>]` (found 2026-10-07). → datasift-api
- **Folder and preset listings default to 10 rows** (`?limit=999`); an exists-check without it lies and the re-create 400s on the unique title. → datasift-api
- **An unknown query key beside real ones is SILENTLY DROPPED;** only a count that MOVES proves a key. A query of only unknown keys 400s. `assigned_to` is a bare uuid string (a list 400s). → datasift-api
- **A stored preset's `filters` is NOT a valid query payload** (county objects, relative dates → 400 "wrong format"). Translate with `dpd_lane_assignee_report.to_query()`, which resolves the relative window. Every lane reader was dead for 3 days on this. → doors-per-deal
- **Filtered counts lag writes by a minute or two** (phantom "unassigned" right after a bulk write), and a lane query can return INDEX GHOSTS whose detail GET and PATCH both 404. Per-record read-back is the truth. → datasift-api
- **`deceased` is TRI-STATE** (3 true / 18,741 false / 12,169 null): `deceased: 1` matches three records. `dnc=1` and `opt_out=1` count 0 here. → datasift-api
- **The thin search row carries NO tags, lists or `type`;** membership = server-side `all_tags` / `all_lists` sets (AND) + detail reads. Clean/Incomplete is the detail field `type`. → datasift-api
- **`GET owner/` takes NO filter and 400s past offset 10,000;** the property query's `search` matches owner mailing strings too (that is the collision check). → datasift-api
- **`GET activity/` ignores `?prop=`** (account-wide bulk jobs only); per-record history is `GET property/{uuid}/logs/`. The Activity Log ECHOES board posts, so count messages via the message API. → datasift-api
- **A live status must be NORMALISED before comparing to `DEAD_STATUSES`** (`not_interested` vs "Not Interested"). → sold-suppression
- **`LiveApi._mint` has no retry** (a 502 at token mint killed a run at step zero); subclass with backoff. The Records UI shows no total — "of N" is PAGES of 10, and the Clean tab hides Incomplete records. → datasift-api
- **The Api-Key writes on `/api/internal/`;** "every write 401s" is true of the OPEN surface only. The distinction is the surface, not the credential. → datasift-api
- **Saved-filter and preset TITLES ARE NOT UNIQUE;** any reader that dedupes by name under-counts (two "District Of Columbia, DC" filters). → doors-per-deal
- **The Records panel DOES filter on ZIP and neighbourhood** (`must.any_zip5` as `{uuid,title,isNegative}` objects, `must_not.any_neighborhood` as plain names, case-insensitive); preset_spec's "no neighbourhood block" line was wrong for six weeks. A geography exclusion for a preset that spans six counties must use the MAX-across-county-tables verdict (the 09-22 file), never a per-county Market Finder string: 22101 McLean reads dead in two tables and is Fairfax's busiest ZIP. → doors-per-deal

### Presets and SiftMap
- **A key that moves the query count proves the QUERY vocabulary, not the STORE shape.** Read `toPropertiesFilters` in `app.reisift.io/main.min.js` before writing a new block; unknown stored keys pass through to the query INVISIBLY (the `[1,1]` owner range ran for an hour unseen). → doors-per-deal
- **SiftMap DOES filter on HOA Lien / Other Lien / Low Income / Bankruptcy / Estate Sale / Heirs** through the Distressors block (`distressors_method=custom`, `distressors_include=<comma-joined is_* keys>`, `distressors_include_min_match`); the 08-26 `UNFILTERABLE` verdict in `dpd_siftmap_manifest.py` was wrong and 29 P1 rungs were never built (found 2026-10-05). A JSON list in `distressors_include` 400s. → doors-per-deal
- **SiftMap `preset_tax_lien` is the TAX LIEN preset, NOT tax delinquency** (0 in all 6 counties); the Work Order's "Tax Delinquent" is the distressor `is_recent_deliquent` (sic; Fairfax P1-13 942 vs workbook 948). Every Fairfax TD filter built on `preset_tax_lien` read 0 for that reason (found 2026-10-05). State Tax Lien = `is_state_tax`. Saved filters can be CREATED over the map API: `POST map.reisift.io/filters/` → 201 (2026-10-05, 29 filters, read back). → doors-per-deal
- **"One property per owner" is the COLLAPSE mode** `{"show_properties": "one_per_owner"}`, not a `[1,1]` range (which EXCLUDES multi-property owners). → doors-per-deal
- **The PRIOR date control is the last n units ENDING at the anchor:** `["30-days", "today"]` = the LAST 30 days, the opposite of "older than 30 days". Anchors: today / yesterday / week / month / quarter / year. → doors-per-deal
- **The obituary window is an INCLUDE window:** a record with NO obituary date does not load (Hottest Ready to Call 52 → 1 on the first write). → doors-per-deal
- **A tag EXCLUSION renders as a red minus chip inside the "ALL TAGS (AND)" block whose dropdown still says Include.** Read chip colour, not the dropdown. → doors-per-deal
- **The panel offers 19 of 38 statuses;** `Lost Deal`, `Close Out`, `Buyer*` are not selectable there (API unprobed). → doors-per-deal
- **A hand-save can land a relative date block by accident** (FTM 02 picked up `last_obituary_date ["24-months","quarter"]`, lane 315 → 33). Diff the store after any manual save. → doors-per-deal
- **A SiftMap filter with auto-add ON and `tags: []` drips UNTAGGED records no preset can see.** Set the filter's tags BEFORE enabling auto-add. → doors-per-deal
- **A saved SiftMap filter's identity is the LIST (county + signal set), not the label;** rank labels move every re-measure. The management page glues the description onto the name (`bare_name()`). An auto-add filter is never deletable. → doors-per-deal
- **The Work Order's doors/deal are MARGINAL and the ladder order IS the ranking;** never re-sort by dpd. This account reads the no-AI ladder (`rows_no_ai`). → doors-per-deal
- **`manage-sold` without `--sold-account-scope in` IMPORTS every sold property in the county-month.** Dry-run first; `in` only tags held records. → sold-suppression
- **Never click `Add Records to Account`, never enable auto-add, never push a pull, without an explicit go.** Keep "Do not replace owners" ON (protects the PR/DM mapping). → datasift-ui

### Pulls
- **MDDC county checkboxes are INERT** (a DC-only search returned 18 counties, VA included). Footprint filtering is post-hoc: the state check, then (since 2026-10-08) a (state, county) clip AFTER the ZIP geocode (`filter_footprint_county`; before it every MD/VA county reached the CSV, 32 of 92 on 10-08); no-county rows are kept; review `<out>_dropped.csv` (`drop_reason`). → mddc-va-md
- **"pagination did not advance" is usually END OF RESULTS** (the site publishes ~3-4 notices a day). Read the row counts before "fixing" it. A quiet DC week is chance: `--days 30`. → mddc-va-md
- **The Firecrawl grid carries NO notice id** (0 of 94 rows); only the Playwright-settled grid does, and the first Details.aspx nav per session bounces back to Search.aspx — retry. → mddc-va-md
- **The MDDC snippet parser has no deed-date guards** and emitted a deed recording date as the auction date; re-mine with the VA parser's guards. A republication carries a NEW id; a past sale date = skip. → mddc-va-md
- **VA multi-county selection cancels its own postbacks** — one county per call; `--max-pages` past 8 misreads as a budget problem. Scrapfly QUOTA EXHAUSTION reports in `scrape_result["error"]` with empty content and was masked as `unknown_page_state`. → mddc-va-md
- **A "first 5 fetches failed → abort" gate must count FETCHES, not resolved rows,** or cross-day reuse fills `results` and a dead gate grinds every new id. → mddc-va-md
- **An unbounded, unbanked slow pass in FRONT of the deliverable kills the leg** (MD RoW wrote nothing for 5 days). Write the CSV before the address pass; every pass takes `limit` + `save_cb`; queue least-recently-checked FIRST; drop rows with no `record_id` at build time. → mddc-va-md
- **Two processes holding the RoW ledger write it back WHOLESALE** — the second silently discards the first's work. The fill job refuses while a Register of Wills process is alive; anything else needs the same guard. → mddc-va-md
- **Estate Search pagination tops out ~12 pages** (Firecrawl 60s cap); split wide windows; `--max-pages` past 12 = 400. → mddc-va-md
- **`Property Address` can hold the literal `Not Found (Claude)`.** That is not an address. → mddc-va-md
- **The ZIP→county cache stored a geocode FAILURE as a blank and served it forever.** The FCC Census Area API 400'd on every call 2026-10-06; 4 ZIPs were cached `""`, two Fairfax rows were dropped as "no county" and two MD rows went blank. A blank is never cached now and the Census Bureau geocoder is the fallback; a run that prints "resolved county by ZIP geocode" can still be serving the cache. → mddc-va-md
- **A Montgomery ratification notice reads "Notice is hereby given this … by the Circuit Court of … that the sale", not "ORDERED",** and the grid snippet truncates before "RATIFIED", so it lands in `_dropped.csv` as type `other`, looking like a parser miss. It is a POST-SALE notice (price stated): never a lead, even with a `(DECEASED)` defendant. → mddc-va-md
- **ITSPE IS OTR,** so re-querying OTR cannot resolve a MEDIUM match (MEDIUM = partial NAME match). A surname match on title is an heir lead, not a dead end. → mddc-va-md
- **DC Recorder of Deeds: a `/results?parties=...` URL answers "No results found" for EVERY term** (even the URL the form itself lands on); the 1391 `rod_url.py` "no results" were false negatives. Only the advanced-search FORM returns rows; `dc_rod_lookup.py` drives it. **The document IMAGE is FREE to VIEW** (click the row → `/doc/<id>`, a page viewer with zoom; Basem 2026-10-02) — only the download / certified copy costs ($4 + $1.50); read the vesting clause off the viewer, never buy it. → prospecting
- **ROD result rows are NOT date-ordered and the page is 50 rows (`limit=` ignored);** reading 100 of 158 `SMITH HELEN` rows missed the 2019 sale. Narrow the term (30-year cap, then the tax-roll middle initial); never treat the first page as the latest. → prospecting
- **In the ROD index `TRUST` / `TRUST ASSIGNMENT` / `TRUST MODIFICATION` = DEED OF TRUST (a mortgage), `TRUSTEES DEED` = foreclosure, `CERTIFICATE OF SATISFACTION` = payoff.** A "trust" flag on them marks every mortgaged owner; trust VESTING comes from the tax roll's `TRUSTEE` suffix. → prospecting
- **Tax-roll SSL writes the suffix after the number (`5519E 0011`), the ROD index before it (`E5519`) or not at all.** Compare on digits; 6 parcels read "no hit" and 1638 Fort Davis's post-death deed was hidden. Condo purchase deeds often sit on the OLD record lot (1391: lot 0138 vs roll 2155). → prospecting
- **The RoW "owner" field is not always the PR; the DC probate name collision** (record 6254b0aa) is still wrong — check Estate Search + deed. → memory
- **Socrata refuses ANY function syntax in `$where`** (`upper(` = 403). `aca-prod.accela.com/FREDERICK` is Frederick, COLORADO. DC `PERMIT_APPLICANT` is the EXPEDITER, not the contractor; `ZIPCODE` is null on every row. → permits

- **The SDAT results grid's first `<a>` in a layout row is the header SORT link;** `a.click()` posted the grid back sorted and every multi-row street search read "no parcel" (38 of 185 MD lookups). Leaf rows only, the row's `a.lnkdetails`, post back through `__doPostBack`, wait 7 s. A condo grid lists one row per unit (`UNIT: 307`); without the unit guard the first row is a neighbour's unit. → prospecting
- **MDLANDREC demands the emailed access code when several Firecrawl sessions log in at once** (4 workers: 1 in 4 searches dead, then every login); one login at a time stays clean. Parallel agents reach it ONLY through `landrec_agent_search.py` (machine-wide lock + daily cap). → prospecting
- **Estate Search by decedent NAME returns namesakes decades back** (8 of 12 single-name hits were 1980-2016 estates on 2026 inheritance rows); a name-only hit joins only within 3 years or ±45 days of a research DOD, the rest is review. Fields `#txtLN` / `#txtFN` / `#cboPartyType`, not `#txtLastName`. → prospecting
- **`title_for_md` read keys the SDAT parser never wrote** (`owner_name`, `mailing`) and would have stored every MD title with an empty owner list; the parser's keys are `owner_names` / `mailing_address` / `premises_address`. → prospecting

### DP batches and pushers
- **A dead LLM looks exactly like "no obituary found."** `llm_client` swallows every exception and returns `None`; 52 records froze as false `unresolved` on an exhausted credit balance. A `None` is an error, never a non-match; `--max-consecutive-errors` aborts. Auto-reload takes ~10 min to settle. → prospecting
- **The spouse trap:** the obituary is the spouse's, the owner is ALIVE (22 catches; `given_name_differs`). Strip honorifics first ("Capt. Roberta J. Grace, USN" IS Roberta Grace). → prospecting
- **The namesake-child trap:** SmartSkip returns the owner's namesake SON and the ranker re-roots his siblings as the decedent's children. Compare BIRTH years (gap ≥18 = parent/child); 23 `Deceased Owner` records were wrong this way and repaired. → prospecting
- **A legacy.com / dignitymemorial / tributearchive title city is the FUNERAL HOME's,** not the residence (8131 Turn Loop Rd). Only body text ("of <City>") is residence. → prospecting
- **Nicknames:** `Gail` = Abigail (485 Ferry Point: a dead owner was called alive). `names_match` stays strict; `names_match_loose` only when the pairing is unambiguous (Chris/Christine would drop a LIVING signer). → prospecting
- **SmartSkip lists the same person on several rows** (101 of 619 records had one person in two REL fields) and **dedupes its INPUT by NAME, not property.** Dedupe relatives; fan same-owner results across records. → prospecting
- **Phone choice must sort by TIER in `cmd_build`, never at rank time** (538 Dial First numbers were parked), and `cmd_score` must price EVERY number on a kept contact — an unscored number fails `_keep_number`. Two-pass allocation: every contact its best number, owner first. → prospecting
- **The plan files are the source of truth;** another session's `push --force` reverted 9 live REL writes within minutes. Patch `push_plan.json` first and check `Get-CimInstance Win32_Process` for a running pusher before any per-record account fix. → prospecting
- **Confirm a one-source name before writing it** (10 of 52 resolver names did not survive Trestle reverse). Trestle reverse holders are under `owners[]`, not `belongs_to`. Trestle Reverse Phone runs ONLY on an explicit per-batch go. → prospecting
- **Unchecked is not clean (DNC).** `dnc_client`: a DNC hit never expires and no source clears it; Tracerfy's `dnc` flag, a DataSift `*DNC` status and Smarter Contact's blocked export only ADD (its Carrier Blocked / Undeliverable rows are NOT DNC); "clean" = the paid vendor said so within 31 days, anything older is stale. A vendor error raises, never returns clean. `dial_ok` in `dnc_review.csv` is the only green light. → prospecting
- **A plan that drops EVERY relative must still run the REL blanking step** (it was gated on `custom_fields`), and a shared-line ghost survives the number rule: after ANY owner-alive re-push run `obituary_dp_qa.py --repair --clear-extra-rel --extra-source <replaced plan>`. → prospecting
- **`cmd_research` DROPS the LLM's `age_at_death` and `city`;** no DP file holds them. The free source is the search hit title + snippet in `trail[0].hits[]`. → prospecting
- **PR goes to the PR fields, never a REL slot; in-laws never in REL slots** (`--channel-cap 0`); DP never changes an existing note, it adds its own. → memory
- **The Tracerfy queue was read early** (fixed `ad65905`); 305 numbers are still missing on 77 live records, repair on go. → memory
- **The DP input DROPPED the vendor's date of death, case number, middle name and PR address,** so the research searched `"David Smith" obituary Washington`, judged three namesakes and wrote "DOD unknown" on a record whose raw file said 08-13-2026 / 2026-ADM-001053 / David Beardsley Smith. `anchors` now joins the raw weekly CSVs, Sheet26 and the RoW ledger (+ Estate Search by `#txtEstateNo`) before `research`. → prospecting
- **A "SIGNER" line was a SmartSkip relationship LABEL, not a finding** (1,138 of 1,496 on the Sept 646; 179 UNRESOLVED records printed "all children must sign"). The judge also answered "high" on a Gaffney SC obituary for a Herndon owner and on five obituaries dated 2019-2024 against 2026 filings. `_verify_anchors` rejects a match that contradicts the filing DOD by >45 days, needs an anchor hit or a location match when a strong anchor exists, and the note now prints `SIGNER (obituary)` / `SIGNER (label only)` and a "death UNVERIFIED" basis on unresolved records. → prospecting
- **The DP research never reads the DC probate portal's PARTY list,** which names the PR and every legatee with an address (`dc_probates_portal.json`), nor the ITSPE co-owner: 1391 Pennsylvania #433 printed "heirs NOT identified" while the court listed five legatees and a co-owner Helen H Smith absent from the case; a SmartSkip "namesake" set-aside (subject row with no first name) hid a court-listed legatee. Join the DC ledger before `research`. → prospecting
- **`output/dc_probate_ledger.json` has ONE top-level key, `estates`;** counting it as "1 estate" hid 273 cases with full party lists (a superset of the 8 daily portal files). Read `["estates"]`. The v5 court join (`dp_court_parties.py --ledger`) uses it; four "missing" cases were there all along. → prospecting
- **The note cap of 1,990 chars was the dead `add-notes` route's;** the message route the pusher uses posts `note[:4000]` (1391's 2,515-char note stored and read back). `NOTE_MAX` is 3,900 since 2026-10-02; a v5 note with TITLE / DEED / COURT PARTIES needs it. → prospecting
- **The SmartSkip subject row has NO first name on every record of the Sept cohort** (53 of 53 DC), so "household subject at the address" is SmartSkip's shape, not a per-record finding; only its AGE and lines say anything (a 37-year-old at a 90-year-old decedent's house is an occupant / candidate co-owner). → prospecting
- **A probate record's address is the decedent's RESIDENCE, not property the estate owns, and the case on it can be a TENANT'S or a RELATIVE'S** (DP V5 0210, 2026-10-02: 6 of 9 DC case numbers were not the owner's - a Foundry Lofts renter, a DCHA tenant, a house sold in 2003, a sister's SC estate; the vendor matched RoW W125319 to 7113 Panorama on the surname alone - it is the NYC puppeteer Ralph Minor Lee, not Rockville's Ralph L Lee). Title + the party list decide, never the address; a portal NAME search found the owner's own 2024 estate the record never carried. Headless Parties = HTTP 500; run the case lookups `--headed`. → prospecting
- **The DP `build` lane rule judged entry tags on the PLAN'S additions, not the record's EXISTING tags,** so a `Priority 1` SiftMap record read as "no entry tag at all" and was handed `Priority 2` (9 of 11 on 2026-10-05; caught in the dry run, fixed: the rule now sees prep's Tags column and the plan carries only what it adds). Probate-list records still get `FTM` by the rule. → prospecting

### Mailing lists
- **Fuzzy address matching must be UNIT-aware:** house-number + first-token matched 13 different condo units in one building. → mailing
- **A BACKUP copy of `ranked_records.json` can out-score the live file.** The heir index keeps the copy with MORE signers, and a pre-repair `*_backup_*` / `*_correction_*` copy has more (the false ones were not yet removed); skip those paths (`heir_mail_build.BACKUP_PARTS`). Same for `stage_c_research.json`: a correction BLANKS `decedent_name`, so "named finding beats unresolved" picks the stale one -- corrections first, then newest. → mailing
- **SmartSkip's "Spouse" row on a surviving-spouse household IS the dead owner,** and the ranker scores it as a signer (17 envelopes addressed to the decedent, 2026-10-06). Strike a spouse-type signer whose first+last is the decedent's. **SiftMap `last_obituary_date` is the DRIP date, not the death** (23 records: 2026-07..09 over a 2010-2025 DOD, 4 exactly one year off); date by the research DOD. A clerk-list row (Drive Inheritance / Probate) was MAILED even if never uploaded: its origin list is its `lists`. → mailing
- **The CRM's `directmail_attempts` is not the mailed signal.** Inheritance / Divorce / Probate list = mailed regardless of counter; Register of Wills only WITH an attempt; the Drive SENT lists are authoritative (61 of 146 drops were found only there). `obituary_mail_consolidate.py build` refuses to run without the index. → mailing

### Wizard, UI automation, imports
- **DataSift inserted an Enrichment step into the wizard** and every later locator ran one step off. Count steps on the live wizard before trusting the script. → datasift-ui
- **The wizard CANNOT update fields on existing records;** use the API. Its Add Tags box used to OVERRIDE the CSV's own Tags column (now `custom_tag`). → datasift-ui
- **The wizard writes NO board note and no PR fields,** so every vendor list it uploads leaves the owner block unlabelled (decedent? PR?). 409 of Bagoury's 836 dial-preset records had no pinned note on 2026-10-05; only the API pushers (DP, verified import, SiftMap pull) pin one. Audit: `dialer_board_audit.py`. → datasift-ui
- **`datasift_api_upload.FIELD_MAP` is Ty's TN schema:** `Notice Type`, `County`, `Decedent Name` do not exist here; `field_pairs` skips them silently unless the caller prints `warn`. The decedent belongs in the board note. → datasift-api
- **Beamer iframes (`#npsIframeContainer`, `#beamerPushModal`) block ALL pointer events** — remove from DOM. Dismiss the tag dropdown by clicking the heading, NOT Escape (clears the tags). Market Finder's ZIP/Neighborhood toggle flips AWAY if already on the wanted view. → datasift-ui
- **Market Finder has no ZIP split for DC;** a 0 there is missing data, not a zero market. → memory
- **Upstream's city search and `.get(county, "47093")` silently queried Knoxville for every MD/DC/VA county** — retired; counties resolve by FIPS through `dpd.jurisdictions` and RAISE. → sold-suppression
- **`output/_archive/live_account_pull.json` must be passed explicitly as `--cache`;** `live_pull.py` otherwise starts a multi-hour re-pull. → doors-per-deal

### Team KPI sources
- **SmrtPhone `/api/v1/logs/segments` stops at 10,000 rows for ANY query and silently ignores every date parameter tried** (an unfiltered walk reaches ~3 weeks back on a 191k-segment account). `user_id` IS honoured: walk one caller at a time, newest first, to the period start (`src/smrtphone_segments.py`). → docs/api/smrtphone.md
- **SmrtPhone stores some outbound calls with NO user (`user_id` null)** - a per-user walk never sees them (Bagoury's 4m20s Correct Number call behind the 10-04 Doyle lead; the legacy Call Logs page shows it as 0 s). Credit them by caller-ID number. About half of segments also return `contact = null`: match those by dialed number through the DataSift `search` (surname check). → docs/api/smrtphone.md
- **Hubstaff activities / screenshots reject any range over 7 days** (HTTP 400 "Date range can not be more than 7 days"); ask week by week. → src/scripts/team_kpi_screens.py
- **A Hubstaff slot can hold one screenshot PER MONITOR taken at the same instant** (Mostafa: 2560 main + 1080 portrait). Judging one monitor read ~31 h of "blank desktop" in September; judge every monitor (any work = work). → src/scripts/team_kpi_screens.py
- **numpy's OpenBLAS dies at import when the Windows commit limit is nearly full** ("Memory allocation still failed after 10 retries", the 10-07 20:00 run; openpyxl and the shared DataSift helper import numpy). `OPENBLAS_NUM_THREADS=1` is set in the runner and the scripts. → tools/manual/team_kpi_daily.ps1
- **A Hubstaff personal access token ROTATES on every exchange;** the `.env` seed dies on first use and the live token is in `hubstaff_state.json`. Cloudflare 403s (error 1010) Python's default User-Agent before the token is even read. → src/hubstaff_client.py

## DataSift API — the contract in brief

Full text and every measured route: [datasift-api.md](docs/history/datasift-api.md).

- **Auth:** mint the JWT (`POST /api/token/` with `DATASIFT_EMAIL` / `DATASIFT_PASSWORD`; re-mint
  every 30 min), or `authorization: Api-Key <REISIFT_API_KEY>`. Both reach `/api/internal/`,
  where custom fields and every write live. Never paste or hardcode a Bearer. The Open API
  surface (93 public routes) has no custom fields and 401s/403s on writes.
- **Proven routes (each with read-back):** create `POST property/` (internal); `PATCH property/{uuid}/`
  for `tags` (merge), built-ins, `assigned_to`, `owner` block; `POST property/{uuid}/add-lists/`,
  `remove-lists/`, `remove-tags/`; `PATCH property/{uuid}/custom-field/update-values/`
  (`[{"field_uuid", "value"}]`, `""` blanks); `POST owner/{uuid}/upsert-phones/`,
  `remove-phones/`; `PATCH owner/{uuid}/` (`skiptraced`, `skiptrace_attempts`); `POST
  property/{uuid}/message/` + `message/{id}/pin/`, `PATCH message/{id}/` (text); `GET
  property/{uuid}/logs/`; `GET task/?ordering=created`, `POST task/{uuid}/complete/`;
  `GET account/user/?limit=999` (uuid → name, filter `is_active`); `GET custom-fields/`,
  `status/`, `tag/`, `filter-preset-folder/?limit=999`; `POST/PATCH filter-preset/` (store
  read-back); SiftLine `siftline/board/…` cards (`{"prop": uuid}`); SiftMap
  `map.reisift.io/filters/` GET + PATCH.
- **Counts and membership:** `POST property/` with `x-http-method-override: GET` and
  `{"query": {"must": {...}}}`; `property_type: incomplete` for the Incomplete tab;
  `"ordering": "created"` honoured.
- **Always upload ONE record and read it back before releasing a file.** That habit caught every
  trap in the list above.
- **No proven API yet (Playwright):** the upload wizard, Send To → Skip Trace, Enrich, the sequence
  builder, SiftMap Add Records, saved-filter deletion.

## DataSift UI and wizard in brief

Full patterns: [datasift-ui.md](docs/history/datasift-ui.md). Wizard (build 1.0.46): Setup →
**Enrichment** → Tags → Upload File → Map Columns → Review/Finish; only the core address fields
auto-map. Post-upload the pipeline runs Enrich (Enrich Owners / Swap Owners OFF) and Skip Trace
(wizard tag box types `REISift Traced MM/YYYY`). Styled-components everywhere (no native selects;
React state needs the native setter + events); the filter panel scrolls as a div (JS
`scrollIntoView`); sequence builder cards need a slow mouse drag; `Sold Property Cleanup` is live
with 3 of 4 actions (`Remove Lists` never landed). Login: click `<label>`s, `domcontentloaded`,
check for `/dashboard` or `/records`.

## REI Skill Library (25 skills)

`skills/manifest.json` is the source of truth (25 current + 2 superseded, `source_dir` per entry);
each `.skill` / `.plugin` is a ZIP with `SKILL.md` + `references/`. Distributed via
[learn.datasift.ai/claude-skills-rei](https://learn.datasift.ai/claude-skills-rei); bundles carry
fictional example data and placeholders, never account data or internal API routes. Constants
that must stay identical across skills: phone tiers 81-100 / 61-80 / 41-60 / 21-40 / 0-20; comp
adjustments bed $5,000, bath $7,500, $85/sqft, age $500/yr; HML 12% + 2 points, conventional 7%,
2.5% closing; `MAX_DOD_GAP_YEARS` 3; 7 notice types. Inventory and corrections:
[skill-library.md](docs/history/skill-library.md).

## My Defaults

- **ALWAYS STAMP THE SKIP-TRACE DATE (Basem, 2026-09-22). Non-negotiable.** Every record that
  gets numbers loaded carries its trace-date tag in the SAME operation, on EVERY path (wizard,
  SmartSkip, IDI, any API upsert), plus `PATCH owner/{uuid}/ {"skiptraced": true,
  "skiptrace_attempts": 1}`. There is NO date field; the tag is the only record of when.
  Backfills stamp the month the TRACE happened. One tag per SOURCE (2026-09-30):
  | Source | Tag |
  |---|---|
  | DataSift skip trace (wizard) | `REISift Traced MM/YYYY` |
  | SmartSkip (DP batches) | `SS Traced MM/YYYY` |
  | IDI data from the clerk | `IDI Traced MM/YYYY` |
  DataSift's own `REISift Skipped` chips stay. Older tags (`Skip Traced MM/YYYY`, `Skip Traced
  IDI …`, `skip_traced_…`) are history: never create more, never retag without a go. Anything
  that CHECKS for a trace must accept every prefix (`skip_dp_census.SKIP_TAG_RE`). Trestle is
  scoring, not a trace. A Tracerfy-only trace has no tag yet: ask.
- **ALWAYS LITIGATOR SCRUB (Basem, 2026-10-05). Non-negotiable, never waits for a go.** Every
  Trestle call carries `add_ons=litigator_checks` (hard-wired in `phone_validator.call_trestle`
  and every other call site; `--no-litigator` / `--litigator` are no-ops) and a number with
  `phone.is_litigator_risk == true` is dropped before it reaches a record. A Trestle cache entry
  with `litigator: None` means UNCHECKED, not clean: re-score it. The add-on's per-call cost is
  pre-approved. Trestle covers litigator risk; DNC / opt-out are DataSift's own fields.
- **DNC CHECK — PARKED (Basem, 2026-10-05, same day).** Kixie (the next dialer, per Moe's action
  items) scrubs DNC on calls and Smarter Contact scrubs it on texts, so the checker below may never
  be needed: do not run it, wire it into a routine, pick a vendor or spend on it. As built:
  `dnc_scrub.py` (agent `sift-dnc-checker`, cache-only) on
  every dial sheet; scorers turn a hit into the `DNC` tag (never dialled; Litigator Risk keeps its
  own tag). **Numbers go out through Smarter Contact (2026-10-05), which scrubs DNC / opt-out /
  litigator at send time;** feed its blocked-numbers export back with `--sc-blocked` so DataSift and
  sheets dialled outside it see the hits. The paid vendor scrub (`--spend`) and the DataSift status
  write (`dnc_status_push.py`) run only on an explicit go. `docs/api/dnc_vendors.md`.
- **Footprint:** the 6 counties, everywhere (pulls, presets, SiftMap filters, sold sweep).
- **Lane tags:** probate / daily pulls = `FTM`; SiftMap rungs = `Priority 1/2`; obituary records =
  `Obituaries` ONLY (an obituary record that is also a probate case keeps FTM).
- **Which source wins on a record two pushers both load:** the richer one; append, never blank.
- **DP batches:** never change an existing note; PR to the PR fields; no in-laws in REL slots;
  Trestle every loaded number (merge vendor IDI before score/build); Trestle Reverse Phone only
  on an explicit go; no obituary-window filter on DP cohorts.
- **Assignment:** after skip trace, split evenly across the 5 dialers, group by OWNER before
  leveling, level-up only.
- **Mailing:** screen every list against the mailed-history index first; one envelope per door.
- **Daily summary channel:** WhatsApp (not wired; only `SLACK_WEBHOOK_URL` exists). Preferred run
  time 06:30 America/New_York. Dispositions: the type-based lists (Foreclosure, Probate, …).
- **Git:** this fork only (`basem/work`, origin Bsanad90), never Ty's main. One step at a time;
  define jargon. Flag long sessions and propose a handoff after one major arc.

## Where the DPD code stands

Detail, every pass and its backups: [doors-per-deal.md](docs/history/doors-per-deal.md).

- `src/dpd/preset_spec.py` — the 85 presets as data (`N_PRESETS`, 12 DPD folders + Obituary
  13/14), `DEAD_STATUSES`, `SUPPRESS_TAGS_CALL_ONLY`, `OBIT_WINDOW_KEY/VALUE`,
  `NO_OBIT_WINDOW_FOLDERS`, `resolve_relative_date`. `block_map.py` — panel mapping + `GAPS`.
- `dpd_presets_verify.py` — reads every stored preset and checks it against the spec; exit 0
  only at 85/85. **Currently reports exactly the 4 known kept extras and nothing else** (2026-10-06):
  FTM 02 + Obituary 02 exclude `Re-Skiptrace`; Deep 01 + Deep 02 carry `Obituaries` as a 5th
  include tag (Deep 01's appeared between 10-01 and 10-06, hand-added). Run after ANY preset change.
- Preset passes, each with backup + `--undo`: `dpd_presets_create.py` (builder),
  `dpd_presets_one_owner.py`, `dpd_presets_obit_window.py` (add OR remove per spec),
  `dpd_presets_entity_exclude.py`, `dpd_presets_tag_exclude.py` (syncs EVERY preset's tag
  exclusions to the spec, budget-aware; `SUPPRESS_TAGS_ALL` = `Recently Sold`),
  `obituary_lane_build.py` (`presets` / `retag`), `dpd_split_test_presets.py` (folder 15, A/B/C
  off Hottest - 02; plan / `--commit` / `--undo` / `--bar`).
- SiftMap: `dpd_siftmap_filters_api.py` (GET + PATCH, backups), `dpd_siftmap_manifest.py --plan`
  (keep / stale / rename / missing planner, P1 only, `list_key` identity), `dpd_siftmap_presets.py`
  (management page: audit + gated delete), `dpd_playbook_extract.py` (Work Order export only).
- Lanes: `dpd_lane_assignee_report.py` (`to_query`, counts), `dpd_lane_rebalance.py`,
  `dpd_owner_group_assign.py`, `lane_incomplete_cleanup.py`, `reskiptrace_*.py`.
- **Stated gaps (`block_map.GAPS`, 2026-09-30):** now expressible but not rebuilt — the mail
  30-day spacing and the Reactivation status-age timers (relative dates + `last_updated_date`
  on `status` exist in the store; read the PRIOR control's meaning off `main.min.js` first).
  Still gaps: the single-family filter (applied at pull time), 19 of 38 statuses in the panel.

## Standing constraints

- Create no Lists (a SiftMap pull applies tags, not lists). AI data is not bought.
- The API is the default write path, one object at a time with a read-back. Collection-level
  routes and `POST property/skip-trace/` are account-wide: never as probes.
- Never click `Add Records to Account`, never turn on SiftMap auto-add, never push a daily pull
  or a DP batch to DataSift, never spend (SmartSkip, Trestle, Firecrawl beyond a probe) without an
  explicit go. Dry-run first, probe one record, then commit.
- Priority 1/2 are stamped at pull time; FTM by the daily pulls; `Obituaries` by the obituary drip
  and the retag. Entry tags never move without a go.
