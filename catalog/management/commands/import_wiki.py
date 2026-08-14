import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from playwright.sync_api import sync_playwright

from catalog.models import Item, Enhancement, ItemEnhancement


API_URL = "https://ddowiki.com/api.php"
WIKI_HOME = "https://ddowiki.com/"
ITEM_NAMESPACE = 500

CHECKPOINT_OVERLAP_SECONDS = 120
RECENT_CHANGES_LIMIT = 500
CONTENT_BATCH_SIZE = 50

STATE_FILE = Path(settings.BASE_DIR) / "wiki_sync_state.json"


class Command(BaseCommand):
    help = "Import new and changed DDO Wiki item pages."

    def add_arguments(self, parser):
        parser.add_argument(
            "--page",
            help="Import one specific Wiki item page.",
        )

        parser.add_argument(
            "--debug-page",
            help="Display raw wikitext for one Wiki page without changing the database.",
        )

        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum number of Named item pages to import.",
        )

        parser.add_argument(
            "--reset-sync",
            action="store_true",
            help="Reset the incremental sync checkpoint.",
        )

    def handle(self, *args, **options):
        page_title = options.get("page")
        debug_page = options.get("debug_page")
        limit = options.get("limit")
        reset_sync = options.get("reset_sync")

        existing_revisions = dict(
            Item.objects.values_list(
                "wiki_page_id",
                "wiki_revision_id",
            )
        )

        sync_end = datetime.now(timezone.utc)

        updates = []
        checkpoint = None

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False
            )

            context = browser.new_context()
            page = context.new_page()

            try:
                self.stdout.write(
                    "Opening DDO Wiki..."
                )

                page.goto(
                    WIKI_HOME,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                self.stdout.write(
                    "Waiting for WAF challenge..."
                )

                page.wait_for_timeout(10000)

                if debug_page:
                    self.debug_page(
                        page,
                        debug_page,
                    )
                    return

                if page_title:
                    updates = self.collect_single_page(
                        page,
                        page_title,
                        existing_revisions,
                    )

                else:
                    (
                        updates,
                        checkpoint,
                    ) = self.collect_changed_items(
                        page,
                        existing_revisions,
                        limit,
                        sync_end,
                        reset_sync,
                    )

            finally:
                browser.close()

        self.save_imported_items(
            updates
        )

        if checkpoint is not None:
            self.save_checkpoint(
                checkpoint
            )

    # ---------------------------------------------------------
    # DEBUG
    # ---------------------------------------------------------

    def debug_page(
        self,
        page,
        title,
    ):
        self.stdout.write("")
        self.stdout.write(
            "DEBUG MODE"
        )
        self.stdout.write(
            "No database changes will be made."
        )
        self.stdout.write(
            "No checkpoint changes will be made."
        )
        self.stdout.write("")
        self.stdout.write(
            f"Fetching: {title}"
        )
        self.stdout.write("")

        data = self.api_request(
            page,
            {
                "action": "query",
                "titles": title,
                "prop": "revisions",
                "rvprop": "ids|content",
                "rvslots": "main",
                "format": "json",
            },
        )

        pages = (
            data.get("query", {})
            .get("pages", {})
        )

        if not pages:
            self.stdout.write(
                self.style.ERROR(
                    "Page not found."
                )
            )
            return

        page_info = next(
            iter(pages.values())
        )

        if "missing" in page_info:
            self.stdout.write(
                self.style.ERROR(
                    "Page not found."
                )
            )
            return

        result = self.extract_page_result(
            page_info
        )

        if not result:
            self.stdout.write(
                self.style.ERROR(
                    "Could not extract page content."
                )
            )
            return

        self.stdout.write(
            f"Page ID: {result['page_id']}"
        )

        self.stdout.write(
            f"Revision: {result['revision_id']}"
        )

        self.stdout.write(
            f"Title: {result['title']}"
        )

        self.stdout.write("")
        self.stdout.write(
            "----- BEGIN WIKITEXT -----"
        )
        self.stdout.write(
            result["wikitext"]
        )
        self.stdout.write(
            "----- END WIKITEXT -----"
        )

    # ---------------------------------------------------------
    # INCREMENTAL SYNCHRONIZATION
    # ---------------------------------------------------------

    def collect_changed_items(
        self,
        page,
        existing_revisions,
        limit,
        sync_end,
        reset_sync,
    ):
        checkpoint = self.load_checkpoint()

        if reset_sync:
            checkpoint = None

        if checkpoint is None:
            self.stdout.write(
                "Initializing incremental Wiki sync."
            )

            self.stdout.write(
                "No historical Item crawl will be performed."
            )

            self.save_checkpoint(
                sync_end
            )

            return [], None

        start = checkpoint - timedelta(
            seconds=CHECKPOINT_OVERLAP_SECONDS
        )

        self.stdout.write(
            "Checking Wiki changes since "
            f"{start.isoformat()}..."
        )

        changed_pages = self.get_recent_changes(
            page,
            start,
            sync_end,
        )

        if not changed_pages:
            self.stdout.write(
                "No Item pages changed."
            )

            return [], sync_end

        unique_pages = {}

        for entry in changed_pages:
            page_id = entry["page_id"]

            existing_revision = (
                existing_revisions.get(
                    page_id
                )
            )

            if (
                existing_revision is not None
                and existing_revision
                == entry["revision_id"]
            ):
                continue

            unique_pages[page_id] = entry

        candidates = list(
            unique_pages.values()
        )

        candidates.sort(
            key=lambda entry: entry["timestamp"]
        )

        self.stdout.write(
            f"Changed Item pages found: "
            f"{len(candidates)}"
        )

        if not candidates:
            self.stdout.write(
                "No Item pages require importing."
            )

            return [], sync_end

        results = []

        #
        # We deliberately do NOT apply --limit to candidates.
        #
        # RecentChanges can contain page types that we are not
        # importing yet, such as Collectable. Therefore --limit
        # means "import this many Named items", not "examine this
        # many changed pages".
        #
        for batch_start in range(
            0,
            len(candidates),
            CONTENT_BATCH_SIZE,
        ):
            batch = candidates[
                batch_start:
                batch_start + CONTENT_BATCH_SIZE
            ]

            page_ids = "|".join(
                str(entry["page_id"])
                for entry in batch
            )

            data = self.api_request(
                page,
                {
                    "action": "query",
                    "pageids": page_ids,
                    "prop": "revisions",
                    "rvprop": "ids|content",
                    "rvslots": "main",
                    "format": "json",
                },
            )

            pages = (
                data.get("query", {})
                .get("pages", {})
            )

            for page_info in pages.values():
                result = self.extract_page_result(
                    page_info
                )

                if not result:
                    continue

                old_revision = (
                    existing_revisions.get(
                        result["page_id"]
                    )
                )

                if (
                    old_revision is not None
                    and old_revision
                    == result["revision_id"]
                ):
                    continue

                #
                # Only Named item pages are imported for now.
                # Other Item namespace page types are ignored.
                #
                item_data = self.parse_item(
                    result["title"],
                    result["wikitext"],
                )

                if not item_data:
                    continue

                self.stdout.write(
                    f"Importing {result['title']} "
                    f"({result['page_id']}, "
                    f"revision "
                    f"{result['revision_id']})..."
                )

                results.append(
                    {
                        "title": result["title"],
                        "page_id": result["page_id"],
                        "revision_id": (
                            result["revision_id"]
                        ),
                        "item_data": item_data,
                    }
                )

                #
                # --limit applies to successfully recognized
                # Named item pages.
                #
                if limit and len(results) >= limit:
                    break

            if limit and len(results) >= limit:
                break

            time.sleep(0.5)

        self.stdout.write(
            f"Items collected: {len(results)}"
        )

        #
        # If --limit stopped us before processing all candidates,
        # only advance the checkpoint through the candidates that
        # were actually examined.
        #
        if limit and len(results) >= limit:
            processed_candidates = min(
                len(candidates),
                batch_start + len(batch),
            )

            last_processed_timestamp = candidates[
                processed_candidates - 1
            ]["timestamp"]

            return (
                results,
                last_processed_timestamp,
            )

        #
        # All changed pages in the window were examined.
        #
        return results, sync_end

    def get_recent_changes(
        self,
        page,
        start,
        end,
    ):
        changes = []
        continuation = None

        while True:
            params = {
                "action": "query",
                "list": "recentchanges",
                "rcnamespace": str(
                    ITEM_NAMESPACE
                ),
                "rctype": "edit|new",
                "rctoponly": "1",
                "rcdir": "newer",
                "rcstart": self.format_timestamp(
                    start
                ),
                "rcend": self.format_timestamp(
                    end
                ),
                "rcprop": "title|ids|timestamp",
                "rclimit": str(
                    RECENT_CHANGES_LIMIT
                ),
                "format": "json",
            }

            if continuation:
                params.update(
                    continuation
                )

            data = self.api_request(
                page,
                params,
            )

            for change in data.get(
                "query",
                {},
            ).get(
                "recentchanges",
                [],
            ):
                page_id = change.get(
                    "pageid"
                )

                revision_id = change.get(
                    "revid"
                )

                title = change.get(
                    "title"
                )

                timestamp = change.get(
                    "timestamp"
                )

                if (
                    page_id is None
                    or revision_id is None
                    or not title
                    or not timestamp
                ):
                    continue

                try:
                    change_time = (
                        datetime.fromisoformat(
                            timestamp.replace(
                                "Z",
                                "+00:00",
                            )
                        )
                    )
                except ValueError:
                    continue

                changes.append(
                    {
                        "page_id": page_id,
                        "revision_id": revision_id,
                        "title": title,
                        "timestamp": change_time,
                    }
                )

            continuation = data.get(
                "continue"
            )

            if not continuation:
                break

            time.sleep(0.5)

        return changes

    # ---------------------------------------------------------
    # SINGLE PAGE
    # ---------------------------------------------------------

    def collect_single_page(
        self,
        page,
        title,
        existing_revisions,
    ):
        data = self.api_request(
            page,
            {
                "action": "query",
                "titles": title,
                "prop": "revisions",
                "rvprop": "ids|content",
                "rvslots": "main",
                "format": "json",
            },
        )

        pages = (
            data.get("query", {})
            .get("pages", {})
        )

        if not pages:
            self.stdout.write(
                self.style.ERROR(
                    f"Page not found: {title}"
                )
            )
            return []

        page_info = next(
            iter(pages.values())
        )

        if "missing" in page_info:
            self.stdout.write(
                self.style.ERROR(
                    f"Page not found: {title}"
                )
            )
            return []

        result = self.extract_page_result(
            page_info
        )

        if not result:
            return []

        old_revision = existing_revisions.get(
            result["page_id"]
        )

        if old_revision == result["revision_id"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{result['title']} is already current."
                )
            )
            return []

        item_data = self.parse_item(
            result["title"],
            result["wikitext"],
        )

        if not item_data:
            self.stdout.write(
                self.style.WARNING(
                    "Page is not a Named item page; "
                    "nothing imported."
                )
            )
            return []

        self.stdout.write(
            f"Importing {result['title']} "
            f"({result['page_id']}, "
            f"revision {result['revision_id']})..."
        )

        return [
            {
                "title": result["title"],
                "page_id": result["page_id"],
                "revision_id": result["revision_id"],
                "item_data": item_data,
            }
        ]

    # ---------------------------------------------------------
    # API
    # ---------------------------------------------------------

    def api_request(
        self,
        page,
        params,
    ):
        response = page.request.get(
            API_URL,
            params=params,
        )

        if response.status != 200:
            raise RuntimeError(
                f"HTTP {response.status}"
            )

        data = response.json()

        if "error" in data:
            raise RuntimeError(
                f"API ERROR: {data['error']}"
            )

        return data

    def extract_page_result(
        self,
        page_info,
    ):
        if "missing" in page_info:
            return None

        page_id = page_info.get(
            "pageid"
        )

        title = page_info.get(
            "title"
        )

        if page_id is None or not title:
            return None

        revisions = page_info.get(
            "revisions",
            [],
        )

        if not revisions:
            return None

        revision = revisions[0]

        revision_id = revision.get(
            "revid"
        )

        if revision_id is None:
            return None

        wikitext = ""

        slots = revision.get(
            "slots"
        )

        if slots:
            main = slots.get(
                "main",
                {},
            )

            wikitext = main.get(
                "*",
                "",
            )

            if not wikitext:
                wikitext = main.get(
                    "content",
                    "",
                )

        if not wikitext:
            wikitext = revision.get(
                "*",
                "",
            )

        if not wikitext:
            wikitext = revision.get(
                "content",
                "",
            )

        return {
            "page_id": page_id,
            "title": title,
            "revision_id": revision_id,
            "wikitext": wikitext,
        }

    # ---------------------------------------------------------
    # CHECKPOINT
    # ---------------------------------------------------------

    def load_checkpoint(self):
        if not STATE_FILE.exists():
            return None

        try:
            with STATE_FILE.open(
                "r",
                encoding="utf-8",
            ) as f:
                data = json.load(f)

            value = data.get(
                "last_successful_sync"
            )

            if not value:
                return None

            return datetime.fromisoformat(
                value
            )

        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
        ):
            return None

    def save_checkpoint(
        self,
        timestamp,
    ):
        temp_file = STATE_FILE.with_suffix(
            ".tmp"
        )

        data = {
            "last_successful_sync":
                timestamp.isoformat()
        }

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                data,
                f,
                indent=2,
            )

        temp_file.replace(
            STATE_FILE
        )

    def format_timestamp(
        self,
        timestamp,
    ):
        return timestamp.astimezone(
            timezone.utc
        ).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    # ---------------------------------------------------------
    # DATABASE
    # ---------------------------------------------------------

    def save_imported_items(
        self,
        imported_data,
    ):
        if not imported_data:
            self.stdout.write(
                self.style.SUCCESS(
                    "Nothing new or changed to import."
                )
            )
            return

        total = 0

        with transaction.atomic():
            for entry in imported_data:
                self.save_item(
                    entry["title"],
                    entry["page_id"],
                    entry["revision_id"],
                    entry["item_data"],
                )

                total += 1

        self.stdout.write("")

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete. "
                f"{total} items imported."
            )
        )

    def save_item(
        self,
        title,
        page_id,
        revision_id,
        item_data,
    ):
        item, created = (
            Item.objects.update_or_create(
                wiki_page_id=page_id,
                defaults={
                    "name": item_data["name"],
                    "wiki_title": title,
                    "wiki_revision_id": (
                        revision_id
                    ),
                    "item_type": (
                        item_data["item_type"]
                    ),
                    "minimum_level": (
                        item_data[
                            "minimum_level"
                        ]
                    ),
                },
            )
        )

        ItemEnhancement.objects.filter(
            item=item
        ).delete()

        for enhancement_data in (
            item_data["enhancements"]
        ):
            enhancement_name = (
                enhancement_data["name"]
            )

            enhancement = (
                Enhancement.objects
                .filter(
                    name__iexact=(
                        enhancement_name
                    )
                )
                .first()
            )

            if not enhancement:
                enhancement = (
                    Enhancement.objects.create(
                        name=enhancement_name
                    )
                )

            ItemEnhancement.objects.create(
                item=item,
                enhancement=enhancement,
                value=enhancement_data[
                    "value"
                ],
                raw_template=(
                    enhancement_data[
                        "raw_template"
                    ]
                ),
            )

    # ---------------------------------------------------------
    # PARSING
    # ---------------------------------------------------------

    def is_named_item(
        self,
        text,
    ):
        return bool(
            re.search(
                r"^\s*\{\{\s*Named item\b",
                text,
                re.MULTILINE | re.IGNORECASE,
            )
        )

    def parse_item(
        self,
        title,
        text,
    ):
        if not self.is_named_item(
            text
        ):
            return None

        name = self.get_parameter(
            text,
            "name",
        )

        if not name:
            return None

        item_type = self.get_parameter(
            text,
            "type",
        )

        item_type = self.clean_item_type(
            item_type
        )

        minlevel = self.get_parameter(
            text,
            "minlevel",
        )

        try:
            minimum_level = (
                int(minlevel)
                if minlevel
                else 0
            )
        except ValueError:
            minimum_level = 0

        enhancements = (
            self.parse_enhancements(
                text
            )
        )

        return {
            "name": name.strip(),
            "item_type": item_type,
            "minimum_level": minimum_level,
            "enhancements": enhancements,
        }

    def clean_item_type(
        self,
        value,
    ):
        if not value:
            return ""

        value = value.strip()

        value = re.sub(
            r"<!--.*?-->",
            "",
            value,
            flags=re.DOTALL,
        ).strip()

        return value

    def get_parameter(
        self,
        text,
        parameter,
    ):
        pattern = (
            rf"^\s*\|\s*"
            rf"{re.escape(parameter)}"
            rf"\s*=\s*(.*?)\s*$"
        )

        match = re.search(
            pattern,
            text,
            re.MULTILINE,
        )

        if not match:
            return None

        return match.group(1).strip()

    def parse_enhancements(
        self,
        text,
    ):
        enhancements_section = re.search(
            r"\|\s*enhancements\s*="
            r"\s*(.*?)(?=\n\s*\|\s*\w+\s*=|\n}})",
            text,
            re.DOTALL,
        )

        if not enhancements_section:
            return []

        section = (
            enhancements_section.group(1)
        )

        results = []

        for match in re.finditer(
            r"\{\{\s*([^{}|]+?)\s*"
            r"(?:\|\s*([^{}]*?))?\s*\}\}",
            section,
        ):
            name = match.group(1).strip()

            if not name:
                continue

            if name.lower() in {
                "div col",
                "div col end",
            }:
                continue

            raw_template = match.group(0)

            parameters = match.group(2)

            value = (
                self.extract_enhancement_value(
                    parameters
                )
            )

            normalized_name = (
                name.replace(
                    "_",
                    " ",
                )
            )

            normalized_name = re.sub(
                r"\s+",
                " ",
                normalized_name,
            ).strip()

            results.append(
                {
                    "name": normalized_name,
                    "value": value,
                    "raw_template": raw_template,
                }
            )

        return results

    def extract_enhancement_value(
        self,
        parameters,
    ):
        if not parameters:
            return ""

        parameters = parameters.strip()

        if not parameters:
            return ""

        parts = [
            part.strip()
            for part in parameters.split("|")
        ]

        if len(parts) == 1:
            return parts[0]

        last = parts[-1]

        if re.fullmatch(
            r"\+?\d+(?:\.\d+)?%?",
            last,
        ):
            return last

        first = parts[0]

        if first:
            return first

        return ""
