"""GET /api/v1/elements/{id} must return the facts a reader needs, not just
the 8 bare identity fields it answered before this change: the element's
custom properties map, provenance (source_kind, source_ref), owner(s) and
status. REQ-ENT-API-FIELDS-01.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org):
    from app.models.user import User

    user = User(
        email=f"elt-{uuid.uuid4().hex[:8]}@example.test",
        first_name="Elliot",
        last_name="Detail",
        organization_id=org.id,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _seeded_element(db_session, org):
    """An ArchiMateElement backing an ApplicationComponent, with custom
    properties, provenance and a primary owner recorded -- the shape the
    Chrome-extension Guard reads company-profile facts out of."""
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement

    owner_user = _user(db_session, org)

    component = ApplicationComponent(
        name=f"Guarded App {uuid.uuid4().hex[:6]}", organization_id=org.id,
    )
    db_session.add(component)
    db_session.flush()

    element = ArchiMateElement(
        name=component.name,
        type="ApplicationComponent",
        layer="application",
        organization_id=org.id,
        application_component_id=component.id,
        status="Approved",
        source_kind="connector",
        source_ref="guard-sync-run-7",
        custom_properties={
            "tags": {
                "registered_address": "1 Example Street",
                "registration_number": "EX-00042",
            }
        },
    )
    db_session.add(element)
    db_session.flush()

    owner = ApplicationOwner(
        application_id=component.id,
        user_id=owner_user.id,
        ownership_type="primary",
        organization_id=org.id,
    )
    db_session.add(owner)
    db_session.flush()

    return element, owner_user


def test_get_element_includes_properties_source_owner_and_status(
    db_session, make_org, client, login_as,
):
    """The real requirement: a reader of GET /api/v1/elements/{id} gets the
    element's custom properties, provenance and recorded owner/status back,
    not just its bare identity fields."""
    org = make_org("guard")
    element, owner_user = _seeded_element(db_session, org)
    element_id = element.id
    owner_user_id = owner_user.id
    db_session.commit()
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, owner_user_id))

    resp = client.get(f"/api/v1/elements/{element_id}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    data = body.get("data", body)

    assert data["properties"] == {
        "tags": {
            "registered_address": "1 Example Street",
            "registration_number": "EX-00042",
        }
    }
    assert data["source_kind"] == "connector"
    assert data["source_ref"] == "guard-sync-run-7"
    assert data["status"] == "Approved"

    assert len(data["owners"]) == 1
    owner_row = data["owners"][0]
    assert owner_row["user_id"] == owner_user_id
    assert owner_row["ownership_type"] == "primary"


def test_get_element_without_owner_or_properties_returns_empty_not_fabricated(
    db_session, make_org, client, login_as,
):
    """An element nobody has tagged or assigned an owner to must come back
    with an empty map and an empty owner list -- never an invented value."""
    from app.models.archimate_core import ArchiMateElement

    org = make_org("guard-empty")
    user = _user(db_session, org)
    element = ArchiMateElement(
        name=f"Bare Element {uuid.uuid4().hex[:6]}",
        type="BusinessActor",
        layer="business",
        organization_id=org.id,
    )
    db_session.add(element)
    db_session.flush()
    element_id = element.id
    user_id = user.id
    db_session.commit()
    db_session.expunge_all()

    from app.models.user import User

    login_as(client, db_session.get(User, user_id))

    resp = client.get(f"/api/v1/elements/{element_id}")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    data = body.get("data", body)

    assert data["properties"] == {}
    assert data["source_kind"] is None
    assert data["source_ref"] is None
    assert data["status"] is None
    assert data["owners"] == []
