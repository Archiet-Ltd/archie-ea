"""A stored persona never widens what the roles allow.

Accounts created before ``platform_admin`` stopped being the stored default still
store it. With no roles and no platform-admin flag they must see nothing a
platform administrator sees.
"""

from __future__ import annotations

import uuid

import pytest

from app.decorators.requires_role import may_open_spend, may_see_spend_pages
from app.middleware.tenant_decorators import is_platform_admin
from app.utils.role_access import can_see_costs, get_effective_role, get_user_role

pytestmark = pytest.mark.usefixtures("db_session")


def _legacy_default_member(db_session, make_org, persona="platform_admin", flagged=False):
    from app.models.user import User

    org = make_org()
    user = User(
        email=f"legacy-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Legacy",
        last_name="Member",
        organization_id=org.id,
        enterprise_role=persona,
        confirmed=True,
    )
    user.is_platform_admin = flagged
    user.password = "Test-password-1!"
    db_session.add(user)
    db_session.flush()
    return user


def test_unchosen_default_persona_sees_no_cost_and_has_no_platform_authority(db_session, make_org, tenant_ctx):
    user = _legacy_default_member(db_session, make_org)
    with tenant_ctx(user.organization_id):
        assert can_see_costs(user) is False
        assert may_see_spend_pages(user) is False
        assert may_open_spend(user) is False
        assert is_platform_admin(user) is False
        assert get_user_role(user) != "platform_admin"
        assert get_effective_role(user) != "platform_admin"


def test_a_deliberately_assigned_narrow_persona_is_unchanged(db_session, make_org, tenant_ctx):
    user = _legacy_default_member(db_session, make_org, persona="cto")
    with tenant_ctx(user.organization_id):
        assert get_user_role(user) == "cto"
        assert can_see_costs(user) is True
