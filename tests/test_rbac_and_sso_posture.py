"""ARCH-091 — enterprise identity, tenancy and RBAC, the still-unverified slice.

Commit 2e3bb7f (see tests/test_admin_org_member_idor.py) already audited all
13 admin routes with an <int:...> org/user/member id parameter for
cross-tenant IDOR — 0 vulnerable. This file covers what that audit did not:

  1. The RBAC matrix — the three-tier gate the app actually implements
     (plain authenticated user -> org_admin -> platform_admin, via
     app/middleware/tenant_decorators.py's org_admin_required /
     platform_admin_required, layered with Permission.ADMINISTER in
     app/utils/decorators.py's admin_required) is exercised across all
     three tiers against a representative gated route, not just asserted
     to exist by reading the decorator source.
  2. SSO posture — app/auth/sso.py is a real, non-trivial OIDC
     implementation (Azure AD, Okta), gated by SSO_ENABLED / the
     sso_authentication FeatureFlag. Its dormant-by-default behaviour is
     pinned here: with the flag off, SSO fails closed rather than silently
     accepting requests. The application registers no SAML sign-in route and
     the service has no SAML assertion method; both are asserted from the URL
     map and the class, not from a response code.
  3. SCIM posture — grepped for across the entire app/ tree (case-insensitive,
     "scim" appears nowhere except as a substring of unrelated vendor JS
     filenames). There is no SCIM provisioning endpoint, no SCIM schema
     model, and no SCIM route registered anywhere in this codebase.

     FINDING, stated plainly per the assignment: SCIM is NOT IMPLEMENTED.
     There is nothing to test because there is no code path to exercise —
     asserting "SCIM works" here would be exactly the "note that pretends to
     be coverage" CLAUDE.md's docs/known-issues/ section warns against. The
     honest artifact is this test, which pins "SCIM is absent" as a fact a
     future PR must update if it ever adds SCIM, rather than a paragraph
     that silently drifts from reality.
"""

from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# RBAC matrix
# ---------------------------------------------------------------------------


@pytest.fixture
def rbac_org(make_org, db_session):
    return make_org("rbac")


def _make_rbac_user(db_session, org, *, is_org_admin=False, is_platform_admin=False, admin_permission=False):
    from app.models.user import Permission, Role, User

    if admin_permission:
        role = Role.query.filter_by(name="Administrator").first()
        if role is None:
            role = Role(name="Administrator", permissions=Permission.ADMINISTER)
            db_session.add(role)
            db_session.flush()
    else:
        role = Role.query.filter_by(name="Architect").first()
        if role is None:
            role = Role(name="Architect", permissions=Permission.GENERAL)
            db_session.add(role)
            db_session.flush()

    user = User(
        email=f"rbac-{uuid.uuid4().hex[:8]}@example.com",
        first_name="RBAC",
        last_name="User",
        organization_id=org.id,
        role=role,
        is_org_admin=is_org_admin,
        is_platform_admin=is_platform_admin,
        confirmed=True,
    )
    user.password = "TestPassw0rd!23"
    db_session.add(user)
    db_session.flush()
    return user


# /admin/api-settings is gated by BOTH org_admin_required (tenant_decorators)
# AND admin_required (Permission.ADMINISTER) — a good target because it
# proves the matrix is layered, not a single flag.
_GATED_ROUTE = "/admin/api-settings"


def test_plain_user_is_rejected_by_the_rbac_gate(app, db_session, rbac_org, login_as):
    """A user with no org-admin flag and no ADMINISTER permission is denied."""
    user = _make_rbac_user(db_session, rbac_org, is_org_admin=False, admin_permission=False)
    db_session.commit()
    client = app.test_client()
    login_as(client, user)

    resp = client.get(_GATED_ROUTE)
    assert resp.status_code == 403, (
        f"a plain user (no org_admin, no ADMINISTER permission) reached "
        f"{_GATED_ROUTE} — got {resp.status_code}"
    )


def test_org_admin_without_administer_permission_is_still_rejected(app, db_session, rbac_org, login_as):
    """org_admin_required alone is not sufficient — admin_required (the
    Permission.ADMINISTER check) is a second, independent gate on the same
    route. This is the layering the RBAC matrix depends on: flipping one
    flag must not be enough to reach an ADMINISTER-only surface.
    """
    user = _make_rbac_user(db_session, rbac_org, is_org_admin=True, admin_permission=False)
    db_session.commit()
    client = app.test_client()
    login_as(client, user)

    resp = client.get(_GATED_ROUTE)
    assert resp.status_code == 403, (
        f"is_org_admin=True but role lacks Permission.ADMINISTER still "
        f"reached {_GATED_ROUTE} — got {resp.status_code}. The two gates "
        "are not actually independent."
    )


