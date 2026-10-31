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


_CONTRAST_JS = """() => {
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 1;
  const ctx = canvas.getContext('2d', {willReadFrequently: true});
  const rgba = (css) => {
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = '#000';
    ctx.fillStyle = css;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const title = [...document.querySelectorAll('main h1, main h2, main h3, main [data-slot=card-title]')]
    .find((el) => el.textContent.trim() === 'Participants');
  if (!title) return {error: 'no Participants title'};
  const fg = rgba(getComputedStyle(title).color);
  let bg = [255, 255, 255, 1];
  const layers = [];
  for (let el = title; el; el = el.parentElement) {
    const c = rgba(getComputedStyle(el).backgroundColor);
    if (c[3] > 0) layers.push(c);
    if (c[3] >= 1) break;
  }
  layers.reverse();
  if (!layers.length || layers[0][3] < 1) layers.unshift(rgba(getComputedStyle(document.body).backgroundColor));
  bg = layers[0];
  for (const l of layers.slice(1)) {
    bg = [0, 1, 2].map((i) => l[i] * l[3] + bg[i] * (1 - l[3])).concat([1]);
  }
  const a = lum(fg), b = lum(bg);
  return {ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05), dark: document.documentElement.classList.contains('dark')};
}"""


def test_the_session_page_is_legible_in_the_real_dark_theme(
    browser, live_server, seeded, short_session
):
    session_id, _ = short_session
    for width, height in ((1440, 900), (390, 844)):
        context = browser.new_context(
            viewport={"width": width, "height": height}, color_scheme="dark"
        )
        page = context.new_page()
        _login(page, live_server, seeded["emails"]["enterprise_architect"])
        page.goto("%s/arb/sessions/%d" % (live_server, session_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        measured = page.evaluate(_CONTRAST_JS)
        assert measured.get("dark") is True, measured
        assert measured["ratio"] >= 4.5, measured
        form = page.get_by_test_id("add-board-member-form")
        form.scroll_into_view_if_needed()
        _shot(page, "session-add-member-dark-%d" % width)
        context.close()


def _post_member(page, base, session_id, **fields):
    response = page.request.post(
        "%s/arb/sessions/%d/add_member" % (base, session_id), form=fields
    )
    return response


def _member_count(session_id):
    from app import create_app
    from app.models.architecture_review_board import ARBBoardMember

    app = create_app("testing")
    with app.app_context():
        return ARBBoardMember.query.filter_by(arb_session_id=session_id).count()


def _other_person_id(seeded, session_id):
    """A user of the organisation who is not yet on the board."""
    from app import create_app
    from app.models.architecture_review_board import ARBBoardMember
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        seated = {
            m.user_id for m in ARBBoardMember.query.filter_by(arb_session_id=session_id)
        }
        owner = User.query.filter_by(email=seeded["emails"]["enterprise_architect"]).one()
        return (
            User.query.filter(
                User.organization_id == owner.organization_id, ~User.id.in_(seated)
            )
            .first()
            .id
        )


@pytest.mark.parametrize("role", ["", "not-a-seat", "x" * 80])
def test_adding_a_member_with_no_valid_seat_is_refused_plainly(
    browser, live_server, seeded, short_session, role
):
    session_id, _ = short_session
    person = _other_person_id(seeded, session_id)
    before = _member_count(session_id)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    response = _post_member(page, live_server, session_id, user_id=str(person), role=role, voting_member="on")
    assert response.ok
    text = response.text()
    assert "Choose a seat for the new member" in text
    assert "Error adding member" not in text
    assert _member_count(session_id) == before
    # the session is still usable afterwards: a valid seat is accepted
    ok = _post_member(page, live_server, session_id, user_id=str(person), role="data_architect", voting_member="on")
    assert "Board member added" in ok.text()
    assert _member_count(session_id) == before + 1


def test_adding_a_person_from_another_organisation_is_refused(
    browser, live_server, seeded, short_session
):
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import User

    session_id, _ = short_session
    app = create_app("testing")
    with app.app_context():
        # One standing second organisation, found by key: the audit trail refers
        # to organisations, so a run does not delete the one it made.
        other = Organization.query.filter_by(slug="outside-board-test").first()
        if other is None:
            other = Organization(name="Outside board test", slug="outside-board-test")
            db.session.add(other)
            db.session.flush()
        outsider = User.query.filter_by(email="outsider.board@example.com").first()
        if outsider is None:
            outsider = User(
                email="outsider.board@example.com",
                first_name="Out",
                last_name="Sider",
                organization_id=other.id,
                confirmed=True,
            )
            db.session.add(outsider)
        db.session.commit()
        outsider_id = outsider.id
    before = _member_count(session_id)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    response = _post_member(page, live_server, session_id, user_id=str(outsider_id), role="data_architect", voting_member="on")
    assert "That person is not a member of this organisation." in response.text()
    assert _member_count(session_id) == before


def test_a_non_approver_cannot_add_a_member(browser, live_server, seeded, short_session):
    session_id, _ = short_session
    person = _other_person_id(seeded, session_id)
    before = _member_count(session_id)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _login(page, live_server, seeded["emails"]["solution_architect"])
    response = _post_member(page, live_server, session_id, user_id=str(person), role="data_architect", voting_member="on")
    assert response.status == 403, response.status
    assert _member_count(session_id) == before


def test_an_empty_secretary_is_explained_not_printed_as_none(
    browser, live_server, seeded, own_session
):
    session_id, _ = own_session
    page = browser.new_page(viewport={"width": 390, "height": 844})
    _login(page, live_server, seeded["emails"]["enterprise_architect"])
    page.goto("%s/arb/sessions/%d" % (live_server, session_id), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    secretary = page.locator("dt", has_text="Secretary").locator("xpath=following-sibling::dd")
    assert secretary.inner_text().strip() == "Unassigned"
    chair = page.locator("dt", has_text="Chair").locator("xpath=following-sibling::dd")
    assert chair.inner_text().strip() == "Unassigned"
    assert "None" not in page.get_by_text("Participants").locator("xpath=ancestor::*[contains(@class,'card') or @data-slot='card'][1]").inner_text()
    _shot(page, "session-participants-390")
