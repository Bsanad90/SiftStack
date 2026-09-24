# Guide D — CRM: Events, Sequences, Tasks, Automation/Drips, Lead Management, Intake Fields

Source pages (learn.datasift.ai): crm-events, crm-sequences, crm-tasks, crm-automation-drip,
drip-campaigns-deep-dive, lead-management, lead-intake-custom-fields. All read in full.

## Cross-page conflicts (the pages disagree — flagged, not resolved)

- **Board count:** Sequences + Lead Mgmt pages: **5 boards** (Lead Management, Acquisitions, Transactions, Wholesale/Flips, Rentals). Events page: **6 boards** (Lead Management, Acquisitions, Transactions, Wholesale, Flips, Rentals).
- **Sequence count:** "26 pre-built" everywhere; Sequences + Lead Mgmt pages also say "26-27".
- **Dead Lead:** Events / Sequences / Drip deep-dive / Lead Mgmt: Dead = **no task, no drip**, handled via marketing filters; drip page explicitly "There is no 'Dead Lead Revival' drip." The older CRM Automation & Drip page instead teaches a **Dead Lead Revival** (drip + task + sequence, 90 days) and lists "dead lead revival on 90-day cycles" under "Automate This"; drip delay ladder also lists "Dead leads = 90 days".
- **Not Interested default:** Automation page + Lead Mgmt default-automation table: status change to Not Interested **auto-changes to Cold Lead + 45-day event/task** ("Quarterly" cadence in Lead Mgmt table). Drip deep-dive / Sequences / Lead Mgmt body: Not Interested -> **Not Interested Nurture drip** (14-day buffer, 90-day cycle x4, SMS + manual call task). Events page: "Quarterly follow-up... Cadence: Probate 45 days, auction 15 days, general 90 days."
- **Ghosting:** Events page "Quarterly marketing minimum ... drip + quarterly direct mail"; Lead Mgmt cadence table "Quarterly, Ongoing, Marketing (drip)"; drip pages: 45-day cycle, 4 touches, ~142 days.
- **Automation page "Automate This"** also lists re-engaging ghosted warm/hot leads and new-inbound notifications; drip deep-dive says New Lead/Warm drips are non-standard.
- **Status count "10 active":** Sequences page list = New Lead, No Contact New Lead, Cold, Warm, Hot, Ghosting Lead, Dead Lead, Not Interested, Under Contract, Closed. Events page adds **Lost Deal** as terminal (property sold to someone else).
- **Speed-to-lead:** Lead Mgmt: HBR, calling within 1 minute = 400% higher close rate; Events: "400% higher conversion within 1 minute"; LM schedule says call every new lead within **5 minutes**; Tasks page: hot first attempt within **1-2 hours**.
- **Drip template 6:** task listed at **+48 Hours** but the wire-up text says "The task at 72 hours".

---

## 1. Events Deep Dive (crm-events)

**What Events is:** not a calendar — the scheduling engine that attaches a next action to every lead. Appointments + tasks in one place.
- **4 tabs:** All Events (combined), Appointments, Tasks, Completed (history; filter by date + team member).
- Filters: date range; team member assigned to, or who assigned, each event.
- STABM "Ta" step lives here: open Events, check tasks, execute.
- **Google Calendar:** enable under Settings; every new appointment and task syncs; DataSift edits update the calendar (described as "two-way sync layer").

**Appointments** — tied to a property record; **4 types:** Property Walkthrough, Contract Signing, Inspection, Other.
- Linking to a record auto-fills the property address as location. Virtual or in-person option.
- **Completion requires selecting an outcome**, logged to the activity record.
- **No linked record = no activity-log entry.** Always link.
- Walkthrough: pre-offer (rehab scope), post-contract (verify condition), investor partner tours; "virtual" for video walkthrough.
- Contract Signing: after seller agrees to terms. **Offer Information must be updated separately on the property record** (appointment tracks the meeting; Offer Info tracks deal terms).
- Inspection: after contract execution; outcome feeds rehab estimate + margin check; a failed inspection triggers renegotiation task or termination decision.
- Other: attorney consults (title/curative), title company meetings, buyer tours (dispo), team training on a deal, seller renegotiation/extension meetings.
- UI: Add button -> Appointment tab (type, property link, date, time); pencil icon to edit; completion dialog with outcome.

**Tasks** — assignee, deadline, notes, recurrence. Created manually or by sequences.
- Create from Events page, Records page, or within a record; the latter two auto-assign the task to the selected property.
- **Default preset groups and tasks:**
  - Lead Management: Call New Lead, No Contact New Lead, Nurture New Lead, Cold Follow-Up, Warm Follow-Up, Hot Follow-Up.
  - Acquisitions: Make Offer, Offer Follow-Up, Send Back to Lead Management. (Overview text also describes: schedule walkthrough, submit offer, comp review, offer follow-up.)
  - Transactions: Process New Contract, Follow Up with Seller and Title, Close Out Deal. (Overview: title search, inspection coordination, closing steps, seller follow-up.)
- **All presets ship assigned to the Account Owner ("Sensei").** Teams: change to "User Round Robin" and select lead manager(s)/closers/TC. Acquisitions -> closers; Transactions -> ops manager / transaction coordinator.
- **Assignment options (4):** specific user; role (every user with that role receives it); custom user group; Round Robin.
  - Round Robin variants: assign to "Everybody" + toggle Round Robin = all active users; assign by role + Round Robin = within that role; "Users Round-Robin" = pick specific members.
- Deadlines: "All day" toggle = end-of-day; uncheck + time for time-sensitive (speed-to-lead calls).
- Recurrence: daily, weekly, bi-weekly, monthly; "Repeat Until" date; "Skip Weekends" rolls Sat/Sun to Monday.
- **Visibility trap:** Acquisitions, Dispositions, Researchers, Prospectors see ONLY records assigned to them — assign the record too or they can't open it. **Lead Managers** see records assigned to themselves and any other user, but **not records with no assignee**.

