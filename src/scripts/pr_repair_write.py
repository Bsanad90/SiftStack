"""PR-field repair for the DC Probates New cohort. Writes ONLY:
  - clean matches (all sheet tabs agree on one name), and
  - single-estate conflicts (name variants of one person, or co-PRs on the
    same estate #) -- the precedence pick (cohort tab, then Uploaded to
    REIsift, then DC Probates).
Holds (NOT written): multi-estate addresses and sheet no-matches -- those go
to the Register of Wills worklist. Prior values are backed up before any
write. --commit for live; default is a dry run.
"""
import sys, csv, json, re, argparse
from collections import defaultdict
from datetime import datetime

ROOT = r"c:\Users\pc\Desktop\Galal Development\Sift Stack"
sys.path.insert(0, ROOT + r"\src\scripts")
OUT = ROOT + r"\output\dp_1122branch"

from obituary_dp_batch import WriteApi, names_match  # noqa: E402
import openpyxl  # noqa: E402

PR_FIRST_UUID = "1dd4aaf9-7304-4a25-b684-54a342aedc04"
PR_LAST_UUID = "eeac8728-6773-4f62-9b03-ef968da073a0"
PRECEDENCE = ["DC Probates New", "Uploaded to REIsift", "DC Probates"]


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def est_norm(e):
    e = str(e or "").strip()
    return e[:-2] if e.endswith(".0") else e


def build_index():
    wb = openpyxl.load_workbook(OUT + r"\probate_extraction.xlsx", read_only=True, data_only=True)
    idx = defaultdict(list)
    for ws in wb.worksheets:
        header = None
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                header = {str(h).strip().lower(): j for j, h in enumerate(row) if h}
                continue
            cols = {k: (row[v] if v < len(row) else None) for k, v in header.items()}
            addr = cols.get("property address")
            if not addr:
                continue
            pf = str(cols.get("pr first name") or "").strip()
            pl = str(cols.get("pr last name") or "").strip()
            est = est_norm(cols.get("estate #") or cols.get("case #"))
            if pf and pl:
                idx[norm(addr)].append({"tab": ws.title, "first": pf, "last": pl, "estate": est})
    return idx


def pick(hits):
    for tab in PRECEDENCE:
        cand = [h for h in hits if h["tab"] == tab]
        if cand:
            return cand[0]
    return hits[0]


def classify(hits):
    """-> (bucket, picked_hit_or_None). Buckets: clean / variant / co_pr / multi_estate."""
    names = {(h["first"].lower(), h["last"].lower()) for h in hits}
    if len(names) == 1:
        return "clean", hits[0]
    estates = {h["estate"] for h in hits if h["estate"]}
    if len(estates) > 1:
        return "multi_estate", None
    # one estate (or none stated): same person spelled differently, or co-PRs
    ref = pick(hits)
    if all(names_match(f"{h['first']} {h['last']}", f"{ref['first']} {ref['last']}") for h in hits):
        return "variant", ref
    return "co_pr", ref


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    idx = build_index()
    audit = list(csv.DictReader(open(OUT + r"\pr_field_audit.csv", encoding="utf-8-sig")))
    plan, holds = [], []
    from collections import Counter
    counts = Counter()
    for r in audit:
        if r["class"] == "ok":
            continue
        hits = idx.get(norm(r["street"])) or []
        if not hits:
            counts["hold:no_match"] += 1
            holds.append({**r, "hold_reason": "no_match"})
            continue
        bucket, h = classify(hits)
        if bucket == "multi_estate":
            counts["hold:multi_estate"] += 1
            holds.append({**r, "hold_reason": "multi_estate",
                          "estates": " | ".join(sorted({x["estate"] for x in hits if x["estate"]}))})
            continue
        counts[f"write:{bucket}"] += 1
        plan.append({"uuid": r["uuid"], "street": r["street"], "bucket": bucket,
                     "prior_first": r["pr_first"], "prior_last": r["pr_last"],
                     "new_first": h["first"], "new_last": h["last"],
                     "source_tab": h["tab"], "estate": h["estate"]})

    print(dict(counts))
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    json.dump(holds, open(OUT + rf"\pr_repair_holds_{ts}.json", "w"), indent=1)
    if not a.commit:
        print(f"DRY RUN: would write {len(plan)} records. Holds: {len(holds)}.")
        json.dump(plan, open(OUT + rf"\pr_repair_plan_{ts}.json", "w"), indent=1)
        return 0

    backup_p = OUT + rf"\pr_repair_backup_{ts}.json"
    json.dump(plan, open(backup_p, "w"), indent=1)   # prior values ride in the plan
    print("backup (prior values):", backup_p)

    api = WriteApi()
    ok = fail = 0
    log = open(OUT + rf"\pr_repair_writelog_{ts}.jsonl", "a", encoding="utf-8")
    for i, p in enumerate(plan, 1):
        body = [{"field_uuid": PR_FIRST_UUID, "value": p["new_first"]},
                {"field_uuid": PR_LAST_UUID, "value": p["new_last"]}]
        st, resp = api.write("PATCH", f"/api/internal/property/{p['uuid']}/custom-field/update-values/", body)
        got = {}
        if st == 200 and isinstance(resp, list):
            for row in resp:
                cf = (row.get("custom_field") or {})
                got[cf.get("uuid")] = row.get("value")
        good = (st == 200 and got.get(PR_FIRST_UUID) == p["new_first"]
                and got.get(PR_LAST_UUID) == p["new_last"])
        ok += good
        fail += (not good)
        log.write(json.dumps({**p, "status": st, "verified": good}) + "\n")
        if not good:
            print(f"  FAIL {p['street']}: status {st} echo {got}")
        if i % 50 == 0:
            print(f"{i}/{len(plan)} written", flush=True)
    log.close()
    print(f"done: {ok} ok, {fail} failed of {len(plan)}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
