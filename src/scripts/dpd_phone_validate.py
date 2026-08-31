"""Trestle-score every phone on the records carrying one property tag.

Built for the Doors-Per-Deal priority presets: a preset is pulled into the
account and stamped with its tag (e.g. "DC P1-05 Notice of Default"), the
records are skip traced, and the dialers then need to know which of those
numbers are worth calling first.

Read-only against DataSift (this account's internal API 403s on writes, and
reads are how dpd_presets_verify.py already checks the store). The only
outbound spend is TrestleIQ at ~$0.015/number.

TWO SHAPE TRAPS, both of which silently produce a zero-phone run:
  * Phones are NOT on the property. `/api/internal/property/{uuid}/` carries
    them under `owner.phones` (and `secondary_owners[].phones`); the property's
    own top level has no phone array at all, so reading `record["phone"]`
    returns nothing on every record.
  * The thin LIST endpoint returns a single `phone` OBJECT -- the primary only.
    Sizing a run off it under-counts (16 records read as 16 numbers when they
    actually carry 43).

Usage:
    python src/scripts/dpd_phone_validate.py --tag "DC P1-05 Notice of Default"
    python src/scripts/dpd_phone_validate.py --tag "..." --dry-run   # free: counts + cost
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import config  # noqa: E402
from live_pull import LiveApi  # noqa: E402
from phone_validator import (  # noqa: E402
    DEFAULT_TIERS,
    clean_phone,
    process_phones,
    write_datasift_tags_csv,
    write_detailed_csv,
    write_errors_csv,
    write_summary,
)

TIER_ORDER = ["Dial First", "Dial Second", "Dial Third", "Dial Fourth", "Drop"]
COST_PER_NUMBER = 0.015


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def resolve_tag(api: LiveApi, title: str) -> str:
    """Exact match on the tag title. A substring match would silently pick a
    different tag ("FTM" is inside several), so this is deliberately exact."""
    st, body = api.get("/api/internal/tag/?limit=999")
    if st != 200:
        raise RuntimeError(f"tag list failed ({st}): {str(body)[:200]}")
    rows = body.get("results") or body.get("data") or []
    exact = [r for r in rows if (r.get("title") or "").strip() == title.strip()]
    if not exact:
        near = [r["title"] for r in rows if title.lower()[:12] in (r.get("title") or "").lower()]
        raise SystemExit(f"tag not found: {title!r}\n  did you mean: {near[:10]}")
    return exact[0]["uuid"]


def pull_records(api: LiveApi, tag_uuid: str) -> list[dict]:
    out, offset = [], 0
    while True:
        st, body = api.post_as_get(
            "/api/internal/property/",
            {"limit": 100, "offset": offset, "query": {"must": {"any_tags": [tag_uuid]}}},
        )
        if st != 200:
            raise RuntimeError(f"property query failed ({st}): {str(body)[:200]}")
        rows = body.get("results") or body.get("data") or []
        out.extend(rows)
        offset += len(rows)
        if len(rows) < 100 or offset >= (body.get("count") or 0):
            return out


def hydrate(api: LiveApi, thin: list[dict]) -> list[dict]:
    full = []
    for r in thin:
        st, body = api.get(f"/api/internal/property/{r['uuid']}/")
        if st != 200:
            print(f"  DETAIL FAILED {st} {r['uuid']}", file=sys.stderr)
            body = dict(r, _detail_failed=st)
        full.append(body)
    return full


def name_of(c: dict) -> str:
    n = " ".join(x for x in [c.get("first_name"), c.get("last_name")] if x).strip()
    return n or (c.get("company") or "").strip() or "(unnamed)"


def contacts_of(rec: dict) -> list[dict]:
    """Every contact on the record that can carry a phone: the owner, then any
    secondary owners. Each is {name, role, phones:[{number,type,...}]}."""
    out = []
    o = rec.get("owner") or {}
    if o:
        out.append({"name": name_of(o), "role": "Owner", "phones": o.get("phones") or []})
    for i, s in enumerate(rec.get("secondary_owners") or [], 1):
        if isinstance(s, dict):
            out.append({"name": name_of(s), "role": f"Secondary Owner {i}",
                        "phones": s.get("phones") or []})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, help="exact property tag title")
    ap.add_argument("--out", default=None, help="output dir (default output/phone_validation/<slug>)")
    ap.add_argument("--dry-run", action="store_true", help="count phones and price the run, spend nothing")
    ap.add_argument("--no-litigator", action="store_true", help="skip the litigator add-on")
    args = ap.parse_args()

    outdir = Path(args.out) if args.out else ROOT / "output" / "phone_validation" / slug(args.tag)
    outdir.mkdir(parents=True, exist_ok=True)

    api = LiveApi()
    tag_uuid = resolve_tag(api, args.tag)
    thin = pull_records(api, tag_uuid)
    print(f"tag {args.tag!r} -> {tag_uuid}")
    print(f"records: {len(thin)}")
    if not thin:
        raise SystemExit("ZERO records carry this tag -- nothing to validate.")

    print("hydrating...")
    full = hydrate(api, thin)
    json.dump(full, open(outdir / "records_detail.json", "w"), indent=1)

    # (cleaned, record, contact, phone_meta) for every phone entry on every contact
    entries = []
    for rec in full:
        for c in contacts_of(rec):
            for p in c["phones"]:
                raw = p.get("number") if isinstance(p, dict) else p
                cleaned = clean_phone(str(raw or ""))
                if cleaned:
                    entries.append((cleaned, rec, c, p if isinstance(p, dict) else {}))

    uniq = list(dict.fromkeys(e[0] for e in entries))
    no_phone = [r for r in full if not any(c["phones"] for c in contacts_of(r))]
    print(f"phone entries: {len(entries)}   unique numbers: {len(uniq)}   "
          f"records with NO phone: {len(no_phone)}")
    print(f"estimated Trestle cost: ${len(uniq) * COST_PER_NUMBER:.2f}")
    if args.dry_run:
        print("\n--dry-run: nothing scored, nothing spent.")
        return 0
    if not uniq:
        raise SystemExit("ZERO phones found on these records -- skip trace them first.")

    key = getattr(config, "TRESTLE_API_KEY", "")
    if not key:
        raise SystemExit("TRESTLE_API_KEY not set in .env")

    results, errors = process_phones(
        [(n, n) for n in uniq], key,
        tiers=DEFAULT_TIERS, add_litigator=not args.no_litigator,
    )
    by_num = {r["phone_number"]: r for r in results}

    write_detailed_csv(results, outdir)
    write_datasift_tags_csv(results, outdir)
    write_errors_csv(errors, outdir)
    write_summary(results, errors, DEFAULT_TIERS, outdir)

    # Dial sheet: one row per (record, contact, phone), best tier first within a record.
    rank = {t: i for i, t in enumerate(TIER_ORDER)}
    rows = []
    for cleaned, rec, c, meta in entries:
        r = by_num.get(cleaned)
        a = rec.get("address") or {}
        rows.append({
            "Street": a.get("street", ""), "City": a.get("city", ""),
            "Zip": a.get("postal_code", ""),
            "Contact": c["name"], "Role": c["role"],
            "Phone": cleaned,
            "Tag": (r or {}).get("assigned_tag", "NOT SCORED"),
            "Score": (r or {}).get("activity_score", ""),
            "Line Type": (r or {}).get("line_type", meta.get("type", "")),
            "Carrier": (r or {}).get("carrier", ""),
            "Litigator Risk": (r or {}).get("is_litigator_risk", ""),
            "Keep": (r or {}).get("keep", ""),
            "DataSift Status": meta.get("status", ""),
            "Record UUID": rec.get("uuid", ""),
        })
    rows.sort(key=lambda x: (x["Street"], rank.get(x["Tag"], 98),
                             -(x["Score"] if isinstance(x["Score"], int) else 0)))
    with open(outdir / "dial_sheet.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # Records that ended with nothing callable are named, never silently absent.
    callable_uuids = {r["Record UUID"] for r in rows if r["Keep"] is True}
    dead = [r for r in full if r.get("uuid") not in callable_uuids]
    with open(outdir / "no_callable_number.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Street", "City", "Owner", "Reason", "Record UUID"])
        for r in dead:
            a, cs = r.get("address") or {}, contacts_of(r)
            has = any(c["phones"] for c in cs)
            w.writerow([a.get("street", ""), a.get("city", ""),
                        cs[0]["name"] if cs else "",
                        "all numbers disqualified" if has else "no phone on record",
                        r.get("uuid", "")])

    counts = {}
    for r in results:
        counts[r["assigned_tag"]] = counts.get(r["assigned_tag"], 0) + 1
    print("\n=== TIERS ===")
    for t in TIER_ORDER + sorted(k for k in counts if k not in TIER_ORDER):
        if counts.get(t):
            print(f"  {t:16} {counts[t]:3}")
    print(f"\nscored {len(results)} / {len(uniq)}   errors {len(errors)}")
    print(f"records with a callable number: {len(callable_uuids)} of {len(full)}")
    print(f"outputs -> {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
