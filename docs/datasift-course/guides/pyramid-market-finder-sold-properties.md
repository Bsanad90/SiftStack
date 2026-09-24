# DataSift guide notes — batch B2

Sources: data-priority-pyramid, market-finder-workflow, sold-properties-analysis, pendulum-theory, saturation-sales-cycle (learn.datasift.ai). All numbers are as the pages state them.

---

## 1. Data Priority Pyramid (companion tool: Doors Per Deal Framework)

### Core metric
- **Doors per deal (dpd)** = list size / deals that list actually produced. Lower = better. Spread from sharpest signal to the default list is >50:1.
- Headline: high equity 317 dpd (Priority 3) vs final judgment 6.2 dpd (Priority 1), same market.
- Every list's dpd is compared to the **county baseline** = dpd of marketing to the whole county. Typical county baseline 100–300 dpd.
- **Tiers:**
  - **Priority 1** = 3x baseline lift or better; a signal at **10x or better ranks P1 at any list size**. P1 also demands volume: **2% of county deal flow, or 25 deals**.
  - **Priority 2** = 1.5x–3x baseline.
  - **Priority 3** = under 1.5x, OR regime-demoted (wrong-state court list), OR too thin to trust (thin samples, sliver-volume rows, churn artifacts where dpd < 1). P3 = coverage, not efficiency.
- Lift alone does not promote a row: Zombie has P1 lift (4.8x) but sits at P2 because only 16 counties measured.
- Basis of all numbers: medians across counties where the signal held a measurement, out of 3,144 counties of recorded investor purchases; single-family, off-market, sold-to-investor deals over a **6-month window**. Run your own county in the Doors Per Deal Framework to replace medians with local ranking.
- "Read the counties column before you trust a row": e.g. Notice of foreclosure (2nd-highest lift, 28 counties) = sharp but verify locally; Absentee 1.7x off 1,966 counties = settled fact. Both belong in plan, in that order.
- Start at P1 on day one, then add layers ("you do not earn your way to the good data"). Example: market ground unscored at 200+ dpd; same market at AI score 90+ ≈ 22 dpd, $125,000 median gross, 56% margins.
- Ranges by tier (lists): P1 6–56 dpd; P2 39–112; P3 158–317. AI score bands (Knox + Blount TN reference): P1 score 90–100 = 11–23 dpd; P2 80–90 = 39–44; P3 50+ = 64–78.
- Full ladder = 50 pull signals: 22 native lists, 25 stacks, 3 AI score bands. **Save P1 rows as their own presets in SiftMap and work them top to bottom.**

### Priority 1 lists (36 signals: 11 native + 25 stacks; best 6.2 dpd; best lift 22.7x; court-driven distress lists 6–20 dpd)
- Notice of foreclosure 6.8 dpd, 20.9x (non-judicial states)
- Final judgment 6.2 dpd, 20.6x (judicial states)
- Notice of default 8.9 dpd, 20.2x (non-judicial)
- Probate 16.4 dpd, 9.1x (measured 38 counties)
- Lis pendens 20.1 dpd, 8.7x (judicial)
- Judgment lien 14.3 / 8.2x; Estate sale 32.9 / 5.2x
- Vacant 44.7 / 3.7x (pulls on every plan)
- Other lien 44.7 / 3.4x; Pre-probate 49.8 / 3.1x; Out-of-state 56.2 / 3.0x
- Sharpest stack: **Absentee + Notice of foreclosure 6.4 dpd, 22.7x**
- State regime decides the real court signal: non-judicial → notice of default + notice of foreclosure; judicial → lis pendens + final judgment; the other pair drops to P3 in that state.
- Access: every P1 row except Vacant needs **SiftMap Pro** data — included on **Expert $499/mo**, or **$297/mo add-on** on **Professional $149/mo**.

### AI Investor Score bands (AI plan $1,250/mo; one county of off-market scoring included)
- **90–100 = P1**: Knox 22.9 dpd / 9.0x; Blount 10.8 dpd / 24.8x. Reaches ~27% of county investor sales (median 27.0% across 37 measured counties). Sharp pull, not coverage. Scores properties before they become obvious marketed lists. Measured only in Knox/Blount (full off-market scored supply) → treat as range, not national median.
- **80–90 = P2**: Knox 38.7 / 5.3x; Blount 43.5 / 6.2x. Cumulative reach at 80+ = 39.5%. Widening buys ~12 points coverage, costs ~half the sharpness. Score is cumulative → pull 80–90 as its **own preset** once 90+ is dialed out. Use when first pull is worked and you need next week's volume. Widen the band; don't stack distress on it.
- **50+ = P3 (coverage)**: Knox 63.8 / 3.2x, reach 61.1%; Blount 78.1 / 3.4x, reach 47.5%; median reach at 50+ across 37 counties = 67.7%. Clears P1 on lift but sits at P3 on purpose — judge on reach (67.7%), not lift. D4D layered on the union adds ~8–13 coverage points. Blended across union ≈ 100–180 dpd. Drop to 50 only when P1 is producing and callers have capacity.
- **Stacking rule (never break):** run AI score and distress lists as **parallel pulls**; **never AND-stack a distress list on top of the score** (score already prices in distress → shrinks list without sharpening: ~48 dpd instead of 31). Stack distress signals on each other; score band = its own separate preset.

