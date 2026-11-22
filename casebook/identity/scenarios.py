"""Identity packs. Each scenario breaks one thing, captures what support would read, and
shows the documented fix working. Scope: protocol (SAML / OAuth / OIDC behaviour any
IdP or client shows) vs vendor-policy (SCIM, JIT, group mappings, domain verification, WIF, modelled
from vendor docs)."""
from __future__ import annotations

import datetime as dt
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization

from casebook.identity import idp as idp_mod
from casebook.identity.keys import signing_cert, utc
from casebook.identity.oauth import AuthorizationServer, OAuthError, OidcRelyingParty, jwk
from casebook.identity.scim import Provisioner, ScimStore
from casebook.identity.sp import LoginError, Member, Org
from casebook.identity.wif import Mapping, WifError, WifProvider, validate_mapping_value

NOW = utc(2026, 11, 21, 9, 0)
DOMAIN = "acme-mutual.example"


def _setup(now: dt.datetime = NOW, cert_days_left: int = 300) -> tuple[idp_mod.StandInIdP, Org]:
    cert = signing_cert("Acme IdP signing", now - dt.timedelta(days=65), now + dt.timedelta(days=cert_days_left), "idp-2026")
    idp = idp_mod.StandInIdP("https://idp.acme-mutual.example/saml", cert)
    org = Org(idp_entity_id=idp.entity_id, idp_cert_pem=cert.cert_pem, provisioning="jit")
    token = org.add_domain(DOMAIN)
    org.refresh_domain(DOMAIN, {DOMAIN: [token]})
    return idp, org


def _login(org: Org, xml: bytes, now: dt.datetime = NOW) -> dict[str, Any]:
    try:
        m = org.saml_login(xml, now)
        return {"ok": True, "role": m.role, "seat": m.seat}
    except LoginError as e:
        return {"ok": False, "code": e.code, "detail": e.detail}


def _resp(idp: idp_mod.StandInIdP, org: Org, email: str, **kw: Any) -> bytes:
    return idp.saml_response(email, sp_entity_id=kw.pop("audience", org.sp_entity_id), acs_url=kw.pop("acs", org.acs_url),
                             now=kw.pop("now", NOW), **kw)


# ---------------------------------------------------------------- SAML / OAuth / OIDC fundamentals (scope: protocol)

def id01(_: Any = None) -> dict[str, Any]:
    idp, org = _setup()
    idp.add("dana.ito@acme-mutual.example", ["claude-users"])
    bad = _resp(idp, org, "dana.ito@acme-mutual.example", nameid="dito")             # IdP sends username, not email
    other = _resp(idp, org, "dana.ito@acme-mutual.example", nameid="dana.ito@acme-mutual-old.example")  # stale alias domain
    good = _resp(idp, org, "dana.ito@acme-mutual.example")
    out = {"nameid_username": _login(org, bad), "nameid_old_domain": _login(org, other), "fixed": _login(org, good),
           "decoded_bad": idp_mod.decode(bad)["nameid"]}
    out["finding"] = {"cls": "saml.nameid_mismatch", "detail": f"NameID 'dito' -> {out['nameid_username']['code']}; NameID on an "
                      f"unverified alias domain -> {out['nameid_old_domain']['code']}; NameID = primary email -> ok={out['fixed']['ok']}"}
    return out


def id02(_: Any = None) -> dict[str, Any]:
    idp, org = _setup()
    idp.add("sam.okafor@acme-mutual.example", ["claude-users"])
    fast = _resp(idp, org, "sam.okafor@acme-mutual.example", clock_offset_s=180)       # IdP clock 3 min ahead
    late = _resp(idp, org, "sam.okafor@acme-mutual.example", now=NOW - dt.timedelta(minutes=10))  # replayed/queued 10 min
    small = _resp(idp, org, "sam.okafor@acme-mutual.example", clock_offset_s=45)        # within 60 s skew
    out = {"idp_clock_ahead_180s": _login(org, fast), "assertion_10min_old": _login(org, late), "idp_clock_ahead_45s": _login(org, small),
           "decoded": idp_mod.decode(fast)}
    out["finding"] = {"cls": "saml.clock_skew", "detail": f"IdP clock +180 s -> {out['idp_clock_ahead_180s']['code']}; assertion used "
                      f"10 min after issue -> {out['assertion_10min_old']['code']}; +45 s within the 60 s skew -> ok"}
    return out


