import no_probe  # noqa: F401  (must come first: disables real system probes)
import tempfile
import threading
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from arch_games.config import Config
from arch_games.models import Game
from arch_games.ui.gallery import create_server, render


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(self.temp.name)
        self.game = Game(
            "pacman:nethack", '<script>alert("x")</script>', package="nethack", category="Terminal"
        )
        self.library = SimpleNamespace(
            profile={"os_architecture": "i686"}, reports={self.game.id: {"status": "Promising"}}, sync={}
        )

    def test_output_encoding(self):
        page = render(self.library, [self.game], "filters")
        self.assertNotIn(b"<script>alert", page)
        self.assertIn(b"&lt;script&gt;", page)
        self.assertIn(b"data-arch=", page)
        self.assertIn(b"Copy install command", page)

    def test_http_readonly_and_host(self):
        server = create_server(self.library, [self.game], "filters", self.config, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:" + str(server.server_port)
        with urlopen(url) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        for request, status in [
            (Request(url, method="POST"), 405),
            (Request(url, headers={"Host": "evil.example"}), 403),
            (Request(url + "/install"), 404),
            (Request(url, headers={"Origin": "https://evil.example"}), 403),
        ]:
            with self.assertRaises(HTTPError) as context:
                urlopen(request)
            self.assertEqual(context.exception.code, status)
            context.exception.close()
        with urlopen(url + "/api/status") as response:
            self.assertIn(b"downloads", response.read())
