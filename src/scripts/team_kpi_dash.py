"""team_kpi_dash.py - the HTML dashboard for the daily / weekly team KPI (opened at 20:00).

Pure rendering: team_kpi.py hands over the period KPI (kpi.json), the period's SmrtPhone rows
and the trailing-28-day rows used for the call-window charts. Self-contained page (no CDN),
light + dark, every chart value also reachable in a table.

Rates (keep in step with team_kpi.py):
  pick-up rate       conversations / dials          (a person answered)
  right-party share  right-party contacts / conversations   (the person was the owner)
  RPC per 100 dials  right-party contacts / dials x 100
"""
from __future__ import annotations

import collections as C
import datetime as D
import html
import json

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SEQ = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
       "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
MIN_CELL, MIN_HOUR, MIN_CID = 25, 120, 40

CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--surface2:#f0efec;--ink:#0b0b0b;--ink2:#52514e;
--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--accent:#2a78d6;--track:#cde2fb;
--good:#0ca30c;--warn:#fab219;--serious:#ec835a;--crit:#d03b3b;--goodtext:#006300}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;
--surface2:#262624;--ink:#fff;--ink2:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--accent:#3987e5;--track:#184f95;--goodtext:#0ca30c}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--surface2:#262624;--ink:#fff;--ink2:#c3c2b7;
--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--accent:#3987e5;--track:#184f95;--goodtext:#0ca30c}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px 40px}.card,.tile{min-width:0}
h1{font-size:22px;margin:0}h2{font-size:15px;margin:0 0 2px}.sub{color:var(--ink2);margin:2px 0 0}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;margin-top:14px}
.cap{color:var(--ink2);font-size:12.5px;margin:0 0 12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:14px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.tile .l{color:var(--ink2);font-size:12.5px}.tile .v{font-size:26px;font-weight:600;margin-top:2px}
.tile.hero .v{font-size:48px;line-height:1.05}.tile .d{color:var(--muted);font-size:12px}
.grid2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px}.grid2>*{min-width:0}@media(max-width:860px){.grid2{grid-template-columns:1fr}}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th{color:var(--muted);font-weight:500;font-size:12px;text-align:right;padding:6px 8px;border-bottom:1px solid var(--axis);white-space:nowrap}
td{padding:7px 8px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}td.l,th.l{text-align:left}td.fl{white-space:normal;min-width:170px}
.scroll{overflow-x:auto}
.bar{display:inline-block;height:10px;border-radius:0 4px 4px 0;background:var(--accent);vertical-align:middle;margin-right:6px}
.meter{display:inline-block;width:70px;height:8px;border-radius:4px;background:var(--track);vertical-align:middle;margin-right:6px;overflow:hidden}
.meter i{display:block;height:100%;background:var(--accent);border-radius:4px}
.flag{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--ink2);margin:1px 6px 1px 0;white-space:nowrap}
.flag b{display:inline-block;width:14px;height:14px;border-radius:50%;color:#fff;font-size:10px;line-height:14px;text-align:center}
.f-crit b{background:var(--crit)}.f-warn b{background:var(--warn);color:#0b0b0b}.f-ok b{background:var(--good)}
.call{background:var(--surface2);border-radius:8px;padding:10px 12px;margin:0 0 12px}
.call strong{font-size:16px}
.heat{display:grid;gap:2px;font-size:11.5px}
.heat .c{height:30px;border-radius:4px;display:flex;align-items:center;justify-content:center;cursor:default;outline-offset:1px}
.heat .c:hover,.heat .c:focus{outline:2px solid var(--ink)}
.heat .h{color:var(--muted);text-align:center;font-size:11px}.heat .rl{color:var(--ink2);display:flex;align-items:center}
.heat .na{background:var(--surface2);color:var(--muted)}
.legend{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:11.5px;margin-top:8px}
.legend span.s{display:inline-block;width:18px;height:10px;border-radius:2px}
.cols{display:flex;align-items:flex-end;gap:2px;height:150px;border-bottom:1px solid var(--axis);padding-top:18px}
.cols .k{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%}
.cols .k i{display:block;width:min(24px,80%);background:var(--accent);border-radius:4px 4px 0 0;min-height:1px}
.cols .k:hover i,.cols .k:focus i{filter:brightness(1.15)}
.cols .k em{font-style:normal;font-size:11px;color:var(--ink2);margin-bottom:3px;white-space:nowrap}
.xl{display:flex;gap:2px;color:var(--muted);font-size:11px;margin-top:4px}.xl span{flex:1;text-align:center}
details{margin-top:10px}summary{cursor:pointer;color:var(--ink2);font-size:12.5px}
.err{border-left:4px solid var(--crit);background:var(--surface);padding:10px 14px;margin-top:14px;border-radius:6px}
#tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--border);
border-radius:8px;padding:8px 10px;font-size:12.5px;box-shadow:0 4px 16px rgba(0,0,0,.15);display:none;z-index:9;max-width:260px}
#tip strong{font-size:15px}
.foot{color:var(--muted);font-size:12px;margin-top:18px}
"""

JS = """
const tip=document.getElementById('tip');
function show(e){const t=e.currentTarget;tip.textContent='';
 const s=document.createElement('strong');s.textContent=t.dataset.v;tip.appendChild(s);
 for(const line of (t.dataset.d||'').split('|')){if(!line)continue;const d=document.createElement('div');d.textContent=line;tip.appendChild(d);}
 tip.style.display='block';const r=t.getBoundingClientRect();
 const x=(e.clientX||r.left+r.width/2)+14, y=(e.clientY||r.top)+14;
 tip.style.left=Math.min(x,innerWidth-tip.offsetWidth-8)+'px';tip.style.top=Math.min(y,innerHeight-tip.offsetHeight-8)+'px';}
function hide(){tip.style.display='none'}
document.querySelectorAll('[data-v]').forEach(el=>{el.tabIndex=0;el.addEventListener('pointermove',show);
 el.addEventListener('focus',show);el.addEventListener('pointerleave',hide);el.addEventListener('blur',hide);});
"""


def esc(x) -> str:
    return html.escape(str(x), quote=True)


def pct(n, d, digits=0):
    return f"{100 * n / d:.{digits}f}%" if d else "-"


def money(v):
    return f"${v:,.0f}" if v is not None else "-"


def fmt_phone(d: str) -> str:
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else d


# ---------------------------------------------------------------- analysis
def window_stats(rows: list[dict], human: set, rpc: set, callers: set) -> dict:
    """Outbound dials by weekday x hour (ET), by hour, by caller-ID number, by caller."""
    cell = C.defaultdict(lambda: [0, 0, 0])
    hour = C.defaultdict(lambda: [0, 0, 0])
    cid = C.defaultdict(lambda: [0, 0, 0, C.Counter()])
    per = C.defaultdict(lambda: [0, 0, 0])
    days = set()
    for r in rows:
        if r.get("direction") != "Outbound" or r.get("person") not in callers:
            continue
        d = D.date.fromisoformat(r["date"])
        h = int(r["time"][:2])
        days.add(r["date"])
        hit = (1, r["status"] in human, r["status"] in rpc)
        for acc in (cell[(d.weekday(), h)], hour[h], per[r["person"]], cid[r.get("agent_number") or "?"]):
            acc[0] += hit[0]
            acc[1] += hit[1]
            acc[2] += hit[2]
        cid[r.get("agent_number") or "?"][3][r["person"]] += 1
    return {"cell": cell, "hour": hour, "cid": cid, "per": per,
            "first": min(days) if days else None, "last": max(days) if days else None}


def best_window(ws: dict) -> list[str]:
    out = []
    hrs = {h: v for h, v in ws["hour"].items() if v[0] >= MIN_HOUR}
    if hrs:
        tot = [sum(v[i] for v in ws["hour"].values()) for i in range(3)]
        bh = max(hrs, key=lambda h: hrs[h][2] / hrs[h][0])
        bp = max(hrs, key=lambda h: hrs[h][1] / hrs[h][0])
        out.append(("Most owners reached", f"{bh:02d}:00-{bh + 1:02d}:00",
                    f"{100 * hrs[bh][2] / hrs[bh][0]:.1f} owners per 100 dials vs "
                    f"{100 * tot[2] / tot[0]:.1f} all day ({hrs[bh][0]:,} dials)"))
        out.append(("Highest pick-up", f"{bp:02d}:00-{bp + 1:02d}:00",
                    f"{pct(hrs[bp][1], hrs[bp][0])} pick up vs {pct(tot[1], tot[0])} all day"))
    cells = {k: v for k, v in ws["cell"].items() if v[0] >= MIN_CELL}
    if cells:
        bc = max(cells, key=lambda k: (cells[k][2] / cells[k][0], cells[k][0]))
        v = cells[bc]
        out.append(("Best single slot", f"{DAYS[bc[0]]} {bc[1]:02d}:00",
                    f"{100 * v[2] / v[0]:.1f} owners per 100 dials, {pct(v[1], v[0])} pick-up ({v[0]} dials)"))
    return out


# ---------------------------------------------------------------- pieces
def tiles(k: dict, callers: dict) -> str:
    dials = sum(p["dials"] for p in callers.values())
    conv = sum(p["conversations"] for p in callers.values())
    rpc = sum(p["rpc"] for p in callers.values())
    cinv = sum(p["invoice"] for p in callers.values())
    items = [("Dials", f"{dials:,}", f"{k['total_hours']:.1f} paid h, all staff", True),
             ("Pick-up rate", pct(conv, dials, 1), f"{conv:,} conversations", False),
             ("Right-party share", pct(rpc, conv, 0), f"{rpc} owners of {conv} who answered", False),
             ("Dials per owner reached", f"{dials / rpc:.0f}" if rpc else "-", "lower is better", False),
             ("Cost per owner reached", money(cinv / rpc) if rpc else "-", f"callers {money(cinv)}", False),
             ("Total cost", money(k["total_invoice"]), "all staff, hours x rate", False)]
    return '<div class="tiles">' + "".join(
        f'<div class="tile{" hero" if h else ""}"><div class="l">{esc(l)}</div><div class="v">{esc(v)}</div>'
        f'<div class="d">{esc(d)}</div></div>' for l, v, d, h in items) + "</div>"


def flags_for(p: dict, short: str, weekly: bool) -> list[tuple[str, str]]:
    f = []
    slack_lim = 8 if weekly else 2
    if p["hours"] >= 1 and not p["dials"]:
        f.append(("crit", "paid, 0 dials"))
    elif p.get("paid_days_no_dials") and weekly:
        f.append(("crit", f"{p['paid_days_no_dials']} paid day(s), 0 dials"))
    if (p["slack_hours"] or 0) >= slack_lim:
        f.append(("warn", f"~{p['slack_hours']:.1f} h off-dialer"))
    if p["dials"] and not p["hours"]:
        f.append(("warn", "dials, no Hubstaff"))
    if p["vm_left_rate"] is not None and p["dials"] >= 50 and p["vm_left_rate"] < 0.25:
        f.append(("warn", f"VM {p['vm_left_rate'] * 100:.0f}%"))
    if (p["no_dispo_rate"] or 0) > 0.08 and p["dials"] >= 50:
        f.append(("warn", f"no-dispo {p['no_dispo_rate'] * 100:.0f}%"))
    return f


def callers_table(k: dict, callers: dict, short: dict, weekly: bool, ws: dict) -> str:
    mx = max([p["dials"] for p in callers.values()] + [1])
    head = ("<tr><th>Caller</th>" + ("<th>Days</th>" if weekly else "") +
            "<th>Paid h</th><th class=l>Dials</th><th>Dials / h</th><th>Pick-up</th><th>Owners</th>"
            "<th>Right-party share</th><th>Cost / owner</th><th>VM left</th><th>1st dial</th><th>Off-dialer h</th>"
            "<th class=l>Flags</th></tr>")
    body = []
    for n, p in sorted(callers.items(), key=lambda kv: -kv[1]["dials"]):
        if not p["hours"] and not p["dials"]:
            body.append(f"<tr><td>{esc(short[n])}</td><td colspan=12 class=l style='color:var(--muted)'>off</td></tr>")
            continue
        first = p["median_first_dial"] if weekly else next((r["first"] for r in k["daily"][n] if r["first"]), "")
        fl = "".join(f'<span class="flag f-{c}"><b>{"!" if c == "crit" else "▲"}</b>{esc(t)}</span>'
                     for c, t in flags_for(p, short[n], weekly)) or '<span class="flag f-ok"><b>✓</b>ok</span>'
        w = max(2, round(110 * p["dials"] / mx))
        body.append(
            f"<tr><td>{esc(short[n])}</td>" + (f"<td>{p['days']}</td>" if weekly else "") +
            f"<td>{p['hours']:.1f}</td><td class=l><span class=bar style='width:{w}px'></span>{p['dials']:,}</td>"
            f"<td>{(p['dials_per_paid_hour'] or 0):.0f}</td><td>{pct(p['conversations'], p['dials'], 1)}</td>"
            f"<td>{p['rpc']}</td><td>{pct(p['rpc'], p['conversations'])}</td><td>{money(p['cost_per_rpc'])}</td>"
            f"<td>{pct(p['vm_left_rate'] or 0, 1) if p['vm_left_rate'] is not None else '-'}</td>"
            f"<td>{esc(first or '-')}</td><td>{(p['slack_hours'] or 0):.1f}</td><td class='l fl'>{fl}</td></tr>")
    dm = [f"{esc(short[n])} {p['hours']:.1f} h" for n, p in k["people"].items() if p["role"] == "data_manager" and p["hours"]]
    cap = ("Owners = right-party contacts (Correct Number, Not Interested, DNC Correct, Already Sold). "
           "Right-party share = owners / people who answered. Off-dialer = paid time not covered by dialing, after 1 h a day.")
    return (f'<section class="card"><h2>Callers</h2><p class="cap">{esc(cap)}</p><div class="scroll"><table>{head}'
            + "".join(body) + "</table></div>"
            + (f'<p class="cap" style="margin:10px 0 0">Data managers (not judged on dials): {", ".join(dm)}</p>' if dm else "")
            + "</section>")


def heatmap(ws: dict) -> str:
    cells = ws["cell"]
    hours = sorted({h for (_, h), v in cells.items() if v[0] >= 5})
    if not hours:
        return ""
    hours = list(range(min(hours), max(hours) + 1))
    rates = sorted(v[1] / v[0] for v in cells.values() if v[0] >= MIN_CELL)
    lo, hi = (rates[0], rates[-1]) if rates else (0, 1)

    def color(r):
        i = 0 if hi == lo else round((r - lo) / (hi - lo) * (len(SEQ) - 1))
        return SEQ[i], ("#fff" if i >= 7 else "#0b0b0b")

    g = [f'<div class="heat" style="grid-template-columns:44px repeat({len(hours)},minmax(30px,1fr));min-width:{44 + 32 * len(hours)}px"><div></div>']
    g += [f'<div class="h">{h:02d}</div>' for h in hours]
    tbl = ["<tr><th>Day</th>" + "".join(f"<th>{h:02d}</th>" for h in hours) + "</tr>"]
    for wd in range(7):
        if not any(cells.get((wd, h), [0])[0] for h in hours):
            continue
        g.append(f'<div class="rl">{DAYS[wd]}</div>')
        trow = [f"<td>{DAYS[wd]}</td>"]
        for h in hours:
            v = cells.get((wd, h), [0, 0, 0])
            if v[0] < MIN_CELL:
                g.append(f'<div class="c na" data-v="{v[0]} dials" data-d="{DAYS[wd]} {h:02d}:00|too few to rate">'
                         f'{"" if not v[0] else "·"}</div>')
                trow.append(f"<td>{'' if not v[0] else '·'}</td>")
                continue
            bg, fg = color(v[1] / v[0])
            g.append(f'<div class="c" style="background:{bg};color:{fg}" data-v="{pct(v[1], v[0])} pick-up" '
                     f'data-d="{DAYS[wd]} {h:02d}:00-{h + 1:02d}:00|{v[0]} dials, {v[1]} answered|'
                     f'{v[2]} owners ({100 * v[2] / v[0]:.1f} per 100 dials)">{pct(v[1], v[0])}</div>')
            trow.append(f"<td>{pct(v[1], v[0])} / {v[2]}</td>")
        tbl.append("<tr>" + "".join(trow) + "</tr>")
    g.append("</div>")
    legend = ('<div class="legend">pick-up rate ' + f"{lo * 100:.0f}% "
              + "".join(f'<span class="s" style="background:{c}"></span>' for c in SEQ[::3])
              + f" {hi * 100:.0f}% · grey = under {MIN_CELL} dials</div>")
    return ('<div class=scroll>' + "".join(g) + "</div>" + legend + "<details><summary>Table view (pick-up / owners reached)</summary>"
            f'<div class="scroll"><table>{"".join(tbl)}</table></div></details>')


def hour_columns(ws: dict) -> str:
    hrs = sorted(h for h, v in ws["hour"].items() if v[0] >= 20)
    if not hrs:
        return ""
    vals = {h: 100 * ws["hour"][h][2] / ws["hour"][h][0] for h in hrs}
    mx = max(vals.values()) or 1
    best = max((h for h in hrs if ws["hour"][h][0] >= MIN_HOUR), key=lambda h: vals[h], default=None)
    cols, xl, rows = [], [], []
    for h in hrs:
        v = ws["hour"][h]
        lab = f"<em>{vals[h]:.1f}</em>" if h == best else ""
        cols.append(f'<div class="k" data-v="{vals[h]:.1f} owners / 100 dials" data-d="{h:02d}:00-{h + 1:02d}:00|'
                    f'{v[0]:,} dials, {pct(v[1], v[0])} pick-up|{v[2]} owners reached">{lab}'
                    f'<i style="height:{max(1, 110 * vals[h] / mx):.0f}px"></i></div>')
        xl.append(f"<span>{h:02d}</span>")
        rows.append(f"<tr><td>{h:02d}:00</td><td>{v[0]:,}</td><td>{pct(v[1], v[0], 1)}</td><td>{v[2]}</td>"
                    f"<td>{vals[h]:.1f}</td></tr>")
    return (f'<div class="cols">{"".join(cols)}</div><div class="xl">{"".join(xl)}</div>'
            "<details><summary>Table view</summary><table><tr><th>Hour</th><th>Dials</th><th>Pick-up</th>"
            f"<th>Owners</th><th>Per 100 dials</th></tr>{''.join(rows)}</table></details>")


def cid_table(ws: dict, short: dict) -> str:
    rows = [(n, v) for n, v in ws["cid"].items() if v[0] >= MIN_CID and n != "?"]
    if not rows:
        return '<p class="cap">Not enough dials per caller-ID number yet.</p>'
    rows.sort(key=lambda x: -x[1][1] / x[1][0])
    tot = [sum(v[i] for _, v in rows) for i in range(3)]
    avg = tot[1] / tot[0]
    mx = max(v[1] / v[0] for _, v in rows)
    pick = rows[:5] + ([("…", None)] if len(rows) > 10 else []) + (rows[-5:] if len(rows) > 5 else [])
    seen, out = set(), []
    for n, v in pick:
        if v is None:
            out.append("<tr><td colspan=6 style='color:var(--muted)'>…</td></tr>")
            continue
        if n in seen:
            continue
        seen.add(n)
        r = v[1] / v[0]
        who = ", ".join(short.get(p, p) for p, _ in v[3].most_common(2))
        low = r < avg * 0.6
        tag = '<span class="flag f-warn"><b>▲</b>low - spam label?</span>' if low else ""
        out.append(f"<tr><td>{esc(fmt_phone(n))}</td><td class=l>{esc(who)}</td><td>{v[0]:,}</td>"
                   f"<td class=l><span class=meter><i style='width:{100 * r / mx:.0f}%'></i></span>{pct(v[1], v[0], 1)}</td>"
                   f"<td>{v[2]}</td><td class=l>{tag}</td></tr>")
    return (f'<p class="cap">Numbers with {MIN_CID}+ dials, best and worst pick-up. Team average {avg * 100:.1f}%. '
            "A number far below average may be carrying a carrier spam label.</p><div class=scroll><table>"
            "<tr><th>Caller ID</th><th class=l>Used by</th><th>Dials</th><th class=l>Pick-up</th><th>Owners</th><th></th></tr>"
            + "".join(out) + "</table></div>")


def caller_window(ws: dict, short: dict) -> str:
    rows = []
    for n, v in sorted(ws["per"].items(), key=lambda kv: -(kv[1][2] / kv[1][0] if kv[1][0] else 0)):
        if not v[0]:
            continue
        rows.append(f"<tr><td>{esc(short.get(n, n))}</td><td>{v[0]:,}</td><td>{pct(v[1], v[0], 1)}</td>"
                    f"<td>{v[2]}</td><td>{pct(v[2], v[1])}</td><td>{100 * v[2] / v[0]:.1f}</td>"
                    f"<td>{(v[0] / v[2]):.0f}</td></tr>" if v[2] else
                    f"<tr><td>{esc(short.get(n, n))}</td><td>{v[0]:,}</td><td>{pct(v[1], v[0], 1)}</td><td>0</td><td>-</td><td>0</td><td>-</td></tr>")
    return ("<div class=scroll><table><tr><th>Caller</th><th>Dials</th><th>Pick-up</th><th>Owners</th>"
            "<th>Right-party share</th><th>Owners / 100 dials</th><th>Dials / owner</th></tr>" + "".join(rows) + "</table></div>")


def by_day(k: dict, rows: list[dict], callers: set, human: set, rpc: set) -> str:
    by = C.defaultdict(lambda: [0.0, 0, 0, 0])
    for n in callers:
        for r in k["daily"].get(n, []):
            by[r["date"]][0] += r["paid"]
    for r in rows:
        if r["person"] in callers and r["direction"] == "Outbound":
            b = by[r["date"]]
            b[1] += 1
            b[2] += r["status"] in human
            b[3] += r["status"] in rpc
    if not by:
        return ""
    mx = max(v[1] for v in by.values()) or 1
    cols, xl, tr = [], [], []
    for d in sorted(by):
        h, dl, cv, rp = by[d]
        lab = D.date.fromisoformat(d)
        cols.append(f'<div class="k" data-v="{dl:,} dials" data-d="{lab:%a %b %d}|{h:.1f} caller h paid|'
                    f'{pct(cv, dl)} pick-up, {rp} owners"><em>{dl:,}</em><i style="height:{max(1, 110 * dl / mx):.0f}px"></i></div>')
        xl.append(f"<span>{lab:%a %d}</span>")
        tr.append(f"<tr><td>{lab:%a %b %d}</td><td>{h:.1f}</td><td>{dl:,}</td><td>{pct(cv, dl, 1)}</td><td>{rp}</td></tr>")
    return ('<section class="card"><h2>Dials by day</h2><p class="cap">Callers only.</p>'
            f'<div class="cols">{"".join(cols)}</div><div class="xl">{"".join(xl)}</div>'
            "<details><summary>Table view</summary><table><tr><th>Day</th><th>Caller h</th><th>Dials</th>"
            f"<th>Pick-up</th><th>Owners</th></tr>{''.join(tr)}</table></details></section>")


# ---------------------------------------------------------------- page
def dm_card(dm: dict) -> str:
    """Mohamed Hisham (data manager) from data_manager_kpi.py's kpi.json."""
    pipe = dm.get("pipeline") or {}
    prog = dm.get("progress_since_last_snapshot")
    tiles_ = [("New estates (filed)", f"{dm['intake_estates']:,}", f"{dm['intake_with_address']} with address"),
              ("PR checks", f"{dm['pr_checks']:,}", f"on {dm['pr_check_days']} day(s)"),
              ("Paid hours", f"{dm['hours']:.1f}" if dm.get("hours") is not None else "-",
               money(dm.get("invoice")) if dm.get("invoice") is not None else ""),
              ("Cost per address", money(dm.get("cost_per_address")), "addresses resolved in intake")]
    if prog:
        tiles_.append(("Moved to Done", f"{prog['newly_done']}", f"since {prog['since']}"))
        tiles_.append(("Deep dives resolved", f"{prog['dive_resolved']}", f"since {prog['since']}"))
    mx = max(pipe.values() or [1]) or 1
    rows = "".join(f"<tr><td>{esc(t)}</td><td class=l><span class=bar style='width:{max(2, round(140 * n / mx))}px'></span>{n:,}</td></tr>"
                   for t, n in pipe.items())
    hand = (f"{dm['addressed_on_row_list']} of {dm['intake_with_address']} addressed estates are on the DataSift "
            "Register of Wills list." if dm.get("addressed_on_row_list") is not None else "")
    over = f"{dm.get('pr_watch_overdue_14d', 0)} PR Watch estates not re-checked in 14+ days."
    return ('<section class="card"><h2>Data manager - Mohamed Hisham</h2><p class="cap">Probate Extraction 2: '
            "Register of Wills, Deep Dive, Legal Notices tabs (footprint counties). Not judged on dials.</p>"
            '<div class="tiles" style="margin-top:0">' + "".join(
                f'<div class="tile"><div class="l">{esc(a)}</div><div class="v">{esc(b)}</div><div class="d">{esc(c)}</div></div>'
                for a, b, c in tiles_) + "</div>"
            f'<div class="grid2" style="margin-top:12px"><div><h2 style="font-size:13px">Pipeline now (sheet routing)</h2>'
            f"<table>{rows}</table></div><div><p class=cap>{esc(over)}</p><p class=cap>{esc(hand)}</p>"
            + ("" if prog else "<p class=cap>Progress lines appear from the next run (baseline snapshot taken).</p>")
            + "</div></div></section>")


def render(kind: str, title: str, subtitle: str, k: dict | None, period_rows: list[dict],
           window_rows: list[dict], cfg: dict, errors: list[str], human: set, rpc: set, dm: dict | None = None) -> str:
    short = {n: p.get("short") or n.split()[0] for n, p in cfg["people"].items()}
    parts = [f"<header><h1>{esc(title)}</h1><p class=sub>{esc(subtitle)}</p></header>"]
    parts += [f'<div class="err"><strong>Not complete:</strong> {esc(e)}</div>' for e in errors]
    if k:
        callers = {n: p for n, p in k["people"].items() if p["role"] != "data_manager"}
        cset = set(callers)
        ws = window_stats(window_rows, human, rpc, cset)
        parts.append(tiles(k, callers))
        parts.append(callers_table(k, callers, short, kind == "weekly", ws))
        if kind == "weekly":
            parts.append(by_day(k, period_rows, cset, human, rpc))
        span = f"{ws['first']} to {ws['last']}" if ws["first"] else "no data"
        bw = best_window(ws)
        call = "".join(f'<div class="call">{esc(a)}: <strong>{esc(b)}</strong><br>'
                       f'<span style="color:var(--ink2)">{esc(c)}</span></div>' for a, b, c in bw)
        parts.append(
            f'<section class="card"><h2>Best call window</h2><p class="cap">All callers, last 28 days ({esc(span)}), '
            "Eastern time. Each cell is the pick-up rate for that weekday and hour; hover for owners reached.</p>"
            f'<div class="grid2"><div>{call}</div><div><h2 style="font-size:13px">Owners reached per 100 dials, by hour</h2>'
            f'{hour_columns(ws)}</div></div><div style="margin-top:14px">{heatmap(ws)}</div></section>')
        parts.append('<div class="grid2">'
                     f'<section class="card"><h2>Caller-ID numbers</h2>{cid_table(ws, short)}</section>'
                     f'<section class="card"><h2>Callers over 28 days</h2><p class="cap">Steadier than one day: '
                     f'who reaches owners, not just who dials.</p>{caller_window(ws, short)}</section></div>')
    if dm:
        parts.append(dm_card(dm))
    parts.append(f'<p class="foot">Generated {D.datetime.now():%Y-%m-%d %H:%M} from Hubstaff + SmrtPhone (read-only). '
                 "Leads are in the monthly review (WhatsApp).</p>")
    return ("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)}</title><style>{CSS}</style></head><body><main>{''.join(parts)}</main>"
            f"<div id=tip role=tooltip></div><script>{JS}</script></body></html>")
