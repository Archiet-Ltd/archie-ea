"""What sign-up, the GitHub review page and the pricing page promise is what the product does.

A new person signs up as the administrator of their own organisation, gives GitHub, and reaches the
AI step. The step says where their own AI key goes: that link must open a page they are allowed to
use. After the read has put applications in the model, the GitHub review page must not say nothing
is in the model, and what it names must be the number Applications lists. The public pricing page
and the billing page must agree with the one payment switch: with payment closed no page offers
Buy, with it open both do.

GitHub is never called: the recorded organisation of the service tests answers, as in the sign-up
journey this reuses. Screenshots go to the folder named by ONBOARD_PROMISES_SHOTS when it is set.
"""

import os
import re
import time
import uuid

import pytest
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD, boot_live_server
from tests.smoke.test_connect_github_to_cited_answer_journey import SKIP_WELCOME, TOKEN
from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401  (fixtures)
    people,
    provider,
    server,
    sign_up,
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("ONBOARD_PROMISES_SHOTS")
PAYMENT_KEYS = {
    "STRIPE_SECRET_KEY": "sk_test_journey_not_a_real_key",
    "STRIPE_WEBHOOK_SECRET": "whsec_journey_not_a_real_secret",
    "STRIPE_PRICE_STARTUP_MONTHLY": "price_journey_startup_month",
    "STRIPE_PRICE_STARTUP_ANNUAL": "price_journey_startup_year",
    "STRIPE_PRICE_TEAM_MONTHLY": "price_journey_team_month",
    "STRIPE_PRICE_TEAM_ANNUAL": "price_journey_team_year",
}
PAYMENT_ENV_NAMES = ("MONELYTICS_BASE_URL", *PAYMENT_KEYS)


def new_page(browser, width=1440, scheme="light"):
    height = 900 if width >= 1000 else 844
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.add_init_script(SKIP_WELCOME)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    return context, context.new_page()


def shoot(page, name):
    """The screen at 1440 wide in light and 390 wide in dark, for the person who reviews it."""
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    before = page.viewport_size
    for width, height, scheme in ((1440, 900, "light"), (390, 844, "dark")):
        page.set_viewport_size({"width": width, "height": height})
        set_theme(page, scheme)
        page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(SHOTS, "%s-%d-%s.png" % (name, width, scheme)), full_page=True)
    page.set_viewport_size(before or {"width": 1440, "height": 900})
    set_theme(page, "light")


def set_theme(page, scheme):
    """Switch the way the app's own theme switch does: the stored choice and the dark class on the page.
    The app ignores a media emulation set after the page has loaded."""
    page.evaluate(
        "scheme => { localStorage.setItem('theme', scheme);"
        " document.documentElement.classList.toggle('dark', scheme === 'dark'); }",
        scheme,
    )
    assert page.evaluate("() => document.documentElement.classList.contains('dark')") == (scheme == "dark")


def assert_not_clipped(page, selector):
    """Every line of the named element is fully visible: nothing inside it is wider than the box showing it."""
    clipped = page.evaluate(
        """selector => [...document.querySelectorAll(selector + ' *, ' + selector)]
            .filter(el => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).display !== 'inline')
            .map(el => el.tagName + ':' + el.scrollWidth + '>' + el.clientWidth)""",
        selector,
    )
    assert not clipped, clipped


@pytest.fixture(scope="module")
def paid_server(request, app, people):
    """A second server with the payment settings present: the switch in its open position."""
    return boot_live_server(request, None, app, extra_env=dict(PAYMENT_KEYS))


def _no_buy_anywhere(page):
    text = page.locator("main").inner_text()
    assert not re.search(r"\bBuy\b", text), text


def _wait_for_the_read(page, base):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        page.goto(base + "/account/welcome", wait_until="domcontentloaded")
        if "items added to your model" in page.get_by_test_id("signup-status-github").inner_text():
            return
        page.wait_for_timeout(3000)
    raise AssertionError("the GitHub read did not finish: " + page.get_by_test_id("signup-status-github").inner_text())


