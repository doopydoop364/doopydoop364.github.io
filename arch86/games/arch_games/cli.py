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
    parser.add_argument(
        "--non-interactive", action="store_true", help="Never prompt; ambiguous names fail with the candidate IDs"
    )
    sub = parser.add_subparsers(dest="action")
    menu = sub.add_parser("menu", help="Keyboard terminal interface")
    menu.add_argument("--plain", action="store_true")
    catalog_options(menu)
    listing = sub.add_parser("list", help="Search the combined catalog")
    catalog_options(listing)
    sub.add_parser("doctor", help="Application integrity and optional prerequisites")
    info = sub.add_parser("info", help="Measured system profile, or everything known about one game")
    info.add_argument("game", nargs="?")
    details = sub.add_parser("details", help="Game details and compatibility report")
    details.add_argument("game")
    details.add_argument("--offline", action="store_true")
    reports = sub.add_parser("reports", help="Compatibility reports, including unknown entries")
    catalog_options(reports)
    analyze = sub.add_parser("analyze", help="Inspect a local executable/archive without running it")
    analyze.add_argument("path", help="Local file, or a game name to analyze its registered launcher")
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
    install.add_argument("--directory", help="Use an already-extracted/installed game folder in place")
    install.add_argument("--plan-only", action="store_true", help="Show the plan; execute nothing")
    install.add_argument("--installer", help="Archive member to run when several installers exist")
    install.add_argument(
        "--approve-installer", action="store_true", help="Explicitly approve running the downloaded installer"
    )
    install.add_argument("--silent-inno", action="store_true", help="Pass Inno Setup silent flags (only if evidence shows Inno)")
    install.add_argument("--test-launch", action="store_true", help="Supervised, time-limited launch after install")
    install.add_argument("--test-seconds", type=int, default=20)
    install.add_argument("--prefix-arch", choices=["win32", "win64"])
    install.add_argument("--sha256", help="Expected SHA-256 of the archive")
    install.add_argument("--offline", action="store_true")
    install.add_argument("--abort", action="store_true", help="Cancel the unfinished job (files go to backups)")
    install.add_argument("--status", action="store_true", help="Show the saved installation job")
    install.add_argument("--extract-only", action="store_true", help="2.0 behaviour: extract --archive, never run anything")
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
    for name in ["inspect", "backups", "export"]:
        psub.add_parser(name).add_argument("game")
    restore = psub.add_parser("restore", help="Restore a prefix backup (preview unless --apply)")
    restore.add_argument("game")
    restore.add_argument("backup")
    restore.add_argument("--apply", action="store_true")
    wine = sub.add_parser("wine", help="Wine runtimes, diagnostics and per-game settings")
    wsub = wine.add_subparsers(dest="operation", required=True)
    wdoctor = wsub.add_parser("doctor")
    wdoctor.add_argument("--json", action="store_true")
    wsub.add_parser("runtimes")
    wreg = wsub.add_parser("register-runtime", help="Register an already-installed wine binary by path")
    wreg.add_argument("name")
    wreg.add_argument("path")
    wsub.add_parser("status").add_argument("game")
    wlogs = wsub.add_parser("logs")
    wlogs.add_argument("game")
    wlogs.add_argument("--lines", type=int, default=60)
    wlogs.add_argument("--raw", action="store_true", help="Do not redact private paths and addresses")
    wcfg = wsub.add_parser("config", help="Per-game Wine settings (stored; applied at launch)")
    wcfg.add_argument("game")
    wcfg.add_argument("--runtime")
    wcfg.add_argument("--windows-version")
    wcfg.add_argument("--dll-override", action="append", default=[], metavar="DLL=MODE")
    wcfg.add_argument("--env", action="append", default=[])
    wcfg.add_argument("--virtual-desktop", help="WIDTHxHEIGHT, or 'off'")
    wcfg.add_argument("--debug", help="WINEDEBUG channels, or 'off'")
    wcfg.add_argument("--clear", action="store_true")
    wapply = wsub.add_parser("apply-version", help="Run winecfg to set the saved Windows version in the prefix")
    wapply.add_argument("game")
    audio = sub.add_parser("audio", help="Audio diagnostics and a bounded sound test")
    usub = audio.add_subparsers(dest="operation", required=True)
    udoctor = usub.add_parser("doctor")
    udoctor.add_argument("--json", action="store_true")
    usub.add_parser("devices")
    uremote = usub.add_parser("remote", help="Optional browser audio via the host bridge (off by default)")
    rsub = uremote.add_subparsers(dest="remote_op", required=True)
    rconf = rsub.add_parser("configure")
    rconf.add_argument("--url", required=True)
    rconf.add_argument("--vm", required=True)
    rconf.add_argument("--allow-http", action="store_true", help="Needed for http:// (use only inside Tailscale/LAN)")
    for name in ["status", "disable", "instructions", "url"]:
        rsub.add_parser(name)
    utest = usub.add_parser("test")
    utest.add_argument("--seconds", type=float, default=1.5)
    utest.add_argument("--backend", choices=["auto", "pulse", "alsa"], default="auto")
    launch = sub.add_parser("launch", help="Explicitly launch a selected installed executable")
    launch.add_argument("game")
    launch.add_argument(
        "executable", nargs="?", help="Optional; omitted = use the registered launcher for this game"
    )
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
    alias = sub.add_parser("alias", help="Friendly names for games")
    asub = alias.add_subparsers(dest="operation", required=True)
    aadd = asub.add_parser("add")
    aadd.add_argument("alias")
    aadd.add_argument("game")
    aadd.add_argument("--force", action="store_true")
    asub.add_parser("list")
    aremove = asub.add_parser("remove")
    aremove.add_argument("alias")
    discover = sub.add_parser("discover", help="Rank the executables that may start a game (nothing is run)")
    discover.add_argument("game")
    discover.add_argument("--dir", action="append", default=[], help="Extra directory to search")
    discover.add_argument("--all", action="store_true", help="Include uninstallers, installers, helpers")
    discover.add_argument("--json", action="store_true")
    discover.add_argument("--register", type=int, metavar="RANK", help="Confirm and register this candidate")
    launcher = sub.add_parser("launcher", help="Registered launcher per game")
    lsub = launcher.add_subparsers(dest="operation", required=True)
    lsub.add_parser("list")
    for name in ["show", "remove"]:
        lsub.add_parser(name).add_argument("game")
    register = lsub.add_parser("register")
    register.add_argument("game")
    register.add_argument("executable", nargs="?", help="Absolute path; omitted = choose from discovery")
    register.add_argument("--prefix")
    register.add_argument("--cwd")
    register.add_argument("--arg", action="append", default=[])
    register.add_argument("--env", action="append", default=[])
    register.add_argument("--allow-external", action="store_true")
    register.add_argument("--force-role", action="store_true")
    register.add_argument("--yes", action="store_true")
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
    settings.add_argument("operation", choices=["show", "set", "game"], nargs="?", default="show")
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


