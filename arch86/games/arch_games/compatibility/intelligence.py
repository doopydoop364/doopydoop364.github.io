"""Evidence facts, per-machine compatibility records and the user-facing classification.

Facts stay separate (installer vs installed game architecture, runtime needs, graphics, host capability, user-confirmed
gameplay, audio). The classification is derived from them and always carries reasons; installing or starting a game never
by itself produces 'Verified working'.
"""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import platform
import uuid

from ..config import AppError

NAMESPACE = "compat"
CLASSES = ("Verified working", "Working with issues", "Likely compatible", "Requires configuration", "Likely incompatible", "Unsupported", "Unknown")
GAMEPLAY = {"unverified", "works", "issues", "slow", "fails"}
AUDIO = {"unknown", "works", "issues", "none"}
LAUNCH = {"never", "started", "exited_ok", "exited_error", "failed"}
INSTALL = {"unknown", "not_installed", "installed", "failed"}


def now():
    return datetime.now(timezone.utc).isoformat()


# -- machine identity ---------------------------------------------------------------------------------------------

def os_release(root="/"):
    values = {}
    try:
        for line in (Path(root) / "etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                values[k] = v.strip().strip('"')
    except OSError:
        pass
    return values


def machine_identity(config, root="/", arch=None):
    """{id, name, architecture, distribution}. The id is a local hash; nothing here is transmitted."""
    from ..platform import architecture

    arch = arch or architecture()
    release = os_release(root)
    distro = release.get("ID", "unknown")
    if distro == "archlinux32" or (distro == "arch" and arch == "i686"):
        guess = "arch86"
    elif distro == "arch" and arch == "x86_64":
        guess = "arch64"
    elif distro == "cachyos":
        guess = "cachyos"
    else:
        guess = "{}-{}".format(distro, arch)
    name = config.values.get("machine_name") or guess
    source = ""
    try:
        source = (Path(root) / "etc/machine-id").read_text().strip()
    except OSError:
        pass
    if not source:
        path = config.data_dir / "machine-id"
        if not path.exists():
            path.write_text(uuid.uuid4().hex + "\n")
            path.chmod(0o600)
        source = path.read_text().strip()
    mid = hashlib.sha256(("arch-games:" + source).encode()).hexdigest()[:16]
    return {"id": mid, "name": name, "architecture": arch, "distribution": distro}


def set_machine_name(config, name):
    import re

    if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}", name or ""):
        raise AppError("Machine name must be 1..32 letters, digits, . _ -")
    config.values["machine_name"] = name
    config.save()


# -- records ------------------------------------------------------------------------------------------------------------

def key(machine_id, game_id):
    return "{}:{}".format(machine_id, game_id)


def get(db, machine_id, game_id):
    return db.get(NAMESPACE, key(machine_id, game_id))


def blank(game_id, title, machine, platform_name="unknown"):
    return {
        "schema": 1, "game_id": game_id, "title": title, "platform": platform_name,
        "machine": {k: machine[k] for k in ("id", "name", "architecture")},
        "runtime": {}, "graphics": {},
        "installation_status": "unknown", "launch_status": "never", "audio_status": "unknown", "gameplay_status": "unverified",
        "known_issues": [], "evidence_times": {}, "notes": "", "updated_at": now(),
    }


def update(db, config, game_id, title=None, platform_name=None, profile=None, issue=None, notes=None, **fields):
    """Merge statuses into this machine's record; each changed status gets its own evidence timestamp."""
    machine = machine_identity(config)
    record = get(db, machine["id"], game_id) or blank(game_id, title or game_id, machine)
    allowed = {"installation_status": INSTALL, "launch_status": LAUNCH, "audio_status": AUDIO, "gameplay_status": GAMEPLAY}
    for field, value in fields.items():
        if field not in allowed or value not in allowed[field]:
            raise AppError("Invalid compatibility value for {}: {}".format(field, value))
        record[field] = value
        record["evidence_times"][field.replace("_status", "")] = now()
    if title:
        record["title"] = title
    if platform_name:
        record["platform"] = platform_name
    if profile:
        wine = profile.get("wine") or {}
        graphics = profile.get("graphics") or {}
        record["runtime"] = {"wine_version": wine.get("version") or None, "wine_path": wine.get("path") or None}
        record["graphics"] = {"renderer": graphics.get("renderer"), "accelerated": graphics.get("accelerated")}
    if issue:
        if len(issue) > 500:
            raise AppError("Issue text is limited to 500 characters")
        record["known_issues"].append({"text": issue, "at": now()})
        record["known_issues"] = record["known_issues"][-50:]
    if notes is not None:
        if len(notes) > 4000:
            raise AppError("Notes are limited to 4000 characters")
        record["notes"] = notes
    record["updated_at"] = now()
    db.put(NAMESPACE, key(machine["id"], game_id), record)
    return record


def all_records(db, machine_id=None):
    rows = [r["value"] for r in db.all(NAMESPACE)]
    return [r for r in rows if machine_id in (None, "all") or r["machine"]["id"] == machine_id]


def export(db, config):
    return {"schema": 1, "exported_at": now(), "machine": machine_identity(config), "records": all_records(db)}


