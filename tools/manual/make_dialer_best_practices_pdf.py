# -*- coding: utf-8 -*-
"""One-page dialer best-practices sheet - how to get answered and stay legal.

Companion to the Dialer Day Card sheet (make_dialer_day_card_pdf.py); same palette,
same one-page gate. The card says WHAT to work; this says HOW to call.

    python tools/manual/make_dialer_best_practices_pdf.py

Sources, all distilled 2026-09-22 - re-read them before changing a number here:
  - TNS (Transaction Network Services), "Recommended Best Practices: Managing TN
    Reputation", May 2025. Source of the 3-calls-in-7-days / 2-per-week-for-cold /
    no-flurry-dialing caps, the dead-air rule and the one-number-one-purpose rule.
  - smrtPhone help center: spam-marked numbers (short-call share over 15pc and ALOC
    under 30s are unhealthy, ASR under 30pc triggers complaints), spam words,
    compliance.
  - NumberVerifier KB: rotate caller IDs, and remediate a flagged number rather than
    swap it. Their ceiling is 75 calls per ID per day; the sheet does NOT carry it -
    the day card already says switch at 25, and two numbers in one sheet is how a
    dialer ends up dialing to the looser one. If the card's 25 ever changes, this is
    the line that has to move with it.
  - Hiya, "10 Tips for Customer-Friendly Phone Calls": hang-ups are a preference
    signal (a SILENT answer is not - that is a pocket answer or a bad line, and the
    sheet says so), and no dialer pause on connect.

Wording rules (inherited from the day card sheet): the dialers work on a PC; hours are
U.S. Eastern and match the card exactly - if the card's window changes, change it here
the same day. A sheet that disagrees with the card in the dialer's hand is worse than
no sheet.
"""
import os

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Flowable,
                                Paragraph, Spacer)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "output", "Dialer_Best_Practices.pdf")
BUILD = "2026-09-22"

INK     = colors.HexColor("#1C2430")
SOFT    = colors.HexColor("#55616F")
LINE    = colors.HexColor("#D8DEE6")
PRIMARY = colors.HexColor("#2B5A8C")

PW, PH = letter
M = 0.5 * inch
HEAD_H = 0.92 * inch
COL_W = PW - 2 * M
BODY_TOP = PH - M - HEAD_H - 0.12 * inch
BODY_H = BODY_TOP - M

body = ParagraphStyle("body", fontName="Helvetica", fontSize=9.1, leading=11.3,
                      textColor=INK, alignment=TA_LEFT, spaceAfter=0)
step = ParagraphStyle("step", parent=body, leftIndent=13, firstLineIndent=-13,
                      spaceAfter=2.4)
note = ParagraphStyle("note", parent=body, fontSize=8.5, leading=10.6,
                      textColor=SOFT, spaceAfter=2.4)
head = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=10.2,
                      leading=11.8, textColor=PRIMARY, spaceBefore=0, spaceAfter=3)


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


def section(title, flowables):
    out = [Paragraph(title.upper(), head), Rule(COL_W), Spacer(1, 3)]
    out.extend(flowables)
    out.append(Spacer(1, 6))
    return out


def bullets(items):
    return [Paragraph("&bull;&nbsp; %s" % t, step) for t in items]


def steps(items):
    return [Paragraph("<b>%d.</b>&nbsp; %s" % (i + 1, t), step)
            for i, t in enumerate(items)]


def red(t):
    return '<font color="#B3372E"><b>%s</b></font>' % t


story = []

story.append(Paragraph(
    "Carriers score every number you dial from. Short calls, dead air, repeat dials and "
    "wrong numbers get it labelled <b>Spam Likely</b> - and then nobody answers, for anyone "
    "on the team. The five things below protect it. They are also the five that get you more "
    "conversations.", note))
story.append(Spacer(1, 6))

story += section("1. The first ten seconds decide", steps([
    "<b>Be there the instant they answer.</b> No pause, no dead air, no hold music. That is the "
    "number one reason a call gets reported.",
    "<b>Name, company, reason</b> - in the first three seconds, and use the same name that shows "
    "on their screen.",
    "<b>Never hang up the moment it is the wrong person.</b> Close it politely. Anything under "
    "six seconds counts against the number; we need to average over thirty.",
    "<b>No answer? Leave a real voicemail</b> - who you are, why you called, a callback number. "
    "It lifts the number's health and it brings callbacks.",
    "<b>Ask anyone who talks to save the number.</b> A saved contact tells the carrier the call "
    "was wanted.",
]))

