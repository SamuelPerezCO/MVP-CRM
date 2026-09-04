"""A minimal .xlsx writer: one sheet, inline strings, no dependencies.

The CRM's exports (see ``core.views.clientes_export``) want a real Excel file
-- the one every ops person double-clicks -- not a CSV that opens with the
wrong encoding and mangles phone numbers. ``openpyxl`` would do it, but it is
a heavy dependency for writing a flat table, and this project deliberately
keeps ``requirements.txt`` short.

An .xlsx is a zip of XML parts; a workbook with one sheet of inline strings
needs five of them and nothing else. That is what :func:`build` produces.
Every cell is written as text (``t="inlineStr"``), which is also the right
thing for the data at hand: a phone number stored as a number would lose its
leading ``+`` and get shown as ``5.73E+11``.

Anything beyond a single flat table (formulas, styles, several sheets) is
out of scope on purpose -- reach for openpyxl at that point.
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

#: Widest column Excel will show without the user dragging; keeps "Mail"
#: readable while not stretching "País" to the same width.
_MIN_WIDTH = 8
_MAX_WIDTH = 48

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>
"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""

_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="{sheet_name}" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""

#: Two cell styles: 0 = default, 1 = bold (the header row).
_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2">
    <font><sz val="11"/><name val="Calibri"/></font>
    <font><b/><sz val="11"/><name val="Calibri"/></font>
  </fonts>
  <fills count="2">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
  </fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
  </cellXfs>
</styleSheet>
"""


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA -- Excel's column naming."""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _clean(value) -> str:
    """A cell's text: None becomes blank, and the control characters XML
    forbids (a stray tab or NUL in a pasted name) are dropped rather than
    producing a file Excel refuses to open."""
    if value is None:
        return ""
    text = str(value)
    return "".join(
        char for char in text if char in "\n\t" or ord(char) >= 32
    )


def _cell(ref: str, value: str, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    if value == "":
        return f'<c r="{ref}"{style_attr}/>'
    # xml:space="preserve" keeps leading/trailing spaces and newlines.
    return (
        f'<c r="{ref}" t="inlineStr"{style_attr}>'
        f'<is><t xml:space="preserve">{escape(value)}</t></is></c>'
    )


def _sheet_xml(headers: list[str], rows: list[list]) -> str:
    widths = [len(header) for header in headers]
    body_rows = []
    for row_index, row in enumerate(rows, start=2):
        cells = []
        for col_index, raw in enumerate(row):
            value = _clean(raw)
            if col_index < len(widths):
                widths[col_index] = max(widths[col_index], len(value))
            cells.append(_cell(f"{column_letter(col_index)}{row_index}", value))
        body_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    header_cells = "".join(
        _cell(f"{column_letter(index)}1", _clean(header), style=1)
        for index, header in enumerate(headers)
    )
    cols = "".join(
        f'<col min="{index + 1}" max="{index + 1}" '
        f'width="{min(max(width + 2, _MIN_WIDTH), _MAX_WIDTH)}" customWidth="1"/>'
        for index, width in enumerate(widths)
    )
    last_col = column_letter(max(len(headers) - 1, 0))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        # Freeze the header row so it stays put while scrolling.
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"<cols>{cols}</cols>"
        f'<sheetData><row r="1">{header_cells}</row>{"".join(body_rows)}</sheetData>'
        # Filter arrows on the header row -- what people reach for first.
        f'<autoFilter ref="A1:{last_col}{len(rows) + 1}"/>'
        "</worksheet>"
    )


def build(headers: list[str], rows: list[list], sheet_name: str = "Hoja1") -> bytes:
    """Return the bytes of an .xlsx holding one sheet: ``headers`` in bold on
    row 1, then one row per entry of ``rows``. Every value is written as
    text (see the module docstring for why)."""
    # Excel caps sheet names at 31 chars and rejects these characters.
    safe_name = "".join(c for c in sheet_name if c not in '[]:*?/\\')[:31] or "Hoja1"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        archive.writestr("xl/workbook.xml", _WORKBOOK.format(sheet_name=escape(safe_name)))
        archive.writestr("xl/styles.xml", _STYLES)
        archive.writestr("xl/worksheets/sheet1.xml", _sheet_xml(headers, rows))
    return buffer.getvalue()


#: What the browser needs to treat the download as Excel.
CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
