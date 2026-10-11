"""Route-level contracts for typed ARB decisions (Lane L1).

Every typed ARB terminal decision must reach ``TypedARBDecisionService`` with
an actor built from the authenticated session only, a tenant-scoped cycle
resolved by an explicit ``(id, organization_id)`` predicate, and an
idempotency key that is never a browser-supplied ``review_item_id``.

The module uses the shared fixtures from ``tests/conftest.py`` (``app``,
``_schema``, ``client``, ``login_as``, ``make_org``, ``tenant_ctx``). It
overrides only ``db_session``: ``CommandService`` opens its own connection on
``db.engine``, so a typed command cannot see rows held in the shared
fixture's uncommitted transaction. Setup is therefore committed and removed
again by an explicit, asserted teardown, exactly as
``tests/test_arb_condition_lifecycle_integration.py`` does.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import inspect
import os
from types import SimpleNamespace
import uuid

import pytest
from flask import g
from flask_login import login_user

from app import db
from app.models.architecture_review_board import ARBReviewCycle, ARBReviewItem
from app.models.arb_decision_event import ARBCondition, ARBDecisionEvent
from app.models.organization import Organization
from app.models.arb_submission_evidence import WorkbenchArtifactEvidence
from app.models.solution_architect_models import (
    DriverType,
    SolutionAnalysisSession,
    SolutionDriver,
    SolutionGoal,
    SolutionProblemDefinition,
)
from app.models.solution_lifecycle_models import SolutionRisk
from app.models.solution_models import Solution
from app.models.solution_governance import SolutionARBReview, SolutionNotification
from app.models.user import Permission, Role, User
from app.modules.transformation_room.arb_submission_service import (
    TypedARBSubmissionService,
)
from app.modules.transformation_room.domain import ActorContext
from app.services.arb_workflow_service import ARBCondition as LegacyARBCondition


os.environ.setdefault("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "74" * 32)


_CLEANUP_TABLES = (
    "subscriptions",
    "archie_command_claim_challenges",
    "arb_condition_events",
    "arb_canonical_conditions",
    "arb_condition_evidence_records",
    "arb_decision_events",
    "arb_submission_events",
    "operation_results",
    "command_materialisations",
    "command_idempotency_records",
    "arb_review_items",
    "arb_review_cycles",
    "arb_subject_evidence_snapshots",
    "architecture_decisions",
    "users",
)


@pytest.fixture
def db_session(app, _schema):
    """Committed setup visible to CommandService's independent sessions."""
    from app import db

    with app.app_context():
        db.session.remove()
        cleanup_org_ids = set()
        cleanup_role_ids = set()
        db.session.info["l1_cleanup_org_ids"] = cleanup_org_ids
        db.session.info["l1_cleanup_role_ids"] = cleanup_role_ids
        try:
            yield db.session
        finally:
            organization_ids = tuple(cleanup_org_ids)
            role_ids = tuple(cleanup_role_ids)
            db.session.remove()
            if not organization_ids:
                return
            raw = db.engine.raw_connection()
            try:
                with raw.cursor() as cursor:
                    cursor.execute("SHOW session_replication_role")
                    original_role = cursor.fetchone()[0]
                    cursor.execute("SET session_replication_role = replica")
                    try:
                        for table in _CLEANUP_TABLES:
                            cursor.execute(
                                f'DELETE FROM "{table}" '  # nosec B608 -- fixed table allowlist
                                "WHERE organization_id = ANY(%s)",
                                (list(organization_ids),),
                            )
                        if role_ids:
                            cursor.execute(
                                "DELETE FROM roles WHERE id = ANY(%s)",
                                (list(role_ids),),
                            )
                        cursor.execute(
                            "DELETE FROM organizations WHERE id = ANY(%s)",
                            (list(organization_ids),),
                        )
                    except Exception:
                        raw.rollback()
                        cursor.execute(
                            f"SET session_replication_role = {original_role}"
                        )
                        raw.commit()
                        raise
                    cursor.execute(f"SET session_replication_role = {original_role}")
                    raw.commit()
                    residual = {}
                    for table in _CLEANUP_TABLES:
                        cursor.execute(
                            f'SELECT count(*) FROM "{table}" '  # nosec B608 -- fixed allowlist
                            "WHERE organization_id = ANY(%s)",
                            (list(organization_ids),),
                        )
                        count = cursor.fetchone()[0]
                        if count:
                            residual[table] = count
                    if residual:
                        raise AssertionError(
                            f"L1 cleanup left rows behind: {residual!r}"
                        )
            finally:
                raw.close()


