"""Launch profiles: validation and pure edits of the persisted launcher record (no I/O, no shell).

A record keeps its historical top-level `arguments` / `environment` / `working_directory`: they are the profile named
"default". Extra profiles live under `profiles` as complete configurations. `extras` is reserved for future settings (wrappers,
graphics or audio presets) so new fields never need a migration."""

import copy
import re

from ..config import AppError
from .environment import environment

DEFAULT = "default"
PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,39}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
MAX_ARGS, MAX_ENV, MAX_PROFILES = 64, 64, 12

# Variables that change how every program is loaded or located. They are refused in the TUI and the web GUI, and in the CLI
# unless --allow-sensitive is given. WINEPREFIX/WINEARCH are always reserved (Arch Games manages them per game).
SENSITIVE_EXACT = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "IFS", "BASH_ENV", "ENV", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME",
    "PERL5LIB", "PERL5OPT", "RUBYOPT", "RUBYLIB", "NODE_OPTIONS", "GCONV_PATH", "LOCPATH", "NLSPATH", "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY", "SUDO_USER", "SUDO_COMMAND", "SSH_AUTH_SOCK", "GIT_SSH_COMMAND",
}
SENSITIVE_PREFIXES = ("LD_", "SUDO_")

POLICY_TEXT = """Environment overrides for a launch profile
 - Names: letters, digits and underscore, not starting with a digit. Values: no NUL or line breaks, up to 8192 characters.
 - Values are passed to the program as-is: nothing is expanded or evaluated by a shell.
 - WINEPREFIX and WINEARCH are reserved: Arch Games sets them per game.
 - Sensitive variables (PATH, HOME, SHELL, LD_*, PYTHONPATH, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS, ...) can change how programs
   are found and loaded. They are refused in the TUI and web GUI; the CLI accepts them only with --allow-sensitive.
 - Everything else, for example SDL_AUDIODRIVER, AUDIODEV, SDL_VIDEODRIVER, MESA_*, LIBGL_*, DXVK_*, is allowed.
 - Variables you do not override are inherited from the session (DISPLAY, WAYLAND_DISPLAY, XDG_*, PULSE_SERVER, ... as listed in runtime/environment.py)."""


def is_sensitive(name):
    return name in SENSITIVE_EXACT or name.startswith(SENSITIVE_PREFIXES)


def validate_env_name(name, allow_sensitive=False):
    if not isinstance(name, str) or not ENV_NAME.match(name):
        raise AppError("Invalid environment variable name: {!r}".format(str(name)[:40]))
    if name in {"WINEPREFIX", "WINEARCH"}:
        raise AppError("{} is managed by Arch Games and cannot be overridden".format(name))
    if is_sensitive(name) and not allow_sensitive:
        raise AppError("{} is a security-sensitive variable and is not allowed here (CLI: --allow-sensitive)".format(name))
    return name


def validate_env_value(value):
    if not isinstance(value, str) or "\0" in value or "\n" in value or "\r" in value or len(value) > 8192:
        raise AppError("Invalid environment value (no NUL or line breaks, at most 8192 characters)")
    return value


def validate_env(env, allow_sensitive=False):
    env = dict(env or {})
    if len(env) > MAX_ENV:
        raise AppError("Too many environment overrides")
    clean = {validate_env_name(k, allow_sensitive): validate_env_value(v) for k, v in env.items()}
    environment(clean)  # the launcher's own validation as the last word
    return clean


def validate_args(args):
    if args is not None and not isinstance(args, (list, tuple)):
        raise AppError("Launch arguments must be a list")
    args = list(args or [])
    if len(args) > MAX_ARGS or not all(isinstance(a, str) and "\0" not in a and "\n" not in a and "\r" not in a and len(a) < 4096 for a in args):
        raise AppError("Invalid launch arguments (text without NUL or line breaks, at most {})".format(MAX_ARGS))
    return args


