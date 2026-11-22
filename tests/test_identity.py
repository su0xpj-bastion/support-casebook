"""Identity lab. Real XML signatures and JWTs; policy modelled on the cited docs."""
from __future__ import annotations

import datetime as dt

import pytest
import yaml

from casebook import packs
from casebook.identity import scenarios as S
from casebook.identity.idp import StandInIdP
from casebook.identity.keys import signing_cert

IDENTITY = sorted(p for p, v in packs.load_all().items() if v.meta.get("surface") == "identity")


def test_identity_pack_set() -> None:
    assert set(IDENTITY) == set(S.SCENARIOS)
    assert len(IDENTITY) == 18


@pytest.mark.parametrize("pid", IDENTITY)
def test_every_identity_pack_is_labelled(pid: str) -> None:
    """Every identity pack says whether it tests protocol behaviour or modelled vendor policy."""
    meta = packs.get(pid).meta
    assert meta["scope"] in ("protocol", "vendor-policy")
    assert meta["modelled_not_real"]


def test_signature_is_really_checked() -> None:
    """Tamper with a signed assertion: the SP must reject it (proves the crypto is real, not mocked)."""
    _idp, org = S._setup()
    _idp.add("dana.ito@acme-mutual.example", ["claude-users"])
    xml = S._resp(_idp, org, "dana.ito@acme-mutual.example").replace(b"claude-users", b"claude-admins")
    r = S._login(org, xml)
    assert r == {"ok": False, "code": "saml.signature_invalid", "detail": r["detail"]}


def test_id01_nameid() -> None:
    r = S.id01()
    assert r["nameid_username"]["code"] == "saml.nameid_not_email"
    assert r["nameid_old_domain"]["code"] == "sso.domain_not_verified" and r["fixed"]["ok"]


def test_id02_clock_skew() -> None:
    r = S.id02()
    assert r["idp_clock_ahead_180s"]["code"] == "saml.not_yet_valid"
    assert r["assertion_10min_old"]["code"] == "saml.expired" and r["idp_clock_ahead_45s"]["ok"]


def test_id03_audience_acs() -> None:
    r = S.id03()
    assert r["audience_from_old_app"]["code"] == "saml.audience_mismatch"
    assert r["acs_from_old_app"]["code"] == "saml.destination_mismatch"


def test_id04_cert_rotation() -> None:
    r = S.id04()
    assert r["sp_has_old_cert"]["code"] == "saml.signature_invalid" and r["sp_updated"]["ok"]


def test_id05_redirect_uri_never_redirects() -> None:
    r = S.id05()
    assert r["trailing_slash"]["error"] == "invalid_request" and r["trailing_slash"]["redirected_to_client"] is False
    assert r["http_not_https"]["error"] == "invalid_request" and r["exact"]["ok"]


def test_id06_invalid_grant() -> None:
    r = S.id06()
    assert r["first_refresh_ok"] and r["after_revocation"]["error"] == "invalid_grant" and r["code_reused"]["error"] == "invalid_grant"


def test_id07_jwks_rotation() -> None:
    r = S.id07()
    assert r["rp_refetches_on_unknown_kid"]["ok"] and r["rp_refetches_on_unknown_kid"]["jwks_fetches"] == 2
    assert r["rp_cache_ttl_only"]["ok"] is False and "no key with kid key-2026b" in r["rp_cache_ttl_only"]["detail"]
    assert "aud mismatch" in r["wrong_audience"] and "iss mismatch" in r["issuer_trailing_slash"]


def test_an32_domain_pending() -> None:
    r = S.an32()
    assert (r["txt_on_www"], r["txt_lowercased"], r["old_value_after_readd"], r["new_value_at_root"]) == \
        ("pending", "pending", "pending", "verified")
    assert r["value_changed_on_readd"]


def test_an33_lockout() -> None:
    r = S.an33()
    assert r["before_expiry"]["ok"] and r["sso_after_expiry"]["code"] == "saml.idp_cert_expired"
    assert r["email_login_owner"] == "sso.required"


