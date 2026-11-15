"""Review page fence on a warm session, and one decision-authority rule for typed and untyped reviews."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label, role="solution_architect"):
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


def _review(db_session, org, submitter, session_id=None):
    from app.models.architecture_review_board import ARBReviewItem

    item = ARBReviewItem(
        review_number=f"REV-TEST-{uuid.uuid4().hex[:10]}",
        title="Fence review",
        review_type="architecture",
        status="submitted",
        submitter_id=submitter.id,
        organization_id=org.id,
        arb_session_id=session_id,
    )
    db_session.add(item)
    db_session.commit()
    return item


def _board(db_session, org, member, *, voting):
    from app.models.architecture_review_board import ARBBoardMember, ArchitectureReviewBoard

    board = ArchitectureReviewBoard(
        organization_id=org.id,
        board_number=f"B-{uuid.uuid4().hex[:8]}",
        name="Fence board",
        scheduled_date=datetime(2030, 1, 1),
        status="scheduled",
    )
    db_session.add(board)
    db_session.flush()
    db_session.add(
        ARBBoardMember(
            organization_id=org.id,
            arb_session_id=board.id,
            user_id=member.id,
            role="enterprise_architect",
            voting_member=voting,
        )
    )
    db_session.commit()
    return board


def test_review_page_of_another_organisation_is_not_found_even_when_already_loaded(
    app, client, db_session, make_org, login_as
):
    org_a, org_b = make_org("fence-a"), make_org("fence-b")
    owner, intruder = _user(db_session, org_a, "owner"), _user(db_session, org_b, "intruder")
    item = _review(db_session, org_a, owner)
    item_id = item.id
    assert db_session.get(type(item), item_id) is item  # warm identity map, as in a busy worker

    login_as(client, intruder)
    assert client.get(f"/arb/reviews/{item_id}").status_code == 404

    login_as(client, owner)
    assert client.get(f"/arb/reviews/{item_id}").status_code == 200


def test_voting_board_member_without_the_role_is_treated_alike_on_both_paths(
    app, client, db_session, make_org, login_as
):
    from app.modules.transformation_room.arb_decision_service import may_decide_review

    org = make_org("fence-board")
    submitter = _user(db_session, org, "submitter")
    voter = _user(db_session, org, "voter")
    observer = _user(db_session, org, "observer")
    board = _board(db_session, org, voter, voting=True)
    observer_board = _board(db_session, org, observer, voting=False)
    voter_review = _review(db_session, org, submitter, board.id)
    observer_review = _review(db_session, org, submitter, observer_board.id)
    voter_review_id, observer_review_id = voter_review.id, observer_review.id

    # typed path: the shared rule
    assert may_decide_review(db_session, voter, voter_review) is True
    assert may_decide_review(db_session, observer, observer_review) is False

    # untyped path: the same answer
    login_as(client, observer)
    refused = client.post(f"/arb/api/arb/{observer_review_id}/review", json={})
    assert refused.status_code == 403
    login_as(client, voter)
    allowed = client.post(f"/arb/api/arb/{voter_review_id}/review", json={})
    assert allowed.status_code == 200, allowed.get_data(as_text=True)
