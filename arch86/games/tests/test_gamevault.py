import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError, URLError

from arch_games.config import AppError, Config, server_url
from arch_games.gamevault.client import AuthenticationError, Client
from arch_games.storage.database import Database


class Response(io.BytesIO):
    headers = {}


class Opener:
    def __init__(self, values):
        self.values, self.requests = list(values), []

    def open(self, req, **kwargs):
        self.requests.append(req)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return Response(json.dumps(value).encode())


class GameVaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(self.temp.name)
        self.config.values.update(server_url="https://example.invalid", account="fixture")
        self.db = Database(self.config.data_dir / "state.db")
        self.addCleanup(self.db.close)
        self.page = json.loads((Path(__file__).parents[1] / "fixtures/gamevault-page.json").read_text())

    def client(self, values):
        return Client(self.config, self.db, Opener(values), retries=0)

    def test_secure_urls(self):
        for url in ["https://user:secret@host", "file:///tmp/a", "https://host?token=x", "http://host"]:
            with self.assertRaises(AppError):
                server_url(url)
        self.assertEqual(server_url("http://host", True), "http://host")

    def test_login_refresh(self):
        client = self.client(
            [
                {"access_token": "first", "refresh_token": "refresh"},
                HTTPError("url", 401, "bad", {}, None),
                {"access_token": "new", "refresh_token": "rotated"},
                {},
            ]
        )
        client.login("fixture", "not-a-real-password")
        client.status()
        self.assertEqual(self.config.credentials()["refresh_token"], "rotated")
        self.assertEqual(self.config.secrets.stat().st_mode & 0o777, 0o600)
        self.assertEqual(client.opener.requests[0].method, "GET")
        self.assertNotIn("password", client.opener.requests[0].full_url)

    def test_invalid_credentials(self):
        client = self.client([HTTPError("url", 401, "bad", {}, None)])
        with self.assertRaises(AuthenticationError):
            client.login("user", "secret")

    def test_cache_and_partial_sync(self):
        client = self.client([self.page])
        games, state = client.library()
        self.assertEqual(games[0].os, "windows")
        self.assertFalse(state["cached"])
        page = dict(self.page, meta={"currentPage": 1, "totalPages": 2})
        client = self.client([page, URLError("offline")])
        games, state = client.library()
        self.assertTrue(state["cached"])
        self.assertEqual(len(games), 1)
        self.assertIn("synced_at", state)

    def test_pagination(self):
        first = dict(self.page, meta={"currentPage": 1, "totalPages": 2})
        second = {"data": [{"id": 2}], "meta": {"currentPage": 2, "totalPages": 2}}
        games, _ = self.client([first, second]).library()
        self.assertEqual(len(games), 2)

    def test_malformed_and_unavailable(self):
        for value in [{}, {"data": [None], "meta": {"currentPage": 1, "totalPages": 1}}, URLError("offline")]:
            with self.subTest(value=value), self.assertRaises(AppError):
                self.client([value]).library()

    def test_empty_metadata(self):
        page = {"data": [{"id": 1, "metadata": None}], "meta": {"currentPage": 1, "totalPages": 1}}
        games, _ = self.client([page]).library()
        self.assertEqual(games[0].title, "Untitled")

    def test_unused_relations_and_credentials_not_cached(self):
        page = dict(self.page)
        page["data"] = [
            dict(
                self.page["data"][0],
                api_key="sensitive-fixture",
                bookmarked_users=[{"api_key": "sensitive-fixture"}],
                metadata={"title": "Fixture", "launch_parameters": "never adopt"},
            )
        ]
        client = self.client([page])
        client.library()
        cache = json.dumps(self.db.get("library", client.cache_key))
        self.assertNotIn("sensitive-fixture", cache)
        self.assertNotIn("launch_parameters", cache)

    def test_invalid_tokens_do_not_leak_into_errors(self):
        client = self.client([])
        self.config.save_credentials({"access_token": "sensitive-fixture\r\nInjected: x"})
        with self.assertRaises(AppError) as error:
            client.headers()
        self.assertNotIn("sensitive-fixture", str(error.exception))
        with self.assertRaises(AppError):
            client.save_tokens({"access_token": "sensitive-fixture\n", "refresh_token": "valid-fixture"})

    def test_credentials_are_bound_to_server_and_account(self):
        old_client = self.client([])
        self.config.save_credentials({"access_token": "fixture-access", "refresh_token": "fixture-refresh"})
        self.config.values["server_url"] = "https://another.invalid"
        other = self.client([])
        with self.assertRaises(AuthenticationError):
            other.headers()
        self.config.save_credentials({"access_token": "other-access", "refresh_token": "other-refresh"})
        with self.assertRaises(AuthenticationError):
            old_client.headers()

    def test_already_rotated_token_is_reused(self):
        client = self.client([])
        self.config.save_credentials({"access_token": "rotated-fixture", "refresh_token": "fresh-fixture"})
        client.refresh("Bearer stale-fixture")
        self.assertEqual(len(client.opener.requests), 0)

    def test_interrupted_response_preserves_cache(self):
        import http.client
        from types import SimpleNamespace

        client = self.client([self.page])
        client.library()

        class Broken(Response):
            def read(self, *args):
                raise http.client.IncompleteRead(b'{"data":', 100)

        client.opener = SimpleNamespace(open=lambda *args, **kwargs: Broken())
        games, state = client.library()
        self.assertTrue(state["cached"])
        self.assertEqual(len(games), 1)
        self.assertIn("interrupted", state["warning"])