class _Fixture:
    """One tenant with a real, open typed ADR review cycle."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


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


def _seed_typed_cycle(db_session, make_org, label="l1"):
    """Create a tenant, a submitter, a decider and one open typed ADR cycle."""
    from app.models.architecture_decision import ArchitectureDecision
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.user import Permission, Role, User
    from app.modules.transformation_room.arb_submission_service import (
        TypedARBSubmissionService,
    )
    from app.modules.transformation_room.domain import ActorContext

    _install_guards(db_session)
    org = make_org(label)
    db_session.info.setdefault("l1_cleanup_org_ids", set()).add(org.id)
    from app.services.billing_plans import set_contract_plan

    set_contract_plan(org, "enterprise", None)  # more people than Community admits
    suffix = uuid.uuid4().hex[:10]

    role = Role(name=f"L1 ARB {suffix}", permissions=Permission.GENERAL)
    db_session.add(role)
    db_session.flush()
    db_session.info.setdefault("l1_cleanup_role_ids", set()).add(role.id)

    submitter = User(
        organization_id=org.id,
        email=f"l1-submit-{suffix}@example.test",
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    decider = User(
        organization_id=org.id,
        email=f"l1-decide-{suffix}@example.test",
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    db_session.add_all((submitter, decider))
    db_session.flush()
    submitter.role_id = role.id
    decider.role_id = role.id
    adr = ArchitectureDecision(
        organization_id=org.id,
        decision_id=f"AD-{suffix[:7]}",
        title=f"L1 typed decision {suffix}",
        status="proposed",
        context="A governed choice needs a recorded decision.",
        decision="Adopt the governed option.",
        rationale="It is testable.",
        consequences="A decision event must exist.",
        created_by_id=submitter.id,
    )
    db_session.add(adr)
    db_session.commit()

    submission = TypedARBSubmissionService.submit(
        actor=ActorContext(
            submitter.id, org.id, frozenset(), f"l1-submit-{suffix}"
        ),
        command_key=f"l1-submit-{suffix}",
        subject_type="adr",
        subject_id=adr.id,
        assertions={"human_reviewed": True},
    )
    cycle_id = submission.object_ids["review_cycle_id"]
    review_item_id = submission.object_ids["review_item_id"]
    review = db_session.get(ARBReviewItem, review_item_id)
    # Scalars only: the tests cross session boundaries, and a detached ORM
    # instance would fail to refresh rather than assert.
    fixture = _Fixture(
        org_id=org.id,
        role_id=role.id,
        submitter_id=submitter.id,
        decider_id=decider.id,
        adr_id=adr.id,
        adr_title=adr.title,
        cycle_id=cycle_id,
        review_item_id=review_item_id,
        review_number=review.review_number,
        suffix=suffix,
    )
    db_session.expunge_all()
    return fixture


def _legacy_review(db_session, fixture):
    """A generic, untyped review item in the same tenant."""
    from app.models.architecture_review_board import ARBReviewItem

    review = ARBReviewItem(
        organization_id=fixture.org_id,
        review_number=f"LEG-{fixture.suffix}",
        title="Legacy generic review",
        review_type="solution_design",
        status="under_review",
        submitter_id=fixture.submitter_id,
    )
    db_session.add(review)
    db_session.commit()
    review_id = review.id
    db_session.expunge_all()
    return review_id


def _decision_events(db_session, cycle_id):
    from app.models.arb_decision_event import ARBDecisionEvent

    return (
        db_session.query(ARBDecisionEvent)
        .filter(ARBDecisionEvent.review_cycle_id == cycle_id)
        .all()
    )


# ---------------------------------------------------------------------------
# 1. Client-trusted actor is never honoured
# ---------------------------------------------------------------------------


def test_forged_actor_fields_are_ignored_and_session_user_decides(
    app, db_session, make_org, client, login_as
):
    """decided_by_id / organization_id / status in the body must not be read."""
    fixture = _seed_typed_cycle(db_session, make_org, "l1-forged")
    login_as(client, fixture.decider_id)

    response = client.post(
        f"/arb/api/arb/{fixture.review_item_id}/approve",
        json={
            "notes": "Approved on the recorded evidence.",
            # Every one of these is a forged trust input.
            "decided_by_id": fixture.submitter_id,
            "organization_id": fixture.org_id + 10_000,
            "actor_id": fixture.submitter_id,
            "role": "platform_admin",
            "status": "approved",
            "readiness": "ready",
        },
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["success"] is True

    events = _decision_events(db_session, fixture.cycle_id)
    assert len(events) == 1
    # The recorded actor is the session user, not the forged decided_by_id.
    assert events[0].actor_id == fixture.decider_id
    assert events[0].organization_id == fixture.org_id


# ---------------------------------------------------------------------------
# 2. Explicit tenant predicates
# ---------------------------------------------------------------------------


def test_cross_tenant_review_id_returns_404_and_leaks_nothing(
    app, db_session, make_org, client, login_as
):
    owner = _seed_typed_cycle(db_session, make_org, "l1-owner")
    intruder = _seed_typed_cycle(db_session, make_org, "l1-intruder")
    login_as(client, intruder.decider_id)

    for path, method in (
        (f"/arb/api/arb/{owner.review_item_id}/approve", client.post),
        (f"/arb/api/arb/{owner.review_item_id}/reject", client.post),
        (f"/arb/api/arb/{owner.review_item_id}/request-changes", client.post),
        (f"/arb/api/arb/{owner.review_item_id}/review", client.post),
    ):
        login_as(client, intruder.decider_id)
        response = method(path, json={"notes": "n", "reason": "r"})
        assert response.status_code == 404, (path, response.get_json())
        payload = response.get_data(as_text=True)
        assert owner.review_number not in payload
        assert owner.adr_title not in payload

    # And no decision was recorded against the other tenant's cycle.
    assert _decision_events(db_session, owner.cycle_id) == []


def test_cross_tenant_implementation_status_get_returns_404(
    app, db_session, make_org, client, login_as
):
    owner = _seed_typed_cycle(db_session, make_org, "l1-impl-owner")
    intruder = _seed_typed_cycle(db_session, make_org, "l1-impl-intruder")
    login_as(client, intruder.decider_id)
    response = client.get(
        f"/arb/api/arb/{owner.review_item_id}/implementation-status"
    )
    assert response.status_code == 404
    assert owner.review_number not in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# 3. Separation of duties
# ---------------------------------------------------------------------------


def test_submitter_cannot_decide_own_review(
    app, db_session, make_org, client, login_as
):
    fixture = _seed_typed_cycle(db_session, make_org, "l1-sod")
    login_as(client, fixture.submitter_id)

    response = client.post(
        f"/arb/api/arb/{fixture.review_item_id}/approve",
        json={"notes": "I approve my own submission."},
    )
    assert response.status_code == 403
    body = response.get_json()
    assert body["success"] is False
    assert body["reason_codes"] == ["arb_decision_separation_of_duties"]
    assert body["request_id"]
    assert _decision_events(db_session, fixture.cycle_id) == []


# ---------------------------------------------------------------------------
# 4/5. Typed service routing, envelope and canonical identifiers
# ---------------------------------------------------------------------------


def test_typed_decision_envelope_keeps_legacy_aliases_and_adds_canonical_ids(
    app, db_session, make_org, client, login_as
):
    fixture = _seed_typed_cycle(db_session, make_org, "l1-envelope")
    login_as(client, fixture.decider_id)

    response = client.post(
        f"/arb/api/arb/{fixture.review_item_id}/request-changes",
        json={
            "notes": "Approved once the two conditions are evidenced.",
            "conditions": ["Complete the threat model", "Confirm the DR runbook"],
        },
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()

    # Legacy aliases the existing callers rely on.
    assert body["success"] is True
    assert body["item_id"] == fixture.review_item_id
    assert body["status"] == "approved_with_conditions"
    assert body["redirect_url"] == f"/arb/reviews/{fixture.review_item_id}"

    # Canonical identifiers added by the blueprint contract.
    assert body["review_cycle_id"] == fixture.cycle_id
    assert body["review_item_id"] == fixture.review_item_id
    assert isinstance(body["decision_event_id"], int)
    assert len(body["condition_ids"]) == 2
    assert body["canonical_url"] == f"/architecture/decisions/{fixture.adr_id}"
    assert body["outcome"] == "approved_with_conditions"
    assert body["idempotent"] is False


def test_conditions_are_canonical_rows_without_an_invented_due_date(
    app, db_session, make_org, client, login_as
):
    from app.models.arb_decision_event import ARBCondition

    fixture = _seed_typed_cycle(db_session, make_org, "l1-conditions")
    login_as(client, fixture.decider_id)

    response = client.post(
        f"/arb/reviews/{fixture.review_item_id}/decision",
        data={
            "decision": "approved_with_conditions",
            "rationale": "Conditional on evidence.",
            "conditions": "Complete the threat model\nConfirm the DR runbook\n",
            # Forged actor fields on the HTML form path as well.
            "decided_by_id": str(fixture.submitter_id),
            "organization_id": "999999",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303), response.get_data(as_text=True)

    conditions = (
        db_session.query(ARBCondition)
        .filter(ARBCondition.review_cycle_id == fixture.cycle_id)
        .order_by(ARBCondition.condition_number)
        .all()
    )
    assert len(conditions) == 2
    # The old form parser invented `utcnow() + 30 days` and a `pending` state.
    assert [condition.due_date for condition in conditions] == [None, None]
    assert all(condition.blocks_execution for condition in conditions)
    events = _decision_events(db_session, fixture.cycle_id)
    assert len(events) == 1
    assert events[0].actor_id == fixture.decider_id


def test_typed_decision_does_not_mutate_solution_governance_status(
    app, db_session, make_org, client, login_as
):
    """The approve path must not write a subject projection directly."""
    from app.models.architecture_review_board import ARBReviewItem

    fixture = _seed_typed_cycle(db_session, make_org, "l1-projection")
    login_as(client, fixture.decider_id)
    response = client.post(
        f"/arb/api/arb/{fixture.review_item_id}/approve",
        json={"notes": "Approved."},
    )
    assert response.status_code == 200, response.get_json()

    db_session.expunge_all()
    review = db_session.get(ARBReviewItem, fixture.review_item_id)
    # The projection is written by the typed service, from the decision event.
    assert review.decided_by_id == fixture.decider_id
    assert review.decision == "approved"


# ---------------------------------------------------------------------------
# 6. Idempotency
# ---------------------------------------------------------------------------


def test_replay_with_the_same_key_returns_the_same_result_and_one_event(
    app, db_session, make_org, client, login_as
):
    fixture = _seed_typed_cycle(db_session, make_org, "l1-replay")
    key = f"l1-replay-{fixture.suffix}"
    body = {"notes": "Approved on the recorded evidence."}

    login_as(client, fixture.decider_id)
    first = client.post(
        f"/arb/api/arb/{fixture.review_item_id}/approve",
        json=body,
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 200, first.get_json()
    login_as(client, fixture.decider_id)
    replay = client.post(
        f"/arb/api/arb/{fixture.review_item_id}/approve",
        json=body,
        headers={"Idempotency-Key": key},
    )
    assert replay.status_code == 200, replay.get_json()

    assert (
        replay.get_json()["decision_event_id"]
        == first.get_json()["decision_event_id"]
    )
    assert replay.get_json()["review_cycle_id"] == fixture.cycle_id
    assert replay.get_json()["idempotent"] is True
    assert len(_decision_events(db_session, fixture.cycle_id)) == 1


def test_same_key_with_a_different_payload_conflicts(
    app, db_session, make_org, client, login_as
):
    fixture = _seed_typed_cycle(db_session, make_org, "l1-conflict")
    key = f"l1-conflict-{fixture.suffix}"

    login_as(client, fixture.decider_id)
    first = client.post(
        f"/arb/api/arb/{fixture.review_item_id}/approve",
        json={"notes": "Approved on the recorded evidence."},
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 200, first.get_json()

    login_as(client, fixture.decider_id)
    conflict = client.post(
        f"/arb/api/arb/{fixture.review_item_id}/reject",
        json={"reason": "A different command under the same key."},
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409, conflict.get_json()
    body = conflict.get_json()
    assert body["success"] is False
    assert body["reason_codes"]
    assert "Traceback" not in str(body)
    assert len(_decision_events(db_session, fixture.cycle_id)) == 1


def test_a_malformed_idempotency_key_is_rejected_before_any_write(
    app, db_session, make_org, client, login_as
):
    fixture = _seed_typed_cycle(db_session, make_org, "l1-badkey")
    login_as(client, fixture.decider_id)
    response = client.post(
        f"/arb/api/arb/{fixture.review_item_id}/approve",
        json={"notes": "Approved."},
        headers={"Idempotency-Key": "short"},
    )
    assert response.status_code == 400
    assert response.get_json()["reason_codes"] == ["invalid_idempotency_key"]
    assert _decision_events(db_session, fixture.cycle_id) == []


def test_review_item_id_is_not_used_as_an_idempotency_token(
    app, db_session, make_org, client, login_as
):
    """Two different cycles under one actor must not collide on the item id."""
    from app.modules.architecture.routes.arb_routes import TypedARBDecisionAdapter
    from app.modules.transformation_room.domain import ActorContext

    fixture = _seed_typed_cycle(db_session, make_org, "l1-key-source")
    actor = ActorContext(fixture.decider_id, fixture.org_id, frozenset(), "req")
    with app.test_request_context("/"):
        first = TypedARBDecisionAdapter.command_key(
            None,
            actor=actor,
            cycle_id=fixture.cycle_id,
            outcome="approved",
            rationale="A",
            conditions=[],
        )
        second = TypedARBDecisionAdapter.command_key(
            None,
            actor=actor,
            cycle_id=fixture.cycle_id + 1,
            outcome="approved",
            rationale="A",
            conditions=[],
        )
    assert first != second
    # Decimal item IDs can occur by chance inside a SHA-256 hex digest.
    # Check the actual input boundary instead of forbidding substrings.
    for item_id in (fixture.review_item_id, fixture.review_item_id + 1):
        for body in ({"json": {"review_item_id": item_id}},
                     {"data": {"review_item_id": str(item_id)}}):
            with app.test_request_context("/", method="POST", **body):
                supplied = TypedARBDecisionAdapter.supplied_command_key()
                assert supplied is None
                assert TypedARBDecisionAdapter.command_key(
                    supplied,
                    actor=actor,
                    cycle_id=fixture.cycle_id,
                    outcome="approved",
                    rationale="A",
                    conditions=[],
                ) == first


# ---------------------------------------------------------------------------
# 4. Operations typed services deliberately do not expose
# ---------------------------------------------------------------------------


def test_typed_reopen_is_rejected_but_legacy_reopen_still_works(
    app, db_session, make_org, client, login_as
):
    from app.models.architecture_review_board import ARBReviewItem

    fixture = _seed_typed_cycle(db_session, make_org, "l1-reopen")
    login_as(client, fixture.decider_id)
    decided = client.post(
        f"/arb/api/arb/{fixture.review_item_id}/approve",
        json={"notes": "Approved."},
    )
    assert decided.status_code == 200, decided.get_json()

    login_as(client, fixture.decider_id)
    response = client.post(
        f"/arb/reviews/{fixture.review_item_id}/reopen",
        data={"reopen_reason": "I changed my mind."},
    )
    assert response.status_code == 409

    db_session.expunge_all()
    review = db_session.get(ARBReviewItem, fixture.review_item_id)
    assert review.decision == "approved"
    assert review.decided_by_id == fixture.decider_id

    # A legacy generic row keeps its historic behaviour, explicitly.
    legacy_id = _legacy_review(db_session, fixture)
    legacy = db_session.get(ARBReviewItem, legacy_id)
    legacy.decision = "approved"
    legacy.status = "approved"
    legacy.decided_by_id = fixture.decider_id
    db_session.commit()
    db_session.expunge_all()
    login_as(client, fixture.decider_id)
    legacy_response = client.post(
        f"/arb/reviews/{legacy_id}/reopen",
        data={"reopen_reason": "Legacy rows may still be reopened."},
    )
    assert legacy_response.status_code in (302, 303)
    db_session.expunge_all()
    reopened = db_session.get(ARBReviewItem, legacy_id)
    assert reopened.status == "under_review"
    assert reopened.decision is None
    assert reopened.decided_by_id is None


def test_typed_implementation_status_patch_is_rejected(
    app, db_session, make_org, client, login_as
):
    fixture = _seed_typed_cycle(db_session, make_org, "l1-impl")
    login_as(client, fixture.decider_id)
    response = client.patch(
        f"/arb/api/arb/{fixture.review_item_id}/implementation-status",
        json={"implementation_status": "completed", "conditions_response": {"0": "x"}},
    )
    assert response.status_code == 409
    body = response.get_json()
    assert body["reason_codes"] == [
        "typed_cycle_implementation_status_not_writable"
    ]


def test_typed_cycle_status_cannot_be_client_assigned(
    app, db_session, make_org, client, login_as
):
    fixture = _seed_typed_cycle(db_session, make_org, "l1-begin")
    login_as(client, fixture.decider_id)
    response = client.post(f"/arb/api/arb/{fixture.review_item_id}/review", json={})
    assert response.status_code == 409
    assert response.get_json()["reason_codes"] == [
        "typed_cycle_status_not_client_mutable"
    ]


# ---------------------------------------------------------------------------
# 5. Documented status-code map
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_factory,expected_status,expected_reason",
    [
        (lambda d: d.NotFound("arb_review_cycle_not_found"), 404,
         "arb_review_cycle_not_found"),
        (lambda d: d.NotFound("arb_review_not_found"), 404,
         "review_not_found"),
        (lambda d: d.NotFound("arb_condition_not_found"), 404,
         "arb_condition_not_found"),
        (lambda d: d.NotAuthorised("arb_decision_separation_of_duties"), 403,
         "arb_decision_separation_of_duties"),
        (lambda d: d.NotAuthorised("some_internal_detail"), 403,
         "actor_not_authorized"),
        (lambda d: d.CommandConflict("arb_cycle_already_terminal"), 409,
         "arb_cycle_already_terminal"),
        (lambda d: d.CommandConflict("internal_row_12345"), 409,
         "decision_conflict"),
        (lambda d: d.KnownPreCommitTransient("db_unavailable"), 503,
         "decision_unconfirmed"),
        (lambda d: d.AuthenticationRequired("not_authenticated"), 401,
         "not_authenticated"),
        (lambda d: RuntimeError("secret tenant name leaked here"), 503,
         "decision_not_confirmed"),
    ],
)
def test_service_exceptions_map_to_documented_statuses(
    app, db_session, make_org, client, login_as, monkeypatch,
    error_factory, expected_status, expected_reason,
):
    from app.modules.transformation_room import arb_decision_service, domain

    fixture = _seed_typed_cycle(db_session, make_org, "l1-status")

    def _raise(**_kwargs):
        raise error_factory(domain)

    monkeypatch.setattr(
        arb_decision_service.TypedARBDecisionService, "decide", staticmethod(_raise)
    )
    login_as(client, fixture.decider_id)
    response = client.post(
        f"/arb/api/arb/{fixture.review_item_id}/approve",
        json={"notes": "Approved."},
    )
    assert response.status_code == expected_status
    body = response.get_json()
    assert body["success"] is False
    assert body["reason_codes"] == [expected_reason]
    assert body["request_id"]
    assert "missing_evidence" in body
    assert "secret tenant name leaked here" not in str(body)


def test_evidence_blockers_map_to_422_with_the_stable_blocker_list(
    app, db_session, make_org, client, login_as, monkeypatch
):
    from app.modules.transformation_room import arb_decision_service
    from app.modules.transformation_room.domain import BlockedByEvidence

    fixture = _seed_typed_cycle(db_session, make_org, "l1-blocked")

    def _raise(**_kwargs):
        raise BlockedByEvidence(
            "arb_subject_not_ready",
            reason_codes=["adr_evidence_stale"],
            missing_evidence=[{"code": "adr_evidence_stale", "resource_type": "adr"}],
        )

    monkeypatch.setattr(
        arb_decision_service.TypedARBDecisionService, "decide", staticmethod(_raise)
    )
    login_as(client, fixture.decider_id)
    response = client.post(
        f"/arb/api/arb/{fixture.review_item_id}/approve",
        json={"notes": "Approved."},
    )
    assert response.status_code == 422
    body = response.get_json()
    assert body["reason_codes"] == ["adr_evidence_stale"]
    assert body["missing_evidence"][0]["resource_type"] == "adr"


def test_unauthenticated_typed_decision_is_not_a_500(app, client):
    """An anonymous caller is redirected/refused, never served a decision."""
    response = client.post("/arb/api/arb/1/approve", json={"notes": "x"})
    assert response.status_code in (302, 401, 403)


def test_unsupported_outcome_is_a_400_on_the_html_form(
    app, db_session, make_org, client, login_as
):
    fixture = _seed_typed_cycle(db_session, make_org, "l1-outcome")
    login_as(client, fixture.decider_id)
    response = client.post(
        f"/arb/reviews/{fixture.review_item_id}/decision",
        data={"decision": "escalate", "rationale": "Not a typed outcome."},
    )
    assert response.status_code == 400
    assert _decision_events(db_session, fixture.cycle_id) == []


def test_outcome_aliases_cover_every_typed_terminal_outcome():
    from app.modules.architecture.routes.arb_routes import TypedARBDecisionAdapter
    from app.modules.transformation_room.arb_decision_service import (
        TypedARBDecisionService,
    )

    aliases = {
        "approve": "approved",
        "reject": "rejected",
        "approved_with_conditions": "approved_with_conditions",
        "request_changes": "approved_with_conditions",
        "return_for_evidence": "returned_for_evidence",
        "return_for_options": "returned_for_options",
    }
    for supplied, expected in aliases.items():
        assert TypedARBDecisionAdapter.normalize_outcome(supplied) == expected
        assert expected in TypedARBDecisionService.TERMINAL_OUTCOMES
    with pytest.raises(ValueError):
        TypedARBDecisionAdapter.normalize_outcome("deferred")


# ---------------------------------------------------------------------------
# Solution lifecycle routes (arb_workflow_routes)
# ---------------------------------------------------------------------------


def test_solution_lifecycle_uses_an_explicit_tenant_predicate(
    app, db_session, make_org, client, login_as
):
    from app import db
    from app.models.solution_models import Solution

    owner = _seed_typed_cycle(db_session, make_org, "l1-sol-owner")
    intruder = _seed_typed_cycle(db_session, make_org, "l1-sol-intruder")
    solution = Solution(
        name=f"L1 tenancy {owner.suffix}",
        organization_id=owner.org_id,
        created_by_id=owner.submitter_id,
        governance_status="under_review",
    )
    db_session.add(solution)
    db_session.commit()
    solution_id, solution_name = solution.id, solution.name
    db_session.expunge_all()
    try:
        login_as(client, intruder.decider_id)
        response = client.get(
            f"/api/arb-workflow/solutions/{solution_id}/lifecycle"
        )
        assert response.status_code == 404
        assert solution_name not in response.get_data(as_text=True)
    finally:
        db_session.execute(
            db.text("DELETE FROM solutions WHERE id = :id"), {"id": solution_id}
        )
        db_session.commit()


def test_typed_lifecycle_transition_names_are_unique():
    from app.modules.transformation_room.arb_decision_adapter import (
        TypedARBDecisionAdapter as CommandDecisionAdapter,
    )

    transitions = CommandDecisionAdapter._typed_lifecycle_transitions(
        "under_review", ()
    )

    assert transitions == [
        "approved",
        "approved_with_conditions",
        "rejected",
        "returned_for_evidence",
        "returned_for_options",
    ]
    assert len(transitions) == len(set(transitions))


def _write_role(session):
    role = Role(
        name=f"Typed route writer {uuid.uuid4().hex[:10]}",
        index="main",
        permissions=Permission.GENERAL,
        default=False,
    )
    session.add(role)
    session.flush()
    return role


def _user(
    session,
    org,
    *,
    role="enterprise_architect",
    role_archetype="architect",
    admin=False,
    write_role=None,
):
    write_role = write_role or _write_role(session)
    user = User(
        email=f"typed-route-{uuid.uuid4().hex[:10]}@example.test",
        first_name="Typed",
        last_name="Reviewer",
        organization_id=org.id,
        enterprise_role=role,
        role_archetype=role_archetype,
        is_org_admin=admin,
        is_platform_admin=admin,
        confirmed=True,
    )
    user.role = write_role
    session.add(user)
    session.flush()
    return user


def _adr(session, org, submitter):
    from app.models.architecture_decision import ArchitectureDecision

    record = ArchitectureDecision(
        organization_id=org.id,
        decision_id=f"AD-{uuid.uuid4().hex[:7]}",
        title=f"Typed route ADR {uuid.uuid4().hex[:8]}",
        status="proposed",
        context="A governed integration choice is required.",
        decision="Use the enterprise event platform.",
        rationale="It supplies durable delivery and schema governance.",
        consequences="Teams must version event schemas.",
        created_by_id=submitter.id,
    )
    session.add(record)
    session.flush()
    return record


def _typed_adr_review(session, org, submitter):
    record = _adr(session, org, submitter)
    session.commit()
    actor = ActorContext(
        submitter.id,
        org.id,
        frozenset({submitter.enterprise_role}),
        f"typed-route-submit-{uuid.uuid4().hex}",
    )
    result = TypedARBSubmissionService.submit(
        actor=actor,
        command_key=f"typed-route-submit-{uuid.uuid4().hex}",
        subject_type="adr",
        subject_id=record.id,
        assertions={"human_reviewed": True},
    )
    session.expire_all()
    return record, session.get(ARBReviewItem, result.object_ids["review_item_id"])


def _solution_review(session, org, submitter):
    workspace = SolutionAnalysisSession(
        organization_id=org.id,
        name=f"Typed route workspace {uuid.uuid4().hex[:8]}",
        created_by_id=submitter.id,
    )
    session.add(workspace)
    session.flush()
    problem = SolutionProblemDefinition(
        organization_id=org.id,
        session_id=workspace.id,
        problem_description="Replace brittle synchronous integration.",
    )
    session.add(problem)
    session.flush()
    session.add_all(
        (
            SolutionDriver(
                organization_id=org.id,
                problem_id=problem.id,
                name="Resilience",
                driver_type=DriverType.TECHNOLOGY,
            ),
            SolutionGoal(
                organization_id=org.id,
                problem_id=problem.id,
                name="Reliable delivery",
            ),
        )
    )
    solution = Solution(
        organization_id=org.id,
        name=f"Typed route solution {uuid.uuid4().hex[:8]}",
        description="Governed solution route subject.",
        created_by_id=submitter.id,
        analysis_session_id=workspace.id,
        governance_status="draft",
    )
    session.add(solution)
    session.flush()
    workspace.custom_metadata = {
        "workspace_type": "greenfield",
        "solution_id": solution.id,
    }
    for name in ("brief", "scope", "recommendation"):
        WorkbenchArtifactEvidence.capture(
            organization_id=org.id,
            workspace_id=workspace.id,
            solution_id=solution.id,
            name=name,
            state="persisted",
            payload={"name": name, "source": "typed-route-fixture"},
            actor_id=submitter.id,
        )
    session.add(
        SolutionRisk(
            organization_id=org.id,
            solution_id=solution.id,
            risk_name="Schema drift",
            risk_description="Consumers may lag schema changes.",
            impact="medium",
            probability="medium",
            mitigation="Use versioned schemas.",
            created_by_id=submitter.id,
        )
    )
    session.flush()
    return solution, workspace


def _count_decisions(session, cycle_id):
    return session.scalar(
        db.select(db.func.count())
        .select_from(ARBDecisionEvent)
        .where(ARBDecisionEvent.review_cycle_id == cycle_id)
    )


def _conditional_payload(*, rationale="Approval requires verified evidence."):
    return {
        "conditions": [
            {
                "description": "Publish deployment evidence",
                "category": "delivery",
            }
        ],
        "approval_notes": rationale,
    }


def _condition_evidence():
    now = datetime.now(timezone.utc)
    return {
        "source_identity": f"route-evidence:{uuid.uuid4().hex}",
        "source_type": "cmdb",
        "source_version": "1",
        "source_checksum": "a" * 64,
        "value_json": {"deployment_verified": True},
        "observed_at": (now - timedelta(minutes=1)).isoformat(),
        "freshness_rule_version": "arb-condition-v1",
        "freshness_expires_at": (now + timedelta(days=1)).isoformat(),
    }


def _login_client(app, user_id):
    from flask import has_app_context

    client = app.test_client()
    from tests._session_test_helpers import mint_test_sid

    _sid = mint_test_sid(user_id, app=app)
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
        if _sid:
            session["_sid"] = _sid
    if has_app_context():
        for cached in ("_login_user", "_current_user", "current_org_id", "current_org"):
            if hasattr(g, cached):
                delattr(g, cached)
    return client


def _call_arb_route(
    app,
    *,
    function_name,
    item_id,
    user_id,
    organization_id,
    method="POST",
    data=None,
    json=None,
    headers=None,
):
    del organization_id
    route = {
        "record_decision": f"/arb/reviews/{item_id}/decision",
        "api_arb_begin_review": f"/arb/api/arb/{item_id}/review",
        "api_arb_approve": f"/arb/api/arb/{item_id}/approve",
        "api_arb_reject": f"/arb/api/arb/{item_id}/reject",
        "api_arb_request_changes": f"/arb/api/arb/{item_id}/request-changes",
        "reopen_decision": f"/arb/reviews/{item_id}/reopen",
        "api_arb_get_implementation_status": (
            f"/arb/api/arb/{item_id}/implementation-status"
        ),
        "api_arb_update_implementation_status": (
            f"/arb/api/arb/{item_id}/implementation-status"
        ),
    }[function_name]
    client = _login_client(app, user_id)
    return client.open(route, method=method, data=data, json=json, headers=headers)


def _call_route(
    app,
    *,
    module,
    function_name,
    args,
    user_id,
    organization_id,
    method="POST",
    data=None,
    json=None,
    headers=None,
):
    del module, organization_id
    if function_name == "record_arb_decision":
        route = f"/api/solutions/{args[0]}/arb/{args[1]}/record-decision"
    else:
        action = {
            "begin_arb_review": "begin-review",
            "approve_solution": "approve",
            "reject_solution": "reject",
            "withdraw_solution": "withdraw",
        }[function_name]
        route = f"/api/arb-workflow/solutions/{args[0]}/{action}"
    client = _login_client(app, user_id)
    return client.open(route, method=method, data=data, json=json, headers=headers)


@pytest.fixture
def route_scope(app, _schema, request):
    """Committed tenant graph visible to the command service's own sessions."""
    scope = SimpleNamespace(
        organization_id=None,
        foreign_organization_id=None,
        submitter_id=None,
        decider_id=None,
        cto_id=None,
        invalid_authority_id=None,
        foreign_decider_id=None,
        review_id=None,
        cycle_id=None,
        solution_id=None,
        solution_review_id=None,
        solution_cycle_id=None,
        role_id=None,
    )

    def cleanup():
        if scope.organization_id is None:
            return
        with app.app_context():
            db.session.remove()
            raw = db.engine.raw_connection()
            try:
                with raw.cursor() as cursor:
                    cursor.execute("SET LOCAL session_replication_role = replica")
                    for table_name in (
                        "arb_condition_events",
                        "arb_canonical_conditions",
                        "arb_condition_evidence_records",
                        "workbench_artifact_evidence",
                        "arb_submission_evidence_snapshots",
                    ):
                        cursor.execute(
                            f'DELETE FROM "{table_name}" '
                            "WHERE organization_id IN (%s, %s)",
                            (scope.organization_id, scope.foreign_organization_id),
                        )
                raw.commit()
            finally:
                raw.close()
            with db.engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
                connection.execute(
                    db.text(
                        "DELETE FROM arb_conditions WHERE review_item_id IN ("
                        "SELECT id FROM arb_review_items "
                        "WHERE organization_id IN (:own, :foreign))"
                    ),
                    {
                        "own": scope.organization_id,
                        "foreign": scope.foreign_organization_id,
                    },
                )
                for table_name in (
                    "arb_decision_events",
                    "arb_submission_events",
                    "transformation_outbox_events",
                    "operation_results",
                    "command_materialisations",
                    "command_idempotency_records",
                    "arb_review_items",
                    "arb_review_cycles",
                    "solution_arb_reviews",
                    "arb_subject_evidence_snapshots",
                    "solution_risks",
                    "solution_goals",
                    "solution_drivers",
                    "solutions",
                    "solution_problem_definitions",
                    "solution_analysis_sessions",
                    "architecture_decisions",
                ):
                    connection.execute(
                        db.text(
                            f'DELETE FROM "{table_name}" '
                            "WHERE organization_id IN (:own, :foreign)"
                        ),
                        {
                            "own": scope.organization_id,
                            "foreign": scope.foreign_organization_id,
                        },
                    )
                for table_name in ("soc2_audit_log", "solution_notifications"):
                    connection.execute(
                        db.text(
                            f'DELETE FROM "{table_name}" '
                            "WHERE user_id IN (SELECT id FROM users "
                            "WHERE organization_id IN (:own, :foreign))"
                        ),
                        {
                            "own": scope.organization_id,
                            "foreign": scope.foreign_organization_id,
                        },
                    )
                connection.execute(
                    db.text(
                        "DELETE FROM subscriptions WHERE organization_id IN (:own, :foreign)"
                    ),
                    {
                        "own": scope.organization_id,
                        "foreign": scope.foreign_organization_id,
                    },
                )
                connection.execute(
                    db.text(
                        "DELETE FROM users WHERE organization_id IN (:own, :foreign)"
                    ),
                    {
                        "own": scope.organization_id,
                        "foreign": scope.foreign_organization_id,
                    },
                )
                connection.execute(
                    db.text("DELETE FROM organizations WHERE id IN (:own, :foreign)"),
                    {
                        "own": scope.organization_id,
                        "foreign": scope.foreign_organization_id,
                    },
                )
                if scope.role_id is not None:
                    connection.execute(
                        db.text("DELETE FROM roles WHERE id = :role_id"),
                        {"role_id": scope.role_id},
                    )

    # Register before the first committed write so setup exceptions cannot leak.
    request.addfinalizer(cleanup)
    with app.app_context():
        own_suffix = uuid.uuid4().hex[:10]
        foreign_suffix = uuid.uuid4().hex[:10]
        org = Organization(
            name=f"Typed route {own_suffix}", slug=f"typed-route-{own_suffix}"
        )
        foreign_org = Organization(
            name=f"Typed route foreign {foreign_suffix}",
            slug=f"typed-route-foreign-{foreign_suffix}",
        )
        db.session.add_all((org, foreign_org))
        db.session.flush()
        scope.organization_id = org.id
        scope.foreign_organization_id = foreign_org.id
        from app.services.billing_plans import set_contract_plan

        set_contract_plan(org, "enterprise", None)  # more people than Community admits
        write_role = _write_role(db.session)
        scope.role_id = write_role.id
        submitter = _user(db.session, org, write_role=write_role)
        decider = _user(
            db.session,
            org,
            role="enterprise_architect",
            role_archetype="enterprise_architect",
            write_role=write_role,
        )
        foreign_decider = _user(
            db.session,
            foreign_org,
            role="enterprise_architect",
            role_archetype="enterprise_architect",
            write_role=write_role,
        )
        cto = _user(
            db.session,
            org,
            role="cto",
            role_archetype="manager",
            write_role=write_role,
        )
        invalid_authority = _user(
            db.session,
            org,
            role="application_manager",
            role_archetype="engineer",
            write_role=write_role,
        )
        scope.submitter_id = submitter.id
        scope.decider_id = decider.id
        scope.cto_id = cto.id
        scope.invalid_authority_id = invalid_authority.id
        scope.foreign_decider_id = foreign_decider.id
        solution, workspace = _solution_review(db.session, org, submitter)
        db.session.commit()
        _record, review = _typed_adr_review(db.session, org, submitter)
        with app.test_request_context("/"):
            g.current_org_id = org.id
            solution_result = TypedARBSubmissionService.submit_legacy_solution(
                actor=ActorContext(
                    submitter.id,
                    org.id,
                    frozenset({submitter.enterprise_role}),
                    f"typed-route-solution-{uuid.uuid4().hex}",
                ),
                command_key=f"typed-route-solution-{uuid.uuid4().hex}",
                solution_id=solution.id,
                workspace_id=workspace.id,
                assertions={
                    "human_reviewed": True,
                    "direct_route_evidence": {
                        name: {"passed": True, "evidence": f"{name} checked"}
                        for name in (
                            "design_reviewed",
                            "security_impact_reviewed",
                            "data_impact_reviewed",
                        )
                    },
                },
            )
        scope.review_id = review.id
        scope.cycle_id = review.review_cycle_id
        scope.solution_id = solution.id
        scope.solution_review_id = solution_result.object_ids["review_item_id"]
        scope.solution_cycle_id = solution_result.object_ids["review_cycle_id"]
        db.session.remove()
        yield scope


