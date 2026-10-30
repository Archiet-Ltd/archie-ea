"""Journey tests for the role landing pages on entelim.org, signed out, in a real browser.

Written from what a visitor in one of these roles needs: open the Roles menu, find their
role, land on a page that speaks to their job, see real screens of the product doing that
job, read answers to their questions and find the two next steps (Start free, Talk to us).
The real application is served over a loopback socket and driven with Chromium. Nothing
here asserts a status code or that an element exists without also asserting what the
visitor reads.
"""
import json

import pytest
from playwright.sync_api import sync_playwright

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}

# slug -> the label the Roles menu gives the role (the registry's own label)
ROLE_PAGES = {
    "chief-architects": "Chief Architect",
    "enterprise-architects": "Enterprise Architect",
    "solution-architects": "Solution Architect",
    "business-architects": "Business Architect",
    "security-architects": "Security Architect",
    "data-architects": "Data Architect",
    "cto-and-cio": "Chief Technology Officer",
    "it-portfolio-managers": "Portfolio Manager",
    "scale-up-cto-and-vp-engineering": "Scale-up CTO",
}

# what the hero of each page must say about the role's own job (words from the role, not the product)
HERO_WORDS = {
    "chief-architects": "architecture function",
    "enterprise-architects": "enterprise architect",
    "solution-architects": "design",
    "business-architects": "capabilities",
    "security-architects": "risk",
    "data-architects": "data",
    "cto-and-cio": "CIO",
    "it-portfolio-managers": "portfolio",
    "scale-up-cto-and-vp-engineering": "GitHub",
}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


def _open_from_roles_menu(page, site_url, slug):
    page.goto(site_url + "/", wait_until="domcontentloaded")
    trigger = page.locator("header").get_by_role("button", name="Roles", exact=True)
    trigger.click()
    panel = page.locator("#" + trigger.get_attribute("aria-controls"))
    link = panel.locator(f'a[href="/roles/{slug}"]').first
    assert link.count() == 1, f"the Roles menu has no link to /roles/{slug}"
    assert link.is_visible(), f"the Roles menu link to /roles/{slug} is not visible"
    link.click()
    page.wait_for_url(site_url + f"/roles/{slug}")


@pytest.mark.parametrize("slug", sorted(ROLE_PAGES))
def test_role_page_serves_the_role_from_the_menu_to_the_next_step(browser, site_url, slug):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        _open_from_roles_menu(page, site_url, slug)

        # the site navbar and footer, as on every other page
        assert page.locator("header").get_by_role("button", name="Roles", exact=True).is_visible()
        assert page.locator("footer a[href='/roles']").count() >= 1, "the footer does not link the roles hub"
        assert page.locator("footer a[href='/pricing']").count() >= 1

        # indexable
        robots = page.locator('meta[name="robots"]')
        for i in range(robots.count()):
            assert "noindex" not in (robots.nth(i).get_attribute("content") or "").lower()

        # the hero: one h1 and a lead that speak to the role's job, then the two next steps
        assert page.locator("h1").count() == 1
        hero = page.locator('[data-testid="landing-hero"]')
        assert hero.is_visible()
        h1 = hero.locator("h1").inner_text()
        lead = hero.locator("p").first.inner_text()
        assert len(h1.split()) >= 5 and len(lead.split()) >= 15, (h1, lead)
        assert HERO_WORDS[slug].lower() in (h1 + " " + lead).lower(), (h1, lead)
        start = page.get_by_role("link", name="Start free").first
        assert start.is_visible()
        talk = page.get_by_role("link", name="Talk to us").first
        talk.scroll_into_view_if_needed()
        assert talk.is_visible()
        assert talk.get_attribute("href") == "/contact"

        # at least two real screenshots, each loads (200) and shows
        images = page.locator('[data-testid="landing-hero-figure"] img, [data-testid="role-more-screens-figure"] img')
        count = images.count()
        assert count >= 2, f"{slug} shows {count} screenshots"
        seen = set()
        for i in range(count):
            img = images.nth(i)
            img.scroll_into_view_if_needed()
            src = img.get_attribute("src")
            assert src not in seen, f"{slug} shows {src} twice"
            seen.add(src)
            assert len(img.get_attribute("alt") or "") >= 40, f"{slug}: alt text of {src} is too short"
            response = page.request.get(site_url + src)
            assert response.status == 200 and len(response.body()) > 5000, f"{src} did not load"
            page.wait_for_function("(el) => el.complete && el.naturalWidth > 0", arg=img.element_handle())
            box = img.bounding_box()
            assert box and box["width"] > 200 and box["height"] > 100, f"{src} is not visible"
            assert img.evaluate("(el) => el.parentElement.parentElement.querySelector('figcaption, [class*=frame]') !== null")
        figcaptions = page.locator("figcaption")
        assert figcaptions.count() >= 2

        # the FAQ: visible questions with their answers, and FAQPage structured data that matches
        heading = page.get_by_role("heading", name="Frequently asked questions")
        heading.scroll_into_view_if_needed()
        assert heading.is_visible()
        blocks = [json.loads(t) for t in page.locator('script[type="application/ld+json"]').all_text_contents()]
        flat = [b for entry in blocks for b in (entry if isinstance(entry, list) else entry.get("@graph", [entry]))]
        faq = [b for b in flat if b.get("@type") == "FAQPage"]
        assert faq, f"{slug} has no FAQPage structured data"
        questions = faq[0]["mainEntity"]
        assert len(questions) >= 4, f"{slug} has {len(questions)} FAQ questions"
        body_text = page.locator("main").inner_text()
        for q in questions:
            assert q["name"].strip() in body_text, f"FAQ question not visible on the page: {q['name']}"
            assert len(q["acceptedAnswer"]["text"].split()) >= 15

        # the visitor can follow what the page says: every link in the page body lands on a page
        hrefs = page.eval_on_selector_all(
            "main a[href^='/']", "els => Array.from(new Set(els.map(a => a.getAttribute('href').split('#')[0])))")
        for href in hrefs:
            if href in ("", "/register") or href.startswith("/account/"):
                continue
            r = page.request.get(site_url + href)
            assert r.status == 200, f"{slug} links to {href}, which answers {r.status}"
    finally:
        assert errors == [], errors
        context.close()


