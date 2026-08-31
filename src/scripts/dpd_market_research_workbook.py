"""
dpd_market_research_workbook.py

Per-county market-research workbook for the 14 MD/DC/VA target jurisdictions,
mirroring the layout of `Knox_County_TN_Market_Research.xlsx` (Ty's Drive).

Layers
  Market Finder : output/dpd_market_finder/<slug>.json  (ZIP + neighborhood, pulled 2026-08-25)
                  data/dpd_dc_geography.json            (DC ZIP/neighbourhood list, SiftMap)
  Economic      : DataUSA (2024 ACS) + BLS LAUS + Redfin  -- researched 2026-08, in RESEARCH{}
  Crime         : local PD / sheriff year-end 2025 + CrimeGrade percentiles -- in RESEARCH{}
  Doors/deal    : county-compare-*.xlsx (Jan-Jun 2026)  -- in DPD{}

Framework is Knox's.  Where Knox uses a TN-calibrated HARD LIMIT that does not
transfer (the $350K / $400K price-accessibility cutoffs in the wholesaling
score), the cutoff is re-based to THIS county's own median active-ZIP value.

Output : output/market_research/<County>_<ST>_Market_Research.xlsx  (x14)
         output/market_research/_RANKING.md                         (re-rank on all numbers)
"""
from __future__ import annotations
import json, os, statistics, datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MF_DIR = os.path.join(ROOT, "output", "dpd_market_finder")
OUT_DIR = os.path.join(ROOT, "output", "market_research")
FRED_DOM_BASELINE = 78          # FRED MEDDAYONMARUS, last value on the Knox sheet (Feb 2026); national, not TN-specific
GEN_DATE = datetime.date.today().strftime("%B %d, %Y")

SLUG = {
 "Montgomery, MD":"Montgomery__MD","Anne Arundel, MD":"Anne_Arundel__MD","Frederick, MD":"Frederick__MD",
 "Carroll, MD":"Carroll__MD","Calvert, MD":"Calvert__MD","Charles, MD":"Charles__MD",
 "Baltimore County, MD":"Baltimore_County__MD","Washington, DC":"District_of_Columbia",
 "Fairfax, VA":"Fairfax__VA","Prince William, VA":"Prince_William__VA","Arlington, VA":"Arlington__VA",
 "Stafford, VA":"Stafford__VA","Spotsylvania, VA":"Spotsylvania__VA","Fredericksburg City, VA":"Fredericksburg_City__VA",
}

# doors-per-deal workbook (Jan-Jun 2026, SFR off-market investor sales)
DPD = {
 "Baltimore County, MD":dict(deals=1065, base=166.3, gross=51000, margin=19.8, inst=24.9, regime="judicial"),
 "Montgomery, MD":dict(deals=556, base=332.2, gross=52000, margin=8.9, inst=8.5, regime="judicial"),
 "Anne Arundel, MD":dict(deals=552, base=268.1, gross=62000, margin=14.6, inst=21.9, regime="judicial"),
 "Frederick, MD":dict(deals=249, base=274.1, gross=64000, margin=13.3, inst=32.5, regime="judicial"),
 "Carroll, MD":dict(deals=107, base=486.6, gross=82350, margin=25.8, inst=14.0, regime="judicial"),
 "Calvert, MD":dict(deals=93, base=348.8, gross=80850, margin=24.5, inst=39.8, regime="judicial"),
 "Charles, MD":dict(deals=206, base=243.0, gross=102000, margin=31.4, inst=46.6, regime="judicial"),
 "Washington, DC":dict(deals=632, base=76.8, gross=111250, margin=18.7, inst=11.6, regime="non-judicial"),
 "Prince William, VA":dict(deals=396, base=216.3, gross=71000, margin=14.4, inst=39.4, regime="non-judicial"),
 "Fairfax, VA":dict(deals=666, base=290.7, gross=36000, margin=5.1, inst=7.8, regime="non-judicial"),
 "Arlington, VA":dict(deals=120, base=250.7, gross=41000, margin=4.5, inst=5.0, regime="non-judicial"),
 "Stafford, VA":dict(deals=176, base=246.2, gross=71000, margin=17.8, inst=37.5, regime="non-judicial"),
 "Spotsylvania, VA":dict(deals=152, base=326.5, gross=92500, margin=23.1, inst=14.5, regime="non-judicial"),
 "Fredericksburg City, VA":dict(deals=25, base=283.5, gross=62000, margin=12.7, inst=0.0, regime="non-judicial"),
}

