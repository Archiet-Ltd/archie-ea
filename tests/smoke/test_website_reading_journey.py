"""A founder gives their website at sign-up and can see it being read, confirm it, and ask the chat about their company.

The person: a startup CTO who has never seen the product. The goal: give the company website, see how far
the reading has got without staying on one page, be taken to the facts to confirm when they are ready (from
the welcome screens, or from the bell once they have moved on), confirm them, and then ask the chat what the
product learned about the company and open each page that answer cites. A site that cannot be read says why
and offers a way forward, and every offer lands on a page with content. Run at 1440 and 390 wide.

The company's website is a small local server, slowed so the reading takes a few seconds to watch. The
reading model is the local provider the sign-up journeys use. Set WEBSITE_JOURNEY_SHOTS to save screenshots.
"""
import os
import re

import pytest
from playwright.sync_api import expect

from tests.smoke.website_wait import wait_for_the_model_pass
from tests.smoke.chat_ask import ask
from tests.smoke.conftest import PAGE_TIMEOUT
from tests.smoke.test_signup_brings_the_company_in_journey import (  # noqa: F401  (these are fixtures)
    assert_is_a_startup_cto_not_an_administrator,
    new_page,
    people,
    provider,
    server,
    sign_up,
)
from tests.test_website_crawl import ABOUT, APP_SHELL, CONTACT, HOME, PRODUCTS, ROBOTS, _html, _Site

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SHOTS = os.environ.get("WEBSITE_JOURNEY_SHOTS")
WIDTHS = [1440, 390]
READING = re.compile(r"Read \d+ of up to 30 pages\. (Reading now: 127\.0\.0\.1:\d+(/\S*)?\.|Working out what they say)|Opening 127\.0\.0\.1:\d+")
READY = re.compile(r"Read (\d+) pages; (\d+) facts wait for you to confirm in the next step\.")


def shoot(page, name, width):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    page.screenshot(path=os.path.join(SHOTS, "%s-%d.png" % (name, width)), full_page=True)


@pytest.fixture(scope="module")
def slow_site():
    """The fixture company's own website, one page at a time and slowly, so its reading can be watched."""
    other = _Site({"/elsewhere": _html("<p>another company</p>")})
    pages = {
        "/": _html(HOME % {"linkedin": "https://www.linkedin.com/company/acme-robotics", "other": other.url}),
        "/about": _html(ABOUT), "/products": _html(PRODUCTS), "/contact": _html(CONTACT), "/app": _html(APP_SHELL),
        "/robots.txt": (200, "text/plain", ROBOTS),
    }
    site = _Site(pages, delay=1.5)
    yield site
    site.stop()
    other.stop()


@pytest.fixture(scope="module")
def closed_site():
    """A company website whose robots file asks every reader to stay out."""
    site = _Site({"/": _html("<html><body><h1>Closed Co</h1></body></html>"),
                  "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /\n")})
    yield site
    site.stop()


def _give_the_website(page, url):
    page.get_by_label("Website address").fill(url)
    page.get_by_label("You may read this website").check()
    page.get_by_role("button", name="Save website").click()


def _landing_has_content(context, href, must_contain=None):
    landing = context.new_page()
    try:
        response = landing.goto(href, wait_until="domcontentloaded")
        assert response is not None and response.ok, (href, response and response.status)
        text = landing.locator("body").inner_text().strip()
        assert text, href
        if must_contain:
            assert must_contain in text, (href, must_contain)
    finally:
        landing.close()


