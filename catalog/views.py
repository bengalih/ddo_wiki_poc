from django.core.paginator import Paginator
from django.db.models import F
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.shortcuts import render

from .models import (
    Enchantment,
    Item,
    ItemEnchantment,
    SyncState,
)
from .services import (
    apply_base_filters,
    apply_enchantment_filter,
    parse_base_filters,
    parse_enchantment_filters,
)

SORT_FIELDS = (
    "name",
    "item_type",
    "minimum_level",
)


def parse_sort(request):
    sort_param = request.GET.get(
        "sort",
        "",
    ).strip()

    descending = sort_param.startswith("-")
    sort_field = sort_param.lstrip("-")

    if sort_field not in SORT_FIELDS:
        return "name", "name", False

    return sort_param, sort_field, descending


def enchantment_options(request):
    base_filters = parse_base_filters(request)

    items = apply_base_filters(
        Item.objects.all(),
        base_filters,
    )

    try:
        row_count = int(
            request.GET.get(
                "enchantment_filter_count",
                "1",
            )
        )
    except ValueError:
        row_count = 1

    row_count = max(1, min(row_count, 20))

    enchantment_filters = parse_enchantment_filters(
        request,
        count=row_count,
    )

    rows = []

    for filter_index in range(row_count):
        candidate_items = items

        # Bidirectional scoping: apply every OTHER row's filter so
        # each dropdown reflects the full current search. The row
        # itself is excluded, otherwise the dropdown for the value
        # being chosen would shrink away as soon as it is selected.
        for index, other_filter in enumerate(enchantment_filters):
            if index == filter_index:
                continue

            candidate_items = apply_enchantment_filter(
                candidate_items,
                other_filter["enchantment"],
                other_filter["value"],
                min_magnitude=other_filter.get("min"),
            )

        candidate_items = candidate_items.distinct()

        enchantment_rows = (
            ItemEnchantment.objects
            .filter(
                item__in=candidate_items
            )
            .values(
                "variant__enchantment__name",
                "variant__enchantment__display_name",
                "variant__value",
                "variant__magnitude",
            )
            .distinct()
            .order_by(
                "variant__enchantment__name",
                "variant__value",
            )
        )

        enchantments = {}
        labels = {}
        has_magnitudes = {}

        for row in enchantment_rows:
            enchantment_name = row["variant__enchantment__name"]
            value = row["variant__value"]

            labels[enchantment_name] = (
                row["variant__enchantment__display_name"]
                or enchantment_name
            )

            values = enchantments.setdefault(
                enchantment_name,
                [],
            )

            if value and value not in values:
                values.append(value)

            if row["variant__magnitude"] is not None:
                has_magnitudes[enchantment_name] = True

        rows.append(
            {
                "enchantments": enchantments,
                "labels": labels,
                "has_magnitudes": has_magnitudes,
            }
        )

    category = request.GET.get("category", "")

    type_rows = (
        Item.objects
        .exclude(item_type="")
    )

    if category:
        type_rows = type_rows.filter(
            item_class__iexact=category
        )

    types = [
        {
            "value": value,
            "label": value,
        }
        for value in (
            type_rows
            .values_list("item_type", flat=True)
            .distinct()
            .order_by("item_type")
        )
    ]

    return JsonResponse(
        {
            "rows": rows,
            "types": types,
        }
    )


def item_search(request):
    if request.GET.get(
        "enchantment_options"
    ) == "1":
        return enchantment_options(
            request
        )

    base_filters = parse_base_filters(request)

    enchantment_filters = parse_enchantment_filters(
        request
    )

    search_performed = any((
        base_filters["name"],
        base_filters["category"],
        base_filters["type"],
        base_filters["min_level"],
        base_filters["max_level"],
        enchantment_filters,
    ))

    sort_param, sort_field, descending = (
        parse_sort(request)
    )

    if search_performed:
        items = apply_base_filters(
            Item.objects.all(),
            base_filters,
        )

        for enchantment_filter in enchantment_filters:
            items = apply_enchantment_filter(
                items,
                enchantment_filter["enchantment"],
                enchantment_filter["value"],
                min_magnitude=enchantment_filter.get("min"),
                include_upgrades=base_filters["include_upgrades"],
            )

        items = (
            items
            .prefetch_related(
                "enchantments__variant__enchantment"
            )
            .distinct()
        )

        if sort_field == "minimum_level":
            order = F("minimum_level")
        else:
            order = Lower(sort_field)

        if descending:
            order = (
                order.desc(nulls_last=True)
                if sort_field == "minimum_level"
                else order.desc()
            )
        else:
            order = (
                order.asc(nulls_last=True)
                if sort_field == "minimum_level"
                else order.asc()
            )

        items = items.order_by(order)

        paginator = Paginator(items, 50)
    else:
        paginator = Paginator(
            Item.objects.none(),
            50,
        )

    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    categories = (
        Item.objects
        .exclude(item_class="")
        .values_list("item_class", flat=True)
        .distinct()
        .order_by("item_class")
    )

    selected_category = base_filters["category"]

    type_rows = (
        Item.objects
        .exclude(item_type="")
    )

    if selected_category:
        type_rows = type_rows.filter(
            item_class__iexact=selected_category
        )

    type_rows = (
        type_rows
        .values_list("item_type", flat=True)
        .distinct()
        .order_by("item_type")
    )

    types = [
        {"value": value, "label": value}
        for value in type_rows
    ]

    enchantments = sorted(
        Enchantment.objects
        .filter(variants__items__isnull=False)
        .distinct(),
        key=lambda enchantment: (
            enchantment.label.casefold()
        ),
    )

    querystring = request.GET.copy()
    querystring.pop("sort", None)
    querystring.pop("page", None)
    base_querystring = querystring.urlencode()

    sync_state = SyncState.objects.first()

    return render(
        request,
        "catalog/item_search.html",
        {
            "page_obj": page_obj,
            "categories": categories,
            "types": types,
            "enchantments": enchantments,
            "search": {
                "name": base_filters["name"],
                "category": base_filters["category"],
                "type": base_filters["type"],
                "min_level": base_filters["min_level"],
                "max_level": base_filters["max_level"],
                "include_upgrades": (
                    base_filters["include_upgrades"]
                ),
                "enchantment_filters": enchantment_filters,
            },
            "item_count": Item.objects.count(),
            "sync_as_of": (
                sync_state.as_of
                if sync_state
                else None
            ),
            "search_performed": search_performed,
            "sort": sort_param,
            "base_querystring": base_querystring,
        },
    )
