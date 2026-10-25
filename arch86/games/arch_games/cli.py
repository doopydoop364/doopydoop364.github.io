"""Explicit user operations; metadata never becomes executable configuration."""
import argparse
import getpass
import json
from pathlib import Path
import sys

from . import __version__
from .config import AppError, Config, server_url
from .gamevault.client import Client
from .storage.database import Database
from .ui.terminal import clean


def emit(value):
    # JSON escaping also prevents terminal escape injection from remote metadata.
    print(json.dumps(value, indent=2, ensure_ascii=True))


def catalog_options(parser):
    parser.add_argument("--source", choices=["all", "pacman", "gamevault"], default="all")
    parser.add_argument("--search", default="")
    parser.add_argument("--category", default="")
    for name in ["all", "compatible", "installed", "wine", "verified", "offline", "json"]:
        parser.add_argument("--" + name, action="store_true")
    parser.add_argument("--sort", choices=["title", "source", "category", "status"], default="title")


def make_parser():
    parser = argparse.ArgumentParser(prog="arch-games", description="Local-first Arch Linux game library")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--state-dir", help="Isolated configuration/data/cache root")
    sub = parser.add_subparsers(dest="action")
    menu = sub.add_parser("menu", help="Keyboard terminal interface")
    menu.add_argument("--plain", action="store_true")
    catalog_options(menu)
    listing = sub.add_parser("list", help="Search the combined catalog")
    catalog_options(listing)
    sub.add_parser("info", help="Measured system profile (JSON)")
    details = sub.add_parser("details", help="Game details and compatibility report")
    details.add_argument("game")
    details.add_argument("--offline", action="store_true")
    reports = sub.add_parser("reports", help="Compatibility reports, including unknown entries")
    catalog_options(reports)
    analyze = sub.add_parser("analyze", help="Inspect a local executable/archive without running it")
    analyze.add_argument("path")
    analyze.add_argument("--game", help="Save evidence against this catalog ID")
    analyze.add_argument("--member", help="Explicit archive executable member to use as evidence")
    analyze.add_argument("--graphics", choices=["2d", "3d", "unknown"], default="unknown")
    analyze.add_argument("--ram-mb", type=int)
    analyze.add_argument("--vulkan", action="store_true")
    analyze.add_argument("--terminal", action="store_true", help="User assertion: game needs no graphical session")
    web = sub.add_parser("web", help="Read-only localhost gallery")
    web.add_argument("--port", type=int, default=8765)
    catalog_options(web)
    install = sub.add_parser("install", help="Install a Pacman game or extract a local game archive")
    install.add_argument("game")
    install.add_argument("--archive")
    install.add_argument("--title")
    install.add_argument("--yes", action="store_true")
    install.add_argument("--reinstall", action="store_true")
    install.add_argument("--timeout", type=int, default=600)
    uninstall = sub.add_parser("uninstall", help="Remove a managed game; archives/prefixes retained")
    uninstall.add_argument("game")
    uninstall.add_argument("--yes", action="store_true")
    installed = sub.add_parser("installed", help="Show installed games")
    catalog_options(installed)
    gv = sub.add_parser("gamevault", help="Connect and browse GameVault")
    gvsub = gv.add_subparsers(dest="operation", required=True)
    connect = gvsub.add_parser("connect")
    connect.add_argument("--url")
    connect.add_argument("--allow-http", action="store_true")
    connect.add_argument("--api-key", action="store_true")
    for name in ["status", "disconnect"]:
        gvsub.add_parser(name)
    for name in ["list", "search", "details"]:
        cmd = gvsub.add_parser(name)
        cmd.add_argument("--offline", action="store_true")
        if name == "search":
            cmd.add_argument("query")
        if name == "details":
            cmd.add_argument("id")
    settings = sub.add_parser("settings", help="Show or change local preferences")
    settings.add_argument("operation", choices=["show", "set"], nargs="?", default="show")
    settings.add_argument("key", nargs="?")
    settings.add_argument("value", nargs="?")
    return parser


def filters(args):
    return {"source": args.source, "search": args.search, "category": args.category,
            "show_all": args.all, "installed": args.installed, "wine": args.wine,
            "verified": args.verified, "compatible": args.compatible, "sort": args.sort}


def gamevault_command(args, config, db):
    if args.operation == "disconnect":
        config.disconnect()
        print("Local credentials removed; existing server sessions expire by server policy.")
        return
    if args.operation == "connect":
        value = args.url or input("GameVault server URL: ").strip()
        config.values.update(server_url=server_url(value, args.allow_http), allow_http=args.allow_http)
        config.values["account"] = input("Account name (cache identity): ").strip()
        client = Client(config, db)
        if args.api_key:
            key = getpass.getpass("API key: ")
            if not key:
                raise AppError("API key cannot be empty")
            previous = config.credentials()
            config.save_credentials({"api_key": key})
            try:
                client.status()
            except AppError:
                config.save_credentials(previous)
                raise
        else:
            client.login(config.values["account"], getpass.getpass("Password: "))
        config.save()
        print("Connected; credentials saved privately.")
        return
    client = Client(config, db)
    if args.operation == "status":
        emit(client.status())
    elif args.operation in {"list", "search"}:
        games, state = client.library(args.offline)
        emit(state)
        term = args.query.casefold() if args.operation == "search" else ""
        for game in games:
            if term in (game.title + " " + game.description).casefold():
                print(clean(game.id + "\t" + game.title))
    elif args.operation == "details":
        game, cached = client.details(args.id, args.offline)
        emit({"game": game.to_dict(), "cached": cached})


