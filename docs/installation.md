# Installation

Full setup instructions for getting DDO Item Search running from a fresh
machine.

---

## Prerequisites

- **Python 3.10+** (the project was generated with Django 5.2)
- **Git** (to clone the repository)

---

## 1. Clone and Create a Virtual Environment

```bash
git clone <repo-url> ddo_wiki_poc
cd ddo_wiki_poc

python -m venv venv
source venv/bin/activate        # Linux/macOS
# or: venv\Scripts\activate     # Windows
```

All subsequent commands assume the venv is active or you're calling
`venv/bin/python` / `venv\Scripts\python.exe` directly.

---

## 2. Install Dependencies

```bash
pip install Django requests playwright
python -m playwright install chromium
```

**Why Playwright?** The DDO wiki sits behind AWS WAF. Plain HTTP requests
return an HTTP 202 challenge page. Playwright launches a headless Chromium
browser to solve the JS challenge and obtain an `aws-waf-token` cookie,
which is reused for subsequent API requests.

---

## 3. Create the Database Schema

```bash
python manage.py migrate
```

This creates `db.sqlite3` with all tables (Item, Enchantment,
EnchantmentVariant, ItemEnchantment, SyncState, plus Django's built-in
tables).

---

## 4. Create an Admin User

```bash
python manage.py createsuperuser
```

Follow the prompts to set a username, email, and password.

---

## Steps 5-7 discuss the initial population of the database with data from ddowiki.com
   Refer to pipeline.md for more detailed information.

## 5. Fetch Item Pages from the Wiki

```bash
python manage.py fetch_item_pages --titles wiki_snapshot/item_titles.txt
```

This contacts ddowiki.com, solves the WAF challenge, and fetches each
item page's rendered HTML + wikitext + revision metadata. Output goes
to `wiki_snapshot/raw/` (~9,038 JSON files).

**This step takes a while** (10-20 minutes depending on network speed
and WAF token stability). It's incremental — re-running fills any
transient failures.

**Options:**
- `--force` — re-fetch all pages even if raw files exist.
- `--limit N` — stop after N pages (useful for testing).
- `--from-wiki` — enumerate pages from the wiki using
  `hastemplate:"Named item"` instead of `item_titles.txt`.
- `--page TITLE` — fetch a single page.

---

## 6. Parse Raw Files into Clean Format

```bash
python manage.py parse_item_pages
```

Reads `wiki_snapshot/raw/*.json`, extracts metadata and enchantments,
writes clean files to `wiki_snapshot/items/`. No network access.

**Options:**
- `--all` — re-parse all files even if output files exist.
- `--raw DIR` — custom raw directory.
- `--out DIR` — custom output directory.

---

## 7. Load into the Database

```bash
python manage.py load_item_files
```

Reads `wiki_snapshot/items/*.json` and upserts into the database.
Incremental by default — skips files whose `revision_id` already
matches the database.

**Options:**
- `--reset` — clear all enchantment tables first and re-import
  everything. Use after schema changes.
- `--prune` — delete orphaned enchantments/variants after loading.

---

## 8. Start the Development Server

```bash
python manage.py runserver 127.0.0.1:8090
```
**Admin:** `/admin/` — superuser `admin` / `admin123` (dev only).

---

## Rebuilding from Scratch

Assuming the only data populated was sourced from ddowiki, the database is disposable.
Rebuilding with cached files without touching the wiki:

```bash
# Stop the dev server first
rm db.sqlite3            # or: del db.sqlite3 on Windows
python manage.py migrate
python manage.py load_item_files
```

The item files in `wiki_snapshot/items/` survive the wipe.

---

## Refreshing from the Wiki

To get the latest data from ddowiki:

```bash
python manage.py fetch_item_pages --titles wiki_snapshot/item_titles.txt --force
python manage.py parse_item_pages --all
python manage.py load_item_files --reset
```

---

## Running Tests

```bash
python manage.py test catalog
```

Tests cover the HTML parser, tree walker, loader, item metadata extractor,
fetcher (including revision-query batching), search view/UI, and
enchantment value parsing.

---

## Environment Summary

| Component | Details |
|---|---|
| Python | 3.10+ |
| Django | 5.2 |
| Database | SQLite (`db.sqlite3`) |
| Dependencies | `django`, `requests`, `playwright` |
| Dev server | `127.0.0.1:8090` |
| Item files | `wiki_snapshot/raw/` (raw), `wiki_snapshot/items/` (parsed) |
| Title list | `wiki_snapshot/item_titles.txt` |
| WAF token cache | `wiki_waf_token.json` |
