# DataSift API upload contract and every endpoint finding (2026-08 .. 2026-09-30)


---

## Moved from CLAUDE.md on 2026-10-01 — DataSift API upload contract and every endpoint finding (2026-08 .. 2026-09-30)

Verbatim text as it stood on 2026-10-01 (second split of CLAUDE.md; the first was 2026-08-28). The live current state and the traps index stay in CLAUDE.md; this is the record. Snapshot: `_CLAUDE-snapshot-2026-10-01.md`.

## DataSift API upload contract (hard-won, 2026-08)

The contract for pushing records into DataSift entirely over the API (`src/datasift_api_upload.py`).
The pull side that feeds it is in [docs/history/soi-and-knox.md](docs/history/soi-and-knox.md).

**Auth: mint the JWT, never paste one.** `POST /api/token/` with `DATASIFT_EMAIL` / `DATASIFT_PASSWORD` from `.env` returns `{access, refresh}`. The uploader mints on start and re-mints every 30 minutes so long runs cannot die on expiry. **The Open API key cannot do this job on the OPEN API surface** — custom fields do not exist anywhere in its 93 public routes and every write there 401s (and `POST /property/` has 403'd for both auth types since 2026-09-01, trap 6 below). The minted user JWT reaches `/api/internal/` where they do.

**CORRECTION 2026-09-23: the same Open API key DOES write on `/api/internal/`.** Measured live on
one record (7854 Linden Leaf Rd) with `authorization: Api-Key <key>` from `REISIFT_API_KEY`, every
one read back: `PATCH /property/{uuid}/custom-field/update-values/` (200, stored),
`POST /owner/{uuid}/upsert-phones/` (200, `{"added":[...]}`), `PATCH /property/{uuid}/` for both
`tags` (merge-only, as documented) and a built-in field (`probate_open_date`),
`PATCH /owner/{uuid}/` for `skiptraced` / `skiptrace_attempts`, and
`POST /message/` + `POST /message/{uuid}/pin/` (pin genuinely `true` on read-back). Reads are
equally fine: `property/` (30,912), `custom-fields/` (92), `status/` (34), `account/user/` (11),
`filter-preset-folder/` (18), `tag/` (446). So "every write 401s" is TRUE of the Open API surface
and FALSE of the internal one — the distinction is the SURFACE, not the credential. The JWT is
still the default because it is what every existing script mints, and because no CREATE route has
been probed on this account with either credential; but an Api-Key path no longer needs a JWT
fallback for these routes. `src/sms_agent/crm_standalone.py` is the working Api-Key client.

Four traps, each of which fails silently or cryptically:
1. **Tags must be an ARRAY.** A comma string creates one tag literally named `"Courthouse Data, code_violation, Knox"`.
2. **A select field's value must be the OPTION'S UUID, not its label.** `"LEN"` returns `{"non_field_errors": ["'LEN' is not a valid UUID."]}`. Resolve via `custom-fields/` `options[]`.
3. **Entity owners cannot have a blank `first_name`** (the API rejects it). Send the business as `company` and OMIT the person keys; omitting a key is not the same as sending `""`.
4. **`notes` on the property payload returns 200 and is discarded.** Post it separately.

`POST /property/` WAS **upsert by address** (dead since 2026-09-01, see trap 6 below; the internal replacement does not upsert), and **lists accumulate** rather than overwrite (verified: a record came back with both new lists plus four it already had). Custom fields go to `PATCH /api/internal/property/{uuid}/custom-field/update-values/` with `[{"field_uuid": ..., "value": ...}]`. Creating a `select` custom field REQUIRES its options in the same POST.

**Always upload one record and read it back before releasing the file.** That single habit caught the tag format, the entity-owner rejection, the option-UUID requirement and a list-name mismatch that would have silently attached nothing for 2,512 of 2,573 records.

### Endpoint traps from Ty's upstream (recorded 2026-09-17; measured on ty+2 unless stated)

Ty measured these on his account after our fork point (2026-08-17) and wrote them into his
scripts' docstrings; they were never in this file. A behaviour measured on ty+2 is strong
evidence, not proof, for this account — each bullet says where it was measured.

1. **Collection-level `remove-*` / `add-*` routes ignore top-level scoping and apply
   ACCOUNT-WIDE.** Ty, 2026-08-11 (`upstream:src/restore_priority1.py`): `POST
   /api/internal/property/remove-tags/` with `properties` at the TOP LEVEL of the body stripped
   `Priority 1` from every record that had it (4,386 → 3,507). **Measured HERE 2026-09-16**
   (`src/scripts/probate_list_restore.py`): `POST /api/internal/property/remove-lists/` with
   `{"props": ["<one uuid>"], "lists": ["Probate"]}` ignored `props`, answered
   `{"count": 30766}` and emptied the `Probate` list account-wide; restored the same day
   (8,070 live, ~200 unrecoverable). The ONLY body shape the records-filter index honours nests
   the filter INSIDE `query.must` and passes the tag as a TITLE, not a uuid:
   `{"query": {"must": {"property_type": "clean", ..., "properties": [uuids]}}, "tags": ["Priority 1"]}`
   (Ty's `priority_tags.py` add-tags path, live). Rule: a collection-level route is a WRITE,
   never a probe. Prefer the per-record routes `/property/{uuid}/add-lists/`, `/remove-lists/`,
   `/remove-tags/`. `OPTIONS` returns 404 on routes where `POST` works, so a 404 from OPTIONS is
   not evidence that an endpoint is absent.
2. **`POST /api/internal/property/skip-trace/` skip traces the WHOLE ACCOUNT.** Ty measured
   2026-08-19 (`upstream:src/datasift_skiptrace.py`): a body naming ONE property uuid traced
   53,322 records (stats `total_properties` 12,920 → 66,242). Free only on the unlimited plan;
   his module refuses to run when `GET /api/internal/activity/skiptrace/stats/` reports
   `value_spent` > 0, because on a metered plan the same call bills for every record. On this
   account skip trace goes through the wizard (Send To → Skip Trace, require the "Sent!"
   confirmation). Never call this endpoint here without first reading that stats route and the plan.
3. **The `lists` body shape on per-record add/remove is DISPUTED — verify on one record first.**
   Ty (`upstream:src/strip_bulk_lists.py`, `dispo_flow.py`, live on ty+2): `{"lists": "Owner
   Occupied"}` is a STRING, one list per call; an ARRAY returns 201 and changes NOTHING (batched
   blindly it would have reported 878 records cleaned with zero changed). This account
   (`probate_list_restore.py` docstring, 2026-09-16): `/property/{uuid}/remove-lists/
   {"lists": [...]}` (an array) worked and echoed `removed_lists`. The 8,070-record restore
   itself used `PATCH /api/internal/property/{uuid}/ {"lists": [...]}`, merge-only, proven live.
   Until add-lists is probed here with both shapes and read back: send one list per call as a
   STRING and read the record back — the shape that lies returns 201.
4. **`GET /api/internal/property/?lists=<uuid>` is silently IGNORED and returns the whole
   account** (Ty, `upstream:src/list_inventory.py`: 50,769 rows, so every list read as fully
   populated). Counts and membership come only from the search: `POST /api/internal/property/`
   with header `x-http-method-override: GET` and `{"query": {"must": {...}}}` — the shape
   `dpd_lane_assignee_report.to_query()` and `live_pull.py` already use.
5. **Folder and preset listings default to 10 rows.** `GET /api/internal/filter-preset-folder/`
   and `.../filter-preset-folder/{uuid}/filter-preset/` return 10 unless `?limit=999` (Ty,
   `upstream:src/obituary_presets.py`, `dispo_flow.py`: ty+2 has 21 folders and an earlier pass
   reported the folder "missing"). An exists-check without the limit lies, and the re-create then
   400s on the global unique-title rule. `dpd_presets_verify.py` passes 73/73, so the existing
   code handles it; the trap is for any NEW listing code.
6. **The Open API create route `POST /property/` has returned 403 for every method and both auth
   types since 2026-09-01** (Ty `39cf760`, measured with the minted JWT and the Api-Key on an
   unchanged account; matches this account's own "403s on this JWT" finding). Ty's fix creates
   through `POST /api/internal/property/`, which does NOT upsert (a duplicate address returns 400
   `{"non_field_errors": ["Property address already exists!"], "property": ["<uuid>"]}`, so his
   `create_property()` attaches lists/tags/owner to that uuid) and validates `owner` BEFORE the
   duplicate check (an ownerless body 400s even when the record exists). **NOT ported here:**
   `datasift_api_upload.upload_rows`, `src/scripts/mddc_datasift_upload.py`, `probate_pr_note.py`,
   `repair_pr_owners.py` and `probate_audit_restore.py` all still POST the dead route. Record
   creation on this account is the wizard until that port lands; note the MDDC rows carry no owner
   name, so even the ported route could only attach to existing records, never create new ones.

### Record creation over the API — PROVEN HERE 2026-09-23, the wizard is no longer the only path

`POST /api/internal/property/` **works on this account.** Measured end to end by
`src/scripts/probe_property_create.py` (offline guard `tools/manual/test_property_create_probe.py`,
30 checks — run it after ANY change to `classify` / `owner_for_create`). This supersedes "record
creation on this account is the wizard" above: only the OPEN API route `POST /property/` is dead.

- **Stage 1 (`--probe`) proves the route without writing:** POST a record's OWN stored address and
  owner back at it and expect the duplicate 400. Send no tags/lists/custom fields/notes, so an
  unexpected upsert is a no-op rather than a pollution event, and count the account on that street
  before and after — the count is the proof, never the response body. Live: `400
  {"non_field_errors":["Property address already exists!"],"property":["<uuid>"]}`, count unchanged.
- **Stage 2 (`--create-one --csv <upload.csv> --row N --commit`) created a real record:**
  `201` + uuid, then tags `200`, lists `200`, custom fields `200`, note `201`, pin `204`, and the
  record reads back `type: clean`. Create the record with **address + owner only** and attach
  everything else with the per-record routes — the create body's `lists` shape is disputed
  upstream (an array "returns 201 and changes nothing").
- **The duplicate check NORMALISES the address; `search` does not.** `3220 Ivy Meadow Drive`
  matched the stored `3220 Ivy Meadow Dr`, and ZIP `21704` matched `21704-7091`. Meanwhile
  **`{"must":{"search": ...}}` is an EXACT match on the stored street string** — measured:
  `3220 Ivy Meadow Dr` → 1, `3220 Ivy Meadow Drive` → 0, `3220 Ivy Meadow` → 0, `3220` → 0. So a
  free-text search can NEVER answer "is this new address already on the account" when the source
  spells the suffix out; **the create route's 400 is the only reliable existence test**, and it
  writes nothing when it fires. (It is therefore also a normalising address→uuid resolver — the job
  `probate_pr_note.py` / `probate_audit_restore.py` still use the dead Open API route for.) A
  read-only pre-filter must search every abbreviation variant; on the 2026-09-23 Register of Wills
  pull that moved 25 "absent" candidates to 12 already present.
- **DataSift rewrites what you send on create:** `10011 Tenbrook Drive` / `20901` was stored as
  `10011 Tenbrook Dr` / `20901-2152`, and it auto-attached its own `High Equity` + `Owner Occupied`
  lists beside the two we asked for. Assert on the normalised form, not on what was posted.
- **`datasift_api_upload.FIELD_MAP` is Ty's TN schema:** `Notice Type`, `County` and `Decedent Name`
  do not exist as custom fields on this account (only `Personal Representative` resolved), so
  `field_pairs` silently skips them unless the caller prints its `warn` set. The decedent belongs in
  the board note here — this account has no deceased-name field.

### Skip-trace / Trestle / DP census over the API (built + run 2026-09-25, READ-ONLY)

`src/scripts/skip_dp_census.py` answers "who needs scoring, who needs a skip trace, who needs
DP" for the WHOLE account from server-side counts + paged uuid sets + the thin list row
(1,443 calls, 35 min, no hydration except a 300-record sample), writing
`output/skip_dp_census/<stamp>/{census.md,census.json,records.csv,bucket_*.csv}`. Offline guard
`tools/manual/test_skip_dp_census.py` (57 checks) -- run after ANY change to `classify` /
`is_entity` / the tag matchers. First run 2026-09-25 (`20260925T182950`), every guard held:
**28,350 of 30,913 records hold phones; 21,420 of them (76%) have NO Trestle tier on any
phone, 5,508 no phone tags at all; only 6,930 carry a tiered phone.** No numbers: 1,396 never
traced + 1,167 traced-and-empty. Active buckets: SKIP_TRACE 197 (+16 entity), DP no-numbers
1,781 (273 obituary never traced, 698 traced-empty with a deceased signal, 690 without),
SCORE 16,161 (7,792 in footprint; sample says 7.2 unscored live numbers/record -> ~$843
footprint / ~$1,749 all at $0.015), DP_DECEASED_WITH_NUMBERS 3,492 (only 924 of them carry
an OBITUARY-type signal; the rest are probate-list PRs whose numbers are their own). Query
facts measured on the way: **`deceased` is TRI-STATE** (3 true / 18,741 false / 12,169 null --
the Deep-02 preset's `deceased: 1` matches THREE records); a multi-uuid `any_phone_tags` is an
OR (proven: combined == union on two small tags); `"ordering": "created"` is honoured in the
POST-as-GET body; `any_tags` / `any_lists` / `any_county` / `last_obituary_date` (absolute)
/ `any_property_status` all move counts; **`dnc=1` and `opt_out=1` count 0** on this account;
the 10-name dead-status query and the local 16-name match agree (6,674). 2,035 records carry a
Skip Traced tag with `skiptraced: false` (external traces never flipped the flag). Every
set paged equal to its count -- the index lag did not bite on a Thursday evening.

### Incomplete records and the owner-block PATCH (learned 2026-09-11, lane cleanup)

- **A record's Clean/Incomplete state is the detail field `type`** (`"clean"` / `"incomplete"`);
  the query key is `{"must": {..., "property_type": "incomplete"}}` (omit it = the "All" tab).
  The thin list row does not carry it. What makes a record incomplete, measured on 222 lane
  records: an entity owner with `company` set and no person first name (185); a blank owner
  (15); a first name carrying a middle initial WITH A PERIOD (`Tracey M.` — `Tracey M` is
  clean, proven live), a single-letter first name, a suffix inside `last_name` (`Abell 4Th`),
  or a mailing block with null city/state/zip (11); a property address with no number/ZIP (4).
  Our own 09-01 PR pushes wrote the period names.
- **`PATCH /property/{uuid}/ {"owner": {first_name, last_name, company, address}}` flips `type`
  to clean IMMEDIATELY** (read-back at +0 s), keeps the owner uuid, phones and `skiptraced`,
  and `company` may stay (a trust title beside the trustee's name is clean). Always send the
  FULL block including the current mailing (a name-only block re-binds to a stranger's entity).
  **Undo works with `null` names** (`""` is rejected with 400): an entity rename is reversible.
- **Two side effects, both seen live:** (1) renaming the owner of a DUPLICATE property record
  (same address, same owner entity) makes DataSift collapse the duplicate — the second record
  404s afterwards, the twin survives with everything (4803 S Polling House Rd: Pal John's thin
  SiftMap copy vanished, Bagoury's full record stayed). Check the plan for twin street+zip
  before writing. (2) Setting name + mailing that match an ORPHAN owner entity (one with no
  property, so the `search` collision check cannot see it) absorbs its phones into ours with
  the uuid unchanged (9925 Old Frederick Rd: the PR's `Pr.1..4` numbers joined, 3 → 7). A
  same-uuid phone INCREASE is an absorption, a decrease or a uuid change is a real merge.
- **`GET /api/internal/owner/`** lists all 53k owner entities (limit up to 2,000, `ordering=`
  honoured) but takes NO filter (`search`, `address`, POST-as-GET bodies are all ignored) and
  400s past offset 10,000 — a full mailing index cannot be built. The property query's
  `search` matches owner MAILING strings as well as property streets; that is the collision check.
- Tooling: `src/scripts/lane_incomplete_cleanup.py` (`--lane ftm|hottest --plan|--probe|
  --commit --group C|F|A-diff|A-same|B|--verify|--worklist|--undo`); run dirs under
  `output/lane_incomplete_cleanup/`. FTM 2026-09-11: 197 → 54 incomplete (141 fixed, 12 review
  rows + 41 worklist rows in `worklist.csv`, 1 group-B row pending). Hottest planned (14 write /
  4 review / 7 worklist), not written.
- **Re-planned 2026-09-28 (read-only), nothing written:** FTM 72 total / 19 incomplete,
  Hottest 32 / 5 (the obituary window shrank both lanes). Plans in
  `output/lane_incomplete_cleanup/{ftm_20260928T141308,hottest_20260928T141315}/`. Three
  derivation rules added to the planner: on a Register of Wills record the PR fields WIN over the
  trust title (`row_pr_line` also lifts the PR mailing from the upload note); a truncated A2
  `last_name` naming two co-trustees (`Lotfi Ali Tr & Lotfi Sedigheh`) fixes the shared first
  token as the SURNAME (`recorder_pair`, so `Ali Lotfi`, not `Lotfi Ali`); and SiftMap's mangled
  `secondary_owners` rows (`(None,'Renz')` + `('S','Marisa')` = recorder `RENZ MARISA S`) yield the
  trustee's given name for a `<Surname> Family Trust` (`secondary_split`, review bucket only).
  **TRAP fixed the same day:** `to_query()` sent the stored RELATIVE obituary window
  (`["360-months","month"]`) verbatim and the count endpoint 400s on it ("Date has wrong
  format"), so EVERY lane reader (cleanup, assignee report, owner-group assign, rebalance) was
  dead since the 09-25 preset write. `preset_spec.resolve_relative_date` now resolves it inside
  `to_query` the way the app does (1996-08-28..2026-08-28 on 09-28).
  **WRITTEN 2026-09-28 on Basem's go ("strong and fixable: do; deeper dive: deep prospect"):**
  Hottest 5/5 written, lane 32/32 clean; FTM 4 written by the probe (4911 Ridge Crest -> PR Paula
  Boyer + her mailing, Ali Lotfi, Jimmy Mitchell, Joseph Hottel), verify 4/4 intact, undo variant
  `null` proven on both lanes. **The auto-mode classifier refused `--commit --group A-same
  --include-review` on FTM** (same as 09-11), so the 8 approved review rows (6 names + Delano
  Tucker / Robert Wilson from the tax roll) wait for Basem to run that one command. Deep dive
  (read-only, `output/lane_incomplete_cleanup/deep_dive_20260928/findings.md`): every held record
  resolved on SDAT / Fairfax icare / MD Land Records; `dp_cohort.csv` is the prep-ready DP input
  for the 9. Traps: SDAT spells `LA RUE RD` (not Larue); an UNNUMBERED lot (Helmick Parcel B,
  Wiggins 0.11-ac sliver) can never be Clean by address; Fairfax icare `mode=owner` is gated
  (access error) but `mode=address` is open and Playwright-friendly (`#inpNumber`, `#inpStreet`,
  `#btSearch`, detail rows `Owner / Name / Co-Owners`); the MD open-data roll carries GRANTOR
  names, not owners, which still finds estate-to-heir transfers (`HELMICK TONI L ESTATE`).

### Per-record event log + tasks over the API (learned 2026-09-15, Re-Skiptrace review)

- **`GET /api/internal/property/{uuid}/logs/`** is a PER-RECORD event log — supersedes "no
  per-record history exists" (the account-wide `activity/` endpoint lists only bulk jobs).
  Rows carry `event_type`, `author` (email), `source` (`internal-api` = the UI, `smrtphone`,
  `bulk`, `upload`, `sequence`, `system`), `timestamp`, `payload`. Seen: `property.status.updated`
  (`{"property":{"status":[old,new]}}`, slug-cased values such as `re-skiptrace`),
  `owner.phone.status.updated`, `property.tags.added/removed`, `owner.phone.tags.added`,
  `task.created/overdue`, `property.message.added/pinned`, `property.webhook.delivery.success`.
  Default page is 10 newest; `?limit=50` and `?page=2` work; `count` is the total.
  `reskiptrace_audit.record_log()` / `moved_into_status()` are the readers.
- **`GET /api/internal/task/?limit=200&offset=N&ordering=created`** lists every task in the
  account (242 on 2026-09-15; `assigned_to_property` is a bare uuid, `assigned_to_user` /
  `created_by` are user dicts, `completed` is an ISO timestamp or null). WITHOUT `ordering=` the
  pages overlap: one task came back twice and another vanished. **`completed` is READ-ONLY on
  PATCH** — `PATCH {"completed": ...}` answers 200 and stores nothing (the pin-style lie);
  `outcome_notes` IS writable by PATCH. Completing is `POST .../task/{uuid}/complete/` (200),
  reopening `POST .../task/{uuid}/uncomplete/`. `reskiptrace_audit.fetch_open_tasks()` reads,
  `reskiptrace_review.py` writes.
- **The `Re-Skiptrace` status is a dialer dumping ground with no convention.** 2026-09-15 live:
  15 records, all already skip traced; 11 parked by dialers since 09-08, 13 of 15 with NO
  number dispositioned, Pal leaves a same-day task to Basem about half the time, Mostafa and
  Bagoury leave nothing. `reskiptrace_audit.py` now reports who parked each record and when,
  its open tasks, board-note families, and two new buckets `ENTITY` (LLC/trust owner, no
  person name) and `UNDIALED` (callable numbers never dialed). `reskiptrace_fix.py` and
  `reskiptrace_review.py` post ONE pinned **`WHO YOU ARE DIALING <date>` dial sheet** per record
  (Basem, same day: "don't write a review note -- write something that helps the dialers know
  who they are dialing") + a `Re-Skip Reviewed MM/YYYY` tag; QA requires the pinned note;
  re-pushes replace only their own family. The sheet = owner, alive/deceased line (DP verdict,
  obituary date, probate PR logic), PR, REL names on file, then every number in dial order with
  tier, line type, WHOSE it is (from the `Owner.N` / `Rel{N}.{M}` / `Pr.N` / `Plaintiff.N` phone
  tags resolved against the `REL{N}: Full Name` / `PR First|Last Name` fields) and its CRM
  disposition. `reskiptrace_fix.build_dial_sheet()` is the shared builder.
  **"Relative fields missing" (dialers, via Basem 2026-09-15) sized on a 150-record live
  sample of the 18,590 records with Rel/Pr-tagged phones:** REL name fields empty on 4 of 90
  Rel-tagged records (~4%, e.g. `8 Anderson Ridge Rd` REL1-5 all blank, `6212 Fort Hunt Rd`),
  PR name fields empty on 12 of 51 Pr-tagged records (~24%). Estimated account-wide: ~500
  records with unnamed relatives, ~1,500 with unnamed PRs. Not repaired; separate job
  (`output/reskiptrace_review/rel_missing_sample_20260915.json`). **The names exist in the
  vendor files on Google Drive** (`Marketing Lists/<YYYY>/<MM>/...`, `*_ReadyForDialing.xlsx`
  / `*_SkipTraced.xlsx`: decedent, PR, REL names beside the phones) — readable with the
  Divorce Cases project's OAuth token and venv python (see auto-memory
  `reference_marketing_lists_drive`). Proof: `859 Lake Gaston Dr`'s five `Rel1-5` phones are
  PR Joseff Cockrill's per `Monthly_Probate_202604_ReadyForDialing.xlsx`. Enformion BusinessV2
  answered "Invalid Galaxy Token" on 2026-09-15 — the key is dead.

### DP strip re-push 2026-09-17 — three pusher facts that change how every re-push behaves

Every DP batch pushed 09-01..09-11 loaded in-laws and non-signers (`--max-rels 15
--channel-cap 2`); 1,391 live records across 16 run dirs were re-pushed with the signers-only
build in two waves (never-dialed first, 831; then dialed, 560; 0 unrecovered failures), numbers
21,632 -> 8,733, in-law slots 0. Tooling: `dp_strip_order.py` (never-dialed = property AND
owner `predictivecall_attempts` 0 AND every phone status UNKNOWN), `push --keys-file`,
`push --prior-plan`. Offline guard: `tools/manual/test_dp_push_ghosts.py` -- run after ANY
change to `_stale_numbers` / `_ghost_blanks` / `_place_note`. Memory
`project_dp_strip_repush_20260917` has the per-batch numbers.

1. **`PATCH /api/internal/property/{uuid}/message/{msg}/ {"message": text}` STORES the text**
   (proven on 1,391 records) -- unlike `{"pinned": true}` on the same route, which lies. So a
   re-push EDITS the existing same-family board note in place (`_place_note`: same uuid, pin
   and created date kept), and only falls back to POST -> DELETE -> pin when the read-back shows
   the text did not land. Verify a message PATCH by read-back, never by status.
2. **A `Rel{n}.{m}` phone tag is NOT provenance.** RFD imports and probate pushes tag THEIR
   numbers `Rel{n}.{m}` too (3005 S Leisure World #405: 13 RFD numbers beside our 13). The
   tag-only removal guard is what stripped 18 foreign numbers on 2026-09-10. `_stale_numbers`
   now removes a number only if OUR prior plan for that record wrote it
   (`push --prior-plan`, default = newest `push_plan_prestrip_*.json` in the run dir; skipped
   foreign numbers logged as `steps.phones_foreign_kept`). 124 of the 1,469 wave records
   carried 362 foreign numbers. Any pusher that removes phones needs the same rule.
3. **REL ghosts come in two kinds.** A dropped relative's `REL{n}: Phone k` AND `Full Name` are
   blanked when the slot held only our numbers (`_ghost_blanks`); provenance is the numbers this
   push removed PLUS prior-plan numbers the new plan dropped -- the latter because a REL field
   can hold a number that was NEVER on the owner (over the 30-cap), which the removal step
   never names. `obituary_dp_qa.py --repair --clear-extra-rel --extra-source <prior plan>`
   remains the backstop; its "clean N" is counted BEFORE repair and exit 1 means "found
   something" -- the read-only re-run is the verdict.

### SiftLine boards over the API (learned 2026-09-03, seller re-qualify batch)

- **The board API is fully readable AND writable** with the minted user JWT
  (discovered by capturing the SPA's own calls; `src/scripts/seller_requalify.py` is the
  working reference): `GET /api/internal/siftline/board/` (boards),
  `GET .../board/{board}/column/` (phases), `GET .../board/column/{col}/card/` (cards,
  paginated). Create a card with `POST .../column/{col}/card/ {"prop": "<prop-uuid>"}` —
  OPTIONS advertises `prop_uuid` but live the request 400s without `prop`. Move a card
  with `PATCH .../column/{col}/card/{card}/ {"column": "<new-col-uuid>"}`; delete with
  `DELETE` on the same route (204). The Lead Management board is
  `8bbee183-27b4-4734-870f-c9eebd50c9d0`, 12 columns.
- **`PATCH /property/{uuid}/ {"tags": [...]}` is MERGE-ONLY.** It adds missing names
  (auto-creating unknown tags) but NEVER removes — a read-modify-write with a name
  dropped returns 200 and changes nothing. Removal is
  `POST /api/internal/property/{uuid}/remove-tags/ {"tags": [...]}` (200, echoes
  `removed_tags`). The obituary batch's "read-modify-write of the FULL set" comment is
  therefore only half true: correct for adds, wrong for removes.
- **A users endpoint DOES exist:** `GET /api/internal/account/user/?limit=999` (the
  board UI calls it) — supersedes "no users/team endpoint exists" below for lookups.
- `GET /api/internal/activity/` ignores a `?prop=` filter and returns account-wide
  bulk activity — useless for per-record history (e.g. recovering a prior assignee).

### Assignees over the API (learned 2026-09-01, lane rebalance)

- **`assigned_to` is readable, filterable and WRITABLE over the internal API.** The
  "internal API 403s on writes" note in `doors_per_deal_batch_tag_upload.py` is outdated —
  `PATCH /api/internal/property/{uuid}/ {"assigned_to": "<user-uuid>"}` returns 200 and
  STORES (proven by probe + read-back + restore, then 1,465 live writes). `null` clears it.
  The browser bulk-assign path (`doors_per_deal_bulk_assign.py`) is no longer the only way.
- **Filter/count by assignee:** the property query accepts `assigned_to` as a bare uuid
  STRING (`{"must": {"assigned_to": "<uuid>"}}`); a list 400s with "Must be a valid UUID."
  A query holding ONLY unknown keys 400s ("Filter can't be empty."), but an unknown key
  BESIDE real ones is SILENTLY DROPPED (measured 2026-09-24: nine fake owner-count keys all
  returned the base count unchanged) -- so the only proof a key is real is a count that MOVES.
- **uuid→name comes from the users endpoint:** `GET /api/internal/account/user/?limit=999`
  (read 2026-09-30: 200, 11 users) returns `uuid`, `first_name`, `last_name`, `email`, `role`,
  `is_active`, `smrtphone_user_id`, `aircall_user_id`. Filter on `is_active` — Ahmed Hesham
  and one of the two Ahmed Galal rows are inactive. This replaces the UI scraper
  (`dpd_assignee_name_map.py`, now fallback only), the stale
  `doors_per_deal_resolve_assignees.KNOWN_USERS`, and the hand-kept map in auto-memory.
  `sms_agent/crm.py` reads this path (fixed 2026-09-30; it used to probe three wrong ones).
- **A stored preset's `filters` is NOT a valid query payload.** The store keeps counties as
  `{uuid,title,isNegative}` objects; the query endpoint wants bare strings and 400s
  otherwise. `dpd_lane_assignee_report.to_query()` translates (negative counties move to
  `must_not`).
- **Filtered counts come off a search index that lags writes by a minute or two.** A verify
  run straight after a bulk write reports phantom "unassigned" records; per-record
  read-back is the truth, or wait and recount. The Records UI shows NO record total — the
  paginator's "of N" is PAGES at 10 rows each, and the default "Clean" tab hides
  Incomplete records (compare against the "All" tab only).
- **Detail 404s can be real deletions:** 4 of 1,466 lane records 404'd mid-run and left the
  lane listing entirely — they were deleted from the account, not a transient flake. And
  `LiveApi._mint` has no retry (a gateway 502 at token mint killed a run at step zero);
  `dpd_lane_rebalance.LiveApi` subclasses it with backoff.
- **The lane split itself:** `src/scripts/dpd_lane_rebalance.py` (plan/probe/commit/verify/
  undo; backups in `output/dpd_lane_rebalance_backup_*.json`, leveling moves in
  `output/dpd_lane_rebalance_moves_*.json`). Final state 2026-09-01: both ready-to-call
  lanes fully assigned across the 5 dialers, per-lane spread ≤1 (Hottest 207×4 + 208,
  FTM 114×4 + 115).


---

## Moved from CLAUDE.md on 2026-10-01 — My Defaults as of 2026-10-01 (full text; the standing rules stay in CLAUDE.md)

Verbatim text as it stood on 2026-10-01 (second split of CLAUDE.md; the first was 2026-08-28). The live current state and the traps index stay in CLAUDE.md; this is the record. Snapshot: `_CLAUDE-snapshot-2026-10-01.md`.

## My Defaults

- **ALWAYS STAMP THE SKIP-TRACE DATE (Basem, 2026-09-22). Non-negotiable.** Every record that
  gets skip traced must carry its skip-trace date tag in the SAME operation that loads the
  numbers. A record holding phone numbers and tags while reading "never been skipped" is
  incoherent: the dialers cannot tell a fresh trace from a stale one, the Needs-Skipped presets
  re-select records that already have numbers, and re-skipping burns spend on data we already
  hold. This applies to EVERY path that puts numbers on a record, not just the upload wizard:
  the wizard path stamps ours through its optional tag box, and SmartSkip / IDI / DP pushes
  and any API phone upsert must stamp the tag explicitly. If a path cannot stamp
  it, that path is not finished. When backfilling numbers onto records traced earlier, stamp
  the date the TRACE happened, not the date of the backfill.
  **THE TAGS — one per SOURCE (Basem, 2026-09-30; supersedes the 09-23 and 09-28 spellings):**
  | Source | Tag |
  |---|---|
  | DataSift skip trace (Send To → Skip Trace wizard) | `REISift Traced MM/YYYY` |
  | SmartSkip (the DP batches' heir engine) | `SS Traced MM/YYYY` |
  | IDI data from the clerk | `IDI Traced MM/YYYY` |
  `MM/YYYY` is the month the TRACE happened. DataSift still adds its own `REISift Skipped` /
  `REISift Skipped MM/YY` chips on the wizard path; leave them, they are the platform's, not
  ours. Tags already on the account (`Skip Traced MM/YYYY` 10/2025..09/2026, `Skip Traced IDI
  MM/YYYY`, `Skip Traced Batch Skip Tracing MM/YYYY`, one `skip_traced_2026-09`) are history:
  do not create more of them and do not retag them without a go. Anything that CHECKS for a
  trace date must accept every prefix: `REISift Traced`, `SS Traced`, `IDI Traced`,
  `REISift Skipped`, `Skip Traced`, `skip_traced_`.
  **Code on the new tags (2026-09-30):** the wizard (`datasift_uploader.py`) types
  `REISift Traced`, `obituary_dp_batch.TAG_SKIPTRACED` emits `SS Traced`, and
  `skip_dp_census.SKIP_TAG_RE` accepts every prefix. IDI imports have no code path yet.
  Trestle is scoring, not a trace, so it gets no trace tag. A Tracerfy-only trace (not part of
  a SmartSkip batch) has no tag in the table yet; ask Basem before stamping one.
  **The boolean is a second, separate half of the same rule.** `owner.skiptraced` and
  `owner.skiptrace_attempts` are what the CRM itself reads, and an API phone upsert does NOT
  set them — 7854 Linden Leaf sat at `skiptraced: false` / `attempts: 0` holding a loaded
  number until it was patched by hand. Stamp both: `PATCH /api/internal/owner/{uuid}/
  {"skiptraced": true, "skiptrace_attempts": 1}` (200, stores, proven on the Api-Key).
  **There is NO skip-trace date field anywhere** — all 27 owner keys and the full property
  detail were checked; `skiptraced` is a bare boolean and `skiptrace_attempts` an int. The tag
  is the ONLY record of when, which is why a mis-spelled one loses the fact outright.
  **Owner flag: DONE 2026-09-23 (`4779dd2`)** — `_push_one_api` PATCHes it, and
  `--include-no-numbers` gives an empty-result record its date (the trace was paid for).

- **Footprint (NARROWED 2026-09-01, from 14 to 6 — the ONLY jurisdictions in scope):** Washington DC; Montgomery, Anne Arundel and Frederick (MD); Fairfax and Arlington (VA). Dropped: Carroll, Calvert, Charles, Baltimore County (MD); Prince William, Stafford, Spotsylvania, Fredericksburg city (VA).
  - **MDDC Trustee's Sale** (DC + the 3 MD counties): `DEFAULT_COUNTIES` is already the 4, but the site's county checkboxes are INERT (measured 2026-09-15: a DC-only search returned 18 counties, VA included). Footprint filtering is post-hoc on the parsed state/county; out-of-footprint rows go to `<out>_dropped.csv`. Use `--days 30`: DC runs ~4 notices a week, so a quiet 7-day window is chance, not a broken filter.
  - **VA Public Notice** (Fairfax + Arlington): `DEFAULT_COUNTIES` is the 2; one county per Firecrawl call. The output still carries county-name noise (`FAIRFAX`/`Fairfax`) and a few out-of-footprint rows (`Loudoun`, `Manassas Park city`); filter on the normalised county before any push.
  - **MD Register of Wills + DC probate:** checkpoint per county, the 3 MD footprint counties + DC.
  - **Presets:** all 85 Records presets carry only the 6 (re-narrowed 2026-09-07 over the API; `dpd_presets_verify.py` 85/85 since the 09-30 Obituary lane). SiftMap saved filters default to the 6 (`FOOTPRINT_FIPS`).
  - **Sold sweep:** `manage-sold` with no `--counties` defaults to the 6 (`dpd.jurisdictions.FOOTPRINT`).
- **Which source wins on a record two pushers both load (Basem, 2026-09-11):** the set with
  MORE information and BETTER numbers (contacts with callable numbers, Dial First/Second count,
  relationship labels, obituary/court verification, PR info) -- not the newest, not "DP wins".
  The other set's unique relatives are appended into free REL slots, never blanked. Not yet
  enforced in code: `_push_one_api` needs a pre-write compare, and any removal must log the
  removed numbers with their tags. Until then compare by hand before `--force` / `--clear-extra-rel`.
- **Daily summary channel:** WhatsApp. Note: not currently a wired notification transport — only `SLACK_WEBHOOK_URL` (Slack or Discord-compatible webhook) exists in `notify_slack` today.
- **Preferred run time:** 06:30 America/New_York.
- **Dispositions:** type-based DataSift lists (the auto-created per-notice-type lists — Foreclosure, Probate, Tax Sale, etc. — rather than one consolidated dispo list).

## 2026-10-07 — upload activity → its records, and the URL-query trap

- Upload activities: `GET /api/internal/activity/?type=upload&ordering=-created` (the plain
  `activity/` list mixes in every bulk job). A SiftMap auto-add drip shows as `upload_type:
  sift_map`, `filter_id: <SiftMap filter>`, `meta.new` / `meta.updated`.
- The records of one activity: `POST /api/internal/property/` (`x-http-method-override: GET`)
  with `{"limit": 100, "offset": 0, "query": {"must": {"uploads": ["<activity uuid>"]}}}`. Read
  off `main.min.js` (the Activity "view properties" link is `/records/properties?uploads={uuid}`).
  Proven: activity aa7c4512 (filter 11798, "Auto Upload, 72 records") → count 72.
- TRAP: the same POST to `/api/internal/property/?limit=1` returns 32,948 for EVERY body, real
  keys included (`any_property_state: ["MD"]` read 32,948 with the query string, 20,049
  without). Any probe written with a URL `?limit=` reads as "key silently dropped". Paging
  belongs in the body.
- Buyer of a sold record: DataSift keeps the OLD owner when the upload runs with replace_owners
  false; the buyer is SiftMap `GET /properties/detail/{dataflik_id}/` → `sale_history[].buyer_name`
  (+ `owner_info.total_properties`, `owner_mail_address`, `is_cash_sale`).
