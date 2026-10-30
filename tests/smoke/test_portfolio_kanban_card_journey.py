"""A portfolio manager opens the Portfolio Kanban and reads it the way a SAFe
portfolio is read: every epic card says whether it is a Business or an Enabler
epic and who owns it, the Portfolio Backlog says it is sorted by WSJF and is,
and every column states the rule for leaving it. On a phone the board keeps
its shape and scrolls sideways.

Runs in the demonstration organisation, whose epics carry a type and an owner."""
import os
import pathlib
import re

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import sign_in
from .test_seeded_boards_journey import demo_org  # noqa: F401  (fixture)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

BOARD = "/safe/portfolio/"
SHOTS = pathlib.Path(os.environ.get("SAFE3C_SHOTS", "/tmp/safe3c-shots"))
COLUMNS = ["Funnel", "Reviewing", "Analyzing", "Portfolio Backlog", "Implementing", "Done"]
EXIT_RULES = {
    "Funnel": "Epic idea captured with its hypothesis",
    "Reviewing": "Hypothesis refined and ready for analysis",
    "Analyzing": "Lean business case and WSJF ready",
    "Portfolio Backlog": "Approved and waiting for capacity",
    "Implementing": "Minimum viable product released",
    "Done": "Outcome delivered or the epic stopped",
}
# A seeded epic whose type and owner the journey checks by name.
KNOWN_EPIC = ("Operational Resilience Programme", "Enabler", "Sage ITOps")
MANAGER = "pm.board@lantern-quay.example.com"
MANAGER_ADMIN = "pm.admin@lantern-quay.example.com"


@pytest.fixture(scope="module")
def managers(demo_org):  # noqa: F811
    """A portfolio manager, and one who also administers the organisation."""
    from app import create_app, db
    from app.models.org_role import OrgRole
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        demo = User.query.filter_by(email="demo@lantern-quay.example.com").one()
        role = Role.query.filter_by(name="Architect").one()
        for email, admin in ((MANAGER, False), (MANAGER_ADMIN, True)):
            if User.query.filter_by(email=email).first():
                continue
            user = User(email=email, first_name="Pat", last_name="Admin" if admin else "Manager",
                        organization_id=demo.organization_id, enterprise_role="portfolio_manager",
                        confirmed=True)
            user.role = role
            user.is_org_admin = admin
            user.password = PASSWORD
            db.session.add(user)
            db.session.commit()
            if admin:
                OrgRole.set_role(demo.organization_id, user.id, "org_admin", granted_by_id=user.id)
                db.session.commit()
    return MANAGER


def _open(browser, live_server, email, width=1440, height=900, scheme="light"):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    page = context.new_page()
    sign_in(page, live_server, email)
    page.goto(live_server + BOARD, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    page.wait_for_selector(".kanban-card", timeout=PAGE_TIMEOUT)
    return context, page


def _column(page, state):
    return page.locator(".kanban-column[data-state='%s']" % state)


def test_every_card_shows_its_epic_type_and_owner(browser, live_server, managers):
    context, page = _open(browser, live_server, managers)
    try:
        cards = page.locator(".kanban-card")
        assert cards.count() == 12, cards.count()
        for i in range(cards.count()):
            card = cards.nth(i)
            title = card.locator("h3").inner_text()
            kind = card.locator("[data-testid='epic-type']").inner_text().strip()
            owner = card.locator("[data-testid='epic-owner']").inner_text().strip()
            assert kind in ("Business", "Enabler"), (title, kind)
            assert re.search(r"[A-Za-z]{2,}", owner) and owner != "—", (title, owner)
        name, expected_type, expected_owner = KNOWN_EPIC
        card = page.locator(".kanban-card").filter(has_text=name)
        assert card.locator("[data-testid='epic-type']").inner_text().strip() == expected_type
        assert expected_owner in card.locator("[data-testid='epic-owner']").inner_text()
        # The owner is shown by initials and name.
        initials = "".join(part[0] for part in expected_owner.split())
        assert initials in card.locator("[data-testid='epic-owner']").inner_text()
    finally:
        context.close()


def test_owner_name_lands_on_a_page_for_the_person_who_can_open_it(browser, live_server, managers):
    # A portfolio manager has no page to open a person on, so the name is not a
    # link for them (a link would land on a refusal).
    context, page = _open(browser, live_server, managers)
    try:
        assert page.locator("[data-testid='epic-owner'] a").count() == 0
    finally:
        context.close()
    # An administrator of the organisation gets the name as a link, and it opens
    # the team page with that person on it.
    context, page = _open(browser, live_server, MANAGER_ADMIN)
    try:
        link = page.locator(".kanban-card").filter(has_text=KNOWN_EPIC[0]).locator("[data-testid='epic-owner'] a")
        assert link.count() == 1
        link.click()
        page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)
        assert "/team" in page.url, page.url
        assert KNOWN_EPIC[2].split()[0] in page.locator("body").inner_text()
    finally:
        context.close()


def test_portfolio_backlog_says_it_is_sorted_by_wsjf_and_is(browser, live_server, managers):
    context, page = _open(browser, live_server, managers)
    try:
        backlog = _column(page, "Portfolio Backlog")
        assert "Sorted by WSJF" in backlog.inner_text()
        for state in COLUMNS:
            if state != "Portfolio Backlog":
                assert "Sorted by WSJF" not in _column(page, state).locator("header, .kanban-column-header").first.inner_text()
        scores = [
            float(re.search(r"WSJF:\s*([\d.]+)", t).group(1))
            for t in backlog.locator(".kanban-card").all_inner_texts()
        ]
        assert len(scores) == 3 and scores == sorted(scores, reverse=True), scores
    finally:
        context.close()


def test_every_column_states_its_exit_rule_under_its_header(browser, live_server, managers):
    context, page = _open(browser, live_server, managers)
    try:
        for state in COLUMNS:
            col = _column(page, state)
            rule = col.locator("[data-testid='column-exit-rule']")
            assert rule.inner_text().strip() == EXIT_RULES[state], (state, rule.inner_text())
            heading_box = col.get_by_role("heading", name=state, exact=True).bounding_box()
            rule_box = rule.bounding_box()
            assert rule_box["y"] > heading_box["y"], state
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / "board-1440-light.png"), full_page=True)
    finally:
        context.close()


def test_on_a_phone_the_board_scrolls_sideways_and_keeps_its_shape(browser, live_server, managers):
    context, page = _open(browser, live_server, managers, width=390, height=844, scheme="dark")
    try:
        scroller = page.locator("[data-kanban-scroll]")
        dims = scroller.evaluate("el => [el.scrollWidth, el.clientWidth]")
        assert dims[0] > dims[1] + 200, dims
        names = [h.strip() for h in page.locator(".kanban-column h2").all_inner_texts()]
        assert names == COLUMNS, names
        xs = [_column(page, s).bounding_box()["x"] for s in COLUMNS]
        assert xs == sorted(xs) and len(set(round(x) for x in xs)) == 6, xs
        scroller.evaluate("el => { el.scrollLeft = el.scrollWidth; }")
        assert scroller.evaluate("el => el.scrollLeft") > 100
        page.evaluate("document.querySelector('[data-kanban-scroll]').scrollLeft = 0")
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / "board-390-dark.png"))
    finally:
        context.close()
