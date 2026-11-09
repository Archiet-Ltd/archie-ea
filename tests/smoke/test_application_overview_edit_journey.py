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
WALK = os.environ.get("APPEDIT_WALK_DIR")


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
               "deployment_status": row.deployment_status, "version": row.version}
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
    _save(page)

    # Back in view mode, showing all three changes.
    assert "edit=1" not in page.url, page.url
    body = page.locator("main").inner_text()
    assert "Runs month-end close for finance." in body, body
    assert "2.5" in body
    assert page.locator("#edit-description").count() == 0, "still showing the form"
    _shot(page, "view-after-save-light-1440")

    page.reload(wait_until="networkidle", timeout=PAGE_TIMEOUT)
    body = page.locator("main").inner_text()
    assert "Runs month-end close for finance." in body and "2.5" in body
    stored = _stored(app_id)
    assert stored["description"] == "Runs month-end close for finance."
    assert stored["deployment_status"] == "production" and stored["version"] == "2.5"

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
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_test_id("sidebar-trigger").click()
    page.locator("#user-menu-btn").click()
    page.get_by_text("Dark theme").first.click()
    page.wait_for_function("() => document.documentElement.classList.contains('dark')")
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    page.locator("#edit-name-error").scroll_into_view_if_needed()
    assert page.locator("#edit-name-error").is_visible()
    _shot(page, "refused-dark-390")
