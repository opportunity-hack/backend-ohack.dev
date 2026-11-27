"""
The participation letter template — ONE module renders both the watermarked
preview and the signed final PDF (ReportLab, pure Python, no system packages).

`compose_letter(facts)` is the locked wording: pure, returns the text blocks,
and drops any sentence whose optional fact is missing. `render_pdf(...)` lays
those blocks out on US Letter with Opportunity Hack letterhead.

Layout: logo top left, small-caps "{SEASON YEAR} | {CITY, STATE}" top right,
navy rule; date; recipient; salutation; body; "Sincerely," + typeset
signature + signer block; verification line + QR; navy rule + org footer.
Everything is on one page (tests assert it).

Fonts are bundled under assets/ (OFL): Noto Serif/Sans for text (Latin,
Greek, Cyrillic, Vietnamese), Allura for the typeset signature. CJK runs in a
name fall back to ReportLab's built-in CID fonts (STSong / HeiseiMin /
HYSMyeongJo — no file to bundle; the viewer supplies the glyphs). Characters
in neither are reported by `unsupported_characters` so the API can refuse the
name instead of printing empty boxes.
"""
import io
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

import pytz
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter as LETTER_SIZE
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, Image, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
)

from api.letters.letter_facts import format_long_date, join_names, MAX_LISTED_NAMES

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# ---- Organization block (twin: frontend src/lib/orgInfo.js) ----------------
ORG_LEGAL_NAME = "Opportunity Hack, Inc."
ORG_TAX_STATUS = "501(c)(3) nonprofit"
ORG_EIN = "84-5113049"
ORG_CITY_STATE_ZIP = "Phoenix, Arizona 85016"
ORG_SITES = "ohack.org | ohack.dev"
# Caps except "(c)", which is part of the code section's name.
FOOTER_LEFT = (f"{ORG_LEGAL_NAME.upper()} | 501(c)(3) NONPROFIT | EIN {ORG_EIN} | "
               f"{ORG_CITY_STATE_ZIP.upper()}")

VERIFY_BASE_URL = os.getenv("LETTERS_VERIFY_BASE_URL", "https://www.ohack.dev/letters/verify")

NAVY = colors.HexColor("#1B3A6B")
INK = colors.HexColor("#16181D")
MUTED = colors.HexColor("#5B6170")
SIGNATURE_INK = colors.HexColor("#1F2F55")

LOGO_PATH = os.path.join(ASSETS, "logo_banner.png")

ISSUE_TZ = "America/Phoenix"


@dataclass(frozen=True)
class SignerConfig:
    """Who signs. A scanned signature image (PNG with transparency) replaces
    the typeset font when `signature_image` points at a readable file."""
    name: str = "Greg Vannoni"
    title: str = "President, Opportunity Hack, Inc."
    signature_image: Optional[str] = None


def default_signer() -> SignerConfig:
    return SignerConfig(
        name=os.getenv("LETTERS_SIGNER_NAME") or SignerConfig.name,
        title=os.getenv("LETTERS_SIGNER_TITLE") or SignerConfig.title,
        signature_image=os.getenv("LETTERS_SIGNATURE_IMAGE") or None,
    )


# ---- Fonts -----------------------------------------------------------------
SERIF, SERIF_BOLD, SANS, SANS_BOLD, SCRIPT = (
    "LetterSerif", "LetterSerif-Bold", "LetterSans", "LetterSans-Bold", "LetterScript")
CJK_FONTS = {"zh": "STSong-Light", "ja": "HeiseiMin-W3", "ko": "HYSMyeongJo-Medium"}
_fonts_ready = False


def register_fonts():
    global _fonts_ready
    if _fonts_ready:
        return
    for name, filename in ((SERIF, "NotoSerif-Regular.ttf"), (SERIF_BOLD, "NotoSerif-Bold.ttf"),
                           (SANS, "NotoSans-Regular.ttf"), (SANS_BOLD, "NotoSans-Bold.ttf"),
                           (SCRIPT, "Allura-Regular.ttf")):
        pdfmetrics.registerFont(TTFont(name, os.path.join(ASSETS, filename)))
    pdfmetrics.registerFontFamily(SERIF, normal=SERIF, bold=SERIF_BOLD,
                                  italic=SERIF, boldItalic=SERIF_BOLD)
    for cid in CJK_FONTS.values():
        pdfmetrics.registerFont(UnicodeCIDFont(cid))
    _fonts_ready = True


def _cjk_lang(ch: str) -> Optional[str]:
    cp = ord(ch)
    if 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F:
        return "ko"
    if 0x3040 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF or 0xFF66 <= cp <= 0xFF9F:
        return "ja"
    if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0xF900 <= cp <= 0xFAFF
            or 0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFF65):
        return "zh"
    return None


