"""Eight real, working pages come out of every persona's sidebar zone and out
of the All-modules directory's rendered rows, with no change to any route,
blueprint, template or service behind them. Each stays reachable by its own
URL and by any deep link that already points at it directly; only the ambient
navigation surfaces stop advertising it.

Uses the shared fixtures in tests/conftest.py (`app`, `db_session`,
`make_org`, `login_as`) per CLAUDE.md, the same pattern
tests/test_s11_nav_discoverability.py and
tests/journeys/test_journey_security_and_data_architect.py already use.
"""

from __future__ import annotations

import uuid

import pytest
from flask import url_for

from app.modules.modules_directory.routes import (
    _DARK,
    _NOT_RENDERED,
    all_module_links,
    visible_module_links,
)

pytestmark = pytest.mark.usefixtures("db_session")

DARK_ENDPOINTS = sorted(_DARK)

# Every persona the product defines today, checked against get_sidebar_zones
# directly rather than through a rendered page — the same approach
# tests/test_sidebar_budgets.py and the journeys test for two of these
# personas already take.
ALL_ROLES = [
    "solution_architect",
    "enterprise_architect",
    "business_architect",
    "arb_member",
    "portfolio_manager",
    "cto",
    "application_manager",
    "procurement",
    "platform_admin",
    "security_architect",
    "data_architect",
]


def _user(db_session, make_org, label, role="enterprise_architect"):
    from app.models.user import User

    org = make_org(f"dark-{label}")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"dark-{label}-{suffix}@example.com",
        first_name="Dark",
        last_name="Tester",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


# ── (5) the set itself: eight, disjoint from _NOT_RENDERED, every key real ──


def test_the_dark_set_is_exactly_eight_and_disjoint_from_not_rendered():
    assert len(_DARK) == 8
    assert set(_DARK) & set(_NOT_RENDERED) == set()


@pytest.mark.parametrize("endpoint", DARK_ENDPOINTS)
def test_every_dark_key_resolves_in_the_url_map(app, endpoint):
    """A typo'd or renamed endpoint here would BuildError every render that
    touches all_module_links() -- resolved once per key, standalone."""
    with app.test_request_context():
        url_for(endpoint)


# ── (1) still answers by URL: 200 or the route's own redirect, never 404 ────


@pytest.mark.parametrize("endpoint", DARK_ENDPOINTS)
def test_dark_endpoint_still_answers_by_url(app, db_session, make_org, login_as, endpoint):
    user = _user(db_session, make_org, "reach")
    client = app.test_client()
    login_as(client, user)
    with app.test_request_context():
        path = url_for(endpoint)
    resp = client.get(path)
    if endpoint == "usage_analytics.analytics_root":
        # This route's whole job is to redirect to the dashboard -- assert
        # the exact target, not just "some 3xx", or a redirect to the wrong
        # place (or to nowhere) would pass this test.
        assert resp.status_code in (301, 302, 303, 307, 308), (
            f"{endpoint} ({path}) returned {resp.status_code}, expected a redirect"
        )
        with app.test_request_context():
            target = url_for("usage_analytics.analytics_dashboard")
        location = resp.headers.get("Location", "")
        assert target in location, (
            f"{endpoint} ({path}) redirected to {location!r}, expected {target!r}"
        )
    else:
        assert resp.status_code == 200, (
            f"{endpoint} ({path}) returned {resp.status_code} for a logged-in user "
            "-- it must keep answering by its own URL"
        )


# ── (2) absent from visible_module_links() and every persona's sidebar ──────


@pytest.mark.parametrize("endpoint", DARK_ENDPOINTS)
def test_dark_endpoint_is_not_in_visible_module_links(app, endpoint):
    with app.test_request_context("/"):
        visible = {link["endpoint"] for link in visible_module_links()}
    assert endpoint not in visible, f"{endpoint} is still returned by visible_module_links()"


@pytest.mark.parametrize("endpoint", DARK_ENDPOINTS)
@pytest.mark.parametrize("role", ALL_ROLES)
def test_dark_endpoint_is_not_in_any_personas_sidebar(app, role, endpoint):
    from app.utils.role_access import get_sidebar_zones

    class _StubUser:
        enterprise_role = role
        is_platform_admin = role == "platform_admin"

        def is_admin(self):
            return self.is_platform_admin

    with app.app_context():
        zones = get_sidebar_zones(_StubUser())
        linked = {link["endpoint"] for zone in zones for link in zone["links"]}
    assert endpoint not in linked, f"{endpoint} is still in {role}'s sidebar"


# ── (3) still known to all_module_links() (or in _DARK directly) ────────────


@pytest.mark.parametrize("endpoint", DARK_ENDPOINTS)
def test_dark_endpoint_is_in_all_module_links_or_dark(app, endpoint):
    with app.test_request_context("/"):
        known = {link["endpoint"] for link in all_module_links()}
    assert endpoint in known or endpoint in _DARK, (
        f"{endpoint} dropped out of all_module_links() and is not in _DARK -- "
        "the discoverability audit would report it an orphan"
    )


# ── (4) the rendered /modules page does not offer a row for it ──────────────

# The exact row shape app/modules/modules_directory/templates/modules_directory/
# index.html emits for both a zone section and More tools -- the same pattern
# tests/test_modules_directory.py's ROW_HREF already relies on. A brand-new,
# onboarding-incomplete user also sees a first-run welcome panel on this page,
# unrelated to and unchanged by this directory, that names two of the eight in
# its own persona-specific quick links using a different markup shape entirely
# (a plain Alpine data object, not this `<li x-show='matches(...)'>` row) --
# scoping to this pattern is what keeps that panel out of the count.
ROW_HREF = r'''<li x-show=['"]matches\([^)]*\)['"]>\s*<a href="([^"]*)"'''


@pytest.mark.parametrize("endpoint", DARK_ENDPOINTS)
def test_dark_endpoint_has_no_row_on_the_rendered_directory(
    app, db_session, make_org, login_as, endpoint
):
    import re

    user = _user(db_session, make_org, "label")
    client = app.test_client()
    login_as(client, user)
    with app.test_request_context():
        own_url = url_for(endpoint)
    resp = client.get("/modules/")
    assert resp.status_code == 200, (
        f"/modules/ returned {resp.status_code} for a logged-in user -- a silently "
        "failed login would otherwise pass this test against an empty or error page"
    )
    html = resp.get_data(as_text=True)
    row_hrefs = re.findall(ROW_HREF, html)
    assert own_url not in row_hrefs, (
        f"{endpoint} ({own_url}) still has a directory row on the /modules page"
    )
