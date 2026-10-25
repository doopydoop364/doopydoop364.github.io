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
