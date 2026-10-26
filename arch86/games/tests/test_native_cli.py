"""Mocked end-to-end OpenTTD workflow through the real CLI entry point.

Discover -> install (approved; the privileged step is mocked) -> detect -> find the executable -> register -> configure audio ->
dry-run -> launch -> record the result. No real package is installed and sudo is never called."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from arch_games.cli import main
from arch_games.storage.database import Database
from arch_games.config import Config
from native_fixtures import FakeSystem

SCRIPT_BODY = 'printf "%s\\n" "$SDL_AUDIODRIVER" "$AUDIODEV" "$@" > "{out}"\nexit 0\n'


class OpenTTDWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.system = FakeSystem(self.temp.name)
        self.state = Path(self.temp.name) / "state"
        self.out = Path(self.temp.name) / "game-ran.txt"
        self.installs = []
        for ctx in (self.system.active(), patch.dict(os.environ, {"DISPLAY": ":99"}),
                    patch("arch_games.compatibility.graphics.detect_graphics", return_value={"display": True, "accelerated": False, "renderer": "llvmpipe", "opengl": True, "vulkan": False}),
                    patch("arch_games.installation.native.run_pacman_install", side_effect=self.fake_install)):
            ctx.__enter__()
            self.addCleanup(ctx.__exit__, None, None, None)

    def fake_install(self, package):
        """Stands in for `sudo pacman -S`: the package appears in the fake database, as it would after a real install."""
        self.installs.append(package)
        self.system.openttd(script=True, body=SCRIPT_BODY.format(out=self.out).encode())
        return 0

    def cli(self, *argv, expect=0):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--state-dir", str(self.state), "--non-interactive", *argv])
        self.assertEqual(code, expect, (argv, err.getvalue()))
        return out.getvalue(), err.getvalue()

    def json(self, *argv, **kw):
        return json.loads(self.cli(*argv, **kw)[0])

    def db(self):
        return Database(Config(self.state).data_dir / "state.db")

    def test_discover_install_configure_dry_run_launch_record(self):
        exe = self.system.path("usr/bin/openttd")
        self.system.available.add("openttd")  # in the repositories, not installed yet
        # 1. discover it in the catalog; the title is the catalog title, not the id
        rows = self.json("search", "openttd", "--json")
        self.assertEqual([(r["id"], r["title"], r["installed"]) for r in rows if r["id"] == "pacman:openttd"], [("pacman:openttd", "OpenTTD", False)])
        info = self.json("info", "openttd")
        self.assertEqual((info["id"], info["title"], info["installed"], info["status"]["state"]), ("pacman:openttd", "OpenTTD", False, "available"))
        # 2. launching an uninstalled game explains what to do and installs nothing
        _, err = self.cli("launch", "openttd", expect=1)
        self.assertIn("is not installed", err)
        self.assertIn("arch-games install pacman:openttd", err)
        self.assertEqual(self.installs, [])
        # 3. installation needs explicit approval
        _, err = self.cli("install", "openttd", expect=1)
        self.assertIn("--yes", err)
        self.assertEqual(self.installs, [])
        # 4. approved: pacman (mocked) installs, Arch Games verifies against the package database, finds and registers the launcher
        report = self.json("install", "openttd", "--yes")
        self.assertEqual(self.installs, ["openttd"])
        self.assertEqual((report["status"], report["version"], report["launcher"]), ("ready", "15.3-1", exe))
        info = self.json("info", "openttd")
        self.assertEqual((info["installed"], info["status"]["state"], info["package"], info["launcher"]["title"], info["launcher"]["runner"]),
                         (True, "ready", {"name": "openttd", "version": "15.3-1"}, "OpenTTD", "native"))
        listing = self.cli("installed", "--offline")[0]
        self.assertIn("OpenTTD", listing)
        self.assertIn("installed", listing)
        found = self.json("discover", "openttd", "--json")
        self.assertEqual((found["runner"], found["installed"], found["version"], [c["path"] for c in found["candidates"]]), ("native", True, "15.3-1", [exe]))
        # 5. configure the audio workaround once (the user's tested configuration for the Arch64 VM)
        self.cli("launcher", "env", "set", "openttd", "SDL_AUDIODRIVER", "alsa")
        self.cli("launcher", "env", "set", "openttd", "AUDIODEV", "plughw:0,0")
        self.cli("launcher", "arg", "add", "openttd", "-s", "sdl")
        config = self.json("launcher", "config", "openttd")
        self.assertEqual((config["arguments"], config["environment"], config["runtime"]), (["-s", "sdl"], {"SDL_AUDIODRIVER": "alsa", "AUDIODEV": "plughw:0,0"}, "native Linux (no Wine)"))
        self.assertEqual(config["command_preview"], "env AUDIODEV=plughw:0,0 SDL_AUDIODRIVER=alsa {} -s sdl".format(exe))
        self.assertIn("ok", self.cli("launcher", "validate", "openttd")[0])
        # 6. dry run: shows everything, starts nothing
        plan = self.json("launch", "openttd", "--dry-run")
        self.assertEqual(plan["argv"], [exe, "-s", "sdl"])
        self.assertEqual(plan["custom_environment"], {"SDL_AUDIODRIVER": "alsa", "AUDIODEV": "plughw:0,0"})
        self.assertEqual((plan["runtime"], plan["profile"], plan["cwd"]), ("native Linux (no Wine)", "default", str(Path.home())))
        self.assertEqual(plan["command_preview"], "env AUDIODEV=plughw:0,0 SDL_AUDIODRIVER=alsa {} -s sdl".format(exe))
        self.assertFalse(self.out.exists())
        # 7. the real launch: the environment and arguments reach the program, no Wine is involved
        self.cli("launch", "openttd")
        self.assertEqual(self.out.read_text().splitlines(), ["alsa", "plughw:0,0", "-s", "sdl"])
        # 8. recorded: history, compatibility evidence (started != verified), then the user's confirmation
        db = self.db()
        try:
            launches = [r["value"] for r in db.all("launches") if r["value"]["game_id"] == "pacman:openttd"]
            self.assertEqual((len(launches), launches[0]["exit_code"], launches[0]["method"], launches[0]["profile"]), (1, 0, "native", "default"))
            self.assertIsNone(launches[0]["prefix"])
        finally:
            db.close()
        compat = self.json("compat", "show", "openttd")
        self.assertEqual((compat["record"]["launch_status"], compat["record"]["gameplay_status"]), ("exited_ok", "unverified"))
        self.assertNotEqual(compat["classification"], "Verified working")
        facts = compat["facts"]
        self.assertEqual((facts["native_package"]["package"], facts["native_package"]["version"], facts["native_package"]["launcher_status"], facts["native_package"]["executable_exists"]),
                         ("openttd", "15.3-1", "ready", True))
        self.assertEqual(facts["launch_configuration"]["environment"], {"SDL_AUDIODRIVER": "alsa", "AUDIODEV": "plughw:0,0"})
        self.assertEqual((facts["launch_configuration"]["arguments"], facts["launch_configuration"]["runner"]), (["-s", "sdl"], "native"))
        self.cli("compat", "set", "openttd", "--gameplay", "works", "--audio", "works")
        self.assertEqual(self.json("compat", "show", "openttd")["classification"], "Verified working")
        exported = self.json("compat", "export")["records"][0]["launch_profile"]
        self.assertEqual(exported["environment"], ["AUDIODEV", "SDL_AUDIODRIVER"])  # names only leave the machine

    def test_settings_are_stable_across_rediscovery_package_upgrade_and_removal(self):
        self.system.available.add("openttd")
        self.cli("install", "openttd", "--yes")
        self.cli("launcher", "env", "set", "openttd", "SDL_AUDIODRIVER", "alsa")
        self.cli("launcher", "arg", "add", "openttd", "-s", "sdl")
        self.cli("launcher", "profile", "create", "openttd", "silent")
        self.cli("launcher", "env", "set", "--profile", "silent", "openttd", "SDL_AUDIODRIVER", "dummy")
        # a package upgrade moves the executable
        self.system.remove_package("openttd")
        new = self.system.program("openttd", directory="usr/games", script=True, body=SCRIPT_BODY.format(out=self.out).encode())
        self.system.add_package("openttd", "15.4-1", [new])
        _, err = self.cli("launch", "openttd", expect=1)
        self.assertIn("rediscover", err)
        self.assertEqual(self.json("info", "openttd")["status"]["state"], "launcher-invalid")
        moved = self.json("launcher", "rediscover", "openttd")
        self.assertEqual((moved["executable"]["after"], moved["package_version"]), (new, "15.4-1"))
        self.assertEqual(moved["settings_kept"], {"arguments": 2, "environment": 1, "profiles": ["default", "silent"]})
        self.cli("launch", "openttd")
        self.assertEqual(self.out.read_text().splitlines()[:2], ["alsa", ""])
        self.cli("launch", "openttd", "--profile", "silent")
        self.assertEqual(self.out.read_text().splitlines()[0], "dummy")
        # the package is removed behind Arch Games' back
        self.system.remove_package("openttd")
        info = self.json("info", "openttd")
        self.assertEqual((info["installed"], info["status"]["state"]), (False, "package-removed"))
        _, err = self.cli("launch", "openttd", expect=1)
        self.assertIn("no longer installed", err)
        self.assertEqual(self.json("launcher", "config", "openttd")["arguments"], ["-s", "sdl"])  # nothing was deleted
        # reinstalling restores everything without re-registering from scratch
        self.cli("install", "openttd", "--yes")
        self.assertEqual(self.json("launcher", "config", "openttd")["arguments"], ["-s", "sdl"])

    def test_installed_before_arch_games_or_directly_through_pacman(self):
        self.system.openttd(script=True, body=SCRIPT_BODY.format(out=self.out).encode())  # as if `sudo pacman -S openttd` was run by hand
        info = self.json("info", "openttd")
        self.assertEqual((info["installed"], info["status"]["state"]), (True, "installed-no-launcher"))
        report = self.json("install", "openttd")  # nothing to approve: already installed; this just completes the launcher
        self.assertEqual((report["status"], self.installs), ("ready", []))
        self.assertEqual(self.json("info", "openttd")["status"]["state"], "ready")

    def test_launch_discovers_and_registers_an_unambiguous_launcher(self):
        self.system.openttd(script=True, body=SCRIPT_BODY.format(out=self.out).encode())
        plan = self.json("launch", "openttd", "--dry-run")  # dry run does not register anything
        self.assertEqual(plan["registered"], False)
        self.assertIn("Not registered yet", plan["note"])
        self.assertEqual(self.json("launcher", "list"), [])
        self.cli("launch", "openttd")
        self.assertEqual(self.out.read_text().splitlines()[2:], [])
        self.assertEqual(len(self.json("launcher", "list")), 1)

    def test_ambiguous_packages_ask_instead_of_guessing(self):
        a, b = self.system.program("snake"), self.system.program("snake-classic")
        self.system.add_package("snake", "1", [a, b])
        self.system.available.add("snake")
        _, err = self.cli("launch", "pacman:snake", expect=1)
        self.assertIn("not confirmed", err)
        report = self.json("install", "pacman:snake")
        self.assertEqual(report["status"], "launcher-needs-confirmation")
        self.assertIn("Installation succeeded; launcher configuration still required.", report["message"])
        self.assertEqual(self.json("launcher", "list"), [])
        self.cli("discover", "pacman:snake", "--register", "1")
        self.assertEqual(self.json("info", "pacman:snake")["status"]["state"], "ready")

    def test_installation_that_finds_no_program_is_still_an_installation(self):
        data = self.system.write("usr/share/artpack/readme.txt", b"art", mode=0o644)
        self.system.add_package("artpack", "1", [data])
        report = self.json("install", "pacman:artpack")
        self.assertEqual(report["status"], "launcher-required")
        self.assertIn("Installation succeeded", report["message"])
        self.assertEqual(self.installs, [])

    def test_pacman_failures_are_reported_honestly(self):
        self.system.available.add("openttd")
        with patch("arch_games.installation.native.run_pacman_install", return_value=1):
            _, err = self.cli("install", "openttd", "--yes", expect=1)
        self.assertIn("exited with status 1", err)
        with patch("arch_games.installation.native.run_pacman_install", return_value=0):  # claims success but nothing is installed
            _, err = self.cli("install", "openttd", "--yes", expect=1)
        self.assertIn("not installed", err)
        self.assertEqual(self.json("launcher", "list"), [])

    def test_no_sudo_outside_the_approved_install_step(self):
        self.system.openttd(script=True, body=SCRIPT_BODY.format(out=self.out).encode())
        for argv in (["discover", "openttd"], ["launch", "openttd", "--dry-run"], ["info", "openttd"], ["launcher", "validate", "openttd"], ["installed", "--offline"]):
            self.cli(*argv, expect=0 if argv[0] != "launcher" else 1)
        self.assertEqual(self.installs, [])
        self.assertTrue(all(call[0] == "pacman" for call in self.system.calls))

    def test_settings_commands_edit_view_and_reset(self):
        self.system.openttd(script=True, body=SCRIPT_BODY.format(out=self.out).encode())
        self.cli("discover", "openttd", "--register", "1")
        self.cli("launcher", "arg", "add", "openttd", "-s", "sdl", "x")
        self.assertEqual(self.json("launcher", "arg", "list", "openttd")["arguments"], ["-s", "sdl", "x"])
        self.assertEqual(self.json("launcher", "arg", "remove", "openttd", "3")["arguments"], ["-s", "sdl"])
        self.assertEqual(self.json("launcher", "arg", "remove", "openttd", "--", "-s")["arguments"], ["sdl"])  # dash-prefixed text needs `--`
        self.assertEqual(self.json("launcher", "arg", "set", "openttd", "-a", "-b")["arguments"], ["-a", "-b"])
        self.cli("launcher", "arg", "remove", "openttd", "nope", expect=1)
        self.cli("launcher", "arg", "add", "openttd", expect=1)
        self.assertEqual(self.json("launcher", "arg", "clear", "openttd")["arguments"], [])
        self.cli("launcher", "env", "set", "openttd", "A", "1")
        self.assertEqual(self.json("launcher", "env", "list", "openttd")["environment"], {"A": "1"})
        self.assertEqual(self.json("launcher", "env", "unset", "openttd", "A")["environment"], {})
        self.cli("launcher", "env", "unset", "openttd", "A", expect=1)
        self.assertIn("sensitive", self.cli("launcher", "env", "policy")[0])
        self.cli("launcher", "env", "set", "openttd", "A", "1")
        self.cli("launcher", "reset", "openttd", expect=1)  # asks first: non-interactive needs --yes
        self.assertEqual(self.json("launcher", "env", "list", "openttd")["environment"], {"A": "1"})
        self.cli("launcher", "reset", "openttd", "--yes")
        self.assertEqual(self.json("launcher", "env", "list", "openttd")["environment"], {})
        self.cli("launcher", "profile", "create", "openttd", "p2")
        self.assertEqual(self.json("launcher", "profile", "default", "openttd", "p2")["default"], "p2")
        self.cli("launcher", "profile", "delete", "openttd", "p2", expect=1)
        self.cli("launcher", "profile", "delete", "openttd", "p2", "--yes")
        self.assertEqual(self.json("launcher", "profile", "list", "openttd")["default"], "default")
        self.cli("launcher", "profile", "delete", "openttd", "default", "--yes", expect=1)

    def test_cli_refuses_sensitive_and_reserved_environment_and_never_evaluates_arguments(self):
        self.system.openttd(script=True, body=SCRIPT_BODY.format(out=self.out).encode())
        self.cli("discover", "openttd", "--register", "1")
        for name in ("LD_PRELOAD", "PATH", "HOME"):
            _, err = self.cli("launcher", "env", "set", "openttd", name, "/x", expect=1)
            self.assertIn("sensitive", err)
        self.cli("launcher", "env", "set", "--allow-sensitive", "openttd", "LD_LIBRARY_PATH", "/opt/x")
        _, err = self.cli("launcher", "env", "set", "--allow-sensitive", "openttd", "WINEPREFIX", "/x", expect=1)
        self.assertIn("managed by Arch Games", err)
        self.cli("launcher", "env", "set", "openttd", "bad-name", "x", expect=1)
        marker = Path(self.temp.name) / "pwned"
        self.cli("launcher", "arg", "add", "openttd", "$(touch {})".format(marker), ";touch {}".format(marker))
        self.cli("launch", "openttd", "--env", "SDL_AUDIODRIVER=alsa")
        self.assertFalse(marker.exists())
        self.assertEqual(self.out.read_text().splitlines()[2:], ["$(touch {})".format(marker), ";touch {}".format(marker)])
