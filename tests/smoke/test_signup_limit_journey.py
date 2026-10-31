"""A visitor who signs up too often is told plainly, and offered the next step that fits them.

The person: someone signing up from one address. The goal: create accounts through the real sign-up
form. Five sign-ups an hour are allowed from one address; the sixth is refused with a page that
says how long to wait and offers Sign in (they are signed out, so not Go to Dashboard), the refused
form creates no account, and Sign in lands on a page with the sign-in form.
"""

import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWS = [(1440, "light"), (390, "light"), (1440, "dark"), (390, "dark")]
WALK = "/home/ubuntu/verify/seats/limits01b/walk"


@pytest.fixture(scope="module")
def limits_server(request, app):
    return boot_live_server(request, None, app, extra_env={"SMOKE_RATE_LIMITING": "1"})


def _clear_counters():
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        db.session.execute(db.text("DELETE FROM rate_limit_counters"))
        db.session.commit()
        db.session.remove()


def _accounts_with(tag):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        rows = db.session.execute(
            db.text("SELECT email FROM users WHERE email LIKE :p"), {"p": "signup-walk-%s-%%" % tag}
        ).all()
        db.session.remove()
    return [r[0] for r in rows]


def _context(browser, width, theme):
    """A fresh browser context, so a fresh session, as a new visitor each time."""
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.set_default_timeout(30000)
    if theme == "dark":
        ctx.add_init_script("try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
                            "document.documentElement.classList.add('dark');")
    return ctx


def _sign_up(browser, base, width, theme, email):
    ctx = _context(browser, width, theme)
    page = ctx.new_page()
    page.goto(base + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#first_name", "Sam")
    page.fill("#last_name", "Walker")
    page.fill("#email", email)
    page.fill("#password", "Correct-horse-9")
    page.fill("#password2", "Correct-horse-9")
    with page.expect_response(lambda r: r.url.endswith("/account/register") and r.request.method == "POST") as sent:
        page.locator("form [type=submit]").first.click()
    return ctx, page, sent.value


@pytest.mark.parametrize("width,theme", VIEWS)
def test_the_sixth_sign_up_in_an_hour_is_refused_with_the_next_step(browser, limits_server, width, theme):
    _clear_counters()
    tag = "%s%d%s" % (uuid.uuid4().hex[:6], width, theme[0])
    os.makedirs(WALK, exist_ok=True)
    emails = ["signup-walk-%s-%d@example.com" % (tag, n) for n in range(6)]
    for email in emails[:5]:
        ctx, page, response = _sign_up(browser, limits_server, width, theme, email)
        assert response.status in (200, 302), (email, response.status)
        assert "429" not in page.title()
        ctx.close()

    ctx, page, response = _sign_up(browser, limits_server, width, theme, emails[5])
    try:
        assert response.status == 429
        assert response.headers.get("retry-after") and int(response.headers["retry-after"]) > 3000
        for name in ("ratelimit-limit", "ratelimit-remaining", "ratelimit-reset"):
            assert response.headers.get(name), (name, response.headers)
        assert response.headers["ratelimit-remaining"] == "0"
        expect(page.get_by_role("heading", name="Too Many Requests")).to_be_visible()
        expect(page.locator("main")).to_contain_text("Try again in about 1 hour")
        expect(page.get_by_role("link", name="Go to Dashboard")).to_have_count(0)
        signin = page.get_by_role("link", name="Sign in")
        expect(signin).to_be_visible()
        page.screenshot(path="%s/signup-refused-%d-%s.png" % (WALK, width, theme), full_page=True)

        # The next step lands on a page with the sign-in form on it.
        signin.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("#email")).to_be_visible()
        expect(page.locator("#password")).to_be_visible()
        page.screenshot(path="%s/signup-refused-signin-%d-%s.png" % (WALK, width, theme), full_page=True)
    finally:
        ctx.close()

    # Five accounts were made, and the refused form made none.
    assert sorted(_accounts_with(tag)) == sorted(emails[:5])
    _clear_counters()