def test_signup_words_lead_to_the_ai_key_page_and_the_review_page_matches_the_model(browser, server, people):
    for name in PAYMENT_ENV_NAMES:
        assert not os.environ.get(name), "the closed server must carry no payment settings: " + name
    base = str(server)
    context, page = new_page(browser)
    try:
        sign_up(page, base, people)
        page.get_by_label("GitHub organisation").fill("acme")
        page.get_by_label("Read-only token").fill(TOKEN)
        page.get_by_role("button", name="Connect GitHub").click()
        expect(page.get_by_test_id("signup-status-github")).to_contain_text(
            re.compile(r"GitHub is connected\. Reading starts in a moment|Reading GitHub now|Read 16 repositories"))
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_role("button", name="Continue").click()
        page.wait_for_url("**/account/welcome?step=4")

        # The step names a place for the organisation's own key. Following the words lands on a page the
        # signed-up administrator may use, and it holds the form that stores the key.
        built_in = page.get_by_test_id("signup-built-in")
        expect(built_in).to_contain_text("AI settings")
        assert "Settings." not in built_in.inner_text().replace("AI settings", "")
        shoot(page, "step4-words")
        page.get_by_test_id("signup-api-settings-link").click()
        page.wait_for_url("**/admin/api-settings?from=signup")
        expect(page.get_by_role("heading", name="AI settings", level=1)).to_be_visible()
        expect(page.locator("main")).not_to_contain_text("does not include this page")
        expect(page.locator("main form").first).to_be_visible()
        assert page.locator("main input, main select").count() > 0
        shoot(page, "api-settings-opened-from-step4")
        # There is a way back to where the person was: the AI step of sign-up, with its words and its Finish.
        page.get_by_test_id("ai-settings-back-to-signup").click()
        page.wait_for_url("**/account/welcome?step=4")
        expect(page.get_by_role("heading", name="Your AI provider")).to_be_visible()
        expect(page.get_by_test_id("signup-finish")).to_be_visible()

        # Payment is closed on this server: the billing page offers no Buy and says what to do instead.
        page.goto(base + "/admin/billing/?plan=startup", wait_until="domcontentloaded")
        checkout = page.locator("#checkout")
        expect(checkout.get_by_role("heading", name="Startup", exact=True)).to_be_visible()
        expect(page.get_by_test_id("checkout-unavailable")).to_contain_text("Payment opens soon")
        expect(page.get_by_test_id("checkout-contact")).to_have_attribute("href", re.compile("contact"))
        expect(page.get_by_test_id("checkout-submit")).to_have_count(0)
        _no_buy_anywhere(page)
        shoot(page, "billing-closed")

        # The sign-up read has put applications in the model. The review page says so, and names the number
        # Applications lists, never "nothing is in your model".
        _wait_for_the_read(page, base)
        page.goto(base + "/dashboard/overview", wait_until="domcontentloaded")
        applications = page.get_by_test_id("welcome-applications")
        expect(applications).to_be_visible()
        total = int(applications.locator("span").first.inner_text())
        assert total >= 3, total
        applications.click()
        expect(page.locator("main")).to_contain_text("%d shown" % total)

        page.goto(base + "/admin/connectors/github/review", wait_until="domcontentloaded")
        main = page.locator("main")
        text = " ".join(main.inner_text().split())
        assert "Nothing is in your model until you approve it" not in text, text
        assert "Nothing is waiting for review." in text, text
        assert "already in your model" in text, text
        assert re.search(r"\b%d applications\b" % total, text), (total, text)
        shoot(page, "review-after-read")
        # The sentence naming what is in the model is fully visible on a phone and a tablet, not cut to "Nothing
        # is waiting for...". Read at 390 and 768 wide.
        for width in (390, 768, 1440):
            page.set_viewport_size({"width": width, "height": 844})
            alert = page.locator("main [role=status]").first
            expect(alert).to_contain_text("already in your model")
            assert_not_clipped(page, "main [role=status]")
        page.set_viewport_size({"width": 1440, "height": 900})
        # The way on from this page lands on content.
        page.get_by_role("link", name="Open the GitHub page").click()
        page.wait_for_url("**/github**")
        expect(page.locator("main")).not_to_be_empty()
    finally:
        context.close()


