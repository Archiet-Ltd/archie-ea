"""The portfolio manager plans the migration on the implementation planning dashboard.

The portfolio manager signs in, lands on the implementation planning dashboard
and sees the planning heading, the Gap register card, the shadcn breadcrumb
trail, and the Tier C3 timeline region that carries the migration roadmap.
They then do the two jobs the page serves: export the work package register
as a CSV they can open in Excel (the file lists the work package the
organisation has recorded), and create a new work package from the header
action. The signed-in user is the portfolio manager persona, not an
administrator or platform role.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ROLE = "portfolio_manager"


def _seed_work_package(app, org_id, user_id, name):
    """Record one work package through the canonical service so the export
    and the timeline carry data the person can rely on."""
    from app.services import work_package_service

    with app.app_context():
        work_package_service.create_work_package(
            organization_id=org_id,
            user_id=user_id,
            name=name,
            status="planned",
            priority="high",
            description="Serves the migration roadmap.",
        )
        from app import db

        db.session.commit()


def _seed_roadmap(app, org_id, user_id, label):
    """Two dated work packages, the second depending on the first, and one
    plateau with a target date: what a portfolio manager plans a migration with."""
    from datetime import date, datetime, timedelta

    from app import db
    from app.models.implementation_migration import Plateau
    from app.services import work_package_service

    with app.app_context():
        start = datetime.now() - timedelta(days=30)
        first = work_package_service.create_work_package(
            organization_id=org_id, user_id=user_id, name="%s Landing Zone" % label,
            status="in_progress", priority="high",
            start_date=start, end_date=start + timedelta(days=60))
        second = work_package_service.create_work_package(
            organization_id=org_id, user_id=user_id, name="%s Data Platform" % label,
            status="planned", priority="medium",
            start_date=start + timedelta(days=61), end_date=start + timedelta(days=150))
        work_package_service.add_dependency(second.id, first.id, organization_id=org_id)
        db.session.add(Plateau(name="%s Target Architecture" % label, organization_id=org_id,
                               sequence_order=1, target_date=date.today() + timedelta(days=120)))
        db.session.commit()
        return first.id, second.id


def _assert_persona(app, email):
    """The signed-in user is the portfolio manager persona, not an administrator."""
    from app.middleware.tenant_decorators import is_platform_admin
    from app.models.user import User
    from app.services.rbac_service import rbac_service

    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == ROLE, user.enterprise_role
        assert not is_platform_admin(user)
        assert not rbac_service.is_org_admin(user, user.organization_id)


def test_implementation_planning_dashboard_reachable(browser, live_server):
    """The portfolio manager reaches the planning dashboard, exports the work
    package register as CSV, and creates a new work package."""
    from app import create_app

    app = create_app("testing")
    org = create_fresh_org(ROLE)
    _assert_persona(app, org["emails"][ROLE])
    _seed_work_package(app, org["org_id"], org["user_ids"][ROLE], "Cloud Foundations")

    context = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"][ROLE])

        # Navigate to the implementation planning dashboard
        page.goto(live_server + "/implementation/",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The page is on the planning dashboard, not a 404 or a login page
        assert page.url.rstrip("/").endswith("/implementation"), page.url

        # Assert the page title is rendered (P01 page header pattern)
        heading = page.get_by_role("heading", name="Implementation Planning")
        expect(heading).to_be_visible()

        # Assert the Gap register card is present
        gap_card = page.get_by_text("Gap register", exact=True)
        expect(gap_card).to_be_visible()

        # Assert the export button works (P29 export-share pattern)
        export_trigger = page.get_by_role("button", name="Export", exact=True)
        expect(export_trigger).to_be_visible()

        # Assert the shadcn breadcrumb is present (P01 breadcrumb pattern)
        breadcrumb = page.locator("nav[aria-label='breadcrumb']")
        expect(breadcrumb).to_be_visible()
        expect(breadcrumb.get_by_text("Planning Dashboard")).to_be_visible()

        # Assert the dashboard-01 page column layout is present
        page_div = page.locator(".flex-1")
        expect(page_div.first).to_be_visible()

        # Assert the C3 timeline region marker is present and the seeded work
        # package appears on it (the roadmap is the point of the page).
        c3_region = page.locator("[data-entelim-form='C3']")
        expect(c3_region).to_be_visible()
        expect(c3_region.get_by_text("Cloud Foundations").first).to_be_visible(timeout=PAGE_TIMEOUT)

        # The person's job: export the work package register as a CSV. The file
        # must list the work package the organisation recorded.
        export_trigger.click()
        export_modal = page.locator("#export-impl-data")
        expect(export_modal).to_be_visible()
        expect(export_modal.get_by_role("heading", name="Export Implementation Data")).to_be_visible()
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Export CSV").click()
        download = download_info.value
        assert download.suggested_filename.endswith(".csv"), download.suggested_filename
        path = download.path()
        with open(path, encoding="utf-8-sig") as handle:
            csv_text = handle.read()
        assert "Cloud Foundations" in csv_text, csv_text
        assert "Name" in csv_text and "Status" in csv_text, csv_text
        # The export closes the dialog; the page is still on the dashboard.
        expect(export_modal).to_be_hidden()
        assert page.url.rstrip("/").endswith("/implementation"), page.url

        # The person's second job: create a work package from the header action.
        page.get_by_role("button", name="Create Work Package", exact=True).first.click()
        create_modal = page.locator("#create-work-package")
        expect(create_modal).to_be_visible()
        expect(create_modal.get_by_role("heading", name="Create Work Package")).to_be_visible()
        page.fill("#cwp-name", "Application Rationalization Sprint 1")
        create_modal.get_by_role("button", name="Create Work Package",
                                 exact=True).click()
        # The modal closes when the work package is saved and the register reloads.
        expect(create_modal).to_be_hidden(timeout=PAGE_TIMEOUT)
        # The new work package now shows on the timeline beside the seeded one.
        expect(c3_region.get_by_text("Application Rationalization Sprint 1").first
               ).to_be_visible(timeout=PAGE_TIMEOUT)

    finally:
        context.close()


def test_dashboard_timeline_shows_packages_dependencies_and_plateaus(browser, live_server):
    """The portfolio manager reads the migration plan on the library timeline:
    both work packages, the plateau, the dependency between them, and counts
    that agree with the register. Another organisation's plan never shows."""
    from app import create_app

    app = create_app("testing")
    org = create_fresh_org(ROLE)
    other = create_fresh_org(ROLE)
    _seed_roadmap(app, org["org_id"], org["user_ids"][ROLE], "Alpha")
    _seed_roadmap(app, other["org_id"], other["user_ids"][ROLE], "Zulu")

    context = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"][ROLE])
        page.goto(live_server + "/implementation/",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        region = page.get_by_role("region", name="Gantt timeline: Implementation Roadmap")
        expect(region).to_be_visible()
        rows = region.get_by_label("Timeline rows")
        expect(rows.get_by_text("Alpha Landing Zone").first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(rows.get_by_text("Alpha Data Platform").first).to_be_visible()
        # The plateau is on the timeline as a marked date.
        expect(region.get_by_text("Alpha Target Architecture").first).to_be_visible()
        # The figures agree with what was recorded: two packages, one plateau,
        # one in progress, none completed.
        stats = region.get_by_text("Work packages", exact=True).locator("xpath=preceding-sibling::div")
        expect(stats).to_have_text("2")
        expect(region.get_by_text("Plateaus", exact=True).locator("xpath=preceding-sibling::div")).to_have_text("1")
        expect(region.get_by_text("In progress", exact=True).locator("xpath=preceding-sibling::div")).to_have_text("1")
        expect(region.get_by_text("Completed", exact=True).locator("xpath=preceding-sibling::div")).to_have_text("0")
        # Another organisation's plan is not on this page.
        assert "Zulu" not in region.inner_text()

        # Opening the dependent package names the package it waits for.
        region.get_by_role("button", name="Alpha Data Platform", exact=False).first.click()
        panel = region.get_by_text("Depends On", exact=True).locator("xpath=..")
        expect(panel.get_by_text("Alpha Landing Zone")).to_be_visible()
    finally:
        context.close()


def test_timeline_on_a_phone_scrolls_inside_its_card_and_names_dates(browser, live_server):
    """On a phone the portfolio manager can still reach the zoom and Today
    buttons by scrolling the timeline sideways inside its card, the page
    itself does not scroll sideways, and a bar is named with its dates."""
    import re

    from app import create_app

    app = create_app("testing")
    org = create_fresh_org(ROLE)
    _seed_roadmap(app, org["org_id"], org["user_ids"][ROLE], "Alpha")

    context = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 390, "height": 844})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"][ROLE])
        page.goto(live_server + "/implementation/",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        region = page.get_by_role("region", name="Gantt timeline: Implementation Roadmap")
        bar = region.get_by_role("button", name=re.compile("Alpha Landing Zone,")).first
        expect(bar).to_be_visible(timeout=PAGE_TIMEOUT)

        # Bars read in dates, never in full timestamps.
        label = bar.get_attribute("aria-label")
        assert re.search(r"\d{4}-\d{2}-\d{2} to \d{4}-\d{2}-\d{2}$", label), label

        # The page does not scroll sideways; the card does.
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        scroller = region.evaluate_handle(
            "el => [el, ...el.querySelectorAll('*')].find("
            "n => n.scrollWidth > n.clientWidth + 1 && /(auto|scroll)/.test(getComputedStyle(n).overflowX)"
            " && n.contains(el.querySelector('button')))")
        assert scroller.as_element() is not None, "the timeline has no sideways scroller at 390"

        # Zoom and Today are reachable by scrolling, and clicking Today works.
        today = region.get_by_role("button", name="Scroll to today", exact=True)
        today.scroll_into_view_if_needed()
        box = today.bounding_box()
        card = region.bounding_box()
        assert box and 0 <= box["x"] and box["x"] + box["width"] <= card["x"] + card["width"] + 1, (box, card)
        today.click()
        months = region.get_by_role("button", name="months", exact=True)
        months.scroll_into_view_if_needed()
        months.click()

        # Opening a package keeps the close button on screen.
        bar.click()
        close = region.get_by_role("button", name="Close detail panel").first
        close.scroll_into_view_if_needed()
        expect(close).to_be_visible()
        cbox = close.bounding_box()
        assert cbox["x"] >= 0 and cbox["x"] + cbox["width"] <= 390 + 1, cbox
    finally:
        context.close()


def test_empty_timeline_names_its_next_step(browser, live_server):
    """An organisation with no work packages sees why the timeline is empty
    and a Create Work Package action that opens the create dialog."""
    org = create_fresh_org(ROLE)
    context = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"][ROLE])
        page.goto(live_server + "/implementation/",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        region = page.get_by_role("region", name="Gantt timeline: Implementation Roadmap")
        expect(region.get_by_text("No timeline items found")).to_be_visible(timeout=PAGE_TIMEOUT)
        action = region.get_by_role("button", name="Create Work Package")
        expect(action).to_be_visible()
        # On a phone the message and its action sit inside the card, not beyond its edge.
        page.set_viewport_size({"width": 390, "height": 844})
        card = region.bounding_box()
        abox = action.bounding_box()
        assert abox["x"] >= 0 and abox["x"] + abox["width"] <= card["x"] + card["width"] + 1, (abox, card)
        page.set_viewport_size({"width": 1440, "height": 1000})
        action.click()
        dialog = page.locator("#create-work-package")
        expect(dialog.get_by_role("heading", name="Create Work Package")).to_be_visible()
    finally:
        context.close()


def test_dialog_controls_save_their_choices_and_the_export_honours_its_checkboxes(browser, live_server):
    """The portfolio manager sets every field of a new work package (priority,
    status, dates, owner, description) and the saved record carries each choice;
    unticking Work Packages in the export leaves the register out of the file."""
    from app import create_app

    app = create_app("testing")
    org = create_fresh_org(ROLE)
    other = create_fresh_org(ROLE)
    _seed_work_package(app, org["org_id"], org["user_ids"][ROLE], "Cloud Foundations")
    _seed_work_package(app, other["org_id"], other["user_ids"][ROLE], "Other Tenant Programme")

    context = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"][ROLE])
        page.goto(live_server + "/implementation/", wait_until="domcontentloaded")
        c3_region = page.locator("[data-entelim-form='C3']")
        expect(c3_region.get_by_text("Cloud Foundations").first).to_be_visible(timeout=PAGE_TIMEOUT)

        # Export: both boxes start ticked; with Work Packages unticked the file
        # holds the header only, never the register.
        page.get_by_role("button", name="Export", exact=False).first.click()
        export_modal = page.locator("#export-impl-data")
        expect(export_modal).to_be_visible()
        expect(export_modal.get_by_label("Work Packages")).to_be_checked()
        export_modal.get_by_label("Work Packages").uncheck()
        expect(export_modal.get_by_label("Work Packages")).not_to_be_checked()
        with page.expect_download() as download_info:
            page.get_by_role("button", name="Export CSV").click()
        with open(download_info.value.path(), encoding="utf-8-sig") as handle:
            csv_text = handle.read()
        assert "Cloud Foundations" not in csv_text, csv_text
        assert csv_text.startswith("Name,Status,Priority"), csv_text

        # Create: every control carries its value into the saved record.
        page.get_by_role("button", name="Create Work Package", exact=True).first.click()
        create_modal = page.locator("#create-work-package")
        expect(create_modal).to_be_visible()
        expect(create_modal.get_by_label("Priority")).to_have_value("medium")
        expect(create_modal.get_by_label("Status")).to_have_value("planned")
        create_modal.get_by_label("Name").fill("Network Segmentation")
        create_modal.get_by_label("Priority").select_option("critical")
        create_modal.get_by_label("Status").select_option("in_progress")
        create_modal.get_by_label("Start Date").fill("2027-02-01")
        create_modal.get_by_label("End Date").fill("2027-05-01")
        create_modal.get_by_label("Assigned To").fill("Platform team")
        create_modal.get_by_label("Description").fill("Split the flat network into zones.")
        create_modal.get_by_role("button", name="Create Work Package", exact=True).click()
        expect(create_modal).to_be_hidden(timeout=PAGE_TIMEOUT)
        expect(c3_region.get_by_text("Network Segmentation").first).to_be_visible(timeout=PAGE_TIMEOUT)

        page.reload(wait_until="domcontentloaded")
        saved = page.evaluate(
            "fetch('/implementation/api/work-packages').then(r => r.json())")
        mine = [w for w in saved["work_packages"] if w["name"] == "Network Segmentation"]
        assert len(mine) == 1, saved
        assert mine[0]["priority"] == "critical", mine[0]
        assert mine[0]["status"] == "in_progress", mine[0]
        assert mine[0]["assigned_to"] == "Platform team", mine[0]
        assert str(mine[0]["start_date"]).startswith("2027-02-01"), mine[0]
        assert str(mine[0]["end_date"]).startswith("2027-05-01"), mine[0]
        assert all(w["name"] != "Other Tenant Programme" for w in saved["work_packages"])
    finally:
        context.close()
