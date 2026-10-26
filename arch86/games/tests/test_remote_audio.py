"""Audio bridge with real sockets and real (synthetic) capture subprocesses. No audio hardware is used."""

import no_probe  # noqa: F401  (must come first: disables real system probes)
import asyncio
import base64
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from arch_games.remote import config as cfgmod
from arch_games.remote import ws
from arch_games.remote.server import Bridge
from arch_games.remote.stream import capture_argv, StreamError

TOKEN = "t" * 40
FAKE = """
import sys, time, struct
source = sys.argv[1]
if 'missing' in source:
    sys.stderr.write('Failed to open source: No such entity\\n'); sys.exit(1)
if 'silent' in source:
    time.sleep(60)
value = sum(source.encode()) % 30000 + 100
frame = struct.pack('<h', value) * 1920
while True:
    sys.stdout.buffer.write(frame); sys.stdout.buffer.flush(); time.sleep(0.02)
"""


def make_cfg(tmp, vms=("arch64", "arch86"), **extra):
    script = Path(tmp) / "fake_capture.py"
    script.write_text(FAKE)
    token = Path(tmp) / "token"
    token.write_text(TOKEN + "\n")
    token.chmod(0o600)
    raw = {"listen": "127.0.0.1", "port": 0, "token_file": str(token), "vms": {
        n: {"sink": n + "_vm", "capture_command": [sys.executable, str(script), "{source}"]} for n in vms}}
    raw.update(extra)
    path = Path(tmp) / "bridge.json"
    path.write_text(json.dumps(raw))
    return cfgmod.load(path)


