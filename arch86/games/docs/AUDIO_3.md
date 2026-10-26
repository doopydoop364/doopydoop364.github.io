# Audio diagnostics

`arch-games audio doctor [--json]` measures separate layers and never conflates them:

| Layer | Question | Source |
|---|---|---|
| guest-device | Does the kernel see a sound card? | `/proc/asound/cards`, `/dev/snd`, `lspci` |
| guest-audio-server | Does a PulseAudio-compatible server (pipewire-pulse) answer? | `pactl info` |
| output-sink | Is there a default sink and is it muted? | `pactl list short sinks`, `get-sink-mute` |
| wine-driver:GAME | Which driver does each prefix prefer, and is its server up? | `user.reg` `[Software\Wine\Drivers]` |
| session-environment | Is `XDG_RUNTIME_DIR` set (needed to find user servers)? | environment |
| remote-delivery | Informational: browser delivery and host routing are outside the guest. | - |

Verdicts: `working` (server + sink), `muted`, `alsa-direct` (sound card and direct ALSA playback, no PulseAudio/PipeWire server: normal and fine for Wine's ALSA driver; the server layer is
informational, not a failure), `no-audio-device`, `device-without-server` (a card but neither a server nor an ALSA playback device could be confirmed), `server-without-sinks`, `server-only`.
Extra layers: `alsa-playback` (devices listed by `aplay -l`, or `pcmC*D*p` nodes when `aplay` is missing) and `wine-audio-confirmed:GAME` (your own audio feedback for this machine, from `compat set` or the TUI `v` panel).
Remote delivery stays a separate, informational layer. Diagnosis never plays sound; only `audio test` does.
In a QEMU/KVM guest with no sound card, the fix text shows the host QEMU flags to add (`-device intel-hda -device hda-output,...`); Arch Games prints
them but never changes the host or restarts a VM. `audio devices` lists ALSA/PCI/Pulse devices. `audio test [--seconds N] [--backend auto|pulse|alsa]` plays a
bounded (<=10 s) 440 Hz tone with `paplay`/`pw-play`/`aplay`, unprivileged; success means the player exited cleanly, not that you heard it.
Remote delivery: see REMOTE_AUDIO_3.md.

### A game that is silent while `audio doctor` says ALSA works
Some games pick the wrong backend (JACK/Pulse) inside a VM. Save the game's own audio settings once as a launch profile (NATIVE_3.md), for example OpenTTD on the Arch64 VM:
`SDL_AUDIODRIVER=alsa AUDIODEV=plughw:0,0` and argument `-s sdl`. That configuration was tested on that VM only; it is an example, not a default.
