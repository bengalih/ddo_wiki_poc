from django.contrib import admin
from django.db.models import Count

from catalog.models import (
    Enchantment,
    EnchantmentVariant,
    Item,
    ItemEnchantment,
    SyncState,
)


class ItemEnchantmentInline(admin.TabularInline):
    # Shown on an Item's admin page: each item has a manageable
    # handful of enchantments, so inspecting them per item is fine.
    # The raw ItemEnchantment table (tens of thousands of rows) is
    # not registered as its own admin section.
    model = ItemEnchantment
    extra = 0
    readonly_fields = [
        "variant",
        "tier",
    ]
    can_delete = False
    show_change_link = True


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "item_type",
        "item_class",
        "slot",
        "item_kind",
        "minimum_level",
        "stale_status",
    ]
    search_fields = [
        "name",
        "wiki_title",
    ]
    inlines = [
        ItemEnchantmentInline,
    ]
    readonly_fields = [
        "wiki_revision_id",
        "wiki_revision_timestamp",
        "fetched_at",
        "updated_at",
    ]


@admin.register(Enchantment)
class EnchantmentAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "display_name",
    ]
    list_editable = [
        "display_name",
    ]
    search_fields = [
        "name",
        "display_name",
    ]

    def get_readonly_fields(self, request, obj=None):
        # The wiki name is immutable: editable only when the row is
        # first created, then locked. display_name is the override.
        if obj:
            return ["name"]
        return []


@admin.register(EnchantmentVariant)
class EnchantmentVariantAdmin(admin.ModelAdmin):
    list_display = [
        "enchantment",
        "value",
        "detail",
        "display_text",
        "magnitude",
        "item_count",
    ]
    search_fields = [
        "enchantment__name",
        "enchantment__display_name",
        "value",
        "detail",
        "display_text",
    ]
    list_filter = [
        "enchantment",
    ]
    autocomplete_fields = [
        "enchantment",
    ]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("enchantment")
            .annotate(
                _item_count=Count("items")
            )
        )

    @admin.display(
        description="Items",
        ordering="_item_count",
    )
    def item_count(self, obj):
        return obj._item_count


@admin.register(SyncState)
class SyncStateAdmin(admin.ModelAdmin):
    list_display = [
        "as_of",
        "loaded_at",
    ]
    readonly_fields = [
        "as_of",
        "loaded_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
