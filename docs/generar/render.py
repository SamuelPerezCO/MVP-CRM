#!/usr/bin/env python
"""Render the MVP-CRM documentation to PDF.

Inputs: front.json (cover, intro, the Part I project document under
``academic``, appendices) and chapters.json (verified chapters in the CHAPTER
schema). Output: a PDF with cover, "Sobre este documento", table of contents,
Part I (unnumbered project sections: problem, objectives, requirements, data
model, interface...), Part II (numbered technical chapters) and appendices.
Base-14 fonts only (Helvetica/Courier): they cover Latin-1, so Spanish
accents render without embedding a font.

Block kinds: paragraph, bullets, code, note, table, image (a PNG next to
this script) and diagram (drawn here from a small JSON description, see
``layers_drawing`` and ``er_drawing``).
"""
import json
import os
import re
import sys
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate,
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
BOX_BG = colors.HexColor("#f6f8fb")
ROOT_BG = colors.HexColor("#dfe9fb")

W, H = A4
M = 2 * cm
AVAIL = W - 2 * M

#: Directory the JSON inputs live in; image paths are relative to it.
BASE = os.path.dirname(os.path.abspath(__file__))

S = {
    "cover_obj": ParagraphStyle("cover_obj", fontName="Helvetica", fontSize=11, leading=16, textColor=INK, alignment=4, spaceAfter=6),
    "cover_title": ParagraphStyle("cover_title", fontName="Helvetica-Bold", fontSize=34, leading=40, textColor=ACCENT, alignment=1, spaceAfter=6),
    "cover_sub": ParagraphStyle("cover_sub", fontName="Helvetica", fontSize=17, leading=22, textColor=INK, alignment=1, spaceAfter=6),
    "cover_label": ParagraphStyle("cover_label", fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=INK, alignment=1, spaceBefore=26),
    "cover_name": ParagraphStyle("cover_name", fontName="Helvetica", fontSize=13, leading=18, textColor=INK, alignment=1),
    "cover_date": ParagraphStyle("cover_date", fontName="Helvetica", fontSize=12, leading=16, textColor=INK, alignment=1, spaceBefore=26),
    "cover_meta": ParagraphStyle("cover_meta", fontName="Helvetica", fontSize=9.5, leading=14, textColor=MUTED, alignment=1),
    "part": ParagraphStyle("part", fontName="Helvetica-Bold", fontSize=30, leading=36, textColor=ACCENT, spaceAfter=14),
    "part_sub": ParagraphStyle("part_sub", fontName="Helvetica", fontSize=12, leading=17, textColor=INK, spaceAfter=10),
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
    "toc0": ParagraphStyle("toc0", fontName="Helvetica-Bold", fontSize=11.5, leading=17, textColor=ACCENT, spaceBefore=10),
    "toc1": ParagraphStyle("toc1", fontName="Helvetica-Bold", fontSize=10.2, leading=14.5, textColor=INK, spaceBefore=3, leftIndent=10),
    "toc2": ParagraphStyle("toc2", fontName="Helvetica", fontSize=9.2, leading=12.5, textColor=INK, leftIndent=26),
}

CODE_RE = re.compile(r"`([^`]+)`")
ITALIC_RE = re.compile(r"\*([^*\n]+)\*")


def fmt(text, italics=False):
    """Escape for Paragraph markup, then turn `spans` into Courier.

    ``italics`` also turns *spans* into italics; only Part I asks for it (the
    bibliography), because the technical chapters use ``*`` literally.
    """
    text = escape(str(text))
    text = CODE_RE.sub(lambda m: '<font face="Courier" size="8.8">%s</font>' % m.group(1), text)
    if italics:
        text = ITALIC_RE.sub(r"<i>\1</i>", text)
    return text


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


# --- Diagrams ---------------------------------------------------------------

def _wrap(text, font, size, width):
    return simpleSplit(text, font, size, width)


