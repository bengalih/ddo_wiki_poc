import re
from functools import lru_cache

from catalog.enhancement_renders import (
    decompose_upgrade_chain,
    lookup_render,
)
from catalog.enhancement_rule_seeds import (
    DEFAULT_RULES,
    ENHANCEMENT_BONUS_TYPES,
)
from catalog.models import EnhancementRule

HANDLERS = {}

CONTROL_KEYS = {"nocat", "cat", "category"}

_NUMERIC_RE = re.compile(r"[+-]?\d+(?:\.\d+)?%?")


def seed_default_rules():
    for data in DEFAULT_RULES:
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


def register(name):
    def decorator(func):
        HANDLERS[name] = func
        return func

    return decorator


@lru_cache(maxsize=1)
def load_rules():
    if not EnhancementRule.objects.exists():
        seed_default_rules()

    rules = {}

    for rule in EnhancementRule.objects.filter(
        enabled=True
    ):
        rules[rule.template_name.lower()] = rule

    return rules


def clear_rules_cache():
    load_rules.cache_clear()


def expand_enhancement_template(
    name,
    parameters,
    raw_template,
    title=None,
):
    rules = load_rules()
    rule = rules.get(name.lower())

    if rule is None:
        # No rule handles this template. If the wiki has
        # already rendered it, use the wiki's canonical
        # name, magnitude, detail and exact display text.
        render = lookup_render(raw_template, title)

        if render is not None:
            # Upgrade templates ({{VaultsOfTheArtificersUpgrade}})
            # render the item's whole tier chain. Split it into
            # per-tier rows so the base (Tier 1) enhancements and
            # the upgrade rows are individually searchable.
            chain = decompose_upgrade_chain(render.raw_html)

            if chain is not None:
                for row in chain:
                    row["raw_template"] = raw_template

                return chain

            return [
                {
                    "name": render.canonical_name,
                    "value": render.value,
                    "detail": render.detail,
                    "display_text": render.display_text,
                    "raw_template": raw_template,
                }
            ]

    handler = HANDLERS.get(
        rule.handler if rule else "default",
        HANDLERS["default"],
    )
    config = rule.config if rule else {}

    results = handler(name, parameters, config)

    for result in results:
        result["raw_template"] = raw_template

    if rule is not None and len(results) == 1:
        # The rule already picked the filter name/value. When
        # the wiki has rendered this call, also attach its exact
        # display text so the item listing matches the wiki.
        render = lookup_render(raw_template, title)

        if render is not None and render.display_text:
            results[0]["display_text"] = render.display_text

    return results


def expand_item_rules(ctx):
    results = []

    for rule in load_rules().values():
        if rule.scope != "item":
            continue

        handler = HANDLERS.get(rule.handler)

        if not handler:
            continue

        results.extend(
            handler(
                rule.template_name,
                None,
                rule.config,
                ctx,
            )
        )

    # Mirror the list-scope path: when the wiki has rendered a
    # generated call (e.g. {{Mythic|Gloves|+1 or +3}}), its exact
    # display text wins - the wiki owns slot-name mapping that the
    # rule cannot reproduce (Gloves -> "Mythic Hands Boost ...").
    for result in results:
        raw = result.get("raw_template")

        if not raw:
            continue

        render = lookup_render(raw)

        if render is not None and render.display_text:
            result["display_text"] = render.display_text

    return results


def split_parameters(parameters):
    if not parameters:
        return []

    return [
        part.strip()
        for part in parameters.split("|")
        if part.strip()
    ]


@register("default")
def default(name, parameters, config, ctx=None):
    display_parts = []

    for part in split_parameters(parameters):
        if "=" in part:
            key = part.split("=", 1)[0].strip().lower()

            if key in CONTROL_KEYS:
                continue

            continue

        display_parts.append(part)

    if not display_parts:
        return [{"name": name, "value": ""}]

    if len(display_parts) == 1:
        return [
            {"name": name, "value": display_parts[0]}
        ]

    return [
        {
            "name": name,
            "value": ", ".join(display_parts),
        }
    ]


