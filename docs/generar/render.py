#!/usr/bin/env python
"""Render the MVP-CRM technical documentation to PDF.

Inputs: front.json (cover, intro, appendices) and chapters.json (the
workflow's output: verified chapters in the CHAPTER schema). Output: a PDF
with cover, "Sobre este documento", table of contents, numbered chapters,
and appendices. Base-14 fonts only (Helvetica/Courier): they cover Latin-1,
so Spanish accents render without embedding a font.
"""
import json
import re
import sys
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Preformatted, Spacer,
                                Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

ACCENT = colors.HexColor("#2f6fd6")
INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#6b7280")
RULE = colors.HexColor("#d5dbe3")
CODE_BG = colors.HexColor("#f3f5f8")
NOTE_BG = colors.HexColor("#eef4fd")
HEAD_BG = colors.HexColor("#e8eef7")

W, H = A4
M = 2 * cm
AVAIL = W - 2 * M

S = {
    "cover_title": ParagraphStyle("cover_title", fontName="Helvetica-Bold", fontSize=32, leading=38, textColor=ACCENT, spaceAfter=10),
    "cover_sub": ParagraphStyle("cover_sub", fontName="Helvetica", fontSize=17, leading=22, textColor=INK, spaceAfter=28),
    "cover_meta": ParagraphStyle("cover_meta", fontName="Helvetica", fontSize=10.5, leading=15, textColor=MUTED),
    "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=21, leading=26, textColor=INK, spaceBefore=4, spaceAfter=10, keepWithNext=True),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=INK, spaceBefore=13, spaceAfter=5, keepWithNext=True),
    "h3": ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=INK, spaceBefore=8, spaceAfter=4, keepWithNext=True),
    "lead": ParagraphStyle("lead", fontName="Helvetica-Oblique", fontSize=10.5, leading=15, textColor=MUTED, spaceAfter=10),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14.2, textColor=INK, spaceAfter=6),
    "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=10, leading=14, textColor=INK, leftIndent=14, bulletIndent=3, spaceAfter=2.5),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.4, leading=10.8, textColor=INK),
    "cell_head": ParagraphStyle("cell_head", fontName="Helvetica-Bold", fontSize=8.4, leading=10.8, textColor=INK),
    "code": ParagraphStyle("code", fontName="Courier", fontSize=8, leading=10.2, textColor=INK),
    "note": ParagraphStyle("note", fontName="Helvetica", fontSize=9.5, leading=13.2, textColor=INK),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.2, leading=11, textColor=MUTED, spaceBefore=8),
    "caption": ParagraphStyle("caption", fontName="Helvetica-Oblique", fontSize=8.4, leading=11, textColor=MUTED, spaceBefore=2, spaceAfter=8),
    "toc1": ParagraphStyle("toc1", fontName="Helvetica-Bold", fontSize=10.5, leading=15, textColor=INK, spaceBefore=4),
    "toc2": ParagraphStyle("toc2", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK, leftIndent=16),
}

CODE_RE = re.compile(r"`([^`]+)`")


def fmt(text):
    """Escape for Paragraph markup, then turn `spans` into Courier."""
    text = escape(str(text))
    return CODE_RE.sub(lambda m: '<font face="Courier" size="8.8">%s</font>' % m.group(1), text)


class Doc(BaseDocTemplate):
    """Registers TOC entries and PDF outline bookmarks for tagged headings."""

    def afterFlowable(self, flowable):
        toc = getattr(flowable, "_toc", None)
        if not toc:
            return
        level, text, key = toc
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(text, key, level=level, closed=False)
        self.notify("TOCEntry", (level, text, self.page, key))


def heading(text, style, level, key):
    p = Paragraph(escape(text), S[style])
    p._toc = (level, text, key)
    return p


