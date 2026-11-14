"""Push to Gantt creates a work package, so a Viewer is refused and a card from
another organisation is not found."""

import pytest

from tests.test_ba_tenant_and_authz import (
    _cleanup_ids,
    _login,
    _make_org_id,
    _make_user_id,
)


@pytest.fixture(scope="module")
def app():
    from app import create_app, db

    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        db.create_all()
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_card(db, org_id, user_id, title):
    from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard

    phase = ADMPhase.query.filter_by(code="A").first()
    if phase is None:
        phase = ADMPhase(name="Architecture Vision", code="A", order=1)
        db.session.add(phase)
        db.session.flush()
    board = KanbanBoard(
        name=f"{title} board", board_type="architecture_development",
        created_by_id=user_id, organization_id=org_id,
    )
    db.session.add(board)
    db.session.flush()
    card = KanbanCard(
        title=title, adm_phase_id=phase.id, board_id=board.id, card_type="task",
        status="todo", created_by_id=user_id, organization_id=org_id,
    )
    db.session.add(card)
    db.session.commit()
    return card.id, board.id


def _count_packages(db, org_id, name):
    from app.models.unified_work_package import UnifiedWorkPackage

    return db.session.query(UnifiedWorkPackage).filter(
        UnifiedWorkPackage.organization_id == org_id, UnifiedWorkPackage.name == name
    ).count()


def _tidy(db, org_ids, user_ids, card_ids, board_ids):
    from app.models.adm_kanban import KanbanBoard, KanbanCard
    from app.models.organization import Organization
    from app.models.unified_work_package import UnifiedWorkPackage
    from app.models.user import User

    for org in org_ids:
        for wp in UnifiedWorkPackage.query.filter_by(organization_id=org).all():
            db.session.delete(wp)
        db.session.commit()
    _cleanup_ids(db, KanbanCard, card_ids)
    _cleanup_ids(db, KanbanBoard, board_ids)
    _cleanup_ids(db, User, user_ids)
    _cleanup_ids(db, Organization, org_ids)


def test_viewer_cannot_push_card_to_gantt(client, app):
    from app import db

    with app.app_context():
        org = _make_org_id(db, "PGV")
        uid = _make_user_id(db, org, "PgViewer", enterprise_role="procurement", role_name="Viewer")
        card, board = _make_card(db, org, uid, "Viewer push card")
    try:
        with app.app_context():
            _login(client, uid)
            r = client.post(f"/api/adm-kanban/v2/cards/task:{card}/push-to-gantt", json={})
            assert r.status_code == 403, r.get_data(as_text=True)
            assert _count_packages(db, org, "Viewer push card") == 0
    finally:
        with app.app_context():
            _tidy(db, [org], [uid], [card], [board])


def test_architect_can_push_card_to_gantt(client, app):
    from app import db

    with app.app_context():
        org = _make_org_id(db, "PGA")
        uid = _make_user_id(db, org, "PgArchitect", enterprise_role="enterprise_architect")
        card, board = _make_card(db, org, uid, "Architect push card")
    try:
        with app.app_context():
            _login(client, uid)
            r = client.post(f"/api/adm-kanban/v2/cards/task:{card}/push-to-gantt", json={})
            assert r.status_code == 201, r.get_data(as_text=True)
            assert _count_packages(db, org, "Architect push card") == 1
    finally:
        with app.app_context():
            _tidy(db, [org], [uid], [card], [board])


def test_other_organisations_card_is_not_found(client, app):
    from app import db

    with app.app_context():
        org_a = _make_org_id(db, "PGX")
        org_b = _make_org_id(db, "PGY")
        arch_a = _make_user_id(db, org_a, "PgArchA", enterprise_role="enterprise_architect")
        arch_b = _make_user_id(db, org_b, "PgArchB", enterprise_role="enterprise_architect")
        card_b, board_b = _make_card(db, org_b, arch_b, "Other org card")
    try:
        with app.app_context():
            _login(client, arch_a)
            r = client.post(f"/api/adm-kanban/v2/cards/task:{card_b}/push-to-gantt", json={})
            assert r.status_code == 404, r.get_data(as_text=True)
            assert _count_packages(db, org_a, "Other org card") == 0
            assert _count_packages(db, org_b, "Other org card") == 0
    finally:
        with app.app_context():
            _tidy(db, [org_a, org_b], [arch_a, arch_b], [card_b], [board_b])
