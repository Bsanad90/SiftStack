# DataSift.ai integration


---

## Moved from CLAUDE.md on 2026-10-01 — DataSift.ai integration: files, CSV schema, upload wizard, enrich + skip trace, UI automation patterns, Market Finder extraction

Verbatim text as it stood on 2026-10-01 (second split of CLAUDE.md; the first was 2026-08-28). The live current state and the traps index stay in CLAUDE.md; this is the record. Snapshot: `_CLAUDE-snapshot-2026-10-01.md`.

## DataSift.ai (REISift) Integration

DataSift.ai (formerly REISift) is the CRM where records land. **It has an API, and the API is the default path:** `apiv2.reisift.io/api/internal/` covers reads, record creation, tags, lists, custom fields, phones, owner blocks, notes/pins, assignees, tasks, presets, boards and users; `map.reisift.io/filters/` covers SiftMap saved filters. The contract and its traps are in "DataSift API upload contract" above. Playwright (below) is still used for what has no proven API route: the upload wizard, Send To → Skip Trace, Enrich, the sequence builder, SiftMap Add Records, and saved-filter deletion.

**Domain:** `app.reisift.io` (NOT `app.datasift.ai`). API at `apiv2.reisift.io`.

**Auth:** mint the JWT from `.env` (`POST /api/token/` with `DATASIFT_EMAIL` / `DATASIFT_PASSWORD`), or use `REISIFT_API_KEY` as `Api-Key` on `/api/internal/`. Never paste or hardcode a Bearer. (The Deal Room shared auth store, `datasift-admin` = staff ty+1, is Ty's staff token for client-account impersonation and is not used for this account.)

### Key Files
- `src/datasift_formatter.py` — Transforms `NoticeData` → DataSift CSV (42 columns)
- `src/datasift_uploader.py` — Playwright login + upload wizard + enrich + skip trace + preset management + sequence builder + SiftMap sold workflow
- `tools/manual/test_datasift_upload.py` — Headed browser test (upload + enrich + skip trace)
- `tools/manual/test_manage_presets.py` — Headed browser test (preset discovery + sold exclusion + sequence creation)
- `tools/manual/test_manage_sold.py` — Headed browser test (SiftMap sold property tagging; `--dry-run` to count without writing)
- `tools/manual/test_manage_sold_url.py` — Offline check of county resolution + URL construction (no browser, no account). Run after any change to the sold sweep.

### CSV Column Structure (42 columns)
- **Core auto-mapped (11):** Property Street/City/State/ZIP, Owner First/Last Name, Mailing Street/City/State/ZIP, Tags
- **Lists + Notes (2):** Lists (for niche sequential), Notes (contextual per notice type)
- **Built-in fields (13):** Estimated Value, MSL Status, Last Sale Date/Price, Equity Percentage, Tax Deliquent Value, Tax Delinquent Year, Tax Auction Date, Foreclosure Date, Probate Open Date, Personal Representative, Parcel ID, Structure Type, Year Built, Living SqFt, Bedrooms, Bathrooms, Lot (Acres)
- **Custom fields (16):** Notice Type, County, Date Added, Owner Deceased, Date of Death, Decedent Name, Decision Maker, DM Relationship, DM Confidence, DM 2/3 Name/Relationship, Obituary URL, Source URL, Notice Screenshot

### Sequential marketing on this account
Records move through Call → Mail → Deep Prospecting lanes in the **85 Records presets** (14 folders: 73 DPD + the Obituary lane 13/14 since 2026-09-30; `src/dpd/preset_spec.py` is the source of truth, `dpd_presets_verify.py` the check). They carry only the 6 footprint counties, exclude the dead statuses (`preset_spec.DEAD_STATUSES`, incl. `Already Sold`), and ride on lane tags stamped at pull time (`FTM` for daily first-to-market pulls, `Priority 1/2` for SiftMap rungs, `Obituaries` for obituary records — never FTM / Priority 1 unless the record is also a probate case). Sold suppression is the `Recently Sold` TAG, which the live `Sold Property Cleanup` sequence turns into the `Already Sold` STATUS; a same-named tag and status would split suppression across two vocabularies. Ty's TN design ("00 Niche Sequential Marketing" 12 + "01. Bulk Sequential Marketing" 9, `niche_sequential.py`) is the upstream model; it is not what runs here.

- **"Courthouse Data" tag:** Ty's TN first-to-market marker, still written by `datasift_formatter.py` beside `FTM`. This account's presets key on `FTM`, not on this tag.
- **Lists column:** Maps `notice_type` → DataSift list name (`foreclosure` → "Foreclosure", `probate` → "Probate", `tax_sale` → "Tax Sale", `tax_delinquent` → "Tax Delinquent", `eviction` → "Eviction", `code_violation` → "Code Violation", `divorce` → "Divorce"). DataSift auto-creates lists from CSV.
- **Tags:** Courthouse Data, notice_type, county, YYYY-MM date, deceased/living, DM confidence level, has_auction, tax_delinquent, photo_import (for photo-sourced records)

### Upload Wizard (6 Steps, build 1.0.46 2026-08-22)
DataSift added an **Enrichment** step between Setup and Add Tags; the wizard code didn't know about it and every subsequent step/locator silently ran one step off. Fixed in `datasift_uploader.py`.
1. **Setup:** Click "Upload File" sidebar → "Add Data" → dropdown "Uploading a new list not in DataSift yet" → enter list name → organization questions
2. **Enrichment:** click through (new step, no fields to fill here yet)
3. **Tags:** Skip through — or fill from a `custom_tag` parameter (see below)
4. **Upload File:** Set file on `input[type="file"]`
5. **Map Columns:** Core address fields auto-map; Tags, Lists, and enrichment columns may need manual mapping
6. **Review + Finish Upload:** Click "Finish Upload" — processing happens in background

**`custom_tag` parameterization:** the Add Tags step used to type a hardcoded tag, silently overriding whatever the CSV's own Tags column already said. It's now a `custom_tag` parameter sourced from the CSV, so any upload — not just one specific pipeline's default tag — can carry its own tag through the wizard.

### Column Mapping Notes
- Only core address fields (Property Street, City, State, ZIP) reliably auto-map
- Tags, Lists, Estimated Value, and enrichment columns often stay unmapped in step 4
- Notes and MSL Status sometimes auto-map
- Custom fields require drag-and-drop mapping in the wizard; set them over the API instead (`custom-field/update-values/`). The 16 custom fields listed above are Ty's TN schema: `Notice Type`, `County` and `Decedent Name` do not exist on this account (2026-09-23), so check `custom-fields/` before mapping any of them

### Contact Logic
- **Deceased owners:** Contact = decision maker (first/last name + mailing address from DM)
- **Living owners:** Contact = property owner (owner mailing address, falls back to property address)

### Post-Upload: Enrich + Skip Trace

After CSV upload, the pipeline automatically runs two DataSift actions via Playwright:

1. **Enrich Property Information** (Manage → Enrich Data): Adds SiftMap property data (beds, baths, Zestimate, sqft, sale history) to uploaded records. "Enrich Owners" and "Swap Owners" are OFF — protects our PR/DM contact mapping.
2. **Skip Trace** (Send To → Skip Trace): Pulls phone numbers (up to 5 per owner) + emails via unlimited plan ($97/mo). Our stamp is **`REISift Traced MM/YYYY`**, typed into the wizard's optional tag box; DataSift adds its own `REISift Skipped` / `REISift Skipped MM/YY` chips by itself (leave them).
   **The date tag is mandatory on every path that loads numbers** — see "ALWAYS STAMP THE SKIP-TRACE DATE" under My Defaults for the three source tags.

Both run in background — tracked in Activity tab. Both are ON by default when `--upload-datasift` is set.

### CLI Flags
```bash
python src/main.py daily --upload-datasift        # upload + enrich + skip trace
python src/main.py daily --upload-datasift --no-enrich       # upload only, skip enrichment
python src/main.py daily --upload-datasift --no-skip-trace   # upload + enrich, skip skip trace
python src/main.py daily --notify-slack            # send run summary to Slack/Discord
python src/main.py daily --deep-heirs               # resolve deceased-owner heirs via Enformion ($0.10/match DataSift rate, ~$0.35 rack)
```

### Environment Variables
- `DATASIFT_EMAIL` — DataSift login email
- `DATASIFT_PASSWORD` — DataSift login password
- `SLACK_WEBHOOK_URL` — Slack/Discord webhook for run summaries

### Login Selectors (SPA quirks)
- Hidden checkboxes (Remember me, Terms) — click `<label>` elements, not `<input>`
- Use `wait_until="domcontentloaded"` (not `networkidle` — SPA keeps WebSocket connections open)
- Cookie validation: check for `/dashboard` or `/records` in URL (5s wait for SPA redirect)

### DataSift UI Automation Patterns

Hard-won patterns from build 1.0.22-1.0.23 (SiftMap, preset management, sequence builder). Follow these to avoid repeating past mistakes.

**Styled-Components (no native HTML controls)**
- No native `<select>` elements — all dropdowns are `[class*="Selectstyles__Select"]` containers
- `[class*="SelectValue"]` = current value display; `[class*="SelectOptionContainer"]` = dropdown options
- Multiple Select dropdowns exist per panel (Lists, Tags, Property Status) — always target the **LAST visible one**
- Use `x > 450` bounds check in all JS queries to avoid matching sidebar elements (sidebar is 0-400px)
- React state updates require native setter + event dispatch, not just `.value = ...`:
  ```js
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(input, 'new value');
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
  ```

**Panel Scrolling (Playwright scroll fails)**
- Filter panel is a scrollable `<div>`, NOT the viewport — `scroll_into_view_if_needed()` does nothing
- Use JS: `el.scrollIntoView({behavior: 'instant', block: 'center'})` instead
- Filter Presets section is at the BOTTOM of the filter panel — must scroll container down to reveal
- After scrollIntoView, element y-positions may be negative — don't filter by `y > 0` for the target element

**React DnD (Sequence Builder)**
- Cards have `draggable="false"` — Playwright's native drag won't work
- Must use slow mouse drag: `mouse.move()` → `mouse.down()` → 20 incremental steps (50ms each) → `mouse.up()`
- Add 500ms pauses between down/move/up phases
- "Add new Action +" button required for 2nd+ actions; first action uses initial drop zone
- Sidebar cards can scroll out of view when main area scrolls — scroll BOTH source and target into view before drag

**Pointer Interception (common blockers)**
- Beamer NPS survey iframe (`#npsIframeContainer`) blocks ALL pointer events globally — remove from DOM via `_dismiss_popups()`
- `RecordsFiltersstyles__RecordsFiltersSection` elements intercept clicks — use `page.evaluate()` JS click or `force=True`
- When Playwright click fails with "outside of viewport" or "intercept": switch to `page.evaluate(el => el.click())`
- SiftMap PropertyDetails panel blocks sidebar checkboxes — remove from DOM before interactions

**Preset Management Workflow**
- Flow: open filter panel → scroll to bottom → expand "Filter Presets" → expand folder → click preset → modify → Save (not Save New) → confirm overwrite
- Folder names have case variations ("00 Niche" vs "00 NICHE") — use `.toUpperCase()` comparison
- Preset names follow pattern `^\d{2}\.` (e.g., "00. Needs Skipped")
- On this account, change presets over the API instead (filter-preset PATCH with store read-back, proven 2026-09-07); the panel flow above is for reading what the panel renders. Before writing a new block, read its STORE shape out of the app bundle — see "Where the DPD code stands".

**Sequence Builder Workflow**
- Flow: `/sequences` → Create → title + folder → drag trigger → condition → actions tab → drag actions → configure → save
- Duplicate name handling: detect error toast "different sequence title", retry with " V2" suffix
- Actions tab: navigate via "Set the Following Actions" button or URL (`/sequences/new/actions`)
- Autocomplete inputs: after each selection, `fill("")` + Escape to dismiss dropdown before next entry
- "Sold Property Cleanup" sequence (build 1.0.23): Trigger (Property Tags Added) → Condition (`Recently Sold`, from `datasift_uploader.RECENTLY_SOLD_TAG`) → Actions (Status→`Already Sold` per `SOLD_STATUS`, Remove Lists, Clear Tasks, Clear Assignee). **Live and Active on this account since 2026-08-31** with 3 of the 4 actions (`Remove Lists` never landed). `Already Sold` is offered by the action picker, so `SOLD_STATUS_FALLBACK` is unused.

**SiftMap Automation**
- Address a county by its FIPS through `dpd.jurisdictions` (the sold sweep builds its SiftMap URL that way; `test_manage_sold_url.py` checks it). Upstream's city search (Knox → "Knoxville, TN") and its `.get(county, "47093")` fallback are retired: they silently queried Knoxville for every MD/DC/VA county.
- PropertyDetails panel auto-opens on search — remove from DOM before other interactions
- "Add Records to Account" modal: keep "Do not replace owners" ON unless told otherwise (it protects PR/DM mapping), add tags, dismiss dropdown by clicking heading (NOT Escape — clears tags). Never click Add Records without an explicit go.
- Saved filters are read and PATCHed over `map.reisift.io/filters/` (see `dpd_siftmap_filters_api.py`); the old "filters set visually but don't re-query, ~3-5 records per run" limit belonged to the city-search path, not the URL-filter path (Montgomery dry run matched 992).

**Market Finder Extraction Patterns (build 1.0.29+)**

Hard-won patterns from building `extract_market_finder.py`. The Market Finder UI differs significantly from the rest of DataSift.

- **NO HTML `<table>` element** — data table is entirely div-based: `Tablestyles__TableContainer` → `TableRow` → `TableCell` (styled-components). Searching for `<table>` or `<tr>/<td>` finds nothing.
- **PAGINATION, not infinite scroll** — table shows 20 rows per page with "1-20 of N" text and `PaginationInnerContainer` with prev/next `<button>` elements. Must click through ALL pages to get complete data. Knox County has 48 ZIPs (3 pages) and 120+ neighborhoods (7 pages).
- **State/County selection uses `InputMultiSearch`** — NOT styled-component Select dropdowns. Inputs have placeholders: `"Select States"`, `"Select Counties"`, `"Select ZIP Codes"`. Click input → type name → click dropdown result item (`[class*="Item"]:has-text("...")`).
- **ZIP/Neighborhood toggle is a styled Select dropdown** — at the top bar with `Selectstyles__SelectValue` showing current view. Check the displayed text BEFORE clicking — if already on the correct view, clicking toggles AWAY from it. Only click to switch if the displayed text doesn't match the desired view.
- **Beamer push modal (`#beamerPushModal`)** — appears on fresh login, blocks ALL pointer events. Different from the NPS survey (`#npsIframeContainer`). Both must be removed from DOM before any click interactions. Always call dismiss with `force=True` as fallback.
- **Page body scrolling required** — pagination controls are at `y=1867`, below the viewport (`clientH=824`). Must scroll `AdminPage__AdminPageBody` container down before pagination buttons are accessible.
- **Summary panel on right side** — shows county-level aggregates: Median Home Value, Homes on Market, Mo. Investor Transactions, Homes Sold Last Month, Market Rent, Gross Rental Yield, Homeownership Rate. Extract via regex on page text.

```bash
# Extract all Market Finder data for a county
python src/extract_market_finder.py --state "Maryland" --county "Montgomery" -v
python src/extract_market_finder.py --state "Virginia" --county "Fairfax,Arlington" --headless
# DC: Market Finder has no ZIP split for DC; a 0 there is missing data, not a zero market

# Output: JSON file in output/market_finder_{state}_{county}_{timestamp}.json
```
