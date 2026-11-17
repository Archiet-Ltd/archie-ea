"""Browser journey for approvals queue poll — Viewer sees no poll, approver sees it."""

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD, ARCHETYPES


pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _login_as(page, base, email):
    """Sign in as a specific user."""
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    try:
        page.click("#submit", force=True, no_wait_after=True)
    except TypeError:
        page.locator("#submit").dispatch_event("click")
    try:
        page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)
    except Exception:
        pass
    assert "/account/login" not in page.url, "could not sign in as %s" % email


@pytest.fixture(scope="module")
def viewer_context(browser, live_server, seeded):
    """Create a Viewer user and return a signed-in context."""
    from app import create_app, db
    from app.models.user import Role, User
    import uuid

    app = create_app("testing")
    with app.app_context():
        Role.insert_roles()
        viewer_role = Role.query.filter_by(name="Viewer").first()
        if viewer_role is None:
            viewer_role = Role(name="Viewer", permissions=0, index="main", default=False)
            db.session.add(viewer_role)
            db.session.flush()

        org_id = seeded["ids"]["org"]
        suffix = uuid.uuid4().hex[:8]
        viewer_email = "smoke.viewer.%s@example.com" % suffix
        viewer = User(
            email=viewer_email,
            first_name="Smoke",
            last_name="Viewer",
            organization_id=org_id,
            enterprise_role="viewer",
            confirmed=True,
        )
        viewer.role = viewer_role
        viewer.password = PASSWORD
        db.session.add(viewer)
        db.session.commit()

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    login_page = ctx.new_page()
    _login_as(login_page, live_server, viewer_email)
    login_page.close()
    yield ctx
    ctx.close()


@pytest.fixture
def viewer_page(viewer_context):
    """A page for the Viewer user."""
    pg = viewer_context.new_page()
    pg.console_errors = []
    pg.page_errors = []
    pg.requests_to_queue = []
    pg.on("console", lambda m: pg.console_errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.page_errors.append(str(e)))
    pg.on("request", lambda r: pg.requests_to_queue.append(r.url) if "/ai-chat/approvals/queue" in r.url else None)
    yield pg
    pg.close()


@pytest.fixture(scope="module")
def approver_context(browser, live_server, seeded):
    """Use the existing enterprise_architect as an approver (has GENERAL permission)."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    login_page = ctx.new_page()
    _login_as(login_page, live_server, seeded["emails"]["enterprise_architect"])
    login_page.close()
    yield ctx
    ctx.close()


@pytest.fixture
def approver_page(approver_context):
    """A page for the approver user."""
    pg = approver_context.new_page()
    pg.console_errors = []
    pg.page_errors = []
    pg.requests_to_queue = []
    pg.on("console", lambda m: pg.console_errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: pg.page_errors.append(str(e)))
    pg.on("request", lambda r: pg.requests_to_queue.append(r.url) if "/ai-chat/approvals/queue" in r.url else None)
    yield pg
    pg.close()


def test_viewer_sees_no_approvals_queue_poll(viewer_page, live_server):
    """A Viewer's AI chat page makes no request to /ai-chat/approvals/queue."""
    viewer_page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    viewer_page.wait_for_timeout(2000)  # Wait for any poll to fire

    # Verify no request was made to the approvals queue
    assert viewer_page.requests_to_queue == [], (
        "Viewer made %d request(s) to /ai-chat/approvals/queue: %s"
        % (len(viewer_page.requests_to_queue), viewer_page.requests_to_queue)
    )

    # Verify the approvals badge/button is not in the DOM
    # The chat options button should exist but without the approvals badge
    chat_options = viewer_page.get_by_test_id("chat-options-trigger")
    assert chat_options.count() == 1, "Chat options button should exist for Viewer"

    # The approvals badge should not be present
    approvals_badge = viewer_page.locator("[data-testid='chat-overflow-menu'] .badge")
    assert approvals_badge.count() == 0, "Viewer should not see approvals badge"

    # The approvals modal should not be in the DOM
    approvals_modal = viewer_page.locator("#ai-chat-approvals-modal")
    assert approvals_modal.count() == 0, "Viewer should not have approvals modal in DOM"

    # No JavaScript errors
    errors = [e for e in viewer_page.console_errors + viewer_page.page_errors
              if "favicon" not in e.lower() and "503" not in e]
    assert not errors, "JavaScript errors on Viewer's /ai-chat: %s" % errors


def test_approver_sees_approvals_queue_poll(approver_page, live_server):
    """An approver's AI chat page polls /ai-chat/approvals/queue and shows the badge."""
    approver_page.goto(live_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    approver_page.wait_for_timeout(3500)  # Wait for at least one poll (30s interval, but initial fetch is immediate)

    # Verify at least one request was made to the approvals queue
    assert len(approver_page.requests_to_queue) >= 1, (
        "Approver made no request to /ai-chat/approvals/queue"
    )

    # Verify the approvals badge/button is in the DOM
    chat_options = approver_page.get_by_test_id("chat-options-trigger")
    assert chat_options.count() == 1, "Chat options button should exist for approver"

    # The approvals modal should be in the DOM
    approvals_modal = approver_page.locator("#ai-chat-approvals-modal")
    assert approvals_modal.count() == 1, "Approver should have approvals modal in DOM"

    # No JavaScript errors
    errors = [e for e in approver_page.console_errors + approver_page.page_errors
              if "favicon" not in e.lower() and "503" not in e]
    assert not errors, "JavaScript errors on approver's /ai-chat: %s" % errors