def _box(d, x, y, w, h, lines, font, size, fill, title=None):
    """A rounded box with centred text lines; ``y`` is the box bottom."""
    d.add(Rect(x, y, w, h, rx=4, ry=4, fillColor=fill, strokeColor=ACCENT, strokeWidth=0.8))
    leading = size * 1.3
    total = len(lines) * leading + (leading if title else 0)
    ty = y + h / 2 + total / 2 - size
    if title:
        d.add(String(x + w / 2, ty, title, fontName="Helvetica-Bold", fontSize=size + 1, fillColor=INK, textAnchor="middle"))
        ty -= leading
    for line in lines:
        d.add(String(x + w / 2, ty, line, fontName=font, fontSize=size, fillColor=INK, textAnchor="middle"))
        ty -= leading


def _arrow(d, x1, y1, x2, y2, head=True):
    d.add(Line(x1, y1, x2, y2, strokeColor=MUTED, strokeWidth=0.8))
    if head:
        # Small triangle at (x2, y2) pointing along the line direction.
        dx, dy = x2 - x1, y2 - y1
        n = (dx * dx + dy * dy) ** 0.5 or 1
        ux, uy = dx / n, dy / n
        px, py = -uy, ux
        s = 4.5
        d.add(Polygon([x2, y2, x2 - ux * s * 2 + px * s, y2 - uy * s * 2 + py * s,
                       x2 - ux * s * 2 - px * s, y2 - uy * s * 2 - py * s],
                      fillColor=MUTED, strokeColor=MUTED, strokeWidth=0.5))


def layers_drawing(b):
    """Rows of boxes with edges to the next row (``to`` indices).

    ``arrow`` = "down" (default) puts the arrowhead on the child, "up" on the
    parent, which is how a problem tree reads: causes push up into the
    problem, the problem pushes up into its effects.
    """
    rows = b["rows"]
    arrow_up = b.get("arrow") == "up"
    gap, vgap = 10, 38
    font, size = "Helvetica", 7.8
    layout = []  # per row: list of (x, w, h, lines, role)
    for row in rows:
        n = len(row)
        w = (AVAIL - gap * (n - 1)) / n
        if n == 1:
            w = min(w, 11.5 * cm)
        boxes = []
        for node in row:
            role = node.get("role", "")
            fs = size + 1 if role == "root" else size
            fn = "Helvetica-Bold" if role == "root" else font
            lines = _wrap(node["text"], fn, fs, w - 14)
            # A word wider than the box would spill past its border: shrink
            # the font (to a legible floor) until the widest word fits.
            widest = max(stringWidth(word, fn, fs) for word in node["text"].split())
            if widest > w - 14:
                fs = max(6.2, fs * (w - 14) / widest)
                lines = _wrap(node["text"], fn, fs, w - 14)
            h = len(lines) * fs * 1.3 + 14
            boxes.append([0, w, h, lines, role, fn, fs])
        total_w = sum(bx[1] for bx in boxes) + gap * (n - 1)
        x = (AVAIL - total_w) / 2
        for bx in boxes:
            bx[0] = x
            x += bx[1] + gap
        layout.append(boxes)
    row_h = [max(bx[2] for bx in boxes) for boxes in layout]
    total_h = sum(row_h) + vgap * (len(rows) - 1)
    d = Drawing(AVAIL, total_h)
    tops = []
    y_top = total_h
    for rh in row_h:
        tops.append(y_top)
        y_top -= rh + vgap
    # Boxes, vertically centred in their row.
    centers = []
    for r, boxes in enumerate(layout):
        cs = []
        for x, w, h, lines, role, fn, fs in boxes:
            y = tops[r] - row_h[r] / 2 - h / 2
            _box(d, x, y, w, h, lines, fn, fs, ROOT_BG if role == "root" else BOX_BG)
            cs.append((x + w / 2, y + h, y))  # centre x, top y, bottom y
        centers.append(cs)
    for r, row in enumerate(rows):
        if r + 1 >= len(rows):
            break
        for i, node in enumerate(row):
            for j in node.get("to", []):
                px, _, pb = centers[r][i]
                cx, ct, _ = centers[r + 1][j]
                if arrow_up:
                    _arrow(d, cx, ct, px, pb)
                else:
                    _arrow(d, px, pb, cx, ct)
    return d


