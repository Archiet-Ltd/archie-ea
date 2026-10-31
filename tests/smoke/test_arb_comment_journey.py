"""A solution architect comments on their own organisation's review and sees it
after a reload; another organisation's architect cannot add to it."""

import os
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = os.environ.get("ARB_COMMENT_WALK_DIR", "")


@pytest.fixture
def own_review(seeded):
    from app import create_app, db
    from app.models.architecture_review_board import ARBReviewItem
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        submitter = User.query.filter_by(email=seeded["emails"]["solution_architect"]).one()
        review = ARBReviewItem(
            organization_id=submitter.organization_id,
            review_number="CMT-%s" % uuid.uuid4().hex[:8],
            title="Comment journey review",
            review_type="architecture_change",
            status="draft",
            submitter_id=submitter.id,
        )
        db.session.add(review)
        db.session.commit()
        return review.id


@pytest.fixture
def other_org_architect(seeded):
    """A solution architect of a second organisation, made without touching the
    shared organisation's seeded rows (a second full seeding deletes the AI
    provider settings later smoke tests use)."""
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import Role, User

    tag = uuid.uuid4().hex[:6]
    email = "smoke.arb.second.%s@example.com" % tag
    app = create_app("testing")
    with app.app_context():
        second = Organization(name="Second %s" % tag, slug="second-%s" % tag)
        db.session.add(second)
        db.session.flush()
        user = User(email=email, first_name="Smoke", last_name="Second",
                    organization_id=second.id, enterprise_role="solution_architect",
                    confirmed=True)
        user.role = Role.query.filter_by(name="Architect").one()
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
    return email


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


def test_architect_comments_on_own_review_and_other_organisation_cannot(
    browser, live_server, seeded, own_review, other_org_architect
):
    note = "Own note %s" % uuid.uuid4().hex[:6]
    intruder = "Intruder note %s" % uuid.uuid4().hex[:6]

    page = browser.new_page(viewport={"width": 1440, "height": 900})
    _login(page, live_server, seeded["emails"]["solution_architect"])
    url = "%s/arb/reviews/%d" % (live_server, own_review)
    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    comments = page.locator("#comments")
    assert "No comments yet" in comments.inner_text()
    _shot(page, "comments-empty-1440")

    page.fill("#arb-comment-content", note)
    page.get_by_role("button", name="Add comment").click()
    page.wait_for_url(url, timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded")
    assert note in page.locator("#comments").inner_text()
    _shot(page, "comments-posted-1440")
    page.set_viewport_size({"width": 390, "height": 844})
    assert note in page.locator("#comments").inner_text()
    _shot(page, "comments-posted-390")

    # The other organisation's architect is refused and nothing is stored.
    foreign = browser.new_page()
    _login(foreign, live_server, other_org_architect)
    foreign.goto(live_server + "/arb/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    token = foreign.locator('meta[name="csrf-token"]').get_attribute("content")
    resp = foreign.request.post(
        url + "/comment",
        form={"content": intruder, "comment_type": "general"},
        headers={"X-CSRFToken": token},
    )
    assert resp.status == 404
    page.reload(wait_until="domcontentloaded")
    text = page.locator("#comments").inner_text()
    assert note in text and intruder not in text
