"""Serve the audio bridge with a synthetic 440 Hz tone instead of a VM, to check browser, network, token and Web Audio.

    python3 tools/audio_bridge_selftest.py [--listen 127.0.0.1] [--port 8787] [--allow-nonloopback]

Open the printed URL in the browser you want to test (a MacBook over Tailscale, for example) and click "Enable audio".
You will hear a tone only in that browser. Nothing on the host's PipeWire is touched. Press Ctrl+C to stop; the token is
random and deleted with the temporary directory.
"""

import argparse
import asyncio
import json
from pathlib import Path
import secrets
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arch_games.remote import config as cfgmod  # noqa: E402
from arch_games.remote.server import Bridge  # noqa: E402

TONE = """
import math, struct, sys, time
rate, i, start, sent = 48000, 0, time.monotonic(), 0
while True:
    out = bytearray()
    for k in range(960):
        v = int(7000 * math.sin(2 * math.pi * 440 * (i + k) / rate))
        out += struct.pack('<hh', v, v)
    i += 960
    sys.stdout.buffer.write(out); sys.stdout.buffer.flush()
    sent += 960
    delay = start + sent / rate - time.monotonic()
    if delay > 0:
        time.sleep(delay)
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--allow-nonloopback", action="store_true")
    args = parser.parse_args()
    if args.listen not in cfgmod.LOOPBACK and not args.allow_nonloopback:
        raise SystemExit("Refusing to bind {} without --allow-nonloopback".format(args.listen))
    with tempfile.TemporaryDirectory(prefix="bridge-selftest-") as tmp:
        tmp = Path(tmp)
        (tmp / "tone.py").write_text(TONE)
        token = secrets.token_urlsafe(32)
        (tmp / "token").write_text(token + "\n")
        (tmp / "token").chmod(0o600)
        (tmp / "bridge.json").write_text(json.dumps({
            "listen": args.listen, "port": args.port, "token_file": str(tmp / "token"),
            "vms": {"selftest": {"sink": "selftest_vm", "capture_command": [sys.executable, str(tmp / "tone.py")]}}}))
        (tmp / "bridge.json").chmod(0o600)
        bridge = Bridge(cfgmod.load(tmp / "bridge.json"))

        async def run():
            port = await bridge.start()
            print("Self-test bridge on {}:{}. Open: http://{}:{}/vm/selftest/#token={}".format(args.listen, port, args.listen, port, token))
            try:
                await asyncio.Event().wait()
            finally:
                await bridge.close()

        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
