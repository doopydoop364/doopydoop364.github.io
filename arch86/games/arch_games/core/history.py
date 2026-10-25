"""Launch history, playtime estimates, recency. Timestamps are ISO-8601 UTC with an explicit +00:00 offset."""

from datetime import datetime, timezone

MAX_SESSION = 12 * 3600
MIN_SESSION = 5


def parse(ts):
    try:
        value = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)  # naive legacy values are treated as UTC


def sessions(db, game_id=None):
    """Play sessions only: real game launches (not installers or supervised test launches)."""
    out = []
    for v in db.mapping("launches").values():
        if v.get("purpose", "game") != "game" or (game_id and v.get("game_id") != game_id):
            continue
        start, end = parse(v.get("started_at")), parse(v.get("finished_at"))
        if not start:
            continue
        seconds = (end - start).total_seconds() if end else None
        counted = v.get("state") == "exited" and seconds is not None and seconds >= MIN_SESSION and not (v.get("exit_code") and seconds < 60)
        out.append({"id": v.get("id"), "game_id": v.get("game_id"), "started_at": start.isoformat(), "seconds": seconds, "state": v.get("state"),
                    "exit_code": v.get("exit_code"), "counted": counted})
    return sorted(out, key=lambda s: s["started_at"])


def playtime(db, game_id=None):
    by = {}
    for s in sessions(db, game_id):
        row = by.setdefault(s["game_id"], {"game_id": s["game_id"], "seconds": 0, "sessions": 0, "counted_sessions": 0, "last_played": None})
        row["sessions"] += 1
        row["last_played"] = max(row["last_played"] or "", s["started_at"])
        if s["counted"]:
            row["counted_sessions"] += 1
            row["seconds"] += min(s["seconds"], MAX_SESSION)
    for row in by.values():
        row["hours"] = round(row["seconds"] / 3600, 2)
        row["note"] = "Estimate: wall-clock time the game process ran; idle time and pauses are included"
    return by


def recent(db, limit=8):
    seen, out = set(), []
    for s in reversed(sessions(db)):
        if s["game_id"] not in seen:
            seen.add(s["game_id"])
            out.append(s)
        if len(out) >= limit:
            break
    return out
