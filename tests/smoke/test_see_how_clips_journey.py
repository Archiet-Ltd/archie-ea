"""A new customer on an empty organisation is shown how to do the next step, on each of the first six empty screens.

The goal: on the applications list, connectors, model, AI chat, compliance and canvases, the empty screen says why
it is empty and offers "See how". The button opens a dialog with the clip of exactly that step; the clip loads and
is the one for that screen; the video is muted, has controls and does not play by itself; a line under it says what
it shows; Escape closes the dialog and the focus returns to the button. The same works from the keyboard alone and
at phone width, where the dialog fits and the video scales.
"""

import re

import pytest
from playwright.sync_api import expect

from app.services.public_pages import HELP_CLIPS
from scripts.capture.help_clips import MAX_SECONDS, MIN_SECONDS

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# key, the page that is empty for a new organisation, the empty state's own heading
SCREENS = [
    ("applications-first", "/applications/", "No applications yet."),
    ("connectors-github", "/admin/connectors", "No connectors configured for this organisation yet."),
    ("model-from-github", "/archimate/composer", "Nothing is modelled yet"),
    ("chat-citation", "/ai-chat", None),
    ("compliance-controls", "/dashboard/compliance", "No regulatory frameworks yet"),
    ("canvases-open", "/business-model/", "No canvases yet"),
]
VIEWPORTS = {"1440": {"width": 1440, "height": 900}, "390": {"width": 390, "height": 844}}


@pytest.fixture(scope="module")
def newcomer(live_server):
    """A new organisation with one administrator and nothing recorded in it."""
    return create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")


@pytest.fixture(scope="module")
def no_active_frameworks(app, live_server):
    """The frameworks are shared by every organisation, so a database that already holds some would never show
    the empty compliance screen. Set aside, by id, whichever active ones it holds, and put each back after."""
    from app.extensions import db
    from app.models.compliance_models import RegulatoryFramework

    with app.app_context():
        set_aside = [(row.id, row.status) for row in RegulatoryFramework.query.filter_by(status="active").all()]
        for framework_id, _ in set_aside:
            db.session.get(RegulatoryFramework, framework_id).status = "deprecated"
        db.session.commit()
    yield set_aside
    with app.app_context():
        for framework_id, status in set_aside:
            db.session.get(RegulatoryFramework, framework_id).status = status
        db.session.commit()


def _open_screen(page, base, path, size):
    page.set_viewport_size(VIEWPORTS[size])
    page.goto(base + path, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)


def _the_button(page, key):
    return page.get_by_test_id("see-how-" + key)


def _check_the_clip(page, key, size):
    """The dialog is the one for this screen, and its clip loads."""
    _, title, line = HELP_CLIPS[key]
    dialog = page.get_by_test_id("see-how-dialog-" + key)
    expect(dialog).to_be_visible(timeout=PAGE_TIMEOUT)
    assert dialog.get_attribute("role") == "dialog"
    expect(dialog.get_by_role("heading", name=title)).to_be_visible()
    assert dialog.get_attribute("aria-labelledby"), "the dialog has no accessible name"
    expect(page.get_by_role("dialog", name=title)).to_be_visible()
    expect(page.get_by_test_id("see-how-line-" + key)).to_have_text(line)

    video = page.get_by_test_id("see-how-video-" + key)
    expect(video).to_be_visible()
    assert video.evaluate("v => v.muted && v.controls && !v.autoplay")
    page.wait_for_function("v => v.readyState >= 1", arg=video.element_handle(), timeout=PAGE_TIMEOUT)
    state = video.evaluate("v => ({src: v.currentSrc, seconds: v.duration, paused: v.paused, width: v.videoWidth})")
    assert state["src"].endswith("/static/img/help/%s.webm" % key), state
    assert MIN_SECONDS <= state["seconds"] <= MAX_SECONDS + 1, state
    assert state["paused"], "the clip started by itself"
    assert state["width"] > 0

    # the clip scales to the dialog, and the dialog fits the screen
    viewport = VIEWPORTS[size]
    box = dialog.bounding_box()
    video_box = video.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"], box
    assert box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"], box
    assert video_box["x"] >= box["x"] and video_box["x"] + video_box["width"] <= box["x"] + box["width"] + 1, (video_box, box)
    assert 150 < video_box["width"] <= viewport["width"]


@pytest.mark.parametrize("size", sorted(VIEWPORTS))
@pytest.mark.parametrize("key,path,heading", SCREENS, ids=[row[0] for row in SCREENS])
def test_each_empty_screen_offers_the_clip_of_its_next_step(
        page, live_server, newcomer, no_active_frameworks, key, path, heading, size):
    sign_in(page, live_server, newcomer["emails"]["platform_admin"])
    _open_screen(page, live_server, path, size)

    if heading:
        empty = page.get_by_text(heading, exact=True)
        expect(empty.first).to_be_visible(timeout=PAGE_TIMEOUT)
    button = _the_button(page, key)
    expect(button).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(button).to_have_text(re.compile(r"^\s*See how\s*$"))
    # nothing is playing or open before the person asks
    assert page.get_by_test_id("see-how-dialog-" + key).is_hidden()

    button.click()
    _check_the_clip(page, key, size)
    page.keyboard.press("Escape")
    expect(page.get_by_test_id("see-how-dialog-" + key)).to_be_hidden()
    assert page.evaluate("document.activeElement && document.activeElement.dataset.testid") == "see-how-" + key


@pytest.mark.parametrize("key,path", [("applications-first", "/applications/"), ("chat-citation", "/ai-chat")])
def test_the_clip_opens_and_closes_from_the_keyboard_alone(page, live_server, newcomer, key, path):
    sign_in(page, live_server, newcomer["emails"]["platform_admin"])
    _open_screen(page, live_server, path, "1440")
    button = _the_button(page, key)
    expect(button).to_be_visible(timeout=PAGE_TIMEOUT)
    button.focus()
    page.keyboard.press("Enter")
    _check_the_clip(page, key, "1440")
    # focus is held inside the dialog while it is open
    page.keyboard.press("Tab")
    assert page.evaluate("!!document.activeElement.closest('[data-slot=dialog-content]')")
    page.keyboard.press("Escape")
    expect(page.get_by_test_id("see-how-dialog-" + key)).to_be_hidden()
    assert page.evaluate("document.activeElement.dataset.testid") == "see-how-" + key


def test_the_next_step_the_clip_shows_is_one_the_screen_offers(page, live_server, newcomer):
    """The connectors clip ends on Connect GitHub: the screen's own button lands on a page that reads a token."""
    sign_in(page, live_server, newcomer["emails"]["platform_admin"])
    _open_screen(page, live_server, "/admin/connectors", "1440")
    connect = page.get_by_role("link", name="Connect GitHub").first
    expect(connect).to_be_visible(timeout=PAGE_TIMEOUT)
    connect.click()
    page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT)
    expect(page.get_by_role("heading", name="Connect GitHub").first).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(page.get_by_text("Read-only token").first).to_be_visible()
