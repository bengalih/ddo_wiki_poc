# Expanded Wikitext Conversion

Status: **POC complete, production integration pending**

## Overview

The DDO wiki stores item enchantment data in wikitext using nested templates
(e.g. `{{Nearly Finished|{{Stat|CHA|8}}|...}}`). Historically, enchantment
trees were extracted from rendered HTML via the `enchantment_html` module. This
document describes two alternative approaches: (1) expanding the raw wikitext via
the MediaWiki API, then parsing the resulting expanded text directly into the same
tree structure; and (2) using the `action=parse` API with `prop=parsetree` to get
structured XML that preserves template names and parameters.

The expanded-wikitext approach preserves template names (e.g. `Stat`, `Save`,
`Augment`) that are lost in rendered HTML, enabling automatic enchantment
grouping for the search UI dropdown without maintaining a static mapping file.

## Findings

### The parsetree is the key to enchantment grouping

The `action=parse` API with `prop=parsetree` returns structured XML that
preserves template names and parameters:

```xml
<template><title>Nearly Finished</title>
  <part><name index="1"/><value>
    <template><title>Stat</title>
      <part><name index="1"/><value>CHA</value></part>
      <part><name index="2"/><value>8</value></part>
    </template>
  </value></part>
  <!-- ... 5 more Stat templates ... -->
</template>
```

This is the source of truth for enchantment grouping. The template name
(`Stat`) gives us the group, and the parameters (`CHA`, `8`) give us the
display text.

### Parsetree and HTML are positionally equivalent

The order of templates in the parsetree matches the order of rendered elements
in the HTML. This correlation is by **nesting structure and order**, not by
field name:

| Parsetree | HTML |
|-----------|------|
| `Nearly Finished` | `Nearly Finished` |
| `Stat CHA 8` | `Charisma +8` |
| `Stat CON 8` | `Constitution +8` |
| `Stat DEX 8` | `Dexterity +8` |
| `Stat INT 8` | `Intelligence +8` |
| `Stat STR 8` | `Strength +8` |
| `Stat WIS 8` | `Wisdom +8` |
| `Almost There` | `Almost There` |
| `Stat CHA 3 Insightful` | `Insightful Charisma +3` |
| ... | ... |
| `Resistance 1 Quality` | `Quality Resistance +1` |
| `Temperance of Belief` | `Temperance of Belief` |
| `Augment Blue` | `Blue Augment Slot` |

The HTML has duplicate links (tooltip text, description links), but the core
order matches. The parsetree template order = HTML rendering order.

### What this means for the parser

1. **Parse the parsetree** to get template name + parameters for each node
2. **Parse the HTML** to get display text + nesting
3. **Match them by position** within the same parent

The display text ("Charisma +8") comes from the Stat template's expansion
logic, which we'd need to hardcode or derive from the parameters. But the
**type** (`Stat`) comes directly from the parsetree.

### Mapping example for Collective Sight

| Enhancement | Type |
|-------------|------|
| Charisma +8 | Stat |
| Constitution +8 | Stat |
| Dexterity +8 | Stat |
| Intelligence +8 | Stat |
| Strength +8 | Stat |
| Wisdom +8 | Stat |
| Insightful Charisma +3 | Stat |
| Insightful Constitution +3 | Stat |
| Insightful Dexterity +3 | Stat |
| Insightful Intelligence +3 | Stat |
| Insightful Strength +3 | Stat |
| Insightful Wisdom +3 | Stat |
| Quality Resistance +1 | Resistance |
| Temperance of Belief | Temperance of Belief |
| Blue Augment Slot | Augment |

## Toolset

### Management command: `poc_tree_compare.py`

Location: `catalog/management/commands/poc_tree_compare.py`

Usage:

```
python manage.py poc_tree_compare "Item Name" [--out output.html]
```

The command:

1. Reads the raw file for the item from `wiki_snapshot/raw/`.
2. Calls the MediaWiki `action=expandtemplates` API to get fully expanded
   wikitext.
3. Parses the expanded wikitext into a tree using `_parse_wikitext_tree()`.
4. Parses the stored HTML (from the same raw file) into a tree using the
   existing HTML parser.