**Custom task presets:** Events -> Preset -> Create Group -> Add New Preset. Usable in sequences.
- "Master the default build before you touch it" — pre-built sequences trigger off specific status names and board moves; renaming a status or deleting a board silently breaks them. Add on top.
- Example groups:
  1. **Wholesale:** "Send to buyer's list" (immediate), "Follow up with top 3 buyers" (24 h), "Schedule closing with buyer" (3 days), "Confirm assignment fee and wire instructions" (closing minus 2 days). Trigger: SiftLine Card Created on Wholesale board. Assignee: Dispositions manager or Round Robin across acquisitions.
  2. **Rentals:** "Schedule tenant walkthrough" (immediate), "Property manager introduction" (3 d), "Verify insurance coverage" (7 d), "Lease signing coordination" (14 d), "First rent collection confirmation" (45 d). Trigger: SiftLine Card Created on Rentals board. Assignee: Operations manager.
  3. **External Integration:** "Update deal tracker spreadsheet" (on contract), "Send Zapier webhook for new contract notification" (immediate), "Notify attorney for title review" (on contract), "Update investor portal" (on closing). Trigger: Property Status Change to "Under Contract". Assignee: Admin/ops manager.
- Do: name groups by workflow stage; 3-6 tasks per group; deadlines relative to trigger; test on a dummy record. Don't: 20 tasks in one group; tasks without assignees; skip the sequence connection; forget record assignment for restricted roles.

**Default account build:**
- **Boards (6 per this page):** Lead Management (New Lead (Unqualified) -> handoff to Acquisitions or Dead Lead); Acquisitions (Appointments, make offer -> Lost Deal or back to Lead Management); Transactions (New Contract -> Closed or Fell Through); Wholesale (New Contract -> Closed or Fell Through); Flips (New Flip -> Sold); Rentals (New Rental -> Rented).
- Board permissions: Just Read, Read & Write, Admin. All auto-added for Account Owner; add members by editing the board.
- **Statuses (10 active) and meaning:**
  - New Lead — wants to sell; call immediately.
  - No Contact New Lead — trying to connect; daily follow-up.
  - Cold Lead — long horizon; every 45 days.
  - Warm Lead — mid horizon; every 15 days.
  - Hot Lead — fast attention; every 2 days.
  - Ghosting Lead — no contact over time; casual no-pressure nurture.
  - Dead Lead — bad lead / unrealistic seller; no task, no drip; handled via marketing filters.
  - Not Interested — seller declined; quarterly follow-ups by property type.
  - Under Contract / Closed — exit statuses. **Lost Deal** = terminal for property sold to someone else.
- Task presets: Tasks page -> **Configure Presets**. Teams: three dots next to task -> Edit -> assign by User or Role.
- **Saved filter presets (Records -> Filter Records -> Load Presets):** My Tasks (due-today/overdue/to-do; per user), Acquisitions (offer + offer follow-up tasks), Lead Management (QC view: lead tasks + leads without tasks), Transactions (contract + title/seller follow-ups), DataSift Base Presets (starter marketing filters).
- **Pre-built sequences:** 26, triggered by status changes and board moves; tasks loop automatically (e.g., daily follow-up becomes weekly after 3-5 days no contact, then Ghosting if still no response). Toggle/edit/delete/create on Sequences page; can Round Robin assign records to users/role inside sequences.
- **Lists** = qualifying type / distressor; choose "Add to existing list" on upload. **Property Tags** defaults include "Do Not Market", "Returned Mail". **Phone Tags** = relationships (Husband, Wife, Son, Daughter…). **Phone Statuses:** Correct, Wrong, Dead, DNC.
- **Configuration checklist (8):** presets reassigned off Account Owner; Google Calendar connected; Round Robin for caller + lead manager roles; record assignment paired with task assignment for restricted roles; all 26 default sequences ON; account time zone set (Settings > Profile); team members on SiftLine boards with right permissions; test sequence on dummy record verifying task creation.

**Sequences create events:** 26 pre-built only activate on **"New Lead"** status change = marketing-to-lead-management handoff.
- Limits: Professional 8 sequences; Business+ unlimited.
- Sequences cannot trigger other sequences (loop prevention); failure -> bell-icon notification.
- **Who can create/edit sequences:** Sensei (Account Owner), Super Admin, Admin, Marketer. Prospectors, Lead Managers, others can trigger but not create/edit.

**Lead lifecycle through events:**
- New Lead: task "Call immediately"; all 26 sequences check New Lead.
- No Contact: daily task 3-5 consecutive days; call daily, voicemail + text days 2-3; after 5 days status stays "No Contact New Lead", task steps down to weekly Nurture New Lead.
- Nurture New Lead = a **task stage, not a status**; weekly call/text for 3-6 months; manual follow-up.
- Hot: "Schedule walkthrough" appointment + "Submit offer" task; every 2 days. Pillars (price <=80% of Zestimate, 2+ pillars) set starting temp, situation can keep it Hot.
- Warm: every 15 days; one pillar confirmed; uncover timeline/condition/price flexibility.
- Cold: every 45 days; zero pillars but not dead.
- Ghosting: quarterly marketing minimum; drips + quarterly direct mail.
- Dead: no task, no drip; re-engaged via bulk marketing pulls, not a CRM drip that double-contacts active leads.
- Not Interested: quarterly; 20-30% of all platform deals come from not-interested leads; cadence probate 45 d, auction 15 d, general 90 d.
- Under Contract: Acquisitions preset fires "Schedule inspection", "Send to title", "Coordinate with buyer"; record moves to Acquisitions board, Transactions team takes over.
- Closed: Transactions preset: final paperwork, payout tracking, post-closing review.

**Activity logging & STABM:** Status, Task, A(ssign), Board, Message (post notes to Message Board on every record touched).
- Property Details page "Assigned Events" section = pending tasks/appointments; completed move to activity log with timestamp + user. Events "Completed" sub-tab = account-wide.
- Do: complete tasks through Events tab (auto-logs); pair task + record assignment; stage-based preset groups; STABM on every record in and out; name presets by stage not person. Don't: giant preset; task without record; skip STABM; leave completed events unmarked; rely on memory ("If it is not logged, it did not happen").

---

## 2. CRM Sequences & Automation (crm-sequences)

- **Records vs Leads:** prospecting records get calls/texts/mail via sequential marketing. When someone says "I'm interested", flip status to **New Lead** -> activates the 26 pre-built sequences. Before New Lead = outbound; after = inbound management.
- Sequences supplement STABM; don't replace it ("STABM is the cockpit, sequences are the autopilot").

