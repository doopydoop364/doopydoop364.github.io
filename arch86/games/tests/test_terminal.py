import unittest
from arch_games.ui.terminal import clean, fit, menu, render_sections


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
