"""Register of Wills Estate Search lookups by estate number (read-only).
For each MD estate on the hold list: search #txtEstateNo (+county), take the
grid row's RecordId, fetch the detail page -> status, dates, personal reps.
Results cached; nothing written to the account."""
import sys, json, os, re, glob

ROOT = r"c:\Users\pc\Desktop\Galal Development\Sift Stack"
sys.path.insert(0, ROOT + r"\src\scripts")
OUT = ROOT + r"\output\dp_1122branch"

from md_register_of_wills_pull import (  # noqa: E402
    firecrawl_scrape_retry, fetch_estate_detail, parse_estate_search_grid,
    _set_value_js, COUNTY_ID, ESTATE_SEARCH_URL)

def county_key(c):
    c = re.sub(r"\s+county$", "", str(c or "").strip(), flags=re.I).strip()
    for k in COUNTY_ID:
        if k.lower() == c.lower():
            return k
    return None

# ── collect the MD estates to check ──
jobs = {}   # estate -> {county, streets, decedents, source}
work = json.load(open(OUT + r"\row_check_worklist.json"))
for w in work:
    ests = (w.get("md_estates") or [])
    for e in ests:
        est, county, tab, dec = e[0], e[1], e[2], e[3]
        j = jobs.setdefault(est, {"county": None, "streets": set(), "decedents": set(),
                                  "reasons": set()})
        ck = county_key(county)
        if ck: j["county"] = ck
        j["streets"].add(w["street"]); j["decedents"].add(dec)
        j["reasons"].add(w["hold_reason"])
holds2 = sorted(glob.glob(OUT + r"\pr_repair_holds_pass2_*.json"))
if holds2:
    for w in json.load(open(holds2[-1])):
        for e in (w.get("pr_hits") or []):
            est, county, dec = e[0], e[1], e[3]
            if re.match(r"^\d{4}-", est):   # DC case
                continue
            j = jobs.setdefault(est, {"county": None, "streets": set(), "decedents": set(),
                                      "reasons": set()})
            ck = county_key(county)
            if ck: j["county"] = ck
            j["streets"].add(w["street"]); j["decedents"].add(dec)
            j["reasons"].add("multi_estate_pass2")

print(f"{len(jobs)} MD estates to look up", flush=True)

cache_p = OUT + r"\row_estate_lookup_cache.json"
cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}

for est, j in sorted(jobs.items()):
    if est in cache:
        continue
    county = j["county"]
    script = _set_value_js("#txtEstateNo", est)
    if county:
        script += _set_value_js("#cboCountyId", COUNTY_ID[county])
    actions = [
        {"type": "wait", "milliseconds": 2000},
        {"type": "executeJavascript", "script": script},
        {"type": "wait", "milliseconds": 1200},
        {"type": "click", "selector": "#cmdSearch"},
        {"type": "wait", "milliseconds": 4500},
        {"type": "scrape"},
    ]
    try:
        htmls = firecrawl_scrape_retry(ESTATE_SEARCH_URL, actions)
        rows = parse_estate_search_grid(htmls[-1], county or "")
        entry = {"estate": est, "county_searched": county,
                 "streets": sorted(j["streets"]), "decedents": sorted(j["decedents"]),
                 "reasons": sorted(j["reasons"]), "grid_rows": rows}
        if len(rows) == 1 and rows[0].get("record_id"):
            entry["detail"] = fetch_estate_detail(rows[0]["record_id"])
        elif len(rows) > 1:
            # estate number collided across counties; keep the grid, match on decedent later
            for r in rows:
                if r.get("record_id"):
                    entry.setdefault("details", []).append(fetch_estate_detail(r["record_id"]))
        cache[est] = entry
        got = entry.get("detail") or {}
        print(f"  {est} [{county}]: rows={len(rows)} status={got.get('status','')} "
              f"opened={got.get('date_opened','')} PRs={len(got.get('personal_reps') or [])}",
              flush=True)
    except Exception as e:  # noqa: BLE001
        cache[est] = {"estate": est, "error": str(e)[:300]}
        print(f"  {est}: ERROR {str(e)[:120]}", flush=True)
    json.dump(cache, open(cache_p, "w"), indent=1)

print("DONE", len(cache), "estates cached ->", cache_p)
