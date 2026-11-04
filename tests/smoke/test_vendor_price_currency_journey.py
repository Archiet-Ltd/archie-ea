"""An administrator reviewing staged vendor prices sees each price in the currency it was recorded in.

The goal, as the platform administrator: "check what the import found before I confirm it, without a euro
price passing as dollars". The staged page is rendered by the product's own template for a euro row, a pound
row, a real zero and a row with no price, and the browser reads what the table shows.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

ROWS = [
    {"vendor_name": "Acme GmbH", "product_name": "Euro Suite", "tier": "Pro", "annual_cost": 4800, "currency": "EUR"},
    {"vendor_name": "Crown Ltd", "product_name": "Pound Desk", "tier": "Pro", "annual_cost": 1200, "currency": "GBP"},
    {"vendor_name": "Free Co", "product_name": "Free Tier", "tier": "Free", "annual_cost": 0, "currency": "USD"},
    {"vendor_name": "Quiet Inc", "product_name": "Unpriced", "tier": "Std", "annual_cost": None, "currency": "EUR"},
]


def _staged_page_html(app, user_id):
    from flask import render_template
    from flask_login import login_user

    from app.models.user import User

    with app.test_request_context("/admin/vendor-pricing/import"):
        from app import db
        login_user(db.session.get(User, user_id))
        return render_template("admin/vendor_pricing_import.html", staging_items=ROWS, upload_error=None)


def _stage(page, base, app, user_id):
    html = _staged_page_html(app, user_id)
    page.goto(base + "/admin/vendor-pricing/import", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.route("**/admin/vendor-pricing/import", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.evaluate("() => { const f = document.createElement('form'); f.method = 'POST'; f.action = '/admin/vendor-pricing/import'; document.body.appendChild(f); f.submit(); }")
    expect(page.get_by_text("Euro Suite")).to_be_visible(timeout=PAGE_TIMEOUT)


def test_staged_prices_read_in_their_own_currency(browser, live_server, app):
    info = create_fresh_org("platform_admin", org_admin=True)
    page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
    sign_in(page, live_server, info["emails"]["platform_admin"])
    _stage(page, live_server, app, info["user_ids"]["platform_admin"])
    rows = {r: page.locator("tr", has_text=r) for r in ("Euro Suite", "Pound Desk", "Free Tier", "Unpriced")}
    euro = rows["Euro Suite"].inner_text()
    assert "€" in euro or "EUR" in euro, euro
    assert "$" not in euro, "a euro price drawn with a dollar sign: %r" % euro
    assert re.search(r"4[.,]800", euro), euro
    pound = rows["Pound Desk"].inner_text()
    assert "$" not in pound and ("£" in pound or "GBP" in pound), pound
    free = rows["Free Tier"].inner_text()
    assert re.search(r"0\.00|\b0\b", free) and "—" not in free.split("Free")[-1][:12], free
    assert "Not recorded" in rows["Unpriced"].inner_text(), rows["Unpriced"].inner_text()


def _signed_in_gbp_page(browser, live_server):
    info = create_fresh_org("enterprise_architect")
    # The page's content security policy forbids evaluating strings; this test evaluates the template's own expressions.
    page = browser.new_context(viewport={"width": 1440, "height": 900}, bypass_csp=True).new_page()
    sign_in(page, live_server, info["emails"]["enterprise_architect"])
    page.wait_for_function("() => !!(window.currencyManager && window.Platform && window.Platform.figure)", timeout=PAGE_TIMEOUT)
    return page


def test_candidate_totals_read_in_dollars_and_a_missing_figure_says_not_recorded(browser, live_server):
    """The discovery candidate list draws its own figures through the one formatter."""
    import pathlib

    page = _signed_in_gbp_page(browser, live_server)
    source = pathlib.Path("app/static/js/dashboards/vendor_discovery.js").read_text()
    fn = re.search(r"function formatTco\(value\) \{.*?\n\}", source, re.S).group(0)
    page.evaluate("() => { " + fn + "; window.__formatTco = formatTco; }")
    assert "$NaN" not in source and "'$' + " not in source
    assert page.evaluate("() => window.__formatTco(undefined)") == "Not recorded"
    assert page.evaluate("() => window.__formatTco(null)") == "Not recorded"
    assert page.evaluate("() => window.__formatTco(0)") == "$0.00"
    assert page.evaluate("() => window.__formatTco(12500)") == "$12,500.00"
    assert "Total TCO, US dollars" in source


@pytest.mark.parametrize("path,total_expr,components_expr", [
    ("app/templates/solutions/partials/_detail_phase_b.html", "aiSuggestions.costs.total", "aiSuggestions.costs.components"),
    ("app/templates/solutions/partials/_detail_ai_panel.html", "aiInsightsData.costs.suggestion.total", "aiInsightsData.costs.suggestion.components"),
])
def test_phase_b_breakdown_and_total_share_the_organisations_currency(browser, live_server, path, total_expr, components_expr):
    """A pound organisation's total and its components are drawn in pounds, each with no dollar claim."""
    import pathlib

    html = pathlib.Path(path).read_text()
    assert "US dollars" not in html
    page = _signed_in_gbp_page(browser, live_server)
    page.evaluate("() => window.currencyManager.setCurrency('GBP')")
    data = {"total": 1000, "components": {"licences": 200, "support": 800}}
    root = total_expr.split(".")[0]
    total_x = re.search(r'x-text="(%s[^"]*window\.currencyManager\.format\(Number\(%s\)[^"]*)"' % (re.escape(total_expr), re.escape(total_expr)), html)
    assert total_x, "total is not drawn through currencyManager.format"
    comp_x = re.search(r'x-text="(window\.currencyManager\.format\(Number\(v\)[^"]*)"', html)
    assert comp_x, "components are not drawn through currencyManager.format"
    drawn = page.evaluate(
        "([t, c, total, comps]) => { const aiSuggestions = {costs: {total: total, components: comps}};"
        " const aiInsightsData = {costs: {suggestion: {total: total, components: comps}}};"
        " const Platform = window.Platform;"
        " return [eval(t), Object.entries(comps).map(([k, v]) => eval(c))]; }",
        [total_x.group(1), comp_x.group(1), data["total"], data["components"]],
    )
    total_text, comp_texts = drawn
    assert "£" in total_text and "$" not in total_text, total_text
    assert comp_texts and all("£" in t and "$" not in t for t in comp_texts), comp_texts
