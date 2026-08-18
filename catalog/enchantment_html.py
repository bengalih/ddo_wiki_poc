"""Parse the enchantment list of a rendered DDO wiki item page.

The wiki renders an item's enchantments as a nested HTML list inside
the "Enchantments" table cell (newer pages) or an "Enhancements" row
inside the infobox (older pages):

    <td class="bg-color-2" ...>
    <ul>
      <li><span class="popup ..."><a href="/page/Keen" title="Keen">Keen</a>
          <span class="popup tooltip ...">hover text</span></span></li>
      <li><span class="popup ..."><a ...>Attuned to Heroism</a>...</span>
        <ul>
          <li><a ...>Attuned by Heroism: Tier 1</a>
            <ul><li>Adds <a ...>Planar Conflux</a></li></ul>
          </li>
          <li><a ...>Attuned by Heroism: Tier 2</a>
            <ul><li>... +7 -> +8 ...</li></ul>
          </li>
        </ul>
      </li>
      ...
    </ul>
    </td>

This turns that cell into a tree of entries so the app can show items
the way the wiki does (nested tiers, upgrade arrows, augment slots)
instead of reconstructing text from template calls.
"""

import re
from html.parser import HTMLParser

_TOOLTIP_CLASS_RE = re.compile(r"\bpopup\s+tooltip\b")
_DROP_TAGS = {"style", "script"}

_CELL_AFTER_HEADER_RE = re.compile(
    r"<th[^>]*>\s*Enchantments\s*</th>\s*<td[^>]*>(.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)

_CELL_BEFORE_HEADER_RE = re.compile(
    r"<td[^>]*>(.*?)</td>\s*<th[^>]*>\s*Enchantments\s*</th>",
    re.IGNORECASE | re.DOTALL,
)

# Older item pages render the same enchantment list as an "Enhancements"
# row inside the infobox (Template:Named_item & friends) instead of a
# separate "Enchantments" table cell. The list markup (ul/li/popup/
# tooltip spans) is identical, so the same parser handles both.
_LEGACY_CELL_AFTER_HEADER_RE = re.compile(
    r"<th[^>]*>\s*Enhancements\s*</th>\s*<td[^>]*>(.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)

_LEGACY_CELL_BEFORE_HEADER_RE = re.compile(
    r"<td[^>]*>(.*?)</td>\s*<th[^>]*>\s*Enhancements\s*</th>",
    re.IGNORECASE | re.DOTALL,
)


def extract_enchantments_cell(page_html):
    """Return the HTML of the enchantment list cell, or None.

    Matches both the modern "Enchantments" table cell and the legacy
    "Enhancements" infobox row, preferring the modern cell when a page
    renders both.
    """
    match = _CELL_AFTER_HEADER_RE.search(page_html)

    if match:
        return match.group(1)

    match = _CELL_BEFORE_HEADER_RE.search(page_html)

    if match:
        return match.group(1)

    match = _LEGACY_CELL_AFTER_HEADER_RE.search(page_html)

    if match:
        return match.group(1)

    match = _LEGACY_CELL_BEFORE_HEADER_RE.search(page_html)

    if match:
        return match.group(1)

    return None


def _normalize(chunks):
    text = "".join(chunks)
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


class _EnchantmentsParser(HTMLParser):
    """Build a tree of {text, tooltip, children} from a cell's HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tree = []
        self._frames = [(self.tree, None)]
        self._li_stack = []
        self._current_li = None
        self._tooltip_depth = 0
        self._drop_depth = 0
        self._link_stack = []

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_TAGS:
            self._drop_depth += 1
            return

        if self._drop_depth:
            return

        attrs = dict(attrs)
        css_class = attrs.get("class", "")

        if tag == "ul":
            parent_li = (
                self._li_stack[-1] if self._li_stack else None
            )

            child_list = (
                parent_li["children"]
                if parent_li is not None
                else self.tree
            )

            self._frames.append((child_list, parent_li))
            return

        if tag == "li":
            node = {
                "text": [],
                "tooltip": [],
                "children": [],
                "links": [],
            }

            self._frames[-1][0].append(node)
            self._current_li = node
            self._li_stack.append(node)
            return

        if tag == "a" and not self._tooltip_depth:
            self._link_stack.append(
                {
                    "href": attrs.get("href"),
                    "title": attrs.get("title"),
                    "text": [],
                }
            )
            return

        if _TOOLTIP_CLASS_RE.search(css_class):
            self._tooltip_depth += 1

    def handle_startendtag(self, tag, attrs):
        if tag in _DROP_TAGS:
            return

        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _DROP_TAGS:
            if self._drop_depth:
                self._drop_depth -= 1

            return

        if self._drop_depth:
            return

        if tag == "ul":
            if len(self._frames) > 1:
                self._frames.pop()

            return

        if tag == "li":
            if self._li_stack:
                self._li_stack.pop()

            self._current_li = (
                self._li_stack[-1] if self._li_stack else None
            )

            return

        if tag == "a":
            if self._link_stack and not self._tooltip_depth:
                link = self._link_stack.pop()
                link["text"] = _normalize(link["text"])

                if self._current_li is not None and (
                    link["href"] or link["title"] or link["text"]
                ):
                    self._current_li["links"].append(link)

            return

        if tag == "span" and self._tooltip_depth:
            self._tooltip_depth -= 1

    def handle_data(self, data):
        if self._drop_depth or self._current_li is None:
            return

        if self._link_stack and not self._tooltip_depth:
            self._link_stack[-1]["text"].append(data)

        if self._tooltip_depth:
            self._current_li["tooltip"].append(data)
        else:
            self._current_li["text"].append(data)


def parse_enchantments_cell(cell_html):
    """Turn an Enchantments cell's HTML into a list of entry dicts.

    Each entry is:
        {"text": str, "tooltip": str or None, "children": [...]}
    """
    parser = _EnchantmentsParser()
    parser.feed(cell_html)
    parser.close()

    return _finalize(parser.tree)


def parse_item_page(page_html):
    """Extract and parse the Enchantments cell of a full page render.

    Returns {"enchantments": [...]}, or None when the page has no
    Enchantments cell.
    """
    cell_html = extract_enchantments_cell(page_html)

    if cell_html is None:
        return None

    return {
        "enchantments": parse_enchantments_cell(cell_html),
    }


def _finalize(nodes):
    cleaned = []

    for node in nodes:
        text = _normalize(node["text"])
        tooltip = _normalize(node["tooltip"])
        children = _finalize(node["children"])
        links = node.get("links", [])

        if not text and not children and not links:
            continue

        entry = {
            "text": text,
            "tooltip": tooltip or None,
            "children": children,
        }

        if links:
            entry["links"] = [
                {
                    "target": link.get("href"),
                    "title": link.get("title"),
                    "text": link.get("text"),
                }
                for link in links
            ]

        cleaned.append(entry)

    return cleaned