@pytest.mark.parametrize(
    ("submitted", "expected"),
    [
        ("approved", "approved"),
        ("approved_with_conditions", "approved_with_conditions"),
        ("rejected", "rejected"),
        ("returned_for_evidence", "returned_for_evidence"),
        ("returned_for_options", "returned_for_options"),
    ],
)
def test_html_decision_route_maps_all_five_typed_outcomes(
    app, route_scope, submitted, expected
):
    form = {"decision": submitted, "rationale": f"Board chose {expected}."}
    if submitted == "approved_with_conditions":
        form["conditions"] = "Complete threat model"

    response = _call_arb_route(
        app,
        function_name="record_decision",
        item_id=route_scope.review_id,
        user_id=route_scope.decider_id,
        organization_id=route_scope.organization_id,
        data=form,
    )

    assert response.status_code == 302
    db.session.remove()
    cycle = db.session.get(ARBReviewCycle, route_scope.cycle_id)
    projected = db.session.get(ARBReviewItem, route_scope.review_id)
    assert cycle.status == cycle.terminal_outcome == expected
    assert projected.status == projected.decision == expected
    assert _count_decisions(db.session, cycle.id) == 1


def test_html_conditions_are_canonical_and_never_invent_a_due_date(
    app, route_scope
):
    response = _call_arb_route(
        app,
        function_name="record_decision",
        item_id=route_scope.review_id,
        user_id=route_scope.decider_id,
        organization_id=route_scope.organization_id,
        data={
            "decision": "approved_with_conditions",
            "rationale": "Approval depends on evidence.",
            "conditions": " Complete threat model \n\nPublish rollback evidence ",
        },
    )

    assert response.status_code == 302
    db.session.remove()
    conditions = db.session.scalars(
        db.select(ARBCondition)
        .where(ARBCondition.review_item_id == route_scope.review_id)
        .order_by(ARBCondition.condition_number)
    ).all()
    assert [(row.condition_number, row.description, row.due_date) for row in conditions] == [
        ("COND-1", "Complete threat model", None),
        ("COND-2", "Publish rollback evidence", None),
    ]


