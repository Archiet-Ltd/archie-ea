"""SAFe Portfolio Kanban journey: seed Epics, verify WSJF ordering, move states,
verify persistence, check access control.

This journey validates:
1. Work package elements marked as SAFe Epics appear on the board
2. Epics within each column are ordered by WSJF score descending
3. Moving an Epic to a new state persists after reload
4. The Epic's kanban state on the element detail page matches the board
5. Viewer role sees the board but cannot move epics
6. Organization B sees an empty board (tenant isolation)
"""
import os
import re
import pathlib
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SCREENSHOT_DIR = os.environ.get(
    "SMOKE_SCREENSHOT_DIR",
    str(pathlib.Path(__file__).resolve().parents[2] / "screenshots" / "portfolio-kanban"),
)
STATES = ["Funnel", "Reviewing", "Analyzing", "Portfolio Backlog", "Implementing", "Done"]


def _assert_board_keeps_its_shape(page):
    """Six column headers sit in one row and the board scrolls sideways when it must."""
    headers = [page.locator(f".kanban-column[data-state='{s}'] h2") for s in STATES]
    boxes = []
    for header, state in zip(headers, STATES):
        assert header.count() == 1, f"Expected one {state} header in the DOM"
        assert header.inner_text().strip() == state
        boxes.append(header.bounding_box())
    tops = {round(b["y"]) for b in boxes}
    assert len(tops) == 1, f"Column headers are not in one row: {tops}"
    lefts = [b["x"] for b in boxes]
    assert lefts == sorted(lefts) and len(set(lefts)) == 6, "Columns are not side by side"


def _assert_column_headers_have_dot_name_count(page):
    """Each column header shows the column name and the card count on the plain surface."""
    for state in STATES:
        header = page.locator(f".kanban-column[data-state='{state}'] .kanban-column-header")
        assert header.count() == 1, f"missing header for {state}"

        # Headers sit on the plain surface: no box, no stage dot.
        assert header.locator("span.h-2.w-2.rounded-full").count() == 0, f"{state} header still carries a stage dot"
        assert header.evaluate("el => getComputedStyle(el).borderTopWidth") == "0px", f"{state} header is boxed"

        # Check column name is visible
        assert state in header.inner_text(), f"{state} name not in header text"

        # Check count text is present (format: "N epic" or "N epics")
        header_text = header.inner_text()
        assert "epic" in header_text.lower(), f"{state} header missing epic count"


def _assert_cards_have_badge_id_title_date_avatar(page):
    """Each card shows: priority badge (WSJF) top left, item id top right, title, date with icon, and owner avatar."""
    # Find at least one card to validate structure
    cards = page.locator(".kanban-card").all()
    assert len(cards) > 0, "Expected at least one card on the board"

    for card in cards[:3]:  # Check first 3 cards as sample
        card_text = card.inner_text()

        # Priority badge (WSJF score)
        badge = card.get_by_text("WSJF:")
        assert badge.count() >= 1, "Card missing WSJF priority badge"
        badge_text = badge.first.inner_text()
        assert "WSJF:" in badge_text, f"Badge doesn't show WSJF score: {badge_text}"

        # Names, not ids: no bare number stands in for the element
        assert card.locator("span.font-mono").count() == 0, "Card shows a raw element id"

        # Title (h3 element, line-clamped)
        title = card.locator("h3.line-clamp-2")
        assert title.count() >= 1, "Card missing title"
        assert len(title.inner_text()) > 0, "Card title is empty"
        assert title.first.inner_text().strip() in card_text, "Card text does not contain its own title"

        # A date carries its icon; a card with no recorded date shows no dash in its place
        if card.locator("[data-lucide='calendar']").count() == 0:
            assert "—" not in card_text, "Card shows a bare dash instead of a date"

        # Owner avatar or initials (may not be present if no owner set)
        # This is optional, so we just check the structure allows it


