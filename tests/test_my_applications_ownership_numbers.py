"""My Applications and the portfolio banner agree on what a user owns.

The application manager's dashboard used to print "Total Apps 1" beside Healthy 0,
At Risk 0, Critical 0 and an empty "No applications assigned yet" panel; the list
page read "1 total" with tabs "All (1)" and "Primary (2)"; and the portfolio
manager's Applications page read "0 of 6 applications ... have an assigned owner"
while one application was assigned. Six numbers, three answers.

This file asks the screens rather than the code. It seeds one tenant, signs in as
the application manager over HTTP, reads all six numbers off the rendered pages
(Total Apps, the health tiles, the panel rows, "All (n)", "Primary (m)" and the
other tabs, the portfolio banner) and asserts they are consistent with each other
and with the ownership rows in the database.

Uses the shared fixtures in tests/conftest.py (db_session rolls everything back).
"""

from __future__ import annotations

import re
import uuid

import pytest
from bs4 import BeautifulSoup

pytestmark = pytest.mark.usefixtures("db_session")

TABS = ("All", "Primary", "Backup", "Technical", "Business")


def _make_user(db_session, org, role, label):
    from app.models.user import User

    user = User(
        email=f"own-numbers-{label}-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Own",
        last_name=label,
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _make_app(db_session, org, name, lifecycle_status, **extra):
    from app.models.application_portfolio import ApplicationComponent

    component = ApplicationComponent(
        name=f"{name} {uuid.uuid4().hex[:6]}",
        organization_id=org.id,
        lifecycle_status=lifecycle_status,
        **extra,
    )
    db_session.add(component)
    db_session.flush()
    return component


def _own(db_session, org, user, application, ownership_type):
    from app.models.application_owner import ApplicationOwner

    db_session.add(
        ApplicationOwner(
            application_id=application.id,
            user_id=user.id,
            organization_id=org.id,
            ownership_type=ownership_type,
        )
    )
    db_session.flush()


def _get(client, login_as, user, path):
    login_as(client, user)
    return client.get(path)


def _soup(response):
    assert response.status_code == 200, response.get_data(as_text=True)[:1500]
    return BeautifulSoup(response.get_data(as_text=True), "html.parser")


def _tile(soup, label):
    """The number printed in the dashboard summary tile with this label."""
    label_div = soup.find("div", string=lambda s: s and s.strip() == label)
    assert label_div is not None, f"dashboard has no {label!r} tile"
    tile = label_div.find_parent("div", class_="rounded-lg")
    return int(tile.find("div", class_="text-3xl").get_text(strip=True))


def _panel_rows(soup):
    heading = soup.find("h2", string=lambda s: s and s.strip() == "My Applications")
    panel = heading.find_parent("div", class_="rounded-lg")
    return [a for a in panel.find_all("a") if "/my-applications/app/" in a.get("href", "")]


def _list_rows(soup):
    return [a for a in soup.find_all("a") if a.get_text(strip=True) == "View details"]


def _tabs(soup):
    counts = {}
    for a in soup.find_all("a"):
        m = re.fullmatch(r"(All|Primary|Backup|Technical|Business) \((\d+)\)", " ".join(a.get_text().split()))
        if m:
            counts[m.group(1)] = int(m.group(2))
    assert set(counts) == set(TABS), f"list page tabs: {counts}"
    return counts


def _portfolio_banner(soup):
    text = " ".join(soup.get_text().split())
    m = re.search(r"(\d+) of (\d+) applications in the portfolio have an assigned owner", text)
    assert m is not None, "portfolio page has no assigned-owner line"
    return int(m.group(1)), int(m.group(2))


@pytest.fixture
def estate(db_session, make_org):
    """One tenant, two application managers, a portfolio manager and six applications.

    The manager under test holds application A twice (primary and technical), B as
    backup and C as business owner: four ownership rows, three applications. D is
    held by the other manager, E by nobody, and F has only a business owner
    recorded on the application itself.
    """
    org = make_org("own-numbers")
    manager = _make_user(db_session, org, "application_manager", "manager")
    other = _make_user(db_session, org, "application_manager", "other")
    portfolio = _make_user(db_session, org, "portfolio_manager", "portfolio")

    a = _make_app(db_session, org, "Payments", "active")
    b = _make_app(db_session, org, "Ledger", "sunset")
    c = _make_app(db_session, org, "Portal", "operational")
    d = _make_app(db_session, org, "Elsewhere", "active")
    e = _make_app(db_session, org, "Orphan", "active")
    f = _make_app(db_session, org, "Text owner", "active", business_owner="Pat Jones")

    _own(db_session, org, manager, a, "primary")
    _own(db_session, org, manager, a, "technical")
    _own(db_session, org, manager, b, "backup")
    _own(db_session, org, manager, c, "business")
    _own(db_session, org, other, d, "primary")

    return {
        "org": org,
        "manager": manager,
        "other": other,
        "portfolio": portfolio,
        "apps": {"a": a, "b": b, "c": c, "d": d, "e": e, "f": f},
    }


def test_six_ownership_numbers_agree(estate, client, login_as):
    """Total Apps, health tiles, panel rows, list tabs and portfolio banner agree.

    Test id recorded in the change: tests/test_my_applications_ownership_numbers.py::
    test_six_ownership_numbers_agree
    """
    manager = estate["manager"]

    # --- the dashboard ---------------------------------------------------------
    dash = _soup(_get(client, login_as, manager, "/my-applications/"))
    total = _tile(dash, "Total Apps")
    healthy = _tile(dash, "Healthy")
    at_risk = _tile(dash, "At Risk")
    critical = _tile(dash, "Critical")
    not_assessed = _tile(dash, "Not assessed")
    panel = _panel_rows(dash)

    # Three distinct applications, however many ownership rows name them.
    assert total == 3
    # Total Apps equals the rows the panel shows.
    assert len(panel) == total
    # The health tiles account for every application.
    assert healthy + at_risk + critical + not_assessed == total
    assert (healthy, at_risk, critical, not_assessed) == (1, 1, 0, 1)

    # --- the list page ---------------------------------------------------------
    listing = _soup(_get(client, login_as, manager, "/my-applications/list"))
    tabs = _tabs(listing)
    rows = _list_rows(listing)

    assert tabs["All"] == total
    assert len(rows) == total
    # A subset never exceeds its set.
    for name in TABS:
        assert tabs[name] <= tabs["All"], f"{name} ({tabs[name]}) exceeds All ({tabs['All']})"
    assert (tabs["Primary"], tabs["Backup"], tabs["Technical"], tabs["Business"]) == (1, 1, 1, 1)

    # Each tab count equals the rows shown when that tab is selected.
    for name in TABS[1:]:
        selected = _soup(_get(client, login_as, manager, f"/my-applications/list?type={name.lower()}"))
        assert len(_list_rows(selected)) == tabs[name], f"{name} tab count differs from its rows"

    # --- the portfolio banner --------------------------------------------------
    portfolio = _soup(_get(client, login_as, estate["portfolio"], "/applications/"))
    assigned, portfolio_total = _portfolio_banner(portfolio)

    from app.models.application_owner import ApplicationOwner

    org_id = estate["org"].id
    with_ownership_row = {
        o.application_id for o in ApplicationOwner.query.filter_by(organization_id=org_id).all()
    }
    with_business_owner = {a.id for a in estate["apps"].values() if a.business_owner}
    assert portfolio_total == len(estate["apps"])
    # Every application either manager owns is counted as having an assigned owner,
    # so the sending side can never say "none" while the receiving side says "1".
    assert assigned == len(with_ownership_row | with_business_owner) == 5
    assert assigned >= total


def test_second_manager_sees_only_their_own_numbers(estate, client, login_as):
    """The count is per user: the other manager owns one application, not four."""
    dash = _soup(_get(client, login_as, estate["other"], "/my-applications/"))
    assert _tile(dash, "Total Apps") == 1
    assert len(_panel_rows(dash)) == 1
    assert _tabs(_soup(_get(client, login_as, estate["other"], "/my-applications/list")))["All"] == 1


def test_empty_panel_names_a_route_and_total_reads_zero(db_session, make_org, client, login_as):
    """No application assigned: the panel says so, offers a way on, and Total Apps is 0."""
    org = make_org("own-numbers-empty")
    manager = _make_user(db_session, org, "application_manager", "empty")
    _make_app(db_session, org, "Someone else's", "active")

    dash = _soup(_get(client, login_as, manager, "/my-applications/"))
    assert _tile(dash, "Total Apps") == 0
    assert _tile(dash, "Not assessed") == 0
    assert _panel_rows(dash) == []

    heading = dash.find("h2", string=lambda s: s and s.strip() == "My Applications")
    panel = heading.find_parent("div", class_="rounded-lg")
    assert "No applications assigned yet" in panel.get_text()
    link = panel.find("a", string=lambda s: s and s.strip() == "Browse all applications")
    assert link is not None, "the empty panel has no 'Browse all applications' link"
    assert link["href"] == "/applications/"

    # The link answers 200 for this manager.
    assert _get(client, login_as, manager, link["href"]).status_code == 200

    assert _tabs(_soup(_get(client, login_as, manager, "/my-applications/list")))["All"] == 0


def test_panel_states_how_many_it_is_showing_when_it_cannot_list_them_all(db_session, make_org, client, login_as):
    """Past the panel's row limit the tile still says how many, and the panel says it is partial."""
    org = make_org("own-numbers-many")
    manager = _make_user(db_session, org, "application_manager", "many")
    for i in range(12):
        _own(db_session, org, manager, _make_app(db_session, org, f"Bulk {i:02d}", "active"), "primary")

    dash = _soup(_get(client, login_as, manager, "/my-applications/"))
    assert _tile(dash, "Total Apps") == 12
    assert len(_panel_rows(dash)) == 10
    assert "Showing 10 of 12" in " ".join(dash.get_text().split())

    listing = _soup(_get(client, login_as, manager, "/my-applications/list"))
    assert _tabs(listing)["All"] == 12
    assert len(_list_rows(listing)) == 12
