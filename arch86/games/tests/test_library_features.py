import no_probe  # noqa: F401  (must come first: disables real system probes)
import json
from pathlib import Path
import tempfile
import time
import unittest

from arch_games.catalog.library import Library
from arch_games.catalog.search import SearchIndex
from arch_games.config import AppError, Config
from arch_games.models import Game
from arch_games.recommend import recommend, set_interests
from arch_games import sync
from arch_games.storage.database import Database
from test_performance_history import launch


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(Path(self.temp.name) / "s")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)

    def add_game(self, gid, title, category="Other", installed=True, os_name="windows", description=""):
        g = Game(gid, title, gid.split(":")[0], category=category, os=os_name, description=description)
        self.db.put("installs", gid, {"directory": "d-" + gid.split(":")[1], "game": g.to_dict(), "archive_sha256": "a"})
        return g

    def library(self):
        profile = {"os_architecture": "x86_64", "os": "linux", "wine": {"available": True, "win64": True, "wow64": True}, "graphics": {"display": True}, "ram_total": 2**32}
        return Library(self.config, self.db, profile).load(offline=True)


class SearchTests(Base):
    def test_fields_and_ranking(self):
        self.add_game("local:1", "Undertale", "RPG")
        self.add_game("local:2", "Deltarune", "RPG")
        self.add_game("local:3", "Linux Racer", "Racing", os_name="linux")
        self.db.put("aliases", "ut", {"alias": "ut", "game_id": "local:1"})
        self.db.put("tags", "local:2", {"tags": ["Toby Fox"]})
        self.db.put("favorites", "local:3", {})
        index = SearchIndex(self.library(), self.db)
        def ids(**k):
            return [r["game"].id for r in index.search(source="local", **k)]

        self.assertEqual(ids(query="undertale"), ["local:1"])
        self.assertEqual(ids(query="ut"), ["local:1"])  # alias
        self.assertEqual(ids(query="toby fox"), ["local:2"])  # tag
        self.assertEqual(ids(platform="linux"), ["local:3"])
        self.assertEqual(sorted(ids(installed=True)), ["local:1", "local:2", "local:3"])
        self.assertEqual(ids(tag="Toby Fox"), ["local:2"])
        self.assertEqual(ids(favorite=True), ["local:3"])
        self.assertEqual(ids(installed=False), [])
        self.assertEqual(ids(query="rpg"), ["local:2", "local:1"])  # category text, ordered by title
        self.assertEqual(ids(query="undertale", platform="linux"), [])  # filters combine
        self.assertEqual(ids(query="zzz"), [])
        self.assertEqual(ids(query="undertale", compat="Requires configuration"), ["local:1"])  # installed, no launcher registered
        self.assertEqual(ids(query="undertale", compat="Verified working"), [])
        self.assertEqual([r["game"].id for r in index.search(query="un", source="local")][0], "local:1")  # prefix beats substring

    def test_responsive_with_large_library(self):
        games = [Game("gamevault:%d" % i, "Game Number %d %s" % (i, "alpha" if i % 7 == 0 else "beta"), "gamevault", category="Cat%d" % (i % 20)) for i in range(1, 20001)]

        class Lib:
            profile = {}
            reports = {}

        lib = Lib()
        lib.games = games
        t0 = time.perf_counter()
        index = SearchIndex(lib, self.db)
        build = time.perf_counter() - t0
        t0 = time.perf_counter()
        for _ in range(10):
            found = index.search("number alpha", limit=20)
        query = (time.perf_counter() - t0) / 10
        self.assertEqual(len(found), 20)
        self.assertLess(build, 3.0)
        self.assertLess(query, 0.35)  # per query over 20,000 entries, generous bound for slow VMs