def test_html_decision_ignores_client_actor_tenant_status_and_subject_fields(
    app, route_scope
):
    response = _call_arb_route(
        app,
        function_name="record_decision",
        item_id=route_scope.review_id,
        user_id=route_scope.decider_id,
        organization_id=route_scope.organization_id,
        data={
            "decision": "approved",
            "rationale": "Server identity remains authoritative.",
            "decided_by_id": route_scope.submitter_id,
            "actor_id": route_scope.submitter_id,
            "organization_id": route_scope.organization_id + 9000,
            "status": "rejected",
            "solution_id": 999999,
        },
    )

    assert response.status_code == 302
    db.session.remove()
    event = db.session.scalar(
        db.select(ARBDecisionEvent).where(
            ARBDecisionEvent.review_cycle_id == route_scope.cycle_id
        )
    )
    assert event.actor_id == route_scope.decider_id
    assert event.organization_id == route_scope.organization_id
    assert event.outcome == "approved"
    assert event.subject_type == "adr"


def test_cross_tenant_review_id_is_a_non_disclosing_404(
    app, route_scope
):
    response = _call_arb_route(
        app,
        function_name="api_arb_approve",
        item_id=route_scope.review_id,
        user_id=route_scope.foreign_decider_id,
        organization_id=route_scope.foreign_organization_id,
        json={"notes": "Must not reveal the foreign review."},
        headers={"Idempotency-Key": f"foreign-{uuid.uuid4().hex}"},
    )

    assert response.status_code == 404
    assert response.get_json()["success"] is False
    assert _count_decisions(db.session, route_scope.cycle_id) == 0


