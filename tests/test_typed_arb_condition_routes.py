"""HTTP contract for the typed ARB condition endpoints.

`typed-arb-route-audit.md` recorded that no HTTP/template/JS route called the
typed condition evidence or lifecycle services. These tests pin the ingress that
closes that gap, against `typed-arb-ui-blueprint.md` §9/§11/§13.

Fixture note
------------
These use the shared ``app``, ``make_org``, ``client`` and ``login_as`` fixtures
from ``tests/conftest.py``. ``db_session`` is overridden here for one specific,
mechanical reason: ``CommandService`` opens its **own** database sessions to
claim and fence a command, so it cannot see rows held in the shared fixture's
never-committed outer transaction. Setup therefore has to commit, and this
fixture takes the cleanup responsibility that the rollback would otherwise have
had — the same shape ``tests/test_arb_condition_lifecycle_integration.py``
already uses for the same reason.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import uuid

import pytest


os.environ["TRANSFORMATION_COMMAND_CAPABILITY_SECRET"] = "74" * 32


@pytest.fixture
def db_session(app, _schema):
    """Committed setup visible to CommandService's independent sessions."""
    from app import db

    with app.app_context():
        db.session.remove()
        cleanup_org_ids = set()
        db.session.info["cleanup_org_ids"] = cleanup_org_ids
        try:
            yield db.session
        finally:
            organization_ids = tuple(cleanup_org_ids)
            db.session.remove()
            if organization_ids:
                from app.models.organization import Organization
                from app.services.org_deletion_service import OrganizationDeletionService

                raw = db.engine.raw_connection()
                try:
                    with raw.cursor() as cursor:
                        # archie_command_claim_challenges carries organization_id with no
                        # foreign key to organizations (a deliberate, trigger-free,
                        # TTL-cleaned table), so the deletion service never reaches it.
                        cursor.execute(
                            "DELETE FROM archie_command_claim_challenges "
                            "WHERE organization_id = ANY(%s)",
                            (list(organization_ids),),
                        )
                        raw.commit()
                finally:
                    raw.close()
                for org_id in organization_ids:
                    if db.session.get(Organization, org_id) is not None:
                        OrganizationDeletionService.delete_organization(org_id)
                db.session.remove()


def _install_guards(db_session):
    from app.models.arb_condition_event import ensure_arb_condition_event_guards
    from app.models.arb_condition_evidence import ensure_arb_condition_evidence_guards
    from app.models.arb_decision_event import ensure_arb_decision_guards
    from app.models.architecture_review_board import ensure_arb_cycle_constraints
    from app.models.transformation_db_guards import ensure_transformation_db_guards

    connection = db_session.connection()
    ensure_transformation_db_guards(connection, capability_secrets=("74" * 32,))
    ensure_arb_cycle_constraints(connection)
    ensure_arb_decision_guards(connection)
    ensure_arb_condition_evidence_guards(connection)
    ensure_arb_condition_event_guards(connection)


class _Fixture:
    """The seeded typed graph plus its actors, as plain identifiers."""

    def __init__(self, **values):
        self.__dict__.update(values)