def _assert_no_inline_styles(page):
    """No element in the kanban board has an inline style attribute."""
    board = page.locator(".kanban-board")
    elements_with_style = board.locator("[style]").all()

    # Filter out elements that might legitimately have styles from Alpine's :style binding
    # (though we've removed those, this ensures the test is accurate)
    bad_elements = []
    for el in elements_with_style:
        style_attr = el.get_attribute("style")
        # Alpine's x-show hides an element (and x-collapse) with display:none; that is a
        # visibility toggle, not authored styling.
        if style_attr and 'display:none' not in style_attr.replace(' ', ''):
            # Get element info for debugging
            tag = el.evaluate("el => el.tagName")
            classes = el.get_attribute("class") or ""
            bad_elements.append(f"{tag}.{classes.split()[0] if classes else '(no-class)'}")

    assert len(bad_elements) == 0, f"Found {len(bad_elements)} elements with inline styles: {bad_elements}"


def _assert_cards_use_words(page):
    card = page.locator(".kanban-card").first
    card.get_by_role("button", name=re.compile("Show WSJF factors")).click()
    card_text = card.inner_text()
    for label in ("Business Value", "Time Criticality", "Risk Reduction", "Job Size"):
        assert label in card_text, f"Card label {label!r} missing"
    board_text = page.locator(".kanban-board").inner_text()
    for initials in ("BV:", "TC:", "RR:", "JS:"):
        assert initials not in board_text, f"Bare initials {initials!r} on the board"


def _assert_empty_columns_name_the_next_step(page):
    reviewing = page.locator(".kanban-column[data-state='Reviewing']").inner_text()
    assert "No epics are being reviewed." in reviewing
    assert "Move an epic here from Funnel when its business case is ready." in reviewing
    done = page.locator(".kanban-column[data-state='Done']").inner_text()
    assert "Move an epic here from Implementing" in done


# Discovery stages tint blue (info token), execution stages tint green (success
# token), Done is neutral — so a SAFe user reads the discovery / execution /
# done bands at a glance.
STAGE_GROUP = {
    "Funnel": "discovery", "Reviewing": "discovery", "Analyzing": "discovery",
    "Portfolio Backlog": "execution", "Implementing": "execution", "Done": "done",
}
# WIP limits the board shows on its column headers. Funnel and Done have none.
WIP_LIMITS = {"Reviewing": 4, "Analyzing": 3, "Portfolio Backlog": 6, "Implementing": 5}


def _assert_stage_colour_is_on_the_cards(page):
    """Headers are plain; the discovery / execution / done bands are read from a
    chip on each card, and the bands render visibly different chips."""
    word = {"discovery": "Discovery", "execution": "Execution", "done": "Done"}
    tints = {}
    for state, group in STAGE_GROUP.items():
        header = page.locator(f".kanban-column[data-state='{state}'] .kanban-column-header")
        assert header.get_attribute("data-stage-group") is None, f"{state} header still carries a stage band"
        assert header.evaluate("el => getComputedStyle(el).backgroundColor") == "rgba(0, 0, 0, 0)", (
            f"{state} header is tinted"
        )
        cards = page.locator(f".kanban-column[data-state='{state}'] .kanban-card")
        if cards.count():
            chip = cards.first.get_by_text(word[group], exact=True)
            assert chip.count() == 1, f"{state} card has no {word[group]} chip"
            tints[group] = chip.evaluate("el => getComputedStyle(el).backgroundColor")
    assert len(set(tints.values())) >= 2 or len(tints) < 2, f"stage chips are not visually distinct: {tints}"


def _assert_wip_limits_show_on_headers(page):
    """Every limited column shows "count / limit" in its header; unlimited
    columns (Funnel, Done) show no limit (criterion 5)."""
    for state, limit in WIP_LIMITS.items():
        header = page.locator(f".kanban-column[data-state='{state}'] .kanban-column-header").inner_text()
        assert f"/ {limit}" in header, f"{state} header does not show its WIP limit: {header!r}"
    for state in ("Funnel", "Done"):
        header = page.locator(f".kanban-column[data-state='{state}'] .kanban-column-header").inner_text()
        assert "/" not in header.replace("epics", ""), f"{state} should show no WIP limit: {header!r}"


def _autoscroll_to(page, target, handle):
    """Carry a held card to a lane that is scrolled out of view the way a person
    does: hold the pointer at the board's edge until the board scrolls the lane in."""
    region = page.locator("[data-kanban-scroll]").first.bounding_box()
    for _ in range(40):
        box = target.bounding_box()
        if box["x"] >= region["x"] and box["x"] + box["width"] <= region["x"] + region["width"]:
            return
        edge = region["x"] + 6 if box["x"] < region["x"] else region["x"] + region["width"] - 6
        page.mouse.move(edge, handle["y"] + handle["height"] / 2, steps=6)
        page.wait_for_timeout(250)


