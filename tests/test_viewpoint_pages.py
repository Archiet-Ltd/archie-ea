"""Viewpoint pages read the signed-in organisation's live model and state its gaps.

Each test uses real organisations and elements written through the ORM. The pages must:
open for a signed-in member, show only that organisation's elements, compute gaps on the
request (an unrelated element shows as unconnected, and stops showing once related), and
refuse an address outside the drawn viewpoints.
"""

from __future__ import annotations

import re
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


def test_index_shows_every_viewpoint_and_opens_the_drawn_ones(app, db_session, make_org):
    from app.config.archimate_viewpoints import VIEWPOINTS

    org = make_org("vp-index")
    tag = uuid.uuid4().hex[:6]
    _element(db_session, org, f"Goal {tag}", "Goal", description="d")
    client = _signed_in(app, db_session, org)
    resp = client.get("/architecture/viewpoints/")
    assert resp.status_code == 200, resp.status_code
    html = resp.get_data(as_text=True)
    for slug, definition in VIEWPOINTS.items():
        assert f'data-viewpoint="{slug}"' in html, slug
    for code in ("motivation", "layered", "requirements_realization"):
        assert f'href="/architecture/viewpoints/{code}"' in html
    assert 'href="/architecture/viewpoints/organization"' not in html
    assert 'data-testid="viewpoint-count"' in html


def test_a_viewpoint_that_cannot_be_built_says_so(app, db_session, make_org, monkeypatch):
    from app.modules.architecture.services import viewpoint_builder

    def broken(self, *args, **kwargs):
        raise RuntimeError("model unreadable")

    monkeypatch.setattr(viewpoint_builder.ViewpointBuilder, "build_viewpoint", broken)
    client = _signed_in(app, db_session, make_org("vp-error"))
    resp = client.get("/architecture/viewpoints/motivation")
    assert resp.status_code == 500
    html = resp.get_data(as_text=True)
    assert 'data-testid="viewpoint-error"' in html
    assert "Nothing in your model was changed" in html
    # The reader gets a reference to quote, never the exception class.
    assert "RuntimeError" not in html
    assert re.search(r'reference &#34;[0-9a-f]{8}&#34;|reference "[0-9a-f]{8}"', html), html[html.find("reference"):][:120]


def test_the_index_says_when_the_counts_cannot_be_read(app, db_session, make_org, monkeypatch):
    from app.modules.architecture.routes import viewpoint_pages

    client = _signed_in(app, db_session, make_org("vp-count-error"))
    monkeypatch.setattr(viewpoint_pages, "_type_counts", lambda organization_id: None)
    index = client.get("/architecture/viewpoints/").get_data(as_text=True)
    assert 'data-testid="viewpoint-count-error"' in index
    assert 'data-testid="viewpoint-count"' not in index


