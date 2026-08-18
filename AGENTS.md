# AGENTS.md

## Quick facts
- Django + SQLite search app over a local mirror of the DDO wiki. App: `catalog`.
  Project root: `D:\ddo_wiki_poc`; settings module: `ddo_search.settings`.
- Running on a Windows system.  See ENVIRONMENT below
- Dev server: `python manage.py runserver 127.0.0.1:8090`.  USER:admin PASSWORD:Admin!

## Instruction gates
- No changes in Plan mode, only planning and conversation
- If user asks for an explanation/definition or includes a question,
  the response should only include an answer and not perform any changes.
- Never generalize when asked a directed question. Use data from local files or query the wiki.
- Never assume you cannot query the wiki.  You can using `catalog.wiki_api.WikiAPI`. It will handle WAF issues.
- Follow best practice for code architecutre, including breaking code out to modular files when required.
- Don't touch other running instances of server other than the dev server
- Never issue git commands unless specifically instructed by user
- If you are unclear on the user's intent when solving an issue, you should pause and ask the user.
- If the user states information about the project which is demonstrably false, then initially challenge the user with the actual data.
- Refer to additional documentation in /docs when required

## Environment
- Don't use linux commands on Windows.
- PowerShell 5.1 writes UTF-16LE when you redirect with `*>` or `>`. This
  causes mojibake in captured output. Always read captures as UTF-8 or decode
  before inspecting.

## Commands
- Full test suite: `python manage.py test catalog`.
- One test: `python manage.py test catalog.tests.<ClassName>.<test_method>`.
- Pipeline (3-step, the only wiki-touching command is the fetcher):
  - `fetch_item_pages --titles wiki_snapshot/item_titles.txt` — fetch rendered
    HTML + wikitext + revision metadata → `wiki_snapshot/raw/<title>.json`.
    Incremental (skips existing files); `--force` re-fetches; also `--from-wiki`
    (uses `hastemplate:"Named item"` to enumerate only real item pages from
    the wiki), `--from-db`, `--page T`, `--limit N`, `--out DIR`.
  - `parse_item_pages` — reads `wiki_snapshot/raw/*.json`, extracts metadata
    and enchantments, writes clean files to `wiki_snapshot/items/`. Fully
    offline. `--all` re-parses all; `--raw DIR`, `--out DIR`.
  - `load_item_files [--items DIR] [--reset] [--prune]` — upserts parsed
    item files into the DB. `--reset` clears enchantment tables first.

## Core architecture (must-know)
- DB is disposable due to fetched data in wiki_snapshot
- `wiki_snapshot/raw/` — raw API responses from fetch (HTML + wikitext +
  revision metadata + api_url). Source of truth for re-parsing.
- `wiki_snapshot/items/` — parsed output: one JSON per item page with
  `page_title`, `page_id`, `revision_id`, `revision_timestamp`, `fetched_at`,
  `categories`, `html`, `enchantments`, `item_class`, `item_template`,
  `item_type`, etc.
- `wiki_snapshot/item_titles.txt` — the full Item-namespace title list, so
  fetching doesn't depend on the DB.
- `db.sqlite3` — parsed, searchable rows only (disposable).
- `SyncState` (single DB row) — `as_of` = newest `fetched_at` across loaded
  files; shown in the UI as "Database current with DDO Wiki as of X". Written
  by `load_item_files`.
- Refer to /docs for more info when required

## Data model gotchas (models.py)
- `Item.enchantment_tree` is a JSONField holding the nested tree (NOT a FK) for
  the wiki-faithful nested display. Searchable rows come from walking it.
- `ItemEnchantment` stores only `item`, `variant`, `tier`, `possible`. Its
  `enchantment` / `value` / `detail` / `display_text` / `magnitude` are PROPERTIES
  proxying to `EnchantmentVariant`. Edit the variant — every item pointing at it updates.
- `Enchantment.display_name` is the user-editable label. Collision rule (recent, easy to break):
  display_name must not match — case-insensitive — any other enchantment's name OR display_name.
  Enforced in `Enchantment.clean()` AND by the functional unique index
  `unique_enchantment_effective_label` on `COALESCE(NULLIF(display_name, ''), name)` (migration 0018).
  Matching your own wiki name is allowed. Keep clean() and the constraint in sync.
- Dropdowns sort on the label (display_name or name), NOT the wiki name: `item_search` sorts by
  `label.casefold()`; the AJAX `enchantment_options` JSON carries a `labels` map that item_search.js sorts on.
- `Enchantment.name` is unique and immutable in the admin (read-only after creation).
- Item `name` is NOT unique; `wiki_title`/`wiki_page_id` are. A row may share its
  name with other rows (different ML versions). The UI Name column shows
  `item.display_name` = `wiki_title` minus the `Item:` prefix, so variants display
  as "Allegiance (level 12)" while `name` stays canonical for search/sort.
- Freshness fields (all populated by `load_item_files` when present in the item
  file): `Item.fetched_at` (when we pulled the page), `wiki_revision_id` /
  `wiki_revision_timestamp` (the wiki's latest revision as captured at fetch).
  `stale_status` (admin column) distinguishes Never fetched / No revision info / OK.
- `item_class` (Armor/Clothing/Cosmetic/Jewelry/Quiver/Shield/Weapon), `slot`,
  `item_kind`, `weapon_class`, `proficiency_class`, `armor_type`,
  `feat_requirement`, `material` are all derived at load from the infobox HTML
  (`catalog/item_meta.py`); `item_type` is the bare searchable type without
  a category prefix (e.g. "Dagger", "Light", "Belt", "Helm").

## Current state
- Run `python manage.py test catalog` to verify; `python manage.py migrate`
  to apply any pending migrations before `load_item_files`.
- `item_class` and `slot` are populated by `load_item_files` — re-run the
  loader after any schema change to fill them.
