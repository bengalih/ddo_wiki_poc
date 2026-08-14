import re

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import ItemEnhancement


class Command(BaseCommand):
    help = "Clean existing ItemEnhancement values from stored Wiki templates."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show changes without modifying the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        records = (
            ItemEnhancement.objects
            .select_related(
                "item",
                "enhancement",
            )
            .order_by(
                "item__name",
                "enhancement__name",
            )
        )

        changes = []

        for record in records:
            new_value = self.extract_value(
                record.raw_template
            )

            if new_value == record.value:
                continue

            changes.append(
                (
                    record,
                    record.value,
                    new_value,
                )
            )

        self.stdout.write(
            f"Enhancement records examined: "
            f"{records.count()}"
        )

        self.stdout.write(
            f"Records requiring cleanup: "
            f"{len(changes)}"
        )

        for record, old_value, new_value in changes:
            self.stdout.write(
                f"{record.item.name} | "
                f"{record.enhancement.name} | "
                f"'{old_value}' -> '{new_value}'"
            )

        if dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN: no database changes made."
                )
            )
            return

        with transaction.atomic():
            for record, old_value, new_value in changes:
                record.value = new_value
                record.save(
                    update_fields=["value"]
                )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Updated {len(changes)} enhancement records."
            )
        )

    def extract_value(self, raw_template):
        if not raw_template:
            return ""

        match = re.match(
            r"\{\{\s*[^|{}]+"
            r"(?:\|(.*?))?\s*\}\}$",
            raw_template,
            re.DOTALL,
        )

        if not match:
            return ""

        parameters = match.group(1)

        if not parameters:
            return ""

        parts = [
            part.strip()
            for part in parameters.split("|")
            if part.strip()
        ]

        positional = []

        for part in parts:
            if "=" in part:
                key, value = part.split(
                    "=",
                    1,
                )

                key = key.strip().lower()

                if key in {
                    "nocat",
                    "cat",
                    "category",
                }:
                    continue

                continue

            positional.append(part)

        if not positional:
            return ""

        numeric_values = [
            part
            for part in positional
            if re.fullmatch(
                r"\+?\d+(?:\.\d+)?%?",
                part,
            )
        ]

        descriptive_values = [
            part
            for part in positional
            if part not in numeric_values
        ]

        # Enhancement bonus|w|7
        #
        # "w" is a template implementation parameter;
        # the actual value is 7.
        if numeric_values:
            if len(numeric_values) == 1:
                return numeric_values[0]

            return ", ".join(
                numeric_values
            )

        return ", ".join(
            descriptive_values
        )