# -*- coding: utf-8 -*-
"""One-page "how to use it" sheet for the Dialer Day Card.

Regenerate this WHENEVER the card is rebuilt - the sheet carries the card's build
date, and a sheet that disagrees with the card in the dialer's hand is worse than no
sheet. The build date is read out of the card's own BUILD constant, never typed here,
so the two cannot drift.

    python tools/manual/make_dialer_day_card_pdf.py
    python tools/manual/make_dialer_day_card_pdf.py --card output/Dialer_Day_Card_2026-09-21.html

Wording rule: the dialers work on a PC, not a phone. Say computer / click / Ctrl+C.
"""
import argparse
import glob
import os
import re

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Flowable,
                                Paragraph, Spacer)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root
DEFAULT_OUT = os.path.join(ROOT, "output", "Dialer_Day_Card_Instructions.pdf")


def newest_card():
    """The most recently written Dialer_Day_Card_*.html in output/."""
    cards = glob.glob(os.path.join(ROOT, "output", "Dialer_Day_Card_*.html"))
    cards = [c for c in cards if "Instructions" not in os.path.basename(c)]
    if not cards:
        raise SystemExit("No output/Dialer_Day_Card_*.html found - pass --card.")
    return max(cards, key=os.path.getmtime)


def card_build(path):
    """Read the card's own BUILD constant. A missing one is a hard stop: a sheet
    stamped with the wrong date is exactly the failure this script exists to avoid."""
    with open(path, encoding="utf-8") as fh:
        m = re.search(r'var\s+BUILD\s*=\s*"(\d{4}-\d{2}-\d{2})"', fh.read())
    if not m:
        raise SystemExit("No BUILD constant in %s" % path)
    return m.group(1)


ap = argparse.ArgumentParser(description="Build the dialer day card instruction sheet.")
ap.add_argument("--card", help="the day card HTML (default: newest in output/)")
ap.add_argument("--out", default=DEFAULT_OUT, help="PDF to write")
args = ap.parse_args()

CARD = args.card or newest_card()
OUT = args.out
BUILD = card_build(CARD)

INK      = colors.HexColor("#1C2430")
SOFT     = colors.HexColor("#55616F")
LINE     = colors.HexColor("#D8DEE6")
PRIMARY  = colors.HexColor("#2B5A8C")
FTM      = colors.HexColor("#C2611B")
STOP     = colors.HexColor("#B3372E")

PW, PH = letter
M = 0.5 * inch
HEAD_H = 1.06 * inch
COL_W = PW - 2 * M
BODY_TOP = PH - M - HEAD_H - 0.16 * inch
BODY_H = BODY_TOP - M

body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.2, leading=11.7,
                      textColor=INK, alignment=TA_LEFT, spaceAfter=0)
step = ParagraphStyle("step", parent=body, leftIndent=14, firstLineIndent=-14,
                      spaceAfter=2.9)
sub = ParagraphStyle("sub", parent=body, fontSize=8.7, leading=11.0,
                     leftIndent=28, firstLineIndent=-12, textColor=SOFT,
                     spaceAfter=2.2)
note = ParagraphStyle("note", parent=body, fontSize=8.7, leading=11.0,
                      textColor=SOFT, leftIndent=14, spaceAfter=2.9)
head = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=10.6,
                      leading=12.4, textColor=PRIMARY, spaceBefore=0, spaceAfter=4)


class Rule(Flowable):
    """Hairline under a section heading."""
    def __init__(self, width):
        Flowable.__init__(self)
        self.width = width
        self.height = 0

    def draw(self):
        self.canv.setStrokeColor(LINE)
        self.canv.setLineWidth(0.7)
        self.canv.line(0, 3, self.width, 3)


def section(title, flowables, rule=True):
    out = [Paragraph(title.upper(), head), Rule(COL_W), Spacer(1, 4)]
    out.extend(flowables)
    out.append(Spacer(1, 6))
    return out


def steps(items):
    return [Paragraph("<b>%d.</b>&nbsp; %s" % (i + 1, t), step)
            for i, t in enumerate(items)]


story = []

story += section("Set up once", steps([
    "Open the card file on your work computer in <b>Chrome</b> or <b>Edge</b>. Bookmark it so you find it every morning.",
    "Use the <b>same computer and same browser every day</b>. The card saves your progress on that device only.",
    "Type your name in the box at the bottom. It is remembered.",
    'Open <b>"Set up my 10 numbers"</b> and paste your 10 caller IDs, one per line, in rotation order.',
]))

