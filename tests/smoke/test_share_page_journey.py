"""A person who is sent a recording opens its share page signed out, watches it, and can start or read on.

The goal: from a link on LinkedIn or X, a visitor lands on a page that plays the recording they were promised
(muted, with controls, not starting by itself), reads in words what it shows, and can follow the call to action
to the page that starts the product, or go back to the module page. The preview tags the platforms read name
files that answer, and nothing on the page overflows a phone.
"""

import os
import tempfile
from pathlib import Path

import pytest
from playwright.sync_api import expect

from app.services.public_pages import MODULE_RECORDINGS

from .chat_ask import sign_in_as
from .conftest import PAGE_TIMEOUT

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SLUGS = sorted(MODULE_RECORDINGS)
VIEWPORTS = {"1440": {"width": 1440, "height": 900}, "390": {"width": 390, "height": 844}}
SHOTS = Path(os.environ.get("SMOKE_WALK_DIR") or Path(tempfile.gettempdir()) / "share-page-shots")


def _meta(page, name):
    return page.locator("head meta[property='%s'], head meta[name='%s']" % (name, name)).first.get_attribute("content")


@pytest.mark.parametrize("size", sorted(VIEWPORTS))
@pytest.mark.parametrize("slug", SLUGS)
def test_a_visitor_watches_the_shared_recording_and_follows_the_call_to_action(page, live_server, slug, size):
    entry = MODULE_RECORDINGS[slug]
    page.set_viewport_size(VIEWPORTS[size])
    page.goto("%s/see/%s" % (live_server, slug), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)

    # the page says what the recording is, and the sentences under it say what it shows
    assert page.locator("h1").first.inner_text().strip() == entry["title"]
    expect(page.get_by_test_id("page-recording").locator("figcaption")).to_have_text(entry["summary"])

    # the recording is muted, has controls, does not start by itself, and plays
    figure = page.get_by_test_id("page-recording")
    figure.scroll_into_view_if_needed()
    expect(figure).to_be_visible(timeout=PAGE_TIMEOUT)
    video = figure.locator("video")
    assert video.evaluate("v => v.muted && v.controls && !v.autoplay")
    page.wait_for_function("v => { v.load(); return true; }", arg=video.element_handle())
    page.wait_for_function("v => v.readyState >= 1", arg=video.element_handle(), timeout=PAGE_TIMEOUT)
    assert video.evaluate("v => v.paused"), "the recording started by itself"
    video.evaluate("v => { v.play(); }")
    page.wait_for_function("v => v.currentTime >= 2", arg=video.element_handle(), timeout=PAGE_TIMEOUT)
    video.evaluate("v => v.pause()")

    # the preview tags read what the page says and name files that answer
    assert _meta(page, "og:title") == entry["title"]
    assert _meta(page, "og:description") == entry["promise"]
    assert _meta(page, "og:url") == "%s/see/%s" % (live_server, slug)
    assert _meta(page, "og:type") == "video.other"
    assert _meta(page, "twitter:card") == "summary_large_image"
    for tag, kind in (("og:image", "image/jpeg"), ("og:video", "video/mp4")):
        url = _meta(page, tag)
        assert url.startswith(live_server + "/static/video/share/"), url
        response = page.request.get(live_server + url[len(live_server):], headers={"Range": "bytes=0-1023"})
        assert response.status in (200, 206) and response.headers["content-type"] == kind, (tag, url)
        assert len(response.body()) > 500

    # the three files offered for sharing each answer
    links = page.get_by_test_id("share-files").locator("a")
    assert links.count() == 3
    for i in range(3):
        response = page.request.get(live_server + links.nth(i).get_attribute("href"), headers={"Range": "bytes=0-99"})
        assert response.status in (200, 206) and response.headers["content-type"] == "video/mp4"

    # the recording sits in the middle of the page, under the centred heading
    box = figure.bounding_box()
    assert abs((box["x"] + box["width"] / 2) - VIEWPORTS[size]["width"] / 2) <= 12, box

    # a phone is not asked to scroll sideways
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / ("share-%s-%s.png" % (slug, size))), full_page=True)

    # the way back to the module page lands on that module with its recording
    page.get_by_test_id("share-module-link").click()
    page.wait_for_url("**/modules/%s" % slug, timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("page-recording")).to_be_visible()
    assert page.locator("h1").first.inner_text().strip()

    # the module page offers the share page again, and it opens with the recording and the files
    page.get_by_test_id("share-recording-link").click()
    page.wait_for_url("**/see/%s" % slug, timeout=PAGE_TIMEOUT)
    expect(page.get_by_test_id("page-recording")).to_be_visible()
    assert page.get_by_test_id("share-files").locator("a").count() == 3
    assert page.get_by_test_id("share-recording-link").count() == 0, "the share page links to itself"

    # and the call to action on the share page lands on the page that starts the product, with content
    page.goto("%s/see/%s" % (live_server, slug), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    cta = page.get_by_test_id("share-cta")
    href = cta.get_attribute("href")
    cta.click()
    page.wait_for_load_state("domcontentloaded")
    assert "/see/" not in page.url, "the call to action stays on the share page"
    assert page.locator("h1").first.inner_text().strip(), "the page the call to action opens has a headline"
    assert len(page.locator("main, body").first.inner_text().split()) > 20, href


@pytest.mark.parametrize("size", sorted(VIEWPORTS))
@pytest.mark.parametrize("slug", SLUGS)
def test_a_signed_in_person_reading_on_or_going_back_lands_on_the_public_module_page(page, live_server, seeded, slug, size):
    """"Read about ..." and "Back to ..." name the module page, so a signed-in person gets that page and its
    recording, not the working screen some modules open for them."""
    entry = MODULE_RECORDINGS[slug]
    page.set_viewport_size(VIEWPORTS[size])
    sign_in_as(page, live_server, seeded, "enterprise_architect")
    for how in ("read", "back"):
        page.goto("%s/see/%s" % (live_server, slug), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        if how == "read":
            page.get_by_role("link", name="Read about", exact=False).first.click()
        else:
            page.get_by_test_id("share-module-link").click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.split("?")[0] == "%s/modules/%s" % (live_server, slug), (how, page.url)
        expect(page.get_by_test_id("page-recording")).to_be_visible(timeout=PAGE_TIMEOUT)
        assert entry["summary"] in page.get_by_test_id("page-recording").inner_text(), how
        assert page.locator("h1").first.inner_text().strip()
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / ("signed-in-%s-%s-%s.png" % (how, slug, size))), full_page=True)


