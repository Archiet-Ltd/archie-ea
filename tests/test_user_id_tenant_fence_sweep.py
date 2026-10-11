"""A user id resolved from stored or request-supplied data is named, or e-mailed,
only inside its own organisation.

Sweep of sites left open by an earlier tenant-fence fix:

- the acting-user e-mail lookup in ``adm_audit_service.py`` / ``arb_audit_service.py``
- the workflow notification recipients in ``ea_workflow_engine.py``
- the ARB review-history actor names in ``solution_design_routes.py`` (a sibling-field
  leak of the same shape as a kanban card's owner field: the actor is resolved from an
  already-loaded relationship rather than through an organisation check on the id)

Each fixed site gets one test proving another organisation's user is refused (not
named, not e-mailed) rather than shown.
"""

from __future__ import annotations

import uuid

# app.models.adm_audit_log is not imported anywhere in the app's normal boot
# path (no blueprint or service references it at import time other than
# adm_audit_service.py, which nothing imports eagerly either), so the
# session-scoped ``_schema`` fixture's create_all() never sees ADMAuditLog
# unless something imports it before that fixture runs. A module-level
# import here, resolved at collection time (before any fixture executes),
# is what makes adm_audit_logs exist for this test's assertions.
# ADMPhaseApproval must come with it: ADMAuditLog.approval is a string-named
# relationship("ADMPhaseApproval"), and that model is equally absent from the
# app's normal import graph, so SQLAlchemy's mapper configuration fails to
# resolve the name unless both are registered together.
from app.models.adm_audit_log import ADMAuditAction, ADMAuditLog  # noqa: F401
from app.models.adm_phase_approval import ADMPhaseApproval  # noqa: F401


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Sweep {label} {suffix}", slug=f"sweep-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, first, last):
    from app.models.user import User

    user = User(
        first_name=first, last_name=last, email=f"{first.lower()}-{uuid.uuid4().hex[:6]}@example.test",
        password="test-password-123", confirmed=True, organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _world(db_session):
    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    mine = _user(db_session, org_a, "Mia", "Mine")
    theirs = _user(db_session, org_b, "Vic", "Tim")
    db_session.commit()
    return org_a, org_b, mine, theirs


# -- site 1a: app/services/adm_audit_service.py ---------------------------------------
#
# Both callers of ADMAuditService.log_event (adm_phase_validator.py,
# arb_intake_service.py) are unreachable in production today: each is only ever
# instantiated as an inert module-level singleton with no caller anywhere in the
# app. The acting-user email lookup they share is fixed as defense in depth
# regardless, using the card's own organisation_id, exactly as a live caller would.


def test_adm_audit_log_event_names_only_an_actor_of_the_supplied_organisation(app, db_session):
    from app.models.adm_audit_log import ADMAuditAction, ADMAuditLog
    from app.services.adm_audit_service import adm_audit_service

    org_a, _org_b, mine, theirs = _world(db_session)

    foreign_log = adm_audit_service.log_event(
        action=ADMAuditAction.CARD_CREATED,
        entity_type="card",
        entity_id=1,
        actor_id=theirs.id,
        organization_id=org_a.id,
    )
    assert foreign_log.actor_email is None, (
        f"Expected no actor_email for a user outside organization {org_a.id}, "
        f"got {foreign_log.actor_email!r}"
    )

    own_log = adm_audit_service.log_event(
        action=ADMAuditAction.CARD_CREATED,
        entity_type="card",
        entity_id=2,
        actor_id=mine.id,
        organization_id=org_a.id,
    )
    assert own_log.actor_email == mine.email

    logged = ADMAuditLog.query.filter_by(entity_id=1).first()
    assert logged.actor_email is None


# -- site 1b: app/services/arb_audit_service.py ----------------------------------------
#
# log_action resolves user_id (which, per arb_exception_service.py's callers, can be a
# request-supplied requested_by_id/approved_by_id/etc. with no organisation check of its
# own) only inside the acting request's own organisation.


def test_arb_audit_log_action_names_only_a_user_of_the_acting_organisation(app, db_session, tenant_ctx):
    from app.models.architecture_review_board import ARBAuditLog
    from app.services.arb_audit_service import arb_audit_service

    org_a, _org_b, mine, theirs = _world(db_session)

    with tenant_ctx(org_a.id):
        foreign_log = arb_audit_service.log_action(
            entity_type="exception", entity_id=101, action="status_change", user_id=theirs.id,
        )
        assert foreign_log.user_email is None, (
            f"Expected no user_email for a user outside organization {org_a.id}, "
            f"got {foreign_log.user_email!r}"
        )

        own_log = arb_audit_service.log_action(
            entity_type="exception", entity_id=102, action="status_change", user_id=mine.id,
        )
        assert own_log.user_email == mine.email

    stored = ARBAuditLog.query.filter_by(entity_id=101).first()
    assert stored.user_email is None


def test_arb_exception_request_audit_names_only_a_user_of_the_caller_organisation(app, db_session, tenant_ctx):
    """The real leak, end to end: ARBException.requested_by_id/approved_by_id/etc.
    are taken straight from request JSON with no organisation check
    (app/modules/architecture/routes/arb_routes.py's api_create_exception,
    api_approve_exception pass **data into ARBExceptionService), and every
    audit entry for the exception used to name whichever organisation the id
    belonged to.

    Exercised directly through arb_audit_service.log_exception_request (the
    same call arb_exception_service.py's create_exception_request makes)
    rather than through the service method itself: that method's
    exception_number generation calls a classmethod
    (ARBException.generate_exception_number) that does not exist anywhere in
    this codebase, an unrelated pre-existing defect that fails identically on
    origin/main and is out of this fix's scope.
    """
    from app.models.architecture_review_board import ARBAuditLog, ARBException, ARBGovernanceStandard
    from app.services.arb_audit_service import arb_audit_service

    org_a, _org_b, _mine, theirs = _world(db_session)

    standard = ARBGovernanceStandard(
        code=f"STD-{uuid.uuid4().hex[:8]}", name="Test standard", category="security",
    )
    db_session.add(standard)
    db_session.flush()

    exception = ARBException(
        exception_number=f"EXC-{uuid.uuid4().hex[:8]}",
        standard_id=standard.id,
        exception_type="waiver",
        status="requested",
        requested_by_id=theirs.id,  # unvalidated, exactly as the live route writes it
        organization_id=org_a.id,
    )
    db_session.add(exception)
    db_session.commit()

    with tenant_ctx(org_a.id):
        arb_audit_service.log_exception_request(exception=exception, user_id=theirs.id)

    audit = ARBAuditLog.query.filter_by(
        entity_type="exception", entity_id=exception.id
    ).order_by(ARBAuditLog.id.desc()).first()
    assert audit is not None
    assert audit.user_email is None, (
        f"Expected the exception-request audit entry to name no user for a "
        f"requester outside organization {org_a.id}, got {audit.user_email!r}"
    )


# -- site 2: app/services/ea_workflow_engine.py ----------------------------------------
#
# config['recipients'] is a list of raw user ids carried in the workflow definition's
# stored JSON. A recipient outside the workflow instance's own organisation must get
# no in-app notification and no email.


def test_workflow_notification_never_notifies_a_recipient_outside_the_workflow_organisation(app, db_session):
    from app.models.workflow_models import EAWorkflowDefinition, EAWorkflowInstance, EAWorkflowNotification
    from app.services.ea_workflow_engine import EAWorkflowEngine

    org_a, _org_b, mine, theirs = _world(db_session)

    definition = EAWorkflowDefinition(
        workflow_code=f"WF-{uuid.uuid4().hex[:8]}",
        workflow_name="Test workflow",
        workflow_category="test",
        steps=[],
        organization_id=org_a.id,
    )
    db_session.add(definition)
    db_session.flush()

    instance = EAWorkflowInstance(
        workflow_definition_id=definition.id,
        instance_code=f"INST-{uuid.uuid4().hex[:8]}",
        context={},
        status="running",
        organization_id=org_a.id,
    )
    db_session.add(instance)
    db_session.commit()

    engine = EAWorkflowEngine()
    step_def = {"config": {"recipients": [theirs.id, mine.id]}}

    with app.test_request_context("/"):
        engine._handle_notification(instance, step_def, {})

    notified_ids = {
        n.recipient_id
        for n in EAWorkflowNotification.query.filter_by(workflow_instance_id=instance.id).all()
    }
    assert theirs.id not in notified_ids, (
        f"Expected no notification for a recipient outside organization {org_a.id}, "
        f"notified {notified_ids!r}"
    )
    assert mine.id in notified_ids


# -- site 3: app/modules/solutions_strategic/v2/routes/solution_design_routes.py -------
#
# _serialize_arb_review_history_item used to derive the submitter/reviewer/decided-by
# and comment-author names from an already-loaded User relationship
# (_condition_actor_name(review.submitter), etc.) with no organisation check of its
# own -- the same sibling-field shape as a kanban card's owner field. It now resolves
# every actor id through _user_display_name (the caller-organisation-scoped helper
# already used for the approval-condition owner name).


def test_arb_review_history_names_only_actors_of_the_callers_organisation(app, db_session, tenant_ctx):
    from app.models.architecture_review_board import ARBReviewComment, ARBReviewItem
    from app.modules.solutions_strategic.v2.routes.solution_design_routes import (
        _serialize_arb_review_history_item,
    )

    org_a, _org_b, _mine, theirs = _world(db_session)

    review = ARBReviewItem(
        review_number=f"REV-{uuid.uuid4().hex[:8]}",
        title="Test review",
        review_type="architectural_decision",
        submitter_id=theirs.id,
        reviewer_id=theirs.id,
        decided_by_id=theirs.id,
        organization_id=org_a.id,
    )
    db_session.add(review)
    db_session.flush()

    comment = ARBReviewComment(
        review_item_id=review.id, user_id=theirs.id, content="A comment",
        organization_id=org_a.id,
    )
    db_session.add(comment)
    db_session.commit()

    with tenant_ctx(org_a.id):
        serialized = _serialize_arb_review_history_item(review)

    assert serialized["submitter_name"] is None, serialized["submitter_name"]
    assert serialized["reviewer_name"] is None, serialized["reviewer_name"]
    assert serialized["decided_by_name"] is None, serialized["decided_by_name"]
    assert len(serialized["comments"]) == 1
    assert serialized["comments"][0]["author_name"] == "Unknown User", (
        serialized["comments"][0]["author_name"]
    )
