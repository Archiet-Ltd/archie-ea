"""A CTO finds unsupported and over-supported capabilities in a real browser.

Open the list from the sidebar, see the capability nothing supports and the one
four applications support; reload and see the same answer; a second
organisation sees neither.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.unified_application_capability_mapping import UnifiedApplicationCapabilityMapping
    from app.models.unified_capability import UnifiedCapability

    app = create_app("testing")
    tag = uuid.uuid4().hex[:6]
    with app.app_context():
        bare = UnifiedCapability(name="Bare capability %s" % tag, organization_id=org_id, level=1)
        busy = UnifiedCapability(name="Busy capability %s" % tag, organization_id=org_id, level=1)
        apps = [ApplicationComponent(name="Supporter %d %s" % (i, tag), organization_id=org_id) for i in range(4)]
        db.session.add_all([bare, busy] + apps)
        db.session.flush()
        for a in apps:
            db.session.add(UnifiedApplicationCapabilityMapping(
                unified_capability_id=busy.id, application_component_id=a.id,
                support_level="full", coverage_percentage=90, is_active=True))
        db.session.commit()
        db.session.remove()
    return tag


def _page(browser):
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return ctx, ctx.new_page()


def test_cto_finds_unsupported_and_crowded_capabilities(browser, live_server):
    org = create_fresh_org("cto")
    other = create_fresh_org("cto")
    tag = _seed(org["org_id"])

    ctx, page = _page(browser)
    sign_in(page, live_server, org["emails"]["cto"])
    page.goto(live_server + "/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT):
        page.get_by_role("link", name="Capability support", exact=True).first.click()
    expect(page).to_have_url(re.compile(r"/architecture/capability-support/?$"), timeout=PAGE_TIMEOUT)

    for _ in range(2):
        expect(page.locator("[data-support-none]")).to_contain_text("Bare capability %s" % tag, timeout=PAGE_TIMEOUT)
        many = page.locator("[data-support-many]")
        expect(many).to_contain_text("Busy capability %s" % tag)
        expect(many).to_contain_text("4 applications")
        expect(many).to_contain_text("Supporter 3 %s" % tag)
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    ctx.close()

    ctx, page = _page(browser)
    sign_in(page, live_server, other["emails"]["cto"])
    page.goto(live_server + "/architecture/capability-support/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("body")).not_to_contain_text("Bare capability %s" % tag)
    expect(page.locator("[data-support-many-empty]")).to_be_visible(timeout=PAGE_TIMEOUT)
    ctx.close()
