# Guide C notes: sequential marketing, channel costs, text touches, direct mail, phone scoring

Source: learn.datasift.ai pages (text dumps). Live counts quoted are from DataSift's Knox + Blount build, July 2026 ("yours will differ").
Cross-page conflicts are flagged **[CONFLICT]** rather than resolved.

---

## 1. Niche Sequential Marketing (15 min)

### Framework
- Every niche record works TWO channels from Day 1: staged call cadence + 6-piece mail rotation. Nothing waits.
- Cost ladder (spend order, NOT a timeline): Cold call ~$0.03-0.06/touch -> SMS follow-up ~$0.01 -> Direct mail ~$0.50-2.00 -> Deep prospecting ~$1.50-4.00.
- Headline numbers: 27 touches in 72 hours; "3 + 6" = 3 call attempts + 6 mail pieces per record; 20-30% of deals come from not-interested follow-ups.
- Rule: spend cheap touches freely, expensive touches selectively; real money only chases records both channels missed. Nickel call, penny text, handwritten letter ~$1.75, deep prospecting up to $4.00.
- On deceased / sensitive lists: drop the cold text.
- Touch quality is saturation-driven: fresh first-to-market lists connect with standard cadence + cheap touches; saturated lists (e.g. foreclosure) need a higher-caliber caller + premium touches.
- One record sits in both channels at once (example: same record in `Hottest - 03 Call Attempt 1` and `Hottest - Mail 01 Handwritten Letter`). One queue per channel.

### 72-hour blitz (per niche lead)
- Day 1: call every number on the record; voicemail if no answer; send follow-up text; Mail 01 handwritten letter triggers (~$1.75), NO call prerequisite.
- Day 2: call every number again; voicemail (different script); different text; mailer in transit.
- Day 3: final call pass on all numbers; voicemail (urgency angle); final text variation; mailer arrives (1-3 day delivery).
- DO: click-to-dial via smrtPhone, one at a time (niche = high-value first-to-market).
- DON'T: power dialer on niche (bad first impression). Save ReadyMode for bulk (thousands of records).
- Trestle-score phones before calling; 81-100 to the top; cuts trash-number rate ~50%.

### Channel cost table
| Order | Channel | Cost/touch | When |
|---|---|---|---|
| 1 | Cold calling | ~$0.03-0.06 | First touch every time; click-to-dial |
| 2 | SMS | ~$0.01 | Follow-up after call attempts; never first touch; dropped on sensitive lists |
| 3 | Direct mail | ~$0.50-2.00 | Triggers Day 1 alongside first call, then typed 6-piece staged sequence |
| 4 | Deep prospecting | ~$1.50-4.00 | Unreachable leads, all channels exhausted |
- Never the same mailer type two months in a row. Rotate handwritten letter (forever stamp ~$1.75), family-style postcard, soft-offer check. Validated across 980,000 mailers.

### Folder structure (Filter Presets panel, 20 folders, dial order = folder order)
- Folders 01-08: niche CALL/MAIL pairs. 09-10: bulk layer. 11: Deep Prospecting. 12: Reactivation. 13-20: operations views.
- Four niches:
  | Niche | Folders | Who enters | Live size |
  |---|---|---|---|
  | Hottest | 01 CALL / 02 MAIL | `Priority 1` tag: AI score 90+ or a hottest two-list combo | 3,667 tagged |
  | Strong | 03 CALL / 04 MAIL | `Priority 2` tag: AI 70-89 or a strong combo | 9,288 tagged |
  | FTM | 05 CALL / 06 MAIL | `FTM` tag: direct-from-county uploads, 0-7 days fresh | dozens, rolling weekly |
  | Tier 2 broad | 07 CALL / 08 MAIL | `Tier 2` tag minus both priority tags | ~1,100 ready to call |
- Vocabulary rule: Tier numbers = provenance (where data came from); priority words (Hottest, Strong) = urgency. Never mix. Priority 1/2 tags are stamped on top of Tier 2 provenance.

### The two counters
- `predictivecall_attempts` (written by the dialer); `directmail_attempts` + last-mailed date (written by each send). Every preset reads these same counters; dial/mail from any view advances the record everywhere.
- One call home: each record lives in exactly one niche call cadence. Strong EXCLUDES Priority 1; broad Tier 2 EXCLUDES both priority tags. No double-dialing across niche cadences.

### Daily motion
1. Property records -> "Filter Records".
2. Expand Filter Presets; folders 01-20 in order.
3. Work top down: Hottest before Strong, Strong before FTM, broad Tier 2 last. Do what the preset name says.

### CALL folder: six stage presets (shown for Hottest; other niches swap the niche tag: Strong=Priority 2, FTM=FTM, etc.)
All carry: Niche tag Include (Priority 1 for Hottest) + Exclude the universal suppression stack.
- `Hottest - 00 Needs Skipped` — Numbers: No; Skiptraced: No. Action: run skip trace. Records returning numbers move themselves to 02.
- `Hottest - 01 Skipped No Numbers` — Numbers: No; Skiptraced: Yes. Action: second-pass provider (Skip Genie, BeenVerified) or tag `Mail Only`. Mail Only removes it from every call preset; full 6-piece mail rotation keeps working it.
- `Hottest - 02 Ready to Call` — predictivecall_attempts 0 to 0; Numbers: Yes. Live dial queue (2,572 in live Hottest).
- `Hottest - 03 Call Attempt 1` — predictivecall_attempts 1 to 1. Day 2 of blitz: call again, different voicemail script, varied text.
- `Hottest - 04 Call Attempt 2` — predictivecall_attempts 2 to 2. Day 3: final phone pass, urgency voicemail.
- `Hottest - 05 Call Attempt 3` — predictivecall_attempts 3 to 3. Record leaves niche call cadence after this; Deep Prospecting exhausted view catches the un-contacted tail; mail continues untouched.
- Dialer writes the counter; the counter moves the record. Never drag leads between stages.
- Mail never pauses while calling escalates.

