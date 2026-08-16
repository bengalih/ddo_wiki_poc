import json
import random
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count
from playwright.sync_api import sync_playwright

from catalog import enhancement_render_store as render_store
from catalog.enhancement_rules import (
    expand_enhancement_template,
    expand_item_rules,
)
from catalog.enhancement_values import parse_magnitude
from catalog.models import (
    Enhancement,
    EnhancementVariant,
    Item,
    ItemEnhancement,
    SyncState,
)

API_URL = "https://ddowiki.com/api.php"
WIKI_HOME = "https://ddowiki.com/"
ITEM_NAMESPACE = 500

# MediaWiki requires a descriptive user agent. Consider adding
# contact information per their bot policy.
USER_AGENT = "DDOItemIndex/0.2 (personal project)"

# Infobox templates recognized as importable item kinds. Extend this
# list to import new item types (e.g. "Collectable").
ITEM_INFOBLOXES = [
    "Named item",
]

# WAF token (AWS WAF challenge solved by a headless browser).
WAF_TOKEN_FILE = Path(settings.BASE_DIR) / "wiki_waf_token.json"
WAF_TOKEN_REUSE_SECONDS = 600

# Pacing and retry policy.
REQUEST_DELAY_SECONDS = 1.0
REQUEST_JITTER_SECONDS = 0.2
MAX_BACKOFF_SECONDS = 60
MAX_RETRIES = 5
MAXLAG = 5

CONTENT_BATCH_SIZE = 50
ALLPAGES_LIMIT = 500

CHECKPOINT_OVERLAP_SECONDS = 120
RECENT_CHANGES_LIMIT = 500
STATE_FILE = Path(settings.BASE_DIR) / "wiki_sync_state.json"

# Some item pages write the `name` parameter as a link template,
# e.g. `{{Item|Allegiance}}` (level-suffix pages) or
# `{{Item|Cavalry Plate|Epic Cavalry Plate}}` (epic-titled pages).
# Template:Item renders `[[Item:{1}|{2|{1}}]]`, so the display name
# is the second argument when present, else the first.
ITEM_NAME_CALL = re.compile(r"\{\{\s*Item\s*\|([^{}]*)\}\}")

# Other pages write the name as a raw wikilink, e.g. `[[Wraps of
# Endless Light]]`, `[[Blasting Chime|Epic Blasting Chime]]`, or
# `Epic [[Ring of the Stalker]]`. MediaWiki renders the text after
# `|` (or the target with its namespace stripped), and `Image:` /
# `File:` links embed media with no visible text.
WIKI_NAME_LINK = re.compile(
    r"\[\[\s*([^\[\]|]+?)(?:\s*\|\s*([^\[\]]*?))?\s*\]\]"
)

# Smart apostrophes in wiki text wouldn't match a straight-quote
# search, and page titles use the ASCII form.
CURVED_TO_ASCII = {
    "\u2019": "'",  # right single quotation mark
    "\u2018": "'",  # left single quotation mark
}


