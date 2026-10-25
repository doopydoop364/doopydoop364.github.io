"""Explicit user operations; metadata never becomes executable configuration."""

import argparse
import getpass
import json
import sqlite3
from pathlib import Path
import sys
import warnings

from . import __version__
from .config import AppError, Config, server_url
from .gamevault.client import Client
from .storage.database import Database
from .ui.terminal import clean
from .compatibility.archive_errors import ArchiveError


def secret_prompt(prompt):
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning:
            raise AppError(
                "Secure password input requires a terminal; reconnect from KMSCON, SSH or a terminal emulator"
            ) from None


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
    parser.add_argument("--debug", action="store_true", help="Show redacted archive-worker diagnostics")
    sub = parser.add_subparsers(dest="action")
    menu = sub.add_parser("menu", help="Keyboard terminal interface")
    menu.add_argument("--plain", action="store_true")
    catalog_options(menu)
    listing = sub.add_parser("list", help="Search the combined catalog")
    catalog_options(listing)
    sub.add_parser("doctor", help="Application integrity and optional prerequisites")
    sub.add_parser("info", help="Measured system profile (JSON)")
    details = sub.add_parser("details", help="Game details and compatibility report")
    details.add_argument("game")
    details.add_argument("--offline", action="store_true")
    reports = sub.add_parser("reports", help="Compatibility reports, including unknown entries")
    catalog_options(reports)
    analyze = sub.add_parser("analyze", help="Inspect a local executable/archive without running it")
    analyze.add_argument("path")
    analyze.add_argument(
        "--debug",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Show redacted archive-worker diagnostics",
    )
    analyze.add_argument("--game", help="Save evidence against this catalog ID")
    analyze.add_argument("--member", help="Explicit archive executable member to use as evidence")
    analyze.add_argument("--graphics", choices=["2d", "3d", "unknown"], default="unknown")
    analyze.add_argument("--ram-mb", type=int)
    analyze.add_argument("--vulkan", action="store_true")
    analyze.add_argument(
        "--terminal", action="store_true", help="User assertion: game needs no graphical session"
    )
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
    uninstall.add_argument(
        "--purge", action="store_true", help="Delete extracted files instead of retaining a backup"
    )
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
    download = sub.add_parser("download", help="Persistent authorized GameVault download queue")
    dsub = download.add_subparsers(dest="operation", required=True)
    add = dsub.add_parser("add")
    add.add_argument("game")
    add.add_argument("--sha256")
    add.add_argument("--max-gb", type=int, default=32)
    add.add_argument("--offline", action="store_true")
    dsub.add_parser("run")
    dsub.add_parser("list")
    cancel = dsub.add_parser("cancel")
    cancel.add_argument("id")
    cancel.add_argument("--cleanup", action="store_true")
    retry = dsub.add_parser("retry")
    retry.add_argument("id")
    retry.add_argument("--restart", action="store_true")
    prefix = sub.add_parser("prefix", help="Isolated per-game Wine environments")
    psub = prefix.add_subparsers(dest="operation", required=True)
    psub.add_parser("list")
    for name in ["create", "check", "backup", "remove", "recover"]:
        cmd = psub.add_parser(name)
        cmd.add_argument("game")
        if name == "create":
            cmd.add_argument("--arch", choices=["win32", "win64"], default="win64")
        if name in {"create", "recover"}:
            cmd.add_argument(
                "--timeout",
                type=int,
                default=120 if name == "create" else 5,
                help="Initialization deadline (after version probe), default 120 seconds"
                if name == "create"
                else "Wait for stable existing prefix files, default 5 seconds",
            )
        if name in {"create", "remove"}:
            cmd.add_argument("--yes", action="store_true")
        if name == "remove":
            cmd.add_argument("--purge", action="store_true")
    launch = sub.add_parser("launch", help="Explicitly launch a selected installed executable")
    launch.add_argument("game")
    launch.add_argument("executable")
    launch.add_argument("--method", choices=["auto", "native", "wine", "dosbox", "custom"], default="auto")
    launch.add_argument("--prefix")
    launch.add_argument("--arg", action="append", default=[])
    launch.add_argument("--env", action="append", default=[])
    launch.add_argument(
        "--command", help="Explicit JSON argument array for custom method; {exe} expands to the path"
    )
    launch.add_argument("--allow-external", action="store_true")
    launch.add_argument("--dry-run", action="store_true")
    launch.add_argument(
        "--interactive",
        action="store_true",
        help="Attach terminal input/output instead of capturing game output",
    )
    sub.add_parser("history", help="Local launch history")
    feedback = sub.add_parser("feedback", help="Local user compatibility results; never uploaded")
    fsub = feedback.add_subparsers(dest="operation", required=True)
    fsub.add_parser("list")
    save = fsub.add_parser("set")
    save.add_argument("game")
    save.add_argument("result", choices=["works", "issues", "slow", "fails", "untested"])
    save.add_argument("--notes", default="")
    save.add_argument("--launch")
    save.add_argument("--manual", action="store_true")
    catalog = sub.add_parser("catalog", help="Explicit local catalog identity assertions")
    csub = catalog.add_subparsers(dest="operation", required=True)
    identify = csub.add_parser("identify")
    identify.add_argument("game")
    identify.add_argument("--canonical", required=True)
    identify.add_argument("--build", required=True)
    identify.add_argument("--edition", default="")
    forget = csub.add_parser("forget")
    forget.add_argument("game")
    settings = sub.add_parser("settings", help="Show or change local preferences")
    settings.add_argument("operation", choices=["show", "set"], nargs="?", default="show")
    settings.add_argument("key", nargs="?")
    settings.add_argument("value", nargs="?")
    return parser


