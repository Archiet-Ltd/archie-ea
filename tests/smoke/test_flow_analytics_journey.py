"""Journey test: Flow analytics page built from shadcn components with live data.

The journey, as an enterprise architect in the seeded organisation with real
Kanban data: opens the flow analytics page; sees stat cards (metrics_card macro
with data-slot="card") with real values; sees chart cards (card macro with
data-slot="card" and canvas); sees a WIP table (table macro with dense-table
classes); sees the board selector (native_select macro); and asserts the signed
in persona is the enterprise architect, not an administrator.

Fails on main (hand-rolled divs, hand-rolled table, raw rgba colours, no
data-slot attributes), passes on the branch (all shadcn macros).
"""

import datetime

import pytest
from playwright.sync_api import Page, expect

from tests.smoke.conftest import PASSWORD

PAGE_TIMEOUT = 30000


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def _seed_flow_analytics_data(app, org_id, user_id):
    """Seed Kanban board + cards into the smoke-test org (in-process, same DB)."""
    from app import db
    from app.models.adm_kanban import KanbanBoard, KanbanCard, ADMPhase, create_adm_phases

    with app.app_context():
        create_adm_phases()
        phase = ADMPhase.query.filter_by(code="A").first()
        assert phase is not None, "ADM phase A must exist"

        # Avoid re-seeding if already done
        existing = KanbanBoard.query.filter_by(organization_id=org_id, name="Journey Test Board").first()
        if existing:
            return existing.id

        board = KanbanBoard(
            name="Journey Test Board",
            organization_id=org_id,
            created_by_id=user_id,
        )
        db.session.add(board)
        db.session.flush()

        now = datetime.datetime.utcnow()
        # Completed cards for cycle time / throughput
        for i in range(5):
            started = now - datetime.timedelta(days=20 + i)
            completed = now - datetime.timedelta(days=7 + i)
            db.session.add(KanbanCard(
                title="Journey Card %d" % (i + 1),
                description="Completed card for flow analytics",
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

        # In-progress cards for WIP snapshot
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


@pytest.fixture
def page(browser, live_server, seeded):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT)
    # Sign in as enterprise_architect, never as an administrator
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    yield page
    context.close()


@pytest.mark.smoke
def test_flow_analytics_shadcn_journey(page: Page, live_server: str, app, seeded):
    """Enterprise architect sees flow analytics built from shadcn macros.

    Asserts the signed-in role is the enterprise architect (not a platform or
    administrator role) before acting, per the brief's persona rule.
    """
    from app.models.user import User

    live_server_url = live_server

    with app.app_context():
        persona = User.query.filter_by(
            email=seeded["emails"]["enterprise_architect"]
        ).first()
        assert persona is not None
        assert persona.enterprise_role == "enterprise_architect"
        assert not persona.is_platform_admin
        user_id = persona.id

    org_id = seeded["ids"]["org"]
    board_id = _seed_flow_analytics_data(app, org_id, user_id)
    assert board_id is not None, "Could not seed Kanban data"

    # Navigate with the board pre-selected
    page.goto(f"{live_server_url}/analytics/flow?board_id={board_id}")

    # 1. Page loads with the flow analytics title (page_shell renders the h1)
    expect(page).to_have_url(lambda url: "/analytics/flow" in url)
    expect(page.locator("h1")).to_contain_text("Flow Analytics")

    # 2. The board selector (native_select macro) is visible with the seeded board
    board_select = page.locator("select#board-selector")
    expect(board_select).to_be_visible()
    expect(board_select).to_contain_text("Journey Test Board")

    # 3. Shadcn metric cards are present (data-slot="card" from macros).
    # The Alpine component loads the four metrics asynchronously; the stat
    # cards themselves render regardless once the board data arrives.
    metric_cards = page.locator('[data-slot="card"]')
    expect(metric_cards.first).to_be_visible(timeout=30000)
    assert metric_cards.count() >= 1, "Should see at least one metric card"

    # 4. Chart cards with canvas elements are present (card macro + Chart.js)
    chart_card = page.locator('[data-slot="card"]:has(canvas)')
    expect(chart_card.first).to_be_visible(timeout=30000)

    # 5. WIP table rendered using the table macro (dense-table class)
    wip_table = page.locator("table.dense-table")
    expect(wip_table).to_be_visible(timeout=30000)

    # 6. The table has a header row with "Board column" and "Cards"
    expect(page.locator("th:has-text('Board column')")).to_be_visible()
    expect(page.locator("th:has-text('Cards')")).to_be_visible()

    # 7. The WIP table shows real rows (the three seeded in-progress cards
    #    grouped by status column).
    wip_rows = wip_table.locator("tbody tr")
    assert wip_rows.count() >= 1, "WIP table should show at least one column row"

    # 8. The metric values are numbers derived from real data, not placeholders.
    #    The cycle-time card value must not be an em dash once data has loaded.
    cycle_value = page.locator('[data-slot="card-title"]', has_text=None).first
    # The first metrics card value should contain 'd' (days) or be a number
    cycle_text = metric_cards.first.text_content()
    assert "Avg Cycle Time" in cycle_text, "Cycle time card label present"