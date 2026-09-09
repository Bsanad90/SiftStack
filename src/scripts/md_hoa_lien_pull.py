"""Montgomery MD HOA/condo lien pull -- the SiftMap-inexpressible P1 rungs.

Harvests HOA/condo lien filings from MD Land Records (landrec.msa.maryland.gov)
for the doors-per-deal rungs SiftMap cannot filter (manifest gaps,
`data/dpd_siftmap_manifest_24031.json`):

    Montgomery P1-01 Free & Clear + HOA Lien   53.6x lift
    Montgomery P1-03 HOA Lien                  13.5x lift

REVIEW-ONLY: writes CSVs to output/, never touches DataSift.

How (every mechanism proven live 2026-09-08, tools/manual/probe_md_landrec_doctype.py):
 1. The Land Records search form DOES carry an instrument-type criterion
    (#body_tbInstrument + #body_ddlinstrument) and a specific-date-range mode
    (#body_ddlDates value 5 -> #body_tbDateRangeStart/End) -- the FTM
    registry's "name, liber/folio, or tax account only" note was wrong.
    A name is still required, so the county-wide sweep runs as: Corporation
    ownership, name Contains <seed> (CONDOMINIUM / ASSOCIATION / ASSN), party
    = GRANTEE (the lien claimant -- measured: 104 grantee-side vs 1
    grantor-side in the same 60 days), instrument Contains LIEN, date range =
    one month chunk. Rows dedupe across seeds on (date, type, party, book/page).
 2. Each lien row's party cell carries "TAX ID: NNNNNNN" = the SDAT account
    number (zero-pad to 8). The FREE Socrata assessment roll
    (kb22-is2w, account_id join proven exact) turns it into premise address +
    city + zip + district + OWNER-OCCUPANCY CODE ('H' = principal residence,
    'N' = not) -- so the Absentee flag costs nothing. Owner names are
    withheld on Socrata; `--sdat-owners N` optionally runs the expensive
    Firecrawl SDAT lookup for the top-N review rows.
 3. Releases: the same sweep with instrument Contains RELEASE, party Both.
    A lien is dropped when a release row carries the same TAX ID on/after the
    lien's date (docs/api/county-data.md: release filtering is not optional).
    Release rows without a tax id are counted and reported, not guessed at.

Login discipline: Firecrawl re-logs-in every call and rapid logins trip the
site's emailed-access-code (MFA) page -- measured: back-to-back calls failed
where a 30s+ gap succeeded. Calls are paced (--pace, default 45s) and an
MFA-shaped page is retried with a longer backoff before the chunk is marked
failed. Progress checkpoints to the raw JSON after every chunk, and --resume
re-runs only the failed/missing chunks.

    python src/scripts/md_hoa_lien_pull.py                    # 12 months
    python src/scripts/md_hoa_lien_pull.py --months 2         # small probe
    python src/scripts/md_hoa_lien_pull.py --resume output/md_hoa_liens_<ts>_raw.json
    python src/scripts/md_hoa_lien_pull.py --sdat-owners 40   # owner names for top rows

Outputs (timestamped, in output/):
    md_hoa_liens_<ts>.csv           the reviewable cohort (absentee first)
    md_hoa_liens_<ts>_dropped.csv   released / unjoinable rows, with reasons
    md_hoa_liens_<ts>_raw.json      every grid row + chunk states (resume file)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "scripts"))

import md_land_records_lookup as landrec  # noqa: E402

COUNTY = "Montgomery"
SEEDS = ["CONDOMINIUM", "ASSOCIATION", "ASSN"]
TAX_ID_RE = re.compile(r"TAX ID:\s*(\d{5,10})")
PARTY_NAME_RE = re.compile(r"^\s*Grant(?:or|ee):\s*(.*?)(?:\s*TAX ID:.*)?$", re.S)

SOCRATA_URL = "https://opendata.maryland.gov/resource/kb22-is2w.json"
SOCRATA_SELECT = ",".join([
    "account_id_mdp_field_acctid",
    "record_key_district_ward_sdat_field_2",
    "record_key_account_number_sdat_field_3",
    "record_key_owner_occupancy_code_mdp_field_ooi_sdat_field_6",
    "mdp_street_address_mdp_field_address",
    "mdp_street_address_city_mdp_field_city",
    "mdp_street_address_zip_code_mdp_field_zipcode",
    "premise_address_condominium_unit_no_sdat_field_28",
])

MFA_BACKOFFS = [90, 180, 300]  # seconds; then the chunk is marked failed


def say(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ── one Land Records sweep (one seed x one month x one instrument) ─────────

def sweep_actions(creds: dict, seed: str, instrument: str, party_sel: str,
                  start: str, end: str) -> list:
    return landrec._login_actions(creds) + [
        {"type": "executeJavascript",
         "script": landrec._set_select_js("#body_ddlbarcounties",
                                          landrec.LANDREC_COUNTY_ID[COUNTY])},
        {"type": "wait", "milliseconds": 4000},
        {"type": "executeJavascript",
         "script": landrec._set_select_js("#body_ddlLastName", "Contains")},
        {"type": "click", "selector": "#body_tbLastName"},
        {"type": "write", "text": seed},
        {"type": "executeJavascript",
         "script": landrec._check_radio_js("#body_rdlOwnership_1")},  # Corporation
        {"type": "executeJavascript", "script": landrec._check_radio_js(party_sel)},
        {"type": "executeJavascript",
         "script": landrec._set_select_js("#body_ddlinstrument", "Contains")},
        {"type": "click", "selector": "#body_tbInstrument"},
        {"type": "write", "text": instrument},
        {"type": "executeJavascript",
         "script": landrec._set_select_js("#body_ddlDates", "5")},  # specific range
        {"type": "wait", "milliseconds": 1500},
        {"type": "executeJavascript",
         "script": landrec._set_text_js("#body_tbDateRangeStart", start)},
        {"type": "executeJavascript",
         "script": landrec._set_text_js("#body_tbDateRangeEnd", end)},
        {"type": "executeJavascript",
         "script": landrec._set_select_js("#body_SortByID", "2")},  # date desc
        {"type": "click", "selector": "#body_btnSubmit"},
        {"type": "wait", "milliseconds": 6000},
        {"type": "scrape"},
    ]


COUNT_RE = re.compile(r"(\d[\d,]*)\s+records?", re.I)


def run_sweep(creds: dict, seed: str, instrument: str, party_sel: str,
              start: str, end: str) -> list[dict]:
    """One sweep with MFA-aware retries. Raises on final failure."""
    for attempt, backoff in enumerate([0] + MFA_BACKOFFS):
        if backoff:
            say(f"    login-page bounce; backing off {backoff}s "
                f"(attempt {attempt + 1}/{len(MFA_BACKOFFS) + 1})")
            time.sleep(backoff)
        html = landrec.firecrawl_scrape_retry(
            landrec.LANDREC_LOGIN_URL,
            sweep_actions(creds, seed, instrument, party_sel, start, end),
            timeout=150)
        try:
            landrec._assert_logged_in(html)
        except RuntimeError:
            continue  # MFA/login page -- back off and retry
        rows = landrec.parse_grantee_results(html)
        m = COUNT_RE.search(html)
        claimed = int(m.group(1).replace(",", "")) if m else None
        if claimed is not None and claimed != len(rows):
            # the grid may paginate past some row count -- fail the chunk
            # loudly so it gets re-run at a narrower window, never truncated
            raise RuntimeError(f"parsed {len(rows)} rows but the page claims "
                               f"{claimed} records -- narrow the window")
        return rows
    raise RuntimeError("login kept landing on the access-code page after "
                       f"{len(MFA_BACKOFFS)} backoffs -- resume later with --resume")


# ── raw-row handling ────────────────────────────────────────────────────────

def row_key(r: dict) -> str:
    return "|".join((r.get("date", ""), r.get("instrument_type", ""),
                     r.get("party", ""), r.get("book_page", "")))


def classify(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split raw grid rows into liens and releases by INSTRUMENT TYPE value
    (a 'Contains LIEN' query also returns e.g. 'RELEASE OF LIEN' rows)."""
    liens, releases = [], []
    for r in rows:
        t = (r.get("instrument_type") or "").upper()
        if "RELEASE" in t or "SATISFACTION" in t:
            releases.append(r)
        elif "LIEN" in t:
            liens.append(r)
    return liens, releases


