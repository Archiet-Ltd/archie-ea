"""Wave 1 e-mail: every message a person is sent arrives, reads plainly and its links work.

Five messages, each requested from the browser the way a person requests it, taken off
a real SMTP session into a sink in this process (the server runs with MAIL_SUPPRESS_SEND
off, so a message that fails to render or send fails here, which the in-memory outbox
used under testing would hide):

  * invitation to join an organisation
  * password reset
  * confirm your e-mail address (sign-up)
  * payment received (a test-mode purchase against the payment-provider stand-in)
  * subscription cancelled

Each message must name what happened, say what to do next and say how to get help, carry
no internal wording, and every link in it must land on a page with content at 1440 and
390 wide. Set MAIL01_SHOTS to a folder to keep each message as received (HTML and text).
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from .test_account_mail_journey import (  # noqa: F401  (the fixtures are used by name)
    NEW_PASSWORD,
    _DISMISS_ONBOARDING_SCRIPT,
    _invite_from_team_page,
    _link_in,
    _seed_member,
    _sign_in,
    mail_server,
    smtp_sink,
)
from .test_signup_brings_the_company_in_journey import people  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("MAIL01_SHOTS")

# Words a customer must never meet in a message: internal names, ids, suppliers, template leaks.
_INTERNAL = (
    "organization_id", "org_id", "user_id", "{{", "{%", "none", "undefined", "traceback", "unknown user",
    "stripe", "openai", "anthropic", "claude", "nvidia", "gemini", "deepseek", "openrouter", "llm",
    "orchestrator", "agent", "monelytics", "not monitored",
)


def _parts(message):
    text = html = None
    for part in message.walk():
        kind = part.get_content_type()
        if kind == "text/plain" and text is None:
            text = part.get_payload(decode=True).decode("utf-8", "replace")
        elif kind == "text/html" and html is None:
            html = part.get_payload(decode=True).decode("utf-8", "replace")
    return text, html


def _message_to(sink, address, subject_has, timeout=30):
    """The newest message to *address* whose subject contains *subject_has*."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        for msg in reversed(sink.messages):
            if address in msg.get("To", "") and subject_has.lower() in str(msg.get("Subject", "")).lower():
                return msg
        time.sleep(0.25)
    raise AssertionError("no %r message to %s reached the SMTP sink; saw %s" % (
        subject_has, address, [str(m.get("Subject")) for m in sink.messages if address in m.get("To", "")]))


def _keep(name, message):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    text, html = _parts(message)
    with open(os.path.join(SHOTS, name + ".txt"), "w", encoding="utf-8") as handle:
        handle.write("Subject: %s\nTo: %s\n\n%s" % (message.get("Subject"), message.get("To"), text or ""))
    with open(os.path.join(SHOTS, name + ".html"), "w", encoding="utf-8") as handle:
        handle.write(html or "")


def _assert_reads_plainly(message, *, subject_has, says):
    """What happened (*says*), what to do next, how to get help, nothing internal."""
    subject = str(message.get("Subject"))
    assert subject_has.lower() in subject.lower(), subject
    text, html = _parts(message)
    assert text and html, "a message needs both a text and an HTML version"
    for body in (text, html):
        lowered = re.sub(r"<[^>]+>", " ", body).lower() if body is html else body.lower()
        for word in _INTERNAL:
            assert not re.search(r"(?<![a-z])" + re.escape(word) + r"(?![a-z])", lowered), \
                "%r in the message %r:\n%s" % (word, subject, body)
        assert not re.search(r"(dear|hello|hi)\s+[^,\n]*@", lowered), "greets by e-mail address:\n" + body
        assert "need help" in lowered, "no way to get help in %r" % subject
        for phrase in says:
            assert phrase.lower() in lowered, "%r missing from %r:\n%s" % (phrase, subject, body)
    # The support route is something a person can act on: an address or a page.
    assert re.search(r"https?://\S+/contact|[\w.+-]+@[\w-]+\.[\w.]+", text), "no support route:\n" + text


def _links(message):
    text, _ = _parts(message)
    return [url.rstrip(".,)") for url in re.findall(r"https?://\S+", text)]


def _lands_with_content(browser, url, *, storage_state=None):
    """Open *url* at 1440 and 390 wide: a page with words on it, not an error or an empty shell."""
    for width in (1440, 390):
        context = browser.new_context(viewport={"width": width, "height": 900}, storage_state=storage_state)
        context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
        page = context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            assert response.status < 400, "%s answered %s at %d" % (url, response.status, width)
            body = page.locator("body").inner_text().strip()
            assert len(body) > 80, "%s is empty at %d: %r" % (url, width, body)
            assert "internal server error" not in body.lower()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), \
                "%s overflows sideways at %d" % (url, width)
        finally:
            context.close()


