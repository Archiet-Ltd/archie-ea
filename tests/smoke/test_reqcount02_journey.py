"""A requirement figure's link lands with the card's title in view, and each requirement row opens that requirement.

The person: an architect whose organisation adopted DORA, who links a requirement to an application,
then follows that figure link and expects the card's title to sit below the top bar, and clicks a
requirement row to land on that requirement's own anchor, marked on the framework page.
"""

import os
import re
import time
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.test_connect_github_to_cited_answer_journey import SKIP_WELCOME

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("REQCOUNT02_SHOTS")


def new_page(browser, width=1440, scheme="light"):
    context = browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme
    )
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(90000)
    context.set_default_navigation_timeout(90000)
    return context, context.new_page()


def shoot(page, name, width, scheme):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    page.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, scheme)), full_page=True)


def _application_for(app, org_id, name):
    with app.app_context():
        from app import db
        from app.models.application_portfolio import ApplicationComponent

        row = ApplicationComponent(name=name, organization_id=org_id)
        db.session.add(row)
        db.session.commit()
        app_id = row.id
        db.session.remove()
    return app_id


def _adopt_dora(app, org_id):
    """Adopt the DORA framework for the organisation so its controls load."""
    with app.app_context():
        from app.modules.account.services import onboarding_service as onboarding

        onboarding._adopt_frameworks(org_id, ["dora"])


def _wait_for_framework(page, base, label):
    deadline = time.monotonic() + 120
    while True:
        page.goto(base + "/dashboard/compliance", wait_until="domcontentloaded")
        if page.locator("tr", has_text=re.compile(r"\b%s\b" % label)).filter(has_text="Adopted").count():
            return
        assert time.monotonic() < deadline, "%s was never loaded" % label
        page.wait_for_timeout(3000)


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_requirement_figure_lands_with_title_in_view(browser, live_server, app, width, scheme):
    """Following the Framework requirements tile link on an application page lands with the card's title visible below the top bar."""
    base = str(live_server)
    # Create a fresh organisation with an enterprise architect.
    org = create_fresh_org("enterprise_architect", org_admin=True)
    org_id = org["org_id"]
    email = org["emails"]["enterprise_architect"]

    context, page = new_page(browser, width, scheme)
    try:
        sign_in(page, base, email)
        page.wait_for_url("**/dashboard/**")

        app_name = "Payments core %s" % uuid.uuid4().hex[:6]
        app_id = _application_for(app, org_id, app_name)

        # Adopt DORA framework so its controls load on the compliance page.
        _adopt_dora(app, org_id)
        _wait_for_framework(page, base, "DORA")
        row = page.locator("tr", has_text=re.compile(r"\bDORA\b")).filter(has_text="Adopted").first
        row.click()

        # Link a requirement to the application from the application page.
        page.goto("%s/applications/%d" % (base, app_id), wait_until="domcontentloaded")
        card = page.get_by_test_id("application-requirements")
        option = card.locator("option", has_text=re.compile("DORA-Art")).first
        card.get_by_label("Requirement to link").select_option(value=option.get_attribute("value"))
        card.get_by_role("button", name="Link framework requirement").click()
        page.wait_for_url(re.compile(r"/applications/%d$" % app_id))

        # The Framework requirements tile links to #application-requirements on the overview tab.
        # Click that tile's link and measure that the card title sits below the top bar.
        tile = page.locator("[data-slot=card]", has_text="Framework requirements").filter(
            has=page.get_by_test_id("metric-link")
        ).first
        tile_link = tile.get_by_test_id("metric-link")
        tile_link.click()
        page.wait_for_selector("[data-testid=application-requirements]")

        # Measure: card title's bounding box top >= top bar's bottom.
        top_bar = page.locator("#admin-header")
        card_title = page.get_by_test_id("application-requirements").locator("h3").first
        top_bar_bottom = top_bar.bounding_box()["y"] + top_bar.bounding_box()["height"]
        card_title_top = card_title.bounding_box()["y"]
        # An anchor jump on a long page scrolls the card to the very top; only a
        # scroll margin at least as tall as the sticky bar keeps its title clear.
        # Short pages never scroll, so read the margin itself.
        margin_top = page.get_by_test_id("application-requirements").evaluate(
            "el => parseFloat(getComputedStyle(el).scrollMarginTop)"
        )
        bar_height = top_bar.bounding_box()["height"]
        assert margin_top >= bar_height, (
            "card scroll margin %s is smaller than the top bar height %s" % (margin_top, bar_height)
        )
        assert card_title_top >= top_bar_bottom, (
            "card title top %s is above top bar bottom %s" % (card_title_top, top_bar_bottom)
        )

        # Now follow a requirement row link to the framework page and verify the marked row matches.
        linked = page.get_by_test_id("application-requirement").first
        href = linked.get_by_role("link").get_attribute("href")
        assert "#requirement-" in href, "figure link must name the requirement: %r" % href
        expected_id = href.split("#requirement-")[-1]

        # Follow the figure: land on the framework page at the requirement anchor.
        linked.get_by_role("link").click()
        page.wait_for_url(re.compile(r"/dashboard/compliance\?framework=\d+"))

        # The requirement row gets its id via Alpine's :id binding after the
        # framework is expanded and controls are loaded. Wait for it to render,
        # then check the anchor highlight before the 10s auto-remove expires.
        page.locator("tr[id^='requirement-']").first.wait_for(state="visible", timeout=30000)
        page.wait_for_timeout(500)

        marked = page.locator("tr[id^='requirement-'].ring-2")
        expect(marked).to_have_count(1)
        # The marked row's id must equal the href's fragment.
        marked_id = marked.first.get_attribute("id")
        assert marked_id == "requirement-%s" % expected_id, (
            "marked row id %s does not match href fragment requirement-%s" % (marked_id, expected_id)
        )
        expect(marked.first).to_have_class(re.compile(r"ring-primary"))
        expect(marked.first).to_have_class(re.compile(r"bg-muted/50"))

        shoot(page, "requirement-figure-lands", width, scheme)
    finally:
        context.close()