def _seed(db_session, make_org, label, *, conditions=2):
    """Seed one tenant with a submitted ADR approved with conditions."""
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.user import User
    from app.modules.transformation_room.arb_decision_service import (
        TypedARBDecisionService,
    )
    from app.modules.transformation_room.arb_submission_service import (
        TypedARBSubmissionService,
    )
    from app.modules.transformation_room.domain import ActorContext

    org = make_org(label)
    db_session.info.setdefault("cleanup_org_ids", set()).add(org.id)
    suffix = uuid.uuid4().hex[:10]
    submitter = User(
        organization_id=org.id,
        email=f"submit-{suffix}@example.test",
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    authority = User(
        organization_id=org.id,
        email=f"authority-{suffix}@example.test",
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    bystander = User(
        organization_id=org.id,
        email=f"bystander-{suffix}@example.test",
        enterprise_role="business_analyst",
        confirmed=True,
    )
    db_session.add_all((submitter, authority, bystander))
    db_session.flush()
    adr = ArchitectureDecision(
        organization_id=org.id,
        decision_id=f"AD-{suffix[:7]}",
        title=f"Typed condition ingress {suffix}",
        status="proposed",
        context="A governed choice needs evidence.",
        decision="Adopt the governed option.",
        rationale="It is testable.",
        consequences="Conditions must be verified.",
        created_by_id=submitter.id,
    )
    db_session.add(adr)
    db_session.commit()

    submitter_actor = ActorContext(
        submitter.id, org.id, frozenset(), f"seed-submit-{suffix}"
    )
    authority_actor = ActorContext(
        authority.id, org.id, frozenset(), f"seed-authority-{suffix}"
    )
    submission = TypedARBSubmissionService.submit(
        actor=submitter_actor,
        command_key=f"submit-{suffix}",
        subject_type="adr",
        subject_id=adr.id,
        assertions={"human_reviewed": True},
    )
    decision = TypedARBDecisionService.decide(
        actor=authority_actor,
        command_key=f"decide-{suffix}",
        cycle_id=submission.object_ids["review_cycle_id"],
        outcome="approved_with_conditions",
        rationale="Approved with proof required.",
        conditions=[
            {"code": f"C-{index + 1}", "text": f"Provide proof {index + 1}."}
            for index in range(conditions)
        ],
    )
    return _Fixture(
        org_id=org.id,
        suffix=suffix,
        submitter_id=submitter.id,
        authority_id=authority.id,
        bystander_id=bystander.id,
        review_cycle_id=submission.object_ids["review_cycle_id"],
        review_item_id=submission.object_ids["review_item_id"],
        condition_ids=list(decision.object_ids["condition_ids"]),
    )


def _now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


def _attestation_body(observed=None):
    return {
        "mode": "manual_attestation",
        "statement": "The control was executed and witnessed.",
        "observed_at": _iso(observed or (_now() - timedelta(minutes=5))),
    }


def _source_body(*, expires_in=timedelta(days=1)):
    return {
        "mode": "source_backed",
        "source_identity": "cmdb:service-1234",
        "source_type": "cmdb",
        "source_version": "7",
        "observed_at": _iso(_now() - timedelta(minutes=5)),
        "expires_at": _iso(_now() + expires_in),
        "value": {"encryption_at_rest": True},
    }


def _waiver_body(**overrides):
    body = {
        "reason": "Time-bound risk acceptance pending the platform upgrade.",
        "expires_at": _iso(_now() + timedelta(days=30)),
        "scope": "The reporting service only.",
        "compensating_control": "Daily manual reconciliation by the duty architect.",
    }
    body.update(overrides)
    return body


def _capture_url(condition_id):
    return f"/arb/api/conditions/{condition_id}/evidence"


def _submit_url(condition_id, evidence_id):
    return f"/arb/api/conditions/{condition_id}/evidence/{evidence_id}/submit"


def _verify_url(condition_id, evidence_id):
    return f"/arb/api/conditions/{condition_id}/evidence/{evidence_id}/verify"


def _waive_url(condition_id):
    return f"/arb/api/conditions/{condition_id}/waive"


# ── registration ─────────────────────────────────────────────────────────────


def test_typed_condition_routes_are_registered_in_the_url_map(app):
    """Blueprints register non-fatally, so an import error is only logged.

    Assert the rules and endpoints are genuinely present rather than trusting a
    clean boot log.
    """
    rules = {
        rule.rule: (rule.endpoint, rule.methods)
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/arb/api/conditions/")
    }
    expected = {
        "/arb/api/conditions/<int:condition_id>/evidence":
            "arb_conditions_api.capture_condition_evidence",
        "/arb/api/conditions/<int:condition_id>/evidence/<int:evidence_id>/submit":
            "arb_conditions_api.submit_condition_evidence",
        "/arb/api/conditions/<int:condition_id>/evidence/<int:evidence_id>/verify":
            "arb_conditions_api.verify_condition_evidence",
        "/arb/api/conditions/<int:condition_id>/waive":
            "arb_conditions_api.waive_condition",
    }
    for rule, endpoint in expected.items():
        assert rule in rules, f"{rule} is missing from the URL map"
        assert rules[rule][0] == endpoint
        assert "POST" in rules[rule][1]


def test_typed_condition_routes_are_not_csrf_exempt(app):
    """§11: CSRF through the established mechanism, with no new exemption."""
    from app._bootstrap.csrf_coverage import audit

    exempt = {
        entry["dest"]
        for entry in audit(app)["exempt_allowed"] + audit(app)["exempt_unjustified"]
    }
    assert not {name for name in exempt if name.startswith("arb_conditions_api.")}


# ── authentication and tenancy ───────────────────────────────────────────────


def test_unauthenticated_capture_returns_401(client):
    response = client.post(_capture_url(1), json=_attestation_body())
    assert response.status_code == 401
    body = response.get_json()
    assert body["success"] is False
    assert body["reason_codes"] == ["not_authenticated"]
    assert body["request_id"]


def test_cross_tenant_condition_returns_404_revealing_nothing(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    owner = _seed(db_session, make_org, "owner")
    intruder = _seed(db_session, make_org, "intruder")
    login_as(client, intruder.submitter_id)

    for url, payload in (
        (_capture_url(owner.condition_ids[0]), _attestation_body()),
        (_waive_url(owner.condition_ids[0]), _waiver_body()),
        (_submit_url(owner.condition_ids[0], 1), {}),
        (_verify_url(owner.condition_ids[0], 1), {}),
    ):
        response = client.post(url, json=payload)
        assert response.status_code == 404, url
        body = response.get_json()
        assert body["reason_codes"] == ["arb_condition_not_found"]
        # Nothing about the owning tenant may appear in the failure.
        # request_id is opaque random correlation data. A short numeric database
        # id can occur inside its hex text by chance (for example cycle id 44 in
        # ``...2447...``), which is not a tenant leak. Inspect the response's
        # meaningful fields while still requiring the correlation id to exist.
        assert body.get("request_id")
        serialised = str({key: value for key, value in body.items() if key != "request_id"})
        assert str(owner.org_id) not in serialised
        assert str(owner.review_cycle_id) not in serialised
        assert "missing_evidence" not in body


def test_cross_tenant_evidence_id_returns_404(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    owner = _seed(db_session, make_org, "evidence-owner")
    intruder = _seed(db_session, make_org, "evidence-intruder")

    login_as(client, owner.submitter_id)
    captured = client.post(
        _capture_url(owner.condition_ids[0]), json=_attestation_body()
    )
    assert captured.status_code == 201
    foreign_evidence_id = captured.get_json()["condition_evidence_id"]

    login_as(client, intruder.submitter_id)
    response = client.post(
        _submit_url(intruder.condition_ids[0], foreign_evidence_id), json={}
    )
    assert response.status_code == 404
    assert response.get_json()["reason_codes"] == ["arb_condition_not_found"]


def test_evidence_belonging_to_another_condition_returns_409(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "membership")
    login_as(client, seeded.submitter_id)
    captured = client.post(
        _capture_url(seeded.condition_ids[0]), json=_attestation_body()
    )
    assert captured.status_code == 201
    evidence_id = captured.get_json()["condition_evidence_id"]

    login_as(client, seeded.submitter_id)
    response = client.post(
        _submit_url(seeded.condition_ids[1], evidence_id), json={}
    )
    assert response.status_code == 409
    assert response.get_json()["reason_codes"] == [
        "arb_condition_evidence_membership_mismatch"
    ]


# ── capture, submit, verify ──────────────────────────────────────────────────


def test_capture_then_submit_then_verify_with_idempotent_replays(
    app, db_session, make_org, client, login_as
):
    from app.models.arb_decision_event import ARBCondition

    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "happy-path")
    condition_id = seeded.condition_ids[0]

    # One body, replayed byte-for-byte: an idempotent replay is the *same*
    # command, not merely the same key.
    evidence_body = _source_body()

    login_as(client, seeded.submitter_id)
    captured = client.post(
        _capture_url(condition_id),
        json=evidence_body,
        headers={"Idempotency-Key": f"key-{seeded.suffix}"},
    )
    assert captured.status_code == 201
    capture_body = captured.get_json()
    assert capture_body["success"] is True
    assert capture_body["status"] == "captured"
    assert capture_body["lifecycle_transitioned"] is False
    assert capture_body["idempotent"] is False
    assert capture_body["condition_id"] == condition_id
    assert capture_body["review_cycle_id"] == seeded.review_cycle_id
    assert capture_body["condition_revision"] == 1
    evidence_id = capture_body["condition_evidence_id"]

    # Same client key replays capture without creating a second record.
    login_as(client, seeded.submitter_id)
    replay = client.post(
        _capture_url(condition_id),
        json=evidence_body,
        headers={"Idempotency-Key": f"key-{seeded.suffix}"},
    )
    assert replay.status_code == 200
    assert replay.get_json()["idempotent"] is True
    assert replay.get_json()["condition_evidence_id"] == evidence_id

    # Same key, different command: a replayed-different command is 409 (§13).
    login_as(client, seeded.submitter_id)
    divergent = client.post(
        _capture_url(condition_id),
        json=_source_body(),
        headers={"Idempotency-Key": f"key-{seeded.suffix}"},
    )
    assert divergent.status_code == 409
    assert divergent.get_json()["success"] is False

    login_as(client, seeded.submitter_id)
    submitted = client.post(
        _submit_url(condition_id, evidence_id),
        json={},
        headers={"Idempotency-Key": f"key-{seeded.suffix}"},
    )
    assert submitted.status_code == 200
    submit_body = submitted.get_json()
    assert submit_body["status"] == "evidence_submitted"
    assert submit_body["projection_status"] == "approved_with_conditions"
    assert submit_body["condition_event_id"]
    assert submit_body["review_item_id"] == seeded.review_item_id
    assert submit_body["idempotent"] is False

    login_as(client, seeded.submitter_id)
    submit_replay = client.post(
        _submit_url(condition_id, evidence_id),
        json={},
        headers={"Idempotency-Key": f"key-{seeded.suffix}"},
    )
    assert submit_replay.status_code == 200
    assert submit_replay.get_json()["idempotent"] is True
    assert (
        submit_replay.get_json()["condition_event_id"]
        == submit_body["condition_event_id"]
    )

    login_as(client, seeded.authority_id)
    verified = client.post(_verify_url(condition_id, evidence_id), json={})
    assert verified.status_code == 200
    verify_body = verified.get_json()
    assert verify_body["status"] == "fulfilled"
    assert verify_body["condition_event_id"] != submit_body["condition_event_id"]

    db_session.expire_all()
    assert db_session.get(ARBCondition, condition_id).status == "fulfilled"


def test_capture_and_submit_use_distinct_command_keys(
    app, db_session, make_org, client, login_as
):
    """§9: two command boundaries, one client key, related-but-distinct keys.

    A single command key across both would make the second call collide with
    the first receipt instead of executing.
    """
    from app.models.transformation_execution import CommandIdempotencyRecord

    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "keys")
    condition_id = seeded.condition_ids[0]
    client_key = f"one-key-{seeded.suffix}"

    login_as(client, seeded.submitter_id)
    captured = client.post(
        _capture_url(condition_id),
        json=_attestation_body(),
        headers={"Idempotency-Key": client_key},
    )
    assert captured.status_code == 201
    evidence_id = captured.get_json()["condition_evidence_id"]

    login_as(client, seeded.submitter_id)
    submitted = client.post(
        _submit_url(condition_id, evidence_id),
        json={},
        headers={"Idempotency-Key": client_key},
    )
    assert submitted.status_code == 200

    db_session.expire_all()
    keys = set(
        db_session.query(CommandIdempotencyRecord.idempotency_key)
        .filter(CommandIdempotencyRecord.organization_id == seeded.org_id)
        .filter(CommandIdempotencyRecord.idempotency_key.like(f"{client_key}%"))
        .all()
    )
    assert {(f"{client_key}:capture",), (f"{client_key}:submit",)} <= keys


def test_capture_succeeded_submit_failed_is_retryable_without_recapture(
    app, db_session, make_org, client, login_as, monkeypatch
):
    """§9: 'evidence captured, not submitted' must retry, not recapture."""
    from app.models.arb_condition_evidence import ARBConditionEvidenceRecord
    from app.models.arb_decision_event import ARBCondition
    from app.modules.transformation_room.arb_condition_lifecycle_service import (
        TypedARBConditionLifecycleService,
    )
    from app.modules.transformation_room.domain import KnownPreCommitTransient

    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "retry")
    condition_id = seeded.condition_ids[0]

    login_as(client, seeded.submitter_id)
    captured = client.post(_capture_url(condition_id), json=_attestation_body())
    assert captured.status_code == 201
    evidence_id = captured.get_json()["condition_evidence_id"]

    original = TypedARBConditionLifecycleService.submit_evidence

    def failing(**kwargs):
        raise KnownPreCommitTransient("submission_not_confirmed")

    monkeypatch.setattr(
        TypedARBConditionLifecycleService, "submit_evidence", failing
    )
    login_as(client, seeded.submitter_id)
    failed = client.post(_submit_url(condition_id, evidence_id), json={})
    assert failed.status_code == 503
    assert failed.get_json()["reason_codes"] == ["arb_condition_command_unconfirmed"]
    # The condition did not advance and nothing claimed that it had.
    assert failed.get_json()["success"] is False
    db_session.expire_all()
    assert db_session.get(ARBCondition, condition_id).status == "pending"

    monkeypatch.setattr(
        TypedARBConditionLifecycleService, "submit_evidence", original
    )
    login_as(client, seeded.submitter_id)
    retried = client.post(_submit_url(condition_id, evidence_id), json={})
    assert retried.status_code == 200
    assert retried.get_json()["status"] == "evidence_submitted"

    # Exactly one evidence record: the retry did not recapture.
    db_session.expire_all()
    assert db_session.query(ARBConditionEvidenceRecord).filter_by(
        organization_id=seeded.org_id, condition_id=condition_id
    ).count() == 1


# ── separation of duties ─────────────────────────────────────────────────────


def test_self_verification_returns_403(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "self-verify")
    condition_id = seeded.condition_ids[0]

    login_as(client, seeded.submitter_id)
    captured = client.post(_capture_url(condition_id), json=_attestation_body())
    assert captured.status_code == 201
    evidence_id = captured.get_json()["condition_evidence_id"]

    login_as(client, seeded.submitter_id)
    assert client.post(_submit_url(condition_id, evidence_id), json={}).status_code == 200

    # A forged POST from the submitter — the route offers no way to do this,
    # and the command refuses it anyway.
    login_as(client, seeded.submitter_id)
    response = client.post(_verify_url(condition_id, evidence_id), json={})
    assert response.status_code == 403
    assert response.get_json()["success"] is False
    assert response.get_json()["reason_codes"][0] in {
        "arb_decision_separation_of_duties",
        "arb_condition_verification_separation_required",
    }


def test_non_authority_waiver_returns_403(
    app, db_session, make_org, client, login_as
):
    from app.models.arb_condition_event import ARBConditionEvent

    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "waiver-authority")
    condition_id = seeded.condition_ids[0]

    login_as(client, seeded.bystander_id)
    response = client.post(_waive_url(condition_id), json=_waiver_body())
    assert response.status_code == 403
    assert response.get_json()["success"] is False

    db_session.expire_all()
    assert db_session.query(ARBConditionEvent).filter_by(
        organization_id=seeded.org_id, condition_id=condition_id
    ).count() == 0


