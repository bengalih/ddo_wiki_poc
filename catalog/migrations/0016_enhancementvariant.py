import django.db.models.deletion
from django.db import migrations, models

from catalog.enchantment_values import parse_magnitude


def deduplicate_enhancements(apps, schema_editor):
    """Collapse per-item enhancement text into shared variants.

    The old ItemEnhancement table copied the rendered text
    (value/detail/display_text/magnitude) into one row per item.
    Build one EnhancementVariant per distinct combination and point
    every item row at it.
    """
    ItemEnhancement = apps.get_model(
        "catalog",
        "ItemEnhancement",
    )
    EnhancementVariant = apps.get_model(
        "catalog",
        "EnhancementVariant",
    )

    distinct = (
        ItemEnhancement.objects
        .values(
            "enhancement_id",
            "value",
            "detail",
            "display_text",
        )
        .order_by()
        .distinct()
    )

    variant_by_key = {}

    for row in distinct:
        key = (
            row["enhancement_id"],
            row["value"],
            row["detail"],
            row["display_text"],
        )

        if key in variant_by_key:
            continue

        variant = EnhancementVariant.objects.create(
            enhancement_id=row["enhancement_id"],
            value=row["value"],
            detail=row["detail"],
            display_text=row["display_text"],
            magnitude=parse_magnitude(row["value"]),
        )

        variant_by_key[key] = variant.id

    batch = []
    batch_size = 2000

    for item_enhancement in (
        ItemEnhancement.objects.iterator()
    ):
        key = (
            item_enhancement.enhancement_id,
            item_enhancement.value,
            item_enhancement.detail,
            item_enhancement.display_text,
        )

        item_enhancement.variant_id = (
            variant_by_key[key]
        )
        batch.append(item_enhancement)

        if len(batch) >= batch_size:
            ItemEnhancement.objects.bulk_update(
                batch,
                ["variant_id"],
            )
            batch = []

    if batch:
        ItemEnhancement.objects.bulk_update(
            batch,
            ["variant_id"],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0015_alter_enhancement_display_name_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="EnhancementVariant",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "value",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "detail",
                    models.CharField(
                        blank=True,
                        max_length=255,
                    ),
                ),
                (
                    "display_text",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "Verbatim display text from the wiki "
                            "render cache."
                        ),
                        max_length=255,
                    ),
                ),
                (
                    "magnitude",
                    models.FloatField(
                        blank=True,
                        help_text=(
                            "Numeric magnitude parsed from the value "
                            "for minimum-at-least searches, e.g. 22 "
                            "from +22%; null when the value is not "
                            "numeric."
                        ),
                        null=True,
                    ),
                ),
                (
                    "enhancement",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="variants",
                        to="catalog.enhancement",
                    ),
                ),
            ],
            options={
                "ordering": [
                    "enhancement__name",
                    "value",
                ],
            },
        ),
        migrations.AddField(
            model_name="itemenhancement",
            name="variant",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="items",
                to="catalog.enhancementvariant",
            ),
        ),
        migrations.RunPython(
            deduplicate_enhancements,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="itemenhancement",
            name="variant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="items",
                to="catalog.enhancementvariant",
            ),
        ),
        migrations.AddConstraint(
            model_name="enhancementvariant",
            constraint=models.UniqueConstraint(
                fields=[
                    "enhancement",
                    "value",
                    "detail",
                    "display_text",
                ],
                name="unique_enhancement_variant",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="itemenhancement",
            name="unique_item_enhancement",
        ),
        migrations.RemoveField(
            model_name="itemenhancement",
            name="enhancement",
        ),
        migrations.RemoveField(
            model_name="itemenhancement",
            name="value",
        ),
        migrations.RemoveField(
            model_name="itemenhancement",
            name="detail",
        ),
        migrations.RemoveField(
            model_name="itemenhancement",
            name="display_text",
        ),
        migrations.RemoveField(
            model_name="itemenhancement",
            name="magnitude",
        ),
        migrations.AddConstraint(
            model_name="itemenhancement",
            constraint=models.UniqueConstraint(
                fields=[
                    "item",
                    "variant",
                    "tier",
                ],
                name="unique_item_enhancement",
            ),
        ),
    ]
