"""The Overview wording that follows sample removal: held until the wording is ruled.

After the sample is removed, the Overview says its counts come from the change
history and names them "Additions logged", with "records added" nowhere on the
page. The protected Overview template does not carry that wording yet, so this
journey is skipped until the wording ruling is made and the template follows.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_try_sample_company_journey import (
    _empty_organisation_user,
    _load_sample_from_ask,
    _login,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.mark.skip(reason="Pending the ruling on the Overview 'Additions logged' wording; the protected Overview template is unchanged")
def test_overview_names_additions_after_sample_removal(browser, live_server, seeded):
    email, _org_id = _empty_organisation_user(live_server)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
        _login(page, live_server, email)
        _load_sample_from_ask(page, live_server)
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Remove it", exact=True).first.click()
        page.locator('[data-testid="sample-company-remove-confirm"]').click()
        expect(page.get_by_text("Sample company removed")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.reload(wait_until="domcontentloaded")

        chart = page.get_by_test_id("activity-chart")
        expect(chart).to_contain_text("Counted from the change history")
        expect(chart).to_contain_text("Additions logged")
        expect(chart).not_to_contain_text("Records added")
        assert "records added" not in page.content().lower()
        expect(page.get_by_test_id("year-card")).to_contain_text("Additions logged in the change history")
    finally:
        context.close()
