"""Authenticated HTTP + WebSocket audio bridge. Raw PCM (s16le) per frame; the browser plays it with Web Audio."""

import asyncio
import contextlib
import hmac
import json
import time
from importlib import resources
from urllib.parse import urlsplit

from . import ws
from .config import LOOPBACK, ConfigError, read_token
from .stream import FRAME_MS, Listener, StreamError, VMStream

MAX_REQUEST = 16 * 1024
AUTH_WINDOW, AUTH_FAILS, AUTH_BLOCK = 60.0, 5, 60.0


def asset(name):
    return resources.files("arch_games.remote").joinpath("static/" + name).read_bytes()


class Bridge:
    def __init__(self, cfg, token=None, which=None):
        self.cfg = cfg
        self.token = token or read_token(cfg["token_file"])
        kwargs = {"which": which} if which else {}
        self.streams = {name: VMStream(name, vm, **kwargs) for name, vm in cfg["vms"].items()}
        self.failures = {}
        self.server = None
        self.port = None
        self.connections = 0

    # -- lifecycle ---------------------------------------------------------------------------------------
    async def start(self):
        host, port = self.cfg["listen"], self.cfg["port"]
        self.server = await asyncio.start_server(self.handle, host, port, limit=MAX_REQUEST)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for stream in self.streams.values():
            for listener in list(stream.listeners):
                listener.closed.set()
                listener.push(None)
        await asyncio.sleep(0)

    # -- auth helpers --------------------------------------------------------------------------------------
    def blocked(self, ip):
        now = time.monotonic()
        recent = [t for t in self.failures.get(ip, []) if now - t < AUTH_WINDOW]
        self.failures[ip] = recent
        return len(recent) >= AUTH_FAILS and now - recent[-1] < AUTH_BLOCK

    def fail(self, ip):
        self.failures.setdefault(ip, []).append(time.monotonic())

    def token_ok(self, supplied):
        return isinstance(supplied, str) and hmac.compare_digest(supplied.encode(), self.token.encode())

    def origin_ok(self, headers):
        origin = headers.get("origin")
        if not origin:
            return True  # non-browser client; it still needs the token
        if origin in self.cfg["allow_origins"]:
            return True
        return urlsplit(origin).netloc.lower() == headers.get("host", "").lower()

    # -- HTTP --------------------------------------------------------------------------------------------------
    async def respond(self, writer, status, body=b"", ctype="text/plain; charset=utf-8", extra=()):
        if isinstance(body, str):
            body = body.encode()
        frame_src = self.cfg.get("novnc_url") or ""
        csp = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws: wss:; img-src 'self' data:; frame-src {}; base-uri 'none'; form-action 'none'".format(
            "'self' " + (urlsplit(frame_src).scheme + "://" + urlsplit(frame_src).netloc if frame_src else "")
        ).strip()
        headers = [("Content-Type", ctype), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"),
                   ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                   ("Content-Security-Policy", csp), ("Connection", "close"), *extra]
        phrase = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 429: "Too Many Requests", 503: "Service Unavailable"}[status]
        writer.write(("HTTP/1.1 %d %s\r\n" % (status, phrase)).encode() + "".join("%s: %s\r\n" % h for h in headers).encode() + b"\r\n" + body)
        with contextlib.suppress(ConnectionError):
            await writer.drain()

    async def handle(self, reader, writer):
        ip = (writer.get_extra_info("peername") or ("?",))[0]
        sock = writer.get_extra_info("socket")
        if sock:
            import socket
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = raw.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            path = urlsplit(target).path
            if headers.get("upgrade", "").lower() == "websocket":
                await self.websocket(reader, writer, ip, path, headers)
            else:
                await self.http(writer, ip, method, path, headers)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ValueError, ConnectionError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def http(self, writer, ip, method, path, headers):
        if method != "GET":
            return await self.respond(writer, 405)
        if path == "/healthz":
            return await self.respond(writer, 200, "ok")
        if path == "/static/player.js":
            return await self.respond(writer, 200, asset("player.js"), "text/javascript; charset=utf-8")
        if path == "/static/player.css":
            return await self.respond(writer, 200, asset("player.css"), "text/css; charset=utf-8")
        if path == "/":
            items = "".join('<li><a href="/vm/{0}/">{0}</a> · <a href="/wrapper/{0}/">with display</a></li>'.format(n) for n in self.streams)
            return await self.respond(writer, 200, "<!doctype html><meta charset=utf-8><title>VM audio</title><h1>VM audio</h1><ul>%s</ul>" % items, "text/html; charset=utf-8")
        parts = [p for p in path.split("/") if p]
        if len(parts) == 2 and parts[0] in {"vm", "wrapper"} and parts[1] in self.streams:
            name = "player.html" if parts[0] == "vm" else "wrapper.html"
            page = asset(name).decode().replace("__VM__", parts[1]).replace("__NOVNC__", json.dumps(self.cfg.get("novnc_url") or ""))
            return await self.respond(writer, 200, page, "text/html; charset=utf-8")
        if path == "/api/status":
            if self.blocked(ip):
                return await self.respond(writer, 429)
            supplied = headers.get("authorization", "")[7:] if headers.get("authorization", "").lower().startswith("bearer ") else ""
            if not self.token_ok(supplied):
                self.fail(ip)
                return await self.respond(writer, 401)
            return await self.respond(writer, 200, json.dumps({n: s.status() for n, s in self.streams.items()}), "application/json")
        await self.respond(writer, 404)

    # -- WebSocket -----------------------------------------------------------------------------------------------
    async def websocket(self, reader, writer, ip, path, headers):
        parts = [p for p in path.split("/") if p]
        if len(parts) != 3 or parts[0] != "vm" or parts[2] != "ws" or parts[1] not in self.streams:
            return await self.respond(writer, 404)
        if self.blocked(ip):
            return await self.respond(writer, 429)
        if not self.origin_ok(headers):
            return await self.respond(writer, 403, "Cross-origin WebSocket refused")
        key = headers.get("sec-websocket-key")
        if not key or headers.get("sec-websocket-version") != "13":
            return await self.respond(writer, 400)
        stream = self.streams[parts[1]]
        if len(stream.listeners) >= self.cfg["max_clients_per_vm"]:
            return await self.respond(writer, 503, "Too many listeners for this VM")
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % ws.accept_key(key)).encode())
        await writer.drain()
        try:  # the token travels in the first message, never in the URL
            opcode, payload = await asyncio.wait_for(ws.read_frame(reader, 1024), 5)
            message = json.loads(payload) if opcode == ws.OP_TEXT else {}
        except (asyncio.TimeoutError, ws.ProtocolError, ValueError, asyncio.IncompleteReadError):
            message = {}
        if not self.token_ok(message.get("token")) or message.get("type") != "auth":
            self.fail(ip)
            await self.send(writer, ws.OP_TEXT, json.dumps({"type": "error", "message": "authentication failed"}))
            await self.send(writer, ws.OP_CLOSE, b"\x03\xf0")
            return
        listener = Listener()
        self.connections += 1
        try:
            await stream.add(listener)
        except StreamError as exc:
            await self.send(writer, ws.OP_TEXT, json.dumps({"type": "error", "message": str(exc)}))
            await self.send(writer, ws.OP_CLOSE, b"\x03\xf3")
            self.connections -= 1
            return
        vm = stream.vm
        await self.send(writer, ws.OP_TEXT, json.dumps({"type": "hello", "format": "s16le", "rate": vm["rate"], "channels": vm["channels"], "frame_ms": FRAME_MS, "vm": stream.name}))
        sender = asyncio.ensure_future(self.pump(writer, listener, stream))
        try:
            while not sender.done():
                receive = asyncio.ensure_future(ws.read_frame(reader, 1024))
                done, _ = await asyncio.wait({receive, sender}, return_when=asyncio.FIRST_COMPLETED)
                if receive not in done:
                    receive.cancel()
                    break
                opcode, payload = receive.result()
                if opcode == ws.OP_CLOSE:
                    break
                if opcode == ws.OP_PING:
                    await self.send(writer, ws.OP_PONG, payload)
        except (ws.ProtocolError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender
            await stream.remove(listener)  # last listener out stops the capture process
            self.connections -= 1
            with contextlib.suppress(Exception):
                await self.send(writer, ws.OP_CLOSE, b"\x03\xe8")

    async def send(self, writer, opcode, payload):
        writer.write(ws.encode_frame(opcode, payload))
        await writer.drain()

    async def pump(self, writer, listener, stream):
        last = time.monotonic()
        while True:
            frame = await listener.queue.get()
            if frame is None:
                await self.send(writer, ws.OP_TEXT, json.dumps({"type": "error", "message": listener.error or "stream ended"}))
                return
            await self.send(writer, ws.OP_BINARY, frame)
            now = time.monotonic()
            if now - last > 2:
                last = now
                await self.send(writer, ws.OP_TEXT, json.dumps({"type": "stats", "dropped": listener.dropped, "listeners": len(stream.listeners)}))
