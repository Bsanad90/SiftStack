# Doors-Per-Deal: Research & Account Build

> Build history extracted from `CLAUDE.md` on 2026-08-28. Verbatim — the API
> contracts, traps and measured numbers here are the record. Live state and next
> steps live in `CLAUDE.md`.

---

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

**`dpd_presets_verify.py`: checked 73/73 against the server store, missing 0, defects 0, extras 0.** Folders 01–12 hold 6/6/6/6/6/6/6/6/9/6/5/5. Every throwaway (`ZZ …`) preset is gone — deletion is automated now (see below), not a hand step. No record, tag, list, status, sequence or pre-existing preset was altered; the final `dpd_doctor --verify` diff is additions only. **~~The one hand step left~~ DONE 2026-08-31, automated: `ZZ DPD SMOKE 153311` deleted via `dpd_siftmap_presets.py --delete-name`** — the "no delete control" conclusion was wrong (the 08-27 discover pass's DOM capture silently no-op'd, `saved_filters_html: ""`); saved filters are managed at `/siftmap/presets/account`, a paginated table with edit + trash per row (Basem spotted it). 28 saved filters remained, all verified present after the delete.

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

**THE SAVE FILTERS DIALOG, mapped (`dpd_siftmap_presets.py --discover`):** rail button at ~(1324, 84), real mouse click. Title "Save Filter"; required input placeholder `Enter new search name`; optional `Enter description`; a **PRO checkbox "Automatically add new properties that start to match this filter after it has been saved."** — that is an automatic PULL and the script asserts it is unchecked and never ticks it; `Cancel`; `Confirm`, disabled until React receives the name. A saved filter appears under the Presets popover's **"Saved Filters"** heading (`dpd_doctor.capture_siftmap_presets` reads exactly that), each POPOVER row shows only a favorite star — but that is not the whole UI: **the management table at `/siftmap/presets/account` (FILTER NAME / CREATED / AUTO-ADD, star + edit + trash per row, 10/page) is where saved filters are edited and deleted** (found 2026-08-31 from Basem's screenshot; `--delete-name` drives it, with a typed `DELETE FILTER` challenge arming the confirm). "Configure" in the popover navigates to the sibling `/siftmap/presets/default` tab, which is why it looked like it "just closed the popover". Reloading a saved filter appends `id=<n>` to the URL — the comparison ignores that key. **The popover LIST SCROLLS**: with 29 saved filters a row can sit at y = −51 and still report a bounding box, so a click at its centre lands on the map and the read-back sees the unfiltered county (every DC preset "reloaded" to 48,530 on the first verify). `load_saved_filter` now scrolls the row into view and requires the URL to change. Saving a filter does NOT create a List (lists 27 before and after). **One throwaway `ZZ DPD SMOKE 153311` remains in Saved Filters** — nothing in the UI deletes a saved filter; leave it or remove it by hand if a control exists somewhere we did not find.

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
