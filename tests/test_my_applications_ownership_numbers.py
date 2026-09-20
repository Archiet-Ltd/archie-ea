"""My Applications and the portfolio banner agree on what a user owns.

This file asks the screens rather than the code. It seeds one tenant, signs in as
the application manager over HTTP, reads the numbers off the rendered pages
(Total Apps, the health tiles, the panel rows, "All (n)", "Primary (m)" and the
other tabs, the portfolio banner) and asserts they are consistent with each other
and with the ownership rows in the database. It also covers the list page's
paging and search, the health page's tiles, and tenant isolation of the
portfolio's owner count.

Uses the shared fixtures in tests/conftest.py (db_session rolls everything back).
"""

from __future__ import annotations

import re
import uuid
from urllib.parse import parse_qs, urlparse

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
    m = re.search(r"(\d+) of (\d+) applications in the portfolio have a named owner", text)
    assert m is not None, "portfolio page has no named-owner line"
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
    """Total Apps, health tiles, panel rows, list tabs and portfolio banner agree."""
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
    # Every application either manager owns is counted as having a named owner,
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


def _pager_link(soup, label):
    link = soup.find("a", string=lambda s: s and s.strip() == label)
    return None if link is None else link["href"]


def _own_many(db_session, org, user, count, name, ownership_type="primary", **extra):
    apps = [_make_app(db_session, org, f"{name} {i:02d}", "active", **extra) for i in range(count)]
    for a in apps:
        _own(db_session, org, user, a, ownership_type)
    return apps


def test_a_page_past_the_end_serves_the_last_page(db_session, make_org, client, login_as):
    """/list?page=N beyond the last page shows the last page, never an empty list."""
    org = make_org("own-numbers-paging")
    manager = _make_user(db_session, org, "application_manager", "paging")
    _own_many(db_session, org, manager, 3, "Few")

    listing = _soup(_get(client, login_as, manager, "/my-applications/list?page=2"))
    assert len(_list_rows(listing)) == 3
    text = " ".join(listing.get_text().split())
    assert "(3 total)" in text
    assert "You don't have any applications assigned to you yet" not in text
    assert "No applications found" not in text

    # Odd page values fall back to a real page as well, however large they are:
    # the values around the largest offset PostgreSQL accepts, and far beyond it.
    for value in (
        "0", "-4", "abc", "1.5",
        "461168601842738791", "461168601842738792", str(2**63), str(10**20), str(10**40),
    ):
        page = _soup(_get(client, login_as, manager, f"/my-applications/list?page={value}"))
        assert len(_list_rows(page)) == 3, value
        assert "(3 total)" in " ".join(page.get_text().split()), value


def test_a_page_past_the_end_of_several_pages_serves_the_last_with_a_way_back(db_session, make_org, client, login_as):
    org = make_org("own-numbers-paging-many")
    manager = _make_user(db_session, org, "application_manager", "pagingmany")
    _own_many(db_session, org, manager, 25, "Many")

    last = _soup(_get(client, login_as, manager, "/my-applications/list?page=9"))
    assert len(_list_rows(last)) == 5
    assert "Page 2 of 2" in " ".join(last.get_text().split())
    assert _pager_link(last, "Previous") is not None
    assert _pager_link(last, "Next") is None

    first = _soup(_get(client, login_as, manager, "/my-applications/list"))
    assert len(_list_rows(first)) == 20
    assert "Page 1 of 2" in " ".join(first.get_text().split())
    assert _pager_link(first, "Next") is not None


def test_a_tab_with_no_rows_does_not_say_nothing_is_assigned(db_session, make_org, client, login_as):
    """An empty tab says so for that tab; it never claims the user owns nothing."""
    org = make_org("own-numbers-empty-tab")
    manager = _make_user(db_session, org, "application_manager", "emptytab")
    _own_many(db_session, org, manager, 1, "Solo")

    listing = _soup(_get(client, login_as, manager, "/my-applications/list?type=backup"))
    assert _list_rows(listing) == []
    text = " ".join(listing.get_text().split())
    assert _tabs(listing)["All"] == 1
    assert "You don't hold any applications as backup owner" in text
    assert "assigned to you yet" not in text


