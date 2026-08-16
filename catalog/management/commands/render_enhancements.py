import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.core.management.base import BaseCommand

from catalog import enhancement_render_store as render_store
from catalog.enhancement_renders import (
    extract_rendered,
    normalize_template_call,
    split_template_call,
)
from catalog.enhancement_rules import (
    expand_enhancement_template,
)
from catalog.management.commands.import_wiki import Command as ImportCommand

# URL-encoded query string length at which the wiki starts
# rejecting expandtemplates requests with HTTP 403. Observed
# boundary is ~2050 chars; keep a safety margin.
BATCH_URL_LIMIT = 1900

# Page-context magic words. A template that references them (usually
# inside {{#switch:{{FULLPAGENAMEE}}}}) expands differently depending
# on which page it is transcluded on, so it must be rendered once per
# page with the page title. Templates without any of these markers
# render identically everywhere and keep the cheap shared cache.
#
# NAMESPACE is deliberately NOT included: every page here lives in the
# Item namespace, so {{NAMESPACE}} is constant across pages and is only
# used for category membership ([[Category:... items]]), never for the
# enhancement's display text. Treating it as page-context made the
# detector flag ~600 templates and try ~8000 per-page renders.
_PAGE_CONTEXT_RE = re.compile(
    r"\{\{\s*"
    r"(?:(?:lc|uc|ucfirst|formatnum)\s*:\s*)?"
    r"(?:FULLPAGENAME|FULLPAGENAMEE|"
    r"PAGENAME|PAGENAMEE|"
    r"BASEPAGENAME|BASEPAGENAMEE|"
    r"SUBPAGENAME|SUBPAGENAMEE|"
    r"ROOTPAGENAME|ROOTPAGENAMEE)\s*\}\}",
    re.IGNORECASE,
)

TEMPLATE_SOURCE_BATCH = 50