### Priority 2 lists (8 native; 39–112 dpd; 1.6–4.8x; 3–20x bigger than P1 lists → go second)
- Zombie 38.7 / 4.8x (only 16 counties)
- HOA lien 59.3 / 2.6x
- Bankruptcy 61.8 / 2.5x
- Tax delinquent 69.5 / 2.2x (427 counties)
- Bad credit 78.4 / 1.8x
- Low income 94.5 / 1.6x
- Tired landlord 103.8 / 1.7x (1,097 counties)
- Absentee 112.3 / 1.7x (1,966 counties; every plan)

### Priority 3 lists (3; 158–317 dpd; 0.6–1.3x) — the three most people pull first
- Free and clear 157.5 / 1.3x (2,155 counties)
- Senior 263.3 / 0.7x (1,520 counties)
- High equity 317.1 / 0.6x (1,387 counties)
- Senior and high equity are BELOW baseline → marketing to the whole county finds deals faster.
- Also P3: judicial lists in non-judicial states and vice versa; thin/sliver/churn rows.
- They are **filters, not lists**: stack on a distress trigger to make P1 rows. High equity + notice of default 9.4 dpd / 19.8x; Free and clear + vacant 39.5 / 4.3x; Free and clear + probate 10.8 / 12.0x; Out-of-state + vacant 25.2; Tax delinquent + vacant 54.9 / 4.3x (→ P1).

### Two dials — never confuse
- **Saturation** sets the touch (FTM lists → cheap multi-touch; saturated → higher-caliber caller + premium touch).
- **Sales-cycle speed** sets urgency (foreclosure 45-day auction clock; probate/courthouse = long nurture).

### From ranking to courthouse
- Ranking = scouting. Once a category proves out in SiftMap, pull the same record at the county → work it **30–90 days before** it appears in paid data.
- If SiftMap coverage is low/zero for a category in your county, the county pull is the only way in (a list nobody can buy = moat). Doors Per Deal Framework flags gaps per county and carries real offices, portals, phones, record-request paths grouped in pull order.
- **Priority A (pull first, 4 sources):**
  - Foreclosure notices — NOF 6.8, final judgment 6.2, NOD 8.9, lis pendens 20.1 (4 of 5 sharpest rows). Window: days 1–30 from notice filing, before auction. Cadence: daily to weekly (fast in non-judicial). Office: county recorder (non-judicial) / court docket (judicial). Pull only the pair your regime supports.
  - Probate filings — probate 16.4, estate sale 32.9, pre-probate 49.8; F&C+probate 10.8 / 12.0x. Window 30–180 days from filing. Cadence daily to weekly from court. Office: probate court or clerk of court. **Contact the personal representative, never the deceased**; deep prospecting on the heir pays for itself here.
  - Tax delinquent roll — 69.5 (P2); + vacant 54.9 / 4.3x (P1). Window ongoing (bills owed, no auction date). Cadence monthly rolling. Office: county treasurer/tax collector. Pull monthly, cross against vacancy flags before dialing.
  - Tax sale list — no SiftMap row; proxy = tax delinquent roll. Window 60–90 days before auction once list publishes. Cadence annual, published as legal notices 3–4 weeks pre-sale. Office: treasurer + legal notices in paper of record. Source pull is the only way at the sale stage.
- **Priority B (pull next, 2 sources):**
  - Eviction filings — proxy tired landlord 103.8 (1,097 counties). Window immediate (landlord as plaintiff motivated now). Cadence weekly from court docket. Office: district or magistrate court. **2+ filings in 12 months = your own tired-landlord definition.**
  - Code violations — proxies vacant 44.7 / 3.7x and other lien 44.7 / 3.4x (both P1, downstream of a violation). Window before compliance deadline, typically 10–30 days. Cadence weekly to monthly. Office usually **city-level**, often a records request → budget for slower turnaround.
- **Priority C (pull last, 1 source):**
  - Divorce filings — no SiftMap row, no provider sells it → lowest competition of the seven. Window 60–180 days from filing (property on marital asset schedule). Cadence weekly. Office: circuit or family court docket. Last because slow to build, not weak.

