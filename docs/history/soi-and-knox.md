# Sphere of Influence, Knox FTM & Account Audits

> Build history extracted from `CLAUDE.md` on 2026-08-28. Verbatim — the API
> contracts, traps and measured numbers here are the record. Live state and next
> steps live in `CLAUDE.md`.

---

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

