"""Parse raw item pages into the clean items format.

Reads wiki_snapshot/raw/*.json (untouched API response with html),
extracts metadata and enchantments, writes wiki_snapshot/items/*.json
(clean format without html).

Run after fetch: python manage.py parse_item_pages
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.enchantment_html import parse_item_page
from catalog.item_meta import extract_item_meta

RAW_DIR = Path(settings.BASE_DIR) / "wiki_snapshot" / "raw"
ITEM_DIR = Path(settings.BASE_DIR) / "wiki_snapshot" / "items"

TEMPLATE_RE = re.compile(
    r"\{\{([^|}\n]+)",
)


def parse_template_name(wikitext):
    """Extract the template name from wikitext like {{Named item|Weapon...}}."""
    if not wikitext:
        return ""
    match = TEMPLATE_RE.match(wikitext.strip())
    if match:
        return match.group(1).strip()
    return ""


class Command(BaseCommand):
    help = (
        "Parse raw item pages (wiki_snapshot/raw/*.json) into "
        "the clean parsed format (wiki_snapshot/items/*.json) "
        "with metadata and enchantments but no html."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--raw",
            metavar="DIR",
            default=None,
            help="Raw directory (default wiki_snapshot/raw).",
        )

        parser.add_argument(
            "--out",
            metavar="DIR",
            default=None,
            help="Output directory (default wiki_snapshot/items).",
        )

        parser.add_argument(
            "--all",
            action="store_true",
            help="Re-parse all files even if items/ exists.",
        )

    def handle(self, *args, **options):
        raw_dir = Path(options["raw"] or RAW_DIR)
        out_dir = Path(options["out"] or ITEM_DIR)
        reparse_all = options["all"]

        if not raw_dir.is_dir():
            self.stderr.write(
                self.style.ERROR(f"No such directory: {raw_dir}")
            )
            return

        out_dir.mkdir(parents=True, exist_ok=True)

        raw_files = sorted(raw_dir.glob("*.json"))

        if not raw_files:
            self.stderr.write(
                self.style.ERROR(f"No raw files in {raw_dir}.")
            )
            return

        self.stdout.write(
            f"Found {len(raw_files)} raw files in {raw_dir}."
        )

        parsed = 0
        skipped = 0
        errors = []

        for idx, path in enumerate(raw_files, 1):
            out_file = out_dir / path.name

            if not reparse_all and out_file.exists():
                skipped += 1
                continue

            record = json.loads(
                path.read_text(encoding="utf-8")
            )
            html = record.get("html")
            title = record.get("page_title", path.stem)

            if not html:
                errors.append(
                    f"{path.name}: no html field, skipping."
                )
                continue

            meta = extract_item_meta(html)
            enchant_result = parse_item_page(html)

            if enchant_result is None:
                errors.append(
                    f"{path.name}: no Enchantments cell found."
                )
                continue

            wikitext = record.get("wikitext", "")
            item_template = parse_template_name(wikitext)

            item_record = {
                "page_title": title,
                "page_id": record.get("page_id"),
                "revision_id": record.get("revision_id"),
                "revision_timestamp": record.get(
                    "revision_timestamp"
                ),
                "fetched_at": record.get("fetched_at"),
                "categories": record.get("categories", []),
                "wikitext": wikitext,
                "item_template": item_template,
                **meta,
                **enchant_result,
            }

            out_file.write_text(
                json.dumps(
                    item_record,
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            parsed += 1

            if parsed % 500 == 0:
                self.stdout.write(
                    f"  Parsed {parsed} items "
                    f"({idx}/{len(raw_files)} files processed)..."
                )

        if errors:
            self.stderr.write(f"\n{len(errors)} errors:")
            for err in errors[:20]:
                self.stderr.write(f"  {err}")

            if len(errors) > 20:
                self.stderr.write(
                    f"  ... and {len(errors) - 20} more."
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Parsed {parsed} items "
                f"({skipped} skipped, {len(errors)} errors)."
            )
        )
