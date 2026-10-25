# Arch Games audio bridge (runs on the VM host, e.g. CachyOS)

Streams one VM's audio to a browser. Stdlib-only Python 3.10+; it lives in `arch_games/remote/` and runs as
`python -m arch_games.remote` from a checkout (or the zipapp from `make-pyz.sh`). Nothing here is deployed for you.

    ./make-pyz.sh                       # builds arch-games-audio-bridge.pyz (copy it to the host yourself)
    python arch-games-audio-bridge.pyz init --vm arch64=arch64_vm --vm arch86=arch86_vm
    python arch-games-audio-bridge.pyz sink create arch64      # live null sink, no persistence
    python arch-games-audio-bridge.pyz qemu arch64             # prints QEMU flags; you edit the VM, then restart it
    python arch-games-audio-bridge.pyz doctor
    python arch-games-audio-bridge.pyz serve                   # 127.0.0.1:8787, token required

See docs/REMOTE_AUDIO_3.md for the architecture, security model and Tailscale setup.