@pytest.mark.parametrize("slug", sorted(ROLE_PAGES))
def test_role_page_has_no_sideways_scroll_on_a_phone(browser, site_url, slug):
    context = browser.new_context(viewport=PHONE, is_mobile=True, has_touch=True)
    page = context.new_page()
    try:
        page.goto(site_url + f"/roles/{slug}", wait_until="load")
        page.locator('[data-testid="landing-hero-figure"] img').wait_for(state="visible")
        widths = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
        assert widths[0] <= widths[1], f"{slug} scrolls sideways on a phone: {widths}"
        assert page.locator("h1").is_visible()
    finally:
        context.close()


def test_roles_hub_links_every_role_that_has_a_page_and_no_link_is_a_404(browser, site_url):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        page.goto(site_url + "/roles", wait_until="domcontentloaded")
        hub = page.locator("main")
        hrefs = set(hub.evaluate_all("els => els.flatMap(m => Array.from(m.querySelectorAll('a[href]')).map(a => a.getAttribute('href')))"))
        for slug in ROLE_PAGES:
            assert f"/roles/{slug}" in hrefs, f"/roles does not link {slug}"
        for href in sorted(hrefs):
            path = href.split("#")[0]
            if not path.startswith("/") or path.startswith("/account/") or path == "/register":
                continue
            response = page.request.get(site_url + path)
            assert response.status == 200, f"/roles links to {href}, which answers {response.status}"
        # the hub offers only roles that have a page (the registry's rule, since the roles page became cards):
        # one card per page, each a link to a page that exists, and a role without a page is not offered at all
        cards = hub.locator("[data-role-card]")
        card_hrefs = [cards.nth(i).get_attribute("href") for i in range(cards.count())]
        assert len(card_hrefs) == len(set(card_hrefs)), f"a role page is offered twice: {card_hrefs}"
        assert {f"/roles/{slug}" for slug in ROLE_PAGES} <= set(card_hrefs), card_hrefs
        assert hub.get_by_role("link", name="Chief Financial Officer").count() == 0
    finally:
        context.close()


def test_scale_up_cto_page_leads_with_connecting_github_or_jira_then_the_spreadsheet(browser, site_url):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    try:
        page.goto(site_url + "/roles/scale-up-cto-and-vp-engineering", wait_until="domcontentloaded")
        hero = page.locator('[data-testid="landing-hero"]')
        top = (hero.locator("h1").inner_text() + " " + hero.locator("p").first.inner_text())
        assert "GitHub" in top and "Jira" in top, top
        assert top.index("GitHub") < top.lower().index("spreadsheet"), top
        body = page.locator("main").inner_text()
        assert "Connect GitHub" in body and "Connect Jira" in body
        assert body.index("Connect GitHub") < body.index("Import, on the Applications screen") or \
            body.index("Connect GitHub") < body.index("Import")
        assert "second route" in body and "spreadsheet" in body
    finally:
        context.close()


def test_scale_up_cto_page_shows_a_phone_sized_capture_on_a_phone(browser, site_url):
    context = browser.new_context(viewport=PHONE, is_mobile=True, has_touch=True, device_scale_factor=1)
    page = context.new_page()
    try:
        page.goto(site_url + "/roles/scale-up-cto-and-vp-engineering", wait_until="load")
        img = page.locator('[data-testid="landing-hero-figure"] img')
        img.wait_for(state="visible")
        page.wait_for_function("(el) => el.complete && el.naturalWidth > 0", arg=img.element_handle())
        src = img.evaluate("el => el.currentSrc")
        natural = img.evaluate("el => el.naturalWidth")
        shown = img.bounding_box()["width"]
        assert "mobile" in src, f"a phone is given the desktop image: {src}"
        assert natural <= 3 * 390, f"the capture is {natural}px wide, not a phone capture"
        assert shown <= 390
    finally:
        context.close()
