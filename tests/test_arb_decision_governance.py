"""Regression tests for M-05 (S1) and M-06 (S1) from the 18 Aug 2026 QA gap
register, confirmed against live production by the owner's auditor.

M-05 — separation of duties: a submitter could approve their own review.
M-06 — attribution: the ARB decision schema stored no ``decided_by`` at all,
so nothing enforced that a decision be attributable to a real approver.

Same defect class as ARCH-022 (closed for the AI approval queue in f147872):
this applies the identical pattern to ``ARBGovernanceService.record_decision``
— refuse to write a decision that has no resolvable, non-submitter approver,
and audit-log the refusal itself.

``decided_by_id`` stays NULLABLE in the database (reconcile-schema is
add-column-nullable-only); the invariant is enforced in application code,
tested here.
"""

import uuid

import pytest


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(db_session, org, *, email=None):
    from app.models.user import Role, User

    role = Role.query.filter_by(name="Architect").first()
    if role is None:
        Role.insert_roles()
        role = Role.query.filter_by(name="Architect").first()

    user = User(
        email=email or f"arb-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Test",
        last_name="User",
        organization_id=org.id,
        role=role,
        confirmed=True,
    )
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _make_review_item(db_session, org, submitter):
    from app.models.architecture_review_board import ARBReviewItem

    suffix = uuid.uuid4().hex[:8]
    item = ARBReviewItem(
        organization_id=org.id,
        review_number=f"REV-TEST-{suffix}",
        title="Test governance review",
        review_type="architecture_change",
        status="submitted",
        submitter_id=submitter.id,
    )
    db_session.add(item)
    db_session.flush()
    return item


class TestSelfApprovalBlocked:
    """M-05 (S1): the submitter cannot also decide their own review."""

    def test_submitter_cannot_approve_own_review(self, app, db_session, make_org):
        from app.services.arb_governance_service import ARBGovernanceService, SelfApprovalError

        with app.app_context():
            org = make_org("arb-self-approval")
            submitter = _make_user(db_session, org)
            item = _make_review_item(db_session, org, submitter)

            with pytest.raises(SelfApprovalError):
                ARBGovernanceService().record_decision(
                    review_item_id=item.id,
                    decision="approved",
                    rationale="Looks good to me",
                    decided_by_id=submitter.id,
                )

            # The refusal must not have written a decision.
            db_session.refresh(item)
            assert item.decision is None
            assert item.decided_by_id is None

    def test_different_approver_can_decide(self, app, db_session, make_org):
        from app.services.arb_governance_service import ARBGovernanceService

        with app.app_context():
            org = make_org("arb-cross-approval")
            submitter = _make_user(db_session, org)
            approver = _make_user(db_session, org)
            item = _make_review_item(db_session, org, submitter)

            result = ARBGovernanceService().record_decision(
                review_item_id=item.id,
                decision="approved",
                rationale="Independent review complete",
                decided_by_id=approver.id,
            )

            assert result.decision == "approved"
            assert result.decided_by_id == approver.id

    def test_self_approval_over_http_is_blocked(self, app, db_session, make_org, login_as, client):
        """Server-side block: the UI is not the control. Posting the decision
        form as the submitter must be rejected regardless of what the client
        sends."""
        from app.models.architecture_review_board import ARBReviewItem

        org = make_org("arb-http-self-approval")
        submitter = _make_user(db_session, org)
        item = _make_review_item(db_session, org, submitter)
        db_session.commit()

        login_as(client, submitter)
        with client.session_transaction() as sess:
            sess["current_org_id"] = org.id

        resp = client.post(
            f"/arb/reviews/{item.id}/decision",
            data={"decision": "approved", "rationale": "self-approving"},
            follow_redirects=False,
        )

        assert resp.status_code == 403

        refreshed = db_session.get(ARBReviewItem, item.id)
        assert refreshed.decision is None
        assert refreshed.decided_by_id is None