**Anatomy:** name + folder; **one trigger** per sequence (same actions on two triggers = two sequences); optional conditions (AND logic); one or more actions.
- Naming convention: `[Trigger Type] - [What It Does]`, e.g., "Status Ghosting - Nurture Drip + Task", "New Lead - Assign Round Robin".
- **10 triggers:** Property Status Change, Property Assignee Change, Tags Added, Tags Removed, Lists Added, Lists Removed, Task Created, Task Completed, SiftLine Card Created, SiftLine Card Moved. Status Change + Tags Added cover ~80%.
- **13 conditions** (as grouped): Property Status; Property Status Change (from X to Y); Property Assignee; Property Assignee Change; Property Tags; Property Doesn't Have Tags; Property Tags Added; Property Lists; Property Isn't on Lists; Property Lists Added; Card Board & Column; Card Has Task; SiftLine Card Moved.
- **12 actions** (as grouped): Create New Task; Change Property Status; Create New Card; Move Card; Duplicate Card; Delete Card; Add/Remove Property Tags; Add/Remove Property Lists; Clear Property Tasks; Start Drip Campaign; Remove from Drip Campaign; Assign Property; Send SMS; Send Email.
- **Actions execute immediately (no delays)**; for delays pair with a Drip Campaign (sequence triggers drip; drip handles timing).
- **No chaining:** an action from one sequence cannot trigger another (A adds Tag X, B triggers on Tag X -> B does not fire). Build every needed action into one sequence.

**Creation A-Z:**
1. Sidebar -> Sequences (26 pre-built if account created after **April 16, 2025**) -> **Create Sequence** (top right). New accounts get 5 folders: **Acquisitions, Deep Prospecting, default, Lead Management, Transactions**.
2. Select trigger (most common Property Status Change).
3. Add conditions (optional, AND logic).
4. Configure actions (combos: Change Status + Create Task + Add Tag; or Assign Property (round robin) + Send SMS + Create Card).
5. Configure action details: task actions pick an existing Task Preset or create inline; card actions pick destination board + column. **Task presets must exist on the Tasks page first** (else dropdown empty).
6. Name, choose folder, save -> starts **active** (toggle off on Sequences page any time).
7. Verify in record's **Activity Log** — sequence name appears next to each automated action with timestamp.
- Warning (Phil Loesch): don't set up sequences without understanding each action; one misconfigured sequence on thousands of records takes hours to clean up.

**Trigger usage notes:**
- Status Change: behind all 26 pre-built (New Lead); also Ghosting Nurture, Not Interested, Hot Lead alerts.
- Assignee Change: intro task for new owner, notification SMS, move to their board.
- Tags Added/Removed: e.g., "Probate" tag -> specialized follow-up; removing "Do Not Call" -> re-entry to calling. **Tags applied during initial upload do NOT fire** (trigger watches changes after record creation).
- Lists Added/Removed: e.g., "Hot Leads Q1" list -> closer review task same day.
- Task Completed: chain work ("initial call task done -> follow-up task 48 h later").
- Card Moved: "Offer Sent" -> "Under Contract" can create transaction tasks, notify, update status.
- Conditions: "Property Status is" = current; "changes from X to Y" = transition. Assignee condition routes to specific members. Tag/list combos, e.g., "Has tag: Probate" AND "Is on list: High Value Properties" AND "Doesn't have tag: Do Not Contact". Card condition e.g., "Acquisitions board, Under Contract column".
- **Create New Task trap:** "Assign to property assignee" ON + record has no assignee = **task not created**. Assign the property first.
- Pipeline actions cascade: status changes triggered... (page says status changes cascade to trigger other sequences downstream — note this contradicts the no-chaining rule; stated as written).
- Organize: when Dead -> remove from "Active Leads" list + clear open tasks; on close clear all open tasks.
- **Drips:** Ghosting -> 45-day Ghosting Nurture; Not Interested -> 90-day Not Interested Nurture. **Mandatory: the receiving sequence (New Lead, Cold, Warm, Hot) MUST include "Remove from Drip Campaign"** or lead gets call tasks AND drip texts.
- Assign Property: specific user or round robin; foundation of distribution at Phase 3.
- Send SMS/Email: instant; SMS needs smrtPhone, Twilio or Plivo; email needs Gmail.

**Single vs multi-step:** single = 1 action, ~2 min setup, 1 failure point, simple routing ("Ghosting? start drip", "Tag Probate? assign niche specialist"). Multi = cascade ("New Lead? assign round robin + call task + add to Active Leads list + welcome SMS + SiftLine card").

**Default library (accounts after April 16, 2025):** 26 sequences in 3 categories, all trigger on status -> "New Lead". Default stack: 5 boards (Lead Management, Acquisitions, Transactions, Wholesale/Flips, Rentals), 10 active statuses, 26-27 sequences. **Renaming a status the sequences fire on silently breaks every automation.**
- Lead Management (essential): Assignment & Distribution (round robin/direct; create initial SiftLine card; fire first so others have an assignee — solo: assign to self); Task Creation (first call, follow-up, qualification tasks via presets, assigned to property assignee with due date; adjust e.g. 1-hour first call); Tagging & List (source type, property type, temperature tags; add to "Active Leads" list).
- Acquisitions: Offer & Contract (status "Offer Sent"/"Under Contract" -> send contract to title, schedule inspection, EMD deadline; move card); Due Diligence (inspection, appraisal, title search reminders over 30-45 day contract period).
- Transactions: Closing & Disposition ("Closing"/"Closed" -> archive, update tags, remove from active lists, post-close tasks: recording docs, thank-you, referrals); Post-Close Follow-Up (30/60/90-day check-in drip; wholesalers notify buyers list).
- **Activation checklist (5):** assignment sequences configured with team (or self); task presets created before enabling task sequences; SiftLine boards have the columns sequences reference; tag/list sequences reviewed; test one record -> status New Lead -> verify all actions fired.

**SMS via sequences:** prerequisite smrtPhone/Twilio/Plivo (**Kixie, Smarter Contact, Launch Control not supported**). Open sequence -> "Make Changes" -> "Add new Action" -> Send SMS.
- Variables: @Owner First Name, @Owner Last Name, @Property Full Address, @City, @State, @Zip, @User First/Last Name. Empty field renders blank.
- Recipients: Primary number (starred), All verified numbers (checkmarked), custom number. Primary is usual.
- **Send window 8 AM–9 PM account timezone**; trigger at 11 PM queues until 8 AM.
- Email: connect Gmail first; composer with subject + rich text (bold, italic, underline, strikethrough, bullets, numbered); same variables in subject + body; recipients primary / all verified / custom; immediate, same 8-9 window. Cold email at scale -> Drip Campaigns; sequence email for notifications/confirmations/warm follow-ups.

