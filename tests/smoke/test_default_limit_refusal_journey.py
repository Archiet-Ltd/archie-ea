"""A visitor who reloads a page too fast is told to wait, on a page that is styled and in their theme.

The person: someone refreshing the sign-in page over and over from one address. The goal: be told
plainly how long to wait. The 121st read in a minute is refused by the limiter's own hook, before
any page hook has run; the page must carry the same script and style nonce the Content-Security-
Policy header names (otherwise the browser blocks its styles and theme script), render in the
visitor's theme, and offer no link that would itself be refused.
"""

import os
import re
import tempfile

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWS = [(1440, "light"), (390, "light"), (1440, "dark"), (390, "dark")]
WALK = os.environ.get("SMOKE_WALK_DIR") or os.path.join(tempfile.gettempdir(), "default-limit-walk")


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


@pytest.mark.parametrize("width,theme", VIEWS)
def test_the_121st_read_in_a_minute_is_refused_on_a_styled_page_with_its_nonce(browser, limits_server, width, theme):
    _clear_counters()
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.set_default_timeout(30000)
    if theme == "dark":
        ctx.add_init_script("try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
                            "document.documentElement.classList.add('dark');")
    page = ctx.new_page()
    try:
        refused = None
        for _ in range(130):
            reply = ctx.request.get(limits_server + "/account/login")
            if reply.status == 429:
                break
        assert reply.status == 429, "the limiter never refused"

        response = page.goto(limits_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 429
        header = set(re.findall(r"'nonce-([^']*)'", response.headers.get("content-security-policy", "")))
        body = set(re.findall(r'nonce="([^"]*)"', response.text()))
        assert len(header) == 1 and "" not in header, header
        assert body == header, (header, body)
        for directive in response.headers["content-security-policy"].split(";"):
            if directive.strip().startswith(("script-src ", "style-src ", "style-src-elem ")):
                assert "unsafe-inline" not in directive, directive

        expect(page.get_by_role("heading", name="Too Many Requests")).to_be_visible()
        expect(page.locator("main")).to_contain_text("Try again in about")
        # Every other page is held for this address too, so nothing is offered that would be refused.
        expect(page.locator("main a")).to_have_count(0)
        luminance = page.evaluate(
            """() => {
                const m = getComputedStyle(document.body).backgroundColor.match(/[\\d.]+/g).map(Number);
                const lin = c => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
                return 0.2126 * lin(m[0]) + 0.7152 * lin(m[1]) + 0.0722 * lin(m[2]);
            }"""
        )
        assert (luminance < 0.2) == (theme == "dark"), luminance
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path="%s/default-limit-%d-%s.png" % (WALK, width, theme), full_page=True)
    finally:
        ctx.close()
        _clear_counters()
