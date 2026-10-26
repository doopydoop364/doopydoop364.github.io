"""Read-only audio facility probes. Each layer is measured separately and never conflated:

1. guest device (kernel/ALSA)   2. guest audio server (PipeWire/PulseAudio)   3. Wine driver choice
4. session environment          5. virtualization context (host routing and browser delivery are outside the guest)
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shutil
import subprocess


@dataclass
class Probe:
    root: Path = Path("/")
    run: object = subprocess.run
    which: object = shutil.which
    environ: dict = field(default_factory=lambda: dict(os.environ))

    def path(self, absolute):
        return self.root / str(absolute).lstrip("/")

    def read(self, absolute, limit=65536):
        try:
            with self.path(absolute).open("r", encoding="utf-8", errors="replace") as stream:
                return stream.read(limit)
        except OSError:
            return None

    def exec(self, argv, timeout=5):
        """Run a read-only tool; (ok, stdout). Missing tool and failure are distinguishable via None."""
        if not self.which(argv[0]):
            return None, ""
        if self.run is subprocess.run and os.environ.get("ARCH_GAMES_NO_PROBE"):
            return None, ""  # test-suite guard: never execute real system tools
        try:
            result = self.run(argv, capture_output=True, text=True, timeout=timeout, env=self.environ)
        except (OSError, subprocess.TimeoutExpired):
            return False, ""
        return result.returncode == 0, (result.stdout or "")[:65536]


def virtualization(probe):
    """Detect a QEMU/KVM (or other) guest from DMI and CPU flags; evidence only."""
    vendor = (probe.read("/sys/class/dmi/id/sys_vendor") or "").strip()
    product = (probe.read("/sys/class/dmi/id/product_name") or "").strip()
    cpuinfo = probe.read("/proc/cpuinfo", 262144) or ""
    ok, detect = probe.exec(["systemd-detect-virt"])
    kind = detect.strip() if ok else ""
    hypervisor_flag = bool(re.search(r"^flags\s*:.*\bhypervisor\b", cpuinfo, re.M))
    qemu = "qemu" in (vendor + product).lower() or kind in {"kvm", "qemu"}
    return {
        "virtualized": bool(kind and kind != "none") or hypervisor_flag or qemu,
        "qemu": qemu,
        "kind": kind or ("hypervisor flag" if hypervisor_flag else "none detected"),
        "dmi": (vendor + " " + product).strip(),
    }


def alsa_cards(probe):
    text = probe.read("/proc/asound/cards")
    cards = []
    if text:
        for line in text.splitlines():
            m = re.match(r"^\s*(\d+)\s+\[(\S+)\s*\]:\s*(.*)$", line)
            if m:
                cards.append({"index": int(m.group(1)), "id": m.group(2), "name": m.group(3).strip()})
    nodes = []
    snd = probe.path("/dev/snd")
    try:
        nodes = sorted(e.name for e in os.scandir(snd))
    except OSError:
        pass
    accessible = None
    if nodes:
        accessible = any(os.access(snd / n, os.R_OK | os.W_OK) for n in nodes if n.startswith("pcm") or n.startswith("control"))
    pci = probe.exec(["lspci", "-nn"])[1] if probe.which("lspci") else ""
    hardware = [line.strip() for line in pci.splitlines() if "audio" in line.lower() or "multimedia" in line.lower()]
    return {"cards": cards, "device_nodes": nodes, "nodes_accessible": accessible, "pci_audio": hardware}


def alsa_playback(probe):
    ok, out = probe.exec(["aplay", "-l"])
    devices = [line.strip() for line in out.splitlines() if line.startswith("card ")]
    return {"tool": ok is not None, "devices": devices}


def server_info(probe):
    """PulseAudio-compatible server state (covers pipewire-pulse). Socket presence and a live query are separate."""
    runtime = probe.environ.get("XDG_RUNTIME_DIR", "/run/user/{}".format(os.getuid()))
    pulse_server = probe.environ.get("PULSE_SERVER", "")
    sockets = {
        "pulse": probe.path(Path(runtime) / "pulse" / "native").exists(),
        "pipewire": probe.path(Path(runtime) / "pipewire-0").exists(),
    }
    result = {"pulse_server_env": pulse_server or None, "sockets": sockets, "tools": {t: bool(probe.which(t)) for t in ("pactl", "wpctl", "pw-cli", "paplay", "aplay", "pw-play", "speaker-test")}}
    ok, info = probe.exec(["pactl", "info"])
    result["reachable"] = ok if ok is not None else None
    result["server_name"] = None
    result["default_sink"] = None
    result["sinks"] = []
    result["default_sink_muted"] = None
    if ok:
        for line in info.splitlines():
            if line.startswith("Server Name:"):
                result["server_name"] = line.split(":", 1)[1].strip()
            if line.startswith("Default Sink:"):
                result["default_sink"] = line.split(":", 1)[1].strip() or None
        okl, sinks = probe.exec(["pactl", "list", "short", "sinks"])
        if okl:
            for line in sinks.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    result["sinks"].append({"id": parts[0], "name": parts[1], "state": parts[-1]})
        if result["default_sink"]:
            okm, mute = probe.exec(["pactl", "get-sink-mute", result["default_sink"]])
            if okm:
                result["default_sink_muted"] = "yes" in mute.lower()
    return result


def wine_driver(probe, user_reg_paths):
    """Audio driver selected in each given prefix user.reg ([Software\\\\Wine\\\\Drivers] "Audio")."""
    found = {}
    for label, path in user_reg_paths.items():
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")[: 8 * 1024 * 1024]
        except OSError:
            continue
        section = re.search(r"\[Software\\\\Wine\\\\Drivers\][^\n]*\n(.*?)(?:\n\[|\Z)", text, re.S)
        m = re.search(r'"Audio"="([^"]*)"', section.group(1)) if section else None
        found[label] = m.group(1) if m else None
    return found