class TestApproverIdentityRequired:
    """M-06 (S1): a decision cannot persist without a resolvable approver."""

    def test_decision_refused_without_approver_id(self, app, db_session, make_org):
        from app.services.arb_governance_service import (
            ARBGovernanceService,
            MissingApproverError,
        )

        with app.app_context():
            org = make_org("arb-no-approver")
            submitter = _make_user(db_session, org)
            item = _make_review_item(db_session, org, submitter)

            with pytest.raises(MissingApproverError):
                ARBGovernanceService().record_decision(
                    review_item_id=item.id,
                    decision="approved",
                    rationale="no one home",
                    decided_by_id=None,
                )

            db_session.refresh(item)
            assert item.decision is None
            assert item.decided_by_id is None

    def test_decision_refused_with_falsy_approver_id(self, app, db_session, make_org):
        """0 is falsy and is never a real user id — must be refused exactly
        like None, not coerced into "no filter"/"anonymous" writes."""
        from app.services.arb_governance_service import (
            ARBGovernanceService,
            MissingApproverError,
        )

        with app.app_context():
            org = make_org("arb-falsy-approver")
            submitter = _make_user(db_session, org)
            item = _make_review_item(db_session, org, submitter)

            with pytest.raises(MissingApproverError):
                ARBGovernanceService().record_decision(
                    review_item_id=item.id,
                    decision="approved",
                    rationale="",
                    decided_by_id=0,
                )


class TestAuditTrailWritten:
    """ARCH-092 groundwork: a successful decision must leave an audit row."""

    def test_decision_writes_audit_log_entry(self, app, db_session, make_org):
        from app.models.architecture_review_board import ARBAuditLog
        from app.services.arb_governance_service import ARBGovernanceService

        with app.app_context():
            org = make_org("arb-audit-trail")
            submitter = _make_user(db_session, org)
            approver = _make_user(db_session, org)
            item = _make_review_item(db_session, org, submitter)

            ARBGovernanceService().record_decision(
                review_item_id=item.id,
                decision="approved",
                rationale="Meets all standards",
                decided_by_id=approver.id,
            )

            logs = (
                ARBAuditLog.query.filter_by(entity_type="review_item", entity_id=item.id)
                .order_by(ARBAuditLog.id.desc())
                .all()
            )
            assert any(log.action == "decision" for log in logs)
            decision_log = next(log for log in logs if log.action == "decision")
            assert decision_log.user_id == approver.id
            assert decision_log.new_value.get("decision") == "approved"

    def test_self_approval_refusal_is_also_audited(self, app, db_session, make_org):
        """A refused write is itself a governance-relevant event and must be
        recorded, mirroring AIChatApprovalAuditLog's execution_refused event."""
        from app.models.architecture_review_board import ARBAuditLog
        from app.services.arb_governance_service import ARBGovernanceService, SelfApprovalError

        with app.app_context():
            org = make_org("arb-audit-refusal")
            submitter = _make_user(db_session, org)
            item = _make_review_item(db_session, org, submitter)

            with pytest.raises(SelfApprovalError):
                ARBGovernanceService().record_decision(
                    review_item_id=item.id,
                    decision="approved",
                    rationale="self",
                    decided_by_id=submitter.id,
                )

            logs = ARBAuditLog.query.filter_by(
                entity_type="review_item", entity_id=item.id
            ).all()
            assert any(log.action == "self_approval_refused" for log in logs)


class TestPreExistingRowWithNoDecidedBy:
    """The production REV-2026-001 row has decision=approved and
    decided_by_id=NULL. Read paths must tolerate that without crashing."""

    def test_review_detail_renders_with_null_decided_by(
        self, app, db_session, make_org, login_as, client
    ):
        from app.models.architecture_review_board import ARBReviewItem

        org = make_org("arb-legacy-null-decider")
        submitter = _make_user(db_session, org)
        item = _make_review_item(db_session, org, submitter)
        item.decision = "approved"
        item.status = "approved"
        item.decided_by_id = None  # pre-existing production shape
        db_session.commit()

        login_as(client, submitter)
        with client.session_transaction() as sess:
            sess["current_org_id"] = org.id

        resp = client.get(f"/arb/reviews/{item.id}")
        assert resp.status_code == 200

        refreshed = db_session.get(ARBReviewItem, item.id)
        assert refreshed.decided_by_id is None


