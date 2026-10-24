"""Portfolio Kanban as the people it serves: an Enterprise Architect whose
platform role is Viewer (the demonstration account's shape) works the board by
select, drag and keyboard, is refused at a column's limit with a plain message,
and adds an epic; a read-only Viewer sees a note and no controls."""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login
from .test_portfolio_kanban_journey import _drag_card, _make_viewer, _seed_epics

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

BOARD = "/safe/portfolio"


@pytest.fixture
def own_org(seeded, request):
    """A freshly seeded organisation per test, so one test's moves never leak into another's board."""
    from .conftest import _seed_standard_org

    return _seed_standard_org(request, None)["ids"]["org"]


def _make_enterprise_architect(org_id):
    """An Enterprise Architect persona on the Viewer platform role, no organisation role row."""
    from app import create_app, db
    from app.models.user import Role, User

    app = create_app("testing")
    email = "smoke.safe-ea.%s@example.com" % org_id
    with app.app_context():
        if User.query.filter_by(email=email).first():
            return email
        Role.insert_roles()
        user = User(
            email=email, first_name="Demo", last_name="Architect", organization_id=org_id,
            enterprise_role="enterprise_architect", confirmed=True,
            role=Role.query.filter_by(name="Viewer").first(),
        )
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
    return email


def _assert_signed_in_as_enterprise_architect(email):
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "enterprise_architect"
        assert user.role.name == "Viewer"


def _seed_in_state(org_id, names, state):
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.services.element_profile_service import set_kind, set_kind_property, set_wsjf_components

    with create_app("testing").app_context():
        for name in names:
            el = ArchiMateElement(name=name, type="WorkPackage", organization_id=org_id,
                                  description="Limit fixture")
            db.session.add(el)
            db.session.flush()
            set_kind(el, "Epic", framework_id="safe", source="test")
            set_kind_property(el, "kanban_state", state, source="test")
            set_wsjf_components(el, business_value=2, time_criticality=2, risk_reduction=2,
                                job_size=2, source="test")
        db.session.commit()


def _open_board(browser, live_server, email):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _login(page, live_server, email)
    page.goto(live_server + BOARD, timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Portfolio Kanban")).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    return context, page


def _is_move(response):
    return response.request.method == "PATCH" and "/api/safe/portfolio/" in response.url


def _column_of(page, title):
    return page.locator(".kanban-column").filter(
        has=page.get_by_role("heading", name=title)).get_attribute("data-state")


def test_enterprise_architect_moves_epics_by_select_drag_and_keyboard(browser, live_server, own_org):
    org_id = own_org
    _seed_epics(org_id)
    email = _make_enterprise_architect(org_id)
    _assert_signed_in_as_enterprise_architect(email)
    context, page = _open_board(browser, live_server, email)
    try:
        assert page.get_by_test_id("board-read-only").count() == 0
        legacy = page.locator(".kanban-card").filter(has_text="Legacy Migration Epic")
        select = legacy.get_by_label("Move Legacy Migration Epic to a different column")
        expect(select).to_be_visible(timeout=PAGE_TIMEOUT)
        with page.expect_response(_is_move) as moved:
            select.select_option("Reviewing")
        assert moved.value.status == 200
        expect(page.locator(".kanban-column[data-state='Reviewing']").get_by_role(
            "heading", name="Legacy Migration Epic")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.reload(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        assert _column_of(page, "Legacy Migration Epic") == "Reviewing"

        portal = page.locator(".kanban-card").filter(has_text="Customer Portal Epic")
        with page.expect_response(_is_move) as moved:
            _drag_card(page, portal, "Analyzing")
        assert moved.value.status == 200
        expect(page.locator(".kanban-column[data-state='Analyzing']").get_by_role(
            "heading", name="Customer Portal Epic")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.reload(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        assert _column_of(page, "Customer Portal Epic") == "Analyzing"

        platform = page.locator(".kanban-card").filter(has_text="Platform Modernization Epic")
        assert platform.get_attribute("tabindex") == "0"
        assert platform.get_attribute("role") == "group"
        assert "Platform Modernization Epic" in platform.get_attribute("aria-label")
        platform.focus()
        with page.expect_response(_is_move) as moved:
            page.keyboard.press("ArrowRight")
        assert moved.value.status == 200
        expect(page.locator(".kanban-column[data-state='Reviewing']").get_by_role(
            "heading", name="Platform Modernization Epic")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.reload(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        assert _column_of(page, "Platform Modernization Epic") == "Reviewing"
    finally:
        context.close()


def test_enterprise_architect_is_refused_at_the_limit_and_adds_an_epic(browser, live_server, own_org):
    org_id = own_org
    _seed_epics(org_id)
    _seed_in_state(org_id, ["Full One", "Full Two", "Full Three"], "Analyzing")
    email = _make_enterprise_architect(org_id)
    _assert_signed_in_as_enterprise_architect(email)
    context, page = _open_board(browser, live_server, email)
    try:
        legacy = page.locator(".kanban-card").filter(has_text="Legacy Migration Epic")
        legacy.get_by_label("Move Legacy Migration Epic to a different column").select_option("Analyzing")
        expect(page.get_by_text("Analyzing is at its limit of 3 epics").first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert _column_of(page, "Legacy Migration Epic") == "Funnel"
        page.reload(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        assert _column_of(page, "Legacy Migration Epic") == "Funnel"

        # The server refuses too, not only the page: a direct request is turned away.
        answer = page.evaluate("""async (id) => {
            const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
            const r = await fetch('/api/safe/portfolio/' + id, {method: 'PATCH',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
                body: JSON.stringify({kanban_state: 'Analyzing'})});
            return {status: r.status, body: await r.json()};
        }""", int(legacy.get_attribute("data-card-id")))
        assert answer["status"] == 409, answer
        assert "at its limit of 3" in answer["body"]["error"]

        page.get_by_role("button", name="Add epic").first.click()
        dialog = page.get_by_role("dialog", name="Add epic")
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        dialog.get_by_label("Name").fill("Architect Added Epic")
        dialog.get_by_role("button", name="Add epic").click()
        page.wait_for_load_state("networkidle")
        card = page.locator(".kanban-column[data-state='Funnel'] .kanban-card").filter(
            has_text="Architect Added Epic")
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        page.reload(timeout=PAGE_TIMEOUT)
        expect(page.locator(".kanban-column[data-state='Funnel'] .kanban-card").filter(
            has_text="Architect Added Epic")).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_viewer_sees_a_read_only_note_and_no_controls(browser, live_server, own_org):
    org_id = own_org
    _seed_epics(org_id)
    context, page = _open_board(browser, live_server, _make_viewer(org_id))
    try:
        note = page.get_by_test_id("board-read-only")
        expect(note).to_contain_text("You can view this board")
        assert page.get_by_role("button", name="Add epic").count() == 0
        selects = page.locator(".kanban-card select")
        for i in range(selects.count()):
            assert not selects.nth(i).is_visible()
        card = page.locator(".kanban-card").filter(has_text="Legacy Migration Epic")
        assert card.get_attribute("draggable") == "false"
        card.focus()
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(500)
        assert _column_of(page, "Legacy Migration Epic") == "Funnel"
    finally:
        context.close()
