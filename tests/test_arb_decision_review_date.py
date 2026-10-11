"""An ARB decision carries the date the board looks at it again, when one is set.

The review date is stored on the review item's ``follow_up_date``. A decision
recorded without one leaves it empty (the page shows "—"); nothing invents a
date the board did not set.
"""

import uuid
from datetime import date

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, enterprise_role):
    from app.models.user import Role, User

    Role.insert_roles()
    user = User(email=f"arb-date-{uuid.uuid4().hex[:8]}@example.com", first_name="Test",
                last_name="User", organization_id=org.id, confirmed=True,
                enterprise_role=enterprise_role, role=Role.query.filter_by(name="Architect").first())
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _submitted_review(db_session, org, submitter):
    from app.models.architecture_review_board import ARBReviewItem

    item = ARBReviewItem(organization_id=org.id, review_number=f"REV-DATE-{uuid.uuid4().hex[:8]}",
                         title="Review date", review_type="architecture_change",
                         status="submitted", submitter_id=submitter.id)
    db_session.add(item)
    db_session.flush()
    return item


def test_decision_stores_the_review_date(db_session, make_org, tenant_ctx):
    from app.services.arb_governance_service import ARBGovernanceService

    org = make_org("arb-review-date")
    with tenant_ctx(org.id):
        submitter = _user(db_session, org, "solution_architect")
        member = _user(db_session, org, "arb_member")
        item = _submitted_review(db_session, org, submitter)
        ARBGovernanceService().record_decision(
            review_item_id=item.id, decision="approved_with_conditions", rationale="Two safeguards.",
            decided_by_id=member.id,
            conditions=[{"condition": "Publish the schema", "status": "pending", "due_date": None}],
            review_date=date(2027, 1, 15))
        assert item.follow_up_date == date(2027, 1, 15)
        assert item.follow_up_required is True
        assert item.to_dict()["follow_up_date"] == "2027-01-15"


def test_decision_without_a_review_date_leaves_it_empty(db_session, make_org, tenant_ctx):
    from app.services.arb_governance_service import ARBGovernanceService

    org = make_org("arb-no-review-date")
    with tenant_ctx(org.id):
        submitter = _user(db_session, org, "solution_architect")
        member = _user(db_session, org, "arb_member")
        item = _submitted_review(db_session, org, submitter)
        ARBGovernanceService().record_decision(
            review_item_id=item.id, decision="approved", rationale="No concerns.",
            decided_by_id=member.id)
        assert item.follow_up_date is None


def test_an_unreadable_review_date_is_refused(app, db_session, make_org, tenant_ctx, login_as):
    org = make_org("arb-bad-review-date")
    with tenant_ctx(org.id):
        submitter = _user(db_session, org, "solution_architect")
        member = _user(db_session, org, "arb_member")
        item = _submitted_review(db_session, org, submitter)
    client = app.test_client()
    login_as(client, member)
    response = client.post(f"/arb/reviews/{item.id}/decision",
                           data={"decision": "approved", "rationale": "x", "review_date": "15/01/2027"})
    assert response.status_code == 400
    db_session.refresh(item)
    assert item.decision is None and item.follow_up_date is None
