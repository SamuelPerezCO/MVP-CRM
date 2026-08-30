"""Hand-written renames for the Crear plantilla editor: the table's free-text
``text``/``template_type`` become the editor's ``body``/``sub_type``. Kept
separate from the auto-generated field additions (0006) so the renames can
never be misread as drop-and-add."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_messagetemplate"),
    ]

    operations = [
        migrations.RenameField(
            model_name="messagetemplate",
            old_name="text",
            new_name="body",
        ),
        migrations.RenameField(
            model_name="messagetemplate",
            old_name="template_type",
            new_name="sub_type",
        ),
    ]
