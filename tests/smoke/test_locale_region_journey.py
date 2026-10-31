"""Journey: an administrator in Berlin sets the organisation's region and currency, and the pages that were converted
to the one formatter write dates and money the way a German company reads them.

The goal: "our weekly briefing and account page read in our own format, not London's". Red before the change: there
was no Region and currency control, and the briefing wrote "19 Oct 2026 to 25 Oct 2026" whatever the organisation.
"""
import re

import pytest
from playwright.sync_api import expect

from . import fresh_org
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

UK_DATE_RANGE = re.compile(r"^\d{1,2} [A-Z][a-z]{2} \d{4} to \d{1,2} [A-Z][a-z]{2} \d{4}$")
GERMAN_DATE_RANGE = re.compile(r"^\d{2}\.\d{2}\.\d{4} to \d{2}\.\d{2}\.\d{4}$")


def _briefing_range(page, base):
    page.goto(base + "/solutions/briefings", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    subtitle = page.locator("main").get_by_text(re.compile(r"\d{4} to .*\d{4}")).first
    expect(subtitle).to_be_visible()
    return subtitle.inner_text().strip()


def test_an_administrator_sets_germany_and_euros_and_the_pages_follow(live_server, browser):
    org = fresh_org.create_fresh_org("enterprise_architect", org_admin=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        fresh_org.sign_in(page, live_server, org["emails"]["enterprise_architect"])

        # Before: the London form on both pages.
        page.goto(live_server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card = page.get_by_test_id("region-card")
        expect(card).to_be_visible()
        expect(page.get_by_test_id("region-example")).to_have_text("£1,234.56 on 25 Oct 2026")
        assert UK_DATE_RANGE.match(_briefing_range(page, live_server)), "the briefing should read in the London form"

        # The administrator chooses Germany and euros and saves, and it is still chosen after a reload.
        page.goto(live_server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_label("Language and region").select_option("de-DE")
        page.get_by_label("Currency").select_option("EUR")
        page.get_by_test_id("region-save").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_text("Region and currency saved.")).to_be_visible()
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_label("Language and region")).to_have_value("de-DE")
        expect(page.get_by_label("Currency")).to_have_value("EUR")

        # After: a German company reads 1.234,56 € and 25.10.2026, on the account page and in the briefing.
        expect(page.get_by_test_id("region-example")).to_have_text("1.234,56 € on 25.10.2026")
        assert GERMAN_DATE_RANGE.match(_briefing_range(page, live_server)), "the briefing should read in the German form"

        # The same form fits a phone.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(live_server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("region-card")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        context.close()


def test_a_person_who_is_not_an_administrator_has_no_region_control_and_cannot_post_one(live_server, browser):
    org = fresh_org.create_fresh_org("enterprise_architect", org_admin=False)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        fresh_org.sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/account/manage", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Notification Preferences")).to_be_visible()
        expect(page.get_by_test_id("region-card")).to_have_count(0)
        token = page.locator("input[name=csrf_token]").first.get_attribute("value")
        reply = page.request.post(live_server + "/account/manage/region",
                                  form={"csrf_token": token, "locale": "de-DE", "currency": "EUR"})
        assert reply.status == 403
    finally:
        context.close()
