"""Resolve a user-supplied game reference using the shared library; used by every command."""

import sys

from ..catalog.library import Library
from ..config import AppError
from .resolver import AmbiguousGameError, Resolver, collect_candidates


def build_resolver(config, db, offline=True, library=None):
    library = library or Library(config, db).load(offline=offline)
    return Resolver(collect_candidates(library.games, db), db), library


def choose(error, interactive):
    """Interactive disambiguation; non-interactive callers get the deterministic error."""
    if not interactive or not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise error
    for number, c in enumerate(error.candidates[:12], 1):
        print("{:2}. {}  ({})".format(number, c["title"], c["id"]), file=sys.stderr)
    answer = input("Select 1-{} (blank cancels): ".format(min(12, len(error.candidates)))).strip()
    if not answer.isdecimal() or not 1 <= int(answer) <= min(12, len(error.candidates)):
        raise AppError("No game selected")
    return error.candidates[int(answer) - 1]


def resolve_game_id(config, db, text, interactive=True, library=None, quiet=False, local=True):
    """Return the canonical game ID for text; announce non-exact matches on stderr.

    local=True (state commands) maps a versioned catalog ID onto the ID this machine already records state under
    (bare vs @version); local=False keeps the catalog ID for download/install planning."""
    from ..core.ids import local_id

    def finish(ident):
        mapped = local_id(db, ident) if local else ident
        if mapped != ident and not quiet:
            print("Using the local state recorded under {}".format(mapped), file=sys.stderr)
        return mapped

    if db.get("launchers", text) is not None or db.get("prefixes", text) is not None:
        return text
    resolver, _ = build_resolver(config, db, library=library)
    try:
        found, how = resolver.resolve(text)
    except AmbiguousGameError as exc:
        found, how = choose(exc, interactive), "selected"
    if how not in {"id", "package"} and not quiet:
        print("Resolved '{}' -> {} [{}] ({} match)".format(text, found["title"], found["id"], how), file=sys.stderr)
    return finish(found["id"])
