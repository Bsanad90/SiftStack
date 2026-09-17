"""Build a ready-to-paste MLS lookup prompt from a Register of Wills pull's --dump-json output.

Reads the "added" key list from the dump's "run" block (added by
md_register_of_wills_pull.py's --dump-json, 2026-09-14), looks each estate up in "records", and
writes a plain-text prompt continuing the MLS Lookup Workflow document (kept outside this
repo) against exactly today's new decedents -- no sheet row-number bookkeeping needed, since this list IS the queue.

  python src/scripts/mls_lookup_prompt.py --json output/daily_pulls/2026-09-14/register_of_wills.json
  python src/scripts/mls_lookup_prompt.py --json <path> --out <path> --limit 15
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_prompt(dump: dict, limit: int = 0) -> str:
    run = dump.get("run", {})
    added_keys = run.get("added", [])
    records_by_key = {r["ledger_key"]: r for r in dump.get("records", [])}
    rows = [records_by_key[k] for k in added_keys if k in records_by_key]
    if limit:
        rows = rows[:limit]
    if not rows:
        return ""

    by_county: dict[str, list[dict]] = {}
    for rec in rows:
        by_county.setdefault(rec.get("county", ""), []).append(rec)

    lines = [
        "Continue the MLS probate property lookup (Bright MLS Public Records Search) for "
        "today's newly-pulled Register of Wills estates -- see the MLS Lookup Workflow doc for "
        "the search ladder (first+last -> first-initial+last -> last-name-only) and matching "
        "rules (SDAT only when genuinely ambiguous).",
        "",
        f"{len(rows)} new estate(s) across {len(by_county)} count{'y' if len(by_county) == 1 else 'ies'} "
        "(State = MD for all):",
        "",
    ]
    for county in sorted(by_county):
        lines.append(f"{county}:")
        for rec in by_county[county]:
            first = rec.get("decedent_first_name", "")
            last = rec.get("decedent_last_name", "")
            estate_no = rec.get("estate_number", "")
            prs = rec.get("personal_reps") or []
            pr_note = f" (PR: {prs[0]['name']})" if prs else ""
            lines.append(f"  - Estate #{estate_no}: {first} {last}{pr_note}")
        lines.append("")
    lines.append(
        'For each: search MLS, write the found address (or "Not Found (Claude)" if genuinely '
        "unresolvable) back to wherever you're tracking results."
    )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", required=True, help="the pull's --dump-json output")
    ap.add_argument("--out", default="", help="write the prompt text here (also always printed to stdout)")
    ap.add_argument("--limit", type=int, default=0, help="cap how many new estates go into one prompt (0 = no cap)")
    args = ap.parse_args()

    dump = json.loads(Path(args.json).read_text(encoding="utf-8"))
    prompt = build_prompt(dump, limit=args.limit)

    if not prompt:
        print("No new estates in this run -- nothing to prompt.")
        if args.out:
            Path(args.out).write_text("", encoding="utf-8")
        return

    print(prompt)
    if args.out:
        Path(args.out).write_text(prompt, encoding="utf-8")


if __name__ == "__main__":
    main()
