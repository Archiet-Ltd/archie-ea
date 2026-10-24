"""Shared steps for journeys that ask a question in the AI chat and read its answer cards."""

from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD


def sign_in(page, base, email):
    """Sign in from a signed-out browser (switching persona mid-test otherwise lands on
    the login page already authenticated, which redirects away)."""
    page.context.clear_cookies()
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda url: "/account/login" not in url, timeout=PAGE_TIMEOUT)


def role_of(email):
    """The enterprise role the signed-in user holds, read from the database."""
    from app import create_app, db

    app = create_app("testing")
    with app.app_context():
        from app.models.user import User

        role = User.query.filter_by(email=email).one().enterprise_role
        db.session.remove()
        return role


def sign_in_as(page, base, seeded, persona):
    """Sign in as the named persona and assert that is the role held, not an administrator."""
    email = seeded["emails"][persona]
    assert role_of(email) == persona, "%s is not the %s persona" % (email, persona)
    sign_in(page, base, email)


def open_chat(page, base):
    page.goto(base + "/ai-chat", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.locator("#user-input")).to_be_visible(timeout=PAGE_TIMEOUT)


def ask(page, base, question, *, fresh=True):
    """Ask *question* in the chat (opening it first when *fresh*) and return the newest
    answer, drawn as cards."""
    if fresh:
        open_chat(page, base)
    before = page.get_by_test_id("estate-answer").count()
    box = page.locator("#user-input")
    box.fill(question)
    box.press("Enter")
    answer = page.get_by_test_id("estate-answer")
    expect(answer).to_have_count(before + 1, timeout=PAGE_TIMEOUT)
    return answer.nth(before)
