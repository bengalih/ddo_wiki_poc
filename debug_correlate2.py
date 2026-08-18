import json, re

with open(r"D:\ddo_wiki_poc\debug_parsetree.json", "r", encoding="utf-8") as f:
    data = json.load(f)

pt = data["parse"]["parsetree"]
html = data["parse"]["text"]

# Extract top-level templates from parsetree (enhancements section)
enh_idx = pt.find("enhancements")
enh_section = pt[enh_idx:]

# Get top-level templates only (after '* ')
top_templates = re.findall(r'\* <template><title>([^<]+)</title>', enh_section)

# For nested templates, extract them in order
# Find all Stat templates with their params in order
stat_templates = re.findall(r'<title>Stat</title>.*?<name index="1"/><value>(\w+)</value>.*?<name index="2"/><value>(\d+)</value>', enh_section, re.DOTALL)

# Check for 3rd param (qualifier)
stat_with_qual = re.findall(r'<title>Stat</title>.*?<name index="1"/><value>(\w+)</value>.*?<name index="2"/><value>(\d+)</value>.*?<name index="3"/><value>(\w+)</value>', enh_section, re.DOTALL)

print("=== PARSETREE ORDER ===")
# Reconstruct the full order
# Nearly Finished (6 Stats), Almost There (6 Stats), Resistance, Temperance of Belief, Augment
stat_idx = 0
for t in top_templates:
    if t == "Nearly Finished":
        print(f"  {t}")
        for i in range(6):
            s = stat_templates[stat_idx]
            print(f"    -> {t}: {s[0]} {s[1]}")
            stat_idx += 1
    elif t == "Almost There":
        print(f"  {t}")
        for i in range(6):
            s = stat_templates[stat_idx]
            qual = stat_with_qual[stat_idx][2] if stat_idx < len(stat_with_qual) else ""
            print(f"    -> {t}: {s[0]} {s[1]} {qual}")
            stat_idx += 1
    else:
        print(f"  {t}")

# Extract HTML order
ench_idx = html.find("Enchantments")
ench_html = html[ench_idx:]
td_start = ench_html.find("<td")
td_end = ench_html.find("</td>", td_start)
ench_cell = ench_html[td_start:td_end + 4]

# Get link texts in order
link_texts = re.findall(r'<a href="/page/[^"]*"[^>]*>\s*([^<]+)</a>', ench_cell)

print("\n=== HTML ORDER ===")
for lt in link_texts:
    lt = lt.strip()
    if lt:
        print(f"  {lt}")
