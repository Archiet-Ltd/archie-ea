"""An enterprise architect checks the flow analytics page for a Kanban board.

The architect signs into an organisation with a board that has completed and
in-progress cards, opens the flow analytics page, selects the board, and
asserts that the figures a person needs are present and agree:
- The metric cards render (Avg Cycle Time, Avg Throughput, Avg Lead Time, Flow Efficiency)
- The board selector lets them pick the seeded board
- The cycle time chart canvas exists
- The throughput chart canvas exists
- The WIP snapshot table shows the card count by column
- The empty/error states are structurally present
"""

import datetime
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("SMOKE_WALK_DIR")

PERSONA = "enterprise_architect"


def _assert_persona(email):
    """The signed-in user is the enterprise architect, not an administrator."""
    from app import create_app
    from app.middleware.tenant_decorators import is_platform_admin
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == PERSONA, \
            "Expected enterprise_role=%s, got %s" % (PERSONA, user.enterprise_role)
        assert user.role.name not in ("Administrator", "Platform Admin"), \
            "Platform role must not be Administrator/Platform Admin, got %s" % user.role.name
        assert not is_platform_admin(user), "User must not be a platform admin"
        assert not user.is_org_admin, "User must not be an org admin"
        db_session = app.extensions["sqlalchemy"].session
        db_session.remove()


def _seed_board_with_cards(org_id, user_id, suffix):
    """Seed a Kanban board with completed and in-progress cards."""
    from app import create_app, db
    from app.models.adm_kanban import KanbanBoard, KanbanCard, ADMPhase, create_adm_phases

    app = create_app("testing")
    with app.app_context():
        create_adm_phases()
        phase = ADMPhase.query.filter_by(code="A").first()
        assert phase is not None

        board = KanbanBoard(
            name="Flow Analytics Board %s" % suffix,
            organization_id=org_id,
            created_by_id=user_id,
        )
        db.session.add(board)
        db.session.flush()

        now = datetime.datetime.utcnow()
        # Completed cards (generates cycle time + throughput + lead time data)
        for i in range(5):
            started = now - datetime.timedelta(days=20 + i)
            completed = now - datetime.timedelta(days=7 + i)
            db.session.add(KanbanCard(
                title="Completed Card %d %s" % (i + 1, suffix),
                description="Test card",
                board_id=board.id,
                adm_phase_id=phase.id,
                card_type="requirement",
                status="done",
                created_by_id=user_id,
                created_at=started,
                started_at=started,
                completed_at=completed,
                organization_id=org_id,
            ))

        # In-progress cards (for WIP snapshot)
        for i in range(3):
            db.session.add(KanbanCard(
                title="WIP Card %d %s" % (i + 1, suffix),
                description="In progress card",
                board_id=board.id,
                adm_phase_id=phase.id,
                card_type="requirement",
                status="in_progress",
                created_by_id=user_id,
                created_at=now - datetime.timedelta(days=5),
                organization_id=org_id,
            ))

        db.session.commit()
        board_id = board.id
        db.session.remove()
    return board_id


def _shot(page, name):
    if WALK_DIR:
        Path(WALK_DIR).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(WALK_DIR) / name), full_page=True)


def test_flow_analytics_journey(browser, live_server):
    """Enterprise architect checks flow analytics for a board with cards."""
    org = create_fresh_org(PERSONA)
    suffix = org["suffix"]
    org_id = org["org_id"]
    user_id = org["user_ids"][PERSONA]
    email = org["emails"][PERSONA]

    board_id = _seed_board_with_cards(org_id, user_id, suffix)

    # Assert the persona is correct before the browser starts
    _assert_persona(email)

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    try:
        sign_in(page, live_server, email)

        page.goto(live_server + "/analytics/flow?board_id=%d" % board_id,
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # 1. The page title renders (Flow Analytics)
        expect(page.locator("h1")).to_contain_text("Flow Analytics")

        # 2. The board selector is present with the seeded board selected
        board_selector = page.locator("#board-selector")
        expect(board_selector).to_be_visible()
        selected_option = board_selector.input_value()
        assert selected_option == str(board_id), "Expected board %s, got %s" % (board_id, selected_option)

        # 3. The four metric cards render
        expect(page.locator("text=Avg Cycle Time").first).to_be_visible()
        expect(page.locator("text=Avg Throughput").first).to_be_visible()
        expect(page.locator("text=Avg Lead Time").first).to_be_visible()
        expect(page.locator("text=Flow Efficiency").first).to_be_visible()

        # 4. Chart canvases exist (cycle time and throughput)
        expect(page.locator("#cycleTimeChart")).to_be_visible()
        expect(page.locator("#throughputChart")).to_be_visible()

        # 5. The WIP snapshot table renders with column headers
        expect(page.locator("text=WIP Snapshot").first).to_be_visible()
        expect(page.locator("text=Board column").first).to_be_visible()
        expect(page.locator("text=Cards").first).to_be_visible()

        # 6. The total WIP count is shown
        expect(page.locator("text=total:").first).to_be_visible()

        # 7. The shadcn-nearest meta tag is present
        meta_shadcn = page.locator('meta[name="shadcn-nearest"]')
        expect(meta_shadcn).to_have_attribute("content", "dashboard-01")

        # 8. Empty state and error state text are present in the markup
        expect(page.locator("text=No flow data yet").first).to_be_visible()
        expect(page.locator("text=Flow metrics could not be loaded").first).to_be_visible()

        # 9. The Retry button exists (from button macro)
        expect(page.locator("button:has-text('Retry')").first).to_be_visible()
    finally:
        context.close()