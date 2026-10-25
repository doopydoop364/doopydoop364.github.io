"""Web API operations. Every operation is an explicit allowlisted call into the same services the CLI uses.
No generic command execution, no file paths from the browser, no shell."""

import re
import threading
import time

from ...catalog.library import Library
from ...config import AppError
from ...core.gameview import describe
from ...core.ids import local_id
from ...storage.database import Database

GAME_ID = re.compile(r"^[a-z0-9_-]{1,24}:[A-Za-z0-9._@-]{1,100}$")
SORTS = {"title", "source", "category", "status"}


class BadRequest(AppError):
    pass


def game_id(value):
    if not isinstance(value, str) or not GAME_ID.match(value):
        raise BadRequest("Invalid game ID")
    return value


class Api:
    def __init__(self, config, library_ttl=30.0):
        self.config = config
        self.ttl = library_ttl
        self._library = None
        self._loaded = 0.0
        self._lock = threading.Lock()
        self.jobs = {}  # in-memory background operation status: id -> dict
        self.samples = {}  # download id -> (time, bytes) for speed
        self.job_lock = threading.Lock()

    def db(self):
        return Database(self.config.data_dir / "state.db")

    def library(self, db, force=False):
        with self._lock:
            if force or self._library is None or time.monotonic() - self._loaded > self.ttl:
                self._library = Library(self.config, db).load(offline=True)
                self._loaded = time.monotonic()
            return self._library

    # -- library --------------------------------------------------------------------------------------------
    def library_list(self, db, query):
        from types import SimpleNamespace

        from ...catalog.search import SearchIndex

        lib = self.library(db)
        sort = query.get("sort", "title")
        if sort not in SORTS:
            raise BadRequest("Unknown sort")
        source = query.get("source", "all")
        if source not in {"all", "pacman", "gamevault", "local"}:
            raise BadRequest("Unknown source")
        games, explanation = lib.filtered(search="", source="all", show_all=query.get("all") == "1", sort=sort)
        index = SearchIndex(SimpleNamespace(games=games, reports=lib.reports), db)  # the same index the CLI `search` uses
        found = index.search(
            query.get("q", "")[:100],
            None if source == "all" else source,
            None,
            True if query.get("installed") == "1" else None,
            query.get("status") or None,
            None,
            True if query.get("favorites") == "1" else None,
            limit=2000,
        )
        out = [
            {"id": r["game"].id, "title": r["game"].title, "source": r["source"], "category": r["game"].category, "installed": r["installed"], "favorite": r["favorite"],
             "cover": ((r["game"].metadata.get("remote") or {}).get("metadata") or {}).get("cover", {}).get("id"), "os": r["game"].os,
             "architecture": r["game"].architecture, "status": r["classification"]}
            for r in found
        ]
        if sort != "title":
            out.sort(key=lambda row: (row[{"status": "status", "source": "source", "category": "category"}[sort]], row["title"].casefold()))
        return {"games": out, "total": len(out), "note": explanation}

    def recent(self, db):
        seen, out = set(), []
        for v in sorted(db.mapping("launches").values(), key=lambda v: v.get("started_at", ""), reverse=True):
            if v.get("purpose", "game") == "game" and v.get("game_id") not in seen:
                seen.add(v["game_id"])
                out.append({"id": v["game_id"], "started_at": v.get("started_at"), "state": v.get("state"), "exit_code": v.get("exit_code")})
            if len(out) >= 8:
                break
        return out

    def game(self, db, gid):
        gid = game_id(gid)  # described under whichever ID it was asked for; describe() looks under the bare ID too
        lib = self.library(db)
        game = next((g for g in lib.games if g.id == gid), None)
        if game is None:
            from ...models import Game

            from ...core.ids import family_get

            known = family_get(db, "launchers", gid) or family_get(db, "prefixes", gid) or family_get(db, "installs", gid)
            if not known:
                raise AppError("Unknown game")
            game = Game(gid, (family_get(db, "launchers", gid) or {}).get("title", gid), gid.split(":")[0])
        from ...core.ids import base

        aliases = [r["value"]["alias"] for r in db.all("aliases") if r["value"]["game_id"] in {gid, base(gid)}]
        favorite = db.get("favorites", gid) is not None or db.get("favorites", base(gid)) is not None
        d = describe(self.config, db, game, lib.reports.get(gid, {}), aliases, favorite)
        d["job"] = self.install_job(db, gid)
        return d

    def set_favorite(self, db, gid, value):
        from ...core import personal

        gid = local_id(db, game_id(gid))
        return {"id": gid, "favorite": personal.set_favorite(db, gid, value)}

    def set_notes(self, db, gid, text, tags):
        from ...core import personal

        gid = local_id(db, game_id(gid))
        try:
            personal.set_notes(db, gid, text)
            personal.set_tags(db, gid, tags)
        except AppError as exc:
            raise BadRequest(str(exc)) from None
        return {"id": gid}

    # -- downloads -----------------------------------------------------------------------------------------------
    def downloads(self, db):
        now = time.monotonic()
        rows = []
        for r in db.all("downloads"):
            j = r["value"]
            speed = None
            last = self.samples.get(j["id"])
            if j.get("state") == "running" and last and now > last[0]:
                speed = max(0.0, (j.get("received", 0) - last[1]) / (now - last[0]))
            self.samples[j["id"]] = (now, j.get("received", 0))
            rows.append({k: j.get(k) for k in ("id", "title", "game_id", "state", "received", "expected_size", "error", "created_at")} | {"speed": speed})
        return rows

    def start_job(self, kind, label, target):
        with self.job_lock:
            if any(j["kind"] == kind and j["state"] == "running" and j["label"] == label for j in self.jobs.values()):
                raise AppError("That operation is already running")
            ident = "%s-%d" % (kind, len(self.jobs) + 1)
            job = {"id": ident, "kind": kind, "label": label, "state": "running", "message": "", "started": time.time()}
            self.jobs[ident] = job

        def run():
            db = self.db()
            try:
                job["result"] = target(db, lambda m: job.__setitem__("message", str(m)[:300]))
                job["state"] = "done"
            except BaseException as exc:  # reported to the UI, never swallowed silently
                job["state"] = "failed"
                job["message"] = str(exc)[:500] if isinstance(exc, AppError) else "Operation failed (%s)" % type(exc).__name__
            finally:
                db.close()

        threading.Thread(target=run, daemon=True, name=ident).start()
        return job

    def download_add(self, db, gid):
        from ...downloads.manager import DownloadManager

        gid = game_id(gid)
        lib = Library(self.config, db).load("gamevault", True)
        return DownloadManager(self.config, db).add(lib.find(gid, alternatives=True))

    def download_run(self):
        from ...downloads.manager import DownloadManager

        def target(db, say):
            DownloadManager(self.config, db).run(lambda j: say("{}: {}/{}".format(j["state"], j["received"], j["expected_size"])))
        return self.start_job("download", "queue", target)

    def download_retry(self, db, ident, restart=False):
        from ...downloads.manager import DownloadManager

        return DownloadManager(self.config, db).retry(str(ident)[:64], bool(restart))

    def download_pause(self, db, ident):
        from ...downloads.manager import DownloadManager

        return DownloadManager(self.config, db).pause(str(ident)[:64])

    def download_cancel(self, db, ident, cleanup=False):
        from ...downloads.manager import DownloadManager

        DownloadManager(self.config, db).cancel(str(ident)[:64], bool(cleanup))
        return {"cancelled": ident}

    # -- install -----------------------------------------------------------------------------------------------------
    def install_job(self, db, gid):
        job = db.get("install_jobs", gid)
        if not job:
            return None
        return {k: job.get(k) for k in ("status", "step", "error", "plan", "next", "steps")} | {"sha256": (job.get("source") or {}).get("sha256")}

    def _flow(self, db, say):
        from ...installation.workflow import Workflow

        lib = self.library(db)

        def find(gid):
            return Library(self.config, db).load("gamevault", True).find(gid, alternatives=True)
        return Workflow(self.config, db, say=say, confirm=None, library_find=find), lib

    def install_prepare(self, gid):
        gid = game_id(gid)

        def target(db, say):
            from ...installation.workflow import Options

            flow, lib = self._flow(db, say)
            title = next((g.title for g in lib.games if g.id == gid), gid)
            return flow.run(gid, title, Options(plan_only=True, offline=True))
        return self.start_job("install", gid, target)

    def install_approve(self, gid, plan_sha256, approve_installer):
        gid = game_id(gid)
        db = self.db()
        try:
            job = db.get("install_jobs", gid)
        finally:
            db.close()
        if not job or job.get("status") != "plan_only":
            raise AppError("Prepare the installation first; there is no plan to approve")
        if not isinstance(plan_sha256, str) or plan_sha256 != (job.get("source") or {}).get("sha256"):
            raise AppError("The plan changed since you reviewed it; reload and review again")

        def target(db, say):
            from ...installation.workflow import Options

            flow, lib = self._flow(db, say)
            title = next((g.title for g in lib.games if g.id == gid), gid)
            return flow.run(gid, title, Options(yes=True, approve_installer=bool(approve_installer), offline=True))
        return self.start_job("install", gid, target)

    # -- launch / wine / audio / diagnostics ---------------------------------------------------------------------------
    def launch(self, gid):
        from ...runtime.launcher import Launcher

        db = self.db()
        try:
            gid = local_id(db, game_id(gid))
            plan = Launcher(self.config, db).plan_registered(gid)
        finally:
            db.close()
        needs_display = plan["method"] in {"wine", "native", "dosbox"}
        import os
        if needs_display and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise AppError("No graphical session (DISPLAY/WAYLAND_DISPLAY) is available to the web server; start it from your desktop session")

        def target(db, say):
            record = Launcher(self.config, db).launch(plan)
            return {"state": record["state"], "exit_code": record["exit_code"]}
        return self.start_job("launch", gid, target)

    def wine_doctor(self, db):
        from ...runtime import winetools
        return winetools.doctor(self.config, db)

    def wine_status(self, db, gid):
        from ...runtime import winetools
        return winetools.status(self.config, db, local_id(db, game_id(gid)))

    def wine_config(self, db, gid, body):
        from ...runtime import winetools
        gid = local_id(db, game_id(gid))
        allowed = {k: body[k] for k in ("runtime", "windows_version", "dll_overrides", "env", "virtual_desktop", "debug") if k in body}
        return winetools.save_settings(db, gid, allowed)

    def audio(self, db):
        from ...audio import remote
        from ...audio.diagnose import diagnose
        from ...runtime.discovery import prefix_path

        regs = {r["key"]: prefix_path(self.config, db, r["key"]) / "user.reg" for r in db.all("prefixes") if prefix_path(self.config, db, r["key"])}
        return {"local": diagnose(prefix_user_regs=regs), "remote": remote.status(self.config, timeout=2)}

    def audio_remote_disable(self):
        from ...audio import remote
        remote.disable(self.config)
        return {"enabled": False}

    def doctor(self, db):
        from ...diagnostics import doctor
        return doctor.run(self.config, db, verbose=True, probe_graphics=False)

    def job(self, ident):
        job = self.jobs.get(str(ident)[:64])
        if not job:
            raise AppError("Unknown job")
        return job
