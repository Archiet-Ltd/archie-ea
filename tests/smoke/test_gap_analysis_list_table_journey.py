"""Gap Analysis: the enterprise architect opens the ADM Kanban Gap Analysis page and
reads the Gap list through the standard list table (toolbar search, column view,
sort, pagination footer), then follows a gap's row action back to the board.

Runs in the seeded organisation and verifies a second organisation's gaps never
appear on this organisation's page.
"""
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/adm-kanban/gap-analysis"


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def _assert_persona(email, persona):
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == persona
        assert not user.is_admin()


def _make_gaps(org_id, suffix, count, board_name):
    """`count` Gap cards on a fresh board in organisation `org_id`."""
    from app import create_app, db
    from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard
    from app.models.user import User

    with create_app("testing").app_context():
        phase_code = "E-%s" % uuid.uuid4().hex[:8]
        phase = ADMPhase(name="Opportunities and Solutions %s" % phase_code, code=phase_code, order=5)
        db.session.add(phase)
        db.session.commit()
        creator = User.query.filter_by(organization_id=org_id).first()
        board = KanbanBoard(name=board_name, organization_id=org_id, current_adm_phase=phase.code,
                             created_by_id=creator.id)
        db.session.add(board)
        db.session.commit()
        ids = []
        for i in range(count):
            card = KanbanCard(
                title="%s Gap %d %s" % (board_name, i, suffix),
                description="Gap %d needs a closing work package" % i,
                adm_phase_id=phase.id,
                board_id=board.id,
                card_type="gap",
                arch_element_type="Gap",
                arch_domain="Application",
                organization_id=org_id,
                created_by_id=creator.id,
            )
            db.session.add(card)
            db.session.flush()
            ids.append(card.id)
        db.session.commit()
        return board.id, phase.id, ids

def _make_closers(org_id, gap_ids, count):
    """`count` WorkPackage cards in `org_id` closing gaps from `gap_ids`."""
    from app import create_app, db
    from app.models.adm_kanban import KanbanCard
    from app.models.user import User

    with create_app("testing").app_context():
        creator = User.query.filter_by(organization_id=org_id).first()
        board_id = KanbanCard.query.get(gap_ids[0]).board_id
        phase_id = KanbanCard.query.get(gap_ids[0]).adm_phase_id
        ids = []
        for i in range(count):
            card = KanbanCard(
                title="Closing WP %d" % i,
                description="Work package for gap %d" % i,
                adm_phase_id=phase_id,
                board_id=board_id,
                card_type="work_package",
                closes_gap_id=gap_ids[i],
                organization_id=org_id,
                created_by_id=creator.id,
            )
            db.session.add(card)
            db.session.flush()
            ids.append(card.id)
        db.session.commit()
        return ids


def _cleanup(board_id, phase_id, card_ids):
    from app import create_app, db
    from app.models.adm_kanban import ADMPhase, KanbanBoard, KanbanCard

    with create_app("testing").app_context():
        db.session.rollback()
        if card_ids:
            KanbanCard.query.filter(KanbanCard.id.in_(card_ids)).delete(synchronize_session=False)
        if board_id:
            KanbanBoard.query.filter_by(id=board_id).delete(synchronize_session=False)
        if phase_id:
            ADMPhase.query.filter_by(id=phase_id).delete(synchronize_session=False)
        db.session.commit()


@pytest.fixture()
def gap_data(app, seeded):
    """Nine gaps in the seeded organisation (enough to show the toolbar and a
    second page), plus one gap in a second organisation that must never appear."""
    suffix = uuid.uuid4().hex[:6]
    org_id = seeded["ids"]["org"]
    board_id, phase_id, ids = _make_gaps(org_id, suffix, 9, "Trace Board %s" % suffix)
    closer_ids = _make_closers(org_id, ids, 3)

    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    with create_app("testing").app_context():
        other = Organization(name="Gap Other %s" % suffix, slug="gap-other-%s" % suffix)
        db.session.add(other)
        db.session.commit()
        other_id = other.id
        other_user = User(email="smoke.gap.other.%s@example.com" % suffix, first_name="Other",
                           last_name="Org", organization_id=other_id, enterprise_role="enterprise_architect",
                           confirmed=True)
        other_user.role = Role.query.filter_by(name="Viewer").one()
        other_user.password = PASSWORD
        db.session.add(other_user)
        db.session.commit()
        other_user_id = other_user.id
    other_board_id, other_phase_id, other_ids = _make_gaps(other_id, suffix, 1, "Foreign Board %s" % suffix)

    yield {"suffix": suffix, "org_id": org_id}

    _cleanup(board_id, phase_id, ids + closer_ids)
    _cleanup(other_board_id, other_phase_id, other_ids)
    with create_app("testing").app_context():
        from sqlalchemy import text

        db.session.rollback()
        db.session.execute(text("DELETE FROM soc2_audit_log WHERE organization_id = :oid"), {"oid": other_id})
        User.query.filter_by(id=other_user_id).delete(synchronize_session=False)
        Organization.query.filter_by(id=other_id).delete(synchronize_session=False)
        db.session.commit()


