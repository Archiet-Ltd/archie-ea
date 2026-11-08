"""Each recording's share page (/see/<slug>) works signed out, carries the preview tags a platform reads, and
every file those tags name answers; the module page carries the same card; the sitemap lists the share page."""

from __future__ import annotations

import html
import re

import pytest

from app.services.public_pages import MODULE_RECORDINGS, MODULE_RECORDING_DEFECTS, SITE_URL

SLUGS = sorted(MODULE_RECORDINGS)


def _tags(body):
    """Every og:/twitter: meta tag of a page as {name: content}."""
    found = re.findall(r'<meta (?:property|name)="((?:og|twitter):[^"]+)" content="([^"]*)"', body)
    return {name: html.unescape(content) for name, content in found}


def _local(url):
    assert url.startswith(SITE_URL + "/"), url
    return url[len(SITE_URL):]


@pytest.mark.parametrize("slug", SLUGS)
def test_a_share_page_carries_the_preview_card_and_every_file_answers(app, slug):
    with app.test_client() as client:
        response = client.get("/see/%s" % slug)
        assert response.status_code == 200, "signed out, the share page opens"
        body = response.get_data(as_text=True)
        tags = _tags(body)
        entry = MODULE_RECORDINGS[slug]
        assert tags["og:title"] == entry["title"]
        assert tags["og:description"] == entry["promise"]
        assert tags["og:url"] == "%s/see/%s" % (SITE_URL, slug)
        assert 'rel="canonical" href="%s/see/%s"' % (SITE_URL, slug) in body
        assert tags["og:type"] == "video.other"
        assert tags["twitter:card"] == "summary_large_image"
        assert tags["og:image:alt"] and tags["twitter:image"] == tags["og:image"]
        assert (tags["og:image:width"], tags["og:image:height"]) == ("1200", "630")
        assert tags["og:video:type"] == "video/mp4" and tags["og:video:width"] == "1280"

        image = client.get(_local(tags["og:image"]))
        assert image.status_code == 200 and image.mimetype == "image/jpeg" and len(image.data) > 10_000
        video = client.get(_local(tags["og:video"]), headers={"Range": "bytes=0-99"})
        assert video.status_code in (200, 206) and video.mimetype == "video/mp4"
        assert tags["og:video"].endswith("%s-wide.mp4" % slug)


@pytest.mark.parametrize("slug", SLUGS)
def test_a_share_page_shows_the_recording_its_summary_and_the_way_on(app, slug):
    with app.test_client() as client:
        body = client.get("/see/%s" % slug).get_data(as_text=True)
        entry = MODULE_RECORDINGS[slug]
        assert 'data-testid="page-recording"' in body and "/static/img/modules/%s.webm" % slug in body
        assert "autoplay" not in body.lower().split("<video", 1)[1].split(">", 1)[0]
        assert html.unescape(entry["summary"]) in html.unescape(body)
        assert 'data-testid="share-cta"' in body
        assert 'data-testid="share-module-link"' in body and 'href="/modules/%s"' % slug in body
        for shape in ("wide", "square", "upright"):
            path = "/static/video/share/%s-%s.mp4" % (slug, shape)
            assert path in body
            assert client.get(path, headers={"Range": "bytes=0-9"}).status_code in (200, 206)
        # nothing a person must sign in to see, and no third-party widget or script
        assert not re.search(r"<script[^>]+src=\"https?://", body)


@pytest.mark.parametrize("slug", SLUGS)
def test_the_module_page_carries_the_same_card(app, slug):
    with app.test_client() as client:
        tags = _tags(client.get("/modules/%s" % slug).get_data(as_text=True))
        assert tags["og:type"] == "video.other"
        assert tags["og:image"] == "%s/static/video/share/%s-cover.jpg" % (SITE_URL, slug)
        assert tags["og:video"] == "%s/static/video/share/%s-wide.mp4" % (SITE_URL, slug)
        assert tags["twitter:card"] == "summary_large_image"


def test_a_module_without_a_recording_has_no_share_page_and_no_video_tags(app):
    slug = sorted(MODULE_RECORDING_DEFECTS)[0]
    with app.test_client() as client:
        assert client.get("/see/%s" % slug).status_code == 404
        assert client.get("/see/not-a-module").status_code == 404
        tags = _tags(client.get("/modules/%s" % slug).get_data(as_text=True))
        assert "og:video" not in tags and tags.get("og:type") != "video.other"


def test_the_sitemap_lists_every_share_page(app):
    with app.test_client() as client:
        sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    for slug in SLUGS:
        assert "<loc>%s/see/%s</loc>" % (SITE_URL, slug) in sitemap
