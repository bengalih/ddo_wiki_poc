import json, sys, os
sys.stdout.reconfigure(encoding='utf-8')
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ddo_search.settings")
import django; django.setup()
from catalog.wiki_api import WikiAPI

api = WikiAPI()
data = api.api_request({
    "action": "expandtemplates",
    "title": "Item:Collective Sight",
    "text": "{{:Item:Collective Sight}}",
    "prop": "wikitext",
    "format": "json",
    "formatversion": "2",
})
wt = data.get("expandtemplates", {}).get("wikitext", "")

# Print lines 41-46 in full
for i, line in enumerate(wt.split("\n")):
    if 41 <= i <= 46:
        print(f"L{i}: {line}")
        print()
