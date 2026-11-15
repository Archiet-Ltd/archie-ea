"""The service status page is public: a customer who cannot sign in can still see whether the service is up.

Journey: someone checking whether the service is down opens /status with no
session and reads, for web, database, background jobs, AI provider and email, a
plain state and the time of the last check; a signed-in Viewer sees the same
page in the public shell (public navbar and footer, no sidebar); on a phone
nothing overflows; and no organisation's name or data is on the page.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

COMPONENTS = ("web", "database", "background-jobs", "ai-provider", "email")
STATES = ("Operational", "Degraded", "Down", "Not checked")


@pytest.fixture(scope="module")
def status_viewer(seeded):
    """A read-only (Viewer role) account in the seeded organisation."""
    from app import create_app, db
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        Role.insert_roles()
        user = User(
            email="smoke.status.%s@example.com" % uuid.uuid4().hex[:8],
            first_name="Status", last_name="Viewer",
            organization_id=seeded["ids"]["org"], enterprise_role="enterprise_architect",
            confirmed=True,
        )
        user.role = Role.query.filter_by(name="Viewer").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        return user.email


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def _open_status(page, live_server):
    response = page.goto(live_server + "/status", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return response


def _assert_the_page_answers(page, org_name):
    assert "/account/login" not in page.url, "status page sent the visitor to sign in: %s" % page.url
    expect(page.get_by_test_id("service-status-page")).to_be_visible(timeout=PAGE_TIMEOUT)
    for key in COMPONENTS:
        card = page.get_by_test_id("status-component-%s" % key)
        expect(card).to_be_visible()
        badge_text = card.get_by_test_id("status-badge-%s" % key).inner_text().strip()
        assert badge_text in STATES, "%s reads %r" % (key, badge_text)
        assert badge_text not in ("—", "-", ""), "%s reads a dash" % key
    expect(page.get_by_test_id("status-last-checked")).to_contain_text("UTC")
    # Read-only: no form, no sign-in prompt, no organisation.
    assert page.locator("main form").count() == 0
    assert page.locator("input[type=password]").count() == 0
    body = page.locator("body").inner_text()
    assert org_name not in body


def test_signed_out_visitor_reads_every_component_state(browser, live_server, seeded):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        response = _open_status(page, live_server)
        assert response.status == 200
        assert response.request.redirected_from is None, "the page redirected"
        _assert_the_page_answers(page, "Smoke Org")
        # Public shell: the public navbar and footer, not the sidebar.
        expect(page.locator("header, nav").first).to_be_visible()
        assert page.locator("footer").count() >= 1
        assert page.get_by_test_id("app-footer").count() == 0
    finally:
        context.close()


def test_signed_in_viewer_sees_the_same_page_in_the_public_shell(browser, live_server, seeded, status_viewer):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, status_viewer)
        response = _open_status(page, live_server)
        assert response.status == 200
        _assert_the_page_answers(page, "Smoke Org")
        assert page.get_by_test_id("app-sidebar").count() == 0
        assert page.locator("aside, [data-slot=sidebar]").count() == 0, "the signed-in sidebar is on the public page"
    finally:
        context.close()


def test_signed_in_footer_link_lands_on_the_status_page(browser, live_server, seeded, status_viewer):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, status_viewer)
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("footer-service-status").click()
        page.wait_for_url("**/status", timeout=PAGE_TIMEOUT)
        _assert_the_page_answers(page, "Smoke Org")
    finally:
        context.close()


def test_nothing_overflows_on_a_phone(browser, live_server, seeded):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        _open_status(page, live_server)
        expect(page.get_by_test_id("service-status-page")).to_be_visible(timeout=PAGE_TIMEOUT)
        width = page.evaluate("document.documentElement.scrollWidth")
        assert width <= 390, "page is %dpx wide at 390" % width
    finally:
        context.close()