story += section("2. How often you may call one person", bullets([
    "Cold record: <b>2 dials a week</b>, and never <b>2 inside 4 hours</b>.",
    "Someone who has spoken with us or asked us to call: <b>3 in 7 days</b>.",
    "<b>Never dial the same record twice in one day.</b> No instant redial after a no-answer - "
    "that pattern on its own reads as a nuisance dialer.",
    "Voicemail landed, or the call came back blocked? That was the attempt. Do not redial.",
    "<b>They hang up as soon as they hear why you are calling?</b> That is an answer. Mark it and "
    "do not dial that number again.",
    "Silence or an instant drop is usually a pocket answer or a bad line, <b>not</b> a refusal. "
    "That one does not count - normal retry rules apply.",
    "A record you have been dialing for <b>months</b> that has never once been answered: say so "
    "in the group instead of dialing it again.",
]))

story += section("3. Hard stops", bullets([
    "<b>9:00 AM - 7:30 PM Eastern, last calls 7:45.</b> Their local time, never your own clock.",
    "%s &quot;Take me off your list&quot; or &quot;don't call again&quot; - mark DNC on that "
    "record <b>during the call</b>. No callback, ever, on any of their numbers."
    % red("Stop on the words."),
    "Wrong or disconnected number: mark it and stop. Repeat dials to dead numbers flag the whole "
    "account.",
    "Never misstate who you are, who you work for, or why you are calling.",
    "%s Violations run <b>$500 - $2,000 each</b>, and they come back to us." % red("This is law."),
]))

story += section("4. Words that get us flagged", [
    Paragraph(
        "Carriers scan calls and texts for sales language. Avoid: <b>guaranteed, 100% free, no "
        "obligation, no cost, fast cash, act now, urgent, today only, expires, discount, bad "
        "credit, winner, congratulations</b>, and <b>ANY DOLLAR FIGURE</b>.", step),
    Paragraph(
        "In texts, also: no ALL CAPS, no stacked exclamation marks, no links, no emoji. Write one "
        "person to one person - anything that reads like a blast gets filtered like one.", step),
    Paragraph(
        "Say it plainly instead: you buy houses in their area, and you wanted to ask whether they "
        "would ever consider selling theirs. Plain beats salesy on the carrier score and on the "
        "call.", step),
])

story += section("5. Your caller IDs", bullets([
    "<b>+1 on the card for every dial. Switch at 25.</b> Work the rotation in order - no "
    "favourites, no skipping, and never run one number past its 25.",
    "Answer rate falls off a cliff, or someone tells you it shows <b>Spam Likely</b>? Say it in "
    "the group <b>that day</b>. The number gets repaired, not thrown away - swapping in a fresh "
    "one without fixing it only burns the next one.",
    "Work numbers are for these calls only. Nothing else, ever.",
]))

story.append(Paragraph(
    "Ops covers what you cannot see: number registration, branded caller ID, STIR/SHAKEN, "
    "warming up new numbers and DNC scrubbing. The five above are yours.", note))

story.append(Paragraph(
    "Sources: TNS <i>Managing TN Reputation</i> (May 2025) &middot; smrtPhone help center "
    "&middot; NumberVerifier KB &middot; Hiya, <i>10 Tips for Customer-Friendly Phone Calls</i>.",
    ParagraphStyle("src", parent=note, fontSize=7.6, leading=9.2)))


def draw_header(canv, doc):
    canv.saveState()
    x, y = M, PH - M - HEAD_H
    canv.setFillColor(PRIMARY)
    canv.roundRect(x, y, PW - 2 * M, HEAD_H, 5, stroke=0, fill=1)
    canv.setFillColor(colors.HexColor("#E3ECF5"))
    canv.setFont("Helvetica-Bold", 8.6)
    canv.drawString(x + 18, y + HEAD_H - 21, "DIALER BEST PRACTICES")
    canv.setFillColor(colors.white)
    canv.setFont("Helvetica-Bold", 20)
    canv.drawString(x + 18, y + HEAD_H - 45, "Getting answered, and staying legal")
    canv.setFillColor(colors.HexColor("#C7D8EA"))
    canv.setFont("Helvetica", 8.8)
    canv.drawString(x + 18, y + HEAD_H - 60, "Keep this next to your day card.")
    canv.drawRightString(PW - M - 18, y + HEAD_H - 60,
                         "All times U.S. Eastern  |  build %s" % BUILD)
    canv.restoreState()


doc = BaseDocTemplate(OUT, pagesize=letter,
                      leftMargin=M, rightMargin=M, topMargin=M, bottomMargin=M,
                      title="Dialer Best Practices",
                      author="SiftStack")
f1 = Frame(M, M, COL_W, BODY_H, leftPadding=0, rightPadding=0,
           topPadding=0, bottomPadding=0)
while story and isinstance(story[-1], Spacer):
    story.pop()
doc.addPageTemplates([PageTemplate(id="one", frames=[f1], onPage=draw_header)])
doc.build(story)

from pypdf import PdfReader

pages = len(PdfReader(OUT).pages)
print("sheet %s - %d page%s" % (os.path.relpath(OUT, ROOT), pages, "" if pages == 1 else "s"))
if pages != 1:
    raise SystemExit(
        "The sheet must stay ONE page - cut words or tighten the styles, do not ship %d." % pages)
