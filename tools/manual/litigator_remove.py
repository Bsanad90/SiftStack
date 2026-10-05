"""Litigator removal pass (Basem 2026-10-05: ALWAYS litigator scrub).

Reads output/litigator_backfill_20261005/ (the backfill's Trestle results) and, for every
number Trestle flags `phone.is_litigator_risk == true` that one of OUR push plans loaded on a
live record:

  1. resolves the live property by the push log's recorded uuid (re-resolved by address if it
     404s -- a 404 can be a merge), reads the owner block;
  2. removes the number with the PER-RECORD route `POST owner/{uuid}/remove-phones/` and reads
     the owner back to prove it is gone (the status code is never the proof);
  3. strips the number from that run dir's push_plan.json (the plan is the source of truth --
     a later `push --force` would otherwise load it again) and stamps the run dir's
     trestle_cache.json with the litigator verdict for EVERY backfilled number, so the QA
     scripts and `_keep_number` see the check. Backups beside each file.

Provenance guard: a number is only removed if it is in OUR plan for that record (that is how
it got onto the list), so the "remove only what our plan wrote" trap is satisfied by
construction. Numbers on the owner that are not in any plan of ours are never touched.

  python tools/manual/litigator_remove.py            # dry run: what would be removed
  python tools/manual/litigator_remove.py --commit   # do it, read back, log
"""
from __future__ import annotations
import argparse, json, re, shutil, sys, time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "src" / "scripts"))
import obituary_dp_batch as odb  # noqa: E402

OUT = ROOT / "output" / "litigator_backfill_20261005"
STAMP = datetime.now().strftime("%Y%m%dT%H%M%S")


def latest_push_uuid(run: Path, key: str) -> str | None:
    uuid = None
    for lg in sorted(run.glob("push*.jsonl")):
        for line in open(lg, encoding="utf8", errors="replace"):
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("key") == key and e.get("uuid"):
                uuid = e["uuid"]
    return uuid


def resolve_by_address(api, street: str, zip_: str) -> str | None:
    st, r = api.search({"limit": 5, "offset": 0, "query": {"must": {"search": street}}})
    if st != 200:
        return None
    rows = [x for x in (r.get("results") or []) if str((x.get("address") or {}).get("postal_code") or "")[:5] == zip_[:5]]
    return rows[0]["uuid"] if len(rows) == 1 else None


