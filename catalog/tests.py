import json
import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from catalog.enchantment_values import parse_magnitude
from catalog.models import Item, SyncState
from catalog.services import apply_enchantment_filter
from catalog.views import enchantment_options
from catalog.wiki_api import WikiAPI


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
        from catalog.management.commands.load_item_files import (
            Command as LoadCommand,
        )

        tree = [
            {
                "text": "+3 Enhancement Bonus",
                "tooltip": None,
                "children": [],
                "links": [
                    {
                        "target": "/page/Enhancement_bonus",
                        "title": "Enhancement bonus",
                        "text": "+3 Enhancement Bonus",
                    }
                ],
            },
            {
                "text": "Combustion 54",
                "tooltip": None,
                "children": [],
                "links": [
                    {
                        "target": "/page/Spell_Power",
                        "title": "Spell Power",
                        "text": "Combustion 54",
                    }
                ],
            },
            {
                "text": "Fire Lore III",
                "tooltip": None,
                "children": [],
                "links": [
                    {
                        "target": "/page/Spell_Lore",
                        "title": "Spell Lore",
                        "text": "Fire III",
                    }
                ],
            },
            {
                "text": "Mythic Weapon Boost +2 or +4",
                "tooltip": None,
                "children": [],
                "links": [
                    {
                        "target": "/page/Mythic_Boost",
                        "title": "Mythic Boost",
                        "text": "Mythic Weapon Boost +2 or +4",
                    }
                ],
            },
            {
                "text": "Spellpen I",
                "tooltip": None,
                "children": [],
                "links": [
                    {
                        "target": "/page/Spellpen",
                        "title": "Spellpen",
                        "text": "Spellpen I",
                    }
                ],
            },
            {
                "text": "Riposte I",
                "tooltip": None,
                "children": [],
                "links": [
                    {
                        "target": "/page/Riposte",
                        "title": "Riposte",
                        "text": "Riposte I",
                    }
                ],
            },
        ]

        with TemporaryDirectory() as tmp:
            record = {
                "page_title": "Item:Test Scorched Sword",
                "page_id": 100,
                "fetched_at": "2026-08-15T00:00:00+00:00",
                "html": "<html></html>",
                "enchantments": tree,
            }

            items_dir = Path(tmp)
            path = items_dir / "Item_Test_Scorched_Sword.json"
            path.write_text(
                json.dumps(record, ensure_ascii=False),
                encoding="utf-8",
            )

            call_command(LoadCommand(), items=items_dir)

            item = Item.objects.get(wiki_page_id=100)
            magnitudes = {
                name: magnitude
                for name, magnitude in item.enchantments.values_list(
                    "variant__enchantment__name",
                    "variant__magnitude",
                )
            }

            self.assertEqual(magnitudes["Spell Power"], 54)
            self.assertEqual(magnitudes["Spell Lore"], 3)
            self.assertEqual(magnitudes["Mythic Boost"], 2)
            self.assertEqual(magnitudes["Spellpen"], 1)
            self.assertEqual(magnitudes["Riposte"], 1)


