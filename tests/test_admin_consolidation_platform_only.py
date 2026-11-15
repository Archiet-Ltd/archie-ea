"""/admin/consolidation is the product's own blueprint-consolidation progress, not anything about an organisation.

Its content is the same for every organisation and describes the platform's internals, so an organisation's own
owner or administrator has no use for it: only a platform administrator opens it.
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
    user = User(email=f"consol-{uuid.uuid4().hex[:6]}@example.test", first_name="Cons", last_name="Tester",
                organization_id=org.id, confirmed=True, role=role)
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def test_an_organisations_owner_is_refused_and_a_platform_admin_reads_it(db_session, make_org, client, login_as):
    owner_a = _user(db_session, make_org("cons-a"))
    owner_b = _user(db_session, make_org("cons-b"))
    platform = _user(db_session, make_org("cons-p"), platform=True)
    db_session.commit()

    for owner in (owner_a, owner_b):
        login_as(client, owner)
        assert client.get("/admin/consolidation").status_code == 403

    login_as(client, platform)
    page = client.get("/admin/consolidation")
    assert page.status_code == 200
    assert "Blueprint Consolidation Status" in page.get_data(as_text=True)
