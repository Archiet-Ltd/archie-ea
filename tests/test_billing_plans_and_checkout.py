"""Plans, checkout, signed billing events and plan limits.

No test here reaches the payment provider: every provider call the code makes
is replaced with a recorded response, and the provider's API base is pointed at
a closed local port so an unreplaced call fails instead of going out.
"""

import hashlib
import hmac
import json
import time
import uuid

import pytest
from flask import render_template_string, session

WEBHOOK_SECRET = "unit-test-signing-value"
PRICES = {
    "STRIPE_PRICE_STARTUP_ANNUAL": "price_startup_year",
    "STRIPE_PRICE_STARTUP_MONTHLY": "price_startup_month",
    "STRIPE_PRICE_TEAM_ANNUAL": "price_team_year",
    "STRIPE_PRICE_TEAM_MONTHLY": "price_team_month",
}
BILLING_ENV = ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_AUTOMATIC_TAX", *PRICES)


@pytest.fixture
def no_billing(monkeypatch):
    for name in BILLING_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def billing(monkeypatch):
    """Billing configured in test mode, with every provider call blocked."""
    import stripe

    monkeypatch.setenv("STRIPE_SECRET_KEY", "unit-test-api-value")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.delenv("STRIPE_AUTOMATIC_TAX", raising=False)
    for name, value in PRICES.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(stripe, "api_base", "http://127.0.0.1:9")
    return stripe


def _org(db_session, label, plan=None):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Billing {label} {suffix}", slug=f"billing-{label}-{suffix}")
    if plan:
        org.plan = plan
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, *, admin=False):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator" if admin else "User").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator" if admin else "User").first()
    user = User(
        email=f"billing-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Bill",
        last_name="Ing",
        organization_id=org.id,
        role=role,
        is_org_admin=admin,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _subscription(db_session, org, **fields):
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    sub = Subscription(
        organization_id=org.id,
        plan=fields.pop("plan", SubscriptionPlan.free),
        status=fields.pop("status", SubscriptionStatus.active),
        seats_purchased=fields.pop("seats_purchased", 3),
        **fields,
    )
    db_session.add(sub)
    db_session.flush()
    return sub


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    stamp = int(time.time())
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={digest}"


def _event(event_type, obj, *, event_id=None, created=None):
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex[:16]}",
        "object": "event",
        "type": event_type,
        "created": created or int(time.time()),
        "data": {"object": obj},
    }


def _stripe_sub(customer, sub_id="sub_1", price="price_startup_year", quantity=1,
                status="active", cancel_at_period_end=False, period_end=1893456000):
    return {
        "id": sub_id,
        "object": "subscription",
        "customer": customer,
        "status": status,
        "cancel_at_period_end": cancel_at_period_end,
        "items": {"data": [{
            "id": "si_1",
            "price": {"id": price, "recurring": {"interval": "year"}},
            "quantity": quantity,
            "current_period_end": period_end,
        }]},
    }


def _post_event(client, event, *, signature=None):
    payload = json.dumps(event).encode()
    return client.post(
        "/admin/billing/webhook",
        data=payload,
        headers={"Stripe-Signature": signature or _sign(payload), "Content-Type": "application/json"},
    )


def _fresh(db_session, org):
    from app.models.subscription import Subscription

    db_session.expire_all()
    return Subscription.query.filter_by(organization_id=org.id).one()


# --------------------------------------------------------------------------- #
# Plan limits                                                                  #
# --------------------------------------------------------------------------- #


def test_limits_are_enforced_per_organisation(db_session):
    from app.services.billing_plans import PlanLimitReached, user_limit_status

    full = _org(db_session, "full")
    roomy = _org(db_session, "roomy")
    for _ in range(3):
        _user(db_session, full)
    _user(db_session, roomy)
    db_session.commit()

    status = user_limit_status(full.id)
    assert (status["plan_name"], status["limit"], status["used"]) == ("Community", 3, 3)
    assert status["limit_reached"] is True
    with pytest.raises(PlanLimitReached):
        _user(db_session, full)
    db_session.rollback()

    assert user_limit_status(roomy.id)["limit_reached"] is False
    _user(db_session, roomy)


def test_team_counts_editors_and_leaves_readers_free(db_session):
    from app.models.org_role import OrgRole
    from app.models.subscription import SubscriptionPlan
    from app.services.billing_plans import user_limit_status

    org = _org(db_session, "team")
    _subscription(db_session, org, plan=SubscriptionPlan.team, seats_purchased=2)
    editor = _user(db_session, org)
    for _ in range(3):
        reader = _user(db_session, org)
        db_session.add(OrgRole(organization_id=org.id, user_id=reader.id, role="viewer"))
    db_session.add(OrgRole(organization_id=org.id, user_id=editor.id, role="architect"))
    db_session.flush()

    status = user_limit_status(org.id)
    assert (status["limit"], status["used"], status["limit_reached"]) == (2, 1, False)


