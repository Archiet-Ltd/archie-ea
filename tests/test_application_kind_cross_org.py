"""One organisation's composition never makes another organisation's record a part of an application,
and the kind control refuses another organisation's record with a 404 and a viewer with a 403."""
import uuid

import pytest

from app import db
from app.models import ArchiMateRelationship
from app.models.application_portfolio import ApplicationComponent
from app.models.user import Role, User
from app.services.application_kind import is_application_expression, kind_of


def _person(org, role_name, enterprise_role):
    person = User(email="kind.%s@example.com" % uuid.uuid4().hex[:10], first_name="Kim", last_name=role_name,
                  organization_id=org.id, confirmed=True, enterprise_role=enterprise_role)
    person.role = Role.query.filter_by(name=role_name).one()
    person.password = "Correct-Horse-9"
    db.session.add(person)
    db.session.flush()
    return person


def _application(org, name, **fields):
    row = ApplicationComponent(name=name, organization_id=org.id, **fields)
    db.session.add(row)
    db.session.flush()
    return row


@pytest.fixture
def two_orgs(db_session, make_org):
    Role.insert_roles()
    mine, theirs = make_org("kind-a"), make_org("kind-b")
    out = {
        "mine": mine,
        "theirs": theirs,
        "architect": _person(mine, "Architect", "enterprise_architect"),
        "viewer": _person(mine, "Viewer", "non_technical_owner"),
        "whole": _application(mine, "Alerts gateway and consumers"),
        "part": _application(mine, "Alerts Gateway"),
        "their_app": _application(theirs, "Billing"),
        "their_other": _application(theirs, "Billing ledger"),
    }
    db_session.add(ArchiMateRelationship(
        type="composition", source_id=out["whole"].archimate_element_id, target_id=out["part"].archimate_element_id,
        organization_id=mine.id))
    # a relationship recorded in the first organisation that points at the second organisation's record
    db_session.add(ArchiMateRelationship(
        type="composition", source_id=out["whole"].archimate_element_id, target_id=out["their_app"].archimate_element_id,
        organization_id=mine.id))
    db_session.flush()
    return out


def _kinds(org_id):
    from flask import g

    g.current_org_id = org_id
    rows = db.session.execute(
        db.select(ApplicationComponent.name, is_application_expression(ApplicationComponent))
        .where(ApplicationComponent.organization_id == org_id)
    ).all()
    return {name: is_app for name, is_app in rows}


def test_a_composition_in_one_organisation_does_not_make_the_others_record_a_part(two_orgs, tenant_ctx):
    with tenant_ctx(two_orgs["mine"].id):
        assert _kinds(two_orgs["mine"].id) == {"Alerts gateway and consumers": True, "Alerts Gateway": False}
    with tenant_ctx(two_orgs["theirs"].id):
        assert _kinds(two_orgs["theirs"].id) == {"Billing": True, "Billing ledger": True}
        assert kind_of(two_orgs["their_app"])["kind"] == "application"


def test_the_kind_control_refuses_another_organisations_record(two_orgs, client, login_as):
    login_as(client, two_orgs["architect"])
    answer = client.post("/applications/%d/kind" % two_orgs["their_app"].id, data={"application_kind": "not_software"})
    assert answer.status_code == 404
    assert ApplicationComponent.query.get(two_orgs["their_app"].id).application_kind is None


def test_an_architect_changes_the_kind_and_a_viewer_is_refused(two_orgs, client, login_as):
    login_as(client, two_orgs["viewer"])
    answer = client.post("/applications/%d/kind" % two_orgs["whole"].id, data={"application_kind": "not_software"})
    assert answer.status_code == 403
    assert "does not have write access" in answer.get_data(as_text=True) or "may read" in answer.get_data(as_text=True)
    assert ApplicationComponent.query.get(two_orgs["whole"].id).application_kind is None

    login_as(client, two_orgs["architect"])
    answer = client.post("/applications/%d/kind" % two_orgs["whole"].id, data={"application_kind": "not_software"})
    assert answer.status_code == 302
    saved = ApplicationComponent.query.get(two_orgs["whole"].id)
    assert saved.application_kind == "not_software" and saved.application_kind_set_by_id == two_orgs["architect"].id
    assert kind_of(saved)["reason"].startswith("Set by Kim Architect on ")
