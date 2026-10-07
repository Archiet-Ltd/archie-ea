"""Follow-up fixes to the kanban/solution-issue tenant fence (PR 234, PR 212).

Reviews of those PRs found four remaining gaps, all in the same
"a request-supplied user id is written unchecked" shape:

* ``SolutionIssue.escalated_to_id`` (written by ``escalate_issue``) and
  ``resolved_by_id`` (written by ``resolve_issue``) had no organisation check
  — only the sibling ``assigned_to_id`` field had been fixed. ``resolved_by_id``
  should never come from the request at all; it is who is actually acting.
  Both routes also loaded the issue by id alone, with no check that it
  belongs to the solution named in the URL.
* ``KanbanCard.assignee`` (a free-text column the projection service reads as
  a user id — see ``kanban_projection_service._resolve_user_label``) was
  still written from the request with no organisation check.
* ``get_board`` resolved each card's assignee with one query per card; this
  pins that the batched replacement still names a card's assignee only when
  that user belongs to the card's organisation, and no longer issues one
  ``users`` query per assigned card.
* rows already holding a foreign id: editing such a card must still work
  without naming the foreign user, and the ``clear-foreign-assignees``
  clean-up command reports them by default and clears them only with
  ``--apply``.
"""

from __future__ import annotations

import uuid


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Followup {label} {suffix}", slug=f"followup-{label}-{suffix}")
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


def _phase(db_session):
    from app.models.adm_kanban import ADMPhase

    suffix = uuid.uuid4().hex[:8].upper()
    phase = ADMPhase(name=f"Test Phase {suffix}", code=suffix[:10], order=1)
    db_session.add(phase)
    db_session.flush()
    return phase


def _board(db_session, org, creator):
    from app.models.adm_kanban import KanbanBoard

    board = KanbanBoard(
        name="Test Board", board_type="architecture_development",
        created_by_id=creator.id, organization_id=org.id,
    )
    db_session.add(board)
    db_session.flush()
    return board


def _card(db_session, board, phase, creator, *, assigned_to_id=None, assignee=None):
    from app.models.adm_kanban import KanbanCard

    card = KanbanCard(
        title="Card", adm_phase_id=phase.id, board_id=board.id,
        card_type="task", status="todo", assigned_to_id=assigned_to_id,
        assignee=assignee,
        created_by_id=creator.id, organization_id=board.organization_id,
    )
    db_session.add(card)
    db_session.flush()
    return card


def _solution(db_session, org, name):
    from app.models.solution_models import Solution

    solution = Solution(name=name, organization_id=org.id)
    db_session.add(solution)
    db_session.flush()
    return solution


def _issue(db_session, solution, *, assigned_to_id=None):
    from app.models.solution_governance import SolutionIssue

    issue = SolutionIssue(
        solution_id=solution.id, title="Issue", description="desc",
        severity="P3", priority=3, status="open",
        assigned_to_id=assigned_to_id, organization_id=solution.organization_id,
    )
    db_session.add(issue)
    db_session.flush()
    return issue


def _world(db_session):
    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    mine = _user(db_session, org_a, "Ada", "Mine")
    theirs = _user(db_session, org_b, "Vic", "Tim")
    phase = _phase(db_session)
    board = _board(db_session, org_a, mine)
    solution_a = _solution(db_session, org_a, "Org A solution")
    db_session.commit()
    return org_a, org_b, mine, theirs, phase, board, solution_a


def _create_issue(client, solution_id):
    return client.post(
        f"/api/solutions/{solution_id}/issues/create",
        json={"title": "Ship it", "description": "desc"},
    )


# -- escalated_to_id: refused for another organisation's user -------------------------
#
# `/api/solutions/<id>/issues/<id>/escalate` is registered twice (this module's
# governance_api blueprint and the legacy app/routes/issue_tracking.py blueprint,
# at the exact same URL — the ADR 0008 duplicate-route shape) and the legacy
# route, which never writes escalated_to_id at all, is the one Werkzeug actually
# dispatches to. These call the service directly so the fix under test is the
# one exercised, rather than the shadowed HTTP path; the routing collision
# itself is pre-existing and out of scope here.


