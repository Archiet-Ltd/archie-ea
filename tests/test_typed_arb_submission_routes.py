"""Route-level convergence of ADR / Architecture Model ARB submission.

Two halves:

* **Committed integration** — drives the real typed command end to end through
  the HTTP ingress.  ``CommandService`` uses independent sessions, so the shared
  rolled-back ``db_session`` cannot drive it; this module therefore uses the
  committed-harness pattern from ``test_arb_condition_lifecycle_integration``.
* **Contract tests** — envelope aliases, forged-field rejection, cross-tenant
  404s and status mapping, driven against the ingress with a stubbed command.

Both halves use the shared fixtures in ``tests/conftest.py``.
"""

from __future__ import annotations

import inspect
import os
import uuid

import pytest
from flask import g
from flask_login import login_user


os.environ.setdefault("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "74" * 32)


_CLEANUP_TABLES = (
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
def committed_session(app, _schema):
    """Committed setup visible to CommandService's independent sessions."""
    from app import db

    with app.app_context():
        db.session.remove()
        cleanup_org_ids = set()
        db.session.info["cleanup_org_ids"] = cleanup_org_ids
        try:
            yield db.session
        finally:
            # CommandService removes its scoped session. Keep cleanup authority
            # outside Session.info so committed test rows cannot be orphaned.
            organization_ids = tuple(cleanup_org_ids)
            db.session.remove()
            if organization_ids:
                raw = db.engine.raw_connection()
                try:
                    with raw.cursor() as cursor:
                        cursor.execute("SHOW session_replication_role")
                        original_role = cursor.fetchone()[0]
                        cursor.execute("SET session_replication_role = replica")
                        try:
                            for table in _CLEANUP_TABLES:
                                cursor.execute(
                                    f'DELETE FROM "{table}" '
                                    "WHERE organization_id = ANY(%s)",
                                    (list(organization_ids),),
                                )
                            cursor.execute(
                                "DELETE FROM organizations WHERE id = ANY(%s)",
                                (list(organization_ids),),
                            )
                        finally:
                            cursor.execute(
                                f"SET session_replication_role = {original_role}"
                            )
                            raw.commit()
                finally:
                    raw.close()


def _make_org(session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:10]
    org = Organization(name=f"Test {label} {suffix}", slug=f"test-{label}-{suffix}")
    session.add(org)
    session.flush()
    session.info.setdefault("cleanup_org_ids", set()).add(org.id)
    return org


def _make_user(session, org, *, role="enterprise_architect"):
    from app.models.user import User

    user = User(
        organization_id=org.id,
        email=f"typed-route-{uuid.uuid4().hex[:10]}@example.test",
        first_name="Typed",
        last_name="Architect",
        enterprise_role=role,
        confirmed=True,
    )
    session.add(user)
    session.flush()
    return user


def _make_adr(session, org, author):
    from app.models.architecture_decision import ArchitectureDecision

    suffix = uuid.uuid4().hex[:7]
    adr = ArchitectureDecision(
        organization_id=org.id,
        decision_id=f"AD-{suffix}",
        title=f"Typed route ADR {suffix}",
        status="proposed",
        context="A governed choice needs a record.",
        decision="Adopt the governed option.",
        rationale="It is testable.",
        consequences="The ARB reviews it.",
        created_by_id=author.id,
    )
    session.add(adr)
    session.flush()
    return adr


def _ensure_guards(session):
    from app.models.arb_condition_event import ensure_arb_condition_event_guards
    from app.models.arb_condition_evidence import ensure_arb_condition_evidence_guards
    from app.models.arb_decision_event import ensure_arb_decision_guards
    from app.models.architecture_review_board import ensure_arb_cycle_constraints
    from app.models.transformation_db_guards import ensure_transformation_db_guards

    connection = session.connection()
    ensure_transformation_db_guards(connection, capability_secrets=("74" * 32,))
    ensure_arb_cycle_constraints(connection)
    ensure_arb_decision_guards(connection)
    ensure_arb_condition_evidence_guards(connection)
    ensure_arb_condition_event_guards(connection)


def _call(app, org, user, view, *args, json=None, headers=None):
    """Invoke a view function inside an authenticated tenant request context."""
    with app.test_request_context(
        "/", method="POST", json=json or {}, headers=headers or {}
    ):
        g.current_org_id = getattr(org, "id", None) or org.organization_id
        login_user(user)
        response = inspect.unwrap(view)(*args)
    body, status = (
        response if isinstance(response, tuple) else (response, response.status_code)
    )
    return status, body.get_json()


# ────────────────────────── committed integration ───────────────────────── #



def test_adr_route_creates_exactly_one_canonical_cycle_and_replays(
    app, committed_session, monkeypatch
):
    """ADR submission goes through the typed command, once per user action."""
    from app import db
    from app.models.architecture_review_board import ARBReviewCycle, ARBReviewItem
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    monkeypatch.setenv("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "74" * 32)
    _ensure_guards(committed_session)
    org = _make_org(committed_session, "typed-adr")
    author = _make_user(committed_session, org)
    adr = _make_adr(committed_session, org, author)
    committed_session.commit()

    view = governance_api_routes.submit_typed_subject_for_arb
    key = f"typed-adr-{uuid.uuid4().hex}"
    status, body = _call(
        app,
        org,
        author,
        view,
        "adr",
        adr.id,
        json={"human_reviewed": True},
        headers={"Idempotency-Key": key},
    )
    assert status == 201, body
    assert body["success"] is True
    assert body["idempotent"] is False
    # Legacy aliases the ADR ingress must keep.
    assert body["review_id"] == body["review_item_id"]
    assert body["review_number"]
    # Canonical typed identifiers added by the convergence.
    assert isinstance(body["review_cycle_id"], int)
    assert isinstance(body["evidence_id"], int)
    assert body["snapshot_id"] == body["evidence_id"]
    assert body["canonical_url"] == f"/architecture/decisions/{adr.id}"
    assert body["redirect_url"] == f"/arb/reviews/{body['review_item_id']}"
    assert body["subject_type"] == "adr"
    assert body["subject_id"] == adr.id

    def _cycle_count():
        return db.session.execute(
            db.select(db.func.count(ARBReviewCycle.id)).where(
                ARBReviewCycle.organization_id == org.id,
                ARBReviewCycle.subject_type == "adr",
                ARBReviewCycle.subject_id == adr.id,
            )
        ).scalar_one()

    def _item_count():
        return db.session.execute(
            db.select(db.func.count(ARBReviewItem.id)).where(
                ARBReviewItem.organization_id == org.id,
                ARBReviewItem.adr_id == adr.id,
            )
        ).scalar_one()

    db.session.remove()
    assert _cycle_count() == 1
    assert _item_count() == 1

    # Replay with the same command key: idempotent 200, no second cycle.
    replay_status, replay_body = _call(
        app,
        org,
        author,
        view,
        "adr",
        adr.id,
        json={"human_reviewed": True},
        headers={"Idempotency-Key": key},
    )
    assert replay_status == 200, replay_body
    assert replay_body["idempotent"] is True
    assert replay_body["review_cycle_id"] == body["review_cycle_id"]
    assert replay_body["review_item_id"] == body["review_item_id"]
    db.session.remove()
    assert _cycle_count() == 1
    assert _item_count() == 1



def test_adr_route_returns_typed_blockers_not_a_review(
    app, committed_session, monkeypatch
):
    """Without the human-review assertion the typed gate blocks with 422."""
    from app import db
    from app.models.architecture_review_board import ARBReviewCycle
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    monkeypatch.setenv("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "74" * 32)
    _ensure_guards(committed_session)
    org = _make_org(committed_session, "typed-adr-blocked")
    author = _make_user(committed_session, org)
    adr = _make_adr(committed_session, org, author)
    committed_session.commit()

    status, body = _call(
        app,
        org,
        author,
        governance_api_routes.submit_typed_subject_for_arb,
        "adr",
        adr.id,
        json={"human_reviewed": False, "readiness": True, "status": "approved"},
    )
    assert status == 422, body
    assert body["success"] is False
    assert "human_review_required" in body["reason_codes"]
    assert body["request_id"]
    db.session.remove()
    assert db.session.execute(
        db.select(db.func.count(ARBReviewCycle.id)).where(
            ARBReviewCycle.organization_id == org.id,
            ARBReviewCycle.subject_id == adr.id,
            ARBReviewCycle.subject_type == "adr",
        )
    ).scalar_one() == 0



def test_cross_tenant_adr_id_is_not_found_and_leaks_nothing(
    app, committed_session, monkeypatch
):
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    monkeypatch.setenv("TRANSFORMATION_COMMAND_CAPABILITY_SECRET", "74" * 32)
    _ensure_guards(committed_session)
    owner_org = _make_org(committed_session, "typed-owner")
    other_org = _make_org(committed_session, "typed-other")
    owner = _make_user(committed_session, owner_org)
    intruder = _make_user(committed_session, other_org)
    adr = _make_adr(committed_session, owner_org, owner)
    committed_session.commit()

    status, body = _call(
        app,
        other_org,
        intruder,
        governance_api_routes.submit_typed_subject_for_arb,
        "adr",
        adr.id,
        json={"human_reviewed": True, "organization_id": owner_org.id},
    )
    assert status == 404, body
    assert body["reason_codes"] == ["arb_subject_not_found"]
    serialised = repr(body)
    assert adr.title not in serialised
    assert owner.email not in serialised


# ──────────────────────────── contract tests ─────────────────────────────── #


def _stub_result(monkeypatch, **overrides):
    """Replace the typed command with a recorded stub."""
    from app.modules.transformation_room import arb_typed_subject_ingress as ingress

    calls = []

    class _Result:
        def __init__(self):
            self.idempotent = overrides.get("idempotent", False)
            self.object_ids = {}
            self.response = {
                "review_cycle_id": 91,
                "review_item_id": 92,
                "evidence_id": 93,
                "review_number": "REV-2026-TYPED",
                "cycle_number": 1,
                "subject_type": overrides.get("subject_type", "adr"),
                "subject_id": overrides.get("subject_id", 7),
                "status": "submitted",
                "canonical_url": overrides.get("canonical_url", "/architecture/models"),
            }

    def _submit(**kwargs):
        calls.append(kwargs)
        return _Result()

    monkeypatch.setattr(
        ingress.TypedARBSubmissionService, "submit", staticmethod(_submit)
    )
    return calls


def test_ingress_ignores_forged_actor_and_tenant_fields(
    app, db_session, make_org, monkeypatch
):
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    org = make_org("typed-forged")
    user = _make_user(db_session, org)
    other_org = make_org("typed-forged-other")
    calls = _stub_result(monkeypatch, subject_id=7)

    status, body = _call(
        app,
        org,
        user,
        governance_api_routes.submit_typed_subject_for_arb,
        "adr",
        7,
        json={
            "human_reviewed": True,
            "actor_id": user.id + 500,
            "decided_by_id": user.id + 500,
            "organization_id": other_org.id,
            "roles": ["platform_admin"],
            "readiness": True,
            "status": "approved",
        },
    )
    assert status == 201, body
    assert len(calls) == 1
    call = calls[0]
    actor = call["actor"]
    assert actor.user_id == user.id
    assert actor.organization_id == org.id
    # Only the human-review assertion crosses the boundary.
    assert call["assertions"] == {"human_reviewed": True}
    assert call["subject_type"] == "adr"
    assert call["subject_id"] == 7


def test_unsupported_subject_type_is_rejected_before_any_command(
    app, db_session, make_org, monkeypatch
):
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    org = make_org("typed-unsupported")
    user = _make_user(db_session, org)
    calls = _stub_result(monkeypatch)

    status, body = _call(
        app,
        org,
        user,
        governance_api_routes.submit_typed_subject_for_arb,
        "capability",
        3,
        json={"human_reviewed": True},
    )
    assert status == 400
    assert body["reason_codes"] == ["unsupported_subject_type"]
    assert calls == []


def test_malformed_idempotency_key_is_rejected(app, db_session, make_org, monkeypatch):
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    org = make_org("typed-badkey")
    user = _make_user(db_session, org)
    calls = _stub_result(monkeypatch)

    status, body = _call(
        app,
        org,
        user,
        governance_api_routes.submit_typed_subject_for_arb,
        "adr",
        4,
        json={"human_reviewed": True},
        headers={"Idempotency-Key": "short"},
    )
    assert status == 400
    assert body["reason_codes"] == ["invalid_idempotency_key"]
    assert calls == []


@pytest.mark.parametrize(
    ("error_factory", "expected_status", "expected_reason"),
    [
        ("NotFound", 404, "arb_subject_not_found"),
        ("NotAuthorised", 403, "actor_not_authorized"),
        ("CommandConflict", 409, "arb_readiness_stale"),
        ("KnownPreCommitTransient", 503, "submission_failed"),
    ],
)
def test_documented_status_mapping(
    app,
    db_session,
    make_org,
    monkeypatch,
    error_factory,
    expected_status,
    expected_reason,
):
    from app.modules.solutions_strategic.v2.routes import governance_api_routes
    from app.modules.transformation_room import arb_typed_subject_ingress as ingress
    from app.modules.transformation_room import domain

    org = make_org(f"typed-status-{expected_status}")
    user = _make_user(db_session, org)
    error_type = getattr(domain, error_factory)
    error = error_type("arb_readiness_stale")

    def _raise(**_kwargs):
        raise error

    monkeypatch.setattr(
        ingress.TypedARBSubmissionService, "submit", staticmethod(_raise)
    )
    status, body = _call(
        app,
        org,
        user,
        governance_api_routes.submit_typed_subject_for_arb,
        "adr",
        5,
        json={"human_reviewed": True},
    )
    assert status == expected_status
    assert body["reason_codes"] == [expected_reason]
    assert body["success"] is False
    assert body["request_id"]
    assert "Traceback" not in repr(body)


def test_composer_rejects_canvas_only_submission(app, db_session, make_org, monkeypatch):
    """A canvas is not a governed subject: no raw review item may be created."""
    from app.models.architecture_review_board import ARBReviewItem
    from app.modules.solutions_strategic.v2.routes import solution_composer_routes

    org = make_org("typed-canvas")
    user = _make_user(db_session, org)
    calls = _stub_result(monkeypatch)
    def _org_items():
        from app import db

        return db_session.execute(
            db.select(db.func.count(ARBReviewItem.id)).where(
                ARBReviewItem.organization_id == org.id
            )
        ).scalar_one()

    before = _org_items()

    status, body = _call(
        app,
        org,
        user,
        solution_composer_routes.submit_to_arb,
        json={
            "title": "Canvas design",
            "nodes": [{"id": "n1", "layer": "application"}],
            "human_reviewed": True,
        },
    )
    assert status == 422
    assert body["success"] is False
    assert body["reason_codes"] == ["architecture_model_required"]
    assert calls == []
    assert _org_items() == before


def test_composer_delegates_a_persisted_model_to_the_typed_command(
    app, db_session, make_org, monkeypatch
):
    from app.modules.solutions_strategic.v2.routes import solution_composer_routes

    org = make_org("typed-canvas-model")
    user = _make_user(db_session, org)
    calls = _stub_result(
        monkeypatch, subject_type="architecture_model", subject_id=31
    )

    status, body = _call(
        app,
        org,
        user,
        solution_composer_routes.submit_to_arb,
        json={"architecture_model_id": 31, "human_reviewed": True},
    )
    # Composer preserves its established success contract while delegating the
    # write to the canonical typed command.
    assert status == 200, body
    assert body["success"] is True
    assert calls[0]["subject_type"] == "architecture_model"
    assert calls[0]["subject_id"] == 31
    assert calls[0]["assertions"] == {"human_reviewed": True}
    data = body["data"]
    # Composer's legacy envelope is preserved.
    assert data["review_number"] == "REV-2026-TYPED"
    assert data["review_item_id"] == 92
    assert data["status"] == "submitted"
    assert data["arb_dashboard_url"] == "/arb/reviews"
    # ...and the canonical typed identifiers are added.
    assert data["review_cycle_id"] == 91
    assert data["snapshot_id"] == 93
    assert data["canonical_url"] == "/architecture/models"


def test_record_arb_decision_ignores_client_decided_by_id(
    app, db_session, make_org
):
    from app.models.solution_governance import SolutionARBReview
    from app.models.solution_models import Solution
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    org = make_org("typed-decision")
    actor = _make_user(db_session, org)
    impostor_target = _make_user(db_session, org)
    solution = Solution(
        name=f"Decision solution {uuid.uuid4().hex[:8]}",
        organization_id=org.id,
        created_by_id=actor.id,
        governance_status="arb_review",
    )
    db_session.add(solution)
    db_session.flush()
    review = SolutionARBReview(
        organization_id=org.id,
        solution_id=solution.id,
        submitted_by_id=actor.id,
        arb_decision="pending",
    )
    db_session.add(review)
    db_session.flush()

    status, _body = _call(
        app,
        org,
        actor,
        governance_api_routes.record_arb_decision,
        solution.id,
        review.id,
        json={
            "decision": "approved",
            "decision_reason": "Meets the standard.",
            "decided_by_id": impostor_target.id,
        },
    )
    assert status == 200
    db_session.expire(review)
    assert review.decided_by_id == actor.id
    assert review.decided_by_id != impostor_target.id


def test_record_arb_decision_rejects_a_foreign_review(app, db_session, make_org):
    from app.models.solution_governance import SolutionARBReview
    from app.models.solution_models import Solution
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    owner_org = make_org("typed-decision-owner")
    other_org = make_org("typed-decision-other")
    owner = _make_user(db_session, owner_org)
    intruder = _make_user(db_session, other_org)
    solution = Solution(
        name=f"Foreign solution {uuid.uuid4().hex[:8]}",
        organization_id=owner_org.id,
        created_by_id=owner.id,
    )
    db_session.add(solution)
    db_session.flush()
    review = SolutionARBReview(
        organization_id=owner_org.id,
        solution_id=solution.id,
        submitted_by_id=owner.id,
        arb_decision="pending",
    )
    db_session.add(review)
    db_session.flush()

    status, body = _call(
        app,
        other_org,
        intruder,
        governance_api_routes.record_arb_decision,
        solution.id,
        review.id,
        json={"decision": "approved", "decision_reason": "Not mine."},
    )
    # The solution guard fires first; either way nothing foreign is disclosed.
    assert status == 404
    assert solution.name not in repr(body)
    db_session.expire(review)
    assert review.arb_decision == "pending"


def test_escalation_service_will_not_type_a_solution_finding(app, db_session, make_org):
    from app.modules.solutions_strategic.v2.services.arb_escalation_service import (
        ARBEscalationService,
    )

    org = make_org("typed-escalate")
    user = _make_user(db_session, org)
    with app.test_request_context("/", method="POST"):
        g.current_org_id = getattr(org, "id", None) or org.organization_id
        login_user(user)
        result = ARBEscalationService.escalate(
            title="Drift detected",
            detail="Estate drift",
            category="drift",
            severity="high",
            user_id=user.id + 999,
            solution_id=1234,
        )
    assert result["success"] is False
    assert "canonical evidence-gated submission endpoint" in result["error"]


# ----------------------- decision_brief ingress -------------------------- #
#
# These reuse the committed `decision_scope` chain (programme -> workstream ->
# candidate -> options -> evidence -> brief), because a Decision Brief can only
# be submitted once a real frozen version exists; there is nothing to stub.

from tests.test_decision_brief_service import (  # noqa: E402
    _assertions as _brief_assertions,
    _freeze_brief,
    _freeze_options,
)
from tests.test_transformation_evidence_service import (  # noqa: E402,F401
    _record_named_source,
    evidence_scope,
)
from tests.test_transformation_option_service import decision_scope  # noqa: E402,F401


def _scope_user(scope):
    from app import db
    from app.models.user import User

    return db.session.execute(
        db.select(User).where(
            User.id == scope.actor_id,
            User.organization_id == scope.organization_id,
        )
    ).scalar_one()


def _brief_cycle_count(scope):
    from app import db
    from app.models.architecture_review_board import ARBReviewCycle

    db.session.remove()
    return db.session.execute(
        db.select(db.func.count(ARBReviewCycle.id)).where(
            ARBReviewCycle.organization_id == scope.organization_id,
            ARBReviewCycle.subject_type == "decision_brief",
            ARBReviewCycle.subject_id == scope.brief_id,
        )
    ).scalar_one()


def test_decision_brief_route_creates_one_cycle_and_replays(app, decision_scope):
    from app import db
    from app.models.transformation_programme import ProgrammeWorkstream
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    option_version_ids = _freeze_options(decision_scope)
    _freeze_brief(
        decision_scope,
        option_version_ids,
        assertions=_brief_assertions(decision_scope),
        key=f"route-freeze-{uuid.uuid4().hex}",
    )
    user = _scope_user(decision_scope)
    workstream = db.session.get(ProgrammeWorkstream, decision_scope.workstream_id)
    expected_url = (
        f"/solutions/programmes/{workstream.programme_id}/workstreams/"
        f"{workstream.id}/decision"
    )

    view = governance_api_routes.submit_typed_subject_for_arb
    key = f"brief-submit-{uuid.uuid4().hex}"
    status, body = _call(
        app, decision_scope, user, view,
        "decision_brief", decision_scope.brief_id,
        json={"human_reviewed": True},
        headers={"Idempotency-Key": key},
    )
    assert status == 201, body
    assert body["success"] is True
    assert body["subject_type"] == "decision_brief"
    assert body["subject_id"] == decision_scope.brief_id
    assert body["canonical_url"] == expected_url
    # Same envelope and aliases as the other typed subjects.
    assert body["review_id"] == body["review_item_id"]
    assert body["snapshot_id"] == body["evidence_id"]
    assert body["redirect_url"] == f"/arb/reviews/{body['review_item_id']}"
    assert body["review_number"]
    assert _brief_cycle_count(decision_scope) == 1

    replay_status, replay_body = _call(
        app, decision_scope, user, view,
        "decision_brief", decision_scope.brief_id,
        json={"human_reviewed": True},
        headers={"Idempotency-Key": key},
    )
    assert replay_status == 200, replay_body
    assert replay_body["idempotent"] is True
    assert replay_body["review_cycle_id"] == body["review_cycle_id"]
    assert _brief_cycle_count(decision_scope) == 1


def test_decision_brief_without_a_frozen_version_is_a_422_blocker(app, decision_scope):
    """An unfrozen brief is a readiness blocker, not a missing record."""
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    user = _scope_user(decision_scope)
    status, body = _call(
        app, decision_scope, user,
        governance_api_routes.submit_typed_subject_for_arb,
        "decision_brief", decision_scope.brief_id,
        json={"human_reviewed": True},
    )
    assert status == 422, body
    assert body["reason_codes"] == ["decision_brief_version_not_frozen"]
    assert body["missing_evidence"][0]["resource_type"] == "decision_brief"
    assert body["request_id"]
    assert _brief_cycle_count(decision_scope) == 0


def test_cross_tenant_decision_brief_id_is_not_found_and_leaks_nothing(
    app, decision_scope
):
    from app import db
    from app.models.organization import Organization
    from app.models.transformation_decision import DecisionBrief
    from app.models.user import User
    from app.modules.solutions_strategic.v2.routes import governance_api_routes

    option_version_ids = _freeze_options(decision_scope)
    _freeze_brief(
        decision_scope,
        option_version_ids,
        assertions=_brief_assertions(decision_scope),
        key=f"foreign-freeze-{uuid.uuid4().hex}",
    )
    suffix = uuid.uuid4().hex[:10]
    other_org = Organization(name=f"Test brief-other {suffix}", slug=f"brief-{suffix}")
    db.session.add(other_org)
    db.session.flush()
    intruder = User(
        organization_id=other_org.id,
        email=f"brief-intruder-{suffix}@example.test",
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    db.session.add(intruder)
    db.session.commit()
    other_org_id = other_org.id
    brief_title = db.session.execute(
        db.select(DecisionBrief.title).where(
            DecisionBrief.id == decision_scope.brief_id,
            DecisionBrief.organization_id == decision_scope.organization_id,
        )
    ).scalar_one()

    try:
        with app.test_request_context(
            "/",
            method="POST",
            json={
                "human_reviewed": True,
                "organization_id": decision_scope.organization_id,
            },
        ):
            g.current_org_id = other_org_id
            login_user(intruder)
            response = inspect.unwrap(
                governance_api_routes.submit_typed_subject_for_arb
            )("decision_brief", decision_scope.brief_id)
        body, status = (
            response
            if isinstance(response, tuple)
            else (response, response.status_code)
        )
        payload = body.get_json()
        assert status == 404
        assert payload["reason_codes"] == ["arb_subject_not_found"]
        assert brief_title not in repr(payload)
        assert _brief_cycle_count(decision_scope) == 0
    finally:
        db.session.remove()
        with db.engine.begin() as connection:
            connection.execute(
                db.text("DELETE FROM users WHERE organization_id = :org"),
                {"org": other_org_id},
            )
            connection.execute(
                db.text("DELETE FROM organizations WHERE id = :org"),
                {"org": other_org_id},
            )


# ------------------------- governance role sets --------------------------- #


def test_submission_and_evidence_role_sets_are_distinct_and_pinned():
    """Two different authorities, two different names, two pinned memberships.

    These sets were both called ``_SUBMIT_ROLES``, which made two different
    authorities look like one and turned a correct 403 into an apparent bug.
    The membership difference is deliberate governance policy; this test makes
    any change to either set explicit in review rather than silent.
    """
    from app.modules.transformation_room.arb_condition_evidence_service import (
        _EVIDENCE_CAPTURE_ROLES,
    )
    from app.modules.transformation_room.arb_submission_service import (
        _SUBJECT_SUBMIT_ROLES,
    )

    assert _SUBJECT_SUBMIT_ROLES == frozenset(
        {
            "chief_architect",
            "enterprise_architect",
            "solution_architect",
            "application_architect",
            "business_architect",
            "data_architect",
            "technology_architect",
            "security_architect",
            "architect",
        }
    )
    assert _EVIDENCE_CAPTURE_ROLES == frozenset(
        {
            "chief_architect",
            "enterprise_architect",
            "solution_architect",
            "architect",
            "arb_member",
        }
    )
    # The differences are the point: neither set may silently absorb the other.
    assert "arb_member" in _EVIDENCE_CAPTURE_ROLES
    assert "arb_member" not in _SUBJECT_SUBMIT_ROLES
    # platform_admin authority is the is_platform_admin predicate, never the
    # self-assignable role value (tests/test_role_self_assignment.py).
    assert "platform_admin" not in _SUBJECT_SUBMIT_ROLES
    assert "platform_admin" not in _EVIDENCE_CAPTURE_ROLES
    assert _SUBJECT_SUBMIT_ROLES != _EVIDENCE_CAPTURE_ROLES