class RecommendTests(Base):
    def test_explainable_and_separates_compatibility(self):
        self.add_game("local:1", "Sword Quest", "RPG", description="a fantasy adventure")
        self.add_game("local:2", "Dragon Tale", "RPG", description="fantasy")
        self.add_game("local:3", "Speed Run", "Racing")
        self.add_game("local:4", "Space Pirates", "Shooter", description="space combat")
        self.db.put("favorites", "local:1", {})
        launch(self.db, "a", "local:1", 0, 7200)
        set_interests(self.db, ["space"])
        lib = self.library()
        result = recommend(lib, self.db)
        titles = [r["title"] for r in result["recommendations"]]
        self.assertEqual(titles[0], "Dragon Tale")  # same category as the favorite you played a lot
        self.assertIn("Space Pirates", titles)  # interest match
        self.assertNotIn("Sword Quest", titles)  # already yours
        self.assertNotIn("Speed Run", titles)  # no signal at all
        top = result["recommendations"][0]
        self.assertTrue(any("favorited" in r or "played" in r for r in top["reasons"]))
        self.assertIn("compatibility", top)
        self.assertIn("separately", result["note"])
        self.assertIn("interests", result["basis"])

    def test_incompatible_games_are_never_recommended(self):
        self.add_game("local:1", "Fav", "RPG")
        self.add_game("local:2", "Blocked RPG", "RPG")
        self.db.put("favorites", "local:1", {})
        lib = self.library()
        lib.reports["local:2"]["classification"] = "Unsupported"
        self.assertEqual(recommend(lib, self.db)["recommendations"], [])

    def test_no_signals_no_recommendations(self):
        self.add_game("local:1", "A", "RPG")
        self.assertEqual(recommend(self.library(), self.db)["recommendations"], [])

    def test_interest_validation(self):
        with self.assertRaises(ValueError):
            set_interests(self.db, ["x" * 41])
        self.assertEqual(set_interests(self.db, [" RPG ", "rpg", ""]), ["rpg"])


class SyncTests(Base):
    def other(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        config = Config(Path(t.name) / "o")
        return config, Database(config.data_dir / "state.db")

    def test_roundtrip_union_and_last_writer_wins_without_paths_or_secrets(self):
        oc, odb = self.other()
        self.addCleanup(odb.close)
        self.add_game("local:1", "One")
        self.db.put("favorites", "local:1", {})
        self.db.put("notes", "local:1", {"text": "from A"})
        self.db.put("tags", "local:1", {"tags": ["rpg"]})
        self.db.put("aliases", "one", {"alias": "one", "game_id": "local:1"})
        self.db.put("custom_titles", "local:1", {"title": "Uno"})
        bundle = sync.export(self.db, self.config)
        text = json.dumps(bundle)
        self.assertNotIn(str(self.config.data_dir), text)
        self.assertNotIn("directory", text)
        self.assertEqual(bundle["items"]["installs"][0]["value"]["archive_sha256"], "a")
        odb.put("notes", "local:1", {"text": "newer on B"})  # written after A's note -> must win
        time.sleep(0.01)
        report = sync.import_bundle(odb, oc, json.loads(text))
        self.assertEqual(odb.get("notes", "local:1")["text"], "newer on B")
        self.assertEqual(report["kept_local"]["notes"], 1)
        self.assertEqual(odb.get("tags", "local:1"), {"tags": ["rpg"]})
        self.assertIsNotNone(odb.get("favorites", "local:1"))
        self.assertEqual(odb.get("custom_titles", "local:1")["title"], "Uno")
        again = sync.import_bundle(odb, oc, json.loads(text))
        self.assertEqual(again["merged"]["tags"], 0)  # idempotent
        self.assertEqual(sync.import_bundle(odb, oc, json.loads(text), prefer_remote=True)["merged"]["notes"], 1)

    def test_alias_conflict_never_repoints_silently_and_bad_input_skipped(self):
        oc, odb = self.other()
        self.addCleanup(odb.close)
        odb.put("aliases", "one", {"alias": "one", "game_id": "local:9"})
        bundle = {"schema": 1, "items": {"aliases": [{"key": "one", "value": {"alias": "one", "game_id": "local:1"}, "updated": "2999-01-01T00:00:00+00:00"},
                                                    {"key": "evil", "value": {"alias": "other", "game_id": "local:1"}},
                                                    {"key": "x", "value": {"alias": "x", "game_id": "../etc"}}],
                                         "favorites": [{"key": "../../x", "value": {}}, {"key": "local:2", "value": {}}],
                                         "notes": [{"key": "local:2", "value": {"text": "x" * 9000}}]}}
        report = sync.import_bundle(odb, oc, bundle)
        self.assertEqual(odb.get("aliases", "one")["game_id"], "local:9")
        self.assertEqual(report["skipped"], 3)
        self.assertEqual(len(odb.get("notes", "local:2")["text"]), 8000)  # clamped
        self.assertIsNone(odb.get("favorites", "../../x"))
        with self.assertRaises(AppError):
            sync.import_bundle(odb, oc, {"schema": 9})
