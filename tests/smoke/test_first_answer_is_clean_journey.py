"""A new startup CTO reads a clean first answer, and the sample loads quickly every time.

The person: a self-serve owner (lens "cto", not a platform administrator, asserted in each
journey) who has just signed up. The goal: load the sample company the chat offers, ask what
breaks if the payments service fails, and read an answer that names its assistant, says every
link in plain words and never prints a missing value.

The sample company is offered in the chat, next to Connect GitHub, to an owner who has recorded
nothing yet; loading it draws its applications, owners and connections into the model, and the
"You are looking at the sample company" bar with the way to remove it appears on every page.
"""
import os
import re
import time

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .test_sign_up_first_screen_journey import QUESTION, SIZES
from .test_signup_brings_the_company_in_journey import (
    assert_is_a_startup_cto_not_an_administrator,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK = os.environ.get("ONB_WALK_DIR")


def _shot(page, name, width):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, "%s-%d.png" % (name, width)), full_page=True)


def _sign_up_a_new_owner(page, base, app):
    """Sign up a fresh owner and carry past the welcome steps to their own dashboard, the way a
    person who connects nothing does. Returns the owner's address."""
    email, _started = sign_up(page, base, [])
    assert_is_a_startup_cto_not_an_administrator(app, email)
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=3")
    page.get_by_test_id("signup-continue").click()
    page.wait_for_url("**/account/welcome?step=4")
    page.get_by_role("button", name="Finish").click()
    page.wait_for_url("**/dashboard/**")
    return email


def _load_sample(page, base):
    """Load the sample company from the chat's first-steps offer (the one place the product
    offers it), and wait for the "You are looking at the sample company" bar. Returns the
    seconds the load took."""
    page.goto(base + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
    steps = page.get_by_test_id("startup-first-steps")
    expect(steps).to_be_visible(timeout=PAGE_TIMEOUT)
    started = time.time()
    steps.get_by_role("button", name="See a sample company").click()
    expect(page.locator('[data-testid="sample-company-indicator"]')).to_be_visible(timeout=PAGE_TIMEOUT * 2)
    return time.time() - started


@pytest.mark.parametrize("name,viewport", SIZES, ids=[s[0] for s in SIZES])
def test_the_first_answer_is_clean_and_in_plain_words(browser, live_server, app, name, viewport):
    context = browser.new_context(viewport=viewport)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        _sign_up_a_new_owner(page, live_server, app)
        _load_sample(page, live_server)

        # "Remove it" is named for what it says (a visible label must be part of its name)
        remove = page.get_by_role("button", name="Remove it", exact=True)
        assert remove.count() == 1
        assert remove.inner_text().strip() == "Remove it"
        _shot(page, "c-sample-loaded", viewport["width"])

        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        chosen = page.locator("#persona-selector").evaluate("e => e.options[e.selectedIndex].text")
        assert chosen == "CTO / technical founder", chosen

        page.locator("#user-input").fill(QUESTION)
        page.locator("#user-input").press("Enter")
        answer = page.get_by_test_id("estate-answer")
        expect(answer).to_be_visible(timeout=PAGE_TIMEOUT)
        assert answer.locator("[data-answer-row]").count() >= 10

        # what a person reads first is plain: no technical wording, no missing value
        text = answer.inner_text()
        for technical in ("derivation table", "followed by", "started by A person"):
            assert technical not in text, text
        assert not re.search(r"\bNone\b", text), text
        _shot(page, "c-answer-plain", viewport["width"])
        # the technical detail is one click away, behind "How this was worked out"
        assert answer.locator("[data-why-detail]:visible").count() == 0
        assert answer.locator("[data-why-toggle]").count() >= 1
        for toggle in answer.locator("[data-why-toggle]").all():
            toggle.click()
        _shot(page, "c-answer-opened", viewport["width"])
        opened = answer.inner_text()
        assert "ArchiMate derivation table" in opened or "no stronger rule applies" in opened, opened
        assert not re.search(r"\bNone\b", opened), opened
        # every citation names a source in words
        for citation in answer.locator("[data-citation]").all():
            assert re.search(r"[A-Za-z]{4}", citation.inner_text()), citation.inner_text()
        # the plain sentence is shown and the technical detail sits behind the disclosure
        assert answer.locator("[data-why-sentence]").count() >= 1
        assert answer.locator("[data-why-detail]").count() >= 1

        # the empty-context sentence is a full sentence naming the context in use
        page.locator("#domain-context").wait_for(state="attached")
        context_text = page.locator("#domain-context").inner_text()
        assert "No specific context available" not in context_text, context_text
    finally:
        context.close()


def test_a_second_press_does_not_load_the_sample_twice(browser, live_server, app):
    """A person who presses again -- a double tap on a slow phone, or a return to the offer --
    must not get a second sample: the load is held to one per organisation, a second press finds
    it already loaded, and the model carries the sample once, not twice."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        email = _sign_up_a_new_owner(page, live_server, app)
        _load_sample(page, live_server)

        def sample_elements():
            with app.app_context():
                from app import db
                from app.models.archimate_core import ArchiMateElement
                from app.models.user import User

                org_id = User.query.filter_by(email=email).one().organization_id
                count = ArchiMateElement.query.filter_by(organization_id=org_id).count()
                db.session.remove()
                return count

        before = sample_elements()
        assert before > 0, "the sample loaded nothing into the model"

        # Press again from the chat's offer; the sample is already loaded, so the page says so and
        # nothing is imported a second time.
        page.goto(live_server + "/ai-chat", wait_until="networkidle", timeout=PAGE_TIMEOUT)
        again = page.get_by_test_id("startup-first-steps").get_by_role("button", name="See a sample company")
        if again.count():
            again.click()
            page.wait_for_load_state("networkidle")
        expect(page.locator('[data-testid="sample-company-indicator"]')).to_have_count(1)
        assert sample_elements() == before, "a second press loaded the sample again"
    finally:
        context.close()


def test_the_sample_loads_in_under_ten_seconds_every_time_on_a_phone_in_the_dark(browser, live_server, app):
    """Five new owners in a row, each loading the sample at 390 wide in the dark: every load ends
    with the sample in their model in under ten seconds (the first, on a cold server, in twenty),
    and the dashboard then counts its applications instead of asking for a source."""
    timings = []
    for _ in range(5):
        context = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
        context.set_default_timeout(PAGE_TIMEOUT)
        context.add_init_script(
            "try { localStorage.setItem('theme', 'dark'); } catch (e) {} "
            "document.documentElement.classList.add('dark');"
        )
        page = context.new_page()
        try:
            _sign_up_a_new_owner(page, live_server, app)
            timings.append(_load_sample(page, live_server))
            # The sample is in the model: the dashboard counts its applications rather than
            # offering a source to connect.
            page.goto(live_server + "/dashboard/overview", wait_until="networkidle", timeout=PAGE_TIMEOUT)
            applications = page.get_by_test_id("welcome-applications")
            expect(applications).to_be_visible(timeout=PAGE_TIMEOUT)
            assert int(applications.locator("span").first.inner_text().strip()) > 0
        finally:
            context.close()
    print("\n[onb05c] sample load seconds at 390 dark: %s" % ", ".join("%.1f" % t for t in timings))
    # The first load after the server starts also pays for loading its code; every one after it
    # is the load a person meets, and must end inside ten seconds.
    assert timings[0] < 20, timings
    assert max(timings[1:]) < 10, timings
