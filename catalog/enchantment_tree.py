"""Turn a parsed Enchantments tree into searchable enchantment rows.

The tree produced by :mod:`catalog.enchantment_html` keeps the wiki's
nested structure (containers, tier groups, alternatives, upgrade
arrows). This module walks it and emits one :class:`Row` per searchable
effect so items stay findable the way they were before, while the tree
itself keeps driving the nested display.

Node kinds:

* Effect leaf (no children)          -> one row (concept + value)
* Plain container                    -> one concept row + child rows
* Tier header ("... Tier N")         -> display only; children get tier N
* Upgrade container ("Upgradeable
  Item (...)" / "Suppressed Power")  -> one concept row + children get
                                        tier 2 (the wiki gives no tier
                                        headers for these, but the
                                        children ARE the upgraded form)
* Alternative wrapper ("can be any
  one of these N sets")              -> one wrapper row + one row per
                                        option, marked possible=True
"""

import re
from dataclasses import dataclass
from urllib.parse import unquote

# "A Mysterious Effect - can be any one of these 4 sets:"
_ALTERNATIVE_RE = re.compile(
    r"can be any one of these \d+ sets",
    re.IGNORECASE,
)

# "Attuned by Heroism: Tier 1" -> tier 1 (base), "Tier 2" -> upgrade 2.
_TIER_RE = re.compile(r"Tier\s+(\d+)", re.IGNORECASE)

# "Upgradeable Item (Stormreaver)" / "Suppressed Power": CraftingEffects
# containers that hold the item's upgraded form but, unlike the Vaults
# chains, render no tier headers. Their children are upgrades.
_UPGRADE_CONTAINER_RE = re.compile(
    r"^(?:Upgradeable Item|Suppressed Power)\b",
    re.IGNORECASE,
)

# The wiki renders upgrade arrows as "X \u2192 Y".
_ARROW_RE = re.compile(r"\s*(?:\u2192|->)\s*")

# Links are "/page/Page_Title" or "/page/Page_Title#Anchor".
_PAGE_PATH_RE = re.compile(r"^/page/([^#]+)(?:#(.+))?$")

_ADD_PREFIX_RE = re.compile(r"^\s*Adds\s+", re.IGNORECASE)

_WS_RE = re.compile(r"\s+")


@dataclass
class Row:
    """A searchable enchantment row on one item.

    ``concept`` is the Enchantment name (dropdown value), ``value`` the
    value shown in the value dropdown, ``display_text`` the verbatim
    wiki text, ``tier`` None for base or N for an N-th upgrade, and
    ``possible`` True when the row is one mutually-exclusive option.
    """

    concept: str
    value: str
    detail: str
    display_text: str
    tier: int | None
    possible: bool


def _clean_text(text):
    if not text:
        return ""

    text = text.replace("\u00a0", " ")
    text = _ADD_PREFIX_RE.sub("", text)
    text = _WS_RE.sub(" ", text)

    return text.strip()


def _concept_from_target(target):
    """Return the wiki page/section name a link points to, or None."""
    if not target:
        return None

    match = _PAGE_PATH_RE.match(target)

    if not match:
        return None

    page = unquote(match.group(1)).replace("_", " ")
    anchor = match.group(2)

    if anchor:
        # "/page/Named_item_sets#Planar_Conflux" names a set, not the
        # "Named item sets" page itself.
        return unquote(anchor).replace("_", " ").strip()

    return page.strip()


def _concept_from_link(link):
    if not link:
        return None

    concept = _concept_from_target(link.get("target"))

    if concept:
        return concept

    return _clean_text(link.get("text")) or None


def _concept_from_text(text):
    return text


def _split_value(text, concept):
    """Return ``text`` with ``concept`` removed (case-insensitive)."""
    if not concept:
        return ""

    lowered = text.casefold()
    needle = concept.casefold()
    index = lowered.find(needle)

    if index < 0:
        return ""

    value = (text[:index] + text[index + len(concept):]).strip()
    value = _WS_RE.sub(" ", value)
    # "Rune Arm Imbue: Cold IV" -> "Cold IV" (the page title sits
    # before a colon that is not part of the value).
    value = re.sub(r"^\s*:\s*", "", value)

    return value


