# REI Skill Library (25 skills) + Contractor Research Workflow


---

## Moved from CLAUDE.md on 2026-10-01 — REI Skill Library (25 skills) + Contractor Research Workflow

Verbatim text as it stood on 2026-10-01 (second split of CLAUDE.md; the first was 2026-08-28). The live current state and the traps index stay in CLAUDE.md; this is the record. Snapshot: `_CLAUDE-snapshot-2026-10-01.md`.

## REI Skill Library (25 Skills)

Distribution-ready Claude Co-Work skill files at top-level `skills/` and `plugins/` (source of truth: `skills/manifest.json` — 25 current entries + 2 superseded, `source_dir` per entry). Each `.skill` is a ZIP containing `SKILL.md` + `references/` folder. Plugins (`.plugin`) also include `commands/` and `.claude-plugin/plugin.json`.

### Skill Inventory

| # | File | Division | Score | What It Does |
|---|------|----------|-------|-------------|
| 1 | `sift-market-research.skill` | Market Intel | 9.6 | Market Finder reports, zip code scoring (6 weights verified against `market_analyzer.py`), 7-sheet Excel output |
| 2 | `first-market-county-data.skill` | Market Intel | 9.7 | County clerk data extraction for all 7 notice types, FOIA templates, marketing windows |
| 3 | `buyer-prospector.skill` | Market Intel | 9.6 | Cash buyer list from 84K+ records, LLC/trust/corp research, 50-state SOS URLs |
| 4 | `real-estate-comping.skill` | Deal Analysis | 9.7 | Two-Bucket ARV, disclosure/non-disclosure routing (12 states), adjustments verified against `comp_analyzer.py`. API-first comp acquisition (Zillow /search per comp-package) with manual browsing fallback + bedroom-band dual-track rule (2026-07) |
| 5 | `rehab-estimator.skill` | Deal Analysis | 9.8 | 912-line skill, complete Repair Cheat Sheet verified against real contractor SOW, 4-tier system |
| 6 | `deal-analyzer.plugin` | Deal Analysis | 9.6 | Combined comp+rehab pipeline, MAO (75%/70% rules), multi-loan financing, exit strategy comparison. Phase 3 now routes comp acquisition API-first (comp-package contract) with the bedroom-band rule (2026-07) |
| 7 | `deep-prospecting-v5.skill` | Deal Analysis | v5 | **SmartSkip heir engine** (relatives + phones in one batch call) + mandatory obituary/web research for DOD and true relationships + Tracerfy gap-fill + Trestle tiers. ~$0.24/record, 4.9x cheaper than the retired Enformion person path. Ships the spouse-obituary trap, the unreliable-deceased-flag gotcha, and the owner-rule-on-shared-lines rule. Enformion BusinessV2 kept for entity owners only |
| 8 | `probate-property-finder.skill` | Deal Analysis | 9.7 | Property lookup for probate decedents, 3-tier search (Tax API→Executor→People search), confidence scoring |
| 9 | `phone-validator.skill` | Operations | 9.8 | Trestle API scoring, 5-tier dial priority, 3 tier strategies, litigator/invalid/skip-line-type qualification, 4.75x connect rate. Auto-detects both the flat DataSift export and per-contact PR/relative "ready for dialing" layouts (2026-08); writes tags back into a reinsertable export, not just the global tag-by-phone-number CSV |
| 10 | `sequential-presets.skill` | Operations | 9.5 | 12 niche + 9 bulk filter presets, Pendulum Theory (SMS→Call→Mail→DP), DataSift UI implementation steps |
| 11 | `sift-sequences.skill` | CRM | 9.5 | 26 TCA sequence templates (verified against `sequence_templates.py`), UI walkthrough, HOT A01-A16 chains |
| 12 | `sift-operations.plugin` | CRM | 9.3 | CRM operations encyclopedia, STABM routine, lead pipeline (9 statuses), task presets, team roles |
| 13 | `playbook-creator.skill` | Operations | 9.5 | Playbook/SOP generator from transcripts, 7-node chart limit, 5th grade reading level, Word doc output |
| 14 | `text-touch-builder.skill` | Operations | 2026-08 | Four-text-touch pre-call SMS sequence per ready-to-call record (identity check, drip, soft ask, breakup) with cold-email style copy rotation; CSV export -> stdlib script -> Add-Data re-import into Text Touch 1-4 custom fields. **Human-voice gate added 2026-08:** `AI_TELLS` refuses (not warns) any message or pool variant containing an em/en dash, a semicolon, a link, emoji, ALL CAPS, stacked exclamations, form-letter openers, or AI vocabulary; `--check-pools` audits the variants and runs on every invocation. Same list mirrored in `src/sms_agent/respond.py` so outbound touches and inbound replies sound like one person. Community-safe (no internal API) |
| 15 | `cold-call-coach.skill` | Operations | new | Pull SmrtPhone cold-call recordings, audio-model transcription with real tonality notes, grade vs the cold-calling rubric (measured reliability +/-3 pts, calibration examples, short calls on their own scale, JSON score footers), Excel workbook export. Self-contained scripts, config-driven roster |
| 16 | `lead-manager-coach.skill` | Operations | new | Same engine, lead-management rubric: 4 pillars qualification, roadblocks, no-ladder, next-action discipline. Call quality only (no CRM hygiene scoring) |
| 17 | `closer-coach.skill` | Operations | new | Same engine, closer rubric: money conversation, three-option offer stack, objection frameworks, commitment locking, negotiation timeline reports |
| 18 | `kpi-engine.skill` | Operations | new | Universal DataSift KPI reporting from the user's own account: activity-log pull (self-contained stdlib script, own JWT, no internal API), three distinct rates, lead counting incl new_lead statuses, funnel pacing (dials->correct->leads->appts->contracts), record-level detail mode, md/CSV/Excel/Slack outputs. Benchmarks shipped as tune-per-operation baselines; internal production version lives in Deal Room `_api/kpi-engine/` |
| 19 | `comp-package.skill` | Deal Analysis | new | Boundary-filtered comp package: /search API pull with 41-row-cap band partitioning, condition bucketing by price/Zestimate ratio, dual-track ARV (same-bed base + labeled reconfig upside), 3-scenario rehab, MAO math, buyer targeting, Excel deliverable spec. Community-safe (own OPENWEBNINJA_API_KEY, requests-only script) |
| 20 | `caller-reputation-monitor.skill` | Operations | new | Keeps outbound cold-calling numbers out of carrier "Spam Likely" labels: daily SmrtPhone-caller-ID health monitoring off your own call outcomes, warm-up/active/watch/rest/retire lifecycle with dial caps, HTML health dashboard, recommended dial pool, carrier registration + flag remediation walkthrough |
| 21 | `candidate-intake.skill` | Operations | new | Aggregates job applicants from Indeed, Gmail, Facebook group posts/Messenger, or pasted text into one running scored master Google Sheet; reviews the ranked list and sends screening outreach to the best candidates. Runs through the Claude in Chrome extension, no API keys |
| 22 | `team-hiring.skill` | Operations | new | Plans, posts for, interviews, and onboards a remote REI team (data manager, prospector, lead manager, acquisitions manager, dispo): role KPIs, hiring-geography cost arbitrage, pay bands and commission, job descriptions, KPI-anchored interview guide, first-week onboarding. Pairs with candidate-intake |
| 23 | `vendor-directory-builder.skill` | Operations | new | Vetted contractor/vendor directory for any market: community mining (FB in-group search, self-promoters + recommendation-thread comments), public-record verification (phone provenance, service area, rating with count, license board, BBB), geo sweep + gap analysis + niche gatekeeper layer (utility districts), Excel via bundled build_directory.py. Never-fabricate rule; also THE tool for vetting a found/AI-generated list. Community-safe (openpyxl only, fictional example data) |
| 24 | `contractor-call-sheet.skill` | Operations | new | Action layer on a finished directory: printable one-page call sheet (build_call_sheet.py, fuzzy column detection, call-first banner for cross-validated providers) + personalized first-contact texts/voicemails + the 6 vetting-call questions. Drafts only, never sends. Community-safe (openpyxl only) |
| 25 | `dispo-deal-blast.skill` | Operations | new | Text one named wholesale deal to the cash buyers whose own deed history says they buy at that price, and hand every reply to a human. Buyer registry from investor transactions gated on recency, asymmetric price band matching, copy that cannot leak the contract price or the address, batch staged HELD behind a deliberate release, responses become a permanent buyer phonebook. Community-safe (stdlib-only cohort script; lifted from upstream b5de920 without the internal dispo engine) |

