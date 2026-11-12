"""Journey, in a browser, as the enterprise architect: saving an application's
inline edit form when no requirement is linked to it.

The organisation has adopted a framework, so the page offers a requirement to
link. The architect opens the edit form of an application with nothing linked,
changes the description and presses Save: the page saves and shows the new text,
still there after a reload. The requirement is then linked through its own
action, and the page lists it. Linking with nothing chosen still asks for a
choice. A second organisation's application is untouched throughout.
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
WALK = os.environ.get("SMOKE_SCREENSHOT_DIR")


def _shot(page, name):
    if WALK:
        Path(WALK).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _seed(org_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.account.services import onboarding_service as onboarding

    app = create_app("testing")
    with app.app_context():
        onboarding._adopt_frameworks(org_id, ["dora"])
        row = ApplicationComponent(
            name="Edit save %s" % uuid.uuid4().hex[:6], organization_id=org_id,
            description="Old description",
        )
        db.session.add(row)
        db.session.commit()
        ident = row.id
        db.session.remove()
    return ident


def _description(app_id):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    app = create_app("testing")
    with app.app_context():
        value = db.session.get(ApplicationComponent, app_id).description
        db.session.remove()
    return value


@pytest.fixture
def orgs():
    out = []
    for _ in range(2):
        org = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
        org["app_id"] = _seed(org["org_id"])
        out.append(org)
    return out


def _phone_dark(page):
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_test_id("sidebar-trigger").click()
    page.locator("#user-menu-btn").click()
    page.get_by_text("Dark theme").first.click()
    page.wait_for_function("() => document.documentElement.classList.contains('dark')")
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")


def _edit_save_and_link(page, live_server, org, other, tag):
    app_id = org["app_id"]
    url = "%s/applications/%d" % (live_server, app_id)
    page.goto(url + "?edit=1&tab=overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    text = "Saved with nothing linked %s." % tag
    page.locator("#edit-description").click()
    page.keyboard.press("Control+A")
    page.keyboard.type(text)
    _shot(page, "edit-before-save-" + tag)
    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name=re.compile("Save")).first.click()

    assert "edit=1" not in page.url, "Save did nothing: still on " + page.url
    assert text in page.locator("main").inner_text()
    page.reload(wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert text in page.locator("main").inner_text()
    assert _description(app_id) == text
    assert _description(other["app_id"]) == "Old description"
    _shot(page, "view-after-save-" + tag)

    # Linking is its own action and still needs a choice.
    card = page.get_by_test_id("application-requirements")
    card.get_by_role("button", name="Link framework requirement").click()
    assert page.get_by_test_id("application-requirements-empty").is_visible()
    option = card.locator("option", has_text="DORA-Art.28").first
    card.get_by_label("Requirement to link").select_option(value=option.get_attribute("value"))
    card.get_by_role("button", name="Link framework requirement").click()
    page.wait_for_url(re.compile(r"/applications/%d$" % app_id))
    linked = page.get_by_test_id("application-requirement")
    assert linked.count() == 1
    assert "ICT third-party risk and register of information" in linked.inner_text()
    _shot(page, "linked-" + tag)


def test_save_works_in_the_inline_editor_with_no_requirement_linked_light_1440(page, live_server, orgs):
    org, other = orgs
    fresh_org.sign_in(page, live_server, org["emails"][ARCHITECT])
    page.set_viewport_size({"width": 1440, "height": 900})
    _edit_save_and_link(page, live_server, org, other, "light-1440")


def test_save_works_in_the_inline_editor_with_no_requirement_linked_dark_390(page, live_server, orgs):
    org, other = orgs
    fresh_org.sign_in(page, live_server, org["emails"][ARCHITECT])
    page.goto("%s/applications/%d" % (live_server, org["app_id"]), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    _phone_dark(page)
    _edit_save_and_link(page, live_server, org, other, "dark-390")
