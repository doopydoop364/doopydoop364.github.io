"""Builds TUI rows from the shared services (library, launchers, downloads, wine, audio, diagnostics)."""


from ...catalog.library import Library
from ...compatibility import intelligence
from ...core.ids import local_id
from ...identity.resolver import normalize
from .model import Row

TONE = {"Verified working": "ok", "Working with issues": "warn", "Likely compatible": "ok", "Requires configuration": "warn",
        "Likely incompatible": "bad", "Unsupported": "bad", "Unknown": "dim",
        "Verified Working": "ok", "Promising": "ok", "Experimental": "warn", "Unlikely": "warn"}
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

    def invalidate(self, reprobe=True):
        """Reload local data (never the network). reprobe=False keeps the measured system profile (saves ~0.5 s)."""
        self.cache.clear()
        profile = self.library.profile if (self.library is not None and not reprobe) else None
        self.library = Library(self.config, self.db, profile).load(offline=True)

    def lib(self):
        if self.library is None:
            self.library = Library(self.config, self.db).load(offline=True)
        return self.library

    def header(self):
        p = self.lib().profile
        ram = p.get("ram_total")
        return "{} | {}".format(p.get("os_architecture", "?"), "{:.1f} GiB RAM".format(ram / 2**30) if ram else "RAM ?")

    GAME_SECTIONS = ("Library", "Installed", "GameVault")

    def gamevault_state(self):
        """('disconnected'|'not-synced'|'sync-failed'|'empty'|'ok', detail): why the GameVault list looks the way it does."""
        if not self.config.values.get("server_url") or not self.config.secrets.exists():
            return "disconnected", ""
        lib = self.lib()
        if any(g.source == "gamevault" for g in lib.games):
            return ("sync-failed", lib.sync.get("warning", "")) if lib.sync.get("warning") else ("ok", "")
        notes = [w for w in lib.warnings if "acman" not in w]
        if any("No cached GameVault library" in w for w in notes):
            return "not-synced", ""
        if notes:
            return "sync-failed", notes[0]
        return "empty", ""

    def hidden_count(self, section, search=""):
        """How many games the current compatibility filter hides (0 when it is showing everything)."""
        memo = ("hidden", section)
        if memo in self.cache:  # the status line asks on every redraw; the answer only changes on refresh
            return self.cache[memo]
        source = {"GameVault": "gamevault"}.get(section, "all")
        library = self.lib()
        shown, _ = library.filtered(search="", show_all=False, source=source, **({"installed": True} if section == "Installed" else {}))
        everything, _ = library.filtered(search="", show_all=True, source=source, **({"installed": True} if section == "Installed" else {}))
        self.cache[memo] = max(len(everything) - len(shown), 0)
        return self.cache[memo]

    def empty_text(self, section, search="", show_all=True):
        """Say precisely why a list is empty: not connected, sync problem, empty catalog, no search match or filtered out."""
        if section in self.GAME_SECTIONS and search:
            return 'No game matches "{}". Esc clears the search.'.format(search)
        if section == "GameVault":
            state, detail = self.gamevault_state()
            if state == "disconnected":
                return "GameVault is not connected on this machine. Connect with: arch-games gamevault connect"
            if state == "not-synced":
                return "Connected, but this machine has no cached GameVault library yet. Press S to sync (needs the network)."
            if state == "sync-failed":
                return "GameVault synchronization failed ({}). Press S to retry.".format(detail or "see 'arch-games gamevault status'")
            if state == "empty":
                return "Connected and synchronized: your GameVault library is empty."
        if section in self.GAME_SECTIONS and not show_all and self.hidden_count(section):
            return "{} games are hidden by the compatibility filter. Press a to show them all.".format(self.hidden_count(section))
        return {"Library": "The catalog is empty. Pacman games need pacman; GameVault games need a connection.",
                "Installed": "Nothing is installed or registered on this machine yet.",
                "GameVault": "No GameVault games.", "Downloads": "No downloads queued."}.get(section, "Nothing to show.")

    def status_text(self, section, count, show_all, search=""):
        if section not in self.GAME_SECTIONS:
            return "{} entr{}".format(count, "y" if count == 1 else "ies")
        text = "{} game{} in {}".format(count, "" if count == 1 else "s", section)
        if search:
            return text + ' matching "{}"'.format(search)
        if section == "GameVault":
            state, detail = self.gamevault_state()
            if state == "sync-failed":
                text += " (last sync failed: showing cached data)"
        hidden = 0 if show_all else self.hidden_count(section)
        if hidden:
            text += "; {} hidden by the compatibility filter (a shows all)".format(hidden)
        elif show_all:
            text += "; showing everything, unknown games are labelled"
        return text

    def section_actions(self, section):
        if section == "GameVault":
            return {"S": ("sync library now", ["gamevault", "list"])}
        return {}

    # -- compatibility feedback (the same service as `arch-games compat set`) -----------------------------------
    def feedback_state(self, game_id):
        record = intelligence.get(self.db, intelligence.machine_identity(self.config)["id"], local_id(self.db, game_id)) or {}
        return {"gameplay": record.get("gameplay_status", "unverified"), "audio": record.get("audio_status", "unknown")}

    # -- launch settings (the same services as `arch-games launcher ...`) --------------------------------------
    def launch_settings(self, game_id):
        from ...runtime import gamelaunch
        from ...runtime.launchers import LauncherStore
        from ...runtime.pacman import PacmanService
        from ...catalog.pacman import default_run

        state_id = local_id(self.db, game_id)
        record = LauncherStore(self.config, self.db).get(state_id)
        if record is None:
            return None
        status = gamelaunch.status(self.config, self.db, state_id, PacmanService(run=default_run()), deep=False)
        return {"state_id": state_id, "record": record, "status": status}

    def save_launch_settings(self, game_id, draft):
        from ...runtime import gamelaunch, profiles

        gamelaunch.edit(self.config, self.db, game_id, lambda record: profiles.apply_user_settings(record, draft))
        self.invalidate(reprobe=False)

    def save_feedback(self, game_id, gameplay, audio):
        state_id = local_id(self.db, game_id)
        stored = self.feedback_state(game_id)
        changes = {}
        if gameplay != stored["gameplay"]:
            changes["gameplay_status"] = gameplay
        if audio != stored["audio"]:
            changes["audio_status"] = audio
        if changes:  # only changed facts get a new evidence timestamp
            intelligence.update(self.db, self.config, state_id, **changes)
        self.invalidate(reprobe=False)
        return self.lib().reports.get(game_id, {}).get("classification", "Unknown")

    def rows(self, section, search="", show_all=False):
        method = getattr(self, "rows_" + section.lower())
        if section in self.GAME_SECTIONS or section in {"Downloads", "Compatibility"}:
            return method(search, show_all)
        # Wine/Audio/Settings/Diagnostics run subprocesses: compute once, reuse when switching tabs, recompute on refresh.
        if section not in self.cache:
            self.cache[section] = method(search, show_all)
        return self.cache[section]

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
            if key and not (key in normalize(g.title) or key in g.id.casefold() or any(key in a for a in aliases.get(g.id, []) + aliases.get(g.id.split("@", 1)[0], []))):
                continue
            root = g.id.split("@", 1)[0]  # bare and versioned IDs are one game
            rows.append(self.game_row(g, library.reports.get(g.id, {}), g.id in favorites or root in favorites,
                                      aliases.get(g.id, []) + (aliases.get(root, []) if root != g.id else [])))
        rows.sort(key=lambda r: (not r.favorite, r.label.casefold()))
        return rows

    def game_row(self, g, report, favorite, aliases):
        from ...core.gameview import describe

        d = describe(self.config, self.db, g, report, aliases, favorite)
        status = d["compatibility"]["classification"]
        installed = d["installed"]
        detail = [("Source: {}   ID: {}".format(g.source, g.id), "normal")]
        if aliases:
            detail.append(("Aliases: " + ", ".join(aliases), "dim"))
        detail.append(("State: " + ("installed" if installed else "not installed"), "ok" if installed else "dim"))
        nat = d.get("native")
        if nat:
            tone = {"ready": "ok", "installed-no-launcher": "warn", "launcher-invalid": "bad", "package-removed": "bad"}.get(nat["status"], "dim")
            detail.append(("Native Linux package {}{}: {}".format(nat["package"], " " + nat["version"] if nat["version"] else "", nat["label"]), tone))
        launcher = d.get("launcher")
        if launcher and launcher["profiles"] != ["default"]:
            detail.append(("Launch profiles: {} (default: {})".format(", ".join(launcher["profiles"]), launcher["default_profile"]), "dim"))
        elif launcher and (launcher["settings"]["default"]["arguments"] or launcher["settings"]["default"]["environment"]):
            detail.append(("Launch settings saved: {} argument(s), {} environment override(s) (o to edit)".format(
                len(launcher["settings"]["default"]["arguments"]), len(launcher["settings"]["default"]["environment"])), "dim"))
        detail.append(("Compatibility: {}".format(status), TONE.get(status, "dim")))
        detail += [("  = " + str(x), "normal") for x in d["compatibility"]["reasons"][:2]]
        detail += [("  + " + str(x), "dim") for x in d["compatibility"]["evidence"][:3]]
        detail += [("  ? " + str(x), "warn") for x in d["compatibility"]["uncertainties"][:2]]
        uc = (d["compatibility"]["facts"] or {}).get("user_confirmed") or {}
        detail.append(("Confirmed here: gameplay {}, audio {}, launch {}".format(uc.get("gameplay", "unverified"), uc.get("audio", "unknown"), uc.get("launch", "never")), "dim"))
        detail.append(("Architecture: {}   OS: {}".format(g.architecture, g.os), "normal"))
        if d["prefix"]:
            detail.append(("Wine prefix: {} ({})".format(d["prefix"]["detected_architecture"] or d["prefix"]["requested_architecture"], d["prefix"]["state"]), "normal"))
        if d["wine_settings"]:
            detail.append(("Wine settings: " + ", ".join(sorted(d["wine_settings"])), "dim"))
        if d["launcher"]:
            detail.append(("Executable: " + d["launcher"]["executable"], "normal" if d["launcher"]["exists"] else "bad"))
        else:
            detail.append(("Executable: not registered (x to find it)", "warn" if installed else "dim"))
        last = d["last_launch"]
        if last:
            failed = last.get("exit_code") not in (None, 0)
            detail.append(("Last launch: {} ({}{})".format(last.get("started_at", "?")[:19].replace("T", " "), last.get("state"), ", exit {}".format(last["exit_code"]) if failed else ""), "bad" if failed else "normal"))
        if d["install_job"]:
            detail.append(("Install job: {} at step {}".format(d["install_job"]["status"], d["install_job"]["step"]), "warn"))
        if d["storage"]:
            detail.append(("Storage: {}{} extracted".format(human(d["storage"]["bytes"]), "+" if d["storage"]["partial"] else ""), "normal"))
        actions = {"d": ("queue download", ["download", "add", g.id]), "i": ("install", ["install", g.id]), "s": ("settings", ["settings", "game", g.id]),
                   "w": ("wine status", ["wine", "status", g.id]), "x": ("find executable", ["discover", g.id]), "c": ("compatibility details", ["details", g.id])}
        if installed:
            actions["l"] = ("launch", ["launch", g.id])
        actions["f"] = ("toggle favorite", ["favorite", "toggle", g.id])
        tag = "{}{}".format(status, " ●" if installed else "")
        return Row(g.id, g.title, tag.strip(), TONE.get(status, "dim"), detail, actions, favorite, installed)

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
            if rep.get("classification", "Unknown") == "Unknown" and not show_all:
                continue
            if key and key not in normalize(g.title):
                continue
            cls = rep.get("classification", "Unknown")
            detail = [("Classification: " + cls, TONE.get(cls, "dim"))] + [("  = " + r, "normal") for r in rep.get("classification_reasons", [])]
            detail += [("  + " + str(e), "dim") for e in (rep.get("evidence") or [])[:6]]
            detail += [("  ? " + str(u), "warn") for u in (rep.get("uncertainties") or [])[:4]]
            rows.append(Row(g.id, g.title, cls, TONE.get(cls, "dim"), detail, {"c": ("full report", ["compat", "show", g.id])}))
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
        from ...audio.diagnose import HEALTHY, diagnose_for

        report = diagnose_for(self.config, self.db)
        rows = [Row("verdict", "Verdict: " + report["verdict"], "", "ok" if report["verdict"] in HEALTHY else "warn", [("Run 'audio doctor' for the full report", "dim")], {"t": ("play test tone", ["audio", "test"])})]
        for layer in report["layers"]:
            rows.append(Row(layer["layer"], layer["layer"], layer["status"].upper(), STATUS_TONE.get(layer["status"], "dim"), [(layer["detail"], "normal")] + [(line, "warn") for line in (layer["fix"] or "").splitlines()]))
        return rows

    def rows_settings(self, search, show_all):
        rows = [Row(k, k, str(v)[:30], "normal", [("{} = {}".format(k, v), "normal")]) for k, v in sorted(self.config.values.items()) if k not in {"remote_audio"}]
        rows.append(Row("remote", "remote audio", "on" if (self.config.values.get("remote_audio") or {}).get("enabled") else "off", "dim", [("Browser audio bridge (optional)", "normal")], {"s": ("status", ["audio", "remote", "status"])}))
        return rows

    def rows_diagnostics(self, search, show_all):
        from ...diagnostics import doctor

        report = doctor.run(self.config, self.db, verbose=True, probe_graphics=False)
        return [Row(c["group"] + ":" + c["check"], "{}: {}".format(c["group"], c["check"]), c["status"].upper(), STATUS_TONE.get(c["status"], "dim"),
                    [(c["detail"], "normal")] + ([("Fix: " + c["fix"], "warn")] if c["fix"] else []), {"D": ("doctor", ["doctor", "--verbose"])}) for c in report["checks"]]
