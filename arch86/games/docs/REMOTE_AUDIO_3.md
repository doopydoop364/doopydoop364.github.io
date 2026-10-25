# Remote browser audio (optional, off by default)

Goal: play Undertale on Arch64 through noVNC from a MacBook and hear it. VNC carries no audio, so audio travels on a
second, independent channel.

## Where each piece runs

    Arch64 guest ──QEMU audiodev──► [dedicated PipeWire null sink "arch64_vm"] (CachyOS host)
                                           │ monitor source (arch64_vm.monitor)
                          parec/pw-record ◄┘   one process per VM, only while a browser is connected
                                           │ raw s16le PCM, 20 ms frames
                      arch_games.remote bridge (host, Python stdlib) ── HTTP + WebSocket, token auth
                                           │
                      browser: Web Audio (no plugin)  ◄── same page can embed the existing noVNC in an iframe

The guest cannot reach the host's PipeWire, so nothing in the guest streams audio. The guest only (a) diagnoses its own
audio (`audio doctor`) and (b) stores where the bridge is (`audio remote configure`) and can query its status.

## Transport decision (investigated, not just assumed)

| Option | Verdict |
|---|---|
| WebRTC (GStreamer webrtcbin / Janus / mediamtx) | Lowest latency over bad networks, but needs signalling, ICE/STUN and a large dependency; Tailscale already provides a routable encrypted path. Candidate for 3.1 if Opus/low bandwidth is needed. |
| Icecast/Ogg-Opus or HLS via ffmpeg | Proven, but 1-5 s latency and a transcoding layer; poor for gameplay. |
| **WebSocket + raw PCM + Web Audio (chosen)** | Standard protocol on both ends, no transcoding, ~100-150 ms buffer measured in a real browser on loopback, works in Safari. Cost: ~1.5 Mbit/s at 48 kHz stereo (use `rate`/`channels` in the config for less: 24 kHz mono is 0.4 Mbit/s). |
| Audio from inside the guest | Possible (PulseAudio network sink), but the guest cannot see the host mixer and it re-implements the capture the host already has. Rejected for now. |

## Isolation and privacy

* Each VM has its own null sink; QEMU is pointed at it (`out.name=`). Only `<sink>.monitor` is captured.
* The bridge refuses any capture source that is not a `.monitor`, starts with `@` (e.g. `@DEFAULT_MONITOR@`) or is shared by two
  VMs, so microphones and desktop audio cannot be configured by mistake.
* Use `-device hda-output` (the printed QEMU flags do) so the guest has no input device that could reach a host microphone.
* A dedicated sink means the VM is silent on the host speakers. `sink listen-locally` prints an optional loopback command.
* Capture starts with the first connected listener and stops (process terminated) when the last one disconnects or the tab closes.

## Security model

* Disabled until you run `init`, create a sink and `serve`; nothing is installed or started for you; QEMU is never edited.
* Binds 127.0.0.1. Any other address needs `--listen ADDR --allow-nonloopback`. A 256-bit token is always required.
* The token is a 0600 file on the host. In the browser it is passed in the URL **fragment** (`#token=…`, never sent to a
  server, removed from the address bar on load) and sent as the first WebSocket message, not in the URL or headers.
* Constant-time token comparison, per-IP throttling (5 failures/min), cross-origin WebSocket refusal (CSWSH), listener cap per VM,
  frame/request size limits, `Content-Security-Policy`, `no-store`, `no-referrer`.
* Plain HTTP is acceptable only inside a WireGuard tunnel (Tailscale). Do not port-forward it to the internet. For HTTPS use
  `tailscale serve` in front of the loopback port and put its origin in `allow_origins`.
* Guest side: `audio remote configure` stores the token in `credentials.json` (0600), refuses `http://` without `--allow-http`, and never
  follows redirects with the token.

## Setup

HOST (CachyOS), from a checkout or the zipapp built by `host-tools/audio-bridge/make-pyz.sh`:

    python -m arch_games.remote init --vm arch64=arch64_vm --vm arch86=arch86_vm
    python -m arch_games.remote sink create arch64
    python -m arch_games.remote qemu arch64        # prints flags; edit the VM and restart it yourself
    python -m arch_games.remote doctor
    python -m arch_games.remote serve              # or: serve --listen <tailscale-ip> --allow-nonloopback

Persist the sink with `sink persist arch64` (prints a pipewire-pulse drop-in; you save it). Put your noVNC URL in
`bridge.json` as `novnc_url` to get `/wrapper/arch64/` (display + audio controls on one page). Otherwise open
`/vm/arch64/` in a second tab.

GUEST: `arch-games audio remote configure --url http://<host>:8787 --vm arch64 --allow-http`, then `audio remote status` / `url`.

## What has and has not been verified

Automated (tests/test_remote_audio.py, 22+4 tests): real sockets and real capture subprocesses with a synthetic source - authentication,
origin check, throttling, connect/disconnect lifecycle, abrupt disconnect cleanup, 4 reconnects, shared capture, listener cap,
missing source, capture that dies mid-stream, tool missing, two concurrent VMs receiving only their own audio, config validation.

Real browser (built-in Chromium-based pane on the CachyOS dev host, synthetic 440 Hz source, loopback): page loaded, token removed from the address
bar, "Enable audio" produced an AudioContext in state `running`, 151 frames played in 3 s, 132 ms buffer, server showed 1 listener, and Disconnect
returned the stream to `idle` with no capture process left. **Not verified:** real VM audio through a real PipeWire sink, QEMU's
`out.name`, Safari on the MacBook, Tailscale, noVNC embedding, or whether the sound is actually audible/smooth. Those are in the
real-machine checklist.
