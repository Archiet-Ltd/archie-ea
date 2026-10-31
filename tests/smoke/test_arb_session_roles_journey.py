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
