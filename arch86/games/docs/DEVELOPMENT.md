# Development and contribution

Use Python 3.10+; runtime is standard-library only. libarchive is an optional system
runtime for archive operations, normally present through Arch's base tooling.
No Textual dependency is needed; curses/plain text handles small target machines.

From arch86/games:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q arch_games tests
python3 -m arch_games --help
python3 -m arch_games --state-dir /tmp/arch-games-dev doctor
sh -n install.sh uninstall.sh
```

Optional development lint: `ruff check .` (configuration in pyproject.toml). A
GitHub Actions workflow exercises supported Python versions. Tests create small
synthetic files and loopback services; no private accounts, games, sudo or live
Wine are required. Fixture archives contain only original synthetic text, and
executable fixtures are nonfunctional header bytes. Do not add real game binaries,
credentials, installed data, logs or private-server snapshots to source control.

Layers are described in ARCHITECTURE.md. Preserve command aliases and the original
catalog. Add focused behavior tests for archive/network/state changes. Do not add
dependencies or execute untrusted targets merely to detect compatibility. New
remote API behavior needs a primary-source contract and fixture. Track provenance
and uncertainty instead of inventing requirements from game titles.

Development branch: feature/arch-games-2, based on
arch86-tailscale-autostart at 5fc38b573da8412b67c65023fe12765af8736943.
Each phase is reviewed, tested and committed with a corresponding
arch-games-2-phase-NN tag. DEVELOPMENT_PROGRESS.md records scope and limitations;
`git rev-parse arch-games-2-phase-01` (etc.) resolves the commit. No main-branch
merge, server change or deployment is part of this task.
