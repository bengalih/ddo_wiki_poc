# Pipeline Documentation

The DDO wiki import pipeline has three stages: **Fetch** (contacts the wiki),
**Parse** (extracts metadata offline), and **Load** (populates the database).
Each stage is a separate management command that reads from and writes to
specific directories.

```
Fetch → wiki_snapshot/raw/<title>.json    (untouched API response)
Parse → wiki_snapshot/items/<title>.json  (clean parsed format)
Load  → db.sqlite3                        (searchable database)
```

---

## Stage 1: Fetch — `fetch_item_pages`

Fetches each item page's rendered HTML and writes one raw JSON file per item.
This is the **only command that contacts the wiki**.

### Basic Usage

```bash
python manage.py fetch_item_pages --titles wiki_snapshot/item_titles.txt
```

### Input Sources (mutually exclusive)

| Argument | Description |
|---|---|
| `--titles FILE` | Read titles from a text file, one per line. Order is preserved; `--limit` takes the first N. `wiki_snapshot/item_titles.txt` holds the full Item-namespace list. |
| `--from-db` | Fetch every item currently in the database. Useful for refreshing existing items. |
| `--from-wiki` | Enumerate real item pages from the wiki using `hastemplate:"Named item"`. Returns only pages that actually transclude the item template. |
| `--page TITLE` | Fetch one specific page. Repeatable for multiple pages. |

### Output Control

| Argument | Description |
|---|---|
| `--out DIR` | Output directory. Default: `wiki_snapshot/raw/` |
| `--limit N` | Stop after N pages. |
| `--force` | Re-fetch pages that already have a raw file. Default behavior is incremental (skip existing). |

### What Each Raw File Contains

Each `wiki_snapshot/raw/<title>.json` is the untouched API response:

| Field | Source |
|---|---|
| `page_title` | Wiki page title (e.g. `Item:+1 Dagger`) |
| `page_id` | Wiki page ID |
| `revision_id` | Wiki revision ID at fetch time |
| `revision_timestamp` | Wiki revision timestamp (from batched `prop=revisions`) |
| `fetched_at` | When we pulled the page (UTC ISO) |
| `categories` | Wiki categories for the page |
| `html` | Full rendered page HTML |
| `wikitext` | Page wikitext (for template-based metadata extraction) |

### How It Works

1. Reads titles from the input source.
2. For each title, calls `action=parse` to get the rendered HTML, wikitext,
   and categories.
3. Batches `action=query&prop=revisions` calls (50 titles per request via
   pageids) to get revision timestamps.
4. Writes one raw JSON file per item.
5. Skips existing files unless `--force` is used.

### WAF Token Handling

The wiki is behind AWS WAF. `fetch_item_pages` uses `WikiAPI` from
`catalog/wiki_api.py` which:
- Launches headless Chromium via Playwright to solve the JS challenge.
- Caches the token in `wiki_waf_token.json` (~10 minutes reuse).
- Retries on HTTP 202 with backoff.
- Paces requests at 1s + 0.2s jitter.

A WAF token refresh at the start of a run is normal.

---

## Stage 2: Parse — `parse_item_pages`

Reads raw files from `wiki_snapshot/raw/`, extracts metadata and
enchantments, writes clean parsed files to `wiki_snapshot/items/`. **No
database or network access.**

### Basic Usage

```bash
python manage.py parse_item_pages
```

### Arguments

| Argument | Description |
|---|---|
| `--raw DIR` | Input directory. Default: `wiki_snapshot/raw/` |
| `--out DIR` | Output directory. Default: `wiki_snapshot/items/` |
| `--all` | Re-parse all files even if the output file already exists. Without this flag, existing output files are skipped. |

### What Each Parsed File Contains

Each `wiki_snapshot/items/<title>.json` is the clean, extracted format:

| Field | Description |
|---|---|
| `page_title` | Wiki page title |
| `page_id` | Wiki page ID |
| `revision_id` | Wiki revision ID |
| `revision_timestamp` | Wiki revision timestamp |
| `fetched_at` | When we fetched the page |
| `categories` | Wiki categories |
| `wikitext` | Page wikitext (passthrough) |
| `item_template` | Template name parsed from wikitext (e.g. `"Named item"`) |
| `item_type` | Bare searchable type (e.g. `"Bastard Sword"`, `"Light"`, `"Bracers"`) |
| `item_class` | Template class (e.g. `"Weapon"`, `"Armor"`, `"Jewelry"`) |
| `slot` | Equipment slot (e.g. `"Main Hand"`, `"Armor"`, `"Wrist"`) |
| `item_kind` | Item kind (e.g. `"Weapon"`, `"Armor"`, `"Shield"`, `"Cosmetic"`) |
| `weapon_class` | Weapon class (e.g. `"Slashing weapons"`) |
| `proficiency_class` | Proficiency (e.g. `"Exotic Weapon Proficiency"`) |
| `armor_type` | Armor type (e.g. `"Chainmail"`, `"Docent"`) |
| `feat_requirement` | Feat requirement (e.g. `"Light Armor Proficiency"`) |
| `material` | Material (e.g. `"Mithral"`, `"Adamantine"`) |
| `minimum_level` | Minimum level (integer) |
| `enchantments` | Nested enchantment tree (from HTML) |

