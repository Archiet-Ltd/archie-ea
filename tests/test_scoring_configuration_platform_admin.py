"""Rationalization scoring weights are platform-wide configuration: only a platform admin may change them.

``ScoringConfiguration`` (``app/models/application_rationalization.py``) has no organisation
column, and ``RationalizationScoringService.get_scoring_configuration`` (the only reader,
``app/services/rationalization_scoring_service.py``) never takes an organisation either — it
resolves a "global" or "default" row with no tenant concept at all. The configuration is
therefore genuinely platform-wide, the same shape as the feature-flag and roles/prompts
findings fixed in PRs #241 and #242: the fix there was to gate writes with
``@platform_admin_required`` rather than invent a tenant column for a store nothing reads
per-tenant.

Before the fix, the create/update/delete routes at ``POST/PUT/DELETE
/dashboard/api/scoring-configurations`` (mounted three times in this repo:
``app/api/dashboard_routes.py``, ``app/modules/dashboard/routes/dashboard_pages_routes.py`` and
the canonical ``app/modules/dashboard/v2/routes/dashboard_pages_routes.py``) carried no
authorisation check beyond ``@login_required`` — not even ``@admin_required`` — so any
authenticated user of any organisation could change the weights that drive every other
organisation's rationalization scores, or delete another organisation's chosen default.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text


def _user(db_session, org, *, platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user = User(
        email=f"sc-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Scoring",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        role=role,
    )
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


def _config(db_session):
    from app.models.application_rationalization import ScoringConfiguration

    config = ScoringConfiguration(
        name=f"probe-{uuid.uuid4().hex[:8]}",
        scope_type="global",
        technical_health_weight=30,
        business_value_weight=35,
        cost_efficiency_weight=25,
        vendor_risk_weight=10,
        is_default=False,
    )
    db_session.add(config)
    db_session.flush()
    return config


def _world(db_session, make_org):
    org = make_org("scoring")
    tenant_admin = _user(db_session, org)
    platform_admin = _user(db_session, org, platform=True)
    config = _config(db_session)
    db_session.commit()
    return tenant_admin.id, platform_admin.id, config.id


def _weight(db_session, config_id):
    return db_session.execute(
        text("select technical_health_weight from scoring_configurations where id = :i"),
        {"i": config_id},
    ).scalar()


def test_a_tenant_administrator_cannot_change_the_platform_wide_scoring_weights(
    app, db_session, make_org, client, login_as
):
    tenant_admin_id, _platform_id, config_id = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_admin_id)
    response = client.put(
        f"/dashboard/api/scoring-configurations/{config_id}",
        json={"technical_health_weight": 99},
    )

    assert response.status_code == 403
    assert _weight(db_session, config_id) == 30


def test_a_tenant_administrator_cannot_delete_the_platform_wide_scoring_configuration(
    app, db_session, make_org, client, login_as
):
    tenant_admin_id, _platform_id, config_id = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_admin_id)
    response = client.delete(f"/dashboard/api/scoring-configurations/{config_id}")

    assert response.status_code == 403
    assert (
        db_session.execute(
            text("select is_active from scoring_configurations where id = :i"), {"i": config_id}
        ).scalar()
        is True
    )


def test_a_tenant_administrator_cannot_create_a_scoring_configuration(
    app, db_session, make_org, client, login_as
):
    tenant_admin_id, _platform_id, _config_id = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_admin_id)
    response = client.post(
        "/dashboard/api/scoring-configurations",
        json={
            "name": "attacker config",
            "technical_health_weight": 40,
            "business_value_weight": 30,
            "cost_efficiency_weight": 20,
            "vendor_risk_weight": 10,
            "is_default": True,
        },
    )

    assert response.status_code == 403
    assert (
        db_session.execute(
            text("select count(*) from scoring_configurations where name = 'attacker config'")
        ).scalar()
        == 0
    )


def test_reading_scoring_configurations_still_works_for_a_tenant_administrator(
    app, db_session, make_org, client, login_as
):
    tenant_admin_id, _platform_id, config_id = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_admin_id)
    response = client.get("/dashboard/api/scoring-configurations")

    assert response.status_code == 200


def test_a_platform_administrator_can_still_change_the_scoring_weights(
    app, db_session, make_org, client, login_as
):
    _tenant_id, platform_admin_id, config_id = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_admin_id)
    response = client.put(
        f"/dashboard/api/scoring-configurations/{config_id}",
        json={"technical_health_weight": 40, "business_value_weight": 25},
    )

    assert response.status_code == 200
    assert _weight(db_session, config_id) == 40