def extract_fields(r: dict) -> dict:
    party = r.get("party") or ""
    m = TAX_ID_RE.search(party)
    tax_id = m.group(1).zfill(8) if m else ""
    nm = PARTY_NAME_RE.match(party)
    return {**r, "tax_id": tax_id,
            "association": (nm.group(1).strip() if nm else party)[:120]}


def apply_releases(liens: list[dict], releases: list[dict]) -> tuple[list[dict], list[dict], int]:
    rel_by_tax: dict[str, list[str]] = {}
    unmatched = 0
    for r in releases:
        if r.get("tax_id"):
            rel_by_tax.setdefault(r["tax_id"], []).append(r.get("date", ""))
        else:
            unmatched += 1
    kept, dropped = [], []
    for l in liens:
        dates = rel_by_tax.get(l.get("tax_id") or "?", [])
        if l.get("tax_id") and any(d >= l.get("date", "") for d in dates):
            dropped.append({**l, "drop_reason": "released (tax-id match, later date)"})
        else:
            kept.append(l)
    return kept, dropped, unmatched


# ── Socrata join (free) ─────────────────────────────────────────────────────

def socrata_by_account(tax_id: str) -> dict | None:
    resp = requests.get(SOCRATA_URL, params={
        "$where": f"record_key_account_number_sdat_field_3 = '{tax_id}'",
        "$select": SOCRATA_SELECT, "$limit": 3}, timeout=45)
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if isinstance(rows, list) and len(rows) == 1 else None


