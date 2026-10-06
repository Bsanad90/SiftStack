# Deep Prospecting & Obituary Work

> Build history extracted from `CLAUDE.md` on 2026-08-28. Verbatim — the API
> contracts, traps and measured numbers here are the record. Live state and next
> steps live in `CLAUDE.md`.

---

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



---

## Moved from CLAUDE.md on 2026-10-01 — FTM DP batch, Obituary DP batch (every trap), the ghost resolver, the owner-died namesake audit

Verbatim text as it stood on 2026-10-01 (second split of CLAUDE.md; the first was 2026-08-28). The live current state and the traps index stay in CLAUDE.md; this is the record. Snapshot: `_CLAUDE-snapshot-2026-10-01.md`.

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
  **6. A NON-DP plan pushed through `obituary_dp_batch.py push` deleted the DP boards
  (found 2026-09-10, fixed).** On 2026-09-01 15:53 an ad-hoc "Register of Wills 9.1.2026"
  plan (`output/probate_fields_push/`, builder script not in the repo; 401 boards posted) was
  pushed with this pusher 13 minutes after the DP QA passed. `_push_one_api` deleted every
  message starting `DEEP PROSPECTING` whatever the NEW note was, so 21 obituary DP notes were
  replaced by a SmartSkip-raw "Relative" list (no obituary, predeceased people loaded), and on
  8 records REL1..REL15 were rewritten in SmartSkip order so the REL fields disagreed with the
  phone `Rel{n}.{m}` tags (1392 Stonecreek Rd: REL1 field JUDITH MCAULEY, Rel1.x phones SARAH
  LIBSON). Fixed: the cleanup now deletes only earlier messages of the SAME FAMILY
  (`_note_family`, first line cut at " (", date stripped; equality not prefix) and logs each
  deletion in `steps.message_replaced`. `names_match` stays strict (PR-repair scripts import
  it, and so does the predeceased drop list -- code review showed ambiguous nicknames such as
  Chris/Christine or Lisa->Elizabeth would drop a LIVING signer); the obituary cross-checks go
  through `_cross_check`, which pairs survivors and SmartSkip relatives 1:1 (strict first,
  `names_match_loose` only when unambiguous; standalone given names are pruned from
  `NICKNAMES`), and REL lines print the obituary's relationship (`Niece (obituary)`) via
  `_rel_label`/`OBIT_LABEL_MAP` only when the decedent IS the owner -- labelling only,
  signer selection and trace targets unchanged. **Repair DONE 2026-09-10** through the
  rebuilt `obituary_dp_qa.py` (`--keys-file` across run dirs, VALUE-compare on REL fields,
  exact `Rel{n}.{m}` tag check, repair of fields/tags/phones with read-back,
  `--clear-extra-rel` which REQUIRES `--extra-source` and blanks only values that other plan
  wrote, per-record backup + `--undo`; code committed `fce82b5` after an 8-angle review): 19 DP
  notes re-posted and pinned BESIDE the kept PROBATE note (two pins coexist), 6 REL sets
  restored + 88 probate-written extra slots blanked (`""` via update-values works), verify
  audit 18/22 clean; the 3 non-clean carry another pusher's REL extras (kept on purpose) and
  8327 Old Harford Rd is a duplicate-address pair where nothing was lost. Records the probate
  plan listed but never pushed (6) were left alone. Backups/undo:
  `output/probate_fields_push/qa_backup_20260910T*/`, `qa_undo_20260910T*.jsonl`.
  **7. The upsert RESET a dialer's phone disposition (found 2026-09-15, fixed).** `cmd_build`
  stamps every plan phone `status: "UNKNOWN"` and `_push_one_api` sent that verbatim, so a
  re-push of a number already on the owner overwrote the CRM mark: on 7639 Paradise Beach Rd
  the house line Bagoury had marked DEAD minutes earlier came back "not dialed yet" and led the
  dial sheet. `_push_one_api` now keeps the LIVE status whenever the plan's is UNKNOWN/blank.
  Same record, same day: SmartSkip returned the namesake SON as the subject and the ranker
  re-rooted his SIBLINGS as the decedent's children -> 6 false SIGNERS (nephews and cousins);
  the LLM research pass had an empty legacy.com shell (`survivors: []`). Fix path that works:
  Firecrawl-scrape the funeral-home page, hand-fill `stage_c_research.json` survivors/preceded/
  executor_named + `canon_rel`/`is_dm` in `ranked_records.json`, `build`, `push --force`. The
  predeceased wife on title surfaces as `secondary_owners` on the detail read.
  Same day, Basem's rule **DP wins** on records another pusher also loaded: 3005 S Leisure
  World #405 REL set restored; 2084 Jasmine Rd / 4462 Owensville Sudley / 1805 Sunnyside Ln
  re-pushed via `obituary_dp_batch.py --run-dir <run> push --route api --probe <key> --force
  --commit` (`--run-dir` is GLOBAL: it goes before the subcommand), which removed 18/0/10
  foreign Rel-tagged numbers under the pusher's own-tag guard. Four other records' Rel tags
  had never matched any DP plan (the final re-push's tags did not land; the old QA's
  `startswith("Rel")` check could not see it) -- re-upserted, verified.
  Trap for any pusher: a board note may only ever displace its own family, and REL fields
  written by another plan are not yours to blank unless their value proves it.
  Also: **SmartSkip dedupes its input by NAME, not property** — 9 of 628 rows returned nothing
  of their own because the same owner holds two properties. Their heir graphs are identical
  people, so the result can be fanned across same-owner records for free (not done).
  The note was rewritten from one `"  ||  "` paragraph to line-broken blocks; **DataSift
  preserves the newlines** (verified live, 37 of them). → [history](docs/history/prospecting.md)

## "Unknown <Surname>" DP relatives — the ghost resolver (step 1 built 2026-09-17)

