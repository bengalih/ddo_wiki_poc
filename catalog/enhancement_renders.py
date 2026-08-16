import html
import re

from catalog import enhancement_render_store as render_store

_LINK_RE = re.compile(
    r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]"
)

_TOOLTIP_RE = re.compile(
    r'<span class="popup tooltip'
)

_WIKI_MARKUP_RE = re.compile(
    r"'{2,3}"
)

_FILE_RE = re.compile(r"\[\[File:[^\]]*\]\]")

_STYLESHEET_RE = re.compile(
    r"<templatestyles[^>]*/>"
)

_MAGNITUDE_RE = re.compile(
    r"[+-]?\d+(?:\.\d+)?(?:/[1-9]\d*)?\s*%?"
    r"(?:\s+or\s+[+-]?\d+(?:\.\d+)?(?:/[1-9]\d*)?\s*%?)?"
)

_CONTROL_KEYS = {"nocat", "cat", "category"}

_TIER_HEADER_RE = re.compile(
    r"\bTier\s+(\d+)\b",
    re.IGNORECASE,
)

_CATEGORY_LINK_RE = re.compile(
    r"\[\[Category:[^\]]*\]\]",
    re.IGNORECASE,
)

_ARROW_RE = re.compile(r"&rarr;|→")


def split_template_call(call):
    # Split a normalized template call like "{{Spelllore|Acid|III}}"
    # back into (name, parameters). The name keeps its original case
    # and spacing; parameters are returned verbatim so rule handlers
    # (and split_parameters) see exactly what the wiki would.
    inner = call.strip()

    if inner.startswith("{{"):
        inner = inner[2:]

    if inner.endswith("}}"):
        inner = inner[:-2]

    inner = inner.strip()

    if "|" in inner:
        name, params = inner.split("|", 1)
    else:
        name, params = inner, ""

    return name.strip(), params.strip()


def normalize_template_call(raw):
    # MediaWiki ignores spacing and underscores in template
    # names, and named-parameter KEYS are matched case-
    # insensitively, so the same logical call can appear many
    # ways in wikitext:
    #
    #   {{Enhancement_bonus|w|1}}  {{Enhancement bonus|w|1}}
    #   {{SomeTpl|nocat=TRUE}}  {{SomeTpl|NOCAT=TRUE}}
    #
    # Collapse those variants to one canonical key so the wiki
    # is asked about each logical call only once.
    #
    # Parameter VALUES keep their case: templates such as
    # {{Clicky|Nimbus of Light|1|50|50}} render the argument
    # verbatim, and lowercasing it would change the expansion
    # ("nimbus of light" instead of "Nimbus of Light — 50
    # Charges (Recharged/Day:50)").
    text = raw.strip()
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)

    inner = text

    if inner.startswith("{{"):
        inner = inner[2:]

    if inner.endswith("}}"):
        inner = inner[:-2]

    inner = inner.strip()

    if "|" in inner:
        name, params = inner.split("|", 1)
    else:
        name, params = inner, ""

    name = name.strip()

    parts = []

    for part in params.split("|"):
        part = part.strip()

        if not part:
            continue

        if "=" in part:
            key, value = part.split("=", 1)
            key = key.strip().lower()

            if key in _CONTROL_KEYS:
                continue

            part = f"{key}={value.strip()}"
        else:
            part = part.strip()

        parts.append(part)

    if parts:
        return "{{" + name + "|" + "|".join(parts) + "}}"

    return "{{" + name + "}}"


def _strip_tooltip(output):
    # The wiki's popup markup nests the hover tooltip inside the
    # visible span, and visible text can CONTINUE after the
    # tooltip closes:
    #
    #   <span class="popup has_tooltip ...">[[Name|Name]]
    #     <span class="popup tooltip ...">hover body</span>
    #   </span> — 50 Charges (Recharged/Day:50)
    #
    # Only the tooltip span is the hover body; remove every one
    # and keep everything else. A chain line may carry several
    # (one per enhancement compared by an upgrade arrow).
    while True:
        match = _TOOLTIP_RE.search(output)

        if not match:
            return output

        start = match.start()
        depth = 1
        pos = match.end()

        while depth > 0:
            open_index = output.find("<span", pos)
            close_index = output.find("</span>", pos)

            if close_index == -1:
                output = output[:start]
                break

            if open_index != -1 and open_index < close_index:
                depth += 1
                pos = open_index + 5
            else:
                depth -= 1
                pos = close_index + 7

        if close_index == -1:
            return output

        output = output[:start] + output[pos:]


