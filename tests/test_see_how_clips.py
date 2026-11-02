"""The "See how" clips on the first empty screens: the empty state, the registry, the recorded files and the scan.

The browser journey that opens each dialog is tests/smoke/test_see_how_clips_journey.py; these are the parts that
need no browser.
"""

from __future__ import annotations

import hashlib
import re
import subprocess

import pytest
from flask import render_template_string

from app.services import public_pages
from app.services.public_pages import HELP_CLIPS, IMG_HELP_DIR, help_clip
from scripts.capture import help_clips
from scripts.capture.clip_kit import CLIP_MAX_BYTES

# sha256 of what the empty state rendered, with the exact arguments the applications list and the canvas library
# pass, before it learned to offer a clip. Without see_how the output must be these bytes.
APPLICATIONS_ARGS = (
    "icon='layout-grid', title='No applications yet.', "
    "description='Your application portfolio is empty. Add your first application to start mapping it to "
    "capabilities, owners and cost.', cta_text='Import Applications', "
    "cta_onclick=\"Platform.modal.open('application-import-modal')\", cta_icon='upload', "
    "secondary_cta_text='Add an application', secondary_cta_onclick=\"Platform.modal.open('modal-create')\", "
    "secondary_cta_icon='plus'"
)
CANVASES_ARGS = (
    "icon='layout-grid', title='No canvases yet', description='Create your first canvas and pick the framework: "
    "the boxes fill from the elements already in your model.', cta_text='New Canvas', "
    "cta_onclick=\"Platform.modal.open('bmc-create-modal')\", cta_icon='plus'"
)
UNCHANGED = {
    "applications": (APPLICATIONS_ARGS, "469ebeebc1227a05ef07d18a486e0e64bade3ff211dd0c17691d46d79b6dc297"),
    "canvases": (CANVASES_ARGS, "cb21db8ae60de6d1cd6ce04fc0b22c8b2357b2f4f44c32844b5b4713d48e2dd9"),
}


def _render(app, args):
    with app.test_request_context("/"):
        return render_template_string("{% from 'components/empty_state.html' import empty_state %}{{ empty_state(" + args + ") }}")


@pytest.mark.parametrize("screen", sorted(UNCHANGED))
def test_the_empty_state_without_see_how_renders_exactly_as_before(app, screen):
    args, digest = UNCHANGED[screen]
    html = _render(app, args).strip()
    assert hashlib.sha256(html.encode()).hexdigest() == digest
    assert "See how" not in html


def test_the_empty_state_offers_see_how_in_a_dialog_when_the_clip_exists(app):
    html = _render(app, "title='Nothing yet', see_how='canvases-open'")
    assert 'data-testid="see-how-canvases-open"' in html
    assert ">See how<" in html.replace("\n", "").replace("  ", "")
    assert 'data-slot="dialog-content"' in html and 'role="dialog"' in html
    title = re.search(r'<h2[^>]*data-slot="dialog-title"[^>]*>\s*([^<]+?)\s*<', html)
    assert title and title.group(1) == HELP_CLIPS["canvases-open"][1]
    video = re.search(r"<video\b[^>]*>", html).group(0)
    assert " controls" in video and " muted" in video and "autoplay" not in video
    assert 'src="/static/img/help/canvases-open.webm"' in html
    assert HELP_CLIPS["canvases-open"][2] in html


def test_no_button_is_offered_for_a_clip_that_is_not_recorded(app):
    html = _render(app, "title='Nothing yet', see_how='not-a-clip'")
    assert "See how" not in html and "<video" not in html


def test_a_clip_is_offered_only_once_its_file_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(public_pages, "IMG_HELP_DIR", tmp_path)
    assert help_clip("canvases-open") is None
    (tmp_path / "canvases-open.webm").write_bytes(b"x")
    found = help_clip("canvases-open")
    assert found["src"] == "/static/img/help/canvases-open.webm" and found["poster"] is None


def test_every_clip_is_registered_recorded_and_the_right_length():
    assert set(help_clips.STEPS) == set(HELP_CLIPS)
    assert len(HELP_CLIPS) == 6
    for key, (path, title, line) in HELP_CLIPS.items():
        clip = IMG_HELP_DIR / f"{key}.webm"
        assert clip.is_file(), key
        assert (IMG_HELP_DIR / f"{key}.poster.webp").is_file(), key
        assert 0 < clip.stat().st_size <= CLIP_MAX_BYTES, key
        assert title and line and path.startswith("/"), key
        seconds = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
            capture_output=True, text=True, check=True).stdout.strip())
        assert help_clips.MIN_SECONDS <= seconds <= help_clips.MAX_SECONDS, (key, seconds)


def test_every_clip_is_recorded_on_a_real_page(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    for key, (path, _, _) in HELP_CLIPS.items():
        assert path in rules, (key, path)


def test_the_scan_refuses_a_secret_and_another_organisations_name():
    assert help_clips.scan_for_leaks(["Applications", "Billing Portal"], ["Other Co"]) == []
    assert help_clips.scan_for_leaks(["token ghp_" + "a" * 30]) != []
    assert help_clips.scan_for_leaks(["key AKIA" + "A" * 16]) != []
    assert help_clips.scan_for_leaks(["Prepared for other co ltd"], ["Other Co Ltd"]) != []
