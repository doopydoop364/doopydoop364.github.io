"""Central game-identity resolution: IDs, aliases, titles and conservative fuzzy matching.

Order: exact ID -> exact alias -> exact title -> case-insensitive title -> normalized title
-> unique high-confidence fuzzy match. Several equally good matches are an ambiguity, never a guess.
"""

from difflib import SequenceMatcher
import re
import unicodedata

from ..config import AppError

ALIASES = "aliases"
FUZZY_RATIO = 0.88


class AmbiguousGameError(AppError):
    def __init__(self, query, candidates):
        self.query, self.candidates = query, candidates
        lines = ["'{}' matches several games; use one of these IDs:".format(query)]
        lines += ["  {}  {}".format(c["id"], c["title"]) for c in candidates[:12]]
        super().__init__("\n".join(lines))


class UnknownGameError(AppError):
    def __init__(self, query, suggestions):
        self.query, self.suggestions = query, suggestions
        text = "No game matches '{}'.".format(query)
        if suggestions:
            text += " Did you mean: " + ", ".join("{} ({})".format(s["title"], s["id"]) for s in suggestions[:5])
        super().__init__(text)


def normalize(text):
    text = unicodedata.normalize("NFKD", str(text)).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]|_", "", text)
    return " ".join(text.split())


def alias_key(text):
    key = normalize(text)
    if not key:
        raise AppError("Alias must contain letters or digits")
    return key


class Candidate(dict):
    """{"id","title","source"} for a resolvable game."""


def collect_candidates(games, db):
    """Library games (and their alternatives) plus anything with local records."""
    seen, result = {}, []

    def add(ident, title, source, extra=()):
        if ident in seen:
            return
        seen[ident] = Candidate(id=ident, title=title or ident, source=source, extra=list(extra))
        result.append(seen[ident])

    for game in games:
        add(game.id, game.title, game.source, [game.package] if game.package else [])
        for alt in game.metadata.get("alternatives", []) or []:
            if isinstance(alt, dict) and alt.get("id"):
                add(alt["id"], alt.get("title") or game.title, alt.get("source", game.source), [alt.get("package", "")])
    for ident, manifest in db.mapping("installs").items():
        game = manifest.get("game") or {}
        add(ident, game.get("title"), game.get("source", "local"))
    versioned = {ident.split("@", 1)[0] for ident in seen if "@" in ident}
    for ident, record in db.mapping("launchers").items():
        if ident not in versioned:  # a versioned library entry already represents this game
            add(ident, record.get("title"), "local")
    for ident in db.mapping("prefixes"):
        if ident not in versioned:
            add(ident, None, ident.split(":", 1)[0])
    return result


class Resolver:
    def __init__(self, candidates, db):
        self.candidates, self.db = candidates, db
        self.by_id = {c["id"]: c for c in candidates}
        self.aliases = {k: v["game_id"] for k, v in db.mapping(ALIASES).items()}

    def _titles(self, c):
        return [c["title"]] + [x for x in c["extra"] if x]

    def resolve(self, query):
        query = (query or "").strip()
        if not query or len(query) > 256 or "\0" in query:
            raise AppError("Game name must be 1..256 characters")
        if query in self.by_id:
            return self.by_id[query], "id"
        for c in self.candidates:
            if query in c["extra"]:
                return c, "package"
        key = normalize(query)
        target = self.aliases.get(alias_key(query)) if key else None
        if target:
            if target in self.by_id:
                return self.by_id[target], "alias"
            return Candidate(id=target, title=target, source=target.split(":", 1)[0], extra=[]), "alias"
        for stage, same in (
            ("title", lambda t: t == query),
            ("title-casefold", lambda t: t.casefold() == query.casefold()),
            ("title-normalized", lambda t: normalize(t) == key),
        ):
            hits = self._dedupe(c for c in self.candidates if any(same(t) for t in self._titles(c)))
            if len(hits) == 1:
                return hits[0], stage
            if hits:
                raise AmbiguousGameError(query, hits)
        hits = self._fuzzy(key)
        if len(hits) == 1:
            return hits[0], "fuzzy"
        if hits:
            raise AmbiguousGameError(query, hits)
        raise UnknownGameError(query, self.suggest(query))

    @staticmethod
    def _dedupe(items):
        seen, out = set(), []
        for c in items:
            if c["id"] not in seen:
                seen.add(c["id"])
                out.append(c)
        return out

    def _score(self, key, c):
        best = 0.0
        for title in self._titles(c):
            t = normalize(title)
            if not t or not key:
                continue
            ratio = SequenceMatcher(None, key, t).ratio()
            words = t.split()
            if key.split() and all(w in words for w in key.split()):
                # whole-word containment ("undertale" in "undertale gog 1 08"), weaker than equality
                ratio = max(ratio, 0.9 * len(key) / max(len(t), 1) + 0.5)
                ratio = min(ratio, 0.97)
            best = max(best, ratio)
        return best

    def _fuzzy(self, key):
        if len(key) < 3:
            return []
        # Whole-word containment ("undertale" in "Undertale GOG 1.08") must be unique, otherwise ambiguous.
        contained = self._dedupe(c for c in self.candidates if self._contained(key, c))
        if contained:
            return contained
        scored = sorted(((self._score(key, c), c) for c in self.candidates), key=lambda x: -x[0])
        good = [(s, c) for s, c in scored if s >= FUZZY_RATIO]
        if not good:
            return []
        # Anything within 0.05 of the best match competes with it; a single clear winner is required.
        return self._dedupe(c for s, c in good if good[0][0] - s < 0.05)

    def _contained(self, key, c):
        words = key.split()
        return any(all(w in normalize(t).split() for w in words) for t in self._titles(c))

    def suggest(self, query, limit=5):
        key = normalize(query)
        scored = sorted(((self._score(key, c), c) for c in self.candidates), key=lambda x: -x[0])
        return [c for s, c in scored if s >= 0.5][:limit]

    def search(self, query, limit=20):
        key = normalize(query)
        if not key:
            return list(self.candidates[:limit])
        hits = [c for c in self.candidates if any(key in normalize(t) for t in self._titles(c)) or key in c["id"].casefold()]
        return hits[:limit] if hits else self.suggest(query, limit)


def add_alias(db, resolver, alias, target, force=False):
    key = alias_key(alias)
    if alias.strip() in resolver.by_id:
        raise AppError("Alias would shadow an existing game ID")
    if target not in resolver.by_id:
        known = [c for c in resolver.candidates if c["id"] == target]
        if not known:
            raise AppError("Unknown game ID '{}'; aliases point to canonical IDs".format(target))
    existing = db.get(ALIASES, key)
    if existing and existing["game_id"] != target and not force:
        raise AppError("Alias '{}' already points to {}; use --force to replace".format(alias, existing["game_id"]))
    db.put(ALIASES, key, {"alias": alias.strip(), "game_id": target})
    return {"alias": alias.strip(), "normalized": key, "game_id": target}


def remove_alias(db, alias):
    key = alias_key(alias)
    if db.get(ALIASES, key) is None:
        raise AppError("No such alias")
    db.delete(ALIASES, key)