def test_contract_enterprise_organisation_has_no_people_limit(db_session):
    from app.services.billing_plans import user_limit_status

    org = _org(db_session, "ent", plan="enterprise")
    for _ in range(5):
        _user(db_session, org)
    status = user_limit_status(org.id)
    assert status["plan_key"] == "enterprise"
    assert status["limit"] is None and status["limit_reached"] is False


def test_seat_counter_and_billing_page_give_one_answer(db_session):
    from app.services.billing_plans import user_limit_status
    from app.services.usage_metering_service import UsageMeteringService

    org = _org(db_session, "agree")
    org.max_users = 50  # the retired column must not answer any more
    _user(db_session, org)
    _user(db_session, org)
    db_session.flush()

    status = user_limit_status(org.id)
    seats = UsageMeteringService.get_seat_usage(org.id)
    assert seats == {"used": status["used"], "purchased": status["limit"],
                     "limit_reached": status["limit_reached"]}
    assert seats["purchased"] == 3


# --------------------------------------------------------------------------- #
# Signed provider events                                                       #
# --------------------------------------------------------------------------- #


def test_webhook_rejects_a_tampered_or_unsigned_event(app, db_session, client, billing):
    from app.models.subscription import BillingEvent, SubscriptionPlan

    org = _org(db_session, "tamper")
    _subscription(db_session, org, stripe_customer_id="cus_tamper")
    db_session.commit()

    genuine = json.dumps(_event("customer.subscription.created", _stripe_sub("cus_tamper"))).encode()
    tampered = json.dumps(
        _event("customer.subscription.created", _stripe_sub("cus_tamper", price="price_team_year", quantity=500))
    ).encode()
    with app.app_context():
        forged = client.post("/admin/billing/webhook", data=tampered,
                             headers={"Stripe-Signature": _sign(genuine)})
        unsigned = client.post("/admin/billing/webhook", data=genuine)
        wrong_key = client.post("/admin/billing/webhook", data=genuine,
                                headers={"Stripe-Signature": _sign(genuine, "another-signing-value")})

    assert (forged.status_code, unsigned.status_code, wrong_key.status_code) == (400, 400, 400)
    assert forged.get_json() == {"error": "Invalid signature"}
    sub = _fresh(db_session, org)
    assert sub.plan == SubscriptionPlan.free and sub.stripe_subscription_id is None
    assert BillingEvent.query.filter_by(organization_id=org.id).count() == 0


def test_webhook_says_not_configured_without_a_signing_secret(app, db_session, client, no_billing):
    with app.app_context():
        resp = _post_event(client, _event("invoice.paid", {"customer": "cus_x"}))
    assert resp.status_code == 503
    assert resp.get_json() == {"error": "Billing is not configured"}


def test_subscription_event_sets_plan_and_limits(app, db_session, client, billing):
    from app.models.subscription import SubscriptionPlan, SubscriptionStatus
    from app.services.billing_plans import user_limit_status

    org = _org(db_session, "created")
    _subscription(db_session, org, stripe_customer_id="cus_created")
    db_session.commit()

    with app.app_context():
        resp = _post_event(client, _event("customer.subscription.created", _stripe_sub("cus_created")))
    assert resp.status_code == 200

    sub = _fresh(db_session, org)
    assert sub.plan == SubscriptionPlan.startup
    assert sub.status == SubscriptionStatus.active
    assert sub.stripe_subscription_id == "sub_1"
    assert sub.billing_interval == "year"
    assert sub.current_period_end.year == 2030
    assert user_limit_status(org.id)["limit"] == 10


def test_the_same_event_delivered_twice_is_applied_once(app, db_session, client, billing):
    from app.models.subscription import BillingEvent, SubscriptionPlan

    org = _org(db_session, "dupe")
    _subscription(db_session, org, stripe_customer_id="cus_dupe")
    db_session.commit()
    now = int(time.time())
    created = _event("customer.subscription.created", _stripe_sub("cus_dupe"),
                     event_id="evt_dupe_created", created=now - 60)
    upgraded = _event("customer.subscription.updated",
                      _stripe_sub("cus_dupe", price="price_team_year", quantity=20),
                      event_id="evt_dupe_upgraded", created=now)

    with app.app_context():
        first = _post_event(client, created)
        second = _post_event(client, upgraded)
        replay = _post_event(client, created)
    assert (first.status_code, second.status_code, replay.status_code) == (200, 200, 200)

    sub = _fresh(db_session, org)
    assert sub.plan == SubscriptionPlan.team and sub.seats_purchased == 20
    assert BillingEvent.query.filter_by(
        organization_id=org.id, provider_event_id="evt_dupe_created").count() == 1
    assert BillingEvent.query.filter_by(organization_id=org.id).count() == 2


