"""A founder claims 90 days of Startup from the billing page, in a real browser.

From the pricing page's "Start with 90 days free", through sign-up, to the credit card on the
billing page: the founder declares the company, presses "Start 90 days free", and the page shows
Startup, "Free for 90 days" and the end date, which is still there after a reload. A second
founder on the same company domain is refused and still sees the Startup buy button, a founder
on a public mailbox is told to use a work email, and when the end passes the page reads as
Community with the alert and a Buy Startup link that lands on the Startup checkout step.
"""
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# The first-visit welcome tour is not part of this journey.
_SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"


def _founded_months_ago(months):
    now = datetime.utcnow()
    index = now.year * 12 + now.month - 1 - months
    return "%04d-%02d" % (index // 12, index % 12 + 1)


def _frame(page, tmp_path, name):
    folder = Path(os.environ.get("SMOKE_SCREENSHOT_DIR") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    for width, theme in ((1440, "light"), (390, "dark")):
        page.set_viewport_size({"width": width, "height": 900})
        page.evaluate("(t) => document.documentElement.classList.toggle('dark', t === 'dark')", theme)
        page.screenshot(path=str(folder / ("startup-credit-%s-%d-%s.png" % (name, width, theme))), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.evaluate("() => document.documentElement.classList.remove('dark')")


def _sign_up(browser, base, email, via_pricing=False):
    """A new founder signs up; the browser context comes back signed in."""
    context = browser.new_context()
    context.add_init_script(_SKIP_WELCOME)
    page = context.new_page()
    if via_pricing:
        page.goto(base + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("credit-startup").click()
    else:
        page.goto(base + "/account/register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_url(lambda url: "/account/register" in url, timeout=PAGE_TIMEOUT)
    page.fill("#first_name", "Ana")
    page.fill("#last_name", "Founder")
    page.fill("#email", email)
    page.fill("#password", PASSWORD)
    page.fill("#password2", PASSWORD)
    page.click("#submit")
    page.wait_for_url(lambda url: "/account/register" not in url, timeout=PAGE_TIMEOUT)
    return context, page


def _give_website(app, email, url):
    from app import db
    from app.models.user import User

    with app.app_context():
        org = User.query.filter_by(email=email).one().organization
        org.settings = {"onboarding": {"website": {"url": url}}}
        db.session.commit()
        db.session.remove()


def _claim(page, base, website, founded, raised="Up to USD 1M"):
    page.goto(base + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    card = page.locator("#startup-credit")
    expect(card.get_by_role("heading", name="90 days of Startup, free")).to_be_visible(timeout=PAGE_TIMEOUT)
    page.fill("#credit_website", website)
    page.fill("#credit_founded", founded)
    page.select_option("#credit_raised", label=raised)
    page.get_by_test_id("startup-credit-submit").click()
    page.wait_for_load_state("domcontentloaded")


def test_a_founder_claims_the_credit_and_the_other_founders_are_refused(browser, live_server, app, tmp_path):
    domain = "acme-%s.example" % uuid.uuid4().hex[:8]
    ana, bob = "ana@%s" % domain, "bob@eu.%s" % domain
    gmail = "founder.%s@gmail.com" % uuid.uuid4().hex[:8]
    contexts = []
    try:
        # 1. From the pricing page the founder signs up and arrives at the credit card.
        context, page = _sign_up(browser, live_server, ana, via_pricing=True)
        contexts.append(context)
        page.wait_for_url(lambda url: "/admin/billing" in url, timeout=PAGE_TIMEOUT)
        expect(page.locator("#startup-credit").get_by_role("heading", name="90 days of Startup, free")).to_be_visible()

        # 2. The website given at sign-up is filled in; the founder declares the company and claims.
        _give_website(app, ana, "https://%s" % domain)
        page.goto(live_server + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#credit_website")).to_have_value("https://%s" % domain)
        _frame(page, tmp_path, "claim-form")
        _claim(page, live_server, "https://%s" % domain, _founded_months_ago(8))

        # 3. Startup, Free for 90 days, and the end date; it is still there after a reload.
        ends = (datetime.utcnow() + timedelta(days=90)).strftime("%d %b %Y").lstrip("0")
        for _ in range(2):
            expect(page.get_by_test_id("billing-current-plan")).to_have_text("Startup", timeout=PAGE_TIMEOUT)
            expect(page.get_by_text("Free for 90 days", exact=True)).to_be_visible()
            expect(page.get_by_text("Free until", exact=True)).to_be_visible()
            expect(page.get_by_test_id("billing-renewal")).to_have_text(ends)
            expect(page.get_by_text("Buy Startup before then to keep ten people")).to_be_visible()
            expect(page.get_by_text("Buying now starts billing today.")).to_be_visible()
            expect(page.locator("#startup-credit")).to_have_count(0)
            if _ == 0:
                _frame(page, tmp_path, "running")
                page.reload(wait_until="domcontentloaded")

        # 4. A second founder on the same company domain is refused and still sees the buy button.
        context_b, page_b = _sign_up(browser, live_server, bob)
        contexts.append(context_b)
        _give_website(app, bob, "https://eu.%s" % domain)
        _claim(page_b, live_server, "https://eu.%s" % domain, _founded_months_ago(8))
        expect(page_b.get_by_text("This company has already used its 90 days.")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page_b.get_by_test_id("billing-current-plan")).to_have_text("Community")
        _frame(page_b, tmp_path, "refused-same-company")
        page_b.get_by_test_id("plan-card-startup").get_by_role("link", name="Choose Startup").click()
        page_b.wait_for_url(lambda url: "plan=startup" in url, timeout=PAGE_TIMEOUT)
        expect(page_b.locator("#checkout").get_by_role("heading", name="Startup", exact=True)).to_be_visible()

        # 5. A founder on a public mailbox is told to use a work email.
        context_c, page_c = _sign_up(browser, live_server, gmail)
        contexts.append(context_c)
        _give_website(app, gmail, "https://gmail.com")
        _claim(page_c, live_server, "https://gmail.com", _founded_months_ago(8))
        expect(page_c.get_by_text("Use your work email to claim the credit.")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page_c.get_by_test_id("plan-choose-startup")).to_be_visible()

        # 6. The end passes (the state the calendar produces: the rule is read at use).
        from app import db
        from app.models.subscription import Subscription
        from app.models.user import User

        with app.app_context():
            org_id = User.query.filter_by(email=ana).one().organization_id
            row = Subscription.query.filter_by(organization_id=org_id).one()
            ended_on = datetime.utcnow() - timedelta(days=1)
            row.current_period_end = ended_on
            db.session.commit()
            db.session.remove()

        # 7. Community, with the alert; Buy Startup lands on the Startup checkout step.
        page.goto(live_server + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("billing-current-plan")).to_have_text("Community")
        ended = page.get_by_test_id("startup-credit-ended")
        expect(ended).to_contain_text("Your 90 days of Startup ended on %s." % ended_on.strftime("%d %b %Y").lstrip("0"))
        expect(ended).to_contain_text("Buy Startup to add people again.")
        expect(page.get_by_text("Free for 90 days")).to_have_count(0)
        _frame(page, tmp_path, "ended")
        ended.get_by_role("link", name="Buy Startup").click()
        page.wait_for_url(lambda url: "plan=startup" in url, timeout=PAGE_TIMEOUT)
        expect(page.locator("#checkout").get_by_role("heading", name="Startup", exact=True)).to_be_visible()
        expect(page.get_by_test_id("checkout-unavailable")).to_contain_text("Startup")
    finally:
        for context in contexts:
            context.close()
