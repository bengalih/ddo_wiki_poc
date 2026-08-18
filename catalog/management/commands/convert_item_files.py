"""One-time migration: split current items/*.json into raw/ + new items/.

Reads every file in wiki_snapshot/items/ (current format with html),
writes wiki_snapshot/raw/ (api response with html) and rewrites
wiki_snapshot/items/ (parsed format: metadata + enchantments, no html).

Run once: python manage.py convert_item_files
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.enchantment_html import parse_item_page
from catalog.item_meta import extract_item_meta


RAW_DIR = Path(settings.BASE_DIR) / "wiki_snapshot" / "raw"
ITEM_DIR = Path(settings.BASE_DIR) / "wiki_snapshot" / "items"


class Command(BaseCommand):
    help = (
        "One-time migration: convert current items/*.json "
        "(with html) into raw/*.json + new items/*.json "
        "(parsed format, no html)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be written without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        files = sorted(ITEM_DIR.glob("*.json"))

        if not files:
            self.stderr.write(
                self.style.ERROR(f"No files in {ITEM_DIR}.")
            )
            return

        RAW_DIR.mkdir(parents=True, exist_ok=True)

        written_raw = 0
        written_items = 0
        errors = []

        for path in files:
            record = json.loads(path.read_text(encoding="utf-8"))
            html = record.get("html")
            title = record.get("page_title", path.stem)

            if not html:
                errors.append(
                    f"{path.name}: no html field, skipping."
                )
                continue

            # ── raw file: everything the API returned ────────
            raw_record = {
                "page_title": title,
                "page_id": record.get("page_id"),
                "revision_id": record.get("revision_id"),
                "revision_timestamp": record.get(
                    "revision_timestamp"
                ),
                "fetched_at": record.get("fetched_at"),
                "categories": record.get("categories", []),
                "html": html,
            }

            raw_path = RAW_DIR / path.name

            # ── parse metadata from the HTML ────────────────
            meta = extract_item_meta(html)

            # ── parse enchantments tree ─────────────────────
            parsed = parse_item_page(html)

            if parsed is None:
                errors.append(
                    f"{path.name}: no Enchantments cell found."
                )
                continue

            # ── new items file ──────────────────────────────
            item_record = {
                "page_title": title,
                "page_id": record.get("page_id"),
                "revision_id": record.get("revision_id"),
                "revision_timestamp": record.get(
                    "revision_timestamp"
                ),
                "fetched_at": record.get("fetched_at"),
                "categories": record.get("categories", []),
                **meta,
                **parsed,
            }

            if dry_run:
                self.stdout.write(
                    f"  {path.name}: "
                    f"{meta.get('item_type', '?')} / "
                    f"{meta.get('item_class', '?')}"
                )
            else:
                raw_path.write_text(
                    json.dumps(
                        raw_record,
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

                path.write_text(
                    json.dumps(
                        item_record,
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            written_raw += 1
            written_items += 1

        if errors:
            self.stderr.write(f"\n{len(errors)} errors:")
            for err in errors:
                self.stderr.write(f"  {err}")

        verb = "Would write" if dry_run else "Wrote"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {written_raw} raw files, "
                f"{written_items} item files."
            )
        )