def id03(_: Any = None) -> dict[str, Any]:
    idp, org = _setup()
    idp.add("lee.park@acme-mutual.example", ["claude-users"])
    wrong_aud = _resp(idp, org, "lee.park@acme-mutual.example", audience="https://sso.claude.example/saml/OLD-org")
    wrong_acs = _resp(idp, org, "lee.park@acme-mutual.example", acs="https://sso.claude.example/saml/OLD-org/acs")
    out = {"audience_from_old_app": _login(org, wrong_aud), "acs_from_old_app": _login(org, wrong_acs),
           "decoded_audience": idp_mod.decode(wrong_aud)["audience"]}
    out["finding"] = {"cls": "saml.audience_or_acs", "detail": f"IdP app copied from an old org: Audience -> {out['audience_from_old_app']['code']}; "
                      f"ACS/Destination -> {out['acs_from_old_app']['code']}"}
    return out


def id04(_: Any = None) -> dict[str, Any]:
    idp, org = _setup()
    idp.add("priya.nair@acme-mutual.example", ["claude-users"])
    new_cert = signing_cert("Acme IdP signing (rotated)", NOW - dt.timedelta(days=1), NOW + dt.timedelta(days=730), "idp-2027")
    rotated = _resp(idp, org, "priya.nair@acme-mutual.example", cert=new_cert)   # IdP signs with new key, SP still has old cert
    before = _login(org, rotated)
    org.idp_cert_pem = new_cert.cert_pem                                         # admin updates metadata in the SP
    after = _login(org, rotated)
    out = {"sp_has_old_cert": before, "sp_updated": after}
    out["finding"] = {"cls": "saml.cert_rotated_idp_only", "detail": f"IdP rotated its signing cert, SP not updated -> {before['code']}; "
                      f"SP metadata updated -> ok={after['ok']}"}
    return out


def id05(_: Any = None) -> dict[str, Any]:
    k = signing_cert("AS", NOW - dt.timedelta(days=1), NOW + dt.timedelta(days=365), "as-1")
    as_ = AuthorizationServer("https://idp.acme-mutual.example", [k], clients={"claims-app": ["https://claims.acme-mutual.example/callback"]})
    out: dict[str, Any] = {}
    for label, uri in (("trailing_slash", "https://claims.acme-mutual.example/callback/"),
                       ("http_not_https", "http://claims.acme-mutual.example/callback"),
                       ("exact", "https://claims.acme-mutual.example/callback")):
        try:
            out[label] = {"ok": True, "redirect": as_.authorize("claims-app", uri, "openid", "dana")[:60]}
        except OAuthError as e:
            out[label] = {"ok": False, "error": e.error, "redirected_to_client": e.redirect, "detail": e.description[:120]}
    out["finding"] = {"cls": "oauth.redirect_uri_mismatch", "detail": f"trailing slash -> {out['trailing_slash']['error']}; http vs https -> "
                      f"{out['http_not_https']['error']}; the AS shows the error itself and never redirects back "
                      f"(redirected={out['trailing_slash']['redirected_to_client']}), so the app never sees it"}
    return out


def id06(_: Any = None) -> dict[str, Any]:
    k = signing_cert("AS", NOW - dt.timedelta(days=1), NOW + dt.timedelta(days=365), "as-1")
    as_ = AuthorizationServer("https://idp.acme-mutual.example", [k], clients={"claims-app": ["https://claims.acme-mutual.example/cb"]})
    code = as_.authorize("claims-app", "https://claims.acme-mutual.example/cb", "openid offline_access", "dana").split("code=")[1]
    t1 = as_.token("authorization_code", NOW, code=code, redirect_uri="https://claims.acme-mutual.example/cb", client_id="claims-app")
    t2 = as_.token("refresh_token", NOW, refresh_token=t1["refresh_token"])
    as_.revoke(t2["refresh_token"])                                   # user offboarded / password reset revokes grants
    out: dict[str, Any] = {"first_refresh_ok": bool(t2["access_token"])}
    try:
        as_.token("refresh_token", NOW, refresh_token=t2["refresh_token"])
    except OAuthError as e:
        out["after_revocation"] = {"error": e.error, "detail": e.description}
    try:
        as_.token("authorization_code", NOW, code=code, redirect_uri="https://claims.acme-mutual.example/cb", client_id="claims-app")
    except OAuthError as e:
        out["code_reused"] = {"error": e.error}
    out["finding"] = {"cls": "oauth.invalid_grant", "detail": f"revoked refresh token -> {out['after_revocation']['error']}; reused "
                      f"authorization code -> {out['code_reused']['error']} — retrying never helps; re-run the authorization flow"}
    return out


