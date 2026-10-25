"""Guided, resumable game installation: GameVault/local archive/folder -> launch-ready game.

Installers are never executed without an explicit approval bound to the plan that was shown.
Job state is persisted after every step (namespace ``install_jobs``) so any interruption can be resumed.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil

from ..compatibility.archives import ArchiveLimits, inspect_archive
from ..compatibility.executables import inspect_file
from ..config import AppError
from ..downloads.manager import RESERVE
from ..downloads.verification import sha256
from ..models import Game
from ..platform import architecture
from ..runtime.discovery import classify_name, confidence, discover
from ..runtime.launcher import Launcher
from ..runtime.launchers import LauncherStore
from ..runtime.prefixes import PrefixManager

NAMESPACE = "install_jobs"
STEPS = ["locate", "acquire", "verify", "inspect", "plan", "approve", "extract", "prefix", "installer", "discover", "register", "test", "finish"]
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".7z", ".rar")
INNO_SILENT = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]


@dataclass
class Options:
    archive: str = None
    directory: str = None
    yes: bool = False
    reinstall: bool = False
    plan_only: bool = False
    installer_member: str = None
    silent_inno: bool = False
    approve_installer: bool = False
    test_launch: bool = False
    test_seconds: int = 20
    timeout: int = 600
    offline: bool = False
    prefix_arch: str = None
    expected_sha256: str = None


def now():
    return datetime.now(timezone.utc).isoformat()


class Workflow:
    def __init__(self, config, db, say=print, confirm=None, choose=None, library_find=None, downloads=None, prefixes=None, launcher=None, installer=None):
        self.config, self.db, self.say = config, db, say
        self.confirm = confirm  # callable(question) -> bool, or None when non-interactive
        self.choose = choose  # callable(question, labels) -> index or None
        self.library_find = library_find
        self._downloads, self._prefixes, self._launcher, self._installer = downloads, prefixes, launcher, installer

    # -- collaborators (lazy so tests can inject) -------------------------------------------------
    @property
    def downloads(self):
        if self._downloads is None:
            from ..downloads.manager import DownloadManager

            self._downloads = DownloadManager(self.config, self.db)
        return self._downloads

    @property
    def prefixes(self):
        return self._prefixes or PrefixManager(self.config, self.db)

    @property
    def launcher(self):
        return self._launcher or Launcher(self.config, self.db)

    @property
    def installer(self):
        if self._installer is None:
            from ..runtime.installer import Installer

            self._installer = Installer(self.config, self.db)
        return self._installer

    # -- job persistence ----------------------------------------------------------------------------
    def job(self, game_id):
        return self.db.get(NAMESPACE, game_id)

    def save(self, job):
        job["updated_at"] = now()
        self.db.put(NAMESPACE, job["game_id"], job)

    def mark(self, job, step, status, detail=""):
        job["steps"][step] = {"status": status, "detail": detail, "at": now()}
        job["step"] = step
        self.save(job)

    def done(self, job, step):
        return job["steps"].get(step, {}).get("status") == "done"

    def abort(self, game_id):
        """Cancel a job. Extracted files move to backups; the prefix (and any saves in it) is untouched."""
        job = self.job(game_id)
        if not job:
            raise AppError("No installation job for this game")
        moved = None
        if job.get("created_install") and self.db.get("installs", game_id):
            moved = self.installer.uninstall(game_id, purge=False)
        job.update(status="cancelled", error=None)
        self.save(job)
        return {"cancelled": game_id, "extracted_files": moved, "prefix": "retained", "downloads": "retained"}

    # -- entry point ----------------------------------------------------------------------------------
    def run(self, game_id, title, options):
        store = LauncherStore(self.config, self.db)
        existing = store.get(game_id)
        if existing and Path(existing["executable"]).is_file() and not options.reinstall:
            return {
                "status": "already_installed",
                "game_id": game_id,
                "launcher": existing["executable"],
                "next": ["arch-games launch " + game_id, "arch-games install {} --reinstall   (keeps a backup)".format(game_id)],
            }
        job = self.job(game_id)
        if job and job.get("status") in {"done", "cancelled"}:
            job = None
        if job is None or options.reinstall and job.get("status") == "done":
            job = {"game_id": game_id, "title": title, "created_at": now(), "steps": {}, "status": "running", "options": {}, "created_install": False}
            self.save(job)
        elif job.get("status") != "running":
            self.say("Resuming installation job (was {}).".format(job["status"]))
            job["status"] = "running"
        job["error"] = None
        job["next"] = []
        try:
            for step in STEPS:
                rerun = {"approve", "register"} | (set() if job.get("approval") else {"plan"})
                if self.done(job, step) and step not in rerun:
                    continue
                result = getattr(self, "step_" + step)(job, options)
                if result == "stop":
                    self.save(job)
                    return self.report(job)
                self.mark(job, step, "done", job["steps"].get(step, {}).get("detail", ""))
            job["status"] = "done"
            job["completed_at"] = now()
            self.save(job)
        except KeyboardInterrupt:
            job.update(status="interrupted", error="Interrupted; re-run the same install command to resume")
            self.save(job)
            raise
        except AppError as exc:
            job.update(status="failed", error=str(exc))
            self.save(job)
            if self.done(job, "extract") or job.get("steps", {}).get("approve"):
                from ..compatibility import intelligence

                intelligence.update(self.db, self.config, game_id, title=title, installation_status="failed")
            raise
        return self.report(job)

    def report(self, job):
        return {
            "status": job["status"],
            "game_id": job["game_id"],
            "step": job.get("step"),
            "error": job.get("error"),
            "plan": job.get("plan"),
            "launcher": (self.db.get("launchers", job["game_id"]) or {}).get("executable"),
            "next": job.get("next", []),
            "limitations": ["Installation success does not verify that the game is playable; use 'feedback set'."],
        }

    # -- steps ----------------------------------------------------------------------------------------
    def step_locate(self, job, o):
        gid = job["game_id"]
        if o.directory and o.archive:
            raise AppError("Use either --archive or --directory, not both")
        if o.directory:
            directory = Path(o.directory).expanduser().resolve()
            if not directory.is_dir():
                raise AppError("Directory does not exist")
            job["source"] = {"type": "directory", "path": str(directory)}
        elif o.archive:
            path = Path(o.archive).expanduser().resolve()
            if not path.is_file():
                raise AppError("Archive or installer is not a regular file")
            job["source"] = {"type": "file", "path": str(path)}
        elif gid.startswith("gamevault:"):
            job["source"] = {"type": "gamevault"}
        else:
            raise AppError("Provide --archive FILE or --directory DIR for {} (no catalog download source)".format(gid))
        self.say("Source: {}".format(job["source"]["type"]))

    def step_acquire(self, job, o):
        source = job["source"]
        if source["type"] != "gamevault":
            return
        if source.get("archive") and Path(source["archive"]).is_file():
            return
        manager = self.downloads
        reuse = None
        for row in self.db.all("downloads"):
            value = row["value"]
            if value.get("game_id") == job["game_id"] and value["state"] == "completed":
                try:
                    if manager.path(value).is_file():
                        reuse = value
                        break
                except AppError:
                    continue
        if reuse:
            self.say("Already downloaded; reusing the existing archive (no new download).")
        else:
            pending = next((r["value"] for r in self.db.all("downloads") if r["value"].get("game_id") == job["game_id"] and r["value"]["state"] in {"queued", "paused", "failed", "running"}), None)
            if pending:
                if pending["state"] in {"paused", "failed"}:
                    manager.retry(pending["id"])
            else:
                if self.library_find is None:
                    raise AppError("GameVault library is unavailable; connect with 'gamevault connect'")
                game = self.library_find(job["game_id"])
                manager.add(game, o.expected_sha256)
            manager.run(lambda j: self.say("download {}: {}/{} bytes".format(j["state"], j["received"], j["expected_size"])))
            reuse = next((r["value"] for r in self.db.all("downloads") if r["value"].get("game_id") == job["game_id"] and r["value"]["state"] == "completed"), None)
            if not reuse:
                raise AppError("Download did not complete; see 'download list' and re-run install to resume")
        source["archive"] = str(manager.path(reuse))
        source["download_id"] = reuse["id"]
        source["verification"] = reuse.get("verification")  # "sha256-verified" | "size-only" | "unverified" (a label, not a dict)
        source["download_sha256"] = reuse.get("sha256")

    def _file(self, job):
        source = job["source"]
        return Path(source.get("archive") or source.get("path"))

    def step_verify(self, job, o):
        source = job["source"]
        if source["type"] == "directory":
            return
        path = self._file(job)
        if not path.is_file():
            raise AppError("Archive disappeared; re-run install to fetch it again")
        digest = sha256(path)
        if o.expected_sha256 and digest != o.expected_sha256.lower():
            raise AppError("SHA-256 does not match the expected value")
        recorded = source.get("download_sha256")
        if recorded and digest != recorded:
            raise AppError("The archive changed after it was downloaded; refusing to continue (retry with a fresh download)")
        source["sha256"] = digest
        checksum_backed = bool(o.expected_sha256) or source.get("verification") == "sha256-verified"
        source["verified_against"] = "expected checksum" if checksum_backed else "download size/integrity checks only (no checksum available from the server)"
        self.say("SHA-256 {} ({})".format(digest[:16] + "...", source["verified_against"]))

    def step_inspect(self, job, o):
        source = job["source"]
        info = {"members": [], "engine_clues": [], "expanded_bytes": 0}
        if source["type"] == "directory":
            info["kind"] = "directory"
        else:
            path = self._file(job)
            head = inspect_file(path)
            if head.format.startswith("PE") or head.format.startswith("ELF"):
                info.update(kind="single-executable", members=[{"name": path.name, "executable": head.to_dict()}], expanded_bytes=path.stat().st_size)
            elif path.name.lower().endswith(ARCHIVE_SUFFIXES):
                self.say("Inspecting archive (bounded, no extraction)...")
                listing = inspect_archive(path, ArchiveLimits(seconds=min(o.timeout, 120)))
                info.update(
                    kind="archive",
                    members=[e for e in listing["entries"] if e.get("executable")],
                    engine_clues=listing.get("engine_clues", []),
                    expanded_bytes=listing["expanded_bytes"],
                    file_count=len(listing["entries"]),
                )
            else:
                raise AppError("Unrecognized file type; supported: ZIP/TAR/RAR/7z archives, .exe installers, ELF binaries")
        source["inspection"] = info

    def step_plan(self, job, o):
        source, info = job["source"], job["source"].get("inspection", {})
        members = info.get("members", [])
        windows = [m for m in members if m["executable"].get("os") == "windows"]
        linux = [m for m in members if m["executable"].get("os") == "linux"]

        def role(m):
            if o.installer_member and m["name"] == o.installer_member:
                return "installer"
            return "installer" if m["executable"].get("role") == "installer" or classify_name(Path(m["name"]).name) == "installer" else classify_name(Path(m["name"]).name)

        installers = [m for m in windows if role(m) == "installer"]
        games = [m for m in windows if role(m) == "game"]
        plan = {
            "installer_architecture": None,
            "game_architecture": "unknown until installed (installer architecture is a separate fact)",
            "notes": [],
            "evidence": [],
            "expanded_bytes": info.get("expanded_bytes", 0),
        }
        if o.installer_member:
            installers = [m for m in windows if m["name"] == o.installer_member]
            if not installers:
                raise AppError("--installer member not found among Windows executables")
        if source["type"] == "directory":
            plan.update(method="register-existing", runner=None)
            plan["notes"].append("Folder is used in place; nothing is copied or run.")
        elif info.get("kind") == "single-executable" and windows and role(windows[0]) == "installer":
            plan.update(method="wine-installer", installer=windows[0]["name"], installer_path=str(self._file(job)), runner="wine")
            plan["installer_architecture"] = windows[0]["executable"].get("architecture")
        elif installers:
            if len(installers) > 1:
                names = [m["name"] for m in installers]
                if not o.installer_member:
                    job["next"] = ["Several installers found: " + ", ".join(names[:6]), "Re-run with --installer MEMBER"]
                    raise AppError("Ambiguous installer; specify --installer MEMBER ({})".format(", ".join(names[:6])))
            pick = installers[0]
            plan.update(method="wine-installer", installer=pick["name"], runner="wine")
            plan["installer_architecture"] = pick["executable"].get("architecture")
            plan["evidence"].append("Installer evidence: " + "; ".join(pick["executable"].get("evidence", [])[:2]))
            if any("Inno Setup Setup Data marker" in e for e in pick["executable"].get("evidence", [])):
                plan["inno_setup_hint"] = True
        elif games:
            plan.update(method="portable-wine", runner="wine")
            plan["notes"].append("Portable Windows game: extracted and launched in an isolated prefix; no installer runs.")
        elif linux:
            plan.update(method="native", runner="native")
        elif info.get("kind") == "single-executable" and linux:
            plan.update(method="native", runner="native")
        else:
            raise AppError("No installer or game executable recognised in the source")
        if info.get("kind") == "single-executable" and plan["method"] != "wine-installer":
            raise AppError("A single non-installer executable has nothing to install; use 'launcher register GAME /abs/path'")
        if plan["method"] in {"wine-installer", "portable-wine"}:
            plan["prefix"] = self._prefix_choice(job["game_id"], o, plan)
        if o.silent_inno:
            if not plan.get("inno_setup_hint"):
                raise AppError("--silent-inno requires Inno Setup evidence; this installer does not show it")
            plan["installer_arguments"] = list(INNO_SILENT)
        else:
            plan["installer_arguments"] = []
            if plan["method"] == "wine-installer":
                plan["notes"].append("The installer runs interactively (a graphical session is required).")
        need = plan["expanded_bytes"] * (2 if plan["method"] == "wine-installer" else 1) + RESERVE
        try:
            free = shutil.disk_usage(self.config.directory("installed")).free
        except OSError:
            free = None
        plan["required_free_bytes"], plan["free_bytes"] = need, free
        if source["type"] != "directory" and free is not None and free < need:
            raise AppError("Insufficient free space: need about {} MiB, have {} MiB".format(need // 2**20, free // 2**20))
        job["plan"] = plan

    def _prefix_choice(self, game_id, o, plan):
        record = self.db.get("prefixes", game_id)
        if record:
            return {"action": "reuse", "architecture": record.get("requested_architecture"), "reason": "An isolated prefix already exists for this game"}
        host = architecture()
        arch = o.prefix_arch or ("win32" if host == "i686" else "win64")
        reason = "Host is {}; {} chosen. {}".format(
            host, arch, "WoW64 prefixes run 32-bit games too; the installer's own bitness is not used to pick the prefix." if arch == "win64" else "32-bit systems only support win32 prefixes."
        )
        return {"action": "create", "architecture": arch, "reason": reason}

    def format_plan(self, job):
        plan = job["plan"]
        lines = ["Installation plan for {} ({})".format(job["title"], job["game_id"]), "  method: " + plan["method"]]
        if plan.get("installer"):
            lines.append("  will run installer: {} ({})".format(plan["installer"], plan.get("installer_architecture")))
            lines.append("  command: wine <installer> {}".format(" ".join(plan["installer_arguments"])).rstrip())
        if plan.get("prefix"):
            lines.append("  prefix: {} {} - {}".format(plan["prefix"]["action"], plan["prefix"]["architecture"], plan["prefix"]["reason"]))
        lines.append("  source sha256: " + str(job["source"].get("sha256", "n/a")))
        lines += ["  note: " + n for n in plan["notes"]]
        return "\n".join(lines)

    def step_approve(self, job, o):
        plan = job["plan"]
        executes = plan["method"] == "wine-installer"
        digest = job["source"].get("sha256")
        approval = job.get("approval")
        if approval and approval.get("sha256") == digest and approval.get("installer") == plan.get("installer"):
            if o.approve_installer and executes and not approval["installer_approved"]:
                approval["installer_approved"] = True
                approval["installer_approved_at"] = now()
            return
        self.say(self.format_plan(job))
        if o.plan_only:
            job["status"] = "plan_only"
            job["next"] = ["Re-run without --plan-only to proceed"]
            return "stop"
        installer_ok = False
        if executes and self.confirm and not (o.yes or o.approve_installer):
            installer_ok = approved = self.confirm("Extract, then run the installer shown above inside the Wine prefix?")
        else:
            approved = o.yes or o.approve_installer or (bool(self.confirm) and self.confirm("Proceed with this plan (no installer is run)?"))
            installer_ok = executes and o.approve_installer
        if not approved:
            job["status"] = "awaiting_approval"
            job["next"] = ["Review the plan, then re-run with --yes" + (" --approve-installer" if executes else "")]
            return "stop"
        job["approval"] = {"at": now(), "sha256": digest, "installer": plan.get("installer"), "installer_approved": bool(installer_ok), "executes_installer": executes}

    def step_prefix(self, job, o):
        choice = job["plan"].get("prefix")
        if not choice:
            return
        gid = job["game_id"]
        if choice["action"] == "reuse":
            health = self.prefixes.check(gid)
            if not health["healthy"]:
                raise AppError("Existing prefix is unhealthy ({}); run 'prefix check {}' / 'prefix recover {}'".format("; ".join(health["issues"][:2]), gid, gid))
            if health["record"].get("state") != "ready":
                self.prefixes.recover(gid)
            return
        self.say("Creating isolated {} Wine prefix...".format(choice["architecture"]))
        self.prefixes.create(gid, choice["architecture"])

    def step_extract(self, job, o):
        plan, source, gid = job["plan"], job["source"], job["game_id"]
        game = Game(gid, job["title"], gid.split(":", 1)[0])
        if plan["method"] == "register-existing" or source["inspection"]["kind"] != "archive":
            return
        if self.db.get("installs", gid) and not o.reinstall:
            self.say("Already extracted; reusing the managed directory.")
            return
        if o.reinstall:
            from ..saves.service import SaveManager

            SaveManager(self.config, self.db).backup_if_known(gid, "pre-reinstall", say=self.say)
        self.say("Extracting into a managed directory (nothing is executed)...")
        self.installer.install(game, self._file(job), reinstall=o.reinstall and bool(self.db.get("installs", gid)), timeout=o.timeout, progress=self.say)
        job["created_install"] = True

    def step_installer(self, job, o):
        plan, source, gid = job["plan"], job["source"], job["game_id"]
        if plan["method"] != "wine-installer":
            return
        if not job["approval"].get("installer_approved"):
            job["status"] = "awaiting_approval"
            job["next"] = ["Files are extracted. To run the installer: arch-games install {} --approve-installer".format(gid)]
            return "stop"
        launcher = self.launcher
        if source["inspection"]["kind"] == "single-executable":
            launch_plan = launcher.plan(gid, str(self._file(job)), "wine", None, plan["installer_arguments"], None, None, True)
        else:
            launch_plan = launcher.plan(gid, plan["installer"], "wine", None, plan["installer_arguments"])
        launch_plan.update(purpose="installer", interactive=not plan["installer_arguments"])
        self.say("Running installer under Wine (approved)...")
        record = launcher.launch(launch_plan)
        job["installer_launch"] = {"id": record["id"], "exit_code": record["exit_code"], "log": record["log"]}
        if record["exit_code"]:
            job["next"] = ["Installer exited with status {}; see {}".format(record["exit_code"], record["log"])]
            raise AppError("Installer exited with status {}; re-run 'install --approve-installer' to retry (prefix and files kept)".format(record["exit_code"]))

    def step_discover(self, job, o):
        gid, source = job["game_id"], job["source"]
        extra = [source["path"]] if source["type"] == "directory" else []
        candidates = discover(self.config, self.db, gid, job["title"], extra)
        job["candidates"] = candidates[:10]
        job["confidence"] = confidence(candidates)
        if not candidates:
            job["status"] = "awaiting_selection"
            job["next"] = ["No game executable found. Try: arch-games discover {} --dir DIR --all".format(gid)]
            return "stop"

    def step_register(self, job, o):
        gid = job["game_id"]
        store = LauncherStore(self.config, self.db)
        if store.get(gid) and Path(store.get(gid)["executable"]).is_file() and not o.reinstall:
            return
        candidates = job.get("candidates") or []
        if not candidates:
            return "stop"
        chosen = None
        if job["confidence"] == "high" and (o.yes or (self.confirm and self.confirm("Use {} as the launcher?".format(candidates[0]["path"])))):
            chosen = candidates[0]
        elif self.choose and job["confidence"] != "high":
            index = self.choose("Which executable starts the game?", [c["path"] for c in candidates])
            chosen = candidates[index] if index is not None else None
        if not chosen:
            job["status"] = "awaiting_selection"
            job["next"] = ["Confirm the launcher: arch-games discover {} --register RANK".format(gid)]
            return "stop"
        is_external = job["source"]["type"] == "directory"
        store.register(gid, job["title"], chosen["path"], evidence=["Install workflow, discovery score {}".format(chosen["score"])] + chosen["evidence"], allow_external=is_external)
        self.say("Launcher registered: " + chosen["path"])

    def step_test(self, job, o):
        if not o.test_launch:
            return
        plan = self.launcher.plan_registered(job["game_id"])
        record = self.launcher.launch(plan, timeout=o.test_seconds)
        job["test_launch"] = {"state": record["state"], "exit_code": record["exit_code"], "log": record["log"], "seconds": o.test_seconds}
        job["next"] = ["Supervised launch ran for up to {}s and was stopped; this does not verify gameplay.".format(o.test_seconds)]

    def step_finish(self, job, o):
        from ..compatibility import intelligence

        intelligence.update(self.db, self.config, job["game_id"], title=job["title"], installation_status="installed")
        job["metadata"] = {"finished_at": now(), "method": job["plan"]["method"], "source_sha256": job["source"].get("sha256")}
        job.setdefault("next", [])
        job["next"] = job["next"] + ["arch-games launch " + job["game_id"], "arch-games feedback set {} works|issues|fails".format(job["game_id"])]