# researched 2026-08.  pop_g / inc_yoy / val_yoy / price_yoy are percent.  hom = homeownership %.
# crime_dir: +1 improving, 0 flat, -1 worsening.  fed_risk: 0 low .. 2 high (federal-workforce exposure).
RESEARCH = {
 "Spotsylvania, VA":dict(pop=146603, pop_g=0.78, age=38.6, inc=112738, inc_yoy=2.89, pov=7.1, hom=80.1,
   val=404900, val_yoy=7.97, ind=["Public Administration","Health Care & Social Assistance","Retail Trade"],
   unemp="~3.5% (BLS LAUS, mid-2026)", redfin_price=466102, price_yoy=-1.1, redfin_dom=None,
   redfin_note="median sale $466,102 (May 2026, Redfin, all home types)",
   crime="Violent-crime rate 1.99/1,000 - 92nd percentile for safety (CrimeGrade). Regional violent crime fell in 2025.",
   crime_dir=1, safety_pct=92, hom_2025=None, hom_2024=None, fed_risk=1,
   src_crime="CrimeGrade.org / NeighborhoodScout; Fredericksburg-area 2025 regional decline"),
 "Anne Arundel, MD":dict(pop=598166, pop_g=1.25, age=None, inc=124911, inc_yoy=3.81, pov=5.64, hom=75.1,
   val=467900, val_yoy=None, ind=["Professional, Scientific & Technical Services","Public Administration","Health Care & Social Assistance"],
   unemp="~3.6% (BLS LAUS, mid-2026)", redfin_price=520938, price_yoy=4.2, redfin_dom=30,
   redfin_note="median sale $520,938 (May 2026, Redfin, +4.2% YoY); DOM ~30",
   crime="11 homicides in 2025 (10 in 2024, a decade low). Citizen robberies -47%, commercial robberies -46%, stolen autos -30%, carjackings -27%, non-fatal shootings -26%. AACPD: 'major reduction in violent crime'.",
   crime_dir=1, safety_pct=None, hom_2025=11, hom_2024=10, fed_risk=1,
   src_crime="Anne Arundel County PD 2025 year-end report (Jan 2026)"),
 "Carroll, MD":dict(pop=175321, pop_g=0.22, age=None, inc=118211, inc_yoy=2.02, pov=5.21, hom=84.3,
   val=434000, val_yoy=6.79, ind=["Health Care & Social Assistance","Educational Services","Professional, Scientific & Technical Services"],
   unemp="4.0% (May 2026, MD DoL)", redfin_price=478565, price_yoy=-0.3, redfin_dom=None,
   redfin_note="median sale $478,565 (May 2026, Redfin, -0.3% YoY)",
   crime="Zero homicides in 2025 (MSP + Westminster PD). MSP reported -22% criminal activity; Westminster PD -17% (430 to 355). Violent-crime rate 2.73/1,000 - 73rd percentile.",
   crime_dir=1, safety_pct=73, hom_2025=0, hom_2024=0, fed_risk=0,
   src_crime="MSP Westminster + Westminster PD 2025 year-end (Fox Baltimore, Jan 2026); CrimeGrade.org"),
 "Baltimore County, MD":dict(pop=850796, pop_g=0.51, age=39.7, inc=91768, inc_yoy=0.95, pov=9.78, hom=66.4,
   val=349300, val_yoy=1.5, ind=["Health Care & Social Assistance","Educational Services","Retail Trade"],
   unemp="~4.1% (BLS LAUS, mid-2026)", redfin_price=345000, price_yoy=1.5, redfin_dom=None,
   redfin_note="median sale ~$345K (+1.5% YoY, Redfin); $196/sqft (-0.5% YoY)",
   crime="28 homicides in 2025 - a 5-year low, -49% from 55 in 2021. Non-fatal shootings also at a 5-year low. Homicide clearance 93%, non-fatal-shooting clearance 95% (record highs). BCoPD: 'violent crime hit a five-year low'.",
   crime_dir=1, safety_pct=None, hom_2025=28, hom_2024=None, fed_risk=0,
   src_crime="Baltimore County PD 2025 year-end (NIBRS finalized)"),
 "Washington, DC":dict(pop=681294, pop_g=0.96, age=None, inc=109870, inc_yoy=3.37, pov=15.4, hom=41.5,
   val=737100, val_yoy=1.73, ind=["Professional, Scientific & Technical Services","Public Administration","Other Services"],
   unemp="4.4% DC-metro (Jan 2026, up from 3.4% YoY - federal workforce cuts)", redfin_price=700000, price_yoy=1.0, redfin_dom=44,
   redfin_note="median sale ~$694K-$747K depending on window (roughly flat to +6% YoY, Redfin); DOM 44 (flat YoY)",
   crime="127 homicides in 2025 - -32% YoY, -54% from 274 in 2023; first sub-150 year since 2017. Overall violent crime -29% (robbery -37%, ADW -10%). DOJ: 'violent crime at a 30-year low'. (MPD stat integrity questioned by Congress; department refutes.)",
   crime_dir=1, safety_pct=None, hom_2025=127, hom_2024=187, fed_risk=2,
   src_crime="MPDC 2025 year-end; DOJ USAO-DC; Washington Post (Jan 2026)"),
 "Prince William, VA":dict(pop=250244, pop_g=0.38, age=36.3, inc=131402, inc_yoy=1.96, pov=6.19, hom=74.3,
   val=585000, val_yoy=3.4, ind=["Professional, Scientific & Technical Services","Public Administration","Health Care & Social Assistance"],
   unemp="3.8% (Jan 2026, up YoY - federal workforce cuts)", redfin_price=599450, price_yoy=3.4, redfin_dom=None,
   redfin_note="median sale ~$599K (May 2026, +3.4% YoY, PWAR/Redfin)",
   crime="8 homicides in 2025 - -64% from 22 in 2024 (100% cleared). Violent crime -17.4% (aggravated assault -16%, robbery -23.5%), total crime -9.3%, burglary -26%. PWCPD: 'crime rate plummeted'.",
   crime_dir=1, safety_pct=None, hom_2025=8, hom_2024=22, fed_risk=1,
   src_crime="Prince William County PD 2025 year-end (Feb 2026)"),
 "Montgomery, MD":dict(pop=1058810, pop_g=0.44, age=40.2, inc=132450, inc_yoy=2.89, pov=7.45, hom=65.3,
   val=616000, val_yoy=1.1, ind=["Professional, Scientific & Technical Services","Health Care & Social Assistance","Public Administration"],
   unemp="3.6% (May 2026, MD DoL)", redfin_price=616000, price_yoy=1.1, redfin_dom=None,
   redfin_note="SFH median ~$615K (Aug 2026, +1.1% YoY, Redfin); $303/sqft (+2.4% YoY)",
   crime="24 homicides in 2025 - +20% from 20 in 2024, but still well below the 36 peak of 2021. Annual report pending.",
   crime_dir=-1, safety_pct=None, hom_2025=24, hom_2024=20, fed_risk=2,
   src_crime="Montgomery County PD data (The Baltimore Banner, Jan 2026)"),
 "Fredericksburg City, VA":dict(pop=28873, pop_g=3.55, age=32.1, inc=86071, inc_yoy=0.82, pov=13.2, hom=39.7,
   val=483700, val_yoy=4.81, ind=["Health Care & Social Assistance","Professional, Scientific & Technical Services","Educational Services"],
   unemp="~3.5% (BLS LAUS, mid-2026)", redfin_price=449755, price_yoy=-1.2, redfin_dom=38,
   redfin_note="median sale $449,755 (June 2026, -1.2% YoY, Redfin, city); ~38 days, competitive",
   crime="Violent-crime rate ~604/100,000 (roughly 2x the national ~318). Violent crime +24% YoY, property crime -11%. 5-year trajectory: worsening. High police staffing (3.7/1,000).",
   crime_dir=-1, safety_pct=3, hom_2025=None, hom_2024=None, fed_risk=1,
   src_crime="NeighborhoodScout / CrimeGrade.org (2024-2025); FBI UCR"),
 "Charles, MD":dict(pop=170527, pop_g=0.26, age=38.9, inc=122816, inc_yoy=1.84, pov=6.87, hom=82.1,
   val=428500, val_yoy=6.51, ind=["Public Administration","Health Care & Social Assistance","Professional, Scientific & Technical Services"],
   unemp="~4.3% (BLS LAUS, mid-2026)", redfin_price=433000, price_yoy=-1.9, redfin_dom=61,
   redfin_note="median sale $433K (-1.9% YoY, Redfin); DOM 61 (was 44 a year ago); 216 sold (was 177)",
   crime="Violent-crime rate 3.33/1,000 - 57th percentile for safety (B-), the weakest of the seven MD counties here. Charles County SO published a 2025 annual report.",
   crime_dir=0, safety_pct=57, hom_2025=None, hom_2024=None, fed_risk=1,
   src_crime="Charles County Sheriff's Office 2025 Annual Report; CrimeGrade.org"),
 "Fairfax, VA":dict(pop=1150000, pop_g=0.51, age=39.1, inc=153637, inc_yoy=2.35, pov=5.94, hom=68.6,
   val=732800, val_yoy=4.73, ind=["Professional, Scientific & Technical Services","Public Administration","Health Care & Social Assistance"],
   unemp="3.8% (Jan 2026, up YoY - federal workforce cuts)", redfin_price=813000, price_yoy=3.4, redfin_dom=None,
   redfin_note="median sale $813,000 (May 2026, +3.4% YoY, Redfin)",
   crime="12 homicides in 2025 - -15% YoY (all cleared). Carjackings -48%, non-fatal shootings -37%, burglaries -27%, robberies -19%. All but one major crime category down.",
   crime_dir=1, safety_pct=None, hom_2025=12, hom_2024=14, fed_risk=2,
   src_crime="Fairfax County PD 2025 Crime Data (WTOP, Jan 2026)"),
 "Frederick, MD":dict(pop=287048, pop_g=0.26, age=39.0, inc=122002, inc_yoy=1.28, pov=6.0, hom=77.0,
   val=464600, val_yoy=6.15, ind=["Professional, Scientific & Technical Services","Health Care & Social Assistance","Educational Services"],
   unemp="~3.7% (BLS LAUS, mid-2026)", redfin_price=445000, price_yoy=-11.0, redfin_dom=None,
   redfin_note="county median sale $445K (Nov 2025, -11% YoY - volatile month; city Frederick only -0.4%); $223/sqft (-0.9%)",
   crime="Violent-crime rate 2.73/1,000 - 73rd percentile (B+). Frederick County SO publishes annual reports.",
   crime_dir=0, safety_pct=73, hom_2025=None, hom_2024=None, fed_risk=1,
   src_crime="CrimeGrade.org; Frederick County Sheriff's Office annual reports"),
 "Stafford, VA":dict(pop=163466, pop_g=2.59, age=None, inc=137807, inc_yoy=3.0, pov=4.67, hom=80.5,
   val=485100, val_yoy=5.73, ind=["Public Administration","Professional, Scientific & Technical Services","Health Care & Social Assistance"],
   unemp="~3.5% (BLS LAUS, mid-2026; NoVA federal exposure)", redfin_price=544317, price_yoy=-5.7, redfin_dom=None,
   redfin_note="median sale $544,317 (May 2026, -5.7% YoY, Redfin)",
   crime="Violent-crime rate 2.13/1,000 - 89th percentile for safety. Overall crime rate 19.15/1,000 - 87th percentile.",
   crime_dir=1, safety_pct=89, hom_2025=None, hom_2024=None, fed_risk=1,
   src_crime="CrimeGrade.org / NeighborhoodScout"),
 "Calvert, MD":dict(pop=94313, pop_g=0.74, age=40.8, inc=133922, inc_yoy=1.41, pov=3.87, hom=87.2,
   val=460200, val_yoy=4.54, ind=["Public Administration","Construction","Educational Services"],
   unemp="4.2% (May 2026, MD DoL)", redfin_price=498494, price_yoy=-6.8, redfin_dom=None,
   redfin_note="median sale $498,494 (June 2026, -6.8% YoY, Redfin)",
   crime="Violent-crime rate 2.46/1,000 - 81st percentile (A-). Lowest total crime rate in the MD county dataset (~739/100,000). Lowest poverty (3.87%) and highest homeownership (87%) of the 14.",
   crime_dir=1, safety_pct=81, hom_2025=None, hom_2024=None, fed_risk=1,
   src_crime="CrimeGrade.org; CrimeByCounty (FBI UCR)"),
 "Arlington, VA":dict(pop=236254, pop_g=-0.35, age=None, inc=142114, inc_yoy=1.39, pov=7.39, hom=41.3,
   val=895000, val_yoy=3.49, ind=["Professional, Scientific & Technical Services","Public Administration","Educational Services"],
   unemp="~3.5% (Jan 2026; heavy federal exposure)", redfin_price=835000, price_yoy=4.4, redfin_dom=None,
   redfin_note="median sale $835K (May 2026, +4.4% YoY, Redfin)",
   crime="Group A offenses -10.9% (11,603 to 10,341). Reported crime fell in 2025 for the first time since 2018, led by property crime, MV theft and robbery drops.",
   crime_dir=1, safety_pct=None, hom_2025=None, hom_2024=None, fed_risk=2,
   src_crime="Arlington County PD 2025 Annual Report (2026); Arlington Patch"),
}