# ── waiver validation ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("expires_at", "code"),
    (
        (lambda: _iso(_now() - timedelta(days=1)), "waiver_expiry_in_past"),
        (lambda: _iso(_now() + timedelta(days=366)), "waiver_expiry_too_far"),
    ),
)
def test_waiver_expiry_outside_the_window_is_rejected_with_no_event(
    app, db_session, make_org, client, login_as, expires_at, code
):
    from app.models.arb_condition_event import ARBConditionEvent

    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "waiver-window")
    condition_id = seeded.condition_ids[0]

    login_as(client, seeded.authority_id)
    response = client.post(
        _waive_url(condition_id), json=_waiver_body(expires_at=expires_at())
    )
    assert response.status_code == 422
    body = response.get_json()
    assert body["reason_codes"] == [code]
    assert body["field_errors"][0]["field"] == "expires_at"

    db_session.expire_all()
    assert db_session.query(ARBConditionEvent).filter_by(
        organization_id=seeded.org_id, condition_id=condition_id
    ).count() == 0


def test_naive_waiver_expiry_is_rejected(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "waiver-naive")
    login_as(client, seeded.authority_id)
    response = client.post(
        _waive_url(seeded.condition_ids[0]),
        json=_waiver_body(expires_at="2027-01-01T00:00:00"),
    )
    assert response.status_code == 400
    assert response.get_json()["reason_codes"] == ["expires_at_not_timezone_aware"]


