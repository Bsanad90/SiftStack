"""QA the obituary deep-prospecting writeback against the LIVE account.

Reads push_plan.json, pulls every record back over the internal API and checks what
actually landed. Read-only by default; `--repair` re-posts only what is missing.

    python -u src/scripts/obituary_dp_qa.py                 # audit, writes nothing
    python -u src/scripts/obituary_dp_qa.py --repair        # fix the gaps it found
    python -u src/scripts/obituary_dp_qa.py --limit 25 -v   # spot check

Checks per record:
  message   a PINNED message on the board carrying the DP note (NOT the `notes` field --
            add-notes writes a field nobody reads on the record page)
  phones    every planned number present on the owner
  tags      every planned phone carries its Rel{N}.{M} tag AND its Trestle tier
  rel       every planned REL{n} custom field holds its value
  proptags  the property tags (Deep Prospected / Deceased Owner / ...)
"""
import argparse
import csv
import importlib.util
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "src" / "scripts")]

_spec = importlib.util.spec_from_file_location("odb", ROOT / "src" / "scripts" / "obituary_dp_batch.py")
odb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(odb)

# "Unknown" is deliberately never written as a tag (see cmd_build), so it must not be
# audited as a missing one -- that read as 2 defects that were correct behaviour.
TIERS = {"Dial First", "Dial Second", "Dial Third", "Dial Fourth", "Drop"}


def digits(n):
    return "".join(c for c in str(n or "") if c.isdigit())[-10:]


