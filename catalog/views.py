from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render

from .models import Enhancement, Item


def item_search(request):
    name = request.GET.get("name", "").strip()
    item_type = request.GET.get("item_type", "").strip()
    min_level = request.GET.get("min_level", "").strip()
    max_level = request.GET.get("max_level", "").strip()
    enhancement = request.GET.get("enhancement", "").strip()
    enhancement_value = request.GET.get(
        "enhancement_value",
        "",
    ).strip()

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

    if enhancement:
        items = items.filter(
            enhancements__enhancement__name__iexact=enhancement
        )

        if enhancement_value:
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
                "enhancement": enhancement,
                "enhancement_value": enhancement_value,
            },
        },
    )

