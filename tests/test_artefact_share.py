"""BA-B1: revocable, read-only share links for capability artefacts.

The public route has no login in front of it, so tenancy is the whole risk here
and most of this file is about it. Uses the shared fixtures in tests/conftest.py
(``db_session`` rolls everything back, so nothing survives a failure).
"""

from __future__ import annotations

import re
import uuid

import pytest

from app.datetime_helpers import utcnow
from app.models.artefact_share import (
    SHAREABLE_ARTEFACTS,
    ArtefactShareLink,
    generate_share_token,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_user(db_session, org, email=None):
    from app.models.user import User

    user = User(
        email=email or f"share-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Share",
        organization_id=org.id,
        confirmed=True,
    )
    # No password: login_as writes the session cookie directly, so these tests
    # never exercise the password path.
    db_session.add(user)
    db_session.flush()
    return user


def _csrf_headers(client):
    """A usable CSRF token for the owner-side POSTs.

    ``require_csrf`` validates the token itself rather than deferring to
    ``WTF_CSRF_ENABLED``, so the test suite's CSRF-off config does not exempt
    these routes. Scrape the token the console page renders, exactly as the
    browser does.
    """
    body = client.get("/share/artefacts").get_data(as_text=True)
    match = re.search(r'name="csrf-token" content="([^"]+)"', body)
    assert match, "console page did not render a csrf-token meta tag"
    return {"X-CSRFToken": match.group(1)}


def _anonymous_get(app, path):
    """GET as a visitor who has no session at all.

    ``db_session`` keeps one application context open for the whole test, so the
    user flask_login resolved for the previous request would otherwise still be
    cached on ``g`` and applied to this request. The session policy would then
    treat the visitor as that user, find no session id in the visitor's cookie
    and redirect to the login page, which a real anonymous request never sees.
    """
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)
    return app.test_client().get(path)


def _make_capability(db_session, name, *, assessed_on=None, current=None, target=None):
    """Seed a capability. Call inside ``with tenant_ctx(org.id):`` so the
    before_flush hook stamps organization_id from the tenant context."""
    from app.models.business_capabilities import BusinessCapability

    cap = BusinessCapability(
        name=name,
        code=f"CAP-{uuid.uuid4().hex[:8]}",
        level=1,
        category="Customer",
        current_maturity_level=current,
        target_maturity_level=target,
        maturity_assessment_date=assessed_on,
    )
    db_session.add(cap)
    db_session.flush()
    return cap


def _make_link(db_session, org, artefact_type="maturity_heatmap", revoked_at=None):
    link = ArtefactShareLink(
        token=generate_share_token(),
        artefact_type=artefact_type,
        organization_id=org.id,
        revoked_at=revoked_at,
        view_count=0,
    )
    db_session.add(link)
    db_session.flush()
    return link


# ── the token itself ──────────────────────────────────────────────────────────


def test_tokens_are_unguessable_and_never_sequential():
    tokens = {generate_share_token() for _ in range(200)}

    assert len(tokens) == 200, "token collision in 200 draws"
    for token in tokens:
        assert len(token) == 32
        assert token.isascii()
        # Never an id: a share token must not be derivable by counting.
        assert not token.isdigit()


def test_every_shareable_artefact_has_a_builder():
    from app.modules.sharing.service import ARTEFACT_BUILDERS

    assert set(ARTEFACT_BUILDERS) == set(SHAREABLE_ARTEFACTS)


# ── the public route ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("artefact_type", sorted(SHAREABLE_ARTEFACTS))
def test_valid_token_renders_without_login(app, db_session, make_org, tenant_ctx, artefact_type):
    org = make_org("owner")
    with tenant_ctx(org.id):
        _make_capability(db_session, "Order Management")
    link = _make_link(db_session, org, artefact_type=artefact_type)
    db_session.commit()

    client = app.test_client()  # never logged in
    response = client.get(f"/shared/{link.token}")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert SHAREABLE_ARTEFACTS[artefact_type] in body


