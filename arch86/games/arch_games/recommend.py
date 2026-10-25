"""Explainable, local recommendations from your own library. Compatibility and 'likely to enjoy' are separate answers."""

import math

from .core import history

BLOCKED = {"Unsupported", "Likely incompatible"}


def interests(db):
    return sorted((db.get("interests", "list") or {}).get("keywords", []))


def set_interests(db, keywords):
    clean = sorted({k.strip().casefold() for k in keywords if k.strip()})
    if any(len(k) > 40 for k in clean) or len(clean) > 50:
        raise ValueError("Interests are up to 50 keywords of at most 40 characters")
    db.put("interests", "list", {"keywords": clean})
    return clean


def recommend(library, db, limit=10):
    play = history.playtime(db)
    favorites = set(db.mapping("favorites"))
    tags = {r["key"]: r["value"].get("tags", []) for r in db.all("tags")}
    wanted = interests(db)
    by_id = {g.id: g for g in library.games}
    weight, why_cat = {}, {}

    def bump(category, amount, reason):
        weight[category] = weight.get(category, 0) + amount
        why_cat.setdefault(category, []).append(reason)

    for gid, g in by_id.items():
        if g.category in {"", "Other"}:
            continue
        if gid in favorites:
            bump(g.category, 3.0, "you favorited {}".format(g.title))
        hours = play.get(gid, {}).get("seconds", 0) / 3600
        if hours >= 0.25:
            bump(g.category, min(4.0, math.log2(1 + hours)), "you played {} for {:.1f} h".format(g.title, hours))
    ram = library.profile.get("ram_total") or 0
    results = []
    for g in library.games:
        if play.get(g.id, {}).get("counted_sessions") or g.id in favorites:
            continue  # already known to you
        report = library.reports.get(g.id, {})
        compat = report.get("classification", "Unknown")
        if compat in BLOCKED:
            continue
        score, reasons = 0.0, []
        if weight.get(g.category):
            score += weight[g.category]
            reasons.append("category '{}' ({})".format(g.category, "; ".join(why_cat[g.category][:2])))
        text = (g.title + " " + g.category + " " + g.description + " " + " ".join(tags.get(g.id, []))).casefold()
        hits = [k for k in wanted if k in text]
        if hits:
            score += 2.0 * len(hits)
            reasons.append("matches your interests: " + ", ".join(hits))
        shared = {t for t in tags.get(g.id, [])} & {t for gid in favorites for t in tags.get(gid, [])}
        if shared:
            score += 1.0 * len(shared)
            reasons.append("shares tags with your favorites: " + ", ".join(sorted(shared)))
        if score <= 0:
            continue
        if g.installed:
            score += 0.5
            reasons.append("already installed")
        if compat in {"Verified working", "Likely compatible"}:
            score += 0.25
        results.append({
            "game_id": g.id, "title": g.title, "category": g.category, "enjoyment_score": round(score, 2), "reasons": reasons,
            "compatibility": compat, "compatibility_reasons": report.get("classification_reasons", []),
            "hardware": {"graphics": report.get("graphics"), "memory": report.get("memory")},
        })
    results.sort(key=lambda r: (-r["enjoyment_score"], r["title"].casefold()))
    return {"recommendations": results[:limit], "basis": {"favorites": len(favorites), "played_games": len(play), "interests": wanted, "ram_total": ram},
            "note": "Scores come only from your own favorites, playtime, tags and interests. Compatibility is reported separately and does not imply you will enjoy a game."}
