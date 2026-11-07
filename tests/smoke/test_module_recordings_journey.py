"""A visitor who wants to see a module working opens its page, plays the recording and learns what it shows.

The goal: on three module pages (and every recorded module's own address) a visitor sees a recording of the module's whole job. The video is muted, has
controls and does not start by itself; its file loads and runs between 30 and 90 seconds; its poster is a real
image; pressing play moves it on; the sentences under it say what it shows and match the page's own text; and
the page is not overflowing at phone width. A module that has no recording (its screen is broken today) offers
no video and no empty box.
"""

import pytest
from playwright.sync_api import expect

from app.services.public_pages import MODULE_RECORDING_DEFECTS, MODULE_RECORDINGS

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SLUGS = ["applications", "risk-register", "digital-twin"]
VIEWPORTS = {"1440": {"width": 1440, "height": 900}, "390": {"width": 390, "height": 844}}


def _open(page, live_server, slug, size):
    page.set_viewport_size(VIEWPORTS[size])
    page.goto("%s/modules/%s" % (live_server, slug), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)


@pytest.mark.parametrize("size", sorted(VIEWPORTS))
@pytest.mark.parametrize("slug", SLUGS)
def test_a_visitor_plays_the_recording_and_reads_what_it_shows(page, live_server, slug, size):
    entry = MODULE_RECORDINGS[slug]
    _open(page, live_server, slug, size)

    figure = page.get_by_test_id("page-recording")
    figure.scroll_into_view_if_needed()
    expect(figure).to_be_visible(timeout=PAGE_TIMEOUT)
    video = figure.locator("video")
    expect(video).to_be_visible()
    assert video.evaluate("v => v.muted && v.controls && !v.autoplay")
    assert video.get_attribute("aria-label") == entry["alt"]

    # the file loads and is the one for this module, of the length the standard allows
    page.wait_for_function("v => { v.load(); return true; }", arg=video.element_handle())
    page.wait_for_function("v => v.readyState >= 1", arg=video.element_handle(), timeout=PAGE_TIMEOUT)
    state = video.evaluate("v => ({src: v.currentSrc, seconds: v.duration, paused: v.paused, width: v.videoWidth})")
    assert state["src"].endswith("/static/img/modules/%s.webm" % slug), state
    assert 30 <= state["seconds"] <= 90, state
    assert state["paused"], "the recording started by itself"
    assert state["width"] == 1440, state

    # the poster is a real image that the page serves
    poster = video.get_attribute("poster")
    assert poster.endswith("/static/img/modules/%s.poster.webp" % slug)
    response = page.request.get(live_server + poster)
    assert response.ok and response.headers["content-type"].startswith("image/"), poster
    assert len(response.body()) > 1000

    # pressing play moves it on
    video.evaluate("v => { v.play(); }")
    page.wait_for_function("v => v.currentTime > 0.5", arg=video.element_handle(), timeout=PAGE_TIMEOUT)

    # the sentences beside it say what it shows, and the person who cannot play it still gets the answer
    expect(figure.locator("figcaption")).to_have_text(entry["summary"])
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1"), \
        "the page scrolls sideways at %s wide" % size

    # search engines get the same recording described
    ld = page.evaluate("() => Array.from(document.querySelectorAll('script[type=\"application/ld+json\"]')).map(s => s.textContent).join('\\n')")
    assert "VideoObject" in ld and "/static/img/modules/%s.webm" % slug in ld


def test_a_module_whose_screen_is_broken_shows_no_video_and_no_empty_box(page, live_server):
    slug = "vendors"  # a page of its own (not a redirect) whose screen is broken today
    assert slug in MODULE_RECORDING_DEFECTS
    _open(page, live_server, slug, "1440")
    assert page.get_by_test_id("page-recording").count() == 0
    assert page.locator("video").count() == 0


@pytest.mark.parametrize("slug", sorted(MODULE_RECORDINGS))
def test_a_recorded_module_is_served_at_its_own_address_with_its_recording(page, live_server, slug):
    """A recording is no use on a page that sends the visitor elsewhere: every recorded module answers 200 itself and
    its page names the file that plays."""
    response = page.request.get("%s/modules/%s" % (live_server, slug), max_redirects=0)
    assert response.status == 200, "/modules/%s answers %s, so its recording is never seen" % (slug, response.status)
    assert "/static/img/modules/%s.webm" % slug in response.text()
    assert page.request.get("%s/static/img/modules/%s.webm" % (live_server, slug)).ok
