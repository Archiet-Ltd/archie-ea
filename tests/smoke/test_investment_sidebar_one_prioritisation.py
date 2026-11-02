"""An enterprise architect follows the sidebar's Investments link and sees the one prioritisation.

Goal: "I open Investments from the sidebar and see which capabilities to fund first,
with the same figures wherever I reach them, and a capability whose cost nobody has
estimated is never shown as costing nothing."
"""
import pytest
from playwright.sync_api import expect

import uuid

from tests.smoke.fresh_org import create_fresh_org, sign_in
from tests.smoke.test_investment_matrix_scores_journey import (  # noqa: F401
    PAGE_TIMEOUT, _cells, _row, _seed_org, two_orgs,
)

MATRIX = "/strategic/investment-matrix"
OTHER_VIEW = "/architecture/investment-priorities"
OLD_ADDRESS = "/enterprise/strategic/investment-matrix"


@pytest.fixture(scope="module")
def cto_org(live_server):
    """A chief technology officer, who has the Spend group in the sidebar, in a linked organisation."""
    from app import create_app

    mine = create_fresh_org("cto")
    app = create_app("testing")
    with app.app_context():
        names = _seed_org(app, mine["org_id"], "c" + uuid.uuid4().hex[:6], costed=True)
    return {"mine": mine, "names": names}


def _open(browser, live_server, orgs, width=1440, height=900, role="enterprise_architect"):
    page = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": height}).new_page()
    sign_in(page, live_server, orgs["mine"]["emails"][role])
    return page


@pytest.mark.smoke
@pytest.mark.journey
def test_sidebar_investments_lands_on_the_matrix_with_the_same_figures(browser, live_server, cto_org):
    names = cto_org["names"]
    page = _open(browser, live_server, cto_org, role="cto")
    page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    sidebar = page.get_by_test_id("sidebar")
    sidebar.get_by_role("button", name="Spend", exact=True).click()
    link = sidebar.get_by_role("link", name="Investments", exact=True)
    expect(link).to_be_visible()
    link.click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url.split("?")[0].endswith(MATRIX), page.url
    expect(_row(page, names["top"])).to_have_count(1)
    from_sidebar = {k: _cells(page, names[k]) for k in ("top", "child", "plain", "unrated")}
    assert "120.0k" in from_sidebar["top"]["investment"]
    assert "Not estimated" in from_sidebar["plain"]["investment"]

    page.goto(live_server + OTHER_VIEW, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert from_sidebar == {k: _cells(page, names[k]) for k in ("top", "child", "plain", "unrated")}


@pytest.mark.smoke
@pytest.mark.journey
def test_old_address_redirects_to_the_matrix(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    response = page.goto(live_server + OLD_ADDRESS, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.url.split("?")[0].endswith(MATRIX), page.url
    assert response.request.redirected_from is not None
    expect(_row(page, names["top"])).to_have_count(1)


@pytest.mark.smoke
@pytest.mark.journey
def test_roi_on_an_unestimated_capability_shows_no_invented_zero(browser, live_server, two_orgs):
    names = two_orgs["names"]
    page = _open(browser, live_server, two_orgs)
    page.goto(live_server + MATRIX, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = _row(page, names["plain"])
    expect(row).to_have_count(1)
    row.get_by_role("button", name="Calculate ROI").click()
    dialog = page.get_by_role("dialog", name="ROI Calculator")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_test_id("roi-not-estimated")).to_be_visible()
    expect(dialog).to_contain_text("not estimated yet")
    field = dialog.get_by_label("Enter investment amount")
    assert field.input_value() == ""
    assert dialog.get_by_label("Expected annual benefit").input_value() == ""
    # Giving a figure is the way forward, and then the return is worked out from it.
    field.fill("100000")
    dialog.get_by_label("Expected annual benefit").fill("50000")
    expect(dialog.get_by_test_id("roi-not-estimated")).to_be_hidden()
    expect(dialog.get_by_text("Payback Period")).to_be_visible()

    # A costed capability opens with its own figure.
    dialog.get_by_role("button", name="Close").last.click()
    _row(page, names["top"]).get_by_role("button", name="Calculate ROI").click()
    assert dialog.get_by_label("Enter investment amount").input_value() == "120000"
    expect(dialog.get_by_test_id("roi-not-estimated")).to_be_hidden()
