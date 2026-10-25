"""Explicit subprocess argument-array launches with local history and logs."""

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import signal
import subprocess
import uuid

from ..compatibility.analyzer import fingerprint
from ..compatibility.executables import inspect_file
from ..config import AppError
from ..downloads.verification import sha256
from ..platform import apply_overrides, system_profile
from .environment import environment
from .installer import child
from .prefixes import PrefixManager


class Launcher:
    def __init__(self, config, db, which=shutil.which, popen=subprocess.Popen):
        self.config, self.db, self.which, self.popen = config, db, which, popen

    def plan(
        self,
        game_id,
        executable,
        method="auto",
        prefix=None,
        arguments=None,
        custom_env=None,
        command=None,
        allow_external=False,
        extra_roots=(),
        cwd=None,
    ):
        manifest = self.db.get("installs", game_id)
        root = (
            child(self.config.directory("installed"), manifest["directory"]).resolve() if manifest else None
        )
        raw_path = Path(executable)
        if not raw_path.is_absolute():
            if root is None:
                raise AppError("Relative executable paths require a managed installation")
            raw_path = root / raw_path
        path = raw_path.resolve()
        roots = ([root] if root else []) + [Path(r) for r in extra_roots]
        if not allow_external and not any(path.is_relative_to(r) for r in roots):
            raise AppError(
                "Executable must be inside this managed game directory; --allow-external is an explicit override"
            )
        if not path.is_file():
            raise AppError("Game executable is missing or is not a regular file")
        binary = inspect_file(path)
        selected = method
        if method == "auto":
            selected = {"linux": "native", "windows": "wine", "dos": "dosbox"}.get(binary.os, "unknown")
        if selected == "unknown":
            raise AppError("Unrecognized executable; select an explicit custom command if intentional")
        env = environment(custom_env)
        if binary.architecture not in {"i686", "x86_64"} and selected != "custom":
            raise AppError("Executable architecture is unsupported by the selected method")
        from ..platform import architecture

        if binary.bits == 64 and architecture() == "i686" and selected != "custom":
            raise AppError("64-bit executable requires a 64-bit operating system")
        args = list(arguments or [])
        if not all(isinstance(arg, str) and "\0" not in arg for arg in args):
            raise AppError("Invalid game argument")
        if selected == "native":
            if binary.os != "linux" or not os.access(path, os.X_OK):
                raise AppError("Native launch requires an executable ELF file")
            argv = [str(path)] + args
        elif selected == "wine":
            if binary.os != "windows":
                raise AppError("Wine launch requires a Windows PE executable")
            wine = self.which("wine")
            if not wine:
                raise AppError("Wine is not installed")
            manager = PrefixManager(self.config, self.db)
            ident = prefix or game_id
            health = manager.check(ident)
            if not health["healthy"]:
                raise AppError("Wine prefix is incomplete; run prefix check; existing files are retained")
            if health["record"].get("state") != "ready":
                raise AppError(
                    "Wine prefix files are healthy but its saved state is {}; run arch-games prefix recover {}".format(
                        health["record"].get("state", "unknown"), ident
                    )
                )
            if binary.bits == 64 and health["architecture"] == "win32":
                raise AppError("PE32+ cannot run in a win32 prefix")
            prefix_env = environment(health["record"].get("environment"))
            prefix_env.update(env)
            env = prefix_env
            env["WINEPREFIX"] = health["path"]
            argv = [wine, str(path)] + args
        elif selected == "dosbox":
            if binary.os != "dos":
                raise AppError("DOSBox launch requires a DOS executable")
            runtime = self.which("dosbox") or self.which("dosbox-staging")
            if not runtime:
                raise AppError("DOSBox is not installed")
            argv = [runtime, str(path), "-exit"] + args
        elif selected == "custom":
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(v, str) and "\0" not in v for v in command)
            ):
                raise AppError("Custom method requires --command with a JSON argument array")
            argv = [value.replace("{exe}", str(path)) for value in command] + args
            runner = self.which(argv[0]) if not Path(argv[0]).is_absolute() else argv[0]
            if not runner or not Path(runner).is_file() or not os.access(runner, os.X_OK):
                raise AppError("Custom command program is missing or is not executable")
            argv[0] = str(Path(runner).resolve())
        else:
            raise AppError("Unknown launch method")
        plan = {
            "argv": argv,
            "cwd": str(Path(cwd).resolve() if cwd else path.parent),
            "env": env,
            "method": selected,
            "game_id": game_id,
            "executable": str(path),
            "executable_sha256": sha256(path),
            "archive_sha256": manifest.get("archive_sha256") if manifest else None,
            "prefix": prefix or game_id if selected == "wine" else None,
        }
        if selected == "wine":
            from . import winetools

            settings = winetools.get_settings(self.db, game_id)
            known = winetools.runtimes(self.config, which=self.which) if settings.get("runtime") else {}
            winetools.apply_to_plan(settings, plan, known)
        return plan

    def plan_registered(self, game_id, extra_arguments=(), extra_env=None):
        """Plan from the persisted, user-confirmed launcher; never guesses an executable."""
        from .launchers import LauncherStore

        store = LauncherStore(self.config, self.db)
        record = store.get(game_id)
        if not record:
            raise AppError("No launcher is registered for this game")
        if not record.get("confirmed"):
            raise AppError("The registered launcher was never confirmed; run 'launcher register' again")
        path = Path(record["executable"])
        if not path.is_file():
            raise AppError("Registered executable no longer exists: {}; run 'discover' and register again".format(path))
        env = dict(record.get("environment") or {})
        env.update(extra_env or {})
        plan = self.plan(
            game_id,
            str(path),
            record["runner"],
            record.get("prefix"),
            list(record.get("arguments") or []) + list(extra_arguments),
            env,
            None,
            False,
            extra_roots=store.allowed_roots(game_id, record),
            cwd=record.get("working_directory"),
        )
        plan["registered"] = True
        return plan

    def launch(self, plan, timeout=None):
        """Run a plan. With timeout, a still-running process is stopped and recorded as a supervised test."""
        ident = uuid.uuid4().hex
        path = self.config.directory("logs") / ("launch-" + ident + ".log")
        record = {
            k: plan[k]
            for k in [
                "game_id",
                "executable",
                "executable_sha256",
                "archive_sha256",
                "method",
                "prefix",
                "argv",
            ]
        }
        import hashlib
        import json

        runtime = {"argv": plan["argv"], "env": plan["env"], "prefix": plan["prefix"]}
        record["prefix_path"] = plan["env"].get("WINEPREFIX") if plan["method"] == "wine" else None
        record["runtime_fingerprint"] = hashlib.sha256(
            json.dumps(runtime, sort_keys=True).encode()
        ).hexdigest()
        record.update(
            id=ident,
            purpose=plan.get("purpose", "game"),
            started_at=datetime.now(timezone.utc).isoformat(),
            state="running",
            log=str(path),
            fingerprint=fingerprint(apply_overrides(system_profile(self.config.data_dir), self.config)),
        )
        self.db.put("launches", ident, record)
        process = None
        try:
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                interactive = plan.get("interactive", False)
                if interactive:
                    output.write(b"Interactive terminal session: game output was not captured.\n")
                process = self.popen(
                    plan["argv"],
                    cwd=plan["cwd"],
                    env=plan["env"],
                    stdout=None if interactive else output,
                    stderr=None if interactive else subprocess.STDOUT,
                    stdin=None if interactive else subprocess.DEVNULL,
                    start_new_session=not interactive,
                )
                if timeout:
                    try:
                        result = process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        self.stop(process, group=not plan.get("interactive", False))
                        record.update(state="stopped_after_test", exit_code=None, test_seconds=timeout)
                        result = None
                else:
                    result = process.wait()
            if result is not None or record["state"] == "running":
                record.update(state="exited", exit_code=result)
        except KeyboardInterrupt:
            if process:
                self.stop(process, group=not plan.get("interactive", False))
            record.update(state="cancelled", exit_code=None)
            raise
        except OSError:
            record.update(state="failed", exit_code=None)
            raise AppError("Launch failed; check runtime, file permissions and local log") from None
        finally:
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.db.put("launches", ident, record)
        return record

    @staticmethod
    def stop(process, group=True):
        try:
            os.killpg(process.pid, signal.SIGTERM) if group else process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL) if group else process.kill()
            process.wait(timeout=5)
        except ProcessLookupError:
            pass