### MAIL folder: 6-piece rotation (per MAIL folder, staged on pieces sent)
| Preset | Piece | Who shows up |
|---|---|---|
| Mail 01 | Handwritten letter, forever stamp (~$1.75) | Never mailed; due immediately day one, no call prerequisite |
| Mail 02 | Family-style postcard | 1 piece sent, a month or more ago |
| Mail 03 | Soft-offer check | 2 sent, last a month+ |
| Mail 04 | Handwritten letter (rotation restarts) | 3 sent, due again |
| Mail 05 | Family-style postcard | 4 sent |
| Mail 06 | Soft-offer check | 5 sent; piece 6 is the exit |
- Preset names: `Hottest - Mail 01 Handwritten Letter`, `Mail 02 Family Postcard`, `Mail 03 Soft-Offer Check`, `Mail 04 Handwritten Letter`, `Mail 05 Family Postcard`, `Mail 06 Soft-Offer Check`.
- `Hottest - Mail 01 Handwritten Letter` criteria: Niche tag Include Priority 1; Exclude universal suppression stack (Mail Only stays IN); directmail_attempts 0 to 0; Vacant Mailing: No. (2,696 Hottest records here day one live.) Export monthly, send the letter.
- Mail 02-06 criteria: Niche tag Include; Exclude suppression stack; directmail_attempts n to n (Mail 02 = 1 to 1 ... Mail 06 = 5 to 5); Last direct mailed: a month or more ago; Vacant Mailing: No.
- Six rules: (1) mail parallel to calling, first letter goes out during phone attempts; (2) never same piece type two months running (rotation bakes it in); (3) month spacing automatic, monthly pulls can never double-mail; (4) vacant owner mailing addresses excluded; (5) Mail Only records ride the same sequence from day one (no separate mail-only flow anymore); (6) after piece 6 record exits mail -> Deep Prospecting.
- Monthly ritual: open each mail preset, export, send the named piece; counters do the rest.

### Universal suppression stack (inside EVERY marketing preset; red chips = exclusions, blue = inclusions)
1. Done and dead statuses: Sold, under contract, not interested, DNC, dead lead, rest of terminal set. Actively worked cold statuses stay IN.
2. The recently-sold tag (SiftMap recently-sold feed) -> exits every flow.
3. Low and negative equity lists (sub-20% equity band loses money on average); now includes FTM records.
4. The 15 dead neighborhoods (measured investor activity of one deal or fewer).
5. Not Single Family — excluded on the priority flows (target houses).
6. Mail Only — on CALL presets only; stays in every mail flow.

### Exits
- Convert: status change (lead, under contract, sold) pulls record from all cold marketing instantly.
- Exhaust: 3 call attempts + 6 mail pieces, no contact -> Deep Prospecting + Reactivation.
- Suppress: any suppression trigger (recent-sale match, equity list) removes from every flow.

### Folder 11: Deep Prospecting (All Tiers) — $1.50-4.00/touch: deeper skip tracing, door knocks, drive-bys. Five presets:
- `01 No/Bad Phone` — skip trace empty or every number wrong.
- `02 Obituary/Deceased` — owner deceased; research heir.
- `03 Exhausted Call` — finished niche call attempts, never answered.
- `04 Return Mail` — a piece bounced; address wrong.
- `05 Vacant` — property flagged vacant.

### Folder 12: Reactivation (four Not Interested timers + Rehash Ready)
- Timers (days): Foreclosure headed to auction 15; Probate 45; Other FTM 90; Tier 2 Not Interested 45.
- `NI` Not Interested Re-Entry: Property Status Include Not Interested; Last status update older than the niche timer (15/45/90/45). Live example: "Not Interested FTM Foreclosure" with 15-day timer.
- `RH` Never-Answered Rehash (`Rehash Ready`): predictivecall_attempts at cadence max (3 niche, 6 bulk); Property Status: no disposition set; Phones: correct numbers on file. Re-enter after a rest; volume tools OK here (personal-touch rule is for fresh niche records).
- Don't skip reactivation: number already works, only timing varies; 20-30% of platform deals.

### External mailing tracking (outside mail house)
1. Tag each batch `DM MM/YYYY` (e.g. `DM 03/2026`) after export.
2. Increment Direct Mail Attempts for every mailed record (else records sit stuck in Mail 01). A send recorded anywhere advances niche and bulk views.
3. Tag bounced mail `Return Mail` -> caught by Deep Prospecting Return Mail preset.
- Sift Mail increments the counter automatically.

### Setup checklist (13): 00 Needs Skipped, 01 Skipped No Numbers, 02 Ready to Call, 03/04/05 Call Attempt 1/2/3, Mail 01-06 (names above), universal suppression verified on every preset.

---

## 2. Bulk Sequential Marketing (18 min)

