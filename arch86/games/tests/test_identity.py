import tempfile
import unittest
from arch_games.config import AppError, Config
from arch_games.identity.resolver import (
    AmbiguousGameError, Resolver, UnknownGameError, add_alias, collect_candidates, normalize, remove_alias,
)
from arch_games.models import Game
from arch_games.storage.database import Database


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(self.temp.name)
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)
        self.games = [
            Game("gamevault:32", "Undertale", "gamevault"),
            Game("gamevault:40", "Baba Is You", "gamevault"),
            Game("gamevault:50", "Portal 2", "gamevault"),
            Game("gamevault:51", "Portal Reloaded", "gamevault"),
            Game("pacman:supertux", "SuperTux", "pacman", package="supertux"),
            Game("gamevault:60", "Doom", "gamevault"),
            Game("gamevault:61", "DOOM", "gamevault"),
        ]

    def resolver(self):
        return Resolver(collect_candidates(self.games, self.db), self.db)

    def test_match_order(self):
        r = self.resolver()
        self.assertEqual(r.resolve("gamevault:32"), (r.by_id["gamevault:32"], "id"))
        self.assertEqual(r.resolve("Undertale")[1], "title")
        self.assertEqual(r.resolve("undertale")[1], "title-casefold")
        self.assertEqual(r.resolve("  UNDERTALE ")[0]["id"], "gamevault:32")
        self.assertEqual(r.resolve("baba is you.")[1], "title-normalized")
        self.assertEqual(r.resolve("supertux")[1], "package")

    def test_alias_beats_title_and_survives_reopen(self):
        add_alias(self.db, self.resolver(), "Ut", "gamevault:32")
        self.assertEqual(self.resolver().resolve("ut"), (self.resolver().by_id["gamevault:32"], "alias"))
        path = self.config.data_dir / "state.db"
        self.db.close()
        db = Database(path)
        self.addCleanup(db.close)
        self.assertEqual(Resolver(collect_candidates(self.games, db), db).resolve("UT")[0]["id"], "gamevault:32")
        self.db = db

    def test_alias_collisions(self):
        r = self.resolver()
        add_alias(self.db, r, "x", "gamevault:32")
        with self.assertRaises(AppError):
            add_alias(self.db, self.resolver(), "X", "gamevault:40")
        add_alias(self.db, self.resolver(), "X", "gamevault:40", force=True)
        with self.assertRaises(AppError):
            add_alias(self.db, self.resolver(), "gamevault:32", "gamevault:40")
        with self.assertRaises(AppError):
            add_alias(self.db, self.resolver(), "nothing", "gamevault:999")
        with self.assertRaises(AppError):
            add_alias(self.db, self.resolver(), "!!!", "gamevault:32")
        remove_alias(self.db, "x")
        with self.assertRaises(AppError):
            remove_alias(self.db, "x")

    def test_duplicate_titles_are_ambiguous(self):
        with self.assertRaises(AmbiguousGameError) as ctx:
            self.resolver().resolve("doom")
        self.assertEqual({c["id"] for c in ctx.exception.candidates}, {"gamevault:60", "gamevault:61"})

    def test_fuzzy_unique_and_never_guesses(self):
        r = self.resolver()
        self.assertEqual(r.resolve("undertle")[0]["id"], "gamevault:32")
        self.assertEqual(r.resolve("undertle")[1], "fuzzy")
        with self.assertRaises(AmbiguousGameError):
            r.resolve("portal")
        with self.assertRaises(UnknownGameError) as ctx:
            r.resolve("zzzzzz")
        self.assertEqual(ctx.exception.suggestions, [])

    def test_contained_word_unique(self):
        self.games.append(Game("gamevault:70", "Hollow Knight GOG 1.5", "gamevault"))
        r = self.resolver()
        self.assertEqual(r.resolve("hollow knight")[0]["id"], "gamevault:70")

    def test_invalid_inputs(self):
        for bad in ["", "   ", "a" * 300, "x\0y"]:
            with self.subTest(bad=bad), self.assertRaises(AppError):
                self.resolver().resolve(bad)

    def test_local_records_are_candidates(self):
        self.db.put("prefixes", "gamevault:99", {"directory": "d"})
        self.db.put("launchers", "gamevault:98", {"title": "Hidden Game"})
        r = self.resolver()
        self.assertEqual(r.resolve("gamevault:99")[1], "id")
        self.assertEqual(r.resolve("hidden game")[0]["id"], "gamevault:98")

    def test_normalize(self):
        self.assertEqual(normalize("Café  Pokémon: Red & Blue!"), "cafe pokemon red and blue")