def test_an_older_event_arriving_late_does_not_roll_the_plan_back(app, db_session, client, billing):
    from app.models.subscription import SubscriptionPlan

    org = _org(db_session, "late")
    _subscription(db_session, org, stripe_customer_id="cus_late")
    db_session.commit()
    now = int(time.time())

    with app.app_context():
        _post_event(client, _event("customer.subscription.updated",
                                   _stripe_sub("cus_late", price="price_team_year", quantity=15),
                                   created=now))
        _post_event(client, _event("customer.subscription.updated",
                                   _stripe_sub("cus_late", price="price_startup_year"),
                                   created=now - 3600))

    assert _fresh(db_session, org).plan == SubscriptionPlan.team


def test_downgrade_lowers_the_limit(app, db_session, client, billing):
    from app.models.subscription import SubscriptionPlan
    from app.services.billing_plans import user_limit_status

    org = _org(db_session, "down")
    _subscription(db_session, org, plan=SubscriptionPlan.team, seats_purchased=20,
                  stripe_customer_id="cus_down", stripe_subscription_id="sub_1")
    for _ in range(12):
        _user(db_session, org)
    db_session.commit()
    assert user_limit_status(org.id)["limit_reached"] is False

    with app.app_context():
        resp = _post_event(client, _event("customer.subscription.updated", _stripe_sub("cus_down")))
    assert resp.status_code == 200

    status = user_limit_status(org.id)
    assert (status["plan_key"], status["limit"], status["used"]) == ("startup", 10, 12)
    assert status["limit_reached"] is True


def test_cancel_keeps_the_plan_until_the_provider_ends_it(app, db_session, client, billing):
    from app.models.subscription import SubscriptionPlan, SubscriptionStatus
    from app.services.billing_plans import user_limit_status

    org = _org(db_session, "cancel")
    _subscription(db_session, org, plan=SubscriptionPlan.startup,
                  stripe_customer_id="cus_cancel", stripe_subscription_id="sub_1")
    db_session.commit()
    now = int(time.time())

    with app.app_context():
        _post_event(client, _event("customer.subscription.updated",
                                   _stripe_sub("cus_cancel", cancel_at_period_end=True), created=now - 10))
    sub = _fresh(db_session, org)
    assert sub.cancel_at_period_end is True
    assert sub.plan == SubscriptionPlan.startup and sub.status == SubscriptionStatus.active
    assert user_limit_status(org.id)["limit"] == 10

    with app.app_context():
        _post_event(client, _event("customer.subscription.deleted",
                                   _stripe_sub("cus_cancel", status="canceled"), created=now))
    sub = _fresh(db_session, org)
    assert sub.plan == SubscriptionPlan.free and sub.status == SubscriptionStatus.cancelled
    assert user_limit_status(org.id)["limit"] == 3


def test_failed_then_paid_invoice_moves_status(app, db_session, client, billing):
    from app.models.subscription import SubscriptionPlan, SubscriptionStatus

    org = _org(db_session, "invoice")
    _subscription(db_session, org, plan=SubscriptionPlan.startup,
                  stripe_customer_id="cus_inv", stripe_subscription_id="sub_inv")
    db_session.commit()
    # Older provider API versions carry the subscription on the invoice,
    # newer ones under parent.subscription_details.
    failed = {"customer": "cus_inv", "subscription": "sub_inv"}
    paid = {"customer": "cus_inv", "parent": {"subscription_details": {"subscription": "sub_inv"}}}

    with app.app_context():
        _post_event(client, _event("invoice.payment_failed", failed))
    assert _fresh(db_session, org).status == SubscriptionStatus.past_due
    with app.app_context():
        _post_event(client, _event("invoice.paid", paid))
    sub = _fresh(db_session, org)
    assert sub.status == SubscriptionStatus.active and sub.plan == SubscriptionPlan.startup


