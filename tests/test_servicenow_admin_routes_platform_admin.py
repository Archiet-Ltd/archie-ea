"""The ServiceNow CMDB connector is platform-wide configuration: only a platform admin may read
or change it.

``ExternalSystem`` carries no tenant column -- one ServiceNow connector config serves every
organisation (same model as Jira and Abacus, see the module docstring on
``tests/test_jira_admin_routes_platform_admin.py``). The v1 ServiceNow settings, test-connection,
trigger-sync and sync-status routes were guarded by ``@admin_required`` (``Permission.ADMINISTER``),
which any organisation's own administrator role holds (pr312-final-check-v3.md HIGH 2). A tenant
administrator could read or change platform-wide ServiceNow credentials, or trigger a sync that
touches every tenant's CMDB data.

These routes exist only in ``app/modules/admin/routes/admin_routes.py``, the v1 modular tree --
``app/modules/admin/v2/routes/admin_routes.py`` has no ServiceNow routes to fix. That tree is not
registered in this test environment (confirmed: every URL-routed call 404s here, not 403), the
same situation ``test_abacus_admin_routes_platform_admin.py``'s ``save_relationship_mappings``
direct-handler test exists to work around -- these tests import and call the view functions
directly, independent of blueprint registration.
"""

from __future__ import annotations

import uuid

import pytest


def _user(db_session, org, *, role_name="Administrator", platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name=role_name).first()
    if role is None:
        pytest.skip("no %s role seeded in this database" % role_name)
    user = User(email=f"snow-{uuid.uuid4().hex[:6]}@example.test", first_name="ServiceNow", last_name="Tester",
                organization_id=org.id, confirmed=True, role=role)
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _world(db_session, make_org):
    org_a = make_org("servicenow-a")
    org_b = make_org("servicenow-b")
    tenant_admin_a = _user(db_session, org_a)
    tenant_admin_b = _user(db_session, org_b)
    platform_admin = _user(db_session, org_a, platform=True)
    db_session.commit()
    return tenant_admin_a.id, tenant_admin_b.id, platform_admin.id


def _call_view(app, db_session, user_id, view, method="POST", path="/"):
    from flask_login import login_user

    from app.models.user import User

    db_session.expunge_all()
    user = db_session.get(User, user_id)

    with app.test_request_context(path, method=method, content_type="application/json", json={}):
        login_user(user)
        return view()


def _status(response):
    # A bare string/HTML body (render_template's own return value, the GET
    # success path) carries Flask's implicit 200 -- only a (body, status)
    # tuple or an actual Response object names its own status explicitly.
    if isinstance(response, tuple):
        return response[1]
    if isinstance(response, str):
        return 200
    return response.status_code


@pytest.mark.parametrize("method,view_name", [
    ("GET", "servicenow_integration"),
    ("POST", "servicenow_integration"),
    ("POST", "servicenow_test_connection"),
    ("POST", "servicenow_trigger_sync"),
    ("GET", "servicenow_sync_status"),
])
def test_a_tenant_administrator_is_refused_on_every_servicenow_handler(
    app, db_session, make_org, method, view_name
):
    from app.modules.admin.routes import admin_routes

    view = getattr(admin_routes, view_name)
    tenant_admin_a_id, tenant_admin_b_id, _platform_id = _world(db_session, make_org)

    for admin_id in (tenant_admin_a_id, tenant_admin_b_id):
        response = _call_view(app, db_session, admin_id, view, method=method)
        assert _status(response) == 403


def test_a_platform_administrator_can_still_reach_the_servicenow_settings_handler(
    app, db_session, make_org
):
    from app.modules.admin.routes import admin_routes

    _tenant_a_id, _tenant_b_id, platform_admin_id = _world(db_session, make_org)

    response = _call_view(
        app, db_session, platform_admin_id, admin_routes.servicenow_integration, method="GET"
    )

    assert _status(response) == 200
