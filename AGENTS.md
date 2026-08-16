# AGENTS.md

## Quick facts
- Django + SQLite search app over a local mirror of the DDO wiki. App: `catalog`.
  Project root: `D:\ddo_wiki_poc`; settings module: `ddo_search.settings`.
- PowerShell: always call the venv python directly:
  `& "D:\ddo_wiki_poc\venv\Scripts\python.exe" manage.py ...`
  No requirements.txt — Django 5.2, requests, playwright are already in the venv.
- DB is `db.sqlite3` (gitignored) at the project root. This IS a git repo, but most work
  (docs, migrations 0004-0018, the render system) is uncommitted.
- Dev server: `python manage.py runserver 127.0.0.1:8090 --noreload`.
  `ALLOWED_HOSTS` includes LAN IPs `10.10.10.120` and `75.7.10.99` — the app is served on the LAN.
  Health check: `GET /?enhancement_options=1` → 200 + JSON. Admin `/admin/`: `admin` / `admin123`.

## Instruction gates
- "Define/explain X before moving on" (or "investigate before fixing") is a HARD STOP:
  present the explanation and END your turn. Do not edit code, run mutating commands,
  or continue until the user explicitly confirms.
- Never generalize about the data from a small sample. If you characterize rows
  (e.g. "these are historic"), verify the claim across the full dataset first.

## Commands
- Full test suite: `python manage.py test catalog` (63 tests, ~1s).
- One test: `python manage.py test catalog.tests.<ClassName>.<test_method>`
  (classes: SnapshotTests, ResolveItemNameTests, RenderExtractionTests, RenderEnhancementCommandTests,
  RuleDisplayTests, RenderStoreTests, ParseMagnitudeTests, SearchViewMinFilterTests,
  SearchPageUITests).
- Management commands: `import_wiki` (`--snapshot`, `--load-snapshot [--force]`, `--full`,
  `--page T`, `--limit N`, `--reset-sync`), `render_enhancements` (`--reparse`, `--render-all`,
  `--clear`), `seed_enhancement_rules`, `clean_enhancements`, `test_browser` / `test_direct_api`
  (WAF debug helpers).

## Core architecture (must-know)
The wiki is only contacted rarely; everything wiki-derived lives on DISK so the DB is disposable:
- `wiki_snapshot/pages/` + `manifest.json` — raw wikitext, full Item namespace (~12,500 pages).
- `wiki_snapshot/renders/` — one JSON per distinct enhancement template call (~6,200 files, incl. raw_html).
- `db.sqlite3` — parsed, searchable rows only.
- `wiki_snapshot/meta.json` — `{"as_of": ...}` snapshot capture date, written when the capture completes.
- `SyncState` (single DB row) — `as_of` = the snapshot's data date (capture date, NOT the import date); shown in the UI as "Database current with DDO Wiki as of X". Written by `load_snapshot_to_db` and by direct-wiki sync.

Full rebuild (never touches the wiki):
```powershell
Remove-Item db.sqlite3
python manage.py migrate
python manage.py seed_enhancement_rules
python manage.py import_wiki --load-snapshot --force
```
Parser/render-fix loop: `render_enhancements --reparse` (re-extracts from stored raw_html, no wiki)
then `import_wiki --load-snapshot --force`. `--clear` re-fetches from the wiki and is only for when
the wiki's own templates changed. `import_wiki --page` skips unchanged revisions — parser fixes only
propagate via `--force`.

## Data model gotchas (models.py)
- `ItemEnhancement` stores only `item`, `variant`, `tier`, `raw_template`. Its
  `enhancement` / `value` / `detail` / `display_text` / `magnitude` are PROPERTIES proxying to
  `EnhancementVariant`. Edit the variant — every item pointing at it updates.
- `Enhancement.display_name` is the user-editable label. Collision rule (recent, easy to break):
  display_name must not match — case-insensitive — any other enhancement's name OR display_name.
  Enforced in `Enhancement.clean()` AND by the functional unique index
  `unique_enhancement_effective_label` on `COALESCE(NULLIF(display_name, ''), name)` (migration 0018).
  Matching your own wiki name is allowed. Keep clean() and the constraint in sync.
