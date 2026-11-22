"""A minimal SCIM 2.0 service-provider store (RFC 7643/7644 resource shapes) and an IdP-side
provisioner that pushes on a schedule — enough to reproduce 'removed user comes back' (OA-30)."""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"


@dataclass
class ScimStore:
    users: dict[str, dict[str, Any]] = field(default_factory=dict)   # id -> resource
    log: list[str] = field(default_factory=list)

    def by_username(self, user_name: str) -> dict[str, Any] | None:
        return next((u for u in self.users.values() if u["userName"].lower() == user_name.lower()), None)

    def post_user(self, user_name: str, email: str, groups: list[str]) -> tuple[int, dict[str, Any]]:
        if self.by_username(user_name):
            return 409, {"schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"], "status": "409", "scimType": "uniqueness"}
        uid = secrets.token_hex(6)
        u = {"schemas": [USER_SCHEMA], "id": uid, "userName": user_name, "active": True,
             "emails": [{"value": email, "primary": True}], "groups": [{"display": g} for g in groups]}
        self.users[uid] = u
        self.log.append(f"POST /Users {user_name}")
        return 201, u

    def delete_user(self, uid: str) -> int:
        self.log.append(f"DELETE /Users/{uid}")
        return 204 if self.users.pop(uid, None) else 404


@dataclass
class Provisioner:
    """IdP side: the IdP is the source of truth. sync() pushes every assigned user that is missing."""
    store: ScimStore
    interval_min: tuple[int, int] = (30, 40)   # OpenAI: typical sync 30–40 min, cannot be forced
    last_sync_min: int = 0

    def sync(self, assigned: dict[str, list[str]], now_min: int, *, forced: bool = False) -> dict[str, Any]:
        if forced:
            return {"ran": False, "reason": "a SCIM sync cannot be forced; next run is on the IdP's schedule"}
        if now_min - self.last_sync_min < self.interval_min[0]:
            return {"ran": False, "reason": f"next sync not due (every {self.interval_min[0]}–{self.interval_min[1]} min)"}
        self.last_sync_min = now_min
        created, removed = [], []
        for email, groups in assigned.items():
            if not self.store.by_username(email):
                self.store.post_user(email, email, groups)
                created.append(email)
        for uid, u in list(self.store.users.items()):
            if u["userName"] not in assigned:
                self.store.delete_user(uid)
                removed.append(u["userName"])
        return {"ran": True, "created": created, "removed": removed}
