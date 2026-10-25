# Security considerations

- No secrets in source, URLs, logs or metadata databases. Tokens are in a private
  XDG file; passwords remain in memory. HTTPS verification stays enabled. HTTP
  requires explicit opt-in. Credential-bearing redirects are refused.
- Metadata is untrusted display data. Terminal controls are stripped; HTML is
  escaped, with CSP/no-sniff/frame restrictions. Remote installer/launch commands
  are not executed or imported as local configuration.
- Archive parsing runs in a resource-limited worker, including RAR/7z via the
  system libarchive. Update libarchive using normal OS maintenance. See
  ARCHIVE_SECURITY.md for bounds and rejected structures. Resource-limit failures
  are inspection failures, not compatibility findings.
- Downloads validate range offsets, validators, sizes and optional supplied
  SHA-256. A locally calculated digest has no external authenticity guarantee.
  Size-only and unverified transfers are labeled honestly. Partial files remain
  separate and can be explicitly cleaned up. Queue operations use a lock.
- Installations stage files, record manifests and journals, and preserve rollback
  copies. They never launch a file. App installation checks the staged command
  and records hashes; doctor verifies installed source integrity. Bootstrap TLS
  authenticates GitHub transport; a pinned revision is recommended for reproducibility.
- Launches use arrays and resolved paths. External files require --allow-external.
  Advanced custom commands and environment settings are explicit user input.
  Wine is not a security sandbox; launched games run with your user privileges.
  The default environment excludes unrelated inherited credential variables.
- Browser endpoints are read-only and localhost-bound. Host/origin checks defend
  against unintended cross-site access. No server upload/delete/reindex APIs are used.
- Prefixes, archives and backups may contain personal save files. Removal retains
  these unless the corresponding purge is explicit. Stop games before backing up.
  Backups on the same disk are recovery copies, not protection against disk loss.

Threat boundary: another process with the same Unix user privileges can read or
change user-owned state. This application is not a sandbox against that user.
Keep bug reports free of credentials, private metadata and raw launch logs.

---

# Arch Games 3.0 security review (developer review of the 3.0 additions; not an external audit)

Method: read every new module against the spec's security list, grep for shell/eval/exec/pickle use (none; all subprocesses take argument arrays), mutation-style
checks that tests fail when a control is removed (token check), and a real-browser pass of the web GUI and audio page (found a CSP/inline-style bug and a DOM bug, both fixed).

| Area | Control | Evidence |
|---|---|---|
| Installers | Never run without per-action approval: `--yes` approves extraction only; `--approve-installer` (or interactive yes / ticked web box bound to the reviewed archive hash) runs it; never as root (no sudo path in the install workflow) | tests/test_install_workflow.py, test_web.py |
| Archives | 2.0 traversal/symlink/limits worker unchanged; workflow uses it for inspection and extraction; installers are never executed from the worker | existing archive tests + workflow tests |
| Discovery | Reads headers only; never executes candidates; no symlink following; bounded entries/depth/registry size; registry paths mapped with `..`/drive checks | test_discovery_launch.py |
| Launchers | Registered executable must stay inside the install dir or the game's Wine prefix (or explicit `--allow-external`); uninstallers/installers refused as launchers; env keys validated (no WINEPREFIX/WINEARCH) | same |
| Name resolution | Fuzzy matches never auto-pick among several; ambiguity is an error; aliases cannot shadow IDs or silently repoint | test_identity.py |
| Database | Versioned migrations in one transaction with an online backup first; newer schema refused; failure rolls back | test_migrations.py |
| Web GUI | Loopback default; token login; HttpOnly+SameSite=Strict session; CSRF header; Origin and Host checks; login throttling; allowlisted operations only; strict CSP; no innerHTML; JSON-only bodies, 64 KiB cap | test_web.py + browser |
| Remote audio | Off by default; loopback default; token mandatory (constant-time compare, first WS message, never in URLs); Origin check; throttling; listener cap; only `<sink>.monitor` sources, `@DEFAULT`/non-monitor refused, no shared sources; config must not be group/world-writable (it can hold a capture command); token file 0600 | test_remote_audio.py |
| Saves | Never follows symlinks; manual folders validated (no `/`, home, system dirs); backups hash-verified and atomic; restore needs preview + `--yes`, refuses damaged backups, symlink targets and any folder not registered for the game, and makes a safety backup first; nothing is deleted | test_saves.py |
| Secrets | Bridge token and web token in separate 0600 files; GameVault credentials untouched by 3.0; diagnostics redaction for paths/IPs/tokens/URLs credentials | test_wine_tools.py, test_remote_audio.py |
| Sync/export | File bundles contain no credentials, paths or game files; imports validate every field and never overwrite this machine's own compatibility records | test_library_features.py, test_compat_intelligence.py |

Findings fixed during the review: (1) a GameVault reconnect or disconnect would have wiped the remote-audio token because it shared `credentials.json` — now a separate file;
(2) a tampered save-backup manifest could have redirected a restore to an arbitrary folder — restore now only writes to folders registered for that game; (3) the bridge config
(which can contain a capture command) was not permission-checked; (4) web inline styles were blocked by the CSP (functional bug found in the real browser); (5) `doctor` ran slow graphics probes by default (performance).

Known residual risks: plain HTTP on a private network is unencrypted by the app (use Tailscale/WireGuard or `tailscale serve`); the web session cookie lacks `Secure` on HTTP; any local process
running as you can read your token files; a malicious Wine installer, once you approved it, runs with your user's rights inside the prefix (Wine is not a security sandbox); Wine links
`Documents`/`Desktop` into your real home on some setups, which is why save discovery and backups never follow those links.
