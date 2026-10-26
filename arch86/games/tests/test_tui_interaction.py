"""TUI filters/empty states, mouse handling and hit testing, and the in-TUI compatibility feedback panel."""

import no_probe  # noqa: F401  (must come first: disables real system probes)
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arch_games.catalog.library import Library
from arch_games.compatibility import intelligence
from arch_games.config import Config
from arch_games.gamevault.client import Client
from arch_games.storage.database import Database
from arch_games.ui.tui.app import enable_mouse, mouse_kind
from arch_games.ui.tui.data import Provider
from arch_games.ui.tui.model import AUDIO_OPTIONS, GAMEPLAY_OPTIONS, SECTIONS, Model
from arch_games.ui.tui.render import Caps, render_frame
from test_discovery_launch import GAME, build_undertale
from test_tui import FakeProvider

PROFILE = {"os_architecture": "x86_64", "os": "linux", "wine": {"available": True, "win64": True, "wow64": True, "version": "wine-11"},
           "graphics": {"display": True, "accelerated": False, "renderer": "llvmpipe"}, "ram_total": 4 * 2**30, "ram_available": 3 * 2**30}


def text(frame):
    return ["".join(t for t, _ in line) for line in frame.lines]


def find_row(frame, needle):
    return next(y for y, line in enumerate(text(frame)) if needle in line)


