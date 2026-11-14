"""The site's canonical address is https://entelim.tech on every public page,
sitemap and feed, whatever host a request arrives on.

Run twice: once with PUBLIC_BASE_URL empty (the helper defaults to
https://entelim.tech) and once with PUBLIC_BASE_URL=https://entelim.tech.
Both runs must produce the same entelim.tech addresses.
"""

import json
import re

import pytest

from app.services.origin import canonical_origin


def _canonical(html: str) -> str:
    match = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    assert match is not None, "page renders no canonical link"
    return match.group(1)


def _og_url(html: str) -> str:
    match = re.search(r'<meta property="og:url" content="([^"]+)"', html)
    assert match is not None, "page renders no og:url"
    return match.group(1)


def _jsonld_urls(html: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
    ):
        try:
            block = json.loads(match.group(1))
        except ValueError:
            continue
        nodes = block if isinstance(block, list) else [block]
        for node in nodes:
            if isinstance(node, dict) and isinstance(node.get("url"), str):
                urls.append(node["url"])
    return urls


def _get(path: str, client, host: str):
    return client.get(
        path,
        headers={"Host": host},
        environ_overrides={"wsgi.url_scheme": "https"},
    )


CANONICAL = "https://entelim.tech"


@pytest.fixture(params=["", CANONICAL], ids=["base-empty", "base-set"], autouse=True)
def configured_base(app, request):
    """Run every test with PUBLIC_BASE_URL empty and with it set to the canonical address."""
    before = app.config.get("PUBLIC_BASE_URL")
    app.config["PUBLIC_BASE_URL"] = request.param
    try:
        with app.app_context():
            assert canonical_origin() == CANONICAL
        yield
    finally:
        app.config["PUBLIC_BASE_URL"] = before


@pytest.mark.parametrize("path", ["/", "/early-access"])
@pytest.mark.parametrize("host", ["entelim.org", "entelim.tech"])
def test_public_page_canonical_address_is_entelim_tech(app, client, path, host):
    """A visitor on entelim.org sees the same entelim.tech canonical address
    as a visitor on entelim.tech: the page never adopts the request's host."""
    response = _get(path, client, host)
    assert response.status_code == 200, (path, host, response.status_code)
    html = response.data.decode()
    expected = f"{CANONICAL}{path if path != '/' else '/'}"
    assert _canonical(html) == expected, (path, host, _canonical(html))
    assert _og_url(html) == expected, (path, host, _og_url(html))
    for url in _jsonld_urls(html):
        assert url.startswith(CANONICAL), (path, host, url)


def test_sitemap_lists_only_entelim_tech_addresses(app, client):
    response = _get("/sitemap.xml", client, "entelim.org")
    assert response.status_code == 200
    xml = response.data.decode()
    origins = re.findall(r"<loc>(https://[^/]+)", xml)
    assert origins, "sitemap renders no loc entries"
    assert origins == [CANONICAL] * len(origins), origins


def test_robots_names_entelim_tech_sitemap(app, client):
    response = _get("/robots.txt", client, "entelim.org")
    assert response.status_code == 200
    assert f"Sitemap: {CANONICAL}/sitemap.xml" in response.data.decode()