def test_the_health_page_tiles_add_up_and_say_what_they_count(db_session, make_org, client, login_as):
    """Healthy, At Risk, Critical and Not assessed sum to Total, and each block says what it counts."""
    org = make_org("own-numbers-health")
    manager = _make_user(db_session, org, "application_manager", "health")
    for name, lifecycle in (("Live", "active"), ("Fading", "sunset"), ("Gone", "retired"), ("Odd", "operational"), ("Blank", None)):
        _own(db_session, org, manager, _make_app(db_session, org, name, lifecycle), "primary")

    page = _soup(_get(client, login_as, manager, "/my-applications/health"))
    total = _tile(page, "Total")
    tiles = [_tile(page, label) for label in ("Healthy", "At Risk", "Critical", "Not assessed")]
    assert total == 5
    assert sum(tiles) == total
    assert tiles == [1, 1, 1, 2]

    text = " ".join(page.get_text().split())
    # The tiles are counted from the lifecycle stage; the grouped list from the
    # health status recorded on each application. The page says both.
    assert "By lifecycle stage" in text
    assert "Healthy is active or production" in text
    assert "By recorded health status" in text
    assert "listed under the health status recorded on it" in text
    assert "Applications with no health status recorded" in text


def test_the_portfolio_banner_says_what_it_counts(db_session, make_org, client, login_as):
    """Named business owners with no assignment rows count, and the wording says why."""
    org = make_org("own-numbers-named")
    portfolio = _make_user(db_session, org, "portfolio_manager", "named")
    manager = _make_user(db_session, org, "application_manager", "namedmanager")
    for i in range(6):
        _make_app(db_session, org, f"Named {i}", "active", business_owner=f"Owner {i}")

    page = _soup(_get(client, login_as, portfolio, "/applications/"))
    assert _portfolio_banner(page) == (6, 6)
    text = " ".join(page.get_text().split())
    assert (
        "6 of 6 applications in the portfolio have a named owner "
        "(a business owner on record, or an application manager assigned)"
    ) in text
    assert "have an assigned owner" not in text

    # The application manager owns none of them, and the wording above is what
    # keeps the two figures from reading as one question.
    dash = _soup(_get(client, login_as, manager, "/my-applications/"))
    assert _tile(dash, "Total Apps") == 0


def test_other_organisations_assignments_do_not_count_as_owned(db_session, make_org, client, login_as):
    """An application's owner count ignores ownership rows that belong to another organisation."""
    org_a = make_org("own-numbers-tenant-a")
    org_b = make_org("own-numbers-tenant-b")
    portfolio = _make_user(db_session, org_a, "portfolio_manager", "tenantportfolio")
    manager_a = _make_user(db_session, org_a, "application_manager", "tenantmanagera")
    stranger = _make_user(db_session, org_b, "application_manager", "tenantstranger")

    genuinely_owned = _make_app(db_session, org_a, "Genuinely owned", "active")
    foreign_row_and_user = _make_app(db_session, org_a, "Foreign row and user", "active")
    foreign_user_only = _make_app(db_session, org_a, "Foreign user only", "active")
    foreign_row_only = _make_app(db_session, org_a, "Foreign row only", "active")
    _make_app(db_session, org_a, "Unowned", "active")

    _own(db_session, org_a, manager_a, genuinely_owned, "primary")
    # Another organisation's user, recorded under that organisation.
    _own(db_session, org_b, stranger, foreign_row_and_user, "primary")
    # Another organisation's user, recorded under this organisation.
    _own(db_session, org_a, stranger, foreign_user_only, "primary")
    # This organisation's user, recorded under another organisation.
    _own(db_session, org_b, manager_a, foreign_row_only, "primary")

    page = _soup(_get(client, login_as, portfolio, "/applications/"))
    assert _portfolio_banner(page) == (1, 5)


def test_pager_links_keep_the_search_and_tab_and_encode_them(db_session, make_org, client, login_as):
    """A search term containing an ampersand survives the pager and the tabs intact."""
    org = make_org("own-numbers-encoding")
    manager = _make_user(db_session, org, "application_manager", "encoding")
    _own_many(db_session, org, manager, 25, "R&D Portal")
    _own_many(db_session, org, manager, 5, "Other")

    page = _soup(_get(client, login_as, manager, "/my-applications/list?search=R%26D&type=primary"))
    assert len(_list_rows(page)) == 20
    query = parse_qs(urlparse(_pager_link(page, "Next")).query)
    assert query == {"page": ["2"], "search": ["R&D"], "type": ["primary"]}

    second = _soup(_get(client, login_as, manager, "/my-applications/list?search=R%26D&type=primary&page=2"))
    query = parse_qs(urlparse(_pager_link(second, "Previous")).query)
    assert query == {"page": ["1"], "search": ["R&D"], "type": ["primary"]}

    for a in page.find_all("a"):
        if re.fullmatch(r"(Primary|Backup|Technical|Business) \(\d+\)", " ".join(a.get_text().split())):
            assert parse_qs(urlparse(a["href"]).query)["search"] == ["R&D"]


