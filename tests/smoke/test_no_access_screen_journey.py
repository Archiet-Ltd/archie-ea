"""Journey: every 403 and 404 a person can reach is the one library screen.

Goal: an invited Viewer who opens a page their role does not include (settings,
administration, an application's edit form) is told so in the same words, with
the same single way back, whichever page refused them; a missing page reads the
same signed in and signed out.
"""

import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

NO_ACCESS = "You don't have access to this page"
NOT_FOUND = "We couldn't find that page"
VIEWPORTS = [
    ("1440x900-light", {"width": 1440, "height": 900}, "light"),
    ("390x844-dark", {"width": 390, "height": 844}, "dark"),
]


@pytest.fixture(scope="module")
def viewer_email(live_server, seeded):
    from app import create_app, db
    from app.models.user import Role, User

    app = create_app("testing")
    with app.app_context():
        Role.insert_roles()
        role = Role.query.filter_by(name="Viewer").first()
        email = "errors.viewer.%s@example.com" % uuid.uuid4().hex[:8]
        user = User(email=email, first_name="Err", last_name="Viewer",
                    organization_id=seeded["ids"]["org"],
                    enterprise_role="viewer", confirmed=True)
        user.role = role
        user.password = PASSWORD
        db.session.add(user)
        db.session.commit()
    return email


def _sign_in(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").dispatch_event("click")
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def _ctx(browser, viewport, scheme):
    ctx = browser.new_context(viewport=viewport, color_scheme=scheme)
    ctx.set_default_timeout(PAGE_TIMEOUT)
    ctx.set_default_navigation_timeout(PAGE_TIMEOUT)
    return ctx


def _assert_library_screen(page, title):
    main = page.locator("main, [data-testid]").first
    body = page.inner_text("body")
    assert title in body, "expected %r, page says %r" % (title, body[:300])
    assert page.get_by_role("heading", name=title).count() == 1
    # the old template's bold status code heading is gone
    assert page.locator("h1.code").count() == 0
    assert "Go Back" not in body
    # one primary action, back to the dashboard
    primary = page.get_by_role("link", name="Go to the dashboard")
    assert primary.count() == 1, "expected one way back to the dashboard"
    assert "/dashboard/overview" in primary.get_attribute("href")
    assert page.get_by_role("link", name="Service status").count() >= 1
    return main


@pytest.mark.parametrize("label,viewport,scheme", VIEWPORTS, ids=[v[0] for v in VIEWPORTS])
def test_viewer_sees_one_no_access_screen(browser, live_server, seeded, viewer_email,
                                          label, viewport, scheme):
    app_id = seeded["ids"].get("application")
    paths = ["/settings", "/admin/"]
    if app_id:
        paths.append("/applications/%s/edit" % app_id)
    ctx = _ctx(browser, viewport, scheme)
    page = ctx.new_page()
    _sign_in(page, live_server, viewer_email)
    texts = []
    for path in paths:
        resp = page.goto(live_server + path, wait_until="domcontentloaded")
        assert resp.status == 403, "%s answered %s" % (path, resp.status)
        _assert_library_screen(page, NO_ACCESS)
        texts.append(page.get_by_test_id("plain-error").inner_text())
        page.screenshot(path="/tmp/shots/%s-%s.png" % (path.strip("/").replace("/", "-") or "root", label))
    assert len(set(texts)) == 1, "403 screens differ: %r" % texts
    ctx.close()


@pytest.mark.parametrize("signed_in", [True, False], ids=["signed-in", "signed-out"])
def test_missing_page_is_the_one_not_found_screen(browser, live_server, seeded, viewer_email, signed_in):
    ctx = _ctx(browser, {"width": 1440, "height": 900}, "light")
    page = ctx.new_page()
    if signed_in:
        _sign_in(page, live_server, viewer_email)
    resp = page.goto(live_server + "/no-such-page-" + uuid.uuid4().hex[:6], wait_until="domcontentloaded")
    assert resp.status == 404
    body = page.inner_text("body")
    assert NOT_FOUND in body, body[:300]
    assert page.locator("h1.code").count() == 0
    ctx.close()
