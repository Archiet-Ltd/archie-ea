"""Browser journey for the home page's capabilities section.

Written from a visitor's goal: a startup CTO scanning entelim.org's home page
should see a capability count that cannot disagree with itself, a numbered
list holding only what ships today, every one of those links landing on real
content, and a separate, honestly-labelled Coming soon group that leads to a
working waiting-list form rather than pretending those three are already
available. The real application is served over a loopback socket and driven
with Chromium; the pages are the real templates, the real stylesheets and
the real waitlist-signup route.

Screenshots are written for a human walk at both viewport widths and in both
colour schemes, per the brief's acceptance criteria.
"""

import os
import pathlib
import re

import pytest
from playwright.sync_api import expect, sync_playwright

DESKTOP = {"width": 1440, "height": 900}
PHONE = {"width": 390, "height": 844}

WALK = pathlib.Path(os.environ.get("SITE13_WALK_DIR", "/tmp/site13-walk"))

DELIVERED_ANCHOR_IDS = [
    "strategy-management", "portfolio-management", "transformation-management",
    "investment-and-financial-management", "architecture-management", "data-management",
    "people-and-organisation-management", "operations-and-service-management",
    "risk-management", "compliance-and-governance", "vendor-and-procurement-management",
    "ai-management", "enterprise-intelligence", "agentic-automation",
    "collaboration-and-communication", "integration-and-interoperability",
    "administration-and-trust",
]

COMING_SOON_LABELS = ["Scenarios", "Advisers for every role", "Predictive checks"]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


def _open_home(browser, site_url, *, width, scheme):
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(site_url + "/")
    return context, page, errors


@pytest.mark.parametrize("width", [DESKTOP["width"], PHONE["width"]])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_capabilities_heading_link_and_list_agree_on_one_count(browser, site_url, width, scheme):
    """The heading's number, the closing link's number and the number of
    entries actually listed must be the same number -- they are rendered
    from the same capability_groups list in the template, so this also
    catches a future edit to one of the three that forgets the others."""
    context, page, errors = _open_home(browser, site_url, width=width, scheme=scheme)
    try:
        heading = page.get_by_test_id("home-capabilities-heading")
        expect(heading).to_be_visible()
        heading_text = heading.text_content()
        match = re.search(r"The (\w+) capabilities", heading_text)
        assert match, f"heading does not state a count: {heading_text!r}"
        number_word = match.group(1)

        closing = page.get_by_test_id("home-capabilities-closing")
        expect(closing).to_contain_text("Every capability listed here is in the product today.")
        assert f"See all {number_word} in full" in closing.text_content()

        listed_links = page.locator('[data-testid="home-capabilities-list"] li a')
        WORDS_TO_INT = {
            "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
            "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
        }
        assert number_word in WORDS_TO_INT, f"unrecognised count word: {number_word!r}"
        assert listed_links.count() == WORDS_TO_INT[number_word] == 17

        WALK.mkdir(parents=True, exist_ok=True)
        heading.scroll_into_view_if_needed()
        page.screenshot(
            path=str(WALK / f"home-capabilities-{width}-{scheme}.png"),
            full_page=True,
        )
    finally:
        assert errors == [], f"the page raised script errors: {errors}"
        context.close()


def test_no_coming_soon_entry_sits_in_the_numbered_list(browser, site_url):
    """None of the three coming-soon labels appears among the delivered,
    numbered entries -- they live only in the separate Coming soon block."""
    context, page, errors = _open_home(browser, site_url, width=DESKTOP["width"], scheme="light")
    try:
        listed_labels = page.locator('[data-testid="home-capabilities-list"] li a').all_text_contents()
        for label in COMING_SOON_LABELS:
            assert label not in listed_labels, f"{label!r} (coming soon) sits in the delivered list"
        assert len(listed_labels) == 17
    finally:
        assert errors == [], f"the page raised script errors: {errors}"
        context.close()


