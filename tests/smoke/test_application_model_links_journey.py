"""An architect sees what the model already links to an application and records it.

The organisation is seeded the way an import leaves it: the application is linked
to two nodes and two supplier actors by association, and nothing is recorded as
what it runs on or as its vendor. The journey opens the Technology tab, sees the
two linked nodes and the cost-share message that says they carry no cost yet,
records one as runs on and watches it move into the runs-on list and the cost
share change, then opens the Vendors tab, sees the two linked suppliers, records
one as the vendor and finds it under vendor organisations. Every link it passes
lands on a page with content. It runs at 1440 wide in the light theme and at 390
wide in the dark theme.
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = Path(os.environ.get("MODEL_LINKS_SHOTS", "") or "/tmp/model-links-shots")


def _seed_estate(org_id):
    """An application linked by association to two nodes and two supplier actors."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.technology_layer import Node

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        application = ApplicationComponent(
            name="Billing Hub %s" % suffix, organization_id=org_id, lifecycle_status="operational")
        nodes = [Node(name="Container Host %d %s" % (i, suffix), organization_id=org_id) for i in (1, 2)]
        actors = [
            ArchiMateElement(name="Payments Supplier %d %s" % (i, suffix), type="BusinessActor",
                             layer="Business", organization_id=org_id)
            for i in (1, 2)
        ]
        db.session.add_all([application] + nodes + actors)
        db.session.flush()
        app_element = db.session.get(ArchiMateElement, application.archimate_element_id)
        for node in nodes:
            db.session.add(ArchiMateRelationship(
                type="association", source_id=node.archimate_element_id, target_id=app_element.id,
                architecture_id=app_element.architecture_id, organization_id=org_id))
        for actor in actors:
            db.session.add(ArchiMateRelationship(
                type="association", source_id=app_element.id, target_id=actor.id,
                architecture_id=app_element.architecture_id, organization_id=org_id))
        db.session.commit()
        return {
            "app": application.id,
            "app_name": application.name,
            "node_names": [n.name for n in nodes],
            "actor_names": [a.name for a in actors],
        }


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.click("#submit", no_wait_after=True)
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _shot(page, name, width, scheme):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%s-%d.png" % (name, scheme, width))), full_page=True)


