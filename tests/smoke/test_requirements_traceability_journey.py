"""An enterprise architect opens requirements traceability and sees the matrix.

In a new organisation the architect creates a requirement, then opens the
sidebar link to requirements traceability and sees the requirement in the
traceability matrix. Every link lands on a page with content.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_requirements_traceability_shows_matrix(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])

        # Navigate to requirements traceability
        page.goto(live_server + "/enterprise/requirements-traceability",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Assert the page shell renders
        expect(page.get_by_role("heading", name="Requirements Traceability")).to_be_visible()

        # Assert the summary chips render
        expect(page.get_by_text("Draft")).to_be_visible()
        expect(page.get_by_text("Refined")).to_be_visible()
        expect(page.get_by_text("Approved")).to_be_visible()
        expect(page.get_by_text("Implemented")).to_be_visible()

        # Assert the traceability chain legend renders
        expect(page.get_by_role("heading", name="Traceability Chain (TOGAF 9.2 / ArchiMate 3.2)")).to_be_visible()
    finally:
        context.close()


def test_requirements_traceability_empty_state(browser, live_server):
    """A fresh organisation with no requirements sees the empty state."""
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/requirements-traceability",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("No requirements with traceability data yet.")).to_be_visible()
    finally:
        context.close()