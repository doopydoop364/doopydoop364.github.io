"""Read-only GameVault adapter; contracts captured from public OpenAPI 17.0.1."""

import base64
import hashlib
import json
import re
from datetime import datetime, timezone
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

from ..config import AppError, server_url
from ..models import Game


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AppError("Server redirect refused; configure the final server URL")


class AuthenticationError(AppError):
    pass


def entity_id(value):
    value = str(value)
    if not re.fullmatch(r"[0-9]{1,20}", value):
        raise AppError("GameVault identifier must be a positive decimal ID")
    return value


def normalize(record):
    if not isinstance(record, dict) or "id" not in record:
        raise AppError("Malformed GameVault game record")
    ident = entity_id(record["id"])
    meta = record.get("metadata") or {}
    if not isinstance(meta, dict):
        raise AppError("Malformed GameVault metadata")
    kind = str(record.get("type", "UNDETECTABLE"))
    os_name = "windows" if kind.startswith("WINDOWS") else "linux" if kind.startswith("LINUX") else "unknown"
    genres = meta.get("genres") or []
    if not isinstance(genres, list):
        raise AppError("Malformed GameVault genre metadata")
    category = str(genres[0].get("name", "Other")) if genres and isinstance(genres[0], dict) else "Other"
    return Game(
        "gamevault:" + ident,
        str(meta.get("title") or record.get("title") or "Untitled"),
        source="gamevault",
        category=category,
        description=str(meta.get("description") or ""),
        os=os_name,
        build=str(record.get("version") or ""),
        metadata={"remote": record, "analysis_source": "Server metadata (architecture unknown)"},
    )