def resolve_item_name(value):
    def replace(match):
        args = [
            arg.strip()
            for arg in match.group(1).split("|")
        ]

        return args[1] if len(args) > 1 else args[0]

    def replace_link(match):
        target = match.group(1).strip()

        if target.lower().startswith(("image:", "file:")):
            return ""

        display = (
            match.group(2).strip()
            if match.group(2) is not None
            else None
        )

        if display:
            return display

        if ":" in target:
            return target.split(":", 1)[1]

        return target

    value = ITEM_NAME_CALL.sub(replace, value)
    value = WIKI_NAME_LINK.sub(replace_link, value)

    # Match MediaWiki rendering: HTML comments never appear in the
    # rendered page (editors use them for notes).
    value = re.sub(
        r"<!--.*?-->",
        "",
        value,
        flags=re.DOTALL,
    )

    for curved, straight in CURVED_TO_ASCII.items():
        value = value.replace(curved, straight)

    return re.sub(r"\s+", " ", value).strip()


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
            help="Maximum number of item pages to import.",
        )

        parser.add_argument(
            "--full",
            action="store_true",
            help="Force a full crawl of the entire Item namespace.",
        )

        parser.add_argument(
            "--reset-sync",
            action="store_true",
            help="Reset the incremental sync checkpoint and trigger a full crawl.",
        )

        parser.add_argument(
            "--snapshot",
            nargs="?",
            const=Path(settings.BASE_DIR) / "wiki_snapshot",
            metavar="DIR",
            help=(
                "Capture wiki pages to a local snapshot directory "
                "instead of the database."
            ),
        )

        parser.add_argument(
            "--load-snapshot",
            nargs="?",
            const=Path(settings.BASE_DIR) / "wiki_snapshot",
            metavar="DIR",
            help=(
                "Load items from a local snapshot into the database "
                "(no wiki access)."
            ),
        )

        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "With --load-snapshot, reparse every page in the "
                "snapshot regardless of revision."
            ),
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = requests.Session()
        self._waf_token = None
        self._waf_expires = 0
        self.unhandled_templates = Counter()

    def handle(self, *args, **options):
        page_title = options.get("page")
        debug_page = options.get("debug_page")
        limit = options.get("limit")
        full = options.get("full")
        reset_sync = options.get("reset_sync")
        snapshot_dir = options.get("snapshot")
        load_snapshot = options.get("load_snapshot")
        force = options.get("force")

        try:
            sync_end = datetime.now(timezone.utc)

            if snapshot_dir and load_snapshot:
                self.stderr.write(
                    "Use either --snapshot or --load-snapshot, "
                    "not both."
                )
                return

            if snapshot_dir:
                self.capture_snapshot(
                    Path(snapshot_dir),
                    limit,
                    full,
                    reset_sync,
                    sync_end,
                )
                return

            if load_snapshot:
                self.load_snapshot_to_db(
                    Path(load_snapshot),
                    force,
                )
                return

            existing_revisions = dict(
                Item.objects.values_list(
                    "wiki_page_id",
                    "wiki_revision_id",
                )
            )

            if debug_page:
                self.debug_page(debug_page)
                return

            if page_title:
                updates = self.collect_single_page(
                    page_title,
                    existing_revisions,
                )

                self.save_imported_items(updates)
                self.print_unhandled()
                return

            checkpoint = self.load_checkpoint()

            if reset_sync:
                checkpoint = None

            if full or checkpoint is None:
                updates, limit_reached = self.collect_all_items(
                    existing_revisions,
                    limit,
                )

                self.save_imported_items(updates)
                self.print_unhandled()

                if limit_reached:
                    self.stdout.write(
                        "Checkpoint NOT advanced (--limit reached). "
                        "Run again to finish the crawl."
                    )
                else:
                    self.save_checkpoint(sync_end)
                    self.record_sync_state(sync_end)
                    self.stdout.write(
                        "Checkpoint initialized; future runs "
                        "will be incremental."
                    )

                return

            updates, new_checkpoint = self.collect_changed_items(
                existing_revisions,
                limit,
                sync_end,
            )

            self.save_imported_items(updates)
            self.print_unhandled()

            if new_checkpoint is not None:
                self.save_checkpoint(new_checkpoint)
                self.record_sync_state(
                    new_checkpoint
                )

        finally:
            self.session.close()

    # ---------------------------------------------------------
    # WAF TOKEN
    # ---------------------------------------------------------

    def waf_token(self, force=False):
        now = time.time()

        if (
            not force
            and self._waf_token
            and self._waf_expires > now + WAF_TOKEN_REUSE_SECONDS
        ):
            return self._waf_token

        if not force:
            cached = self.load_waf_token()

            if (
                cached
                and cached["expires"] > now + WAF_TOKEN_REUSE_SECONDS
            ):
                self._waf_token = cached["token"]
                self._waf_expires = cached["expires"]
                self.set_session_token(cached["token"])

                return cached["token"]

        self.stdout.write(
            "Solving WAF challenge with a headless browser..."
        )

        token, expires = self.solve_waf_token()

        self._waf_token = token
        self._waf_expires = expires
        self.save_waf_token(token, expires)
        self.set_session_token(token)

        return token

    def solve_waf_token(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True
            )

            context = browser.new_context()
            page = context.new_page()

            try:
                page.goto(
                    WIKI_HOME,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

                token_cookie = None
                deadline = time.time() + 45

                while time.time() < deadline:
                    page.wait_for_timeout(2000)

                    try:
                        response = page.request.get(
                            API_URL,
                            params={
                                "action": "query",
                                "meta": "siteinfo",
                                "format": "json",
                            },
                        )
                    except Exception:
                        continue

                    if response.status != 200:
                        continue

                    cookies = context.cookies()

                    token_cookie = next(
                        (
                            cookie
                            for cookie in cookies
                            if cookie["name"] == "aws-waf-token"
                        ),
                        None,
                    )

                    if token_cookie:
                        break

                if not token_cookie:
                    raise RuntimeError(
                        "WAF challenge could not be solved "
                        "within the timeout."
                    )
            finally:
                browser.close()

        expires = token_cookie.get("expires", -1)

        if expires is None or expires < 0:
            expires = time.time() + 2 * 3600

        return token_cookie["value"], expires

    def load_waf_token(self):
        if not WAF_TOKEN_FILE.exists():
            return None

        try:
            with WAF_TOKEN_FILE.open(
                "r",
                encoding="utf-8",
            ) as f:
                data = json.load(f)

            if data.get("token") and data.get("expires"):
                return data

            return None

        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def save_waf_token(self, token, expires):
        temp_file = WAF_TOKEN_FILE.with_suffix(
            ".tmp"
        )

        data = {
            "token": token,
            "expires": expires,
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

        temp_file.replace(WAF_TOKEN_FILE)

    def set_session_token(self, token):
        self.session.cookies.set(
            "aws-waf-token",
            token,
            domain="ddowiki.com",
            path="/",
        )

    # ---------------------------------------------------------
    # API
    # ---------------------------------------------------------

    def api_request(self, params):
        query = dict(params)
        query.setdefault(
            "maxlag",
            str(MAXLAG),
        )

        for attempt in range(1, MAX_RETRIES + 1):
            token = self.waf_token()

            try:
                response = self.session.get(
                    API_URL,
                    params=query,
                    headers={
                        "User-Agent": USER_AGENT,
                    },
                    timeout=30,
                )
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES:
                    raise RuntimeError(
                        f"Request failed after {MAX_RETRIES} "
                        f"attempts: {exc}"
                    ) from exc

                self.stdout.write(
                    f"Request failed ({exc}); retrying..."
                )

                time.sleep(self.backoff(attempt))
                continue

            if response.status_code == 202:
                if attempt == 1:
                    self.stdout.write(
                        "WAF token expired; refreshing..."
                    )

                    self.waf_token(force=True)
                    time.sleep(self.backoff(1))
                    continue

                raise RuntimeError(
                    "WAF challenge could not be solved."
                )

            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    if attempt == MAX_RETRIES:
                        raise RuntimeError(
                            "API returned invalid JSON."
                        )

                    time.sleep(self.backoff(attempt))
                    continue

                error = data.get("error")

                if error:
                    if error.get("code") == "maxlag":
                        if attempt == MAX_RETRIES:
                            raise RuntimeError(
                                "Wiki lag did not clear."
                            )

                        self.stdout.write(
                            "Wiki is lagging; backing off..."
                        )

                        time.sleep(self.backoff(attempt))
                        continue

                    raise RuntimeError(
                        f"API ERROR: {error}"
                    )

                self.pace_sleep()

                return data

            if response.status_code in (
                429,
                500,
                502,
                503,
                504,
            ):
                retry_after = response.headers.get(
                    "Retry-After"
                )

                wait = (
                    float(retry_after)
                    if retry_after
                    else self.backoff(attempt)
                )

                self.stdout.write(
                    f"HTTP {response.status_code}; "
                    f"waiting {wait:.1f}s..."
                )

                if attempt == MAX_RETRIES:
                    raise RuntimeError(
                        f"HTTP {response.status_code} after "
                        f"{MAX_RETRIES} attempts."
                    )

                time.sleep(wait)
                continue

            raise RuntimeError(
                f"Unexpected HTTP {response.status_code} "
                "from the wiki."
            )

        raise RuntimeError("API retries exhausted.")

    def backoff(self, attempt):
        return (
            min(2 ** attempt, MAX_BACKOFF_SECONDS)
            + random.uniform(0, REQUEST_JITTER_SECONDS)
        )

    def pace_sleep(self):
        time.sleep(
            REQUEST_DELAY_SECONDS
            + random.uniform(0, REQUEST_JITTER_SECONDS)
        )

    # ---------------------------------------------------------
    # DEBUG
    # ---------------------------------------------------------

    def debug_page(self, title):
        self.stdout.write("")
        self.stdout.write("DEBUG MODE")
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
            {
                "action": "query",
                "titles": title,
                "prop": "revisions",
                "rvprop": "ids|content|timestamp",
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
    # FULL CRAWL
    # ---------------------------------------------------------

    def collect_all_items(
        self,
        existing_revisions,
        limit,
    ):
        pages = self.enumerate_all_pages()

        self.stdout.write(
            f"Item pages found: {len(pages)}"
        )

        results = []
        limit_reached = False

        for batch_start in range(
            0,
            len(pages),
            CONTENT_BATCH_SIZE,
        ):
            batch = pages[
                batch_start:
                batch_start + CONTENT_BATCH_SIZE
            ]

            page_ids = "|".join(
                str(page_id)
                for page_id, _ in batch
            )

            data = self.fetch_revisions(
                page_ids
            )

            pages_data = (
                data.get("query", {})
                .get("pages", {})
            )

            limit_reached = self.process_pages(
                pages_data,
                existing_revisions,
                results,
                limit,
            )

            processed = min(
                len(pages),
                batch_start + len(batch),
            )

            self.stdout.write(
                f"  {processed} / {len(pages)} "
                f"pages fetched..."
            )

            self.stdout.flush()

            if limit_reached:
                break

        self.stdout.write(
            f"Items collected: {len(results)}"
        )

        return results, limit_reached

    def enumerate_all_pages(self):
        self.stdout.write(
            "Enumerating the Item namespace..."
        )

        pages = []
        continuation = None

        while True:
            params = {
                "action": "query",
                "list": "allpages",
                "apnamespace": str(ITEM_NAMESPACE),
                "apfilterredir": "nonredirects",
                "aplimit": str(ALLPAGES_LIMIT),
                "format": "json",
            }

            if continuation:
                params["apcontinue"] = continuation

            data = self.api_request(params)

            for page in data.get(
                "query",
                {},
            ).get(
                "allpages",
                [],
            ):
                pages.append(
                    (
                        page["pageid"],
                        page["title"],
                    )
                )

            self.stdout.write(
                f"  {len(pages)} pages enumerated..."
            )

            self.stdout.flush()

            continuation = (
                data.get("continue", {})
                .get("apcontinue")
            )

            if not continuation:
                break

        return pages

    # ---------------------------------------------------------
    # INCREMENTAL SYNCHRONIZATION
    # ---------------------------------------------------------

    def collect_changed_items(
        self,
        existing_revisions,
        limit,
        sync_end,
    ):
        checkpoint = self.load_checkpoint()

        if checkpoint is None:
            return [], sync_end

        start = checkpoint - timedelta(
            seconds=CHECKPOINT_OVERLAP_SECONDS
        )

        self.stdout.write(
            "Checking Wiki changes since "
            f"{start.isoformat()}..."
        )

        changed_pages = self.get_recent_changes(
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
        limit_reached = False
        batch_start = 0

        #
        # We deliberately do NOT apply --limit to candidates.
        #
        # RecentChanges can contain page types that we are not
        # importing yet, such as Collectable. Therefore --limit
        # means "import this many items", not "examine this
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

            data = self.fetch_revisions(
                page_ids
            )

            pages_data = (
                data.get("query", {})
                .get("pages", {})
            )

            limit_reached = self.process_pages(
                pages_data,
                existing_revisions,
                results,
                limit,
            )

            processed = min(
                len(candidates),
                batch_start + len(batch),
            )

            self.stdout.write(
                f"  {processed} / {len(candidates)} "
                f"changed pages fetched..."
            )

            self.stdout.flush()

            if limit_reached:
                break

        self.stdout.write(
            f"Items collected: {len(results)}"
        )

        #
        # If --limit stopped us before processing all candidates,
        # only advance the checkpoint through the candidates that
        # were actually examined.
        #
        if limit_reached:
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

            data = self.api_request(params)

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

            self.stdout.write(
                f"  {len(changes)} changes "
                f"scanned..."
            )

            self.stdout.flush()

            continuation = data.get(
                "continue"
            )

            if not continuation:
                break

        return changes

    # ---------------------------------------------------------
    # SINGLE PAGE
    # ---------------------------------------------------------

    def collect_single_page(
        self,
        title,
        existing_revisions,
    ):
        data = self.api_request(
            {
                "action": "query",
                "titles": title,
                "prop": "revisions",
                "rvprop": "ids|content|timestamp",
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
                    "Page is not a recognized item page; "
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
                "revision_timestamp": (
                    result["revision_timestamp"]
                ),
                "item_data": item_data,
            }
        ]

    # ---------------------------------------------------------
    # REVISION BATCHES
    # ---------------------------------------------------------

    def fetch_revisions(self, page_ids):
        return self.api_request(
            {
                "action": "query",
                "pageids": page_ids,
                "prop": "revisions",
                "rvprop": "ids|content|timestamp",
                "rvslots": "main",
                "format": "json",
            },
        )

    def process_pages(
        self,
        pages_data,
        existing_revisions,
        results,
        limit,
    ):
        limit_reached = False

        for page_info in pages_data.values():
            result = self.extract_page_result(
                page_info
            )

            if not result:
                continue

            old_revision = existing_revisions.get(
                result["page_id"]
            )

            if (
                old_revision is not None
                and old_revision
                == result["revision_id"]
            ):
                continue

            item_data = self.parse_item(
                result["title"],
                result["wikitext"],
            )

            if not item_data:
                template = self.first_template_name(
                    result["wikitext"]
                )

                if template:
                    self.unhandled_templates[template] += 1

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
                    "revision_id": result["revision_id"],
                    "revision_timestamp": (
                        result["revision_timestamp"]
                    ),
                    "item_data": item_data,
                }
            )

            if limit and len(results) >= limit:
                limit_reached = True
                break

        return limit_reached

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

        revision_timestamp = self.parse_api_timestamp(
            revision.get("timestamp")
        )

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

        if not wikitext:
            return None

        return {
            "page_id": page_id,
            "title": title,
            "revision_id": revision_id,
            "revision_timestamp": (
                revision_timestamp
            ),
            "wikitext": wikitext,
        }

    @staticmethod
    def parse_api_timestamp(value):
        if not value:
            return None

        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            return None

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
    # LOCAL SNAPSHOT
    # ---------------------------------------------------------

    def capture_snapshot(
        self,
        snapshot_dir,
        limit,
        full,
        reset_sync,
        sync_end,
    ):
        manifest = self.load_snapshot_manifest(
            snapshot_dir
        )

        existing_revisions = {
            int(page_id): meta["revision_id"]
            for page_id, meta in manifest.items()
        }

        checkpoint = self.load_checkpoint()

        if reset_sync:
            checkpoint = None

        if full or checkpoint is None or not manifest:
            count, limit_reached = (
                self.capture_all_snapshot(
                    snapshot_dir,
                    manifest,
                    existing_revisions,
                    limit,
                )
            )

            if limit_reached:
                self.stdout.write(
                    "Checkpoint NOT advanced (--limit reached). "
                    "Run again to finish the capture."
                )
            else:
                self.save_checkpoint(sync_end)
                self.save_snapshot_as_of(
                    snapshot_dir,
                    sync_end,
                )
                self.stdout.write(
                    "Snapshot captured; future runs will be "
                    "incremental."
                )

            return

        count, limit_reached, new_checkpoint = (
            self.capture_changed_snapshot(
                snapshot_dir,
                manifest,
                existing_revisions,
                limit,
                sync_end,
            )
        )

        if new_checkpoint is not None:
            self.save_checkpoint(new_checkpoint)
            self.save_snapshot_as_of(
                snapshot_dir,
                new_checkpoint,
            )

    def capture_all_snapshot(
        self,
        snapshot_dir,
        manifest,
        existing_revisions,
        limit,
    ):
        self.stdout.write(
            "Capturing the Item namespace..."
        )

        count = 0
        limit_reached = False

        #
        # generator=allpages feeds the namespace listing straight into
        # prop=revisions, so each response carries the page id, title
        # and wikitext together. There is no separate enumeration phase
        # and no 12,509-entry list held in memory; with --limit we stop
        # requesting as soon as enough pages have been captured.
        #
        params = {
            "action": "query",
            "generator": "allpages",
            "gapnamespace": str(ITEM_NAMESPACE),
            "gapfilterredir": "nonredirects",
            "gaplimit": str(CONTENT_BATCH_SIZE),
            "prop": "revisions",
            "rvprop": "ids|content|timestamp",
            "rvslots": "main",
            "format": "json",
        }

        while True:
            data = self.api_request(params)

            for page_info in (
                data.get("query", {})
                .get("pages", {})
                .values()
            ):
                result = self.extract_page_result(
                    page_info
                )

                if not result:
                    continue

                old_revision = existing_revisions.get(
                    result["page_id"]
                )

                if (
                    old_revision is not None
                    and old_revision
                    == result["revision_id"]
                ):
                    continue

                self.write_snapshot_page(
                    snapshot_dir,
                    result,
                )

                manifest[str(
                    result["page_id"]
                )] = {
                    "title": result["title"],
                    "revision_id":
                        result["revision_id"],
                    "file": (
                        "pages/"
                        f"{result['page_id']}.json"
                    ),
                }

                count += 1

                if limit and count >= limit:
                    limit_reached = True
                    break

            self.save_snapshot_manifest(
                snapshot_dir,
                manifest,
            )

            self.stdout.write(
                f"  {count} pages captured..."
            )

            self.stdout.flush()

            if limit_reached:
                break

            continuation = data.get("continue")

            if not continuation:
                break

            params.update(continuation)

        self.stdout.write(
            f"Pages captured: {count}"
        )

        return count, limit_reached

    def capture_changed_snapshot(
        self,
        snapshot_dir,
        manifest,
        existing_revisions,
        limit,
        sync_end,
    ):
        checkpoint = self.load_checkpoint()

        if checkpoint is None:
            return 0, False, sync_end

        start = checkpoint - timedelta(
            seconds=CHECKPOINT_OVERLAP_SECONDS
        )

        self.stdout.write(
            "Checking Wiki changes since "
            f"{start.isoformat()}..."
        )

        changed_pages = self.get_recent_changes(
            start,
            sync_end,
        )

        if not changed_pages:
            self.stdout.write(
                "No Item pages changed."
            )

            return 0, False, sync_end

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
                "No Item pages require capturing."
            )

            return 0, False, sync_end

        count = 0
        limit_reached = False
        batch_start = 0
        batch = []

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

            data = self.fetch_revisions(page_ids)

            for page_info in (
                data.get("query", {})
                .get("pages", {})
                .values()
            ):
                result = self.extract_page_result(
                    page_info
                )

                if not result:
                    continue

                self.write_snapshot_page(
                    snapshot_dir,
                    result,
                )

                manifest[str(
                    result["page_id"]
                )] = {
                    "title": result["title"],
                    "revision_id":
                        result["revision_id"],
                    "file": (
                        "pages/"
                        f"{result['page_id']}.json"
                    ),
                }

                count += 1

                if limit and count >= limit:
                    limit_reached = True
                    break

            self.save_snapshot_manifest(
                snapshot_dir,
                manifest,
            )

            processed = min(
                len(candidates),
                batch_start + len(batch),
            )

            self.stdout.write(
                f"  {processed} / {len(candidates)} "
                f"changed pages captured..."
            )

            self.stdout.flush()

            if limit_reached:
                break

        self.stdout.write(
            f"Pages captured: {count}"
        )

        if limit_reached:
            processed_candidates = min(
                len(candidates),
                batch_start + len(batch),
            )

            last_processed_timestamp = (
                candidates[
                    processed_candidates - 1
                ]["timestamp"]
            )

            return (
                count,
                limit_reached,
                last_processed_timestamp,
            )

        return count, limit_reached, sync_end

    def write_snapshot_page(
        self,
        snapshot_dir,
        result,
    ):
        pages_dir = snapshot_dir / "pages"

        pages_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        file_path = pages_dir / (
            f"{result['page_id']}.json"
        )

        data = {
            "page_id": result["page_id"],
            "title": result["title"],
            "revision_id":
                result["revision_id"],
            "revision_timestamp": (
                result.get(
                    "revision_timestamp"
                ).isoformat()
                if result.get(
                    "revision_timestamp"
                )
                else None
            ),
            "wikitext": result["wikitext"],
        }

        temp_file = file_path.with_suffix(
            ".json.tmp"
        )

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                data,
                f,
                indent=2,
            )

        temp_file.replace(file_path)

    def load_snapshot_manifest(
        self,
        snapshot_dir,
    ):
        manifest_file = (
            snapshot_dir / "manifest.json"
        )

        if not manifest_file.exists():
            return {}

        try:
            with manifest_file.open(
                "r",
                encoding="utf-8",
            ) as f:
                return json.load(f)

        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
        ):
            return {}

    def save_snapshot_manifest(
        self,
        snapshot_dir,
        manifest,
    ):
        snapshot_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        manifest_file = (
            snapshot_dir / "manifest.json"
        )

        temp_file = (
            snapshot_dir / "manifest.json.tmp"
        )

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                manifest,
                f,
                indent=2,
            )

        temp_file.replace(manifest_file)

    def load_snapshot_as_of(
        self,
        snapshot_dir,
    ):
        meta_file = (
            snapshot_dir / "meta.json"
        )

        if not meta_file.exists():
            return None

        try:
            with meta_file.open(
                "r",
                encoding="utf-8",
            ) as f:
                data = json.load(f)

            value = data.get("as_of")

            if not value:
                return None

            return datetime.fromisoformat(value)

        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
        ):
            return None

    def save_snapshot_as_of(
        self,
        snapshot_dir,
        as_of,
    ):
        snapshot_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        meta_file = (
            snapshot_dir / "meta.json"
        )

        temp_file = (
            snapshot_dir / "meta.json.tmp"
        )

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                {
                    "as_of": as_of.isoformat()
                },
                f,
                indent=2,
            )

        temp_file.replace(meta_file)

    def load_snapshot_to_db(
        self,
        snapshot_dir,
        force,
    ):
        render_store.set_current_dir(snapshot_dir)

        manifest = self.load_snapshot_manifest(
            snapshot_dir
        )

        if not manifest:
            self.stdout.write(
                self.style.ERROR(
                    "No snapshot manifest found."
                )
            )

            return

        existing_revisions = {}

        if not force:
            existing_revisions = dict(
                Item.objects.values_list(
                    "wiki_page_id",
                    "wiki_revision_id",
                )
            )

        entries = sorted(
            manifest.items(),
            key=lambda pair: int(pair[0]),
        )

        count = 0
        skipped = 0
        unparseable = []

        with transaction.atomic():
            for processed, (page_id, meta) in enumerate(
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
                    self.stdout.write(
                        f"  Failed to read "
                        f"{file_path}; skipping."
                    )

                    continue

                revision_id = data.get(
                    "revision_id"
                )

                if (
                    not force
                    and existing_revisions.get(
                        int(page_id)
                    )
                    == revision_id
                ):
                    skipped += 1
                    continue

                revision_timestamp = (
                    self.parse_api_timestamp(
                        data.get(
                            "revision_timestamp"
                        )
                    )
                )

                item_data = self.parse_item(
                    data["title"],
                    data["wikitext"],
                )

                if not item_data:
                    unparseable.append(
                        data["title"]
                    )
                    continue

                self.save_item(
                    data["title"],
                    int(page_id),
                    revision_id,
                    item_data,
                    revision_timestamp=(
                        revision_timestamp
                    ),
                )

                count += 1

                if (
                    processed % 100 == 0
                    or processed == len(entries)
                ):
                    self.stdout.write(
                        f"  {processed} / "
                        f"{len(entries)} pages "
                        f"({count} items)..."
                    )

                    self.stdout.flush()

        self.stdout.write(
            self.style.SUCCESS(
                f"Snapshot loaded. {count} items "
                f"imported, {skipped} unchanged "
                f"skipped."
            )
        )

        # Renames and removals can leave Enhancement rows with no
        # item references (e.g. ":Adamantine" after the canonical
        # name is normalized to "Adamantine"). Prune them so the
        # admin table and dropdowns stay clean.
        orphaned = Enhancement.objects.annotate(
            item_count=Count("variants__items")
        ).filter(item_count=0)

        orphan_count = orphaned.count()

        if orphan_count:
            orphaned.delete()

            self.stdout.write(
                self.style.WARNING(
                    f"  Pruned {orphan_count} orphaned "
                    f"enhancement rows."
                )
            )

        if unparseable:
            self.stdout.write(
                f"{len(unparseable)} pages skipped "
                f"(no recognized item infobox):"
            )

            for title in sorted(unparseable):
                self.stdout.write(
                    f"  - {title}"
                )

        as_of = self.load_snapshot_as_of(
            snapshot_dir
        )

        if as_of is None:
            # Older snapshots predate the meta file; fall back to
            # the sync checkpoint (the last capture date).
            as_of = self.load_checkpoint()

        self.record_sync_state(as_of)

    def record_sync_state(
        self,
        as_of,
    ):
        state = SyncState.objects.first()

        if state is None:
            state = SyncState()

        state.as_of = as_of
        state.loaded_at = datetime.now(
            timezone.utc
        )
        state.save()

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
                    revision_timestamp=(
                        entry.get(
                            "revision_timestamp"
                        )
                    ),
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
        revision_timestamp=None,
    ):
        defaults = {
            "name": item_data["name"],
            "wiki_title": title,
            "wiki_revision_id": (
                revision_id
            ),
            "item_type": (
                item_data["item_type"]
            ),
            "item_kind": (
                item_data["item_kind"]
            ),
            "minimum_level": (
                item_data[
                    "minimum_level"
                ]
            ),
        }

        if revision_timestamp is not None:
            defaults["wiki_revision_timestamp"] = (
                revision_timestamp
            )

        item, created = (
            Item.objects.update_or_create(
                wiki_page_id=page_id,
                defaults=defaults,
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

            variant, _ = (
                EnhancementVariant.objects
                .get_or_create(
                    enhancement=enhancement,
                    value=enhancement_data[
                        "value"
                    ],
                    detail=(
                        enhancement_data.get(
                            "detail",
                            "",
                        )
                    ),
                    display_text=(
                        enhancement_data.get(
                            "display_text",
                            "",
                        )
                    ),
                    defaults={
                        "magnitude": parse_magnitude(
                            enhancement_data[
                                "value"
                            ]
                        ),
                    },
                )
            )

            ItemEnhancement.objects.create(
                item=item,
                variant=variant,
                tier=enhancement_data.get(
                    "tier",
                ),
                raw_template=(
                    enhancement_data[
                        "raw_template"
                    ]
                ),
            )

    def print_unhandled(self):
        if not self.unhandled_templates:
            return

        self.stdout.write("")
        self.stdout.write(
            "Unhandled infoboxes (not imported):"
        )

        for name, count in (
            self.unhandled_templates.most_common()
        ):
            self.stdout.write(
                f"  {name}: {count}"
            )

    # ---------------------------------------------------------
    # PARSING
    # ---------------------------------------------------------

    def detect_kind(self, text):
        for template in ITEM_INFOBLOXES:
            pattern = (
                r"^\s*\{\{\s*"
                + re.escape(template)
                + r"\b"
            )

            if re.search(
                pattern,
                text,
                re.MULTILINE | re.IGNORECASE,
            ):
                return template

        return None

    def first_template_name(self, text):
        match = re.search(
            r"^\s*\{\{\s*([^{}|]+)",
            text,
            re.MULTILINE | re.IGNORECASE,
        )

        if not match:
            return None

        return re.sub(
            r"\s+",
            " ",
            match.group(1),
        ).strip()

    def parse_item(
        self,
        title,
        text,
    ):
        kind = self.detect_kind(text)

        if kind is None:
            return None

        name = self.get_parameter(
            text,
            "name",
        )

        if not name:
            name = title

        name = re.sub(
            r"^Item\s*:\s*",
            "",
            name,
            flags=re.IGNORECASE,
        )

        name = resolve_item_name(name)

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

        ctx = {
            "item_type": item_type,
            "named_type_arg": (
                self.first_infobox_arg(
                    text,
                    kind,
                )
            ),
            "mythic": self.get_parameter(
                text,
                "mythic",
            ),
        }

        enhancements = (
            self.parse_enhancements(
                text,
                ctx,
                title,
            )
        )

        enhancements.extend(
            expand_item_rules(ctx)
        )

        return {
            "name": name,
            "item_type": item_type,
            "item_kind": kind,
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

    def first_infobox_arg(
        self,
        text,
        template,
    ):
        pattern = (
            r"^\s*\{\{\s*"
            + re.escape(template)
            + r"\b\s*\|\s*([^|{}]+)"
        )

        match = re.search(
            pattern,
            text,
            re.MULTILINE | re.IGNORECASE,
        )

        if not match:
            return None

        value = re.sub(
            r"\s+",
            " ",
            match.group(1),
        ).strip()

        if "=" in value:
            return None

        return value

    def parse_enhancements(
        self,
        text,
        ctx=None,
        title=None,
    ):
        enhancements_section = re.search(
            r"\|\s*enhancements\s*="
            r"\s*(.*?)(?=\n\s*\|\s*\w+\s*=|\n}})",
            text,
            re.DOTALL,
        )

        if not enhancements_section:
            return []

        section = enhancements_section.group(1)

        results = []

        # Upgrade tiers nest under base bullets:
        #
        #   * {{Enhancement bonus|i|7}}            base
        #   * {{CraftingEffects|Upgradeable Item}} base
        #   ** Tier 1:                             tier header
        #   *** Adds {{Spellpen|VI}}               tier 1
        #   ** Tier 2:                             tier header
        #   *** Adds {{Spelllore|Fire|XIII}}       tier 2
        #   * {{SpellPower|Combustion|150}}        base
        #
        # Walk the text between templates to track the active
        # tier so tier items are tagged (and base lines reset it).
        current_tier = None
        previous_end = 0

        for match in re.finditer(
            r"\{\{\s*([^{}|]+?)\s*"
            r"(?:\|\s*([^{}]*?))?\s*\}\}",
            section,
        ):
            gap = section[
                previous_end:match.start()
            ]

            for line in gap.splitlines():
                stripped = line.strip()

                tier_match = re.match(
                    r"\*\*\s*Tier\s*(\d+)",
                    stripped,
                    re.IGNORECASE,
                )

                if tier_match:
                    current_tier = int(
                        tier_match.group(1)
                    )
                elif (
                    stripped.startswith("* ")
                    or stripped == "*"
                ):
                    current_tier = None

            previous_end = match.end()

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

            normalized_name = name.replace(
                "_",
                " ",
            )

            normalized_name = re.sub(
                r"\s+",
                " ",
                normalized_name,
            ).strip()

            for expansion in (
                expand_enhancement_template(
                    normalized_name,
                    parameters,
                    raw_template,
                    title,
                )
            ):
                # Chain rows ({{VaultsOfTheArtificersUpgrade}})
                # already carry their own tier; the wikitext level
                # only applies to rows without one.
                expansion.setdefault(
                    "tier",
                    current_tier,
                )
                results.append(expansion)

        return results