def test_the_pricing_page_offers_no_buy_while_payment_is_closed(browser, server):
    base = str(server)
    context, page = new_page(browser)
    try:
        page.goto(base + "/pricing", wait_until="domcontentloaded")
        for key, name in (("startup", "Startup"), ("team", "Team")):
            card = page.get_by_test_id("plan-card-" + key)
            expect(card).to_be_visible()
            expect(card.get_by_test_id("buy-" + key)).to_have_text("Start free")
            expect(card.get_by_role("link", name="Talk to us about " + name)).to_be_visible()
        assert not re.search(r"\bBuy\b", page.locator("main").inner_text())
        shoot(page, "pricing-closed")

        # Start free leads to the sign-up form; Talk to us leads to the contact page, which has content.
        page.get_by_test_id("plan-card-startup").get_by_test_id("buy-startup").click()
        page.wait_for_url("**/account/register**")
        expect(page.locator("#email")).to_be_visible()
        page.goto(base + "/pricing", wait_until="domcontentloaded")
        page.get_by_test_id("plan-card-team").get_by_role("link", name="Talk to us about Team").click()
        page.wait_for_url("**/contact**")
        expect(page.locator("main")).to_contain_text(re.compile("contact|sales|talk", re.I))

        # The plan section on a role page reads the same switch: no payment page, no Buy, and the way on lands.
        page.goto(base + "/roles/cto-and-cio", wait_until="domcontentloaded")
        section = page.get_by_test_id("pricing-buy")
        expect(section).to_be_visible()
        text = section.inner_text()
        assert "payment page" not in text and not re.search(r"\bBuy\b", text), text
        expect(section).to_contain_text("Paid plans are set up in a conversation with us")
        section.get_by_test_id("talk-startup").click()
        page.wait_for_url("**/contact**")
        expect(page.locator("main")).to_contain_text(re.compile("contact|sales|talk", re.I))

        # The pricing cards say what Start free does, and the billing questions promise no payment page.
        page.goto(base + "/pricing", wait_until="domcontentloaded")
        expect(page.get_by_test_id("start-free-note-startup")).to_contain_text("Start free puts you on Community")
        # The note sits clear of the card's feature list (no text drawn over text).
        for key in ("startup", "team"):
            card = page.get_by_test_id("plan-card-" + key)
            note = card.get_by_test_id("start-free-note-" + key).bounding_box()
            lead = card.locator(".pp-lead-in").bounding_box()
            assert note["y"] + note["height"] <= lead["y"] + 1, (key, note, lead)
        faq = page.get_by_test_id("pricing-faq").inner_text()
        assert "payment page" not in faq and "Each payment produces an invoice" not in faq, faq

        # The features page does not say plans can be bought and cancelled by the reader.
        page.goto(base + "/features", wait_until="domcontentloaded")
        assert "buy, change and cancel yourself" not in page.locator("main").inner_text()
    finally:
        context.close()


def test_with_payment_open_the_pricing_and_billing_pages_offer_buy(browser, paid_server, people):
    base = str(paid_server)
    context, page = new_page(browser)
    try:
        page.goto(base + "/pricing", wait_until="domcontentloaded")
        startup = page.get_by_test_id("plan-card-startup")
        expect(startup.get_by_test_id("buy-startup")).to_have_text("Buy Startup, billed annually")
        expect(startup.get_by_test_id("buy-startup")).to_have_attribute("href", re.compile("interval%3Dyear"))
        expect(page.get_by_test_id("plan-card-team").get_by_test_id("buy-team")).to_have_text("Buy Team, billed annually")
        expect(startup.get_by_role("link", name="Talk to us about Startup")).to_have_count(0)
        shoot(page, "pricing-open")

        startup.get_by_test_id("buy-startup").click()
        page.wait_for_url(lambda url: "/account/register" in url and "plan=startup" in url)
        sign_up_email = "cto.open.%s@example.com" % uuid.uuid4().hex[:8]
        people.append(sign_up_email)
        page.fill("#first_name", "Casey")
        page.fill("#last_name", "Founder")
        page.fill("#email", sign_up_email)
        page.fill("#password", PASSWORD)
        page.fill("#password2", PASSWORD)
        page.locator("#submit").click()
        page.goto(base + "/admin/billing/?plan=startup", wait_until="domcontentloaded")
        checkout = page.locator("#checkout")
        expect(checkout.get_by_role("heading", name="Buy Startup")).to_be_visible()
        expect(page.get_by_test_id("checkout-unavailable")).to_have_count(0)
        expect(page.get_by_test_id("checkout-submit")).to_be_visible()
        shoot(page, "billing-open")
    finally:
        context.close()
