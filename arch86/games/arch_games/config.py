"""Private XDG storage and atomic configuration writes."""

import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit


class AppError(Exception):
    """A safe message for interactive users (must not contain remote response bodies)."""


def private_dir(path):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise AppError("Application directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise AppError("Application directory must be owned by this user")
    path.chmod(0o700)
    return path


def atomic_json(path, value):
    path = Path(path)
    private_dir(path.parent)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path, default=None, private=False):
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    if path.is_symlink() or (private and path.stat().st_mode & 0o077):
        raise AppError("Private state file has unsafe permissions or is a symlink")
    if path.stat().st_size > 16 * 1024 * 1024:
        raise AppError("State file exceeds size limit")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise AppError("Invalid state file; restore a backup or repair configuration") from None


def server_url(value, allow_http=False):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(ord(c) < 33 for c in value)
    ):
        raise AppError("Server URL must be HTTP(S), without credentials, queries or fragments")
    if parsed.scheme == "http" and not allow_http:
        raise AppError("HTTP requires --allow-http (only use on a trusted private network)")
    try:
        parsed.port
    except ValueError:
        raise AppError("Invalid server port") from None
    return value.rstrip("/")


class Config:
    def __init__(self, state_dir=None):
        if state_dir:
            root = private_dir(state_dir)
            self.config_dir, self.data_dir, self.cache_dir = root / "config", root / "data", root / "cache"
        else:
            home = Path.home()
            self.config_dir = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "arch-games"
            self.data_dir = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "arch-games"
            self.cache_dir = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache")) / "arch-games"
        for path in [self.config_dir, self.data_dir, self.cache_dir]:
            private_dir(path)
        self.path = self.config_dir / "settings.json"
        self.secrets = self.config_dir / "credentials.json"
        self.values = read_json(self.path)
        if not isinstance(self.values, dict):
            raise AppError("Settings must be a JSON object")
        for key in ["allow_http", "show_all", "wine_win32", "wine_win64", "wine_wow64", "performance_monitoring"]:
            if key in self.values and self.values[key] is not None and not isinstance(self.values[key], bool):
                raise AppError("Boolean setting has an invalid type: " + key)
        for key in ["server_url", "account"]:
            if key in self.values and not isinstance(self.values[key], str):
                raise AppError("Text setting has an invalid type: " + key)

    def save(self):
        atomic_json(self.path, self.values)

    def credentials(self):
        result = read_json(self.secrets, private=True)
        if not isinstance(result, dict):
            raise AppError("Invalid credentials file")
        return result

    def save_credentials(self, value):
        value = dict(value)
        value.setdefault("_server_url", self.values.get("server_url"))
        value.setdefault("_account", self.values.get("account", ""))
        atomic_json(self.secrets, value)

    def disconnect(self):
        self.secrets.unlink(missing_ok=True)

    def directory(self, name):
        if name not in {"downloads", "installed", "prefixes", "backups", "logs"}:
            raise AppError("Unknown application directory")
        return private_dir(self.data_dir / name)
