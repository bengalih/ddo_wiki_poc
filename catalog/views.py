import json

from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render

from .models import Enhancement, Item, ItemEnhancement

def enhancement_options(request):
    name = request.GET.get("name", "").strip()
    item_type = request.GET.get("item_type", "").strip()
    min_level = request.GET.get("min_level", "").strip()
    max_level = request.GET.get("max_level", "").strip()

    items = Item.objects.all()

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

    try:
        filter_count = int(
            request.GET.get(
                "enhancement_filter_count",
                "1",
            )
        )
    except ValueError:
        filter_count = 1

    filter_count = max(
        1,
        min(filter_count, 20),
    )

    filters = []

    for index in range(filter_count):

        enhancement = request.GET.get(
            f"enhancement_{index}",
            "",
        ).strip()

        value = request.GET.get(
            f"enhancement_value_{index}",
            "",
        ).strip()

        filters.append(
            {
                "enhancement": enhancement,
                "value": value,
            }
        )

    rows = []

    for filter_index in range(
        max(len(filters), 1)
    ):
        candidate_items = items

        # Bidirectional scoping: apply every OTHER row's
        # filter so each dropdown reflects the full current
        # search. The row itself is excluded, otherwise the
        # dropdown for the value being chosen would shrink
        # away as soon as it is selected.
        for index, other_filter in enumerate(filters):

            if index == filter_index:
                continue

            enhancement = other_filter[
                "enhancement"
            ]

            value = other_filter[
                "value"
            ]

            if enhancement:
                candidate_items = candidate_items.filter(
                    enhancements__enhancement__name__iexact=
                    enhancement
                )

                if value:
                    candidate_items = candidate_items.filter(
                        enhancements__enhancement__name__iexact=
                        enhancement,
                        enhancements__value__iexact=
                        value,
                    )

            elif value:
                candidate_items = candidate_items.filter(
                    enhancements__value__iexact=value
                )

        candidate_items = candidate_items.distinct()

        rows.append(
            {
                "enhancements": {}
            }
        )

        enhancement_rows = (
            ItemEnhancement.objects
            .filter(
                item__in=candidate_items
            )
            .values(
                "enhancement__name",
                "value",
            )
            .distinct()
            .order_by(
                "enhancement__name",
                "value",
            )
        )

        for row in enhancement_rows:
            enhancement_name = row[
                "enhancement__name"
            ]

            value = row["value"]

            values = rows[
                filter_index
            ]["enhancements"].setdefault(
                enhancement_name,
                [],
            )

            if (
                value
                and value not in values
            ):
                values.append(value)

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
        
    name = request.GET.get("name", "").strip()
    item_type = request.GET.get("item_type", "").strip()
    min_level = request.GET.get("min_level", "").strip()
    max_level = request.GET.get("max_level", "").strip()

    enhancement_filters = []
    index = 0
    while True:
        enhancement = request.GET.get(
            f"enhancement_{index}",
            "",
        ).strip()

        enhancement_value = request.GET.get(
            f"enhancement_value_{index}",
            "",
        ).strip()

        if not enhancement and not enhancement_value:
            if index > 0:
                break
        else:
            enhancement_filters.append(
                {
                    "enhancement": enhancement,
                    "value": enhancement_value,
                }
            )

        index += 1

        if index >= 20:
            break

    # Backward compatibility with the previous single-enhancement
    # query-string format.
    if not enhancement_filters:
        enhancement = request.GET.get(
            "enhancement",
            "",
        ).strip()

        enhancement_value = request.GET.get(
            "enhancement_value",
            "",
        ).strip()

        if enhancement or enhancement_value:
            enhancement_filters.append(
                {
                    "enhancement": enhancement,
                    "value": enhancement_value,
                }
            )

    items = Item.objects.all()

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

    # Each enhancement filter is applied separately.
    #
    # This gives us AND behavior:
    #
    #   enhancement_0 = Deadly
    #   enhancement_1 = Seeker
    #
    # means the item must have BOTH Deadly AND Seeker.
    #
    # Filtering the enhancement and value together also guarantees
    # that the value belongs to that particular enhancement.
    for enhancement_filter in enhancement_filters:
        enhancement = enhancement_filter["enhancement"]
        enhancement_value = enhancement_filter["value"]

        if enhancement:
            items = items.filter(
                enhancements__enhancement__name__iexact=enhancement
            )

            if enhancement_value:
                items = items.filter(
                    enhancements__enhancement__name__iexact=enhancement,
                    enhancements__value__iexact=enhancement_value,
                )

        elif enhancement_value:
            items = items.filter(
                enhancements__value__iexact=enhancement_value
            )

    items = (
        items
        .prefetch_related("enhancements__enhancement")
        .distinct()
        .order_by("name")
    )

    paginator = Paginator(items, 50)

    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    item_types = (
        Item.objects
        .exclude(item_type="")
        .values_list("item_type", flat=True)
        .distinct()
        .order_by("item_type")
    )

    enhancements = (
        Enhancement.objects
        .order_by("name")
    )

    # Build:
    #
    #   item type
    #       -> enhancement
    #           -> values
    #
    # This lets the search UI restrict both the enhancement list
    # and its values to the currently selected item type.
    enhancement_values = {}

    enhancement_rows = (
        ItemEnhancement.objects
        .values(
            "item__item_type",
            "enhancement__name",
            "value",
        )
        .distinct()
        .order_by(
            "item__item_type",
            "enhancement__name",
            "value",
        )
    )

    for row in enhancement_rows:
        item_type_name = row["item__item_type"]
        enhancement_name = row["enhancement__name"]
        value = row["value"]

        type_enhancements = enhancement_values.setdefault(
            item_type_name,
            {},
        )

        values = type_enhancements.setdefault(
            enhancement_name,
            [],
        )

        if value and value not in values:
            values.append(value)

    return render(
        request,
        "catalog/item_search.html",
        {
            "page_obj": page_obj,
            "item_types": item_types,
            "enhancements": enhancements,
            "enhancement_values_json": json.dumps(
                enhancement_values
            ),
            "search": {
                "name": name,
                "item_type": item_type,
                "min_level": min_level,
                "max_level": max_level,
                "enhancement_filters": enhancement_filters,
            },
        },
    )