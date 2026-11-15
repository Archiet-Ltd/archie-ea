"""A buyer can read every legal page before paying, once the pages are switched on.

Boots its own server with LEGAL_PAGES_ENABLED=true so the flag is on here and
nowhere else in the smoke session. Signed out, each page renders in the public
layout and the footer reaches all six legal pages by clicking. Signed in as an
organisation administrator, choosing a plan on the billing page opens its
checkout step, and each legal link in it opens a page that renders.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server
from .test_archetype_journeys import _login

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PAGES = {
    "terms": "Terms",
    "privacy": "Privacy",
    "data-processing-agreement": "Data processing agreement",
    "cookie-policy": "Cookie policy",
    "refund-policy": "Refund policy",
    "commercial-licence": "Commercial licence",
}


@pytest.fixture(scope="module")
def legal_server(request, ai_protocol_stub, app):
    return boot_live_server(request, ai_protocol_stub, app, extra_env={"LEGAL_PAGES_ENABLED": "true"})


@pytest.mark.parametrize("slug", ["data-processing-agreement", "cookie-policy", "refund-policy", "commercial-licence"])
def test_each_new_legal_page_renders(page, legal_server, slug):
    response = page.goto(legal_server + "/" + slug, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    assert response.status == 200
    expect(page.locator("article.public-page-content h1")).to_have_text(PAGES[slug])
    expect(page.locator("article.public-page-content h2").first).to_be_visible()
    assert "Archiet Ltd" in page.inner_text("article.public-page-content")


def test_the_footer_reaches_every_legal_page(page, legal_server):
    page.goto(legal_server + "/terms", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    for slug, title in PAGES.items():
        link = page.locator("footer a[data-legal-link='%s']" % slug)
        expect(link).to_have_text(title)
        link.click()
        page.wait_for_url(legal_server + "/" + slug, timeout=PAGE_TIMEOUT)
        expect(page.locator("article.public-page-content h1")).to_have_text(title)


def test_the_checkout_panel_links_every_legal_page(page, legal_server, seeded):
    _login(page, legal_server, seeded["emails"]["platform_admin"])
    page.goto(legal_server + "/admin/billing/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    panel = page.locator("#checkout")
    expect(panel).to_have_count(0)
    page.get_by_test_id("plan-card-team").get_by_role("link", name="Choose Team").click()
    expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)

    notice = panel.locator("[data-checkout-legal]")
    expect(notice).to_be_visible()
    for slug, title in PAGES.items():
        link = notice.locator("a[data-legal-link='%s']" % slug)
        expect(link).to_have_text(title)
        with page.context.expect_page() as opened:
            link.click()
        legal_page = opened.value
        legal_page.wait_for_load_state("domcontentloaded")
        assert legal_page.url == legal_server + "/" + slug
        expect(legal_page.locator("article.public-page-content h1")).to_have_text(title)
        legal_page.close()


def test_a_buyer_finds_which_ai_providers_get_their_content(page, legal_server):
    """From the data agreement, the sub-processor link lands on the list naming each provider."""
    page.goto(legal_server + "/data-processing-agreement", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    agreement = page.locator("article.public-page-content")
    expect(agreement).to_contain_text("This does not apply to the AI features")
    expect(agreement).not_to_contain_text("Each sub-processor is bound by written terms")

    agreement.get_by_role("link", name="sub-processor list").first.click()
    page.wait_for_url(legal_server + "/privacy#sub-processors", timeout=PAGE_TIMEOUT)
    heading = page.locator("h2#sub-processors")
    expect(heading).to_be_visible()
    rows = heading.locator("xpath=following-sibling::table[1]//tbody/tr")
    assert rows.count() == 3
    table = rows.first.locator("xpath=ancestor::table").inner_text()
    for needle in ("OpenRouter", "NVIDIA", "DeepSeek", "United States", "People's Republic of China"):
        assert needle in table, needle
    privacy = page.locator("article.public-page-content").inner_text().lower()
    assert "no data processing agreement" in privacy
    assert "train" in privacy
    assert "no data from your organisation's model is sent to a third party" not in privacy
