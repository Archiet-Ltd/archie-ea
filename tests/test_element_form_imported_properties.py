"""A property a model file brought in is never taken for a form value, and a stored value never breaks the page.

The form's fields write the element's own columns. The few it offers that have no column (a business function's
domain) are kept under the form's own key, so an imported property of the same name keeps its value through
every save. A stored value that is not text (a list or a mapping) is shown as words and never raises.
"""
from __future__ import annotations

import json
import uuid

import pytest
import sqlalchemy as sa

from app import db
from tests.test_element_form_values import (
    _capability, _create, _edit_target, _layer, _rename, _signed_in, crud,
)

pytestmark = pytest.mark.usefixtures("db_session")


# The element of each record, by record id. Rows are read and written by key with SQL, so another organisation's
# rows can be checked without opening them through this organisation's session.
_ELEMENT_OF = {}


def _element_id(element_type, element_id):
    if element_id not in _ELEMENT_OF:
        _ELEMENT_OF[element_id] = crud.MODEL_REGISTRY[element_type].query.filter_by(id=element_id).first().archimate_element_id
    return _ELEMENT_OF[element_id]


def _properties(element_type, element_id):
    return db.session.execute(
        sa.text("SELECT custom_properties FROM archimate_elements WHERE id = :id"),
        {"id": _element_id(element_type, element_id)},
    ).scalar()


def _import_properties(db_session, element_type, element_id, properties):
    db_session.execute(
        sa.text("UPDATE archimate_elements SET custom_properties = CAST(:props AS json) WHERE id = :id"),
        {"props": json.dumps(properties), "id": _element_id(element_type, element_id)},
    )
    db_session.flush()
    db_session.expire_all()


def _function(db_session, client, org, name):
    return _create(client, "BusinessFunction", name, {}, capability_id=_capability(db_session, org))


def _held_record(db_session, org, element_type, name, **columns):
    """A record of another organisation with its element, as an import leaves it, made without the form."""
    from app.models.archimate_core import ArchiMateElement

    element = ArchiMateElement(organization_id=org.id, name=name, type=element_type, layer="Business")
    db_session.add(element)
    db_session.flush()
    record = crud.MODEL_REGISTRY[element_type](
        organization_id=org.id, name=name, archimate_element_id=element.id, **columns)
    db_session.add(record)
    db_session.flush()
    _ELEMENT_OF[record.id] = element.id
    return record.id


def test_a_save_leaves_an_imported_property_of_the_same_name_untouched(app, db_session, client, make_org):
    org = _signed_in(client, db_session, make_org, "props-mine")
    other_org = make_org("props-theirs")
    tag = uuid.uuid4().hex[:6]
    mine = _function(db_session, client, org, "Imported function %s" % tag)
    theirs = _held_record(db_session, other_org, "BusinessFunction", "Their function %s" % tag,
                          capability_id=_capability(db_session, other_org))
    _import_properties(db_session, "BusinessFunction", mine, {"domain": "Imported domain", "version": "1.2"})
    _import_properties(db_session, "BusinessFunction", theirs, {"domain": "Their imported domain"})
    capability = _capability(db_session, org)

    # The imported property is not the form's value.
    assert _edit_target(client, "BusinessFunction", mine)["domain"] == ""

    # A save with a domain stores the form's value and leaves the imported property as it was.
    resp = _rename(client, "BusinessFunction", mine, "Saved %s" % tag, {"domain": "Finance", "capability_id": capability})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    db_session.expire_all()
    assert _edit_target(client, "BusinessFunction", mine)["domain"] == "Finance"
    kept = _properties("BusinessFunction", mine)
    assert kept["domain"] == "Imported domain" and kept["version"] == "1.2"

    # Emptying the form's domain clears only the form's value.
    resp = _rename(client, "BusinessFunction", mine, "Cleared %s" % tag, {"domain": "", "capability_id": capability})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    db_session.expire_all()
    assert _edit_target(client, "BusinessFunction", mine)["domain"] == ""
    assert _properties("BusinessFunction", mine)["domain"] == "Imported domain"

    # The other organisation's element and its property were not touched.
    assert _properties("BusinessFunction", theirs) == {"domain": "Their imported domain"}


def test_a_status_property_never_changes_with_the_status_column(app, db_session, client, make_org):
    _signed_in(client, db_session, make_org, "status-mine")
    other_org = make_org("status-theirs")
    tag = uuid.uuid4().hex[:6]
    mine = _create(client, "WorkPackage", "Imported package %s" % tag, {"status": "planned"})
    theirs = _held_record(db_session, other_org, "WorkPackage", "Their package %s" % tag, status="planned")
    _import_properties(db_session, "WorkPackage", mine, {"status": "Imported status"})
    _import_properties(db_session, "WorkPackage", theirs, {"status": "Their imported status"})

    resp = _rename(client, "WorkPackage", mine, "Saved %s" % tag, {"status": "in_progress"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    db_session.expire_all()
    record = crud.MODEL_REGISTRY["WorkPackage"].query.filter_by(id=mine).first()
    assert record.status == "in_progress"
    assert _properties("WorkPackage", mine)["status"] == "Imported status"
    assert _properties("WorkPackage", theirs)["status"] == "Their imported status"
    assert db.session.execute(
        sa.text("SELECT status FROM work_packages WHERE id = :id"), {"id": theirs}).scalar() == "planned"


@pytest.mark.parametrize("stored,shown", [(["Finance", "Risk"], "Finance, Risk"), ({"unit": "Treasury"}, "Unit: Treasury")])
def test_a_stored_value_that_is_not_text_is_shown_as_words_and_never_raises(
        app, db_session, client, make_org, stored, shown):
    org = _signed_in(client, db_session, make_org, "nontext-mine")
    mine = _function(db_session, client, org, "Listed function %s" % uuid.uuid4().hex[:6])
    # A value of the form's own, as an earlier import or a script left it: kept the way the form keeps its values.
    record = crud.MODEL_REGISTRY["BusinessFunction"].query.filter_by(id=mine).first()
    crud._store_custom_fields(record, {"domain": stored}, "BusinessFunction")
    db_session.flush()

    page = client.get("/architecture/%s/BusinessFunction/%s" % (_layer("BusinessFunction"), mine))
    assert page.status_code == 200
    assert shown in page.get_data(as_text=True)
    assert _edit_target(client, "BusinessFunction", mine)["domain"] == shown