# ---------- styling ----------
H1   = Font(bold=True, size=14, color="1F3864")
H2   = Font(bold=True, size=11, color="FFFFFF")
BOLD = Font(bold=True)
MUTE = Font(italic=True, color="7F7F7F", size=9)
FILL_H2 = PatternFill("solid", fgColor="2E5A9E")
FILL_TH = PatternFill("solid", fgColor="D9E2F3")
FILL_STUB = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="BFBFBF")
BOX  = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")

def _w(ws, widths):
    from openpyxl.utils import get_column_letter
    for i, wd in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = wd

def sec(ws, r, text, span=11):
    c = ws.cell(row=r, column=1, value=text); c.font = H2; c.fill = FILL_H2
    for cc in range(2, span+1): ws.cell(row=r, column=cc).fill = FILL_H2
    return r + 1

def th(ws, r, headers):
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=r, column=i, value=h); c.font = BOLD; c.fill = FILL_TH; c.border = BOX
    return r + 1

def money(v):
    return f"${v:,.0f}" if isinstance(v, (int, float)) and v else ("" if v in (None, "") else v)

def pct(v, plus=True):
    if v is None: return "n/a"
    return (f"{v:+.1f}%" if plus else f"{v:.1f}%")

def spread_pct(mv, sp):
    if not mv or not sp: return None, ""
    if sp > 1_000_000 and sp > 3 * mv: return None, "Outlier"
    p = (sp - mv) / mv * 100
    return p, f"{p:+.1f}%"

def supply_mo(hom, hsold):
    if not hsold: return None, "N/A"
    return hom / hsold, f"{hom/hsold:.1f}"

def wscore(inv, dom, mv, outlier, county_med):
    """Knox rubric STRUCTURE; the price-accessibility cutoff is re-based to this county's own median."""
    if outlier: return "*"
    if not inv: return "-"
    dvn = (dom - FRED_DOM_BASELINE) if dom else 99
    acc      = (mv is not None and county_med and mv <= county_med)          # accessible for THIS market
    very_acc = (mv is not None and county_med and mv <= 0.85 * county_med)   # very accessible for THIS market
    if inv >= 30 and dvn <= -15 and very_acc: return "*****"
    if (inv >= 20 and dvn < 0 and acc) or inv >= 30: return "****"
    if (inv >= 10 and dvn <= 5) or inv >= 20: return "***"
    if 3 <= inv <= 29: return "**"
    if 1 <= inv <= 2: return "*"
    return "-"

STAR = {"*****":"★★★★★","****":"★★★★☆","***":"★★★☆☆","**":"★★☆☆☆","*":"★☆☆☆☆","-":"—"}

def _clamp(x, lo=0.0, hi=1.0): return max(lo, min(hi, x))
def _scale(v, lo, hi):
    if v is None: return 0.0
    return _clamp((v - lo) / (hi - lo))


def county_metrics(juris):
    """Pass 1: derive Market Finder + economic sub-scores so the 14 can be re-ranked together."""
    d = json.load(open(os.path.join(MF_DIR, f"{SLUG[juris]}.json")))
    zd = d.get("zip_data") or d.get("county_rollup") or []
    real = [z for z in zd if (z.get("homes_sold_last_month") or 0) > 0 and (z.get("total_inv_trans_6mo") or 0) >= 3]
    m = dict(juris=juris)
    if real:
        for z in real:
            z["_mos"] = z["homes_on_market"] / z["homes_sold_last_month"]
            mv, sp = z.get("median_home_value"), z.get("median_sale_price")
            z["_spmv"] = sp / mv if (mv and sp) else None
        m["inv6"] = sum(z.get("total_inv_trans_6mo") or 0 for z in real)
        m["dom"] = statistics.median([z["median_days_on_market"] for z in real if z.get("median_days_on_market")])
        m["mhv"] = statistics.median([z["median_home_value"] for z in real if z.get("median_home_value")])
        tot_hom = sum(z.get("homes_on_market") or 0 for z in (d.get("zip_data") or zd))
        tot_hs  = sum(z.get("homes_sold_last_month") or 0 for z in (d.get("zip_data") or zd))
        m["mos"] = tot_hom / tot_hs if tot_hs else None
        m["spmv"] = statistics.median([z["_spmv"] for z in real if z.get("_spmv") and 0.3 < z["_spmv"] < 3])
        wk = [z for z in real if z.get("median_home_value") and 150000 <= z["median_home_value"] <= 560000]
        m["lane"] = round(100 * len(wk) / len(real))
    else:
        m.update(inv6=0, dom=None, mhv=None, mos=None, spmv=None, lane=0)

    dp = DPD[juris]; rs = RESEARCH[juris]
    # Market Finder sub-score
    if m["dom"]:
        s_supply = _scale(-(m["mos"] or 5), -5.0, -1.5)
        s_dom    = _scale(-(m["dom"]), -70, -30)
        s_lane   = m["lane"] / 100.0
        iv = m["inv6"]
        s_vol = _scale(iv, 40, 500) if iv <= 500 else max(0.2, 1 - _scale(iv, 500, 1200) * 0.6)
        s_price = _scale(-(m["spmv"] or 1.1), -1.10, -0.85)
        m["mf"] = round((s_supply + s_dom + s_lane + s_vol + s_price) / 5, 3)
    else:
        m["mf"] = 0.30
    # deal-economics sub-score
    m["econ"] = round((_scale(dp["margin"], 4, 32) + _scale(dp["gross"], 35000, 112000)
                       + _scale(-dp["inst"], -47, 0) + _scale(-dp["base"], -490, -75)) / 4, 3)
    # fundamentals sub-score (population growth, income, low poverty, price stability, crime direction, federal risk, absentee stock)
    f_growth = _scale(rs["pop_g"], -0.5, 3.0)
    f_income = _scale(rs["inc"], 85000, 155000)
    f_pov    = _scale(-rs["pov"], -16, -3)
    yoy = rs["price_yoy"]
    f_price  = 1.0 if (yoy is not None and -4.0 <= yoy <= 3.0) else _scale(-abs(yoy or 8) , -12, -2)
    f_crime  = {1: 1.0, 0: 0.55, -1: 0.15}[rs["crime_dir"]]
    f_fed    = {0: 1.0, 1: 0.7, 2: 0.4}[rs["fed_risk"]]
    hom = rs["hom"]
    f_stock  = 1.0 - _clamp(abs(hom - 62) / 40)     # ~62% homeownership ideal (renter/absentee stock without being a condo city)
    m["fund"] = round((f_growth + f_income + f_pov + f_price + f_crime + f_fed + f_stock) / 7, 3)
    m["comp"] = round(0.45 * m["mf"] + 0.30 * m["econ"] + 0.25 * m["fund"], 3)
    return m


