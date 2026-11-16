"""Journey: a data architect works the Data register: list, create, diagram.

Signed in to the seeded Lantern Quay Systems organisation as a data architect, a person:
1. opens Systems > Data register, sees one sidebar entry marked, filters Classification = Confidential
   (5 entities), sorts Entity descending (Shipment first) and finds no bare dash in any cell;
2. follows Create entity under the trail Home > Systems > Data register, saves "Warranty Claim" in
   Orders and Fulfilment, and finds 15 entities in the register after a reload;
3. opens the Diagram tab under the same heading and entry, and finds the new entity among the 6 of
   Orders and Fulfilment;
4. at 390 wide reaches every row menu without sideways page scroll, and every diagram toolbar button.
"""
import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
NEW_NAME = "Warranty Claim"
SHOTS = os.environ.get("DATA_REGISTER_SHOTS")


def _shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / (name + ".png")), full_page=True)


def _forget_new_entity(app, org_id):
    from app import db
    from app.models.process_data import DataEntity

    with app.app_context():
        DataEntity.query.filter_by(organization_id=org_id, name=NEW_NAME).delete()
        db.session.commit()
        db.session.remove()


@pytest.fixture(scope="module")
def demo(live_server, app):
    from app import db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.organization import Organization
    from app.models.user import User

    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            if Organization.query.filter_by(slug="lantern-quay").first() is None:
                seed_demo_company()
            org_id = Organization.query.filter_by(slug="lantern-quay").one().id
            user = User.query.filter_by(email=DEMO_EMAIL, organization_id=org_id).one()
            user.password = PASSWORD
            user.enterprise_role = "data_architect"
            db.session.commit()
            db.session.remove()
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous
    _forget_new_entity(app, org_id)
    yield {"org_id": org_id}
    _forget_new_entity(app, org_id)


def _open(browser, live_server, width, scheme="light"):
    page = browser.new_page(viewport={"width": width, "height": 900 if width > 600 else 844}, color_scheme=scheme)
    sign_in(page, live_server, DEMO_EMAIL)
    return page


def _marked(page):
    return page.locator('[data-slot="sidebar"] a[data-nav-key][data-active="true"]')


def _rows(page):
    return page.locator('[data-testid="register-table"] tbody [data-testid="data-entity-row"]:visible')


def _crumbs(page):
    return re.sub(r"\s+", " ", page.locator('nav[aria-label="Breadcrumb"]').first.inner_text()).strip()


def _open_register(page, live_server):
    page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    toggle = page.locator('[data-nav-toggle="systems"]').first
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    page.locator('a[data-nav-key="data_register"]').first.click()
    page.wait_for_url(re.compile(r"/architecture/data-entities$"), timeout=PAGE_TIMEOUT)


def test_register_list_create_and_diagram(browser, live_server, demo, app):
    page = _open(browser, live_server, 1440)
    _open_register(page, live_server)
    expect(page.get_by_role("heading", name="Data register", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)

    # exactly one entry is marked, and it is the Data register
    marked = _marked(page)
    expect(marked).to_have_count(1)
    expect(marked).to_have_text("Data register")
    expect(_rows(page)).to_have_count(14)
    _shot(page, "register-1440-light")

    # no cell reads as a bare dash
    cells = page.locator('[data-testid="register-table"] tbody td').all_inner_texts()
    assert cells and not [c for c in cells if c.strip() == "—"]

    # filter Classification = Confidential: 5 entities
    page.locator("#register-classification").select_option("confidential")
    page.get_by_role("button", name="Apply").click()
    page.wait_for_url(re.compile(r"classification=confidential"), timeout=PAGE_TIMEOUT)
    expect(_rows(page)).to_have_count(5)

    # sort by Entity, descending: Shipment is first
    page.get_by_role("link", name="Clear").click()
    page.wait_for_url(re.compile(r"/data-entities$"), timeout=PAGE_TIMEOUT)
    page.locator('[data-table-sort-trigger="0"]').click()
    page.locator('[data-table-sort="0"][data-dir="desc"]').click()
    expect(_rows(page).first.locator("td").first).to_contain_text("Shipment")

    # create: the trail names the register, then save
    page.get_by_role("link", name="Create entity").first.click()
    page.wait_for_url(re.compile(r"/data-entities/create$"), timeout=PAGE_TIMEOUT)
    assert _crumbs(page).startswith("Home Systems Data register"), _crumbs(page)
    expect(_marked(page)).to_have_text("Data register")
    expect(_marked(page)).to_have_count(1)
    _shot(page, "create-1440-light")
    page.locator("#name").fill(NEW_NAME)
    page.locator("#domain_id").select_option(label="Orders and Fulfilment")
    page.get_by_role("button", name="Create entity").click()
    page.wait_for_url(re.compile(r"/data-entities$"), timeout=PAGE_TIMEOUT)
    page.reload(wait_until="networkidle")
    expect(_rows(page)).to_have_count(15)
    expect(page.locator('[data-testid="register-table"]')).to_contain_text(NEW_NAME)

    # the diagram: same name, same entry, six entities in Orders and Fulfilment
    page.get_by_role("link", name="Diagram", exact=True).click()
    page.wait_for_url(re.compile(r"/data-entities/diagram"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Data register", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(_marked(page)).to_have_count(1)
    expect(_marked(page)).to_have_text("Data register")
    assert _crumbs(page).startswith("Home Systems Data register"), _crumbs(page)
    page.locator("#erd-domain").select_option(label="Orders and Fulfilment")
    page.wait_for_url(re.compile(r"domain_id="), timeout=PAGE_TIMEOUT)
    finder = page.get_by_test_id("finder-count")
    expect(finder).to_have_text("6", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("finder-list")).to_contain_text(NEW_NAME)
    _shot(page, "diagram-1440-light")
    page.close()


def test_register_is_usable_at_390(browser, live_server, demo, app):
    _forget_new_entity(app, demo["org_id"])
    page = _open(browser, live_server, 390)
    page.goto(live_server + "/architecture/data-entities", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    _shot(page, "register-390-light")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the page scrolls sideways"
    rows = _rows(page)
    expect(rows).to_have_count(14)
    for i in range(rows.count()):
        trigger = rows.nth(i).get_by_role("button", name=re.compile(r"^Actions for "))
        trigger.scroll_into_view_if_needed()
        box = trigger.bounding_box()
        assert box and 0 <= box["x"] and box["x"] + box["width"] <= 390, "row %d menu is out of sideways view" % i
    rows.first.get_by_role("button", name=re.compile(r"^Actions for ")).click()
    expect(page.get_by_role("menuitem", name="Edit")).to_be_visible()

    page.goto(live_server + "/architecture/data-entities/diagram", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    _shot(page, "diagram-390-light")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "the diagram page scrolls sideways"
    bar = page.locator('[data-testid="diagram-toolbar"]')
    buttons = bar.locator("button:visible")
    assert buttons.count() > 0
    for i in range(buttons.count()):
        button = buttons.nth(i)
        button.scroll_into_view_if_needed()
        box = button.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 391, "a toolbar button is cut off at 390"
    page.close()
