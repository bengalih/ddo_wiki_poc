import json
import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from catalog import enhancement_render_store as render_store
from catalog.enhancement_renders import (
    decompose_upgrade_chain,
    extract_rendered,
    normalize_template_call,
)
from catalog.enhancement_rule_seeds import DEFAULT_RULES
from catalog.enhancement_rules import (
    enhancement_bonus,
    expand_item_rules,
    healing_amp,
    spell_lore,
    spell_power,
)
from catalog.enhancement_values import parse_magnitude
from catalog.management.commands.import_wiki import Command
from catalog.models import Item, SyncState
from catalog.services import apply_enhancement_filter
from catalog.views import enhancement_options


def _batched_response(text, output_map):
    # The command now renders calls in batches, sending sentinel
    # markers (@@K{n}@@) that the wiki echoes back so each call's
    # render can be recovered. Simulate that: split the request,
    # look up each call's render, and reassemble the batched reply.
    chunks = []
    calls = re.split(r"@@K(\d+)@@", text)

    for i in range(1, len(calls), 2):
        call = calls[i + 1].strip()
        output = output_map.get(call, "")

        chunks.append(f"@@K{calls[i]}@@ {output}")

    return " ".join(chunks)


SAMPLE_WIKITEXT = """{{Named item|Weapon
|name = Test Scorched Sword
|type = Weapon
|minlevel = 7
|enhancements =
* {{Enhancement bonus|i|3}}
* {{CraftingEffects|Upgradeable Item (Temple of Elemental Evil)|nocat=TRUE}}
** Tier 1:
*** Adds {{Spellpen|I|nocat=TRUE}}
*** Adds {{Riposte|I|nocat=TRUE}}
* {{SpellPower|Combustion|54}}
* {{Spelllore|Fire|III}}
}}
"""


BASE_WIKITEXT = """{{Named item|Weapon
|name = Test Base Dagger
|type = Dagger
|minlevel = 3
|enhancements =
{{Enhancement bonus|w|2}}
}}
"""


