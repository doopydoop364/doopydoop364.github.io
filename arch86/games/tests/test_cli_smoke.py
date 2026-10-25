"""Every command parses, and the read-only commands run on an empty state without crashing."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import tempfile
import unittest

from arch_games.cli import main, make_parser


def leaves(parser, prefix=()):
    subs = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
    if not subs:
        yield prefix
        return
    for name, sub in subs.choices.items():
        yield from leaves(sub, prefix + (name,))


class SmokeTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_every_command_has_working_help(self):
        commands = [c for c in leaves(make_parser()) if c]
        self.assertGreater(len(commands), 80)
        for command in commands:
            code, out, _ = self.run_cli(*command, "--help")
            self.assertEqual(code, 0, command)
            self.assertIn("usage:", out, command)

    def test_read_only_commands_on_empty_state(self):
        with tempfile.TemporaryDirectory() as d:
            base = ["--state-dir", d, "--non-interactive"]
            for argv in [
                ["alias", "list"], ["launcher", "list"], ["history"], ["playtime"], ["favorite", "list"], ["tag", "list"],
                ["interests", "list"], ["machine", "show"], ["compat", "list"], ["compat", "export"], ["sync", "export"],
                ["prefix", "list"], ["download", "list"], ["download", "dedupe"], ["wine", "runtimes"], ["settings", "show"],
                ["audio", "devices"], ["recommend"], ["search", "zzz-nothing", "--json"], ["installed", "--offline"],
                ["info"], ["audio", "remote", "status"], ["audio", "remote", "instructions"],
            ]:
                code, out, err = self.run_cli(*base, *argv)
                self.assertEqual(code, 0, (argv, err))
            code, out, _ = self.run_cli(*base, "doctor", "--json")
            self.assertIn(code, (0, 1))
            json.loads(out)
            code, _, err = self.run_cli(*base, "wine", "doctor", "--json")
            self.assertEqual(code, 0, err)

    def test_unknown_game_errors_are_clean_messages_not_tracebacks(self):
        with tempfile.TemporaryDirectory() as d:
            for argv in (["launch", "ghost"], ["install", "ghost"], ["info", "ghost"], ["saves", "list", "ghost"], ["wine", "status", "ghost"], ["compat", "show", "ghost"], ["repair", "ghost"], ["performance", "ghost"]):
                code, out, err = self.run_cli("--state-dir", d, "--non-interactive", *argv)
                self.assertEqual(code, 1, argv)
                self.assertTrue(err.startswith("Error:"), (argv, err[:200]))
                self.assertNotIn("Traceback", err)