**Folders:** structures by Department (Lead Management, Acquisitions, Transactions, Marketing), by Trigger Type (Status-Based, Tag-Based, Task-Based, SiftLine-Based), by Team Role (Caller Sequences, Lead Manager, Closer, Admin/Owner).
- Create Folder in sidebar; **default folder cannot be deleted or renamed**; move via three-dot menu -> Move to Folder (or in save dialog); moving doesn't affect trigger/actions; search matches sequence names, not folder names.
- **Deleting a folder is permanent**; if not chosen otherwise, sequences move to default folder (not deleted).

**By phase:**
- Phase 1 Operator (Professional, **8-sequence limit**) — choose 8: New Lead Assignment (self + first call task); New Lead SiftLine Card; Ghosting Nurture (45-day cycle SMS drip, 4 touches, status = Ghosting); Not Interested Nurture (90-day cycle, 14-day buffer, 4 cycles, SMS + manual call task each); Hot Lead Alert (urgent task on Hot); Offer Sent Tracking (move card to Offer column + follow-up task); Under Contract Checklist (inspection/title/EMD tasks); Closed Deal Archive (clean up tags, lists, tasks).
- Phase 2 Delegator (Business+, unlimited): speed-to-lead SMS within seconds; dual-path tagging (FTM vs Stacked Niche); escalation if No Contact 5+ days; all 26 activated + customized.
- Phase 3 Manager: multi-role round robin (callers -> escalate to LMs); role-specific task presets; team SMS alerts on high-value leads; auto-reassign after X days no activity. First hire Data Manager ($500-$700/mo); ~2 callers per lead manager; never a salesperson before the marketing layer.
- Phase 4 Owner: niche tagging by property type on New Lead; deep prospecting L1-L4 research tasks when tagged; genealogy research flag for Data Manager; Not Interested Nurture; periodic niche marketing (probate 45-day, Ghosting-style); KPI tracking tags at pipeline stages.

**Troubleshooting (sequences fail silently; only a bell-icon number):**
- Bell icon failure: target board/column missing, tag/list deleted, or no assignee for task -> read message, fix config.
- Never fires (run count 0): toggled off, trigger mismatch (Hot vs Warm), conditions exclude, upload-time tags.
- Wrong assignee: round robin not configured or includes inactive members; **without round robin default goes to account owner (Sensei)**.
- Duplicate actions: two sequences with overlapping triggers, no differentiating conditions -> add conditions or consolidate.
- **Plan limits: Essentials (legacy) 3, Professional 8, Business unlimited ($299/mo).** Consolidate same-trigger singles into multi-step; on Professional cover lead management first, acquisitions second.
- SMS/email not sending (run count increments): integration not connected, or record lacks valid primary phone/email; check integration dashboard.

---

## 3. CRM Tasks & Task Presets (crm-tasks)

- Do: work overdue first then by temperature; batch similar tasks; close every task with a next action or disposition; build presets before enabling sequences. Don't: cherry-pick; leave overdue without rescheduling; tasks without assignees/deadlines; manually recreate same task daily.
- **Creation:** entry points Events page (bulk; must attach property manually), Records list (select one or more for batch), individual record (auto-links). Steps: entry point -> "Add New Event" -> Task tab -> assignment method -> deadline (All day vs specific time; hot callbacks need a specific hour) -> recurrence ("Repeat Task": daily/weekly/bi-weekly/monthly; "Skip Weekends") -> notes (what to say/look for/outcome) -> save (triggers Google Calendar sync).
- **Always create from within the property record** (saves ~10 s/task).
- **Assignment matrix:** Self-assign (solo, general: All-Day deadlines, daily recurrence for prospecting, review each morning); Round Robin (team, general: "Round Robin" -> "Users Round Robin"); Self + Notes (solo wearing hats: prefix "[Closer] Review offer on 123 Main St"); Role / Custom User Group (team, role-specific; e.g., "Senior Closers" vs "Junior Closers"). **Round Robin is default for teams of 2+**; override only for specific expertise (counter-offer to that closer).
- **Record visibility by role:** Sensei/Super Admin/Admin — all records; Marketer — all records; Prospector, Acquisitions, Dispositions, Researcher — **assigned records only** (assign record before/alongside task). Task-only = task visible but property locked; no console error, no warning.
- **Deadline patterns:**
  - Hot: recur every 2 days; first attempt on new hot lead within 1-2 hours, specific time (urgent notification); **Skip Weekends OFF**.
  - Warm: every 15 days; All-day, 15 days from last contact; Skip Weekends ON.
  - Cold: every 45 days; All-day, 45 days from last contact; Skip Weekends ON. **Cold is NOT on drips** (manual call every 45 days; only exception: solo operator who will never hire an LM).
  - No Contact / Nurture: status stays "No Contact New Lead" through both. Phase 1 "No Contact New Lead" task (days 3-5) daily, 3 attempts/day (part of the **27-touch attempt grid**), Skip Weekends OFF. Phase 2 "Nurture New Lead" task (week 2+) weekly for 3-6 months, Skip Weekends ON. Switch after initial attempt cycle exhausts.
  - Temperature = cadence, not motivation count. 4 Pillars (Reason, Timeline, Condition, Price) + price band set starting temperature; situation overrides (foreclosure with auction date stays Hot).
- **Preset Pyramid:**
  - Layer 1 defaults (as-is; covers 90% for Phase 1 solo): LM (Call New Lead, No Contact New Lead, Nurture New Lead, Cold Follow-Up, Warm Follow-Up, Hot Follow-Up); Acquisitions (Make Offer, Offer Follow-Up, Send Back to Lead Management); Transactions (Process New Contract, Follow Up with Seller and Title, Close Out Deal). Ship assigned to Sensei — change to User Round Robin before hiring. 3-6 tasks per group.
  - Layer 2 customized: assignees; deadlines (tighten in fast markets e.g. Phoenix, Dallas; longer rural/Midwest); descriptions (scripts, comp criteria, offer templates — "call lead, reference probate filing date, ask about timeline to sell").
  - Layer 3 custom: Wholesale (send to buyers list, follow up top 3 buyers, schedule closing, confirm assignment fees); Rentals (tenant walkthrough, PM intro, insurance verification, lease signing, first rent collection); Deep Prospecting (skip trace research, heir tree check, courthouse records pull, Ancestry.com search, owner swap in CRM); External Integration (Zapier task relay, attorney package send, investor portal update). Naming `[Department] - [Workflow]` ("Acquisitions - Wholesale", "Research - Deep Prospecting").
  - Creating: Events -> Preset tab -> "Create Group" -> "Add New Preset" (task name, assignment, deadline, recurrence, description).
  - **Create presets BEFORE enabling sequences.** Sequences reference presets by name; missing preset = task action silently fails.
