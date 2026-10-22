"""A CTO trusts that one figure reads the same on every screen that shows it.

Data: a linked portfolio (six applications, two without an owner, four risks,
annual costs that add up to 299,000, one solution so the health score is
measured) and a second organisation with look-alike records and larger costs.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import sign_in
from .linked_portfolio import linked_world  # noqa: F401  (the one shared fixture)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _signed_in(browser, live_server, world):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, world["org"]["emails"]["cto"])
    return context, page


def _go(page, base, path):
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 200, "%s answered %s" % (path, response.status)


def _number(text):
    return int(re.sub(r"[^\d]", "", text))


def test_application_count_reads_the_same_on_every_screen(browser, live_server, linked_world):
    expected = len(linked_world["mine"]["apps"])
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _go(page, live_server, "/dashboard/overview")
        page.get_by_test_id("layer-tab-overview").click()
        portfolio_line = re.search(
            r"(\d[\d,]*) in portfolio", page.get_by_test_id("lifecycle-distribution").inner_text(), re.I)
        seen = {
            "overview card": _number(page.get_by_test_id("health-kpi-applications").inner_text()),
            "overview portfolio line": _number(portfolio_line.group(1)),
        }
        page.get_by_test_id("layer-tab-cto").click()
        seen["CTO card"] = _number(page.get_by_test_id("cto-kpi-applications").inner_text())
        # The card is a link: it opens the list that holds exactly those rows.
        page.get_by_test_id("cto-kpi-applications").click()
        page.wait_for_url(re.compile(r"/applications/?$"), timeout=PAGE_TIMEOUT)
        seen["list rows"] = page.locator("[data-testid^='app-row-']").count()
        badge = re.match(r"(\d+) shown", page.get_by_test_id("total-count-badge").inner_text())
        seen["list badge"] = int(badge.group(1))
        banner = page.get_by_test_id("data-quality-alert").inner_text()
        seen["list banner"] = int(re.search(
            r"of (\d+) applications in the portfolio have a named owner", banner).group(1))
        _go(page, live_server, "/applications/ownership-coverage")
        total = page.locator("p", has_text="Total applications").locator("xpath=following-sibling::p").first
        seen["ownership coverage"] = _number(total.inner_text())
        assert set(seen.values()) == {expected}, "application count disagrees across screens: %r" % seen
    finally:
        context.close()


def test_owned_application_count_agrees_between_the_list_and_the_coverage_page(browser, live_server, linked_world):
    apps = linked_world["mine"]["apps"]
    owned = sum(1 for a in apps.values() if a["owner"])
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _go(page, live_server, "/applications/")
        banner = page.get_by_test_id("data-quality-alert").inner_text()
        on_list = int(re.search(
            r"(\d+) of \d+ applications in the portfolio have a named owner", banner).group(1))
        _go(page, live_server, "/applications/ownership-coverage")
        card = page.locator("p", has_text="With named owner").locator("xpath=following-sibling::p").first
        on_coverage = _number(card.inner_text())
        assert {on_list, on_coverage} == {owned}, (
            "owned applications: list banner %s, coverage page %s, recorded %s" % (on_list, on_coverage, owned))
    finally:
        context.close()


def test_health_score_reads_the_same_everywhere_it_appears(browser, live_server, linked_world):
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _go(page, live_server, "/dashboard/overview")
        score = page.get_by_test_id("health-score-value").inner_text().strip()
        assert re.fullmatch(r"\d+(\.\d+)?", score) and float(score) > 0, "overview score is %r" % score
        page.get_by_test_id("layer-tab-cto").click()
        assert page.get_by_test_id("cto-health-score-value").inner_text().strip() == score
        cto = page.get_by_test_id("lens-cto")
        summary = cto.locator("div").filter(
            has=page.get_by_role("heading", name="Executive Summary", exact=True)).last
        label = summary.get_by_text("Health Score", exact=True)
        expect(label).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(label.locator("..").locator("p").last).to_have_text(score)
        page.get_by_test_id("cto-health-score-value").click()
        page.wait_for_url(re.compile(r"/dashboard/health"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("health-score-value")).to_have_text(score, timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_open_risks_on_the_executive_summary_equal_the_register(browser, live_server, linked_world):
    mine = linked_world["mine"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _go(page, live_server, "/risks/")
        rows = page.evaluate("window.RISK_ROWS")
        assert sorted(r["title"] for r in rows) == sorted(r["title"] for r in mine["risks"].values())
        assert _number(page.locator("#ranked-risks h2").inner_text()) == len(rows)
        open_rows = sum(1 for r in rows if r["status"] == "open")

        _go(page, live_server, "/dashboard/overview")
        page.get_by_test_id("layer-tab-cto").click()
        label = page.get_by_test_id("lens-cto").get_by_text("Open Risks", exact=True)
        expect(label).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _number(label.locator("..").locator("p").last.inner_text()) == open_rows
    finally:
        context.close()


@pytest.mark.xfail(strict=True, reason=(
    "The CTO view risk-by-impact table reads zero at every level while the executive summary and the "
    "register both show four open risks."))
def test_risk_by_impact_on_the_cto_view_equals_the_register_levels(browser, live_server, linked_world):
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _go(page, live_server, "/risks/")
        rows = page.evaluate("window.RISK_ROWS")
        by_level = {}
        for row in rows:
            if row["status"] == "open":
                by_level[row["risk_level"]] = by_level.get(row["risk_level"], 0) + 1

        _go(page, live_server, "/dashboard/overview")
        page.get_by_test_id("layer-tab-cto").click()
        table = page.get_by_test_id("cto-risk-by-impact")
        for level in ("critical", "high", "medium", "low"):
            cell = table.locator("tr", has_text=re.compile("^%s" % level, re.I)).locator("td").nth(1)
            shown = cell.inner_text().strip()
            assert shown == str(by_level.get(level, 0)), (
                "%s risks: summary says %r, register rows say %s" % (level, shown, by_level.get(level, 0)))
    finally:
        context.close()


@pytest.mark.xfail(strict=True, reason=(
    "The fact sheet of an application with a recorded business owner lists Owner among the fields still "
    "missing, while the list and the coverage page count that application as owned."))
def test_an_owned_application_is_not_reported_as_missing_its_owner(browser, live_server, linked_world):
    billing = linked_world["mine"]["apps"]["billing"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _go(page, live_server, "/applications/%d/fact-sheet" % billing["id"])
        main = page.locator("#main-content, main").first
        expect(main).to_contain_text(billing["owner"], timeout=PAGE_TIMEOUT)
        missing = main.get_by_text("Missing to reach a complete record").locator("xpath=..")
        assert not re.search(r"\bOwner\b", missing.inner_text()), missing.inner_text()
    finally:
        context.close()


def test_each_fact_sheet_states_the_cost_recorded_for_its_application(browser, live_server, linked_world):
    apps = linked_world["mine"]["apps"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        for application in apps.values():
            _go(page, live_server, "/applications/%d/fact-sheet" % application["id"])
            card = page.get_by_text("Total cost of ownership", exact=True).locator(
                "xpath=ancestor::*[self::div][2]")
            expect(card.first).to_contain_text(application["cost_text"], timeout=PAGE_TIMEOUT)
            # The other organisation look-alike never leaks its larger cost in.
            expect(page.locator("body")).not_to_contain_text("999,999")
    finally:
        context.close()


@pytest.mark.xfail(strict=True, reason=(
    "The CFO view says no cost of ownership is recorded, although every application has an annual cost "
    "on its fact sheet."))
def test_total_cost_on_the_cfo_view_is_the_sum_of_the_application_costs(browser, live_server, linked_world):
    total = sum(a["cost"] for a in linked_world["mine"]["apps"].values())
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _go(page, live_server, "/dashboard/overview")
        page.get_by_role("button", name="CFO", exact=True).click()
        section = page.get_by_test_id("lens-cfo")
        expect(section).to_contain_text("{:,.0f}".format(total), timeout=PAGE_TIMEOUT)
    finally:
        context.close()


@pytest.mark.xfail(strict=True, reason=(
    "The CFO view shows zero vendors while the application list says every application has a vendor "
    "recorded."))
def test_vendor_count_on_the_cfo_view_equals_the_vendors_recorded_on_the_list(browser, live_server, linked_world):
    apps = linked_world["mine"]["apps"]
    with_vendor = sum(1 for a in apps.values() if a["vendor"])
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        _go(page, live_server, "/applications/")
        banner = page.get_by_test_id("data-quality-alert").inner_text()
        on_list = int(re.search(r"(\d+) of \d+ applications in the portfolio have a vendor recorded", banner).group(1))
        assert on_list == with_vendor
        _go(page, live_server, "/dashboard/overview")
        page.get_by_role("button", name="CFO", exact=True).click()
        shown = _number(page.get_by_test_id("cfo-kpi-vendors").inner_text())
        assert shown > 0 and shown <= with_vendor, "CFO view counts %s vendors, the list counts %s" % (shown, with_vendor)
    finally:
        context.close()
