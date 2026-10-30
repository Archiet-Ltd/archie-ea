"""My Applications dashboard: an application owner sees the figures the other screens agree with.

An application owner opens My Applications. The application count must equal the Applications
list filtered to their ownership (and leave out applications someone else owns), the health figures
must equal the Health page's for the same applications, every destination the old navigation row
reached must still be reachable, and the figures sit in the library's stat row: at most four cards,
no icons, no coloured borders.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org
from .overview_data import purge_org
from .test_try_sample_company_journey import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# (name, health status recorded on it)
OWNED = [("Ledger Core", "healthy"), ("Payments Hub", "healthy"), ("Old Billing", "at_risk"),
         ("Claims Portal", "critical"), ("Intake Form", None)]
NOT_OWNED = ["Someone Else One", "Someone Else Two"]


def _seed(owned, not_owned):
    info = create_fresh_org("application_manager", extra_roles=("cto",))
    if owned or not_owned:
        from app import create_app, db
        from app.models.application_owner import ApplicationOwner
        from app.models.application_portfolio import ApplicationComponent

        app = create_app("testing")
        with app.app_context():
            for name, health in owned:
                row = ApplicationComponent(name="%s %s" % (name, info["suffix"]), organization_id=info["org_id"],
                                           lifecycle_status="operational", health_status=health)
                db.session.add(row)
                db.session.flush()
                db.session.add(ApplicationOwner(user_id=info["user_ids"]["application_manager"], application_id=row.id,
                                                organization_id=info["org_id"], ownership_type="primary"))
            for name in not_owned:
                db.session.add(ApplicationComponent(name="%s %s" % (name, info["suffix"]),
                                                    organization_id=info["org_id"], lifecycle_status="operational"))
            db.session.commit()
            db.session.remove()
    return info


@pytest.fixture(scope="module")
def org(live_server):
    info = _seed(OWNED, NOT_OWNED)
    yield info
    purge_org(info["org_id"])


@pytest.fixture(scope="module")
def empty_org(live_server):
    info = _seed([], [])
    yield info
    purge_org(info["org_id"])


def _number(page, testid):
    return int(page.get_by_test_id(testid).inner_text().strip())


def _open(page, live_server, email, path="/my-applications/"):
    _login(page, live_server, email)
    response = page.goto(live_server + path, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert response.status == 200
    page.wait_for_load_state("networkidle")


def test_application_count_equals_the_owners_list(live_server, org, page):
    _open(page, live_server, org["emails"]["application_manager"])
    total = _number(page, "my-apps-total-value")
    assert total == len(OWNED), "the two applications nobody here owns must not be counted"

    page.goto(live_server + "/my-applications/list", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    assert page.get_by_role("link", name="View details").count() == total
    assert "All (%d)" % total in " ".join(page.inner_text("main").split())

    # The dashboard's own list shows every one of those applications, none of the others.
    page.goto(live_server + "/my-applications/", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    rows = page.get_by_test_id("my-apps-row")
    assert rows.count() == total
    body = page.inner_text("main")
    assert "Someone Else" not in body


def test_health_figures_equal_the_health_page(live_server, org, page):
    _open(page, live_server, org["emails"]["application_manager"])
    # Three states are stat cards; applications with no status recorded are counted in the chart's table.
    shown = [_number(page, "my-apps-%s-value" % key) for key in ("healthy", "at-risk", "critical")]
    shown.append(_number(page, "my-apps-health-count-not-assessed"))
    assert shown == [2, 1, 1, 1]
    assert sum(shown) == _number(page, "my-apps-total-value")
    # The chart's figures table says the same as the cards.
    table = [_number(page, "my-apps-health-count-%s" % key) for key in ("healthy", "at-risk", "critical", "not-assessed")]
    assert table == shown

    page.goto(live_server + "/my-applications/health", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    text = " ".join(page.inner_text("main").split())
    match = re.search(r"Total (\d+) Healthy (\d+) At Risk (\d+) Critical (\d+) Not assessed (\d+)", text)
    assert match, "the health page's tiles were not found in: %s" % text[:300]
    assert [int(n) for n in match.groups()] == [len(OWNED)] + shown


def test_figures_are_stat_cards_without_icons_or_coloured_borders(live_server, org, page):
    _open(page, live_server, org["emails"]["application_manager"])
    stats = page.get_by_test_id("my-apps-stats")
    cards = stats.locator("[data-slot='card']")
    assert 1 <= cards.count() <= 4, "a stat row holds at most four cards"
    assert stats.locator("svg").count() == 0, "stat cards carry no icons"
    assert page.evaluate(
        """() => [...document.querySelectorAll('[data-testid=my-apps-stats] [data-slot=card]')]
                 .every(c => !/border-(success|warning|destructive)/.test(c.className))"""
    )


def test_every_destination_of_the_old_navigation_row_is_still_reachable(live_server, org, page):
    expectations = [
        ("Health overview", "/my-applications/health", "Application Health Overview"),
        ("Roadmap impact", "/my-applications/roadmap", None),
        ("All applications", "/applications", None),
    ]
    _open(page, live_server, org["emails"]["application_manager"])
    for label, fragment, heading in expectations:
        link = page.get_by_role("main").get_by_role("link", name=re.compile(label, re.I)).first
        expect(link).to_be_visible()
        href = link.get_attribute("href")
        assert fragment in href, "%s leads to %s" % (label, href)
        response = page.goto(live_server + href, wait_until="networkidle", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        assert len(page.inner_text("main").strip()) > 80, "%s opened an empty page" % label
        if heading:
            expect(page.get_by_role("heading", name=heading).first).to_be_visible()
        page.goto(live_server + "/my-applications/", wait_until="networkidle", timeout=PAGE_TIMEOUT)


def test_an_owner_of_nothing_is_told_why_and_what_to_do(live_server, empty_org, page):
    _open(page, live_server, empty_org["emails"]["application_manager"])
    expect(page.get_by_test_id("my-applications-empty-title")).to_have_text("No applications assigned yet")
    expect(page.get_by_test_id("my-applications-empty-next-step")).to_contain_text("add yourself as an owner")
    assert _number(page, "my-apps-total-value") == 0
