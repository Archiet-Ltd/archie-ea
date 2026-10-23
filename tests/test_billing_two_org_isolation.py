"""Two-organisation isolation for the admin billing family (blueprint ``billing``).

The billing page at ``GET /admin/billing/`` renders the subscription, plan and
seat limit of ``current_org()`` -- the caller's ACTIVE organisation, resolved
from ``session[ACTIVE_ORG_SESSION_KEY]`` and NOT from
``current_user.organization_id`` (see app/middleware/tenant_context.py).

This file proves the boundary the way the app really crosses it: the signed-in
administrator is a NATIVE member of organisation B and also an OrgRole member of
organisation A, and is switched INTO organisation A through the real session
switch (AccountService.switch_active_organization, the POST
/account/switch-organization route). Once switched, the billing page must render
organisation A's own named current plan (Enterprise), because ``current_org()``
follows the switched session. A bug where ``current_org()`` read
``user.organization_id`` instead of the switched session would render
organisation B's Community plan, and these tests would FAIL -- which is the
point. The assertions name the specific current-plan row (the
``billing-current-plan`` testid value), never a bare substring that a nonce or a
price could satisfy by accident.

Follows tests/test_rationalization_benefits_aggregates_tenant.py and
tests/smoke/test_multi_org_account_switching_journey.py: the shared db_session /
make_org / login_as / client fixtures from tests/conftest.py, two real
organisations with real ``User`` rows, and the production org-switch mechanism.
This is the genuine two-organisation isolation test the ASVS register's
admin-billing V4 row cites.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _switch_into(client, org_id):
    """Switch the signed-in client INTO *org_id* via the real session switch.

    Posts to /account/switch-organization exactly as the app's own UI does; the
    route calls AccountService.switch_active_organization, which writes
    session[ACTIVE_ORG_SESSION_KEY]. Returns the response so the caller can
    assert the switch was accepted.
    """
    return client.post(
        "/account/switch-organization",
        data={"organization_id": str(org_id)},
        follow_redirects=False,
    )


def _grant_membership(db_session, user, org_id, role="viewer"):
    """Give *user* an OrgRole in *org_id* so they may switch into it."""
    from app.models.org_role import OrgRole

    OrgRole.set_role(org_id, user.id, role)
    db_session.commit()


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
    def test_billing_page_follows_the_switched_session_into_org_a(
        self, db_session, org_a, org_b, org_a_on_enterprise, admin_b, client, login_as
    ):
        """The administrator is a native member of organisation B and an OrgRole
        member of organisation A. After switching INTO organisation A through the
        real session switch, organisation B's own ``/admin/billing`` route must
        render organisation A's named Enterprise plan -- because ``current_org()``
        follows the switched session, not ``user.organization_id``.

        DB-ONLY (CI): this request needs PostgreSQL; it cannot run in the
        sandbox. A bug where ``current_org()`` read ``user.organization_id``
        would render organisation B's Community plan and FAIL this assertion.
        """
        _grant_membership(db_session, admin_b, org_a.id)
        login_as(client, admin_b)

        switch = _switch_into(client, org_a.id)
        assert switch.status_code in (302, 303), (
            "the session switch into organisation A should be accepted"
        )

        resp = client.get("/admin/billing/")
        assert resp.status_code == 200
        body = resp.data.decode()

        # Named-row assertion: the current-plan testid value must be the specific
        # plan identity of the ACTIVE organisation (org_a's Enterprise), not a
        # bare substring. If current_org() ignored the switch it would read
        # org_b's Community plan and this would fail.
        assert _current_plan(body) == "Enterprise", (
            "current_org() did not follow the switched session into "
            "organisation A: the billing page showed a current plan that is not "
            "organisation A's named Enterprise plan."
        )

    def test_org_b_billing_page_without_switch_shows_its_own_community_plan(
        self, db_session, org_a, org_b, org_a_on_enterprise, admin_b, client, login_as
    ):
        """No-regression control: with NO switch, organisation B's administrator
        on organisation B's own ``/admin/billing`` route sees organisation B's
        own Community plan, never organisation A's named Enterprise plan.

        DB-ONLY (CI): needs PostgreSQL.
        """
        login_as(client, admin_b)
        resp = client.get("/admin/billing/")

        assert resp.status_code == 200
        body = resp.data.decode()
        assert _current_plan(body) == "Community", (
            "TENANT LEAK: organisation B's billing page showed a current plan "
            "that is not its own Community plan."
        )
