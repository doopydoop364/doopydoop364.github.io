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

Verdicts: `working`, `muted`, `no-audio-device`, `device-without-server`, `server-without-sinks`, `server-only`.
In a QEMU/KVM guest with no sound card, the fix text shows the host QEMU flags to add (`-device intel-hda -device hda-output,...`); Arch Games prints
them but never changes the host or restarts a VM. `audio devices` lists ALSA/PCI/Pulse devices. `audio test [--seconds N] [--backend auto|pulse|alsa]` plays a
bounded (<=10 s) 440 Hz tone with `paplay`/`pw-play`/`aplay`, unprivileged; success means the player exited cleanly, not that you heard it.
Remote delivery: see REMOTE_AUDIO_3.md.