130 DP relatives across the run dirs are named `Unknown <Surname>` (113 live in `REL{n}: Full
Name` with `Rel{n}.{m}` phones): SmartSkip's Subject row came back with a blank First Name,
`_reroot_namesake` kept that nameless Subject as a Child signer, and research never joins by
name. `src/scripts/dp_ghost_name_resolver.py` (READ-ONLY, no spend) proposes a first name per
ghost from the vendor phone index (`output/rel_pr_name_repair/index.json`), obituary survivors
and SmartSkip Subject-row emails, and writes `output/unknown_ghosts/ghost_name_resolution.csv`
+ `.json` for review. Tiers: HIGH / MEDIUM / NAMESAKE (the phones belong to someone carrying the
owner's or a named relative's exact name at another age) / DUPLICATE (the ghost IS an
already-named relative, SmartSkip listed them twice -> merge, not rename) / LOW / HINT (email
only) / UNRESOLVED. First run: 25/10/6/8/3/59/19. **Trestle Reverse Phone** (`src/scripts/trestle_reverse_phone.py`,
`GET /3.2/phone`, ~$0.07/query, cached per number, `--commit` to bill) is used on Basem's
per-batch go: the 5-property probe on 2026-09-17 (17 queries, $1.19) returned a same-surname
holder on 5/5 and a NEW person to call on 4/5 (the fifth was the deceased owner's own line).
Batch 2 the same day: 44 ghosts, 47 queries, $3.29 -> `trestle_reverse_enrich.py` (offline, from
the cache) writes `output/unknown_ghosts/trestle_enrichment.csv`: 25 NEW PERSON, **13 NAMESAKE SON**
(the owner's name as Jr./III or another middle name, a generation younger -- the namesake-child trap,
finally named), 1 merge, 1 owner's own line, 8 age mismatches, 1 no hit. Batch 3 (confirm the resolver's names,
leftover numbers, the 22 household ghosts via `--shared`): 87 queries, $6.09; total $10.57 / 151.
Final sheet, 115 probed: 59 NEW PERSON, 15 NAMESAKE SON, 10 merges, 11 household-only, 13 age
mismatch, 5 no hit, 2 owner's line; 39 of the resolver's 52 confirmed, Welzel's ghost turned out to
be the named IN-LAW Cindy (not a signer), Helmick is Kevin Lee Helmick (neither source had him).
Rule that paid for itself: **confirm a one-source name before writing it** -- 10 of 52 did not
survive. **Step 3 WRITTEN 2026-09-17:** `dp_ghost_name_write.py` renamed 82 REL slots live (QA 90/90),
no phone moved; the 8 "duplicates" were renames because the twin held no slot of its own. **TRAP:**
the DP strip re-push wave from another session (`push --force` of `push_plan.json`) reverted 9 writes
within minutes -- the plan files are the source of truth, patch them first (`dp_ghost_plan_patch.py`,
idempotent, backups) and check `Get-CimInstance Win32_Process` for a running pusher before any
per-record account fix. **Trap: holders are under
`owners[]`, not `belongs_to`** -- the first parse read every response as empty. Free alongside: the funeral-home page rescrape for the 21 obituaries that came back
without a survivors list, hand review of the 4 rows whose obituary names children absent from
the record, and demoting the 34 whose obituary already accounts for every child (not heirs
whatever their name, so never worth a query). Offline checks: `python tools/manual/test_ghost_name_resolver.py`
-- run after any change to `same_person` / `vendor_candidates` / `decide`. Traps it encodes: a
shared MIDDLE name is not an identity; a same-name relative is a duplicate only when the ghost's
age fits (or Sr., or a shared line); the predeceased drop yields to a survivor match. Nothing
has been written to the account; the write step reuses the rel_pr_name_repair pattern.

## Owner-died namesake audit (built + free pass run 2026-09-18, READ-ONLY)

`src/scripts/dp_owner_died_namesake_audit.py` scores every `owner did die` finding in every live DP
run dir (783 across 18 dirs) on the signals that would have caught 8131 Turn Loop Rd, and writes
`output/namesake_audit/owner_died_namesake_audit_<stamp>.csv` + `summary_<stamp>.md`. Free pass
2026-09-18 (`--owner-of-record --sdat-limit 0`): **LIKELY_FALSE 28** (all pushed live, 23 of them as
`Deceased Owner`), REVIEW 141 (31 GENERATION_GAP, 13 SPOUSE_OR_RELATIVE), NO_SIGNAL 124, CLEAN 490.
Nothing written to the account; a confirmed false positive is repaired the Zimmerman way on Basem's
go. Offline guard `tools/manual/test_namesake_audit.py` -- run after ANY change to `recommend` or the
age/place regexes. Facts it rests on:
1. **The LLM's `age_at_death` and `city` are DROPPED by `cmd_research`** (asked for in
   `OBITUARY_PROMPT`, never copied out of `parsed`), so no DP file holds a decedent age or city. The
   free source is the search hit `title`+`snippet` kept in `searched[].trail[0].hits[]` (age on 58%
   of owner-died rows, residence on 32%). Persisting them is the cheap upstream fix.
2. **Compare BIRTH years, not ages** (SmartSkip's age is now, the obituary's is at death): gap <=2 ok,
   4-17 conflict, **>=18 is a parent/child pair** -- SmartSkip returned the namesake CHILD and no
   re-root fired -- where the verdict may be right and the signer tree wrong (REVIEW, never LIKELY).
3. **A title city is the FUNERAL HOME's** on legacy.com, dignitymemorial AND tributearchive ("Jen
   Chen Obituary - Falls Church, VA" for a McLean resident). Only body text ("of <City>, MD",
   "resident of") is residence; the title city is recorded and never scored.