def analyze_command(args, config, db):
    from .compatibility.executables import inspect_file
    from .compatibility.archives import inspect_archive
    from .compatibility.analyzer import analyze
    from .models import Game
    from .platform import system_profile
    path = Path(args.path)
    if not path.is_file():
        raise AppError("Analysis path is not a regular file")
    binary = inspect_file(path).to_dict()
    archive = None
    if binary["format"] == "unknown":
        archive = inspect_archive(path)
        if args.member:
            record = next((r for r in archive["entries"] if r["name"] == args.member and r.get("executable")), None)
            if not record:
                raise AppError("Selected archive member has no recognized executable header")
            binary = record["executable"]
    requirements = {"graphics": args.graphics}
    if args.graphics == "3d":
        requirements["acceleration"] = True
    if args.ram_mb is not None:
        if args.ram_mb <= 0:
            raise AppError("RAM requirement must be positive")
        requirements["ram_bytes"] = args.ram_mb * 1024**2
    if args.vulkan:
        requirements["vulkan"] = True
    if args.terminal:
        requirements["display"] = False
    if args.game and binary["format"] == "unknown":
        raise AppError("Select a recognized executable with --member before saving archive evidence")
    game = Game(args.game or "local:analysis", path.name, "local")
    value = {"executable": binary, "requirements": requirements}
    if args.game:
        from .downloads.verification import sha256
        value.update(archive_sha256=sha256(path), source_path=str(path.resolve()), member=args.member)
        db.put("analysis", args.game, value)
    emit({"report": analyze(game, system_profile(config.data_dir), binary, requirements).to_dict(),
          "executable": binary, "archive": archive})


def settings_command(args, config):
    if args.operation == "show":
        emit(config.values)
        return
    allowed = {"show_all", "wine_win32", "wine_win64", "wine_wow64"}
    if args.key not in allowed or args.value not in {"true", "false", "unknown"}:
        raise AppError("Settings keys: show_all/wine_win32/wine_win64/wine_wow64; values: true/false/unknown")
    config.values[args.key] = {"true": True, "false": False, "unknown": None}[args.value]
    config.save()
    print("Saved. Wine capability overrides are user assertions, not measured results.")


def dispatch(args, config, db):
    from .catalog.library import Library
    from .platform import system_profile
    action = args.action or "menu"
    if action == "gamevault":
        return gamevault_command(args, config, db)
    if action == "info":
        return emit(system_profile(config.data_dir))
    if action == "settings":
        return settings_command(args, config)
    if action == "analyze":
        return analyze_command(args, config, db)
    library = Library(config, db).load(getattr(args, "source", "all"), getattr(args, "offline", False))
    for warning in library.warnings:
        print("Notice: " + clean(warning), file=sys.stderr)
    if action in {"list", "reports", "installed"}:
        selected = filters(args)
        selected["show_all"] = args.all or action in {"reports", "installed"} or bool(config.values.get("show_all"))
        selected["installed"] = args.installed or action == "installed"
        games, explanation = library.filtered(**selected)
        print(clean(explanation), file=sys.stderr)
        if args.json or action == "reports":
            return emit({"games": [g.to_dict() for g in games], "reports": {g.id: library.reports[g.id] for g in games}, "sync": library.sync})
        for game in games:
            print(clean("{} | {} | {} | {}".format(game.package or game.id, game.title,
                                                  "installed" if game.installed else "available", library.reports[game.id]["status"])))
    elif action == "details":
        game = library.find(args.game)
        emit({"game": game.to_dict(), "compatibility": library.reports[game.id], "sync": library.sync})
    elif action == "menu":
        from .ui.terminal import menu
        menu(library, lambda argv: main((["--state-dir", args.state_dir] if args.state_dir else []) + argv), getattr(args, "plain", False))
    elif action == "install":
        game = library.find(args.game)
        if game.source != "pacman":
            raise AppError("GameVault installation requires --archive after an explicit download")
        if game.installed:
            print("Already installed")
            return
        if args.yes or input("Install {} using Pacman? [y/N] ".format(clean(game.package))).lower() == "y":
            import subprocess
            result = subprocess.run(["sudo", "pacman", "-S", "--needed", "--", game.package])
            if result.returncode:
                raise AppError("Pacman installation failed")
    elif action == "web":
        from .ui.gallery import serve
        games, explanation = library.filtered(**filters(args))
        serve(library, games, explanation, config, args.port)
    else:
        raise AppError("Operation not implemented")


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.action is None:
        args = parser.parse_args((["--state-dir", args.state_dir] if args.state_dir else []) + ["menu"])
    db = None
    try:
        config = Config(args.state_dir)
        db = Database(config.data_dir / "state.db")
        dispatch(args, config, db)
        return 0
    except (AppError, OSError, ValueError) as exc:
        print("Error: " + clean(str(exc)), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 130
    finally:
        if db:
            db.close()
