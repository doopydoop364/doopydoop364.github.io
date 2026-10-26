"""Layered audio diagnosis with explicit, separate verdicts (device / server / wine / environment / vm)."""

import re

from .probe import Probe, alsa_cards, alsa_playback, server_info, virtualization, wine_driver

QEMU_ADVICE = [
    "On the HOST (not changed by Arch Games): add an audio device to this VM's QEMU command line, e.g.",
    "  -audiodev pipewire,id=snd0 -device intel-hda -device hda-duplex,audiodev=snd0",
    "  (older QEMU: -audiodev pa,id=snd0,server=unix:/run/user/1000/pulse/native)",
    "Restart the VM yourself afterwards; Arch Games never edits host services or restarts VMs.",
]


def layer(name, status, detail, fix=None):
    return {"layer": name, "status": status, "detail": detail, "fix": fix}


def diagnose(probe=None, prefix_user_regs=None, confirmations=None):
    probe = probe or Probe()
    virt = virtualization(probe)
    cards = alsa_cards(probe)
    playback = alsa_playback(probe)
    server = server_info(probe)
    drivers = wine_driver(probe, prefix_user_regs or {})
    layers = []

    has_device = bool(cards["cards"]) or bool(playback["devices"])
    if has_device:
        names = ", ".join(c["name"] for c in cards["cards"]) or "ALSA playback device listed"
        status = "ok"
        fix = None
        if cards["nodes_accessible"] is False:
            status, fix = "warn", "Add your user to the 'audio' group (explicit admin action: sudo usermod -aG audio $USER) and log in again"
        layers.append(layer("guest-device", status, "sound card present: " + names, fix))
    else:
        detail = "no sound card visible to the kernel (/proc/asound/cards empty or missing)"
        fix = None
        if virt["qemu"] or virt["virtualized"]:
            detail += "; this is a {} guest, so the VM has no audio hardware attached".format(virt["kind"])
            fix = "\n".join(QEMU_ADVICE)
        else:
            fix = "Check that a sound card exists (lspci) and the snd driver loaded (dmesg | grep -i snd)"
        layers.append(layer("guest-device", "fail", detail, fix))

    playback_nodes = [n for n in cards["device_nodes"] if re.fullmatch(r"pcmC\d+D\d+p", n)]
    alsa_direct = bool(playback["devices"]) or bool(playback_nodes)
    if has_device:
        if playback["devices"]:
            layers.append(layer("alsa-playback", "ok", "direct ALSA playback available: " + "; ".join(d.split(",")[0] + "," + d.split(",")[1] if "," in d else d for d in playback["devices"][:3])))
        elif playback_nodes:
            layers.append(layer("alsa-playback", "ok", "ALSA playback device nodes exist ({}); `aplay` is not installed, so they are not listed".format(", ".join(playback_nodes[:3])), "Optional: install alsa-utils to list and test ALSA devices"))
        else:
            layers.append(layer("alsa-playback", "warn", "no ALSA playback device node was found for the card", "Check `dmesg | grep -i snd` and that the audio group/permissions are right"))

    reachable = server["reachable"]
    if reachable:
        layers.append(layer("guest-audio-server", "ok", "{} reachable".format(server["server_name"] or "server")))
        if not server["sinks"]:
            layers.append(layer("output-sink", "fail", "server is running but exposes no output sinks", "The server cannot see the device; restart the user audio service: systemctl --user restart pipewire pipewire-pulse"))
        else:
            muted = server["default_sink_muted"]
            layers.append(layer("output-sink", "warn" if muted else "ok", "default sink {}{}".format(server["default_sink"] or "(none set)", " is MUTED" if muted else ""), "pactl set-sink-mute @DEFAULT_SINK@ 0" if muted else None))
    elif reachable is None:
        any_socket = any(server["sockets"].values())
        layers.append(layer("guest-audio-server", "info" if alsa_direct else "warn", "pactl is not installed, so a Pulse-compatible server could not be queried" + ("; a server socket exists" if any_socket else ""), "Optional: install libpulse (pactl) to diagnose a PulseAudio/PipeWire server; direct ALSA output does not need one"))
    elif alsa_direct:
        layers.append(layer("guest-audio-server", "info", "No PulseAudio/PipeWire server is running. That is fine for programs that use ALSA directly (Wine can use its ALSA driver); a server is only needed by applications that require one, or to mix several at once.", "Optional, not required: systemctl --user enable --now pipewire pipewire-pulse wireplumber"))
    else:
        any_socket = any(server["sockets"].values())
        layers.append(layer("guest-audio-server", "fail", "no audio server answered" + (" (a socket exists but is unresponsive)" if any_socket else " and no server socket exists") + ", and no ALSA playback device could be confirmed", "Start it in your session: systemctl --user enable --now pipewire pipewire-pulse wireplumber"))

    if drivers:
        for label, driver in drivers.items():
            if driver is None:
                layers.append(layer("wine-driver:" + label, "ok", "no explicit audio driver set; Wine auto-selects"))
            elif "pulse" in driver and not reachable:
                layers.append(layer("wine-driver:" + label, "warn", "prefix prefers PulseAudio but no Pulse-compatible server is reachable", "Start pipewire-pulse, or set the prefix driver to alsa with winecfg"))
            else:
                layers.append(layer("wine-driver:" + label, "ok", "driver: " + driver))
    for item in confirmations or []:
        state = {"works": ("ok", "you confirmed audio works"), "issues": ("warn", "you reported audio problems"), "none": ("warn", "you reported no sound")}.get(item.get("audio"))
        if state:
            layers.append(layer("wine-audio-confirmed:" + str(item.get("game")), state[0], "{} for {} on this machine (Wine audio, as you tested it)".format(state[1], item.get("title") or item.get("game"))))
    env = probe.environ
    session = bool(env.get("XDG_RUNTIME_DIR"))
    layers.append(layer("session-environment", "ok" if session else "warn", "XDG_RUNTIME_DIR " + ("set" if session else "unset: user audio servers cannot be found, e.g. when using su/KMSCON without a login session"), None if session else "Log in through a proper session (loginctl) or export XDG_RUNTIME_DIR=/run/user/$(id -u)"))

    verdict = overall(layers, has_device, reachable, server, alsa_direct)
    layers.append(layer("remote-delivery", "info", "Delivery to a browser/noVNC viewer is separate from guest audio; see 'remote-audio' docs. Host routing cannot be observed from inside the guest.", None))
    return {
        "verdict": verdict,
        "virtualization": virt,
        "alsa": cards | {"playback_devices": playback["devices"]},
        "server": server,
        "wine_drivers": drivers,
        "layers": layers,
    }


