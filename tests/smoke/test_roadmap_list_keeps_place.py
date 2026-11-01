"""An application manager edits a work package from the roadmap list and stays on the list.

The goal: from the List tab, change a work package and be back on the List tab, with
the timeline status filter they had chosen. At phone width Type, Edit and Delete stay
in reach without the page scrolling sideways, and the new work package is linked to
the application being planned, not to another application.
"""

import datetime
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture(scope="module")
def world():
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement
    from app.models.implementation_migration import WorkPackage

    org = create_fresh_org("application_manager", org_admin=True)
    app = create_app("testing")
    with app.app_context():
        ids = {}
        for key, name in (("billing", "Billing Hub"), ("ledger", "Ledger Core")):
            element = ArchiMateElement(
                name=name, type="ApplicationComponent", layer="Application", organization_id=org["org_id"]
            )
            db.session.add(element)
            db.session.flush()
            comp = ApplicationComponent(
                name=name, organization_id=org["org_id"], lifecycle_status="operational",
                archimate_element_id=element.id,
            )
            db.session.add(comp)
            db.session.flush()
            ids[key] = comp.id
        for name, status in (("Retire old ledger", "planned"), ("Move ledger to cloud", "in_progress")):
            db.session.add(WorkPackage(
                name=name, application_component_id=ids["ledger"], organization_id=org["org_id"],
                status=status, priority="high", start_date=datetime.date(2026, 6, 1),
                target_date=datetime.date(2027, 3, 31), element_type="Migration",
                togaf_phase="Implementation", sequence_order=1,
            ))
        db.session.commit()
        db.session.remove()
    org["ids"] = ids
    return org


def _open(page, live_server, world, suffix=""):
    page.goto(
        live_server + "/applications/%d/roadmap%s" % (world["ids"]["ledger"], suffix),
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    expect(page.get_by_role("button", name="Add Work Package").first).to_be_visible(timeout=PAGE_TIMEOUT)


def test_saving_from_the_list_returns_to_the_list_and_filter(page, live_server, world):
    sign_in(page, live_server, world["emails"]["application_manager"])
    _open(page, live_server, world)

    # The status filter belongs to the timeline only.
    page.get_by_label("Timeline status filter").select_option("planned")
    assert "status=planned" in page.url
    page.get_by_role("tab", name="List").click()
    expect(page.get_by_label("Timeline status filter")).to_be_hidden()
    assert page.url.endswith("#list")

    # Edit from the list, save, and land back on the list with the filter.
    row = page.locator("[data-testid=roadmap-work-package-row]").filter(has_text="Retire old ledger")
    row.get_by_role("button", name="Edit").click()
    modal = page.locator("#roadmap-work-package-modal")
    modal.locator("input[type=text]").first.fill("Retire old ledger now")
    with page.expect_response(re.compile(r"/api/applications/\d+/work-packages/\d+")) as saved:
        modal.get_by_role("button", name="Save Changes").click()
    assert saved.value.ok
    page.wait_for_load_state("domcontentloaded")
    expect(page.get_by_role("tab", name="List")).to_have_attribute("aria-selected", "true", timeout=PAGE_TIMEOUT)
    assert page.url.endswith("status=planned#list") or ("status=planned" in page.url and page.url.endswith("#list"))
    expect(page.locator("[data-testid=roadmap-work-package-row]").filter(has_text="Retire old ledger now")).to_be_visible()
    expect(page.get_by_label("Timeline status filter")).to_be_hidden()
    page.get_by_role("tab", name="Timeline").click()
    expect(page.get_by_label("Timeline status filter")).to_have_value("planned")
    # The status is a badge, and dates are written as on the timeline.
    page.get_by_role("tab", name="List").click()
    row = page.locator("[data-testid=roadmap-work-package-row]").filter(has_text="Retire old ledger now")
    expect(row.locator("div.rounded-full").first).to_contain_text("Planned")
    expect(row).to_contain_text(datetime.date(2027, 3, 31).strftime("%-m/%-d/%Y"))


def test_new_work_package_shows_its_own_application(page, live_server, world):
    sign_in(page, live_server, world["emails"]["application_manager"])
    _open(page, live_server, world, "#list")
    page.get_by_role("button", name="Add Work Package").first.click()
    modal = page.locator("#roadmap-work-package-modal")
    modal.locator("input[type=text]").first.fill("Archive ledger data")
    dates = modal.locator("input[type=date]")
    dates.nth(0).fill("2026-10-01")
    dates.nth(1).fill("2027-06-01")
    modal.get_by_role("button", name="Create Work Package").click()
    row = page.locator("[data-testid=roadmap-work-package-row]").filter(has_text="Archive ledger data")
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(row).to_contain_text("Ledger Core")
    expect(row).not_to_contain_text("Billing Hub")
    expect(page.get_by_role("tab", name="List")).to_have_attribute("aria-selected", "true")


def test_phone_list_keeps_type_edit_and_delete_in_reach(browser, live_server, world):
    context = browser.new_context(viewport={"width": 390, "height": 800})
    page = context.new_page()
    try:
        sign_in(page, live_server, world["emails"]["application_manager"])
        _open(page, live_server, world, "#list")
        row = page.locator("[data-testid=roadmap-work-package-row]").filter(has_text="Move ledger to cloud")
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(row).to_contain_text("Migration")
        for name in ("Edit", "Delete"):
            button = row.get_by_role("button", name=name)
            button.scroll_into_view_if_needed()
            expect(button).to_be_in_viewport()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()
