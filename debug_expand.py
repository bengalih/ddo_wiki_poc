import json
import re

import django
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ddo_search.settings")
django.setup()

from catalog.wiki_api import WikiAPI
api = WikiAPI()

data = api.api_request({
    "action": "expandtemplates",
    "title": "Item:Agony, the Knife in the Dark",
    "text": "{{:Item:Agony, the Knife in the Dark}}",
    "prop": "wikitext",
    "format": "json",
    "formatversion": "2",
})
wt = data.get("expandtemplates", {}).get("wikitext", "")

# Find the Attuned/Heroism section
for i, line in enumerate(wt.split("\n")):
    if "ttuned" in line or "Special" in line or "Heroism" in line:
        print(f"L{i}: {repr(line)}")

print("\n--- Enchantments section ---")
for i, line in enumerate(wt.split("\n")):
    if "Enchantment" in line or "enhancement" in line.lower():
        # Print this line and the next 30
        lines = wt.split("\n")
        for j in range(i, min(i + 40, len(lines))):
            print(f"L{j}: {repr(lines[j])}")
        break