### How It Works

1. Reads each raw file.
2. Extracts metadata from HTML via `catalog/item_meta.py`.
3. Extracts enchantments tree from HTML via `catalog/enchantment_html.py`.
4. Parses template name from wikitext via `parse_template_name()`.
5. Writes the clean parsed file.

---

## Stage 3: Load — `load_item_files`

Reads parsed item files and upserts into the database. **No wiki access.**

### Basic Usage

```bash
python manage.py load_item_files
```

### Arguments

| Argument | Description |
|---|---|
| `--items DIR` | Input directory. Default: `wiki_snapshot/items/` |
| `--reset` | Clear all ItemEnchantment/EnchantmentVariant/Enchantment rows before loading. Bypasses the revision check so every file is re-imported. |
| `--prune` | After loading, delete variants/enchantments no longer used by any item. |

### How It Works (3-Phase Incremental)

1. **Phase 1 — Scan**: Read every item file for `(title, revision_id)`.
2. **Phase 2 — Compare**: Compare against the database. Skip files whose
   `revision_id` already matches the DB. New items and changed revisions
   are marked for loading.
3. **Phase 3 — Load**: For each item to load:
   - Upsert `Item` by `wiki_title`.
   - Store metadata fields (`item_type`, `item_class`, `item_template`,
     `slot`, etc.).
   - Store freshness fields (`fetched_at`, `wiki_revision_id`,
     `wiki_revision_timestamp`).
   - Store the enchantment tree in `Item.enchantment_tree` (JSONField).
   - Walk the tree via `enchantment_tree.walk_tree()` into searchable
     `ItemEnchantment` rows.
4. Updates `SyncState.as_of` to the newest `fetched_at`.

### What Gets Stored in the Database

| Table | Contents |
|---|---|
| `Item` | Item metadata + `enchantment_tree` JSONField |
| `Enchantment` | Unique enchantment names (e.g. "Keen", "Improved Proof Against Evil") |
| `EnchantmentVariant` | Distinct `(enchantment, value, detail, display_text)` tuples, shared across items |
| `ItemEnchantment` | Links items to variants with tier and `possible` flag |
| `SyncState` | Single row; `as_of` = newest `fetched_at` |

---

## Common Workflows

### Initial Data Population (from scratch)

```bash
# 1. Fetch all item pages from the wiki (~9,038 pages)
python manage.py fetch_item_pages --titles wiki_snapshot/item_titles.txt

# 2. Parse raw files into clean format
python manage.py parse_item_pages

# 3. Load into database
python manage.py load_item_files
```

### Refresh from Wiki (re-fetch + re-parse + reload)

```bash
# Re-fetch all pages (incremental; use --force to re-fetch everything)
python manage.py fetch_item_pages --titles wiki_snapshot/item_titles.txt --force

# Re-parse all raw files
python manage.py parse_item_pages --all

# Reload into database
python manage.py load_item_files --reset
```

### Rebuild Database (no wiki access needed)

```bash
# Stop the dev server
rm db.sqlite3            # or: del db.sqlite3 on Windows
python manage.py migrate
python manage.py load_item_files
```

### Fetch New Items Only

```bash
# Add new titles to item_titles.txt, then:
python manage.py fetch_item_pages --titles wiki_snapshot/item_titles.txt
python manage.py parse_item_pages
python manage.py load_item_files
```

### Fetch a Single Page

```bash
python manage.py fetch_item_pages --page "Item:+1 Dagger"
python manage.py parse_item_pages
python manage.py load_item_files
```

---

## Management Commands Reference

| Command | Wiki? | Description |
|---|---|---|
| `fetch_item_pages` | Yes | Fetch rendered HTML + revision metadata → `wiki_snapshot/raw/` |
| `parse_item_pages` | No | Parse raw files → `wiki_snapshot/items/` (metadata + enchantments) |
| `load_item_files` | No | Load parsed files → database |

---

## File Layout

```
wiki_snapshot/
  item_titles.txt          # Full Item-namespace title list
  raw/                     # Untouched API responses (Stage 1 output)
    Item_+1 Dagger.json
    Item_Allegiance (level 12).json
    ...
  items/                   # Clean parsed format (Stage 2 output)
    Item_+1 Dagger.json
    Item_Allegiance (level 12).json
    ...

wiki_waf_token.json        # Cached WAF token (auto-managed)
db.sqlite3                 # Database (disposable, derived from items/)
```
