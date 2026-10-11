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
    "admin.audit_log_viewer": (
        "/admin/audit-log",
        "Each entry is sealed to the one before it",
    ),
    "admin.governance_gates": ("/admin/governance-gates", "Governance Gates"),
    "admin.power_platform_integration": (
        "/admin/integrations/power-platform",
        "Power Platform CoE Integration",
    ),
    "admin.salesforce_integration": (
        "/admin/integrations/salesforce",
        "Salesforce Org Discovery",
    ),
    # admin.seed_management was folded from platform_admin's Admin zone to
    # make room for "Audit Log" within the link budget; it is a tile on the
    # admin dashboard page (Command Center) instead.
    "error_events.errors_dashboard": (
        "/admin/errors",
        "Deduplicated server + client errors, aggregated by fingerprint across every organization.",
    ),
    "intelligence_ui.value_streams_at_risk": (
        "/intelligence/value-streams-at-risk",
        "Value streams at risk",
    ),
    # Canvas/framework UI fix (24 Sep 2026): batch_import_view.dashboard was
    # intentionally folded from platform_admin's Admin zone to stay within the
    # link budget.
    "consolidation_list.dashboard": (
        "/consolidation-list/",
        "Consolidation List Dashboard",
    ),
    # Canvas/framework UI fix, round 2 (25 Sep 2026): dashboard_pages.
    # import_history was intentionally folded from platform_admin's Admin
    # zone (with Batch Import above) to stay within the link budget once
    # "Canvases" and "Frameworks" joined every role's Library zone. Both are
    # tiles on the admin dashboard page instead of a sidebar zone entry --
    # see app/utils/role_access.py's _ADMIN_LINKS comment.
    "dashboard_pages.rationalization_scorecard": (
        "/dashboard/rationalization/scorecard",
        "Executive Rationalization Scorecard",
    ),
    "data_architecture.data_architecture_dashboard": (
        "/architecture/data-architecture",
        "Data Architecture Dashboard",
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


def _make_user(db_session, enterprise_role="platform_admin"):
    """A confirmed user in a fresh org, carrying *enterprise_role*.

    A Role row is attached because ``@require_roles`` / ``admin_required``
    routes 403 a user holding no role at all — which would make this file
    assert "the link is unreachable" rather than "the page renders".
    ``is_platform_admin`` is set for the same reason: four of these pages sit
    in the Admin zone, which is gated on that real boolean.
    """
    from sqlalchemy import select

    from app.models.organization import Organization
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Nav Verify {suffix}", slug=f"nav-verify-{suffix}")
    db_session.add(org)
    db_session.flush()

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Administrator").first()

    # User's mapper-level audit hook writes its own audit row on the flush
    # connection. In this rollback-fixture transaction shape that can leave the
    # connection aborted even when the insert itself succeeds, which would make
    # this file prove only the fixture breakage rather than whether the sidebar
    # route renders. Insert the row directly, then load the mapped User back for
    # login and route guards.
    inserted = db_session.execute(
        User.__table__.insert().values(
            email=f"nav-{suffix}@example.com",
            first_name="Nav",
            last_name="Verifier",
            organization_id=org.id,
            confirmed=True,
            enterprise_role=enterprise_role,
            is_platform_admin=True,
            role_id=role.id,
        )
    )
    user_id = inserted.inserted_primary_key[0]
    return db_session.execute(select(User).where(User.id == user_id)).scalar_one()


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
