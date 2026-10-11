"""Monelytics billing provider: token handling, checkout, refresh and errors.

Every test here is fully offline: app.services.monelytics_provider.requests
is replaced with a fake that answers from an in-memory routing table, so
nothing reaches a real host, staging or otherwise. See
tests/test_billing_plans_and_checkout.py for the direct-Stripe path and the
fallback behaviour this provider must leave untouched.
"""

import uuid

import pytest

MONELYTICS_ENV = (
    "MONELYTICS_BASE_URL",
    "MONELYTICS_KEYCLOAK_TOKEN_URL",
    "MONELYTICS_CLIENT_ID",
    "MONELYTICS_CLIENT_SECRET",
)
BASE_URL = "https://monelytics-staging-api.example.test"
TOKEN_URL = "https://auth-staging.example.test/realms/master/protocol/openid-connect/token"


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.text = text or (str(json_body) if json_body is not None else "")
        self.content = b"x" if (json_body is not None or text) else b""

    def json(self):
        if self._json is None:
            raise ValueError("no body")
        return self._json


@pytest.fixture
def no_monelytics(monkeypatch):
    for name in MONELYTICS_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def monelytics_env(monkeypatch):
    """The four settings present; no network call is made just from this."""
    from app.services import monelytics_provider

    monkeypatch.setenv("MONELYTICS_BASE_URL", BASE_URL)
    monkeypatch.setenv("MONELYTICS_KEYCLOAK_TOKEN_URL", TOKEN_URL)
    monkeypatch.setenv("MONELYTICS_CLIENT_ID", "unit-test-client")
    monkeypatch.setenv("MONELYTICS_CLIENT_SECRET", "unit-test-secret")
    monelytics_provider.reset_token_cache()
    yield
    monelytics_provider.reset_token_cache()


def _token_response(token="token-1", expires_in=3600):
    return FakeResponse(200, {"access_token": token, "expires_in": expires_in, "token_type": "Bearer"})


def _plans_response(plan_code="STARTUP", variant_interval="monthly",
                     plan_id="plan-startup", variant_id="variant-startup-month"):
    return FakeResponse(200, [{
        "id": plan_id,
        "code": plan_code,
        "product_code": "ENTELIM",
        "price": 49.0,
        "name": plan_code.title(),
        "features": [],
        "variants": [{"id": variant_id, "interval": variant_interval, "price": 49.0}],
    }])


def _checkout_response(sub_id="mon-sub-1", checkout_url="https://pay.example.test/c/mon-sub-1"):
    return FakeResponse(202, {
        "subscription": {"id": sub_id, "status": "pending"},
        "checkoutUrl": checkout_url,
        "requiresAction": True,
        "activationState": "pending",
        "trialEligible": False,
        "trialDays": 0,
        "trialAlreadyUsed": False,
        "requiresImmediatePayment": True,
    })


def _subscription_response(sub_id="mon-sub-1", plan_code="STARTUP", interval="monthly",
                            status="active", seats=1, period_end="2030-01-01T00:00:00Z",
                            cancel_at_period_end=False):
    return FakeResponse(200, {
        "id": sub_id,
        "tenantId": "entelim-org-1",
        "productId": "ENTELIM",
        "planId": "plan-startup",
        "planCode": plan_code,
        "billingInterval": interval,
        "status": status,
        "seatCount": seats,
        "subscriptionPrice": 49.0,
        "currentPeriodStart": "2026-01-01T00:00:00Z",
        "currentPeriodEnd": period_end,
        "paymentProvider": "stripe",
        "cancelAtPeriodEnd": cancel_at_period_end,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    })


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Monelytics {label} {suffix}", slug=f"monelytics-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, *, admin=False, platform=False):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Administrator" if admin else "User").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator" if admin else "User").first()
    user = User(
        email=f"monelytics-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Mon",
        last_name="Admin",
        organization_id=org.id,
        role=role,
        is_org_admin=admin,
        is_platform_admin=platform,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _admin_org(db_session, label):
    org = _org(db_session, label)
    admin = _user(db_session, org, admin=True)
    db_session.commit()
    return org, admin


# --------------------------------------------------------------------------- #
# Token fetch and caching                                                      #
# --------------------------------------------------------------------------- #


def test_token_is_fetched_once_and_cached(monelytics_env, monkeypatch):
    from app.services import monelytics_provider

    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        assert url == TOKEN_URL
        return _token_response()

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)

    first = monelytics_provider._get_token()
    second = monelytics_provider._get_token()

    assert first == second == "token-1"
    assert len(calls) == 1  # the second call reused the cached token


