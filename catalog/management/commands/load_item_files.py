import json
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.enchantment_tree import walk_tree
from catalog.enchantment_values import parse_magnitude
from catalog.models import (
    Enchantment,
    EnchantmentVariant,
    Item,
    ItemEnchantment,
    SyncState,
)

ITEM_FILE_DIR = Path(settings.BASE_DIR) / "wiki_snapshot" / "items"


class Command(BaseCommand):
    help = (
        "Load parsed item-page files (wiki_snapshot/items/*.json) "
        "into the database: store the Enchantments tree on each Item "
        "and rebuild its searchable enchantment rows. "
        "Incremental by default — skips files whose revision_id "
        "already matches the database. Pass --reset for a full "
        "rebuild of the enchantment tables."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--items",
            metavar="DIR",
            default=None,
            help=(
                "Directory of parsed item files "
                "(default wiki_snapshot/items)."
            ),
        )

        parser.add_argument(
            "--reset",
            action="store_true",
            help=(
                "Clear all ItemEnchantment/EnchantmentVariant/"
                "Enchantment rows before loading. Bypasses the "
                "revision check so every file is re-imported."
            ),
        )

        parser.add_argument(
            "--prune",
            action="store_true",
            help=(
                "After loading, delete variants/enchantments no "
                "longer used by any item."
            ),
        )

    def handle(self, *args, **options):
        items_dir = Path(options["items"] or ITEM_FILE_DIR)

        if not items_dir.is_dir():
            self.stderr.write(
                self.style.ERROR(
                    f"No such directory: {items_dir}"
                )
            )

            return

        files = sorted(items_dir.glob("*.json"))

        if not files:
            self.stderr.write(
                self.style.ERROR(
                    f"No item files found in {items_dir}."
                )
            )

            return

        reset = options["reset"]

        # ── Phase 1: scan every file for (title, revision_id) ────
        self.stdout.write(
            f"Scanning {len(files)} item files in {items_dir}..."
        )

        file_index = {}  # title -> (path, revision_id, fetched_at_str)

        for path in files:
            record = json.loads(
                path.read_text(encoding="utf-8")
            )

            title = record.get("page_title") or path.stem
            file_index[title] = (
                path,
                record.get("revision_id"),
                record.get("fetched_at"),
            )

        self.stdout.write(
            f"Scanned {len(file_index)} item files."
        )

        # ── Phase 2: compare against the database ────────────────
        if reset:
            # Bypass comparison — load everything.
            to_load = set(file_index.keys())
            self.stdout.write(
                f"Reset mode: all {len(to_load)} items will be reloaded."
            )
        else:
            db_revisions = dict(
                Item.objects.filter(
                    wiki_title__in=file_index.keys()
                ).values_list("wiki_title", "wiki_revision_id")
            )

            to_load = set()

            for title, (_, file_rev, _) in file_index.items():
                db_rev = db_revisions.get(title)

                # New item (not in DB) → load.
                if db_rev is None:
                    to_load.add(title)
                    continue

                # Revision changed or file has no revision_id → load.
                if file_rev is None or db_rev != file_rev:
                    to_load.add(title)

            skipped = len(file_index) - len(to_load)

            if skipped:
                self.stdout.write(
                    f"Skipped {skipped} unchanged items "
                    f"({len(to_load)} to load)."
                )

        if not to_load:
            self.stdout.write(
                self.style.SUCCESS("Nothing to load.")
            )

            return

        # ── Phase 3: load only changed / new items ────────────────
        self.stdout.write(
            f"Loading {len(to_load)} items into database..."
        )

        if reset:
            self.stdout.write("Clearing enchantment tables...")
            ItemEnchantment.objects.all().delete()
            EnchantmentVariant.objects.all().delete()
            Enchantment.objects.all().delete()
            self.stdout.write("Enchantment tables cleared.")

        loaded = 0
        rows_created = 0
        created = 0
        newest_fetched = None

        with transaction.atomic():
            for idx, title in enumerate(sorted(to_load), 1):
                path, _, _ = file_index[title]
                record = json.loads(
                    path.read_text(encoding="utf-8")
                )

                fetched = None
                fetched_at = record.get("fetched_at")

                if fetched_at:
                    try:
                        fetched = datetime.fromisoformat(fetched_at)

                        if (
                            newest_fetched is None
                            or fetched > newest_fetched
                        ):
                            newest_fetched = fetched
                    except ValueError:
                        pass

                enchantments = record.get("enchantments")

                if not isinstance(enchantments, list):
                    self.stderr.write(
                        f"{path.name}: no enchantments tree."
                    )

                    continue

                item = Item.objects.filter(
                    wiki_title=title
                ).first()

                if item is None:
                    item = Item.objects.create(
                        name=title[5:] if title.startswith("Item:") else title,
                        wiki_title=title,
                        wiki_page_id=(
                            record.get("page_id") or 0
                        ),
                    )

                    created += 1

                ItemEnchantment.objects.filter(
                    item=item
                ).delete()

                if record.get("item_type"):
                    item.item_type = record["item_type"]

                if record.get("item_template"):
                    item.item_template = record["item_template"]

                if record.get("item_class"):
                    item.item_class = record["item_class"]

                if record.get("slot"):
                    item.slot = record["slot"]

                if record.get("item_kind"):
                    item.item_kind = record["item_kind"]

                if record.get("minimum_level") is not None:
                    item.minimum_level = record["minimum_level"]

                for field in (
                    "weapon_class",
                    "proficiency_class",
                    "armor_type",
                    "feat_requirement",
                    "material",
                ):
                    if record.get(field):
                        setattr(item, field, record[field])

                if fetched is not None:
                    item.fetched_at = fetched

                revision_timestamp = record.get(
                    "revision_timestamp"
                )

                if revision_timestamp:
                    try:
                        item.wiki_revision_timestamp = (
                            datetime.fromisoformat(
                                revision_timestamp.replace(
                                    "Z",
                                    "+00:00",
                                )
                            )
                        )
                    except (TypeError, ValueError):
                        pass

                if record.get("revision_id"):
                    item.wiki_revision_id = record["revision_id"]

                item.enchantment_tree = enchantments
                item.save()

                item_rows = 0

                for row in walk_tree(enchantments):
                    enchantment, _ = Enchantment.objects.get_or_create(
                        name=row.concept
                    )

                    variant, _ = EnchantmentVariant.objects.get_or_create(
                        enchantment=enchantment,
                        value=row.value,
                        detail=row.detail,
                        display_text=row.display_text,
                        defaults={
                            "magnitude": parse_magnitude(row.value),
                        },
                    )

                    _, row_created = ItemEnchantment.objects.get_or_create(
                        item=item,
                        variant=variant,
                        tier=row.tier,
                        defaults={
                            "possible": row.possible,
                        },
                    )

                    if row_created:
                        rows_created += 1

                    item_rows += 1

                loaded += 1

                if loaded % 500 == 0 or loaded == len(to_load):
                    self.stdout.write(
                        f"  Loaded {loaded}/{len(to_load)} items "
                        f"({rows_created} rows so far)..."
                    )

        if options["prune"]:
            self.stdout.write("Pruning orphaned enchantments...")
            EnchantmentVariant.objects.filter(
                items__isnull=True
            ).delete()

            Enchantment.objects.filter(
                variants__isnull=True
            ).delete()

        if newest_fetched is not None:
            state = SyncState.objects.first()

            if state is None:
                state = SyncState()

            state.as_of = newest_fetched
            state.loaded_at = datetime.now(timezone.utc)
            state.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {loaded} item files "
                f"({rows_created} rows, {created} items created)."
            )
        )
