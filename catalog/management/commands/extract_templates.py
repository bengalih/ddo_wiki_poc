"""Extract all {{template}} calls from raw wikitext files
and produce a deduplicated list."""

import json
import re
from pathlib import Path
from django.core.management.base import BaseCommand


def extract_templates(text):
    """Extract all {{template|param1|param2}} patterns from
    wikitext, including nested ones. Returns list of
    (template_name, [params])."""
    results = []
    i = 0
    while i < len(text):
        start = text.find("{{", i)
        if start == -1:
            break
        # Track brace depth to find matching }}
        depth = 1
        j = start + 2
        while j < len(text) and depth > 0:
            if text[j:j+2] == "{{":
                depth += 1
                j += 2
            elif text[j:j+2] == "}}":
                depth -= 1
                j += 2
            else:
                j += 1
        if depth != 0:
            i = start + 2
            continue
        full = text[start:j]
        # Advance past THIS {{, not past the whole match,
        # so nested {{ inside are also found
        i = start + 2
        # Skip comments
        if full.startswith("{{!"):
            continue
        # Parse template name and params
        inner = full[2:-2]  # strip outer {{
        pipe = inner.find("|")
        if pipe == -1:
            name = inner.strip()
            params = []
        else:
            name = inner[:pipe].strip()
            # Split top-level params only (depth 0 pipes)
            param_str = inner[pipe+1:]
            params = []
            current = ""
            bd = 0
            for ch in param_str:
                if ch == "{":
                    bd += 1
                    current += ch
                elif ch == "}":
                    bd -= 1
                    current += ch
                elif ch == "|" and bd == 0:
                    params.append(current.strip())
                    current = ""
                else:
                    current += ch
            params.append(current.strip())
        if not name:
            continue
        # Skip boilerplate
        skip = (
            "Named item", "History", "Non-food", "Cormyr",
            "#switch", "DEFAULTSORT", "See also",
            "Help improve", "For ", "Lamannia", "Item",
            "Icon", "Tooltip", "Collapsible", "Tooltip:",
        )
        if any(name.startswith(s) for s in skip):
            continue
        results.append((name, params))
    return results


class Command(BaseCommand):
    help = "Extract deduplicated template list from raw files"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Limit to N files (0 = all)",
        )
        parser.add_argument(
            "--output", type=str, default="template_list.txt",
            help="Output file path",
        )
        parser.add_argument(
            "--sort", type=str, default="count",
            choices=["count", "name"],
            help="Sort order",
        )
        parser.add_argument(
            "--flat", action="store_true",
            help="Output flat list of {{x|y|z}} patterns only",
        )

    def handle(self, *args, **options):
        raw_dir = Path("wiki_snapshot/raw")
        files = sorted(raw_dir.glob("Item_*.json"))
        if options["limit"]:
            files = files[:options["limit"]]

        self.stdout.write(
            f"Scanning {len(files)} raw files..."
        )

        # template_name -> { "full": set of full matches,
        #   "count": int, "items": set of item names,
        #   "param_patterns": set of param tuples }
        templates = {}
        total = 0
        for fi, fpath in enumerate(files):
            with fpath.open("r", encoding="utf-8") as f:
                data = json.load(f)
            wt = data.get("wikitext", "")
            if not wt:
                continue
            # Extract item title from filename
            title = fpath.stem.replace("Item_", "", 1)
            calls = extract_templates(wt)
            for name, params in calls:
                if name not in templates:
                    templates[name] = {
                        "count": 0,
                        "items": set(),
                        "param_patterns": set(),
                    }
                templates[name]["count"] += 1
                templates[name]["items"].add(title)
                templates[name]["param_patterns"].add(
                    tuple(
                        re.sub(r'[+-]?\d+', '1', p[:30]).lower()
                        for p in params
                    )
                )
                total += 1
            if (fi + 1) % 1000 == 0:
                self.stdout.write(f"  Processed {fi + 1}...")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {total} total template calls, "
                f"{len(templates)} unique template names."
            )
        )

        # Sort
        if options["sort"] == "count":
            sorted_t = sorted(
                templates.items(),
                key=lambda x: -x[1]["count"],
            )
        else:
            sorted_t = sorted(templates.items())

        # Write output
        out_path = Path(options["output"])

        if options["flat"]:
            # Flat list: just {{x|y|z}} lines, deduplicated
            flat = set()
            for name, info in templates.items():
                for pattern in info["param_patterns"]:
                    params_str = "|".join(pattern)
                    flat.add(f"{{{{{name.lower()}|{params_str}}}}}")
            lines = sorted(flat)
        else:
            lines = []
            lines.append(
                f"UNIQUE TEMPLATE NAMES: {len(templates)}"
            )
            lines.append(f"TOTAL CALLS: {total}")
            lines.append("=" * 60)
            lines.append("")
            for name, info in sorted_t:
                lines.append(
                    f"[{info['count']:>5}] {name} "
                    f"({len(info['items'])} items, "
                    f"{len(info['param_patterns'])} param patterns)"
                )
                for i, pattern in enumerate(sorted(info["param_patterns"])):
                    if i >= 30:
                        lines.append(
                            f"        ... and {len(info['param_patterns']) - 30} more"
                        )
                        break
                    lines.append(f"        {'|'.join(pattern)}")
                lines.append("")

        out_path.write_text(
            "\n".join(lines), encoding="utf-8"
        )
        self.stdout.write(f"Written to {out_path}")
