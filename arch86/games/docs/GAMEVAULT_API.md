# GameVault contract

Inspected public OpenAPI at https://demo.gamevau.lt/api/docs-json on 2026-10-25,
version 17.0.1. Public guide: https://gamevau.lt/docs/advanced-usage/rest-api/ and
https://gamevau.lt/docs/advanced-usage/authentication/ . The prose guide currently
says POST for basic login, but the published schema defines **GET** with HTTP Basic
Authorization. Follow the schema; do not probe multiple methods with credentials.

Implemented: GET /api/auth/basic/login; POST /api/auth/refresh using refresh Bearer
header; GET /api/users/me; GET /api/games with page, limit, sortBy; GET
/api/games/{game_id}; GET /api/media/{id}; GET /api/game/{id}/versions/{version_id}.
The deprecated GET /api/games/{id}/download remains a fallback for older servers.
List envelope is data + meta.currentPage/totalPages + links. Effective metadata
contains title/description/genres/cover.id. Modern versions[] have IDs, sizes and
types. Architectures, requirements, checksum and archive listings are NOT in this
schema. Never infer these from a title. Server launch/installer parameters are
untrusted metadata and are never adopted as executable configuration.

Tokens rotate on refresh and are stored in a private 0600 file under a 0700 XDG
config directory. Passwords are only used in memory. API-key login is also
supported. No server modifications, credential-bearing redirects or log payloads.
HTTP is opt-in for a trusted private Tailscale transport. HTTPS uses default trust.
Account/server-scoped caches are replaced only after a full successful sync.
OAuth interactive browser flows are outside this client's authentication support;
use an existing API key if enabled by the server administrator.

A live server may use a different contract: check its /api/docs-json before use.
This development did not connect to a private library or modify any server.
