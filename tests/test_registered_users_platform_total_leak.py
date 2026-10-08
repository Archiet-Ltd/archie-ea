"""/admin/users must not tell an ordinary organisation admin how many users exist on
the whole platform.

``registered_users`` computed ``platform_total_users = User.query.count()``
unconditionally and always passed it to the template, which always rendered it into
the page description ("... of N users platform-wide ..."). The page itself is
correctly scoped to the caller's own organisation and must stay reachable by an
ordinary org admin, so the fix is not to gate the whole route -- only the platform-wide
total is now computed only for an actual platform admin (refuter review of PR 428,
finding D-6, LOW). Both live module trees render the same
``admin/registered_users.html`` template and share this fix.
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
    user = User(
        email=f"regusers-{uuid.uuid4().hex[:6]}@example.test",
        first_name="RegUsers",
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


def test_an_ordinary_org_admin_does_not_see_the_platform_wide_total(
    app, db_session, make_org, client, login_as
):
    # A second organisation's users exist so there is something to leak if the
    # fix regresses.
    org_a = make_org("regusers-a")
    make_org("regusers-b")
    tenant_admin = _user(db_session, org_a)
    db_session.commit()

    _login(db_session, client, login_as, tenant_admin.id)
    response = client.get("/admin/users")

    assert response.status_code == 200
    assert b"platform-wide" not in response.data
    # Nor should the page render a bare "None" where the total used to sit.
    assert b"of None users" not in response.data


def test_a_platform_administrator_does_see_the_platform_wide_total(
    app, db_session, make_org, client, login_as
):
    org_a = make_org("regusers-platform")
    make_org("regusers-platform-b")
    platform_admin = _user(db_session, org_a, platform=True)
    db_session.commit()

    _login(db_session, client, login_as, platform_admin.id)
    response = client.get("/admin/users")

    assert response.status_code == 200
    assert b"platform-wide" in response.data
