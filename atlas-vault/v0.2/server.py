#!/usr/bin/env python3
"""Atlas Vault v0.2 (Auth0 OAuth Resource Server).
Do not deploy publicly until issuer, allowed subject, audience, and MCP OAuth
client-registration flow are tested.
"""
import os
import re
import json
import secrets
import subprocess
import time

import jwt
from jwt import PyJWKClient
from pydantic import AnyHttpUrl
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp import FastMCP

HOST = "127.0.0.1"
PORT = 4192
PROJECT = os.environ.get("ATLAS_PROJECT_ID", "")
BWS_BIN = os.environ.get("BWS_BIN", "/usr/local/bin/bws")
BWS_TOKEN = os.environ.get("BWS_ACCESS_TOKEN", "")
ISSUER = os.environ.get("ATLAS_AUTH0_ISSUER", "").rstrip("/") + "/"
RESOURCE = os.environ.get("ATLAS_OAUTH_RESOURCE", "")
AUDIENCE = os.environ.get("ATLAS_AUTH0_AUDIENCE", "")
ALLOWED_SUB = os.environ.get("ATLAS_AUTH0_ALLOWED_SUB", "")
ALLOWED_CLIENT_ID = os.environ.get("ATLAS_AUTH0_ALLOWED_CLIENT_ID", "")
if not all((PROJECT, BWS_TOKEN, RESOURCE, AUDIENCE, ALLOWED_SUB, ALLOWED_CLIENT_ID)):
    raise RuntimeError("Missing required vault or OAuth environment settings")
if not ISSUER.startswith("https://") or not RESOURCE.startswith("https://") or not AUDIENCE.startswith("https://"):
    raise RuntimeError("Auth0 issuer, MCP resource, and audience must be HTTPS")
if not re.fullmatch(r"[a-fA-F0-9-]{36}", PROJECT):
    raise RuntimeError("Invalid project ID")

_jwks = PyJWKClient(ISSUER + ".well-known/jwks.json", cache_jwk_set=True, lifespan=300)

class Auth0Verifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                return None
            key = _jwks.get_signing_key_from_jwt(token).key
            payload = jwt.decode(token, key, algorithms=["RS256"], audience=AUDIENCE,
                                 issuer=ISSUER, options={"require": ["exp", "iat", "iss", "sub", "aud"]},
                                 leeway=30)
            if payload.get("sub") != ALLOWED_SUB:
                return None
            # Auth0 OAuth clients are allowed only by explicitly configured client ID.
            client = payload.get("azp") or payload.get("client_id")
            if client != ALLOWED_CLIENT_ID:
                return None
            scopes = payload.get("scope", "").split()
            if "vault:read" not in scopes:
                return None
            return AccessToken(token=token, client_id=client, scopes=scopes,
                               expires_at=payload["exp"], subject=payload["sub"],
                               resource=AUDIENCE)
        except (jwt.PyJWTError, ValueError, KeyError, TypeError, OSError):
            return None

mcp = FastMCP(
    "Atlas Vault", host=HOST, port=PORT, stateless_http=True, json_response=True,
    token_verifier=Auth0Verifier(),
    auth=AuthSettings(issuer_url=AnyHttpUrl(ISSUER),
                      resource_server_url=AnyHttpUrl(RESOURCE),
                      required_scopes=["vault:read"],
                      validate_token_resource=False),
)

def require_scope(required: str):
    token = get_access_token()
    if token is None or required not in token.scopes:
        raise PermissionError("Access denied: missing required scope")

def call(*args):
    if not BWS_TOKEN:
        raise RuntimeError("Secrets Manager not configured")
    proc = subprocess.run(
        [BWS_BIN, *args, "--output", "json"],
        env={"BWS_ACCESS_TOKEN": BWS_TOKEN, "HOME": "/var/lib/atlas-vault",
             "PATH": "/usr/local/bin:/usr/bin:/bin"},
        text=True, capture_output=True, timeout=40
    )
    if proc.returncode:
        raise RuntimeError("Bitwarden operation failed; inspect locally without exposing secrets")
    return json.loads(proc.stdout)

def meta(value):
    return {"id": value.get("id"), "name": value.get("key"),
            "note": value.get("note"), "project_id": value.get("projectId")}

@mcp.tool()
def vault_status() -> dict:
    """Check Bitwarden connectivity without returning secret values."""
    require_scope("vault:read")
    items = call("secret", "list")
    return {"connected": True, "project_id": PROJECT,
            "account_count": sum(x.get("projectId") == PROJECT for x in items)}

@mcp.tool()
def list_atlas_accounts() -> list[dict]:
    """List only metadata for this project. Never return secret values."""
    require_scope("vault:read")
    return [meta(x) for x in call("secret", "list") if x.get("projectId") == PROJECT]

@mcp.tool()
def generate_and_store_account(service: str, username: str, url: str = "") -> dict:
    """Generate a server-side random password, save to Bitwarden, return only metadata."""
    require_scope("vault:write")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,62}", service):
        raise ValueError("Invalid service identifier")
    if not username or any(c in username+url for c in "\r\n\x00") or len(username)>255 or len(url)>255:
        raise ValueError("Invalid username or URL")
    key = "atlas.account." + service.lower()
    if any(x.get("projectId")==PROJECT and x.get("key")==key for x in call("secret", "list")):
        return {"error": "Account exists; refusing overwrite"}
    # BWS CLI v2.1 takes the secret as argv: privileged local process inspection
    # may observe it briefly. On a shared/untrusted server this is unacceptable.
    password = secrets.token_urlsafe(32)
    note = json.dumps({"username":username,"url":url}, separators=(",",":"))
    entry = call("secret", "create", key, password, PROJECT, "--note", note)
    return {"status":"stored", "account":meta(entry),
            "note":"Password retained in Bitwarden; not sent back to ChatGPT."}

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
