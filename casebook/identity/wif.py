"""Token exchange modelled on OpenAI Workload Identity Federation's documented rules.

  https://developers.openai.com/api/docs/guides/workload-identity-federation
  - issuer URL compared with trailing slash ignored; audience must equal the configured value
  - key source: OIDC discovery (JWKS cached 600 s; an unknown kid triggers one refresh) OR uploaded JWKS
  - mapping values: a single trailing '*' after a non-empty prefix is the only wildcard
  - a token is issued only if EXACTLY ONE enabled mapping matches; more than one is rejected
  - lifetime <= 1 h and never beyond the subject token; no refresh token
  - documented error codes: invalid_subject_token, invalid_grant
Codes for failures the docs describe without a code are marked synthesized in the packs.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import jwt


class WifError(Exception):
    def __init__(self, error: str, description: str) -> None:
        super().__init__(f"{error}: {description}")
        self.error, self.description = error, description


_VALID = re.compile(r"^[^*]+\*?$")


def validate_mapping_value(value: str) -> None:
    if not _VALID.match(value):
        raise ValueError(f"mapping value {value!r}: only a single trailing '*' after a non-empty prefix is allowed")


def _matches(pattern: str, value: str) -> bool:
    return value.startswith(pattern[:-1]) if pattern.endswith("*") else value == pattern


@dataclass
class Mapping:
    service_account: str
    conditions: dict[str, str]      # claim -> exact value or prefix*
    enabled: bool = True

    def __post_init__(self) -> None:
        for v in self.conditions.values():
            validate_mapping_value(v)


@dataclass
class WifProvider:
    issuer: str
    audience: str
    key_source: str = "discovery"                       # discovery | uploaded
    fetch_jwks: Callable[[], dict[str, Any]] | None = None
    uploaded_jwks: dict[str, Any] | None = None
    mappings: list[Mapping] = field(default_factory=list)
    cache_ttl_s: int = 600
    _cache: dict[str, Any] | None = None
    _fetched_at: dt.datetime | None = None

    def _keys(self, now: dt.datetime, kid: str) -> dict[str, Any]:
        if self.key_source == "uploaded":
            return {k["kid"]: k for k in (self.uploaded_jwks or {"keys": []})["keys"]}
        stale = self._fetched_at is None or (now - self._fetched_at).total_seconds() >= self.cache_ttl_s
        keys = {k["kid"]: k for k in (self._cache or {"keys": []})["keys"]}
        if stale or kid not in keys:   # unknown kid -> one refresh before rejecting
            self._cache, self._fetched_at = self.fetch_jwks(), now  # type: ignore[misc]
            keys = {k["kid"]: k for k in self._cache["keys"]}
        return keys

    def exchange(self, subject_token: str, now: dt.datetime) -> dict[str, Any]:
        header = jwt.get_unverified_header(subject_token)
        keys = self._keys(now, header.get("kid", ""))
        if header.get("kid") not in keys:
            raise WifError("invalid_grant", f"no key with kid {header.get('kid')} in the provider's "
                                            f"{'uploaded JWKS' if self.key_source == 'uploaded' else 'JWKS'}")
        try:
            claims = jwt.decode(subject_token, jwt.PyJWK(keys[header["kid"]]).key, algorithms=["RS256"],
                                audience=self.audience, options={"verify_exp": False, "verify_iss": False})
        except jwt.InvalidAudienceError as e:
            raise WifError("invalid_grant", f"aud does not match the provider audience {self.audience}") from e
        if claims.get("iss", "").rstrip("/") != self.issuer.rstrip("/"):
            raise WifError("invalid_grant", f"iss {claims.get('iss')} does not match {self.issuer}")
        if claims.get("exp", 0) <= now.timestamp():
            raise WifError("invalid_subject_token", "subject token expired")
        hits = [m for m in self.mappings if m.enabled and all(_matches(p, str(claims.get(c, ""))) for c, p in m.conditions.items())]
        if len(hits) != 1:
            raise WifError("invalid_grant", f"{len(hits)} enabled mappings match (exactly one required): "
                                            f"{[m.service_account for m in hits]}")
        exp = min(int((now + dt.timedelta(hours=1)).timestamp()), int(claims["exp"]))
        return {"access_token": "wif-" + hits[0].service_account, "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "token_type": "Bearer", "expires_in": exp - int(now.timestamp()), "expires_at": exp,
                "service_account": hits[0].service_account}