class Command(BaseCommand):
    help = (
        "Scan a wiki snapshot, find every distinct enhancement "
        "template call, and render the ones the wiki has not "
        "been asked about yet. The display text, canonical name, "
        "magnitude and detail are cached in files under "
        "wiki_snapshot/renders/ (one JSON file per template call) "
        "and reused by every item import."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--snapshot",
            nargs="?",
            const=Path(settings.BASE_DIR) / "wiki_snapshot",
            metavar="DIR",
            help=(
                "Snapshot directory to scan. Defaults to "
                "BASE_DIR/wiki_snapshot."
            ),
        )

        parser.add_argument(
            "--clear",
            action="store_true",
            help="Drop all cached renders first.",
        )

        parser.add_argument(
            "--reparse",
            action="store_true",
            help=(
                "Re-extract name/display/value/detail from the "
                "stored raw_html for every cached render. Local "
                "only, no wiki calls."
            ),
        )

        parser.add_argument(
            "--render-all",
            action="store_true",
            help=(
                "Also ask the wiki about calls whose template "
                "rule already composes the display text locally "
                "(SpellPower, Spelllore, Enhancement bonus, "
                "HealingAmp). By default those calls are skipped: "
                "the rule output is ground truth for them."
            ),
        )

    def handle(self, *args, **options):
        snapshot_dir = Path(
            options["snapshot"]
            or (Path(settings.BASE_DIR) / "wiki_snapshot")
        )

        render_store.set_current_dir(snapshot_dir)

        if options["reparse"]:
            self.reparse_cached()
            return

        if options["clear"]:
            render_store.delete_all(snapshot_dir)
            self.stdout.write(
                "Cleared cached renders."
            )

        import_command = ImportCommand()
        import_command.stdout = self.stdout

        manifest = import_command.load_snapshot_manifest(
            snapshot_dir
        )

        if not manifest:
            self.stdout.write(
                self.style.ERROR(
                    f"No snapshot manifest found in "
                    f"{snapshot_dir}."
                )
            )

            return

        template_calls = set()
        call_pages = defaultdict(set)

        entries = sorted(
            manifest.items(),
            key=lambda pair: int(pair[0]),
        )

        self.stdout.write(
            f"Scanning {len(entries)} pages locally "
            f"for template calls..."
        )

        for index, (page_id, meta) in enumerate(
            entries,
            start=1,
        ):
            file_path = snapshot_dir / meta.get(
                "file",
                f"pages/{page_id}.json",
            )

            try:
                with file_path.open(
                    "r",
                    encoding="utf-8",
                ) as f:
                    data = json.load(f)
            except (
                OSError,
                ValueError,
                json.JSONDecodeError,
            ):
                continue

            item_data = import_command.parse_item(
                data["title"],
                data["wikitext"],
            )

            if not item_data:
                continue

            for enhancement in item_data["enhancements"]:
                raw = enhancement.get("raw_template")

                if raw:
                    template_calls.add(raw)
                    call_pages[
                        normalize_template_call(raw)
                    ].add(data["title"])

            if (
                index % 500 == 0
                or index == len(entries)
            ):
                self.stdout.write(
                    f"  scanned {index} / "
                    f"{len(entries)} pages "
                    f"({len(template_calls)} calls)..."
                )

                self.stdout.flush()

        calls = sorted(
            {
                normalize_template_call(call)
                for call in template_calls
            }
        )

        existing = {}
        existing_paged = {}

        for entry in render_store.entries(snapshot_dir):
            call = entry["template_call"]
            has_raw = bool(entry.get("raw_html"))
            title = entry.get("page_title")

            if title:
                existing_paged[(call, title)] = (
                    existing_paged.get((call, title), False)
                    or has_raw
                )
            else:
                existing[call] = (
                    existing.get(call, False)
                    or has_raw
                )

        self.stdout.write(
            f"{len(template_calls)} raw template calls "
            f"collapsed to {len(calls)} distinct."
        )

        # Templates that reference the page name ({{#switch:
        # {{FULLPAGENAMEE}}}}) expand differently depending on
        # which page they are transcluded on, so they are rendered
        # once per page with the page title. Everything else stays
        # on the cheap shared cache keyed by the call alone.
        #
        # Detect them from their own template sources. This must
        # cover EVERY call in the snapshot, not just the pending
        # ones: a stale titleless render makes a context template
        # look already-cached, and that fallback text is exactly
        # the bug we are here to prevent.
        # Map normalized name -> original spelling as written in the
        # snapshot. The normalized form is the cache key, but the
        # API must be queried with the original spelling: MediaWiki
        # titles are case-sensitive beyond the first letter, so
        # asking for the lowercased name misses the real page.
        name_map = {}
        for call in calls:
            raw_name = split_template_call(call)[0]
            name_map.setdefault(
                self._norm_template_name(raw_name),
                raw_name,
            )

        self.stdout.write(
            f"Fetching sources for {len(name_map)} "
            f"distinct templates to detect "
            f"page-context ones..."
        )

        self.stdout.flush()

        context_names = self._fetch_context_names(
            name_map,
            import_command,
        )

        self.stdout.write(
            f"{len(context_names)} page-context "
            f"templates detected."
        )

        context_calls = {
            call
            for call in calls
            if self._norm_template_name(
                split_template_call(call)[0]
            )
            in context_names
        }

        rendered = 0
        skipped = 0
        rule_composed = 0
        pending = []
        paged_pending = []

        for call in calls:
            if call in context_calls:
                # A titleless render for such a template is only
                # the fallback text ("* See the item description
                # page for details."); never let one leak into
                # lookups. Render the missing pages individually.
                render_store.delete(call, snapshot_dir)

                for title in sorted(call_pages.get(call, ())):
                    if not existing_paged.get(
                        (call, title),
                        False,
                    ):
                        paged_pending.append((call, title))

                continue

            if existing.get(call):
                skipped += 1
                continue

            if call not in existing:
                # A template rule may already produce the exact
                # display text for this call (e.g. Spelllore).
                # When every row it yields is fully composed, the
                # wiki is not needed; otherwise fall through and
                # ask the wiki.
                name, params = split_template_call(call)

                expansions = expand_enhancement_template(
                    name,
                    params,
                    call,
                )

                if (
                    not options["render_all"]
                    and expansions
                    and all(
                        expansion.get("display_text")
                        for expansion in expansions
                    )
                ):
                    self.stdout.write(
                        f"  = {call} "
                        f"(rule-composed, no wiki call)"
                    )

                    rule_composed += 1

                    continue

            pending.append(call)

        by_page = defaultdict(list)

        for call, title in paged_pending:
            by_page[title].append(call)

        for title_index, title in enumerate(
            sorted(by_page),
            start=1,
        ):
            for batch in self._batch_calls(
                by_page[title]
            ):
                rendered += self._render_batch(
                    batch,
                    import_command,
                    snapshot_dir,
                    title=title,
                )

            if (
                title_index % 20 == 0
                or title_index == len(by_page)
            ):
                self.stdout.write(
                    f"  page-context render "
                    f"{title_index} / {len(by_page)} "
                    f"pages -> {rendered} rendered"
                )

                self.stdout.flush()

        batches = self._batch_calls(pending)

        self.stdout.write(
            f"{len(pending)} page-independent calls "
            f"need the wiki "
            f"({len(batches)} batched requests); "
            f"{len(paged_pending)} page-context "
            f"(call, page) pairs render individually."
        )

        for batch_index, batch in enumerate(
            batches,
            start=1,
        ):
            rendered += self._render_batch(
                batch,
                import_command,
                snapshot_dir,
            )

            self.stdout.write(
                f"  batch {batch_index}/{len(batches)} "
                f"({len(batch)} calls) "
                f"-> {rendered} rendered"
            )

            self.stdout.flush()

        self.stdout.write(
            self.style.SUCCESS(
                f"Rendered {rendered} calls "
                f"({skipped} already cached, "
                f"{rule_composed} rule-composed, "
                f"{len(paged_pending)} page-context)."
            )
        )

    def _fetch_context_names(self, name_map, import_command):
        # Fetch the wikitext of each distinct template among the
        # snapshot's calls (batched, one API query per 50 templates)
        # and keep the ones whose source references a page-context
        # magic word. A missing/empty source is treated as
        # page-independent so offline tests and transient fetch
        # failures never force the per-page path.
        sources = self._fetch_template_sources(
            name_map,
            import_command,
        )

        return {
            name
            for name, source in sources.items()
            if _PAGE_CONTEXT_RE.search(source)
        }

    @staticmethod
    def _norm_template_name(name):
        name = re.sub(
            r"^template\s*:\s*",
            "",
            name.strip().replace("_", " "),
            flags=re.IGNORECASE,
        )

        return re.sub(
            r"\s+",
            " ",
            name,
        ).strip().lower()

    def _fetch_template_sources(self, name_map, import_command):
        sources = {}

        normed_names = sorted(name_map)

        for batch_index, start in enumerate(
            range(
                0,
                len(normed_names),
                TEMPLATE_SOURCE_BATCH,
            ),
            start=1,
        ):
            batch_normed = normed_names[
                start:start + TEMPLATE_SOURCE_BATCH
            ]

            if (
                batch_index % 10 == 0
                or batch_index == 1
            ):
                self.stdout.write(
                    f"  fetched template sources "
                    f"{start} / {len(normed_names)} "
                    f"(batch {batch_index})..."
                )

                self.stdout.flush()

            data = import_command.api_request(
                {
                    "action": "query",
                    "titles": "|".join(
                        f"Template:{name_map[name]}"
                        for name in batch_normed
                    ),
                    "prop": "revisions",
                    "rvprop": "content",
                    "rvslots": "main",
                    "format": "json",
                }
            )

            pages = (
                data.get("query", {})
                .get("pages", {})
                or {}
            )

            for page in pages.values():
                content = ""

                revisions = page.get(
                    "revisions",
                ) or []

                if revisions:
                    slots = (
                        revisions[0].get("slots")
                        or {}
                    )

                    main_slot = slots.get("main")

                    content = (
                        main_slot.get("*", "")
                        if main_slot
                        else ""
                    )

                sources[
                    self._norm_template_name(
                        page.get("title", "")
                    )
                ] = content

        return sources

    def _render_batch(
        self,
        batch,
        import_command,
        snapshot_dir,
        title=None,
    ):
        # If the wiki rejects the batch (403, usually the URL
        # getting too long), shrink it and retry until it fits.
        # Keeps rendering robust if the length limit ever changes.
        pending = list(batch)
        rendered = 0

        while pending:
            attempt = list(pending)

            try:
                text = " ".join(
                    f"@@K{i}@@ {call}"
                    for i, call in enumerate(attempt)
                )

                params = {
                    "action": "expandtemplates",
                    "text": text,
                    "prop": "wikitext",
                    "format": "json",
                }

                if title:
                    params["title"] = title

                data = import_command.api_request(params)

                output = (
                    data.get(
                        "expandtemplates",
                        {},
                    )
                    .get("wikitext", "")
                    or ""
                )
            except RuntimeError:
                if len(attempt) == 1:
                    self.stdout.write(
                        f"  ! {attempt[0]} "
                        f"(wiki rejected even solo; skipping)"
                    )

                    pending = []
                    continue

                self.stdout.write(
                    f"  batch rejected; shrinking "
                    f"{len(attempt)} -> "
                    f"{len(attempt) // 2} calls..."
                )

                pending = attempt[: len(attempt) // 2]
                continue

            # The wiki keeps the sentinel markers in the output,
            # so split on them to recover each call's render.
            chunks = re.split(
                r"@@K(\d+)@@",
                output,
            )

            results = {}

            for i in range(1, len(chunks), 2):
                results[int(chunks[i])] = chunks[i + 1]

            for i, call in enumerate(attempt):
                extracted = extract_rendered(
                    results.get(i, "")
                )

                if (
                    not extracted["name"]
                    or not extracted["display"]
                ):
                    self.stdout.write(
                        f"  ? {call} -> "
                        f"name={extracted['name']!r} "
                        f"display={extracted['display']!r}"
                    )

                entry = {
                    "template_call": call,
                    "canonical_name":
                        extracted["name"],
                    "display_text":
                        extracted["display"],
                    "value": extracted["value"],
                    "detail": extracted["detail"],
                    "raw_html": results.get(
                        i,
                        "",
                    ),
                }

                if title:
                    entry["page_title"] = title

                render_store.save(
                    entry,
                    snapshot_dir,
                )

                rendered += 1

            pending = []

        return rendered

    def _batch_calls(self, calls):
        batches = []
        current = []

        for call in calls:
            trial = current + [call]

            text = " ".join(
                f"@@K{i}@@ {c}"
                for i, c in enumerate(trial)
            )

            query = {
                "action": "expandtemplates",
                "text": text,
                "prop": "wikitext",
                "format": "json",
                "maxlag": "5",
            }

            if current and len(urlencode(query)) > BATCH_URL_LIMIT:
                batches.append(current)
                current = [call]
            else:
                current = trial

        if current:
            batches.append(current)

        return batches

    def reparse_cached(self, snapshot_dir=None):
        entries = render_store.entries(snapshot_dir)

        total = len(entries)
        updated = 0
        missing = 0

        for entry in sorted(
            entries,
            key=lambda item: item["template_call"],
        ):
            call = entry["template_call"]

            if not entry.get("raw_html"):
                missing += 1
                self.stdout.write(
                    f"  - {call} "
                    f"(no raw_html stored)"
                )
                continue

            extracted = extract_rendered(entry["raw_html"])

            if (
                extracted["name"] == entry.get("canonical_name")
                and extracted["display"] == entry.get("display_text")
                and extracted["value"] == entry.get("value")
                and extracted["detail"] == entry.get("detail")
            ):
                continue

            entry["canonical_name"] = extracted["name"]
            entry["display_text"] = extracted["display"]
            entry["value"] = extracted["value"]
            entry["detail"] = extracted["detail"]
            render_store.save(entry, snapshot_dir)

            updated += 1

            self.stdout.write(
                f"  ~ {call}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Reparsed {updated} of {total} cached "
                f"renders ({missing} missing raw_html)."
            )
        )
