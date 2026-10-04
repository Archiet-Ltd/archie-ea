"""ARB review consolidation acceptance criteria: two-organisation isolation and template consolidation.

Proves:
1. Organisation B cannot see or act on organisation A's submission, decision,
   condition or waiver.
2. The legacy partial templates (_legacy_dashboard.html, _legacy_review_detail.html)
   are no longer rendered — the dashboard reads one path, not two.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from flask import render_template, url_for
from sqlalchemy import select, text

from app import db
from app.models.architecture_decision import ArchitectureDecision
from app.models.architecture_review_board import ARBReviewCycle, ARBReviewItem
from app.models.arb_decision_event import ARBCondition, ARBDecisionEvent
from app.models.arb_submission_event import ARBSubmissionEvent
from app.models.organization import Organization
from app.models.user import User
from app.modules.transformation_room.arb_submission_service import (
    TypedARBSubmissionService,
)
from app.modules.transformation_room.arb_decision_service import (
    TypedARBDecisionService,
)
from app.modules.transformation_room.arb_condition_lifecycle_service import (
    TypedARBConditionLifecycleService,
)
from app.modules.transformation_room.domain import (
    ActorContext,
    NotFound,
    NotAuthorised,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_CLEANUP_TABLES = (
    "arb_canonical_conditions",
    "arb_condition_events",
    "arb_condition_evidence_records",
    "arb_decision_events",
    "arb_submission_events",
    "transformation_outbox_events",
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
    with app.app_context():
        db.session.remove()
        cleanup_org_ids = set()
        db.session.info["r1b31_cleanup_org_ids"] = cleanup_org_ids
        try:
            yield db.session
        finally:
            organization_ids = tuple(cleanup_org_ids)
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
                                f'DELETE FROM "{table}" '
                                "WHERE organization_id = ANY(%s)",
                                (list(organization_ids),),
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
            finally:
                raw.close()


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


def _make_two_orgs(db_session, make_org):
    """Create two organisations, each with a user and an ADR."""
    _install_guards(db_session)

    org_a = make_org("r1b31-a")
    org_b = make_org("r1b31-b")
    db_session.info.setdefault("r1b31_cleanup_org_ids", set()).add(org_a.id)
    db_session.info.setdefault("r1b31_cleanup_org_ids", set()).add(org_b.id)

    from app.services.billing_plans import set_contract_plan
    set_contract_plan(org_a, "enterprise", None)
    set_contract_plan(org_b, "enterprise", None)

    suffix = uuid.uuid4().hex[:10]

    user_a = User(
        organization_id=org_a.id,
        email=f"r1b31-a-{suffix}@example.test",
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    # Separate decider for org A (separation of duties)
    decider_a = User(
        organization_id=org_a.id,
        email=f"r1b31-decider-a-{suffix}@example.test",
        enterprise_role="chief_architect",
        confirmed=True,
    )
    user_b = User(
        organization_id=org_b.id,
        email=f"r1b31-b-{suffix}@example.test",
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    db_session.add_all((user_a, decider_a, user_b))
    db_session.flush()

    adr_a = ArchitectureDecision(
        organization_id=org_a.id,
        decision_id=f"AD-A-{suffix}",
        title=f"Org A decision {suffix}",
        status="proposed",
        context="A governed choice for org A.",
        decision="Adopt the governed option for A.",
        rationale="It is testable.",
        consequences="A decision event must exist.",
        created_by_id=user_a.id,
    )
    adr_b = ArchitectureDecision(
        organization_id=org_b.id,
        decision_id=f"AD-B-{suffix}",
        title=f"Org B decision {suffix}",
        status="proposed",
        context="A governed choice for org B.",
        decision="Adopt the governed option for B.",
        rationale="It is testable.",
        consequences="A decision event must exist.",
        created_by_id=user_b.id,
    )
    db_session.add_all((adr_a, adr_b))
    db_session.commit()

    actor_a = ActorContext(
        user_a.id, org_a.id, frozenset({"enterprise_architect"}), f"r1b31-a-{suffix}"
    )
    decider_actor_a = ActorContext(
        decider_a.id, org_a.id, frozenset({"chief_architect"}), f"r1b31-decider-a-{suffix}"
    )
    actor_b = ActorContext(
        user_b.id, org_b.id, frozenset({"enterprise_architect"}), f"r1b31-b-{suffix}"
    )

    return {
        "org_a_id": org_a.id,
        "org_b_id": org_b.id,
        "user_a_id": user_a.id,
        "decider_a_id": decider_a.id,
        "user_b_id": user_b.id,
        "adr_a_id": adr_a.id,
        "adr_b_id": adr_b.id,
        "actor_a": actor_a,
        "decider_actor_a": decider_actor_a,
        "actor_b": actor_b,
        "suffix": suffix,
    }


# ---------------------------------------------------------------------------
# 1. Two-organisation isolation: submission
# ---------------------------------------------------------------------------


def test_org_b_cannot_submit_against_org_a_subject(db_session, make_org):
    """Organisation B's actor cannot submit org A's ADR — NotFound, no side effects."""
    ctx = _make_two_orgs(db_session, make_org)

    with pytest.raises(NotFound, match="arb_subject_not_found"):
        TypedARBSubmissionService.submit(
            actor=ctx["actor_b"],
            command_key=f"cross-org-submit-{ctx['suffix']}",
            subject_type="adr",
            subject_id=ctx["adr_a_id"],
            assertions={"human_reviewed": True},
        )

    # Prove no side effects leaked into org A's data
    with db.session.no_autoflush:
        cycles_a = (
            db_session.query(ARBReviewCycle)
            .filter(ARBReviewCycle.organization_id == ctx["org_a_id"])
            .count()
        )
        items_a = (
            db_session.query(ARBReviewItem)
            .filter(ARBReviewItem.organization_id == ctx["org_a_id"])
            .count()
        )
    assert cycles_a == 0, "org B's failed submission must not create cycles in org A"
    assert items_a == 0, "org B's failed submission must not create items in org A"


def test_org_b_cannot_see_org_a_submission_via_query(db_session, make_org, tenant_ctx):
    """Organisation B's tenant context cannot see org A's review cycles or items."""
    ctx = _make_two_orgs(db_session, make_org)

    # Submit as org A
    submission = TypedARBSubmissionService.submit(
        actor=ctx["actor_a"],
        command_key=f"submit-a-{ctx['suffix']}",
        subject_type="adr",
        subject_id=ctx["adr_a_id"],
        assertions={"human_reviewed": True},
    )
    cycle_id = submission.object_ids["review_cycle_id"]
    item_id = submission.object_ids["review_item_id"]

    db_session.expunge_all()

    # Org B's tenant context must not see org A's cycle or item
    with tenant_ctx(ctx["org_b_id"]):
        cycle_b = ARBReviewCycle.query.filter_by(id=cycle_id).first()
        item_b = ARBReviewItem.query.filter_by(id=item_id).first()

    assert cycle_b is None, (
        f"TENANT LEAK: org B can see org A's review cycle (id={cycle_id})"
    )
    assert item_b is None, (
        f"TENANT LEAK: org B can see org A's review item (id={item_id})"
    )

    # Org A's tenant context must still see its own data
    with tenant_ctx(ctx["org_a_id"]):
        cycle_a = ARBReviewCycle.query.filter_by(id=cycle_id).first()
        item_a = ARBReviewItem.query.filter_by(id=item_id).first()

    assert cycle_a is not None, "org A must see its own review cycle"
    assert item_a is not None, "org A must see its own review item"


# ---------------------------------------------------------------------------
# 2. Two-organisation isolation: decision
# ---------------------------------------------------------------------------


def test_org_b_cannot_decide_org_a_cycle(db_session, make_org):
    """Organisation B's actor cannot record a decision on org A's cycle."""
    ctx = _make_two_orgs(db_session, make_org)

    # Submit as org A
    submission = TypedARBSubmissionService.submit(
        actor=ctx["actor_a"],
        command_key=f"submit-for-decision-{ctx['suffix']}",
        subject_type="adr",
        subject_id=ctx["adr_a_id"],
        assertions={"human_reviewed": True},
    )
    cycle_id = submission.object_ids["review_cycle_id"]

    # Org B tries to decide org A's cycle
    with pytest.raises((NotFound, NotAuthorised)):
        TypedARBDecisionService.decide(
            actor=ctx["actor_b"],
            command_key=f"cross-org-decide-{ctx['suffix']}",
            cycle_id=cycle_id,
            outcome="approved",
            rationale="Cross-org decision attempt",
            conditions=[],
        )

    # Prove no decision events were created in org A
    with db.session.no_autoflush:
        events_a = (
            db_session.query(ARBDecisionEvent)
            .filter(ARBDecisionEvent.organization_id == ctx["org_a_id"])
            .count()
        )
    assert events_a == 0, (
        "org B's failed decision must not create decision events in org A"
    )


def test_org_b_cannot_see_org_a_decision_event(db_session, make_org, tenant_ctx):
    """Organisation B's tenant context cannot see org A's decision events."""
    ctx = _make_two_orgs(db_session, make_org)

    # Submit and decide as org A (using separate decider for separation of duties)
    submission = TypedARBSubmissionService.submit(
        actor=ctx["actor_a"],
        command_key=f"submit-decide-a-{ctx['suffix']}",
        subject_type="adr",
        subject_id=ctx["adr_a_id"],
        assertions={"human_reviewed": True},
    )
    cycle_id = submission.object_ids["review_cycle_id"]

    decision = TypedARBDecisionService.decide(
        actor=ctx["decider_actor_a"],
        command_key=f"decide-a-{ctx['suffix']}",
        cycle_id=cycle_id,
        outcome="approved",
        rationale="Board approved the submission",
        conditions=[],
    )
    event_id = decision.object_ids.get("decision_event_id")

    db_session.expunge_all()

    # Org B must not see org A's decision event
    with tenant_ctx(ctx["org_b_id"]):
        if event_id:
            event_b = ARBDecisionEvent.query.filter_by(id=event_id).first()
            assert event_b is None, (
                f"TENANT LEAK: org B can see org A's decision event (id={event_id})"
            )

    # Org A must still see its own decision event
    with tenant_ctx(ctx["org_a_id"]):
        if event_id:
            event_a = ARBDecisionEvent.query.filter_by(id=event_id).first()
            assert event_a is not None, "org A must see its own decision event"


# ---------------------------------------------------------------------------
# 3. Two-organisation isolation: condition
# ---------------------------------------------------------------------------


def test_org_b_cannot_see_org_a_condition(db_session, make_org, tenant_ctx):
    """Organisation B's tenant context cannot see org A's conditions."""
    ctx = _make_two_orgs(db_session, make_org)

    # Submit and decide with conditions as org A
    submission = TypedARBSubmissionService.submit(
        actor=ctx["actor_a"],
        command_key=f"submit-cond-a-{ctx['suffix']}",
        subject_type="adr",
        subject_id=ctx["adr_a_id"],
        assertions={"human_reviewed": True},
    )
    cycle_id = submission.object_ids["review_cycle_id"]

    TypedARBDecisionService.decide(
        actor=ctx["decider_actor_a"],
        command_key=f"decide-cond-a-{ctx['suffix']}",
        cycle_id=cycle_id,
        outcome="approved_with_conditions",
        rationale="Board approved with conditions",
        conditions=[{"code": "SEC-1", "text": "Complete the threat model"}],
    )

    db_session.expunge_all()

    # Org B must not see org A's conditions
    with tenant_ctx(ctx["org_b_id"]):
        conditions_b = (
            ARBCondition.query
            .filter(ARBCondition.organization_id == ctx["org_a_id"])
            .count()
        )
    assert conditions_b == 0, (
        f"TENANT LEAK: org B can see org A's conditions (count={conditions_b})"
    )

    # Org A must see its own conditions
    with tenant_ctx(ctx["org_a_id"]):
        conditions_a = (
            ARBCondition.query
            .filter(ARBCondition.organization_id == ctx["org_a_id"])
            .count()
        )
    assert conditions_a > 0, "org A must see its own conditions"


# ---------------------------------------------------------------------------
# 4. Two-organisation isolation: waiver
# ---------------------------------------------------------------------------


def test_org_b_cannot_waive_org_a_condition(db_session, make_org):
    """Organisation B's actor cannot record a waiver on org A's condition."""
    ctx = _make_two_orgs(db_session, make_org)

    # Submit and decide with conditions as org A
    submission = TypedARBSubmissionService.submit(
        actor=ctx["actor_a"],
        command_key=f"submit-waive-a-{ctx['suffix']}",
        subject_type="adr",
        subject_id=ctx["adr_a_id"],
        assertions={"human_reviewed": True},
    )
    cycle_id = submission.object_ids["review_cycle_id"]

    TypedARBDecisionService.decide(
        actor=ctx["decider_actor_a"],
        command_key=f"decide-waive-a-{ctx['suffix']}",
        cycle_id=cycle_id,
        outcome="approved_with_conditions",
        rationale="Board approved with conditions for waiver test",
        conditions=[{"code": "SEC-1", "text": "Complete the threat model"}],
    )

    # Find the condition id
    db_session.expunge_all()
    condition = (
        db_session.query(ARBCondition)
        .filter(
            ARBCondition.organization_id == ctx["org_a_id"],
            ARBCondition.review_cycle_id == cycle_id,
        )
        .first()
    )
    assert condition is not None, "org A's condition must exist"

    # Org B tries to waive org A's condition
    from datetime import timedelta
    waiver_expires = datetime.now(timezone.utc) + timedelta(days=30)
    with pytest.raises((NotFound, NotAuthorised)):
        TypedARBConditionLifecycleService.waive(
            actor=ctx["actor_b"],
            command_key=f"cross-org-waive-{ctx['suffix']}",
            condition_id=condition.id,
            reason="Cross-org waiver attempt",
            expires_at=waiver_expires,
            scope={"evidence": "none"},
            compensating_control="None",
        )

    # Prove the condition was not waived
    db_session.expunge_all()
    condition_after = db_session.get(ARBCondition, condition.id)
    assert condition_after is not None
    assert condition_after.status != "waived", (
        "org B's failed waiver must not waive org A's condition"
    )
    assert condition_after.waived_at is None, (
        "org B's failed waiver must not set waived_at on org A's condition"
    )


# ---------------------------------------------------------------------------
# 5. Legacy partial templates are no longer rendered
# ---------------------------------------------------------------------------


def test_legacy_dashboard_partial_is_not_rendered(app):
    """The legacy _legacy_dashboard.html partial must not exist on disk."""
    import os
    template_path = os.path.join(
        app.root_path, "templates", "arb", "partials", "_legacy_dashboard.html"
    )
    assert not os.path.exists(template_path), (
        "Legacy _legacy_dashboard.html partial must be deleted — "
        "the dashboard reads one path (typed queue), not two."
    )


def test_legacy_review_detail_partial_is_not_rendered(app):
    """The legacy _legacy_review_detail.html partial must not exist on disk."""
    import os
    template_path = os.path.join(
        app.root_path, "templates", "arb", "partials", "_legacy_review_detail.html"
    )
    assert not os.path.exists(template_path), (
        "Legacy _legacy_review_detail.html partial must be deleted — "
        "the review detail reads one path (typed workspace), not two."
    )


def test_dashboard_template_has_no_legacy_branch(app):
    """The dashboard template must not contain the legacy dispatcher branch."""
    import os
    template_path = os.path.join(
        app.root_path, "templates", "arb", "dashboard.html"
    )
    content = open(template_path).read()
    assert "_legacy_dashboard.html" not in content, (
        "dashboard.html must not reference the legacy partial"
    )
    # The typed queue is the single code path
    assert "_typed_queue.html" in content, (
        "dashboard.html must include the typed queue partial"
    )


def test_review_detail_template_has_no_legacy_branch(app):
    """The review detail template must not contain the legacy dispatcher branch."""
    import os
    template_path = os.path.join(
        app.root_path, "templates", "arb", "review_detail.html"
    )
    content = open(template_path).read()
    assert "_legacy_review_detail.html" not in content, (
        "review_detail.html must not reference the legacy partial"
    )
    # The typed workspace is the single code path
    assert "_typed_review_" in content, (
        "review_detail.html must include typed workspace partials"
    )


def test_dashboard_route_does_not_pass_generic_reviews(app, client):
    """The dashboard route must not pass generic_reviews to the template context."""
    import inspect
    from app.modules.architecture.routes.arb_routes import dashboard

    source = inspect.getsource(dashboard)
    # The route must not pass generic_reviews as a template variable.
    # Comments mentioning the removal are fine; the actual render_template
    # call must not include generic_reviews=.
    assert "generic_reviews=generic_reviews" not in source, (
        "dashboard() route must not pass generic_reviews to render_template — "
        "the typed queue is the single code path"
    )
    assert "generic_reviews=" not in source, (
        "dashboard() route must not pass generic_reviews to render_template — "
        "the typed queue is the single code path"
    )


def test_review_detail_route_has_no_legacy_branch(app, client):
    """The review_detail route must not have a legacy branch."""
    import inspect
    from app.modules.architecture.routes.arb_routes import review_detail

    source = inspect.getsource(review_detail)
    assert "_legacy_review_detail.html" not in source, (
        "review_detail() route must not reference the legacy partial"
    )