@register("enhancement_bonus")
def enhancement_bonus(name, parameters, config, ctx=None):
    parts = split_parameters(parameters)

    numeric = [
        part
        for part in parts
        if re.fullmatch(
            r"[+-]?\d+(?:\.\d+)?%?",
            part,
        )
    ]

    if not numeric:
        return [{"name": name, "value": "", "detail": ""}]

    type_map = config.get(
        "types",
        ENHANCEMENT_BONUS_TYPES,
    )

    type_letter = next(
        (
            part
            for part in parts
            if part in type_map
        ),
        None,
    )

    label = type_map.get(type_letter, "")

    display_name = config.get("name", name)
    implement_name = config.get(
        "implement_name",
        "Spellcasting implement",
    )

    enhancement_value = format_bonus(numeric[0])

    is_implement = type_letter in {
        "i",
        "ii",
        "io",
    }

    if is_implement:
        try:
            amount = int(numeric[0])
        except ValueError:
            amount = None

        if len(numeric) > 1:
            implement_value = numeric[1]
        elif amount is not None:
            implement_value = str(
                amount
                * config.get(
                    "implement_multiplier",
                    3,
                )
            )
        else:
            implement_value = None

        rows = []

        if implement_value is not None:
            rows.append(
                {
                    "name": implement_name,
                    "value": format_bonus(
                        implement_value
                    ),
                    "detail": "",
                    "display_text": bonus_display(
                        config,
                        "implement_display_template",
                        "Spellcasting Implement {value}",
                        "implement_display_types",
                        type_letter,
                        implement_value,
                    ),
                }
            )

        if type_letter != "io":
            rows.append(
                {
                    "name": display_name,
                    "value": enhancement_value,
                    "detail": "",
                    "display_text": bonus_display(
                        config,
                        "enhancement_display_template",
                        "{value} Enhancement Bonus",
                        "display_types",
                        type_letter,
                        numeric[0],
                    ),
                }
            )

        return rows

    if type_letter == "oi":
        rows = [
            {
                "name": display_name,
                "value": enhancement_value,
                "detail": label,
            }
        ]

        try:
            amount = int(numeric[0])
        except ValueError:
            amount = None

        if len(numeric) > 1:
            implement_value = numeric[1]
        elif amount is not None:
            implement_value = str(
                amount
                * config.get(
                    "implement_multiplier",
                    3,
                )
            )
        else:
            implement_value = None

        if implement_value is not None:
            rows.append(
                {
                    "name": implement_name,
                    "value": format_bonus(
                        implement_value
                    ),
                    "detail": "",
                }
            )

        return rows

    return [
        {
            "name": display_name,
            "value": enhancement_value,
            "detail": label,
            "display_text": bonus_display(
                config,
                "enhancement_display_template",
                "{value} Enhancement Bonus",
                "display_types",
                type_letter,
                numeric[0],
            ),
        }
    ]


def bonus_display(
    config,
    template_key,
    default_template,
    types_key,
    type_letter,
    value,
):
    # Compose the wiki's display text for a bonus row, but only for
    # type letters whose output has been verified against real wiki
    # renders (config `display_types` / `implement_display_types`).
    # Unverified types return "" so render_enhancements still asks
    # the wiki about them.
    if type_letter not in config.get(
        types_key,
        [],
    ):
        return ""

    return format_value(
        config.get(
            template_key,
            default_template,
        ),
        {
            "value": format_bonus(value),
        },
    )


def format_bonus(value):
    value = str(value).strip()

    if value.startswith(("+", "-")):
        return value

    if re.fullmatch(
        r"\d+(?:\.\d+)?%?",
        value,
    ):
        return f"+{value}"

    return value


def capitalize_name(value):
    # Normalize the first letter of spell type/element names so
    # {{SpellPower|corrosion|54}} and {{SpellPower|Corrosion|54}}
    # produce the same search value instead of two variants.
    value = value.strip()

    if not value:
        return value

    return value[0].upper() + value[1:]


def format_value(value_template, fields):
    value = value_template.format(**fields)
    return re.sub(r"\s+", " ", value).strip()


@register("spell_power")
def spell_power(name, parameters, config, ctx=None):
    parts = split_parameters(parameters)
    spell_type = parts[0] if parts else ""
    spell_type = capitalize_name(spell_type)
    amount = parts[1] if len(parts) > 1 else ""

    value = format_value(
        config.get(
            "value_template",
            "{type} {amount}",
        ),
        {
            "type": spell_type,
            "amount": amount,
        },
    )

    # The wiki renders the amount as "+N": {{SpellPower|Combustion|54}}
    # -> "Combustion +54". Only compose the display when the amount is
    # a plain number; anything else stays wiki-rendered.
    display_text = ""

    if spell_type and re.fullmatch(
        _NUMERIC_RE,
        amount,
    ):
        display_text = format_value(
            config.get(
                "display_template",
                "{type} {value}",
            ),
            {
                "type": spell_type,
                "value": format_bonus(amount),
            },
        )

    return [
        {
            "name": config.get(
                "name",
                "Spell Power",
            ),
            "value": value,
            "display_text": display_text,
        }
    ]


