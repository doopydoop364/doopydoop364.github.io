# GameVault connection guide

Use a configurable base URL, including any reverse-proxy path prefix, without
`/api`, username, password, query or fragment. Example:
`arch-games gamevault connect --url https://games.your-tailnet.example`.
Credentials are requested interactively with hidden password input. Login is the
HTTP Basic endpoint specified by OpenAPI 17.0.1; access and refresh tokens are
stored privately and rotated on expiry. No password is retained. Use
`gamevault connect --api-key --url URL` for an existing server-enabled API key;
the account name is a local cache identity. SSO-only installations can use an
administrator-enabled existing API key; this client does not conduct OAuth browser
flows or change server authentication settings.

Plain HTTP requires explicit `--allow-http`. Use that only when you intentionally
trust the transport, such as your private encrypted Tailscale connection. TLS uses
system certificate validation. Redirects are refused to avoid forwarding secrets.
No machine address is hardcoded and Tailscale is not required by the client.

`status` authenticates against /api/users/me. `list` retrieves paginated games;
`search QUERY` filters the retrieved library locally; `details ID` retrieves full
metadata. Modern version-specific catalog IDs are `gamevault:GAME_ID@VERSION_ID`.
The API's game details operation uses the bare numeric GAME_ID.

List metadata is cached by server URL and account. Complete syncs replace the
cache atomically. A failed partial sync preserves the previous complete snapshot
and shows a warning and synchronization time. Use `--offline` for deliberate cache
use, including when tokens have expired. Auth errors are visible, not silently
masked. Different server/account settings never share metadata caches.

`gamevault disconnect` removes local credentials, retaining metadata for offline
use. It does not revoke a server session; existing refresh tokens expire under
server policy. Files remain under a 0700 application config directory and tokens
have 0600 permissions. Back up such directories privately. Never share them in bug
reports. The gallery fetches only requested cover IDs through the authenticated
client, stores local image copies, and never puts tokens in image URLs.

For live acceptance, inspect your server's /api/docs-json, compare the contract in
GAMEVAULT_API.md, then connect/list/details/download a small authorized game. The
client never calls indexing, upload, deletion or metadata-update endpoints.
