"""An administrator can see and set the rate limits.

The person: the platform administrator, who has found one address hammering the service. The
goal: deny that address on the Rate limits page, see its next request refused with a plain
sentence and logged on the page, then lift the block and see access restored. Every limited
response also reports the remaining allowance, and an organisation's own administrator reads
only its own people's refused requests.

The "other client" is a plain HTTP client bound to OTHER_ADDRESS, a second loopback address, so the
administrator's browser (127.0.0.1) is never the address being denied.
"""

import http.client
import json
import os
import re
import time
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .fresh_org import create_fresh_org
from .fresh_org import sign_in as _sign_in_once

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

VIEWS = [(1440, "light"), (390, "light"), (1440, "dark"), (390, "dark")]
# A loopback address no other process uses (named by this process's id), never 127.0.0.1, which is
# the administrator's own browser. Two runs on one database then deny different addresses.
OTHER_ADDRESS = "127.%d.%d.2" % ((os.getpid() >> 8) & 255, os.getpid() & 255)
DENIED_SENTENCE = "Requests from this address are blocked"


@pytest.fixture(scope="module")
def limits_server(request, app):
    return boot_live_server(request, None, app, extra_env={"SMOKE_RATE_LIMITING": "1"})


def sign_in(page, base, email):
    """Sign in. Every browser here connects from 127.0.0.1, and sign-in is held to ten tries a minute
    for an address, so when other runs share the machine a refused sign-in waits out the minute."""
    for attempt in range(3):
        try:
            return _sign_in_once(page, base, email)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(65)


def _client_get(base, path, *, headers=None, source=OTHER_ADDRESS):
    """GET from another loopback address; returns (status, headers dict, body text)."""
    host, port = base.split("//", 1)[1].split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=60, source_address=(source, 0))
    try:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read().decode("utf-8", "replace")
    finally:
        connection.close()


def _platform_admin():
    """A fresh organisation whose first person is a platform administrator."""
    from app import create_app, db
    from app.models.user import User

    org = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    app = create_app("testing")
    with app.app_context():
        user = db.session.get(User, org["user_ids"]["platform_admin"])
        user.is_platform_admin = True
        db.session.commit()
        db.session.remove()
    return org


def _clear_lists(*emails):
    """Remove the entries and refusals this run made, by key: its own address and its own people."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        mine = {"address": OTHER_ADDRESS, "emails": list(emails)}
        db.session.execute(db.text(
            "DELETE FROM rate_limit_rules WHERE value = :address OR (kind = 'user' AND value IN "
            "(SELECT CAST(id AS VARCHAR) FROM users WHERE email = ANY(:emails)))"), mine)
        db.session.execute(db.text("SELECT set_config('archie.platform_scope', 'on', true)"))
        db.session.execute(db.text(
            "DELETE FROM rate_limit_violations WHERE address = :address OR user_id IN "
            "(SELECT id FROM users WHERE email = ANY(:emails))"), mine)
        db.session.commit()
        db.session.remove()


def _context(browser, width, theme):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    ctx.set_default_timeout(30000)
    if theme == "dark":
        ctx.add_init_script("try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
                            "document.documentElement.classList.add('dark');")
    return ctx


def _shot(page, tag, name):
    root = os.environ.get("JOURNEY_SHOT_DIR") or os.path.join(
        os.path.dirname(__file__), "..", "..", "design-audit", "journeys", "rate-limits")
    folder = os.path.abspath(os.path.join(root, tag))
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, name + ".png"), full_page=True)


def _open_add_entry(page):
    """Open the Add entry dialog; the page's scripts may still be starting, so click until it opens."""
    field = page.locator("#rule-value")
    for _ in range(10):
        page.get_by_role("button", name="Add an allow or deny entry").click()
        try:
            field.wait_for(state="visible", timeout=2000)
            return
        except Exception:
            continue
    raise AssertionError("the Add entry dialog never opened")


# A change to the lists applies at once on the worker that saved it and within this many seconds on
# the other (the server here runs two); a request is retried that long before it is called wrong.
SETTLE_SECONDS = 8


def _goto_until(page, url, status):
    """Open ``url`` until the lists have reached every worker and it answers ``status``."""
    deadline = time.time() + SETTLE_SECONDS
    while True:
        response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if response.status == status or time.time() > deadline:
            return response
        time.sleep(0.5)


