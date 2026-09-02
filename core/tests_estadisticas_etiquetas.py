"""Tests for the Estadísticas > Etiquetas panel: every tag with how many
conversations carry it, the summary tiles, and the empty state."""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Client
from messaging import services
from messaging.models import Conversation, Tag


def make_conversation(phone: str) -> Conversation:
    contact = Client.objects.create(first_name=f"C{phone[-2:]}", phone=phone)
    return Conversation.objects.create(
        contact=contact, channel="whatsapp", last_message_at=timezone.now()
    )


def section_url() -> str:
    return reverse("section", args=["estadisticas"]) + "?view=etiquetas"


class EtiquetasPanelTests(TestCase):
    def setUp(self):
        self.venta = Tag.objects.create(name="VENTA EFECTIVA", color="green")
        self.nuevo = Tag.objects.create(name="CLIENTE NUEVO", color="blue")
        self.viejo = Tag.objects.create(name="CAMPAÑA 2024", color="gray")

        self.chats = [make_conversation(f"+5730000001{i:02d}") for i in range(3)]
        services.apply_tag(self.chats, self.venta)          # on all 3
        services.apply_tag(self.chats[:1], self.nuevo)      # on 1
        # Applied while active, archived after -- the only way an archived
        # tag ends up with history (apply_tag refuses archived tags).
        services.apply_tag(self.chats[:2], self.viejo)
        services.set_tag_archived(self.viejo, True)
        self.untagged = make_conversation("+573000000199")  # never tagged

    def test_panel_is_real_not_the_placeholder(self):
        response = self.client.get(section_url())
        self.assertEqual(response.context["active_view"], "etiquetas")
        self.assertEqual(
            response.context["panel_template"],
            "partials/estadisticas/panels/etiquetas.html",
        )
        self.assertContains(response, "Estadísticas de etiquetas")
        self.assertNotContains(response, "próximamente")

    def test_every_tag_renders_as_its_pill_with_its_count(self):
        html = self.client.get(section_url()).content.decode()
        for tag in [self.venta, self.nuevo, self.viejo]:
            with self.subTest(tag.name):
                # Archived pills carry an extra modifier class before the >.
                self.assertRegex(
                    html, f'tag-pill--{tag.color}[^>]*>{tag.name}</span>'
                )
        rows = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        self.assertIn('tag-stats__num">3</td>', rows)
        self.assertIn('tag-stats__num">1</td>', rows)
        self.assertIn('tag-stats__num">2</td>', rows)

    def test_counts_chats_not_applications(self):
        # Re-applying is a no-op row-wise (unique constraint), so the count
        # stays "chats with this tag".
        services.apply_tag(self.chats, self.venta)
        response = self.client.get(section_url())
        venta = next(
            t for t in response.context["tag_stats"] if t.pk == self.venta.pk
        )
        self.assertEqual(venta.chats, 3)

    def test_active_tags_rank_by_count_above_archived_ones(self):
        stats = self.client.get(section_url()).context["tag_stats"]
        self.assertEqual(
            [t.pk for t in stats], [self.venta.pk, self.nuevo.pk, self.viejo.pk]
        )

    def test_archived_tag_is_marked(self):
        response = self.client.get(section_url())
        self.assertContains(response, "tag-row--archived")
        self.assertContains(response, "· archivada")

    def test_tiles_report_the_tagged_untagged_split(self):
        context = self.client.get(section_url()).context
        self.assertEqual(context["active_tag_count"], 2)
        self.assertEqual(context["archived_tag_count"], 1)
        self.assertEqual(context["total_conversations"], 4)
        # 3 tagged chats -- the one carrying two tags counts once.
        self.assertEqual(context["tagged_conversations"], 3)
        self.assertEqual(context["untagged_conversations"], 1)

    def test_busiest_tag_gets_the_full_bar(self):
        html = self.client.get(section_url()).content.decode()
        self.assertEqual(self.client.get(section_url()).context["max_chats"], 3)
        self.assertIn("width: 100%", html)

    def test_zero_count_tag_renders_without_a_bar_fill(self):
        Tag.objects.create(name="SIN USO", color="pink")
        html = self.client.get(section_url()).content.decode()
        row = html.split(">SIN USO</span>", 1)[1].split("</tr>", 1)[0]
        self.assertIn('tag-stats__num">0</td>', row)
        self.assertNotIn("tag-bar__fill", row)

    def test_panel_endpoint_returns_the_same_fragment(self):
        response = self.client.get(reverse("estadisticas_panel", args=["etiquetas"]))
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<html", body)
        self.assertNotIn("side-nav", body)
        self.assertIn("Estadísticas de etiquetas", body)
        self.assertIn(self.venta.name, body)


class EtiquetasPanelEmptyTests(TestCase):
    def test_no_tags_renders_the_empty_state(self):
        response = self.client.get(section_url())
        self.assertContains(response, "Aún no hay etiquetas")
        # The hint links to where tags are actually created.
        crm_url = reverse("section", args=["crm"]) + "?view=etiquetas"
        self.assertContains(response, f'href="{crm_url}"')

    def test_no_conversations_still_renders_zeros(self):
        Tag.objects.create(name="PRIMERA", color="teal")
        response = self.client.get(section_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PRIMERA")
        self.assertEqual(response.context["max_chats"], 0)
        self.assertEqual(response.context["untagged_conversations"], 0)