def join_roll(lien: dict) -> dict:
    out = dict(lien)
    if not lien.get("tax_id"):
        out["join_status"] = "no_tax_id_in_index"
        return out
    try:
        p = socrata_by_account(lien["tax_id"])
    except Exception as e:  # noqa: BLE001
        out["join_status"] = f"socrata_error: {type(e).__name__}: {e}"
        return out
    if not p:
        out["join_status"] = "no_roll_row"
        return out
    ooi = (p.get("record_key_owner_occupancy_code_mdp_field_ooi_sdat_field_6") or "").strip()
    out.update({
        "join_status": "ok",
        "premise_address": p.get("mdp_street_address_mdp_field_address") or "",
        "premise_city": p.get("mdp_street_address_city_mdp_field_city") or "",
        "premise_zip": p.get("mdp_street_address_zip_code_mdp_field_zipcode") or "",
        "unit": p.get("premise_address_condominium_unit_no_sdat_field_28") or "",
        "district": p.get("record_key_district_ward_sdat_field_2") or "",
        "occupancy_code": ooi,
        "absentee": ooi == "N",
        "principal_residence": ooi == "H",
    })
    return out


# ── optional SDAT owner names (Firecrawl, expensive -- final rows only) ─────

def sdat_owner(lien: dict) -> dict:
    try:
        res = landrec.lookup_sdat(COUNTY, lien.get("district", ""), lien["tax_id"])
    except Exception as e:  # noqa: BLE001
        return {"sdat_status": f"error: {type(e).__name__}: {e}"}
    if not res or not res.get("owner_names"):
        return {"sdat_status": "not_found"}
    return {"sdat_status": "ok",
            "owner_name": "; ".join(res["owner_names"])[:150],
            "mailing_address": res.get("mailing_address", ""),
            "sdat_principal_residence": res.get("principal_residence", "")}


# ── output ──────────────────────────────────────────────────────────────────

MAIN_COLS = ["absentee", "occupancy_code", "date", "instrument_type", "association",
             "premise_address", "premise_city", "premise_zip", "unit",
             "owner_name", "mailing_address", "sdat_principal_residence",
             "tax_id", "district", "book_page", "remarks", "join_status",
             "sdat_status", "seed"]


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    say(f"wrote {path} ({len(rows)} rows)")


