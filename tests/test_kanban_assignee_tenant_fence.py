"""A kanban card's assignee, and a solution issue's assignee written through the
governance API, are named and accepted only inside the caller's organisation.

``KanbanCard.assigned_to_id`` is a plain FK to ``users.id``: the board API named
it by an unscoped ``card.assigned_to`` relationship load, and the create/update
routes wrote it from request input with no organisation check. The kanban
projection service's own read (``owner``) was already fixed by an earlier
change and is re-asserted here as the other surface serialising the same
field. ``SolutionIssue.assigned_to_id``, written through
``SolutionIssueService.assign_issue`` from the governance API, had the same
unchecked write.
"""

from __future__ import annotations

import uuid


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Kanban {label} {suffix}", slug=f"kanban-{label}-{suffix}")
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

    suffix = uuid.uuid4().hex[:8]
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


def _card(db_session, board, phase, creator, *, assigned_to_id=None):
    from app.models.adm_kanban import KanbanCard

    card = KanbanCard(
        title="Card", adm_phase_id=phase.id, board_id=board.id,
        card_type="task", status="todo", assigned_to_id=assigned_to_id,
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


def _world(db_session):
    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    mine = _user(db_session, org_a, "Ada", "Mine")
    theirs = _user(db_session, org_b, "Vic", "Tim")
    phase = _phase(db_session)
    board = _board(db_session, org_a, mine)
    solution_a = _solution(db_session, org_a, "Org A solution")
    db_session.commit()
    return org_a, org_b, mine, theirs, phase, board, solution_a


# -- write side: kanban card create/update refuse a foreign assigned_to_id ------------


def test_create_card_refuses_assigned_to_id_of_another_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, _solution_a = _world(db_session)

    login_as(client, mine)
    resp = client.post(
        f"/api/adm-kanban/boards/{board.id}/cards",
        json={"title": "Card", "adm_phase_id": phase.id, "assigned_to_id": theirs.id},
    )

    assert resp.status_code == 400, resp.get_json()
    assert "error" in resp.get_json()

    from app.models.adm_kanban import KanbanCard

    assert KanbanCard.query.filter(KanbanCard.board_id == board.id).count() == 0


def test_create_card_accepts_assigned_to_id_of_the_callers_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, _solution_a = _world(db_session)

    login_as(client, mine)
    resp = client.post(
        f"/api/adm-kanban/boards/{board.id}/cards",
        json={"title": "Card", "adm_phase_id": phase.id, "assigned_to_id": mine.id},
    )

    assert resp.status_code == 201, resp.get_json()


def test_update_card_refuses_assigned_to_id_of_another_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, _solution_a = _world(db_session)
    card = _card(db_session, board, phase, mine)
    db_session.commit()

    login_as(client, mine)
    resp = client.put(
        f"/api/adm-kanban/cards/{card.id}",
        json={"assigned_to_id": theirs.id},
    )

    assert resp.status_code == 400, resp.get_json()
    assert "error" in resp.get_json()

    from app.models.adm_kanban import KanbanCard

    refreshed = KanbanCard.query.get(card.id)
    assert refreshed.assigned_to_id is None


def test_update_card_accepts_assigned_to_id_of_the_callers_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, _solution_a = _world(db_session)
    card = _card(db_session, board, phase, mine)
    db_session.commit()

    login_as(client, mine)
    resp = client.put(
        f"/api/adm-kanban/cards/{card.id}",
        json={"assigned_to_id": mine.id},
    )

    assert resp.status_code == 200, resp.get_json()


# -- read side: the board API shows no name for a foreign assigned_to_id --------------


def test_board_api_hides_foreign_assignee_name(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, _solution_a = _world(db_session)
    card = _card(db_session, board, phase, mine, assigned_to_id=theirs.id)
    db_session.commit()

    login_as(client, mine)
    resp = client.get(f"/api/adm-kanban/boards/{board.id}")

    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert theirs.first_name not in str(body)
    assert theirs.last_name not in str(body)

    cards = [
        c for phase_cards in body["data"]["cards_by_phase"].values() for c in phase_cards
    ]
    matched = [c for c in cards if c["id"] == card.id]
    assert matched, cards
    assert matched[0]["assigned_to"] is None, matched[0]


def test_kanban_projection_service_hides_foreign_assignee_name(app, db_session):
    """Same rule on the other surface serialising this field (the unified
    card projection used by the transformation-room board view)."""
    from app.services.kanban_projection_service import KanbanProjectionService

    org_a, org_b, mine, theirs, phase, board, _solution_a = _world(db_session)
    card = _card(db_session, board, phase, mine, assigned_to_id=theirs.id)
    db_session.commit()

    result = KanbanProjectionService()._project_one_kanban_card(card)
    assert result["owner"] is None, result


# -- governance API: solution issue assignment refuses a foreign id -------------------


def _create_issue(client, solution_id):
    return client.post(
        f"/api/solutions/{solution_id}/issues/create",
        json={"title": "Ship it", "description": "desc"},
    )


def test_assign_issue_refuses_assigned_to_id_of_another_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)

    login_as(client, mine)
    create_resp = _create_issue(client, solution_a.id)
    assert create_resp.status_code == 201, create_resp.get_json()
    issue_id = create_resp.get_json()["id"]

    resp = client.post(
        f"/api/solutions/{solution_a.id}/issues/{issue_id}/assign",
        json={"assigned_to_id": theirs.id},
    )

    assert resp.status_code == 400, resp.get_json()
    assert "error" in resp.get_json()

    from app.models.solution_governance import SolutionIssue

    refreshed = SolutionIssue.query.get(issue_id)
    assert refreshed.assigned_to_id is None


def test_assign_issue_accepts_assigned_to_id_of_the_callers_organisation(app, db_session, client, login_as):
    org_a, org_b, mine, theirs, phase, board, solution_a = _world(db_session)

    login_as(client, mine)
    create_resp = _create_issue(client, solution_a.id)
    assert create_resp.status_code == 201, create_resp.get_json()
    issue_id = create_resp.get_json()["id"]

    resp = client.post(
        f"/api/solutions/{solution_a.id}/issues/{issue_id}/assign",
        json={"assigned_to_id": mine.id},
    )

    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["assigned_to_id"] == mine.id