def audit_one(api, key, p, repair=False):
    """Return a dict of what is wrong with this record on the live account."""
    out = {"key": key, "street": p["street"], "uuid": None, "problems": [], "repaired": []}
    uuid, _rec, why = odb._resolve_uuid(api, p)
    if not uuid:
        out["problems"].append(f"unresolved: {why}")
        return out
    out["uuid"] = uuid

    st, full = api.get(f"/api/internal/property/{uuid}/")
    if st != 200:
        out["problems"].append(f"GET property {st}")
        return out

    # ── the message board ──────────────────────────────────────────────
    stm, mb = api.get(f"/api/internal/property/{uuid}/message/")
    msgs = (mb.get("results") or []) if isinstance(mb, dict) else []
    dp = [x for x in msgs if (x.get("message") or "").startswith("DEEP PROSPECTING")]
    pinned = [x for x in dp if x.get("pinned")]
    if not dp:
        out["problems"].append("no DP message on the board")
    elif not pinned:
        out["problems"].append("DP message present but NOT pinned")
    if repair and (not dp or not pinned):
        if not dp:
            sc, r = api.write("POST", f"/api/internal/property/{uuid}/message/",
                              {"message": p["note"][:4000]})
            mu = (r or {}).get("uuid") if isinstance(r, dict) else None
            out["repaired"].append(f"posted message ({sc})")
        else:
            mu = dp[0].get("uuid")
        if mu:
            sc2, _ = api.write("POST", f"/api/internal/property/{uuid}/message/{mu}/pin/", {})
            out["repaired"].append(f"pinned ({sc2})")

    # ── phones + their tags ────────────────────────────────────────────
    have = {digits(x.get("number")): x for x in ((full.get("owner") or {}).get("phones") or [])}
    want = p["phones"]
    missing = [x["number"] for x in want if digits(x["number"]) not in have]
    if missing:
        out["problems"].append(f"{len(missing)} of {len(want)} phones missing")
    no_rel_tag, no_tier = [], []
    for x in want:
        got = have.get(digits(x["number"]))
        if not got:
            continue
        tags = {t.get("name") if isinstance(t, dict) else str(t) for t in (got.get("tags") or [])}
        if not any(t.startswith("Rel") or t.startswith("Owner.") for t in tags):
            no_rel_tag.append(x["number"])
        if x.get("tier") and x["tier"] in TIERS and x["tier"] not in tags:
            no_tier.append(x["number"])
    if no_rel_tag:
        out["problems"].append(f"{len(no_rel_tag)} phones with no Rel tag")
    if no_tier:
        out["problems"].append(f"{len(no_tier)} phones with no Trestle tier tag")
    out["phone_repair"] = (missing, no_rel_tag, no_tier)
    if repair and (missing or no_rel_tag or no_tier):
        body = [{"number": x["number"], "type": x.get("type") or "UNKNOWN",
                 "tags": x.get("tags") or [], "status": "UNKNOWN", "is_connected": True}
                for x in want]
        owner_uuid = (full.get("owner") or {}).get("uuid")
        if owner_uuid:
            sc, _ = api.write("POST", f"/api/internal/owner/{owner_uuid}/upsert-phones/",
                              {"phones": body})
            out["repaired"].append(f"re-upserted {len(body)} phones ({sc})")

    # ── REL custom fields ──────────────────────────────────────────────
    stc, cf = api.get(f"/api/internal/property/{uuid}/custom-field/")
    rows = cf if isinstance(cf, list) else ((cf.get("results") or []) if isinstance(cf, dict) else [])
    got_cf = {((r.get("custom_field") or {}).get("label") or ""): r.get("value") for r in rows}
    cf_missing = [k for k in p["custom_fields"] if not got_cf.get(k)]
    if cf_missing:
        out["problems"].append(f"{len(cf_missing)} of {len(p['custom_fields'])} REL fields empty")
    out["cf_missing"] = cf_missing

    # ── property tags ──────────────────────────────────────────────────
    ptags = {t.get("name") if isinstance(t, dict) else str(t) for t in (full.get("tags") or [])}
    tag_missing = [t for t in p["tags"] if t not in ptags]
    if tag_missing:
        out["problems"].append(f"property tags missing: {tag_missing}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", default=str(odb.DEFAULT_RUN_DIR))
    ap.add_argument("--repair", action="store_true", help="re-post what is missing")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--key")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    run = Path(a.run_dir)
    plan = json.load(open(run / "push_plan.json", encoding="utf-8"))["records"]
    keys = [a.key] if a.key else list(plan)
    if a.limit:
        keys = keys[: a.limit]

    api = odb.WriteApi()
    results, tally = [], Counter()
    t0 = time.time()
    for i, k in enumerate(keys, 1):
        try:
            r = audit_one(api, k, plan[k], repair=a.repair)
        except Exception as e:  # noqa: BLE001
            r = {"key": k, "street": plan[k]["street"], "problems": [f"EXCEPTION {type(e).__name__}: {e}"]}
        results.append(r)
        for pr in r["problems"]:
            tally[pr.split(":")[0].split(" of ")[0].rsplit(" ", 0)[0]
                  if False else _bucket(pr)] += 1
        if not r["problems"]:
            tally["clean"] += 1
        if a.verbose or r["problems"]:
            print(f"  [{i}/{len(keys)}] {r['street']}: "
                  + ("; ".join(r["problems"]) if r["problems"] else "clean")
                  + (f"  -> repaired: {', '.join(r['repaired'])}" if r.get("repaired") else ""))
        if i % 25 == 0:
            el = time.time() - t0
            print(f"  ... {i}/{len(keys)}  ({el:.0f}s, {el / i:.1f}s/rec, "
                  f"clean {tally['clean']})", flush=True)

    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out = run / f"qa_{'repair' if a.repair else 'audit'}_{stamp}.json"
    json.dump({"generated_at": datetime.now().isoformat(timespec="seconds"),
               "repair": a.repair, "records": len(keys),
               "tally": dict(tally), "results": results},
              open(out, "w", encoding="utf-8"), indent=1)
    print(f"\n{len(keys)} records audited{' + repaired' if a.repair else ''}")
    for kk, vv in tally.most_common():
        print(f"  {kk:44} {vv}")
    print(f"report: {out}")
    return 0 if tally["clean"] == len(keys) else 1


def _bucket(pr: str) -> str:
    for frag in ("no DP message on the board", "NOT pinned", "phones missing",
                 "no Rel tag", "no Trestle tier tag", "REL fields empty",
                 "property tags missing", "unresolved", "EXCEPTION"):
        if frag in pr:
            return frag
    return pr[:40]


if __name__ == "__main__":
    sys.exit(main())