@register("spell_lore")
def spell_lore(name, parameters, config, ctx=None):
    parts = split_parameters(parameters)
    element = parts[0] if parts else ""
    element = capitalize_name(element)
    magnitude = parts[1] if len(parts) > 1 else ""

    value = format_value(
        config.get(
            "value_template",
            "{element} {magnitude}",
        ),
        {
            "element": element,
            "magnitude": magnitude,
        },
    )

    # {{Spelllore|Fire|III}} -> "Fire Lore III" (Roman magnitude
    # verbatim), but {{Spelllore|Sacred Ground|22}} -> "Sacred Ground
    # Lore +22%" (numeric magnitude becomes a percentage).
    display_text = ""

    if element and magnitude:
        magnitude_display = magnitude

        if (
            config.get(
                "numeric_magnitude_percent",
                False,
            )
            and re.fullmatch(_NUMERIC_RE, magnitude)
        ):
            magnitude_display = (
                format_bonus(magnitude) + "%"
            )

        display_text = format_value(
            config.get(
                "display_template",
                "{element} Lore {magnitude}",
            ),
            {
                "element": element,
                "magnitude": magnitude_display,
            },
        )

    return [
        {
            "name": config.get(
                "name",
                "Spell Lore",
            ),
            "value": value,
            "display_text": display_text,
        }
    ]


@register("healing_amp")
def healing_amp(name, parameters, config, ctx=None):
    # {{HealingAmp|8|h|Exceptional}} renders as
    # "Healing Amplification +8 (Exceptional)":
    #   arg 1 = amount
    #   arg 2 = amp type: h/healing -> Healing (default),
    #           r/repair -> Repair, n/negative -> Negative
    #   arg 3 = bonus type (Exceptional, Competence, ...)
    parts = split_parameters(parameters)
    amount = parts[0] if parts else ""
    amp_type = parts[1] if len(parts) > 1 else ""

    type_map = config.get(
        "types",
        {},
    )

    type_label = type_map.get(
        amp_type.strip().lower(),
        config.get("default_type", "Healing"),
    )

    detail = (
        parts[2] if len(parts) > 2 else ""
    )

    row_name = (
        f"{type_label} "
        f"{config.get('name', 'Amplification')}"
    )

    # {{HealingAmp|8|h|Exceptional}} -> "Exceptional Healing
    # Amplification +8". Only composed when the amount is numeric.
    display_text = ""

    if amount and re.fullmatch(
        _NUMERIC_RE,
        amount,
    ):
        display_text = format_value(
            config.get(
                "display_template",
                "{detail} {name} {value}",
            ),
            {
                "detail": detail,
                "name": row_name,
                "value": format_bonus(amount),
            },
        ).strip()

    return [
        {
            "name": row_name,
            "value": amount,
            "detail": detail,
            "display_text": display_text,
        }
    ]


@register("mythic_auto")
def mythic_auto(name, parameters, config, ctx=None):
    ctx = ctx or {}
    mythic = (ctx.get("mythic") or "").strip().lower()

    off_values = {
        str(value).lower()
        for value in config.get(
            "off_values",
            ["0", "no", "false", "n"],
        )
    }

    if mythic in off_values:
        return []

    named_type = (
        ctx.get("named_type_arg") or ""
    ).lower()
    item_type = (ctx.get("item_type") or "").strip()

    specs = None

    for entry in config.get("types", []):
        if named_type in entry.get("match", []):
            specs = entry.get("specs", [])
            break

    if specs is None:
        if not item_type:
            return []

        specs = [
            {
                "kind": item_type,
                "bonus": config.get(
                    "default_bonus",
                    "+1 or +3",
                ),
            }
        ]

    rows = []

    for spec in specs:
        kind = spec.get("kind")

        if not kind:
            continue

        bonus = spec.get(
            "bonus",
            config.get(
                "default_bonus",
                "+1 or +3",
            ),
        )

        rows.append(
            {
                "name": config.get(
                    "name",
                    "Mythic",
                ),
                "value": f"{kind} Boost {bonus}",
                "display_text": "",
                "raw_template": (
                    f"{{{{Mythic|{kind}|{bonus}}}}}"
                ),
            }
        )

    return rows