story += section("Every morning", steps([
    "Open the card and look at the coloured pill at the top.",
]) + [
    Paragraph('<b>OK to dial</b> &nbsp;=&nbsp; 9:00 AM to 7:30 PM Eastern', sub),
    Paragraph('<b>Last calls</b> &nbsp;=&nbsp; until 7:45 PM', sub),
    Paragraph('<b>STOP</b> &nbsp;=&nbsp; do not dial', sub),
    Paragraph('Never plan by your own clock.', note),
    Paragraph("<b>2.</b>&nbsp; Work the 5 phases in order and tick each box as you finish it:", step),
    Paragraph("<b>1</b> &nbsp;Morning admin: yesterday's statuses, tasks, appointments, boards. Before any dialing.", sub),
    Paragraph("<b>2</b> &nbsp;Callbacks: everyone who is expecting your call.", sub),
    Paragraph("<b>3</b> &nbsp;FTM blitz: load <b>FTM - 02 Ready to Call</b>, Assignee = you, <b>All</b> tab. List order, no cherry-picking. If it runs dry go Call 2, Call 3, Call 4.", sub),
    Paragraph("<b>4</b> &nbsp;Hottest book: <b>Hottest - 02 Ready to Call</b>, same way, rest of the day.", sub),
    Paragraph("<b>5</b> &nbsp;End of shift checks.", sub),
])

story += section("On every record", steps([
    "Read the pinned note on the message board first. An empty board is normal.",
    "Dial the best untried number: <b>Dial First</b>, then <b>Dial Second</b>. No tag means top to bottom.",
    "<b>Set the status right away</b>, while it is fresh. No answer = set nothing.",
    "Next record. Never dial the same record twice in one day.",
]) + [Paragraph('Open <b>Quick reference</b> on the card for the status table, dial order and skip list.', note)])

story += section("Caller ID rotation", [
    Paragraph('Click <b>+1 call</b> for every number you dial.', step),
    Paragraph('At <b>25</b> the box turns red. Click <b>Switched - next number</b> before your next dial.', step),
    Paragraph('<b>Back a number</b> undoes a mis-click and gives your call count back.', step),
    Paragraph('The count does not reset overnight. That is on purpose, so numbers 8, 9 and 10 get used too.', note),
])

story += section("End of day", steps([
    "Type <b>FTM records worked</b> and <b>Hottest records worked</b> (records touched, not numbers dialed), then <b>Numbers dialed today</b> and <b>People you actually spoke to</b>.",
    "Add anything your manager should know in the notes box.",
    'Click <b>Copy EOD report</b> and paste it to your manager on WhatsApp. If the copy does not work, the text is already selected in the box: press <b>Ctrl+C</b>.',
    "You can click Copy again later to send an updated report.",
]))

story += section("Good to know", [
    Paragraph('Checks clear themselves at <b>midnight Eastern</b>.', step),
    Paragraph('<b>Clear today</b> (click twice) wipes the day early. Your name and rotation stay.', step),
    Paragraph('If the card says it is more than <b>14 days old</b>, ask your manager for a fresh one before you dial.', step),
    Paragraph('<font color="#B3372E"><b>Never</b></font> move records between lists and <font color="#B3372E"><b>never</b></font> press Save on a filter preset. The system advances every record on its own.', step),
])


def draw_header(canv, doc):
    canv.saveState()
    x, y = M, PH - M - HEAD_H
    canv.setFillColor(PRIMARY)
    canv.roundRect(x, y, PW - 2 * M, HEAD_H, 5, stroke=0, fill=1)
    canv.setFillColor(colors.HexColor("#E3ECF5"))
    canv.setFont("Helvetica-Bold", 8.6)
    canv.drawString(x + 18, y + HEAD_H - 22, "DIALER DAY CARD")
    canv.setFillColor(colors.white)
    canv.setFont("Helvetica-Bold", 21)
    canv.drawString(x + 18, y + HEAD_H - 48, "How to use it")
    canv.setFillColor(colors.HexColor("#C7D8EA"))
    canv.setFont("Helvetica", 8.8)
    canv.drawString(x + 18, y + HEAD_H - 64,
                    "Keep this next to you for the first week.")
    canv.drawRightString(PW - M - 18, y + HEAD_H - 64,
                         "All times U.S. Eastern  |  card build %s" % BUILD)
    canv.restoreState()


doc = BaseDocTemplate(OUT, pagesize=letter,
                      leftMargin=M, rightMargin=M, topMargin=M, bottomMargin=M,
                      title="Dialer Day Card - How to use it",
                      author="SiftStack")
f1 = Frame(M, M, COL_W, BODY_H, leftPadding=0, rightPadding=0,
           topPadding=0, bottomPadding=0)
while story and isinstance(story[-1], Spacer):
    story.pop()
doc.addPageTemplates([PageTemplate(id="one", frames=[f1], onPage=draw_header)])
doc.build(story)

from pypdf import PdfReader

pages = len(PdfReader(OUT).pages)
print("card  %s (build %s)" % (os.path.relpath(CARD, ROOT), BUILD))
print("sheet %s - %d page%s" % (os.path.relpath(OUT, ROOT), pages, "" if pages == 1 else "s"))
if pages != 1:
    raise SystemExit("The sheet must stay ONE page - tighten the styles, do not ship %d." % pages)