class Client:
    def __init__(self, config, database, opener=None, timeout=15, retries=2):
        self.config, self.db = config, database
        self.url = server_url(config.values.get("server_url", ""), config.values.get("allow_http", False))
        self.opener = opener or build_opener(NoRedirect())
        self.timeout, self.retries = timeout, retries
        identity = self.url + "\n" + str(config.values.get("account", ""))
        self.cache_key = hashlib.sha256(identity.encode()).hexdigest()

    def headers(self):
        auth = self.config.credentials()
        if auth.get("api_key"):
            return {"X-Api-Key": auth["api_key"]}
        if auth.get("access_token"):
            return {"Authorization": "Bearer " + auth["access_token"]}
        return {}

    def open(self, path, headers=None, method="GET", authenticated=True, refresh=True):
        if not path.startswith("/api/") or ".." in path or "\n" in path or "\r" in path:
            raise AppError("Invalid API path")
        all_headers = self.headers() if authenticated else {}
        all_headers.update(headers or {})
        all_headers["Accept-Encoding"] = "identity"
        for attempt in range(self.retries + 1):
            request = Request(self.url + path, headers=all_headers, method=method)
            try:
                return self.opener.open(request, timeout=self.timeout)
            except HTTPError as exc:
                code = exc.code
                exc.close()
                if (
                    code == 401
                    and authenticated
                    and refresh
                    and self.config.credentials().get("refresh_token")
                ):
                    self.refresh()
                    return self.open(path, headers, method, authenticated, refresh=False)
                if code in {401, 403, 406}:
                    raise AuthenticationError(
                        "Authentication failed or expired; run gamevault connect"
                    ) from None
                if code < 500 or attempt == self.retries or method != "GET":
                    raise AppError("GameVault request failed (HTTP {})".format(code)) from None
            except (URLError, OSError, TimeoutError):
                if attempt == self.retries or method != "GET":
                    raise AppError("GameVault is unreachable or timed out") from None
            time.sleep(min(0.2 * 2**attempt, 1))
        raise AppError("GameVault request failed")

    def request(self, path, **kwargs):
        with self.open(path, **kwargs) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise AppError("GameVault response exceeds metadata limit")
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise AppError("Malformed GameVault JSON response") from None

    def save_tokens(self, value):
        if not isinstance(value, dict) or not all(
            isinstance(value.get(k), str) and value[k] for k in ["access_token", "refresh_token"]
        ):
            raise AppError("Malformed GameVault token response")
        self.config.save_credentials({k: value[k] for k in ["access_token", "refresh_token"]})

    def login(self, username, password):
        if ":" in username:
            raise AppError("Basic authentication username cannot contain a colon")
        token = base64.b64encode((username + ":" + password).encode()).decode("ascii")
        result = self.request(
            "/api/auth/basic/login", headers={"Authorization": "Basic " + token}, authenticated=False
        )
        self.save_tokens(result)

    def refresh(self):
        token = self.config.credentials().get("refresh_token", "")
        result = self.request(
            "/api/auth/refresh",
            method="POST",
            authenticated=False,
            headers={"Authorization": "Bearer " + token},
        )
        self.save_tokens(result)

    def status(self):
        self.request("/api/users/me")
        return {"connected": True, "server_url": self.url}

    def library(self, offline=False):
        cached = self.db.get("library", self.cache_key)
        if not offline:
            try:
                records, seen = [], set()
                for page in range(1, 201):
                    value = self.request(
                        "/api/games?" + urlencode({"page": page, "limit": 100, "sortBy": "id:ASC"})
                    )
                    if (
                        not isinstance(value, dict)
                        or not isinstance(value.get("data"), list)
                        or not isinstance(value.get("meta"), dict)
                    ):
                        raise AppError("Malformed GameVault pagination")
                    meta = value["meta"]
                    total = meta.get("totalPages")
                    if not isinstance(total, int) or not 0 <= total <= 200 or meta.get("currentPage") != page:
                        raise AppError("Invalid GameVault pagination limits")
                    for record in value["data"]:
                        game = normalize(record)
                        if game.id in seen:
                            raise AppError("Library changed during synchronization; retry")
                        seen.add(game.id)
                        records.append(record)
                    if page >= total:
                        break
                else:
                    raise AppError("GameVault library exceeds page limit")
                cached = {"games": records, "synced_at": datetime.now(timezone.utc).isoformat()}
                self.db.put("library", self.cache_key, cached)
                return [normalize(r) for r in records], {"cached": False, "synced_at": cached["synced_at"]}
            except AuthenticationError:
                raise
            except AppError as exc:
                if not cached:
                    raise
                return [normalize(r) for r in cached["games"]], {
                    "cached": True,
                    "synced_at": cached["synced_at"],
                    "warning": str(exc),
                }
        if not cached:
            raise AppError("No cached GameVault library for this account")
        return [normalize(r) for r in cached["games"]], {"cached": True, "synced_at": cached["synced_at"]}

    def details(self, ident, offline=False):
        ident = entity_id(ident)
        key = self.cache_key + ":" + ident
        if not offline:
            try:
                game = normalize(self.request("/api/games/" + quote(ident)))
                game.metadata["last_synced_at"] = datetime.now(timezone.utc).isoformat()
                self.db.put("details", key, game.to_dict())
                return game, False
            except AuthenticationError:
                raise
            except AppError:
                pass
        cached = self.db.get("details", key)
        if cached:
            return Game(**cached), True
        games, state = self.library(offline=True)
        game = next((g for g in games if g.id == "gamevault:" + ident), None)
        if not game:
            raise AppError("Game details unavailable and no cached record exists")
        game.metadata["last_synced_at"] = state["synced_at"]
        return game, True

    def download_path(self, game_id, version_id=None):
        game_id = entity_id(game_id)
        if version_id is not None:
            return "/api/game/{}/versions/{}".format(game_id, entity_id(version_id))
        return "/api/games/{}/download".format(game_id)

    def artwork(self, media_id):
        with self.open("/api/media/" + entity_id(media_id)) as response:
            kind = response.headers.get("Content-Type", "").split(";")[0]
            if kind not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                raise AppError("Unsupported artwork content type")
            data = response.read(4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise AppError("Artwork exceeds size limit")
            return data, kind
