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
        candidates += [
            Game(**manifest["game"])
            for ident, manifest in installs.items()
            if ident not in known and manifest.get("game")
        ]
        known |= {game.id for game in candidates}
        for ident, launcher in self.db.mapping("launchers").items():
            if ident not in known and isinstance(launcher, dict):
                # Installed outside Arch Games' extractor (e.g. by a Wine installer) and registered by the user.
                candidates.append(Game(ident, launcher.get("title") or ident, ident.split(":", 1)[0], installed=True))
                known.add(ident)
        for game in candidates:
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
            if feedback and is_installer(feedback.get("binary"), feedback.get("executable")):
                feedback = None
            if binary:
                game.architecture = binary.get("architecture", game.architecture)
            elif installer:
                game.architecture = "unknown"
                game.metadata["analysis_source"] = "Installer header only; game architecture unmeasured"
            self.reports[game.id] = analyze(
                game, self.profile, binary or installer, local.get("requirements"), feedback
            ).to_dict()
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
            raise AppError("Game is not in the current catalog; use list --all or installed")
        return game
