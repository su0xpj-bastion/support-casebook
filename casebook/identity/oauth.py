"""OAuth 2.0 authorization server + OIDC relying party, enough for redirect-URI, refresh-token and
JWKS-rotation scenarios. Tokens are real RS256 JWTs (PyJWT); behaviour follows RFC 6749 / OIDC Core."""
from __future__ import annotations

import base64
import datetime as dt
import json
import secrets
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization

from casebook.identity.keys import SigningCert


class OAuthError(Exception):
    def __init__(self, error: str, description: str, redirect: bool) -> None:
        super().__init__(f"{error}: {description}")
        self.error, self.description, self.redirect = error, description, redirect


def jwk(cert: SigningCert) -> dict[str, Any]:
    pub = cert.public_key.public_numbers()

    def b64(n: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()

    return {"kty": "RSA", "kid": cert.kid, "use": "sig", "alg": "RS256", "n": b64(pub.n), "e": b64(pub.e)}


@dataclass
class AuthorizationServer:
    issuer: str
    keys: list[SigningCert]                         # keys[0] signs; all are published in JWKS
    clients: dict[str, list[str]] = field(default_factory=dict)   # client_id -> registered redirect URIs
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    refresh_tokens: dict[str, dict[str, Any]] = field(default_factory=dict)

    def jwks(self) -> dict[str, Any]:
        return {"keys": [jwk(k) for k in self.keys]}

    def authorize(self, client_id: str, redirect_uri: str, scope: str, subject: str) -> str:
        """RFC 6749 §4.1.2.1: if the redirect URI is invalid, the AS must NOT redirect — it shows the error."""
        if client_id not in self.clients:
            raise OAuthError("invalid_request", f"unknown client_id {client_id}", redirect=False)
        if redirect_uri not in self.clients[client_id]:
            raise OAuthError("invalid_request", f"redirect_uri {redirect_uri} is not registered for {client_id} "
                                                f"(registered: {self.clients[client_id]})", redirect=False)
        code = secrets.token_urlsafe(12)
        self.codes[code] = {"client_id": client_id, "redirect_uri": redirect_uri, "scope": scope, "sub": subject}
        return f"{redirect_uri}?code={code}"

    def token(self, grant_type: str, now: dt.datetime, *, code: str | None = None, redirect_uri: str | None = None,
              client_id: str | None = None, refresh_token: str | None = None, audience: str = "casebook-app") -> dict[str, Any]:
        if grant_type == "authorization_code":
            c = self.codes.pop(code or "", None)
            if c is None or c["redirect_uri"] != redirect_uri or c["client_id"] != client_id:
                raise OAuthError("invalid_grant", "code invalid, used, or redirect_uri differs from the authorize request", redirect=False)
            grant = {"sub": c["sub"], "scope": c["scope"], "client_id": client_id}
        elif grant_type == "refresh_token":
            g = self.refresh_tokens.get(refresh_token or "")
            if g is None or g.get("revoked"):
                raise OAuthError("invalid_grant", "refresh token is invalid, expired or revoked", redirect=False)
            grant = g
        else:
            raise OAuthError("unsupported_grant_type", grant_type, redirect=False)
        rt = secrets.token_urlsafe(16)
        self.refresh_tokens[rt] = dict(grant)
        return {"access_token": secrets.token_urlsafe(16), "token_type": "Bearer", "expires_in": 3600, "refresh_token": rt,
                "id_token": self.id_token(grant["sub"], audience, now), "scope": grant["scope"]}

    def revoke(self, refresh_token: str) -> None:
        if refresh_token in self.refresh_tokens:
            self.refresh_tokens[refresh_token]["revoked"] = True

    def id_token(self, sub: str, aud: str, now: dt.datetime, issuer: str | None = None) -> str:
        k = self.keys[0]
        return jwt.encode({"iss": issuer or self.issuer, "sub": sub, "aud": aud, "iat": int(now.timestamp()),
                           "exp": int((now + dt.timedelta(minutes=10)).timestamp()), "email": sub},
                          serialization.load_pem_private_key(k.key_pem, None), algorithm="RS256", headers={"kid": k.kid})


@dataclass
class OidcRelyingParty:
    """Validates ID tokens against a cached JWKS. refetch_on_unknown_kid models a correct client;
    False models a client that only refreshes on its cache TTL."""
    issuer: str
    audience: str
    fetch_jwks: Any                          # callable -> JWKS dict
    cache_ttl_s: int = 600
    refetch_on_unknown_kid: bool = True
    _cache: dict[str, Any] | None = None
    _fetched_at: dt.datetime | None = None
    fetches: int = 0

    def _keys(self, now: dt.datetime, force: bool = False) -> dict[str, Any]:
        stale = self._fetched_at is None or (now - self._fetched_at).total_seconds() >= self.cache_ttl_s
        if force or stale or self._cache is None:
            self._cache, self._fetched_at = self.fetch_jwks(), now
            self.fetches += 1
        return {k["kid"]: k for k in self._cache["keys"]}

    def verify(self, token: str, now: dt.datetime) -> dict[str, Any]:
        kid = jwt.get_unverified_header(token).get("kid")
        keys = self._keys(now)
        if kid not in keys and self.refetch_on_unknown_kid:
            keys = self._keys(now, force=True)
        if kid not in keys:
            raise OAuthError("invalid_token", f"no key with kid {kid} in cached JWKS (cached {self._fetched_at:%H:%M:%S})", redirect=False)
        key = jwt.PyJWK(keys[kid]).key
        try:
            return jwt.decode(token, key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                              options={"verify_exp": False})
        except jwt.InvalidAudienceError as e:
            raise OAuthError("invalid_token", f"aud mismatch: {e}", redirect=False) from e
        except jwt.InvalidIssuerError as e:
            raise OAuthError("invalid_token", f"iss mismatch: {e}", redirect=False) from e


_ = json
