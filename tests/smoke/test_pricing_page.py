"""The plan-card pricing page, driven in a real browser.

Each plan's price and its buy button sit inside one card; the monthly/annual
toggle changes the prices and the live buy button; the comparison table's plan
headers stay in view while the page scrolls; the self-hosting band links to the
repository; the billing questions open. Anonymous throughout.
"""
import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _open(browser, live_server, width=1440, height=900):
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    page.goto(live_server + "/pricing", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return context, page


def test_each_plan_card_holds_its_price_and_its_buy_button(browser, live_server):
    context, page = _open(browser, live_server)
    try:
        for key, price, button in [
            ("community", "Free", "buy-community"),
            ("startup", "USD 490", "buy-startup"),
            ("team", "USD 290", "buy-team"),
            ("enterprise", "From USD 24,000", "buy-enterprise"),
        ]:
            card = page.get_by_test_id(f"plan-card-{key}")
            expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
            expect(card.locator(".pp-price")).to_contain_text(price)
            expect(card.get_by_test_id(button)).to_be_visible()
            # Large numeral (40-48px) and the shared public button (at least 40px tall; it wraps, so it may grow; radius + 4px).
            size = card.locator(".pp-price").evaluate("e => parseFloat(getComputedStyle(e).fontSize)")
            assert 40 <= size <= 48, f"{key} price numeral is {size}px"
            box = card.get_by_test_id(button).evaluate(
                "e => { const s = getComputedStyle(e); return [e.getBoundingClientRect().height, s.borderRadius]; }")
            assert box[0] >= 40 and box[1] == "14px", f"{key} button is {box}"
        # Four across at desktop width.
        tops = {page.get_by_test_id(f"plan-card-{k}").bounding_box()["y"]
                for k in ("community", "startup", "team", "enterprise")}
        assert len(tops) == 1
    finally:
        context.close()


def test_toggle_switches_prices_and_the_live_buy_link(browser, live_server):
    context, page = _open(browser, live_server)
    try:
        startup = page.get_by_test_id("plan-card-startup")
        shown = startup.locator(".pp-price > span:visible")
        expect(shown).to_have_count(1)
        expect(shown).to_have_text("USD 490 a year")
        expect(startup.get_by_test_id("buy-startup")).to_have_attribute("href", __import__("re").compile("interval%3Dyear"))
        page.get_by_text("Monthly", exact=True).click()
        expect(shown).to_have_count(1)
        expect(shown).to_have_text("USD 49 a month")
        expect(startup.get_by_test_id("buy-startup")).to_be_hidden()
        expect(startup.get_by_test_id("buy-startup-monthly")).to_be_visible()
        expect(startup.get_by_test_id("buy-startup-monthly")).to_have_attribute(
            "href", __import__("re").compile("interval%3Dmonth"))
        team = page.get_by_test_id("plan-card-team")
        expect(team.locator(".pp-price > span:visible")).to_have_text("USD 29 per editor a month")
        # The visible buy button still leads to that plan's registration.
        startup.get_by_test_id("buy-startup-monthly").click()
        page.wait_for_url(lambda url: "/account/register" in url and "plan=startup" in url
                          and "interval=month" in url, timeout=PAGE_TIMEOUT)
    finally:
        context.close()


def test_comparison_table_headers_stay_in_view_while_scrolling(browser, live_server):
    context, page = _open(browser, live_server)
    try:
        table = page.get_by_test_id("pricing-compare")
        expect(table).to_be_visible(timeout=PAGE_TIMEOUT)
        header = table.locator("thead th", has_text="Team")
        # Scroll until the table's last row is on screen: the header must still be visible.
        last_row = table.locator("tbody tr").last
        last_row.scroll_into_view_if_needed()
        page.wait_for_timeout(300)
        box = header.bounding_box()
        assert box is not None and 0 <= box["y"] < 400, f"sticky header not pinned: {box}"
        expect(header).to_be_in_viewport()
        groups = [t.strip().lower() for t in table.locator(".pp-group").all_inner_texts()]
        assert groups == ["modelling", "questions and answers", "governance",
                          "security and access", "support"]
    finally:
        context.close()


def test_self_hosting_band_and_faq_with_no_roadmap_band(browser, live_server):
    context, page = _open(browser, live_server)
    try:
        band = page.get_by_test_id("pricing-selfhost")
        expect(band).to_contain_text("Self-hosting is always free")
        expect(page.get_by_test_id("selfhost-github")).to_have_attribute(
            "href", "https://github.com/Archiet-Ltd/archie-ea")
        faq = page.get_by_test_id("pricing-faq")
        questions = faq.locator("summary")
        assert 6 <= questions.count() <= 8
        questions.first.click()
        expect(faq.locator("details").first.locator("p")).to_be_visible()
        # No coming item is offered inside a plan: the page carries no dated roadmap band.
        assert page.get_by_test_id("pricing-grow").count() == 0
        assert "gains as the platform grows" not in page.locator("main").inner_text()
    finally:
        context.close()


def test_pricing_reads_back_clean_and_stacks_on_mobile(browser, live_server):
    context, page = _open(browser, live_server, 390, 844)
    try:
        expect(page.get_by_test_id("plan-card-enterprise")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        tops = [page.get_by_test_id(f"plan-card-{k}").bounding_box()["y"]
                for k in ("community", "startup", "team", "enterprise")]
        assert tops == sorted(tops) and len(set(tops)) == 4, "cards do not stack"
        main = page.locator("main")
        body = main.inner_text().lower()
        for banned in ("every canvas", "usage history", "verified", "assumption",
                       "tbc", "export and share", "docs/", ".md"):
            assert banned not in body, f"banned phrase on the page: {banned!r}"
    finally:
        context.close()


def test_heading_and_intro_share_left_edge_at_1440(browser, live_server):
    """The Pricing heading and its intro paragraph share one left edge at 1440px."""
    context, page = _open(browser, live_server, 1440, 900)
    try:
        h1 = page.locator("h1")
        p = page.locator(".pp-lead p").first
        expect(h1).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(p).to_be_visible()
        h1_box = h1.bounding_box()
        p_box = p.bounding_box()
        assert h1_box is not None and p_box is not None
        assert abs(h1_box["x"] - p_box["x"]) <= 1, (
            f"heading left edge {h1_box['x']} != paragraph left edge {p_box['x']} at 1440px"
        )
    finally:
        context.close()


def test_heading_and_intro_share_left_edge_at_390(browser, live_server):
    """The Pricing heading and its intro paragraph share one left edge at 390px."""
    context, page = _open(browser, live_server, 390, 844)
    try:
        h1 = page.locator("h1")
        p = page.locator(".pp-lead p").first
        expect(h1).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(p).to_be_visible()
        h1_box = h1.bounding_box()
        p_box = p.bounding_box()
        assert h1_box is not None and p_box is not None
        assert abs(h1_box["x"] - p_box["x"]) <= 1, (
            f"heading left edge {h1_box['x']} != paragraph left edge {p_box['x']} at 390px"
        )
    finally:
        context.close()


@pytest.mark.parametrize("width", [1024, 1100, 1280])
def test_four_across_prices_fit_their_cards_and_the_buttons_share_a_row(browser, live_server, width):
    context, page = _open(browser, live_server, width=width)
    try:
        expect(page.get_by_test_id("plan-card-enterprise")).to_be_visible(timeout=PAGE_TIMEOUT)
        tops, rights = [], []
        for key in ("community", "startup", "team", "enterprise"):
            card = page.get_by_test_id(f"plan-card-{key}")
            price = card.locator(".pp-price")
            fits = price.evaluate("e => e.scrollWidth <= e.clientWidth")
            assert fits, f"{key} price overflows its box at {width}px"
            # The text itself, measured with a range, stays inside the card's padding box.
            inside = card.evaluate("""c => {
                const p = c.querySelector('.pp-price'), cs = getComputedStyle(c);
                const r = document.createRange(); r.selectNodeContents(p);
                const limit = c.getBoundingClientRect().right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth);
                return Math.max(...[...r.getClientRects()].map(x => x.right)) <= limit + 0.5;
            }""")
            assert inside, f"{key} price text runs past the card padding at {width}px"
            box = card.get_by_test_id(f"buy-{key}").bounding_box()
            tops.append(round(box["y"]))
            assert 40 <= box["height"] <= 80
            rights.append(round(card.bounding_box()["x"] + card.bounding_box()["width"]))
        assert max(rights) <= width, f"a plan card runs past the viewport at {width}px: {rights}"
        assert max(tops) - min(tops) <= 1, f"plan buttons sit at different heights at {width}px: {tops}"
    finally:
        context.close()