def extract_rendered(output):
    output = _strip_tooltip(output)

    output = _STYLESHEET_RE.sub("", output)
    output = _FILE_RE.sub("", output)

    text = re.sub(r"<[^>]+>", "", output)
    text = html.unescape(text)
    text = _WIKI_MARKUP_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()

    match = _LINK_RE.search(text)

    if not match:
        visible = text

        canonical = _clean_name(visible)
        display = visible
    else:
        target = match.group(1)
        title = (
            match.group(2)
            if match.group(2) is not None
            else match.group(1)
        )

        trailing = text[match.end():]

        canonical = _clean_name(target.split("#", 1)[0])
        display = re.sub(
            r"\s+",
            " ",
            (
                f"{html.unescape(title)} "
                f"{trailing}"
            ),
        ).strip()

    value, detail = _split_render(canonical, display)

    return {
        "name": canonical,
        "display": display,
        "value": value,
        "detail": detail,
    }


def _chain_line_links(output):
    # Extract every [[link]] from one rendered line, in order,
    # with the tooltip/File/category noise stripped first so the
    # plain visible text is all that remains.
    text = _strip_tooltip(output)
    text = _STYLESHEET_RE.sub("", text)
    text = _FILE_RE.sub("", text)
    text = _CATEGORY_LINK_RE.sub("", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = _WIKI_MARKUP_RE.sub("", text)

    return [
        _clean_name(link[0])
        for link in _LINK_RE.findall(text)
    ], text


def decompose_upgrade_chain(output):
    # {{VaultsOfTheArtificersUpgrade}} and similar templates render
    # the item's ENTIRE upgrade path in one block:
    #
    #   * Upgradeable - Tier 1: ...
    #   ** Riposte +4
    #   ** Insightful Combat Mastery +4
    #   * Upgradeable - Tier 2: ...
    #   ** Disable Device +17 -> Disable Device +18
    #   ** Adds Use Magic Device +3
    #
    # A search app must not treat that whole block as one
    # enhancement. Split it into per-tier rows: the Tier 1 bullets
    # are the item's base enhancements (tier=None) and the Tier 2/3
    # bullets are upgrade rows (tier=N) shown only when the user
    # checks "Include upgrades".
    #
    # Some pages (e.g. Mournlode level-4) render a flat bullet list
    # with no tiers at all; each bullet is a base enhancement.
    #
    # Returns None when the output is not an upgrade chain so the
    # caller falls back to the normal single-enhancement handling.
    lines = output.splitlines()

    tiers = []
    flat = []

    for line in lines:
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("**"):
            if not tiers:
                continue

            tiers[-1]["bullets"].append(stripped)
        else:
            tier_match = _TIER_HEADER_RE.search(stripped)

            if tier_match:
                tiers.append(
                    {
                        "tier": int(tier_match.group(1)),
                        "bullets": [],
                    }
                )
            else:
                flat.append(stripped)

    rows = []

    if tiers:
        # Tiered chain. Tier 1 bullets are the item's current
        # (base) enhancements; higher tiers are upgrade rows.
        for tier in tiers:
            tier_number = tier["tier"]
            stored_tier = (
                None if tier_number == 1 else tier_number
            )

            for bullet in tier["bullets"]:
                row = _parse_chain_bullet(bullet, stored_tier)

                if row:
                    rows.append(row)
    elif len(flat) > 1:
        # Flat bullet list: every bullet is a base enhancement.
        # Only multi-bullet output is an upgrade template block;
        # a lone bullet is an ordinary single enhancement that
        # extract_rendered already handles.
        for bullet in flat:
            row = _parse_chain_bullet(bullet, None)

            if row:
                rows.append(row)

    rows = [row for row in rows if row]

    if not rows:
        return None

    return rows


def _parse_chain_bullet(bullet, tier):
    links, text = _chain_line_links(bullet)

    if not links:
        return None

    # "** Adds Use Magic Device +3" - the added enhancement.
    text = text.strip()
    text = re.sub(r"^Adds\s+", "", text, re.IGNORECASE)

    # An upgrade bullet may compare old and new ("+17 -> +18").
    # The enhancement the item GAINS at this tier is the final
    # link, so prefer it over the pre-upgrade value.
    arrow_parts = _ARROW_RE.split(text)

    if len(arrow_parts) > 1:
        text = arrow_parts[-1].strip()

    match = _LINK_RE.search(text)

    if not match:
        return None

    title = (
        match.group(2)
        if match.group(2) is not None
        else match.group(1)
    )

    trailing = text[match.end():].strip()

    canonical = _clean_name(match.group(1).split("#", 1)[0])
    display = re.sub(
        r"\s+",
        " ",
        f"{html.unescape(title)} {trailing}",
    ).strip()

    value, detail = _split_render(canonical, display)

    return {
        "name": canonical,
        "value": value,
        "detail": detail,
        "display_text": display,
        "tier": tier,
    }


def _split_render(canonical, display):
    # Split the rendered text into a magnitude (for the search
    # filter's value dropdown) and a detail (bonus types such
    # as Insightful/Quality), e.g.:
    #
    #   Insightful Sheltering +9  -> value +9, detail Insightful
    #   Fire Absorption +26%      -> value +26%, detail Fire Absorption
    #   Blue Augment Slot         -> value Blue, detail Slot
    #   Improved Demonic Shield   -> value Improved, detail ""
    display = display.strip()

    if not display:
        return "", ""

    canonical_name = canonical.strip().lower()
    idx = display.lower().find(canonical_name)

    if idx != -1:
        prefix = display[:idx].strip()
        suffix = display[idx + len(canonical):].strip()
    else:
        match = _MAGNITUDE_RE.search(display)

        if match:
            value = match.group(0).strip()
            prefix = display[:match.start()].strip()
            suffix = display[match.end():].strip()
            detail = f"{prefix} {suffix}".strip()

            return value, detail

        return display, ""

    match = _MAGNITUDE_RE.match(suffix)

    if match:
        value = match.group(0).strip()
        rest = suffix[match.end():].strip()
        detail = f"{prefix} {rest}".strip()

        return value, detail

    # Otherwise the value is the leading word before or after the
    # name: "Blue" in "Blue Augment Slot", "Greater" in "Greater
    # Acid Torrent". Only numeric or alphabetic tokens count;
    # punctuation such as the em dash in "Nimbus of Light — 50
    # Charges (Recharged/Day:50)" is not a searchable value.

    def _leading_word(boundary):
        parts = boundary.split(None, 1)
        token = parts[0] if parts else ""

        if not (_MAGNITUDE_RE.fullmatch(token) or token.isalpha()):
            return None

        return token, (parts[1] if len(parts) > 1 else "")

    if prefix:
        extracted = _leading_word(prefix)

        if extracted is not None:
            value, detail = extracted

            if suffix:
                detail = f"{detail} {suffix}".strip()

            return value, detail

    if suffix:
        extracted = _leading_word(suffix)

        if extracted is not None:
            value, detail = extracted

            return value, detail

    return "", ""


def _clean_name(value):
    value = html.unescape(value)
    value = value.split("#", 1)[0]

    # A leading colon on a link target is MediaWiki link syntax,
    # not part of the page title: templates such as {{Mat|...}}
    # emit "[[:Adamantine|Adamantine]]" (the colon forces a main-
    # namespace link). Strip exactly one so the canonical name
    # matches what the page is really called.
    if value.startswith(":"):
        value = value[1:]

    return re.sub(r"\s+", " ", value).strip()


def lookup_render(template_call, title=None):
    key = normalize_template_call(template_call)

    # Page-context templates ({{#switch:{{FULLPAGENAMEE}}}}) were
    # rendered once per page under a key that includes the title.
    # Prefer that render when we know which page we are on.
    if title:
        render = render_store.get(
            key,
            title=title,
        )

        if render is not None:
            return render

    return render_store.get(key)