- Question answered: "I want to run a big campaign today, who do I load?" One flat cadence over the whole validated universe.
- Bulk universe = every record carrying `Priority 1`, `Priority 2`, or `Tier 2` (OR). Not a new list; same records as niche, volume lens.
- FTM stays in its own lane (folders 05/06), weekly blitz; fresh courthouse pulls never mix into bulk.
- Folder `09 BULK - CALL` holds call stages 00-08; `10 BULK - MAIL` holds Mail 01-06. Same two counters.
- Live: 10,069 callable after suppression.
- Bulk and niche run simultaneously by design; shared counters prevent double-dial/double-send.
- Mail does not wait for calls; first handwritten letter due the day the record enters. Old call-first-then-mail gate is gone.

### Niche vs bulk
| Aspect | Niche (01-08) | Bulk (09-10) |
|---|---|---|
| Question | Who do I dial first? | Who do I load for a big campaign? |
| Style | Prioritized daily, Hottest > Strong > FTM > Tier 2 | Mass loads, new dialer seats |
| Call attempts | 3 per cadence | 6; attempts 4-6 = extended runway |
| Mail | Same 6-piece rotation | Same |
| Suppression | Same universal stack | Same |
| Records | One tag per folder pair | Union of P1, P2, Tier 2 |
| Best for | Dial hour in priority order | Filling a predictive dialer for a week |

### Trestle tiers (as stated on this page) — tag names in DataSift: `Dial First` (81-100), `Dial Second` (61-80), `Dial Third` (41-60), `Dial Fourth` (21-40), `Drop` (0-20).
- Drop: remove from calling queue entirely; if record has no other number above 20, tag `Mail Only` (mail runs either way).
- ~50% dead numbers eliminated, protects caller IDs, "doubles dialing efficiency". Cost ~$0.015/number; 5,000 records x 3 numbers = $225.