def _clip(cx, cy, w, h, tx, ty):
    """Point where the segment centre->(tx,ty) leaves the box centred at (cx,cy)."""
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    sx = (w / 2) / abs(dx) if dx else float("inf")
    sy = (h / 2) / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def er_drawing(b):
    """Entity boxes on a (col,row) grid with labelled edges between them."""
    nodes = b["nodes"]
    cols = max(n["col"] for n in nodes) + 1
    rows = max(n["row"] for n in nodes) + 1
    gap, vgap = 34, 34
    w = (AVAIL - gap * (cols - 1)) / cols
    fsize, lead = 7.4, 9.6
    heights = {}
    for n in nodes:
        n["_lines"] = []
        for f in n["fields"]:
            n["_lines"] += _wrap(f, "Helvetica", fsize, w - 12)
        heights[n["id"]] = 16 + len(n["_lines"]) * lead + 8
    row_h = [max([heights[n["id"]] for n in nodes if n["row"] == r] or [0]) for r in range(rows)]
    total_h = sum(row_h) + vgap * (rows - 1)
    d = Drawing(AVAIL, total_h)
    pos = {}
    for n in nodes:
        x = n["col"] * (w + gap)
        y_top = total_h - sum(row_h[:n["row"]]) - vgap * n["row"]
        h = heights[n["id"]]
        y = y_top - row_h[n["row"]] / 2 - h / 2
        pos[n["id"]] = (x, y, w, h)
        d.add(Rect(x, y, w, h, rx=3, ry=3, fillColor=colors.white, strokeColor=ACCENT, strokeWidth=0.9))
        d.add(Rect(x, y + h - 16, w, 16, rx=3, ry=3, fillColor=HEAD_BG, strokeColor=ACCENT, strokeWidth=0.9))
        d.add(String(x + w / 2, y + h - 11.5, n["title"], fontName="Helvetica-Bold", fontSize=8.6, fillColor=INK, textAnchor="middle"))
        ty = y + h - 16 - lead + 1
        for line in n["_lines"]:
            d.add(String(x + 6, ty, line, fontName="Helvetica", fontSize=fsize, fillColor=INK))
            ty -= lead
    for e in b.get("edges", []):
        ax, ay, aw, ah = pos[e["from"]]
        bx, by, bw, bh = pos[e["to"]]
        acx, acy = ax + aw / 2, ay + ah / 2
        bcx, bcy = bx + bw / 2, by + bh / 2
        x1, y1 = _clip(acx, acy, aw, ah, bcx, bcy)
        x2, y2 = _clip(bcx, bcy, bw, bh, acx, acy)
        _arrow(d, x1, y1, x2, y2)
        label = e.get("label")
        if label:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            lw = stringWidth(label, "Helvetica", 7) + 6
            d.add(Rect(mx - lw / 2, my - 5, lw, 10, fillColor=colors.white, strokeColor=None))
            d.add(String(mx, my - 2.5, label, fontName="Helvetica", fontSize=7, fillColor=MUTED, textAnchor="middle"))
    return d


# --- Blocks -----------------------------------------------------------------

def block_flowables(b, italics=False):
    kind = b.get("kind")
    if kind == "paragraph":
        return [Paragraph(fmt(b.get("text", ""), italics), S["body"])]
    if kind == "bullets":
        return [Paragraph(fmt(i, italics), S["bullet"], bulletText="•") for i in b.get("items", [])]
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
    if kind == "image":
        path = os.path.join(BASE, b["path"])
        iw, ih = ImageReader(path).getSize()
        width = min(AVAIL, b.get("width_cm", 17) * cm)
        height = width * ih / iw
        # Never taller than the page body: a 1440x900 capture is fine, a
        # portrait one gets scaled down instead of overflowing the frame.
        max_h = H - 2 * M - 4 * cm
        if height > max_h:
            width, height = width * max_h / height, max_h
        img = Image(path, width=width, height=height)
        framed = Table([[img]], colWidths=[width], style=TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]), hAlign="CENTER")
        parts = [Spacer(1, 4), framed]
        if b.get("caption"):
            parts.append(Paragraph(fmt(b["caption"]), S["caption"]))
        else:
            parts.append(Spacer(1, 8))
        return [KeepTogether(parts)]
    if kind == "diagram":
        d = layers_drawing(b) if b.get("layout") == "layers" else er_drawing(b)
        d.hAlign = "CENTER"
        parts = [Spacer(1, 6), d]
        if b.get("caption"):
            parts.append(Paragraph(fmt(b["caption"]), S["caption"]))
        else:
            parts.append(Spacer(1, 8))
        return [KeepTogether(parts)]
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


