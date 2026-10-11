"""The platform-admin capability merge report (/admin/capability-merges).

Seeds a real merge across two different legacy stores (business_capability's
`deprecated_in_favor_of_id` and capabilities' new `retired_into_id`) pointing
at the same `unified_capabilities` row, and asserts the JSON the route
returns. A tenant administrator -- not a platform administrator -- must be
refused.
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
        email=f"merges-{uuid.uuid4().hex[:6]}@example.test",
        first_name="Merge", last_name="Tester",
        organization_id=org.id, confirmed=True, role=role,
    )
    user.password = uuid.uuid4().hex
    user.is_org_admin = True
    user.is_platform_admin = platform
    db_session.add(user)
    db_session.flush()
    return user


def _seed_merge(db_session, org):
    """One canonical unified_capabilities row with two legacy records (from
    two different stores) retired into it."""
    from app.models.business_capabilities import BusinessCapability, Capability
    from app.models.unified_capability import UnifiedCapability

    canonical = UnifiedCapability(
        name="Order Management", level=1, scope="tenant", organization_id=org.id,
    )
    db_session.add(canonical)
    db_session.flush()

    legacy_business = BusinessCapability(
        name="Order Management (legacy)", organization_id=org.id,
        deprecated_in_favor_of_id=canonical.id,
    )
    legacy_capability = Capability(
        name="Order Management (registry)", organization_id=org.id,
        retired_into_id=canonical.id,
    )
    db_session.add_all([legacy_business, legacy_capability])
    db_session.flush()
    return canonical.id, legacy_business.id, legacy_capability.id


def _login(db_session, client, login_as, user_id):
    from app.models.user import User

    db_session.expunge_all()
    login_as(client, db_session.get(User, user_id))


def test_platform_admin_sees_the_merge_across_two_legacy_stores(
    app, db_session, make_org, client, login_as
):
    org = make_org("merges")
    platform_admin = _user(db_session, org, platform=True)
    canonical_id, business_id, capability_id = _seed_merge(db_session, org)
    db_session.commit()

    _login(db_session, client, login_as, platform_admin.id)
    response = client.get("/admin/capability-merges")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True

    group = next(
        (g for g in payload["merges"] if g["unified_capability_id"] == canonical_id), None
    )
    assert group is not None, payload["merges"]
    assert group["unified_capability_name"] == "Order Management"
    assert group["merged_record_count"] == 2
    assert set(group["merged_records"]) == {
        f"business_capability:{business_id}",
        f"capabilities:{capability_id}",
    }


def test_a_tenant_administrator_is_refused(app, db_session, make_org, client, login_as):
    org = make_org("merges-tenant")
    tenant_admin = _user(db_session, org, platform=False)
    _seed_merge(db_session, org)
    db_session.commit()

    _login(db_session, client, login_as, tenant_admin.id)
    response = client.get("/admin/capability-merges")

    assert response.status_code == 403