def _open(page, base, email, theme=None):
    _login(page, base, email)
    if theme:
        page.evaluate("localStorage.setItem('theme', '%s')" % theme)
    page.goto(base + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_selector('[data-testid="gaps-table"]', timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("viewport,theme", [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")])
def test_gap_analysis_list_table(browser, live_server, seeded, gap_data, viewport, theme):
    """The Gap Analysis page renders its gaps through the standard list table:
    search, a sortable column, the column-view menu, the pagination footer, and
    following a gap's link lands on the ADM Kanban board."""
    email = seeded["emails"]["enterprise_architect"]
    _assert_persona(email, "enterprise_architect")
    suffix = gap_data["suffix"]

    context = browser.new_context(ignore_https_errors=True, viewport=viewport)
    page = context.new_page()
    try:
        _open(page, live_server, email, theme)
        is_dark = page.evaluate("document.documentElement.classList.contains('dark')")
        assert is_dark == (theme == "dark"), "the page did not apply the %s theme" % theme
        table = page.locator('[data-testid="gaps-table"]')
        pager = page.locator('[data-testid="gaps-table-pager"]')

        # The toolbar search filters to a gap named in this test.
        search = pager.locator('input[data-table-filter]')
        expect(search).to_be_visible(timeout=PAGE_TIMEOUT)
        search.fill("Gap 0 %s" % suffix)
        expect(table.locator('tbody [data-testid="gap-row"]:visible')).to_have_count(1)
        expect(pager.locator('[data-table-reset]')).to_be_visible(timeout=PAGE_TIMEOUT)
        pager.locator('[data-table-reset]').click()
        expect(search).to_have_value("")

        # A column sorts.
        pager.locator('[data-table-sort-trigger="0"]').click()
        page.get_by_role("menuitem", name="Ascending").click()
        page.wait_for_timeout(300)

        # Column view hides a column (desktop only: the View menu is hidden below lg).
        if viewport["width"] >= 1024:
            pager.get_by_role("button", name="View").click()
            page.locator('[data-table-toggle="1"][data-table-owner="gaps-table"]').click()
            page.keyboard.press("Escape")
            expect(table.locator('thead th[data-col="1"]')).to_be_hidden()

        # Pagination footer shows the full count and a working next page.
        footer = page.locator('[data-slot="data-table-pagination"]')
        expect(footer).to_contain_text("Page 1 of")
        next_btn = pager.locator('[data-table-pager="next"]')
        if next_btn.is_enabled():
            next_btn.click()
            expect(footer).to_contain_text("Page 2")

        # A gap's row action (its title link) opens the ADM Kanban board.
        first_link = table.locator('tbody [data-testid="gap-row"] a').first
        expect(first_link).to_be_visible()
        first_link.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/adm-kanban" in page.url

        # A second organisation's gap never appears on this page.
        page.go_back(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector('[data-testid="gaps-table"]', timeout=PAGE_TIMEOUT)
        assert "Foreign Board" not in page.content()

        # The coverage bar's value agrees with the percentage shown beside it.
        # 3 of the 9 seeded gaps are closed by `gap_data`'s closer cards, so the
        # computed figure is round(3 / 9 * 100) == 33.
        summary = page.locator('[data-testid="gap-coverage-summary"]')
        expect(summary).to_be_visible(timeout=PAGE_TIMEOUT)
        progress_bar = summary.locator('[role="progressbar"]').first
        expect(progress_bar).to_be_visible()
        bar_now = float(progress_bar.get_attribute('aria-valuenow'))
        bar_max = float(progress_bar.get_attribute('aria-valuemax'))
        percentage_text = summary.locator('[data-testid="gap-coverage-pct"]').inner_text()
        percentage_value = float(re.search(r'(\d+)%', percentage_text).group(1))
        assert bar_max == 100, bar_max
        assert bar_now == 33, bar_now
        assert bar_now == percentage_value, (bar_now, percentage_value)

        # The attributes above are necessary but not sufficient: the previous
        # defect (a Tailwind arbitrary-value class, `w-[{{ pct }}%]`, that
        # Tailwind's static build never generates) left aria-valuenow correct
        # while the bar still drew full at every percentage. Measure what is
        # actually rendered: the fill element's visible overlap with its
        # track, as a fraction of the track's width.
        visual = page.evaluate(
            """
            () => {
                const bar = document.querySelector(
                    '[data-testid="gap-coverage-summary"] [role="progressbar"]'
                );
                if (!bar) return null;
                const indicator = bar.querySelector('[data-slot="progress-indicator"]');
                const fillEl = indicator || bar;
                const trackEl = indicator ? bar : bar.parentElement;
                const fillRect = fillEl.getBoundingClientRect();
                const trackRect = trackEl.getBoundingClientRect();
                const overlapLeft = Math.max(fillRect.left, trackRect.left);
                const overlapRight = Math.min(fillRect.right, trackRect.right);
                const overlapWidth = Math.max(0, overlapRight - overlapLeft);
                return trackRect.width > 0 ? (overlapWidth / trackRect.width) * 100 : null;
            }
            """
        )
        assert visual is not None, "no [role=progressbar] found inside the coverage summary"
        assert abs(visual - percentage_value) < 2, (
            "the bar's rendered fill is %.1f%% of its track but the page says %s%%"
            % (visual, percentage_value)
        )

        # The table caption is correct.
        caption = page.locator('caption')
        expect(caption).to_have_text("Gap records and the work packages that close them")

    finally:
        context.close()


def test_gap_analysis_empty_table_agrees_with_the_computed_figures(browser, live_server):
    """An enterprise architect opens an organisation that has a computed portfolio
    gap (an application mapped to no capability) but no recorded Gap element.

    The figures above the table are non-zero -- the screen must not then show an
    empty table that contradicts them. The empty state names why it is empty (no
    gap record yet, the figures above come from the capability map) and offers
    the one next step for someone who may create elements: a link that lands on
    a real page, never the old, self-contradicting "No gaps detected" sentence.
    """
    from .fresh_org import create_fresh_org, sign_in

    org = create_fresh_org("enterprise_architect")
    email = org["emails"]["enterprise_architect"]

    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent

    with create_app("testing").app_context():
        orphan = ApplicationComponent(
            name="Unmapped App %s" % org["suffix"],
            organization_id=org["org_id"],
            lifecycle_status="operational",
            description="Not linked to any capability or process; the computed gap this test needs.",
        )
        db.session.add(orphan)
        db.session.commit()
        db.session.remove()

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        sign_in(page, live_server, email)
        page.goto(live_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_selector('[data-testid="gaps-table"]', timeout=PAGE_TIMEOUT)

        # The figures above are computed from the capability map and must be
        # non-zero: this organisation has a real gap, just no Gap record for it.
        total_tile = page.locator("div.text-center", has_text="Total Gaps").locator(".text-2xl")
        expect(total_tile).to_have_text(re.compile(r"[1-9]"))

        table = page.locator('[data-testid="gaps-table"]')
        expect(table).to_contain_text("No gap record has been made yet")
        expect(table).to_contain_text("computed from the capability map")
        assert "No gaps detected" not in table.inner_text(), (
            "the empty table still says 'No gaps detected' while the figures above it are non-zero"
        )

        # The one next step: a link that actually lands on content, not a dead end.
        action = table.get_by_role("link", name=re.compile("gap record", re.I))
        expect(action).to_be_visible(timeout=PAGE_TIMEOUT)
        action.click()
        page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
        # It lands on a form for a gap, not on an empty list.
        dialog = page.get_by_role("dialog", name="New Architecture Task")
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(dialog.locator("#ct-arch-element-type")).to_have_value("Gap")
    finally:
        context.close()