def block_flowables(b):
    kind = b.get("kind")
    if kind == "paragraph":
        return [Paragraph(fmt(b.get("text", "")), S["body"])]
    if kind == "bullets":
        return [Paragraph(fmt(i), S["bullet"], bulletText="•") for i in b.get("items", [])]
    if kind == "code":
        text = str(b.get("text", "")).rstrip("\n")
        # An ASCII diagram loses its meaning if a line wraps, so scale the
        # font down (to a legible floor) until the longest line fits.
        usable = AVAIL - 16
        longest = max((len(l) for l in text.split("\n")), default=0)
        size = 8.0
        if longest:
            size = max(5.8, min(8.0, usable / (0.6 * longest)))
        style = S["code"] if size >= 7.95 else ParagraphStyle(
            "code_fit", parent=S["code"], fontSize=size, leading=size * 1.28)
        # The font was sized so `longest` fits; wrap at exactly that many
        # characters so rounding can never orphan a trailing character.
        pre = Preformatted(text, style, maxLineLength=max(20, longest))
        box = Table([[pre]], colWidths=[AVAIL], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
            ("BOX", (0, 0), (-1, -1), 0.4, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        out = [box]
        if b.get("caption"):
            out.append(Paragraph(fmt(b["caption"]), S["caption"]))
        else:
            out.append(Spacer(1, 6))
        return out
    if kind == "note":
        p = Paragraph("<b>Nota.</b> " + fmt(b.get("text", "")), S["note"])
        box = Table([[p]], colWidths=[AVAIL], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NOTE_BG),
            ("LINEBEFORE", (0, 0), (0, -1), 2.2, ACCENT),
            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        return [box, Spacer(1, 7)]
    if kind == "table":
        headers = [str(h) for h in b.get("headers", [])]
        rows = [[str(c) for c in r] for r in b.get("rows", [])]
        if not headers and not rows:
            return []
        n = max([len(headers)] + [len(r) for r in rows])
        headers = (headers + [""] * n)[:n]
        rows = [(r + [""] * n)[:n] for r in rows]
        # Preferred width tracks average content length; the floor is the
        # column's longest unbreakable word, so a header like "Correcciones"
        # can never be split mid-word.
        pref, mins = [], []
        for i in range(n):
            cells = [headers[i]] + [r[i] for r in rows]
            pref.append(min(max(sum(len(c) for c in cells) / max(len(cells), 1), 6), 60))
            widest_word = 0.0
            for c in cells:
                for w in re.split(r"\s+", c):
                    w = w.strip("`")
                    if w:
                        widest_word = max(widest_word, stringWidth(w, "Helvetica-Bold", 8.4))
            mins.append(min(widest_word + 12, AVAIL * 0.45))
        total = sum(pref) or 1
        widths = [max(AVAIL * p / total, m) for p, m in zip(pref, mins)]
        # Reclaim any overflow from the columns that sit above their floor.
        over = sum(widths) - AVAIL
        if over > 0:
            slack = sum(w - m for w, m in zip(widths, mins) if w > m)
            if slack > 0:
                take = min(over, slack)
                widths = [w - (w - m) / slack * take if w > m else w
                          for w, m in zip(widths, mins)]
            scale = AVAIL / sum(widths)
            widths = [w * scale for w in widths]
        data = [[Paragraph(fmt(h), S["cell_head"]) for h in headers]]
        data += [[Paragraph(fmt(c), S["cell"]) for c in r] for r in rows]
        t = Table(data, colWidths=widths, repeatRows=1, style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
            ("GRID", (0, 0), (-1, -1), 0.4, RULE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]))
        out = [t]
        if b.get("caption"):
            out.append(Paragraph(fmt(b["caption"]), S["caption"]))
        else:
            out.append(Spacer(1, 8))
        return out
    return []


def chapter_flowables(num, ch):
    key = "ch%d" % num
    out = [heading("%d. %s" % (num, ch["title"]), "h1", 0, key)]
    if ch.get("summary"):
        out.append(Paragraph(fmt(ch["summary"]), S["lead"]))
    for j, sec in enumerate(ch.get("sections", []), 1):
        skey = "%s_s%d" % (key, j)
        title = "%d.%d %s" % (num, j, sec.get("heading", ""))
        blocks = sec.get("blocks", [])
        head = heading(title, "h2", 1, skey)
        if blocks:
            # Keep a heading with its first block so no heading strands at a page foot.
            first = block_flowables(blocks[0])
            out.append(KeepTogether([head] + first[:1]))
            out.extend(first[1:])
            for b in blocks[1:]:
                out.extend(block_flowables(b))
        else:
            out.append(head)
    files = ch.get("files") or []
    if files:
        out.append(Paragraph("<b>Archivos de referencia:</b> " + ", ".join(
            '<font face="Courier" size="7.8">%s</font>' % escape(f) for f in files), S["small"]))
    out.append(PageBreak())
    return out


def on_body_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8.2)
    canvas.setFillColor(MUTED)
    canvas.drawString(M, H - M + 14, doc.header_left)
    canvas.drawRightString(W - M, H - M + 14, doc.header_right)
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(M, H - M + 8, W - M, H - M + 8)
    canvas.drawCentredString(W / 2, M - 22, "Página %d" % doc.page)
    canvas.restoreState()


