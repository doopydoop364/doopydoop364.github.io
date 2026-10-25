"""Bounded, unprivileged sound test: a short sine tone played through an available player."""

import math
from pathlib import Path
import struct
import subprocess
import tempfile
import wave

from ..config import AppError
from .probe import Probe


def write_tone(path, seconds=1.5, freq=440.0, rate=22050, volume=0.25):
    seconds = max(0.2, min(float(seconds), 10.0))
    frames = bytearray()
    for i in range(int(rate * seconds)):
        envelope = min(1.0, i / (rate * 0.05), (rate * seconds - i) / (rate * 0.05))  # fade avoids clicks
        frames += struct.pack("<h", int(32767 * volume * envelope * math.sin(2 * math.pi * freq * i / rate)))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))
    return seconds


PLAYERS = {
    "pulse": [("paplay", lambda f: ["paplay", f]), ("pw-play", lambda f: ["pw-play", f])],
    "alsa": [("aplay", lambda f: ["aplay", "-q", f])],
}


def run_test(probe=None, seconds=1.5, backend="auto"):
    """Play a tone. Returns a dict; success means the player exited 0, not that sound was heard."""
    probe = probe or Probe()
    if backend not in {"auto", "pulse", "alsa"}:
        raise AppError("Backend must be auto, pulse or alsa")
    order = ["pulse", "alsa"] if backend == "auto" else [backend]
    attempts = []
    with tempfile.TemporaryDirectory(prefix="arch-games-tone-") as tmp:
        wav = Path(tmp) / "tone.wav"
        length = write_tone(wav, seconds)
        for name in order:
            for tool, build in PLAYERS[name]:
                if not probe.which(tool):
                    attempts.append({"backend": name, "tool": tool, "result": "tool not installed"})
                    continue
                try:
                    result = probe.run(build(str(wav)), capture_output=True, text=True, timeout=length + 5, env=probe.environ)
                except subprocess.TimeoutExpired:
                    attempts.append({"backend": name, "tool": tool, "result": "timed out (device or server hung)"})
                    continue
                except OSError as exc:
                    attempts.append({"backend": name, "tool": tool, "result": "could not start: " + exc.strerror})
                    continue
                if result.returncode == 0:
                    attempts.append({"backend": name, "tool": tool, "result": "played"})
                    return {"played": True, "backend": name, "tool": tool, "seconds": length, "attempts": attempts, "note": "The player finished without error. Only you can confirm that sound was audible."}
                attempts.append({"backend": name, "tool": tool, "result": "failed: " + (result.stderr or "").strip().splitlines()[-1][:200] if (result.stderr or "").strip() else "failed with status {}".format(result.returncode)})
    return {"played": False, "attempts": attempts, "note": "No player succeeded. Run 'audio doctor' to see which layer is missing."}
