"""Journey, in a browser, as the enterprise architect: an application's
requirement figure agrees with the requirements its page lists, and linking a
requirement while editing never drops what was typed.

The organisation has adopted a framework. One application has requirements
linked, one has none, and a second organisation has an application with its own
linked requirements. The figure at the top of each page names what it counts,
equals the rows of the list it links to, and that link lands on the list. In
edit mode, typing a change and then pressing the link button keeps the page,
says why nothing was linked, and the change is saved with the Save button.
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


def _seed(org_id, linked):
    """An adopted framework and two applications: one with `linked` requirements, one with none."""
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.modules.account.services import onboarding_service as onboarding
    from app.modules.compliance.services.applicability_service import ApplicabilityService

    app = create_app("testing")
    with app.app_context():
        onboarding._adopt_frameworks(org_id, ["dora"])
        options = ApplicabilityService.requirements_to_link(org_id)
        ids = []
        for label in ("Linked", "Bare"):
            row = ApplicationComponent(
                name="%s %s" % (label, uuid.uuid4().hex[:6]), organization_id=org_id,
                description="Old description",
            )
            db.session.add(row)
            db.session.commit()
            ids.append(row.id)
        for option in options[:linked]:
            ApplicabilityService.link_requirement(org_id, ids[0], option["id"])
        db.session.commit()
        db.session.remove()
    return {"linked": ids[0], "bare": ids[1], "count": linked}


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
    for linked in (3, 5):
        org = fresh_org.create_fresh_org(ARCHITECT, org_admin=False)
        org["apps"] = _seed(org["org_id"], linked)
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


def _figure(page):
    tile = page.locator("[data-slot=card]", has_text="Framework requirements").filter(
        has=page.get_by_test_id("metric-link")).first
    assert "Linked Requirements" not in page.locator("main").inner_text()
    return tile, int(tile.get_by_test_id("metric-link").inner_text().strip())


def _figures_agree(page, live_server, app_id, expected, tag):
    page.goto("%s/applications/%d" % (live_server, app_id), wait_until="networkidle", timeout=PAGE_TIMEOUT)
    tile, figure = _figure(page)
    assert figure == expected, "tile reads %s, expected %s" % (figure, expected)
    tile.get_by_test_id("metric-link").click()
    page.wait_for_selector("[data-testid=application-requirements]")
    card = page.get_by_test_id("application-requirements")
    assert card.get_by_test_id("application-requirement").count() == figure
    assert ("(%d)" % figure) in card.locator("h3").inner_text()
    if figure:
        first = card.get_by_test_id("application-requirement").first.locator("a")
        first.click()
        page.wait_for_load_state("networkidle")
        assert "DORA" in page.locator("main").inner_text()
    else:
        assert card.get_by_test_id("application-requirements-empty").is_visible()
    _shot(page, "figures-%d-%s" % (expected, tag))


def _edit_then_link(page, live_server, app_id, tag):
    page.goto("%s/applications/%d?edit=1&tab=overview" % (live_server, app_id),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)
    text = "Typed before linking %s." % tag
    page.locator("#edit-description").click()
    page.keyboard.press("Control+A")
    page.keyboard.type(text)
    card = page.get_by_test_id("application-requirements")
    option = card.locator("option", has_text="DORA-Art.28").first
    card.get_by_label("Requirement to link").select_option(value=option.get_attribute("value"))
    card.get_by_role("button", name="Link framework requirement").click()

    blocked = page.get_by_test_id("link-requirement-blocked")
    assert blocked.is_visible()
    assert "not saved" in blocked.inner_text()
    assert "edit=1" in page.url, "the page left edit mode: " + page.url
    assert page.locator("#edit-description").input_value() == text
    assert card.get_by_test_id("application-requirement").count() == 0
    _shot(page, "link-blocked-" + tag)

    with page.expect_navigation(wait_until="networkidle", timeout=PAGE_TIMEOUT):
        blocked.get_by_role("button", name="Save changes").click()
    assert "edit=1" not in page.url
    assert text in page.locator("main").inner_text()
    assert _description(app_id) == text

    page.goto("%s/applications/%d?edit=1&tab=overview" % (live_server, app_id),
              wait_until="networkidle", timeout=PAGE_TIMEOUT)
    card = page.get_by_test_id("application-requirements")
    option = card.locator("option", has_text="DORA-Art.28").first
    card.get_by_label("Requirement to link").select_option(value=option.get_attribute("value"))
    card.get_by_role("button", name="Link framework requirement").click()
    page.wait_for_url(re.compile(r"/applications/%d$" % app_id))
    assert page.get_by_test_id("application-requirement").count() == 1
    _tile, figure = _figure(page)
    assert figure == 1


def _walk(page, live_server, orgs, tag):
    org, other = orgs
    _figures_agree(page, live_server, org["apps"]["linked"], 3, tag)
    _figures_agree(page, live_server, org["apps"]["bare"], 0, tag)
    _edit_then_link(page, live_server, org["apps"]["bare"], tag)
    # The second organisation's five requirements never reach this organisation.
    page.goto("%s/applications/%d" % (live_server, other["apps"]["linked"]), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    assert page.get_by_test_id("application-requirement").count() == 0


def test_requirement_figures_agree_and_linking_keeps_edits_light_1440(page, live_server, orgs):
    fresh_org.sign_in(page, live_server, orgs[0]["emails"][ARCHITECT])
    page.set_viewport_size({"width": 1440, "height": 900})
    _walk(page, live_server, orgs, "light-1440")


def test_requirement_figures_agree_and_linking_keeps_edits_dark_390(page, live_server, orgs):
    fresh_org.sign_in(page, live_server, orgs[0]["emails"][ARCHITECT])
    page.goto("%s/applications/%d" % (live_server, orgs[0]["apps"]["linked"]), wait_until="networkidle",
              timeout=PAGE_TIMEOUT)
    _phone_dark(page)
    _walk(page, live_server, orgs, "dark-390")


def test_each_organisation_counts_only_its_own_requirements(page, live_server, orgs):
    """Two real organisations: each page's figure is its own organisation's count."""
    for org, expected in zip(orgs, (3, 5)):
        context = page.context.browser.new_context()
        other_page = context.new_page()
        try:
            fresh_org.sign_in(other_page, live_server, org["emails"][ARCHITECT])
            other_page.goto("%s/applications/%d" % (live_server, org["apps"]["linked"]),
                            wait_until="networkidle", timeout=PAGE_TIMEOUT)
            _tile, figure = _figure(other_page)
            assert figure == expected
            assert other_page.get_by_test_id("application-requirement").count() == expected
        finally:
            context.close()
