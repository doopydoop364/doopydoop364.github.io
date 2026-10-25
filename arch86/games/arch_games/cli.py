"""Command entry point; extended incrementally without changing old launch aliases."""
import argparse

from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(prog="arch-games")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("action", nargs="?", default="menu",
                        choices=["menu", "web", "list", "info", "install"])
    parser.add_argument("package", nargs="?")
    args = parser.parse_args(argv)
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
