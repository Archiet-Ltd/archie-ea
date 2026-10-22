"""A CTO lands on the overview: every link takes them somewhere useful and the
figures on the cards are the figures on the pages they open.

Data: an established organisation (six applications, owners, costs, a mapped
capability, a solution, four risks) beside an organisation that has only just
been created and holds nothing.
"""
import re

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in
from .linked_portfolio import linked_world  # noqa: F401  (the one shared fixture)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

_ERROR_WORDS = re.compile(r"page not found|something went wrong|internal server error|traceback|forbidden", re.I)


def _open(browser, live_server, email, width=1440):
    context = browser.new_context(ignore_https_errors=True, viewport={"width": width, "height": 1000})
    context.add_init_script("localStorage.setItem('archie_onboarding_ts', Date.now().toString());")
    page = context.new_page()
    sign_in(page, live_server, email)
    return context, page


def _lands_on_content(page, label):
    """The landing page has a heading, real text and no error."""
    page.wait_for_load_state("domcontentloaded")
    heading = page.get_by_role("heading", level=1).first
    expect(heading, "%s: no page heading at %s" % (label, page.url)).to_be_visible(timeout=PAGE_TIMEOUT)
    assert heading.inner_text().strip(), "%s: empty heading at %s" % (label, page.url)
    body = " ".join(page.locator("#main-content, main").first.inner_text().split())
    assert not _ERROR_WORDS.search(body), "%s: error text at %s: %s" % (label, page.url, body[:200])
    assert len(body) > 120, "%s: nearly empty page at %s: %r" % (label, page.url, body)


def test_every_workspace_link_lands_on_a_page_with_content(browser, live_server, linked_world):
    context, page = _open(browser, live_server, linked_world["org"]["emails"]["cto"])
    try:
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        nav = page.get_by_role("navigation", name="Your Workspace")
        expect(nav).to_be_visible(timeout=PAGE_TIMEOUT)
        links = nav.get_by_role("link")
        found = [(links.nth(i).inner_text().strip(), links.nth(i).get_attribute("href"))
                 for i in range(links.count())]
        assert len(found) >= 3, "the workspace row holds too few links: %r" % found
        for label, href in found:
            page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.get_by_role("navigation", name="Your Workspace").get_by_role("link", name=label, exact=True).click()
            _lands_on_content(page, label)
    finally:
        context.close()


@pytest.mark.xfail(strict=True, reason=(
    "Portfolio KPIs in the workspace row opens a page headed Executive Rationalization Scorecard, "
    "so the link and the page it opens do not carry the same name."))
def test_each_workspace_link_opens_a_page_headed_with_the_name_it_carries(browser, live_server, linked_world):
    context, page = _open(browser, live_server, linked_world["org"]["emails"]["cto"])
    try:
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        links = page.get_by_role("navigation", name="Your Workspace").get_by_role("link")
        labels = [links.nth(i).inner_text().strip() for i in range(links.count())]
        mismatches = []
        for label in labels:
            page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.get_by_role("navigation", name="Your Workspace").get_by_role("link", name=label, exact=True).click()
            page.wait_for_load_state("domcontentloaded")
            heading = page.get_by_role("heading", level=1).first.inner_text().strip()
            words = re.findall(r"[a-z]{3,}", label.lower())
            if not any(w[:4] in heading.lower() or w[:4] in page.title().lower() for w in words):
                mismatches.append("%r opens a page headed %r" % (label, heading))
        assert not mismatches, "; ".join(mismatches)
    finally:
        context.close()