# Trailing "+3", "75%", "+2 or +4", or a Roman numeral (Spell Penetration
# I) that names the value when the concept's page title does not appear
# in the rendered text ("Dodge +8%" links to page "Dodge bonus").
_TAIL_VALUE_RE = re.compile(
    r"([+-]?\d+(?:\.\d+)?\s*%?"
    r"(?:\s+or\s+[+-]?\d+(?:\.\d+)?\s*%?)?|[IVXL]+)\s*$"
)


def _tail_value(text):
    if not text:
        return ""

    match = _TAIL_VALUE_RE.search(text)

    if match:
        return _WS_RE.sub(" ", match.group(1)).strip()

    return ""


def _leaf_rows(node, tier, clean, links, concept):
    text = node.get("text") or ""
    display = _clean_text(text)

    parts = _ARROW_RE.split(clean)

    if len(parts) > 1:
        # "X -> Y": index only the after part; the tree keeps the full
        # arrow for display.
        after = parts[-1]
        link = links[-1] if links else None
        after_concept = _concept_from_link(link) if link else None
        value = _split_value(after, after_concept)

        if not value and link:
            anchor = _clean_text(link.get("text"))

            if anchor and after.casefold().startswith(anchor.casefold()):
                value = _split_value(after, anchor)

        if not value:
            value = _tail_value(after)

        return [
            Row(
                after_concept or _concept_from_text(after),
                value,
                "",
                display,
                tier,
                False,
            )
        ]

    if len(links) > 1:
        # One line with several linked effects ("Dexterity +3 and
        # Resistance +3"): one row per link.
        rows = []

        for link in links:
            link_concept = _concept_from_link(link)
            anchor = _clean_text(link.get("text"))
            value = _split_value(anchor, link_concept)

            if not value:
                value = _tail_value(anchor)

            if not value:
                value = _tail_value(clean)

            rows.append(
                Row(
                    link_concept or anchor or clean,
                    value,
                    "",
                    anchor or clean,
                    tier,
                    False,
                )
            )

        return rows

    if concept:
        value = _split_value(clean, concept)

        if not value and links:
            anchor = _clean_text(links[0].get("text"))

            if anchor and clean.casefold().startswith(anchor.casefold()):
                value = _split_value(clean, anchor)

        if not value:
            value = _tail_value(clean)

        return [Row(concept, value, "", display, tier, False)]

    return [Row(_concept_from_text(clean), "", "", display, tier, False)]


def _node_rows(node, tier, possible):
    text = node.get("text") or ""
    children = node.get("children") or []
    links = node.get("links") or []
    clean = _clean_text(text)
    concept = _concept_from_link(links[0]) if links else None

    if not children:
        rows = _leaf_rows(node, tier, clean, links, concept)

        for row in rows:
            row.possible = row.possible or possible

        return rows

    if _ALTERNATIVE_RE.search(text):
        rows = [Row(concept or clean, "", "", clean, tier, possible)]

        for child in children:
            rows.extend(_node_rows(child, tier, True))

        return rows

    tier_match = _TIER_RE.search(text)

    if tier_match:
        child_tier = int(tier_match.group(1))

        if child_tier <= 1:
            child_tier = None

        rows = []

        for child in children:
            rows.extend(_node_rows(child, child_tier, possible))

        return rows

    if _UPGRADE_CONTAINER_RE.search(text):
        rows = [Row(concept or clean, "", "", clean, tier, possible)]

        child_tier = tier or 2

        for child in children:
            rows.extend(_node_rows(child, child_tier, possible))

        return rows

    rows = [Row(concept or clean, "", "", clean, tier, possible)]

    for child in children:
        rows.extend(_node_rows(child, tier, possible))

    return rows


def walk_tree(nodes):
    """Return the list of searchable Rows for a parsed tree."""
    rows = []

    for node in nodes:
        rows.extend(_node_rows(node, None, False))

    return rows