4. The shell-override shape (a match:false page >=3000 chars, then a match:true page <3000) exists on
   22 rows; `_llm_match` still takes the first true.
5. **CRM `owner_first` carries a middle initial on 0 of 783 rows**, so initial evidence comes only
   from the tax roll: DC ITSPE is free (`itspe_verify_address`; 53/57 resolved, 34 decedent on title,
   12 initial CONFLICTS), MD SDAT is one billed Firecrawl scrape per row (`--sdat-limit N`; 93 MD rows
   sit in the review buckets; `records.county` says plain `Baltimore`, which `SDAT_COUNTY_ID` lacks,
   so `sdat_county()` maps it), VA has no lookup at all (265 rows). `name_match_level` returns MEDIUM
   for a conflicting initial, never a conflict; women's maiden names ride as middles, so a roll
   initial conflicts only when it matches NONE of the decedent's middle initials.
6. `given_name_differs` (same surname, another first name) is the SPOUSE shape: 15 rows where the
   spouse died and the CRM owner is alive under an "owner did die" verdict. Strip honorifics first
   ("Capt. Roberta J. Grace, USN" IS Roberta Grace).
Built, not run: Tier 1 `--fetch-pages` (275 rows lack both age and place; 483 of 782 obituary URLs
are on Firecrawl-only hosts, so `--allow-firecrawl` bills) and the MD SDAT tier.

**The 23 LIKELY_FALSE `Deceased Owner` records REPAIRED live 2026-09-18 (QA 23/23 clean)** with
`src/scripts/dp_namesake_repair.py` -- the Zimmerman path scripted: `plan` (dry run) -> `apply
--commit` (per-run-dir backup `namesake_repair_backup_<stamp>/` of the 4 plan files; verdict
rewritten WITH the evidence, original under `namesake_correction`; dead same-name relative onto
`drop_names`; obituary-injected relatives removed; a re-rooted record's owner phones restored from
`decedent_from_smartskip`; Trestle on the new numbers only, $0.65; ONLY those records rebuilt
through the real `cmd_build` in a temp dir and spliced into push_plan.json; note gets a CORRECTED
header, "DO NOT open with condolences", living-owner SIGNERS/MUST VERIFY, tax-roll owner, prior
match kept as a labelled trace) -> `push --keys-file --force --commit` per run dir -> `untag
--commit` -> QA. 21 presumed alive, 2 spouse-died (`Owner Alive - Spouse Deceased`); co-owners on
title loaded as REL1. Numbers 76 removed / 84 loaded; review in `output/namesake_audit/repair_<stamp>/`.
Four traps, the first two fixed in code:
1. `_push_one_api` ran the REL step only `if p["custom_fields"]`, so a plan that dropped EVERY
   relative never ran `_ghost_blanks` (5908 Veranda Dr kept the son in REL1). Now gated on
   "anything to write OR blank".
2. `obituary_dp_qa.py --clear-extra-rel` required the CURRENT plan to hold a REL field; an
   owner-alive plan never does, so 7 of 9 provenanced ghosts were skipped. Now also true when the
   `--extra-source` plan wrote REL fields for that key.
3. **Shared-line ghosts:** when the old plan gave a relative a number the OWNER also holds, the
   new plan keeps the number as `Owner.N`, so it is never "removed" and the relative's REL slot
   survives the number rule. After ANY owner-alive re-push run `obituary_dp_qa.py --repair
   --clear-extra-rel --extra-source <the plan just replaced>` -- the value rule is the only one
   that sees it (31 fields blanked here).
4. **A 404 on a known uuid can be a MERGE.** 3718 Calvert St NW: the 04:03 SiftMap bulk import
   created a new record and `property.merged` absorbed the old uuid (in the survivor's `/logs/`);
   the survivor inherited `Deceased Owner`, the push resolved by address and POSTED a fresh note
   (nothing same-family to edit). Re-resolve by address on a 404 before calling it a deletion.
Still open from the audit: the 5 LIKELY_FALSE pushed as `DP Death Unverified` (plan_group
`unverified`; `load_rows` filters on `deceased`), REVIEW 141, the paid tiers, the upstream fixes.

## DP research quality audit + anchored research (2026-10-01, Basem: "how much can the dialer depend on the note?")

**Trigger.** The one record pushed live to prove the CREATE path (1391 Pennsylvania Ave SE #433,
uuid 74742f8e) was wrong on every line a dialer reads: "DECEDENT: David Smith - DOD unknown (no
source)", the intestacy template as the SIGNERS line, five relatives with no kinship. The vendor's
raw weekly probate CSV (`Marketing Lists/2026/09/0930/probate_2026-09-30_07-40-33 Raw.csv`) for that
row carried `Date of Death` 08-13-2026, `Case Number` 2026-ADM-001053, `Middle Name` Beardsley, the
PR's full name + Brooklyn address and the attorney. `dp_input_export.csv` keeps none of those columns.

**Measured on the Sept 646 (`output/dp_drive_sept_missing_20260929`) before any fix, offline:**
- WHO TO CALL "SIGNER" lines 1,496: 1,138 rested on a SmartSkip relationship label alone, 304 carried
  an obituary word, 54 were the PR. 581 signer people / 1,564 numbers sat on 232 UNRESOLVED records,
  179 of which still printed "spouse and children (all children must sign)".
- 117 "owner did die" records whose obituary named NO survivors still printed SIGNER lines from labels;
  100 records loaded an "Unknown <Surname>" as a signer; 16 listed two or more spouses (111 re-rooted).
