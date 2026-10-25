"""Builds TUI rows from the shared services (library, launchers, downloads, wine, audio, diagnostics)."""

from pathlib import Path

from ...catalog.library import Library
from ...config import AppError
from ...identity.resolver import normalize
from .model import GAME_SECTIONS, Row

TONE = {"Verified Working": "ok", "Promising": "ok", "Experimental": "warn", "Unlikely": "warn", "Unsupported": "bad", "Unknown": "dim"}
STATUS_TONE = {"ok": "ok", "info": "dim", "warn": "warn", "fail": "bad"}


def dirsize(path, limit=20000):
    total = count = 0
    try:
        for root, _, names in __import__("os").walk(path, followlinks=False):
            for n in names:
                count += 1
                if count > limit:
                    return total, True
                try:
                    total += __import__("os").lstat(__import__("os").path.join(root, n)).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total, False


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return "{:.1f} {}".format(n, unit) if unit != "B" else "{} B".format(n)
        n /= 1024


class Provider:
    def __init__(self, config, db, library=None):
        self.config, self.db = config, db
        self.library = library
        self.cache = {}

    def invalidate(self):
        self.cache.clear()
        self.library = Library(self.config, self.db).load(offline=True)

    def lib(self):
        if self.library is None:
            self.library = Library(self.config, self.db).load(offline=True)
        return self.library

    def header(self):
        p = self.lib().profile
        ram = p.get("ram_total")
        return "{} | {}".format(p.get("os_architecture", "?"), "{:.1f} GiB RAM".format(ram / 2**30) if ram else "RAM ?")

    def empty_text(self, section):
        return {"Library": "No games match. Connect GameVault or clear the search.", "Installed": "Nothing installed yet. Press 1 for the Library.",
                "GameVault": "No GameVault games (connect with: arch-games gamevault connect).", "Downloads": "No downloads."}.get(section, "Nothing to show.")

    def rows(self, section, search="", show_all=False):
        method = getattr(self, "rows_" + section.lower())
        return method(search, show_all)

    # -- games ---------------------------------------------------------------------------------------------
    def game_rows(self, search, show_all, **filters):
        library = self.lib()
        games, _ = library.filtered(search="", show_all=show_all, **filters)
        key = normalize(search)
        favorites = set(self.db.mapping("favorites"))
        aliases = {}
        for row in self.db.all("aliases"):
            aliases.setdefault(row["value"]["game_id"], []).append(row["key"])
        rows = []
        for g in games:
            if key and not (key in normalize(g.title) or key in g.id.casefold() or any(key in a for a in aliases.get(g.id, []))):
                continue
            rows.append(self.game_row(g, library.reports.get(g.id, {}), g.id in favorites, aliases.get(g.id, [])))
        rows.sort(key=lambda r: (not r.favorite, r.label.casefold()))
        return rows

    def game_row(self, g, report, favorite, aliases):
        status = report.get("status", "Unknown")
        launcher = self.db.get("launchers", g.id)
        installed = g.installed or launcher is not None or self.db.get("prefixes", g.id) is not None
        detail = [("Source: {}   ID: {}".format(g.source, g.id), "normal")]
        if aliases:
            detail.append(("Aliases: " + ", ".join(aliases), "dim"))
        detail.append(("State: " + ("installed" if installed else "not installed"), "ok" if installed else "dim"))
        detail.append(("Compatibility: {}".format(status), TONE.get(status, "dim")))
        for line in (report.get("evidence") or [])[:3]:
            detail.append(("  + " + str(line), "dim"))
        for line in (report.get("uncertainties") or [])[:2]:
            detail.append(("  ? " + str(line), "warn"))
        detail.append(("Architecture: {}   OS: {}".format(g.architecture, g.os), "normal"))
        prefix = self.db.get("prefixes", g.id)
        if prefix:
            detail.append(("Wine prefix: {} ({})".format(prefix.get("detected_architecture") or prefix.get("requested_architecture"), prefix.get("state")), "normal"))
        settings = self.db.get("game_settings", g.id)
        if settings:
            detail.append(("Wine settings: " + ", ".join(sorted(settings)), "dim"))
        if launcher:
            detail.append(("Executable: " + launcher["executable"], "normal" if Path(launcher["executable"]).is_file() else "bad"))
        else:
            detail.append(("Executable: not registered (x to find it)", "warn" if installed else "dim"))
        last = max((v for v in self.db.mapping("launches").values() if v.get("game_id") == g.id and v.get("purpose", "game") == "game"), key=lambda v: v.get("started_at", ""), default=None)
        if last:
            detail.append(("Last launch: {} ({}{})".format(last.get("started_at", "?")[:19].replace("T", " "), last.get("state"), "" if last.get("exit_code") in (None, 0) else ", exit {}".format(last["exit_code"])), "bad" if last.get("exit_code") else "normal"))
        job = self.db.get("install_jobs", g.id)
        if job and job.get("status") not in {"done", "cancelled"}:
            detail.append(("Install job: {} at step {}".format(job["status"], job.get("step")), "warn"))
        manifest = self.db.get("installs", g.id)
        if manifest:
            try:
                size, partial = dirsize(self.config.directory("installed") / manifest["directory"])
                detail.append(("Storage: {}{} extracted".format(human(size), "+" if partial else ""), "normal"))
            except AppError:
                pass
        actions = {"d": ("queue download", ["download", "add", g.id]), "i": ("install", ["install", g.id]), "s": ("settings", ["settings", "game", g.id]),
                   "w": ("wine status", ["wine", "status", g.id]), "x": ("find executable", ["discover", g.id]), "c": ("compatibility details", ["details", g.id])}
        if launcher or installed:
            actions["l"] = ("launch", ["launch", g.id])
        actions["f"] = ("toggle favorite", ["favorite", "toggle", g.id])
        tag = "{}{}".format(status if status != "Unknown" else "", " ●" if installed else "")
        return Row(g.id, g.title, tag.strip(), TONE.get(status, "dim"), detail, actions, favorite, bool(installed))

    def rows_library(self, search, show_all):
        return self.game_rows(search, show_all)

    def rows_installed(self, search, show_all):
        return [r for r in self.game_rows(search, True) if r.installed]

    def rows_gamevault(self, search, show_all):
        return self.game_rows(search, show_all, source="gamevault")

    # -- other sections ----------------------------------------------------------------------------------------
    def rows_downloads(self, search, show_all):
        rows = []
        for r in self.db.all("downloads"):
            j = r["value"]
            total = j.get("expected_size") or 0
            pct = "{}%".format(int(j.get("received", 0) * 100 / total)) if total else human(j.get("received", 0))
            tone = {"completed": "ok", "failed": "bad", "running": "warn"}.get(j.get("state"), "dim")
            detail = [("Game: {} ({})".format(j.get("title"), j.get("game_id")), "normal"), ("State: {}".format(j.get("state")), tone),
                      ("Received: {} of {}".format(human(j.get("received", 0)), human(total) if total else "unknown"), "normal")]
            if j.get("error"):
                detail.append(("Error: " + str(j["error"]), "bad"))
            acts = {"R": ("run queue", ["download", "run"])}
            if j.get("state") in {"failed", "paused", "cancelled"}:
                acts["t"] = ("retry", ["download", "retry", j["id"]])
            if j.get("state") in {"queued", "running", "paused", "failed"}:
                acts["c"] = ("cancel", ["download", "cancel", j["id"]])
            rows.append(Row(j["id"], j.get("title", j["id"]), "{} {}".format(j.get("state"), pct), tone, detail, acts))
        return rows

    def rows_compatibility(self, search, show_all):
        key = normalize(search)
        rows = []
        for g in self.lib().games:
            rep = self.lib().reports.get(g.id, {})
            if rep.get("status", "Unknown") == "Unknown" and not show_all:
                continue
            if key and key not in normalize(g.title):
                continue
            detail = [("Status: " + rep.get("status", "Unknown"), TONE.get(rep.get("status"), "dim"))]
            detail += [("  + " + str(e), "dim") for e in (rep.get("evidence") or [])[:6]]
            detail += [("  ? " + str(u), "warn") for u in (rep.get("uncertainties") or [])[:4]]
            rows.append(Row(g.id, g.title, rep.get("status", "Unknown"), TONE.get(rep.get("status"), "dim"), detail, {"c": ("full report", ["details", g.id])}))
        return rows

    def rows_wine(self, search, show_all):
        from ...runtime import winetools

        rows = []
        for i in winetools.doctor(self.config, self.db)["checks"]:
            rows.append(Row(i["check"], i["check"], i["status"].upper(), STATUS_TONE.get(i["status"], "dim"), [(i["detail"], "normal")] + ([("Fix: " + i["fix"], "warn")] if i["fix"] else []), {"D": ("wine doctor", ["wine", "doctor"])}))
        for p in self.db.all("prefixes"):
            rows.append(Row("prefix:" + p["key"], "prefix " + p["key"], p["value"].get("state", "?"), "ok" if p["value"].get("state") == "ready" else "warn",
                            [("Architecture: {}".format(p["value"].get("detected_architecture") or p["value"].get("requested_architecture")), "normal")],
                            {"k": ("check", ["prefix", "check", p["key"]]), "b": ("backup", ["prefix", "backup", p["key"]]), "v": ("inspect", ["prefix", "inspect", p["key"]])}))
        return rows

    def rows_audio(self, search, show_all):
        from ...audio.diagnose import diagnose

        report = diagnose()
        rows = [Row("verdict", "Verdict: " + report["verdict"], "", "ok" if report["verdict"] == "working" else "warn", [("Run 'audio doctor' for the full report", "dim")], {"t": ("play test tone", ["audio", "test"])})]
        for layer in report["layers"]:
            rows.append(Row(layer["layer"], layer["layer"], layer["status"].upper(), STATUS_TONE.get(layer["status"], "dim"), [(layer["detail"], "normal")] + [(line, "warn") for line in (layer["fix"] or "").splitlines()]))
        return rows

    def rows_settings(self, search, show_all):
        rows = [Row(k, k, str(v)[:30], "normal", [("{} = {}".format(k, v), "normal")]) for k, v in sorted(self.config.values.items()) if k not in {"remote_audio"}]
        rows.append(Row("remote", "remote audio", "on" if (self.config.values.get("remote_audio") or {}).get("enabled") else "off", "dim", [("Browser audio bridge (optional)", "normal")], {"s": ("status", ["audio", "remote", "status"])}))
        return rows

    def rows_diagnostics(self, search, show_all):
        from ...diagnostics import doctor

        report = doctor.run(self.config, self.db, verbose=True)
        return [Row(c["group"] + ":" + c["check"], "{}: {}".format(c["group"], c["check"]), c["status"].upper(), STATUS_TONE.get(c["status"], "dim"),
                    [(c["detail"], "normal")] + ([("Fix: " + c["fix"], "warn")] if c["fix"] else []), {"D": ("doctor", ["doctor", "--verbose"])}) for c in report["checks"]]
