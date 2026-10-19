"""The public trust pages: data processing agreement, sub-processors, acceptable use.

Each page answers 200 signed out, is listed in the sitemap and the footer, and
carries none of the internal wording the public pages must not show. The
sub-processor list names every outside service the code can send customer
content or personal data to.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TRUST_SLUGS = ["data-processing-agreement", "sub-processors", "acceptable-use"]

NOT_ON_A_PUBLIC_PAGE = [
    ".py", "app/", "docs/", "TBC", "ASSUMPTION", "VERIFIED", "PLANNING", "PROPOSED",
    "certified", "ISO 27001", "SOC 2", "SOC2", "penetration",
]

# Every outside service the code can send content or personal data to. Each
# name must appear on /sub-processors.
PROVIDERS = [
    "DigitalOcean",
    "OpenAI",
    "Anthropic",
    "Google Gemini",
    "Microsoft Azure",
    "DeepSeek",
    "OpenRouter",
    "SendGrid",
    "Raygun",
    "PostHog",
    "Google Analytics",
    "Microsoft Clarity",
]


def _text(html: str) -> str:
    body = html.split("public-page-content", 1)[1].split("</article>", 1)[0]
    return re.sub(r"<[^>]+>", " ", body)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.mark.parametrize("slug", TRUST_SLUGS)
def test_each_trust_page_is_served(client, slug):
    assert client.get("/" + slug).status_code == 200


@pytest.mark.parametrize("slug", TRUST_SLUGS)
def test_each_trust_page_is_in_the_sitemap_and_footer(client, slug):
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/" + slug + "</loc>" in sitemap
    footer_page = client.get("/terms").get_data(as_text=True)
    assert 'href="/' + slug + '"' in footer_page


@pytest.mark.parametrize("slug", TRUST_SLUGS)
def test_no_internal_wording_on_a_trust_page(client, slug):
    html = client.get("/" + slug).get_data(as_text=True)
    text = _text(html)
    for banned in NOT_ON_A_PUBLIC_PAGE:
        assert banned not in text, slug + ": " + repr(banned)
    assert "page_role" not in html and "page_family" not in html


def test_sub_processors_lists_every_provider_the_code_can_call(client):
    text = _text(client.get("/sub-processors").get_data(as_text=True))
    for name in PROVIDERS:
        assert name in text, name
    assert "Where the assistant sends your content" in text
    assert "approval" in text


def test_the_agreement_points_at_the_list_and_the_contact_form(client):
    html = client.get("/data-processing-agreement").get_data(as_text=True)
    assert 'href="/sub-processors"' in html
    assert 'href="/contact"' in html
    assert "@" not in _text(html)


def test_features_no_longer_says_proven(client):
    html = client.get("/features").get_data(as_text=True)
    assert "proven tenant isolation" not in html
    assert "tenant isolation enforced in the database" in html


def test_the_health_check_link_to_the_agreement_resolves(client):
    html = client.get("/architecture-health-check").get_data(as_text=True)
    assert 'href="/data-processing-agreement"' in html
    assert client.get("/data-processing-agreement").status_code == 200


def test_terms_and_privacy_are_still_served(client):
    assert client.get("/terms").status_code == 200
    assert client.get("/privacy").status_code == 200


def test_robots_allows_the_trust_pages():
    robots = (Path(__file__).resolve().parent.parent / "app/static/robots.txt").read_text()
    for slug in TRUST_SLUGS:
        assert "Allow: /" + slug in robots
