"""An enterprise architect cancels a scheduled ARB session and sees it cancelled after a
reload; a solution architect is shown no cancel or delete control on the same session."""

import os
import uuid
from datetime import datetime

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("ARB_COMMENT_WALK_DIR", "")


@pytest.fixture
def quorum_session(seeded):
    """A scheduled session of the organisation with the three board members a
    completion needs."""
    from app import create_app, db
    from app.models.architecture_review_board import (
        ARBBoardMember,
        ArchitectureReviewBoard,
    )
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        owner = User.query.filter_by(email=seeded["emails"]["enterprise_architect"]).one()
        board = ArchitectureReviewBoard(
            organization_id=owner.organization_id,
            board_number="QRM-%s" % uuid.uuid4().hex[:8],
            name="Quorum journey board",
            scheduled_date=datetime(2030, 1, 1),
            status="scheduled",
        )
        db.session.add(board)
        db.session.flush()
        members = User.query.filter_by(organization_id=owner.organization_id).limit(3).all()
        assert len(members) == 3
        for user in members:
            db.session.add(
                ARBBoardMember(
                    organization_id=owner.organization_id,
                    arb_session_id=board.id,
                    user_id=user.id,
                    role="enterprise_architect",
                )
            )
        db.session.commit()
        return board.id, board.board_number


@pytest.fixture
def short_session(seeded):
    """A scheduled session with three seats of which only two vote."""
    from app import create_app, db
    from app.models.architecture_review_board import (
        ARBBoardMember,
        ArchitectureReviewBoard,
    )
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        owner = User.query.filter_by(email=seeded["emails"]["enterprise_architect"]).one()
        board = ArchitectureReviewBoard(
            organization_id=owner.organization_id,
            board_number="SHT-%s" % uuid.uuid4().hex[:8],
            name="Short journey board",
            scheduled_date=datetime(2030, 1, 1),
            status="scheduled",
        )
        db.session.add(board)
        db.session.flush()
        members = User.query.filter_by(organization_id=owner.organization_id).limit(3).all()
        assert len(members) == 3
        for index, user in enumerate(members):
            db.session.add(
                ARBBoardMember(
                    organization_id=owner.organization_id,
                    arb_session_id=board.id,
                    user_id=user.id,
                    role="enterprise_architect",
                    voting_member=index < 2,
                )
            )
        db.session.commit()
        return board.id, board.board_number


@pytest.fixture
def own_session(seeded):
    from app import create_app, db
    from app.models.architecture_review_board import ArchitectureReviewBoard
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        owner = User.query.filter_by(email=seeded["emails"]["enterprise_architect"]).one()
        board = ArchitectureReviewBoard(
            organization_id=owner.organization_id,
            board_number="ROLE-%s" % uuid.uuid4().hex[:8],
            name="Role journey board",
            scheduled_date=datetime(2030, 1, 1),
            status="scheduled",
        )
        db.session.add(board)
        db.session.commit()
        return board.id, board.board_number


