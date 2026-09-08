#!/usr/bin/env python
"""Render the short, plain-language summary of MVP-CRM to PDF.

Reads resumen.json (one file: cover, sections, diagrams and which screenshots
to show) and reuses the styles and block renderers from render.py, so both
documents look alike. No table of contents, no numbered chapters: the point
is to fit in about ten pages.

    uv run --with reportlab python resumen.py ../MVP-CRM-resumen.pdf
"""
import json
import re
import sys
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import cm
from reportlab.platypus import (Frame, KeepTogether, NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, Spacer)

import render
from render import (AVAIL, BOX_BG, M, S, Doc, _arrow, _box, _wrap, block_flowables, fmt,
                    heading, on_body_page)

BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def fmt_simple(text):
    """fmt() plus **bold** and *italic*, which the summary uses freely."""
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


def blocks(section_blocks):
    out = []
    for b in section_blocks:
        if b.get("kind") == "diagram" and b.get("layout") == "flow":
            d = flow_drawing(b)
            d.hAlign = "CENTER"
            parts = [Spacer(1, 4), d, Paragraph(fmt(b.get("caption", "")), S["caption"])]
            out.append(KeepTogether(parts))
        elif b.get("kind") in ("paragraph", "bullets"):
            # Route through fmt_simple so **bold** works in the summary.
            if b["kind"] == "paragraph":
                out.append(Paragraph(fmt_simple(b.get("text", "")), S["body"]))
            else:
                out += [Paragraph(fmt_simple(i), S["bullet"], bulletText="•") for i in b.get("items", [])]
        else:
            out += block_flowables(b)
    return out


def build(front, out_path):
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

    story = [Spacer(1, 1.2 * cm), Paragraph(fmt(front["objective"]), S["cover_obj"]),
             Spacer(1, 3.2 * cm),
             Paragraph(escape(front["title"]), S["cover_title"]),
             Paragraph(escape(front["subtitle"]), S["cover_sub"]),
             Paragraph("Autor:", S["cover_label"]),
             Paragraph(escape(front["author"]), S["cover_name"]),
             Paragraph(escape(front["date"]), S["cover_date"]),
             Spacer(1, 2.6 * cm)]
    for line in front.get("cover_lines", []):
        story.append(Paragraph(fmt(line), S["cover_meta"]))
    story += [NextPageTemplate("Body"), PageBreak()]

    for i, sec in enumerate(front["sections"], 1):
        head = heading(sec["heading"], "h1", 0, "s%d" % i)
        body = blocks(sec.get("blocks", []))
        story.append(KeepTogether([head] + body[:1]))
        story += body[1:]
        story.append(Spacer(1, 10))
    doc.multiBuild(story)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "MVP-CRM-resumen.pdf"
    with open("resumen.json", encoding="utf-8") as f:
        front = json.load(f)
    build(front, out)
    print("wrote", out)
