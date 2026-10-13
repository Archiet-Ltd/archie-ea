"""Net-terms invoice billing for an enterprise organisation (R1-B95 PR 2,
TB-0193): invoiced instead of paying by card, started by a platform
administrator from the organisation's detail page.

No test here reaches the payment provider -- every provider call is
replaced with a recorded response, same discipline as
tests/test_billing_plans_and_checkout.py.
"""

from __future__ import annotations

import uuid

import pytest

WEBHOOK_SECRET = "unit-test-signing-value"
PRICES = {
    "STRIPE_PRICE_STARTUP_ANNUAL": "price_startup_year",
    "STRIPE_PRICE_STARTUP_MONTHLY": "price_startup_month",
    "STRIPE_PRICE_TEAM_ANNUAL": "price_team_year",
    "STRIPE_PRICE_TEAM_MONTHLY": "price_team_month",
    "STRIPE_PRICE_ENTERPRISE_ANNUAL": "price_enterprise_year",
}


@pytest.fixture
def billing(monkeypatch):
    import stripe

    monkeypatch.setenv("STRIPE_SECRET_KEY", "unit-test-api-value")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.delenv("STRIPE_AUTOMATIC_TAX", raising=False)
    for name, value in PRICES.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(stripe, "api_base", "http://127.0.0.1:9")
    return stripe


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"{label}-{suffix}", slug=f"{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, *, admin=False, platform_admin=False):
    from app.models.user import User

    user = User(
        email=f"{uuid.uuid4().hex[:10]}@example.com",
        first_name="T", last_name="User",
        organization_id=org.id, confirmed=True,
        is_platform_admin=platform_admin,
    )
    user.password = uuid.uuid4().hex
    if admin:
        user.is_org_admin = True
    db_session.add(user)
    db_session.flush()
    return user


def _platform_admin(db_session):
    org = _org(db_session, "platform")
    admin = _user(db_session, org, admin=True, platform_admin=True)
    db_session.commit()
    return admin


class TestStartInvoiceBillingService:
    def test_starts_a_send_invoice_subscription_at_net_30(self, app, db_session, billing, monkeypatch):
        from app.models.subscription import Subscription
        from app.services.billing_service import BillingService

        org = _org(db_session, "invoice-org")
        db_session.commit()

        monkeypatch.setattr(billing.Customer, "create", lambda **kw: {"id": "cus_inv_1"})
        calls = {}

        def _create(**kw):
            calls.update(kw)
            return {
                "id": "sub_inv_1",
                "customer": "cus_inv_1",
                "status": "active",
                "collection_method": kw.get("collection_method"),
                "days_until_due": kw.get("days_until_due"),
                "items": {"data": [{"price": {"id": kw["items"][0]["price"]}, "quantity": kw["items"][0]["quantity"]}]},
            }

        monkeypatch.setattr(billing.Subscription, "create", _create)

        with app.app_context():
            plan = BillingService.start_invoice_billing(org, "enterprise", "year", seats=None, days_until_due=30)

        assert plan.key == "enterprise"
        assert calls["collection_method"] == "send_invoice"
        assert calls["days_until_due"] == 30
        assert calls["items"] == [{"price": "price_enterprise_year", "quantity": 1}]

        db_session.expire_all()
        sub = Subscription.query.filter_by(organization_id=org.id).one()
        assert sub.collection_method == "send_invoice"
        assert sub.days_until_due == 30
        assert sub.stripe_customer_id == "cus_inv_1"
        assert sub.stripe_subscription_id == "sub_inv_1"

    def test_refuses_when_the_organisation_already_has_a_live_subscription(self, app, db_session, billing, monkeypatch):
        from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus
        from app.services.billing_service import BillingError, BillingService

        org = _org(db_session, "already-live")
        sub = Subscription(
            organization_id=org.id, plan=SubscriptionPlan.startup, status=SubscriptionStatus.active,
            stripe_subscription_id="sub_existing",
        )
        db_session.add(sub)
        db_session.commit()

        with app.app_context(), pytest.raises(BillingError, match="already has a subscription"):
            BillingService.start_invoice_billing(org, "enterprise", "year")

    def test_refuses_a_plan_with_no_configured_price(self, app, db_session, billing, monkeypatch):
        from app.services.billing_service import BillingNotConfigured, BillingService

        monkeypatch.delenv("STRIPE_PRICE_ENTERPRISE_ANNUAL", raising=False)
        org = _org(db_session, "no-price")
        db_session.commit()

        with app.app_context(), pytest.raises(BillingNotConfigured):
            BillingService.start_invoice_billing(org, "enterprise", "year")

    def test_quantity_defaults_to_the_plans_default_seats_for_a_per_seat_plan(self, app, db_session, billing, monkeypatch):
        """Enterprise is not per-seat today, so this proves the seat-quantity
        path still works correctly for a per-seat plan invoiced this way
        (e.g. Team sold by contract with net terms)."""
        from app.services.billing_service import BillingService

        org = _org(db_session, "team-invoice")
        db_session.commit()
        monkeypatch.setattr(billing.Customer, "create", lambda **kw: {"id": "cus_team_inv"})
        calls = {}

        def _create(**kw):
            calls.update(kw)
            return {
                "id": "sub_team_inv", "customer": "cus_team_inv", "status": "active",
                "collection_method": "send_invoice", "days_until_due": 30,
                "items": {"data": [{"price": {"id": kw["items"][0]["price"]}, "quantity": kw["items"][0]["quantity"]}]},
            }

        monkeypatch.setattr(billing.Subscription, "create", _create)

        with app.app_context():
            BillingService.start_invoice_billing(org, "team", "year", seats=None, days_until_due=30)

        assert calls["items"][0]["quantity"] == 15  # Team's default_seats


