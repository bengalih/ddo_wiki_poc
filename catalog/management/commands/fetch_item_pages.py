import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from catalog.wiki_api import WikiAPI
from catalog.models import Item

# action=parse answers include the full rendered page HTML.
API_PARSE_BATCH = 1

# action=parse returns revid but no timestamp, so the latest-revision
# timestamps come from a batched action=query&prop=revisions call.
# Uses pageids (numeric, ~6 chars each) instead of titles (~40 chars
# each) to stay well under the wiki's 2048-char URL length limit.
API_REVISION_BATCH = 50

# Raw API response output directory. Each file stores the
# fetched metadata (page_title, page_id, revision_id, revision_timestamp,
# categories, html, wikitext, api_url) — no metadata extraction.
# Parsed items are produced by parse_item_pages.py.
RAW_FILE_DIR = Path(settings.BASE_DIR) / "wiki_snapshot" / "raw"

# The Item namespace on ddowiki (MediaWiki namespace 500). --from-wiki
# uses generator=embeddedin to find all pages that transclude
# {{Named_item}}, which returns only real item pages (~9,038)
# server-side — no wasted requests on non-items.
ITEM_NAMESPACE = 500
EMBEDDEDIN_LIMIT = 500

# Sentinel: no item file exists yet on disk for a title.
_NO_FILE = object()

# Debug/reference output location. --from-wiki --debug writes the
# enumerated title list and a run report here; nothing reads them back.
DEBUG_DIR = Path(settings.BASE_DIR) / "wiki_snapshot"
DEBUG_TITLES_FILE = "item_titles.txt"
DEBUG_REPORT_FILE = "fetch_report.json"


def _safe_filename(title):
    return re.sub(r'[<>:"/\\|?*]', "_", title)