def parse_env(values):
    result = {}
    for value in values:
        if "=" not in value:
            raise AppError("Environment values must be KEY=VALUE")
        key, val = value.split("=", 1)
        result[key] = val
    return result


def interactive(args):
    return not args.non_interactive and sys.stdin.isatty() and sys.stdout.isatty()


def game_title(config, db, game_id):
    record = db.get("launchers", game_id) or {}
    manifest = db.get("installs", game_id) or {}
    return record.get("title") or (manifest.get("game") or {}).get("title") or game_id


def choose_candidate(candidates, args):
    """Present ranked candidates; returns the chosen one or raises with guidance. Never guesses."""
    from .runtime.discovery import confidence

    if not candidates:
        raise AppError("No game executables found. Use 'discover --dir DIR' or 'launcher register GAME /abs/path.exe'")
    for c in candidates[:10]:
        print(clean("{:2}. [{}] {}  ({})".format(c["rank"], c["score"], c["path"], c["origin"])), file=sys.stderr)
        for line in c["evidence"][:4]:
            print(clean("      - " + line), file=sys.stderr)
    if not interactive(args):
        raise AppError(
            "Executable not confirmed ({} confidence). Re-run 'discover GAME --register RANK' to confirm one.".format(
                confidence(candidates)
            )
        )
    answer = input("Register which candidate? 1-{} (blank cancels): ".format(min(10, len(candidates)))).strip()
    if not answer.isdecimal() or not 1 <= int(answer) <= min(10, len(candidates)):
        raise AppError("No executable selected")
    return candidates[int(answer) - 1]


def register_candidate(config, db, game_id, candidate, **options):
    from .runtime.launchers import LauncherStore

    return LauncherStore(config, db).register(
        game_id,
        game_title(config, db, game_id),
        candidate["path"],
        evidence=["Discovery score {}".format(candidate["score"])] + candidate["evidence"],
        **options,
    )


def catalog_title(config, db, game_id):
    from .identity.service import build_resolver

    try:
        resolver, _ = build_resolver(config, db)
        return resolver.by_id[game_id]["title"] if game_id in resolver.by_id else game_title(config, db, game_id)
    except AppError:
        return game_title(config, db, game_id)