class TestDecisionVocabulary:
    """An ARB can only record outcomes it recognises.

    Until 31 Aug 2026 record_decision() never validated `decision`, so any
    string was written straight through. An adversarial sweep sent
    `decision=banana`: it persisted, the status did not move, and the review
    page rendered "Outcome: Banana" over a row whose status contradicted it.
    The audit log then held an immutable entry reading "Decision recorded:
    banana", so the record of truth attested to it.
    """

    def test_an_unrecognised_decision_is_refused(self, app, db_session, make_org):
        from app.services.arb_governance_service import (
            ARBGovernanceService,
            InvalidDecisionError,
        )

        with app.app_context():
            org = make_org("arb-vocab")
            submitter = _make_user(db_session, org)
            decider = _make_user(db_session, org)
            item = _make_review_item(db_session, org, submitter)

            with pytest.raises(InvalidDecisionError):
                ARBGovernanceService().record_decision(
                    review_item_id=item.id,
                    decision="banana",
                    rationale="nonsense",
                    decided_by_id=decider.id,
                )

            db_session.refresh(item)
            assert item.decision != "banana", "the garbage value was persisted"
            assert item.status == "submitted", "status moved on a refused decision"

    @pytest.mark.parametrize(
        "decision",
        ["approved", "approved_with_conditions", "rejected", "deferred"],
    )
    def test_every_real_outcome_is_still_accepted(
        self, app, db_session, make_org, decision
    ):
        """The guard must not have narrowed the product's actual vocabulary."""
        from app.services.arb_governance_service import ARBGovernanceService

        with app.app_context():
            org = make_org("arb-vocab-ok")
            submitter = _make_user(db_session, org)
            decider = _make_user(db_session, org)
            item = _make_review_item(db_session, org, submitter)

            ARBGovernanceService().record_decision(
                review_item_id=item.id,
                decision=decision,
                rationale="A real outcome.",
                decided_by_id=decider.id,
            )
            db_session.refresh(item)
            assert item.decision == decision


class TestDecisionStateMachine:
    """A decision needs something to decide, and once made it stands."""

    def test_an_unsubmitted_review_cannot_be_decided(self, app, db_session, make_org):
        """Approving a draft bypasses the entire governance workflow."""
        from app.services.arb_governance_service import (
            ARBGovernanceService,
            InvalidStateTransitionError,
        )

        with app.app_context():
            org = make_org("arb-draft")
            submitter = _make_user(db_session, org)
            decider = _make_user(db_session, org)
            item = _make_review_item(db_session, org, submitter)
            item.status = "draft"
            db_session.flush()

            with pytest.raises(InvalidStateTransitionError):
                ARBGovernanceService().record_decision(
                    review_item_id=item.id,
                    decision="approved",
                    rationale="never submitted",
                    decided_by_id=decider.id,
                )

            db_session.refresh(item)
            assert item.status == "draft", "a draft was moved to a decided state"

    def test_a_decided_review_cannot_be_silently_redecided(
        self, app, db_session, make_org
    ):
        """Re-deciding rewrites the record of truth with no indication which
        entry is authoritative."""
        from app.services.arb_governance_service import (
            ARBGovernanceService,
            InvalidStateTransitionError,
        )

        with app.app_context():
            org = make_org("arb-final")
            submitter = _make_user(db_session, org)
            decider = _make_user(db_session, org)
            item = _make_review_item(db_session, org, submitter)

            service = ARBGovernanceService()
            service.record_decision(
                review_item_id=item.id,
                decision="approved",
                rationale="Approved on the evidence.",
                decided_by_id=decider.id,
            )
            db_session.refresh(item)
            assert item.status == "approved"

            with pytest.raises(InvalidStateTransitionError):
                service.record_decision(
                    review_item_id=item.id,
                    decision="rejected",
                    rationale="changed my mind",
                    decided_by_id=decider.id,
                )

            db_session.refresh(item)
            assert item.status == "approved", "an approved review was overturned"
            assert item.decision == "approved"

    def test_a_deferred_review_can_come_back_for_a_decision(
        self, app, db_session, make_org
    ):
        """Deferral means "decide later", so it must not be terminal.

        This is the case the finality rule must NOT break, and the reason
        DECIDABLE_STATUSES includes deferred.
        """
        from app.services.arb_governance_service import ARBGovernanceService

        with app.app_context():
            org = make_org("arb-deferred")
            submitter = _make_user(db_session, org)
            decider = _make_user(db_session, org)
            item = _make_review_item(db_session, org, submitter)

            service = ARBGovernanceService()
            service.record_decision(
                review_item_id=item.id,
                decision="deferred",
                rationale="Needs the cost model first.",
                decided_by_id=decider.id,
            )
            db_session.refresh(item)
            assert item.status == "deferred"

            service.record_decision(
                review_item_id=item.id,
                decision="approved",
                rationale="Cost model supplied.",
                decided_by_id=decider.id,
            )
            db_session.refresh(item)
            assert item.status == "approved"
