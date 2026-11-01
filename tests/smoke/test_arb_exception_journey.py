"""An architect asks the board for an exception and the board decides it, on the review page.

In a new organisation a solution architect raises a review, opens it and uses
"Request an exception": a standard, a type, a reason, a duration and the
elements it covers. A duration of 0 is refused in the form in plain words. The
request then shows on the review as Requested with the end date it would have,
and the architect is offered no Approve or Refuse. A board member of the same
organisation opens the review, approves one request with a reason and refuses
the other with a reason. After a reload the review shows the first as Approved
with its end date and the reason, and the second as Refused with its reason.

A board member of a second organisation cannot open the review at all.
"""

import re
import uuid
from datetime import datetime, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


@pytest.fixture
def standard():
    """A governance standard to ask an exception against; removed by its key afterwards."""
    from app import create_app, db
    from app.models.architecture_review_board import ARBGovernanceStandard

    code = "STD-J-%s" % uuid.uuid4().hex[:8]
    app = create_app("testing")
    with app.app_context():
        row = ARBGovernanceStandard(code=code, name="Single sign-on for every service", status="active")
        db.session.add(row)
        db.session.commit()
    yield "%s Single sign-on for every service" % code
    with app.app_context():
        ARBGovernanceStandard.query.filter_by(code=code).delete()
        db.session.commit()


def _new_page(browser):
    """A fresh browser session with the first-login welcome tour already seen."""
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _raise_review(page, base, title):
    """Raise a review through the same interface the New Review dialog calls."""
    page.goto(base + "/arb/reviews", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    status, body = api(page, "POST", "/arb/reviews/create", {
        "title": title,
        "description": "Replace the batch interface with an event feed.",
        "review_type": "architecture_change",
        "decision_sought": "approve_integration_pattern",
        "priority": "medium",
    })
    assert status == 201, "raising the review answered %s: %s" % (status, body)
    return body["id"]


def _request_exception(page, label, reason, scope, days):
    page.get_by_test_id("request-exception").click()
    form = page.get_by_test_id("exception-request-form")
    expect(form).to_be_visible(timeout=PAGE_TIMEOUT)
    form.get_by_label("Standard").select_option(label=label)
    form.get_by_label("Type").select_option("waiver")
    form.get_by_label("Reason").fill(reason)
    form.get_by_label("Duration in days").fill(str(days))
    form.get_by_label("Elements it covers").fill(scope)
    return form


def _row(page, text):
    return page.get_by_test_id("exception-row").filter(has_text=text)


def test_architect_requests_and_the_board_decides_an_exception(browser, live_server, standard):
    org = create_fresh_org("solution_architect", extra_roles=("arb_member",))
    other = create_fresh_org("arb_member")
    title = "Customer portal sign-in %s" % org["suffix"]
    approved_reason = "Legacy portal is retired in the spring, so the exception is short and safe."
    refused_reason = "Nothing here justifies leaving the service without single sign-on."
    asked_for = "Customer portal and the partner extranet"
    other_ask = "Billing batch service"

    context, page = _new_page(browser)
    sign_in(page, live_server, org["emails"]["solution_architect"])
    review_id = _raise_review(page, live_server, title)
    review_url = live_server + "/arb/reviews/%d" % review_id
    page.goto(review_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # Nothing asked yet: the page says so and says what to do.
    exceptions = page.get_by_test_id("review-exceptions")
    expect(exceptions).to_contain_text("No exception has been requested on this review")

    # A duration of 0 is refused in the form, in plain words, and nothing is saved.
    form = _request_exception(page, standard, "The portal cannot move yet", asked_for, 0)
    form.get_by_test_id("exception-request-submit").click()
    expect(page.get_by_test_id("exception-request-error")).to_contain_text("1 or more", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("exception-row")).to_have_count(0)

    # A valid request is saved and shown on the review after the reload.
    form.get_by_label("Duration in days").fill("30")
    form.get_by_test_id("exception-request-submit").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    first = _row(page, asked_for)
    expect(first).to_have_count(1, timeout=PAGE_TIMEOUT)
    expect(first).to_contain_text("The portal cannot move yet")
    expect(first).to_contain_text("Requested")
    expect(first).to_contain_text("EXC-")
    expect(first).to_contain_text("Not decided yet")
    ends = (datetime.utcnow() + timedelta(days=30)).strftime("%d %b %Y")
    expect(first.get_by_test_id("exception-ends")).to_have_text(ends)

    _request_exception(page, standard, "A second service has the same gap", other_ask, 14)
    page.get_by_test_id("exception-request-submit").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("exception-row")).to_have_count(2, timeout=PAGE_TIMEOUT)

    # The person who asked cannot decide it.
    expect(page.get_by_test_id("exception-approve")).to_have_count(0)
    expect(page.get_by_test_id("exception-refuse")).to_have_count(0)
    expect(first).to_contain_text("Waiting for a board approver")
    context.close()

    # The board member approves one with a reason and refuses the other with a reason.
    context, page = _new_page(browser)
    sign_in(page, live_server, org["emails"]["arb_member"])
    page.goto(review_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    first = _row(page, asked_for)
    first.get_by_test_id("exception-approve").click()
    decision = page.get_by_test_id("exception-decision-form")
    expect(decision).to_be_visible(timeout=PAGE_TIMEOUT)
    decision.get_by_test_id("exception-decision-submit").click()
    expect(page.get_by_test_id("exception-decision-error")).to_contain_text("reason", timeout=PAGE_TIMEOUT)
    decision.get_by_label("Reason").fill(approved_reason)
    decision.get_by_test_id("exception-decision-submit").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(_row(page, other_ask).get_by_test_id("exception-refuse")).to_have_count(1, timeout=PAGE_TIMEOUT)

    _row(page, other_ask).get_by_test_id("exception-refuse").click()
    decision = page.get_by_test_id("exception-decision-form")
    expect(decision).to_be_visible(timeout=PAGE_TIMEOUT)
    decision.get_by_label("Reason").fill(refused_reason)
    decision.get_by_test_id("exception-decision-submit").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)

    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    first = _row(page, asked_for)
    expect(first).to_contain_text("Approved", timeout=PAGE_TIMEOUT)
    expect(first.get_by_test_id("exception-ends")).to_have_text(ends)
    expect(first).to_contain_text(approved_reason)
    expect(first.get_by_test_id("exception-approve")).to_have_count(0)
    second = _row(page, other_ask)
    expect(second).to_contain_text("Refused")
    expect(second).to_contain_text(refused_reason)
    expect(second).to_contain_text("Not granted")
    context.close()

    # The architect sees the decisions too.
    context, page = _new_page(browser)
    sign_in(page, live_server, org["emails"]["solution_architect"])
    page.goto(review_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(_row(page, asked_for)).to_contain_text("Approved")
    expect(_row(page, asked_for)).to_contain_text(approved_reason)
    context.close()

    # Another organisation cannot reach the review or its exceptions.
    context, page = _new_page(browser)
    sign_in(page, live_server, other["emails"]["arb_member"])
    response = page.goto(review_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 404, "another organisation's review answered %s" % response.status
    expect(page.get_by_text(asked_for)).to_have_count(0)
    context.close()
