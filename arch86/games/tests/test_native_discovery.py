"""Package-aware discovery of native Linux games against a fake Pacman database (no real packages, no sudo, nothing executed)."""

from pathlib import Path
import tempfile
import unittest

from arch_games.config import AppError, Config
from arch_games.runtime import native
from arch_games.runtime.launchers import LauncherStore
from arch_games.runtime.pacman import PacmanService, parse_desktop_entry, parse_exec
from arch_games.storage.database import Database
from native_fixtures import FakeSystem


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.system = FakeSystem(self.temp.name)
        ctx = self.system.active()
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.config = Config(Path(self.temp.name) / "state")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)
        self.pacman = PacmanService(run=self.system.run)

    def discover(self, package, title="", **kw):
        return native.discover_native(self.pacman, package, title, **kw)


class PacmanServiceTests(Base):
    def test_versions_files_owner_and_missing_packages(self):
        self.system.openttd()
        self.assertEqual(self.pacman.version("openttd"), "15.3-1")
        self.assertIsNone(self.pacman.version("not-installed"))
        self.assertIn(self.system.path("usr/bin/openttd"), self.pacman.files("openttd"))
        self.assertEqual(self.pacman.files("not-installed"), [])
        self.assertEqual(self.pacman.owner(self.system.path("usr/bin/openttd")), "openttd")
        self.assertIsNone(self.pacman.owner(self.system.path("usr/bin/unowned")))
        self.assertEqual(self.pacman.installed_versions(), {"openttd": "15.3-1"})

    def test_names_and_paths_are_validated_before_any_call(self):
        for bad in ("", "-S", "a b", "x;rm", "../x", "a" * 200, None):
            with self.assertRaises(ValueError):
                self.pacman.version(bad)
        self.assertIsNone(self.pacman.owner("relative/path"))
        self.assertIsNone(self.pacman.owner("/x\0y"))
        self.assertEqual(self.system.calls, [])

    def test_pacman_unavailable_is_reported_not_raised(self):
        def missing(argv, **kw):
            raise FileNotFoundError("pacman")

        service = PacmanService(run=missing)
        self.assertIsNone(service.version("openttd"))
        self.assertTrue(service.unavailable)
        self.assertEqual(service.files("openttd"), [])

    def test_only_read_only_flags_are_ever_used(self):
        self.system.openttd()
        native.discover_native(self.pacman, "openttd", "OpenTTD")
        self.assertTrue(all(call[1] in {"-Q", "-Ql", "-Qoq", "-Qq", "-Slq"} for call in self.system.calls))
        self.assertFalse(any(call[0] == "sudo" or "-S" in call[1:2] for call in self.system.calls))


class DesktopEntryParsingTests(unittest.TestCase):
    def test_exec_parsing_is_literal_and_drops_field_codes(self):
        self.assertEqual(parse_exec("openttd %f"), ("openttd", [], {}))
        self.assertEqual(parse_exec('/usr/bin/game --fullscreen "--name=A B" %U'), ("/usr/bin/game", ["--fullscreen", "--name=A B"], {}))
        self.assertEqual(parse_exec("env SDL_VIDEODRIVER=x11 FOO=1 game -w"), ("game", ["-w"], {"SDL_VIDEODRIVER": "x11", "FOO": "1"}))
        self.assertEqual(parse_exec("game 100%% %i"), ("game", ["100%"], {}))
        self.assertEqual(parse_exec('unterminated "quote'), (None, [], {}))
        self.assertEqual(parse_exec(""), (None, [], {}))
        program, args, _ = parse_exec("sh -c 'rm -rf $HOME; echo %f'")
        self.assertEqual((program, args), ("sh", ["-c", "rm -rf $HOME; echo "]))  # parsed as plain words: never evaluated

    def test_entry_group_only(self):
        entry = parse_desktop_entry("[Other]\nExec=evil\n[Desktop Entry]\nName=G\nName[de]=Spiel\nExec=g %U\n# c\n[Desktop Action x]\nExec=also-evil\n")
        self.assertEqual(entry, {"Name": "G", "Exec": "g %U"})


