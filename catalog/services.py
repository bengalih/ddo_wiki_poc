from .models import Item


def parse_base_filters(request):
    # The checkbox and a hidden twin share the name
    # "include_upgrades". When checked the browser submits both
    # ("1" from the checkbox first, then "0" from the hidden), so
    # the FIRST value decides; when unchecked only the hidden "0"
    # is submitted. No JS needed for the search to honor the box.
    include_upgrades_values = request.GET.getlist(
        "include_upgrades"
    )

    include_upgrades = not (
        include_upgrades_values
        and str(
            include_upgrades_values[0]
        ).strip().lower()
        in {"", "0", "false", "no", "off"}
    )

    return {
        "name": request.GET.get("name", "").strip(),
        "category": request.GET.get("category", "").strip(),
        "type": request.GET.get("type", "").strip(),
        "min_level": request.GET.get("min_level", "").strip(),
        "max_level": request.GET.get("max_level", "").strip(),
        "include_upgrades": include_upgrades,
    }


def _parse_min(value):
    if not value:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_enchantment_filters(request, count=None):
    filters = []

    if count is not None:
        # Positional reader used by the AJAX options endpoint: read
        # every row by index even if empty, so the response rows line
        # up with the dropdown rows in the page.
        for index in range(count):
            filters.append(
                {
                    "enchantment": request.GET.get(
                        f"enchantment_{index}",
                        "",
                    ).strip(),
                    "value": request.GET.get(
                        f"enchantment_value_{index}",
                        "",
                    ).strip(),
                    "min": _parse_min(
                        request.GET.get(
                            f"enchantment_min_{index}",
                            "",
                        ).strip()
                    ),
                }
            )

        return filters

    index = 0

    while True:
        enchantment = request.GET.get(
            f"enchantment_{index}",
            "",
        ).strip()

        value = request.GET.get(
            f"enchantment_value_{index}",
            "",
        ).strip()

        minimum = _parse_min(
            request.GET.get(
                f"enchantment_min_{index}",
                "",
            ).strip()
        )

        if not enchantment and not value and minimum is None:
            if index > 0:
                break
        else:
            filters.append(
                {
                    "enchantment": enchantment,
                    "value": value,
                    "min": minimum,
                }
            )

        index += 1

        if index >= 20:
            break

    # Backward compatibility with the previous single-enchantment
    # query-string format.
    if not filters:
        enchantment = request.GET.get(
            "enchantment",
            "",
        ).strip()

        value = request.GET.get(
            "enchantment_value",
            "",
        ).strip()

        if enchantment or value:
            filters.append(
                {
                    "enchantment": enchantment,
                    "value": value,
                    "min": _parse_min(
                        request.GET.get(
                            "enchantment_min",
                            "",
                        ).strip()
                    ),
                }
            )

    return filters


def apply_base_filters(items, base_filters):
    name = base_filters["name"]
    category = base_filters["category"]
    item_type = base_filters["type"]
    min_level = base_filters["min_level"]
    max_level = base_filters["max_level"]

    if name:
        items = items.filter(
            name__icontains=name
        )

    if category:
        items = items.filter(
            item_class__iexact=category
        )

    if item_type:
        items = items.filter(
            item_type__iexact=item_type
        )

    if min_level:
        try:
            items = items.filter(
                minimum_level__gte=int(min_level)
            )
        except ValueError:
            pass

    if max_level:
        try:
            items = items.filter(
                minimum_level__lte=int(max_level)
            )
        except ValueError:
            pass

    return items


def apply_enchantment_filter(
    items,
    enchantment,
    value,
    min_magnitude=None,
    include_upgrades=True,
):
    base_kwargs = {
        "enchantments__variant__enchantment__name__iexact": (
            enchantment
        ),
    }

    if not include_upgrades:
        base_kwargs["enchantments__tier__isnull"] = True

    if enchantment:
        items = items.filter(**base_kwargs)

        if min_magnitude is not None:
            # A minimum magnitude overrides an exact value pick.
            min_kwargs = dict(base_kwargs)
            min_kwargs["enchantments__variant__magnitude__gte"] = (
                min_magnitude
            )

            items = items.filter(**min_kwargs)
        elif value:
            value_kwargs = dict(base_kwargs)
            value_kwargs["enchantments__variant__value__iexact"] = (
                value
            )

            items = items.filter(**value_kwargs)
    elif value:
        if not include_upgrades:
            items = items.filter(
                enchantments__variant__value__iexact=value,
                enchantments__tier__isnull=True,
            )
        else:
            items = items.filter(
                enchantments__variant__value__iexact=value
            )

    return items