def test_type_counts_is_none_when_the_query_fails(app, db_session, make_org, monkeypatch):
    from app import db
    from app.modules.architecture.routes.viewpoint_pages import _type_counts

    org = make_org("vp-count-raise")

    def unreadable(*args, **kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(db.session, "query", unreadable)
    assert _type_counts(org.id) is None


def test_the_index_counts_in_one_query_not_one_build_per_card(app, db_session, make_org, monkeypatch):
    from app.modules.architecture.routes import viewpoint_pages

    org = make_org("vp-count-once")
    for i in range(3):
        _element(db_session, org, f"Goal {i}", "Goal", description="d")
    _element(db_session, org, "App", "ApplicationComponent", layer="Application", description="d")

    def no_build(*args, **kwargs):
        raise AssertionError("the index must not build each viewpoint to count it")

    monkeypatch.setattr(viewpoint_pages, "_builder", no_build)
    client = _signed_in(app, db_session, org)
    html = client.get("/architecture/viewpoints/").get_data(as_text=True)
    motivation = html[html.index('data-testid="viewpoint-card" data-viewpoint="motivation"'):][:3000]
    assert re.search(r'data-testid="viewpoint-count">\s*3\s*<', motivation), motivation[:600]
    layered = html[html.index('data-testid="viewpoint-card" data-viewpoint="layered"'):]
    assert re.search(r'data-testid="viewpoint-count">\s*4\s*<', layered[:3000]), layered[:600]


def test_each_card_links_to_the_composer_and_none_says_coming_next(app, db_session, make_org):
    from app.config.archimate_viewpoints import VIEWPOINTS
    from app.services.archimate_viewpoint_service import STANDARD_VIEWPOINTS

    client = _signed_in(app, db_session, make_org("vp-composer"))
    html = client.get("/architecture/viewpoints/").get_data(as_text=True)
    assert "coming next" not in html.lower()
    assert html.count('data-testid="viewpoint-composer-link"') == len(VIEWPOINTS)
    assert 'href="/archimate/composer?viewpoint=motivation"' in html
    assert all(slug in STANDARD_VIEWPOINTS for slug in ("motivation", "layered"))


def test_readers_see_no_spec_hash_or_bracketed_citation_ids(app, db_session, make_org, monkeypatch):
    from app.modules.architecture.routes import viewpoint_pages

    org = make_org("vp-cite")
    goal = _element(db_session, org, "Grow margin", "Goal", description="g")
    facts = {goal.id: [{"citation": "biz:cap:42", "text": "Supported by 1 application", "href": None, "warn": False}]}
    sources = [{"domain": "business", "ok": True, "nodes": 1, "spec_hash": "abcdef1234567890", "as_of": "2026-11-04"}]
    monkeypatch.setattr(viewpoint_pages, "genome_facts", lambda organization_id: (facts, sources))
    client = _signed_in(app, db_session, org)
    html = client.get("/architecture/viewpoints/motivation").get_data(as_text=True)
    assert "Supported by 1 application" in html
    assert 'data-citation="biz:cap:42"' in html
    assert "[biz:cap:42]" not in html
    assert 'data-spec-hash="abcdef1234567890"' in html
    assert "spec abcdef" not in html


def test_the_old_archimate_address_is_not_these_pages(app, db_session, make_org):
    client = _signed_in(app, db_session, make_org("vp-old"))
    resp = client.get("/archimate/viewpoints/")
    assert 'data-testid="viewpoints-index"' not in resp.get_data(as_text=True)


def test_an_undrawn_viewpoint_is_not_found(app, db_session, make_org):
    client = _signed_in(app, db_session, make_org("vp-404"))
    assert client.get("/architecture/viewpoints/no_such_view").status_code == 404


def test_another_organisations_elements_never_show(app, db_session, make_org):
    mine, theirs = make_org("vp-mine"), make_org("vp-theirs")
    tag = uuid.uuid4().hex[:6]
    _element(db_session, mine, f"Grow revenue {tag}", "Goal", description="Ours")
    _element(db_session, theirs, f"Secret goal {tag}", "Goal", description="Theirs")
    client = _signed_in(app, db_session, mine)
    html = client.get("/architecture/viewpoints/motivation").get_data(as_text=True)
    assert f"Grow revenue {tag}" in html
    assert f"Secret goal {tag}" not in html


def test_diagram_relationships_carry_the_ends_the_renderer_joins(app, db_session, make_org):
    import json
    import re

    org = make_org("vp-lines")
    driver = _element(db_session, org, "Margin pressure", "Driver", description="d")
    goal = _element(db_session, org, "Grow margin", "Goal", description="g")
    _relate(db_session, org, driver, goal)
    client = _signed_in(app, db_session, org)
    html = client.get("/architecture/viewpoints/motivation").get_data(as_text=True)
    m = re.search(r"viewpoint-diagram-data\" type=\"application/json\">(.*?)</script>", html, re.S)
    assert m, html[html.find("viewpoint-diagram"):][:400]
    raw = m.group(1)
    rels = json.loads(raw)["relationships"]
    assert rels and rels[0]["source_id"] == driver.id and rels[0]["target_id"] == goal.id


def test_gaps_are_computed_on_each_request(app, db_session, make_org):
    org = make_org("vp-gaps")
    driver = _element(db_session, org, "Market pressure", "Driver", description="Competitors cut prices")
    goal = _element(db_session, org, "Keep margin", "Goal")
    client = _signed_in(app, db_session, org)

    html = client.get("/architecture/viewpoints/motivation").get_data(as_text=True)
    assert 'data-gap="unconnected"' in html
    assert 'data-gap="no-description"' in html
    assert 'data-gap="missing-types"' in html

    _relate(db_session, org, driver, goal)
    goal.description = "Hold gross margin above 40 per cent"
    db_session.flush()
    _forget_cached_user()
    html = client.get("/architecture/viewpoints/motivation").get_data(as_text=True)
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


# --- review of 0505ae8ff: the gaps line, the layered bands, the timestamp -----------------------------

def test_any_type_is_never_reported_missing():
    from app.config.archimate_viewpoints import VIEWPOINTS
    from app.modules.architecture.routes.viewpoint_pages import missing_types

    layered = VIEWPOINTS["layered"]
    assert "*" in layered.allowed_elements
    assert missing_types(layered, {"BusinessActor"}) == []


def test_a_parent_type_satisfies_its_specialisation():
    from app.config.archimate_viewpoints import VIEWPOINTS
    from app.modules.architecture.routes.viewpoint_pages import missing_types

    realisation = VIEWPOINTS["requirements_realization"]
    assert "Application" in realisation.allowed_elements
    missing = missing_types(realisation, {"ApplicationComponent"})
    assert "Application" not in missing and "ApplicationComponent" not in missing


def test_a_specialisation_satisfies_its_parent():
    from app.config.archimate_viewpoints import VIEWPOINTS
    from app.modules.architecture.routes.viewpoint_pages import missing_types

    realisation = VIEWPOINTS["requirements_realization"]
    missing = missing_types(realisation, {"Application"})
    assert "ApplicationComponent" not in missing and "Application" not in missing


def test_a_missing_parent_is_named_once():
    from app.config.archimate_viewpoints import VIEWPOINTS
    from app.modules.architecture.routes.viewpoint_pages import missing_types

    missing = missing_types(VIEWPOINTS["requirements_realization"], set())
    assert "ApplicationComponent" in missing and "Application" not in missing


def test_the_empty_state_names_no_star():
    from app.config.archimate_viewpoints import VIEWPOINTS
    from app.modules.architecture.routes.viewpoint_pages import expected_types

    assert expected_types(VIEWPOINTS["layered"]) is None
    assert "*" not in (expected_types(VIEWPOINTS["motivation"]) or [])


def test_the_reading_time_is_written_for_people():
    from app.modules.architecture.routes.viewpoint_pages import format_as_of

    assert format_as_of("2026-11-04T10:12:33+00:00") == "4 Nov 2026, 10:12 UTC"
    assert format_as_of("2026-11-04T10:12:33Z") == "4 Nov 2026, 10:12 UTC"
    assert format_as_of(None) is None


def test_the_layered_diagram_is_drawn_in_bands(tmp_path):
    from pathlib import Path

    from app.modules.architecture.routes.viewpoint_pages import LAYER_BANDS

    assert [b["layer"] for b in LAYER_BANDS][:3] == ["motivation", "strategy", "business"]
    script = Path("app/static/js/archimate/viewpoint_page.js").read_text(encoding="utf8")
    assert "layout = 'bands'" in script and "opts.bands = model.bands" in script
