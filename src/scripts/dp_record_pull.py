"""Deep prospecting Step 1: pull ONE live record with its custom fields.

The account-wide cache (`output/live_account_pull.json`, built by live_pull.py)
hydrates `/api/internal/property/{uuid}/` only, and that endpoint does NOT carry
custom fields -- which is exactly where the skip-trace relatives' NAMES live. The
record's phones come back tagged `Rel1.1`..`Rel5.3` (relative N, phone M) with no
name attached, so without the custom fields the dial sheet is twelve anonymous
numbers.

Read-only. Three endpoints, all GET:
    /api/internal/property/{uuid}/                 the record
    /api/internal/property/{uuid}/custom-field/    the values (the relatives)
    /api/internal/custom-fields/?limit=999         option uuid -> label map, so
                                                   select fields read as words

Auth reuses live_pull.LiveApi (mints a JWT from DATASIFT_EMAIL/DATASIFT_PASSWORD
via POST /api/token/, 429-aware). Deliberately NOT obituary_opportunity.Reader:
that one imports reisift_auth from the Deal Room `_api/clients` checkout, which
does not exist on this machine.

Usage:
    python src/scripts/dp_record_pull.py --uuid <uuid> [--out output/x.json]
    python src/scripts/dp_record_pull.py --address "123 Example Ln"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_pull import LiveApi  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def fetch_record(api: LiveApi, uuid: str) -> dict:
    status, body = api.get(f"/api/internal/property/{uuid}/")
    if status != 200:
        raise RuntimeError(f"record fetch failed ({status}): {str(body)[:300]}")
    return body


def find_uuid(api: LiveApi, address: str) -> list[dict]:
    """Free-text search. Same `search` query key src/sms_agent/crm.py uses."""
    status, body = api.post_as_get(
        "/api/internal/property/",
        {"limit": 25, "offset": 0, "query": {"must": {"search": address}}},
    )
    if status != 200:
        raise RuntimeError(f"search failed ({status}): {str(body)[:300]}")
    return body.get("results") or body.get("data") or []


def fetch_custom_fields(api: LiveApi, uuid: str) -> list[dict]:
    status, body = api.get(f"/api/internal/property/{uuid}/custom-field/")
    if status != 200:
        raise RuntimeError(f"custom-field fetch failed ({status}): {str(body)[:300]}")
    if isinstance(body, list):
        return body
    return body.get("results") or body.get("data") or []


def option_labels(api: LiveApi) -> dict[str, str]:
    """option uuid -> label, so a select-type value reads as a word not a uuid."""
    status, body = api.get("/api/internal/custom-fields/?limit=999")
    if status != 200:
        raise RuntimeError(f"custom-fields schema fetch failed ({status}): {str(body)[:300]}")
    items = body if isinstance(body, list) else (body.get("results") or body.get("data") or [])
    out: dict[str, str] = {}
    for f in items:
        for o in (f.get("options") or []):
            if o.get("uuid"):
                out[o["uuid"]] = o.get("label") or o.get("title") or ""
    return out


def flatten(rows: list[dict], labels: dict[str, str]) -> dict[str, str]:
    """custom-field rows -> {label: value}, empties dropped, options resolved."""
    flat: dict[str, str] = {}
    for row in rows:
        label = ((row.get("custom_field") or {}).get("label") or "").strip()
        if not label:
            continue
        val = row.get("value")
        if isinstance(val, list):
            val = ", ".join(labels.get(v, str(v)) for v in val if v not in (None, ""))
        elif isinstance(val, str):
            val = labels.get(val, val)
        elif val is not None:
            val = str(val)
        val = (val or "").strip()
        if val:
            flat[label] = val
    return flat


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--uuid")
    ap.add_argument("--address", help="free-text search when the uuid is unknown")
    ap.add_argument("--out", default="output/deep_prospect_record.json")
    a = ap.parse_args()
    if not a.uuid and not a.address:
        ap.error("--uuid or --address required")

    api = LiveApi()

    uuid = a.uuid
    if not uuid:
        hits = find_uuid(api, a.address)
        print(f"search {a.address!r}: {len(hits)} hit(s)")
        for h in hits[:10]:
            ad = h.get("address") or {}
            print(f"  {h.get('uuid')}  {ad.get('street')}, {ad.get('city')} {ad.get('state')}")
        if len(hits) != 1:
            print("Refusing to guess. Re-run with --uuid.")
            return 1
        uuid = hits[0]["uuid"]

    record = fetch_record(api, uuid)
    labels = option_labels(api)
    cf_rows = fetch_custom_fields(api, uuid)
    flat = flatten(cf_rows, labels)

    addr = record.get("address") or {}
    owner = record.get("owner") or {}
    phones = owner.get("phones") or []
    print(f"\n{addr.get('street')}, {addr.get('city')} {addr.get('state')} "
          f"{addr.get('postal_code')}   ({addr.get('county')} County)")
    print(f"owner: {owner.get('first_name')} {owner.get('last_name')}"
          f"{' / ' + owner.get('company') if owner.get('company') else ''}")
    print(f"lists: {', '.join(record.get('lists') or []) or '(none)'}")
    print(f"tags:  {', '.join(record.get('tags') or []) or '(none)'}")
    print(f"status: {record.get('status')}   last_obituary_date: {record.get('last_obituary_date')}"
          f"   probate_open_date: {record.get('probate_open_date')}"
          f"   PR: {record.get('personal_representative')}")

    print(f"\n--- phones on record ({len(phones)}) ---")
    for p in phones:
        print(f"  {p.get('number')}  {p.get('type'):<10} {','.join(p.get('tags') or []):<10} "
              f"{p.get('status')}")

    print(f"\n--- custom fields ({len(flat)} populated of {len(cf_rows)} returned) ---")
    if not flat:
        print("  NONE POPULATED. The relatives' names are not in custom fields on this record.")
    for k in sorted(flat):
        print(f"  {k}: {flat[k]}")

    out_path = ROOT / a.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"uuid": uuid, "record": record, "custom_fields_raw": cf_rows,
         "custom_fields": flat}, indent=1), encoding="utf-8")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