def test_an_event_changes_only_its_own_organisation(app, db_session, client, billing):
    from app.models.subscription import BillingEvent, SubscriptionPlan

    mine = _org(db_session, "mine")
    theirs = _org(db_session, "theirs")
    _subscription(db_session, mine, stripe_customer_id="cus_mine")
    _subscription(db_session, theirs, stripe_customer_id="cus_theirs")
    db_session.commit()

    with app.app_context():
        _post_event(client, _event("customer.subscription.created",
                                   _stripe_sub("cus_mine", price="price_team_year", quantity=30)))
        unknown = _post_event(client, _event("customer.subscription.created",
                                             _stripe_sub("cus_nobody", sub_id="sub_nobody")))

    assert _fresh(db_session, mine).plan == SubscriptionPlan.team
    assert _fresh(db_session, theirs).plan == SubscriptionPlan.free
    assert BillingEvent.query.filter_by(organization_id=theirs.id).count() == 0
    assert unknown.status_code == 200


# --------------------------------------------------------------------------- #
# Screens: checkout, change, cancel, details, limits                           #
# --------------------------------------------------------------------------- #


def _admin_org(db_session, label, **sub_fields):
    org = _org(db_session, label)
    if sub_fields:
        _subscription(db_session, org, **sub_fields)
    admin = _user(db_session, org, admin=True)
    db_session.commit()
    return org, admin


def test_without_keys_the_billing_page_says_payment_is_not_set_up(app, db_session, client, login_as, no_billing):
    org, admin = _admin_org(db_session, "nokeys")
    with app.app_context():
        login_as(client, admin)
        page = client.get("/admin/billing/?plan=startup&interval=year")
        login_as(client, admin)
        resp = client.post("/admin/billing/upgrade", data={"plan": "startup", "interval": "year"},
                           follow_redirects=True)
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Online payment is not set up on this installation" in html
    # An organisation administrator is a customer, not the platform operator:
    # the banner tells them payment is not available, but not which settings
    # are missing.
    assert "STRIPE_SECRET_KEY" not in html
    assert "Settings the operator has not provided" not in html
    assert "Continue to payment" not in html
    assert "Online payment is not set up on this installation. No payment was taken." in resp.get_data(as_text=True)


def test_without_keys_a_platform_admin_sees_which_settings_are_missing(app, db_session, client, login_as, no_billing):
    platform = _platform_admin(db_session)
    with app.app_context():
        login_as(client, platform)
        page = client.get("/admin/billing/")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Online payment is not set up on this installation" in html
    assert "STRIPE_SECRET_KEY" in html  # the operator can see which setting is missing


def test_with_keys_the_billing_page_shows_no_configuration_warning(app, db_session, client, login_as, billing):
    org, admin = _admin_org(db_session, "haskeys")
    with app.app_context():
        login_as(client, admin)
        page = client.get("/admin/billing/")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Online payment is not set up on this installation" not in html
    assert 'data-testid="billing-not-configured"' not in html


def test_buy_starts_checkout_for_the_chosen_plan(app, db_session, client, login_as, billing, monkeypatch):
    from app.models.subscription import Subscription

    org, admin = _admin_org(db_session, "buy")
    calls = {}
    monkeypatch.setattr(billing.Customer, "create", lambda **kw: {"id": "cus_buy"})

    def _create(**kw):
        calls.update(kw)
        return {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"}

    monkeypatch.setattr(billing.checkout.Session, "create", _create)
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/billing/upgrade",
                           data={"plan": "team", "interval": "year", "seats": "20"})

    assert resp.status_code == 303
    assert resp.headers["Location"] == "https://checkout.stripe.com/c/pay/cs_test_1"
    assert calls["line_items"] == [{"price": "price_team_year", "quantity": 20}]
    assert calls["client_reference_id"] == str(org.id)
    assert calls["customer"] == "cus_buy"
    assert calls["mode"] == "subscription"
    assert calls["success_url"].endswith("/admin/billing/checkout/complete?session_id={CHECKOUT_SESSION_ID}")
    db_session.expire_all()
    assert Subscription.query.filter_by(organization_id=org.id).one().stripe_customer_id == "cus_buy"


def test_enterprise_is_contact_sales_not_checkout(app, db_session, client, login_as, billing, monkeypatch):
    org, admin = _admin_org(db_session, "ent")

    def _never(**kw):
        raise AssertionError("checkout must not start for Enterprise")

    monkeypatch.setattr(billing.checkout.Session, "create", _never)
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/billing/upgrade", data={"plan": "enterprise", "interval": "year"},
                           follow_redirects=True)
    assert "Enterprise is sold by annual contract. Contact sales to buy it." in resp.get_data(as_text=True)