5. Writes a side-by-side HTML comparison file (`poc_tree_*.html`).

### MediaWiki API calls

#### expandtemplates (for expanded wikitext)

```
GET https://ddowiki.com/api.php
  ?action=expandtemplates
  &title=Item:<PageName>
  &text={{:Item:<PageName>}}
  &prop=wikitext
  &format=json
  &formatversion=2
```

Key parameters:

- `title` **must** use the `Item:` namespace prefix (e.g. `Item:Agony, the Knife
  in the Dark`). Without it, transcluded templates like `{{Attuned to Heroism}}`
  are not expanded.
- `text` must use `{{:Item:<PageName>}}` transclusion syntax (leading colon)
  combined with the `title` parameter. Omitting the `Item:` prefix in `title`
  causes incomplete expansion — typically 7,000–8,000 chars instead of 10,000+.
- The WAF token from `wiki_api.py` is required. The API call goes through
  `WikiAPI().api_request()` which handles token refresh and retry.

#### action=parse with parsetree (for template names)

```
GET https://ddowiki.com/api.php
  ?action=parse
  &page=Item:<PageName>
  &prop=text|wikitext|parsetree|revid
  &format=json
  &formatversion=2
```

This returns:

- `text`: Rendered HTML (what we currently parse)
- `wikitext`: Raw wikitext (what we currently fetch)
- `parsetree`: Structured XML preserving template names and parameters
- `revid`: Revision ID

The parsetree is the key to enchantment grouping. It's positionally equivalent
to the HTML — the order of templates matches the order of rendered elements.

### What the expanded wikitext looks like

Before expansion (raw wikitext):

```
* {{Nearly Finished|{{Stat|CHA|8}}|{{Stat|CON|8}}|...}}
```

After expansion:

```
* <span class="popup has_tooltip with-icon basic">[[Nearly Finished]][[File:Icon tooltip.png|...]]<span class="popup tooltip wide left below" ...>...</span></span><br/><ul><li>One of the following:<ul><li><span class="popup has_tooltip with-icon basic">[[Charisma| Charisma +8]][[File:Icon tooltip.png|...]]<span class="popup tooltip ...">...</span></span>[[Category: Charisma +8 items]]</li><li>...</li></ul></li></ul>
```

Templates expand into:

- Full `* / ** / ***` wikitext nesting with `[[Link|Display]]` syntax
- HTML tooltip `<span>` elements
- `<templatestyles>` tags (CSS includes)
- `[[File:...]]` and `[[Category:...]]` tags
- Container templates (Nearly Finished, Almost There) use `<ul><li>` HTML
  nesting instead of `**` wikitext nesting for their child lists

### What the parsetree looks like

The parsetree is structured XML that preserves the template hierarchy:

```xml
<root>
  <template>
    <title>Named item</title>
    <part><name index="1"/><value>Jewelry</value></part>
    <part><name> enhancements </name><equals>=</equals><value>
      * <template><title>Nearly Finished</title>
          <part><name index="1"/><value>
            <template><title>Stat</title>
              <part><name index="1"/><value>CHA</value></part>
              <part><name index="2"/><value>8</value></part>
            </template>
          </value></part>
          <!-- ... 5 more Stat templates ... -->
        </template>
      * <template><title>Resistance</title>
          <part><name index="1"/><value>1</value></part>
          <part><name index="2"/><value>Quality</value></part>
        </template>
    </value></part>
  </template>
</root>
```

The parsetree preserves:

- Template names (`Stat`, `Resistance`, `Augment`)
- Parameters (positional and named)
- Nesting hierarchy (which templates are children of which)
- The `* / ** / ***` list markers that define tree depth

## Parsing approach

### Parsetree parser (proposed)

A new parser for the parsetree XML would:

1. Parse the XML using `xml.etree.ElementTree`
2. Walk the `<template>` nodes, extracting `<title>` (template name) and
   `<part>` elements (parameters)
3. Build a tree of `{"template", "params", "children"}` dicts
4. Correlate with the HTML tree by position within the same parent

This gives us:

- Template names for grouping (e.g., `Stat`, `Resistance`, `Augment`)
- Parameters for display text (e.g., `CHA`, `8`)
- Nesting structure matching the HTML tree

### Wikitext parser: `_parse_wikitext_tree()`

Module-level function in `poc_tree_compare.py`. Processes the full expanded
wikitext string and returns a tree of `{"text", "children", "links"}` dicts
matching the HTML-derived tree format.

The parser works line-by-line on the raw expanded wikitext:

1. **Strip cruft**: `[[File:...]]`, `[[Category:...]]`, `{{double brace templates}}`,
   `<templatestyles>`, tooltip `<span>` elements, remaining `<span>` tags,
   `<br>`, `<small>`, `<sup>`, `<sub>`, `<nowiki>`, HTML entities.
2. **Detect `<ul><li>` nesting**: If the cleaned text contains `<ul>`, extract
   child nodes via `_parse_ul()` (handles nested `<ul><li>` structures).
3. **Extract `[[Link|Display]]`**: Replace wiki links with display text, collect
   link metadata.
4. **Classify line prefix**: `*` = top-level child, `**` = grandchild, etc.
   Depth determined by `depth = len(prefix) - 1`.
5. **Strip template residue**: Remove `&rarr; →`, apostrophes, collapse
   whitespace.

### Nested `<ul><li>` parser: `_parse_ul()`

Handles the HTML-style nesting used by container templates. Uses a
position-based scanner (not regex) to correctly match `<li>` / `</li>` pairs
accounting for nested `<ul>` and `<li>` tags:

1. Find each top-level `<li>` in the `<ul>`.
2. Track tag depth to find the matching `</li>`.
3. If the `<li>` content contains a nested `<ul>`, recurse — the text before
   the `<ul>` becomes the parent node, the nested `<li>` elements become
   children.
4. If no nested `<ul>`, the `<li>` is a leaf node.

### Node construction: `_make_ul_node()`

Converts raw `<li>` text into a tree node dict:

1. Strip HTML tags, unescape entities.
2. Extract `[[Link|Display]]` as `{"target", "text"}` link dicts.
3. Replace links with display text in the node text.
4. Clean up arrows, apostrophes, whitespace.

## Results

All three test items produce expanded-wikitext trees matching their
HTML-derived trees in node count and structure:

| Item | Nodes | Matches HTML | Notes |
|------|-------|-------------|-------|
| Agony, the Knife in the Dark | 10 | Yes | Tier lines (`** / ***`) parse correctly |
| Agarta's Belt | 6 | Yes | Tooltip text differences (by design) |
| Collective Sight | 6 | Yes | `One of the following:` correctly nests 6 stat choices |

### Collective Sight parsetree correlation

The parsetree for Collective Sight shows:

- `Nearly Finished` template with 6 `Stat` children (CHA, CON, DEX, INT, STR, WIS)
- `Almost There` template with 6 `Stat` children (with Insightful qualifier)
- `Resistance` template (Quality, 1)
- `Temperance of Belief` template (no params)
- `Augment` template (Blue)

This maps directly to the HTML rendering order, confirming positional
equivalence. The parsetree gives us the template name for each enhancement,
which is what we need for the grouping feature.

## Approach

### Option 1: Parsetree + HTML (recommended)

Use the `action=parse` API with `prop=text|parsetree` to get both:

- **Parsetree**: Template names and parameters for grouping
- **HTML**: Display text and nesting for tree structure

Parse both, correlate by position within the same parent. This gives us:

- Template names for grouping (e.g., `Stat` → "Ability" subcategory)
- Display text from HTML (e.g., "Charisma +8")
- Tree structure from HTML (nesting, tiers, choices)

### Option 2: Expanded wikitext only

Use the `expandtemplates` API to get expanded wikitext, then parse:

- `[[Link|Display]]` syntax for display text
- `* / ** / ***` nesting for tree structure
- `<ul><li>` HTML for container templates

This gives us display text and tree structure, but NOT template names
for grouping (templates are already expanded).

### Recommendation

Option 1 is better because:

- One API call per item (vs two for Option 2)
- Template names preserved for grouping
- HTML parsing is already implemented and tested
- Parsetree parsing is straightforward XML

## Gotchas

### Namespace resolution is critical

The `expandtemplates` API requires the `title` parameter to include the
`Item:` namespace prefix, and `text` must use `{{:Item:Title}}` transclusion
syntax (note the leading colon). Without this:

- Templates like `{{Attuned to Heroism}}` are returned unexpanded (7,697 chars
  vs 10,922 for Agony)
- Tier lines (`** / ***`) are absent
- The result is useless for tree parsing

The POC reads `page_title` from the raw file (e.g.
`Item:Agony, the Knife in the Dark`) or prepends `Item:` to the item name if
not present.

### Container templates use `<ul><li>`, not `**`

Templates like `Nearly Finished` and `Almost There` inject their child lists
using `<ul><li>` HTML nesting in the expanded wikitext, not `** / ***`
wikitext nesting. The parser must handle both formats. The `<ul><li>` path is
handled by `_parse_ul()`.

### Mythic Boost variants are wiki-template injected

The `Named item` wiki template uses `{{#ifeq:{{lc:{{NAMESPACE}}}}|item|...}}`
to inject Mythic Boost variants. These appear in rendered HTML but **not** in
the raw wikitext or `expandtemplates` output. A wikitext-based parser will
miss them unless the injection logic is replicated.

### Tooltip text differs between wikitext and HTML

Expanded wikitext preserves wiki link syntax inside tooltips (`[[Link|Display]]`
with apostrophes for bold), while rendered HTML has plain text. This is expected
and not a bug — the tree structure matches; only tooltip content differs.

### `<templatestyles>` regex must handle `/` in attributes

The `<templatestyles>` tag can contain paths with `/` (e.g.
`src="Augment/styles.css"`). Use `<templatestyles[^>]*>` not
`<templatestyles[^/]*/>`.

### Text cleanup order matters

The stripping order is:

1. `[[File:...]]` and `[[Category:...]]` (before `<ul><li>` detection)
2. `{{double brace templates}}` (template residue)
3. `<templatestyles>`
4. Tooltip `<span class="popup tooltip ...">...</span>`
5. Remaining `<span>` / `</span>` tags
6. `<br>`, `<small>`, `<sup>`, `<sub>`, `<nowiki>`
7. HTML entity unescaping

If `<span>` stripping happens before tooltip stripping, tooltip content is
lost. If `[[File:...]]` is not stripped before `<ul><li>` detection, it
contaminates child node text.

### WAF token required

All API calls go through `WikiAPI` which handles WAF challenges via headless
Chromium. The token is cached in `wiki_waf_token.json` with ~10 min TTL. The
POC command automatically refreshes it.

### `_parse_ul` uses position-based scanning, not regex

The `<li>` / `</li>` matching must account for nested `<ul>` and `<li>` tags.
A regex approach (e.g. `re.findall(r'<li>(.*?)</li>', ...)`) will fail on
nested structures because `.*?` does not track tag depth.

### Parsetree XML may contain whitespace in template names

Template titles and parameter names may have leading/trailing whitespace
(e.g. `<name> enhancements </name>`). Strip whitespace when comparing.

### Parsetree positional correlation assumes no wiki-side changes

The positional correlation between parsetree templates and HTML rendering
depends on the wiki's template expansion logic. If a template changes how it
renders (e.g., reorders parameters), the correlation breaks. This is unlikely
but worth monitoring.

## Debug files

These files were created during POC development and can be cleaned up:

- `debug_expand.py` — Debug script for expanded wikitext
- `debug_tree.py` — Debug script for tree building step-by-step
- `debug_api.py` — Debug script for API comparison
- `debug_correlate.py` — Debug script for parsetree/HTML correlation
- `debug_correlate2.py` — Debug script for positional correlation
- `debug_parsetree.json` — API response for Collective Sight
- `poc_tree_agony.html` — Side-by-side visual comparison
- `poc_tree_agarta.html` — Side-by-side visual comparison
- `poc_tree_collective_sight.html` — Side-by-side visual comparison