def test_token_is_refetched_once_it_expires(monelytics_env, monkeypatch):
    from app.services import monelytics_provider

    tokens = iter(["token-1", "token-2"])
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        return _token_response(token=next(tokens))

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)

    first = monelytics_provider._get_token()
    # Simulate time passing past the cached expiry, rather than racing the
    # clock with a tiny real expires_in.
    monelytics_provider._token_cache["expires_at"] = 0.0
    second = monelytics_provider._get_token()

    assert (first, second) == ("token-1", "token-2")
    assert len(calls) == 2


def test_a_401_triggers_one_forced_token_refresh_and_retry(monelytics_env, monkeypatch):
    from app.services import monelytics_provider

    tokens = iter(["stale-token", "fresh-token"])
    token_calls = []
    api_calls = []

    def fake_request(method, url, **kwargs):
        if url == TOKEN_URL:
            token_calls.append(url)
            return _token_response(token=next(tokens))
        api_calls.append(kwargs.get("headers", {}).get("Authorization"))
        if kwargs["headers"]["Authorization"] == "Bearer stale-token":
            return FakeResponse(401, {"error": "token expired"})
        return _plans_response()

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)

    result = monelytics_provider._request(
        "GET", monelytics_provider._PATH_PLANS, tenant_id="entelim-org-1"
    )

    assert len(token_calls) == 2  # the first (cached-and-wrong) token, then a forced refresh
    assert api_calls == ["Bearer stale-token", "Bearer fresh-token"]
    assert result[0]["code"] == "STARTUP"


def test_not_configured_without_the_four_settings(no_monelytics):
    from app.services import monelytics_provider

    assert monelytics_provider.configured() is False
    status = monelytics_provider.configuration_status()
    assert status["ready"] is False
    assert set(status["missing"]) == set(monelytics_provider.SETTINGS)


# --------------------------------------------------------------------------- #
# Tenant id mapping                                                            #
# --------------------------------------------------------------------------- #


def test_tenant_id_maps_1to1_onto_the_organisation(db_session):
    from app.services import monelytics_provider

    one, two = _org(db_session, "one"), _org(db_session, "two")
    db_session.commit()

    assert monelytics_provider.tenant_id_for(one) == monelytics_provider.tenant_id_for(one)
    assert monelytics_provider.tenant_id_for(one) != monelytics_provider.tenant_id_for(two)
    assert str(one.id) in monelytics_provider.tenant_id_for(one)


# --------------------------------------------------------------------------- #
# Checkout request shape                                                      #
# --------------------------------------------------------------------------- #


