"""ScoringConfiguration carries no organization_id -- it is a platform-wide
table (scope_type/scope_entity_id are a business-unit label, not a tenant
fence) -- and create/update/delete were gated only by @login_required, so
any signed-in user from any organisation could create a configuration with
is_default=True (silently unsetting every other configuration's default,
platform-wide) or edit/delete an existing one. Now platform-admin-only,
matching the write-gating already applied to the other shared config
tables (feature flags, persona prompts, sidebar/editor content, vendor
pricing).

app.modules.dashboard.v2 is the live module (USE_DASHBOARD_GUARDRAILS
defaults ON, confirmed by reading app/_bootstrap/blueprints.py's
_register_dashboard before writing this fix) -- these tests exercise it.
"""
from __future__ import annotations

import uuid

import pytest


def _user(db_session, org, *, platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(email=f"sc-{uuid.uuid4().hex[:6]}@example.test", first_name="Scoring", last_name="Tester",
                organization_id=org.id, confirmed=True, role=role)
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


def _world(db_session, make_org):
    # Two organisations: the platform admin sits in org A (the configuration's
    # creator), the refused tenant admin sits in org B -- so a pass here cannot
    # be explained by same-org membership, only by the is_platform_admin gate.
    org_a = make_org("scoring-config-a")
    org_b = make_org("scoring-config-b")
    tenant = _user(db_session, org_b)
    platform = _user(db_session, org_a, platform=True)
    db_session.commit()
    return tenant.id, platform.id


_VALID_PAYLOAD = {
    "name": "Test configuration",
    "technical_health_weight": 30,
    "business_value_weight": 35,
    "cost_efficiency_weight": 25,
    "vendor_risk_weight": 10,
}


def test_a_tenant_administrator_cannot_create_a_scoring_configuration(app, db_session, make_org, client, login_as):
    tenant_id, _platform = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_id)
    response = client.post("/dashboard/api/scoring-configurations", json=_VALID_PAYLOAD)

    assert response.status_code == 403


def test_a_tenant_administrator_cannot_set_a_new_platform_default(app, db_session, make_org, client, login_as):
    """The real-world exploit shape: is_default=True on a new row silently
    unsets every other row's default, platform-wide."""
    from app.models.application_rationalization import ScoringConfiguration

    tenant_id, _platform = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_id)
    response = client.post("/dashboard/api/scoring-configurations",
                            json={**_VALID_PAYLOAD, "is_default": True})

    assert response.status_code == 403
    assert ScoringConfiguration.query.filter_by(name="Test configuration").first() is None


def test_a_tenant_administrator_cannot_update_or_delete_a_scoring_configuration(
    app, db_session, make_org, client, login_as
):
    from app.models.application_rationalization import ScoringConfiguration

    tenant_id, platform_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_id)
    created = client.post("/dashboard/api/scoring-configurations", json=_VALID_PAYLOAD)
    assert created.status_code == 201
    config_id = created.get_json()["data"]["id"]

    _login(db_session, client, login_as, tenant_id)
    updated = client.put(f"/dashboard/api/scoring-configurations/{config_id}",
                          json={"name": "Tampered"})
    deleted = client.delete(f"/dashboard/api/scoring-configurations/{config_id}")

    assert updated.status_code == 403
    assert deleted.status_code == 403
    still = db_session.get(ScoringConfiguration, config_id)
    assert still.name == "Test configuration"
    assert still.is_active is True


def test_a_platform_administrator_can_still_manage_scoring_configurations(
    app, db_session, make_org, client, login_as
):
    _tenant, platform_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_id)
    created = client.post("/dashboard/api/scoring-configurations", json=_VALID_PAYLOAD)
    assert created.status_code == 201
    config_id = created.get_json()["data"]["id"]

    updated = client.put(f"/dashboard/api/scoring-configurations/{config_id}",
                          json={"name": "Renamed"})
    assert updated.status_code == 200

    deleted = client.delete(f"/dashboard/api/scoring-configurations/{config_id}")
    assert deleted.status_code == 200


def test_the_services_defence_in_depth_refusal_stays_a_403_not_a_500(
    app, db_session, make_org, client, login_as, monkeypatch
):
    """pr324-review-v1 nit 1: scoring_configuration_service._require_platform_admin
    raises Forbidden (an HTTPException) when a caller reaches it without the
    route's own @platform_admin_required having already refused them. Before
    this fix, the route's bare `except Exception` caught that Forbidden too,
    turning a would-be 403 into a logged 500 with a rollback. Simulated here by
    making the service itself raise Forbidden -- the same path the real guard
    takes, not by removing the route's decorator (the platform admin used below
    would legitimately pass that decorator; the service is what refuses them)."""
    from werkzeug.exceptions import Forbidden

    from app.services import scoring_configuration_service

    _tenant, platform_id = _world(db_session, make_org)

    def _always_forbidden(*args, **kwargs):
        raise Forbidden()

    monkeypatch.setattr(
        scoring_configuration_service, "create_scoring_configuration", _always_forbidden
    )

    _login(db_session, client, login_as, platform_id)
    response = client.post("/dashboard/api/scoring-configurations", json=_VALID_PAYLOAD)

    assert response.status_code == 403