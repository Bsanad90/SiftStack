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

