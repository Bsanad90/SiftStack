"""report_pdf.py - render a graded call report (.md) as a readable coaching PDF.

The markdown report is written for the grader and the parsers (tables, JSON footer,
evidence in rubric order). A manager or dialer reading it needs the opposite order:
the result first, the fixes next, the evidence after, the math last. This script
re-arranges the SAME content into that order and lays it out with reportlab.

  python skills/cold-call-coach/scripts/report_pdf.py <report.md> [<out.pdf>] [--no-ops]

--no-ops leaves out the "Internal ops notes" section (the dialer-safe version).
Nothing is re-graded or re-worded: every sentence comes from the report file.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

W = 7.0 * inch
NAVY = colors.HexColor("#1f3a5f")
LIGHT = colors.HexColor("#eef2f7")
LINE = colors.HexColor("#c5cdd8")
RED = colors.HexColor("#b3261e")
AMBER = colors.HexColor("#b26a00")
GREEN = colors.HexColor("#2e7d32")
BAND_COLOR = {"Elite": GREEN, "Strong": GREEN, "Developing": AMBER, "Needs Work": AMBER, "Retrain": RED}

ss = getSampleStyleSheet()
P = ParagraphStyle("p", parent=ss["Normal"], fontName="Helvetica", fontSize=10, leading=14.5, spaceAfter=6)
SMALL = ParagraphStyle("small", parent=P, fontSize=8.8, leading=12, textColor=colors.HexColor("#444444"))
H1 = ParagraphStyle("h1", parent=P, fontName="Helvetica-Bold", fontSize=19, leading=23, textColor=NAVY, spaceAfter=2)
H2 = ParagraphStyle("h2", parent=P, fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=NAVY,
                    spaceBefore=14, spaceAfter=6)
LBL = ParagraphStyle("lbl", parent=P, fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=NAVY)
BUL = ParagraphStyle("bul", parent=P, leftIndent=14, bulletIndent=2, spaceAfter=4)
CELL = ParagraphStyle("cell", parent=P, fontSize=9.4, leading=13, spaceAfter=0)
CELLB = ParagraphStyle("cellb", parent=CELL, fontName="Helvetica-Bold")
BIG = ParagraphStyle("big", parent=P, fontName="Helvetica-Bold", fontSize=20, leading=24, alignment=1, spaceAfter=0)
BIGLBL = ParagraphStyle("biglbl", parent=P, fontSize=8.5, leading=11, alignment=1, textColor=colors.HexColor("#555555"), spaceAfter=0)
QUOTE = ParagraphStyle("quote", parent=CELL, fontName="Helvetica-Oblique")


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<font face='Courier' size='8.5'>\1</font>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<link href='\2' color='#1a4d8f'><u>\1</u></link>", s)
    s = re.sub(r"(?<![\w'>=])(https?://[^\s<),]+)", r"<link href='\1' color='#1a4d8f'><u>\1</u></link>", s)
    return s


def grab(pattern: str, text: str, flags=0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


def parse(md: str) -> dict:
    d: dict = {}
    body, _, ops = md.partition("## Internal ops notes")
    d["ops"] = [l[2:].strip() for l in ops.splitlines() if l.startswith("- ")]
    d["title"] = grab(r"^# (.+)$", body, re.M)
    d["caller"] = grab(r"^Caller:\s*(.+?)\s{2,}Date/Time", body, re.M)
    d["datetime"] = grab(r"Date/Time:\s*(.+?)\s{2,}Lead source", body)
    d["lead"] = grab(r"Lead source/list:\s*(.+)$", body, re.M)
    d["length"] = grab(r"^Call length:\s*(.+?)\s{2,}Outcome", body, re.M)
    d["outcome"] = grab(r"Outcome:\s*(.+)$", body.split("Call length:", 1)[-1], re.M)
    d["autofail"] = grab(r"^Auto-fail check:\s*(.+)$", body, re.M)
    d["band"] = grab(r"^Grade band:\s*(.+)$", body, re.M)
    d["total"] = grab(r"^\|\s*TOTAL[^|]*\|[^\n]*?([\d.]+)\s*/\s*100", body, re.M)
    d["property"] = grab(r"^\*{0,2}Property:?\*{0,2}\s*(.+)$", body, re.M)
    d["source"] = grab(r"^Source:\s*(.+)$", body, re.M)
    d["label"] = grab(r"^Label check:\s*(.+)$", body, re.M)
    d["gate"] = grab(r"^Gate:\s*(.+)$", body, re.M)
    d["routing"] = grab(r"^Routing note[^:]*:\s*(.+)$", body, re.M)
    d["ruling"] = grab(r"^Auto-fail ruling[^:]*:\s*(.+)$", body, re.M)
    d["na"] = grab(r"^N/A discipline:\s*(.+)$", body, re.M)
    d["math"] = grab(r"^Math check:\s*(.+)$", body, re.M)
    d["pillars"] = grab(r"^PILLARS CAPTURED:\s*(.+)$", body, re.M)
    d["drill"] = grab(r"^ONE DRILL FOR THIS WEEK\s*\n(.+?)(?:\n```|\n\n)", body, re.S)
    # manager-review bullets: the list right after "Two further items"
    mr = grab(r"Two further items[^\n]*\n((?:- .+\n?)+)", body)
    d["review"] = [l[2:].strip() for l in mr.splitlines() if l.startswith("- ")]
    # tables
    cats, crits = [], []
    for line in body.splitlines():
        m = re.match(r"^\|\s*(\d(?:\.\d)?)\.?\s+([^|]+?)\s*\|(.+)\|\s*$", line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(3).split("|")]
        if "." in m.group(1):
            crits.append((m.group(1), m.group(2).strip(), cells[-1]))
        else:
            cats.append((m.group(1), m.group(2).strip(), cells[0], cells[1], cells[2]))
    d["cats"], d["crits"] = cats, crits
    # evidence
    ev = {}
    for m in re.finditer(r"^- (\d\.\d) \(([^)]+)\):\s*(.+)$", body, re.M):
        ev[m.group(1)] = m.group(3).strip()
    d["evidence"] = ev
    # strengths / improvements
    sec = grab(r"TOP 3 STRENGTHS[^\n]*\n(.+?)\n\s*\n", body, re.S)
    d["strengths"] = [re.sub(r"^\d+\.\s*", "", l).strip() for l in sec.splitlines() if re.match(r"^\d+\.", l)]
    sec = grab(r"TOP 3 IMPROVEMENT AREAS[^\n]*\n(.+?)\n\s*\n", body, re.S)
    imps = []
    for l in sec.splitlines():
        if not re.match(r"^\d+\.", l):
            continue
        l = re.sub(r"^\d+\.\s*", "", l).strip()
        said = grab(r'They said:\s*"(.+?)"\s*(?:\(.*?\))?\s*Use instead', l)
        use = grab(r'Use instead:\s*"(.+?)"', l)
        why = l.split('Use instead:', 1)[-1]
        why = re.sub(r'^\s*"[^"]*"\s*', "", why).strip()
        imps.append((said, use, why))
    d["improvements"] = imps
    return d


def kv_table(rows, col1=1.55 * inch):
    data = [[Paragraph(inline(k), LBL), Paragraph(inline(v), CELL)] for k, v in rows if v]
    t = Table(data, colWidths=[col1, W - col1])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def result_strip(d: dict):
    fail = d["autofail"].upper().startswith("FAIL")
    headline = "FAIL" if fail else "PASS"
    sub = d["autofail"].split("(")[0].replace("FAIL:", "").replace("PASS", "").strip(" :") or ("auto-fail" if fail else "no auto-fail")
    band = re.sub(r"\s*\(.*\)$", "", d["band"])
    cells = [
        [Paragraph(headline, ParagraphStyle("r1", parent=BIG, textColor=RED if fail else GREEN)),
         Paragraph(f"{d['total']} <font size='11'>/100</font>", BIG),
         Paragraph(band, ParagraphStyle("r3", parent=BIG, textColor=BAND_COLOR.get(band, NAVY)))],
        [Paragraph("Auto-fail check" + (f": {sub}" if sub else ""), BIGLBL),
         Paragraph("Call score", BIGLBL),
         Paragraph("Grade band", BIGLBL)],
    ]
    t = Table(cells, colWidths=[W / 3] * 3)
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, LINE), ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("LINEAFTER", (0, 0), (1, -1), 0.5, LINE),
        ("TOPPADDING", (0, 0), (-1, 0), 10), ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
    ]))
    return t


def grid(data, widths, header=True):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
          ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    if header:
        st.append(("BACKGROUND", (0, 0), (-1, 0), LIGHT))
    t.setStyle(TableStyle(st))
    return t


def score_cell(score: str):
    s = score.strip()
    color = colors.black
    if re.fullmatch(r"[0-5](\.\d+)?", s):
        v = float(s)
        color = RED if v <= 1 else AMBER if v < 3 else GREEN
    return Paragraph(f"<font color='{color.hexval() if hasattr(color, 'hexval') else color}'><b>{s}</b></font>", ParagraphStyle("sc", parent=CELL, alignment=1))


def build(md_path: Path, out: Path, include_ops: bool = True):
    d = parse(md_path.read_text(encoding="utf-8"))
    story = []

    # ---- page 1: result, facts, why, first fix
    story.append(Paragraph("Cold Call Coaching Report", H1))
    story.append(Paragraph(inline(f"{d['caller']}  |  {d['datetime']}"), SMALL))
    story.append(Spacer(1, 10))
    story.append(result_strip(d))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Call facts", H2))
    rec = grab(r"(https://rec\.smrtphone\.io/\S+?)\.?(?:\s|$)", d["source"]) or ""
    story.append(kv_table([
        ("Caller", d["caller"]), ("Date and time", d["datetime"]), ("Property", d["property"]),
        ("Person reached", d["label"].split("Labels verified")[0].split("SELLER is", 1)[-1].strip() if "SELLER is" in d["label"] else ""),
        ("Lead source", d["lead"]), ("Call length", d["length"]), ("Outcome", d["outcome"]),
        ("Recording", rec),
    ]))

    if d["ruling"]:
        story.append(Paragraph("Why this call fails", H2))
        ruling = d["ruling"][:1].upper() + d["ruling"][1:]
        story.append(Paragraph(inline(ruling), P))
    if d["review"]:
        story.append(Paragraph("Also for manager review", LBL))
        for r in d["review"]:
            story.append(Paragraph(inline(r), BUL, bulletText="•"))

    # ---- fixes
    story.append(Spacer(1, 6))
    story.append(Paragraph("What to fix, in order", H2))
    story.append(Paragraph("Each line is a moment from the call, the playbook line that belonged there, and why.", SMALL))
    for n, (said, use, why) in enumerate(d["improvements"], 1):
        rows = [
            [Paragraph(f"<b>{n}. They said</b>", LBL), Paragraph(inline(f"“{said}”"), QUOTE)],
            [Paragraph("<b>Use instead</b>", LBL), Paragraph(inline(f"“{use}”"), CELL)],
            [Paragraph("<b>Why</b>", LBL), Paragraph(inline(why), CELL)],
        ]
        t = Table(rows, colWidths=[1.15 * inch, W - 1.15 * inch])
        t.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("BACKGROUND", (0, 0), (0, -1), LIGHT),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -2), 0.3, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(KeepTogether([t, Spacer(1, 8)]))

    if d["drill"]:
        story.append(Paragraph("Drill for this week", H2))
        t = Table([[Paragraph(inline(d["drill"]), CELL)]], colWidths=[W])
        t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, NAVY), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f9fc")),
                               ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                               ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
        story.append(t)

    if d["strengths"]:
        story.append(Paragraph("What worked", H2))
        for s in d["strengths"]:
            story.append(Paragraph(inline(s), BUL, bulletText="•"))

    # ---- scores
    story.append(Spacer(1, 6))
    story.append(Paragraph("Scores", H2))
    data = [[Paragraph("Category", CELLB), Paragraph("Weight", CELLB), Paragraph("Score (0-5)", CELLB), Paragraph("Points", CELLB)]]
    for num, name, w, sc, wt in d["cats"]:
        data.append([Paragraph(f"{num}. {name}", CELL), Paragraph(inline(w), CELL), score_cell(sc), Paragraph(inline(wt), CELL)])
    data.append([Paragraph("<b>Total</b>", CELL), "", "", Paragraph(f"<b>{d['total']} / 100</b>", CELL)])
    t = grid(data, [W * 0.46, W * 0.22, W * 0.14, W * 0.18])
    t.setStyle(TableStyle([("BACKGROUND", (0, -1), (-1, -1), LIGHT)]))
    story.append(t)
    if d["pillars"]:
        story.append(Spacer(1, 6))
        story.append(Paragraph("<b>Pillars captured:</b> " + inline(d["pillars"]), SMALL))

    story.append(Paragraph("Criterion by criterion", H2))
    story.append(Paragraph("Score 0-5 per rubric anchor. N/A means the seller made that criterion unreachable on this call.", SMALL))
    data = [[Paragraph("Criterion", CELLB), Paragraph("Score", CELLB), Paragraph("What happened on the call", CELLB)]]
    for num, name, sc in d["crits"]:
        ev = d["evidence"].get(num, "")
        data.append([Paragraph(f"<b>{num}</b> {inline(name)}", CELL), score_cell(sc), Paragraph(inline(ev) if ev else "<font color='#777777'>not reachable</font>", CELL)])
    story.append(grid(data, [W * 0.27, W * 0.08, W * 0.65]))

    # ---- appendix
    story.append(PageBreak())
    story.append(Paragraph("Appendix: grading notes", H2))
    story.append(kv_table([
        ("Source", d["source"]), ("Label check", d["label"]), ("Gate", d["gate"]),
        ("Routing", d["routing"]), ("N/A discipline", d["na"]), ("Math check", d["math"]),
    ], col1=1.25 * inch))
    if include_ops and d["ops"]:
        story.append(Paragraph("Appendix: internal ops notes (not for the dialer)", H2))
        for r in d["ops"]:
            story.append(Paragraph(inline(r), BUL, bulletText="•"))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.grey)
        canvas.drawString(0.75 * inch, 0.5 * inch, f"Cold call coaching - {d['caller']} - {md_path.stem.split('_')[0]}")
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(out), pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.75 * inch, bottomMargin=0.8 * inch,
                            title=f"Cold Call Coaching Report - {d['caller']}", author="Galal Development")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    md = Path(args[0])
    out = Path(args[1]) if len(args) > 1 else md.with_suffix(".pdf")
    build(md, out, include_ops="--no-ops" not in sys.argv)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