### Before loading the dialer
- Power (multi-line) dialer: 250-350 dials/caller/day; scale up once click-to-dial proves ROI.
- Click-to-dial: 150-200 dials/day — fine for niche, too slow for bulk (don't work a 10,000-record queue on it).
- Score with Trestle first; never dial raw unscored lists (one spam flag = weeks of recovery).
- Tools named: ReadyMode (power dialer for Bulk - 02 export); Smarter Contact (SMS follow-up after a call attempt).
- Setup: (1) find folders 09/10 in the 01-20 stack; (2) confirm skip funnel (Bulk 00/01); (3) export `Bulk - 02 Ready to Call`, load dialer.

### Bulk presets (shared filter shape: Tags (OR) Include Priority 1, Priority 2, Tier 2; Exclude universal suppression)
- `Bulk - 00 Needs Skipped` — Tags OR P1/P2/Tier 2; Exclude: done/dead statuses, recently sold, Low/Negative Equity lists, dead neighborhoods, Not Single Family, Mail Only; Numbers No; Skiptraced No. (366 live.) Action: skip trace.
- `Bulk - 01 Skipped No Numbers` — same tags/stack; Numbers No; Skiptraced Yes. Mail is their channel.
- `Bulk - 02 Ready to Call` — same tags; full stack incl. Mail Only; Numbers Yes; Call Attempts 0 to 0. (9,743 live; 965 pages x 10.)
- `Bulk - 03` through `Bulk - 08` Call Attempt 1..6 — Call Attempts N to N (N=1..6); everything else identical to Bulk - 02. Preset 08 = `Bulk - 08 Call Attempt 6`. Above rung 6 -> deep prospecting.
- Bulk and niche read the same counter; dialing from bulk advances niche cadence too. Attempts 4-6 overlap with tier mail deliberately.
- `Bulk - Mail 01` to `06` — Direct Mail Attempts one value per preset (Mail 01 = 0, Mail 02 = 1 ... Mail 06 = 5); Last Direct Mailed a month or more ago (Mail 02 onward); Tags OR P1/P2/Tier 2; Exclude full stack, Mail Only stays IN; Vacant Mailing excluded. Names: `Bulk - Mail 01 Handwritten Letter`, `Bulk - Mail 02 Family Postcard`, Mail 03 Soft-Offer Check, 04-06 repeat.
- Live: Mail 01 9,325 due now (925 pages); Mail 02-06 0 until counters advance.
- Same six mail rules as niche.
- Pick ONE pull convention per month (bulk or niche folders) purely for budgeting clarity; sends increment the one shared counter either way.

### Exits
- Convert (status change); Suppress (recent sale, equity list, dead neighborhood); Exhaust (after attempt 6 and piece 6 -> folder 11).
- Not interested is not dead: Reactivation timers 15 (foreclosure to auction) / 45 (probate) / 90 (other FTM) / 45 (Tier 2).
- Not interested -> Reactivation timer; never answered -> exhausts 6 attempts, then rehash.

### Protect caller IDs
- Free Caller Registry (freecallerregistry.com): register all dialing numbers, re-register every 90 days (calendar reminder). Free.
- Trestle (trestleiq.com): score before dialing.
- Workflow: score + tag (Dial First..Drop) -> register numbers -> load ONLY Dial First, Second, Third into dialer; Drop never enters.
- Checklist (10): locate 09/10; confirm Bulk 00; Bulk 01; load Bulk 02; confirm ladder 03-08; confirm Mail 01-06; verify suppression chips on one preset; Trestle scoring; caller IDs registered + 90-day reminder; monthly mail pull convention chosen.

---

## 3. Channel Order and Cost Per Touch (9 min)

- Order is set by compliance, not price: cold call is ALWAYS the first live touch (cold text blast = fastest route to TCPA problem + carrier spam flag). Within that, spend cheapest to most expensive.
- Ladder: SMS $0.01 (rides the dial); cold calling $0.03-0.06 (first live touch); direct mail $0.50-0.75 (non-responders) **[CONFLICT: other pages say $0.50-2.00]**; deep prospecting $1.50-4.00 (escalation tail); door knocking $25-50 (premium). 2,500x spread.
- $100 buys: 10,000 texts; 1,600-3,300 dials; 133-200 mail pieces; 25-66 deep-prospect records; 2-4 doors.
- SMS: Text Touch Builder sends 4 unique per-record texts 10-20 minutes before the dial, then a varied text rides with every call attempt. Generic blasts got numbers spam-flagged in 2 days. Never on sensitive, deceased, obituary lists.
- Cold calling cadence here: 4 full attempts per record; a full attempt = every number on the record + a text + a voicemail, then counter +1. Benchmark 150 dials/day per prospector, click-to-dial, top down from hottest folder. **[CONFLICT: niche page = 3 attempts with Day-1 mail]**
- Direct mail here: enters after the 4th full attempt (non-responders). Never same piece twice in a row; alternate letters, postcards, soft-offer checks. Exception: skip-traced records with NO numbers get mail first (Tyler: 137 postcards, 3 contracts, $70-90K). **[CONFLICT: niche/bulk pages say mail starts Day 1 for all]**
- Deep prospecting: the 22-38% of any list nobody else reaches. Entry: obituary records, 4+ exhausted call attempts, return mail, vacant at both addresses, no numbers left. On deceased-owner lists it leads from touch one.
- Door knocking: ration; best on high-saturation lists (foreclosure).
- Four rules: (1) call leads always; (2) text rides with the dial (4 unique texts 10-20 min before, non-sensitive lists); (3) mail picks up non-responders after 4 full attempts; (4) no numbers -> mail first.

### Sensitive lists (obituary, deceased, probate) flip the order
- Obituary list: send "to the family of" handwritten mail immediately; hold all calls 3 months, then dial with care; open with "do you have any plans for the property?"; deep-prospect heir/executor from touch one.
- Never on any sensitive list: cold text (zero exceptions); opening by mentioning the death; "I want to buy your house" blast copy; standard text-and-dial cadence.
- Obituary records reach SiftMap 3-7 days after the notice; immediate mail lands ~a week out (respectful window).
- Opener verbatim: "I was just calling about 123 Main Street to see if you had any plans for it."

### Saturation
- Low saturation (obituary, same-day FTM, senior, vacant, tired-landlord): standard call-first cadence on pennies.
- High saturation (foreclosure): highest-caliber caller + premium touches (door knock, handwritten mail). A cheap touch on a saturated list is less effective, not failed — escalate touch, don't abandon list. Other axis: sales-cycle speed.
- Three daily moves: (1) cheap and wide — call-first, texts ride, 4 full attempts; (2) mail the misses (no-numbers started here day one); (3) expensive and narrow — deep prospect survivors, door knock only where saturation demands. Solo = Phase 1; handing dial block to a caller = Phase 2 "The Delegator".

---

## 4. Text Touches: Warm the Number Before You Dial (12 min)

- Four short texts per record, stored in the CRM, sent BEFORE the call, never instead of it. Touch N goes before call attempt N.
- Custom fields: `Text Touch 1`..`Text Touch 4` (type Text, object Property, group Misc. fine). Spell labels exactly (import mapping matches on them).
- SiftStack `text-touch-builder` skill: 25 handwritten variants (7 for touch 1, 6 each for 2-4, incl. no-name versions); variant choice seeded from record's address + owner (neighbors never match; re-runs deterministic).
- 160-char target (one SMS segment); hard cap 320. 0 links, no images, no prices.

### One job per touch
| Touch | Job | Before | Never |
|---|---|---|---|
| 1 | Identity check only: is this address yours? | Call attempt 1 | Pitches anything |
| 2 | Drip: not sure my text went through | Attempt 2 | Guilts/pressures |
| 3 | Soft ask: ever thought about selling, can we talk? | Attempt 3 | Talks price |
| 4 | Breakup: did you decide to keep it? | Final attempt | Turns negative |
- Merge exactly four values: owner first name, street line only ("714 Martha Ln", never full address with zip), city, assigned caller's real first name. End every message in a one-word-answer question. Goal = phone call.
- Examples: T1 "Hi Eugene! I hope your week is going great. My name is Maria, I was looking at 714 Martha Ln and was wondering if it's yours? Thanks so much!"; T1 no-name "...I'm trying to reach the owner of 714 Martha Ln. Did I get the right number?"; T2 "I reached out the other day and wasn't sure my text went through. Is 714 Martha Ln your place?"; T3 "...have you ever thought about selling it? No pressure at all, just curious!" (one variant uses city merge); T4 "...Did you decide to keep it instead? Either way, wishing you the best!"; T4 no-name asks for a referral to a better contact.
- Rewrite pools in your own voice; seasonal openers (Monday/Friday, new month, holiday) when regenerating weekly.

### Nine rules
1. One job per touch. 2. <160 chars, cap 320. 3. Merge four values only. 4. Positive/warm; never urgency, pressure, all caps. 5. Never name the list (foreclosure, probate, tax, auction, "I know you're going through"). 6. No links/images/prices. 7. End on one-word question (usually yes/no identity). 8. Vary copy; neighbors never identical. 9. Goal is a call; on reply ask the three questions, then phone.

### Banned tells (skill REFUSES, not warns; skipped + reported)
- Em/en dash; semicolons; links, emoji, ALL CAPS, stacked exclamations; script phrases ("I hope this message finds you well", "I wanted to reach out", "circle back", "touch base", "at your earliest convenience", "no obligation"); vocabulary: delve, navigate, landscape, streamline, robust, leverage, utilize, seamless, elevate, unlock, tailored, curated, comprehensive, holistic.
- Human = contractions, trailing sentence, imperfect apology ("I'm not even sure I have the right number.").

### Name hygiene
- `C Eugene Suthard` -> Eugene. `E A Henry` (initials only) -> no-name variant ("the owner of 1100 Colonial Ave"). `Suthard Family Trust` -> no-name (never "Hi Suthard"). Two co-owners -> one name only. Never "Hi E A!".

### Path 1: by hand (5 steps)
1. Settings -> Custom Fields: add four Text fields `Text Touch 1..4` (one-time).
2. Open the queue's preset (e.g. Ready to Call), select all, export CSV. Need property street minimum; owner first name, city, Assigned To improve messages. (Example `Hottest 02 Ready to Call` preset shown with lists, tags, investor score 95-100, named neighborhoods.)
3. Write four messages per record; consecutive records never match. Import file columns: Property Street Address (required, match key, upsert on address, must match export exactly), Property City/State/ZIP Code (recommended), Text Touch 1-4 (required).
4. Review sample: names render (no "Hi E A"), none >320 chars, sign-offs match assigned caller; read three aloud, rewrite brochure-sounding variants.
5. Upload File -> Add Data -> choose the EXISTING list the records belong to (upserts by address, no duplicates); in mapping step (step 4 of 5) drag Text Touch 1-4 onto custom fields — custom fields NEVER auto-map. Finish.
- Upload one record and read it back first; address mismatches, wrong mapping, wrong list name all return success.

### Path 2: Claude over the API
- Use text-touch-builder skill in Claude Code (VS Code) or Claude Cowork; prompt: create fields if missing (text, property, Misc., never duplicate), pull every record in "Ready to Call" list, generate seeded touches signed by Assigned To, show 3 samples and STOP until go, then write and read one back; <160 chars; run human-voice check and report skips.
- Credentials: app.reisift.io / API apiv2.reisift.io. Open API key: reads yes, writes/upsert by address yes, custom fields NO (absent from its 93-route surface -> 404 looks like bad URL; writes 401), never expires. Minted user JWT: all yes incl. custom fields, ~48h expiry. Mint at run start (`POST /api/token/` with email/password -> `access`), re-mint every 30 min; never paste a token.
- Create fields: `GET /api/internal/custom-fields/group/?limit=999` to resolve `group_id` for "Misc." (required on create, not returned by field list); `GET /api/internal/custom-fields/?limit=999` to check existing labels; `POST /api/internal/custom-fields/` `{"label","field_type":"text","entity_type":"property","group_id"}`.
- Write values: property search = `POST /api/internal/property/` with header `x-http-method-override: GET`, body `{"limit","offset","query":{"must":{"any_lists":[list_uuid]}}}`, paginate on `count`; values via `PATCH /api/internal/property/{uuid}/custom-field/update-values/` body `[{"field_uuid","value"}]`; sleep 0.5 s (~2 req/s); checkpoint every 25; read back via `GET /api/internal/property/{uuid}/custom-field/`.
- Traps: wrong credential (401/missing routes); pasted token dies mid-run; threading (6 threads ~7 req/s -> 429 on 529 of 740; single-thread ~2/s clean); select fields need option UUID and options in the creating POST — keep Text Touch as plain text.
- Follow-up asks: fallback signer when no Assigned To; remap street column named "Address"; audit rewritten pools before generating; regenerate with Monday openers; show ten longest with char counts. Always review samples aloud before anything reaches a phone.

### Run the sequence
- Caller: open record, copy Text Touch N into texting tool, send, then dial attempt N. Touches on separate days following the preset's call cadence (e.g. Mon/Tue/Wed mornings ahead of dial).
- Reply ends automation; a person answers. Three questions: right person? considered selling? price in mind? then get on phone.
- Replies: "Yes, who's this?" -> warm intro, one qualifying question, push for call. "How did you get my number?" -> honest, calm: you research property records for homes you're interested in; fine if bad time. "Not interested" -> thank, mark record, note tone (soft no = follow-up, hard no = not). "Wrong number" -> apologize, thank, mark phone bad. "STOP"/hostility -> mark number do-not-contact immediately, no reply.
- Compliance: own business number, one-to-one; honor opt-outs immediately; quiet hours — nothing before 8am or after 9pm recipient local time; TCPA + state law apply to you; volume platforms have own registration requirements; personalization doesn't make bulk texting legal.

---

## 5. Direct Mail Mastery (18 min)

- Dataset: 6 months, 980,000 mailers from all DataSift users; operators rotating mailer types monthly beat same-template senders. $0.60-$1.90/mailer.
- Position: third in sequence (call $0.03-0.06, SMS $0.01, mail $0.50-2.00). Door knocking $25-50.

### Three mailers
- **Real Penned Handwritten Letter** — $1.79/piece (Business+). A6 envelope, Lavender or Navy Blue (five envelope styles pictured: Lavender Embossed, Orange Quatrefoil, Grey Herringbone, Solid White, Blue Mosaic). Machine real-ink handwriting; forever stamp -> 1-3 day delivery (vs 5-7 presorted bulk); no tracking barcode (looks like a real letter). Use: niche Day 1, freshest FTM (probate, tax sale, foreclosure), lists of 200-500. Arrives during Day 1-3 calls. 300 x $1.79 = $537/month. DataSift CANNOT track delivery (forever stamps bypass USPS tracking).
- **Snapback Check** — $0.67 (6x11 postcard) to $1.90 (6x18 bi-fold self-mailer), check-style layout in template editor with soft offer amount. Use: not-interested follow-ups (quarterly), warm/hot re-engagement, answered-but-said-no. Use AI Write for offer language variations; merge fields for property address. Alternate with postcards monthly; never same check design two cycles running.
- **Family/Local Postcard** — $0.60-$0.77; 4x6, 6x9, 6x11; family photo, landmarks, community branding. Use: bulk (5K+), monthly rehash of exhausted phone lists, aged data. 5,000 x $0.60 = $3,000/month (= 120 door knocks at $25). Street View add-on +$0.02 (worth it for lists under 2,000). 4x6 for high-volume bulk; 6x11 for targeted.

### When to use each
- Niche (FTM, <1K records): Day 1 call all numbers (attempt 1) + trigger handwritten letter; Day 2 call + VM + text; Day 3 call + VM + text. After 3 attempts: monthly rotation alternating postcard and snapback check. **[CONFLICT: niche page rotation is letter/postcard/check from Day 1]**
- Sensitive carve-out (deceased, probate, obituary): no cold text ever; call, voicemail, then mail; deep-prospect heir/executor. Cold call still leads.
- Bulk (SiftMap/AI data, 5K+): Phase 1 power dialer (ReadyMode, Smarter Contact) 6-8 call attempts **[CONFLICT: bulk page = 6]**; Phase 2 family postcard $0.60 alternating with snapback check monthly; Phase 3 deep prospecting $1.50-4.00 (skip trace upgrades, BeenVerified, Ancestry).
- Monthly rotation: postcard month 1, check month 2, postcard month 3.

### Not-interested cadences (20-30% of platform deals)
- Do: probate mail every 45 days; foreclosure every 15 days; general NI every 90 days; rotate mailer each cycle (postcard, check, letter); exclude returned/undeliverable.
- Don't: same mailer two months running; mail vacant addresses; skip "Do Not Mail" filter (legal risk); treat all NI the same; mail without calling first.

### Pricing (includes printing, postage, delivery; no minimums) — Business/Expert/AI | Professional | Essentials*
- 4x6 Postcard First Class $0.60 | $0.65 | $0.70
- 6x9 Postcard FC $0.63 | $0.68 | $0.73
- 6x11 Postcard FC $0.67 | $0.72 | $0.77
- 9x12 Tri-Fold Self-Mailer $0.80 | $0.85 | $0.90
- Professional Letter #10 Windowed $1.00 | $1.05 | $1.10
- Personal Letter A6 Solid $1.12 | $1.17 | $1.22
- Personal Letter A6 Herringbone $1.12 | $1.17 | $1.22
- Professional Letter #10 Non-Windowed $1.12 | $1.17 | $1.22
- Real Penned Letter A6 Lavender $1.79 | $1.83 | $1.89
- Real Penned Letter A6 Navy Blue $1.79 | $1.83 | $1.89
- 6x18 Bi-Fold Self-Mailer $1.90 | $1.95 | $2.00
- *Essentials grandfathered, not available to new accounts. Street View +$0.02 all plans.

### Setup
1. Direct Mail page (left sidebar) -> gear icon top-right.
2. Default return address (name, company, street, city, state, zip) — business address or PO Box; returned mail goes here.
3. Default sender contact (name, phone, email) -> populates Sender Fields in templates.

### Design (template editor)
1. Mail Design tab -> Create New Custom Design -> choose type (postcards 3 sizes, personal letters, professional letters, real penned letters, self-mailers; 11 types total).
2. Scratch or searchable pre-made templates.
3. Variable fields: Contact Fields (owner name, property address, city, state, zip); Sender Fields (from settings).
4. QR code free (enter URL); Street View +$0.02, renders only at print, not preview.
5. AI Write: rewrite, shorten, continue, proofread, rephrase in tones.
- Create at least 3 template variations before first campaign; letter + two postcards (family photo, landmarks) + snapback check = four-month rotation.

### Campaigns
- Path A: Records -> filter -> select -> Send to -> Direct Mail (most common). Path B: Direct Mail tab -> Create New Campaign.
- Setup: choose template + Single Touch or Multi-Touch (multiple templates over time, configurable delays). Optional batches for single-touch (all at once or split with week/month delays; controls lead volume; credits charged upfront for all batches). Schedule ASAP or future date. Orders process 5PM CST, 2-day print lead time. Credits charged on order confirmation.
- Send schedule (submit by 5PM CST -> mail date): Mon -> Wed; Tue -> Thu; Wed -> Fri; Thu -> Mon; Sun -> Tue. After 5PM rolls to next window.

### 8 Direct Mail filter presets (preset surfaces records; flow picks mailer)
Generating Leads (1-4):
1. **Skip Traced, No Numbers** (both flows): Property Status exclude all statuses; DM Attempts 0; Skip Traced Yes; Numbers No; Vacant Mailing No; Last Mail Status exclude Returned + Undeliverable. Weekly/monthly.
2. **No Contact After Marketing** (bulk; called 5+, texted 3+, no response): Property Status exclude all; DM Attempts 0; Call Attempts min 5; SMS Attempts min 3; Vacant Mailing No; Last Mail Status exclude Returned + Undeliverable. Weekly/monthly.
3. **All Numbers Bad (Wrong/Dead/DNC)** (both): Phone Status Combination all Wrong, Dead, DNC; Property Status exclude all; DM Attempts 0; Vacant Mailing No; Last Mail Status exclude Returned + Undeliverable.
4. **Monthly Mail Rehash** (bulk workhorse): Property Status exclude all; DM Attempts min 1, max 6 or 12; Last Direct Mail Date "Prior to" more than 1 month ago; Vacant Mailing No; Last Mail Status exclude Returned + Undeliverable. Monthly, different template each month.
Following Up (5-8):
5. **Leads Not Mailed** (both): Property Status include New Lead, Cold Lead, Warm Lead, Hot Lead, No Contact New Lead; DM Attempts 0/0; Vacant Mailing No.
6. **Leads Mail Monthly** (both): Property Status include lead statuses; Last Direct Mail Date prior to >1 month ago; Last Mail Status exclude Returned + Undeliverable; Vacant Mailing No.
7. **Not Interested, Not Mailed** (both): Property Status include Not Interested; DM Attempts 0/0; Vacant Mailing No. Snapback check shines here.
8. **Not Interested, Mail Quarterly** (both): Property Status include Not Interested; Last Direct Mail Date prior to >1 quarter ago; DM Attempts optional min 1, max 6-12; Vacant Mailing No. Rotate snapback check / family postcard each quarter.
- Cadence: Mondays presets 1-3 (new mail-only records from last week's skip traces); Fridays presets 5-8 (queue next week's nurture); preset 4 first Monday of each month with fresh template. Consistency over exact rhythm.
- Use "Prior to" (relative) date filters, NOT "Fixed" dates, in saved presets (fixed need manual updating).

### Campaign Templates (Business plan $299/mo and above only; Professional/Essentials configure manually)
1. After configuring, "Save New" (top-right of Campaign Setup) -> name + folder.
2. Folders example: 1 "Niche Sequential" (Day-1 letter, monthly postcard); 2 "Bulk Sequential" (family postcard, snapback check alternating); 3 "Not-Interested Follow-Up" (quarterly check, quarterly postcard). Number folders in workflow order. Alternative: by mailer type.
3. "Load" template, edit, disc icon saves updates.

### Mail status tracking (7 statuses; DM attempts + last mailed date auto-update when sent through platform)
- Scheduled (pending processing) -> Processing (printing, sent 2 days before mail date) -> En Route (dropped at USPS) -> Delivered (local USPS received). Undeliverable (address invalid, credits refunded, never printed). Returned (return to sender; can update from delivered over time). Failed (processing error; contact support).
- Batch tab tracks each record's status.
- Do: filter out Returned + Undeliverable in every preset; tag returned records to skip-trace new addresses; toggle "Do Not Mail Ever" on removal requests; check batch status 3-5 days after send. Don't: delete a template used in a scheduled/processing campaign; ignore Undeliverable (need fresh skip traces); forget to exclude "Do Not Mail Ever" (legal risk); expect tracking on Real Penned.

### External mail tracking
1. Apply preset -> Select All -> Manage -> Add Tags -> `DM MM/YYYY` (e.g. DM 03/2026) -> Manage -> Export Records.
2. Send via Sift Mail or upload CSV to external provider.
3. Same filter -> Select All -> Manage -> Update Attempts -> increment Direct Mail by 1.
- For the external "Mail Monthly" preset: exclude current month's DM tag, include previous month's tag (prevents re-mailing).

---

## 6. Phone Scoring & Spam Management (16 min)

- Problem framing: blind dialing 2-3% connect; ~50% dead numbers; dead dials train carriers to flag you; caller ID = depreciating asset. Score first, dial second.
- Waste math: 150 dials/day min per caller; 50% dead = 75 wasted dials at $0.03-0.06 = $2.25-4.50/day/caller, $50-100/month/caller.

### 5-tier system (2,000-record study, scored vs DataSift phone status)
| Tier | Score | Count | % | Correct rate | Dead rate | Action |
|---|---|---|---|---|---|---|
| Dial First | 81-100 | 881 | 43.4% | 30.3% | 1.9% | Call + text immediately; niche Day 1 priority; fills ReadyMode first in bulk |
| Dial Second | 61-80 | 99 | 4.9% | 11.1% | 11.1% | After Dial First |
| Dial Third | 41-60 | 102 | 5.0% | 8.8% | 12.7% | If capacity; tiers 1-3 = safe conservative approach |
| Dial Fourth | 21-40 | 671 | 33.1% | 1.0% | 34.6% | Low priority; consider direct mail only |
| Drop | 0-20 | 275 | 13.6% | 0.7% | 33.0% | Do not dial; remove; mail property address |
- Dial First + Second = 980 phones (48.3%) holding 92% of all Correct. Dial Fourth + Drop = 946 (46.7%) with 8% of Correct, 82% of Dead.
- Bimodal: clusters at 30 (662 phones) and 100 (819).
- WRONG-status phones average 78.2: active line, wrong person (relative, tenant, other decision-maker) -> deep prospecting look.
- Results: Joshua English VA split test 2-3% -> 9.5% connect (4.75x). 93.6% of Correct phones score >=70; 88.4% of Dead score <=30.

### Line types (Trestle vs DataSift)
- Mobile avg 80.8 (best; textable, dialable). FixedVOIP 47.0 (DataSift often labels "Landline"; many textable). NonFixedVOIP 46.5 (Google Voice, TextNow; textable, dialable). Landline 30.2 (call-only, often dead).
- 24% of DataSift "Landlines" aren't: of 776, 134 FixedVOIP + 53 NonFixedVOIP.
- Status x line type (mean/median/count/signal): CORRECT Mobile 97.3/100/263 Strong; CORRECT_DNC Mobile 95.6/100/9 Strong; WRONG Mobile 85.9/100/76 Caution; NO_ANSWER Mobile 82.8/100/327 Active; UNKNOWN Mobile 76.8/100/368 Worth Dialing; CORRECT FixedVOIP 75.0/85/8 Strong; CORRECT Landline 70.0/70/7 Strong; UNKNOWN NonFixedVOIP 50.3/45/32 Mixed; UNKNOWN FixedVOIP 48.2/30/106 Mixed; DEAD Mobile 38.8/30/97 Avoid; UNKNOWN Landline 29.9/30/337 Low Value; DEAD Landline 28.2/30/236 Avoid.

### Setup (30 min, 6 steps)
1. Trestle account at trestleiq.com — WORK email required (no Gmail/Yahoo). ~$0.015/number.
2. Export phone numbers from DataSift in Trestle's required column format (shown in screenshot; not reproduced in text).
3. Run Claude Phone Validator skill (submits numbers, retrieves scores, generates tier tags).
4. Download scored results (0-100 score + recommended tag).
5. Upload scored results back into DataSift applying the tag per number.
6. Filter by tag; load Dial First into smrtPhone or ReadyMode, then Dial Second; route Dial Fourth + Drop to direct mail only.
- Cost: 5,000 records x 3 numbers = $225.

### Phone status vs tags
- Status (what happened on the call), mean Trestle score: CORRECT 95.3 (right person); CORRECT_DNC 89.0 (right person, requested DNC, active line); WRONG 78.2 (active, wrong person, DP opportunity); NO_ANSWER 75.8 (active, retry); UNKNOWN 53.4 (never called); DEAD 31.8 (disconnected, remove).
- Tags (metadata): Trestle priority tags `Dial First`/`Dial Second`/`Dial Third`/`Dial Fourth`/`Drop`, uploaded via CSV, persist for filtering/dialer queues. Relationship tags for skip-trace context: `Daughter`, `Husband`, `Son`, `Wife`, `Grandchild`, `Relative`; `Spanish Speaking` for language routing.
- Three ways to add phone tags: within the record (click number, add tag); CSV bulk upload matched by phone number column; during data import (map tag column). Edit/merge/bulk-remove on the Phone Tags management page.

### Free Caller Registry (prevention)
1. Register every outbound number at freecallerregistry.com/fcr. 2. Takes effect within a few days. 3. Re-register quarterly — set a recurring DataSift task every 3 months (registrations expire). Free, 5 minutes. Does NOT fix an already-flagged number.
- Claude Caller Reputation Monitor skill: reads your SmrtPhone call log daily, watches answer rates per caller ID, flags numbers trending to spam, manages warm-up/rest/rotation with dial caps, writes health dashboard + recommended dial pool. Skip the Telnyx/Nomorobo/Numverify lookups in its docs; use IPQualityScore (IPQS) free tier as only outside reputation check.

### Number Verifier (detection) — app.numberverifier.com
- Real devices on AT&T, T-Mobile, Verizon call your numbers daily; reports what recipient sees. Example 865-273-0270: AT&T "Spam Risk", T-Mobile/Verizon clean (~a third of market refusing).
- Device history grid: row per caller ID, column per day, three dots (AT&T, T-Mobile, Verizon); green clean, red labeled, dash no scan. Read the row, not the dot.
  - Three green every day -> full volume.
  - One red isolated day -> note, keep dialing, recheck tomorrow.
  - One carrier red 2+ days -> submit remediation, cut that number's daily dials.
  - Two/three red 2+ days -> pull from dialer, remediate, rest.
  - Dash -> no signal.
- Name each number after its caller (e.g. "Adriana - 8", "Tinaa - 9", "Adriana - 14").
- Three layers: Trestle (numbers you dial), Free Caller Registry (prevention), Number Verifier (detection).
- Setup (7 steps): (1) account, plans sized by number count (their VHB Main pool = 21 numbers); (2) one campaign per dialing operation/brand/market (keep separate pools apart); (3) add EVERY outbound number (Phone Numbers screen or Import Numbers CSV; don't sample); (4) name after caller (optional Name field); (5) confirm Carrier column (theirs reads TELNYX, behind smrtPhone; wrong carrier = unreliable until support confirms); (6) check Device History every morning before callers start; anything red a second consecutive day comes out that morning; (7) submit remediation, Assisted Remediation for stubborn ones; Remediate column tracks last submission; rest number, dial from a clean one.
- Rotation rule: 400 dials/day on one caller ID gets flagged; split across four numbers usually doesn't.
- Automate later: webhooks, API Flags, API History, Integrations screen; pair with the reputation monitor skill.

### Pipeline placement
- Data Pull -> Skip Trace -> Phone Score -> Tag & Filter -> Load Dialer -> Call.
- Niche (200-500 records): score all; call Dial First-Third over 3 days click-to-dial (smrtPhone); Dial Fourth -> direct mail only; Drop removed.
- Bulk (5,000+): score all; load ONLY Dial First + Dial Second into ReadyMode; remaining tiers -> SMS-only or direct mail. **[CONFLICT: bulk page says load Dial First-Third]**
- Team Structure guide: Data Manager runs Trestle scoring at 11 AM daily.
