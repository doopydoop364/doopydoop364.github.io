"""Personal library state (favorites, notes, tags, custom titles): one implementation for the CLI, TUI and web UI."""

from datetime import datetime, timezone
import re

from ..config import AppError

TAG = re.compile(r"^[\w .+-]{1,32}$")
MAX_NOTES = 8000


def _now():
    return datetime.now(timezone.utc).isoformat()


def is_favorite(db, game_id):
    return db.get("favorites", game_id) is not None


def set_favorite(db, game_id, value):
    if value:
        db.put("favorites", game_id, {"added": _now()})
    else:
        db.delete("favorites", game_id)
    return bool(value)


def toggle_favorite(db, game_id):
    return set_favorite(db, game_id, not is_favorite(db, game_id))


def get_notes(db, game_id):
    return (db.get("notes", game_id) or {}).get("text", "")


def set_notes(db, game_id, text):
    if not isinstance(text, str) or len(text) > MAX_NOTES:
        raise AppError("Notes must be text up to {} characters".format(MAX_NOTES))
    db.put("notes", game_id, {"text": text})


def get_tags(db, game_id):
    return list((db.get("tags", game_id) or {}).get("tags", []))


def set_tags(db, game_id, tags):
    if not isinstance(tags, list) or len(tags) > 32 or not all(isinstance(t, str) and TAG.match(t.strip()) for t in tags):
        raise AppError("Tags are up to 32 short words (letters, digits, spaces or . + -)")
    clean = sorted({t.strip() for t in tags})
    db.put("tags", game_id, {"tags": clean})
    return clean


def add_tag(db, game_id, tag):
    return set_tags(db, game_id, get_tags(db, game_id) + [tag])


def remove_tag(db, game_id, tag):
    return set_tags(db, game_id, [t for t in get_tags(db, game_id) if t != tag.strip()])


def set_title(db, game_id, title):
    title = (title or "").strip()
    if not title or len(title) > 200 or "\0" in title:
        raise AppError("Title must be 1..200 characters")
    db.put("custom_titles", game_id, {"title": title})


def clear_title(db, game_id):
    db.delete("custom_titles", game_id)
