"""A technology element page prints a money value only to a person the one cost rule allows.

Written from two people's goals: an architect opens a node to see what it is and what runs on it,
and the chief technology officer opens the same node to see what it costs. The architect reads no
amount and is told once why; the chief technology officer reads every figure the node carries. The
node's cost and another organisation's node cost are seeded so a leak shows as a number on the page.
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WITHHELD = "Cost figures are withheld for your role."
WALK = os.environ.get("SMOKE_SCREENSHOT_DIR")
MONEY = re.compile(r"\d[\d,]*\.\d\d\s*(GBP|USD|EUR)|£\s*\d|\b(GBP|USD|EUR)\s*\d")
VIEWS = (("1440-light", 1440, 900, False), ("390-dark", 390, 844, True))


def _shot(page, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


@pytest.fixture(scope="module")
def estate(seeded, live_server):
    """A node with a recorded cost, an application it runs, and a second organisation's costed node."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.organization import Organization
    from app.models.technology_layer import Node
    from app.modules.architecture.services import application_technology_links
    from app.services import technology_cost

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    org_id = seeded["ids"]["org"]
    with app.app_context():
        other = Organization(name="Other Org %s" % suffix, slug="other-%s" % suffix)
        db.session.add(other)
        db.session.flush()
        application = ApplicationComponent(
            name="Billing %s" % suffix, organization_id=org_id, lifecycle_status="operational")
        node = Node(name="Cluster %s" % suffix, organization_id=org_id)
        other_node = Node(name="Foreign %s" % suffix, organization_id=other.id)
        db.session.add_all([application, node, other_node])
        db.session.flush()
        db.session.commit()
        technology_cost.record_cost(org_id, node.archimate_element_id, "40000", "GBP", "hosting")
        technology_cost.record_cost(other.id, other_node.archimate_element_id, "88888", "GBP", "hosting")
        with app.test_request_context():
            from flask import g

            g.current_org_id = org_id
            application_technology_links.add_link(application.id, node.archimate_element_id)
        db.session.commit()
        return {"node": node.archimate_element_id, "node_name": node.name, "app": application.id,
                "app_name": application.name}


def _open(browser, live_server, email, width, height, dark):
    ctx = browser.new_context(viewport={"width": width, "height": height})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    if dark:
        ctx.add_init_script("document.documentElement.classList.add('dark');")
    page = ctx.new_page()
    page.goto(live_server + "/account/login", wait_until="domcontentloaded")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.click("#submit", no_wait_after=True)
    page.wait_for_url(lambda url: "/account/login" not in url)
    return ctx, page


def _node_url(live_server, estate):
    return "%s/architecture/technology/Node/%d?ae=1" % (live_server, estate["node"])


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_an_architect_reads_the_node_without_any_amount(browser, live_server, seeded, estate, label, width, height, dark):
    ctx, page = _open(browser, live_server, seeded["emails"]["enterprise_architect"], width, height, dark)
    try:
        page.goto(_node_url(live_server, estate), wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name=estate["node_name"]).first).to_be_visible()
        section = page.locator("[data-testid=technology-cost]")
        expect(section).to_be_visible()
        expect(section).to_contain_text(WITHHELD)
        # the applications the node runs stay readable, and each name opens its page
        link = section.locator("[data-testid=technology-cost-share-link]", has_text=estate["app_name"])
        expect(link).to_be_visible()
        text = page.locator("main").inner_text()
        assert text.count(WITHHELD) == 1, "the sentence is said once, not %d times" % text.count(WITHHELD)
        assert not MONEY.search(text), "an amount reached a person the rule withholds: %s" % MONEY.search(text).group(0)
        for figure in ("40,000", "88,888", "88888", "40000"):
            assert figure not in text
        section.scroll_into_view_if_needed()
        _shot(page, "technology-element-withheld-" + label)
        with page.expect_navigation():
            link.click()
        expect(page.get_by_role("heading", name=estate["app_name"]).first).to_be_visible()
    finally:
        ctx.close()


@pytest.mark.parametrize("label,width,height,dark", VIEWS)
def test_the_chief_technology_officer_reads_the_same_node_with_its_figures(browser, live_server, seeded, estate, label, width, height, dark):
    ctx, page = _open(browser, live_server, seeded["emails"]["cto"], width, height, dark)
    try:
        page.goto(_node_url(live_server, estate), wait_until="domcontentloaded")
        section = page.locator("[data-testid=technology-cost]")
        expect(section.locator("[data-testid=technology-cost-total]")).to_contain_text("40,000.00")
        expect(section.locator("[data-testid=technology-cost-fact-row]").first).to_contain_text("40,000.00 GBP")
        assert WITHHELD not in page.locator("main").inner_text()
        assert "88,888" not in page.locator("main").inner_text()
        section.scroll_into_view_if_needed()
        _shot(page, "technology-element-allowed-" + label)
    finally:
        ctx.close()
