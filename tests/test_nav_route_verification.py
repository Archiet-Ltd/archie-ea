"""Every sidebar link is loaded by a test, and renders its own page.

`scripts/route_verification_audit.py` measures the one combination that
actually hurts a user: a route reachable from a persona's sidebar that no test
has ever served. Line coverage cannot see it — such a route can be fully
covered by unit tests of its service layer and still 500 the moment somebody
clicks the link, and clicking it is the only thing a user will ever do.

This file closes that set (`nav_verified` 15 -> 0). For each endpoint it
asserts two things, because either alone is worthless:

* the response is not an error (``status < 400``), and
* the page rendered **its own** content — a marker string that only that
  screen's template produces, or, for the endpoints that are deliberate
  redirects, the specific target they must land on.

A bare ``assert status == 200`` passes for a page that silently rendered an
error partial, an empty shell, or somebody else's template — which is exactly
the state an unclicked link degrades into.

Uses the shared fixtures in tests/conftest.py (``app``, ``db_session``) — see
CLAUDE.md. The hand-rolled module-scoped ``app`` fixtures older test modules
carry are flaky by construction and must not be copied.
"""

from __future__ import annotations

import uuid

import pytest

from app.utils.role_access import SIDEBAR_ZONES

pytestmark = pytest.mark.usefixtures("db_session")


# endpoint -> (url, marker). The marker is a string only that page's template
# emits; for the two redirect endpoints it is the Location they must send the
# user to, asserted instead of the body.
NAV_PAGES = {
    "admin.governance_gates": ("/admin/governance-gates", "Governance Gates"),
    "admin.power_platform_integration": (
        "/admin/integrations/power-platform",
        "Power Platform CoE Integration",
    ),
    "admin.salesforce_integration": (
        "/admin/integrations/salesforce",
        "Salesforce Org Discovery",
    ),
    "admin.seed_management": ("/admin/seed-management", "Seed Management"),
    "error_events.errors_dashboard": (
        "/admin/errors",
        "Deduplicated server + client errors, aggregated by fingerprint across every organization.",
    ),
    "batch_import_view.dashboard": ("/batch-import/", "Batch Import Dashboard"),
    "consolidation_list.dashboard": (
        "/consolidation-list/",
        "Consolidation List Dashboard",
    ),
    "dashboard_pages.import_history": ("/dashboard/import-history", "Import History"),
    "dashboard_pages.rationalization_scorecard": (
        "/dashboard/rationalization/scorecard",
        "Executive Rationalization Scorecard",
    ),
    "main.capability_roadmap": ("/capability-roadmap", "Enterprise Capability Roadmap"),
    "main.settings": ("/settings", "System Settings"),
    # NAV-1: the sidebar used to name solution_prompt_admin.
    # solution_prompts_page here, which registers the same rule as this one and
    # loses the URL-map match — it could never be served. See role_access.py.
    "admin.solution_prompts_page": ("/admin/solution-prompts", "Solution AI Prompts"),
    "strategic.capability_health": (
        "/strategic/capability-health",
        "Capability Health Dashboard",
    ),
    "unified_duplicate.simple_dashboard": (
        "/duplicate-detection/simple",
        "Duplicate Detection",
    ),
}

# Endpoints whose whole job is to hand the user on to another screen. Asserting
# a body marker would pin the wrong contract; the redirect target IS the
# contract, and a 302 to the wrong place is the failure this catches.
NAV_REDIRECTS = {
    # "Motivation Model" (business_architect). The motivation layer is a view
    # mode of the ArchiMate element browser, not a page of its own.
    "architect_ui.motivation_view": ("/architecture/motivation", "layer=motivation"),
}

# Sidebar diet (22 Sep 2026): Data Architecture came out of every persona
# zone that carried it (see app/modules/modules_directory/routes.py's
# _DARK), so it no longer belongs in NAV_PAGES above -- test_every_endpoint_
# covered_here_is_still_in_a_sidebar would fail the moment it is not in one.
# The page still renders its own real content for a user who has the
# address, which is exactly what test_sidebar_page_renders_its_own_content
# proves for NAV_PAGES; this is the same proof for a page that is
# deliberately no longer sidebar-reachable, kept out of the sidebar
# cross-check on purpose.
DARK_PAGES = {
    "data_architecture.data_architecture_dashboard": (
        "/architecture/data-architecture",
        "Data Architecture Dashboard",
    ),
}