def month_chunks(months: int) -> list[tuple[str, str]]:
    out = []
    end = datetime.now()
    for _ in range(months):
        start = end - timedelta(days=30)
        out.append((start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
        end = start - timedelta(days=1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--seeds", default=",".join(SEEDS))
    ap.add_argument("--pace", type=int, default=45,
                    help="seconds between Firecrawl calls (login pacing)")
    ap.add_argument("--resume", help="prior *_raw.json: re-run failed/missing chunks")
    ap.add_argument("--sdat-owners", type=int, default=0,
                    help="run the Firecrawl SDAT owner lookup on the first N "
                         "review rows (absentee first); 0 = skip")
    a = ap.parse_args()

    seeds = [s.strip().upper() for s in a.seeds.split(",") if s.strip()]
    if a.resume:
        raw_path = Path(a.resume)
        state = json.loads(raw_path.read_text(encoding="utf-8"))
        say(f"resuming {raw_path}")
    else:
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        raw_path = ROOT / "output" / f"md_hoa_liens_{ts}_raw.json"
        state = {"at": ts, "county": COUNTY, "months": a.months, "seeds": seeds,
                 "chunks": {}}

    creds = landrec.load_credentials()
    plan = [(seed, instrument, party, start, end)
            for start, end in month_chunks(a.months)
            for seed in seeds
            for instrument, party in (("LIEN", "#body_rdlParty_2"),
                                      ("RELEASE", "#body_rdlParty_0"))]

    first_call = True
    for seed, instrument, party, start, end in plan:
        key = f"{seed}|{instrument}|{start}"
        if state["chunks"].get(key, {}).get("ok"):
            continue
        if not first_call:
            time.sleep(a.pace)
        first_call = False
        say(f"chunk {key} ({start}..{end})")
        try:
            rows = run_sweep(creds, seed, instrument, party, start, end)
            state["chunks"][key] = {"ok": True, "n": len(rows), "rows": rows,
                                    "seed": seed, "instrument": instrument}
            say(f"  ok: {len(rows)} rows")
        except Exception as e:  # noqa: BLE001 - checkpoint and keep going
            state["chunks"][key] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            say(f"  FAILED: {e}")
        raw_path.write_text(json.dumps(state, indent=1), encoding="utf-8")

    failed = [k for k, v in state["chunks"].items() if not v.get("ok")]
    if failed:
        say(f"{len(failed)} chunks failed -- re-run with --resume {raw_path}")

    # assemble: dedupe across seeds, tag each row with the seed that found it
    seen: dict[str, dict] = {}
    for v in state["chunks"].values():
        if not v.get("ok"):
            continue
        for r in v["rows"]:
            k = row_key(r)
            if k not in seen:
                seen[k] = {**extract_fields(r), "seed": v["seed"]}
    all_rows = list(seen.values())
    liens, releases = classify(all_rows)
    say(f"{len(all_rows)} unique rows -> {len(liens)} liens, {len(releases)} releases")

    kept, dropped, unmatched_rel = apply_releases(liens, releases)
    say(f"release filter: {len(liens)} -> {len(kept)} kept, {len(dropped)} released"
        f" ({unmatched_rel} release rows carried no tax id and matched nothing)")

    joined = []
    for i, l in enumerate(kept, 1):
        r = join_roll(l)
        (joined if r["join_status"] == "ok" else dropped).append(
            r if r["join_status"] == "ok" else {**r, "drop_reason": r["join_status"]})
        if i % 50 == 0:
            say(f"  roll join {i}/{len(kept)}")
        time.sleep(0.3)

    joined.sort(key=lambda r: (not r.get("absentee"), r.get("date", "")))
    if a.sdat_owners:
        say(f"SDAT owner lookup on the first {a.sdat_owners} rows (paced)")
        for r in joined[:a.sdat_owners]:
            time.sleep(a.pace)
            r.update(sdat_owner(r))

    base = str(raw_path).replace("_raw.json", "")
    n_abs = sum(1 for r in joined if r.get("absentee"))
    write_csv(Path(f"{base}.csv"), joined, MAIN_COLS)
    write_csv(Path(f"{base}_dropped.csv"), dropped,
              ["drop_reason"] + [c for c in MAIN_COLS if c not in ("join_status",)])
    say(f"DONE: {len(joined)} HOA-liened properties ({n_abs} absentee), "
        f"{len(dropped)} dropped, {len(failed)} chunks still failed")
    return 3 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
