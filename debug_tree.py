import json, sys, os, re
sys.stdout.reconfigure(encoding='utf-8')
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ddo_search.settings")
import django; django.setup()
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
expanded = data.get("expandtemplates", {}).get("wikitext", "")

# Same regex as poc_tree_compare.py
m = re.search(
    r'(?:\|\s*enhancements?\s*(?:=\s*)?|'
    r'![^|]*\|[Ee]nchantments)\s*\n'
    r'(?:\|[^*]*\|\s*\n)?'
    r'((?:\*.*\n?)+)',
    expanded,
)

section = m.group(1).strip()
import html as html_mod

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
        print(f"  SKIP (empty content): depth={depth} | {line[:80]}")
        continue

    # Same cleanup as poc_tree_compare.py
    clean = re.sub(r'\[\[Category:[^\]]*\]\]', '', content)
    clean = re.sub(r'\[\[File:[^\]]*\]\]', '', clean)
    clean = re.sub(r'\{\{[^}]*\}\}', '', clean)
    clean = re.sub(r'<templatestyles[^/]*/>', '', clean)
    clean = re.sub(
        r'<span class="popup tooltip[^"]*"[^>]*>'
        r'.*?</span>',
        '', clean, flags=re.DOTALL,
    )
    clean = re.sub(r'<span[^>]*>', '', clean)
    clean = re.sub(r'</span>', '', clean)
    clean = re.sub(r'<br\s*/?\s*>', ' ', clean)
    clean = re.sub(r'</?ul>', ' ', clean)
    clean = re.sub(r'</?li>', ' ', clean)

    parts = []
    last_end = 0
    all_links = []
    for lm in re.finditer(
        r'\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]',
        clean,
    ):
        target = lm.group(1).strip()
        display = (lm.group(2) or target).strip()
        all_links.append({"target": target, "text": display})
        before = clean[last_end:lm.start()]
        parts.append(before)
        parts.append(display)
        last_end = lm.end()
    parts.append(clean[last_end:])
    text = "".join(parts)

    text = text.replace("&rarr;", " -> ")
    text = text.replace("&#x27;", "'")
    text = re.sub(r"'+", "", text)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        print(f"  SKIP (empty text after cleanup): depth={depth} | clean={clean[:80]}")
        continue

    node = {"text": text, "children": [], "links": []}

    # Tree building
    while stack and stack[-1][0] >= depth:
        stack.pop()

    if depth == 1 or not stack:
        tree.append(node)
        stack.append((depth, node))
        parent = "ROOT"
    else:
        if stack:
            stack[-1][1]["children"].append(node)
            parent = stack[-1][1]["text"]
        else:
            tree.append(node)
            parent = "ROOT"
        stack.append((depth, node))

    print(f"  depth={depth} parent='{parent}' text='{text[:60]}' stack_depth={len(stack)}")

print(f"\nTree has {len(tree)} top-level nodes")
for n in tree:
    nc = len(n.get("children", []))
    print(f"  {n['text'][:60]} ({nc} children)")
    for c in n.get("children", []):
        nc2 = len(c.get("children", []))
        print(f"    {c['text'][:60]} ({nc2} children)")
