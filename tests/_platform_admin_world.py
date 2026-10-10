"""Shared test-user/login builders for "platform-admin-only write" test files.

At least three test files (test_sidebar_editor_vendor_platform_admin.py,
test_persona_prompts_platform_admin.py, test_roles_and_prompts_platform_admin.py)
independently rebuilt the same "one organisation, a tenant Administrator and a
platform Administrator, logged in one at a time" scaffold. This is the one copy;
new platform-admin-only test files should import from here rather than adding
another.
"""

from __future__ import annotations

import uuid

import pytest


def platform_admin_user(db_session, org, *, prefix, platform=False):
    """A confirmed Administrator-role user in ``org``, optionally also a
    platform administrator. ``prefix`` keeps generated emails distinguishable
    across test files sharing one database."""
    from app.models import Role
    from app.models.user import User

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        pytest.skip("no Administrator role seeded in this database")
    user = User(
        email=f"{prefix}-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Test",
        last_name="Admin",
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


def login_platform_admin_world_user(db_session, client, login_as, user_id):
    """Log in as a user created by ``platform_admin_user``, defeating the
    flask_login ``g`` cache the same way every other test module's
    ``login_as`` usage must (see conftest.py's ``login_as`` docstring)."""
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


def platform_admin_world(db_session, make_org, *, org_prefix, user_prefix):
    """One organisation with a tenant Administrator and a platform
    Administrator. Returns ``(tenant_id, platform_id)``."""
    org = make_org(org_prefix)
    tenant = platform_admin_user(db_session, org, prefix=user_prefix)
    platform = platform_admin_user(db_session, org, prefix=user_prefix, platform=True)
    db_session.commit()
    return tenant.id, platform.id
