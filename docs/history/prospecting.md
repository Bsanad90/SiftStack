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
