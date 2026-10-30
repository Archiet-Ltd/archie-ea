"""The Gap Analysis view (`/adm-kanban/gap-analysis`) scopes both the Gap cards
and the WorkPackage cards that close them to the caller's organisation.

Round c of fix-round list-table-b11e added an `organization_id` predicate to
the closers query but shipped it with a Python indentation error that broke
the whole blueprint, so the fix itself was never exercised. This is the
nearest existing coverage of that query: the assignee tenant-fence tests in
`tests/test_kanban_assignee_tenant_fence.py` cover a different field on the
same model, not this route.
"""

from __future__ import annotations

import uuid


def _org(db_session, label):
    from app.models.organization import Organization

    suffix = uuid.uuid4().hex[:8]
    org = Organization(name=f"Gap Fence {label} {suffix}", slug=f"gap-fence-{label}-{suffix}")
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
        name=f"Board {uuid.uuid4().hex[:6]}", board_type="architecture_development",
        created_by_id=creator.id, organization_id=org.id,
    )
    db_session.add(board)
    db_session.flush()
    return board


def _gap(db_session, board, phase, creator, title):
    from app.models.adm_kanban import KanbanCard

    card = KanbanCard(
        title=title, adm_phase_id=phase.id, board_id=board.id,
        card_type="gap", arch_element_type="Gap", status="todo",
        created_by_id=creator.id, organization_id=board.organization_id,
    )
    db_session.add(card)
    db_session.flush()
    return card


def _closer(db_session, board, phase, creator, title, closes_gap_id):
    from app.models.adm_kanban import KanbanCard

    card = KanbanCard(
        title=title, adm_phase_id=phase.id, board_id=board.id,
        card_type="work_package", status="todo",
        closes_gap_id=closes_gap_id,
        created_by_id=creator.id, organization_id=board.organization_id,
    )
    db_session.add(card)
    db_session.flush()
    return card


def test_gap_analysis_view_excludes_another_organisations_gaps_and_closers(app, db_session, client, login_as):
    """An enterprise architect at org A opens Gap Analysis and never sees org
    B's gap, nor the WorkPackage card org B uses to close it -- the two rows
    the closers query, unscoped, would have leaked across the tenant fence."""
    org_a = _org(db_session, "a")
    org_b = _org(db_session, "b")
    mine = _user(db_session, org_a, "Ada", "Mine")
    theirs = _user(db_session, org_b, "Vic", "Tim")
    phase = _phase(db_session)
    board_a = _board(db_session, org_a, mine)
    board_b = _board(db_session, org_b, theirs)
    db_session.commit()

    _gap(db_session, board_a, phase, mine, "Org A Gap Unique Title")
    gap_b = _gap(db_session, board_b, phase, theirs, "Org B Gap Unique Title")
    _closer(db_session, board_b, phase, theirs, "Org B Closing WorkPackage", closes_gap_id=gap_b.id)
    db_session.commit()

    login_as(client, mine)
    resp = client.get("/adm-kanban/gap-analysis")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Org A Gap Unique Title" in body
    assert "Org B Gap Unique Title" not in body
    assert "Org B Closing WorkPackage" not in body


def test_gap_analysis_view_shows_my_own_closer_for_my_own_gap(app, db_session, client, login_as):
    """The organisation filter narrows the closers query; it must not also
    hide a caller's own closing WorkPackage for their own gap."""
    org_a = _org(db_session, "a")
    mine = _user(db_session, org_a, "Ada", "Mine")
    phase = _phase(db_session)
    board_a = _board(db_session, org_a, mine)
    db_session.commit()

    gap_a = _gap(db_session, board_a, phase, mine, "Org A Owned Gap")
    _closer(db_session, board_a, phase, mine, "Org A Owned Closer", closes_gap_id=gap_a.id)
    db_session.commit()

    login_as(client, mine)
    resp = client.get("/adm-kanban/gap-analysis")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Org A Owned Gap" in body
    assert "Org A Owned Closer" in body
