"""ADM board as the Enterprise Architect who runs it.

The goal: "see where each of my solutions sits in the ADM cycle, what is due to
be produced for it, and open any of it". The board is the TOGAF cycle laid out
as lanes (Preliminary, A to H, Requirements Management) with the solutions in
the lane a practitioner expects, grouped by status inside the lane.

Each journey signs in as an Enterprise Architect on the Viewer platform role
(never an administrator) and asserts that before it clicks anything.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

BOARD = "/adm-kanban/"

LANE_NAMES = [
    "Preliminary",
    "Architecture Vision",
    "Business Architecture",
    "Information Systems Architectures",
    "Technology Architecture",
    "Opportunities and Solutions",
    "Migration Planning",
    "Implementation Governance",
    "Architecture Change Management",
    "Requirements Management",
]

DELIVERABLES = {
    "B": ["Lane Test Business Architecture Document", "Lane Test Business Gap Analysis", "Lane Test Business Principles"],
    "D": ["Lane Test Technology Architecture", "Lane Test Technology Roadmap Update"],
    "F": ["Lane Test Migration Plan", "Lane Test Transition Architecture"],
}

SOLUTIONS = [
    ("Lane Test Customer Onboarding", "B", "in_progress"),
    ("Lane Test Data Platform", "D", "draft"),
    ("Lane Test Payments Migration", "F", "approved"),
]


@pytest.fixture
def own_org(seeded, request):
    """A fresh organisation per test so one journey's moves never reach another's board."""
    from .conftest import _seed_standard_org

    return _seed_standard_org(request, None)["ids"]["org"]


def _make_enterprise_architect(org_id):
    from app import create_app, db
    from app.models.user import Role, User

    email = "smoke.adm-ea.%s@example.com" % org_id
    with create_app("testing").app_context():
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
    """The journey's persona is the Enterprise Architect, not an administrator."""
    from app import create_app
    from app.models.user import User

    with create_app("testing").app_context():
        user = User.query.filter_by(email=email).one()
        assert user.enterprise_role == "enterprise_architect"
        assert user.role.name == "Viewer"
        assert not user.is_platform_admin
        assert not user.is_org_admin


def _seed_board(org_id, architect_email):
    from app import create_app, db
    from app.models.adm_deliverable import ADMDeliverable, ADMDeliverableCheck
    from app.models.solution_models import Solution
    from app.models.user import User

    with create_app("testing").app_context():
        architect_id = User.query.filter_by(email=architect_email).one().id
        # The deliverable catalogue is shared by every organisation; start from a known set.
        ADMDeliverableCheck.query.delete()
        ADMDeliverable.query.delete()
        for phase, names in DELIVERABLES.items():
            for name in names:
                if not ADMDeliverable.query.filter_by(name=name).first():
                    db.session.add(ADMDeliverable(
                        phase=phase, name=name, is_template=True,
                        description="Produced in phase %s." % phase))
        for name, phase, status in SOLUTIONS:
            db.session.add(Solution(
                name=name, description="Board fixture.", organization_id=org_id,
                governance_status=status, adm_phase=phase, solution_owner="Someone Else",
                # Only the data platform solution is the signed-in architect's own.
                created_by_id=architect_id if name == SOLUTIONS[1][0] else None))
        db.session.commit()


def _drop_deliverables():
    from app import create_app, db
    from app.models.adm_deliverable import ADMDeliverable, ADMDeliverableCheck

    with create_app("testing").app_context():
        ADMDeliverableCheck.query.delete()
        ADMDeliverable.query.delete()
        db.session.commit()


@pytest.fixture
def seeded_board(own_org):
    _seed_board(own_org, _make_enterprise_architect(own_org))
    yield own_org
    _drop_deliverables()


