"""Fold pre-editor free-text values into the new enums: category strings like
"Marketing" become their keys, and any old ``tipo`` string ("Texto") becomes
the default sub-type. Unknown values fall back to marketing/custom rather
than failing -- these were display-only strings with no downstream logic."""

from django.db import migrations

_CATEGORY_KEYS = {
    "marketing": "marketing",
    "utility": "utility",
    "autenticación": "authentication",
    "authentication": "authentication",
}

_SUB_TYPE_KEYS = {"custom", "limited_time_offer", "carousel", "auth_code"}


def map_legacy_values(apps, schema_editor):
    MessageTemplate = apps.get_model("core", "MessageTemplate")
    for template in MessageTemplate.objects.all():
        template.category = _CATEGORY_KEYS.get(
            template.category.strip().lower(), "marketing"
        )
        if template.sub_type not in _SUB_TYPE_KEYS:
            template.sub_type = "custom"
        template.save(update_fields=["category", "sub_type"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0006_messagetemplate_body_sample_values_and_more"),
    ]

    operations = [
        # No reverse mapping: the old free-text strings are unrecoverable and
        # the new keys are valid under the old schema anyway.
        migrations.RunPython(map_legacy_values, migrations.RunPython.noop),
    ]
