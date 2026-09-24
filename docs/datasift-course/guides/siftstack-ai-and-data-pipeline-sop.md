# Guide A — DataSift training pages (digest)

Source files: siftstack-setup, agent-org-chart, claude-skills-rei, ai-daily-workflow, blueprint-hub (actually "The Deal Flow Ladder"), data-pipeline-sop. Facts only as stated on the pages.

---

## 1. The SiftStack: AI-Powered Deal Flow Automation (siftstack-setup)

**What it is**
- "Advanced AI Workflow"; ~45 min setup; requires a DataSift account; repo `https://github.com/tyvhb/SiftStack`.
- Antigravity is retired for this workflow → use VS Code. Migration prompt to Claude Code: "Update my global CLAUDE.md to remove every Antigravity mention and switch my setup to VS Code."
- Four layers: (1) VS Code (editor) → (2) Claude Code (AI developer, extension in VS Code) → (3) GSD Framework ("Get Shit Done": planning, phase execution, verification, context mgmt) → (4) SiftStack (Python app: scrape distress notices, enrich with 10+ data points — address validation, Zillow, skip trace, phone scoring, obituary detection — upload to DataSift).
- Monthly cost ~$25–160 depending on API tier. Replaces a $500–700/mo data manager (manual courthouse pulls, skip tracing, CRM entry). ~30 s/day to run, one command.

**Claude tiers (Chat → Co-Work → Code)**
- Chat (claude.ai): text in/out; cannot touch files/run commands. $20/mo Pro; free tier limited. For research, writing, brainstorming.
- Co-Work: Claude Projects + Skills in browser; persistent context per project; custom skills (Comping, Rehab Estimator), file uploads, reusable reports; cannot touch local files or run scripts. $20 Pro works; $100 Max recommended for heavy skill use. You still do last-mile (paste into CRM).
- Code: inside VS Code on your machine; read/write files, run terminal commands, spawn subagents, MCP servers, deploy automations. $20 Pro to start; $100 Max recommended for daily use. Required tier to deploy SiftStack.

**Prerequisites (4)**
- Windows 10/11 (Mac works with minor differences); active DataSift account (any plan: Professional, Business, Expert, or AI); credit card for API signups; 30–45 uninterrupted min.
- Installs: VS Code (free), Node.js (free, needed by Claude Code), Python 3.12 (free), Git (free), Claude Code ($20–100/mo), GSD (free), SiftStack (free/open source).

**Step 1 — VS Code**: code.visualstudio.com/download → run installer with all defaults → launch. Layout: file explorer (left), editor (center), Claude Code panel (right, after install).

**Step 2 — Node.js, Python, Git** ("Golden Rule": from here on never open a terminal/type commands; Claude Code does it)
- Node.js: nodejs.org → LTS version (left button), not Current; defaults.
- Python: python.org/downloads → "Download Python 3.12" (any 3.12.x). CRITICAL: tick "Add python.exe to PATH" BEFORE "Install Now". #1 mistake; if missed, uninstall and reinstall. Done at "Setup was successful".
- Git: git-scm.com/downloads → accept all defaults on every screen.

**Step 3 — Claude Code**
- Subscription: Pro $20 gets through setup but hits token limits fast; Max $100 recommended minimum.
- Ctrl+Shift+X → search "Claude Code" → official Anthropic extension (blue icon) → Install (~10 s; button becomes Uninstall/Disable) → click the "C" icon in left sidebar → Sign in (browser, Anthropic account, "You're signed in").
- First prompt: "Verify that Node.js, Python, and Git are installed correctly on my computer. Report the version of each." Approve permission requests.
- Permission modes (cycle Shift+Tab): Plan Mode (plan → approve → execute; for big changes like adding a state/restructuring a scraper); **Edit Automatically (recommended default for daily SiftStack ops**; auto-approves edits, still asks for risky shell); Bypass Permissions (only on a trusted machine, never on a shared computer).
- Wisprflow (wisprflow.ai) for dictation: ~50 wpm typing vs 200 wpm speaking.

**Power features**
- Slash commands: `/help`, `/compact` (summarize, frees tokens; use after any big op like the install), `/clear` (wipe; use when switching tasks, e.g. market research → deployment). `/gsd:help` after GSD.
- SiftStack ships 6 built-in skills: Comping, Rehab Estimator, Deal Analyzer, Buyer Prospecting, Deep Prospecting, Playbook Generator. Skills = on-demand portable cousin of always-on CLAUDE.md.
- MCP servers suggested: Gmail MCP (read county notice subscription emails), Google Drive MCP (auto-back up CSV outputs), Google Calendar MCP (schedule courthouse trips).
- `claude --dangerously-skip-permissions` = no approval prompts ("3x faster"); diff panel still shows every change.