def test_start_checkout_sends_the_tenant_id_and_chosen_plan(db_session, monelytics_env, monkeypatch):
    from app.services import monelytics_provider

    org = _org(db_session, "checkout")
    db_session.commit()
    captured = {}

    def fake_request(method, url, **kwargs):
        if url == TOKEN_URL:
            return _token_response()
        if url.endswith("/api/billing/plans"):
            return _plans_response(plan_code="TEAM", variant_interval="yearly",
                                   plan_id="plan-team", variant_id="variant-team-year")
        if url.endswith("/api/billing/subscriptions"):
            captured["method"] = method
            captured["headers"] = dict(kwargs["headers"])
            captured["json"] = kwargs["json"]
            return _checkout_response()
        raise AssertionError(f"unexpected call to {url}")

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)

    url = monelytics_provider.start_checkout(
        org, "team", "year", 7, "https://entelim.example/admin/billing",
        "https://entelim.example/admin/billing/?plan=team",
    )

    assert url == "https://pay.example.test/c/mon-sub-1"
    assert captured["method"] == "POST"
    assert captured["headers"]["X-Tenant-Id"] == f"entelim-org-{org.id}"
    assert captured["headers"]["Authorization"] == "Bearer token-1"
    body = captured["json"]
    assert body["tenantId"] == f"entelim-org-{org.id}"
    assert body["productCode"] == "ENTELIM"
    assert body["planId"] == "plan-team"
    assert body["variantId"] == "variant-team-year"
    assert body["paymentProvider"] == "stripe"
    assert body["seatCount"] == 7
    assert body["successUrl"] == "https://entelim.example/admin/billing"
    assert body["cancelUrl"] == "https://entelim.example/admin/billing/?plan=team"


def test_start_checkout_omits_seat_count_for_a_flat_plan(db_session, monelytics_env, monkeypatch):
    from app.services import monelytics_provider

    org = _org(db_session, "flat")
    db_session.commit()
    captured = {}

    def fake_request(method, url, **kwargs):
        if url == TOKEN_URL:
            return _token_response()
        if url.endswith("/api/billing/plans"):
            return _plans_response()
        captured["json"] = kwargs["json"]
        return _checkout_response()

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)
    monelytics_provider.start_checkout(org, "startup", "month", None, "https://x/ok", "https://x/cancel")

    assert "seatCount" not in captured["json"]


# --------------------------------------------------------------------------- #
# Error handling                                                               #
# --------------------------------------------------------------------------- #


def test_unreachable_provider_raises_a_readable_error(db_session, monelytics_env, monkeypatch):
    import requests as requests_module

    from app.services import monelytics_provider

    def fake_request(method, url, **kwargs):
        raise requests_module.ConnectionError("connection refused")

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)
    org = _org(db_session, "down")
    db_session.commit()

    with pytest.raises(monelytics_provider.MonelyticsError, match="could not be reached"):
        monelytics_provider.start_checkout(org, "startup", "year", None, "https://x/ok", "https://x/cancel")


def test_provider_error_message_is_surfaced(db_session, monelytics_env, monkeypatch):
    from app.services import monelytics_provider

    def fake_request(method, url, **kwargs):
        if url == TOKEN_URL:
            return _token_response()
        if url.endswith("/api/billing/plans"):
            return _plans_response(variant_interval="yearly")
        return FakeResponse(409, {"error": "An active or pending subscription already exists"})

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)
    org = _org(db_session, "conflict")
    db_session.commit()

    with pytest.raises(monelytics_provider.MonelyticsError,
                       match="An active or pending subscription already exists"):
        monelytics_provider.start_checkout(org, "startup", "year", None, "https://x/ok", "https://x/cancel")


def test_plan_not_yet_set_up_on_monelytics_is_a_readable_error(db_session, monelytics_env, monkeypatch):
    from app.services import monelytics_provider

    def fake_request(method, url, **kwargs):
        if url == TOKEN_URL:
            return _token_response()
        return FakeResponse(200, [])  # no plans at all yet

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)
    org = _org(db_session, "noplans")
    db_session.commit()

    with pytest.raises(monelytics_provider.MonelyticsError, match="not set up on Monelytics yet"):
        monelytics_provider.start_checkout(org, "startup", "year", None, "https://x/ok", "https://x/cancel")


