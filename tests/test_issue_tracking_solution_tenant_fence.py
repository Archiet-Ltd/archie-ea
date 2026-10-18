"""POST /api/solutions/<id>/issues refuses a solution the caller's organisation cannot see.

The create handler never looked the solution up. Organisation B could post an issue to
organisation A's solution id: the row was stamped with B but pointed at A's solution (201 for
an existing foreign solution, 500 for an unknown one, an existence oracle), and A deleting the
solution cascaded away B's row.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text


def _solution(db_session, org):
    from app.models.solution_models import Solution

    sol = Solution(name=f"Sol {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(sol)
    db_session.flush()
    return sol


def _user(db_session, org):
    from app.models.user import User

    user = User(email=f"it-{uuid.uuid4().hex[:8]}@example.test", first_name="I", last_name="T",
                organization_id=org.id, confirmed=True)
    user.password = uuid.uuid4().hex
    db_session.add(user)
    db_session.flush()
    return user


def _setup(db_session, make_org):
    org_a, org_b = make_org("iss-a"), make_org("iss-b")
    sol_a, sol_b = _solution(db_session, org_a), _solution(db_session, org_b)
    user_a = _user(db_session, org_a)
    db_session.commit()
    ids = (sol_a.id, sol_b.id, user_a.id)
    db_session.expunge_all()
    return ids


def _post(client, login_as, db_session, user_id, solution_id):
    from app.models.user import User

    login_as(client, db_session.get(User, user_id))
    return client.post(f"/api/solutions/{solution_id}/issues",
                       json={"title": "t", "description": "d"})


def test_issue_on_another_organisations_solution_is_refused(app, db_session, make_org, client, login_as):
    _sol_a, sol_b, user_a = _setup(db_session, make_org)
    resp = _post(client, login_as, db_session, user_a, sol_b)
    assert resp.status_code == 404, resp.get_json()
    count = db_session.execute(
        text("select count(*) from solution_issues where solution_id = :s"), {"s": sol_b}).scalar()
    assert count == 0


def test_issue_on_an_unknown_solution_is_the_same_404(app, db_session, make_org, client, login_as):
    _sol_a, _sol_b, user_a = _setup(db_session, make_org)
    assert _post(client, login_as, db_session, user_a, 2_000_000_000).status_code == 404


def test_issue_on_own_solution_is_still_created(app, db_session, make_org, client, login_as):
    sol_a, _sol_b, user_a = _setup(db_session, make_org)
    assert _post(client, login_as, db_session, user_a, sol_a).status_code == 201
