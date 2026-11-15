"""Journey test: Flow analytics page built from shadcn components.

The journey, as an enterprise architect with a real Kanban board and
completed cards: requests the flow analytics page; asserts the page renders
with shadcn component macros (metrics cards, table, empty state, button,
board selector, card chart containers); and that the signed-in persona is
the enterprise architect, not an administrator.

Fails on main (raw components, raw colours, no data-slot attributes),
passes on the branch (all shadcn macros, zero compliance defects).
"""

import datetime

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey


def _seed_board_and_cards(db, org_id, user_id):
    """Create a Kanban board with completed and in-progress cards."""
    from app.models.adm_kanban import KanbanBoard, KanbanCard, ADMPhase, create_adm_phases

    create_adm_phases()
    phase = ADMPhase.query.filter_by(code="A").first()
    assert phase is not None

    board = KanbanBoard(
        name="Flow Analytics Test Board",
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
            title="Completed Card %d" % (i + 1),
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
            title="WIP Card %d" % (i + 1),
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
    return board.id


def test_flow_analytics_shadcn_journey(app, client):
    """Enterprise architect sees flow analytics page built from shadcn macros.

    The test asserts:
    - The signed-in persona is enterprise_architect, not a platform admin role
    - The shadcn-nearest meta tag exists (Tier B gate B1)
    - Page_shell renders the page title
    - Metrics cards use data-slot="card" (from metrics_card macro)
    - Board selector uses native_select with id="board-selector"
    - WIP table uses the dense-table class (from table macro)
    - Empty and error states exist in the markup
    - Charts use ArchieColorTokens.cssHSL (not raw rgba literals)
    - No raw rgb()/rgba() functions remain in the page content
    - Retry button is present (from button macro)
    """
    from app import db
    from app.models.user import User

    with app.app_context():
        org_id = make_org(db, "Flow Analytics")
        user_id = make_user(db, org_id, "FlowEA", enterprise_role="enterprise_architect")

        # Assert the signed-in persona is NOT a platform/administrator role
        user = db.session.get(User, user_id)
        assert user.enterprise_role == "enterprise_architect"
        assert not user.is_platform_admin

        board_id = _seed_board_and_cards(db, org_id, user_id)

    login(client, user_id)

    response = client.get("/analytics/flow?board_id=%d" % board_id)
    assert response.status_code == 200, "Page should render, got %d" % response.status_code
    body = response.data.decode("utf-8")

    # Tier B gate check: shadcn-nearest meta tag
    assert 'name="shadcn-nearest" content="dashboard-01"' in body
    # Page title from page_shell
    assert "Flow Analytics" in body
    # Metrics cards from metrics_card macro: data-slot="card"
    assert 'data-slot="card"' in body
    assert 'data-slot="card-title"' in body
    # Board selector from native_select macro
    assert 'id="board-selector"' in body
    # WIP table from table macro: dense-table class
    assert "dense-table" in body
    # Empty state text
    assert "No flow data yet" in body
    # ArchieColorTokens resolver used for chart colours
    assert "ArchieColorTokens" in body
    # cssHSL function used for chart colours
    assert "cssHSL" in body
    # Retry button from button macro
    assert "Retry" in body
    # WIP table headers
    assert "Board column" in body
    assert "Cards" in body
    # Page shell renders breadcrumb
    assert "Home" in body
    # Chart canvases exist
    assert "cycleTimeChart" in body
    assert "throughputChart" in body
    # Alpine component initialised
    assert "flowAnalytics" in body
    # No raw rgba() or rgb() literals remain
    import re
    raw_rgb = re.findall(r'\brgba?\(', body)
    assert len(raw_rgb) == 0, "Raw rgb()/rgba() found: %d" % len(raw_rgb)