**CLAUDE.md ("project brain")**
- First file Claude Code reads; SiftStack ships a 31KB CLAUDE.md. Edit it to add permanent context.
- Prompt: add a "My Defaults" section at the bottom: primary county, daily summaries to Slack/Discord, preferred run time, dispositions go to [DataSift list name].
- Every couple of weeks: "Review our recent conversations and update CLAUDE.md and your memory files with anything new… County quirks, disposition rules, run times, preferences."
- Don't run one endless conversation; `/clear` or new chat when a task ends (CLAUDE.md reloads fresh).

**Step 4 — GSD**
- Prompt: "Install the GSD (Get Shit Done) framework globally on my machine and configure the hooks so it runs automatically with every Claude Code session… confirm that I can use the /gsd:help command." Then run `/gsd:help` (60+ commands).
- Key commands: `/gsd:new-project`, `/gsd:progress`, `/gsd:health`; `/gsd:plan-phase`, `/gsd:execute-phase`, `/gsd:verify-work`, `/gsd:autonomous` (all phases back-to-back); `/gsd:debug`, `/gsd:validate-phase`, `/gsd:ship` (creates PR).

**Step 5 — Clone & configure**
- Prompt: clone repo to Desktop, create Python venv in project folder, install requirements.txt (18 packages), install Playwright Chromium, open in new VS Code window. 3–5 min. Then `/compact`.
- Ready state: `src/` (code), hidden `.venv`, `.env.example` (key template).

**Step 6 — API keys (.env)**
- Prompt: create `.env` from `.env.example` and open it. Never share `.env`; it's in `.gitignore`.
- Required: public notice site account (TN: tnpublicnotice.com, free) → `TNPN_EMAIL`, `TNPN_PASSWORD`; 2Captcha (~$3/mo; add $3 credit, lasts ~1 month at ~30 notices/day) → `CAPTCHA_API_KEY`; DataSift login → `DATASIFT_EMAIL`, `DATASIFT_PASSWORD`.
- Recommended: Anthropic API (~$2/mo, Claude Haiku parses notices + detects obituaries; console.anthropic.com, separate from chat subscription; key starts `sk-ant-`) → `ANTHROPIC_API_KEY`; Smarty (USPS validation, zip+4, geocode; free 250/mo) → `SMARTY_AUTH_ID`, `SMARTY_AUTH_TOKEN`; Trestle ($0.015/phone, scores 0–100 activity, eliminates ~50% dead numbers) → `TRESTLE_API_KEY`; Tracerfy ($0.02/record, phones + emails) → `TRACERFY_API_KEY`; Slack/Discord webhook (free) → `SLACK_WEBHOOK_URL` (Discord: append `/slack` to the webhook URL).
- Optional: OpenWeb Ninja Zillow data (Zestimate, MLS, equity, photos; free 100/mo) → `OPENWEBNINJA_API_KEY`; Serper.dev Google search (obituary/entity research; free 2,500 queries) → `SERPER_API_KEY`; Firecrawl JS page scraping (free 500 pages) → `FIRECRAWL_API_KEY`; Ancestry.com SSDI + obituary ($29/mo, World Explorer subscription) → `ANCESTRY_EMAIL`, `ANCESTRY_PASSWORD`.

**Configure market data sources** (built on Knox County TN; must adapt — "the step most people skip")
- Phase 1 research prompt: for [county, state] cover 6 notice types (foreclosures, probates, tax sales, tax delinquencies, evictions, code violations); for each find (1) primary source (portal, newspaper, courthouse, clerk PDF), (2) URL, (3) programmatically scrapable?, (4) publishing cadence; spawn one parallel subagent per notice type, synthesize into one table (2–3 min vs 15).
- Phase 2 paths (most counties need 2–3):
  - Online Portal (e.g., tnpublicnotice.com, Florida myfloridalegal.com portals, county clerk sites): daily automated, first-to-market within hours, zero manual work. Prompt: adapt scraper config to [portal URL], update saved searches to county + types, test scrape last 7 days (Claude edits `config.py`).
  - Monthly PDFs (tax sale lists, probate dockets): download PDF to SiftStack folder → prompt to run `pdf-import` with county + notice type, show first 10 records → then full enrichment + DataSift upload; calendar reminder monthly.
  - Courthouse In-Person (nothing digital): photograph terminal screen/paper docket weekly (30–45 min/visit) or hire via TaskRabbit/Craigslist $25–50/hr. Put photos in `courthouse_photos` folder → `photo-import` with county + type (probate/foreclosure/tax_sale/eviction); ask for blurry photos to reshoot. Optional Dropbox folder auto-sync + watcher.
- Phase 3 cadence: Portal = Daily (same-day pulls win); PDFs = Monthly (more often wastes time); Courthouse photos = Weekly. Portal easiest; courthouse most defensible.

