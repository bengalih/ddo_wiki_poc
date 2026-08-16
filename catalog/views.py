from django.core.paginator import Paginator
from django.db.models import F
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.shortcuts import render

from .models import (
    Enhancement,
    Item,
    ItemEnhancement,
    SyncState,
)
from .services import (
    apply_base_filters,
    apply_enhancement_filter,
    parse_base_filters,
    parse_enhancement_filters,
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


def enhancement_options(request):
    base_filters = parse_base_filters(request)

    items = apply_base_filters(
        Item.objects.all(),
        base_filters,
    )

    try:
        row_count = int(
            request.GET.get(
                "enhancement_filter_count",
                "1",
            )
        )
    except ValueError:
        row_count = 1

    row_count = max(1, min(row_count, 20))

    enhancement_filters = parse_enhancement_filters(
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
        for index, other_filter in enumerate(enhancement_filters):
            if index == filter_index:
                continue

            candidate_items = apply_enhancement_filter(
                candidate_items,
                other_filter["enhancement"],
                other_filter["value"],
                min_magnitude=other_filter.get("min"),
            )

        candidate_items = candidate_items.distinct()

        enhancement_rows = (
            ItemEnhancement.objects
            .filter(
                item__in=candidate_items
            )
            .values(
                "variant__enhancement__name",
                "variant__enhancement__display_name",
                "variant__value",
                "variant__magnitude",
            )
            .distinct()
            .order_by(
                "variant__enhancement__name",
                "variant__value",
            )
        )

        enhancements = {}
        labels = {}
        has_magnitudes = {}

        for row in enhancement_rows:
            enhancement_name = row["variant__enhancement__name"]
            value = row["variant__value"]

            labels[enhancement_name] = (
                row["variant__enhancement__display_name"]
                or enhancement_name
            )

            values = enhancements.setdefault(
                enhancement_name,
                [],
            )

            if value and value not in values:
                values.append(value)

            if row["variant__magnitude"] is not None:
                has_magnitudes[enhancement_name] = True

        rows.append(
            {
                "enhancements": enhancements,
                "labels": labels,
                "has_magnitudes": has_magnitudes,
            }
        )

    return JsonResponse(
        {
            "rows": rows,
        }
    )


def item_search(request):
    if request.GET.get(
        "enhancement_options"
    ) == "1":
        return enhancement_options(
            request
        )

    base_filters = parse_base_filters(request)

    enhancement_filters = parse_enhancement_filters(
        request
    )

    search_performed = any((
        base_filters["name"],
        base_filters["item_type"],
        base_filters["min_level"],
        base_filters["max_level"],
        enhancement_filters,
    ))

    sort_param, sort_field, descending = (
        parse_sort(request)
    )

    if search_performed:
        items = apply_base_filters(
            Item.objects.all(),
            base_filters,
        )

        for enhancement_filter in enhancement_filters:
            items = apply_enhancement_filter(
                items,
                enhancement_filter["enhancement"],
                enhancement_filter["value"],
                min_magnitude=enhancement_filter.get("min"),
                include_upgrades=base_filters["include_upgrades"],
            )

        items = (
            items
            .prefetch_related(
                "enhancements__variant__enhancement"
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

    item_types = (
        Item.objects
        .exclude(item_type="")
        .values_list("item_type", flat=True)
        .distinct()
        .order_by("item_type")
    )

    enhancements = sorted(
        Enhancement.objects
        .filter(variants__items__isnull=False)
        .distinct(),
        key=lambda enhancement: (
            enhancement.label.casefold()
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
            "item_types": item_types,
            "enhancements": enhancements,
            "search": {
                "name": base_filters["name"],
                "item_type": base_filters["item_type"],
                "min_level": base_filters["min_level"],
                "max_level": base_filters["max_level"],
                "include_upgrades": (
                    base_filters["include_upgrades"]
                ),
                "enhancement_filters": enhancement_filters,
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