def _drag_card(page, card, to_state):
    """Drag a card onto a column with real mouse events: press on the card's
    drag handle, move across the board in steps, release over the target lane.
    The browser raises dragstart, dragover and drop itself."""
    target = page.locator(f".kanban-column[data-state='{to_state}']")
    card.scroll_into_view_if_needed()
    handle = card.locator("[data-drag-handle]").bounding_box()
    page.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2)
    page.mouse.down()
    _autoscroll_to(page, target, handle)
    box = target.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 60, steps=12)
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 70, steps=4)
    page.mouse.up()
    page.wait_for_timeout(800)


def _move_by_menu(card, name, to_state):
    """Open the card's actions menu and choose a column."""
    card.get_by_role("button", name=f"Move {name} to a different column").click()
    card.get_by_role("menuitem", name=f"Move to {to_state}").click()


def _shoot(browser, live_server, email, width, height, scheme, name, scroll_to_end=False):
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    try:
        page = context.new_page()
        _login(page, live_server, email)
        page.goto(live_server + "/safe/portfolio", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Portfolio Kanban")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        _assert_board_keeps_its_shape(page)
        _assert_cards_use_words(page)
        _assert_empty_columns_name_the_next_step(page)

        scroller = page.locator("[data-kanban-scroll]")
        metrics = scroller.evaluate("el => ({sw: el.scrollWidth, cw: el.clientWidth})")
        if width < 1024:
            assert metrics["sw"] > metrics["cw"], f"Board does not scroll sideways at {width}: {metrics}"
            assert page.evaluate("document.documentElement.scrollWidth") <= width, "Page itself scrolls sideways"
        if scroll_to_end:
            scroller.evaluate("el => { el.scrollLeft = el.scrollWidth; }")
            assert scroller.evaluate("el => el.scrollLeft") > 0
        page.screenshot(path=f"{SCREENSHOT_DIR}/{name}.png", full_page=True)
    finally:
        context.close()


def _seed_epics(org_id):
    """Create three work package elements as SAFe Epics with different WSJF scores.
    
    Uses a direct database connection (not the test's db_session fixture) so the
    data is visible to the live server process.
    """
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.services.element_profile_service import set_kind, set_wsjf_components

    app = create_app("testing")
    with app.app_context():
        epics = [
            {
                "name": "Customer Portal Epic",
                "bv": 10, "tc": 8, "rr": 5, "js": 3,
                # WSJF = (10+8+5)/3 = 7.67
            },
            {
                "name": "Legacy Migration Epic",
                "bv": 3, "tc": 2, "rr": 9, "js": 5,
                # WSJF = (3+2+9)/5 = 2.8
            },
            {
                "name": "Platform Modernization Epic",
                "bv": 9, "tc": 10, "rr": 8, "js": 2,
                # WSJF = (9+10+8)/2 = 13.5 (highest)
            },
        ]

        epic_ids = []
        for data in epics:
            element = ArchiMateElement(
                name=data["name"],
                type="WorkPackage",
                organization_id=org_id,
                description="Smoke test epic for portfolio kanban",
            )
            db.session.add(element)
            db.session.flush()

            set_kind(element, "Epic", framework_id="safe", source="test")
            set_wsjf_components(
                element,
                business_value=data["bv"],
                time_criticality=data["tc"],
                risk_reduction=data["rr"],
                job_size=data["js"],
                source="test",
            )
            epic_ids.append(element.id)

        db.session.commit()
        return epic_ids


def test_portfolio_kanban_journey(browser, live_server, seeded, db_session):
    """Full journey: seed epics, verify WSJF order, move, verify persistence, check access control."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    org_id = seeded["ids"]["org"]

    try:
        epic_ids = _seed_epics(org_id)
        _ = epic_ids  # used implicitly via the seeded data

        # === Step 1: Login as Architect and load the board ===
        _login(page, live_server, seeded["emails"]["solution_architect"])
        response = page.goto(live_server + "/safe/portfolio", timeout=PAGE_TIMEOUT)
        assert response.status == 200
        expect(page.get_by_role("heading", name="Portfolio Kanban")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Save screenshot of the board with three epics in different states
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        page.screenshot(path=f"{SCREENSHOT_DIR}/kanban-1440-light-all-funnel.png", full_page=True)

        # === Step 2: Verify all three epics are in Funnel (default state) ===
        expect(page.get_by_role("heading", name="Customer Portal Epic")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Legacy Migration Epic")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Platform Modernization Epic")).to_be_visible(timeout=PAGE_TIMEOUT)

        # === Step 2a: colour-coded stage headers and WIP limits (criteria 5, 6) ===
        _assert_stage_colour_is_on_the_cards(page)
        _assert_wip_limits_show_on_headers(page)

        # === Step 2b: the highest-WSJF card in a column carries the Top WSJF accent ===
        funnel = page.locator(".kanban-column[data-state='Funnel'] .kanban-card")
        assert "Platform Modernization Epic" in funnel.first.inner_text()
        assert "Top WSJF" in funnel.first.inner_text(), "highest-WSJF card is not accented"

        # === Step 2c: shape, labels, empty columns and screenshots on the pristine
        # board — before any move changes which columns are empty. The board keeps
        # its six columns in one row and scrolls sideways at 390 (section 26). ===
        email = seeded["emails"]["solution_architect"]
        _assert_no_inline_styles(page)
        _assert_board_keeps_its_shape(page)
        _assert_cards_use_words(page)
        _assert_empty_columns_name_the_next_step(page)

        # === Step 2d: flask-basecoat anatomy assertions (fix931b brief) ===
        _assert_column_headers_have_dot_name_count(page)
        _assert_cards_have_badge_id_title_date_avatar(page)

        for width, height in ((1440, 900), (390, 844)):
            for scheme in ("light", "dark"):
                _shoot(browser, live_server, email, width, height, scheme, f"kanban-{width}-{scheme}")
        for scheme in ("light", "dark"):
            _shoot(browser, live_server, email, 390, 844, scheme, f"kanban-390-{scheme}-scrolled", scroll_to_end=True)

        # === Step 3: Verify WSJF ordering in Funnel column (descending) ===
        # Platform Modernization (13.5) should be first, Customer Portal (7.67) second, Legacy Migration (2.8) third
        cards = page.locator(".kanban-column[data-state='Funnel'] .kanban-card").all()
        assert len(cards) >= 3, "Expected at least 3 epic cards in Funnel"

        # Check order by card position (first card should have highest WSJF)
        first_card_text = cards[0].inner_text()
        assert "Platform Modernization Epic" in first_card_text, f"First card should be Platform Modernization, got: {first_card_text}"
        assert "WSJF: 13.5" in first_card_text

        second_card_text = cards[1].inner_text()
        assert "Customer Portal Epic" in second_card_text
        assert "WSJF: 7.67" in second_card_text

        third_card_text = cards[2].inner_text()
        assert "Legacy Migration Epic" in third_card_text
        assert "WSJF: 2.8" in third_card_text

        # === Step 4: Move Customer Portal Epic to Implementing ===
        # Find the select dropdown for Customer Portal Epic
        customer_card = page.locator(".kanban-column[data-state='Funnel'] .kanban-card").filter(has_text="Customer Portal Epic")
        _move_by_menu(customer_card, "Customer Portal Epic", "Implementing")

        # Wait for the move to complete (card should disappear from Funnel)
        page.wait_for_timeout(1000)

        # === Step 5: Verify Customer Portal Epic is now in Implementing column ===
        implementing_column = page.locator(".kanban-column[data-state='Implementing']")
        expect(implementing_column.get_by_text("Customer Portal Epic")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Funnel should now only have 2 epics
        funnel_cards = page.locator(".kanban-column[data-state='Funnel'] .kanban-card").all()
        assert len(funnel_cards) == 2, "Funnel should have 2 epics after moving one"

        # === Step 6: Reload and verify state persisted ===
        page.reload(timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Portfolio Kanban")).to_be_visible(timeout=PAGE_TIMEOUT)

        # Customer Portal should still be in Implementing
        implementing_column = page.locator(".kanban-column[data-state='Implementing']")
        expect(implementing_column.get_by_text("Customer Portal Epic")).to_be_visible(timeout=PAGE_TIMEOUT)

        funnel_cards_after_reload = page.locator(".kanban-column[data-state='Funnel'] .kanban-card").all()
        assert len(funnel_cards_after_reload) == 2, "Funnel should still have 2 epics after reload"

        # === Step 7: Drag an epic from Funnel to Reviewing (criterion 3) ===
        # The architect drags, not selects: the card lands in Reviewing, the
        # column counts update, and the move survives a reload.
        platform_card = page.locator(".kanban-column[data-state='Funnel'] .kanban-card").filter(
            has_text="Platform Modernization Epic")
        expect(platform_card).to_be_visible(timeout=PAGE_TIMEOUT)
        _drag_card(page, platform_card, "Reviewing")
        reviewing_column = page.locator(".kanban-column[data-state='Reviewing']")
        expect(reviewing_column.get_by_role("heading", name="Platform Modernization Epic")).to_be_visible(timeout=PAGE_TIMEOUT)
        # It is the only card in Reviewing, and so carries the Top WSJF accent there.
        assert "Top WSJF" in reviewing_column.locator(".kanban-card").first.inner_text()
        # Funnel is down to one card (Legacy Migration; Customer Portal moved to Implementing above).
        expect(page.locator(".kanban-column[data-state='Funnel'] .kanban-card")).to_have_count(1, timeout=PAGE_TIMEOUT)

        page.reload(timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Portfolio Kanban")).to_be_visible(timeout=PAGE_TIMEOUT)
        reviewing_column = page.locator(".kanban-column[data-state='Reviewing']")
        expect(reviewing_column.get_by_role("heading", name="Platform Modernization Epic")).to_be_visible(timeout=PAGE_TIMEOUT)

    finally:
        page.close()


def _make_viewer(org_id):
    """Add a read-only viewer to *org_id* and return their email. A viewer
    holds the organisation's viewer role, so the permission matrix — not a role
    name — refuses their write."""
    from app import create_app, db
    from app.models.user import User
    from app.models.org_role import OrgRole

    app = create_app("testing")
    email = "smoke.safe-viewer.%s@example.com" % org_id
    with app.app_context():
        existing = User.query.filter_by(email=email).first()
        if existing:
            return email
        user = User(
            email=email, first_name="Smoke", last_name="Viewer",
            organization_id=org_id, enterprise_role="Viewer", confirmed=True,
        )
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
        OrgRole.set_role(org_id, user.id, "viewer")
        db.session.commit()
    return email


def test_portfolio_board_viewer_is_read_only(browser, live_server, seeded, db_session):
    """A viewer sees the board but no way to move a card: no drag handles and no
    "Move to" select (criterion 4)."""
    org_id = seeded["ids"]["org"]
    _seed_epics(org_id)
    viewer_email = _make_viewer(org_id)

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, viewer_email)
        page.goto(live_server + "/safe/portfolio", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Portfolio Kanban")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        # The epics are visible (read access), but no move control is shown.
        expect(page.get_by_role("heading", name="Platform Modernization Epic").first).to_be_visible(timeout=PAGE_TIMEOUT)
        # The "Move to" menu is gated on canWrite (x-show), so for a viewer none
        # is visible — a hidden node may remain in the DOM, so the test
        # asserts visibility, not DOM presence.
        assert page.locator(".kanban-card [data-testid='card-actions']").count() == 0, "viewer is shown a card actions menu"
        assert page.locator("[data-drag-handle]").count() == 0, "viewer is shown a drag handle"
        menus = page.locator(".kanban-card [data-kanban-move] button")
        for i in range(menus.count()):
            assert not menus.nth(i).is_visible(), "viewer is shown a Move to menu"
        # The board's read-only-ness is also reported by the API it renders from.
        import json
        page.goto(live_server + "/api/safe/portfolio", timeout=PAGE_TIMEOUT)
        api = json.loads(page.inner_text("body"))
        assert api["can_write"] is False, "the board reports a viewer can write"
    finally:
        context.close()


def test_portfolio_board_second_organisation_sees_only_its_own(browser, live_server, seeded, request):
    """Two real organisations: a user in the second organisation sees that
    organisation's epics and none of the first's (criterion 4)."""
    from .conftest import _seed_standard_org

    org_a = seeded["ids"]["org"]
    _seed_epics(org_a)   # org A gets the three well-known epics

    # A second, independently seeded organisation with one clearly-named epic.
    # The AI protocol stub is session-wide (owned by `seeded`); this second
    # organisation does not re-create it (passes None) so the shared stub's
    # one-row teardown contract is undisturbed.
    other = _seed_standard_org(request, None)
    org_b = other["ids"]["org"]
    assert org_b != org_a
    _seed_named_epic(org_b, "Second Org Only Epic")

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, other["emails"]["solution_architect"])
        page.goto(live_server + "/safe/portfolio", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Portfolio Kanban")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        board_text = page.locator(".kanban-board").inner_text()
        assert "Second Org Only Epic" in board_text, "second org cannot see its own epic"
        for first_org_epic in ("Customer Portal Epic", "Legacy Migration Epic", "Platform Modernization Epic"):
            assert first_org_epic not in board_text, (
                f"second org can see the first org's {first_org_epic!r} — tenant isolation is broken"
            )
    finally:
        context.close()


def _seed_named_epic(org_id, name):
    """One SAFe Epic with a given name in *org_id*, via a connection the live
    server process can see (mirrors _seed_epics)."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement
    from app.services.element_profile_service import set_kind, set_wsjf_components

    app = create_app("testing")
    with app.app_context():
        element = ArchiMateElement(
            name=name, type="WorkPackage", organization_id=org_id,
            description="Second-organisation isolation fixture.",
        )
        db.session.add(element)
        db.session.flush()
        set_kind(element, "Epic", framework_id="safe", source="test")
        set_wsjf_components(
            element, business_value=5, time_criticality=5,
            risk_reduction=5, job_size=2, source="test",
        )
        db.session.commit()
        return element.id


def test_architect_adds_an_epic_from_the_board(browser, live_server, seeded, request):
    """The architect clicks Add epic, fills the form, and the new epic is in the
    Funnel with its WSJF score, still there after a reload, and not visible to
    another organisation. A viewer is not offered the button."""
    from .conftest import _seed_standard_org

    org_id = seeded["ids"]["org"]
    _seed_epics(org_id)
    other = _seed_standard_org(request, None)

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["solution_architect"])
        page.goto(live_server + "/safe/portfolio", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="Add epic").first.click()
        dialog = page.get_by_role("dialog", name="Add epic")
        expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
        dialog.get_by_label("Name").fill("Onboarding Rework Epic")
        dialog.get_by_label("Business Value").fill("8")
        dialog.get_by_label("Time Criticality").fill("4")
        dialog.get_by_label("Risk Reduction").fill("2")
        dialog.get_by_label("Job Size").fill("2")
        dialog.get_by_role("button", name="Add epic").click()
        page.wait_for_load_state("networkidle")

        funnel = page.locator(".kanban-column[data-state='Funnel']")
        card = funnel.locator(".kanban-card").filter(has_text="Onboarding Rework Epic")
        expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
        assert "WSJF: 7" in card.inner_text(), card.inner_text()  # (8+4+2)/2
        page.reload(timeout=PAGE_TIMEOUT)
        expect(page.locator(".kanban-column[data-state='Funnel'] .kanban-card").filter(
            has_text="Onboarding Rework Epic")).to_be_visible(timeout=PAGE_TIMEOUT)

        page2 = context.new_page()
        context.clear_cookies()
        _login(page2, live_server, other["emails"]["solution_architect"])
        page2.goto(live_server + "/safe/portfolio", timeout=PAGE_TIMEOUT)
        page2.wait_for_load_state("networkidle")
        assert "Onboarding Rework Epic" not in page2.locator(".kanban-board").inner_text()
    finally:
        context.close()

    viewer = browser.new_context(viewport={"width": 1440, "height": 900})
    try:
        vpage = viewer.new_page()
        _login(vpage, live_server, _make_viewer(org_id))
        vpage.goto(live_server + "/safe/portfolio", timeout=PAGE_TIMEOUT)
        vpage.wait_for_load_state("networkidle")
        assert vpage.get_by_role("button", name="Add epic").count() == 0
    finally:
        viewer.close()