def test_returning_from_checkout_applies_the_plan_at_once(app, db_session, client, login_as, billing, monkeypatch):
    from app.models.subscription import SubscriptionPlan
    from app.services.billing_plans import user_limit_status

    org, admin = _admin_org(db_session, "back", stripe_customer_id="cus_back")
    session = {"id": "cs_back", "client_reference_id": str(org.id), "status": "complete",
               "customer": "cus_back", "subscription": _stripe_sub("cus_back", sub_id="sub_back")}
    monkeypatch.setattr(billing.checkout.Session, "retrieve", lambda sid, **kw: session)
    with app.app_context():
        login_as(client, admin)
        resp = client.get("/admin/billing/checkout/complete?session_id=cs_back", follow_redirects=True)

    assert "Payment received. Your organisation is on the Startup plan." in resp.get_data(as_text=True)
    sub = _fresh(db_session, org)
    assert sub.plan == SubscriptionPlan.startup and sub.stripe_subscription_id == "sub_back"
    assert user_limit_status(org.id)["limit"] == 10


def test_returning_with_another_organisations_checkout_is_refused(app, db_session, client, login_as, billing, monkeypatch):
    from app.models.subscription import SubscriptionPlan

    org, admin = _admin_org(db_session, "mine2")
    other = _org(db_session, "other2")
    db_session.commit()
    session = {"id": "cs_other", "client_reference_id": str(other.id), "status": "complete",
               "customer": "cus_other", "subscription": _stripe_sub("cus_other", price="price_team_year")}
    monkeypatch.setattr(billing.checkout.Session, "retrieve", lambda sid, **kw: session)
    with app.app_context():
        login_as(client, admin)
        resp = client.get("/admin/billing/checkout/complete?session_id=cs_other", follow_redirects=True)

    assert "This payment belongs to a different organisation." in resp.get_data(as_text=True)
    from app.models.subscription import Subscription
    from app.services.billing_plans import user_limit_status

    db_session.expire_all()
    # Nothing was written: not the other organisation's plan, and not even a
    # subscriptions row, since showing the billing page only reads.
    assert Subscription.query.filter_by(organization_id=org.id).first() is None
    assert user_limit_status(org.id)["plan_key"] == SubscriptionPlan.free.value


def test_administrator_downgrades_from_the_billing_page(app, db_session, client, login_as, billing, monkeypatch):
    from app.models.subscription import SubscriptionPlan

    org, admin = _admin_org(db_session, "chg", plan=SubscriptionPlan.team, seats_purchased=15,
                            stripe_customer_id="cus_chg", stripe_subscription_id="sub_chg")
    modified = {}
    monkeypatch.setattr(billing.Subscription, "retrieve",
                        lambda sid, **kw: _stripe_sub("cus_chg", sub_id=sid, price="price_team_year", quantity=15))

    def _modify(sid, **kw):
        modified.update(kw)
        return _stripe_sub("cus_chg", sub_id=sid, price=kw["items"][0]["price"],
                           quantity=kw["items"][0]["quantity"])

    monkeypatch.setattr(billing.Subscription, "modify", _modify)
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/billing/change-plan", data={"plan": "startup", "interval": "year"},
                           follow_redirects=True)

    assert "Your organisation is now on the Startup plan." in resp.get_data(as_text=True)
    assert modified["items"] == [{"id": "si_1", "price": "price_startup_year", "quantity": 1}]
    assert _fresh(db_session, org).plan == SubscriptionPlan.startup


def test_administrator_cancels_at_period_end(app, db_session, client, login_as, billing, monkeypatch):
    from app.models.subscription import SubscriptionPlan, SubscriptionStatus

    org, admin = _admin_org(db_session, "cxl", plan=SubscriptionPlan.startup,
                            stripe_customer_id="cus_cxl", stripe_subscription_id="sub_cxl")
    monkeypatch.setattr(billing.Subscription, "modify",
                        lambda sid, **kw: _stripe_sub("cus_cxl", sub_id=sid, cancel_at_period_end=True))
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/billing/cancel", follow_redirects=True)

    html = resp.get_data(as_text=True)
    assert "Your subscription is cancelled. The plan stays in place until 01 Jan 2030." in html
    assert "Keep my subscription" in html
    sub = _fresh(db_session, org)
    assert sub.cancel_at_period_end is True
    assert sub.plan == SubscriptionPlan.startup and sub.status == SubscriptionStatus.active


