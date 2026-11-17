"""A public page published in English, pt-BR, es or fr declares its language, names its translations, carries its
own canonical address, and is listed in the sitemap of its language."""

import re
import xml.etree.ElementTree as ET

import pytest

from app.services import public_pages
from app.services.origin import canonical_origin

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
LANGUAGES = {"pt-BR": "/pt-br", "es": "/es", "fr": "/fr"}


def _write(path, lang=None, source=None, title="Page"):
    head = ["---", "wave: 1"]
    if lang:
        head += [f"lang: {lang}", f"translation_of: {source}"]
    head += ["---", "", f"# {title}", "", f"Words for {title}.", ""]
    path.write_text("\n".join(head), encoding="utf-8")


@pytest.fixture()
def content(tmp_path, monkeypatch):
    """One English page translated into every language, and one English page nobody has translated."""
    site = tmp_path / "site"
    site.mkdir()
    _write(site / "about.md", title="About")
    _write(site / "security.md", title="Security")
    for code in LANGUAGES:
        _write(site / f"about.{code.lower()}.md", lang=code, source="about", title=f"About {code}")
    monkeypatch.setattr(public_pages, "CONTENT_ROOT", tmp_path)
    return tmp_path


def _html(client, path):
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    return response.data.decode()


def _alternates(html):
    return dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)"', html))


def _canonical(html):
    return re.search(r'<link rel="canonical" href="([^"]+)"', html).group(1)


def _lang(html):
    return re.search(r"<html lang=\"([^\"]+)\"", html).group(1)


def test_english_and_translated_pages_name_each_other(app, client, content):
    origin = canonical_origin()
    expected = {
        "en": f"{origin}/about",
        "pt-BR": f"{origin}/pt-br/about",
        "es": f"{origin}/es/about",
        "fr": f"{origin}/fr/about",
        "x-default": f"{origin}/about",
    }
    english = _html(client, "/about")
    assert _lang(english) == "en"
    assert _alternates(english) == expected
    assert _canonical(english) == f"{origin}/about"

    for code, prefix in LANGUAGES.items():
        translated = _html(client, f"{prefix}/about")
        assert _lang(translated) == code
        assert _alternates(translated) == expected
        assert _canonical(translated) == f"{origin}{prefix}/about"
        assert f"About {code}" in translated


def test_sitemap_is_an_index_with_one_sitemap_per_language(app, client, content):
    origin = canonical_origin()
    index = ET.fromstring(client.get("/sitemap.xml").data)
    assert index.tag == f"{SITEMAP_NS}sitemapindex"
    named = [loc.text for loc in index.iter(f"{SITEMAP_NS}loc")]
    assert named == [f"{origin}/sitemap-{code}.xml" for code in ("en", "pt-br", "es", "fr")]

    for code, prefix in (("pt-br", "/pt-br"), ("es", "/es"), ("fr", "/fr")):
        response = client.get(f"/sitemap-{code}.xml")
        assert response.status_code == 200
        urls = [loc.text for loc in ET.fromstring(response.data).iter(f"{SITEMAP_NS}loc")]
        assert urls == [f"{origin}{prefix}/about"]

    english = [loc.text for loc in ET.fromstring(client.get("/sitemap-en.xml").data).iter(f"{SITEMAP_NS}loc")]
    assert f"{origin}/about" in english
    assert not any(url.startswith((f"{origin}/pt-br", f"{origin}/es", f"{origin}/fr")) for url in english)

    assert f"Sitemap: {origin}/sitemap.xml" in client.get("/robots.txt").data.decode()


def test_sitemap_lists_only_languages_that_have_pages(app, client, tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    _write(site / "about.md", title="About")
    _write(site / "about.es.md", lang="es", source="about", title="Sobre")
    monkeypatch.setattr(public_pages, "CONTENT_ROOT", tmp_path)
    named = [loc.text for loc in ET.fromstring(client.get("/sitemap.xml").data).iter(f"{SITEMAP_NS}loc")]
    assert named == [f"{canonical_origin()}/sitemap-en.xml", f"{canonical_origin()}/sitemap-es.xml"]
    assert client.get("/sitemap-fr.xml").status_code == 404


def test_page_without_a_translation_shows_no_alternates(app, client, content):
    html = _html(client, "/security")
    assert _alternates(html) == {}
    assert "hreflang" not in html
    assert _lang(html) == "en"
    assert _canonical(html) == f"{canonical_origin()}/security"


def test_translation_of_a_missing_page_is_not_served(app, client, content):
    _write(content / "site" / "ghost.fr.md", lang="fr", source="ghost", title="Fantome")
    assert client.get("/fr/ghost").status_code == 404
    assert client.get("/fr/about").status_code == 200