- The obituary judge is fine WHEN the person is right: 18 of 22 obituary DODs matched the vendor's, 0
  contradicted. But it is unanchored: `_search_obit` ran one query, name + city + state, `_try_person`
  judged at most 3 pages, nothing from the filing reached the search or the prompt. Namesakes: 1403
  Horizon Ct, Herndon matched a Gaffney SC obituary (then the re-root turned two "Parent" rels into
  "Spouse" and the note said "surviving spouse takes"); 5 cached matches were dated 2019-2024 against
  2026 filings (7016 Channel Village Ct, 3901 Connecticut Ave NW, 15105 McKnew Rd, 405 Pearl St,
  7501 Langport Ct); 74 died verdicts had neither city nor state in the hit and no corroboration.
- Unresolved 272: 78 after zero pages judged (70 with zero search hits), 35 after one page.

**What was in hand and never used.** Vendor weekly raw CSVs (DOD, case, middle name, PR + address,
attorney) -- on Drive back to 2026-01 (`title contains 'Raw' and title contains 'probate'`), only
0930/0805 were on disk; 0902/0909/0916/0923 and the Aug weeks downloaded to
`output/vendor_lists/files/Marketing Lists/2026/_raw_from_drive/`. Sheet26 rows carry the RoW estate
number; the RoW ledger (`output/md_row_ledger.json`, county|estate) already held 79 of those 171 with
a DOD; the Estate Search form takes `#txtEstateNo` + county and its GRID carries the DOD, filing date
and name as filed -- one Firecrawl scrape per estate, no detail page. Monthly inheritance (338) and
monthly probate (131) files carry no DOD anywhere. The DC docket (`dc_case_pr_lookup.py`, headed +
2Captcha) was not run: the raw file already gives DOD + PR for the DC weeklies.

**Built (`obituary_dp_batch.py`, guard `tools/manual/test_dp_research_anchors.py`):**
- `anchors` subcommand -> `rec["anchors"]` {dod, dod_source, case_number, court, file_date,
  estate_number, decedent_full, decedent_middle, decedent_as_filed, pr_full, pr_mailing, pr_others,
  attorney, notice_date, source}; `--estate-search` fills the DOD by estate number (cache
  `row_estate_lookup.json`; the ledger is never written -- two writers of that file lose work).
- `research`: `_anchor_queries` ("First Middle Last" obituary first, DOD year on the base query; DC
  no longer searches "Washington Washington DC"); `_anchor_facts` puts the filing facts + up to 8
  SmartSkip relative names into the prompt and asks for `anchor_hits` / `location_match`;
  `_verify_anchors` IN CODE rejects: obituary DOD vs filing DOD > 45 days; strong anchor (DOD /
  middle / PR) on file with no hit and no location match; out-of-footprint URL slug
  (`_url_outside_footprint`, dignitymemorial `brooksville-fl` style) with no hit. No obituary but a
  filing DOD = verdict "owner did die" with `confidence: court record`, evidence names the case,
  flag "no obituary found; death from the court record only" -- never the spouse search (which called
  617 Underwood St NW's owner alive), never "unresolved". `--keys-file` + the subset merge (a
  `--key`/`--limit` run used to shrink stage_c_research.json to the subset).
- `build`: SIGNERS basis says "death UNVERIFIED - ... SmartSkip labels, not confirmed heirs" on
  unresolved records, "...names no survivors - relationships below are SmartSkip labels, unverified"
  on died-without-survivors, "N of M signers below are label-only" otherwise; REL lines print
  `SIGNER (obituary)` / `SIGNER (label only)`; flags `two_spouses`, `unknown_name_signer`,
  `label_only_signers`, `obituary_outside_footprint`; the note gains `CASE: ... - <court>, filed ...`,
  the PR's full name + mailing, `ATTORNEY ON FILING`; the court DOD is used when the obituary has none;
  `dial_sheet.csv` gains `kinship_source` (obituary / filing (PR) / smartskip label).

**Re-research set:** Sept 401 keys (272 unresolved + 3 "spouse died" with a filing DOD + 5 DOD
conflicts + uncorroborated / out-of-state died verdicts with anchors or no city in the hit), run as
3 shards; 0930 all 21. Firecrawl balance before: 56,027 credits.

**Result (same day, both plans rebuilt, nothing pushed):** Sept 646 -- died 348 -> 444 (74 court-record-only), unresolved
272 -> 184 (all without any vendor DOD: monthly inheritance / monthly probate), 10 old matches rejected, 99 matches
anchor-confirmed, 187 CASE lines; signer lines now obituary 343 / PR 57 / label-only 1,071 said as such; numbers on
unresolved records 1,564 -> 1,065. 0930 21 -- 21/21 died (10 court-only), 0 unresolved. Found on the way: a one-letter
middle initial in the quoted query returns junk and the page budget was spent in query order (`_rank_hits` fixes the
order; initials are not searched); `research --key` used to shrink stage_c_research.json to the subset (merged now);
the LLM "returned nothing" on ~2% of records under 3 parallel shards and again on 5700 Overly Dr alone (left unresolved).
The DC docket was not consulted (the raw file carries DOD + PR + attorney for the DC weeklies). Fixing the re-root (two
"Parent" rels -> two "Spouse" signers, 14 records flagged `two_spouses`) is still open, see [[project-dp-reroot-research-gap]].


## 2026-10-01 — 1391 Pennsylvania Ave SE #433: the court's party list beats the research (DP v5 by hand)

Basem asked for a v5 on the batch's live probe record. The anchored research had run two queries, found no
obituary (none exists under any name variant) and printed "heirs NOT identified". The same morning's DC probate
portal pull (`output/daily_pulls/2026-10-01/dc_probates_portal.json`, case 2026-ADM-001053) already held the PR
and five **Legatees** with addresses (so a will is on file), and the ITSPE check in that pull recorded the
co-owner **SMITH, HELEN H** beside SMITH, DAVID B (bought 2019-08-06, $811k). Neither is read by
`obituary_dp_batch.py anchors`/`research`.

