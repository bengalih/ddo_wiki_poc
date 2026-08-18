import json
import html as html_mod
import re
from pathlib import Path

from django.core.management.base import BaseCommand

from catalog.wiki_api import WikiAPI


def parse_wikitext_tree(expanded_wikitext):
    """Parse the Enchantments section of fully-expanded
    wikitext into a tree. The expanded wikitext has all
    templates resolved into [[Link|Display]] syntax with
    * / ** / *** nesting."""
    # Find the Enchantments section. It appears as:
    #   ! ... |Enchantments\n| class=...|\n* items...
    #   | enhancements = \n* items...
    # Match the header line, optional cell value line,
    # then capture all * lines.
    m = re.search(
        r'(?:\|\s*enhancements?\s*(?:=\s*)?|'
        r'![^|]*\|[Ee]nchantments)\s*\n'
        r'(?:\|[^*]*\|\s*\n)?'  # optional cell line
        r'((?:\*.*\n?)+)',
        expanded_wikitext,
    )
    if not m:
        return []

    section = m.group(1).strip()
    tree = []
    stack = []

    for line in section.split("\n"):
        line = line.strip()
        if not line.startswith("*"):
            continue

        depth = 0
        while depth < len(line) and line[depth] == "*":
            depth += 1

        content = line[depth:].strip()
        if not content:
            continue

        # Strip category tags, file links, templatestyles,
        # ALL HTML tags and their content (tooltips),
        # keep [[Link|Display]] and &rarr;
        clean = re.sub(
            r'\[\[Category:[^\]]*\]\]', '', content
        )
        clean = re.sub(
            r'\[\[File:[^\]]*\]\]', '', clean
        )
        clean = re.sub(
            r'\{\{[^}]*\}\}', '', clean
        )
        # Strip <templatestyles ... /> (handles / in attributes)
        clean = re.sub(
            r'<templatestyles[^>]*>', '', clean
        )
        # Strip tooltip spans and their content:
        # <span class="popup tooltip ...">...</span>
        clean = re.sub(
            r'<span class="popup tooltip[^"]*"[^>]*>'
            r'.*?</span>',
            '', clean, flags=re.DOTALL,
        )
        # Strip remaining spans (just tags, no content)
        clean = re.sub(r'<span[^>]*>', '', clean)
        clean = re.sub(r'</span>', '', clean)
        # Strip <br />, <br/>, <br>
        clean = re.sub(r'<br\s*/?\s*>', ' ', clean)
        # Strip <small>, <sup>, <sub>, <nowiki>
        clean = re.sub(r'</?small>', ' ', clean)
        clean = re.sub(r'</?sup>', '', clean)
        clean = re.sub(r'</?sub>', '', clean)
        clean = re.sub(r'</?nowiki>', '', clean)
        # Decode HTML entities (&#32; -> space, etc.)
        clean = html_mod.unescape(clean)

        # Check for <ul><li> HTML nesting (used by container
        # templates like Nearly Finished, Almost There).
        # If present, extract child nodes from <li> elements.
        # Handles nested <ul><li> like:
        #   <li>One of the following:<ul><li>child1</li>...
        html_children = []
        ul_match = re.search(r'<ul>', clean)
        if ul_match:
            # Parent text is everything before the <ul>
            parent_text = clean[:ul_match.start()].strip()

            # Parse the <ul> content recursively
            html_children = _parse_ul(
                clean[ul_match.start():]
            )
            clean = parent_text

        # Strip <ul>, </ul>, <li>, </li> (in case any remain)
        clean = re.sub(r'</?ul>', ' ', clean)
        clean = re.sub(r'</?li>', ' ', clean)

        # Replace [[Link|Display]] with just the display
        # text, and [[Link]] with the link text
        parts = []
        last_end = 0
        all_links = []
        for lm in re.finditer(
            r'\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]',
            clean,
        ):
            target = lm.group(1).strip()
            display = (lm.group(2) or target).strip()
            all_links.append({
                "target": target,
                "text": display,
            })
            # Add text before this link
            before = clean[last_end:lm.start()]
            parts.append(before)
            # Replace link with display text
            parts.append(display)
            last_end = lm.end()

        parts.append(clean[last_end:])
        text = "".join(parts)

        # Clean up: &rarr; → arrow, whitespace, bold,
        # leading "Adds "
        text = text.replace("&rarr;", " → ")
        text = text.replace("&#x27;", "'")
        text = re.sub(r"'+", "", text)
        text = re.sub(r"\s+", " ", text).strip()

        if all_links:
            target = all_links[0]["target"]
        else:
            target = text

        # Build child nodes from <ul><li> HTML nesting
        children = []
        for hc in html_children:
            hc_links = hc.get("links", [])
            hc_target = (
                hc_links[0]["target"] if hc_links
                else hc["text"]
            )
            children.append({
                "text": hc["text"],
                "children": hc.get("children", []),
                "links": [
                    {
                        "target": f"/page/{lk['target'].replace(' ', '_')}",
                        "title": lk["target"],
                        "text": lk["text"],
                    }
                    for lk in hc_links
                ] if hc_links else [{
                    "target": f"/page/{hc_target.replace(' ', '_')}",
                    "title": hc_target,
                    "text": hc["text"],
                }],
            })

        node = {
            "text": text,
            "children": children,
            "links": [
                {
                    "target": f"/page/{link['target'].replace(' ', '_')}",
                    "title": link["target"],
                    "text": link["text"],
                }
                for link in all_links
            ] if all_links else [{
                "target": f"/page/{target.replace(' ', '_')}",
                "title": target,
                "text": text,
            }],
        }

        # Adjust depth: the Enchantments section uses
        # * for top-level, ** for children, *** for
        # grandchildren. But some expansions start at
        # ** (e.g. Attuned children without parent *).
        # Normalize so depth 1 = top level.
        if not tree:
            min_depth = depth

        while stack and stack[-1][0] >= depth:
            stack.pop()

        if depth == 1 or not stack:
            tree.append(node)
            stack.append((depth, node))
        else:
            if stack:
                stack[-1][1]["children"].append(node)
            else:
                tree.append(node)
            stack.append((depth, node))

    return tree


