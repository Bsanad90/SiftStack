# Guide notes B1: SiftMap Mastery, First-to-Market Data, Doors Per Deal (+ the county tool pages), Case Studies

Source: learn.datasift.ai pages converted to text (extratxt/). Only facts stated on the pages; nothing added.

---

## 1. SiftMap Mastery (siftmap-mastery.txt; "14 min read", Market Analysis, $0-$297/mo)

### Positioning
- SiftMap = the data engine inside DataSift; where the data strategy starts. Filters on property type, value, equity, ownership duration, distressors, AI/Investor Score, geography down to neighborhood.
- "Lead precision engine": pull + market same day, no build-out ("precise list this morning, dialing this afternoon"). First-to-market county sourcing is fresher/cheaper per contract but needs a 25-35 day build; add it later.
- Prerequisite: Market Finder Workflow (picks counties, ZIPs, neighborhoods). SiftMap pulls the records.
- Filter Layering Funnel: 4 layers (base characteristics -> geography -> Pro distressors -> AI score); each shrinks list, raises precision. Ramsey County, MN example: ~270 of 48,000 survive.
- Why start with AI/Investor Score (live SiftMap supply, Knox + Blount TN, real sold data): Score 80 ~30.6 doors/deal; 90+ ~22.8; broad all-SFR baseline ~218. 90+ band: fattest margins 56-58%, ~$5.70M-$7.22M gross deal value per 1,000 doors worked.

### Orientation (steps)
1. Open SiftMap: DataSift dashboard -> SiftMap in left nav -> map view + filter panel on left.
2. Filter categories:
   - Basic: estimated value, beds/baths, prospect list presets (equity tiers, foreclosure statuses). Basic panel = MLS status, listing price, foreclosure indicators; all plans.
   - Advanced: sq ft, year built, lot size, vacancy, absentee status, cash buyer history, MLS status, days on market.
   - Pro (requires SiftMap Pro): geographic targeting, distressor indicators, owner details.
3. Property pin -> 4 tabs: Property (structure, land, tax), Owner (name, address, mortgage, equity, other properties), Comps (comparable sold), History (transactions, mortgages, MLS, foreclosures).

### Plan record limits
- Every plan has free monthly records. Max 10,000 per single upload. Extra records $5 per 10,000.

| Plan | Monthly free records | Skip trace | SiftMap Pro |
|---|---|---|---|
| Professional $149/mo | 10,000 | $0.15/record | +$297/mo add-on |
| Business $299/mo | 25,000 | $0.15/record | +$297/mo add-on |
| Expert $499/mo | 50,000 | Unlimited | Included |
| AI $1,250/mo | 100,000 | Unlimited | Included |

- Who can add records from SiftMap: only Sensei/Owner, Super Admin, Admin, Marketer. View-only: Cleaner, Acquisitions, Dispositions, Lead Manager, Researcher, Prospector.

