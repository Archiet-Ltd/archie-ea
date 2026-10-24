"""The ADM board shows each solution's own deliverables against the phase's TOGAF checklist.

The goal, as the Enterprise Architect: "what has each of my solutions produced
in its phase, what is still to start, and tick off what is finished". Runs
against the demonstration organisation's own seed: the three solutions and the
seven deliverables it creates, with no rows inserted by the test.

Each journey signs in as an Enterprise Architect on the Viewer platform role
(never an administrator) and asserts that before it clicks anything.
"""
import os
import pathlib
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

BOARD = "/adm-kanban/"
SHOTS = pathlib.Path(os.environ.get("ADM_DELIVERABLES_SHOTS", "/tmp/adm-deliverables-shots"))
DEMO_USER = "demo@lantern-quay.example.com"
ARCHITECT = "adm.deliverables.architect@example.com"

CALIBRATION = "Field Calibration Platform"
SEVEN = [
    "Calibration process model", "Stakeholder impact assessment", "Integration architecture",
    "Supplier onboarding runbook", "Security review", "Migration roadmap", "Cut-over plan",
]


@pytest.fixture(scope="module")
def demo_architect(live_server):
    """The demonstration organisation, seeded by the product's own seed, and an architect in it."""
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.user import Role, User

    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    app = create_app("testing")
    try:
        with app.app_context():
            seed_demo_company()
            org_id = User.query.filter_by(email=DEMO_USER).one().organization_id
            if not User.query.filter_by(email=ARCHITECT).first():
                Role.insert_roles()
                user = User(
                    email=ARCHITECT, first_name="Demo", last_name="Architect", organization_id=org_id,
                    enterprise_role="enterprise_architect", confirmed=True,
                    role=Role.query.filter_by(name="Viewer").first(),
                )
                user.password = PASSWORD
                db.session.add(user)
                db.session.commit()
    finally:
        del os.environ["DEMO_USER_PASSWORD"]
    return ARCHITECT


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


def _open(browser, live_server, email, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    _login(page, live_server, email)
    page.goto(live_server + BOARD, timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="ADM Kanban", level=1)).to_be_visible(timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle")
    return context, page


def _shot(page, name, width):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("%s-%d.png" % (name, width))), full_page=True)


def _card(page, title):
    return page.locator('[data-testid="adm-card"]').filter(has_text=title).first


def _chip(page, title):
    return _card(page, title).get_by_test_id("adm-deliverables-chip")


@pytest.mark.parametrize("width", [1440, 390])
def test_chip_and_dialog_show_the_solutions_own_deliverables_against_the_checklist(
        browser, live_server, demo_architect, width):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, width)
    try:
        # Two of the seven belong to this solution; the chip says so, not what the phase expects.
        expect(_chip(page, CALIBRATION)).to_have_text(re.compile(r"of 2 deliverables complete"))
        expect(_chip(page, "Supplier Collaboration Hub")).to_have_text(re.compile(r"of 3 deliverables complete"))
        _chip(page, CALIBRATION).click()
        dialog = page.get_by_role("dialog", name=re.compile("Deliverables — " + CALIBRATION))
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        checklist = dialog.get_by_test_id("adm-checklist")
        # Phase B's TOGAF checklist: the matched item shows the solution's deliverable and its state,
        # the others say they are not started.
        matched = checklist.locator("li").filter(has_text="Business Architecture document")
        expect(matched).to_contain_text("Calibration process model")
        expect(matched).to_contain_text("In progress")
        for untouched in ("Architecture Definition Document (business view)", "Gap Analysis"):
            expect(checklist.locator("li").filter(has_text=untouched)).to_contain_text("Not started")
        # A deliverable with no checklist item is listed rather than dropped.
        also = dialog.get_by_test_id("adm-also-produced")
        expect(also).to_contain_text("Stakeholder impact assessment")
        # The other solutions' deliverables are not shown here.
        expect(dialog).not_to_contain_text("Integration architecture")
        expect(dialog).not_to_contain_text("Migration roadmap")
        _shot(page, "dialog", width)
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        # A phase-F solution shows its own, already complete.
        _chip(page, "Event-Driven Operations Backbone").click()
        dialog = page.get_by_role("dialog", name=re.compile("Deliverables — Event-Driven"))
        expect(dialog.locator("li").filter(has_text="Finalized Implementation Plan")).to_contain_text("Complete")
    finally:
        context.close()