def test_submitter_cannot_decide_own_typed_review(
    app, route_scope
):
    response = _call_arb_route(
        app,
        function_name="api_arb_approve",
        item_id=route_scope.review_id,
        user_id=route_scope.submitter_id,
        organization_id=route_scope.organization_id,
        json={"notes": "Self approval must fail."},
    )

    assert response.status_code == 403
    assert response.get_json()["success"] is False
    assert _count_decisions(db.session, route_scope.cycle_id) == 0


def test_api_terminal_replay_is_idempotent_and_conflicting_decision_is_409(
    app, route_scope
):
    headers = {"Idempotency-Key": f"typed-decision-{uuid.uuid4().hex}"}

    first = _call_arb_route(
        app,
        function_name="api_arb_reject",
        item_id=route_scope.review_id,
        user_id=route_scope.decider_id,
        organization_id=route_scope.organization_id,
        json={"reason": "Evidence is incomplete."},
        headers=headers,
    )
    replay = _call_arb_route(
        app,
        function_name="api_arb_reject",
        item_id=route_scope.review_id,
        user_id=route_scope.decider_id,
        organization_id=route_scope.organization_id,
        json={"reason": "Evidence is incomplete."},
        headers=headers,
    )
    conflict = _call_arb_route(
        app,
        function_name="api_arb_approve",
        item_id=route_scope.review_id,
        user_id=route_scope.decider_id,
        organization_id=route_scope.organization_id,
        json={"notes": "Try to overwrite the decision."},
        headers={"Idempotency-Key": f"other-{uuid.uuid4().hex}"},
    )

    assert first.status_code == replay.status_code == 200
    assert first.get_json()["idempotent"] is False
    assert replay.get_json()["idempotent"] is True
    assert conflict.status_code == 409
    assert _count_decisions(db.session, route_scope.cycle_id) == 1


