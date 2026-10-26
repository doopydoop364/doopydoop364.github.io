"""`arch-games launcher ...` settings commands: view, validate, rediscover and edit arguments, environment and launch profiles."""

import argparse
import json
import sys

from .config import AppError
from .runtime import gamelaunch, profiles
from .runtime.launchers import LauncherStore
from .ui.terminal import clean

OPERATIONS = {"config", "arg", "env", "profile", "reset", "validate", "rediscover"}


def extend(lsub):
    """Add the settings sub-commands to the existing `launcher` parser."""
    config = lsub.add_parser("config", help="Show the effective launch configuration (view only; nothing is changed)")
    config.add_argument("game")
    config.add_argument("--profile")

    arg = lsub.add_parser("arg", help="Launch arguments of a profile (passed to the program as separate arguments, never via a shell)")
    asub = arg.add_subparsers(dest="launcher_sub", required=True)
    for name, text in (("add", "Append arguments, e.g.: launcher arg add openttd -s sdl"), ("set", "Replace all arguments")):
        p = asub.add_parser(name, help=text)
        p.add_argument("--profile")
        p.add_argument("game")
        p.add_argument("values", nargs=argparse.REMAINDER, help="Arguments, taken literally (dash-prefixed ones included)")
    remove = asub.add_parser("remove", help="Remove one argument by its list position (1-based) or exact text")
    remove.add_argument("--profile")
    remove.add_argument("game")
    remove.add_argument("target")
    for name in ("list", "clear"):
        p = asub.add_parser(name)
        p.add_argument("--profile")
        p.add_argument("game")

    env = lsub.add_parser("env", help="Per-game environment overrides (no shell expansion; sensitive variables are refused)")
    esub = env.add_subparsers(dest="launcher_sub", required=True)
    eset = esub.add_parser("set", help="Set NAME to VALUE")
    eset.add_argument("--profile")
    eset.add_argument("--allow-sensitive", action="store_true", help="Allow variables such as LD_PRELOAD or PATH (CLI only)")
    eset.add_argument("game")
    eset.add_argument("name")
    eset.add_argument("value")
    eunset = esub.add_parser("unset", help="Remove an override")
    eunset.add_argument("--profile")
    eunset.add_argument("game")
    eunset.add_argument("name")
    elist = esub.add_parser("list")
    elist.add_argument("--profile")
    elist.add_argument("game")
    esub.add_parser("policy", help="Explain which variables can and cannot be overridden")

    profile = lsub.add_parser("profile", help="Alternative named launch configurations and the default one")
    psub = profile.add_subparsers(dest="launcher_sub", required=True)
    psub.add_parser("list").add_argument("game")
    pshow = psub.add_parser("show")
    pshow.add_argument("game")
    pshow.add_argument("name", nargs="?")
    pcreate = psub.add_parser("create", help="New profile, copied from another (default: the current default profile)")
    pcreate.add_argument("game")
    pcreate.add_argument("name")
    pcreate.add_argument("--copy-from")
    pdelete = psub.add_parser("delete")
    pdelete.add_argument("game")
    pdelete.add_argument("name")
    pdelete.add_argument("--yes", action="store_true")
    pdefault = psub.add_parser("default", help="Choose the profile used by a plain `launch`")
    pdefault.add_argument("game")
    pdefault.add_argument("name")

    reset = lsub.add_parser("reset", help="Clear a profile's arguments and environment (asks first)")
    reset.add_argument("game")
    reset.add_argument("--profile")
    reset.add_argument("--yes", action="store_true")
    validate = lsub.add_parser("validate", help="Check a saved configuration without launching anything")
    validate.add_argument("game")
    validate.add_argument("--profile")
    rediscover = lsub.add_parser("rediscover", help="Re-find the executable (after a package upgrade); your arguments, environment and profiles are kept")
    rediscover.add_argument("game")
    rediscover.add_argument("--dir", action="append", default=[])


def _note(text):
    print(text, file=sys.stderr)  # human messages go to stderr so stdout stays machine-readable JSON


def _emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=True))


def _record(config, db, game_id):
    record = LauncherStore(config, db).get(game_id)
    if record is None:
        raise AppError("No launcher is registered for {}; run: arch-games discover {} --register 1".format(game_id, game_id))
    return record


def describe_config(config, db, game_id, profile=None, pacman=None):
    record = _record(config, db, game_id)
    cfg = profiles.get(record, profile)
    st = gamelaunch.status(config, db, game_id, pacman)
    return {
        "game_id": game_id, "title": record.get("title"), "runtime": "native Linux (no Wine)" if cfg["runner"] == "native" else cfg["runner"],
        "status": st["status"], "status_text": st["label"] + (": " + st["detail"] if st["detail"] else ""),
        "package": ({"name": record.get("package"), "version": st["version"]} if record.get("package") else None),
        "executable": cfg["executable"], "working_directory": cfg["working_directory"] or "(your home directory)" if record.get("kind") == "native-package" else cfg["working_directory"],
        "profile": cfg["profile"], "default_profile": profiles.default_name(record), "profiles": profiles.names(record),
        "arguments": cfg["arguments"], "environment": cfg["environment"],
        "command_preview": profiles.command_preview(cfg["executable"], cfg["arguments"], cfg["environment"], cfg["runner"]),
        "note": "Viewing only. Change settings with: launcher arg|env|profile ...",
    }