@pytest.mark.parametrize("width", WIDTHS, ids=[str(w) for w in WIDTHS])
def test_a_founder_watches_the_reading_confirms_what_it_found_and_asks_the_chat_about_their_company(
        browser, server, people, app, slow_site, width):
    context, page = new_page(browser, width)
    page.set_viewport_size({"width": width, "height": 900 if width > 500 else 844})
    try:
        email, _started = sign_up(page, str(server), people)
        assert_is_a_startup_cto_not_an_administrator(app, email)
        _give_the_website(page, slow_site.url)

        # Within moments the sources step says how far the reading has got: pages read of the limit, and the page being read.
        status = page.get_by_test_id("signup-status-website")
        expect(status).to_contain_text(READING, timeout=PAGE_TIMEOUT)
        expect(page.locator("[data-reading-bar] [role='progressbar']")).to_be_visible()
        shoot(page, "sources-reading", width)

        # The person carries on; the next step still says where the reading is.
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        expect(page.get_by_test_id("signup-status-website")).to_contain_text(re.compile(r"of up to 30 pages|Opening 127"), timeout=PAGE_TIMEOUT)
        shoot(page, "company-reading", width)
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        expect(page.get_by_test_id("signup-status-website")).to_contain_text(re.compile(r"of up to 30 pages|Opening 127|facts wait"), timeout=PAGE_TIMEOUT)
        shoot(page, "ai-reading", width)

        # When the facts are ready, the screen says how many and offers the way to them.
        ready = page.get_by_test_id("website-next")
        expect(ready).to_be_visible(timeout=240000)
        said = READY.search(page.get_by_test_id("signup-status-website").inner_text())
        assert said, page.get_by_test_id("signup-status-website").inner_text()
        pages_read, facts_said = int(said.group(1)), int(said.group(2))
        assert pages_read == 5, pages_read
        shoot(page, "ai-ready", width)
        ready.click()
        page.wait_for_url("**/account/welcome?step=3")

        # The figure agrees with the facts on the screen it leads to, and each fact links to the page it came from.
        facts = page.locator("[data-fact]")
        expect(facts.first).to_be_visible()
        assert facts.count() == facts_said, (facts.count(), facts_said)
        shoot(page, "confirm", width)
        # What the model works out follows as its own job; the person sees it when they open the step again.
        wait_for_the_model_pass(page)
        facts_said = facts.count()

        # The person corrects the company name and confirms what is ticked.
        name = facts.filter(has=page.get_by_test_id("fact-label").get_by_text("Company name", exact=True))
        name.locator("[data-fact-value]").fill("Acme Robotics Group")
        # and ticks the product and the people, which the reader was not sure enough of to tick for them.
        for label in ("Product", "Key person"):
            for box in facts.filter(has=page.get_by_test_id("fact-label").get_by_text(label, exact=True)).get_by_role("checkbox").all():
                box.check()
        kept = page.locator("[data-fact-keep]:checked").count()
        assert 0 < kept <= facts_said
        page.get_by_label("Company size").select_option(label="11 to 50 people")
        page.get_by_label("Your role").fill("CTO")
        page.get_by_label("What do you want first?").select_option(label="Prepare for an audit")
        page.get_by_role("button", name="Continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        # The chat answers about the company from what was confirmed: as many records as were ticked.
        answer = ask(page, str(server), "What did Entelim learn about our company?")
        card = answer.locator("[data-catalogue-entry='company_profile']")
        expect(card).to_be_visible()
        records = card.locator("[data-catalogue-record]")
        assert records.count() == kept, (records.count(), kept)
        expect(card).to_contain_text("%d in your records" % kept)
        expect(card).to_contain_text("Company name: Acme Robotics Group")
        expect(card).to_contain_text("corrected by Casey Founder")
        expect(card).to_contain_text("confirmed by Casey Founder")
        expect(card).not_to_contain_text("Acme Robotics Ltd")
        # Every record's name is readable in full: it wraps inside the card instead of running off the screen.
        edge = page.evaluate("() => window.innerWidth")
        for i in range(records.count()):
            link = records.nth(i).get_by_test_id("catalogue-open")
            box = link.bounding_box()
            assert box and box["x"] + box["width"] <= edge, (link.inner_text(), box, edge)
            fits = link.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
            assert fits, link.inner_text()
        shoot(page, "chat-overview", width)

        # Every fact links to the company's own page it was read from, and each page has content.
        hrefs = sorted({records.nth(i).locator("a").first.get_attribute("href") for i in range(records.count())})
        assert hrefs and all(h.startswith(slow_site.url) for h in hrefs), hrefs
        for href in hrefs:
            _landing_has_content(context, href)

        # A plain question gets its own answer, with the page that states it.
        sell = ask(page, str(server), "What do we sell?", fresh=False)
        expect(sell).to_contain_text("What do we sell?")
        expect(sell).to_contain_text("Product: PickBot")
        link = sell.locator("[data-catalogue-record] a").filter(has_text="PickBot").first
        assert link.get_attribute("href") == slow_site.url + "/products"
        _landing_has_content(context, link.get_attribute("href"), "PickBot")
        leads = ask(page, str(server), "Who leads the company?", fresh=False)
        expect(leads).to_contain_text("Dana Whitfield")
        shoot(page, "chat-sell", width)
    finally:
        context.close()


@pytest.mark.parametrize("width", WIDTHS, ids=[str(w) for w in WIDTHS])
def test_a_founder_who_moved_on_is_told_in_the_bell_and_the_bell_lands_on_the_facts(
        browser, server, people, app, slow_site, width):
    context, page = new_page(browser, width)
    page.set_viewport_size({"width": width, "height": 900 if width > 500 else 844})
    try:
        email, _started = sign_up(page, str(server), people)
        _give_the_website(page, slow_site.url)
        expect(page.get_by_test_id("signup-status-website")).to_contain_text(READING, timeout=PAGE_TIMEOUT)
        # The person does not wait: they carry on to the dashboard.
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=3")
        page.get_by_test_id("signup-continue").click()
        page.wait_for_url("**/account/welcome?step=4")
        page.get_by_role("button", name="Finish").click()
        page.wait_for_url("**/dashboard/**")

        # When the facts are ready the bell says so, once, and its link opens the confirm screen with those facts.
        bell = page.locator("#notif-btn")
        deadline_reached = False
        for _ in range(40):
            page.reload(wait_until="domcontentloaded")
            if page.locator("#notif-badge-count").is_visible() and page.locator("#notif-badge-count").inner_text().strip() == "1":
                deadline_reached = True
                break
            page.wait_for_timeout(5000)
        assert deadline_reached, "the bell never said the facts were ready"
        bell.click()
        panel = page.locator("#notif-dropdown")
        item = panel.locator("a.notif-item")
        expect(item).to_have_count(1)
        message = item.inner_text()
        said = re.search(r"We read your website and (\d+) facts are ready for you to confirm\.", message)
        assert said, message
        shoot(page, "bell", width)
        item.click()
        page.wait_for_url("**/account/welcome?step=3")
        facts = page.locator("[data-fact]")
        expect(facts.first).to_be_visible()
        assert facts.count() == int(said.group(1)), (facts.count(), said.group(1))
        shoot(page, "bell-landing", width)

        # Another organisation's founder, who gave no website, is told nothing.
        other_context, other = new_page(browser, width)
        try:
            sign_up(other, str(server), people)
            other.get_by_test_id("signup-continue").click()
            other.wait_for_url("**/account/welcome?step=3")
            other.get_by_test_id("signup-continue").click()
            other.wait_for_url("**/account/welcome?step=4")
            other.get_by_role("button", name="Finish").click()
            other.wait_for_url("**/dashboard/**")
            other.locator("#notif-btn").click()
            expect(other.locator("#notif-dropdown")).to_contain_text("No notifications")
            expect(other.locator("#notif-badge")).to_be_hidden()
        finally:
            other_context.close()
    finally:
        context.close()


@pytest.mark.parametrize("width", WIDTHS, ids=[str(w) for w in WIDTHS])
def test_a_site_that_cannot_be_read_says_why_and_every_way_forward_lands_on_a_page_with_content(
        browser, server, people, app, closed_site, width):
    context, page = new_page(browser, width)
    page.set_viewport_size({"width": width, "height": 900 if width > 500 else 844})
    try:
        sign_up(page, str(server), people)
        _give_the_website(page, closed_site.url)
        status = page.get_by_test_id("signup-status-website")
        expect(status).to_contain_text("asks readers like ours to stay out, in its robots.txt, so we have not read it.", timeout=PAGE_TIMEOUT * 2)
        expect(status).to_contain_text("You can connect GitHub or Jira instead, or look around the sample company.")
        body = page.locator("main").inner_text()
        for word in ("Traceback", "CrawlRefused", "Exception", "Error:"):
            assert word not in body, word
        shoot(page, "cannot-read", width)

        links = page.get_by_test_id("website-instead")
        assert [links.nth(i).inner_text().strip() for i in range(links.count())] == ["Connect GitHub", "Connect Jira", "See the sample company"]
        # GitHub and Jira are on this very step: the link goes to their forms.
        page.get_by_role("link", name="Connect GitHub").click()
        expect(page).to_have_url(re.compile(r"/account/welcome#signup-github$"))
        expect(page.get_by_test_id("signup-github").get_by_label("GitHub organisation")).to_be_visible()
        page.get_by_role("link", name="Connect Jira").click()
        expect(page).to_have_url(re.compile(r"/account/welcome#signup-jira$"))
        expect(page.get_by_test_id("signup-jira").get_by_label("Jira site address")).to_be_visible()
        # The sample company is offered in the chat, which opens with its offer.
        page.get_by_role("link", name="See the sample company").click()
        page.wait_for_url("**/ai-chat")
        expect(page.get_by_test_id("startup-first-steps").get_by_role("button", name="See a sample company")).to_be_visible(timeout=PAGE_TIMEOUT)
        shoot(page, "cannot-read-sample", width)
    finally:
        context.close()