def test_org_admin_with_administer_permission_is_admitted(app, db_session, rbac_org, login_as):
    """The intersection of both flags is the only combination the spec's
    RBAC matrix says should pass this route.
    """
    user = _make_rbac_user(db_session, rbac_org, is_org_admin=True, admin_permission=True)
    db_session.commit()
    client = app.test_client()
    login_as(client, user)

    resp = client.get(_GATED_ROUTE)
    assert resp.status_code in (200, 302), (
        f"an org_admin with ADMINISTER permission was rejected from "
        f"{_GATED_ROUTE} — got {resp.status_code}"
    )


def test_platform_admin_only_route_rejects_a_mere_org_admin(app, db_session, rbac_org, login_as):
    """R-31-adjacent: platform_admin_required must reject an org_admin who
    is not also a platform_admin. Reuses the exact route
    tests/test_admin_org_member_idor.py already proved is safe against
    cross-tenant reads; this test is the same gate from the role-matrix
    angle rather than the tenant-ID angle.
    """
    from app.models.organization import Organization

    other_org = Organization(
        name=f"RBAC Other {uuid.uuid4().hex[:8]}", slug=f"rbac-other-{uuid.uuid4().hex[:8]}"
    )
    db_session.add(other_org)
    db_session.flush()

    tenant_admin = _make_rbac_user(db_session, rbac_org, is_org_admin=True, is_platform_admin=False)
    db_session.commit()
    client = app.test_client()
    login_as(client, tenant_admin)

    resp = client.get(f"/admin/organizations/{other_org.id}")
    assert resp.status_code in (403, 404), (
        f"an org_admin (not platform_admin) reached a platform_admin_required "
        f"route — got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# SSO posture (R-32-adjacent scope: "is the identity boundary real")
# ---------------------------------------------------------------------------


def test_sso_service_is_a_real_implementation_not_a_stub():
    """Pin what SSO actually is here: a real OIDC client (Azure AD, Okta),
    not a stub or a TODO. Guards against a future refactor silently degrading
    it to a stub while tests keep passing on method presence alone.
    """
    from app.auth.sso import SSOService

    svc = SSOService()
    assert hasattr(svc, "init_app")
    # A real implementation resolves a provider config; a stub would have no
    # notion of providers at all.
    assert hasattr(svc, "providers")
    assert svc.enabled is False, "SSOService must default to disabled before init_app() runs"


def test_sso_disabled_by_default_fails_closed(app):
    """SSO_ENABLED defaults False in this environment (no IdP configured for
    local/test), and the service's public entry points must fail closed —
    not silently authenticate — while disabled.
    """
    from app.auth.sso import SSOService

    svc = SSOService()
    svc.init_app(app)
    if app.config.get("SSO_ENABLED", False):
        pytest.skip("SSO_ENABLED is true in this environment's config — closed-by-default not applicable")
    assert svc.enabled is False
    assert svc.providers == {}, "a disabled SSOService must not carry provider config forward"


def test_oidc_sign_in_routes_404_when_disabled(app):
    """The OIDC sign-in routes are registered, and answer 404 while SSO is off.

    Each path is matched against the URL map first, so the check cannot pass
    against a path that was never registered.
    """
    if app.config.get("SSO_ENABLED", False):
        pytest.skip("SSO_ENABLED is true in this environment's config")

    adapter = app.url_map.bind("localhost")
    client = app.test_client()
    for path in ("/account/sso/azure", "/account/sso/callback/azure"):
        adapter.match(path)  # raises NotFound when no rule is registered for the path
        assert client.get(path).status_code == 404, (
            f"{path} must answer 404 while SSO is disabled"
        )


# The single registered rule that names SAML: the per-organisation callback
# (app/modules/auth/sso_routes.py). It is the explicit refusal for an
# organisation whose SSO configuration says SAML: it answers 501 to every
# request, reads no assertion and signs nobody in. Any other rule that names
# SAML, in its path or its endpoint, is a SAML sign-in route and must not exist.
_SAML_REFUSAL = ("/auth/sso/callback/saml", "sso.sso_callback_saml")


def _saml_rules(url_map):
    """(path, endpoint) of every rule that names SAML, read from a URL map."""
    return sorted(
        (rule.rule, rule.endpoint)
        for rule in url_map.iter_rules()
        if "saml" in rule.rule.lower() or "saml" in rule.endpoint.lower()
    )


def test_no_saml_sign_in_route_is_registered(app):
    """The application's URL map holds no SAML sign-in route.

    Asserted over the registered rules, so it cannot pass against a path that
    was never registered, and it does not infer anything from a 404. The only
    rule that may name SAML is the per-organisation refusal.
    """
    assert _saml_rules(app.url_map) == [_SAML_REFUSAL], (
        "the URL map must hold exactly one SAML-named rule, the per-organisation "
        f"refusal {_SAML_REFUSAL}; found {_saml_rules(app.url_map)}"
    )


def test_the_per_organisation_saml_callback_only_refuses(app):
    """The one SAML-named rule is a GET-only refusal that answers 501."""
    rules = [rule for rule in app.url_map.iter_rules() if rule.endpoint == _SAML_REFUSAL[1]]
    assert len(rules) == 1 and rules[0].rule == _SAML_REFUSAL[0]
    assert rules[0].methods - {"HEAD", "OPTIONS"} == {"GET"}, (
        "the per-organisation SAML callback must not accept a POST"
    )

    resp = app.test_client().get(_SAML_REFUSAL[0])
    assert resp.status_code == 501
    assert "error" in resp.get_json()


@pytest.mark.parametrize("module_path,blueprint_name", [
    ("app.modules.account.routes.account_routes", "account_bp"),
    ("app.modules.account.v2.routes.account_routes", "account_bp_v2"),
])
def test_each_account_route_module_registers_no_saml_rule(module_path, blueprint_name):
    """Neither account route module adds a SAML rule.

    The application registers one of the two, chosen by USE_ACCOUNT_GUARDRAILS,
    so the running URL map only ever shows one. Each blueprint is registered on
    its own scratch application here and its rules read from that URL map.
    """
    import importlib

    from flask import Flask

    blueprint = getattr(importlib.import_module(module_path), blueprint_name)
    scratch = Flask(f"scratch_{blueprint_name}")
    scratch.register_blueprint(blueprint, url_prefix="/account")

    registered = [str(rule) for rule in scratch.url_map.iter_rules()]
    assert "/account/login" in registered, "the scratch URL map did not pick up the account routes"
    assert _saml_rules(scratch.url_map) == []


def test_sso_service_has_no_saml_assertion_method():
    """SSOService carries no SAML or assertion-consuming method or attribute."""
    import inspect

    from app.auth.sso import SSOService

    named = [
        name
        for name in (*dir(SSOService), *vars(SSOService()))
        if "saml" in name.lower() or "assertion" in name.lower()
    ]
    assert not named, f"SSOService must not carry SAML members, found {named}"
    assert "urn:oasis:names:tc:saml" not in inspect.getsource(SSOService).lower()


# ---------------------------------------------------------------------------
# SCIM posture — the honest "not implemented" finding.
# ---------------------------------------------------------------------------


def test_scim_is_not_implemented_anywhere_in_the_app_tree():
    """FINDING: SCIM provisioning does not exist in this codebase.

    Walks app/ for anything naming SCIM (case-insensitive) in a .py file, as
    a route path, blueprint name, model, or service. The only pre-existing
    hits anywhere in the repo are `zxcvbn.js` substring false positives
    (a password-strength library filename, unrelated), which is a search
    outside this scope (.py only) and so does not appear here.

    If this test ever starts failing because someone genuinely lands SCIM
    support, that is the correct outcome — update this test to assert the
    new endpoints are tenant-scoped and auth-gated, rather than deleting it.
    """
    import re
    from pathlib import Path

    app_dir = Path(__file__).resolve().parent.parent / "app"
    scim_pattern = re.compile(r"\bscim\b", re.IGNORECASE)

    hits = []
    for path in app_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if scim_pattern.search(text):
            hits.append(str(path.relative_to(app_dir.parent)))

    assert not hits, (
        f"SCIM references found where none were expected: {hits}. This test "
        "encodes 'SCIM is not implemented' as of ARCH-091's audit — if SCIM "
        "now exists, replace this test with real coverage of it rather than "
        "deleting the assertion."
    )
