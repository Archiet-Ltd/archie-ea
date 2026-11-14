"""A lecturer signs up with a university address, opens the mailed link and is on Education.

Driven in a real browser against a live server with a real mail sink: the person fills in the sign-up
form, the confirmation link is read from the message that reached the sink, and everything after is
clicked. An address the list does not match carries on with Community.
"""
import os
import uuid
from datetime import date, datetime

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .test_account_mail_journey import _DISMISS_ONBOARDING_SCRIPT, _link_in, mail_server, smtp_sink  # noqa: F401

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# Opt-in walk screenshots: off unless the variable names a folder.
WALK = os.environ.get("EDUCATION_SIGNUP_WALK_DIR")


def _shot(page, name):
    if WALK:
        os.makedirs(WALK, exist_ok=True)
        page.screenshot(path=os.path.join(WALK, name + ".png"), full_page=True)


def _end(today):
    end = date(today.year, 8, 31)
    if end < today or (end - today).days < 60:
        end = date(today.year + 1, 8, 31)
    return end


def _register(page, base, address):
    page.goto(base + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#first_name", "Lena")
    page.fill("#last_name", "Marti")
    page.fill("#email", address)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    page.click("#submit")
    page.wait_for_url(lambda u: "/account/register" not in u, timeout=PAGE_TIMEOUT)


def _sign_in(page, base, address):
    page.goto(base + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.fill("#email", address)
    page.fill("#password", PASSWORD)
    page.locator("#submit").click()
    page.wait_for_url(lambda u: "/account/login" not in u, timeout=PAGE_TIMEOUT)


def test_a_lecturer_confirms_a_university_address_and_is_on_education(browser, mail_server, smtp_sink):  # noqa: F811
    address = "l.marti.%s@cs.uda.ad" % uuid.uuid4().hex[:8]
    until = _end(datetime.utcnow().date())
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        _register(page, mail_server, address)
        page.goto(_link_in(smtp_sink.text_to(address), "/account/confirm-account/"),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_url("**/education/eligibility", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("You're at University of Andorra")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("education-institution")).to_contain_text("University of Andorra")
        expect(page.get_by_test_id("education-institution")).to_contain_text(address)
        _shot(page, "eligibility-1440-light")

        # Nothing is granted by arriving, and the form does not go without the terms ticked.
        page.get_by_label("I teach").check()
        page.get_by_test_id("education-start").click()
        assert page.url.endswith("/education/eligibility"), page.url

        page.get_by_label("I teach").check()
        page.get_by_label("I will use this free plan for teaching or learning").check()
        page.get_by_test_id("education-start").click()
        page.wait_for_url("**/admin/billing/", timeout=PAGE_TIMEOUT)
        expected = "Education, free until 31 Aug %d" % until.year
        expect(page.get_by_test_id("billing-education")).to_have_text(expected, timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("billing-current-plan")).to_have_text("Education")
        expect(page.get_by_test_id("billing-renewal")).to_have_text("31 Aug %d" % until.year)
        expect(page.get_by_test_id("billing-people-limit")).to_contain_text("of unlimited people")
        expect(page.get_by_test_id("plan-card-education")).to_have_count(0)
        _shot(page, "billing-1440-light")

        # It is still there after a reload, and the eligibility page says so instead of asking again.
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_test_id("billing-education")).to_have_text(expected)
        page.goto(mail_server + "/education/eligibility", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Your organisation is on Education")).to_be_visible(timeout=PAGE_TIMEOUT)
        page.get_by_test_id("education-billing").click()
        page.wait_for_url("**/admin/billing/", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("billing-education")).to_have_text(expected)
    finally:
        context.close()

    narrow = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    narrow.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = narrow.new_page()
    try:
        _sign_in(page, mail_server, address)
        page.goto(mail_server + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("billing-education")).to_have_text(expected, timeout=PAGE_TIMEOUT)
        _shot(page, "billing-390-dark")
        page.goto(mail_server + "/education/eligibility", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Your organisation is on Education")).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page, "eligibility-active-390-dark")
    finally:
        narrow.close()


def test_an_alumni_address_is_offered_community_and_never_refused(browser, mail_server, smtp_sink):  # noqa: F811
    address = "old.student.%s@alumni.uda.ad" % uuid.uuid4().hex[:8]
    context = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    context.add_init_script(_DISMISS_ONBOARDING_SCRIPT)
    page = context.new_page()
    try:
        _register(page, mail_server, address)
        page.goto(_link_in(smtp_sink.text_to(address), "/account/confirm-account/"),
                  wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_load_state("domcontentloaded")
        assert "/education" not in page.url, page.url
        page.goto(mail_server + "/education/eligibility", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("We don't recognise this address as a university or college")).to_be_visible(
            timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("education-start")).to_have_count(0)
        _shot(page, "eligibility-no-match-390-dark")
        page.get_by_test_id("education-community").click()
        page.wait_for_url(lambda u: "/education" not in u, timeout=PAGE_TIMEOUT)
        expect(page.locator("main").first).not_to_be_empty()
        page.goto(mail_server + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("billing-current-plan")).to_have_text("Community", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("billing-education")).to_have_count(0)
    finally:
        context.close()
