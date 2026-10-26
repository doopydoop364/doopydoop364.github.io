"""The real 2.0 state: the Undertale prefix is keyed by the bare ID (gamevault:32) while a GameVault library cache
expands the game into versioned IDs (gamevault:32@34). Names must still reach the existing prefix and nothing is duplicated."""

import no_probe  # noqa: F401  (must come first: disables real system probes)
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arch_games.cli import main
from arch_games.config import Config
from arch_games.gamevault.client import Client
from arch_games.storage.database import Database
from test_discovery_launch import GAME, build_undertale  # GAME == "gamevault:32"


class V2Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.config = Config(self.state)
        self.config.values.update(server_url="https://fixture.invalid", account="u")
        self.config.save()
        db = Database(self.config.data_dir / "state.db")
        client = Client(self.config, db)
        db.put("library", client.cache_key, {"synced_at": "t", "games": [{
            "id": 32, "title": "Undertale", "type": "WINDOWS_SETUP", "metadata": {"title": "Undertale", "genres": [{"name": "RPG"}]},
            "versions": [{"id": 34, "type": "WINDOWS_SETUP", "size": "100", "file_path": "Undertale.zip", "version": "GOG 1.08"}]}]})
        self.prefix, self.game_dir = build_undertale(self.config, db)  # prefix keyed gamevault:32, no alias, no launcher
        db.close()
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        self.log = Path(self.temp.name) / "wine.log"
        wine = self.bin / "wine"
        wine.write_text("#!/bin/sh\nprintf '%s\\n' \"$WINEPREFIX\" \"$@\" >> '{}'\n".format(self.log))
        wine.chmod(0o755)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        env = {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"], "DISPLAY": ":99"}
        with patch.dict(os.environ, env), redirect_stdout(out), redirect_stderr(err):
            code = main(["--state-dir", str(self.state), "--non-interactive", *argv])
        return code, out.getvalue(), err.getvalue()


class V2StateTests(V2Base):
    def test_discover_and_launch_by_title_reach_the_bare_id_prefix(self):
        code, out, err = self.cli("discover", "undertale", "--json")
        self.assertEqual(code, 0, err)
        found = json.loads(out)["candidates"]
        self.assertEqual([c["name"] for c in found], ["UNDERTALE.exe"])
        self.assertEqual(self.cli("discover", "undertale", "--register", "1")[0], 0)
        code, out, err = self.cli("launcher", "show", "undertale")
        self.assertEqual(json.loads(out)["game_id"], GAME)  # recorded under the id the prefix already uses
        code, out, err = self.cli("launch", "undertale", "--dry-run")
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual(plan["prefix"], GAME)
        self.assertTrue(plan["executable"].endswith("UNDERTALE.exe"))
        code, out, err = self.cli("launch", "undertale")
        self.assertEqual(code, 0, err)
        lines = self.log.read_text().splitlines()  # (the first entries are Wine's --version probe, which has no prefix)
        self.assertIn(str(self.prefix), lines)
        self.assertTrue(any(line.endswith("UNDERTALE.exe") for line in lines))
        self.assertEqual(json.loads(self.cli("prefix", "check", "undertale")[1])["path"], str(self.prefix))
        for argv in (["wine", "status", "undertale"], ["info", "undertale"], ["settings", "game", "undertale"], ["compat", "show", "undertale"], ["saves", "discover", "undertale"]):
            self.assertEqual(self.cli(*argv)[0], 0, argv)

    def test_title_and_bare_id_name_one_game_not_two(self):
        code, out, _ = self.cli("search", "undertale", "--json")
        rows = json.loads(out)
        self.assertEqual(len(rows), 1, rows)
        self.assertTrue(rows[0]["installed"])  # the prefix under the bare ID counts for the versioned library entry
        self.assertEqual(self.cli("info", "gamevault:32")[0], 0)
        self.assertEqual(self.cli("info", "gamevault:32@34")[0], 0)

    def test_install_does_not_create_a_second_prefix_or_redownload(self):
        self.cli("discover", "undertale", "--register", "1")
        code, out, err = self.cli("install", "undertale", "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "already_installed")
        db = Database(self.config.data_dir / "state.db")
        try:
            self.assertEqual(sorted(r["key"] for r in db.all("prefixes")), [GAME])
            self.assertEqual(db.all("downloads"), [])
            self.assertEqual(db.all("install_jobs"), [])
        finally:
            db.close()

    def test_install_of_unregistered_but_installed_game_points_to_discovery(self):
        code, out, err = self.cli("install", "undertale", "--yes")
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual(report["status"], "installed_unregistered")
        self.assertIn("discover", " ".join(report["next"]))
        db = Database(self.config.data_dir / "state.db")
        try:
            self.assertEqual(sorted(r["key"] for r in db.all("prefixes")), [GAME])
            self.assertEqual(db.all("downloads"), [])
        finally:
            db.close()


class IdHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Config(Path(self.temp.name) / "s").data_dir / "state.db")
        self.addCleanup(self.db.close)

    def test_local_id_directions(self):
        from arch_games.core.ids import base, family_get, local_id

        self.assertEqual(base("gamevault:32@34"), "gamevault:32")
        self.assertEqual(local_id(self.db, "gamevault:32@34"), "gamevault:32@34")  # no state: unchanged
        self.db.put("prefixes", "gamevault:32", {"directory": "d"})
        self.assertEqual(local_id(self.db, "gamevault:32@34"), "gamevault:32")  # versioned -> bare
        self.assertEqual(local_id(self.db, "gamevault:32"), "gamevault:32")
        self.db.put("launchers", "gamevault:50@7", {"executable": "/x"})
        self.assertEqual(local_id(self.db, "gamevault:50"), "gamevault:50@7")  # bare -> its only versioned sibling
        self.db.put("launchers", "gamevault:50@8", {"executable": "/y"})
        self.assertEqual(local_id(self.db, "gamevault:50"), "gamevault:50")  # two siblings: never guess
        self.db.put("prefixes", "gamevault:60@1", {})
        self.db.put("notes", "gamevault:60", {"text": "n"})
        self.assertEqual(family_get(self.db, "notes", "gamevault:60@1"), {"text": "n"})
        self.assertEqual(local_id(self.db, "gamevault:60@2"), "gamevault:60@2")  # a different version is not silently merged

    def test_web_launch_and_favorite_follow_the_state_id(self):
        import threading
        from arch_games.ui.web.server import WebApp
        import http.client

        config = Config(Path(self.temp.name) / "w")
        db = Database(config.data_dir / "state.db")
        prefix, game = build_undertale(config, db)
        from arch_games.runtime.launchers import LauncherStore

        LauncherStore(config, db).register(GAME, "Undertale", str(game / "UNDERTALE.exe"))
        db.close()
        app = WebApp(config, port=0)
        port = app.start()
        threading.Thread(target=app.serve_forever, daemon=True).start()
        self.addCleanup(app.stop)

        def call(method, path, body=None, cookie=None, csrf=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            headers = {"Host": "127.0.0.1:%d" % port, "Content-Type": "application/json"}
            if cookie:
                headers["Cookie"] = cookie
            if csrf:
                headers["X-CSRF-Token"] = csrf
            conn.request(method, path, json.dumps(body) if body is not None else None, headers)
            r = conn.getresponse()
            data = json.loads(r.read() or b"{}")
            return r.status, data, r

        status, data, r = call("POST", "/api/login", {"token": app.token})
        cookie, csrf = r.getheader("Set-Cookie").split(";")[0], data["csrf"]
        status, data, _ = call("POST", "/api/favorite/gamevault:32@34", {"value": True}, cookie, csrf)
        self.assertEqual((status, data["id"]), (200, GAME))  # recorded where the rest of the state lives
        status, data, _ = call("GET", "/api/game/gamevault:32@34", cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["favorite"])
        self.assertTrue(data["launcher"]["exists"])


class LibraryListingTests(V2Base):
    def test_registered_launcher_and_install_under_the_bare_id_do_not_duplicate_the_versioned_entry(self):
        from arch_games.catalog.library import Library
        from arch_games.runtime.launchers import LauncherStore

        db = Database(self.config.data_dir / "state.db")
        try:
            LauncherStore(self.config, db).register(GAME, "Undertale", str(self.game_dir / "UNDERTALE.exe"))
            db.put("installs", GAME, {"directory": "d", "game": {"id": GAME, "title": "Undertale", "source": "gamevault"}, "archive_sha256": "a"})
            lib = Library(self.config, db, {"os_architecture": "x86_64", "os": "linux", "wine": {"available": True, "win64": True}, "graphics": {"display": True}, "ram_total": 2**32}).load(offline=True)
            ids = [g.id for g in lib.games if "gamevault" in g.id]
            self.assertEqual(ids, ["gamevault:32@34"])
            game = lib.games[[g.id for g in lib.games].index("gamevault:32@34")]
            self.assertTrue(game.installed)  # the bare-ID install/launcher state is visible on the versioned entry
            self.assertEqual(lib.reports[game.id]["facts"]["game_executable"]["architecture"], "i686")
        finally:
            db.close()


class LauncherTitleTests(V2Base):
    def test_registering_stores_the_resolved_human_title_not_the_id(self):
        self.assertEqual(self.cli("discover", "undertale", "--register", "1")[0], 0)
        code, out, _ = self.cli("launcher", "show", "undertale")
        self.assertEqual(json.loads(out)["title"], "Undertale")
        # registering by ID (no title typed) still resolves the catalog title
        self.cli("launcher", "remove", "undertale")
        self.assertEqual(self.cli("discover", "gamevault:32", "--register", "1")[0], 0)
        self.assertEqual(json.loads(self.cli("launcher", "show", "gamevault:32")[1])["title"], "Undertale")

    def test_custom_titles_are_respected_and_reregistering_never_downgrades_a_title(self):
        self.cli("title", "set", "undertale", "UT (my copy)")
        self.cli("discover", "undertale", "--register", "1")
        self.assertEqual(json.loads(self.cli("launcher", "show", "undertale")[1])["title"], "UT (my copy)")
        db = Database(self.config.data_dir / "state.db")
        try:
            from arch_games.runtime.launchers import LauncherStore

            store = LauncherStore(self.config, db)
            exe = json.loads(self.cli("launcher", "show", "undertale")[1])["executable"]
            store.register(GAME, GAME, exe)  # a caller that only knows the ID
            self.assertEqual(store.get(GAME)["title"], "UT (my copy)")
            # an old record whose title is the bare ID is simply updated when a real title is known
            record = dict(store.get(GAME), title=GAME)
            db.put("launchers", GAME, record)
            store.register(GAME, "Undertale", exe)
            self.assertEqual(store.get(GAME)["title"], "Undertale")
        finally:
            db.close()