### Cross-Skill Verified Consistency

These values are identical across all skills that reference them:
- **Phone tiers:** 81-100 (Dial First), 61-80 (Dial Second), 41-60 (Dial Third), 21-40 (Dial Fourth), 0-20 (Drop)
- **Preset folders:** "00 Niche Sequential Marketing" (12 presets), "01. Bulk Sequential Marketing" (9 presets)
- **Sequence count:** 26 TCA templates across 5 folders (Lead Management 6, Acquisitions 6, Transactions 6, Deep Prospecting 4, Default 4)
- **Comp adjustments:** Bedroom $5,000, Bathroom $7,500, $/sqft $85, Age $500/yr (from `comp_analyzer.py`)
- **Financing defaults:** HML 12%, conventional 7%, 2 points, 2.5% closing (from `deal_analyzer.py`)
- **DOD sanity:** MAX_DOD_GAP_YEARS = 3 (from `obituary_enricher.py`)
- **Notice types:** 7 total (foreclosure, tax_sale, tax_delinquent, probate, eviction, code_violation, divorce)

### Key Corrections Made During Optimization (April 2026)
- **Hardcoded credentials removed** from sift-market-research (had email/password in SKILL.md)
- **Bedroom adjustment corrected** from $10K to $5K in real-estate-comping (matched to `comp_analyzer.py`)
- **HML points corrected** from 0% to 2% in deal-analyzer (matched to `deal_analyzer.py DEFAULT_HARD_MONEY_POINTS`)
- **Linux paths fixed** in sequential-presets (was `/home/ubuntu/skills/...`, now relative)
- **Preset names aligned** across 3 skills to match `niche_sequential.py` source code
- **Transfer tax labeled** as Tennessee-specific in deal-analyzer with state reference table for top 10 states
- **"Substantial renovation" defined** in real-estate-comping: kitchen + 1 bath minimum (~$15K spend)

