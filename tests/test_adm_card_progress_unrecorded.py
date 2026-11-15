"""A card with no recorded progress can be edited and saved, and stays without a progress figure.

The edit sheet sends `progress_pct` back with every save. A card nobody has measured carries no
progress; saving it must keep it that way (not 0%, not a server error), a typed figure is stored, and
a figure outside 0 to 100 is refused.
"""

from __future__ import annotations

import uuid

import pytest

from app import db


def _world(db_session):
    from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard
    from app.models.organization import Organization
    from app.models.user import User

    rows = {}
    for label in ("a", "b"):
        suffix = uuid.uuid4().hex[:8]
        org = Organization(name=f"Progress {label} {suffix}", slug=f"progress-{label}-{suffix}")
        db_session.add(org)
        db_session.flush()
        user = User(first_name="Pat", last_name=label.upper(), email=f"pat-{label}-{suffix}@example.test",
                    password="test-password-123", confirmed=True, organization_id=org.id)
        db_session.add(user)
        db_session.flush()
        board = KanbanBoard(name="Board", board_type="architecture_development",
                            created_by_id=user.id, organization_id=org.id)
        db_session.add(board)
        db_session.flush()
        rows[label] = (org, user, board)
    code = uuid.uuid4().hex[:8].upper()
    phase = ADMPhase(name=f"Phase {code}", code=code, order=1)
    db_session.add(phase)
    db_session.flush()
    cards = {}
    for label, (org, user, board) in rows.items():
        card = KanbanCard(title=f"Card {label}", adm_phase_id=phase.id, board_id=board.id, card_type="task",
                          status="todo", created_by_id=user.id, organization_id=org.id, progress_pct=None)
        db_session.add(card)
        db_session.flush()
        cards[label] = card
    db_session.commit()
    # The column's own default fills a new row with 0; these cards are ones nobody has measured.
    for card in cards.values():
        db_session.execute(db.text("UPDATE kanban_cards SET progress_pct = NULL WHERE id = :id"), {"id": card.id})
    db_session.commit()
    ids = {label: (rows[label][1].id, cards[label].id) for label in rows}
    # Per request code runs on a fresh session: drop the identity map so the card is loaded, and fenced, as it is there.
    db_session.expunge_all()
    return ids


def _patch(client, login_as, user_id, card_id, body):
    db.session.expunge_all()
    login_as(client, user_id)
    return client.patch(f"/api/adm-kanban/v2/cards/task:{card_id}", json=body)


@pytest.mark.parametrize("sent", ["", None])
def test_saving_a_card_with_no_progress_keeps_it_unrecorded(app, db_session, client, login_as, sent):
    from app.models.adm_kanban import KanbanCard

    ids = _world(db_session)
    resp = _patch(client, login_as, *ids["a"], {"title": "Renamed", "progress_pct": sent})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["card"]["progress_pct"] is None
    db_session.expire_all()
    assert db.session.get(KanbanCard, ids["a"][1]).progress_pct is None


def test_a_typed_progress_is_stored_and_a_measured_zero_stays_zero(app, db_session, client, login_as):
    from app.models.adm_kanban import KanbanCard

    ids = _world(db_session)
    assert _patch(client, login_as, *ids["a"], {"progress_pct": 40}).status_code == 200
    db_session.expire_all()
    assert db.session.get(KanbanCard, ids["a"][1]).progress_pct == 40
    assert _patch(client, login_as, *ids["a"], {"progress_pct": 0}).status_code == 200
    db_session.expire_all()
    assert db.session.get(KanbanCard, ids["a"][1]).progress_pct == 0


@pytest.mark.parametrize("bad", ["lots", 140, -1])
def test_a_progress_that_is_not_a_percentage_is_refused(app, db_session, client, login_as, bad):
    ids = _world(db_session)
    resp = _patch(client, login_as, *ids["a"], {"progress_pct": bad})
    assert resp.status_code == 400
    assert "0 to 100" in resp.get_json()["error"]


def test_another_organisations_card_is_not_changed(app, db_session, client, login_as):
    from app.models.adm_kanban import KanbanCard

    ids = _world(db_session)
    resp = _patch(client, login_as, ids["a"][0], ids["b"][1], {"progress_pct": 90})
    assert resp.status_code in (403, 404)
    db_session.expire_all()
    stored = db.session.execute(db.text("SELECT progress_pct FROM kanban_cards WHERE id = :id"),
                                {"id": ids["b"][1]}).scalar_one()  # tenancy-ok: reading back the other organisation's row
    assert stored is None
