"""A fintech CTO chooses DORA (or NIS2) at sign-up and finds its requirements, then links one to an application.

The person: a CTO who has just signed up, first goal "audit". Their organisation already holds a payments
application. After Finish they open the compliance frameworks screen and see the regulation's requirements with
their article references, open the payments application, link one requirement to it, and see that link on the
application's page; the link opens the framework with its requirements listed. A second organisation that chose
nothing sees none of it.
"""

import os
import re
import time

import pytest
from playwright.sync_api import expect

from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401  (the module's fixtures)
    SHOTS,
    new_page,
    people,
    provider,
    server,
    shoot,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

CASES = {
    "DORA": {
        "label": "DORA",
        "expect": ["ICT Risk Management", "Incident Reporting", "Resilience Testing", "Third-Party Risk", "Information Sharing",
                   "Regulation (EU) 2022/2554, Article 5", "Regulation (EU) 2022/2554, Article 45"],
        "link": "DORA-Art.28",
        "linked_text": "ICT third-party risk and register of information",
    },
    "NIS2": {
        "label": "NIS2",
        "expect": ["Risk analysis and security policies", "Multi-factor authentication and secure communications",
                   "Directive (EU) 2022/2555, Article 21(2)(a)", "Directive (EU) 2022/2555, Article 21(2)(j)",
                   "Early warning of a significant incident"],
        "link": "NIS2-Art.21(2)(d)",
        "linked_text": "Supply chain security",
    },
}


def _frame(page, locator, name):
    """The one card a person is asked to look at, saved for the reviewer."""
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    locator.scroll_into_view_if_needed()
    locator.screenshot(path=os.path.join(SHOTS, name + ".png"))


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
        assert time.monotonic() < deadline, "%s was never loaded: %s" % (label, page.locator("main").inner_text()[:300])
        page.wait_for_timeout(3000)


@pytest.mark.parametrize("width", [1440, 390], ids=["1440", "390"])
@pytest.mark.parametrize("key", ["DORA", "NIS2"])
def test_a_cto_who_chose_the_regulation_sees_its_requirements_and_links_one_to_an_application(browser, server, people, app, key, width):
    case = CASES[key]
    base = str(server)
    context, page = new_page(browser, width)
    try:
        email, _started = sign_up(page, base, people)
        app_name = "Payments core %s" % key
        app_id = _application_for(app, email, app_name)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_test_id("signup-goal").locator("select").select_option("audit")
        if not page.get_by_label(case["label"], exact=True).is_visible():
            page.get_by_role("button", name="Show all frameworks").click()
        expect(page.get_by_label(case["label"], exact=True)).to_be_visible()
        page.get_by_label(case["label"], exact=True).check()
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        # The compliance frameworks screen lists the regulation as chosen at sign-up and opens onto its requirements.
        _wait_for_framework(page, base, case["label"])
        row = page.locator("tr", has_text=re.compile(r"\b%s\b" % case["label"])).filter(has_text="Adopted").first
        expect(row).to_contain_text("chosen at sign-up")
        row.click()
        detail = page.locator("tr:visible", has_text=case["expect"][-1]).first
        expect(detail).to_be_visible()
        panel = page.locator("td[colspan='7']:visible").first
        for text in case["expect"]:
            expect(panel).to_contain_text(text)
        expect(page.locator("main")).not_to_contain_text(re.compile("compliant|certified", re.I))
        shoot(page, "regulation-requirements-%s-%d" % (key.lower(), width))

        # From the application, the requirement is chosen and linked, and the link shows on that page.
        page.goto("%s/applications/%d" % (base, app_id), wait_until="domcontentloaded")
        card = page.get_by_test_id("application-requirements")
        expect(card.get_by_test_id("application-requirements-empty")).to_be_visible()
        _frame(page, card, "regulation-link-form-%s-%d" % (key.lower(), width))
        option = card.locator("option", has_text=case["link"]).first
        card.get_by_label("Requirement to link").select_option(value=option.get_attribute("value"))
        card.get_by_role("button", name="Link framework requirement").click()
        page.wait_for_url(re.compile(r"/applications/%d$" % app_id))
        linked = page.get_by_test_id("application-requirement")
        expect(linked).to_have_count(1)
        expect(linked).to_contain_text(case["linked_text"])
        expect(linked).to_contain_text(case["label"])
        expect(page.get_by_test_id("application-requirements")).not_to_contain_text("No requirement is linked")
        # It is still there after a reload, and the requirement is no longer offered again.
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_test_id("application-requirement")).to_have_count(1)
        expect(page.get_by_test_id("application-requirements").locator("option", has_text=case["link"])).to_have_count(0)
        _frame(page, page.get_by_test_id("application-requirements"), "regulation-linked-%s-%d" % (key.lower(), width))

        # The linked requirement opens the framework with its requirements listed, including the one linked.
        page.get_by_test_id("application-requirement").get_by_role("link").click()
        page.wait_for_url(re.compile(r"/dashboard/compliance\?framework=\d+"))
        expect(page.locator("tr:visible", has_text=case["linked_text"]).first).to_be_visible()
    finally:
        context.close()


def test_an_organisation_that_chose_no_framework_is_told_so_on_its_application(browser, server, people, app):
    context, page = new_page(browser)
    try:
        email, _started = sign_up(page, str(server), people)
        app_id = _application_for(app, email, "Ledger")
        page.goto("%s/applications/%d" % (server, app_id), wait_until="domcontentloaded")
        card = page.get_by_test_id("application-requirements")
        expect(card.get_by_test_id("application-requirements-none-adopted")).to_be_visible()
        card.get_by_role("link", name="Choose frameworks").click()
        page.wait_for_url("**/frameworks/")
        expect(page.locator("main")).to_contain_text("Frameworks")
    finally:
        context.close()
