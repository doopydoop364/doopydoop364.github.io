"""Guest-side view of the optional host audio bridge: configuration, reachability and instructions.

The bridge itself runs on the VM host (see arch_games/remote). Remote audio is disabled until configured."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import os

from ..config import AppError, server_url

KEY = "remote_audio"


def token_file(config):
    """Own file, so GameVault connect/disconnect (which rewrite credentials.json) never touch it."""
    return config.config_dir / "remote-audio.token"


def read_token(config):
    path = token_file(config)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        return None
    return path.read_text().strip() or None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # never send the bearer token to a redirect target


def settings(config):
    return config.values.get(KEY) or {}


def configure(config, url, vm, token, allow_http=False):
    url = server_url(url, allow_http=allow_http)
    if not vm or len(vm) > 32:
        raise AppError("VM name required")
    if not token or len(token) < 24:
        raise AppError("Bridge token looks too short")
    path = token_file(config)
    if path.is_symlink():
        raise AppError("Token file must not be a symlink")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(token + "\n")
    path.chmod(0o600)
    config.values[KEY] = {"enabled": True, "url": url, "vm": vm}
    config.save()
    return page_url(config)


def disable(config):
    config.values[KEY] = dict(settings(config), enabled=False)
    config.save()
    token_file(config).unlink(missing_ok=True)


def page_url(config):
    """Browser URL without the token: the user pastes the token when asked, or appends #token=… themselves."""
    s = settings(config)
    if not s.get("enabled"):
        return None
    return "{}/wrapper/{}/".format(s["url"], s["vm"])


def status(config, timeout=3):
    s = settings(config)
    if not s.get("enabled"):
        return {"enabled": False, "note": "Remote audio is off. Enable with 'audio remote configure'. See docs/REMOTE_AUDIO_3.md."}
    token = read_token(config)
    if not token:
        return {"enabled": True, "reachable": False, "error": "token missing; reconfigure"}
    request = Request(s["url"] + "/api/status", headers={"Authorization": "Bearer " + token})
    try:
        with build_opener(NoRedirect).open(request, timeout=timeout) as response:
            body = json.loads(response.read(65536))
    except HTTPError as exc:
        return {"enabled": True, "reachable": True, "authenticated": exc.code != 401, "error": "HTTP {}".format(exc.code)}
    except (URLError, OSError, ValueError) as exc:
        return {"enabled": True, "reachable": False, "error": "bridge unreachable ({})".format(type(exc).__name__)}
    vm = body.get(s["vm"])
    return {"enabled": True, "reachable": True, "authenticated": True, "vm": s["vm"], "stream": vm,
            "vm_known": vm is not None, "page": page_url(config)}


def instructions(vm="arch64"):
    sink = vm.replace("-", "_") + "_vm"
    return "\n".join([
        "Remote browser audio, step by step (nothing below runs automatically):",
        "HOST (CachyOS):",
        "  1. python arch-games-audio-bridge.pyz init --vm {}={}".format(vm, sink),
        "  2. python arch-games-audio-bridge.pyz sink create {}      # dedicated null sink for this VM only".format(vm),
        "  3. python arch-games-audio-bridge.pyz qemu {}             # copy the printed -audiodev/-device flags into the VM's QEMU command".format(vm),
        "  4. restart the VM yourself, then: python arch-games-audio-bridge.pyz serve",
        "  5. for remote use over Tailscale: serve --listen <tailscale-ip> --allow-nonloopback (token stays mandatory)",
        "GUEST (this machine):",
        "  arch-games audio remote configure --url http://<host-tailscale-name>:8787 --vm {} --allow-http".format(vm),
        "  arch-games audio remote status",
        "BROWSER: open the printed page, click 'Enable audio'. Only the dedicated sink is streamed; host desktop sound and microphones are not.",
    ])
