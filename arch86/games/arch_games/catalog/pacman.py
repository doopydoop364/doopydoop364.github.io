"""Query configured repositories once; never refresh package databases implicitly."""

from pathlib import Path
import re
import subprocess

from ..models import Game

PACKAGE = re.compile(r"[a-zA-Z0-9@_+][a-zA-Z0-9@._+\-]*\Z")


def parse_catalog(text):
    games = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|", 4)]
        if len(parts) != 5 or not PACKAGE.fullmatch(parts[1]):
            continue
        title, package, category, art, description = parts
        games.append(
            Game(
                "pacman:" + package,
                title,
                package=package,
                category=category,
                art=art,
                description=description,
                os="linux",
            )
        )
    return games


def load_catalog(path=None):
    path = Path(path) if path else Path(__file__).parents[1] / "catalog.txt"
    return parse_catalog(path.read_text(encoding="utf-8"))


def default_run():
    """The subprocess runner used for Pacman queries (a hook so tests can substitute a fake package database)."""
    return subprocess.run


def query_packages(flag, run=None):
    run = run or default_run()
    try:
        result = run(["pacman", flag], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return set(), "Pacman is unavailable; use this command on an Arch Linux machine."
    if result.returncode:
        return set(), "Pacman database query failed; check configured repositories."
    return set(result.stdout.splitlines()), ""


def visible(games=None, run=None):
    run = run or default_run()
    available, error = query_packages("-Slq", run)
    owned, installed_error = query_packages("-Qq", run)
    selected = []
    for game in games if games is not None else load_catalog():
        if game.package in available or game.package in owned:
            game.installed = game.package in owned
            selected.append(game)
    return selected, error or installed_error