def build(front, chapters, out_path):
    doc = Doc(out_path, pagesize=A4, leftMargin=M, rightMargin=M, topMargin=M + 6, bottomMargin=M + 4,
              title="%s — %s" % (front["title"], front["subtitle"]), author=front.get("author", ""),
              subject=front.get("subject", ""))
    doc.header_left = "%s · %s" % (front["title"], front["subtitle"])
    doc.header_right = front.get("version", "")
    frame = Frame(M, M, W - 2 * M, H - 2 * M, id="f")
    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[frame]),
        PageTemplate(id="Body", frames=[frame], onPage=on_body_page),
    ])

    story = []
    story += [Spacer(1, 5.5 * cm),
              Paragraph(escape(front["title"]), S["cover_title"]),
              Paragraph(escape(front["subtitle"]), S["cover_sub"])]
    for line in front.get("cover_lines", []):
        story.append(Paragraph(fmt(line), S["cover_meta"]))
    story += [NextPageTemplate("Body"), PageBreak()]

    story.append(heading("Sobre este documento", "h1", 0, "about"))
    for sec in front.get("about", []):
        if sec.get("heading"):
            story.append(Paragraph(escape(sec["heading"]), S["h2"]))
        for b in sec.get("blocks", []):
            story += block_flowables(b)
    story.append(PageBreak())

    toc = TableOfContents()
    toc.levelStyles = [S["toc1"], S["toc2"]]
    toc.dotsMinLevel = 0
    story += [Paragraph("Índice", S["h1"]), toc, PageBreak()]

    for i, ch in enumerate(chapters, 1):
        story += chapter_flowables(i, ch)

    for letter, ap in zip("ABCDEFG", front.get("appendices", [])):
        key = "ap%s" % letter
        story.append(heading("Anexo %s. %s" % (letter, ap["title"]), "h1", 0, key))
        for sec in ap.get("sections", []):
            if sec.get("heading"):
                story.append(Paragraph(escape(sec["heading"]), S["h2"]))
            for b in sec.get("blocks", []):
                story += block_flowables(b)
        story.append(PageBreak())

    doc.multiBuild(story)


if __name__ == "__main__":
    front_path, chapters_path, out_path = sys.argv[1:4]
    with open(front_path, encoding="utf-8") as f:
        front = json.load(f)
    with open(chapters_path, encoding="utf-8") as f:
        payload = json.load(f)
    entries = list(payload.get("chapters", [])) + list(payload.get("extra", []))
    chapters = [e["chapter"] for e in entries if e and e.get("chapter")]
    build(front, chapters, out_path)
    print("wrote", out_path, "with", len(chapters), "chapters")
