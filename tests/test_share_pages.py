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


def _tracked_files():
    """What a clean checkout holds: the files git tracks (or has staged), not whatever sits in the working tree."""
    import pathlib
    import subprocess

    root = pathlib.Path(__file__).resolve().parents[1]
    listed = subprocess.run(["git", "ls-files", "--cached", "--", "app/static/video/share"], cwd=root,
                            capture_output=True, text=True, check=True).stdout.split()
    return set(listed)


@pytest.mark.parametrize("slug", SLUGS)
def test_every_preview_image_on_the_share_and_module_pages_is_in_a_clean_checkout(app, slug):
    tracked = _tracked_files()
    with app.test_client() as client:
        for page_path in ("/see/%s" % slug, "/modules/%s" % slug):
            tags = _tags(client.get(page_path).get_data(as_text=True))
            for name in ("og:image", "twitter:image"):
                path = _local(tags[name])
                assert client.get(path).status_code == 200, "%s %s" % (page_path, name)
                assert path.lstrip("/").replace("static/", "app/static/", 1) in tracked, (
                    "%s: %s names %s, which git does not track, so a clean checkout would answer 404" % (page_path, name, path))


@pytest.mark.parametrize("slug", SLUGS)
def test_a_crawler_may_read_the_share_page_as_it_may_read_the_module_page(app, slug):
    from urllib.robotparser import RobotFileParser

    with app.test_client() as client:
        robots = client.get("/robots.txt").get_data(as_text=True)
    for agent in ("*", "LinkedInBot", "Twitterbot", "facebookexternalhit", "Googlebot"):
        parser = RobotFileParser()
        parser.parse(robots.splitlines())
        assert parser.can_fetch(agent, "/modules/%s" % slug)
        assert parser.can_fetch(agent, "/see/%s" % slug), "%s may not read /see/%s" % (agent, slug)


@pytest.mark.parametrize("slug", SLUGS)
def test_the_module_page_links_to_its_share_page_and_that_page_has_content(app, slug):
    with app.test_client() as client:
        body = client.get("/modules/%s" % slug).get_data(as_text=True)
        link = re.search(r'<a href="([^"]+)" data-testid="share-recording-link">Share this recording</a>', body)
        assert link, "the module page has no 'Share this recording' link"
        assert link.group(1) == "/see/%s" % slug
        landed = client.get(link.group(1))
        assert landed.status_code == 200 and 'data-testid="share-files"' in landed.get_data(as_text=True)
        # the share page does not link to itself
        assert "share-recording-link" not in landed.get_data(as_text=True)


@pytest.mark.parametrize("slug", SLUGS)
def test_the_recording_offers_a_second_source_for_browsers_that_do_not_play_webm(app, slug):
    with app.test_client() as client:
        for page_path in ("/see/%s" % slug, "/modules/%s" % slug):
            video = re.search(r"<video.*?</video>", client.get(page_path).get_data(as_text=True), re.S).group(0)
            sources = re.findall(r'<source src="([^"]+)" type="([^"]+)"', video)
            assert sources == [("/static/img/modules/%s.webm" % slug, "video/webm"),
                               ("/static/video/share/%s-wide.mp4" % slug, "video/mp4")], page_path


def test_a_held_module_has_no_share_page_in_the_sitemap_list(monkeypatch):
    from app.services import public_pages

    slug = SLUGS[0]
    assert "/see/%s" % slug in public_pages.share_page_paths()
    kept = [p for p in public_pages.load_feed_pages() if p.url != "/modules/%s" % slug]
    monkeypatch.setattr(public_pages, "load_feed_pages", lambda: kept)
    listed = public_pages.share_page_paths()
    assert "/see/%s" % slug not in listed and len(listed) == len(SLUGS) - 1


def test_the_closing_call_to_action_is_written_once():
    import pathlib

    templates = pathlib.Path(__file__).resolve().parents[1] / "app" / "templates"
    holders = sorted(str(p.relative_to(templates)) for p in templates.rglob("*.html")
                     if "Connect your tools. Get a cited answer." in p.read_text(encoding="utf-8")
                     and "<h2" in p.read_text(encoding="utf-8").split("Connect your tools. Get a cited answer.")[0][-120:])
    assert holders == ["public/_partials/_section_closing.html"]


@pytest.mark.parametrize("slug", SLUGS)
def test_the_features_page_links_each_share_page_so_it_is_two_clicks_from_home(app, slug):
    with app.test_client() as client:
        home = client.get("/").get_data(as_text=True)
        assert 'href="/features"' in home
        features = client.get("/features").get_data(as_text=True)
        assert 'href="/see/%s"' % slug in features
        assert client.get("/see/%s" % slug).status_code == 200


@pytest.mark.parametrize("wave", ["1", "2", "3", "4", "5", "6"])
def test_under_every_release_wave_a_share_page_opens_with_its_recording_where_the_module_page_does(app, monkeypatch, wave):
    """The share link on a module page must not lead to a "Coming to Entelim" page while the module page is open."""
    monkeypatch.setitem(app.config, "RELEASE_WAVE", wave)
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    opened = 0
    with app.test_client() as client:
        for slug in SLUGS:
            module = client.get("/modules/%s" % slug)
            shared = client.get("/see/%s" % slug)
            module_open = 'data-testid="early-access-page"' not in module.get_data(as_text=True)
            share_body = shared.get_data(as_text=True)
            assert (shared.status_code == 200) and ('data-testid="early-access-page"' not in share_body) == module_open, (wave, slug)
            if module_open:
                opened += 1
                assert 'data-testid="page-recording"' in share_body and 'data-testid="share-files"' in share_body, (wave, slug)
                assert 'href="/see/%s"' % slug in module.get_data(as_text=True), (wave, slug)
    assert opened >= 1, "no module page with a recording is open in wave %s, so nothing was compared" % wave