def test_overlong_waiver_reason_is_rejected(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "waiver-length")
    login_as(client, seeded.authority_id)
    response = client.post(
        _waive_url(seeded.condition_ids[0]), json=_waiver_body(reason="x" * 2001)
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body["reason_codes"] == ["reason_too_long"]
    assert body["field_errors"][0] == {
        "field": "reason", "code": "too_long", "limit": 2000
    }


def test_waiver_succeeds_for_decision_authority_and_projects(
    app, db_session, make_org, client, login_as
):
    from app.models.arb_decision_event import ARBCondition

    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "waiver-ok", conditions=1)
    condition_id = seeded.condition_ids[0]

    login_as(client, seeded.authority_id)
    response = client.post(_waive_url(condition_id), json=_waiver_body())
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "waived"
    assert body["projection_status"] == "approved"
    assert body["condition_event_id"]

    db_session.expire_all()
    condition = db_session.get(ARBCondition, condition_id)
    assert condition.status == "waived"
    # Scope is stored as the documented {"description": ...} shape.
    assert condition.waiver_scope_json["scope"] == {
        "description": "The reporting service only."
    }


@pytest.mark.parametrize("scope", (123, [], {"description": "ok", "extra": 1}, None))
def test_waiver_scope_must_be_text_or_a_description_object(
    app, db_session, make_org, client, login_as, scope
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "waiver-scope")
    login_as(client, seeded.authority_id)
    response = client.post(
        _waive_url(seeded.condition_ids[0]), json=_waiver_body(scope=scope)
    )
    assert response.status_code == 400
    assert response.get_json()["reason_codes"] == ["scope_invalid"]


