"""Application-wide diagnostics shared by `doctor`, the TUI and the web API. Read-only; repairs are separate and explicit."""

import ctypes.util
import json
import os
from pathlib import Path
import shutil
import sys

from ..config import AppError
from ..downloads.verification import sha256
from ..storage.migrations import CURRENT_VERSION, schema_version


def item(group, name, status, detail, fix=None):
    return {"group": group, "check": name, "status": status, "detail": detail, "fix": fix}


def app_integrity():
    marker = Path(__file__).parents[2] / ".installed.json"
    if not marker.is_file():
        return item("application", "installation", "info", "Source checkout; no installation manifest")
    try:
        manifest = json.loads(marker.read_text())
        damaged = [n for n, d in manifest["files"].items() if not (marker.parent / n).is_file() or sha256(marker.parent / n) != d]
    except (OSError, ValueError, KeyError):
        return item("application", "installation", "fail", "Installation manifest unreadable", "Re-run install.sh")
    if damaged:
        return item("application", "installation", "fail", "Files failed integrity check: " + ", ".join(damaged[:5]), "Re-run install.sh (settings and data are kept)")
    return item("application", "installation", "ok", "All installed file hashes match")


def database_checks(db):
    out = []
    try:
        quick = db.connection.execute("PRAGMA quick_check").fetchone()[0]
        version = schema_version(db.connection)
    except Exception as exc:
        return [item("database", "integrity", "fail", "Cannot query state database: {}".format(type(exc).__name__), "Restore a backup from data/db-backups")]
    out.append(item("database", "integrity", "ok" if quick == "ok" else "fail", "SQLite quick_check: " + quick, None if quick == "ok" else "Restore a backup from data/db-backups"))
    out.append(item("database", "schema", "ok" if version == CURRENT_VERSION else "warn", "schema version {} (expected {})".format(version, CURRENT_VERSION)))
    # record-level consistency
    for ns, value in ((r["key"], r["value"]) for r in db.all("launchers")):
        if not isinstance(value, dict) or not value.get("executable"):
            out.append(item("database", "launcher:" + ns, "fail", "launcher record is malformed", "arch-games launcher remove {}".format(ns)))
    stuck = [r["key"] for r in db.all("install_jobs") if r["value"].get("status") in {"running", "interrupted"}]
    if stuck:
        out.append(item("database", "install-jobs", "warn", "unfinished installation jobs: " + ", ".join(stuck[:5]), "Re-run 'arch-games install GAME' to resume, or 'install GAME --abort'"))
    pending = db.all("install_transactions")
    if pending:
        out.append(item("database", "install-transactions", "warn", "{} pending file transaction(s)".format(len(pending)), "They are completed or rolled back automatically by the next install/uninstall"))
    return out


def storage_checks(config, db):
    out = []
    for name in ("downloads", "installed", "prefixes", "backups", "logs"):
        path = config.directory(name)
        writable = os.access(path, os.W_OK)
        free = shutil.disk_usage(path).free
        status = "ok" if writable and free > 1 << 30 else "warn" if writable else "fail"
        out.append(item("storage", name, status, "{} ({:.1f} GiB free{})".format(path, free / 2**30, "" if writable else ", NOT writable")))
    for row in db.all("downloads"):
        job = row["value"]
        if job.get("state") == "completed":
            try:
                if not (config.directory("downloads") / job["filename"]).is_file():
                    out.append(item("storage", "download:" + row["key"][:8], "warn", "completed download file is missing: " + job.get("title", "?"), "arch-games install {} will fetch it again".format(job.get("game_id", "GAME"))))
            except KeyError:
                pass
    return out


def launcher_checks(config, db):
    out = []
    for row in db.all("launchers"):
        record = row["value"]
        path = Path(record.get("executable", ""))
        ok = path.is_file()
        out.append(item("games", "launcher:" + row["key"], "ok" if ok else "fail", str(path) if ok else "registered executable is missing: {}".format(path),
                        None if ok else "arch-games discover {} then launcher register".format(row["key"])))
    return out


def run(config, db, verbose=False, probe=None):
    from ..audio.diagnose import diagnose
    from ..runtime import winetools

    items = [app_integrity()]
    items.append(item("application", "python", "ok" if sys.version_info >= (3, 10) else "fail", sys.version.split()[0]))
    items.append(item("application", "libarchive", "ok" if ctypes.util.find_library("archive") else "warn", "present" if ctypes.util.find_library("archive") else "not found: archive extraction unavailable", None if ctypes.util.find_library("archive") else "pacman -S libarchive"))
    items += database_checks(db)
    items += storage_checks(config, db)
    # GameVault: configuration only; contacting the server is an explicit `gamevault status`
    server = config.values.get("server_url")
    items.append(item("gamevault", "configuration", "ok" if server and config.secrets.exists() else "info", "configured (server not contacted by doctor)" if server and config.secrets.exists() else "not configured (optional)", None if server else "arch-games gamevault connect"))
    for row in winetools.doctor(config, db)["checks"]:
        status = "warn" if row["check"] == "wine" and row["status"] == "fail" else row["status"]  # Wine is optional for Pacman games
        items.append(item("wine", row["check"], status, row["detail"], row["fix"]))
    items += launcher_checks(config, db)
    regs = {}
    from ..runtime.discovery import prefix_path

    for row in db.all("prefixes"):
        p = prefix_path(config, db, row["key"])
        if p:
            regs[row["key"]] = p / "user.reg"
    audio = diagnose(prefix_user_regs=regs)
    items.append(item("audio", "verdict", {"working": "ok"}.get(audio["verdict"], "warn"), audio["verdict"]))
    if verbose:
        for layer in audio["layers"]:
            items.append(item("audio", layer["layer"], layer["status"] if layer["status"] != "info" else "info", layer["detail"], layer["fix"]))
    from ..audio import remote

    remote_status = remote.settings(config)
    items.append(item("remote-audio", "bridge", "info" if not remote_status.get("enabled") else "ok", "disabled (default)" if not remote_status.get("enabled") else "configured for {} at {}".format(remote_status.get("vm"), remote_status.get("url"))))
    web = config.values.get("web", {})
    items.append(item("web", "configuration", "ok" if web.get("host", "127.0.0.1") in {"127.0.0.1", "::1", "localhost"} else "warn", "binds {}".format(web.get("host", "127.0.0.1 (default, loopback only)")),
                      None if web.get("host", "127.0.0.1") in {"127.0.0.1", "::1", "localhost"} else "A non-loopback web host needs authentication; see WEB_GUI_3.md"))
    summary = {s: sum(1 for i in items if i["status"] == s) for s in ("ok", "info", "warn", "fail")}
    return {"summary": summary, "checks": items if verbose else [i for i in items if i["status"] in {"warn", "fail"} or i["group"] in {"application"}] , "all_checks": len(items)}
