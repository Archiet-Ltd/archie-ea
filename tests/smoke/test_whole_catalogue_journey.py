"""A founder chooses ISO 27001 at sign-up and finds the whole of Annex A, from A.5.1 to A.8.34.

The person: a founder who has just signed up and wants to see what ISO 27001 asks of the company. After
Finish the frameworks page shows the standard as chosen at sign-up with its 93 controls and no
"part loaded" line; opening it lands on the compliance screen, which lists all 93, first A.5.1, last A.8.34.
"""

import re
import time

import pytest
from playwright.sync_api import expect

from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401  (the module's fixtures)
    new_page,
    people,
    provider,
    server,
    shoot,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _wait_for_card(page, base):
    deadline = time.monotonic() + 120
    while True:
        page.goto(base + "/frameworks/", wait_until="domcontentloaded")
        if page.locator("[data-framework-card='ISO - 27001']").count():
            return
        assert time.monotonic() < deadline, "ISO 27001 never loaded: %s" % page.locator("main").inner_text()[:300]
        page.wait_for_timeout(3000)


@pytest.mark.parametrize("width", [1440, 390], ids=["1440", "390"])
def test_a_founder_who_chose_iso_27001_sees_all_93_controls(browser, server, people, width):
    base = str(server)
    context, page = new_page(browser, width)
    try:
        sign_up(page, base, people)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_role("button", name="Show all frameworks").click()
        page.get_by_label("ISO 27001", exact=True).check()
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        _wait_for_card(page, base)
        card = page.locator("[data-framework-card='ISO - 27001']")
        expect(card).to_contain_text("Chosen at sign-up")
        expect(card).to_contain_text("93 controls")
        expect(card).to_contain_text("All 93 controls loaded")
        expect(card).not_to_contain_text(re.compile(r"\d+ of 93", re.I))
        shoot(page, "iso-27001-card-%d" % width)

        card.click()
        page.wait_for_url("**/dashboard/compliance")
        row = page.locator("tr", has_text="ISO/IEC 27001").first
        expect(row).to_be_visible()
        row.click()
        panel = page.locator("td[colspan='7']:visible").first
        expect(panel).to_contain_text("A.5.1")
        expect(panel).to_contain_text("A.8.34")
        codes = re.findall(r"\bA\.[5-8]\.\d{1,2}\b", panel.inner_text())
        assert len(set(codes)) == 93, len(set(codes))
        expect(page.locator("main")).not_to_contain_text(re.compile("compliant|certified|of 93", re.I))
        shoot(page, "iso-27001-controls-%d" % width)
    finally:
        context.close()
