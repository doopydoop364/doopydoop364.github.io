"""Catalog filtering explains exclusions; deduplication needs explicit identity."""
import copy


def merge_catalogs(*catalogs):
    result, identifiers, canonical = [], set(), {}
    for catalog in catalogs:
        for original in catalog:
            if original.id in identifiers:
                continue
            identifiers.add(original.id)
            game = copy.deepcopy(original)
            # Titles alone never establish equivalence. canonical_id is a local/user assertion.
            identity = game.metadata.get("canonical_id")
            key = (identity, game.os, game.architecture, game.edition, game.build)
            if identity and game.os != "unknown" and game.build and key in canonical:
                winner = canonical[key]
                winner.metadata.setdefault("alternatives", []).append(game.to_dict())
                winner.installed = winner.installed or game.installed
            else:
                result.append(game)
                if identity and game.os != "unknown" and game.build:
                    canonical[key] = game
    return result


def select(games, reports=None, search="", category="", source="all", installed=False,
           wine=False, verified=False, compatible=False, show_all=False, sort="title"):
    reports = reports or {}
    output, hidden = [], 0
    term = search.casefold()
    for game in games:
        report = reports.get(game.id, {})
        status = report.get("status", "Unknown")
        if (source != "all" and game.source != source) or (installed and not game.installed):
            continue
        if term not in (game.title + " " + game.description + " " + game.category).casefold():
            continue
        if category and game.category.casefold() != category.casefold():
            continue
        if wine and "Wine" not in report.get("runtime", ""):
            continue
        if verified and status != "Verified Working":
            continue
        if compatible and status not in {"Promising", "Verified Working"}:
            hidden += 1
            continue
        if not show_all and game.source == "gamevault" and status not in {"Promising", "Verified Working"}:
            hidden += 1
            continue
        output.append(game)
    keys = {"title": lambda g: (g.title.casefold(), g.id), "category": lambda g: (g.category, g.title.casefold()),
            "source": lambda g: (g.source, g.title.casefold()),
            "status": lambda g: (reports.get(g.id, {}).get("status", "Unknown"), g.title.casefold())}
    output.sort(key=keys.get(sort, keys["title"]))
    explanation = "{} entries hidden by compatibility filters; use --all to inspect unknown/unverified/incompatible games".format(hidden)
    return output, explanation