def test_request_changes_creates_conditional_typed_decision(
    app, route_scope
):
    response = _call_arb_route(
        app,
        function_name="api_arb_request_changes",
        item_id=route_scope.review_id,
        user_id=route_scope.decider_id,
        organization_id=route_scope.organization_id,
        json={
            "conditions": ["Document data retention", "Add recovery evidence"],
            "notes": "Proceed only after both controls are evidenced.",
            "decided_by_id": route_scope.submitter_id,
        },
    )

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["status"] == body["outcome"] == "approved_with_conditions"
    assert len(body["condition_ids"]) == 2
    assert all(condition["due_date"] is None for condition in body["conditions"])


def test_begin_review_projects_cycle_and_item_without_solution_status_write(
    app, route_scope
):
    response = _call_arb_route(
        app,
        function_name="api_arb_begin_review",
        item_id=route_scope.review_id,
        user_id=route_scope.decider_id,
        organization_id=route_scope.organization_id,
        json={"status": "approved"},
    )

    assert response.status_code == 409
    assert response.get_json()["reason_codes"] == [
        "typed_cycle_status_not_client_mutable"
    ]
    db.session.remove()
    cycle = db.session.get(ARBReviewCycle, route_scope.cycle_id)
    projected = db.session.get(ARBReviewItem, route_scope.review_id)
    assert cycle.status == projected.status == "submitted"
    assert projected.reviewer_id is None
    assert projected.review_started_at is None


def test_registered_conditional_approval_is_typed_and_does_not_require_due_date(
    app, route_scope
):
    authority = db.session.get(User, route_scope.decider_id)
    assert authority.enterprise_role == "enterprise_architect"
    assert authority.can(Permission.GENERAL)
    assert not authority.can(Permission.ADMINISTER)
    client = _login_client(app, route_scope.decider_id)

    response = client.post(
        f"/api/arb-workflow/{route_scope.review_id}/conditional-approval",
        json=_conditional_payload(),
        headers={"Idempotency-Key": f"route-conditional-{uuid.uuid4().hex}"},
    )

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["success"] is True
    assert body["data"]["status"] == "approved_with_conditions"
    assert len(body["data"]["condition_ids"]) == 1
    condition = db.session.get(ARBCondition, body["data"]["condition_ids"][0])
    assert condition.due_date is None
    assert _count_decisions(db.session, route_scope.cycle_id) == 1


def test_registered_typed_transition_routes_decision_and_rejects_stage_mutation(
    app, route_scope
):
    client = _login_client(app, route_scope.decider_id)

    unsupported = client.post(
        f"/api/arb-workflow/{route_scope.review_id}/transition",
        json={"target_stage": "under_review", "notes": "Legacy stage mutation"},
    )
    decided = client.post(
        f"/api/arb-workflow/{route_scope.review_id}/transition",
        json={"target_stage": "rejected", "notes": "The evidence is insufficient."},
        headers={"Idempotency-Key": f"route-transition-{uuid.uuid4().hex}"},
    )

    assert unsupported.status_code == 409
    assert unsupported.get_json()["reason_codes"] == [
        "typed_stage_transition_not_supported"
    ]
    assert decided.status_code == 200, decided.get_json()
    assert decided.get_json()["data"]["status"] == "rejected"
    db.session.remove()
    assert db.session.get(ARBReviewCycle, route_scope.cycle_id).status == "rejected"


def test_registered_typed_fulfill_requires_capture_submit_then_separate_verify(
    app, route_scope
):
    decider_client = _login_client(app, route_scope.decider_id)
    conditional = decider_client.post(
        f"/api/arb-workflow/{route_scope.review_id}/conditional-approval",
        json=_conditional_payload(),
    ).get_json()["data"]
    condition_id = conditional["condition_ids"][0]

    submitter_client = _login_client(app, route_scope.submitter_id)
    submitted = submitter_client.post(
        f"/api/arb-workflow/conditions/{condition_id}/fulfill",
        json={
            "governance_model": "typed",
            "review_item_id": route_scope.review_id,
            "action": "submit_evidence",
            "evidence": _condition_evidence(),
        },
        headers={"Idempotency-Key": f"route-evidence-{uuid.uuid4().hex}"},
    )

    assert submitted.status_code == 200, submitted.get_json()
    submitted_data = submitted.get_json()["data"]
    assert submitted_data["status"] == "evidence_submitted"
    evidence_id = submitted_data["condition_evidence_id"]

    verified = _login_client(app, route_scope.decider_id).post(
        f"/api/arb-workflow/conditions/{condition_id}/fulfill",
        json={
            "governance_model": "typed",
            "review_item_id": route_scope.review_id,
            "action": "verify",
            "condition_evidence_id": evidence_id,
        },
        headers={"Idempotency-Key": f"route-verify-{uuid.uuid4().hex}"},
    )

    assert verified.status_code == 200, verified.get_json()
    assert verified.get_json()["data"]["status"] == "fulfilled"
    db.session.remove()
    assert db.session.get(ARBCondition, condition_id).status == "fulfilled"
    assert db.session.get(ARBReviewCycle, route_scope.cycle_id).status == "approved"


def test_registered_typed_waiver_uses_canonical_lifecycle_command(app, route_scope):
    client = _login_client(app, route_scope.decider_id)
    conditional = client.post(
        f"/api/arb-workflow/{route_scope.review_id}/conditional-approval",
        json=_conditional_payload(),
    ).get_json()["data"]
    condition_id = conditional["condition_ids"][0]

    response = client.post(
        f"/api/arb-workflow/conditions/{condition_id}/waive",
        json={
            "governance_model": "typed",
            "review_item_id": route_scope.review_id,
            "reason": "Temporary release exception",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "scope": {"release": "R1"},
            "compensating_control": "Daily deployment evidence review",
        },
        headers={"Idempotency-Key": f"route-waive-{uuid.uuid4().hex}"},
    )

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data"]["status"] == "waived"
    db.session.remove()
    assert db.session.get(ARBCondition, condition_id).status == "waived"
    assert db.session.get(ARBReviewCycle, route_scope.cycle_id).status == "approved"


