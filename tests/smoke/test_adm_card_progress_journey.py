"""An architect edits a task whose progress nobody has recorded, and it is not turned into 0%.

The goal, as the Enterprise Architect: "rename a task on the board without being told it is 0% done".
The demonstration organisation's board gets one task with no progress figure. The architect opens it,
presses Edit, sees the progress line say "Not recorded", changes the title, saves, reloads and finds
the task under its new title still without a progress figure. Then a figure is set with the slider and
it is kept after a reload.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_adm_board_requirements_journey import (  # noqa: F401  (the demonstration organisation and its architect)
    BOARD, _assert_signed_in_as_enterprise_architect, _open, _shot, demo_architect,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture
def unmeasured_task(live_server, demo_architect):
    from app import create_app, db
    from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard
    from app.models.user import User

    title = "Unmeasured task " + uuid.uuid4().hex[:6]
    with create_app("testing").app_context():
        user = User.query.filter_by(email=demo_architect).one()
        board = KanbanBoard.query.filter_by(organization_id=user.organization_id).first()
        if board is None:
            board = KanbanBoard(name="ADM Board", board_type="architecture_development",
                                created_by_id=user.id, organization_id=user.organization_id)
            db.session.add(board)
            db.session.flush()
        phase = ADMPhase.query.order_by(ADMPhase.order).first()
        if phase is None:
            phase = ADMPhase(name="Preliminary", code="PRE", order=1)
            db.session.add(phase)
            db.session.flush()
        card = KanbanCard(title=title, adm_phase_id=phase.id, board_id=board.id, card_type="task", status="todo",
                          created_by_id=user.id, organization_id=user.organization_id)
        db.session.add(card)
        db.session.commit()
        db.session.execute(db.text("UPDATE kanban_cards SET progress_pct = NULL WHERE id = :id"), {"id": card.id})
        db.session.commit()
        card_id = card.id
    yield {"id": card_id, "title": title}
    with create_app("testing").app_context():
        db.session.execute(db.text("DELETE FROM kanban_cards WHERE id = :id"), {"id": card_id})
        db.session.commit()


def _stored(card_id):
    from app import create_app, db

    with create_app("testing").app_context():
        row = db.session.execute(db.text("SELECT title, progress_pct FROM kanban_cards WHERE id = :id"),
                                 {"id": card_id}).one()
        return row[0], row[1]


def _line(page):
    return page.locator("label:text-is('Progress') + span")


def _slider(page):
    return page.get_by_role("slider", name="Progress", exact=True)


def _open_edit(page, title):
    page.locator('[data-testid="adm-card"]').filter(has_text=title).first.get_by_text(title).click()
    page.get_by_role("button", name="Edit", exact=True).click()
    expect(_slider(page)).to_be_visible(timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("width", [1440, 390])
def test_saving_a_task_with_no_progress_keeps_it_unrecorded(browser, live_server, demo_architect, unmeasured_task, width):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, width)
    try:
        title = unmeasured_task["title"]
        _open_edit(page, title)
        expect(_line(page)).to_have_text("Not recorded")
        _shot(page, "progress-unrecorded", width)

        renamed = title + " renamed"
        page.get_by_label("Task title", exact=True).fill(renamed)
        with page.expect_response(re.compile(r"/api/adm-kanban/v2/cards/task:\d+")) as saved:
            page.get_by_role("button", name="Save", exact=True).click()
        assert saved.value.status == 200
        assert _stored(unmeasured_task["id"]) == (renamed, None)

        page.reload()
        page.wait_for_load_state("networkidle")
        _open_edit(page, renamed)
        expect(_line(page)
               ).to_have_text("Not recorded")
        assert "0%" not in _line(page).inner_text()

        _slider(page).fill("60")
        expect(_line(page)
               ).to_have_text("60%")
        with page.expect_response(re.compile(r"/api/adm-kanban/v2/cards/task:\d+")) as saved:
            page.get_by_role("button", name="Save", exact=True).click()
        assert saved.value.status == 200
        assert _stored(unmeasured_task["id"])[1] == 60
    finally:
        context.close()