def build(juris, metrics_by, rank_by):
    d = json.load(open(os.path.join(MF_DIR, f"{SLUG[juris]}.json")))
    st = juris.split(", ")[-1]
    dp = DPD[juris]; rs = RESEARCH[juris]; mm = metrics_by[juris]
    rk = rank_by[juris]                     # (rank, comp, mf, econ, fund)

    dc_mode = not d.get("zip_data")
    if dc_mode and d.get("county_rollup"):
        zrows = rows_from(d["county_rollup"], "zip_code", mm["mhv"])
        nrows = []
        geo = json.load(open(os.path.join(ROOT, "data", "dpd_dc_geography.json")))
        dc_zip_list, dc_nbr_list = geo.get("zips", []), geo.get("neighborhoods", [])
    else:
        zrows = rows_from(d.get("zip_data") or [], "zip_code", mm["mhv"])
        nrows = rows_from(d.get("neighborhood_data") or [], "neighborhood", mm["mhv"])
        dc_zip_list = dc_nbr_list = None

    active_z = [r for r in zrows if r["inv"] > 0]
    active_n = [r for r in nrows if r["inv"] > 0]
    tot_inv6  = sum(r["inv"] for r in zrows)
    tot_hom   = sum(r["hom"] for r in zrows)
    tot_hsold = sum(r["hsold"] for r in zrows)
    med_val   = mm["mhv"]
    mos       = mm["mos"]
    doms      = sorted(r["dom"] for r in active_z if r["dom"] and r["inv"] >= 10)
    dom_lo, dom_hi = (doms[0], doms[-1]) if doms else (None, None)

    wb = Workbook()

    # ============ 1. EXECUTIVE SUMMARY ============
    ws = wb.active; ws.title = "Executive Summary"; _w(ws, [36, 24, 66, 4, 4])
    ws["A1"] = f"{juris.upper()} - COMPREHENSIVE MARKET RESEARCH REPORT"; ws["A1"].font = H1
    ws["A2"] = f"Generated: {GEN_DATE}  |  Property type: Single-Family Residential  |  Market data: REI Sift Market Finder (pulled 2026-08-25)"; ws["A2"].font = MUTE
    ws["A3"] = "Sources: REI Sift Market Finder + FRED + BLS LAUS + Census/DataUSA (2024 ACS) + Redfin + local PD/Sheriff (2025)"; ws["A3"].font = MUTE
    r = 5
    r = sec(ws, r, "A. COUNTY OVERVIEW")
    r = th(ws, r, ["Metric", "Value", "Notes"])
    ov = [
        ("Population", f"{rs['pop']:,}", f"{pct(rs['pop_g'])} 1-yr (DataUSA 2024 ACS)"),
        ("Median Household Income", money(rs["inc"]), f"{pct(rs['inc_yoy'])} 1-yr (DataUSA 2024 ACS)"),
        ("Poverty Rate", pct(rs["pov"], plus=False), "DataUSA 2024 ACS"),
        ("Unemployment Rate", rs["unemp"], "BLS LAUS"),
        ("Homeownership Rate", pct(rs["hom"], plus=False), f"renters {100-rs['hom']:.1f}% (DataUSA 2024 ACS)"),
        ("Median Property Value (ACS)", money(rs["val"]), (f"{pct(rs['val_yoy'])} 1-yr" if rs["val_yoy"] else "DataUSA 2024 ACS")),
        ("Median Home Value (active ZIPs)", money(med_val), "median of ZIPs with investor activity - REI Sift Market Finder"),
        ("Median Sale Price trend (Redfin)", (f"{money(rs['redfin_price'])}  ({pct(rs['price_yoy'])} YoY)" if rs["redfin_price"] else "n/a"), rs["redfin_note"]),
        ("Homes on Market / Sold per mo", f"{tot_hom:,} / {tot_hsold:,}", "sum across all ZIPs - REI Sift Market Finder"),
        ("Mo. Investor Transactions", f"{tot_inv6/6:.0f}", f"6-month total {tot_inv6} / 6 - REI Sift Market Finder"),
        ("Months of Supply", (f"{mos:.1f}" if mos else "N/A"),
         (f"{tot_hom:,} / {tot_hsold:,} = " + ("seller's (<3)" if mos and mos < 3 else "balanced (3-6)" if mos and mos <= 6 else "buyer's (>6)")) if mos else "n/a"),
        ("National DOM baseline (FRED)", f"{FRED_DOM_BASELINE} days", "FRED MEDDAYONMARUS (verify - see Data Sources)"),
        ("Local Median DOM (active ZIPs)", (f"{dom_lo}-{dom_hi} days" if dom_lo else "N/A"),
         (f"{FRED_DOM_BASELINE-dom_hi} to {FRED_DOM_BASELINE-dom_lo} days faster than national" if dom_lo and dom_hi and dom_hi < FRED_DOM_BASELINE else "vs 78-day national")),
        ("Total ZIP Codes", f"{len(zrows)}", f"{len(active_z)} with active investor transactions"),
        ("Total Neighborhoods", f"{len(nrows)}", (f"{len(active_n)} active" if nrows else "no sub-county neighbourhood data in Market Finder")),
    ]
    for m_, v_, n_ in ov:
        ws.cell(row=r, column=1, value=m_).border = BOX
        ws.cell(row=r, column=2, value=v_).border = BOX
        ws.cell(row=r, column=3, value=n_).border = BOX; ws.cell(row=r, column=3).alignment = WRAP
        r += 1
    r += 1

    r = sec(ws, r, "B. MARKET ASSESSMENT (core indicators)")
    r = th(ws, r, ["Indicator", "Rating", "Assessment"])
    aff_ratio = rs["val"] / rs["inc"] if rs["inc"] else None
    ind = [
        ("Affordability", ("FAVORABLE" if aff_ratio and aff_ratio < 4.0 else "MODERATE" if aff_ratio and aff_ratio < 5.5 else "STRETCHED"),
         f"median value {money(rs['val'])} vs income {money(rs['inc'])} = {aff_ratio:.1f}x." if aff_ratio else ""),
        ("Market Saturation", ("LOW" if dp["inst"] < 15 else "MODERATE" if dp["inst"] < 30 else "HIGH"),
         f"institutional share {dp['inst']}% of investor deals; baseline {dp['base']} doors/deal."),
        ("Supply & Demand", ("SELLER'S" if mos and mos < 3 else "BALANCED" if mos and mos <= 6 else "BUYER'S"),
         f"{mos:.1f} months of supply." if mos else "n/a"),
        ("Prices & Appreciation", ("RISING" if (rs["price_yoy"] or 0) > 3 else "COOLING" if (rs["price_yoy"] or 0) < -3 else "STABLE"),
         f"{pct(rs['price_yoy'])} YoY (Redfin). " + ("Cooling = better acquisition." if (rs['price_yoy'] or 0) < 0 else "Rising = safer exit, harder buy.")),
        ("Distress Signal", ("STRONG" if tot_inv6 >= 400 else "MODERATE" if tot_inv6 >= 120 else "THIN"),
         f"{tot_inv6} investor transactions in 6 months across {len(active_z)} active ZIPs."),
        ("Demographics", ("GROWING" if rs["pop_g"] > 0.6 else "FLAT" if rs["pop_g"] > -0.2 else "SHRINKING"),
         f"{pct(rs['pop_g'])} 1-yr population; median age {rs['age'] if rs['age'] else 'n/a'}."),
        ("Employment", ("EXPOSED" if rs["fed_risk"] == 2 else "MIXED" if rs["fed_risk"] == 1 else "DIVERSE"),
         f"unemployment {rs['unemp']}. Top sectors: {', '.join(rs['ind'][:3])}."),
        ("Crime Trend", ("IMPROVING" if rs["crime_dir"] == 1 else "FLAT" if rs["crime_dir"] == 0 else "WORSENING"),
         rs["crime"][:230]),
        ("Government & Taxes", ("MD - judicial foreclosure" if st == "MD" else "DC - non-judicial" if st == "DC" else "VA - non-judicial"),
         ("judicial state: key on Lis Pendens / Final Judgment." if dp["regime"] == "judicial" else "non-judicial: key on Notice of Default / Notice of Foreclosure.")),
        ("Investor Sentiment", ("POSITIVE" if tot_inv6 >= 300 and (dom_hi or 99) < FRED_DOM_BASELINE else "NEUTRAL"),
         f"{tot_inv6/6:.0f} investor buys/mo; active-ZIP DOM {dom_lo}-{dom_hi} vs {FRED_DOM_BASELINE} national." if dom_lo else ""),
    ]
    for a, rt, tx in ind:
        ws.cell(row=r, column=1, value=a).border = BOX
        ws.cell(row=r, column=2, value=rt).border = BOX; ws.cell(row=r, column=2).font = BOLD
        ws.cell(row=r, column=3, value=tx).border = BOX; ws.cell(row=r, column=3).alignment = WRAP
        r += 1
    r += 1

    r = sec(ws, r, "C. 14-JURISDICTION RANKING (this county)")
    r = th(ws, r, ["Metric", "Value", "Notes"])
    for m_, v_, n_ in [
        ("Overall rank (all numbers)", f"#{rk[0]} of 14", "0.45 Market Finder + 0.30 deal economics + 0.25 fundamentals"),
        ("Market Finder sub-score", f"{rk[2]:.2f}", "supply, DOM, price-lane %, sale/value, balanced investor volume"),
        ("Deal-economics sub-score", f"{rk[3]:.2f}", f"margin {dp['margin']}%, gross {money(dp['gross'])}, institutional {dp['inst']}%, baseline {dp['base']} doors/deal"),
        ("Fundamentals sub-score", f"{rk[4]:.2f}", "pop growth, income, poverty, price stability, crime direction, federal-jobs risk, rental stock"),
        ("SFR investor deals (6mo)", f"{dp['deals']:,}", "doors-per-deal workbook, Jan-Jun 2026"),
        ("Foreclosure regime", dp["regime"], "drives which court signal is the real one"),
    ]:
        ws.cell(row=r, column=1, value=m_).border = BOX
        ws.cell(row=r, column=2, value=v_).border = BOX
        ws.cell(row=r, column=3, value=n_).border = BOX; ws.cell(row=r, column=3).alignment = WRAP
        r += 1
    r += 1

    r = sec(ws, r, "D. TOP 5 ZIP CODES (by 6-mo investor transactions)")
    r = th(ws, r, ["Rank", "ZIP", "6-Mo Inv Trans", "Median Home Value", "Median DOM", "Score"])
    for i, z in enumerate(active_z[:5], 1):
        for col, val in enumerate([i, z["name"], z["inv"], money(z["mv"]), z["dom"], z["stars"]], 1):
            ws.cell(row=r, column=col, value=val).border = BOX
        r += 1
    r += 1
    if active_n:
        r = sec(ws, r, "E. TOP 5 NEIGHBORHOODS (by 6-mo investor transactions)")
        r = th(ws, r, ["Rank", "Neighborhood", "6-Mo Inv Trans", "Median Home Value", "Median DOM", "Score"])
        for i, z in enumerate(active_n[:5], 1):
            for col, val in enumerate([i, z["name"], z["inv"], money(z["mv"]), z["dom"], z["stars"]], 1):
                ws.cell(row=r, column=col, value=val).border = BOX
            r += 1
        r += 1

    r = sec(ws, r, "F. SNAPSHOT")
    supply_word = "a seller's market" if mos and mos < 3 else "a balanced market" if mos and mos <= 6 else "a buyer's market"
    snap = (f"{juris}: {tot_inv6} single-family investor transactions in 6 months ({tot_inv6/6:.0f}/mo) across {len(active_z)} active ZIPs, "
            f"{mos:.1f} months of supply ({supply_word}), active-ZIP DOM {dom_lo}-{dom_hi} vs {FRED_DOM_BASELINE} national. "
            f"Median active-ZIP value {money(med_val)}; Redfin county price {pct(rs['price_yoy'])} YoY. "
            f"Population {pct(rs['pop_g'])}/yr, income {money(rs['inc'])}, homeownership {rs['hom']:.0f}%, poverty {rs['pov']:.1f}%. "
            f"Crime {('improving' if rs['crime_dir']==1 else 'flat' if rs['crime_dir']==0 else 'worsening')}. "
            f"Federal-jobs risk {['low','moderate','high'][rs['fed_risk']]}. "
            f"Doors-per-deal: {dp['margin']}% margin, {money(dp['gross'])} gross, {dp['inst']}% institutional, {dp['base']} baseline, {dp['regime']}. "
            f"OVERALL RANK #{rk[0]} of 14 (composite {rk[1]:.2f}).")
    c = ws.cell(row=r, column=1, value=snap); c.alignment = WRAP
    ws.merge_cells(start_row=r, start_column=1, end_row=r + 7, end_column=3)

    # ============ 2. ZIP CODE ANALYSIS ============
    ws = wb.create_sheet("ZIP Code Analysis"); _w(ws, [12, 15, 15, 14, 12, 14, 18, 18, 11, 13, 16])
    ws["A1"] = f"ZIP CODE ANALYSIS - {juris.upper()}"; ws["A1"].font = H1
    ws["A2"] = (f"Sorted by 6-mo investor transactions | FRED baseline {FRED_DOM_BASELINE} days | "
                f"score price-cutoff re-based to this county's median active-ZIP value {money(med_val)}"); ws["A2"].font = MUTE
    if dc_mode:
        ws["A3"] = ("Market Finder publishes NO sub-county metrics for DC (ZIP grid disabled). First row = citywide rollup. "
                    "DC ZIP list + record counts are from the SiftMap geography measurement."); ws["A3"].font = MUTE; ws["A3"].alignment = WRAP
    hr = th(ws, 5, ["ZIP Code", "6-Mo Inv Trans", "Homes on Market", "Homes Sold/Mo", "Median DOM",
                    "DOM vs National", "Median Home Value", "Median Sale Price", "Spread %", "Supply Months", "Wholesaling Score"])
    for z in zrows:
        vals = [z["name"], z["inv"], z["hom"], z["hsold"], z["dom"],
                (f"{z['dvn']:+d}" if z["dvn"] is not None else "N/A"),
                money(z["mv"]), money(z["sp"]), z["spread"], z["supply"], z["stars"]]
        for col, v in enumerate(vals, 1):
            ws.cell(row=hr, column=col, value=v).border = BOX
        hr += 1
    if dc_mode and dc_zip_list:
        hr += 1
        hr = sec(ws, hr, "DC RESIDENTIAL ZIPs - SiftMap record counts (no Market Finder DOM / value / supply)")
        hr = th(ws, hr, ["ZIP", "SiftMap records"] + [""] * 9)
        for z in dc_zip_list:
            ws.cell(row=hr, column=1, value=z["value"]).border = BOX
            ws.cell(row=hr, column=2, value=z["count"]).border = BOX
            hr += 1
    hr += 1
    hr = sec(ws, hr, "WHOLESALING SCORE METHODOLOGY (Knox rubric structure; price cutoff re-based to this county)")
    for a, b in [
        ("★★★★★", f"30+ inv AND DOM 15+ below national AND value <= 0.85x county median ({money(0.85*med_val) if med_val else 'n/a'})"),
        ("★★★★☆", f"20+ inv AND DOM below national AND value <= county median ({money(med_val)}); OR 30+ inv at any price"),
        ("★★★☆☆", "10+ inv AND DOM within 5 days of national; OR 20+ inv at higher prices"),
        ("★★☆☆☆", "3-9 inv, or higher price / slower DOM"),
        ("★☆☆☆☆", "1-2 inv, DOM above national, or outlier data"),
        ("—", "zero investor transactions in 6 months"),
        ("Outlier rule", "median sale > 3x home value AND > $1M => flagged Outlier, forced 1 star"),
        ("Spread %", "(median sale - median value) / median value x 100. Negative = buyers below list (distressed)."),
        ("Supply Months", "homes on market / homes sold last month. <3 seller's, 3-6 balanced, >6 buyer's."),
        ("TN vs state note", "Knox's $350K/$400K cutoffs are TN-calibrated hard limits; MD/DC/VA run higher, so the cutoff here IS the county's own median. Everything else in the rubric is unchanged."),
    ]:
        ws.cell(row=hr, column=1, value=a).border = BOX
        ws.cell(row=hr, column=2, value=b).border = BOX; ws.cell(row=hr, column=2).alignment = WRAP
        ws.merge_cells(start_row=hr, start_column=2, end_column=11, end_row=hr)
        hr += 1

    # ============ 3. NEIGHBORHOOD ANALYSIS ============
    ws = wb.create_sheet("Neighborhood Analysis"); _w(ws, [34, 15, 15, 14, 12, 14, 18, 18, 11, 13, 16])
    ws["A1"] = f"NEIGHBORHOOD ANALYSIS - {juris.upper()}"; ws["A1"].font = H1
    if dc_mode:
        ws["A2"] = "Market Finder has no DC neighbourhood metrics. List + record counts from SiftMap geography measurement."; ws["A2"].font = MUTE
        hr = th(ws, 4, ["Neighborhood", "SiftMap records"] + [""] * 9)
        for z in (dc_nbr_list or []):
            ws.cell(row=hr, column=1, value=z["value"]).border = BOX
            ws.cell(row=hr, column=2, value=z["count"]).border = BOX
            hr += 1
    else:
        ws["A2"] = f"{len(nrows)} neighborhoods | sorted by 6-mo investor transactions | same scoring as the ZIP sheet"; ws["A2"].font = MUTE
        hr = th(ws, 4, ["Neighborhood", "6-Mo Inv Trans", "Homes on Market", "Homes Sold/Mo", "Median DOM",
                        "DOM vs National", "Median Home Value", "Median Sale Price", "Spread %", "Supply Months", "Wholesaling Score"])
        for z in nrows:
            vals = [z["name"], z["inv"], z["hom"], z["hsold"], z["dom"],
                    (f"{z['dvn']:+d}" if z["dvn"] is not None else "N/A"),
                    money(z["mv"]), money(z["sp"]), z["spread"], z["supply"], z["stars"]]
            for col, v in enumerate(vals, 1):
                ws.cell(row=hr, column=col, value=v).border = BOX
            hr += 1

    # ============ 4. ECONOMIC INDICATORS ============
    ws = wb.create_sheet("Economic Indicators"); _w(ws, [40, 26, 62])
    ws["A1"] = f"ECONOMIC INDICATORS - {juris.upper()}"; ws["A1"].font = H1
    ws["A2"] = "DataUSA 2024 ACS + BLS LAUS + Redfin. Verify figures against the linked sources before high-stakes use."; ws["A2"].font = MUTE
    rr = 4
    rr = sec(ws, rr, "A. DEMOGRAPHICS (DataUSA / Census 2024 ACS)")
    rr = th(ws, rr, ["Metric", "Value", "Notes"])
    for m_, v_, n_ in [
        ("Population", f"{rs['pop']:,}", f"{pct(rs['pop_g'])} 1-year"),
        ("Median Age", (str(rs["age"]) if rs["age"] else "n/a"), ""),
        ("Median Household Income", money(rs["inc"]), f"{pct(rs['inc_yoy'])} 1-year"),
        ("Poverty Rate", pct(rs["pov"], plus=False), ""),
        ("Homeownership Rate", pct(rs["hom"], plus=False), f"renters {100-rs['hom']:.1f}%"),
        ("Median Property Value (ACS)", money(rs["val"]), (f"{pct(rs['val_yoy'])} 1-year" if rs["val_yoy"] else "")),
    ]:
        ws.cell(row=rr, column=1, value=m_).border = BOX
        ws.cell(row=rr, column=2, value=v_).border = BOX
        ws.cell(row=rr, column=3, value=n_).border = BOX
        rr += 1
    rr += 1
    rr = sec(ws, rr, "B. EMPLOYMENT (BLS LAUS + DataUSA)")
    rr = th(ws, rr, ["Metric", "Value", "Notes"])
    ws.cell(row=rr, column=1, value="Unemployment Rate").border = BOX
    ws.cell(row=rr, column=2, value=rs["unemp"]).border = BOX
    ws.cell(row=rr, column=3, value=("Federal-workforce exposure: " + ["low", "moderate", "high"][rs["fed_risk"]] +
        ". NoVA/DC-metro jobless rate rose YoY in early 2026 on federal cuts." if rs["fed_risk"] else "")).border = BOX
    ws.cell(row=rr, column=3).alignment = WRAP
    rr += 1
    for i, sector in enumerate(rs["ind"], 1):
        ws.cell(row=rr, column=1, value=f"Top sector {i}").border = BOX
        ws.cell(row=rr, column=2, value=sector).border = BOX
        rr += 1
    rr += 1
    rr = sec(ws, rr, "C. HOUSING MARKET (Redfin, county-level, most recent)")
    rr = th(ws, rr, ["Metric", "Value", "Notes"])
    for m_, v_, n_ in [
        ("Median Sale Price", (money(rs["redfin_price"]) if rs["redfin_price"] else "n/a"), rs["redfin_note"]),
        ("Median Sale Price YoY", pct(rs["price_yoy"]), ("cooling = better acquisition pricing" if (rs["price_yoy"] or 0) < 0 else "rising = safer exit, harder buy")),
        ("Redfin Days on Market", (str(rs["redfin_dom"]) if rs["redfin_dom"] else "see note"), ""),
    ]:
        ws.cell(row=rr, column=1, value=m_).border = BOX
        ws.cell(row=rr, column=2, value=v_).border = BOX
        ws.cell(row=rr, column=3, value=n_).border = BOX; ws.cell(row=rr, column=3).alignment = WRAP
        rr += 1

    # ============ 5. CRIME & SAFETY ============
    ws = wb.create_sheet("Crime & Safety"); _w(ws, [26, 90])
    ws["A1"] = f"CRIME & SAFETY - {juris.upper()}"; ws["A1"].font = H1
    ws["A2"] = "Local PD / Sheriff 2025 year-end + CrimeGrade.org percentiles. Regional context: MD/DC/VA violent crime fell broadly in 2025."; ws["A2"].font = MUTE
    rr = 4
    rr = sec(ws, rr, "A. 2025 SUMMARY", span=2)
    for m_, v_ in [
        ("Trend", {1: "IMPROVING", 0: "FLAT / MIXED", -1: "WORSENING"}[rs["crime_dir"]]),
        ("Homicides 2025", (str(rs["hom_2025"]) if rs["hom_2025"] is not None else "see narrative")),
        ("Homicides prior year", (str(rs["hom_2024"]) if rs["hom_2024"] is not None else "n/a")),
        ("Safety percentile (CrimeGrade)", (f"{rs['safety_pct']}th" if rs["safety_pct"] is not None else "n/a")),
        ("Narrative", rs["crime"]),
        ("Source", rs["src_crime"]),
    ]:
        ws.cell(row=rr, column=1, value=m_).border = BOX; ws.cell(row=rr, column=1).font = BOLD
        ws.cell(row=rr, column=2, value=v_).border = BOX; ws.cell(row=rr, column=2).alignment = WRAP
        rr += 1
    rr += 1
    rr = sec(ws, rr, "B. SAFETY BY ZIP (map to the active ZIPs on the ZIP sheet - fill from the local dashboard)", span=2)
    rr = th(ws, rr, ["Area / ZIPs", "Safety / implication"])
    for z in active_z[:6]:
        ws.cell(row=rr, column=1, value=str(z["name"])).border = BOX
        c = ws.cell(row=rr, column=2, value="fill from local PD crime map"); c.border = BOX; c.fill = FILL_STUB
        rr += 1

    # ============ 6. INVESTMENT RECOMMENDATIONS ============
    ws = wb.create_sheet("Investment Recommendations"); _w(ws, [16, 14, 18, 10, 10, 66])
    ws["A1"] = f"INVESTMENT RECOMMENDATIONS - {juris.upper()}"; ws["A1"].font = H1
    vals_sorted = sorted([z["mv"] for z in active_z if z["mv"]]) if active_z else []
    lo_band = vals_sorted[len(vals_sorted)//3] if vals_sorted else 0
    hi_band = vals_sorted[2*len(vals_sorted)//3] if vals_sorted else 0
    def tier_of(z):
        if z["inv"] >= 30 and (z["dom"] or 99) <= FRED_DOM_BASELINE + 5 and (not z["supply_v"] or 1.5 <= z["supply_v"] <= 7): return 1
        if z["inv"] >= 15: return 2
        if z["inv"] >= 5: return 3
        return 4
    for z in active_z: z["tier"] = tier_of(z)
    rr = 3
    for tnum, label in [(1, "A. TIER 1 - HIGHEST PRIORITY ZIPs"), (2, "B. TIER 2 - SECONDARY"), (3, "C. TIER 3 - THIN / NEEDS CLOSING ABILITY")]:
        rr = sec(ws, rr, label)
        rr = th(ws, rr, ["ZIP", "6-Mo Trans", "Median Value", "DOM", "Score", "Rationale"])
        picked = [z for z in active_z if z["tier"] == tnum]
        for z in picked[:12]:
            rat = []
            if z["inv"] >= 30: rat.append("high activity")
            if z["dom"] and z["dom"] < FRED_DOM_BASELINE: rat.append(f"{FRED_DOM_BASELINE-z['dom']}d below national DOM")
            if z["mv"] and z["mv"] <= lo_band: rat.append("bottom-third price (wholesale lane)")
            elif z["mv"] and z["mv"] > hi_band: rat.append("top-third price (flip/wholetail lane)")
            if z["spread"] and z["spread"].startswith("-"): rat.append(f"{z['spread']} spread")
            for col, v in enumerate([z["name"], z["inv"], money(z["mv"]), z["dom"], z["stars"], "; ".join(rat) or "active market"], 1):
                c = ws.cell(row=rr, column=col, value=v); c.border = BOX
                if col == 6: c.alignment = WRAP
            rr += 1
        if not picked:
            ws.cell(row=rr, column=1, value="(none in this tier)").font = MUTE; rr += 1
        rr += 1
    rr = sec(ws, rr, "D. EXIT STRATEGY BY SEGMENT (price bands relative to this county)")
    rr = th(ws, rr, ["Exit", "Price band", "Best ZIPs", "", "", "Notes"])
    t1 = [z for z in active_z if z["tier"] == 1]
    whole = [z["name"] for z in active_z if z["mv"] and z["mv"] <= lo_band][:6]
    wtail = [z["name"] for z in active_z if z["mv"] and lo_band < z["mv"] <= hi_band][:6]
    flip  = [z["name"] for z in active_z if z["mv"] and z["mv"] > hi_band][:6]
    for ex, band, zs, note in [
        ("Wholesale", f"<= {money(lo_band)}", whole, "assignment on bottom-third-priced active ZIPs"),
        ("Wholetail", f"{money(lo_band)} - {money(hi_band)}", wtail, "clean / paint / carpet + MLS"),
        ("Fix & Flip", f"> {money(hi_band)}", flip, "heavier rehab; wholesale margins thin at this price"),
        ("Buy & Hold", "county-wide", [z["name"] for z in t1[:5]], "verify rent + gross yield from Market Finder summary panel"),
    ]:
        ws.cell(row=rr, column=1, value=ex).border = BOX
        ws.cell(row=rr, column=2, value=band).border = BOX
        ws.cell(row=rr, column=3, value=", ".join(str(x) for x in zs)).border = BOX
        ws.merge_cells(start_row=rr, start_column=3, end_column=5, end_row=rr)
        ws.cell(row=rr, column=6, value=note).border = BOX; ws.cell(row=rr, column=6).alignment = WRAP
        rr += 1
    rr += 1
    rr = sec(ws, rr, "E. ACTION PLAN")
    top6 = active_z[:6]
    share = (sum(z["inv"] for z in top6) / tot_inv6 * 100) if tot_inv6 else 0
    plan = [
        f"1. PRIMARY ZIPs: {', '.join(str(z['name']) for z in t1[:6]) or '(see ZIP sheet - no ZIP cleared Tier 1)'}.",
        f"2. The top 6 ZIPs by volume hold {sum(z['inv'] for z in top6)} of {tot_inv6} transactions ({share:.0f}%).",
        f"3. MOTIVATED SELLERS: prioritise listings above local median DOM ({dom_hi or 'n/a'} days) for leverage.",
        f"4. PRICE TARGET: <= {money(hi_band)} covers the wholesale/wholetail lane in this county.",
        f"5. FORECLOSURE SIGNAL: {dp['regime']} regime -> " + ("Lis Pendens / Final Judgment from the court docket." if dp['regime'] == "judicial" else "Notice of Default / Notice of Foreclosure from the recorder."),
        f"6. RISK WATCH: federal-jobs exposure {['low','moderate','high'][rs['fed_risk']]}; price trend {pct(rs['price_yoy'])} YoY; crime {'improving' if rs['crime_dir']==1 else 'flat' if rs['crime_dir']==0 else 'WORSENING'}.",
        "7. SEQUENTIAL MARKETING: Skip Trace -> Call -> Text -> Direct Mail -> Deep Prospect per Tier 1 ZIP.",
    ]
    for p in plan:
        c = ws.cell(row=rr, column=1, value=p); c.alignment = WRAP
        ws.merge_cells(start_row=rr, start_column=1, end_column=6, end_row=rr)
        rr += 1

    # ============ 7. DATA SOURCES & METHODOLOGY ============
    ws = wb.create_sheet("Data Sources & Methodology"); _w(ws, [30, 42, 58])
    ws["A1"] = "DATA SOURCES & METHODOLOGY"; ws["A1"].font = H1
    rr = 3
    rr = sec(ws, rr, "A. SOURCES")
    rr = th(ws, rr, ["Source", "Data", "Notes"])
    for s, dt, u in [
        ("REI Sift Market Finder", "investor trans, home value, DOM, sale price (ZIP + neighbourhood)", "app.reisift.io - pulled 2026-08-25. No SFR-only filter in Market Finder (deed-based counts)."),
        ("FRED (St. Louis Fed)", f"national median DOM baseline ({FRED_DOM_BASELINE} d)", "fred.stlouisfed.org/series/MEDDAYONMARUS"),
        ("Census / DataUSA", "population, income, poverty, homeownership, industry", "datausa.io - 2024 ACS"),
        ("BLS LAUS", "county unemployment", "bls.gov - local area unemployment statistics"),
        ("Redfin", "county median sale price + YoY + DOM", "redfin.com - most recent month available 2026"),
        ("Local PD / Sheriff", "2025 year-end crime", rs["src_crime"]),
        ("Doors-per-deal workbook", "SFR investor deals, margin, institutional %, baseline doors/deal", "county-compare-*.xlsx (Jan-Jun 2026)"),
    ]:
        ws.cell(row=rr, column=1, value=s).border = BOX
        ws.cell(row=rr, column=2, value=dt).border = BOX; ws.cell(row=rr, column=2).alignment = WRAP
        ws.cell(row=rr, column=3, value=u).border = BOX; ws.cell(row=rr, column=3).alignment = WRAP
        rr += 1
    rr += 1
    rr = sec(ws, rr, "B. METHODOLOGY")
    for a, b in [
        ("Framework", "Knox County (TN) market-research report. Structure and rubric reused as-is."),
        ("TN hard limits re-based", "Knox's wholesaling-score price cutoffs ($350K five-star, $400K four-star) are TN-calibrated. MD/DC/VA medians run $400K-$900K, so the cutoff here is THIS county's own median active-ZIP value (five-star <= 0.85x, four-star <= 1.0x). DOM (vs 78-day national), activity thresholds (30/20/10 inv), supply bands (3/6) and the outlier rule are unchanged - not TN-specific."),
        ("Property type", "Single-Family Residential. Doors-per-deal economics are already SFR; the SiftMap buy box is type_single_family=true. Market Finder has no property-type filter, so its counts include some non-SFR deed activity."),
        ("DC", "state = county = District of Columbia. Market Finder returns only the citywide rollup (ZIP grid disabled); DC ZIP/neighbourhood lists + record counts come from the SiftMap geography measurement."),
        ("Wholesaling Score", "Star rating. Primary: 6-mo investor volume. Secondary: DOM vs 78. Tertiary: value vs county median. Outlier if sale > 3x value AND > $1M."),
        ("Overall rank", "0.45 x Market Finder metrics + 0.30 x deal economics + 0.25 x fundamentals (pop growth, income, low poverty, price stability, crime direction, federal-jobs risk, rental-stock balance), across the 14 MD/DC/VA jurisdictions."),
    ]:
        ws.cell(row=rr, column=1, value=a).border = BOX; ws.cell(row=rr, column=1).font = BOLD
        ws.cell(row=rr, column=2, value=b).border = BOX; ws.cell(row=rr, column=2).alignment = WRAP
        ws.merge_cells(start_row=rr, start_column=2, end_column=3, end_row=rr)
        rr += 1
    rr += 1
    rr = sec(ws, rr, "C. DISCLAIMERS")
    for t in [
        "Market data as of the 2026-08-25 Market Finder pull; economic/crime data researched 2026-08. Re-pull to refresh.",
        "REI Sift data = proprietary investor transaction tracking from deed recordings.",
        "Wholesaling Scores are composite indicators, not guarantees. Validate with local knowledge.",
        "Crime figures are 2025 year-end (some preliminary). Not financial advice.",
    ]:
        ws.cell(row=rr, column=1, value=t); ws.merge_cells(start_row=rr, start_column=1, end_column=3, end_row=rr)
        rr += 1

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, juris.replace(", ", "_").replace(" ", "_") + "_Market_Research.xlsx")
    wb.save(path)
    return path, len(zrows), len(active_z), len(nrows), len(active_n)


def rows_from(records, keyname, county_med):
    out = []
    for z in records:
        mv, sp = z.get("median_home_value"), z.get("median_sale_price")
        inv = z.get("total_inv_trans_6mo") or 0
        hom = z.get("homes_on_market") or 0
        hs = z.get("homes_sold_last_month") or 0
        dom = z.get("median_days_on_market")
        sp_p, sp_s = spread_pct(mv, sp)
        su_v, su_s = supply_mo(hom, hs)
        sc = wscore(inv, dom, mv, sp_s == "Outlier", county_med)
        out.append(dict(name=z.get(keyname), inv=inv, hom=hom, hsold=hs, dom=dom,
                        dvn=(dom - FRED_DOM_BASELINE) if dom else None, mv=mv, sp=sp,
                        spread=sp_s, supply=su_s, supply_v=su_v, score=sc, stars=STAR[sc]))
    out.sort(key=lambda r: (-(r["inv"] or 0), -(r["mv"] or 0)))
    return out


def main():
    metrics = {j: county_metrics(j) for j in SLUG}
    order = sorted(SLUG, key=lambda j: -metrics[j]["comp"])
    rank_by = {j: (i + 1, metrics[j]["comp"], metrics[j]["mf"], metrics[j]["econ"], metrics[j]["fund"])
               for i, j in enumerate(order)}

    print(f"{'#':>3} {'jurisdiction':24} {'comp':6} {'MF':6} {'econ':6} {'fund':6}")
    print("-" * 60)
    for j in order:
        r = rank_by[j]
        print(f"{r[0]:>3} {j:24} {r[1]:<6} {r[2]:<6} {r[3]:<6} {r[4]:<6}")

    for j in SLUG:
        build(j, metrics, rank_by)

    # ranking summary
    lines = ["# 14-Jurisdiction Re-Rank - all numbers (Market Finder + deal economics + fundamentals)",
             "", f"_Generated {GEN_DATE}. Composite = 0.45 Market Finder + 0.30 deal economics + 0.25 fundamentals._", "",
             "| # | Jurisdiction | Composite | Market Finder | Deal econ | Fundamentals | Median value | Price YoY | Crime | Fed risk |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for i, j in enumerate(order, 1):
        m = metrics[j]; rs = RESEARCH[j]
        lines.append(f"| {i} | {j} | {m['comp']:.2f} | {m['mf']:.2f} | {m['econ']:.2f} | {m['fund']:.2f} | "
                     f"{money(m['mhv'])} | {pct(rs['price_yoy'])} | "
                     f"{'improving' if rs['crime_dir']==1 else 'flat' if rs['crime_dir']==0 else 'worsening'} | "
                     f"{['low','moderate','high'][rs['fed_risk']]} |")
    lines += ["", "## Top 5", ""]
    for i, j in enumerate(order[:5], 1):
        lines.append(f"{i}. **{j}** - composite {metrics[j]['comp']:.2f}")
    with open(os.path.join(OUT_DIR, "_RANKING.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n{len(SLUG)} workbooks + _RANKING.md written to {OUT_DIR}")


if __name__ == "__main__":
    main()
