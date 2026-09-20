"""HTTP contract for the canonical Transformation Room API.

The route-surface checks pin every Task 10 resource.  The integration checks
drive the real programme service and command envelope; they intentionally use
committed setup because ``CommandService`` opens independent sessions.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db
from app.models.transformation_programme import ProgrammeWorkstream
from app.modules.transformation_room.evidence_service import REQUIRED_EVIDENCE_CLAIMS

from tests.test_transformation_evidence_service import (
    _grant_decision_authority,
    _plan_all_requests,
    evidence_scope,
)
from tests.test_rationalisation_discovery_service import committed_scope
from tests.test_decision_brief_service import _remove_fixture_brief
from tests.test_transformation_option_service import (
    _option_values,
    decision_scope,
)
from tests.test_transformation_execution_service import committed_execution_scope


os.environ.setdefault("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "74" * 32)


BASE = "/api/v1/transformation-programmes"

ROUTE_CONTRACT = {
    (BASE, "GET"),
    (BASE, "POST"),
    (f"{BASE}/<int:programme_id>", "GET"),
    (f"{BASE}/<int:programme_id>", "DELETE"),
    (f"{BASE}/<int:programme_id>/role-assignments", "POST"),
    (f"{BASE}/<int:programme_id>/workstreams", "GET"),
    (f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>", "GET"),
    (f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>/objective", "PATCH"),
    (f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>/transitions", "POST"),
    (f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>/discovery-candidates", "GET"),
    (f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>/candidates", "POST"),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/candidates/<int:candidate_id>/evidence-requests",
        "POST",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/candidates/<int:candidate_id>/evidence-observations",
        "POST",
    ),
    (f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>/evidence", "GET"),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/evidence-requests/<int:evidence_request_id>/attestations",
        "POST",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/evidence-requests/<int:evidence_request_id>/acceptance",
        "POST",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/evidence-requests/<int:evidence_request_id>/decline",
        "POST",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/evidence-requests/<int:evidence_request_id>/expiry",
        "POST",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/evidence-requests/<int:evidence_request_id>/waiver",
        "POST",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/evidence/<int:conflict_evidence_id>/resolution",
        "POST",
    ),
    (f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>/options", "POST"),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/options/<int:option_id>/versions",
        "POST",
    ),
    (f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>/option-comparison", "GET"),
    (f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>/decision-briefs", "POST"),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/decision-briefs/<int:brief_id>/readiness",
        "GET",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/decision-briefs/<int:brief_id>/versions",
        "POST",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/decision-briefs/<int:brief_id>/arb-submissions",
        "POST",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/decision-brief-versions/<int:brief_version_id>/execution",
        "POST",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/decision-brief-versions/<int:brief_version_id>/technology-solutions",
        "POST",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/work-packages/<int:work_package_id>/delivery-exports",
        "POST",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/work-packages/<int:work_package_id>/delivery-exports/<int:attempt_id>/retries",
        "POST",
    ),
    (
        f"{BASE}/<int:programme_id>/workstreams/<int:workstream_id>"
        "/benefits/<int:benefit_id>/measurements",
        "POST",
    ),
}


MUTATION_PATHS = (
    ("POST", BASE),
    ("DELETE", f"{BASE}/999999"),
    ("POST", f"{BASE}/999999/role-assignments"),
    ("PATCH", f"{BASE}/999999/workstreams/999999/objective"),
    ("POST", f"{BASE}/999999/workstreams/999999/transitions"),
    ("POST", f"{BASE}/999999/workstreams/999999/candidates"),
    ("POST", f"{BASE}/999999/workstreams/999999/candidates/999999/evidence-requests"),
    ("POST", f"{BASE}/999999/workstreams/999999/candidates/999999/evidence-observations"),
    ("POST", f"{BASE}/999999/workstreams/999999/evidence-requests/999999/attestations"),
    ("POST", f"{BASE}/999999/workstreams/999999/evidence-requests/999999/acceptance"),
    ("POST", f"{BASE}/999999/workstreams/999999/evidence-requests/999999/decline"),
    ("POST", f"{BASE}/999999/workstreams/999999/evidence-requests/999999/expiry"),
    ("POST", f"{BASE}/999999/workstreams/999999/evidence-requests/999999/waiver"),
    ("POST", f"{BASE}/999999/workstreams/999999/evidence/999999/resolution"),
    ("POST", f"{BASE}/999999/workstreams/999999/options"),
    ("POST", f"{BASE}/999999/workstreams/999999/options/999999/versions"),
    ("POST", f"{BASE}/999999/workstreams/999999/decision-briefs"),
    ("POST", f"{BASE}/999999/workstreams/999999/decision-briefs/999999/versions"),
    ("POST", f"{BASE}/999999/workstreams/999999/decision-briefs/999999/arb-submissions"),
    ("POST", f"{BASE}/999999/workstreams/999999/decision-brief-versions/999999/execution"),
    (
        "POST",
        f"{BASE}/999999/workstreams/999999/decision-brief-versions/999999/technology-solutions",
    ),
    ("POST", f"{BASE}/999999/workstreams/999999/work-packages/999999/delivery-exports"),
    (
        "POST",
        f"{BASE}/999999/workstreams/999999/work-packages/999999/delivery-exports/999999/retries",
    ),
    ("POST", f"{BASE}/999999/workstreams/999999/benefits/999999/measurements"),
)


_CLEANUP_TABLES = (
    "archie_command_claim_challenges",
    "transformation_outbox_events",
    "operation_results",
    "command_materialisations",
    "command_idempotency_records",
    "measure_definitions",
    "programme_outcome_commitments",
    "programme_role_assignments",
    "programme_workstreams",
    "strategic_initiatives",
    "users",
)


@pytest.fixture(scope="module", autouse=True)
def transformation_api_schema(app, _schema):
    from app.commands.reconcile_schema import _reconcile

    with app.app_context():
        _added, failed, _missing, _blocking = _reconcile(dry_run=False)
        assert failed == []


@pytest.fixture
def committed_session(app, _schema):
    """Committed setup visible to the command service's isolated sessions."""
    from app import db

    with app.app_context():
        db.session.remove()
        cleanup_org_ids: set[int] = set()
        try:
            yield db.session, cleanup_org_ids
        finally:
            db.session.remove()
            if cleanup_org_ids:
                raw = db.engine.raw_connection()
                try:
                    with raw.cursor() as cursor:
                        cursor.execute("SHOW session_replication_role")
                        original_role = cursor.fetchone()[0]
                        cursor.execute("SET session_replication_role = replica")
                        try:
                            for table in _CLEANUP_TABLES:
                                cursor.execute(
                                    f'DELETE FROM "{table}" WHERE organization_id = ANY(%s)',
                                    (list(cleanup_org_ids),),
                                )
                            cursor.execute(
                                "DELETE FROM organizations WHERE id = ANY(%s)",
                                (list(cleanup_org_ids),),
                            )
                        finally:
                            cursor.execute(f"SET session_replication_role = {original_role}")
                            raw.commit()
                finally:
                    raw.close()


