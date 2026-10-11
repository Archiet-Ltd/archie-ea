"""An ARB member records a conditional approval with a review date.

In a brand-new organisation a solution architect raises two reviews from
the ARB reviews page ("New Review") and submits each for review from its own
page. An ARB member of the same organisation opens the first, chooses
"Approve with Conditions", enters a rationale, two conditions (one per line)
and a review date, and confirms. After a reload the review page shows the
outcome, both conditions and the review date. The second review is approved
without a review date, and its page shows "—" for it rather than a date the
board never set. Before this, the decision form had no review date at all.

An ARB member of a second new organisation cannot open either review and
does not see them in the reviews list.
"""

import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _new_page(browser):
    """A fresh browser session with the first-login welcome tour already seen."""
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    return context, context.new_page()


def _open_reviews(page, base):
    page.goto(base + "/arb/reviews", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=PAGE_TIMEOUT)


def _raise_review(page, base, title):
    """Create a review through the "New Review" dialog and submit it from its page."""
    _open_reviews(page, base)
    page.get_by_role("button", name="New Review").first.click()
    dialog = page.locator("#create-arb-review")
    expect(dialog.locator("#car-title")).to_be_visible(timeout=PAGE_TIMEOUT)
    dialog.locator("#car-title").fill(title)
    expect(dialog.locator("#car-type option[value='architecture_change']")).to_have_count(1, timeout=PAGE_TIMEOUT)
    dialog.locator("#car-type").select_option("architecture_change")
    dialog.locator("#car-decision-sought").select_option("approve_integration_pattern")
    dialog.locator("#car-description").fill("Replace the batch interface with an event feed.")
    dialog.get_by_label("I understand this will be a record-only item").check()
    with page.expect_response(
        lambda r: r.url.endswith("/arb/reviews/create") and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as created:
        dialog.get_by_role("button", name="Submit for Review").click()
    assert created.value.status == 201, "raising the review answered %s" % created.value.status
    review_id = created.value.json()["id"]

    page.wait_for_timeout(1200)   # the dialog reloads the list after a short toast
    _open_reviews(page, base)
    link = page.locator("a[href='/arb/reviews/%d']" % review_id)
    expect(link).to_have_count(1, timeout=PAGE_TIMEOUT)
    link.click()
    page.wait_for_url(re.compile(r"/arb/reviews/%d$" % review_id), timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name="Submit for review").click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("button", name="Submit for review")).to_have_count(0, timeout=PAGE_TIMEOUT)
    return review_id


def _decide(page, base, review_id, choice, rationale, conditions=None, review_date=None):
    page.goto(base + "/arb/reviews/%d" % review_id, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.get_by_role("button", name=choice).click()
    form = page.locator("form[action$='/arb/reviews/%d/decision']" % review_id)
    expect(form).to_be_visible(timeout=PAGE_TIMEOUT)
    form.get_by_label("Rationale").fill(rationale)
    if conditions:
        form.get_by_label("Conditions (one per line)").fill("\n".join(conditions))
    if review_date:
        form.get_by_label("Review date").fill(review_date)
    with page.expect_response(
        lambda r: r.url.endswith("/arb/reviews/%d/decision" % review_id) and r.request.method == "POST",
        timeout=PAGE_TIMEOUT,
    ) as decided:
        form.get_by_role("button", name="Confirm Decision").click()
    assert decided.value.status in (200, 302), "recording the decision answered %s" % decided.value.status
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)


def _decision_card(page):
    card = page.locator("dl").filter(has=page.get_by_test_id("decision-review-date"))
    expect(card).to_have_count(1, timeout=PAGE_TIMEOUT)
    return card


def test_arb_member_records_conditions_and_a_review_date(browser, live_server):
    org = create_fresh_org("solution_architect", extra_roles=("arb_member",))
    other = create_fresh_org("arb_member")
    suffix = org["suffix"]
    conditional = "Event feed for ledger %s" % suffix
    plain = "Retire batch export %s" % suffix
    conditions = ["Publish the event schema to the catalogue",
                  "Run both interfaces in parallel for one billing cycle"]

    context, page = _new_page(browser)
    sign_in(page, live_server, org["emails"]["solution_architect"])
    conditional_id = _raise_review(page, live_server, conditional)
    plain_id = _raise_review(page, live_server, plain)
    context.close()

    context, page = _new_page(browser)
    sign_in(page, live_server, org["emails"]["arb_member"])
    _decide(page, live_server, conditional_id, "Approve with conditions",
            "Sound design; two safeguards before cut-over.", conditions, "2027-01-15")
    _decide(page, live_server, plain_id, "Approve this review", "No concerns.")

    page.goto(live_server + "/arb/reviews/%d" % conditional_id, wait_until="domcontentloaded",
              timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    card = _decision_card(page)
    expect(card).to_contain_text("Approved With Conditions")
    expect(card).to_contain_text("Sound design; two safeguards before cut-over.")
    for condition in conditions:
        expect(card.get_by_text(condition)).to_have_count(1)
    expect(page.get_by_test_id("decision-review-date")).to_have_text("15 Jan 2027")

    page.goto(live_server + "/arb/reviews/%d" % plain_id, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    card = _decision_card(page)
    expect(card).to_contain_text("Approved")
    expect(page.get_by_test_id("decision-review-date")).to_have_text("—")
    context.close()

    # Another organisation's board member cannot reach either review.
    context, page = _new_page(browser)
    sign_in(page, live_server, other["emails"]["arb_member"])
    for review_id in (conditional_id, plain_id):
        response = page.goto(live_server + "/arb/reviews/%d" % review_id, wait_until="domcontentloaded",
                             timeout=PAGE_TIMEOUT)
        assert response.status == 404, "another organisation's review answered %s" % response.status
    _open_reviews(page, live_server)
    expect(page.get_by_text(conditional)).to_have_count(0)
    expect(page.locator("a[href='/arb/reviews/%d']" % conditional_id)).to_have_count(0)
    context.close()
