"""OAuth pieces for AG-19 / AG-20.

Docker is not available on this machine, so Keycloak (the plan's authorization server) can't run
here. A small local authorization-server stand-in serves RFC 8414 metadata in three shapes, and the
MCP resource server uses the reference SDK's own bearer middleware (mcp 2.3.0) with a token
registry. The behaviour under test (401 vs 403 insufficient_scope, audience checks, DCR vs Client ID
Metadata Documents) is the SDKs' — the stand-in only supplies metadata and tokens.
"""
from __future__ import annotations

import contextlib
import json
import threading
from typing import Any, Iterator

import uvicorn
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from casebook.mcp_lab.servers import _wait, free_port

SCOPE = "claims:read"


def tokens(resource: str) -> dict[str, AccessToken]:
    return {
        "tok-good": AccessToken(token="tok-good", client_id="casebook", scopes=[SCOPE], resource=resource),
        "tok-no-scope": AccessToken(token="tok-no-scope", client_id="casebook", scopes=["profile"], resource=resource),
        "tok-wrong-audience": AccessToken(token="tok-wrong-audience", client_id="casebook", scopes=[SCOPE],
                                          resource="https://other-server.example.com/mcp"),
    }


class Verifier:
    def __init__(self, resource: str) -> None:
        self._t = tokens(resource)

    async def verify_token(self, token: str) -> AccessToken | None:
        return self._t.get(token)


def verifier(resource: str) -> Verifier:
    return Verifier(resource)


def auth_settings(scope: str, resource: str, issuer: str) -> AuthSettings:
    return AuthSettings(issuer_url=issuer, resource_server_url=resource, required_scopes=[scope],
                        validate_token_resource=True)


def build_as(issuer: str, shape: str, log: list[dict[str, Any]]) -> Starlette:
    """shape: 'dcr' (registration_endpoint only) | 'cimd' (client ID metadata documents only)."""
    async def metadata(request: Request) -> JSONResponse:
        log.append({"path": request.url.path})
        md: dict[str, Any] = {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": [SCOPE],
        }
        if shape == "dcr":
            md["registration_endpoint"] = f"{issuer}/register"
        if shape == "cimd":
            md["client_id_metadata_document_supported"] = True
        return JSONResponse(md)

    async def register(request: Request) -> JSONResponse:
        body = await request.json()
        log.append({"path": "/register", "client_name": body.get("client_name")})
        return JSONResponse({**body, "client_id": "dcr-client-0001", "client_id_issued_at": 1795000000}, status_code=201)

    return Starlette(routes=[Route("/.well-known/oauth-authorization-server", metadata),
                             Route("/.well-known/openid-configuration", metadata),
                             Route("/register", register, methods=["POST"])])


@contextlib.contextmanager
def auth_server(shape: str) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    port = free_port()
    issuer = f"http://127.0.0.1:{port}"
    log: list[dict[str, Any]] = []
    srv = uvicorn.Server(uvicorn.Config(build_as(issuer, shape, log), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    _wait(port)
    try:
        yield issuer, log
    finally:
        srv.should_exit = True
        t.join(timeout=5)


_ = json
