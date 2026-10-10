# Atlas Vault v0.2 OAuth candidate — staging only

Uses Auth0-signed RS256 JWT access tokens with MCP SDK OAuth resource metadata.
**Do not expose over the internet until Auth0 client registration, scopes, login,
authorized-subject checks, and revocation/expiry behavior are independently tested.**

## Required environment values
Keep existing BWS_ACCESS_TOKEN, ATLAS_PROJECT_ID, BWS_BIN and add:
- ATLAS_AUTH0_ISSUER=https://atlas-vault.us.auth0.com/
- ATLAS_OAUTH_RESOURCE=https://vault.pcenthusiast.blog/mcp
- ATLAS_AUTH0_AUDIENCE=https://vault.pcenthusiast.blog/mcp
- ATLAS_AUTH0_ALLOWED_SUB=<your exact Auth0 user sub>
- ATLAS_AUTH0_ALLOWED_CLIENT_ID=<exact OAuth client id for ChatGPT>

**Resource URL and Auth0 API identifier must be identical.** If you previously
made your Auth0 API identifier `https://vault.pcenthusiast.blog`, you may need a
new API with the `/mcp` identifier. Never change the existing API blindly.
Auth0's resource parameter compatibility profile should be enabled.

## Preparation
1. Preserve your fully working v0.1 with systemd and source backups.
2. Copy server.py into a separate staging directory, e.g. /opt/atlas-vault-v0.2.
3. Use a separate venv and install `mcp>=1.27.2,<3`, `PyJWT[crypto]>=2.9,<3`.
4. Fill the environment fields locally. Do not guess `ATLAS_AUTH0_ALLOWED_SUB`.
   Extract it from a locally verified Auth0 login; don't send raw tokens to chat.
5. Stage service on a DIFFERENT localhost port (change PORT) rather than replacing
   v0.1 first.
6. Verify unauthenticated HTTP requests return 401 and resource metadata identifies
   your actual Auth0 issuer and externally reachable MCP URL. Then perform a complete
   OAuth PKCE login and test read/write scope separation.
7. Verify ChatGPT client registration support (predefined client/DCR/CIMD). The
   existing Auth0 SPA application by itself DOES NOT ensure integration works.
8. Retain rollback; don't open the tunnel before all verification passes.

## Security
Read scope required to access all tools, with separate write scope for creation.
Requests are restricted to one Auth0 subject and client ID; invalid tokens rejected.
Secrets Manager remains server-side. Secrets passed to BWS CLI via process arguments
may be visible to privileged local observers. Treat ChatGPT tool outputs and mail
content as untrusted, and require user confirmation for credential-changing calls.