def validate_profile_name(name):
    if not isinstance(name, str) or not PROFILE_NAME.match(name):
        raise AppError("Profile names are 1..40 letters, digits, spaces or . _ - and start with a letter or digit")
    return name


def names(record):
    return [DEFAULT] + sorted((record.get("profiles") or {}))


def default_name(record):
    chosen = record.get("default_profile") or DEFAULT
    return chosen if chosen in names(record) else DEFAULT


def get(record, name=None):
    """The configuration of one profile, merged with the shared top-level fields: what a launch will use."""
    name = name or default_name(record)
    if name not in names(record):
        raise AppError("No such launch profile: {} (profiles: {})".format(name, ", ".join(names(record))))
    own = record if name == DEFAULT else (record.get("profiles") or {})[name]
    return {
        "profile": name,
        "label": own.get("label") or name,
        "executable": own.get("executable") or record.get("executable"),
        "runner": own.get("runner") or record.get("runner"),
        "working_directory": own.get("working_directory") if name != DEFAULT else record.get("working_directory"),
        "arguments": list(own.get("arguments") or []),
        "environment": dict(own.get("environment") or {}),
        "extras": dict(own.get("extras") or {}),
    }


def _target(record, name):
    name = name or default_name(record)
    if name not in names(record):
        raise AppError("No such launch profile: {}".format(name))
    return record if name == DEFAULT else record.setdefault("profiles", {})[name]


def edit(record, profile=None, arguments=None, environment_changes=None, remove_env=(), working_directory=None, allow_sensitive=False):
    """Return a copy of the record with the profile's arguments replaced and/or environment variables set or removed."""
    record = copy.deepcopy(record)
    target = _target(record, profile)
    if arguments is not None:
        target["arguments"] = validate_args(arguments)
    env = dict(target.get("environment") or {})
    for name in remove_env or ():
        env.pop(name, None)
    env.update(validate_env(environment_changes or {}, allow_sensitive))
    if len(env) > MAX_ENV:
        raise AppError("Too many environment overrides")
    target["environment"] = env
    if working_directory is not None:
        target["working_directory"] = working_directory or None
    return record


def reset(record, profile=None):
    record = copy.deepcopy(record)
    target = _target(record, profile)
    target["arguments"], target["environment"] = [], {}
    return record


def create(record, name, copy_from=None):
    record = copy.deepcopy(record)
    validate_profile_name(name)
    if name == DEFAULT or name in (record.get("profiles") or {}):
        raise AppError("A profile named {} already exists".format(name))
    if len(record.get("profiles") or {}) >= MAX_PROFILES:
        raise AppError("Too many launch profiles")
    source = get(record, copy_from)
    record.setdefault("profiles", {})[name] = {
        "label": name, "arguments": source["arguments"], "environment": source["environment"],
        "working_directory": source["working_directory"], "extras": {},
    }
    return record


def delete(record, name):
    record = copy.deepcopy(record)
    if name == DEFAULT:
        raise AppError("The default profile cannot be deleted (use reset to clear it)")
    if name not in (record.get("profiles") or {}):
        raise AppError("No such launch profile: {}".format(name))
    del record["profiles"][name]
    if record.get("default_profile") == name:
        record["default_profile"] = DEFAULT
    return record


def set_default(record, name):
    if name not in names(record):
        raise AppError("No such launch profile: {}".format(name))
    record = copy.deepcopy(record)
    record["default_profile"] = name
    return record


def command_preview(executable, arguments, environment_overrides, runner=None):
    """Display-only rendering of what will run (`env K=V exe args`). It is never executed or passed to a shell."""
    import shlex

    parts = []
    if environment_overrides:
        parts += ["env"] + ["{}={}".format(k, shlex.quote(v)) for k, v in sorted(environment_overrides.items())]
    if runner == "wine":
        parts.append("wine")
    parts.append(shlex.quote(str(executable)))
    parts += [shlex.quote(a) for a in arguments]
    return " ".join(parts)
