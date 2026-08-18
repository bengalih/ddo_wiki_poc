# Wiki API Reference

All wiki requests go through `catalog/wiki_api.py` (`WikiAPI.api_request()`).
No other code touches the network.

Base URL: `https://ddowiki.com/api.php`

---

## WAF Token Handling

The wiki sits behind AWS WAF. Plain HTTP returns HTTP 202 (challenge page).
`WikiAPI` solves this by:

1. Launching headless Chromium via Playwright.
2. Navigating to the wiki homepage and waiting out the JS challenge.
3. Extracting the `aws-waf-token` cookie.
4. Reusing it for subsequent requests (~10 minute TTL).
5. Caching the token in `wiki_waf_token.json` to avoid re-solving between runs.

On HTTP 202, the token is re-solved and the request retried with backoff.

** WAF proxy has 2048-character URL length limit. Exceeding generates a 403.
   Keep API queries under the 2048 character limit.

** Ensure that any requests for items are properly encoded (no "+" in item names), or use pageids where appropriate.
   Not doing so may cause 404 errors
---

## Request Mechanics

- **User-Agent:** `DDOItemIndex/0.2 (personal project)` (MediaWiki policy).
- **Pacing:** 1 second + 0.2s random jitter between requests.
- **maxlag:** `5` — MediaWiki server-side lag check; auto-retries on lag.
- **Retries:** Up to 5 attempts with exponential backoff (max 60s).
- **Timeout:** 30 seconds per request.

---

## API Calls

### 1. Enumerate Named Item Pages (`--from-wiki` only)

Finds all pages in the Item namespace (500) that transclude
`{{Named_item}}`, returning only real item pages (~9,038) server-side.

**Endpoint:** `action=query` with `generator=embeddedin`

```
GET https://ddowiki.com/api.php
    ?action=query
    &generator=embeddedin
    &geititle=Template:Named_item
    &geinamespace=500
    &geilimit=500
    &prop=revisions
    &rvprop=ids
    &rvslots=main
    &format=json
    &formatversion=2
    &maxlag=5
```

**Pagination:** Uses `continue` token. Repeated until no `continue`
field is returned. ~19 round trips for the full list.

**Output:** `{title: {page_id, revision_id}}` map stored in `page_map`.

---

### 2. Fetch Page Content (per page)

Renders a single page and returns HTML, wikitext, categories, and
revision ID. Called once per item page.

**Endpoint:** `action=parse`

```
GET https://ddowiki.com/api.php
    ?action=parse
    &page=Item%3A%2B1+Starter+Dagger
    &prop=text%7Cwikitext%7Ccategories%7Crevid
    &format=json
    &formatversion=2
    &maxlag=5
```

**Fields extracted:**
- `parse.pageid` → `page_id`
- `parse.revid` → `revision_id`
- `parse.text` → `html` (full rendered HTML)
- `parse.wikitext` → `wikitext` (template source)
- `parse.categories[*].title` → `categories` (string list)

---

### 3. Get Revision Timestamps (batched, after all fetches)

Returns the revision ID and timestamp for a batch of page IDs.
Called once after all parse calls complete, batched 50 pageids
per request.

**Endpoint:** `action=query` with `prop=revisions`

```
GET https://ddowiki.com/api.php
    ?action=query
    &pageids=17500%7C17501%7C17502
    &prop=revisions
    &rvprop=ids%7Ctimestamp
    &rvslots=main
    &format=json
    &formatversion=2
    &maxlag=5
```

**Fields extracted:**
- `revisions[0].revid` → `revision_id` (already set from parse, not overwritten)
- `revisions[0].timestamp` → `revision_timestamp`

**Batching:** 50 pageids per request (~181 requests for 9,038 items).
Uses pageids (numeric, ~6 chars each) instead of titles (~40 chars each)
to stay under the wiki's 2048-char URL length limit.

---

## Raw File Format

Each `wiki_snapshot/raw/<title>.json` stores the stitched result:

```json
{
  "page_title": "Item:+1 Starter Dagger",
  "page_id": 17500,
  "revision_id": 671095,
  "revision_timestamp": "2026-01-08T21:08:46Z",
  "fetched_at": "2026-08-17T04:30:58.966912+00:00",
  "categories": ["Daggers", "Weapons", ...],
  "api_url": "https://ddowiki.com/api.php?action=parse&...",
  "html": "<div class=\"mw-content-ltr mw-parser-output\">...</div>",
  "wikitext": "{{Named item|Weapon\n  | name = ..."
}
```

** These are not pure API dumps — `fetched_at` and `revision_timestamp`
are added by the fetch command.

---