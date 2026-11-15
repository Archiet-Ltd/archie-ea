"""A person signs up, confirms by the emailed link and reaches their first answer; a script gets nowhere.

The person: someone signing up from a real address. The goal: an account of their own, a workspace
that exists once the address is confirmed, and an answer in the chat. The scripts: a submission that
is too quick, one that fills the hidden field, one from a disposable-mailbox address and one whose
user agent names itself a bot. Each is told why in plain words and creates nothing.

The app runs against a real SMTP session into a sink in this process, so the link followed is the
one in the message.
"""

import os
import re
import tempfile
import time
import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask
from .conftest import PAGE_TIMEOUT, boot_live_server
from .intelligence_graph import seed_impact_graph
from .test_account_mail_journey import SmtpSink  # noqa: F401  (the sink class)
from .test_archetype_journeys import PASSWORD as ADMIN_PASSWORD
from .test_account_mail_journey import smtp_sink  # noqa: F401  (module fixture, re-used as is)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PASSWORD = "Correct-horse-9"
VIEWS = [(1440, "light"), (390, "dark")]
WALK = os.environ.get("SMOKE_WALK_DIR") or os.path.join(tempfile.gettempdir(), "signup-bot-walk")
MIN_SECONDS = 2


@pytest.fixture(scope="module")
def guarded_server(request, app, smtp_sink, ai_protocol_stub):
    return boot_live_server(request, ai_protocol_stub, app, extra_env={
        "MAIL_SERVER": "127.0.0.1",
        "MAIL_PORT": str(smtp_sink.server_address[1]),
        "MAIL_USE_TLS": "false",
        "MAIL_USE_SSL": "false",
        "MAIL_USERNAME": "",
        "MAIL_PASSWORD": "",
        "MAIL_DEFAULT_SENDER": "no-reply@example.com",
        "MAIL_SUPPRESS_SEND": "false",
        "SIGNUP_FORM_TOKEN_REQUIRED": "true",
        "SIGNUP_MIN_SECONDS": str(MIN_SECONDS),
    })


