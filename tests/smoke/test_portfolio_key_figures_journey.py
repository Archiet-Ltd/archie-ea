"""A CTO reads the portfolio key figures and follows one to the list behind it.

Five applications are registered in a new organisation. The overview shows the
application figure as 5; following it lands on the application list, which holds
the same five. Another organisation's overview does not count them.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _seed(org_id, names):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        for name in names:
            db.session.add(ApplicationComponent(name=name, organization_id=org_id))
        db.session.commit()
        db.session.remove()


def _page(browser):
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return ctx, ctx.new_page()


def _application_figure(page):
    page.get_by_test_id("layer-tab-overview").click()
    card = page.locator('a[href="/applications/"]').filter(has_text="Active applications in portfolio").first
    expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
    return card, re.search(r"\d[\d,]*|—", card.inner_text()).group(0)


def test_cto_follows_the_application_figure_to_its_list(browser, live_server):
    org = create_fresh_org("cto")
    other = create_fresh_org("cto")
    names = ["Key figure app %d %s" % (i, org["suffix"]) for i in range(5)]
    _seed(org["org_id"], names)

    ctx, page = _page(browser)
    sign_in(page, live_server, org["emails"]["cto"])
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    card, figure = _application_figure(page)
    assert figure == "5"
    with page.expect_navigation(timeout=PAGE_TIMEOUT):
        card.click()
    rows = page.get_by_test_id("applications-table").locator("tbody tr").filter(has_text="Key figure app")
    expect(rows).to_have_count(5, timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(rows).to_have_count(5, timeout=PAGE_TIMEOUT)
    ctx.close()

    ctx, page = _page(browser)
    sign_in(page, live_server, other["emails"]["cto"])
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    # A new organisation has fewer than five applications, so it opens on the setup view
    # and never reports the first organisation's five.
    expect(page.get_by_text("Key figure app")).to_have_count(0)
    ctx.close()