def test_forged_typed_condition_review_context_is_rejected_before_capture(
    app, route_scope
):
    client = _login_client(app, route_scope.decider_id)
    conditional = client.post(
        f"/api/arb-workflow/{route_scope.review_id}/conditional-approval",
        json=_conditional_payload(),
    ).get_json()["data"]
    condition_id = conditional["condition_ids"][0]

    response = _login_client(app, route_scope.submitter_id).post(
        f"/api/arb-workflow/conditions/{condition_id}/fulfill",
        json={
            "governance_model": "typed",
            "review_item_id": route_scope.solution_review_id,
            "action": "submit_evidence",
            "evidence": _condition_evidence(),
        },
    )

    assert response.status_code == 404
    db.session.remove()
    assert db.session.get(ARBCondition, condition_id).status == "pending"


def _legacy_condition_with_typed_id(route_scope, condition_id):
    review = ARBReviewItem(
        organization_id=route_scope.organization_id,
        review_number=f"REV-LEGACY-COND-{uuid.uuid4().hex[:8]}",
        title="Legacy condition collision",
        review_type="strategic",
        status="approved_with_conditions",
        decision="approved_with_conditions",
        submitter_id=route_scope.submitter_id,
        submitted_at=datetime.utcnow(),
    )
    db.session.add(review)
    db.session.flush()
    condition = LegacyARBCondition(
        id=condition_id,
        review_item_id=review.id,
        condition_number=1,
        description="Legacy condition sharing a canonical numeric ID",
        due_date=(datetime.utcnow() + timedelta(days=30)).date(),
        status="pending",
    )
    db.session.add(condition)
    db.session.commit()
    return review.id


def test_legacy_fulfill_same_id_collision_never_selects_typed_condition(
    app, route_scope
):
    client = _login_client(app, route_scope.decider_id)
    conditional = client.post(
        f"/api/arb-workflow/{route_scope.review_id}/conditional-approval",
        json=_conditional_payload(),
    ).get_json()["data"]
    condition_id = conditional["condition_ids"][0]
    legacy_review_id = _legacy_condition_with_typed_id(route_scope, condition_id)

    response = _login_client(app, route_scope.decider_id).post(
        f"/api/arb-workflow/conditions/{condition_id}/fulfill",
        json={"evidence": "Legacy evidence remains on the legacy review."},
    )

    assert response.status_code == 200, response.get_json()
    db.session.remove()
    assert db.session.get(LegacyARBCondition, condition_id).status == "fulfilled"
    assert db.session.get(LegacyARBCondition, condition_id).review_item_id == legacy_review_id
    assert db.session.get(ARBCondition, condition_id).status == "pending"


def test_legacy_waive_same_id_collision_never_selects_typed_condition(
    app, route_scope
):
    client = _login_client(app, route_scope.decider_id)
    conditional = client.post(
        f"/api/arb-workflow/{route_scope.review_id}/conditional-approval",
        json=_conditional_payload(),
    ).get_json()["data"]
    condition_id = conditional["condition_ids"][0]
    legacy_review_id = _legacy_condition_with_typed_id(route_scope, condition_id)

    response = _login_client(app, route_scope.decider_id).post(
        f"/api/arb-workflow/conditions/{condition_id}/waive",
        json={"reason": "Legacy waiver remains on the legacy review."},
    )

    assert response.status_code == 200, response.get_json()
    db.session.remove()
    assert db.session.get(LegacyARBCondition, condition_id).status == "waived"
    assert db.session.get(LegacyARBCondition, condition_id).review_item_id == legacy_review_id
    assert db.session.get(ARBCondition, condition_id).status == "pending"


def test_solution_condition_toggle_rejects_typed_json_mutation(app, route_scope):
    client = _login_client(app, route_scope.decider_id)
    conditional = client.post(
        f"/api/arb-workflow/{route_scope.solution_review_id}/conditional-approval",
        json=_conditional_payload(),
    ).get_json()["data"]
    condition_id = conditional["condition_ids"][0]
    db.session.remove()
    before = db.session.get(ARBReviewItem, route_scope.solution_review_id).conditions

    response = _login_client(app, route_scope.decider_id).post(
        f"/solutions/{route_scope.solution_id}/arb-condition/0/toggle"
    )

    assert response.status_code == 409, response.get_json()
    assert response.get_json()["reason_codes"] == [
        "typed_condition_toggle_not_supported"
    ]
    db.session.remove()
    assert db.session.get(ARBReviewItem, route_scope.solution_review_id).conditions == before
    assert db.session.get(ARBCondition, condition_id).status == "pending"


def test_solution_lifecycle_get_uses_typed_graph_not_stale_solution_projection(
    app, route_scope
):
    client = _login_client(app, route_scope.decider_id)
    decision = client.post(
        f"/api/arb-workflow/{route_scope.solution_review_id}/conditional-approval",
        json=_conditional_payload(),
    ).get_json()["data"]

    response = client.get(
        f"/api/arb-workflow/solutions/{route_scope.solution_id}/lifecycle"
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["governance_status"] == "approved_with_conditions"
    assert body["review_cycle_id"] == route_scope.solution_cycle_id
    assert body["review_item_id"] == route_scope.solution_review_id
    assert body["decision_event_id"] == decision["decision_event_id"]
    assert body["can_withdraw"] is False
    assert body["allowed_transitions"] == ["submit_condition_evidence", "waive_condition"]
    assert body["conditions"][0]["status"] == "pending"
    assert db.session.get(Solution, route_scope.solution_id).governance_status == "draft"


def test_registered_conditional_approval_serializes_concurrent_commands(
    app, route_scope
):
    command_keys = [
        f"route-concurrent-{uuid.uuid4().hex}",
        f"route-concurrent-{uuid.uuid4().hex}",
    ]

    def decide(command_key):
        client = _login_client(app, route_scope.decider_id)
        response = client.post(
            f"/api/arb-workflow/{route_scope.review_id}/conditional-approval",
            json=_conditional_payload(),
            headers={"Idempotency-Key": command_key},
        )
        return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(decide, command_keys))

    assert all(status in {200, 409} for status, _body in results), results
    assert any(status == 200 for status, _body in results), results
    for status, body in results:
        if status == 409:
            assert body["reason_codes"] == ["arb_cycle_already_terminal"]

    db.session.remove()
    assert _count_decisions(db.session, route_scope.cycle_id) == 1


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/arb/reviews/{review_id}/reopen"),
        ("get", "/arb/api/arb/{review_id}/implementation-status"),
        ("patch", "/arb/api/arb/{review_id}/implementation-status"),
    ],
)
def test_typed_reopen_and_raw_implementation_mutation_are_rejected(
    app, route_scope, method, path
):
    response = _call_arb_route(
        app,
        function_name=(
            "reopen_decision"
            if method == "post"
            else (
                "api_arb_get_implementation_status"
                if method == "get"
                else "api_arb_update_implementation_status"
            )
        ),
        item_id=route_scope.review_id,
        user_id=route_scope.decider_id,
        organization_id=route_scope.organization_id,
        method=method.upper(),
        data={"reopen_reason": "rewrite"} if method == "post" else None,
        json={"implementation_status": "completed"} if method == "patch" else None,
    )

    assert response.status_code == 409
    assert _count_decisions(db.session, route_scope.cycle_id) == 0


def test_solution_decision_route_rejects_subject_review_mismatch(
    client, login_as, route_scope
):
    login_as(client, route_scope.decider_id)

    response = client.post(
        f"/api/solutions/{route_scope.solution_id}/arb/{route_scope.review_id}/record-decision",
        json={
            "decision": "approved",
            "decision_reason": "Forged subject/review association.",
            "decided_by_id": route_scope.decider_id,
        },
    )

    assert response.status_code == 404
    assert _count_decisions(db.session, route_scope.cycle_id) == 0


