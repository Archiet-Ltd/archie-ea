"""A portfolio manager activates a formula version for the rationalization
score, and a second organisation never sees it.

From the Formula Register (/admin/formula-register) the portfolio manager of
a brand-new organisation sees "no version registered yet" for the
rationalization score, activates a version with every required weight
(summing to 1.0, as the scorer requires -- a partial or unnormalised
submission is refused, not silently activated), and after a reload sees
that version named with its weights and in the version history. A second
new organisation's portfolio manager still sees "no version registered
yet" -- activating a formula in one organisation never leaks into
another's.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGE = "/admin/formula-register/"


def _new_page(browser):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("try { localStorage.setItem('archie_onboarding_ts', Date.now().toString()); } catch (e) {}")
    return context, context.new_page()


def test_portfolio_manager_activates_a_formula_version_and_it_stays_scoped(browser, live_server):
    first = create_fresh_org("portfolio_manager")
    second = create_fresh_org("portfolio_manager")

    context, page = _new_page(browser)
    sign_in(page, live_server, first["emails"]["portfolio_manager"])

    page.goto(live_server + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("formula-no-version")).to_be_visible(timeout=PAGE_TIMEOUT)

    # Input names are fixed to the formula's known dimensions (readonly);
    # only the weight fields are editable. Every dimension needs a weight
    # and they must sum to 1.0, or the server refuses to activate it.
    form = page.get_by_test_id("formula-new-version-form").first
    weight_inputs = form.get_by_test_id("formula-input-weight")
    weight_inputs.nth(0).fill("0.6")
    weight_inputs.nth(1).fill("0.2")
    weight_inputs.nth(2).fill("0.1")
    weight_inputs.nth(3).fill("0.1")
    form.get_by_test_id("formula-activate-button").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)

    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("formula-active-version").first).to_contain_text("v1")
    expect(page.get_by_test_id("formula-weight-row").first).to_contain_text("technical_health")
    expect(page.get_by_test_id("formula-weight-row").first).to_contain_text("0.6")
    context.close()

    # A second organisation's portfolio manager never sees the first
    # organisation's activated version.
    context, page = _new_page(browser)
    sign_in(page, live_server, second["emails"]["portfolio_manager"])
    page.goto(live_server + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("formula-no-version")).to_be_visible(timeout=PAGE_TIMEOUT)
    context.close()
