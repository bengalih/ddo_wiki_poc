"""Extract item infobox metadata from a rendered item page.

The fetch pipeline stores each item page's rendered HTML; the old
importer read the wikitext infobox instead. The item infobox renders
as a ``<table>`` with ``<th>`` label / ``<td>`` value rows, and the
label wording varies by item kind: "Type", "Item Type", "Weapon
Type", "Armor Type", "Shield Type"; "Minimum Level", "Minimum level",
"Minimum Level:". Some pages put a "History" box before the infobox,
so every table is scanned and the first one holding a matching row
wins. "Damage and Type" is a weapon-stat row, not the item type, and
is deliberately not matched.

Weapons: "Weapon Type" is "Bastard Sword / Slashing weapons"; the
searchable type is just "Bastard Sword" and the weapon class goes into
``weapon_class``. Armor: the raw "Armor Type" value is kept and the
searchable type is derived by ``classify_armor`` from the page's "Feat
Requirement" row first (the wiki already accounts for material quirks
like mithral "one type lighter"), with "Docent"/"Cloth" split off the
armor type when the feat is "None". Shields keep the raw shield type.
A bare "Type"/"Item Type" value keeps the subtype part ("Clothing /
Belt" -> "Belt"). Cosmetic items store the cosmetic subtype ("Armor",
"Helm", "Weapon", etc.). ``item_class`` holds the category half
separately (Weapon/Armor/Shield/Cosmetic, or the "Type" cell prefix
like Clothing/Jewelry/Quiver).
"""

import html as html_module
import re

_LABEL_PATTERN = re.compile(
    r"^(?:type|item type|weapon type|armor type|shield type"
    r"|feat requirement|proficiency class|proficiency|material"
    r"|minimum level|min level)$"
)

_TYPE_LABELS = (
    "type",
    "item type",
    "weapon type",
    "armor type",
    "shield type",
)

# Canonical armor-type -> armor class, used only when the page's
# Feat Requirement row is missing or has no class word (e.g. the
# generic "Armor Proficiency" on +1 Starter gear).
_ARMOR_CLASS_BY_TYPE = {
    "docent": "Docent",
    "cloth": "Cloth",
    "cloth armor": "Cloth",
    "clothing": "Cloth",
    "cosmetic armor": "Cloth",
    "outfit": "Cloth",
    "robe": "Cloth",
    "starter rags": "Cloth",
    "light": "Light",
    "light armor": "Light",
    "leather": "Light",
    "leather armor": "Light",
    "studded leather": "Light",
    "chain shirt": "Light",
    "hide": "Light",
    "medium": "Medium",
    "medium armor": "Medium",
    "breastplate": "Medium",
    "breastplate / scalemail": "Medium",
    "scalemail": "Medium",
    "brigandine": "Medium",
    "chainmail": "Medium",
    "banded mail": "Medium",
    "splint mail": "Medium",
    "heavy": "Heavy",
    "heavy armor": "Heavy",
    "full plate": "Heavy",
    "fullplate": "Heavy",
    "half plate": "Heavy",
    "platemail": "Heavy",
}