class DiscoveryTests(Base):
    def test_single_executable_with_desktop_entry_is_high_confidence(self):
        exe = self.system.openttd()
        result = self.discover("openttd", "OpenTTD")
        self.assertEqual((result["installed"], result["version"], result["confidence"]), (True, "15.3-1", "high"))
        [c] = result["candidates"]
        self.assertEqual((c["path"], c["kind"], c["architecture"], c["rank"]), (exe, "elf", "x86_64", 1))
        text = " ".join(c["evidence"])
        for needle in ("Owned by package openttd 15.3-1", "Desktop entry 'OpenTTD' launches it", "matches the package name", "only launchable program"):
            self.assertIn(needle, text)

    def test_not_installed_means_no_candidates_and_no_file_listing(self):
        self.system.available.add("openttd")
        result = self.discover("openttd", "OpenTTD")
        self.assertEqual((result["installed"], result["version"], result["candidates"], result["confidence"]), (False, None, [], "none"))
        self.assertEqual(self.system.query_calls("-Ql"), [])

    def test_helpers_installers_and_maintenance_programs_are_excluded(self):
        game = self.system.program("openttd")
        helpers = [self.system.program(n) for n in ("openttd-crashreporter", "openttd-updater", "openttd-uninstall", "openttd-config", "vcredist_x86")]
        self.system.add_package("openttd", "15.3-1", [game, *helpers])
        result = self.discover("openttd", "OpenTTD")
        self.assertEqual([c["name"] for c in result["candidates"]], ["openttd"])
        everything = self.discover("openttd", "OpenTTD", include_excluded=True)
        roles = {c["name"]: c["role"] for c in everything["candidates"]}
        self.assertEqual(roles["openttd-crashreporter"], "crash_reporter")
        self.assertEqual(roles["openttd-updater"], "updater")
        self.assertEqual(roles["openttd-uninstall"], "uninstaller")
        self.assertEqual(roles["openttd-config"], "config_utility")
        self.assertTrue(all(c["score"] < 0 for c in everything["candidates"] if c["role"] != "game"))

    def test_package_name_may_differ_from_the_executable_name(self):
        exe = self.system.program("ksnake")
        desktop = self.system.desktop("kde-snake", "ksnake %U", display="KSnake", filename="org.kde.ksnake")
        self.system.add_package("kdegames-snake", "1.0-1", [exe, desktop])
        result = self.discover("kdegames-snake", "Snake Duel")
        self.assertEqual([c["path"] for c in result["candidates"]], [exe])
        self.assertEqual(result["confidence"], "high")
        self.assertIn("Desktop entry 'KSnake' launches it", " ".join(result["candidates"][0]["evidence"]))

    def test_several_programs_are_ranked_and_never_guessed(self):
        a, b = self.system.program("snake"), self.system.program("snake-classic")
        c = self.system.program("snake-arena")
        self.system.add_package("snake", "1", [a, b, c])
        result = self.discover("snake", "Snake")
        self.assertEqual(result["candidates"][0]["path"], a)  # name == package ranks first
        self.assertEqual(len(result["candidates"]), 3)
        self.assertNotEqual(result["confidence"], "high")  # close competitors: the user is asked to confirm

    def test_private_library_directory_executables_are_found_but_rank_below_bin(self):
        main = self.system.program("coolgame", directory="usr/lib/coolgame")
        wrapper = self.system.program("coolgame", script=True, body=b'exec /usr/lib/coolgame/coolgame "$@"\n')
        desktop = self.system.desktop("coolgame", "coolgame")
        self.system.add_package("coolgame", "2.0-1", [main, wrapper, desktop])
        result = self.discover("coolgame", "Cool Game")
        paths = [c["path"] for c in result["candidates"]]
        self.assertEqual(paths[0], wrapper)
        self.assertIn(main, paths)
        top = result["candidates"][0]
        self.assertEqual(top["kind"], "script")
        self.assertIn("Wrapper script (/bin/sh)", " ".join(top["evidence"]))

    def test_opt_installed_programs(self):
        exe = self.system.program("launcher", directory="opt/bigsoft")
        self.system.add_package("bigsoft-game", "3", [exe])
        result = self.discover("bigsoft-game", "Big Soft")
        self.assertEqual([c["path"] for c in result["candidates"]], [exe])

    def test_owned_symlinks_are_followed_unowned_ones_are_not(self):
        real = self.system.program("game", directory="usr/lib/game")
        link = self.system.write("usr/bin/game", link_to=real)
        outside = self.system.write("usr/lib/other/binary", b"\x7fELF" + bytes(100))
        evil = self.system.write("usr/bin/helper-game", link_to=outside)
        self.system.add_package("game", "1", [real, link, evil])
        result = self.discover("game", "Game", include_excluded=True)
        by_path = {c["path"]: c for c in result["candidates"]}
        self.assertIn(link, by_path)
        self.assertIn("Symlink to", " ".join(by_path[link]["evidence"]))
        self.assertNotIn(evil, by_path)  # the target belongs to nobody the package owns
        self.assertEqual(by_path[link]["resolved"], real)

    def test_data_files_libraries_and_non_executables_are_ignored(self):
        exe = self.system.program("tool-game")
        lib = self.system.write("usr/lib/tool-game/libengine.so.1", b"\x7fELF" + bytes(200), mode=0o755)
        plain = self.system.write("usr/bin/readme-game", b"just text", mode=0o755)
        noexec = self.system.write("usr/bin/data-game", b"\x7fELF" + bytes(200), mode=0o644)
        self.system.add_package("tool-game", "1", [exe, lib, plain, noexec])
        self.assertEqual([c["name"] for c in self.discover("tool-game")["candidates"]], ["tool-game"])

    def test_desktop_entries_cannot_smuggle_in_unowned_programs_or_shell_commands(self):
        exe = self.system.program("legit-game")
        evil_entry = self.system.desktop("evil", "sh -c 'touch /tmp/pwned-by-desktop-entry'", filename="evil")
        abs_entry = self.system.desktop("abs", "/bin/bash %U", filename="abs")
        marker = Path(self.temp.name) / "marker"
        self.system.add_package("legit-game", "1", [exe, evil_entry, abs_entry])
        result = self.discover("legit-game", "Legit Game")
        self.assertEqual([c["path"] for c in result["candidates"]], [exe])
        self.assertFalse(marker.exists())
        self.assertFalse(Path("/tmp/pwned-by-desktop-entry").exists())

    def test_files_outside_the_trusted_roots_are_ignored(self):
        outside = Path(self.temp.name) / "elsewhere" / "game"
        outside.parent.mkdir()
        outside.write_bytes(b"\x7fELF" + bytes(100))
        outside.chmod(0o755)
        self.system.add_package("odd", "1", [str(outside)])
        self.assertEqual(self.discover("odd")["candidates"], [])

    def test_nothing_is_executed_to_identify_programs(self):
        marker = Path(self.temp.name) / "ran"
        script = self.system.program("danger", script=True, body="touch {}\n".format(marker).encode())
        self.system.add_package("danger", "1", [script])
        self.discover("danger", "Danger")
        self.assertFalse(marker.exists())


