"""The model map reads the signed-in organisation's elements and relationships, and nobody else's.

Node and link counts equal what the model holds, each node names where it came from and when, and an
element with no recorded origin says it was entered by hand.
"""

import datetime as dt
import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _element(db_session, org, name, layer="Application", kind="ApplicationComponent", **cols):
    from app.models.archimate_core import ArchiMateElement

    el = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org.id, **cols)
    db_session.add(el)
    db_session.flush()
    return el


def _link(db_session, org, source, target, kind="serving"):
    from app.models.archimate_core import ArchiMateRelationship

    db_session.add(ArchiMateRelationship(source_id=source.id, target_id=target.id, type=kind,
                                         organization_id=org.id))
    db_session.flush()


def _seed_two_orgs(db_session, make_org):
    a, b = make_org("a"), make_org("b")
    api = _element(db_session, a, "Billing API", source_kind="connector", source_label="GitHub",
                   source_ref='{"system": "github", "repository": "acme/billing"}',
                   source_recorded_at=dt.datetime(2026, 11, 2, 9, 30))
    db = _element(db_session, a, "Ledger database", "Technology", "Node", source_kind="import",
                  source_label="landscape.xml", source_recorded_at=dt.datetime(2026, 10, 1))
    goal = _element(db_session, a, "Cut run cost", "Motivation", "Goal")
    _link(db_session, a, db, api)
    _link(db_session, a, goal, api, "influence")
    other = _element(db_session, b, "Other firm CRM")
    other2 = _element(db_session, b, "Other firm DB", "Technology", "Node")
    _link(db_session, b, other2, other)
    return a, b, (api, db, goal), (other, other2)


def test_counts_nodes_and_links_equal_the_model_and_exclude_another_organisation(db_session, make_org, tenant_ctx):
    from app.services.model_map import build_model_map

    a, b, mine, theirs = _seed_two_orgs(db_session, make_org)
    with tenant_ctx(a.id):
        data = build_model_map()
    assert data["mode"] == "elements"
    assert {n["id"] for n in data["nodes"]} == {e.id for e in mine}
    assert len(data["nodes"]) == data["total"] == 3
    assert len(data["edges"]) == 2
    assert not {n["id"] for n in data["nodes"]} & {e.id for e in theirs}
    per_layer = {c["layer"]: c["count"] for c in data["counts"]}
    assert per_layer["application"] == 1 and per_layer["technology"] == 1 and per_layer["motivation"] == 1
    assert sum(per_layer.values()) == 3

    with tenant_ctx(b.id):
        other = build_model_map()
    assert {n["id"] for n in other["nodes"]} == {e.id for e in theirs}
    assert len(other["edges"]) == 1


def test_each_node_names_its_source_and_date(db_session, make_org, tenant_ctx):
    from app.services.model_map import build_model_map

    a, _b, (api, db, goal), _ = _seed_two_orgs(db_session, make_org)
    db_session.refresh(goal)
    with tenant_ctx(a.id):
        nodes = {n["id"]: n for n in build_model_map()["nodes"]}
    assert "GitHub: acme/billing" in nodes[api.id]["source"]
    assert nodes[api.id]["as_of"] == "2 Nov 2026"
    assert "landscape.xml" in nodes[db.id]["source"] and nodes[db.id]["as_of"] == "1 Oct 2026"
    # no recorded origin: said plainly as entered by hand, dated by when the model recorded it
    assert nodes[goal.id]["source"].startswith("Entered by the team")
    assert nodes[goal.id]["as_of"] == "%d %s %d" % (goal.recorded_at.day, goal.recorded_at.strftime("%b"),
                                                      goal.recorded_at.year)
    for node in nodes.values():
        assert node["href"].endswith("/%d?ae=1" % node["id"])


def test_one_layer_returns_only_that_layer_and_other_collects_the_unclassified(db_session, make_org, tenant_ctx):
    from app.services.model_map import build_model_map

    a, _b, (api, db, goal), _ = _seed_two_orgs(db_session, make_org)
    odd = _element(db_session, a, "Somewhere", "", "Location")
    with tenant_ctx(a.id):
        tech = build_model_map("technology")
        other = build_model_map("other")
    assert [n["id"] for n in tech["nodes"]] == [db.id]
    assert tech["edges"] == []  # the link leaves the layer, so it is not drawn
    assert [n["id"] for n in other["nodes"]] == [odd.id]