def render_tree_html(nodes, depth=0):
    if not nodes:
        return ""
    parts = ['<ul class="tree">']
    for node in nodes:
        text = html_mod.escape(node["text"])
        children = node.get("children", [])
        if children:
            parts.append(
                f'<li class="branch">{text}'
                f'{render_tree_html(children, depth + 1)}'
                f'</li>'
            )
        else:
            parts.append(f'<li class="leaf">{text}</li>')
    parts.append("</ul>")
    return "\n".join(parts)


class Command(BaseCommand):
    help = (
        "POC: expanded-wikitext tree vs HTML tree."
    )

    def add_arguments(self, parser):
        parser.add_argument("item_title")
        parser.add_argument(
            "--out", default="poc_tree_compare.html"
        )

    def handle(self, *args, **options):
        item_title = options["item_title"]
        out_path = Path(options["out"])

        # ── Load stored HTML tree ────────────────────────
        item_data = self._load_file(
            "wiki_snapshot/items", item_title
        )
        raw_data = self._load_file(
            "wiki_snapshot/raw", item_title
        )
        stored_tree = (
            (item_data or raw_data) or {}
        ).get("enchantments", [])

        # Determine the full wiki page title with namespace
        wiki_title = item_title
        if ":" not in item_title:
            raw_page_title = (raw_data or {}).get("page_title")
            if raw_page_title:
                wiki_title = raw_page_title
            else:
                wiki_title = f"Item:{item_title}"

        # ── Fetch expanded wikitext via API ──────────────
        self.stdout.write(
            f"Fetching expanded wikitext for {wiki_title}..."
        )

        api = WikiAPI()
        api.stdout = self.stdout
        api.stderr = self.stderr

        data = api.api_request({
            "action": "expandtemplates",
            "title": wiki_title,
            "text": f"{{{{:{wiki_title}}}}}",
            "prop": "wikitext",
            "format": "json",
            "formatversion": "2",
        })

        expanded = (
            data.get("expandtemplates", {})
            .get("wikitext", "")
        )

        self.stdout.write(
            f"  Expanded wikitext: {len(expanded)} chars"
        )

        # ── Parse expanded wikitext into tree ────────────
        wt_tree = parse_wikitext_tree(expanded)

        self.stdout.write(
            f"  Expanded tree: {len(wt_tree)} nodes"
        )
        self.stdout.write(
            f"  HTML tree: {len(stored_tree)} nodes"
        )

        for node in wt_tree:
            nc = len(node.get("children", []))
            suffix = f" ({nc} children)" if nc else ""
            self.stdout.write(f"    - {node['text']}{suffix}")

        # ── Generate HTML ────────────────────────────────
        wt_html = render_tree_html(wt_tree)
        stored_html = render_tree_html(stored_tree)
        esc = html_mod.escape

        output = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Tree Comparison — {esc(item_title)}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont,
      "Segoe UI", Roboto, sans-serif;
    max-width: 1400px;
    margin: 0 auto;
    padding: 20px;
    background: #1a1a2e;
    color: #e0e0e0;
  }}
  h1 {{
    color: #00d4ff;
    border-bottom: 2px solid #00d4ff;
    padding-bottom: 10px;
    margin-bottom: 10px;
  }}
  h2 {{ color: #ff6b6b; margin: 20px 0 10px; }}
  .meta {{ color: #888; margin-bottom: 20px; }}
  .columns {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
  }}
  .panel {{
    border: 1px solid #333;
    border-radius: 8px;
    padding: 15px;
    background: #16213e;
  }}
  .panel h2 {{ margin-top: 0; }}
  .tree {{
    list-style: none;
    padding-left: 20px;
  }}
  .tree li {{
    padding: 4px 0;
    position: relative;
  }}
  .tree li::before {{
    content: "";
    position: absolute;
    left: -15px;
    top: 0;
    bottom: 0;
    width: 1px;
    background: #444;
  }}
  .tree li:last-child::before {{ bottom: 50%; }}
  .tree li::after {{
    content: "";
    position: absolute;
    left: -15px;
    top: 12px;
    width: 12px;
    height: 1px;
    background: #444;
  }}
  .leaf {{ color: #e0e0e0; }}
  .branch {{ color: #00d4ff; font-weight: bold; }}
  .branch > .tree li {{ color: #e0e0e0; font-weight: normal; }}
  .branch > .tree li::after {{ background: #00d4ff; }}
  .code {{
    background: #0f0f23;
    border: 1px solid #333;
    border-radius: 4px;
    padding: 12px;
    font-size: 11px;
    white-space: pre-wrap;
    max-height: 500px;
    overflow-y: auto;
    margin-top: 10px;
  }}
  .count {{
    display: inline-block;
    background: #0f0f23;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 12px;
    color: #888;
    margin-left: 8px;
  }}
  .section {{
    margin-top: 20px;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 15px;
    background: #16213e;
  }}
</style>
</head>
<body>
  <h1>{esc(item_title)}</h1>
  <div class="meta">
    Expanded via
    <code>action=expandtemplates&amp;title={esc(item_title)}&amp;text={{{{:{esc(item_title)}}}}}</code>
  </div>

  <div class="columns">
    <div class="panel">
      <h2>Expanded Wikitext Tree
        <span class="count">{len(wt_tree)} nodes</span>
      </h2>
      {wt_html}
      <details>
        <summary style="color:#888;cursor:pointer;margin-top:10px">JSON</summary>
        <pre class="code">{esc(json.dumps(wt_tree, indent=2))}</pre>
      </details>
    </div>

    <div class="panel">
      <h2>HTML-Derived Tree (current)
        <span class="count">{len(stored_tree)} nodes</span>
      </h2>
      {stored_html}
      <details>
        <summary style="color:#888;cursor:pointer;margin-top:10px">JSON</summary>
        <pre class="code">{esc(json.dumps(stored_tree, indent=2))}</pre>
      </details>
    </div>
  </div>

  <div class="section">
    <h2>Raw Expanded Wikitext</h2>
    <pre class="code">{esc(expanded[:5000])}</pre>
  </div>
</body>
</html>"""

        out_path.write_text(output, encoding="utf-8")
        self.stdout.write(
            self.style.SUCCESS(f"Written to {out_path}")
        )

    def _load_file(self, directory, item_title):
        filename = (
            item_title.replace(":", "_")
            .replace("/", "_")
        )
        # Files are stored with Item_ prefix
        prefixed = Path(directory) / f"Item_{filename}.json"
        if prefixed.exists():
            with prefixed.open("r", encoding="utf-8") as f:
                return json.load(f)
        bare = Path(directory) / f"{filename}.json"
        if bare.exists():
            with bare.open("r", encoding="utf-8") as f:
                return json.load(f)
        return None


def _parse_ul(html):
    """Parse a <ul>...</ul> block into a list of child dicts.
    Handles nested <ul><li> structures like:
      <li>One of the following:<ul><li>child1</li>...</ul>
    """
    children = []
    pos = 0
    while True:
        li_start = html.find("<li>", pos)
        if li_start == -1:
            break
        li_content_start = li_start + 4
        depth = 1
        scan = li_content_start
        li_end = -1
        while depth > 0 and scan < len(html):
            next_li = html.find("<li>", scan)
            next_end = html.find("</li>", scan)
            next_ul_open = html.find("<ul>", scan)
            next_ul_close = html.find("</ul>", scan)
            candidates = []
            if next_li != -1:
                candidates.append(("li_open", next_li))
            if next_end != -1:
                candidates.append(("li_close", next_end))
            if next_ul_open != -1:
                candidates.append(("ul_open", next_ul_open))
            if next_ul_close != -1:
                candidates.append(("ul_close", next_ul_close))
            if not candidates:
                break
            candidates.sort(key=lambda x: x[1])
            tag, idx = candidates[0]
            if tag == "ul_open":
                depth += 1
                scan = idx + 4
            elif tag == "ul_close":
                depth -= 1
                scan = idx + 5
            elif tag == "li_close":
                depth -= 1
                if depth == 0:
                    li_end = idx
                    break
                scan = idx + 5
            elif tag == "li_open":
                depth += 1
                scan = idx + 4
        if li_end == -1:
            break

        li_content = html[li_content_start:li_end]
        pos = li_end + 5

        nested_ul = re.search(r'<ul>', li_content)
        if nested_ul:
            parent_text = li_content[:nested_ul.start()].strip()
            sub_children = _parse_ul(
                li_content[nested_ul.start():]
            )
            children.append(_make_ul_node(parent_text, sub_children))
        else:
            children.append(_make_ul_node(li_content, []))

    return children


def _make_ul_node(raw_text, sub_children):
    """Convert raw <li> text into a node dict with cleaned
    text, links, and children."""
    text = re.sub(r'<[^>]+>', '', raw_text)
    text = html_mod.unescape(text)
    links = []
    parts = []
    last = 0
    for lm in re.finditer(
        r'\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]', text,
    ):
        target = lm.group(1).strip()
        display = (lm.group(2) or target).strip()
        links.append({"target": target, "text": display})
        parts.append(text[last:lm.start()])
        parts.append(display)
        last = lm.end()
    parts.append(text[last:])
    text = "".join(parts)
    text = text.replace("&rarr;", " → ")
    text = re.sub(r"'+", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    t = links[0]["target"] if links else text
    return {
        "text": text,
        "children": sub_children,
        "links": [
            {
                "target": f"/page/{lk['target'].replace(' ', '_')}",
                "title": lk["target"],
                "text": lk["text"],
            }
            for lk in links
        ] if links else [{
            "target": f"/page/{t.replace(' ', '_')}",
            "title": t,
            "text": text,
        }],
    }
