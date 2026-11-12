"""An architect records a supplier the model links from the Vendors page and finds it on the system.

The organisation is seeded the way an import leaves it: one system is linked to three supplier
actors and to a node by association, and nothing is recorded as its vendor or what it runs on. A
second organisation holds its own system and suppliers. The journey opens the Vendors page, reads
that the model links three suppliers to the system (not that no system names a vendor), records
one, and finds it recorded on the page and on the system's Vendors tab with the same count. It then
records the node as what the system runs on and reads that the banner says the score counts the
hosting description, with a link that lands on the field. It runs at 1440 wide in the light theme
and at 390 wide in the dark theme.
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = Path(os.environ.get("VENDORS_PORTFOLIO_SHOTS", "") or "/tmp/vendors-portfolio-shots")


def _seed(org_id, label, suppliers=3):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
    from app.models.technology_layer import Node

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:6]
    with app.app_context():
        application = ApplicationComponent(
            name="%s Billing Hub %s" % (label, suffix), organization_id=org_id, lifecycle_status="operational")
        node = Node(name="%s Container Host %s" % (label, suffix), organization_id=org_id)
        actors = [
            ArchiMateElement(name="%s Search Supplier %d %s" % (label, i, suffix), type="Business Actor",
                             layer="Business", organization_id=org_id)
            for i in range(suppliers)
        ]
        db.session.add_all([application, node] + actors)
        db.session.flush()
        element = db.session.get(ArchiMateElement, application.archimate_element_id)
        db.session.add(ArchiMateRelationship(
            type="association", source_id=node.archimate_element_id, target_id=element.id,
            architecture_id=element.architecture_id, organization_id=org_id))
        for actor in actors:
            db.session.add(ArchiMateRelationship(
                type="association", source_id=element.id, target_id=actor.id,
                architecture_id=element.architecture_id, organization_id=org_id))
        db.session.commit()
        return {"app": application.id, "app_name": application.name, "node_name": node.name,
                "actor_names": [a.name for a in actors]}


def _other_org():
    from app import create_app, db
    from app.models.organization import Organization

    app = create_app("testing")
    with app.app_context():
        tag = uuid.uuid4().hex[:6]
        org = Organization(name="Other Portfolio %s" % tag, slug="other-portfolio-%s" % tag)
        db.session.add(org)
        db.session.commit()
        return org.id


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
    response = page.request.get(href if href.startswith("http") else base + href)
    assert response.status == 200, (href, response.status)
    body = response.text()
    assert len(body) > 2000, href
    if expected:
        assert expected in body, (href, expected)


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_architect_records_a_linked_supplier_from_the_vendors_page(browser, live_server, seeded, width, scheme):
    mine = _seed(seeded["ids"]["org"], "Mine")
    theirs = _seed(_other_org(), "Theirs", suppliers=2)
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto(live_server + "/applications/vendors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The page says what the model links, not that nothing names a vendor.
        answer = page.get_by_test_id("vendors-answer")
        expect(answer).to_contain_text(re.compile("the model (also )?links", re.I))
        expect(answer).not_to_contain_text("name a vendor yet")
        linked = page.get_by_test_id("suppliers-linked-portfolio")
        summary = linked.get_by_test_id("suppliers-linked-summary")
        figures = re.compile(r"(\d+) suppliers? linked in the model to (\d+) systems?, none recorded as the vendor")
        before = int(figures.search(summary.inner_text()).group(1))
        assert before >= 3
        row = linked.locator("[data-testid=linked-system-row][data-application='%d']" % mine["app"])
        expect(row.get_by_test_id("linked-supplier-row")).to_have_count(3)
        for name in mine["actor_names"]:
            expect(row).to_contain_text(name)
        expect(row.get_by_test_id("linked-system-count")).to_contain_text("3 on its Vendors tab")
        # Another organisation's system and suppliers are never listed.
        expect(page.locator("body")).not_to_contain_text(theirs["app_name"])
        expect(page.locator("body")).not_to_contain_text(theirs["actor_names"][0])
        _shot(page, "portfolio-before", width, scheme)

        # The system link lands on the system's Vendors tab, with content.
        system_href = row.get_by_test_id("linked-system-link").get_attribute("href")
        _has_content(page, live_server, system_href, mine["app_name"])

        # Record one supplier from the page.
        chosen = mine["actor_names"][0]
        page.wait_for_function(
            "() => { const el = document.querySelector('[data-linked-suppliers]');"
            " return !!(el && el._x_dataStack); }")
        with page.expect_response(lambda r: "/vendor-link" in r.url and r.request.method == "POST") as saved:
            row.get_by_role("button", name="Record %s as vendor for %s" % (chosen, mine["app_name"])).click()
        assert saved.value.status == 201
        page.wait_for_url(re.compile(r"/applications/vendors$"))
        linked = page.get_by_test_id("suppliers-linked-portfolio")
        after = int(figures.search(linked.get_by_test_id("suppliers-linked-summary").inner_text()).group(1))
        assert after == before - 1
        row = linked.locator("[data-testid=linked-system-row][data-application='%d']" % mine["app"])
        expect(row.get_by_test_id("linked-supplier-row")).to_have_count(2)
        expect(row.get_by_test_id("linked-system-count")).to_contain_text("%s recorded" % chosen)
        expect(page.get_by_test_id("vendors-table")).to_contain_text(chosen)
        _shot(page, "portfolio-after", width, scheme)

        # The system's Vendors tab agrees: the vendor is recorded and the count is the same.
        page.goto(live_server + "/applications/%d?tab=vendors" % mine["app"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        tab = page.locator("[data-vendor-links]")
        expect(tab.get_by_test_id("vendor-organisation-row")).to_have_count(1)
        expect(tab.get_by_test_id("vendor-organisation-row")).to_contain_text(chosen)
        expect(tab.get_by_test_id("supplier-linked-row")).to_have_count(2)

        # Record the node as what the system runs on: the banner says what the score reads.
        page.goto(live_server + "/applications/%d?tab=technology" % mine["app"],
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_function(
            "() => { const el = document.querySelector('[data-technology-links]');"
            " return !!(el && el._x_dataStack); }")
        banner = page.locator("#completeness-banner")
        expect(banner).to_contain_text("1 technology element linked, none recorded as runs on")
        with page.expect_response(lambda r: "/technology-links" in r.url and r.request.method == "POST") as ran:
            page.locator("[data-technology-links]").get_by_role(
                "button", name="Record %s as runs on" % mine["node_name"]).click()
        assert ran.value.status == 201
        expect(banner).not_to_contain_text("none recorded as runs on")
        expect(banner).to_contain_text("what it runs on is recorded, but the score counts the hosting description")
        _shot(page, "banner-after", width, scheme)

        # Its link lands on the edit page, on the hosting field; saving it clears the gap.
        link = banner.get_by_role("link", name="add the hosting description")
        _has_content(page, live_server, link.get_attribute("href"), "Hosting / Deployment Model")
        link.click()
        page.wait_for_url(re.compile(r"/edit#deployment_model$"))
        page.select_option("#deployment_model", "cloud")
        page.get_by_role("button", name=re.compile("^(Save|Update)", re.I)).first.click()
        page.wait_for_url(lambda url: "/edit" not in url)
        page.goto(live_server + "/applications/%d" % mine["app"], wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#completeness-banner")).not_to_contain_text("Hosting / deployment")
    finally:
        context.close()