def test_billing_details_and_invoices(app, db_session, client, login_as, billing, monkeypatch):
    org, admin = _admin_org(db_session, "inv", stripe_customer_id="cus_inv2")
    modified = {}
    monkeypatch.setattr(billing.Customer, "modify", lambda cid, **kw: modified.update(cid=cid, **kw))
    monkeypatch.setattr(billing.Customer, "retrieve", lambda cid, **kw: {
        "id": cid, "email": "finance@example.com", "address": {"country": "GB"},
        "invoice_settings": {"custom_fields": [{"name": "PO number", "value": "PO-7781"}]},
    })
    monkeypatch.setattr(billing.Invoice, "list", lambda **kw: {"data": [{
        "number": "ENT-0001", "created": 1790000000, "total": 58800, "tax": 9800, "currency": "gbp",
        "status": "paid", "invoice_pdf": "https://pay.stripe.com/invoice/ENT-0001/pdf",
        "hosted_invoice_url": None,
    }, {
        "number": "ENT-0002", "created": 1790100000, "total": 49000, "currency": "usd",
        "status": "open", "invoice_pdf": None, "hosted_invoice_url": None,
    }]})
    with app.app_context():
        login_as(client, admin)
        saved = client.post("/admin/billing/details",
                            data={"billing_email": "finance@example.com", "po_number": "PO-7781"},
                            follow_redirects=True)
    html = saved.get_data(as_text=True)

    assert modified["cid"] == "cus_inv2"
    assert modified["email"] == "finance@example.com"
    assert modified["invoice_settings"] == {"custom_fields": [{"name": "PO number", "value": "PO-7781"}]}
    assert "Billing details saved." in html
    assert 'value="PO-7781"' in html
    assert "GBP 588.00" in html and "GBP 98.00" in html
    assert "https://pay.stripe.com/invoice/ENT-0001/pdf" in html
    # An invoice whose tax the provider did not report shows a dash, not 0.00.
    assert "USD 0.00" not in html


def test_billing_page_uses_the_switched_organisation(app, db_session, client, login_as, no_billing):
    from app.models.org_role import OrgRole
    from app.models.subscription import SubscriptionPlan

    home_org, admin = _admin_org(db_session, "home")
    switched_org = _org(db_session, "second")
    # Billing authority follows the ACTIVE organisation: the viewer must be an
    # org_admin of the organisation they switched into (an architect grant
    # there is covered by the refusal test below).
    OrgRole.set_role(switched_org.id, admin.id, "org_admin", granted_by_id=admin.id)
    _subscription(db_session, switched_org, plan=SubscriptionPlan.team, seats_purchased=20)
    db_session.commit()

    with app.app_context():
        login_as(client, admin)
        switched = client.post(
            "/account/switch-organization",
            data={"organization_id": str(switched_org.id)},
            follow_redirects=True,
        )
        login_as(client, admin)
        page = client.get("/admin/billing/")

    switched_html = switched.get_data(as_text=True)
    html = page.get_data(as_text=True)

    assert switched.status_code == 200
    assert page.status_code == 200
    assert f"Active: {switched_org.name}" in switched_html
    assert switched_org.name in html
    assert home_org.name not in html
    assert 'data-testid="billing-current-plan">Team<' in html


def test_home_org_admin_switched_into_another_org_is_refused_its_billing(
    app, db_session, client, login_as, no_billing
):
    """Active-org property: administering your HOME organisation grants nothing
    over billing in an organisation you merely hold a lesser role in."""
    from app.models.org_role import OrgRole
    from app.models.subscription import SubscriptionPlan

    home_org, admin = _admin_org(db_session, "home-refused")
    switched_org = _org(db_session, "second-refused")
    OrgRole.set_role(switched_org.id, admin.id, "architect", granted_by_id=admin.id)
    _subscription(db_session, switched_org, plan=SubscriptionPlan.team, seats_purchased=20)
    db_session.commit()

    with app.app_context():
        login_as(client, admin)
        client.post("/account/switch-organization",
                    data={"organization_id": str(switched_org.id)}, follow_redirects=True)
        login_as(client, admin)
        page = client.get("/admin/billing/")
        login_as(client, admin)
        upgrade = client.post("/admin/billing/upgrade", data={"plan": "startup", "interval": "year"})

    assert page.status_code == 403
    assert upgrade.status_code == 403


def test_currency_context_and_filter_follow_the_switched_organisation(app, db_session, make_org):
    from app.models.org_role import OrgRole
    from tests._session_test_helpers import mint_test_sid

    home_org = make_org("currency-home")
    switched_org = make_org("currency-second")
    home_org.settings = {"currency_code": "USD"}
    switched_org.settings = {"currency_code": "EUR"}
    user = _user(db_session, home_org, admin=True)
    OrgRole.set_role(switched_org.id, user.id, "architect", granted_by_id=user.id)
    db_session.commit()

    sid = mint_test_sid(user.id, organization_id=home_org.id, app=app)
    with app.test_request_context("/"):
        session["_user_id"] = str(user.id)
        session["_fresh"] = True
        session["_sid"] = sid
        session["current_org_id"] = switched_org.id

        app.preprocess_request()
        rendered = render_template_string(
            "{{ active_organization_name }}|{{ currency_config.code }}|{{ 12.5|format_currency(show_code=True) }}"
        )

    assert rendered == f"{switched_org.name}|EUR|EUR 12.50€"