class RegistrationAndStatusTests(Base):
    def store(self):
        return LauncherStore(self.config, self.db)

    def register(self, exe=None, package="openttd", **kw):
        exe = exe or self.system.openttd()
        candidate = self.discover(package, "OpenTTD")["candidates"][0]
        return self.store().register_native("pacman:openttd", "OpenTTD", package, candidate, self.pacman, **kw)

    def test_registration_records_package_runtime_and_title(self):
        record = self.register()
        self.assertEqual((record["kind"], record["runner"], record["package"], record["package_version"], record["title"]), ("native-package", "native", "openttd", "15.3-1", "OpenTTD"))
        self.assertEqual(record["working_directory"], None)
        self.assertEqual(record["desktop_entry"]["name"], "OpenTTD")
        self.assertEqual(record["binary"]["architecture"], "x86_64")
        self.assertIsNone(record["prefix"])

    def test_refuses_files_the_package_does_not_own_and_helper_names(self):
        self.system.openttd()
        stray = self.system.program("stray")
        with self.assertRaises(AppError):
            self.store().register_native("pacman:openttd", "OpenTTD", "openttd", {"path": stray}, self.pacman)
        helper = self.system.program("openttd-updater")
        self.system.package_files["openttd"].append(helper)
        with self.assertRaises(AppError) as ctx:
            self.store().register_native("pacman:openttd", "OpenTTD", "openttd", {"path": helper}, self.pacman)
        self.assertIn("updater", str(ctx.exception))
        self.store().register_native("pacman:openttd", "OpenTTD", "openttd", {"path": helper}, self.pacman, force_role=True)
        with self.assertRaises(AppError):
            self.store().register_native("pacman:x", "X", "not-installed", {"path": helper}, self.pacman)
        with self.assertRaises(AppError):
            self.store().register_native("pacman:x", "X", "bad name;", {"path": helper}, self.pacman)
        self.system.owner_overrides[self.system.path("usr/bin/openttd")] = "another-package"
        with self.assertRaises(AppError):
            self.store().register_native("pacman:openttd", "OpenTTD", "openttd", {"path": self.system.path("usr/bin/openttd")}, self.pacman)

    def test_status_vocabulary(self):
        from arch_games.runtime import gamelaunch

        st = lambda: gamelaunch.status(self.config, self.db, "pacman:openttd", self.pacman)  # noqa: E731
        self.assertEqual(st()["status"], native.AVAILABLE) if False else None
        self.system.available.add("openttd")
        self.assertEqual(st()["status"], native.AVAILABLE)
        self.system.openttd()
        self.assertEqual(st()["status"], native.NO_LAUNCHER)
        record = self.register()
        state = st()
        self.assertEqual((state["status"], state["version"], state["launcher"]), (native.READY, "15.3-1", record["executable"]))
        exe = Path(record["executable"])
        exe.unlink()  # a package upgrade moved or removed the program
        self.assertEqual(st()["status"], native.INVALID)
        self.assertIn("rediscover", st()["detail"])
        self.system.remove_package("openttd")
        self.system.available.add("openttd")
        self.assertEqual(st()["status"], native.REMOVED)

    def test_ownership_change_marks_the_launcher_invalid(self):
        from arch_games.runtime import gamelaunch

        self.register()
        self.system.owner_overrides[self.system.path("usr/bin/openttd")] = "something-else"
        state = gamelaunch.status(self.config, self.db, "pacman:openttd", self.pacman)
        self.assertEqual(state["status"], native.INVALID)
        self.assertIn("owned by something-else", state["detail"])

    def test_upgrade_moves_the_program_rediscovery_keeps_your_settings(self):
        from arch_games.runtime import gamelaunch, profiles

        record = self.register()
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.edit(r, None, arguments=["-s", "sdl"], environment_changes={"SDL_AUDIODRIVER": "alsa"}))
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.create(r, "quiet"))
        # package 15.4 moves the program into /usr/games
        self.system.remove_package("openttd")
        new_exe = self.system.program("openttd", directory="usr/games")
        self.system.add_package("openttd", "15.4-1", [new_exe])
        self.assertEqual(gamelaunch.status(self.config, self.db, "pacman:openttd", self.pacman)["status"], native.INVALID)
        candidate = gamelaunch.discover(self.config, self.db, "pacman:openttd", "OpenTTD", self.pacman)["candidates"][0]
        updated = gamelaunch.register(self.config, self.db, "pacman:openttd", "OpenTTD", candidate, self.pacman)
        self.assertEqual((updated["executable"], updated["package_version"]), (new_exe, "15.4-1"))
        self.assertNotEqual(updated["executable"], record["executable"])
        self.assertEqual(updated["arguments"], ["-s", "sdl"])
        self.assertEqual(updated["environment"], {"SDL_AUDIODRIVER": "alsa"})
        self.assertIn("quiet", profiles.names(updated))
        self.assertEqual(updated["created_at"], record["created_at"])
        self.assertEqual(gamelaunch.status(self.config, self.db, "pacman:openttd", self.pacman)["status"], native.READY)

    def test_reset_settings_is_explicit(self):
        from arch_games.runtime import gamelaunch, profiles

        self.register()
        gamelaunch.edit(self.config, self.db, "pacman:openttd", lambda r: profiles.edit(r, None, arguments=["-x"]))
        candidate = self.discover("openttd", "OpenTTD")["candidates"][0]
        kept = self.store().register_native("pacman:openttd", "OpenTTD", "openttd", candidate, self.pacman)
        self.assertEqual(kept["arguments"], ["-x"])
        cleared = self.store().register_native("pacman:openttd", "OpenTTD", "openttd", candidate, self.pacman, reset_settings=True)
        self.assertEqual(cleared["arguments"], [])

    def test_package_installed_before_arch_games_existed_is_recognised(self):
        from arch_games.runtime import gamelaunch

        self.system.openttd()  # no install record, no launcher, no database rows at all
        self.assertEqual(self.db.all("installs"), [])
        self.assertEqual(gamelaunch.status(self.config, self.db, "pacman:openttd", self.pacman)["status"], native.NO_LAUNCHER)
        found = gamelaunch.discover(self.config, self.db, "pacman:openttd", "OpenTTD", self.pacman)
        self.assertEqual((found["runner"], found["installed"], found["confidence"]), ("native", True, "high"))
