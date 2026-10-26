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
    menu.add_argument("--ascii", action="store_true", help="ASCII-only drawing (also set by TERM=linux)")
    catalog_options(menu)
    listing = sub.add_parser("list", help="Search the combined catalog")
    catalog_options(listing)
    doctor = sub.add_parser("doctor", help="Application, data, Wine, audio and storage diagnostics")
    doctor.add_argument("--verbose", action="store_true")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--online", action="store_true", help="Also contact GameVault and the audio bridge (off by default)")
    doctor.add_argument("--redact", action="store_true", help="Hide home/data paths, IPs and tokens so the output can be shared")
    repair = sub.add_parser("repair", help="Plan (default / --dry-run) or apply safe repairs for one game")
    repair.add_argument("game")
    repair.add_argument("--dry-run", action="store_true", help="Show the plan (this is also the default)")
    repair.add_argument("--apply", action="store_true", help="Run the safe automatic steps only")
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
    web = sub.add_parser("web", help="Optional web GUI (loopback by default, token login)")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--allow-remote", action="store_true", help="Required to bind a non-loopback address")
    web.add_argument("--allowed-host", action="append", default=[], metavar="HOST:PORT", help="Extra Host header accepted (remote use)")
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
    add.add_argument("--priority", type=int, default=5, help="0 (first) .. 9 (last)")
    add.add_argument("--force", action="store_true", help="Download again even if this exact archive is already stored")
    dsub.add_parser("run")
    dpause = dsub.add_parser("pause", help="Stop and keep the partial file; 'retry' resumes")
    dpause.add_argument("id")
    dprio = dsub.add_parser("priority")
    dprio.add_argument("id")
    dprio.add_argument("level", type=int)
    ddedupe = dsub.add_parser("dedupe", help="Find identical archives; --apply hard-links the duplicates (nothing is deleted)")
    ddedupe.add_argument("--apply", action="store_true")
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
    machine = sub.add_parser("machine", help="This machine's identity for compatibility records")
    msub = machine.add_subparsers(dest="operation", required=True)
    msub.add_parser("show")
    msub.add_parser("name").add_argument("name")
    compat = sub.add_parser("compat", help="Per-machine compatibility records (local; never uploaded)")
    csub2 = compat.add_subparsers(dest="operation", required=True)
    cshow = csub2.add_parser("show")
    cshow.add_argument("game")
    clist = csub2.add_parser("list")
    clist.add_argument("--machine", default="this", help="'this', 'all' or a machine id")
    cset = csub2.add_parser("set", help="Record what you observed on this machine")
    cset.add_argument("game")
    cset.add_argument("--gameplay", choices=["unverified", "works", "issues", "slow", "fails"])
    cset.add_argument("--audio", choices=["unknown", "works", "issues", "none"])
    cset.add_argument("--issue", help="Add a known issue note")
    cset.add_argument("--notes")
    cexp = csub2.add_parser("export")
    cexp.add_argument("--output", help="File to write (default: stdout)")
    cimp = csub2.add_parser("import")
    cimp.add_argument("file")
    saves = sub.add_parser("saves", help="Find, back up and restore game saves (local only)")
    ssub = saves.add_subparsers(dest="operation", required=True)
    for name in ["discover", "paths", "list", "backup"]:
        sp = ssub.add_parser(name)
        sp.add_argument("game")
        if name == "backup":
            sp.add_argument("--reason", default="manual", choices=["manual", "pre-reinstall", "pre-prefix-change"])
    for name in ["add", "remove"]:
        sp = ssub.add_parser(name, help="Register / unregister a save folder you choose")
        sp.add_argument("game")
        sp.add_argument("path")
    for name in ["show", "verify"]:
        sp = ssub.add_parser(name)
        sp.add_argument("game")
        sp.add_argument("backup")
    srestore = ssub.add_parser("restore", help="Preview (default) or apply a restore")
    srestore.add_argument("game")
    srestore.add_argument("backup")
    srestore.add_argument("--apply", action="store_true")
    srestore.add_argument("--yes", action="store_true")
    search = sub.add_parser("search", help="Search the library by name, alias, tag, source, platform, state, compatibility")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--source", choices=["pacman", "gamevault", "local"])
    search.add_argument("--platform", choices=["windows", "linux", "dos"])
    search.add_argument("--installed", choices=["yes", "no"])
    search.add_argument("--compat", help="e.g. 'Verified working', 'Likely compatible'")
    search.add_argument("--tag")
    search.add_argument("--favorites", action="store_true")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--json", action="store_true")
    rec = sub.add_parser("recommend", help="Explainable suggestions from your own favorites, playtime, tags and interests")
    rec.add_argument("--limit", type=int, default=10)
    interests = sub.add_parser("interests", help="Keywords you like (used only for local recommendations)")
    isub = interests.add_subparsers(dest="operation", required=True)
    isub.add_parser("list")
    for name in ["add", "remove"]:
        isub.add_parser(name).add_argument("keyword", nargs="+")
    sync = sub.add_parser("sync", help="Private file-based bundle of favorites, aliases, notes, tags, titles, compatibility")
    ysub = sync.add_subparsers(dest="operation", required=True)
    yexp = ysub.add_parser("export")
    yexp.add_argument("--output")
    yimp = ysub.add_parser("import")
    yimp.add_argument("file")
    yimp.add_argument("--prefer-remote", action="store_true")
    favorite = sub.add_parser("favorite", help="Mark favorite games")
    fsub2 = favorite.add_subparsers(dest="operation", required=True)
    fsub2.add_parser("list")
    for name in ["add", "remove", "toggle"]:
        fsub2.add_parser(name).add_argument("game")
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
    launch.add_argument("--profile", help="Launch profile to use (default: the game's default profile)")
    launch.add_argument("--monitor", action="store_true", help="Record CPU/memory samples for this launch (also: setting performance_monitoring)")
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
    from . import cli_launcher

    cli_launcher.extend(lsub)
    history = sub.add_parser("history", help="Launch history (all games, or one)")
    history.add_argument("game", nargs="?")
    history.add_argument("--limit", type=int, default=50)
    playtime = sub.add_parser("playtime", help="Playtime estimates from launch history")
    playtime.add_argument("game", nargs="?")
    perf = sub.add_parser("performance", help="Stored performance measurements (launches nothing)")
    perf.add_argument("game")
    bench = sub.add_parser("benchmark", help="Explicitly launch a game and measure it for a fixed time")
    bench.add_argument("game")
    bench.add_argument("--seconds", type=int, default=60)
    bench.add_argument("--yes", action="store_true")
    title = sub.add_parser("title", help="Custom display titles")
    tsub = title.add_subparsers(dest="operation", required=True)
    tset = tsub.add_parser("set")
    tset.add_argument("game")
    tset.add_argument("title")
    tsub.add_parser("clear").add_argument("game")
    notes = sub.add_parser("notes", help="Show or set your notes for a game")
    notes.add_argument("game")
    notes.add_argument("text", nargs="?")
    tag = sub.add_parser("tag", help="User-defined tags")
    tagsub = tag.add_subparsers(dest="operation", required=True)
    for name in ["add", "remove"]:
        t = tagsub.add_parser(name)
        t.add_argument("game")
        t.add_argument("tag")
    tagsub.add_parser("list").add_argument("game", nargs="?")
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


