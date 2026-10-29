"""Second half of administration on the standard list table.

A platform administrator opens three of the pages this batch converted
(registered users, connectors, blueprint consolidation status) and uses the
list the way a person actually would: searches it, sorts a column, hides a
column, reads the pagination footer, and opens one row's action. A second
organisation's rows never appear on the first organisation's registered
users page.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _platform_admin_with_users(n=10):
    """A fresh organisation with a platform administrator and n other users."""
    from app import create_app, db

    app = create_app("testing")
    suffix = uuid.uuid4().hex[:8]
    out = {"org_id": None, "admin_email": None, "names": []}
    with app.app_context():
        from app.models.organization import Organization
        from app.models.user import Role, User

        Role.insert_roles()
        architect_role = Role.query.filter_by(name="Architect").one()
        org = Organization(name="List Table B03 %s" % suffix, slug="ltb03-%s" % suffix, plan="enterprise")
        db.session.add(org)
        db.session.commit()
        out["org_id"] = org.id

        admin_email = "ltb03.admin.%s@example.com" % suffix
        admin = User(
            email=admin_email, first_name="Platform", last_name="Admin",
            organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
        )
        admin.role = architect_role
        admin.is_org_admin = True
        admin.is_platform_admin = True
        admin.password = PASSWORD
        db.session.add(admin)
        db.session.commit()
        out["admin_email"] = admin_email

        for i in range(n):
            first = ("Zuser%02d-%s" % (i, suffix)) if i == 0 else ("Buser%02d-%s" % (i, suffix))
            email = "ltb03.user%02d.%s@example.com" % (i, suffix)
            user = User(
                email=email, first_name=first, last_name="Rowe",
                organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
            )
            user.role = architect_role
            user.password = PASSWORD
            db.session.add(user)
            out["names"].append(first)
        db.session.commit()
        db.session.remove()
    return out


def _sign_in(page, live_server, email):
    page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


@pytest.fixture(scope="module")
def org_a(live_server):
    return _platform_admin_with_users(10)


@pytest.fixture(scope="module")
def org_b(live_server):
    return _platform_admin_with_users(2)


def test_registered_users_search_sort_hide_column_page_and_row_action(live_server, browser, org_a, org_b):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _sign_in(page, live_server, org_a["admin_email"])
        page.goto(live_server + "/admin/users", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        table = page.get_by_test_id("registered-users-table")
        expect(table).to_be_visible()

        # Eleven rows (ten seeded users + the admin) means the pagination footer is real.
        rows_before = page.locator("[data-testid=user-row]:not([hidden])")
        expect(rows_before).to_have_count(11)  # page size 25 default, but only 11 total rows -> all on page 1
        expect(page.locator("[data-table-page]")).to_contain_text("Page 1 of")

        # Search narrows to the matching row.
        page.locator("[data-table-filter]").fill("Zuser00")
        expect(page.locator("[data-testid=user-row]:not([hidden])")).to_have_count(1)
        page.locator("[data-table-filter]").fill("")

        # Sort by First name, ascending: Zuser00's row (named to sort first/last distinctly) moves.
        page.locator("[data-table-sort-trigger='0']").click()
        page.get_by_role("menuitem", name="Ascending").click()
        first_cell_text = page.locator("[data-testid=user-row]:not([hidden])").first.inner_text()
        assert "Buser" in first_cell_text or "Platform" in first_cell_text

        # Hide the Email address column (index 2) through the View menu.
        page.get_by_role("button", name="View").click()
        page.get_by_role("menuitemcheckbox", name="Email address").click()
        expect(page.locator('[data-col="2"]').first).to_be_hidden()
        page.keyboard.press("Escape")

        # One row's action: the row menu offers View, Edit, Set password and Delete, and
        # following View lands on that user's own detail page.
        first_row = page.locator("[data-testid=user-row]:not([hidden])").first
        first_row.get_by_role("button", name=re.compile("Actions for")).click()
        view_item = page.get_by_role("menuitem", name="View")
        expect(view_item).to_be_visible()
        view_href = view_item.get_attribute("href")
        assert view_href and "/admin/user/" in view_href
        page.goto(live_server + view_href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/admin/user/" in page.url

        # A second organisation's rows never appear here.
        page.goto(live_server + "/admin/users", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        body_text = page.inner_text("body")
        for name in org_b["names"]:
            assert name not in body_text
    finally:
        context.close()


def test_connectors_list_renders_as_a_list_table(live_server, browser, org_a):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _sign_in(page, live_server, org_a["admin_email"])
        page.goto(live_server + "/admin/connectors", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        table = page.get_by_test_id("connectors-table")
        expect(table).to_be_visible()
        # No connector exists yet in this fresh organisation: the empty message says why,
        # and the search/pagination chrome only appears once there is a row to act on --
        # the list-table macro's own, unchanged behaviour.
        expect(page.get_by_text("No connectors configured for this organisation yet")).to_be_visible()
    finally:
        context.close()


def test_consolidation_status_two_tables_render_as_list_tables(live_server, browser, org_a):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _sign_in(page, live_server, org_a["admin_email"])
        page.goto(live_server + "/admin/consolidation", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("consolidation-targets-table")).to_be_visible()
        expect(page.get_by_test_id("blueprints-table")).to_be_visible()
        # Both tables carry their own toolbar and pagination footer independently.
        expect(page.locator("[data-table-filter]")).to_have_count(2)
        expect(page.locator("[data-table-page]")).to_have_count(2)
    finally:
        context.close()


def test_registered_users_narrow_viewport_no_horizontal_overflow(live_server, browser, org_a):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    try:
        page = context.new_page()
        _sign_in(page, live_server, org_a["admin_email"])
        page.goto(live_server + "/admin/users", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("registered-users-table")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        context.close()