def _make_user(db_session, enterprise_role="platform_admin"):
    """A confirmed user in a fresh org, carrying *enterprise_role*.

    A Role row is attached because ``@require_roles`` / ``admin_required``
    routes 403 a user holding no role at all — which would make this file
    assert "the link is unreachable" rather than "the page renders".
    ``is_platform_admin`` is set for the same reason: four of these pages sit
    in the Admin zone, which is gated on that real boolean.
    """
    from app.models.organization import Organization
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Nav Verify {suffix}", slug=f"nav-verify-{suffix}")
    db_session.add(org)
    db_session.flush()

    user = User(
        email=f"nav-{suffix}@example.com",
        first_name="Nav",
        last_name="Verifier",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=enterprise_role,
        is_platform_admin=True,
    )
    db_session.add(user)
    db_session.flush()

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()
    user.role = role
    db_session.flush()
    return user


def _login(client, user_id):
    """Log in, defeating flask_login's ``g`` cache — see tests/conftest.py."""
    from flask import g, has_app_context

    from tests._session_test_helpers import mint_test_sid
    _sid = mint_test_sid(user_id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
        if _sid:
            sess["_sid"] = _sid
    if not has_app_context():
        return
    for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
        if hasattr(g, cached):
            delattr(g, cached)


@pytest.mark.parametrize(
    "endpoint,url,marker",
    [(ep, url, marker) for ep, (url, marker) in sorted(NAV_PAGES.items())],
)
def test_sidebar_page_renders_its_own_content(
    app, db_session, endpoint, url, marker
):
    """A link a persona can click serves that persona's page, not an error."""
    user = _make_user(db_session)
    client = app.test_client()
    _login(client, user.id)

    resp = client.get(url, follow_redirects=True)

    assert resp.status_code < 400, (
        f"{endpoint} ({url}) returned {resp.status_code} — it is in a persona's "
        f"sidebar, so this is a link a user can click into an error"
    )
    body = resp.get_data(as_text=True)
    assert marker in body, (
        f"{endpoint} ({url}) returned {resp.status_code} but did not render its "
        f"own page: {marker!r} is absent. A 200 alone does not mean the screen "
        f"rendered — an error partial or an empty shell also returns 200."
    )


@pytest.mark.parametrize(
    "endpoint,url,target_fragment",
    [(ep, url, tgt) for ep, (url, tgt) in sorted(NAV_REDIRECTS.items())],
)
def test_sidebar_redirect_lands_where_it_claims(
    app, db_session, endpoint, url, target_fragment
):
    """A redirect-only sidebar link must send the user to the right screen."""
    user = _make_user(db_session)
    client = app.test_client()
    _login(client, user.id)

    resp = client.get(url, follow_redirects=False)

    assert resp.status_code in (301, 302, 303, 307, 308), (
        f"{endpoint} ({url}) returned {resp.status_code}; this link exists to "
        f"redirect and a non-redirect means the target moved"
    )
    location = resp.headers.get("Location", "")
    assert target_fragment in location, (
        f"{endpoint} ({url}) redirected to {location!r}, which does not carry "
        f"{target_fragment!r} — the link would land the user on the wrong view"
    )


@pytest.mark.parametrize(
    "endpoint,url,marker",
    [(ep, url, marker) for ep, (url, marker) in sorted(DARK_PAGES.items())],
)
def test_dark_page_still_renders_its_own_content(app, db_session, endpoint, url, marker):
    """No sidebar link reaches this page any more, but a user who has the
    address still gets the real page, not a stub or an error."""
    user = _make_user(db_session)
    client = app.test_client()
    _login(client, user.id)

    resp = client.get(url, follow_redirects=True)

    assert resp.status_code == 200, (
        f"{endpoint} ({url}) returned {resp.status_code} for a logged-in user"
    )
    body = resp.get_data(as_text=True)
    assert marker in body, (
        f"{endpoint} ({url}) returned 200 but did not render its own page: "
        f"{marker!r} is absent."
    )


def test_every_endpoint_covered_here_is_still_in_a_sidebar():
    """Guard against this file drifting into testing links nobody can click.

    If a link is retired from SIDEBAR_ZONES its entry belongs somewhere else
    (or nowhere); leaving it here would keep the nav-verified gate green for a
    route that is no longer navigation at all.
    """
    nav_endpoints = {
        link["endpoint"]
        for zones in SIDEBAR_ZONES.values()
        for zone in zones
        for link in zone["links"]
    }
    covered = set(NAV_PAGES) | set(NAV_REDIRECTS)
    orphaned = covered - nav_endpoints
    assert not orphaned, (
        f"{sorted(orphaned)} are covered here but are no longer in any "
        f"persona's sidebar"
    )
