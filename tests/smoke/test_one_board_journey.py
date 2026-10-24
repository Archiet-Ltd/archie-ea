"""One board: the backlog prioritisation and product roadmap pages are the shared
board, and an architect works them by drag, by keyboard and by the "Move to" menu.

Each journey signs in as the persona the page serves (never an administrator),
works the real controls, reloads, and asserts the move was saved on the server.
A second organisation proves a board shows only its own organisation's items, and
an organisation with nothing on the board is told why and given the next step.
"""
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, _seed_standard_org
from .test_archetype_journeys import _login
from .test_portfolio_kanban_enterprise_architect import _make_enterprise_architect
from .test_portfolio_kanban_journey import _drag_card, _make_viewer, _move_by_menu

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

BACKLOG = "/backlog/prioritisation"
ROADMAP = "/product-roadmap"
UNSET_LABEL = "Not yet prioritised"


@pytest.fixture
def own_org(seeded, request):
    return _seed_standard_org(request, None)["ids"]["org"]


@pytest.fixture
def other_org(seeded, request):
    return _seed_standard_org(request, None)["ids"]["org"]


def _assert_persona(email, persona):
    """The signed-in user is the persona named, on the Viewer platform role, and not an administrator."""
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == persona, user.enterprise_role
        assert user.role.name == "Viewer", user.role.name
        assert not user.is_admin()


def _seed_requirements(org_id, titles, **fields):
    from app import create_app, db
    from app.models.models import Requirement

    with create_app("testing").app_context():
        for title in titles:
            db.session.add(Requirement(title=title, type="functional", organization_id=org_id, **fields))
        db.session.commit()


def _seed_epics(org_id, specs):
    """specs: [(title, horizon-or-None)]"""
    from app import create_app, db
    from app.models.models import Requirement

    with create_app("testing").app_context():
        for title, horizon in specs:
            db.session.add(Requirement(title=title, requirement_type="epic", type="functional",
                                       organization_id=org_id, horizon=horizon))
        db.session.commit()


def _open(browser, live_server, email, path, heading, width=1440):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    _login(page, live_server, email)
    page.goto(live_server + path, timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name=heading, exact=True).first).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    return context, page


def _column(page, state):
    return page.locator(".kanban-column[data-state='%s']" % state)


def _column_of(page, title):
    return page.locator(".kanban-column").filter(
        has=page.get_by_role("heading", name=title)).get_attribute("data-state")


def _is_patch(fragment):
    return lambda r: r.request.method == "PATCH" and fragment in r.url


def _wait_cards(page):
    expect(page.locator(".kanban-card").first).to_be_visible(timeout=PAGE_TIMEOUT)


