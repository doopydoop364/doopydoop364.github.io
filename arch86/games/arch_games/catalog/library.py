"""Compose repository availability, remote metadata and local evidence."""

from dataclasses import replace
from .pacman import load_catalog, visible
from .filters import merge_catalogs, select
from ..compatibility.analyzer import analyze
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


class Library:
    def __init__(self, config, db, profile=None):
        self.config, self.db = config, db
        self.profile = profile if profile is not None else system_profile(config.data_dir)
        from ..platform import apply_overrides

        apply_overrides(self.profile, config)
        self.games, self.reports, self.warnings = [], {}, []
        self.sync = {}

    def load(self, source="all", offline=False):
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
        installs = {row["key"]: row["value"] for row in self.db.all("installs")}
        self.games = merge_catalogs(pacman, remote)
        known = {g.id for g in self.games}
        for ident, manifest in installs.items():
            if ident not in known and manifest.get("game"):
                self.games.append(Game(**manifest["game"]))
        for game in self.games:
            game.installed = game.installed or game.id in installs
            local = self.db.get("analysis", game.id, {})
            from ..compatibility.feedback import current_feedback

            feedback = current_feedback(self.db, game.id)
            if feedback and feedback.get("binary") and not local.get("executable"):
                local = dict(local, executable=feedback["binary"])
            if game.id in installs and not local.get("executable"):
                binaries = installs[game.id].get("executables", [])
                if len(binaries) == 1:
                    local = dict(local, executable=binaries[0]["executable"])
            self.reports[game.id] = analyze(
                game, self.profile, local.get("executable"), local.get("requirements"), feedback
            ).to_dict()
        return self

    def filtered(self, **filters):
        if self.config.values.get("show_all") and "show_all" not in filters:
            filters["show_all"] = True
        return select(self.games, self.reports, **filters)

    def find(self, ident):
        game = next((g for g in self.games if g.id == ident or g.package == ident), None)
        if not game:
            raise AppError("Game is not in the current catalog; use list --all or installed")
        return game
