# MD / DC / VA Public-Notice Pipelines

> Build history extracted from `CLAUDE.md` on 2026-08-28. Verbatim — the API
> contracts, traps and measured numbers here are the record. Live state and next
> steps live in `CLAUDE.md`.

---

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


## 2026-08-28 — MD Probates / MD Legal Notices fixes, SDAT by street, VA multi-county + pagination fixes

Basem: "run 2, 3, and 5 — do not upload to account, let me check the results first before uploading or skiptracing." Nothing touched the account. Everything below landed as code + local files for review: `output/data_extraction_test.xlsx` (rebuilt by the new `src/scripts/data_extraction_test_build.py`), `output/Register of Wills REVIEW 08-26-2026 Anne Arundel.csv`, `output/md_row_review_08-26-2026.json`, `output/va_estate_claims.csv`, `output/va_tax_deeds.csv`.

### Item 2 — MDDC now stamps `FTM`

`mddc_datasift_upload.BATCH_TAG = "FTM"` (was `Claude first batch 8.22`). The uploader splits the Tags column on commas, so a provenance tag can be appended later if wanted. Not run.

### Item 5 — MD Probates + MD Legal Notices (`md_register_of_wills_pull.py`, `md_land_records_lookup.py`)

- **`--date MM/DD/YYYY`** pulls exactly one filing/publication date (shorthand for `--since D --until D`); **`--dump-json PATH`** writes the run's records (only the estates this run touched) for the workbook builder. The checkpoint can no longer move BACKWARDS on a `--commit` of an older `--date`.
- **SDAT is queried by STREET ADDRESS, never by tax account** (Basem 2026-08-27). `lookup_sdat_by_address()` uses search type `01` with `txtStreenNumber` (SDAT's own typo) + `txtStreetName`; the street name must be given WITHOUT its suffix (`8590` + `OAK HILL` finds the parcel, `OAK HILL DR` returns nothing — confirmed live). A multi-match grid is clicked by row text; a direct hit is a no-op. The deed's tax id and the Remarks account are still parsed and RETURNED for reference, never used to search (this also makes the unfinished Account-Identifier "Subdivision" field moot).
- **The deed's OWN address phrasing is mostly `"The improvements thereon being known as No. 123 Example Drive."`** — bare number + street, no city/zip — on real Anne Arundel deeds (1979, 1983). The labeled `"has the address of: ..., Owings, Maryland 20736"` form is the exception. `DEED_KNOWN_AS_BARE_RE` captures number + street ending in a suffix; the county is known and SDAT supplies city/zip. Before this, 4 decedents had readable deeds that "stated no address".
- **SDAT's current owner decides the verdict, and the first pass called two strangers HIGH.** A first+last-only overlap scored "JUAN CARLOS SAMPLE MORALES" = SDAT's "PEREZ MORALES JUAN DANILO" as 1.0. `name_match_level()` now uses the FULL name: HIGH = every token of the decedent's name is on the owner (a bare initial on the owner side credits the matching middle name: "SAMPLE WILLIAM H" vs "WILLIAM HENRY SAMPLE" is HIGH), MEDIUM = surname + first name only (a human decides), LOW = surname only = not the decedent. LOW on a deed address means **the decedent no longer owns it** (deed 1983 → SDAT owners Doeburn; deed 2008 → Roeman/Collins): the loop keeps looking at older deeds and, if nothing confirms, reports the mismatch with the SDAT owner instead of the address. An address SDAT cannot find ships as MEDIUM `sdat_unconfirmed` with the reason.
- **`MAX_DEED_CANDIDATES = 5`**, newest first. One common-surname decedent had 31 deed rows and ate 15+ minutes at ~40 s per candidate (Land Records login + PDF fetch + SDAT per deed).
- **Legal Notice parser: two real notice shapes were silently dropped.** (1) Co-PRs: `"A WHOSE ADDRESS IS X, B WHOSE ADDRESS IS Y WERE ON <date> APPOINTED"` — the regex demanded one pair + `WAS ON`; `_split_pr_pairs()` now yields every PR and they land in `personal_reps` (PR 2/3 columns). (2) **Foreign personal representative notices** (`NOTICE TO CREDITORS OF APPOINTMENT OF FOREIGN PERSONAL REPRESENTATIVES`, decedent domiciled out of state) use a different sentence AND print the Maryland property outright: `"THE DECEDENT OWNED REAL OR LEASEHOLD PROPERTY IN THE FOLLOWING MARYLAND COUNTIES: ANNE ARUNDEL / 123 EXAMPLE DRIVE / ODENTON MD 21113"`. Parsed into `notice_property`, applied as the property address (source `legal_notice_text`), then SDAT-checked by street for the owner. `AKA:` tails are split into `decedent_aka`. DOD normalised to MM/DD/YYYY on both sources.
- **Name/address splitting bugs found by the same pull:** the detail page's `FIRST MIDDLE LAST SR` was split as last name `SR` (Land Records then searched surname "SR"); `split_last_first_name` now uses the suffix-aware splitter. `GLEN` is a street suffix AND the first word of Glen Burnie, so "345 EXAMPLE RD GLEN BURNIE" became street "…RD GLEN" + city "BURNIE" — `split_street_city_tokens()` prefers the last STRONG suffix when the last suffix is a weak one (GLEN/POINT/MANOR/RIDGE/RUN…), backs off when the only suffix is the final token ("5 SAMPLE RUN SPARROWS POINT"), and keeps a post-directional with the street ("MAPLE ST SE GLEN BURNIE"). Shared by the PR-address, SDAT-premises and notice-property parsers.
- **Result on Anne Arundel 08/26/2026 (dry run, 15 estates: 5 Estate Search + 10 notice-only):** 7 with a property address (2 HIGH, 5 MEDIUM incl. 3 SDAT-unconfirmed), 2 explicit "sold since deed" mismatches, 6 with no deed on the grantee index at all. Firecrawl was degraded all day (repeated 500s), so PDF-text availability varied between identical runs.
- **Review workbook builder** `data_extraction_test_build.py --md-json … --va-estate-claims … --va-tax-deeds …` rebuilds the MD Probates / MD Legal Notices tabs (PR1–PR3 in separate name/first/last/street/city/state/zip columns, `published_on`, property + SDAT owner + confidence + reason) and adds VA Estate Claims / VA Tax Deeds tabs; MDDC and VA (Foreclosures) tabs are left as they were.

### Item 3 — the two VA pulls, and what running them found (`va_trustee_sale_pull.py`)

The first run "succeeded": Estate Claims wrote 4 rows from Richmond/Albemarle/Botetourt, Tax Deeds wrote 0 rows after dropping 30 "foreclosures". Both were statewide, all-category grids. Step-by-step form-state probes (scrape after every action) established the mechanics:

- **Changing the Popular Search dropdown and clicking Go are FULL navigations to `Search.aspx`**; the ~2.7K "empty shell" seen on more than half of earlier attempts is a scrape taken mid-navigation, not a render flake. With Firecrawl slow, a fixed 3 s wait left the county clicks running against an empty document. `build_actions` now stamps `body[data-dpd-pre]` before each navigation and waits for `body:not([data-dpd-pre]) #…ddlPopularSearches` (the NEXT document, with the form).
- **The county CheckBoxList is an AutoPostBack control inside the `as1` UpdatePanel** (listed in the page's `PageRequestManager._initialize`). Every click is its own async postback and ASP.NET aborts the in-flight one when the next starts, so five clicks in one JS burst cancel each other: Foreclosures + Arlington alone → 10/10 Arlington rows with box 6 checked on the results page; the same code with five counties → statewide grid, no box checked. Setting `checked=true` WITHOUT the click event does nothing (the server only learns a county from its postback). The single-county case works for every category (Estate Claims + Arlington → 8 probate rows, all Arlington).
- **A heavy query blows Firecrawl's per-request budget.** Estate Claims + Fairfax + 60 days → `SCRAPE_ALL_ENGINES_FAILED` after ~130 s; the same over 7 days → 23 s, 10 Fairfax rows. So `collect_pages()` now runs **one county per Firecrawl call** and **splits the date window in half on failure** down to `MIN_SPLIT_DAYS = 7`, and every results page is checked with `counties_not_applied()` (the requested box must still be ticked on the results page — the server's own statement of what it filtered on). A window that still fails at 7 days raises `UntrustworthyResult`; a partial pull is never written as complete.
- **Pagination had NEVER worked.** The next-page click targeted `#…WSExtendedGrid1_GridView1_ctl01_btnNext`; the results grid is `WSExtendedGridNP1`. Every VA run to date captured page 1 only (10 rows) — the recurring "pagination did not advance" warning was this, not a missed click. Fixed. The grid is inside `updateWSGrid` (async), so page clicks keep a fixed wait.
- **"Tax Deeds" is a keyword category**: Tax Deeds + Arlington returned 9 trustee sales (every "Deed of Trust" matches). The `SEARCH_NOTICE_TYPE` filter (keep `tax_sale`/`other`, drop `foreclosure`) is what makes that category usable; expect few rows.
- Estate Claims rows carry no per-decedent parse yet (the snippet is a "Notice of Taking Proof of Debts and Demands" listing several estates); they are delivered as notices for review, not as records.
- **Session paused 2026-08-28 ~13:20 before the fixed run finished.** The fixed script was verified piecewise live (single-county filter correct for categories 4/6/8; Fairfax 7-day window OK; the `NP1` pager id read off two real results pages) but the full 5-county Estate Claims + Tax Deeds run with all fixes together was interrupted. `output/va_estate_claims.csv` (4 rows) and `output/va_tax_deeds.csv` (1 row) on disk are the pre-fix, statewide, page-1-only outputs. Re-run per the Order of work in CLAUDE.md; expect ~10 Firecrawl calls per category (5 counties, Fairfax split into 30-day halves) at 25–130 s each.

## External Scheduled Task: MD Cases (outside this repo, 2026-08-24)

Not part of SiftStack's own automation (no cron/scheduler in this repo runs it) — flagged here only so a session working in this directory knows it exists and what its failure looks like. Referred to here as **"MD Cases"** (the live Windows Task Scheduler job is still literally named "MD Courts Daily PDF Pull" — that name predates this doc's rename and hasn't been touched on the machine). It runs `Galal Development\automation\daily_mdcourts_pull.ps1` daily at **9:00 AM America/New_York** (machine-local). It downloads that day's `fileYYYY-MM-DD.pdf` from `https://www.mdcourts.gov/data/case/`, retrying hourly up to 9 attempts (9am-5pm) if the file isn't published yet, into `Galal Development\Divorce Cases\`, then launches `master_watch.bat` (which starts `watch_all_case_pipelines.py`, the Shared Case Watcher) unless it's already running. The PDF carries divorce, comptroller, and residential foreclosure filings in one daily feed; this feeds the divorce-case pipeline, a sibling to (not part of) the MDDC trustee-sale/foreclosure pull.

**Failed 2026-08-24 09:00 with a DNS resolution error** (`The remote name could not be resolved: 'www.mdcourts.gov'`), most likely the machine's network wasn't fully up yet right at the trigger. Two things made this harder to catch than it should have been, both visible in `automation\daily_pull_log.txt`:
- The task's own exit code was **0 (success)** even though no PDF was ever saved for the day — `LastTaskResult` in Task Scheduler is not a reliable success signal here. **Ground truth is whether `Divorce Cases\file<today>.pdf` exists**, not the task result or the log's tone.
- The script's own retry-loop and per-attempt log lines (`"Saved ..."`, `"Attempt N/9: not available yet..."`) did not reliably appear in the log even when the download itself worked (confirmed on the manual re-run same day: the file downloaded correctly but neither the "Saved" line nor any retry line was written) — treat `daily_pull_log.txt` as a partial trace, not a complete one, when diagnosing a miss.

**Recovery is just re-running the script**: `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "Galal Development\automation\daily_mdcourts_pull.ps1"` — safe to run any time, it no-ops (`"Already have..."`) if today's file is already saved, and skips relaunching the watcher if it's already running.

---

## CLAUDE.md "Order of work (from 2026-08-28)" — moved here verbatim 2026-09-30

Retired from CLAUDE.md as no longer current (Basem, 2026-09-30). Item 3 is the VA parsing record; the rest is DPD build history.

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


---

## Moved from CLAUDE.md on 2026-10-01 — MDDC Trustee's Sale, VA Public Notice, cross-day full-text reuse, MD Register of Wills — current state as of 2026-10-01

Verbatim text as it stood on 2026-10-01 (second split of CLAUDE.md; the first was 2026-08-28). The live current state and the traps index stay in CLAUDE.md; this is the record. Snapshot: `_CLAUDE-snapshot-2026-10-01.md`.

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
  count. `--hide-read-notices` defaults OFF as of 2026-08-31.
  **COUNTY CHECKBOXES ARE INERT — measured 2026-09-15.** A search with ONLY "Washington DC" ticked
  returned 89 rows spanning **18 counties**, Virginia ones included (Loudoun, Culpeper, Stafford,
  Prince William, Alexandria city). The site returns the whole MD/DC/VA feed whatever is selected;
  the `county` column is DERIVED afterwards from notice free-text plus ZIP geocode. So narrowing
  `DEFAULT_COUNTIES` (`be69672`) never narrowed the SEARCH — it only changed which inert boxes get
  ticked. Footprint filtering is post-hoc and STATE-based, not county-based.
  **The "pagination did not advance" warning is usually a FALSE ALARM.** The site publishes only
  ~3-4 trustee notices/day in total, so a 7-day window genuinely ends after ~3 pages and the
  warning fires whenever the result set is shorter than `--max-pages`. Read the ROW COUNTS instead:
  a short final page (10, 10, then 8) is end-of-results, not a stall. A 30-day window walked all
  10 pages cleanly. Do not "fix" this without checking the counts first.
  **Consequence for DC:** DC runs ~18 notices per 30 days (~4/week), so a 7-day window can contain
  ZERO DC by chance — it did for the whole of 09-11..09-15, which looked like a broken county
  filter and was not. `--days 30` surfaced 25 in-footprint leads, 13 of them DC. Prefer a wider
  window over trusting a quiet week.
  **DC probate ownership (`dc_probate_pull.py`): ITSPE *is* OTR** ("Integrated Tax System Public
  Extract", served off DCGIS), so re-querying OTR cannot resolve a MEDIUM owner match — MEDIUM
  means the NAME match is partial, usually just an extra middle initial or maiden name on our side
  (`Michael L. Joyner` vs OTR `JOYNER, MICHAEL` is the same person). The next source up is the DC
  Recorder of Deeds, which is still an unfinished signup. A residence owned by someone whose
  SURNAME matches the decedent is a lead, not a dead end — an heir already on title.
  → [history](docs/history/mddc-va-md.md)
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
- **Cross-day full-text reuse (`--prior`, both fulltext legs) — BUILT + validated live 2026-09-21.**
  `mddc_fetch_full_text_browser.py` and `va_fetch_full_text_browser.py` re-fetched the WHOLE 7-day
  window every run (87 MDDC Turnstile solves, ~53 VA Scrapfly replays at ~65 s each, the VA leg ran
  ~60 min) although the sites add ~3-4 notices a day. Notice ids are stable across days (77/77 MDDC
  ids on 09-21 were in 09-20's file) and a republication carries a NEW id, so `src/scripts/fulltext_prior.py`
  keys reuse on `notice_id`: `--prior <path>` (repeatable, globs; a previous `_full.csv` or the new
  `<stem>_details.json` sidecar) or `--prior-auto` (scans the sibling `daily_pulls/YYYY-MM-DD/` dirs,
  14 days back by default -- needed because the Tue-Sat schedule makes "yesterday" wrong on Tuesdays).
  Reused text passes the SAME street guard as fetched text and `auction_date` / `loan_principal` are
  ALWAYS re-parsed from the text, never copied. The sidecar holds EVERY resolved detail, including grid
  rows that match no CSV row (13 of 53 VA fetches on 09-21 were such rows and were re-fetched daily),
  and MERGES on write so the VA `--fill-missing` pass cannot shrink it. Republications are now skipped
  BEFORE the fetch (they used to be fetched and discarded). **Measured live 09-21 against the day's
  real grid:** MDDC 87 targets -> **4 fetched**, 77 reused, 6 republications skipped, 80/80 rows
  matched, every id / auction date / loan principal identical to the un-cached run (the only text
  differences are the site's per-render GUID tokens at the head and tail of each page). Two traps the
  build encodes: the "first 5 fetches failed -> abort" gate must count FETCHES, not resolved rows
  (with reuse, `results` fills up and a dead gate would grind through every new id --
  `FetchBudget`); and the Scrapfly preflight is now lazy, run before the first REAL fetch, so an
  all-reused day needs no Scrapfly at all. Offline guard `tools/manual/test_fulltext_prior.py` (45
  checks, incl. the real MDDC loop with a fake browser) -- run after ANY change to the resolve loop.
  The runner passes `--prior-auto` on all three fulltext invocations.
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
  unchecked and any later fill/daily run resumes them.
  **DEAD FOR FIVE DAYS, FIXED 2026-09-15 (`72af4a5`).** The daily leg wrote NOTHING 09-11..09-15:
  killed at the runner's 75m per-leg cap every day, and because neither slow pass banked progress
  the ledger was last written 09-10 — five days of Firecrawl spend for zero output, while the
  reconciliation queue grew 156 -> 170 instead of draining. Three defects, one shape — an
  unbounded, unbanked slow pass sitting IN FRONT of the only deliverable: `run_reconciliation_pass`
  had no cap and no `save_cb`; `fill_addresses` was never given a `limit` on the normal pull path;
  and `fill_addresses` flushed only every 10 records, which at 5-11 MINUTES per decedent never
  fires (09-15 resolved 2 of 26 and banked none of them). Fixed: both passes take `limit` +
  `save_cb`, `fill_addresses` flushes EVERY record, and **the CSV + JSON are written BEFORE the
  address pass and again after**, so the deliverable no longer depends on how far addresses get —
  which is what lets the daily address pass run uncapped. New `--reconcile-limit`; `--limit` now
  applies to the normal pull too.
  **The ordering matters as much as the cap:** the queue was in ledger insertion order, so a bare
  limit would re-check the same head forever and never reach the tail. It now sorts
  least-recently-checked FIRST, and rows with no `record_id` are dropped at build time — they can
  never be re-checked, so they never get `last_checked` and would sort first forever, eating a slot
  of every capped run. Offline regression: `tools/manual/test_md_row_pass_budgets.py` (12 checks) —
  run it after ANY change to either pass. Live proof: exit 0 in 47m43s, 76 rows written.
  **Measured throughput, use these to size caps:** reconciliation ~6.9s/estate; Land Records
  5-11 MINUTES/decedent; pull + detail fetches ~13m.
  **Real address throughput now comes from `automation/md_row_address_fill.ps1`** — the same
  `--fill-addresses-only` lookup with no 75m ceiling, run several times a day, resuming via the
  per-record `property_lookup_checked` guard and exiting in seconds when nothing is unchecked.
  **CONCURRENCY HAZARD:** the pull and the fill job both hold the WHOLE ledger in memory and write
  it back wholesale, so two at once means the second writer SILENTLY discards the first's work. The
  fill job refuses to start while a Register of Wills process is alive; anything else that touches
  the ledger needs the same guard.
  **Reading the CSV:** `Property Address` can hold the literal string `Not Found (Claude)` — that
  is NOT an address. The 09-15 pull's 76 rows were 36 real addresses, 21 `Not Found` placeholders
  and 19 blank; only **15** had a real address AND a PR. → [history](docs/history/mddc-va-md.md)

## 2026-10-06 — ZIP geocoder poisoned its own cache; Montgomery ratification notices typed `other`

**What the morning review found.** Tuesday's pull (the weekend gap) ran all legs exit 0: MDDC 97 rows
(51 in footprint, 48 with an auction date, 51 with full text), VA 30 footprint rows, MD RoW 89 estates
(checkpoint to 10/05), DC DWLR 33. The DC portal walk died on the WAF (2Captcha applied three tokens,
the challenge stayed, the 4-minute headed wait expired); a headed re-run at 12:25 cleared it on the
second solve and walked 2026-ADM-001092..001095 (4 hits, checkpoint → 001096). Same pattern as 09-16:
a WAF death is a bad day, not a dead pipeline. Note for the launcher: `Start-Process powershell
-ArgumentList '-File',<path>` with a path containing a space silently runs nothing; quote the whole
argument string.

**Trap 1 — the ZIP→county cache stores a failure.** `resolve_county_by_zip` in both
`mddc_trustee_sale_pull.py` and `va_trustee_sale_pull.py` did `if zip5 in cache: return cache[zip5]`
and, after any failure, `cache[zip5] = ""`. On 2026-10-06 the FCC Census Area API
(`geo.fcc.gov/api/census/area`) answered HTTP 400 for every point (`{"status":"error", ... "file":"auth.c",
"routine":"ClientAuthentication"}` — a Postgres auth failure on their side), so four ZIPs the cache had
never seen (20816 Bethesda, 21704 Frederick, 22043 Falls Church, 20191 Reston) were written back as
`""`. Effects the same morning: 5912 Carlton Ln and 6105 Bartonsville Rd sat in the MDDC CSV with a
blank county (a county-filtered push would skip them), and 6510 Overbrook St + 2367 Paddock Ln were
dropped from the VA footprint file as `out_of_footprint (no county)` even though the full-text leg had
already fetched both notices (Fairfax County land records, auctions Nov 17 and Nov 9). The 79 other
rows "resolved by ZIP geocode" in the log all came from the cache, so the log line proves nothing about
the API being alive. Fix: a blank is never cached (`if cache.get(zip5)`), the lookup moved to
`_geocode_zip_county`, and the Census Bureau geocoder
(`geocoding.geo.census.gov/geocoder/geographies/coordinates`, layer `Counties`) is the fallback when
the FCC returns no result. Verified live through both scripts with a scratch cache: all four resolve
(Montgomery / Frederick / Fairfax / Fairfax) and a bogus ZIP returns `""` without being cached. The
four cache entries were repaired by hand; today's rows were patched in place (`*.bak-20261006-county.*`
beside each file), the VA filter re-run offline (32 kept, 4 Alexandria city drops), and the two Fairfax
rows appended to `va_foreclosures_footprint_full.csv` from the details JSON — no spend.

**Trap 2 — Montgomery ratification notices.** `ORDER_NISI_RE` matched "ORDER NISI", "ratified and
confirmed", "will be ratified" and the Charles-style "ORDERED this … by the Circuit Court for …
that the sale". Montgomery's clerk writes "Notice is hereby given this 25th day of September, 2026,
by the Circuit Court of Montgomery County, Maryland, that the sale of the property … will be RATIFIED
AND CONFIRMED", and the grid snippet truncates at "Notice is hereby gi…", so 8601 Manchester Rd #516
(defendant "AYLA A. O'CONNOR (DECEASED)", sold at foreclosure for $85,000) and 18325 Queen Elizabeth Dr
(sold for $380,000) sat in `_dropped.csv` every day 10-01..10-06 as type `other` — the drop was right,
the label made them look like missed leads worth a probate look. Decision (Basem delegated it,
2026-10-06): a ratification notice is post-sale; the deceased owner's heirs have nothing left to sell.
Not a lead, no DP. The regex now carries the "Notice is hereby given … by the Circuit Court (of|for) …
that the sale" shape so the full-text classification reads `order_nisi` (snippet-only rows still read
`other` because the snippet ends before the distinguishing words — same drop either way).

**Also seen, not fixed.** The DC DWLR reconciliation (dry run, nothing persisted) rewrote
2026-ADM-000944's PR city/state to "WOODBRIDGE VIRGIN" / "IA" and replaced 2026-ADM-000953's applicant
Evelyn Patrick Ellis with "Deirdre J. Payne"; check both before the next `--commit` of that ledger.

**Portal "not found in ITSPE" rows, hand-checked the same day (Basem: "check OTR? check deeds?").** ITSPE is
OTR, so the same address query cannot un-miss itself; what works is (a) the tax roll by owner NAME and by a
loosened address, then (b) the free Recorder of Deeds index by decedent name (`dc_rod_lookup.py` with a
two-key `records.json` in the scratchpad, 45 s for both), then (c) ITSPE by each SQ/LOT the index names.
2026-ADM-001092 Falls: the portal's residence "1327 Hamlin Street NW" is really NE (ZIP 20017 is Brookland);
`itspe_rows_for_address` found the NE parcel on its loose query and then discarded it on the quadrant, so a
wrong quadrant in a court record defeats the address path. The parcel belongs to Stephen E + Iver C Ricks
(Stephen holds her 2019 POA); her own three parcels were sold (3577 Texas Ave SE 2019, 2226 Ridge Pl SE 1992)
or condo-foreclosed (3937 Pennsylvania Ave SE #202, 2021). 2026-ADM-001093 Joiner: #905 at 1330 Massachusetts
Ave NW is one of 141 units Thomas Circle Property LLC bought in bulk in 02/2026 — a tenant; his five former
parcels were all conveyed by 2013 and one (2201 L St NW #209) is the PR's own home. Neither estate holds DC
real estate: not leads. Verdicts written into `dc_probates_portal.csv` and the ledger (`HAND` confidence),
deed index saved under `dc_portal_hand_checks/`.


### 2026-10-08: the MDDC "footprint filter" only checked the state
`filter_footprint` predates the 6-county narrowing and drops only rows outside MD/DC/DE/VA;
the site's county checkboxes are inert, and 78 of the day's rows got their county from the ZIP
geocode that ran AFTER the filter. So `mddc_trustee_sale.csv` carried every MD/VA county:
10-08 had 92 rows, 51 in the footprint, 32 outside (Charles 14, Calvert 3, Prince William,
Manassas city...), 9 with no county; the full-text leg paid a 2Captcha solve on each new
out-of-county notice. Fix: `filter_footprint_county` clips on (state, county) after the geocode
(Montgomery and Frederick also exist in VA; "Fairfax city" is not Fairfax County), keeps
no-county rows (snippet cut before the address), and writes the rest to `_dropped.csv` with a
`drop_reason` column. Checked offline on the 10-08 file (60 kept = 51 + 9 blank, 32 dropped)
and `test_fulltext_prior.py` passes.

## 2026-10-16 — DC probate DWLR dead 5 runs (User-Agent), MDDC full-text crash

- **DC probate (`dc_probate_pull.py --source dwlr`)** failed every run 10-10, 10-13, 10-14, 10-15, 10-16
  with `RemoteDisconnected('Remote end closed connection without response')` after 3 attempts.
  The site was up: curl and a `Chrome/141` UA got 200 (~136 KB); the script's `Chrome/126` UA
  was dropped every time (reproduced twice side by side). Fix: `UA` bumped to `Chrome/141`.
  Re-run for 10-16: 41 rows, ledger 10 added / 31 changed. `dc_hoa_lien_pull.py` and
  `permit_portal_pull.py` still send `Chrome/126` (different sites, not yet seen failing).
- **MDDC full text (`mddc_fetch_full_text_browser.py`)** crashed 10-15 and 10-16 with
  `Page.content: ... page is navigating` inside `fetch_detail` (a late redirect after landing on
  Details.aspx); one exception killed the whole leg, no `_full.csv` written. Fix: both
  `page.content()` calls in `fetch_detail` now go through the existing `_content_settled()`.
  Re-run for 10-16: 0 failed, 97 fetched, 13 reused, 35 of 55 rows matched; the 20 unmatched are
  10/13 notices past the 12-page / 120-notice grid cap (all 20 have text in the 10-14 file).
- Re-runs must use the runner's Python (`Python314`); a shell with another venv on PATH has no
  `twocaptcha` and dies at the first Turnstile.

## 2026-10-19 — MDDC subscription expired; the pull exits 0 with 1 row

- The 10-19 manual run (the Tue–Sat task had been blocked by the account gate since 10-17) got
  100 raw rows but **1 in the footprint**: `detected page numbers: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]`
  (the grid served page 1 ten times), 50 rows typed `other` with blank street/state, 4 real
  foreclosures all Loudoun / Prince William. The full-text leg died with
  `Page.select_option: Timeout 30000ms` because `ddlSavedSearches` was `disabled`.
- `--list-searches` explains it: the site lists ONE saved search, `id=0 Subscription or free
  trial expired`, and reports 43 ("Trustee's Sale") as configured but not on the site.
- Timeline: 10-10 through 10-16 paged 1..10 and kept 55-69 rows, so the lapse happened between
  10-16 06:00 and 10-19 10:00. The "no extractable notice_id" warning on every page is OLDER
  (present on every day since at least 10-10) and is a separate, still-open parser issue.
- Nothing in the code can fix this; Basem renews the subscription, then re-run with
  `--list-searches` first to confirm 43 (or its replacement id) is back.
- Renewed the same morning: at ~11:00 `--list-searches` listed 43 again and a manual re-run
  (runner commands, Python314) got 57 footprint rows (DC 17, AA 9, Mont 6, Fred 4, Fairfax 2,
  19 no-county) and the full-text leg matched 57 of 57 (20 fetched, 88 reused, 40 auction dates,
  none in the past). Logs: `MDDC_rerun_stdout.log`, `MDDC-fulltext_rerun_stdout.log`.

