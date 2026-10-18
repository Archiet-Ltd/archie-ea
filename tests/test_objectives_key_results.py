"""Objectives (goal elements) and key results (widened measure definitions).

Drives the real HTTP surface with committed data, because the outcome
measurement service opens its own command sessions.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app import db
from app.models.archimate_core import ArchiMateElement
from app.models.transformation_execution import OutcomeMeasurement
from app.models.transformation_programme import MeasureDefinition

from tests.test_transformation_api import (  # noqa: F401  (fixtures)
    BASE,
    _ensure_guards,
    _intake,
    _make_org,
    _make_user,
    committed_session,
    transformation_api_schema,
)

os.environ.setdefault("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "74" * 32)

OBJ = "/api/v1/objectives"


def _h():
    return {"Idempotency-Key": f"okr-{uuid.uuid4().hex}"}


def _post(client, login_as, user, path, payload):
    db.session.remove()
    login_as(client, user.id)
    return client.post(path, json=payload, headers=_h())


def _get(client, login_as, user, path):
    db.session.remove()
    login_as(client, user.id)
    return client.get(path)


def _measurement(value="620", source="finance:ledger-1"):
    return {
        "value": value,
        "observed_at": "2026-10-01T09:00:00+00:00",
        "source_identity": source,
        "source_version": "v1",
    }


@pytest.fixture
def okr(client, committed_session, login_as, monkeypatch):
    monkeypatch.setenv("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "74" * 32)
    session, cleanup = committed_session
    _ensure_guards(session)
    org_a = _make_org(session, cleanup, "okr-a")
    org_b = _make_org(session, cleanup, "okr-b")
    a = _make_user(session, org_a)
    b = _make_user(session, org_b)
    reader = _make_user(session, org_a, role="arb_member")
    session.commit()
    ids = {"org_a": org_a.id, "org_b": org_b.id, "a": a.id, "b": b.id, "reader": reader.id}

    class U:  # detached-safe user handle
        def __init__(self, id):
            self.id = id

    return client, login_as, U(ids["a"]), U(ids["b"]), U(ids["reader"]), ids


def _objective_with_key_result(client, login_as, user):
    created = _post(client, login_as, user, OBJ, {"name": "Cut run cost", "description": "FY27"})
    assert created.status_code == 201, created.get_json()
    goal_id = created.get_json()["data"]["goal_element_id"]
    kr = _post(
        client, login_as, user, f"{OBJ}/{goal_id}/key-results",
        {"metric_name": "Annual run cost", "unit": "GBP", "baseline_value": "1000", "target_value": "700"},
    )
    assert kr.status_code == 201, kr.get_json()
    return goal_id, kr.get_json()["data"]


def test_objective_is_a_goal_element_and_key_result_a_widened_measure(okr):
    client, login_as, a, _b, _r, ids = okr
    goal_id, kr = _objective_with_key_result(client, login_as, a)

    db.session.remove()
    element = db.session.get(ArchiMateElement, goal_id)
    assert element.type == "Goal" and element.organization_id == ids["org_a"]
    measure = db.session.get(MeasureDefinition, kr["id"])
    assert measure.subject_element_id == goal_id
    assert measure.outcome_commitment_id is None
    assert measure.benefit_id == kr["benefit_id"]


def test_measurement_lands_in_the_one_outcome_measurement_history(okr):
    client, login_as, a, _b, _r, ids = okr
    goal_id, kr = _objective_with_key_result(client, login_as, a)

    recorded = _post(client, login_as, a, f"{OBJ}/key-results/{kr['id']}/measurements", _measurement())
    assert recorded.status_code == 201, recorded.get_json()

    db.session.remove()
    rows = db.session.scalars(
        select(OutcomeMeasurement).where(OutcomeMeasurement.benefit_id == kr["benefit_id"])
    ).all()
    assert [str(r.value) for r in rows] == ["620.000000"]
    assert recorded.get_json()["data"]["outcome_measurement_id"] == rows[0].id

    detail = _get(client, login_as, a, f"{OBJ}/{goal_id}").get_json()["data"]
    row = detail["key_results"][0]
    assert row["latest_value"] == "620.000000"
    assert row["status"] == "realising" or row["status"] in {"realised", "not_realised"}
    assert row["measurements"][0]["source_identity"].endswith("ledger-1")

    # Replaying the same command key is idempotent: still one observation.
    key = {"Idempotency-Key": "okr-replay-1"}
    db.session.remove(); login_as(client, a.id)
    first = client.post(f"{OBJ}/key-results/{kr['id']}/measurements", json=_measurement("610", "finance:ledger-2"), headers=key)
    db.session.remove(); login_as(client, a.id)
    second = client.post(f"{OBJ}/key-results/{kr['id']}/measurements", json=_measurement("610", "finance:ledger-2"), headers=key)
    assert first.status_code == 201 and second.status_code == 200
    db.session.remove()
    assert db.session.scalar(
        select(func.count(OutcomeMeasurement.id)).where(OutcomeMeasurement.benefit_id == kr["benefit_id"])
    ) == 2


def test_programme_scoped_measure_is_unchanged_and_not_listed_as_key_result(okr):
    client, login_as, a, _b, _r, ids = okr
    created = _post(client, login_as, a, BASE, _intake(a.id))
    assert created.status_code == 201, created.get_json()
    data = created.get_json()["data"]

    db.session.remove()
    measure = db.session.get(MeasureDefinition, data["measure_definition_id"])
    assert measure.subject_element_id is None and measure.benefit_id is None
    assert measure.outcome_commitment_id == data["outcome_commitment_id"]
    assert measure.to_dict()["outcome_commitment_id"] == data["outcome_commitment_id"]

    # It still reads through the programme room, and is not an objective's key result.
    room = _get(client, login_as, a, f"{BASE}/{data['programme_id']}/workstreams/{data['workstream_id']}")
    assert room.status_code == 200
    listing = _get(client, login_as, a, OBJ).get_json()["data"]
    assert listing == []
    assert _get(client, login_as, a, f"{OBJ}/key-results/{measure.id}").status_code == 404
    assert _post(
        client, login_as, a, f"{OBJ}/key-results/{measure.id}/measurements", _measurement()
    ).status_code == 404


def test_objectives_and_key_results_are_invisible_to_another_organisation(okr):
    client, login_as, a, b, _r, _ids = okr
    goal_id, kr = _objective_with_key_result(client, login_as, a)

    assert _get(client, login_as, b, OBJ).get_json()["data"] == []
    assert _get(client, login_as, b, f"{OBJ}/{goal_id}").status_code == 404
    assert _get(client, login_as, b, f"{OBJ}/key-results/{kr['id']}").status_code == 404
    assert _post(
        client, login_as, b, f"{OBJ}/{goal_id}/key-results",
        {"metric_name": "x", "unit": "u", "baseline_value": "1", "target_value": "2"},
    ).status_code == 404
    assert _post(
        client, login_as, b, f"{OBJ}/key-results/{kr['id']}/measurements", _measurement()
    ).status_code == 404
    # Picking A's goal from B must not succeed either.
    assert _post(client, login_as, b, OBJ, {"goal_element_id": goal_id}).status_code == 404

    mine = _get(client, login_as, a, OBJ).get_json()["data"]
    assert [o["goal_element_id"] for o in mine] == [goal_id]
    assert mine[0]["key_result_count"] == 1


def test_pick_an_existing_goal_and_role_gate(okr):
    client, login_as, a, _b, reader, _ids = okr
    goal_id, _kr = _objective_with_key_result(client, login_as, a)
    picked = _post(client, login_as, a, OBJ, {"goal_element_id": goal_id})
    assert picked.status_code == 201 and picked.get_json()["data"]["goal_element_id"] == goal_id
    denied = _post(client, login_as, reader, OBJ, {"name": "Nope"})
    assert denied.status_code == 403
    assert _get(client, login_as, reader, f"{OBJ}/{goal_id}").status_code == 200


def test_database_requires_a_subject_and_keeps_it_in_the_tenant(okr):
    client, login_as, a, b, _r, ids = okr
    goal_id, kr = _objective_with_key_result(client, login_as, a)
    db.session.remove()
    with pytest.raises((IntegrityError, DBAPIError)):
        db.session.execute(text(
            "INSERT INTO measure_definitions (organization_id, metric_name, unit, aggregation, revision) "
            "VALUES (:o, 'orphan', 'u', 'latest', 1)"), {"o": ids["org_a"]})
        db.session.flush()
    db.session.rollback()
    with pytest.raises((IntegrityError, DBAPIError)):
        db.session.execute(text(
            "INSERT INTO measure_definitions (organization_id, subject_element_id, metric_name, unit, aggregation, revision) "
            "VALUES (:o, :e, 'cross', 'u', 'latest', 1)"), {"o": ids["org_b"], "e": goal_id})
        db.session.flush()
    db.session.rollback()
