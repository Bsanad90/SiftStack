# Building-permit portals


---

## Moved from CLAUDE.md on 2026-10-01 — Building-permit portals — surveyed + pulled 2026-09-24

Verbatim text as it stood on 2026-10-01 (second split of CLAUDE.md; the first was 2026-08-28). The live current state and the traps index stay in CLAUDE.md; this is the record. Snapshot: `_CLAUDE-snapshot-2026-10-01.md`.

## Building-permit portals — surveyed + pulled 2026-09-24 (READ-ONLY, no spend)

`src/scripts/permit_portal_pull.py` pulls the three FREE machine-readable permit feeds in the
footprint into `output/permits/<YYYYMMDD>/` (per-page JSONL cache + `--resume`, `--analyze-only`
rebuilds every CSV with zero HTTP) and writes `evaluation.md`: the answer to "what can a permit
portal fill in for the skills". Offline guard `tools/manual/test_permit_portal_pull.py` (50+
checks) — run after ANY change to `clean_applicant` / `is_gov_or_institution` / `bucket_subtype` /
`stall_class` / `fetch_pages`. First full run 2026-09-24: **69,283 permits** (DC 49,725 /
Montgomery 10,456 / Fairfax 9,102), 3 min, exit 0.

- **DC** (`FEEDS/DCRA/FeatureServer` layers 18 = 2026, 17 = 2025, 13 = Basic Business Licenses)
  is the only feed with NAMES. `OWNER_NAME` is good. `PERMIT_APPLICANT` is the **applicant, not
  the licensed contractor** — the top filers are permit EXPEDITERS (Margo Ricks 1,708 permits for
  1,258 different owners; Bryce Carrington 1,233 / 1,147), and the raw value carries junk prefixes
  (`NA NA; X`, `NULL NULL; X`, `N / A`) that `clean_applicant` strips. The vendor seed therefore
  draws ONLY from the BBL contractor-roster join (5,875 active "Contractor and Construction
  Services" licenses), never from raw applicants. `ZIPCODE` is NULL on every row — the ZIP is
  parsed off `FULL_ADDRESS`. The layer holds ISSUED permits only (0 rows without an issue date)
  and no application date, so DC can never show a stalled application, only expired / cancelled.
  Repeat owners on construction permits are government first (DISTRICT OF COLUMBIA 344, DCHA
  205, USA 109 — removed by word-boundary rules) then developers (MCMILLAN ASSOCIATES LLC 117,
  SKYLAND PHASE C-2 71); 100 of the 785 repeat owners are already in the dispo buyer registry.
- **Montgomery** (Socrata `xfxj-qszi` residential / `i26v-w6bd` commercial / `b6ht-fw3x`
  demolition) and **Fairfax** (`Building_Records_PLUS/FeatureServer/0`, nightly) carry **no
  applicant, contractor or owner**: "multiple permits" there is by address / ZIP only. Montgomery
  has `declaredvaluation` + applied/issued/finaled dates (671 applications open > 6 months =
  the stalled-permit distress list); Fairfax publishes accepted records only. The Socrata
  Cloudflare rule holds: `socrata_where()` refuses ANY function syntax in `$where`.
- **Anne Arundel, Frederick, Arlington have no machine-readable feed.** Contractor names for
  those and for Montgomery / Fairfax live behind anonymous browser searches (AACO Accela,
  Montgomery DPS eSearch, Fairfax PLUS Accela, Arlington Accela, Frederick CIVICS + monthly PDF)
  — the phase-2 list with exact URLs is section 8 of `evaluation.md`. **TRAP:**
  `aca-prod.accela.com/FREDERICK` is the Town of Frederick, COLORADO.
- **No portal anywhere carries phone, email, rating or BBB** — the vendor-directory columns that
  matter most for calling stay blank; permits give company / contact / license / service area.
- Draft `data/ftm_sources_mddcva.csv` rows are in `evaluation.md` §9 but NOT appended:
  `dpd_ftm_registry.CANON` has no `building_permit` type, so a row would canon to None.
