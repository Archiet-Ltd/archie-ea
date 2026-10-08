"""Browser journeys for the e-mailed account links: password reset and team
invitations, driven through the real sign-in and team pages.

The app runs as its own server with a real SMTP configuration pointed at a
small SMTP sink in this process, so every message the product sends crosses a
real SMTP session and the journey follows the link it actually contains. The
shared ``live_server`` has no mail server configured; it is used for the
journey that checks the product says so.
"""
import email
import re
import socketserver
import threading
import time
import uuid

import pytest

from .conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

NEW_PASSWORD = "Fresh-Journey!2026"
# The first-visit tour covers the header; it is not what these journeys test.
_DISMISS_ONBOARDING_SCRIPT = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"


class _SmtpHandler(socketserver.StreamRequestHandler):
    def _reply(self, text):
        self.wfile.write((text + "\r\n").encode())

    def handle(self):
        self._reply("220 smoke-sink ESMTP")
        in_data, lines = False, []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            if in_data:
                if line in (b".\r\n", b".\n"):
                    self.server.messages.append(email.message_from_bytes(b"".join(lines)))
                    in_data, lines = False, []
                    self._reply("250 OK")
                else:
                    lines.append(line[1:] if line.startswith(b"..") else line)
                continue
            verb = line.decode("ascii", "replace").strip().upper()
            if verb.startswith(("EHLO", "HELO")):
                self._reply("250 smoke-sink")
            elif verb.startswith(("MAIL", "RCPT", "RSET", "NOOP")):
                self._reply("250 OK")
            elif verb == "DATA":
                in_data = True
                self._reply("354 End data with <CR><LF>.<CR><LF>")
            elif verb == "QUIT":
                self._reply("221 Bye")
                return
            else:
                self._reply("502 Command not implemented")


