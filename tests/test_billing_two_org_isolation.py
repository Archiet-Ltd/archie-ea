"""Two-organisation isolation for the admin billing family (blueprint ``billing``).

The billing page at ``GET /admin/billing/`` renders the subscription, plan and
seat limit of ``current_org()`` -- the caller's active organisation. The tenant
boundary is the ``organization_id`` on ``subscriptions`` (``Subscription`` is a
``TenantMixin`` model). This file proves the boundary by building TWO
organisations, giving organisation A a distinctive paid plan, and asserting that
organisation B's administrator, driving organisation B's own ``/admin/billing``
route, never sees organisation A's plan, seats or billing details -- while
organisation B still sees its own.

Follows tests/test_rationalization_benefits_aggregates_tenant.py: the shared
db_session / make_org / login_as / client fixtures from tests/conftest.py, two
real organisations with real ``User`` rows, never a hand-rolled app fixture.
This is the genuine two-organisation isolation test the ASVS register's
admin-billing V4 row cites; the older tests/test_billing_access_control.py
proves the administrator permission on an isolated Flask app and is not a
two-organisation isolation test.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


# --------------------------------------------------------------------- #
# Helpers                                                                #
# --------------------------------------------------------------------- #


def _make_admin(db_session, org_id, label):
    """An Administrator-role ``User`` pinned to *org_id*, an org admin of it."""
    from app.models.user import Permission, Role, User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        first_name=label,
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        is_org_admin=True,
    )
    db_session.add(user)
    db_session.flush()

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(
            name="Administrator",
            permissions=Permission.ADMINISTER,
            index="main",
            default=False,
        )
        db_session.add(role)
        db_session.flush()
    user.role = role
    db_session.flush()
    return user


def _set_plan(db_session, org_id, plan_key, seats):
    """Record a subscription row for *org_id* on *plan_key* with *seats*."""
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    sub = Subscription(
        organization_id=org_id,
        plan=SubscriptionPlan[plan_key],
        status=SubscriptionStatus.active,
        seats_purchased=seats,
    )
    db_session.add(sub)
    db_session.flush()
    return sub


def _current_plan(body: str):
    """The plan name the billing page shows as the caller's current plan."""
    match = re.search(
        r'data-testid="billing-current-plan"[^>]*>\s*([^<]+?)\s*<', body
    )
    return match.group(1).strip() if match else None


@pytest.fixture
def org_a(make_org):
    return make_org("billing-a")


@pytest.fixture
def org_b(make_org):
    return make_org("billing-b")


@pytest.fixture
def admin_a(db_session, org_a):
    return _make_admin(db_session, org_a.id, "AdminA")


@pytest.fixture
def admin_b(db_session, org_b):
    return _make_admin(db_session, org_b.id, "AdminB")


@pytest.fixture
def org_a_on_enterprise(db_session, org_a):
    """Organisation A buys Enterprise with a distinctive 97-seat contract;
    organisation B stays on Community (the default, no subscription row)."""
    sub = _set_plan(db_session, org_a.id, "enterprise", seats=97)
    db_session.commit()
    return sub


@pytest.fixture
def client(app):
    return app.test_client()


# --------------------------------------------------------------------- #
# GET /admin/billing/ -- the current plan and seats are tenant-scoped    #
# --------------------------------------------------------------------- #


class TestBillingPageIsTwoOrgIsolated:
    def test_org_b_billing_page_never_shows_org_a_enterprise_plan(
        self, db_session, org_a_on_enterprise, admin_b, client, login_as
    ):
        """Organisation B's administrator, on organisation B's own
        ``/admin/billing`` route, must see Community (its own plan), never
        organisation A's Enterprise contract or its 97 purchased seats."""
        login_as(client, admin_b)
        resp = client.get("/admin/billing/")

        assert resp.status_code == 200
        body = resp.data.decode()

        assert _current_plan(body) == "Community", (
            "TENANT LEAK: organisation B's billing page showed a current plan "
            "that is not its own Community plan."
        )
        assert "97" not in body, (
            "TENANT LEAK: organisation B's billing page showed organisation A's "
            "97 purchased seats."
        )

    def test_org_a_still_sees_its_own_enterprise_plan(
        self, db_session, org_a_on_enterprise, admin_a, client, login_as
    ):
        """No-regression control: organisation A's own administrator still sees
        the Enterprise plan it bought on its own ``/admin/billing`` route."""
        login_as(client, admin_a)
        resp = client.get("/admin/billing/")

        assert resp.status_code == 200
        body = resp.data.decode()
        assert _current_plan(body) == "Enterprise", (
            "organisation A's own Enterprise plan should still render for it."
        )