def _client_until(base, path, status, **kwargs):
    """GET from the other address until the lists have reached every worker and it answers ``status``."""
    deadline = time.time() + SETTLE_SECONDS
    while True:
        answer = _client_get(base, path, **kwargs)
        if answer[0] == status or time.time() > deadline:
            return answer
        time.sleep(0.5)


def _remove_entry(page, subject):
    """Remove the entry for ``subject`` from its own row; other people's entries stay."""
    row = page.get_by_test_id("rate-limit-rule-row").filter(has_text=subject)
    row.get_by_role("button").click()
    page.locator('[data-testid="rate-limit-rule-remove"]:visible').click()  # the open menu, wherever it is drawn
    _confirm_if_asked(page)


def _confirm_if_asked(page):
    """The remove action asks first; answer the page's own confirmation."""
    dialog = page.get_by_role("dialog")
    try:
        dialog.get_by_role("button", name="Confirm").click(timeout=3000)
    except Exception:
        pass


@pytest.mark.parametrize("width,theme", VIEWS)
def test_deny_an_address_then_lift_it(browser, limits_server, width, theme):
    admin = _platform_admin()
    ctx = _context(browser, width, theme)
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    tag = "%d-%s" % (width, theme)
    _clear_lists(admin["emails"]["platform_admin"])
    try:
        sign_in(page, limits_server, admin["emails"]["platform_admin"])
        page.goto(limits_server + "/admin/rate-limits", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # Before: the other client is served, and its answer reports the allowance falling.
        status, first, _ = _client_get(limits_server, "/account/login")
        assert status == 200
        status, second, _ = _client_get(limits_server, "/account/login")
        assert status == 200
        for name in ("ratelimit-limit", "ratelimit-remaining", "ratelimit-reset"):
            assert name in first and name in second, (name, first)
        assert int(second["ratelimit-remaining"]) < int(first["ratelimit-remaining"]), (first, second)
        assert int(first["ratelimit-limit"]) > 0

        # The administrator cannot lock themselves out.
        _open_add_entry(page)
        page.fill("#rule-value", "127.0.0.1")
        page.get_by_test_id("rate-limit-rule-save").click()
        expect(page.get_by_test_id("rate-limit-flash")).to_contain_text("lose access")

        # Nor can they deny their own user: the same plain sentence.
        _open_add_entry(page)
        page.select_option("#rule-kind", "user")
        page.fill("#rule-value", admin["emails"]["platform_admin"])
        page.get_by_test_id("rate-limit-rule-save").click()
        expect(page.get_by_test_id("rate-limit-flash")).to_contain_text("you are signed in as")
        expect(page.get_by_test_id("rate-limit-rule-row").filter(has_text=admin["emails"]["platform_admin"])).to_have_count(0)

        # Deny the other client's address from the page.
        _open_add_entry(page)
        page.fill("#rule-value", OTHER_ADDRESS)
        page.fill("#rule-note", "journey %s" % tag)
        page.get_by_test_id("rate-limit-rule-save").click()
        expect(page.get_by_test_id("rate-limit-rule-row").filter(has_text=OTHER_ADDRESS)).to_be_visible()
        _shot(page, tag, "01-denied")

        # Its next request is refused, in a sentence and in the API envelope.
        status, _, body = _client_until(limits_server, "/account/login", 403)
        assert status == 403 and DENIED_SENTENCE in body, (status, body[:300])
        status, _, body = _client_until(limits_server, "/api/v1/capabilities/", 403, headers={"Accept": "application/json"})
        assert status == 403
        envelope = json.loads(body)
        assert envelope["success"] is False and DENIED_SENTENCE in envelope["error"]

        # The refusal is on the page.
        page.reload(wait_until="domcontentloaded")
        row = page.get_by_test_id("rate-limit-violation-row").filter(has_text=OTHER_ADDRESS).filter(
            has_text="GET /account/login").first
        expect(row).to_contain_text("denied: " + OTHER_ADDRESS)
        expect(page.get_by_test_id("rate-limit-violation-row").filter(has_text="GET /api/v1/capabilities/").first
               ).to_be_visible()
        _shot(page, tag, "02-violation-logged")

        # Remove the entry: access returns on the very next request.
        _remove_entry(page, OTHER_ADDRESS)
        expect(page.get_by_test_id("rate-limit-rule-row").filter(has_text=OTHER_ADDRESS)).to_have_count(0)
        status, _, body = _client_until(limits_server, "/account/login", 200)
        assert status == 200, (status, body[:200])
        _shot(page, tag, "03-restored")
    finally:
        _clear_lists(admin["emails"]["platform_admin"])
        ctx.close()


def test_an_organisation_administrator_reads_only_its_own_refusals(browser, limits_server):
    admin = _platform_admin()
    a = create_fresh_org("architect", org_admin=True)
    b = create_fresh_org("architect", org_admin=True)
    emails = (a["emails"]["architect"], b["emails"]["architect"])
    _clear_lists(*emails)
    ctx_admin, ctx_a, ctx_b = (_context(browser, 1440, "light") for _ in range(3))
    page_admin, page_a, page_b = ctx_admin.new_page(), ctx_a.new_page(), ctx_b.new_page()
    try:
        sign_in(page_admin, limits_server, admin["emails"]["platform_admin"])
        sign_in(page_a, limits_server, a["emails"]["architect"])
        sign_in(page_b, limits_server, b["emails"]["architect"])

        # Deny the first organisation's person by name, so only that person's requests are refused.
        page_admin.goto(limits_server + "/admin/rate-limits", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _open_add_entry(page_admin)
        page_admin.select_option("#rule-kind", "user")
        page_admin.fill("#rule-value", a["emails"]["architect"])
        page_admin.get_by_test_id("rate-limit-rule-save").click()
        expect(page_admin.get_by_test_id("rate-limit-rule-row").filter(has_text=a["emails"]["architect"])).to_be_visible()

        response = _goto_until(page_a, limits_server + "/dashboard/overview", 403)
        assert response.status == 403 and DENIED_SENTENCE in page_a.content()
        page_b.goto(limits_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

        # The denied person's own administrator would be refused too, so lift the entry before reading.
        page_admin.reload(wait_until="domcontentloaded")
        _remove_entry(page_admin, a["emails"]["architect"])
        expect(page_admin.get_by_test_id("rate-limit-rule-row").filter(has_text=a["emails"]["architect"])).to_have_count(0)

        # The platform administrator sees it; organisation A's administrator sees it; B's sees none.
        page_admin.reload(wait_until="domcontentloaded")
        expect(page_admin.get_by_test_id("rate-limit-violation-row").filter(has_text=a["emails"]["architect"]).first).to_be_visible()
        page_a.goto(limits_server + "/admin/rate-limits", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page_a.get_by_test_id("rate-limit-violation-row").filter(has_text=a["emails"]["architect"]).first).to_be_visible()
        assert page_a.get_by_test_id("rate-limit-rules").count() == 0
        page_b.goto(limits_server + "/admin/rate-limits", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert a["emails"]["architect"] not in page_b.content()
        expect(page_b.get_by_test_id("rate-limit-violations")).to_contain_text("No request from your organisation")
    finally:
        _clear_lists(*emails)
        for ctx in (ctx_admin, ctx_a, ctx_b):
            ctx.close()


@pytest.mark.parametrize("width,theme", [(1440, "light"), (390, "dark")])
def test_an_organisation_administrator_finds_the_page_in_the_user_menu_and_sees_only_their_own_refusals(
        browser, limits_server, width, theme):
    """Goal: the administrator of one organisation looks up who in their organisation was refused.

    Two organisations each have one person refused by the limiter. Organisation A's administrator
    starts from the dashboard, opens the Rate limits entry in the user menu, and reads their own
    person's refusal and none of organisation B's. The breadcrumb names the page, not Platform admin.
    """
    admin = _platform_admin()
    a = create_fresh_org("architect", org_admin=True)
    b = create_fresh_org("architect", org_admin=True)
    mail_a, mail_b = a["emails"]["architect"], b["emails"]["architect"]
    emails = (mail_a, mail_b)
    _clear_lists(*emails)
    contexts = [_context(browser, 1440, "light"), _context(browser, width, theme), _context(browser, 1440, "light")]
    page_admin, page_a, page_b = (c.new_page() for c in contexts)
    tag = "%d-%s" % (width, theme)
    try:
        sign_in(page_admin, limits_server, admin["emails"]["platform_admin"])
        sign_in(page_a, limits_server, mail_a)
        sign_in(page_b, limits_server, mail_b)
        page_admin.goto(limits_server + "/admin/rate-limits", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        for mail, person in ((mail_a, page_a), (mail_b, page_b)):
            _open_add_entry(page_admin)
            page_admin.select_option("#rule-kind", "user")
            page_admin.fill("#rule-value", mail)
            page_admin.get_by_test_id("rate-limit-rule-save").click()
            expect(page_admin.get_by_test_id("rate-limit-rule-row").filter(has_text=mail)).to_be_visible()
            assert _goto_until(person, limits_server + "/dashboard/overview", 403).status == 403
        for mail in emails:
            page_admin.reload(wait_until="domcontentloaded")
            _remove_entry(page_admin, mail)
            expect(page_admin.get_by_test_id("rate-limit-rule-row").filter(has_text=mail)).to_have_count(0)

        # Organisation A's administrator, from the dashboard: the user menu's Rate limits entry.
        _goto_until(page_a, limits_server + "/dashboard/overview", 200)
        if width < 1000:  # the sidebar is behind its toggle on a narrow screen
            page_a.get_by_role("button", name=re.compile("toggle sidebar|open (the )?(menu|sidebar)", re.I)).first.click()
        page_a.get_by_test_id("user-menu").click()
        page_a.get_by_test_id("user-menu-rate_limits_menu").click()
        page_a.wait_for_url(re.compile(r"/admin/rate-limits"), timeout=PAGE_TIMEOUT)
        expect(page_a.get_by_test_id("rate-limit-violation-row").filter(has_text=mail_a).first).to_be_visible()
        assert mail_b not in page_a.content()
        assert page_a.get_by_test_id("rate-limit-rules").count() == 0
        trail = page_a.get_by_role("navigation", name=re.compile("breadcrumb", re.I)).first
        expect(trail).to_contain_text("Rate limits")
        assert "Platform admin" not in trail.inner_text()
        _shot(page_a, tag, "04-organisation-administrator")

        # Organisation B's administrator reads B's own refusal and none of A's.
        page_b.goto(limits_server + "/admin/rate-limits", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page_b.get_by_test_id("rate-limit-violation-row").filter(has_text=mail_b).first).to_be_visible()
        assert mail_a not in page_b.content()
    finally:
        _clear_lists(*emails)
        for ctx in contexts:
            ctx.close()


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_blocked_person_sees_the_refusal_page_in_their_own_theme(browser, limits_server, width, theme):
    admin = _platform_admin()
    a = create_fresh_org("architect", org_admin=True)
    emails = (a["emails"]["architect"],)
    _clear_lists(*emails)
    ctx_admin, ctx_a = _context(browser, 1440, "light"), _context(browser, width, theme)
    page_admin, page_a = ctx_admin.new_page(), ctx_a.new_page()
    try:
        sign_in(page_admin, limits_server, admin["emails"]["platform_admin"])
        sign_in(page_a, limits_server, a["emails"]["architect"])
        page_admin.goto(limits_server + "/admin/rate-limits", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _open_add_entry(page_admin)
        page_admin.select_option("#rule-kind", "user")
        page_admin.fill("#rule-value", a["emails"]["architect"])
        page_admin.get_by_test_id("rate-limit-rule-save").click()
        expect(page_admin.get_by_test_id("rate-limit-rule-row").filter(has_text=a["emails"]["architect"])).to_be_visible()

        response = _goto_until(page_a, limits_server + "/dashboard/overview", 403)
        assert response.status == 403 and DENIED_SENTENCE in page_a.content()
        header_nonce = set(re.findall(r"'nonce-([^']*)'", response.headers.get("content-security-policy", "")))
        assert len(header_nonce) == 1 and "" not in header_nonce, header_nonce
        assert set(re.findall(r'nonce="([^"]*)"', response.text())) == header_nonce
        assert page_a.get_by_role("link").count() == 0
        luminance = page_a.evaluate(
            """() => {
                const m = getComputedStyle(document.body).backgroundColor.match(/[\\d.]+/g).map(Number);
                const lin = c => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
                return 0.2126 * lin(m[0]) + 0.7152 * lin(m[1]) + 0.0722 * lin(m[2]);
            }"""
        )
        assert (luminance < 0.2) == (theme == "dark"), luminance
        walk = os.environ.get("SMOKE_WALK_DIR")
        if walk:
            os.makedirs(walk, exist_ok=True)
            page_a.screenshot(path="%s/blocked-%d-%s.png" % (walk, width, theme), full_page=True)
    finally:
        _clear_lists(*emails)
        for ctx in (ctx_admin, ctx_a):
            ctx.close()
