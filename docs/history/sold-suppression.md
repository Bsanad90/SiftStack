# Sold suppression: the SiftMap sold sweep and the 2026-09 backlog

Moved verbatim from CLAUDE.md "Active pipelines" on 2026-09-30. Current state lives in CLAUDE.md;
this is the build record. Note: the sold backlog is now handled by the SiftMap sweep
(`manage-sold --sold-account-scope in`), not `sold_backlog.py` (Basem, 2026-09-30).

- **manage-sold (SiftMap sold sweep)** — CODE DONE + SUPPRESSION WIRED 2026-08-31. **No sweep
  has run; no records imported.** Ported from Ty's Knox/Blount flow to all 14 jurisdictions:
  cadence, per-month `Sold YYYY-MM` tags, `--min-sale-price 1000` and the record-importing
  behaviour are his, unchanged. Three things are not: counties resolve through
  `dpd.jurisdictions` and **raise** instead of falling back to Knox's `47093` (the old
  `.get(county, "47093")` silently queried Knoxville for every MD/DC/VA county); the tag is
  `Recently Sold`, not `Sold`, which is a status here; and a per-(fips, month) checkpoint lets a
  14-county run resume. Offline verification passes
  (`python tools/manual/test_manage_sold_url.py`).
  **Done live:** the `Recently Sold` property tag exists (`dpd_tags_create.py --names`, now
  supports arbitrary names), and the **`Sold Property Cleanup` sequence is live and Active** in
  the Transactions folder — trigger *tag added* → condition `Recently Sold` → **Change Status to
  `Already Sold`** + Delete All Property Tasks + Clear Assignee. Because every preset excludes
  `Already Sold` via `DEAD_STATUSES`, suppression needs no preset edit. `Already Sold` IS
  selectable in the sequence action picker, so `SOLD_STATUS_FALLBACK` was not needed.
  **Two gaps, stated rather than hidden:** (1) `Remove Property Lists` never landed — the React
  DnD reported "Added action" twice while leaving an empty drop zone, so the sequence has 3 of 4
  actions. Status suppression is unaffected; removing lists is hygiene and can be added by hand
  via "Make Changes". (2) **`dpd_doctor` under-reports sequences** — it read `sequences
  unchanged (5)` after the sequence was demonstrably created, because it does not expand the
  folder chevrons on `/sequences`. Do not trust `--verify` for sequences; search `/sequences`
  by name instead. The earlier "5 sequences, all Basem's" baseline is suspect for the same
  reason.
  **Traps learned:** the sequence condition input is an autocomplete over EXISTING tags — typing
  a tag that does not exist leaves the field empty and the save is rejected, so the tag must be
  created first. And DataSift confirms a save with a MODAL while staying on
  `/sequences/new/actions`; the old code both claimed success unconditionally and tested the URL
  with `"/new" not in url or "/sequences/" in url`, which is always true there. Both fixed —
  the save now reads the confirmation modal and the validation banner, and reports which action
  cards actually landed.
  **Dry run PASSED live 2026-08-31** (`--counties Montgomery --months-back 1 --dry-run`):
  resolved to fips 24031, window 2026-07-01..07-31, **992 properties matched**, nothing
  imported or tagged, no checkpoint written, exit 0. The screenshot
  (`datasift_siftmap_filtered_24031_2026-07.png`) shows Potomac / Montgomery Village /
  Poolesville and rows in Cabin John MD 20818, Beallsville MD 20839 — Montgomery County, NOT
  Knoxville. 992 against the ~1,152/month Market Finder predicts is good corroboration. Rows
  include Condominium Unit and Townhouse, confirming there is no single-family filter: the
  sweep takes every property type. One defect found and fixed by this run — county success was
  keyed on `records > 0`, so every dry run reported "No counties processed successfully" and
  exited 1; it now keys on the month results, and the offline suite has a regression check.
  **Next:** dry-run all 14 headless, then a live sweep on the smallest county.