def test_escalate_issue_refuses_escalated_to_id_of_another_organisation(app, db_session):
    import pytest

    from app.models.solution_governance import SolutionIssue
    from app.modules.solutions_strategic.v2.services.solution_issue_service import (
        SolutionIssueService,
    )

    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    issue = _issue(db_session, solution_a)
    db_session.commit()

    service = SolutionIssueService()
    with pytest.raises(ValueError):
        service.escalate_issue(
            issue_id=issue.id, escalated_to_id=theirs.id,
            escalation_reason="urgent", organization_id=org_a.id,
            solution_id=solution_a.id,
        )

    refreshed = SolutionIssue.query.get(issue.id)
    assert refreshed.escalated_to_id is None


def test_escalate_issue_accepts_escalated_to_id_of_the_callers_organisation(app, db_session):
    from app.models.solution_governance import SolutionIssue
    from app.modules.solutions_strategic.v2.services.solution_issue_service import (
        SolutionIssueService,
    )

    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    issue = _issue(db_session, solution_a)
    db_session.commit()

    service = SolutionIssueService()
    service.escalate_issue(
        issue_id=issue.id, escalated_to_id=mine.id,
        escalation_reason="urgent", organization_id=org_a.id,
        solution_id=solution_a.id,
    )

    refreshed = SolutionIssue.query.get(issue.id)
    assert refreshed.escalated_to_id == mine.id


# -- resolved_by_id: taken from the acting user, never the request --------------------


def test_resolve_issue_ignores_a_request_supplied_resolved_by_id(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)

    login_as(client, mine)
    create_resp = _create_issue(client, solution_a.id)
    assert create_resp.status_code == 201, create_resp.get_json()
    issue_id = create_resp.get_json()["id"]

    resp = client.post(
        f"/api/solutions/{solution_a.id}/issues/{issue_id}/resolve",
        json={"resolved_by_id": theirs.id, "resolution_notes": "done"},
    )

    assert resp.status_code == 200, resp.get_json()

    from app.models.solution_governance import SolutionIssue

    refreshed = SolutionIssue.query.get(issue_id)
    assert refreshed.resolved_by_id == mine.id, refreshed.resolved_by_id


# -- issue must belong to the solution named in the URL --------------------------------


def test_assign_issue_refused_when_issue_belongs_to_a_different_solution(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    other_solution_a = _solution(db_session, org_a, "Org A other solution")
    db_session.commit()

    login_as(client, mine)
    create_resp = _create_issue(client, solution_a.id)
    assert create_resp.status_code == 201, create_resp.get_json()
    issue_id = create_resp.get_json()["id"]

    resp = client.post(
        f"/api/solutions/{other_solution_a.id}/issues/{issue_id}/assign",
        json={"assigned_to_id": mine.id},
    )

    assert resp.status_code == 400, resp.get_json()
    assert "not found" in resp.get_json()["error"].lower(), resp.get_json()

    from app.models.solution_governance import SolutionIssue

    refreshed = SolutionIssue.query.get(issue_id)
    assert refreshed.assigned_to_id is None


def test_escalate_issue_refused_when_issue_belongs_to_a_different_solution(app, db_session):
    import pytest

    from app.models.solution_governance import SolutionIssue
    from app.modules.solutions_strategic.v2.services.solution_issue_service import (
        SolutionIssueService,
    )

    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    other_solution_a = _solution(db_session, org_a, "Org A other solution")
    issue = _issue(db_session, solution_a)
    db_session.commit()

    service = SolutionIssueService()
    with pytest.raises(ValueError, match="not found"):
        service.escalate_issue(
            issue_id=issue.id, escalated_to_id=mine.id,
            escalation_reason="urgent", organization_id=org_a.id,
            solution_id=other_solution_a.id,
        )

    refreshed = SolutionIssue.query.get(issue.id)
    assert refreshed.escalated_to_id is None


def test_resolve_issue_refused_when_issue_belongs_to_a_different_solution(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    other_solution_a = _solution(db_session, org_a, "Org A other solution")
    db_session.commit()

    login_as(client, mine)
    create_resp = _create_issue(client, solution_a.id)
    assert create_resp.status_code == 201, create_resp.get_json()
    issue_id = create_resp.get_json()["id"]

    resp = client.post(
        f"/api/solutions/{other_solution_a.id}/issues/{issue_id}/resolve",
        json={"resolution_notes": "done"},
    )

    assert resp.status_code == 400, resp.get_json()
    assert "not found" in resp.get_json()["error"].lower(), resp.get_json()

    from app.models.solution_governance import SolutionIssue

    refreshed = SolutionIssue.query.get(issue_id)
    assert refreshed.status == "open"


# -- kanban card free-text assignee: refused for another organisation's user ----------


def test_create_task_refuses_assignee_of_another_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)

    login_as(client, mine)
    resp = client.post(
        "/api/adm-kanban/v2/cards",
        json={"title": "Card", "phase": phase.code, "assignee": theirs.id},
    )

    assert resp.status_code == 400, resp.get_json()
    assert resp.get_json()["success"] is False

    from app.models.adm_kanban import KanbanCard

    assert KanbanCard.query.filter(KanbanCard.board_id == board.id).count() == 0


def test_create_task_refusal_leaves_no_auto_created_board(app, db_session, client, login_as):
    """A caller with no board yet gets one auto-created by create_task. A
    refused assignee must not leave that board behind (the audit decorator
    commits after the view returns, refusals included)."""
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    newcomer = _user(db_session, org_a, "Nel", "New")
    db_session.commit()

    login_as(client, newcomer)
    resp = client.post(
        "/api/adm-kanban/v2/cards",
        json={"title": "Card", "phase": phase.code, "assignee": theirs.id},
    )

    assert resp.status_code == 400, resp.get_json()

    from app.models.adm_kanban import KanbanBoard

    db_session.expire_all()
    assert KanbanBoard.query.filter(KanbanBoard.created_by_id == newcomer.id).count() == 0


def test_create_task_accepts_assignee_of_the_callers_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)

    login_as(client, mine)
    resp = client.post(
        "/api/adm-kanban/v2/cards",
        json={"title": "Card", "phase": phase.code, "assignee": mine.id},
    )

    assert resp.status_code == 201, resp.get_json()
    assert str(resp.get_json()["card"]["assignee"]) == str(mine.id)


