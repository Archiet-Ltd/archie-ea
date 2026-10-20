"""Scenario branches: what-if changes laid over the model without altering it."""

from __future__ import annotations

import uuid

# Fixtures (app, db_session, make_org, client, login_as) come from tests.conftest.

BASE = "/api/v1/intelligence"


def _user(db_session, org_id, role="enterprise_architect"):
    from app.models.user import User

    user = User(
        email=f"scn-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Scen",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org_id, name, type_="ApplicationComponent", layer="application"):
    from app.models import ArchiMateElement

    el = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org_id)
    db_session.add(el)
    db_session.flush()
    return el


def _relationship(db_session, org_id, source, target, type_="Serving"):
    from app.models import ArchiMateRelationship

    rel = ArchiMateRelationship(source_id=source.id, target_id=target.id, type=type_, organization_id=org_id)
    db_session.add(rel)
    db_session.flush()
    return rel


def _world(db_session, make_org):
    """A -> B -> C in one organisation, with an editor signed in as that organisation."""
    org = make_org(f"scn-{uuid.uuid4().hex[:6]}")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "A")
    b = _element(db_session, org.id, "B")
    c = _element(db_session, org.id, "C")
    ab = _relationship(db_session, org.id, a, b)
    bc = _relationship(db_session, org.id, b, c)
    db_session.commit()
    return org, user, a, b, c, ab, bc


def _create(client, name="Retire B"):
    resp = client.post(f"{BASE}/scenarios", json={"name": name})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["data"]["id"]


def _impact(client, element_id, scenario_id=None, **extra):
    query = {"direction": "downstream", **extra}
    if scenario_id is not None:
        query["scenario_id"] = scenario_id
    resp = client.get(f"{BASE}/impact/{element_id}", query_string=query)
    return resp


def _ids(resp):
    return {r["element_id"] for r in resp.get_json()["data"]["rows"]}


def test_a_branch_that_removes_a_relationship_shortens_the_impact_and_the_model_is_unchanged(
    app, db_session, make_org, client, login_as
):
    from app.models import ArchiMateRelationship

    org, user, a, b, c, ab, bc = _world(db_session, make_org)
    login_as(client, user)
    sid = _create(client)
    resp = client.post(
        f"{BASE}/scenarios/{sid}/changes",
        json={"change_kind": "remove", "target_kind": "relationship", "target_id": bc.id},
    )
    assert resp.status_code == 201, resp.get_json()

    login_as(client, user)
    assert _ids(_impact(client, a.id)) == {b.id, c.id}
    with_branch = _impact(client, a.id, sid)
    assert with_branch.status_code == 200
    assert _ids(with_branch) == {b.id}
    data = with_branch.get_json()["data"]
    assert data["summary"]["scenario_id"] == sid
    assert "derivation_not_applied_to_scenario" in data["reasons"]

    db_session.expire_all()
    assert ArchiMateRelationship.query.filter_by(id=bc.id).first() is not None


def test_a_branch_that_adds_an_element_and_a_link_extends_the_impact(
    app, db_session, make_org, client, login_as
):
    org, user, a, b, c, ab, bc = _world(db_session, make_org)
    login_as(client, user)
    sid = _create(client, "Add D")
    added = client.post(
        f"{BASE}/scenarios/{sid}/changes",
        json={
            "change_kind": "add",
            "target_kind": "element",
            "payload": {"name": "New D", "type": "ApplicationComponent"},
        },
    )
    assert added.status_code == 201, added.get_json()
    pseudo = added.get_json()["data"]["added_id"]
    assert pseudo < 0

    login_as(client, user)
    link = client.post(
        f"{BASE}/scenarios/{sid}/changes",
        json={
            "change_kind": "add",
            "target_kind": "relationship",
            "payload": {"source_id": c.id, "target_id": pseudo, "type": "serving"},
        },
    )
    assert link.status_code == 201, link.get_json()

    login_as(client, user)
    resp = _impact(client, a.id, sid)
    data = resp.get_json()["data"]
    assert {b.id, c.id, pseudo} == {r["element_id"] for r in data["rows"]}
    assert data["elements"][str(pseudo)]["name"] == "New D"
    # The base answer does not see it.
    assert pseudo not in _ids(_impact(client, a.id))


def test_a_branch_that_removes_an_element_hides_it_and_what_only_it_reached(
    app, db_session, make_org, client, login_as
):
    org, user, a, b, c, ab, bc = _world(db_session, make_org)
    login_as(client, user)
    sid = _create(client, "Drop B")
    resp = client.post(
        f"{BASE}/scenarios/{sid}/changes",
        json={"change_kind": "remove", "target_kind": "element", "target_id": b.id},
    )
    assert resp.status_code == 201, resp.get_json()
    login_as(client, user)
    assert _ids(_impact(client, a.id, sid)) == set()


def test_a_rename_on_a_branch_shows_in_the_branch_answer_only(app, db_session, make_org, client, login_as):
    org, user, a, b, c, ab, bc = _world(db_session, make_org)
    login_as(client, user)
    sid = _create(client, "Rename B")
    resp = client.post(
        f"{BASE}/scenarios/{sid}/changes",
        json={"change_kind": "change", "target_kind": "element", "target_id": b.id, "payload": {"name": "B renamed"}},
    )
    assert resp.status_code == 201, resp.get_json()
    login_as(client, user)
    assert _impact(client, a.id, sid).get_json()["data"]["elements"][str(b.id)]["name"] == "B renamed"
    assert _impact(client, a.id).get_json()["data"]["elements"][str(b.id)]["name"] == "B"


