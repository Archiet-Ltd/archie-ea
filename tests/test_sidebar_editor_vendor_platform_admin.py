"""Shared configuration written from the admin console is platform-only.

The sidebar menu items (``SidebarMenuItem``), the inline-editor content (``EditableHTML``) and the shared
vendor catalogue (``VendorProduct``) have no tenant column: one row serves every organisation. Their write
routes were guarded by ``@admin_required`` (``Permission.ADMINISTER``), which every organisation's own
Administrator role holds, so a tenant administrator could reset the sidebar, overwrite editor content or
confirm vendor pricing for all tenants. They now use ``@platform_admin_required``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text


def _user(db_session, org, *, platform=False):
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(email=f"sev-{uuid.uuid4().hex[:6]}@example.test", first_name="Shared", last_name="Tester",
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
    org = make_org("shared-config")
    tenant, platform = _user(db_session, org), _user(db_session, org, platform=True)
    db_session.commit()
    return tenant.id, platform.id


WRITES = [
    "/api/admin/sidebar/items/reset",
    "/api/admin/sidebar/items/section/home" "/toggle",
    "/api/admin/sidebar/items/1/toggle",
    "/admin/vendor-pricing/confirm",
]


@pytest.mark.parametrize("path", WRITES)
def test_a_tenant_administrator_is_refused_on_every_shared_write(app, db_session, make_org, client, login_as, path):
    tenant_id, _platform = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_id)
    response = client.post(path, data={"editor_name": "probe", "edit_data": "<b>x</b>"})

    assert response.status_code == 403


RETIRED_EDITOR_SAVE = "/admin/_update_editor_contents"


def test_the_retired_editor_save_address_is_gone_for_a_tenant_administrator(app, db_session, make_org, client, login_as):
    tenant_id, _platform = _world(db_session, make_org)
    name = "probe-" + uuid.uuid4().hex[:8]

    _login(db_session, client, login_as, tenant_id)
    response = client.post(RETIRED_EDITOR_SAVE, data={"editor_name": name, "edit_data": "<b>x</b>"})

    assert response.status_code in (404, 405)
    assert db_session.execute(text("select count(*) from editable_html where editor_name = :n"), {"n": name}).scalar() == 0


def test_the_retired_editor_save_address_is_gone_for_a_platform_administrator(app, db_session, make_org, client, login_as):
    _tenant, platform_id = _world(db_session, make_org)
    name = "probe-" + uuid.uuid4().hex[:8]

    _login(db_session, client, login_as, platform_id)
    response = client.post(RETIRED_EDITOR_SAVE, data={"editor_name": name, "edit_data": "<b>ok</b>"})
    reset = client.post("/api/admin/sidebar/items/reset")

    assert response.status_code in (404, 405)
    assert db_session.execute(text("select count(*) from editable_html where editor_name = :n"), {"n": name}).scalar() == 0
    assert reset.status_code == 200


@pytest.mark.parametrize("module", ["app.modules.admin.routes.admin_routes", "app.modules.admin.v2.routes.admin_routes"])
def test_neither_admin_blueprint_serves_the_retired_editor_save_address(module):
    import importlib
    import inspect

    source = inspect.getsource(importlib.import_module(module))

    assert "_update_editor_contents" not in source
    assert not hasattr(importlib.import_module(module), "update_editor_contents")
