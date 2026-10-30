"""An administrator sets the organisation to German and euros, and every converted screen follows.

The goal: a company in Berlin reads 25.10.2026 and 1.234,56 EUR-style amounts where the same company
in London read 25 Oct 2026 and pounds. The journey signs up, loads the sample company, reads the
Overview, the Systems list, the billing page and the weekly briefing in en-GB, sets the organisation
to de-DE and EUR on the regional settings page, and reads them again.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

NB = " "
ENGLISH_DATE = re.compile(r"\b\d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")


def _open(page, base, path):
    page.goto(base.rstrip("/") + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return page.locator("#main-content")


def _seed_estate(org_id):
    """Linked data: applications, and active contracts with a recorded annual cost."""
    import datetime as dt

    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.application_portfolio import ApplicationComponent, VendorContract

        for number in range(9):
            db.session.add(ApplicationComponent(
                name="Locale system %d" % number, organization_id=org_id, lifecycle_status="operational",
                description="A system the locale journey lists."))
        for number, cost in enumerate((1_234_560.0, 480_000.0, 135_000.0)):
            db.session.add(VendorContract(
                organization_id=org_id, contract_name="Locale contract %d" % number,
                contract_number="LOC-%d-%d" % (org_id, number), status="active", annual_cost=cost,
                contract_value=cost, start_date=dt.date.today() - dt.timedelta(days=400)))
        db.session.commit()
        db.session.remove()


def test_an_administrator_sets_german_and_euros_and_the_screens_follow(browser, live_server):
    base = str(live_server)
    org = create_fresh_org("cto", org_admin=True)
    _seed_estate(org["org_id"])
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, base, org["emails"]["cto"])

        # London: pounds, English dates.
        _open(page, base, "/dashboard/overview")
        british = page.get_by_test_id("card-run-cost-value").inner_text().strip()
        british_exact = page.get_by_test_id("monthly-cost-value").get_attribute("title")
        assert british == "\u00a31.8m", british
        assert re.fullmatch(r"\u00a3\d{1,3}(,\d{3})*", british_exact), british_exact
        _open(page, base, "/solutions/briefings")
        assert ENGLISH_DATE.search(page.get_by_test_id("briefing-page").inner_text())
        _open(page, base, "/applications/")
        assert "9 shown" in page.get_by_test_id("total-count-badge").inner_text()

        # The administrator changes the organisation to de-DE and EUR on the settings page.
        _open(page, base, "/admin/")
        page.get_by_test_id("admin-regional-tile").click()
        page.wait_for_url("**/admin/regional", timeout=PAGE_TIMEOUT)
        page.select_option("#locale", "de-DE")
        page.select_option("#currency", "EUR")
        page.get_by_role("button", name="Save").click()
        page.wait_for_url("**/admin/regional", timeout=PAGE_TIMEOUT)
        example = page.get_by_test_id("regional-example").inner_text()
        assert re.search(r"\d{2}\.\d{2}\.\d{4}", example) and "1.234,56" + NB + "\u20ac" in example, example

        # Berlin: euros with German separators, German dates, no pounds, no English month.
        _open(page, base, "/dashboard/overview")
        german = page.get_by_test_id("card-run-cost-value").inner_text().strip()
        german_exact = page.get_by_test_id("monthly-cost-value").get_attribute("title")
        assert german == "1,8" + NB + "Mio." + NB + "\u20ac", german
        assert re.fullmatch(r"\d{1,3}(\.\d{3})*" + NB + "\u20ac", german_exact), german_exact
        assert re.fullmatch(r"\d{1,3}(\.\d{3})*" + NB + "\u20ac", page.get_by_test_id("monthly-cost-value").inner_text().strip())
        assert "\u00a3" not in page.get_by_test_id("overview-dashboard").inner_text()

        _open(page, base, "/solutions/briefings")
        text = page.get_by_test_id("briefing-page").inner_text()
        assert re.search(r"\d{2}\.\d{2}\.\d{4}", text), text
        assert not ENGLISH_DATE.search(text), text

        _open(page, base, "/applications/")
        assert "\u00a3" not in page.locator("body").inner_text()
        assert page.locator("table tbody tr").count() == 9
    finally:
        context.close()
