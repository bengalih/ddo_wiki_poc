from django.db import models


class Item(models.Model):
    name = models.CharField(max_length=255)
    wiki_title = models.CharField(max_length=255, unique=True)
    wiki_page_id = models.PositiveIntegerField(unique=True)
    wiki_revision_id = models.PositiveIntegerField(null=True, blank=True)
    item_type = models.CharField(max_length=255, blank=True)
    minimum_level = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return self.name


class Enhancement(models.Model):
    name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name


class ItemEnhancement(models.Model):
    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name="enhancements",
    )
    enhancement = models.ForeignKey(
        Enhancement,
        on_delete=models.CASCADE,
        related_name="items",
    )
    value = models.CharField(
        max_length=255,
        blank=True,
    )
    raw_template = models.CharField(
        max_length=500,
        blank=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["item", "enhancement", "value"],
                name="unique_item_enhancement",
            )
        ]

    def __str__(self):
        if self.value:
            return f"{self.item} - {self.enhancement} ({self.value})"
        return f"{self.item} - {self.enhancement}"