What that changed: (1) Sarah Smith Devary (Belton MO), whom the batch had set aside as a SmartSkip *namesake's*
child (`excluded_smartskip_namesake` — SmartSkip's subject row carried no first name and age 80, i.e. the
co-owner, not a stranger), is a court-listed legatee; (2) Helen H Smith is absent from the probate parties
although on title; the Recorder of Deeds index shows her co-signing the 2019-10-23 sale of sq 1038 lot 68,
nothing later, and no deed on sq 1045 lot 2155 under either exact name — so whether the estate can sell at
all turns on whether she is living and how the 2019 deed vests (image $4, on a go); (3) the pusher adds the
verdict tag but never removes `DP Death Unverified`, so a re-push leaves both on the record.

Files: `output/dp_1391_pennsylvania_20261001/REPORT.md` (family tree, who-must-sign, every scored number),
`rod_party.py` (index by party; the URL-built `results?parties=` form answered "No results" for every term and
is not trusted), the patched note in `output/dp_drive_0930_new/push_plan.json`
(backup `push_plan_pre_1391_court_heirs_20261001.json`). Live note still the old one; re-push on a go.
Fix to build: `anchors` joins the DC probate ledger (`heirs[]`, `personal_reps[]`, `property_itspe_co_owner`)
and the research treats a court legatee list as the heir set when no obituary matches.


## 2026-10-02 — the v5 method as a batch: title / court parties / deed index, DC pilot (53 records)

Basem: "run them with multiple agents". Three builders ran in parallel, each writing one sidecar under
`output/dp_drive_sept_missing_20260929/rerun_v5/`, and `obituary_dp_batch.py` learned to read them:

- **`src/scripts/dp_title.py`** -> `title.json`. DC tax roll through `dc_probate_pull.itspe_query` (unit-first,
  then bare street at limit 400 — the old limit of 50 hid condo units — then a quadrant-filtered fallback and a
  surname-within-building query for unit-less condos). Per owner string: exact / probable (roll spelling
  `STACIL`~`STANCIL`, glued initial `MTAVSS`) / PR / co-owner; `TRUSTEE` stripped and `trust_vesting` set;
  `$0` with a date = family or trust deed; `owner_of_record_is_decedent` False = "DECEDENT NOT ON TITLE".
  MD `title_for_md` sits behind `--allow-firecrawl` (never run); VA writes `source: none`.
- **`src/scripts/dp_court_parties.py`** -> `court.json` (+ guard `tools/manual/test_dp_court_parties.py`, 51).
  Joins the daily `dc_probates_portal.json` files AND, with `--ledger`, `output/dc_probate_ledger.json`
  (273 cases — the handoff had it as "1 estate", see the trap). Join order: `anchors.case_number`, then
  normalised address + zip5, then decedent name gated on zip (`name_only`, review). `will_on_file` = any party
  connection `Legatee`; `Beneficiary` (41 rows) is tallied in `connections`, not counted as a will.
- **`src/scripts/dc_rod_lookup.py`** -> `deed.json`. The Recorder of Deeds party index lifted from the 1391 run's
  `rod_url.py`: index free, images $4 and forbidden in the run; grantor + grantee 1990-2026, common names capped
  to 30 years; a second pass searches the tax-roll owner names where they differ from the vendor name
  (130 Randolph was searched as Whitmon-Clark; title says Mary L Clark).
- **`obituary_dp_batch.py`**: `v5merge` folds the sidecars into `records.json[key]["title"|"court"|"deed"]`
  (backup `records_pre_v5merge_<date>.json`); `build --keys-file --plan-out` rebuilds a subset into its own
  folder so the live `push_plan.json` is never touched; the note gains `CASE` (from the court join when the
  anchors carry none, or "no case on file"), `TITLE`, `DEED` (last CONVEYANCE, not the last release),
  `COURT PARTIES` (PR + every heir with address, matched to the SmartSkip/IDI REL or "no number on file");
  every REL line prints `SIGNER (court|title|obituary|label only)` and `dial_sheet.csv` `kinship_source`
  follows; the SIGNERS line leads with the fact that decides the deal (PR signs for the estate / co-owner on
  title not in the probate / DECEDENT NOT ON TITLE / title held in TRUST) and the intestacy template follows
  as "then ..."; a court decedent who matches the owner proves the death (DOD from the court, verdict
  `owner did die`, `Deceased Owner`); a court party is never dropped as the decedent's namesake; SmartSkip
  signers on no record are listed as such. `_matches_decedent` now requires the shared given name to be the
  RELATIVE's first name (Kavita Arjan Aswani is not Arjan Aswani). Guard: `tools/manual/test_dp_title_court.py`.
  A no-sidecar rebuild of the 53 keys reproduced the live plan exactly apart from the date header and three
  notes the raised `NOTE_MAX` no longer trims.

