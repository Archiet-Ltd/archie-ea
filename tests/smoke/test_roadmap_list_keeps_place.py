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
from .fresh_org import create_fresh_org, list_date, sign_in

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
    row.get_by_role("button", name=re.compile("^Actions for")).click()
    page.get_by_role("menuitem", name="Edit").click()
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
    expect(row.locator("td span.rounded-full:visible").first).to_contain_text("Planned")
    expect(row).to_contain_text(list_date(datetime.date(2027, 3, 31)))
    # The list and the timeline colour a status with the one mapping.
    list_badge = row.locator("td span.rounded-full:visible").first.get_attribute("class")
    page.get_by_role("tab", name="Timeline").click()
    bar = page.locator("[data-slot=roadmap-timeline-bar]").filter(has_text="Retire old ledger now")
    expect(bar).to_be_visible()
    assert "bg-muted" in bar.get_attribute("class") and "bg-muted" in list_badge


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
    # The page is Ledger Core's own roadmap: its row does not name Ledger Core as its answer.
    expect(row).not_to_contain_text("Ledger Core")
    expect(row).not_to_contain_text("Billing Hub")
    expect(row.locator("td").first.locator("div.rounded-full")).to_have_count(0)
    expect(page.get_by_role("tab", name="List")).to_have_attribute("aria-selected", "true")


def test_phone_list_shows_status_and_dates_without_sideways_scroll(browser, live_server, world):
    context = browser.new_context(viewport={"width": 390, "height": 800})
    page = context.new_page()
    try:
        sign_in(page, live_server, world["emails"]["application_manager"])
        _open(page, live_server, world, "#list")
        row = page.locator("[data-testid=roadmap-work-package-row]").filter(has_text="Move ledger to cloud")
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(row).to_contain_text("Migration")
        summary = row.locator("[data-testid=roadmap-work-package-summary]")
        expect(summary).to_contain_text("In Progress")
        expect(summary).to_contain_text(list_date(datetime.date(2026, 6, 1)))
        expect(summary).to_contain_text(list_date(datetime.date(2027, 3, 31)))
        # Nothing is scrolled into view first: the table fits its card.
        table = page.locator("[data-testid=roadmap-work-packages-table]")
        sizes = table.evaluate(
            "el => { const t = el.matches('table') ? el : el.querySelector('table'); "
            "const box = t.parentElement; return [t.scrollWidth, t.clientWidth, box.scrollWidth, box.clientWidth]; }"
        )
        assert sizes[0] <= sizes[1] and sizes[2] <= sizes[3], sizes
        trigger = row.get_by_role("button", name=re.compile("^Actions for"))
        trigger.scroll_into_view_if_needed()
        expect(trigger).to_be_in_viewport()
        trigger.click()
        expect(page.get_by_role("menuitem", name="Edit")).to_be_visible()
        expect(page.get_by_role("menuitem", name="Delete")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def _add_package(world, name, status="planned"):
    from app import create_app, db
    from app.models.implementation_migration import WorkPackage

    app = create_app("testing")
    with app.app_context():
        package = WorkPackage(
            name=name, application_component_id=world["ids"]["ledger"], organization_id=world["org_id"],
            status=status, priority="medium", start_date=datetime.date(2026, 6, 1),
            target_date=datetime.date(2027, 3, 31), element_type="Migration",
            togaf_phase="Implementation", sequence_order=1,
        )
        db.session.add(package)
        db.session.commit()
        return package.id


def _package_exists(world, package_id):
    from app import create_app, db
    from app.models.implementation_migration import WorkPackage

    app = create_app("testing")
    with app.app_context():
        found = db.session.get(WorkPackage, package_id)
        db.session.remove()
        return found


def _row(page, name):
    return page.locator("[data-testid=roadmap-work-package-row]").filter(has_text=name)


def _open_row_menu_with_keyboard(page, name):
    row = _row(page, name)
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    row.get_by_role("button", name=re.compile("^Actions for")).focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("menuitem", name="Edit")).to_be_visible()


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 800)])
def test_row_menu_edits_and_deletes_from_the_keyboard(browser, live_server, world, width, height):
    keep = "Keyboard keep %d" % width
    gone = "Keyboard delete %d" % width
    renamed = "Keyboard renamed %d" % width
    _add_package(world, keep)
    gone_id = _add_package(world, gone)
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    try:
        sign_in(page, live_server, world["emails"]["application_manager"])
        _open(page, live_server, world, "#list")

        # Enter on the menu's trigger opens it; Enter on Edit opens that row's editor.
        _open_row_menu_with_keyboard(page, keep)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("ArrowUp")
        page.keyboard.press("Enter")
        modal = page.locator("#roadmap-work-package-modal")
        expect(modal).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(modal.locator("input[type=text]").first).to_have_value(keep)
        modal.locator("input[type=text]").first.fill(renamed)
        with page.expect_response(re.compile(r"/api/applications/\d+/work-packages/\d+")) as saved:
            modal.get_by_role("button", name="Save Changes").click()
        assert saved.value.ok
        page.wait_for_load_state("domcontentloaded")
        expect(_row(page, renamed)).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(_row(page, keep + " ")).to_have_count(0)

        # Space on Delete asks first, then removes only that package.
        _open_row_menu_with_keyboard(page, gone)
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Space")
        dialog = page.get_by_role("alertdialog").or_(page.get_by_role("dialog")).filter(has_text="Delete work package")
        expect(dialog.first).to_be_visible(timeout=PAGE_TIMEOUT)
        with page.expect_response(re.compile(r"/api/applications/\d+/work-packages/\d+")) as deleted:
            dialog.first.get_by_role("button", name="Delete").click()
        assert deleted.value.ok
        page.wait_for_load_state("domcontentloaded")
        expect(_row(page, gone)).to_have_count(0, timeout=PAGE_TIMEOUT)
        assert _package_exists(world, gone_id) is None
        expect(_row(page, renamed)).to_be_visible()
    finally:
        context.close()


def test_list_badge_uses_the_one_status_class_for_every_status(page, live_server, world):
    for status in ("planned", "in_progress", "completed", "blocked"):
        _add_package(world, "Badge %s" % status, status)
    sign_in(page, live_server, world["emails"]["application_manager"])
    _open(page, live_server, world, "#list")
    for status in ("planned", "in_progress", "completed", "blocked"):
        badge = _row(page, "Badge %s" % status).locator("td span.rounded-full:visible").first
        expect(badge).to_be_visible(timeout=PAGE_TIMEOUT)
        expected = page.evaluate(
            "s => Alpine.$data(document.querySelector('[x-data=\"applicationRoadmapCrud()\"]')).statusBadgeClass(s)",
            status,
        )
        classes = badge.get_attribute("class").split()
        assert all(c in classes for c in expected.split()), (status, expected, classes)
