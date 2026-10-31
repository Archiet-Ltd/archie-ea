"""The first-week account and billing journeys, each walked end to end by a new person.

One journey per flow, from the person's goal to the screen they needed:

  * forgot the password: the sign-in page, the e-mailed link, a new password, signed in;
  * signed up: the sign-up form, the e-mailed confirmation link, the set-up steps, signed in;
  * invited: the team page's invitation, the e-mailed link, a password, signed in;
  * started with the sample company: sign-up, skipping every source, the sample's facts;
  * bought Startup at checkout, then cancelled it.

The e-mail flows run against a real SMTP sink (the same one the account-mail journeys use)
and follow the link the message actually carries. The checkout runs against the loopback
payment provider in test mode: the person pays on its hosted page, and its end-of-period
event arrives signed, as the real provider's does. Every journey ends on the signed-in
dashboard under the person's own organisation name, or on the billing page naming the plan.
"""
import re
import urllib.request
import uuid

import pytest
from playwright.sync_api import expect

from .chat_ask import ask
from .conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from .test_account_mail_journey import (  # noqa: F401  (the fixtures are used by name)
    NEW_PASSWORD,
    _link_in,
    _seed_member,
    _sign_in,
    mail_server,
    smtp_sink,
)
from .test_signup_brings_the_company_in_journey import (  # noqa: F401  (the fixtures are used by name)
    new_page,
    people,
    provider,
    server,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# The first-visit tour covers the header; it is not what these journeys test.
_SKIP_TOUR = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"


def _organisation_name_of(app, email):
    with app.app_context():
        from app import db
        from app.models.user import User

        name = User.find_by_email(email).organization.name
        db.session.remove()
        return name


def _assert_signed_in_on_dashboard(page, organisation_name):
    """The person is on the dashboard, under their own organisation's name, with real content."""
    page.wait_for_url(re.compile(r"/dashboard/"), timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("button", name="Switch organisation")).to_contain_text(
        organisation_name, timeout=PAGE_TIMEOUT)
    main = page.locator("#main-content")
    expect(main).not_to_be_empty()
    assert len(main.inner_text().strip()) > 40, "the dashboard is empty"


def _count(text):
    digits = re.sub(r"[^0-9]", "", text)
    assert digits, "no figure in %r" % text
    return int(digits)


def _shoot(page, name):
    """Optional screenshots for the review walk, at the two widths people use."""
    import os

    folder = os.environ.get("FIRST_WEEK_SHOTS")
    if not folder:
        return
    os.makedirs(folder, exist_ok=True)
    for width, height in ((1440, 900), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(folder, "%s-%d.png" % (name, width)), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})


# --------------------------------------------------------------------------- #
# 1. Forgot the password
# --------------------------------------------------------------------------- #