### Targeting Matrix (freshness × competition → how to touch)
- **First Mover** (fresh, low saturation; highest ROI; start here): AI Investor Score + native obituary flag. Action: pull AI score 90+ and obituary flag, skip trace, **dial same day**; add same-day courthouse pulls once producing.
- **Hidden Gem** (fresh, heavily marketed; e.g. foreclosure after lis pendens): best closer, premium touch (door knock, handwritten mail), move fast — bottom-of-funnel on 45-day clock.
- **Crowded Race** (old data, low saturation; low ROI): only worth it if you narrow high-volume lists (senior, vacant, tired landlord) by dpd. Action: narrow with AI score before dialing, or add fresh trigger (new code violation on old tax-delinquent property); else spend time on fresher data.
- **Dead Money** (recycled broker lists, stale, 20–50 other investors calling): stop buying pre-built lists; pull SiftMap precision score and start in First Mover. Most investors are here and think they're in First Mover.

### Case: Kasim Delake (Delaware W-2 → $100K+ wholesaling)
- Ad account died in COVID; courthouse method. Market Delaware + MD, VA, PA, NJ satellites. Data: county foreclosure + probate filings, cost zero. Phase 2 Delegator path.
- Weekly fresh courthouse pull (online or in person). **Day 1–3: call, text, mail every new record → can't reach: second skip trace with new numbers → still nothing: relative phone numbers → last resort: direct mail only for ongoing touches.**
- Now also Facebook ads + PPC; courthouse engine via DataSift remains backbone. Would now add stacks (Absentee+NOF 6.4; F&C+probate 10.8). "The pyramid is a work order, not a ladder."

### How far down by phase
- **Phase 1 Operator:** P1 only, lists view. One county, one list, sequential marketing live. Top 3–4 P1 rows your state supports, skip trace, dial same day (non-judicial NOD+NOF 7–9 dpd; judicial LP+FJ; probate 16.4 everywhere). Budget $499/mo (Expert: SiftMap Pro + unlimited skip trace) or ~$297/mo SiftMap Pro add-on on $149 Professional. First hire: Data Manager $500–700/mo after first deal. No AI plan needed to start.
- **Phase 2 Delegator:** P1 in full (stacks included) before widening, then P2. **4 full attempts on every record**, mail to non-responders, data/admin delegated. Budget $500–2,000/mo. Team: Data Manager + 1–2 callers. AI band only as parallel pull.
- **Phase 3 Manager:** all three priorities, both views. Scale on overflow: handoff system first, then admin, prospectors, lead manager, closer. First phase with capacity for P3: AI 50+ (67.7% reach); high equity / F&C as filters on a distress trigger, not dialed straight. Budget $3,000–10,000+/mo. Team: Data Manager, Callers, Lead Manager, Closer, Sales Manager. Rule: never skip a rung; leading with P3 collapses dpd and margins.
- **Phase 4 Owner:** P1 only, worked to the bone. Deep-prospect every unreached door; obituary layer = notice-of-death flag on owner, added weekly nationwide, highly uncontested. Estate sale 32.9, pre-probate 49.8 reward depth. Budget $200–1,000/mo on top of running stack. Team: niche-trained Data Manager. Never go wide.

### Base vs apex
- Start at P3 (high equity, senior, F&C): 158–317 dpd, 0.6–1.3x, 20–50+ investors calling, data 30–90 days old, AI score stacked on distress.
- Start at P1 (court signals your state supports): 6–20 dpd, 8–21x, 1–3 investors calling, 0–7 days from filing, AI score as own parallel pull.

---

## 2. Market Finder Workflow

- Principle: let data pick the market/neighborhoods, don't pick a market then force it. 4-phase progressive drill-down, same 4 metrics at every level (state → county → ZIP → neighborhood). Quote: better to be "at 4K across seven different counties" — fewer, more targeted people in more counties.
- Market Finder .skill for Claude (Co-Work/Projects), from Google Drive; needs Google Chrome connector (auto-logs into Sift; prompts for creds if no Chrome).

### The 4 key metrics
- **Median Sales Price** — what closes. Compare to median home value; sales price below home value = opportunity (motivated sellers). Want consistent closings in your 60% range.
- **Days on Market** — absorption speed; lower = hotter (30 DOM zip moves faster than 90). Red flag: DOM above county average. Darkest heat-map zip can still be slow: Knox 37902 (downtown) 112 DOM. Volume alone doesn't validate a zip.
- **Median Home Value** — price anchor; compare to national average **$375K**. Used for the 60% range.
- **Investor Transaction Volume** — proof of viability/active buyers. Low volume ≠ no opportunity but validate exits carefully. Pick solid volume, not the absolute highest (very high = heavy competition). Sweet spot: balanced count + low DOM.
- **60% Price Range Rule:** stay in middle 60% of home values (Minnesota: $200K–$600K). Below → deferred maintenance; above → institutional buyers.