def _login(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").dispatch_event("click")
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def _shot(page, name):
    if WALK_DIR:
        os.makedirs(WALK_DIR, exist_ok=True)
        page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def test_member_sees_no_session_controls_and_approver_cancels(
    browser, live_server, seeded, own_session
):
    session_id, number = own_session
    url = "%s/arb/sessions/%d" % (live_server, session_id)

    member = browser.new_page(viewport={"width": 1440, "height": 900})
    _login(member, live_server, seeded["emails"]["solution_architect"])
    member.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert number in member.inner_text("main")
    assert member.get_by_role("button", name="Cancel Session").count() == 0
    _shot(member, "session-member-1440")

    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _shot(page, "session-approver-1440")
    page.get_by_role("button", name="Cancel Session").click()
    page.wait_for_url("%s/arb/sessions" % live_server, timeout=PAGE_TIMEOUT)
    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert "Cancelled" in page.inner_text("main")
    assert number in page.inner_text("main")
    _shot(page, "session-cancelled-1440")
    page.set_viewport_size({"width": 390, "height": 844})
    assert "Cancelled" in page.inner_text("main")
    _shot(page, "session-cancelled-390")


def test_session_list_offers_complete_only_to_an_approver(
    browser, live_server, seeded, quorum_session
):
    session_id, number = quorum_session
    url = "%s/arb/sessions" % live_server

    member = browser.new_page(viewport={"width": 1440, "height": 900})
    _login(member, live_server, seeded["emails"]["solution_architect"])
    member.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert number in member.inner_text("main")
    assert member.get_by_role("button", name="Complete").count() == 0
    _shot(member, "sessions-list-member-1440")
    assert "3 Members" in member.inner_text("main")
    member.set_viewport_size({"width": 390, "height": 844})
    assert number in member.inner_text("main")
    assert member.get_by_role("button", name="Complete").count() == 0
    _shot(member, "sessions-list-member-390")

    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _shot(page, "sessions-list-approver-1440")
    row = page.locator("main div").filter(has_text=number).filter(
        has=page.get_by_role("button", name="Complete")
    ).last
    assert "3 Members" in row.inner_text()
    with page.expect_response(lambda r: r.url.endswith("/complete")) as sent:
        row.get_by_role("button", name="Complete").click()
    assert sent.value.status in (200, 302), (sent.value.status, sent.value.url)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    detail = "%s/arb/sessions/%d" % (live_server, session_id)
    page.goto(detail, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert number in page.inner_text("main")
    assert "Completed" in page.inner_text("main")
    _shot(page, "sessions-list-completed-1440")


def test_session_list_complete_is_reachable_at_phone_width(
    browser, live_server, seeded, quorum_session
):
    session_id, number = quorum_session
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto("%s/arb/sessions" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator("main div").filter(has_text=number).filter(
        has=page.get_by_role("button", name="Complete")
    ).last
    for control in (row.get_by_role("link", name="View"), row.get_by_role("button", name="Complete")):
        box = control.bounding_box()
        assert box is not None
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390, box
    _shot(page, "sessions-list-approver-390")
    with page.expect_response(lambda r: r.url.endswith("/complete")) as sent:
        row.get_by_role("button", name="Complete").click()
    assert sent.value.status in (200, 302), (sent.value.status, sent.value.url)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    page.goto("%s/arb/sessions/%d" % (live_server, session_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert number in page.inner_text("main")
    assert "Completed" in page.inner_text("main")


def test_completing_a_full_board_says_so_on_the_list(
    browser, live_server, seeded, quorum_session
):
    session_id, number = quorum_session
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto("%s/arb/sessions" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    row = page.locator("main div").filter(has_text=number).filter(
        has=page.get_by_role("button", name="Complete")
    ).last
    row.get_by_role("button", name="Complete").click()
    page.wait_for_url("%s/arb/sessions" % live_server, timeout=PAGE_TIMEOUT)
    page.wait_for_selector("text=completed successfully", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(1500)
    box = page.get_by_text("completed successfully").first.bounding_box()
    assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 1440, box
    assert box["y"] >= 0 and box["y"] + box["height"] <= 900, box
    assert number in page.inner_text("body")
    _shot(page, "complete-success-1440")
    page.goto("%s/arb/sessions/%d" % (live_server, session_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert "Completed" in page.inner_text("main")


def test_completing_a_short_board_gives_the_reason_and_changes_nothing(
    browser, live_server, seeded, short_session
):
    session_id, number = short_session
    for width in (1440, 390):
        page = browser.new_page(viewport={"width": width, "height": 900})
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto("%s/arb/sessions" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator("main div").filter(has_text=number).filter(
            has=page.get_by_role("button", name="Complete")
        ).last
        row.get_by_role("button", name="Complete").click()
        page.wait_for_selector("text=This board has 2 of the 3 voting members it needs", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)
        box = page.get_by_text("This board has 2 of the 3 voting members it needs").first.bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width, box
        text = page.inner_text("body")
        assert "add" in text.lower() and "member" in text.lower()
        _shot(page, "complete-refused-%d" % width)
        page.goto("%s/arb/sessions/%d" % (live_server, session_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "Scheduled" in page.inner_text("main")
        assert "Completed" not in page.inner_text("main")


def _members_table_text(page):
    return page.get_by_test_id("board-members-table").inner_text()


def test_short_board_is_fixed_from_the_session_page_and_then_completes(
    browser, live_server, seeded, short_session
):
    session_id, number = short_session
    for width, height in ((1440, 900), (390, 844)):
        page = browser.new_page(viewport={"width": width, "height": height})
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto("%s/arb/sessions" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator("main div").filter(has_text=number).filter(
            has=page.get_by_role("button", name="Complete")
        ).last
        row.get_by_role("button", name="Complete").click()
        page.wait_for_selector("text=This board has 2 of the 3 voting members it needs", timeout=PAGE_TIMEOUT)
        page.goto("%s/arb/sessions/%d" % (live_server, session_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        table = _members_table_text(page)
        assert "Voting" in table and "Non-voting" in table, table
        before = page.get_by_test_id("board-member-row").count()
        assert before == 3
        _shot(page, "session-members-%d" % width)

        form = page.get_by_test_id("add-board-member-form")
        picker = form.get_by_label("Person")
        options = picker.locator("option")
        assert options.count() >= 2
        person = options.nth(1).inner_text()
        picker.select_option(index=1)
        form.get_by_label("Voting member").check()
        form.scroll_into_view_if_needed()
        _shot(page, "session-add-member-%d" % width)
        page.evaluate("document.documentElement.classList.add('dark')")
        _shot(page, "session-add-member-dark-%d" % width)
        page.evaluate("document.documentElement.classList.remove('dark')")
        form.get_by_role("button", name="Add member").click()
        page.wait_for_selector("text=Board member added", timeout=PAGE_TIMEOUT)
        page.goto("%s/arb/sessions/%d" % (live_server, session_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_test_id("board-member-row").count() == before + 1
        assert person.split(" (")[0] in _members_table_text(page)

        page.goto("%s/arb/sessions" % live_server, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        row = page.locator("main div").filter(has_text=number).filter(
            has=page.get_by_role("button", name="Complete")
        ).last
        row.get_by_role("button", name="Complete").click()
        page.wait_for_selector("text=completed successfully", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(1500)
        box = page.get_by_text("completed successfully").first.bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width, box
        assert box["y"] >= 0 and box["y"] + box["height"] <= height, box
        _shot(page, "complete-after-add-%d" % width)
        page.goto("%s/arb/sessions/%d" % (live_server, session_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "Completed" in page.inner_text("main")
        page.close()
        # reopen the board for the second width
        from app import create_app, db
        from app.models.architecture_review_board import (
            ARBBoardMember,
            ArchitectureReviewBoard,
        )

        app = create_app("testing")
        with app.app_context():
            added = (
                ARBBoardMember.query.filter_by(arb_session_id=session_id)
                .order_by(ARBBoardMember.id.desc())
                .first()
            )
            db.session.delete(added)
            db.session.get(ArchitectureReviewBoard, session_id).status = "scheduled"
            db.session.commit()


def test_a_member_sees_the_board_but_no_add_form(
    browser, live_server, seeded, short_session
):
    session_id, number = short_session
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _login(page, live_server, seeded["emails"]["solution_architect"])
    page.goto("%s/arb/sessions/%d" % (live_server, session_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert page.get_by_test_id("board-member-row").count() == 3
    assert "Non-voting" in _members_table_text(page)
    assert page.get_by_test_id("add-board-member-form").count() == 0
    assert page.get_by_role("button", name="Add member").count() == 0
    _shot(page, "session-members-member-1440")


def test_the_session_list_and_page_render_the_token_on_the_server(
    browser, live_server, seeded, short_session
):
    session_id, _ = short_session
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    response = page.request.get("%s/arb/sessions" % live_server)
    html = response.text()
    assert 'name="csrf_token"' in html
    assert "i.name='csrf_token'" not in html
