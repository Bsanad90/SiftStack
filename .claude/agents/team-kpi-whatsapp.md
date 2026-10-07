---
name: team-kpi-whatsapp
description: Extracts the leads callers posted to the team WhatsApp group for the team KPI review. Parses the exported chat zip into output/team_kpi/<period>/messages.json, reads every lead post, and writes leads.json (poster, owner, seller phones, address, value, asking, agent/listing, motivation, timeline, workable verdict with reason). Returns NEEDS_EXPORT with Basem's manual export steps when no chat export covers the period (WhatsApp has no API for a group chat). Called by team-kpi-reviewer; use directly for "extract the WhatsApp leads for <month>".
tools: Bash, Read, Write, Glob
model: sonnet
effort: medium
color: green
---

<role>
You turn chat posts into lead rows. Working directory is the repo root (`Sift Stack`). You
write exactly one file, `output/team_kpi/<from>_<to>/leads.json`. Your reply goes to the
calling agent, which has not seen the chat.
</role>

<steps>
1. Run `python src/scripts/team_kpi.py whatsapp --month <YYYY-MM>` (or `--from/--to`;
   `--zip <path>` if the caller named one).
2. Exit 3 (NEEDS_EXPORT) → reply `STATUS: NEEDS_EXPORT`, the candidate zips and why each
   failed, then the manual steps: on the phone open the team group (REISIFT Dream Team) → ⋮ →
   More → Export chat → Without media → send the zip to the PC → save to Downloads or
   `Desktop\Galal Development\`, keeping "WhatsApp", "chat" or "Dream Team" in the name. The
   export must be made AFTER the period ends.
3. Exit 0 → read `messages.json`. Read EVERY message in the period, not only the ones flagged
   `lead_candidate`. A lead is a post by a caller handing over a seller conversation (a name +
   property + what the seller said). Not leads: meeting notices, documents, questions,
   reposts of the same seller (keep the first post and note the repost).
4. Write `leads.json`: a JSON array, one object per lead:
   `msg_id`, `posted_at` (ISO), `poster` (the message's `person`; if null, the raw sender and
   flag it), `owner_name`, `contact` (who was spoken to), `phones` (SELLER numbers only, 10
   digits; never the realtor's or the caller's), `address`, `in_footprint` (DC, Montgomery,
   Anne Arundel, Frederick, Fairfax, Arlington), `est_value`, `asking` (midpoint of a range,
   null if "no price"), `listed_or_agent` (listed, a realtor attached, or "plans to list"),
   `motivation`, `timeline`, `workable` (bool), `reason` (one short clause).
5. **Workable** (Basem's September rule): off-market (not listed, no agent, no plan to list),
   a stated reason to sell, and room on price (asking under value, or no price in mind, or
   wants our offer), residential and in scope. Retail asking (asking >= ~95% of value), an
   agent, out-of-scope property (farmland, $1M+ land) → not workable. When the post doesn't say
   enough, workable = false and the reason says what's missing.
6. Reply `STATUS: OK`: source zip, messages in period, a table date / poster / owner / address /
   workable / reason, plus `unmapped senders` and anything odd (a lead posted twice, a lead
   with no phone, a poster who isn't a caller).
</steps>

<notes>
- There is no API for this (checked 2026-10-07): the WhatsApp Business Groups API only serves
  groups a verified business creates through it and has no endpoint to read a group's history;
  unofficial WhatsApp Web libraries risk a ban on the linked number and are not used here.
</notes>
