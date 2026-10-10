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
            ("startup", "$490", "buy-startup"),
            ("team", "$290", "buy-team"),
            ("enterprise", "From $24,000", "buy-enterprise"),
        ]:
            card = page.get_by_test_id(f"plan-card-{key}")
            expect(card).to_be_visible(timeout=PAGE_TIMEOUT)
            expect(card.locator(".pp-price")).to_contain_text(price)
            expect(card.get_by_test_id(button)).to_be_visible()
            # Large numeral (40-48px) and a 44px / 8px button, per the review's spec.
            size = card.locator(".pp-price").evaluate("e => parseFloat(getComputedStyle(e).fontSize)")
            assert 40 <= size <= 48, f"{key} price numeral is {size}px"
            box = card.get_by_test_id(button).evaluate(
                "e => { const s = getComputedStyle(e); return [e.getBoundingClientRect().height, s.borderRadius]; }")
            assert box == [44, "8px"], f"{key} button is {box}"
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
        expect(shown).to_have_text("$490/year")
        expect(startup.get_by_test_id("buy-startup")).to_have_attribute("href", __import__("re").compile("interval%3Dyear"))
        page.get_by_text("Monthly", exact=True).click()
        expect(shown).to_have_count(1)
        expect(shown).to_have_text("$49/month")
        expect(startup.get_by_test_id("buy-startup")).to_be_hidden()
        expect(startup.get_by_test_id("buy-startup-monthly")).to_be_visible()
        expect(startup.get_by_test_id("buy-startup-monthly")).to_have_attribute(
            "href", __import__("re").compile("interval%3Dmonth"))
        team = page.get_by_test_id("plan-card-team")
        expect(team.locator(".pp-price > span:visible")).to_have_text("$29/editor/month")
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


def test_self_hosting_band_faq_and_roadmap(browser, live_server):
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
        # Roadmap dates are quarter-year only.
        text = page.get_by_test_id("pricing-grow").inner_text()
        import re
        assert re.findall(r"Q[1-4] 20\d\d", text) == ["Q4 2026", "Q1 2027", "Q2 2027", "Q3 2027"]
        assert not re.search(r"(January|February|March|April|May|June|July|August|September|October|November|December)", text)
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
        body = page.locator("body").inner_text().lower()
        for banned in ("every canvas", "usage history", "coming soon", "verified", "assumption",
                       "tbc", "export and share", "docs/", ".md"):
            assert banned not in body, f"banned phrase on the page: {banned!r}"
    finally:
        context.close()
