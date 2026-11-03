"""Viewpoint pages read the signed-in organisation's live model and state its gaps.

Each test uses real organisations and elements written through the ORM. The pages must:
open for a signed-in member, show only that organisation's elements, compute gaps on the
request (an unrelated element shows as unconnected, and stops showing once related), and
refuse an address outside the drawn viewpoints.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture(autouse=True)
def _no_two_factor_banner(monkeypatch):
    """The first-week two-factor banner is not under test here; its date format ('%-d') is glibc-only
    and raises on Windows, so it is switched off to let these tests run on any machine."""
    from app.services import mfa_service

    monkeypatch.setattr(mfa_service, "window_ends_at", lambda user: None)


def _forget_cached_user():
    from flask import g, has_app_context

    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)


def _signed_in(app, db_session, org):
    from app.models.user import User
    from tests._session_test_helpers import mint_test_sid

    user = User(email=f"viewpoint-{uuid.uuid4().hex[:8]}@example.com", first_name="View", last_name="Point",
                organization_id=org.id, confirmed=True, enterprise_role="enterprise_architect")
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    sid = mint_test_sid(user.id)
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
        if sid:
            sess["_sid"] = sid
    _forget_cached_user()
    return client


def _element(db_session, org, name, type_, layer="Motivation", description=None):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org.id, description=description)
    db_session.add(el)
    db_session.flush()
    return el


def _relate(db_session, org, source, target, type_="Influence"):
    from app.models.archimate_core import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org.id)
    db_session.add(rel)
    db_session.flush()
    return rel


def test_index_lists_the_drawn_viewpoints(app, db_session, make_org):
    client = _signed_in(app, db_session, make_org("vp-index"))
    resp = client.get("/archimate/viewpoints/")
    assert resp.status_code == 200, resp.status_code
    html = resp.get_data(as_text=True)
    for code in ("motivation", "layered", "requirements_realization"):
        assert f"/archimate/viewpoints/{code}" in html


def test_an_undrawn_viewpoint_is_not_found(app, db_session, make_org):
    client = _signed_in(app, db_session, make_org("vp-404"))
    assert client.get("/archimate/viewpoints/no_such_view").status_code == 404


def test_another_organisations_elements_never_show(app, db_session, make_org):
    mine, theirs = make_org("vp-mine"), make_org("vp-theirs")
    tag = uuid.uuid4().hex[:6]
    _element(db_session, mine, f"Grow revenue {tag}", "Goal", description="Ours")
    _element(db_session, theirs, f"Secret goal {tag}", "Goal", description="Theirs")
    client = _signed_in(app, db_session, mine)
    html = client.get("/archimate/viewpoints/motivation").get_data(as_text=True)
    assert f"Grow revenue {tag}" in html
    assert f"Secret goal {tag}" not in html


def test_gaps_are_computed_on_each_request(app, db_session, make_org):
    org = make_org("vp-gaps")
    driver = _element(db_session, org, "Market pressure", "Driver", description="Competitors cut prices")
    goal = _element(db_session, org, "Keep margin", "Goal")
    client = _signed_in(app, db_session, org)

    html = client.get("/archimate/viewpoints/motivation").get_data(as_text=True)
    assert 'data-gap="unconnected"' in html
    assert 'data-gap="no-description"' in html
    assert 'data-gap="missing-types"' in html

    _relate(db_session, org, driver, goal)
    goal.description = "Hold gross margin above 40 per cent"
    db_session.flush()
    _forget_cached_user()
    html = client.get("/archimate/viewpoints/motivation").get_data(as_text=True)
    assert 'data-gap="unconnected"' not in html
    assert 'data-gap="no-description"' not in html


def test_readiness_names_each_gap_with_why_and_next():
    from types import SimpleNamespace as NS

    from app.modules.architecture.routes.viewpoint_pages import readiness

    vp = NS(elements=[NS(id=1, element_type="Goal", description=""), NS(id=2, element_type="Driver", description="d")],
            relationships=[], metadata={})
    gaps, loose = readiness(vp, NS(allowed_elements=["Goal", "Driver", "Outcome"]))
    assert loose == {1, 2}
    keys = {g["key"] for g in gaps}
    assert keys == {"missing-types", "unconnected", "no-description"}
    assert all(g["why"] and g["next"] for g in gaps)


def test_a_capitalised_relationship_type_counts_in_the_viewpoint():
    """The model stores "Influence"; the definition lists "influence". Both must match."""
    from app.config.archimate_viewpoints import is_relationship_allowed_in_viewpoint

    assert is_relationship_allowed_in_viewpoint("Influence", "MOT")
    assert is_relationship_allowed_in_viewpoint("influence", "MOT")
    assert not is_relationship_allowed_in_viewpoint("Flow", "MOT")
