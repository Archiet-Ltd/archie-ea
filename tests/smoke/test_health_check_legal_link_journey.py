"""A buyer reading the health check page is only pointed at legal pages that exist.

Signed out. With the held legal pages off, the confidentiality sentence offers a
mutual NDA and names no agreement; every link in the article opens a page with
content. With them on, the same sentence links the data processing agreement and
the link opens it.
"""

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT, boot_live_server

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

PATH = "/architecture-health-check"
ARTICLE = "article.public-page-content"


@pytest.fixture(scope="module")
def flag_off_server(request, ai_protocol_stub, app):
    return boot_live_server(request, ai_protocol_stub, app, extra_env={"LEGAL_PAGES_ENABLED": "false"})


@pytest.fixture(scope="module")
def flag_on_server(request, ai_protocol_stub, app):
    return boot_live_server(request, ai_protocol_stub, app, extra_env={"LEGAL_PAGES_ENABLED": "true"})


def _article_paths(page):
    hrefs = page.locator(ARTICLE + " a[href^='/']").evaluate_all("els => els.map(e => e.getAttribute('href'))")
    return sorted({h.split("#")[0] for h in hrefs if not h.startswith("//")})


def test_flag_off_the_page_offers_an_nda_and_every_link_opens_a_page(page, flag_off_server):
    page.goto(flag_off_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    article = page.locator(ARTICLE)
    expect(article).to_contain_text("bound by a duty of confidentiality")
    expect(article).to_contain_text("mutual NDA")
    expect(article).not_to_contain_text("data processing agreement")
    assert "/data-processing-agreement" not in _article_paths(page)

    paths = _article_paths(page)
    assert "/privacy" in paths
    for path in paths:
        response = page.goto(flag_off_server + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert response.status == 200, path
        expect(page.locator("h1").first).to_be_visible()


def test_flag_on_the_agreement_link_opens_the_agreement(page, flag_on_server):
    page.goto(flag_on_server + PATH, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator(ARTICLE).get_by_role("link", name="data processing agreement").click()
    page.wait_for_url(flag_on_server + "/data-processing-agreement", timeout=PAGE_TIMEOUT)
    expect(page.locator(ARTICLE + " h1")).to_have_text("Data processing agreement")
    expect(page.locator(ARTICLE + " h2").first).to_be_visible()
