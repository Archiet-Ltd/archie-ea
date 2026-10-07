"""The Composer's relationship rules and what a saved view gives back.

* Serving, flow and triggering between active structure elements of one layer
  are allowed (ArchiMate 3.2 Appendix B); realization of an active element
  never is.
* A refused relationship answers 400 with the types that are allowed.
* A saved view returns each relationship's flow label and access mode, so the
  reopened diagram shows what was drawn.
"""

import uuid

import pytest

from app.services.archimate_validity_service import ArchimateValidityService


@pytest.mark.parametrize("source,target", [
    ("ApplicationComponent", "ApplicationComponent"),
    ("ApplicationInterface", "ApplicationComponent"),
    ("Node", "Node"),
])
@pytest.mark.parametrize("rel_type", ["serving", "flow", "triggering"])
def test_dynamic_relationships_between_active_elements_are_valid(source, target, rel_type):
    assert ArchimateValidityService().is_valid(source, target, rel_type)


def test_an_active_element_is_never_realised():
    svc = ArchimateValidityService()
    assert not svc.is_valid("ApplicationComponent", "ApplicationInterface", "realization")
    assert not svc.is_valid("ApplicationComponent", "ApplicationComponent", "access")


def _element(db_session, org_id, kind, name):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(name="%s %s" % (name, uuid.uuid4().hex[:6]), type=kind,
                           layer="application", organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _user(db_session, org_id):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(email="composer-%s@example.com" % uuid.uuid4().hex[:10], first_name="C",
                last_name="A", organization_id=org_id, confirmed=True,
                enterprise_role="solution_architect")
    user.role = Role.query.filter_by(name="Architect").first()
    db_session.add(user)
    db_session.flush()
    return user


def test_refused_relationship_names_the_allowed_types(app, db_session, make_org, client, login_as):
    org = make_org("composer-refusal")
    user = _user(db_session, org.id)
    component = _element(db_session, org.id, "ApplicationComponent", "Ordering")
    interface = _element(db_session, org.id, "ApplicationInterface", "Payments API")
    login_as(client, user)

    resp = client.post("/archimate/api/relationships", json={
        "source_element_id": component.id, "target_element_id": interface.id,
        "relationship_type": "realization",
    })
    assert resp.status_code == 400
    body = resp.get_json()
    assert "does not allow realization" in body["error"]
    assert "realization" not in body["valid_types"]
    assert {"serving", "flow", "composition"} <= set(body["valid_types"])

    resp = client.post("/archimate/api/relationships", json={
        "source_element_id": component.id, "target_element_id": interface.id,
        "relationship_type": "flow", "flow_label": "Order data",
    })
    assert resp.status_code == 201, resp.get_data(as_text=True)


def test_saved_view_returns_the_flow_label(app, db_session, make_org, client, login_as):
    from app.models.archimate_core import ArchiMateRelationship, SavedDiagram, SavedDiagramElement

    org = make_org("composer-saved-view")
    user = _user(db_session, org.id)
    first = _element(db_session, org.id, "ApplicationComponent", "Ordering")
    second = _element(db_session, org.id, "ApplicationComponent", "Billing")
    db_session.add(ArchiMateRelationship(source_id=first.id, target_id=second.id, type="flow",
                                         flow_label="Order data", organization_id=org.id))
    view = SavedDiagram(name="Landscape %s" % uuid.uuid4().hex[:6], organization_id=org.id)
    db_session.add(view)
    db_session.flush()
    for element in (first, second):
        db_session.add(SavedDiagramElement(diagram_id=view.id, element_id=element.id))
    db_session.flush()
    login_as(client, user)

    resp = client.get("/archimate/api/saved-viewpoints/%d" % view.id)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    rels = resp.get_json()["relationships"]
    assert [(r["type"], r["flow_label"]) for r in rels] == [("flow", "Order data")]
