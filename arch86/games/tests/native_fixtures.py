"""A fake Pacman database over a temporary file tree, so native-game code runs unmodified without real packages or sudo."""

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from arch_games.runtime import native
from test_detection import elf


class FakeSystem:
    """root/usr/bin, root/usr/lib, root/usr/share/applications, root/opt ... stand in for the real directories."""

    def __init__(self, base):
        self.root = Path(base) / "sys"
        self.versions, self.package_files, self.available = {}, {}, set()
        self.calls = []
        self.owner_overrides = {}
        for d in ("usr/bin", "usr/games", "usr/lib", "usr/share/applications", "opt"):
            (self.root / d).mkdir(parents=True, exist_ok=True)

    # -- building packages ----------------------------------------------------------------------------------
    def path(self, rel):
        return str(self.root / rel)

    def write(self, rel, data=b"", mode=0o755, link_to=None):
        target = self.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            target.unlink()
        if link_to is not None:
            target.symlink_to(link_to)
        else:
            target.write_bytes(data)
            target.chmod(mode)
        return str(target)

    def add_package(self, name, version, files):
        """files: iterable of absolute paths (strings) that this package owns; directories are listed automatically."""
        self.versions[name] = version
        self.available.add(name)
        self.package_files[name] = list(files)

    def remove_package(self, name):
        for path in self.package_files.pop(name, []):
            p = Path(path)
            if p.is_symlink() or p.exists():
                p.unlink()
        self.versions.pop(name, None)

    def program(self, name, directory="usr/bin", script=False, body=b""):
        data = (b"#!/bin/sh\n" + (body or b"exit 0\n")) if script else elf(64, 62) + bytes(4096)
        return self.write("{}/{}".format(directory, name), data)

    def desktop(self, name, exec_line, display="Game", categories="Game;", hidden=False, filename=None):
        text = "[Desktop Entry]\nType=Application\nName={}\nExec={}\nCategories={}\n{}".format(display, exec_line, categories, "NoDisplay=true\n" if hidden else "")
        return self.write("usr/share/applications/{}.desktop".format(filename or name), text.encode(), mode=0o644)

    def openttd(self, version="15.3-1", script=False, body=b""):
        exe = self.program("openttd", script=script, body=body)
        desktop = self.desktop("openttd", "openttd %f", display="OpenTTD", categories="Game;Simulation;")
        data = self.write("usr/share/openttd/baseset/opengfx.grf", b"data", mode=0o644)
        self.add_package("openttd", version, [exe, desktop, data])
        return exe

    # -- the pacman command ---------------------------------------------------------------------------------
    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        assert argv[0] == "pacman", argv
        assert "sudo" not in argv
        flag, rest = argv[1], [a for a in argv[2:] if a != "--"]
        ok = lambda out="": subprocess.CompletedProcess(argv, 0, out, "")  # noqa: E731
        fail = lambda: subprocess.CompletedProcess(argv, 1, "", "error: package not found")  # noqa: E731
        if flag == "-Slq":
            return ok("\n".join(sorted(self.available)) + "\n")
        if flag == "-Qq":
            return ok("\n".join(sorted(self.versions)) + "\n")
        if flag == "-Q":
            if not rest:
                return ok("".join("{} {}\n".format(n, v) for n, v in sorted(self.versions.items())))
            return ok("{} {}\n".format(rest[0], self.versions[rest[0]])) if rest[0] in self.versions else fail()
        if flag == "-Ql":
            if rest[0] not in self.versions:
                return fail()
            return ok("".join("{} {}\n".format(rest[0], p) for p in self.package_files[rest[0]]))
        if flag == "-Qoq":
            path = rest[0]
            if path in self.owner_overrides:
                return ok(self.owner_overrides[path] + "\n")
            for name, files in self.package_files.items():
                if path in files:
                    return ok(name + "\n")
            return fail()
        raise AssertionError("unexpected pacman call: {}".format(argv))

    def query_calls(self, flag):
        return [c for c in self.calls if c[1] == flag]

    @contextmanager
    def active(self):
        """Point the trusted directories and the Pacman runner at this tree."""
        root = str(self.root)
        with patch.object(native, "TRUSTED_ROOTS", (root + "/usr", root + "/opt")), \
                patch.object(native, "STD_BIN_DIRS", (root + "/usr/bin/", root + "/usr/games/")), \
                patch.object(native, "LIB_DIRS", (root + "/usr/lib/", root + "/opt/")), \
                patch("arch_games.catalog.pacman.default_run", return_value=self.run):
            yield self


def make_installed(system, name, files):
    """Convenience for tests that only need executables to exist: installs a package and returns its paths."""
    system.add_package(name, "1.0-1", files)
    return files