### Navigating Market Finder
- Select state, then one or multiple counties (side-by-side comparison).
- **Heat map**: darker = more investor activity. Five heat-map metrics: Investor Transactions, Homes Sold, Median Sales Price, Homes on Market, Days on Market.
- Breakdown below map per area: total investor transactions (last 6 months), homes on market, homes sold last month, median DOM, median home value, median sale price.
- **Quick Overview panel** (right sidebar): median home value, median investor transactions, total homes on market, homes sold last month; plus home price distribution.
- **Calculate** button: property characteristics from past investor deal patterns (price ranges, property types, position vs statewide/nationwide). Then **"View On SiftMap"** applies those filters and adds matching properties to your account (bridge research → list).
- **Homeownership Rates**: renter/owner %, median monthly rent, gross rental yield (high renter % + strong yield = rental opportunity).
- **Property Breakdown**: % by property type, bedroom count, year built (e.g. 70% SFR 3-bed 1970–1999 = bread and butter).
- FAQ:
  - Investor transaction = LLC or company bought from an individual; LLC/Trust selling to another company/trust excluded.
  - Recency: Homes Sold = last completed month; Investor Transactions = last six completed months; DOM and Median Home Value refresh with public data updates.
  - No single best metric: use DOM + Months of Inventory + Investor Transactions + Price fit together.
  - Low median monthly investor transactions often reflect rural areas in counties with big cities; deprioritize those areas, not a bad market.

### 4-phase drill-down
1. **State:** median home value vs $375K national (prefer at/under). DOM vs national average (lower better). Demand: homes on market vs homes sold → want **< 3 months of supply**. Identify 60% price range (operating range for rest).
2. **County:** compare counties on the 4 metrics. Ideal: median sales price below median home value, DOM below county average, healthy demand ratio. **Rank top 3.**
3. **ZIP:** switch to zip code view; heat map. Don't just pick darkest; verify all 4 metrics (high volume + high DOM = trap). **Select top 3–5 zips** passing all four and inside 60% range.
4. **Neighborhood:** each ZIP broken into neighborhoods with same 4 metrics; bad zips can hold great neighborhoods and vice versa. **Select top 3–8 neighborhoods.** Enables hyper-targeting: direct mail, door knocking, Facebook retargeting, D4D.

