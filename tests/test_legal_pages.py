"""Legal pages a buyer reads before paying.

The data processing agreement, cookie policy, refund policy and commercial
licence must stay unpublished until their text is approved: with the flag off
they answer 404 and nothing links to them. With it on they render in the
public page layout and the footer and checkout panel link to all six pages.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.services.legal_pages import AWAITING_APPROVAL, LEGAL_PAGES_FLAG, legal_links
from app.services.public_pages import load_all_pages

GATED = [slug for slug, _ in AWAITING_APPROVAL]
TITLES = {
    "data-processing-agreement": "Data processing agreement",
    "cookie-policy": "Cookie policy",
    "refund-policy": "Refund policy",
    "commercial-licence": "Commercial licence",
}


def _fresh_config_module(monkeypatch):
    """config.py evaluated now, with the flag absent from the environment."""
    monkeypatch.delenv(LEGAL_PAGES_FLAG, raising=False)
    path = Path(__file__).resolve().parent.parent / "config.py"
    spec = importlib.util.spec_from_file_location("_config_legal_default", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_flag_defaults_off_in_every_configuration(monkeypatch):
    config = _fresh_config_module(monkeypatch)
    for name in ("Config", "DevelopmentConfig", "TestingConfig", "ProductionConfig"):
        assert getattr(config, name).LEGAL_PAGES_ENABLED is False, name


@pytest.fixture
def flag_off(app, monkeypatch):
    monkeypatch.setitem(app.config, LEGAL_PAGES_FLAG, False)
    return app


@pytest.fixture
def flag_on(app, monkeypatch):
    monkeypatch.setitem(app.config, LEGAL_PAGES_FLAG, True)
    return app


@pytest.mark.parametrize("slug", GATED)
def test_unapproved_pages_are_not_served_while_the_flag_is_off(flag_off, slug):
    assert flag_off.test_client().get(f"/{slug}").status_code == 404


def test_nothing_links_to_unapproved_pages_while_the_flag_is_off(flag_off):
    client = flag_off.test_client()
    footer_page = client.get("/terms").get_data(as_text=True)
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    llms = client.get("/llms.txt").get_data(as_text=True)
    for slug in GATED:
        assert f'href="/{slug}"' not in footer_page
        assert f"/{slug}<" not in sitemap
        assert f"/{slug})" not in llms
    assert 'href="/terms"' in footer_page
    assert 'href="/privacy"' in footer_page
    with flag_off.app_context():
        assert [link["slug"] for link in legal_links()] == ["terms", "privacy"]
        assert not [p for p in load_all_pages() if p.slug in GATED]


@pytest.mark.parametrize("slug", GATED)
def test_each_page_renders_in_the_public_layout_when_the_flag_is_on(flag_on, slug):
    response = flag_on.test_client().get(f"/{slug}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f"<h1>{TITLES[slug]}</h1>" in html
    assert f"<title>{TITLES[slug]}" in html
    assert "public-page-content" in html
    assert "Archiet Ltd" in html
    # Front-matter never leaks into the page.
    assert "page_role" not in html and "page_family" not in html


def test_the_footer_links_every_legal_page_when_the_flag_is_on(flag_on):
    html = flag_on.test_client().get("/privacy").get_data(as_text=True)
    for slug in ["terms", "privacy", *GATED]:
        assert f'href="/{slug}"' in html, slug


def test_the_sitemap_and_llms_list_the_pages_when_the_flag_is_on(flag_on):
    client = flag_on.test_client()
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    llms = client.get("/llms.txt").get_data(as_text=True)
    for slug in GATED:
        assert f"/{slug}</loc>" in sitemap
        assert f"/{slug})" in llms


def test_every_link_the_footer_offers_is_served(flag_on):
    client = flag_on.test_client()
    with flag_on.app_context():
        links = legal_links()
    assert [link["slug"] for link in links] == ["terms", "privacy", *GATED]
    for link in links:
        assert client.get(link["href"]).status_code == 200, link["href"]


def test_every_template_sees_only_the_links_the_flag_allows(app, monkeypatch):
    """The footer and the checkout panel both read ``legal_links`` from here."""
    from flask import render_template_string

    snippet = '{% for link in legal_links %}<a href="{{ link.href }}">{{ link.label }}</a>{% endfor %}'
    everything = ["terms", "privacy", *GATED]
    for enabled, expected in ((False, ["terms", "privacy"]), (True, everything)):
        monkeypatch.setitem(app.config, LEGAL_PAGES_FLAG, enabled)
        with app.test_request_context("/admin/billing/"):
            rendered = render_template_string(snippet)
        assert [s for s in everything if f'href="/{s}"' in rendered] == expected