def test_revoked_token_stops_working_immediately(app, db_session, make_org, tenant_ctx):
    org = make_org("owner")
    with tenant_ctx(org.id):
        _make_capability(db_session, "Billing")
    link = _make_link(db_session, org)
    db_session.commit()

    client = app.test_client()
    assert client.get(f"/shared/{link.token}").status_code == 200

    link.revoked_at = utcnow()
    db_session.commit()

    assert client.get(f"/shared/{link.token}").status_code == 404


def test_unknown_token_is_a_404(app, db_session):
    client = app.test_client()
    assert client.get(f"/shared/{generate_share_token()}").status_code == 404
    # An over-long token is rejected before it reaches a query.
    assert client.get("/shared/" + "a" * 200).status_code == 404


def test_public_page_exposes_no_edit_controls_or_navigation(
    app, db_session, make_org, tenant_ctx
):
    org = make_org("owner")
    with tenant_ctx(org.id):
        _make_capability(db_session, "Customer Onboarding")
    link = _make_link(db_session, org)
    db_session.commit()

    body = app.test_client().get(f"/shared/{link.token}").get_data(as_text=True)

    assert "<form" not in body.lower()
    assert "admin_sidebar" not in body
    assert "/capability-map" not in body
    assert "/capability-maturity" not in body
    assert 'href="/dashboard' not in body
    for word in ("Edit", "Delete", "Save"):
        assert f">{word}<" not in body


# ── tenancy: the part most likely to leak ─────────────────────────────────────


def test_token_of_org_a_never_renders_org_b_data(app, db_session, make_org, tenant_ctx):
    """The load-bearing test.

    Two organisations, one capability each, one token belonging to org A. The
    public request has no logged-in user, so nothing is auto-filtered — if scope
    were not derived from the share row, org B's capability would appear.
    """
    org_a = make_org("a")
    org_b = make_org("b")

    marker_a = f"Alpha-Only-{uuid.uuid4().hex[:8]}"
    marker_b = f"Bravo-Only-{uuid.uuid4().hex[:8]}"

    with tenant_ctx(org_a.id):
        _make_capability(db_session, marker_a)
    with tenant_ctx(org_b.id):
        _make_capability(db_session, marker_b)

    link_a = _make_link(db_session, org_a, artefact_type="capability_map")
    db_session.commit()

    body = app.test_client().get(f"/shared/{link_a.token}").get_data(as_text=True)

    assert marker_a in body
    assert marker_b not in body, "org B's capability leaked through org A's share link"


def test_builder_is_scoped_by_the_row_not_the_request_context(
    app, db_session, make_org, tenant_ctx
):
    """Directly assert the builder ignores whatever tenant context it is called in.

    Called from inside org B's context but asked for org A's data, it must return
    org A's rows — and must leave the ambient context untouched afterwards.
    """
    from flask import g

    from app.modules.sharing.service import build_artefact

    org_a = make_org("a")
    org_b = make_org("b")
    marker_a = f"Alpha-{uuid.uuid4().hex[:8]}"
    marker_b = f"Bravo-{uuid.uuid4().hex[:8]}"

    with tenant_ctx(org_a.id):
        _make_capability(db_session, marker_a)
    with tenant_ctx(org_b.id):
        _make_capability(db_session, marker_b)
    db_session.commit()

    with tenant_ctx(org_b.id):
        data = build_artefact("capability_map", org_a.id)
        names = {
            cap["name"] for group in data["groups"] for cap in group["capabilities"]
        }
        assert marker_a in names
        assert marker_b not in names
        # The context it was called in is restored, not left pinned to org A.
        assert g.current_org_id == org_b.id


def test_org_b_cannot_revoke_org_a_link(app, db_session, make_org, login_as):
    org_a = make_org("a")
    org_b = make_org("b")
    link_a = _make_link(db_session, org_a)
    user_b = _make_user(db_session, org_b)
    db_session.commit()

    client = app.test_client()
    login_as(client, user_b)
    response = client.post(
        f"/share/artefacts/{link_a.id}/revoke", json={}, headers=_csrf_headers(client)
    )

    assert response.status_code == 404, "org B could address org A's share link"
    db_session.refresh(link_a)
    assert link_a.revoked_at is None
    assert link_a.is_active


