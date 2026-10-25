# Arch Games 2.0 architecture and audit

Baseline: `5fc38b573da8412b67c65023fe12765af8736943`, branch
`arch86-tailscale-autostart`. The original 146-line script parses 69 pipe-delimited
records, queries Pacman sync and installed databases, prompts before `sudo pacman
-S --needed`, and serves an escaped, read-only gallery on 127.0.0.1:8765.

Preserve the catalog and old command names. Reuse its records and visual thumbnail
concept. The old script has import-time subprocess/file access, crashes without
Pacman, calls repository availability "compatibility", offers no persistent state,
and its installer downloads mutable files individually. Replace these gradually.
Do not recommend a standalone `pacman -Sy` partial-upgrade workflow.

Python 3.10+ is the declared minimum. Standard library only at runtime. Package
layers: platform/config/models; Pacman and GameVault adapters; bounded executable
and archive inspection; conservative structured reports; SQLite local state;
download/install/prefix/launch services; CLI, plain terminal and read-only gallery.
UI layers call services; server metadata never becomes shell commands or launch
configuration. Reads never execute games or install system dependencies.

Tests use unittest and synthetic files/HTTP servers. Commands:
`python3 -m unittest discover -s tests -v` and
`python3 -m compileall -q arch_games tests`. Ruff is an optional development tool.

Live GameVault API version, private credentials, VM network, graphics and Wine
capabilities are not known. Establish API contracts from public OpenAPI and source;
keep actual VM verification distinct from simulated results.
