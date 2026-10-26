# Optional web GUI

    arch-games web                      # http://127.0.0.1:8765/ , prints a one-time-style login URL with #token=…
    arch-games web --host <tailscale-ip> --allow-remote --allowed-host <name-or-ip>:8765

Stdlib only (no web framework to install). Not needed for any feature: everything here also exists in the CLI/TUI, and the web server
calls the same services (`Library`, `describe`, `Workflow`, `DownloadManager`, `Launcher`, `winetools`, audio, doctor).

## Pages
Library (cover art proxied through the server, search by name/alias/tag/source, filters for source/compatibility/installed/favorites, sort,
recently played, favorites, show-unverified), game detail (state, executable, prefix, last launch, storage, evidence and uncertainties,
Wine settings form, notes/tags, Launch / Queue download / Prepare install), Downloads (queue, progress, speed, retry, cancel, run), Installs
(job list with the same plan the CLI shows; installer execution needs a ticked approval box and a plan hash that matches what you reviewed),
Audio (local verdict + layers, remote bridge status/link/disable), Wine (doctor), Diagnostics (doctor + storage).

## Native games and launch settings
Pacman games show their package status (available / installed-no-launcher / ready / launcher-invalid / package-removed), the native executable and a **Launch settings** form: a profile selector, arguments (one per line),
environment (`NAME=VALUE` per line), Save, make default, create/delete profile and "Launch with this profile". Stale or missing launchers show the reason and the terminal command to fix them
(`arch-games launcher rediscover ID`). **Not editable in the browser, by design:** the executable, the runner, the working directory, and anything that needs sudo (package installation). Sensitive environment
variables (PATH, HOME, LD_*, ...) and `WINEPREFIX` are refused by the server, arguments are passed to the program as an argument array (never a shell), and the new endpoints
(`GET /api/launcher/ID`, `POST /api/launcher/ID/settings`, `POST /api/launcher/ID/profile`, `launch` with `profile`) sit behind the same token session, CSRF header, Host/Origin checks and input validation as the rest.

## Security model
* Loopback by default. Non-loopback needs `--allow-remote`; token login is always required; the token is a 0600 file `web-token` in the config dir.
* Login: token in the URL **fragment** → `POST /api/login` → `HttpOnly; SameSite=Strict` session cookie (8 h) + per-session CSRF token that
  every state-changing request must send in `X-CSRF-Token`. Origin must match Host on POST. `Host` must be an allowed value (DNS-rebinding defence).
  5 failed logins/min per address are throttled.
* Allowlisted operations only (see `ui/web/server.py: dispatch`). There is no shell/exec endpoint; the browser never supplies paths, argv or
  environment. `launch` takes only a game ID and runs the user-registered launcher; `install` takes only GameVault game IDs (local archives and
  `--directory` installs are CLI-only). Wine settings accept validated fields only (no arbitrary env vars).
* Strict CSP (no inline script or style attributes), `X-Frame-Options: DENY`, `no-store`, `nosniff`; the UI builds DOM nodes with text only (no `innerHTML`),
  so remote GameVault titles cannot inject markup. Plain HTTP on a private tunnel only; for HTTPS put `tailscale serve` in front.
* Launch needs a graphical session visible to the server process; otherwise the API says so instead of failing silently.

## Verified
Automated: tests/test_web.py (15) — real HTTP server, sessions, CSRF/Origin/Host/throttle, method and body limits, no-exec-surface, shared state with the CLI, validation,
launch with a stub wine, install-approval checks. Manual real browser (built-in browser pane on the dev host): login via fragment (token removed from the address bar),
library, game detail, downloads progress bar, audio/wine/diagnostics views rendered; this found and fixed a DOM-helper bug and a CSP/inline-style bug that unit tests had not.
Not verified: Safari/Firefox, phones, remote access over Tailscale, real GameVault cover art, real installs through the browser.