# ── honest evidence modes ────────────────────────────────────────────────────


def test_manual_attestation_is_stored_as_an_attestation_not_a_measurement(
    app, db_session, make_org, client, login_as
):
    from app.models.arb_condition_evidence import ARBConditionEvidenceRecord

    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "attestation")
    login_as(client, seeded.submitter_id)
    response = client.post(
        _capture_url(seeded.condition_ids[0]), json=_attestation_body()
    )
    assert response.status_code == 201
    evidence_id = response.get_json()["condition_evidence_id"]

    db_session.expire_all()
    record = db_session.query(ARBConditionEvidenceRecord).filter_by(
        id=evidence_id, organization_id=seeded.org_id
    ).one()
    assert record.source_type == "manual_attestation"
    assert record.freshness_status == "not_applicable"
    assert record.freshness_rule_version == "arb-condition-not-applicable-v1"
    assert record.freshness_expires_at is None
    assert record.source_identity == f"manual-attestation:user:{seeded.submitter_id}"
    assert record.value_json["evidence_mode"] == "manual_attestation"
    assert record.value_json["attested_by_user_id"] == seeded.submitter_id
    # Server-derived, not client-supplied.
    assert record.content_hash == record.recompute_content_hash()
    assert len(record.source_checksum) == 64


def test_expired_source_backed_record_is_rejected(
    app, db_session, make_org, client, login_as
):
    from app.models.arb_condition_evidence import ARBConditionEvidenceRecord

    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "expired-source")
    login_as(client, seeded.submitter_id)
    response = client.post(
        _capture_url(seeded.condition_ids[0]),
        json=_source_body(expires_in=-timedelta(minutes=1)),
    )
    assert response.status_code == 422
    body = response.get_json()
    assert body["reason_codes"] == ["arb_condition_evidence_source_expired"]
    assert body["field_errors"][0]["field"] == "expires_at"

    db_session.expire_all()
    assert db_session.query(ARBConditionEvidenceRecord).filter_by(
        organization_id=seeded.org_id
    ).count() == 0