### Knox County TN example (skill output: 5 min vs 3–4 hours manual)
- Spreadsheet 7 tabs: Executive Summary, ZIP Code Analysis, Neighborhood Analysis, Economic Indicators, Crime & Safety, Investment Recommendations, Data Sources.
- Population 487,401 (+1.25% YoY); median home value $352,500; monthly investor transactions 91 (546 in 6 months / 20 active ZIPs); DOM 57–67 (15–27% faster than 78-day national); months of supply 3.9 (3–6 healthy/balanced); homes sold/month 709, 2,800 on market; gross rental yield 7.28% ($2,098/mo rent); homeownership 68.1% (31.9% renters).
- **12 core indicators** (Knox passes all 12): Affordability & Rates FAVORABLE ($352K vs $71.6K income = 4.9x, under 5x; sub-$250K inventory in top ZIPs); Market Saturation MODERATE (91/mo); Supply & Demand BALANCED (3.9; active ZIPs 2.8–5.2); Home Prices STABLE/COOLING (−2.6% YoY county, city +2.5%); Distressed Properties STRONG SIGNAL (37914 −22.3% spread); Demographics GROWING (+1.25%, median age 37.3); Infrastructure STRONG; Crime Trend IMPROVING (murders −26% in 2025, 17, lowest since 2016; non-fatal shootings −27%); Mortgage Health STABLE (3.0% unemployment); Government & Taxes INVESTOR-FRIENDLY (no TN income tax, property tax ~$2.12/$100, landlord-friendly); Builder Sentiment POSITIVE (investor volume >90/mo); Economic Conditions STRONG.
- ZIP scoring: 48 ZIPs, 20 active; sorted by 6-mo investor volume. Star score weights: activity (primary), velocity vs 78-day FRED baseline (secondary), price accessibility (tertiary).
- Top ZIPs (6-mo trans / value / DOM / spread / supply / stars): 37920 86/$289,130/59/−9.3%/3.8/5; 37917 57/$249,231/57/−1.4%/2.8/5; 37918 55/$321,218/64/−3.3%/4.4/4; 37914 52/$245,896/67/−22.3%/4.7/4; 37921 44/$268,439/57/−7.5%/4.0/5; 37919 43/$490,956/64/+15.0%/3.0/4; 37912 34/$282,449/57/−11.4%/3.8/5; 37931 30/$415,698/62/−3.6%/3.4/4; 37922 23/$606,975/60/+0.5%/5.0/3; 37924 21/$311,761/70/+4.7%/5.2/4.
- Top 6 ZIPs = 328 of 546 transactions (60%) → market there first.
- **Negative spread % = median sale price below median home value = your wholesale margin.**
- Neighborhoods: 121 analyzed. Top: Colonial Village 23 trans/$251,702/53 DOM/−19.5%; Burlington 20/$203,207/77/−9.5%; Hallbrook 17/$300,489/53/−17.5%; Berkshire Wood 15/$421,790/53/−6.0%; Chilhowee Hills 14/$213,948/79/−25.1%; Vestal 14/$220,019/66/−22.9%; Windsor Park 13/$250,390/65/−19.9%; Belle Morris 12/$222,658/74/−6.7%; John Sevier 12/$304,820/78/−3.7%; Lonsdale 8/$202,184/35/−36.2% (fastest DOM in county + steepest discount = cash buyer magnet).
- Safety overlay: West Knox (Farragut 37934, 37932) HIGH → buy-and-hold; North Knox (Fountain City 37918) MOD-HIGH → flips; South Knox 37920 MODERATE → highest activity; East Knox 37914/37917 MOD-LOW → value-add, lowest prices; Powell/Halls 37849/37938 HIGH → emerging.
- Employment: MSA labor force 454,931, 3.0% unemployment (vs 4.0% national). Sectors: Health Care 34,874; Retail 30,903; Education 24,763; Manufacturing ~22,000; Professional ~20,000; Accommodation/Food ~19,000; Government ~18,000; Construction ~15,000.
- Demographics: median HH income $71,662 (+4.49%; national $75,149); median age 37.3 (nat 38.9); poverty 12.2%; bachelor's+ ~39%; commute 22.2 min (nat 26.4).
- Major employers: University of Tennessee (~10K+), UT Medical Center, ORNL, Y-12, Pilot Flying J HQ, Covenant Health.
- Redfin Jan–Feb 2026: median sale price county $391,000 (−2.6%), city $314,000 (+2.5%); homes sold Jan 2026 237 (+4.4%); price drops 26.9% (+2.7pp); sale-to-list 98.3%.
- Tier 1 ZIPs: 37920, 37917, 37914, 37921, 37912, 37918.
- Exit strategy by segment: Wholesale $150K–$350K (37920, 37917, 37914, 37921, 37912; Colonial Village, Burlington, Vestal, Lonsdale); Wholetail $250K–$450K (37918, 37924, 37923, 37849; Hallbrook, Cherokee Ridge, Inskip); Full Flip $350K–$650K (37919, 37931, 37922; Berkshire Wood, Sequoyah Hills); Buy-and-Hold $200K–$400K (37920, 37918, 37917, 37912; Burlington, Belle Morris, Windsor Park).
- Action plan: focus Tier 1 ZIPs; target neighborhoods Colonial Village, Burlington, Vestal, Lonsdale, Chilhowee Hills, Hallbrook; 65+ DOM properties = above local average → prioritize follow-up; wholesale price target $150K–$350K (80%+ of investor transactions); monitor 37923 (49 DOM) and Lonsdale (35 DOM).
- **ZIP count rule: 1–2 deals/mo = 5–10 ZIPs; 3–5 deals/mo = 10–15 ZIPs.** Knox supports 3–5 deals/mo.
- Do: factor insurance in East Knox (37914, 37917); verify FEMA flood zone in South Knox (37920); plan marketing pushes for Mar–Sep selling season (DOM +10 days in winter).
- Don't: chase wholesale margins above $400K (West Knox 37934, 37932, 37922 razor-thin); trust 37902 or Old City data ($1M+ commercial/condo outliers); ignore the −2.6% correction (rate cuts could reverse window).
- Sources the skill blends: Market Finder, FRED, BLS, Census, Redfin, local crime databases.

### Skill setup (5 steps)
1. Download .skill from Google Drive. 2. Open claude.ai (Claude Pro $20/mo suffices). 3. Upload in Co-Work or Projects. 4. Enable Google Chrome connector (auto-login; else prompts creds). 5. Give it state + county → structured report (Google Sheet, 7 tabs).
- Automated: 10–15 min/market, 1 step, needs Claude Pro + Chrome connector. Manual A–Z: 45–90 min/market, 4 phases, DataSift account only. Same sources/metrics.
- If Co-Work unavailable (no Chrome/Pro, corporate restrictions) use manual process.
- "Analysis without action is an expensive hobby" — the job is pulling a list and having seller calls.