class SnapshotTests(TestCase):

    def setUp(self):
        self.command = Command()

    def _write_snapshot(self, snapshot_dir, entries):
        manifest = {}

        for page_id, title, revision_id, wikitext in entries:
            self.command.write_snapshot_page(
                Path(snapshot_dir),
                {
                    "page_id": page_id,
                    "title": title,
                    "revision_id": revision_id,
                    "wikitext": wikitext,
                },
            )

            manifest[str(page_id)] = {
                "title": title,
                "revision_id": revision_id,
                "file": f"pages/{page_id}.json",
            }

        self.command.save_snapshot_manifest(
            Path(snapshot_dir),
            manifest,
        )

    def test_load_snapshot_creates_items(self):
        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (100, "Test Scorched Sword", 55, SAMPLE_WIKITEXT),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            item = Item.objects.get(wiki_page_id=100)
            self.assertEqual(item.name, "Test Scorched Sword")
            self.assertEqual(item.wiki_revision_id, 55)
            self.assertEqual(item.item_kind, "Named item")
            self.assertEqual(item.minimum_level, 7)

            names = set(
                item.enhancements.values_list(
                    "variant__enhancement__name",
                    flat=True,
                )
            )

            self.assertIn("Enhancement bonus", names)
            self.assertIn("Spellcasting implement", names)
            self.assertIn("Spell Power", names)
            self.assertIn("Spell Lore", names)
            self.assertIn("Mythic", names)

            mythic = item.enhancements.get(
                variant__enhancement__name="Mythic"
            )
            self.assertEqual(
                mythic.value,
                "Weapon Boost +2 or +4",
            )

            spellpen = item.enhancements.get(
                variant__enhancement__name="Spellpen"
            )
            self.assertEqual(spellpen.tier, 1)

            riposte = item.enhancements.get(
                variant__enhancement__name="Riposte"
            )
            self.assertEqual(riposte.tier, 1)

            enhancement_bonus = item.enhancements.get(
                variant__enhancement__name="Enhancement bonus"
            )
            self.assertIsNone(enhancement_bonus.tier)

            spell_power = item.enhancements.get(
                variant__enhancement__name="Spell Power"
            )
            self.assertIsNone(spell_power.tier)

    def test_load_snapshot_skips_unchanged(self):
        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (100, "Test Scorched Sword", 55, SAMPLE_WIKITEXT),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            self.assertEqual(Item.objects.count(), 1)

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=False,
            )

            self.assertEqual(Item.objects.count(), 1)
            item = Item.objects.get(wiki_page_id=100)
            self.assertEqual(item.wiki_revision_id, 55)

    def test_load_snapshot_force_reimports(self):
        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (100, "Test Scorched Sword", 55, SAMPLE_WIKITEXT),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            enhancements = Item.objects.get(
                wiki_page_id=100
            ).enhancements.count()
            self.assertGreater(enhancements, 0)

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            self.assertEqual(Item.objects.count(), 1)
            self.assertEqual(
                Item.objects.get(
                    wiki_page_id=100
                ).enhancements.count(),
                enhancements,
            )

    def test_load_snapshot_uses_title_when_no_name_param(self):
        wikitext = (
            "{{Named item|Shield\n"
            "|type = Large\n"
            "|minlevel = 2\n"
            "|enhancements =\n"
            "{{Enhancement bonus|s|1}}\n"
            "}}\n"
        )

        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (
                        101,
                        "Item:+1 Starter Heavy Steel Shield",
                        77,
                        wikitext,
                    ),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            item = Item.objects.get(wiki_page_id=101)
            self.assertEqual(
                item.name,
                "+1 Starter Heavy Steel Shield",
            )

    def test_load_snapshot_skips_non_item_pages(self):
        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (200, "Some Crafting", 99, "{{Crafting\n|foo = bar\n}}\n"),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            self.assertEqual(Item.objects.count(), 0)

    def test_include_upgrades_filter(self):
        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (
                        300,
                        "Test Scorched Sword",
                        90,
                        SAMPLE_WIKITEXT,
                    ),
                    (
                        301,
                        "Test Base Dagger",
                        91,
                        BASE_WIKITEXT,
                    ),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            all_items = Item.objects.all()

            with_upgrades = apply_enhancement_filter(
                all_items,
                "Spellpen",
                "I",
                include_upgrades=True,
            )
            self.assertEqual(with_upgrades.count(), 1)

            without_upgrades = apply_enhancement_filter(
                all_items,
                "Spellpen",
                "I",
                include_upgrades=False,
            )
            self.assertEqual(without_upgrades.count(), 0)

            base_match = apply_enhancement_filter(
                all_items,
                "Enhancement bonus",
                "+2",
                include_upgrades=False,
            )
            self.assertEqual(base_match.count(), 1)

    def test_options_dropdown_ignores_include_upgrades(self):
        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (
                        300,
                        "Test Scorched Sword",
                        90,
                        SAMPLE_WIKITEXT,
                    ),
                    (
                        301,
                        "Test Base Dagger",
                        91,
                        BASE_WIKITEXT,
                    ),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            with_upgrades = json.loads(
                enhancement_options(
                    self._make_request(include_upgrades="1")
                ).content
            )

            without_upgrades = json.loads(
                enhancement_options(
                    self._make_request(include_upgrades="0")
                ).content
            )

            spellpen_with = (
                with_upgrades["rows"][0]["enhancements"].get("Spellpen")
            )
            spellpen_without = (
                without_upgrades["rows"][0]["enhancements"].get("Spellpen")
            )

            # "Spellpen" only exists at Tier 1 in the sample, so it
            # must appear in the dropdown regardless of the checkbox.
            self.assertEqual(spellpen_with, spellpen_without)

    def test_min_magnitude_filter(self):
        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (
                        300,
                        "Test Scorched Sword",
                        90,
                        SAMPLE_WIKITEXT,
                    ),
                    (
                        301,
                        "Test Base Dagger",
                        91,
                        BASE_WIKITEXT,
                    ),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            all_items = Item.objects.all()

            match = apply_enhancement_filter(
                all_items,
                "Spell Power",
                "",
                min_magnitude=20,
            )
            self.assertEqual(match.count(), 1)

            too_high = apply_enhancement_filter(
                all_items,
                "Spell Power",
                "",
                min_magnitude=60,
            )
            self.assertEqual(too_high.count(), 0)

    def test_min_magnitude_overrides_exact_value(self):
        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (
                        300,
                        "Test Scorched Sword",
                        90,
                        SAMPLE_WIKITEXT,
                    ),
                    (
                        301,
                        "Test Base Dagger",
                        91,
                        BASE_WIKITEXT,
                    ),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            # The exact value "Weapon Boost +2 or +4" is present on the
            # item, but its parsed magnitude is 2. A minimum of 3 must
            # supersede the exact pick and exclude it.
            match = apply_enhancement_filter(
                Item.objects.all(),
                "Mythic",
                "Weapon Boost +2 or +4",
                min_magnitude=3,
            )
            self.assertEqual(match.count(), 0)

            within = apply_enhancement_filter(
                Item.objects.all(),
                "Mythic",
                "Weapon Boost +2 or +4",
                min_magnitude=2,
            )
            self.assertEqual(within.count(), 2)

    def test_min_magnitude_ignores_non_numeric(self):
        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (
                        300,
                        "Test Scorched Sword",
                        90,
                        SAMPLE_WIKITEXT,
                    ),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            # Spellpen's value is "I", which has no numeric magnitude,
            # so a minimum can never match it.
            match = apply_enhancement_filter(
                Item.objects.all(),
                "Spellpen",
                "",
                min_magnitude=1,
            )
            self.assertEqual(match.count(), 0)

    def test_options_exposes_has_magnitudes(self):
        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (
                        300,
                        "Test Scorched Sword",
                        90,
                        SAMPLE_WIKITEXT,
                    ),
                    (
                        301,
                        "Test Base Dagger",
                        91,
                        BASE_WIKITEXT,
                    ),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            data = json.loads(
                enhancement_options(
                    self._make_request(include_upgrades="1")
                ).content
            )

            has_magnitudes = (
                data["rows"][0]["has_magnitudes"]
            )

            self.assertIs(
                has_magnitudes.get("Spell Power"),
                True,
            )

            # "Spellpen" only has the Roman numeral "I", which is not
            # a numeric magnitude, so no min box should be offered.
            self.assertNotIn(
                "Spellpen",
                has_magnitudes,
            )

    def _make_request(self, include_upgrades):
        from django.test import RequestFactory

        request = RequestFactory().get(
            "/",
            {
                "enhancement_options": "1",
                "include_upgrades": include_upgrades,
                "enhancement_filter_count": "1",
            },
        )
        return request

    def test_manifest_round_trip(self):
        with TemporaryDirectory() as tmp:
            snapshot_dir = Path(tmp)

            self.command.write_snapshot_page(
                snapshot_dir,
                {
                    "page_id": 100,
                    "title": "Test Scorched Sword",
                    "revision_id": 55,
                    "wikitext": SAMPLE_WIKITEXT,
                },
            )

            self.command.save_snapshot_manifest(
                snapshot_dir,
                {
                    "100": {
                        "title": "Test Scorched Sword",
                        "revision_id": 55,
                        "file": "pages/100.json",
                    }
                },
            )

            manifest = self.command.load_snapshot_manifest(
                snapshot_dir
            )

            self.assertEqual(manifest["100"]["revision_id"], 55)
            self.assertEqual(
                manifest["100"]["file"],
                "pages/100.json",
            )

            page_file = snapshot_dir / "pages" / "100.json"
            self.assertTrue(page_file.exists())

            with page_file.open("r", encoding="utf-8") as f:
                data = json.load(f)

            self.assertEqual(data["page_id"], 100)
            self.assertEqual(data["revision_id"], 55)
            self.assertEqual(
                data["wikitext"],
                SAMPLE_WIKITEXT,
            )

    def test_load_prunes_orphaned_enhancements(self):
        from catalog.models import Enhancement

        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (300, "Test Scorched Sword", 90, SAMPLE_WIKITEXT),
                ],
            )

            # Simulate a leftover row whose only references were
            # removed by a canonical-name fix (e.g. ":Adamantine"
            # normalized to "Adamantine").
            Enhancement.objects.create(name=":Adamantine")

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            self.assertFalse(
                Enhancement.objects.filter(
                    name=":Adamantine"
                ).exists()
            )
            self.assertTrue(
                Enhancement.objects.filter(
                    name="Enhancement bonus"
                ).exists()
            )

    def test_enhancements_dropdown_excludes_orphans(self):
        from catalog.models import Enhancement

        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (300, "Test Scorched Sword", 90, SAMPLE_WIKITEXT),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            # Simulate an orphaned enhancement appearing after the
            # last load (e.g. a manual edit or a pre-fix import).
            # The dropdown must hide it even before the next load.
            Enhancement.objects.create(name=":Adamantine")

            with_upgrades = json.loads(
                enhancement_options(
                    self._make_request(include_upgrades="1")
                ).content
            )

            dropdown_names = set(
                with_upgrades["rows"][0]["enhancements"].keys()
            )

            self.assertNotIn(":Adamantine", dropdown_names)
            self.assertIn("Spell Power", dropdown_names)

    def test_import_preserves_existing_name_and_override(self):
        from catalog.models import Enhancement

        Enhancement.objects.create(
            name="Spell Power",
            display_name="Bloop",
        )

        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (300, "Test Scorched Sword", 90, SAMPLE_WIKITEXT),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

        enhancement = Enhancement.objects.get(
            name="Spell Power"
        )
        self.assertEqual(enhancement.name, "Spell Power")
        self.assertEqual(enhancement.display_name, "Bloop")

    def test_import_does_not_rewrite_name_casing(self):
        from catalog.models import Enhancement

        # Simulate an older import that captured different casing.
        Enhancement.objects.create(name="spell power")

        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (300, "Test Scorched Sword", 90, SAMPLE_WIKITEXT),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

        # The wiki name is immutable: the import matches case-
        # insensitively but must never rewrite the stored casing.
        enhancement = Enhancement.objects.get(
            name="spell power"
        )
        self.assertEqual(enhancement.name, "spell power")

    def test_enhancements_dropdown_honors_display_name(self):
        from catalog.models import Enhancement

        with TemporaryDirectory() as tmp:
            self._write_snapshot(
                tmp,
                [
                    (300, "Test Scorched Sword", 90, SAMPLE_WIKITEXT),
                ],
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            # An admin can override how a name is shown in the
            # dropdown without re-parsing or re-coding.
            Enhancement.objects.filter(
                name="Spell Power"
            ).update(display_name="Spellpower")

            with_upgrades = json.loads(
                enhancement_options(
                    self._make_request(include_upgrades="1")
                ).content
            )

            row = with_upgrades["rows"][0]

            self.assertEqual(
                row["labels"].get("Spell Power"),
                "Spellpower",
            )
            # The canonical name stays the key/value so existing
            # filters and URL round-trips keep working.
            self.assertIn("Spell Power", row["enhancements"])


class ResolveItemNameTests(TestCase):
    def test_resolve_item_name_one_arg(self):
        from catalog.management.commands.import_wiki import (
            resolve_item_name,
        )

        self.assertEqual(
            resolve_item_name("{{Item|Bronze Ingot Arcanum}}"),
            "Bronze Ingot Arcanum",
        )

    def test_resolve_item_name_two_args(self):
        from catalog.management.commands.import_wiki import (
            resolve_item_name,
        )

        self.assertEqual(
            resolve_item_name(
                "{{Item|Cavalry Plate|Epic Cavalry Plate}}"
            ),
            "Epic Cavalry Plate",
        )

    def test_resolve_item_name_preserves_suffix(self):
        from catalog.management.commands.import_wiki import (
            resolve_item_name,
        )

        self.assertEqual(
            resolve_item_name(
                "{{Item|Crystallized Eternity}} (level 12)"
            ),
            "Crystallized Eternity (level 12)",
        )

    def test_resolve_item_name_collapses_whitespace(self):
        from catalog.management.commands.import_wiki import (
            resolve_item_name,
        )

        self.assertEqual(
            resolve_item_name("{{Item|Mournlode  Maul}}"),
            "Mournlode Maul",
        )

    def test_resolve_item_name_strips_html_comments(self):
        from catalog.management.commands.import_wiki import (
            resolve_item_name,
        )

        self.assertEqual(
            resolve_item_name(
                "Green Steel Sceptre "
                "<!--name Turbine use-->"
            ),
            "Green Steel Sceptre",
        )

    def test_resolve_item_name_normalizes_apostrophes(self):
        from catalog.management.commands.import_wiki import (
            resolve_item_name,
        )

        self.assertEqual(
            resolve_item_name("Valen\u2019s Mace"),
            "Valen's Mace",
        )

    def test_resolve_item_name_simple_wikilink(self):
        from catalog.management.commands.import_wiki import (
            resolve_item_name,
        )

        self.assertEqual(
            resolve_item_name("[[Wraps of Endless Light]]"),
            "Wraps of Endless Light",
        )

    def test_resolve_item_name_wikilink_with_display(self):
        from catalog.management.commands.import_wiki import (
            resolve_item_name,
        )

        self.assertEqual(
            resolve_item_name(
                "[[Blasting Chime|Epic Blasting Chime]]"
            ),
            "Epic Blasting Chime",
        )

    def test_resolve_item_name_wikilink_namespace(self):
        from catalog.management.commands.import_wiki import (
            resolve_item_name,
        )

        self.assertEqual(
            resolve_item_name(
                "[[Item:Guidance of Shar|Guidance of Shar]]"
            ),
            "Guidance of Shar",
        )

    def test_resolve_item_name_wikilink_prefix_and_suffix(self):
        from catalog.management.commands.import_wiki import (
            resolve_item_name,
        )

        self.assertEqual(
            resolve_item_name(
                "Epic [[Ring of the Stalker]]"
            ),
            "Epic Ring of the Stalker",
        )
        self.assertEqual(
            resolve_item_name(
                "[[Flotsam|Epic Flotsam]] (Level 20)"
            ),
            "Epic Flotsam (Level 20)",
        )

    def test_resolve_item_name_image_embed(self):
        from catalog.management.commands.import_wiki import (
            resolve_item_name,
        )

        self.assertEqual(
            resolve_item_name(
                "[[Image:Skull Head icon.png]] Skull Head"
            ),
            "Skull Head",
        )

    def test_parse_item_resolves_name_templates(self):
        wikitext = (
            "{{Named item|Weapon\n"
            "| name = {{Item|Allegiance}}\n"
            "| type = Quarterstaff\n"
            "| minlevel = 25\n"
            "}}\n"
        )

        data = Command().parse_item(
            "Item:Allegiance (level 25)",
            wikitext,
        )

        self.assertEqual(data["name"], "Allegiance")


class RenderExtractionTests(TestCase):

    CLICKY_OUTPUT = (
        '<templatestyles src="Popup/common.css" />'
        '<span class="popup has_tooltip with-icon basic">'
        "[[Nimbus of Light|Nimbus of Light]]"
        "[[File:Icon tooltip.png|link=|super|10px]]"
        '<span class="popup tooltip left below" '
        'style="text-align: center;">'
        "'''[[Nimbus of Light|Nimbus of Light]]'''<br />"
        "'''Caster level:''' 1<br />"
        "'''Charges: '''50 (50/day)"
        "</span></span> "
        "'''\u2014''' 50 Charges&#32;(Recharged/Day:50)"
    )

    ABSORPTION_OUTPUT = (
        '<templatestyles src="Popup/common.css" />'
        '<span class="popup has_tooltip with-icon basic">'
        "[[Energy Absorption| Fire Absorption +26%]]"
        "[[File:Icon tooltip.png|link=|super|10px]]"
        '<span class="popup tooltip wide left below" '
        'style="text-align: center;">'
        "'''[[Energy Absorption| Fire Absorption +26%]]:''' "
        "Passive: 26% Enhancement Bonus to Fire Absorption."
        "</span></span>"
    )

    AUGMENT_OUTPUT = (
        '<templatestyles src="Augment/common.css" />'
        '<templatestyles src="Popup/common.css" />'
        '<span class="popup has_tooltip with-icon augment blue">'
        '<span class="title">'
        "[[Augment Slot|Blue Augment Slot]]"
        "</span>"
        "[[File:Icon tooltip.png|link=|super|10px]]"
        '<span class="popup tooltip wide left below" '
        'style="text-align: center;">'
        "[[:Category:Blue augments|Blue]] and "
        "[[:Category:Colorless augments|Colorless]]"
        "</span></span>"
    )

    ARMOR_BONUS_OUTPUT = (
        '<templatestyles src="Popup/common.css" />'
        '<span class="popup has_tooltip with-icon basic">'
        "[[Armor Bonus|Armor Bonus +5]]"
        "[[File:Icon tooltip.png|link=|super|10px]]"
        '<span class="popup tooltip wide left below" '
        'style="text-align: center;">'
        "'''[[Armor Bonus|Armor Bonus +5]]:''' "
        "This item surrounds the wearer..."
        "</span></span>"
    )

    ACID_TORRENT_OUTPUT = (
        '<templatestyles src="Popup/common.css" />'
        '<span class="popup has_tooltip with-icon basic">'
        "[[Acid Torrent#Greater Acid Torrent|Greater Acid Torrent]]"
        "[[File:Icon tooltip.png|link=|super|10px]]"
        '<span class="popup tooltip wide left below" '
        'style="text-align: center;">'
        "'''[[Acid Torrent#Greater Acid Torrent|"
        "Greater Acid Torrent]]:''' ..."
        "</span></span>"
    )

    def test_normalize_preserves_parameter_case(self):
        self.assertEqual(
            normalize_template_call(
                "{{Clicky|Nimbus of Light|1|50|50}}"
            ),
            "{{Clicky|Nimbus of Light|1|50|50}}",
        )
        self.assertEqual(
            normalize_template_call(
                "{{Clicky|nimbus of light|1|50|50}}"
            ),
            "{{Clicky|nimbus of light|1|50|50}}",
        )

    def test_normalize_collapses_underscores_and_spacing(self):
        self.assertEqual(
            normalize_template_call(
                "{{Enhancement_bonus| w | 1 }}"
            ),
            "{{Enhancement bonus|w|1}}",
        )

    def test_normalize_lowercases_named_keys_keeps_values(self):
        self.assertEqual(
            normalize_template_call(
                "{{SomeTpl|Key=Value|nocat=TRUE}}"
            ),
            "{{SomeTpl|key=Value}}",
        )

    def test_extract_clicky_keeps_trailing_charge_info(self):
        result = extract_rendered(self.CLICKY_OUTPUT)

        self.assertEqual(result["name"], "Nimbus of Light")
        self.assertEqual(
            result["display"],
            "Nimbus of Light \u2014 "
            "50 Charges (Recharged/Day:50)",
        )
        self.assertEqual(result["value"], "")
        self.assertEqual(result["detail"], "")

    def test_extract_absorption(self):
        result = extract_rendered(self.ABSORPTION_OUTPUT)

        self.assertEqual(result["name"], "Energy Absorption")
        self.assertEqual(result["display"], "Fire Absorption +26%")
        self.assertEqual(result["value"], "+26%")
        self.assertEqual(result["detail"], "Fire Absorption")

    def test_extract_augment(self):
        result = extract_rendered(self.AUGMENT_OUTPUT)

        self.assertEqual(result["name"], "Augment Slot")
        self.assertEqual(result["display"], "Blue Augment Slot")
        self.assertEqual(result["value"], "Blue")
        self.assertEqual(result["detail"], "")

    def test_extract_armor_bonus(self):
        result = extract_rendered(self.ARMOR_BONUS_OUTPUT)

        self.assertEqual(result["name"], "Armor Bonus")
        self.assertEqual(result["display"], "Armor Bonus +5")
        self.assertEqual(result["value"], "+5")
        self.assertEqual(result["detail"], "")

    def test_extract_acid_torrent_anchor_link(self):
        result = extract_rendered(self.ACID_TORRENT_OUTPUT)

        self.assertEqual(result["name"], "Acid Torrent")
        self.assertEqual(result["display"], "Greater Acid Torrent")
        self.assertEqual(result["value"], "Greater")
        self.assertEqual(result["detail"], "")


class UpgradeChainDecompositionTests(TestCase):

    CHAIN = (
        "\n"
        '* <span class="popup has_tooltip with-icon basic">'
        "[[Vaults of the Artificers loot|Upgradeable - Tier 1]]"
        "[[File:Icon tooltip.png|link=|super|10px]]"
        '<span class="popup tooltip wide left below">'
        "[[Vaults of the Artificers loot|Upgradeable - Tier 1]]: "
        "This item is a Tier 1 upgradable item."
        "</span></span>\n"
        '** <span class="popup has_tooltip with-icon basic">'
        "[[Riposte|Riposte +4]]"
        '<span class="popup tooltip wide left below">'
        "'''[[Riposte|Riposte +4]]:''' Riposte body."
        "</span></span>[[Category:Riposte +4 items]]\n"
        '** <span class="popup has_tooltip with-icon basic">'
        "[[Combat Mastery|Insightful Combat Mastery +4]]"
        '<span class="popup tooltip wide left below">'
        "'''[[Combat Mastery|Insightful Combat Mastery +4]]:''' +4 DC."
        "</span></span>\n"
        '** <span class="popup has_tooltip with-icon basic">'
        "[[Staggering Blow]]"
        '<span class="popup tooltip wide left below">'
        "'''[[Staggering Blow]]:''' Knockdown on a 20."
        "</span></span>[[Category:Staggering Blow items]]\n"
        "\n"
        '* <span class="popup has_tooltip with-icon basic">'
        "[[Vaults of the Artificers loot|Upgradeable - Tier 2]]"
        '<span class="popup tooltip wide left below">'
        "Upgradeable - Tier 2"
        "</span></span>\n"
        '** <span class="popup has_tooltip with-icon basic">'
        "[[Disable Device|Disable Device +17]]"
        '<span class="popup tooltip wide left below">'
        "'''[[Disable Device|Disable Device +17]]:''' +17 DD."
        "</span></span> &rarr; "
        '<span class="popup has_tooltip with-icon basic">'
        "[[Disable Device|Disable Device +18]]"
        '<span class="popup tooltip wide left below">'
        "'''[[Disable Device|Disable Device +18]]:''' +18 DD."
        "</span></span>[[Category:Disable Device +18 items]]\n"
        '** Adds <span class="popup has_tooltip with-icon basic">'
        "[[Use Magic Device|Use Magic Device +3]]"
        '<span class="popup tooltip wide left below">'
        "'''[[Use Magic Device|Use Magic Device +3]]:''' +3 UMD."
        "</span></span>\n"
    )

    FLAT = (
        "\n"
        '* <span class="popup has_tooltip with-icon basic">'
        "[[Protection|Protection +3]]"
        '<span class="popup tooltip wide left below">'
        "'''[[Protection|Protection +3]]:''' +3 AC."
        "</span></span>[[Category:Protection +3 items]]\n"
        '* <span class="popup has_tooltip with-icon basic">'
        "[[Resistance (enchantment)| Resistance +3]]"
        '<span class="popup tooltip wide left below">'
        "'''[[Resistance (enchantment)| Resistance +3]]:''' +3 saves."
        "</span></span>\n"
    )

    def test_chain_decomposes_into_per_tier_rows(self):
        rows = decompose_upgrade_chain(self.CHAIN)

        self.assertIsNotNone(rows)

        tier1 = [r for r in rows if r["tier"] is None]
        tier2 = [r for r in rows if r["tier"] == 2]

        self.assertEqual(
            [(r["name"], r["value"]) for r in tier1],
            [
                ("Riposte", "+4"),
                ("Combat Mastery", "+4"),
                ("Staggering Blow", ""),
            ],
        )

        self.assertEqual(
            [r["display_text"] for r in tier1],
            [
                "Riposte +4",
                "Insightful Combat Mastery +4",
                "Staggering Blow",
            ],
        )

        self.assertEqual(
            [(r["name"], r["value"]) for r in tier2],
            [
                ("Disable Device", "+18"),
                ("Use Magic Device", "+3"),
            ],
        )

        # The upgrade arrow keeps only the target enhancement.
        self.assertEqual(
            tier2[0]["display_text"],
            "Disable Device +18",
        )

    def test_flat_bullet_list_decomposes_as_base_rows(self):
        rows = decompose_upgrade_chain(self.FLAT)

        self.assertIsNotNone(rows)
        self.assertEqual(
            [(r["name"], r["value"]) for r in rows],
            [
                ("Protection", "+3"),
                ("Resistance (enchantment)", "+3"),
            ],
        )
        self.assertTrue(
            all(r["tier"] is None for r in rows)
        )

    def test_single_enhancement_output_is_not_a_chain(self):
        self.assertIsNone(
            decompose_upgrade_chain(
                "[[Riposte|Riposte +1]]"
            )
        )
        # {{Mat|Adamantine}} renders the material name as a forced
        # main-namespace link: [[:Adamantine|Adamantine]]. The colon
        # is MediaWiki link syntax, not part of the title.
        result = extract_rendered("[[:Adamantine|Adamantine]]")

        self.assertEqual(result["name"], "Adamantine")
        self.assertEqual(result["display"], "Adamantine")
        self.assertEqual(result["value"], "")
        self.assertEqual(result["detail"], "")


    def test_extract_keeps_inner_anchor_in_link_target(self):
        # Anchors are part of the page target; only a leading
        # namespace-forcing colon is stripped.
        result = extract_rendered(
            "[[:Acid Torrent#Greater Acid Torrent|Greater Acid Torrent]]"
        )

        self.assertEqual(result["name"], "Acid Torrent")
        self.assertEqual(result["display"], "Greater Acid Torrent")
        self.assertEqual(result["value"], "Greater")
        self.assertEqual(result["detail"], "")


class RenderEnhancementCommandTests(TestCase):

    WAND_WIKITEXT = """{{Named item|Weapon
|name = Test Nimbus Wand
|type = Wand
|minlevel = 3
|enhancements =
* {{Clicky|Nimbus of Light|1|50|50}}
}}
"""

    CLICKY_OUTPUT = (
        '<templatestyles src="Popup/common.css" />'
        '<span class="popup has_tooltip with-icon basic">'
        "[[Nimbus of Light|Nimbus of Light]]"
        '<span class="popup tooltip left below">'
        "hover body"
        "</span></span> "
        "'''\u2014''' 50 Charges&#32;(Recharged/Day:50)"
    )

    def setUp(self):
        self.command = Command()

    def _write_snapshot(self, snapshot_dir):
        self.command.write_snapshot_page(
            Path(snapshot_dir),
            {
                "page_id": 100,
                "title": "Test Nimbus Wand",
                "revision_id": 55,
                "wikitext": self.WAND_WIKITEXT,
            },
        )

        self.command.save_snapshot_manifest(
            Path(snapshot_dir),
            {
                "100": {
                    "title": "Test Nimbus Wand",
                    "revision_id": 55,
                    "file": "pages/100.json",
                }
            },
        )

    def _mock_api(self, output_map):
        def fake_api_request(self, params):
            if params["action"] == "query":
                return {"query": {"pages": {}}}

            return {
                "expandtemplates": {
                    "wikitext": _batched_response(
                        params["text"],
                        output_map,
                    )
                }
            }

        return patch.object(
            Command,
            "api_request",
            new=fake_api_request,
        )

    def _seed_render(self, snapshot_dir, **entry):
        render_store.save(
            {"template_call": entry["template_call"], **entry},
            Path(snapshot_dir),
        )

    def tearDown(self):
        render_store.clear_current_dir()
        render_store.clear_cache()

    def test_render_stores_raw_html(self):
        call = "{{Clicky|Nimbus of Light|1|50|50}}"

        with TemporaryDirectory() as tmp:
            self._write_snapshot(tmp)

            with self._mock_api({call: self.CLICKY_OUTPUT}):
                call_command(
                    "render_enhancements",
                    snapshot=Path(tmp),
                )

            render = render_store.get(call, Path(tmp))

        self.assertIsNotNone(render)
        self.assertEqual(
            render.raw_html.strip(),
            self.CLICKY_OUTPUT,
        )

    def test_render_backfills_raw_html_on_existing_rows(self):
        call = "{{Clicky|Nimbus of Light|1|50|50}}"

        with TemporaryDirectory() as tmp:
            self._write_snapshot(tmp)
            self._seed_render(
                tmp,
                template_call=call,
                canonical_name="nimbus of light",
                display_text="nimbus of light",
                raw_html="",
            )

            with self._mock_api({call: self.CLICKY_OUTPUT}):
                call_command(
                    "render_enhancements",
                    snapshot=Path(tmp),
                )

            render = render_store.get(call, Path(tmp))

        self.assertIsNotNone(render)
        self.assertEqual(
            render.raw_html.strip(),
            self.CLICKY_OUTPUT,
        )
        self.assertEqual(
            render.canonical_name,
            "Nimbus of Light",
        )

    def test_reparse_reapplies_extraction_locally(self):
        call = "{{Clicky|Nimbus of Light|1|50|50}}"

        with TemporaryDirectory() as tmp:
            self._seed_render(
                tmp,
                template_call=call,
                canonical_name="nimbus of light",
                display_text="nimbus of light",
                value="",
                detail="",
                raw_html=self.CLICKY_OUTPUT,
            )

            call_command(
                "render_enhancements",
                reparse=True,
                snapshot=Path(tmp),
            )

            render = render_store.get(call, Path(tmp))

        self.assertIsNotNone(render)
        self.assertEqual(render.canonical_name, "Nimbus of Light")
        self.assertEqual(
            render.display_text,
            "Nimbus of Light \u2014 50 "
            "Charges (Recharged/Day:50)",
        )

    def test_reparse_warns_on_missing_raw_html(self):
        call = "{{Clicky|Nimbus of Light|1|50|50}}"

        with TemporaryDirectory() as tmp:
            self._seed_render(
                tmp,
                template_call=call,
                canonical_name="Nimbus of Light",
                display_text="Nimbus of Light",
            )

            with StringIO() as out:
                call_command(
                    "render_enhancements",
                    reparse=True,
                    snapshot=Path(tmp),
                    stdout=out,
                )
                output = out.getvalue()

            render = render_store.get(call, Path(tmp))

        self.assertIn(
            "no raw_html stored",
            output,
        )
        self.assertIsNotNone(render)
        self.assertEqual(render.canonical_name, "Nimbus of Light")

    def test_reparse_skips_when_extraction_unchanged(self):
        call = "{{Clicky|Nimbus of Light|1|50|50}}"

        with TemporaryDirectory() as tmp:
            self._seed_render(
                tmp,
                template_call=call,
                canonical_name="Nimbus of Light",
                display_text="Nimbus of Light \u2014 50 "
                "Charges (Recharged/Day:50)",
                value="",
                detail="",
                raw_html=self.CLICKY_OUTPUT,
            )

            call_command(
                "render_enhancements",
                reparse=True,
                snapshot=Path(tmp),
            )

            render = render_store.get(call, Path(tmp))

        self.assertIsNotNone(render)
        self.assertEqual(
            render.canonical_name,
            "Nimbus of Light",
        )

    LORE_OUTPUT = (
        '<templatestyles src="Popup/common.css" />'
        '<span class="popup has_tooltip with-icon basic">'
        "[[Spell Lore|Acid Lore III]]"
        '<span class="popup tooltip left below">'
        "hover body"
        "</span></span>"
    )

    def _write_ruled_snapshot(self, snapshot_dir):
        self.command.write_snapshot_page(
            Path(snapshot_dir),
            {
                "page_id": 200,
                "title": "Test Lore Dagger",
                "revision_id": 55,
                "wikitext": (
                    "{{Named item|Weapon\n"
                    "|name = Test Lore Dagger\n"
                    "|type = Dagger\n"
                    "|minlevel = 3\n"
                    "|enhancements =\n"
                    "* {{Spelllore|Acid|III}}\n"
                    "* {{Clicky|Nimbus of Light|1|50|50}}\n"
                    "}}\n"
                ),
            },
        )

        self.command.save_snapshot_manifest(
            Path(snapshot_dir),
            {
                "200": {
                    "title": "Test Lore Dagger",
                    "revision_id": 55,
                    "file": "pages/200.json",
                }
            },
        )

    def test_render_skips_rule_composed_calls(self):
        lore_call = "{{Spelllore|Acid|III}}"
        clicky_call = "{{Clicky|Nimbus of Light|1|50|50}}"
        clicky_output = self.CLICKY_OUTPUT
        requested = []

        def fake_api_request(self, params):
            if params["action"] == "query":
                return {"query": {"pages": {}}}

            requested.append(params["text"])

            return {
                "expandtemplates": {
                    "wikitext": _batched_response(
                        params["text"],
                        {clicky_call: clicky_output},
                    )
                }
            }

        with TemporaryDirectory() as tmp:
            self._write_ruled_snapshot(tmp)

            with patch.object(
                Command,
                "api_request",
                new=fake_api_request,
            ):
                call_command(
                    "render_enhancements",
                    snapshot=Path(tmp),
                )

            requested_text = " ".join(requested)

            self.assertNotIn(lore_call, requested_text)
            self.assertIn(clicky_call, requested_text)
            self.assertIsNone(
                render_store.get(lore_call, Path(tmp))
            )

    def test_render_all_forces_rule_composed_fetch(self):
        lore_call = "{{Spelllore|Acid|III}}"
        lore_output = self.LORE_OUTPUT

        def fake_api_request(self, params):
            if params["action"] == "query":
                return {"query": {"pages": {}}}

            return {
                "expandtemplates": {
                    "wikitext": _batched_response(
                        params["text"],
                        {lore_call: lore_output},
                    )
                }
            }

        with TemporaryDirectory() as tmp:
            self._write_ruled_snapshot(tmp)

            with patch.object(
                Command,
                "api_request",
                new=fake_api_request,
            ):
                call_command(
                    "render_enhancements",
                    snapshot=Path(tmp),
                    render_all=True,
                )

            render = render_store.get(lore_call, Path(tmp))

        self.assertIsNotNone(render)
        self.assertEqual(render.display_text, "Acid Lore III")

    def _write_item_page(
        self,
        snapshot_dir,
        page_id,
        title,
        name,
        enhancements,
    ):
        self.command.write_snapshot_page(
            Path(snapshot_dir),
            {
                "page_id": page_id,
                "title": title,
                "revision_id": 55,
                "wikitext": (
                    "{{Named item|Weapon\n"
                    f"|name = {name}\n"
                    "|type = Weapon\n"
                    "|minlevel = 7\n"
                    "|enhancements =\n"
                    f"* {enhancements}\n"
                    "}}\n"
                ),
            },
        )

    def _save_item_manifest(
        self,
        snapshot_dir,
        pages,
    ):
        self.command.save_snapshot_manifest(
            Path(snapshot_dir),
            {
                str(page_id): {
                    "title": title,
                    "revision_id": 55,
                    "file": f"pages/{page_id}.json",
                }
                for page_id, title in pages
            },
        )

    def test_render_page_context_call_uses_title(self):
        call = "{{VaultsOfTheArtificersUpgrade}}"
        template_source = (
            "{{#switch:{{lc:{{FULLPAGENAMEE}}}}\n"
            "|item:blasting_chime_(level_7) = "
            "{{Weaken Construct}}\n"
            "|item:epic_blasting_chime = {{Anthem}}\n"
            "|#default = * See the item description page "
            "for details.\n"
            "}}"
        )

        title_outputs = {
            "Item:Blasting Chime (level 7)":
                "[[Weaken Construct]]",
            "Item:Epic Blasting Chime": "[[Anthem]]",
        }

        requested = []

        def fake_api_request(self, params):
            if params["action"] == "query":
                return {
                    "query": {
                        "pages": {
                            "1": {
                                "title": (
                                    "Template:"
                                    "VaultsOfTheArtificersUpgrade"
                                ),
                                "revisions": [
                                    {
                                        "slots": {
                                            "main": {
                                                "*": template_source,
                                            }
                                        }
                                    }
                                ],
                            }
                        }
                    }
                }

            requested.append(
                (params.get("title"), params["text"])
            )

            return {
                "expandtemplates": {
                    "wikitext": _batched_response(
                        params["text"],
                        {
                            call: title_outputs.get(
                                params.get("title"),
                                "",
                            )
                        },
                    )
                }
            }

        with TemporaryDirectory() as tmp:
            self._write_item_page(
                tmp,
                100,
                "Item:Blasting Chime (level 7)",
                "Blasting Chime",
                call,
            )

            self._write_item_page(
                tmp,
                101,
                "Item:Epic Blasting Chime",
                "Epic Blasting Chime",
                call,
            )

            self._save_item_manifest(
                tmp,
                [
                    (
                        100,
                        "Item:Blasting Chime (level 7)",
                    ),
                    (101, "Item:Epic Blasting Chime"),
                ],
            )

            with patch.object(
                Command,
                "api_request",
                new=fake_api_request,
            ):
                call_command(
                    "render_enhancements",
                    snapshot=Path(tmp),
                )

            # A page-context template must never be rendered
            # without a title: that only yields the fallback.
            self.assertEqual(
                [
                    text
                    for title, text in requested
                    if title is None
                    and call in text
                ],
                [],
            )

            low = render_store.get(
                call,
                Path(tmp),
                title="Item:Blasting Chime (level 7)",
            )
            epic = render_store.get(
                call,
                Path(tmp),
                title="Item:Epic Blasting Chime",
            )

            self.assertIsNotNone(low)
            self.assertEqual(
                low.canonical_name,
                "Weaken Construct",
            )
            self.assertEqual(
                low.page_title,
                "Item:Blasting Chime (level 7)",
            )

            self.assertIsNotNone(epic)
            self.assertEqual(
                epic.canonical_name,
                "Anthem",
            )

            self.assertIsNone(
                render_store.get(call, Path(tmp))
            )

    def test_render_removes_stale_titleless_context_render(self):
        call = "{{VaultsOfTheArtificersUpgrade}}"
        template_source = (
            "{{#switch:{{FULLPAGENAMEE}}\n"
            "|item:blasting_chime_(level_7) = "
            "[[Weaken Construct]]\n"
            "|#default = * See the item description page "
            "for details.\n"
            "}}"
        )

        def fake_api_request(self, params):
            if params["action"] == "query":
                return {
                    "query": {
                        "pages": {
                            "1": {
                                "title": (
                                    "Template:"
                                    "VaultsOfTheArtificersUpgrade"
                                ),
                                "revisions": [
                                    {
                                        "slots": {
                                            "main": {
                                                "*": template_source,
                                            }
                                        }
                                    }
                                ],
                            }
                        }
                    }
                }

            return {
                "expandtemplates": {
                    "wikitext": _batched_response(
                        params["text"],
                        {call: "[[Weaken Construct]]"},
                    )
                }
            }

        with TemporaryDirectory() as tmp:
            self._write_item_page(
                tmp,
                100,
                "Item:Blasting Chime (level 7)",
                "Blasting Chime",
                call,
            )

            self._save_item_manifest(
                tmp,
                [
                    (
                        100,
                        "Item:Blasting Chime (level 7)",
                    ),
                ],
            )

            # Simulate the pre-fix state: a titleless render of
            # the page-context template cached the fallback text.
            render_store.save(
                {
                    "template_call": call,
                    "canonical_name": (
                        "* See the item description page "
                        "for details."
                    ),
                    "display_text": (
                        "* See the item description page "
                        "for details."
                    ),
                    "raw_html": (
                        "* See the item description page "
                        "for details."
                    ),
                },
                Path(tmp),
            )

            with patch.object(
                Command,
                "api_request",
                new=fake_api_request,
            ):
                call_command(
                    "render_enhancements",
                    snapshot=Path(tmp),
                )

            self.assertIsNone(
                render_store.get(call, Path(tmp))
            )

            render = render_store.get(
                call,
                Path(tmp),
                title="Item:Blasting Chime (level 7)",
            )

            self.assertIsNotNone(render)
            self.assertEqual(
                render.canonical_name,
                "Weaken Construct",
            )


def _rule_config(template_name):
    return next(
        rule["config"]
        for rule in DEFAULT_RULES
        if rule["template_name"] == template_name
    )


class RuleDisplayTests(TestCase):
    """Handlers compose the wiki's display text locally, so the wiki
    does not need to be asked about those template calls. Each case is
    verified against a real wiki render cached in wiki_snapshot/renders/.
    """

    def test_spell_power_composes_wiki_display(self):
        row = spell_power(
            "SpellPower",
            "Combustion|54",
            _rule_config("SpellPower"),
        )[0]
        self.assertEqual(row["value"], "Combustion 54")
        self.assertEqual(row["display_text"], "Combustion +54")

    def test_spell_power_multi_word_type(self):
        row = spell_power(
            "SpellPower",
            "Power of the Sacred Ground|148",
            _rule_config("SpellPower"),
        )[0]
        self.assertEqual(
            row["display_text"],
            "Power of the Sacred Ground +148",
        )

    def test_spell_power_capitalizes_lowercase_type(self):
        row = spell_power(
            "SpellPower",
            "corrosion|54",
            _rule_config("SpellPower"),
        )[0]
        self.assertEqual(row["display_text"], "Corrosion +54")

    def test_spell_power_non_numeric_amount_keeps_display_empty(self):
        row = spell_power(
            "SpellPower",
            "Combustion|foo",
            _rule_config("SpellPower"),
        )[0]
        self.assertEqual(row["display_text"], "")

    def test_spell_lore_roman_magnitude(self):
        row = spell_lore(
            "Spelllore",
            "Acid|III",
            _rule_config("Spelllore"),
        )[0]
        self.assertEqual(row["display_text"], "Acid Lore III")

    def test_spell_lore_numeric_magnitude_is_percent(self):
        row = spell_lore(
            "Spelllore",
            "Sacred Ground|22",
            _rule_config("Spelllore"),
        )[0]
        self.assertEqual(
            row["display_text"],
            "Sacred Ground Lore +22%",
        )

    def test_enhancement_bonus_single_row(self):
        rows = enhancement_bonus(
            "Enhancement bonus",
            "w|5",
            _rule_config("Enhancement bonus"),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], "+5")
        self.assertEqual(
            rows[0]["display_text"],
            "+5 Enhancement Bonus",
        )

    def test_enhancement_bonus_implement_two_rows(self):
        rows = enhancement_bonus(
            "Enhancement bonus",
            "i|7",
            _rule_config("Enhancement bonus"),
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            rows[0]["display_text"],
            "Spellcasting Implement +21",
        )
        self.assertEqual(
            rows[1]["display_text"],
            "+7 Enhancement Bonus",
        )

    def test_enhancement_bonus_unverified_type_keeps_display_empty(self):
        rows = enhancement_bonus(
            "Enhancement bonus",
            "o|5",
            _rule_config("Enhancement bonus"),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["display_text"], "")

    def test_healing_amp_composes_display(self):
        row = healing_amp(
            "HealingAmp",
            "8|h|Exceptional",
            _rule_config("HealingAmp"),
        )[0]
        self.assertEqual(
            row["display_text"],
            "Exceptional Healing Amplification +8",
        )

    def test_healing_amp_without_detail(self):
        row = healing_amp(
            "HealingAmp",
            "10|h",
            _rule_config("HealingAmp"),
        )[0]
        self.assertEqual(
            row["display_text"],
            "Healing Amplification +10",
        )

    def test_item_scope_uses_wiki_display_when_rendered(self):
        # Slot-name mapping lives in the wiki template
        # (Gloves -> "Mythic Hands Boost"), so the render must win.
        call = "{{Mythic|Gloves|+1 or +3}}"

        with TemporaryDirectory() as tmp:
            render_store.save(
                {
                    "template_call": call,
                    "canonical_name": "Mythic Boost",
                    "display_text": "Mythic Hands Boost +1 or +3",
                    "value": "+1",
                    "detail": "Mythic Hands Boost or +3",
                    "raw_html": (
                        "<b>Mythic Hands Boost +1 or +3</b>"
                    ),
                },
                Path(tmp),
            )

            render_store.set_current_dir(tmp)

            try:
                rows = expand_item_rules(
                    {
                        "item_type": "Gloves",
                        "named_type_arg": "gloves",
                        "mythic": "1",
                    }
                )
            finally:
                render_store.clear_current_dir()

        self.assertEqual(
            rows[0]["display_text"],
            "Mythic Hands Boost +1 or +3",
        )
        self.assertEqual(rows[0]["value"], "Gloves Boost +1 or +3")

    def test_item_scope_without_render_has_no_display_text(self):
        with TemporaryDirectory() as tmp:
            render_store.set_current_dir(tmp)

            try:
                rows = expand_item_rules(
                    {
                        "item_type": "Weapon",
                        "named_type_arg": "weapon",
                        "mythic": "1",
                    }
                )
            finally:
                render_store.clear_current_dir()

        self.assertEqual(rows[0]["display_text"], "")
        self.assertEqual(rows[0]["value"], "Weapon Boost +2 or +4")

    def test_item_enhancement_display_name_uses_override(self):
        from catalog.models import (
            Enhancement,
            EnhancementVariant,
            Item,
            ItemEnhancement,
        )

        item = Item.objects.create(
            name="Test Dagger",
            wiki_title="Test Dagger",
            wiki_page_id=999,
        )
        enhancement = Enhancement.objects.create(
            name="Seeker",
        )
        variant = EnhancementVariant.objects.create(
            enhancement=enhancement,
            value="+10",
            detail="",
            display_text="Seeker +10",
        )
        ItemEnhancement.objects.create(
            item=item,
            variant=variant,
        )

        # Without an override the wiki's verbatim text wins.
        self.assertEqual(
            ItemEnhancement.objects.get(
                item=item
            ).display_name,
            "Seeker +10",
        )

        # An override replaces the name everywhere, so it composes
        # with the value instead of being shadowed by wiki text.
        enhancement.display_name = "Bloop"
        enhancement.save()

        self.assertEqual(
            ItemEnhancement.objects.get(
                item=item
            ).display_name,
            "Bloop +10",
        )

        # The wiki name is never touched.
        self.assertEqual(
            Enhancement.objects.get(
                id=enhancement.id
            ).name,
            "Seeker",
        )

    def test_variant_shared_across_items_edits_propagate(self):
        from catalog.models import (
            Enhancement,
            EnhancementVariant,
            Item,
            ItemEnhancement,
        )

        enhancement = Enhancement.objects.create(
            name="Seeker",
        )
        variant = EnhancementVariant.objects.create(
            enhancement=enhancement,
            value="+10",
            detail="",
            display_text="Seeker +10",
            magnitude=10.0,
        )

        for index in range(3):
            item = Item.objects.create(
                name=f"Test Dagger {index}",
                wiki_title=f"Test Dagger {index}",
                wiki_page_id=1000 + index,
            )
            ItemEnhancement.objects.create(
                item=item,
                variant=variant,
            )

        # One variant row backs all three items.
        self.assertEqual(EnhancementVariant.objects.count(), 1)
        self.assertEqual(ItemEnhancement.objects.count(), 3)

        # Editing the variant once updates every item's display.
        variant.display_text = "Seeker +12"
        variant.value = "+12"
        variant.magnitude = 12.0
        variant.save()

        for item in Item.objects.all():
            self.assertEqual(
                item.enhancements.get().display_name,
                "Seeker +12",
            )

    def test_import_deduplicates_variant_rows(self):
        from catalog.models import EnhancementVariant, Item

        with TemporaryDirectory() as tmp:
            self.command = Command()

            self.command.write_snapshot_page(
                Path(tmp),
                {
                    "page_id": 300,
                    "title": "Test Scorched Sword",
                    "revision_id": 90,
                    "wikitext": SAMPLE_WIKITEXT,
                },
            )
            self.command.write_snapshot_page(
                Path(tmp),
                {
                    "page_id": 301,
                    "title": "Test Base Dagger",
                    "revision_id": 91,
                    "wikitext": BASE_WIKITEXT,
                },
            )
            self.command.save_snapshot_manifest(
                Path(tmp),
                {
                    "300": {
                        "title": "Test Scorched Sword",
                        "revision_id": 90,
                        "file": "pages/300.json",
                    },
                    "301": {
                        "title": "Test Base Dagger",
                        "revision_id": 91,
                        "file": "pages/301.json",
                    },
                },
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

        # "Enhancement bonus" appears on both items (i|3 and w|2);
        # the two distinct versions are stored once each, not per item.
        enhancement_bonus = Item.objects.get(
            wiki_page_id=300
        ).enhancements.filter(
            variant__enhancement__name="Enhancement bonus"
        )
        self.assertTrue(enhancement_bonus.exists())

        total_variants = EnhancementVariant.objects.count()
        total_links = (
            Item.objects.values_list(
                "enhancements__id",
            ).count()
        )

        self.assertLess(total_variants, total_links)

    def test_display_name_must_be_unique_when_set(self):
        from django.core.exceptions import ValidationError

        from catalog.models import Enhancement

        Enhancement.objects.create(
            name="Seeker",
            display_name="Bloop",
        )

        duplicate = Enhancement(
            name="Something Else",
            display_name="Bloop",
        )

        with self.assertRaises(ValidationError):
            duplicate.full_clean()

        # Blank display names (the default) never collide.
        first = Enhancement(name="First")
        second = Enhancement(name="Second")
        first.full_clean()
        second.full_clean()

    def test_display_name_cannot_match_existing_name(self):
        from django.core.exceptions import ValidationError

        from catalog.models import Enhancement

        slime = Enhancement.objects.create(name="Slime")

        # Renaming Slime to an unused name is fine.
        slime.display_name = "Bloop"
        slime.full_clean()

        # You can't rename another enhancement to Slime's wiki name.
        seeker = Enhancement(name="Seeker")
        seeker.display_name = "Slime"

        with self.assertRaises(ValidationError):
            seeker.full_clean()

        # A display name matching its own wiki name is harmless.
        seeker.display_name = "Seeker"
        seeker.full_clean()

    def test_dropdown_sorts_on_display_name(self):
        from catalog.models import (
            Enhancement,
            EnhancementVariant,
            Item,
            ItemEnhancement,
        )

        # "Zulu" renamed to "Alpha": by label it sorts before
        # "Mango", but by wiki name it would sort after it.
        zulu = Enhancement.objects.create(
            name="Zulu",
            display_name="Alpha",
        )
        mango = Enhancement.objects.create(
            name="Mango",
        )

        for index, enhancement in enumerate(
            [zulu, mango]
        ):
            item = Item.objects.create(
                name=f"Test Item {index}",
                wiki_title=f"Test Item {index}",
                wiki_page_id=3000 + index,
            )
            variant = EnhancementVariant.objects.create(
                enhancement=enhancement,
                value="+1",
                display_text=f"{enhancement.name} +1",
            )
            ItemEnhancement.objects.create(
                item=item,
                variant=variant,
            )

        from django.test import Client

        response = Client().get("/")
        html = response.content.decode("utf-8")

        import re

        option_values = re.findall(
            r'<option value="([^"]*)">',
            html,
        )
        names = [
            value
            for value in option_values
            if value
        ]

        # Label sort puts "Alpha" (Zulu's display name) before
        # "Mango"; a name sort would put Mango first.
        self.assertEqual(names, ["Zulu", "Mango"])


class RenderStoreTests(TestCase):

    def setUp(self):
        render_store.clear_cache()
        render_store.clear_current_dir()

    def tearDown(self):
        render_store.clear_cache()
        render_store.clear_current_dir()

    def test_save_get_round_trip(self):
        call = "{{Clicky|Nimbus of Light|1|50|50}}"

        with TemporaryDirectory() as tmp:
            render_store.save(
                {
                    "template_call": call,
                    "canonical_name": "Nimbus of Light",
                    "display_text": "Nimbus of Light \u2014 "
                    "50 Charges (Recharged/Day:50)",
                    "value": "",
                    "detail": "",
                    "raw_html": "<span>[[Nimbus of Light]]</span>",
                },
                Path(tmp),
            )

            entry = render_store.get(call, Path(tmp))
            render_store.clear_cache()
            re_read = render_store.get(call, Path(tmp))

        self.assertIsNotNone(entry)
        self.assertEqual(
            entry.canonical_name,
            "Nimbus of Light",
        )
        self.assertEqual(
            re_read.display_text,
            "Nimbus of Light \u2014 50 Charges "
            "(Recharged/Day:50)",
        )

    def test_delete_all_clears_store(self):
        call = "{{Clicky|Nimbus of Light|1|50|50}}"

        with TemporaryDirectory() as tmp:
            render_store.save(
                {
                    "template_call": call,
                    "canonical_name": "Nimbus of Light",
                    "display_text": "Nimbus of Light",
                },
                Path(tmp),
            )

            render_store.delete_all(Path(tmp))

            self.assertIsNone(
                render_store.get(call, Path(tmp))
            )
            self.assertEqual(
                render_store.entries(Path(tmp)),
                [],
            )


class ParseMagnitudeTests(TestCase):

    def test_signed_numbers(self):
        self.assertEqual(parse_magnitude("+22%"), 22)
        self.assertEqual(parse_magnitude("+26%"), 26)
        self.assertEqual(parse_magnitude("+12"), 12)
        self.assertEqual(parse_magnitude("54"), 54)
        self.assertEqual(parse_magnitude("-5"), -5)

    def test_flavored_numbers(self):
        self.assertEqual(parse_magnitude("Combustion 54"), 54)
        self.assertEqual(parse_magnitude("Shield Bashing +12"), 12)
        self.assertEqual(parse_magnitude("Fire Absorption +26%"), 26)

    def test_ranges_use_lower_bound(self):
        self.assertEqual(parse_magnitude("+2 or +4"), 2)
        self.assertEqual(parse_magnitude("Weapon Boost +2 or +4"), 2)

    def test_fractions(self):
        self.assertEqual(parse_magnitude("1/2"), 0.5)

    def test_roman_numerals(self):
        self.assertEqual(parse_magnitude("Fire III"), 3)
        self.assertEqual(parse_magnitude("Wizardry II"), 2)
        self.assertEqual(parse_magnitude("Riposte XII"), 12)

    def test_non_numeric(self):
        self.assertIsNone(parse_magnitude("Blue"))
        self.assertIsNone(parse_magnitude("Improved"))
        self.assertIsNone(parse_magnitude(""))
        self.assertIsNone(parse_magnitude(None))

    def test_magnitudes_stored_on_import(self):
        with TemporaryDirectory() as tmp:
            self.command = Command()

            self.command.write_snapshot_page(
                Path(tmp),
                {
                    "page_id": 100,
                    "title": "Test Scorched Sword",
                    "revision_id": 55,
                    "wikitext": SAMPLE_WIKITEXT,
                },
            )

            self.command.save_snapshot_manifest(
                Path(tmp),
                {
                    "100": {
                        "title": "Test Scorched Sword",
                        "revision_id": 55,
                        "file": "pages/100.json",
                    }
                },
            )

            self.command.load_snapshot_to_db(
                Path(tmp),
                force=True,
            )

            item = Item.objects.get(wiki_page_id=100)
            magnitudes = {
                name: magnitude
                for name, magnitude in item.enhancements.values_list(
                    "variant__enhancement__name",
                    "variant__magnitude",
                )
            }

            self.assertEqual(magnitudes["Spell Power"], 54)
            self.assertEqual(magnitudes["Spell Lore"], 3)
            self.assertEqual(magnitudes["Mythic"], 2)
            self.assertIsNone(magnitudes["Spellpen"])
            self.assertIsNone(magnitudes["Riposte"])


class SearchViewMinFilterTests(TestCase):

    def setUp(self):
        self.command = Command()

    def test_min_filter_via_search_url(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            snapshot_dir = Path(tmp)

            self.command.write_snapshot_page(
                snapshot_dir,
                {
                    "page_id": 300,
                    "title": "Test Scorched Sword",
                    "revision_id": 90,
                    "wikitext": SAMPLE_WIKITEXT,
                },
            )

            self.command.write_snapshot_page(
                snapshot_dir,
                {
                    "page_id": 301,
                    "title": "Test Base Dagger",
                    "revision_id": 91,
                    "wikitext": BASE_WIKITEXT,
                },
            )

            self.command.save_snapshot_manifest(
                snapshot_dir,
                {
                    "300": {
                        "title": "Test Scorched Sword",
                        "revision_id": 90,
                        "file": "pages/300.json",
                    },
                    "301": {
                        "title": "Test Base Dagger",
                        "revision_id": 91,
                        "file": "pages/301.json",
                    },
                },
            )

            self.command.load_snapshot_to_db(
                snapshot_dir,
                force=True,
            )

            from django.test import Client

            client = Client()

            matching = client.get(
                "/",
                {
                    "enhancement_0": "Spell Power",
                    "enhancement_min_0": "20",
                },
            )
            self.assertContains(
                matching,
                "Test Scorched Sword",
            )

            too_high = client.get(
                "/",
                {
                    "enhancement_0": "Spell Power",
                    "enhancement_min_0": "60",
                },
            )
            self.assertNotContains(
                too_high,
                "Test Scorched Sword",
            )


class SearchPageUITests(TestCase):

    def setUp(self):
        from django.test import Client

        self.client = Client()

        Item.objects.create(
            name="Beta Wand",
            wiki_title="Item:Beta Wand",
            wiki_page_id=1,
            item_type="Wand",
            minimum_level=5,
        )
        Item.objects.create(
            name="alpha Dagger",
            wiki_title="Item:Alpha Dagger",
            wiki_page_id=2,
            item_type="Dagger",
            minimum_level=3,
        )
        Item.objects.create(
            name="Gamma Staff",
            wiki_title="Item:Gamma Staff",
            wiki_page_id=3,
            item_type="Staff",
            minimum_level=9,
        )
        Item.objects.create(
            name="Allegiance",
            wiki_title="Item:Allegiance (level 12)",
            wiki_page_id=4,
            item_type="Quarterstaff",
            minimum_level=1,
        )

    def test_no_search_shows_count_but_no_items(self):
        response = self.client.get("/")

        self.assertContains(
            response,
            "in database.",
        )
        self.assertContains(
            response,
            "Enter search criteria above to find items.",
        )
        self.assertNotContains(
            response,
            "Beta Wand",
        )
        self.assertNotContains(
            response,
            "Gamma Staff",
        )

    def test_search_lists_items_and_result_count(self):
        response = self.client.get(
            "/",
            {"name": "wand"},
        )

        self.assertContains(
            response,
            "Beta Wand",
        )
        self.assertContains(
            response,
            "result",
        )
        self.assertNotContains(
            response,
            "alpha Dagger",
        )

    def test_search_shows_variant_display_name(self):
        response = self.client.get(
            "/",
            {"name": "allegiance"},
        )

        # The page title disambiguates variants while `name` stays
        # canonical.
        self.assertContains(
            response,
            "Allegiance (level 12)",
        )

    def test_default_sort_is_name_ascending(self):
        response = self.client.get(
            "/",
            {"min_level": "0"},
        )

        html = response.content.decode()

        positions = [
            html.index(name)
            for name in ("Alpha Dagger", "Beta Wand", "Gamma Staff")
        ]

        self.assertEqual(
            positions,
            sorted(positions),
        )

    def test_sort_name_descending(self):
        response = self.client.get(
            "/",
            {"min_level": "0", "sort": "-name"},
        )

        html = response.content.decode()

        positions = [
            html.index(name)
            for name in ("Gamma Staff", "Beta Wand", "Alpha Dagger")
        ]

        self.assertEqual(
            positions,
            sorted(positions),
        )

    def test_sort_by_minimum_level(self):
        response = self.client.get(
            "/",
            {"min_level": "0", "sort": "minimum_level"},
        )

        html = response.content.decode()

        positions = [
            html.index(name)
            for name in ("Alpha Dagger", "Beta Wand", "Gamma Staff")
        ]

        self.assertEqual(
            positions,
            sorted(positions),
        )

    def test_sort_by_item_type(self):
        response = self.client.get(
            "/",
            {"min_level": "0", "sort": "-item_type"},
        )

        html = response.content.decode()

        positions = [
            html.index(name)
            for name in ("Beta Wand", "Gamma Staff", "Alpha Dagger")
        ]

        self.assertEqual(
            positions,
            sorted(positions),
        )

    def test_sort_links_preserve_other_filters(self):
        response = self.client.get(
            "/",
            {"name": "wand", "sort": "name"},
        )

        html = response.content.decode()

        self.assertIn(
            "?name=wand&amp;sort=-name",
            html,
        )

    def test_admin_and_new_search_links(self):
        response = self.client.get("/")

        self.assertContains(
            response,
            'href="/admin/"',
        )
        self.assertContains(
            response,
            'href="/"',
        )

    def test_sync_state_date_is_displayed(self):
        SyncState.objects.create(
            as_of=datetime(
                2026,
                8,
                20,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        response = self.client.get("/")

        self.assertContains(
            response,
            "Database current with DDO Wiki as of August 20, 2026",
        )

    def test_loading_snapshot_records_sync_state(self):
        command = Command()

        with TemporaryDirectory() as tmp:
            snapshot_dir = Path(tmp)

            command.write_snapshot_page(
                snapshot_dir,
                {
                    "page_id": 300,
                    "title": "Test Scorched Sword",
                    "revision_id": 90,
                    "wikitext": SAMPLE_WIKITEXT,
                },
            )

            command.save_snapshot_manifest(
                snapshot_dir,
                {
                    "300": {
                        "title": "Test Scorched Sword",
                        "revision_id": 90,
                        "file": "pages/300.json",
                    },
                },
            )

            command.load_snapshot_to_db(
                snapshot_dir,
                force=True,
            )

        state = SyncState.objects.first()
        self.assertIsNotNone(state)
