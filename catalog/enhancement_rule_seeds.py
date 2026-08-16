# Seed definitions for enhancement template rules.
#
# Edit this file to add or change rules. Each rule's `config` is
# interpreted by the matching handler function registered in
# enhancement_rules.py (see the `handler` key). The database remains
# the runtime source of truth - these seeds are only loaded when the
# EnhancementRule table is empty (see load_rules()).

# DDO wiki Template:Enhancement bonus type letters.
ENHANCEMENT_BONUS_TYPES = {
    "a": "Armor",
    "s": "Shield",
    "w": "Weapon",
    "i": "Spellcasting implement",
    "ii": "Spellcasting implement",
    "io": "Spellcasting implement",
    "o": "Orb",
    "oi": "Orb",
}

DEFAULT_RULES = [
    {
        "template_name": "Enhancement bonus",
        "scope": "list",
        "handler": "enhancement_bonus",
        "order": 10,
        "config": {
            "implement_flag": "i",
            "implement_name": "Spellcasting implement",
            "implement_multiplier": 3,
            # display_text compositions, verified against real
            # wiki renders. Type letters NOT listed below (o/oi/ii/io)
            # are left to the wiki render cache because their output
            # has not been verified.
            "enhancement_display_template": (
                "{value} Enhancement Bonus"
            ),
            "implement_display_template": (
                "Spellcasting Implement {value}"
            ),
            "display_types": ["a", "s", "w", "i"],
            "implement_display_types": ["i"],
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
            "display_template": "{type} {value}",
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
            "display_template": "{element} Lore {magnitude}",
            # Numeric magnitudes render as a percentage: the wiki
            # turns {{Spelllore|Sacred Ground|22}} into
            # "Sacred Ground Lore +22%".
            "numeric_magnitude_percent": True,
        },
    },
    {
        "template_name": "HealingAmp",
        "scope": "list",
        "handler": "healing_amp",
        "order": 40,
        "config": {
            "name": "Amplification",
            "default_type": "Healing",
            "types": {
                "h": "Healing",
                "healing": "Healing",
                "r": "Repair",
                "repair": "Repair",
                "n": "Negative",
                "negative": "Negative",
            },
            "display_template": "{detail} {name} {value}",
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