def test_tab_counts_and_subtitle_follow_an_active_search(db_session, make_org, client, login_as):
    """With a search active the counts describe the matches, and the subtitle says how many match."""
    org = make_org("own-numbers-search")
    manager = _make_user(db_session, org, "application_manager", "search")
    _own_many(db_session, org, manager, 20, "Riverbed", "primary")
    _own_many(db_session, org, manager, 5, "Riverbed extra", "backup")
    _own_many(db_session, org, manager, 5, "Other", "primary")

    everything = _soup(_get(client, login_as, manager, "/my-applications/list"))
    assert _tabs(everything)["All"] == 30
    assert "(30 total)" in " ".join(everything.get_text().split())

    found = _soup(_get(client, login_as, manager, "/my-applications/list?search=Riverbed"))
    tabs = _tabs(found)
    assert (tabs["All"], tabs["Primary"], tabs["Backup"], tabs["Technical"], tabs["Business"]) == (25, 20, 5, 0, 0)
    assert "(25 of 30 match your search)" in " ".join(found.get_text().split())
    assert len(_list_rows(found)) == 20

    # Each tab under the search lists exactly the rows its count promises.
    for name in ("Primary", "Backup"):
        selected = _soup(_get(client, login_as, manager, f"/my-applications/list?search=Riverbed&type={name.lower()}"))
        assert len(_list_rows(selected)) == tabs[name]


def _row_names(soup):
    """Names of the application cards on a list page."""
    return sorted(h.get_text(strip=True) for h in soup.find_all("h3") if h.get("class") == ["font-medium"])


def test_an_ownership_row_of_another_organisation_counts_on_neither_side(db_session, make_org, client, login_as):
    """A row whose organisation differs from its user's is ignored by the screens and the banner alike."""
    org_a = make_org("own-numbers-mismatch-a")
    org_b = make_org("own-numbers-mismatch-b")
    portfolio = _make_user(db_session, org_a, "portfolio_manager", "mismatchportfolio")
    manager = _make_user(db_session, org_a, "application_manager", "mismatchmanager")
    stranger = _make_user(db_session, org_b, "application_manager", "mismatchstranger")

    genuine = _make_app(db_session, org_a, "Genuine", "active")
    row_elsewhere = _make_app(db_session, org_a, "Row elsewhere", "active")
    user_elsewhere = _make_app(db_session, org_a, "User elsewhere", "active")
    _make_app(db_session, org_a, "Unowned", "active")

    _own(db_session, org_a, manager, genuine, "primary")
    # The manager and the application are in this organisation, the row is not.
    _own(db_session, org_b, manager, row_elsewhere, "primary")
    # The row and the application are in this organisation, the user is not.
    _own(db_session, org_a, stranger, user_elsewhere, "primary")

    dash = _soup(_get(client, login_as, manager, "/my-applications/"))
    assert _tile(dash, "Total Apps") == 1
    assert len(_panel_rows(dash)) == 1
    listing = _soup(_get(client, login_as, manager, "/my-applications/list"))
    assert _tabs(listing)["All"] == 1
    assert len(_list_rows(listing)) == 1

    outsider = _soup(_get(client, login_as, stranger, "/my-applications/"))
    assert _tile(outsider, "Total Apps") == 0

    # The banner counts the same single application, so the two sides agree.
    banner = _soup(_get(client, login_as, portfolio, "/applications/"))
    assert _portfolio_banner(banner) == (1, 4)


def test_search_text_is_matched_literally(db_session, make_org, client, login_as):
    """%, _ and a backslash in a search term stand for themselves, not for patterns."""
    org = make_org("own-numbers-literal")
    manager = _make_user(db_session, org, "application_manager", "literal")
    names = {
        "percent": "Save 50% today",
        "percent_decoy": "Save 50 today",
        "underscore": "Batch_job runner",
        "underscore_decoy": "BatchXjob runner",
        "backslash": "Share\\drive",
        "backslash_decoy": "Sharedrive",
    }
    apps = {key: _make_app(db_session, org, name, "active") for key, name in names.items()}
    for a in apps.values():
        _own(db_session, org, manager, a, "primary")

    def search(term):
        from urllib.parse import quote

        page = _soup(_get(client, login_as, manager, "/my-applications/list?search=" + quote(term, safe="")))
        return _row_names(page), _tabs(page)["All"]

    for term, key in (("%", "percent"), ("50%", "percent"), ("_", "underscore"), ("h_j", "underscore"), ("\\", "backslash")):
        rows, count = search(term)
        assert count == 1, term
        assert len(rows) == 1 and rows[0].startswith(names[key]), (term, rows)

    # Text with no pattern characters still matches as a substring.
    rows, count = search("runner")
    assert count == 2
    assert len(rows) == 2
