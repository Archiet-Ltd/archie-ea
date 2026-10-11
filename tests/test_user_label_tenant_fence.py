"""A user resolved from a request-supplied id is named only inside the caller's organisation.

``User`` has an ``organization_id`` column but no ``TenantMixin``, so the ORM filter does not
apply to it. Two places named a user from an id the request could set, whichever organisation
the user belonged to: the kanban card assignee label and the approval-condition owner name.
Before the fix both returned another organisation's user's full name.
"""

from __future__ import annotations

import uuid


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Label {label} {suffix}", slug=f"label-{label}-{suffix}")
    db_session.add(org)
    db_session.flush()
    return org


def _user(db_session, org, first, last):
    from app.models.user import User

    user = User(
        first_name=first, last_name=last, email=f"{first.lower()}-{uuid.uuid4().hex[:6]}@example.test",
        password="test-password-123", confirmed=True, organization_id=org.id,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _world(db_session):
    org_a, org_b = _org(db_session, "a"), _org(db_session, "b")
    mine = _user(db_session, org_a, "Mia", "Mine")
    theirs = _user(db_session, org_b, "Vic", "Tim")
    db_session.commit()
    return org_a, org_b, mine, theirs


# -- the shared helper ---------------------------------------------------------------


def test_user_in_org_returns_only_a_user_of_that_organisation(app, db_session):
    from app.utils.tenant_users import user_in_org

    org_a, org_b, mine, theirs = _world(db_session)

    assert user_in_org(mine.id, org_a.id).id == mine.id
    assert user_in_org(theirs.id, org_a.id) is None
    assert user_in_org(theirs.id, org_b.id).id == theirs.id


def test_user_in_org_fails_closed_on_bad_input(app, db_session):
    from app.utils.tenant_users import user_in_org

    org_a, _org_b, mine, _theirs = _world(db_session)

    assert user_in_org(mine.id, None) is None
    assert user_in_org(None, org_a.id) is None
    assert user_in_org("", org_a.id) is None
    assert user_in_org("not-a-number", org_a.id) is None
    assert user_in_org(str(mine.id), org_a.id).id == mine.id


# -- the two callers -----------------------------------------------------------------


def test_kanban_assignee_label_and_owner_name_only_users_of_the_cards_organisation(app, db_session):
    """Two organisations, a card in organisation A whose assignee and
    assigned_to_id point at a user in organisation B: neither the assignee
    label nor the owner names that user."""
    from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard
    from app.services.kanban_projection_service import KanbanProjectionService

    org_a, org_b, mine, theirs = _world(db_session)

    phase = ADMPhase(name="Test Phase A", code="A", order=1)
    db_session.add(phase)
    db_session.flush()

    board = KanbanBoard(
        name="Test Board", board_type="architecture_development",
        created_by_id=mine.id, organization_id=org_a.id,
    )
    db_session.add(board)
    db_session.flush()

    # Card in org A whose assignee and assigned_to_id point at org B's user
    foreign_card = KanbanCard(
        title="Foreign assignee card", adm_phase_id=phase.id,
        board_id=board.id, card_type="task", status="todo",
        assignee=str(theirs.id), assigned_to_id=theirs.id,
        created_by_id=mine.id, organization_id=org_a.id,
    )
    db_session.add(foreign_card)

    # Card in org A whose assignee and assigned_to_id point at own user
    own_card = KanbanCard(
        title="Own assignee card", adm_phase_id=phase.id,
        board_id=board.id, card_type="task", status="todo",
        assignee=str(mine.id), assigned_to_id=mine.id,
        created_by_id=mine.id, organization_id=org_a.id,
    )
    db_session.add(own_card)
    db_session.commit()

    service = KanbanProjectionService()

    result = service._project_one_kanban_card(foreign_card)
    assert result["assignee_label"] == "", (
        f"Expected empty assignee_label for foreign user, got {result['assignee_label']!r}"
    )
    assert result["owner"] is None, (
        f"Expected None owner for foreign user, got {result['owner']!r}"
    )

    result = service._project_one_kanban_card(own_card)
    assert result["assignee_label"] == "Mia Mine", (
        f"Expected 'Mia Mine' assignee_label for own user, got {result['assignee_label']!r}"
    )
    assert result["owner"] == "Mia Mine", (
        f"Expected 'Mia Mine' owner for own user, got {result['owner']!r}"
    )


def test_condition_owner_name_names_only_a_user_of_the_callers_organisation(app, db_session):
    from app.modules.solutions_strategic.v2.routes.solution_design_routes import _user_display_name

    org_a, org_b, mine, theirs = _world(db_session)

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org_a.id
        assert _user_display_name(mine.id) == "Mia Mine"
        assert _user_display_name(theirs.id) is None

    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = None
        assert _user_display_name(mine.id) is None
