# DDO Item Search (Enchantments)

A Django + SQLite web app that mirrors item pages from the DDO wiki
(`ddowiki.com`) and provides a search UI with base filters (name, item class,
item type, slot, minimum level) plus enchantment filters (bidirectionally-
scoped dropdowns, upgrade-tier inclusion, minimum-value filters).

Data is populated from each item's rendered HTML wiki page.
Once fetched, all info is stored in local files, rendering the database disposable in this regard.
Changes to the wiki can be fetched and applied incrementally.

## Quick Start

```bash
python -m venv venv
source venv/bin/activate        # Linux/macOS
# or: venv\Scripts\activate     # Windows

pip install Django requests playwright
python -m playwright install chromium
python manage.py migrate
python manage.py fetch_item_pages --from-wiki
python manage.py parse_item_pages
python manage.py load_item_files
python manage.py runserver 127.0.0.1:8090
```

## Documentation

- **[Architecture](docs/architecture.md)** — high-level overview, component
  descriptions, and architectural gotchas.
- **[Pipeline](docs/pipeline.md)** — detailed pipeline documentation with
  all commands, arguments, and workflows.
- **[Installation](docs/installation.md)** — full setup from a fresh machine,
  including troubleshooting and admin user creation.
