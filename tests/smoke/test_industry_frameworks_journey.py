"""A business architect browses the industry frameworks and adopts a process.

The frameworks are already there (no button), each opens as the APQC hierarchy
from category 1.0 down to level 3, and a process adopted from Recommendations
shows up in the capability model after a reload.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


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


def test_business_architect_browses_the_hierarchy_and_adopts_a_process(
    page, live_server, seeded
):
    from app import create_app, db
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        person = db.session.get(User, seeded["ids"]["business_architect_user"])
        assert person.enterprise_role == "business_architect"

    _login(page, live_server, seeded["emails"]["business_architect"])
    page.goto(live_server + "/industry-apqc/", wait_until="domcontentloaded")

    # Frameworks are listed with counts, and nothing offers to seed them.
    cards = page.locator("[data-framework-card]")
    expect(cards.first).to_be_visible()
    assert cards.count() >= 6
    bfs = page.locator("[data-framework-card=BFS]")
    expect(bfs).to_contain_text("Banking & Financial Services")
    expect(bfs).to_contain_text("13")
    assert re.search(r"Processes\s*\d{2,}", bfs.inner_text())
    expect(page.get_by_text("Seed", exact=False)).to_have_count(0)

    # Open one: category 1.0 expands to level 3.
    bfs.click()
    page.wait_for_url(re.compile(r"/industry-apqc/framework/BFS"))
    tree = page.locator("[data-pcf-tree]")
    categories = tree.locator(":scope > ul > li")
    assert categories.count() == 13
    one = page.locator("[data-pcf-node='1.0']")
    one.locator("summary").first.click()
    expect(page.locator("[data-pcf-node='1.1']")).to_be_visible()
    page.locator("[data-pcf-node='1.1'] summary").first.click()
    expect(page.locator("[data-pcf-node='1.1.1']")).to_contain_text("Assess the external environment")

    # Recommendations is a tab of this page; adopt a process.
    page.get_by_role("tab", name="Recommendations").click()
    row = page.locator("[data-recommended-process]", has_text="Credit risk modeling")
    expect(row).to_be_visible()
    with page.expect_response(lambda r: "/adopt" in r.url and r.request.method == "POST") as done:
        row.get_by_role("button", name=re.compile("Adopt")).click()
    assert done.value.status == 200
    expect(row).to_contain_text("In your capability model")

    # It persisted: reload and the process reads as already adopted.
    page.reload(wait_until="domcontentloaded")
    page.get_by_role("tab", name="Recommendations").click()
    row = page.locator("[data-recommended-process]", has_text="Credit risk modeling")
    expect(row).to_contain_text("In your capability model")

    # And the link lands on the capability model, which holds it.
    with page.expect_navigation():
        row.get_by_role("link", name="Open the capability model").click()
    assert "/capability-map/hierarchy" in page.url
    page.wait_for_load_state("networkidle")
    expect(page.get_by_text("Credit risk modeling").first).to_be_visible()


def test_the_old_recommendations_address_opens_the_tab(page, live_server, seeded):
    _login(page, live_server, seeded["emails"]["business_architect"])
    page.goto(live_server + "/industry-apqc/recommendations?industry=MFG",
              wait_until="domcontentloaded")
    assert page.url.endswith("/industry-apqc/framework/MFG?tab=recommendations")
    expect(page.locator("[data-recommended-process]", has_text="Shop floor management")).to_be_visible()


def test_the_tree_scrolls_on_a_phone_instead_of_stacking(browser, live_server, seeded):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    page = ctx.new_page()
    try:
        _login(page, live_server, seeded["emails"]["business_architect"])
        page.goto(live_server + "/industry-apqc/framework/RET", wait_until="domcontentloaded")
        tree = page.locator("[data-pcf-tree]")
        expect(tree).to_be_visible()
        assert tree.evaluate("el => el.scrollWidth > el.clientWidth")
        # The page itself does not scroll sideways.
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        ctx.close()