---

## 3. Sold Properties Analysis (requires SiftMap Pro)

- Access: SiftMap Pro, included in Expert ($499/mo) and AI ($1,250/mo); standalone add-on $297/mo.
- Shows what actually sold in last 6 months, to whom, at what price. Filters are hypotheses; sold data is proof. **Distressors present at time of sale pick your lists — not the reverse.**

### Validation loop (run quarterly)
1. **Assume** — SiftMap base criteria from Market Finder: price from 60% window, **equity 30%+**, year built per local housing stock age, **ownership 5+ years**.
2. **Verify** — open Sold Properties for county, date range last 6 months, **Investor Transactions** tab, **AI score filter 50–100**.
3. **Correlate** — which distressors, price points, ZIPs; click individual transactions to find buyers, prices paid, repeat purchasers.
4. **Refine** — back to SiftMap: tighten non-matching criteria, expand where missing real transactions, build refined presets; repeat next quarter.

### Three tabs
- **All Transactions** — every completed sale; baseline/denominator for prediction-rate calc.
- **Investor Transactions** — primary tab: LLC/company bought from an individual; excludes LLC-to-LLC transfers, manufactured homes, double closings. Knox 90+/month = supports volume.
- **In My Records** — CRM properties that sold (accountability check). Example: 5,000 records, 200 sales last quarter; high match = targeting works, low = adjust filters.

### Top Patterns (5 dimensions)
- **Price Range** — does Market Finder 60% window match? If window $200K–$600K but 70% of investor deals < $350K, cut upper range.
- **Property Type** — don't default to SFR only; e.g. 15% duplexes = missed deals.
- **Year Built** — e.g. set "2000 or earlier" but cluster at 1960–1980 → that's renovation-ready inventory; tighten.
- **Bed/Bath** — 3/2 dominant = flip profile; 2/1 at disproportionately low price = overlooked wholesale opportunity.
- **Distressors** (most important) — flags at time of sale (example: F&C 40%, senior 25%, tax delinquent 8%). Prioritize lists correlating with actual sales, not ones that "sound motivated".

