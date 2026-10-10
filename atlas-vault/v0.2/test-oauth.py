#!/usr/bin/env python3
"""Local-only Atlas Vault v0.2 Auth0 Authorization Code + PKCE smoke test.

Run on homeserver with SSH local port forwarding from browser desktop:
  ssh -N -L 8765:127.0.0.1:8765 root@10.0.0.68
Never share the generated login URL or callback query string.
"""
import asyncio
import base64
import hashlib
import json
import secrets
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ISSUER = "https://atlas-vault.us.auth0.com"
AUDIENCE = "https://vault.pcenthusiast.blog/mcp"
CALLBACK = "http://127.0.0.1:8765/callback"

client_id = input("Auth0 Atlas Vault ChatGPT Client ID: ").strip()
if not client_id:
    raise SystemExit("Missing client ID")

verifier = secrets.token_urlsafe(64)
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
state = secrets.token_urlsafe(32)
result = {}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path != "/callback" or query.get("state", [None])[0] != state:
            status, msg = 400, b"Invalid OAuth callback"
        elif "error" in query:
            result["error"] = query["error"][0]
            status, msg = 400, b"Login failed; check terminal"
        elif "code" in query:
            result["code"] = query["code"][0]
            status, msg = 200, b"Login received; return to terminal"
        else:
            status, msg = 400, b"Missing authorization code"
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(msg)

    def log_message(self, *_):
        pass

params = urllib.parse.urlencode({
    "response_type": "code",
    "client_id": client_id,
    "redirect_uri": CALLBACK,
    "audience": AUDIENCE,
    "scope": "openid profile vault:read vault:write",
    "state": state,
    "code_challenge": challenge,
    "code_challenge_method": "S256",
})

server = HTTPServer(("127.0.0.1", 8765), Handler)
server.timeout = 180
try:
    print("\nOpen the following URL in your desktop browser (DO NOT SHARE IT):\n")
    print(ISSUER + "/authorize?" + params)
    print("\nWaiting up to 3 minutes for callback...")
    server.handle_request()
finally:
    server.server_close()

if not result.get("code"):
    raise SystemExit("OAuth callback did not provide a code. Error: " + result.get("error", "timeout or state mismatch"))

form = urllib.parse.urlencode({
    "grant_type": "authorization_code",
    "client_id": client_id,
    "code": result["code"],
    "code_verifier": verifier,
    "redirect_uri": CALLBACK,
}).encode()
req = urllib.request.Request(
    ISSUER + "/oauth/token", form,
    {"Content-Type": "application/x-www-form-urlencoded"}
)
try:
    with urllib.request.urlopen(req, timeout=20) as response:
        access_token = json.load(response)["access_token"]
except urllib.error.HTTPError as exc:
    # No tokens or codes in output
    raise SystemExit(f"Token exchange rejected: HTTP {exc.code}") from None

try:
    middle = access_token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4)))
except (ValueError, IndexError):
    raise SystemExit("Received a non-JWT access token")
print("\nToken claims (diagnostics only; verification occurs on MCP server):")
for key in ("iss", "aud", "sub", "azp", "client_id", "scope", "permissions"):
    print(key, ":", claims.get(key))

async def test():
    async with streamablehttp_client(
        "http://127.0.0.1:4193/mcp",
        headers={"Authorization": "Bearer " + access_token},
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool("vault_status", {})
            print("Vault tool error:", r.isError)
            print("Vault response:", r.content)

try:
    asyncio.run(test())
except Exception as exc:
    # Exceptions may contain sensitive endpoint data; show type only
    print("MCP test failed:", type(exc).__name__)
print("No raw access token saved or printed.")