def test_card_figures_equal_the_figures_on_the_pages_they_open(browser, live_server, linked_world):
    mine = linked_world["mine"]
    context, page = _open(browser, live_server, linked_world["org"]["emails"]["cto"])
    try:
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        applications = page.get_by_test_id("health-kpi-applications").inner_text().strip()
        solutions = page.get_by_test_id("health-kpi-solutions").inner_text().strip()
        elements = page.get_by_test_id("health-kpi-elements").inner_text().strip()
        assert applications == str(len(mine["apps"])), "overview says %s applications" % applications
        assert solutions == "1", "overview says %s solutions" % solutions
        page.get_by_test_id("layer-tab-overview").click()
        overview_row = page.get_by_test_id("architecture-overview").inner_text()
        assert re.search(r"\b%s elements" % re.escape(elements), overview_row), (elements, overview_row)

        # The same count where the applications are listed, row for row.
        page.goto(live_server + "/applications/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        badge = page.get_by_test_id("total-count-badge").inner_text()
        assert re.match(r"%s shown" % applications, badge), badge
        rows = page.locator("[data-testid^='app-row-']")
        assert rows.count() == int(applications)
        for application in mine["apps"].values():
            expect(page.locator("[data-testid='app-row-%d']" % application["id"])).to_contain_text(application["name"])

        # The solution counted on the card is on the solutions page.
        page.goto(live_server + "/solutions/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        expect(page.locator("#main-content, main").first).to_contain_text(
            "%s Payments modernisation" % linked_world["noun"], timeout=PAGE_TIMEOUT)

        # The capability row's View link opens the capability, which is named there.
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("layer-tab-overview").click()
        widget = page.get_by_test_id("capability-health-widget")
        expect(widget).to_contain_text(mine["capability"])
        row = widget.locator("tr", has_text=mine["capability"])
        expect(row.locator("td").nth(1)).to_have_text("1")
        row.hover()
        row.get_by_role("link", name="View").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("#main-content, main").first).to_contain_text(mine["capability"], timeout=PAGE_TIMEOUT)
        _lands_on_content(page, "capability view")

        # The pipeline link and the composer link land on pages with content.
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("layer-tab-overview").click()
        page.get_by_role("link", name="Open the pipeline").click()
        _lands_on_content(page, "pipeline")
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        page.get_by_test_id("layer-tab-overview").click()
        page.get_by_test_id("architecture-overview").get_by_role("link", name="Open Composer").click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.locator("body")).not_to_contain_text("Page not found")
    finally:
        context.close()


def test_each_cto_card_opens_the_page_that_holds_its_figure(browser, live_server, linked_world):
    mine = linked_world["mine"]
    context, page = _open(browser, live_server, linked_world["org"]["emails"]["cto"])
    try:
        for testid, path, figure in [
            ("cto-kpi-applications", r"/applications/?$", str(len(mine["apps"]))),
            ("cto-health-score-value", r"/dashboard/health", None),
            ("cto-kpi-solutions", r"/solutions/?$", "1"),
            ("cto-kpi-elements", r"/architecture/dashboard", None),
        ]:
            page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.get_by_test_id("layer-tab-cto").click()
            card = page.get_by_test_id(testid)
            shown = card.inner_text().strip()
            assert shown not in ("", "0", "\u2014"), "%s reads %r" % (testid, shown)
            card.click()
            page.wait_for_url(re.compile(path), timeout=PAGE_TIMEOUT)
            _lands_on_content(page, testid)
            if figure:
                assert shown == figure, "%s reads %s, expected %s" % (testid, shown, figure)
            if testid == "cto-kpi-elements":
                # The architecture dashboard counts elements by layer; the layers add up to the card.
                expect(page.locator("#main-content, main").first).to_contain_text(
                    re.compile(r"Implementation\s+\d+"), timeout=PAGE_TIMEOUT)
                text = page.locator("#main-content, main").first.inner_text()
                layers = dict(re.findall(
                    r"(Motivation|Strategy|Business|Application|Technology|Physical|Implementation)\s*\n\s*(\d+)",
                    text))
                assert len(layers) == 7, "layer counts not found on the page: %r" % layers
                assert sum(int(n) for n in layers.values()) == int(shown), (
                    "the card says %s elements, the layers add up to %r" % (shown, layers))
    finally:
        context.close()


def test_a_new_organisation_is_given_a_next_step_not_zeros(browser, live_server):
    sparse = create_fresh_org("cto")
    context, page = _open(browser, live_server, sparse["emails"]["cto"])
    try:
        page.goto(live_server + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        setup = page.get_by_test_id("guided-setup")
        expect(setup).to_be_visible(timeout=PAGE_TIMEOUT)
        # No key figures standing at zero or as a dash.
        expect(page.get_by_test_id("health-kpi-applications")).to_have_count(0)
        steps = setup.locator("li a")
        hrefs = [(steps.nth(i).inner_text().strip(), steps.nth(i).get_attribute("href"))
                 for i in range(steps.count())]
        assert hrefs, "no next step is offered"
        for label, href in hrefs:
            page.goto(live_server + href, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            _lands_on_content(page, "step %r" % label)
    finally:
        context.close()