def id07(_: Any = None) -> dict[str, Any]:
    old = signing_cert("AS old", NOW - dt.timedelta(days=200), NOW + dt.timedelta(days=200), "key-2026a")
    new = signing_cert("AS new", NOW - dt.timedelta(days=1), NOW + dt.timedelta(days=400), "key-2026b")
    as_ = AuthorizationServer("https://idp.acme-mutual.example", [old])
    rp_good = OidcRelyingParty(as_.issuer, "claims-app", as_.jwks)
    rp_cache_only = OidcRelyingParty(as_.issuer, "claims-app", as_.jwks, refetch_on_unknown_kid=False)
    rp_good.verify(as_.id_token("dana", "claims-app", NOW), NOW)
    rp_cache_only.verify(as_.id_token("dana", "claims-app", NOW), NOW)
    as_.keys = [new]                                                   # rotated AND old key removed at once
    t = NOW + dt.timedelta(minutes=2)
    tok = as_.id_token("dana", "claims-app", t)
    out: dict[str, Any] = {}
    for label, rp in (("rp_refetches_on_unknown_kid", rp_good), ("rp_cache_ttl_only", rp_cache_only)):
        try:
            rp.verify(tok, t)
            out[label] = {"ok": True, "jwks_fetches": rp.fetches}
        except OAuthError as e:
            out[label] = {"ok": False, "detail": e.description, "jwks_fetches": rp.fetches}
    try:
        rp_good.verify(as_.id_token("dana", "other-app", t), t)
    except OAuthError as e:
        out["wrong_audience"] = e.description[:80]
    try:
        rp_good.verify(as_.id_token("dana", "claims-app", t, issuer="https://idp.acme-mutual.example/"), t)
    except OAuthError as e:
        out["issuer_trailing_slash"] = e.description[:80]
    out["finding"] = {"cls": "oidc.jwks_rotation", "detail": f"key rotated and old key pulled at once: RP that refetches on an unknown kid ok="
                      f"{out['rp_refetches_on_unknown_kid']['ok']}; RP relying on its 600 s cache ok={out['rp_cache_ttl_only']['ok']} "
                      f"(fails until the cache expires); issuer with a "
                      f"trailing slash -> {'rejected' if 'issuer_trailing_slash' in out else 'accepted'} by a strict RP"}
    return out


# ---------------------------------------------------------------- Anthropic-documented SSO/provisioning (docs-derived)

def an32(_: Any = None) -> dict[str, Any]:
    org = Org()
    token = org.add_domain(DOMAIN)
    out = {"txt_on_www": org.refresh_domain(DOMAIN, {f"www.{DOMAIN}": [token]}),
           "txt_lowercased": org.refresh_domain(DOMAIN, {DOMAIN: [token.upper()]})}
    org.remove_domain(DOMAIN)
    token2 = org.add_domain(DOMAIN)                                      # re-added "to see the value again"
    out["old_value_after_readd"] = org.refresh_domain(DOMAIN, {DOMAIN: [token]})
    out["new_value_at_root"] = org.refresh_domain(DOMAIN, {DOMAIN: [token2]})
    out["value_changed_on_readd"] = token != token2
    out["finding"] = {"cls": "sso.domain_pending", "detail": f"TXT on www -> {out['txt_on_www']}; case changed -> {out['txt_lowercased']}; "
                      f"domain removed and re-added (new value issued) with the old record -> {out['old_value_after_readd']}; "
                      f"new value at the root -> {out['new_value_at_root']}"}
    return out