def section_flowables(key, title, blocks, italics=False):
    """An unnumbered Part I section: heading kept with its first block."""
    head = heading(title, "h1", 1, key)
    if not blocks:
        return [head]
    first = block_flowables(blocks[0], italics)
    rest = blocks[1:]
    # A one-paragraph intro followed by a diagram reads as one unit: keep the
    # heading, the intro and the drawing on the same page.
    if rest and rest[0].get("kind") == "diagram" and blocks[0].get("kind") == "paragraph":
        first += block_flowables(rest[0], italics)
        rest = rest[1:]
    out = [KeepTogether([head] + first)]
    for b in rest:
        out.extend(block_flowables(b, italics))
    return out


def chapter_flowables(num, ch):
    key = "ch%d" % num
    out = [heading("%d. %s" % (num, ch["title"]), "h1", 1, key)]
    if ch.get("summary"):
        out.append(Paragraph(fmt(ch["summary"]), S["lead"]))
    for j, sec in enumerate(ch.get("sections", []), 1):
        skey = "%s_s%d" % (key, j)
        title = "%d.%d %s" % (num, j, sec.get("heading", ""))
        blocks = sec.get("blocks", [])
        head = heading(title, "h2", 2, skey)
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


def part_flowables(label, title, intro, key):
    """A divider page opening a part; a level-0 entry in the index."""
    p = Paragraph(escape(label), S["part_sub"])
    p._toc = (0, "%s. %s" % (label, title), key)
    out = [Spacer(1, 6 * cm), p, Paragraph(escape(title), S["part"])]
    if intro:
        out.append(Paragraph(fmt(intro), S["body"]))
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

    # Cover, laid out like a project document: the general objective on top,
    # then the project name, the author and the date.
    story = [Spacer(1, 1.2 * cm)]
    if front.get("objective"):
        story.append(Paragraph(fmt(front["objective"]), S["cover_obj"]))
    story += [Spacer(1, 3.2 * cm),
              Paragraph(escape(front["title"]), S["cover_title"]),
              Paragraph(escape(front["subtitle"]), S["cover_sub"])]
    if front.get("author"):
        story += [Paragraph("Autor:", S["cover_label"]),
                  Paragraph(escape(front["author"]), S["cover_name"])]
    if front.get("date"):
        story.append(Paragraph(escape(front["date"]), S["cover_date"]))
    story.append(Spacer(1, 2.6 * cm))
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
    toc.levelStyles = [S["toc0"], S["toc1"], S["toc2"]]
    toc.dotsMinLevel = 0
    story += [Paragraph("Índice", S["h1"]), toc, PageBreak()]

    academic = front.get("academic")
    if academic:
        story += part_flowables("Parte I", academic.get("part_title", "Documento del proyecto"),
                                academic.get("part_intro", ""), "part1")
        for i, sec in enumerate(academic.get("sections", []), 1):
            # Part I flows continuously, like the project document it mirrors;
            # only the screenshots section starts on a fresh page.
            if sec["heading"] == "Interfaz":
                story.append(PageBreak())
            story += section_flowables("p1_s%d" % i, sec["heading"], sec.get("blocks", []), italics=True)
            story.append(Spacer(1, 14))
        story.append(PageBreak())
        story += part_flowables("Parte II", academic.get("part2_title", "Documentación técnica del código"),
                                academic.get("part2_intro", ""), "part2")

    for i, ch in enumerate(chapters, 1):
        story += chapter_flowables(i, ch)

    for letter, ap in zip("ABCDEFG", front.get("appendices", [])):
        key = "ap%s" % letter
        story.append(heading("Anexo %s. %s" % (letter, ap["title"]), "h1", 1, key))
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
