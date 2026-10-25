"""Command entry point; extended incrementally without changing old launch aliases."""
import argparse
import getpass
import json
import sys

from .config import AppError, Config, server_url
from .gamevault.client import Client
from .storage.database import Database

from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(prog="arch-games")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("action", nargs="?", default="menu",
                        choices=["menu", "web", "list", "info", "install", "gamevault"])
    parser.add_argument("package", nargs="?")
    parser.add_argument("rest", nargs="*")
    parser.add_argument("--state-dir")
    parser.add_argument("--url")
    parser.add_argument("--allow-http", action="store_true")
    parser.add_argument("--api-key", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "gamevault":
        try:
            return gamevault_command(args)
        except (AppError, OSError) as exc:
            print("Error: " + str(exc), file=sys.stderr)
            return 1
    from . import legacy
    games = legacy.visible()
    if args.action == "menu":
        legacy.menu(games)
    elif args.action == "web":
        legacy.gallery(games)
    elif args.action == "list":
        for game in games:
            print("{}\t{}".format(game["pkg"], game["title"]))
    elif args.action == "info":
        print("System: {}, visible: {}, catalog: {}".format(legacy.ARCH, len(games), len(legacy.CATALOG)))
    elif args.action == "install":
        game = next((g for g in games if g["pkg"] == args.package), None)
        if not game:
            parser.error("Package is not available in this VM's repositories")
        legacy.install(game)
    return 0


def gamevault_command(args):
    config = Config(args.state_dir)
    db = Database(config.data_dir / "state.db")
    try:
        action = args.package or "status"
        if action == "disconnect":
            config.disconnect()
            print("Local credentials removed. Server session expires according to server policy.")
            return 0
        if action == "connect":
            value = args.url or input("GameVault server URL: ").strip()
            config.values.update(server_url=server_url(value, args.allow_http), allow_http=args.allow_http)
            config.values["account"] = input("Account name (cache identity): ").strip()
            client = Client(config, db)
            if args.api_key:
                key = getpass.getpass("API key: ")
                if not key:
                    raise AppError("API key cannot be empty")
                config.save_credentials({"api_key": key})
                try:
                    client.status()
                except AppError:
                    config.disconnect()
                    raise
            else:
                client.login(config.values["account"], getpass.getpass("Password: "))
            config.save()
            print("Connected; credentials saved privately.")
            return 0
        client = Client(config, db)
        if action == "status":
            print(json.dumps(client.status(), indent=2))
        elif action in {"list", "search"}:
            games, state = client.library(offline=args.offline)
            print(json.dumps(state))
            term = " ".join(args.rest).casefold() if action == "search" else ""
            for game in games:
                if term in (game.title + " " + game.description).casefold():
                    print(game.id, game.title)
        elif action == "details" and args.rest:
            game, cached = client.details(args.rest[0], args.offline)
            print(json.dumps({"game": game.to_dict(), "cached": cached}, indent=2))
        else:
            raise AppError("Usage: gamevault connect|status|list|search QUERY|details ID|disconnect")
        return 0
    finally:
        db.close()