def an33(_: Any = None) -> dict[str, Any]:
    idp, org = _setup(cert_days_left=5)
    org.members["owner@acme-mutual.example"] = Member("owner@acme-mutual.example", role="primary_owner", seat="premium")
    org.members["dana.ito@acme-mutual.example"] = Member("dana.ito@acme-mutual.example", role="admin", seat="premium")
    idp.add("owner@acme-mutual.example", ["claude-admins"])
    idp.add("dana.ito@acme-mutual.example", ["claude-admins"])
    org.require_sso_claude = org.require_sso_console = True
    later = NOW + dt.timedelta(days=6)                                   # the IdP cert expired yesterday
    out = {"before_expiry": _login(org, _resp(idp, org, "owner@acme-mutual.example"))}
    out["sso_after_expiry"] = _login(org, _resp(idp, org, "dana.ito@acme-mutual.example", now=later), later)
    try:
        org.email_login("owner@acme-mutual.example")
        out["email_login_owner"] = "ok"
    except LoginError as e:
        out["email_login_owner"] = e.code
    out["anyone_can_fix_in_app"] = False
    out["recovery"] = "Support verifies ownership via a request from an Owner/Primary Owner email address, then the cert is updated and Test sign-in run"
    out["finding"] = {"cls": "sso.cert_expired_lockout", "detail": f"IdP cert expired with Require SSO on: SSO -> {out['sso_after_expiry']['code']}, "
                      f"email login -> {out['email_login_owner']}; nobody can sign in to rotate it — recovery is via Support from an Owner email"}
    return out


def an35(_: Any = None) -> dict[str, Any]:
    idp, org = _setup()
    org.members["mei.chen@acme-mutual.example"] = Member("mei.chen@acme-mutual.example", source="personal", seat="premium")
    idp.add("mei.chen@acme-mutual.example", assigned=False)                # has a personal Pro account; not in the SSO app
    before = org.email_login("mei.chen@acme-mutual.example").source
    org.require_sso_claude = True
    out: dict[str, Any] = {"before": before}
    try:
        org.email_login("mei.chen@acme-mutual.example")
    except LoginError as e:
        out["email_after_enforce"] = e.code
    try:
        _resp(idp, org, "mei.chen@acme-mutual.example")
    except PermissionError as e:
        out["sso_after_enforce"] = f"IdP refuses: {e}"
    out["account_deleted"] = "mei.chen@acme-mutual.example" not in org.members
    out["finding"] = {"cls": "sso.personal_accounts_unreachable", "detail": f"Require SSO for Claude on a verified domain: personal account owner "
                      f"not in the IdP app -> email {out['email_after_enforce']}, SSO refused by IdP; account deleted={out['account_deleted']} "
                      "(unreachable, not deleted) — export history before rollout"}
    return out


def an36(_: Any = None) -> dict[str, Any]:
    _, org = _setup()
    for n in range(40):
        org.members[f"user{n:02d}@acme-mutual.example"] = Member(f"user{n:02d}@acme-mutual.example", source="jit")
    org.members["owner@acme-mutual.example"] = Member("owner@acme-mutual.example", role="primary_owner")
    assigned_so_far = {f"user{n:02d}@acme-mutual.example" for n in range(10)}  # IdP admin assigned only the pilot group
    guarded = org.scim_save(assigned_so_far, max_removals=5)
    unguarded = org.scim_save(assigned_so_far)
    out = {"guarded": {k: guarded[k] for k in ("saved", "count", "reason") if k in guarded},
           "unguarded": {"saved": unguarded["saved"], "deprovisioned": unguarded["count"]},
           "primary_owner_kept": "owner@acme-mutual.example" in org.members, "members_after": len(org.members)}
    out["finding"] = {"cls": "scim.save_before_assign", "detail": f"SCIM saved with 10 of 40 users assigned: preview shows {guarded['count']} removals "
                      f"(guard stops it); saved anyway -> {unguarded['count']} deprovisioned, Primary Owner exempt={out['primary_owner_kept']}"}
    return out


def an37(_: Any = None) -> dict[str, Any]:
    _, org = _setup()
    org.members.update({e: Member(e, role=r, seat="premium") for e, r in (("owner@acme-mutual.example", "primary_owner"),
                        ("dana.ito@acme-mutual.example", "admin"), ("it.admin@acme-mutual.example", "admin"))})
    groups = {"dana.ito@acme-mutual.example": ["claude-admins"], "it.admin@acme-mutual.example": ["it-staff"]}   # the configurer
    changes = org.enable_group_mappings({"claude-admins": ("admin", "premium"), "claude-users": ("user", "standard")}, groups)
    out = {"changes": changes, "it_admin_role": org.members["it.admin@acme-mutual.example"].role,
           "primary_owner_role": org.members["owner@acme-mutual.example"].role}
    out["finding"] = {"cls": "mapping.admin_downgraded", "detail": f"group mappings enabled by an admin who is in no Admin-mapped group: {changes}; "
                      "Primary Owner untouched — fix: another admin restores the role, or add self to the Admin group and sync"}
    return out


