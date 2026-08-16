import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings


# Renders are cached on disk, inside the snapshot directory, so a
# database wipe never loses them: they cost wiki round-trips and must
# survive like the raw wikitext pages do.
#
#   wiki_snapshot/renders/<sha1(render_key)>.json
#
# Each file holds the wiki's raw output plus the fields parsed from it.
# The whole store is loaded into an in-memory dict on first use because
# lookup_render() fires once per template per item during imports.
#
# Most templates render identically everywhere and are keyed by the
# normalized template call alone. A few templates are page-context
# dependent ({{#switch:{{FULLPAGENAMEE}}}} and friends), so those are
# rendered once per page and keyed by the call PLUS the page title.
# See composite_key().

_current_dir = None
_cache = {}
_cache_dir = None

_FIELD_DEFAULTS = {
    "canonical_name": "",
    "display_text": "",
    "value": "",
    "detail": "",
    "raw_html": "",
}


def default_dir():
    return Path(settings.BASE_DIR) / "wiki_snapshot"


def current_dir():
    return _current_dir or default_dir()


def set_current_dir(path):
    global _current_dir
    _current_dir = Path(path)
    clear_cache()


def clear_current_dir():
    global _current_dir
    _current_dir = None
    clear_cache()


def renders_dir(path=None):
    base = Path(path) if path is not None else current_dir()
    return base / "renders"


def _filename(key):
    digest = hashlib.sha1(
        key.encode("utf-8")
    ).hexdigest()
    return f"{digest}.json"


# A page-context render is keyed by call + title so two items
# using the same template call do not collide. The separator is a
# control character that can never appear in a template call or a
# MediaWiki title.
def composite_key(template_call, page_title=None):
    if not page_title:
        return template_call

    return f"{template_call}\u0001{page_title}"


def _load_into_cache(path=None):
    global _cache_dir

    directory = renders_dir(path)

    entries = {}

    if directory.is_dir():
        for file_path in directory.glob("*.json"):
            try:
                with file_path.open(
                    "r",
                    encoding="utf-8",
                ) as f:
                    entry = json.load(f)
            except (OSError, ValueError):
                continue

            call = entry.get("template_call")

            if call:
                title = entry.get("page_title")
                entries[
                    composite_key(call, title)
                ] = entry

    _cache.clear()
    _cache.update(entries)
    _cache_dir = directory


def _ensure_cache(path=None):
    directory = renders_dir(path)

    if _cache_dir != directory:
        _load_into_cache(path)


def clear_cache():
    global _cache, _cache_dir
    _cache = {}
    _cache_dir = None


def get(template_call, path=None, title=None):
    _ensure_cache(path)
    entry = _cache.get(
        composite_key(template_call, title)
    )

    if entry is None:
        return None

    merged = {**_FIELD_DEFAULTS, **entry}

    return SimpleNamespace(**merged)


def keys(path=None):
    _ensure_cache(path)
    return set(_cache)


def entries(path=None):
    _ensure_cache(path)
    return list(_cache.values())


def save(entry, path=None):
    call = entry.get("template_call")

    if not call:
        return

    page_title = entry.get("page_title")
    key = composite_key(call, page_title)

    directory = renders_dir(path)
    directory.mkdir(parents=True, exist_ok=True)

    file_path = directory / _filename(key)
    temp_file = file_path.with_suffix(".json.tmp")

    payload = dict(entry)
    payload["rendered_at"] = (
        datetime.now().astimezone().isoformat()
    )

    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    os.replace(temp_file, file_path)

    _ensure_cache(path)
    _cache[key] = payload


def delete(template_call, path=None):
    _ensure_cache(path)
    key = composite_key(template_call)

    if key in _cache:
        del _cache[key]

    file_path = renders_dir(path) / _filename(key)
    file_path.unlink(missing_ok=True)


def delete_all(path=None):
    directory = renders_dir(path)

    if directory.is_dir():
        for file_path in directory.iterdir():
            file_path.unlink(missing_ok=True)

    clear_cache()