def test_another_organisations_branch_is_not_found_everywhere(app, db_session, make_org, client, login_as):
    org_a, user_a, a, *_ = _world(db_session, make_org)
    org_b, user_b, *_rest = _world(db_session, make_org)
    login_as(client, user_b)
    foreign = _create(client, "Theirs")

    login_as(client, user_a)
    assert client.get(f"{BASE}/scenarios/{foreign}").status_code == 404
    assert client.get(f"{BASE}/scenarios").get_json()["data"]["scenarios"] == []
    resp = _impact(client, a.id, foreign)
    assert resp.status_code == 404
    assert resp.get_json()["error"]["details"]["reason"] == "scenario_not_found"
    assert client.patch(f"{BASE}/scenarios/{foreign}", json={"name": "Mine now"}).status_code == 404
    login_as(client, user_a)
    assert client.delete(f"{BASE}/scenarios/{foreign}").status_code == 404
    assert (
        client.post(
            f"{BASE}/scenarios/{foreign}/changes",
            json={"change_kind": "add", "target_kind": "element", "payload": {"name": "x", "type": "Node"}},
        ).status_code
        == 404
    )

    login_as(client, user_b)
    assert client.get(f"{BASE}/scenarios/{foreign}").status_code == 200


def test_a_change_cannot_name_another_organisations_element(app, db_session, make_org, client, login_as):
    org_a, user_a, a, b, c, ab, bc = _world(db_session, make_org)
    org_b, user_b, a2, b2, c2, ab2, bc2 = _world(db_session, make_org)
    login_as(client, user_a)
    sid = _create(client)
    for body in (
        {"change_kind": "remove", "target_kind": "element", "target_id": b2.id},
        {"change_kind": "remove", "target_kind": "relationship", "target_id": bc2.id},
        {
            "change_kind": "add",
            "target_kind": "relationship",
            "payload": {"source_id": a.id, "target_id": c2.id, "type": "serving"},
        },
    ):
        login_as(client, user_a)
        assert client.post(f"{BASE}/scenarios/{sid}/changes", json=body).status_code in (400, 404)
    login_as(client, user_a)
    assert client.get(f"{BASE}/scenarios/{sid}").get_json()["data"]["changes"] == []


def test_a_read_only_viewer_cannot_write_but_can_read(app, db_session, make_org, client, login_as):
    from app.models.user import Role

    org, user, a, b, c, ab, bc = _world(db_session, make_org)
    login_as(client, user)
    sid = _create(client, "Editors only")

    user.role = Role.query.filter_by(name="Viewer").first()
    db_session.commit()
    login_as(client, user)
    assert client.post(f"{BASE}/scenarios", json={"name": "Nope"}).status_code == 403
    login_as(client, user)
    assert client.patch(f"{BASE}/scenarios/{sid}", json={"name": "Nope"}).status_code == 403
    login_as(client, user)
    assert (
        client.post(
            f"{BASE}/scenarios/{sid}/changes",
            json={"change_kind": "remove", "target_kind": "relationship", "target_id": ab.id},
        ).status_code
        == 403
    )
    login_as(client, user)
    assert client.delete(f"{BASE}/scenarios/{sid}").status_code == 403
    login_as(client, user)
    assert client.get(f"{BASE}/scenarios/{sid}").status_code == 200
    assert _impact(client, a.id, sid).status_code == 200


def test_names_are_unique_per_organisation_and_validated(app, db_session, make_org, client, login_as):
    org, user, *_ = _world(db_session, make_org)
    login_as(client, user)
    _create(client, "Same")
    login_as(client, user)
    assert client.post(f"{BASE}/scenarios", json={"name": "Same"}).status_code == 409
    login_as(client, user)
    assert client.post(f"{BASE}/scenarios", json={"name": "  "}).status_code == 400


def test_only_the_latest_change_can_be_taken_back_and_delete_removes_the_branch(
    app, db_session, make_org, client, login_as
):
    org, user, a, b, c, ab, bc = _world(db_session, make_org)
    login_as(client, user)
    sid = _create(client)
    ids = []
    for rel in (ab, bc):
        login_as(client, user)
        resp = client.post(
            f"{BASE}/scenarios/{sid}/changes",
            json={"change_kind": "remove", "target_kind": "relationship", "target_id": rel.id},
        )
        ids.append(resp.get_json()["data"]["id"])
    login_as(client, user)
    assert client.delete(f"{BASE}/scenarios/{sid}/changes/{ids[0]}").status_code == 409
    login_as(client, user)
    assert client.delete(f"{BASE}/scenarios/{sid}/changes/{ids[1]}").status_code == 200
    login_as(client, user)
    assert client.delete(f"{BASE}/scenarios/{sid}").status_code == 200
    login_as(client, user)
    assert client.get(f"{BASE}/scenarios/{sid}").status_code == 404


def test_an_unknown_scenario_id_on_the_service_is_answered_not_found(app, db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    org, user, a, *_ = _world(db_session, make_org)
    with tenant_ctx(org.id):
        out = IntelligenceQueryService.cross_layer_impact(a.id, scenario_id=999999)
    assert out["rows"] == []
    assert out["reasons"] == ["scenario_not_found"]