def an38(_: Any = None) -> dict[str, Any]:
    idp, org = _setup()
    idp.add("former@acme-mutual.example", ["claude-users"])
    _login(org, _resp(idp, org, "former@acme-mutual.example"))
    seat_before = org.members["former@acme-mutual.example"].seat
    org.remove_member("former@acme-mutual.example")                      # removed in Claude only
    comeback = _login(org, _resp(idp, org, "former@acme-mutual.example"))
    idp.users["former@acme-mutual.example"].assigned = False             # now unassigned in the IdP, still a member
    out = {"seat_before": seat_before, "reappeared_after_claude_only_removal": comeback["ok"],
           "still_member_after_idp_unassign": "former@acme-mutual.example" in org.members,
           "seat_still_held": org.members.get("former@acme-mutual.example", Member("x", seat=None)).seat}
    out["finding"] = {"cls": "jit.no_auto_removal", "detail": f"JIT: removed only in Claude -> back at next SSO login ({comeback['ok']}); unassigned "
                      f"only in the IdP -> still a member holding a {out['seat_still_held']} seat. Remove in both places"}
    return out


# ---------------------------------------------------------------- OpenAI-documented SCIM and WIF (docs-derived)

def oa30(_: Any = None) -> dict[str, Any]:
    store = ScimStore()
    prov = Provisioner(store)
    assigned = {"ari.gold@northwind.example": ["chatgpt-users"], "mei.chen@northwind.example": ["chatgpt-users"]}
    prov.sync(assigned, now_min=40)
    uid = store.by_username("ari.gold@northwind.example")["id"]
    store.delete_user(uid)                                               # admin removes Ari in workspace settings only
    forced = prov.sync(assigned, now_min=45, forced=True)
    early = prov.sync(assigned, now_min=55)
    later = prov.sync(assigned, now_min=80)
    del assigned["ari.gold@northwind.example"]
    final = prov.sync(assigned, now_min=120)
    out = {"force_sync": forced["reason"], "sync_at_55": early["reason"], "sync_at_80_recreated": later.get("created"),
           "after_idp_unassign_removed": final.get("removed")}
    out["finding"] = {"cls": "scim.removed_user_returns", "detail": f"removed in the workspace only -> re-created by the next sync {later.get('created')}; "
                      f"a sync can't be forced; unassigning in the IdP removes them for good {final.get('removed')}"}
    return out


def _wif_world() -> tuple[Any, Any, Any]:
    k1 = signing_cert("CI issuer", NOW - dt.timedelta(days=30), NOW + dt.timedelta(days=300), "ci-key-1")
    published = {"keys": [jwk(k1)]}

    def mint(sub: str, key: Any = k1, aud: str = "openai-wif", iss: str = "https://ci.northwind.example",
             exp_in: dt.timedelta = dt.timedelta(minutes=30), at: dt.datetime = NOW) -> str:
        return jwt.encode({"iss": iss, "sub": sub, "aud": aud, "iat": int(at.timestamp()), "exp": int((at + exp_in).timestamp())},
                          serialization.load_pem_private_key(key.key_pem, None), algorithm="RS256", headers={"kid": key.kid})
    return k1, published, mint


def oa38(_: Any = None) -> dict[str, Any]:
    _, published, mint = _wif_world()
    p = WifProvider("https://ci.northwind.example/", "openai-wif", fetch_jwks=lambda: published,
                    mappings=[Mapping("svc-deploy", {"sub": "repo:northwind/app:ref:refs/heads/main"})])
    tok = mint("repo:northwind/app:ref:refs/heads/main")
    ok = p.exchange(tok, NOW)
    p.mappings.append(Mapping("svc-ci-all", {"sub": "repo:northwind/app:*"}))   # someone adds a broad mapping
    try:
        p.exchange(tok, NOW)
        after: dict[str, Any] = {"ok": True}
    except WifError as e:
        after = {"error": e.error, "detail": e.description}
    out = {"one_mapping": ok["service_account"], "after_second_mapping": after,
           "issuer_trailing_slash_ignored": ok["service_account"] == "svc-deploy"}
    out["finding"] = {"cls": "wif.multiple_mappings", "detail": f"exchange worked with one mapping; a second, broader mapping that also matches -> "
                      f"{after['error']} ({after['detail'][:60]})"}
    return out


