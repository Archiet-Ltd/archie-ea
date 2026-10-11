"""Framework configuration and management routes require platform admin.

The tables behind these surfaces (CapabilityFrameworkConfiguration,
FrameworkInstance, FrameworkExtension, FrameworkConfigurationTemplate,
FrameworkMigrationMapping) have no organisation column, so every customer
shares them. Before this fix every route required only @login_required.
"""

import pytest


def _make_architect(db_session, org):
    """A confirmed user with the default Architect role, NOT a platform admin."""
    from app.models.user import Role, User

    user = User(
        email=f"architect-{org.id}@framework.test",
        organization_id=org.id,
        enterprise_role="enterprise_architect",
        confirmed=True,
        is_platform_admin=False,
    )
    db_session.add(user)
    db_session.flush()

    Role.insert_roles()
    role = Role.query.filter_by(name="Architect").first()
    user.role = role
    db_session.flush()
    return user


def _make_platform_admin(db_session, org):
    """A confirmed user with is_platform_admin=True AND the Administrator role."""
    from app.models.user import Role, User

    user = User(
        email=f"platform-admin-{org.id}@framework.test",
        organization_id=org.id,
        enterprise_role="platform_admin",
        confirmed=True,
        is_platform_admin=True,
    )
    db_session.add(user)
    db_session.flush()

    Role.insert_roles()
    role = Role.query.filter_by(name="Administrator").first()
    user.role = role
    db_session.flush()
    return user


def _make_viewer(db_session, org):
    """A confirmed user with the Viewer role (no permissions)."""
    from app.models.user import Role, User

    user = User(
        email=f"viewer-{org.id}@framework.test",
        organization_id=org.id,
        enterprise_role="viewer",
        confirmed=True,
        is_platform_admin=False,
    )
    db_session.add(user)
    db_session.flush()

    Role.insert_roles()
    role = Role.query.filter_by(name="Viewer").first()
    user.role = role
    db_session.flush()
    return user


# ---------------------------------------------------------------------------
# Architect (not platform admin) — every framework route must 403
# ---------------------------------------------------------------------------


def test_architect_rejected_framework_config_ui(app, db_session, make_org, client, login_as):
    """GET /framework-config/ returns 403 for an Architect who is not a platform admin."""
    org = make_org("fw-arch-ui")
    user = _make_architect(db_session, org)
    login_as(client, user)
    resp = client.get("/framework-config/")
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}"


def test_architect_rejected_api_configurations_get(app, db_session, make_org, client, login_as):
    """GET /api/framework-config/configurations returns 403 for an Architect."""
    org = make_org("fw-arch-api-get")
    user = _make_architect(db_session, org)
    login_as(client, user)
    resp = client.get("/api/framework-config/configurations")
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}"


def test_architect_rejected_api_configurations_post(app, db_session, make_org, client, login_as):
    """POST /api/framework-config/configurations returns 403 for an Architect."""
    org = make_org("fw-arch-api-post")
    user = _make_architect(db_session, org)
    login_as(client, user)
    resp = client.post(
        "/api/framework-config/configurations",
        json={"configuration_name": "Test", "configuration_code": "TEST"},
    )
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}"


def test_architect_rejected_bulk_delete_instances(app, db_session, make_org, client, login_as):
    """DELETE /framework-config/api/instances/bulk returns 403 for an Architect."""
    org = make_org("fw-arch-bulk")
    user = _make_architect(db_session, org)
    login_as(client, user)
    resp = client.delete(
        "/framework-config/api/instances/bulk",
        json={"ids": [1]},
    )
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}"


def test_architect_rejected_framework_management(app, db_session, make_org, client, login_as):
    """GET /framework-management/ returns 403 for an Architect."""
    org = make_org("fw-arch-mgmt")
    user = _make_architect(db_session, org)
    login_as(client, user)
    resp = client.get("/framework-management/")
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}"


# ---------------------------------------------------------------------------
# Platform admin — GETs must return 200
# ---------------------------------------------------------------------------


def test_platform_admin_allowed_framework_config_ui(app, db_session, make_org, client, login_as):
    """GET /framework-config/ returns 200 for a platform admin."""
    org = make_org("fw-pa-ui")
    user = _make_platform_admin(db_session, org)
    login_as(client, user)
    resp = client.get("/framework-config/")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"


def test_platform_admin_allowed_api_configurations_get(app, db_session, make_org, client, login_as):
    """GET /api/framework-config/configurations returns 200 for a platform admin."""
    org = make_org("fw-pa-api")
    user = _make_platform_admin(db_session, org)
    login_as(client, user)
    resp = client.get("/api/framework-config/configurations")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"


# ---------------------------------------------------------------------------
# Viewer — compliance mapping POST must 403
# ---------------------------------------------------------------------------


def test_viewer_rejected_compliance_map_post(app, db_session, make_org, client, login_as):
    """POST /api/applications/<id>/compliance/map returns 403 for a Viewer."""
    org = make_org("fw-viewer-comp")
    user = _make_viewer(db_session, org)
    login_as(client, user)
    resp = client.post(
        "/dashboard/api/applications/nonexistent-app/compliance/map",
        json={"control_id": 1},
    )
    assert resp.status_code == 403, f"expected 403, got {resp.status_code}"