_STYLE_BLOCK = re.compile(
    r"<style\b[^>]*>.*?</style>",
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_BLOCK = re.compile(
    r"<script\b[^>]*>.*?</script>",
    re.IGNORECASE | re.DOTALL,
)
_COSMETIC_SUBTYPE = re.compile(
    r"cosmetic\s+([a-z0-9]+(?:\s+[a-z0-9]+)*)",
    re.IGNORECASE,
)
_TAG_OPEN = re.compile(
    r"<(span|div)\b([^>]*)>",
    re.IGNORECASE,
)
_CLASS_ATTR = re.compile(
    r'\bclass\s*=\s*(["\'])(.*?)\1',
    re.IGNORECASE | re.DOTALL,
)


def _clean_fragment(fragment):
    text = html_module.unescape(fragment)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def _is_tooltip_element(attrs):
    match = _CLASS_ATTR.search(attrs)

    if not match:
        return False

    # Token check: the hover box is "class='popup tooltip ...'";
    # "popup has&#95;tooltip ..." (the wrapper) must not match.
    return "tooltip" in match.group(2).split()


def _remove_tooltip_blocks(fragment):
    """Strip popup-tooltip spans/divs (the wiki's hover descriptions).

    The Material cell renders as ``<span class="popup ...">Mithral
    <span class="popup tooltip">Mithral: ...description...</span>
    </span>``; the visible value is the text outside the tooltip.
    """

    while True:
        match = _TAG_OPEN.search(fragment)

        while match and not _is_tooltip_element(
            match.group(2)
        ):
            match = _TAG_OPEN.search(
                fragment,
                match.end(),
            )

        if not match:
            return fragment

        tag = match.group(1).lower()
        depth = 1
        index = match.end()
        close_pattern = re.compile(
            rf"<{tag}\b|</{tag}>",
            re.IGNORECASE,
        )

        while depth and index < len(fragment):
            close = close_pattern.search(fragment, index)

            if not close:
                break

            if fragment.startswith("</", close.start()):
                depth -= 1
            else:
                depth += 1

            index = close.end()

        fragment = fragment[: match.start()] + fragment[index:]


def _clean_material(fragment):
    text = _STYLE_BLOCK.sub("", fragment)
    text = _SCRIPT_BLOCK.sub("", text)
    text = _remove_tooltip_blocks(text)
    text = _clean_fragment(text)

    if "unknown material" in text.casefold():
        return ""

    return text


def _normalize_label(label):
    text = _clean_fragment(label)
    text = re.sub(r":\s*$", "", text)

    return text.strip().casefold()


def _tables(page_html):
    start = 0

    while True:
        match = re.search(r"<table\b", page_html[start:])

        if not match:
            return

        begin = start + match.start()
        depth = 1
        index = begin + match.end()

        while depth:
            close = page_html.find("</table>", index)

            if close < 0:
                return

            depth += page_html.count("<table", index, close)
            index = close + len("</table>")
            depth -= 1

        yield page_html[begin:index]
        start = index


def split_weapon_type(value):
    """Split "Bastard Sword / Slashing weapons" into
    ("Bastard Sword", "Slashing weapons"). No separator yields
    ("<value>", "").
    """

    head, separator, tail = value.partition(" / ")

    if not separator:
        return value, ""

    return head.strip(), tail.strip()


def _cosmetic_subtype(value):
    """Slot subtype from a cosmetic "Type"/"Item Type" value.

    "Clothing / Cosmetic Helm" -> "Helm", "Clothing / Cosmetic
    cloak" -> "Cloak". Returns None when the value is not cosmetic
    or carries no subtype.
    """

    if not value or "cosmetic" not in value.casefold():
        return None

    match = _COSMETIC_SUBTYPE.search(value)

    if not match:
        return None

    subtype = match.group(1).strip()

    if not subtype:
        return None

    return subtype.capitalize()


def classify_armor(feat_requirement, armor_type, material=""):
    """Return the armor class for the item, or None when unknown.

    Feat Requirement first: "Light/Medium/Heavy Armor Proficiency" is
    the wiki's per-item answer and already bakes in material effects
    (e.g. mithral Chainmail requires Light). An explicit "None" means
    Cloth unless the armor type is a Docent. A missing feat row or a
    generic "Armor Proficiency" (the +1 Starter items) falls back to
    the canonical armor-type map, then to material hints; the Starter
    pages are "Unknown Material" with no armor type, so they come back
    None.
    """

    armor_type_norm = (armor_type or "").strip().casefold()
    feat = (feat_requirement or "").strip().casefold()

    if armor_type_norm == "docent":
        return "Docent"

    if feat == "heavy armor proficiency":
        return "Heavy"

    if feat == "medium armor proficiency":
        return "Medium"

    if feat == "light armor proficiency":
        return "Light"

    if feat == "none":
        return "Cloth"

    if armor_type_norm in _ARMOR_CLASS_BY_TYPE:
        return _ARMOR_CLASS_BY_TYPE[armor_type_norm]

    material_norm = (material or "").strip().casefold()

    if "cloth" in material_norm:
        return "Cloth"

    if "leather" in material_norm:
        return "Light"

    return None


# Equipment slot per the wiki's Equipment_slot page. The category
# decides the slot for Armor/Weapon/Shield/Quiver; for the "Type" rows
# (Clothing/Jewelry/...) the subtype does. Cosmetic items map to the
# wiki's five cosmetic slots.
_COSMETIC_SLOT = {
    "armor": "Armor",
    "helm": "Headwear",
    "headwear": "Headwear",
    "cloak": "Cloak",
    "shield": "Off Hand",
    "weapon": "Main Hand",
}

_SLOT_BY_TYPE = {
    "cloak": "Back",
    "boots": "Feet",
    "gloves": "Hand",
    "helm": "Head",
    "helmet": "Head",
    "cowl": "Head",
    "belt": "Waist",
    "goggles": "Eye",
    "ring": "Finger",
    "necklace": "Neck",
    "trinket": "Trinket",
    "bracers": "Wrist",
    "bracer": "Wrist",
    "quiver": "Quiver",
    "orb": "Off Hand",
}


def derive_slot(item_type, item_class):
    """Return the item's equipment slot, or "" when unknown.

    Armor (any class) -> Armor, weapons -> Main Hand, shields ->
    Off Hand, quivers -> Quiver. For the wiki's "Type" rows the
    subtype decides (Cloak -> Back, Ring -> Finger, ...), which keeps
    wiki inconsistencies like "Clothing / Bracers" on the right slot.
    Cosmetic items use the wiki's cosmetic slot names. An empty
    item_class (wands, rune arms, starters) yields "".
    """

    if not item_class:
        return ""

    category = item_class.casefold()

    if category == "armor":
        return "Armor"

    if category == "weapon":
        return "Main Hand"

    if category == "shield":
        return "Off Hand"

    if category == "quiver":
        return "Quiver"

    if not item_type:
        return ""

    subtype = item_type.casefold()

    if category == "cosmetic":
        return _COSMETIC_SLOT.get(subtype, "")

    return _SLOT_BY_TYPE.get(subtype, "")


def _extract_from_table(table_html):
    raw = {}
    order = []

    for row in re.findall(
        r"<tr[^>]*>(.*?)</tr>",
        table_html,
        re.DOTALL,
    ):
        pending = None

        for cell in re.findall(
            r"<th[^>]*>(.*?)</th>|<td[^>]*>(.*?)</td>",
            row,
            re.DOTALL,
        ):
            th_inner, td_inner = cell

            if th_inner:
                label = _normalize_label(th_inner)

                if _LABEL_PATTERN.match(label):
                    pending = label
            elif td_inner and pending is not None:
                if pending not in raw:
                    raw[pending] = td_inner
                    order.append(pending)

                pending = None

    return _build_meta(raw, order)


def _build_meta(raw, order):
    meta = {}

    feat_requirement = (
        _clean_fragment(raw["feat requirement"])
        if raw.get("feat requirement")
        else ""
    )

    if feat_requirement:
        meta["feat_requirement"] = feat_requirement

    if raw.get("proficiency class"):
        meta["proficiency_class"] = _clean_fragment(
            raw["proficiency class"]
        )
    elif raw.get("proficiency"):
        meta["proficiency_class"] = _clean_fragment(
            raw["proficiency"]
        )

    material = (
        _clean_material(raw["material"])
        if raw.get("material")
        else ""
    )

    if material:
        meta["material"] = material

    first_type = next(
        (
            label
            for label in order
            if label in _TYPE_LABELS
        ),
        None,
    )

    if first_type == "weapon type":
        weapon_value = _clean_fragment(raw["weapon type"])

        if "cosmetic" in weapon_value.casefold():
            meta["item_kind"] = "Cosmetic"
            meta["item_class"] = "Cosmetic"
            meta["item_type"] = "Weapon"

            _, weapon_class = split_weapon_type(
                weapon_value
            )

            if weapon_class:
                meta["weapon_class"] = weapon_class
        else:
            item_type, weapon_class = split_weapon_type(
                weapon_value
            )

            meta["item_type"] = item_type

            if weapon_class:
                meta["weapon_class"] = weapon_class

            meta["item_kind"] = "Weapon"
            meta["item_class"] = "Weapon"
    elif first_type == "armor type":
        armor_type = _clean_fragment(raw["armor type"])

        meta["armor_type"] = armor_type

        if "cosmetic" in armor_type.casefold():
            meta["item_kind"] = "Cosmetic"
            meta["item_class"] = "Cosmetic"

            cosmetic_type = armor_type.replace(
                "Cosmetic ", ""
            ).strip()

            meta["item_type"] = (
                cosmetic_type or "Armor"
            )
        else:
            meta["item_kind"] = "Armor"
            meta["item_class"] = "Armor"

            classification = classify_armor(
                feat_requirement,
                armor_type,
                material,
            )

            if classification:
                meta["item_type"] = classification
    elif first_type == "shield type":
        shield_type = _clean_fragment(raw["shield type"])

        if "cosmetic" in shield_type.casefold():
            meta["item_kind"] = "Cosmetic"
            meta["item_class"] = "Cosmetic"

            cosmetic_type = shield_type.replace(
                "Cosmetic ", ""
            ).strip()

            meta["item_type"] = (
                cosmetic_type or "Shield"
            )
        else:
            meta["item_type"] = shield_type
            meta["item_kind"] = "Shield"
            meta["item_class"] = "Shield"
    elif first_type in ("type", "item type"):
        item_value = _clean_fragment(raw[first_type])
        subtype = _cosmetic_subtype(item_value)

        if subtype:
            meta["item_type"] = subtype
            meta["item_kind"] = "Cosmetic"
            meta["item_class"] = "Cosmetic"
        else:
            head, separator, tail = item_value.partition(" / ")

            if separator and tail:
                meta["item_type"] = tail.strip()
                meta["item_class"] = head.strip()
            else:
                meta["item_type"] = item_value

    if meta.get("item_class"):
        slot = derive_slot(
            meta.get("item_type", ""),
            meta["item_class"],
        )

        if slot:
            meta["slot"] = slot

    level_raw = raw.get("minimum level") or raw.get(
        "min level"
    )

    if level_raw:
        try:
            meta["minimum_level"] = int(
                _clean_fragment(level_raw)
            )
        except ValueError:
            pass

    return meta


def extract_item_meta(page_html):
    """Return item infobox metadata extracted from the page.

    Keys depend on the item kind and which rows the page renders:
    item_type, item_class, slot, weapon_class, armor_type,
    feat_requirement, proficiency_class, material, item_kind,
    minimum_level. Missing rows are omitted. ``item_type`` is the
    bare searchable type without a category prefix: a weapon name
    ("Bastard Sword"), an armor class ("Light", "Docent"), a shield
    type ("Large Shield"), a cosmetic subtype ("Armor", "Helm"), or
    a split "Type"/"Item Type" subtype ("Belt" from "Clothing / Belt").
    ``item_class`` is the category half (Weapon/Armor/Shield/Cosmetic,
    or the "Type" cell prefix like Clothing/Jewelry/Quiver); items
    without a categorizable type row have no item_class. ``slot``
    is derived from the category/type via ``derive_slot``.
    """

    for table in _tables(page_html):
        meta = _extract_from_table(table)

        if meta:
            return meta

    return {}
