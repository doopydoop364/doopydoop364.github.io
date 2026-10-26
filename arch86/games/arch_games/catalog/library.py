"""Compose repository availability, remote metadata and local evidence."""

from dataclasses import replace
from .pacman import load_catalog, visible
from .filters import merge_catalogs, select
from ..compatibility.analyzer import analyze
from ..compatibility.executables import is_installer, label_executable
from ..config import AppError
from ..gamevault.client import Client
from ..models import Game
from ..platform import system_profile
from ..compatibility import intelligence
from ..runtime import native
from ..compatibility.executables import inspect_file
from pathlib import Path


def expand_versions(game):
    versions = game.metadata.get("remote", {}).get("versions") or []
    if not versions:
        return [game]
    result = []
    for version in versions:
        if not isinstance(version, dict) or not str(version.get("id", "")).isdecimal():
            continue
        kind = str(version.get("type", ""))
        os_name = (
            "windows" if kind.startswith("WINDOWS") else "linux" if kind.startswith("LINUX") else "unknown"
        )
        meta = dict(game.metadata, selected_version=version)
        result.append(
            replace(
                game,
                id=game.id + "@" + str(version["id"]),
                build=str(version.get("version") or version["id"]),
                os=os_name,
                metadata=meta,
            )
        )
    return result or [game]


def alias_family(mapping, ids):
    """Let a versioned library ID (gamevault:32@34) see records kept under the bare ID, as 2.0 allowed."""
    for ident in ids:
        if "@" in ident and ident not in mapping:
            root = ident.split("@", 1)[0]
            if root in mapping:
                mapping[ident] = mapping[root]
    return mapping


def evidence_binary(local, manifest, feedback=None):
    """Keep installer architecture out of game predictions, including older records."""
    installer = (local.get("installer_analysis") or {}).get("executable")
    if installer:
        installer = dict(installer, role="installer")
    observations = [(local.get("executable"), local.get("member") or local.get("source_path"))]
    if feedback:
        observations.append((feedback.get("binary"), feedback.get("executable")))
    game_binary = None
    for binary, name in observations:
        if not binary:
            continue
        binary = label_executable(binary, name)
        if binary.get("role") == "installer":
            installer = binary
        elif game_binary is None:
            game_binary = binary
    native = []
    for entry in manifest.get("executables", []):
        binary = label_executable(entry["executable"], entry.get("name"))
        if binary.get("role") == "installer":
            installer = installer or binary
        else:
            native.append(binary)
    if game_binary is None and len(native) == 1:
        game_binary = native[0]
    return game_binary, installer


