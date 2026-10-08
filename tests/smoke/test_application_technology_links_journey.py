"""An architect maps an application onto the node it runs on, in a real browser.

The journey clicks the real control: open the application's Technology tab, see
the honest empty state, type the node's name into the picker, choose it, reload
and see it still listed, then open the Twin map on the node and see the
application among what depends on it. The link is an ArchiMate relationship, so
the map needs no change of its own to show it.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, type_and_wait

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def estate(seeded, live_server):
    """An application and a node of its own in the seeded organisation, so the
    journey never depends on what other tests left behind."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.technology_layer import Node

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    org_id = seeded["ids"]["org"]
    with app.app_context():
        application = ApplicationComponent(
            name="Claims Portal %s" % suffix, organization_id=org_id,
            lifecycle_status="operational",
        )
        node = Node(name="Linux Cluster %s" % suffix, organization_id=org_id)
        db.session.add_all([application, node])
        db.session.commit()
        return {
            "app": application.id,
            "app_name": application.name,
            "node_element": node.archimate_element_id,
            "node_name": node.name,
        }


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


def _open_technology_tab(page, base, app_id):
    page.goto(base + "/applications/%d?tab=technology" % app_id,
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_function(
        "() => { const el = document.querySelector('[data-technology-links]');"
        " return !!(el && el._x_dataStack); }"
    )


def test_architect_maps_an_application_to_a_node_and_sees_it_in_the_blast_radius(
    page, live_server, seeded, estate
):
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _open_technology_tab(page, live_server, estate["app"])

    panel = page.locator("[data-technology-links]")
    expect(panel.locator("[data-technology-empty]")).to_have_text("No technology mapped")

    type_and_wait(page, "techstack", estate["node_name"])
    option = page.locator("#techstack-picker-listbox [role=option]", has_text=estate["node_name"])
    with page.expect_response(
        lambda r: "/technology-links" in r.url and r.request.method == "POST"
    ) as saved:
        option.first.click()
    assert saved.value.status == 201
    expect(panel.locator("[data-technology-list]")).to_contain_text(estate["node_name"])

    # It persisted: a fresh page load reads it back from the server.
    _open_technology_tab(page, live_server, estate["app"])
    expect(panel.locator("[data-technology-list]")).to_contain_text(estate["node_name"])
    expect(panel.locator("[data-technology-empty]")).to_be_hidden()

    # The node's blast radius on the Twin map now includes the application.
    with page.expect_navigation():
        panel.get_by_role("link", name="What depends on it").first.click()
    assert "element=%d" % estate["node_element"] in page.url
    page.wait_for_selector("[data-graph-nodes] button", state="visible")
    expect(page.locator("[data-graph-nodes]")).to_contain_text(estate["app_name"])


def test_an_application_with_no_technology_says_so(page, live_server, seeded):
    """The seeded application has no technology links: the panel states it."""
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    _open_technology_tab(page, live_server, seeded["ids"]["application"])
    expect(page.locator("[data-technology-empty]")).to_have_text("No technology mapped")