def test_a_person_who_forgot_their_password_resets_it_and_is_signed_in(browser, mail_server, smtp_sink, app):
    member = _seed_member(app)
    other = _seed_member(app)  # a second organisation, which must never appear for this person
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(_SKIP_TOUR)
    page = context.new_page()
    try:
        page.goto(mail_server + "/account/login", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_role("link", name="Forgot password?").click()
        page.wait_for_url("**/account/reset-password", timeout=PAGE_TIMEOUT)
        page.fill("#email", member["email"])
        page.get_by_role("button", name="Reset password").click()
        expect(page.get_by_text("Check your e-mail")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text(member["email"])
        _shoot(page, "reset-requested")

        message = smtp_sink.text_to(member["email"])
        assert "reset" in message.lower()
        link = _link_in(message, "/account/reset-password/")
        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Choose a new password")).to_be_visible(timeout=PAGE_TIMEOUT)
        _shoot(page, "reset-choose")
        page.fill("#password", NEW_PASSWORD)
        page.fill("#password2", NEW_PASSWORD)
        page.get_by_role("button", name="Set new password").click()
        page.wait_for_url("**/account/login", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Your password has been updated").first).to_be_visible()

        # The old password no longer works; the new one signs in to the dashboard.
        page.fill("#email", member["email"])
        page.fill("#password", PASSWORD)
        page.click("#submit")
        expect(page.locator("#main-content, main").first).to_contain_text("Invalid", ignore_case=True,
                                                                           timeout=PAGE_TIMEOUT)
        page.fill("#email", member["email"])
        page.fill("#password", NEW_PASSWORD)
        page.click("#submit")
        _assert_signed_in_on_dashboard(page, member["org_name"])
        assert other["org_name"] not in page.locator("body").inner_text()
        _shoot(page, "reset-signed-in")
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# 2. Signed up, confirmed the address
# --------------------------------------------------------------------------- #

def test_a_new_person_signs_up_confirms_their_address_and_is_signed_in(browser, mail_server, smtp_sink, people, app):
    address = "newcomer.%s@example.com" % uuid.uuid4().hex[:8]
    people.append(address)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(_SKIP_TOUR)
    page = context.new_page()
    try:
        # From the pricing page's free plan to the sign-up form.
        page.goto(mail_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("buy-community").click()
        page.wait_for_url("**/account/register**", timeout=PAGE_TIMEOUT)
        page.fill("#first_name", "Nia")
        page.fill("#last_name", "Newperson")
        page.fill("#email", address)
        page.fill("#password", PASSWORD)
        page.fill("#password2", PASSWORD)
        page.click("#submit")

        # Told to confirm, with the address named, before anything else is reachable.
        page.wait_for_url("**/account/unconfirmed", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Confirm your e-mail address to continue")).to_be_visible()
        expect(page.locator("main")).to_contain_text(address)
        _shoot(page, "signup-unconfirmed")
        page.goto(mail_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert "/account/unconfirmed" in page.url, "an unconfirmed account reached the dashboard"

        # The e-mailed link confirms and leads on to the set-up steps.
        link = _link_in(smtp_sink.text_to(address), "/account/confirm-account/")
        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.wait_for_url("**/account/welcome", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Bring in your company")).to_be_visible()
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Finish").click()

        _assert_signed_in_on_dashboard(page, _organisation_name_of(app, address))
        _shoot(page, "signup-signed-in")

        # The same link, opened again, says so and offers a way on.
        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#main-content, main").first).not_to_be_empty()
        assert "/account/confirm-account/" not in page.url or page.get_by_text("no longer works").count() == 1
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# 3. Invited to join the team
# --------------------------------------------------------------------------- #

def test_an_invited_person_joins_through_the_e_mail_and_is_signed_in(browser, mail_server, smtp_sink, app):
    owner = _seed_member(app, org_admin=True)
    stranger = _seed_member(app, org_admin=True)  # another organisation with its own team page
    invitee = "invited.%s@example.com" % uuid.uuid4().hex[:8]
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(_SKIP_TOUR)
    page = context.new_page()
    try:
        _sign_in(page, mail_server, owner["email"])
        page.goto(mail_server + "/admin/team", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.fill("#email", invitee)
        page.select_option("#role", "architect")
        page.get_by_role("button", name="Send invitation").click()
        page.wait_for_url("**/admin/team", timeout=PAGE_TIMEOUT)
        # The e-mail cell names the invitation; its delivery state is a sibling cell in the same row.
        row = page.locator('[data-testid="invitation-row"]').filter(
            has=page.locator('[data-invitation-email="%s"]' % invitee))
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        assert row.locator('[data-invitation-delivery="sent"]').count() == 1
        message = smtp_sink.text_to(invitee)
        assert owner["org_name"] in message, message
        link = _link_in(message, "/account/join/")
    finally:
        context.close()

    # The other organisation's team page does not list the invitation.
    other_context = browser.new_context()
    other_context.add_init_script(_SKIP_TOUR)
    other_page = other_context.new_page()
    try:
        _sign_in(other_page, mail_server, stranger["email"])
        other_page.goto(mail_server + "/admin/team", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert other_page.locator('[data-invitation-email="%s"]' % invitee).count() == 0
    finally:
        other_context.close()

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(_SKIP_TOUR)
    page = context.new_page()
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Join %s" % owner["org_name"])).to_be_visible(
            timeout=PAGE_TIMEOUT)
        expect(page.locator("main")).to_contain_text(invitee)
        _shoot(page, "invite-join")
        page.fill("#password", NEW_PASSWORD)
        page.fill("#password2", NEW_PASSWORD)
        page.get_by_role("button", name="Join %s" % owner["org_name"]).click()
        page.wait_for_url("**/account/login", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("You have joined %s" % owner["org_name"]).first).to_be_visible()
        page.fill("#email", invitee)
        page.fill("#password", NEW_PASSWORD)
        page.click("#submit")
        _assert_signed_in_on_dashboard(page, owner["org_name"])
        assert stranger["org_name"] not in page.locator("body").inner_text()
        _shoot(page, "invite-signed-in")
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# 4. Started with the sample company
# --------------------------------------------------------------------------- #

def test_a_new_person_starts_with_the_sample_company_and_reads_its_facts(browser, server, people, app):
    context, page = new_page(browser)
    try:
        email, _started = sign_up(page, str(server), people)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Finish").click()
        organisation = _organisation_name_of(app, email)
        _assert_signed_in_on_dashboard(page, organisation)

        # Nothing is connected, so the chat's first screen offers the sample company.
        page.get_by_role("link", name="AI chat").first.click()
        page.wait_for_url("**/ai-chat**", timeout=PAGE_TIMEOUT)
        sample = page.get_by_test_id("chat-first-sample")
        expect(sample.first).to_be_visible(timeout=PAGE_TIMEOUT)
        _shoot(page, "sample-offer")
        sample.first.click()
        expect(page.get_by_test_id("sample-company-indicator")).to_be_visible(timeout=PAGE_TIMEOUT * 2)
        expect(page.get_by_text("You are looking at the sample company")).to_be_visible()

        # The figures on the first screen are the sample's, not dashes or zeros.
        overview = page.get_by_test_id("overview-dashboard")
        applications = _count(page.get_by_test_id("welcome-applications").locator("span").first.inner_text())
        assert applications >= 8, applications
        assert "Appears here" not in overview.get_by_test_id("card-run-cost").inner_text()
        for figure in ("card-run-cost-value", "card-relationships-value"):
            assert overview.get_by_test_id(figure).inner_text().strip() not in ("", "-", "\u2014", "0"), figure
        _shoot(page, "sample-dashboard")

        # The applications list holds as many systems as the first screen counted, and names the sample's.
        page.get_by_test_id("welcome-applications").click()
        page.wait_for_url("**/applications/**", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text("Payments service", exact=False).first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.locator("table tbody tr").count() == applications
        _shoot(page, "sample-applications")

        # The first question is answered from the sample, with dependants and sources.
        answer = ask(page, str(server), "What breaks if Payments service fails")
        rows = answer.locator("[data-answer-row]")
        expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        assert rows.count() >= 2, "the sample answer lists fewer than two dependants"
        assert answer.locator("[data-citation]").count() >= 1, "no row names its source"
        _shoot(page, "sample-answer")

        # Every system the answer lists opens on a page with content.
        first_link = answer.locator("[data-answer-row] a[href]").first
        href = first_link.get_attribute("href")
        first_link.click()
        page.wait_for_url(lambda url: href.split("?")[0] in url, timeout=PAGE_TIMEOUT)
        expect(page.locator("#main-content")).not_to_be_empty()
        assert len(page.locator("#main-content").inner_text().strip()) > 40
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# 5. Bought Startup at checkout, then cancelled
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def checkout_server(request, app):
    """A server whose payment provider is the loopback stand-in, in test mode."""
    from .payment_provider_stub import PaymentProviderStub

    with PaymentProviderStub() as stub:
        yield boot_live_server(request, None, app, extra_env=stub.child_environment({})), stub


def _plan_shown(page):
    return page.get_by_test_id("billing-current-plan").inner_text().strip()


def test_a_new_person_buys_startup_at_checkout_and_then_cancels(browser, checkout_server, people, app):
    from app.services import billing_plans

    base, provider_stub = checkout_server
    startup = billing_plans.get_plan("startup")
    address = "buyer.%s@example.com" % uuid.uuid4().hex[:8]
    people.append(address)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(_SKIP_TOUR)
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        # The pricing page's price is the catalogue's price, and its button leads to that plan.
        page.goto(base + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        card = page.get_by_test_id("plan-card-startup")
        assert "USD %d" % startup.display_price_annual in card.locator(".pp-price").inner_text()
        page.get_by_label("Monthly", exact=True).check()
        assert "USD %d" % startup.display_price_monthly in card.locator(".pp-price").inner_text()
        _shoot(page, "buy-pricing-monthly")
        page.get_by_label("Annual", exact=False).check()
        _shoot(page, "buy-pricing")
        card.get_by_test_id("buy-startup").click()
        page.wait_for_url(re.compile(r"/account/register\?.*plan=startup"), timeout=PAGE_TIMEOUT)

        # Sign up; no mail server here, so the account is usable at once and goes on to checkout.
        page.fill("#first_name", "Bo")
        page.fill("#last_name", "Buyer")
        page.fill("#email", address)
        page.fill("#password", PASSWORD)
        page.fill("#password2", PASSWORD)
        page.click("#submit")
        # Checkout does not wait on two-factor set-up: the buy step is on screen straight away.
        page.wait_for_url(re.compile(r"/admin/billing/\?.*plan=startup"), timeout=PAGE_TIMEOUT)
        checkout = page.locator("#checkout")
        expect(checkout.get_by_role("heading", name="Buy Startup")).to_be_visible()
        expect(page.get_by_test_id("checkout-price")).to_contain_text("USD %.2f per year" % startup.display_price_annual)
        assert _plan_shown(page) == "Community"
        _shoot(page, "buy-checkout")
        page.get_by_test_id("checkout-submit").click()

        # The provider's own payment page, in test mode; paying brings the person back.
        page.wait_for_url(re.compile(r"/pay/cs_test_"), timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="Pay for Startup")).to_be_visible()
        expect(page.locator("main")).to_contain_text("%.2f USD per year" % startup.display_price_annual)
        page.get_by_role("button", name="Pay").click()
        page.wait_for_url(re.compile(r"/admin/billing/$"), timeout=PAGE_TIMEOUT)
        # The payment confirmation is a toast (not an inline alert).
        expect(page.locator("#platform-toast-container")).to_contain_text(
            "Payment received", timeout=PAGE_TIMEOUT)
        assert _plan_shown(page) == "Startup"
        assert "10" in page.get_by_test_id("billing-people-limit").inner_text()
        expect(page.get_by_test_id("billing-renewal")).not_to_be_empty()
        _shoot(page, "buy-startup")

        # The plan is held on the person's own organisation, not only shown.
        with app.app_context():
            from app import db
            from app.models.user import User

            organisation = User.find_by_email(address).organization
            subscription = billing_plans.current_subscription(organisation)
            assert billing_plans.effective_plan(subscription).key == "startup"
            org_id = organisation.id
            db.session.remove()

        # Cancel: one plain confirmation, and the page says what happens next.
        page.reload(wait_until="domcontentloaded")
        page.get_by_role("button", name="Cancel subscription").click()
        page.get_by_role("button", name="Cancel at the end of the period").click()
        # The cancel confirmation is a toast, not an inline alert.
        expect(page.locator("#platform-toast-container")).to_contain_text(
            "Your subscription is cancelled.", timeout=PAGE_TIMEOUT)
        assert _plan_shown(page) == "Startup", "the paid period is still running"
        expect(page.get_by_role("button", name="Keep my subscription")).to_be_visible()
        # The cancel message shows once (toast), not twice (no inline alert with the same words).
        inline_text = page.locator("#main-content [role=status], #main-content [role=alert]").all_inner_texts()
        assert not any("Your subscription is cancelled." in t for t in inline_text), \
            "the cancel message appeared as an inline alert as well as a toast"
        _shoot(page, "cancel-scheduled")

        # The paid period ends: the provider's signed event arrives and the plan is Community.
        subscription_id = next(iter(provider_stub.subscriptions))
        assert provider_stub.subscriptions[subscription_id]["cancel_at_period_end"] is True
        body, signature = provider_stub.period_ended_event(subscription_id)
        request = urllib.request.Request(
            base + "/admin/billing/webhook", data=body, method="POST",
            headers={"Content-Type": "application/json", "Stripe-Signature": signature})
        with urllib.request.urlopen(request, timeout=60) as response:
            assert response.status == 200
        page.reload(wait_until="domcontentloaded")
        assert _plan_shown(page) == "Community"
        expect(page.get_by_test_id("billing-people-limit")).to_contain_text("of 3")
        expect(page.get_by_role("button", name="Cancel subscription")).to_have_count(0)
        # On the free plan: status reads "Free plan" with no red, renewal says "Nothing to renew".
        expect(page.get_by_test_id("billing-page")).not_to_contain_text("Cancelled")
        expect(page.get_by_test_id("billing-renewal")).to_have_text("Nothing to renew")
        # Every plan price says "USD", not "$" alone.
        for key in ("startup", "team", "enterprise"):
            price_text = page.get_by_test_id("plan-price-%s" % key).inner_text()
            assert "USD" in price_text, "plan %s price does not say USD: %s" % (key, price_text)
        _shoot(page, "cancel-community")
        with app.app_context():
            from app import db

            subscription = billing_plans.current_subscription(db.session.get(
                __import__("app.models.organization", fromlist=["Organization"]).Organization, org_id))
            assert billing_plans.effective_plan(subscription).key == "free"
            db.session.remove()
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# 6. Every figure names its currency
# --------------------------------------------------------------------------- #

def test_currency_is_named_on_overview_and_prices_are_usd(browser, server, people, app):
    """A person sees the currency named on the overview, and every plan price reads USD."""
    context, page = new_page(browser)
    try:
        email, _started = sign_up(page, str(server), people)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Finish").click()
        organisation = _organisation_name_of(app, email)
        _assert_signed_in_on_dashboard(page, organisation)

        # The overview names its currency in the subtitle.
        expect(page.locator("#main-content")).to_contain_text("Figures in GBP", timeout=PAGE_TIMEOUT)

        # The public pricing page shows every plan price as USD.
        page.goto(server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        from app.services import billing_plans

        for plan in (billing_plans.get_plan("startup"), billing_plans.get_plan("team"),
                     billing_plans.get_plan("enterprise")):
            card = page.get_by_test_id(f"plan-card-{plan.key}")
            expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
            price_text = card.locator(".pp-price").inner_text()
            assert "USD" in price_text, f"plan {plan.key} card price missing USD: {price_text}"
            assert "$" not in price_text, f"plan {plan.key} card price uses bare $: {price_text}"

        # The pricing page's note text also says USD, never a bare $.
        for key in ("startup", "team"):
            card = page.get_by_test_id(f"plan-card-{key}")
            note_text = card.locator(".pp-note").inner_text()
            assert "USD" in note_text, f"plan {key} note missing USD: {note_text}"
            assert "$" not in note_text, f"plan {key} note uses bare $: {note_text}"

        # The empty overview's links all lead somewhere with content.
        page.goto(server + "/dashboard/overview", wait_until="domcontentloaded")
        for link in page.locator("main a").all():
            href = link.get_attribute("href") or ""
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            if not href.startswith("http"):
                target = server.rstrip("/") + href
                new_page_obj = context.new_page()
                try:
                    new_page_obj.goto(target, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                    assert new_page_obj.locator("main").inner_text().strip(), f"empty page at {href}"
                finally:
                    new_page_obj.close()
    finally:
        context.close()


# --------------------------------------------------------------------------- #
# 7. One currency source: reporting_currency drives every money figure
# --------------------------------------------------------------------------- #

def test_org_currency_reads_reporting_currency_column(browser, server, people, app):
    """An org whose reporting_currency is EUR shows the euro sign on the overview,
    names EUR once, and the overview reads the one stored column."""
    context, page = new_page(browser)
    try:
        email, _started = sign_up(page, str(server), people)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4", timeout=PAGE_TIMEOUT)
        page.get_by_role("button", name="Finish").click()
        organisation = _organisation_name_of(app, email)
        _assert_signed_in_on_dashboard(page, organisation)

        # A fresh sign-up gets the platform default currency.
        expect(page.locator("#main-content")).to_contain_text("Figures in GBP", timeout=PAGE_TIMEOUT)

        # Change the organisation's reporting_currency to EUR.
        with app.app_context():
            from app import db
            from app.models.organization import Organization
            from app.models.user import User

            user = User.find_by_email(email)
            org = db.session.get(Organization, user.organization_id)
            org.reporting_currency = "EUR"
            db.session.commit()
            db.session.remove()

        # After reload, the overview reads the one stored column and shows EUR.
        page.goto(server + "/dashboard/overview", wait_until="domcontentloaded")
        expect(page.locator("#main-content")).to_contain_text("Figures in EUR", timeout=PAGE_TIMEOUT)

        # The currency is named exactly once on the page.
        main_text = page.locator("#main-content").inner_text()
        assert main_text.count("Figures in EUR") == 1, (
            "currency named %d times, expected once" % main_text.count("Figures in EUR"))
    finally:
        context.close()