def run(args, config, db, helpers):
    """helpers: the CLI's interactive/choose_candidate/catalog_title/pacman_service so tests and the CLI share one path."""
    op, game = args.operation, args.game if hasattr(args, "game") else None
    pacman = helpers["pacman"]()
    if op == "config":
        return _emit(describe_config(config, db, game, args.profile, pacman))
    if op == "validate":
        report = gamelaunch.validate(config, db, game, args.profile, pacman)
        for c in report["checks"]:
            print(clean("[{}] {}: {}".format("ok" if c["ok"] else "FAIL", c["check"], c["detail"])))
        if report["preview"]:
            print("Would run: " + clean(report["preview"]))
        if not report["ok"]:
            raise AppError("The configuration is not launch-ready")
        return
    if op == "arg":
        return _args(args, config, db)
    if op == "env":
        return _env(args, config, db)
    if op == "profile":
        return _profile(args, config, db)
    if op == "reset":
        if not args.yes and (not helpers["interactive"](args) or input("Clear arguments and environment of profile '{}' for {}? [y/N] ".format(args.profile or profiles.default_name(_record(config, db, game)), clean(game))).strip().lower() != "y"):
            if not helpers["interactive"](args):
                raise AppError("Resetting clears saved settings; add --yes to confirm")
            return
        record = gamelaunch.edit(config, db, game, lambda r: profiles.reset(r, args.profile))
        _note("Profile '{}' reset (executable and other profiles untouched)".format(args.profile or profiles.default_name(record)))
        return
    if op == "rediscover":
        return rediscover(args, config, db, helpers, pacman)
    raise AppError("Unknown launcher operation")


def _args(args, config, db):
    sub, game = args.launcher_sub, args.game
    record = _record(config, db, game)
    current = profiles.get(record, args.profile)["arguments"]
    if sub == "list":
        return _emit({"profile": profiles.get(record, args.profile)["profile"], "arguments": current})
    if sub == "add":
        values = list(args.values)
        if not values:
            raise AppError("Give at least one argument, e.g.: launcher arg add {} -s sdl".format(game))
        new = current + values
    elif sub == "set":
        new = list(args.values)
    elif sub == "clear":
        new = []
    else:  # remove
        if args.target.isdigit() and 1 <= int(args.target) <= len(current):
            new = current[: int(args.target) - 1] + current[int(args.target):]
        elif args.target in current:
            index = current.index(args.target)
            new = current[:index] + current[index + 1:]
        else:
            raise AppError("No such argument: {} (positions are 1-{})".format(args.target, len(current)))
    updated = gamelaunch.edit(config, db, game, lambda r: profiles.edit(r, args.profile, arguments=new))
    _emit({"profile": profiles.get(updated, args.profile)["profile"], "arguments": profiles.get(updated, args.profile)["arguments"]})


def _env(args, config, db):
    sub = args.launcher_sub
    if sub == "policy":
        print(profiles.POLICY_TEXT)
        return
    game = args.game
    record = _record(config, db, game)
    cfg = profiles.get(record, args.profile)
    if sub == "list":
        return _emit({"profile": cfg["profile"], "environment": cfg["environment"], "note": "These are overrides; unlisted variables are inherited from the session."})
    if sub == "set":
        updated = gamelaunch.edit(config, db, game, lambda r: profiles.edit(r, args.profile, environment_changes={args.name: args.value}, allow_sensitive=args.allow_sensitive))
    else:
        if args.name not in cfg["environment"]:
            raise AppError("{} is not set in profile {}".format(args.name, cfg["profile"]))
        updated = gamelaunch.edit(config, db, game, lambda r: profiles.edit(r, args.profile, remove_env=[args.name]))
    _emit({"profile": cfg["profile"], "environment": profiles.get(updated, args.profile)["environment"]})


def _profile(args, config, db):
    sub, game = args.launcher_sub, args.game
    record = _record(config, db, game)
    if sub == "list":
        return _emit({"default": profiles.default_name(record), "profiles": [{"name": n, "arguments": profiles.get(record, n)["arguments"], "environment": profiles.get(record, n)["environment"]} for n in profiles.names(record)]})
    if sub == "show":
        return _emit(profiles.get(record, args.name))
    if sub == "create":
        updated = gamelaunch.edit(config, db, game, lambda r: profiles.create(r, args.name, args.copy_from))
        _note("Created profile '{}' (copied from '{}')".format(args.name, args.copy_from or profiles.default_name(record)))
    elif sub == "delete":
        if not args.yes:
            raise AppError("Deleting a profile removes its saved arguments and environment; add --yes to confirm")
        updated = gamelaunch.edit(config, db, game, lambda r: profiles.delete(r, args.name))
        _note("Deleted profile '{}'".format(args.name))
    else:
        updated = gamelaunch.edit(config, db, game, lambda r: profiles.set_default(r, args.name))
        _note("Default profile for {} is now '{}'".format(game, args.name))
    return _emit({"default": profiles.default_name(updated), "profiles": profiles.names(updated)})


def rediscover(args, config, db, helpers, pacman):
    record = _record(config, db, args.game)
    title = helpers["catalog_title"](config, db, args.game)
    result = gamelaunch.discover(config, db, args.game, title, pacman, getattr(args, "dir", ()))
    if result["runner"] == "native" and not result["installed"]:
        raise AppError("Package {} is not installed, so there is nothing to rediscover. Reinstall it with: arch-games install {}".format(result["package"], args.game))
    candidates = result["candidates"]
    if not candidates:
        raise AppError("No launchable program was found; your saved settings were left untouched")
    chosen = candidates[0] if result["confidence"] == "high" else helpers["choose_candidate"](candidates, args)
    updated = gamelaunch.register(config, db, args.game, title, chosen, pacman)
    cfg = profiles.get(updated)
    _emit({"executable": {"before": record.get("executable"), "after": updated["executable"]}, "package_version": updated.get("package_version"),
           "settings_kept": {"arguments": len(cfg["arguments"]), "environment": len(cfg["environment"]), "profiles": profiles.names(updated)}})