def install_command(args, config, db):
    from .installation.workflow import Options, Workflow

    def confirm(question):
        return input(question + " [y/N] ").strip().lower() == "y"

    def choose(question, labels):
        print(question, file=sys.stderr)
        for number, label in enumerate(labels[:10], 1):
            print(clean("{:2}. {}".format(number, label)), file=sys.stderr)
        answer = input("Number (blank cancels): ").strip()
        return int(answer) - 1 if answer.isdecimal() and 1 <= int(answer) <= min(10, len(labels)) else None

    def library_find(game_id):
        from .catalog.library import Library

        return Library(config, db).load("gamevault", args.offline).find(game_id, alternatives=True)

    flow = Workflow(
        config,
        db,
        say=lambda text: print(clean(text), file=sys.stderr),
        confirm=confirm if interactive(args) else None,
        choose=choose if interactive(args) else None,
        library_find=library_find,
    )
    if args.status:
        return emit(flow.job(args.game) or {"status": "no job"})
    if args.abort:
        return emit(flow.abort(args.game))
    options = Options(
        archive=args.archive,
        directory=args.directory,
        yes=args.yes,
        reinstall=args.reinstall,
        plan_only=args.plan_only,
        installer_member=args.installer,
        silent_inno=args.silent_inno,
        approve_installer=args.approve_installer,
        test_launch=args.test_launch,
        test_seconds=args.test_seconds,
        timeout=args.timeout,
        offline=args.offline,
        prefix_arch=args.prefix_arch,
        expected_sha256=args.sha256,
    )
    report = flow.run(args.game, catalog_title(config, db, args.game), options)
    emit(report)
    if report["status"] in {"awaiting_approval", "awaiting_selection", "plan_only"}:
        print("Installation paused: {}. {}".format(report["status"], " ".join(report["next"])), file=sys.stderr)