**DC pilot (53 non-grounded Sept records; `rerun_v5/pilot_dc/PILOT_DC_REPORT.md` + `before_after.csv`):**
title on 53/53 (45 decedent confirmed, 4 probable by spelling, 4 DECEDENT NOT ON TITLE — 1638 Fort Davis Pl SE
transferred 2026-09-15 for $0, 3715 30th Pl NE 2023, 1001 L St NW resolved by surname to #201, 1616 Varnum
POWDEN M E vs Drusilla); 30 co-owners on title who are not the decedent (mostly spouses; whether they are
living decides every one of those deals); 9 trusts (successor trustee signs); court case on 5 (48 are Monthly
inheritance rows with no case, as predicted); 2605 24th St NE went from "death UNVERIFIED" to a court-proved
death with a PR and six heirs — and its nine SmartSkip "signers" (Brown / Coachman / Seamon) are on no record
while the court's heirs are surnamed Quick (714 Rittenhouse's owner is Polly Quick-Brown: the same decedent
with two properties, or a namesake family at 2605 — hand look). 0930 batch: court 8/8 DC, title 8/8 (co-owner
Ruby M Morgan on 1201 Kalmia Rd NW beside PR Darren Morgan; Helen Smith on 1391). Deed index: hits on 51 of 53
(0 hits: 5724 8th St NW, 1616 Varnum — pre-1990 paper), 25 with a red-flag instrument on the parcel: 5 transfer-on-death
deeds recorded by the owner (the house passes to the TOD beneficiary outside probate — 1738 Lyman, 7435 9th, 1266
Delafield, 1104 Chaplin, plus 1638 Fort Davis's chain), 6 owners who are GRANTOR on their last conveyance, 5 with 2026
paper, 1 conveyance after the DOD (1638 Fort Davis), 1 active foreclosure (5201 Wisconsin #102: substitute trustee
2026-05-05, NewRez lis pendens 2026-05-26). The four ROD traps (dead URL path, unordered 50-row pages, TRUST = deed of
trust, SSL suffix form) are in CLAUDE.md Pulls. Notes: longest 3,896 chars, none at the 3,900 cap.
Nothing pushed, nothing spent, code uncommitted; MD (Firecrawl) and VA (no fetcher) wait on Basem.

## MD pass of the v5 method — the combined Firecrawl run (2026-10-02)

Basem asked whether the two open MD Firecrawl asks (SDAT title + MDLANDREC deed index on the 334 MD Sept
records; Register of Wills Estate Search by decedent NAME on the 167 MD no-case rows) could run together.
They could: one cohort, three sites, one Firecrawl account with 52,639 credits expiring 2026-10-04 and a
measured cost of ONE credit per action-scrape (concurrency 50). Basem: "go ahead". Run dir
`output/dp_drive_sept_missing_20260929/rerun_v5/`, handoff `HANDOFF_MD_PASS.md`, memory
`project_dp_md_pass_20261002.md`. Nothing pushed; live `push_plan.json` untouched.

**What was built.** `md_estate_name_search.py` (Estate Search by name, `#txtLN` / `#txtFN` /
`#cboPartyType` Decedent — verified on a 1-credit probe; the handoff's guessed `#txtLastName` was wrong),
`md_deed_lookup.py` (MDLANDREC party search as `both`, radio `#body_rdlParty_0`, dc_rod_lookup-shaped
instruments, on-parcel by Book/page against the SDAT Deed Reference), SDAT `parse_sdat_transfers` +
`deed_reference`, `dp_title.title_for_md` fixed, `dp_court_parties --extra-store`, MD note lines in the
batch, guard `test_md_v5_legs.py`, `tools/manual/pilot_report.py` generalised.

**Traps found.**
- *The SDAT grid click never worked.* `document.querySelectorAll('table tr')` returns an OUTER layout row
  whose innerText matches the street and the owner, and whose first `<a>` is the header's SORT link —
  `a.click()` posted the grid back sorted. 38 of the first 185 MD lookups read "no parcel"; every one was
  a multi-row grid (unit addresses, `Dr` vs `Rd`). The row's own link is `a.lnkdetails` with
  `href="javascript:WebForm_DoPostBackWithOptions(..., performValidation=true ...)"` — a plain click runs
  the validators and does nothing; `__doPostBack(<its event target>, '')` navigates, and the detail page
  needs ~7 s, not 3.5. Fixed: leaf rows only, `a.lnkdetails`, `__doPostBack`, 7 s wait. 403 Vale Dr went
  from "no parcel" to COLBURN WILLIAM D + STEPHANIE B, bought 1987 arms-length.
- *A condo grid is one row per UNIT* (`66 FRANKLIN ST UNIT: 307`, 79 rows). The first street match is a
  neighbour: 66 Franklin St #307 read ADKINS (unit 104), 10401 Grosvenor #919 read an LLC (unit 1018).
  `street_unit()` + a unit regex anchored on `UNIT|APT|STE|#` with the grid's own separators tolerated;
  32 unit keys re-run — 19 land on the right unit, 13 are honest misses.
- *MDLANDREC MFA under concurrency.* 4 Firecrawl workers logging in at once → 1 in 4 searches hit the
  emailed access-code step, then EVERY login did; after a pause a single sequential login was clean
  (17 s per search). The deed leg runs with one worker (~1–2 min per record, 2 terms each).
- *Estate Search by name returns namesakes decades back.* 12 single-candidate hits with no research DOD:
  8 were estates from 1980–2016 on records that sit on the 2026 inheritance list — a namesake or the prior
  generation. Rule: a name-only hit joins only when the grid DOD is within `MAX_DOD_GAP_YEARS` (3) or ±45
  days of the research DOD; everything else stays in `hits` as review (`--rechoose` re-applies the rule
  without a scrape). 40 chosen → 32 after the rule; 21 review; 113 no estate (58 with a 2025/26 DOD = no
  probate opened yet — the note now says so instead of "no case on file").
- *`title_for_md` read `owner_name` / `mailing` / `premises`* — keys `parse_sdat_result` never wrote
  (`owner_names`, `mailing_address`, `premises_address`); the MD path had never been exercised and would
  have stored every title with an empty owner list.
- *Nicknames on the roll*: Steve Wilmot vs WILMOT STEPHEN C, Donald Zink vs ZINK DON L TRUSTEE were
  classified "not the decedent"; `_NICKNAME_MAP` forms now count as PROBABLE (confirm on the deed),
  `dp_title --reclassify` re-runs the classification on stored entries.
- *`court_entry_mdva` dropped the anchors' `estate_number`* (it read `case_number` only), so every MD court
  entry had `case_no: None`; `_v5_has_case` still stays False for MD (no party list).

**Numbers (interim, deed leg running):** 305 of 334 parcels read; 215 decedent on title, 21 probable,
69 NOT on title, 196 with a co-owner who is not the decedent, 59 in trust, 108 household subjects;
199 of 334 with an estate number (170 anchors + 29 by name); 795 Firecrawl credits. Plan rebuilt into
`rerun_v5/pilot_md/` with tags / REL names / phones identical to the live plan; notes max 3,424 chars.

## DP V5 0210 batch - the address is the residence, not the estate's property (2026-10-02)

Basem tagged 12 records `DP V5 0210` and asked for the v5 method on them (run dir `output/dp_v5_0210_20261002/`,
REPORT.md there). All 12 sat at status Deep Prospecting with zero phones. The v5 pass (title, DC portal party
lists, Recorder of Deeds index, obituaries, SmartSkip, Tracerfy, Trestle) showed what the earlier batches could
not see:

- **6 of the 9 DC records carried a case number that was not the owner's.** The DC probate pulls write the
  decedent's RESIDENCE as the property and the PR in the owner slot. 301 Tingey St SE is the Foundry Lofts rental
  (every parcel is SEFC 160 LLC); 635 Edgewood St NE #422 is DC Housing Authority; 3719 Grant Pl NE was sold by
  the Kelly family in 2003, a year after the decedent died, and the 2026 case probates a 2002 death; 1015 50th Pl NE
  carries the sister's South Carolina foreign estate (2026-FEP-000054), not the owner's; 4529 Douglas St NE
  carries the estate of the owner's granddaughter-generation heir - the OWNER's own 2023 death (2024-ADM-001109,
  closed) surfaced only through a portal NAME search ("Matthews, Frank"); 5180 Hanna Pl SE is a typo for 5120.
- **The vendor matched a Register of Wills estate to a parcel on the surname alone.** W125319 (Montgomery, FP) is
  Ralph Minor Lee, the New York puppeteer who died in Manhattan 2023-05-12, PR his widow Casey Compton at Westbeth;
  7113 Panorama Dr is owned by "LEE RALPH L & J M", principal residence - a different family. Not a lead.
- **The co-owner can be the PR.** 1727 P St NW #B: Megan E O'Donnell died 2025-01-08; the DataSift owner Pia Tamm
  is the surviving co-owner AND the court-appointed foreign PR - one signer. The record's PR field held the decedent.
- **Title can already have moved to the heir.** 5120 Hanna: Hilda Mae Hicks died 2026-04-20; a 2026-07-01 deed put
  the heir Pierre Fletcher (the DataSift owner) on title alone, with no PR on the party list.
- **The Tyler portal's Parties service answers HTTP 500 headless** (both CaseSummariesSlim and Parties); the same
  calls succeed in a headed browser. `court_lookup_dc.py --headed --cases {...}` and `court_name_search_dc.py`
  in the run dir are the by-case and by-name drivers (the name search returns every case type; filter ADM/FEP).
- SmartSkip namesake trap again: the co-owner "Bernetta Francis" at 4529 Douglas resolved to a 53-year-old
  Francis in Suitland; the real 79-year-old co-owner came back as that subject's PARENT at the court's address.
- Spend: SmartSkip $1.65, Tracerfy $0.08, Trestle $0.65, Firecrawl 9 credits. 8 records pushed and read back,
  4 got a pinned NOT A TARGET note; the pre-v5 tags (DP / VA DP / MD DP / Deep Prospecting 2.0) came off the one
  record that carried them. Open for Basem: the Hanna address fix, the 4 lane removals, the three Schenkel
  duplicates (c09ccc58 / 88630b5a / aeb502c7), deed images where vesting decides.

### Merging duplicates by address (Schenkel, 2026-10-02)

Basem: "Make all addresses read the same - that's how those would merge." Three records for 10101 Ridge Manor Ter
#2000B (c09ccc58 June Inheritance / v5 record, 88630b5a June RoW with Paula as 'owner', aeb502c7 July IDI 'Willam').
Procedure that worked, with read-back after every step: (1) back up all three (detail, custom fields, board, logs ->
`rerun_v5/schenkel_backup_pre_merge_20261002.json`); (2) fold lists, tags, estimate value, the NO ANSWER disposition
and Paula's extra scored lines onto the v5 record; (3) `PATCH property/<dup>/ {"address": {...exact survivor
street...}}` -- DataSift collapsed the pair within seconds, and BOTH times the record that received the patch survived
and the other uuid 404'd (the opposite of what the 4803 S Polling House note implied). Lists and tags carried over;
the board note, PR/REL custom fields, status, assignee and estimate_value did not; phones pooled only after the owner
block was patched to the same name + mailing (the orphan-entity absorption). (4) `schenkel_restore.py <uuid>` put
status/value/dialer back, removed the dead relatives' and stale-tagged numbers, re-upserted the clean set, blanked
REL2-5, deleted the pointer note; (5) `push --force` on the key re-landed the v5 note and PR/REL1 fields (the pusher
resolves by address when the logged uuid 404s). Final: ONE record, 88630b5a, owner William Schenkel, prospecting,
Ahmed Galal, 9 phones (Pr.1-7 Paula, Rel1.1-2 David), 6 lists, 15 tags, pinned v5 note, QA clean.

## DNC check — scrub cache, scorer override, read-only agent (2026-10-05)

Before this, nothing checked dial-sheet numbers against a Do Not Call list. The only automated gate
was Trestle's litigator add-on. Tracerfy returns a per-phone `dnc` flag, and the main pipeline threw
it away. DataSift's DNC phone statuses (`DNC` / `CORRECT_DNC` / `WRONG_DNC`) were written only by
the SMS opt-out flow, and the record-level `dnc` / `opt_out` booleans count 0 on this account.

Basem chose a commercial scrub API, plus a read-only checker agent with a separate writer that runs
on a go. Built:
- `src/dnc_client.py` — per-number cache (`output/dnc_cache.json`), one entry per source (vendor /
  tracerfy / datasift). The rules:
  - A hit never expires and no source clears it.
  - Secondary sources only add hits.
  - Clean = a vendor answer no older than 31 days (the FTC refresh window); an older one is stale.
  - `scrub_remote` raises on a missing adapter or a partial answer, and never returns "clean"
    for numbers it did not get back (the `llm_client` lesson).
  - The vendor adapter (`_ADAPTER`) is empty until a vendor is picked (`docs/api/dnc_vendors.md`).
- `phone_validator.evaluate_phone(..., number=)` returns `DNC` / keep=False after the litigator
  check. `dpd_lane_phone_score.resolve_number_tag` and the `resolve_tag` functions in
  `reskiptrace_fix.py` and `not_related_requalify.py` pass through `dnc_client.override_tag`.
  `DNC` is in `SCORE_TAGS`, ranks 8 (uncallable), and Litigator Risk keeps its own tag.
- `src/scripts/dnc_scrub.py` — cache-only by default. It harvests DataSift DNC statuses from the
  sheet and any `"dnc": true` phone in `--harvest` JSON, and writes `dnc_review.csv`. Exit codes:
  0 all checked, 2 some stale/unchecked, 1 error or zero numbers. `--spend [--limit N]` is paid.
- `src/scripts/dnc_status_push.py` — the writer: dry run by default, `--probe` then `--commit`.
  It sends the full owner phone list once per owner, changes only the DNC numbers'
  status (CORRECT→CORRECT_DNC, WRONG→WRONG_DNC, else DNC), and reads back status and tags. Rows
  keyed `street|zip` (DP dial sheets) are skipped as "no uuid".
- Agent `.claude/agents/sift-dnc-checker.md`. `sift-ops-manager`'s post-batch routine runs it
  cache-only next to the QA audit.
- Guard: `tools/manual/test_dnc_scrub.py` (44 checks).

The first cache-only run, on `output/dp_v5_0210_20261002` (2026-10-05), read 33 rows: 31 numbers,
all unchecked, exit 2, nothing spent.

## Smart Skip 10.5 — SmartSkip on 11 phoneless auto-add records + Trestle on the 2026-10-03 cohort (2026-10-05)

Basem skip traced the 2026-10-03 SiftMap auto-add (filter 12059, "DC P1-03 High Equity + Notice of
Default", 239 records: 221 new, 18 updated) on DataSift on 2026-10-05: 225 records came back tagged
`REISift Skipped 10/26`, 209 of them with phones, 16 with none. He tagged 11 of the 16 `Smart Skip 10.5`
and asked for SmartSkip + Trestle on those and Trestle on everything that was skip traced. Run dir
`output/smartskip_1005/`.

- **Trestle cohort** (`trestle_score_push.py`, a clone of the DPD-pull engine): 214 records pulled (the 11
  excluded), 653 unique numbers, none cached anywhere, all 653 paid (~$9.79, 0 errors): Dial First 316 /
  Second 23 / Third 31 / Fourth 203 / Drop 77 / **Litigator Risk 3** (removed from 2 owners with
  `remove-phones/`, never loaded). Push per record: tier-tagged, tier-ordered phones, `REISift Traced
  10/2026` stamp (the first use of the wizard-path trace tag), `Mail Only` ×4, pinned
  `[DPD Auto-Add 2026-10-03]` rung note. 29 of the 214 are status `Already Sold` (the live
  `Sold Property Cleanup` sequence had already run on them) — scored anyway, every preset excludes them.
  The engine treats a cached verdict with `litigator: None` as UNCHECKED and re-scores it.
- **SmartSkip 11** through the DP batch (`output/smartskip_1005/dp`, export CSV synthesised from the live
  detail reads — no wizard export needed): order `6ac3d9428ca669d0cdc032d5`, 11 entities, $1.65, delivered in
  32 s; 10 of 11 returned at least one phone, 19 relatives / 51 phones; Trestle 56 numbers (~$0.84).
  `research --presume-alive-without-signal`: 8 presumed alive, 3 unresolved (5530 Bass Pl SE — owner Tonya
  Redd IS the PR per the custom fields; 705 Hemlock St NW — RoW list, SmartSkip returned nothing, `DP No
  Numbers 10/2026`; 2913 University Ter NW — see below). Push 11/11 ok, QA 11/11 clean first pass.
  Two trust titles parsed to a person at low confidence (Floyd R Davis Trustee → Floyd Davis, Potter Penneys
  Revocable Trust → Potter Penneys); both returned numbers, both tagged `DP Trust Trustee Inferred`.
- **Research miss to know about:** the strict query `"Hormoz Hekmat" obituary Washington DC` returned zero
  hits and the record went out `DP Death Unverified`, yet an unquoted `"Hormoz Hekmat" Washington DC` search
  returns "In Memoriam: Hormoz Hekmat - KAYHAN LIFE" on the first page (the record is `Zombie` + Vacant).
  The research trail stops at the first zero-hit query; a fallback without the word "obituary" is the fix.
- **Trap (fixed):** `cmd_build` ran `apply_lane_rule(lists, tags)` on the plan's ADDITIONS only, so
  `derive_entry_tags` saw "no entry tag at all" on records that live-carry `Priority 1` and added
  `Priority 2` to 9 of 11; the two Register-of-Wills-list records correctly derived `FTM`. Now the rule sees
  prep's Tags column (`existing + tags`) and the plan keeps only what it adds. Caught in the dry run; nothing
  wrong reached the account.
- Not done: assignment (224 of the 225 are unassigned; the standing rule says split across the 5 dialers
  after a trace, but routing P1 auto-add records into a call lane is still the open decision from
  2026-09-08), and the 5 other phoneless records Basem did not tag (600 Tuckerman St NW, 3181 Porter St NW,
  1524 Allison St NW, 4620 B St SE, and the LLC at 5631 Macarthur Blvd NW).
