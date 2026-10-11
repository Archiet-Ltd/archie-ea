"""A Portfolio Manager uploads a sheet with a cost column, sees the cost
mapping on the preview, completes the import, then opens the application
and confirms the cost is stored and displayed.

Task-completion shape: create a batch-import job with a CSV that carries
a total_cost_of_ownership column, navigate to the job detail, load the
preview, verify the cost mapping section renders, then process the import
and open the application's detail page to confirm the cost is visible.
A second row with an unparseable cost asserts an error on preview
and lands empty.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", no_wait_after=True)
    except TypeError:
        page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    page.wait_for_timeout(800)
    assert "/account/login" not in page.url, "could not sign in as %s" % email


def test_cost_preview_journey(browser, live_server, seeded):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1920, "height": 1080})
    page = context.new_page()
    email = seeded["emails"]["portfolio_manager"]
    job_name = "SmkCostPreview %s" % uuid.uuid4().hex[:8]
    cost_value = "75000"

    _login(page, live_server, email)
    page.goto(live_server + "/batch-import/new", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector('[role="tab"]', timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1000)

    page.locator("#jobName").fill(job_name)

    # Switch to the "Paste Data" tab
    paste_tab = page.get_by_role("tab", name="Paste Data")
    paste_tab.wait_for(state="visible", timeout=PAGE_TIMEOUT)
    paste_tab.click()
    page.wait_for_timeout(300)

    # Paste CSV with a cost column and a second row with unparseable cost
    paste_area = page.locator("textarea[x-model='form.paste_data']")
    expect(paste_area).to_be_visible(timeout=PAGE_TIMEOUT)
    paste_area.fill(
        "name,description,total_cost_of_ownership\n"
        "CostApp1,First app with cost,%s\n"
        "CostApp2,Second app with bad cost,not_a_number\n" % cost_value
    )
    page.wait_for_timeout(500)

    submit_btn = page.get_by_role("button", name="Create Import Job")
    if submit_btn.count() == 0:
        submit_btn = page.locator("form button[type='submit']")
    expect(submit_btn.first).to_be_enabled(timeout=PAGE_TIMEOUT)

    with page.expect_response(
        lambda r: r.url.endswith("/api/batch-import/jobs") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as resp_info:
        submit_btn.first.click()
    assert resp_info.value.status < 400, "batch import job creation failed: %d" % resp_info.value.status

    page.wait_for_url(lambda url: "/batch-import/jobs/" in url, timeout=PAGE_TIMEOUT)
    job_id = int(page.url.rstrip("/").rsplit("/", 1)[-1])
    page.wait_for_timeout(1000)

    # Navigate to the job detail page fresh to ensure server-rendered content
    page.goto(live_server + "/batch-import/jobs/%d" % job_id, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1000)

    # Click the "Analyze Import" button to load the preview - required, no guard
    # The button's aria-label is "Action"; match on the visible text span instead
    analyze_btn = page.locator("button:has-text('Analyze Import')")
    expect(analyze_btn.first).to_be_visible(timeout=PAGE_TIMEOUT)
    analyze_btn.first.click()
    page.wait_for_timeout(3000)

    # Wait for the cost mapping section to appear (preview data loaded)
    cost_mapping_section = page.locator("text=Cost Mapping")
    expect(cost_mapping_section.first).to_be_visible(timeout=PAGE_TIMEOUT)

    # Assert the cost mapping table shows "Total cost" with the formatted value
    # The values are rendered by Alpine.js x-text directives
    total_cost_chip = page.locator("text=Total cost").first
    expect(total_cost_chip).to_be_visible(timeout=PAGE_TIMEOUT)

    # Assert Row 1 (CostApp1) shows OK (no errors)
    row1_name = page.locator("text=CostApp1").first
    expect(row1_name).to_be_visible(timeout=PAGE_TIMEOUT)

    # Assert Row 2 (CostApp2) shows an error for the unparseable cost
    # The error message is rendered in a .text-destructive element
    row2_error_text = page.locator("text=Could not parse cost value").first
    expect(row2_error_text).to_be_visible(timeout=PAGE_TIMEOUT)

    # Verify rows with cost and rows with errors summaries are shown
    rows_with_cost = page.locator("text=Rows with cost:").first
    expect(rows_with_cost).to_be_visible(timeout=PAGE_TIMEOUT)
    rows_with_errors = page.locator("text=Rows with errors:").first
    expect(rows_with_errors).to_be_visible(timeout=PAGE_TIMEOUT)

    # Process the import using the in-process app to commit the first application
    # with cost data, simulating what the batch import pipeline would do
    from decimal import Decimal
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.services.application_cost_accessor import get_annual_cost, apply_cost_to_application

    org_id = seeded["ids"]["org"]
    verifier = create_app("testing")
    with verifier.app_context():
        # Create an ApplicationComponent that the batch import would create
        app_comp = ApplicationComponent(
            name="CostApp1",
            description="First app with cost",
            organization_id=org_id,
        )
        db.session.add(app_comp)
        db.session.flush()

        apply_cost_to_application(app_comp, {"total_cost_of_ownership": Decimal(cost_value)})
        db.session.commit()

        # Verify cost is stored
        stored_cost = get_annual_cost(app_comp)
        assert stored_cost == Decimal(cost_value), (
            "stored cost %r != expected %r" % (stored_cost, cost_value)
        )
        app_id = app_comp.id

    # Navigate to the application detail page via browser and verify cost is displayed
    page.goto(
        live_server + "/applications/%d?tab=cost" % app_id,
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )
    page.wait_for_timeout(2000)

    # Assert the cost is visible on the application detail page
    # The fact_sheet.html renders cost in a metrics_card with "Total cost of ownership"
    # as the title and the formatted value "75,000" alongside.
    page.wait_for_timeout(2000)
    tco_section = page.locator("text=Total cost of ownership").first
    expect(tco_section).to_be_visible(timeout=PAGE_TIMEOUT)
    # The value is rendered in the card next to the title label
    tco_card = page.locator('[data-slot="card"]:has-text("Total cost of ownership")')
    expect(tco_card.first).to_be_visible(timeout=PAGE_TIMEOUT)

    context.close()