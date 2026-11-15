"""A signed-in person is warned before an idle sign-out, told at once when it happens, and keeps their chat question.

The application runs with a 40 second idle limit for everyone. Each journey is a real person in a real
browser: the administrator who leaves a page open, the same administrator whose chat question is half typed,
and a non-administrator who ticks "Keep me logged in".
"""

import os
import time

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from tests.smoke.fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

LIMIT = 40
SHOTS = os.environ.get("IDLE_SIGNOUT_SHOTS")
DRAFT = "Which applications would a supplier outage reach?"


@pytest.fixture(scope="module")
def short_limit_server(request, app):
    return boot_live_server(request, None, app, extra_env={
        "SESSION_IDLE_TIMEOUT_SECONDS": str(LIMIT),
        "SESSION_IDLE_TIMEOUT_ADMIN_SECONDS": str(LIMIT),
    })


def _administrator():
    return create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")


def _signed_in(page, base, path="/dashboard/"):
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return "/account/login" not in page.url


def _shot(page, name, width, theme):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    page.wait_for_timeout(600)  # let the dialog finish opening
    page.emulate_media(color_scheme=theme)
    page.evaluate("t => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
    page.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, theme)))


def _login_remembering(page, base, email):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.check("#remember_me")
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def test_warning_comes_before_the_sign_out_and_stay_signed_in_keeps_the_session(browser, short_limit_server):
    org = _administrator()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    sign_in(page, short_limit_server, org["emails"]["platform_admin"])
    assert _signed_in(page, short_limit_server)
    opened = time.time()
    dialog = page.locator("#session-warning-dialog")
    expect(dialog).to_be_visible(timeout=(LIMIT + 5) * 1000)
    assert time.time() - opened < LIMIT, "the warning must come before the sign-out, not with it"
    expect(dialog).to_contain_text("You'll be signed out in")
    stay = dialog.get_by_role("button", name="Stay signed in")
    expect(stay).to_be_focused()
    _shot(page, "warning", 1440, "light")
    page.set_viewport_size({"width": 390, "height": 800})
    _shot(page, "warning", 390, "dark")
    page.emulate_media(color_scheme="light")
    page.evaluate("document.documentElement.classList.remove('dark')")
    stay.press("Enter")
    expect(dialog).to_be_hidden()
    # Wait past the limit counted from sign-in: only the press can have kept the session.
    page.wait_for_timeout(max(0, int((opened + LIMIT + 6 - time.time()) * 1000)))
    assert _signed_in(page, short_limit_server), "pressing Stay signed in must keep the session"
    page.close()


def test_idle_tab_says_nothing_false_while_the_person_works_in_another_tab(browser, short_limit_server):
    org = _administrator()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    idle_tab = context.new_page()
    sign_in(idle_tab, short_limit_server, org["emails"]["platform_admin"])
    assert _signed_in(idle_tab, short_limit_server)
    working_tab = context.new_page()
    started = time.time()
    # The person keeps working in the second tab for longer than the limit; the first tab's own clock runs out.
    while time.time() - started < LIMIT + 20:
        assert _signed_in(working_tab, short_limit_server, "/dashboard/overview")
        working_tab.wait_for_timeout(10000)
    expect(idle_tab.locator("#session-ended-dialog")).to_be_hidden()
    assert _signed_in(idle_tab, short_limit_server), "the session is alive, so the idle tab must still be signed in"
    context.close()


def test_open_chat_page_says_at_once_when_the_session_ended_and_signs_back_in_with_the_question(browser, short_limit_server):
    org = _administrator()
    email = org["emails"]["platform_admin"]
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    sign_in(page, short_limit_server, email)
    page.goto(short_limit_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    box = page.locator("#user-input")
    expect(box).to_be_visible(timeout=PAGE_TIMEOUT)
    box.fill(DRAFT)
    # Nothing else happens: the page's own polls are not the person acting.
    ended = page.locator("#session-ended-dialog")
    expect(ended).to_be_visible(timeout=(LIMIT + 40) * 1000)
    expect(ended).to_contain_text("You were signed out after 40 seconds without activity.")
    sign_in_button = ended.get_by_role("button", name="Sign in")
    expect(sign_in_button).to_be_visible()
    _shot(page, "signed-out", 1440, "light")
    sign_in_button.click()
    page.wait_for_url(lambda url: "/account/login" in url, timeout=PAGE_TIMEOUT)
    login_url = page.url
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    try:
        page.wait_for_url(lambda url: "/ai-chat" in url, timeout=20000)
    except Exception:
        raise AssertionError("after signing in the person is at %s (login was %s): %s" % (page.url, login_url, page.locator("body").inner_text()[:300]))
    box = page.locator("#user-input")
    expect(box).to_have_value(DRAFT, timeout=PAGE_TIMEOUT)
    assert page.get_by_test_id("estate-answer").count() == 0
    page.close()


def test_a_session_ended_elsewhere_is_shown_on_the_open_page_within_one_poll(browser, short_limit_server, app):
    org = _administrator()
    page = browser.new_page(viewport={"width": 390, "height": 800})
    sign_in(page, short_limit_server, org["emails"]["platform_admin"])
    page.goto(short_limit_server + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("#user-input")).to_be_visible(timeout=PAGE_TIMEOUT)
    with app.app_context():
        from app.extensions import db
        from app.models.user_session import UserSession
        from datetime import datetime, timezone

        rows = UserSession.query.filter_by(user_id=org["user_ids"]["platform_admin"], revoked_at=None).all()
        assert rows
        for row in rows:
            row.revoked_at = datetime.now(timezone.utc)
            row.revoked_reason = "idle_timeout"
        db.session.commit()
        db.session.remove()
    ended = page.locator("#session-ended-dialog")
    expect(ended).to_be_visible(timeout=45000)
    expect(ended).to_contain_text("You were signed out")
    expect(ended.get_by_role("button", name="Sign in")).to_be_visible()
    _shot(page, "signed-out", 390, "dark")
    page.close()


def test_keep_me_logged_in_holds_for_a_non_administrator_and_is_not_promised_to_an_administrator(browser, short_limit_server):
    person = create_fresh_org("solution_architect", platform_role="Architect")
    admin = _administrator()
    # A non-administrator who ticks the option is still signed in after the browser is restarted
    # (the session cookie is gone, the remember cookie is what remains).
    ctx = browser.new_context()
    page = ctx.new_page()
    _login_remembering(page, short_limit_server, person["emails"]["solution_architect"])
    remembered = [c for c in ctx.cookies() if c["name"] == "remember_token"]
    assert remembered, "ticking the option must set the remember cookie for a non-administrator"
    restarted = browser.new_context()
    restarted.add_cookies([dict(remembered[0])])
    page2 = restarted.new_page()
    assert _signed_in(page2, short_limit_server), "a remembered person must be signed in after a restart"
    ctx.close()
    restarted.close()
    # The sign-in page says what applies to an administrator, and an administrator who ticks it is not
    # given a remember cookie that the policy would then refuse.
    page3 = browser.new_page()
    page3.goto(short_limit_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page3.locator("#remember-admin-note")).to_have_text(
        "Administrators are always signed out after %d seconds without activity, to protect the organisation's data." % LIMIT)
    ctx2 = browser.new_context()
    page4 = ctx2.new_page()
    _login_remembering(page4, short_limit_server, admin["emails"]["platform_admin"])
    assert not [c for c in ctx2.cookies() if c["name"] == "remember_token"]
    ctx2.close()
    page3.close()
