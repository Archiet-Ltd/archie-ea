"""Every element in an answer carries ``type``, ``type_label`` and ``base_type`` (ADR-0014 Decision 9).

``type`` stays the stored string; ``type_label`` is the registry's plain name as it stands; ``base_type`` is the
standard ArchiMate type. The label is read through the registry, so these tests pin the behaviour to it: if the one
plain name for a type changes, every label changes with it.
"""

from __future__ import annotations

import pytest

from app.models.archimate_element_types import PLAIN_LANGUAGE_NAMES, base_type, plain_name_for
from app.modules.intelligence.services import answer_envelope as env
from app.modules.intelligence.tests.test_data_for_element import _edge, _run as _run_data
from app.modules.intelligence.tests.test_query_service_elements_map import (
    _element,
    _impact,
    _relationship,
    _user,
)

APP = "Application"
COMP = "ApplicationComponent"
THREE = ("type", "type_label", "base_type")


def _expect(stored):
    return {"type": stored, "type_label": plain_name_for(stored), "base_type": base_type(stored)}


def _check(entry, stored):
    for key, value in _expect(stored).items():
        assert entry[key] == value, (key, entry)


@pytest.fixture
def world(app, db_session, make_org):
    org = make_org("type-label")
    root = _element(db_session, org.id, "Root", type_="BusinessProcess", layer="business")
    application = _element(db_session, org.id, "Billing", type_=APP)
    component = _element(db_session, org.id, "Billing API", type_=COMP)
    _relationship(db_session, org.id, root, application)
    _relationship(db_session, org.id, root, component)
    user = _user(db_session, org.id)
    db_session.commit()
    return org, user, root, application, component


def test_the_label_is_the_registry_plain_name_not_a_second_table():
    assert base_type(APP) == COMP
    assert plain_name_for(APP) == PLAIN_LANGUAGE_NAMES[APP]
    assert plain_name_for(COMP) == PLAIN_LANGUAGE_NAMES[COMP]


def test_flipping_the_one_plain_name_changes_the_label_everywhere(app, db_session, world, monkeypatch):
    org, _user_row, root, application, component = world
    monkeypatch.setitem(PLAIN_LANGUAGE_NAMES, COMP, "Flipped name")
    result = _impact(app, org.id, root.id)
    assert result["elements"][str(component.id)]["type_label"] == "Flipped name"
    assert result["elements"][str(component.id)]["type"] == COMP


def test_ask_answer_elements_carry_the_three_fields(app, db_session, world):
    org, _u, root, application, component = world
    result = _impact(app, org.id, root.id)
    _check(result["elements"][str(application.id)], APP)
    _check(result["elements"][str(component.id)], COMP)
    assert result["elements"][str(application.id)]["base_type"] == COMP


def test_data_answer_elements_carry_the_three_fields(app, db_session, make_org):
    org = make_org("type-label-data")
    centre = _element(db_session, org.id, "Core", type_=APP)
    other = _element(db_session, org.id, "Upstream", type_=COMP)
    _edge(db_session, org.id, other, centre, lineage_type="ETL")
    db_session.commit()
    result = _run_data(app, org.id, centre.id)
    _check(result["elements"][str(centre.id)], APP)
    _check(result["elements"][str(other.id)], COMP)


def test_envelope_elements_pass_the_three_fields_through(app, db_session, world):
    org, _u, root, application, component = world
    result = _impact(app, org.id, root.id)
    built = env.build_envelope(env.KIND_IMPACT, {"element_id": root.id}, result)
    _check(built["elements"][str(application.id)], APP)
    _check(built["elements"][str(component.id)], COMP)


def test_citations_keep_their_record_kind_type(app, db_session, world):
    org, _u, root, _a, _c = world
    result = _impact(app, org.id, root.id)
    built = env.build_envelope(env.KIND_IMPACT, {"element_id": root.id}, result)
    for citation in built["citations"]:
        assert "type_label" not in citation and "base_type" not in citation


def test_search_carries_the_three_fields(client, login_as, world):
    org, user, _r, application, component = world
    login_as(client, user)
    data = client.get("/archimate/api/elements/search?q=Billing&limit=10").get_json()["data"]
    by_id = {row["id"]: row for row in data}
    _check(by_id[application.id], APP)
    _check(by_id[component.id], COMP)


def test_detail_carries_the_three_fields(client, login_as, world):
    org, user, root, application, component = world
    login_as(client, user)
    body = client.get(f"/archimate/api/elements/{application.id}/detail").get_json()
    _check(body, APP)
    body = client.get(f"/archimate/api/elements/{component.id}/detail").get_json()
    _check(body, COMP)
    body = client.get(f"/archimate/api/elements/{root.id}/detail").get_json()
    connected = {row["id"]: row for row in body["connected_elements"]}
    _check(connected[application.id], APP)
    _check(connected[component.id], COMP)


def test_two_organisations_see_only_their_own_labelled_elements(client, login_as, db_session, make_org):
    org_a, org_b = make_org("type-label-a"), make_org("type-label-b")
    mine = _element(db_session, org_a.id, "Shared name", type_=APP)
    theirs = _element(db_session, org_b.id, "Shared name", type_=COMP)
    user_a = _user(db_session, org_a.id)
    db_session.commit()
    login_as(client, user_a)
    rows = client.get("/archimate/api/elements/search?q=Shared name&limit=10").get_json()["data"]
    assert [row["id"] for row in rows] == [mine.id]
    _check(rows[0], APP)
    assert theirs.id not in {row["id"] for row in rows}