def _make_org(session, cleanup_org_ids, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"API {label} {suffix}", slug=f"api-{label}-{suffix}")
    session.add(org)
    session.flush()
    cleanup_org_ids.add(org.id)
    return org


def _make_user(session, org, *, role="enterprise_architect"):
    from app.models.user import User

    user = User(
        organization_id=org.id,
        email=f"transformation-api-{uuid.uuid4().hex[:10]}@example.test",
        first_name="Transformation",
        last_name="Architect",
        enterprise_role=role,
        confirmed=True,
    )
    session.add(user)
    session.flush()
    return user


def _ensure_guards(session):
    from app.models.transformation_db_guards import ensure_transformation_db_guards

    ensure_transformation_db_guards(session.connection(), capability_secrets=("74" * 32,))


def _intake(owner_id):
    return {
        "name": "Simplify the application estate",
        "objective": "Reduce duplicated capability cost without service loss",
        "owner_id": owner_id,
        "target_date": "2027-06-30",
        "target_date_unavailable_reason": None,
        "workstream_type": "application_rationalisation",
        "scope_expression": {"business_units": ["Retail"]},
        "outcome": {
            "statement": "Reduce annual run cost",
            "owner_id": owner_id,
            "direction": "decrease",
            "measure": {
                "metric_name": "Annual run cost",
                "unit": "GBP",
                "currency": "GBP",
                "aggregation": "sum",
                "baseline_value": None,
                "unavailable_reason": "Finance baseline requested",
                "target_value": "900000.00",
            },
        },
    }


def _assert_envelope(body, *, error_code=None):
    assert set(body) == {"data", "meta", "errors", "request_id"}
    assert isinstance(body["request_id"], str) and body["request_id"]
    if error_code is None:
        assert body["errors"] == []
        assert body["data"] is not None
    else:
        assert body["data"] is None
        assert body["errors"][0]["code"] == error_code


def _programme_id(workstream_id: int, organization_id: int) -> int:
    with Session(db.engine) as session:
        return session.scalar(
            select(ProgrammeWorkstream.programme_id).where(
                ProgrammeWorkstream.id == workstream_id,
                ProgrammeWorkstream.organization_id == organization_id,
            )
        )


def test_transformation_api_registers_the_complete_single_versioned_surface(app):
    observed = {
        (rule.rule, method)
        for rule in app.url_map.iter_rules()
        for method in rule.methods
        if rule.rule.startswith(BASE) and method not in {"HEAD", "OPTIONS"}
    }
    assert observed == ROUTE_CONTRACT


def test_transformation_api_anonymous_requests_use_uniform_401(client):
    response = client.get(BASE, headers={"X-Request-ID": "anonymous-api-probe"})
    assert response.status_code == 401
    body = response.get_json()
    _assert_envelope(body, error_code="not_authenticated")
    assert body["request_id"] == "anonymous-api-probe"


@pytest.mark.parametrize(("method", "path"), MUTATION_PATHS)
def test_transformation_api_every_mutation_requires_idempotency_key(
    method, path, client, committed_session, login_as
):
    session, cleanup_org_ids = committed_session
    org = _make_org(session, cleanup_org_ids, "headers")
    user = _make_user(session, org)
    session.commit()
    login_as(client, user)

    response = client.open(path, method=method, json={})

    assert response.status_code == 400
    body = response.get_json()
    _assert_envelope(body, error_code="validation_failed")
    assert body["errors"][0]["field"] == "Idempotency-Key"


