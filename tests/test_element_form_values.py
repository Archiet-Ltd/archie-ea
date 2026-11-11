"""The element form keeps every value it offers: on create, on rename and when it is reopened.

Eight fields the form offers have no column on their element's record (an actor type on a role, a protocol
on an application service, a domain on a business function). Their values are kept in the element's
custom_properties under the field's own key, read back into the form, and shown on the element's page with the field's label.
An empty date, number or choice is stored as NULL, so any element can be renamed with its optional fields
empty, and a stored date reaches its box as 2027-03-15.
"""
from __future__ import annotations

import json
import re
import uuid

import pytest

from tests.test_archimate_typed_field_create import _login, _make_user

from app.modules.architecture.routes.archimate_crud import routes as crud
from app.modules.architecture.routes.archimate_crud.services.field_configs import ELEMENT_TYPE_CONFIGS

CONFIGS = ELEMENT_TYPE_CONFIGS

# The fields the form offers on a record that has no column for them. Seven fields that once lived here now
# have a column of their own (a node's data centre, a deliverable's delivery status, ...), so they are saved there.
KEPT_IN_PROPERTIES = {
    ("Outcome", "outcome_type"), ("BusinessRole", "actor_type"), ("BusinessProcess", "apqc_code"),
    ("BusinessFunction", "domain"), ("BusinessObject", "lifecycle_state"), ("ApplicationService", "protocol"),
    ("ApplicationService", "sla_tier"), ("Plateau", "plateau_type"),
}


@pytest.fixture
def client(app):
    return app.test_client()


def _layer(element_type):
    return crud.ELEMENT_TYPE_TO_LAYER[element_type]


def _offered(element_type):
    return [f for f in CONFIGS[element_type].fields if f.name != "architecture_state"]


def _optional(element_type):
    """The offered fields a person may leave empty: not required, and not a choice that always has a value."""
    return [f for f in _offered(element_type) if not f.required and f.allow_empty and f.field_type != "capability"]


def _text(value):
    """A value as its box holds it; a number reads the same however many decimals the column keeps."""
    try:
        return str(float(value))
    except (TypeError, ValueError):
        return str(value)


def _shown(target, element_type):
    """The form's values as text, the way a box holds them."""
    return {f.name: _text(target[f.name]) for f in _offered(element_type)}


def _value_for(field, capability_id=None):
    if field.field_type == "capability":
        return str(capability_id)
    if field.name == "bandwidth_gbps":
        return "10"
    if field.name.endswith("date"):
        return "2027-03-15"
    if field.field_type == "select":
        return field.options[-1].value
    return "Value for %s" % field.name


def _create(client, element_type, name, values, capability_id=None):
    body = {"name": name, "description": ""}
    body.update(values)
    if capability_id:
        body["capability_id"] = capability_id
    resp = client.post("/architecture/%s/%s/new" % (_layer(element_type), element_type), json=body)
    assert resp.status_code == 200, (element_type, resp.get_data(as_text=True))
    return resp.get_json()["id"]


def _edit_target(client, element_type, element_id):
    resp = client.get("/architecture/%s/%s/%s/edit" % (_layer(element_type), element_type, element_id))
    assert resp.status_code == 200, (element_type, resp.status_code)
    found = re.search(r"editingElement = (\{.*?\});\n", resp.get_data(as_text=True), re.S)
    assert found, element_type
    return json.loads(found.group(1))


def _rename(client, element_type, element_id, name, form):
    """What the form posts: the name, the description and every field it shows."""
    body = {"name": name, "description": ""}
    body.update(form)
    return client.post(
        "/architecture/%s/%s/%s/edit" % (_layer(element_type), element_type, element_id), json=body
    )


def _signed_in(client, db_session, make_org, label):
    org = make_org(label)
    user = _make_user(db_session, org.id, label.title().replace("-", ""))
    _login(client, user.id)
    return org


def _capability(db_session, org):
    from app.models.unified_capability import UnifiedCapability

    cap = UnifiedCapability(
        name="Parent capability %s" % uuid.uuid4().hex[:6], code="CAP-%s" % uuid.uuid4().hex[:8],
        level=1, scope="tenant", organization_id=org.id,
    )
    db_session.add(cap)
    db_session.flush()
    return cap.id


def _make(db_session, client, org, element_type, name, values):
    """A stored element, created from the form the way a person does."""
    if element_type == "Principle":
        values = dict(values, statement="Statement")
    capability = _capability(db_session, org) if element_type == "BusinessFunction" else None
    return _create(client, element_type, name, values, capability_id=capability)


