"""A visitor opens a recorded module page signed out and sees the recording first, with no screenshot repeating it.

The goal: the founder's rule (one recording per page, screenshots only where they show something the recording does
not). On each of the six recorded module pages the recording is the first media element, it opens with its cover
image, and no image on the page is the screen the recording opens on (the module's registered screenshot, in either
width). A module without a recording keeps its screenshot.
"""

import os
import tempfile
from pathlib import Path

import pytest
from playwright.sync_api import expect

from app.services.public_pages import MODULE_CAPTURES, MODULE_RECORDINGS

from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SLUGS = sorted(MODULE_RECORDINGS)
VIEWPORTS = {"1440": {"width": 1440, "height": 900}, "390": {"width": 390, "height": 844}}
SHOTS = Path(os.environ.get("SMOKE_WALK_DIR") or Path(tempfile.gettempdir()) / "module-recording-first-shots")


@pytest.mark.parametrize("size", sorted(VIEWPORTS))
@pytest.mark.parametrize("slug", SLUGS)
def test_a_visitor_sees_the_recording_first_and_no_picture_of_its_opening_view(page, live_server, slug, size):
    page.set_viewport_size(VIEWPORTS[size])
    page.goto("%s/modules/%s" % (live_server, slug), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    recording = page.get_by_test_id("page-recording")
    expect(recording).to_have_count(1)
    expect(recording.locator("video")).to_have_attribute("poster", "/static/img/modules/%s.poster.webp" % slug)
    expect(recording.locator("figcaption")).to_have_text(MODULE_RECORDINGS[slug]["summary"])

    # the recording comes before every other picture or video in the page's main content
    first_media = page.evaluate(
        """() => {
            const m = document.querySelector('main') || document.body;
            const el = m.querySelector('video, img, picture');
            return el ? el.tagName.toLowerCase() : null;
        }"""
    )
    assert first_media == "video", "the first picture on /modules/%s is a %s, not the recording" % (slug, first_media)

    # no image on the page is the screen the recording opens on, in either width
    registered = [e for e in MODULE_CAPTURES if e[0] == slug]
    assert registered, "%s is recorded but has no registry entry" % slug
    sources = page.evaluate(
        "() => [...document.querySelectorAll('img, source')].map(e => e.getAttribute('src') || e.getAttribute('srcset') || '')"
    )
    for src in sources:
        assert "/static/img/modules/%s.webp" % slug not in src, "the page repeats the recording's view: %s" % src
        assert "/static/img/modules/%s-mobile.webp" % slug not in src, "the page repeats the recording's view: %s" % src
    expect(page.get_by_test_id("page-screenshot")).to_have_count(0)

    # the page still has its words, so nothing is left empty
    assert len(page.locator("main").first.inner_text().split()) > 50
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("module-%s-%s.png" % (slug, size))), full_page=True)


@pytest.mark.parametrize("slug", ["ai-chat", "arb"])
def test_a_module_without_a_recording_keeps_its_screenshot(page, live_server, slug):
    assert slug not in MODULE_RECORDINGS
    page.goto("%s/modules/%s" % (live_server, slug), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("page-recording")).to_have_count(0)
    expect(page.get_by_test_id("page-screenshot")).to_have_count(1)
    assert page.locator("[data-testid=page-screenshot] img").get_attribute("src") == "/static/img/modules/%s.webp" % slug