def oa39(_: Any = None) -> dict[str, Any]:
    k1, published, mint = _wif_world()
    k2 = signing_cert("CI issuer 2", NOW, NOW + dt.timedelta(days=300), "ci-key-2")
    uploaded = WifProvider("https://ci.northwind.example", "openai-wif", key_source="uploaded", uploaded_jwks={"keys": [jwk(k1)]},
                           mappings=[Mapping("svc-deploy", {"sub": "repo:northwind/app:*"})])
    discovery = WifProvider("https://ci.northwind.example", "openai-wif", fetch_jwks=lambda: published,
                            mappings=[Mapping("svc-deploy", {"sub": "repo:northwind/app:*"})])
    discovery.exchange(mint("repo:northwind/app:ref:refs/heads/main"), NOW)      # warms the 600 s cache
    published["keys"] = [jwk(k2)]                                                  # CI rotates to key 2, publishes only key 2
    tok2 = mint("repo:northwind/app:ref:refs/heads/main", key=k2)
    out: dict[str, Any] = {}
    try:
        uploaded.exchange(tok2, NOW)
    except WifError as e:
        out["uploaded_jwks_stale"] = {"error": e.error, "detail": e.description}
    out["discovery_unknown_kid_refreshes"] = discovery.exchange(tok2, NOW + dt.timedelta(seconds=30))["service_account"]
    try:
        discovery.exchange(mint("repo:northwind/app:ref:refs/heads/main", key=k1), NOW + dt.timedelta(seconds=40))
    except WifError as e:
        out["inflight_old_key_token"] = {"error": e.error, "detail": e.description}
    uploaded.uploaded_jwks = {"keys": [jwk(k1), jwk(k2)]}
    out["uploaded_after_update"] = uploaded.exchange(tok2, NOW)["service_account"]
    out["finding"] = {"cls": "wif.key_rotation", "detail": f"uploaded JWKS not updated before the new kid -> {out['uploaded_jwks_stale']['error']}; "
                      f"discovery refreshes on an unknown kid -> ok; tokens still signed with the removed key -> "
                      f"{out['inflight_old_key_token']['error']} — publish both keys during rotation"}
    return out


def oa40(_: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for v in ("*", "repo:*/main", "repo:northwind/*:main", "repo:northwind/app:*", "repo:northwind/app:ref:refs/heads/main"):
        try:
            validate_mapping_value(v)
            out[v] = "accepted"
        except ValueError:
            out[v] = "rejected"
    out["finding"] = {"cls": "wif.wildcard_rules", "detail": "; ".join(f"{k} {v}" for k, v in out.items() if k != "finding")}
    return out


def oa41(_: Any = None) -> dict[str, Any]:
    _, published, mint = _wif_world()
    p = WifProvider("https://ci.northwind.example", "openai-wif", fetch_jwks=lambda: published,
                    mappings=[Mapping("svc-batch", {"sub": "repo:northwind/batch:*"})])
    long_sub = mint("repo:northwind/batch:ref:refs/heads/main", exp_in=dt.timedelta(hours=6))
    short_sub = mint("repo:northwind/batch:ref:refs/heads/main", exp_in=dt.timedelta(minutes=20))
    a, b = p.exchange(long_sub, NOW), p.exchange(short_sub, NOW)
    out = {"long_subject_expires_in": a["expires_in"], "short_subject_expires_in": b["expires_in"], "refresh_token": "refresh_token" in a}
    try:
        p.exchange(short_sub, NOW + dt.timedelta(minutes=25))
    except WifError as e:
        out["reexchange_with_expired_subject"] = e.error
    out["finding"] = {"cls": "wif.one_hour_no_refresh", "detail": f"6 h subject token -> access token for {a['expires_in']} s; 20 min subject -> "
                      f"{b['expires_in']} s; no refresh token; re-exchange needs a fresh subject token "
                      f"({out['reexchange_with_expired_subject']})"}
    return out


SCENARIOS = {"ID-01": id01, "ID-02": id02, "ID-03": id03, "ID-04": id04, "ID-05": id05, "ID-06": id06, "ID-07": id07,
             "AN-32": an32, "AN-33": an33, "AN-35": an35, "AN-36": an36, "AN-37": an37, "AN-38": an38,
             "OA-30": oa30, "OA-38": oa38, "OA-39": oa39, "OA-40": oa40, "OA-41": oa41}