- Dropdowns sort on the label (display_name or name), NOT the wiki name: `item_search` sorts by
  `label.casefold()`; the AJAX `enhancement_options` JSON carries a `labels` map that item_search.js sorts on.
- `Enhancement.name` is unique and immutable in the admin (read-only after creation).
- Item `name` is NOT unique; `wiki_title`/`wiki_page_id` are. Some wiki pages write the `name` field as
  a link template — `Item:X (level N)` (one page per minimum level, 1027 of them), `(historic)` (26),
  and `Item:Epic X` (23) — e.g.   `{{Item|Allegiance}}` or `{{Item|Cavalry Plate|Epic Cavalry Plate}}`.
  Template:Item renders `[[Item:{1}|{2|{1}}]]`; the importer resolves it via `resolve_item_name`
  (2nd arg = display, else 1st; trailing `(level N)` text preserved). Other pages write `name` as a
  raw wikilink (`[[Wraps of Endless Light]]`, `[[Blasting Chime|Epic Blasting Chime]]`,
  `Epic [[Ring of the Stalker]]`, `[[Image:...]] text`) — those resolve to MediaWiki's rendered text
  (pipe display, else namespace-stripped target; Image/File embeds drop entirely).
  `resolve_item_name` also strips HTML comments (MediaWiki renders them invisible; editors use them
  for notes) and normalizes curved apostrophes (`’`/`‘`) to `'` — page titles use the ASCII form.
  A row may share its name with other rows (different ML versions).
- The UI shows `item.display_name` in the Name column = `wiki_title` minus the `Item:` prefix, so
  variants display as "Allegiance (level 12)" / "Allegiance (historic)" while `name` stays canonical
  for search/sort.

## Wiki / WAF (only import_wiki.py talks to the wiki)
- The wiki is behind AWS WAF: plain HTTP / `requests` returns HTTP 202. Only `import_wiki.py`'s
  `api_request()` handles the WAF token (headless-Chromium solve via playwright + cached token in
  `wiki_waf_token.json`). `render_enhancements` reuses it.
- The `expandtemplates` render pass rejects newlines and over-long URLs (403). Calls are batched with
  `@@K{i}@@` sentinels, capped at `BATCH_URL_LIMIT = 1900`, and a rejected batch shrinks in half. Do not
  "fix" batching with newline-separated text.
- A WAF token refresh line at the start of a run is normal; only repeated 202s after refresh indicate trouble.

## Testing quirks
- Render-command tests mock the wiki with `_batched_response()` (top of tests.py). The request text is
  sentinel-joined — asserting a raw single-call equality will never match.

## PowerShell gotcha
- `>` / `*>` redirects write UTF-16LE; reading them back as text shows mojibake (duplicated/truncated).
  Decode to UTF-8 or capture from inside Python. If shell output looks corrupted, suspect this before a logic bug.

## Git gotcha
- `wiki_snapshot/` (~12,500 files) and `wiki_waf_token.json` are untracked and NOT gitignored;
  `db.sqlite3` and `venv/` ARE ignored. Never `git add -A` blindly.

## Current state (verified 2026-08-15)
- 9,038 items, 1,098 enhancements, 6,152 variants, 56,328 ItemEnhancement rows, 5 rules, no
  display_name overrides set. All 86 tests pass. Server on 10.10.10.120:8000 (fresh restart).
- `{{Item|...}}` name-resolution fix applied: 1,079 names resolved in DB (1,076 `{{Item|X}}`/
  `{{Item|X|Y}}`/suffix variants + 3 `Epic {{Item|X}}`), plus 82 raw-wikilink names (`[[X]]`,
  `[[X|Y]]`, `[[Image:...]] text`) and 3 HTML-comment + 3 curved-apostrophe names. `resolve_item_name`
  in import_wiki.py prevents recurrence on re-import; a full-name sweep shows 0 names contain any
  `{ } [ ] < >` markup. UI Name column shows `item.display_name` (page title, e.g. "Allegiance (level 12)").
- `Item.wiki_revision_timestamp` (wiki last-edit date) and `Item.updated_at` (importer write date)
  are recorded on new imports only — existing rows are null until re-captured/reloaded. UI additions:
  admin link, New Search button, "N items in database", sortable Name/Type/Min Level headers,
  subtitle with the SyncState as-of date; items list only appears after a search.
