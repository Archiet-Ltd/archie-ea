"""Roles and the platform's AI prompt overrides are shared by every organisation: only a platform admin may change them.

``Role`` has no tenant column (names are unique across organisations) and the solution-prompt
override is one name-keyed row used for every organisation, so an organisation's own
administrator could create, rename or delete roles other organisations' users hold, and
rewrite the platform's AI system prompt for all tenants. The routes were guarded by
``@admin_required`` (``Permission.ADMINISTER``); they now use ``@platform_admin_required``
on every verb, including list -- no template calls this endpoint, so there is no
team-management need to balance against the leak.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests._platform_admin_world import (
    login_platform_admin_world_user as _login,
    platform_admin_user as _user,
)


def _world(db_session, make_org):
    from app.models import Role

    org = make_org("roles-prompts")
    tenant = _user(db_session, org, prefix="rp")
    platform = _user(db_session, org, prefix="rp", platform=True)
    custom = Role(name=f"probe-{uuid.uuid4().hex[:8]}", permissions=1, index="main", default=False)
    db_session.add(custom)
    db_session.commit()
    return tenant.id, platform.id, custom.id, custom.name


def _prompt_key():
    from app.modules.admin.v2.routes.admin_routes import _get_prompt_defaults

    return sorted(_get_prompt_defaults())[0]


def test_a_tenant_administrator_cannot_create_rename_or_delete_a_role(app, db_session, make_org, client, login_as):
    tenant_id, _platform, role_id, role_name = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_id)
    created = client.post("/admin/api/roles", json={"name": f"new-{uuid.uuid4().hex[:6]}"})
    renamed = client.put(f"/admin/api/roles/{role_id}", json={"name": role_name + "-x"})
    deleted = client.delete(f"/admin/api/roles/{role_id}")

    assert (created.status_code, renamed.status_code, deleted.status_code) == (403, 403, 403)
    assert db_session.execute(text("select name from roles where id = :i"), {"i": role_id}).scalar() == role_name


def test_a_tenant_administrator_cannot_list_roles(app, db_session, make_org, client, login_as):
    """Matches tests/test_admin_org_member_idor.py's
    TestRolesApiPlatformAdminOnly (pre-existing on main, independent of
    this PR): list is platform_admin-only like every other /api/roles
    verb -- no UI depends on an org admin reaching it."""
    tenant_id, *_ = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_id)

    assert client.get("/admin/api/roles").status_code == 403


@pytest.mark.parametrize("method,path", [
    ("post", "/admin/solution-prompts/{key}/update"),
    ("post", "/admin/solution-prompts/{key}/reset"),
    ("post", "/admin/solution-prompts/{key}/rollback/1"),
    ("get", "/admin/solution-prompts/{key}/history"),
])
def test_a_tenant_administrator_cannot_touch_the_platform_prompts(app, db_session, make_org, client, login_as, method, path):
    tenant_id, *_ = _world(db_session, make_org)

    _login(db_session, client, login_as, tenant_id)
    response = getattr(client, method)(path.format(key=_prompt_key()), json={"prompt_text": "OVERRIDE"})

    assert response.status_code == 403
    assert db_session.execute(text("select count(*) from ai_prompt_templates where system_prompt = 'OVERRIDE'")).scalar() == 0


def test_a_platform_administrator_can_still_manage_roles_and_prompts(app, db_session, make_org, client, login_as):
    _tenant, platform_id, role_id, role_name = _world(db_session, make_org)

    _login(db_session, client, login_as, platform_id)
    renamed = client.put(f"/admin/api/roles/{role_id}", json={"name": role_name + "-ok"})
    prompt = client.post(f"/admin/solution-prompts/{_prompt_key()}/update", json={"prompt_text": "PLATFORM OVERRIDE"})
    client.post(f"/admin/solution-prompts/{_prompt_key()}/reset")

    assert renamed.status_code == 200 and prompt.status_code == 200
