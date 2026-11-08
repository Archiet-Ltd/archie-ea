"""The board's approval rate is the share of decided reviews that were approved, not of every review."""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _review(db_session, org, user, status, tag):
    from app.models.architecture_review_board import ARBReviewItem

    db_session.add(ARBReviewItem(
        organization_id=org.id, status=status, review_number="REV-%s-%s" % (tag, uuid.uuid4().hex[:6]),
        title="Review %s" % status, review_type="architecture", submitter_id=user.id))


def _user(db_session, org):
    from app.models.user import User

    user = User(email="arb-%s@example.com" % uuid.uuid4().hex[:8], first_name="A", last_name="B",
                organization_id=org.id, confirmed=True)
    db_session.add(user)
    db_session.flush()
    return user


def test_approval_rate_leaves_out_reviews_still_pending(db_session, make_org, tenant_ctx):
    from app.services.arb_governance_service import ARBGovernanceService

    mine, theirs = make_org("arb-rate-a"), make_org("arb-rate-b")
    me, them = _user(db_session, mine), _user(db_session, theirs)
    tag = uuid.uuid4().hex[:4]
    # mine: 3 approved, 1 rejected, 6 pending -> 3 of 4 decided are approved
    for status in ["approved", "approved", "approved_with_conditions", "rejected"] + ["submitted"] * 6:
        _review(db_session, mine, me, status, tag)
    # theirs: nothing decided
    for _ in range(5):
        _review(db_session, theirs, them, "submitted", tag)
    db_session.flush()
    with tenant_ctx(mine.id):
        metrics = ARBGovernanceService().get_governance_dashboard()["metrics"]
    assert metrics["total_items"] == 10 and metrics["pending_items"] == 6
    assert metrics["approval_rate"] == 75.0
    with tenant_ctx(theirs.id):
        metrics = ARBGovernanceService().get_governance_dashboard()["metrics"]
    assert metrics["approval_rate"] is None