def _serif_covers(ch: str) -> bool:
    register_fonts()
    return ord(ch) in pdfmetrics.getFont(SERIF).face.charToGlyph


def unsupported_characters(text: str) -> List[str]:
    """Characters the letter cannot print (neither the bundled serif nor a
    CJK fallback has them), deduped in order."""
    out = []
    for ch in text or "":
        if ch.isspace() or _serif_covers(ch) or _cjk_lang(ch):
            continue
        if ch not in out:
            out.append(ch)
    return out


def _markup(text: str) -> str:
    """Escape for Paragraph markup and route CJK runs to a CID font. A name
    mixing Chinese and Japanese uses the font of the first CJK script seen
    (Japanese kana anywhere -> HeiseiMin) so one name renders in one face."""
    if not text:
        return ""
    langs = {_cjk_lang(c) for c in text} - {None}
    if not langs:
        return escape(text)
    lang = "ja" if "ja" in langs else ("ko" if "ko" in langs else "zh")
    font = CJK_FONTS[lang]
    out, run, run_cjk = [], "", None
    for ch in text:
        is_cjk = _cjk_lang(ch) is not None or (run_cjk and ch == " ")
        if run and is_cjk != run_cjk:
            out.append(f'<font name="{font}">{escape(run)}</font>' if run_cjk else escape(run))
            run = ""
        run += ch
        run_cjk = is_cjk
    if run:
        out.append(f'<font name="{font}">{escape(run)}</font>' if run_cjk else escape(run))
    return "".join(out)


# ---- Wording (locked) --------------------------------------------------------
OPENING = "On behalf of the Opportunity Hack team, I am writing to confirm that you"
CLOSING = ("This letter is provided at your request. Thank you for your service "
           "and your support of our nonprofit partners.")
JUDGING_CRITERIA = ("the published Opportunity Hack judging criteria "
                    "(Scope, Documentation, Polish and Security)")
ROLE_PHRASE = {
    "judge": "served as a judge at",
    "mentor": "served as a mentor at",
    "volunteer": "served as an event volunteer at",
    "hacker": "participated in",
}


def _listed(names: List[str]) -> str:
    names = [n for n in names if n]
    if len(names) > MAX_LISTED_NAMES:
        return ", ".join(names[:MAX_LISTED_NAMES]) + ", and others"
    return join_names(names)


def _where_when(facts: Dict[str, Any]) -> str:
    """", held in person at X in Tempe, Arizona, on November 15, 2026" —
    every piece optional."""
    held = ""
    if facts.get("attendance") == "in_person":
        held = ", held in person"
        if facts.get("venue"):
            held += f" at {facts['venue']}"
        if facts.get("city_state"):
            held += f" in {facts['city_state']}"
    elif facts.get("attendance") == "virtual":
        held = ", held virtually"
    when = f" on {facts['served_date']}" if facts.get("served_date") else ""
    return held + when


def compose_letter(facts: Dict[str, Any]) -> Dict[str, Any]:
    """The letter's text. Pure; every optional fact drops its own sentence or
    clause. Returns {recipient: [lines], salutation, paragraphs: [str]}."""
    role = facts["role"]
    name = facts["name"]
    event = facts.get("event_title") or "an Opportunity Hack event"
    first = f"{OPENING} {ROLE_PHRASE[role]} {event}{_where_when(facts)}."

    second = ""
    if role == "judge":
        n = facts.get("n_teams")
        if n:
            projects = ("one team project built for a nonprofit organization" if n == 1
                        else f"{n} team projects built for nonprofit organizations")
            nonprofits = _listed(facts.get("nonprofits") or [])
            including = f", including {nonprofits}" if nonprofits else ""
            second = (f"You applied to judge, were approved by the Opportunity Hack team, "
                      f"and evaluated {projects}{including}, against {JUDGING_CRITERIA}.")
        else:
            second = (f"You applied to judge, were approved by the Opportunity Hack team, "
                      f"and evaluated team projects against {JUDGING_CRITERIA}.")
    elif role == "mentor":
        second = ("You applied to mentor, were approved by the Opportunity Hack team, "
                  "and supported participating teams during the event.")
        teams = _listed(facts.get("mentor_teams") or [])
        if teams:
            second += f" Event records show you worked with {teams}."
    elif role == "volunteer":
        second = "You applied to volunteer and were approved by the Opportunity Hack team."
    elif role == "hacker" and facts.get("team_name"):
        second = f"You were a member of team {facts['team_name']}"
        project, nonprofit = facts.get("project_title"), facts.get("nonprofit_name")
        if project and nonprofit:
            second += f", which built {project} for {nonprofit}"
        elif project:
            second += f", which built {project}"
        elif nonprofit:
            second += f", which built a project for {nonprofit}"
        second += "."
        awards = [a for a in (facts.get("awards") or []) if a]
        if awards:
            second += f" The team received {join_names(awards)}."

    recipient = [name] + ([facts["title_line"]] if facts.get("title_line") else [])
    return {
        "recipient": recipient,
        "salutation": f"Dear {name},",
        "paragraphs": [p for p in (first, second, CLOSING) if p],
    }