class TestInvoiceBillingRoute:
    def test_a_platform_admin_can_start_invoice_billing_from_the_organisation_page(
        self, app, db_session, client, login_as, billing, monkeypatch
    ):
        from app.models.subscription import Subscription

        org = _org(db_session, "route-org")
        platform = _platform_admin(db_session)

        monkeypatch.setattr(billing.Customer, "create", lambda **kw: {"id": "cus_route_1"})

        def _create(**kw):
            return {
                "id": "sub_route_1", "customer": "cus_route_1", "status": "active",
                "collection_method": "send_invoice", "days_until_due": 30,
                "items": {"data": [{"price": {"id": kw["items"][0]["price"]}, "quantity": kw["items"][0]["quantity"]}]},
            }

        monkeypatch.setattr(billing.Subscription, "create", _create)

        with app.app_context():
            login_as(client, platform)
            resp = client.post(
                f"/admin/organizations/{org.id}/invoice-billing",
                data={"plan": "enterprise", "interval": "year", "days_until_due": "30"},
                follow_redirects=True,
            )

        assert resp.status_code == 200
        assert b"Invoice billing started" in resp.data

        db_session.expire_all()
        sub = Subscription.query.filter_by(organization_id=org.id).one()
        assert sub.collection_method == "send_invoice"

    def test_a_mere_org_admin_cannot_start_invoice_billing_for_another_org(self, app, db_session, client, login_as):
        """platform_admin_required gates this route -- an ordinary
        organisation administrator, even of a different organisation, must
        be refused."""
        org = _org(db_session, "victim-org")
        other_org = _org(db_session, "attacker-org")
        attacker = _user(db_session, other_org, admin=True)
        db_session.commit()

        with app.app_context():
            login_as(client, attacker)
            resp = client.post(
                f"/admin/organizations/{org.id}/invoice-billing",
                data={"plan": "enterprise", "interval": "year"},
            )

        assert resp.status_code in (302, 403)

    def test_the_status_panel_renders_once_invoice_billing_has_started(
        self, app, db_session, client, login_as, billing, monkeypatch
    ):
        from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

        org = _org(db_session, "status-org")
        platform = _platform_admin(db_session)
        sub = Subscription(
            organization_id=org.id, plan=SubscriptionPlan.enterprise, status=SubscriptionStatus.active,
            stripe_subscription_id="sub_status_1", collection_method="send_invoice", days_until_due=30,
            billing_interval="year",
        )
        db_session.add(sub)
        db_session.commit()

        with app.app_context():
            login_as(client, platform)
            resp = client.get(f"/admin/organizations/{org.id}")

        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "Invoiced on net-30 terms" in html
        assert "Start invoice billing" not in html
