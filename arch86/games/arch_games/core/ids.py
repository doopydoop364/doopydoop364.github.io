"""Bare (gamevault:32) and versioned (gamevault:32@34) IDs name the same game for everything that lives on this machine.

2.0 let a prefix be created under either spelling, while the GameVault library expands a game into versioned IDs. These helpers
make local-state lookups (prefixes, launchers, installs, favorites, notes...) find the record whichever spelling was used,
and say which ID an operation should record new state under so one game never ends up with two prefixes."""

STATE_NAMESPACES = ("launchers", "prefixes", "installs")


def base(game_id):
    return str(game_id).split("@", 1)[0]


def has_state(db, game_id):
    return any(db.get(ns, game_id) is not None for ns in STATE_NAMESPACES)


def local_id(db, game_id):
    """The ID under which this game's local state is (or should be) recorded: the ID itself when it has state, else its
    bare ID when that has state, else the single versioned sibling that has state, else the ID unchanged."""
    if has_state(db, game_id):
        return game_id
    root = base(game_id)
    if root != game_id:
        return root if has_state(db, root) else game_id
    siblings = {key for ns in STATE_NAMESPACES for key in db.mapping(ns) if key.startswith(root + "@")}
    return siblings.pop() if len(siblings) == 1 else game_id


def family_get(db, namespace, game_id, default=None):
    """db.get that also looks under the bare ID."""
    value = db.get(namespace, game_id)
    if value is None and "@" in str(game_id):
        value = db.get(namespace, base(game_id))
    return default if value is None else value


def family_lookup(mapping, game_id):
    """Same for an already-loaded {id: record} mapping."""
    value = mapping.get(game_id)
    return mapping.get(base(game_id)) if value is None and "@" in str(game_id) else value