def test_the_invitation_arrives_and_reads_plainly(browser, mail_server, smtp_sink, app):
    owner = _seed_member(app, org_admin=True)
    invitee = "invitee.%s@example.com" % uuid.uuid4().hex[:8]
    context = browser.new_context()
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        _sign_in(page, mail_server, owner["email"])
        _invite_from_team_page(page, mail_server, invitee)
    finally:
        context.close()
    message = _message_to(smtp_sink, invitee, "invit")
    _keep("invitation", message)
    _assert_reads_plainly(message, subject_has="invit", says=[owner["org_name"], "choose a password"])
    links = _links(message)
    assert any("/account/join/" in link for link in links)
    for link in links:
        _lands_with_content(browser, link)


def test_the_password_reset_arrives_and_reads_plainly(browser, mail_server, smtp_sink, app):
    member = _seed_member(app)
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(mail_server + "/account/reset-password", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", member["email"])
        page.get_by_role("button", name="Reset password").click()
        page.get_by_text("Check your e-mail").wait_for(timeout=PAGE_TIMEOUT)
    finally:
        context.close()
    message = _message_to(smtp_sink, member["email"], "password")
    _keep("password-reset", message)
    _assert_reads_plainly(message, subject_has="password", says=["new password", "did not ask"])
    links = _links(message)
    assert any("/account/reset-password/" in link for link in links)
    for link in links:
        _lands_with_content(browser, link)


def test_the_confirmation_arrives_and_reads_plainly(browser, mail_server, smtp_sink, people, app):
    address = "confirm.%s@example.com" % uuid.uuid4().hex[:8]
    people.append(address)
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        page.goto(mail_server + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#first_name", "Nia")
        page.fill("#last_name", "Newperson")
        page.fill("#email", address)
        page.fill("#password", PASSWORD)
        page.fill("#password2", PASSWORD)
        page.click("#submit")
        page.wait_for_url("**/account/unconfirmed", timeout=PAGE_TIMEOUT)
        message = _message_to(smtp_sink, address, "confirm")
        _keep("confirm-email", message)
        _assert_reads_plainly(message, subject_has="confirm", says=["confirm your e-mail address"])
        links = _links(message)
        confirm = _link_in(_parts(message)[0], "/account/confirm-account/")
        for link in links:
            if link != confirm:
                _lands_with_content(browser, link)
        # The link that confirms is opened last: it is used up by being followed.
        page.goto(confirm, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_url("**/account/welcome", timeout=PAGE_TIMEOUT)
        assert len(page.locator("main").inner_text().strip()) > 80
    finally:
        context.close()


@pytest.fixture(scope="module")
def purchase_server(request, app, smtp_sink):
    """A server with a real mail session and the payment provider's test-mode stand-in."""
    from .payment_provider_stub import PaymentProviderStub

    with PaymentProviderStub() as stub:
        env = stub.child_environment({})
        env.update({
            "MAIL_SERVER": "127.0.0.1",
            "MAIL_PORT": str(smtp_sink.server_address[1]),
            "MAIL_USE_TLS": "false",
            "MAIL_USE_SSL": "false",
            "MAIL_USERNAME": "",
            "MAIL_PASSWORD": "",
            "MAIL_DEFAULT_SENDER": "no-reply@example.com",
            "MAIL_SUPPRESS_SEND": "false",
        })
        yield boot_live_server(request, None, app, extra_env=env), stub


def test_the_receipt_and_the_cancellation_arrive_and_read_plainly(browser, purchase_server, smtp_sink, app):
    base, _stub = purchase_server
    owner = _seed_member(app, org_admin=True)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _sign_in(page, base, owner["email"])
        page.goto(base + "/admin/billing/?plan=startup&interval=year", wait_until="domcontentloaded")
        page.get_by_test_id("checkout-submit").click()
        page.wait_for_url(re.compile(r"/pay/cs_test_"))
        page.get_by_role("button", name="Pay").click()
        page.wait_for_url(re.compile(r"/admin/billing/$"))
        expect(page.locator("#platform-toast-container")).to_contain_text("Payment received")

        receipt = _message_to(smtp_sink, owner["email"], "payment")
        _keep("payment-receipt", receipt)
        _assert_reads_plainly(receipt, subject_has="payment", says=["Startup", "USD"])
        state = context.storage_state()
        for link in _links(receipt):
            _lands_with_content(browser, link, storage_state=state)

        page.reload(wait_until="domcontentloaded")
        page.get_by_role("button", name="Cancel subscription").click()
        page.get_by_role("button", name="Cancel at the end of the period").click()
        expect(page.locator("#platform-toast-container")).to_contain_text("Your subscription is cancelled.")

        cancelled = _message_to(smtp_sink, owner["email"], "cancel")
        _keep("subscription-cancelled", cancelled)
        _assert_reads_plainly(cancelled, subject_has="cancel", says=["Startup", "keep"])
        for link in _links(cancelled):
            _lands_with_content(browser, link, storage_state=state)
    finally:
        context.close()
