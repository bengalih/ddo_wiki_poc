import re

from django.core.exceptions import ValidationError
from django.db import models


class Item(models.Model):
    name = models.CharField(max_length=255)
    wiki_title = models.CharField(max_length=255, unique=True)
    wiki_page_id = models.PositiveIntegerField(unique=True)
    wiki_revision_id = models.PositiveIntegerField(null=True, blank=True)
    wiki_revision_timestamp = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Wiki timestamp of the imported revision. Shows how "
            "fresh the content is versus the wiki; populated on "
            "new imports, null for rows imported before this "
            "field existed."
        ),
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this row was last written by the importer.",
    )
    item_type = models.CharField(max_length=255, blank=True)
    item_kind = models.CharField(max_length=255, blank=True)
    minimum_level = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return self.name

    @property
    def display_name(self):
        # The page title disambiguates variants ("Allegiance (level
        # 12)", "Allegiance (historic)", "Fellblade (falchion)")
        # while `name` stays the canonical item name.
        if self.wiki_title.startswith("Item:"):
            return self.wiki_title[5:]

        return self.wiki_title

    def ordered_enhancements(self):
        # Preserve the wiki's wikitext order: base lines and tier
        # groups stay interleaved (tiers appear right after the
        # "Upgradeable Item" CraftingEffects line), with consecutive
        # tier rows merged into one group per tier number.
        ordered = []
        current_tier_group = None

        for enhancement in self.enhancements.all():
            if enhancement.tier is None:
                current_tier_group = None

                ordered.append(
                    {
                        "type": "item",
                        "enhancement": enhancement,
                    }
                )

                continue

            if (
                current_tier_group is None
                or current_tier_group["tier"]
                != enhancement.tier
            ):
                current_tier_group = {
                    "type": "tier",
                    "tier": enhancement.tier,
                    "items": [],
                }

                ordered.append(
                    current_tier_group
                )

            current_tier_group["items"].append(
                enhancement
            )

        return ordered


class Enhancement(models.Model):
    name = models.CharField(
        max_length=255,
        unique=True,
        help_text=(
            "Canonical name from the wiki. Immutable after "
            "import; never edit this field."
        ),
    )
    display_name = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Editable override shown to users everywhere the "
            "enhancement appears (dropdowns, item pages). "
            "Leave blank to use the wiki name. Never changes "
            "the name field above."
        ),
    )

    def __str__(self):
        return self.display_name or self.name

    @property
    def label(self):
        return self.display_name or self.name

    class Meta:
        constraints = [
            models.UniqueConstraint(
                models.functions.Coalesce(
                    models.functions.NullIf(
                        "display_name",
                        models.Value(""),
                    ),
                    "name",
                ),
                name="unique_enhancement_effective_label",
            )
        ]

    def clean(self):
        if self.display_name:
            conflicts = (
                Enhancement.objects
                .filter(
                    models.Q(name__iexact=self.display_name)
                    | models.Q(
                        display_name__iexact=self.display_name
                    )
                )
                .exclude(pk=self.pk)
                .exists()
            )

            if conflicts:
                raise ValidationError(
                    {
                        "display_name": (
                            "This display name conflicts with an "
                            "existing enhancement name or display "
                            "name."
                        )
                    }
                )


class EnhancementRule(models.Model):
    SCOPE_CHOICES = [
        ("list", "Enhancement list"),
        ("item", "Item-wide"),
    ]

    template_name = models.CharField(max_length=255, unique=True)
    scope = models.CharField(
        max_length=10,
        choices=SCOPE_CHOICES,
        default="list",
    )
    handler = models.CharField(max_length=100)
    config = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "template_name"]

    def __str__(self):
        return self.template_name


class EnhancementVariant(models.Model):
    """One row per distinct rendered version of an enhancement.

    e.g. "Spell Power" with value "Combustion 54" appears on hundreds
    of items; it is stored here once and every item-enhancement row
    points to it. Editing a variant updates every item that uses it.
    """

    enhancement = models.ForeignKey(
        Enhancement,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    value = models.CharField(
        max_length=255,
        blank=True,
    )
    detail = models.CharField(
        max_length=255,
        blank=True,
    )
    display_text = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Verbatim display text from the wiki render cache."
        ),
    )
    magnitude = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Numeric magnitude parsed from the value for "
            "minimum-at-least searches, e.g. 22 from +22%; "
            "null when the value is not numeric."
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "enhancement",
                    "value",
                    "detail",
                    "display_text",
                ],
                name="unique_enhancement_variant",
            )
        ]
        ordering = ["enhancement__name", "value"]

    def __str__(self):
        label = self.enhancement.label

        if self.value:
            label = f"{label} {self.value}"

        return label


class ItemEnhancement(models.Model):
    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name="enhancements",
    )
    variant = models.ForeignKey(
        EnhancementVariant,
        on_delete=models.CASCADE,
        related_name="items",
    )
    tier = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Upgrade tier number when this enhancement only "
            "exists after an item upgrade; blank for base."
        ),
    )
    raw_template = models.CharField(
        max_length=500,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "item",
                    "variant",
                    "tier",
                ],
                name="unique_item_enhancement",
            )
        ]

    @property
    def enhancement(self):
        return self.variant.enhancement

    @property
    def value(self):
        return self.variant.value

    @value.setter
    def value(self, new_value):
        self.variant.value = new_value

    @property
    def detail(self):
        return self.variant.detail

    @detail.setter
    def detail(self, new_value):
        self.variant.detail = new_value

    @property
    def display_text(self):
        return self.variant.display_text

    @display_text.setter
    def display_text(self, new_value):
        self.variant.display_text = new_value

    @property
    def magnitude(self):
        return self.variant.magnitude

    @magnitude.setter
    def magnitude(self, new_value):
        self.variant.magnitude = new_value

    @property
    def display_name(self):
        override = self.enhancement.display_name.strip()
        name = override or self.enhancement.name.strip()

        # When no override is set, the wiki's verbatim rendered
        # text wins (e.g. "Fire Absorption +26%"). An override
        # replaces the name everywhere, so it composes with the
        # value instead of being shadowed by the wiki text.
        if not override and self.display_text:
            return self.display_text

        if not self.value:
            return name

        values = [
            part.strip()
            for part in self.value.split(",")
            if part.strip()
        ]

        formatted_values = []

        for value in values:
            if re.fullmatch(
                r"\+?\d+(?:\.\d+)?%?",
                value,
            ):
                value = value.lstrip("+")
                formatted_values.append(f"+{value}")
            else:
                formatted_values.append(value)

        if not formatted_values:
            return name

        label = (
            f"{name} "
            f"{', '.join(formatted_values)}"
        )

        if self.detail:
            label = f"{label} ({self.detail})"

        return label

    def __str__(self):
        return self.display_name


class SyncState(models.Model):
    """Single-row record of how current the database is with the wiki.

    `as_of` is the date of the snapshot data that was imported (the
    capture date, not the import date); `loaded_at` is when that data
    was actually written into this database.
    """

    as_of = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Date the imported data reflects (snapshot capture "
            "date), not the date it was imported."
        ),
    )
    loaded_at = models.DateTimeField(
        auto_now=True,
        help_text="When the database was last brought current.",
    )

    def __str__(self):
        if self.as_of:
            return (
                "Database current with DDO Wiki as of "
                f"{self.as_of:%Y-%m-%d %H:%M:%S}"
            )
        return "Database sync state (no date recorded)"