class SmtpSink(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _SmtpHandler)
        self.messages = []

    def text_to(self, address, timeout=20):
        """The plain-text body of the newest message to ``address``."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for msg in reversed(self.messages):
                if address in msg.get("To", ""):
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            return part.get_payload(decode=True).decode("utf-8", "replace")
            time.sleep(0.25)
        raise AssertionError("no message to %s reached the SMTP sink" % address)

    def count_to(self, address):
        return sum(1 for msg in self.messages if address in msg.get("To", ""))


@pytest.fixture(scope="module")
def smtp_sink():
    sink = SmtpSink()
    thread = threading.Thread(target=sink.serve_forever, daemon=True)
    thread.start()
    yield sink
    sink.shutdown()
    sink.server_close()


@pytest.fixture(scope="module")
def mail_server(request, app, smtp_sink):
    return boot_live_server(request, None, app, extra_env={
        "MAIL_SERVER": "127.0.0.1",
        "MAIL_PORT": str(smtp_sink.server_address[1]),
        "MAIL_USE_TLS": "false",
        "MAIL_USE_SSL": "false",
        "MAIL_USERNAME": "",
        "MAIL_PASSWORD": "",
        "MAIL_DEFAULT_SENDER": "no-reply@example.com",
        "MAIL_SUPPRESS_SEND": "false",
    })


def _seed_member(app, *, org_admin=False):
    """A confirmed account with a password, in an organisation of its own."""
    from app import db
    from app.models.org_role import OrgRole
    from app.models.organization import Organization
    from app.models.user import Role, User

    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        Role.insert_roles()
        org = Organization(name="Mail Journey %s" % suffix, slug="mail-journey-%s" % suffix)
        db.session.add(org)
        db.session.flush()
        user = User(
            first_name="Mail", last_name="Owner %s" % suffix,
            email="mail.owner.%s@example.com" % suffix, password=PASSWORD,
            confirmed=True, organization_id=org.id, is_org_admin=org_admin,
            enterprise_role="solution_architect",
        )
        db.session.add(user)
        db.session.flush()
        OrgRole.set_role(org.id, user.id, "org_admin" if org_admin else "viewer",
                         granted_by_id=user.id)
        db.session.commit()
        return {"email": user.email, "org_id": org.id, "org_name": org.name}


def _sign_in(page, base, address, password=PASSWORD):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", address)
    page.fill("#password", password)
    page.click("#submit")
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def _link_in(text, path):
    match = re.search(r"https?://\S+" + re.escape(path) + r"\S+", text)
    assert match, "no %s link in the message:\n%s" % (path, text)
    return match.group(0)


def test_forgotten_password_is_reset_from_the_sign_in_page(browser, mail_server, smtp_sink, app):
    member = _seed_member(app)
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(mail_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Forgot password?").click()
        page.wait_for_url("**/account/reset-password", timeout=PAGE_TIMEOUT)
        page.fill("#email", member["email"])
        page.get_by_role("button", name="Reset password").click()
        page.get_by_text("Check your e-mail").wait_for(timeout=PAGE_TIMEOUT)

        link = _link_in(smtp_sink.text_to(member["email"]), "/account/reset-password/")
        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_text("Choose a new password").wait_for(timeout=PAGE_TIMEOUT)
        page.fill("#password", NEW_PASSWORD)
        page.fill("#password2", NEW_PASSWORD)
        page.get_by_role("button", name="Set new password").click()
        page.wait_for_url("**/account/login", timeout=PAGE_TIMEOUT)
        assert page.get_by_text("Your password has been updated").first.is_visible()

        _sign_in(page, mail_server, member["email"], NEW_PASSWORD)
        assert "/account/" not in page.url
    finally:
        context.close()

    # The same link, opened again after use, says so and offers a new one.
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_text("This reset link no longer works").wait_for(timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Send a new reset link").click()
        page.wait_for_url("**/account/reset-password", timeout=PAGE_TIMEOUT)

        # An address with no account gets the same answer and no message.
        unknown = "nobody.%s@example.com" % uuid.uuid4().hex[:8]
        page.fill("#email", unknown)
        page.get_by_role("button", name="Reset password").click()
        page.get_by_text("Check your e-mail").wait_for(timeout=PAGE_TIMEOUT)
        time.sleep(2)
        assert smtp_sink.count_to(unknown) == 0
    finally:
        context.close()


def test_an_invited_teammate_joins_the_inviters_organisation(browser, mail_server, smtp_sink, app):
    owner = _seed_member(app, org_admin=True)
    invitee = "teammate.%s@example.com" % uuid.uuid4().hex[:8]
    context = browser.new_context()
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        _sign_in(page, mail_server, owner["email"])
        page.locator("#user-menu-btn").click()
        page.get_by_role("menuitem", name="Invite your team").click()
        page.wait_for_url("**/admin/team", timeout=PAGE_TIMEOUT)

        page.fill("#email", invitee)
        page.select_option("#persona", "data_architect")
        page.select_option("#role", "architect")
        page.get_by_role("button", name="Send invitation").click()
        page.wait_for_url("**/admin/team", timeout=PAGE_TIMEOUT)
        row = page.locator('[data-invitation-email="%s"]' % invitee)
        row.wait_for(timeout=PAGE_TIMEOUT)
        assert row.locator('[data-invitation-delivery="sent"]').count() == 1

        link = _link_in(smtp_sink.text_to(invitee), "/account/join/")
    finally:
        context.close()

    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("heading", name="Join %s" % owner["org_name"]).wait_for(timeout=PAGE_TIMEOUT)
        page.fill("#password", NEW_PASSWORD)
        page.fill("#password2", NEW_PASSWORD)
        page.get_by_role("button", name="Join %s" % owner["org_name"]).click()
        page.wait_for_url("**/account/login", timeout=PAGE_TIMEOUT)
        _sign_in(page, mail_server, invitee, NEW_PASSWORD)
        assert "/account/" not in page.url

        # Once taken up the link is spent, whoever opens it.
        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_text("This invitation no longer works").wait_for(timeout=PAGE_TIMEOUT)
    finally:
        context.close()

    with app.app_context():
        from app.models.org_role import OrgRole
        from app.models.user import User

        joined = User.find_by_email(invitee)
        assert joined.organization_id == owner["org_id"]
        assert joined.enterprise_role == "data_architect"
        assert OrgRole.get_role(owner["org_id"], joined.id) == "architect"

    # After a reload the owner sees the teammate as a member, not an open invitation.
    context = browser.new_context()
    page = context.new_page()
    try:
        _sign_in(page, mail_server, owner["email"])
        page.goto(mail_server + "/admin/team", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator("[data-team-members] tbody tr", has_text=invitee).count() == 1
        assert page.locator('[data-invitation-email="%s"]' % invitee).count() == 0
    finally:
        context.close()


def _invite_from_team_page(page, base, address, role="architect"):
    page.goto(base + "/admin/team", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", address)
    page.select_option("#role", role)
    page.get_by_role("button", name="Send invitation").click()
    page.wait_for_url("**/admin/team", timeout=PAGE_TIMEOUT)
    row = page.locator('[data-invitation-email="%s"]' % address)
    row.wait_for(timeout=PAGE_TIMEOUT)
    return row


def test_someone_with_an_account_accepts_an_invitation_after_signing_in(
    browser, mail_server, smtp_sink, app
):
    owner = _seed_member(app, org_admin=True)
    elsewhere = _seed_member(app)
    context = browser.new_context()
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        _sign_in(page, mail_server, owner["email"])
        row = _invite_from_team_page(page, mail_server, elsewhere["email"])
        assert row.locator('[data-invitation-kind="has-account"]').count() == 1
        assert row.locator('[data-invitation-delivery="sent"]').count() == 1
        link = _link_in(smtp_sink.text_to(elsewhere["email"]), "/account/join/")
    finally:
        context.close()

    context = browser.new_context()
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        # Opening the link signed out goes to sign in, then back to the invitation.
        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_url("**/account/login**", timeout=PAGE_TIMEOUT)
        page.fill("#email", elsewhere["email"])
        page.fill("#password", PASSWORD)
        page.click("#submit")
        page.get_by_role("heading", name="Join %s" % owner["org_name"]).wait_for(timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Accept invitation").click()
        page.wait_for_url(lambda u: "/account/join/" not in u, timeout=PAGE_TIMEOUT)

        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_text("This invitation no longer works").wait_for(timeout=PAGE_TIMEOUT)
    finally:
        context.close()

    with app.app_context():
        from app.models.org_role import OrgRole
        from app.models.user import User

        member = User.find_by_email(elsewhere["email"])
        assert OrgRole.get_role(owner["org_id"], member.id) == "architect"

    # After a reload the owner no longer sees it as an open invitation.
    context = browser.new_context()
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        _sign_in(page, mail_server, owner["email"])
        page.goto(mail_server + "/admin/team", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.locator('[data-invitation-email="%s"]' % elsewhere["email"]).count() == 0
    finally:
        context.close()


def test_a_withdrawn_invitation_frees_the_address_to_register(browser, mail_server, smtp_sink, app):
    owner = _seed_member(app, org_admin=True)
    invitee = "withdrawn.%s@example.com" % uuid.uuid4().hex[:8]
    context = browser.new_context()
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        _sign_in(page, mail_server, owner["email"])
        _invite_from_team_page(page, mail_server, invitee)
        link = _link_in(smtp_sink.text_to(invitee), "/account/join/")
        page.get_by_role("button", name="Withdraw invitation to %s" % invitee).click()
        page.wait_for_url("**/admin/team", timeout=PAGE_TIMEOUT)
        page.reload(wait_until="domcontentloaded")
        assert page.locator('[data-invitation-email="%s"]' % invitee).count() == 0
    finally:
        context.close()

    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_text("This invitation no longer works").wait_for(timeout=PAGE_TIMEOUT)

        page.goto(mail_server + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#first_name", "Freed")
        page.fill("#last_name", "Address")
        page.fill("#email", invitee)
        page.fill("#password", NEW_PASSWORD)
        page.fill("#password2", NEW_PASSWORD)
        page.click("#submit")
        page.wait_for_url(lambda u: "/account/register" not in u, timeout=PAGE_TIMEOUT)
    finally:
        context.close()

    with app.app_context():
        from app.models.user import User

        registered = User.find_by_email(invitee)
        assert registered is not None
        assert registered.organization_id != owner["org_id"]
        assert registered.verify_password(NEW_PASSWORD)


def test_without_a_mail_server_the_reset_page_says_so(browser, live_server):
    page = browser.new_page()
    try:
        page.goto(live_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Forgot password?").click()
        page.wait_for_url("**/account/reset-password", timeout=PAGE_TIMEOUT)
        alert = page.locator('[data-reset-state="mail-unavailable"]')
        alert.wait_for(timeout=PAGE_TIMEOUT)
        assert "E-mail is not available on this server" in alert.inner_text()
        assert page.locator("#email").count() == 0
    finally:
        page.close()
