#!/usr/bin/env python
"""Render the short, plain-language documentation of Vendi to PDF.

Reads documentacion.json (cover, sections, diagrams, the Fase 1 checklist and
which screenshots to show) plus capturas/recuadros.json (where each red box
goes, written by capturar.py), and reuses the styles and block renderers from
render.py so every document looks alike.

    uv run --with reportlab python documentacion.py ../Vendi-documentacion.pdf
"""
import json
import os
import re
import sys
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (Flowable, Frame, KeepTogether, NextPageTemplate, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

import render
from render import (AVAIL, BASE, BOX_BG, INK, M, RULE, S, Doc, _arrow, _box, _wrap,
                    block_flowables, fmt, heading, on_body_page)

BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
RED = colors.HexColor("#e02424")
GREEN = colors.HexColor("#16a34a")

S_CHECK = ParagraphStyle("check", parent=S["body"], spaceAfter=0, leading=13.5)
S_NOTE = ParagraphStyle("shot_note", parent=S["body"], fontSize=9.2, leading=12.4, spaceAfter=0)
S_NUM = ParagraphStyle("shot_num", parent=S_NOTE, fontName="Helvetica-Bold", textColor=colors.white,
                       alignment=1, fontSize=8.6, leading=11)
S_SHOT_TITLE = ParagraphStyle("shot_title", parent=S["h2"], spaceBefore=10, spaceAfter=6)


def fmt_simple(text):
    """fmt() plus **bold** and *italic*, which this document uses freely."""
    text = fmt(text, italics=False)
    text = BOLD_RE.sub(r"<b>\1</b>", text)
    return render.ITALIC_RE.sub(r"<i>\1</i>", text)


def flow_drawing(b):
    """Boxes left to right joined by arrows: the path of one message."""
    steps = b["steps"]
    n = len(steps)
    gap = 14
    w = (AVAIL - gap * (n - 1)) / n
    font, size = "Helvetica", 7.6
    wrapped = [_wrap(s, font, size, w - 12) for s in steps]
    h = max(len(l) for l in wrapped) * size * 1.3 + 14
    d = Drawing(AVAIL, h)
    for i, lines in enumerate(wrapped):
        x = i * (w + gap)
        _box(d, x, 0, w, h, lines, font, size, BOX_BG)
        if i:
            _arrow(d, x - gap + 1, h / 2, x - 1, h / 2)
    return d


class AnnotatedImage(Flowable):
    """A screenshot with numbered red rectangles drawn on top."""

    def __init__(self, path, boxes, width):
        super().__init__()
        from reportlab.lib.utils import ImageReader
        self.path = path
        self.boxes = boxes  # [(number, x, y, w, h)] as fractions of the image
        iw, ih = ImageReader(path).getSize()
        self.w = width
        self.h = width * ih / iw

    def wrap(self, *_):
        return self.w, self.h

    def draw(self):
        c = self.canv
        c.drawImage(self.path, 0, 0, self.w, self.h)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.5)
        c.rect(0, 0, self.w, self.h)
        c.setStrokeColor(RED)
        c.setLineWidth(1.6)
        for num, x, y, w, h in self.boxes:
            rx, ry = x * self.w, self.h - (y + h) * self.h
            c.rect(rx, ry, w * self.w, h * self.h, stroke=1, fill=0)
        for num, x, y, w, h in self.boxes:
            # Badge on the box's top-left corner, nudged inside the image.
            bx = min(max(x * self.w, 7), self.w - 7)
            by = min(max(self.h - y * self.h, 7), self.h - 7)
            c.setFillColor(RED)
            c.circle(bx, by, 6.6, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 8)
            c.drawCentredString(bx, by - 2.8, str(num))


def shot_flowables(b, recuadros):
    screen = recuadros.get(b["screen"])
    if not screen:
        raise SystemExit("Falta la captura %r en capturas/recuadros.json" % b["screen"])
    by_id = {bx["id"]: bx for bx in screen["boxes"]}
    numbered, legend = [], []
    for key, text in b.get("notes", {}).items():
        box = by_id.get(key)
        if not box:
            print("  aviso: sin recuadro para %s/%s" % (b["screen"], key))
            continue
        n = len(numbered) + 1
        numbered.append((n, box["x"], box["y"], box["w"], box["h"]))
        legend.append([Paragraph(str(n), S_NUM), Paragraph(fmt_simple(text), S_NOTE)])
    img = AnnotatedImage(os.path.join(BASE, screen["file"]), numbered, AVAIL)
    out = []
    if b.get("title"):
        out.append(Paragraph(escape(b["title"]), S_SHOT_TITLE))
    out += [img, Spacer(1, 6)]
    if legend:
        t = Table(legend, colWidths=[16, AVAIL - 16], style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (0, -1), RED),
            ("LEFTPADDING", (0, 0), (0, -1), 0), ("RIGHTPADDING", (0, 0), (0, -1), 0),
            ("LEFTPADDING", (1, 0), (1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LINEBELOW", (1, 0), (1, -1), 0.3, RULE),
        ]))
        out.append(t)
    return [KeepTogether(out), Spacer(1, 12)]


