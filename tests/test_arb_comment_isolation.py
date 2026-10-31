"""A review comment is stored on, and shown on, only the signed-in organisation's review items."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.usefixtures("db_session")


def _user(db_session, org, label):
    from app.models.user import User

    user = User(
        email=f"{label}-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Test",
        last_name=label,
        organization_id=org.id,
        enterprise_role="solution_architect",
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _review(db_session, org, submitter):
    from app.models.architecture_review_board import ARBReviewItem

    item = ARBReviewItem(
        review_number=f"REV-TEST-{uuid.uuid4().hex[:10]}",
        title="Isolation review",
        review_type="architecture",
        status="draft",
        submitter_id=submitter.id,
        organization_id=org.id,
    )
    db_session.add(item)
    db_session.commit()
    return item


def _count(db_session, org_id, review_id):
    return db_session.execute(
        text("SELECT count(*) FROM arb_review_comments WHERE review_item_id = :r"),
        {"r": review_id},
    ).scalar()


def test_foreign_organisation_cannot_comment_or_read_comment(
    app, client, db_session, make_org, login_as
):
    org_a, org_b = make_org("arb-a"), make_org("arb-b")
    user_a, user_b = _user(db_session, org_a, "a"), _user(db_session, org_b, "b")
    item_a = _review(db_session, org_a, user_a)
    item_a_id = item_a.id

    login_as(client, user_b)
    resp = client.post(
        f"/arb/reviews/{item_a_id}/comment",
        data={"content": "intruder note from B", "comment_type": "general"},
    )
    assert resp.status_code == 404
    assert _count(db_session, org_a.id, item_a_id) == 0

    login_as(client, user_a)
    resp = client.get(f"/arb/reviews/{item_a_id}")
    assert resp.status_code == 200
    assert b"intruder note from B" not in resp.data


def test_comment_read_ignores_rows_of_another_organisation(
    app, client, db_session, make_org, login_as
):
    """A comment row already stored under another organisation never renders."""
    from app.models.architecture_review_board import ARBReviewComment

    org_a, org_b = make_org("arb-a"), make_org("arb-b")
    user_a, user_b = _user(db_session, org_a, "a"), _user(db_session, org_b, "b")
    item_a = _review(db_session, org_a, user_a)
    item_a_id = item_a.id
    db_session.add_all(
        [
            ARBReviewComment(
                review_item_id=item_a_id, user_id=user_a.id, organization_id=org_a.id,
                content="own note from A",
            ),
            ARBReviewComment(
                review_item_id=item_a_id, user_id=user_b.id, organization_id=org_b.id,
                content="stray note from B",
            ),
        ]
    )
    db_session.commit()

    login_as(client, user_a)
    resp = client.get(f"/arb/reviews/{item_a_id}")
    assert resp.status_code == 200
    assert b"own note from A" in resp.data
    assert b"stray note from B" not in resp.data


def test_own_organisation_comment_is_stored_and_shown(app, client, db_session, make_org, login_as):
    org_a = make_org("arb-a")
    user_a = _user(db_session, org_a, "a")
    item_id = _review(db_session, org_a, user_a).id

    login_as(client, user_a)
    resp = client.post(
        f"/arb/reviews/{item_id}/comment",
        data={"content": "note from A itself", "comment_type": "concern"},
    )
    assert resp.status_code == 302
    assert _count(db_session, org_a.id, item_id) == 1
    login_as(client, user_a)
    assert b"note from A itself" in client.get(f"/arb/reviews/{item_id}").data


# Every route that takes a review item id, called by the other organisation.
_FOREIGN_CALLS = [
    ("GET", "/arb/reviews/{id}", None),
    ("GET", "/arb/reviews/{id}/audit-trail.csv", None),
    ("GET", "/arb/reviews/{id}/history.csv", None),
    ("POST", "/arb/reviews/{id}/submit", {}),
    ("POST", "/arb/reviews/{id}/assign", {"arb_session_id": "1"}),
    ("POST", "/arb/reviews/{id}/delete", {}),
    ("GET", "/arb/api/reviews/{id}/assess", None),
    ("POST", "/arb/api/reviews/{id}/checklist", {"checklist": {"x": True}}),
    ("GET", "/arb/api/arb/{id}/implementation-status", None),
    ("PATCH", "/arb/api/arb/{id}/implementation-status", {"status": "in_progress"}),
    ("POST", "/arb/api/arb/{id}/review", {}),
    ("POST", "/arb/api/arb/{id}/approve", {"rationale": "approve it now"}),
    ("POST", "/arb/api/arb/{id}/reject", {"rationale": "reject it now"}),
    ("POST", "/arb/api/arb/{id}/request-changes", {"rationale": "change it now"}),
]


@pytest.mark.parametrize("method,path,payload", _FOREIGN_CALLS)
def test_foreign_organisation_gets_not_found_on_review_routes(
    app, client, db_session, make_org, login_as, method, path, payload
):
    from app.models.architecture_review_board import ARBReviewItem

    org_a, org_b = make_org("arb-a"), make_org("arb-b")
    user_a, user_b = _user(db_session, org_a, "a"), _user(db_session, org_b, "b")
    item_id = _review(db_session, org_a, user_a).id

    login_as(client, user_b)
    url = path.format(id=item_id)
    if method == "GET":
        resp = client.get(url)
    elif method == "PATCH" or "/api/" in path:
        resp = client.open(url, method=method, json=payload)
    else:
        resp = client.post(url, data=payload)
    assert resp.status_code == 404, (url, resp.status_code)

    db_session.expire_all()
    row = db_session.execute(
        text("SELECT status, arb_session_id FROM arb_review_items WHERE id = :i"),
        {"i": item_id},
    ).one_or_none()
    assert row is not None and row[0] == "draft" and row[1] is None


_FOREIGN_SESSION_CALLS = [
    ("GET", "/arb/sessions/{id}", None),
    ("POST", "/arb/sessions/{id}/complete", {"minutes": "x"}),
    ("POST", "/arb/sessions/{id}/cancel", {}),
    ("POST", "/arb/sessions/{id}/delete", {}),
    ("POST", "/arb/sessions/{id}/add_member", {"user_id": "{uid}", "role": "member"}),
]


@pytest.mark.parametrize("method,path,payload", _FOREIGN_SESSION_CALLS)
def test_foreign_organisation_gets_not_found_on_session_routes(
    app, client, db_session, make_org, login_as, method, path, payload
):
    from datetime import datetime

    from app.models.architecture_review_board import ArchitectureReviewBoard

    org_a, org_b = make_org("arb-a"), make_org("arb-b")
    user_b = _user(db_session, org_b, "b")
    user_b.enterprise_role = "enterprise_architect"
    board = ArchitectureReviewBoard(
        board_number=f"ARB-TEST-{uuid.uuid4().hex[:10]}",
        name="Isolation board",
        scheduled_date=datetime(2030, 1, 1),
        status="scheduled",
        organization_id=org_a.id,
    )
    db_session.add(board)
    db_session.commit()
    board_id = board.id

    login_as(client, user_b)
    data = {k: (str(user_b.id) if v == "{uid}" else v) for k, v in (payload or {}).items()}
    resp = client.open(path.format(id=board_id), method=method, data=data)
    assert resp.status_code == 404, (path.format(id=board_id), resp.status_code)

    db_session.expire_all()
    row = db_session.execute(
        text("SELECT status FROM architecture_review_boards WHERE id = :i"), {"i": board_id}
    ).one_or_none()
    assert row is not None and row[0] == "scheduled"
    members = db_session.execute(
        text("SELECT count(*) FROM arb_board_members WHERE arb_session_id = :i"), {"i": board_id}
    ).scalar()
    assert members == 0


def test_review_cannot_be_assigned_to_another_organisations_session(
    app, client, db_session, make_org, login_as
):
    from datetime import datetime

    from app.models.architecture_review_board import ArchitectureReviewBoard

    org_a, org_b = make_org("arb-a"), make_org("arb-b")
    user_a = _user(db_session, org_a, "a")
    item_id = _review(db_session, org_a, user_a).id
    board = ArchitectureReviewBoard(
        board_number=f"ARB-TEST-{uuid.uuid4().hex[:10]}",
        name="Other organisation board",
        scheduled_date=datetime(2030, 1, 1),
        status="scheduled",
        organization_id=org_b.id,
    )
    db_session.add(board)
    db_session.commit()
    board_id = board.id

    login_as(client, user_a)
    resp = client.post(f"/arb/reviews/{item_id}/assign", data={"arb_session_id": str(board_id)})
    assert resp.status_code == 404
    db_session.expire_all()
    row = db_session.execute(
        text("SELECT status, arb_session_id FROM arb_review_items WHERE id = :i"), {"i": item_id}
    ).one()
    assert tuple(row) == ("draft", None)