def test_update_task_refuses_assignee_of_another_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    card = _card(db_session, board, phase, mine)
    db_session.commit()

    login_as(client, mine)
    resp = client.patch(
        f"/api/adm-kanban/v2/cards/task:{card.id}",
        json={"assignee": theirs.id},
    )

    assert resp.status_code == 400, resp.get_json()

    from app.models.adm_kanban import KanbanCard

    refreshed = KanbanCard.query.get(card.id)
    assert refreshed.assignee is None


def test_update_task_accepts_assignee_of_the_callers_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    card = _card(db_session, board, phase, mine)
    db_session.commit()

    login_as(client, mine)
    resp = client.patch(
        f"/api/adm-kanban/v2/cards/task:{card.id}",
        json={"assignee": mine.id},
    )

    assert resp.status_code == 200, resp.get_json()

    from app.models.adm_kanban import KanbanCard

    refreshed = KanbanCard.query.get(card.id)
    assert str(refreshed.assignee) == str(mine.id)


# -- a stored foreign assignee (written before the check) does not block editing ------
#
# Both edit forms send the stored assignee back unchanged on every save (the
# card drawer's PATCH carries `assignee: card.assignee`; the board edit form's
# PUT carries `assigned_to_id`). A card written before the organisation check
# can hold another organisation's user id, and re-validating that unchanged
# value refused every edit of the card.