def test_admin_at_the_limit_is_shown_the_upgrade_instead_of_adding(app, db_session, client, login_as, no_billing):
    from app.models.user import Role, User

    org, admin = _admin_org(db_session, "full2")
    _user(db_session, org)
    _user(db_session, org)
    db_session.commit()
    role = Role.query.filter_by(name="User").first()
    password = uuid.uuid4().hex
    form = {"role": str(role.id), "first_name": "Ann", "last_name": "Other",
            "email": f"ann-{uuid.uuid4().hex[:8]}@example.com", "password": password,
            "password2": password}
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/new-user", data=form)

    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Your organisation has reached its plan limit." in html
    assert "/admin/billing/#plans" in html
    assert User.query.filter_by(email=form["email"]).first() is None


def test_admin_under_the_limit_adds_into_their_own_organisation(app, db_session, client, login_as, no_billing):
    from app.models.user import Role, User

    org, admin = _admin_org(db_session, "room")
    role = Role.query.filter_by(name="User").first()
    email = f"new-{uuid.uuid4().hex[:8]}@example.com"
    password = uuid.uuid4().hex
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/new-user", data={
            "role": str(role.id), "first_name": "New", "last_name": "Person", "email": email,
            "password": password, "password2": password})
    assert resp.status_code == 200
    created = User.query.filter_by(email=email).one()
    assert created.organization_id == org.id


def test_pricing_page_has_a_buy_button_per_plan(app, client):
    with app.app_context():
        resp = client.get("/pricing")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    # Buy buttons route through the click-tracking redirect
    # (app/main/views.py::track_plan_click) before landing on registration, so
    # the plan-preserving registration URL now travels as the redirect's
    # "next" parameter.
    assert "/t/plan-click?plan=startup&amp;next=/account/register?plan%3Dstartup%26interval%3Dyear" in html
    assert "/t/plan-click?plan=team&amp;next=/account/register?plan%3Dteam%26interval%3Dyear" in html
    assert "/t/plan-click?plan=team&amp;next=/account/register?plan%3Dteam%26interval%3Dmonth" in html
    assert 'data-testid="buy-enterprise"' in html


def test_pricing_contact_sales_button_points_at_the_contact_page(app, client):
    """'Contact sales' leads to /contact (via the click-tracking redirect,
    app/main/views.py::track_plan_click), which now carries the sales enquiry
    form rather than the old pre-launch waiting list."""
    import re

    resp = client.get("/pricing")
    html = resp.get_data(as_text=True)
    assert re.search(
        r'<a href="/t/plan-click\?plan=enterprise&amp;next=/contact"[^>]*data-testid="buy-enterprise"', html)


def test_signing_in_returns_the_visitor_to_the_plan_they_chose(app, db_session, client, login_as, no_billing):
    org, admin = _admin_org(db_session, "next")
    with app.app_context():
        resp = client.get("/admin/billing/?plan=team&interval=month")
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert "/account/login?next=" in location
    from urllib.parse import parse_qs, urlparse

    next_path = parse_qs(urlparse(location).query)["next"][0]
    assert next_path == "/admin/billing/?plan=team&interval=month"
    from app.utils.safe_redirect import is_safe_next_url

    assert is_safe_next_url(next_path)


def test_an_event_is_matched_by_customer_never_by_its_own_org_reference(app, db_session, client, billing):
    """Another installation on the same provider account sends its own org ids."""
    from app.models.subscription import BillingEvent, SubscriptionPlan

    org = _org(db_session, "ref")
    _subscription(db_session, org, stripe_customer_id="cus_ref")
    db_session.commit()
    foreign = _stripe_sub("cus_elsewhere", price="price_team_year", quantity=99)
    foreign["metadata"] = {"org_id": str(org.id)}
    checkout = {"object": "checkout.session", "customer": "cus_ref",
                "client_reference_id": str(org.id + 100000), "subscription": "sub_foreign"}

    with app.app_context():
        first = _post_event(client, _event("customer.subscription.created", foreign))
        second = _post_event(client, _event("checkout.session.completed", checkout))

    assert (first.status_code, second.status_code) == (200, 200)
    sub = _fresh(db_session, org)
    assert sub.plan == SubscriptionPlan.free and sub.stripe_subscription_id is None
    assert BillingEvent.query.filter_by(organization_id=org.id).count() == 0


