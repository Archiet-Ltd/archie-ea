"""The four initiative-creating routes under /strategic/api/initiatives/ used to
carry only @login_required, so any signed-in member of any organisation could
create a StrategicInitiative. They now reuse can_edit_capabilities
(app.utils.role_access) -- the existing rule for "who may author the
capability/architecture model" -- as an authorisation check: a member outside
that rule gets 403, a member inside it succeeds, and both hold independently
in two separate organisations (tenant isolation, not a single global switch).
"""

import uuid

import pytest

ROUTES = (
    "/strategic/api/initiatives/from-health",
    "/strategic/api/initiatives/from-investment",
    "/strategic/api/initiatives/from-risk",
    "/strategic/api/initiatives/from-impact",
)


@pytest.fixture(scope="module")
def app():
    from app import create_app, db

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        db.create_all()

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_org_id(db, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"{label} Org {suffix}", slug=f"{label.lower()}-org-{suffix}")
    db.session.add(org)
    db.session.flush()
    db.session.commit()
    return org.id


def _make_user_id(db, org_id, label, enterprise_role):
    """A real User row pinned to *org_id*, with no Administrator role and no
    is_admin()/is_org_admin flag set, so the only thing that can admit it to
    the gated routes is can_edit_capabilities' enterprise_role check."""
    suffix = uuid.uuid4().hex[:8]
    from app.models.user import User

    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=enterprise_role,
    )
    db.session.add(user)
    db.session.flush()
    db.session.commit()
    return user.id


def _login(client, user_id):
    """Switch the test client to *user_id*. See tests/test_ba_tenant_and_authz.py's
    ``_login`` for why both the session cookie AND Flask-Login/tenant-context g
    caches must be cleared: pytest-flask's shared request context otherwise
    keeps the previous request's resolved user and organisation."""
    from tests._session_test_helpers import mint_test_sid

    _sid = mint_test_sid(user_id, app=client.application)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        sess["_sid"] = _sid

    from flask import g, has_app_context

    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


@pytest.fixture
def two_orgs(app):
    from app import db

    with app.app_context():
        org_a = _make_org_id(db, "RoleGateA")
        org_b = _make_org_id(db, "RoleGateB")

        # Outside can_edit_capabilities' CAPABILITY_EDITOR_ROLES set.
        outsider_a = _make_user_id(db, org_a, "OutsiderA", enterprise_role="procurement")
        outsider_b = _make_user_id(db, org_b, "OutsiderB", enterprise_role="procurement")
        # Inside it (CTO is one of the roles can_edit_capabilities admits).
        planner_a = _make_user_id(db, org_a, "PlannerA", enterprise_role="cto")
        planner_b = _make_user_id(db, org_b, "PlannerB", enterprise_role="cto")

    return {
        "org_a": org_a, "org_b": org_b,
        "outsider_a": outsider_a, "outsider_b": outsider_b,
        "planner_a": planner_a, "planner_b": planner_b,
    }


@pytest.mark.parametrize("route", ROUTES)
def test_member_outside_the_role_is_forbidden_in_org_a(client, two_orgs, route):
    _login(client, two_orgs["outsider_a"])
    resp = client.post(route, json={"name": "Attempted initiative"})
    assert resp.status_code == 403, (route, resp.status_code, resp.get_json())
    body = resp.get_json()
    assert body is not None and body.get("success") is False


@pytest.mark.parametrize("route", ROUTES)
def test_member_outside_the_role_is_forbidden_in_org_b(client, two_orgs, route):
    """The same rule holds in a second, unrelated organisation -- not a single
    global switch that happened to be flipped for org A."""
    _login(client, two_orgs["outsider_b"])
    resp = client.post(route, json={"name": "Attempted initiative"})
    assert resp.status_code == 403, (route, resp.status_code, resp.get_json())
    body = resp.get_json()
    assert body is not None and body.get("success") is False


@pytest.mark.parametrize("route", ROUTES)
def test_member_inside_the_role_succeeds_in_org_a(client, two_orgs, route):
    _login(client, two_orgs["planner_a"])
    resp = client.post(route, json={"name": "Org A initiative via %s" % route})
    assert resp.status_code == 200, (route, resp.status_code, resp.get_json())
    body = resp.get_json()
    assert body is not None and body.get("success") is True


@pytest.mark.parametrize("route", ROUTES)
def test_member_inside_the_role_succeeds_in_org_b(client, two_orgs, route):
    """The same rule admits the right role in a second, unrelated organisation."""
    _login(client, two_orgs["planner_b"])
    resp = client.post(route, json={"name": "Org B initiative via %s" % route})
    assert resp.status_code == 200, (route, resp.status_code, resp.get_json())
    body = resp.get_json()
    assert body is not None and body.get("success") is True