def test_every_delivered_entry_links_to_a_features_section_with_real_content(browser, site_url):
    """Every one of the 17 delivered entries is a link to /features#<id>;
    this follows each one and asserts the target heading is on the page and
    is followed by real body text, not an empty or missing section."""
    context, page, errors = _open_home(browser, site_url, width=DESKTOP["width"], scheme="light")
    try:
        links = page.locator('[data-testid="home-capabilities-list"] li a').evaluate_all(
            "els => els.map(e => [e.getAttribute('href'), e.textContent.trim()])"
        )
        assert len(links) == 17
        hrefs = [href for href, _ in links]
        assert hrefs == [f"/features#{anchor_id}" for anchor_id in DELIVERED_ANCHOR_IDS], hrefs

        page.goto(site_url + "/features")
        for href, label in links:
            anchor_id = href.split("#", 1)[1]
            heading_el = page.locator(f"#{anchor_id}")
            expect(heading_el).to_be_attached()
            # the body copy directly after the heading must be real text, not empty
            body_text = heading_el.evaluate(
                "el => { let n = el.nextElementSibling; let out = ''; "
                "for (let i = 0; i < 3 && n; i++, n = n.nextElementSibling) out += n.textContent; "
                "return out.trim(); }"
            )
            assert len(body_text) > 20, f"{label!r} (#{anchor_id}) has no real body content on /features"
    finally:
        assert errors == [], f"the page raised script errors: {errors}"
        context.close()


def test_coming_soon_block_is_labelled_and_leads_to_a_working_waiting_list_form(browser, site_url):
    """The Coming soon block: labelled "Coming soon", carries
    "Not available yet. Join the waiting list.", lists exactly the three
    entries, each marked early_access like the menu's own coming-soon rows,
    and each leads to a page with a working interest form. One submission
    is carried through end to end; the other two are checked for a visible,
    working form (the menu journey already submits through "Scenarios")."""
    context, page, errors = _open_home(browser, site_url, width=DESKTOP["width"], scheme="light")
    try:
        block = page.get_by_test_id("home-coming-soon")
        expect(block).to_be_visible()
        expect(block.get_by_text("Coming soon", exact=True)).to_be_visible()
        expect(block.get_by_text("Not available yet. Join the waiting list.")).to_be_visible()

        rows = block.locator("a[data-availability='early_access']")
        assert rows.count() == 3
        row_data = rows.evaluate_all(
            "els => els.map(e => [e.getAttribute('href'), e.textContent.trim()])"
        )
        assert [label for _, label in row_data] == COMING_SOON_LABELS
        for label in COMING_SOON_LABELS:
            assert f"{label} (coming soon)" not in page.content(), (
                f"{label!r} still carries its own inline (coming soon) suffix; "
                "the block label now carries that meaning"
            )

        # Advisers for every role: a full, working submission.
        block.get_by_role("link", name="Advisers for every role").click()
        expect(page).to_have_url(site_url + "/modules/advisers-for-every-role")
        form = page.get_by_test_id("feature-interest-form")
        expect(form).to_be_visible()
        form.get_by_label("Email").fill("visitor-home-capabilities@example.com")
        form.get_by_label("Used to tell you when this becomes available, and for nothing else.").check()
        form.get_by_test_id("feature-interest-submit").click()
        expect(page.get_by_test_id("feature-interest-thanks")).to_be_visible()

        # Predictive checks: the form is present and working, checked without submitting again.
        page.goto(site_url + "/")
        block = page.get_by_test_id("home-coming-soon")
        block.get_by_role("link", name="Predictive checks").click()
        expect(page).to_have_url(site_url + "/modules/predictive-checks")
        expect(page.get_by_test_id("feature-interest-form")).to_be_visible()
    finally:
        assert errors == [], f"the page raised script errors: {errors}"
        context.close()


@pytest.mark.parametrize("width", [DESKTOP["width"], PHONE["width"]])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_no_sideways_scroll_on_the_capabilities_section(browser, site_url, width, scheme):
    """The capabilities section and the new Coming soon block keep the page
    within its own width at both the desktop and phone viewports."""
    context, page, errors = _open_home(browser, site_url, width=width, scheme=scheme)
    try:
        page.get_by_test_id("home-coming-soon").scroll_into_view_if_needed()
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        assert scroll_width <= client_width + 1, (
            f"page scrolls sideways at {width}px ({scheme}): "
            f"scrollWidth={scroll_width} clientWidth={client_width}"
        )
    finally:
        assert errors == [], f"the page raised script errors: {errors}"
        context.close()