class SearchViewMinFilterTests(TestCase):

    def test_min_filter_via_search_url(self):
        from catalog.management.commands.load_item_files import (
            Command as LoadCommand,
        )

        sword_tree = [
            {
                "text": "+3 Enhancement Bonus",
                "tooltip": None,
                "children": [],
                "links": [
                    {
                        "target": "/page/Enhancement_bonus",
                        "title": "Enhancement bonus",
                        "text": "+3 Enhancement Bonus",
                    }
                ],
            },
            {
                "text": "Combustion 54",
                "tooltip": None,
                "children": [],
                "links": [
                    {
                        "target": "/page/Spell_Power",
                        "title": "Spell Power",
                        "text": "Combustion 54",
                    }
                ],
            },
        ]

        dagger_tree = [
            {
                "text": "+2 Enhancement Bonus",
                "tooltip": None,
                "children": [],
                "links": [
                    {
                        "target": "/page/Enhancement_bonus",
                        "title": "Enhancement bonus",
                        "text": "+2 Enhancement Bonus",
                    }
                ],
            },
        ]

        with TemporaryDirectory() as tmp:
            items_dir = Path(tmp)

            sword_record = {
                "page_title": "Item:Test Scorched Sword",
                "page_id": 300,
                "fetched_at": "2026-08-15T00:00:00+00:00",
                "html": (
                    "<table><tr>"
                    "<th>Weapon Type</th><td>Longsword</td>"
                    "</tr></table>"
                ),
                "enchantments": sword_tree,
            }

            dagger_record = {
                "page_title": "Item:Test Base Dagger",
                "page_id": 301,
                "fetched_at": "2026-08-15T00:00:00+00:00",
                "html": (
                    "<table><tr>"
                    "<th>Weapon Type</th><td>Dagger</td>"
                    "</tr></table>"
                ),
                "enchantments": dagger_tree,
            }

            for record in [sword_record, dagger_record]:
                safe = record["page_title"].replace(
                    ":", "_"
                ).replace(",", "_").replace(" ", "_")
                (items_dir / f"{safe}.json").write_text(
                    json.dumps(record, ensure_ascii=False),
                    encoding="utf-8",
                )

            call_command(LoadCommand(), items=items_dir)

            from django.test import Client

            client = Client()

            matching = client.get(
                "/",
                {
                    "enchantment_0": "Spell Power",
                    "enchantment_min_0": "20",
                },
            )
            self.assertContains(
                matching,
                "Test Scorched Sword",
            )

            too_high = client.get(
                "/",
                {
                    "enchantment_0": "Spell Power",
                    "enchantment_min_0": "60",
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
            item_class="Weapon",
            minimum_level=5,
        )
        Item.objects.create(
            name="alpha Dagger",
            wiki_title="Item:Alpha Dagger",
            wiki_page_id=2,
            item_type="Dagger",
            item_class="Weapon",
            minimum_level=3,
        )
        Item.objects.create(
            name="Gamma Staff",
            wiki_title="Item:Gamma Staff",
            wiki_page_id=3,
            item_type="Staff",
            item_class="Weapon",
            minimum_level=9,
        )
        Item.objects.create(
            name="Allegiance",
            wiki_title="Item:Allegiance (level 12)",
            wiki_page_id=4,
            item_type="Quarterstaff",
            item_class="Weapon",
            minimum_level=1,
        )
        Item.objects.create(
            name="Cloak of Shadows",
            wiki_title="Item:Cloak of Shadows",
            wiki_page_id=5,
            item_type="Cloak",
            item_class="Clothing",
            minimum_level=7,
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

    def test_category_dropdown_lists_categories(self):
        response = self.client.get("/")

        self.assertContains(
            response,
            '<option\n    value="Weapon"\n',
        )
        self.assertContains(
            response,
            '<option\n    value="Clothing"\n',
        )

    def test_type_dropdown_defaults_to_list_by_category(self):
        response = self.client.get(
            "/",
            {"category": "Weapon"},
        )

        html = response.content.decode()

        self.assertIn(
            '<option value="">List by Category</option>',
            html,
        )

        # The type dropdown is scoped to the category and lists the
        # subtype half of "Category: Type" (e.g. "Dagger", not
        # "Weapon: Dagger"); the clothing type is excluded.
        self.assertIn(
            '<option\n    value="Dagger"\n',
            html,
        )
        self.assertIn(
            '<option\n    value="Wand"\n',
            html,
        )
        self.assertNotIn(
            '<option\n    value="Cloak"\n',
            html,
        )

    def test_category_filter_lists_everything_in_category(self):
        response = self.client.get(
            "/",
            {"category": "Clothing"},
        )

        self.assertContains(
            response,
            "Cloak of Shadows",
        )
        self.assertNotContains(
            response,
            "Beta Wand",
        )

    def test_category_and_type_filter_narrows_search(self):
        response = self.client.get(
            "/",
            {"category": "Weapon", "type": "Dagger"},
        )

        self.assertContains(
            response,
            "Alpha Dagger",
        )
        self.assertNotContains(
            response,
            "Beta Wand",
        )
        self.assertNotContains(
            response,
            "Cloak of Shadows",
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
        from catalog.management.commands.load_item_files import (
            Command as LoadCommand,
        )

        tree = [
            {
                "text": "+3 Enhancement Bonus",
                "tooltip": None,
                "children": [],
                "links": [
                    {
                        "target": "/page/Enhancement_bonus",
                        "title": "Enhancement bonus",
                        "text": "+3 Enhancement Bonus",
                    }
                ],
            },
        ]

        with TemporaryDirectory() as tmp:
            items_dir = Path(tmp)

            record = {
                "page_title": "Item:Test Scorched Sword",
                "page_id": 300,
                "fetched_at": "2026-08-15T00:00:00+00:00",
                "html": "<html></html>",
                "enchantments": tree,
            }

            path = items_dir / "Item_Test_Scorched_Sword.json"
            path.write_text(
                json.dumps(record, ensure_ascii=False),
                encoding="utf-8",
            )

            call_command(LoadCommand(), items=items_dir)

        state = SyncState.objects.first()
        self.assertIsNotNone(state)


class EnchantmentHtmlParseTests(TestCase):
    """Link capture and nesting in the full-page parser."""

    def _node(self, text, **overrides):
        node = {"text": text, "tooltip": None, "children": []}

        node.update(overrides)

        return node

    def test_parse_captures_links(self):
        from catalog import enchantment_html

        cell = (
            "<ul>"
            '<li><a href="/page/Keen" title="Keen">Keen</a></li>'
            '<li><a href="/page/Enhancement_bonus" '
            'title="Enhancement bonus">+7 Enhancement Bonus</a></li>'
            "</ul>"
        )

        tree = enchantment_html.parse_enchantments_cell(cell)

        self.assertEqual(
            tree[0]["links"],
            [
                {
                    "target": "/page/Keen",
                    "title": "Keen",
                    "text": "Keen",
                }
            ],
        )
        self.assertEqual(
            tree[1]["text"],
            "+7 Enhancement Bonus",
        )

    def test_tooltip_content_is_not_a_link(self):
        from catalog import enchantment_html

        cell = (
            "<ul>"
            '<li><a href="/page/Keen" title="Keen">Keen</a>'
            '<span class="popup tooltip">hover text '
            "containing nothing</span></li>"
            "</ul>"
        )

        tree = enchantment_html.parse_enchantments_cell(cell)

        self.assertEqual(tree[0]["tooltip"], "hover text containing nothing")
        self.assertEqual(len(tree[0]["links"]), 1)
        self.assertEqual(tree[0]["links"][0]["target"], "/page/Keen")

    def test_parse_nested_tiers(self):
        from catalog import enchantment_html

        cell = (
            "<ul>"
            "<li>Attuned to Heroism"
            "<ul>"
            '<li>Attuned by Heroism: Tier 1'
            "<ul>"
            '<li>Adds <a href="/page/Named_item_sets#Planar_Conflux" '
            'title="Named item sets">Planar Conflux</a></li>'
            "</ul>"
            "</li>"
            "</ul>"
            "</li>"
            "</ul>"
        )

        tree = enchantment_html.parse_enchantments_cell(cell)

        self.assertEqual(tree[0]["text"], "Attuned to Heroism")
        child = tree[0]["children"][0]
        self.assertEqual(
            child["text"],
            "Attuned by Heroism: Tier 1",
        )
        self.assertEqual(
            child["children"][0]["links"][0]["target"],
            "/page/Named_item_sets#Planar_Conflux",
        )

    def test_parse_item_page_extracts_cell(self):
        from catalog import enchantment_html

        page = (
            "<html><body><table>"
            "<tr><th>Item Type</th><td>Weapon</td></tr>"
            "<tr><th>Enchantments</th><td>"
            "<ul><li>Keen</li></ul>"
            "</td></tr>"
            "</table></body></html>"
        )

        result = enchantment_html.parse_item_page(page)

        self.assertEqual(
            result["enchantments"][0]["text"],
            "Keen",
        )

    def test_parse_item_page_extracts_legacy_infobox_row(self):
        from catalog import enchantment_html

        page = (
            "<table>"
            "<tr>"
            '<th class="bg-color-1" style="background:#AACCFF">'
            "Minimum Level"
            "</th>"
            '<td class="bg-color-2" style="background:#CCEEFF">6</td>'
            "</tr>"
            "<tr>"
            '<th class="bg-color-1" style="background:#AACCFF">'
            "Enhancements"
            "</th>"
            '<td class="bg-color-2" style="background:#CCEEFF">'
            '<ul><li><span class="popup has_tooltip with-icon basic">'
            '<a href="/page/Blinding" title="Blinding">Blinding</a>'
            '<span class="popup tooltip wide left below">'
            "<b>Blinding:</b> chance to blind opponents.</span>"
            "</span></li></ul>"
            "</td>"
            "</tr>"
            "</table>"
        )

        result = enchantment_html.parse_item_page(page)

        self.assertEqual(
            result["enchantments"][0]["text"],
            "Blinding",
        )

    def test_extract_cell_prefers_modern_cell_over_legacy_row(self):
        from catalog import enchantment_html

        page = (
            "<table>"
            "<tr><th>Enhancements</th><td>"
            "<ul><li>Legacy Row</li></ul>"
            "</td></tr>"
            "<tr><th>Enchantments</th><td>"
            "<ul><li>Modern Cell</li></ul>"
            "</td></tr>"
            "</table>"
        )

        self.assertIn(
            "Modern Cell",
            enchantment_html.extract_enchantments_cell(page),
        )

        self.assertNotIn(
            "Legacy Row",
            enchantment_html.extract_enchantments_cell(page),
        )


class EnchantmentTreeTests(TestCase):
    """Walk the parsed tree into searchable rows."""

    def _node(self, text, children=None, links=None):
        node = {
            "text": text,
            "tooltip": None,
            "children": children or [],
        }

        if links:
            node["links"] = links

        return node

    def test_leaf_concept_and_value(self):
        from catalog.enchantment_tree import walk_tree

        tree = [
            self._node(
                "+7 Enhancement Bonus",
                links=[
                    {
                        "target": "/page/Enhancement_bonus",
                        "title": "Enhancement bonus",
                        "text": "+7 Enhancement Bonus",
                    }
                ],
            )
        ]

        rows = walk_tree(tree)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].concept, "Enhancement bonus")
        self.assertEqual(rows[0].value, "+7")
        self.assertEqual(rows[0].tier, None)
        self.assertFalse(rows[0].possible)

    def test_adds_prefix_stripped(self):
        from catalog.enchantment_tree import walk_tree

        tree = [
            self._node(
                "Adds Planar Conflux",
                links=[
                    {
                        "target": "/page/Named_item_sets#Planar_Conflux",
                        "title": "Named item sets",
                        "text": "Planar Conflux",
                    }
                ],
            )
        ]

        row = walk_tree(tree)[0]

        self.assertEqual(row.concept, "Planar Conflux")
        self.assertEqual(row.value, "")

    def test_tier_chain(self):
        from catalog.enchantment_tree import walk_tree

        tree = [
            self._node(
                "Attuned to Heroism",
                children=[
                    self._node(
                        "Attuned by Heroism: Tier 1",
                        children=[
                            self._node(
                                "Adds Planar Conflux",
                                links=[
                                    {
                                        "target": "/page/Named_item_sets"
                                        "#Planar_Conflux",
                                        "title": "Named item sets",
                                        "text": "Planar Conflux",
                                    }
                                ],
                            )
                        ],
                    ),
                    self._node(
                        "Attuned by Heroism: Tier 2",
                        children=[
                            self._node(
                                "+7 Enhancement Bonus \u2192 "
                                "+8 Enhancement Bonus",
                                links=[
                                    {
                                        "target": "/page/Enhancement_bonus",
                                        "title": "Enhancement bonus",
                                        "text": "+7 Enhancement Bonus",
                                    },
                                    {
                                        "target": "/page/Enhancement_bonus",
                                        "title": "Enhancement bonus",
                                        "text": "+8 Enhancement Bonus",
                                    },
                                ],
                            )
                        ],
                    ),
                ],
            )
        ]

        rows = walk_tree(tree)

        by_concept = {}

        for row in rows:
            by_concept.setdefault(row.concept, []).append(row)

        self.assertEqual(
            by_concept["Planar Conflux"][0].tier,
            None,
        )
        self.assertEqual(
            by_concept["Enhancement bonus"][0].value,
            "+8",
        )
        self.assertEqual(
            by_concept["Enhancement bonus"][0].tier,
            2,
        )

    def test_alternatives_marked_possible(self):
        from catalog.enchantment_tree import walk_tree

        tree = [
            self._node(
                "A Mysterious Effect - can be any one of these 4 sets:",
                links=[
                    {
                        "target": "/page/A_Mysterious_Effect",
                        "title": "A Mysterious Effect",
                        "text": "A Mysterious Effect",
                    }
                ],
                children=[
                    self._node(
                        "Dexterity +3 and Resistance +3",
                        links=[
                            {
                                "target": "/page/Dexterity",
                                "title": "Dexterity",
                                "text": "Dexterity +3",
                            },
                            {
                                "target": "/page/Resistance_(enchantment)",
                                "title": "Resistance (enchantment)",
                                "text": "Resistance +3",
                            },
                        ],
                    )
                ],
            )
        ]

        rows = walk_tree(tree)

        self.assertEqual(rows[0].concept, "A Mysterious Effect")
        self.assertFalse(rows[0].possible)

        options = {
            row.concept: row for row in rows if row.possible
        }

        self.assertEqual(options["Dexterity"].value, "+3")
        self.assertEqual(options["Resistance (enchantment)"].value, "+3")

    def test_upgradeable_container_children_get_tier_2(self):
        from catalog.enchantment_tree import walk_tree

        tree = [
            self._node(
                "Upgradeable Item (Stormreaver)",
                children=[
                    self._node(
                        "Adds Bone Breaking",
                        links=[
                            {
                                "target": "/page/Bone_Breaking",
                                "title": "Bone Breaking",
                                "text": "Bone Breaking",
                            }
                        ],
                    )
                ],
            )
        ]

        rows = walk_tree(tree)

        self.assertEqual(
            rows[0].concept,
            "Upgradeable Item (Stormreaver)",
        )
        self.assertIsNone(rows[0].tier)
        self.assertEqual(rows[1].concept, "Bone Breaking")
        self.assertEqual(rows[1].tier, 2)
        self.assertFalse(rows[1].possible)

    def test_suppressed_power_children_get_tier_2(self):
        from catalog.enchantment_tree import walk_tree

        tree = [
            self._node(
                "Wisdom +2",
                links=[
                    {
                        "target": "/page/Wisdom",
                        "title": "Wisdom",
                        "text": "Wisdom +2",
                    }
                ],
            ),
            self._node(
                "Suppressed Power",
                children=[
                    self._node(
                        "Wisdom +2 \u2192 Wisdom +6",
                        links=[
                            {
                                "target": "/page/Wisdom",
                                "title": "Wisdom",
                                "text": "Wisdom +2",
                            },
                            {
                                "target": "/page/Wisdom",
                                "title": "Wisdom",
                                "text": "Wisdom +6",
                            },
                        ],
                    )
                ],
            ),
        ]

        rows = walk_tree(tree)

        by_concept = {}

        for row in rows:
            by_concept.setdefault(row.concept, []).append(row)

        base = by_concept["Wisdom"][0]
        upgrade = by_concept["Wisdom"][1]

        self.assertIsNone(base.tier)
        self.assertEqual(base.value, "+2")
        self.assertEqual(upgrade.tier, 2)
        self.assertEqual(upgrade.value, "+6")

    def test_page_title_concept_with_anchor_fallback_value(self):
        from catalog.enchantment_tree import walk_tree

        tree = [
            self._node(
                "Mythic Weapon Boost +2 or +4",
                links=[
                    {
                        "target": "/page/Mythic_Boost",
                        "title": "Mythic Boost",
                        "text": "Mythic Weapon Boost",
                    }
                ],
            )
        ]

        row = walk_tree(tree)[0]

        self.assertEqual(row.concept, "Mythic Boost")
        self.assertEqual(row.value, "+2 or +4")

    def test_tail_value_when_page_title_absent_from_text(self):
        from catalog.enchantment_tree import walk_tree

        tree = [
            self._node(
                "Dodge +8%",
                links=[
                    {
                        "target": "/page/Dodge_bonus",
                        "title": "Dodge bonus",
                        "text": "Dodge +8%",
                    }
                ],
            )
        ]

        row = walk_tree(tree)[0]

        self.assertEqual(row.concept, "Dodge bonus")
        self.assertEqual(row.value, "+8%")

    def test_arrow_value_falls_back_to_tail(self):
        from catalog.enchantment_tree import walk_tree

        tree = [
            self._node(
                "Ice Lore +16% \u2192 Ice Lore +17%",
                links=[
                    {
                        "target": "/page/Spell_Lore",
                        "title": "Spell Lore",
                        "text": "Ice Lore +16%",
                    },
                    {
                        "target": "/page/Spell_Lore",
                        "title": "Spell Lore",
                        "text": "Ice Lore +17%",
                    },
                ],
            )
        ]

        row = walk_tree(tree)[0]

        self.assertEqual(row.concept, "Spell Lore")
        self.assertEqual(row.value, "+17%")

    def test_value_strips_leading_colon(self):
        from catalog.enchantment_tree import walk_tree

        tree = [
            self._node(
                "Rune Arm Imbue: Cold IV",
                links=[
                    {
                        "target": "/page/Rune_Arm_Imbue",
                        "title": "Rune Arm Imbue",
                        "text": "Rune Arm Imbue",
                    }
                ],
            )
        ]

        row = walk_tree(tree)[0]

        self.assertEqual(row.concept, "Rune Arm Imbue")
        self.assertEqual(row.value, "Cold IV")

    def test_anchor_removal_requires_prefix(self):
        from catalog.enchantment_tree import walk_tree

        # The anchor ("Bound to Character on Acquire") describes the
        # effect and is not a value; it must not be removed mid-text.
        tree = [
            self._node(
                "Becomes Bound to Character on Acquire",
                links=[
                    {
                        "target": "/page/Bind",
                        "title": "Bind",
                        "text": "Bound to Character on Acquire",
                    }
                ],
            )
        ]

        row = walk_tree(tree)[0]

        self.assertEqual(row.concept, "Bind")
        self.assertEqual(row.value, "")


class LoadItemFilesTests(TestCase):
    """The load_item_files command rebuilds rows from item files."""

    def _write_item_file(
        self,
        items_dir,
        title,
        tree,
        page_id=1,
        **meta,
    ):
        items_dir.mkdir(parents=True, exist_ok=True)

        record = {
            "page_title": title,
            "page_id": page_id,
            "fetched_at": "2026-08-15T00:00:00+00:00",
            **meta,
            "enchantments": tree,
        }

        safe = title.replace(":", "_").replace(",", "_").replace(
            " ", "_"
        )

        path = items_dir / f"{safe}.json"
        path.write_text(
            json.dumps(record, ensure_ascii=False),
            encoding="utf-8",
        )

        return path

    def test_loads_tree_and_rows(self):
        from catalog.management.commands.load_item_files import (
            Command as LoadCommand,
        )

        tree = [
            {
                "text": "Good Luck +2",
                "tooltip": None,
                "children": [],
                "links": [
                    {
                        "target": "/page/Good_Luck",
                        "title": "Good Luck",
                        "text": "Good Luck +2",
                    }
                ],
            }
        ]

        with TemporaryDirectory() as tmp:
            self._write_item_file(
                Path(tmp),
                "Item:Test Amulet",
                tree,
            )

            call_command(LoadCommand(), items=Path(tmp))

        item = Item.objects.get(wiki_title="Item:Test Amulet")
        self.assertEqual(item.enchantment_tree, tree)

        rows = item.enchantments.all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0].variant.enchantment.name,
            "Good Luck",
        )
        self.assertEqual(rows[0].variant.value, "+2")
        self.assertEqual(rows[0].variant.magnitude, 2)

    def test_reset_clears_other_items_rows(self):
        from catalog.management.commands.load_item_files import (
            Command as LoadCommand,
        )
        from catalog.models import (
            Enchantment,
            EnchantmentVariant,
            ItemEnchantment,
        )

        other = Item.objects.create(
            name="Old Blade",
            wiki_title="Item:Old Blade",
            wiki_page_id=2,
        )

        enchantment, _ = Enchantment.objects.get_or_create(
            name="Stale"
        )

        variant, _ = EnchantmentVariant.objects.get_or_create(
            enchantment=enchantment,
            value="+1",
            detail="",
            display_text="Stale +1",
        )

        ItemEnchantment.objects.create(
            item=other,
            variant=variant,
            tier=None,
        )

        with TemporaryDirectory() as tmp:
            self._write_item_file(
                Path(tmp),
                "Item:Test Amulet",
                [
                    {
                        "text": "Keen",
                        "tooltip": None,
                        "children": [],
                    }
                ],
            )

            call_command(
                LoadCommand(),
                items=Path(tmp),
                reset=True,
            )

        self.assertFalse(
            Item.objects.get(
                wiki_title="Item:Old Blade"
            ).enchantments.exists()
        )

    def test_possible_flag_loaded(self):
        from catalog.management.commands.load_item_files import (
            Command as LoadCommand,
        )

        tree = [
            {
                "text": "A Mysterious Effect - can be any one of these 1 sets:",
                "tooltip": None,
                "children": [
                    {
                        "text": "Charisma +3",
                        "tooltip": None,
                        "children": [],
                        "links": [
                            {
                                "target": "/page/Charisma",
                                "title": "Charisma",
                                "text": "Charisma +3",
                            }
                        ],
                    }
                ],
            }
        ]

        with TemporaryDirectory() as tmp:
            self._write_item_file(
                Path(tmp),
                "Item:Test Helm",
                tree,
            )

            call_command(LoadCommand(), items=Path(tmp))

        item = Item.objects.get(wiki_title="Item:Test Helm")
        rows = list(item.enchantments.order_by("id"))

        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0].possible)
        self.assertTrue(rows[1].possible)

    def test_loader_stores_type_and_min_level(self):
        from catalog.management.commands.load_item_files import (
            Command as LoadCommand,
        )

        tree = [
            {
                "text": "Keen",
                "tooltip": None,
                "children": [],
            }
        ]

        with TemporaryDirectory() as tmp:
            self._write_item_file(
                Path(tmp),
                "Item:Test Meta Blade",
                tree,
                item_type="Longsword",
                item_class="Weapon",
                minimum_level=7,
            )

            call_command(LoadCommand(), items=Path(tmp))

        item = Item.objects.get(
            wiki_title="Item:Test Meta Blade"
        )
        self.assertEqual(item.item_type, "Longsword")
        self.assertEqual(item.minimum_level, 7)

    def test_loader_stores_armor_classification(self):
        from catalog.management.commands.load_item_files import (
            Command as LoadCommand,
        )

        tree = [
            {
                "text": "Keen",
                "tooltip": None,
                "children": [],
            }
        ]

        with TemporaryDirectory() as tmp:
            self._write_item_file(
                Path(tmp),
                "Item:Test Docent",
                tree,
                item_type="Docent",
                item_class="Armor",
                slot="Armor",
                item_kind="Armor",
                armor_type="Docent",
                feat_requirement="None",
                material="Adamantine",
            )

            call_command(LoadCommand(), items=Path(tmp))

        item = Item.objects.get(
            wiki_title="Item:Test Docent"
        )
        self.assertEqual(item.item_type, "Docent")
        self.assertEqual(item.item_class, "Armor")
        self.assertEqual(item.slot, "Armor")
        self.assertEqual(item.item_kind, "Armor")
        self.assertEqual(item.armor_type, "Docent")
        self.assertEqual(item.feat_requirement, "None")
        self.assertEqual(item.material, "Adamantine")

    def test_loader_stores_weapon_meta(self):
        from catalog.management.commands.load_item_files import (
            Command as LoadCommand,
        )

        tree = [
            {
                "text": "Keen",
                "tooltip": None,
                "children": [],
            }
        ]

        with TemporaryDirectory() as tmp:
            self._write_item_file(
                Path(tmp),
                "Item:Test Bastard Sword",
                tree,
                item_type="Bastard Sword",
                item_class="Weapon",
                slot="Main Hand",
                weapon_class="Slashing weapons",
                proficiency_class="Exotic Weapon Proficiency",
                item_kind="Weapon",
            )

            call_command(LoadCommand(), items=Path(tmp))

        item = Item.objects.get(
            wiki_title="Item:Test Bastard Sword"
        )
        self.assertEqual(item.item_type, "Bastard Sword")
        self.assertEqual(item.item_class, "Weapon")
        self.assertEqual(item.slot, "Main Hand")
        self.assertEqual(item.weapon_class, "Slashing weapons")
        self.assertEqual(item.proficiency_class,
                         "Exotic Weapon Proficiency")
        self.assertEqual(item.item_kind, "Weapon")

    def test_loader_stores_import_and_wiki_timestamps(self):
        from catalog.management.commands.load_item_files import (
            Command as LoadCommand,
        )

        tree = [
            {
                "text": "Keen",
                "tooltip": None,
                "children": [],
            }
        ]

        with TemporaryDirectory() as tmp:
            items_dir = Path(tmp)

            record = {
                "page_title": "Item:Test Stamped Blade",
                "page_id": 1,
                "revision_id": 500,
                "fetched_at": "2026-08-15T12:00:00+00:00",
                "revision_timestamp": "2026-08-14T09:30:00Z",
                "html": (
                    "<table><tr>"
                    "<th>Weapon Type</th><td>Dagger</td>"
                    "</tr></table>"
                ),
                "enchantments": tree,
            }

            safe = "Item_Test_Stamped_Blade"
            path = items_dir / f"{safe}.json"
            path.write_text(
                json.dumps(record, ensure_ascii=False),
                encoding="utf-8",
            )

            call_command(LoadCommand(), items=Path(tmp))

        item = Item.objects.get(
            wiki_title="Item:Test Stamped Blade"
        )
        self.assertEqual(item.wiki_revision_id, 500)
        self.assertEqual(
            item.fetched_at,
            datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            item.wiki_revision_timestamp,
            datetime(2026, 8, 14, 9, 30, tzinfo=timezone.utc),
        )

    def test_stale_status(self):
        fresh = Item.objects.create(
            name="Fresh",
            wiki_title="Item:Fresh",
            wiki_page_id=1,
            fetched_at=datetime(
                2026, 8, 15, tzinfo=timezone.utc
            ),
            wiki_revision_timestamp=datetime(
                2026, 8, 14, tzinfo=timezone.utc
            ),
        )
        self.assertEqual(fresh.stale_status, "OK")

        no_revision = Item.objects.create(
            name="No Revision",
            wiki_title="Item:No Revision",
            wiki_page_id=2,
            fetched_at=datetime(
                2026, 8, 15, tzinfo=timezone.utc
            ),
        )
        self.assertEqual(
            no_revision.stale_status,
            "No revision info",
        )

        never = Item.objects.create(
            name="Never",
            wiki_title="Item:Never",
            wiki_page_id=3,
        )
        self.assertEqual(
            never.stale_status,
            "Never fetched",
        )

    def test_loader_updates_sync_state(self):
        from catalog.management.commands.load_item_files import (
            Command as LoadCommand,
        )

        tree = [
            {
                "text": "Keen",
                "tooltip": None,
                "children": [],
            }
        ]

        with TemporaryDirectory() as tmp:
            self._write_item_file(
                Path(tmp),
                "Item:Test Sync",
                tree,
            )

            call_command(LoadCommand(), items=Path(tmp))

        state = SyncState.objects.first()
        self.assertIsNotNone(state)
        self.assertEqual(
            state.as_of.isoformat(),
            "2026-08-15T00:00:00+00:00",
        )


