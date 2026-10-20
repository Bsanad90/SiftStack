# RoW manual review: setup for Che

**How the day runs (Tue-Sat):**

1. ~06:00-07:00 Basem's PC pulls the notices and uploads them to the Drive folder
   **SiftStack Daily Pulls / YYYY-MM-DD** (you are an editor there). `READY.txt` = complete.
2. Right after the pull, the automated Land Records lookup fills property addresses for new
   estates (usually ~1 hour).
3. Then Claude's research agents (on Basem's PC) re-check every estate the lookup found
   nothing for, 10 estates per agent in parallel (obituaries, Land Records with other name
   forms, SDAT). Addresses they find are verified against SDAT.
   Estates still not found are **added to Probate Extraction 2 -> Register of Wills** as new
   rows with Property Address `Not Found (Claude)`.
4. Every estate the agents could not settle is first looked up on the DataSift account
   (read-only). One already there under the decedent's name, or at the agents' candidate
   street, is held back so you never redo it; it is listed in
   `row_manual_review_<date>_on_account.csv` for reference. The rest go to the day folder as
   **`row_manual_review_<date>.csv`**. That is your list. A row marked `PR only` in its
   `on_account` column means the account holds a record under the PR's name at the PR's own
   house (a lead, not the estate's property).
5. You work it with your Claude, which uploads your findings and marks the list REVIEWED.
   Basem gets a notification and takes it from there (skip trace, deep prospecting, dialing,
   DataSift upload are all his call).

## One-time setup

1. **Install Claude Code** and sign in with your galaldev.com claude.ai account.
2. **Clone the repo:**
   ```
   git clone https://github.com/7elenkesh/SiftStack-private.git
   cd SiftStack-private
   ```
3. **Connect Google Drive** at claude.ai -> Settings -> Connectors -> Google Drive, signed in as
   mohammad@galaldev.com. In Claude Code, `/mcp` should list `claude.ai Google Drive`.
4. **Python 3** on PATH with `openpyxl` (`pip install openpyxl`), used to read Probate
   Extraction 2.

## Every pull day, once the list is in the day folder (usually late morning ET)

```
cd SiftStack-private
git pull
claude --agent row-manual-review
```
Type: `what do I need to review today`. It shows the worklist (easiest first) with what
Probate Extraction 2 already says for each estate. Then just tell it what you find:
- `W128213 is 12 Elm St Rockville 20850, from Land Records deed 2019`
- `54934 sold in 2026, skip`
- `look up the obituary for 55211`

When finished: `done, upload it`. It uploads
`row_manual_review_<date>_REVIEWED_by_che.csv` and renames the list to
`... - REVIEWED.csv`, which notifies Basem.

## Rules

- Land Records: search it yourself in your browser. Never ask YOUR Claude to log in there:
  Basem's PC runs the automated logins one at a time, and a second automated session at the
  same moment triggers the emailed access code that locks everyone out.
- Probate Extraction 2: edit it yourself as usual; Claude only reads it.
- Never write to DataSift from this routine.
- Files under `output/` hold real names and addresses: keep them on your PC, never commit them.