### Filter Layer 1: Base characteristics (5 filters)
- Validate every range against your Sold Properties analysis (what actually trades); tighten/expand from sold data.
- Asset Type: start Single Family Residential. Also supported: mobile homes, townhouses, multi-family, commercial, land, condos, RV parks, warehouses. Expand (e.g., manufactured, duplex) only if Sold Properties shows activity. Start narrow: one asset type, one strategy.
- Estimated Value, "60% price range": ~40% below to ~20% above county median home value (from Market Finder; "do not guess"). Example median $375K -> ~$200K-$600K. Most important filter for cost efficiency.
- Year Built: 2000-or-earlier default start. Heavy post-2000 markets (Phoenix, Austin, parts of FL): 2005 or earlier. Older markets (Midwest, Northeast): 1990 or earlier. Sold data decides.
- Years Owned: 5+ minimum (1-4 yr owners: near-peak price, low equity, no motivation). Turnover signal: if this filter drops count ~50%, half the area changed hands in 5 years = active/competitive market; factor into channel strategy.
- Equity: 30%+ floor. TX and CA exception: 20% can work.
- Equity presets: Low Equity (0-20%), High Equity (30-99%), Free and Clear (100%).
- Standard panels (all plans): MLS Filters (Status, Listing Price, Sold Date); Foreclosure Filters (Auction Date, Notice Date, Status, Notice Type); Financial Details (Equity %, Estimated Value, Last Sales Price, Suggested Rent, Assessed Value) -> set 60% range + 30% equity here; Property Details (Heated SqFt, Lot Size, Year Built, # of Units, Vacant status, Flood Zone).

### Filter Layer 2: Geography
- County (all plans): mid-size metro, expect 30,000-60,000 records after base criteria. Marketing: bulk sequential via power dialer (ReadyMode); cold call first, always, never lead with SMS; text + voicemail follow up. Cannot afford to mail/knock 48,000.
- ZIP (all plans): Market Finder ZIP Code Analysis ranks by median sales price, days on market, median home value, investor transaction volume. Take top 3-5 ZIPs -> SiftMap geo filter -> 48,000 drops to ~20,000-25,000. Marketing: bulk sequential + direct mail.
  - Warning: dark heat-map ZIPs mislead (high investor volume can coexist with 75+ DOM). Cross-reference DOM and median price.
- Neighborhood (SiftMap Pro): Market Finder Neighborhood Analysis -> Pro neighborhood filter. Bad ZIPs can contain great neighborhoods (Ramsey 55108 poor overall; Como Park North inside it excellent: low DOM, active investors, in 60% range). 200-500 records -> niche sequential, full 3-day cadence: call, text, direct mail, door knock.
- SiftMap Pro $297/mo add-on; included in Expert ($499) and AI ($1,250). Required for neighborhood filtering, distressor indicators, expanded owner details (and buyer data).
- Do: use Market Finder rankings; cross-reference investor volume vs DOM; re-evaluate quarterly. Don't: pick darkest heat-map ZIP blind; ignore neighborhoods in weak ZIPs; assume last quarter's top area still best.

### Prospect List Presets (auto-assigned lists on add)
- Records added from SiftMap get system lists automatically; no manual tagging needed.
- 8 free lists (all plans): Free and Clear (100% equity), High Equity (30-99%), Low Equity (0-20%; novation/subject-to candidates), Negative Equity (short sale/sub-to/novation), Owner Occupied (emotional; longer nurture), Absentee (landlords, inherited, out-of-state; higher motivation), Pre-Foreclosure (early; high urgency, time-sensitive), Foreclosure (active, auction dates set).
  - Foreclosure is NOT first-to-market: lis pendens fired earlier, most saturated list; needs higher-caliber caller + premium touches.
- 24 Pro distressor lists (auto-assigned when added via SiftMap Pro):
  - Pre-foreclosure variants: Notice of Default, Lis Pendens, Court Order, Final Judgment.
  - Liens & Financial: Tax Lien, Federal Lien, HOA Lien, Judgment Lien, Tax Delinquent, Low Income, Short Term Loan.
  - Life Events & Distress: Probate, Estate, Bankruptcy, Eviction, Vacancy, Divorce, Credit Distress.
  - Demographics: Senior Homeowners (2nd-highest reason people sell).
- List stacking: one property can sit on multiple lists; more lists = higher motivation signal.

### Filter Layer 3: SiftMap Pro distressors (4 groups)
- Homeowner (5): Senior Owners (55+/65+); Absentee (Out of State) — gold with senior + multi-property; Multi-Property Owners (3-6) — portfolio-fatigue sweet spot; Investor Type (investor / landlord / primary resident; target tired landlords); 15+ Years Ownership (small niche, high conversion).
- Property (5): Pre-Foreclosure (4 variants, rising urgency); Vacant (no utilities; abandonment/move-out/estate); Eviction; Code Violations; MLS Status (active/pending/expired/withdrawn — expired + withdrawn are strong signals).
- Financial (7): Tax Delinquent (FL: 7.5% of private sellers tax delinquent); Tax Lien; Federal Lien (IRS/federal); HOA Lien; Judgment Lien; Low Income; Short Term Loan (ARM/balloon reset).
- Legal & Life Events (5 + Obituary): Obituary (native, Pro); Probate (long, top-of-funnel, nurture); Estate (held in estate, pre-probate); Bankruptcy; Divorce; Credit Distress.
- Native Obituary:
  - Notice-of-death flag on the owner; most first-to-market source, least saturated, nearly uncontested.
  - "Last obituary date" field for timing. Auto-adds weekly, nationwide. If family files probate, obituary flag is removed and replaced by probate flag.
  - Access: Expert $499 (Pro + unlimited skip trace) or Pro add-on (~$297) on Professional $149. No AI plan needed.
  - Benchmarks: ~12% contact rate, 7% lead rate off SiftMap obituary + skip trace alone (before deep-prospecting heirs). Deceased niche in Elite band, ~14.8 doors/deal (Pre-Probate/Deceased, real sold data).
  - Rules: never mention the death; open soft ("do you have any plans for the property?"); mail goes to the family; deep-prospect the heir; NEVER cold text an obituary/deceased record.
- Pro filter panels: Homeowner + Property Distressors with a "Stacked" option (two or more distressors); Advanced Geography (ZIP, City, Neighborhood, Municipality, Include/Exclude); Data Details (quality filters); Owner Details (Owner Age, Mailing Address is Post Office = absentee indicator, Owner is Investor, Owners with Multiple Properties).
- Verify targeting on records: summary card shows AI scores (e.g., 7400 Battle Creek Ln 100/86/84), equity (89%), status (Off Market), distressor tags; Lists tab shows matched Prospect Lists (e.g., High Equity, Owner Occupied) and Distressors PRO (e.g., Pre-Foreclosures, Bankruptcy Properties).

### Filter Layer 4: AI Scoring
- Lead with it ("single most efficient lever"). Applied last in funnel, 50-100 filter cuts list ~50%.
- Score 80 ~30.6 dpd, 90+ ~22.8, baseline ~218 (Knox/Blount).
- RULE: run parallel, do not AND-stack. Distress AND Score 80 = ~48.4 dpd vs 30.6 for Score 80 alone. Pull score and acute distressors as separate lists.
- Three scores:
  - Investor AI Score (Off-Market): likelihood of off-market sale to investor; primary score for wholesalers/flippers/buy-hold. Set 50-100. Included in AI plan ($1,250); $297/mo add-on for Expert. County-specific; only works for subscribed counties.
  - Realtor AI Score: likelihood to list on MLS with agent before listing; agents/novation. 50-100. $297/mo add-on, all tiers, nationwide.
  - Investor AI Score (On-Market): every MLS listing nationwide, likelihood of investor purchase; novation. $297/mo add-on, all tiers, nationwide.
- Novation example: Off-market AI 50+ + Realtor AI 50+ + 15-30% equity; 880 records -> 346 after adding Realtor score.
- AI Scores panel: range inputs for all three; set minimum 50.
- List-size impact: ZIP-filtered ~25,000 -> ZIP + AI ~12,000 (~50%); Neighborhood + distressors ~542 -> + AI ~270.

### Building & saving presets (3 tiers)
- SiftMap saves filter configs as named presets (select from dropdown instead of rebuilding ~8 filters).
- T1 Hyper-Targeted, 200-500 records: base + top 3-5 neighborhoods + 2-3 stacked distressors + AI 50-100 if available. Niche sequential, full 3-day cadence (call, text, mail, knock). Name e.g. "Ramsey T1 - Como Park Senior Absentee". Review weekly (know every property's status).
- T2 Focused, 20K-25K: base + top 3-5 ZIPs; no distressor stacking. Bulk sequential + mail (too big to knock). Name "Ramsey T2 - Top Zips Base Criteria". Review monthly; promote top ZIPs to T1.
- T3 Volume, 48K+: base only, county-wide. Bulk sequential via power dialer (ReadyMode); cold call first always, never lead with SMS: calls ($0.03-$0.06), then text ($0.01) + voicemail, then direct mail for reachable tail. Name "Ramsey T3 - County Cold Call". Review quarterly.
- 20 default presets on every account (Judgment, Quick Resale, Adjustable Loans, Negative Equity, Cash Buyer, Bank Owned REO, more). Tabs: Default Presets, Saved Presets. Custom presets from Market Finder are where real targeting happens. Presets can be favorited in the dropdown.
- Ty's two parallel strategies:
  - AI Score presets x 3 geo tiers, each Single Family + AI Score 50+: Tier 1 Zips (niche sequential), Tier 2 Zips (bulk + mail), Tier 3 county-wide (power dialer). Example AI 50+ preset matched 16,498.
  - Stacked Distressor preset: Single Family + 50% Equity + Stacked (2+ distressors, e.g., senior+absentee, tax delinquent+vacant, pre-foreclosure+multi-property). No AI needed, only Pro. Example matched 8,795.
  - Re-run Market Finder every 90 days and update presets.
- Niche vs bulk: most SiftMap lists fit bulk sequential (power dialer, cold-call-led); niche sequential fits lists under 1,000 from a high-priority neighborhood/ZIP with stacked distressors.

### Adding records to CRM (steps)
1. Apply filters; check record count against plan monthly limit (example 299).
2. Click blue "Add to Account". Max 10,000 per upload; larger lists need multiple pulls.
3. Assign a tag (e.g., "SiftMap_Mar2026_T1_ComoPark") to track which pull produced which records; optionally assign a list. System prospect lists auto-assign.
4. Verify in Activity tab/log: record count, upload date, duplicates skipped.
- Records flagged "incomplete" (missing phones/addresses, property+owner data present): have a VA research manually, ~200/week; "gold everyone else ignores".

### Marketing allocation by list size
| List size | Cost/touch | Channel |
|---|---|---|
| 48K | $0.03-$0.06 | Cold call only |
| 20-25K | $0.50-$2.00 | Bulk sequential + mail |
| 500 | multi-channel | Niche sequential (call, text, mail, knock) |
| 200 or fewer | $1.50-$4.00+ | Personal attention + deep prospecting |
- Principle: better 4,000 targeted records across 7 counties than 48,000 in one county blasted.

### 17 most powerful lists (9 singles, 2 age filters, 6 stacks); all pullable in SiftMap same day; enrich with owner age, equity, ownership duration
- Singles (FTM = also sourceable first-to-market from county; Sift = SiftMap): Probate (FTM; highest avg deal size in "the vault"), Vacant (Sift; "#1 most important motivation factor", stacks with everything), Pre-Foreclosure NOD (FTM; 30-90 day window before auction), Tax Delinquent (FTM; 7.5% of FL private sellers), Divorce (Sift; court-mandated, forced seller), Bankruptcy (Sift; court-ordered liquidation), Eviction (FTM), Code Violations (FTM), Obituary (Sift; deep-prospect heir; no cold text ever).
- Age filters: Owner Age 65+ (Sift; 2nd-highest sell reason; senior downsizer/estate/retirement stacks); Owner Age 25-35 (Sift; overleveraged; pair with Low Equity + Pre-Foreclosure).
- Power stacks: Probate + Vacant (FTM/Sift, highest-conversion vault-proven stack); Pre-Foreclosure + Vacant ("zombie properties"); Age 65+ + High Equity + Free & Clear (Sift, premium downsizing, $50K-$150K+ deals); Absentee + Vacant (Sift, workhorse, both free on every plan); Tax Delinquent + Pre-Foreclosure (FTM, "Avi's combo" for $100K+ deals); Senior 55+ + Absentee + Multi-Property (Sift, "gold leads": 14 contracts in 2021, ~3 houses each, $1.3M revenue).
- "Two filters is good. Three is gold." 200-record hyper-niche outperforms 20,000 spray-and-pray.

---

## 2. First-to-Market Data (first-to-market-data.txt; 25 min read)

### Positioning / economics
- FTM = distress records sourced directly from county offices day/week they go public, before aggregators/platforms.
- Consistency tier of Data Priority Pyramid, NOT the start. Lead with same-day SiftMap precision (Investor Score + acute distressor lists); build FTM once those produce.
- "The harder a list is to reach, the less you compete for it." Moat = saturation, not build difficulty.
- FTM pulled same-day: $500-$2,000 cost per contract once built. Same list resold by aggregator = saturated.
- Build: 25-35 days (two county auth systems, Apify, Dropbox, Trestle, captcha, courthouse runners). Sales cycle 4-7 months (pulled-probate average).
- Funnel top: source aggregate ~66 doors/deal, typical 50-75. Builds next quarter's pipeline while SiftMap pulls close this month.

### Six sources (difficulty)
- Probate — Hard. Where: Probate Court Clerk, Surrogate's Court (NY/NJ), Register of Wills (PA/MD), County Clerk Probate Section (also Orphans' Court PA/MD). Format: case filings with PR number, decedent name, executor info; often NO property address. Many counties in-person; portals lack export. 22-38% have deceased owners needing deep prospecting. Cost: free; skip trace $0.10-0.15/record; deep prospecting ~$1-4/record with Claude.
- Tax Sale / Tax-Delinquent — Medium. Where: County Treasurer, Tax Collector, Trustee's Office (TN), Commissioner of Revenue. Format: PDF schedules, Excel, portals. Two flavors: delinquency list (all unpaid) and auction schedule. Free; some nominal bulk fees.
- Foreclosure — Medium. Lis pendens at Register of Deeds or newspaper legal notices. Median notice-to-auction 34 days (Knox); marketing window Day 1-30. Where: County Clerk Civil Division, Register of Deeds, newspaper legal notices. Free. 38% deceased-owner rate -> deep prospecting often needed.
- Pre-Foreclosure — Medium. Default notices before formal foreclosure; earliest signal, longest window. Where: County Recorder, Register of Deeds, court docket (judicial vs non-judicial state). Free; some recorder subscription portals $25-50/mo.
- Code Violations — Easy. City-level usually. Where: City Code Enforcement, Building & Zoning, Community Development, Housing Inspection Services. Portals/FOIA exports/published lists; FOIA typically within 10 business days; fees $5-25.
- Eviction — Medium. Where: General Sessions Court, Civil Court Clerk, Small Claims. Target the landlord (owner), not the tenant; skip trace the owner. 2nd/3rd eviction = more ready to sell.

### Where data lives (3 methods)
- Online portals: Recorder/Register of Deeds (deeds, lis pendens), Treasurer/Tax Collector (delinquency, auctions), Probate Court Clerk portal, City Code Enforcement portal. Search "[County] + [record type] + search" or "[County] + recorder online"; FTM County Data Skill automates.
- In-person courthouse: bring photo ID, phone/camera to photograph screens, USB drive if export allowed, list of record types. Can't go: TaskRabbit or Investor Bootz; one trip covers 2-3 list types.
- Publication notices: newspaper legal notices, county-designated legal publication sites, state legal notice aggregators, county "Public Notices"/"Legal Notices". Contain address, case number, auction date. Use Claude to convert notice text to structured CSV.

### County difficulty tiers
- Easy (online, exportable, free): recurring weekly/monthly calendar check; automate; tax delinquency updates monthly, code violations weekly.
- Medium (online, limited: subscription, CAPTCHA, one-at-a-time): targeted searches; bulk via FOIA or county bulk subscriptions ($50-200/yr). E.g., probate portals, Register of Deeds with per-search CAPTCHA.
- Hard (in-person only): go or hire TaskRabbit ($20/hr) / Investor Bootz; photo every screen; process after. Strongest competitive advantage.
- Needs Help (affidavits, license verification, 2-hour weekly windows, disorganized): TaskRabbit task under "Personal Assistant" with courthouse, record types, format instructions + example screenshots; build ongoing runner relationship for monthly pulls.
- Target: courthouse to first contact in 48 hours (six-step path).

### Probate workflow
- Search case types: Estate, Probate, Administration, Heirship Determination. Date range last 30-90 days. Capture decedent name, case/PR number, executor name + address, date of death.
- Keep residential real property estates only (skip personal-property-only). Focus on cases with executor appointed.
- Missing address problem: filing lacks property address -> no mail, skip trace, or comp. Manual: assessor by decedent name, cross-reference deeds, check trust-held; 20-45 min/record, $1-3 at VA rates, 10-15/day, variable accuracy. Probate Property Finder skill: 2-5 min/record, $20+/mo Claude Pro, 50-100/day, bulk CSV, high accuracy (assessor = source of truth); input decedent name, county, state (+ optional PR number, executor, DOD); outputs CSV with addresses, parcel IDs, property types, assessed values, Sift-ready.
- Processing for CRM (after address): skip trace -> upload -> assign filter preset -> niche sequential.
  - Owner swap: change CRM owner from deceased to executor/heir; mailing = executor's address.
  - Tag: "Probate" + sub-tags (multiple heirs, vacant, occupied by family).
  - Deep prospecting queue: 22-38% need L3-L4 research.
  - Not-interested cadence: Probate 45 days (Foreclosure 15, general 90).
- 22-38% of all distressed lists have deceased owners (higher for probate/foreclosure).

### Tax sales
- Auction Schedule Lists: published 30-60 days before auction; address, owner, amount owed, auction date; check treasurer/trustee monthly. Claude prompt: "Extract this tax sale data into a CSV with columns: Property Address, Owner Name, Amount Owed, Auction Date, Parcel ID."
- Tax Delinquency Lists: full unpaid list, often thousands, often annual Excel. Filter residential + 2+ years delinquent; high equity = motivation.
- Tax Lien Certificates (lien states): public record; target certificates nearing redemption deadline.
- Do: Claude PDF extraction; check monthly; filter residential high equity; cross-reference DataSift for existing records before upload. Don't: hand-type 200+ rows; wait until week before auction; skip cleaning (remove commercial, vacant land, duplicates); assume every delinquent is a deal (liens can exceed value).

### Foreclosures
- Lis Pendens (Register of Deeds): Day 1, digital, full (address, owner, lender, case #, filing date), low competition.
- Newspaper Legal Notice: Day 20+ (3-4 weeks before sale), text needing extraction, partial (legal description, not always address), higher competition.
- Reference analysis: 12 months Knox, 537 records, 248 unique properties.

### FTM County Data Skill
- Input county/state -> every office, portal, phone, difficulty per FTM type.
- Priority A core: Probate/Heirship, Foreclosure/Auction, Tax Sale, Tax Delinquency. B standard: Code Violations, Condemned Structures, Mechanic's Liens, IRS/State Tax Liens. C extended: HOA/Condo Liens, Utility Shut-offs, Building Permits (demolition/repair), Bankruptcy.
- Steps: download .skill -> claude.ai Settings import -> enter county. Requires Claude Pro ($20/mo).

### Runners
- TaskRabbit ~$20/hr, $25-50/trip, "Personal Assistant", 2-4 hours. Investor Bootz: RE-specific runners, varied price. One trip $40-80 covers 2-3 list types; one $10,000 assignment = 100x+ ROI.
- Steps: post task (courthouse address, records, date range) -> written instructions + example screenshots (photo every screen, USB export if possible, note office name/contact) -> Claude extracts from photos/PDFs -> clean, format, skip trace, upload.

### FTM into niche sequential
- Flow: cleaned FTM CSV -> DataSift upload tagged by list type -> dedicated filter preset per list type -> 3-day cadence, cold-call-led: call, then direct mail, then text.
- Do: tag every FTM record by list type (Probate, Tax Sale, Foreclosure, ...); dedicated preset per FTM type; niche sequential for <1,000 records; not-interested cadences (Probate 45, Foreclosure 15, General 90 days).
- Don't: mix FTM with purchased-list records in same filter; power-dial small FTM lists (<1,000); cold-text deceased/probate (run cold call, voicemail, mail, then deep-prospect heir/executor); skip tagging (lose cost-per-contract by source); skip not-interested cadences (20-30% of deals come from them).
- Niche Sequential guide = 12-filter setup + 3-day cadence.

---

## 3. Doors Per Deal (doors-per-deal.txt; 15 min read; companion to Data Priority Pyramid)

### Definition
- Doors per deal (dpd) = list size / sold-to-investor deals from that list. Properties marketed to close one deal; lower is better. Use it to rank every list, score, ZIP before spending.
- Worked: Absentee + Low Income, Knox: 1,779 / 51 = 34.9 (Strong).
- Blind Knox SFR pull 206; AI Investor Score 90+ 25.3 (8.2x more efficient). Blind pull is the number to beat.
- "Don't pull 10,000 records from PropStream and just blindly market to it."
- Knox same county/6 months: Blind SFR 206; AI 90+ 25.3; Absentee + Low Income 34.9; best ZIP 37914 104.9.

### Bands
- Elite / Strong / Volume. Band decides how you work the list: Elite + Strong = under 35 (precision mode); Volume = over 35 (bulk mode). (Elite threshold not numerically stated beyond "under 35" grouping; obituary/deceased ~14.8 called Elite in SiftMap page.)

### Efficiency ladder (AI cutoff; Knox)
- Example: cutoff 80+ = 32.0 dpd, Strong, 6,503 records, 203 deals, 6.4x, $54k median gross, 27.2% of county investor deals still inside.
- Precision costs coverage: AI 90+ is 8.2x but holds 12.6% of Knox deals; pyramid coverage tiers exist for the rest.

| AI cutoff | List size | Deals | DPD | Lift | Coverage | Median gross |
|---|---|---|---|---|---|---|
| >=5 | 153,675 | 742 | 207.1 | 1.0x | 99.6% | $54k |
| >=10 | 153,675 | 741 | 207.4 | 1.0x | 99.5% | $54k |
| >=15 | 147,769 | 727 | 203.3 | 1.0x | 97.6% | $54k |
| >=20 | 113,919 | 682 | 167.0 | 1.2x | 91.5% | $56k |
| >=25 | 86,418 | 635 | 136.1 | 1.5x | 85.2% | $62k |
| >=30 | 67,842 | 602 | 112.7 | 1.8x | 80.8% | $62k |
| >=35 | 54,656 | 571 | 95.7 | 2.2x | 76.6% | $62k |
| >=40 | 44,583 | 533 | 83.6 | 2.5x | 71.5% | $63k |
| >=45 | 36,391 | 504 | 72.2 | 2.9x | 67.7% | $63k |
| >=50 | 29,546 | 455 | 64.9 | 3.2x | 61.1% | $61k |
| >=55 | 23,246 | 412 | 56.4 | 3.7x | 55.3% | $59k |
| >=60 | 17,963 | 365 | 49.2 | 4.2x | 49.0% | $57k |
| >=65 | 14,143 | 323 | 43.8 | 4.7x | 43.4% | $56k |
| >=70 | 11,508 | 298 | 38.6 | 5.3x | 40.0% | $54k |
| >=75 | 8,918 | 246 | 36.3 | 5.7x | 33.0% | $55k |
| >=80 | 6,503 | 203 | 32.0 | 6.4x | 27.2% | $54k |
| >=85 | 4,088 | 143 | 28.6 | 7.2x | 19.2% | $88k |
| >=90 | 2,378 | 94 | 25.3 | 8.2x | 12.6% | $125k |
| >=95 | 953 | 44 | 21.7 | 9.5x | 5.9% | $131k |

### Payoff axis (read both effort and gross)
- Negative-margin trap: Bad Credit + Out-of-State 10.4 dpd but -$5k/deal. Sharp != profitable.
- Small-sample mirage: Notice of Default 13.6 dpd, $153k gross from 8 deals; directional, not bankable. Act on high-confidence rows first.

### Three levers
1. Stack complementary owner signals: Absentee 105.9, Low Income 62.3, together 34.9. BUT stacking distress on AI score worsens: 48.4 vs 30.6 (922-deal backtest); score already accounts for distress.
2. Move one ZIP over: Knox ZIPs 105-586 dpd (5.6x spread). 37914 and 37917 ~half county baseline; 37922 5.6x harder.
3. Trust strong signals: D4D (Knox+Blount metro) bad roof strongest ~44 dpd (a "5.6x signal"); bad driveway weakest ~180 (dead weight). D4D supply is estimated, metro-level: coverage overlay, never one-to-one ranking.

### Funnel position (dpd measures effort; funnel times it)
- First to Market (obituary, pre-probate, probate): pipeline builder; saturation near zero (often only call); cycle 4-7 mo (probate average). FTM aggregate 66 (typical 50-75).
- Niche Volume (senior, vacant, absentee stacks): volume engine; low saturation; faster cycle (real motivation, no deadline). Senior Knox 198.5; Absentee + Low Income 34.9.
- Deadline Distress (foreclosure, NOD): fast closer; saturation maxed (every dialer runs it); fastest cycle. NOD 13.6 (8-deal sample).
- Ranked by doors alone the funnel reads upside down: sharpest list is most crowded; heaviest keeps producing because nobody works it.
- Field note: door-knocking foreclosure lists had strong traction; the same owners barely answered calls. Saturated lists need higher-quality touch.
- Run all three at once: top builds next quarter, middle feeds this month, bottom pays fastest if touch upgraded. Full read blends dpd, saturation, reachability, cycle speed.

### Methodology
- Window: six complete months Dec 2025-May 2026. SFR, off-market, sold to investor. Knox + Blount TN.
- Supply: live SiftMap list counts today on an account with off-market AI provisioned.
- Deals: sold-to-investor transactions back-tested; if sold to investor and on the list, counts.
- Confidence: high-confidence rows actionable; thin samples below SPECULATIVE divider, directional only.
- Numbers move with window: earlier 922-deal backtest set band 22.8-30.6 for AI 80-90; this window 25.3-32.0. Order never flips. Re-pull quarterly.

### Knox Lists & Combos (Segment | Type | Deals | List size | DPD | Tier | Median gross | Confidence)
| Segment | Type | Deals | List | DPD | Tier | Gross | Conf |
|---|---|---|---|---|---|---|---|
| Absentee + Low Income | Combo | 51 | 1,779 | 34.9 | Strong | $42k | High |
| Bad Credit + Free & Clear | Combo | 44 | 1,783 | 40.5 | Volume | $59k | High |
| Free & Clear + Low Income | Combo | 60 | 2,524 | 42.1 | Volume | $56k | High |
| Absentee + Bad Credit | Combo | 33 | 1,413 | 42.8 | Volume | $54k | High |
| Free & Clear + Out-of-State | Combo | 50 | 2,599 | 52.0 | Volume | $36k | High |
| Bad Credit + Low Income | Combo | 45 | 2,486 | 55.2 | Volume | $56k | High |
| Bad Credit | List | 49 | 3,010 | 61.4 | Volume | $61k | High |
| Low Income | List | 69 | 4,298 | 62.3 | Volume | $54k | High |
| Absentee + Tax Delinquent | Combo | 27 | 1,717 | 63.6 | Volume | $72k | High |
| Tax Delinquent + Senior | Combo | 22 | 1,464 | 66.5 | Volume | $76k | High |
| Free & Clear + Tax Delinquent | Combo | 40 | 2,892 | 72.3 | Volume | $77k | High |
| Absentee + Free & Clear | Combo | 294 | 23,714 | 80.7 | Volume | $43k | High |
| Out-of-State | List | 54 | 4,381 | 81.1 | Volume | $25k | High |
| Tax Delinquent | List | 50 | 4,188 | 83.8 | Volume | $77k | High |
| Senior + Tired Landlord | Combo | 42 | 3,737 | 89.0 | Volume | $65k | High |
| Absentee + Out-of-State | Combo | 46 | 4,370 | 95.0 | Volume | $25k | High |
| Absentee | List | 345 | 36,551 | 105.9 | Volume | $33k | High |
| Free & Clear + Tired Landlord | Combo | 82 | 8,701 | 106.1 | Volume | $68k | High |
| Free & Clear | List | 500 | 61,153 | 122.3 | Volume | $54k | High |
| Tired Landlord | List | 102 | 12,666 | 124.2 | Volume | $72k | High |
| Absentee + Senior | Combo | 65 | 8,471 | 130.3 | Volume | $84k | High |
| Free & Clear + Senior | Combo | 165 | 27,724 | 168.0 | Volume | $62k | High |
| Senior | List | 232 | 46,041 | 198.5 | Volume | $63k | High |
| Absentee + High Equity | Combo | 30 | 8,548 | 284.9 | Volume | $10k | High |
| High Equity + Senior | Combo | 56 | 16,180 | 288.9 | Volume | $76k | High |
| High Equity | List | 193 | 68,886 | 356.9 | Volume | $54k | High |
| *SPECULATIVE below* | | | | | | | |
| Probate | List | 4 | 49 | 12.2 | Strong | $52k | Low |
| Notice of Default | List | 8 | 109 | 13.6 | Strong | $153k | Low |
| Other Lien | List | 3 | 99 | 33.0 | Strong | $73k | Low |
| Estate Sale | List | 2 | 80 | 40.0 | Volume | $45k | Low |
| Bankruptcy | List | 4 | 211 | 52.8 | Volume | $76k | Low |
| HOA Lien | List | 5 | 468 | 93.6 | Volume | $120k | Low |
| Out-of-State + Tired Landlord | Combo | 8 | 1,056 | 132.0 | Volume | $76k | Low |
| High Equity + Tired Landlord | Combo | 18 | 3,307 | 183.7 | Volume | $74k | Medium |
| Bad Credit + Out-of-State | Combo | 15 | 156 | 10.4 | Strong | -$5k | Low |
| Low Income + Out-of-State | Combo | 15 | 167 | 11.1 | Strong | -$5k | Low |
| Pre-Probate | List | 3 | 103 | 34.3 | Strong | -$24k | Low |

### Knox ZIP areas (ZIP | Deals | List | DPD | Gross | Conf), sorted most efficient first
| ZIP | Deals | List | DPD | Gross | Conf |
|---|---|---|---|---|---|
| 37914 | 75 | 7,865 | 104.9 | $54k | High |
| 37917 | 85 | 9,101 | 107.1 | $87k | High |
| 37912 | 40 | 5,709 | 142.7 | $61k | High |
| 37921 | 62 | 9,521 | 153.6 | $105k | High |
| 37920 | 79 | 14,705 | 186.1 | $50k | High |
| 37919 | 47 | 8,785 | 186.9 | $22k | High |
| 37918 | 68 | 14,284 | 210.1 | $62k | High |
| 37923 | 30 | 8,039 | 268.0 | $45k | High |
| 37932 | 31 | 8,432 | 272.0 | $33k | High |
| 37938 | 23 | 6,819 | 296.5 | $37k | High |
| 37931 | 35 | 10,773 | 307.8 | $50k | High |
| 37934 | 30 | 9,997 | 333.2 | $108k | High |
| 37849 | 23 | 9,003 | 391.4 | $70k | High |
| 37922 | 24 | 14,071 | 586.3 | $28k | High |
| *SPECULATIVE (incomplete supply / thin)* | | | | | |
| 37902 | 6 | 10 | 1.7 | $18k | Low |
| 37915 | 13 | 1,065 | 81.9 | $146k | Medium |
| 37909 | 19 | 3,764 | 198.1 | $26k | Medium |
| 37924 | 13 | 5,122 | 394.0 | $103k | Medium |
| 37721 | 13 | 5,187 | 399.0 | $41k | Medium |
| 37754 | 2 | 1,339 | 669.5 | n/a | Low |
| 37871 | 3 | 3,065 | 1021.7 | $2k | Low |
| 37806 | 1 | 1,196 | 1196.0 | $3k | Low |
| 37764 | 1 | 3,035 | 3035.0 | $30k | Low |
| 37916 | 7 | 326 | 46.6 | -$7k | Low |
- Full Knox-Blount Master Workbook (Blount, D4D, neighborhoods, deal-size, methodology tabs) is a separate download.

### Six ways to misread it
1. Effort without payoff (10.4 dpd, -$5k gross).
2. Tiny samples (37902: 1.7 dpd on 10-record list, 6 deals = noise).
3. AND-stacking on AI score (48.4 vs 30.6; owner combos improve, e.g., 34.9).
4. Ranking D4D one-to-one (estimated, metro-level, no live list; extends coverage only).
5. Doors vs dials: doors = records marketed; dials = phone attempts, a separate Day 5 KPI: ~100 correct numbers per deal.
6. Ranking funnel by one number (NOD 13.6 but every dialer owns it; Senior 198.5 keeps producing).

### What the number tells you to do
- Elite + Strong (under 35): precision mode; small list; click-to-dial, aggressive multi-touch, every record worked by name -> Niche Sequential Marketing.
- Volume (over 35): bulk mode; power dialer, cold call first, 6-8 attempts before recycling -> Bulk Sequential Marketing.
- Huge list, no band: narrow before dialing (cut by AI score or a second owner signal) -> SiftMap Mastery.
- Sharp but saturated (foreclosure-grade): upgrade the touch; knock the door -> Saturation and Sales Cycle.
- Validation loop: Sold Properties Analysis computes dpd for any filter before spend.

---

## 4. Doors Per Deal Framework — Community Edition (doors-per-deal-distressors.txt) and county-list-playbook.txt

- NOTE: both files are the same interactive tool shell (county-list-playbook = the tool; distressors file = "Community Edition" with the 27-min Day 1 walkthrough video from the last 5-Day Deal Flow Challenge). The per-county distressor tables are rendered client-side and are NOT in the text; only the method text below is present. Free, no account needed; "Find your county" selector.
- Logic (4 steps):
  1. Start from every SFR an investor bought in the county over the last six months (recorded sales).
  2. Check which lists those homes were on (vacant, probate, tax delinquent, high equity, every list + combination); a sold home on a list credits that list with a deal.
  3. dpd = list size / deals (1,600-door list, 47 deals = 34 dpd). Lower better.
  4. Rank vs county baseline: Priority 1 = pull first (3x+ more efficient than baseline; 10x+ ranks P1 at any list size). Priority 2 = strong, 1.5-3x baseline, second wave. Priority 3 = context rows: regime-demoted, thin samples, tiny slivers, demand-only, or lists the state's rules make unreliable.
- Use this week: pull P1 rows (each row's plan tag shows data access needed), save EACH as its own SiftMap preset, work top to bottom.
- Views/filters: All signals / Lists / Stacks / AI score.
- Combined ranking: all selected counties' signals ranked together by priority and lift, each judged against its OWN county's baseline and regime; top = best dollar across all counties.
- Signal consistency: P1 in every county = portfolio-wide first pull; sharp in one, dead in another = county-by-county budgets.
- Market share ladder ("one work order"): every list, combo, score band arranged in pull order; each rung counts only properties rungs above did not already give you, so its dpd = what that pull really costs next; "market share after" = share of county investor deals the stack now reaches. Rungs re-measured on remainder, so the same list can sit on different rungs with different numbers in the two views ("All lists" vs "All lists + AI score"); score bands soak up deals first. Ladder priorities: P1 cheapest deals, work first; P2 keeps volume at fair price; P3 maximum market share at steep cost per new deal.
- Deal share = % of county investor purchases that sat on the list; small lists often sharpest and cheap to work in full.
- Plan tags: All plans (any tier); Expert plan (SiftMap Pro data: included Expert/AI or +$297 add-on); AI plan (Investor AI Score). Deal Room access identical on all qualifying plans; tier changes only platform features and data reach.
- Stacking rule: AI score and distress lists = PARALLEL pulls; never AND-stack a distress list on the score (score already prices distress; stacking shrinks without sharpening). Stack distress signals on each other (STACK rows); AI band as its own pull.
- Obituary list (Expert plan), "earliest signal there is": positioning list, not quick-flip. Expect ~zero closings first 6-9 months, then a wave. Start respectful low-pressure mail 30-60 days after filing (condolence-adjacent tone, brand first, no hard offer); add calls from month 6 as estate reaches selling window; hold cadence through month 12-24 where most sales land. Family that has seen your name for a year answers when probate mailers arrive.
- First-to-market section: lists pulled at the source on a publishing clock; pull weekly, skip trace fresh filings, reach the seller before a provider list exists. A list nobody can buy is a moat.
- Also per county: "WHERE to hunt" hottest ZIPs; "SFR price range" efficiency by home-value band (AVM-based, reliable even in non-disclosure states); longest bar = fewest doors per deal.
- CTAs: join next live challenge; schedule a call with the team.

---

## 5. Case Studies (case-studies.txt)

- No operational workflow content; proof page. 305+ success stories, 4 phases; counters rendered as 0 in text (dynamic).
- Founders: Ty, Rami, Tyler (co-founders). DataFlik + REI Sift merged into DataSift in 2025.
- Stats: $15K/mo combined marketing spend (live R&D loop), $2-3M/yr combined revenue, 1,000+ daily Sift users, 5,000+ community members; Inc. 5000.
- Lesson: "The deal isn't in the volume. It's in the data ahead of the volume."
- Phase framework: Phase 1 Operator (one county, one list, sequential marketing live); Phase 2 Delegator (4 full attempts, data/admin off your plate); Phase 3 Manager (scale team on overflow, one seat at a time); Phase 4 Owner (deep-prospect every unreached door, instrument KPIs).
- Deal screenshots are images only (no text).
- Recurring CTA across all pages: 5-Day Deal Flow Challenge, next cohort Mon Sep 21-25 (2026), free, live with Ty, for investors who have closed at least one deal.