def test_transformation_api_real_intake_replays_and_rejects_changed_digest(
    client, committed_session, login_as, monkeypatch
):
    from app import db
    from app.models.strategic import StrategicInitiative

    monkeypatch.setenv("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "74" * 32)
    session, cleanup_org_ids = committed_session
    _ensure_guards(session)
    org = _make_org(session, cleanup_org_ids, "replay")
    owner = _make_user(session, org)
    session.commit()
    # Read while the instance is still attached: the session is removed between
    # requests below and a committed instance cannot be refreshed once detached.
    org_id = org.id
    login_as(client, owner)
    command_key = f"programme-intake-{uuid.uuid4().hex}"

    first = client.post(BASE, json=_intake(owner.id), headers={"Idempotency-Key": command_key})
    assert first.status_code == 201, first.get_json()
    first_body = first.get_json()
    _assert_envelope(first_body)
    assert first_body["meta"]["idempotent"] is False

    db.session.remove()
    login_as(client, owner)
    replay = client.post(BASE, json=_intake(owner.id), headers={"Idempotency-Key": command_key})
    assert replay.status_code == 200, replay.get_json()
    replay_body = replay.get_json()
    assert replay_body["data"] == first_body["data"]
    assert replay_body["meta"]["idempotent"] is True
    assert replay_body["meta"]["operation_result_id"] == first_body["meta"]["operation_result_id"]

    db.session.remove()
    login_as(client, owner)
    changed = _intake(owner.id)
    changed["objective"] = "A different command body under the same receipt key"
    conflict = client.post(BASE, json=changed, headers={"Idempotency-Key": command_key})
    assert conflict.status_code == 409
    _assert_envelope(conflict.get_json(), error_code="conflict")

    db.session.remove()
    assert (
        db.session.query(StrategicInitiative)
        .filter_by(organization_id=org_id, record_kind="transformation_programme")
        .count()
        == 1
    )

    db.session.remove()
    login_as(client, owner.id)
    listed = client.get(BASE)
    detail = client.get(f"{BASE}/{first_body['data']['programme_id']}")
    workstreams = client.get(
        f"{BASE}/{first_body['data']['programme_id']}/workstreams"
    )
    workstream = client.get(
        f"{BASE}/{first_body['data']['programme_id']}/workstreams/"
        f"{first_body['data']['workstream_id']}"
    )
    assert all(
        response.status_code == 200
        for response in (listed, detail, workstreams, workstream)
    )

    transition_key = f"programme-transition-{uuid.uuid4().hex}"
    transition_url = (
        f"{BASE}/{first_body['data']['programme_id']}/workstreams/"
        f"{first_body['data']['workstream_id']}/transitions"
    )
    transition = client.post(
        transition_url,
        json={"target_stage": "discover"},
        headers={"Idempotency-Key": transition_key, "If-Match": "1"},
    )
    transition_replay = client.post(
        transition_url,
        json={"target_stage": "discover"},
        headers={"Idempotency-Key": transition_key, "If-Match": "1"},
    )
    assert transition.status_code == 200, transition.get_json()
    assert transition_replay.status_code == 200, transition_replay.get_json()
    assert transition_replay.get_json()["meta"]["idempotent"] is True

    archive_key = f"programme-archive-{uuid.uuid4().hex}"
    archive_url = f"{BASE}/{first_body['data']['programme_id']}"
    archive_body = {"rationale": "The governed transformation has been superseded."}
    archived = client.delete(
        archive_url,
        json=archive_body,
        headers={"Idempotency-Key": archive_key, "If-Match": "1"},
    )
    archived_replay = client.delete(
        archive_url,
        json=archive_body,
        headers={"Idempotency-Key": archive_key, "If-Match": "1"},
    )
    assert archived.status_code == 200, archived.get_json()
    assert archived_replay.status_code == 200, archived_replay.get_json()
    assert archived_replay.get_json()["meta"]["idempotent"] is True

    changed_archive = client.delete(
        archive_url,
        json={"rationale": "A changed archive command."},
        headers={"Idempotency-Key": archive_key, "If-Match": "1"},
    )
    assert changed_archive.status_code == 409


def test_discovery_and_candidate_acceptance_use_production_http_services(
    client, login_as, committed_scope
):
    scope = committed_scope
    programme_id = _programme_id(scope.workstream_id, scope.organization_id)
    base = f"{BASE}/{programme_id}/workstreams/{scope.workstream_id}"
    login_as(client, scope.actor_id)

    discovered = client.get(f"{base}/discovery-candidates")
    assert discovered.status_code == 200, discovered.get_json()
    candidate = next(
        item
        for item in discovered.get_json()["data"]["candidates"]
        if item["application_id"] == scope.application_id
    )
    assert len(candidate["signals"]) == 7
    body = {
        "application_id": scope.application_id,
        "signal_digests": candidate["signal_digests"],
        "inclusion_reason": "Govern the canonical inventory subject.",
        "overlap_disposition": {
            "decision": "justified_distinct",
            "overlapping_application_ids": [scope.sibling_application_id],
            "rationale": "The applications serve distinct operating contexts.",
        },
    }
    key = f"api-candidate-{uuid.uuid4().hex}"
    accepted = client.post(
        f"{base}/candidates",
        json=body,
        headers={"Idempotency-Key": key},
    )
    replay = client.post(
        f"{base}/candidates",
        json=body,
        headers={"Idempotency-Key": key},
    )
    assert accepted.status_code == 201, accepted.get_json()
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()["data"] == accepted.get_json()["data"]
    assert replay.get_json()["meta"]["idempotent"] is True

    changed = dict(body)
    changed["inclusion_reason"] = "A changed receipt body."
    conflict = client.post(
        f"{base}/candidates",
        json=changed,
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409
    _assert_envelope(conflict.get_json(), error_code="conflict")


def test_evidence_attestation_and_waiver_routes_reconcile_before_mutable_checks(
    client, login_as, evidence_scope
):
    """Exact HTTP retries survive submitted state and a subsequently expired waiver."""
    scope = evidence_scope
    programme_id = _programme_id(scope.workstream_id, scope.organization_id)
    planned = _plan_all_requests(
        scope, key=f"api-reconcile-evidence-{uuid.uuid4().hex}"
    )
    lifecycle_request_id = planned.object_ids["request_lifecycle_id"]
    risk_request_id = planned.object_ids["request_risk_id"]
    base = f"{BASE}/{programme_id}/workstreams/{scope.workstream_id}"

    attestation_key = f"api-attestation-{uuid.uuid4().hex}"
    attestation_body = {
        "value": {"value_type": "string", "value": "active"}
    }
    login_as(client, scope.actor_id)
    first = client.post(
        f"{base}/evidence-requests/{lifecycle_request_id}/attestations",
        json=attestation_body,
        headers={"Idempotency-Key": attestation_key, "If-Match": "0"},
    )
    assert first.status_code == 201, first.get_json()

    db.session.remove()
    login_as(client, scope.actor_id)
    replay = client.post(
        f"{base}/evidence-requests/{lifecycle_request_id}/attestations",
        json=attestation_body,
        headers={"Idempotency-Key": attestation_key, "If-Match": "0"},
    )
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()["data"] == first.get_json()["data"]
    assert replay.get_json()["meta"]["idempotent"] is True

    db.session.remove()
    login_as(client, scope.actor_id)
    changed_attestation = client.post(
        f"{base}/evidence-requests/{lifecycle_request_id}/attestations",
        json={"value": {"value_type": "string", "value": "retired"}},
        headers={"Idempotency-Key": attestation_key, "If-Match": "0"},
    )
    assert changed_attestation.status_code == 409
    _assert_envelope(changed_attestation.get_json(), error_code="conflict")

    _grant_decision_authority(scope)
    decline = client.post(
        f"{base}/evidence-requests/{risk_request_id}/decline",
        json={"reason": "The source owner cannot provide the evidence."},
        headers={
            "Idempotency-Key": f"api-decline-{uuid.uuid4().hex}",
            "If-Match": "1",
        },
    )
    assert decline.status_code == 200, decline.get_json()
    waiver_revision = decline.get_json()["data"]["revision"]

    expiry = datetime.now(timezone.utc) + timedelta(seconds=3)
    waiver_key = f"api-waiver-{uuid.uuid4().hex}"
    waiver_body = {
        "reason": "Proceed temporarily under named accountability.",
        "expires_at": expiry.isoformat(),
        "interim_accountable_id": scope.actor_id,
    }
    waiver = client.post(
        f"{base}/evidence-requests/{risk_request_id}/waiver",
        json=waiver_body,
        headers={
            "Idempotency-Key": waiver_key,
            "If-Match": str(waiver_revision),
        },
    )
    assert waiver.status_code == 201, waiver.get_json()
    time.sleep(max(0.0, (expiry - datetime.now(timezone.utc)).total_seconds()) + 0.2)

    db.session.remove()
    login_as(client, scope.actor_id)
    waiver_replay = client.post(
        f"{base}/evidence-requests/{risk_request_id}/waiver",
        json=waiver_body,
        headers={
            "Idempotency-Key": waiver_key,
            "If-Match": str(waiver_revision),
        },
    )
    assert waiver_replay.status_code == 200, waiver_replay.get_json()
    assert waiver_replay.get_json()["data"] == waiver.get_json()["data"]
    assert waiver_replay.get_json()["meta"]["idempotent"] is True

    changed_waiver = dict(waiver_body)
    changed_waiver["reason"] = "A changed body must conflict with the receipt."
    db.session.remove()
    login_as(client, scope.actor_id)
    waiver_conflict = client.post(
        f"{base}/evidence-requests/{risk_request_id}/waiver",
        json=changed_waiver,
        headers={
            "Idempotency-Key": waiver_key,
            "If-Match": str(waiver_revision),
        },
    )
    assert waiver_conflict.status_code == 409
    _assert_envelope(waiver_conflict.get_json(), error_code="conflict")


def test_evidence_http_lifecycle_uses_real_observation_conflict_and_request_services(
    client, login_as, evidence_scope
):
    from app.models.transformation_evidence import EvidenceRequest

    scope = evidence_scope
    programme_id = _programme_id(scope.workstream_id, scope.organization_id)
    base = f"{BASE}/{programme_id}/workstreams/{scope.workstream_id}"
    login_as(client, scope.actor_id)

    observation_key = f"api-observation-{uuid.uuid4().hex}"
    observation_body = {
        "claim_key": "application_owner",
        "adapter_key": "application-inventory",
        "source_key": str(scope.application_id),
    }
    observation = client.post(
        f"{base}/candidates/{scope.candidate_id}/evidence-observations",
        json=observation_body,
        headers={"Idempotency-Key": observation_key, "If-Match": "0"},
    )
    observation_replay = client.post(
        f"{base}/candidates/{scope.candidate_id}/evidence-observations",
        json=observation_body,
        headers={"Idempotency-Key": observation_key, "If-Match": "0"},
    )
    assert observation.status_code == 201, observation.get_json()
    assert observation_replay.status_code == 200, observation_replay.get_json()
    observed_evidence_id = observation.get_json()["data"]["evidence_record_id"]

    active = client.get(
        f"{base}/evidence",
        query_string={
            "subject_type": "application",
            "subject_id": scope.application_id,
        },
    )
    assert active.status_code == 200, active.get_json()
    assert observed_evidence_id in {
        row["id"] for row in active.get_json()["data"]["evidence"]
    }

    plan_key = f"api-plan-{uuid.uuid4().hex}"
    plan_body = {
        "assignments": {
            claim: scope.actor_id for claim in REQUIRED_EVIDENCE_CLAIMS
        }
    }
    planned = client.post(
        f"{base}/candidates/{scope.candidate_id}/evidence-requests",
        json=plan_body,
        headers={"Idempotency-Key": plan_key},
    )
    planned_replay = client.post(
        f"{base}/candidates/{scope.candidate_id}/evidence-requests",
        json=plan_body,
        headers={"Idempotency-Key": plan_key},
    )
    assert planned.status_code == 201, planned.get_json()
    assert planned_replay.status_code == 200, planned_replay.get_json()
    assert len(planned.get_json()["data"]["request_ids"]) == len(
        REQUIRED_EVIDENCE_CLAIMS
    )

    with Session(db.engine) as session:
        request_ids = dict(
            session.execute(
                select(EvidenceRequest.claim_key, EvidenceRequest.id).where(
                    EvidenceRequest.organization_id == scope.organization_id,
                    EvidenceRequest.candidate_id == scope.candidate_id,
                )
            ).all()
        )

    owner_attestation = client.post(
        f"{base}/evidence-requests/{request_ids['application_owner']}/attestations",
        json={
            "value": {
                "value_type": "string",
                "value": "A different accountable owner",
            }
        },
        headers={
            "Idempotency-Key": f"api-owner-conflict-{uuid.uuid4().hex}",
            "If-Match": "0",
        },
    )
    assert owner_attestation.status_code == 201, owner_attestation.get_json()
    owner_data = owner_attestation.get_json()["data"]
    assert owner_data["conflict_evidence_id"]

    _grant_decision_authority(scope)
    resolution_key = f"api-resolution-{uuid.uuid4().hex}"
    resolution_body = {
        "governing_evidence_id": owner_data["evidence_record_id"],
        "rationale": "The accountable owner confirmed the current assignment.",
    }
    resolution_url = (
        f"{base}/evidence/{owner_data['conflict_evidence_id']}/resolution"
    )
    resolved = client.post(
        resolution_url,
        json=resolution_body,
        headers={"Idempotency-Key": resolution_key},
    )
    resolved_replay = client.post(
        resolution_url,
        json=resolution_body,
        headers={"Idempotency-Key": resolution_key},
    )
    assert resolved.status_code == 201, resolved.get_json()
    assert resolved_replay.status_code == 200, resolved_replay.get_json()

    lifecycle_attestation = client.post(
        f"{base}/evidence-requests/{request_ids['lifecycle']}/attestations",
        json={"value": {"value_type": "string", "value": "active"}},
        headers={
            "Idempotency-Key": f"api-lifecycle-submit-{uuid.uuid4().hex}",
            "If-Match": "0",
        },
    )
    assert lifecycle_attestation.status_code == 201, lifecycle_attestation.get_json()
    lifecycle_data = lifecycle_attestation.get_json()["data"]
    acceptance_key = f"api-acceptance-{uuid.uuid4().hex}"
    acceptance_body = {"evidence_id": lifecycle_data["evidence_record_id"]}
    acceptance_url = (
        f"{base}/evidence-requests/{request_ids['lifecycle']}/acceptance"
    )
    accepted = client.post(
        acceptance_url,
        json=acceptance_body,
        headers={"Idempotency-Key": acceptance_key, "If-Match": "2"},
    )
    accepted_replay = client.post(
        acceptance_url,
        json=acceptance_body,
        headers={"Idempotency-Key": acceptance_key, "If-Match": "2"},
    )
    assert accepted.status_code == 200, accepted.get_json()
    assert accepted_replay.status_code == 200, accepted_replay.get_json()
    assert accepted_replay.get_json()["meta"]["idempotent"] is True

    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
        connection.execute(
            EvidenceRequest.__table__.update()
            .where(
                EvidenceRequest.id == request_ids["cost"],
                EvidenceRequest.organization_id == scope.organization_id,
            )
            .values(due_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        )
    expiry_key = f"api-expiry-{uuid.uuid4().hex}"
    expiry_url = f"{base}/evidence-requests/{request_ids['cost']}/expiry"
    expired = client.post(
        expiry_url,
        json={},
        headers={"Idempotency-Key": expiry_key, "If-Match": "1"},
    )
    expired_replay = client.post(
        expiry_url,
        json={},
        headers={"Idempotency-Key": expiry_key, "If-Match": "1"},
    )
    assert expired.status_code == 200, expired.get_json()
    assert expired_replay.status_code == 200, expired_replay.get_json()
    assert expired_replay.get_json()["meta"]["idempotent"] is True


def test_options_brief_and_arb_routes_execute_the_production_command_chain(
    client, login_as, decision_scope
):
    scope = decision_scope
    programme_id = _programme_id(scope.workstream_id, scope.organization_id)
    base = f"{BASE}/{programme_id}/workstreams/{scope.workstream_id}"
    login_as(client, scope.actor_id)

    values = _option_values(
        scope, title="Retire", action_type="retire", ordinal=3
    )
    draft = {
        key: value
        for key, value in values.items()
        if key
        not in {"organization_id", "workstream_id", "candidate_id", "revision"}
    }
    for field in (
        "cost_min",
        "cost_max",
        "benefit_min",
        "benefit_max",
        "risk_min",
        "risk_max",
    ):
        draft[field] = str(draft[field])
    draft["impacts"][0].update(
        {
            "status": "planned",
            "roles": ["service-owner"],
            "request_id": "impact-source-42",
        }
    )
    option_body = {"candidate_id": scope.candidate_id, "draft": draft}
    option_key = f"api-option-{uuid.uuid4().hex}"
    option = client.post(
        f"{base}/options",
        json=option_body,
        headers={"Idempotency-Key": option_key},
    )
    option_replay = client.post(
        f"{base}/options",
        json=option_body,
        headers={"Idempotency-Key": option_key},
    )
    assert option.status_code == 201, option.get_json()
    assert option_replay.status_code == 200, option_replay.get_json()
    assert option_replay.get_json()["meta"]["idempotent"] is True
    created_option_id = option.get_json()["data"]["option_id"]

    version_ids = []
    for ordinal, option_id in enumerate(
        (*scope.option_ids, created_option_id), start=1
    ):
        freeze_key = f"api-option-freeze-{ordinal}-{uuid.uuid4().hex}"
        frozen = client.post(
            f"{base}/options/{option_id}/versions",
            json={},
            headers={"Idempotency-Key": freeze_key, "If-Match": "1"},
        )
        assert frozen.status_code == 201, frozen.get_json()
        version_ids.append(frozen.get_json()["data"]["option_version_id"])
        if ordinal == 1:
            frozen_replay = client.post(
                f"{base}/options/{option_id}/versions",
                json={},
                headers={"Idempotency-Key": freeze_key, "If-Match": "1"},
            )
            assert frozen_replay.status_code == 200, frozen_replay.get_json()
            assert frozen_replay.get_json()["meta"]["idempotent"] is True

    comparison = client.get(
        f"{base}/option-comparison",
        query_string=[("option_version_id", version_id) for version_id in version_ids],
    )
    assert comparison.status_code == 200, comparison.get_json()
    assert comparison.get_json()["data"]["option_version_ids"] == version_ids

    _remove_fixture_brief(scope)
    brief_body = {
        "candidate_id": scope.candidate_id,
        "title": "Application rationalisation decision",
        "recommendation_option_id": scope.option_ids[1],
        "decision_authority_id": scope.actor_id,
        "unknown_codes": ["cost_source_unknown"],
        "conflicts": ["Operational cutover window requires confirmation"],
        "expected_impacts": ["Lower run cost after controlled migration"],
    }
    brief_key = f"api-brief-{uuid.uuid4().hex}"
    brief = client.post(
        f"{base}/decision-briefs",
        json=brief_body,
        headers={"Idempotency-Key": brief_key},
    )
    brief_replay = client.post(
        f"{base}/decision-briefs",
        json=brief_body,
        headers={"Idempotency-Key": brief_key},
    )
    assert brief.status_code == 201, brief.get_json()
    assert brief_replay.status_code == 200, brief_replay.get_json()
    brief_id = brief.get_json()["data"]["decision_brief_id"]

    readiness = client.get(f"{base}/decision-briefs/{brief_id}/readiness")
    assert readiness.status_code == 200, readiness.get_json()
    assert readiness.get_json()["data"]["ready"] is True

    assertions = {
        "reviewed_ai_material": True,
        "acknowledged_unknown_codes": ["cost_source_unknown"],
        "acknowledged_superseded_evidence_ids": [],
        "rationale": "A human reviewed the evidence and recommendation.",
    }
    freeze_body = {
        "option_version_ids": version_ids,
        "evidence_ids": [scope.evidence_id],
        "assertions": assertions,
    }
    freeze_key = f"api-brief-freeze-{uuid.uuid4().hex}"
    frozen_brief = client.post(
        f"{base}/decision-briefs/{brief_id}/versions",
        json=freeze_body,
        headers={"Idempotency-Key": freeze_key, "If-Match": "1"},
    )
    frozen_brief_replay = client.post(
        f"{base}/decision-briefs/{brief_id}/versions",
        json=freeze_body,
        headers={"Idempotency-Key": freeze_key, "If-Match": "1"},
    )
    assert frozen_brief.status_code == 201, frozen_brief.get_json()
    assert frozen_brief_replay.status_code == 200, frozen_brief_replay.get_json()
    assert frozen_brief_replay.get_json()["meta"]["idempotent"] is True

    changed_freeze = {
        **freeze_body,
        "assertions": {
            **assertions,
            "rationale": "A changed body under the same receipt key.",
        },
    }
    freeze_conflict = client.post(
        f"{base}/decision-briefs/{brief_id}/versions",
        json=changed_freeze,
        headers={"Idempotency-Key": freeze_key, "If-Match": "1"},
    )
    assert freeze_conflict.status_code == 409

    blocked_submission = client.post(
        f"{base}/decision-briefs/{brief_id}/arb-submissions",
        json={"assertions": {"human_reviewed": False}},
        headers={"Idempotency-Key": f"api-arb-blocked-{uuid.uuid4().hex}"},
    )
    assert blocked_submission.status_code == 422, blocked_submission.get_json()
    _assert_envelope(blocked_submission.get_json(), error_code="blocked_by_evidence")

    submission_key = f"api-arb-{uuid.uuid4().hex}"
    submission_body = {"assertions": {"human_reviewed": True}}
    submitted = client.post(
        f"{base}/decision-briefs/{brief_id}/arb-submissions",
        json=submission_body,
        headers={"Idempotency-Key": submission_key},
    )
    submitted_replay = client.post(
        f"{base}/decision-briefs/{brief_id}/arb-submissions",
        json=submission_body,
        headers={"Idempotency-Key": submission_key},
    )
    assert submitted.status_code == 201, submitted.get_json()
    assert submitted_replay.status_code == 200, submitted_replay.get_json()
    assert submitted_replay.get_json()["meta"]["idempotent"] is True


def test_execution_solution_export_and_outcome_routes_use_production_services(
    client, login_as, committed_execution_scope, monkeypatch
):
    scope = committed_execution_scope
    base = f"{BASE}/{scope.programme_id}/workstreams/{scope.workstream_id}"
    login_as(client, scope.actor.user_id)

    action = scope.action
    materialise_body = {
        "actions": [
            {
                "action_key": action.action_key,
                "option_version_id": action.option_version_id,
                "title": action.title,
                "owner_id": action.owner_id,
                "start_date": action.start_date.isoformat(),
                "target_date": action.target_date.isoformat(),
                "scheduling_applicable": action.scheduling_applicable,
            }
        ]
    }
    materialise_url = (
        f"{base}/decision-brief-versions/{scope.decision_brief_version_id}/execution"
    )
    materialise_key = f"api-materialise-{uuid.uuid4().hex}"
    materialised = client.post(
        materialise_url,
        json=materialise_body,
        headers={"Idempotency-Key": materialise_key},
    )
    materialised_replay = client.post(
        materialise_url,
        json=materialise_body,
        headers={"Idempotency-Key": materialise_key},
    )
    assert materialised.status_code == 201, materialised.get_json()
    assert materialised_replay.status_code == 200, materialised_replay.get_json()
    assert materialised_replay.get_json()["data"] == materialised.get_json()["data"]
    assert materialised_replay.get_json()["meta"]["idempotent"] is True
    work_package_id = materialised.get_json()["data"]["work_package_ids"][0]
    benefit_id = materialised.get_json()["data"]["benefit_ids"][0]

    changed_materialise = {
        "actions": [{**materialise_body["actions"][0], "title": "Changed action"}]
    }
    conflict = client.post(
        materialise_url,
        json=changed_materialise,
        headers={"Idempotency-Key": materialise_key},
    )
    assert conflict.status_code == 409

    solution_url = (
        f"{base}/decision-brief-versions/{scope.decision_brief_version_id}"
        "/technology-solutions"
    )
    solution_body = {"option_version_id": scope.option_version_id}
    solution_key = f"api-solution-{uuid.uuid4().hex}"
    solution = client.post(
        solution_url,
        json=solution_body,
        headers={"Idempotency-Key": solution_key},
    )
    solution_replay = client.post(
        solution_url,
        json=solution_body,
        headers={"Idempotency-Key": solution_key},
    )
    assert solution.status_code == 201, solution.get_json()
    assert solution_replay.status_code == 200, solution_replay.get_json()
    assert solution_replay.get_json()["meta"]["idempotent"] is True

    provider_calls = []

    def unavailable_provider(work_package, provider_request, provider_key):
        provider_calls.append((work_package.id, dict(provider_request), provider_key))
        raise ConnectionError("provider unavailable")

    providers = {"delivery-provider": unavailable_provider}
    monkeypatch.setitem(
        client.application.extensions,
        "transformation_delivery_exporters",
        providers,
    )
    export_url = f"{base}/work-packages/{work_package_id}/delivery-exports"
    export_body = {
        "provider_key": "delivery-provider",
        "request": {
            "project": "ARCH",
            "status": "To Do",
            "roles": ["delivery"],
            "request_id": "provider-request-42",
        },
    }
    export_key = f"api-export-{uuid.uuid4().hex}"
    failed_export = client.post(
        export_url,
        json=export_body,
        headers={"Idempotency-Key": export_key},
    )
    failed_export_replay = client.post(
        export_url,
        json=export_body,
        headers={"Idempotency-Key": export_key},
    )
    assert failed_export.status_code == 502, failed_export.get_json()
    assert failed_export_replay.status_code == 502, failed_export_replay.get_json()
    _assert_envelope(failed_export.get_json(), error_code="provider_failed")
    assert failed_export_replay.get_json()["meta"]["idempotent"] is True
    assert len(provider_calls) == 1
    failed_attempt_id = failed_export.get_json()["meta"][
        "delivery_export_attempt_id"
    ]

    changed_export = {
        **export_body,
        "request": {**export_body["request"], "project": "DIFFERENT"},
    }
    changed_export_response = client.post(
        export_url,
        json=changed_export,
        headers={"Idempotency-Key": export_key},
    )
    assert changed_export_response.status_code == 409
    assert len(provider_calls) == 1

    def available_provider(work_package, provider_request, provider_key):
        provider_calls.append((work_package.id, dict(provider_request), provider_key))
        return {"external_key": "ARCH-42"}

    providers["delivery-provider"] = available_provider
    retry_url = f"{export_url}/{failed_attempt_id}/retries"
    retry_key = f"api-export-retry-{uuid.uuid4().hex}"
    retried = client.post(
        retry_url,
        json=export_body,
        headers={"Idempotency-Key": retry_key},
    )
    retried_replay = client.post(
        retry_url,
        json=export_body,
        headers={"Idempotency-Key": retry_key},
    )
    assert retried.status_code == 201, retried.get_json()
    assert retried.get_json()["data"]["external_key"] == "ARCH-42"
    assert retried_replay.status_code == 200, retried_replay.get_json()
    assert retried_replay.get_json()["meta"]["idempotent"] is True
    assert len(provider_calls) == 2

    login_as(client, scope.outcome_actor.user_id)
    measurement_url = f"{base}/benefits/{benefit_id}/measurements"
    measurement_body = {
        "value": "700.00",
        "unavailable_reason": None,
        "observed_at": "2026-08-29T12:00:00+00:00",
        "source_identity": "finance-ledger:run-cost",
        "source_version": "ledger-v42",
    }
    measurement_key = f"api-outcome-{uuid.uuid4().hex}"
    measured = client.post(
        measurement_url,
        json=measurement_body,
        headers={"Idempotency-Key": measurement_key},
    )
    measured_replay = client.post(
        measurement_url,
        json=measurement_body,
        headers={"Idempotency-Key": measurement_key},
    )
    assert measured.status_code == 201, measured.get_json()
    assert measured_replay.status_code == 200, measured_replay.get_json()
    assert measured_replay.get_json()["meta"]["idempotent"] is True

    changed_measurement = {**measurement_body, "value": "701.00"}
    measurement_conflict = client.post(
        measurement_url,
        json=changed_measurement,
        headers={"Idempotency-Key": measurement_key},
    )
    assert measurement_conflict.status_code == 409


def test_transformation_api_rejects_server_owned_identity_and_status(
    client, committed_session, login_as
):
    session, cleanup_org_ids = committed_session
    org = _make_org(session, cleanup_org_ids, "owned-fields")
    owner = _make_user(session, org)
    session.commit()
    # Read while the instance is still attached: the session is removed between
    # requests below and a committed instance cannot be refreshed once detached.
    org_id = org.id
    login_as(client, owner)

    legitimate = _intake(owner.id)
    legitimate["scope_expression"] = {
        "portfolio_filter": {
            "status": "operational",
            "roles": ["customer-facing"],
            "request_id": "portfolio-source-request-17",
        }
    }
    accepted = client.post(
        BASE,
        json=legitimate,
        headers={"Idempotency-Key": f"nested-domain-fields-{uuid.uuid4().hex}"},
    )
    assert accepted.status_code == 201, accepted.get_json()

    db.session.remove()
    login_as(client, owner)
    payload = _intake(owner.id)
    payload.update(
        {
            "organization_id": org_id,
            "created_by_id": owner.id,
            "status": "approved",
            "roles": ["platform_admin"],
        }
    )

    response = client.post(
        BASE,
        json=payload,
        headers={"Idempotency-Key": f"owned-fields-{uuid.uuid4().hex}"},
    )

    assert response.status_code == 400
    body = response.get_json()
    _assert_envelope(body, error_code="validation_failed")
    assert set(body["errors"][0]["details"]["fields"]) == {
        "created_by_id",
        "organization_id",
        "roles",
        "status",
    }


def test_transformation_api_stale_if_match_is_conflict_and_foreign_id_is_opaque(
    client, committed_session, login_as, monkeypatch
):
    from app import db

    monkeypatch.setenv("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "74" * 32)
    session, cleanup_org_ids = committed_session
    _ensure_guards(session)
    org = _make_org(session, cleanup_org_ids, "tenant-a")
    foreign_org = _make_org(session, cleanup_org_ids, "tenant-b")
    owner = _make_user(session, org)
    foreign_user = _make_user(session, foreign_org)
    session.commit()
    foreign_user_id = foreign_user.id
    login_as(client, owner)
    created = client.post(
        BASE,
        json=_intake(owner.id),
        headers={"Idempotency-Key": f"tenant-boundary-{uuid.uuid4().hex}"},
    ).get_json()["data"]
    programme_id = created["programme_id"]
    workstream_id = created["workstream_id"]

    db.session.remove()
    login_as(client, owner)
    updated = client.patch(
        f"{BASE}/{programme_id}/workstreams/{workstream_id}/objective",
        json={"objective": "A newly governed objective", "scope_expression": {"regions": ["UK"]}},
        headers={"Idempotency-Key": f"objective-{uuid.uuid4().hex}", "If-Match": '"1"'},
    )
    assert updated.status_code == 200, updated.get_json()
    assert updated.get_json()["data"]["revision"] == 2

    db.session.remove()
    login_as(client, owner)
    stale = client.patch(
        f"{BASE}/{programme_id}/workstreams/{workstream_id}/objective",
        json={"objective": "A stale objective", "scope_expression": {}},
        headers={"Idempotency-Key": f"stale-{uuid.uuid4().hex}", "If-Match": "1"},
    )
    assert stale.status_code == 409
    _assert_envelope(stale.get_json(), error_code="conflict")

    db.session.remove()
    login_as(client, foreign_user_id)
    foreign = client.get(f"{BASE}/{programme_id}")
    assert foreign.status_code == 404
    foreign_body = foreign.get_json()
    _assert_envelope(foreign_body, error_code="not_found")
    assert str(programme_id) not in foreign_body["errors"][0]["message"]


def test_transformation_api_does_not_accept_a_forged_role_header(
    client, committed_session, login_as, monkeypatch
):
    from app.security.audit import audit_logger

    audited = []
    monkeypatch.setattr(
        audit_logger,
        "log_security_event",
        lambda event_type, severity, details: audited.append(
            (event_type, severity, details)
        ),
    )
    session, cleanup_org_ids = committed_session
    org = _make_org(session, cleanup_org_ids, "forged-role")
    user = _make_user(session, org, role="procurement")
    session.commit()
    login_as(client, user)

    response = client.post(
        BASE,
        json=_intake(user.id),
        headers={
            "Idempotency-Key": f"forged-role-{uuid.uuid4().hex}",
            "X-Enterprise-Role": "platform_admin",
            "X-Roles": "platform_admin,chief_architect",
        },
    )

    assert response.status_code == 403
    _assert_envelope(response.get_json(), error_code="not_authorised")
    assert len(audited) == 1
    details = audited[0][2]
    assert details == {
        "actor_id": user.id,
        "endpoint": "transformation_api.create_programme",
        "reason_code": "programme_create_not_authorised",
        "request_id": response.get_json()["request_id"],
        "tenant_id": org.id,
    }


def test_transformation_api_resolves_current_workstream_assignment_server_side(
    client, committed_session, login_as, monkeypatch
):
    from app import db

    monkeypatch.setenv("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "74" * 32)
    session, cleanup_org_ids = committed_session
    _ensure_guards(session)
    org = _make_org(session, cleanup_org_ids, "assignment")
    owner = _make_user(session, org)
    assigned_user = _make_user(session, org, role="procurement")
    session.commit()
    owner_id = owner.id
    assigned_user_id = assigned_user.id
    login_as(client, owner_id)
    created = client.post(
        BASE,
        json=_intake(owner_id),
        headers={"Idempotency-Key": f"assignment-programme-{uuid.uuid4().hex}"},
    ).get_json()["data"]

    db.session.remove()
    login_as(client, owner_id)
    assigned = client.post(
        f"{BASE}/{created['programme_id']}/role-assignments",
        json={
            "workstream_id": created["workstream_id"],
            "user_id": assigned_user_id,
            "role": "workstream_lead",
            "effective_from": "2026-08-29",
            "effective_to": None,
        },
        headers={
            "Idempotency-Key": f"assignment-{uuid.uuid4().hex}",
            "If-Match": "1",
        },
    )
    assert assigned.status_code == 201, assigned.get_json()
    assert assigned.get_json()["data"]["revision"] == 2

    db.session.remove()
    login_as(client, assigned_user_id)
    updated = client.patch(
        f"{BASE}/{created['programme_id']}/workstreams/{created['workstream_id']}/objective",
        json={"objective": "Owned by the assigned workstream lead", "scope_expression": {}},
        headers={
            "Idempotency-Key": f"assigned-objective-{uuid.uuid4().hex}",
            "If-Match": "2",
            "X-Roles": "",
        },
    )
    assert updated.status_code == 200, updated.get_json()
    assert updated.get_json()["data"]["revision"] == 3


def test_transformation_api_maps_real_gate_blockers_exactly(
    client, committed_session, login_as
):
    from app import db

    session, cleanup_org_ids = committed_session
    _ensure_guards(session)
    org = _make_org(session, cleanup_org_ids, "error-map")
    user = _make_user(session, org)
    session.commit()
    user_id = user.id
    login_as(client, user_id)
    intake = _intake(user_id)
    intake["scope_expression"] = {}
    created = client.post(
        BASE,
        json=intake,
        headers={"Idempotency-Key": f"error-map-programme-{uuid.uuid4().hex}"},
    ).get_json()["data"]

    db.session.remove()
    login_as(client, user_id)
    blocked = client.post(
        f"{BASE}/{created['programme_id']}/workstreams/{created['workstream_id']}/transitions",
        json={"target_stage": "discover"},
        headers={
            "Idempotency-Key": f"blocked-map-{uuid.uuid4().hex}",
            "If-Match": "1",
        },
    )
    assert blocked.status_code == 422
    blocked_body = blocked.get_json()
    _assert_envelope(blocked_body, error_code="blocked_by_evidence")
    blocker_codes = {
        item["code"] for item in blocked_body["errors"][0]["details"]["blockers"]
    }
    assert "scope_required" in blocker_codes


def test_transformation_api_write_surface_remains_csrf_protected(app):
    from app._bootstrap.csrf_coverage import audit

    result = audit(app)
    transformation_writes = [
        entry
        for entry in result["protected"]
        if entry["rule"].startswith(BASE)
    ]
    expected_writes = sum(
        1 for _rule, method in ROUTE_CONTRACT if method in {"POST", "PATCH", "DELETE"}
    )
    assert len(transformation_writes) == expected_writes
    assert not [
        entry
        for entry in result["exempt_allowed"] + result["exempt_unjustified"]
        if entry["rule"].startswith(BASE)
    ]


def test_transformation_api_repeated_foreign_id_probes_use_established_limiter(
    client, committed_session, login_as, monkeypatch
):
    from app.security.audit import audit_logger

    audited = []
    monkeypatch.setattr(
        audit_logger,
        "log_security_event",
        lambda event_type, severity, details: audited.append(
            (event_type, severity, details)
        ),
    )
    session, cleanup_org_ids = committed_session
    _ensure_guards(session)
    org = _make_org(session, cleanup_org_ids, "probe-limit")
    foreign_org = _make_org(session, cleanup_org_ids, "probe-limit-foreign")
    user = _make_user(session, org)
    foreign_user = _make_user(session, foreign_org)
    session.commit()
    user_id = user.id
    foreign_user_id = foreign_user.id

    login_as(client, user_id)
    local_programme = client.post(
        BASE,
        json=_intake(user_id),
        headers={"Idempotency-Key": f"probe-local-{uuid.uuid4().hex}"},
    ).get_json()["data"]["programme_id"]
    db.session.remove()
    login_as(client, foreign_user_id)
    foreign_programme = client.post(
        BASE,
        json=_intake(foreign_user_id),
        headers={"Idempotency-Key": f"probe-foreign-{uuid.uuid4().hex}"},
    ).get_json()["data"]["programme_id"]

    db.session.remove()
    login_as(client, user_id)
    monkeypatch.setitem(client.application.config, "RATE_LIMITING_ENABLED", True)
    monkeypatch.setitem(
        client.application.config, "TRANSFORMATION_FOREIGN_ID_PROBE_LIMIT", 2
    )

    successful = [client.get(f"{BASE}/{local_programme}") for _ in range(4)]
    foreign = client.get(f"{BASE}/{foreign_programme}")
    missing = client.get(f"{BASE}/2147483001")
    limited = client.get(f"{BASE}/2147483002")

    assert [response.status_code for response in successful] == [200, 200, 200, 200]
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert limited.status_code == 429
    body = limited.get_json()
    _assert_envelope(body, error_code="retryable_failure")
    assert body["meta"]["retry_after"] >= 1
    assert [event[2]["reason_code"] for event in audited] == [
        "programme_not_found",
        "programme_not_found",
        "identifier_probe_rate_limited",
    ]
    assert all(
        set(event[2])
        == {"actor_id", "endpoint", "reason_code", "request_id", "tenant_id"}
        for event in audited
    )
