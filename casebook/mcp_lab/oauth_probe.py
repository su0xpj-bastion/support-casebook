"""Drive the reference SDK's OAuth client (mcp 2.3.0 OAuthClientProvider) far enough to see how it
gets a client_id: dynamic registration (DCR) or a Client ID Metadata Document URL (CIMD). The flow
stops at the browser redirect — no user, no real IdP."""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken


class _Mem:
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client = client_info


class _Stop(Exception):
    pass


async def probe(mcp_url: str, *, client_metadata_url: str | None) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def redirect(url: str) -> None:
        q = parse_qs(urlparse(url).query)
        seen["authorize_client_id"] = q.get("client_id", [None])[0]
        seen["resource"] = q.get("resource", [None])[0]
        raise _Stop

    async def callback() -> Any:
        raise _Stop

    provider = OAuthClientProvider(
        server_url=mcp_url,
        client_metadata=OAuthClientMetadata(redirect_uris=["http://localhost:3030/callback"], client_name="casebook",
                                            grant_types=["authorization_code", "refresh_token"],
                                            response_types=["code"], token_endpoint_auth_method="none"),
        storage=_Mem(), redirect_handler=redirect, callback_handler=callback,
        client_metadata_url=client_metadata_url,
    )
    try:
        async with httpx2.AsyncClient(auth=provider, timeout=10) as c:
            await c.post(mcp_url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                         headers={"accept": "application/json, text/event-stream"})
        seen["outcome"] = "no auth needed"
    except _Stop:
        seen["outcome"] = "reached authorization redirect"
    except BaseException as e:  # noqa: BLE001
        while isinstance(e, BaseExceptionGroup) and e.exceptions:
            e = e.exceptions[0]
        seen["outcome"] = "failed"
        seen["error"] = f"{type(e).__name__}: {str(e)[:240]}"
    return seen


def run(mcp_url: str, client_metadata_url: str | None) -> dict[str, Any]:
    return asyncio.run(probe(mcp_url, client_metadata_url=client_metadata_url))
