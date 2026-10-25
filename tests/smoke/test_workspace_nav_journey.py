"""Connections and Billing have a lasting place in the sidebar: Connections at the bottom, Billing in the account menu.

The people: a startup CTO who has just signed up (the owner of a new organisation)
and an enterprise architect who administers theirs. The goal: find where to connect
GitHub or Jira, and where to see the plan and payment, from any page, and land on a
page with content. A colleague who may not open those pages must not be shown them.
"""
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SIDEBAR_ALL = "#admin-sidebar"


def _sign_up_owner():
    """A startup CTO, registered exactly as the sign-up form registers one."""
    from app import create_app, db
    from app.models.user import Role
    from app.modules.account.services.account_service import AccountService

    app = create_app("testing")
    email = "owner.%s@example.com" % uuid.uuid4().hex[:8]
    with app.test_request_context():
        Role.insert_roles()
        user = AccountService.register_user("Nora", "Owner", email, PASSWORD)
        out = {"email": email, "org_id": user.organization_id}
        db.session.remove()
    return out


def _assert_startup_cto(email):
    from app import create_app
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.persona_lens == User.DEFAULT_PERSONA_LENS, user.persona_lens
        assert not user.is_platform_admin
        assert user.is_org_admin


def _assert_enterprise_architect(email, administers):
    from app import create_app
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "enterprise_architect", user.enterprise_role
        assert not user.is_platform_admin
        assert bool(user.is_org_admin) is administers


@pytest.fixture(scope="module")
def world():
    owner = _sign_up_owner()
    architects = create_fresh_org(
        "enterprise_architect", org_admin=True, platform_role="Administrator",
        extra_roles=("business_architect",))
    return {"owner": owner, "architects": architects}


def _walk_connections(page):
    """Click Connections at the bottom of the sidebar and check it opens a page with content."""
    link = page.locator('%s a[data-nav-key="connections"]' % SIDEBAR_ALL)
    expect(link).to_have_count(1)
    expect(link).to_be_visible()
    link.click()
    page.wait_for_url(lambda url: "/admin/connectors" in url, timeout=PAGE_TIMEOUT)
    expect(page.locator("main").first).to_contain_text("GitHub")
    expect(page.locator('%s a[data-nav-key="connections"]' % SIDEBAR_ALL)).to_have_attribute("aria-current", "page")
    assert "403" not in page.title() and "Not Found" not in page.title()


def _walk_billing(page):
    """Open the account menu in the sidebar footer, choose Billing, and read the plan."""
    page.get_by_test_id("user-menu").click()
    billing = page.get_by_test_id("user-menu-billing")
    expect(billing).to_be_visible()
    billing.click()
    page.wait_for_url(lambda url: "/admin/billing" in url, timeout=PAGE_TIMEOUT)
    expect(page.locator("main").first).to_contain_text("Plan")


def _journey(page, base, email):
    sign_in(page, base, email)
    page.goto(base + "/dashboard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator(SIDEBAR_ALL)).to_contain_text("Connections")
    _walk_connections(page)
    _walk_billing(page)
    # Still there from a page deep in the product, not only from the overview.
    page.goto(base + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator('%s a[data-nav-key="connections"]' % SIDEBAR_ALL)).to_be_visible()
    page.get_by_test_id("user-menu").click()
    expect(page.get_by_test_id("user-menu-billing")).to_be_visible()


def test_startup_cto_finds_connections_and_billing(page, live_server, world):
    _assert_startup_cto(world["owner"]["email"])
    _journey(page, live_server, world["owner"]["email"])


def test_enterprise_architect_finds_connections_and_billing(page, live_server, world):
    email = world["architects"]["emails"]["enterprise_architect"]
    _assert_enterprise_architect(email, administers=True)
    _journey(page, live_server, email)


def _assert_persona(email, persona, administers):
    from app import create_app
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == persona, user.enterprise_role
        assert not user.is_platform_admin
        assert bool(user.is_org_admin) is administers


@pytest.fixture(scope="module")
def colleagues():
    """An organisation like the demonstration one: the architects hold the
    Viewer platform role and do not administer the organisation."""
    return create_fresh_org(
        "enterprise_architect", org_admin=False, platform_role="Viewer",
        extra_roles=("business_architect", "security_architect"))


def _reaches_connections_not_billing(page, base, email):
    sign_in(page, base, email)
    page.goto(base + "/dashboard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("user-menu").click()
    expect(page.get_by_test_id("user-menu-account")).to_be_visible()
    expect(page.get_by_test_id("user-menu-billing")).to_have_count(0)
    page.keyboard.press("Escape")
    _walk_connections(page)
    page.goto(base + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("main").first).not_to_contain_text("Plan")


def test_enterprise_architect_who_does_not_administer_finds_connections_only(page, live_server, colleagues):
    email = colleagues["emails"]["enterprise_architect"]
    _assert_persona(email, "enterprise_architect", administers=False)
    _reaches_connections_not_billing(page, live_server, email)


def test_business_architect_finds_connections_only(page, live_server, colleagues):
    email = colleagues["emails"]["business_architect"]
    _assert_persona(email, "business_architect", administers=False)
    _reaches_connections_not_billing(page, live_server, email)


def test_security_architect_is_shown_neither(page, live_server, colleagues):
    email = colleagues["emails"]["security_architect"]
    _assert_persona(email, "security_architect", administers=False)
    sign_in(page, live_server, email)
    page.goto(live_server + "/dashboard/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    nav = page.locator(SIDEBAR_ALL)
    expect(nav).not_to_contain_text("Connections")
    page.get_by_test_id("user-menu").click()
    expect(page.get_by_test_id("user-menu-billing")).to_have_count(0)
    page.goto(live_server + "/admin/connectors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("main").first).not_to_contain_text("GitHub")


def test_owner_reads_the_plans_prices_and_a_plain_renewal_line(page, live_server, world):
    """The owner opens Billing: every plan shows what it costs, the same figures
    /pricing shows; the renewal tile says there is no renewal yet instead of a
    dash; the sidebar still carries the Entelim name."""
    _assert_startup_cto(world["owner"]["email"])
    sign_in(page, live_server, world["owner"]["email"])
    page.goto(live_server + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("#admin-sidebar .sidebar-app-name")).to_contain_text("Entelim")
    expect(page.get_by_test_id("billing-renewal")).to_have_text("No renewal yet")
    shown = {}
    for key in ("free", "startup", "team", "enterprise"):
        shown[key] = " ".join(page.get_by_test_id("plan-price-%s" % key).inner_text().split())
        assert shown[key], key
    assert shown["startup"] == "$490 a year $49 a month"
    assert shown["team"] == "$290 per editor a year $29 per editor a month"
    assert shown["enterprise"] == "From $24,000 a year"
    page.goto(live_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    pricing = page.locator("main").inner_text()
    for figure in ("$490", "$49", "$290", "$29", "$24,000"):
        assert figure in pricing, figure
    # Choosing a plan lands on its checkout step, which says plainly what to do.
    page.goto(live_server + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("plan-card-team").get_by_role("link", name="Choose Team").click()
    page.wait_for_url(lambda url: "plan=team" in url, timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("checkout-unavailable")).to_contain_text("Contact us")