class ItemMetaTests(TestCase):
    """extract_item_meta pulls type/minimum level out of the infobox."""

    def test_weapon_type_and_minimum_level(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<div class='mw-parser-output'>"
            "<table>"
            "<tr><th>Weapon Type</th><td>Great Sword / Slashing "
            "weapons</td></tr>"
            "<tr><th>Minimum Level</th><td>14</td></tr>"
            "</table>"
            "<p>rest of page</p>"
            "</div>"
        )

        self.assertEqual(
            extract_item_meta(html),
             {"item_type": "Great Sword",
             "item_class": "Weapon",
             "slot": "Main Hand",
             "weapon_class": "Slashing weapons",
             "item_kind": "Weapon",
             "minimum_level": 14},
        )

    def test_item_type_and_minlevel_with_colon(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Item Type</th><td>Clothing / Belt</td>"
            "</tr><tr>"
            "<th>Minimum Level:</th><td>13</td>"
            "</tr></table>"
        )

        self.assertEqual(
            extract_item_meta(html),
             {"item_type": "Belt",
             "item_class": "Clothing",
             "slot": "Waist",
             "minimum_level": 13},
        )

    def test_min_level_ignores_non_numeric_value(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Minimum level</th><td>No minimum level</td>"
            "</tr></table>"
        )

        self.assertEqual(extract_item_meta(html), {})

    def test_no_infobox_returns_empty(self):
        from catalog.item_meta import extract_item_meta

        self.assertEqual(extract_item_meta("<p>nothing here</p>"), {})
        self.assertEqual(extract_item_meta(""), {})

    def test_only_first_table_scanned(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Weapon Type</th><td>Great Axe</td>"
            "</tr></table>"
            "<table><tr>"
            "<th>Type</th><td>Not the infobox</td>"
            "</tr></table>"
        )

        self.assertEqual(
            extract_item_meta(html)["item_type"],
            "Great Axe",
        )

    def test_rowspan_image_th_does_not_break_pairing(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Minimum level</th><td>None</td>"
            "<th rowspan='11'>File:Some image.jpg</th>"
            "</tr><tr>"
            "<th>Item Type</th><td>Clothing / Cloak</td>"
            "</tr></table>"
        )

        self.assertEqual(
            extract_item_meta(html),
             {"item_type": "Cloak",
             "item_class": "Clothing",
             "slot": "Back"},
        )

    def test_armor_type_label(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Armor Type</th><td>Breastplate</td>"
            "</tr><tr>"
            "<th>Minimum Level</th><td>None</td>"
            "</tr></table>"
        )

        self.assertEqual(
            extract_item_meta(html),
            {"armor_type": "Breastplate",
             "item_kind": "Armor",
             "item_class": "Armor",
             "slot": "Armor",
             "item_type": "Medium"},
        )

    def test_armor_feat_requirement_wins(self):
        from catalog.item_meta import extract_item_meta

        # Mithral Chainmail renders with Light Armor Proficiency
        # ("one type lighter"); the feat row is authoritative even
        # though chainmail is normally medium.
        html = (
            "<table><tr>"
            "<th>Armor Type</th><td>Chainmail</td>"
            "</tr><tr>"
            "<th>Feat Requirement</th><td>Light Armor "
            "Proficiency</td>"
            "</tr></table>"
        )

        self.assertEqual(
            extract_item_meta(html),
            {"armor_type": "Chainmail",
             "feat_requirement": "Light Armor Proficiency",
             "item_kind": "Armor",
             "item_class": "Armor",
             "slot": "Armor",
             "item_type": "Light"},
        )

    def test_armor_docent(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Armor Type</th><td>Docent</td>"
            "</tr><tr>"
            "<th>Feat Requirement</th><td>None</td>"
            "</tr></table>"
        )

        meta = extract_item_meta(html)
        self.assertEqual(meta["item_type"], "Docent")
        self.assertEqual(meta["armor_type"], "Docent")
        self.assertEqual(meta["feat_requirement"], "None")

    def test_armor_cloth_from_feat_none(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Armor Type</th><td>Robe</td>"
            "</tr><tr>"
            "<th>Feat Requirement</th><td>None</td>"
            "</tr></table>"
        )

        self.assertEqual(
            extract_item_meta(html)["item_type"],
            "Cloth",
        )

    def test_armor_generic_proficiency_unclassifiable(self):
        from catalog.item_meta import extract_item_meta

        # +1 Starter gear: generic "Armor Proficiency" feat, no
        # armor type, material "Unknown Material" -> no item_type.
        html = (
            "<table><tr>"
            "<th>Armor Type</th><td>\n</td>"
            "</tr><tr>"
            "<th>Feat Requirement</th><td>Armor Proficiency</td>"
            "</tr></table>"
        )

        meta = extract_item_meta(html)
        self.assertNotIn("item_type", meta)
        self.assertEqual(meta["item_kind"], "Armor")
        self.assertEqual(meta["feat_requirement"],
                         "Armor Proficiency")

    def test_cosmetic_armor(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Armor Type</th><td>Cosmetic Armor</td>"
            "</tr><tr>"
            "<th>Feat Requirement</th><td>None</td>"
            "</tr></table>"
        )

        meta = extract_item_meta(html)
        self.assertEqual(meta["item_type"], "Armor")
        self.assertEqual(meta["item_kind"], "Cosmetic")
        self.assertEqual(meta["armor_type"], "Cosmetic Armor")

    def test_cosmetic_shield(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Shield Type</th><td>Cosmetic Shield</td>"
            "</tr></table>"
        )

        meta = extract_item_meta(html)
        self.assertEqual(meta["item_type"], "Shield")
        self.assertEqual(meta["item_kind"], "Cosmetic")

    def test_cosmetic_weapon_grouped(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Weapon Type</th><td>Cosmetic Great Axe / Cosmetic "
            "weapons</td>"
            "</tr></table>"
        )

        meta = extract_item_meta(html)
        self.assertEqual(meta["item_type"], "Weapon")
        self.assertEqual(meta["item_kind"], "Cosmetic")
        self.assertEqual(meta["weapon_class"], "Cosmetic weapons")

    def test_cosmetic_clothing_subtype(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Item Type</th><td>Clothing / Cosmetic Helm</td>"
            "</tr></table>"
        )

        meta = extract_item_meta(html)
        self.assertEqual(meta["item_type"], "Helm")
        self.assertEqual(meta["item_kind"], "Cosmetic")

        html = (
            "<table><tr>"
            "<th>Item Type</th><td>Clothing / Cosmetic cloak</td>"
            "</tr></table>"
        )

        self.assertEqual(
            extract_item_meta(html)["item_type"],
            "Cloak",
        )

    def test_material_cleaned_of_tooltip_and_style(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Armor Type</th><td>Chainmail</td>"
            "</tr><tr>"
            "<th>Feat Requirement</th><td>Light Armor "
            "Proficiency</td>"
            "</tr><tr>"
            "<th>Material</th><td><style>.css{}</style>"
            "<span class='popup has&#95;tooltip'><a "
            "href='/page/Mithral'>Mithral</a><span class='popup "
            "tooltip'>Mithral: one type lighter</span></span>"
            "</td>"
            "</tr></table>"
        )

        meta = extract_item_meta(html)
        self.assertEqual(meta["material"], "Mithral")
        self.assertEqual(meta["item_type"], "Light")

    def test_weapon_no_slash_keeps_whole_type(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Weapon Type</th><td>Throwing Hammer</td>"
            "</tr></table>"
        )

        meta = extract_item_meta(html)
        self.assertEqual(meta["item_type"], "Throwing Hammer")
        self.assertNotIn("weapon_class", meta)
        self.assertEqual(meta["item_kind"], "Weapon")

    def test_weapon_proficiency_class_stored(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Weapon Type</th><td>Bastard Sword / Slashing "
            "weapons</td>"
            "</tr><tr>"
            "<th>Proficiency Class</th><td>Exotic Weapon "
            "Proficiency</td>"
            "</tr></table>"
        )

        meta = extract_item_meta(html)
        self.assertEqual(meta["weapon_class"], "Slashing weapons")
        self.assertEqual(meta["proficiency_class"],
                         "Exotic Weapon Proficiency")

    def test_classify_armor_direct(self):
        from catalog.item_meta import classify_armor

        self.assertEqual(
            classify_armor("Heavy Armor Proficiency", "Full Plate"),
            "Heavy",
        )
        self.assertEqual(
            classify_armor("None", "Docent"),
            "Docent",
        )
        self.assertEqual(
            classify_armor("None", "Cloth Armor"),
            "Cloth",
        )
        self.assertEqual(
            classify_armor("Armor Proficiency", "", ""),
            None,
        )
        self.assertEqual(
            classify_armor("Armor Proficiency", "Leather Armor"),
            "Light",
        )
        self.assertEqual(
            classify_armor("Armor Proficiency", "", "Cloth"),
            "Cloth",
        )
        self.assertEqual(
            classify_armor("", "Breastplate"),
            "Medium",
        )

    def test_derive_slot(self):
        from catalog.item_meta import derive_slot

        self.assertEqual(
            derive_slot("Light", "Armor"),
            "Armor",
        )
        self.assertEqual(
            derive_slot("", "Armor"),
            "Armor",
        )
        self.assertEqual(
            derive_slot("Great Sword", "Weapon"),
            "Main Hand",
        )
        self.assertEqual(
            derive_slot("Handwraps", "Weapon"),
            "Main Hand",
        )
        self.assertEqual(
            derive_slot("Buckler", "Shield"),
            "Off Hand",
        )
        self.assertEqual(
            derive_slot("Thin Quiver", "Quiver"),
            "Quiver",
        )
        self.assertEqual(
            derive_slot("Cloak", "Clothing"),
            "Back",
        )
        self.assertEqual(
            derive_slot("Boots", "Clothing"),
            "Feet",
        )
        self.assertEqual(
            derive_slot("Gloves", "Clothing"),
            "Hand",
        )
        self.assertEqual(
            derive_slot("Helm", "Clothing"),
            "Head",
        )
        self.assertEqual(
            derive_slot("Helmet", "Clothing"),
            "Head",
        )
        self.assertEqual(
            derive_slot("Belt", "Clothing"),
            "Waist",
        )
        self.assertEqual(
            derive_slot("Ring", "Jewelry"),
            "Finger",
        )
        self.assertEqual(
            derive_slot("Necklace", "Jewelry"),
            "Neck",
        )
        self.assertEqual(
            derive_slot("Goggles", "Jewelry"),
            "Eye",
        )
        self.assertEqual(
            derive_slot("Bracers", "Jewelry"),
            "Wrist",
        )
        # Wiki inconsistencies still land on the right slot.
        self.assertEqual(
            derive_slot("Bracers", "Clothing"),
            "Wrist",
        )
        self.assertEqual(
            derive_slot("Helm", "Cosmetic"),
            "Headwear",
        )
        self.assertEqual(
            derive_slot("Cloak", "Cosmetic"),
            "Cloak",
        )
        self.assertEqual(
            derive_slot("Shield", "Cosmetic"),
            "Off Hand",
        )
        self.assertEqual(
            derive_slot("Weapon", "Cosmetic"),
            "Main Hand",
        )
        self.assertEqual(derive_slot("", ""), "")
        self.assertEqual(
            derive_slot("Eternal Wand", ""),
            "",
        )

    def test_shield_type_label(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Shield Type</th><td>Large Shield</td>"
            "</tr><tr>"
            "<th>Minimum Level</th><td>6</td>"
            "</tr></table>"
        )

        self.assertEqual(
            extract_item_meta(html),
             {"item_type": "Large Shield",
             "item_class": "Shield",
             "slot": "Off Hand",
             "item_kind": "Shield",
             "minimum_level": 6},
        )

    def test_entities_and_links_cleaned(self):
        from catalog.item_meta import extract_item_meta

        html = (
            "<table><tr>"
            "<th>Item Type</th>"
            "<td><a href='/page/Jewelry'>Jewelry</a>&#160;/&#160;Trinket"
            "</td>"
            "</tr></table>"
        )

        self.assertEqual(
            extract_item_meta(html)["item_type"],
            "Trinket",
        )


