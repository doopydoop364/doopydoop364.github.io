"""Optional web GUI server: stdlib only, loopback by default, token login -> session cookie + CSRF header."""

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import re
import secrets
import socket
import threading
import time
from urllib.parse import parse_qsl, unquote, urlsplit

from ...config import AppError, private_dir
from .api import Api, BadRequest

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
SESSION_TTL = 8 * 3600
MAX_BODY = 64 * 1024
LOGIN_FAILS, LOGIN_WINDOW = 5, 60.0


class Raw:
    def __init__(self, data, kind):
        self.data, self.kind = data, kind


def static(name):
    return resources.files("arch_games.ui.web").joinpath("static/" + name).read_bytes()


def token_path(config):
    return config.config_dir / "web-token"


def load_token(config, create=True):
    path = token_path(config)
    if path.is_symlink():
        raise AppError("Web token file must not be a symlink")
    if not path.exists():
        if not create:
            raise AppError("No web token yet; run 'arch-games web' once")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(32) + "\n")
    if path.stat().st_mode & 0o077:
        raise AppError("Web token file permissions are too open (need 0600)")
    token = path.read_text().strip()
    if len(token) < 24:
        raise AppError("Web token is too short; delete the file to regenerate it")
    return token


class WebApp:
    def __init__(self, config, host="127.0.0.1", port=8765, allow_remote=False, allowed_hosts=(), token=None):
        if host not in LOOPBACK and not allow_remote:
            raise AppError("Refusing to bind {}: remote access needs --allow-remote (token login stays mandatory)".format(host))
        self.config, self.host, self.requested_port = config, host, port
        self.token = token or load_token(config)
        self.api = Api(config)
        self.sessions = {}  # sid -> (expiry, csrf)
        self.failures = {}
        self.allowed_hosts = set(allowed_hosts)
        self.lock = threading.Lock()
        self.httpd = None

    @property
    def port(self):
        return self.httpd.server_address[1] if self.httpd else self.requested_port

    def start(self):
        app = self
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET

        class Server(ThreadingHTTPServer):
            address_family = family
            daemon_threads = True

        self.httpd = Server((self.host, self.requested_port), make_handler(app))
        return self.port

    def serve_forever(self):
        self._serving = True
        try:
            self.httpd.serve_forever()
        finally:
            self._serving = False

    def stop(self):
        """Safe to call at any time: shutdown() would block forever if serve_forever never ran."""
        if self.httpd:
            if getattr(self, "_serving", False):
                self.httpd.shutdown()
            self.httpd.server_close()

    def host_ok(self, header):
        h = (header or "").lower()
        if not h:
            return False
        allowed = {"127.0.0.1:%d" % self.port, "localhost:%d" % self.port, "[::1]:%d" % self.port, "%s:%d" % (self.host, self.port)}
        return h in allowed or h in self.allowed_hosts

    def new_session(self):
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with self.lock:
            now = time.time()
            self.sessions = {k: v for k, v in self.sessions.items() if v[0] > now}
            self.sessions[sid] = (now + SESSION_TTL, csrf)
        return sid, csrf

    def session(self, cookie):
        m = re.search(r"(?:^|;\s*)ag_session=([A-Za-z0-9_-]{20,64})", cookie or "")
        if not m:
            return None
        with self.lock:
            entry = self.sessions.get(m.group(1))
            if not entry or entry[0] < time.time():
                self.sessions.pop(m.group(1), None)
                return None
            return m.group(1), entry[1]

    def login_blocked(self, ip):
        now = time.time()
        self.failures[ip] = [t for t in self.failures.get(ip, []) if now - t < LOGIN_WINDOW]
        return len(self.failures[ip]) >= LOGIN_FAILS

    def token_ok(self, supplied):
        return isinstance(supplied, str) and hmac.compare_digest(supplied.encode(), self.token.encode())


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ArchGames"
        sys_version = ""
        protocol_version = "HTTP/1.0"

        def log_message(self, *args):
            pass

        # -- helpers ------------------------------------------------------------------------------------------
        def send(self, code, body=b"", ctype="application/json", headers=()):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def fail(self, code, message):
            self.send(code, {"error": message})

        def guard(self):
            """Host check (DNS rebinding) for everything."""
            if not app.host_ok(self.headers.get("Host")):
                self.fail(403, "Unrecognised Host header")
                return False
            return True

        def authed(self, mutating):
            session = app.session(self.headers.get("Cookie"))
            if not session:
                self.fail(401, "Not logged in")
                return None
            if mutating:
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc.lower() != (self.headers.get("Host") or "").lower():
                    self.fail(403, "Cross-origin request refused")
                    return None
                if not hmac.compare_digest((self.headers.get("X-CSRF-Token") or "").encode(), session[1].encode()):
                    self.fail(403, "Missing or invalid CSRF token")
                    return None
            return session

        def body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY or length < 0:
                raise BadRequest("Request body too large")
            if not length:
                return {}
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                raise BadRequest("JSON body required")
            try:
                value = json.loads(self.rfile.read(length))
            except ValueError:
                raise BadRequest("Malformed JSON") from None
            if not isinstance(value, dict):
                raise BadRequest("JSON object required")
            return value

        # -- verbs ----------------------------------------------------------------------------------------------
        def do_GET(self):
            try:
                self._get()
            except ValueError:
                self.fail(400, "Bad request")

        def _get(self):
            if not self.guard():
                return
            url = urlsplit(self.path)
            path = url.path
            query = dict(parse_qsl(url.query, max_num_fields=20))
            if path in {"/", "/index.html"}:
                return self.send(200, static("index.html"), "text/html; charset=utf-8")
            if path in {"/app.js", "/app.css"}:
                return self.send(200, static(path[1:]), "text/javascript; charset=utf-8" if path.endswith("js") else "text/css; charset=utf-8")
            if not path.startswith("/api/"):
                return self.fail(404, "Not found")
            if path == "/api/session":
                session = app.session(self.headers.get("Cookie"))
                return self.send(200, {"authenticated": bool(session), "csrf": session[1] if session else None})
            if not self.authed(False):
                return
            self.route("GET", path, query, {})

        def do_POST(self):
            try:
                self._post()
            except ValueError:
                self.fail(400, "Bad request")

        def _post(self):
            if not self.guard():
                return
            path = urlsplit(self.path).path
            if path == "/api/login":
                ip = self.client_address[0]
                if app.login_blocked(ip):
                    return self.fail(429, "Too many failed logins; wait a minute")
                try:
                    data = self.body()
                except (BadRequest, ValueError) as exc:
                    return self.fail(400, str(exc))
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc.lower() != (self.headers.get("Host") or "").lower():
                    return self.fail(403, "Cross-origin request refused")
                if not app.token_ok(data.get("token")):
                    app.failures.setdefault(ip, []).append(time.time())
                    return self.fail(401, "Invalid token")
                sid, csrf = app.new_session()
                return self.send(200, {"authenticated": True, "csrf": csrf}, headers=[("Set-Cookie", "ag_session=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=%d" % (sid, SESSION_TTL))])
            if not path.startswith("/api/"):
                return self.fail(404, "Not found")
            if not self.authed(True):
                return
            try:
                data = self.body()
            except (BadRequest, ValueError) as exc:
                return self.fail(400, str(exc))
            self.route("POST", path, {}, data)

        def do_PUT(self):
            self.fail(405, "Method not allowed")

        do_DELETE = do_PATCH = do_PUT

        # -- routing ----------------------------------------------------------------------------------------------
        def route(self, method, path, query, data):
            api = app.api
            parts = [unquote(p) for p in path.split("/")[2:]]
            db = None
            try:
                db = api.db()
                result = self.dispatch(api, db, method, parts, query, data)
            except BadRequest as exc:
                return self.fail(400, str(exc))
            except AppError as exc:
                return self.fail(409 if method == "POST" else 404, str(exc))
            except Exception:  # never leak internals to the browser
                return self.fail(500, "Internal error; see 'arch-games doctor'")
            finally:
                if db is not None:
                    db.close()
            if isinstance(result, Raw):
                return self.send(200, result.data, result.kind)
            self.send(200, result)

        def dispatch(self, api, db, method, p, q, d):
            n = len(p)
            if method == "GET":
                if p == ["library"]:
                    return api.library_list(db, q)
                if p == ["recent"]:
                    return api.recent(db)
                if n == 2 and p[0] == "game":
                    return api.game(db, p[1])
                if n == 2 and p[0] == "launcher":
                    return api.launcher_state(db, p[1])
                if p == ["downloads"]:
                    return api.downloads(db)
                if p == ["installs"]:
                    return [{"game": r["key"], "title": r["value"].get("title"), "status": r["value"].get("status"), "step": r["value"].get("step"), "error": r["value"].get("error")} for r in db.all("install_jobs")]
                if n == 2 and p[0] == "job":
                    return api.job(p[1])
                if n == 2 and p[0] == "cover":
                    return self.cover(api, db, p[1])
                if p == ["wine", "doctor"]:
                    return api.wine_doctor(db)
                if n == 3 and p[:2] == ["wine", "status"]:
                    return api.wine_status(db, p[2])
                if p == ["audio"]:
                    return api.audio(db)
                if p == ["doctor"]:
                    return api.doctor(db)
                if p == ["history"]:
                    return [dict(v) for v in sorted(db.mapping("launches").values(), key=lambda v: v.get("started_at", ""), reverse=True)[:50]]
                if p == ["storage"]:
                    return self.storage(api)
            else:
                if n == 2 and p[0] == "favorite":
                    return api.set_favorite(db, p[1], bool(d.get("value")))
                if n == 2 and p[0] == "notes":
                    return api.set_notes(db, p[1], d.get("text", ""), d.get("tags", []))
                if p == ["downloads", "add"]:
                    return api.download_add(db, d.get("game"))
                if p == ["downloads", "run"]:
                    return api.download_run()
                if p == ["downloads", "retry"]:
                    return api.download_retry(db, d.get("id"), d.get("restart"))
                if p == ["downloads", "pause"]:
                    return api.download_pause(db, d.get("id"))
                if p == ["downloads", "cancel"]:
                    return api.download_cancel(db, d.get("id"), d.get("cleanup"))
                if p == ["install", "prepare"]:
                    return api.install_prepare(d.get("game"))
                if p == ["install", "approve"]:
                    return api.install_approve(d.get("game"), d.get("plan_sha256"), d.get("approve_installer"))
                if p == ["launch"]:
                    return api.launch(d.get("game"), d.get("profile"))
                if n == 3 and p[0] == "launcher" and p[2] == "settings":
                    return api.launcher_settings(db, p[1], d)
                if n == 3 and p[0] == "launcher" and p[2] == "profile":
                    return api.launcher_profile(db, p[1], d)
                if n == 3 and p[:2] == ["wine", "config"]:
                    return api.wine_config(db, p[2], d)
                if p == ["audio", "remote", "disable"]:
                    return api.audio_remote_disable()
            raise AppError("Unknown operation")

        def cover(self, api, db, media):
            if not re.fullmatch(r"[0-9]{1,20}", media):
                raise BadRequest("Invalid media ID")
            cache = private_dir(app.config.cache_dir / "covers") / (media + ".img")
            if not cache.exists():
                from ...gamevault.client import Client

                data = Client(app.config, db).artwork(media)
                cache.write_bytes(data[0] if isinstance(data, tuple) else data)
            raw = cache.read_bytes()
            kind = "image/png" if raw[:4] == b"\x89PNG" else "image/jpeg" if raw[:2] == b"\xff\xd8" else "image/webp" if raw[8:12] == b"WEBP" else "image/gif" if raw[:4] == b"GIF8" else None
            if not kind:
                raise AppError("Unsupported image")
            return Raw(raw, kind)

        def storage(self, api):
            import shutil

            out = {}
            for name in ("downloads", "installed", "prefixes", "backups"):
                path = app.config.directory(name)
                from ...core.gameview import dir_size

                size, partial = dir_size(path, 50000)
                out[name] = {"bytes": size, "partial": partial}
            out["free_bytes"] = shutil.disk_usage(app.config.data_dir).free
            return out

    return Handler
