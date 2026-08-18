from django.db import migrations


def fix_handler(apps, schema_editor):
    EnchantmentRule = apps.get_model("catalog", "EnchantmentRule")
    EnchantmentRule.objects.filter(
        handler="enhancement_bonus"
    ).update(
        handler="enchantment_bonus"
    )


def revert_handler(apps, schema_editor):
    EnchantmentRule = apps.get_model("catalog", "EnchantmentRule")
    EnchantmentRule.objects.filter(
        handler="enchantment_bonus"
    ).update(
        handler="enhancement_bonus"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0021_rename_enhancement_to_enchantment"),
    ]

    operations = [
        migrations.RunPython(
            fix_handler,
            revert_handler,
        ),
    ]