def test_403_raises_the_not_yet_authorised_error_not_the_generic_refusal(
    db_session, monelytics_env, monkeypatch
):
    from app.services import monelytics_provider

    def fake_request(method, url, **kwargs):
        if url == TOKEN_URL:
            return _token_response()
        if url.endswith("/api/billing/plans"):
            return _plans_response()
        return FakeResponse(403, {"error": "Forbidden"})

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)
    org = _org(db_session, "notpermitted")
    db_session.commit()

    with pytest.raises(monelytics_provider.MonelyticsError) as exc_info:
        monelytics_provider.start_checkout(org, "startup", "month", None, "https://x/ok", "https://x/cancel")

    assert str(exc_info.value) == monelytics_provider.NOT_PERMITTED_YET
    assert str(exc_info.value) != monelytics_provider.PROVIDER_REFUSED


def test_refresh_returns_none_when_monelytics_has_no_subscription(db_session, monelytics_env, monkeypatch):
    from app.services import monelytics_provider

    def fake_request(method, url, **kwargs):
        if url == TOKEN_URL:
            return _token_response()
        return FakeResponse(404, {"error": "Subscription not found"})

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)
    org = _org(db_session, "fresh")
    db_session.commit()

    assert monelytics_provider.refresh_subscription(org) is None


# --------------------------------------------------------------------------- #
# Through BillingService / the billing page                                   #
# --------------------------------------------------------------------------- #


def test_without_the_other_three_settings_the_org_admin_sees_the_generic_banner(
    app, db_session, client, login_as, monelytics_env, monkeypatch
):
    monkeypatch.delenv("MONELYTICS_CLIENT_SECRET", raising=False)
    org, admin = _admin_org(db_session, "orgadmin")
    with app.app_context():
        login_as(client, admin)
        page = client.get("/admin/billing/")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Online payment is not set up on this installation" in html
    assert "MONELYTICS_CLIENT_SECRET" not in html
    assert "Settings the operator has not provided" not in html


def test_without_the_other_three_settings_a_platform_admin_sees_which_are_missing(
    app, db_session, client, login_as, monelytics_env, monkeypatch
):
    monkeypatch.delenv("MONELYTICS_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("MONELYTICS_CLIENT_ID", raising=False)
    org = _org(db_session, "platform")
    platform = _user(db_session, org, admin=True, platform=True)
    db_session.commit()
    with app.app_context():
        login_as(client, platform)
        page = client.get("/admin/billing/")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "MONELYTICS_CLIENT_SECRET" in html
    assert "MONELYTICS_CLIENT_ID" in html


def test_buy_starts_monelytics_checkout_and_returns_to_the_billing_index(
    app, db_session, client, login_as, monelytics_env, monkeypatch
):
    org, admin = _admin_org(db_session, "buy")
    captured = {}

    def fake_request(method, url, **kwargs):
        if url == TOKEN_URL:
            return _token_response()
        if url.endswith("/api/billing/plans"):
            return _plans_response()
        captured["json"] = kwargs["json"]
        return _checkout_response(checkout_url="https://pay.example.test/c/buy")

    from app.services import monelytics_provider

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/billing/upgrade", data={"plan": "startup", "interval": "month"})

    assert resp.status_code == 303
    assert resp.headers["Location"] == "https://pay.example.test/c/buy"
    # Both URLs land back on the billing index, never on /checkout/complete:
    # there is no Monelytics-specific session id for that route to look up.
    assert captured["json"]["successUrl"].endswith("/admin/billing/")
    assert "/checkout/complete" not in captured["json"]["successUrl"]


def test_buy_never_calls_stripe_directly(app, db_session, client, login_as, monelytics_env, monkeypatch):
    import app.services.billing_service as billing_service_module

    org, admin = _admin_org(db_session, "nostripe")

    def _never(*args, **kwargs):
        raise AssertionError("the Monelytics checkout path must not call Stripe directly")

    monkeypatch.setattr(billing_service_module, "_api", _never)

    def fake_request(method, url, **kwargs):
        if url == TOKEN_URL:
            return _token_response()
        if url.endswith("/api/billing/plans"):
            return _plans_response()
        return _checkout_response()

    from app.services import monelytics_provider

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/billing/upgrade", data={"plan": "startup", "interval": "month"})

    assert resp.status_code == 303


def test_checkout_error_is_shown_without_crashing(
    app, db_session, client, login_as, monelytics_env, monkeypatch
):
    import requests as requests_module

    org, admin = _admin_org(db_session, "unreachable")

    def fake_request(method, url, **kwargs):
        raise requests_module.ConnectionError("connection refused")

    from app.services import monelytics_provider

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/billing/upgrade", data={"plan": "startup", "interval": "month"},
                           follow_redirects=True)

    assert resp.status_code == 200
    assert "could not be reached" in resp.get_data(as_text=True)


