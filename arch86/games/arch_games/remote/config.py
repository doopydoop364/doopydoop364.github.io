"""Bridge configuration: validated JSON, a private token file, strict capture-source rules."""

import json
import os
from pathlib import Path
import re
import secrets
import stat

SOURCE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class ConfigError(Exception):
    pass


def default_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "arch-games-audio-bridge"


def validate_source(name):
    """Only a dedicated sink monitor may be captured: never a microphone, never the host's default."""
    if not isinstance(name, str) or not SOURCE_RE.match(name) or name.startswith("@") or name.startswith("-"):
        raise ConfigError("Invalid capture source name")
    if not name.endswith(".monitor"):
        raise ConfigError("Capture source must be a sink monitor (ending .monitor); microphones are refused")
    return name


def validate_vm(name, vm):
    if not NAME_RE.match(name):
        raise ConfigError("VM names are lowercase letters, digits, - and _ (max 32)")
    sink = vm.get("sink")
    if not isinstance(sink, str) or not SOURCE_RE.match(sink) or sink.startswith("@"):
        raise ConfigError("VM '{}' needs a dedicated sink name".format(name))
    source = validate_source(vm.get("source") or sink + ".monitor")
    rate, channels = int(vm.get("rate", 48000)), int(vm.get("channels", 2))
    if rate not in (8000, 16000, 22050, 24000, 44100, 48000) or channels not in (1, 2):
        raise ConfigError("Unsupported rate/channels")
    latency = int(vm.get("latency_ms", 40))
    if not 10 <= latency <= 500:
        raise ConfigError("latency_ms must be 10..500")
    return {"sink": sink, "source": source, "rate": rate, "channels": channels, "latency_ms": latency,
            "capture_command": vm.get("capture_command")}


def load(path):
    path = Path(path)
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ConfigError("Cannot read bridge config {}: {}".format(path, exc)) from None
    if not isinstance(raw, dict) or not isinstance(raw.get("vms"), dict) or not raw["vms"]:
        raise ConfigError("Config needs a non-empty 'vms' object")
    sources = set()
    vms = {}
    for name, vm in raw["vms"].items():
        vms[name] = validate_vm(name, vm)
        if vms[name]["source"] in sources:
            raise ConfigError("Two VMs may not share a capture source (audio isolation)")
        sources.add(vms[name]["source"])
    listen = raw.get("listen", "127.0.0.1")
    port = int(raw.get("port", 8787))
    if not 1 <= port <= 65535 and port != 0:
        raise ConfigError("Invalid port")
    token_file = Path(raw.get("token_file") or path.parent / "token")
    return {
        "listen": listen, "port": port, "vms": vms, "token_file": token_file,
        "novnc_url": raw.get("novnc_url"), "allow_origins": list(raw.get("allow_origins") or []),
        "max_clients_per_vm": int(raw.get("max_clients_per_vm", 4)),
    }


def read_token(path):
    path = Path(path)
    try:
        info = path.lstat()
    except OSError:
        raise ConfigError("Token file missing: run 'init' first") from None
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ConfigError("Token file must be a regular file owned by you with mode 0600")
    token = path.read_text().strip()
    if len(token) < 24:
        raise ConfigError("Token is too short")
    return token


def init(directory, vms, force=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path, token_path = directory / "bridge.json", directory / "token"
    if config_path.exists() and not force:
        raise ConfigError("Config already exists; use --force to rewrite (token is kept)")
    if not token_path.exists():
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(32) + "\n")
    body = {"listen": "127.0.0.1", "port": 8787, "token_file": str(token_path), "novnc_url": None, "allow_origins": [],
            "vms": {name: {"sink": sink, "rate": 48000, "channels": 2, "latency_ms": 40} for name, sink in vms.items()}}
    config_path.write_text(json.dumps(body, indent=2) + "\n")
    config_path.chmod(0o600)
    return config_path, token_path
