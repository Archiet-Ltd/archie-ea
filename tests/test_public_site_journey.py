"""A startup CTO visits the public site, looks around, joins a waiting list and starts free.

The person: a startup CTO who has never seen Entelim. The goal: find out what it does today, see which
things are not available yet, say which they want, and start free. They land on the home page, open every
menu, follow every link, join the waiting list for something that is not built yet and are told they are on
it, read what the paid plans cost and what the button does, and reach sign-up from the main action.
Run at 1440 and 390 wide, light and dark.

What a person needs from each step:
- the navbar is the library's navigation menu on a desktop and its sheet on a phone;
- every link in the navbar and the footer lands on a page with content;
- an early-access page joins a waiting list and never starts a sign-up, and the answer says so;
- the paid plans' button joins the paid-plan waiting list, because paying opens later, and nothing early
  access is listed in a plan;
- Community starts free.
"""
import re
import threading

import pytest
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}
EARLY_ACCESS_PAGES = {
    "Ask in plain language": "/modules/ask-in-plain-language",
    "Business model canvas and frameworks": "/modules/business-model-canvas-and-frameworks",
    "Connect GitHub or Jira": "/modules/connect-github-or-jira",
    "An architecture team in your chat": "/modules/an-architecture-team-in-your-chat",
}


@pytest.fixture(scope="module")
def site_url(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


def _lands_on_content(page, site_url, href, label):
    response = page.request.get(href if href.startswith("http") else site_url + href)
    assert response.ok, f"{label}: {href} answered {response.status}"
    body = re.sub(r"<[^>]+>", " ", response.text())
    assert len(" ".join(body.split())) > 300, f"{label}: {href} has no content"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_desktop_navigation_is_the_navigation_menu_and_every_link_lands_on_content(browser, site_url, scheme):
    context = browser.new_context(viewport=DESKTOP, color_scheme=scheme)
    page = context.new_page()
    try:
        page.goto(site_url + "/")
        nav = page.locator("[data-site-nav] [data-slot=navigation-menu]")
        expect(nav).to_be_visible()
        assert page.locator("[data-site-nav] [data-slot=navigation-menu-trigger]").count() >= 4
        hrefs = set()
        for trigger in page.locator("[data-site-nav] [data-menu-trigger][data-nav-top]").all():
            trigger.click()
            panel = page.locator("#" + trigger.get_attribute("aria-controls"))
            expect(panel).to_be_visible()
            assert panel.get_attribute("data-slot") == "navigation-menu-content"
            hrefs |= {a for a in panel.locator("a[href]").evaluate_all("els => els.map(a => a.getAttribute('href'))")}
            trigger.click()
        hrefs |= set(page.locator("[data-site-nav] a[href]").evaluate_all("els => els.map(a => a.getAttribute('href'))"))
        hrefs |= set(page.locator("[data-site-footer] a[href]").evaluate_all("els => els.map(a => a.getAttribute('href'))"))
        hrefs = {h for h in hrefs if h and not h.startswith(("#", "mailto:")) and "/account/" not in h}
        assert len(hrefs) > 20, hrefs
        for href in sorted(hrefs):
            if href.startswith("http") and not href.startswith(site_url):
                continue
            _lands_on_content(page, site_url, href, "navbar or footer")
        # The footer separates its parts with the library's separator.
        assert page.locator("[data-site-footer] [data-slot=separator]").count() >= 1
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_phone_menu_is_a_sheet_that_opens_and_closes(browser, site_url, scheme):
    context = browser.new_context(viewport=PHONE, color_scheme=scheme)
    page = context.new_page()
    try:
        page.goto(site_url + "/")
        sheet = page.locator("#site-nav-panel")
        assert sheet.get_attribute("data-slot") == "sheet-content"
        expect(sheet).to_be_hidden()
        page.locator("[data-sheet-trigger]").click()
        expect(sheet).to_be_visible()
        expect(sheet.get_by_text("Menu", exact=True)).to_be_visible()
        sheet.locator("[data-menu-trigger][aria-controls=menu-product]").click()
        expect(page.locator("#menu-product")).to_be_visible()
        # The early-access entries are in the phone menu too.
        expect(page.locator("#menu-product")).to_contain_text("Connect GitHub or Jira")
        page.locator("[data-sheet-close]").click()
        expect(sheet).to_be_hidden()
        # Nothing overflows sideways on a phone.
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440", "390"])
@pytest.mark.parametrize("label,path", list(EARLY_ACCESS_PAGES.items()))
def test_a_visitor_joins_the_waiting_list_for_something_not_built_yet(browser, site_url, app, viewport, label, path):
    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    try:
        page.goto(site_url + "/")
        if viewport is PHONE:
            page.locator("[data-sheet-trigger]").click()
        page.locator("[data-menu-trigger][aria-controls=menu-product]").click()
        page.locator("#menu-product").get_by_role("link", name=label, exact=False).first.click()
        expect(page).to_have_url(re.compile(re.escape(path) + "$"))
        expect(page.get_by_role("heading", level=1)).to_contain_text(label.split(" ")[0])
        expect(page.get_by_test_id("early-access-page")).to_contain_text("Early access")
        assert page.locator("img").evaluate_all("els => els.filter(e => e.closest('main')).length") == 0, "an early-access page shows a screenshot"
        assert page.get_by_role("link", name=re.compile(r"^Buy", re.I)).count() == 0, "an early-access page sells"
        email = "visitor-%s-%s@example.com" % (re.sub(r"\W", "", label)[:12].lower(), viewport["width"])
        form = page.get_by_test_id("feature-interest-form")
        form.get_by_label("Email").fill(email)
        form.get_by_label(re.compile("Used to tell you")).check()
        form.get_by_role("button", name="Join the waiting list").click()
        expect(page.get_by_test_id("feature-interest-thanks")).to_contain_text("waiting list")
        from app.models.product_inquiry import ProductInquiry

        with app.app_context():
            row = ProductInquiry.query.filter_by(email=email).first()
            assert row is not None and row.offer == ("feature:" + path.rsplit("/", 1)[1])[:50], "the waiting-list entry was not stored"
    finally:
        context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("viewport", [DESKTOP, PHONE], ids=["1440", "390"])
def test_pricing_lists_what_exists_and_paid_plans_join_a_waiting_list(browser, site_url, app, viewport, scheme):
    context = browser.new_context(viewport=viewport, color_scheme=scheme)
    page = context.new_page()
    try:
        page.goto(site_url + "/pricing")
        body = page.locator("main").evaluate(
            "el => { const c = el.cloneNode(true); c.querySelectorAll('[data-site-footer], script').forEach(n => n.remove()); return c.textContent; }")
        body = " ".join(body.split())
        for published in ("USD 490", "USD 290", "24,000"):
            assert published in body, published
        for absent in ("SAML", "Single sign-on", "review-board workflow", "Business Model Canvas", "ArchiMate Composer",
                       "Audit log export", "dedicated deployment", "Custom service levels", "model key", "Buy Startup", "Buy Team"):
            assert absent.lower() not in body.lower(), f"{absent!r} is on the pricing page"
        assert "minimum five editors" in body.lower()
        # Community starts free; the paid plans join the waiting list.
        page.get_by_test_id("buy-community").click()
        expect(page).to_have_url(re.compile(r"/account/register"))
        assert len(page.locator("form").first.inner_text().strip()) > 20
        page.goto(site_url + "/pricing")
        page.get_by_test_id("waitlist-startup").click()
        expect(page).to_have_url(re.compile(r"/modules/paid-plans$"))
        expect(page.get_by_test_id("early-access-page")).to_contain_text("Startup and Team")
        form = page.get_by_test_id("feature-interest-form")
        email = "paid-%s-%s@example.com" % (viewport["width"], scheme)
        form.get_by_label("Email").fill(email)
        form.get_by_label(re.compile("Used to tell you")).check()
        form.get_by_role("button", name="Join the waiting list").click()
        expect(page.get_by_test_id("feature-interest-thanks")).to_be_visible()
        from app.models.product_inquiry import ProductInquiry

        with app.app_context():
            assert ProductInquiry.query.filter_by(email=email).first() is not None
    finally:
        context.close()


def test_the_main_action_reaches_sign_up_and_the_home_page_shows_what_is_early_access(browser, site_url):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        page.goto(site_url + "/")
        for item in list(EARLY_ACCESS_PAGES)[:3]:
            expect(page.get_by_test_id("home-early-access")).to_contain_text(item)
        # The home page claims nothing that arrives later as available.
        text = page.locator("main").evaluate(
            "el => { const c = el.cloneNode(true); c.querySelectorAll('[data-testid=home-early-access]').forEach(n => n.remove()); return c.textContent; }")
        for later in ("cited answer", "Connect GitHub and", "eighteen capabilities", "live on every plan", "Q2 2027"):
            if later == "cited answer":
                assert "get a cited answer" not in text
            else:
                assert later not in text, later
        # Seven jobs: building the first model and knowing what you have are open; the other five are coming next.
        assert page.locator("[data-testid=home-job-today]").count() == 2
        assert page.locator("[data-testid=home-job-next]").count() == 5
        expect(page.locator("[data-job=first-model]")).to_contain_text("We read your website and build your first model in minutes")
        expect(page.locator("[data-job=first-model]").get_by_text("Available today")).to_be_visible()
        team = page.get_by_test_id("home-architecture-team")
        expect(team).to_contain_text("Coming next: an architecture team in your chat.")
        expect(team).to_contain_text("You approve every change.")
        assert team.locator("img").count() == 0, "the role advisers carry no screenshot"
        team.get_by_test_id("home-architecture-team-join").click()
        expect(page).to_have_url(re.compile(r"/modules/an-architecture-team-in-your-chat$"))
        expect(page.get_by_test_id("early-access-page")).to_contain_text("Early access")
        page.goto(site_url + "/")
        page.get_by_test_id("home-hero-start").click()
        expect(page).to_have_url(re.compile(r"/account/register"))
        assert len(page.locator("form").first.inner_text().strip()) > 20
    finally:
        context.close()
