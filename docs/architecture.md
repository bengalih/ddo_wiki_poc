# Architecture

## High-Level Overview

- DDO Item Search is a Django + SQLite application designed for cross searching attributes across inventory items via a web UI.
- Inventory includes (but not limited to): item class, item type, slot, minimum level) plus enchantment filters
- UI includes (but not limited to): bidirectionally-scoped dropdowns, upgrade-tier inclusion, minimum-value filters.
- Data is sourced from ddowiki.com, a MediaWiki using a pipeline of API calls and parsing of json/html into database fields.

## Pipeline for data loads

```
ddowiki.com (WAF-protected)
   │  WikiAPI.api_request()  ── solves AWS WAF token, paces requests
   ▼
fetch_item_pages  (--titles FILE | --from-db | --from-wiki | --page TITLE)
   │  action=parse                      → rendered HTML + wikitext + categories + revid
   │  action=query&prop=revisions       → revision_id + revision_timestamp
   │                                     (batched 50/request)
   ▼
wiki_snapshot/raw/<title>.json   (untouched API response: html + wikitext)
   │
   ▼
parse_item_pages
   │  enchantment_html / item_meta      → metadata + enchantments tree
   ▼
wiki_snapshot/items/<title>.json  (clean parsed format)
   │
   ▼
load_item_files
   │  enchantment_tree.walk_tree        → searchable rows
   ▼
db.sqlite3  →  Item / Enchantment / EnchantmentVariant / ItemEnchantment
   │
   ▼
Search UI  (views.py, services.py, templates, static JS)
```

** Only `fetch_item_pages` contacts the wiki. `parse_item_pages` and
`load_item_files` are fully offline. The UI is read-only.

---

## Components

### 1. Wiki API Client — `catalog/wiki_api.py`

The sole gateway to ddowiki. Handles AWS WAF token acquisition via headless
Chromium (Playwright), token caching (`wiki_waf_token.json`), request pacing
(1s + jitter), retry with backoff, and `maxlag` handling. Every wiki request
goes through `WikiAPI.api_request()` — no other code touches the network.

**Key parts:**
- `WikiAPI.solve_waf_token()` — launches headless Chromium, solves the JS
  challenge, extracts the `aws-waf-token` cookie.
- `WikiAPI.api_request()` — the single entry point for all API calls. Handles
  token refresh, pacing, retries.
- `WAF_TOKEN_REUSE_SECONDS = 600` — tokens are reused for ~10 minutes.
- `USER_AGENT = "DDOItemIndex/0.2 (personal project)"` — MediaWiki policy.

** See wiki_api.md for more info**

### 2. Fetcher — `catalog/management/commands/fetch_item_pages.py`

The only wiki-touching command. Fetches each item page's rendered HTML and
writes one raw JSON file per item to `wiki_snapshot/raw/`. Also requests
`prop=wikitext|categories` for template parsing and `prop=revisions` for
freshness metadata (batched 50/request via pageids to stay under URL length
limits).

**Key parts:**
- `Command.handle()` — orchestrates the fetch: reads titles, batches API
  calls, writes raw files.
- Incremental by default: existing files are skipped unless `--force`.

### 3. Parser — `catalog/management/commands/parse_item_pages.py`

Reads raw files from `wiki_snapshot/raw/`, extracts metadata and
enchantments, writes clean parsed files to `wiki_snapshot/items/`. No
database or network access.

**Key parts:**
- `parse_template_name(wikitext)` — extracts the template name from wikitext
  (e.g. `{{Named item|...}}` → `"Named item"`).
- Uses `catalog/item_meta.py` for infobox metadata extraction from HTML.
- Uses `catalog/enchantment_html.py` for enchantments tree extraction from HTML.
- Stores wikitext passthrough for future template-based metadata extraction.

### 4. Item Metadata Extractor — `catalog/item_meta.py`

Parses the item infobox `<table>` from the rendered HTML. Extracts
`item_type`, `item_class`, `slot`, `weapon_class`, `proficiency_class`,
`armor_type`, `feat_requirement`, `material`, `minimum_level`. Handles
cosmetic items, weapon type splitting, armor classification, and the
wiki's inconsistent label wording ("Type", "Item Type", "Weapon Type",
"Armor Type", "Shield Type").

**Key parts:**
- `extract_item_meta(page_html)` — main entry point; scans tables for the
  infobox.
- `_build_meta(raw, order)` — the classification logic that decides
  `item_type` and `item_class` from the raw label/value pairs.
- `classify_armor(feat_requirement, armor_type, material)` — resolves armor
  class from the wiki's own Feat Requirement row (which already accounts
  for material effects like mithral).
- `derive_slot(item_type, item_class)` — maps category/type to equipment slot.
- `split_weapon_type(value)` — splits "Bastard Sword / Slashing weapons".

### 5. Enchantment HTML Parser — `catalog/enchantment_html.py`

Parses the Enchantments cell's nested `<ul>/<li>` structure into a tree.
Preserves the exact wiki structure: containers, tier groups, alternatives,
upgrade arrows, tooltips, augment slots, Mythic items.

**Key parts:**
- `parse_item_page(page_html)` — main entry; finds the Enchantments or
  Enhancements cell and parses the nested list.
- `_parse_list(ul)` — recursive parser for the `<ul>/<li>` tree.
- `_clean_text(node)` — extracts visible text, stripping HTML tags.