def test_source_backed_record_may_not_masquerade_as_an_attestation(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "masquerade")
    body = _source_body()
    body["source_type"] = "manual_attestation"
    login_as(client, seeded.submitter_id)
    response = client.post(_capture_url(seeded.condition_ids[0]), json=body)
    assert response.status_code == 400
    assert response.get_json()["reason_codes"] == ["source_type_reserved"]


def test_future_observed_at_is_rejected(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "future-observed")
    login_as(client, seeded.submitter_id)
    response = client.post(
        _capture_url(seeded.condition_ids[0]),
        json=_attestation_body(observed=_now() + timedelta(hours=1)),
    )
    assert response.status_code == 400
    assert response.get_json()["reason_codes"] == ["observed_at_in_future"]


def test_unknown_evidence_mode_is_rejected(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "bad-mode")
    login_as(client, seeded.submitter_id)
    response = client.post(
        _capture_url(seeded.condition_ids[0]), json={"mode": "trust_me"}
    )
    assert response.status_code == 400
    assert response.get_json()["reason_codes"] == ["evidence_mode_invalid"]


# ── no client-selected state ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field",
    (
        "organization_id",
        "actor_id",
        "created_by_id",
        "status",
        "condition_status",
        "review_cycle_id",
        "review_item_id",
        "decision_event_id",
        "content_hash",
        "source_checksum",
        "freshness_status",
        "freshness_rule_version",
        "condition_revision",
    ),
)
def test_capture_rejects_every_client_selected_field(
    app, db_session, make_org, client, login_as, field
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, f"reject-{field[:8]}")
    body = _attestation_body()
    body[field] = 1
    login_as(client, seeded.submitter_id)
    response = client.post(_capture_url(seeded.condition_ids[0]), json=body)
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["reason_codes"] == ["request_field_not_accepted"]
    assert payload["field_errors"] == [
        {"field": field, "code": "field_not_accepted"}
    ]


