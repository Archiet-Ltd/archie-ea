"""A newcomer with nothing to connect sees a sample company on the first screen after sign-up.

The person: the owner of a brand-new organisation, on /account/welcome with no GitHub organisation or
Jira site to hand. The goal: press "See a sample company", land on a first screen showing the sample
company's ten applications, then ask the AI chat what breaks if the payments service fails and read an
answer with a source that opens a page with content. A double press imports the sample once.
"""
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

QUESTION = "What breaks if the payments service fails?"


def _shot(page, tmp_path, name, width, dark):
    folder = os.environ.get("SMOKE_SCREENSHOT_DIR") or str(tmp_path)
    os.makedirs(folder, exist_ok=True)
    page.screenshot(path=os.path.join(folder, "%s-%s-%s.png" % (name, width, "dark" if dark else "light")))


@pytest.mark.parametrize("width,dark", [(1440, False), (390, True)])
def test_a_newcomer_with_nothing_to_connect_gets_a_cited_answer_from_the_first_screen(
        live_server, browser, width, dark, tmp_path):
    address = "welcome.%s@example.com" % uuid.uuid4().hex[:8]
    company = "Sample offer %s" % uuid.uuid4().hex[:8]
    context = browser.new_context(viewport={"width": width, "height": 900},
                                  color_scheme="dark" if dark else "light")
    context.set_default_timeout(PAGE_TIMEOUT)
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page = context.new_page()
    try:
        page.goto(live_server + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#first_name", "Nia")
        page.fill("#last_name", "Newcomer")
        page.fill("#email", address)
        if page.locator("#organization_name").count():
            page.fill("#organization_name", company)
        page.fill("#password", PASSWORD)
        page.fill("#password2", PASSWORD)
        page.click("#submit")
        page.wait_for_url("**/account/welcome", timeout=PAGE_TIMEOUT)

        # The first screen offers the sample, secondary to connecting the company's own sources.
        offer = page.get_by_role("button", name="See a sample company")
        expect(offer).to_be_visible()
        expect(page.get_by_text("Nothing to connect yet? Look around a sample company first.")).to_be_visible()
        assert offer.get_attribute("data-testid") == "signup-sample"
        assert "bg-primary " not in (offer.get_attribute("class") or "") + " "
        _shot(page, tmp_path, "welcome-offer", width, dark)

        # Press it twice, quickly. At the moment of the first press the button locks and the busy line,
        # announced as a status, is showing; the second press sends nothing.
        page.evaluate("""() => {
            window.addEventListener('submit', (event) => {
                const form = event.target;
                const log = JSON.parse(sessionStorage.getItem('seen') || '[]');
                log.push({
                    stopped: event.defaultPrevented,
                    disabled: form.querySelector('button[type=submit]').disabled,
                    line: !form.querySelector('[data-sample-busy]').hidden,
                    role: form.querySelector('[data-sample-busy]').getAttribute('role'),
                });
                sessionStorage.setItem('seen', JSON.stringify(log));
            });
        }""")
        offer.dblclick()
        page.wait_for_url("**/dashboard/overview", timeout=PAGE_TIMEOUT * 4)
        text = page.locator("main, body").first.inner_text()
        assert re.search(r"\b10\b\s+Applications|Applications\s+10\b", text), text[:600]
        expect(page.get_by_test_id("sample-company-indicator").first).to_be_visible()
        _shot(page, tmp_path, "first-screen", width, dark)

        # One import, however many presses: ten applications, and pressing again adds none.
        seen = page.evaluate("JSON.parse(sessionStorage.getItem('seen') || '[]')")
        assert seen and seen[0] == {"stopped": False, "disabled": True, "line": True, "role": "status"}, seen
        assert _applications_of(address) == 10

        # The chat answers from the sample with a source that opens a page with content.
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        page.locator("#user-input").fill(QUESTION)
        page.locator("#user-input").press("Enter")
        answer = page.get_by_test_id("estate-answer")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)
        assert answer.locator("[data-answer-row]").count() >= 1
        assert answer.locator("[data-citation]").first.inner_text().strip() not in ("", "Source not recorded")
        href = answer.locator("[data-answer-row] a").first.get_attribute("href")
        assert href
        reply = page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert reply.status == 200
        assert page.locator("main, #main-content, body").first.inner_text().strip()
    finally:
        context.close()


def _applications_of(address):
    from app import create_app, db
    from app.models.application_portfolio import ApplicationComponent
    from app.models.user import User

    app = create_app("testing")
    with app.app_context():
        user = User.query.filter_by(email=address).first()
        count = ApplicationComponent.query.filter(
            ApplicationComponent.organization_id == user.organization_id).count()
        db.session.remove()
        return count
