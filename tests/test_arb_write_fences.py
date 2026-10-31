"""ARB writes need the approver's role and stay inside the signed-in organisation."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, role):
    from app.models.user import User

    user = User(
        email=f"{label}-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Test",
        last_name=label,
        organization_id=org.id,
        enterprise_role=role,
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _session(db_session, org):
    from app.models.architecture_review_board import ArchitectureReviewBoard

    board = ArchitectureReviewBoard(
        board_number=f"ARB-T-{uuid.uuid4().hex[:8]}",
        name="Fence board",
        scheduled_date=datetime.utcnow(),
        status="scheduled",
        organization_id=org.id,
    )
    db_session.add(board)
    db_session.commit()
    return board


def _review(db_session, org, submitter, status="draft"):
    from app.models.architecture_review_board import ARBReviewItem

    item = ARBReviewItem(
        review_number=f"REV-T-{uuid.uuid4().hex[:10]}",
        title="Fence review",
        review_type="architecture",
        status=status,
        submitter_id=submitter.id,
        organization_id=org.id,
    )
    db_session.add(item)
    db_session.commit()
    return item


def _exception(db_session, org, requester):
    from app.models.architecture_review_board import ARBException

    row = ARBException(
        exception_number=f"EXC-T-{uuid.uuid4().hex[:10]}",
        status="requested",
        exception_reason="Needs a waiver",
        requested_by_id=requester.id,
        organization_id=org.id,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _exception_state(db_session, exception_id):
    return db_session.execute(
        text("SELECT status, approved_by_id FROM arb_exceptions WHERE id = :i"),
        {"i": exception_id},
    ).one()


def _member_count(db_session, session_id):
    return db_session.execute(
        text("SELECT count(*) FROM arb_board_members WHERE arb_session_id = :s"),
        {"s": session_id},
    ).scalar()


def test_member_without_the_role_cannot_approve_an_exception(
    app, client, db_session, make_org, login_as
):
    org = make_org("arb-exc")
    member = _user(db_session, org, "member", "solution_architect")
    exc_id = _exception(db_session, org, member).id

    login_as(client, member)
    resp = client.put(f"/arb/api/exceptions/{exc_id}/approve", json={})
    assert resp.status_code == 403
    assert _exception_state(db_session, exc_id).status == "requested"


def test_approver_approves_an_exception_as_themselves(
    app, client, db_session, make_org, login_as
):
    org = make_org("arb-exc")
    member = _user(db_session, org, "member", "solution_architect")
    approver = _user(db_session, org, "approver", "enterprise_architect")
    exc_id = _exception(db_session, org, member).id
    approver_id, member_id = approver.id, member.id

    login_as(client, approver)
    resp = client.put(
        f"/arb/api/exceptions/{exc_id}/approve", json={"approved_by_id": member_id}
    )
    assert resp.status_code == 200
    state = _exception_state(db_session, exc_id)
    assert state.status == "approved"
    assert state.approved_by_id == approver_id


def test_approver_of_another_organisation_cannot_approve_an_exception(
    app, client, db_session, make_org, login_as
):
    org_a, org_b = make_org("arb-a"), make_org("arb-b")
    member_a = _user(db_session, org_a, "a", "solution_architect")
    approver_b = _user(db_session, org_b, "b", "enterprise_architect")
    exc_id = _exception(db_session, org_a, member_a).id

    login_as(client, approver_b)
    resp = client.put(f"/arb/api/exceptions/{exc_id}/approve", json={})
    assert resp.status_code == 404
    assert _exception_state(db_session, exc_id).status == "requested"


def test_adding_another_organisations_user_to_a_session_is_refused(
    app, client, db_session, make_org, login_as
):
    org_a, org_b = make_org("arb-a"), make_org("arb-b")
    chair_a = _user(db_session, org_a, "chair", "enterprise_architect")
    outsider = _user(db_session, org_b, "outsider", "solution_architect")
    session_id = _session(db_session, org_a).id

    login_as(client, chair_a)
    resp = client.post(
        f"/arb/sessions/{session_id}/add_member",
        data={"user_id": outsider.id, "role": "solution_architect", "voting_member": "on"},
    )
    assert resp.status_code == 302
    assert _member_count(db_session, session_id) == 0


def test_adding_a_user_of_the_same_organisation_is_stored(
    app, client, db_session, make_org, login_as
):
    org_a = make_org("arb-a")
    chair_a = _user(db_session, org_a, "chair", "enterprise_architect")
    colleague = _user(db_session, org_a, "colleague", "solution_architect")
    session_id = _session(db_session, org_a).id

    login_as(client, chair_a)
    resp = client.post(
        f"/arb/sessions/{session_id}/add_member",
        data={"user_id": colleague.id, "role": "solution_architect", "voting_member": "on"},
    )
    assert resp.status_code == 302
    assert _member_count(db_session, session_id) == 1


@pytest.mark.parametrize("action", ["complete", "cancel", "delete"])
def test_member_without_the_role_cannot_close_or_delete_a_session(
    app, client, db_session, make_org, login_as, action
):
    org = make_org("arb-sess")
    member = _user(db_session, org, "member", "solution_architect")
    session_id = _session(db_session, org).id

    login_as(client, member)
    resp = client.post(f"/arb/sessions/{session_id}/{action}")
    assert resp.status_code == 403
    status = db_session.execute(
        text("SELECT status FROM architecture_review_boards WHERE id = :i"),
        {"i": session_id},
    ).scalar()
    assert status == "scheduled"


def test_member_without_the_role_cannot_assign_or_delete_a_review(
    app, client, db_session, make_org, login_as
):
    org = make_org("arb-rev")
    member = _user(db_session, org, "member", "solution_architect")
    session_id = _session(db_session, org).id
    review_id = _review(db_session, org, member).id

    login_as(client, member)
    resp = client.post(f"/arb/reviews/{review_id}/assign", data={"arb_session_id": session_id})
    assert resp.status_code == 403
    resp = client.post(f"/arb/reviews/{review_id}/delete")
    assert resp.status_code == 403
    row = db_session.execute(
        text("SELECT arb_session_id FROM arb_review_items WHERE id = :i"), {"i": review_id}
    ).one_or_none()
    assert row is not None and row.arb_session_id is None


def test_approver_can_cancel_their_own_organisations_session(
    app, client, db_session, make_org, login_as
):
    org = make_org("arb-sess")
    chair = _user(db_session, org, "chair", "enterprise_architect")
    session_id = _session(db_session, org).id

    login_as(client, chair)
    resp = client.post(f"/arb/sessions/{session_id}/cancel")
    assert resp.status_code == 302
    status = db_session.execute(
        text("SELECT status FROM architecture_review_boards WHERE id = :i"),
        {"i": session_id},
    ).scalar()
    assert status == "cancelled"


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/arb/reviews/{id}/audit-trail.csv"),
        ("get", "/arb/reviews/{id}/history.csv"),
        ("post", "/arb/api/reviews/{id}/checklist"),
        ("post", "/arb/api/arb/{id}/review"),
        ("post", "/arb/api/arb/{id}/approve"),
        ("post", "/arb/api/arb/{id}/reject"),
        ("post", "/arb/api/arb/{id}/request-changes"),
        ("get", "/arb/api/arb/{id}/implementation-status"),
        ("patch", "/arb/api/arb/{id}/implementation-status"),
    ],
)
def test_another_organisations_review_is_not_found_on_every_lookup(
    app, client, db_session, make_org, login_as, method, path
):
    org_a, org_b = make_org("arb-a"), make_org("arb-b")
    user_a = _user(db_session, org_a, "a", "enterprise_architect")
    user_b = _user(db_session, org_b, "b", "enterprise_architect")
    review = _review(db_session, org_a, user_a, status="under_review")
    review_id = review.id
    user_b_id = user_b.id
    # Loaded in this session, as it is when the same worker served organisation A a moment ago.
    assert db_session.get(type(review), review_id) is review

    login_as(client, user_b)
    kwargs = {"json": {"checklist": {}}} if method != "get" else {}
    resp = getattr(client, method)(path.format(id=review_id), **kwargs)
    assert resp.status_code == 404, resp.data[:200]
    status = db_session.execute(
        text("SELECT status FROM arb_review_items WHERE id = :i"), {"i": review_id}
    ).scalar()
    assert status == "under_review"
    assert user_b_id


def _review_state(db_session, review_id):
    return db_session.execute(
        text("SELECT status, decided_by_id FROM arb_review_items WHERE id = :i"),
        {"i": review_id},
    ).one()


_DECISION_CALLS = (
    ("review", "under_review", {}),
    ("approve", "under_review", {}),
    ("reject", "under_review", {"reason": "Not aligned"}),
    ("request-changes", "under_review", {"conditions": ["Fix the data flow"]}),
)


@pytest.mark.parametrize("action,start_status,body", _DECISION_CALLS)
def test_member_cannot_decide_an_untyped_review(
    app, client, db_session, make_org, login_as, action, start_status, body
):
    org = make_org("arb-dec")
    submitter = _user(db_session, org, "submitter", "solution_architect")
    member = _user(db_session, org, "member", "solution_architect")
    start = "submitted" if action == "review" else start_status
    review_id = _review(db_session, org, submitter, status=start).id

    login_as(client, member)
    resp = client.post(f"/arb/api/arb/{review_id}/{action}", json=body)
    assert resp.status_code == 403
    assert _review_state(db_session, review_id).status == start


@pytest.mark.parametrize("action,start_status,body", _DECISION_CALLS)
def test_submitter_cannot_decide_their_own_untyped_review(
    app, client, db_session, make_org, login_as, action, start_status, body
):
    org = make_org("arb-dec")
    submitter = _user(db_session, org, "submitter", "enterprise_architect")
    start = "submitted" if action == "review" else start_status
    review_id = _review(db_session, org, submitter, status=start).id

    login_as(client, submitter)
    resp = client.post(f"/arb/api/arb/{review_id}/{action}", json=body)
    assert resp.status_code == 403
    assert _review_state(db_session, review_id).status == start


def test_approver_who_is_not_the_submitter_approves_and_is_recorded(
    app, client, db_session, make_org, login_as
):
    org = make_org("arb-dec")
    submitter = _user(db_session, org, "submitter", "solution_architect")
    approver = _user(db_session, org, "approver", "enterprise_architect")
    review_id = _review(db_session, org, submitter, status="submitted").id
    approver_id = approver.id

    login_as(client, approver)
    assert client.post(f"/arb/api/arb/{review_id}/review", json={}).status_code == 200
    resp = client.post(f"/arb/api/arb/{review_id}/approve", json={})
    assert resp.status_code == 200
    state = _review_state(db_session, review_id)
    assert state.status == "approved"
    assert state.decided_by_id == approver_id


def test_other_organisation_cannot_decide_the_review(
    app, client, db_session, make_org, login_as
):
    org = make_org("arb-dec")
    other = make_org("arb-dec-other")
    submitter = _user(db_session, org, "submitter", "solution_architect")
    outsider = _user(db_session, other, "outsider", "enterprise_architect")
    review_id = _review(db_session, org, submitter, status="under_review").id

    login_as(client, outsider)
    assert client.post(f"/arb/api/arb/{review_id}/approve", json={}).status_code == 404
    assert _review_state(db_session, review_id).status == "under_review"


def test_member_cannot_initialise_governance_standards(
    app, client, db_session, make_org, login_as
):
    org = make_org("arb-std")
    member = _user(db_session, org, "member", "solution_architect")
    login_as(client, member)
    assert client.post("/arb/initialize_standards").status_code == 403