@pytest.mark.parametrize("element_type", sorted(CONFIGS))
def test_every_type_renames_with_all_optional_fields_empty_and_with_each_set(
    app, db_session, client, make_org, element_type
):
    org = _signed_in(client, db_session, make_org, "form-values")
    element_id = _make(db_session, client, org, element_type, "Before %s" % uuid.uuid4().hex[:6], {})

    capability = _capability(db_session, org) if element_type == "BusinessFunction" else None
    values = {f.name: _value_for(f, capability) for f in _offered(element_type)}
    optional = {f.name for f in _optional(element_type)}

    # Rename with every optional field empty, as the form sends them. A field the record must have is sent as it is.
    empty = {name: ("" if name in optional else value) for name, value in values.items()}
    resp = _rename(client, element_type, element_id, "After empty", empty)
    assert resp.status_code == 200, (element_type, resp.get_data(as_text=True))
    target = _edit_target(client, element_type, element_id)
    assert target["name"] == "After empty"
    assert all(target[name] == "" for name in optional), (element_type, target)

    # Rename with each field set: every value is stored and comes back to the form.
    resp = _rename(client, element_type, element_id, "After set", values)
    assert resp.status_code == 200, (element_type, resp.get_data(as_text=True))
    target = _edit_target(client, element_type, element_id)
    assert _shown(target, element_type) == {name: _text(value) for name, value in values.items()}, element_type

    # A rename that changes only the name sends the stored values back and keeps them.
    resp = _rename(client, element_type, element_id, "After third",
                    {f.name: target[f.name] for f in _offered(element_type)})
    assert resp.status_code == 200
    assert _shown(_edit_target(client, element_type, element_id), element_type) == {
        name: _text(value) for name, value in values.items()}


def test_fifteen_fields_without_a_column_are_kept_and_shown_on_the_page(app, db_session, client, make_org):
    org = _signed_in(client, db_session, make_org, "form-fifteen")
    from app.models.archimate_core import ArchiMateElement

    offered_without_column = {
        (et, f.name)
        for et in CONFIGS
        for f in _offered(et)
        if not hasattr(crud.MODEL_REGISTRY[et], f.name)
    }
    assert offered_without_column == KEPT_IN_PROPERTIES

    by_type = {}
    for element_type, name in sorted(KEPT_IN_PROPERTIES):
        by_type.setdefault(element_type, []).append(name)

    for element_type, names in by_type.items():
        fields = {f.name: f for f in _offered(element_type)}
        values = {n: _value_for(fields[n]) for n in names}
        element_id = _make(db_session, client, org, element_type, "Kept %s" % element_type, values)
        # A field the record must have is sent with its value on every save below.
        capability = _capability(db_session, org) if element_type == "BusinessFunction" else None
        must = {f.name: _value_for(f, capability) for f in _offered(element_type) if f not in _optional(element_type)}

        # Created with the value: it is stored on the model-graph row under the field's key.
        record = crud.MODEL_REGISTRY[element_type].query.filter_by(id=element_id).first()
        row = ArchiMateElement.query.filter_by(id=record.archimate_element_id).first()
        for key, value in values.items():
            assert row.custom_properties[key] == value, (element_type, key)

        # Renamed, then reopened: the form shows it.
        resp = _rename(client, element_type, element_id, "Renamed %s" % element_type,
                       {**{f.name: values.get(f.name, "") for f in _offered(element_type)}, **must})
        assert resp.status_code == 200, (element_type, resp.get_data(as_text=True))
        target = _edit_target(client, element_type, element_id)
        assert {n: str(target[n]) for n in names} == values, element_type

        # The page says each value under the field's label.
        page = client.get("/architecture/%s/%s/%s" % (_layer(element_type), element_type, element_id))
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        for key in names:
            field = fields[key]
            labels = {o.value: o.label for o in field.options}
            assert field.label in html, (element_type, field.label)
            assert labels.get(values[key], values[key]) in html, (element_type, key)

        # Emptying a field removes the stored value.
        _rename(client, element_type, element_id, "Cleared %s" % element_type,
                {**{f.name: "" for f in _offered(element_type)}, **must})
        db_session.expire_all()
        row = ArchiMateElement.query.filter_by(id=record.archimate_element_id).first()
        assert not any(k in (row.custom_properties or {}) for k in names), element_type


def test_stored_value_on_the_page_is_not_visible_to_another_organisation(app, db_session, client, make_org):
    _signed_in(client, db_session, make_org, "form-owner")
    element_id = _create(client, "Node", "Private node", {"datacenter": "Dublin data centre"})
    page = client.get("/architecture/technology/Node/%s" % element_id)
    assert "Dublin data centre" in page.get_data(as_text=True)

    other = app.test_client()
    _signed_in(other, db_session, make_org, "form-other")
    resp = other.get("/architecture/technology/Node/%s" % element_id)
    assert resp.status_code == 404
    assert "Dublin data centre" not in resp.get_data(as_text=True)


def test_a_date_reaches_its_box_as_a_plain_date(app, db_session, client, make_org):
    _signed_in(client, db_session, make_org, "form-date")
    element_id = _create(client, "WorkPackage", "Dated package", {"target_date": "2027-03-15"})
    assert _edit_target(client, "WorkPackage", element_id)["target_date"] == "2027-03-15"


def test_a_null_choice_stays_null_on_rename(app, db_session, client, make_org):
    _signed_in(client, db_session, make_org, "form-null")
    from app.models.application_layer import ApplicationInterface

    element_id = _create(client, "ApplicationInterface", "Interface without a type", {})
    record = ApplicationInterface.query.filter_by(id=element_id).first()
    assert record.interface_type is None
    resp = _rename(client, "ApplicationInterface", element_id, "Renamed interface", {"interface_type": ""})
    assert resp.status_code == 200
    db_session.expire_all()
    assert ApplicationInterface.query.filter_by(id=element_id).first().interface_type is None
