"""Suppress the sold-property backlog already sitting in the account.

The manage-sold SWEEP catches what sells from now on. This catches what already
sold while we kept marketing it -- records whose own `last_sold` is recent but
whose status is still active. Measured 2026-08-31 against the 26,645-record
hydration: 1,689 such records in the last 24 months, 1,214 of them already
mailed, 651 sitting in "No Answer" on the dialer.

It stamps the same tag the sweep stamps, so the same "Sold Property Cleanup"
sequence flips them to `Already Sold` and every preset drops them. No browser,
no SiftMap, no imported records.

    python src/scripts/sold_backlog.py --audit                     # CSV only, no writes
    python src/scripts/sold_backlog.py --audit --since 2025-08-21  # narrower window
    python src/scripts/sold_backlog.py --one <uuid>                # ONE record, then stop
    python src/scripts/sold_backlog.py --commit                    # the whole reviewed set
    python src/scripts/sold_backlog.py --undo <backup.json>        # restore prior tags

WHAT `last_sold` ACTUALLY MEANS. "This property last changed hands" -- NOT "our
lead sold out from under us". A recent sale can equally mean the current owner
just bought it. Both make the owner data stale; only the first makes the lead
dead. That is why the CSV carries sale_year and price_band, and why the two
low-confidence cohorts (blank/$0 price, and 2024 sales) sort to the top for
review. Basem's decision 2026-08-31: run all bands, not just the confident ones.

REVERSIBILITY. Every run writes a backup of each record's prior tags and status
BEFORE touching it. At 24 months across all price bands some rows will be wrong,
and being able to put one back is what makes running the full set reasonable.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from datasift_api_upload import Api  # noqa: E402
from datasift_uploader import RECENTLY_SOLD_TAG  # noqa: E402
from dpd.preset_spec import DEAD_STATUSES  # noqa: E402

CACHE = ROOT / "output" / "_archive" / "live_account_pull.json"
OUT_DIR = ROOT / "output"

# Reads work on this account; writes are the open question. The public
# /property/{uuid}/ route 403s on GET here, while /api/internal/property/{uuid}/
# returns the record -- so internal is the only surface that sees a property.
READ_PATH = "/api/internal/property/%s/"

# Candidate write shapes, tried in order and recorded. None is confirmed: the
# standing note in CLAUDE.md is that this account's internal API 403s on writes.
# If they all fail, the fallback is the Add-Data CSV wizard with `custom_tag`,
# which is a browser path but does not need a new endpoint.
WRITE_ATTEMPTS = [
    ("PATCH", "/api/internal/property/%s/", "full_tags"),
    ("PATCH", "/api/internal/property/%s/update-tags/", "tag_only"),
    ("POST", "/api/internal/property/%s/add-tags/", "tag_only"),
]


def _status_key(s: str) -> str:
    """Normalise a status for comparison.

    The account stores some statuses as display labels ("No Answer", "Already
    Sold") and others snake_cased ("not_interested", "under_contract", "dnc",
    "follow_up", "closed", "listed", "sold"). `preset_spec.DEAD_STATUSES` holds
    the display forms only, so a plain lowercase compare silently MISSED the
    snake_cased dead ones -- `not_interested` and `under_contract` were being
    selected as "still active" and would have been tagged and flipped. Verified
    against the hydration: 107 not_interested rows were in the first selection.
    """
    return (s or "").strip().lower().replace("_", " ").replace("-", " ")


def _dead_status_keys() -> set[str]:
    return {_status_key(s) for s in DEAD_STATUSES}


def months_ago(n: int) -> str:
    return (date.today() - timedelta(days=int(n * 30.44))).isoformat()


def price_band(p) -> str:
    try:
        v = float(p or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v <= 0:
        return "blank_or_zero"
    if v < 20000:
        return "under_20k"
    if v < 100000:
        return "20k_100k"
    return "100k_plus"


def select(cache: Path, since: str) -> list[dict]:
    """Records that sold on/after `since` and are STILL in an active status."""
    if not cache.exists():
        raise SystemExit(
            f"cache not found: {cache}\n"
            "Pass --cache explicitly. Do NOT let a missing cache start a fresh "
            "multi-hour account re-pull."
        )
    data = json.loads(cache.read_text(encoding="utf-8"))
    dead = _dead_status_keys()
    rows = []
    for r in data.get("records") or []:
        rec, thin = r.get("record") or {}, r.get("thin") or {}
        sold = rec.get("last_sold")
        status = (thin.get("status") or "").strip()
        if not sold or sold < since or _status_key(status) in dead:
            continue
        addr = thin.get("address") or {}
        owner = thin.get("owner") or {}
        rows.append({
            "uuid": r.get("uuid"),
            "street": addr.get("street") or "",
            "city": addr.get("city") or "",
            "state": addr.get("state") or "",
            "zip": addr.get("zip5") or addr.get("postal_code") or "",
            "owner": " ".join(x for x in [owner.get("first_name"),
                                          owner.get("last_name")] if x).strip()
                     or (owner.get("company") or ""),
            "prior_status": status or "(none)",
            "last_sold": sold,
            "sale_year": sold[:4],
            "last_sale_price": rec.get("last_sale_price") or "",
            "price_band": price_band(rec.get("last_sale_price")),
            "directmail_attempts": thin.get("directmail_attempts") or 0,
            "has_phones": bool(thin.get("has_phones")),
            "list_count": thin.get("list_count") or 0,
            "sold_month_tag": "Sold %s" % sold[:7],
        })
    # Review-first ordering: the cohorts most likely to be false positives lead.
    rank = {"blank_or_zero": 0, "under_20k": 1, "20k_100k": 2, "100k_plus": 3}
    rows.sort(key=lambda x: (rank.get(x["price_band"], 9), x["last_sold"]))
    return rows


def summarise(rows: list[dict], since: str) -> None:
    from collections import Counter
    print("\n=== SOLD BACKLOG (sold on/after %s, still active) ===" % since)
    print("  records                : %d" % len(rows))
    print("  already mailed         : %d" % sum(1 for r in rows
                                                if (r["directmail_attempts"] or 0) > 0))
    print("  on at least one list   : %d" % sum(1 for r in rows if r["list_count"]))
    for label, key in (("price band", "price_band"), ("sale year", "sale_year"),
                       ("state", "state"), ("prior status", "prior_status")):
        c = Counter(r[key] for r in rows)
        top = ", ".join("%s %d" % (k, v) for k, v in c.most_common(8))
        print("  by %-20s: %s" % (label, top))


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("\nwrote %s (%d rows)" % (path, len(rows)))


# The "Tagging existing properties" wizard path needs ONLY these four columns
# (read off the wizard's own Data Requirements panel, 2026-08-31). No owner and no
# mailing columns, which is exactly why this path is safe: there is no owner field
# in the file, so nothing can overwrite the PR/DM contact mapping. Tags ride along
# in a fifth column; the wizard's Add-tags step supplies RECENTLY_SOLD_TAG for the
# whole file, and this column carries each record's own sale-month tag.
WIZARD_COLUMNS = ["Property Street", "Property City", "Property State",
                  "Property ZIP Code", "Tags"]


def write_wizard_csv(rows: list[dict], path: Path) -> Path:
    """Minimal CSV for Update Data -> Tagging existing properties."""
    path.parent.mkdir(parents=True, exist_ok=True)
    skipped = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=WIZARD_COLUMNS)
        w.writeheader()
        for r in rows:
            # An address-matched update with no street matches nothing. Drop it
            # loudly rather than shipping a row that silently tags nobody.
            if not r["street"].strip():
                skipped += 1
                continue
            w.writerow({
                "Property Street": r["street"],
                "Property City": r["city"],
                "Property State": r["state"],
                "Property ZIP Code": r["zip"],
                "Tags": "%s,%s" % (RECENTLY_SOLD_TAG, r["sold_month_tag"]),
            })
    print("wrote %s (%d rows%s)"
          % (path, len(rows) - skipped,
             ", %d skipped for a blank street" % skipped if skipped else ""))
    return path


def stamp(api: Api, uuid: str, month_tag: str, backup: list) -> tuple[bool, str]:
    """Add RECENTLY_SOLD_TAG plus the sale-month tag to one record.

    Snapshots prior tags/status into `backup` BEFORE any write, so --undo can
    put the record back.
    """
    before = api.call(READ_PATH % uuid)
    tags = list(before.get("tags") or [])
    backup.append({"uuid": uuid, "tags": tags, "status": before.get("status"),
                   "lists": before.get("lists"), "write_shape": None})
    if RECENTLY_SOLD_TAG in tags:
        return True, "already tagged"

    # Both tags, matching the wizard path and the SiftMap sweep exactly. The
    # earlier version wrote only the trigger tag while the docstring and the run
    # summary both claimed the sale-month tag was applied.
    want = tags + [t for t in (RECENTLY_SOLD_TAG, month_tag) if t and t not in tags]
    last_err = ""
    for method, path, shape in WRITE_ATTEMPTS:
        body = {"tags": want if shape == "full_tags" else [RECENTLY_SOLD_TAG]}
        try:
            api.call(path % uuid, method, body)
            # Record WHICH shape worked, so --undo replays that one. Always
            # replaying WRITE_ATTEMPTS[0] meant a record written by the 2nd or 3rd
            # fallback could never be restored.
            backup[-1]["write_shape"] = [method, path]
            return True, "%s %s" % (method, path.split("/")[-2] or "property")
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:160]
    return False, last_err


def live_audit(src_csv: Path, limit: int = 0) -> Path:
    """Read every backlog record LIVE and classify it. The hydration cache is
    2026-08-21 and Basem hand-flipped statuses since, so the cache cannot answer
    "what still needs the tag" -- only per-record live reads can. Resumable: the
    per-uuid reads land in a JSONL state file next to the output."""
    rows = list(csv.DictReader(src_csv.open(encoding="utf-8-sig")))
    if limit:
        rows = rows[:limit]
    state_p = OUT_DIR / ("sold_backlog_live_state_%s.jsonl" % src_csv.stem[-15:])
    seen: dict[str, dict] = {}
    if state_p.exists():
        for line in state_p.open(encoding="utf-8"):
            try:
                e = json.loads(line)
                seen[e["uuid"]] = e
            except json.JSONDecodeError:
                pass
    api = Api()
    dead_keys = _dead_status_keys()
    out_rows = []
    with state_p.open("a", encoding="utf-8") as st:
        for i, r in enumerate(rows, 1):
            u = r["uuid"]
            e = seen.get(u)
            if e is None:
                try:
                    rec = api.call(READ_PATH % u)
                    tags = [t.get("name") if isinstance(t, dict) else str(t)
                            for t in (rec.get("tags") or [])]
                    e = {"uuid": u, "live_status": rec.get("status") or "",
                         "tagged": RECENTLY_SOLD_TAG in tags}
                except Exception as ex:  # noqa: BLE001
                    e = {"uuid": u, "gone": True, "err": str(ex)[:120]}
                st.write(json.dumps(e) + "\n")
                st.flush()
            if e.get("gone"):
                cat = "gone"
            elif e.get("tagged"):
                cat = "tagged_already"
            elif _status_key(e.get("live_status")) in dead_keys:
                cat = ("dead_already_sold"
                       if _status_key(e.get("live_status")) == "already sold"
                       else "dead_other")
            else:
                cat = "active"
            out_rows.append({**r, "live_status": e.get("live_status", ""),
                             "live_category": cat})
            if i % 100 == 0:
                print("  %d/%d read" % (i, len(rows)), flush=True)

    from collections import Counter
    print("live categories:", dict(Counter(r["live_category"] for r in out_rows)))
    out_p = OUT_DIR / ("sold_backlog_live_%s.csv" % datetime.now().strftime("%Y%m%dT%H%M%S"))
    write_csv(out_rows, out_p)
    return out_p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--since", default=None,
                    help="ISO date; default 24 months back (Basem's window)")
    ap.add_argument("--audit", action="store_true", help="CSV + summary, no writes")
    ap.add_argument("--one", metavar="UUID", help="stamp ONE record and stop")
    ap.add_argument("--commit", action="store_true", help="stamp every selected record")
    ap.add_argument("--undo", metavar="BACKUP_JSON", help="restore tags from a backup")
    ap.add_argument("--wizard-csv", action="store_true",
                    help="also write the minimal CSV for the browser path "
                         "(Update Data -> Tagging existing properties), which needs "
                         "no API access")
    ap.add_argument("--live-audit", metavar="CSV",
                    help="read every record in this backlog CSV LIVE and classify "
                         "(gone / tagged_already / dead / active); no writes")
    ap.add_argument("--commit-live", metavar="CSV",
                    help="stamp only the live_category==active rows of a --live-audit "
                         "output CSV")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.15)
    a = ap.parse_args()

    stamp_ts = datetime.now().strftime("%Y%m%dT%H%M%S")

    if a.live_audit:
        live_audit(Path(a.live_audit), a.limit)
        print("\nReview the live CSV, then stamp the stragglers with "
              "--commit-live <that csv>.")
        return 0

    if a.commit_live:
        rows = [r for r in csv.DictReader(Path(a.commit_live).open(encoding="utf-8-sig"))
                if r.get("live_category") == "active"]
        if a.limit:
            rows = rows[: a.limit]
        print("%d active records to stamp" % len(rows))
        api = Api()
        backup: list = []
        backup_path = OUT_DIR / ("sold_backlog_backup_%s.json" % stamp_ts)
        ok = bad = 0
        try:
            for i, r in enumerate(rows, 1):
                good, note = stamp(api, r["uuid"], r["sold_month_tag"], backup)
                ok, bad = ok + int(good), bad + int(not good)
                print("  [%d/%d] %s %s -- %s" % (i, len(rows), r["uuid"][:8],
                                                 r["street"][:38],
                                                 note if good else "FAILED " + note))
                if not good and i == 1:
                    print("\nFirst record failed; stopping.")
                    break
                time.sleep(a.sleep)
        finally:
            if backup:
                backup_path.write_text(json.dumps(backup, indent=1), encoding="utf-8")
                print("\nprior state -> %s  (undo with --undo)" % backup_path)
        print("\ntagged %d, failed %d" % (ok, bad))
        return 0 if bad == 0 else 1

    if a.undo:
        api = Api()
        recs = json.loads(Path(a.undo).read_text(encoding="utf-8"))
        done = 0
        for r in recs:
            method, path = (r.get("write_shape")
                            or [WRITE_ATTEMPTS[0][0], WRITE_ATTEMPTS[0][1]])
            if r.get("write_shape") is None:
                print("  %s: no recorded write shape (never written?), "
                      "restoring via the default PATCH" % r["uuid"][:8])
            try:
                api.call(path % r["uuid"], method, {"tags": r["tags"]})
                done += 1
            except Exception as e:  # noqa: BLE001
                print("  undo failed %s: %s" % (r["uuid"], str(e)[:120]))
        print("restored %d of %d records" % (done, len(recs)))
        return 0

    since = a.since or months_ago(24)
    rows = select(Path(a.cache), since)
    if not rows:
        print("nothing selected for since=%s" % since)
        return 0
    summarise(rows, since)
    csv_path = OUT_DIR / ("sold_backlog_%s.csv" % stamp_ts)
    write_csv(rows, csv_path)

    if a.wizard_csv:
        sel = rows[: a.limit] if a.limit else rows
        write_wizard_csv(sel, OUT_DIR / ("sold_backlog_wizard_%s.csv" % stamp_ts))

    if a.audit or not (a.commit or a.one):
        print("\nAudit only -- nothing written. Review the CSV, then --one <uuid>, "
              "then --commit.")
        return 0

    targets = [r for r in rows if r["uuid"] == a.one] if a.one else rows
    if a.one and not targets:
        print("uuid %s is not in the selected set" % a.one)
        return 2
    if a.limit:
        targets = targets[: a.limit]

    api = Api()
    backup: list = []
    backup_path = OUT_DIR / ("sold_backlog_backup_%s.json" % stamp_ts)
    ok = bad = 0
    try:
        for i, r in enumerate(targets, 1):
            good, note = stamp(api, r["uuid"], r["sold_month_tag"], backup)
            ok, bad = ok + int(good), bad + int(not good)
            if good:
                print("  [%d/%d] %s %s -- %s" % (i, len(targets), r["uuid"][:8],
                                                 r["street"][:38], note))
            else:
                print("  [%d/%d] FAILED %s -- %s" % (i, len(targets),
                                                     r["uuid"][:8], note))
                if i == 1:
                    print("\nFirst record failed. Stopping rather than hammering "
                          "the API with a call shape it rejects.")
                    break
            time.sleep(a.sleep)
    finally:
        if backup:
            backup_path.write_text(json.dumps(backup, indent=1), encoding="utf-8")
            print("\nprior state -> %s  (undo with --undo)" % backup_path)

    print("\ntagged %d, failed %d" % (ok, bad))
    if ok and a.one:
        print("\nNow verify the chain end to end:")
        print("  - the record carries %r and its sale-month tag" % RECENTLY_SOLD_TAG)
        print("  - the Sold Property Cleanup sequence flipped its status")
        print("  - it no longer appears in two of the 73 presets")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
