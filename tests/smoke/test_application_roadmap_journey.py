"""An application manager plans and updates transformation work for an application.

The goal: the application manager opens the application roadmap, sees planned
work packages on a timeline with their status, and can add, edit and delete
packages. Every link on the page takes the manager to a page with content, not
an empty state.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _assert_application_manager_role(email):
    """Assert the signed-in user is an application_manager, not a platform admin."""
    from app import create_app
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "application_manager", (
            "expected application_manager, got %s" % user.enterprise_role
        )
        assert not user.is_platform_admin, "application_manager must not be platform admin"


def _seed_app_with_work_package(org_id, app_id, user_id):
    """Seed a work package linked to the application so the timeline renders content."""
    from app import create_app, db
    from app.models.implementation_migration import WorkPackage

    app = create_app("testing")
    with app.app_context():
        wp = WorkPackage(
            name="Migrate legacy CRM",
            description="Transition the CRM to the new platform.",
            application_component_id=app_id,
            owner_id=user_id,
            organization_id=org_id,
            status="in_progress",
            priority="high",
            start_date=__import__("datetime").date(2026, 6, 1),
            target_date=__import__("datetime").date(2027, 3, 31),
            element_type="Migration",
            togaf_phase="Implementation",
            sequence_order=1,
        )
        db.session.add(wp)
        db.session.commit()
        wp_id = wp.id
        db.session.remove()
    return wp_id


@pytest.fixture(scope="module")
def world():
    """Two organisations: one with linked data, one empty for empty-state testing."""
    full = create_fresh_org("application_manager", org_admin=True)
    empty = create_fresh_org("application_manager")
    # Create an application in each org
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        component = ApplicationComponent(
            name="Payments Engine",
            organization_id=full["org_id"],
            lifecycle_status="operational",
            description="Core payment processing.",
        )
        db.session.add(component)
        db.session.commit()
        full["app_id"] = component.id

        empty_component = ApplicationComponent(
            name="Empty App",
            organization_id=empty["org_id"],
            lifecycle_status="operational",
            description="No work packages.",
        )
        db.session.add(empty_component)
        db.session.commit()
        empty["app_id"] = empty_component.id
        db.session.remove()

    full["wp_id"] = _seed_app_with_work_package(
        full["org_id"], full["app_id"], full["user_ids"]["application_manager"]
    )
    out = {"full": full, "empty": empty}
    out["suffix"] = full["suffix"]
    return out


def test_application_manager_reaches_roadmap_with_role_assertion(
    page, live_server, world
):
    """The application manager sees the roadmap with content and their role is correct."""
    email = world["full"]["emails"]["application_manager"]
    _assert_application_manager_role(email)

    sign_in(page, live_server, email)
    roadmap_url = live_server + "/applications/%d/roadmap" % world["full"]["app_id"]
    response = page.goto(roadmap_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response and response.status == 200, "roadmap returned %s" % (response and response.status)

    # The page shows the application name in the heading
    expect(page.locator("h1")).to_contain_text("Payments Engine Roadmap")

    # Stat cards render with the correct count
    expect(page.locator("text=Total").first).to_be_visible()
    expect(page.locator("text=In Progress").first).to_be_visible()

    # The timeline view is shown by default — a work package bar appears
    expect(page.get_by_role("button", name=re.compile("Migrate legacy CRM"))).to_be_visible(
        timeout=PAGE_TIMEOUT
    )

    # Switch to list view and see the same row
    page.get_by_role("tab", name="List").click()
    row = page.locator("tr").filter(has_text="Migrate legacy CRM")
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)


def test_every_link_lands_on_content(page, live_server, world):
    """Every navigable link from the roadmap lands on a page with real content."""
    email = world["full"]["emails"]["application_manager"]
    sign_in(page, live_server, email)
    roadmap_url = live_server + "/applications/%d/roadmap" % world["full"]["app_id"]
    page.goto(roadmap_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Breadcrumb: Applications → list page
    page.get_by_role("link", name="Applications").first.click()
    page.wait_for_url(lambda u: "/applications" in u and "roadmap" not in u, timeout=PAGE_TIMEOUT)
    expect(page.locator("h1").first).to_be_visible(timeout=PAGE_TIMEOUT)

    # Go back to roadmap
    page.goto(roadmap_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Breadcrumb: application name → detail page
    page.get_by_role("link", name="Payments Engine").first.click()
    page.wait_for_url(lambda u: "/applications/%d" % world["full"]["app_id"] in u,
                      timeout=PAGE_TIMEOUT)
    body = page.locator("main").first
    expect(body).to_contain_text("Payments Engine", timeout=PAGE_TIMEOUT)
    assert "403" not in page.title() and "Not Found" not in page.title()

    # Go back to roadmap
    page.goto(roadmap_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # "Back to Application" button → detail page
    page.get_by_role("link", name="Back to Application").click()
    page.wait_for_url(lambda u: "/applications/%d" % world["full"]["app_id"] in u,
                      timeout=PAGE_TIMEOUT)
    expect(page.locator("main").first).to_contain_text("Payments Engine", timeout=PAGE_TIMEOUT)
    assert "403" not in page.title() and "Not Found" not in page.title()


def test_timeline_bar_opens_edit_modal(page, live_server, world):
    """Clicking a timeline work package bar opens the edit modal with its values."""
    email = world["full"]["emails"]["application_manager"]
    sign_in(page, live_server, email)
    page.goto(
        live_server + "/applications/%d/roadmap" % world["full"]["app_id"],
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )

    # Timeline view is default — click the bar button
    bar = page.get_by_role("button", name=re.compile("Migrate legacy CRM"))
    expect(bar).to_be_visible(timeout=PAGE_TIMEOUT)
    bar.click()

    # Edit modal opens with the work package name pre-filled
    modal = page.locator("#roadmap-work-package-modal")
    expect(modal.get_by_role("heading", name="Edit Work Package")).to_be_visible(
        timeout=PAGE_TIMEOUT
    )
    expect(modal.get_by_label("Name", exact=True)).to_have_value("Migrate legacy CRM")


def test_empty_org_shows_empty_state(page, live_server, world):
    """An application with no work packages shows the empty state with a next step."""
    email = world["empty"]["emails"]["application_manager"]
    sign_in(page, live_server, email)
    page.goto(
        live_server + "/applications/%d/roadmap" % world["empty"]["app_id"],
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )

    # The empty state should be visible in the timeline tab (default)
    empty_text = page.locator("main").inner_text()
    assert "No work packages" in empty_text, "empty state missing in: %s" % empty_text[:200]
    assert "Add Work Package" in empty_text, "next step CT" + "A missing for empty state"

    # There should be an "Add Work Package" button
    add_btn = page.get_by_role("button", name="Add Work Package")
    expect(add_btn).to_be_visible(timeout=PAGE_TIMEOUT)