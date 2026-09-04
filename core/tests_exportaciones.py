"""Tests for CRM > Exportaciones: the page, the .xlsx download behind it and
the minimal writer in core.xlsx."""

import io
import zipfile
from xml.etree import ElementTree

from django.test import TestCase
from django.urls import reverse

from core import xlsx
from core.models import Client
from core.views import CLIENT_EXPORT_COLUMNS
from messaging.models import Conversation, Tag
from messaging import services as messaging_services

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def sheet_rows(data: bytes) -> list[list[str]]:
    """The cell texts of sheet1, row by row -- what Excel would show."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in root.findall(".//m:sheetData/m:row", NS):
        cells = []
        for cell in row.findall("m:c", NS):
            text = cell.find("m:is/m:t", NS)
            cells.append(text.text if text is not None else "")
        rows.append(cells)
    return rows


class XlsxWriterTests(TestCase):
    def test_produces_a_zip_with_the_parts_excel_needs(self):
        data = xlsx.build(["A", "B"], [["1", "2"]])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
        for part in (
            "[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/worksheets/sheet1.xml",
        ):
            with self.subTest(part):
                self.assertIn(part, names)

    def test_headers_then_rows_as_text(self):
        rows = sheet_rows(xlsx.build(["Nombre", "Teléfono"], [["Ana", "+573167687288"]]))
        self.assertEqual(rows, [["Nombre", "Teléfono"], ["Ana", "+573167687288"]])

    def test_every_value_is_written_as_text_so_phones_survive(self):
        # A numeric cell would show +573167687288 as 5.73E+11.
        data = xlsx.build(["Teléfono"], [["+573167687288"]])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('t="inlineStr"', sheet)
        self.assertNotIn("<v>", sheet)

    def test_xml_special_characters_are_escaped(self):
        rows = sheet_rows(xlsx.build(["Mail"], [["a&b <c>"]]))
        self.assertEqual(rows[1], ["a&b <c>"])

    def test_none_and_numbers_become_plain_text(self):
        rows = sheet_rows(xlsx.build(["A", "B"], [[None, 3]]))
        self.assertEqual(rows[1], ["", "3"])

    def test_control_characters_are_dropped(self):
        # A NUL in a pasted name would make Excel refuse the whole file.
        rows = sheet_rows(xlsx.build(["A"], [["An\x00a"]]))
        self.assertEqual(rows[1], ["Ana"])

    def test_the_sheet_name_is_sanitized(self):
        data = xlsx.build(["A"], [], sheet_name="Clientes: 2026/09 [final]?")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            workbook = archive.read("xl/workbook.xml").decode()
        self.assertIn('name="Clientes 202609 final"', workbook)

    def test_column_letters(self):
        self.assertEqual([xlsx.column_letter(i) for i in (0, 25, 26, 27, 701, 702)],
                         ["A", "Z", "AA", "AB", "ZZ", "AAA"])


class ExportPageTests(TestCase):
    URL = reverse("section", args=["crm"]) + "?view=exportaciones"

    def test_the_page_is_real_now(self):
        response = self.client.get(self.URL)
        self.assertContains(response, "Base de datos de clientes")
        self.assertNotContains(response, "próximamente")

    def test_it_counts_the_clients_and_links_the_download(self):
        Client.objects.create(first_name="Ana", phone="+571")
        Client.objects.create(first_name="Luc", phone="+331")
        html = self.client.get(self.URL).content.decode()
        self.assertIn("2 clientes", html)
        self.assertIn(f'href="{reverse("clientes_export")}"', html)
        self.assertNotIn("is-disabled", html)

    def test_with_no_clients_the_button_is_inert(self):
        html = self.client.get(self.URL).content.decode()
        self.assertIn("Aún no hay clientes que exportar", html)
        self.assertIn("is-disabled", html)

    def test_the_columns_are_listed(self):
        html = self.client.get(self.URL).content.decode()
        for column in CLIENT_EXPORT_COLUMNS:
            self.assertIn(column, html)

    def test_the_clientes_toolbar_offers_the_download(self):
        html = self.client.get(reverse("section", args=["crm"])).content.decode()
        self.assertIn("Descargar Excel", html)
        self.assertIn(f'href="{reverse("clientes_export")}"', html)


class ExportDownloadTests(TestCase):
    URL = reverse("clientes_export")

    def test_answers_an_xlsx_attachment(self):
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], xlsx.CONTENT_TYPE)
        self.assertIn('attachment; filename="clientes-', response["Content-Disposition"])
        self.assertIn(".xlsx", response["Content-Disposition"])

    def test_one_row_per_client_in_the_declared_column_order(self):
        ana = Client.objects.create(
            first_name="Ana", last_name="Gil", phone="+573167687288",
            country="CO", email="ana@example.com", channel="whatsapp",
        )
        Client.objects.create(first_name="Bruno", phone="+525512345678", country="MX")
        conversation = Conversation.objects.create(contact=ana, channel="whatsapp")
        Conversation.objects.create(contact=ana, channel="instagram")
        tag = Tag.objects.create(name="VIP", color="green")
        messaging_services.apply_tag([conversation], tag)

        rows = sheet_rows(self.client.get(self.URL).content)
        self.assertEqual(rows[0], CLIENT_EXPORT_COLUMNS)
        self.assertEqual(len(rows), 3)
        ana_row = rows[1]
        self.assertEqual(ana_row[0], "Ana")
        self.assertEqual(ana_row[1], "Gil")
        self.assertEqual(ana_row[2], "+573167687288")
        self.assertEqual(ana_row[3], "Colombia")       # code -> name
        self.assertEqual(ana_row[4], "ana@example.com")
        self.assertEqual(ana_row[5], "WhatsApp")
        self.assertRegex(ana_row[6], r"^\d{2}/\d{2}/\d{4}$")
        self.assertEqual(ana_row[7], "2")               # conversations
        self.assertEqual(ana_row[8], "VIP")             # tags across threads
        self.assertEqual(rows[2][0], "Bruno")
        self.assertEqual(rows[2][3], "México")
        self.assertEqual(rows[2][5], "")                # no channel -> blank

    def test_an_unknown_country_code_is_kept_verbatim(self):
        Client.objects.create(first_name="Zed", phone="+9991", country="ZZ")
        rows = sheet_rows(self.client.get(self.URL).content)
        self.assertEqual(rows[1][3], "ZZ")

    def test_an_empty_base_still_downloads_a_valid_file(self):
        rows = sheet_rows(self.client.get(self.URL).content)
        self.assertEqual(rows, [CLIENT_EXPORT_COLUMNS])

    def test_the_query_count_does_not_grow_with_the_client_base(self):
        for n in range(30):
            client = Client.objects.create(first_name=f"C{n}", phone=f"+57{n:010d}")
            Conversation.objects.create(contact=client, channel="whatsapp")
        with self.assertNumQueries(3):   # clients, conversations, tags
            self.client.get(self.URL)