class CheckMark(Flowable):
    """A green tick drawn as a path, so it renders in every PDF viewer."""

    size = 11

    def wrap(self, *_):
        return self.size, self.size

    def draw(self):
        c = self.canv
        s = self.size
        c.setStrokeColor(GREEN)
        c.setLineWidth(2.2)
        c.setLineCap(1)
        c.setLineJoin(1)
        path = c.beginPath()
        path.moveTo(s * 0.08, s * 0.52)
        path.lineTo(s * 0.38, s * 0.18)
        path.lineTo(s * 0.95, s * 0.88)
        c.drawPath(path, stroke=1, fill=0)


def checklist_flowables(b):
    rows = []
    for i, item in enumerate(b["items"], 1):
        rows.append([CheckMark(), Paragraph("<b>%d.</b> %s" % (i, fmt_simple(item)), S_CHECK)])
    t = Table(rows, colWidths=[22, AVAIL - 22], style=TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (0, -1), 4),
    ]))
    return [t, Spacer(1, 8)]


def blocks(section_blocks, recuadros):
    out = []
    for b in section_blocks:
        kind = b.get("kind")
        if kind == "diagram" and b.get("layout") == "flow":
            d = flow_drawing(b)
            d.hAlign = "CENTER"
            out.append(KeepTogether([Spacer(1, 4), d, Paragraph(fmt(b.get("caption", "")), S["caption"])]))
        elif kind == "paragraph":
            out.append(Paragraph(fmt_simple(b.get("text", "")), S["body"]))
        elif kind == "bullets":
            out += [Paragraph(fmt_simple(i), S["bullet"], bulletText="•") for i in b.get("items", [])]
        elif kind == "subheading":
            out.append(Paragraph(escape(b["text"]), S_SHOT_TITLE))
        elif kind == "checklist":
            out += checklist_flowables(b)
        elif kind == "shot":
            out += shot_flowables(b, recuadros)
        else:
            out += block_flowables(b)
    return out


def build(front, recuadros, out_path):
    doc = Doc(out_path, pagesize=render.A4, leftMargin=M, rightMargin=M, topMargin=M + 6, bottomMargin=M + 4,
              title="%s — %s" % (front["title"], front["subtitle"]), author=front.get("author", ""),
              subject=front.get("subject", ""))
    doc.header_left = "%s · %s" % (front["title"], front["subtitle"])
    doc.header_right = front.get("version", "")
    frame = Frame(M, M, render.W - 2 * M, render.H - 2 * M, id="f")
    doc.addPageTemplates([
        PageTemplate(id="Cover", frames=[frame]),
        PageTemplate(id="Body", frames=[frame], onPage=on_body_page),
    ])

    story = [Spacer(1, 7 * cm)]
    if front.get("objective"):
        story.append(Paragraph(fmt(front["objective"]), S["cover_obj"]))
    story += [Paragraph(escape(front["title"]), S["cover_title"]),
              Paragraph(escape(front["subtitle"]), S["cover_sub"])]
    if front.get("author"):
        story += [Paragraph("Autor:", S["cover_label"]), Paragraph(escape(front["author"]), S["cover_name"])]
    if front.get("date"):
        story.append(Paragraph(escape(front["date"]), S["cover_date"]))
    story.append(Spacer(1, 2.6 * cm))
    for line in front.get("cover_lines", []):
        story.append(Paragraph(fmt(line), S["cover_meta"]))
    story += [NextPageTemplate("Body"), PageBreak()]

    for i, sec in enumerate(front["sections"], 1):
        head = heading(sec["heading"], "h1", 0, "s%d" % i)
        body = blocks(sec.get("blocks", []), recuadros)
        if sec["heading"] == "Pantallas":
            story.append(PageBreak())
        story.append(KeepTogether([head] + body[:1]))
        story += body[1:]
        story.append(Spacer(1, 10))
    doc.multiBuild(story)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "Vendi-documentacion.pdf"
    with open(os.path.join(BASE, "documentacion.json"), encoding="utf-8") as f:
        front = json.load(f)
    rec_path = os.path.join(BASE, "capturas", "recuadros.json")
    recuadros = {}
    if os.path.exists(rec_path):
        with open(rec_path, encoding="utf-8") as f:
            recuadros = json.load(f)
    build(front, recuadros, out)
    print("wrote", out)
