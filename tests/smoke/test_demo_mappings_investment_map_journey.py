"""The Investment Map shows the application-to-capability mappings the demo seed writes.

An organisation with an application and a capability is seeded with the demo
mappings; the enterprise architect opens the Investment Map, sees the capability
supported by an application (not the "map your first application" page), and
sees the same after a reload.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def seeded_mappings(seeded, live_server):
    from app import create_app, db
    from app.commands.seed_demo_mappings import seed_demo_mappings
    from app.jobs.tenant_safe_job import tenant_scope
    from app.models.application_portfolio import ApplicationComponent
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    org_id = seeded["ids"]["org"]
    with app.app_context():
        with tenant_scope(org_id):
            db.session.add(ApplicationComponent(name="Blue Yonder WMS", organization_id=org_id))
            db.session.add(BusinessCapability(name="Warehouse Management", organization_id=org_id))
            db.session.commit()
            stats = seed_demo_mappings(org_id)
        assert stats["unified_created"] > 0
        return stats


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.click("#submit", no_wait_after=True)
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _open_map(page, base):
    page.goto(base + "/architecture/investment-priorities",
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Investment Priority Matrix")).to_be_visible()
    row = page.locator("tr", has_text="Warehouse Management").first
    expect(row).to_be_visible()
    return row


def test_investment_map_shows_seeded_mappings_after_reload(
    page, live_server, seeded, seeded_mappings
):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    row = _open_map(page, live_server)
    expect(row).to_contain_text("1 apps")
    expect(row).not_to_contain_text("no current application support")

    page.reload(wait_until="domcontentloaded")
    row = _open_map(page, live_server)
    expect(row).to_contain_text("1 apps")
