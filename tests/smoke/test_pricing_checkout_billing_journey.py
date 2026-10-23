"""A visitor compares plans: the price on the card is the price they are asked
to pay at checkout and the plan the billing page shows afterwards.

Data: the loopback payment provider holds the four recorded plan prices; an
administrator of a Community organisation signs in from the buy button.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_billing_journey import _SKIP_WELCOME, _community_org, billing_server  # noqa: F401  (the one stubbed server)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PLANS = [("startup", "year"), ("startup", "month"), ("team", "year"), ("team", "month")]


def _card_price(page, plan, interval):
    """The number on the plan card for the interval the visitor chose."""
    if interval == "month":
        page.get_by_text("Monthly", exact=True).click()
    card = page.get_by_test_id("plan-card-%s" % plan)
    shown = card.locator(".pp-price > span:visible")
    expect(shown).to_have_count(1, timeout=PAGE_TIMEOUT)
    text = shown.inner_text()
    assert ("/" + interval) in text, "%s card reads %r for %s" % (plan, text, interval)
    return int(re.search(r"\$([\d,]+)", text).group(1).replace(",", "")), card


def _buy_button(card, plan, interval):
    name = "buy-%s" % plan if interval == "year" else "buy-%s-monthly" % plan
    return card.get_by_test_id(name)


@pytest.mark.parametrize("plan,interval", PLANS)
def test_the_price_on_the_card_is_the_price_at_checkout_and_the_plan_on_the_billing_page(
        browser, billing_server, app, plan, interval):
    base, _stub = billing_server
    _org_id, admin_email = _community_org(app, members=0)
    context = browser.new_context(ignore_https_errors=True)
    context.add_init_script(_SKIP_WELCOME)
    page = context.new_page()
    try:
        page.goto(base + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card_amount, card = _card_price(page, plan, interval)
        _buy_button(card, plan, interval).click()
        page.wait_for_url(lambda url: "/account/register" in url and "plan=%s" % plan in url
                          and "interval=%s" % interval in url, timeout=PAGE_TIMEOUT)
        page.get_by_test_id("register-signin").get_by_role("link", name="Sign in").click()
        page.wait_for_url(lambda url: "/account/login" in url and "plan=%s" % plan in url, timeout=PAGE_TIMEOUT)
        page.fill("#email", admin_email)
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/admin/billing" in url and "plan=%s" % plan in url, timeout=PAGE_TIMEOUT)

        checkout = page.locator("#checkout")
        name = "Startup" if plan == "startup" else "Team"
        expect(checkout.get_by_role("heading", name="Buy %s" % name)).to_be_visible(timeout=PAGE_TIMEOUT)
        price = page.get_by_test_id("checkout-price")
        expect(price).to_contain_text("per %s" % interval, timeout=PAGE_TIMEOUT)
        amount = re.search(r"([\d,]+\.\d\d)", price.inner_text())
        assert amount, "checkout shows no price: %r" % price.inner_text()
        assert float(amount.group(1).replace(",", "")) == float(card_amount), (
            "%s %s: the card says $%s, checkout asks %s" % (plan, interval, card_amount, price.inner_text()))
        if plan == "team":
            expect(price).to_contain_text("per editor")
        expect(page.get_by_test_id("checkout-submit")).to_be_visible()

        # Afterwards, once the provider has reported the subscription, the billing
        # page names that plan and interval and marks its card as the current one.
        _record_subscription(app, _org_id, plan, interval)
        page.goto(base + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("billing-current-plan")).to_have_text(name, timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("billing-page")).to_contain_text(
            "Billed annually" if interval == "year" else "Billed monthly")
        expect(page.get_by_test_id("plan-card-%s" % plan)).to_contain_text("Current plan")
        if plan == "team":
            # Changing seats opens the same checkout, still at the card price.
            page.get_by_test_id("plan-card-team").get_by_role("link", name="Change seats").click()
            again = page.get_by_test_id("checkout-price")
            expect(again).to_contain_text("per %s" % interval, timeout=PAGE_TIMEOUT)
            assert float(re.search(r"([\d,]+\.\d\d)", again.inner_text()).group(1).replace(",", "")) == float(card_amount)
    finally:
        context.close()


def _record_subscription(app, org_id, plan, interval):
    """What the provider report does to the stored subscription."""
    from app import db
    from app.models.organization import Organization
    from app.models.subscription import SubscriptionPlan, SubscriptionStatus
    from app.services import billing_plans

    with app.app_context():
        org = db.session.get(Organization, org_id)
        sub = billing_plans.ensure_subscription(org)
        sub.plan = SubscriptionPlan[plan]
        sub.billing_interval = interval
        sub.status = SubscriptionStatus("active")
        if plan == "team":
            sub.seats_purchased = 15
        sub.stripe_subscription_id = "sub_smoke_%s_%s" % (plan, interval)
        db.session.commit()
        db.session.remove()


def test_the_price_that_checkout_asks_is_read_from_the_payment_provider(browser, billing_server, app):
    """Change one plan price at the provider only: the card keeps its list price,
    so the two disagree and the visitor can see which one they will be charged."""
    base, stub = billing_server
    _org_id, admin_email = _community_org(app, members=0)
    original = stub.prices["price_smoke_team_month"]
    stub.prices["price_smoke_team_month"] = (3900, "month")
    context = browser.new_context(ignore_https_errors=True)
    context.add_init_script(_SKIP_WELCOME)
    page = context.new_page()
    try:
        page.goto(base + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card_amount, card = _card_price(page, "team", "month")
        _buy_button(card, "team", "month").click()
        page.get_by_test_id("register-signin").get_by_role("link", name="Sign in").click()
        page.fill("#email", admin_email)
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/admin/billing" in url, timeout=PAGE_TIMEOUT)
        amount = re.search(r"([\d,]+\.\d\d)", page.get_by_test_id("checkout-price").inner_text())
        assert float(amount.group(1)) == 39.0, "checkout did not read the provider price"
        assert float(amount.group(1)) != float(card_amount)
    finally:
        stub.prices["price_smoke_team_month"] = original
        context.close()


def test_community_goes_straight_to_registration_and_enterprise_to_a_contact_page(browser, live_server):
    context = browser.new_context(ignore_https_errors=True)
    context.add_init_script(_SKIP_WELCOME)
    page = context.new_page()
    try:
        page.goto(live_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("plan-card-community").get_by_test_id("buy-community").click()
        page.wait_for_url(lambda url: "/account/register" in url, timeout=PAGE_TIMEOUT)
        assert "plan=" not in page.url or "plan=free" in page.url or "plan=community" in page.url, page.url
        expect(page.get_by_role("heading", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator("#email")).to_be_visible()

        page.goto(live_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("plan-card-enterprise").get_by_test_id("buy-enterprise").click()
        page.wait_for_url(re.compile(r"/contact"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator("body")).not_to_contain_text("Page not found")
        assert len(" ".join(page.locator("main, #main-content").first.inner_text().split())) > 80
    finally:
        context.close()
