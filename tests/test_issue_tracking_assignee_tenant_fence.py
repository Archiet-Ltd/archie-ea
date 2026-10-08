"""A solution issue's assignee is named, and can be set, only inside the caller's organisation.

``SolutionIssue`` is org-scoped (``TenantMixin``), but ``assigned_to_id`` is a plain FK to
``users.id`` written straight from request JSON and resolved with an unscoped
``User.query.get(...)``. Before the fix, a signed-in user could POST or PUT another
organisation's user id as ``assigned_to_id`` and the list/detail/update responses would
then show that user's e-mail local part — and a pre-existing row with a foreign
``assigned_to_id`` (e.g. from data set before this fix, or a still-unfixed caller) leaked
the same way on every read.
"""

from __future__ import annotations

import uuid


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Fence {label} {suffix}", slug=f"fence-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, first, last):
    from app.models.user import User

    user = User(
        first_name=first, last_name=last,
        email=f"{first.lower()}-{uuid.uuid4().hex[:6]}@example.test",
        password="test-password-123", confirmed=True, organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _solution(db_session, org, name):
    from app.models.solution_models import Solution

    solution = Solution(name=name, organization_id=org.id)
    db_session.add(solution)
    db_session.flush()
    return solution


def _issue(db_session, solution, *, assigned_to_id=None, created_by_id=None):
    from app.models.solution_governance import SolutionIssue

    issue = SolutionIssue(
        solution_id=solution.id,
        title="Pre-existing issue",
        description="Filed before this fence existed.",
        severity="P2",
        priority=10,
        status="open",
        organization_id=solution.organization_id,
        assigned_to_id=assigned_to_id,
        created_by_id=created_by_id,
    )
    db_session.add(issue)
    db_session.flush()
    return issue


def _world(db_session):
    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    mine = _user(db_session, org_a, "Ada", "Mine")
    theirs = _user(db_session, org_b, "Vic", "Tim")
    solution_a = _solution(db_session, org_a, "Org A solution")
    db_session.commit()
    return org_a, org_b, mine, theirs, solution_a


# -- write side: refuse a foreign assigned_to_id --------------------------------------


def test_create_issue_refuses_assigned_to_id_of_another_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, solution_a = _world(db_session)

    login_as(client, mine)
    resp = client.post(
        f"/api/solutions/{solution_a.id}/issues",
        json={"title": "Ship it", "description": "desc", "assigned_to_id": theirs.id},
    )

    assert resp.status_code == 400, resp.get_json()
    assert "error" in resp.get_json()

    from app.models.solution_governance import SolutionIssue

    assert SolutionIssue.query.filter(SolutionIssue.solution_id == solution_a.id).count() == 0


def test_create_issue_accepts_assigned_to_id_of_the_callers_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, solution_a = _world(db_session)

    login_as(client, mine)
    resp = client.post(
        f"/api/solutions/{solution_a.id}/issues",
        json={"title": "Ship it", "description": "desc", "assigned_to_id": mine.id},
    )

    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert body["assigned_to_id"] == mine.id


def test_update_issue_refuses_assigned_to_id_of_another_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, solution_a = _world(db_session)
    issue = _issue(db_session, solution_a, assigned_to_id=None, created_by_id=mine.id)
    db_session.commit()

    login_as(client, mine)
    resp = client.put(
        f"/api/solutions/{solution_a.id}/issues/{issue.id}",
        json={"assigned_to_id": theirs.id},
    )

    assert resp.status_code == 400, resp.get_json()
    assert "error" in resp.get_json()

    from app.models.solution_governance import SolutionIssue

    refreshed = SolutionIssue.query.get(issue.id)
    assert refreshed.assigned_to_id is None


# -- read side: a stored foreign assigned_to_id shows no name or e-mail ---------------


def test_list_issues_hides_foreign_assignee_name_and_email(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, solution_a = _world(db_session)
    _issue(db_session, solution_a, assigned_to_id=theirs.id, created_by_id=mine.id)
    db_session.commit()

    login_as(client, mine)
    resp = client.get(f"/api/solutions/{solution_a.id}/issues")

    assert resp.status_code == 200, resp.get_json()
    issues = resp.get_json()
    assert len(issues) == 1
    assert "assigned_to" not in issues[0], issues[0]
    assert theirs.email.split("@")[0] not in str(issues[0])


def test_get_issue_hides_foreign_assignee_name_and_email(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, solution_a = _world(db_session)
    issue = _issue(db_session, solution_a, assigned_to_id=theirs.id, created_by_id=mine.id)
    db_session.commit()

    login_as(client, mine)
    resp = client.get(f"/api/solutions/{solution_a.id}/issues/{issue.id}")

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "assigned_to" not in body, body
    assert theirs.email.split("@")[0] not in str(body)


def test_update_issue_response_hides_foreign_assignee_name_and_email(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, solution_a = _world(db_session)
    issue = _issue(db_session, solution_a, assigned_to_id=theirs.id, created_by_id=mine.id)
    db_session.commit()

    login_as(client, mine)
    # Update an unrelated field; the pre-existing foreign assigned_to_id is untouched.
    resp = client.put(
        f"/api/solutions/{solution_a.id}/issues/{issue.id}",
        json={"title": "Renamed"},
    )

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "assigned_to" not in body, body
    assert theirs.email.split("@")[0] not in str(body)
