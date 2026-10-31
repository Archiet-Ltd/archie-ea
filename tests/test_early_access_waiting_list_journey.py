"""A startup CTO reads what Entelim is for, finds what is coming, and asks to hear when it ships.

The person: a visitor on the public site, signed out. The goal: understand the product in one line,
see which capabilities are early access rather than available today, and join the waiting list for
the predictive checks. What they need to see: the promise under the hero, both early-access entries
on the features list marked as such, a waiting-list form with no picture and no plan, and a thank-you.
What the founder needs: the sign-up stored against that entry, so demand is counted per entry.
"""

import pytest
from playwright.sync_api import expect, sync_playwright

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
PROMISE = "Connect your company. Ask in plain language. Every answer cited."
ENTRIES = ("Advisers for every role", "Predictive checks")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


def _sign_ups(app, offer):
    from app.models.product_inquiry import ProductInquiry

    with app.app_context():
        return ProductInquiry.query.filter_by(offer=offer).count()


def _forget(app, email):
    from app import db
    from app.models.product_inquiry import ProductInquiry

    with app.app_context():
        ProductInquiry.query.filter_by(email=email).delete()
        db.session.commit()


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440", "390"])
def test_a_visitor_joins_the_waiting_list_for_the_predictive_checks(browser, site_url, app, viewport):
    from app.services.public_pages import load_page

    offer = load_page("module", slug="predictive-checks").feature_interest_offer
    email = "early-access-%d@example.com" % viewport["width"]
    _forget(app, email)
    before = _sign_ups(app, offer)

    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(site_url + "/")
        expect(page.get_by_test_id("home-hero-promise")).to_have_text(PROMISE)
        expect(page.locator("h1")).to_have_text("See what breaks before you change it.")

        # the home page names both as coming soon, inside the page's own Coming soon
        # block, whose caption now carries that meaning once (T-site-14b): a link's own
        # text is the capability name, marked by the block and by data-availability,
        # not by repeating "(coming soon)" in every link's own text.
        coming_soon_block = page.get_by_test_id("home-coming-soon")
        expect(coming_soon_block.get_by_text("Coming soon", exact=True)).to_be_visible()
        for entry in ENTRIES:
            link = coming_soon_block.get_by_role("link", name=entry, exact=True)
            expect(link).to_be_visible()
            assert link.get_attribute("data-availability") == "early_access", entry
        page.goto(site_url + "/features#coming-soon")
        section = page.locator("article")
        for entry in ENTRIES:
            item = section.locator("li").filter(has=page.get_by_role("link", name=entry, exact=True))
            expect(item).to_contain_text("early access")

        section.get_by_role("link", name="Predictive checks", exact=True).click()
        expect(page).to_have_url(site_url + "/modules/predictive-checks")
        main = page.locator("main")
        expect(main).to_contain_text("Early access")
        expect(main).to_contain_text("one person who owns too much")
        # an early-access page shows no picture of the feature and offers no plan
        own = page.evaluate("""() => {
            const inside = (selector) => Array.from(document.querySelectorAll('main ' + selector)).filter(e => !e.closest('footer'));
            return {
                media: inside('img, picture, video, figure').length,
                plans: inside('a').filter(a => /^\\/pricing/.test(a.getAttribute('href')) || /^\\/account\\/register/.test(a.getAttribute('href'))).length,
            };
        }""")
        assert own == {"media": 0, "plans": 0}, own

        form = page.get_by_test_id("feature-interest-form")
        form.get_by_label("Email").fill(email)
        form.get_by_label("Used to tell you when this becomes available, and for nothing else.").check()
        form.get_by_test_id("feature-interest-submit").click()
        expect(page.get_by_test_id("feature-interest-thanks")).to_be_visible()

        # the sign-up is stored against this entry, and the count for it rose by one
        assert _sign_ups(app, offer) == before + 1
        from app.models.product_inquiry import ProductInquiry

        with app.app_context():
            assert ProductInquiry.query.filter_by(email=email).one().offer == offer
        assert errors == [], errors
    finally:
        context.close()
        _forget(app, email)


def test_neither_early_access_entry_is_on_the_pricing_page(browser, site_url):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        page.goto(site_url + "/pricing")
        # the page's own content: the footer's Coming soon column sits inside <main> on every page
        text = page.evaluate("""() => {
            const main = document.querySelector('main').cloneNode(true);
            main.querySelectorAll('footer, nav, header').forEach(e => e.remove());
            return main.textContent;
        }""").lower()
        assert "plan" in text or "month" in text, text[:300]
        for word in ("adviser", "predictive"):
            assert word not in text, word
    finally:
        context.close()
