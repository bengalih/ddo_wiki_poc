import json
import html as html_mod
import re
from pathlib import Path

from django.core.management.base import BaseCommand

from catalog.wiki_api import WikiAPI


# Templates we know produce enchantment tree nodes.
# Map template name -> group label for the dropdown.
ENCHANTMENT_TEMPLATES = {
    "Stat": "Ability",
    "Save": "Save",
    "Spell Lore": "Spell Lore",
    "Spell Penetration": "Spell Penetration",
    "Augment": "Augment",
    "Resistance": "Resistance",
    "Melee Power": "Power",
    "Ranged Power": "Power",
    "Doublestrike": "Melee",
    "Sneak Attack": "Melee",
    "Insightful": "Insightful",
    "Quality": "Quality",
}

# Templates that are upgrade containers (produce
# parent nodes with tier children).
CONTAINER_TEMPLATES = {
    "Nearly Finished",
    "Almost There",
    "Attuned to Heroism",
}

# Templates to skip (metadata, not enchantments).
SKIP_TEMPLATES = {
    "Named item",
    "Bind",
    "Item",
    "Forums",
    "CCi",
    "Popup",
}


def parse_template_call(text):
    """Parse {{Name|param1|param2|...}} into (name, params).
    Handles nested templates by tracking depth."""
    if not text.startswith("{{") or not text.endswith("}}"):
        return None, []

    inner = text[2:-2].strip()
    parts = []
    depth = 0
    current = []

    for ch in inner:
        if ch == "{" :
            depth += 1
            current.append(ch)
        elif ch == "}":
            depth -= 1
            current.append(ch)
        elif ch == "|" and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)

    parts.append("".join(current).strip())

    if not parts:
        return None, []

    return parts[0], parts[1:]


def wikitext_to_tree(wikitext):
    """Parse the enhancements section of a {{Named item}}
    wikitext into a tree structure similar to what
    enchantment_html.py produces."""
    # Extract the enhancements parameter
    enh_match = re.search(
        r"\|\s*enhancements\s*=\s*\n(.*?)(?=\n\s*\|\s*\w+\s*=|\n\}\})",
        wikitext,
        re.DOTALL,
    )

    if not enh_match:
        return []

    enh_text = enh_match.group(1).strip()
    tree = []
    current_parent = None
    current_children = []

    for line in enh_text.split("\n"):
        line = line.strip()

        if not line.startswith("*"):
            continue

        # Count leading * for nesting depth
        depth = 0
        while depth < len(line) and line[depth] == "*":
            depth += 1

        content = line[depth:].strip()

        if not content:
            continue

        # Check if this is a [[Link]] (wiki link, not template)
        link_match = re.match(
            r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]",
            content,
        )

        if link_match:
            link_text = link_match.group(1)
            node = {
                "text": link_text,
                "children": [],
                "links": [{
                    "target": f"/page/{link_text.replace(' ', '_')}",
                    "title": link_text,
                    "text": link_text,
                }],
            }

            if depth == 1:
                tree.append(node)
                current_parent = node
                current_children = []
            elif depth == 2 and current_parent:
                current_parent["children"].append(node)

            continue

        # Parse template call
        if content.startswith("{{"):
            name, params = parse_template_call(content)

            if not name or name in SKIP_TEMPLATES:
                continue

            # Build the rendered text from template
            rendered = render_template(name, params)

            node = {
                "text": rendered,
                "children": [],
                "links": [{
                    "target": f"/page/{name.replace(' ', '_')}",
                    "title": name,
                    "text": rendered,
                }],
            }

            if name in CONTAINER_TEMPLATES:
                # Container template: params are nested
                # templates or links
                for param in params:
                    param = param.strip()

                    if param.startswith("{{"):
                        child_name, child_params = (
                            parse_template_call(
                                "{{" + param + "}}"
                                if not param.startswith("{{")
                                else param
                            )
                        )

                        if child_name and child_name not in SKIP_TEMPLATES:
                            child_rendered = render_template(
                                child_name, child_params
                            )
                            child_node = {
                                "text": child_rendered,
                                "children": [],
                                "links": [{
                                    "target": f"/page/{child_name.replace(' ', '_')}",
                                    "title": child_name,
                                    "text": child_rendered,
                                }],
                            }
                            node["children"].append(child_node)
                    elif param.startswith("[["):
                        link_match = re.match(
                            r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]",
                            param,
                        )

                        if link_match:
                            lt = link_match.group(1)
                            node["children"].append({
                                "text": lt,
                                "children": [],
                                "links": [{
                                    "target": f"/page/{lt.replace(' ', '_')}",
                                    "title": lt,
                                    "text": lt,
                                }],
                            })

                tree.append(node)
                current_parent = node
                current_children = []
            elif depth == 1:
                tree.append(node)
                current_parent = node
                current_children = []
            elif depth == 2 and current_parent:
                current_parent["children"].append(node)

    return tree


