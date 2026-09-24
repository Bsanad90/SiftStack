# DataSift REI Mastery course — operating reference

Read on 2026-09-24 from `learn.datasift.ai/course/modules.html`: 15 modules, 117 lessons
(88 recorded with a written walkthrough, 29 "coming soon" with an outline only), plus the ~40
guide pages the lessons link to. This file is the condensed course; the guide pages are
digested in [guides/](guides/). Numbers are the course's own (mostly Ty's Knox County, TN
business and the August 2026 challenge), not this account's.

**How to use it here.** This is how DataSift teaches the platform to be run. Where it and
this repo's CLAUDE.md disagree, CLAUDE.md records what was measured on THIS account and wins
on facts; the course wins on intended workflow. Known divergences are listed at the end.

---

## 1. The operating system (modules 1 and 15)

- Two halves: data (DataFlik) and process (REISift), merged into DataSift in 2025.
- **Deal Flow Ladder**, four phases climbed in order in ONE market; climb on overflow, never
  ambition. Each takes one thing off the owner's plate:
  - **Operator** (nothing off): pick one of three ways to start (mail-first, call/text yourself,
    hire one caller), pull the doors-per-deal lists, run ONE channel well, Trestle + litigation
    scrub from day one, seed one first-to-market list for the long cycle. Gate to hire #1
    (data manager $500–700/mo): flow live, 4 full attempts per record, FTM seeded, leads
    overflowing.
  - **Delegator** (data/admin off): same list, more touches — stack SMS + calling + mail.
    Add the obituary list. Daily call order: follow-ups/rehash, attempt 3, 2, 1, fresh last.
    Benchmarks: 150 dials/day, 4 full attempts, 75–100 correct-number not-interesteds per
    deal within 12 months.
  - **Manager** (phones off): build the system before hiring (status = next action, dated task
    on every lead, one board, structured intake). Hiring order fixed: data manager →
    prospectors ($750–1,000) → lead manager ($1,500–3,000, ~2 callers per LM) → closer
    ($2,000 + 3–10%). Money gate: 3–6 months of the seat's cost banked. Watch per seat daily:
    dials, answer rate, correct numbers, leads, appointments.
  - **Owner** (deals off): all seven channels on the proven list; deep prospecting on
    everything unreached; sales manager ($5–6K + 2–5% override) and ops manager ($5–7K).
    Health number: fully loaded cost per deal. Target CPC $1,000–3,000. Only then take the
    next list; a second market restarts at phase 1 ("duplicate, do not dilute").
- Five stations every phase: data, marketing, conversations, conversion, team/leverage.

## 2. Data strategy (module 2)

- **Pendulum theory**: every channel works until everyone finds it (texting 2018 → mail 2021 →
  PPC 2022 → PPL 2023 → back to bulk 2024–25). Months 1–3 honeymoon, then the crash. Build on
  data + evergreen channels, never an auction channel alone.
- **Two axes per list**: sales cycle and saturation. Death-related (obituary, pre-probate,
  probate) 4–7 month cycle, ~zero saturation. Seniors/vacant/absentee faster, lower
  saturation. Foreclosure/NOD fastest, saturated. Run long + fast lists together.
- **Doors per deal** (County List Playbook, `learn.datasift.ai/county-list-playbook`):
  properties reached per investor deal over 6 months. Knox: 927 investor deals / 153,675 SFR
  = 166 baseline. The market ladder is a **pull order**; each rung counts only properties the
  rungs above did not already give you (net new = rung total − previous total, e.g.
  456 − 119 = 337). Typical gross profit per list sizes the offer (non-disclosure states lack
  it). Go deep on a small list (1,457 records, every 20 sells in 6 months) instead of wide.
  Lists turn over 5–10%/month (use 7.5%: 3,200 × 0.075 = 240 new/month).