def letter_text(facts: Dict[str, Any]) -> str:
    """The composed wording as plain text (tests + admin facts view)."""
    c = compose_letter(facts)
    return "\n\n".join(["\n".join(c["recipient"]), c["salutation"], *c["paragraphs"]])


def verify_url(letter_id: str) -> str:
    return f"{VERIFY_BASE_URL.rstrip('/')}/{letter_id}"


def issue_date_today() -> date:
    return datetime.now(pytz.timezone(ISSUE_TZ)).date()


# ---- Rendering ----------------------------------------------------------------
PAGE_W, PAGE_H = LETTER_SIZE
MARGIN_X = 1 * inch
HEADER_RULE_Y = PAGE_H - 1.2 * inch
FOOTER_RULE_Y = 0.85 * inch


def _styles():
    register_fonts()
    body = ParagraphStyle("body", fontName=SERIF, fontSize=10.5, leading=15,
                          textColor=INK, alignment=TA_LEFT, spaceAfter=9)
    return {
        "body": body,
        "date": ParagraphStyle("date", parent=body, spaceAfter=16),
        "name": ParagraphStyle("name", parent=body, fontName=SERIF_BOLD, spaceAfter=0),
        "recipient": ParagraphStyle("recipient", parent=body, spaceAfter=0),
        "salutation": ParagraphStyle("salutation", parent=body, spaceBefore=16, spaceAfter=9),
        "signature": ParagraphStyle("signature", fontName=SCRIPT, fontSize=30, leading=32,
                                    textColor=SIGNATURE_INK),
        "signer": ParagraphStyle("signer", parent=body, spaceAfter=0, leading=14),
        "small": ParagraphStyle("small", parent=body, fontName=SANS, fontSize=8.5,
                                leading=12, textColor=MUTED, spaceAfter=0),
        "note": ParagraphStyle("note", parent=body, fontName=SANS, fontSize=8.5,
                               leading=12, textColor=MUTED, spaceBefore=6),
    }


def _fit_text(c, text: str, font: str, size: float, space: float, max_w: float):
    """Shrink letter-spaced caps until they fit `max_w`."""
    while size > 5:
        width = pdfmetrics.stringWidth(text, font, size) + space * max(len(text) - 1, 0)
        if width <= max_w:
            return size, width
        size -= 0.25
    return size, pdfmetrics.stringWidth(text, font, size)


def _draw_spaced(c, x: float, y: float, text: str, font: str, size: float, space: float):
    t = c.beginText(x, y)
    t.setFont(font, size)
    t.setCharSpace(space)
    t.textOut(text)
    c.drawText(t)


def _page_decor(header_label: Optional[str], preview: bool):
    def draw(c, _doc):
        c.saveState()
        # Logo (vendored; 2048x312 banner).
        logo_h = 0.4 * inch
        logo_w = logo_h * 2048 / 312
        c.drawImage(LOGO_PATH, MARGIN_X, HEADER_RULE_Y + 0.16 * inch, width=logo_w,
                    height=logo_h, mask="auto")
        if header_label:
            size, width = _fit_text(c, header_label, SANS_BOLD, 8.5, 1.1,
                                    PAGE_W - 2 * MARGIN_X - logo_w - 0.2 * inch)
            c.setFillColor(NAVY)
            _draw_spaced(c, PAGE_W - MARGIN_X - width, HEADER_RULE_Y + 0.26 * inch,
                         header_label, SANS_BOLD, size, 1.1)
        c.setStrokeColor(NAVY)
        c.setLineWidth(1.4)
        c.line(MARGIN_X, HEADER_RULE_Y, PAGE_W - MARGIN_X, HEADER_RULE_Y)
        # Footer.
        c.setLineWidth(0.9)
        c.line(MARGIN_X, FOOTER_RULE_Y, PAGE_W - MARGIN_X, FOOTER_RULE_Y)
        c.setFillColor(MUTED)
        right_size, right_w = _fit_text(c, ORG_SITES, SANS, 7, 0.5, 1.4 * inch)
        avail = PAGE_W - 2 * MARGIN_X - right_w - 0.2 * inch
        left_size, _ = _fit_text(c, FOOTER_LEFT, SANS, 7, 0.5, avail)
        _draw_spaced(c, MARGIN_X, FOOTER_RULE_Y - 0.2 * inch, FOOTER_LEFT, SANS, left_size, 0.5)
        _draw_spaced(c, PAGE_W - MARGIN_X - right_w, FOOTER_RULE_Y - 0.2 * inch,
                     ORG_SITES, SANS, right_size, 0.5)
        if preview:
            c.setFillColor(colors.Color(0.78, 0.2, 0.15, alpha=0.16))
            c.translate(PAGE_W / 2, PAGE_H / 2)
            c.rotate(40)
            c.setFont(SANS_BOLD, 58)
            c.drawCentredString(0, -20, "PREVIEW, NOT VALID")
        c.restoreState()
    return draw


