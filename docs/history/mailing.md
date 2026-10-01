# Mailed-history index


---

## Moved from CLAUDE.md on 2026-10-01 — Mailed-history index — screen every mailing list before it goes out

Verbatim text as it stood on 2026-10-01 (second split of CLAUDE.md; the first was 2026-08-28). The live current state and the traps index stay in CLAUDE.md; this is the record. Snapshot: `_CLAUDE-snapshot-2026-10-01.md`.

## Mailed-history index — screen EVERY mailing list before it goes out (built 2026-09-17)

The CRM's only "was this property mailed" signal is `directmail_attempts` on the thin row, and
it was not always advanced: on the 2026-09-15 obituary heir cohort, 137 records sat on the
Inheritance / Probate / Register of Wills lists (mailed campaigns) and 26 carried an attempt.
Basem's rule (2026-09-17): a property on the **Inheritance, Divorce or Probate** list was
mailed, counter or no counter; **Register of Wills Probates** counts only WITH an attempt; and
the authoritative record is the weekly SENT list the mail team files on Drive —
`Marketing Lists/<YYYY>/<MM>/<MMDD>/{Probate With PR,Probate No PR,Divorce}_<YYYYMMDD>_Mailings.xlsx`
(2026 from April only, per Basem; the 2025 `Weekly Mailings_*` variants are out of scope).

```bash
# 1. pull the sent lists (Divorce Cases venv python; token path + folder id from auto-memory
#    reference_marketing_lists_drive, passed as env vars -- they are NOT in .env)
"<Divorce Cases>/.venv/Scripts/python.exe" src/scripts/vendor_lists_fetch.py --families mailings --years 2026 --since 20260401
python src/scripts/mailed_history.py columns          # gate: every sheet must resolve a property street column
python src/scripts/mailed_history.py index            # -> output/vendor_lists/mailed_index.json
# 2. screen any mailing CSV (defaults to property_street / property_zip columns)
python src/scripts/mailed_history.py check --csv <mailing.csv>
```

`obituary_mail_consolidate.py build` applies all three signals and **refuses to run without the
index** (`--no-drive` screens on the CRM signals alone, `--keep-mailed` reproduces the unscreened
file); dropped properties land in `excluded.csv` as `previously mailed` with the lists, the
counter and the matching Drive file named. Measured 2026-09-17 on the obituary cohort: 5,042 →
**4,563 pieces**, 146 properties dropped — **61 of them had NO CRM signal at all** and were found
only in the Drive files. Same day, **one envelope per door**: when a ranked signer LIVES AT the
property the anonymous "The Shaws" household piece is dropped and the named signer piece stands
in for it (629 household pieces, logged as `resident_signer_mailed`; final **3,934 pieces**:
548 household + 3,386 signer). Siblings at one address are merged by the `merge` subcommand into
a SEPARATE file, `obituary_mailing_merged.csv` (**3,364 rows**; Basem: keep the per-person file
as is): every heir must sign so nobody is preferred — `Samuel & Amy Tapper`, or
`The Benitez Family` / `<top> & Family` for 3+, a 40-char overflow or an `Unknown` first name;
spouse first; `salutation` names everyone; `co_addressees` + `merged_mail_ids` keep the trace.
Drive-only finding: 61 were found
only in the Drive files (mostly Aug/Sep `Probate With PR` drops), which is why the Drive leg is
mandatory. **Trap: fuzzy address matching must be UNIT-aware.** The house-number + first-token
fallback (needed because `Apt 304` and `#304` never key equal) matched 13 DIFFERENT condo units
in the same building on the first pass; `units_compatible()` now requires the unit to agree.
**Closing the cycle:** after a drop is sent, save its final CSV to Drive as
`<Campaign>_<YYYYMMDD>_Mailings.xlsx` in that week's folder (by hand — the fetcher never writes
to Drive) so the next fetch + `index` sees it. The obituary heir list itself has NOT been mailed
as of 2026-09-17.

---
