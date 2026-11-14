"""Criterion 2: entelim.tech is the canonical origin on every page, sitemap and feed.

With PUBLIC_BASE_URL empty (defaults to https://entelim.tech) and with it set
to https://entelim.tech, GET / and one early-access page on host entelim.org
and on host entelim.tech carry <link rel="canonical">, og:url and the
structured-data url on https://entelim.tech; /sitemap.xml lists only
https://entelim.tech addresses; /robots.txt names https://entelim.tech/sitemap.xml.

Red at the line tip (no origin helper, hard-coded entelim.org), green at head.
"""

from __future__ import annotations

import re
from contextlib import contextmanager

import pytest

from app.services.origin import canonical_origin


@contextmanager
def _set_base(app, value: str | None):
    """Override PUBLIC_BASE_URL for one test, then restore it."""
    before = app.config.get("PUBLIC_BASE_URL")
    app.config["PUBLIC_BASE_URL"] = value or ""
    try:
        yield
    finally:
        app.config["PUBLIC_BASE_URL"] = before


@pytest.fixture
def base_empty(app):
    with _set_base(app, None):
        yield


@pytest.fixture
def base_tech(app):
    with _set_base(app, "https://entelim.tech"):
        yield


def _page_checks(html: str, base: str) -> None:
    """Assert canonical link and og:url use base (homepage also has JSON-LD)."""
    assert f'<link rel="canonical" href="{base}/' in html, "missing canonical link on base"
    assert f'og:url" content="{base}' in html or f'property="og:url" content="{base}' in html, "missing og:url on base"


def _homepage_checks(html: str, base: str) -> None:
    """Homepage additionally carries JSON-LD structured-data url on base."""
    _page_checks(html, base)
    assert f'"url": "{base}' in html, "missing structured-data url on base"


def test_homepage_canonical_is_entelim_tech_when_base_empty(client, app, base_empty):
    """PUBLIC_BASE_URL empty -> canonical origin defaults to https://entelim.tech."""
    assert canonical_origin() == "https://entelim.tech"
    html = client.get("https://entelim.tech/").data.decode()
    _homepage_checks(html, "https://entelim.tech")
    html = client.get("https://entelim.org/").data.decode()
    _homepage_checks(html, "https://entelim.tech")


def test_homepage_canonical_is_entelim_tech_when_base_set(client, app, base_tech):
    """PUBLIC_BASE_URL=https://entelim.tech -> canonical origin is https://entelim.tech."""
    assert canonical_origin() == "https://entelim.tech"
    html = client.get("https://entelim.tech/").data.decode()
    _homepage_checks(html, "https://entelim.tech")
    html = client.get("https://entelim.org/").data.decode()
    _homepage_checks(html, "https://entelim.tech")


def test_early_access_page_canonical_is_entelim_tech(client, app, base_tech):
    """Early-access page carries entelim.tech canonical on either host."""
    assert canonical_origin() == "https://entelim.tech"
    html = client.get("https://entelim.tech/early-access").data.decode()
    _page_checks(html, "https://entelim.tech")
    html = client.get("https://entelim.org/early-access").data.decode()
    _page_checks(html, "https://entelim.tech")


def test_sitemap_lists_only_entelim_tech_addresses(client, app, base_tech):
    """sitemap.xml lists only https://entelim.tech addresses."""
    assert canonical_origin() == "https://entelim.tech"
    xml = client.get("https://entelim.tech/sitemap.xml").data.decode()
    locs = re.findall(r"<loc>(https://[^<]+)</loc>", xml)
    assert locs, "sitemap.xml has no loc entries"
    for loc in locs:
        assert loc.startswith("https://entelim.tech"), f"sitemap lists non-entelim.tech address: {loc}"


def test_robots_names_entelim_tech_sitemap(client, app, base_tech):
    """robots.txt names https://entelim.tech/sitemap.xml."""
    assert canonical_origin() == "https://entelim.tech"
    text = client.get("https://entelim.tech/robots.txt").data.decode()
    assert "Sitemap: https://entelim.tech/sitemap.xml" in text, (
        "robots.txt does not name entelim.tech/sitemap.xml"
    )