def _has_content(page, base, href, expected=None):
    """Follow a link as a person would and require a page with something on it."""
    response = page.request.get(href if href.startswith("http") else base + href)
    assert response.status == 200, (href, response.status)
    body = response.text()
    assert len(body) > 2000, href
    if expected:
        assert expected in body, (href, expected)


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_architect_records_linked_technology_and_suppliers(browser, live_server, seeded, width, scheme):
    estate = _seed_estate(seeded["ids"]["org"])
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto(live_server + "/applications/%d?tab=technology" % estate["app"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_function(
            "() => { const el = document.querySelector('[data-technology-links]');"
            " return !!(el && el._x_dataStack); }")

        # The Technology tab lists both linked nodes, each as an association.
        panel = page.locator("[data-technology-links]")
        linked = panel.locator("[data-technology-linked-row]")
        expect(linked).to_have_count(2)
        for name in estate["node_names"]:
            expect(panel.locator("[data-technology-linked]")).to_contain_text(name)
        expect(panel.locator("[data-technology-linked]")).to_contain_text("association")
        expect(panel.locator("[data-technology-none-recorded]")).to_be_visible()
        expect(panel.locator("[data-technology-empty]")).to_be_hidden()

        # The cost share says how many are linked, not that nothing is mapped.
        costs = page.locator("#technology-cost-shares")
        expect(costs).to_contain_text("2 technology elements linked in the model, none recorded as runs on")
        expect(costs).not_to_contain_text("not mapped to any technology")
        link = costs.get_by_test_id("cost-share-runs-on-link")
        expect(link).to_be_visible()
        banner = page.locator("#completeness-banner")
        expect(banner).to_contain_text("2 technology elements linked, none recorded as runs on")
        expect(banner).to_contain_text("2 suppliers linked, none recorded as vendor")
        expect(banner).to_contain_text("counts only facts recorded")
        _shot(page, "technology-before", width, scheme)

        # Every link on the card lands on a page with content.
        for href in panel.locator("[data-technology-linked] a").evaluate_all(
                "els => els.map(e => e.href)"):
            _has_content(page, live_server, href)

        # Record the first as runs on: it moves into the runs-on list, no reload.
        first = estate["node_names"][0]
        with page.expect_response(lambda r: "/technology-links" in r.url and r.request.method == "POST") as saved:
            panel.get_by_role("button", name="Record %s as runs on" % first).click()
        assert saved.value.status == 201
        expect(panel.locator("[data-technology-list]")).to_contain_text(first)
        expect(panel.locator("[data-technology-linked-row]")).to_have_count(1)
        expect(panel.locator("[data-technology-linked]")).not_to_contain_text(first)
        expect(panel.locator("[data-technology-none-recorded]")).to_be_hidden()

        # The cost share now rests on the recorded link and names it.
        expect(costs).not_to_contain_text("none recorded as runs on")
        cost_link = costs.get_by_test_id("app-technology-cost-link")
        expect(cost_link).to_have_text(first)
        _has_content(page, live_server, cost_link.get_attribute("href"), first)
        expect(banner).not_to_contain_text("technology elements linked, none recorded as runs on")
        expect(banner).to_contain_text("2 suppliers linked, none recorded as vendor")
        _shot(page, "technology-after", width, scheme)

        # Vendors: the two suppliers the model links are offered; the register holds none.
        page.get_by_role("tab", name=re.compile("^Vendors")).click()
        vendors = page.locator("[data-vendor-links]")
        expect(vendors.get_by_test_id("supplier-linked-row")).to_have_count(2)
        for name in estate["actor_names"]:
            expect(vendors.get_by_test_id("suppliers-linked")).to_contain_text(name)
        expect(vendors.get_by_test_id("vendor-organisations-empty")).to_contain_text(
            "The model links 2 suppliers to it")
        expect(vendors.get_by_test_id("vendor-organisation-row")).to_have_count(0)
        _shot(page, "vendors-before", width, scheme)

        chosen = estate["actor_names"][0]
        with page.expect_response(lambda r: "/vendor-link" in r.url and r.request.method == "POST") as saved:
            vendors.get_by_role("button", name="Record %s as vendor" % chosen).click()
        assert saved.value.status == 201
        row = page.locator("[data-vendor-links]").get_by_test_id("vendor-organisation-row")
        expect(row).to_have_count(1)
        expect(row).to_contain_text(chosen)
        expect(page.locator("[data-vendor-links]").get_by_test_id("supplier-linked-row")).to_have_count(1)
        expect(page.locator("[data-vendor-links]")).to_contain_text("Primary Vendor")
        expect(page.locator("[data-vendor-links]").get_by_test_id("suppliers-linked")).not_to_contain_text(chosen)
        expect(banner).not_to_contain_text("suppliers linked, none recorded as vendor")
        _shot(page, "vendors-after", width, scheme)

        # The vendor link lands on the vendor's page, with the application under it.
        open_href = row.get_by_role("link", name="Open vendor").get_attribute("href")
        _has_content(page, live_server, open_href, chosen)

        # The record agrees with both tabs: the vendor is no longer a gap, and the hosting
        # note is gone now that one element is recorded as runs on.
        page.goto(live_server + "/applications/%d/fact-sheet" % estate["app"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        gaps = page.get_by_test_id("missing-record")
        expect(gaps).to_be_visible()
        expect(gaps).not_to_contain_text("suppliers linked, none recorded as vendor")
        expect(gaps).not_to_contain_text("technology elements linked, none recorded as runs on")
        _shot(page, "record-after", width, scheme)
    finally:
        context.close()