def filters(args):
    return {
        "source": args.source,
        "search": args.search,
        "category": args.category,
        "show_all": args.all,
        "installed": args.installed,
        "wine": args.wine,
        "verified": args.verified,
        "compatible": args.compatible,
        "sort": args.sort,
    }


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
            key = secret_prompt("API key: ")
            if not key:
                raise AppError("API key cannot be empty")
            previous = config.credentials()
            config.save_credentials({"api_key": key})
            try:
                client.status()
            except AppError:
                if previous:
                    config.save_credentials(previous)
                else:
                    config.disconnect()
                raise
        else:
            client.login(config.values["account"], secret_prompt("Password: "))
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
                print(clean(game.id) + " | " + clean(game.title))
    elif args.operation == "details":
        game, cached = client.details(args.id, args.offline)
        emit({"game": game.to_dict(), "cached": cached})


def analyze_command(args, config, db):
    from .compatibility.executables import inspect_file
    from .compatibility.archives import inspect_archive
    from .compatibility.analyzer import Report, analyze, fingerprint
    from .models import Game
    from .platform import apply_overrides, system_profile

    path = Path(args.path)
    if not path.is_file():
        raise AppError("Analysis path is not a regular file")
    binary = inspect_file(path).to_dict()
    archive = None
    if binary["format"] == "unknown" and path.name.lower().endswith(
        (".zip", ".tar", ".tar.gz", ".tgz", ".7z", ".rar")
    ):
        archive = inspect_archive(path, member=args.member)
        if args.member:
            selected = archive["selected_member"]
            record = next(
                (r for r in archive["entries"] if r["name"] == selected and r.get("executable")), None
            )
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

        value.update(
            archive_sha256=sha256(path),
            source_path=str(path.resolve()),
            member=archive["selected_member"] if archive else args.member,
        )
        previous = db.get("analysis", args.game, {})
        if binary.get("role") == "installer":
            # Installer observations must not replace existing game executable evidence.
            saved = dict(previous, installer_analysis=dict(value, evidence_scope="installer"))
        else:
            saved = value
            if previous.get("installer_analysis"):
                saved["installer_analysis"] = previous["installer_analysis"]
        db.put("analysis", args.game, saved)
    profile = apply_overrides(system_profile(config.data_dir), config)
    if archive is not None and not args.member:
        report = Report(
            evidence_scope="archive_listing",
            fingerprint=fingerprint(profile),
            evidence=["Archive listing only; no executable was selected"],
            uncertainties=[
                "Select a member to inspect its executable header",
                "Installer architecture does not establish installed game architecture",
            ],
        )
    else:
        report = analyze(game, profile, binary, requirements)
    emit(
        {
            "report": report.to_dict(),
            "executable": binary,
            "evidence_scope": report.evidence_scope
            if archive
            else "installer"
            if binary.get("role") == "installer"
            else "executable",
            "archive": archive,
        }
    )


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
    if action == "doctor":
        import ctypes.util
        import shutil
        from .downloads.verification import sha256

        marker = Path(__file__).parents[1] / ".installed.json"
        integrity = "Source checkout; no installation manifest"
        if marker.is_file():
            manifest = json.loads(marker.read_text())
            damaged = [
                name
                for name, digest in manifest["files"].items()
                if not (marker.parent / name).is_file() or sha256(marker.parent / name) != digest
            ]
            if damaged:
                raise AppError("Installed files failed integrity check: " + ", ".join(damaged))
            integrity = "All installed file hashes match"
        return emit(
            {
                "integrity": integrity,
                "python": sys.version.split()[0],
                "optional": {
                    name: bool(shutil.which(name))
                    for name in [
                        "pacman",
                        "wine",
                        "wineboot",
                        "wineserver",
                        "dosbox",
                        "glxinfo",
                        "vulkaninfo",
                    ]
                },
                "libarchive": bool(ctypes.util.find_library("archive")),
                "data_dir": str(config.data_dir),
            }
        )
    if action == "info":
        return emit(system_profile(config.data_dir))
    if action == "catalog":
        if args.operation == "forget":
            db.delete("catalog_identity", args.game)
            print("Local identity assertion removed")
            return
        if (
            any(not value or len(value) > 256 for value in [args.game, args.canonical, args.build])
            or len(args.edition) > 256
        ):
            raise AppError("Catalog identity fields must contain 1..256 characters")
        db.put(
            "catalog_identity",
            args.game,
            {"canonical": args.canonical, "build": args.build, "edition": args.edition},
        )
        print(
            "Saved explicit identity assertion; only matching OS, architecture, edition and build can merge"
        )
        return
    if action == "settings":
        return settings_command(args, config)
    if action == "analyze":
        return analyze_command(args, config, db)
    if action == "feedback":
        from .compatibility.feedback import save_feedback
        from .platform import apply_overrides

        if args.operation == "list":
            return emit([row["value"] for row in db.all("feedback")])
        return emit(
            save_feedback(
                db,
                args.game,
                args.result,
                apply_overrides(system_profile(config.data_dir), config),
                args.notes,
                args.launch,
                args.manual,
            )
        )
    if action == "history":
        return emit([row["value"] for row in db.all("launches")])
    if action == "launch":
        from .runtime.launcher import Launcher

        custom_env = {}
        for value in args.env:
            if "=" not in value:
                raise AppError("Environment values must be KEY=VALUE")
            key, val = value.split("=", 1)
            custom_env[key] = val
        launcher = Launcher(config, db)
        plan = launcher.plan(
            args.game,
            args.executable,
            args.method,
            args.prefix,
            args.arg,
            custom_env,
            json.loads(args.command) if args.command else None,
            args.allow_external,
        )
        if args.dry_run:
            return emit({k: v for k, v in plan.items() if k != "env"} | {"custom_environment": custom_env})
        plan["interactive"] = args.interactive
        record = launcher.launch(plan)
        emit(record)
        if record["exit_code"]:
            raise AppError(
                "Game exited with status {}; see history and launch log".format(record["exit_code"])
            )
        return
    if action == "prefix":
        from .runtime.prefixes import PrefixManager

        manager = PrefixManager(config, db)
        if args.operation == "list":
            return emit([row["value"] for row in db.all("prefixes")])
        if args.operation == "check":
            return emit(manager.check(args.game))
        if args.operation == "recover":
            return emit(manager.recover(args.game, args.timeout))
        if args.operation == "backup":
            return emit(manager.backup(args.game))
        if (
            not args.yes
            and input(
                "{} isolated Wine prefix for {}? [y/N] ".format(args.operation.title(), clean(args.game))
            ).lower()
            != "y"
        ):
            return
        return emit(
            manager.create(args.game, args.arch, timeout=args.timeout)
            if args.operation == "create"
            else manager.remove(args.game, args.purge)
        )
    if action == "download":
        from .downloads.manager import DownloadManager

        manager = DownloadManager(config, db)
        if args.operation == "list":
            return emit([row["value"] for row in db.all("downloads")])
        if args.operation == "run":
            manager.run(
                lambda job: print(
                    clean(
                        "{}: {} / {} bytes ({})".format(
                            job["id"], job["received"], job["expected_size"], job["state"]
                        )
                    ),
                    flush=True,
                )
            )
            failed = [r for r in db.all("downloads") if r["value"]["state"] == "failed"]
            if failed:
                raise AppError("Some downloads failed; inspect download list and retry")
            return
        if args.operation == "retry":
            return emit(manager.retry(args.id, args.restart))
        if args.operation == "cancel":
            manager.cancel(args.id, args.cleanup)
            print("Cancellation requested")
            return
        library = Library(config, db).load("gamevault", args.offline)
        return emit(
            manager.add(library.find(args.game, alternatives=True), args.sha256, args.max_gb * 1024**3)
        )
    if action == "uninstall":
        from .runtime.installer import Installer

        if (
            args.yes
            or input(
                "Remove managed game {}{}? [y/N] ".format(
                    clean(args.game), " permanently" if args.purge else " to backups"
                )
            ).lower()
            == "y"
        ):
            return emit(Installer(config, db).uninstall(args.game, args.purge))
        return
    if action == "install" and args.archive:
        from .runtime.installer import Installer
        from .models import Game

        if args.game.startswith("local:"):
            game = Game(args.game, args.title or args.game.split(":", 1)[1], "local")
        else:
            library = Library(config, db).load(offline=True)
            game = library.find(args.game, alternatives=True)
        if (
            args.yes
            or input("Extract {} into a managed game directory? [y/N] ".format(clean(game.title))).lower()
            == "y"
        ):
            manifest = Installer(config, db).install(game, args.archive, args.reinstall, args.timeout, print)
            emit(manifest)
        return
    library = Library(config, db).load(getattr(args, "source", "all"), getattr(args, "offline", False))
    if library.sync:
        print("GameVault synchronization: " + json.dumps(library.sync), file=sys.stderr)
    for warning in library.warnings:
        print("Notice: " + clean(warning), file=sys.stderr)
    if action in {"list", "reports", "installed"}:
        selected = filters(args)
        selected["show_all"] = (
            args.all or action in {"reports", "installed"} or bool(config.values.get("show_all"))
        )
        selected["installed"] = args.installed or action == "installed"
        games, explanation = library.filtered(**selected)
        print(clean(explanation), file=sys.stderr)
        if args.json or action == "reports":
            return emit(
                {
                    "games": [g.to_dict() for g in games],
                    "reports": {g.id: library.reports[g.id] for g in games},
                    "sync": library.sync,
                }
            )
        for game in games:
            print(
                clean(
                    "{} | {} | {} | {}".format(
                        game.package or game.id,
                        game.title,
                        "installed"
                        if game.installed
                        else "available"
                        if game.source == "pacman"
                        else "not installed",
                        library.reports[game.id]["status"],
                    )
                )
            )
    elif action == "details":
        game = library.find(args.game)
        emit({"game": game.to_dict(), "compatibility": library.reports[game.id], "sync": library.sync})
    elif action == "menu":
        from .ui.terminal import menu

        menu(
            library,
            lambda argv: main((["--state-dir", args.state_dir] if args.state_dir else []) + argv),
            getattr(args, "plain", False),
        )
    elif action == "install":
        game = library.find(args.game, alternatives=True)
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

        selected = filters(args)
        selected["show_all"] = args.all or bool(config.values.get("show_all"))
        games, explanation = library.filtered(**selected)
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
    except ArchiveError as exc:
        print("Error: " + clean(str(exc)), file=sys.stderr)
        if args.debug:
            secrets = []
            try:
                secrets = list(config.credentials().values())
            except (AppError, OSError):
                pass
            print(exc.diagnostics(secrets), file=sys.stderr)
        return 1
    except (AppError, OSError, ValueError) as exc:
        print("Error: " + clean(str(exc)), file=sys.stderr)
        return 1
    except sqlite3.Error:
        print(
            "Error: Local state database is unavailable or damaged; restore a private backup before continuing.",
            file=sys.stderr,
        )
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 130
    finally:
        if db:
            db.close()