class FetchItemPagesTests(TestCase):
    """The fetch_item_pages command writes item files per page."""

    ITEM_HTML = (
        "<table><tr>"
        "<th>Enchantments</th><td>"
        "<ul><li><a href='/page/Keen' title='Keen'>Keen</a></li></ul>"
        "</td></tr></table>"
    )

    def _parse_response(self, title, revid, page_id=100):
        return {
            "parse": {
                "title": title,
                "pageid": page_id,
                "revid": revid,
                "text": self.ITEM_HTML,
                "wikitext": (
                    "{{Named item|Weapon\n  | name = "
                    + title
                    + "\n  | type = Dagger\n}}"
                ),
                "categories": [
                    {"title": "Category:Named items"},
                ],
            }
        }

    def _query_response(self, titles):
        pages = []

        if isinstance(titles, str):
            titles = titles.split("|")

        for index, title in enumerate(titles):
            pages.append(
                {
                    "pageid": 100 + index,
                    "title": title,
                    "revisions": [
                        {
                            "revid": 200 + index,
                            "timestamp": (
                                f"2026-08-{index + 1:02d}T12:00:00Z"
                            ),
                        }
                    ],
                }
            )

        return {"query": {"pages": pages}}

    def _wikitext_check_response(self, titles, has_named_item=True):
        """Simulate a prop=revisions&rvprop=ids|content response.

        Returns wikitext containing {{Named item|...}} when
        has_named_item is True, or generic content when False.
        """

        pages = []

        for index, title in enumerate(titles.split("|")):
            wikitext = (
                "{{Named item|Foo}}\n'''Bar''' is a thing."
                if has_named_item
                else "This page has no infobox."
            )

            pages.append(
                {
                    "pageid": 100 + index,
                    "title": title,
                    "revisions": [
                        {
                            "revid": 200 + index,
                            "slots": {
                                "main": {"content": wikitext}
                            },
                        }
                    ],
                }
            )

        return {"query": {"pages": pages}}

    def _enumerate_response(self, pages, continuation=None):
        response = {
            "query": {
                "pages": [
                    {
                        "pageid": page_id,
                        "ns": 500,
                        "title": title,
                        "revisions": [{"revid": revid}],
                    }
                    for page_id, title, revid in pages
                ]
            }
        }

        if continuation:
            response["continue"] = continuation

        return response

    def _fetch(self, titles_list, api, out_dir):
        from catalog.management.commands.fetch_item_pages import (
            Command as FetchCommand,
        )

        command = FetchCommand()
        stdout = StringIO()
        stderr = StringIO()
        command.stdout = stdout
        command.stderr = stderr

        with patch.object(
            WikiAPI,
            "api_request",
            side_effect=api,
        ):
            command.handle(
                page=titles_list,
                from_db=False,
                titles=None,
                from_wiki=False,
                debug=None,
                limit=0,
                force=False,
                out=str(out_dir),
            )

        return command, stdout, stderr

    def test_writes_record_with_timestamps_and_categories(self):
        query_calls = []
        page_title_map = {}

        def api(params):
            if params["action"] == "parse":
                resp = self._parse_response(
                    params["page"],
                    revid=200,
                )
                page_title_map[resp["parse"]["pageid"]] = params["page"]
                return resp

            if params["action"] == "query":
                pageids = params["pageids"].split("|")
                titles = [page_title_map[int(pid)] for pid in pageids]
                query_calls.append(titles)
                return self._query_response(titles)

            raise AssertionError(
                f"unexpected action {params['action']}"
            )

        with TemporaryDirectory() as tmp:
            _, _, _ = self._fetch(
                ["Item:Test Dagger"],
                api,
                tmp,
            )

            record = json.loads(
                (Path(tmp) / "Item_Test Dagger.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(record["page_title"], "Item:Test Dagger")
        self.assertEqual(record["page_id"], 100)
        self.assertEqual(record["revision_id"], 200)
        self.assertEqual(
            record["revision_timestamp"],
            "2026-08-01T12:00:00Z",
        )
        self.assertEqual(
            record["categories"],
            ["Category:Named items"],
        )
        self.assertIn("html", record)
        self.assertNotIn("enchantments", record)
        self.assertIn("wikitext", record)
        self.assertIn("Named item", record["wikitext"])
        self.assertIn("api_url", record)
        self.assertIn("action=parse", record["api_url"])
        self.assertIn(
            "Item%3ATest+Dagger", record["api_url"]
        )
        self.assertEqual(
            query_calls,
            [["Item:Test Dagger"]],
        )

    def test_batches_revision_queries_by_fifty(self):
        from catalog.management.commands.fetch_item_pages import (
            API_REVISION_BATCH,
        )

        titles = [
            f"Item:Test Blade {index:02d}"
            for index in range(API_REVISION_BATCH + 3)
        ]

        query_calls = []
        page_title_map = {}
        next_page_id = [100]

        def api(params):
            if params["action"] == "parse":
                pid = next_page_id[0]
                next_page_id[0] += 1
                resp = self._parse_response(
                    params["page"],
                    revid=200,
                    page_id=pid,
                )
                page_title_map[pid] = params["page"]
                return resp

            if params["action"] == "query":
                pageids = params["pageids"].split("|")
                titles_batch = [page_title_map[int(pid)] for pid in pageids]
                query_calls.append(titles_batch)
                return self._query_response(titles_batch)

            raise AssertionError(
                f"unexpected action {params['action']}"
            )

        with TemporaryDirectory() as tmp:
            _, _, _ = self._fetch(
                titles,
                api,
                tmp,
            )

            written = sorted(
                Path(tmp).glob("Item_Test Blade *.json")
            )

            last_record = json.loads(
                written[-1].read_text(encoding="utf-8")
            )

        self.assertEqual(len(query_calls), 2)
        self.assertEqual(
            len(query_calls[0]),
            API_REVISION_BATCH,
        )
        self.assertEqual(
            len(query_calls[1]),
            3,
        )
        self.assertEqual(len(written), len(titles))

        self.assertIn(
            "revision_timestamp",
            last_record,
        )

    def test_query_failure_keeps_records_without_timestamp(self):
        def api(params):
            if params["action"] == "parse":
                return self._parse_response(
                    params["page"],
                    revid=200,
                )

            raise RuntimeError("WAF token expired")

        with TemporaryDirectory() as tmp:
            _, _, stderr = self._fetch(
                ["Item:Test Dagger"],
                api,
                tmp,
            )

            record = json.loads(
                (Path(tmp) / "Item_Test Dagger.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertNotIn(
            "revision_timestamp",
            record,
        )
        self.assertIn(
            "revision timestamps unavailable",
            stderr.getvalue(),
        )

    def test_from_wiki_enumerates_then_fetches(self):
        from catalog.management.commands.fetch_item_pages import (
            Command as FetchCommand,
        )

        page_title_map = {}

        def api(params):
            if params.get("generator") == "embeddedin":
                if params.get("geicontinue") == "Item:Beta":
                    resp = self._enumerate_response(
                        [(3, "Item:Gamma", 200)]
                    )
                else:
                    resp = self._enumerate_response(
                        [
                            (1, "Item:Alpha", 200),
                            (2, "Item:Beta", 200),
                        ],
                        continuation={
                            "geicontinue": "Item:Beta",
                            "continue": "-||",
                        },
                    )

                for page in resp["query"]["pages"]:
                    page_title_map[page["pageid"]] = page["title"]

                return resp

            if params["action"] == "parse":
                return self._parse_response(
                    params["page"],
                    revid=200,
                )

            if params["action"] == "query":
                pageids = params["pageids"].split("|")
                titles = [page_title_map[int(pid)] for pid in pageids]
                return self._query_response(titles)

            raise AssertionError(
                f"unexpected params {params}"
            )

        with TemporaryDirectory() as tmp:
            with TemporaryDirectory() as debug_tmp:
                command = FetchCommand()
                stdout = StringIO()
                stderr = StringIO()
                command.stdout = stdout
                command.stderr = stderr

                with patch.object(
                    WikiAPI,
                    "api_request",
                    side_effect=api,
                ):
                    command.handle(
                        page=None,
                        from_db=False,
                        titles=None,
                        from_wiki=True,
                        debug=str(debug_tmp),
                        limit=0,
                        force=False,
                        out=str(tmp),
                    )

                title_file = (
                    Path(debug_tmp) / "item_titles.txt"
                )

                self.assertEqual(
                    title_file.read_text(
                        encoding="utf-8"
                    ).splitlines(),
                    ["Item:Alpha", "Item:Beta", "Item:Gamma"],
                )

                report = json.loads(
                    (Path(debug_tmp) / "fetch_report.json")
                    .read_text(encoding="utf-8")
                )

                self.assertEqual(report["enumerated"], 3)
                self.assertEqual(report["fetched"], 3)
                self.assertEqual(report["failed"], {})
                self.assertEqual(report["missing"], [])
                self.assertEqual(
                    report["new"],
                    ["Item:Alpha", "Item:Beta", "Item:Gamma"],
                )
                self.assertEqual(report["changed"], [])
                self.assertEqual(
                    report["new_titles"],
                    ["Item:Alpha", "Item:Beta", "Item:Gamma"],
                )
                self.assertEqual(report["removed_titles"], [])

                written = sorted(
                    path.name
                    for path in Path(tmp).glob("*.json")
                )

                self.assertEqual(len(written), 3)

        self.assertIn(
            "Item_Alpha.json",
            written,
        )

        self.assertIn(
            "Enumerating Named item pages",
            stdout.getvalue(),
        )

    def test_from_wiki_limit_stops_fetch_but_writes_full_list(self):
        from catalog.management.commands.fetch_item_pages import (
            Command as FetchCommand,
        )

        page_title_map = {}

        def api(params):
            if params.get("generator") == "embeddedin":
                resp = self._enumerate_response(
                    [
                        (1, "Item:Alpha", 200),
                        (2, "Item:Beta", 200),
                        (3, "Item:Gamma", 200),
                    ]
                )
                for page in resp["query"]["pages"]:
                    page_title_map[page["pageid"]] = page["title"]
                return resp

            if params["action"] == "parse":
                return self._parse_response(
                    params["page"],
                    revid=200,
                )

            if params["action"] == "query":
                pageids = params["pageids"].split("|")
                titles = [page_title_map[int(pid)] for pid in pageids]
                return self._query_response(titles)

            raise AssertionError(
                f"unexpected params {params}"
            )

        with TemporaryDirectory() as tmp:
            with TemporaryDirectory() as debug_tmp:
                command = FetchCommand()
                stdout = StringIO()
                command.stdout = stdout

                with patch.object(
                    WikiAPI,
                    "api_request",
                    side_effect=api,
                ):
                    command.handle(
                        page=None,
                        from_db=False,
                        titles=None,
                        from_wiki=True,
                        debug=str(debug_tmp),
                        limit=2,
                        force=False,
                        out=str(tmp),
                    )

                title_file = (
                    Path(debug_tmp) / "item_titles.txt"
                )

                self.assertEqual(
                    title_file.read_text(
                        encoding="utf-8"
                    ).splitlines(),
                    ["Item:Alpha", "Item:Beta", "Item:Gamma"],
                )

                report = json.loads(
                    (Path(debug_tmp) / "fetch_report.json")
                    .read_text(encoding="utf-8")
                )

                self.assertEqual(report["enumerated"], 3)
                self.assertEqual(report["fetched"], 2)

                written = sorted(
                    path.name
                    for path in Path(tmp).glob("*.json")
                )

        self.assertEqual(len(written), 2)
        self.assertIn("Item_Alpha.json", written)
        self.assertNotIn("Item_Gamma.json", written)

    def test_debug_report_tracks_new_and_removed(self):
        from catalog.management.commands.fetch_item_pages import (
            Command as FetchCommand,
        )

        page_title_map = {}

        def api(params):
            if params.get("generator") == "embeddedin":
                resp = self._enumerate_response(
                    [
                        (1, "Item:Alpha", 200),
                        (2, "Item:Beta", 200),
                        (3, "Item:Gamma", 200),
                    ]
                )
                for page in resp["query"]["pages"]:
                    page_title_map[page["pageid"]] = page["title"]
                return resp

            if params["action"] == "parse":
                return self._parse_response(
                    params["page"],
                    revid=200,
                )

            if params["action"] == "query":
                pageids = params["pageids"].split("|")
                titles = [page_title_map[int(pid)] for pid in pageids]
                return self._query_response(titles)

            raise AssertionError(
                f"unexpected params {params}"
            )

        with TemporaryDirectory() as tmp:
            with TemporaryDirectory() as debug_tmp:
                titles_file = (
                    Path(debug_tmp) / "item_titles.txt"
                )

                titles_file.write_text(
                    "Item:Alpha\nItem:Old\n",
                    encoding="utf-8",
                )

                command = FetchCommand()
                stdout = StringIO()
                command.stdout = stdout

                with patch.object(
                    WikiAPI,
                    "api_request",
                    side_effect=api,
                ):
                    command.handle(
                        page=None,
                        from_db=False,
                        titles=None,
                        from_wiki=True,
                        debug=str(debug_tmp),
                        limit=0,
                        force=False,
                        out=str(tmp),
                    )

                report = json.loads(
                    (Path(debug_tmp) / "fetch_report.json")
                    .read_text(encoding="utf-8")
                )

        self.assertEqual(
            report["new_titles"],
            ["Item:Beta", "Item:Gamma"],
        )
        self.assertEqual(
            report["removed_titles"],
            ["Item:Old"],
        )

    def test_debug_report_captures_failed_and_missing(self):
        from catalog.management.commands.fetch_item_pages import (
            Command as FetchCommand,
        )

        page_title_map = {}

        def api(params):
            if params.get("generator") == "embeddedin":
                resp = self._enumerate_response(
                    [
                        (1, "Item:Alpha", 200),
                        (2, "Item:Beta", 200),
                        (3, "Item:Gamma", 200),
                    ]
                )
                for page in resp["query"]["pages"]:
                    page_title_map[page["pageid"]] = page["title"]
                return resp

            if params["action"] == "parse":
                if params["page"] == "Item:Beta":
                    raise RuntimeError("WAF token expired")

                if params["page"] == "Item:Gamma":
                    return {"parse": {"title": "Item:Gamma"}}

                return self._parse_response(
                    params["page"],
                    revid=200,
                )

            if params["action"] == "query":
                pageids = params["pageids"].split("|")
                titles = [page_title_map[int(pid)] for pid in pageids]
                return self._query_response(titles)

            raise AssertionError(
                f"unexpected params {params}"
            )

        with TemporaryDirectory() as tmp:
            with TemporaryDirectory() as debug_tmp:
                command = FetchCommand()
                stdout = StringIO()
                command.stdout = stdout

                with patch.object(
                    WikiAPI,
                    "api_request",
                    side_effect=api,
                ):
                    command.handle(
                        page=None,
                        from_db=False,
                        titles=None,
                        from_wiki=True,
                        debug=str(debug_tmp),
                        limit=0,
                        force=False,
                        out=str(tmp),
                    )

                report = json.loads(
                    (Path(debug_tmp) / "fetch_report.json")
                    .read_text(encoding="utf-8")
                )

        self.assertEqual(report["fetched"], 1)
        self.assertEqual(
            report["failed"],
            {"Item:Beta": "WAF token expired"},
        )
        self.assertEqual(
            report["missing"],
            [
                {
                    "title": "Item:Gamma",
                    "reason": "no page text",
                }
            ],
        )

    def test_from_wiki_compare_fetches_new_and_changed_skips_unchanged(
        self,
    ):
        from catalog.management.commands.fetch_item_pages import (
            Command as FetchCommand,
        )

        page_title_map = {}

        def api(params):
            if params.get("generator") == "embeddedin":
                resp = self._enumerate_response(
                    [
                        (1, "Item:Alpha", 250),
                        (2, "Item:Beta", 250),
                        (3, "Item:Gamma", 250),
                    ]
                )
                for page in resp["query"]["pages"]:
                    page_title_map[page["pageid"]] = page["title"]
                return resp

            if params["action"] == "parse":
                return self._parse_response(
                    params["page"],
                    revid=250,
                )

            if params["action"] == "query":
                pageids = params["pageids"].split("|")
                titles = [page_title_map[int(pid)] for pid in pageids]
                return self._query_response(titles)

            raise AssertionError(
                f"unexpected params {params}"
            )

        with TemporaryDirectory() as tmp:
            alpha_file = Path(tmp) / "Item_Alpha.json"
            beta_file = Path(tmp) / "Item_Beta.json"

            alpha_file.write_text(
                '{"revision_id": 200}',
                encoding="utf-8",
            )

            beta_file.write_text(
                '{"revision_id": 250}',
                encoding="utf-8",
            )

            with TemporaryDirectory() as debug_tmp:
                command = FetchCommand()
                stdout = StringIO()
                command.stdout = stdout

                with patch.object(
                    WikiAPI,
                    "api_request",
                    side_effect=api,
                ):
                    command.handle(
                        page=None,
                        from_db=False,
                        titles=None,
                        from_wiki=True,
                        debug=str(debug_tmp),
                        limit=0,
                        force=False,
                        out=str(tmp),
                    )

                report = json.loads(
                    (Path(debug_tmp) / "fetch_report.json")
                    .read_text(encoding="utf-8")
                )

                alpha = json.loads(
                    alpha_file.read_text(encoding="utf-8")
                )

                beta = json.loads(
                    beta_file.read_text(encoding="utf-8")
                )

        self.assertEqual(report["fetched"], 2)
        self.assertEqual(
            report["changed"],
            ["Item:Alpha"],
        )
        self.assertEqual(
            report["new"],
            ["Item:Gamma"],
        )
        self.assertEqual(
            report["skipped"],
            1,
        )

        self.assertEqual(alpha["revision_id"], 250)

        self.assertEqual(beta["revision_id"], 250)
        self.assertNotIn("page_title", beta)

    def test_from_wiki_compare_refetches_files_without_revision_metadata(
        self,
    ):
        from catalog.management.commands.fetch_item_pages import (
            Command as FetchCommand,
        )

        page_title_map = {}

        def api(params):
            if params.get("generator") == "embeddedin":
                resp = self._enumerate_response(
                    [
                        (1, "Item:Alpha", 250),
                    ]
                )
                for page in resp["query"]["pages"]:
                    page_title_map[page["pageid"]] = page["title"]
                return resp

            if params["action"] == "parse":
                return self._parse_response(
                    params["page"],
                    revid=250,
                )

            if params["action"] == "query":
                pageids = params["pageids"].split("|")
                titles = [page_title_map[int(pid)] for pid in pageids]
                return self._query_response(titles)

            raise AssertionError(
                f"unexpected params {params}"
            )

        with TemporaryDirectory() as tmp:
            alpha_file = Path(tmp) / "Item_Alpha.json"

            alpha_file.write_text(
                '{"page_title": "Item:Alpha", "html": "old"}',
                encoding="utf-8",
            )

            with TemporaryDirectory() as debug_tmp:
                command = FetchCommand()
                stdout = StringIO()
                command.stdout = stdout

                with patch.object(
                    WikiAPI,
                    "api_request",
                    side_effect=api,
                ):
                    command.handle(
                        page=None,
                        from_db=False,
                        titles=None,
                        from_wiki=True,
                        debug=str(debug_tmp),
                        limit=0,
                        force=False,
                        out=str(tmp),
                    )

                report = json.loads(
                    (Path(debug_tmp) / "fetch_report.json")
                    .read_text(encoding="utf-8")
                )

                alpha = json.loads(
                    alpha_file.read_text(encoding="utf-8")
                )

        self.assertEqual(
            report["changed"],
            ["Item:Alpha"],
        )

        self.assertEqual(alpha["revision_id"], 250)
        self.assertEqual(alpha["page_title"], "Item:Alpha")