def _reload(page):
    page.reload(timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    _wait_cards(page)


# ── Backlog prioritisation ───────────────────────────────────────────────────

def test_architect_prioritises_requirements_by_drag_keyboard_and_menu(browser, live_server, own_org):
    tag = uuid.uuid4().hex[:6]
    titles = ["Export invoices %s" % tag, "Remind overdue customers %s" % tag, "Archive old ledgers %s" % tag]
    _seed_requirements(own_org, titles)
    email = _make_enterprise_architect(own_org)
    _assert_persona(email, "enterprise_architect")
    context, page = _open(browser, live_server, email, BACKLOG, "Backlog Prioritisation")
    try:
        _wait_cards(page)
        # Every requirement starts in "Not yet prioritised", and the count badge agrees with the cards.
        unset = _column(page, "UNSET")
        expect(unset.locator(".kanban-card")).to_have_count(3, timeout=PAGE_TIMEOUT)
        assert unset.locator("[data-slot=card-header] [data-slot=card]").count() == 0
        expect(unset.get_by_role("heading", name=UNSET_LABEL)).to_be_visible()
        assert unset.locator("h2 + div, h2 ~ div").first.inner_text().strip().startswith("3")

        # Drag: first requirement to Must have.
        with page.expect_response(_is_patch("/moscow")) as moved:
            _drag_card(page, page.locator(".kanban-card").filter(has_text=titles[0]), "MUST")
        assert moved.value.status == 200
        # Menu: second requirement to Should have.
        with page.expect_response(_is_patch("/moscow")) as moved:
            _move_by_menu(page.locator(".kanban-card").filter(has_text=titles[1]), "Should have")
        assert moved.value.status == 200
        # Keyboard: third requirement picked up with Space, moved right with the arrow key, dropped with Space.
        third = page.locator(".kanban-card").filter(has_text=titles[2])
        third.focus()
        page.keyboard.press("Space")
        expect(page.locator("[data-kanban-announce]").first).to_contain_text("picked up", timeout=PAGE_TIMEOUT)
        with page.expect_response(_is_patch("/moscow")) as moved:
            page.keyboard.press("ArrowRight")
        assert moved.value.status == 200
        page.keyboard.press("Space")
        expect(page.locator("[data-kanban-announce]").first).to_contain_text("dropped", timeout=PAGE_TIMEOUT)

        # Saved on the server: a fresh load shows each requirement where it was put.
        _reload(page)
        assert _column_of(page, titles[0]) == "MUST"
        assert _column_of(page, titles[1]) == "SHOULD"
        assert _column_of(page, titles[2]) == "MUST"
        expect(_column(page, "UNSET").locator(".kanban-card")).to_have_count(0)
        expect(_column(page, "UNSET").locator("[data-empty-state]")).to_be_visible()
        api = page.evaluate("""async () => (await (await fetch('/api/backlog/prioritised?method=moscow')).json())
            .map(r => [r.title, r.moscow_priority])""")
        saved = dict(api)
        assert saved[titles[0]] == "MUST" and saved[titles[1]] == "SHOULD" and saved[titles[2]] == "MUST"

        # The WSJF tab lists the same requirements the board shows.
        page.get_by_role("tab", name="WSJF").click()
        for title in titles:
            expect(page.get_by_role("cell", name=title)).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_backlog_board_works_on_a_phone_with_the_menu(browser, live_server, own_org):
    title = "Phone requirement %s" % uuid.uuid4().hex[:6]
    _seed_requirements(own_org, [title])
    email = _make_enterprise_architect(own_org)
    context, page = _open(browser, live_server, email, BACKLOG, "Backlog Prioritisation", width=390)
    try:
        _wait_cards(page)
        scroller = page.locator("[data-kanban-scroll]").first
        assert scroller.evaluate("el => el.scrollWidth > el.clientWidth"), "board does not scroll sideways at 390"
        with page.expect_response(_is_patch("/moscow")) as moved:
            _move_by_menu(page.locator(".kanban-card").filter(has_text=title), "Could have")
        assert moved.value.status == 200
        _reload(page)
        assert _column_of(page, title) == "COULD"
    finally:
        context.close()


def test_viewer_sees_the_backlog_board_read_only_and_the_server_refuses(browser, live_server, own_org):
    title = "Read only requirement %s" % uuid.uuid4().hex[:6]
    _seed_requirements(own_org, [title])
    email = _make_viewer(own_org)
    context, page = _open(browser, live_server, email, BACKLOG, "Backlog Prioritisation")
    try:
        _wait_cards(page)
        expect(page.get_by_text("You can view this board. Ask an architect to change priorities.")).to_be_visible()
        card = page.locator(".kanban-card").filter(has_text=title)
        expect(card).to_be_visible()
        assert card.get_attribute("draggable") == "false"
        assert not card.locator("[data-kanban-move]").is_visible()
        answer = page.evaluate("""async () => {
            const id = (await (await fetch('/api/backlog/prioritised?method=moscow')).json())[0].id;
            const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
            const r = await fetch('/api/backlog/' + id + '/moscow', {method: 'PATCH',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
                body: JSON.stringify({priority: 'MUST'})});
            return {status: r.status, body: await r.json()};
        }""")
        assert answer["status"] == 403, answer
        assert "can view the backlog" in answer["body"]["error"]
    finally:
        context.close()


def test_backlog_board_shows_only_the_signed_in_organisations_requirements(browser, live_server, own_org, other_org):
    mine = "Mine %s" % uuid.uuid4().hex[:6]
    theirs = "Theirs %s" % uuid.uuid4().hex[:6]
    _seed_requirements(own_org, [mine])
    _seed_requirements(other_org, [theirs])
    email = _make_enterprise_architect(own_org)
    context, page = _open(browser, live_server, email, BACKLOG, "Backlog Prioritisation")
    try:
        _wait_cards(page)
        expect(page.locator(".kanban-card").filter(has_text=mine)).to_have_count(1)
        assert page.locator(".kanban-card").filter(has_text=theirs).count() == 0
        assert theirs not in page.inner_text("body")
    finally:
        context.close()


def test_backlog_with_nothing_to_rank_says_why_and_the_link_lands_on_content(browser, live_server, own_org):
    email = _make_enterprise_architect(own_org)
    context, page = _open(browser, live_server, email, BACKLOG, "Backlog Prioritisation")
    try:
        expect(page.get_by_role("heading", name="No requirements to prioritise")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.locator(".kanban-card").count() == 0
        page.get_by_role("link", name="Open the requirements backlog").click()
        page.wait_for_load_state("networkidle")
        assert page.url.endswith("/enterprise/requirements-backlog"), page.url
        expect(page.locator("main, body").first).not_to_contain_text("404")
        assert len(page.inner_text("body").strip()) > 200
    finally:
        context.close()


# ── Product roadmap ──────────────────────────────────────────────────────────

def _solution_architect(org_id):
    from app import create_app, db
    from app.models.user import Role, User

    email = "board.sa.%s@example.com" % org_id
    with create_app("testing").app_context():
        if User.query.filter_by(email=email).first():
            return email
        Role.insert_roles()
        user = User(email=email, first_name="Demo", last_name="Solution", organization_id=org_id,
                    enterprise_role="solution_architect", confirmed=True,
                    role=Role.query.filter_by(name="Viewer").first())
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
    return email


def test_solution_architect_schedules_epics_by_drag_keyboard_and_menu(browser, live_server, own_org):
    tag = uuid.uuid4().hex[:6]
    a, b, c = "Epic A %s" % tag, "Epic B %s" % tag, "Epic C %s" % tag
    _seed_epics(own_org, [(a, None), (b, None), (c, "next")])
    email = _solution_architect(own_org)
    _assert_persona(email, "solution_architect")
    context, page = _open(browser, live_server, email, ROADMAP, "Product Roadmap")
    try:
        _wait_cards(page)
        expect(_column(page, "unscheduled").locator(".kanban-card")).to_have_count(2, timeout=PAGE_TIMEOUT)
        expect(_column(page, "next").locator(".kanban-card")).to_have_count(1)

        with page.expect_response(_is_patch("/horizon")) as moved:
            _drag_card(page, page.locator(".kanban-card").filter(has_text=a), "now")
        assert moved.value.status == 200
        with page.expect_response(_is_patch("/horizon")) as moved:
            _move_by_menu(page.locator(".kanban-card").filter(has_text=b), "Later")
        assert moved.value.status == 200
        card_c = page.locator(".kanban-card").filter(has_text=c)
        card_c.focus()
        page.keyboard.press("Enter")
        with page.expect_response(_is_patch("/horizon")) as moved:
            page.keyboard.press("ArrowRight")
        assert moved.value.status == 200
        page.keyboard.press("Enter")

        _reload(page)
        assert _column_of(page, a) == "now"
        assert _column_of(page, b) == "later"
        assert _column_of(page, c) == "later"
        # The "on track" figure is explained by the Now column: one epic with no stories done is 0%, not a dash.
        assert page.get_by_test_id("on-track").inner_text().strip() == "0%"

        # Put one back to unscheduled: the server clears the horizon.
        with page.expect_response(_is_patch("/horizon")) as moved:
            _move_by_menu(page.locator(".kanban-card").filter(has_text=a), "Unscheduled")
        assert moved.value.status == 200
        _reload(page)
        assert _column_of(page, a) == "unscheduled"
        assert page.get_by_test_id("on-track").inner_text().strip() == "—"
    finally:
        context.close()


def test_roadmap_works_on_a_phone_and_a_viewer_is_read_only(browser, live_server, own_org):
    title = "Phone epic %s" % uuid.uuid4().hex[:6]
    _seed_epics(own_org, [(title, "now")])
    sa = _solution_architect(own_org)
    context, page = _open(browser, live_server, sa, ROADMAP, "Product Roadmap", width=390)
    try:
        _wait_cards(page)
        assert page.locator("[data-kanban-scroll]").first.evaluate("el => el.scrollWidth > el.clientWidth")
        with page.expect_response(_is_patch("/horizon")) as moved:
            _move_by_menu(page.locator(".kanban-card").filter(has_text=title), "Next")
        assert moved.value.status == 200
        _reload(page)
        assert _column_of(page, title) == "next"
    finally:
        context.close()

    viewer = _make_viewer(own_org)
    context, page = _open(browser, live_server, viewer, ROADMAP, "Product Roadmap")
    try:
        _wait_cards(page)
        expect(page.get_by_text("You can view this roadmap. Ask an architect to schedule epics.")).to_be_visible()
        card = page.locator(".kanban-card").filter(has_text=title)
        assert card.get_attribute("draggable") == "false"
        assert not card.locator("[data-kanban-move]").is_visible()
        status = page.evaluate("""async () => {
            const id = (await (await fetch('/api/product-roadmap')).json()).horizons.next.epics[0].id;
            const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
            const r = await fetch('/api/product-roadmap/epics/' + id + '/horizon', {method: 'PATCH',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
                body: JSON.stringify({horizon: 'now'})});
            return r.status;
        }""")
        assert status == 403
    finally:
        context.close()


def test_roadmap_shows_only_the_signed_in_organisations_epics_and_explains_when_empty(
        browser, live_server, own_org, other_org):
    theirs = "Theirs epic %s" % uuid.uuid4().hex[:6]
    _seed_epics(other_org, [(theirs, "now")])
    email = _solution_architect(own_org)
    context, page = _open(browser, live_server, email, ROADMAP, "Product Roadmap")
    try:
        expect(page.get_by_role("heading", name="No epics on the roadmap yet")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert theirs not in page.inner_text("body")
        page.get_by_role("link", name="Open the requirements backlog").click()
        page.wait_for_load_state("networkidle")
        assert page.url.endswith("/enterprise/requirements-backlog"), page.url
        assert len(page.inner_text("body").strip()) > 200
    finally:
        context.close()