- Obituary data: scraped daily nationwide, 1-week delay, tied to parcels (293K since
  Dec 2025). Probate timeline: month 0 death; 1–3 probate filed; 3–6 creditor windows; 6–24
  most sales; past 24 months they keep it. Market from the date of death, past month 3. An
  obituary 6+ months old with no probate = something wrong (the curative play). Only ~30% of
  deaths ever get a probate filed.
- **Three ways to source courthouse data**: online portal (easiest, ~1 day to set up), notice
  site (hardest; scrape with Claude because formats vary), in-person terminal (runner /
  TaskRabbit ~$100/week, photos → Drive → transcribed). The Playbook workbook's First to
  Market tab lists office, access, cadence and workarounds per county.
- **Pulling a rung in SiftMap**: county → price (Ty: $100K–700K) → property type SFR → More →
  Homeowner & Property Distressors = **Custom Combination, "All selected"** (AND, not "at
  least one") → equity 100% under Financial details (plus Unknown where wanted) → Apply →
  check count ≈ calculator → select all → Add Records to Account: **no list** (system tags
  lists), tag **Priority 1**, toggle **auto-add new records** → verify on Activity › Upload
  (SiftMap + SiftMap Auto-Add rows) → rename the saved filter `Knox - Priority 1 - <rung> -
  19.8 doors per deal`. Start with the first ~5 rungs.
- **Recently sold**: SiftMap › "show only records in my account" + last sold date → save
  preset → add with tag `recently sold` (no list) → auto-add on → exclude the tag on every
  marketing filter.
- **Obituary list**: SiftMap preset (county, value band, SFR/mobile/townhouse, distressor
  obituary, ~30–50% equity, not listed) → download base → auto-add + `obituary` tag.
  Records flow: `FTM Obituary Needs Numbers` (obituary list, numbers No, skiptraced No) →
  skip trace the decedent, add kin from SmartSkip/Skip Genie/TruePeopleSearch, board note
  "Timmy, son, 4455", phone tag = relationship → `Ready to Call` (numbers, 0 attempts). Click
  to dial, never a bulk dialer. **Obituary: leave the owner until an heir is reached.
  Probate: change the owner to the PR right away.**
- **Skip trace** (Kylie): unlimited add-on $97/mo (optional 90-day auto re-trace) or
  pay-as-you-go credits. Filter Params & Others skiptraced No + numbers No. Records must be
  clean (owner first/last, full mailing + property address). Up to 5 phones/match; no-match
  and duplicate owners refunded. Keep the auto tags. Status colours: blue <30 days with
  numbers, gray >30 days with numbers, yellow traced/no numbers, orange numbers with no trace
  date (from upload), red never traced/no numbers.
- **Enrich** (Manage › Enrich): property info, owner info (fill missing), **swap owners**
  (replaces owner; a different mailing address creates a new owner profile and phone numbers
  can drop off). Fails when SiftMap has no match (no "Open in SiftMap" on the record).
- **Upload**: headers on row 1; Add Data (new list / existing list) vs Update Data (existing
  records only). Required: property + owner street/city/state/ZIP (map a blank column if
  owner ZIP missing); clean = owner first + last. Update Data options: by property address,
  by mailing address (cannot add tags/lists/statuses), misc by phone (one phone column + one
  tag/status/type column). Phone uploads ask skip-trace source + date — **no date = shows as
  never skip traced**. Not-uploaded rows = address mismatch (provider reformatted); retry by
  the other address.

## 3. Market selection (module 3)

- Size county → ZIPs → neighborhoods with a **base criteria** (Lake County example: SFR only,
  AVM floor/ceiling = middle ~80% of values, year built ≤2010, 7+ years owned, not listed;
  no beds/baths filter; no equity filter because 7+ years keeps unknown-equity records).
  Save the base list. Unknown sale date + equity is a second base list.
- Market Finder shows *where* investors find value; Sold Properties shows *what* they buy.
  Market where competition is (volume). Sanity-check median price (a $1,000 median = tax
  sale/data noise); divide 6-month transactions by 6; months of inventory = on market / sold
  last month.
- Rank ZIPs on price point too ($307K median supports a $30K wholesale fee).
- **Suppress, don't shortlist**: exclude zero-investor-transaction neighborhoods/ZIPs across
  all marketing. Split effort within a neighborhood (door-knock absentees, mail seniors, call
  everyone). Market Finder skill (`Sift market research`) automates the ranking.

## 4. Sequential marketing (module 4)

- **Channel order, cheapest touch first, all on the same list**: SMS (~1¢; SmarterContact),
  calling (3–6¢; SmrtPhone / Kixie), direct mail (postcard ~50¢, soft-offer check at 80% of
  value, handwritten ~$2 — best on death lists), deep prospecting (up to $4/record), door
  knocking (most expensive, cuts through saturation — auctions), email (flat ~$200/mo,
  Instantly, own domains), Meta ads to the list (7th). Fixed infrastructure: **Trestle**
  (score numbers), **Blacklist Alliance** litigation scrub (~1.5¢/number; hit = never-market
  tag forever), Number Verifier. Ty does not scrub the full DNC (removes ~60% of numbers);
  opt-outs are removed on request. Not legal advice.
- Free and clear = 100% equity **plus Unknown**. Estimated value ≈ as-is, not ARV (200K EV ≈
  230–260K ARV in Knox; for a 300K ARV cap, set max value ~400K).
- Channel contribution on the top list: 261 correct contacts from 1,457 (17%), ~8% via text,
  ~9% via calls. 10 numbers per caller to start (20 at full scale), 25 calls+texts per
  number per day.
- **A full attempt** = call every number, voicemail, text, email, mail (door knock). 4–6 full
  attempts per record; most deals came on the 3rd–4th. Skip-traced-no-numbers records get
  their own workflow: mail first (and door knock / deep prospect).
- **The preset pipeline** (lesson 4.5, Ty's account). Folders run hottest → strong → first to
  market → bulk → deep prospecting → reactivation; channel-separated presets. Every preset
  suppresses (red): low equity, negative equity, recently sold, `mail only` tag, non-SFR,
  unwanted neighborhoods/ZIPs; statuses allowed = blank/prospecting only (plus No Contact New
  Lead, Follow Up, Auction Day Has Passed). Tags: **Priority 1** (hottest, SiftMap P1 pulls),
  **Priority 2** (strong), **FTM** (courthouse data). Rungs differ by one thing:
  - `Needs Skipped`: call attempts 0, Params: Numbers No, Skiptraced No.
  - `Skipped No Numbers`: Skiptraced Yes, Numbers No.
  - `Ready to Call`: Skiptraced Yes, Numbers Yes, attempts 0, **Owner properties owned = one
    property per owner** (else a multi-property owner is dialed 3–4×/day and reports spam).
  - `Call Attempt 1/2/3`: attempts = 1/2/3. At attempt 4 the record leaves the flow; back
    only via rehash or not-interested.
  - Mail: same filters minus the `mail only` exclusion, Direct Mail Attempts 0,1,2…,
    **Vacant Mailing No**, last direct mail date window; cap at 6; alternate the mailer.
  - Bulk: same shapes for records without P1/P2; export to dialer (all number types, dial
    tags with no status), name `bulk call campaign touch one`, then **Manage › Update
    Attempts: increment call attempts +1** (or it never leaves Ready to Call).
  - Deep prospecting: `Exhausted Call to DP`, no/bad numbers, return mail, and obituary
    (Obituary list included, one property per owner, **Last Obituary Date prior to last
    quarter, last 24 months**), then obituary ready-to-call and attempt stages.
  - Reactivation: `Not Interested P1` (status Not Interested, **Last Updated Field = Status,
    30+ days ago**; 20–30% of deals come from not-interesteds; revisit every 45–90 days) and
    `Rehash Ready - never answered` (call attempts ≥3, excluding sold/not interested; run
    every ~90 days, can go to bulk).
- **Click to dial**: SmrtPhone Chrome extension pinned; start the day on the **last attempt
  and work backwards** (attempt 3 → 2 → 1 → ready) to avoid double-calling; star presets to
  pin them; dial every Dial First number, text, voicemail, then change the attempt; Shift+→
  next record.
- **Kixie**: send your DataSift API key (Settings › Integrations › Kixie) to your Kixie CSM;
  one CRM at a time; outcomes update phone status (Business plan; property status too via
  PowerList with columns `property address/city/state/zip` → custom 1–4). 10DLC needed to
  text.
- **Dialer dispositions upload**: New Lead / Not Interested / Listed / Buyer / Sold → Update
  Data › *updating property status* (add phone status column = correct); Wrong number / DNC →
  *tagging existing properties*, phone status only.
- **SmarterContact**: Settings › Integrations › Connect; send filtered records (one contact
  per phone number; new campaign = Mobile + No Status; follow-up = Correct; by phone tag e.g.
  PR). Inbound: paste the data-in API key into SmarterContact API settings; build labels
  (New Lead, Wrong Number, Correct Number, Correct DNC, Wrong DNC, Unknown DNC, Not
  Interested, Relative, Spanish Speaking); map labels → property status, phone status,
  phone tags. The VA labels each reply and clicks **Export to CRM** (not automatic; the last
  phone-status label wins; notes push to the board every time, so repeats duplicate).
- **Phone scoring (Trestle)**: activity, not ownership. Tiers Dial First 81–100 / Second
  61–80 / Third 41–60 / Fourth 21–40 / Drop 0–20. Dial First+Second hold 92% of correct
  numbers; blind ~1 correct per 32 dials vs ~1 per 10 scored; connect 2–3% → 9.5%.
- **Number health**: Number Verifier (~$10/line, $150/mo minimum, 10 lines) shows what
  AT&T/T-Mobile/Verizon display and auto-remediates (~5 days). New numbers often arrive
  flagged — keep and clean rather than churn. Register every number with Free Call Registry,
  First Orion, Hiya, TNS. Check numbers morning and afternoon. Texts slide past spam easier
  than calls. Priming calls did not work.
- **Direct mail in DataSift** (OpenLetter behind it): folder of presets — `Needs First Mail`
  (exclude reached statuses, DM attempts 0–0, Vacant Mailing No), `Mail Monthly` (attempts
  ≥1, max 12, last DM date prior to 1 month ago, exclude last mail status Return / return-mail
  tag). Outside provider: same, but increment attempts yourself and tag `DM month MM/YYYY`
  (include last month's, exclude this month's). Returned mail → `do not mail ever`
  automatically, tag and deep prospect. "Delivered" = reached the local USPS office.

## 5. Deep prospecting (module 5)

- Skip tracing guesses the top 5 numbers from ~6,000 sources using DOB, name, mailing and
  property address. Trace up to 3 providers (DataSift, SmartSkip 15¢, DirectSkip); beyond
  that diminishing returns. Full flow 25–45¢/record; contact rate on obituary records ~5%
  by luck → 60–70% deep prospected. Worth it on FTM, obituary, no-number and return-mail
  records; not on all P1. Probate: find all heirs even when a PR is set.
- AI research run (Claude): register-of-deeds documents read like a title agent (total debt
  load, e.g. HELOC + mortgage), obituary → living heirs, SmartSkip for relatives + numbers,
  Excel heir list. Mail every heir by name at their own address (OpenLetter handwritten
  ~$1.75); mail the PR if one exists, else the family. Pin the summary to the record; read the
  board before calling. Only load Dial First/Second when an owner would exceed 30 numbers.
  Re-skip heirs individually when numbers are bad.

## 6. Sales conversations (module 6)

- Four pillars: reason, timeline, condition, price. **Walk the property when one or two are
  present**; ignore the asking price when motivation is there. Hot price = ≤80% of Zestimate.
- One open-ended opener ("any plans for the property?") for every list; frame changes:
  foreclosure = the owner, angry, move fast; obituary/probate = relatives, long cycle.
- Count a **correct number only when they say not interested** (a hang-up may not be the
  owner). Prospectors take calls, texts and mail replies; 150 dials/day floor → 20–25
  conversations.
- Call review is the biggest training lever: weekly AI grading of long calls (cold-call /
  lead-manager / closer coaches), one fix per call, daily for a new hire's first week.
- Close in person, never send a contract without being on the phone; walked → contract
  50–60%. No fake deadlines. Offer ladder: cash (21 days worst case) → novation (60–90 days) →
  listing. Ghosting = an uncovered objection.

## 7. Lead management (modules 7–9)

- Lead manager = first human touch between marketing and acquisitions; gathers intelligence,
  never lets a lead sit without a next action. Acquisitions never nurtures. Four sources
  (mail, cold calling, website, PPL) — check the source systems, not just the CRM sync.
- **Status / task / frequency / phase.** Every conversation ends with an appointment or a
  dated task. Default statuses left on: New Lead, No Contact New Lead, Cold, Warm, Hot,
  Ghosting Lead, Dead Lead, Not Interested, Under Contract, Closed.
- Cadence: New Lead 1 day (task due 18:00); No Contact New Lead daily 3–5 days; Nurture New
  Lead weekly (1–3 months marginal, ~6 months high-value) → Ghosting (monthly bulk dial +
  drip 30/30/30 days: SMS, email, call-review task); Cold every 45 days, Warm 15, Hot 2.
  Terminal statuses: Dead (truly never), Not Interested (really a cold lead → reactivation
  campaign), Lost Deal (sold), Ghosting.
- **Move cards by phase, not status** — the phase triggers the sequence that sets status and
  task. Moving a card straight to a later board skips the automation (orphan tasks).
- **STABM** on the way in and out of every record: Status, Tasks, Assignee, Board, Messages;
  mark the number you actually talk on as primary.
- Daily flow: clear queue (missed calls, VMs, texts, inboxes) → Call New Lead tasks (overdue
  then due today) → unqualified → qualified → **Leads Missing Tasks** (must be empty) →
  send-backs (needs review / send back to LM → requalify).
- Default account build: six SiftLine boards (lead management, acquisitions, transactions,
  wholesale, flips, rentals), default sequences, lists, tags, statuses, task presets (Call New
  Lead, Cold/Warm/Hot follow-up, No Contact, Nurture; Make Offer, Offer Follow-up, Send Back
  to LM; Process New Contract, Follow-up with Seller and Title, Close Out). Under Contract →
  transactions board.
- 75 intake custom fields (price, firm?, other offers, listed recently, plans after sale,
  condition tour, occupancy/lease, CapEx years — roof, HVAC, water heater, panel — mortgage
  balance, arrears, taxes, HOA, code violations, probate/heirs, title). Custom fields for
  repeating facts, message board for the rest. FTM fields: notice URL, outstanding liens,
  total delinquency, auction date (a sequence unenrolls past the auction date).
- Sequences: trigger → optional condition → actions; assign tasks via **task presets** (a
  plain task gets a fixed date). SMS/email actions need SmrtPhone/Twilio/Plivo or Gmail; SMS
  ≤150 chars; send to primary or all correct numbers. Round robin by user or role, with
  "assign task to property assignee". Drips: time zone set (sends 8AM–9PM); removal on reply
  is manual; a 3-minute speed-to-lead text on New Lead.

## 8. Deals, dispo, team, KPIs (modules 10–13)

- Comping: draw natural boundaries first (interstates, big roads), two columns — renovated
  (ARV) and distressed cash (as-is). AI chain: comps → rehab from walkthrough videos
  (tier 1 rental grade … tier 3 builder grade; mirror comp finishes) → lender package →
  dispo.
- Buyers: SiftMap near the deal → More › investor transaction type Flip, last sold 12 months
  → history tab (buyer, double closings = wholesaler, mortgage amounts are reliable in
  non-disclosure states) → reach the flipper through the listing agent → Phonebook contact
  with mailing address, tags, status **Buyer** → filter Buyer + tags → drip "new deal" from a
  dispo number. Work neighborhood → ZIP → county.
- Novation: make-ready ≤8–10% of ARV, ~8% selling costs, net must clear payoff.
  Curative: obituary 6+ months + no probate + tax delinquent 2+ years; never cold text.
- Team: KPIs not hours (250 dials+texts/week, 150–200 dials/day, 1–4 opportunities/day),
  calling window 9AM–7PM ET, 90-day trials, core values. Pay (Ty): LatAm caller $1,100 +
  2.5%; LM $1,500 + 5% taken through; closer $2,000 + 10%; ~20% of a deal to sales; no
  clawbacks. Hiring via Facebook groups + Indeed country posts (~$70/hire), Claude screening,
  Loom verification.
- KPIs: 5:1 conversations→appointment, 1.5:1 appointment→offer, 4:1 offer→contract; a caller
  touches ~30–50 records/day. Kill a list the same day response drops. Audit: four pillars
  (marketing, sales, operations, KPIs), fix the lowest first. CPC $500–1,000 solo FTM,
  $1–2K team FTM, $4–5K AI data alone, $7–15K industry.

## 9. AI leverage (module 14)

- SiftStack = Claude Code in VS Code on the public repo, credentials in `.env`; install 74
  agents / 9 divisions / 22 skills from the guide link; setup 3–5 days. **Plan mode** before
  any build, strongest model for account builds; the demo built 52 SiftMap presets + 46
  feeder record presets on a staging account in ~90 minutes.
- Learn three things by hand before automating: one SiftMap preset + list, one records
  marketing preset, the sold-property sequence.
- Model cost: route cheap jobs (transcription) to cheap models (OpenRouter); automate only
  what costs less than a person. Morning FTM pull on a server ~40–50¢/day.
- Weekly QA skill: leads whose status ≠ board column, no correct number, 2+ tasks. Hot can
  mean "fits criteria" (AI 70+, top ZIP, 50%+ equity). Reset cadence from lost deals.
- SMS agent: personalized varied texts, 9AM scheduled job spread over the day, rotating
  numbers, SmrtPhone dials out from the number that texted, stops and assigns on interest
  (Slack alert), writes dispositions back. ~25 texts+calls per number per day.

---

## The guide pages (digested in [guides/](guides/))

| File | Covers |
|---|---|
| [sequential-presets-mail-phone-scoring.md](guides/sequential-presets-mail-phone-scoring.md) | Niche + bulk preset definitions filter by filter, 20-folder layout, universal suppression stack, reactivation timers, text touches, all 8 direct-mail presets + price table, Trestle tier study, Number Verifier |
| [crm-events-sequences-tasks-drips-intake.md](guides/crm-events-sequences-tasks-drips-intake.md) | Statuses, boards, task presets, the 10 triggers / 13 conditions / action list, sequence traps, drip templates word for word, role visibility, the 75 intake fields |
| [siftmap-ftm-doors-per-deal.md](guides/siftmap-ftm-doors-per-deal.md) | SiftMap filter layers, plan limits, the 24 Pro distressor lists, AI score rules, preset tiers, FTM sources by office, Knox doors-per-deal tables |
| [pyramid-market-finder-sold-properties.md](guides/pyramid-market-finder-sold-properties.md) | Priority 1/2/3 thresholds, national dpd medians per list, courthouse Priority A/B/C, Market Finder drill-down, Sold Properties validation loop |
| [siftstack-ai-and-data-pipeline-sop.md](guides/siftstack-ai-and-data-pipeline-sop.md) | SiftStack install, agent org chart, Co-Work skills, the 7-stage Data Pipeline SOP |
| [calls-offers-deep-prospecting-buyers-comps-rehab.md](guides/calls-offers-deep-prospecting-buyers-comps-rehab.md) | Scripts verbatim, TARP, offer stack, objection levers, coach rubrics, deep-prospecting levels + DP board, buyer presets, Two-Bucket comping, rehab tiers |
| [team-kpis-audit-unit-economics-phases.md](guides/team-kpis-audit-unit-economics-phases.md) | Roles and pay by country, hiring steps, KPI ratios and scorecards, the 20-question audit, cost per deal, the four phase roadmaps |

The guide pages contradict each other in places (3 vs 4 call attempts, mail on Day 1 vs after
the attempts, 5 vs 6 boards, Not Interested → Cold vs a 90-day drip). The digests mark each
one `[CONFLICT]` / `[NOTE]` rather than picking a side. Raw text of every lesson, walkthrough
and guide page is kept locally (gitignored) in `output/datasift_course/` for grepping.

Platform facts worth knowing before building anything (from the guides):
- **Sequences do not chain** (an action from one sequence never triggers another) and
  **tags applied during the initial upload do not fire tag triggers**.
- A "Create New Task" action with *assign to property assignee* on a record with no assignee
  creates **no task**, silently. Task presets must exist before a sequence references them.
- Sequence/drip SMS works only through SmrtPhone, Twilio or Plivo (not Kixie, Smarter
  Contact, Launch Control); sends are held to 8AM–9PM account time zone.
- Prospector / Acquisitions / Dispositions / Researcher roles see only records assigned to
  them; Lead Managers see assigned records but not unassigned ones.
- Custom-field API: `field_type: "text"` creates a MULTI-line field, `"text_input"` a
  single-line one. Plan limits: Professional 8 sequences, Business+ unlimited.

---

## Where this account differs from the course (checked 2026-09-24)

- ~~No "one property per owner" block on the DPD Ready-to-Call presets.~~ **CLOSED 2026-09-24:**
  `preset_spec.py` marks `one_owner` on Ready to Call, every Call N stage and Deep 02, and
  `dpd_presets_one_owner.py` wrote the block's collapse mode (`must.ownerPropertiesOwned:
  {"show_properties": "one_per_owner"}`) to all 24 live, store-verified. A multi-property
  owner now shows as ONE row in each dial list (FTM Ready to Call 315 -> 303).
- **The sold sweep sets a $1,000 minimum sale price but no "unrelated parties" relationship
  filter.** The Data Pipeline SOP calls both mandatory, or $0 deeds and inherited/probate
  transfers get tagged sold. `manage-sold` has not run live yet; grep found no
  unrelated-party filter in `datasift_uploader.py`. Worth adding before the first live sweep,
  after confirming the SiftMap control exists.
- The DPD call ladder runs to Call 7 (6 attempts) and `03 Exhausted Call` at 4+; the course
  exits at attempt 4 into rehash/not-interested. Different by design, not a defect.
- Sold suppression: the course excludes a `recently sold` TAG on each filter; this account
  maps the `Recently Sold` tag to the `Already Sold` STATUS via the cleanup sequence (see
  CLAUDE.md). Same effect.
- Skip-trace tag: the course leaves DataSift's auto tags on; the account standard is
  `Skip Traced MM/YYYY` on every path (CLAUDE.md My Defaults).
- Obituary/probate contact rules the course states and this account should keep in view:
  never cold text a deceased/obituary record; mail the family immediately; first call 3+
  months after the filing (the obituary DP preset uses Last Obituary Date prior to last
  quarter); probate owner = PR, obituary owner unchanged until an heir is reached.
- Course numbers (Knox DPD 166, 19.8, $67K gross etc.) are Knox's. This account's ladders are
  the no-AI Work Order ladders in `data/dpd_playbook_<fips>.json`.