def test_an35_personal_accounts() -> None:
    r = S.an35()
    assert r["before"] == "personal" and r["email_after_enforce"] == "sso.required"
    assert "not assigned" in r["sso_after_enforce"] and r["account_deleted"] is False


def test_an36_scim_save_trap() -> None:
    r = S.an36()
    assert r["guarded"]["saved"] is False and r["guarded"]["count"] == 30
    assert r["unguarded"]["deprovisioned"] == 30 and r["primary_owner_kept"] and r["members_after"] == 11


def test_an37_admin_downgrade() -> None:
    r = S.an37()
    assert r["it_admin_role"] == "user" and r["primary_owner_role"] == "primary_owner"
    assert r["changes"] == ["it.admin@acme-mutual.example: admin -> user"]


def test_an38_jit() -> None:
    r = S.an38()
    assert r["reappeared_after_claude_only_removal"] and r["still_member_after_idp_unassign"] and r["seat_still_held"]


def test_oa30_scim_returns() -> None:
    r = S.oa30()
    assert "cannot be forced" in r["force_sync"] and "not due" in r["sync_at_55"]
    assert r["sync_at_80_recreated"] == ["ari.gold@northwind.example"]
    assert r["after_idp_unassign_removed"] == ["ari.gold@northwind.example"]


def test_oa38_multiple_mappings() -> None:
    r = S.oa38()
    assert r["one_mapping"] == "svc-deploy" and r["after_second_mapping"]["error"] == "invalid_grant"
    assert "2 enabled mappings match" in r["after_second_mapping"]["detail"]


def test_oa39_rotation() -> None:
    r = S.oa39()
    assert r["uploaded_jwks_stale"]["error"] == "invalid_grant" and r["discovery_unknown_kid_refreshes"] == "svc-deploy"
    assert r["inflight_old_key_token"]["error"] == "invalid_grant" and r["uploaded_after_update"] == "svc-deploy"


def test_oa40_wildcards() -> None:
    r = S.oa40()
    assert [r[k] for k in ("*", "repo:*/main", "repo:northwind/*:main")] == ["rejected"] * 3
    assert r["repo:northwind/app:*"] == "accepted"


def test_oa41_lifetime() -> None:
    r = S.oa41()
    assert r["long_subject_expires_in"] == 3600 and r["short_subject_expires_in"] == 1200
    assert r["refresh_token"] is False and r["reexchange_with_expired_subject"] == "invalid_subject_token"


def test_expired_idp_cert_still_signs_valid_xml() -> None:
    """Signature math doesn't know about dates: the SP has to check NotAfter itself (AN-33)."""
    now = S.NOW
    old = signing_cert("expired", now - dt.timedelta(days=400), now - dt.timedelta(days=1), "x")
    idp = StandInIdP("https://idp.acme-mutual.example/saml", old)
    _, org = S._setup()
    org.idp_cert_pem = old.cert_pem
    idp.add("owner@acme-mutual.example")
    r = S._login(org, S._resp(idp, org, "owner@acme-mutual.example"))
    assert r["code"] == "saml.idp_cert_expired" and "expired 2026-11-20" in r["detail"]
    _ = yaml


def test_signxml_reports_expiry_as_invalid_signature() -> None:
    """Library behaviour worth knowing: signxml 5.3.0 raises InvalidSignature for an expired cert,
    so an IdP cert expiry can surface in logs as 'signature invalid'."""
    from lxml import etree
    from signxml import InvalidSignature, SignatureConfiguration, XMLVerifier

    now = S.NOW
    old = signing_cert("expired", now - dt.timedelta(days=400), now - dt.timedelta(days=1), "x")
    idp = StandInIdP("https://idp.acme-mutual.example/saml", old)
    idp.add("owner@acme-mutual.example")
    _, org = S._setup()
    xml = S._resp(idp, org, "owner@acme-mutual.example")
    with pytest.raises(InvalidSignature, match="certificate has expired"):
        XMLVerifier().verify(etree.fromstring(xml), x509_cert=old.cert_pem.decode(),
                             expect_config=SignatureConfiguration(expect_references=1, verification_time=now))
