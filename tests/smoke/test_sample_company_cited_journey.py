"""A startup CTO loads the sample company and reads a cited, complete first answer.

As the architect of a fresh workspace: loads the sample company and reads a toast
count that equals the card; opens the capability map and the capabilities list and
finds the five capabilities the model holds; asks what breaks if the payments
service fails and reads, on every connection, where it came from and (where the
model records one) its health, with each owner named once and no prompt to work
the indirect connections out; when an answer fails, presses Try again and gets it;
opens the removal dialog and closes it by its name.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_try_sample_company_journey import _empty_organisation_user, _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("SAMPLE_WALK_DIR")
FIVE = ["Process online payments", "Track customer orders", "Sell to customers online",
        "Manage stock", "Run customer support"]


def _shot(page, name, width):
    if WALK:
        page.screenshot(path=os.path.join(WALK, "%s-%d.png" % (name, width)), full_page=True)


def _ask_payments(page, base):
    page.goto(base + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator("#ask-picker-input").fill("Payments service")
    page.wait_for_selector("#ask-picker-listbox [role=option]")
    page.locator("#ask-picker-listbox [role=option]", has_text="Payments service").first.click()


@pytest.mark.parametrize("width", [1440, 390])
def test_first_answer_is_cited_and_the_figures_agree(browser, live_server, seeded, width):
    email, org_id = _empty_organisation_user(live_server)
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Load sample").first.click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)

        # The toast counts what the card counts.
        card = page.locator('[data-testid="health-kpi-elements"]').inner_text().strip()
        toast = page.get_by_text(re.compile(r"Sample company loaded: \d+ elements created")).first
        assert re.search(r"(\d+) elements", toast.inner_text()).group(1) == card
        _shot(page, "loaded", width)

        # The capability map and the list count the five capabilities.
        page.goto(live_server + "/capability-map/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#unified-cap-count")).to_have_text("5", timeout=PAGE_TIMEOUT)
        _shot(page, "capability-map", width)
        page.goto(live_server + "/enterprise/capability-map/capabilities", wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        for name in FIVE:
            expect(page.get_by_text(name, exact=True).first).to_be_attached()
        expect(page.get_by_text("No capabilities yet")).to_have_count(0)

        # The first answer: every connection cites its source; nothing asks to be worked out.
        _ask_payments(page, live_server)
        rows = page.locator("[data-ask-row]")
        expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert rows.count() >= 10
        expect(page.locator("[data-not-computed-notice]")).to_be_hidden()
        expect(page.locator("[data-citation-missing]:visible")).to_have_count(0)
        for i in range(rows.count()):
            row = rows.nth(i)
            source = row.locator("[data-citation]:visible").first.inner_text()
            assert "Sample company" in source or "Worked out from the model" in source, source
        _shot(page, "first-answer", width)

        # An application shows its health and its owner once; a capability its computed health.
        web = rows.filter(has_text="Customer web app").first
        assert web.get_by_label(re.compile(r"^Health: ")).get_attribute("aria-label") == "Health: Healthy"
        owner = web.get_by_label(re.compile(r"^Owner: ")).get_attribute("aria-label")
        assert owner == "Owner: Startup CTO", owner
        capability = rows.filter(has_text="Process online payments").first
        assert re.match(r"Health: (Healthy|Warning|Critical)$",
                        capability.get_by_label(re.compile(r"^Health: ")).get_attribute("aria-label"))
    finally:
        context.close()


def test_a_failed_answer_offers_try_again(browser, live_server, seeded):
    email, org_id = _empty_organisation_user(live_server)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Load sample").first.click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)

        failures = {"left": 1}

        def refuse_once(route):
            if failures["left"]:
                failures["left"] -= 1
                route.abort()
            else:
                route.continue_()

        page.route(re.compile(r"/api/v1/intelligence/impact/"), refuse_once)
        _ask_payments(page, live_server)
        expect(page.get_by_text("We could not answer that just now.").first).to_be_visible(timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Try again").click()
        expect(page.locator("[data-ask-row]").first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("We could not answer that just now.").first).to_be_hidden()
    finally:
        context.close()


def test_the_removal_dialog_closes_by_its_name(browser, live_server, seeded):
    email, org_id = _empty_organisation_user(live_server)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Load sample").first.click()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        page.get_by_role("button", name="Remove the sample company").first.click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, "dialog", 1440)
        dialog.get_by_role("button", name="Close").click()
        expect(dialog).to_be_hidden()
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible()
    finally:
        context.close()