### 6. Enchantment Tree Walker — `catalog/enchantment_tree.py`

Converts the nested tree (from `enchantment_html`) into flat searchable rows
for the database. Each row is a `(enchantment, value, tier, possible)` tuple.

**Key parts:**
- `walk_tree(tree)` — generator yielding `Row` dataclass instances.
- Handles effect leaves, plain containers, tier headers, upgrade containers,
  and alternative wrappers.
- `Row` dataclass: `concept`, `value`, `detail`, `display_text`, `tier`,
  `possible`.

### 7. Enchantment Values — `catalog/enchantment_values.py`

Parses magnitude values from enchantment text for the minimum-value filter
("+4 or better"). Handles numeric values, percentages, Roman numerals, and
ranges.

**Key parts:**
- `parse_magnitude(value)` — returns a float or None.
- Handles "Combustion 54", "+22%", "Shield Bashing +12", "Wizardry XII".

### 8. Loader — `catalog/management/commands/load_item_files.py`

Reads parsed item files from `wiki_snapshot/items/` and upserts into the
database. Stores the enchantment tree on each Item, walks it into searchable
rows, and updates freshness metadata. Three-phase incremental: scan files,
compare revision_ids against DB, load only changed/new items.

**Key parts:**
- Phase 1: scan all files for `(title, revision_id)`.
- Phase 2: compare against DB to find delta.
- Phase 3: load changed items (upsert Item, rebuild ItemEnchantment rows).
- `--reset` bypasses the revision check and clears all enchantment tables.
- `--prune` deletes orphaned enchantments/variants after loading.

### 9. Data Models — `catalog/models.py`

- `Item` — core item record. `wiki_title` is unique. `enchantment_tree`
  is a JSONField holding the nested tree (NOT a FK). Metadata fields:
  `item_type`, `item_class`, `item_template`, `slot`, `item_kind`,
  `weapon_class`, `proficiency_class`, `armor_type`, `feat_requirement`,
  `material`, `minimum_level`. Freshness fields: `fetched_at`,
  `wiki_revision_id`, `wiki_revision_timestamp`, `updated_at`.
- `Enchantment` — unique wiki name (immutable in admin). `display_name`
  is user-editable; collisions enforced via functional unique index on
  `COALESCE(NULLIF(display_name, ''), name)`.
- `EnchantmentVariant` — one row per distinct `(enchantment, value, detail,
  display_text)`. Shared by every item that uses it. `magnitude` powers the
  minimum-value filter.
- `ItemEnchantment` — item + variant FK, tier, `possible`. The
  `enchantment`/`value`/`detail`/`display_text`/`magnitude` properties
  proxy through the variant.
- `SyncState` — single row; `as_of` = newest `fetched_at`, shown in the UI.

### 10. Search Backend — `catalog/services.py` + `catalog/views.py`

`services.py` handles filter parsing and query building. `views.py` serves
the main search page (`item_search`) and the AJAX endpoint
(`enchantment_options`) that provides bidirectionally-scoped dropdown
options.

**Key parts:**
- `parse_base_filters(request)` — extracts name, category, type, level range,
  include_upgrades from query params.
- `apply_base_filters(items, base_filters)` — applies Django ORM filters.
- `enchantment_options(request)` — AJAX endpoint; for each filter row, applies
  every OTHER row's filter so each dropdown reflects the full current search.
- `item_search(request)` — main view; applies all filters, paginates,
  renders the template.

### 11. Frontend — `catalog/templates/` + `catalog/static/`

- `catalog/templates/catalog/item_search.html` — main search page template.
- `catalog/templates/catalog/_enchantment_tree.html` — nested enchantment
  display (driven by `enchantment_tree` JSONField).
- `catalog/static/catalog/js/item_search.js` — client-side behavior for
  the search form: AJAX dropdown updates, form submission, tier inclusion
  toggle.

---

## Architectural Gotchas

### Sourcing Data from the Wiki

Refer to wiki_api.md for more info on how to format API calls.

The wiki uses dynamic template expansion on page renders. This makes
determining things like the enchantments table and upgrades/crafting
details very complex with API queries. Therefore, parsing the raw HTML
allows us to source and present these in a manner identical to how the
Wiki renders it to users.

### Why raw files are separate from parsed files

The raw files (`wiki_snapshot/raw/`) store the untouched API response (HTML +
wikitext). The raw files also include some added metadata.

The parsed files (`wiki_snapshot/items/`) are the clean, extracted
format. This separation exists because:

- Raw files are the archive — if the parser logic changes, raw files can be
  re-parsed without re-fetching from the wiki.
- Parsed files are the working format — they're clean, small, and self-
  sufficient. Deleting raw files after parsing is safe.


### Why `Item.enchantment_tree` is a JSONField, not normalized tables

The enchantment tree is a nested structure (containers, tier groups,
alternatives, upgrade arrows) that mirrors the wiki's display. Normalizing
it into flat tables would lose the hierarchy needed for the wiki-faithful
nested display. The searchable rows (`ItemEnchantment`) are derived from
the tree via `walk_tree()`.

### Why enchantment dropdowns are bidirectionally scoped

When a user selects "Keen" in the first dropdown and "+3" in the second, the
second dropdown should still show all values available across the full
current search — not just values that co-occur with "+3". This is achieved by
applying every OTHER row's filter when computing options for each row,
producing a "what would be available if I changed this row" view.
