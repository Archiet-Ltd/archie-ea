"""An architect opens an application's fact sheet and every gap says what to do.

The goal: read what is known about one application and see, for each fact that is
missing, the words "Not set" and a link to the strip that lists what the record needs.

* an application with no owner, no vendor and no business purpose shows "Not set"
  on each, never a bare dash, and the link lands on "Missing to reach a complete record";
* the four headline cards carry no icon and show their figures;
* the two legacy owner rows appear only when they carry a value;
* the same page works at phone width.
"""

import uuid
from datetime import datetime

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DASHES = ("—", "-", "–", "−")


def _make_world(app):
    from app import db
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.organization import Organization
    from app.models.user import Role, User
    from app.services.billing_plans import set_contract_plan

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Fact %s" % suffix, slug="fact-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        db.session.commit()
        user = User(
            email="fact.%s@example.com" % suffix, first_name="Fact", last_name="Reader",
            organization_id=org.id, enterprise_role="platform_admin", confirmed=True,
        )
        user.role = Role.query.filter_by(name="Administrator").one()
        user.is_platform_admin = True
        user.is_org_admin = True
        user.password = PASSWORD
        user.onboarding_completed_at = datetime.utcnow()
        db.session.add(user)
        db.session.commit()

        bare = ApplicationComponent(name="Bare Ledger %s" % suffix, organization_id=org.id,
                                    component_type="web_application")
        full = ApplicationComponent(
            name="Full Ledger %s" % suffix, organization_id=org.id, component_type="web_application",
            description="Invoices customers.", business_criticality="high", lifecycle_status="2.1 strategic",
            business_domain="Finance", technology_stack="Python", deployment_model="cloud",
            data_classification="confidential", vendor_name="Acme Software",
            business_purpose="Bills every customer monthly.",
            legacy_business_owner="Casey Recorded", legacy_technical_owner="Robin Recorded",
        )
        db.session.add_all([bare, full])
        db.session.flush()
        db.session.add(ApplicationOwner(application_id=full.id, user_id=user.id, organization_id=org.id,
                                        ownership_type="primary"))
        db.session.commit()
        return {"email": user.email, "bare": bare.id, "full": full.id}


@pytest.fixture
def world(app, live_server):
    return _make_world(app)


def _context(browser, width, height, scheme="light"):
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    return ctx


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _open(page, base, app_id):
    response = page.goto("%s/applications/%s/fact-sheet" % (base, app_id), wait_until="domcontentloaded",
                         timeout=PAGE_TIMEOUT)
    assert response is not None and response.status == 200
    page.wait_for_selector("main h1")
    page.wait_for_timeout(800)


def _lone_dashes(page):
    return page.evaluate(
        """(dashes) => [...document.querySelectorAll('main *, [role=main] *')]
            .filter(e => e.children.length === 0 && dashes.includes((e.textContent || '').trim()))
            .map(e => e.outerHTML.slice(0, 120))""",
        list(DASHES),
    )


def test_empty_facts_say_not_set_and_lead_to_the_missing_strip(browser, live_server, world):
    ctx = _context(browser, 1440, 900)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        _open(page, live_server, world["bare"])

        assert _lone_dashes(page) == []

        for key in ("owners", "vendor", "business-purpose"):
            cell = page.locator('[data-testid="property-%s"]' % key)
            assert cell.count() == 1, key
            assert " ".join(cell.inner_text().split()) == "Not set", key
            link = cell.locator("a")
            assert link.count() == 1, key
            assert link.get_attribute("href") == "#missing-record", key

        # Following the link lands on the strip that names what the record needs.
        page.locator('[data-testid="property-vendor"] a').click()
        page.wait_for_timeout(300)
        assert page.url.endswith("#missing-record")
        strip = page.locator("#missing-record")
        assert strip.is_visible()
        text = " ".join(strip.inner_text().split())
        assert "Missing to reach a complete record" in text
        for label in ("Owner", "Vendor"):
            assert label in text

        # The legacy owner rows carry no value, so they are not shown.
        body = " ".join(page.locator("main").inner_text().split())
        assert "(legacy)" not in body

        # Four headline cards, none with an icon, each with a figure.
        cards = page.locator('[data-testid="fact-sheet-stats"] [data-testid="stat-card"]')
        assert cards.count() == 4
        assert page.locator('[data-testid="fact-sheet-stats"] svg').count() == 0
        completeness = " ".join(cards.nth(0).inner_text().split())
        assert "Record completeness" in completeness and "key fields recorded" in completeness
    finally:
        ctx.close()


def test_legacy_owner_rows_show_when_they_carry_a_value(browser, live_server, world):
    ctx = _context(browser, 1440, 900)
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        _open(page, live_server, world["full"])

        assert _lone_dashes(page) == []
        assert page.locator('[data-testid="property-business-owner-legacy"]').inner_text().strip() == "Casey Recorded"
        assert page.locator('[data-testid="property-technical-owner-legacy"]').inner_text().strip() == "Robin Recorded"
        assert "Fact Reader" in page.locator('[data-testid="property-owners"]').inner_text()
        assert page.locator('[data-testid="property-vendor"]').inner_text().strip() == "Acme Software"
        assert page.locator('[data-testid="property-vendor"] a').count() == 0
    finally:
        ctx.close()


def test_fact_sheet_holds_at_phone_width(browser, live_server, world):
    ctx = _context(browser, 390, 844, "dark")
    page = ctx.new_page()
    try:
        _login(page, live_server, world["email"])
        _open(page, live_server, world["bare"])
        assert _lone_dashes(page) == []
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, overflow
        assert page.locator('[data-testid="fact-sheet-stats"] [data-testid="stat-card"]').count() == 4
    finally:
        ctx.close()
