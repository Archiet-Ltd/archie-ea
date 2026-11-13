"""A person looks for an element by typing its name, in the global search and in the Impact Analysis picker.

The signed-in person is the Administrator of the demonstration company. Nine texts are typed into both
boxes, one after the other: the full name, a part of it and the name with one letter wrong, for an
application ("Customer Portal"), a capability ("Asset Lifecycle Management") and a business process
("Annual calibration by a third-party laboratory"). For each text the two boxes list the same elements
(the global search lists pages as well, and stops at five records). The picker is then driven with the
keyboard alone, and a text nothing answers offers a next step.

Run at 1440 wide in the light theme and at 390 wide in the dark theme. Pictures are written only when
JOURNEY_SHOT_DIR names a folder.
"""

import os
import re
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, PASSWORD
from .fresh_org import sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

DEMO_EMAIL = "demo@lantern-quay.example.com"
PAGE = "/strategic/impact-analysis"
VIEWS = [("light", 1440, 900), ("dark", 390, 844)]
EXPECT_DEFAULT_TIMEOUT = 5000

SEARCH = 'input[aria-label="Type to search..."]'
ROWS = '#impact-picker-list [role="option"]'
GLOBAL_INPUT = 'input[aria-label="Search pages and records"]'
GLOBAL_ROWS = '[data-testid="sidebar-search-record"]'
NO_MATCH = 'div[role="status"]:has-text("No elements match")'

# (text typed, name that both boxes must list)
TEXTS = [
    ("Customer Portal", "Customer Portal"),
    ("Portal", "Customer Portal"),
    ("Custumer Portal", "Customer Portal"),
    ("Asset Lifecycle Management", "Asset Lifecycle Management"),
    ("Lifecycle", "Asset Lifecycle Management"),
    ("Asset Lifecycle Managemant", "Asset Lifecycle Management"),
    ("Annual calibration by a third-party laboratory", "Annual calibration by a third-party laboratory"),
    ("calibration", "Calibration Ledger"),
    ("Annual calibratien by a third-party laboratory", "Annual calibration by a third-party laboratory"),
]

# Where a record in the global search lands. Anything else in the list is a page.
RECORD_LINK = re.compile(r"^/(architecture/[a-z_]+/element/|applications/|enterprise/capabilities/|capability-map|solutions/)")
ELEMENT_ONLY = re.compile(r"^/(architecture/[a-z_]+/element/|applications/|enterprise/capabilities/|capability-map)")

READY = """() => {
  const root = document.querySelector('[x-data="impactAnalysis()"]');
  return !!(window.Alpine && root && root._x_dataStack);
}"""


@pytest.fixture(scope="module", autouse=True)
def _assertions_wait_as_long_as_pages_do():
    expect.set_options(timeout=PAGE_TIMEOUT)
    yield
    expect.set_options(timeout=EXPECT_DEFAULT_TIMEOUT)


@pytest.fixture(scope="module")
def demo(live_server, app):
    """The demonstration organisation, seeded the way the product's own seed does it."""
    previous = os.environ.get("DEMO_USER_PASSWORD")
    os.environ["DEMO_USER_PASSWORD"] = PASSWORD
    try:
        with app.app_context():
            from app import db
            from app.commands.seed_demo_company import seed_demo_company
            from app.models.organization import Organization
            from app.models.user import User

            seed_demo_company()
            org = Organization.query.filter_by(slug="lantern-quay").one()
            admin = User.query.filter_by(email=DEMO_EMAIL, organization_id=org.id).one()
            admin.password = PASSWORD
            admin.is_org_admin = True
            db.session.commit()
            db.session.remove()
        yield
    finally:
        if previous is None:
            os.environ.pop("DEMO_USER_PASSWORD", None)
        else:
            os.environ["DEMO_USER_PASSWORD"] = previous


@pytest.fixture(params=VIEWS, ids=["1440-light", "390-dark"])
def view(request, browser, live_server, demo):
    scheme, width, height = request.param
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme)
    context.set_default_timeout(PAGE_TIMEOUT)
    context.set_default_navigation_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    sign_in(page, live_server, DEMO_EMAIL)
    yield page, scheme, width
    context.close()


def _picture(page, name, scheme, width):
    folder = os.environ.get("JOURNEY_SHOT_DIR")
    if folder:
        os.makedirs(folder, exist_ok=True)
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(folder, "%s-%s-%d.png" % (name, scheme, width)), full_page=True)