def pacman_service():
    """Read-only Pacman queries. A function so tests can substitute a fake database; nothing here installs anything."""
    from .catalog import pacman as catalog_pacman
    from .runtime.pacman import PacmanService

    return PacmanService(run=catalog_pacman.default_run())


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
    from .runtime import gamelaunch

    return gamelaunch.register(config, db, game_id, catalog_title(config, db, game_id), candidate, pacman_service(), **options)


def catalog_title(config, db, game_id):
    """Human-readable title for a game ID: the catalog/custom title (also via its bare/versioned sibling), else the best local record."""
    from .core.ids import base
    from .identity.service import build_resolver

    try:
        resolver, _ = build_resolver(config, db)
    except AppError:
        return game_title(config, db, game_id)
    if game_id in resolver.by_id and resolver.by_id[game_id]["title"] != game_id:
        return resolver.by_id[game_id]["title"]
    siblings = [c for c in resolver.candidates if base(c["id"]) == base(game_id) and c["title"] != c["id"]]
    return siblings[0]["title"] if siblings else game_title(config, db, game_id)


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
    from .core.ids import local_id as _local

    if args.status:
        return emit(flow.job(_local(db, args.game)) or flow.job(args.game) or {"status": "no job"})
    if args.abort:
        return emit(flow.abort(_local(db, args.game)))
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
    from .core.ids import local_id

    state_id = local_id(db, args.game)
    if state_id != args.game:
        print("Using the local state recorded under {}".format(state_id), file=sys.stderr)
    report = flow.run(state_id, catalog_title(config, db, args.game), options, catalog_id=args.game)
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
    report = audio.diagnose_for(config, db)
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
    from .runtime import gamelaunch

    result = gamelaunch.discover(config, db, args.game, catalog_title(config, db, args.game), pacman_service(), args.dir, args.all)
    candidates = result["candidates"]
    if args.register:
        chosen = next((c for c in candidates if c["rank"] == args.register), None)
        if not chosen:
            raise AppError("No candidate with that rank")
        return emit(register_candidate(config, db, args.game, chosen))
    if args.json:
        return emit({"runner": result["runner"], "installed": result["installed"], "package": result["package"], "version": result["version"],
                     "confidence": result["confidence"], "notes": result["notes"], "candidates": candidates})
    for note in result["notes"]:
        print(clean(note))
    if not candidates and not result["notes"]:
        print("No candidate executables found.")
    for c in candidates:
        print(clean("{:2}. [{:4}] {:16} {}".format(c["rank"], c["score"], c["role"], c["path"])))
        for line in c["evidence"]:
            print(clean("        - " + line))
    print("Confidence: " + result["confidence"] + "  (register with: discover GAME --register RANK)")


