from django.db import migrations


def seed_rules(apps, schema_editor):
    EnhancementRule = apps.get_model(
        "catalog",
        "EnhancementRule",
    )

    rules = [
        {
            "template_name": "Enhancement bonus",
            "scope": "list",
            "handler": "enhancement_bonus",
            "order": 10,
            "config": {
                "implement_flag": "i",
                "implement_name": "Spellcasting implement",
                "implement_multiplier": 3,
            },
        },
        {
            "template_name": "SpellPower",
            "scope": "list",
            "handler": "spell_power",
            "order": 20,
            "config": {
                "name": "Spell Power",
                "value_template": "{type} {amount}",
            },
        },
        {
            "template_name": "Spelllore",
            "scope": "list",
            "handler": "spell_lore",
            "order": 30,
            "config": {
                "name": "Spell Lore",
                "value_template": "{element} {magnitude}",
            },
        },
        {
            "template_name": "Named item",
            "scope": "item",
            "handler": "mythic_auto",
            "order": 0,
            "config": {
                "name": "Mythic",
                "off_values": ["0", "no", "false", "n"],
                "default_bonus": "+1 or +3",
                "types": [
                    {
                        "match": ["weapon", "eternal wand"],
                        "specs": [
                            {
                                "kind": "Weapon",
                                "bonus": "+2 or +4",
                            }
                        ],
                    },
                    {
                        "match": ["armor"],
                        "specs": [
                            {
                                "kind": "Armor",
                                "bonus": "+2 or +4",
                            }
                        ],
                    },
                    {
                        "match": [
                            "orb",
                            "shield",
                            "rune arm",
                        ],
                        "specs": [
                            {
                                "kind": "Weapon",
                                "bonus": "+2 or +4",
                            },
                            {
                                "kind": "Shield",
                                "bonus": "+2 or +4",
                            },
                        ],
                    },
                    {
                        "match": ["quiver"],
                        "specs": [],
                    },
                ],
            },
        },
    ]

    for data in rules:
        template_name = data["template_name"]
        defaults = {
            key: value
            for key, value in data.items()
            if key != "template_name"
        }

        EnhancementRule.objects.update_or_create(
            template_name=template_name,
            defaults=defaults,
        )


def unseed_rules(apps, schema_editor):
    EnhancementRule = apps.get_model(
        "catalog",
        "EnhancementRule",
    )

    EnhancementRule.objects.filter(
        template_name__in=[
            "Enhancement bonus",
            "SpellPower",
            "Spelllore",
            "Named item",
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0005_enhancementrule"),
    ]

    operations = [
        migrations.RunPython(
            seed_rules,
            unseed_rules,
        ),
    ]
