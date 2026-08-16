from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0013_delete_enhancementrender"),
    ]

    operations = [
        migrations.AddField(
            model_name="enhancement",
            name="display_name",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Optional override shown in the search dropdown. "
                    "Lets an admin fix a name the wiki renders "
                    "awkwardly without re-parsing or re-coding."
                ),
                max_length=255,
            ),
        ),
    ]