def render_template(name, params):
    """Render a template call to human-readable text.
    This is a simplified renderer for known templates."""
    if name == "Stat":
        # {{Stat|STR|3}} -> "Strength +3"
        # {{Stat|STR|1|Insightful}} -> "Insightful Strength +1"
        stat_abbr = params[0] if params else ""
        value = params[1] if len(params) > 1 else ""
        prefix = params[2] if len(params) > 2 else ""

        stat_map = {
            "STR": "Strength",
            "DEX": "Dexterity",
            "CON": "Constitution",
            "INT": "Intelligence",
            "WIS": "Wisdom",
            "CHA": "Charisma",
        }

        stat_name = stat_map.get(
            stat_abbr.upper(), stat_abbr
        )

        parts = []

        if prefix:
            parts.append(prefix)

        parts.append(stat_name)
        parts.append(f"+{value}")

        return " ".join(parts)

    if name == "Save":
        # {{Save|Fortitude|4}} -> "Fortitude Save +4"
        save_name = params[0] if params else ""
        value = params[1] if len(params) > 1 else ""

        return f"{save_name} Save +{value}"

    if name == "Augment":
        # {{Augment|Blue}} -> "Blue Augment Slot"
        color = params[0] if params else ""
        return f"{color} Augment Slot"

    if name == "Resistance":
        # {{Resistance|1|Quality}} -> "Quality Resistance +1"
        value = params[0] if params else ""
        prefix = params[1] if len(params) > 1 else ""

        parts = []

        if prefix:
            parts.append(prefix)

        parts.append("Resistance")
        parts.append(f"+{value}")

        return " ".join(parts)

    if name == "Enhancement bonus":
        # {{Enhancement bonus|w|7}} -> "+7 Enhancement Bonus"
        value = params[1] if len(params) > 1 else params[0]
        return f"+{value} Enhancement Bonus"

    if name == "Nearly Finished":
        return "Nearly Finished (choose one)"

    if name == "Almost There":
        return "Almost There (choose one)"

    if name == "Attuned to Heroism":
        return "Attuned to Heroism"

    # Generic: use template name as text
    if params:
        return f"{name} {' '.join(params)}"

    return name


