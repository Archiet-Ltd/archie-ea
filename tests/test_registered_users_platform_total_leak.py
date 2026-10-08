"""D-04 (review-pr430-v2.md): the v1 admin blueprint's ``registered_users``
must not tell an ordinary organisation admin how many users exist on the
whole platform.

``registered_users`` computed ``platform_total_users = User.query.count()``
unconditionally and always passed it to the template, which always rendered
it into the page description ("... of N users platform-wide ..."). The page
itself is correctly scoped to the caller's own organisation and must stay
reachable by an ordinary org admin, so the fix is not to gate the whole
route -- only the platform-wide total is now computed solely for an actual
platform admin, with a ``tenant-scoping-ok`` marker recording why
(``check_tenant_scoping.py`` would otherwise flag it as a cross-tenant leak).

Scoped to the v1 (modular) admin blueprint only, per D-05's own v1-only
scope this round -- this process defaults USE_ADMIN_GUARDRAILS=true
(app/_bootstrap/blueprints.py), so the v2 (guardrail) admin tree is what
actually serves ``/admin/users`` here; v2's own ``registered_users`` carries
the identical unconditional ``User.query.count()`` and is unfixed by this
split (not named in review-pr430-v2.md's round-3 ruling, which named only
the v1 admin blueprint). These tests call the v1 view function directly,
inside a real request context, so the assertion exercises the fix under
test regardless of which tree the running app happens to route to.
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


def _render_v1_registered_users(app, db_session, user_id, org_id):
    from flask import g
    from flask_login import login_user

    from app.models.user import User
    from app.modules.admin.routes.admin_routes import registered_users

    with app.test_request_context("/admin/users"):
        login_user(db_session.get(User, user_id))
        g.current_org_id = org_id
        # registered_users is wrapped by @_active_org_admin_required; calling
        # the route function (not the raw function) exercises both D-04 and
        # D-05 together, same as an HTTP request would.
        return registered_users()


def test_an_ordinary_org_admin_does_not_see_the_platform_wide_total(
    app, db_session, make_org
):
    # A second organisation's users exist so there is something to leak if the
    # fix regresses.
    org_a = make_org("regusers-a")
    make_org("regusers-b")
    tenant_admin = _user(db_session, org_a)
    db_session.commit()

    org_a_id = org_a.id
    tenant_admin_id = tenant_admin.id

    response_data = _render_v1_registered_users(app, db_session, tenant_admin_id, org_a_id)

    assert b"platform-wide" not in response_data.encode()
    # Nor should the page render a bare "None" where the total used to sit.
    assert "of None users" not in response_data


def test_a_platform_administrator_does_see_the_platform_wide_total(
    app, db_session, make_org
):
    org_a = make_org("regusers-platform")
    make_org("regusers-platform-b")
    platform_admin = _user(db_session, org_a, platform=True)
    db_session.commit()

    org_a_id = org_a.id
    platform_admin_id = platform_admin.id

    response_data = _render_v1_registered_users(app, db_session, platform_admin_id, org_a_id)

    assert "platform-wide" in response_data