class Library:
    def __init__(self, config, db, profile=None):
        self.config, self.db = config, db
        self.profile = profile if profile is not None else system_profile(config.data_dir)
        from ..platform import apply_overrides

        apply_overrides(self.profile, config)
        self.games, self.reports, self.warnings = [], {}, []
        self.sync = {}
        self.package_versions = {}

    def load(self, source="all", offline=False):
        self.warnings, self.sync, self.reports = [], {}, {}
        pacman, remote = [], []
        if source in {"all", "pacman"}:
            custom = self.config.config_dir / "catalog.txt"
            pacman, warning = visible(load_catalog(custom) if custom.is_file() else None)
            if warning:
                self.warnings.append(warning)
        if source in {"all", "gamevault"} and self.config.values.get("server_url"):
            try:
                remote, self.sync = Client(self.config, self.db).library(offline)
            except AppError as exc:
                self.warnings.append(str(exc))
        if source in {"all", "pacman"}:
            from ..runtime.pacman import PacmanService
            from .pacman import default_run

            # The local package database is authoritative for native games, whoever installed them and whenever.
            self.package_versions = PacmanService(run=default_run()).installed_versions() if pacman else {}
        for game in pacman:
            if game.installed:
                game.metadata["package_version"] = self.package_versions.get(game.package)
        remote = [version for game in remote for version in expand_versions(game)]
        installs = self.db.mapping("installs")
        analyses = self.db.mapping("analysis")
        feedbacks = self.db.mapping("feedback")
        launches = {}
        for launch in self.db.mapping("launches").values():
            ident = launch.get("game_id")
            if ident not in launches or launch.get("started_at", "") > launches[ident].get("started_at", ""):
                launches[ident] = launch
        feedback_context = {"installs": installs, "feedback": feedbacks, "launches": launches}
        identities = self.db.mapping("catalog_identity")
        candidates = pacman + remote
        known = {game.id for game in candidates}
        versioned_roots = {i.split("@", 1)[0] for i in known if "@" in i}  # a versioned library entry represents its bare ID
        candidates += [
            Game(**manifest["game"])
            for ident, manifest in installs.items()
            if ident not in known and ident not in versioned_roots and manifest.get("game")
        ]
        known |= {game.id for game in candidates}
        for mapping in (installs, analyses, feedbacks, launches):
            alias_family(mapping, known)
        for ident, launcher in self.db.mapping("launchers").items():
            if ident not in known and ident not in versioned_roots and isinstance(launcher, dict):
                # Installed outside Arch Games' extractor (e.g. by a Wine installer) and registered by the user.
                candidates.append(Game(ident, launcher.get("title") or ident, ident.split(":", 1)[0], installed=True))
                known.add(ident)
        custom_titles = alias_family(self.db.mapping("custom_titles"), known)
        for game in candidates:
            if game.id in custom_titles and custom_titles[game.id].get("title"):
                game.metadata["original_title"] = game.title
                game.title = custom_titles[game.id]["title"]
            game.installed = game.installed or game.id in installs
            local = analyses.get(game.id, {})
            if (
                game.id in installs
                and local.get("archive_sha256")
                and local["archive_sha256"] != installs[game.id].get("archive_sha256")
            ):
                local = {}
            binary, installer = evidence_binary(local, installs.get(game.id, {}))
            if binary:
                game.architecture = binary.get("architecture", game.architecture)
            elif installer:
                game.architecture = "unknown"
            identity = identities.get(game.id)
            if identity:
                game.metadata["canonical_id"] = identity["canonical"]
                game.metadata["identity_source"] = "Explicit local user assertion"
                game.build, game.edition = identity["build"], identity.get("edition", "")
        self.games = merge_catalogs(candidates, deduplicate=source == "all")
        machine = intelligence.machine_identity(self.config)
        compat = self.db.mapping(intelligence.NAMESPACE)  # one query for every record, not one per game
        by_game = {}
        for rec in compat.values():
            if rec["machine"]["id"] != machine["id"]:
                by_game.setdefault(rec["game_id"], []).append(rec)
        launchers = alias_family(self.db.mapping("launchers"), {g.id for g in self.games})
        for game in self.games:
            game.installed = game.installed or game.id in installs
            local = analyses.get(game.id, {})
            if (
                game.id in installs
                and local.get("archive_sha256")
                and local["archive_sha256"] != installs[game.id].get("archive_sha256")
            ):
                local = {}
            from ..compatibility.feedback import current_feedback

            feedback = current_feedback(self.db, game.id, feedback_context)
            binary, installer = evidence_binary(local, installs.get(game.id, {}), feedback)
            launcher = launchers.get(game.id)
            if launcher and Path(launcher["executable"]).is_file():
                # The executable the user confirmed as the launcher is the best evidence of the game's own architecture.
                measured = inspect_file(launcher["executable"]).to_dict()
                if measured.get("os") in {"windows", "linux", "dos"} and measured.get("role") != "installer":
                    binary = measured
            if feedback and is_installer(feedback.get("binary"), feedback.get("executable")):
                feedback = None
            if binary:
                game.architecture = binary.get("architecture", game.architecture)
            elif installer:
                game.architecture = "unknown"
                game.metadata["analysis_source"] = "Installer header only; game architecture unmeasured"
            report = analyze(game, self.profile, binary or installer, local.get("requirements"), feedback).to_dict()
            record = compat.get(intelligence.key(machine["id"], game.id)) or (
                compat.get(intelligence.key(machine["id"], game.id.split("@", 1)[0])) if "@" in game.id else None
            )
            others = by_game.get(game.id, [])
            report["classification"], report["classification_reasons"] = intelligence.classify(
                report, record, installed_without_launcher=game.installed and game.id not in launchers,
                measured=bool(binary and binary is not installer),
            )
            report["facts"] = intelligence.facts(game, report, binary if binary and binary is not installer else None, installer, self.profile, record, others)
            package = native.package_of(game.id, game)
            if package:
                launcher_record = launchers.get(game.id)
                report["facts"]["native_package"] = {
                    "package": package, "installed": bool(game.installed), "version": game.metadata.get("package_version"),
                    "launcher_status": native.status(bool(game.installed), game.metadata.get("package_version"), launcher_record,
                                                     bool(launcher_record and Path(launcher_record["executable"]).exists())),
                    "executable_exists": bool(launcher_record and Path(launcher_record["executable"]).exists()),
                }
            if record and record.get("launch_profile"):
                report["facts"]["launch_configuration"] = record["launch_profile"]  # the configuration of the last launch on this machine
            report["machine"] = machine["name"]
            self.reports[game.id] = report
        return self

    def filtered(self, **filters):
        if self.config.values.get("show_all") and "show_all" not in filters:
            filters["show_all"] = True
        return select(self.games, self.reports, **filters)

    def find(self, ident, alternatives=False):
        game = next((g for g in self.games if g.id == ident or g.package == ident), None)
        if not game:
            for primary in self.games:
                alternative = next(
                    (
                        record
                        for record in primary.metadata.get("alternatives", [])
                        if record["id"] == ident or record.get("package") == ident
                    ),
                    None,
                )
                if alternative:
                    return Game(**alternative) if alternatives else primary
        if not game:
            # bare <-> versioned: gamevault:32 names the single listed gamevault:32@34 (and the reverse)
            siblings = [g for g in self.games if g.id.startswith(ident + "@")] if "@" not in ident else [g for g in self.games if g.id == ident.split("@", 1)[0]]
            if len(siblings) == 1:
                return siblings[0]
            if len(siblings) > 1:
                raise AppError("Several versions are listed for {}: {}".format(ident, ", ".join(g.id for g in siblings[:6])))
        if not game:
            raise AppError("Game is not in the current catalog; use list --all or installed")
        return game
