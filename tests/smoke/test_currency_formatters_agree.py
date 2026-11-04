"""One amount reads the same through every currency formatter a person can meet.

The goal, as an administrator reading the same price on three screens: "four thousand eight hundred euros is
written one way". The browser manager (currency.js), the ``|currency`` filter and the ``|format_currency`` filter
are fed identical amounts; the browser one runs in a real signed-in page.
"""
import pytest

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

CASES = [
    (4800, "EUR", "€4,800.00"),
    (1200, "GBP", "£1,200.00"),
    (12500, "USD", "$12,500.00"),
    (0, "EUR", "€0.00"),
    (0, "USD", "$0.00"),
    (-1234.5, "EUR", "€-1,234.50"),
    (-1234.5, "GBP", "£-1,234.50"),
]


@pytest.fixture(scope="module")
def signed_in_page(browser, live_server):
    info = create_fresh_org("platform_admin", org_admin=True)
    page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
    sign_in(page, live_server, info["emails"]["platform_admin"])
    page.wait_for_function("() => !!(window.currencyManager && window.currencyManager.format)", timeout=PAGE_TIMEOUT)
    return page


@pytest.mark.parametrize("amount,code,expected", CASES)
def test_all_three_formatters_agree(signed_in_page, app, amount, code, expected):
    with app.test_request_context("/"):
        via_currency = app.jinja_env.filters["currency"](amount, code)
        via_format_currency = app.jinja_env.filters["format_currency"](amount, currency=code)
    via_browser = signed_in_page.evaluate("([a, c]) => window.currencyManager.format(a, c)", [amount, code])
    assert via_currency == via_format_currency == via_browser == expected, (via_currency, via_format_currency, via_browser)