### Skill File Structure
```
skill-name.skill (ZIP containing):
├── SKILL.md              # Main skill instructions
├── references/            # Domain knowledge files
│   ├── *.md              # Reference documents
│   └── *.pdf             # SOPs, guides
└── scripts/              # Optional automation scripts
    └── *.py / *.js

plugin-name.plugin (ZIP containing):
├── .claude-plugin/
│   └── plugin.json       # Plugin manifest
├── commands/             # Slash commands
│   └── *.md
├── skills/
│   └── skill-name/
│       ├── SKILL.md
│       └── references/
└── README.md
```

## Contractor Research Workflow (build 1.0.47, 2026-08-20)

The team's contractor/sub sourcing method, imported from the Desktop Contractor-Research-Toolkit and registered as two community-safe skills: `skills/vendor-directory-builder/` (research engine: community mining -> public-record verification -> geo sweep + gap analysis + niche gatekeeper layer -> Excel via `scripts/build_directory.py`) and `skills/contractor-call-sheet/` (action layer: printable call sheet via `scripts/build_call_sheet.py` + personalized outreach drafts, never sends). Both tier none (pure openpyxl), category Operations, on the agent map under Deal Analysis (`vendordir`, `callsheet`). Internal SOP with the real Knox+Blount worked example (68 providers): `docs/contractor-research-workflow.md`.

**Rules that make it work:** never fabricate a field (unverifiable = "not found" / UNVERIFIED, someone will dial these numbers); cross-validation (2+ independent recommenders) is the call-first signal; service area is the most common silent failure (the geo sweep removed 5 of the first Knox list); ratings always carry the review COUNT; found/AI-generated lists are claims to verify, and catching their wrong numbers IS the deliverable. The distributed bundles carry fictional example data and [Your Company]/[Your Name] placeholders; the real provider data stays in the internal doc and the Desktop toolkit. Community page (learn.datasift.ai/claude-skills-rei) listing is still a follow-up.
