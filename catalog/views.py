from django.core.paginator import Paginator
from django.shortcuts import render

from .models import Enhancement, Item


def item_search(request):
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
                    enhancements__value__icontains=enhancement_value,
                )

        elif enhancement_value:
            items = items.filter(
                enhancements__value__icontains=enhancement_value
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

    return render(
        request,
        "catalog/item_search.html",
        {
            "page_obj": page_obj,
            "item_types": item_types,
            "enhancements": enhancements,
            "search": {
                "name": name,
                "item_type": item_type,
                "min_level": min_level,
                "max_level": max_level,
                "enhancement_filters": enhancement_filters,
            },
        },
    )