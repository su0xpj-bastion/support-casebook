"""Stand-in identity provider: users, app assignment, groups, and signed SAML 2.0 assertions."""
from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass, field
from typing import Any

from lxml import etree
from signxml import XMLSigner, methods

from casebook.identity.keys import SigningCert

NS = {"saml": "urn:oasis:names:tc:SAML:2.0:assertion", "samlp": "urn:oasis:names:tc:SAML:2.0:protocol"}


@dataclass
class IdpUser:
    email: str
    groups: list[str] = field(default_factory=list)
    assigned: bool = True          # assigned to the service provider's app in the IdP
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class StandInIdP:
    entity_id: str
    cert: SigningCert
    users: dict[str, IdpUser] = field(default_factory=dict)

    def add(self, email: str, groups: list[str] | None = None, assigned: bool = True, **attrs: str) -> IdpUser:
        u = IdpUser(email, groups or [], assigned, dict(attrs))
        self.users[email] = u
        return u

    def saml_response(self, email: str, *, sp_entity_id: str, acs_url: str, now: dt.datetime,
                      nameid: str | None = None, lifetime_s: int = 300, clock_offset_s: int = 0,
                      cert: SigningCert | None = None) -> bytes:
        """Signed SAML Response. Overrides model real IdP misconfigurations:
        nameid (wrong NameID source attribute), clock_offset_s (IdP clock drift), cert (which key signs)."""
        u = self.users[email]
        if not u.assigned:
            raise PermissionError(f"{email} is not assigned to the app in the IdP")
        issued = now + dt.timedelta(seconds=clock_offset_s)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        resp = etree.Element(f"{{{NS['samlp']}}}Response", nsmap=NS, ID="_" + secrets.token_hex(8), Version="2.0",
                             IssueInstant=issued.strftime(fmt), Destination=acs_url)
        etree.SubElement(resp, f"{{{NS['saml']}}}Issuer").text = self.entity_id
        a = etree.SubElement(resp, f"{{{NS['saml']}}}Assertion", ID="_" + secrets.token_hex(8), Version="2.0",
                             IssueInstant=issued.strftime(fmt))
        etree.SubElement(a, f"{{{NS['saml']}}}Issuer").text = self.entity_id
        subj = etree.SubElement(a, f"{{{NS['saml']}}}Subject")
        etree.SubElement(subj, f"{{{NS['saml']}}}NameID",
                         Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress").text = nameid or email
        cond = etree.SubElement(a, f"{{{NS['saml']}}}Conditions", NotBefore=(issued - dt.timedelta(seconds=30)).strftime(fmt),
                                NotOnOrAfter=(issued + dt.timedelta(seconds=lifetime_s)).strftime(fmt))
        ar = etree.SubElement(cond, f"{{{NS['saml']}}}AudienceRestriction")
        etree.SubElement(ar, f"{{{NS['saml']}}}Audience").text = sp_entity_id
        stmt = etree.SubElement(a, f"{{{NS['saml']}}}AttributeStatement")
        for name, values in (("email", [email]), ("groups", u.groups), *((k, [v]) for k, v in u.attrs.items())):
            at = etree.SubElement(stmt, f"{{{NS['saml']}}}Attribute", Name=name)
            for v in values:
                etree.SubElement(at, f"{{{NS['saml']}}}AttributeValue").text = v
        c = cert or self.cert
        signed = XMLSigner(method=methods.enveloped, signature_algorithm="rsa-sha256", digest_algorithm="sha256").sign(
            a, key=c.key_pem, cert=c.cert_pem.decode())
        resp.replace(a, signed)
        return etree.tostring(resp)


def decode(xml: bytes) -> dict[str, Any]:
    """What a support engineer reads off a captured assertion (SAML-tracer style)."""
    root = etree.fromstring(xml)
    a = root.find("saml:Assertion", NS)
    cond = a.find("saml:Conditions", NS)
    return {
        "destination": root.get("Destination"),
        "issuer": a.findtext("saml:Issuer", namespaces=NS),
        "nameid": a.findtext("saml:Subject/saml:NameID", namespaces=NS),
        "audience": cond.findtext("saml:AudienceRestriction/saml:Audience", namespaces=NS),
        "not_before": cond.get("NotBefore"),
        "not_on_or_after": cond.get("NotOnOrAfter"),
        "attributes": {at.get("Name"): [v.text for v in at.findall("saml:AttributeValue", NS)]
                       for at in a.findall("saml:AttributeStatement/saml:Attribute", NS)},
    }
