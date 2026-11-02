"""A person refused by a role check or a rate limit reads why, on one styled page in their theme.

The person: a member of an organisation who is not an administrator. Goal one: open the Rate limits
settings page, be told plainly that their role does not include it, and reach the dashboard from the
page (it keeps the sidebar and the theme). Goal two: reload a page too fast and be told how long to
wait on the same plain error treatment, styled, with the nonce the Content-Security-Policy names,
and follow the offered link to the dashboard.
"""

import os
import re
import tempfile

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server, clear_address_counters, forward_from_own_address
from .fresh_org import create_fresh_org
from .fresh_org import sign_in as sign_in_once

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWS = [(1440, "light"), (390, "light"), (1440, "dark"), (390, "dark")]
WALK = os.environ.get("SMOKE_WALK_DIR") or os.path.join(tempfile.gettempdir(), "refusal-pages-walk")


@pytest.fixture(scope="module")
def limits_server(request, app):
    server = boot_live_server(request, None, app, extra_env={"SMOKE_RATE_LIMITING": "1"})
    return forward_from_own_address(request, server)


@pytest.fixture(scope="module")
def member():
    return create_fresh_org("solution_architect")


def _is_dark(page):
    return page.evaluate(
        """() => {
            const m = getComputedStyle(document.body).backgroundColor.match(/[\\d.]+/g).map(Number);
            const lin = c => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
            return (0.2126 * lin(m[0]) + 0.7152 * lin(m[1]) + 0.0722 * lin(m[2])) < 0.2;
        }"""
    )


def _context(browser, width, theme):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.set_default_timeout(30000)
    if theme == "dark":
        ctx.add_init_script("try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
                            "document.documentElement.classList.add('dark');")
    return ctx


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_member_refused_a_page_reads_why_and_reaches_the_dashboard(browser, limits_server, member, width, theme):
    clear_address_counters(limits_server)
    ctx = _context(browser, width, theme)
    page = ctx.new_page()
    try:
        sign_in_once(page, limits_server, member["emails"]["solution_architect"])
        response = page.goto(limits_server + "/admin/rate-limits", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 403
        expect(page.get_by_role("heading", name="You don't have access to this page")).to_be_visible()
        expect(page.locator("main")).to_contain_text("Ask an administrator of your organisation")
        assert _is_dark(page) == (theme == "dark")
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path="%s/no-access-%d-%s.png" % (WALK, width, theme), full_page=True)

        page.get_by_role("link", name="Go to the dashboard").click()
        page.wait_for_load_state("domcontentloaded")
        assert "/dashboard" in page.url
        expect(page.locator("main h1, main h2").first).to_be_visible()
        assert len(page.locator("main").inner_text().strip()) > 40
    finally:
        ctx.close()
        clear_address_counters(limits_server)


def _own_counters(member):
    return list(member["user_ids"].values())


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_member_who_reloads_too_fast_is_told_how_long_to_wait(browser, limits_server, member, width, theme):
    clear_address_counters(limits_server, _own_counters(member))
    ctx = _context(browser, width, theme)
    page = ctx.new_page()
    try:
        sign_in_once(page, limits_server, member["emails"]["solution_architect"])
        # One limited route, hit directly with no redirect followed, so the count does not depend on speed.
        reply = None
        for _ in range(140):
            reply = ctx.request.get(limits_server + "/account/login", max_redirects=0)
            if reply.status == 429:
                break
        assert reply.status == 429, "the limiter never refused"
        assert int(reply.headers["retry-after"]) > 0

        response = page.goto(limits_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 429
        header = set(re.findall(r"'nonce-([^']*)'", response.headers.get("content-security-policy", "")))
        assert len(header) == 1 and set(re.findall(r'nonce="([^"]*)"', response.text())) == header
        expect(page.get_by_role("heading", name="You have made too many requests to this page")).to_be_visible()
        expect(page.locator("main")).to_contain_text(re.compile(r"Try again in about \d+ (second|minute|hour)s?\."))
        assert _is_dark(page) == (theme == "dark")
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path="%s/too-many-%d-%s.png" % (WALK, width, theme), full_page=True)

        # Limits are counted for each route, so the dashboard is still open to this person.
        page.get_by_role("link", name="Go to the dashboard").click()
        page.wait_for_load_state("domcontentloaded")
        assert "/dashboard" in page.url
        expect(page.locator("main h1, main h2").first).to_be_visible()
        assert len(page.locator("main").inner_text().strip()) > 40
    finally:
        ctx.close()
        clear_address_counters(limits_server, _own_counters(member))
