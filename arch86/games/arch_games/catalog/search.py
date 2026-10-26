"""Fast library search over names, aliases, tags, source, platform, installed state and compatibility."""

from ..core.ids import base
from ..identity.resolver import normalize


class SearchIndex:
    """Haystacks are built once per load, so repeated queries are a linear scan over short strings."""

    def __init__(self, library, db):
        aliases, tags = {}, {}
        for row in db.all("aliases"):
            aliases.setdefault(row["value"]["game_id"], []).append(normalize(row["value"]["alias"]))
        for row in db.all("tags"):
            tags[row["key"]] = [normalize(t) for t in row["value"].get("tags", [])]
        launchers = set(db.mapping("launchers"))
        prefixes = set(db.mapping("prefixes"))
        favorites = set(db.mapping("favorites"))
        fam = lambda ident, group: ident in group or base(ident) in group  # noqa: E731  (bare and versioned IDs are one game)
        self.rows = []
        for g in library.games:
            report = library.reports.get(g.id, {})
            title = normalize(g.title)
            g_aliases = aliases.get(g.id, []) + (aliases.get(base(g.id), []) if "@" in g.id else [])
            g_tags = tags.get(g.id) or tags.get(base(g.id)) or []
            self.rows.append({
                "game": g, "title": title, "aliases": g_aliases, "tags": g_tags,
                "source": g.source, "platform": g.os, "installed": bool(g.installed) if g.id.startswith("pacman:") else bool(g.installed or fam(g.id, launchers) or fam(g.id, prefixes)),
                "classification": report.get("classification", "Unknown"), "favorite": fam(g.id, favorites),
                "blob": " ".join([title, g.id.casefold(), normalize(g.category), *g_aliases, *g_tags]),
            })

    def search(self, query="", source=None, platform=None, installed=None, compat=None, tag=None, favorite=None, limit=50):
        terms = normalize(query).split()
        tag_key = normalize(tag) if tag else None
        out = []
        for r in self.rows:
            if source and r["source"] != source:
                continue
            if platform and r["platform"] != platform:
                continue
            if installed is not None and r["installed"] != installed:
                continue
            if compat and r["classification"].casefold() != compat.casefold():
                continue
            if tag_key and tag_key not in r["tags"]:
                continue
            if favorite is not None and r["favorite"] != favorite:
                continue
            if terms and not all(t in r["blob"] for t in terms):
                continue
            q = " ".join(terms)
            rank = 0 if not terms else 0 if r["title"] == q or q in r["aliases"] else 1 if r["title"].startswith(q) else 2 if q in r["title"] else 3
            out.append((rank, r["title"], r))
        out.sort(key=lambda x: (x[0], x[1]))
        return [x[2] for x in out[:limit]]