def test_solution_governance_decision_uses_typed_cycle_and_server_actor(
    app, route_scope
):
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    authority = db.session.get(User, route_scope.decider_id)
    assert authority.enterprise_role == "enterprise_architect"
    assert authority.can(Permission.GENERAL)
    assert not authority.can(Permission.ADMINISTER)

    response = _call_route(
        app,
        module=governance_api_routes,
        function_name="record_arb_decision",
        args=(route_scope.solution_id, route_scope.solution_review_id),
        user_id=route_scope.decider_id,
        organization_id=route_scope.organization_id,
        json={
            "decision": "approved",
            "decision_reason": "The pinned solution evidence is sufficient.",
            "decided_by_id": route_scope.submitter_id,
            "status": "rejected",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["review_cycle_id"] == route_scope.solution_cycle_id
    db.session.remove()
    event = db.session.scalar(
        db.select(ARBDecisionEvent).where(
            ARBDecisionEvent.review_cycle_id == route_scope.solution_cycle_id
        )
    )
    solution = db.session.get(Solution, route_scope.solution_id)
    assert event.actor_id == route_scope.decider_id
    assert event.outcome == "approved"
    assert solution.governance_status == "draft"


def test_solution_governance_typed_response_keeps_legacy_fields_and_notifies_once(
    app, route_scope
):
    client = _login_client(app, route_scope.decider_id)
    command_key = f"solution-response-{uuid.uuid4().hex}"
    payload = {
        "decision": "approved",
        "decision_reason": "Pinned evidence is sufficient.",
    }

    first = client.post(
        f"/api/solutions/{route_scope.solution_id}/arb/"
        f"{route_scope.solution_review_id}/record-decision",
        json=payload,
        headers={"Idempotency-Key": command_key},
    )
    replay = client.post(
        f"/api/solutions/{route_scope.solution_id}/arb/"
        f"{route_scope.solution_review_id}/record-decision",
        json=payload,
        headers={"Idempotency-Key": command_key},
    )

    assert first.status_code == replay.status_code == 200
    body = first.get_json()
    assert {
        "id",
        "solution_id",
        "submitted_at",
        "arb_decision",
        "decided_at",
        "arb_attendees",
        "conditions",
        "compliance_areas_reviewed",
        "next_steps",
        "next_review_date",
    }.issubset(body)
    assert body["id"] == route_scope.solution_review_id
    assert body["solution_id"] == route_scope.solution_id
    assert body["arb_decision"] == "approved"
    assert replay.get_json()["idempotent"] is True
    db.session.remove()
    notifications = db.session.scalars(
        db.select(SolutionNotification).where(
            SolutionNotification.solution_id == route_scope.solution_id,
            SolutionNotification.user_id == route_scope.submitter_id,
            SolutionNotification.type == "arb_submission",
        )
    ).all()
    assert len(notifications) == 1


def test_legacy_solution_decision_rejects_same_tenant_review_subject_collision(
    app, route_scope
):
    collision_id = 8_000_000 + int(uuid.uuid4().hex[:5], 16)
    with app.app_context():
        other = Solution(
            organization_id=route_scope.organization_id,
            name=f"Legacy collision solution {uuid.uuid4().hex[:8]}",
            description="Must not be decided through another solution URL.",
            created_by_id=route_scope.submitter_id,
            governance_status="draft",
        )
        db.session.add(other)
        db.session.flush()
        untyped = ARBReviewItem(
            id=collision_id,
            organization_id=route_scope.organization_id,
            review_number=f"REV-COLLIDE-{uuid.uuid4().hex[:8]}",
            title="Same-tenant untyped collision",
            review_type="strategic",
            status="under_review",
            submitter_id=route_scope.submitter_id,
        )
        legacy = SolutionARBReview(
            id=collision_id,
            organization_id=route_scope.organization_id,
            solution_id=other.id,
            submitted_by_id=route_scope.submitter_id,
            arb_decision="pending",
        )
        db.session.add_all((untyped, legacy))
        db.session.commit()
        other_id = other.id

    client = _login_client(app, route_scope.decider_id)
    response = client.post(
        f"/api/solutions/{route_scope.solution_id}/arb/{collision_id}/record-decision",
        json={
            "decision": "approved",
            "decision_reason": "Wrong URL subject must not adopt this review.",
        },
    )

    assert response.status_code == 404
    db.session.remove()
    assert db.session.get(SolutionARBReview, collision_id).arb_decision == "pending"
    assert db.session.get(Solution, other_id).governance_status == "draft"


def test_solution_workflow_begin_and_reject_project_typed_review_only(
    app, route_scope
):
    from app.modules.architecture.routes import arb_workflow_routes

    begun = _call_route(
        app,
        module=arb_workflow_routes,
        function_name="begin_arb_review",
        args=(route_scope.solution_id,),
        user_id=route_scope.decider_id,
        organization_id=route_scope.organization_id,
        json={"status": "approved", "notes": "Begin the board review."},
    )
    rejected = _call_route(
        app,
        module=arb_workflow_routes,
        function_name="reject_solution",
        args=(route_scope.solution_id,),
        user_id=route_scope.decider_id,
        organization_id=route_scope.organization_id,
        json={"reason": "Controls are incomplete.", "decided_by_id": route_scope.submitter_id},
    )

    assert begun.status_code == 409
    assert begun.get_json()["reason_codes"] == [
        "typed_cycle_status_not_client_mutable"
    ]
    assert rejected.status_code == 200
    assert rejected.get_json()["governance_status"] == "rejected"
    db.session.remove()
    cycle = db.session.get(ARBReviewCycle, route_scope.solution_cycle_id)
    review = db.session.get(ARBReviewItem, route_scope.solution_review_id)
    solution = db.session.get(Solution, route_scope.solution_id)
    assert cycle.status == review.status == "rejected"
    assert review.reviewer_id is None
    assert solution.governance_status == "draft"


def test_solution_workflow_cto_authority_reaches_typed_service_without_solution_write(
    app, route_scope
):
    from app.modules.architecture.routes import arb_workflow_routes

    response = _call_route(
        app,
        module=arb_workflow_routes,
        function_name="approve_solution",
        args=(route_scope.solution_id,),
        user_id=route_scope.cto_id,
        organization_id=route_scope.organization_id,
        json={"notes": "The pinned evidence meets the governance bar."},
    )

    assert response.status_code == 200
    assert response.get_json()["governance_status"] == "approved"
    db.session.remove()
    event = db.session.scalar(
        db.select(ARBDecisionEvent).where(
            ARBDecisionEvent.review_cycle_id == route_scope.solution_cycle_id
        )
    )
    assert event.actor_id == route_scope.cto_id
    assert db.session.get(ARBReviewCycle, route_scope.solution_cycle_id).status == "approved"
    assert db.session.get(Solution, route_scope.solution_id).governance_status == "draft"


def test_solution_workflow_invalid_enterprise_role_is_denied_by_typed_service(
    app, route_scope
):
    from app.modules.architecture.routes import arb_workflow_routes

    authority = db.session.get(User, route_scope.invalid_authority_id)
    assert authority.can(Permission.GENERAL)
    assert authority.enterprise_role == "application_manager"
    response = _call_route(
        app,
        module=arb_workflow_routes,
        function_name="approve_solution",
        args=(route_scope.solution_id,),
        user_id=route_scope.invalid_authority_id,
        organization_id=route_scope.organization_id,
        json={"notes": "A write-capable but unauthorized decision attempt."},
    )

    assert response.status_code == 403
    assert response.get_json()["reason_codes"] == ["arb_decision_not_authorised"]
    db.session.remove()
    assert _count_decisions(db.session, route_scope.solution_cycle_id) == 0
    assert db.session.get(ARBReviewCycle, route_scope.solution_cycle_id).status == "submitted"


def test_solution_workflow_withdraw_rejects_typed_cycle_without_mutation(
    app, route_scope
):
    from app.modules.architecture.routes import arb_workflow_routes

    response = _call_route(
        app,
        module=arb_workflow_routes,
        function_name="withdraw_solution",
        args=(route_scope.solution_id,),
        user_id=route_scope.submitter_id,
        organization_id=route_scope.organization_id,
        json={"reason": "Attempt to bypass append-only governance."},
    )

    assert response.status_code == 409
    assert response.get_json()["reason_codes"] == ["typed_withdraw_not_supported"]
    db.session.remove()
    assert db.session.get(ARBReviewCycle, route_scope.solution_cycle_id).status == "submitted"
    assert db.session.get(Solution, route_scope.solution_id).governance_status == "draft"


def test_typed_decision_route_remains_csrf_protected(
    app, client, login_as, route_scope
):
    login_as(client, route_scope.decider_id)
    original = app.config["WTF_CSRF_ENABLED"]
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = client.post(
            f"/arb/api/arb/{route_scope.review_id}/approve",
            json={"notes": "No CSRF token."},
        )
    finally:
        app.config["WTF_CSRF_ENABLED"] = original

    assert response.status_code == 400
    assert _count_decisions(db.session, route_scope.cycle_id) == 0


def test_legacy_html_decision_keeps_existing_service_path(
    app, db_session, make_org, monkeypatch
):
    from flask import g
    from flask_login import login_user
    from app.modules.architecture.routes import arb_routes

    org = make_org("typed-route-legacy")
    submitter = _user(db_session, org)
    decider = _user(db_session, org, role="chief_architect", admin=True)
    legacy = ARBReviewItem(
        organization_id=org.id,
        review_number=f"REV-LEGACY-{uuid.uuid4().hex[:10]}",
        title="Legacy generic review",
        description="Not a typed subject.",
        review_type="strategic",
        status="under_review",
        submitter_id=submitter.id,
        submitted_at=datetime.utcnow(),
    )
    db_session.add(legacy)
    db_session.flush()
    calls = []
    monkeypatch.setattr(
        arb_routes.arb_service,
        "record_decision",
        lambda **kwargs: calls.append(kwargs) or legacy,
    )
    with app.test_request_context(
        "/", method="POST", data={"decision": "approved", "rationale": "Legacy"}
    ):
        g.current_org_id = org.id
        login_user(decider)
        response = arb_routes.record_decision.__wrapped__.__wrapped__(legacy.id)

    assert response.status_code == 302
    assert calls and calls[0]["review_item_id"] == legacy.id