def _open(page, base, scheme):
    page.goto(base + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_function(READY, timeout=PAGE_TIMEOUT)
    if scheme == "dark":
        page.evaluate("() => document.documentElement.classList.add('dark')")
    page.wait_for_timeout(1200)


def _asked(response, path, text):
    """True when the response answers a search for exactly this text."""
    url = urlparse(response.url)
    return url.path == path and parse_qs(url.query).get("q") == [text]


def _picker_names(page, text):
    """What the picker lists for the text: the names in its rows, once it has answered."""
    page.fill(SEARCH, "")
    with page.expect_response(lambda r: _asked(r, "/archimate/api/elements/search", text)):
        page.fill(SEARCH, text)
    page.wait_for_timeout(400)      # the rows are drawn from the answer
    page.wait_for_function(
        """() => document.querySelectorAll('#impact-picker-list [role="option"]').length > 0
                 || [...document.querySelectorAll('div[role="status"]')].some(d => d.textContent.includes('No elements match'))""",
        timeout=PAGE_TIMEOUT,
    )
    return page.locator(ROWS).evaluate_all("els => els.map(e => e.querySelector('span').textContent.trim())")


def _global_records(page, text):
    """What the global search (the sidebar's search box) lists for the text, as (name, link) pairs."""
    box = page.locator(GLOBAL_INPUT)
    if not box.is_visible():
        page.locator('[data-testid="sidebar-trigger"]').first.click()
    expect(box).to_be_visible()
    box.fill("")
    with page.expect_response(lambda r: _asked(r, "/api/sidebar/search", text)):
        box.fill(text)
    page.wait_for_timeout(400)      # the rows are drawn from the answer
    page.wait_for_function(
        """() => document.querySelectorAll('[data-testid="sidebar-search-record"]').length > 0
                 || document.querySelector('#sidebar-search-results').textContent.includes('Nothing matches')""",
        timeout=PAGE_TIMEOUT,
    )
    rows = page.locator(GLOBAL_ROWS).evaluate_all(
        "els => els.map(e => [e.querySelector('span').textContent.trim(), e.getAttribute('href')])"
    )
    box.fill("")
    return [(name, href) for name, href in rows if RECORD_LINK.match(href or "")]


def test_both_boxes_list_the_same_elements_for_all_nine_texts(view, live_server):
    page, scheme, width = view
    _open(page, live_server, scheme)
    for text, wanted in TEXTS:
        picker = _picker_names(page, text)
        records = _global_records(page, text)
        global_names = [name for name, _ in records]

        # Both boxes answer, and both list the element the person was looking for.
        assert wanted in picker, (text, picker)
        assert wanted in global_names, (text, global_names)
        # Everything the global search lists as an element is an element the picker lists.
        stray = [name for name, href in records if ELEMENT_ONLY.match(href) and name not in picker]
        assert not stray, (text, stray, picker)
        # When the picker lists no more than the global search can hold, the two lists are the same.
        if len(picker) <= 5:
            assert sorted(global_names) == sorted(picker), (text, global_names, picker)
        if text == "Custumer Portal":
            _picture(page, "same-answer-misspelt", scheme, width)

    # Every link lands on a page with content: the global search's link for the full name, and the picker's
    # analysis for it.
    for text, wanted in TEXTS[:1] + TEXTS[3:4] + TEXTS[6:7]:
        link = dict(_global_records(page, text))[wanted]
        page.goto(live_server + link, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_text(wanted).locator("visible=true").first).to_be_visible()
        _open(page, live_server, scheme)
        _picker_names(page, text)
        page.locator(ROWS).filter(has_text=wanted).first.click()
        analyze = page.locator("button", has_text="Analyze Impact")
        expect(analyze).to_be_enabled()
        analyze.click()
        expect(page.get_by_text("Impact of Minor Modification on " + wanted)).to_be_visible()
        expect(page.get_by_role("heading", name="Direct Architectural Dependencies")).to_be_visible()


def test_the_picker_works_by_keyboard_and_focus_lands_on_analyze_impact(view, live_server):
    page, scheme, width = view
    _open(page, live_server, scheme)
    names = _picker_names(page, "Portal")
    assert len(names) >= 3, names

    box = page.locator(SEARCH)
    assert box.get_attribute("role") == "combobox"
    assert box.get_attribute("aria-expanded") == "true"
    assert page.locator("#impact-picker-list").get_attribute("role") == "listbox"

    box.focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    active = box.get_attribute("aria-activedescendant")
    option = page.locator("#" + active)
    assert option.get_attribute("role") == "option" and option.get_attribute("aria-selected") == "true"
    assert option.locator("span").first.inner_text().strip() == names[1]
    page.keyboard.press("ArrowUp")
    assert page.locator("#" + box.get_attribute("aria-activedescendant")).locator("span").first.inner_text().strip() == names[0]
    page.keyboard.press("ArrowDown")
    _picture(page, "keyboard-choosing", scheme, width)
    page.keyboard.press("Enter")

    chosen = names[1]
    expect(page.get_by_text(chosen + " selected")).to_be_visible()
    assert page.evaluate("() => document.activeElement.textContent.trim()").startswith("Analyze Impact")
    assert box.get_attribute("aria-expanded") == "false"
    _picture(page, "keyboard-chosen", scheme, width)
    page.keyboard.press("Enter")
    expect(page.get_by_text("Impact of Minor Modification on " + chosen)).to_be_visible()


def test_a_text_nothing_answers_offers_a_next_step(view, live_server):
    page, scheme, width = view
    _open(page, live_server, scheme)
    assert _picker_names(page, "zzqxv") == []
    status = page.locator(NO_MATCH)
    expect(status).to_have_text("No elements match 'zzqxv' in any layer.")
    hint = page.get_by_text("Check the spelling or type fewer words.")
    expect(hint).to_be_visible()
    _picture(page, "no-match-next-step", scheme, width)
    page.get_by_role("button", name="Clear the search").click()
    expect(page.locator(SEARCH)).to_have_value("")
    assert page.evaluate("() => document.activeElement.getAttribute('aria-label')") == "Type to search..."
    expect(status).to_be_hidden()
