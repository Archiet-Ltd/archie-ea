"""The Impact Analysis page answers "what breaks if this fails" -- the same
answer, from the same records, the AI chat gives for the same question.

Replaces a page that offered only an element-type dropdown over a table of
recorded analysis runs (always empty on a new account, so four KPI cards read
zero above a model with real connections). Launch-bar line 4 (every promise
delivered) and line 5 (sign-up to first answer): the sidebar's own job label
for this entry is "What breaks if this fails" (navigation registry, job 2),
and the page could not answer it.
"""

import os
import re

import pytest
from playwright.sync_api import expect

from .chat_ask import ask, open_chat
from .conftest import PAGE_TIMEOUT
from .fresh_org import api, create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGE = "/enterprise/analysis/impact-analysis"
ASKED_ELEMENT = "Order management system"

_SHOT_DIR = os.environ.get("JOURNEY_SHOT_DIR")


def _shot(page, name):
    """Evidence for the report, only when JOURNEY_SHOT_DIR is set; a no-op otherwise."""
    if not _SHOT_DIR:
        return
    os.makedirs(_SHOT_DIR, exist_ok=True)
    page.screenshot(path=os.path.join(_SHOT_DIR, name + ".png"), full_page=True)


def _load_sample_company(page):
    status, body = api(page, "POST", "/architecture/sample-company/load")
    assert status in (200, 201), (status, body)


def _open_from_sidebar(page, base):
    page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    toggle = page.locator('[data-nav-toggle="impact"]')
    if toggle.get_attribute("aria-expanded") != "true":
        toggle.click()
    page.get_by_role("link", name="Impact analysis", exact=True).first.click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Impact Analysis", exact=True)).to_be_visible(timeout=PAGE_TIMEOUT)


def _choose_element(page, name):
    picker = page.locator('#impact-page-picker-input')
    picker.fill(name)
    row = page.get_by_role("option", name=name, exact=True)
    expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
    row.click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)


def _page_element_names(page):
    return sorted(t.strip() for t in page.get_by_test_id("impact-answer-element-link").all_inner_texts())


def _chat_element_names(answer_card):
    return sorted(t.strip() for t in answer_card.locator("[data-answer-row] a").all_inner_texts())


def test_impact_page_matches_the_chat_and_a_reload_keeps_the_answer(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        _load_sample_company(page)

        # The chat's answer to the same question, first -- the reference the
        # page must agree with.
        question = "What breaks if the %s fails?" % ASKED_ELEMENT.lower()
        answer_card = ask(page, live_server, question)
        chat_names = _chat_element_names(answer_card)
        assert chat_names, "the chat answered with no connections; nothing to compare against"

        # Open the Impact Analysis page from the sidebar, as a person does --
        # never a dead end: an "Element Type" dropdown with nowhere to search.
        _open_from_sidebar(page, live_server)
        # One empty state, not a duplicate: the page has one call site for
        # whichever empty state applies, not one per branch.
        expect(page.get_by_text("Choose an element to see what depends on it")).to_have_count(1)
        _shot(page, "impact-page-no-selection-1440-light")

        _choose_element(page, ASKED_ELEMENT)
        expect(page.get_by_role("heading", name="What breaks if %s fails" % ASKED_ELEMENT, exact=True)).to_be_visible(
            timeout=PAGE_TIMEOUT
        )
        _shot(page, "impact-page-answer-1440-light")
        page_names = _page_element_names(page)
        assert page_names, "the page's answer listed no connected elements"
        assert page_names == chat_names, (
            "the page and the chat disagree on what depends on %s: page=%r chat=%r"
            % (ASKED_ELEMENT, page_names, chat_names)
        )

        # Every row links to its own record (the Composer), not a dead end.
        first_link = page.get_by_test_id("impact-answer-element-link").first
        href = first_link.get_attribute("href")
        assert href and href.startswith("/"), href

        # A reload with ?element=<id> shows the same answer -- the URL contract
        # a link from anywhere (a chat answer, an application's page) relies on.
        url = page.url
        assert re.search(r"[?&]element=\d+", url), url
        page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_role("heading", name="What breaks if %s fails" % ASKED_ELEMENT, exact=True)).to_be_visible(
            timeout=PAGE_TIMEOUT
        )
        assert _page_element_names(page) == chat_names
    finally:
        context.close()


def test_impact_page_at_390_dark_keeps_the_answer_readable(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark")
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        _load_sample_company(page)
        page.goto(live_server + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _choose_element(page, ASKED_ELEMENT)
        expect(page.get_by_role("heading", name="What breaks if %s fails" % ASKED_ELEMENT, exact=True)).to_be_visible(
            timeout=PAGE_TIMEOUT
        )
        _shot(page, "impact-page-answer-390-dark")
        assert _page_element_names(page), "no connections shown at 390 wide"
        body_overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert body_overflow <= 1, "the page overflows horizontally at 390 wide (%spx)" % body_overflow
    finally:
        context.close()


def test_an_empty_organisation_sees_the_three_next_steps(browser, live_server):
    org = create_fresh_org("enterprise_architect")
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    context.set_default_timeout(PAGE_TIMEOUT)
    page = context.new_page()
    try:
        sign_in(page, live_server, org["emails"]["enterprise_architect"])
        page.goto(live_server + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("impact-first-steps")).to_be_visible(timeout=PAGE_TIMEOUT)
        expect(page.get_by_test_id("impact-load-sample")).to_be_visible()
        expect(page.get_by_test_id("impact-add-application")).to_be_visible()
        _shot(page, "impact-page-empty-organisation-1440-light")
    finally:
        context.close()


def test_an_element_of_another_organisation_is_not_found(browser, live_server):
    org_a = create_fresh_org("enterprise_architect")
    org_b = create_fresh_org("enterprise_architect")

    context_a = browser.new_context(viewport={"width": 1440, "height": 1000})
    context_a.set_default_timeout(PAGE_TIMEOUT)
    page_a = context_a.new_page()
    try:
        sign_in(page_a, live_server, org_a["emails"]["enterprise_architect"])
        _load_sample_company(page_a)
        page_a.goto(live_server + PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        _choose_element(page_a, ASKED_ELEMENT)
        element_id = re.search(r"[?&]element=(\d+)", page_a.url).group(1)
    finally:
        context_a.close()

    context_b = browser.new_context(viewport={"width": 1440, "height": 1000})
    context_b.set_default_timeout(PAGE_TIMEOUT)
    page_b = context_b.new_page()
    try:
        sign_in(page_b, live_server, org_b["emails"]["enterprise_architect"])
        page_b.goto(
            live_server + PAGE + ("?element=%s" % element_id),
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT,
        )
        expect(page_b.get_by_test_id("impact-element-not-found")).to_be_visible(timeout=PAGE_TIMEOUT)
        _shot(page_b, "impact-page-element-not-found-1440-light")
        assert ASKED_ELEMENT not in page_b.content(), "org B's page leaked org A's element name"
        assert page_b.get_by_test_id("impact-answer-element-link").count() == 0
    finally:
        context_b.close()