def _account(email):
    """(confirmed, organisation slug, name, id, roles held) or None when there is no account."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        row = db.session.execute(db.text(
            "SELECT u.confirmed, o.slug, o.name, o.id, (SELECT count(*) FROM org_roles r WHERE r.user_id = u.id) "
            "FROM users u JOIN organizations o ON o.id = u.organization_id WHERE lower(u.email) = :e"
        ), {"e": email.lower()}).first()
        db.session.remove()
    return tuple(row) if row else None


def _context(browser, width, theme, agent=None):
    options = {"viewport": {"width": width, "height": 900}}
    if agent:
        options["user_agent"] = agent
    ctx = browser.new_context(**options)
    ctx.set_default_timeout(30000)
    if theme == "dark":
        ctx.add_init_script("try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
                            "document.documentElement.classList.add('dark');")
    return ctx


def _fill(page, email):
    page.fill("#first_name", "Sam")
    page.fill("#last_name", "Walker")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)


def _submit(page):
    with page.expect_response(lambda r: r.url.endswith("/account/register") and r.request.method == "POST") as sent:
        page.locator("form [type=submit]").first.click()
    return sent.value


def _open_form(ctx, base):
    page = ctx.new_page()
    page.goto(base + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return page


def _shot(page, name, width, theme):
    os.makedirs(WALK, exist_ok=True)
    page.screenshot(path="%s/%s-%d-%s.png" % (WALK, name, width, theme), full_page=True)


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_script_is_told_why_and_creates_nothing(browser, guarded_server, width, theme):
    tag = uuid.uuid4().hex[:8]
    ctx = _context(browser, width, theme)
    try:
        # Too quick: sent the instant the form is drawn.
        email = "quick-%s@example.com" % tag
        page = _open_form(ctx, guarded_server)
        _fill(page, email)
        assert _submit(page).status == 400
        expect(page.locator("main")).to_contain_text("quicker than a person can fill in the form")
        _shot(page, "refused-too-quick", width, theme)
        assert _account(email) is None

        # The same person, a few seconds later, is let through by the same form.
        page.wait_for_timeout((MIN_SECONDS + 1) * 1000)
        page.fill("#password", PASSWORD)
        page.fill("#password2", PASSWORD)
        assert _submit(page).status in (200, 302)
        assert _account(email) is not None
        ctx.clear_cookies()

        # The hidden field is filled.
        email = "hidden-%s@example.com" % tag
        page = _open_form(ctx, guarded_server)
        _fill(page, email)
        page.evaluate("document.querySelector('[name=nickname]').value = 'http://spam.example'")
        page.wait_for_timeout((MIN_SECONDS + 1) * 1000)
        assert _submit(page).status == 400
        expect(page.locator("main")).to_contain_text("We could not accept this sign-up")
        _shot(page, "refused-hidden-field", width, theme)
        assert _account(email) is None

        # A disposable-mailbox address.
        email = "temp-%s@mailinator.com" % tag
        page = _open_form(ctx, guarded_server)
        _fill(page, email)
        page.wait_for_timeout((MIN_SECONDS + 1) * 1000)
        _submit(page)
        expect(page.locator("main")).to_contain_text("Please use your work or personal email address")
        _shot(page, "refused-disposable-address", width, theme)
        assert _account(email) is None
    finally:
        ctx.close()

    # A user agent that names itself a bot.
    bot = _context(browser, width, theme, agent="WebslockBot/1.0")
    try:
        email = "agent-%s@example.com" % tag
        page = _open_form(bot, guarded_server)
        _fill(page, email)
        page.wait_for_timeout((MIN_SECONDS + 1) * 1000)
        assert _submit(page).status == 400
        expect(page.locator("main")).to_contain_text("Automated sign-ups are not accepted")
        _shot(page, "refused-bot-agent", width, theme)
        assert _account(email) is None
    finally:
        bot.close()


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_person_signs_up_confirms_by_the_emailed_link_and_gets_a_first_answer(
    browser, guarded_server, smtp_sink, width, theme
):
    email = "person-%s@example.com" % uuid.uuid4().hex[:10]
    ctx = _context(browser, width, theme)
    try:
        page = _open_form(ctx, guarded_server)
        _fill(page, email)
        page.wait_for_timeout((MIN_SECONDS + 1) * 1000)
        _submit(page)
        page.wait_for_url(re.compile(r".*/account/unconfirmed$"), timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text(email)
        _shot(page, "check-your-email", width, theme)

        # Waiting on the address: an account, in the shared holding organisation, with no role anywhere.
        confirmed, slug, _name, _id, roles = _account(email)
        assert (confirmed, slug, roles) == (False, "unconfirmed-sign-ups", 0)

        # The emailed link, followed as the message gives it.
        body = smtp_sink.text_to(email)
        link = re.search(r"https?://[^\s\"'<>]+/account/confirm-account/[^\s\"'<>]+", body).group(0)
        started = time.time()
        page.goto(guarded_server + link[link.index("/account/confirm-account/"):], wait_until="domcontentloaded",
                  timeout=PAGE_TIMEOUT)
        page.wait_for_url(re.compile(r".*/account/welcome.*"), timeout=PAGE_TIMEOUT)
        _shot(page, "confirmed-welcome", width, theme)

        # Their workspace now exists, theirs alone.
        confirmed, slug, name, org_id, roles = _account(email)
        assert confirmed is True and slug != "unconfirmed-sign-ups" and roles == 1
        assert "Sam Walker" in name

        # The link works once: opened again by a stranger, it says so.
        stranger = _context(browser, width, theme)
        again = stranger.new_page()
        response = again.goto(guarded_server + link[link.index("/account/confirm-account/"):])
        assert response.status == 410
        expect(again.locator("main")).to_contain_text("no longer works")
        stranger.close()

        # First answer: their estate holds linked systems (the import step is covered by the startup CTO
        # journey); the chat answers what breaks if the service fails, naming what depends on it.
        graph = seed_impact_graph(org_id)
        answer = ask(page, guarded_server, "What breaks if %s fails" % graph["names"]["service"])
        assert answer.locator("[data-answer-row]").count() >= 1
        text = answer.inner_text()
        assert graph["names"]["gateway"] in text and graph["names"]["portal"] in text, text
        _shot(page, "first-answer", width, theme)
        print("\n[signup-bot %d-%s] confirm to first answer: %.0f s" % (width, theme, time.time() - started))
    finally:
        ctx.close()


def _sign_up_held(browser, base, width, theme, tag):
    email = "held-%s-%s@example.com" % (tag, uuid.uuid4().hex[:8])
    ctx = _context(browser, width, theme)
    page = _open_form(ctx, base)
    _fill(page, email)
    page.wait_for_timeout((MIN_SECONDS + 1) * 1000)
    _submit(page)
    page.wait_for_url(re.compile(r".*/account/unconfirmed$"), timeout=PAGE_TIMEOUT)
    return ctx, page, email


@pytest.mark.parametrize("width,theme", VIEWS)
def test_a_person_waiting_on_their_address_can_open_nothing_else_and_sees_nobody_else(
    browser, guarded_server, width, theme
):
    first_ctx, first, first_email = _sign_up_held(browser, guarded_server, width, theme, "first")
    second_ctx, _second, second_email = _sign_up_held(browser, guarded_server, width, theme, "second")
    try:
        # Both wait in the one shared holding organisation.
        assert _account(first_email)[1] == "unconfirmed-sign-ups" and _account(second_email)[1] == "unconfirmed-sign-ups"

        for path in ("/account/manage", "/account/manage/change-email", "/admin/users", "/api/users",
                     "/dashboard/", "/applications/", "/ai-chat/"):
            first.goto(guarded_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert first.url.endswith("/account/unconfirmed"), (path, first.url)
            expect(first.locator("main")).to_contain_text(first_email)
            expect(first.locator("body")).not_to_contain_text(second_email)
        _shot(first, "waiting-on-address", width, theme)

        # Their own way forward is still there: the page says how to get the link again.
        expect(first.locator("main")).to_contain_text("link")
    finally:
        first_ctx.close()
        second_ctx.close()


def _administrator(email):
    """A confirmed platform administrator in the ``default`` organisation, as ``create_admin.py`` places one.

    Returns the ids to remove afterwards: (user id, organisation id when this made it).
    """
    from app import create_app, db
    from app.models.organization import Organization
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        org = Organization.query.filter_by(slug="default").first()
        made = None
        if org is None:
            org = Organization(name="Default Organization", slug="default")
            db.session.add(org)
            db.session.flush()
            made = org.id
        admin = User(first_name="Plat", last_name="Admin", email=email, organization_id=org.id,
                     confirmed=True, is_platform_admin=True, is_org_admin=True)
        admin.password = ADMIN_PASSWORD
        db.session.add(admin)
        db.session.commit()
        ids = (admin.id, made)
        db.session.remove()
    return ids


def _remove_administrator(ids):
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        user_id, org_id = ids
        # Everything that points at the organisation this run created goes before it, audit rows included
        # (by organisation as well as by person); a database that already had a default organisation keeps it.
        for table in ("org_roles", "soc2_audit_log"):
            db.session.execute(db.text("DELETE FROM %s WHERE user_id = :u" % table), {"u": user_id})
        if org_id:
            db.session.execute(db.text("DELETE FROM soc2_audit_log WHERE organization_id = :o"), {"o": org_id})
            db.session.execute(db.text("DELETE FROM org_roles WHERE organization_id = :o"), {"o": org_id})
        db.session.execute(db.text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        if org_id:
            db.session.execute(db.text("DELETE FROM organizations WHERE id = :o"), {"o": org_id})
        db.session.commit()
        db.session.remove()


@pytest.mark.parametrize("width,theme", VIEWS)
def test_the_platform_administrator_sees_their_own_members_and_an_empty_holding_organisation(
    browser, guarded_server, width, theme
):
    from .test_archetype_journeys import _login

    held_ctx, _held, held_email = _sign_up_held(browser, guarded_server, width, theme, "stranger")
    admin_email = "plat-%s@example.com" % uuid.uuid4().hex[:8]
    ids = _administrator(admin_email)
    ctx = _context(browser, width, theme)
    try:
        page = ctx.new_page()
        _login(page, guarded_server, admin_email)
        page.goto(guarded_server + "/admin/organizations", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rows = page.locator("[data-testid=organization-row]")

        # The organisation unconfirmed sign-ups wait in says so, and says how many are waiting (the stranger just signed up).
        page.fill("input[placeholder='Filter organisations...']", "unconfirmed-sign-ups")
        holding = rows.filter(has_text="unconfirmed-sign-ups")
        expect(holding).to_have_count(1)
        expect(holding).to_contain_text("unconfirmed sign-ups wait here")
        waiting = re.search(r"(^|\s)(\d+)\s+unconfirmed sign-ups wait here", " ".join(holding.inner_text().split()))
        assert waiting is not None and int(waiting.group(2)) >= 1, holding.inner_text()
        _shot(page, "organisations-holding-row", width, theme)

        # The administrator's own organisation still counts the people who really are in it.
        page.fill("input[placeholder='Filter organisations...']", "default")
        own = rows.filter(has_text="Default Organization")
        expect(own).to_have_count(1)
        own_text = " ".join(own.inner_text().split())
        counted = int(re.search(r"(\d+)\s+(?:of \d+|\(no limit\))", own_text).group(1))
        assert counted >= 1, own_text
        _shot(page, "organisations-own-row", width, theme)

        # Each name opens a page about that organisation, and the stranger is not on the administrator's.
        own.locator("a").first.click()
        page.wait_for_url(re.compile(r".*/admin/organizations/\d+$"), timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text("Default Organization")
        expect(page.locator("body")).not_to_contain_text(held_email)
        _shot(page, "organisation-own-detail", width, theme)
    finally:
        ctx.close()
        held_ctx.close()
        _remove_administrator(ids)
