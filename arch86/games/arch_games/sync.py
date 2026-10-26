"""Private, file-based synchronisation bundle: favorites, aliases, notes, tags, custom titles, compatibility records and
installation metadata. No network, no credentials, no paths, no game files. Newest-write wins per item."""

from datetime import datetime, timezone
import re

from .compatibility import intelligence
from .config import AppError

SCHEMA = 1
GAME_ID = re.compile(r"^[a-z0-9_-]{1,24}:[A-Za-z0-9._@-]{1,100}$")
TEXT_NAMESPACES = {
    "favorites": lambda v: {},
    "aliases": lambda v: {"alias": str(v["alias"])[:80], "game_id": str(v["game_id"])},
    "notes": lambda v: {"text": str(v.get("text", ""))[:8000]},
    "tags": lambda v: {"tags": [str(t)[:32] for t in v.get("tags", [])][:32]},
    "custom_titles": lambda v: {"title": str(v["title"])[:200]},
    "interests": lambda v: {"keywords": [str(k)[:40] for k in v.get("keywords", [])][:50]},
}


def export(db, config):
    data = {"schema": SCHEMA, "exported_at": datetime.now(timezone.utc).isoformat(), "machine": intelligence.machine_identity(config), "items": {}}
    for ns in TEXT_NAMESPACES:
        data["items"][ns] = [{"key": r["key"], "value": r["value"], "updated": r["updated"]} for r in db.all(ns)]
    data["items"]["compat"] = [intelligence.shareable(r) for r in intelligence.all_records(db, intelligence.machine_identity(config)["id"])]
    # Installation metadata: what was installed and how, never where.
    data["items"]["installs"] = [
        {"key": r["key"], "value": {"game_id": r["key"], "title": (r["value"].get("game") or {}).get("title"), "archive_sha256": r["value"].get("archive_sha256")}, "updated": r["updated"]}
        for r in db.all("installs")
    ]
    return data


def parse_time(value):
    try:
        t = datetime.fromisoformat(value)
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def import_bundle(db, config, data, prefer_remote=False):
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("items"), dict):
        raise AppError("Unrecognised sync bundle")
    report = {"merged": {}, "kept_local": {}, "skipped": 0}
    for ns, clean in TEXT_NAMESPACES.items():
        merged = kept = 0
        existing = {r["key"]: r for r in db.all(ns)}
        for item in data["items"].get(ns, [])[:20000]:
            try:
                key = str(item["key"])
                if ns != "interests" and ns != "aliases" and not GAME_ID.match(key):
                    raise ValueError
                value = clean(item["value"])
                if ns == "aliases":
                    from .identity.resolver import alias_key

                    if alias_key(value["alias"]) != key or not GAME_ID.match(value["game_id"]):
                        raise ValueError
            except (KeyError, TypeError, ValueError, AppError):
                report["skipped"] += 1
                continue
            local = existing.get(key)
            if ns == "favorites" and local:
                kept += 1
                continue  # union: nothing to change
            if local:
                if ns == "aliases" and local["value"]["game_id"] != value["game_id"] and not prefer_remote:
                    kept += 1  # never silently repoint an alias
                    continue
                if parse_time(item.get("updated")) <= parse_time(local["updated"]) and not prefer_remote:
                    kept += 1
                    continue
            db.put(ns, key, value)
            merged += 1
        report["merged"][ns], report["kept_local"][ns] = merged, kept
    report["merged"]["compat"] = intelligence.import_records(db, config, {"schema": 1, "records": data["items"].get("compat", [])})["merged"]
    return report