**First run**
1. "Activate the SiftStack virtual environment and run a daily scrape for [county]. Show me the output as it runs."
2. Enrichment per notice: standardize address, Zillow, skip trace, phone score, obituary check (only services configured in .env).
3. "Open the output CSV file from that run and show me the first 10 records."
4. Full pipeline: "Run the full SiftStack daily pipeline for [county]. Upload the results to DataSift and send me a Slack summary when done." → scrape, enrich, upload, triggers SiftMap + skip tracing inside DataSift, Slack/Discord summary.
5. Verify: per record confirm address standardized, phones scored (if Trestle), DataSift upload succeeded; report failures + why. **Verify every run for first 2 weeks, then weekly.**
- Daily habit: 8 AM reminder, one prompt.
- What happened: Scraped (login, CAPTCHAs) → Enriched → Uploaded (41-column DataSift CSV; triggers SiftMap enrichment + skip trace) → Notified (record counts, errors, run duration).

**Scheduling (3 options, all set up via Claude Code prompt, run daily 6 AM Eastern)**
- Apify Actor (easiest, ~$5/mo min; `.actor/` folder is the manifest; secrets into Apify secret store; built-in monitoring; less flexible).
- GitHub Actions (free for public repos; fork repo first; version controlled; workflow installs deps, runs daily pipeline, notifies Slack on success/failure; secrets clunkier).
- Modal (serverless, free tier; retry logic, webhooks, event triggers; more concepts).

**Run the Skills (6)**
- Comping: Two-Bucket (renovated vs unrenovated), adjustments, calibrated ARV, 7-tab Excel; disclosure + non-disclosure states. Replaces a $30/comp manual workflow → run on every hot lead.
- Rehab Estimator: 4-tier finish grade (tier 3 = investor flip grade); 9-tab Excel (budget, wholetail, deal analyzer, timeline). First-pass triage in place of contractor walk-through.
- Deal Analyzer: MAO, ROI, margins, hard money vs conventional side by side.
- Buyer Prospecting: investor transactions last 12 months, filter by buyer type (fix-and-flip, buy-and-hold, wholesaler); e.g., top 50 fix-and-flip buyers with ≥3 deals in 12 months. ~10 min vs VA two days.
- Deep Prospecting: L1 enhanced skip trace, L2 public records, L3 genealogy (Ancestry, FindAGrave, obituary), L4 expert services; research pack PDF, heir mapping, DM identification; for high-value distressed properties.
- Playbook Generator: SOPs, call scripts, training; blueprints wholesale / novation / buy-and-hold / creative finance.

**Commands Claude runs (reference)**
- `python src/main.py daily --counties Knox`; `historical --counties Knox` (12 months); `pdf-import --pdf-path FILE --pdf-county Knox`; `photo-import --folder DIR --photo-county Knox --photo-type probate`; `dropbox-watch`; `csv-import --csv-path FILE` (re-import/re-enrich).
- `comp --address "…"`; `rehab --address "…" --tier 2`; `analyze-deal --address "…" --purchase-price 150000`; `market-analysis --counties Knox`; `buyer-prospect --counties Knox`; `deep-prospect --csv-path output/records.csv --depth 3`.
- `manage-presets --discover`; `manage-sold --months-back 12`; `phone-validate --list-name "Foreclosure"`; `lead-manage --lead-action qualify` (4 Pillars); `setup-sequences --dry-run` (dry run first); `niche-sequential --channel sms --day 1`; `playbook --blueprint wholesale --market knoxville`.
- Flags: `--upload-datasift`, `--notify-slack`, `--skip-smarty`, `--skip-zillow`, `--skip-obituary`, `--split` (CSV per county+type), `-v`.