### Lists that convert (per page)
- High volume: **Free & Clear** (often largest distressor in sold data); **Senior Owner** (second highest sell reason; multi-channel niche sequential); **Absentee Owner** (absentee + senior + multi-property triple-stack = highest-quality lists).
- Niche: **Tax Delinquent** (7.5% of FL private sellers tax delinquent; reliable niche sequential list); **Multi-Property (3–6)** (Ty's 2021 niche: 14 contracts, ~3 houses each, $1.3M revenue); **15+ Years Owned** (tired-landlord equivalent, small, high conversion).
- Assumptions vs reality examples: 42% F&C vs 8% tax delinquent → F&C primary; 70% below $350K → tightened to $150K–$350K; top-volume zip 112 DOM vs zip #4 47 DOM with more investor transactions → swap focus.
- (Note: this page's High-Volume framing of F&C/senior differs from the Pyramid's P3 ranking of those as standalone lists; the Pyramid treats them as stacking filters.)

### AI score validation
- Sold Properties shows AI score at time of sale → measure prediction rate; **> 60% prediction rate validates the model**. (The page's stat counters for prediction rate / data points per score / recommended filter did not render in the text capture; the comparison table uses **AI score 50–100** as the filter.)
- Without AI filter: full volume, diluted; best for **bulk sequential (20K+ records)**.
- With AI 50–100: ~**50% list reduction**, higher concentration; best for **niche sequential (< 1,000 records)**.
- Score types: **Investor Off-Market** (sells to investor off-market; primary for acquisitions; included in AI plan $1,250/mo); **Realtor Score** (likely to list on MLS; agents/novation; $297/mo add-on); **Investor On-Market** (MLS listings nationwide scored for investor purchase; novators/on-market buyers; $297/mo add-on).

### Where investors actually buy
- Investor Transactions tab → sort by zip → compare to Market Finder rankings. #1 volume zip often ≠ #1 investor zip (e.g. 50 sales/30 investor vs 80 sales/5 investor). **Highest ratio investor transactions / total sales = best niche sequential targets.** Ratio > raw count.
- Do: cross-reference MF zip rankings with investor transaction counts. Don't: chase highest-volume zip without DOM check (37902: 112 DOM).

### Advanced filters
- **Ownership Duration** buckets 0–3, 3–5, 5–10, 10–20, 20+ yrs. 40% from 10+ yr owners confirms "5+ years"; mostly 0–3 = wrong profile.
- **Equity Percentage** custom 0–100%. Knox foreclosures median equity 77% → 30% floor holds; add separate high-equity preset at **60%+**.
- **Distressor Stacking** — multiple categories to see overlap (e.g. senior AND F&C). Triple-stack absentee + senior + multi-property = peak conversion.
- **Year Built Range** — validate SiftMap year-built setting.
- **Saved Presets** — save validated combos (e.g. "Knox County Investor 6mo", "High Equity Senior", "Sub-$300K Distressed"); folders by market/analysis type; revisit quarterly.

### 20-minute validation routine (before ANY new campaign)
1. Open Sold Properties, select county.
2. Time range last 6 months (captures seasonality, avoids stale data).
3. Investor Transactions tab (ignore total sales).
4. Review Top Patterns (price, type, year-built clusters, distressors); note contradictions with SiftMap filters.
5. Click individual transactions: buyers, prices paid, **repeat purchasers (2+ purchases = active buyer)**, neighborhoods of concentration.
6. Document findings (validation record vs next quarter).
7. Adjust SiftMap filters: tighten price, add/remove distressor layers, build refined preset.

### Close the loop (MF said vs SP confirmed)
- MF: median sale price $314,000 (Knoxville city) → set 60% window. SP: check whether investor purchases cluster below city median (e.g. 70% under $300K → lower true ceiling).
- MF top zips 37914, 37918, 37920 → SP confirm real investor activity; may surface a surprise high-ratio zip.
- MF 90+ investor transactions/month → confirms volume.
- **Market Finder does not show distressors; Sold Properties fills that gap** → distressor distribution = SiftMap stacking strategy.
- Run quarterly; document each cycle. "First cycle eye-opening; third cycle is where you stop wasting money."

---

## 4. The Pendulum Theory

- Every channel saturates: early adopters win → masses pile in → costs spike, response tanks → channel dies → next "hot" channel. Defend against what works now; stop chasing the swing.
- Timeline:
  - **2018 Texting**: 2,000–3,000 texts → a deal. Then mass-blast tools, compliance tightened, carriers filtered investor texts.
  - **2021 Direct Mail**: 3,000 mailers → a deal, almost any list (phones ringing off hook, mailbox empty). Then 10+ "We Buy Houses" postcards/month, costs up, returns down.
  - **2022 PPC**: "sell my house fast" Google Ads low competition, cheap high-quality leads. Then CPL tripled in competitive markets; deep pockets only.
  - **2023 PPL (pay-per-lead)**: outsourced leads. Then quality dropped, same lead sold to 5+ investors.
  - **2024 Cold Calling back**: inbound saturated → phones quiet → sellers answering again.
- Going all-in burns you: e.g. direct mail great 3 months, dead by month 6, $5–10K spent. Channel didn't break, it got crowded.
- **Do:** run 3+ channels simultaneously; lead with the cold call then layer cheaper follow-ups; track cost per deal by channel; expect every channel to saturate.
- **Don't:** go all-in on the hot channel; start with the most expensive channel; assume best channel lasts; chase next trend when one dies.
- **Cost per touch:** Cold calling $0.03–0.06; SMS $0.01; Direct mail $0.50–0.75; Deep prospecting $1.50–4.00; Door knocking $25–50 (text→knock gap 2,500x).
- Sequential marketing rule: **cold call always leads**, cheaper follow-ups layered on top. You stack, never replace a cheap channel with an expensive one.
- Single vs multi-channel (page's comparison, single channel): deal consistency 2/10, recovery speed months, monthly variance 60%, dependency risk critical.
- Sequence tiers: (1) Cheapest first — bulk SMS + cold calling (foundation); (2) Mid-tier — direct mail + deep prospecting for leads not converted on outbound; (3) Premium — door knocking + deep research for hardest-to-reach. One list, escalating touches; cheap touches keep running underneath.
- **PPC/SEO/Facebook = safety net, not primary** ("be seen" so missed outbound leads can find you). Inbound is backup.
- Start with **two channels: text + call**; add a third only after first two produce consistently. Launching everything at once = doing nothing well.
- Case Nate Beekman: scaled PPC to $40K/mo → market saturated → CPL tripled → burnout → rebuilt lean on outbound + foundational data. Inbound needs continuous high spend; costs accelerate under competition.
- Day 2 reference: full 27-touch attempt SOP and hiring order (Team Structure & Sequential Marketing guide).

---

## 5. Saturation and the Sales Cycle

- Every list has **two independent axes**:
  - **Saturation** (low = fresh/uncompeted → high = marketed for months): fresher/FTM → higher connect rate, more open owner, cheaper working touch. Saturated ≠ dead; needs stronger caller + premium touches.
  - **Sales-cycle speed** (bottom = forced/close now → middle = decision-based → top = long nurture).
  - Saturation sets how you reach the owner; cycle sets how long you work them. Read both before spending.
- Anchors: low saturation → $0.03 cold call connects; high saturation → $25–50 door knock to stand out on foreclosure; forced close = 45 days (auction countdown); long nurture = 4–7 months (probate cycle).

### List placements and touch plans
- **Foreclosure** (high saturation, forced): lis pendens fired months ago, every investor marketed. Touch: premium — door knock $25–50 + handwritten mail; cheap touches still run but less effective. Caller: your strongest (45 days, ~ten operators calling; caliber decides).
- **Obituary and deceased** (lowest saturation, heir decides): owner can't answer. Touch: **call, voicemail, then mail; never a cold text on a deceased list**; deep prospect heir/executor at $1.50–4.00/record. Caller: empathy over pressure. **14.8 dpd** because few compete.
- **First-to-Market courthouse** (low saturation only if same-day; long nurture): FTM only if pulled and marketed same day. Touch: cheap — cold call first ($0.03–0.06), $0.01 text follows **on non-sensitive categories**, mail behind. Caller: standard caller produces. Expect **3–6 month cycle and $500–2,000 per contract** once built.
- **Senior, vacant, tired landlord** (low saturation, decision middle): not pullable from PropStream or DealMachine → owners still answer. Touch: cheap touches connect but lists huge → **narrow by doors per deal first**, market only top cut. Caller: standard caliber/cadence, **4 full attempts per record**; consistency beats intensity.
- **Recent probate** (low saturation, decision middle): estate can sell, not forced; **4–7 month cycle**. Touch: call, voicemail, mail on patient cadence; **sensitive — no cold text, ever**. Caller: like obituary, measured and question-led; follow-up cadence closes it.
- **Everyone's pulls** (high saturation, no urgency): standard exports, owner heard from ten investors this month. Touch: cheap less effective, budget premium; better re-cut by dpd or skip. Caller: stronger than the list deserves; average callers burn out here.

### Funnel bands
- **Bottom, forced — Foreclosure**: sale happens 45 days out regardless; most saturated (marketing began when lis pendens fired). Door knock, hand-mail, best caller.
- **Middle, decision — senior, vacant, tired landlord, recent probate** (+ obituary, least competed, hardest to reach): big platforms don't carry them; huge volume → narrow by dpd, cheap touches.
- **Top, long nurture — courthouse pipeline** (FTM county pulls, broad probate): consistency engine, **$500–2,000/contract on 3–6 month cycle once the 25–35 day build runs**; cheap touches carry it.

### Touch vs saturation
- Same cost ladder: call $0.03–0.06, SMS $0.01, mail $0.50–0.75, DP $1.50–4.00, knock $25–50. Low-saturation lists connect near penny end; high-saturation earn the premium end (deal closes in weeks).
- Deep prospecting = **reach tool, not saturation tool** (for owners you can't find: deceased/heirs) — why obituary stays uncrowded. (See Channel Cost Per Touch guide.)
- Cheap touches don't fail on saturated lists; they get less effective → keep them, layer premium on top + stronger caller. On fresh lists they're the whole plan.

### Caller assignment
- Caller ≈ **$750–1,000/month, 150 dials/day**. Fresh same-day courthouse pull forgives an average opener; foreclosure doesn't. New $750 caller can produce on same-day courthouse in week one; on foreclosure you'd wrongly conclude the caller is broken.
- **Do:** pull FTM and market same day; strongest caller on foreclosure/saturated; door knock the 45-day auction window; narrow senior/vacant/tired landlord by dpd first; lead every sequence with the cold call.
- **Don't:** give saturated list to newest caller; door knock a fresh list a $0.03 call would connect; **cold text a deceased, obituary, or probate record**; call a crowded list dead instead of upgrading the touch; grade a list on one axis.

### Four-factor list read (< 1 minute, before any list reaches a dialer)
1. **Doors per deal** — judgments 6.2, deceased 14.8, broad unscored data 118+. Rank first.
2. **Saturation** — fresh → cheap touches; heavily marketed → premium + stronger caller.
3. **Reach** — can you find the owner; deceased/hard-to-trace need DP $1.50–4.00; difficulty is the moat.
4. **Cycle** — forced (foreclosure 45 days); decision lists move when owner does; nurture pays in months (probate 4–7).
- At scale this is a Phase 2 (Delegator) task: becomes the Data Manager/callers' SOP.
