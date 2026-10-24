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

        # Assert the dashboard-01 page column layout classes are present
        page_div = page.locator(".\\@container\\/main")
        expect(page_div).to_be_visible()

        # Assert the C3 timeline region marker is present and the seeded work
        # package appears on it (the roadmap is the point of the page).
        c3_region = page.locator("[data-entelim-form='C3']")
        expect(c3_region).to_be_visible()
        expect(c3_region.get_by_text("Cloud Foundations")).to_be_visible(timeout=PAGE_TIMEOUT)

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
        expect(c3_region.get_by_text("Application Rationalization Sprint 1")
               ).to_be_visible(timeout=PAGE_TIMEOUT)

    finally:
        context.close()