def wine_command(args, config, db):
    from .diagnostics.redact import redact
    from .runtime import winetools

    op = args.operation
    if op == "doctor":
        report = winetools.doctor(config, db)
        if args.json:
            return emit(report)
        for item in report["checks"]:
            print(clean("[{:4}] {}: {}".format(item["status"].upper(), item["check"], item["detail"])))
            if item["fix"]:
                print(clean("       fix: " + item["fix"]))
        print(clean("Summary: " + json.dumps(report["summary"])))
        return
    if op == "runtimes":
        return emit({k: v.to_dict() for k, v in winetools.runtimes(config).items()})
    if op == "register-runtime":
        winetools.register_runtime(config, args.name, args.path)
        print("Runtime registered (path recorded only; nothing was downloaded or run)")
        return
    if op == "status":
        return emit(winetools.status(config, db, args.game))
    if op == "logs":
        launches = sorted(
            (v for v in db.mapping("launches").values() if v.get("game_id") == args.game),
            key=lambda v: v.get("started_at", ""),
        )
        if not launches:
            raise AppError("No launches recorded for this game")
        record = launches[-1]
        path = Path(record["log"])
        if not path.is_file():
            raise AppError("Launch log file is missing")
        size = path.stat().st_size
        with path.open("rb") as stream:
            stream.seek(max(0, size - 256 * 1024))
            text = stream.read().decode("utf-8", errors="replace")
        text = "\n".join(text.splitlines()[-max(1, min(args.lines, 2000)):])
        if not args.raw:
            text = redact(text, [(record.get("prefix_path"), "<prefix>")])
        print("# launch {} state={} exit={}{}".format(record["id"], record["state"], record.get("exit_code"), "" if args.raw else " (redacted)"))
        print(clean(text))
        return
    if op == "config":
        if args.clear:
            winetools.clear_settings(db, args.game)
            print("Per-game Wine settings cleared")
            return
        updates = {}
        if args.runtime:
            updates["runtime"] = args.runtime
        if args.windows_version:
            updates["windows_version"] = args.windows_version
        if args.dll_override:
            overrides = dict(winetools.get_settings(db, args.game).get("dll_overrides") or {})
            for item in args.dll_override:
                if "=" not in item:
                    raise AppError("DLL overrides look like dxgi=n,b")
                dll, mode = item.split("=", 1)
                overrides[dll] = mode
            updates["dll_overrides"] = overrides
        if args.env:
            updates["env"] = dict(winetools.get_settings(db, args.game).get("env") or {}, **parse_env(args.env))
        if args.virtual_desktop is not None:
            updates["virtual_desktop"] = None if args.virtual_desktop == "off" else args.virtual_desktop
        if args.debug is not None:
            updates["debug"] = None if args.debug == "off" else args.debug
        return emit(winetools.save_settings(db, args.game, updates) if updates else winetools.get_settings(db, args.game))
    if op == "apply-version":
        import subprocess

        settings = winetools.get_settings(db, args.game)
        if not settings.get("windows_version"):
            raise AppError("No windows_version saved; use 'wine config GAME --windows-version win7'")
        from .runtime.prefixes import PrefixManager

        health = PrefixManager(config, db).check(args.game)
        if not health["healthy"]:
            raise AppError("Prefix is not healthy; run prefix check first")
        env = dict(__import__("arch_games.runtime.environment", fromlist=["environment"]).environment(), WINEPREFIX=health["path"])
        known = winetools.runtimes(config)
        runtime = known.get(settings.get("runtime", "system"))
        if not runtime:
            raise AppError("Wine runtime unavailable")
        result = subprocess.run([runtime.wine, "winecfg", "-v", settings["windows_version"]], env=env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AppError("winecfg failed; run 'wine logs' or retry with a graphical session")
        print("Windows version set to " + settings["windows_version"])
        return


def audio_command(args, config, db):
    from .audio import diagnose as audio
    from .audio.tone import run_test
    from .runtime.discovery import prefix_path

    if args.operation == "remote":
        from .audio import remote

        if args.remote_op == "configure":
            token = secret_prompt("Bridge token: ")
            print(remote.configure(config, args.url, args.vm, token, args.allow_http))
            return
        if args.remote_op == "disable":
            remote.disable(config)
            print("Remote audio disabled; token removed")
            return
        if args.remote_op == "instructions":
            print(remote.instructions(remote.settings(config).get("vm", "arch64")))
            return
        if args.remote_op == "url":
            print(remote.page_url(config) or "Remote audio is not configured")
            return
        return emit(remote.status(config))
    if args.operation == "devices":
        return emit(audio.devices())
    if args.operation == "test":
        result = run_test(seconds=args.seconds, backend=args.backend)
        emit(result)
        if not result["played"]:
            raise AppError("Sound test failed; run 'audio doctor'")
        return
    regs = {}
    for row in db.all("prefixes"):
        path = prefix_path(config, db, row["key"])
        if path:
            regs[row["key"]] = path / "user.reg"
    report = audio.diagnose(prefix_user_regs=regs)
    if args.json:
        return emit(report)
    print("Audio verdict: " + report["verdict"])
    virt = report["virtualization"]
    print("Environment: " + ("virtual machine ({})".format(virt["kind"]) if virt["virtualized"] else "physical machine / not detected as VM"))
    for item in report["layers"]:
        print(clean("[{:4}] {}: {}".format(item["status"].upper(), item["layer"], item["detail"])))
        if item["fix"]:
            for line in item["fix"].splitlines():
                print(clean("       " + line))


def alias_command(args, config, db):
    from .identity.resolver import add_alias, remove_alias
    from .identity.service import build_resolver

    if args.operation == "list":
        return emit([row["value"] for row in db.all("aliases")])
    if args.operation == "remove":
        remove_alias(db, args.alias)
        print("Alias removed")
        return
    resolver, _ = build_resolver(config, db)
    target = resolver.resolve(args.game)[0]["id"]
    emit(add_alias(db, resolver, args.alias, target, args.force))


def discover_command(args, config, db):
    from .runtime.discovery import confidence, discover

    candidates = discover(config, db, args.game, game_title(config, db, args.game), args.dir, args.all)
    if args.register:
        chosen = next((c for c in candidates if c["rank"] == args.register), None)
        if not chosen:
            raise AppError("No candidate with that rank")
        return emit(register_candidate(config, db, args.game, chosen))
    if args.json:
        return emit({"confidence": confidence(candidates), "candidates": candidates})
    if not candidates:
        print("No candidate executables found.")
    for c in candidates:
        print(clean("{:2}. [{:4}] {:16} {}".format(c["rank"], c["score"], c["role"], c["path"])))
        for line in c["evidence"]:
            print(clean("        - " + line))
    print("Confidence: " + confidence(candidates) + "  (register with: discover GAME --register RANK)")


def launcher_command(args, config, db):
    from .runtime.discovery import discover
    from .runtime.launchers import LauncherStore

    store = LauncherStore(config, db)
    if args.operation == "list":
        return emit(list(store.all().values()))
    if args.operation == "show":
        record = store.get(args.game)
        if not record:
            raise AppError("No registered launcher for this game")
        return emit(record)
    if args.operation == "remove":
        store.remove(args.game)
        print("Launcher removed; game files untouched")
        return
    options = dict(
        prefix=args.prefix,
        arguments=args.arg,
        env=parse_env(args.env),
        cwd=args.cwd,
        allow_external=args.allow_external,
        force_role=args.force_role,
    )
    if args.executable:
        candidate = {"path": str(Path(args.executable).expanduser()), "score": "user", "evidence": ["Path supplied by the user"]}
    else:
        candidate = choose_candidate(discover(config, db, args.game, game_title(config, db, args.game)), args)
    emit(register_candidate(config, db, args.game, candidate, **options))


def game_info(config, db, game_id):
    from .runtime.launchers import LauncherStore

    prefix = db.get("prefixes", game_id)
    return {
        "id": game_id,
        "title": game_title(config, db, game_id),
        "aliases": [r["value"]["alias"] for r in db.all("aliases") if r["value"]["game_id"] == game_id],
        "installed": db.get("installs", game_id) is not None,
        "launcher": LauncherStore(config, db).get(game_id),
        "prefix": {k: prefix.get(k) for k in ("directory", "state", "requested_architecture", "detected_architecture")} if prefix else None,
        "last_launch": max(
            (v for v in db.mapping("launches").values() if v.get("game_id") == game_id),
            key=lambda v: v.get("started_at", ""),
            default=None,
        ),
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
        from .identity.service import resolve_game_id

        game_id = resolve_game_id(config, db, args.path, interactive=interactive(args))
        record = db.get("launchers", game_id)
        if not record:
            raise AppError("No registered launcher for {}; run 'discover {}' first".format(game_id, game_id))
        path = Path(record["executable"])
        args.game = args.game or game_id
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
    from .identity.service import resolve_game_id
    import re

    def resolve(text):
        try:
            return resolve_game_id(config, db, text, interactive=interactive(args))
        except AppError:
            if re.fullmatch(r"[a-z0-9_-]+:[^\s]+", text):  # unknown but well-formed canonical ID (e.g. new local game)
                return text
            raise

    if action in {"details", "install", "uninstall", "launch", "prefix", "feedback", "catalog", "download", "discover", "launcher", "info"} and getattr(args, "game", None):
        if not (action == "launcher" and args.operation == "list"):
            args.game = resolve(args.game)
    if action == "settings" and args.operation == "game":
        args.key = resolve(args.key or "")
    if action == "alias":
        return alias_command(args, config, db)
    if action == "audio":
        return audio_command(args, config, db)
    if action == "wine":
        if args.operation in {"status", "logs", "config", "apply-version"}:
            args.game = resolve(args.game)
        return wine_command(args, config, db)
    if action == "discover":
        return discover_command(args, config, db)
    if action == "launcher":
        return launcher_command(args, config, db)
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
        return emit(game_info(config, db, args.game) if args.game else system_profile(config.data_dir))
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
        if args.operation == "game":
            return emit(game_info(config, db, args.key))
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

        custom_env = parse_env(args.env)
        launcher = Launcher(config, db)
        if args.executable:
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
        else:
            from .runtime.launchers import LauncherStore

            if args.method != "auto" or args.prefix or args.command or args.allow_external:
                raise AppError("--method/--prefix/--command/--allow-external need an explicit executable")
            if not LauncherStore(config, db).get(args.game):
                from .runtime.discovery import confidence, discover

                print("No launcher registered for {}; searching installed files...".format(clean(args.game)), file=sys.stderr)
                candidates = discover(config, db, args.game, game_title(config, db, args.game))
                chosen = choose_candidate(candidates, args)
                register_candidate(config, db, args.game, chosen)
                print("Launcher registered.", file=sys.stderr)
            plan = launcher.plan_registered(args.game, args.arg, custom_env)
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
        if args.operation == "inspect":
            from .runtime.winetools import inspect_prefix

            return emit(inspect_prefix(config, db, args.game))
        if args.operation == "backups":
            return emit(manager.backups(args.game))
        if args.operation == "export":
            return emit(manager.export_metadata(args.game))
        if args.operation == "restore":
            if args.apply and interactive(args):
                question = "Replace the prefix with backup {}? The current one is kept. [y/N] ".format(clean(args.backup))
                if input(question).strip().lower() != "y":
                    return
            return emit(manager.restore(args.game, args.backup, apply=args.apply))
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
    if action == "install" and not (args.game.startswith("pacman:") or args.extract_only):
        return install_command(args, config, db)
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
