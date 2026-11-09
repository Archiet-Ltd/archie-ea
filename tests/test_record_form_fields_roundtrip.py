"""Every field a record form shows is saved and read back, for every record type: set it, save, reopen the
Edit page, and the same value is there. A field the record has no place for is refused by name, never
reported as saved."""

from __future__ import annotations

import json
import re

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _types():
    from app.modules.architecture.routes.archimate_crud.services.field_configs import ELEMENT_TYPE_CONFIGS

    return sorted(ELEMENT_TYPE_CONFIGS)


def _editor(db_session, make_org, client, login_as, label):
    from tests.test_work_package_writer import _user

    org = make_org(label)
    login_as(client, _user(db_session, org, label.replace("-", ""), role="enterprise_architect"))
    return org


def _opened(client, url):
    html = client.get(url).get_data(as_text=True)
    found = re.search(r"__APP_CONFIG__\.editingElement\s*=\s*(\{.*?\});", html, re.S)
    assert found, "the Edit page carries no record to open the form on: %s" % url
    return json.loads(found.group(1))


def _values(config, round_two):
    """A value for each shown field the way a person fills it: the first option, or typed text; the
    second round picks something different so a save that kept the old value cannot pass."""
    values = {}
    for fld in config.fields:
        if fld.field_type == "select":
            options = [o.value for o in fld.options]
            values[fld.name] = options[1 if round_two and len(options) > 1 else 0]
        elif fld.field_type == "date":
            values[fld.name] = "2032-04-06" if round_two else "2031-07-09"
        elif fld.name in ("bandwidth_gbps",):
            values[fld.name] = "40" if round_two else "10"
        else:
            values[fld.name] = "Second words" if round_two else "First words"
    return values


def _same(shown, posted):
    return str(shown).strip() == str(posted).strip() or _number(shown) == _number(posted) is not None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _principle(db_session, org):
    from app.models.models import Principle

    row = Principle(organization_id=org.id, name="Round trip Principle", statement="A statement")
    db_session.add(row)
    db_session.flush()
    return row.id


def _business_function(db_session, org):
    from app.models.business_capabilities import BusinessFunction
    from app.models.unified_capability import UnifiedCapability

    capability = UnifiedCapability(organization_id=org.id, name="Round trip capability")
    db_session.add(capability)
    db_session.flush()
    row = BusinessFunction(organization_id=org.id, name="Round trip BusinessFunction", capability_id=capability.id)
    db_session.add(row)
    db_session.flush()
    return row.id


_NEEDS_OTHER_RECORDS = {"Principle": _principle, "BusinessFunction": _business_function}


@pytest.mark.parametrize("element_type", _types())
def test_every_field_the_form_shows_is_saved_and_reopens_with_the_same_value(
    db_session, make_org, client, login_as, element_type
):
    from app.modules.architecture.routes.archimate_crud.services.field_configs import ELEMENT_TYPE_CONFIGS

    config = ELEMENT_TYPE_CONFIGS[element_type]
    org = _editor(db_session, make_org, client, login_as, "roundtrip-%s" % element_type.lower())

    if element_type in _NEEDS_OTHER_RECORDS:
        # The create form does not collect these types' required columns, so the record starts as a row.
        new_id = _NEEDS_OTHER_RECORDS[element_type](db_session, org)
        first = client.post("/architecture/%s/%s/%d/edit" % (config.layer, element_type, new_id),
                            json={"name": "Round trip %s" % element_type, "description": "d", **_values(config, False)})
        assert first.status_code == 200, (element_type, first.get_json())
        db_session.expire_all()
    else:
        created = client.post("/architecture/%s/%s/new" % (config.layer, element_type),
                              json={"name": "Round trip %s" % element_type, "description": "d", **_values(config, False)})
        assert created.status_code == 200, (element_type, created.get_json())
        new_id = created.get_json()["id"]
    edit_url = "/architecture/%s/%s/%d/edit" % (config.layer, element_type, new_id)

    for round_two in (False, True):
        values = _values(config, round_two)
        if round_two:
            saved = client.post(edit_url, json={"name": "Round trip %s" % element_type, "description": "d", **values})
            assert saved.status_code == 200, (element_type, saved.get_json())
            db_session.expire_all()
        opened = _opened(client, edit_url)
        for name, posted in values.items():
            assert name in opened, "%s: the saved %s did not reopen on the form" % (element_type, name)
            assert _same(opened[name], posted), (element_type, name, posted, opened[name])


def test_a_field_the_record_has_no_place_for_answers_400_naming_it(db_session, make_org, client, login_as):
    _editor(db_session, make_org, client, login_as, "unknown-field")
    created = client.post("/architecture/business/BusinessRole/new", json={"name": "Role"})
    edit_url = "/architecture/business/BusinessRole/%d/edit" % created.get_json()["id"]

    reply = client.post(edit_url, json={"name": "Role", "invented_field": "x"})

    assert reply.status_code == 400
    assert "invented_field" in reply.get_json()["error"]


def test_a_date_that_is_not_a_date_answers_400_naming_the_field(db_session, make_org, client, login_as):
    _editor(db_session, make_org, client, login_as, "bad-date")
    created = client.post("/architecture/implementation/WorkPackage/new", json={"name": "Package"})
    edit_url = "/architecture/implementation/WorkPackage/%d/edit" % created.get_json()["id"]

    reply = client.post(edit_url, json={"name": "Package", "target_date": "next Tuesday"})

    assert reply.status_code == 400
    assert reply.get_json()["error"] == "Target date must be a date (YYYY-MM-DD)"
    assert _opened(client, edit_url).get("target_date") in (None, "")


def test_the_form_shows_a_date_field_as_a_date_input_and_a_gap_rating_under_its_own_name():
    from app.modules.architecture.routes.archimate_crud.services.field_configs import (
        GAP_RATING_LABEL, get_element_config)

    for element_type in ("WorkPackage", "Plateau"):
        target = [f for f in get_element_config(element_type).fields if f.name == "target_date"][0]
        assert target.field_type == "date"
    rating = [f for f in get_element_config("Gap").fields if f.name == "priority"][0]
    assert rating.label == GAP_RATING_LABEL == "Rating (sets priority and severity)"
    assert rating.effect == "priority and severity"
