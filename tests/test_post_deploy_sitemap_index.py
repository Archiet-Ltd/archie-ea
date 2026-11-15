"""The deploy check's page list follows the sitemap index to every page."""
from __future__ import annotations

import importlib.util
from pathlib import Path

NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("post_deploy_verify", ROOT / "scripts" / "post_deploy_verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _urlset(*addresses):
    return '<urlset xmlns="%s">%s</urlset>' % (NS, "".join("<url><loc>%s</loc></url>" % a for a in addresses))


def test_sitemap_urls_returns_every_page_of_every_child_sitemap(monkeypatch):
    module = _load()
    index = '<sitemapindex xmlns="%s"><sitemap><loc>https://site.example/sitemap-en.xml</loc></sitemap>' \
            '<sitemap><loc>https://site.example/sitemap-pt-br.xml</loc></sitemap></sitemapindex>' % NS
    answers = {
        "http://live.test/sitemap.xml": (200, index),
        "http://live.test/sitemap-en.xml": (200, _urlset("https://site.example/", "https://site.example/pricing")),
        "http://live.test/sitemap-pt-br.xml": (200, _urlset("https://site.example/pt-br/precos")),
    }
    monkeypatch.setattr(module, "_fetch", lambda url, timeout=30: answers.get(url, (404, "")))

    assert module.sitemap_urls("http://live.test/") == [
        "https://site.example/", "https://site.example/pricing", "https://site.example/pt-br/precos"]


def test_sitemap_urls_still_reads_a_plain_sitemap(monkeypatch):
    module = _load()
    monkeypatch.setattr(module, "_fetch", lambda url, timeout=30: (200, _urlset("https://site.example/a")))
    assert module.sitemap_urls("http://live.test") == ["https://site.example/a"]
