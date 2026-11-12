"""An architect opens an application with little recorded and the page says only what is true.

The goal: open an application with no category and no competitive advantage set, then
* see no empty Application Identity card and "Not recorded" for the advantage;
* set the category "AI code generation platform" and see it displayed with "AI" intact;
* open the full editor from the Technology tab and see one breadcrumb trail, no
  connector named (the organisation has none), and the authentication method typed there
  shown on the Technology tab afterwards.
"""

import os
import re
import uuid
from pathlib import Path

import pytest

from . import fresh_org
from .conftest import PAGE_TIMEOUT

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

    app = create_app("testing")
    with app.app_context():
        row = ApplicationComponent(
            name="Honest page %s" % uuid.uuid4().hex[:6], organization_id=org_id,
            description="Runs the ledger.",
        )
        db.session.add(row)
        db.session.flush()
        db.session.execute(
            db.text(
                "UPDATE application_components SET lifecycle_status = NULL, application_category = NULL,"
                " application_type = NULL, deployment_model = NULL, architecture_domain = NULL,"
                " version = NULL, application_code = NULL, competitive_advantage = NULL WHERE id = :i"
            ),
            {"i": row.id},
        )
        db.session.commit()
        ident = row.id
        db.session.remove()
    return ident


@pytest.fixture
def org(live_server):
    out = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
    out["app_id"] = _seed(out["org_id"])
    return out


def _body(page):
    return " ".join(page.locator("main").inner_text().split())


def test_unset_facts_are_honest_and_the_editor_has_one_trail(page, live_server, org):
    url = "%s/applications/%d" % (live_server, org["app_id"])
    fresh_org.sign_in(page, live_server, org["emails"][ARCHITECT])
    page.set_viewport_size({"width": 1440, "height": 900})

    page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert page.get_by_role("heading", name="Application Identity").count() == 0
    body = _body(page)
    assert "Competitive Advantage Not recorded" in body, body
    assert "Competitive Advantage No " not in body
    assert "add one in Architecture" not in body
    _shot(page, "overview-light-1440")

    # Set the category in the page's own editor.
    page.get_by_role("link", name=re.compile("^\\s*Edit\\s*$")).first.click()
    page.wait_for_selector("#edit-application_category")
    page.locator("#edit-application_category").fill("AI code generation platform")
    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name=re.compile("Save")).first.click()
    assert page.get_by_role("heading", name="Application Identity").count() == 1
    body = _body(page)
    assert "AI Code Generation Platform" in body, body
    assert "Ai Code" not in body

    # The full editor, reached from the Technology tab.
    page.goto(url + "?tab=technology", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.get_by_role("link", name=re.compile("Edit Technology Details")).click()
    page.wait_for_selector("#authentication_method")
    assert page.locator("nav[aria-label='Breadcrumb']").count() == 1
    assert "Abacus" not in page.locator("main").inner_text()
    _shot(page, "editor-light-1440")
    page.locator("#authentication_method").fill("SAML single sign-on")
    page.locator("#competitive_advantage").select_option("yes")
    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        page.get_by_role("button", name=re.compile("Save|Update")).last.click()

    page.goto(url + "?tab=technology", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert "Authentication Method SAML single sign-on" in _body(page)
    _shot(page, "technology-light-1440")
    page.goto(url, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert "Competitive Advantage Yes" in _body(page)


def test_the_page_holds_at_phone_width_in_the_dark(page, live_server, org):
    fresh_org.sign_in(page, live_server, org["emails"][ARCHITECT])
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("%s/applications/%d" % (live_server, org["app_id"]), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    page.get_by_test_id("sidebar-trigger").click()
    page.locator("#user-menu-btn").click()
    page.get_by_text("Dark theme").first.click()
    page.wait_for_function("() => document.documentElement.classList.contains('dark')")
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    assert page.get_by_role("heading", name="Application Identity").count() == 0
    assert "Competitive Advantage Not recorded" in _body(page)
    _shot(page, "overview-dark-390")
    for path, name in (("?tab=technology", "technology-dark-390"), ("/edit", "editor-dark-390")):
        page.goto("%s/applications/%d%s" % (live_server, org["app_id"], path), wait_until="networkidle",
                  timeout=PAGE_TIMEOUT)
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, (name, overflow)
        _shot(page, name)
    assert page.locator("nav[aria-label='Breadcrumb']").count() == 1
