"""A CTO opens the risk register and follows every "Touches" entry.

Goal: see what each risk would hit, and trust that each entry is the real
application (same name, owner and cost as everywhere else), not a dead end.
Data: four risks over a linked portfolio (one on one application, one on two,
one linked twice, one linked to nothing) and a second organisation with a
look-alike application and its own risk.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import sign_in
from .linked_portfolio import linked_world  # noqa: F401  (the one shared fixture)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]


def _signed_in(browser, live_server, world, role="enterprise_architect"):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, world["org"]["emails"][role])
    return context, page


def _row(page, base, title):
    page.goto(base + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    return page.locator("[data-risk-row]", has_text=title)


def test_each_application_a_risk_touches_opens_the_real_application(browser, live_server, linked_world):
    mine, other = linked_world["mine"], linked_world["other"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        for key in ("billing", "ledger", "gateway"):
            risk = mine["risks"][key]
            for app_key in risk["apps"]:
                application = mine["apps"][app_key]
                row = _row(page, live_server, risk["title"])
                expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
                touches = row.locator("p", has_text="Touches:")
                links = touches.locator('a[href*="/applications/%d/fact-sheet"]' % application["id"])
                assert links.count() >= 1, "%s does not list %s" % (risk["title"], application["name"])
                expect(links.first).to_have_text(application["name"])
                links.first.click()
                page.wait_for_url(re.compile(r"/applications/%d/fact-sheet" % application["id"]),
                                  timeout=PAGE_TIMEOUT)
                body = page.locator("main, #main-content").first
                # The landing page is that application, with the owner and cost the list shows.
                expect(page.get_by_role("heading", level=1)).to_contain_text(application["name"], timeout=PAGE_TIMEOUT)
                expect(body).to_contain_text(application["owner"])
                expect(body).to_contain_text(application["cost_text"])
                # And it lists this very risk back, so the link is two-way.
                expect(body).to_contain_text(risk["title"])
        # Nothing from the other organisation is on the register.
        page.goto(live_server + "/risks/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        text = page.locator("body").inner_text()
        assert other["risks"]["secret"]["title"] not in text
        assert "Zed Outsider" not in text and "Competitor Ltd" not in text
    finally:
        context.close()


@pytest.mark.xfail(strict=True, reason=(
    "A risk linked to an application and also holding that application element lists the "
    "application twice in its Touches line."))
def test_a_risk_linked_twice_to_the_same_application_lists_it_once(browser, live_server, linked_world):
    mine = linked_world["mine"]
    risk = mine["risks"]["gateway"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        row = _row(page, live_server, risk["title"])
        links = row.locator("p", has_text="Touches:").get_by_role(
            "link", name=mine["apps"]["gateway"]["name"], exact=True)
        assert links.count() == 1, "%s lists %s %d times" % (
            risk["title"], mine["apps"]["gateway"]["name"], links.count())
    finally:
        context.close()


@pytest.mark.xfail(strict=True, reason=(
    "A risk linked to two applications says no linked elements are recorded and shows a bare dash for "
    "how far it reaches, although its Touches line names both."))
def test_a_linked_risk_never_says_nothing_is_linked(browser, live_server, linked_world):
    risk = linked_world["mine"]["risks"]["ledger"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        row = _row(page, live_server, risk["title"])
        expect(row.locator("p", has_text="Touches:")).to_be_visible(timeout=PAGE_TIMEOUT)
        text = row.inner_text()
        assert "no linked elements recorded" not in text, text
        assert "Reaches —" not in text, text
    finally:
        context.close()


@pytest.mark.xfail(strict=True, reason=(
    "A risk linked to nothing shows its own name as what it touches and an unexplained dash for how far "
    "it reaches, instead of saying it is not linked and offering to link it."))
def test_a_risk_linked_to_nothing_says_so_and_offers_the_next_step(browser, live_server, linked_world):
    loose = linked_world["mine"]["risks"]["loose"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        row = _row(page, live_server, loose["title"])
        expect(row).to_be_visible(timeout=PAGE_TIMEOUT)
        text = row.inner_text()
        # No bare dash standing in for an answer, and no entry that is the risk itself.
        assert "Reaches —" not in text, text
        assert "Touches:" not in text, text
        expect(row).to_contain_text(re.compile(r"not linked|no linked|could not be worked out", re.I))
        row.get_by_role("button", name=re.compile("Link it|Map", re.I)).first.click()
        expect(page.get_by_label("Search entities to link")).to_be_visible(timeout=PAGE_TIMEOUT)
    finally:
        context.close()


@pytest.mark.xfail(strict=True, reason=(
    "A risk lists its own record as something it touches, and that entry opens an impact page that says "
    "the element has no relationships."))
def test_every_touches_entry_of_every_risk_lands_on_a_page_with_content(browser, live_server, linked_world):
    """Click all of them, not only the applications: an element entry must open
    its impact page with something drawn, never the empty state."""
    mine = linked_world["mine"]
    context, page = _signed_in(browser, live_server, linked_world)
    try:
        for key in ("billing", "ledger", "gateway"):
            title = mine["risks"][key]["title"]
            row = _row(page, live_server, title)
            names = row.locator("p", has_text="Touches:").locator("a").all_inner_texts()
            assert names, "%s lists nothing it touches" % title
            for name in names:
                row = _row(page, live_server, title)
                row.locator("p", has_text="Touches:").get_by_role("link", name=name, exact=True).click()
                page.wait_for_load_state("domcontentloaded")
                body = page.locator("body")
                expect(body).not_to_contain_text("Page not found")
                expect(body).not_to_contain_text("Something went wrong")
                if "/impact" in page.url:
                    expect(page.locator("#impact-counts")).not_to_contain_text("Loading", timeout=PAGE_TIMEOUT)
                    expect(page.locator("#impact-empty")).to_be_hidden()
                else:
                    expect(body).to_contain_text(name, timeout=PAGE_TIMEOUT)
    finally:
        context.close()