class Command(BaseCommand):
    help = (
        "POC: show raw wikitext alongside expanded wikitext "
        "and current HTML tree for comparison."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "item_title",
            help=(
                "Wiki page title, e.g. "
                "'Item:Collective Sight'"
            ),
        )
        parser.add_argument(
            "--out",
            default="poc_expand_output.html",
            help="Output HTML file path.",
        )

    def handle(self, *args, **options):
        item_title = options["item_title"]
        out_path = Path(options["out"])

        # ── Load stored data ─────────────────────────────
        raw_data = self._load_raw(item_title)
        item_data = self._load_item(item_title)

        if not raw_data and not item_data:
            self.stderr.write(
                f"Could not find stored data for "
                f"{item_title}. Checked wiki_snapshot/raw/ "
                f"and wiki_snapshot/items/."
            )
            return

        wikitext = (
            (raw_data or item_data)
            .get("wikitext", "")
        )
        stored_tree = (
            item_data or raw_data or {}
        ).get("enchantments", [])

        self.stdout.write(
            f"Wikitext: {len(wikitext)} chars"
        )
        self.stdout.write(
            f"Stored tree: {len(stored_tree)} top-level "
            f"nodes"
        )

        # ── Try expandtemplates via API ──────────────────
        expanded_wikitext = None
        api_error = None

        try:
            self.stdout.write(
                "Trying action=expandtemplates..."
            )
            api = WikiAPI()
            api.stdout = self.stdout
            api.stderr = self.stderr

            data = api.api_request({
                "action": "expandtemplates",
                "text": wikitext,
                "prop": "wikitext",
                "format": "json",
                "formatversion": "2",
            })

            expanded_wikitext = (
                data.get("expandtemplates", {})
                .get("wikitext", "")
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Expanded: {len(expanded_wikitext)} "
                    f"chars"
                )
            )

        except Exception as exc:
            api_error = str(exc)
            self.stdout.write(
                self.style.WARNING(
                    f"expandtemplates failed: {exc}"
                )
            )

        # ── Manual expansion attempt ─────────────────────
        # Try to identify template calls in wikitext
        template_calls = self._find_template_calls(
            wikitext
        )

        self.stdout.write(
            f"Found {len(template_calls)} template calls "
            f"in wikitext"
        )

        # ── Parse wikitext into tree ─────────────────────
        self.stdout.write(
            "Parsing wikitext into tree structure..."
        )

        wikitext_tree = wikitext_to_tree(wikitext)

        self.stdout.write(
            f"  wikitext tree: {len(wikitext_tree)} "
            f"top-level nodes"
        )

        for node in wikitext_tree:
            child_count = len(node.get("children", []))
            self.stdout.write(
                f"    - {node['text']}"
                f"{f' ({child_count} children)' if child_count else ''}"
            )

        # ── Generate output ──────────────────────────────
        output = self._build_html(
            item_title=item_title,
            wikitext=wikitext,
            expanded_wikitext=expanded_wikitext,
            api_error=api_error,
            template_calls=template_calls,
            wikitext_tree=wikitext_tree,
            stored_tree=stored_tree,
        )

        out_path.write_text(output, encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(
                f"Written to {out_path}"
            )
        )

    def _find_template_calls(self, wikitext):
        """Find all {{Template|...}} calls in wikitext,
        properly handling nesting via a stack."""
        calls = []
        stack = []
        i = 0

        while i < len(wikitext):
            if wikitext[i:i+2] == "{{":
                stack.append(i)
                i += 2
            elif wikitext[i:i+2] == "}}":
                if stack:
                    start = stack.pop()
                    call = wikitext[start:i+2]
                    inner = call[2:-2].strip()
                    pipe = inner.find("|")
                    name = (
                        inner[:pipe].strip()
                        if pipe != -1
                        else inner.strip()
                    )
                    calls.append({
                        "start": start,
                        "end": i + 2,
                        "name": name,
                        "full": call,
                    })
                i += 2
            else:
                i += 1

        return calls

    def _load_raw(self, item_title):
        filename = (
            item_title.replace(":", "_")
            .replace("/", "_")
        )
        path = Path("wiki_snapshot/raw") / f"{filename}.json"

        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)

        return None

    def _load_item(self, item_title):
        filename = (
            item_title.replace(":", "_")
            .replace("/", "_")
        )
        path = Path("wiki_snapshot/items") / f"{filename}.json"

        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)

        return None

    def _build_html(
        self,
        item_title,
        wikitext,
        expanded_wikitext,
        api_error,
        template_calls,
        wikitext_tree,
        stored_tree,
    ):
        esc = html_mod.escape

        # Build template calls table
        calls_rows = ""

        for call in template_calls:
            name = call["name"]
            group = ENCHANTMENT_TEMPLATES.get(name, "")
            group_badge = (
                f' <span style="color:#0f0">→ {esc(group)}</span>'
                if group
                else ""
            )
            calls_rows += f"""<tr>
  <td>{call["start"]}</td>
  <td><code>{esc(name)}</code>{group_badge}</td>
  <td class="code-cell">{esc(call["full"][:120])}</td>
</tr>
"""

        expanded_section = ""

        if expanded_wikitext:
            expanded_section = f"""
<div class="section">
  <h2>Expanded Wikitext (from API)</h2>
  <pre class="code">{esc(expanded_wikitext)}</pre>
</div>
"""
        elif api_error:
            expanded_section = f"""
<div class="diff-note">
  <strong>expandtemplates API failed:</strong> {esc(api_error)}
  <br>This endpoint may be disabled on this wiki.
  The wikitext below shows the raw template calls.
</div>
"""

        stored_section = ""

        if stored_tree:
            stored_section = f"""
<div class="section">
  <h2>Current Stored Tree (from HTML parsing)</h2>
  <pre class="code">{esc(json.dumps(stored_tree, indent=2))}</pre>
</div>
"""

        wikitext_tree_section = ""

        if wikitext_tree:
            wikitext_tree_section = f"""
<div class="section">
  <h2>Wikitext → Tree (parsed from raw wikitext)</h2>
  <pre class="code">{esc(json.dumps(wikitext_tree, indent=2))}</pre>
</div>
"""

        # Highlight template calls in wikitext
        highlighted_wikitext = esc(wikitext)

        for call in reversed(template_calls):
            name = call["name"]
            group = ENCHANTMENT_TEMPLATES.get(name, "")
            color = "#ff6b6b" if not group else "#00ff88"
            badge = f" [{group}]" if group else ""

            highlighted = (
                f'<span style="color:{color};'
                f'font-weight:bold">'
                f"{esc(call['full'][:80])}"
                f"{'...' if len(call['full']) > 80 else ''}"
                f"{badge}</span>"
            )

            highlighted_wikitext = (
                highlighted_wikitext[:call["start"]]
                + highlighted
                + highlighted_wikitext[call["end"]:]
            )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>POC: Wikitext Expansion — {esc(item_title)}</title>
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
    margin-bottom: 10px;
    border-bottom: 2px solid #00d4ff;
    padding-bottom: 10px;
  }}
  h2 {{
    color: #ff6b6b;
    margin: 20px 0 10px 0;
  }}
  .meta {{
    color: #888;
    margin-bottom: 20px;
  }}
  .section {{
    margin-bottom: 30px;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 15px;
    background: #16213e;
  }}
  .section h2 {{
    margin-top: 0;
  }}
  .code {{
    background: #0f0f23;
    border: 1px solid #333;
    border-radius: 4px;
    padding: 12px;
    overflow-x: auto;
    font-size: 13px;
    line-height: 1.4;
    white-space: pre-wrap;
    word-wrap: break-word;
    max-height: 600px;
    overflow-y: auto;
  }}
  .html-preview {{
    background: #fff;
    color: #000;
    border: 1px solid #333;
    border-radius: 4px;
    padding: 12px;
    max-height: 600px;
    overflow-y: auto;
  }}
  .diff-note {{
    background: #2a1a00;
    border: 1px solid #664d00;
    border-radius: 4px;
    padding: 10px;
    margin: 10px 0;
    color: #ffcc00;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 10px 0;
  }}
  th, td {{
    padding: 6px 10px;
    border: 1px solid #444;
    text-align: left;
    font-size: 13px;
  }}
  th {{
    background: #0f0f23;
    color: #00d4ff;
  }}
  tr:nth-child(even) {{
    background: rgba(255,255,255,0.03);
  }}
  code {{
    background: #0f0f23;
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 12px;
  }}
  .code-cell {{
    max-width: 400px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
</style>
</head>
<body>
  <h1>POC: Wikitext → Tree</h1>
  <div class="meta">
    {esc(item_title)} &middot;
    {len(wikitext)} chars wikitext &middot;
    {len(stored_tree)} tree nodes &middot;
    {len(template_calls)} template calls found
  </div>

  <div class="section">
    <h2>1. Raw Wikitext</h2>
    <pre class="code">{esc(wikitext)}</pre>
  </div>

  <div class="section">
    <h2>2. Template Calls Detected</h2>
    <p style="color:#888;margin-bottom:10px">
      Green = known enchantment template with group.
      Red = unrecognized template.
    </p>
    <table>
      <tr>
        <th>Position</th>
        <th>Template Name</th>
        <th>Full Call</th>
      </tr>
      {calls_rows}
    </table>
  </div>

  <div class="section">
    <h2>3. Wikitext with Templates Highlighted</h2>
    <pre class="code">{highlighted_wikitext}</pre>
  </div>

  {expanded_section}

  {wikitext_tree_section}

  {stored_section}
</body>
</html>"""
