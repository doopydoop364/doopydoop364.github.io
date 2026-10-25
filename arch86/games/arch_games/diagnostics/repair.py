"""Explicit, non-destructive repairs. `plan` only reads; `apply` runs the safe automatic subset; nothing is deleted."""

from pathlib import Path

from ..config import AppError
from ..runtime.discovery import discover


def plan(config, db, game_id):
    steps = []

    def step(ident, problem, action, command, automatic=False):
        steps.append({"id": ident, "problem": problem, "action": action, "command": command, "automatic": automatic})

    launcher = db.get("launchers", game_id)
    prefix = db.get("prefixes", game_id)
    job = db.get("install_jobs", game_id)
    if launcher and not Path(launcher["executable"]).is_file():
        found = discover(config, db, game_id, launcher.get("title", ""))
        step("relaunch-path", "registered executable is missing: " + launcher["executable"],
             "Register the executable again" + (" (best candidate: {})".format(found[0]["path"]) if found else " (none found; reinstall or use --dir)"),
             "arch-games discover {} --register 1".format(game_id) if found else "arch-games install {} --reinstall".format(game_id))
    if prefix:
        from ..runtime.prefixes import PrefixManager

        try:
            health = PrefixManager(config, db).check(game_id)
        except AppError as exc:
            health = None
            step("prefix-record", "prefix cannot be inspected: " + str(exc), "Inspect the prefix directory manually; nothing was changed", "arch-games prefix inspect " + game_id)
        if health and health["healthy"] and health["record"].get("state") != "ready":
            step("prefix-recover", "prefix files are valid but state is '{}'".format(health["record"].get("state")),
                 "Re-verify and mark ready (metadata only; registry and game files untouched)", "arch-games prefix recover " + game_id, True)
        elif health and not health["healthy"]:
            backups = PrefixManager(config, db).backups(game_id)
            step("prefix-unhealthy", "prefix failed checks: " + "; ".join(health["issues"][:3]),
                 "Restore the newest backup ({}) after previewing it".format(backups[0]["id"]) if backups else "No backup exists; recreate the prefix (your saves inside it would need to be copied out first)",
                 "arch-games prefix restore {} {}".format(game_id, backups[0]["id"]) if backups else "arch-games prefix backup " + game_id)
    if job and job.get("status") in {"running", "interrupted", "failed", "awaiting_approval", "awaiting_selection"}:
        step("install-job", "installation job is {} at step '{}'{}".format(job["status"], job.get("step"), ": " + job["error"] if job.get("error") else ""),
             "Resume the job (nothing is re-downloaded)", "arch-games install {}".format(game_id))
    if db.all("install_transactions"):
        step("install-transactions", "unfinished file operations are recorded", "Complete or roll them back (backups are kept)", "(automatic)", True)
    return steps


def apply(config, db, game_id, only=None):
    done = []
    for s in plan(config, db, game_id):
        if not s["automatic"] or (only and s["id"] not in only):
            continue
        if s["id"] == "prefix-recover":
            from ..runtime.prefixes import PrefixManager

            PrefixManager(config, db).recover(game_id)
        elif s["id"] == "install-transactions":
            from ..runtime.installer import Installer

            installer = Installer(config, db)
            with installer.lock():
                installer.recover()
        done.append(s["id"])
    return {"applied": done, "remaining": [s for s in plan(config, db, game_id)]}
