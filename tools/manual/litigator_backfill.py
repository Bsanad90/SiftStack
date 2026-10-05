"""Litigator backfill (Basem 2026-10-05: ALWAYS litigator scrub).

Numbers our DP pushers loaded on live records (push_plan phones of records whose push_log
entry committed with phones 200) that no Trestle cache ever checked for litigator risk
(litigator None) are re-scored WITH the add-on into one central, resumable cache.

  python litigator_backfill.py plan      # compute the set, print count + cost, write numbers
  python litigator_backfill.py run       # Trestle calls (threaded, resumable), write results
"""
import json, os, re, sys, time, pathlib
from concurrent.futures import ThreadPoolExecutor, as_completed
ROOT = pathlib.Path(r"C:/Users/pc/Desktop/Galal Development/Sift Stack")
os.chdir(ROOT); sys.path.insert(0, "src"); sys.path.insert(0, "src/scripts")
OUT = ROOT / "output" / "litigator_backfill_20261005"; OUT.mkdir(exist_ok=True)
NUMS = OUT / "numbers.json"; CACHE = OUT / "trestle_litigator_cache.json"; MAP = OUT / "number_to_records.json"

def pushed_keys(rd: pathlib.Path) -> set:
    keys = set()
    for lg in rd.glob("push*.jsonl"):
        for line in open(lg, encoding="utf8", errors="replace"):
            try: e = json.loads(line)
            except Exception: continue
            if e.get("commit") and ((e.get("steps") or {}).get("phones") or {}).get("status") == 200:
                keys.add(e.get("key"))
    return keys

def plan():
    checked = set()
    for f in ROOT.glob("output/**/trestle_cache.json"):
        try: d = json.load(open(f, encoding="utf8"))
        except Exception: continue
        for n, v in d.items():
            if isinstance(v, dict) and v.get("litigator") is not None: checked.add(n)
    want = {}; per = {}
    for plan_f in sorted(ROOT.glob("output/**/push_plan.json")):
        rd = plan_f.parent
        if "backup" in str(rd) or "correction" in str(rd): continue
        keys = pushed_keys(rd)
        if not keys: continue
        d = json.load(open(plan_f, encoding="utf8"))
        recs = d.get("records", d) if isinstance(d, dict) else d
        if isinstance(recs, dict): recs = list(recs.values())
        c = 0
        for r in recs:
            if not isinstance(r, dict) or r.get("key") not in keys: continue
            for p in r.get("phones") or []:
                n = p.get("number") if isinstance(p, dict) else p
                if isinstance(n, str) and re.fullmatch(r"\d{10}", n) and n not in checked:
                    want.setdefault(n, []).append({"run": str(rd.relative_to(ROOT)), "key": r.get("key"), "street": r.get("street")}); c += 1
        per[str(rd.relative_to(ROOT))] = (len(keys), c)
    for k, (nk, c) in sorted(per.items(), key=lambda x: -x[1][1]):
        print(f"{nk:5d} pushed recs  {c:6d} unchecked numbers  {k}")
    print(f"\nUNCHECKED numbers on live records: {len(want)}  (~${0.015*len(want):.0f} + add-on);"
          f" already checked somewhere: {len(checked)}")
    json.dump(sorted(want), open(NUMS, "w")); json.dump(want, open(MAP, "w"), indent=0)

def run():
    import phone_validator as pv
    key = os.environ.get("TRESTLE_PAID_API_KEY") or os.environ.get("TRESTLE_API_KEY") or ""
    if not key:
        from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
        key = os.environ.get("TRESTLE_PAID_API_KEY") or os.environ.get("TRESTLE_API_KEY") or ""
    assert key, "no Trestle key"
    nums = json.load(open(NUMS)); cache = json.load(open(CACHE)) if CACHE.exists() else {}
    todo = [n for n in nums if n not in cache or cache[n].get("error")]
    print(f"{len(nums)} numbers, {len(cache)} cached, {len(todo)} to call", flush=True)
    def one(n):
        d = pv.call_trestle(n, key)
        if not isinstance(d, dict) or (d.get("error") and not d.get("phone_number")):
            return n, {"error": str((d or {}).get("error") or d)[:120]}
        lit = ((d.get("add_ons") or {}).get("litigator_checks") or {}).get("phone.is_litigator_risk")
        return n, {"activity_score": d.get("activity_score"), "line_type": d.get("line_type"),
                   "is_valid": d.get("is_valid"), "carrier": d.get("carrier") or "", "litigator": lit}
    t0 = time.time(); done = 0; errs = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(one, n) for n in todo]
        for f in as_completed(futs):
            n, r = f.result(); cache[n] = r; done += 1; errs += 1 if r.get("error") else 0
            if done % 250 == 0 or done == len(todo):
                json.dump(cache, open(CACHE, "w"))
                risk = sum(1 for v in cache.values() if v.get("litigator") is True)
                print(f"  {done}/{len(todo)}  {time.time()-t0:.0f}s  errors={errs}  litigator_risk={risk}", flush=True)
            if errs > 50 and errs > done * 0.5:
                json.dump(cache, open(CACHE, "w")); raise SystemExit(f"ABORT: {errs} errors in {done}")
    json.dump(cache, open(CACHE, "w"))
    risk = sorted(n for n, v in cache.items() if v.get("litigator") is True)
    none = sum(1 for v in cache.values() if v.get("litigator") is None and not v.get("error"))
    print(f"DONE: {len(cache)} scored, litigator_risk={len(risk)}, still None={none}, errors={errs}")
    json.dump(risk, open(OUT / "litigator_risk_numbers.json", "w"), indent=0)

{"plan": plan, "run": run}[sys.argv[1]]()
