"""The usage analytics dashboard states its privacy sentence in a real browser.

The dashboard was extended to show the same "Collect anonymous usage
analytics to improve the application" sentence the settings page already
carries, above its summary cards. This loads the page in a real browser as a
signed-in user and checks the sentence is actually on the rendered page, not
only in the template source.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_dashboard_renders_the_privacy_sentence(browser, live_server, seeded):
    page = browser.new_page()
    try:
        _login(page, live_server, seeded["emails"]["solution_architect"])
        response = page.goto(live_server + "/usage-analytics/dashboard", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        sentence = page.get_by_text(
            "Collect anonymous usage analytics to improve the application"
        )
        expect(sentence).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        page.close()