def test_submit_and_verify_accept_no_state_or_identity_body(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "no-body")
    condition_id = seeded.condition_ids[0]
    login_as(client, seeded.submitter_id)
    captured = client.post(_capture_url(condition_id), json=_attestation_body())
    evidence_id = captured.get_json()["condition_evidence_id"]

    for url in (
        _submit_url(condition_id, evidence_id),
        _verify_url(condition_id, evidence_id),
    ):
        login_as(client, seeded.submitter_id)
        response = client.post(url, json={"status": "fulfilled"})
        assert response.status_code == 400, url
        assert response.get_json()["reason_codes"] == ["request_field_not_accepted"]


def test_invalid_idempotency_key_is_rejected(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "bad-key")
    login_as(client, seeded.submitter_id)
    response = client.post(
        _capture_url(seeded.condition_ids[0]),
        json=_attestation_body(),
        headers={"Idempotency-Key": "x" * 201},
    )
    assert response.status_code == 400
    assert response.get_json()["reason_codes"] == ["idempotency_key_invalid"]


# ── conflict states ──────────────────────────────────────────────────────────


def test_capture_against_a_non_pending_condition_returns_409(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "not-pending")
    condition_id = seeded.condition_ids[0]

    login_as(client, seeded.submitter_id)
    captured = client.post(_capture_url(condition_id), json=_attestation_body())
    evidence_id = captured.get_json()["condition_evidence_id"]
    login_as(client, seeded.submitter_id)
    assert client.post(_submit_url(condition_id, evidence_id), json={}).status_code == 200

    login_as(client, seeded.submitter_id)
    response = client.post(_capture_url(condition_id), json=_attestation_body())
    assert response.status_code == 409
    assert response.get_json()["reason_codes"] == ["arb_condition_not_pending"]


def test_verifying_unsubmitted_evidence_returns_409(
    app, db_session, make_org, client, login_as
):
    _install_guards(db_session)
    seeded = _seed(db_session, make_org, "verify-early")
    condition_id = seeded.condition_ids[0]
    login_as(client, seeded.submitter_id)
    captured = client.post(_capture_url(condition_id), json=_attestation_body())
    evidence_id = captured.get_json()["condition_evidence_id"]

    login_as(client, seeded.authority_id)
    response = client.post(_verify_url(condition_id, evidence_id), json={})
    assert response.status_code == 409
    assert response.get_json()["reason_codes"][0].startswith("arb_condition_")