def test_a_late_payment_failure_does_not_mark_a_recovered_subscription_past_due(app, db_session, client, billing):
    from app.models.subscription import SubscriptionPlan, SubscriptionStatus

    org = _org(db_session, "lateinv")
    _subscription(db_session, org, plan=SubscriptionPlan.startup,
                  stripe_customer_id="cus_late", stripe_subscription_id="sub_late")
    db_session.commit()
    invoice = {"customer": "cus_late", "subscription": "sub_late"}
    now = int(time.time())

    with app.app_context():
        _post_event(client, _event("invoice.paid", invoice, created=now))
        # The failure that payment recovered from, delivered afterwards.
        late = _post_event(client, _event("invoice.payment_failed", invoice, created=now - 600))
    assert late.status_code == 200
    assert _fresh(db_session, org).status == SubscriptionStatus.active

    with app.app_context():
        _post_event(client, _event("invoice.payment_failed", invoice, created=now + 600))
    assert _fresh(db_session, org).status == SubscriptionStatus.past_due


def _platform_admin(db_session):
    org = _org(db_session, "platform")
    admin = _user(db_session, org, admin=True)
    admin.is_platform_admin = True
    db_session.commit()
    return admin


def test_reading_limits_on_list_and_billing_pages_writes_nothing(app, db_session, client, login_as, no_billing):
    from app.models.subscription import Subscription

    org, admin = _admin_org(db_session, "readonly")
    platform = _platform_admin(db_session)
    with app.app_context():
        login_as(client, admin)
        assert client.get("/admin/billing/").status_code == 200
        login_as(client, admin)
        assert client.get("/admin/new-user").status_code == 200
        login_as(client, platform)
        assert client.get("/admin/organizations").status_code == 200
        login_as(client, platform)
        assert client.get(f"/admin/organizations/{org.id}").status_code == 200
    db_session.expire_all()
    assert Subscription.query.filter_by(organization_id=org.id).first() is None


def test_the_organisation_form_reads_and_writes_the_subscription(app, db_session, client, login_as, no_billing):
    from app.models.organization import Organization
    from app.models.subscription import SubscriptionPlan

    org, _admin = _admin_org(db_session, "form", plan=SubscriptionPlan.startup, seats_purchased=10)
    org.plan = "enterprise"  # a stale retired value must not be what the form shows
    db_session.commit()
    platform = _platform_admin(db_session)

    with app.app_context():
        login_as(client, platform)
        page = client.get(f"/admin/organizations/{org.id}/edit").get_data(as_text=True)
    assert '<option value="startup" selected>' in page
    assert '<option value="enterprise" selected>' not in page

    with app.app_context():
        login_as(client, platform)
        resp = client.post(f"/admin/organizations/{org.id}/edit",
                           data={"name": org.name, "slug": org.slug, "plan": "team", "seats": "25"})
    assert resp.status_code == 302
    sub = _fresh(db_session, org)
    assert (sub.plan, sub.seats_purchased) == (SubscriptionPlan.team, 25)
    assert db_session.get(Organization, org.id).plan == "enterprise"  # not written


def test_the_organisation_form_does_not_edit_a_plan_paid_online(app, db_session, client, login_as, no_billing):
    from app.models.subscription import SubscriptionPlan

    org, _admin = _admin_org(db_session, "formpaid", plan=SubscriptionPlan.startup, seats_purchased=1,
                             stripe_customer_id="cus_formpaid", stripe_subscription_id="sub_formpaid")
    platform = _platform_admin(db_session)
    with app.app_context():
        login_as(client, platform)
        page = client.get(f"/admin/organizations/{org.id}/edit").get_data(as_text=True)
        login_as(client, platform)
        client.post(f"/admin/organizations/{org.id}/edit",
                    data={"name": org.name, "slug": org.slug, "plan": "enterprise"})
    assert 'data-testid="org-plan-paid-online"' in page and 'name="plan"' not in page
    assert _fresh(db_session, org).plan == SubscriptionPlan.startup


def test_admin_losing_the_race_for_the_last_place_sees_the_limit(app, db_session, client, login_as, no_billing, monkeypatch):
    """The form's own pre-check saw a free place; the save is still refused."""
    from app.models.user import Role, User
    from app.services import billing_plans

    org, admin = _admin_org(db_session, "race")
    _user(db_session, org)
    _user(db_session, org)
    db_session.commit()
    real = billing_plans.user_limit_status
    monkeypatch.setattr(billing_plans, "user_limit_status",
                        lambda org_id: {**real(org_id), "limit_reached": False})
    role = Role.query.filter_by(name="User").first()
    password = uuid.uuid4().hex
    email = f"race-{uuid.uuid4().hex[:8]}@example.com"
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/new-user", data={
            "role": str(role.id), "first_name": "Late", "last_name": "Comer", "email": email,
            "password": password, "password2": password})

    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Your organisation has reached its plan limit." in html
    assert User.query.filter_by(email=email).first() is None
