"""Host-side control: python -m arch_games.remote {init,serve,doctor,sink,qemu}. Stdlib only."""

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys

from . import config as cfgmod
from .server import Bridge
from .stream import capture_argv, StreamError


def pactl(*args):
    if not shutil.which("pactl"):
        raise SystemExit("pactl not found: install libpulse (it talks to pipewire-pulse too)")
    return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=10)


def cmd_init(args):
    vms = {}
    for item in args.vm:
        name, _, sink = item.partition("=")
        vms[name] = sink or name.replace("-", "_") + "_vm"
    path, token_path = cfgmod.init(args.dir, vms, args.force)
    token = token_path.read_text().strip()
    print("Config: {}\nToken file: {} (mode 0600)".format(path, token_path))
    print("Open (token stays in the URL fragment, never sent to servers):")
    for name in vms:
        print("  http://127.0.0.1:8787/vm/{}/#token={}".format(name, token))


def cmd_serve(args):
    cfg = cfgmod.load(args.config)
    if args.listen:
        cfg["listen"] = args.listen
    if args.port is not None:
        cfg["port"] = args.port
    if cfg["listen"] not in cfgmod.LOOPBACK and not args.allow_nonloopback:
        raise SystemExit("Refusing to bind {}: use --listen with your Tailscale address AND --allow-nonloopback (token auth stays mandatory)".format(cfg["listen"]))
    bridge = Bridge(cfg)

    async def run():
        port = await bridge.start()
        print("Audio bridge listening on {}:{} for VMs: {}".format(cfg["listen"], port, ", ".join(cfg["vms"])), flush=True)
        try:
            await asyncio.Event().wait()
        finally:
            await bridge.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


def cmd_doctor(args):
    cfg = cfgmod.load(args.config)
    ok = True
    print("capture tool:", end=" ")
    try:
        print(capture_argv(next(iter(cfg["vms"].values())))[0])
    except StreamError as exc:
        print("MISSING -", exc)
        ok = False
    sources = pactl("list", "short", "sources").stdout if shutil.which("pactl") else ""
    names = {line.split("\t")[1] for line in sources.splitlines() if "\t" in line}
    for name, vm in cfg["vms"].items():
        present = vm["source"] in names
        ok &= present
        print("[{}] {}: source '{}' {}".format("OK" if present else "MISSING", name, vm["source"], "exists" if present else "not found; run: python -m arch_games.remote sink create " + name))
    sys.exit(0 if ok else 1)


def cmd_sink(args):
    cfg = cfgmod.load(args.config)
    vm = cfg["vms"].get(args.vm)
    if not vm:
        raise SystemExit("Unknown VM")
    if args.action == "create":
        existing = pactl("list", "short", "sinks").stdout
        if vm["sink"] in existing:
            print("Sink already exists")
            return
        result = pactl("load-module", "module-null-sink", "sink_name=" + vm["sink"], "sink_properties=device.description=ArchGames-" + args.vm)
        print(result.stdout.strip() or result.stderr.strip(), "(live only; lost when pipewire-pulse restarts)")
        print("Make it permanent: python -m arch_games.remote sink persist {}".format(args.vm))
    elif args.action == "remove":
        modules = pactl("list", "short", "modules").stdout
        for line in modules.splitlines():
            if "module-null-sink" in line and "sink_name=" + vm["sink"] in line:
                pactl("unload-module", line.split("\t")[0])
                print("Removed module", line.split("\t")[0])
    elif args.action == "persist":
        print("# Save as ~/.config/pipewire/pipewire-pulse.conf.d/arch-games-{}.conf then: systemctl --user restart pipewire-pulse".format(args.vm))
        print('pulse.cmd = [ { cmd = "load-module" args = "module-null-sink sink_name={} sink_properties=device.description=ArchGames-{}" flags = [ ] } ]'.format(vm["sink"], args.vm))
    elif args.action == "listen-locally":
        print("Optional: also hear this VM on the host speakers (the null sink otherwise keeps it silent):")
        print("pactl load-module module-loopback source={} sink=@DEFAULT_SINK@ latency_msec=40".format(vm["source"]))


def cmd_qemu(args):
    cfg = cfgmod.load(args.config)
    vm = cfg["vms"].get(args.vm)
    if not vm:
        raise SystemExit("Unknown VM")
    print("# Add to the QEMU command line for '{}' (printed only, nothing is modified; restart the VM yourself)".format(args.vm))
    print("-audiodev pipewire,id=snd0,out.name={}".format(vm["sink"]))
    print("-device intel-hda -device hda-output,audiodev=snd0")
    print("# hda-output is output-only: the VM cannot capture your host microphone. Use hda-duplex only if you need a guest mic.")
    print("# PulseAudio-protocol alternative: -audiodev pa,id=snd0,server=unix:/run/user/$(id -u)/pulse/native,out.name={}".format(vm["sink"]))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m arch_games.remote", description="Per-VM browser audio bridge (host side)")
    parser.add_argument("--config", default=str(cfgmod.default_dir() / "bridge.json"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="Create config and a private token")
    i.add_argument("--dir", default=str(cfgmod.default_dir()))
    i.add_argument("--vm", action="append", required=True, metavar="NAME[=SINK]")
    i.add_argument("--force", action="store_true")
    s = sub.add_parser("serve")
    s.add_argument("--listen")
    s.add_argument("--port", type=int)
    s.add_argument("--allow-nonloopback", action="store_true")
    sub.add_parser("doctor")
    k = sub.add_parser("sink")
    k.add_argument("action", choices=["create", "remove", "persist", "listen-locally"])
    k.add_argument("vm")
    q = sub.add_parser("qemu")
    q.add_argument("vm")
    args = parser.parse_args(argv)
    try:
        {"init": cmd_init, "serve": cmd_serve, "doctor": cmd_doctor, "sink": cmd_sink, "qemu": cmd_qemu}[args.cmd](args)
    except cfgmod.ConfigError as exc:
        raise SystemExit("Error: " + str(exc))


if __name__ == "__main__":
    main()