def _open(browser, live_server, email, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    # The first-visit welcome overlay is not what these journeys are about.
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    _login(page, live_server, email)
    page.goto(live_server + BOARD, timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="ADM Kanban", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    return context, page


def _lane(page, phase):
    return page.locator('[data-testid="adm-lane"][data-phase="%s"]' % phase)


@pytest.mark.parametrize("width", [1440, 390])
def test_architect_reads_the_cycle_as_lanes_with_solutions_and_deliverables(
        browser, live_server, seeded_board, width):
    email = _make_enterprise_architect(seeded_board)
    _assert_signed_in_as_enterprise_architect(email)
    context, page = _open(browser, live_server, email, width)
    try:
        lanes = page.locator('[data-testid="adm-lane"]')
        expect(lanes).to_have_count(10, timeout=PAGE_TIMEOUT)
        names = [lanes.nth(i).get_by_role("heading").first.inner_text() for i in range(10)]
        for expected, actual in zip(LANE_NAMES, names):
            assert expected in actual, "lane %r reads %r" % (expected, actual)

        # The three solutions sit in B, D and F, each with its deliverable count.
        for title, phase, _status in SOLUTIONS:
            card = _lane(page, phase).locator('[data-testid="adm-card"]').filter(has_text=title)
            expect(card).to_have_count(1)
            chip = card.get_by_test_id("adm-deliverables-chip")
            count = len(DELIVERABLES[phase])
            expect(chip).to_have_text(re.compile(r"%d deliverables?" % count))

        # Counts agree between the lane header and what the lane holds, in the right number.
        expect(_lane(page, "B")).to_contain_text("1 solution")
        body = page.locator("main").inner_text()
        assert "1 cards" not in body and "1 solutions" not in body

        # No grid of dash cells: an empty lane says so once, with the next step.
        empty = _lane(page, "E")
        expect(empty.get_by_text("No solutions in this phase")).to_have_count(1)
        expect(empty.get_by_role("link", name=re.compile("Start a solution"))).to_have_count(1)
        assert page.locator('[data-testid="adm-lane"]:has-text("—")').count() == 0

        # The board scrolls sideways inside its own region; the page does not.
        scroller = page.locator(".kanban-board [data-kanban-scroll]")
        assert scroller.evaluate("el => el.scrollWidth > el.clientWidth") is True
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1

        # The chip opens the list of what the phase produces; the list has content.
        card = _lane(page, "B").locator('[data-testid="adm-card"]').filter(has_text=SOLUTIONS[0][0])
        card.get_by_test_id("adm-deliverables-chip").click()
        dialog = page.get_by_role("dialog", name=re.compile("Deliverables"))
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        for name in DELIVERABLES["B"]:
            expect(dialog).to_contain_text(name)
    finally:
        context.close()


def test_every_card_link_lands_on_the_solution(browser, live_server, seeded_board):
    email = _make_enterprise_architect(seeded_board)
    _assert_signed_in_as_enterprise_architect(email)
    context, page = _open(browser, live_server, email, 1440)
    try:
        # A solution the architect can open links to it, and the page has its content.
        card = _lane(page, "D").locator('[data-testid="adm-card"]').filter(has_text=SOLUTIONS[1][0])
        card.get_by_role("link", name=re.compile("Open")).click()
        page.wait_for_load_state("networkidle")
        assert "/solutions/" in page.url
        expect(page.get_by_text(SOLUTIONS[1][0]).first).to_be_visible(timeout=PAGE_TIMEOUT)

        # One the architect cannot open carries no link to a refusal; it says why.
        page.goto(live_server + BOARD, timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        other = _lane(page, "B").locator('[data-testid="adm-card"]').filter(has_text=SOLUTIONS[0][0])
        expect(other.get_by_role("link", name=re.compile("Open"))).to_have_count(0)
        expect(other).to_contain_text("Open to its owner and stakeholders")
    finally:
        context.close()


def test_deliverables_filter_lists_the_deliverables_not_an_empty_board(browser, live_server, seeded_board):
    email = _make_enterprise_architect(seeded_board)
    _assert_signed_in_as_enterprise_architect(email)
    context, page = _open(browser, live_server, email, 1440)
    try:
        page.get_by_label("Filter by type").select_option("deliverable")
        for names in DELIVERABLES.values():
            for name in names:
                expect(page.locator("main").get_by_text(name).first).to_be_visible(timeout=PAGE_TIMEOUT)
        text = page.locator("main").inner_text()
        assert "No solutions on the board" not in text
        assert "Create a solution to start tracking" not in text
    finally:
        context.close()


def test_status_change_from_the_card_survives_a_reload(browser, live_server, seeded_board):
    email = _make_enterprise_architect(seeded_board)
    _assert_signed_in_as_enterprise_architect(email)
    context, page = _open(browser, live_server, email, 1440)
    try:
        title = SOLUTIONS[1][0]
        card = _lane(page, "D").locator('[data-testid="adm-card"]').filter(has_text=title)
        expect(card.get_by_test_id("adm-status")).to_have_text("Proposed")
        card.get_by_label("Move %s to another status" % title).select_option("review")
        expect(card.get_by_test_id("adm-status")).to_have_text("Review", timeout=PAGE_TIMEOUT)
        page.reload(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        card = _lane(page, "D").locator('[data-testid="adm-card"]').filter(has_text=title)
        expect(card.get_by_test_id("adm-status")).to_have_text("Review", timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def _empty_org():
    """An organisation with nothing in it at all: no solutions, no tasks."""
    import uuid

    from app import create_app, db
    from app.models.organization import Organization
    from app.services.billing_plans import set_contract_plan

    suffix = uuid.uuid4().hex[:8]
    with create_app("testing").app_context():
        org = Organization(name="Empty Org %s" % suffix, slug="empty-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        set_contract_plan(org, "enterprise", None)
        db.session.commit()
        return org.id


def test_a_board_with_nothing_on_it_says_what_is_empty_and_what_to_do(browser, live_server, seeded):
    own_org = _empty_org()
    email = _make_enterprise_architect(own_org)
    _assert_signed_in_as_enterprise_architect(email)
    context, page = _open(browser, live_server, email, 1440)
    try:
        main = page.locator("main")
        expect(main.get_by_text("No solutions or tasks yet")).to_be_visible(timeout=PAGE_TIMEOUT)
        link = main.get_by_role("link", name=re.compile("Start a solution")).first
        expect(link).to_be_visible()
        link.click()
        page.wait_for_load_state("networkidle")
        assert "/architecture-journey" in page.url
    finally:
        context.close()