def backup(path: Path) -> None:
    shutil.copy2(path, path.with_name(f"{path.stem}.bak_litigator_{STAMP}{path.suffix}"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    cache = json.load(open(OUT / "trestle_litigator_cache.json", encoding="utf8"))
    nmap = json.load(open(OUT / "number_to_records.json", encoding="utf8"))
    risk = sorted(n for n, v in cache.items() if v.get("litigator") is True)
    print(f"{len(cache)} backfilled numbers, {len(risk)} litigator risk")

    # ---- 1. stamp every run dir's trestle_cache.json with the verdicts (plans unaffected)
    by_run: dict[str, set] = {}
    for n, hits in nmap.items():
        for h in hits:
            by_run.setdefault(h["run"], set()).add(n)
    for run, nums in sorted(by_run.items()):
        tc = ROOT / run / "trestle_cache.json"
        if not tc.exists():
            continue
        d = json.load(open(tc, encoding="utf8"))
        changed = 0
        for n in nums:
            v = cache.get(n) or {}
            if n in d and isinstance(d[n], dict) and v.get("litigator") is not None and d[n].get("litigator") is None:
                d[n]["litigator"] = v["litigator"]; changed += 1
        if changed and a.commit:
            backup(tc); json.dump(d, open(tc, "w", encoding="utf8"), indent=1)
        print(f"  cache {run}: {changed} verdicts {'written' if a.commit else 'would be written'}")

    # ---- 2. per affected (run, key): remove from the live owner, strip from the plan
    targets: dict[tuple, dict] = {}
    for n in risk:
        for h in nmap.get(n, []):
            t = targets.setdefault((h["run"], h["key"]), {"street": h["street"], "numbers": set()})
            t["numbers"].add(n)
    print(f"{len(targets)} (run, record) pairs carry a litigator number")
    api = odb.WriteApi()
    log_path = OUT / f"removal_log_{STAMP}.jsonl"
    removed_total = 0
    for (run, key), t in sorted(targets.items()):
        rd = ROOT / run
        entry = {"run": run, "key": key, "street": t["street"], "numbers": sorted(t["numbers"]), "at": datetime.now().isoformat(timespec="seconds"), "commit": a.commit}
        uuid = latest_push_uuid(rd, key)
        st, full = api.get(f"/api/internal/property/{uuid}/") if uuid else (0, None)
        if st == 404 or not uuid:
            zip_ = key.rsplit("|", 1)[-1]
            uuid2 = resolve_by_address(api, t["street"], zip_)
            entry["reresolved"] = uuid2
            if uuid2:
                uuid = uuid2; st, full = api.get(f"/api/internal/property/{uuid}/")
        if st != 200:
            entry["error"] = f"property fetch {st}"; print(f"  !! {t['street']}: {entry['error']}")
            open(log_path, "a", encoding="utf8").write(json.dumps(entry) + "\n"); continue
        owner = full.get("owner") or {}
        ouuid = owner.get("uuid")
        live = {odb._d10(p.get("number") or ""): p for p in (owner.get("phones") or []) if p.get("number")}
        on_owner = [n for n in sorted(t["numbers"]) if n in live]
        entry.update({"uuid": uuid, "owner_uuid": ouuid, "owner": f"{owner.get('first_name') or ''} {owner.get('last_name') or ''}".strip(),
                      "phones_before": len(live), "on_owner": on_owner,
                      "tags_on_owner": {n: live[n].get("tags") for n in on_owner}})
        if not on_owner:
            entry["result"] = "nothing on owner"; print(f"  -- {t['street']}: litigator number(s) {sorted(t['numbers'])} not on the owner any more")
        elif not a.commit:
            entry["result"] = "would remove"; print(f"  DRY {t['street']} ({entry['owner']}): would remove {on_owner} of {len(live)} phones")
        else:
            wst, resp = api.write("POST", f"/api/internal/owner/{ouuid}/remove-phones/", {"phones": on_owner})
            entry["remove_status"] = wst; entry["remove_resp"] = str(resp)[:200]
            st2, full2 = api.get(f"/api/internal/property/{uuid}/")
            live2 = {odb._d10(p.get("number") or "") for p in ((full2 or {}).get("owner") or {}).get("phones") or [] if p.get("number")}
            still = [n for n in on_owner if n in live2]
            entry["phones_after"] = len(live2); entry["still_present"] = still
            entry["result"] = "removed" if not still else "FAILED read-back"
            removed_total += len(on_owner) - len(still)
            print(f"  {'OK ' if not still else '!! '}{t['street']} ({entry['owner']}): remove-phones {wst}, {len(live)} -> {len(live2)} phones, still present {still}")
            time.sleep(0.3)
        # strip from the plan (the source of truth) so a later push --force cannot re-add
        plan_f = rd / "push_plan.json"
        if a.commit and plan_f.exists():
            plan = json.load(open(plan_f, encoding="utf8"))
            recs = plan.get("records", plan) if isinstance(plan, dict) else plan
            recs = list(recs.values()) if isinstance(recs, dict) else recs
            stripped = 0
            for r in recs:
                if isinstance(r, dict) and r.get("key") == key:
                    before = len(r.get("phones") or [])
                    r["phones"] = [p for p in (r.get("phones") or []) if odb._d10((p.get("number") if isinstance(p, dict) else p) or "") not in t["numbers"]]
                    stripped += before - len(r["phones"])
            if stripped:
                backup(plan_f); json.dump(plan, open(plan_f, "w", encoding="utf8"), indent=1)
            entry["plan_stripped"] = stripped
        open(log_path, "a", encoding="utf8").write(json.dumps(entry) + "\n")
    print(f"\n{'removed' if a.commit else 'would remove'} {removed_total if a.commit else sum(len(t['numbers']) for t in targets.values())} numbers; log {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