- **Completion:** logged with timestamp + user; Completed tab. Completion can fire "Task Completed" trigger -> next task, status change, notification, drip (page says completion "does five things"; the list isn't in the text).
- **Morning triage (the T in STABM, ~10 min):** 1 open Events tab (not email/Slack); 2 sort by overdue; 3 batch by type (calls, texts, offer reviews); 4 within batch hot -> warm -> cold -> nurture; 5 set tomorrow's tasks — every active lead has a task tomorrow.
- **Scorecard KPIs:** completion rate 95%+ (green >95%, yellow 80-95%, red below); overdue <5%, zero at end of day; time to complete: Hot within 2 h, Warm within 48 h, Cold within 7 days; task load 40-60/day per caller.
- **Lead manager daily benchmarks:** dials 150/day (floor); conversations 25/day; conversation rate 15-20% (below 15% = list/number problem); **Sent to AQS 2-5/day (THE money metric)**; same-day contact 100%; leads missing tasks 0.
- Queue priority: probate + foreclosure before high-equity cold; inbound replies before outbound. **Every call ends with an appointment or a dated follow-up task.**
- **By phase:** P1 Operator: Layer 1 as-is, all self, no round robin, phone reminders for hot, 20-30 tasks/day. P2 Delegator: Layer 2, list hygiene/admin to Data Manager, owner keeps seller conversations, audit record assignment weekly. P3 Manager: Layer 2 + Layer 3 per exit strategy, round robin general, role-specific acq/transactions, ~2 callers per LM, audit sequence frequency weekly, Sales Manager ($5,000-$6,000/mo) audits overdue daily. P4 Owner: Layer 3 deep-prospecting presets (skip trace, heir tree, courthouse, owner swap), self or Data Manager ($500-$700/mo), 3-4 custom presets max, 10-15 deep research tasks/day.

---

## 4. CRM Automation & Drip Campaigns (crm-automation-drip) — older page, see conflicts

- Philosophy: automation supports, never leads; personalized/non-automated outreach gets higher response ("insane drip sequences that feel automated ... going away in 2025"). Primary system = lead manager doing 50 attempts/day.
- Automate: re-engaging ghosted warm/hot leads after manual attempts stall; dead lead revival on 90-day cycles; new inbound lead notifications; automated texts paired with manual tasks. Don't automate: entire follow-up; robotic AI sequences; judgment on temperature/objections.
- **Four core systems:** Events (calendar-driven alerts fired by status changes; default events pre-built); Tasks (presets with assignee — person or round robin — due date, notes; due dates daily/15/45/90 days); Sequences (trigger/condition/action; drag-and-drop builder; chainable conditions); Drip Campaigns (timed SMS; merge fields property address/contact name; delay nodes in hours/days/weeks).
- **Default presets table:** New Lead — alert + task, immediate; No Contact — daily task, 3-5 days; Nurture — weekly, 3-6 months; Hot Follow-up — priority task, every 2 days; Warm — every 15 days; Cold — every 45 days; **Not Interested — auto-change to Cold Lead + 45-day event**.
- SiftLine (visual board) and Records Page both trigger the same automations.
- Example routing: "hot qualified lead AND 2 closers -> route to best closer".
- **Dead Lead Revival (as taught here):**
  - Drip "Dead Lead Revival": SMS 1 immediate "It's been a while since we spoke about [property address]. Just checking in to see if anything's changed." -> 90-day delay -> SMS 2 "Hey [contact name], just wanted to touch base again regarding [property address]. I hope you're doing well." (adjustable 60/120 days).
  - Task preset: name "Dead Follow-Up Attempt One"; assigned Lead Manager (or Round Robin); due 90 days; notes "Dead revival on a 90-day drip".
  - Sequence "Lead Management Revival": trigger Property Status Change from ANY to DEAD LEAD; fires drip, creates 90-day task, updates SiftLine phase.
- Not Interested campaigns: identify NI records with valid phones (focus first-to-market); build drip (use SIFT AI skill for messaging); positive reply -> back to LM pipeline.
- Ghosting recovery: closer ghosted -> push back to LM with temperature preserved -> LM restarts no-contact cadence (daily 3-5 days then weekly) -> re-qualify on 4 Pillars -> back to acquisitions. Root cause = an uncovered objection: **Price, Time, Third Party, Trust**.
- Golden rule: every lead must have a next step or action.

---

## 5. Drip Campaigns Deep Dive (drip-campaigns-deep-dive)

- Sequences = immediate; drips = configurable delays (min/hr/days). Account ships **26 sequences, zero drips** (build your own). Sequences trigger drips.
- 80% of motivated sellers need 5+ follow-ups.
- **Three design rules:** (1) drips supplement manual follow-up, never replace; (2) the receiving sequence (New Lead, Cold, Warm, Hot) MUST include "Remove from Drip Campaign"; (3) no task conflicts — don't add drip tasks to records with a running sequence task loop; pick one.
- Active pipeline (New Lead, Cold, Warm, Hot) gets manual calls; **Cold not on drips**. Read drip texts aloud; rewrite robotic ones.
- **Step types:** SMS (needs smrtPhone/Twilio/Plivo; ~$0.01/message; urgent touches); Email (Gmail; all plans; longer nurture; use drip email for cold outreach at scale); Task (due date from the preset, not the drip delay).
- **Delays:** minutes 0-59, hours 1-23, days 1-365. First step fires immediately on enrollment. Send window 8 AM–9 PM account timezone, else holds to 8 AM.
- Carriers: Kixie, Smarter Contact, Launch Control incompatible. Comply with **A2P 10DLC**. If smrtPhone numbers missing: open any record, click refresh phone icon in 1:1 communication section.
- Merge fields: {First Name}, {Last Name}, {Property Address}, {Agent Name}, {Company}; missing = blank ("Hi , this is about" looks worse than no text). Insert by typing @[variable_name]. Keep SMS under 150 characters.
- **Build:** 1 sidebar Drip Campaigns; 2 Add New Campaign, descriptive name incl. cadence ("Ghosting Nurture 45-Day"); 3 drag SMS/Email/Task steps; 4 delays between steps; 5 select carrier + number per SMS step (each step can use a different number); 6 write copy + save -> ready for enrollment.
- **Two canonical drips (build first):**
  - Ghosting Nurture — trigger: record moves to Ghosting phase; 45-day cycle, 4 touches, ~142 days; casual; metric reply rate (any response -> back to active).
  - Not Interested Nurture — trigger: disposition "Not Interested"; 14-day buffer then 90-day cycle x4, ~360 days; each cycle SMS + manual call task; metric re-engagement rate. 20-30% of platform deals from initial NIs.
  - Dead: no drip, no task (marketing filters). No "Dead Lead Revival" drip.
- **Templates (verbatim copy):**
  1. **Ghosting Nurture (45-Day), 4 SMS:** Immediate: "Hi {First Name}, this is {Agent Name} with {Company}. We chatted about {Property Address} and then lost touch. No worries at all. If you want to pick it back up, just reply to this text." +45 d: "Hey {First Name}, circling back on {Property Address}. No pressure. If you ever want to explore your options, I'm here." +45 d: "{First Name}, still thinking of you on {Property Address}. Whenever the timing feels right, reach out. I'm easy to work with." +45 d: "{First Name}, one last casual check-in on {Property Address}. Circumstances change and I want to make sure you have my number. Reply anytime." Wire: sequence trigger = moves to Ghosting; action add to drip; re-entry sequence must Remove from Drip.
  2. **Not Interested Nurture (90-Day), 4 SMS + 4 tasks** (non-distressed): +14 d buffer SMS: "Hi {First Name}, I completely understand that {Property Address} isn't something you're looking to sell right now. If that ever changes, my number is right here." Same day task: "Call {First Name} re: {Property Address}. Not-interested cycle 1 check-in." +90 d SMS: "Hey {First Name}, touching base about {Property Address}. Things change and I wanted to make sure the offer stands if you need it." Same day task: "... Not-interested cycle 2 check-in." Repeat pair every 90 days for 4 cycles. Wire: trigger disposition -> Not Interested; add to drip; re-entry Remove from Drip.
  3. **NI Probate (45-Day), 3 SMS + 1 task:** Immediate: "Hi {First Name}, I understand dealing with {Property Address} is a lot right now. No rush. If the estate process gets complicated or you need options, I'm here." +45 d: "Hey {First Name}, checking in on {Property Address}. Probate timelines can shift and I wanted you to know the offer is still available." +45 d task: "Call {First Name}. Probate 90-day checkpoint on {Property Address}." +45 d: "{First Name}, one more check-in on {Property Address}. The estate timeline may have moved along. Reply if you'd like to revisit." Wire: NI trigger + condition probate tag; route here instead of general 90-day.
  4. **NI Foreclosure (15-Day), 3 SMS:** Immediate: "Hi {First Name}, I understand the timing wasn't right for {Property Address}. Auction dates can move quickly. I want to make sure you have options. My number is right here." +15 d: "{First Name}, quick check-in on {Property Address}. If the timeline has changed, I can still help. Just reply." +15 d: "Last follow-up on {Property Address}, {First Name}. The offer stands if you need it." Wire: NI trigger + condition foreclosure/pre-foreclosure tags.
  5. **Warm Lead Nurture (NON-STANDARD, caution), 2 SMS + 1 email + 1 task:** only if never hiring another LM; Warm sequence must Remove from Drip + no conflicting task loop. Immediate SMS: "Hi {First Name}, this is {Agent Name}. Following up on our conversation about {Property Address}. Take your time. When you're ready, I'm here." +30 d email subject "Still thinking about {Property Address}?" (brief personal check-in, reference prior conversation, no hard sell). +30 d SMS: "Hey {First Name}, circling back on {Property Address}. Anything change on your end?" +30 d task: "Call {First Name}. Warm lead 90-day re-qualification for {Property Address}." Trigger: status -> Warm.
  6. **Speed-to-Lead Supplement (NON-STANDARD, caution), 2 SMS + 1 task:** New Lead needs same-day manual call; New Lead sequence must Remove from Drip and not collide with New Lead task loop. Immediate: "Hi {First Name}, this is {Agent Name} with {Company}. Reaching out about {Property Address}. Would love to connect. What's a good time to chat?" +24 h: "Hey {First Name}, following up on my message about {Property Address}. I have some options that might interest you. Feel free to call or text back." +48 h task: "Call {First Name} re: {Property Address}. New lead, 2 texts sent, no response. Manual follow-up needed." Trigger: status -> New Lead.
- **Delay ladder:** minutes (same-session chains, e.g., SMS at 0, email at 30 min; max 2 steps; >3 messages in an hour feels aggressive); hours (same-day pairs; a 6-hour delay set at 5 PM holds to 8 AM); days/weeks (15/30/45/90). Cadence guide: Foreclosure 15, Probate 45, General 90, Dead 90.
- **Integrations:** smrtPhone recommended — Settings -> Integrations -> smrtPhone, paste API key, validate. Twilio/Plivo identical. Gmail — Settings -> Integrations -> Gmail -> "Connect with Google". Set account timezone under Settings before creating drips. Don't ignore A2P 10DLC (deliverability, number suspension).
- **Enrollment:** Manual — Records page filter -> select -> **Send to -> Drip Campaigns** -> pick campaign (e.g., 500 ghosted leads 6+ months old). Automatic — sequence with status/disposition trigger + Drip Campaign action ("Make Changes" -> add action). Example shown: status -> Cold Lead triggers "AQ-Cold Follow Up" drip. Mara Garcia workflow: call -> disposition NI -> sequence -> quarterly drip.
- **Monitoring:** View Details per campaign; statuses Active (waiting next delay), Completed, **Failed (missing primary phone or email)**, Removed (manually pulled). Campaign list shows duration, steps, runs, active, failed, removed.
- Issues: SMS not sending -> carrier; numbers missing -> refresh icon; wrong times -> Settings -> Profile timezone; high failure -> missing primary phone/email, review Failed weekly and fix the record.
- Folders: Create Folder; suggested "Not Interested", "Dead Leads", "Nurture", "New Leads". Deleting folder: choose delete folder only or drips inside; **deleted drips cannot be recovered**.
- Remove a record: campaign View Details -> three dots -> remove; or from the record's property page.
- **By phase:** P1: Template 1; add Template 2 once 50+ NI dispositions; skip 3-6. P2: Templates 1 + 2 wired to sequences; skip 5, 6. P3: 1 + 2 then 3 + 4; folders "Ghosting", "Not Interested", "Niche Cadences"; data manager maintains library, reviews Failed weekly. P4: Templates 3 or 4 first (niche), then 1 + 2; skip non-standard.

---

## 6. Lead Management & CRM Automation (lead-management)

- Rule: every lead must have a next step/action; lead without scheduled task is dying.
- Stats: 400% higher close rate calling within 1 minute (HBR; odds drop after 5 min); lead-to-deal 1:50 conservative, 1:10 top-tier; LM attempt target 50/day. Anecdote: $45K deal lost when LM on vacation 5 days -> built push-back system.
- **4 Pillars (Reason, Timeline, Condition, Price):**
  - Reason: Hot = clear distress (death, divorce, tax lien, code violation, foreclosure); Warm = vague ("thinking about selling", lifestyle); Cold = none/testing market.
  - Timeline: Hot <30 days/ASAP; Warm 1-3 months; Cold 3-12 months/"no rush".
  - Condition: Hot significant work (seller admits); Warm "could use some updates" (assume worse); Cold good, verified by photos.
  - Price vs Zestimate: Hot <=80%; Warm 80-100%; Cold >100%.
  - Temperature = frequency. Healthy pipeline ~75% Cold, 15% Warm, 5% Hot.
- **STABM** on every record opened and left: S Status (correct for pipeline? — stale "New Lead" keeps firing "Call New Lead" tasks); T Task (one task, right frequency; zero overdue EOD; if overwhelmed, hire); A Assignee (none or still account owner = invisible; route presets through User Round Robin); B Board (**update status and task before dragging a card** — moving triggers sequences; moving to "Hot Lead" without changing status keeps firing new-lead sequences); M Messages (read message board before calling).
- Four core systems (Events, Tasks, Sequences, Drips) as in §4. Use automation for the 90% long-tail, humans for the 10% ready to deal.
- **Statuses by phase:** Phase 1 New Leads: New Lead, No Contact New Lead, Nurture. Phase 2 Qualified: Hot Lead, Warm Lead, Cold Lead, Ghosting. Phase 3 Disposition: Dead Lead, Not Interested, Under Contract, Closed.
- **Default automations table:** New Lead -> "Call New Lead" task, immediate; No Contact -> daily task, 3-5 days; Nurture -> weekly, 3-6 months; Hot -> notify closer + 2-day task, every 2 days; Warm -> 15-day task; Cold -> 45-day task; Not Interested -> change to Cold, schedule 45-day task, "Quarterly".
- **Push-back loop:** ghosted closer lead returns to LM with **temperature tag preserved**; LM re-engages via no-contact cadence. Objections: Price (offer too low, shopping), Time (not ready), Third Party ("need to talk to my wife"), Trust (need proof).
- Ghosting Nurture (this page's copy): Cycle 1 "Hi {First Name}, this is {Agent Name} with {Company}. Still thinking about {Property Address}? Happy to chat if anything has changed. No pressure." Cycle 2 (+45 d) "Hey {First Name}, {Agent Name} here. Your property at {Property Address} is still on my radar. Text or call me if you want to talk." Sequence: moved to Ghosting on SiftLine -> enroll; **no active call task, drip carries follow-up**. TCA: Trigger moved to Ghosting -> Condition record has a correct number -> Action enroll in Ghosting drip. Re-entry sequence must Remove from Drip ("fastest way to look like a spammer").
- NI campaign: disposition "Not Interested" -> wait 14 days -> 90-day cycle x4 (SMS + manual call task); auction dates compress cadence; on response move back usually at **Warm**, receiving sequence removes from drip. Don't delete NI leads.
- **Lead Manager:** $1K-1.5K/month; 150 dials/day floor; 1st critical hire. Schedule: 9:00 STABM; 9:15 new leads first (call within 5 min of arrival); 10:00 follow-ups hot->warm->cold; 12:00 lunch; 1:00 follow-ups, push qualified to closer; 3:00 nurture calls (weekly, 3-6 month pipeline); 4:30 update statuses, set tomorrow's tasks, clear board. Benchmarks as in §3.
- **Closer:** $2K base + 3-10% commission; 5-10 offers/day; 2:1 LM-to-closer. Schedule: 9:00 review hot leads pushed overnight, prep offers; 9:30 present offers, handle objections; 11:00 follow up pending offers, push-back ghosts to LM; 12:00 lunch; 1:00 contract management/title; 3:00 buyer outreach; 4:30 update pipeline, push back ghosts. **Hire a closer when 2 LMs consistently overflow**; if LMs aren't pushing 2-5 qualified/day each, bottleneck is qualification.
- **Pipeline setup (6 steps):** 1 SiftLine boards (default build: 5 boards, 10 statuses, 26-27 sequences — don't rename/delete/rewire until you can run the default); 2 sequences (Ghosting Nurture, Not Interested, Hot Lead alert); 3 task presets (LM, Acq, Transactions); 4 task assignment (role or round robin); 5 filter presets (My Tasks, Lead Management, Acquisitions); 6 lists & tags (tags: Do Not Market, phone quality, temperature).
- Do: STABM before first call; next action on every lead; consistent temperature tags; push ghosts back to LM; pair automation with manual. Don't: closers re-qualifying ghosts; skip nurture (weekly 3-6 months not optional); delete cold leads; replace calls with drips; rely on memory.
- **Cadence reference:** No Contact daily, 3-5 days, LM; Nurture weekly, 3-6 months, LM; Hot every 2 days until closed/ghosted, **Closer**; Warm every 15 days until qualified/cold, LM; Cold every 45 days until warm or dead, LM; Ghosting quarterly, ongoing, Marketing (drip); Not Interested quarterly, ongoing, Marketing (drip).
- If cold leads sell to competitors, tighten cold cadence 45 -> 30, in 15-day increments.

---

## 7. The Intake Call: 75 Custom Fields (lead-intake-custom-fields)

- Fields = the offer; single source of truth for Acquisitions, underwriting, closer. Fill during/right after call in seller's exact words. Anecdote: roof "Unknown" + skipped back taxes -> closed $18K below what should have been paid.
- **7 folders, 75 fields, in call order.** Folders 3 (CapEx) and 4 (Debts) are the MAO drivers (red).
- **Every "Unknown" forces a Full-Gut / full-replacement assumption** that lowers the offer; three Unknowns can lose the deal.

**Folder 1 — Qualifying Questions (8):** Seller Timeline (Dropdown); Reason for Selling (Dropdown); Asking Price (Number, raw, no formatting); Is Price Flexible? (Dropdown); Other Offers Received? (Dropdown); Other Offer Amount (Number); Previously Listed with Agent? (Dropdown); What Happens After They Sell? (Dropdown). Sets temperature (ASAP pre-foreclosure + flexible = hot; "just exploring" + firm Zillow number = cold).

**Folder 2 — Property Condition - General (10):** Overall Condition (Dropdown); Interior Condition Notes (Multi line: flooring, kitchen, baths, paint, fixtures, smell); Exterior Condition Notes (Multi line: roof visible issues, paint, landscaping, foundation cracks, windows, siding); Vacancy Status (Dropdown); If Tenant - Lease Status (Dropdown); Junk / Debris Present? (Dropdown); Soft Floors / Water Damage? (Dropdown); Prior Flooding or Mold? (Dropdown); Year of Last Major Update (Single line, e.g., "kitchen done in 2018"); Photos Obtained? (Dropdown).

**Folder 3 — CapEx Assessment (17):**
- Roof Condition (Dropdown: Insurable / Needs Replacement / Recently Replaced / Unknown); Roof Age (Years) (Number); Roof Notes (Single line: material, damage, exact words). "Insurable" = passes carrier inspection. Unknown -> full tear-off (five figures).
- HVAC Condition (Working / Insurable / Needs Replacement / Unknown); HVAC Age (Years) (Number); HVAC Notes (brand, heats/cools, issues). Unknown -> full system swap.
- Water Heater Condition (Working / Needs Replacement / Recently Replaced / Unknown); Water Heater Age (Years); Water Heater Notes (gas vs electric, leaks, tankless vs tank). Unknown -> replacement.
- Plumbing Condition (No Known Issues / Known Issues / Needs Full Replacement / Unknown); Plumbing Type (Copper / PVC / Galvanized / Unknown; galvanized + cast-iron drains = red flags); Plumbing Notes. Unknown -> full re-pipe.
- Electrical Condition (No Known Issues / Known Issues / Needs Panel Upgrade / Unknown); Panel Type (Breaker panel / Fuse box / Updated recently / Unknown; fuse box almost always = panel upgrade); Electrical Notes (knob-and-tube, aluminum wiring). Unknown -> panel upgrade + rewiring allowance.
- Junk Removal Needed? (Yes - significant (est. $3,000-$5,000+) / Yes - minor (est. $1,500) / No / Unknown); Junk Removal Notes (furniture, personal property, debris, vehicles). Unknown -> conservative high-end estimate.

**Folder 4 — Property Debts & Encumbrances (17), each debt off MAO dollar-for-dollar:** Mortgage Balance (Number); Mortgage Status (Dropdown); Back Taxes Owed? (Dropdown); Back Taxes Amount (Number); HOA? (Dropdown); HOA Monthly Amount (Number); HOA Arrears? (Dropdown); HOA Arrears Amount (Number); Code Violations or Liens? (Dropdown); Code Violation Notes (Single line: what, cost to cure); Open or Unpermitted Work? (Dropdown); Unpermitted Work Notes (Single line); Probate / Estate Situation? (Dropdown); Probate Notes (Single line: attorney? heirs? court timeline?); Foreclosure Filed? (Dropdown); Foreclosure Sale Date (Date); Encumbrance Notes (Multi line). Example: $40K mortgage + $6K back taxes + $2K HOA arrears = $48K off the top.

**Folder 5 — Title & Ownership (8):** Is Seller the Owner of Record? (Dropdown); Other Owners or Heirs? (Dropdown); Other Owners / Heirs Names (Single line); All Parties Willing to Sell? (Dropdown); Attorney or Executor Involved? (Dropdown); Attorney / Executor Name & Contact (Single line); Property in Trust? (Dropdown); Trust Notes (Single line: trust name, trustee).

**Folder 6 — Deal Intelligence (7):** Seller's Price Expectation Source (Dropdown; Zillow guess = soft, agent appraisal = firmer); Seller Open to Below-Market Offer? (Dropdown); Best Price Seller Would Accept (Number); Preferred Closing Timeline (Dropdown); Cash or Finance Preference? (Dropdown); How Did They Hear About Us? (Dropdown, lead source attribution); Deal Intelligence Notes (Multi line: emotional state, what they care about, what makes them say yes).

**Folder 7 — Appointment & Next Steps (8):** Photos on File? (Dropdown); Field Visit Scheduled? (Dropdown); Field Visit Date (Date); Who Will Be Present? (Single line: seller, tenant, TC, vacant); Property Access (Dropdown); Lockbox / Key Available? (Dropdown); Lockbox Code (Single line); Field Visit Notes (Multi line). Appointment dates/times live in Tasks.

- (Page lists dropdown option values only for the CapEx fields; others' options are not given.)
- **MAO:** ARV − target profit − holding costs − rehab (Folder 3) − every debt (Folder 4) = MAO; Folder 1 tells how far below MAO the seller will go.
- Order: qualify -> condition -> money -> confirm signer -> visit; slow down on Folders 3 and 4.
- **Auto-create:** DataSift Lead Management plugin (Claude Cowork, .plugin on Google Drive) POSTs the spec to **apiv2.reisift.io**, builds 7 groups + 75 fields in ~60 s; also generates branded LM training manual + daily KPI (Slack) bot. Steps: install plugin -> "set up my training" -> "build my custom fields" -> verify in **Settings > Custom Fields > Fields** (7 folders in order, correct counts).
- **API field-type quirk:** `text` creates a MULTI-line field; `text_input` creates a SINGLE-line field.
