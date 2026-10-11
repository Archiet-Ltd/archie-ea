"""The product must read as Entelim on the surfaces a user actually opens.

The rename touched the wordmark in the base layouts, the login screen, the
dashboard welcome banner, the API docs title and the AI-chat entry points.
A source-level sweep can prove the old strings are gone from templates, but
only a browser can prove the page a user signs into is the one that renders,
and that the assistant surface reads as Entelim rather than a stale brand.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_login_page_reads_entelim(browser, live_server, seeded):
    """The unauth page a user opens names the product Entelim."""
    page = browser.new_page()
    try:
        page.goto(live_server + "/account/login", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Entelim", exact=True)).not_to_have_count(0)
        body = page.inner_text("body")
        assert "A.R.C.H.I.E." not in body
        assert "ARCHIE" not in body
    finally:
        page.close()


def test_dashboard_and_chat_read_entelim_after_login(browser, live_server, seeded):
    """Signed-in surfaces carry the Entelim wordmark, not the old one."""
    email = seeded["emails"]["solution_architect"]
    page = browser.new_page()
    try:
        _login(page, live_server, email)

        page.goto(live_server + "/dashboard/overview", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Entelim", exact=True)).not_to_have_count(0)
        body = page.inner_text("body")
        assert "A.R.C.H.I.E." not in body
        assert "ARCHIE" not in body

        page.goto(live_server + "/ai-chat", timeout=PAGE_TIMEOUT)
        body = page.inner_text("body")
        assert "Entelim" in body
        assert "A.R.C.H.I.E." not in body
    finally:
        page.close()