def _qr_image(url: str, size: float) -> Image:
    from api.certificates.qr_code import generate_qr_code
    img = generate_qr_code(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Image(buf, width=size, height=size)


def _header_label(facts: Dict[str, Any]) -> Optional[str]:
    parts = [p for p in (facts.get("season_label"), facts.get("city_state")) if p]
    return " | ".join(parts).upper() or None


def render_pdf(facts: Dict[str, Any], *, letter_id: Optional[str] = None,
               issued_on: Optional[date] = None, preview: bool = False,
               signer: Optional[SignerConfig] = None) -> bytes:
    """The letter as PDF bytes. `preview=True` adds a diagonal
    "PREVIEW, NOT VALID" watermark and leaves out the signature, the letter
    ID and the verification line. A final letter needs `letter_id`.

    Output is byte-stable for the same inputs (ReportLab invariant mode)."""
    if not preview and not letter_id:
        raise ValueError("a final letter needs a letter_id")
    register_fonts()
    signer = signer or default_signer()
    issued_on = issued_on or issue_date_today()
    st = _styles()
    text = compose_letter(facts)

    flow = [Paragraph(f"Date: {format_long_date(issued_on)}", st["date"])]
    flow.append(Paragraph(_markup(text["recipient"][0]), st["name"]))
    for line in text["recipient"][1:]:
        flow.append(Paragraph(_markup(line), st["recipient"]))
    flow.append(Paragraph(_markup(text["salutation"]), st["salutation"]))
    for para in text["paragraphs"]:
        flow.append(Paragraph(_markup(para), st["body"]))
    flow.append(Paragraph("Sincerely,", ParagraphStyle("s", parent=st["body"], spaceBefore=4,
                                                         spaceAfter=2)))
    if preview:
        flow.append(Spacer(1, 30))
    elif signer.signature_image and os.path.isfile(signer.signature_image):
        reader = ImageReader(signer.signature_image)
        w, h = reader.getSize()
        sig_h = 0.55 * inch
        flow.append(Image(signer.signature_image, width=sig_h * w / h, height=sig_h, hAlign="LEFT"))
    else:
        flow.append(Paragraph(escape(signer.name), st["signature"]))
    flow.append(HRFlowable(width=2.8 * inch, thickness=0.6, color=MUTED, hAlign="LEFT",
                           spaceBefore=4, spaceAfter=7))
    flow.append(Paragraph(f"<b>{escape(signer.name)}</b>", st["signer"]))
    flow.append(Paragraph(escape(signer.title), st["signer"]))
    if preview:
        flow.append(Paragraph("Preview only. The issued letter carries the signature, "
                              "a letter ID and a verification link.", st["note"]))
    else:
        flow.append(Paragraph(f"Electronically signed {format_long_date(issued_on)}", st["small"]))
        flow.append(Paragraph(f"Letter ID: {escape(letter_id)}", st["small"]))
        url = verify_url(letter_id)
        verify = Paragraph(
            f'Verify this letter: <link href="{escape(url)}" color="#1B3A6B">{escape(url)}</link>',
            ParagraphStyle("v", parent=st["small"], textColor=INK, fontSize=9))
        table = Table([[_qr_image(url, 0.8 * inch), verify]],
                      colWidths=[0.95 * inch, 5.55 * inch], hAlign="LEFT")
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        flow.append(Spacer(1, 14))
        flow.append(table)

    buf = io.BytesIO()
    top, bottom = PAGE_H - HEADER_RULE_Y + 0.3 * inch, FOOTER_RULE_Y + 0.25 * inch
    doc = BaseDocTemplate(
        buf, pagesize=LETTER_SIZE, invariant=1,
        title="Opportunity Hack participation letter" + (" (preview)" if preview else ""),
        author=ORG_LEGAL_NAME, subject=facts.get("event_title") or "",
        leftMargin=MARGIN_X, rightMargin=MARGIN_X, topMargin=top, bottomMargin=bottom,
    )
    frame = Frame(MARGIN_X, bottom, PAGE_W - 2 * MARGIN_X, PAGE_H - top - bottom,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="body")
    doc.addPageTemplates([PageTemplate(id="letter", frames=[frame],
                                       onPage=_page_decor(_header_label(facts), preview))])
    doc.build(flow)
    return buf.getvalue()


def page_count(pdf: bytes) -> int:
    """Pages in a PDF we rendered (counts page objects; good enough for our
    own output, used by tests and the sample script)."""
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))
