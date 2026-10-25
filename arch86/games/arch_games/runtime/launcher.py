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

    def plan(self, game_id, executable, method="auto", prefix=None, arguments=None, custom_env=None,
             command=None, allow_external=False):
        manifest = self.db.get("installs", game_id)
        root = child(self.config.directory("installed"), manifest["directory"]).resolve() if manifest else None
        raw_path = Path(executable)
        if not raw_path.is_absolute():
            if root is None:
                raise AppError("Relative executable paths require a managed installation")
            raw_path = root / raw_path
        path = raw_path.resolve()
        if not allow_external and (root is None or not path.is_relative_to(root)):
            raise AppError("Executable must be inside this managed game directory; --allow-external is an explicit override")
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
            if not health["healthy"] or health["record"].get("state") != "ready":
                raise AppError("Wine prefix is incomplete; run prefix check")
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
            if not isinstance(command, list) or not command or not all(isinstance(v, str) and "\0" not in v for v in command):
                raise AppError("Custom method requires --command with a JSON argument array")
            argv = [value.replace("{exe}", str(path)) for value in command] + args
            runner = self.which(argv[0]) if not Path(argv[0]).is_absolute() else argv[0]
            if not runner or not Path(runner).is_file() or not os.access(runner, os.X_OK):
                raise AppError("Custom command program is missing or is not executable")
            argv[0] = str(Path(runner).resolve())
        else:
            raise AppError("Unknown launch method")
        return {"argv": argv, "cwd": str(path.parent), "env": env, "method": selected,
                "game_id": game_id, "executable": str(path), "executable_sha256": sha256(path),
                "archive_sha256": manifest.get("archive_sha256") if manifest else None,
                "prefix": prefix or game_id if selected == "wine" else None}

    def launch(self, plan):
        ident = uuid.uuid4().hex
        path = self.config.directory("logs") / ("launch-" + ident + ".log")
        record = {k: plan[k] for k in ["game_id", "executable", "executable_sha256", "archive_sha256", "method", "prefix", "argv"]}
        import hashlib
        import json
        runtime = {"argv": plan["argv"], "env": plan["env"], "prefix": plan["prefix"]}
        record["runtime_fingerprint"] = hashlib.sha256(json.dumps(runtime, sort_keys=True).encode()).hexdigest()
        record.update(id=ident, started_at=datetime.now(timezone.utc).isoformat(),
                      state="running", log=str(path), fingerprint=fingerprint(apply_overrides(system_profile(self.config.data_dir), self.config)))
        self.db.put("launches", ident, record)
        process = None
        try:
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                process = self.popen(plan["argv"], cwd=plan["cwd"], env=plan["env"],
                                     stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
                result = process.wait()
            record.update(state="exited", exit_code=result)
        except KeyboardInterrupt:
            if process:
                self.stop(process)
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
    def stop(process):
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        except ProcessLookupError:
            pass
