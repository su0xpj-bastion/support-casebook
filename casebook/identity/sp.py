"""A service provider MODELLED on the documented Claude Team/Enterprise SSO + provisioning rules.

Not Anthropic's system (Anthropic's SSO runs through WorkOS). The SAML checks use real XML-signature
verification (signxml); the policy below is transcribed from the support articles:

  Setting up SSO            https://support.claude.com/en/articles/13132885-setting-up-single-sign-on-sso
  JIT or SCIM provisioning  https://support.claude.com/en/articles/13133195-setting-up-jit-or-scim-provisioning
  Before enabling SSO       https://support.claude.com/en/articles/10276682-important-considerations-before-enabling-s

Error codes ("saml.audience_mismatch" etc.) are casebook's own names, not product strings.
"""
from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass, field
from typing import Any

from cryptography import x509
from lxml import etree
from signxml import InvalidSignature, SignatureConfiguration, XMLVerifier

from casebook.identity.idp import NS

TXT_PREFIX = "anthropic-domain-verification-"


class LoginError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


@dataclass
class Member:
    email: str
    role: str = "user"                 # primary_owner | owner | admin | user
    seat: str | None = "standard"      # premium | standard | None (no seat)
    source: str = "invite"             # invite | jit | scim | personal


@dataclass
class Org:
    sp_entity_id: str = "https://sso.claude.example/saml/casebook-org"
    acs_url: str = "https://sso.claude.example/saml/casebook-org/acs"
    idp_entity_id: str = ""
    idp_cert_pem: bytes = b""
    clock_skew_s: int = 60
    domains: dict[str, dict[str, Any]] = field(default_factory=dict)   # domain -> {token, verified}
    require_sso_claude: bool = False
    require_sso_console: bool = False
    provisioning: str = "invite"       # invite | jit | scim
    group_mappings: dict[str, tuple[str, str | None]] = field(default_factory=dict)  # group -> (role, seat)
    seats: dict[str, int] = field(default_factory=lambda: {"premium": 5, "standard": 50})
    members: dict[str, Member] = field(default_factory=dict)
    invites: set[str] = field(default_factory=set)
    events: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ domain verification (AN-32)

    def add_domain(self, domain: str) -> str:
        """Returns the TXT value. Shown once; adding the same domain again issues a NEW value."""
        token = TXT_PREFIX + secrets.token_hex(12)
        self.domains[domain] = {"token": token, "verified": False}
        return token

    def remove_domain(self, domain: str) -> None:
        self.domains.pop(domain, None)

    def refresh_domain(self, domain: str, dns: dict[str, list[str]]) -> str:
        d = self.domains[domain]
        if d["token"] in dns.get(domain, []):
            d["verified"] = True
            return "verified"
        return "pending"

    def _domain_ok(self, email: str) -> bool:
        return self.domains.get(email.split("@")[-1], {}).get("verified", False)

    # ------------------------------------------------------------ SAML login

    def saml_login(self, xml: bytes, now: dt.datetime) -> Member:
        cert = x509.load_pem_x509_certificate(self.idp_cert_pem)
        try:
            # verification_time = the SP's clock, so scenarios can stand on either side of an expiry date.
            # signxml checks certificate validity itself and reports an expired cert as InvalidSignature.
            verified = XMLVerifier().verify(etree.fromstring(xml), x509_cert=self.idp_cert_pem.decode(),
                                            expect_config=SignatureConfiguration(expect_references=1, verification_time=now)).signed_xml
        except InvalidSignature as e:
            if "certificate has expired" in str(e) or now > cert.not_valid_after_utc:
                raise LoginError("saml.idp_cert_expired", f"configured IdP signing certificate expired "
                                                         f"{cert.not_valid_after_utc:%Y-%m-%d} (library said: {str(e)[:90]}…)") from e
            raise LoginError("saml.signature_invalid", f"signature does not verify against the configured IdP cert: {e}") from e
        root = etree.fromstring(xml)
        if root.get("Destination") != self.acs_url:
            raise LoginError("saml.destination_mismatch", f"Destination {root.get('Destination')} != ACS {self.acs_url}")
        if verified.findtext("saml:Issuer", namespaces=NS) != self.idp_entity_id:
            raise LoginError("saml.issuer_mismatch", f"Issuer {verified.findtext('saml:Issuer', namespaces=NS)}")
        cond = verified.find("saml:Conditions", NS)
        aud = cond.findtext("saml:AudienceRestriction/saml:Audience", namespaces=NS)
        if aud != self.sp_entity_id:
            raise LoginError("saml.audience_mismatch", f"Audience {aud} != SP entity ID {self.sp_entity_id}")
        skew = dt.timedelta(seconds=self.clock_skew_s)
        nb = dt.datetime.fromisoformat(cond.get("NotBefore").replace("Z", "+00:00"))
        noa = dt.datetime.fromisoformat(cond.get("NotOnOrAfter").replace("Z", "+00:00"))
        if now + skew < nb:
            raise LoginError("saml.not_yet_valid", f"NotBefore {nb:%H:%M:%S} is ahead of SP clock {now:%H:%M:%S} by more than {self.clock_skew_s}s")
        if now - skew >= noa:
            raise LoginError("saml.expired", f"NotOnOrAfter {noa:%H:%M:%S} passed (SP clock {now:%H:%M:%S})")
        email = verified.findtext("saml:Subject/saml:NameID", namespaces=NS) or ""
        attrs = {a.get("Name"): [v.text for v in a.findall("saml:AttributeValue", NS)]
                 for a in verified.findall("saml:AttributeStatement/saml:Attribute", NS)}
        if "@" not in email:
            raise LoginError("saml.nameid_not_email", f"NameID '{email}' is not an email address")
        if not self._domain_ok(email):
            raise LoginError("sso.domain_not_verified", f"{email.split('@')[-1]} is not a verified domain")
        return self._provision_on_login(email, attrs.get("groups", []))

    def _provision_on_login(self, email: str, groups: list[str]) -> Member:
        m = self.members.get(email)
        if self.provisioning == "invite" and m is None and email not in self.invites:
            raise LoginError("sso.not_invited", f"{email} has no invite (provisioning: invite only)")
        if self.provisioning == "scim" and m is None:
            raise LoginError("sso.not_provisioned", f"{email} was not provisioned by SCIM")
        mapped = self._map(groups)
        if self.group_mappings and mapped is None and (m is None or m.role != "primary_owner"):
            if m is not None and self.provisioning == "jit":
                del self.members[email]  # JIT + mappings: removed from every mapped group -> removed at next login
            raise LoginError("sso.no_mapped_group", f"{email} is in no mapped group")
        if m is None:
            m = Member(email, role="user", seat=None, source=self.provisioning)
            self.members[email] = m
            self.events.append(f"created {email} via {self.provisioning}")
        if mapped and m.role != "primary_owner":
            m.role, seat = mapped
            if m.seat is None:
                m.seat = self._take_seat(seat)
        elif m.seat is None and not self.group_mappings:
            m.seat = self._take_seat("premium") or self._take_seat("standard")
        return m

    def _map(self, groups: list[str]) -> tuple[str, str | None] | None:
        rank = {"owner": 3, "admin": 2, "user": 1}
        hits = [self.group_mappings[g] for g in groups if g in self.group_mappings]
        return max(hits, key=lambda h: rank.get(h[0], 0)) if hits else None

    def _take_seat(self, seat: str | None) -> str | None:
        """New users get the requested seat type if one is unassigned, else no seat."""
        if seat and self.seats.get(seat, 0) > 0:
            self.seats[seat] -= 1
            return seat
        return None

    # ------------------------------------------------------------ password / email login (Require SSO)

    def email_login(self, email: str, product: str = "claude") -> Member:
        enforced = self.require_sso_claude if product == "claude" else self.require_sso_console
        if enforced and self._domain_ok(email):
            raise LoginError("sso.required", f"Require SSO for {product.title()} is on: {email} must use Continue with SSO")
        m = self.members.get(email)
        if m is None:
            raise LoginError("login.unknown_member", email)
        return m

    # ------------------------------------------------------------ group mappings (AN-37)

    def enable_group_mappings(self, mappings: dict[str, tuple[str, str | None]], directory_groups: dict[str, list[str]]) -> list[str]:
        """Mappings set role and seat only. Owners/Admins not in a mapped Owner/Admin group are
        downgraded — including the admin doing the configuring. The Primary Owner is exempt."""
        self.group_mappings = dict(mappings)
        changes = []
        for email, m in self.members.items():
            if m.role == "primary_owner":
                continue
            mapped = self._map(directory_groups.get(email, []))
            new_role = mapped[0] if mapped else "user"
            if new_role != m.role:
                changes.append(f"{email}: {m.role} -> {new_role}")
                m.role = new_role
        self.events += changes
        return changes

    # ------------------------------------------------------------ SCIM (AN-36) — see scim.py for the wire shape

    def scim_enable_preview(self, idp_assigned: set[str]) -> dict[str, Any]:
        """Saving SCIM deprovisions members not assigned to the app in the IdP (Primary Owner exempt)."""
        remove = sorted(e for e, m in self.members.items() if e not in idp_assigned and m.role != "primary_owner")
        return {"would_deprovision": remove, "count": len(remove), "members_before": len(self.members)}

    def scim_save(self, idp_assigned: set[str], *, max_removals: int | None = None) -> dict[str, Any]:
        preview = self.scim_enable_preview(idp_assigned)
        if max_removals is not None and preview["count"] > max_removals:
            return {"saved": False, "reason": f"preview would remove {preview['count']} > guard {max_removals}", **preview}
        for e in preview["would_deprovision"]:
            m = self.members.pop(e)
            if m.seat:
                self.seats[m.seat] = self.seats.get(m.seat, 0) + 1
        self.provisioning = "scim"
        self.events.append(f"scim enabled; deprovisioned {preview['count']}")
        return {"saved": True, **preview}

    def remove_member(self, email: str) -> None:
        m = self.members.pop(email, None)
        if m and m.seat:
            self.seats[m.seat] = self.seats.get(m.seat, 0) + 1
