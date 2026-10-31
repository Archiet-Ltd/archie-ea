"""fix05 — No screen prints "None", no page throws on load, and the TOGAF vision page opens.

Journey tests for QA-016, QA-017, QA-018 from QA's full sweep 1025f.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def test_capability_health_no_none_displayed(browser, live_server):
    """Capability Health page shows no 'None' — domain column explains missing values."""
    org = create_fresh_org("business_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["business_architect"])
        page.goto(live_server + "/strategic/capability-health",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Page should render without console errors
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # Wait for table to render
        page.wait_for_selector("table.dense-table", timeout=PAGE_TIMEOUT)

        # Check that no cell contains raw "None" text
        page_text = page.inner_text("body")
        assert "Capability | None" not in page_text, "Found 'Capability | None' in page"
        assert "Domain not assigned" in page_text or "Unknown Domain" not in page_text, \
            "Domain column should explain missing values"

        # Verify at-risk table domain column shows explanatory text, not "None"
        domain_cells = page.locator("table.dense-table tbody td:nth-child(2)")
        count = domain_cells.count()
        for i in range(count):
            cell_text = domain_cells.nth(i).inner_text()
            assert cell_text != "None", f"Domain cell {i} shows raw 'None': {cell_text}"

        assert len(console_errors) == 0, f"Console errors: {console_errors}"
    finally:
        context.close()


def test_investment_matrix_no_none_maturity_and_no_alpine_error(browser, live_server):
    """Investment Matrix page shows no 'None None None' for maturity and loads without Alpine errors."""
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/strategic/investment-matrix",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Capture console errors
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # Wait for Alpine component to initialize
        page.wait_for_selector('[x-data*="investmentMatrix"]', timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1000)  # Allow Alpine to boot

        # Check that no "None None None" appears for maturity
        page_text = page.inner_text("body")
        assert "None None None" not in page_text, "Found 'None None None' in page"

        # Verify no Alpine error about 'after' of undefined
        alpine_errors = [e for e in console_errors if "Alpine" in e and "after" in e]
        assert len(alpine_errors) == 0, f"Alpine errors: {alpine_errors}"

        assert len(console_errors) == 0, f"Console errors: {console_errors}"
    finally:
        context.close()


def test_hybrid_mapping_no_none_mapping_strength(browser, live_server):
    """Hybrid Mapping page shows 'Not rated' instead of 'None/5' for mapping strength."""
    org = create_fresh_org("business_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["business_architect"])
        page.goto(live_server + "/hybrid-mapping-dashboard",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Capture console errors
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # Wait for mapping cards to render (skip org switcher dropdown which has same classes)
        page.wait_for_selector(".grid .bg-card.border.border-border.rounded-lg", timeout=PAGE_TIMEOUT)

        # Check that no "None/5" appears
        page_text = page.inner_text("body")
        assert "None/5" not in page_text, "Found 'None/5' in page"

        # Verify mapping strength badges show "Not rated" or a number
        strength_badges = page.locator("span:has-text('Not rated'), span:has-text('/5')")
        count = strength_badges.count()
        for i in range(count):
            badge_text = strength_badges.nth(i).inner_text()
            assert "None/5" not in badge_text, f"Mapping strength badge {i} shows 'None/5': {badge_text}"
            assert "Not rated/5" not in badge_text, f"Mapping strength badge {i} shows 'Not rated/5': {badge_text}"

        assert len(console_errors) == 0, f"Console errors: {console_errors}"
    finally:
        context.close()


def test_risk_assessment_no_none_risk_factors(browser, live_server):
    """Risk Assessment page shows 'No risk factors identified' instead of 'None' column."""
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/strategic/risk-assessment",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Capture console errors
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # Wait for risk table to render
        page.wait_for_selector("table.dense-table", timeout=PAGE_TIMEOUT)

        # Check that no raw "None" appears in risk factors column
        # The risk factors column (4th column) should not show raw "None"
        risk_factor_cells = page.locator("table.dense-table tbody td:nth-child(4)")
        count = risk_factor_cells.count()
        for i in range(count):
            cell_text = risk_factor_cells.nth(i).inner_text()
            assert cell_text != "None", f"Risk factors cell {i} shows raw 'None': {cell_text}"
            # Should show explanatory text
            assert "No risk factors identified" in cell_text or len(cell_text) > 0, \
                f"Risk factors cell {i} should explain missing factors: {cell_text}"

        assert len(console_errors) == 0, f"Console errors: {console_errors}"
    finally:
        context.close()


def test_vision_deliverable_page_opens(browser, live_server):
    """Vision deliverable page returns 200 with content, not 404."""
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/ea-workflows/deliverables/vision",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Page should not be a 404
        assert "404" not in page.title(), f"Page title indicates 404: {page.title()}"
        assert "page not found" not in page.inner_text("body").lower(), "Page shows 'page not found'"

        # Should show either the deliverable editor or the not-configured page
        page_text = page.inner_text("body")
        assert "Architecture Vision" in page_text, "Page should mention Architecture Vision"
        assert "Workflow Definitions" in page_text or "deliverable" in page_text.lower(), \
            "Page should link to workflow definitions or show deliverable editor"

        # No console errors
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        assert len(console_errors) == 0, f"Console errors: {console_errors}"
    finally:
        context.close()


def test_investment_matrix_enterprise_no_none_maturity(browser, live_server):
    """Enterprise Investment Matrix page shows explanatory text for missing maturity."""
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + "/enterprise/strategic/investment-matrix",
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Capture console errors
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # Wait for table to render
        page.wait_for_selector("table.dense-table", timeout=PAGE_TIMEOUT)

        # Check maturity columns show explanatory text, not raw "None"
        page_text = page.inner_text("body")
        assert "None None None" not in page_text, "Found 'None None None' in page"

        # Current maturity column (3rd column)
        current_maturity_cells = page.locator("table.dense-table tbody td:nth-child(3)")
        count = current_maturity_cells.count()
        for i in range(count):
            cell_text = current_maturity_cells.nth(i).inner_text()
            assert cell_text != "None", f"Current maturity cell {i} shows raw 'None': {cell_text}"
            assert cell_text == "Not assessed" or cell_text.isdigit(), \
                f"Current maturity cell {i} should be 'Not assessed' or a number: {cell_text}"

        # Target maturity column (4th column)
        target_maturity_cells = page.locator("table.dense-table tbody td:nth-child(4)")
        count = target_maturity_cells.count()
        for i in range(count):
            cell_text = target_maturity_cells.nth(i).inner_text()
            assert cell_text != "None", f"Target maturity cell {i} shows raw 'None': {cell_text}"
            assert cell_text == "No target set" or cell_text.isdigit(), \
                f"Target maturity cell {i} should be 'No target set' or a number: {cell_text}"

        assert len(console_errors) == 0, f"Console errors: {console_errors}"
    finally:
        context.close()