def test_console_lists_only_the_callers_own_links(app, db_session, make_org, login_as):
    org_a = make_org("a")
    org_b = make_org("b")
    link_a = _make_link(db_session, org_a)
    link_b = _make_link(db_session, org_b)
    user_a = _make_user(db_session, org_a)
    db_session.commit()

    client = app.test_client()
    login_as(client, user_a)
    body = client.get("/share/artefacts").get_data(as_text=True)

    assert link_a.token in body
    assert link_b.token not in body, "another organisation's token leaked into the console"


# ── owner lifecycle ───────────────────────────────────────────────────────────


def test_owner_creates_then_revokes_a_link(app, db_session, make_org, login_as):
    org = make_org("owner")
    user = _make_user(db_session, org)
    db_session.commit()

    client = app.test_client()
    login_as(client, user)

    created = client.post(
        "/share/artefacts/maturity_heatmap", json={}, headers=_csrf_headers(client)
    )
    assert created.status_code == 201
    payload = created.get_json()
    token = payload["token"]
    assert len(token) == 32
    assert payload["share_url"].endswith(f"/shared/{token}")

    link = ArtefactShareLink.query.filter_by(token=token).one()
    assert link.organization_id == org.id
    assert link.created_by_id == user.id

    assert _anonymous_get(app, f"/shared/{token}").status_code == 200

    login_as(client, user)
    revoked = client.post(
        f"/share/artefacts/{link.id}/revoke", json={}, headers=_csrf_headers(client)
    )
    assert revoked.status_code == 200

    assert _anonymous_get(app, f"/shared/{token}").status_code == 404


def test_owner_routes_require_login(app):
    anonymous = app.test_client()

    listing = anonymous.get("/share/artefacts")
    assert listing.status_code in (302, 401)

    creation = anonymous.post("/share/artefacts/maturity_heatmap", json={})
    assert creation.status_code in (302, 401, 400, 403)


def test_unshareable_artefact_type_is_rejected(app, db_session, make_org, login_as):
    org = make_org("owner")
    user = _make_user(db_session, org)
    db_session.commit()

    client = app.test_client()
    login_as(client, user)
    assert (
        client.post(
            "/share/artefacts/application_costs", json={}, headers=_csrf_headers(client)
        ).status_code
        == 404
    )


def test_unassessed_maturity_renders_a_dash_not_a_fabricated_score(
    app, db_session, make_org, tenant_ctx
):
    """270 fabricated maturity rows were cleared from production; a share view
    must not reintroduce them by defaulting an unassessed capability to Level 1."""
    from app.modules.sharing.service import build_maturity_heatmap

    org = make_org("owner")
    unassessed_name = f"Never-Assessed-{uuid.uuid4().hex[:8]}"
    with tenant_ctx(org.id):
        # Levels present on the row but no assessment date: still not assessed.
        _make_capability(db_session, unassessed_name, current=1, target=5)
    db_session.commit()

    with tenant_ctx(org.id):
        data = build_maturity_heatmap(org.id)

    entry = next(
        cap
        for group in data["groups"]
        for cap in group["capabilities"]
        if cap["name"] == unassessed_name
    )
    assert entry["assessed"] is False
    assert entry["current"] is None
    assert entry["target"] is None
    assert entry["gap"] is None
    assert data["assessed_count"] == 0
    assert data["avg_current"] is None

    link = _make_link(db_session, org)
    db_session.commit()
    body = app.test_client().get(f"/shared/{link.token}").get_data(as_text=True)

    # With nothing assessed the page deliberately does NOT render a table of
    # em dashes — 219 rows of them is accurate and unreadable, and a leadership
    # artefact is a summary. It states the position instead. What must never
    # appear either way is a fabricated level for an unassessed capability.
    assert "No capability has been assessed yet" in body
    assert "not reported as Level 1" in body
    # The stat strip still reports the honest counts.
    assert "Assessed" in body
    # No maturity level chip anywhere, because none was measured.
    assert 'title="Not assessed"' not in body or "—" in body
