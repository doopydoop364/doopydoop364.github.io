import unittest
from arch_games.ui.terminal import clean, fit, menu, render_sections, section_command


class TerminalTests(unittest.TestCase):
    def test_resizing_ascii(self):
        for width in [1, 10, 40, 80]:
            for height in [1, 5, 24]:
                lines = render_sections({"os_architecture": "i686"}, width, height, ascii_only=True)
                self.assertLessEqual(len(lines), height)
                self.assertTrue(all(len(line) <= width and line.isascii() for line in lines))
        self.assertEqual(fit("界abc", 3), "界a")

    def test_no_terminal_controls(self):
        self.assertNotIn("\x1b", clean("\x1b[31mDanger"))
        self.assertNotIn("\u202e", clean("name\u202eexe"))

    def test_fallback_navigation(self):
        class Library:
            profile = {}
            reports = {}

            def filtered(self, **kwargs):
                return [], "No entries"

        inputs = iter(["1", "/query", "n", "p", "b", "q"])
        lines = []
        menu(Library(), lambda args: None, True, lambda prompt: next(inputs), lines.append)
        self.assertTrue(any("Page 1" in line for line in lines))

    def test_section_actions(self):
        self.assertEqual(section_command(4, "r"), ["download", "run"])
        self.assertEqual(section_command(4, "cleanup abc"), ["download", "cancel", "abc", "--cleanup"])
        self.assertEqual(
            section_command(6, "create local:one win32"), ["prefix", "create", "local:one", "--arch", "win32"]
        )
        self.assertEqual(section_command(7, "connect"), ["gamevault", "connect"])
        self.assertEqual(section_command(6, "recover gamevault:32"), ["prefix", "recover", "gamevault:32"])
        self.assertEqual(section_command(7, "set show_all true"), ["settings", "set", "show_all", "true"])
        with self.assertRaises(ValueError):
            section_command(6, "create --help win64")
