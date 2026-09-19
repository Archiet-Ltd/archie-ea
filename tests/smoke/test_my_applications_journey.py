"""The application manager's screens and the portfolio banner agree, in a real browser.

The application manager owns one seeded application. Every number on the My
Applications dashboard, the list and the health page must describe that one
application, a page past the end of the list must not claim the user owns
nothing, and the portfolio's owner count must say what it counts.
"""

import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    pg = ctx.new_page()
    yield pg
    ctx.close()


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


def _text(page):
    return " ".join(page.inner_text("main").split())


def _visit(page, base, path):
    response = page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response is not None and response.status == 200, "%s answered %s" % (path, response and response.status)
    page.wait_for_timeout(500)
    return _text(page)


def _tiles(text, first_label):
    match = re.search(
        first_label + r" (\d+) Healthy (\d+) At Risk (\d+) Critical (\d+) Not assessed (\d+)", text
    )
    assert match, "summary tiles not found in: %s" % text[:300]
    return [int(n) for n in match.groups()]


def test_application_manager_numbers_agree(page, live_server, seeded):
    _login(page, live_server, seeded["emails"]["application_manager"])

    dashboard = _visit(page, live_server, "/my-applications/")
    total, healthy, at_risk, critical, not_assessed = _tiles(dashboard, "Total Apps")
    assert total >= 1
    assert healthy + at_risk + critical + not_assessed == total
    assert page.locator("a[href*='/my-applications/app/']").count() == total
    assert "No applications assigned yet" not in dashboard

    listing = _visit(page, live_server, "/my-applications/list")
    assert "All (%d)" % total in listing
    assert page.get_by_role("link", name="View details").count() == total

    # A page beyond the last serves the last page instead of an empty list.
    past_the_end = _visit(page, live_server, "/my-applications/list?page=9")
    assert page.get_by_role("link", name="View details").count() == total
    assert "assigned to you yet" not in past_the_end

    health = _visit(page, live_server, "/my-applications/health")
    health_total, healthy, at_risk, critical, not_assessed = _tiles(health, "Total")
    assert health_total == total
    assert healthy + at_risk + critical + not_assessed == total


def test_portfolio_banner_says_what_it_counts(page, live_server, seeded):
    _login(page, live_server, seeded["emails"]["portfolio_manager"])
    applications = _visit(page, live_server, "/applications/")
    match = re.search(
        r"(\d+) of (\d+) applications in the portfolio have a named owner "
        r"\(a business owner on record, or an application manager assigned\)",
        applications,
    )
    assert match, "portfolio page has no owner line"
    # The application manager fixture is assigned one application.
    assert int(match.group(1)) >= 1
