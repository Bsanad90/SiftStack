"""Probe POST /api/internal/property/ -- the one DataSift CREATE never measured here.

WHY. Record creation is the single operation still forced through the Playwright
upload wizard. The Open API route `POST /property/` has 403'd for every method and
both credentials since 2026-09-01. Ty's accounts create fine through the INTERNAL
route, which this account already writes to daily (tags, statuses, owners, phones,
custom fields, notes, presets). CLAUDE.md's standing constraint keeps writes
browser-driven "until one CREATE route is probed on this account". This is it.

HOW STAGE 1 AVOIDS WRITING. The route does not upsert: Ty measured a duplicate
address answering 400 {"non_field_errors": ["Property address already exists!"],
"property": ["<uuid>"]}, and it validates `owner` BEFORE the duplicate check, so a
body needs a real owner block to reach that check. So we POST a record's OWN stored
address and owner straight back at the route and expect to be bounced. No tags, no
lists, no custom fields, no notes are sent -- which is what makes an unexpected
upsert a no-op rather than a pollution event. The account is counted on that street
before and after; a changed count is the proof, never the response body.

FIVE OUTCOMES, NOT TWO. This codebase has been bitten repeatedly by a 200 that
stores nothing, and by a route that ignores its own scoping key (the Probate list,
emptied account-wide on 2026-09-16). classify() separates BLOCKED / ROUTE_WORKS /
VALIDATION_ERROR / UPSERT_NO_CHANGE / RECORD_CREATED, and treats any 2xx carrying a
uuid we did not target as an alarm: a created record CANNOT be deleted over this
API, only in the UI.

STAGE 2 creates ONE real record from a pending pull CSV -- a lead we wanted anyway,
so a pass leaves nothing to clean up. Address and owner go in the create body;
tags, lists, custom fields and the pinned note are attached afterwards with the
per-record routes already proven on this account (the create body's `lists` shape
is disputed upstream -- an array "returns 201 and changes nothing" -- so it is not
used).

Usage:
    python -X utf8 -u tools/manual/test_property_create_probe.py    # offline first
    python -X utf8 -u src/scripts/probe_property_create.py          # explain, no calls
    python -X utf8 -u src/scripts/probe_property_create.py --probe  # stage 1, writes nothing
    python -X utf8 -u src/scripts/probe_property_create.py --create-one \
        --csv output/daily_pulls/DATE/PULL.csv --row 0 --commit     # stage 2
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

from obituary_dp_batch import WriteApi  # noqa: E402

OUT = ROOT / "output" / "property_create_probe"
CREATE_PATH = "/api/internal/property/"
DETAIL = "/api/internal/property/%s/"

# Exit codes. 0 = question answered in our favour, 3 = answered against us (a real
# result, not an error), 4 = inconclusive, 5 = we changed the account.
EXIT_OK, EXIT_BLOCKED, EXIT_INCONCLUSIVE, EXIT_CREATED = 0, 3, 4, 5
# 6 = the address is ALREADY on the account, so --create-one had nothing to do.
# Not success and not a failure: free-text `search` cannot detect this (it matches
# the stored street EXACTLY -- '3220 Ivy Meadow' finds nothing when the account
# holds '3220 Ivy Meadow Dr'), whereas the create route normalises the suffix and
# ZIP+4. The 400 is the only reliable existence test, and it writes nothing.
EXIT_EXISTS = 6


def say(*a):
    print(*a, flush=True)


def _text(body) -> str:
    return json.dumps(body) if isinstance(body, (dict, list)) else str(body or "")


def _as_obj(body):
    """Body as a dict when possible -- WriteApi hands back raw text on an error."""
    if isinstance(body, dict):
        return body
    try:
        v = json.loads(body)
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def addr_of(d: dict) -> dict:
    a = d.get("address") or {}
    return {"street": (a.get("street") or "").strip(),
            "city": (a.get("city") or "").strip(),
            "state": (a.get("state") or "").strip(),
            "postal_code": (a.get("postal_code") or "").strip()}


def owner_for_create(d: dict):
    """The record's own owner, shaped as the create route wants it back.

    Mirrors datasift_api_upload.build_property: a person carries first_name (+
    last_name only when present), an entity carries `company` with the person keys
    OMITTED -- the API rejects a blank first_name outright, and omitting a key is
    not the same as sending "". Returns None when the record has no usable owner
    (the blank-owner Incomplete records), which makes it a bad probe target.
    """
    o = d.get("owner") or {}
    first = (o.get("first_name") or "").strip()
    last = (o.get("last_name") or "").strip()
    company = (o.get("company") or "").strip()
    a = o.get("address") or {}
    mail = {"street": (a.get("street") or "").strip(),
            "city": (a.get("city") or "").strip(),
            "state": (a.get("state") or "").strip(),
            "postal_code": (a.get("postal_code") or "").strip()}
    if not mail["street"]:
        # A block without a mailing address re-binds to a random entity on PATCH;
        # on create it is simply incomplete. Fall back to the property address.
        mail = dict(addr_of(d))
    owner = {"address": mail}
    if first:
        owner["first_name"] = first
        if last:
            owner["last_name"] = last
    elif company:
        owner["company"] = company
    else:
        return None
    return owner


def classify(status, body, target_uuid: str) -> dict:
    """Turn one create response into a verdict. Pure -- the offline test drives it.

    Never collapses to pass/fail: a 2xx carrying an unfamiliar uuid means we made a
    record, which is strictly worse than a refusal because it cannot be undone here.
    """
    txt = _text(body)
    obj = _as_obj(body)

    if status in (0, 429) or (isinstance(status, int) and status >= 500):
        return {"verdict": "TRANSIENT", "exit": EXIT_INCONCLUSIVE, "created_uuid": "",
                "note": "throttled/network/server -- no evidence either way, re-run"}
    if status == 403:
        return {"verdict": "BLOCKED", "exit": EXIT_BLOCKED, "created_uuid": "",
                "note": "403 -- same wall as the Open API route; creation stays in the wizard"}
    if status == 401:
        return {"verdict": "AUTH_FAILED", "exit": EXIT_INCONCLUSIVE, "created_uuid": "",
                "note": "401 after the client's own re-mint -- check DATASIFT_EMAIL/PASSWORD"}
    if status == 404:
        return {"verdict": "ROUTE_ABSENT", "exit": EXIT_BLOCKED, "created_uuid": "",
                "note": "404 -- but OPTIONS/404 has lied before; confirm the path"}
    if status == 400:
        if "already exist" in txt.lower():
            prop = obj.get("property")
            got = (prop[0] if isinstance(prop, list) and prop else (prop or "")) or ""
            same = (not got) or (not target_uuid) or got == target_uuid
            return {"verdict": "ROUTE_WORKS", "exit": EXIT_OK, "created_uuid": "",
                    "note": ("duplicate rejected, nothing created" if same else
                             "duplicate rejected but it named %s, not the target" % got)}
        return {"verdict": "VALIDATION_ERROR", "exit": EXIT_INCONCLUSIVE, "created_uuid": "",
                "note": "reachable, body rejected: " + txt[:300]}
    if status in (200, 201, 202):
        new = (obj.get("uuid") or "").strip()
        if new and target_uuid and new == target_uuid:
            return {"verdict": "UPSERT_NO_CHANGE", "exit": EXIT_OK, "created_uuid": "",
                    "note": "route upserts; we sent the record's own data, so nothing changed"}
        if new:
            return {"verdict": "RECORD_CREATED", "exit": EXIT_CREATED, "created_uuid": new,
                    "note": "A RECORD WAS CREATED -- delete it in the UI, it cannot be "
                            "deleted over this API"}
        return {"verdict": "UNKNOWN_2XX", "exit": EXIT_CREATED, "created_uuid": "",
                "note": "2xx with no uuid -- assume something was written: " + txt[:300]}
    return {"verdict": "UNEXPECTED_%s" % status, "exit": EXIT_INCONCLUSIVE,
            "created_uuid": "", "note": txt[:300]}


# ------------------------------------------------------------------ live helpers
def read_detail(api, uuid: str) -> dict:
    st, body = api.get(DETAIL % uuid)
    if st != 200:
        raise RuntimeError("detail read failed for %s: %s %s" % (uuid, st, str(body)[:200]))
    return body or {}


def street_count(api, street: str):
    """(status, count) for a free-text search. The status SURVIVES to the caller: a
    throttled read collapsed into a number is how a no-op looks like a change."""
    st, body = api.search({"limit": 1, "offset": 0, "query": {"must": {"search": street}}})
    if st != 200:
        return st, None
    return 200, (body or {}).get("count")


def pick_target(api, uuid: str = "") -> dict:
    """A record with a real street AND a usable owner. Detail-reads recent rows."""
    if uuid:
        d = read_detail(api, uuid)
        if not addr_of(d)["street"] or owner_for_create(d) is None:
            raise RuntimeError("%s has no usable street/owner -- pick another" % uuid)
        return d
    st, body = api.get("/api/internal/property/?limit=20&offset=0&ordering=-created")
    if st != 200:
        raise RuntimeError("listing failed: %s %s" % (st, str(body)[:200]))
    for r in ((body or {}).get("results") or [])[:8]:
        try:
            d = read_detail(api, r["uuid"])
        except RuntimeError:
            continue
        if addr_of(d)["street"] and owner_for_create(d) is not None:
            return d
    raise RuntimeError("no recent record had both a street and a usable owner")


def log_created(uuid: str, payload: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / ("created_%s.json" % datetime.now().strftime("%Y%m%dT%H%M%S"))
    p.write_text(json.dumps(dict(payload, uuid=uuid), indent=1), encoding="utf-8")
    return p


# ----------------------------------------------------------------------- stage 1
def cmd_probe(api, uuid: str) -> int:
    d = pick_target(api, uuid)
    target = d.get("uuid") or uuid
    addr = addr_of(d)
    owner = owner_for_create(d)
    body = {"address": addr, "owner": owner}

    say("target   %s, %s %s %s" % (addr["street"], addr["city"], addr["state"],
                                   addr["postal_code"]))
    say("uuid     %s" % target)
    say("body     %s" % json.dumps(body))

    st0, before = street_count(api, addr["street"])
    if before is None:
        say("ABORT: pre-count failed (%s) -- without it a create would be invisible" % st0)
        return EXIT_INCONCLUSIVE
    say("count    %s record(s) match %r before" % (before, addr["street"]))

    st, resp = api.write("POST", CREATE_PATH, body, tries=1)
    say("POST     %s -> %s" % (CREATE_PATH, st))
    v = classify(st, resp, target)

    st1, after = street_count(api, addr["street"])
    if after is None:
        say("WARNING: post-count failed (%s) -- verify by hand" % st1)
    else:
        say("count    %s record(s) after" % after)
        if after != before:
            say("ALARM: the count moved %s -> %s. Something was written." % (before, after))
            if v["exit"] == EXIT_OK:
                v = dict(v, verdict="COUNT_MOVED", exit=EXIT_CREATED,
                         note="response looked clean but the account count changed")

    say("")
    say("VERDICT  %s -- %s" % (v["verdict"], v["note"]))
    if v["verdict"] == "ROUTE_WORKS":
        say("         => POST /api/internal/property/ is reachable and authorized here.")
        say("            Next: --create-one --csv <pull.csv> --row N --commit")
    if v["created_uuid"]:
        p = log_created(v["created_uuid"], {"address": addr, "stage": "probe",
                                            "note": v["note"]})
        say("         logged to %s -- DELETE THIS RECORD IN THE UI" % p)
    return v["exit"]


# ----------------------------------------------------------------------- stage 2
def cmd_create_one(api, csv_path: Path, row_n: int, commit: bool) -> int:
    import datasift_api_upload as up  # noqa: E402

    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if row_n >= len(rows):
        say("row %s out of range (%s rows)" % (row_n, len(rows)))
        return EXIT_INCONCLUSIVE
    row = rows[row_n]
    full = up.build_property(row)
    # Address + owner ONLY. Tags/lists ride the per-record routes afterwards.
    body = {"address": full["address"], "owner": full["owner"]}
    tags = full.get("tags") or []
    # A RoW record carries two lists ("Register of Wills Probates" + "Probate"),
    # so the column is comma-separated the way Tags is.
    lists = [x.strip() for x in (row.get("Lists") or "").split(",") if x.strip()]

    say("row %s of %s" % (row_n, csv_path.name))
    say("create   %s" % json.dumps(body))
    say("then     tags=%s lists=%s notes=%s"
        % (tags, lists, "yes" if row.get("Notes") else "no"))
    if not commit:
        say("\nDry run. Add --commit to create this record.")
        return EXIT_OK

    st, resp = api.write("POST", CREATE_PATH, body, tries=1)
    say("POST     %s -> %s" % (CREATE_PATH, st))
    v = classify(st, resp, "")
    if v["verdict"] == "ROUTE_WORKS":
        say("EXISTS   this address is already on the account; nothing created")
        return EXIT_EXISTS
    if v["verdict"] not in ("RECORD_CREATED", "UNKNOWN_2XX"):
        say("VERDICT  %s -- %s" % (v["verdict"], v["note"]))
        return v["exit"]
    uuid = v["created_uuid"]
    if not uuid:
        say("2xx with no uuid -- cannot attach anything; check the account by hand")
        return EXIT_CREATED
    say("created  %s" % uuid)

    steps = []
    idx = up.field_index(up.Api()) if any(row.get(c) for c in up.FIELD_MAP) else {}
    # field_pairs SKIPS anything it cannot resolve and reports it through `warn`.
    # Swallowing that set hides a silently-dropped column: datasift_api_upload's
    # FIELD_MAP is Ty's TN schema, and Notice Type / County / Decedent Name do not
    # exist on this account at all.
    warn: set = set()
    pairs = up.field_pairs(row, idx, warn) if idx else []
    if warn:
        say("SKIPPED custom fields (absent from this account or unresolved option): %s"
            % ", ".join(sorted(warn)))
    if tags:
        steps.append(("tags",) + api.write("PATCH", DETAIL % uuid, {"tags": tags}))
    if lists:
        steps.append(("lists",) + api.write("PATCH", DETAIL % uuid, {"lists": lists}))
    if pairs:
        steps.append(("fields",) + api.write(
            "PATCH", "/api/internal/property/%s/custom-field/update-values/" % uuid, pairs))
    if row.get("Notes"):
        mst, mbody = api.write("POST", "/api/internal/property/%s/message/" % uuid,
                               {"message": row["Notes"][:4000]})
        steps.append(("note", mst, ""))
        muuid = _as_obj(mbody).get("uuid") or ""
        if muuid:
            steps.append(("pin",) + api.write(
                "POST", "/api/internal/property/%s/message/%s/pin/" % (uuid, muuid), {}))
    for name, st_, resp_ in steps:
        say("  %-8s %s %s" % (name, st_,
                              str(resp_)[:120] if st_ not in (200, 201, 204) else ""))

    # Read-back is the only accepted evidence.
    d = read_detail(api, uuid)
    got_tags = sorted((t.get("title") if isinstance(t, dict) else t)
                      for t in (d.get("tags") or []))
    got_lists = sorted((x.get("title") if isinstance(x, dict) else x)
                       for x in (d.get("lists") or []))
    say("")
    say("readback address=%s" % addr_of(d))
    say("readback owner=%s" % json.dumps(d.get("owner") or {}, default=str)[:200])
    say("readback tags=%s" % got_tags)
    say("readback lists=%s" % got_lists)
    missing = [t for t in tags if t not in got_tags] + \
              [x for x in lists if x not in got_lists]

    p = log_created(uuid, {"address": addr_of(d), "tags": got_tags, "lists": got_lists,
                           "fields": len(pairs), "source": str(csv_path), "row": row_n,
                           "steps": [[n, s] for n, s, _ in steps]})
    say("\nlogged   %s" % p)
    if missing:
        say("INCOMPLETE: %s did not land -- attach by hand or re-PATCH" % missing)
        return EXIT_INCONCLUSIVE
    say("OK: record created over the API and verified by read-back.")
    return EXIT_OK


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--probe", action="store_true",
                   help="stage 1: POST one record's own address+owner back; writes nothing")
    g.add_argument("--create-one", action="store_true",
                   help="stage 2: create ONE real record from a pull CSV")
    ap.add_argument("--uuid", default="", help="probe this record instead of the newest")
    ap.add_argument("--csv", type=Path, help="pull CSV for --create-one")
    ap.add_argument("--row", type=int, default=0, help="row index within the CSV (default 0)")
    ap.add_argument("--commit", action="store_true", help="actually create (stage 2 only)")
    a = ap.parse_args()

    if not (a.probe or a.create_one):
        say(__doc__)
        say("No action flag. Use --probe first; --create-one only after it passes.")
        return EXIT_OK
    if a.create_one and not a.csv:
        ap.error("--create-one needs --csv")

    api = WriteApi()
    if a.probe:
        return cmd_probe(api, a.uuid)
    return cmd_create_one(api, a.csv, a.row, a.commit)


if __name__ == "__main__":
    sys.exit(main())
