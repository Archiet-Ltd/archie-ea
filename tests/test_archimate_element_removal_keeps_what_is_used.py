"""Removing a model element never unlinks or breaks what a person built on it."""
import uuid

import pytest

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.models import Principle, RiskAssessment
from app.modules.applications.routes._helpers import _delete_mirror_archimate_element

pytestmark = pytest.mark.usefixtures("db_session")


def _owner(db_session, org):
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(
        email="owner.%s@example.test" % uuid.uuid4().hex[:8], first_name="Own", last_name="Er",
        organization_id=org.id, enterprise_role="solution_architect", confirmed=True,
    )
    user.role = Role.query.filter_by(name="Administrator").first() or Role.query.filter_by(name="Architect").one()
    user.password = "Sup3r-Secret!pass"
    db_session.add(user)
    db_session.flush()
    OrgRole.set_role(org.id, user.id, "org_admin", granted_by_id=user.id)
    db_session.flush()
    return user


def _element(org, name="Used element"):
    element = ArchiMateElement(name=name, type="Principle", layer="Motivation", organization_id=org.id)
    db.session.add(element)
    db.session.flush()
    return element


@pytest.fixture
def two(db_session, make_org):
    one, other = make_org("keep-one"), make_org("keep-two")
    return {"one": one, "two": other, "one_owner": _owner(db_session, one)}


def test_an_unreferenced_element_is_still_removed_with_its_relationships(two):
    a, b = _element(two["one"], "A"), _element(two["one"], "B")
    db.session.add(ArchiMateRelationship(source_id=a.id, target_id=b.id, type="Association", organization_id=two["one"].id))
    db.session.flush()
    outcome = _delete_mirror_archimate_element(a.id)
    assert outcome["errors"] == [] and outcome["elements_deleted"] == 1 and outcome["relationships_deleted"] == 1
    assert db.session.get(ArchiMateElement, a.id) is None and db.session.get(ArchiMateElement, b.id) is not None


def test_a_principle_linked_to_an_element_blocks_its_removal_and_keeps_the_link(two):
    element = _element(two["one"])
    principle = Principle(
        name="Cloud first", statement="Prefer cloud.", organization_id=two["one"].id, archimate_element_id=element.id
    )
    db.session.add(principle)
    db.session.flush()
    outcome = _delete_mirror_archimate_element(element.id)
    assert outcome["elements_deleted"] == 0
    assert outcome["errors"] and "principles" in outcome["errors"][0] and "kept" in outcome["errors"][0]
    db.session.expire_all()
    assert db.session.get(ArchiMateElement, element.id) is not None
    assert db.session.get(Principle, principle.id).archimate_element_id == element.id, "the link must survive"


def test_a_risk_assessment_linked_to_an_element_blocks_its_removal_and_keeps_the_link(two):
    element = _element(two["one"])
    risk = RiskAssessment(
        name="Vendor lock-in", description="Too tied to one vendor.", organization_id=two["one"].id,
        archimate_element_id=element.id,
    )
    db.session.add(risk)
    db.session.flush()
    outcome = _delete_mirror_archimate_element(element.id)
    assert outcome["elements_deleted"] == 0 and "risk assessments" in outcome["errors"][0]
    db.session.expire_all()
    assert db.session.get(RiskAssessment, risk.id).archimate_element_id == element.id


def test_a_list_keeps_only_the_used_elements_and_removes_the_rest(two):
    used, free = _element(two["one"], "Used"), _element(two["one"], "Free")
    db.session.add(Principle(name="P", statement="S", organization_id=two["one"].id, archimate_element_id=used.id))
    db.session.flush()
    outcome = _delete_mirror_archimate_element([used.id, free.id])
    assert outcome["elements_deleted"] == 1 and list(outcome["blocked"]) == [used.id]
    assert db.session.get(ArchiMateElement, free.id) is None and db.session.get(ArchiMateElement, used.id) is not None


def _application_with_principle(org):
    from app.models.application_portfolio import ApplicationComponent

    application = ApplicationComponent(name="Ledger %s" % uuid.uuid4().hex[:6], organization_id=org.id)
    db.session.add(application)
    db.session.flush()
    assert application.archimate_element_id, "an application gets its mirror element"
    principle = Principle(
        name="Keep ledger", statement="S", organization_id=org.id, archimate_element_id=application.archimate_element_id
    )
    db.session.add(principle)
    db.session.commit()
    return application, principle


def _url(app, endpoint, **values):
    with app.test_request_context():
        from flask import url_for

        return url_for(endpoint, **values)


@pytest.mark.parametrize(
    "endpoint,method,key",
    [
        ("unified_applications.application_delete", "post", "id"),
        ("unified_applications.api_delete_application", "delete", "app_id"),
    ],
)
def test_deleting_an_application_keeps_an_element_a_principle_uses(client, login_as, two, app, endpoint, method, key):
    application, principle = _application_with_principle(two["one"])
    element_id, principle_id = application.archimate_element_id, principle.id
    login_as(client, two["one_owner"])
    reply = getattr(client, method)(_url(app, endpoint, **{key: application.id}), json={})
    assert reply.status_code == 200, reply.get_data(as_text=True)
    body = reply.get_json()
    assert body["elements_deleted"] == 0 and "principles" in " ".join(body["errors"])
    assert "principles" in body["message"], "the person is told what refers to the element"
    db.session.expire_all()
    assert db.session.get(ArchiMateElement, element_id) is not None
    assert db.session.get(Principle, principle_id).archimate_element_id == element_id


def test_the_v1_api_delete_keeps_an_element_a_principle_uses(client, login_as, two, app):
    application, principle = _application_with_principle(two["one"])
    element_id, principle_id = application.archimate_element_id, principle.id
    login_as(client, two["one_owner"])
    reply = client.delete("/api/v1/applications/%s" % application.id)
    assert reply.status_code == 200, reply.get_data(as_text=True)
    text = reply.get_data(as_text=True)
    assert '"elements_deleted": 0' in text.replace(":0", ": 0") and "principles" in text
    db.session.expire_all()
    assert db.session.get(Principle, principle_id).archimate_element_id == element_id
    assert db.session.get(ArchiMateElement, element_id) is not None


def test_the_management_screen_delete_tells_the_person_what_refers_to_the_element(client, login_as, two, app):
    application, principle = _application_with_principle(two["one"])
    element_id, principle_id = application.archimate_element_id, principle.id
    login_as(client, two["one_owner"])
    reply = client.post(_url(app, "application_mgmt.application_delete", id=application.id), follow_redirects=True)
    assert reply.status_code == 200
    assert "principles" in reply.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(Principle, principle_id).archimate_element_id == element_id
    assert db.session.get(ArchiMateElement, element_id) is not None
