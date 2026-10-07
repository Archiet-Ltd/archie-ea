"""Buying a plan: the pricing page's buy button and the administrator's billing page.

The smoke server carries no payment-provider keys, so this is the journey a
visitor and an administrator take on an installation where online payment is
not set up: every buy control must lead somewhere real and say plainly that
payment is not available, never fire nothing or pretend to have worked. The
paths with keys present (checkout, change, cancel, signed events) are driven
against recorded provider responses in tests/test_billing_plans_and_checkout.py.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_pricing_buy_button_leads_a_visitor_to_that_plans_checkout(browser, live_server, seeded):
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(live_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        buy = page.get_by_test_id("buy-startup")
        expect(buy).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("buy-enterprise")).to_have_attribute("href", "/contact")

        buy.click()
        # Not signed in yet: the button opens registration and keeps the plan;
        # someone with an account signs in from there and keeps it too.
        page.wait_for_url(lambda url: "/account/register" in url and "plan=startup" in url,
                          timeout=PAGE_TIMEOUT)
        page.get_by_test_id("register-signin").get_by_role("link", name="Sign in").click()
        page.wait_for_url(lambda url: "/account/login" in url and "plan=startup" in url,
                          timeout=PAGE_TIMEOUT)
        page.fill("#email", seeded["emails"]["platform_admin"])
        page.fill("#password", PASSWORD)
        page.locator("#submit").click()
        page.wait_for_url(lambda url: "/admin/billing" in url and "plan=startup" in url,
                          timeout=PAGE_TIMEOUT)

        checkout = page.locator("#checkout")
        expect(checkout.get_by_role("heading", name="Buy Startup")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("plan-card-startup")).to_be_visible()
        # No keys on this server: say so, and offer no payment button that cannot work.
        expect(page.get_by_test_id("checkout-unavailable")).to_contain_text(
            "Online payment is not set up on this installation")
        expect(page.get_by_test_id("checkout-submit")).to_have_count(0)
    finally:
        context.close()


def test_administrator_sees_plan_limits_and_can_choose_a_plan(browser, live_server, seeded):
    from .test_archetype_journeys import _login

    context = browser.new_context()
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["platform_admin"])
        response = page.goto(live_server + "/admin/billing/", wait_until="domcontentloaded",
                             timeout=PAGE_TIMEOUT)
        assert response.status == 200

        expect(page.get_by_role("heading", name="Billing & plan", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("billing-not-configured")).to_be_visible()
        plan_name = page.get_by_test_id("billing-current-plan").inner_text().strip()
        assert plan_name in {"Community", "Startup", "Team", "Enterprise"}
        # The people figure is "<used> of <limit>" from the plan, never blank.
        people = page.get_by_test_id("billing-people-limit").inner_text().strip()
        assert " of " in people, people

        page.get_by_test_id("plan-card-team").get_by_role("link", name="Choose Team").click()
        page.wait_for_url(lambda url: "plan=team" in url, timeout=PAGE_TIMEOUT)
        expect(page.locator("#checkout").get_by_role("heading", name="Buy Team")).to_be_visible(
            timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("checkout-unavailable")).to_be_visible()

        # The state survives a reload: the chosen plan is in the address.
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#checkout").get_by_role("heading", name="Buy Team")).to_be_visible(
            timeout=PAGE_TIMEOUT)

        # Invoices and billing details say why they are empty instead of showing zeros.
        expect(page.get_by_text("Invoices are not available")).to_be_visible()
    finally:
        context.close()


def _community_org(app, members):
    """A Community organisation (three people) with an administrator and
    *members* more people; returns (org id, administrator's e-mail)."""
    import uuid

    from app import db
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        admin_role = Role.query.filter_by(name="Administrator").one()
        user_role = Role.query.filter_by(name="User").one()
        org = Organization(name="Smoke Community %s" % suffix, slug="smoke-community-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        admin = User(email="smoke.community-admin.%s@example.com" % suffix, first_name="Casey",
                     last_name="Admin", organization_id=org.id, confirmed=True, role=admin_role,
                     is_org_admin=True, enterprise_role="enterprise_architect")
        admin.password = PASSWORD
        db.session.add(admin)
        db.session.flush()
        OrgRole.set_role(org.id, admin.id, "org_admin", granted_by_id=admin.id)
        for i in range(members):
            db.session.add(User(email="smoke.community-%d.%s@example.com" % (i, suffix),
                                first_name="Member", last_name=str(i), organization_id=org.id,
                                confirmed=True, role=user_role))
        db.session.commit()
        org_id, email = org.id, admin.email
        db.session.remove()
    return org_id, email


# The first-visit welcome tour is not part of these journeys.
_SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"


def _add_person(page, base, email):
    page.goto(base + "/admin/new-user", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.select_option("#role", label="User")
    page.fill("#first_name", "Robin")
    page.fill("#last_name", "Newcomer")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    page.get_by_role("button", name="Create").click()
    page.wait_for_load_state("domcontentloaded")


def test_administrator_at_the_plan_limit_is_refused_and_nobody_is_added(browser, live_server, app):
    """Community admits three: the third person is added, the fourth is refused."""
    import uuid

    from .test_archetype_journeys import _login

    _org_id, admin_email = _community_org(app, members=1)
    third = "smoke.third.%s@example.com" % uuid.uuid4().hex[:8]
    fourth = "smoke.fourth.%s@example.com" % uuid.uuid4().hex[:8]
    context = browser.new_context()
    context.add_init_script(_SKIP_WELCOME)
    page = context.new_page()
    try:
        _login(page, live_server, admin_email)

        _add_person(page, live_server, third)
        expect(page.locator("#main-content").get_by_text("successfully created")).to_be_visible(
            timeout=PAGE_TIMEOUT)
        # Now full: the form says so before anything is typed.
        expect(page.get_by_test_id("plan-limit-reached")).to_contain_text(
            "Your organisation has reached its plan limit.")

        _add_person(page, live_server, fourth)
        refusal = page.get_by_test_id("plan-limit-reached")
        expect(refusal).to_contain_text("The Community plan admits 3 people and 3 are in use")
        expect(refusal.get_by_role("link", name="Upgrade plan")).to_be_visible()

        # After a reload of the people list: the third persisted, the fourth never did.
        page.goto(live_server + "/admin/users", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_text(third)).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(fourth)).to_have_count(0)
    finally:
        context.close()


@pytest.fixture(scope="module")
def billing_server(request, app):
    """A second server whose payment provider is a loopback stub."""
    from .conftest import boot_live_server
    from .payment_provider_stub import PaymentProviderStub

    with PaymentProviderStub() as stub:
        server = boot_live_server(request, None, app, extra_env=stub.child_environment({}))
        yield server, stub


def test_administrator_saves_billing_details_and_they_survive_a_reload(browser, billing_server, app):
    import uuid

    from .test_archetype_journeys import _login

    base, stub = billing_server
    _org_id, admin_email = _community_org(app, members=0)
    invoice_email = "accounts.%s@example.com" % uuid.uuid4().hex[:6]
    po_number = "PO-%s" % uuid.uuid4().hex[:6].upper()
    context = browser.new_context()
    context.add_init_script(_SKIP_WELCOME)
    page = context.new_page()
    try:
        _login(page, base, admin_email)
        page.goto(base + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        form = page.get_by_test_id("billing-details-form")
        expect(form).to_be_visible(timeout=PAGE_TIMEOUT)
        form.get_by_label("Invoice e-mail").fill(invoice_email)
        form.get_by_label("Purchase-order number").fill(po_number)
        form.get_by_role("button", name="Save billing details").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_test_id("billing-page").get_by_text(
            "Billing details saved. Future invoices carry them.")).to_be_visible(timeout=PAGE_TIMEOUT)

        page.reload(wait_until="domcontentloaded")
        form = page.get_by_test_id("billing-details-form")
        expect(form.get_by_label("Invoice e-mail")).to_have_value(invoice_email, timeout=PAGE_TIMEOUT)
        expect(form.get_by_label("Purchase-order number")).to_have_value(po_number)
        # Held by the provider, not only echoed by the page.
        assert any(c.get("email") == invoice_email for c in stub.customers.values())
    finally:
        context.close()
