import django.db.models.deletion
from django.db import migrations, models
import django.db.models.functions.comparison


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0020_item_enchantment_tree_itemenhancement_possible"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="Enhancement",
            new_name="Enchantment",
        ),
        migrations.RenameModel(
            old_name="EnhancementVariant",
            new_name="EnchantmentVariant",
        ),
        migrations.RenameModel(
            old_name="ItemEnhancement",
            new_name="ItemEnchantment",
        ),
        migrations.RenameModel(
            old_name="EnhancementRule",
            new_name="EnchantmentRule",
        ),
        migrations.RemoveConstraint(
            model_name="enchantmentvariant",
            name="unique_enhancement_variant",
        ),
        migrations.RenameField(
            model_name="enchantmentvariant",
            old_name="enhancement",
            new_name="enchantment",
        ),
        migrations.AddConstraint(
            model_name="enchantmentvariant",
            constraint=models.UniqueConstraint(
                fields=[
                    "enchantment",
                    "value",
                    "detail",
                    "display_text",
                ],
                name="unique_enchantment_variant",
            ),
        ),
        migrations.AlterModelOptions(
            name="enchantmentvariant",
            options={
                "ordering": [
                    "enchantment__name",
                    "value",
                ],
            },
        ),
        migrations.RemoveConstraint(
            model_name="itemenchantment",
            name="unique_item_enhancement",
        ),
        migrations.AddConstraint(
            model_name="itemenchantment",
            constraint=models.UniqueConstraint(
                fields=[
                    "item",
                    "variant",
                    "tier",
                ],
                name="unique_item_enchantment",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="enchantment",
            name="unique_enhancement_effective_label",
        ),
        migrations.AddConstraint(
            model_name="enchantment",
            constraint=models.UniqueConstraint(
                django.db.models.functions.comparison.Coalesce(
                    django.db.models.functions.comparison.NullIf(
                        "display_name",
                        models.Value(""),
                    ),
                    "name",
                ),
                name="unique_enchantment_effective_label",
            ),
        ),
        migrations.AlterField(
            model_name="itemenchantment",
            name="item",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="enchantments",
                to="catalog.item",
            ),
        ),
        migrations.AlterField(
            model_name="enchantment",
            name="display_name",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Editable override shown to users everywhere the "
                    "enchantment appears (dropdowns, item pages). "
                    "Leave blank to use the wiki name. Never changes "
                    "the name field above."
                ),
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="enchantmentrule",
            name="scope",
            field=models.CharField(
                choices=[
                    ("list", "Enchantment list"),
                    ("item", "Item-wide"),
                ],
                default="list",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="itemenchantment",
            name="tier",
            field=models.PositiveIntegerField(
                blank=True,
                help_text=(
                    "Upgrade tier number when this enchantment only "
                    "exists after an item upgrade; blank for base."
                ),
                null=True,
            ),
        ),
    ]
