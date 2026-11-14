"""Journeys, in a browser: record that an application supports a capability once and see it everywhere.

A solution architect records "Monelytics supports Plans, Quotas and Payments" on the element page with
Add relationship, or on the application's Capabilities tab, and then finds it on the regenerated coverage
matrix, on the application page, on the element page and in the chat's answers. An organisation with no
support yet is told where to record it.
"""

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect

from . import fresh_org
from .chat_ask import ask, open_chat, sign_in
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ARCHITECT = "solution_architect"
CAPABILITY = "Plans, Quotas and Payments"
OTHER_CAPABILITIES = [
    "Customer onboarding", "Invoicing", "Usage metering", "Entitlement checks", "Revenue reporting",
    "Support desk", "Identity and access", "Partner reporting",
]
APPLICATION = "Monelytics"
OTHER_APPLICATIONS = ["CapAlign", "Iyene", "Entelim", "Ifiok"]

# Opt-in walk screenshots: off unless a directory is named.
WALK = os.environ.get("CAPABILITY_SUPPORT_WALK_DIR")


def _shot(page, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _seed(org_id, *, with_support=False):
    """Monelytics and four other applications (some linked), nine capabilities, no support recorded."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_relationship_sync import _ensure_relationship
    from app.models.business_capabilities import BusinessCapability

    app = create_app("testing")
    with app.app_context():
        suffix = uuid.uuid4().hex[:5]
        main = ApplicationComponent(name=APPLICATION, organization_id=org_id, description="Metering and billing")
        others = [ApplicationComponent(name=n, organization_id=org_id) for n in OTHER_APPLICATIONS]
        db.session.add_all([main] + others)
        caps = [BusinessCapability(name=n, organization_id=org_id, level=1, code="C%s-%d" % (suffix, i))
                for i, n in enumerate([CAPABILITY] + OTHER_CAPABILITIES)]
        db.session.add_all(caps)
        db.session.flush()
        for other in others:
            _ensure_relationship(db.session, "association", other.archimate_element_id, main.archimate_element_id,
                                 expected_organization_id=org_id)
        out = {"app_id": main.id, "app_element_id": main.archimate_element_id,
               "capability_element_id": caps[0].archimate_element_id}
        db.session.commit()
        db.session.remove()
    return out


def _supports(org_id):
    from app import create_app, db
    from app.services.capability_support import support_rows

    app = create_app("testing")
    with app.app_context():
        rows = support_rows(org_id)
        db.session.remove()
        return rows


@pytest.fixture
def org():
    made = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
    made.update(_seed(made["org_id"]))
    return made


def _generate(page, base):
    page.goto(base + "/genome/coverage/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("generate-coverage").click()
    page.wait_for_load_state("networkidle")


def _dark(page):
    """Dark theme for the page now open: the saved choice and the class the stylesheet keys on."""
    page.evaluate("try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
                  "document.documentElement.classList.add('dark');")


def _light(page):
    page.evaluate("try { localStorage.setItem('theme', 'light'); } catch (e) {} "
                  "document.documentElement.classList.remove('dark');")


def _wide(page):
    page.set_viewport_size({"width": 1440, "height": 900})


def test_support_recorded_on_the_element_page_shows_on_every_reader(page, live_server, org):
    _wide(page)
    fresh_org.sign_in(page, live_server, org["emails"][ARCHITECT])

    # Nothing is recorded: the matrix has no cell and says why, with the next step.
    _generate(page, live_server)
    expect(page.get_by_test_id("coverage-empty")).to_contain_text("No application is recorded as supporting a capability yet.")
    expect(page.get_by_test_id("coverage-summary")).to_contain_text("from 0 recorded supports (9 capabilities, 5 applications)")
    _shot(page, "coverage-empty-1440")

    # Add relationship on the application's element page: Realization to the capability.
    page.goto("%s/architecture/application/ApplicationComponent/%d?ae=1" % (live_server, org["app_element_id"]), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    _shot(page, "element-page-before-1440")
    page.get_by_test_id("add-relationship").click()
    page.locator("#rel-target-search").fill(CAPABILITY)
    page.get_by_role("button", name=re.compile(re.escape(CAPABILITY))).first.click()
    page.locator("#rel-type-select").select_option("realization")
    _shot(page, "element-add-relationship-1440")
    page.set_viewport_size({"width": 390, "height": 844})
    _dark(page)
    expect(page.locator("#rel-type-select")).to_be_visible()
    _shot(page, "element-add-relationship-dark-390")
    page.set_viewport_size({"width": 1440, "height": 900})
    _light(page)
    page.get_by_role("button", name="Create relationship").click()
    # The page reloads itself once the relationship is saved; the line below is what it then shows.
    line = page.get_by_test_id("answer-line").filter(has_text="Supports")
    expect(line).to_contain_text("Supports 1 capability", timeout=PAGE_TIMEOUT)
    expect(line).to_contain_text(CAPABILITY)
    expect(line).to_contain_text("Realizes")
    _shot(page, "element-page-after-1440")

    # The regenerated matrix has the cell, and says how much it read.
    _generate(page, live_server)
    expect(page.get_by_test_id("coverage-cell")).to_have_count(1)
    expect(page.get_by_test_id("coverage-summary")).to_contain_text("from 1 recorded support (9 capabilities, 5 applications)")
    cell = page.get_by_test_id("coverage-cell")
    assert CAPABILITY in cell.get_attribute("title") and "Realizes" in cell.get_attribute("title")
    _shot(page, "coverage-generated-1440")

    # The capability name on the matrix opens its page, which has content.
    page.get_by_role("link", name=CAPABILITY).first.click()
    page.wait_for_load_state("networkidle")
    expect(page.locator("main")).to_contain_text(CAPABILITY)

    # The application page lists it, with how and how well, and its figure card agrees.
    page.goto("%s/applications/%d?tab=capabilities" % (live_server, org["app_id"]), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    row = page.get_by_test_id("mapped-capability-row")
    expect(row).to_have_count(1)
    expect(row).to_contain_text(CAPABILITY)
    expect(row).to_contain_text("Realizes")
    expect(page.get_by_test_id("metric-link").filter(has_text=re.compile(r"^1$"))).to_have_count(1)

    # The chat names the same support.
    open_chat(page, live_server)
    unsupported = ask(page, live_server, "Which capabilities have no supporting application?", fresh=False)
    for name in OTHER_CAPABILITIES:
        expect(unsupported).to_contain_text(name)
    assert CAPABILITY not in unsupported.inner_text()
    supporters = ask(page, live_server, "What supports %s?" % CAPABILITY, fresh=False)
    expect(supporters).to_contain_text(APPLICATION)
    _shot(page, "chat-supports-1440")

    assert [(r["application_name"], r["capability_name"], r["kind"]) for r in _supports(org["org_id"])] == [
        (APPLICATION, CAPABILITY, "realization")]

    # Phone width, dark theme: the same support reads on the element page and the matrix.
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("%s/architecture/application/ApplicationComponent/%d?ae=1" % (live_server, org["app_element_id"]),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)
    _dark(page)
    expect(page.get_by_test_id("answer-line").filter(has_text="Supports")).to_contain_text(CAPABILITY)
    _shot(page, "element-page-after-dark-390")
    _generate(page, live_server)
    _dark(page)
    expect(page.get_by_test_id("coverage-summary")).to_contain_text("from 1 recorded support")
    expect(page.get_by_test_id("coverage-cell")).to_have_count(1)
    _shot(page, "coverage-generated-dark-390")


def test_support_recorded_on_the_application_page_shows_on_every_reader(page, live_server, org):
    _wide(page)
    fresh_org.sign_in(page, live_server, org["emails"][ARCHITECT])

    # The tab opens with nothing mapped, says so, and offers the way to map one.
    page.goto("%s/applications/%d" % (live_server, org["app_id"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    tab = page.get_by_role("tab", name=re.compile("Capabilities"))
    assert tab.get_attribute("aria-disabled") != "true", "the Capabilities tab cannot be opened"
    tab.click()
    page.wait_for_load_state("networkidle")
    expect(page.get_by_test_id("mapped-capabilities-table")).to_contain_text("No capabilities mapped")
    _shot(page, "capabilities-tab-empty-1440")
    page.set_viewport_size({"width": 390, "height": 844})
    _dark(page)
    expect(page.get_by_test_id("map-capability")).to_be_visible()
    _shot(page, "capabilities-tab-empty-dark-390")
    page.set_viewport_size({"width": 1440, "height": 900})
    _light(page)

    # Map Capability: the capability, how it supports, how well.
    page.get_by_test_id("map-capability").click()
    page.locator("#cap-select").select_option(
        value=page.locator("#cap-select option", has_text=CAPABILITY).first.get_attribute("value"))
    page.locator("#cap-kind").select_option("realization")
    page.locator("#cap-support").select_option("primary")
    _shot(page, "map-capability-dialog-1440")
    page.get_by_role("button", name="Add Mapping").click()
    page.wait_for_load_state("networkidle")

    page.goto("%s/applications/%d?tab=capabilities" % (live_server, org["app_id"]), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    row = page.get_by_test_id("mapped-capability-row")
    expect(row).to_have_count(1)
    expect(row).to_contain_text(CAPABILITY)
    expect(row).to_contain_text("Realizes")
    expect(row).to_contain_text("Primary")
    _shot(page, "capabilities-tab-one-1440")

    # The figure card reads 1 and lands on the table.
    page.goto("%s/applications/%d" % (live_server, org["app_id"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_test_id("metric-link").filter(has_text=re.compile(r"^1$")).first.click()
    page.wait_for_load_state("networkidle")
    assert "mapped-capabilities" in page.url
    expect(page.get_by_test_id("mapped-capabilities-table")).to_contain_text(CAPABILITY)

    # The element page and the regenerated matrix agree.
    page.goto("%s/architecture/application/ApplicationComponent/%d?ae=1" % (live_server, org["app_element_id"]), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("answer-line").filter(has_text="Supports")).to_contain_text("Supports 1 capability")
    _generate(page, live_server)
    expect(page.get_by_test_id("coverage-cell")).to_have_count(1)

    # Phone width, dark theme: the Capabilities tab still lists it, and the dialog is reachable.
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("%s/applications/%d?tab=capabilities" % (live_server, org["app_id"]), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    _dark(page)
    expect(page.get_by_test_id("mapped-capability-row")).to_contain_text(CAPABILITY)
    expect(page.get_by_test_id("map-capability")).to_be_visible()
    _shot(page, "capabilities-tab-one-dark-390")
    page.get_by_test_id("map-capability").click()
    expect(page.locator("#cap-select")).to_be_visible()
    _shot(page, "map-capability-dialog-dark-390")


def test_the_empty_matrix_points_to_the_applications(page, live_server, org):
    _wide(page)
    fresh_org.sign_in(page, live_server, org["emails"][ARCHITECT])
    _generate(page, live_server)
    empty = page.get_by_test_id("coverage-empty")
    expect(empty).to_contain_text("Capabilities tab")
    expect(empty).to_contain_text("Add relationship")
    expect(empty).to_contain_text("AI chat")
    page.get_by_test_id("open-applications").click()
    page.wait_for_load_state("networkidle")
    assert page.url.rstrip("/").endswith("/applications")
    expect(page.locator("main")).to_contain_text(APPLICATION)
    page.set_viewport_size({"width": 390, "height": 844})
    _dark(page)
    _generate(page, live_server)
    expect(page.get_by_test_id("coverage-empty")).to_be_visible()
    _shot(page, "coverage-empty-dark-390")