def test_billing_page_refreshes_plan_and_limits_from_monelytics(
    app, db_session, client, login_as, monelytics_env, monkeypatch
):
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    org, admin = _admin_org(db_session, "refresh")
    db_session.add(Subscription(organization_id=org.id, plan=SubscriptionPlan.free,
                                status=SubscriptionStatus.active, seats_purchased=3))
    db_session.commit()

    def fake_request(method, url, **kwargs):
        if url == TOKEN_URL:
            return _token_response()
        if url.endswith("/api/billing/subscriptions/ENTELIM"):
            return _subscription_response(plan_code="STARTUP", interval="monthly", status="active")
        raise AssertionError(f"unexpected call to {url}")

    from app.services import monelytics_provider

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)
    with app.app_context():
        login_as(client, admin)
        page = client.get("/admin/billing/")

    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'data-testid="billing-current-plan">Startup<' in html

    db_session.expire_all()
    sub = Subscription.query.filter_by(organization_id=org.id).one()
    assert sub.plan == SubscriptionPlan.startup
    assert sub.status == SubscriptionStatus.active
    assert sub.billing_interval == "month"
    assert sub.monelytics_subscription_id == "mon-sub-1"
    from app.services.billing_plans import user_limit_status
    assert user_limit_status(org.id)["limit"] == 10


def test_refresh_failure_shows_an_error_but_the_page_still_renders(
    app, db_session, client, login_as, monelytics_env, monkeypatch
):
    import requests as requests_module

    org, admin = _admin_org(db_session, "refreshfail")

    def fake_request(method, url, **kwargs):
        if url == TOKEN_URL:
            return _token_response()
        raise requests_module.ConnectionError("connection refused")

    from app.services import monelytics_provider

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)
    with app.app_context():
        login_as(client, admin)
        page = client.get("/admin/billing/")

    assert page.status_code == 200
    assert "could not be reached" in page.get_data(as_text=True)


def test_a_live_monelytics_subscription_blocks_a_second_checkout(
    app, db_session, client, login_as, monelytics_env, monkeypatch
):
    from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus

    org, admin = _admin_org(db_session, "live")
    db_session.add(Subscription(organization_id=org.id, plan=SubscriptionPlan.startup,
                                status=SubscriptionStatus.active, seats_purchased=10,
                                monelytics_subscription_id="mon-sub-existing"))
    db_session.commit()

    def fake_request(method, url, **kwargs):
        if url == TOKEN_URL:
            return _token_response()
        if url.endswith("/api/billing/subscriptions/ENTELIM"):
            return _subscription_response(sub_id="mon-sub-existing")
        raise AssertionError(f"checkout must not start a second time: {url}")

    from app.services import monelytics_provider

    monkeypatch.setattr(monelytics_provider.requests, "request", fake_request)
    with app.app_context():
        login_as(client, admin)
        resp = client.post("/admin/billing/upgrade", data={"plan": "team", "interval": "year"},
                           follow_redirects=True)

    assert "Your organisation already has a subscription. Use Change plan to switch." in resp.get_data(as_text=True)
