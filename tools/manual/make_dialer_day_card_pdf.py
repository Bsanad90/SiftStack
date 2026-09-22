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

body = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=12.8,
                      textColor=INK, alignment=TA_LEFT, spaceAfter=0)
step = ParagraphStyle("step", parent=body, leftIndent=14, firstLineIndent=-14,
                      spaceAfter=3.6)
sub = ParagraphStyle("sub", parent=body, fontSize=9.4, leading=12.0,
                     leftIndent=28, firstLineIndent=-12, textColor=SOFT,
                     spaceAfter=2.2)
note = ParagraphStyle("note", parent=body, fontSize=9.4, leading=12.0,
                      textColor=SOFT, leftIndent=14, spaceAfter=3.6)
head = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=11.2,
                      leading=13, textColor=PRIMARY, spaceBefore=0, spaceAfter=4)


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
    out.append(Spacer(1, 8))
    return out


def steps(items):
    return [Paragraph("<b>%d.</b>&nbsp; %s" % (i + 1, t), step)
            for i, t in enumerate(items)]


story = []

story += section("Set up once", steps([
    "Open the card on your work computer in <b>Chrome</b> or <b>Edge</b> and bookmark it. Same computer every day - it saves there.",
    "Type your name in the <b>top bar</b>. It is remembered.",
    'Open <b>"Set up my 10 numbers"</b> and paste your 10 caller IDs, one per line, in rotation order.',
]))

story += section("Every morning", steps([
    "Check the pill at the top: <b>OK to dial</b> 9:00 AM - 7:30 PM Eastern, <b>Last calls</b> to 7:45, then <b>STOP</b>. Never your own clock.",
    "Work the 5 phases in order and tick each box: morning admin, callbacks, FTM, Hottest, end of shift.",
]))

story += section("The order - work in rounds", [
    Paragraph('<b>FTM Ready to Call &rarr; Hottest Ready to Call &rarr; FTM Call 2 &rarr; Hottest Call 2 '
              '&rarr; FTM Call 3 &rarr; Hottest Call 3 &rarr; Call 4</b>', step),
    Paragraph('FTM dry? Go to <b>Hottest</b> - not deeper into FTM. Drop a rung only when both are dry.', step),
])

story += section("On every record", steps([
    "Read the pinned note on the message board. Empty is normal.",
    "No status yet? Set <b>Prospecting</b> before you dial. It stays Prospecting until a call moves it.",
    "Dial the best untried number: <b>Dial First</b>, then <b>Dial Second</b>. No tag = top to bottom.",
    "Set the status right after the call. No answer = leave it. Never dial a record twice in one day.",
]) + [Paragraph('The full status table, dial order and skip list are under <b>Quick reference</b> on the card.', note)])

# Basem 2026-09-21: counts, not stories. The one exception is a lead they are unsure
# about - and the rule there is pass it on, because a missed qualified lead is gone.
story += section("Notes - counts only", [
    Paragraph('In the top-bar box, as they happen: <b>follow-ups</b>, <b>cold leads</b>, '
              '<b>warm leads in follow-up</b>, <b>unqualified leads</b>, how many need an '
              '<b>email</b> or a <b>text</b>.', step),
    Paragraph('Write a sentence only when you are unsure a lead qualifies. <b>When in doubt, pass it on</b> '
              '- an unqualified lead costs minutes, a qualified one you talked yourself out of is gone.', step),
    Paragraph('Problems, questions, a record that looks wrong: say it in the group, not the notes.', step),
])

story += section("Caller ID", [
    Paragraph('Click <b>+1 call</b> for every dial. At <b>25</b>, click <b>Switched - next number</b>. '
              '<b>Back a number</b> undoes a mis-click. The count carries overnight on purpose.', step),
])

story += section("End of day", steps([
    "Type the four numbers: <b>FTM worked</b>, <b>Hottest worked</b>, <b>dials</b>, <b>people you spoke to</b>.",
    "Click <b>Copy EOD report</b> and paste it to your manager on WhatsApp. If copy fails, press <b>Ctrl+C</b> - the text is already selected.",
]))

story += section("Good to know", [
    Paragraph('Checks clear at midnight Eastern. <b>Clear today</b> (click twice) wipes the day early. '
              'Card more than <b>14 days old</b>? Ask for a fresh one.', step),
    Paragraph('<font color="#B3372E"><b>Never</b></font> move records between lists and '
              '<font color="#B3372E"><b>never</b></font> press Save on a filter preset. '
              'The system advances every record on its own.', step),
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
    raise SystemExit(
        "The sheet must stay ONE page - cut words or tighten the styles, do not ship %d." % pages)
