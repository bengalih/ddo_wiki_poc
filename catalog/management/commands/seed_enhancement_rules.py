from django.core.management.base import BaseCommand

from catalog.enhancement_rules import (
    seed_default_rules,
)


class Command(BaseCommand):
    help = (
        "Seed (or refresh) the default enhancement "
        "rules. Run this after a database flush."
    )

    def handle(self, *args, **options):
        seed_default_rules()

        self.stdout.write(
            self.style.SUCCESS(
                "Enhancement rules seeded."
            )
        )
