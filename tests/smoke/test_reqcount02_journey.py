"""A requirement figure's link lands with the card's title in view, and each requirement row opens that requirement.

The person: an architect whose organisation adopted DORA, who links a requirement to an application,
then follows that figure link and expects the card's title to sit below the top bar, and clicks a
requirement row to land on that requirement's own anchor, marked on the framework page.
"""

import os
import re
import time

import pytest
from playwright.sync_api import expect

from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401
    people,
    provider,
    server,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("REQCOUNT02_SHOTS")


def new_page(browser, width=1440, scheme="light"):
    context = browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme
    )
    context.set_default_timeout(90000)
    context.set_default_navigation_timeout(90000)
    return context, context.new_page()


def shoot(page, name, width, scheme):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    page.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, scheme)), full_page=True)


def _application_for(app, email, name):
    with app.app_context():
        from app import db
        from app.models.application_portfolio import ApplicationComponent
        from app.models.user import User

        org_id = User.query.filter_by(email=email).one().organization_id
        row = ApplicationComponent(name=name, organization_id=org_id)
        db.session.add(row)
        db.session.commit()
        app_id = row.id
        db.session.remove()
    return app_id


def _wait_for_framework(page, base, label):
    deadline = time.monotonic() + 120
    while True:
        page.goto(base + "/dashboard/compliance", wait_until="domcontentloaded")
        if page.locator("tr", has_text=re.compile(r"\b%s\b" % label)).filter(has_text="Adopted").count():
            return
        assert time.monotonic() < deadline, "%s was never loaded" % label
        page.wait_for_timeout(3000)


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_requirement_figure_lands_with_title_in_view(browser, server, people, app, width, scheme):
    """Following a requirement figure on an application page lands with the card's title visible below the top bar."""
    base = str(server)
    context, page = new_page(browser, width, scheme)
    try:
        email, _started = sign_up(page, base, people)
        app_name = "Payments core"
        app_id = _application_for(app, email, app_name)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_test_id("signup-goal").locator("select").select_option("audit")
        if not page.get_by_label("DORA", exact=True).is_visible():
            page.get_by_role("button", name="Show all frameworks").click()
        page.get_by_label("DORA", exact=True).check()
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

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

        # The figure link points at the requirement's own anchor, not the whole framework page.
        linked = page.get_by_test_id("application-requirement").first
        href = linked.get_by_role("link").get_attribute("href")
        assert "#requirement-" in href, "figure link must name the requirement: %r" % href

        # The card carries scroll margin so its title clears the top bar when scrolled into view.
        expect(card).to_have_class(re.compile(r"\bscroll-mt-12\b"))

        # Follow the figure: land on the framework page at the requirement anchor.
        linked.get_by_role("link").click()
        page.wait_for_url(re.compile(r"/dashboard/compliance\?framework=\d+"))

        # The requirement row gets its id via Alpine's :id binding after the
        # framework is expanded and controls are loaded. Wait for it to render,
        # then check the anchor highlight before the 2.5s auto-remove expires.
        page.locator("tr[id^='requirement-']").first.wait_for(state="visible", timeout=30000)
        page.wait_for_timeout(500)

        marked = page.locator("tr[id^='requirement-'].ring-2")
        expect(marked).to_have_count(1)
        expect(marked.first).to_have_class(re.compile(r"ring-primary"))
        expect(marked.first).to_have_class(re.compile(r"bg-muted/50"))

        shoot(page, "requirement-figure-lands", width, scheme)
    finally:
        context.close()
