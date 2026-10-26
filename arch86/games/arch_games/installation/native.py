"""Native Pacman game installation: explicit approval, verification against the real package database, launcher discovery.

Installation success and launcher configuration are separate outcomes: a package that installed but whose launcher could not be
chosen is reported as installed with 'launcher configuration still required', never as a failed installation."""

import subprocess

from ..compatibility import intelligence
from ..config import AppError
from ..runtime import native
from ..runtime.launchers import LauncherStore


def run_pacman_install(package):
    """The only privileged step, run only after approval. sudo prompts on the user's own terminal; argument array, no shell."""
    return subprocess.run(["sudo", "pacman", "-S", "--needed", "--", package]).returncode


def install(config, db, game_id, package, title, pacman, confirm=None, yes=False, choose=None, say=print, installer=None):
    """-> report dict. confirm(question) -> bool for interactive approval; choose(candidates) -> candidate|None."""
    installer = installer or run_pacman_install
    version = pacman.version(package)
    if version is None:
        if not yes:
            if confirm is None:
                raise AppError("Installing {} runs 'sudo pacman -S'; approve it with --yes (or run interactively)".format(package))
            if not confirm("Install {} using Pacman (runs sudo pacman -S --needed)?".format(package)):
                return {"status": "cancelled", "game_id": game_id, "package": package}
        code = installer(package)
        if code:
            raise AppError("Pacman exited with status {}; nothing was changed by Arch Games".format(code))
        version = pacman.version(package)  # the local database is the authority, not the exit status
        if version is None:
            raise AppError("Pacman reported success but package {} is not installed; check the Pacman output above".format(package))
        say("Installed {} {}.".format(package, version))
    else:
        say("{} {} is already installed; checking its launcher.".format(package, version))
    intelligence.update(db, config, game_id, title=title, platform_name="native", installation_status="installed")
    return finish(config, db, game_id, package, title, version, pacman, choose, say)


def finish(config, db, game_id, package, title, version, pacman, choose=None, say=print):
    """Discover and (when safe) register the launcher. Used after installation and for 'retry without reinstalling'."""
    store = LauncherStore(config, db)
    existing = store.get(game_id)
    if existing is not None:
        state, detail = native.verify_launcher(pacman, existing) if existing.get("kind") == "native-package" else (native.READY, "")
        if state == native.READY:
            return {"status": "ready", "game_id": game_id, "package": package, "version": version, "launcher": existing["executable"],
                    "next": ["arch-games launch " + game_id]}
        say("The registered launcher is stale ({}); rediscovering with your settings kept.".format(detail))
    result = native.discover_native(pacman, package, title)
    candidates = result["candidates"]
    report = {"game_id": game_id, "package": package, "version": version, "candidates": [{"rank": c["rank"], "path": c["path"], "score": c["score"]} for c in candidates[:5]]}
    chosen = None
    if candidates and result["confidence"] == "high":
        chosen = candidates[0]
    elif candidates and choose is not None:
        chosen = choose(candidates)
    if chosen is None:
        report["status"] = "launcher-needs-confirmation" if candidates else "launcher-required"
        report["message"] = "Installation succeeded; launcher configuration still required."
        report["next"] = (["arch-games discover {} --register N   (pick one of the candidates)".format(game_id)] if candidates
                          else ["No launchable program was found in the package. If it provides one anyway: arch-games launcher register {} /usr/bin/NAME".format(game_id)])
        return report
    record = store.register_native(game_id, title, package, chosen, pacman,
                                   evidence=["Discovery score {}".format(chosen["score"])] + chosen["evidence"])
    report.update(status="ready", launcher=record["executable"], next=["arch-games launch " + game_id])
    say("Launcher registered: {}".format(record["executable"]))
    return report
