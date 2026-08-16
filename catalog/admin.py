from django.contrib import admin
from django.db.models import Count

from catalog.enhancement_rules import clear_rules_cache
from catalog.models import (
    Enhancement,
    EnhancementRule,
    EnhancementVariant,
    Item,
    ItemEnhancement,
    SyncState,
)


@admin.register(EnhancementRule)
class EnhancementRuleAdmin(admin.ModelAdmin):
    list_display = [
        "template_name",
        "scope",
        "handler",
        "enabled",
        "order",
    ]
    list_editable = [
        "enabled",
        "order",
    ]
    list_filter = [
        "scope",
        "enabled",
        "handler",
    ]
    search_fields = [
        "template_name",
    ]

    def save_model(
        self,
        request,
        obj,
        form,
        change,
    ):
        super().save_model(
            request,
            obj,
            form,
            change,
        )

        clear_rules_cache()

    def delete_model(
        self,
        request,
        obj,
    ):
        super().delete_model(request, obj)

        clear_rules_cache()


class ItemEnhancementInline(admin.TabularInline):
    # Shown on an Item's admin page: each item has a manageable
    # handful of enhancements, so inspecting them per item is fine.
    # The raw ItemEnhancement table (tens of thousands of rows) is
    # not registered as its own admin section.
    model = ItemEnhancement
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
        "item_kind",
        "minimum_level",
    ]
    search_fields = [
        "name",
        "wiki_title",
    ]
    inlines = [
        ItemEnhancementInline,
    ]
    readonly_fields = [
        "wiki_revision_id",
        "wiki_revision_timestamp",
        "updated_at",
    ]


@admin.register(Enhancement)
class EnhancementAdmin(admin.ModelAdmin):
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


@admin.register(EnhancementVariant)
class EnhancementVariantAdmin(admin.ModelAdmin):
    list_display = [
        "enhancement",
        "value",
        "detail",
        "display_text",
        "magnitude",
        "item_count",
    ]
    search_fields = [
        "enhancement__name",
        "enhancement__display_name",
        "value",
        "detail",
        "display_text",
    ]
    list_filter = [
        "enhancement",
    ]
    autocomplete_fields = [
        "enhancement",
    ]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("enhancement")
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