def launcher_command(args, config, db):
    from .runtime.discovery import discover
    from .runtime.launchers import LauncherStore

    from . import cli_launcher

    if args.operation in cli_launcher.OPERATIONS:
        return cli_launcher.run(args, config, db, {"interactive": interactive, "choose_candidate": choose_candidate, "catalog_title": catalog_title, "pacman": pacman_service})
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
        candidate = choose_candidate(discover(config, db, args.game, catalog_title(config, db, args.game)), args)
    emit(register_candidate(config, db, args.game, candidate, **options))


def game_info(config, db, game_id):
    from .runtime import gamelaunch
    from .runtime.launchers import LauncherStore

    prefix = db.get("prefixes", game_id)
    state = gamelaunch.status(config, db, game_id, pacman_service(), deep=False)
    installed = state["version"] is not None if state["package"] else db.get("installs", game_id) is not None
    return {
        "id": game_id,
        "title": catalog_title(config, db, game_id),
        "aliases": [r["value"]["alias"] for r in db.all("aliases") if r["value"]["game_id"] == game_id],
        "installed": installed,
        "status": {"state": state["status"], "text": state["label"]},
        "package": {"name": state["package"], "version": state["version"]} if state["package"] else None,
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
    allowed = {"show_all", "wine_win32", "wine_win64", "wine_wow64", "performance_monitoring"}
    if args.key not in allowed or args.value not in {"true", "false", "unknown"}:
        raise AppError("Settings keys: show_all/wine_win32/wine_win64/wine_wow64/performance_monitoring; values: true/false/unknown")
    config.values[args.key] = {"true": True, "false": False, "unknown": None}[args.value]
    config.save()
    print("Saved. Wine capability overrides are user assertions, not measured results.")


def dispatch(args, config, db):
    from .catalog.library import Library
    from .platform import system_profile

    action = args.action or "menu"
    from .identity.service import resolve_game_id
    import re

    def resolve(text, local=True):
        try:
            return resolve_game_id(config, db, text, interactive=interactive(args), local=local)
        except AppError:
            if re.fullmatch(r"[a-z0-9_-]+:[^\s]+", text):  # unknown but well-formed canonical ID (e.g. new local game)
                return text
            raise

    if action in {"details", "install", "uninstall", "launch", "prefix", "feedback", "catalog", "download", "discover", "launcher", "info", "repair", "performance", "benchmark", "saves"} and getattr(args, "game", None):
        if not (action == "launcher" and args.operation == "list"):
            # Catalog operations keep the versioned ID; everything about this machine uses the ID its state is recorded under.
            args.game = resolve(args.game, local=action not in {"details", "download", "install"})
    if action == "settings" and args.operation == "game":
        args.key = resolve(args.key or "")
    if action == "search":
        from .catalog.library import Library
        from .catalog.search import SearchIndex

        library = Library(config, db).load(offline=True)
        found = SearchIndex(library, db).search(
            args.query, args.source, args.platform, {"yes": True, "no": False}.get(args.installed), args.compat, args.tag, True if args.favorites else None, max(1, args.limit)
        )
        if args.json:
            return emit([{"id": r["game"].id, "title": r["game"].title, "source": r["source"], "platform": r["platform"], "installed": r["installed"], "compatibility": r["classification"], "favorite": r["favorite"]} for r in found])
        for r in found:
            print(clean("{:34} {:10} {:8} {:3} {}".format(r["game"].title[:34], r["source"], r["platform"], "yes" if r["installed"] else "no", r["classification"])) + clean("  [" + r["game"].id + "]"))
        if not found:
            print("No matches")
        return
    if action == "recommend":
        from .catalog.library import Library
        from .recommend import recommend

        return emit(recommend(Library(config, db).load(offline=True), db, max(1, args.limit)))
    if action == "interests":
        from .recommend import interests as list_interests, set_interests

        if args.operation == "list":
            return emit(list_interests(db))
        current = set(list_interests(db))
        words = {w.casefold() for w in args.keyword}
        try:
            return emit(set_interests(db, current | words if args.operation == "add" else current - words))
        except ValueError as exc:
            raise AppError(str(exc)) from None
    if action == "sync":
        from . import sync as sync_module

        if args.operation == "export":
            data = sync_module.export(db, config)
            if args.output:
                from .config import atomic_json

                atomic_json(args.output, data)
                print("Wrote private bundle (no credentials, paths or game files): " + args.output)
                return
            return emit(data)
        from .config import read_json

        return emit(sync_module.import_bundle(db, config, read_json(args.file), args.prefer_remote))
    if action == "saves":
        from .saves.service import SaveManager

        manager = SaveManager(config, db)
        game_id = resolve(args.game)
        say = lambda t: print(clean(t), file=sys.stderr)  # noqa: E731
        op = args.operation
        if op == "discover":
            found = manager.discover(game_id, catalog_title(config, db, game_id))
            note = "Nothing is registered automatically. Register a folder with: saves add GAME PATH. Registry-stored saves are not covered."
            if not found:
                note = ("No save folder was found. That is expected if the game has not been played and saved yet (most games create their save folder on the first save): "
                        "play and save once, then run this again, or register a folder yourself with: saves add GAME PATH. " + note)
            return emit({"candidates": found, "note": note})
        if op == "paths":
            return emit(manager.paths(game_id))
        if op == "add":
            return emit(manager.add_path(game_id, args.path))
        if op == "remove":
            manager.remove_path(game_id, args.path)
            print("Save folder unregistered (files untouched)")
            return
        if op == "backup":
            return emit(manager.backup(game_id, args.reason, title=catalog_title(config, db, game_id), say=say))
        if op == "list":
            return emit(manager.list_backups(game_id))
        if op == "show":
            return emit(manager.manifest(game_id, args.backup)[0])
        if op == "verify":
            return emit(manager.verify(game_id, args.backup))
        if op == "restore":
            if args.apply and not args.yes:
                if not interactive(args):
                    raise AppError("Restoring replaces files; add --yes to confirm (preview first without --apply)")
                preview = manager.preview(game_id, args.backup)
                print("{} file(s) would be overwritten, {} are new.".format(preview["overwrites"], preview["new"]), file=sys.stderr)
                if input("Restore now? A safety backup of current saves is made first. [y/N] ").strip().lower() != "y":
                    return
            return emit(manager.restore(game_id, args.backup, confirm=args.apply, say=say))
    if action == "machine":
        from .compatibility import intelligence

        if args.operation == "name":
            intelligence.set_machine_name(config, args.name)
        return emit(intelligence.machine_identity(config))
    if action == "compat":
        from .compatibility import intelligence

        machine_id = intelligence.machine_identity(config)["id"]
        if args.operation == "list":
            return emit(intelligence.all_records(db, machine_id if args.machine == "this" else args.machine))
        if args.operation == "export":
            data = intelligence.export(db, config)
            if args.output:
                from .config import atomic_json

                atomic_json(args.output, data)
                print("Exported {} records (private file; contains no credentials)".format(len(data["records"])))
                return
            return emit(data)
        if args.operation == "import":
            from .config import read_json

            return emit(intelligence.import_records(db, config, read_json(args.file)))
        game_id = resolve(args.game)
        if args.operation == "set":
            fields = {k: v for k, v in (("gameplay_status", args.gameplay), ("audio_status", args.audio)) if v}
            return emit(intelligence.update(db, config, game_id, issue=args.issue, notes=args.notes, **fields))
        from .catalog.library import Library

        library = Library(config, db).load(offline=True)
        report = library.reports[library.find(game_id).id]
        return emit({"game": game_id, "machine": intelligence.machine_identity(config), "classification": report["classification"],
                     "reasons": report["classification_reasons"], "legacy_status": report["status"],
                     "facts": report["facts"], "record": intelligence.get(db, machine_id, game_id)})
    if action == "favorite":
        if args.operation == "list":
            return emit(sorted(db.mapping("favorites")))
        from .core import personal

        game_id = resolve(args.game)
        wanted = {"add": True, "remove": False}.get(args.operation)
        now_on = personal.toggle_favorite(db, game_id) if wanted is None else personal.set_favorite(db, game_id, wanted)
        print(("Added to favorites: " if now_on else "Removed from favorites: ") + game_id)
        return
    if action == "web":
        from .ui.web.server import WebApp

        app = WebApp(config, args.host, args.port, args.allow_remote, args.allowed_host)
        port = app.start()
        shown = args.host if args.host not in ("0.0.0.0", "::") else "127.0.0.1"
        print("Arch Games web GUI on http://{}:{}/".format(shown, port), flush=True)
        print(
            "Sign in (token file {}):\n  http://{}:{}/#token={}".format(
                config.config_dir / "web-token", shown, port, app.token
            ),
            flush=True,
        )
        if args.host not in ("127.0.0.1", "::1", "localhost"):
            print("WARNING: reachable from other machines; use only on a private network such as Tailscale.", file=sys.stderr)
        print("Press Ctrl+C to stop.", flush=True)
        try:
            app.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping web GUI.", file=sys.stderr)
        finally:
            app.stop()
        return
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
        from .diagnostics import doctor

        report = doctor.run(config, db, verbose=args.verbose, online=args.online)
        if args.redact:
            from .diagnostics.redact import redact

            paths = [(config.data_dir, "<data>"), (config.config_dir, "<config>"), (config.cache_dir, "<cache>"), (config.data_dir.parent, "<state>")]
            for c in report["checks"]:
                c["detail"] = redact(c["detail"], paths)
                c["fix"] = redact(c["fix"], paths) if c["fix"] else c["fix"]
        if args.json:
            emit(report)
            if report["summary"]["fail"]:
                raise AppError("{} check(s) failed".format(report["summary"]["fail"]))
            return
        for c in report["checks"]:
            print(clean("[{:4}] {}/{}: {}".format(c["status"].upper(), c["group"], c["check"], c["detail"])))
            if c["fix"]:
                print(clean("       fix: " + c["fix"]))
        print("Summary: {} ({} checks{})".format(json.dumps(report["summary"]), report["all_checks"], "" if args.verbose else "; --verbose shows all"))
        if report["summary"]["fail"]:
            raise AppError("{} check(s) failed".format(report["summary"]["fail"]))
        return
    if action == "repair":
        from .diagnostics import repair

        if args.apply:
            return emit(repair.apply(config, db, args.game))
        steps = repair.plan(config, db, args.game)
        if not steps:
            print("Nothing to repair for " + args.game)
        return emit({"dry_run": True, "steps": steps, "note": "Nothing was changed. Automatic steps run with --apply; others are commands for you to review."}) if steps else None
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
        from .compatibility import intelligence

        profile = apply_overrides(system_profile(config.data_dir), config)
        saved = save_feedback(db, args.game, args.result, profile, args.notes, args.launch, args.manual)
        intelligence.update(
            db, config, args.game, profile=profile,
            gameplay_status={"works": "works", "issues": "issues", "slow": "slow", "fails": "fails", "untested": "unverified"}[args.result],
            notes=args.notes or None,
        )
        return emit(saved)
    if action == "history":
        from .core import history as hist

        if not args.game:
            return emit([row["value"] for row in db.all("launches")][-max(1, args.limit):])
        return emit(hist.sessions(db, resolve(args.game))[-max(1, args.limit):])
    if action == "playtime":
        from .core import history as hist

        return emit(hist.playtime(db, resolve(args.game) if args.game else None))
    if action == "performance":
        from .performance import service

        game_id = resolve(args.game)
        rows = service.history(db, game_id)
        return emit({"game": game_id, "aggregate": service.aggregate(rows), "runs": rows[-20:],
                     "note": "Frame rate is reported only when a real measurement mechanism produced data; otherwise it is unavailable"})
    if action == "benchmark":
        from .performance import service
        from .runtime.launcher import Launcher

        game_id = resolve(args.game)
        if not args.yes:
            if not interactive(args):
                raise AppError("Benchmarks launch the game; add --yes to confirm (they never run automatically)")
            if input("Launch {} and measure it for {} s, then stop it? [y/N] ".format(clean(game_id), args.seconds)).strip().lower() != "y":
                return
        return emit(service.benchmark(config, db, Launcher(config, db), game_id, args.seconds, say=lambda t: print(clean(t), file=sys.stderr)))
    if action in {"title", "notes", "tag"}:
        from .core import personal

        if action == "tag" and args.operation == "list":
            rows = {r["key"]: r["value"].get("tags", []) for r in db.all("tags")}
            return emit(personal.get_tags(db, resolve(args.game)) if args.game else rows)
        game_id = resolve(args.game)
        if action == "title":
            if args.operation == "clear":
                personal.clear_title(db, game_id)
                print("Custom title removed")
                return
            personal.set_title(db, game_id, args.title)
            print("Custom title saved")
            return
        if action == "notes":
            if args.text is None:
                return emit({"text": personal.get_notes(db, game_id)})
            personal.set_notes(db, game_id, args.text)
            print("Notes saved")
            return
        return emit((personal.add_tag if args.operation == "add" else personal.remove_tag)(db, game_id, args.tag))
    if action == "launch":
        from .runtime import gamelaunch, native, profiles as launch_profiles
        from .runtime.launcher import Launcher
        from .runtime.launchers import LauncherStore

        custom_env = parse_env(args.env)
        pacman = pacman_service()
        launcher = Launcher(config, db, pacman=pacman)
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
            if args.method != "auto" or args.prefix or args.command or args.allow_external:
                raise AppError("--method/--prefix/--command/--allow-external need an explicit executable")
            plan = None
            record = LauncherStore(config, db).get(args.game)
            if record is None:
                print("No launcher registered for {}; searching installed files...".format(clean(args.game)), file=sys.stderr)
                result = gamelaunch.discover(config, db, args.game, catalog_title(config, db, args.game), pacman)
                for note in result["notes"]:
                    print(clean(note), file=sys.stderr)
                if result["runner"] == "native" and not result["installed"]:
                    raise AppError("{} is not installed. Install it with: arch-games install {}".format(result["package"], args.game))
                if result["runner"] == "native" and result["confidence"] == "high":
                    chosen = result["candidates"][0]  # single clear, package-owned launchable program
                    print("Using {} (owned by package {}).".format(chosen["path"], result["package"]), file=sys.stderr)
                else:
                    chosen = choose_candidate(result["candidates"], args)
                if result["runner"] == "native" and args.dry_run:
                    link, resolved = native.verify_candidate(pacman, result["package"], chosen["path"])
                    plan = launcher.plan(args.game, str(link), "native", None, args.arg, custom_env, None, False, cwd=str(Path.home()),
                                         allow_paths=(str(link), str(resolved)))
                    plan.update(registered=False, command_preview=launch_profiles.command_preview(plan["executable"], plan["arguments"], plan["custom_environment"], "native"),
                                note="Not registered yet: run without --dry-run to register this launcher and start the game.")
                else:
                    register_candidate(config, db, args.game, chosen)
                    print("Launcher registered.", file=sys.stderr)
            elif record.get("kind") == "native-package":
                state = gamelaunch.status(config, db, args.game, pacman)
                if state["status"] == native.INVALID:
                    print("{}: {}".format(state["label"], clean(state["detail"])), file=sys.stderr)
                    if interactive(args) and input("Rediscover the launcher now (your settings are kept)? [y/N] ").strip().lower() == "y":
                        from . import cli_launcher

                        cli_launcher.rediscover(argparse.Namespace(game=args.game, dir=[], non_interactive=args.non_interactive), config, db,
                                                {"choose_candidate": choose_candidate, "catalog_title": catalog_title}, pacman)
                    else:
                        raise AppError("Run: arch-games launcher rediscover {}".format(args.game))
            if plan is None:
                plan = launcher.plan_registered(args.game, args.arg, custom_env, profile=args.profile)
        if args.dry_run:
            return emit({k: v for k, v in plan.items() if k != "env"})
        plan["interactive"] = args.interactive
        monitor = None
        if args.monitor or config.values.get("performance_monitoring"):
            from .performance.service import monitor_factory

            monitor = monitor_factory()
        record = launcher.launch(plan, monitor=monitor)
        if monitor is not None and record.get("performance"):
            from .performance import service as perf_service

            perf_service.save(db, config, args.game, "monitor", record, apply_overrides(system_profile(config.data_dir), config))
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
        if args.operation == "restore" and args.apply:
            from .saves.service import SaveManager

            SaveManager(config, db).backup_if_known(args.game, "pre-prefix-change", say=lambda t: print(clean(t), file=sys.stderr))
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
        if args.operation == "remove":
            from .saves.service import SaveManager

            SaveManager(config, db).backup_if_known(args.game, "pre-prefix-change", say=lambda t: print(clean(t), file=sys.stderr))
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
        if args.operation == "pause":
            return emit(manager.pause(args.id))
        if args.operation == "priority":
            return emit(manager.set_priority(args.id, args.level))
        if args.operation == "dedupe":
            return emit(manager.dedupe(args.apply))
        if args.operation == "retry":
            return emit(manager.retry(args.id, args.restart))
        if args.operation == "cancel":
            manager.cancel(args.id, args.cleanup)
            print("Cancellation requested")
            return
        library = Library(config, db).load("gamevault", args.offline)
        return emit(
            manager.add(library.find(args.game, alternatives=True), args.sha256, args.max_gb * 1024**3, args.priority, args.force)
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
    if action == "install" and args.game.startswith("pacman:"):
        from .installation import native as native_install

        def pick(candidates):
            try:
                return choose_candidate(candidates, args)
            except AppError:
                return None  # not interactive: the report tells the user how to confirm

        report = native_install.install(
            config, db, args.game, args.game.split(":", 1)[1], catalog_title(config, db, args.game), pacman_service(),
            confirm=(lambda q: input(q + " [y/N] ").strip().lower() == "y") if interactive(args) else None,
            yes=args.yes, choose=pick if interactive(args) else None, say=lambda t: print(clean(t), file=sys.stderr),
        )
        emit(report)
        if report.get("message"):
            print(report["message"], file=sys.stderr)
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

        if not getattr(args, "plain", False):
            from .ui.tui.app import Unsupported, run as run_tui

            try:
                return run_tui(
                    config,
                    db,
                    lambda argv: main((["--state-dir", args.state_dir] if args.state_dir else []) + argv),
                    ascii_only=getattr(args, "ascii", False),
                    library=library,
                )
            except Unsupported:
                pass  # no interactive terminal: plain keyboard menu below

        menu(
            library,
            lambda argv: main((["--state-dir", args.state_dir] if args.state_dir else []) + argv),
            getattr(args, "plain", False),
        )
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