class Client:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def open(cls, port, path, origin=None, host=None):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        key = base64.b64encode(os.urandom(16)).decode()
        lines = ["GET %s HTTP/1.1" % path, "Host: %s" % (host or "127.0.0.1:%d" % port), "Upgrade: websocket", "Connection: Upgrade",
                 "Sec-WebSocket-Key: %s" % key, "Sec-WebSocket-Version: 13"]
        if origin:
            lines.append("Origin: " + origin)
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        return cls(reader, writer), head.decode()

    async def send_text(self, obj):
        self.writer.write(ws.client_frame(ws.OP_TEXT, json.dumps(obj)))
        await self.writer.drain()

    async def recv(self, timeout=5):
        head = await asyncio.wait_for(self.reader.readexactly(2), timeout)
        opcode, n = head[0] & 0xF, head[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", await self.reader.readexactly(2))[0]
        data = await self.reader.readexactly(n)
        return opcode, data

    async def auth(self, token=TOKEN):
        await self.send_text({"type": "auth", "token": token})
        return await self.recv()

    async def close(self):
        try:
            self.writer.write(ws.client_frame(ws.OP_CLOSE, b"\x03\xe8"))
            await self.writer.drain()
        except ConnectionError:
            pass
        self.writer.close()


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


async def wait_until(predicate, timeout=5):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


class BridgeCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(self.tmp.name)
        self.bridge = Bridge(self.cfg)
        self.port = await self.bridge.start()
        self.addAsyncCleanup(self.bridge.close)

    async def stream_frames(self, client, count=3):
        frames = []
        while len(frames) < count:
            opcode, data = await client.recv()
            if opcode == ws.OP_BINARY:
                frames.append(data)
        return frames


class LifecycleTests(BridgeCase):
    async def test_auth_stream_and_cleanup_on_disconnect(self):
        client, head = await Client.open(self.port, "/vm/arch64/ws")
        self.assertIn("101", head)
        opcode, data = await client.auth()
        hello = json.loads(data)
        self.assertEqual((opcode, hello["type"], hello["rate"], hello["channels"]), (ws.OP_TEXT, "hello", 48000, 2))
        frames = await self.stream_frames(client)
        self.assertTrue(all(len(f) == 48000 * 2 * 2 * 20 // 1000 for f in frames))
        stream = self.bridge.streams["arch64"]
        pid = stream.process.pid
        self.assertEqual(stream.status()["state"], "running")
        await client.close()
        self.assertTrue(await wait_until(lambda: not alive(pid) and stream.state == "idle"))
        self.assertEqual(self.bridge.connections, 0)

    async def test_abrupt_disconnect_releases_capture(self):
        client, _ = await Client.open(self.port, "/vm/arch64/ws")
        await client.auth()
        pid = self.bridge.streams["arch64"].process.pid
        client.writer.transport.abort()
        self.assertTrue(await wait_until(lambda: not alive(pid)))
        self.assertEqual(len(self.bridge.streams["arch64"].listeners), 0)

    async def test_multiple_reconnects_restart_capture_each_time(self):
        pids = set()
        for _ in range(4):
            client, _ = await Client.open(self.port, "/vm/arch64/ws")
            await client.auth()
            await self.stream_frames(client, 1)
            pids.add(self.bridge.streams["arch64"].process.pid)
            await client.close()
            self.assertTrue(await wait_until(lambda: self.bridge.streams["arch64"].process is None))
        self.assertEqual(len(pids), 4)
        self.assertEqual(self.bridge.streams["arch64"].starts, 4)

    async def test_second_listener_shares_one_capture(self):
        a, _ = await Client.open(self.port, "/vm/arch64/ws")
        await a.auth()
        b, _ = await Client.open(self.port, "/vm/arch64/ws")
        await b.auth()
        stream = self.bridge.streams["arch64"]
        self.assertEqual(stream.starts, 1)
        pid = stream.process.pid
        await a.close()
        await asyncio.sleep(0.2)
        self.assertTrue(alive(pid))  # still one listener
        await b.close()
        self.assertTrue(await wait_until(lambda: not alive(pid)))

    async def test_listener_limit(self):
        self.bridge.cfg["max_clients_per_vm"] = 1
        a, _ = await Client.open(self.port, "/vm/arch64/ws")
        await a.auth()
        _, head = await Client.open(self.port, "/vm/arch64/ws")
        self.assertIn("503", head)
        await a.close()


class FailureTests(BridgeCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.bridge.cfg["vms"]["gone"] = dict(self.cfg["vms"]["arch64"], sink="missing_vm", source="missing_vm.monitor")
        self.bridge.streams["gone"] = type(self.bridge.streams["arch64"])("gone", self.bridge.cfg["vms"]["gone"])

    async def test_missing_source_reports_reason_to_client(self):
        client, _ = await Client.open(self.port, "/vm/gone/ws")
        opcode, data = await client.auth()
        message = json.loads(data)
        self.assertEqual(message["type"], "error")
        self.assertIn("No such entity", message["message"])
        self.assertEqual(self.bridge.streams["gone"].state, "failed")
        self.assertIsNone(self.bridge.streams["gone"].process)
        self.assertEqual(len(self.bridge.streams["gone"].listeners), 0)

    async def test_failed_stream_can_recover_on_next_connect(self):
        c, _ = await Client.open(self.port, "/vm/gone/ws")
        await c.auth()
        self.bridge.streams["gone"].vm["source"] = "fixed_vm.monitor"
        c2, _ = await Client.open(self.port, "/vm/gone/ws")
        opcode, data = await c2.auth()
        self.assertEqual(json.loads(data)["type"], "hello")
        await c2.close()

    async def test_tool_not_installed(self):
        vm = dict(self.cfg["vms"]["arch64"], capture_command=None)
        with self.assertRaises(StreamError):
            capture_argv(vm, which=lambda n: None)
        self.assertEqual(capture_argv(vm, which=lambda n: "/usr/bin/" + n if n == "pw-record" else None)[0], "pw-record")
        argv = capture_argv(vm, which=lambda n: "/usr/bin/" + n)
        self.assertEqual(argv[0], "parec")
        self.assertIn("--device=arch64_vm.monitor", argv)

    async def test_capture_that_dies_mid_stream_notifies_client(self):
        client, _ = await Client.open(self.port, "/vm/arch64/ws")
        await client.auth()
        await self.stream_frames(client, 1)
        self.bridge.streams["arch64"].process.kill()
        messages = []
        for _ in range(60):
            opcode, data = await client.recv()
            if opcode == ws.OP_TEXT:
                messages.append(json.loads(data))
                if messages[-1]["type"] == "error":
                    break
        self.assertEqual(messages[-1]["message"], "capture ended unexpectedly")
        await client.close()
        self.assertTrue(await wait_until(lambda: len(self.bridge.streams["arch64"].listeners) == 0))


class AuthTests(BridgeCase):
    async def test_wrong_and_missing_token_never_start_capture(self):
        for token in ("wrong", "", None):
            client, _ = await Client.open(self.port, "/vm/arch64/ws")
            opcode, data = await client.auth(token)
            self.assertEqual(json.loads(data)["type"], "error")
            self.assertIsNone(self.bridge.streams["arch64"].process)

    async def test_no_auth_message_times_out_without_capture(self):
        client, _ = await Client.open(self.port, "/vm/arch64/ws")
        await client.send_text({"type": "hello"})
        opcode, data = await client.recv()
        self.assertEqual(json.loads(data)["message"], "authentication failed")
        self.assertIsNone(self.bridge.streams["arch64"].process)

    async def test_cross_origin_websocket_refused(self):
        _, head = await Client.open(self.port, "/vm/arch64/ws", origin="http://evil.example")
        self.assertIn("403", head)
        _, head = await Client.open(self.port, "/vm/arch64/ws", origin="http://127.0.0.1:%d" % self.port)
        self.assertIn("101", head)
        self.bridge.cfg["allow_origins"].append("https://mac.tailnet.example")
        _, head = await Client.open(self.port, "/vm/arch64/ws", origin="https://mac.tailnet.example")
        self.assertIn("101", head)

    async def test_bruteforce_is_throttled(self):
        for _ in range(5):
            client, _ = await Client.open(self.port, "/vm/arch64/ws")
            await client.auth("nope")
        _, head = await Client.open(self.port, "/vm/arch64/ws")
        self.assertIn("429", head)

    async def test_unknown_vm_and_bad_paths(self):
        _, head = await Client.open(self.port, "/vm/nonexistent/ws")
        self.assertIn("404", head)
        _, head = await Client.open(self.port, "/vm/../etc/ws")
        self.assertIn("404", head)

    async def test_http_status_requires_bearer_and_pages_have_no_secret(self):
        async def get(path, headers=""):
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
            writer.write(("GET %s HTTP/1.1\r\nHost: x\r\n%s\r\n" % (path, headers)).encode())
            await writer.drain()
            data = await reader.read(65536)
            writer.close()
            return data.decode(errors="replace")

        self.assertIn("401", (await get("/api/status")).split("\r\n")[0])
        ok = await get("/api/status", "Authorization: Bearer %s\r\n" % TOKEN)
        self.assertIn("200", ok.split("\r\n")[0])
        self.assertIn('"arch64"', ok)
        for path in ("/", "/vm/arch64/", "/static/player.js", "/wrapper/arch86/"):
            page = await get(path)
            self.assertIn("200", page.split("\r\n")[0], path)
            self.assertNotIn(TOKEN, page)
            self.assertIn("Content-Security-Policy", page)
        self.assertIn("404", (await get("/vm/nope/")).split("\r\n")[0])
        self.assertIn("404", (await get("/static/../config")).split("\r\n")[0])


class IsolationTests(BridgeCase):
    async def test_concurrent_vms_receive_only_their_own_audio(self):
        a, _ = await Client.open(self.port, "/vm/arch64/ws")
        b, _ = await Client.open(self.port, "/vm/arch86/ws")
        await a.auth()
        await b.auth()
        fa, fb = await self.stream_frames(a, 5), await self.stream_frames(b, 5)
        va = {struct.unpack("<h", f[:2])[0] for f in fa}
        vb = {struct.unpack("<h", f[:2])[0] for f in fb}
        self.assertEqual(va, {sum(b"arch64_vm.monitor") % 30000 + 100})
        self.assertEqual(vb, {sum(b"arch86_vm.monitor") % 30000 + 100})
        self.assertNotEqual(va, vb)
        pa, pb = self.bridge.streams["arch64"].process.pid, self.bridge.streams["arch86"].process.pid
        self.assertNotEqual(pa, pb)
        await a.close()
        self.assertTrue(await wait_until(lambda: not alive(pa)))
        self.assertTrue(alive(pb))  # the other VM keeps streaming
        await b.close()
        self.assertTrue(await wait_until(lambda: not alive(pb)))


class ConfigTests(unittest.TestCase):
    def test_sources_must_be_dedicated_monitors(self):
        for bad in ("@DEFAULT_MONITOR@", "alsa_input.usb-mic", "-evil.monitor", "a b.monitor", "x;rm.monitor", "", None, 5):
            with self.assertRaises(cfgmod.ConfigError, msg=repr(bad)):
                cfgmod.validate_source(bad)
        self.assertEqual(cfgmod.validate_source("arch64_vm.monitor"), "arch64_vm.monitor")

    def test_default_source_is_sink_monitor_and_no_sharing(self):
        vm = cfgmod.validate_vm("arch64", {"sink": "arch64_vm"})
        self.assertEqual(vm["source"], "arch64_vm.monitor")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "c.json").write_text(json.dumps({"vms": {"a": {"sink": "s"}, "b": {"sink": "s"}}}))
            with self.assertRaises(cfgmod.ConfigError):
                cfgmod.load(Path(d) / "c.json")

    def test_invalid_vm_settings(self):
        for vm in ({"sink": "@DEFAULT_SINK@"}, {}, {"sink": "x", "rate": 12345}, {"sink": "x", "channels": 6}, {"sink": "x", "latency_ms": 5}):
            with self.assertRaises(cfgmod.ConfigError):
                cfgmod.validate_vm("ok", vm)
        with self.assertRaises(cfgmod.ConfigError):
            cfgmod.validate_vm("Bad Name", {"sink": "x"})

    def test_token_file_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            path, token_path = cfgmod.init(d, {"arch64": "arch64_vm"})
            self.assertEqual(oct(token_path.stat().st_mode & 0o777), "0o600")
            self.assertGreaterEqual(len(cfgmod.read_token(token_path)), 40)
            token_path.chmod(0o644)
            with self.assertRaises(cfgmod.ConfigError):
                cfgmod.read_token(token_path)
            token_path.chmod(0o600)
            with self.assertRaises(cfgmod.ConfigError):
                cfgmod.init(d, {"arch64": "x"})  # existing config not overwritten silently
            first = token_path.read_text()
            cfgmod.init(d, {"arch64": "x"}, force=True)
            self.assertEqual(token_path.read_text(), first)  # token kept

    def test_nonloopback_bind_refused_by_cli(self):
        from arch_games.remote.__main__ import main

        with tempfile.TemporaryDirectory() as d:
            cfgmod.init(d, {"arch64": "arch64_vm"})
            with self.assertRaises(SystemExit) as ctx:
                main(["--config", str(Path(d) / "bridge.json"), "serve", "--listen", "0.0.0.0"])
            self.assertIn("allow-nonloopback", str(ctx.exception))

    def test_frame_codec_rejects_bad_frames(self):
        async def run(data):
            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            return await ws.read_frame(reader, 100)

        self.assertEqual(asyncio.run(run(ws.client_frame(ws.OP_TEXT, "hi"))), (ws.OP_TEXT, b"hi"))
        with self.assertRaises(ws.ProtocolError):
            asyncio.run(run(ws.encode_frame(ws.OP_TEXT, "unmasked")))
        with self.assertRaises(ws.ProtocolError):
            asyncio.run(run(ws.client_frame(ws.OP_BINARY, b"x" * 500)))


class GuestSideTests(unittest.TestCase):
    def setUp(self):
        from arch_games.config import Config

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Config(Path(self.tmp.name) / "state")

    def test_disabled_by_default_and_token_stored_privately(self):
        from arch_games.audio import remote

        self.assertFalse(remote.status(self.config)["enabled"])
        self.assertIsNone(remote.page_url(self.config))
        url = remote.configure(self.config, "http://100.64.0.5:8787", "arch64", "x" * 40, allow_http=True)
        self.assertEqual(url, "http://100.64.0.5:8787/wrapper/arch64/")
        self.assertNotIn("x" * 40, json.dumps(self.config.values))
        self.assertEqual(oct(remote.token_file(self.config).stat().st_mode & 0o777), "0o600")
        self.config.save_credentials({"api_key": "gv"})  # GameVault reconnect rewrites credentials.json ...
        self.assertEqual(remote.read_token(self.config), "x" * 40)  # ... without losing the bridge token
        self.config.disconnect()
        self.assertEqual(remote.read_token(self.config), "x" * 40)
        remote.disable(self.config)
        self.assertIsNone(remote.read_token(self.config))
        self.assertFalse(remote.status(self.config)["enabled"])

    def test_http_requires_flag_and_short_token_refused(self):
        from arch_games.audio import remote
        from arch_games.config import AppError

        with self.assertRaises(AppError):
            remote.configure(self.config, "http://host:8787", "arch64", "x" * 40)
        with self.assertRaises(AppError):
            remote.configure(self.config, "https://host:8787", "arch64", "short")

    def test_status_against_live_bridge_and_wrong_token(self):
        from arch_games.audio import remote

        async def go():
            cfg = make_cfg(self.tmp.name)
            bridge = Bridge(cfg)
            port = await bridge.start()
            try:
                remote.configure(self.config, "http://127.0.0.1:%d" % port, "arch64", TOKEN, allow_http=True)
                good = await asyncio.get_running_loop().run_in_executor(None, remote.status, self.config)
                remote.token_file(self.config).write_text("w" * 40 + "\n")
                bad = await asyncio.get_running_loop().run_in_executor(None, remote.status, self.config)
                return good, bad
            finally:
                await bridge.close()

        good, bad = asyncio.run(go())
        self.assertTrue(good["authenticated"] and good["vm_known"])
        self.assertEqual(good["stream"]["state"], "idle")
        self.assertFalse(bad["authenticated"])

    def test_unreachable_bridge(self):
        from arch_games.audio import remote

        remote.configure(self.config, "http://127.0.0.1:9", "arch64", "x" * 40, allow_http=True)
        result = remote.status(self.config, timeout=1)
        self.assertFalse(result["reachable"])


class ConfigPermissionTests(unittest.TestCase):
    def test_world_writable_config_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path, _ = cfgmod.init(d, {"arch64": "arch64_vm"})
            cfgmod.load(path)
            path.chmod(0o666)
            with self.assertRaises(cfgmod.ConfigError):
                cfgmod.load(path)


class HostCliTests(unittest.TestCase):
    """The host-side commands that only print instructions must actually run (one of them once crashed on a format string)."""

    def run_main(self, config, *argv):
        import io
        from contextlib import redirect_stdout
        from arch_games.remote.__main__ import main

        out = io.StringIO()
        with redirect_stdout(out):
            main(["--config", str(config), *argv])
        return out.getvalue()

    def test_print_only_commands(self):
        with tempfile.TemporaryDirectory() as d:
            path, token_path = cfgmod.init(d, {"arch64": "arch64_vm", "arch86": "arch86_vm"})
            persist = self.run_main(path, "sink", "persist", "arch64")
            self.assertIn("sink_name=arch64_vm", persist)
            self.assertIn("pipewire-pulse.conf.d/arch-games-arch64.conf", persist)
            qemu = self.run_main(path, "qemu", "arch86")
            self.assertIn("out.name=arch86_vm", qemu)
            self.assertIn("hda-output", qemu)  # output-only: no guest microphone path
            self.assertIn("arch64_vm.monitor", self.run_main(path, "sink", "listen-locally", "arch64"))
            self.assertGreaterEqual(len(token_path.read_text().strip()), 40)

    def test_unknown_vm_and_missing_config(self):
        with tempfile.TemporaryDirectory() as d:
            path, _ = cfgmod.init(d, {"arch64": "arch64_vm"})
            with self.assertRaises(SystemExit):
                self.run_main(path, "qemu", "nonexistent")
            with self.assertRaises(SystemExit):
                self.run_main(Path(d) / "missing.json", "qemu", "arch64")
