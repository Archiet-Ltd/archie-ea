"""Journey, in a browser, as the enterprise architect: correcting an
application on its Overview edit page.

The architect opens the edit form, types a new description, picks a deployment
status and types a version, presses Save, and finds the page back in view mode
showing all three changes, still showing them after a reload. A save the page
cannot accept stays on the form, says which field and why, keeps what was
typed and saves nothing. A second organisation cannot save to the application
by its id.
"""

import os
import re
import uuid
from pathlib import Path

import pytest

from .conftest import PAGE_TIMEOUT
from . import fresh_org

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ARCHITECT = "enterprise_architect"

# Opt-in walk screenshots: off unless a directory is named.
WALK = os.environ.get("SMOKE_SCREENSHOT_DIR")


def _shot(page, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _seed(org_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        row = ApplicationComponent(
            name="Edit journey %s" % uuid.uuid4().hex[:6], organization_id=org_id,
            description="Old description", deployment_status="development",
            version="1.0", business_domain="Sales", lifecycle_status="operational",
        )
        db.session.add(row)
        db.session.commit()
        ident = row.id
        db.session.remove()
    return ident


def _stored(app_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        row = db.session.get(ApplicationComponent, app_id)
        out = {"name": row.name, "description": row.description,
               "deployment_status": row.deployment_status, "version": row.version,
               "category": row.application_category, "business_owner": row.business_owner}
        db.session.remove()
    return out


@pytest.fixture
def orgs():
    out = []
    for _ in range(2):
        org = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
        org["app_id"] = _seed(org["org_id"])
        out.append(org)
    return out


def _phone_dark(page):
    """Phone width in the dark theme, through the app's own theme switch."""
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_test_id("sidebar-trigger").click()
    page.locator("#user-menu-btn").click()
    page.get_by_text("Dark theme").first.click()
    page.wait_for_function("() => document.documentElement.classList.contains('dark')")
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")


def _save(page):
    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name=re.compile("Save")).first.click()


def test_architect_corrects_an_application_and_the_page_shows_it(page, live_server, orgs):
    org_a, org_b = orgs
    app_id = org_a["app_id"]
    url = "%s/applications/%d" % (live_server, app_id)
    fresh_org.sign_in(page, live_server, org_a["emails"][ARCHITECT])

    page.goto(url + "?edit=1&tab=overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.set_viewport_size({"width": 1440, "height": 900})
    _shot(page, "edit-light-1440")

    description = page.locator("#edit-description")
    description.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("Runs month-end close for finance.")
    status = page.locator("#edit-deployment_status")
    assert "Live" not in status.inner_text(), "status is offered as free text, not a list"
    status.focus()
    page.keyboard.type("Production")
    version = page.locator("#edit-version")
    version.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("2.5")
    # The form holds only what the stored column holds: 60 typed, 50 kept.
    category = page.locator("#edit-application_category")
    category.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("x" * 60)
    assert len(category.input_value()) == 50, len(category.input_value())
    _save(page)

    # Back in view mode, showing all three changes.
    assert "edit=1" not in page.url, page.url
    body = page.locator("main").inner_text()
    assert "Runs month-end close for finance." in body, body
    assert "2.5" in body
    header = page.locator("main").first.inner_text()
    assert "Production" in header, "the deployment status chosen is not shown after the save"
    assert page.locator("#edit-description").count() == 0, "still showing the form"
    _shot(page, "view-after-save-light-1440")

    page.reload(wait_until="networkidle", timeout=PAGE_TIMEOUT)
    body = page.locator("main").inner_text()
    assert "Runs month-end close for finance." in body and "2.5" in body
    stored = _stored(app_id)
    assert stored["description"] == "Runs month-end close for finance."
    assert stored["deployment_status"] == "production" and stored["version"] == "2.5"
    assert stored["category"] == "x" * 50

    # The edit page itself offers the saved status as the chosen one.
    page.goto(url + "?edit=1&tab=overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert page.locator("#edit-deployment_status").input_value() == "production"

    # A second organisation cannot reach or save to it by id.
    page.context.clear_cookies()
    fresh_org.sign_in(page, live_server, org_b["emails"][ARCHITECT])
    own = page.goto("%s/applications/%d?edit=1" % (live_server, org_b["app_id"]),
                    wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert own.status == 200
    token = page.locator("input[name=csrf_token]").first.get_attribute("value")
    status_code = page.evaluate(
        """async ([id, token]) => {
            const body = new URLSearchParams({csrf_token: token, description: 'Hijacked'});
            const r = await fetch('/applications/' + id + '/overview-update',
                {method: 'POST', body, credentials: 'same-origin', redirect: 'manual'});
            return r.status;
        }""",
        [app_id, token],
    )
    assert status_code == 404, status_code
    assert _stored(app_id)["description"] == "Runs month-end close for finance."


def test_a_refused_save_names_the_field_keeps_the_typing_and_saves_nothing(
    page, live_server, orgs
):
    org_a = orgs[0]
    app_id = org_a["app_id"]
    fresh_org.sign_in(page, live_server, org_a["emails"][ARCHITECT])
    page.goto("%s/applications/%d?edit=1&tab=overview" % (live_server, app_id),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)

    name = page.locator("#edit-name")
    name.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    description = page.locator("#edit-description")
    description.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("Typed but not yet saved.")
    _save(page)

    error = page.locator("#edit-name-error")
    assert error.inner_text().strip() == "Name is required."
    alert = page.locator("[data-testid=overview-form-errors]")
    assert "Nothing was saved" in alert.inner_text()
    assert page.locator("#edit-description").input_value() == "Typed but not yet saved."
    assert page.locator("#edit-name").get_attribute("aria-invalid") == "true"
    _shot(page, "refused-light-1440")
    assert _stored(app_id)["description"] == "Old description"

    # The same page at phone width, in the dark theme, through the app's own switch.
    _phone_dark(page)
    page.locator("#edit-name-error").scroll_into_view_if_needed()
    assert page.locator("#edit-name-error").is_visible()
    _shot(page, "refused-dark-390")


def _seed_owned(org_id, owner_name):
    """An application whose business owner, on record, is a user of the organisation."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        owner = User(email="owner.%s@example.com" % uuid.uuid4().hex[:8],
                     first_name=owner_name.split()[0], last_name=owner_name.split()[1],
                     organization_id=org_id, confirmed=True, enterprise_role="application_manager")
        owner.role = Role.query.filter_by(name="User").one()
        db.session.add(owner)
        db.session.flush()
        row = ApplicationComponent(
            name="Owned journey %s" % uuid.uuid4().hex[:6], organization_id=org_id,
            description="Old description", deployment_status="development",
        )
        db.session.add(row)
        db.session.flush()
        row.business_owner = owner_name
        db.session.commit()
        ident = row.id
        db.session.remove()
    return ident


def test_an_owner_nobody_matches_is_refused_and_a_real_person_is_saved(page, live_server):
    org = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
    app_id = _seed_owned(org["org_id"], "Olive Owner")
    url = "%s/applications/%d" % (live_server, app_id)
    fresh_org.sign_in(page, live_server, org["emails"][ARCHITECT])
    page.goto(url + "?edit=1&tab=overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)

    owner = page.locator("#edit-businessOwner")
    assert owner.input_value() == "Olive Owner"
    owner.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("Nobody Atall")
    _save(page)

    error = page.locator("#edit-businessOwner-error")
    assert "no one in this organisation is called" in error.inner_text()
    assert "Application updated" not in page.locator("body").inner_text()
    assert page.locator("#edit-businessOwner").input_value() == "Nobody Atall", "typing was lost"
    _shot(page, "owner-refused-light-1440")
    _phone_dark(page)
    page.locator("#edit-businessOwner-error").scroll_into_view_if_needed()
    assert page.locator("#edit-businessOwner-error").is_visible()
    _shot(page, "owner-refused-dark-390")
    page.set_viewport_size({"width": 1440, "height": 900})
    assert _stored(app_id)["business_owner"] == "Olive Owner"

    # Cancel lands on the view page, which still names the owner on record.
    page.get_by_role("link", name="Cancel").first.click()
    page.wait_for_load_state("networkidle")
    assert "edit=1" not in page.url
    body = page.locator("main").inner_text()
    assert "Olive Owner" in body and "Nobody Atall" not in body, body

    # A name that matches a person in the organisation changes the owner.
    page.goto(url + "?edit=1&tab=overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.locator("#edit-businessOwner").click()
    page.keyboard.press("Control+A")
    page.keyboard.type("Launch enterprise_a")
    _save(page)
    assert "edit=1" not in page.url, page.url
    assert "Launch enterprise_a" in page.locator("main").inner_text()
    assert _stored(app_id)["business_owner"] == "Launch enterprise_a"


def test_a_person_who_may_not_edit_is_offered_no_edit_and_cannot_save(page, live_server):
    org = fresh_org.create_fresh_org("procurement_manager", org_admin=False, platform_role="User")
    app_id = _seed(org["org_id"])
    url = "%s/applications/%d" % (live_server, app_id)
    fresh_org.sign_in(page, live_server, org["emails"]["procurement_manager"])

    opened = page.goto(url + "?tab=overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert opened.status == 200
    assert page.get_by_role("link", name=re.compile(r"^\s*Edit\s*$")).count() == 0, "an Edit link is offered"
    page.get_by_text("Old description").first.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    body = page.locator("main").inner_text()
    assert "Edit journey" in body and "Old description" in body, "the page has no content: " + body

    page.goto(url + "?edit=1&tab=overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert page.locator("#overview-edit-form").count() == 0, "the edit form is offered"
    assert page.locator("#edit-description").count() == 0
    assert "Old description" in page.locator("main").inner_text()
    _shot(page, "read-only-light-1440")

    token = page.locator("meta[name=csrf-token]").get_attribute("content")
    status_code = page.evaluate(
        """async ([id, token]) => {
            const body = new URLSearchParams({csrf_token: token, description: 'Overwritten'});
            const r = await fetch('/applications/' + id + '/overview-update',
                {method: 'POST', body, credentials: 'same-origin', redirect: 'manual'});
            return r.status;
        }""",
        [app_id, token],
    )
    assert status_code == 403, status_code
    assert _stored(app_id)["description"] == "Old description"


def test_choosing_decommissioned_shows_the_application_as_ended(page, live_server, orgs):
    org_a = orgs[0]
    app_id = org_a["app_id"]
    fresh_org.sign_in(page, live_server, org_a["emails"][ARCHITECT])
    page.goto("%s/applications/%d?edit=1&tab=overview" % (live_server, app_id),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.locator("#edit-deployment_status").select_option("decommissioned")
    _save(page)

    assert _stored(app_id)["deployment_status"] == "decommissioned"
    banner = page.get_by_text("RETIRED \u2014").first
    assert banner.is_visible(), "no banner for a decommissioned application"
    assert "Decommissioned" in page.locator("main").inner_text()
    _shot(page, "decommissioned-light-1440")


def _set_status(app_id, status):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        db.session.get(ApplicationComponent, app_id).deployment_status = status
        db.session.commit()
        db.session.remove()


def test_a_stored_status_in_another_case_is_offered_once_and_kept_on_save(page, live_server, orgs):
    org_a, org_b = orgs
    app_id = org_a["app_id"]
    _set_status(app_id, "Production")
    _set_status(org_b["app_id"], "Production")
    fresh_org.sign_in(page, live_server, org_a["emails"][ARCHITECT])
    page.goto("%s/applications/%d?edit=1&tab=overview" % (live_server, app_id),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.set_viewport_size({"width": 1440, "height": 900})

    select = page.locator("#edit-deployment_status")
    shown = [o.strip() for o in select.locator("option").all_inner_texts() if o.strip() != "Not set"]
    assert shown == ["Design", "Development", "Testing", "Staging", "Production",
                     "Retiring", "Decommissioned"], shown
    assert select.input_value() == "production", "the stored status is not the one chosen"
    _shot(page, "status-stored-production-light-1440")

    description = page.locator("#edit-description")
    description.click()
    page.keyboard.press("Control+A")
    page.keyboard.type("Status left alone.")
    _save(page)
    assert "edit=1" not in page.url, page.url
    assert "Status left alone." in page.locator("main").inner_text()
    assert "Production" in page.locator("main").first.inner_text()
    stored = _stored(app_id)
    assert stored["description"] == "Status left alone."
    assert stored["deployment_status"] == "Production", "a save with no pick rewrote the status"
    assert _stored(org_b["app_id"])["deployment_status"] == "Production"

    page.goto("%s/applications/%d?edit=1&tab=overview" % (live_server, app_id),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)
    _phone_dark(page)
    page.locator("#edit-deployment_status").scroll_into_view_if_needed()
    assert page.locator("#edit-deployment_status").is_visible()
    _shot(page, "status-stored-production-dark-390")
    page.locator("#edit-deployment_status").select_option("retiring")
    _save(page)
    assert _stored(app_id)["deployment_status"] == "retiring"
    assert page.get_by_text("RETIRING \u2014").first.is_visible()
