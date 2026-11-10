"""A visitor from a systems-engineering company finds the SysML early-access page and joins its waiting list.

The goal, as the visitor: "We model in SysML and our architects work in ArchiMate. Does Entelim bring the two
together, and can I tell them I need it?" The real application is served over a loopback socket and driven with
Chromium, signed out. The page is public, so there is no organisation to seed; the sign-up it records is counted
in the same table and event log the other waiting-list pages use.

Screenshots of the walk go to SYSML_WALK_DIR when it is set, otherwise to a temporary folder.
"""

import os
import pathlib
import tempfile
import uuid

import pytest
from playwright.sync_api import expect, sync_playwright

PATH = "/modules/sysml-to-archimate"
OFFER = "feature:sysml-to-archimate"
LABEL = "Bring your SysML models"
SHOTS = pathlib.Path(os.environ.get("SYSML_WALK_DIR") or tempfile.gettempdir()) / "sysml-walk"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def _counts(app):
    from app.models.product_inquiry import ProductInquiry
    from app.models.public_visitor_event import PublicVisitorEvent

    with app.app_context():
        return (
            ProductInquiry.query.filter_by(offer=OFFER).count(),
            PublicVisitorEvent.query.filter_by(event_type="offer_enquiry_submitted", offer=OFFER).count(),
        )


def _forget(app, email):
    from app import db
    from app.models.product_inquiry import ProductInquiry

    with app.app_context():
        ProductInquiry.query.filter_by(email=email, offer=OFFER).delete()
        db.session.commit()


def _open_through_the_menu(page, site_url):
    page.goto(site_url + "/")
    page.locator("header").get_by_role("button", name="Product", exact=True).click()
    soon = page.locator("#menu-product [data-menu-group='coming-soon']")
    link = soon.get_by_role("link", name=LABEL)
    expect(link).to_be_visible()
    link.click()
    expect(page).to_have_url(site_url + PATH)


def test_a_visitor_reaches_the_sysml_page_from_the_menu_and_reads_that_it_is_early_access(browser, site_url):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    _open_through_the_menu(page, site_url)
    expect(page.locator("h1").first).to_contain_text("SysML")
    first_lines = " ".join(page.locator("main").inner_text().split()[:60]).lower()
    assert "early access" in first_lines
    text = page.locator("main").inner_text()
    assert "ArchiMate" in text and "SysML v2" in text
    for word in ("certified", "compliant with"):
        assert word not in text.lower()
    # nothing to look at that does not exist: no screenshot, picture or video of an import
    own = page.evaluate(
        "() => Array.from(document.querySelectorAll('main img, main picture, main video, main figure'))"
        ".filter(e => !e.closest('footer')).length"
    )
    assert own == 0
    context.close()


def test_joining_the_waiting_list_records_one_sign_up_against_this_page(browser, site_url, app):
    email = f"sysml.visitor.{uuid.uuid4().hex[:8]}@example.com"
    before = _counts(app)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _open_through_the_menu(page, site_url)
        form = page.get_by_test_id("feature-interest-form")
        form.get_by_label("Email").fill(email)
        form.get_by_label("Used to tell you when this becomes available").check()
        form.get_by_test_id("feature-interest-submit").click()
        expect(page.get_by_test_id("feature-interest-thanks")).to_be_visible()
        assert _counts(app) == (before[0] + 1, before[1] + 1)
    finally:
        context.close()
        _forget(app, email)


def test_every_link_on_the_page_lands_on_a_page_with_content(browser, site_url):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.goto(site_url + PATH)
    hrefs = page.evaluate(
        "() => Array.from(document.querySelectorAll('main a[href]')).map(a => a.getAttribute('href'))"
    )
    targets = sorted({h.split("#")[0] for h in hrefs if h.startswith("/") and h.split("#")[0]})
    # the page links to the notation, import, chat, twin map and connector pages the brief names
    for wanted in ("/modules/architecture-model", "/modules/ai-chat", "/connectors", "/modules/digital-twin"):
        assert wanted in targets, wanted
    problems = []
    for target in targets:
        response = page.goto(site_url + target, wait_until="domcontentloaded")
        words = len(page.locator("main").inner_text().split())
        if response.status != 200 or not page.locator("h1").first.inner_text().strip() or words < 40:
            problems.append((target, response.status, words))
    assert problems == []
    context.close()


def test_the_hub_lists_the_page_and_the_product_menu_and_pricing_do_not_sell_it(browser, site_url):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.goto(site_url + "/early-access")
    entry = page.get_by_test_id("early-access-hub").get_by_role("link", name=LABEL)
    expect(entry).to_have_attribute("href", PATH)
    entry.click()
    expect(page).to_have_url(site_url + PATH)
    expect(page.locator("h1").first).to_contain_text("SysML")

    page.goto(site_url + "/")
    page.locator("header").get_by_role("button", name="Product", exact=True).click()
    live = page.locator("#menu-product a[data-availability='live']")
    assert LABEL not in " ".join(live.all_inner_texts())
    pricing = page.goto(site_url + "/pricing")
    assert pricing.status == 200
    # the site footer sits inside main and lists everything coming; the plans and prices are the rest of the page
    plans = page.evaluate(
        "() => { const m = document.querySelector('main').cloneNode(true);"
        " m.querySelectorAll('footer').forEach(f => f.remove()); return m.innerText; }"
    )
    assert "SysML" not in plans
    context.close()


@pytest.mark.parametrize("scheme,viewport", [("light", {"width": 1440, "height": 900}), ("dark", {"width": 390, "height": 844})])
def test_the_page_is_readable_in_both_themes_and_widths(browser, site_url, scheme, viewport):
    context = browser.new_context(viewport=viewport, is_mobile=viewport["width"] < 500)
    page = context.new_page()
    page.goto(site_url + PATH)
    if scheme == "dark":
        # the site's own theme switch
        page.evaluate("() => Alpine.store('theme').toggle()")
        expect(page.locator("html")).to_have_class(__import__("re").compile(r"\bdark\b"))
    expect(page.locator("h1").first).to_contain_text("SysML")
    overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1
    _shot(page, f"sysml-{scheme}-{viewport['width']}")
    context.close()
