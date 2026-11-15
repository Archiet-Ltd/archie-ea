"""A new customer on an empty organisation is shown how to do the next step, on each of the first six empty screens.

The goal: on the applications list, connectors, model, AI chat, compliance and canvases, the empty screen says why
it is empty and offers "See how". The button opens a dialog with the clip of exactly that step; the clip loads and
is the one for that screen; the video is muted, has controls and does not play by itself; a line under it says what
it shows; Escape closes the dialog and the focus returns to the button. The same works from the keyboard alone and
at phone width, where the dialog fills the screen and the video takes its width.

What each clip shows is checked too: the recorder measures what each clip declares it must show before it keeps the
clip, and the journey reads that record, so a clip that shows less than its line says cannot pass. The compliance
screen is reached in an organisation of its own that adopted no framework; no shared row is changed.
"""

import hashlib
import json
import re
import uuid

import pytest
from playwright.sync_api import expect

from app.services.public_pages import HELP_CLIPS, IMG_HELP_DIR
from scripts.capture.clip_kit import ClipRefused
from scripts.capture.help_clips import CHECKS, CHECKS_FILE, MAX_SECONDS, MIN_SECONDS, Seen, run_checks

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

# key, the page that is empty for a new organisation, the empty state's own heading
SCREENS = [
    ("applications-first", "/applications/", "No applications yet."),
    ("connectors-github", "/admin/connectors", "No connectors configured for this organisation yet."),
    ("model-from-github", "/archimate/composer", "Nothing is modelled yet"),
    ("chat-citation", "/ai-chat", None),
    ("compliance-controls", "/dashboard/compliance", "No regulatory frameworks adopted yet"),
    ("canvases-open", "/business-model/", "No canvases yet"),
]
VIEWPORTS = {"1440": {"width": 1440, "height": 900}, "390": {"width": 390, "height": 844}}


@pytest.fixture(scope="module")
def newcomer(live_server):
    """A new organisation with one administrator and nothing recorded in it."""
    return create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")


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
    # the clip takes the dialog's width, so the text inside the recording is as large as the screen allows:
    # a wide dialog from md up, the whole screen on a phone
    content = dialog.evaluate(
        "el => { const s = getComputedStyle(el); return el.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight); }")
    assert video_box["width"] >= 0.9 * content, (video_box, content)
    if size == "1440":
        assert box["width"] >= 960, "the dialog is not wide at 1440: %r" % box
    else:
        assert box["width"] >= viewport["width"] - 1 and box["height"] >= viewport["height"] - 1, \
            "the dialog does not fill the phone screen: %r" % box


@pytest.mark.parametrize("size", sorted(VIEWPORTS))
@pytest.mark.parametrize("key,path,heading", SCREENS, ids=[row[0] for row in SCREENS])
def test_each_empty_screen_offers_the_clip_of_its_next_step(
        page, live_server, newcomer, key, path, heading, size):
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


def _kept_record():
    return json.loads((IMG_HELP_DIR / CHECKS_FILE).read_text(encoding="utf-8"))


@pytest.mark.parametrize("key", sorted(HELP_CLIPS))
def test_every_clip_passed_the_checks_it_declares_when_it_was_recorded(key):
    """From the recorder's own record: each declared check was measured on the clip now in the tree, and passed."""
    record = _kept_record()
    assert key in record, "%s has no record of its checks: record it again" % key
    entry = record[key]
    clip = IMG_HELP_DIR / (key + ".webm")
    assert entry["clip_sha256"] == hashlib.sha256(clip.read_bytes()).hexdigest(), \
        "the %s clip is not the one whose checks were recorded" % key
    assert [row["check"] for row in entry["checks"]] == [check.name for check in CHECKS[key]], \
        "the checks the clip declares are not the ones it was recorded under"
    for row in entry["checks"]:
        assert row["passed"] and row["measured"] >= row["at_least"], (key, row)


@pytest.fixture
def canvas_organisation(app):
    """An organisation of its own with an empty canvas, and the person who owns it; removed afterwards."""
    from app import db
    from app.models.business_model import BusinessModelCanvas

    from .test_connect_github_to_cited_answer_journey import remove_organisations

    made = create_fresh_org("platform_admin", org_admin=True, platform_role="Administrator")
    with app.app_context():
        canvas = BusinessModelCanvas(organization_id=made["org_id"], name="Canvas " + uuid.uuid4().hex[:6],
                                     operating_model_type="coordination")
        db.session.add(canvas)
        db.session.commit()
        made["canvas_id"] = canvas.id
        db.session.remove()
    yield made
    remove_organisations(app, [made["org_id"]])


def test_the_recorder_refuses_the_canvases_clip_when_the_canvas_is_emptied(
        page, live_server, app, canvas_organisation):
    """An empty canvas fails the canvases clip's declared checks; the same canvas filled from the model passes."""
    from app import db
    from app.commands.seed_demo_company import _seed_canvas_model
    from app.models.business_model import BusinessModelCanvas

    sign_in(page, live_server, canvas_organisation["emails"]["platform_admin"])
    address = "%s/business-model/%d" % (live_server, canvas_organisation["canvas_id"])
    page.goto(address, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    with pytest.raises(ClipRefused, match="boxes of the open canvas are filled"):
        run_checks(page, Seen(), CHECKS["canvases-open"])

    with app.app_context():
        canvas = db.session.get(BusinessModelCanvas, canvas_organisation["canvas_id"])
        _seed_canvas_model(canvas_organisation["org_id"], canvas)
        db.session.commit()
        db.session.remove()
    page.goto(address, wait_until="networkidle", timeout=PAGE_TIMEOUT)
    rows = run_checks(page, Seen(), CHECKS["canvases-open"])
    assert all(row["passed"] for row in rows), rows
    assert rows[0]["measured"] >= 5