def test_update_task_with_unchanged_foreign_assignee_saves_other_fields(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    card = _card(db_session, board, phase, mine, assignee=str(theirs.id))
    db_session.commit()

    login_as(client, mine)
    resp = client.patch(
        f"/api/adm-kanban/v2/cards/task:{card.id}",
        json={"title": "Renamed", "assignee": str(theirs.id)},
    )

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["card"]["title"] == "Renamed"
    # The foreign user is still not named anywhere in the response.
    assert body["card"]["assignee_label"] == ""
    assert theirs.first_name not in str(body)
    assert theirs.last_name not in str(body)

    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    refreshed = KanbanCard.query.get(card.id)
    assert refreshed.title == "Renamed"
    assert refreshed.assignee == str(theirs.id)


def test_update_task_still_refuses_changing_to_a_different_foreign_assignee(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    other_foreign = _user(db_session, org_b, "Oli", "Other")
    card = _card(db_session, board, phase, mine, assignee=str(theirs.id))
    db_session.commit()

    login_as(client, mine)
    resp = client.patch(
        f"/api/adm-kanban/v2/cards/task:{card.id}",
        json={"title": "Renamed", "assignee": other_foreign.id},
    )

    assert resp.status_code == 400, resp.get_json()

    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    refreshed = KanbanCard.query.get(card.id)
    assert refreshed.title == "Card"
    assert refreshed.assignee == str(theirs.id)


def test_update_task_can_clear_a_stored_foreign_assignee(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    card = _card(db_session, board, phase, mine, assignee=str(theirs.id))
    db_session.commit()

    login_as(client, mine)
    resp = client.patch(
        f"/api/adm-kanban/v2/cards/task:{card.id}",
        json={"assignee": None},
    )

    assert resp.status_code == 200, resp.get_json()

    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    assert KanbanCard.query.get(card.id).assignee is None


def test_update_card_with_unchanged_foreign_assigned_to_id_saves_other_fields(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    card = _card(db_session, board, phase, mine, assigned_to_id=theirs.id)
    db_session.commit()

    login_as(client, mine)
    resp = client.put(
        f"/api/adm-kanban/cards/{card.id}",
        json={"title": "Renamed", "assigned_to_id": theirs.id},
    )

    assert resp.status_code == 200, resp.get_json()
    assert theirs.first_name not in str(resp.get_json())

    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    refreshed = KanbanCard.query.get(card.id)
    assert refreshed.title == "Renamed"
    assert refreshed.assigned_to_id == theirs.id

    # The board API still shows no name for the stored foreign assignee.
    board_resp = client.get(f"/api/adm-kanban/boards/{board.id}")
    assert board_resp.status_code == 200, board_resp.get_json()
    board_body = board_resp.get_json()
    assert theirs.first_name not in str(board_body)
    cards = [c for phase_cards in board_body["data"]["cards_by_phase"].values() for c in phase_cards]
    edited = next(c for c in cards if c["id"] == card.id)
    assert edited["assigned_to"] is None, edited


def test_update_card_still_refuses_changing_to_a_different_foreign_assigned_to_id(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    other_foreign = _user(db_session, org_b, "Oli", "Other")
    card = _card(db_session, board, phase, mine, assigned_to_id=theirs.id)
    db_session.commit()

    login_as(client, mine)
    resp = client.put(
        f"/api/adm-kanban/cards/{card.id}",
        json={"title": "Renamed", "assigned_to_id": other_foreign.id},
    )

    assert resp.status_code == 400, resp.get_json()

    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    refreshed = KanbanCard.query.get(card.id)
    assert refreshed.title == "Card"
    assert refreshed.assigned_to_id == theirs.id


# -- a refused edit changes nothing -------------------------------------------------
#
# The audit decorator on both update routes commits the session after the view
# returns, refusals included, so every refusal has to run before any field is
# set on the card.


def test_update_task_refused_for_empty_title_leaves_the_card_unchanged(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    card = _card(db_session, board, phase, mine)
    card.description = "original"
    db_session.commit()

    login_as(client, mine)
    resp = client.patch(
        f"/api/adm-kanban/v2/cards/task:{card.id}",
        json={"description": "changed", "priority": "critical", "title": "   "},
    )

    assert resp.status_code == 400, resp.get_json()

    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    refreshed = KanbanCard.query.get(card.id)
    assert refreshed.title == "Card"
    assert refreshed.description == "original"
    assert refreshed.priority != "critical"


def test_update_card_refused_for_phase_change_leaves_the_card_unchanged(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    other_phase = _phase(db_session)
    card = _card(db_session, board, phase, mine)
    board.current_adm_phase = phase.code
    db_session.commit()

    login_as(client, mine)
    resp = client.put(
        f"/api/adm-kanban/cards/{card.id}",
        json={"title": "Renamed", "priority": "critical", "adm_phase_id": other_phase.id},
    )

    assert resp.status_code == 400, resp.get_json()
    assert "scoped" in resp.get_json()["error"], resp.get_json()

    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    refreshed = KanbanCard.query.get(card.id)
    assert refreshed.title == "Card"
    assert refreshed.priority != "critical"
    assert refreshed.adm_phase_id == phase.id


def test_update_card_accepts_an_allowed_phase_change_with_other_fields(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    card = _card(db_session, board, phase, mine)
    db_session.commit()

    login_as(client, mine)
    resp = client.put(
        f"/api/adm-kanban/cards/{card.id}",
        json={"title": "Renamed", "adm_phase_id": phase.id},
    )

    assert resp.status_code == 200, resp.get_json()

    from app.models.adm_kanban import KanbanCard

    db_session.expire_all()
    refreshed = KanbanCard.query.get(card.id)
    assert refreshed.title == "Renamed"
    assert refreshed.adm_phase_id == phase.id


# -- kanban board API: batched assignee resolution stays fail-closed per card ---------


def test_board_api_batched_lookup_names_only_the_same_organisation_assignee(app, db_session, client, login_as):
    """Two cards on one board: one assigned to a user of the card's own
    organisation, one assigned to another organisation's user. After the
    batched lookup that replaced the per-card user_in_org query in get_board,
    the first card is named and the second shows no assignee."""
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    own_card = _card(db_session, board, phase, mine, assigned_to_id=mine.id)
    foreign_card = _card(db_session, board, phase, mine, assigned_to_id=theirs.id)
    db_session.commit()

    login_as(client, mine)
    resp = client.get(f"/api/adm-kanban/boards/{board.id}")

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    cards = [c for phase_cards in body["data"]["cards_by_phase"].values() for c in phase_cards]

    own = next(c for c in cards if c["id"] == own_card.id)
    foreign = next(c for c in cards if c["id"] == foreign_card.id)

    assert own["assigned_to"] is not None and own["assigned_to"]["id"] == mine.id, own
    assert foreign["assigned_to"] is None, foreign


def test_get_board_does_not_cost_one_users_query_per_assigned_card(app, db_session, client, login_as):
    """get_board used to run one user_in_org query per assigned card. A board
    with ten assigned cards must issue no more organisation-filtered `users`
    queries than the same board with one — that is the N+1 this fix removed."""
    from sqlalchemy import event

    from app import db

    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    _card(db_session, board, phase, mine, assigned_to_id=mine.id)
    db_session.commit()

    login_as(client, mine)

    def _is_assignee_lookup(statement):
        """A `users` read whose WHERE clause carries an organization_id
        predicate — the shape of both user_in_org's per-card query and its
        batched replacement — as opposed to flask-login's per-request user
        loader, whose WHERE clause is bare `users.id = ...` (organization_id
        only appears there as a SELECTed column, not a predicate)."""
        lowered = statement.lower()
        if "from users" not in lowered or "where" not in lowered:
            return False
        return "organization_id" in lowered.split("where", 1)[1]

    def _count_users_queries(path):
        statements = []

        def _record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        engine = db.engine
        event.listen(engine, "before_cursor_execute", _record)
        try:
            resp = client.get(path)
        finally:
            event.remove(engine, "before_cursor_execute", _record)
        assert resp.status_code == 200, resp.get_json()
        return sum(1 for s in statements if _is_assignee_lookup(s))

    board_path = f"/api/adm-kanban/boards/{board.id}"
    client.get(board_path)  # warm-up, discarded — see test_query_budget.py
    few = _count_users_queries(board_path)

    for _ in range(9):
        _card(db_session, board, phase, mine, assigned_to_id=mine.id)
    db_session.commit()

    many = _count_users_queries(board_path)

    assert many <= few, (few, many, "one users query per assigned card: the N+1 is back")


# -- CLI: clear-foreign-assignees ------------------------------------------------------


def test_clear_foreign_assignees_helpers_count_and_clear_only_foreign_ids(app, db_session):
    from app.commands.clear_foreign_assignees import (
        _clear_foreign_assignees,
        _foreign_assignee_counts,
    )
    from app.models.adm_kanban import KanbanCard
    from app.models.solution_governance import SolutionIssue

    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    foreign_card = _card(db_session, board, phase, mine, assigned_to_id=theirs.id)
    own_card = _card(db_session, board, phase, mine, assigned_to_id=mine.id)
    foreign_issue = _issue(db_session, solution_a, assigned_to_id=theirs.id)
    own_issue = _issue(db_session, solution_a, assigned_to_id=mine.id)
    db_session.commit()

    conn = db_session.connection()
    before = _foreign_assignee_counts(conn)
    assert before["kanban_cards.assigned_to_id"] >= 1
    assert before["solution_issues.assigned_to_id"] >= 1

    _clear_foreign_assignees(conn)
    db_session.commit()

    after = _foreign_assignee_counts(conn)
    assert after["kanban_cards.assigned_to_id"] == 0
    assert after["solution_issues.assigned_to_id"] == 0

    db_session.expire_all()
    assert KanbanCard.query.get(foreign_card.id).assigned_to_id is None
    assert KanbanCard.query.get(own_card.id).assigned_to_id == mine.id
    assert SolutionIssue.query.get(foreign_issue.id).assigned_to_id is None
    assert SolutionIssue.query.get(own_issue.id).assigned_to_id == mine.id


def test_clear_foreign_assignees_command_is_registered(app):
    assert "clear-foreign-assignees" in app.cli.commands


def _invoke_clear_command(app, *args):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["clear-foreign-assignees", *args])
    assert result.exit_code == 0, (result.output, result.exception)
    return result.output


def _foreign_rows(db_session):
    """A kanban card and a solution issue each assigned to another
    organisation's user (the issue also escalated to that user), alongside a
    same-organisation pair, plus a foreign id in a column the command does not
    own (the card's created_by_id)."""
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)
    foreign_card = _card(db_session, board, phase, theirs, assigned_to_id=theirs.id)
    own_card = _card(db_session, board, phase, mine, assigned_to_id=mine.id)
    foreign_issue = _issue(db_session, solution_a, assigned_to_id=theirs.id)
    foreign_issue.escalated_to_id = theirs.id
    own_issue = _issue(db_session, solution_a, assigned_to_id=mine.id)
    own_issue.escalated_to_id = mine.id
    db_session.commit()
    return mine, theirs, foreign_card, own_card, foreign_issue, own_issue


def test_clear_foreign_assignees_command_without_apply_reports_and_changes_nothing(app, db_session):
    from app.models.adm_kanban import KanbanCard
    from app.models.solution_governance import SolutionIssue

    mine, theirs, foreign_card, own_card, foreign_issue, own_issue = _foreign_rows(db_session)

    output = _invoke_clear_command(app)

    assert "Dry run" in output, output
    assert "kanban_cards.assigned_to_id:" in output, output
    assert "solution_issues.assigned_to_id:" in output, output
    assert "solution_issues.escalated_to_id:" in output, output
    assert "after clearing" not in output, output

    db_session.expire_all()
    assert KanbanCard.query.get(foreign_card.id).assigned_to_id == theirs.id
    assert KanbanCard.query.get(own_card.id).assigned_to_id == mine.id
    assert SolutionIssue.query.get(foreign_issue.id).assigned_to_id == theirs.id
    assert SolutionIssue.query.get(foreign_issue.id).escalated_to_id == theirs.id
    assert SolutionIssue.query.get(own_issue.id).assigned_to_id == mine.id
    assert SolutionIssue.query.get(own_issue.id).escalated_to_id == mine.id


def test_clear_foreign_assignees_command_with_apply_clears_only_foreign_user_ids(app, db_session):
    from app.models.adm_kanban import KanbanCard
    from app.models.solution_governance import SolutionIssue

    mine, theirs, foreign_card, own_card, foreign_issue, own_issue = _foreign_rows(db_session)

    output = _invoke_clear_command(app, "--apply")

    assert "Dry run" not in output, output
    for name in (
        "kanban_cards.assigned_to_id",
        "solution_issues.assigned_to_id",
        "solution_issues.escalated_to_id",
    ):
        assert f"{name}: 0 row(s) naming another organisation's user after clearing" in output, output

    db_session.expire_all()
    cleared_card = KanbanCard.query.get(foreign_card.id)
    cleared_issue = SolutionIssue.query.get(foreign_issue.id)
    kept_issue = SolutionIssue.query.get(own_issue.id)
    # Foreign assignee and escalation ids are cleared ...
    assert cleared_card.assigned_to_id is None
    assert cleared_issue.assigned_to_id is None
    assert cleared_issue.escalated_to_id is None
    # ... same-organisation ones are kept ...
    assert KanbanCard.query.get(own_card.id).assigned_to_id == mine.id
    assert kept_issue.assigned_to_id == mine.id
    assert kept_issue.escalated_to_id == mine.id
    # ... and a column the command does not own is untouched, even where it
    # also holds another organisation's user id.
    assert cleared_card.created_by_id == theirs.id