def import_records(db, config, data):
    """Merge records exported from another machine (newest update wins). Never touches this machine's own records."""
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("records"), list):
        raise AppError("Unrecognised compatibility export")
    local = machine_identity(config)["id"]
    merged = skipped = 0
    for rec in data["records"][:20000]:
        try:
            machine_id, game_id = rec["machine"]["id"], rec["game_id"]
            if not isinstance(machine_id, str) or not isinstance(game_id, str) or len(machine_id) > 64 or len(game_id) > 200:
                raise KeyError
            for field, allowed in (("installation_status", INSTALL), ("launch_status", LAUNCH), ("audio_status", AUDIO), ("gameplay_status", GAMEPLAY)):
                if rec.get(field) not in allowed:
                    raise KeyError(field)
        except (KeyError, TypeError):
            skipped += 1
            continue
        if machine_id == local:
            skipped += 1
            continue
        current = get(db, machine_id, game_id)
        if current is None or str(rec.get("updated_at", "")) > str(current.get("updated_at", "")):
            clean = {k: rec[k] for k in blank(game_id, "", rec["machine"]) if k in rec}
            clean["machine"] = {k: str(rec["machine"].get(k, ""))[:64] for k in ("id", "name", "architecture")}
            clean["known_issues"] = [{"text": str(i.get("text", ""))[:500], "at": str(i.get("at", ""))[:40]} for i in rec.get("known_issues", [])[:50] if isinstance(i, dict)]
            db.put(NAMESPACE, key(machine_id, game_id), clean)
            merged += 1
        else:
            skipped += 1
    return {"merged": merged, "skipped": skipped}


# -- facts -----------------------------------------------------------------------------------------------------------------

def facts(game, report, binary, installer, profile, record, others):
    imports = set((binary or {}).get("imports", []))
    directx = next((v for v, dll in (("12", "d3d12.dll"), ("11", "d3d11.dll"), ("9", "d3d9.dll")) if dll in imports), None)
    wine = profile.get("wine") or {}
    graphics = profile.get("graphics") or {}
    return {
        "installer": {"architecture": (installer or {}).get("architecture"), "observed": bool(installer), "note": "Installer architecture says nothing about the installed game"},
        "game_executable": {"architecture": (binary or {}).get("architecture"), "format": (binary or {}).get("format"), "os": (binary or {}).get("os"), "measured": bool(binary)},
        "runtime_requirements": {"runtime": report.get("runtime"), "directx": directx, "vulkan": "vulkan-1.dll" in imports or None,
                                 "components": sorted(d for d in imports if d.startswith(("msvcp", "vcruntime", "mscoree", "d3dx")))},
        "engine_clues": list(game.metadata.get("engine_clues", [])),
        "graphics_requirements": report.get("graphics"),
        "host": {"architecture": profile.get("os_architecture"), "wine_available": wine.get("available"), "wine_version": wine.get("version") or None,
                 "renderer": graphics.get("renderer"), "accelerated": graphics.get("accelerated"), "display": graphics.get("display"), "ram_total": profile.get("ram_total")},
        "user_confirmed": {"gameplay": (record or {}).get("gameplay_status", "unverified"), "audio": (record or {}).get("audio_status", "unknown"),
                           "launch": (record or {}).get("launch_status", "never"), "installation": (record or {}).get("installation_status", "unknown"),
                           "evidence_times": (record or {}).get("evidence_times", {})},
        "other_machines": [{"machine": o["machine"]["name"], "gameplay": o["gameplay_status"], "audio": o["audio_status"], "updated": o["updated_at"]} for o in others],
    }


PERMANENT = ("64-bit executable requires", "Foreign executable", "Executable OS/ABI", "Only Linux", "This application targets Linux")
FOREIGN = ("Foreign executable", "Executable OS/ABI", "Only Linux", "This application targets Linux")
RECOVERABLE = ("Wine is not installed", "Graphical session is required", "DOSBox is not installed", "Known missing dependency", "Installed Wine cannot")


def classify(report, record, installed_without_launcher=False, measured=False):
    """-> (classification, [reasons]). Only this machine's own record can verify gameplay."""
    status = report.get("status", "Unknown")
    evidence = report.get("evidence", [])
    gameplay = (record or {}).get("gameplay_status", "unverified")
    launch = (record or {}).get("launch_status", "never")
    if status == "Unsupported":
        permanent = [e for e in evidence if e.startswith(PERMANENT)]
        recoverable = [e for e in evidence if e.startswith(RECOVERABLE)]
        if any(e.startswith(FOREIGN) for e in permanent):
            return "Unsupported", permanent
        if permanent:
            return "Likely incompatible", permanent
        if recoverable:
            return "Requires configuration", ["Missing on this machine: " + "; ".join(recoverable)]
        return "Likely incompatible", ["A concrete blocker was found for the selected method"]
    if gameplay == "works":
        reasons = ["You confirmed gameplay works on this machine"]
        if (record or {}).get("audio_status") == "issues":
            return "Working with issues", reasons + ["Audio problems reported"]
        return "Verified working", reasons
    if gameplay == "issues":
        return "Working with issues", ["You reported problems on this machine"]
    if gameplay in {"fails", "slow"}:
        return "Likely incompatible", ["You reported '{}' on this machine".format(gameplay)]
    if status == "Unlikely":
        return "Likely incompatible", [e for e in report.get("evidence", []) if "unlikely" in e.lower() or "below" in e.lower()] or ["Known resource limits"]
    if status == "Promising" or (status == "Experimental" and (measured or launch in {"started", "exited_ok"})):
        why = ["Evidence supports an attempt; gameplay not confirmed on this machine"]
        if status == "Experimental":
            why.append("Still uncertain: " + "; ".join(u for u in report.get("uncertainties", []) if "Architecture alone" not in u)[:200])
        if launch in {"started", "exited_ok"}:
            why.append("It started here, which does not prove it is playable")
        return "Likely compatible", why
    if installed_without_launcher:
        return "Requires configuration", ["Installed, but no launcher is registered: run 'arch-games discover GAME'"]
    if status == "Experimental":
        return "Unknown", ["Significant uncertainty: " + "; ".join(report.get("uncertainties", [])[:2])]
    return "Unknown", ["Not enough evidence about the game executable"]
