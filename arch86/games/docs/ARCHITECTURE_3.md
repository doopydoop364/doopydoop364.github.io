# Arch Games 3.0 architecture

Layers (stdlib only; web/audio/perf parts are optional modules):

    ui/cli  ui/tui  ui/web        <- thin front ends, no business logic
              |
        core services             <- identity, discovery, launchers, install workflow, wine, audio, saves ...
              |
   storage (SQLite KV + migrations)   config (XDG, private files)
              |
   adapters: GameVault client, Pacman, Wine runtime, PipeWire/Pulse probes
   host-tools/audio-bridge        <- runs on the CachyOS host, not in the guest

Rules: CLI, TUI and web call the same service functions; the web layer has no state-changing operation that the CLI
lacks; nothing executes downloaded binaries without an explicit, per-action approval.
Detailed per-module notes are appended as each phase lands.