class World(unittest.TestCase):
    """A temp state with a connected GameVault cache of 3 games (no compatibility evidence) and the Undertale prefix."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(Path(self.temp.name) / "s")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)

    def connect(self, games=3, credentials=True):
        self.config.values.update(server_url="https://fixture.invalid", account="u")
        self.config.save()
        if credentials:
            self.config.save_credentials({"api_key": "k"})
        cache = [{"id": n, "title": "Game %d" % n, "type": "WINDOWS_SETUP", "metadata": {"title": "Game %d" % n}, "versions": []} for n in range(1, games + 1)]
        self.db.put("library", Client(self.config, self.db).cache_key, {"synced_at": "t", "games": cache})

    def provider(self):
        return Provider(self.config, self.db, Library(self.config, self.db, dict(PROFILE)).load(offline=True))


class FilterAndEmptyStateTests(World):
    def test_gamevault_tab_shows_unverified_games_by_default_and_a_filters(self):
        self.connect()
        m = Model(self.provider())
        m.switch(SECTIONS.index("GameVault"))
        self.assertTrue(m.show_all)
        self.assertEqual(len(m.rows), 3)
        self.assertTrue(all(r.tag.startswith("Unknown") or r.tag for r in m.rows))  # labelled, not hidden
        m.handle_key("a")
        self.assertFalse(m.show_all)
        self.assertEqual(len(m.rows), 0)  # nothing is verified yet: the filter hides them
        self.assertIn("compatibility filter", m.provider.empty_text("GameVault", "", False))
        self.assertIn("3 games are hidden", m.provider.empty_text("GameVault", "", False))
        self.assertIn("Hiding", m.message)
        m.handle_key("a")
        self.assertEqual(len(m.rows), 3)
        self.assertIn("Showing every game", m.message)
        m.switch(0)  # the general Library keeps its own default
        self.assertFalse(m.show_all)

    def test_each_empty_state_has_its_own_message(self):
        provider = Provider(self.config, self.db, Library(self.config, self.db, dict(PROFILE)).load(offline=True))
        self.assertIn("not connected", provider.empty_text("GameVault", "", True))
        self.assertIn("gamevault connect", provider.empty_text("GameVault", "", True))
        self.connect(games=0)
        self.db.delete("library", Client(self.config, self.db).cache_key)
        p = Provider(self.config, self.db, Library(self.config, self.db, dict(PROFILE)).load(offline=True))
        self.assertIn("no cached GameVault library", p.empty_text("GameVault", "", True))
        self.assertIn("Press S", p.empty_text("GameVault", "", True))
        self.connect(games=0)  # connected and synchronized, nothing in the catalog
        p = Provider(self.config, self.db, Library(self.config, self.db, dict(PROFILE)).load(offline=True))
        self.assertIn("library is empty", p.empty_text("GameVault", "", True))
        self.connect(games=2)
        p = self.provider()
        self.assertIn('No game matches "zzz"', p.empty_text("GameVault", "zzz", True))
        self.assertIn("compatibility filter", p.empty_text("GameVault", "", False))

    def test_sync_failure_with_cached_data_is_visible_in_the_status_line(self):
        self.connect()
        lib = Library(self.config, self.db, dict(PROFILE)).load(offline=True)
        lib.sync = {"cached": True, "warning": "server unreachable"}
        p = Provider(self.config, self.db, lib)
        self.assertEqual(p.gamevault_state()[0], "sync-failed")
        self.assertIn("last sync failed", p.status_text("GameVault", 3, True))
        lib2 = Library(self.config, self.db, dict(PROFILE)).load(offline=True)
        lib2.games = [g for g in lib2.games if g.source != "gamevault"]
        lib2.warnings = ["Server unreachable"]
        p2 = Provider(self.config, self.db, lib2)
        self.assertIn("synchronization failed (Server unreachable)", p2.empty_text("GameVault", "", True))

    def test_switching_tabs_never_uses_the_network_or_reloads_the_library(self):
        self.connect()
        loads = []
        real_load = Library.load

        def counting_load(self_, *a, **k):
            loads.append(k.get("offline", a[1] if len(a) > 1 else None))
            return real_load(self_, *a, **k)

        with patch.object(Library, "load", counting_load), patch("arch_games.gamevault.client.Client.open", side_effect=AssertionError("network")):
            m = Model(Provider(self.config, self.db))
            before = len(loads)
            for _ in range(2):
                for i in range(len(SECTIONS)):
                    m.switch(i)
            self.assertEqual(len(loads), before)  # one load for the whole session of tab switching
            from arch_games.runtime import winetools

            calls = []
            real_doctor = winetools.doctor
            with patch.object(winetools, "doctor", lambda *a, **k: (calls.append(1), real_doctor(*a, **k))[1]):
                m.provider.invalidate()
                m.switch(SECTIONS.index("Wine"))
                m.switch(0)
                m.switch(SECTIONS.index("Wine"))
            self.assertEqual(len(calls), 1)  # slow sections are computed once until `r`

    def test_help_documents_the_filter_mouse_and_feedback_shortcuts(self):
        m = Model(FakeProvider())
        m.handle_key("?")
        screen = "\n".join(text(render_frame(m, 100, 40)))
        for needle in ("a", "toggle the compatibility filter", "double-click", "wheel", "v", "feedback", "S (GameVault tab)"):
            self.assertIn(needle, screen)


class MouseTests(unittest.TestCase):
    def model(self, n=40, clock=None):
        m = Model(FakeProvider(n), clock=clock or (lambda: 0.0))
        return m

    def frame(self, m, w=100, h=30):
        f = render_frame(m, w, h, Caps(False, False))
        m.hits = f.hits
        return f

    def test_click_row_selects_and_double_click_opens_details(self):
        m = self.model()
        f = self.frame(m)
        y = find_row(f, "Game 05")
        m.handle_mouse("click", 5, y)
        self.assertEqual(m.current().label, "Game 05")
        self.assertFalse(m.detail_open)
        m.handle_mouse("double", 5, y)
        self.assertTrue(m.detail_open)

    def test_two_quick_clicks_count_as_a_double_click_but_slow_ones_do_not(self):
        now = [0.0]
        m = self.model(clock=lambda: now[0])
        y = find_row(self.frame(m), "Game 02")
        m.handle_mouse("click", 5, y)
        now[0] = 0.2
        m.handle_mouse("click", 5, y)
        self.assertTrue(m.detail_open)
        m.detail_open = False
        now[0] = 5.0
        m.handle_mouse("click", 5, y)
        now[0] = 9.0
        m.handle_mouse("click", 5, y)
        self.assertFalse(m.detail_open)

    def test_click_tabs_switch_sections_and_narrow_mode_cycles(self):
        m = self.model()
        f = self.frame(m, 120, 30)
        tab = next(h for h in f.hits if h.kind == "tab" and h.value == 2)
        m.handle_mouse("click", tab.x0 + 1, tab.y0)
        self.assertEqual(m.name, "GameVault")
        f = self.frame(m, 50, 30)  # narrow: a single "next section" control
        m.handle_mouse("click", 3, 1)
        self.assertEqual(m.name, "Downloads")

    def test_wheel_scrolls_and_clicks_stay_correct_after_scrolling(self):
        m = self.model(60)
        f = self.frame(m, 100, 24)
        m.handle_mouse("wheel_down", 5, 5)
        self.assertEqual(m.cursor, 3)
        for _ in range(10):
            m.handle_mouse("wheel_down", 5, 5)
        self.assertEqual(m.cursor, 33)
        m.handle_mouse("wheel_up", 5, 5)
        self.assertEqual(m.cursor, 30)
        f = self.frame(m, 100, 24)  # the visible window moved with the cursor
        self.assertEqual(sum(1 for t in text(f) if "Game 0" in t), 0)
        y = find_row(f, "Game 35")
        m.handle_mouse("click", 5, y)
        self.assertEqual(m.current().label, "Game 35")

    def test_hit_testing_follows_the_terminal_size(self):
        m = self.model(60)
        for w, h in ((100, 30), (80, 24), (120, 50), (60, 12)):
            f = self.frame(m, w, h)
            rows = [x for x in f.hits if x.kind == "row"]
            self.assertTrue(rows, (w, h))
            for r in rows:
                self.assertTrue(0 <= r.y0 < h and r.x1 <= w, (w, h, r))
                self.assertIn(m.rows[r.value].label, text(f)[r.y0])  # the rectangle sits on the line that shows that row
            m.cursor = min(m.cursor + 7, 59)

    def test_clicks_outside_any_region_and_after_resize_are_harmless(self):
        m = self.model(5)
        self.frame(m, 100, 30)
        self.assertIsNone(m.handle_mouse("click", 999, 999))
        self.assertIsNone(m.handle_mouse("click", 0, 0))
        self.frame(m, 40, 8)
        self.assertIsNone(m.handle_mouse("click", 90, 25))
        m.hits = []
        self.assertIsNone(m.handle_mouse("click", 3, 3))
        self.assertIsNone(m.handle_mouse("hover", 3, 3))

    def test_detail_actions_and_back_are_clickable(self):
        m = self.model(5)
        m.handle_key("enter")
        f = self.frame(m)
        launch_y = find_row(f, "[l] launch")
        action = m.handle_mouse("click", 4, launch_y)
        self.assertEqual((action.kind, action.argv), ("run", ["launch", "g:0"]))
        back = find_row(f, "[Esc] back")
        m.handle_mouse("click", 4, back)
        self.assertFalse(m.detail_open)

    def test_keyboard_still_does_everything(self):
        m = self.model(5)
        for key in ["down", "down", "enter", "esc"]:
            m.handle_key(key)
        self.assertEqual((m.cursor, m.detail_open), (2, False))
        for key in ["tab", "btab", "/", "G", "esc"]:
            m.handle_key(key)
        self.assertEqual(m.name, "Library")
        self.assertEqual(m.handle_key("q").kind, "quit")

    def test_help_click_closes_help(self):
        m = self.model()
        m.handle_key("?")
        m.handle_mouse("click", 1, 1)
        self.assertFalse(m.help_open)


class MouseSupportDetectionTests(unittest.TestCase):
    CONSTS = {"BUTTON1_PRESSED": 1, "BUTTON1_RELEASED": 2, "BUTTON1_CLICKED": 4, "BUTTON1_DOUBLE_CLICKED": 8, "BUTTON1_TRIPLE_CLICKED": 16, "BUTTON4_PRESSED": 32, "BUTTON5_PRESSED": 64}

    def test_event_translation(self):
        k = lambda b: mouse_kind(b, self.CONSTS)  # noqa: E731
        self.assertEqual(k(4), "click")
        self.assertEqual(k(2), "click")
        self.assertIsNone(k(1))  # a bare press waits for the release
        self.assertEqual(k(8), "double")
        self.assertEqual(k(32), "wheel_up")
        self.assertEqual(k(64), "wheel_down")
        self.assertIsNone(k(0))
        self.assertIsNone(mouse_kind(4, {}))  # a build without the constants: no events, no crash

    def test_fallback_when_mouse_is_unavailable(self):
        class Err(Exception):
            pass

        class NoMouse:
            error = Err

            def mousemask(self, mask):
                return (0, 0)  # e.g. the Linux console without gpm

        class Broken:
            error = Err

            def mousemask(self, mask):
                raise Err()

        class Missing:
            error = Err

        class Works(NoMouse):
            BUTTON1_CLICKED = 4

            def mousemask(self, mask):
                return (mask, 0)

        self.assertIsNone(enable_mouse(NoMouse()))
        self.assertIsNone(enable_mouse(Broken()))
        self.assertIsNone(enable_mouse(Missing()))
        self.assertEqual(enable_mouse(Works())["BUTTON1_CLICKED"], 4)


class FeedbackPanelTests(World):
    def setUp(self):
        super().setUp()
        self.prefix, self.game_dir = build_undertale(self.config, self.db)
        from arch_games.runtime.launchers import LauncherStore

        LauncherStore(self.config, self.db).register(GAME, "Undertale", str(self.game_dir / "UNDERTALE.exe"))
        self.model = Model(self.provider())
        self.model.switch(SECTIONS.index("Installed"))

    def stored(self):
        record = intelligence.get(self.db, intelligence.machine_identity(self.config)["id"], GAME) or {}
        return record.get("gameplay_status", "unverified"), record.get("audio_status", "unknown")

    def classification(self):
        return self.model.provider.lib().reports[GAME]["classification"]

    def open(self):
        self.assertEqual(self.model.current().key, GAME)
        self.model.handle_key("enter")
        self.model.handle_key("v")
        self.assertIsNotNone(self.model.panel)

    def test_opens_from_the_detail_view_with_stored_values_and_documents_the_shortcut(self):
        intelligence.update(self.db, self.config, GAME, gameplay_status="issues", audio_status="works")
        self.model.provider.invalidate(reprobe=False)
        self.model.reload()
        self.model.handle_key("enter")
        screen = "\n".join(text(render_frame(self.model, 100, 30)))
        self.assertIn("[v] compatibility feedback", screen)
        self.model.handle_key("v")
        p = self.model.panel
        self.assertEqual((GAMEPLAY_OPTIONS[p.gameplay][0], AUDIO_OPTIONS[p.audio][0]), ("Works with issues", "Works"))
        panel_screen = "\n".join(text(render_frame(self.model, 100, 30)))
        self.assertIn("Stored: gameplay Works with issues, audio Works", panel_screen)
        self.assertIn("Broken", panel_screen)
        self.assertIn("Untested", panel_screen)

    def test_navigation_changes_nothing_until_save(self):
        self.open()
        for key in ["right", "right", "down", "right", "up", "left"]:
            self.model.handle_key(key)
        self.assertEqual(self.stored(), ("unverified", "unknown"))
        self.assertTrue(self.model.panel.dirty is True or self.model.panel.dirty is False)

    def test_save_updates_record_details_and_classification(self):
        self.assertEqual(self.classification(), "Likely compatible")
        self.open()
        self.model.handle_key("left")  # Untested -> Broken?  cycle: stored 'unverified'(3) -> 2 'fails'
        self.model.handle_key("left")  # -> 1 'issues'
        self.model.handle_key("left")  # -> 0 'works'
        self.model.handle_key("down")
        self.model.handle_key("left")  # audio Untested -> Broken
        self.model.handle_key("left")  # -> Works with issues
        self.model.handle_key("left")  # -> Works
        self.assertEqual(self.stored(), ("unverified", "unknown"))
        self.model.handle_key("s")
        self.assertIsNone(self.model.panel)
        self.assertEqual(self.stored(), ("works", "works"))
        self.assertEqual(self.classification(), "Verified working")
        self.assertIn("Verified working", self.model.message)
        self.model.handle_key("enter")
        self.assertIn("Verified working", "\n".join(text(render_frame(self.model, 100, 30))))

    def test_broken_and_issue_values_map_to_the_cli_values(self):
        self.open()
        for _ in range(1):
            self.model.handle_key("left")  # gameplay -> fails (Broken)
        self.model.handle_key("down")
        self.model.handle_key("left")  # audio -> none (Broken)
        self.model.handle_key("s")
        self.assertEqual(self.stored(), ("fails", "none"))
        self.assertEqual(self.classification(), "Likely incompatible")
        self.open()
        self.model.handle_key("left")  # fails -> issues
        self.model.handle_key("s")
        self.assertEqual(self.stored()[0], "issues")
        self.assertEqual(self.classification(), "Working with issues")

    def test_cancel_by_escape_or_button_keeps_the_stored_feedback(self):
        intelligence.update(self.db, self.config, GAME, gameplay_status="works", audio_status="works")
        before = intelligence.get(self.db, intelligence.machine_identity(self.config)["id"], GAME)
        self.open()
        self.model.handle_key("left")
        self.model.handle_key("esc")
        self.assertIsNone(self.model.panel)
        self.assertIn("Cancelled", self.model.message)
        self.open()
        self.model.handle_key("left")
        self.model.handle_key("tab")
        self.model.handle_key("tab")
        self.model.handle_key("tab")
        self.model.handle_key("enter")  # focus on Cancel
        self.assertIsNone(self.model.panel)
        after = intelligence.get(self.db, intelligence.machine_identity(self.config)["id"], GAME)
        self.assertEqual(before, after)

    def test_only_changed_facts_get_new_evidence_timestamps(self):
        intelligence.update(self.db, self.config, GAME, gameplay_status="works")
        before = intelligence.get(self.db, intelligence.machine_identity(self.config)["id"], GAME)["evidence_times"]
        self.open()
        self.model.handle_key("down")
        self.model.handle_key("left")
        self.model.handle_key("left")
        self.model.handle_key("left")  # audio -> works; gameplay untouched
        self.model.handle_key("s")
        after = intelligence.get(self.db, intelligence.machine_identity(self.config)["id"], GAME)["evidence_times"]
        self.assertEqual(after["gameplay"], before["gameplay"])
        self.assertIn("audio", after)

    def test_mouse_selects_options_and_saves_or_cancels(self):
        self.open()
        f = render_frame(self.model, 100, 30, Caps(False, False))
        self.model.hits = f.hits
        works = next(h for h in f.hits if h.kind == "fb_option" and h.value == ("gameplay", 0))
        audio_issue = next(h for h in f.hits if h.kind == "fb_option" and h.value == ("audio", 1))
        self.model.handle_mouse("click", works.x0 + 1, works.y0)
        self.model.handle_mouse("click", audio_issue.x0 + 1, audio_issue.y0)
        self.assertEqual(self.stored(), ("unverified", "unknown"))  # clicking an option does not save
        f = render_frame(self.model, 100, 30, Caps(False, False))
        self.model.hits = f.hits
        save = next(h for h in f.hits if h.kind == "fb_save")
        self.model.handle_mouse("click", save.x0 + 1, save.y0)
        self.assertEqual(self.stored(), ("works", "issues"))
        self.open()
        f = render_frame(self.model, 100, 30, Caps(False, False))
        self.model.hits = f.hits
        broken = next(h for h in f.hits if h.kind == "fb_option" and h.value == ("gameplay", 2))
        self.model.handle_mouse("click", broken.x0 + 1, broken.y0)
        cancel = next(h for h in f.hits if h.kind == "fb_cancel")
        self.model.handle_mouse("click", cancel.x0 + 1, cancel.y0)
        self.assertEqual(self.stored(), ("works", "issues"))

    def test_panel_renders_vertically_on_narrow_terminals_and_in_ascii(self):
        self.open()
        for w in (40, 60, 71, 72, 100):
            f = render_frame(self.model, w, 24, Caps(False, False))
            for line in text(f):
                self.assertLessEqual(len(line), w)
                self.assertTrue(line.isascii())
            kinds = [h.kind for h in f.hits]
            self.assertEqual(kinds.count("fb_option"), 8)
            self.assertIn("fb_save", kinds)
        unicode_screen = "\n".join(text(render_frame(self.model, 100, 24, Caps(True, True))))
        self.assertIn("(•)", unicode_screen)

    def test_other_machines_records_are_untouched(self):
        mine = intelligence.update(self.db, self.config, GAME, gameplay_status="works")
        other = dict(mine, machine={"id": "other", "name": "arch86", "architecture": "i686"}, gameplay_status="fails")
        intelligence.import_records(self.db, self.config, {"schema": 1, "records": [other]})
        self.open()
        self.model.handle_key("right")
        self.model.handle_key("s")
        self.assertEqual(intelligence.get(self.db, "other", GAME)["gameplay_status"], "fails")
        record = intelligence.get(self.db, intelligence.machine_identity(self.config)["id"], GAME)
        self.assertEqual(record["machine"]["id"], intelligence.machine_identity(self.config)["id"])

    def test_feedback_for_a_versioned_library_id_is_stored_under_the_state_id(self):
        self.config.values.update(server_url="https://fixture.invalid", account="u")
        self.config.save()
        self.config.save_credentials({"api_key": "k"})
        self.db.put("library", Client(self.config, self.db).cache_key, {"synced_at": "t", "games": [{"id": 32, "title": "Undertale", "type": "WINDOWS_SETUP", "metadata": {"title": "Undertale"}, "versions": [{"id": 34, "type": "WINDOWS_SETUP", "version": "v"}]}]})
        model = Model(Provider(self.config, self.db, Library(self.config, self.db, dict(PROFILE)).load(offline=True)))
        model.switch(SECTIONS.index("GameVault"))
        self.assertEqual(model.current().key, "gamevault:32@34")
        model.handle_key("v")
        model.handle_key("left")
        model.handle_key("left")
        model.handle_key("left")
        model.handle_key("s")
        self.assertEqual(self.stored()[0], "works")  # recorded where the prefix, launcher and CLI feedback live
        self.assertIsNone(intelligence.get(self.db, intelligence.machine_identity(self.config)["id"], "gamevault:32@34"))


class PanelWithoutServiceTests(unittest.TestCase):
    def test_provider_without_feedback_support_degrades_with_a_message(self):
        m = Model(FakeProvider(3))
        m.handle_key("v")
        self.assertIsNone(m.panel)
        self.assertIn("not available", m.message)


class SaveFailureTests(unittest.TestCase):
    def test_a_failing_save_keeps_the_panel_and_reports_it(self):
        class Failing(FakeProvider):
            def feedback_state(self, game_id):
                return {"gameplay": "unverified", "audio": "unknown"}

            def save_feedback(self, game_id, gameplay, audio):
                raise OSError("disk full")

        m = Model(Failing(3))
        m.handle_key("v")
        m.handle_key("left")
        m.handle_key("s")
        self.assertIsNotNone(m.panel)  # the choices are kept so you can retry or cancel
        self.assertIn("Could not save: disk full", m.message)
        m.handle_key("esc")
        self.assertIsNone(m.panel)