class Command(BaseCommand):
    help = (
        "Fetch the full rendered HTML of item pages (action=parse) and "
        "write one raw JSON file per item under wiki_snapshot/raw/ "
        "with the untouched API response. Then run parse_item_pages "
        "to extract metadata and enchantments into wiki_snapshot/items/. "
        "Fetch does not touch the items/ directory."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--page",
            action="append",
            metavar="TITLE",
            help=(
                "Fetch a page, e.g. "
                "Item:Nightmare, the Fallen Moon. "
                "Repeatable."
            ),
        )

        parser.add_argument(
            "--from-db",
            action="store_true",
            help="Fetch every item currently in the database.",
        )

        parser.add_argument(
            "--from-wiki",
            action="store_true",
            help=(
                "Enumerate the Item namespace from the wiki and "
                "filter to pages that use the {{Named item}} infobox "
                "(only real item pages; cargo manifests, spirits, "
                "etc. excluded). Works with no title file and no DB. "
                "Incremental: each enumerated page's current revision "
                "id is compared against the stored revision_id in its "
                "item file, and only new or changed pages are fetched "
                "(a file with no revision id is treated as changed). "
                "--force re-fetches everything. Pass --debug to also "
                "write the title list and a run report for reference."
            ),
        )

        parser.add_argument(
            "--titles",
            metavar="FILE",
            default=None,
            help=(
                "Read page titles from a text file, one per line "
                "(order preserved; --limit takes the first N)."
            ),
        )

        parser.add_argument(
            "--debug",
            nargs="?",
            const=str(DEBUG_DIR),
            metavar="DIR",
            help=(
                "Write debug/reference artifacts into DIR (default "
                "wiki_snapshot): item_titles.txt (the enumerated "
                "Item-namespace title list; --from-wiki only) and "
                "fetch_report.json (counts plus failed/missing titles "
                "and new/removed titles vs the previous list). "
                "Reference only; nothing reads these back."
            ),
        )

        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Stop after this many pages (0 = no limit).",
        )

        parser.add_argument(
            "--out",
            metavar="DIR",
            default=None,
            help="Output directory (default wiki_snapshot/raw).",
        )

        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-fetch pages that already have an item file.",
        )

    def handle(self, *args, **options):
        out_dir = Path(
            options["out"] or RAW_FILE_DIR
        )

        out_dir.mkdir(parents=True, exist_ok=True)

        self.wiki_api = WikiAPI()
        self.wiki_api.stdout = self.stdout

        try:
            self._handle(options, out_dir)
        finally:
            self.wiki_api.close()

    def _handle(self, options, out_dir):

        self._debug = None

        if options["debug"]:
            self._debug_dir = Path(options["debug"])
            self._debug_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            self._debug = {
                "fetched": [],
                "skipped": [],
                "new": [],
                "changed": [],
                "failed": {},
                "missing": [],
            }

        limit = options["limit"]
        force = options["force"]
        fetched = 0
        skipped = 0
        titles_are_prefiltered = False
        page_map = {}

        # ── Phase 1: determine titles to fetch ────────────────
        self.stdout.write(
            self.style.NOTICE("Phase 1: resolving titles...")
        )

        if options["page"]:
            titles = options["page"]
            self.stdout.write(
                f"  --page mode: {len(titles)} title(s)."
            )
        elif options["from_wiki"]:
            self.stdout.write(
                "  --from-wiki: enumerating pages from wiki..."
            )
            page_map = self._enumerate_item_pages()
            all_titles = sorted(page_map)
            titles = []
            titles_are_prefiltered = True

            for title in all_titles:
                if force:
                    titles.append(title)
                    continue

                out_file = out_dir / (
                    _safe_filename(title) + ".json"
                )

                stored = self._stored_revision_id(out_file)

                if stored is _NO_FILE:
                    titles.append(title)

                    if self._debug is not None:
                        self._debug["new"].append(title)
                elif stored != page_map[title]["revision_id"]:
                    titles.append(title)

                    if self._debug is not None:
                        self._debug["changed"].append(title)
                else:
                    skipped += 1

                    if self._debug is not None:
                        self._debug["skipped"].append(title)

            self.stdout.write(
                f"  {skipped} skipped (already at "
                f"latest revision), "
                f"{len(titles)} to fetch."
            )

            if self._debug is not None:
                titles_file = (
                    self._debug_dir / DEBUG_TITLES_FILE
                )

                prev_titles = self._read_titles_file(
                    titles_file
                )

                self._write_titles_file(
                    all_titles,
                    titles_file,
                )

                self._debug["enumerated"] = all_titles
                self._debug["prev_titles"] = set(
                    prev_titles
                )

            if limit:
                titles = titles[: limit]
        elif options["titles"]:
            titles = [
                line.strip()
                for line in Path(options["titles"]).read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]

            if limit:
                titles = titles[: limit]
        elif options["from_db"]:
            titles = list(
                Item.objects.order_by("wiki_title").values_list(
                    "wiki_title",
                    flat=True,
                )
            )

            if limit:
                titles = titles[: limit]
        else:
            self.stdout.write(
                self.style.ERROR(
                    "Pass --page TITLE, --from-wiki, "
                    "--titles FILE, or --from-db."
                )
            )

            return

        if titles and not force:
            self.stdout.write(
                f"{len(titles)} pages to fetch."
            )

        timestamped_files = []

        # ── Phase 2: fetch each page ──────────────────────────
        self.stdout.write(
            self.style.NOTICE(
                f"Phase 2: fetching {len(titles)} pages..."
            )
        )

        for index, title in enumerate(titles, start=1):
            out_file = out_dir / (
                _safe_filename(title) + ".json"
            )

            if (
                not titles_are_prefiltered
                and out_file.exists()
                and not force
            ):
                skipped += 1

                if self._debug is not None:
                    self._debug["skipped"].append(title)

                continue

            parse_params = {
                "action": "parse",
                "page": title,
                "prop": "text|wikitext|categories|revid",
                "format": "json",
                "formatversion": "2",
            }

            try:
                data = self.wiki_api.api_request(
                    parse_params
                )
            except RuntimeError as exc:
                self.stderr.write(
                    f"{title}: fetch failed: {exc}"
                )

                if self._debug is not None:
                    self._debug["failed"][title] = str(exc)

                continue

            parsed = data.get("parse")

            if not parsed or "text" not in parsed:
                self.stderr.write(
                    f"{title}: API returned no page text."
                )

                if self._debug is not None:
                    self._debug["missing"].append(
                        {
                            "title": title,
                            "reason": "no page text",
                        }
                    )

                continue

            enum_info = page_map.get(title, {})

            record = {
                "page_title": title,
                "page_id": (
                    enum_info.get("page_id")
                    or parsed.get("pageid")
                ),
                "revision_id": (
                    parsed.get("revid")
                    or enum_info.get("revision_id")
                ),
                "fetched_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "categories": [
                    category.get("title")
                    for category in parsed.get("categories", [])
                    if category.get("title")
                ],
                "api_url": self.wiki_api.build_url(
                    parse_params
                ),
                "html": parsed["text"],
                "wikitext": parsed.get("wikitext", ""),
            }

            self._write_single_record(out_file, record)

            fetched += 1
            timestamped_files.append((title, out_file))

            if self._debug is not None:
                self._debug["fetched"].append(title)

            if index % 25 == 0 or index == len(titles):
                self.stdout.write(
                    f"  [{index}/{len(titles)}] "
                    f"fetched {fetched}, skipped {skipped}..."
                )

            if limit and fetched >= limit:
                break

        # ── Phase 3: attach revision timestamps ────────────────
        if timestamped_files:
            self.stdout.write(
                self.style.NOTICE(
                    f"Phase 3: querying revision timestamps "
                    f"for {len(timestamped_files)} pages..."
                )
            )
            self._attach_timestamps_to_files(
                timestamped_files, out_dir
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {fetched} pages fetched, "
                f"{skipped} skipped, "
                f"{len(timestamped_files)} timestamped."
            )
        )

        if self._debug is not None:
            self._write_debug_report()

    def _enumerate_item_pages(self):
        """Named-item pages, each with its latest revision id.

        generator=embeddedin asks the wiki which pages transclude
        {{Named_item}} in namespace 500, returning only real item
        pages (~9,038) server-side. prop=revisions attaches each
        page's current revision id in the same response. ~19 round
        trips for the full list — no client-side filtering needed.
        """

        self.stdout.write(
            "Enumerating Named item pages from the wiki..."
        )

        pages = {}
        continuation = None

        while True:
            params = {
                "action": "query",
                "generator": "embeddedin",
                "geititle": "Template:Named_item",
                "geinamespace": str(ITEM_NAMESPACE),
                "geilimit": str(EMBEDDEDIN_LIMIT),
                "prop": "revisions",
                "rvprop": "ids",
                "rvslots": "main",
                "format": "json",
                "formatversion": "2",
            }

            if continuation:
                params.update(continuation)

            data = self.wiki_api.api_request(params)

            for page in (
                data.get("query", {})
                .get("pages", [])
            ):
                title = page.get("title")

                if not title:
                    continue

                revisions = page.get("revisions") or []

                pages[title] = {
                    "page_id": page.get("pageid"),
                    "revision_id": (
                        revisions[0].get("revid")
                        if revisions
                        else None
                    ),
                }

            self.stdout.write(
                f"  {len(pages)} pages enumerated..."
            )

            continuation = data.get("continue")

            if not continuation:
                break

        return pages

    def _stored_revision_id(self, out_file):
        """The revision_id recorded in an item file, or _NO_FILE.

        A corrupt/unreadable file yields None (unknown), so it gets
        re-fetched rather than trusted. A file written before revision
        metadata existed also yields None and is refreshed once.
        """

        if not out_file.exists():
            return _NO_FILE

        try:
            with out_file.open("r", encoding="utf-8") as f:
                return json.load(f).get("revision_id")
        except (OSError, ValueError):
            return None

    def _read_titles_file(self, path):
        """Read a previously written titles file, if any."""

        if not path.exists():
            return []

        try:
            with path.open("r", encoding="utf-8") as f:
                return [
                    line.strip()
                    for line in f
                    if line.strip()
                ]
        except OSError:
            return []

    def _write_titles_file(self, titles, path):
        """Write the current Item-namespace title list for reference.

        Debug/reference only (--debug): nothing reads this back.
        """

        temp_file = path.with_name(path.name + ".tmp")

        temp_file.parent.mkdir(parents=True, exist_ok=True)

        with temp_file.open("w", encoding="utf-8") as f:
            f.write("\n".join(titles) + "\n")

        temp_file.replace(path)

    def _write_debug_report(self):
        """Summarize the run into fetch_report.json (reference only)."""

        enumerated = self._debug.get("enumerated")
        prev_titles = self._debug.get("prev_titles")

        new_titles = []
        removed_titles = []

        if enumerated is not None:
            current = set(enumerated)

            new_titles = sorted(
                current - prev_titles
            )

            removed_titles = sorted(
                prev_titles - current
            )

        report = {
            "as_of": datetime.now(
                timezone.utc
            ).isoformat(),
            "fetched": len(self._debug["fetched"]),
            "skipped": len(self._debug["skipped"]),
            "new": self._debug.get("new", []),
            "changed": self._debug.get("changed", []),
            "failed": self._debug["failed"],
            "missing": self._debug["missing"],
            "new_titles": new_titles,
            "removed_titles": removed_titles,
        }

        if enumerated is not None:
            report["enumerated"] = len(enumerated)

        path = self._debug_dir / DEBUG_REPORT_FILE
        temp_file = path.with_name(path.name + ".tmp")

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                report,
                f,
                indent=2,
                ensure_ascii=False,
            )

        temp_file.replace(path)

        self.stdout.write(
            self.style.SUCCESS(
                f"Debug report written to {path} "
                f"({len(new_titles)} new, "
                f"{len(removed_titles)} removed)."
            )
        )

    def _revision_timestamps(self, page_ids):
        """Map page titles to (revid, timestamp) from the wiki.

        action=parse with formatversion=2 returns neither revid nor
        timestamp; one batched query per API_REVISION_BATCH page IDs
        fills both in. Uses numeric pageids (short) instead of string
        titles (long) to stay under the wiki's 2048-char URL length
        limit.
        """

        revisions = {}
        total = len(page_ids)

        for start in range(
            0,
            len(page_ids),
            API_REVISION_BATCH,
        ):
            chunk = page_ids[start:start + API_REVISION_BATCH]

            batch_num = (start // API_REVISION_BATCH) + 1
            total_batches = (
                (total + API_REVISION_BATCH - 1)
                // API_REVISION_BATCH
            )

            self.stdout.write(
                f"  revisions batch {batch_num}/"
                f"{total_batches} "
                f"({len(chunk)} pageids)..."
            )
            chunk = page_ids[start:start + API_REVISION_BATCH]

            data = self.wiki_api.api_request(
                {
                    "action": "query",
                    "pageids": "|".join(
                        str(pid) for pid in chunk
                    ),
                    "prop": "revisions",
                    "rvprop": "ids|timestamp",
                    "rvslots": "main",
                    "format": "json",
                    "formatversion": "2",
                }
            )

            for page in (
                data.get("query", {})
                .get("pages", [])
            ):
                title = page.get("title")

                if not title:
                    continue

                revs = page.get("revisions") or []

                if revs:
                    revisions[title] = (
                        revs[0].get("revid"),
                        revs[0].get("timestamp"),
                    )

        return revisions

    def _attach_timestamps_to_files(self, files, out_dir):
        """Batch-query revision ids + timestamps, then update written files."""

        page_ids = []
        title_to_file = {}

        for title, out_file in files:
            try:
                with out_file.open(
                    "r", encoding="utf-8"
                ) as f:
                    record = json.load(f)
            except (OSError, ValueError):
                continue

            pid = record.get("page_id")

            if pid:
                page_ids.append(pid)
                title_to_file[title] = (out_file, record)

        if not page_ids:
            return

        try:
            revisions = self._revision_timestamps(page_ids)
        except RuntimeError as exc:
            self.stderr.write(
                f"revision timestamps unavailable: {exc}"
            )

            return

        updated = 0

        for title, (out_file, record) in (
            title_to_file.items()
        ):
            rev_info = revisions.get(title)

            if not rev_info:
                continue

            revid, timestamp = rev_info
            changed = False

            if revid and not record.get("revision_id"):
                record["revision_id"] = revid
                changed = True

            if timestamp and not record.get(
                "revision_timestamp"
            ):
                record["revision_timestamp"] = timestamp
                changed = True

            if changed:
                temp_file = out_file.with_suffix(".json.tmp")

                with temp_file.open(
                    "w",
                    encoding="utf-8",
                ) as f:
                    json.dump(
                        record,
                        f,
                        indent=2,
                        ensure_ascii=False,
                    )

                temp_file.replace(out_file)

                updated += 1

        self.stdout.write(
            f"  Updated {updated} files with "
            f"revision metadata."
        )

    def _write_single_record(self, out_file, record):
        """Write a single item record to disk immediately."""

        temp_file = out_file.with_suffix(".json.tmp")

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                record,
                f,
                indent=2,
                ensure_ascii=False,
            )

        temp_file.replace(out_file)
