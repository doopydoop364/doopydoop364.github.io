import unittest
from arch_games.catalog.filters import merge_catalogs, select
from arch_games.catalog.library import expand_versions
from arch_games.models import Game


class CatalogTests(unittest.TestCase):
    def test_no_title_conflation(self):
        linux = Game("p:1", "Same", os="linux")
        windows = Game("g:1", "Same", "gamevault", os="windows")
        self.assertEqual(len(merge_catalogs([linux], [windows, windows])), 2)

    def test_explicit_dedup_build_os(self):
        first = Game("p", "Title", os="linux", build="1", metadata={"canonical_id": "same"})
        second = Game("g", "Title", "gamevault", os="linux", build="1", metadata={"canonical_id": "same"})
        self.assertEqual(len(merge_catalogs([first], [second])), 1)
        second.build = "2"
        self.assertEqual(len(merge_catalogs([first], [second])), 2)

    def test_filters_and_explanation(self):
        games = [
            Game("p", "Pacman"),
            Game("g", "Unknown", "gamevault"),
            Game("v", "Verified", "gamevault", installed=True),
        ]
        reports = {"v": {"status": "Verified Working", "runtime": "Wine"}}
        result, explanation = select(games, reports)
        self.assertEqual({g.id for g in result}, {"p", "v"})
        self.assertIn("1 entries hidden", explanation)
        result, _ = select(games, reports, installed=True, verified=True, wine=True)
        self.assertEqual([g.id for g in result], ["v"])
        self.assertEqual(len(select(games, reports, show_all=True)[0]), 3)

    def test_versions_remain_distinct(self):
        game = Game(
            "gamevault:1",
            "Game",
            "gamevault",
            metadata={
                "remote": {
                    "versions": [
                        {"id": 2, "type": "WINDOWS_PORTABLE", "version": "1"},
                        {"id": 3, "type": "LINUX_PORTABLE", "version": "1"},
                    ]
                }
            },
        )
        result = expand_versions(game)
        self.assertEqual([g.id for g in result], ["gamevault:1@2", "gamevault:1@3"])
        self.assertEqual([g.os for g in result], ["windows", "linux"])

    def test_installed_alternative_keeps_correct_source(self):
        first = Game("p", "Title", package="pkg", os="linux", build="1", metadata={"canonical_id": "same"})
        second = Game(
            "g",
            "Title",
            "gamevault",
            os="linux",
            build="1",
            installed=True,
            metadata={"canonical_id": "same"},
        )
        result = merge_catalogs([first], [second])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source, "gamevault")
        self.assertFalse(result[0].metadata["alternatives"][0]["installed"])
        self.assertNotIn("alternatives", first.metadata)

    def test_source_specific_catalog_does_not_collapse_other_sources(self):
        first = Game("p", "Title", os="linux", build="1", metadata={"canonical_id": "same"})
        second = Game("g", "Title", "gamevault", os="linux", build="1", metadata={"canonical_id": "same"})
        self.assertEqual(len(merge_catalogs([first], [second], deduplicate=False)), 2)