def test_the_deliverables_view_lists_all_seven(browser, live_server, demo_architect):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, 1440)
    try:
        page.get_by_label("Filter by type").select_option("deliverable")
        for name in SEVEN:
            expect(page.locator("main").get_by_text(name, exact=True).first).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("adm-status-summary")).to_contain_text("7 deliverables")
        # Each says which solution it is for.
        expect(page.locator('[data-testid="adm-card"]').filter(has_text="Security review")).to_contain_text(
            "For Supplier Collaboration Hub")
        expect(page.locator('[data-testid="adm-card"]')).to_have_count(7)
        _shot(page, "deliverables-view", 1440)
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_ticking_a_deliverable_complete_changes_the_count_and_survives_a_reload(
        browser, live_server, demo_architect, width):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, width)
    try:
        before = _chip(page, CALIBRATION).inner_text().strip()
        assert before.startswith("0 of 2"), before
        _chip(page, CALIBRATION).click()
        dialog = page.get_by_role("dialog", name=re.compile("Deliverables — " + CALIBRATION))
        tick = dialog.get_by_label("Mark Calibration process model complete")
        tick.check()
        expect(dialog).to_contain_text("1 of 2 deliverables complete", timeout=PAGE_TIMEOUT)
        expect(dialog.get_by_test_id("adm-checklist").locator("li").filter(
            has_text="Business Architecture document")).to_contain_text("Complete")
        expect(_chip(page, CALIBRATION)).to_have_text(re.compile(r"1 of 2 deliverables complete"))
        _shot(page, "ticked", width)

        page.reload()
        page.wait_for_load_state("networkidle")
        expect(_chip(page, CALIBRATION)).to_have_text(re.compile(r"1 of 2 deliverables complete"), timeout=PAGE_TIMEOUT)
        # The Deliverables view agrees: the same deliverable now sits in Done.
        page.get_by_label("Filter by type").select_option("deliverable")
        expect(page.locator('[data-testid="adm-card"]').filter(has_text="Calibration process model")
               ).to_contain_text("Complete", timeout=PAGE_TIMEOUT)
        page.get_by_label("Filter by type").select_option("")

        # Put it back so the next journey starts from the seed.
        _chip(page, CALIBRATION).click()
        dialog = page.get_by_role("dialog", name=re.compile("Deliverables — " + CALIBRATION))
        dialog.get_by_label("Mark Calibration process model complete").uncheck()
        expect(dialog).to_contain_text("0 of 2 deliverables complete", timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_no_false_empty_state_while_the_board_loads(browser, live_server, demo_architect):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    # Record every moment the page reads "No deliverables" while the cards are still on their way.
    watch = """
        window.__flashed = false;
        new MutationObserver(function () {
            if (document.body && document.body.innerText.indexOf('No deliverables') !== -1) window.__flashed = true;
        }).observe(document, {subtree: true, childList: true, characterData: true, attributes: true});
    """

    held = []

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    try:
        _login(page, live_server, demo_architect)
        page.add_init_script(watch)
        page.route(re.compile(r"/api/adm-kanban/v2/cards"), lambda route: held.append(route))
        page.goto(live_server + BOARD, timeout=PAGE_TIMEOUT)
        # While waiting, the board is a skeleton, not an empty board.
        expect(page.get_by_test_id("adm-board-loading")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "No deliverables" not in page.locator("main").inner_text()
        held[0].continue_()
        expect(_chip(page, CALIBRATION)).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.evaluate("window.__flashed") is False
    finally:
        context.close()


def test_status_chips_label_their_limits(browser, live_server, demo_architect):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, 1440)
    try:
        summary = page.get_by_test_id("adm-status-summary")
        text = summary.inner_text()
        assert re.search(r"\d+ of \d+ limit", text), text
        assert not re.search(r"\d+ / \d+", text), text
        chip = summary.locator('[title*="should be in"]').first
        assert "at once" in chip.get_attribute("title")
    finally:
        context.close()


def test_the_card_link_still_lands_on_the_solution(browser, live_server, demo_architect):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, 1440)
    try:
        _card(page, CALIBRATION).get_by_role("link", name=re.compile("Open")).click()
        page.wait_for_load_state("networkidle")
        assert "/solutions/" in page.url
        expect(page.get_by_text(CALIBRATION).first).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
