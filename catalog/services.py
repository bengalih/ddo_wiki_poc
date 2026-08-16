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
        "item_type": request.GET.get("item_type", "").strip(),
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


def parse_enhancement_filters(request, count=None):
    filters = []

    if count is not None:
        # Positional reader used by the AJAX options endpoint: read
        # every row by index even if empty, so the response rows line
        # up with the dropdown rows in the page.
        for index in range(count):
            filters.append(
                {
                    "enhancement": request.GET.get(
                        f"enhancement_{index}",
                        "",
                    ).strip(),
                    "value": request.GET.get(
                        f"enhancement_value_{index}",
                        "",
                    ).strip(),
                    "min": _parse_min(
                        request.GET.get(
                            f"enhancement_min_{index}",
                            "",
                        ).strip()
                    ),
                }
            )

        return filters

    index = 0

    while True:
        enhancement = request.GET.get(
            f"enhancement_{index}",
            "",
        ).strip()

        value = request.GET.get(
            f"enhancement_value_{index}",
            "",
        ).strip()

        minimum = _parse_min(
            request.GET.get(
                f"enhancement_min_{index}",
                "",
            ).strip()
        )

        if not enhancement and not value and minimum is None:
            if index > 0:
                break
        else:
            filters.append(
                {
                    "enhancement": enhancement,
                    "value": value,
                    "min": minimum,
                }
            )

        index += 1

        if index >= 20:
            break

    # Backward compatibility with the previous single-enhancement
    # query-string format.
    if not filters:
        enhancement = request.GET.get(
            "enhancement",
            "",
        ).strip()

        value = request.GET.get(
            "enhancement_value",
            "",
        ).strip()

        if enhancement or value:
            filters.append(
                {
                    "enhancement": enhancement,
                    "value": value,
                    "min": _parse_min(
                        request.GET.get(
                            "enhancement_min",
                            "",
                        ).strip()
                    ),
                }
            )

    return filters


def apply_base_filters(items, base_filters):
    name = base_filters["name"]
    item_type = base_filters["item_type"]
    min_level = base_filters["min_level"]
    max_level = base_filters["max_level"]

    if name:
        items = items.filter(
            name__icontains=name
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


def apply_enhancement_filter(
    items,
    enhancement,
    value,
    min_magnitude=None,
    include_upgrades=True,
):
    base_kwargs = {
        "enhancements__variant__enhancement__name__iexact": (
            enhancement
        ),
    }

    if not include_upgrades:
        base_kwargs["enhancements__tier__isnull"] = True

    if enhancement:
        items = items.filter(**base_kwargs)

        if min_magnitude is not None:
            # A minimum magnitude overrides an exact value pick.
            min_kwargs = dict(base_kwargs)
            min_kwargs["enhancements__variant__magnitude__gte"] = (
                min_magnitude
            )

            items = items.filter(**min_kwargs)
        elif value:
            value_kwargs = dict(base_kwargs)
            value_kwargs["enhancements__variant__value__iexact"] = (
                value
            )

            items = items.filter(**value_kwargs)
    elif value:
        if not include_upgrades:
            items = items.filter(
                enhancements__variant__value__iexact=value,
                enhancements__tier__isnull=True,
            )
        else:
            items = items.filter(
                enhancements__variant__value__iexact=value
            )

    return items