**Cost tiers**
- Minimum ~$25/mo + DataSift: Claude Pro $20, 2Captcha ~$3, Smarty free (250), OpenWeb Ninja free (100), Serper free (2,500).
- Full Stack ~$160/mo + DataSift (author's tier, 1–2 counties daily): Claude Max $100, 2Captcha ~$3, Anthropic API ~$2, Tracerfy ~$20, Trestle ~$15, Ancestry $29 (optional).
- Start minimum; add services one at a time. Pipeline degrades gracefully (skipped service = skipped step).

---

## 2. SiftStack Agent Org Chart (agent-org-chart)

- 82 agents, 9 divisions; 75 live, 55 with a human gate. Legend: Live / Gated / Planned or retired / Human signs off. Top: Operator → Claude Orchestrator → SiftStack core.
- **Data Acquisition (15)** — "First to market or it is not worth pulling": Intake Orchestrator (routes every source into one NoticeData shape, dedupes by address, stamps provenance); Scheduled FTM Runner (unattended cloud FTM pull of foreclosure + probate, Knox & Blount); Egress Proxy Resolver (site gates on egress IP, not code); FTM Scheduler (business-local timing); Notice Scraper (tnpublicnotice.com, 8 saved searches, paginates all); Turnstile Gate Runner (Cloudflare Turnstile on every notice detail page); Notice Parser (address/owner/dates from free text); Foreclosure Filter (keeps only real FTM trustee sales); Courthouse Photo Importer; PDF Importer; Dropbox Watcher (cursor-based polling); Knox FTM Puller (every Knox source with a property address + buy box); Lien Resolver (lien debtor names → parcels, drops satisfied); Foreclosure Consolidator (master list of still-active foreclosures over last N months); Proof-of-Source Capture (RETIRED 2026-08-14; full-page notice screenshots).
- **Enrichment & Identity (10)** — "Who actually owns it and who can legally sign": Enrichment Pipeline (10+ steps, bare address → dialable DM); Address Standardizer; Property Enricher (beds/baths/sqft/year/valuation); Tax Enricher (delinquency, assessed value, owner of record); Obituary Enricher (DOD + named representative); Deep Prospecting v5 (heirs/signers via SmartSkip, confirmed against obituaries); Entity Researcher (human behind LLC/trust/corp); Tracerfy Skip Trace (~2¢/record phones+emails); Phone Validator (scores, dial tiers); Probate Property Finder (names but no address).
- **Deal Analysis (13)** — Post-Walkthrough Package (9 sheets, Sift-linked, built the hour after walking); Comp Package Engine (boundary-filtered comps, dual-track ARV); Dual-Track ARV Engine (price inside own bedroom band; reconfig upside labeled separately); Rehab Estimator (room-by-room, 4-tier, locked Knox material list); Master Material List (frozen, git-tracked price source); Vendor Directory Builder; Contractor Call Sheet; Exit Strategy Engine (scores each lane off conservative ARV); Deal Analyzer; Lender Analysis (private money in every resale lane); Lot Split Underwriter; Lender Package Builder (8-piece set); Lender Document Set (cover letter, promissory note, personal guarantee, closing instructions, insurance request, investor sheet, satisfaction and release).
- **Dispo & Buyers (8)** — "Deed-verified buyers before you need them": Deal Package Generator (6-sheet workbook → dial sheet); Buyer Sweep (deed-level, who buys in a zip, ranked); Dispo Skiptrace (3-source waterfall + audit matrix of which source missed); Buyer Prospector; Dispo Buyer Registry (deduped PEOPLE with scored phones); Dispo Deal Blast (text one deal to buyers whose deed history fits the price; replies to a human); Dispo Buyer Phonebook (opt-outs suppressed forever); Dispo Sequential Flow (buyer CRM lanes mirror acquisitions presets; nobody texted twice).
- **Outreach & Marketing (9)** — "The seller should feel found, not targeted": Two-Way SMS Agent; Reply Classifier (confidence floor); Reply Drafter (human voice, validator hard-blocks risky); Text Touch Builder (4 pre-call touches, varied); MMS Screenshot Sender (foreclosure homeowner gets the auction notice image); Obituary Mail Campaign; Caller Reputation Monitor; Sender Pool Manager (owner-bound numbers so callbacks reach the texter); Obituary Mail Export.
- **CRM Operations (9)** — "Get it into DataSift without silently losing half of it": Upload Orchestrator; API Uploader (no browser); Schema Setup (custom fields, select options, lists; idempotent, dry-run default); Upload Wizard Driver (5-step browser wizard); Preset Manager; Sequence Builder (26 TCA templates via drag and drop); SiftMap Sold Tagger; Account Blueprint Export (reference account → portable uuid-free JSON: preset folders/presets, statuses, lists, tags, custom fields, task presets, boards, sequences, SiftMap presets); Account Blueprint Apply (dependency order, read-back per object, gap report; stdlib only; runs on your own login).
- **Market Intelligence (4)** — Market Analyzer (6-factor zip scoring, grades, budget allocation); Market Finder Extractor; County Market Report Generator (7-sheet, county-agnostic); Obituary Opportunity Ranker (lean-budget call order).
- **Coaching & Performance (8)** — "Grade the humans and the agents on the same rubric": Call Coaching Engine (3 rubrics); Call Puller; Transcription & Triage (diarized transcript with delivery notes, then strict-JSON triage); Cold Call Coach (opener, motivation probing, objections, tonality, close); Lead Manager Coach (4 pillars, roadblocks, no-ladder, next-action); Closer Coach (money conversation, three-option offer stack, commitment locking); KPI Engine; Playbook Creator (screen recording/transcript → SOP/playbook/Scribe-style guide + agent-executable `.sop.md`).
- **Sphere of Influence (6)** — realtor contact export → scored homeowner call list: SOI Pipeline (name+email exports → confirmed metro homeowners ranked by Realtor AI score); Contact Intake; County Owner Pull (free); Owner Name Join; Enformion Resolver (pays only for roster misses); Realtor Score Enrichment.
- Install the 27 skills: `claude plugin marketplace add DataSift-Ty-Personal/SiftStack; claude plugin install siftstack-all@siftstack`; then `/plugin` → Marketplaces → siftstack → Enable auto-update.

---

## 3. Claude Co-Work & Skills for REI (claude-skills-rei)

**Co-Work**: agent mode (reads files, browses, spawns sub-agents, produces documents). Features: file access (spreadsheets/PDFs/images), sub-agents in parallel (comps + rehab), Chrome Connector.
- Setup: Claude Desktop or claude.ai, Pro $20+ → select Co-Work from the mode dropdown → set Global Instructions (market, Deal Flow Ladder phase, team size, CRM setup) → upload a `.skill`/`.plugin`. Start with the Deal Analyzer plugin (address → ARV, rehab, MAO, profit projection).
- Types: `.skill` (single workflow), `.plugin` (multiple skills; e.g., Deal Analyzer = comp + rehab), built-in (code exec, web search, file creation, data analysis). Skills now live at **Settings > Customize**.

**Capabilities & Permissions** (API skills fail silently without them)
- Name (lower left) → Settings → Capabilities → enable Artifacts, Inline visualizations, Code execution, **Allow network egress** (critical).
- Add domains to the egress allowlist: Phone Validator needs `api.trestleiq.com` (else runs, returns no scores).

**Library (19 skills listed)**
- Market Research & Data: Sift Market Research (.skill, 11MB: Market Finder reports, county analysis, zip scoring, competition, targets); First-to-Market County Data (probate, tax delinquent, foreclosure parsing, code violations); Buyer Prospector (1.9MB; SiftMap investor transactions, portfolio analysis, buyer type, scoring).
- Deal Analysis: Real Estate Comping (Two-Bucket ARV, adjustments, confidence bands, disclosure/non-disclosure); Rehab Estimator (4-tier finish, room-by-room, regional pricing, wholetail vs full rehab); Deal Analyzer .plugin (MAO + 75% Rule, ROI, full package).
- Lead Research & Prospecting: Deep Prospecting (4 levels, genealogy heirs, public record cross-ref, owner swap documentation); Probate Property Finder (probate filings, deceased owner properties, heir contacts, property status); Phone Validator (Trestle 0–100, 5-Tier Dial Priority tags, line type, ~50% dead eliminated; needs network egress); Caller Reputation Monitor (SmrtPhone caller IDs daily, warm-up/rest/rotation, dial caps, HTML dashboard, recommended dial pool, free registration + spam-flag remediation; Trestle scores numbers you dial, this protects numbers you dial FROM); Text Touch Builder (4-text sequence per ready-to-call record, identity check → breakup, seeded variants, CSV import into **Text Touch 1-4** custom fields, signed by assigned caller; callers copy next touch before the call).
- CRM & Operations: Sequential Presets (12 niche filter presets: skip trace queue, call/mail/DP cycles, not-interested recycling); Sift Sequences (TCA = Trigger, Condition, Action; 26 templates; SMS/email; folder organization); Sift Operations .plugin (lead pipeline, statuses, task presets, events, team roles, daily workflows); DataSift Lead Management .plugin (7-folder, 75-field intake structure via API, branded Lead Manager training manual, daily KPI Slack bot, round-robin task presets); Playbook Creator (SOPs, scripts, daily checklists).
- Call Coaching: Cold Call Coach (SmrtPhone recordings, audio-model tonality, opener/4-pillar/objections/close, per-caller scorecards, Excel; transcription ~$0.002/audio minute); Lead Manager Coach (4 qualifying questions: condition, timeline, motivation, price; 4-pillar depth, roadblocks, next-action); Closer Coach (multi-option offers, negotiation timeline of every price move and trigger, "moment it was won or lost"; in-person recordings too).

**Install**: download (Google Drive) → Desktop/claude.ai Pro → Co-Work or new Project → "+" → select file → trigger prompt ("Run a comp analysis on [address]") → set Capabilities if API.

**Chrome Extension**: reads active tab, extracts tables to spreadsheet/CSV, cross-references multiple tabs (Zillow vs county). Do: public data, combine with skills, structured pages, keep updated. Don't: pages requiring login, expect button clicks/form fills, JS-heavy SPAs, fast-changing real-time data.

**WisprFlow**: voice-to-text in any field; free tier.

**Troubleshooting**
- Skills not visible → Settings > Customize; refresh/restart.
- Skill not used → uploaded to current conversation/project? Say "use the [skill name] skill"; trigger phrases.
- File too large → Sift Market Research 11MB near limit; upload via Desktop (higher limits) or split (skill in one message, analysis next).
- Greyed out → plan needs Pro+; Settings > Billing.
- Chrome extension not connecting → same account in extension and web app; disable/re-enable; clear cache.

---

## 4. AI in the Daily Workflow (ai-daily-workflow)

- Run Claude like a hire across 5 seats: prospector, lead manager, closer, admin, owner. One admin + Claude covers what several seats used to.
- CRM sets the ceiling: run **STABM** on every record in and out — Status, Task, Assignee, Board, Messages.
- Benchmarks: dial floor 150, target 200; 9pm Slack daily digest per caller; weekly report takes 2–5 min per week of data.

**Seat schedule (Morning / Midday / After hours)**
- Prospector: text-touch-builder (copy next touch before each call) / phone-validator (Trestle tiers order queue; hold 150 dials) / dial-report (9pm digest). Also caller-reputation-monitor publishes daily recommended dial pool with per-number caps.
- Lead manager: sift-operations (same-day contact on every overnight lead; STABM; 100% same-day contact) / deep-prospecting-v4 (fires on dead skip traces, 3+ attempts no contact, return mail, or deceased owner; ~$2.30–3.00/record → who-must-sign table) / lead-manager-coach (weekly, tonality, 4 Pillars).
- Closer: closer-coach pre-game on past tape (widen pull to 30 days; closes rarer) / real-estate-comping + rehab-estimator (Two-Bucket ARV, room-by-room, MAO pack) / closer-coach negotiation-timeline post-mortem.
- Admin: first-market-county-data + sequential-presets (lists land before callers log on) / phone-validator (score CSVs; ~9 dials per correct number scored vs 32 blind) + candidate-intake (ranks Interview/Maybe/Dismiss; never messages anyone before your review) / text-touch-builder (regenerate touches weekly).
- Owner: read 9pm dial-report digest (know yesterday's dials per caller) / caller-reputation-monitor when answers dip / Friday kpi-engine full-funnel report + 3 coach scorecards weekly. Built-in benchmarks: 2–3 leads per prospector per day; 1 contract per 15–20 leads.
- 9pm digest contents: dials per caller vs 150 floor; answer/conversation/contact (3 rates); correct numbers + dispositions per caller; talk time with SmrtPhone cross-check (>8% drift flagged).

**Install / teach**: download → upload to Project/Co-Work (Pro $20) → trigger by name; set Capabilities for API skills or fails silently. Teach flow: record workflow (Loom transcript or Teach Claude Chrome extension) → feed to playbook-creator (record 3 times for richer SOP) → ask Claude to package as `.skill` → upload.

**Lead Management Plugin**
- 5 SiftLine boards: Lead Management, Acquisitions, Transactions, Wholesale/Flips, Rentals. 10 active statuses (New Lead through Closed). 26–27 sequences incl. both exit-pipeline drips. 75 intake fields in 7 folders, built by API in ~60 s.
- Commands in Cowork: "Set up my training" (structure + branded Lead Manager manual); "Build my custom fields" (POSTs to apiv2.reisift.io, creates folders/fields).
- Run the default build before customizing: two-drip model + temperature cadence **Hot every 2 days, Warm every 15, Cold every 45**. The Intake Call walks all 75 fields folder by folder.

**Phases**
- 1 Operator: you run every seat; Claude = second pair of hands (comping, phone scoring, list pulls).
- 2 Delegator: first hire admin $500–700/mo (list pulls, skip tracing, intake, reporting).
- 3 Manager: prospectors $750–1,000; lead manager $1,500–3,000; closer $2,000 + 3–10%; coaches + kpi-engine = management layer.
- 4 Owner: read Friday report + scorecards.

---

## 5. The Deal Flow Ladder (blueprint-hub)

- Sequential phases: Operator → Delegator → Manager → Owner; one market; graduate on overflow, never on ambition; never skip a rung ("A rung skipped is a leak built"; overflow funds every climb).
- Entry questions: (1) What's off your plate? Nothing / no flow running → Phase 1; data + admin handled → Q2. (2) Leads overflowing capacity? No → Phase 2 (multiply touches until they overflow); Yes → Phase 3 (hand off the phones). (3) Market runs without you? No → stay Phase 3 until a week away changes nothing; Yes → Phase 4 (max the market).
- Prior editions: Blueprint A, B, C, D.

---

## 6. The Data Pipeline SOP (data-pipeline-sop)

**Conveyor**: Pull → Skip trace → Route → Score → Assign. Data manager runs stations 1–4; callers take station 5.
- Headline numbers: SiftMap precision pulls = marketing starts Day 1 (pull today, dial today); each Trestle pass cuts ~50% of dials, keeps ~80% of correct numbers; 4 attempts in 72 hours (initial + 3 follow-ups) on every FTM record; 200 records/month floor per precision strategy.
- Elite lists, doors per deal (lower better): Judgments 6.2; Pre-Foreclosure Final Judgement 9.4; Negative Equity 10.9; Estate Sales 12.4; Pre-Probate / Deceased 14.8; Investor AI Score 80–90 22.8–30.6.
- Core rule: escalate cheapest touch → most expensive; cold call is the first live touch, never SMS; track every action at property level; revenue from cheap touches funds the next.

**Stage 1 — Pull SiftMap precision, market same day** (3 precision pulls in parallel, not stacked)
- AI/Investor Score 80–90: 22.8–30.6 dpd (Knox+Blount backtest); score 70 = 37.9; score 50 = 66.9 (drop only for coverage).
- Acute distressor lists (6.2–14.8): each Elite list its own parallel pull.
- Native obituary: auto-adds weekly, nationwide, nearly uncontested; living flag — if probate files later the flag flips and a last-obituary-date field keeps the record findable. Pair with deep prospecting on the heir.
- **Never AND-stack Standard Distress on the AI score**: Distress AND Investor 80+ = 48.4 dpd vs 30.6 score alone.
- Steps: (1) build filter preset in SiftMap: county + zip + list type + equity + asset class, one preset per pull; (2) toggle **Auto Add New Records** (daily job; lands on records page already skip traced on Expert plan); (3) filter to new records only via "records in my account / exclude my account" filters; (4) tag the preset (precision tag → niche pipeline; no tag → bulk).
- Exclude-my-account = lowest-saturation position; AI score flags before it's an obvious marketed list.
- Access/pricing: native distressors + obituary on SiftMap Pro, included in $499/mo Expert (unlimited skip trace), or $297/mo add-on on $149/mo Professional. Investor AI Score on $1,250/mo AI plan.
- Broad Score 50+ and Driving for Dollars = coverage (70–85% of market at 100–180 blended dpd) — reach, not the lead.

**Stage 2 — Clean + skip trace**
- Standardize every raw pull to a DataSift-compatible CSV: clean address fields, proper headers, residential only, duplicates removed; messy PDFs/photos → Claude extracts; never hand-type records.
- Skip trace ≈ guess: aggregates ~6,000 sources, ~15 candidate numbers, surfaces top 5 by cross-source frequency.
- Pass order: DataSift built-in first ($0.10–0.15/record) → Skip Genie → Forewarn (if licensed). Expert plan auto skip-traces incoming records.

**Stage 3 — Route by tag** (the Stage 1 preset's tag decides; no manual list adds on Expert)
- Precision tag (niche list, acute distressor, obituary, courthouse data) → niche sequential, click-to-dial every number, lists < ~1,000.
- No tag / bulk tag → bulk sequential, multi-line power dial, lists 1,000+.
- Current direction: pull top-neighborhood pockets, run niche click-to-dial, treat bulk-quality data like FTM.
- Suppression preset: recently sold owners drop out of marketing; **suppress, never delete**; look back ~3 years (county→Sift data delay). Mandatory settings: **last sale relationship = unrelated parties AND last sale price minimum $1,000** — otherwise $0 deed transfers and inherited/probate properties get wrongly tagged sold.

**Stage 4 — Trestle phone scoring**
- 0–100 on activity (carrier data on texts sent/numbers dialed), not correctness; ~1.5¢/number via phone-validator skill. Skip trace guesses ownership; Trestle confirms activity.
- Dial dial-first (81–100), dial-second (61–80), dial-third tags; skip dial-four and dial-five entirely.
- Each pass removes ~50% of numbers, keeps ~80% of correct; connect rate 2–3% → 10–12% (~1 in 10 dials a conversation).
- Ignore Trestle's own "do not dial below 30" advice; missed correct numbers caught later by deep prospecting + mail. Trestle pass lets bulk be worked like FTM.

**Stage 5 — Sequential flow**
- Touch costs: Text $0.01 (never first; follows call + voicemail); Cold call $0.03–0.06 (always first live touch; click-to-dial niche, power dial bulk); Direct mail $0.50–0.75 (never same mailer twice in a row); Deep prospecting $1.50–4.00 (only for survivors of every cheaper touch); Door knocking $25–50 (premium, saturated bottom-of-funnel).
- Bulk mode (Score 50+ / FTM at scale): cold call → direct mail → deep prospecting for unreachable tail.
- Precision mode: call → voicemail → text → mail → email.
- **Sensitive-list carve-out (non-negotiable)**: deceased/obituary lists drop cold text entirely: call up to 4 attempts, voicemail, mail, then deep-prospect heir/executor; monthly rehash after. Never mention the death; open with "do you have plans with the property"; address mail to the family of the deceased, not the decedent by name.
- A full attempt = dial every number on the record + send a text + leave a voicemail; manual increment — dialer integrations cannot increment the counter; team marks attempt complete.
- FTM cadence: initial + 3 follow-ups = 4 attempts in 72 h.
- **Dial back to front**: follow-up 3 → follow-up 2 → follow-up 1 → ready-to-call (else you double-dial same day).
- Mailers (980,000 platform mailers): real-pen handwritten letter + forever stamp (FTM + sensitive); snapback check at 70–80% of Zillow estimate in window envelope (bulk pattern interrupt); personalized family postcard (cheap bulk). Top leads: personalized foreclosure-options PDF in $10–12 priority envelope (value first, then ask).
- Rehash cadence: Tax sale every 30 days (hard deadline); Probate every 45 (60 loses deals, 30 annoys); all other FTM every 90 (long nurture).
- Not-interested campaigns re-call only the correct number; rehash campaigns re-call still-callable numbers on the per-list cadence.
- Saturation sets caller caliber + touch quality; sales-cycle speed sets cadence. Foreclosure most saturated, ~45 days from auction → stronger caller + premium touch. Fresh low-saturation pulls (obituary, same-day courthouse, senior, vacant, tired landlord) → cheap sequence.

**Stage 6 — Deep prospecting**: queue = precision/courthouse records that survived call, text, voicemail, mail with no contact; biggest spreads.
- L1 standard skip trace (marketing grade, top 5 of ~15); L2 title/entity error (wrong recorded ownership, trust/LLC → find signing member, skip the person); L3 deceased owner (native obituary list, confirm ownership, find heir — most DP money); L4 curative/partition (buy out heir percentages, or buy one share and force sale).
- Treat deceased as a positive DP signal from the start.

**Stage 7 — Courthouse layer**
- County filings take ~25–35 days to reach brokers/platforms; non-judicial foreclosure 3–4 weeks filing→auction → gone before paid sources show it.
- Cheapest per contract once built: $500–2,000; build ~25–35 days, 3–6 month cycle (difficulty = moat). Feeds Stages 2–6.
- Sources: Online portal (recorder, treasurer, probate clerk, code enforcement; recurring calendar pulls: tax delinquency monthly, code violations weekly); In-person courthouse (photograph terminal; runner via TaskRabbit posted under "Personal Assistant" or Investor Bootz, ~$100/weekly job; one trip covers 2–3 list types); Public notice (newspaper notices with addresses, case numbers, auction dates; Claude → CSV; automatable into Slack each morning, tagged by source).
- Run only 3–4 niches: tax delinquent, foreclosure, probate, tax sale. First-to-Market County Data skill maps office, portal, phone, difficulty per niche.
- Tax roll: latest roll; ~$200,000 assessed-value minimum + $3,000 delinquency floor (≈2 years behind). Ask for "people delinquent in their taxes for more than 2 years" or use-code 0100; never "tax deed sales."
- Probate: filings give case number + executor, no address → Probate Property Finder searches assessor by decedent name → Sift-ready CSV → owner swap to executor/heir.
- Foreclosure: pull lis pendens Day 1 (beats Day 20+ newspaper notice); marketing window Day 1–30.
- 200-record month example: foreclosures 40–50/mo; probate ~120/mo; tax sale ~750/quarter; obituary 65–80/mo; target 200+/mo per strategy. Below ~200 deal flow gets lumpy → stack niches or blend coverage-tier data. Scale precision first, then geography.
- Some operators run the cheap flow only to surface the DP set: break even front half, profit back half.

**Data manager seat** ($500–700/mo, first hire; owns Stages 1–4; Phase 1 you run it yourself)
- Daily done: auto-add presets ran; new records skip traced + tagged; Trestle pass on new numbers; dial queues set back to front; attempt counters current (incremented by team, not dialer).
- Weekly done: obituary auto-adds landed; code-violation portal pull; courthouse runner drop processed with Claude and loaded.
- Monthly done: tax-delinquency roll refreshed; suppression preset checked; every precision strategy counted vs 200-record line.

**What breaks it → fix**
- AND-stacking distress on AI score (48.4 vs 30.6) → separate parallel pulls.
- Leading with SMS/bulk text → cold call first; text after call + voicemail.
- Cold text on deceased/obituary → drop; call, VM, mail, DP heir; never mention death.
- Dialing front to back → start at follow-up 3, work backward.
- Suppression preset missing 2 settings → unrelated parties AND $1,000 min sale price.
- Same mailer twice in a row → alternate the 3 proven types.
- Trusting dialer to count attempts → team marks each attempt complete.
- One niche < 200 records/mo → stack niches or blend coverage-tier.
- Launching every niche + market at once → one county, one niche, then add.
