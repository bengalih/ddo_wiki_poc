import json, re

with open(r"D:\ddo_wiki_poc\debug_parsetree.json", "r", encoding="utf-8") as f:
    data = json.load(f)

pt = data["parse"]["parsetree"]
html = data["parse"]["text"]

# --- Parsetree: extract template structure ---
enh_idx = pt.find("enhancements")
enh_section = pt[enh_idx:]

# Find all top-level templates (after '* ') in enhancements
# These are the direct children of the enhancements wikitext list
top_templates = re.findall(r'\* <template><title>([^<]+)</title>', enh_section)
print("=== PARSETREE top-level templates ===")
for t in top_templates:
    print(f"  {t}")

# Find all templates in enhancements (nested too)
all_templates = re.findall(r'<title>([^<]+)</title>', enh_section)
print(f"\nAll templates in enhancements: {all_templates}")

# Count Stat with each stat name
stat_details = re.findall(r'<title>Stat</title>.*?<name index="1"/><value>(\w+)</value>.*?<name index="2"/><value>(\d+)</value>', enh_section, re.DOTALL)
print("\nStat details (param1, param2):")
for s in stat_details:
    print(f"  {s}")

# Check for named params (3rd param = qualifier)
stat_qualifiers = re.findall(r'<title>Stat</title>.*?<name index="1"/><value>(\w+)</value>.*?<name index="2"/><value>(\d+)</value>.*?<name index="3"/><value>(\w+)</value>', enh_section, re.DOTALL)
print("\nStat with qualifier (param1, param2, param3):")
for s in stat_qualifiers:
    print(f"  {s}")

# --- HTML: extract enchantment structure ---
ench_idx = html.find("Enchantments")
ench_html = html[ench_idx:]

# Find the td cell with enchantments
td_start = ench_html.find("<td")
td_end = ench_html.find("</td>", td_start)
ench_cell = ench_html[td_start:td_end + 4]

print("\n=== HTML ENCHANTMENTS (cleaned) ===")
# Strip tooltip spans, icon images, templatestyles
cleaned = re.sub(r'<span class="popup tooltip[^"]*"[^>]*>[^<]*(?:<[^>]*>[^<]*)*</span>', '', ench_cell)
cleaned = re.sub(r'<span class="mw-valign-super"[^>]*>.*?</span>', '', cleaned, flags=re.DOTALL)
cleaned = re.sub(r'<style[^>]*>.*?</style>', '', cleaned, flags=re.DOTALL)
cleaned = re.sub(r'<img[^>]*>', '', cleaned)
cleaned = re.sub(r'<br\s*/?>', '', cleaned)
print(cleaned[:3000])

# Count <li> items with enhancement links
li_items = re.findall(r'<a href="/page/[^"]*"[^>]*>\s*([^<]+)</a>', ench_cell)
print("\n=== HTML link texts ===")
for item in li_items:
    item = item.strip()
    if item:
        print(f"  {item}")