def overall(layers, has_device, reachable, server, alsa_direct=False):
    if not has_device and reachable and server["sinks"]:
        return "server-only"  # e.g. a network sink (module-tunnel) with no local card
    if not has_device:
        return "no-audio-device"
    if reachable and server["sinks"]:
        muted = server["default_sink_muted"]
        return "muted" if muted else "working"
    if reachable:
        return "server-without-sinks"
    return "alsa-direct" if alsa_direct else "device-without-server"


def devices(probe=None):
    probe = probe or Probe()
    cards = alsa_cards(probe)
    return {"alsa_cards": cards["cards"], "alsa_playback": alsa_playback(probe)["devices"], "pci_audio": cards["pci_audio"], "pulse": server_info(probe)}


HEALTHY = {"working", "alsa-direct"}


def diagnose_for(config, db, probe=None):
    """The diagnosis with this machine's prefixes and your own audio confirmations (one place for CLI, TUI, web and doctor)."""
    from ..compatibility import intelligence
    from ..runtime.discovery import prefix_path

    regs = {}
    for row in db.all("prefixes"):
        path = prefix_path(config, db, row["key"])
        if path:
            regs[row["key"]] = path / "user.reg"
    machine = intelligence.machine_identity(config)["id"]
    confirmations = [
        {"game": r["game_id"], "title": r.get("title"), "audio": r.get("audio_status")}
        for r in intelligence.all_records(db, machine)
        if r.get("audio_status") not in (None, "unknown")
    ]
    return diagnose(probe, regs, confirmations)
