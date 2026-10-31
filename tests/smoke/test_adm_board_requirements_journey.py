"""The ADM board shows the requirements each solution serves, and lets an architect link more.

The goal, as the Enterprise Architect: "TOGAF work is requirement-driven, so I
see which requirements each piece of work serves, find the work behind one
requirement, and attach a requirement that is not yet attached". Runs against
the demonstration organisation's own seed (three solutions, eight requirements)
plus one requirement that is deliberately not linked to anything.

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
SHOTS = pathlib.Path(os.environ.get("ADM_REQUIREMENTS_SHOTS", "/tmp/adm-requirements-shots"))
DEMO_USER = "demo@lantern-quay.example.com"
ARCHITECT = "adm.requirements.architect@example.com"

CALIBRATION = "Field Calibration Platform"
SUPPLIER = "Supplier Collaboration Hub"
EVENTS = "Event-Driven Operations Backbone"
LOOSE = "Alert on a missed calibration deadline"
NINE_LANES = [
    "Preliminary", "Architecture Vision", "Business Architecture", "Information Systems Architectures",
    "Technology Architecture", "Opportunities and Solutions", "Migration Planning",
    "Implementation Governance", "Architecture Change Management",
]


@pytest.fixture(scope="module")
def demo_architect(live_server):
    """The demonstration organisation, an architect in it, and one requirement linked to nothing."""
    from app import create_app, db
    from app.commands.seed_demo_company import seed_demo_company
    from app.models.solution_architect_models import RequirementType, SolutionRequirement
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
            loose = SolutionRequirement.query.filter_by(organization_id=org_id, name=LOOSE).first()
            if loose is not None:
                # A rerun starts with it unattached again.
                loose.solution_id = None
                loose.togaf_phase = None
            else:
                db.session.add(SolutionRequirement(
                    name=LOOSE, description="Raise an alert before a calibration falls overdue.",
                    status="Draft", moscow_priority="SHOULD", requirement_type=RequirementType.FUNCTIONAL,
                    organization_id=org_id, owner="Ivo Reed"))
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


def _open(browser, live_server, email, width, theme=None):
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
    return _card(page, title).get_by_test_id("adm-requirements-chip")


def _panel(page):
    return page.get_by_test_id("adm-req-panel")


def _row(scope, name):
    return scope.get_by_test_id("adm-req-row").filter(has_text=name).first


def test_each_card_shows_the_requirements_it_serves(browser, live_server, demo_architect):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, 1440)
    try:
        expect(_chip(page, CALIBRATION)).to_have_text(re.compile(r"4 requirements"))
        expect(_chip(page, SUPPLIER)).to_have_text(re.compile(r"2 requirements"))
        expect(_chip(page, EVENTS)).to_have_text(re.compile(r"2 requirements"))
        # The top two by priority are named on the card; the rest wait in its detail.
        card = _card(page, CALIBRATION)
        expect(card).to_contain_text("Record the as-found reading for every instrument")
        expect(card).not_to_contain_text("Export certificates as signed documents")
        _shot(page, "cards", 1440)
        card.get_by_role("button", name=CALIBRATION).first.click()
        detail = page.get_by_test_id("adm-detail-requirements")
        expect(detail).to_be_visible(timeout=PAGE_TIMEOUT)
        for name in ("Record the as-found reading for every instrument",
                     "Show a customer the status of an open request",
                     "Suggest the nearest qualified engineer",
                     "Export certificates as signed documents"):
            expect(detail).to_contain_text(name)
        expect(detail).not_to_contain_text("Suppliers confirm delivery dates in the portal")
    finally:
        context.close()


def test_the_panel_is_open_beside_the_board_and_figures_agree(browser, live_server, demo_architect):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, 1440)
    try:
        panel = _panel(page)
        expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)
        # Beside the board, not below it.
        board_box = page.get_by_test_id("adm-lane").first.bounding_box()
        panel_box = panel.bounding_box()
        assert panel_box["x"] > board_box["x"]
        assert abs(panel_box["y"] - board_box["y"]) < 400
        # Nine requirements: the eight seeded and the one not linked yet.
        expect(panel.get_by_test_id("adm-req-count")).to_have_text(re.compile(r"9 requirements"))
        expect(panel.get_by_test_id("adm-req-row")).to_have_count(9)
        row = _row(panel, "Suppliers confirm delivery dates in the portal")
        expect(row.get_by_test_id("adm-req-phase")).to_have_text("Information Systems Architectures")
        expect(row.get_by_test_id("adm-req-solution")).to_have_text("Serves " + SUPPLIER)
        row = _row(panel, "Reject certificates with a missing reference standard")
        expect(row.get_by_test_id("adm-req-phase")).to_have_text("Technology Architecture")
        expect(row.get_by_test_id("adm-req-solution")).to_have_text("Serves " + SUPPLIER)
        # The same figures as the cards: 4 + 2 + 2 linked, 1 not linked.
        assert 4 + 2 + 2 + 1 == 9
        expect(_row(panel, LOOSE).get_by_test_id("adm-req-solution")).to_have_text("Not linked to a solution")
        _shot(page, "panel", 1440)
    finally:
        context.close()


def test_choosing_a_requirement_highlights_the_cards_it_drives(browser, live_server, demo_architect):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, 1440)
    try:
        _row(_panel(page), "Suppliers confirm delivery dates in the portal").get_by_role(
            "button", name=re.compile("Show the work behind")).click()
        expect(_card(page, SUPPLIER)).to_have_attribute("data-highlighted", "true")
        expect(_card(page, CALIBRATION)).not_to_have_attribute("data-highlighted", "true")
        expect(_card(page, EVENTS)).not_to_have_attribute("data-highlighted", "true")
        _shot(page, "highlight", 1440)
    finally:
        context.close()


def test_linking_a_requirement_to_a_card_shows_on_both_after_a_reload(browser, live_server, demo_architect):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, 1440)
    try:
        expect(_chip(page, EVENTS)).to_have_text(re.compile(r"2 requirements"))
        row = _row(_panel(page), LOOSE)
        row.get_by_label(re.compile("Solution served by")).select_option(label=EVENTS)
        expect(_chip(page, EVENTS)).to_have_text(re.compile(r"3 requirements"), timeout=PAGE_TIMEOUT)
        page.reload()
        page.wait_for_load_state("networkidle")
        expect(_chip(page, EVENTS)).to_have_text(re.compile(r"3 requirements"), timeout=PAGE_TIMEOUT)
        row = _row(_panel(page), LOOSE)
        expect(row.get_by_test_id("adm-req-solution")).to_have_text("Serves " + EVENTS)
        # A requirement with no phase takes the phase of the solution it now serves.
        expect(row.get_by_test_id("adm-req-phase")).to_have_text("Migration Planning")
    finally:
        context.close()


def test_the_cycle_in_the_header_counts_each_phase_and_filters_the_board(browser, live_server, demo_architect):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, 1440)
    try:
        lanes = page.get_by_test_id("adm-lane")
        expect(lanes).to_have_count(9, timeout=PAGE_TIMEOUT)
        names = [lanes.nth(i).get_by_role("heading").first.inner_text() for i in range(9)]
        assert names == NINE_LANES, names
        ring = page.get_by_test_id("adm-cycle")
        expect(ring).to_be_visible()
        for phase, count in (("B", "1"), ("D", "1"), ("F", "1"), ("C", "0")):
            expect(ring.get_by_test_id("adm-cycle-phase-" + phase)).to_have_attribute(
                "aria-label", re.compile(r"^%s %s: .*%s card" % (phase, count, count)))
        ring.get_by_test_id("adm-cycle-phase-D").click()
        expect(lanes).to_have_count(1)
        expect(lanes.first.get_by_role("heading").first).to_have_text("Technology Architecture")
        expect(_card(page, SUPPLIER)).to_be_visible()
        expect(page.get_by_test_id("adm-card")).to_have_count(1)
        _shot(page, "cycle-d", 1440)
        # Choosing it again returns every phase.
        ring.get_by_test_id("adm-cycle-phase-D").click()
        expect(lanes).to_have_count(9)
    finally:
        context.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_the_cycle_labels_are_readable_and_a_focused_phase_is_named(browser, live_server, demo_architect, width):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, width)
    try:
        ring = page.get_by_test_id("adm-cycle")
        expect(ring).to_be_visible(timeout=PAGE_TIMEOUT)
        sizes = ring.locator("svg text").evaluate_all(
            "els => els.map(e => [e.textContent.trim(), parseFloat(getComputedStyle(e).fontSize)"
            " * e.ownerSVGElement.getScreenCTM().a])")
        assert len(sizes) == 9 * 2 + 2, sizes
        small = [pair for pair in sizes if pair[1] < 12]
        assert not small, "cycle labels under 12 px: %r" % small
        assert ring.locator("svg").bounding_box()["width"] >= 239
        expect(ring.get_by_test_id("adm-cycle-requirements")).to_contain_text("Requirements")
        ring.get_by_test_id("adm-cycle-phase-D").focus()
        expect(ring.get_by_test_id("adm-cycle-tip")).to_have_text("D: Technology Architecture")
        page.get_by_test_id("adm-cycle-phase-B").hover()
        expect(ring.get_by_test_id("adm-cycle-tip")).to_have_text("B: Business Architecture")
        _shot(page, "cycle-large", width)
    finally:
        context.close()


def test_at_390_the_requirements_open_in_a_sheet(browser, live_server, demo_architect):
    _assert_signed_in_as_enterprise_architect(demo_architect)
    context, page = _open(browser, live_server, demo_architect, 390)
    try:
        expect(_panel(page)).to_be_hidden()
        # Page does not scroll sideways; the lanes scroll in their own region.
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.get_by_test_id("adm-req-toggle").click()
        sheet = page.get_by_test_id("adm-req-sheet")
        expect(sheet).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(sheet.get_by_test_id("adm-req-row")).to_have_count(9)
        _shot(page, "sheet", 390)
        _row(sheet, "Retry a failed event delivery three times").get_by_role(
            "button", name=re.compile("Show the work behind")).click()
        # Choosing one closes the sheet so the highlighted card can be seen.
        expect(sheet).to_be_hidden()
        expect(_card(page, EVENTS)).to_have_attribute("data-highlighted", "true")
    finally:
        context.close()


def test_an_organisation_with_no_requirements_is_told_what_to_do(browser, live_server, demo_architect):
    from app import create_app, db
    from app.models.solution_models import Solution

    from .test_adm_board_phase_lanes import _empty_org, _make_enterprise_architect

    org_id = _empty_org()
    email = _make_enterprise_architect(org_id)
    with create_app("testing").app_context():
        db.session.add(Solution(name="Lone Solution", description="No requirements yet.",
                                organization_id=org_id, governance_status="draft", adm_phase="B"))
        db.session.commit()
    _assert_signed_in_as_enterprise_architect(email)
    context, page = _open(browser, live_server, email, 1440)
    try:
        panel = _panel(page)
        expect(panel).to_contain_text("No requirements recorded yet", timeout=PAGE_TIMEOUT)
        expect(_chip(page, "Lone Solution")).to_have_text(re.compile(r"No requirements"))
        link = panel.get_by_role("link", name=re.compile("Add a requirement"))
        link.click()
        page.wait_for_load_state("networkidle")
        assert "/enterprise/requirements-backlog" in page.url
        expect(page.get_by_role("heading", level=1).first).to_be_visible()
    finally:
        context.close()