- **Sold backlog suppression (`src/scripts/sold_backlog.py`) — COMPLETE 2026-09-07.** All
  1,559 backlog records are suppressed: 928 were flipped `Already Sold` by Basem's manual
  sweep, and the remaining 630 still-active were stamped `Recently Sold` + month tag over the
  API (`--commit-live`, new mode) — the cleanup sequence flipped every sampled one, QA 15/15.
  The API tag write (merge-only property PATCH) works and fires the sequence, so the browser
  wizard runbook below is OBSOLETE — kept only as history. New pattern for any future backlog:
  `--live-audit <csv>` (live per-record classify, resumable) then `--commit-live <live csv>`.
  Backups: `output/sold_backlog_backup_20260907T*.json` (restorable with `--undo`).
  Historical record of the build and the parked browser path follows.
  The sweep above catches what sells from now on; this
  catches what already sold while we kept marketing it. Audit (read-only, from the 26,645-record
  hydration) selects **1,559 records** that sold on/after 2024-08-31 and are STILL in an active
  status: 1,123 already mailed, all of them on a list, 643 sitting in "No Answer" on the dialer.
  MD 941 / VA 488 / DC 127. Price bands $100k+ 905, blank-or-$0 635, $20k-100k 11, under-$20k 8.
  (Was 1,668 until a code review caught the status bug below.)
  Reviewable CSV at `output/sold_backlog_<ts>.csv`, sorted review-first (blank/$0 price and 2024
  sales lead, being the likeliest false positives — `last_sold` means the property changed hands,
  which can equally mean the CURRENT owner just bought it). Every row carries `prior_status`;
  every write run snapshots prior tags to a backup with `--undo` to restore.
  **Browser path VALIDATED except the tag click (2026-08-31).** `src/scripts/sold_backlog_upload.py`
  drives Upload File -> **Update Data -> "Tagging existing properties"**, which is the right
  surface: the wizard's own Data Requirements for that option are Property Street / City / State /
  ZIP Code and NOTHING else, so the file carries no owner column and cannot overwrite PR/DM
  mapping, and it updates existing records rather than creating any. Proven in a dry run on one
  row: the option sticks, the file uploads, **all four address columns auto-map with the real
  values**, and the Review step is reached. Nothing was ever submitted.
  **The one blocker is applying the tag**, and both routes are unproven:
  (a) the Add-tags step's input refuses automation — Playwright `.type()` leaves it empty and the
  dropdown unfiltered, and React's native-value-setter pattern sets the DOM value (readback
  confirms `Recently Sold`) but React clears it and never filters. (b) The CSV's Tags column
  **does not auto-map** at step 4 (the known Tags/Lists behaviour), so mapping it needs the React
  DnD.
  **Three false successes were caught by screenshots in this flow, all now fixed to fail loudly:**
  the tag helper reported success on an empty input; the chip check matched `Recently Sold` in the
  open SUGGESTION LIST rather than a committed chip (it is a real account tag now, so it appears
  as an option); and the script would have proceeded to Review and Finish having tagged nothing.
  It now refuses to continue unless the tag is verifiably committed.
  **RUNBOOK — one human pass, ~2 minutes, no code needed.** This is a one-off backlog clear, not a
  recurring job, so hand-running the wizard is cheaper than automating two hostile React controls:
  Records -> Upload File -> **Update Data** -> "Tagging existing properties" -> Next ->
  **Add tags: type `Recently Sold` and click Add** -> Next -> upload
  `output/sold_backlog_wizard_20260831T163705.csv` -> Next -> the four address columns auto-map; **drag the
  `Tags` column onto the `Tags` target** to also land each row's `Sold YYYY-MM` -> Next -> Finish.
  Then confirm on one record that the status flipped to `Already Sold` (the sequence does that) and
  that it left two presets.
  **Why the API route is parked, not disproven:** the one-record write probe was stopped by the local permission classifier, so [sentence truncated in the original; the API route was later proven, 2026-09-07]
  **Code review 2026-08-31 caught a silent selection bug:** the account stores some statuses as
  display labels ("No Answer") and others snake_cased ("not_interested", "under_contract",
  "dnc"), while `preset_spec.DEAD_STATUSES` holds only the display forms. A plain lowercase
  compare therefore MISSED the snake_cased dead statuses, and 109 already-dead records --
  107 of them `not_interested` -- were selected as "still active" and would have been re-tagged
  and flipped. `sold_backlog._status_key()` now normalises underscores and hyphens before
  comparing. Anything else comparing a live status against `DEAD_STATUSES` has the same hazard.
  **Related finding:** a record read live carried the list `Probate`, which is NOT among the 27
  lists `dpd_doctor` captured — so its LIST capture under-reports as well as its sequence capture.
  `datasift_uploader.SOLD_REMOVE_LISTS` was built from that baseline and is therefore probably
  missing real lists. Not blocking (status does the suppression, lists are hygiene), but do not
  treat those 22 names as the full set. A live sweep **imports** ~7.9K records/month across the 14 (measured from
  `output/dpd_market_finder/*.json`), so it needs an explicit go. Separately, 1,689 records
  already in the account sold in the last 24 months and are still active (1,214 already mailed,
  651 on the dialer) — now that the tag and sequence exist, stamping `Recently Sold` on those
  by address suppresses them with no browser and no imports.


---

## Moved from CLAUDE.md on 2026-10-01 — Sold suppression state as of 2026-10-01

Verbatim text as it stood on 2026-10-01 (second split of CLAUDE.md; the first was 2026-08-28). The live current state and the traps index stay in CLAUDE.md; this is the record. Snapshot: `_CLAUDE-snapshot-2026-10-01.md`.

- **Sold suppression — the SiftMap sold sweep (`manage-sold`) is THE path, for new sales AND
  any backlog (Basem, 2026-09-30).** The sweep stamps `Recently Sold` + `Sold YYYY-MM`; the live,
  Active **`Sold Property Cleanup`** sequence (Transactions folder) fires on that tag and sets
  status `Already Sold`, deletes tasks, clears the assignee; every preset excludes `Already Sold`
  via `DEAD_STATUSES`, so no preset edit is needed. Run it on the 6 footprint counties with
  `--sold-account-scope in` (tags records we already hold, imports nothing); a catch-up is the
  same command with a larger `--months-back`. Dry-run first. Without `in` it IMPORTS every sold
  property in the county-month, which needs an explicit go. State: the Montgomery dry run passed
  2026-08-31 (992 matched, fips 24031); **no live sweep has run.** The 2026-09-07 backlog (1,559
  records) was cleared by `sold_backlog.py --commit-live`; that script is retired for new work.
  Known gaps: the sequence has 3 of 4 actions (`Remove Property Lists` never landed, add by hand);
  `dpd_doctor` under-reports sequences and lists; a live status compared to `DEAD_STATUSES` must be
  normalised (`not_interested` vs "Not Interested"). With no `--counties` it sweeps the 6
  (`dpd.jurisdictions.FOOTPRINT`, 2026-09-30). → [history](docs/history/sold-suppression.md)