def test_over_the_node_limit_the_map_is_one_node_per_layer_with_counts(db_session, make_org, tenant_ctx, monkeypatch):
    from app.services import model_map

    a, _b, _mine, _ = _seed_two_orgs(db_session, make_org)
    monkeypatch.setattr(model_map, "MAX_NODES", 2)
    with tenant_ctx(a.id):
        clusters = model_map.build_model_map()
        one_layer = model_map.build_model_map("application")
    assert clusters["mode"] == "clusters"
    assert {n["layer"]: n["count"] for n in clusters["nodes"]} == {"application": 1, "technology": 1, "motivation": 1}
    assert all(n["cluster"] for n in clusters["nodes"])
    assert one_layer["mode"] == "elements" and len(one_layer["nodes"]) == 1


def test_empty_organisation_has_no_nodes_and_zero_counts(db_session, make_org, tenant_ctx):
    from app.services.model_map import build_model_map

    empty = make_org("empty")
    with tenant_ctx(empty.id):
        data = build_model_map()
    assert data["total"] == 0 and data["nodes"] == [] and data["edges"] == []


def _signed_in(app, db_session, login_as, org):
    from app.models import Role, User
    from app.models.user import Permission

    role = Role.query.filter_by(name="Administrator").first()
    if role is None:
        role = Role(name="Administrator", permissions=Permission.ADMINISTER)
        db_session.add(role)
        db_session.flush()
    user = User(email="map-%s@example.com" % uuid.uuid4().hex[:8], first_name="Map", last_name="Viewer",
                organization_id=org.id, role=role, confirmed=True)
    db_session.add(user)
    db_session.flush()
    client = app.test_client()
    login_as(client, user)
    return client


def test_api_and_page_answer_for_the_signed_in_organisation_only(app, db_session, make_org, login_as):
    a, _b, mine, theirs = _seed_two_orgs(db_session, make_org)
    client = _signed_in(app, db_session, login_as, a)
    body = client.get("/archimate/api/model-map").get_json()
    assert {n["id"] for n in body["nodes"]} == {e.id for e in mine}
    page = client.get("/archimate/model-map")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'data-mode="model"' in html and "Model map" in html
    assert 'data-testid="model-map-count-application"' in html
    assert "Other firm CRM" not in html


def test_empty_organisation_page_explains_and_links_to_connect(app, db_session, make_org, login_as):
    client = _signed_in(app, db_session, login_as, make_org("empty"))
    html = client.get("/archimate/model-map").get_data(as_text=True)
    assert 'data-testid="model-map-empty"' in html
    assert "Your model is empty" in html
    assert 'data-testid="model-map-connect-github"' in html
    assert 'id="impact-svg"' not in html


def test_the_impact_page_is_still_the_impact_view(app, db_session, make_org, login_as):
    a, _b, (api, _db, _goal), _ = _seed_two_orgs(db_session, make_org)
    client = _signed_in(app, db_session, login_as, a)
    html = client.get("/archimate/elements/%d/impact" % api.id).get_data(as_text=True)
    assert 'data-element-id="%d"' % api.id in html and 'data-mode="model"' not in html
    assert "Impact graph" in html


def _github_page(monkeypatch, client, counts):
    from app.modules.admin import connector_routes

    monkeypatch.setattr(connector_routes, "_github_state", lambda org_id: {
        "state": "done", "organization": "acme", "has_token": True, "job": None,
        "counts": {"repositories": 3, "pending": 0, **counts}})
    return client.get("/admin/connectors/github").get_data(as_text=True)


def test_the_github_screen_offers_see_your_model_once_a_model_is_built(app, db_session, make_org, login_as, monkeypatch):
    client = _signed_in(app, db_session, login_as, make_org("gh"))
    built = _github_page(monkeypatch, client, {"applications": 3, "components": 4, "relationships": 2})
    link = re.search(r'<a href="([^"]+)" id="github-see-model"[^>]*class="([^"]*)"', built)
    assert link and link.group(1) == "/archimate/model-map" and " hidden" not in link.group(2)
    assert "See your model" in built
    nothing = _github_page(monkeypatch, client, {"applications": 0, "components": 0, "relationships": 0})
    hidden = re.search(r'id="github-see-model"[^>]*class="([^"]*)"', nothing)
    assert hidden and " hidden" in hidden.group(1)


def test_the_element_list_toolbar_links_to_the_model_map(app, db_session, make_org, login_as):
    client = _signed_in(app, db_session, login_as, make_org("list"))
    html = client.get("/architecture/elements").get_data(as_text=True)
    assert 'data-testid="open-model-map"' in html
    assert "/archimate/model-map" in html