@pytest.mark.parametrize("size", sorted(VIEWPORTS))
@pytest.mark.parametrize("slug", SLUGS[:2])
def test_a_signed_in_person_is_offered_their_dashboard_and_a_visitor_is_offered_sign_up(page, live_server, seeded, slug, size):
    """On a share page and on a module page alike, the call to action is "Open Entelim" and opens the dashboard for
    a signed-in person, and "Start free" opens sign-up for a visitor, never the other way round."""
    page.set_viewport_size(VIEWPORTS[size])
    kinds = (("see", "share-cta"), ("modules", "module-cta"))

    for kind, testid in kinds:
        page.goto("%s/%s/%s" % (live_server, kind, slug), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        cta = page.get_by_test_id(testid)
        assert cta.inner_text().strip() == "Start free", (kind, cta.inner_text())
        cta.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/register" in page.url and page.locator("h1").first.inner_text().strip() == "Create an account", (kind, page.url)

    sign_in_as(page, live_server, seeded, "enterprise_architect")
    for kind, testid in kinds:
        page.goto("%s/%s/%s" % (live_server, kind, slug), wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        assert page.get_by_role("link", name="Start free").count() == 0, kind
        cta = page.get_by_test_id(testid)
        assert cta.inner_text().strip() == "Open Entelim", (kind, cta.inner_text())
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / ("signed-in-cta-%s-%s-%s.png" % (kind, slug, size))), full_page=True)
        cta.click()
        page.wait_for_load_state("domcontentloaded")
        assert "/dashboard" in page.url and "/see/" not in page.url and "/modules/" not in page.url, (kind, page.url)
        assert page.locator("h1").first.inner_text().strip(), kind
        assert len(page.locator("main").first.inner_text().split()) > 20, kind
